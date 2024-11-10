import os
import uuid
import logging
import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters.command import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup,KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
import psycopg2
from psycopg2.extras import RealDictCursor
import aiogram

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# API Key and Database URL
API_KEY = "7886181806:AAHVgeAEWW6tJTgc3vB750Q8O-XIM4zNi00"
DB_URL="postgresql://postgres.jmujxtsvrbhlvthkkbiq:dbanMcmX9oxJyQlE@aws-0-eu-central-1.pooler.supabase.com:6543/postgres"

bot = Bot(token=API_KEY)
dp = Dispatcher()

db_connection = None
users_row = None
cafe_options = None
coffee_options = None
is_cafe_chosen = False
is_coffee_chosen = False

### Database Helpers ###

async def db_execute(query, params=None, fetch=False):
    try:
        with db_connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, params)
            db_connection.commit()
            if fetch:   
                return cursor.fetchall()
    except Exception as e:
        logger.error(f"Database error: {e}")
        return None


### Data Retrieval Helpers ###

async def retrieve_cafe_options():
    query = "SELECT * FROM cafes WHERE is_active = TRUE;"
    return await db_execute(query, fetch=True)


async def retrieve_menu(cafe_id):
    query = "SELECT * FROM menu WHERE cafe_id = %s;"
    return await db_execute(query, params=(cafe_id,), fetch=True)


async def create_order(telegram_id, cafe_id, menu_id):
    # Fetch the user_id using telegram_id
    query_get_user_id = "SELECT user_id FROM users WHERE telegram_id = %s;"
    user = await db_execute(query_get_user_id, params=(str(telegram_id),), fetch=True)  # Cast telegram_id to string

    if not user:
        logger.error(f"User with telegram_id={telegram_id} not found in the database.")
        return None

    user_id = user[0]["user_id"]

    # Insert the order
    query_create_order = """
        INSERT INTO orders (user_id, cafe_id, menu_id, order_date, status)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING order_id;
    """
    return await db_execute(query_create_order, params=(user_id, cafe_id, menu_id, datetime.now(), "pending"), fetch=True)


async def check_user_subscription(telegram_id):
    """Check if a user has an active subscription."""
    query = "SELECT * FROM users WHERE telegram_id = %s;"
    try:
        # Cast telegram_id to a string to match the database column type
        result = await db_execute(query, params=(str(telegram_id),), fetch=True)
        return result[0] if result else None
    except Exception as e:
        logger.error(f"Database error: {e}")
        return None
async def get_user_by_id(user_id):
    """Retrieve user information by user_id."""
    query = "SELECT * FROM users WHERE user_id = %s;"
    result = await db_execute(query, params=(user_id,), fetch=True)
    return result[0] if result else None
async def get_user_latest_order(user_id):
    query = """
        SELECT o.order_id, o.status, o.otp_code, m.coffee_name
        FROM orders o
        JOIN menu m ON o.menu_id = m.menu_id
        WHERE o.user_id = %s
        ORDER BY o.order_date DESC
        LIMIT 1;
    """
    result = await db_execute(query, params=(user_id,), fetch=True)
    return result[0] if result else None
async def display_users_credentials(message: types.Message):
    if not users_row:
        await message.answer("Данные пользователя не найдены.")
        return

    phone_number = users_row.get("phone_number", "Не указан")
    username = message.from_user.username or "Не указан"
    reply_message = (
        f"<b>Ваши данные:</b>\n"
        f"<b>Номер телефона: {phone_number}</b>\n"
        f"<b>Телеграм аккаунт: {username}</b>"
    )
    await message.answer(reply_message, parse_mode="HTML")


async def display_subscription_status(message: types.Message):
    if not users_row["subscription_status"]:
        reply_message = (
            "У вас еще нет подписки, для приобретения напишите слово «подписка» администратору @tratatapara.\n"
            "По подписке клиент получает 30 кофе в месяц, 1 кофе в день в любой партнерской кофейне."
        )
    else:
        reply_message = (
            "Спасибо за приобретение подписки. Инструкция по использованию.\n"
            "Вы можете получить 30 кофе в месяц, 1 кофе в день."
        )

    await send_message_and_menu_buttons(message, reply_message, ["Оформить заказ"])


