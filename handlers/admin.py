"""
handlers/admin.py — Административные функции: Excel, синхронизация, подтверждение оплаты, управление остатками, QR-код.
"""

import io
import logging
from datetime import datetime, timedelta
import asyncio
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from db import PromoCode, PromoUsage
from utils import parse_datetime
import aiohttp
import aiomax
from aiomax import fsm, filters
from aiomax.buttons import KeyboardBuilder, CallbackButton
from config import ADMIN_USER_ID, CHANNEL_ID
from db import (
    get_session,
    get_all_users,
    OrderStatus,
    Product,
    User,
    upsert_product,
    set_bot_setting,
    get_bot_setting,
)
from excel_reports import build_monthly_report, build_clients_excel
from keyboards import (
    kb_back_to_menu,
    kb_admin_confirm_payment,
    kb_admin_menu,
)
from cache import invalidate_catalog_cache
from .catalog import delete_catalog_messages
from states import UserStates
from channel_publisher import publish_product_to_max
from sqlalchemy import delete
from db import OrderItem


logger = logging.getLogger(__name__)


PRODUCTS_PER_PAGE = 5
CATEGORIES_PER_PAGE = 8



def _subcategory_of(name: str) -> str:
    return name.split(",")[0].strip() if "," in name else name.strip()


