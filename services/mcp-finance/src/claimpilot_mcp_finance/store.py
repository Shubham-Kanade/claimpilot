"""SQLite-backed claim store: idempotent submission, reference numbering, the status machine.

This module knows nothing about MCP. The tool layer (``server.py``) adapts tool arguments to
these methods and turns ``FinanceError`` into tool errors the model can read and act on.

Status machine::

    received -> under_review -> approved | rejected

``decide`` on a ``received`` claim passes through ``under_review`` first (both steps land in the
history), so an approver can decide in one call. Decisions are final.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from claimpilot_mcp_finance.models import (
    FINAL_STATUSES,
    ClaimRecord,
    ClaimStatus,
    ClaimSummary,
    Decision,
    StatusEvent,
    SubmissionReceipt,
)

SCHEMA_VERSION = 1
REFERENCE_PREFIX = "FIN"
MAX_DOCUMENTS = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    seq             INTEGER PRIMARY KEY,
    reference       TEXT NOT NULL UNIQUE,
    claim_id        TEXT NOT NULL,
    employee_id     TEXT NOT NULL,
    title           TEXT NOT NULL,
    total           REAL NOT NULL,
    currency        TEXT NOT NULL,
    document_ids    TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_hash    TEXT NOT NULL,
    status          TEXT NOT NULL
        CHECK (status IN ('received', 'under_review', 'approved', 'rejected')),
    received_at     TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_claims_status ON claims (status);
CREATE INDEX IF NOT EXISTS idx_claims_employee ON claims (employee_id);
CREATE TABLE IF NOT EXISTS claim_history (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    seq     INTEGER NOT NULL REFERENCES claims (seq),
    status  TEXT NOT NULL,
    at      TEXT NOT NULL,
    actor   TEXT,
    comment TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_history_seq ON claim_history (seq);
"""

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """The current time in UTC, to the second (stable, readable timestamps)."""
    return datetime.now(UTC).replace(microsecond=0)


class FinanceError(Exception):
    """A domain failure the caller can understand and fix; the message is safe to show a model."""


class InvalidClaimError(FinanceError):
    """An argument is missing, malformed or out of range."""


class ClaimNotFoundError(FinanceError):
    """No claim has the requested reference."""


class IdempotencyConflictError(FinanceError):
    """The idempotency key was already used for a different payload."""


class InvalidTransitionError(FinanceError):
    """The requested status change is not allowed from the claim's current status."""


def _text(field: str, value: str, *, max_len: int = 200) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise InvalidClaimError(f"{field} must not be empty")
    if len(cleaned) > max_len:
        raise InvalidClaimError(f"{field} must be at most {max_len} characters")
    return cleaned


def _amount(total: float) -> float:
    if not math.isfinite(total):
        raise InvalidClaimError("total must be a finite number")
    rounded = round(total, 2)
    if rounded <= 0:
        raise InvalidClaimError("total must be greater than zero")
    return rounded


def _currency(value: str) -> str:
    cleaned = value.strip()
    if not re.fullmatch(r"[A-Za-z]{3}", cleaned):
        raise InvalidClaimError("currency must be a 3-letter ISO 4217 code, e.g. 'INR'")
    return cleaned.upper()


def _documents(values: Sequence[str]) -> list[str]:
    cleaned = [_text("document_ids entry", v, max_len=100) for v in values]
    if not cleaned:
        raise InvalidClaimError("document_ids must list at least one document")
    if len(cleaned) > MAX_DOCUMENTS:
        raise InvalidClaimError(f"document_ids may list at most {MAX_DOCUMENTS} documents")
    if len(set(cleaned)) != len(cleaned):
        raise InvalidClaimError("document_ids must not contain duplicates")
    return cleaned


