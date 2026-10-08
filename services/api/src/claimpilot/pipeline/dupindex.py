"""The duplicate index backed by the ``documents`` table.

The trust layer asks "have we seen this receipt before?" through the ``DuplicateIndex`` protocol.
Here "seen" means any processed document in the database, from this employee or another (a
split bill or a shared receipt is just as worth flagging). Documents of the batch being
processed are remembered in memory as well, so two copies uploaded together are caught even
before the first one is saved.

Scale note: matching loads the hashes of processed documents and compares them in Python. That
is exactly right at demo scale (thousands of rows); a BK-tree or pgvector index would replace
``_candidates`` if this ever had to handle millions.
"""

from __future__ import annotations

from sqlalchemy import select

from claimpilot.db import Document, SessionFactory
from claimpilot.trust.duplicates import DuplicateMatch, SeenDocument, classify_match
from claimpilot.trust.phash import hamming, is_phash


def _distance(a: str | None, b: str | None) -> int | None:
    return (
        hamming(a, b) if a is not None and b is not None and is_phash(a) and is_phash(b) else None
    )


class DbDuplicateIndex:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions
        self._in_flight: dict[str, SeenDocument] = {}

    async def add(self, record: SeenDocument) -> None:
        self._in_flight[record.document_id] = record

    async def _candidates(self) -> dict[str, SeenDocument]:
        query = select(
            Document.id, Document.sha256, Document.phash, Document.fingerprint, Document.employee_id
        ).where(Document.status == "processed")
        async with self._sessions() as session:
            rows = (await session.execute(query)).all()
        seen = {
            row.id: SeenDocument(row.id, row.sha256, row.phash, row.fingerprint, row.employee_id)
            for row in rows
        }
        return {**seen, **self._in_flight}

    async def find_similar(
        self, *, phash: str, fingerprint: str | None, max_distance: int
    ) -> list[DuplicateMatch]:
        matches = []
        for record in (await self._candidates()).values():
            distance = _distance(phash, record.phash)
            kind = classify_match(
                distance=distance,
                fingerprint=fingerprint,
                stored_fingerprint=record.fingerprint,
                max_distance=max_distance,
            )
            if kind is not None:
                matches.append(
                    DuplicateMatch(
                        document_id=record.document_id,
                        employee_id=record.employee_id,
                        kind=kind,
                        distance=distance,
                        fingerprint=record.fingerprint,
                    )
                )
        return matches

    async def find_exact(self, *, sha256: str) -> list[DuplicateMatch]:
        return [
            DuplicateMatch(r.document_id, r.employee_id, "exact", 0, r.fingerprint)
            for r in (await self._candidates()).values()
            if r.sha256 == sha256
        ]
