"""Receipt files on the local disk, checked before anything is read or sent.

``upload_receipts`` is the one tool that touches the machine it runs on, so it is strict: only
JPEG, PNG, WebP and PDF, the API's own size and count limits, no folders, no guessing at relative
paths, and optionally a single folder it may read from. Every problem is collected and reported
together so the model can fix the whole list in one go. Nothing is read until every file passes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from claimpilot_mcp.errors import UploadError
from claimpilot_mcp.text import ELLIPSIS, plain_text

# By extension; the bytes must agree (the API sniffs the content and ignores the name).
EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".pdf"})
MAX_PROBLEMS_SHOWN = 8
HTTP_WITHOUT_ROOT = (
    "upload_receipts reads files from the machine this server runs on, and this server is "
    "running over HTTP without CLAIMPILOT_UPLOAD_ROOT. Run it over stdio, or set "
    "CLAIMPILOT_UPLOAD_ROOT to a folder it may read."
)


@dataclass(frozen=True, slots=True)
class Upload:
    """One receipt ready to send: its bare file name, media type and bytes."""

    name: str
    media_type: str
    content: bytes


class _Refused(Exception):
    """One file cannot be uploaded; the message says why."""


def sniff(head: bytes) -> str | None:
    """The media type the first bytes announce, if it is one the API accepts."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def _shown(raw: str, limit: int = 100) -> str:
    """The path as the model gave it, cleaned, keeping its end: the file name is what matters."""
    clean = plain_text(raw, 4096)
    return clean if len(clean) <= limit else ELLIPSIS + clean[-(limit - 1) :]


def _locate(raw: str, root: Path | None) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        if root is None:
            raise _Refused("give the full path of the file (a relative path is ambiguous here)")
        path = root / path
    path = path.resolve()
    if root is not None and not path.is_relative_to(root):
        raise _Refused("outside the folder uploads may be read from")
    return path


def _check(path: Path, limit: int, limit_mb: int) -> None:
    if not path.exists():
        raise _Refused("file not found")
    if path.is_dir():
        raise _Refused("this is a folder; pass the receipt files inside it instead")
    if not path.is_file():
        raise _Refused("not a regular file")
    if path.suffix.lower() not in EXTENSIONS:
        raise _Refused("only JPEG, PNG, WebP and PDF files can be uploaded")
    size = path.stat().st_size
    if size == 0:
        raise _Refused("the file is empty")
    if size > limit:
        raise _Refused(f"larger than {limit_mb} MB")


def _read(path: Path, limit: int, limit_mb: int) -> Upload:
    content = path.read_bytes()
    if len(content) > limit:  # it grew since it was measured
        raise _Refused(f"larger than {limit_mb} MB")
    media_type = sniff(content[:12])
    if media_type is None:
        raise _Refused("the content is not a JPEG, PNG, WebP or PDF, whatever the name says")
    return Upload(path.name, media_type, content)


def load_uploads(
    paths: Sequence[str],
    *,
    max_files: int,
    max_file_mb: int,
    root: Path | None,
    anywhere: bool,
) -> list[Upload]:
    """Validate ``paths`` and read them, or raise ``UploadError`` listing everything wrong.

    ``root`` confines reads to one folder (relative paths are resolved inside it); without it any
    absolute path is allowed only when ``anywhere`` is true (stdio: the user's own machine).
    Blocking disk I/O: call it from a worker thread.
    """
    if root is None and not anywhere:
        raise UploadError([HTTP_WITHOUT_ROOT])
    if not paths:
        raise UploadError(["Give at least one file to upload."])
    if len(paths) > max_files:
        raise UploadError([f"At most {max_files} files per upload, got {len(paths)}."])
    root = root.resolve() if root else None
    limit = max_file_mb * 1024 * 1024

    problems: list[str] = []
    located: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        shown = _shown(raw)
        try:
            path = _locate(raw, root)
            _check(path, limit, max_file_mb)
            if path in seen:
                raise _Refused("listed twice")
        except _Refused as refusal:
            problems.append(f"{shown}: {refusal}.")
        except (OSError, ValueError):  # a name the file system rejects, an unreadable link...
            problems.append(f"{shown}: cannot be read.")
        else:
            seen.add(path)
            located.append(path)

    uploads: list[Upload] = []
    if not problems:
        for path in located:
            try:
                uploads.append(_read(path, limit, max_file_mb))
            except _Refused as refusal:
                problems.append(f"{_shown(path.name)}: {refusal}.")
            except OSError:
                problems.append(f"{_shown(path.name)}: cannot be read.")

    if problems:
        extra = len(problems) - MAX_PROBLEMS_SHOWN
        shown_problems = problems[:MAX_PROBLEMS_SHOWN]
        if extra > 0:
            shown_problems.append(f"...and {extra} more.")
        raise UploadError(shown_problems)
    return uploads
