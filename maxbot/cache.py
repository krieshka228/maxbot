"""
cache.py — простой in-memory кэш с TTL для данных каталога.

Каталог запрашивается очень часто, товары обновляются редко.
Кэш сбрасывается при любом изменении товаров через invalidate_catalog_cache().
"""

import asyncio
import time
from typing import Any, Awaitable, Callable

DEFAULT_TTL = 60.0  # секунд

_cache: dict[str, tuple[float, Any]] = {}
_lock = asyncio.Lock()


async def get_or_set(key: str, loader: Callable[[], Awaitable[Any]], ttl: float = DEFAULT_TTL) -> Any:
    """Возвращает значение из кэша или вычисляет через loader() и кэширует."""
    now = time.monotonic()
    async with _lock:
        entry = _cache.get(key)
        if entry is not None and (now - entry[0]) < ttl:
            return entry[1]

    value = await loader()

    async with _lock:
        _cache[key] = (now, value)
    return value


def invalidate_catalog_cache() -> None:
    """Сбрасывает весь кэш каталога. Вызывать при любом изменении товаров."""
    _cache.clear()
