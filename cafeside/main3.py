import os
import uuid
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
import re
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load your API key and database URL
API_KEY = "7537071518:AAE2fDi3HoOT4p8RNmptqzwwEOgXUDdhoZw"
DB_URL = "postgresql://postgres.jmujxtsvrbhlvthkkbiq:dbanMcmX9oxJyQlE@aws-0-eu-central-1.pooler.supabase.com:6543/postgres"

# FSM States
class MenuState(StatesGroup):
    waiting_for_new_item = State()

bot = Bot(token=API_KEY)
dp = Dispatcher()

db_connection = None
cafe_id = 6  # Replace with the actual cafe ID


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


async def add_menu_item(name, is_available=True):
    """Add a new menu item."""
    query = """
        INSERT INTO menu (cafe_id, coffee_name, is_available)
        VALUES (%s, %s, %s);
    """
    await db_execute(query, params=(cafe_id, name, is_available))


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
        "Используйте команду /menu для просмотра и управления меню.\n"
        "Используйте команду /add для добавления нового напитка."
    )


@dp.message(Command("menu"))
async def show_menu(message: types.Message, page: int = 0):
    """Display the cafe menu with pagination and interactive buttons."""
    await render_menu(message, page)


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
    total_pages = (total_items + items_per_page - 1) // items_per_page
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

    if isinstance(message_or_callback, types.Message):
        await message_or_callback.answer("Here is your menu:", reply_markup=keyboard)
    elif isinstance(message_or_callback, types.CallbackQuery):
        await message_or_callback.message.edit_text("Here is your menu:", reply_markup=keyboard)


@dp.message(Command("add"))
async def add_item(message: types.Message, state: FSMContext):
    """Start the process of adding a menu item."""
    await message.answer("Введите название нового напитка.")
    await state.set_state(MenuState.waiting_for_new_item)


@dp.message(MenuState.waiting_for_new_item)
async def handle_new_menu_item(message: types.Message, state: FSMContext):
    """Handle adding the menu item after receiving the name."""
    coffee_name = message.text.strip()

    if not coffee_name:
        await message.answer("Название не может быть пустым. Попробуйте снова.")
        return

    await add_menu_item(coffee_name)
    await message.answer(f"Напиток '{coffee_name}' добавлен в меню.")
    await state.clear()


@dp.callback_query(F.data.startswith("toggle_"))
async def toggle_availability(callback_query: types.CallbackQuery):
    """Toggle the availability of a menu item."""
    data_parts = callback_query.data.split("_")
    menu_id = int(data_parts[1])
    page = int(data_parts[2])

    query = "SELECT is_available FROM menu WHERE menu_id = %s;"
    result = await db_execute(query, params=(menu_id,), fetch=True)

    if not result:
        await callback_query.answer("Напиток не найден.", show_alert=True)
        return

    new_state = not result[0]["is_available"]
    await update_menu_availability(menu_id, new_state)
    await render_menu(callback_query, page=page)
    await callback_query.answer("Статус обновлен.")


@dp.callback_query(F.data.startswith("delete_confirm_"))
async def confirm_delete(callback_query: types.CallbackQuery):
    """Ask for confirmation before deleting a menu item."""
    data_parts = callback_query.data.split("_")
    menu_id = int(data_parts[2])
    page = int(data_parts[3])

    query = "SELECT coffee_name FROM menu WHERE menu_id = %s;"
    result = await db_execute(query, params=(menu_id,), fetch=True)

    if not result:
        await callback_query.answer("Напиток не найден.", show_alert=True)
        return

    coffee_name = result[0]["coffee_name"]

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
    page = int(data_parts[2])

    await delete_menu_item(menu_id)
    await render_menu(callback_query, page=page)
    await callback_query.answer("Напиток удален.")


@dp.callback_query(F.data.startswith("cancel_delete_"))
async def cancel_delete(callback_query: types.CallbackQuery):
    """Cancel the deletion process."""
    page = int(callback_query.data.split("_")[2])
    await render_menu(callback_query, page=page)
    await callback_query.answer("Удаление отменено.")