async def send_message_and_menu_buttons(message, reply_message, buttons_names):
    keyboard = [[KeyboardButton(text=name)] for name in buttons_names]
    menu = ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=False)
    await message.answer(reply_message, reply_markup=menu)


### User Interaction ###


@dp.message(Command("start"))
async def start(message: types.Message):
    global users_row, is_cafe_chosen, is_coffee_chosen
    is_cafe_chosen = False
    is_coffee_chosen = False

    telegram_id = message.from_user.id

    users_row = await check_user_subscription(telegram_id)
    if users_row:
        await display_users_credentials(message)
        await display_subscription_status(message)
    else:
        reply_message = "Нажмите «разрешить», чтобы мы зарегистрировали ваш текущий номер телефона."
        menu = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Разрешить", request_contact=True)]],
            resize_keyboard=True,
        )
        await message.answer(reply_message, reply_markup=menu)

@dp.message(F.contact)
async def register_and_display_data(message: types.Message):
    telegram_id = message.from_user.id
    phone_number = message.contact.phone_number
    query = """
        INSERT INTO users (telegram_id, phone_number, subscription_status)
        VALUES (%s, %s, FALSE)
        ON CONFLICT (telegram_id) DO NOTHING;
    """
    await db_execute(query, params=(telegram_id, phone_number))
    await start(message)

@dp.message(F.text == "Оформить заказ")
async def handle_order_request(message: types.Message):
    global cafe_options
    
    telegram_id = message.from_user.id
    user = await check_user_subscription(telegram_id)
    
    # Проверка подписки
    if not user or not user["subscription_status"]:
        await message.answer("У вас нет активной подписки. Для её приобретения напишите администратору.")
        return
    
    # Проверка на лимит заказов
    query = """
        SELECT COUNT(*) AS daily_orders
        FROM orders
        JOIN users ON orders.user_id = users.user_id
        WHERE users.telegram_id = %s AND DATE(orders.order_date) = CURRENT_DATE;
    """
    result = await db_execute(query, params=(str(telegram_id),), fetch=True)
    daily_orders = result[0]["daily_orders"] if result else 0
    
    if daily_orders >= 1:
        await message.answer("Вы уже сделали заказ сегодня. Подписка позволяет заказывать 1 кофе в день.")
        return
    
    # Получение списка кафе
    cafe_options = await retrieve_cafe_options()
    if not cafe_options:
        await message.answer("К сожалению, сейчас нет доступных кафе.")
        return

    await show_cafe_selection(message, is_new=True)



async def show_cafe_selection(message, page=0, is_new=True):
    global cafe_options
    if not cafe_options:
        await message.answer("Нет доступных заведений.")
        return

    items_per_page = 4
    total_pages = (len(cafe_options) + items_per_page - 1) // items_per_page  # Вычисление общего числа страниц
    start_idx = page * items_per_page
    end_idx = min(start_idx + items_per_page, len(cafe_options))
    cafes_page = cafe_options[start_idx:end_idx]  # Выбираем только элементы текущей страницы

    # Кнопки для кафе
    buttons = [
        [InlineKeyboardButton(text=cafe["name"], callback_data=f"cafe_{cafe['cafe_id']}_{page}")]
        for cafe in cafes_page
    ]

    # Кнопки навигации
    navigation_buttons = []
    if page > 0:  # Если не первая страница, добавляем кнопку "<"
        navigation_buttons.append(InlineKeyboardButton(text="<", callback_data=f"cafe_prev_{page - 1}"))
    if page < total_pages - 1:  # Если не последняя страница, добавляем кнопку ">":
        navigation_buttons.append(InlineKeyboardButton(text=">", callback_data=f"cafe_next_{page + 1}"))

    if navigation_buttons:  # Добавляем навигационные кнопки только если они нужны
        buttons.append(navigation_buttons)

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    if is_new:  # Если это новое сообщение, отправляем его
        await message.answer("Выберите кафе:", reply_markup=keyboard)
    else:  # Если сообщение уже есть, редактируем его
        try:
            await message.edit_text("Выберите кафе:", reply_markup=keyboard)
        except aiogram.exceptions.TelegramBadRequest:
            # Если сообщение нельзя отредактировать, отправляем новое
            await message.answer("Выберите кафе:", reply_markup=keyboard)



