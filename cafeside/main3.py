import os
import uuid
import logging
import asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters.command import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
import psycopg2
from psycopg2.extras import RealDictCursor
import re
from aiogram.exceptions import TelegramNetworkError,TelegramBadRequest
import time
from aiogram import Router, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load your API key and database URL
API_KEY = "7537071518:AAE2fDi3HoOT4p8RNmptqzwwEOgXUDdhoZw"
DB_URL = "postgresql://postgres.jmujxtsvrbhlvthkkbiq:dbanMcmX9oxJyQlE@aws-0-eu-central-1.pooler.supabase.com:6543/postgres"
#API_KEY = "5630308060:AAEXU8OHgxBeZ_AByL3mGAqVAJ079eidxAo"
#DB_URL="postgresql://postgres.xerkmpqjygwvwzgiysep:23147513Faq@aws-0-eu-central-1.pooler.supabase.com:6543/postgres"
# FSM States
class MenuState(StatesGroup):
    waiting_for_new_item = State()
bot = Bot(token=API_KEY, timeout=60)

dp = Dispatcher()

router = Router()
dp.include_router(router)
@router.errors()
async def handle_errors(update: types.Update, exception: Exception):
    if isinstance(exception, TelegramNetworkError):
        logger.warning(f"Проблема с сетью: {exception}. Повторная попытка через 5 секунд.")
        await asyncio.sleep(5)
        return True  # Пробуем снова
    logger.error(f"Необработанная ошибка: {exception}")
    return False

async def db_execute(query, params=None, fetch=False):
    global db_connection
    try:
        if db_connection.closed:
            db_connection = psycopg2.connect(DB_URL)
        with db_connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, params)
            db_connection.commit()  # Фиксируем изменения
            if fetch:
                return cursor.fetchall() or []
    except psycopg2.OperationalError as e:
        logger.error(f"Ошибка подключения: {e}")
        db_connection.rollback()  # Откат при ошибке
        return None
    except Exception as e:
        logger.error(f"Ошибка запроса: {e}")
        db_connection.rollback()  # Откат при ошибке
        return None



### MENU MANAGEMENT ###
def clean_message_cache():
    """Удаляет старые записи из кэша."""
    current_time = time.time()
    expiry_time = 3600  # Сообщения старше 1 часа удаляются
    removed = 0  # Счетчик удаленных записей
    
    for message_id, (timestamp, _) in list(message_cache.items()):
        if current_time - timestamp > expiry_time:
            del message_cache[message_id]
            removed += 1

    logger.info(f"Очистка кэша завершена. Удалено {removed} записей.")

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

@router.message(Command("start"))
async def start(message: types.Message):
    global cafe_id
    # Fetch the cafe ID associated with this admin
    admin_id = str(message.from_user.id)

    query = "SELECT cafe_id FROM admins WHERE telegram_id = %s;"
    result = await db_execute(query, params=(admin_id,), fetch=True)

    if not result:
        await message.answer("У вас нет доступа к этому боту. Обратитесь к администратору.")
        return

    # Extract the cafe_id from the query result
    cafe_id = result[0]["cafe_id"]

    # Create inline keyboard
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Меню", callback_data="menu")],
        [InlineKeyboardButton(text="Заказы", callback_data="orders")]
    ])

    await message.answer(
        "Добро пожаловать в кафе-бот! Вы можете управлять своим меню.",
        reply_markup=keyboard
    )

@router.message(Command("menu"))
async def show_menu_command(message: types.Message):
    global cafe_id
    # Fetch the cafe ID associated with this admin
    admin_id = str(message.from_user.id)

    query = "SELECT cafe_id FROM admins WHERE telegram_id = %s;"
    result = await db_execute(query, params=(admin_id,), fetch=True)

    if not result:
        await message.answer("У вас нет доступа к этому боту. Обратитесь к администратору.")
        return

    # Extract the cafe_id from the query result
    cafe_id = result[0]["cafe_id"]

    await render_menu(message, page=0)
    
@router.callback_query(F.data == "menu")
async def show_menu_callback(callback_query: CallbackQuery):
    await render_menu(callback_query.message, page=0)