@dp.callback_query(lambda c: c.data.startswith("prev_page_"))
async def previous_page(callback_query: types.CallbackQuery):
    """Go to the previous page."""
    page = int(callback_query.data.split("_")[2])
    await render_menu(callback_query, page=page)
    await callback_query.answer()  # Acknowledge the callback


@dp.callback_query(lambda c: c.data.startswith("next_page_"))
async def next_page(callback_query: types.CallbackQuery):
    """Go to the next page."""
    page = int(callback_query.data.split("_")[2])
    await render_menu(callback_query, page=page)
    await callback_query.answer()  # Acknowledge the callback

### MAIN ###
async def get_incoming_orders():
    """Retrieve incoming orders for the current cafe."""
    query = """
        SELECT o.order_id, o.user_id, o.menu_id, o.order_date, o.status, m.coffee_name
        FROM orders o
        JOIN menu m ON o.menu_id = m.menu_id
        WHERE o.cafe_id = %s AND o.status = 'pending';
    """
    return await db_execute(query, params=(cafe_id,), fetch=True)


async def update_order_status(order_id, status):
    """Update the status of an order."""
    query = "UPDATE orders SET status = %s WHERE order_id = %s;"
    await db_execute(query, params=(status, order_id))


@dp.message(Command("orders"))
async def show_orders(message: types.Message):
    """Display incoming orders."""
    orders = await get_incoming_orders()

    if not orders:
        await message.answer("Нет новых заказов.")
        return

    for order in orders:
        # If the order is pending, show only the "Принять" button
        if order["status"] == "pending":
            buttons = [[InlineKeyboardButton(text="Принять", callback_data=f"accept_{order['order_id']}")]]
        # If the order is accepted, show only the "Готово" button
        else:
            # Skip completed or invalid status orders
            continue
        if order["status"] == "готовится":
            buttons = [[InlineKeyboardButton(text="Готово", callback_data=f"done_{order['order_id']}")]]
        else:
            # Skip completed or invalid status orders
            continue

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        await message.answer(
            f"Заказ №{order['order_id']}:\n"
            f"Клиент: {order['user_id']}\n"
            f"Напиток: {order['coffee_name']}\n"  # Use coffee_name here
            f"Статус: {order['status']}",
            reply_markup=keyboard,
        )

async def get_user_by_id(user_id):
    """Retrieve user information by user_id."""
    query = "SELECT * FROM users WHERE user_id = %s;"
    result = await db_execute(query, params=(user_id,), fetch=True)
    return result[0] if result else None


@dp.callback_query(F.data.startswith("accept_"))
async def accept_order(callback_query: types.CallbackQuery):
    """Handle accepting an order."""
    order_id = int(callback_query.data.split("_")[1])
    await update_order_status(order_id, "готовится")

    # Edit the message to show the new status and the "Готово" button
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Готово", callback_data=f"done_{order_id}")]
        ]
    )
    await callback_query.message.edit_text(
        f"Заказ №{order_id} принят. Статус обновлен на 'готовится'.",
        reply_markup=keyboard,
    )
    await callback_query.answer("Заказ принят.")



@dp.callback_query(F.data.startswith("done_"))
async def complete_order(callback_query: types.CallbackQuery):
    """Handle completing an order."""
    try:
        order_id = int(callback_query.data.split("_")[1])

        # Update the order status to "готово"
        await update_order_status(order_id, "готово")

        # Retrieve the order details
        order_details = await get_order_by_id(order_id)
        if not order_details:
            await callback_query.answer("Ошибка: заказ не найден.", show_alert=True)
            return

        # Add a button for generating OTP
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Сгенерировать OTP", callback_data=f"generate_{order_id}")]
            ]
        )

        # Update the admin message with the new status and button
        await callback_query.message.edit_text(
            f"Заказ №{order_id} завершён. Статус обновлен на 'готово'.",
            reply_markup=keyboard,
        )
        await callback_query.answer("Статус заказа обновлён.")
    except Exception as e:
        logger.error(f"Ошибка в обработке 'done_': {e}")
        await callback_query.answer("Произошла ошибка при обновлении заказа.", show_alert=True)



