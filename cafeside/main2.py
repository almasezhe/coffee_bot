import os,uuid
import logging
import asyncio
from datetime import datetime
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters.command import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton

import psycopg2
from psycopg2.extras import RealDictCursor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load your API key and database URL
API_KEY="7537071518:AAE2fDi3HoOT4p8RNmptqzwwEOgXUDdhoZw"
DB_URL="postgresql://postgres.jmujxtsvrbhlvthkkbiq:dbanMcmX9oxJyQlE@aws-0-eu-central-1.pooler.supabase.com:6543/postgres"

# Define FSM states
bot = Bot(token=API_KEY)
dp = Dispatcher()

db_connection = None
cafe_id = 6  # Replace this with the correct cafe_id


async def db_execute(query, params=None, fetch=False):
    """Helper function to execute a query on the database."""
    try:
        with db_connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, params)
            db_connection.commit()
            if fetch:
                return cursor.fetchall()
    except Exception as e:
        logger.error(f"Database error: {e}")
        return None


### MENU MANAGEMENT ###

async def get_menu():
    """Retrieve the menu for the current cafe."""
    query = "SELECT * FROM menu WHERE cafe_id = %s;"
    return await db_execute(query, params=(cafe_id,), fetch=True)


async def update_menu_availability(menu_id, is_available):
    """Update the availability of a menu item."""
    query = "UPDATE menu SET is_available = %s WHERE menu_id = %s;"
    await db_execute(query, params=(is_available, menu_id))


async def delete_menu_item(menu_id):
    """Delete a menu item."""
    query = "DELETE FROM menu WHERE menu_id = %s;"
    await db_execute(query, params=(menu_id,))


### COMMAND HANDLERS ###

@dp.message(Command("start"))
async def start(message: types.Message):
    global cafe_id
    # Fetch the cafe ID associated with this admin
    admin_id = str(message.from_user.id)

    query = "SELECT cafe_id FROM admins WHERE telegram_id = %s;"
    result = await db_execute(query, params=(admin_id,), fetch=True)

    if not result:
        await message.answer("У вас нет доступа к этому боту. Обратитесь к администратору.")
        return

    cafe_id = result[0]["cafe_id"]
    await message.answer(
        "Добро пожаловать в кафе-бот! Вы можете управлять своим меню.\n"
        "Используйте команду /menu для просмотра и управления меню."
    )

async def render_menu(message_or_callback, page: int = 0):
    """Render the menu for a specific page."""
    menu = await get_menu()

    if not menu:
        if isinstance(message_or_callback, types.Message):
            await message_or_callback.answer("Ваше меню пусто.")
        elif isinstance(message_or_callback, types.CallbackQuery):
            await message_or_callback.message.edit_text("Ваше меню пусто.")
        return

    # Define pagination
    items_per_page = 4
    total_items = len(menu)
    total_pages = (total_items + items_per_page - 1) // items_per_page  # Ceiling division
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    menu_page = menu[start_idx:end_idx]

    # Build the buttons for the current page
    buttons = []
    for item in menu_page:
        availability = "Доступно" if item["is_available"] else "Недоступно"
        buttons.append([
            InlineKeyboardButton(
                text=f"{item['coffee_name']} ({availability})",
                callback_data=f"toggle_{item['menu_id']}_{page}"
            ),
            InlineKeyboardButton(
                text="Удалить",
                callback_data=f"delete_confirm_{item['menu_id']}_{page}"
            )
        ])

    # Add navigation buttons
    navigation_buttons = []
    if page > 0:
        navigation_buttons.append(InlineKeyboardButton(text="<", callback_data=f"prev_page_{page - 1}"))
    if page < total_pages - 1:
        navigation_buttons.append(InlineKeyboardButton(text=">", callback_data=f"next_page_{page + 1}"))
    if navigation_buttons:
        buttons.append(navigation_buttons)

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    # Update the message
    if isinstance(message_or_callback, types.Message):
        await message_or_callback.answer("Here is your menu:", reply_markup=keyboard)
    elif isinstance(message_or_callback, types.CallbackQuery):
        await message_or_callback.message.edit_text("Here is your menu:", reply_markup=keyboard)