async def render_menu(message_or_callback, page: int = 0):
    """Render the menu for a specific page."""
    menu = await get_menu()
    buttons = []

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    if not menu:
        if isinstance(message_or_callback, types.Message):
            await message_or_callback.answer("Ваше меню пусто.",reply_markup=keyboard)
        elif isinstance(message_or_callback, types.CallbackQuery):
            await message_or_callback.message.edit_text("Ваше меню пусто.",reply_markup=keyboard)
        return

    for item in menu:
        availability = "Доступно" if item["is_available"] else "Недоступно"
        buttons.append([
            InlineKeyboardButton(
                text=f"{item['coffee_name']} ({availability})",
                callback_data=f"toggle_{item['menu_id']}_{page}"
            ),
         #   InlineKeyboardButton(
          #      text="Удалить",
           #     callback_data=f"delete_confirm_{item['menu_id']}_{page}"
            #)
        ])
   # buttons.append([
    #    InlineKeyboardButton(
     #       text="Добавить напиток",
      #      callback_data="add"
       # )
    #])
    # Add navigation buttons
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    if isinstance(message_or_callback, types.Message):
        await message_or_callback.answer("Here is your menu:", reply_markup=keyboard)
    elif isinstance(message_or_callback, types.CallbackQuery):
        await message_or_callback.message.edit_text("Here is your menu:", reply_markup=keyboard)


