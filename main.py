import os
import uuid
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters.command import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup,KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
import psycopg2
from psycopg2.extras import RealDictCursor
import aiogram

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
# API Key and Database URL
API_KEY = "8103008160:AAFlMNkjk84genN5awpUcUDIayEc3DJyHO0"
DB_URL="postgresql://postgres.jmujxtsvrbhlvthkkbiq:dbanMcmX9oxJyQlE@aws-0-eu-central-1.pooler.supabase.com:6543/postgres"
bot = Bot(token=API_KEY)
dp = Dispatcher()
astana_tz = timezone(timedelta(hours=5))
users_row = None
cafe_options = None
coffee_options = None
is_cafe_chosen = False  
is_coffee_chosen = False
user_data = {}
### Database Helpers ###

async def db_execute(query, params=None, fetch=False):
    global db_connection
    if db_connection.closed:
        db_connection = psycopg2.connect(DB_URL)
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
    weekday = datetime.now(astana_tz).weekday()  # Понедельник = 0, Воскресенье = 6
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


async def create_order(telegram_id, cafe_id, menu_id,take_out):
    # Fetch the user_id using telegram_id
    query_get_user_id = "SELECT user_id FROM users WHERE telegram_id = %s;"
    user = await db_execute(query_get_user_id, params=(str(telegram_id),), fetch=True)  # Cast telegram_id to string

    if not user:
        logger.error(f"User with telegram_id={telegram_id} not found in the database.")
        return None

    user_id = user[0]["user_id"]

    # Check for unfinished orders
    unfinished_query = """
        SELECT COUNT(*) AS unfinished_orders
        FROM orders
        WHERE user_id = %s AND is_finished = FALSE;
    """
    unfinished_result = await db_execute(unfinished_query, params=(user_id,), fetch=True)
    unfinished_orders = unfinished_result[0]["unfinished_orders"] if unfinished_result else 0

    if unfinished_orders > 0:
        logger.info(f"User with telegram_id={telegram_id} has unfinished orders. Cannot create a new order.")
        #TODO 
        return {"error": "У вас есть незавершенный заказ. Завершите его, прежде чем оформлять новый."}

    # Check daily limit
    daily_limit_query = """
        SELECT COUNT(*) AS daily_orders
        FROM orders
        WHERE user_id = %s
          AND DATE(order_date) = CURRENT_DATE
          AND status NOT IN ('canceled');
    """
    daily_limit_result = await db_execute(daily_limit_query, params=(user_id,), fetch=True)
    daily_orders = daily_limit_result[0]["daily_orders"] if daily_limit_result else 0

    if daily_orders >= 1:
        logger.info(f"User with telegram_id={telegram_id} has reached their daily order limit.")
        return {"error": "Вы уже сделали заказ сегодня. Подписка позволяет заказывать 1 кофе в день."}

    # Insert the order
    query_create_order = """
        INSERT INTO orders (user_id, cafe_id, menu_id, order_date, status,take_out)
        VALUES (%s, %s, %s, %s, %s,%s)
        RETURNING order_id;
    """
    try:
        result = await db_execute(
            query_create_order,
            params=(user_id, cafe_id, menu_id, datetime.now(astana_tz), "pending",take_out),
            fetch=True
        )
        return {"success": result}
    except Exception as e:
        logger.error(f"Error creating order for user_id={user_id}: {e}")
        return {"error": "Failed to create order due to an internal error."}


async def get_order_by_id(order_id):
    """Получить информацию о заказе по его order_id."""
    query = """
SELECT 
    o.order_id,
    o.user_id,
    o.menu_id,
    o.order_date,
    o.status,
    o.details,
    o.take_out, -- Добавляем колонку take_out
    m.coffee_name,
    u.username,
    u.phone_number,
    c.cafe_tg
FROM orders o
JOIN menu m ON o.menu_id = m.menu_id
JOIN users u ON o.user_id = u.user_id
JOIN cafes c ON o.cafe_id = c.cafe_id
WHERE o.order_id = %s;

    """
    result = await db_execute(query, params=(order_id,), fetch=True)
    print("DEBUG:", result)
    return result[0] if result else None


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
"У вас еще нет подписки 🥺 \n\n"

