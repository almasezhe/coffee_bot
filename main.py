import os
import uuid
import logging
import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters.command import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
import psycopg2
from psycopg2.extras import RealDictCursor
import aiogram
from aiogram_inline_paginations import InlineKeyboardPaginator

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
@dp.message(Command("start"))
async def start_command_handler(message: types.Message):
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


async def send_message_and_menu_buttons(message, reply_message, buttons_names):
    keyboard = [[KeyboardButton(text=name)] for name in buttons_names]
    menu = ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=False)
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
    await start_command_handler(message)

@dp.message(F.text == "Оформить заказ")
async def handle_order_request(message: types.Message):
    global cafe_options
    cafe_options = await retrieve_cafe_options()

    if not cafe_options:
        await message.answer("К сожалению, сейчас нет доступных кафе.")
        return

    await show_cafe_selection(message)


async def show_cafe_selection(message, page=1):
    global cafe_options
    if not cafe_options:
        await message.answer("Нет доступных заведений.")
        return

    items_per_page = 4
    paginator = InlineKeyboardPaginator(
        len(cafe_options),
        current_page=page,
        data_pattern='cafe#{page}'
    )

    start_idx = (page - 1) * items_per_page
    end_idx = min(start_idx + items_per_page, len(cafe_options))
    cafes_page = cafe_options[start_idx:end_idx]

    # Creating buttons for the cafes
    for cafe in cafes_page:
        paginator.add_after(InlineKeyboardButton(text=cafe['name'], callback_data=f"cafe_{cafe['cafe_id']}"))

    await message.answer("Выберите кафе:", reply_markup=paginator.markup)


@dp.callback_query(lambda c: c.data.startswith("cafe#"))
async def cafe_page_callback(callback_query: types.CallbackQuery):
    page = int(callback_query.data.split("#")[1])
    await show_cafe_selection(callback_query.message, page=page)
    await callback_query.answer()

@dp.callback_query(lambda c: c.data.startswith("cafe_"))
async def cafe_selected(callback_query: types.CallbackQuery):
    global coffee_options
    cafe_id = int(callback_query.data.split("_")[1])
    coffee_options = await retrieve_menu(cafe_id)

    if not coffee_options:
        await callback_query.message.edit_text("В этом кафе нет доступного кофе.")
        return

    await show_coffee_selection(callback_query.message, cafe_id)


async def show_coffee_selection(message, cafe_id, page=1):
    items_per_page = 4
    paginator = InlineKeyboardPaginator(
        len(coffee_options),
        current_page=page,
        data_pattern=f'coffee#{cafe_id}#{page}'
    )

    start_idx = (page - 1) * items_per_page
    end_idx = min(start_idx + items_per_page, len(coffee_options))
    coffee_page = coffee_options[start_idx:end_idx]

    # Creating buttons for the coffees
    for coffee in coffee_page:
        name = f"~{coffee['coffee_name']}~" if not coffee["is_available"] else coffee["coffee_name"]
        paginator.add_after(InlineKeyboardButton(text=name, callback_data=f"coffee_{coffee['menu_id']}_{cafe_id}"))

    await message.answer("Выберите кофе:", reply_markup=paginator.markup)


@dp.callback_query(lambda c: c.data.startswith("coffee#"))
async def coffee_page_callback(callback_query: types.CallbackQuery):
    cafe_id, page = map(int, callback_query.data.split("#")[1:])
    await show_coffee_selection(callback_query.message, cafe_id, page=page)
    await callback_query.answer()

@dp.callback_query(lambda c: c.data.startswith("coffee_"))
async def coffee_selected(callback_query: types.CallbackQuery):
    data = callback_query.data.split("_")
    menu_id, cafe_id = int(data[1]), int(data[2])

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
        await callback_query.answer("Кофе добавлен в заказ!")


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