@router.callback_query(F.data == "add")
async def add_item_callback(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.message.answer("Введите название нового напитка.")
    await state.set_state(MenuState.waiting_for_new_item)


@router.message(MenuState.waiting_for_new_item)
async def handle_new_menu_item(message: types.Message, state: FSMContext):
    """Handle adding the menu item after receiving the name."""
    coffee_name = message.text.strip()

    if not coffee_name:
        await message.answer("Название не может быть пустым. Попробуйте снова.")
        return

    await add_menu_item(coffee_name)
    await message.answer(f"Напиток '{coffee_name}' добавлен в меню.")
    await state.clear()


# Кэш для хранения текста сообщений
message_cache = {}
def clean_message_cache():
    """Удаляет старые записи из кэша."""
    current_time = time.time()
    expiry_time = 3600  # Сообщения старше 1 часа удаляются
    for message_id, (timestamp, _) in list(message_cache.items()):
        if current_time - timestamp > expiry_time:
            del message_cache[message_id]
            logger.info(f"Сообщение с ID {message_id} удалено из кэша.")
message_cache_lock = asyncio.Lock()  # Добавляем блокировку

async def monitor_order_status():
    """Мониторинг заказов и обновление их статусов в чате кафе."""
    while True:
        try:
            async with message_cache_lock:  # Синхронизируем доступ к кэшу
                query = """
                    SELECT 
                        o.order_id, 
                        o.user_id, 
                        o.menu_id, 
                        o.order_date, 
                        o.status, 
                        o.cancel_notified, 
                        o.message_id, 
                        m.coffee_name, 
                        u.username, 
                        u.phone_number, 
                        o.cafe_id,
                        o.is_finished
                    FROM orders o
                    JOIN menu m ON o.menu_id = m.menu_id
                    JOIN users u ON o.user_id = u.user_id
                    WHERE o.status = 'canceled' 
                        AND o.cancel_notified = FALSE 
                        AND o.is_finished = FALSE;
                """
                canceled_orders = await db_execute(query, fetch=True)
                if not canceled_orders:
                    logger.info("Нет отменённых заказов для обработки")
                    await asyncio.sleep(5)
                    continue
                for order in canceled_orders:
                    order_id = order["order_id"]
                    message_id = order["message_id"]
                    cafe_id = order["cafe_id"]

                    # Формируем новый текст сообщения
                    new_text = (
                        f"🔴 Заказ №{order_id} был отменён клиентом 🔴\n"
                        f"Клиент: @{order['username']}\n"
                        f"Номер: {order['phone_number']}\n"
                        f"Напиток: {order['coffee_name']}\n"
                        f"Дата заказа: {order['order_date']}"
                    )

                    # Получаем ID чата кафе
                    cafe_chat_id = await get_cafe_chat_id(cafe_id)

                    if not cafe_chat_id or not message_id:
                        continue

                    # Проверяем хэш сообщения
                    current_hash = hash(message_cache.get(message_id, ''))
                    new_hash = hash(new_text)
                    
                    logger.info(
                        f"Hash check for message_id={message_id}: "
                        f"cached={current_hash}, new={new_hash}"
                    )

                    if current_hash == new_hash:
                        logger.info(f"Сообщение {message_id} не изменилось (hash match)")
                        continue

                    try:
                        # Пытаемся обновить сообщение
                        await bot.edit_message_text(
                            chat_id=cafe_chat_id,
                            message_id=message_id,
                            text=new_text
                        )
                        
                        # Обновляем кэш ПОСЛЕ успешного редактирования
                        message_cache[message_id] = new_text
                        logger.info(f"Сообщение {message_id} успешно обновлено")

                        # Атомарно обновляем статус в БД
                        update_query = """
                            UPDATE orders
                            SET cancel_notified = TRUE, 
                                is_finished = TRUE
                            WHERE order_id = %s;
                        """
                        await db_execute(update_query, params=(order_id,))

                    except TelegramBadRequest as e:
                        if "message is not modified" in str(e):
                            logger.info(f"Сообщение {message_id} уже актуально")
                            message_cache[message_id] = new_text  # Обновляем кэш при false-positive
                        else:
                            logger.error(f"Ошибка редактирования {message_id}: {e}")
                    except Exception as e:
                        logger.error(f"Критическая ошибка: {e}")

        except Exception as e:
            logger.error(f"Ошибка в monitor_order_status: {e}")
        
        await asyncio.sleep(5)  # Увеличиваем интервал для снижения нагрузки
@router.callback_query(F.data.startswith("toggle_"))
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


@router.callback_query(F.data.startswith("delete_confirm_"))
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


@router.callback_query(F.data.startswith("delete_"))
async def delete_item(callback_query: types.CallbackQuery):
    """Delete a menu item after confirmation."""
    data_parts = callback_query.data.split("_")
    menu_id = int(data_parts[1])
    page = int(data_parts[2])

    await delete_menu_item(menu_id)
    await render_menu(callback_query, page=page)
    await callback_query.answer("Напиток удален.")


@router.callback_query(F.data.startswith("cancel_delete_"))
async def cancel_delete(callback_query: types.CallbackQuery):
    """Cancel the deletion process."""
    page = int(callback_query.data.split("_")[2])
    await render_menu(callback_query, page=page)
    await callback_query.answer("Удаление отменено.")

@router.callback_query(lambda c: c.data.startswith("prev_page_"))
async def previous_page(callback_query: types.CallbackQuery):
    """Go to the previous page."""
    page = int(callback_query.data.split("_")[2])
    await render_menu(callback_query, page=page)
    await callback_query.answer()  # Acknowledge the callback


@router.callback_query(lambda c: c.data.startswith("next_page_"))
async def next_page(callback_query: types.CallbackQuery):
    """Go to the next page."""
    page = int(callback_query.data.split("_")[2])
    await render_menu(callback_query, page=page)
    await callback_query.answer()  # Acknowledge the callback

### MAIN ###
async def get_incoming_orders():
    """Retrieve incoming orders for the current cafe."""
    query = """
        SELECT o.order_id, o.user_id, u.username, o.menu_id, o.order_date, o.status, m.coffee_name, u.phone_number,o.take_out
        FROM orders o
        JOIN menu m ON o.menu_id = m.menu_id
        JOIN users u ON o.user_id = u.user_id
        WHERE o.cafe_id = %s AND o.status = 'pending';
    """
    return await db_execute(query, params=(cafe_id,), fetch=True)
async def update_order_status(order_id, status):
    """Update the status of an order and set is_finished if necessary."""
    # Check the current status of the order
    query_check_status = "SELECT status FROM orders WHERE order_id = %s;"
    result = await db_execute(query_check_status, params=(order_id,), fetch=True)
    
    if not result:
        logger.warning(f"Заказ с ID {order_id} не найден.")
        return False  # Order not found

    current_status = result[0]['status']
    if current_status in ['canceled', 'выдан']:
        logger.warning(f"Попытка изменить статус отменённого или выданного заказа #{order_id}")
        return False  # Prohibit status changes for finished orders

    # Determine if is_finished should be set to TRUE
    if status in ['canceled', 'выдан']:
        query_update_status = """
            UPDATE orders
            SET status = %s, is_finished = TRUE
            WHERE order_id = %s;
        """
    else:
        query_update_status = """
            UPDATE orders
            SET status = %s
            WHERE order_id = %s;
        """
    
    try:
        await db_execute(query_update_status, params=(status, order_id))
        db_connection.commit()
        return True
    except Exception as e:
        logger.error(f"Ошибка при обновлении статуса заказа #{order_id}: {e}")
        db_connection.rollback()
        return False




@router.callback_query(F.data == "orders")
async def show_orders_callback(callback_query: CallbackQuery):
    orders = await get_incoming_orders()

    if not orders:
        await callback_query.message.answer("Нет новых заказов.")
        return

    for order in orders:
        buttons = []

        if order["status"] == "pending":
            # Кнопки для принятия и отмены заказа
            buttons.append([InlineKeyboardButton(text="Принять", callback_data=f"accept^{order['order_id']}")])
            buttons.append([InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_cafe^{order['order_id']}")])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        message = await callback_query.message.answer(
            f"🔵 Новый заказ #{order['order_id']}: 🔵\n"
            f"Клиент: @{order['username']}\n"
            f"Номер: {order.get('phone_number', 'Нет номера')}\n"
            f"Напиток: {order['coffee_name']}\n"
            f"{order['take_out']}\n"
            f"Комментарий: {order['details']}\n",
            reply_markup=keyboard,
        )

        # Сохраняем message_id для обновления в будущем
        update_query = """
            UPDATE orders
            SET message_id = %s
            WHERE order_id = %s;
        """
        await db_execute(update_query, params=(message.message_id, order["order_id"]))


@router.callback_query(F.data.startswith("cancel_cafe^"))
async def handle_cafe_cancel_order(callback_query: CallbackQuery):
    """Обработка отмены заказа со стороны кафе."""
    try:
        order_id = int(callback_query.data.split("^")[1])

        # Проверяем текущий статус заказа
        query = "SELECT status FROM orders WHERE order_id = %s;"
        result = await db_execute(query, params=(order_id,), fetch=True)

        if not result:
            await callback_query.answer("Заказ не найден.", show_alert=True)
            return

        current_status = result[0]["status"]

        if current_status in ["готово", "выдан"]:
            await callback_query.answer("Этот заказ уже завершён и не может быть отменён.", show_alert=True)
            return

        # Отменяем заказ
        update_query = """
                        UPDATE orders
                        SET status = 'canceled', cancel_notified = TRUE, is_finished = TRUE
                        WHERE order_id = %s;
                    """
        await db_execute(update_query, params=(order_id,))

        # Получаем данные заказа
        order_details = await get_order_by_id(order_id)
        if not order_details:
            await callback_query.answer("Ошибка: заказ не найден.", show_alert=True)
            return
        username = order_details.get("username", "Неизвестный пользователь")
        phone_number = order_details.get("phone_number", "Не указан")
        coffee_name = order_details.get("coffee_name", "Не указан")
        details = order_details.get("details", "Нет комментария")
        take_out= order_details.get("take_out","Не указан")
        order_date = order_details.get("order_date", "Не указана")
        # Уведомление кафе
        await callback_query.message.edit_text(
            f"🔴 Заказ #{order_details['order_id']} был отменён кофейней🔴\n"
            f"Клиент: @{username}\n"
            f"Номер: {phone_number}\n"
            f"Напиток: {coffee_name}\n"
            f"{take_out}\n"
            f"Комментарий: {details}\n"
            f"Дата заказа: {order_date}\n"
        )
        await callback_query.answer("Заказ отменён.")
    except Exception as e:
        logger.error(f"Ошибка отмены заказа кафе: {e}")
        await callback_query.answer("Произошла ошибка при отмене заказа. Попробуйте позже.", show_alert=True)


async def get_user_by_id(user_id):
    """Retrieve user information by user_id."""
    query = "SELECT * FROM users WHERE user_id = %s;"
    result = await db_execute(query, params=(user_id,), fetch=True)
    return result[0] if result else None


@router.callback_query(F.data.startswith("accept^"))
async def accept_order(callback_query: types.CallbackQuery):
    
    order_id = int(callback_query.data.split("^")[1])
    # Получаем данные заказа из базы
    order = await get_order_by_id(order_id)
    print(order)
    if not order:
        await callback_query.answer("Заказ не найден.", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Готово", callback_data=f"done^{order_id}")]
        ])
    # Логика обработки
    await update_order_status(order_id, "готовится")
    await callback_query.message.edit_text(
        f"🟡Заказ #{order_id} принят🟡\n"
        f"Клиент: @{order['username']}\n"
        f"Номер: {order['phone_number']}\n"
        f"Напиток: {order['coffee_name']}\n"
        f"{order['take_out']}\n"
        f"Комментарий: {order['details']}\n"
        f"Статус был обновлён на: готовится",
        reply_markup=keyboard
    )
    await callback_query.answer("Заказ принят.")



@router.callback_query(F.data.startswith("done^"))
async def complete_order(callback_query: types.CallbackQuery):
    """Handle completing an order."""
    try:
        order_id = int(callback_query.data.split("^")[1])

        # Обновляем статус заказа на "готово"
        success = await update_order_status(order_id, "готово")
        if not success:
            await callback_query.answer("Невозможно обновить статус заказа.", show_alert=True)
            return

        # Получаем детали заказа
        order_details = await get_order_by_id(order_id)
        if not order_details:
            await callback_query.answer("Ошибка: заказ не найден.", show_alert=True)
            return

        # Генерируем клавиатуру с кнопкой для OTP
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Сгенерировать OTP",
                        callback_data=f"generate^{order_id}"
                    )
                ]
            ]
        )

        # Обновляем сообщение администратора
        await callback_query.message.edit_text(
            f"🟣Заказ №{order_details['order_id']} готов🟣\n"
            f"Клиент: @{order_details['username']}\n"
            f"Номер: {order_details['phone_number']}\n"
            f"Напиток: {order_details['coffee_name']}\n"
            f"{order_details['take_out']}\n"
            f"Комментарий: {order_details['details']}\n"
            f"Статус был обновлён на: готов",
            reply_markup=keyboard
        )
        await callback_query.answer("Статус заказа обновлён.")
    except Exception as e:
        logger.error(f"Ошибка в обработке 'done_': {e}")




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