async def get_order_by_id(order_id):
    """Получить информацию о заказе по его order_id."""
    query = """
        SELECT o.order_id, o.user_id, o.menu_id, o.order_date, o.status, m.coffee_name
        FROM orders o
        JOIN menu m ON o.menu_id = m.menu_id
        WHERE o.order_id = %s;
    """
    result = await db_execute(query, params=(order_id,), fetch=True)
    return result[0] if result else None

@dp.callback_query(F.data.startswith("generate_"))
async def generate_otp_code(callback_query: types.CallbackQuery):
    """Generate and send the OTP code for the order."""
    order_id = int(callback_query.data.split("_")[1])

    # Generate a unique 4-digit OTP code
    otp_code = str(uuid.uuid4().int)[:4]
    await update_order_otp(order_id, otp_code)

    # Retrieve user information
    order_details = await get_order_by_id(order_id)
    user_id = order_details["user_id"]


    # Update the message in the admin chat
    await callback_query.message.answer(
        f"Заказ №{order_id} завершён. Сверьте OTP код с клиентом: {otp_code}.",InlineKeyboardButton=None
    )

async def update_order_otp(order_id, otp_code):
    """Обновить OTP-код для заказа."""
    query = "UPDATE orders SET otp_code = %s WHERE order_id = %s;"
    await db_execute(query, params=(otp_code, order_id))



async def get_order_by_id(order_id):
    """Retrieve order details by ID."""
    query = """
        SELECT o.order_id, o.user_id, o.menu_id, o.order_date, o.status, o.otp_code, m.coffee_name
        FROM orders o
        JOIN menu m ON o.menu_id = m.menu_id
        WHERE o.order_id = %s;
    """
    result = await db_execute(query, params=(order_id,), fetch=True)
    return result[0] if result else None

async def auto_push_new_orders():
    """Continuously check for new orders and push them to the cafe admin."""
    already_sent_orders = set()

    # Fetch the admin's Telegram ID for the current cafe
    admin_telegram_id = await get_admin_telegram_id(cafe_id)
    if not admin_telegram_id:
        logger.error("Не найден admin_telegram_id для cafe_id %s", cafe_id)
        return

    while True:
        # Fetch pending orders
        orders = await get_incoming_orders()
        for order in orders:
            if order["order_id"] not in already_sent_orders:
                # Mark the order as sent
                already_sent_orders.add(order["order_id"])

                # Send the order to the admin
                buttons = [
                    [InlineKeyboardButton(text="Принять", callback_data=f"accept_{order['order_id']}")]
                ]
                keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
                await bot.send_message(
                    chat_id=admin_telegram_id,  # Use the admin's Telegram ID
                    text=(
                        f"Новый заказ #{order['order_id']}:\n"
                        f"Клиент: {order['user_id']}\n"
                        f"Напиток: {order['coffee_name']}\n"
                        f"Статус: {order['status']}"
                    ),
                    reply_markup=keyboard,
                )

        # Wait for a few seconds before checking again
        await asyncio.sleep(5)


async def get_admin_telegram_id(cafe_id):
    """Retrieve the telegram_id of the admin for the given cafe."""
    query = "SELECT telegram_id FROM admins WHERE cafe_id = %s LIMIT 1;"
    result = await db_execute(query, params=(cafe_id,), fetch=True)
    return result[0]["telegram_id"] if result else None

@dp.message()
async def default_message_handler(message: types.Message):
    """Handle other messages that don't match any commands or states."""
    await message.answer(
        "Неизвестная команда или сообщение. Используйте /menu, /add, /delete или /orders."
    )


### MAIN ###

async def main():
    global db_connection
    db_connection = psycopg2.connect(DB_URL)

    # Start the bot and the auto-push task
    asyncio.create_task(auto_push_new_orders())
    logger.info("Бот для кафе запущен и готов к работе")
    await dp.start_polling(bot)

    db_connection.close()



if __name__ == "__main__":
    asyncio.run(main())