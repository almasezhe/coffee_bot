import os
import logging
import asyncio
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from psycopg2.extras import RealDictCursor
import psycopg2

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# API Key and Database URL
API_KEY = "7696239640:AAHgrwzHYacGqaYBoXzHKXu17Y7qk07MWI8"
DB_URL = "postgresql://postgres.jmujxtsvrbhlvthkkbiq:dbanMcmX9oxJyQlE@aws-0-eu-central-1.pooler.supabase.com:6543/postgres"
bot = Bot(token=API_KEY)
dp = Dispatcher()
astana_tz = timezone(timedelta(hours=5))


### Database Helpers ###

async def db_execute(query, params=None, fetch=False):
    """Выполняет запрос к базе данных с обработкой ошибок."""
    global db_connection
    if db_connection.closed:
        db_connection = psycopg2.connect(DB_URL)

    try:
        
        logger.info(f"Executing query: {query}, params: {params}")
        with db_connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, params)
            if fetch:
                result = cursor.fetchall()
                logger.info(f"Query result: {result}")
                return result
            db_connection.commit()  # Коммит транзакции, если нет fetch
    except Exception as e:
        logger.error(f"Database error: {e} | Query: {query} | Params: {params}")
        db_connection.rollback()  # Откатываем транзакцию, если была ошибка
        return None


async def get_user_role_and_cafe(telegram_id):
    query = "SELECT role, cafe_id FROM admins WHERE telegram_id = %s;"
    result = await db_execute(query, params=(str(telegram_id),), fetch=True)  # Преобразуем telegram_id в строку
    return result[0] if result else None


### FSM States ###

class AdminStates(StatesGroup):
    adding_cafe = State()
    removing_cafe = State()
    managing_users = State()
    adding_cafe_chat_id= State()
    adding_cafe_schedule = State()


async def can_manage_cafes(telegram_id):
    user = await get_user_role_and_cafe(telegram_id)
    print(user['role'] == 'owner')
    return user and user['role'] == 'owner'


    # Proceed with cafe management logic
@dp.message(StateFilter(AdminStates.adding_cafe_chat_id))
async def handle_add_cafe_chat_id(message: types.Message, state: FSMContext):
    chat_id = message.text.strip()
    await state.update_data(chat_id=chat_id)
    await message.answer("Введите график работы нового кафе (например: 09:00-18:00):")
    await state.set_state(AdminStates.adding_cafe_schedule)


@dp.message(StateFilter(AdminStates.adding_cafe_schedule))
async def handle_add_cafe_schedule(message: types.Message, state: FSMContext):
    schedule = message.text.strip()
    data = await state.get_data()
    cafe_name = data.get("cafe_name")
    chat_id = data.get("chat_id")

    query = "INSERT INTO cafes (name, chat_id, working_hours, is_active) VALUES (%s, %s, %s, TRUE);"
    await db_execute(query, params=(cafe_name, chat_id, schedule))

    await message.answer(f"Кафе '{cafe_name}' успешно добавлено с графиком работы: {schedule}!")
    await state.clear()
@dp.message(Command("start"))
async def start(message: types.Message):
    user = await get_user_role_and_cafe(message.from_user.id)
    print(f"user :{user} \n {message.from_user.id}")
    if not user:
        await message.answer("У вас нет прав доступа.")
        return

    # Base menu
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            #[types.KeyboardButton(text="Управление кафе")],
            [types.KeyboardButton(text="Управление пользователями")],
            #[types.KeyboardButton(text="Просмотр статистики")],
            #[types.KeyboardButton(text="Посмотреть Админов")],
        ],
        resize_keyboard=True,
    )

    await message.answer("Добро пожаловать в панель администратора. Выберите действие:", reply_markup=keyboard)

