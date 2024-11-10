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
class MenuState(StatesGroup):   
    waiting_for_new_item = State()

bot = Bot(token=API_KEY)
dp = Dispatcher()

db_connection = None
cafe_id = 6  # The cafe ID for this bot instance


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
    # Assume the cafe_id is determined by the admin's Telegram ID
    admin_id = str(message.from_user.id)  # Convert to string to match database type

    # Query to find cafe associated with the admin
    query = "SELECT cafe_id FROM admins WHERE telegram_id = %s;"
    result = await db_execute(query, params=(admin_id,), fetch=True)

    if not result:
        await message.answer("У вас нет доступа к этому боту. Обратитесь к администратору.")
        return

    cafe_id = result[0]["cafe_id"]
    await message.answer(
        "Добро пожаловать в кафе-бот! Вы можете управлять своим меню и заказы.\n"
        "Используйте команды:\n"
        "/menu - посмотреть меню\n"
        "/add - добавить позицию в меню\n"
        "/orders - посмотреть входящие заказы"
    )


@dp.message(Command("menu"))
async def show_menu(message: types.Message):
    """Display the cafe menu."""
    menu = await get_menu()

    if not menu:
        await message.answer("Ваше меню пусто.")
        return

    reply_message = "Ваше меню:\n"
    for item in menu:
        status = "Доступно" if item["is_available"] else "Недоступно"
        reply_message += f"{item['menu_id']}. {item['coffee_name']} ({status})\n"

    await message.answer(reply_message)


@dp.message(Command("add"))
async def add_item(message: types.Message, state: FSMContext):
    """Start the process of adding a menu item."""
    await message.answer("Введите название нового напитка.")
    # Set the state to indicate that we are waiting for a new menu item name
    await state.set_state(MenuState.waiting_for_new_item)


@dp.message(MenuState.waiting_for_new_item)
async def handle_new_menu_item(message: types.Message, state: FSMContext):
    """Handle adding the menu item after receiving the name."""
    coffee_name = message.text.strip()

    if not coffee_name:
        await message.answer("Название не может быть пустым. Попробуйте снова.")
        return

    # Add the menu item to the database
    await add_menu_item(coffee_name)

    # Notify the user and clear the state
    await message.answer(f"Напиток '{coffee_name}' добавлен в меню.")
    await state.clear()  # Clear the state to stop further input handling

    # Stop further processing after one item
    await message.answer("Добавление завершено. Используйте /add для добавления следующего напитка.")


@dp.message(Command("delete"))
async def delete_item(message: types.Message):
    """Start the process of deleting a menu item."""
    menu = await get_menu()
    if not menu:
        await message.answer("Ваше меню пусто.")
        return

    # Create buttons for each menu item
    buttons = [
        [InlineKeyboardButton(text=item["coffee_name"], callback_data=f"delete_{item['menu_id']}")]
        for item in menu
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)  # Pass the buttons as `inline_keyboard`

    await message.answer("Выберите напиток для удаления:", reply_markup=keyboard)



### ORDER MANAGEMENT ###

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
            f"Заказ #{order['order_id']}:\n"
            f"Клиент: {order['user_id']}\n"
            f"Напиток: {order['coffee_name']}\n"  # Use coffee_name here
            f"Статус: {order['status']}",
            reply_markup=keyboard,
        )



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
        f"Заказ #{order_id} принят. Статус обновлен на 'готовится'.",
        reply_markup=keyboard,
    )
    await callback_query.answer("Заказ принят.")



@dp.callback_query(F.data.startswith("done_"))
async def complete_order(callback_query: types.CallbackQuery):
    """Handle completing an order."""
    order_id = int(callback_query.data.split("_")[1])

    # Update the order status to "готово"
    await update_order_status(order_id, "готово")

    # Add "Сгенерировать код" button
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Сгенерировать код", callback_data=f"generate_{order_id}")]
        ]
    )

    # Notify the cafe and show the "Сгенерировать код" button
    await callback_query.message.edit_text(
        f"Заказ #{order_id} готов. Статус обновлен на 'готово'.",
        reply_markup=keyboard,
    )
    await callback_query.answer("Заказ завершен.")

@dp.callback_query(F.data.startswith("generate_"))
async def generate_otp_code(callback_query: types.CallbackQuery):
    """Handle generating the OTP code for an order."""
    try:
        # Extract order_id from the callback data
        order_id = int(callback_query.data.split("_")[1])
    except (IndexError, ValueError):
        await callback_query.answer("Некорректный формат данных. Попробуйте снова.", show_alert=True)
        return

    # Generate a unique 4-digit OTP code
    otp_code = str(uuid.uuid4().int)[:4]

    # Update the database with the OTP code
    await update_order_otp(order_id, otp_code)

    # Notify the admin and user of the generated code
    await callback_query.message.edit_text(
        f"Заказ #{order_id} готов. OTP-код: {otp_code}\n"
        f"Пожалуйста, передайте этот код клиенту.",
    )
    await callback_query.answer("OTP-код сгенерирован.")

    # Notify the user about the generated OTP code
    order_details = await get_order_by_id(order_id)
    user_id = order_details["user_id"]
    await bot.send_message(
        user_id,
        f"Ваш заказ #{order_id} готов! Пожалуйста, сообщите кассиру этот OTP-код: {otp_code}",
    )


async def update_order_otp(order_id, otp_code):
    """Update the OTP code for an order."""
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