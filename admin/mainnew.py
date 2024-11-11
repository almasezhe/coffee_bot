import os
import logging
import asyncio
from datetime import datetime

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
API_KEY = "7696239640:AAElC7D2Hi0slgJg6CkJFSgCILUG5hWWXBE"
DB_URL = "postgresql://postgres.jmujxtsvrbhlvthkkbiq:dbanMcmX9oxJyQlE@aws-0-eu-central-1.pooler.supabase.com:6543/postgres"

bot = Bot(token=API_KEY)
dp = Dispatcher()

db_connection = None

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

async def get_user_role_and_cafe(telegram_username):
    query = "SELECT role, cafe_id FROM admins WHERE telegram_username = %s;"
    result = await db_execute(query, params=(telegram_username,), fetch=True)
    return result[0] if result else None

### FSM States ###

class AdminStates(StatesGroup):
    adding_cafe = State()
    removing_cafe = State()
    managing_users = State()
    adding_cafe_chat_id= State()
    adding_cafe_schedule = State()


async def can_manage_cafes(telegram_username):
    user = await get_user_role_and_cafe(telegram_username)
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
    user = await get_user_role_and_cafe(message.from_user.username)
    if not user:
        await message.answer("У вас нет прав доступа.")
        return

    # Base menu
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="Управление кафе")],
            [types.KeyboardButton(text="Управление пользователями")],
            [types.KeyboardButton(text="Просмотр статистики")],
            [types.KeyboardButton(text="Посмотреть Админов")],
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

    query = "SELECT admin_id, telegram_id, role FROM admins WHERE cafe_id = %s;"
    admins = await db_execute(query, params=(cafe_id,), fetch=True)

    buttons = []
    if admins:
        buttons = [
            [
                InlineKeyboardButton(
                    text=f"Удалить {admin['telegram_id']} ({admin['role']})",
                    callback_data=f"delete_admin_{admin['telegram_id']}_{cafe_id}"
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

    await callback_query.message.edit_text("Введите Telegram ID администратора:")
    await state.set_state(AdminStates.managing_users)

@dp.message(StateFilter(AdminStates.managing_users))
async def finalize_add_admin(message: types.Message, state: FSMContext):
    data = await state.get_data()
    cafe_id = data["selected_cafe"]
    new_admin_username = message.text.strip()

    query = "INSERT INTO admins (telegram_id, role, cafe_id) VALUES (%s, 'admin', %s) ON CONFLICT DO NOTHING;"
    await db_execute(query, params=(new_admin_username, cafe_id))

    await message.answer(f"Пользователь @{new_admin_username} успешно назначен администратором.")
    await state.clear()

@dp.callback_query(F.data.startswith("delete_admin_"))
async def delete_admin(callback_query: types.CallbackQuery):
    data = callback_query.data.split("_")
    admin_username = data[2]
    cafe_id = int(data[3])

    query = "DELETE FROM admins WHERE telegram_id = %s;"
    await db_execute(query, params=(admin_username,))

    await callback_query.answer("Администратор успешно удалён.")
    await view_admins(callback_query)

### Cafe Management ###

async def retrieve_cafes():
    query = "SELECT * FROM cafes WHERE is_active = TRUE;"
    return await db_execute(query, fetch=True)



@dp.message(F.text == "Управление кафе")
async def cafe_management(message: types.Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Добавить кафе", callback_data="add_cafe")],
            [InlineKeyboardButton(text="Удалить кафе", callback_data="remove_cafe")],
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

async def show_cafes_page(message, cafes, page=0):
    items_per_page = 4
    total_pages = (len(cafes) + items_per_page - 1) // items_per_page
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    cafes_page = cafes[start_idx:end_idx]

    buttons = [
        [
            InlineKeyboardButton(text=f"{cafe['name']}", callback_data=f"details_{cafe['cafe_id']}"),
            InlineKeyboardButton(text=f"График: {cafe['working_hours'] or 'Не указан'}", callback_data="noop")
        ]
        for cafe in cafes_page
    ]

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
    query = "SELECT user_id, phone_number, subscription_status FROM users;"
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
                text=f"{user['phone_number']} - {'АКТИВНО' if user['subscription_status'] else 'НЕАКТИВНО'}",
                callback_data=f"toggle_user_{user['user_id']}"
            )
        ]
        for user in users
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    # Send the message and store it for editing
    await message.answer("Список пользователей и их статус подписки:", reply_markup=keyboard)


async def edit_user_management_message(message: types.Message):
    """Edit the existing user management message with updated data."""
    users = await retrieve_users()
    if not users:
        await message.edit_text("Нет пользователей для отображения.")
        return

    buttons = [
        [
            InlineKeyboardButton(
                text=f"{user['phone_number']} - {'АКТИВНО' if user['subscription_status'] else 'НЕАКТИВНО'}",
                callback_data=f"toggle_user_{user['user_id']}"
            )
        ]
        for user in users
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await message.edit_text("Список пользователей и их статус подписки:", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("toggle_user_"))
async def toggle_user_subscription(callback_query: types.CallbackQuery):
    """Toggle the subscription status of a user and update the message."""
    user_id = int(callback_query.data.split("_")[2])
    user = next((u for u in await retrieve_users() if u["user_id"] == user_id), None)       

    if not user:
        await callback_query.answer("Пользователь не найден.", show_alert=True)
        return

    # Toggle subscription status
    new_status = not user["subscription_status"]
    query = "UPDATE users SET subscription_status = %s WHERE user_id = %s;"
    await db_execute(query, params=(new_status, user_id))

    # Notify the admin via a callback alert
    await callback_query.answer(
        f"Подписка пользователя {user['phone_number']} изменена на {'АКТИВНО' if new_status else 'НЕАКТИВНО'}.",
        show_alert=True
    )

    # Edit the original message with the updated user list
    await edit_user_management_message(callback_query.message)



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