def _digest(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def normalize_reference(reference: str) -> str:
    return reference.strip().upper()


class FinanceStore:
    """All claim state. One shared connection guarded by a lock: tools run on worker threads."""

    def __init__(self, path: str | Path = ":memory:", *, clock: Clock = utc_now) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        location = str(path)
        if location != ":memory:":
            Path(location).parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: autocommit, so the explicit BEGIN/COMMIT in `_tx` is the only
        # transaction control. check_same_thread=False: the lock serialises all access.
        self._db = sqlite3.connect(location, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        try:
            self._init_schema()
        except BaseException:
            self._db.close()
            raise

    # -- lifecycle ---------------------------------------------------------------------------

    def _init_schema(self) -> None:
        with self._lock:
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise RuntimeError(
                    f"finance database has schema version {version}; this build expects "
                    f"{SCHEMA_VERSION}"
                )
            if version == 0:
                self._db.executescript(_SCHEMA)
                self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def ping(self) -> None:
        """Raise if the database is not usable (readiness probe)."""
        with self._lock:
            self._db.execute("SELECT 1").fetchone()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    def _now(self) -> datetime:
        now = self._clock()
        return now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)

    # -- commands ----------------------------------------------------------------------------

    def submit(
        self,
        *,
        claim_id: str,
        employee_id: str,
        title: str,
        total: float,
        currency: str,
        document_ids: Sequence[str],
        idempotency_key: str,
    ) -> SubmissionReceipt:
        """Record a claim as ``received``; replaying the same key returns the original receipt."""
        claim_id = _text("claim_id", claim_id, max_len=100)
        employee_id = _text("employee_id", employee_id, max_len=100)
        title = _text("title", title)
        total = _amount(total)
        currency = _currency(currency)
        documents = _documents(document_ids)
        key = _text("idempotency_key", idempotency_key)
        digest = _digest(
            {
                "claim_id": claim_id,
                "employee_id": employee_id,
                "title": title,
                "total": total,
                "currency": currency,
                "document_ids": sorted(documents),
            }
        )
        with self._tx() as db:
            existing = db.execute(
                "SELECT reference, payload_hash, received_at FROM claims WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if existing is not None:
                if existing["payload_hash"] != digest:
                    raise IdempotencyConflictError(
                        f"idempotency_key '{key}' was already used for a different claim "
                        f"payload (reference {existing['reference']}); use a new key for a "
                        "new submission"
                    )
                return SubmissionReceipt(
                    reference=existing["reference"],
                    received_at=datetime.fromisoformat(existing["received_at"]),
                    duplicate=True,
                )
            now = self._now()
            seq = db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM claims").fetchone()[0]
            reference = f"{REFERENCE_PREFIX}-{now.year}-{seq:06d}"
            stamp = now.isoformat()
            db.execute(
                "INSERT INTO claims (seq, reference, claim_id, employee_id, title, total, "
                "currency, document_ids, idempotency_key, payload_hash, status, received_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'received', ?, ?)",
                (
                    seq,
                    reference,
                    claim_id,
                    employee_id,
                    title,
                    total,
                    currency,
                    json.dumps(documents),
                    key,
                    digest,
                    stamp,
                    stamp,
                ),
            )
            db.execute(
                "INSERT INTO claim_history (seq, status, at, actor, comment) "
                "VALUES (?, 'received', ?, NULL, '')",
                (seq, stamp),
            )
        return SubmissionReceipt(reference=reference, received_at=now)

    def start_review(self, reference: str, reviewer_id: str) -> ClaimRecord:
        """``received`` -> ``under_review``."""
        reviewer = _text("reviewer_id", reviewer_id, max_len=100)
        with self._tx() as db:
            row = self._row(db, reference)
            if row["status"] != "received":
                raise InvalidTransitionError(
                    f"claim {row['reference']} is {row['status']}; only a 'received' claim can "
                    "be taken into review"
                )
            self._move(db, row, "under_review", reviewer, "")
            return self._record(db, row["seq"])

    def decide(
        self, reference: str, decision: Decision, approver_id: str, comment: str = ""
    ) -> ClaimRecord:
        """Approve or reject a ``received`` / ``under_review`` claim. Final."""
        if decision not in ("approved", "rejected"):
            raise InvalidClaimError("decision must be 'approved' or 'rejected'")
        approver = _text("approver_id", approver_id, max_len=100)
        note = comment.strip()
        if len(note) > 1000:
            raise InvalidClaimError("comment must be at most 1000 characters")
        if decision == "rejected" and not note:
            raise InvalidClaimError("a comment explaining the reason is required to reject a claim")
        with self._tx() as db:
            row = self._row(db, reference)
            if row["status"] in FINAL_STATUSES:
                raise InvalidTransitionError(
                    f"claim {row['reference']} is already {row['status']}; decisions are final"
                )
            if row["status"] == "received":
                self._move(db, row, "under_review", approver, "review started by the decision")
            self._move(db, row, decision, approver, note)
            return self._record(db, row["seq"])

    # -- queries -----------------------------------------------------------------------------

    def get(self, reference: str) -> ClaimRecord:
        with self._lock:
            return self._record(self._db, self._row(self._db, reference)["seq"])

    def find(
        self, status: ClaimStatus | None = None, employee_id: str | None = None
    ) -> list[ClaimSummary]:
        """Claims in submission order, optionally filtered by status and / or employee."""
        clauses: list[str] = []
        params: list[str] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if employee_id is not None:
            clauses.append("employee_id = ?")
            params.append(employee_id.strip())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._db.execute(f"SELECT * FROM claims{where} ORDER BY seq", params).fetchall()
        return [self._summary(row) for row in rows]

    # -- internals ---------------------------------------------------------------------------

    @staticmethod
    def _row(db: sqlite3.Connection, reference: str) -> sqlite3.Row:
        wanted = normalize_reference(reference)
        row = db.execute("SELECT * FROM claims WHERE reference = ?", (wanted,)).fetchone()
        if row is None:
            raise ClaimNotFoundError(f"no claim with reference '{wanted}'")
        return row

    def _move(
        self, db: sqlite3.Connection, row: sqlite3.Row, status: str, actor: str, comment: str
    ) -> None:
        stamp = self._now().isoformat()
        db.execute(
            "UPDATE claims SET status = ?, updated_at = ? WHERE seq = ?",
            (status, stamp, row["seq"]),
        )
        db.execute(
            "INSERT INTO claim_history (seq, status, at, actor, comment) VALUES (?, ?, ?, ?, ?)",
            (row["seq"], status, stamp, actor, comment),
        )

    @staticmethod
    def _summary(row: sqlite3.Row) -> ClaimSummary:
        return ClaimSummary(
            reference=row["reference"],
            claim_id=row["claim_id"],
            employee_id=row["employee_id"],
            title=row["title"],
            total=row["total"],
            currency=row["currency"],
            document_ids=json.loads(row["document_ids"]),
            status=row["status"],
            received_at=datetime.fromisoformat(row["received_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _record(self, db: sqlite3.Connection, seq: int) -> ClaimRecord:
        row = db.execute("SELECT * FROM claims WHERE seq = ?", (seq,)).fetchone()
        events = db.execute(
            "SELECT status, at, actor, comment FROM claim_history WHERE seq = ? ORDER BY id",
            (seq,),
        ).fetchall()
        history = [
            StatusEvent(
                status=e["status"],
                at=datetime.fromisoformat(e["at"]),
                actor=e["actor"],
                comment=e["comment"],
            )
            for e in events
        ]
        return ClaimRecord(**self._summary(row).model_dump(), history=history)