@dp.message(F.text == "Посмотреть Админов")
async def view_admins_menu(message: types.Message):
    user = await get_user_role_and_cafe(message.from_user.username)

    cafes = await retrieve_cafes()
    if not cafes:
        await message.answer("Нет активных кафе для управления администраторами.")
        return

    buttons = [
        [InlineKeyboardButton(text=cafe["name"], callback_data=f"view_admins_{cafe['cafe_id']}")]
        for cafe in cafes
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("Выберите кафе для просмотра и управления администраторами:", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("view_admins_"))
async def view_admins(callback_query: types.CallbackQuery):
    cafe_id = int(callback_query.data.split("_")[2])

    query = "SELECT admin_id, telegram_id, telegram_username, role FROM admins WHERE cafe_id = %s;"
    admins = await db_execute(query, params=(cafe_id,), fetch=True)

    buttons = []
    if admins:
        buttons = [
            [
                InlineKeyboardButton(
                    text=f"Удалить {admin['telegram_username']} ({admin['role']})",
                    callback_data=f"delete_admin_{admin['telegram_id']}_{cafe_id}_{admin['telegram_username']}"
                )
            ]
            for admin in admins
        ]
    buttons.append([InlineKeyboardButton(text="Добавить Админа", callback_data=f"add_admin_{cafe_id}")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback_query.message.edit_text(
        f"Администраторы кафе (ID: {cafe_id}):",
        reply_markup=keyboard
    )

@dp.callback_query(F.data.startswith("add_admin_"))
async def add_admin_start(callback_query: types.CallbackQuery, state: FSMContext):
    cafe_id = int(callback_query.data.split("_")[2])
    await state.update_data(selected_cafe=cafe_id)

    await callback_query.message.edit_text("Введите Telegram ник администратора:")
    await state.set_state(AdminStates.managing_users)

@dp.message(StateFilter(AdminStates.managing_users))
async def finalize_add_admin(message: types.Message, state: FSMContext):
    data = await state.get_data()
    cafe_id = data["selected_cafe"]
    new_admin_username = message.text.strip()

    # Шаг 1: Получение telegram_id пользователя
    get_telegram_id_query = """
    SELECT telegram_id 
    FROM users 
    WHERE username = %s;
    """
    result = await db_execute(get_telegram_id_query, params=(new_admin_username,), fetch=True)

    if not result:
        await message.answer(f"Ошибка: пользователь @{new_admin_username} не найден в системе.")
        await state.clear()
        return
    print(result)
    telegram_id = result[0]["telegram_id"]
    print(telegram_id)
    print(new_admin_username)
    print(cafe_id)
    # Шаг 2: Добавление администратора
    add_admin_query = """
    INSERT INTO admins (telegram_username, role, cafe_id, telegram_id)
    VALUES (%s, 'admin', %s, %s)
    RETURNING admin_id;
    """
    result = await db_execute(add_admin_query, params=(new_admin_username, cafe_id, telegram_id), fetch=True)

    if result:
        await message.answer(f"Пользователь @{new_admin_username} успешно назначен администратором.")
    else:
        await message.answer(f"Ошибка: пользователь @{new_admin_username} уже является администратором или данные указаны неверно.")

    # Очистка состояния
    await state.clear()



@dp.callback_query(F.data.startswith("delete_admin_"))
async def delete_admin(callback_query: types.CallbackQuery):
    try:
        # Разбираем данные из callback
        data = callback_query.data.split("_")
        admin_id = data[2]
        cafe_id = int(data[3])
        admin_username= data[4]
        print(data)
        # Проверяем, существует ли такой администратор
        check_query = """
        SELECT admin_id 
        FROM admins 
        WHERE telegram_id = %s AND cafe_id = %s;
        """
        result = await db_execute(check_query, params=(admin_id, cafe_id), fetch=True)

        if not result:
            await callback_query.answer(f"Администратор @{admin_username} не найден для кафе с ID {cafe_id}.")
            return
        
        # Если администратор найден, удаляем
        delete_query = """
        DELETE FROM admins 
        WHERE telegram_id = %s AND cafe_id = %s;
        """
        await db_execute(delete_query, params=(admin_id, cafe_id))

        await callback_query.answer(f"Администратор @{admin_username} успешно удалён.")
        await view_admins(callback_query)

    except Exception as e:
        # В случае ошибки
        await callback_query.answer(f"Ошибка при удалении администратора: {str(e)}")


### Cafe Management ###

async def retrieve_cafes():
    query = "SELECT * FROM cafes WHERE is_active = TRUE;"
    return await db_execute(query, fetch=True)



@dp.message(F.text == "Управление кафе")
async def cafe_management(message: types.Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Добавить кафе", callback_data="add_cafe")],
            #[InlineKeyboardButton(text="Удалить кафе", callback_data="remove_cafe")],
            [InlineKeyboardButton(text="Просмотреть список кафе", callback_data="view_cafes")],
                    
        ]
    )
    await message.answer("Выберите действие для управления кафе:", reply_markup=keyboard)


@dp.callback_query(F.data == "add_cafe")
async def add_cafe_handler(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.message.answer("Введите название нового кафе:")
    await state.set_state(AdminStates.adding_cafe)


@dp.message(StateFilter(AdminStates.adding_cafe))
async def handle_add_cafe_name(message: types.Message, state: FSMContext):
    cafe_name = message.text.strip()
    if not cafe_name:
        await message.answer("Название кафе не может быть пустым. Попробуйте снова.")
        return

    # Сохраняем название кафе во временное состояние
    await state.update_data(cafe_name=cafe_name)
    await message.answer("Введите chat_id для нового кафе:")
    await state.set_state(AdminStates.adding_cafe_chat_id)


@dp.message(StateFilter(AdminStates.adding_cafe_chat_id))
async def handle_add_cafe_chat_id(message: types.Message, state: FSMContext):
    chat_id = message.text

    # Получаем сохраненное название кафе
    data = await state.get_data()
    cafe_name = data.get("cafe_name")

    # Вставляем данные в базу
    query = "INSERT INTO cafes (name, chat_id, is_active) VALUES (%s, %s, TRUE);"
    await db_execute(query, params=(cafe_name, chat_id))

    await message.answer(f"Кафе '{cafe_name}' с chat_id '{chat_id}' успешно добавлено!")
    await state.clear()



@dp.callback_query(F.data == "remove_cafe")
async def remove_cafe_handler(callback_query: types.CallbackQuery):
    cafes = await retrieve_cafes()
    if not cafes:
        await callback_query.message.edit_text("Нет доступных кафе для удаления.")
        return

    buttons = [
        [InlineKeyboardButton(text=cafe["name"], callback_data=f"delete_cafe_{cafe['cafe_id']}")]
        for cafe in cafes
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback_query.message.edit_text(
        "Выберите кафе для удаления:",
        reply_markup=keyboard,
    )
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

async def show_cafes_page(message, cafes, page=0):
    items_per_page = 4
    total_pages = (len(cafes) + items_per_page - 1) // items_per_page
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    cafes_page = cafes[start_idx:end_idx]

    buttons = []
    for cafe in cafes_page:
        # Получаем расписание для текущего дня
        schedule = await retrieve_cafe_schedule(cafe["cafe_id"])
        if schedule:
            # Форматируем время работы
            open_time = schedule["open_time"].strftime("%H:%M")
            close_time = schedule["close_time"].strftime("%H:%M")
            working_hours_text = f"{open_time} - {close_time}"
        else:
            working_hours_text = "Расписание не указано"

        # Кнопки для каждого кафе
        buttons.append([
            InlineKeyboardButton(text=cafe["name"], callback_data=f"details_{cafe['cafe_id']}"),
            InlineKeyboardButton(text=f"{working_hours_text}", callback_data="noop"),
        ])

    # Навигационные кнопки
    navigation_buttons = []
    if page > 0:
        navigation_buttons.append(InlineKeyboardButton("<--", callback_data=f"cafes_page_{page - 1}"))
    if page < total_pages - 1:
        navigation_buttons.append(InlineKeyboardButton("-->", callback_data=f"cafes_page_{page + 1}"))

    if navigation_buttons:
        buttons.append(navigation_buttons)

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.edit_text("<b>Список кафе:</b>", reply_markup=keyboard, parse_mode="HTML")



@dp.callback_query(F.data.startswith("cafes_page_"))
async def navigate_cafe_pages(callback_query: types.CallbackQuery):
    """Navigate between cafe pages."""
    page = int(callback_query.data.split("_")[2])
    cafes = await retrieve_cafes()
    if not cafes:
        await callback_query.message.edit_text("Нет активных кафе.")
        return

    await show_cafes_page(callback_query.message, cafes, page)
    await callback_query.answer()


@dp.callback_query(F.data.startswith("delete_cafe_"))
async def delete_cafe(callback_query: types.CallbackQuery):
    """Mark a cafe as inactive."""
    cafe_id = int(callback_query.data.split("_")[2])
    query = "UPDATE cafes SET is_active = FALSE WHERE cafe_id = %s;"
    await db_execute(query, params=(cafe_id,))
    await callback_query.answer("Кафе успешно удалено.")

    # Refresh the cafe list
    cafes = await retrieve_cafes()
    if not cafes:
        await callback_query.message.edit_text("Нет активных кафе.")
    else:
        await show_cafes_page(callback_query.message, cafes, page=0)


@dp.callback_query(F.data == "view_cafes")
async def view_cafes(callback_query: types.CallbackQuery):
    """Display a paginated list of cafes."""
    cafes = await retrieve_cafes()
    if not cafes:
        await callback_query.message.edit_text("Нет активных кафе.")
        return

    await show_cafes_page(callback_query.message, cafes, page=0)



### User Management ###
async def retrieve_users():
    query = "SELECT user_id, phone_number, username, subscription_status FROM users;"
    return await db_execute(query, fetch=True)


@dp.message(F.text == "Управление пользователями")
async def user_management(message: types.Message):
    """Send or edit the user list with subscription statuses."""
    users = await retrieve_users()
    if not users:
        await message.answer("Нет пользователей для отображения.")
        return

    buttons = [
        [
            InlineKeyboardButton(
                text=(
                    f"{user['username']} - "
                    f"{user['phone_number'] + ' - ' if user['phone_number'] else ''}"
                    f"{'АКТИВНО' if user['subscription_status'] else 'НЕАКТИВНО'}"
                ),
                callback_data=f"toggle_user_{user['user_id']}"
            )
        ]
        for user in users
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    # Send the message and store it for editing
    await message.answer("Список пользователей и их статус подписки:", reply_markup=keyboard)



async def toggle_user_subscription(user_id: int):
    """Toggle the subscription status of a user and update subscription dates."""
    # Получить текущий статус пользователя
    query_select = "SELECT subscription_status FROM users WHERE user_id = %s;"
    user = await db_execute(query_select, params=(user_id,), fetch=True)
    
    if not user:
        return None

    current_status = user[0]['subscription_status']
    new_status = not current_status  # Переключение статуса

    if new_status:  # Если делаем активным
        subscription_start_date = datetime.now().date()
        subscription_end_date = subscription_start_date + timedelta(days=30)
    else:  # Если отключаем подписку
        subscription_start_date = None
        subscription_end_date = None

    # Обновление статуса и дат в базе данных
    query_update = """
        UPDATE users
        SET subscription_status = %s,
            subscription_start_date = %s,
            subscription_end_date = %s
        WHERE user_id = %s;
    """
    await db_execute(query_update, params=(
        new_status,
        subscription_start_date,
        subscription_end_date,
        user_id,
    ))
    return new_status

@dp.callback_query(F.data.startswith("toggle_user_"))
async def handle_toggle_subscription(callback_query: types.CallbackQuery):
    """Handle user subscription toggling."""
    user_id = int(callback_query.data.split("_")[-1])

    # Переключить статус
    new_status = await toggle_user_subscription(user_id)
    if new_status is None:
        await callback_query.answer("Ошибка: Пользователь не найден.", show_alert=True)
        return

    # Обновить сообщение с клавиатурой
    users = await retrieve_users()
    if not users:
        await callback_query.message.edit_text("Нет пользователей для отображения.")
        return

    buttons = [
        [
            InlineKeyboardButton(
                text=(
                    f"{user['username']} - "
                    f"{user['phone_number'] + ' - ' if user['phone_number'] else ''}"
                    f"{'АКТИВНО' if user['subscription_status'] else 'НЕАКТИВНО'}"
                ),
                callback_data=f"toggle_user_{user['user_id']}"
            )
        ]
        for user in users
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback_query.message.edit_text(
        "Список пользователей и их статус подписки:", reply_markup=keyboard
    )
    await callback_query.answer("Статус подписки обновлен.")



### Statistics ###

@dp.message(F.text == "Просмотр статистики")
async def view_statistics(message: types.Message):
    cafes = await db_execute("SELECT COUNT(*) FROM cafes WHERE is_active = TRUE;", fetch=True)
    users = await db_execute("SELECT COUNT(*) FROM users;", fetch=True)
    active_subscriptions = await db_execute("SELECT COUNT(*) FROM users WHERE subscription_status = TRUE;", fetch=True)

    cafes_count = cafes[0]["count"] if cafes else 0
    users_count = users[0]["count"] if users else 0
    active_sub_count = active_subscriptions[0]["count"] if active_subscriptions else 0

    await message.answer(
        f"<b>Статистика:</b>\n"
        f"Активные кафе: {cafes_count}\n"
        f"Зарегистрированные пользователи: {users_count}\n"
        f"Активные подписки: {active_sub_count}",
        parse_mode="HTML",InlineKeyboardButton=None
    )


### Main ###

async def main():
    global db_connection
    db_connection = psycopg2.connect(DB_URL)
    logger.info("Бот запущен и готов к работе")
    await dp.start_polling(bot)
    db_connection.close()


if __name__ == "__main__":
    asyncio.run(main())
