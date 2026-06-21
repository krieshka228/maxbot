"""Тесты для db.py — критических функций работы с общей БД (Telegram +
Max используют одну схему, см. promt.md, раздел «Главное требование:
единая БД»). Используют изолированную in-memory SQLite (см. conftest.py)."""

import cache
import db


async def test_upsert_product_creates_with_correct_field_names(session):
    """Регрессия: исходная версия upsert_product передавала в Product(...)
    несуществующие kwargs photo_ids=/video_ids= вместо photo_file_ids=/
    video_file_ids=, из-за чего создание товара падало с TypeError."""
    cache.invalidate_catalog_cache()
    product = await db.upsert_product(
        session,
        post_id="post-1",
        name="Тестовый товар",
        price=123.0,
        photo_file_ids="tg_photo_1,tg_photo_2",
        video_file_ids="tg_video_1",
        article="ABC1",
        category="Категория А",
        stock=5,
    )
    assert product.id is not None
    assert product.photo_file_ids == "tg_photo_1,tg_photo_2"
    assert product.video_file_ids == "tg_video_1"
    assert product.is_active is True  # stock=5 > 0 и in_stock=True по умолчанию


async def test_upsert_product_sets_inactive_when_out_of_stock(session):
    cache.invalidate_catalog_cache()
    product = await db.upsert_product(
        session, post_id="post-2", name="Товар Б", price=10.0, stock=0,
    )
    assert product.is_active is False


async def test_upsert_product_updates_existing_by_post_id(session):
    """Регрессия: при обновлении исходная версия делала
    product.photo_ids = photo_ids — несуществующий атрибут, который
    тихо создавался как обычный Python-атрибут и НЕ сохранялся в БД."""
    cache.invalidate_catalog_cache()
    await db.upsert_product(session, post_id="post-3", name="В1", price=10.0, stock=1)
    updated = await db.upsert_product(
        session, post_id="post-3", name="В1 (обновлено)", price=15.0,
        stock=3, photo_file_ids="new_photo",
    )
    assert updated.name == "В1 (обновлено)"
    assert updated.price == 15.0
    assert updated.photo_file_ids == "new_photo"

    # Убеждаемся, что в БД создалась ровно одна запись (а не дубликат).
    all_products = await db.get_all_active_products(session)
    assert len([p for p in all_products if p.post_id == "post-3"]) == 1


async def test_get_active_categories_excludes_inactive(session):
    cache.invalidate_catalog_cache()
    await db.upsert_product(session, post_id="p1", name="A", price=1, category="Кат1", stock=1)
    await db.upsert_product(session, post_id="p2", name="B", price=1, category="Кат2", stock=0)  # неактивен

    categories = await db.get_active_categories(session)
    assert "Кат1" in categories
    assert "Кат2" not in categories


async def test_get_active_products_in_category(session):
    cache.invalidate_catalog_cache()
    cache.invalidate_catalog_cache()
    await db.upsert_product(session, post_id="p1", name="A", price=1, category="Кат1", stock=2)
    await db.upsert_product(session, post_id="p2", name="B", price=1, category="Кат1", stock=3)
    await db.upsert_product(session, post_id="p3", name="C", price=1, category="Кат2", stock=1)

    products = await db.get_active_products_in_category(session, "Кат1")
    names = {p.name for p in products}
    assert names == {"A", "B"}


async def test_get_products_without_max_post(session):
    cache.invalidate_catalog_cache()
    p1 = await db.upsert_product(session, post_id="p1", name="A", price=1, stock=1)
    await db.upsert_product(session, post_id="p2", name="B", price=1, stock=1)

    pending = await db.get_products_without_max_post(session)
    assert {p.post_id for p in pending} == {"p1", "p2"}

    await db.mark_product_published(session, p1, "max-msg-123")
    pending_after = await db.get_products_without_max_post(session)
    assert {p.post_id for p in pending_after} == {"p2"}


async def test_get_or_create_draft_reuses_existing_draft(session):
    order1 = await db.get_or_create_draft(session, user_id=1)
    order2 = await db.get_or_create_draft(session, user_id=1)
    assert order1.id == order2.id


async def test_add_item_to_order_accumulates_quantity(session):
    product = await db.upsert_product(session, post_id="p1", name="A", price=50.0, stock=10)
    order = await db.get_or_create_draft(session, user_id=2)

    await db.add_item_to_order(session, order, product, 2)
    await session.refresh(order, attribute_names=["items"])
    await db.add_item_to_order(session, order, product, 3)
    await session.refresh(order, attribute_names=["items"])

    assert len(order.items) == 1
    assert order.items[0].quantity == 5
    assert order.total_amount == 250.0


async def test_remove_item_from_order(session):
    product = await db.upsert_product(session, post_id="p1", name="A", price=20.0, stock=10)
    order = await db.get_or_create_draft(session, user_id=3)
    item = await db.add_item_to_order(session, order, product, 1)

    removed = await db.remove_item_from_order(session, order, item.id)
    assert removed is True
    assert order.total_amount == 0.0


async def test_catalog_cache_invalidated_after_upsert(session):
    """Регрессия: каталог должен сбрасывать TTL-кэш при изменении товаров
    (см. cache.py), иначе клиенты видят устаревшие остатки до 60 секунд."""
    cache.invalidate_catalog_cache()
    await db.upsert_product(session, post_id="p1", name="A", price=1, category="К1", stock=1)
    first = await db.get_active_categories(session)
    assert first == ["К1"]

    # Меняем категорию у того же товара — кэш должен сброситься внутри upsert_product.
    await db.upsert_product(session, post_id="p1", name="A", price=1, category="К2", stock=1)
    second = await db.get_active_categories(session)
    assert second == ["К2"]