@dp.callback_query(lambda c: c.data.startswith("cafe_"))
async def cafe_selected(callback_query: types.CallbackQuery):
    global coffee_options
    data = callback_query.data.split("_")
    if data[1] in ["next", "prev"]:
        await cafe_navigation(callback_query)
        return

    cafe_id, page = int(data[1]), int(data[2])
    coffee_options = await retrieve_menu(cafe_id)

    if not coffee_options:
        await callback_query.message.edit_text("В этом кафе нет доступного кофе.")
        return

    await show_coffee_selection(callback_query.message, cafe_id)

async def show_coffee_selection(message, cafe_id, page=0):
    items_per_page = 4
    total_pages = (len(coffee_options) + items_per_page - 1) // items_per_page  # Вычисление общего числа страниц
    start_idx = page * items_per_page
    end_idx = min(start_idx + items_per_page, len(coffee_options))
    coffee_page = coffee_options[start_idx:end_idx]  # Выбираем только элементы текущей страницы

    # Кнопки для кофе
    buttons = []
    for coffee in coffee_page:
        name = f"~{coffee['coffee_name']}~" if not coffee["is_available"] else coffee["coffee_name"]
        buttons.append([InlineKeyboardButton(text=name, callback_data=f"coffee_{coffee['menu_id']}_{cafe_id}_{page}")])

    # Кнопки навигации
    navigation_buttons = []
    if page > 0:  # Если не первая страница, добавляем кнопку "<"
        navigation_buttons.append(InlineKeyboardButton(text="<", callback_data=f"coffee_prev_{cafe_id}_{page - 1}"))
    if page < total_pages - 1:  # Если не последняя страница, добавляем кнопку ">":
        navigation_buttons.append(InlineKeyboardButton(text=">", callback_data=f"coffee_next_{cafe_id}_{page + 1}"))

    if navigation_buttons:  # Добавляем навигационные кнопки только если они нужны
        buttons.append(navigation_buttons)

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    if hasattr(message, 'reply_markup'):  # Если сообщение уже отправлено, редактируем его
        await message.edit_text("Выберите кофе:", reply_markup=keyboard)
    else:  # В противном случае отправляем новое сообщение
        await message.answer("Выберите кофе:", reply_markup=keyboard)


@dp.callback_query(lambda c: c.data.startswith("coffee_"))
async def coffee_selected(callback_query: types.CallbackQuery):
    data = callback_query.data.split("_")
    if data[1] in ["next", "prev"]:
        await coffee_navigation(callback_query)
        return

    menu_id, cafe_id, page = int(data[1]), int(data[2]), int(data[3])

    coffee = next((c for c in coffee_options if c["menu_id"] == menu_id), None)
    if not coffee:
        await callback_query.answer("Этот кофе не найден.", show_alert=True)
        return

    if not coffee["is_available"]:
        await callback_query.answer("Извините, этот кофе недоступен.", show_alert=True)
        return

    telegram_id = callback_query.from_user.id
    order = await create_order(telegram_id, cafe_id, menu_id)

    if order:
        order_id = order[0]["order_id"]
        await bot.send_message(
            chat_id=callback_query.from_user.id,
            text=f"Ваш заказ #{order_id} создан! Ожидайте готовности."
        )

        asyncio.create_task(monitor_order_status(telegram_id))
        await callback_query.answer("Кофе добавлен в заказ!")


