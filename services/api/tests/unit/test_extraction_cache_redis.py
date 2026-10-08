from __future__ import annotations

from claimpilot.domain import DocType, ExtractedReceipt
from claimpilot.extraction.cache import RedisExtractionCache


class StubRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, name: str) -> str | None:
        return self.store.get(name)

    async def set(self, name: str, value: str, *, ex: int) -> None:
        self.store[name] = value
        self.ttls[name] = ex


async def test_redis_cache_round_trip_with_ttl():
    redis = StubRedis()
    cache = RedisExtractionCache(redis, ttl_seconds=60)
    receipt = ExtractedReceipt(doc_type=DocType.fuel_slip, total=1500.0)

    assert await cache.get("k") is None
    await cache.set("k", receipt)
    assert await cache.get("k") == receipt
    assert redis.ttls["k"] == 60
