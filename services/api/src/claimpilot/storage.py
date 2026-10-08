"""Blob storage for uploaded documents (ADR-009: a local volume now, S3-compatible later).

Callers depend on the ``Storage`` protocol; ``LocalStorage`` keeps files under one root and
refuses any key that could escape it.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Protocol

_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,200}$")


class StorageError(Exception):
    pass


class Storage(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _path(self, key: str) -> Path:
        if not _SAFE_KEY.fullmatch(key) or ".." in key.split("/"):
            raise StorageError(f"invalid storage key: {key!r}")
        path = (self._root / key).resolve()
        if not path.is_relative_to(self._root):
            raise StorageError(f"key escapes the storage root: {key!r}")
        return path

    async def put(self, key: str, data: bytes) -> None:
        path = self._path(key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)  # atomic: readers never see a half-written file

        await asyncio.to_thread(write)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise StorageError(f"no such object: {key}") from exc

    async def delete(self, key: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(path.unlink, True)


class InMemoryStorage:
    """For tests."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    async def get(self, key: str) -> bytes:
        try:
            return self.objects[key]
        except KeyError as exc:
            raise StorageError(f"no such object: {key}") from exc

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)
