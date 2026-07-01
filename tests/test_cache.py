"""Unit-тесты для cache.py — TTL-кэш каталога (см. promt.md, раздел
«Оптимизация и производительность» — TTL-кэш для категорий, 60 сек)."""

import asyncio

import pytest

import cache


@pytest.fixture(autouse=True)
def _reset_cache():
    cache.invalidate_catalog_cache()
    yield
    cache.invalidate_catalog_cache()


async def test_get_or_set_caches_value():
    calls = []

    async def loader():
        calls.append(1)
        return "value"

    result1 = await cache.get_or_set("key1", loader, ttl=60)
    result2 = await cache.get_or_set("key1", loader, ttl=60)

    assert result1 == "value"
    assert result2 == "value"
    # loader должен вызваться только один раз — второй раз отдаётся из кэша.
    assert len(calls) == 1


async def test_get_or_set_respects_ttl_expiry():
    calls = []

    async def loader():
        calls.append(1)
        return len(calls)

    # TTL=0 -> запись считается устаревшей сразу же.
    await cache.get_or_set("key2", loader, ttl=0)
    await asyncio.sleep(0.01)
    await cache.get_or_set("key2", loader, ttl=0)

    assert len(calls) == 2


async def test_invalidate_catalog_cache_clears_everything():
    async def loader():
        return "x"

    await cache.get_or_set("a", loader, ttl=60)
    await cache.get_or_set("b", loader, ttl=60)
    assert "a" in cache._cache
    assert "b" in cache._cache

    cache.invalidate_catalog_cache()

    assert cache._cache == {}


async def test_different_keys_are_independent():
    async def loader_a():
        return "A"

    async def loader_b():
        return "B"

    result_a = await cache.get_or_set("cat:a", loader_a, ttl=60)
    result_b = await cache.get_or_set("cat:b", loader_b, ttl=60)

    assert result_a == "A"
    assert result_b == "B"
