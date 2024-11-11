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
user_data = {}
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


async def retrieve_cafe_schedule(cafe_id):
    """Получить расписание работы кафе на основе текущего дня."""
    # Определяем тип дня (будний, суббота или воскресенье)
    weekday = datetime.now().weekday()  # Понедельник = 0, Воскресенье = 6
    if weekday < 5:
        day_type = "будний"
    elif weekday == 5:
        day_type = "суббота"
    else:
        day_type = "воскресенье"
    
    # SQL-запрос для получения расписания
    query = """
        SELECT open_time, close_time 
        FROM working_hours 
        WHERE cafe_id = %s AND day_type = %s;
    """
    schedule = await db_execute(query, params=(cafe_id, day_type), fetch=True)
    return schedule[0] if schedule else None


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
    username = message.from_user.username  # Получаем username пользователя
    first_name = message.from_user.first_name  # Получаем имя пользователя
    if username:  # Если username существует
        await register_user(telegram_id, username)
        if first_name:  # Если имя указано
            await message.answer(
                f"Привет, {first_name}! Добро пожаловать!"
            )
        else:  # Если имя отсутствует, приветствуем по username
            await message.answer(
                f"Привет, {username}! Добро пожаловать!"
            )
    else:  # Если username отсутствует
        await message.answer(
            "У вас нет установленного username в Telegram. Пожалуйста, установите его в настройках Telegram."
        )
    users_row = await check_user_subscription(telegram_id)
    if users_row:
        await display_subscription_status(message)