def register(bot: aiomax.Bot) -> None:
    # ================== УПРАВЛЕНИЕ ТОВАРАМИ ==================

    @bot.on_button_callback("admin:manage_products")
    async def manage_products(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        """Показывает список категорий для управления товарами."""
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="Нет доступа.")
            return

        async for session in get_session():
            categories = (await session.execute(
                select(Product.category).where(Product.category != None).distinct().order_by(Product.category)
            )).scalars().all()

        if not categories:
            await cb.answer(text="📭 В базе нет товаров.", keyboard=kb_back_to_menu())
            return

        kb = KeyboardBuilder()
        for cat in categories:
            kb.row(CallbackButton(f"📂 {cat}", f"admin:prod_cat:{cat}"))
        kb.row(CallbackButton("⚙️ Админ-меню", "admin:menu"))
        await cb.answer(text="📦 **Управление товарами**\nВыберите категорию:", keyboard=kb, format="markdown")

    @bot.on_button_callback(lambda cb: cb.payload.startswith("admin:prod_cat:"))
    async def manage_products_category(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        """Показывает подкатегории выбранной категории."""
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="Нет доступа.")
            return

        category = cb.payload.split(":", 2)[2]
        cursor.change_data({"admin_current_cat": category, "admin_current_sub": None})

        async for session in get_session():
            products = (await session.execute(select(Product).where(Product.category == category))).scalars().all()

        subcategories = {}
        for p in products:
            sub = _subcategory_of(p.name)
            subcategories[sub] = subcategories.get(sub, 0) + 1

        if not subcategories:
            kb = KeyboardBuilder()
            kb.row(CallbackButton("↩️ К категориям", "admin:manage_products"))
            kb.row(CallbackButton("⚙️ Админ-меню", "admin:menu"))
            await cb.answer(text=f"В категории «{category}» нет товаров.", keyboard=kb)
            return

        kb = KeyboardBuilder()
        for sub in sorted(subcategories):
            kb.row(CallbackButton(f"📱 {sub} ({subcategories[sub]})", f"admin:prod_sub:{category}:{sub}"))
        kb.row(CallbackButton("↩️ К категориям", "admin:manage_products"))
        kb.row(CallbackButton("⚙️ Админ-меню", "admin:menu"))
        await cb.answer(
            text=f"📦 **Управление товарами** — {category}\nВыберите подкатегорию:",
            keyboard=kb,
            format="markdown"
        )

    @bot.on_button_callback(lambda cb: cb.payload.startswith("admin:prod_sub:"))
    async def manage_products_subcategory(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        """Показывает товары в выбранной подкатегории."""
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="Нет доступа.")
            return

        parts = cb.payload.split(":")
        category = parts[2]
        subcategory = parts[3]
        cursor.change_data({"admin_current_cat": category, "admin_current_sub": subcategory, "admin_current_page": 0})
        await show_manage_products_page(cb, cursor, page=0)

    @bot.on_button_callback(lambda cb: cb.payload.startswith("admin:prod_page:"))
    async def manage_products_page_handler(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        """Обработчик пагинации в управлении товарами."""
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="Нет доступа.")
            return

        page = int(cb.payload.split(":")[-1])
        await show_manage_products_page(cb, cursor, page=page)

    async def show_manage_products_page(cb: aiomax.Callback, cursor: fsm.FSMCursor, page: int = 0):
        data = cursor.get_data() or {}
        category = data.get("admin_current_cat")
        subcategory = data.get("admin_current_sub")

        if not category:
            await cb.answer("Сначала выберите категорию.", keyboard=kb_back_to_menu())
            return

        # ★ сохраняем контекст для последующих действий (скрыть/показать/удалить)
        cursor.change_data({
            "admin_current_cat": category,
            "admin_current_sub": subcategory,
            "admin_current_page": page
        })

        async for session in get_session():
            stmt = select(Product).where(Product.category == category)
            if subcategory:
                stmt = stmt.where(
                    (Product.name == subcategory) |
                    (Product.name.startswith(subcategory + ',')) |
                    (Product.name.startswith(subcategory + ' ,'))
                )
            total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar()
            products = (await session.execute(
                stmt.order_by(Product.id).offset(page * PRODUCTS_PER_PAGE).limit(PRODUCTS_PER_PAGE)
            )).scalars().all()

        if not products:
            kb = KeyboardBuilder()
            if subcategory:
                kb.row(CallbackButton("↩️ К подкатегориям", f"admin:prod_cat:{category}"))
            else:
                kb.row(CallbackButton("↩️ К категориям", "admin:manage_products"))
            kb.row(CallbackButton("⚙️ Админ-меню", "admin:menu"))
            await cb.answer(text=f"В этой категории нет товаров.", keyboard=kb)
            return

        total_pages = (total - 1) // PRODUCTS_PER_PAGE + 1
        title = f"{category} / {subcategory}" if subcategory else category
        lines = [f"📦 **Управление товарами** — {title} (стр. {page + 1}/{total_pages})\n"]
        kb = KeyboardBuilder()

        for p in products:
            status = "🟢 активен" if p.is_active else "🔴 скрыт"
            lines.append(f"• {p.name} — {status}")

            row = [
                CallbackButton("🗑 Удалить", f"admin:prod_delete:{p.id}", intent='negative'),
            ]
            if p.is_active:
                row.append(CallbackButton("🙈 Скрыть", f"admin:prod_hide:{p.id}", intent='default'))
            else:
                row.append(CallbackButton("👁 Показать", f"admin:prod_show:{p.id}", intent='default'))
            kb.row(*row)

        # Пагинация
        nav = []
        if page > 0:
            nav.append(CallbackButton("← Назад", f"admin:prod_page:{page - 1}"))
        if page < total_pages - 1:
            nav.append(CallbackButton("Вперёд →", f"admin:prod_page:{page + 1}"))
        if nav:
            kb.row(*nav)

        # Возврат
        if subcategory:
            kb.row(CallbackButton("↩️ К подкатегориям", f"admin:prod_cat:{category}"))
        else:
            kb.row(CallbackButton("↩️ К категориям", "admin:manage_products"))
        kb.row(CallbackButton("⚙️ Админ-меню", "admin:menu"))

        await cb.answer(text="\n".join(lines), keyboard=kb, format="markdown")

    # Удаление товара
    # Удаление товара
    @bot.on_button_callback(lambda cb: cb.payload.startswith("admin:prod_delete:"))
    async def product_delete(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="Нет доступа.")
            return

        product_id = int(cb.payload.split(":")[-1])
        data = cursor.get_data() or {}
        page = data.get("admin_current_page", 0)

        async for session in get_session():
            product = await session.get(Product, product_id)
            if not product:
                await cb.answer(notification="❌ Товар не найден.")
                return
            await session.execute(delete(OrderItem).where(OrderItem.product_id == product_id))
            await session.delete(product)
            await session.commit()
            invalidate_catalog_cache()  # <-- сброс кэша
            logger.info(f"admin deleted product id={product_id} name={product.name}")

        await show_manage_products_page(cb, cursor, page=page)

    # Скрыть товар
    @bot.on_button_callback(lambda cb: cb.payload.startswith("admin:prod_hide:"))
    async def product_hide(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="Нет доступа.")
            return

        product_id = int(cb.payload.split(":")[-1])
        data = cursor.get_data() or {}
        page = data.get("admin_current_page", 0)

        async for session in get_session():
            product = await session.get(Product, product_id)
            if not product:
                await cb.answer(notification="❌ Товар не найден.")
                return
            product.is_active = False
            await session.commit()
            invalidate_catalog_cache()  # <-- сброс кэша
            logger.info(f"admin hid product id={product_id} name={product.name}")

        await show_manage_products_page(cb, cursor, page=page)

    # Показать товар
    @bot.on_button_callback(lambda cb: cb.payload.startswith("admin:prod_show:"))
    async def product_show(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="Нет доступа.")
            return

        product_id = int(cb.payload.split(":")[-1])
        data = cursor.get_data() or {}
        page = data.get("admin_current_page", 0)

        async for session in get_session():
            product = await session.get(Product, product_id)
            if not product:
                await cb.answer(notification="❌ Товар не найден.")
                return
            product.is_active = True
            await session.commit()
            invalidate_catalog_cache()  # <-- сброс кэша
            logger.info(f"admin showed product id={product_id} name={product.name}")

        await show_manage_products_page(cb, cursor, page=page)

    @bot.on_button_callback("admin:publish_all")
    async def admin_publish_all(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        """Принудительная публикация всех неопубликованных товаров."""
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="Нет доступа.")
            return
        await cb.answer(notification="Начинаю публикацию...")
        asyncio.create_task(publish_all_products(bot, cb))

        # Получаем текущее состояние автопубликации для клавиатуры
        async for session in get_session():
            enabled = await get_bot_setting(session, "auto_publish_enabled")
            break
        is_enabled = enabled == "true"

        await cb.answer(
            text="✅ Публикация запущена в фоне.",
            keyboard=kb_admin_menu(is_enabled)
        )

    async def publish_all_products(bot: aiomax.Bot, cb: aiomax.Callback):
        """Публикует все товары, у которых нет max_post_id в Max."""
        async for session in get_session():
            products = (await session.execute(
                select(Product).where(
                    Product.is_active == True,
                    Product.max_post_id == None
                )
            )).scalars().all()
            break

        if not products:
            await cb.send("📭 Нет товаров для публикации.")
            return

        count = 0
        for product in products:
            post_id = await publish_product_to_max(bot, product, CHANNEL_ID)
            if post_id:
                async for session in get_session():
                    product.max_post_id = post_id
                    await session.commit()
                count += 1
            await asyncio.sleep(0.5)

        await cb.send(f"✅ Опубликовано {count} товаров.")
    # ------------------- Админ-меню -----------------------
    @bot.on_button_callback("admin:menu")
    async def admin_menu(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return

        async for session in get_session():
            enabled = await get_bot_setting(session, "auto_publish_enabled")
            break
        is_enabled = enabled == "true"

        from keyboards import kb_admin_menu
        await cb.answer(
            text="⚙️ **Админ‑меню:**",
            keyboard=kb_admin_menu(is_enabled),
            format="markdown"
        )

    # ------------------- УПРАВЛЕНИЕ БОНУСАМИ -----------------------
    @bot.on_button_callback("admin:bonus_menu")
    async def admin_bonus_menu(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        kb = KeyboardBuilder()
        kb.row(CallbackButton("👥 Начислить бонусы всем", "admin:bonus_add_all"))
        kb.row(CallbackButton("👀 Проверить баланс", "admin:bonus_check"))
        kb.row(CallbackButton("↩️ Назад", "admin:menu"))
        await cb.answer(text="💎 **Управление бонусами**", keyboard=kb, format="markdown")

    # --- начисление конкретному пользователю (по user_id) ---


    # --- начисление всем ---
    @bot.on_button_callback("admin:bonus_add_all")
    async def admin_bonus_add_all_start(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        cursor.change_state("admin_bonus_add_all")
        await cb.answer(notification=" ")
        await cb.send(
            "✏️ Введите сумму бонусов для начисления всем пользователям:\nПример: 500",
            keyboard=kb_back_to_menu(),
            format="markdown"
        )

    @bot.on_message(filters.state("admin_bonus_add_all"))
    async def handle_admin_bonus_add_all(message: aiomax.Message, cursor: fsm.FSMCursor):
        if message.sender.user_id != ADMIN_USER_ID:
            return
        try:
            amount = int(message.body.text.strip())
            if amount <= 0:
                raise ValueError
        except ValueError:
            await message.reply("❌ Введите положительное целое число.", keyboard=kb_admin_menu())
            cursor.clear()
            return

        count = 0
        async for session in get_session():
            users = await get_all_users(session)
            for user in users:
                if user.id != ADMIN_USER_ID:
                    user.bonus_balance_max = (user.bonus_balance_max or 0) + amount
                    count += 1
                    try:
                        await bot.send_message(
                            user_id=user.id,
                            text=f"🎉 Вам начислено {amount} бонусов!\n💰 Ваш баланс: {user.bonus_balance} бонусов.",
                            format="markdown"
                        )
                    except Exception:
                        pass
            await session.commit()
        cursor.clear()
        await message.reply(f"✅ {count} пользователям начислено по {amount} бонусов.", keyboard=kb_admin_menu())

    # --- проверка баланса ---
    @bot.on_button_callback("admin:bonus_check")
    async def admin_bonus_check_start(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        cursor.change_state("admin_bonus_check")
        await cb.answer(notification=" ")
        await cb.send(
            "✏️ Введите user_id пользователя для проверки баланса:",
            keyboard=kb_back_to_menu(),
            format="markdown"
        )

    @bot.on_message(filters.state("admin_bonus_check"))
    async def handle_admin_bonus_check(message: aiomax.Message, cursor: fsm.FSMCursor):
        if message.sender.user_id != ADMIN_USER_ID:
            return
        try:
            user_id = int(message.body.text.strip())
        except ValueError:
            await message.reply("❌ Введите корректный user_id.", keyboard=kb_admin_menu())
            cursor.clear()
            return

        async for session in get_session():
            user = await session.get(User, user_id)
            if not user:
                await message.reply(f"❌ Пользователь с ID {user_id} не найден.", keyboard=kb_admin_menu())
                cursor.clear()
                return
            balance = user.bonus_balance_max or 0
        cursor.clear()
        await message.reply(
            f"💎 Баланс пользователя {user_id}: {balance} бонусов.",
            keyboard=kb_admin_menu()
        )

    # ------------------- УПРАВЛЕНИЕ ПРОМОКОДАМИ -----------------------
    @bot.on_button_callback("admin:promo_menu")
    async def admin_promo_menu(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        kb = KeyboardBuilder()
        kb.row(CallbackButton("➕ Создать промокод", "admin:promo_create"))
        kb.row(CallbackButton("📋 Список промокодов", "admin:promo_list"))
        kb.row(CallbackButton("🗑 Удалить промокод", "admin:promo_delete"))
        kb.row(CallbackButton("↩️ Назад", "admin:bonus_menu"))
        await cb.answer(text="🎫 **Управление промокодами**", keyboard=kb, format="markdown")

    # --- создание промокода ---
    @bot.on_button_callback("admin:promo_create")
    async def admin_promo_create_start(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        cursor.change_state("admin_promo_create")
        await cb.answer(notification=" ")
        await cb.send(
            "✏️ Введите данные промокода в формате:\n"
            "КОД <бонусы> [лимит] [дата_окончания]\n\n"
            "Примеры:\n"
            "NEWYEAR 500 100 31-12-2026 — 500 бонусов, 100 активаций, до 31.12.2026\n"
            "WELCOME 200 — безлимитный и бессрочный",
            keyboard=kb_back_to_menu(),
            format="markdown"
        )

    @bot.on_message(filters.state("admin_promo_create"))
    async def handle_admin_promo_create(message: aiomax.Message, cursor: fsm.FSMCursor):
        if message.sender.user_id != ADMIN_USER_ID:
            return
        parts = message.body.text.strip().split()
        if len(parts) < 2:
            await message.reply("❌ Укажите код и количество бонусов.", keyboard=kb_admin_menu())
            cursor.clear()
            return

        code = parts[0].upper()
        try:
            bonus = int(parts[1])
        except ValueError:
            await message.reply("❌ Неверное количество бонусов.", keyboard=kb_admin_menu())
            cursor.clear()
            return

        max_uses = None
        expires_at = None
        for part in parts[2:]:
            try:
                max_uses = int(part)
                continue
            except ValueError:
                pass
            dt = parse_datetime(part)
            if dt:
                expires_at = dt
                continue
            await message.reply(f"❌ Не удалось распознать параметр '{part}'.", keyboard=kb_admin_menu())
            cursor.clear()
            return

        async for session in get_session():
            exists = (await session.execute(select(PromoCode).where(PromoCode.code == code))).scalar_one_or_none()
            if exists:
                await message.reply(f"❌ Промокод {code} уже существует.", keyboard=kb_admin_menu())
                cursor.clear()
                return
            promo = PromoCode(
                code=code,
                bonus_amount=bonus,
                max_uses=max_uses,
                expires_at=expires_at,
                created_by=message.sender.user_id,
                platform='MAX'  # ← привязка к платформе Max
            )
            session.add(promo)
            await session.commit()
        cursor.clear()
        msg = f"✅ Промокод {code} создан: {bonus} бонусов"
        if max_uses:
            msg += f", лимит {max_uses}"
        if expires_at:
            msg += f", до {expires_at.strftime('%d.%m.%Y')}"
        await message.reply(msg, keyboard=kb_admin_menu())

    # --- список промокодов ---
    @bot.on_button_callback("admin:promo_list")
    async def admin_promo_list(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        async for session in get_session():
            promos = (await session.execute(select(PromoCode).order_by(PromoCode.created_at.desc()))).scalars().all()
        if not promos:
            await cb.answer(text="🎫 Нет созданных промокодов.", keyboard=kb_back_to_menu())
            return
        lines = ["🎫 **Промокоды:**"]
        now = datetime.utcnow()
        for p in promos:
            active = "✅" if p.is_active else "❌"
            uses = f"{p.used_count}/{p.max_uses}" if p.max_uses else f"{p.used_count}/∞"
            expires_info = ""
            if p.expires_at:
                if p.expires_at < now:
                    expires_info = f" (истёк {p.expires_at.strftime('%d.%m.%Y')})"
                else:
                    expires_info = f" (до {p.expires_at.strftime('%d.%m.%Y %H:%M')})"
            lines.append(f"{active} `{p.code}` — {p.bonus_amount} бонусов, {uses}{expires_info}")
        await cb.answer(text="\n".join(lines), format="markdown", keyboard=kb_back_to_menu())

    # --- удаление промокода ---
    @bot.on_button_callback("admin:promo_delete")
    async def admin_promo_delete_start(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        cursor.change_state("admin_promo_delete")
        await cb.answer(notification=" ")
        await cb.send("✏️ Введите код промокода для удаления:", keyboard=kb_back_to_menu())

    @bot.on_message(filters.state("admin_promo_delete"))
    async def handle_admin_promo_delete(message: aiomax.Message, cursor: fsm.FSMCursor):
        if message.sender.user_id != ADMIN_USER_ID:
            return
        code = message.body.text.strip().upper()
        async for session in get_session():
            promo = (await session.execute(select(PromoCode).where(PromoCode.code == code))).scalar_one_or_none()
            if not promo:
                await message.reply(f"❌ Промокод {code} не найден.", keyboard=kb_admin_menu())
                cursor.clear()
                return
            await session.delete(promo)
            await session.commit()
        cursor.clear()
        await message.reply(f"✅ Промокод {code} удалён.", keyboard=kb_admin_menu())
    @bot.on_button_callback("admin:auto_publish_menu")
    async def auto_publish_menu(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return

        async for session in get_session():
            enabled = await get_bot_setting(session, "auto_publish_enabled")
            break
        is_enabled = enabled == "true"

        from keyboards import kb_auto_publish_menu
        await cb.answer(
            text="📤 **Управление автопубликацией**\n\n"
                 "Автопубликация автоматически выкладывает новые товары в канал Max.",
            keyboard=kb_auto_publish_menu(is_enabled),
            format="markdown"
        )

    @bot.on_button_callback("admin:auto_publish_toggle")
    async def auto_publish_toggle(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="Нет доступа.")
            return

        data = cursor.get_data() or {}
        if data.get("toggling"):
            return

        # Устанавливаем блокировку через change_data
        cursor.change_data({"toggling": True})

        try:
            async for session in get_session():
                enabled = await get_bot_setting(session, "auto_publish_enabled")
                break

            new_state = "false" if enabled == "true" else "true"
            async for session in get_session():
                await set_bot_setting(session, "auto_publish_enabled", new_state)
                break

            is_enabled = new_state == "true"
            from keyboards import kb_auto_publish_menu

            # Редактируем текущее сообщение и показываем уведомление
            await cb.answer(
                text="📤 **Управление автопубликацией**\n\n"
                     "Автопубликация автоматически выкладывает новые товары в канал Max.",
                keyboard=kb_auto_publish_menu(is_enabled),
                format="markdown",
                notification="Автопубликация переключена."
            )
        finally:
            # Снимаем блокировку
            cursor.change_data({"toggling": False})

    @bot.on_button_callback("auto_publish_status")
    async def status_stub(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        await cb.answer(notification=" ")
    # ------------------- Публикация товаров в канал Max -----------------------
    @bot.on_button_callback("admin:publish_to_channel")
    async def publish_to_channel(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        if not CHANNEL_ID:
            await cb.answer(notification="❌ CHANNEL_ID не настроен в .env.")
            return

        await cb.answer(notification="📤 Публикую товары в канал...")

        from channel_publisher import publish_pending_products
        published, failed, had_products = await publish_pending_products(bot)
        if not had_products:
            await cb.send("📭 Нет новых товаров для публикации — всё уже опубликовано.")
            return

        summary = f"✅ Опубликовано товаров: {published}."
        if failed:
            summary += f"\n⚠️ Не удалось опубликовать: {failed} (см. логи)."
        await cb.send(summary)

    # ------------------- Заказы за месяц (Excel) -----------------------
    @bot.on_button_callback("admin:excel:summary")
    async def excel_monthly(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        await cb.answer(notification="Формирую отчёт за месяц...")

        now = datetime.utcnow()
        month_ago = now - timedelta(days=30)

        async for session in get_session():
            from db import Order, OrderItem

            stmt = (
                select(Order)
                .where(Order.created_at >= month_ago)
                .options(
                    selectinload(Order.items).selectinload(OrderItem.product),
                    selectinload(Order.user),
                )
                .order_by(Order.created_at.desc())
            )
            result = await session.execute(stmt)
            orders = result.scalars().all()

            user_ids = {order.user_id for order in orders if order.user_id}
            users = []
            if user_ids:
                user_stmt = select(User).where(User.id.in_(user_ids))
                user_result = await session.execute(user_stmt)
                users = user_result.scalars().all()

        if not orders:
            await cb.answer(
                text="📊 За последний месяц нет заказов.",
                keyboard=kb_back_to_menu(),
                format="markdown"
            )
            return

        excel_bytes = build_monthly_report(orders, users)
        count_orders = len(orders)
        count_users = len(users)

        try:
            file_attachment = await bot.upload_file(excel_bytes, filename="monthly_report.xlsx")
            await cb.send(attachments=file_attachment)
            await cb.answer(
                text=f"📊 Отчёт за месяц: {count_orders} заказов, {count_users} клиентов.",
                keyboard=kb_admin_menu(),
                format="markdown"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки: {e}")
            await cb.answer(
                text=f"❌ Ошибка: {e}",
                keyboard=kb_admin_menu(),
                format="markdown"
            )

    # ------------------- База клиентов (Excel) -----------------------
    @bot.on_button_callback("admin:excel:clients")
    async def excel_clients(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        await cb.answer(notification="Формирую базу клиентов...")
        async for session in get_session():
            users = await get_all_users(session)
            if not users:
                await cb.answer(
                    text="👥 База клиентов пуста.",
                    keyboard=kb_admin_menu(),
                    format="markdown"
                )
                return
            excel_bytes = build_clients_excel(users)
            count = len(users)

        try:
            file_attachment = await bot.upload_file(excel_bytes, filename="clients.xlsx")
            await cb.send(attachments=file_attachment)
            await cb.answer(
                text=f"👥 База клиентов ({count} чел.) отправлена.",
                keyboard=kb_admin_menu(),
                format="markdown"
            )
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            await cb.answer(
                text=f"❌ Ошибка: {e}",
                keyboard=kb_admin_menu(),
                format="markdown"
            )

    # ------------------- Заказы к подтверждению -----------------------
    @bot.on_button_callback("admin:confirm_list")
    async def confirm_list(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        await cb.answer(notification=" ")
        async for session in get_session():
            from db import Order, OrderItem

            stmt = (
                select(Order)
                .where(Order.status == OrderStatus.paid)
                .options(
                    selectinload(Order.items).selectinload(OrderItem.product),
                    selectinload(Order.user),
                )
            )
            result = await session.execute(stmt)
            orders = result.scalars().all()

        if not orders:
            await cb.answer(
                text="✅ Нет заказов, ожидающих подтверждения.",
                keyboard=kb_admin_menu(),
                format="markdown"
            )
            return

        for order in orders:
            user = order.user
            user_info = (
                f"@{user.username}"
                if (user and user.username)
                else (user.full_name if user else f"ID {order.user_id}")
            )
            lines = [f"📦 **Заказ #{order.id}** — {user_info}"]
            for item in order.items:
                name = item.product.name if item.product else f"ID {item.product_id}"
                lines.append(
                    f"  • {name}: {item.quantity} шт. × {item.price_at_order:.0f} ₽"
                )
            lines.append(f"💰 Итого: {order.total_amount:.0f} ₽")
            await cb.send(
                "\n".join(lines),
                format="markdown",
                keyboard=kb_admin_confirm_payment(order.id),
            )
        await cb.answer(
            text="⚙️ **Админ-меню:**",
            keyboard=kb_admin_menu(),
            format="markdown"
        )



    # ------------------- Изменение остатка (stock) -----------------------
    ITEMS_PER_PAGE = 5
    CATEGORIES_PER_PAGE = 5

    @bot.on_button_callback("admin:set_stock_list")
    async def set_stock_list(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        await show_stock_level1(cb, page=0)

    async def show_stock_level1(cb: aiomax.Callback, page: int = 0):
        """Показывает список категорий для управления остатками."""
        async for session in get_session():
            total = (await session.execute(
                select(func.count(Product.category.distinct())).where(
                    Product.category != None
                )
            )).scalar()
            categories = (await session.execute(
                select(Product.category).where(
                    Product.category != None
                ).distinct()
                .order_by(Product.category)
            )).scalars().all()

        if total == 0:
            kb = KeyboardBuilder()
            kb.row(CallbackButton("⚙️ Админ-меню", "admin:menu"))
            await cb.answer("📭 В базе нет товаров.", keyboard=kb)
            return

        total_pages = (total - 1) // ITEMS_PER_PAGE + 1
        page = max(0, min(page, total_pages - 1))
        start = page * ITEMS_PER_PAGE
        end = start + ITEMS_PER_PAGE
        page_categories = categories[start:end]

        kb = KeyboardBuilder()
        for cat in page_categories:
            kb.row(CallbackButton(cat, f"admin:stock_level2:{cat}"))
        nav_row = []
        if page > 0:
            nav_row.append(CallbackButton("← Назад", f"admin:stock_level1_page:{page - 1}"))
        if page < total_pages - 1:
            nav_row.append(CallbackButton("Вперёд →", f"admin:stock_level1_page:{page + 1}"))
        if nav_row:
            kb.row(*nav_row)
        kb.row(CallbackButton("🔍 Поиск по артикулу", "admin:stock_search_article"))
        kb.row(CallbackButton("⚙️ Админ-меню", "admin:menu"))

        await cb.answer(
            text=f"**Категории** (стр. {page + 1}/{total_pages})",
            keyboard=kb,
            format="markdown"
        )

    @bot.on_button_callback(lambda cb: cb.payload.startswith("admin:stock_level1_page:"))
    async def stock_level1_page(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        page = int(cb.payload.split(":")[-1])
        await show_stock_level1(cb, page)

    @bot.on_button_callback(lambda cb: cb.payload.startswith("admin:stock_level2:"))
    async def stock_level2_page(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        level1 = cb.payload.split(":", 2)[2]
        await show_stock_level2(cb, level1, page=0)

    def _subcategory_of(name: str) -> str:
        """Возвращает «подкатегорию» товара — часть названия до первой
        запятой (тот же принцип, что и в каталоге, см. handlers/catalog.py:
        show_category_page фильтрует по `name == subcategory or
        name.startswith(subcategory + ',')`). В схеме БД отдельной колонки
        подкатегории нет — она всегда выводится из Product.name."""
        return name.split(",", 1)[0].strip() if "," in name else name

    async def show_stock_level2(cb: aiomax.Callback, level1: str, page: int = 0):
        """Показывает подкатегории (по названиям товаров) внутри категории
        level1. Включает ВСЕ товары категории, а не только активные —
        админу нужно видеть и скрытые (stock=0), чтобы восстановить остаток."""
        async for session in get_session():
            products = (await session.execute(
                select(Product.name).where(Product.category == level1)
            )).scalars().all()

        subcats = sorted({_subcategory_of(n) for n in products})
        total = len(subcats)

        kb = KeyboardBuilder()
        if not subcats:
            kb.add(CallbackButton("↩️ К категориям", "admin:set_stock_list", intent='default'))
            kb.add(CallbackButton("⚙️ Админ-меню", "admin:menu", intent='default'))
            await cb.answer(text=f"В категории «{level1}» нет товаров.", keyboard=kb, format="markdown")
            return

        total_pages = (total - 1) // CATEGORIES_PER_PAGE + 1
        page = max(0, min(page, total_pages - 1))
        page_subcats = subcats[page * CATEGORIES_PER_PAGE: (page + 1) * CATEGORIES_PER_PAGE]

        header = f"**{level1}** — подкатегории (стр. {page + 1}/{total_pages})"
        for cat in page_subcats:
            kb.add(CallbackButton(cat, f"admin:stock_category:{level1}:{cat}", intent='default'))
            kb.row()

        nav = []
        if page > 0:
            nav.append(CallbackButton("← Назад", f"admin:stock_level2_page:{level1}:{page - 1}", intent='default'))
        if page < total_pages - 1:
            nav.append(CallbackButton("Вперёд →", f"admin:stock_level2_page:{level1}:{page + 1}", intent='default'))
        if nav:
            kb.row(*nav)
        kb.row(CallbackButton("↩️ К категориям", "admin:set_stock_list", intent='default'))
        kb.row(CallbackButton("⚙️ Админ-меню", "admin:menu", intent='default'))
        await cb.answer(text=header, keyboard=kb, format="markdown")

    @bot.on_button_callback(lambda cb: cb.payload.startswith("admin:stock_level2_page:"))
    async def stock_level2_page_nav(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        parts = cb.payload.split(":")
        level1 = parts[2]
        page = int(parts[3])
        await show_stock_level2(cb, level1, page)

    @bot.on_button_callback(lambda cb: cb.payload.startswith("admin:stock_category:"))
    async def stock_category_page(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        parts = cb.payload.split(":")
        level1 = parts[2]
        category = parts[3]
        await show_stock_products_page(cb, level1, category, 0)

    @bot.on_button_callback(lambda cb: cb.payload.startswith("admin:stock_catpage:"))
    async def stock_catpage(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        parts = cb.payload.split(":")
        level1 = parts[2]
        category = parts[3]
        page = int(parts[4])
        await show_stock_products_page(cb, level1, category, page)

    async def show_stock_products_page(cb: aiomax.Callback, level1: str, category: str, page: int):
        async for session in get_session():
            # level1 = категория (Product.category), category = подкатегория,
            # выводимая из Product.name (см. _subcategory_of выше) — та же
            # логика сопоставления, что и в каталоге (handlers/catalog.py).
            all_products = (await session.execute(
                select(Product).where(Product.category == level1)
            )).scalars().all()
            matched = [
                p for p in all_products
                if p.name == category or p.name.startswith(category + ",")
            ]

        if not matched:
            await cb.answer(
                text=f"В подкатегории «{category}» нет товаров.",
                keyboard=kb_back_to_menu(),
                format="markdown"
            )
            return

        total = len(matched)
        total_pages = (total - 1) // ITEMS_PER_PAGE + 1
        page = max(0, min(page, total_pages - 1))
        products = matched[page * ITEMS_PER_PAGE: (page + 1) * ITEMS_PER_PAGE]
        lines = [f"**Остатки: {category}** (стр. {page+1}/{total_pages})\n"]
        kb = KeyboardBuilder()

        for p in products:
            if p.stock is not None:
                stock_str = f"{p.stock} шт."
                if not p.is_active:
                    stock_str += " (скрыт)"
            else:
                stock_str = "∞"
            lines.append(f"• {p.name} — на складе: {stock_str}")
            kb.add(CallbackButton(f"✏️ {p.name[:25]}", f"admin:set_stock_select:{p.id}", intent='default'))
            kb.add(CallbackButton("🗑", f"admin:delete_prompt:{level1}:{category}:{page}:{p.id}", intent='negative'))
            kb.row()

        nav = []
        if page > 0:
            nav.append(
                CallbackButton("← Назад", f"admin:stock_catpage:{level1}:{category}:{page - 1}", intent='default'))
        if page < total_pages - 1:
            nav.append(
                CallbackButton("Вперёд →", f"admin:stock_catpage:{level1}:{category}:{page + 1}", intent='default'))
        if nav:
            kb.row(*nav)
        kb.row(CallbackButton("↩️ К подкатегориям", f"admin:stock_level2:{level1}", intent='default'))
        kb.row(CallbackButton("⚙️ Админ-меню", "admin:menu", intent='default'))

        await cb.answer(text="\n".join(lines), keyboard=kb, format="markdown")

    # ------------------- Удаление товара из списка остатков (с возвратом) ---
    @bot.on_button_callback(lambda cb: cb.payload.startswith("admin:delete_prompt:"))
    async def delete_prompt(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        parts = cb.payload.split(":")
        level1, category, page, product_id = parts[2], parts[3], parts[4], parts[5]
        kb = KeyboardBuilder()
        kb.row(CallbackButton("✅ Да, удалить", f"admin:delete_confirm:{level1}:{category}:{page}:{product_id}", intent='negative'))
        kb.row(CallbackButton("❌ Отмена", f"admin:cancel_delete:{level1}:{category}:{page}", intent='default'))
        await cb.answer(text=f"Удалить товар #{product_id}?", keyboard=kb, format="markdown")

    @bot.on_button_callback(lambda cb: cb.payload.startswith("admin:delete_confirm:"))
    async def delete_confirm(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        parts = cb.payload.split(":")
        level1, category, page, product_id = parts[2], parts[3], int(parts[4]), int(parts[5])

        from sqlalchemy import delete as sql_delete
        from db import OrderItem

        async for session in get_session():
            product = await session.get(Product, product_id)
            if not product:
                await cb.answer(text="❌ Товар не найден.", keyboard=kb_admin_menu(), format="markdown")
                return
            name = product.name
            # Сначала удаляем связанные позиции заказов — иначе упадём на FK
            # (см. telegram_bot/bot/handlers/admin.py — тот же порядок).
            await session.execute(sql_delete(OrderItem).where(OrderItem.product_id == product_id))
            await session.delete(product)
            await session.commit()
            invalidate_catalog_cache()
            logger.info(f"admin deleted product id={product_id} name={name}")

        await cb.answer(notification=f"✅ Товар «{name}» удалён.")
        # Возвращаемся к тому же списку (с той же страницей — она сама
        # скорректируется, если товаров на ней больше не осталось).
        await show_stock_products_page(cb, level1, category, page)

    @bot.on_button_callback(lambda cb: cb.payload.startswith("admin:cancel_delete:"))
    async def cancel_delete(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        parts = cb.payload.split(":")
        level1, category, page = parts[2], parts[3], int(parts[4])
        await show_stock_products_page(cb, level1, category, page)
    @bot.on_button_callback("admin:stock_search_article")
    async def stock_search_article_start(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        cursor.change_state("admin_stock_search_article")
        await cb.answer(notification=" ")
        await cb.send("🔎 Введите артикул товара для изменения остатка:", keyboard=kb_back_to_menu())

    @bot.on_message(filters.state("admin_stock_search_article"))
    async def stock_search_article_result(message: aiomax.Message, cursor: fsm.FSMCursor):
        if message.sender.user_id != ADMIN_USER_ID:
            return
        article = message.body.text.strip() if message.body and message.body.text else ""
        if not article:
            await message.reply("❌ Введите артикул.", keyboard=kb_back_to_menu())
            return

        async for session in get_session():
            product = (await session.execute(
                select(Product).where(Product.article == article)
            )).scalar_one_or_none()
            break

        if not product:
            await message.reply("🔎 Товар с таким артикулом не найден.", keyboard=kb_admin_menu())
            cursor.clear()
            return

        cursor.change_state("admin_set_stock")
        cursor.change_data({"product_id": product.id})
        await message.reply(
            f"Товар: **{product.name}**\nАртикул: {product.article}\nТекущий остаток: {product.stock or 0}\n\n"
            "✏️ Введите новое количество (целое число) или 0, чтобы скрыть товар:",
            keyboard=kb_back_to_menu(),
            format="markdown"
        )

    @bot.on_button_callback(lambda cb: cb.payload.startswith("admin:set_stock_select:"))
    async def set_stock_select(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        product_id = int(cb.payload.split(":")[-1])
        cursor.change_state("admin_set_stock")
        cursor.change_data({"product_id": product_id})
        await cb.answer(notification=" ")
        await cb.send("✏️ Введите новое количество (целое число) или 0, чтобы скрыть товар:", keyboard=kb_back_to_menu())

    @bot.on_message(filters.state("admin_set_stock"))
    async def handle_set_stock(message: aiomax.Message, cursor: fsm.FSMCursor):
        if message.sender.user_id != ADMIN_USER_ID:
            return
        data = cursor.get_data() or {}
        product_id = data.get("product_id")
        from  validators import parse_non_negative_int
        new_stock = parse_non_negative_int(message.body.text if message.body else None)
        if new_stock is None:
            await message.reply("❌ Введите целое неотрицательное число.", keyboard=kb_back_to_menu())
            return

        async for session in get_session():
            product = await session.get(Product, product_id)
            if not product:
                await message.reply("❌ Товар не найден.", keyboard=kb_admin_menu())
                cursor.clear()
                return
            product.stock = new_stock
            product.is_active = new_stock > 0
            product.in_stock = new_stock > 0
            await session.commit()
            invalidate_catalog_cache()
            await message.reply(
                f"✅ Остаток товара «{product.name}» обновлён: {new_stock}",
                keyboard=kb_admin_menu()
            )
        cursor.clear()

    # ------------------- Управление QR-кодом оплаты -----------------------
    @bot.on_button_callback("admin:payment_qr")
    async def admin_payment_qr_menu(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        kb = KeyboardBuilder()
        kb.add(CallbackButton("🖼 Загрузить QR-код", "admin:upload_qr", intent='default'))
        kb.row(CallbackButton("👀 Показать текущий", "admin:show_qr", intent='default'))
        kb.row(CallbackButton("🗑 Удалить QR-код", "admin:delete_qr", intent='default'))
        kb.row(CallbackButton("⚙️ Админ-меню", "admin:menu", intent='default'))
        await cb.answer(
            text="**Управление QR-кодом для оплаты**",
            keyboard=kb,
            format="markdown"
        )

    @bot.on_button_callback("admin:upload_qr")
    async def admin_upload_qr_start(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        cursor.change_state(UserStates.ADMIN_PAYMENT_QR)
        await cb.answer(notification=" ")
        await cb.send("📷 Пришлите PNG-изображение с QR-кодом:", keyboard=kb_back_to_menu())

    @bot.on_message(filters.state(UserStates.ADMIN_PAYMENT_QR))
    async def admin_upload_qr_finish(message: aiomax.Message, cursor: fsm.FSMCursor):
        if message.sender.user_id != ADMIN_USER_ID:
            return
        # Ищем первое изображение во вложениях
        token = None
        if message.body and hasattr(message.body, "attachments") and message.body.attachments:
            for att in message.body.attachments:
                if att.type == "image" and hasattr(att, "token") and att.token:
                    token = att.token
                    break
        if not token:
            await message.reply("❌ Пришлите изображение в формате PNG.", keyboard=kb_back_to_menu())
            return

        # Сохраняем Max-токен (он уже загружен в Max)
        async for session in get_session():
            await set_bot_setting(session, "payment_qr_token", token)
        cursor.clear()
        await message.reply(
            "✅ QR-код сохранён. Теперь он будет показываться покупателям при оформлении заказа.",
            keyboard=kb_admin_menu()
        )

    @bot.on_button_callback("admin:show_qr")
    async def admin_show_qr(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        async for session in get_session():
            token = await get_bot_setting(session, "payment_qr_token")
        if not token:
            await cb.answer(notification="QR-код не задан.")
            return
        await cb.send(
            attachments=aiomax.PhotoAttachment(token=token),
            keyboard=kb_back_to_menu()
        )
        await cb.answer(notification=" ")

    @bot.on_button_callback("admin:delete_qr")
    async def admin_delete_qr(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        async for session in get_session():
            await set_bot_setting(session, "payment_qr_token", "")
        await cb.answer(notification="QR-код удалён.")
    # ------------------- Удаление товаров по артикулам -----------------------
    @bot.on_button_callback("admin:delete_by_articles")
    async def delete_by_articles_start(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        cursor.change_state("admin_delete_articles")
        await cb.answer(notification=" ")
        await cb.send(
            "🗑️ Введите артикулы товаров через запятую для удаления:\n"
            "Пример: `A001, A002, A003`",
            keyboard=kb_back_to_menu(),
            format="markdown"
        )

    @bot.on_message(filters.state("admin_delete_articles"))
    async def handle_delete_by_articles(message: aiomax.Message, cursor: fsm.FSMCursor):
        if message.sender.user_id != ADMIN_USER_ID:
            return
        text = message.body.text.strip() if message.body and message.body.text else ""
        if not text:
            await message.reply("❌ Введите хотя бы один артикул.", keyboard=kb_back_to_menu())
            return

        articles = [a.strip() for a in text.split(",") if a.strip()]
        if not articles:
            await message.reply("❌ Не распознаны артикулы.", keyboard=kb_back_to_menu())
            return

        deleted = []
        not_found = []
        async for session in get_session():
            for article in articles:
                product = (await session.execute(
                    select(Product).where(Product.article == article)
                )).scalar_one_or_none()
                if product:
                    await session.delete(product)
                    deleted.append(article)
                else:
                    not_found.append(article)
            await session.commit()

        from cache import invalidate_catalog_cache
        invalidate_catalog_cache()

        lines = []
        if deleted:
            lines.append(f"✅ Удалены: {', '.join(deleted)}")
        if not_found:
            lines.append(f"⚠️ Не найдены: {', '.join(not_found)}")
        cursor.clear()
        await message.reply("\n".join(lines) or "Ничего не сделано.", keyboard=kb_admin_menu())