"Для приобретения напишите администратору @tratatapara ✍️\n\n"

"По подписке вы получите 30 кофе в месяц во всех партнерских кофейнях ☕️\n\n"

"Где вы можете использовать свою подписку? ✅: \n\n"

"- Coffee Moose | Мангилик ел 56 \n"
"https://2gis.kz/astana/geo/70000001090408124\n\n"

"- Coffee Moose | Айнаколь 66\n"
"https://2gis.kz/astana/geo/70000001094220075\n\n"

"- Coffee Original | Республика 27\n"
"https://2gis.kz/astana/geo/70000001075322780\n\n"

"- EspressoDay | Назарбаев Университет (доступен только студентам НУ и персоналу НУ)\n"
"https://2gis.kz/astana/branches/70000001075103852/firm/70000001080140646/71.395796%2C51.090729?m=71.397718%2C51.092483%2F15.29\n\n"

"Пишите скорее нашему администратору @tratatapara, и мы позаботимся о вашем комфорте в каждой выпитой чашке кофе 🫶",
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
    reply_message= await message.answer(reply_message, reply_markup=menu)
    asyncio.create_task(delete_message_after_timeout(reply_message, 4000)) 



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
            greeting=await message.answer(
                f"Привет, {first_name} 🥳\nДобро пожаловать в Refill - сервис подписки на кофе 🤗"
            )
        else:  # Если имя отсутствует, приветствуем по username
            greeting=await message.answer(
                f"Привет, {username} 🥳\nДобро пожаловать в Refill - сервис подписки на кофе 🤗"
            )
    else:  # Если username отсутствует
        greeting=await message.answer(
            "У вас нет установленного username в Telegram. Пожалуйста, установите его в настройках Telegram."
        )
    asyncio.create_task(delete_message_after_timeout(greeting, 4000)) 

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
        subs=await message.answer(
            "У вас нет активной подписки 🥺\n"
            "Для её приобретения напишите администратору \n@tratatapara ✅"
        )
        asyncio.create_task(delete_message_after_timeout(subs, 4000)) 

        return

    # Проверка на незавершенные заказы
    unfinished_query = """
        SELECT COUNT(*) AS unfinished_orders
        FROM orders
        JOIN users ON orders.user_id = users.user_id
        WHERE users.telegram_id = %s
          AND orders.is_finished = FALSE;
    """
    unfinished_result = await db_execute(unfinished_query, params=(str(telegram_id),), fetch=True)
    unfinished_orders = unfinished_result[0]["unfinished_orders"] if unfinished_result else 0

    if unfinished_orders > 0:
        unfinish=await message.answer("У вас есть незавершенный заказ. Завершите его, прежде чем оформлять новый.")
        asyncio.create_task(delete_message_after_timeout(unfinish, 4000)) 

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
        daily=await message.answer("Вы уже сделали заказ сегодня. Подписка позволяет заказывать 1 кофе в день.")
        asyncio.create_task(delete_message_after_timeout(daily, 4000)) 

        return

    # Проверка наличия номера телефона у пользователя
    if user["request_number"]:  # Если нужно спрашивать номер телефона
        # Запрашиваем номер телефона
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📞 Отправить номер телефона", request_contact=True)],
                [KeyboardButton(text="❌ Отказаться")]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        phone=await message.answer(
            "Для оформления заказа нам необходим ваш номер телефона ☎️\n\n"
            "Он будет использован для уточнения деталей ваших заказов 🤗\n\n"
            "Пожалуйста, нажмите кнопку ниже и разрешите доступ к номеру ✅.",
            reply_markup=keyboard,
        )
        asyncio.create_task(delete_message_after_timeout(phone, 4000)) 

        return

    # Получение списка кафе
    cafe_options = await retrieve_cafe_options()
    if not cafe_options:
        not_dostup_kafe=await message.answer("К сожалению, сейчас нет доступных кафе.")
        asyncio.create_task(delete_message_after_timeout(not_dostup_kafe, 4000)) 
        return

    await show_cafe_selection(message)