async def register_user(telegram_id: int, username: str):
    query = """
        INSERT INTO users (telegram_id, username, subscription_status)
        VALUES (%s, %s, FALSE)
        ON CONFLICT (telegram_id) DO NOTHING;
    """
    await db_execute(query, params=(telegram_id, username))


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
        WHERE users.telegram_id = %s
          AND DATE(orders.order_date) = CURRENT_DATE
          AND orders.status NOT IN ('canceled');  -- Исключаем отмененные заказы
    """
    result = await db_execute(query, params=(str(telegram_id),), fetch=True)
    daily_orders = result[0]["daily_orders"] if result else 0

    if daily_orders >= 1:
        await message.answer("Вы уже сделали заказ сегодня. Подписка позволяет заказывать 1 кофе в день.")
        return

    # Проверка наличия номера телефона у пользователя
    if not user["phone_number"]:  # Если номера телефона нет
        # Запрашиваем номер телефона
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📞 Отправить номер телефона", request_contact=True)],
                [KeyboardButton(text="❌ Отказаться")]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await message.answer(
            "Для оформления заказа необходимо указать ваш номер телефона. Пожалуйста, нажмите кнопку ниже.",
            reply_markup=keyboard,
        )
        return


    # Получение списка кафе
    cafe_options = await retrieve_cafe_options()
    if not cafe_options:
        await message.answer("К сожалению, сейчас нет доступных кафе.")
        return

    await show_cafe_selection(message)


@dp.message(F.contact)
async def handle_phone_number(message: types.Message):
    """Сохраняем номер телефона, если пользователь его отправил."""
    telegram_id = message.from_user.id
    phone_number = message.contact.phone_number

    # Сохраняем номер телефона в базу данных
    query = "UPDATE users SET phone_number = %s WHERE telegram_id = %s;"
    await db_execute(query, params=(phone_number, str(telegram_id)))

    await message.answer("Ваш номер телефона успешно сохранён! Теперь вы можете оформить заказ.")
    await handle_order_request(message)  # Перезапускаем процесс оформления заказа


@dp.message(F.text == "❌ Отказаться")
async def handle_decline_phone_request(message: types.Message):
    """Обрабатываем отказ от предоставления номера телефона."""
    await message.answer("Вы отказались предоставить номер телефона. Вы можете оформить заказ без него.")



async def show_cafe_selection(message, page=0):
    global cafe_options
    if not cafe_options:
        await message.answer("Нет доступных заведений.")
        return

    items_per_page = 4
    total_pages = (len(cafe_options) + items_per_page - 1) // items_per_page
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    cafes_page = cafe_options[start_idx:end_idx]

    # Кнопки для кафе с временем работы и ссылкой на 2ГИС
    buttons = []
    for cafe in cafes_page:
        schedule = await retrieve_cafe_schedule(cafe["cafe_id"])
        if schedule:
            close_time = schedule["close_time"].strftime("%H:%M")
            open_time = schedule["open_time"].strftime("%H:%M")
            text = f"{cafe['name']} - С {open_time} до {close_time}"
        else:
            text = f"{cafe['name']} - Расписание не указано"

        # Кнопка с названием кафе и кнопка "2ГИС" в одном ряду
        row = [
            InlineKeyboardButton(text=text, callback_data=f"cafe_{cafe['cafe_id']}"),   
            InlineKeyboardButton(text=f"📍 {cafe['location']}", url=cafe["location_url"]) if cafe.get("location_url") else None
        ]
        # Фильтруем None и добавляем в общий список
        buttons.append([btn for btn in row if btn])

    # Навигационные кнопки
    navigation_buttons = []
    if page > 0:
        navigation_buttons.append(InlineKeyboardButton(text="<--", callback_data=f"cafes_page_{page - 1}"))
    if page < total_pages - 1:
        navigation_buttons.append(InlineKeyboardButton(text="-->", callback_data=f"cafes_page_{page + 1}"))

    if navigation_buttons:
        buttons.append(navigation_buttons)

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    try:
        await message.edit_text("Выберите кафе:", reply_markup=keyboard)
    except aiogram.exceptions.TelegramBadRequest:
        await message.answer("Выберите кафе:", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("cafes_page_"))
async def navigate_cafe_pages(callback_query: types.CallbackQuery):
    """Навигация по страницам кафе."""
    page = int(callback_query.data.split("_")[2])
    await show_cafe_selection(callback_query.message, page=page)
    await callback_query.answer()


@dp.callback_query(F.data.startswith("cafe_"))
async def handle_cafe_selection(callback_query: types.CallbackQuery):
    """Обработка выбора кафе и отображение списка кофе."""
    cafe_id = int(callback_query.data.split("_")[1])

    # Проверить расписание работы кафе
    schedule = await retrieve_cafe_schedule(cafe_id)
    if not schedule:
        await callback_query.answer("У этого кафе нет указанного расписания.", show_alert=True)
        return

    now = datetime.now().time()
    if not (schedule["open_time"] <= now <= schedule["close_time"]):
        await callback_query.answer("К сожалению, это кафе сейчас не работает.", show_alert=True)
        return

    # Попытка загрузить меню
    try:
        await show_coffee_selection(callback_query.message, cafe_id)
    except Exception as e:
        await callback_query.answer(f"Ошибка: {e}", show_alert=True)
    await callback_query.answer()


async def show_coffee_selection(message, cafe_id, page=0):
    """Отображение списка кофе в выбранном кафе."""
    global coffee_options

    # Получение меню для выбранного кафе
    coffee_options = await retrieve_menu(cafe_id)
    if not coffee_options:
        await message.answer("В этом кафе пока нет доступного кофе.")
        return

    # Пагинация меню
    items_per_page = 4
    total_pages = (len(coffee_options) + items_per_page - 1) // items_per_page
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    coffee_page = coffee_options[start_idx:end_idx]

    # Создание кнопок для кофе
    buttons = [
        [
            InlineKeyboardButton(
                text=f"~{coffee['coffee_name']}~" if not coffee["is_available"] else coffee["coffee_name"],
                callback_data=f"coffee_{coffee['menu_id']}_{cafe_id}"
            )
        ]
        for coffee in coffee_page
    ]

    # Навигационные кнопки
    navigation_buttons = []
    if page > 0:
        navigation_buttons.append(InlineKeyboardButton(text="<--", callback_data=f"coffee_page_{cafe_id}_{page - 1}"))
    if page < total_pages - 1:
        navigation_buttons.append(InlineKeyboardButton(text="-->", callback_data=f"coffee_page_{cafe_id}_{page + 1}"))

    if navigation_buttons:
        buttons.append(navigation_buttons)

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    # Попытка редактирования сообщения, если невозможно — отправить новое
    try:
        await message.edit_text("Выберите кофе:", reply_markup=keyboard)
    except aiogram.exceptions.TelegramBadRequest:
        await message.answer("Выберите кофе:", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("coffee_page_"))
async def navigate_coffee_pages(callback_query: types.CallbackQuery):
    """Навигация по страницам кофе."""
    try:
        data = callback_query.data.split("_")
        cafe_id = int(data[2])
        page = int(data[3])

        await show_coffee_selection(callback_query.message, cafe_id, page=page)
        await callback_query.answer()
    except (IndexError, ValueError) as e:
        logger.error(f"Ошибка обработки навигации кофе: {e}")
        await callback_query.answer("Неверный формат данных. Попробуйте снова.", show_alert=True)

@dp.callback_query(F.data.startswith("coffee_"))
async def handle_coffee_selection(callback_query: types.CallbackQuery):
    """Обработка выбора кофе с возможностью добавления комментария."""
    try:
        data = callback_query.data.split("_")
        menu_id = int(data[1])  # ID выбранного кофе
        cafe_id = int(data[2])  # ID кафе, к которому принадлежит кофе

        # Проверить доступность кофе
        selected_coffee = next((coffee for coffee in coffee_options if coffee["menu_id"] == menu_id), None)
        if not selected_coffee:
            await callback_query.answer("Этот кофе не найден или недоступен.", show_alert=True)
            return

        if not selected_coffee["is_available"]:
            await callback_query.answer("Извините, этот кофе временно недоступен.", show_alert=True)
            return

        # Сохранить выбор кофе для текущего пользователя
        telegram_id = callback_query.from_user.id
        user_data[telegram_id] = {"cafe_id": cafe_id, "menu_id": menu_id}

        # Запросить комментарий к заказу
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Да", callback_data="add_comment_yes")],
                [InlineKeyboardButton(text="Нет", callback_data="add_comment_no")]
            ]
        )
        await callback_query.message.edit_text(
            f"Вы выбрали {selected_coffee['coffee_name']}. Хотите добавить комментарии к заказу? (например, сироп, сахар и т.д.)",
            reply_markup=keyboard
        )
        await callback_query.answer()
    except Exception as e:
        logger.error(f"Ошибка обработки выбора кофе: {e}")
        await callback_query.answer("Произошла ошибка. Попробуйте снова.", show_alert=True)


@dp.callback_query(F.data == "add_comment_yes")
async def handle_add_comment_yes(callback_query: types.CallbackQuery):
    """Запросить детали заказа у пользователя."""
    telegram_id = callback_query.from_user.id
    await callback_query.message.answer("Пожалуйста, отправьте сообщение с комментариями к вашему заказу (например, добавить сироп, сахар и т.д.).",reply_markup=None)
    user_data[telegram_id]["awaiting_comment"] = True  # Установить флаг ожидания комментария
    await callback_query.answer()


@dp.callback_query(F.data == "add_comment_no")
async def handle_add_comment_no(callback_query: types.CallbackQuery):
    """Создать заказ без комментариев."""
    telegram_id = callback_query.from_user.id
    order_data = user_data.get(telegram_id, {})
    
    if not order_data:
        await callback_query.answer("Произошла ошибка. Попробуйте снова.", show_alert=True)
        return

    # Создать заказ без деталей
    order = await create_order(telegram_id, order_data["cafe_id"], order_data["menu_id"])
    if order:
        order_id = order[0]["order_id"]
        
        # Создаем клавиатуру с кнопкой "Отменить"
        cancel_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"cancel_order_{order_id}")]
            ]
        )

        await callback_query.message.answer(
            f"Ваш заказ #{order_id} успешно создан! Если хотите отменить, нажмите кнопку ниже.",
            reply_markup=cancel_keyboard
        )
    else:
        await callback_query.answer("Произошла ошибка при создании заказа.", show_alert=True)


@dp.message(lambda message: user_data.get(message.from_user.id, {}).get("awaiting_comment"))
async def handle_order_comment(message: types.Message):
    """Обработать комментарий пользователя и создать заказ."""
    telegram_id = message.from_user.id
    order_data = user_data.get(telegram_id, {})

    if not order_data:
        await message.answer("Произошла ошибка. Попробуйте снова.")
        return

    # Создать заказ с деталями
    comment = message.text
    order = await create_order_with_details(telegram_id, order_data["cafe_id"], order_data["menu_id"], comment)
    if order:
        order_id = order[0]["order_id"]
        
        # Создаем клавиатуру с кнопкой "Отменить"
        cancel_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"cancel_order_{order_id}")]
            ]
        )

        await message.answer(
            f"Ваш заказ #{order_id} успешно создан с комментариями: {comment}. Если хотите отменить, нажмите кнопку ниже.",
            reply_markup=cancel_keyboard
        )
    else:
        await message.answer("Произошла ошибка при создании заказа.")
    
    # Очистить данные пользователя
    user_data.pop(telegram_id, None)


async def create_order_with_details(telegram_id, cafe_id, menu_id, details):
    """Создание заказа с комментариями."""
    query_get_user_id = "SELECT user_id FROM users WHERE telegram_id = %s;"
    user = await db_execute(query_get_user_id, params=(str(telegram_id),), fetch=True)

    if not user:
        logger.error(f"User with telegram_id={telegram_id} not found in the database.")
        return None

    user_id = user[0]["user_id"]

    query_create_order = """
        INSERT INTO orders (user_id, cafe_id, menu_id, order_date, status, details)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING order_id;
    """
    return await db_execute(query_create_order, params=(user_id, cafe_id, menu_id, datetime.now(), "pending", details), fetch=True)

@dp.callback_query(F.data.startswith("cancel_order_"))
async def cancel_order(callback_query: types.CallbackQuery):
    """Отмена заказа, если статус позволяет."""
    try:
        order_id = int(callback_query.data.split("_")[2])

        # Проверяем текущий статус заказа
        query = "SELECT status FROM orders WHERE order_id = %s;"
        result = await db_execute(query, params=(order_id,), fetch=True)

        if not result:
            await callback_query.answer("Заказ не найден.", show_alert=True)
            return

        current_status = result[0]["status"]

        if current_status == "готовится":
            await callback_query.answer("Этот заказ уже готовится и не может быть отменен.", show_alert=True)
            return
        elif current_status == "готово":
            await callback_query.answer("Этот заказ уже готов и не может быть отменен.", show_alert=True)
            return

        # Если статус позволяет, отменяем заказ
        update_query = "UPDATE orders SET status = 'canceled' WHERE order_id = %s;"
        await db_execute(update_query, params=(order_id,))

        # Уведомление пользователя об успешной отмене
        await callback_query.message.edit_text(
            f"Ваш заказ #{order_id} был успешно отменен. Теперь вы можете сделать другой заказ."
        )

        # Отправляем новое сообщение и удаляем старое
        await callback_query.message.delete()
        await bot.send_message(
            chat_id=callback_query.message.chat.id,
            text=f"Ваш заказ #{order_id} был успешно отменен. Теперь вы можете сделать другой заказ."
        )
        await callback_query.answer("Ваш заказ отменен.")
    except (IndexError, ValueError) as e:
        logger.error(f"Ошибка отмены заказа: {e}")
        await callback_query.answer("Произошла ошибка при отмене заказа. Попробуйте позже.", show_alert=True)



async def monitor_order_updates():
    """Проверка обновлений статусов заказов и уведомление пользователей."""
    while True:
        try:
            query = """
                SELECT o.order_id, o.status, u.telegram_id
                FROM orders o
                JOIN users u ON o.user_id = u.user_id
                WHERE o.status != 'готово' AND o.status_notified = FALSE;
            """
            pending_orders = await db_execute(query, fetch=True)

            for order in pending_orders:
                order_id = order["order_id"]
                last_status = order["status"]
                telegram_id = order["telegram_id"]

                try:
                    # Отправляем уведомление в зависимости от статуса
                    if last_status == "готовится":
                        await bot.send_message(
                            chat_id=telegram_id,
                            text=f"Ваш заказ №{order_id} обновлен. Статус: готовится."
                        )
                    elif last_status == "готово":
                        await bot.send_message(
                            chat_id=telegram_id,
                            text=f"Ваш заказ №{order_id} готов! Пожалуйста, подождите пока кассир не сгенерирует OTP код."
                        )

                    # Обновляем флаг уведомления в базе данных
                    update_query = "UPDATE orders SET status_notified = TRUE WHERE order_id = %s;"
                    await db_execute(update_query, params=(order_id,))

                except Exception as e:
                    logger.error(f"Ошибка при отправке уведомления о статусе заказа {order_id}: {e}")

            # Ждём перед следующей проверкой
            await asyncio.sleep(5)

        except Exception as e:
            logger.error(f"Ошибка в monitor_order_updates: {e}")





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
    asyncio.create_task(monitor_order_updates())

    logger.info("Бот запущен и готов к работе")
    await dp.start_polling(bot)

    db_connection.close()



if __name__ == "__main__":
    asyncio.run(main())