@router.callback_query(F.data.startswith("generate^"))
async def generate_otp_code(callback_query: types.CallbackQuery):
    """Generate and send the OTP code for the order."""
    try:
        order_id = int(callback_query.data.split("^")[1])

        # Генерация уникального 4-значного OTP-кода
        otp_code = str(uuid.uuid4().int)[:4]
        await update_order_otp(order_id, otp_code)

        # Получение информации о заказе
        order_details = await get_order_by_id(order_id)
        if not order_details:
            await callback_query.answer("Ошибка: заказ не найден.", show_alert=True)
            return

        # Клавиатура для подтверждения выдачи
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Подтвердить выдачу",
                        callback_data=f"confirm_issued^{order_id}"
                    )
                ]
            ]
        )

        # Обновление сообщения для админа
        await callback_query.message.edit_text(
            f"🟤Заказ №{order_details['order_id']} готов🟤\n"
            f"Клиент: @{order_details['username']}\n"
            f"Номер: {order_details['phone_number']}\n"
            f"Напиток: {order_details['coffee_name']}\n"
            f"{order_details['take_out']}\n"
            f"Комментарий: {order_details['details']}\n"
            f"⭕️ OTP-код для клиента : {otp_code} ⭕️\n"
            f"Если код совпадает, подтвердите выдачу.",
            reply_markup=keyboard
        )
        await callback_query.answer("OTP-код сгенерирован.")
    except Exception as e:
        logger.error(f"Ошибка в обработке 'generate^': {e}")
        await callback_query.answer("Произошла ошибка при генерации OTP-кода.", show_alert=True)