@dp.message(F.contact)
async def handle_phone_number(message: types.Message):
    """Сохраняем номер телефона, если пользователь его отправил."""
    telegram_id = message.from_user.id
    phone_number = message.contact.phone_number

    query = "UPDATE users SET phone_number = %s, request_number = FALSE WHERE telegram_id = %s;"
    

    await db_execute(query, params=(phone_number, str(telegram_id)))
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Оформить заказ")
            ]
        ],
        resize_keyboard=True,  # Уменьшает размер кнопки
        one_time_keyboard=False  # Скрывает клавиатуру после нажатия
    )
    oform=await message.answer("Ваш номер телефона успешно сохранён ✅\n\n""Теперь вы можете оформить заказ 🥳", reply_markup=keyboard)
    asyncio.create_task(delete_message_after_timeout(oform, 4000)) 
    await handle_order_request(message)  # Перезапускаем процесс оформления заказа


@dp.message(F.text == "❌ Отказаться")
async def handle_decline_phone_request(message: types.Message):
    telegram_id = message.from_user.id
    print(telegram_id)  # Debugging: Ensure the ID is correct

    # Define the keyboard
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Оформить заказ")
            ]
        ],
        resize_keyboard=True,  # Reduce button size
        one_time_keyboard=False  # Keep the keyboard open
    )

    # Correct database query
    query = "UPDATE users SET request_number = FALSE WHERE telegram_id = %s;"
    try:
        # Pass the parameter as a tuple
        await db_execute(query, params=(telegram_id,))  
    except Exception as e:
        # Handle database errors for debugging
        logging.error(f"Database error: {e}")
        await message.answer("Произошла ошибка при обновлении данных. Попробуйте позже.")
        return

    # Handle the user's decline to share their phone number
    otkaz = await message.answer(
        "Вы отказались предоставить номер телефона 😔\n\n"
        "Вы можете оформить заказ и без него ✅",
        reply_markup=keyboard
    )
    asyncio.create_task(delete_message_after_timeout(otkaz, 4000))

    # Retrieve available cafe options
    cafe_options = await retrieve_cafe_options()
    if not cafe_options:
        not_dostup_kafe = await message.answer("К сожалению, сейчас нет доступных кафе.")
        asyncio.create_task(delete_message_after_timeout(not_dostup_kafe, 4000))
        return

    await show_cafe_selection(message)



async def show_cafe_selection(message):
    cafe_options = await retrieve_cafe_options()
    if not cafe_options:
        not_dostup_kafe=await message.answer("Нет доступных заведений.")
        asyncio.create_task(delete_message_after_timeout(not_dostup_kafe, 4000)) 
        return

    buttons = []
    for cafe in cafe_options:
        schedule = await retrieve_cafe_schedule(cafe["cafe_id"])
        if schedule:
            close_time = schedule["close_time"].strftime("%H:%M")
            open_time = schedule["open_time"].strftime("%H:%M")
            text = f"{cafe['name']}"
        else:
            text = f"{cafe['name']}"

        # Кнопка с названием кафе и кнопка "2ГИС" в одном ряду
        row = [
            InlineKeyboardButton(text=text, callback_data=f"cafe_{cafe['cafe_id']}"),   
            InlineKeyboardButton(text=f"📍 {cafe['location']}", url=cafe["location_url"]) if cafe.get("location_url") else None
        ]
        # Фильтруем None и добавляем в общий список
        buttons.append([btn for btn in row if btn])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    try:
        kafe_choose=await message.edit_text("Выберите кофейню 👇:", reply_markup=keyboard)
        asyncio.create_task(delete_message_after_timeout(kafe_choose, 4000)) 
    except aiogram.exceptions.TelegramBadRequest:
        kafe_choose=await message.answer("Выберите кофейню 👇:", reply_markup=keyboard)
        asyncio.create_task(delete_message_after_timeout(kafe_choose, 4000)) 



