from __future__ import annotations

import pytest

from claimpilot.storage import InMemoryStorage, LocalStorage, StorageError


async def test_local_storage_round_trip_and_nested_keys(tmp_path):
    store = LocalStorage(tmp_path / "uploads")
    await store.put("batch1/doc1.png", b"\x89PNG data")
    assert await store.get("batch1/doc1.png") == b"\x89PNG data"
    assert (tmp_path / "uploads" / "batch1" / "doc1.png").is_file()
    await store.put("batch1/doc1.png", b"replaced")  # overwrite is atomic
    assert await store.get("batch1/doc1.png") == b"replaced"
    assert not list((tmp_path / "uploads").rglob("*.tmp"))


async def test_delete_is_idempotent(tmp_path):
    store = LocalStorage(tmp_path)
    await store.put("a/b.bin", b"x")
    await store.delete("a/b.bin")
    await store.delete("a/b.bin")
    with pytest.raises(StorageError, match="no such object"):
        await store.get("a/b.bin")


@pytest.mark.parametrize(
    "key",
    [
        "../outside",
        "a/../../outside",
        "/etc/passwd",
        "",
        "a b",
        "..",
        "a/./../b",
        r"C:\x",
        ".hidden",
    ],
)
async def test_unsafe_keys_are_rejected(tmp_path, key):
    store = LocalStorage(tmp_path)
    with pytest.raises(StorageError, match="invalid storage key"):
        await store.put(key, b"x")
    with pytest.raises(StorageError):
        await store.get(key)


async def test_in_memory_storage():
    store = InMemoryStorage()
    await store.put("k", b"v")
    assert await store.get("k") == b"v"
    await store.delete("k")
    await store.delete("k")
    with pytest.raises(StorageError):
        await store.get("k")