async def update_order_otp(order_id, otp_code):
    """Обновить OTP-код для заказа."""
    query = "UPDATE orders SET otp_code = %s WHERE order_id = %s;"
    await db_execute(query, params=(otp_code, order_id))

@router.callback_query(F.data.startswith("confirm_issued^"))
async def confirm_order_issued(callback_query: types.CallbackQuery):
    """Handle confirming the order has been issued."""
    try:
        order_id = int(callback_query.data.split("^")[1])

        # Обновляем статус заказа на "выдан"
        success = await update_order_status(order_id, "выдан")
        if not success:
            await callback_query.answer("Ошибка при обновлении статуса заказа.", show_alert=True)
            return

        # Получаем информацию о заказе
        order_details = await get_order_by_id(order_id)
        if not order_details:
            await callback_query.answer("Ошибка: заказ не найден.", show_alert=True)
            return

        # Обновляем сообщение для админа
        await callback_query.message.edit_text(
            f"✅ Заказ №{order_details['order_id']} успешно завершен ✅\n"
            f"Клиент: @{order_details['username']}\n"
            f"Номер: {order_details['phone_number']}\n"
            f"Напиток: {order_details['coffee_name']}\n"
            f"{order_details['take_out']}\n"
            f"Комментарий: {order_details['details']}\n"
            f"Дата заказа: {order_details['order_date']}",
        )
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await callback_query.answer("Заказ помечен как выдан.")
    except Exception as e:
        logger.error(f"Ошибка в обработке 'confirm_issued^': {e}")