@dp.callback_query(F.data.startswith("cafe_"))
async def handle_cafe_selection(callback_query: types.CallbackQuery):
    """Обработка выбора кафе и отображение списка кофе."""
    cafe_id = int(callback_query.data.split("_")[1])

    # Проверить расписание работы кафе
    schedule = await retrieve_cafe_schedule(cafe_id)
    if not schedule:
        await callback_query.answer("У этого кафе нет указанного расписания.", show_alert=True)
        return

    now = datetime.now(astana_tz).time()
    if not (schedule["open_time"] <= now <= schedule["close_time"]):
        await callback_query.answer(f"К сожалению кофейня сейчас закрыта, время работы кофейни: {schedule['open_time']} - {schedule['close_time']} \n\nВы можете заказать в другой кофейне", show_alert=True)
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
    # Создание кнопок для кофе
    buttons = [
        [
            InlineKeyboardButton(
                text=f"🚫{coffee['coffee_name']} - НЕДОСТУПНО" if not coffee["is_available"] else coffee["coffee_name"],
                callback_data=f"coffee_{coffee['menu_id']}_{cafe_id}"
            )
        ]
        for coffee in coffee_options
    ]

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    try:
        cofe=await message.edit_text("Выберите кофе 👇:", reply_markup=keyboard)
        asyncio.create_task(delete_message_after_timeout(cofe, 4000))
    except aiogram.exceptions.TelegramBadRequest:
        cofe=await message.answer("Выберите кофе 👇:", reply_markup=keyboard)
        asyncio.create_task(delete_message_after_timeout(cofe, 4000))



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
                [InlineKeyboardButton(text="В кофейне", callback_data="at_cafe")],
                [InlineKeyboardButton(text="На вынос", callback_data="take_out")]
            ]
        )
        chosed=await callback_query.message.edit_text(
            f"Вы выбрали {selected_coffee['coffee_name']}✅\nКофе на вынос или в заведении?\n",
            reply_markup=keyboard
        )
        await callback_query.answer()
        asyncio.create_task(delete_message_after_timeout(chosed, 4000))
    except Exception as e:
        logger.error(f"Ошибка обработки выбора кофе: {e}")
        await callback_query.answer("Произошла ошибка. Попробуйте снова.", show_alert=True)


@dp.callback_query(F.data == "at_cafe")
async def handle_at_cafe(callback_query: types.CallbackQuery):
    """Обработка выбора 'В кафе'."""
    telegram_id = callback_query.from_user.id
    order_data = user_data.get(telegram_id, {})

    if not order_data:
        await callback_query.answer("Произошла ошибка. Попробуйте снова.", show_alert=True)
        return

    # Сохранить выбор "В кафе" в user_data
    order_data["take_out"] = "В кофейне"
    user_data[telegram_id] = order_data

    # Предложить добавить комментарии
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Добавить комментарий", callback_data="add_comment_yes")],
            [InlineKeyboardButton(text="Без комментариев", callback_data="add_comment_no")]
        ]
    )

    await callback_query.message.edit_text(
        "Вы выбрали: В кофейне 🏠\n"
        "Хотите добавить комментарии к заказу? (например, добавить сироп, сахар и т.д.)",
        reply_markup=keyboard
    )
    await callback_query.answer("Вы выбрали 'В кафе'.")

@dp.callback_query(F.data == "take_out")
async def handle_take_out(callback_query: types.CallbackQuery):
    """Обработка выбора 'На вынос'."""
    telegram_id = callback_query.from_user.id
    order_data = user_data.get(telegram_id, {})

    if not order_data:
        await callback_query.answer("Произошла ошибка. Попробуйте снова.", show_alert=True)
        return

    # Сохранить выбор "На вынос" в user_data
    order_data["take_out"] = "На вынос"
    user_data[telegram_id] = order_data

    # Предложить добавить комментарии
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Добавить комментарий", callback_data="add_comment_yes")],
            [InlineKeyboardButton(text="Без комментариев", callback_data="add_comment_no")]
        ]
    )

    await callback_query.message.edit_text(
        "Вы выбрали: На вынос 🚶‍♂️\n"
        "Хотите добавить комментарии к заказу? (например, добавить сироп, сахар и т.д.)",
        reply_markup=keyboard
    )
    await callback_query.answer("Вы выбрали 'На вынос'.")



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
    order_result = await create_order(telegram_id, order_data["cafe_id"], order_data["menu_id"],order_data["take_out"])
    if "error" in order_result:
        # Notify the user about the specific issue
        await callback_query.answer(order_result["error"], show_alert=True)
        return

    if "success" in order_result:
        order_id = order_result["success"][0]["order_id"]
        
        # Создаем клавиатуру с кнопкой "Отменить"
        cancel_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"cancel_order_{order_id}")]
            ]
        )
        asyncio.create_task(monitor_order_status(telegram_id))
        order_details= await get_order_by_id(order_id)

        await callback_query.message.edit_text(
            f"Ваш заказ #{order_id} успешно создан 🥳\n\n"
            f"Напиток: {order_details['coffee_name']}\n"
            f"Контакт кофейни для связи: {order_details['cafe_tg']}\n"
            f"{order_details['take_out']}\n\n"
            f"Ждем подтверждения от кофейни ⏰\n"
            f"Если хотите отменить заказ, подождите 3 секунды"
        )
        await asyncio.sleep(3)
        cancel_message=await callback_query.message.edit_text(
            f"Ваш заказ #{order_id} успешно создан 🥳\n\n"
            f"Напиток: {order_details['coffee_name']}\n"
            f"Контакт кофейни для связи: {order_details['cafe_tg']}\n"
            f"{order_details['take_out']}\n\n"
            f"Ждем подтверждения от кофейни ⏰\n"
            f"Если хотите отменить, нажмите на кнопку снизу 🚫\n",
            reply_markup=cancel_keyboard
        )
        asyncio.create_task(delete_message_after_timeout(cancel_message, 4000))

    else:
        await callback_query.answer("Произошла ошибка при создании заказа.", show_alert=True)