@dp.callback_query(lambda c: c.data.startswith("coffee_next_") or c.data.startswith("coffee_prev_"))
async def coffee_navigation(callback_query: types.CallbackQuery):
    data = callback_query.data.split("_")
    cafe_id, page = int(data[2]), int(data[3])
    if "next" in data:
        await show_coffee_selection(callback_query.message, cafe_id, page=page + 1)
    elif "prev" in data:
        await show_coffee_selection(callback_query.message, cafe_id, page=page - 1)
    await callback_query.answer()

@dp.callback_query(lambda c: c.data.startswith("cafe_next_") or c.data.startswith("cafe_prev_"))
async def cafe_navigation(callback_query: types.CallbackQuery):
    data = callback_query.data.split("_")
    page = int(data[-1])
    if "next" in data:
        await show_cafe_selection(callback_query.message, page=page + 1, is_new=False)
    elif "prev" in data:
        await show_cafe_selection(callback_query.message, page=page - 1, is_new=False)
    await callback_query.answer()



async def monitor_order_status(telegram_id):
    """Monitor the status of an order and notify the user."""
    try:
        query = """
            SELECT o.order_id, o.status, o.otp_code, m.coffee_name
            FROM orders o
            JOIN menu m ON o.menu_id = m.menu_id
            JOIN users u ON o.user_id = u.user_id
            WHERE u.telegram_id = %s
            ORDER BY o.order_date DESC
            LIMIT 1;
        """
        order = await db_execute(query, params=(str(telegram_id),), fetch=True)  # Cast telegram_id to string

        if not order:
            logger.error(f"No recent orders found for telegram_id={telegram_id}")
            return

        order_id, last_status = order[0]["order_id"], order[0]["status"]

        while last_status != "готово":
            await asyncio.sleep(5)
            updated_order = await db_execute(query, params=(str(telegram_id),), fetch=True)

            if not updated_order or updated_order[0]["status"] == last_status:
                continue

            last_status = updated_order[0]["status"]
            if last_status == "готовится":
                await bot.send_message(
                    chat_id=telegram_id,
                    text=f"Ваш заказ #{order_id} обновлен. Статус: готовится."
                )
            elif last_status == "готово":
                otp_code = updated_order[0]["otp_code"]
                await bot.send_message(
                    chat_id=telegram_id,
                    text=f"Ваш заказ #{order_id} готов! Пожалуйста, подождите пока кассир не сгенерирует OTP код"
                )
                break
    except Exception as e:
        logger.error(f"Ошибка в monitor_order_status: {e}")




async def monitor_otp_updates():
    """Проверка базы данных на обновления OTP-кодов и уведомление пользователей."""
    while True:
        query = """
            SELECT o.order_id, o.otp_code, u.telegram_id
            FROM orders o
            JOIN users u ON o.user_id = u.user_id
            WHERE o.otp_code IS NOT NULL AND o.otp_notified = FALSE;
        """
        otp_orders = await db_execute(query, fetch=True)

        for order in otp_orders:
            order_id = order["order_id"]
            otp_code = order["otp_code"]
            telegram_id = order["telegram_id"]

            # Отправляем сообщение пользователю
            try:
                await bot.send_message(
                    chat_id=telegram_id,
                    text=f"Ваш заказ №{order_id} готов! Ваш OTP-код: {otp_code}"
                )
                # Обновляем флаг уведомления в базе данных
                update_query = "UPDATE orders SET otp_notified = TRUE WHERE order_id = %s;"
                await db_execute(update_query, params=(order_id,))
            except Exception as e:
                logger.error(f"Ошибка при отправке OTP-кода пользователю {telegram_id}: {e}")

        # Ждём 1 секунду перед следующей проверкой
        await asyncio.sleep(1)


async def main():
    global db_connection
    db_connection = psycopg2.connect(DB_URL)

    # Запуск мониторинга OTP-кодов
    asyncio.create_task(monitor_otp_updates())

    logger.info("Бот запущен и готов к работе")
    await dp.start_polling(bot)

    db_connection.close()



if __name__ == "__main__":
    asyncio.run(main())
