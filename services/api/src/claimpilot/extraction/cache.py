"""Extraction result cache keyed by (document hash, prompt version, model, effort).

A receipt that was already read with the same prompt and model is never paid for twice,
which also makes eval reruns free (ADR-004).
"""

from __future__ import annotations

from typing import Any, Protocol

from claimpilot.domain import ExtractedReceipt

DEFAULT_TTL_SECONDS = 30 * 24 * 3600


def cache_key(doc_sha256: str, prompt_version: str, model_key: str, effort: str | None) -> str:
    return f"extract:{prompt_version}:{model_key}:{effort or '-'}:{doc_sha256}"


class ExtractionCache(Protocol):
    async def get(self, key: str) -> ExtractedReceipt | None: ...

    async def set(self, key: str, receipt: ExtractedReceipt) -> None: ...


class InMemoryExtractionCache:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def get(self, key: str) -> ExtractedReceipt | None:
        raw = self._data.get(key)
        return ExtractedReceipt.model_validate_json(raw) if raw is not None else None

    async def set(self, key: str, receipt: ExtractedReceipt) -> None:
        self._data[key] = receipt.model_dump_json()


class RedisExtractionCache:
    def __init__(self, redis: Any, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        """``redis`` is a ``redis.asyncio.Redis`` (typed Any: its stubs mix sync and async)."""
        self._redis = redis
        self._ttl = ttl_seconds

    async def get(self, key: str) -> ExtractedReceipt | None:
        raw = await self._redis.get(key)
        return ExtractedReceipt.model_validate_json(raw) if raw is not None else None

    async def set(self, key: str, receipt: ExtractedReceipt) -> None:
        await self._redis.set(key, receipt.model_dump_json(), ex=self._ttl)