async def delete_message_after_timeout(message, timeout: int):
    print("Start deleting")
    """Удалить сообщение через заданное время."""
    await asyncio.sleep(timeout)
    try:
        await message.delete()
    except Exception as e:
        # Игнорируем ошибки (например, если сообщение уже удалено)
        print(f"Ошибка при удалении сообщения: {e}")
@dp.message(lambda message: user_data.get(message.from_user.id, {}).get("awaiting_comment"))
async def handle_order_comment(message: types.Message):
    """Обработать комментарий пользователя и создать заказ."""
    telegram_id = message.from_user.id
    order_data = user_data.get(telegram_id, {})

    if not order_data:
        await message.answer("Произошла ошибка. Попробуйте снова.")
        return

    # Проверка на незавершенные заказы
    query_unfinished_orders = """
        SELECT COUNT(*) AS unfinished_orders
        FROM orders
        JOIN users ON orders.user_id = users.user_id
        WHERE users.telegram_id = %s
          AND orders.is_finished = FALSE;
    """
    unfinished_result = await db_execute(query_unfinished_orders, params=(str(telegram_id),), fetch=True)
    unfinished_orders = unfinished_result[0]["unfinished_orders"] if unfinished_result else 0

    if unfinished_orders > 0:
        unfinish=await message.answer("У вас есть незавершенный заказ. Завершите его, прежде чем оформлять новый.")
        asyncio.create_task(delete_message_after_timeout(unfinish, 4000))
        # Очистить данные пользователя
        user_data.pop(telegram_id, None)
        return

    # Проверка на лимит заказов в день
    query_daily_orders = """
        SELECT COUNT(*) AS daily_orders
        FROM orders
        JOIN users ON orders.user_id = users.user_id
        WHERE users.telegram_id = %s
          AND DATE(orders.order_date) = CURRENT_DATE
          AND orders.status NOT IN ('canceled');  -- Исключаем отмененные заказы
    """
    result = await db_execute(query_daily_orders, params=(str(telegram_id),), fetch=True)
    daily_orders = result[0]["daily_orders"] if result else 0

    if daily_orders >= 1:
        already=await message.answer("Вы уже сделали заказ сегодня. Подписка позволяет заказывать 1 кофе в день.")
        asyncio.create_task(delete_message_after_timeout(already, 4000))
        # Очистить данные пользователя
        user_data.pop(telegram_id, None)
        return

    # Создать заказ с деталями
    comment = message.text
    order = await create_order_with_details(telegram_id, order_data["cafe_id"], order_data["menu_id"], comment, order_data["take_out"])
    if order:
        order_id = order[0]["order_id"]
        
        # Создаём клавиатуру с кнопкой "Отменить"
        cancel_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"cancel_order_{order_id}")]
            ]
        )

        asyncio.create_task(monitor_order_status(telegram_id))
        order_details = await get_order_by_id(order_id)

        # Отправляем первое сообщение без кнопки
        order_confirmed_message=await message.answer(
            f"Ваш заказ #{order_id} успешно создан 🥳\n\n"
            f"Напиток: {order_details['coffee_name']}\n"
            f"Контакты кафе: {order_details['cafe_tg']}\n"
            f"{order_details['take_out']}\n\n"
            f"Ждем подтверждения от кофейни ⏰\n"
            f"Если хотите отменить заказ, подождите 3 секунды"
        )
        asyncio.create_task(delete_message_after_timeout(order_confirmed_message, 4000)) 

        # Ждём 3 секунды
        await asyncio.sleep(3)

        # Отправляем сообщение с кнопкой "Отменить заказ"
        cancel_message=await message.answer(
            f"Если хотите отменить, нажмите на кнопку снизу 🚫\n",
            reply_markup=cancel_keyboard
        )
        asyncio.create_task(delete_message_after_timeout(cancel_message, 4000)) 