@dp.message(Command("menu"))
async def show_menu(message: types.Message, page: int = 0):
    """Display the cafe menu with pagination and interactive buttons."""
    # Delegate menu rendering to the helper function
    await render_menu(message, page)


@dp.callback_query(F.data.startswith("prev_page_"))
async def previous_page(callback_query: types.CallbackQuery):
    """Go to the previous page."""
    page = int(callback_query.data.split("_")[2])
    await render_menu(callback_query, page=page)
    await callback_query.answer()  # Acknowledge the callback


@dp.callback_query(F.data.startswith("next_page_"))
async def next_page(callback_query: types.CallbackQuery):
    """Go to the next page."""
    page = int(callback_query.data.split("_")[2])
    await render_menu(callback_query, page=page)
    await callback_query.answer()  # Acknowledge the callback



@dp.callback_query(F.data.startswith("toggle_"))
async def toggle_availability(callback_query: types.CallbackQuery):
    """Toggle the availability of a menu item."""
    data_parts = callback_query.data.split("_")
    menu_id = int(data_parts[1])
    page = int(data_parts[2])  # Current page number

    # Retrieve the current availability
    query = "SELECT is_available FROM menu WHERE menu_id = %s;"
    result = await db_execute(query, params=(menu_id,), fetch=True)

    if not result:
        await callback_query.answer("Напиток не найден.", show_alert=True)
        return

    current_state = result[0]["is_available"]
    new_state = not current_state  # Toggle availability

    # Update the database
    update_query = "UPDATE menu SET is_available = %s WHERE menu_id = %s;"
    await db_execute(update_query, params=(new_state, menu_id))

    # Refresh the menu
    await render_menu(callback_query, page=page)
    await callback_query.answer("Статус обновлен.")



@dp.callback_query(F.data.startswith("delete_confirm_"))
async def confirm_delete(callback_query: types.CallbackQuery):
    """Ask for confirmation before deleting a menu item."""
    data_parts = callback_query.data.split("_")
    menu_id = int(data_parts[2])
    page = int(data_parts[3])  # Current page number

    # Fetch the coffee name
    query = "SELECT coffee_name FROM menu WHERE menu_id = %s;"
    result = await db_execute(query, params=(menu_id,), fetch=True)

    if not result:
        await callback_query.answer("Напиток не найден.", show_alert=True)
        return

    coffee_name = result[0]["coffee_name"]

    # Ask for confirmation
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data=f"delete_{menu_id}_{page}"),
                InlineKeyboardButton(text="Нет", callback_data=f"cancel_delete_{page}"),
            ]
        ]
    )
    await callback_query.message.edit_text(
        f"Потвердить удаление {coffee_name}?",
        reply_markup=keyboard,
    )


@dp.callback_query(F.data.startswith("delete_"))
async def delete_item(callback_query: types.CallbackQuery):
    """Delete a menu item after confirmation."""
    data_parts = callback_query.data.split("_")
    menu_id = int(data_parts[1])
    page = int(data_parts[2])  # Current page number

    # Delete the item
    delete_query = "DELETE FROM menu WHERE menu_id = %s;"
    await db_execute(delete_query, params=(menu_id,))

    # Refresh the menu
    await render_menu(callback_query, page=page)
    await callback_query.answer("Напиток удален.")


@dp.callback_query(F.data.startswith("cancel_delete_"))
async def cancel_delete(callback_query: types.CallbackQuery):
    """Cancel the deletion process."""
    page = int(callback_query.data.split("_")[2])  # Current page number
    await render_menu(callback_query, page=page)
    await callback_query.answer("Удаление отменено.")



### MAIN ###

async def main():
    global db_connection
    db_connection = psycopg2.connect(DB_URL)

    logger.info("Бот для кафе запущен и готов к работе")
    await dp.start_polling(bot)

    db_connection.close()


if __name__ == "__main__":
    asyncio.run(main())