async def auto_push_new_orders():
    """Проверяет базу на новые заказы и отправляет уведомления в кафе."""
    already_notified = set()

    while True:
        try:
            query = """
                SELECT o.order_id, u.username, m.coffee_name, o.cafe_id, o.status, o.details, u.phone_number, o.order_date, o.take_out
                FROM orders o
                JOIN menu m ON o.menu_id = m.menu_id
                JOIN users u ON o.user_id = u.user_id
                WHERE o.status = 'pending';
            """
            orders = await db_execute(query, fetch=True)
            if orders is None:
                logger.error("Ошибка получения заказов")
                await asyncio.sleep(10)
                continue
            logger.info(f"🔄 Проверка новых заказов... (найдено: {len(orders)})")

            if not orders:
                await asyncio.sleep(4)
                continue

            for order in orders:
                if order["order_id"] in already_notified:
                    continue

                already_notified.add(order["order_id"])
                cafe_chat_id = await get_cafe_chat_id(order["cafe_id"])

                message_text = (
                    f"🔵 Новый заказ #{order['order_id']}: 🔵\n"
                    f"Клиент: @{order['username']}\n"
                    f"Номер: {order.get('phone_number', 'Нет номера')}\n"
                    f"Напиток: {order['coffee_name']}\n"
                    f"{order['take_out']}\n"
                    f"Комментарий: {order['details']}\n"
                )

                buttons = [
                    [InlineKeyboardButton(text="Принять", callback_data=f"accept^{order['order_id']}")],
                    [InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_cafe^{order['order_id']}")],
                ]
                keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

                if cafe_chat_id:
                    try:
                        message = await bot.send_message(chat_id=cafe_chat_id, text=message_text, reply_markup=keyboard)
                        update_query = "UPDATE orders SET message_id = %s WHERE order_id = %s;"
                        await db_execute(update_query, params=(message.message_id, order["order_id"]))
                        logger.info(f"📩 Уведомление отправлено для заказа {order['order_id']}.")
                    except Exception as send_error:
                        logger.error(f"❌ Ошибка отправки сообщения: {send_error}")

        except Exception as e:
            logger.error(f"❌ Ошибка в `auto_push_new_orders()`: {e}")
            await asyncio.sleep(10)

        await asyncio.sleep(4)


async def get_admin_contact(cafe_id):
    """Retrieve the admin's Telegram ID for a given cafe."""
    query = "SELECT telegram_id FROM admins WHERE cafe_id = %s LIMIT 1;"
    result = await db_execute(query, params=(cafe_id,), fetch=True)
    return result[0]["telegram_id"] if result else None



async def get_cafe_chat_id(cafe_id):
    """Retrieve the chat ID for the cafe."""
    query = "SELECT chat_id FROM cafes WHERE cafe_id = %s;"
    result = await db_execute(query, params=(cafe_id,), fetch=True)
    return result[0]["chat_id"] if result else None

async def send_notification(chat_id, message_text):
    """Send a notification to a specific chat_id."""
    try:
        await bot.send_message(chat_id=chat_id, text=message_text)
    except Exception as e:
        logger.error(f"Error sending notification: {e}")
async def clean_cache_periodically():
    """Фоновая задача для очистки кэша каждые 15 минут."""
    while True:
        clean_message_cache()
        logger.info("Кэш сообщений очищен.")
        await asyncio.sleep(900)  # 900 секунд = 15 минут

### MAIN ###
async def main():
    global db_connection
    db_connection = psycopg2.connect(DB_URL)

    tasks = [
        asyncio.create_task(monitor_order_status()),
        asyncio.create_task(clean_cache_periodically()),
        asyncio.create_task(auto_push_new_orders()),
    ]
    logger.info("Бот запущен и готов к работе")

    try:
        await dp.start_polling(bot)
    finally:
        for task in tasks:
            task.cancel()


    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())