async def create_order_with_details(telegram_id, cafe_id, menu_id, details,take_out):
    """Создание заказа с комментариями."""
    query_get_user_id = "SELECT user_id FROM users WHERE telegram_id = %s;"
    user = await db_execute(query_get_user_id, params=(str(telegram_id),), fetch=True)

    if not user:
        logger.error(f"User with telegram_id={telegram_id} not found in the database.")
        return None

    user_id = user[0]["user_id"]

    query_create_order = """
        INSERT INTO orders (user_id, cafe_id, menu_id, order_date, status, details,take_out)
        VALUES (%s, %s, %s, %s, %s, %s,%s)
        RETURNING order_id;
    """
    
    return await db_execute(query_create_order, params=(user_id, cafe_id, menu_id, datetime.now(astana_tz), "pending", details,take_out), fetch=True)


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
        elif current_status == "выдан":
            await callback_query.answer("Этот заказ уже был выдан вам и не может быть отменен.", show_alert=True)
            return

        # Если статус позволяет, отменяем заказ
        update_query = """
        UPDATE orders 
        SET status = 'canceled'
        WHERE order_id = %s;
        """

        await db_execute(update_query, params=(order_id,))

        # Уведомление пользователя об успешной отмене
        await callback_query.message.edit_text(
            f"🛑Ваш заказ #{order_id} был отменён🛑\n"
"Мы надеемся, вы сделаете новый заказ позже 🥺"
        )

        # Отправляем новое сообщение и удаляем старое
        await callback_query.message.delete()
        await callback_query.answer("Ваш заказ отменён.")
    except (IndexError, ValueError) as e:
        logger.error(f"Ошибка отмены заказа: {e}")
        await callback_query.answer("Произошла ошибка при отмене заказа. Попробуйте позже.", show_alert=True)




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

        while last_status not in ["готово", "выдан"]:
            await asyncio.sleep(5)
            updated_order = await db_execute(query, params=(str(telegram_id),), fetch=True)

            if not updated_order or updated_order[0]["status"] == last_status:
                continue

            last_status = updated_order[0]["status"]
            if last_status == "готовится":
                await bot.send_message(
                    chat_id=telegram_id,
                    text=f"🟡Ваш заказ #{order_id} обновлен🟡\nСтатус: готовится⏳"
                )
            elif last_status == "готово":
                otp_code = updated_order[0]["otp_code"]
                await bot.send_message(
                    chat_id=telegram_id,
                    text=f"✅Ваш заказ #{order_id} готов✅\nПодойдите к кассиру с телефоном для получения заказа ☕️"
                )
            elif last_status == "выдан":
                await bot.send_message(
                    chat_id=telegram_id,
                    text=f"✅ Ваш заказ #{order_id} выдан \n✅Спасибо что пользуетесь нашим сервисом 🫶\nЖдем вас завтра за новой чашечкой кофе 🤗"
                )
                break
            elif last_status == "canceled":
                await bot.send_message(
                    chat_id=telegram_id,
                    text=f"🛑 Ваш заказ #{order_id} был отменён. Мы надеемся, вы сделаете новый заказ позже 🥺"
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
                    text=f"✅Ваш заказ #{order_id} готов✅\n⭕️ Ваш OTP-код: {otp_code} ⭕️"
                )
                # Обновляем флаг уведомления в базе данных
                update_query = "UPDATE orders SET otp_notified = TRUE WHERE order_id = %s;"
                await db_execute(update_query, params=(order_id,))
            except Exception as e:
                logger.error(f"Ошибка при отправке OTP-кода пользователю {telegram_id}: {e}")

        # Ждём 1 секунду перед следующей проверкой
        await asyncio.sleep(1)

