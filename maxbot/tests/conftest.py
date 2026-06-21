"""Общие фикстуры для тестов.

Для тестов БД намеренно НЕ используется глобальный engine из db.py
(он создаётся один раз при импорте модуля из config.DATABASE_URL) —
вместо этого каждый тест получает свежую in-memory SQLite базу,
изолированную от других тестов и от реального orders.db.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import cache
import db as db_module


@pytest.fixture(autouse=True)
def _reset_catalog_cache():
    """TTL-кэш каталога (cache.py) — модуль-уровневый dict, общий для всех
    тестов. Без сброса между тестами разные in-memory БД могли бы отдавать
    друг другу устаревшие закэшированные результаты по одинаковым ключам
    (например, одинаковое имя категории в двух разных тестах)."""
    cache.invalidate_catalog_cache()
    yield
    cache.invalidate_catalog_cache()


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(db_module.Base.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as s:
        yield s

    await engine.dispose()