async def monitor_otp_updates():
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
                    text=f"✅Ваш заказ #{order_id} готов✅\n⭕️ Ваш OTP-код: {otp_code} ⭕️"
                )
                # Обновляем флаг уведомления в базе данных
                update_query = "UPDATE orders SET otp_notified = TRUE WHERE order_id = %s;"
                await db_execute(update_query, params=(order_id,))
            except Exception as e:
                logger.error(f"Ошибка при отправке OTP-кода пользователю {telegram_id}: {e}")

        # Ждём 1 секунду перед следующей проверкой
        await asyncio.sleep(1)

async def monitor_otp_updates():
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
                    text=f"✅Ваш заказ #{order_id} готов✅\n⭕️ Ваш OTP-код: {otp_code} ⭕️"
                )
                # Обновляем флаг уведомления в базе данных
                update_query = "UPDATE orders SET otp_notified = TRUE WHERE order_id = %s;"
                await db_execute(update_query, params=(order_id,))
            except Exception as e:
                logger.error(f"Ошибка при отправке OTP-кода пользователю {telegram_id}: {e}")

        # Ждём 1 секунду перед следующей проверкой
        await asyncio.sleep(1)

async def monitor_subscription_updates():
    while True:
        # Проверяем активные подписки
        query = """
            SELECT user_id, telegram_id, subscription_end_date, subscription_notified
            FROM users
            WHERE subscription_status = TRUE;
        """
        active_subscriptions = await db_execute(query, fetch=True)

        for user in active_subscriptions:
            user_id = user["user_id"]
            telegram_id = user["telegram_id"]
            subscription_end_date = user["subscription_end_date"]
            subscription_notified = user["subscription_notified"]

            # Если уведомление еще не отправлено
            if not subscription_notified:
                try:
                    sub_on=await bot.send_message(
                        chat_id=telegram_id,
                        text="Поздравляем, вы приобрели подписку Refill 🎉\n\nДобро пожаловать в сервис, где заботятся о тех кто любит кофе 🤗\n\nПриятного использования 🫶"
                    )
                    # Обновляем флаг уведомления в базе данных
                    update_query = "UPDATE users SET subscription_notified = TRUE WHERE user_id = %s;"
                    await db_execute(update_query, params=(user_id,))
                    asyncio.create_task(delete_message_after_timeout(sub_on, 4000))

                except Exception as e:
                    logger.error(f"Ошибка при отправке сообщения пользователю {telegram_id}: {e}")

            # Проверяем окончание подписки
            if subscription_end_date and datetime.now().date() >= subscription_end_date:
                try:
                    # Ставим подписку на FALSE и сбрасываем уведомление
                    update_query = """
                        UPDATE users
                        SET subscription_status = FALSE, subscription_notified = FALSE
                        WHERE user_id = %s;
                    """
                    await db_execute(update_query, params=(user_id,))

                    # Уведомляем пользователя об окончании подписки
                    passed=await bot.send_message(
                        chat_id=telegram_id,
                        text="Ваша подписка истекла. Подпишитесь снова, чтобы продолжить пользоваться услугами. 😊"
                    )
                    asyncio.create_task(delete_message_after_timeout(passed, 4000)) 
                except Exception as e:
                    logger.error(f"Ошибка при обновлении подписки пользователя {telegram_id}: {e}")

        # Ждём 1 секунду перед следующей проверкой
        await asyncio.sleep(1)
async def main():
    global db_connection
    db_connection = psycopg2.connect(DB_URL)

    # Запуск мониторинга OTP-кодов
    asyncio.create_task(monitor_otp_updates())
    asyncio.create_task(monitor_subscription_updates())
    logger.info("Бот запущен и готов к работе")
    await dp.start_polling(bot)

    db_connection.close()



if __name__ == "__main__":
    asyncio.run(main())