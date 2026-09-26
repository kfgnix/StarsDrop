import asyncio
import logging
import random
from datetime import datetime, timedelta
import aiosqlite

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery, LabeledPrice, PreCheckoutQuery
from aiogram.filters import CommandStart, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = "8718972364:AAEkSYNeN5w0ZcNMl5EKCryOv8ZEBIPr4Ko"
CHANNEL_ID = -1001234567890  # Замени на реальный ID своего канала (число)
CHANNEL_URL = "https://t.me/DropFreeTG"

MAX_FREE_SPINS = 10
SPINS_PER_HOUR = 2

# Шансы дропа (в процентах)
PRIZES = ["nothing", "small", "medium", "valuable", "super"]
WEIGHTS = [75.0, 20.0, 4.0, 0.9, 0.1]

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

# --- БАЗА ДАННЫХ ---

async def init_db():
    async with aiosqlite.connect('roulette.db') as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                stars_balance INTEGER DEFAULT 0,
                free_spins INTEGER DEFAULT 0,
                last_accrual_time TEXT,
                referrer_id INTEGER
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT,
                result TEXT,
                timestamp TEXT
            )
        ''')
        await db.commit()

async def get_user(user_id: int):
    async with aiosqlite.connect('roulette.db') as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)) as cursor:
            return await cursor.fetchone()

async def update_free_spins(user_id: int) -> int:
    user = await get_user(user_id)
    if not user:
        return 0
    
    last_time = datetime.fromisoformat(user['last_accrual_time'])
    now = datetime.now()
    hours_passed = int((now - last_time).total_seconds() // 3600)
    
    if hours_passed > 0:
        new_spins = min(MAX_FREE_SPINS, user['free_spins'] + (hours_passed * SPINS_PER_HOUR))
        new_time = (last_time + timedelta(hours=hours_passed)).isoformat()
        
        async with aiosqlite.connect('roulette.db') as db:
            await db.execute('UPDATE users SET free_spins = ?, last_accrual_time = ? WHERE user_id = ?', 
                             (new_spins, new_time, user_id))
            await db.commit()
        return new_spins
    return user['free_spins']

# --- ХЭНДЛЕРЫ И ЛОГИКА ---

async def check_subscription(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception:
        return False

@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject):
    user_id = message.from_user.id
    ref_id = command.args if command.args and command.args.isdigit() else None
    
    async with aiosqlite.connect('roulette.db') as db:
        user = await get_user(user_id)
        if not user:
            await db.execute(
                'INSERT INTO users (user_id, referrer_id, free_spins, last_accrual_time) VALUES (?, ?, ?, ?)', 
                (user_id, ref_id, 3, datetime.now().isoformat())
            )
            await db.commit()
            
            if ref_id and int(ref_id) != user_id:
                await db.execute(
                    'UPDATE users SET free_spins = MIN(free_spins + 2, ?) WHERE user_id = ?', 
                    (MAX_FREE_SPINS, int(ref_id))
                )
                await db.commit()
                try:
                    await bot.send_message(int(ref_id), "Ваш друг присоединился! Вы получили +2 попытки.")
                except Exception:
                    pass

    await send_main_menu(message)

async def send_main_menu(message: Message):
    user_id = message.from_user.id
    spins = await update_free_spins(user_id)
    user = await get_user(user_id)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🎰 Крутить (Бесплатно)", callback_data="spin_free")
    builder.button(text="⭐️ Крутить (5 Stars)", callback_data="spin_paid")
    builder.button(text="📦 10 спинов (40 Stars)", callback_data="buy_10")
    builder.button(text="📢 Канал (Проверка)", url=CHANNEL_URL)
    builder.adjust(1, 1, 1, 1)
    
    bot_info = await bot.me()
    text = (
        f"🎮 **Рулетка**\n\n"
        f"Бесплатных попыток: {spins}/{MAX_FREE_SPINS} (восстанавливаются по {SPINS_PER_HOUR} в час)\n"
        f"Баланс: {user['stars_balance']} ⭐️\n\n"
        f"Ваша реферальная ссылка:\nhttps://t.me/{bot_info.username}?start={user_id}"
    )
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@router.callback_query(F.data == "spin_free")
async def process_free_spin(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    if not await check_subscription(user_id):
        await callback.answer("Сначала подпишитесь на канал!", show_alert=True)
        return

    spins = await update_free_spins(user_id)
    if spins <= 0:
        await callback.answer("У вас нет бесплатных попыток. Подождите или купите за Stars.", show_alert=True)
        return

    async with aiosqlite.connect('roulette.db') as db:
        await db.execute('UPDATE users SET free_spins = free_spins - 1 WHERE user_id = ?', (user_id,))
        prize = random.choices(PRIZES, weights=WEIGHTS, k=1)[0]
        await db.execute(
            'INSERT INTO history (user_id, action, result, timestamp) VALUES (?, ?, ?, ?)', 
            (user_id, 'spin_free', prize, datetime.now().isoformat())
        )
        
        if prize == "small":
            await db.execute('UPDATE users SET stars_balance = stars_balance + 2 WHERE user_id = ?', (user_id,))
        await db.commit()

    messages = {
        "nothing": "Ничего не выпало. Повезет в следующий раз! 😔",
        "small": "Вы выиграли утешительный приз! +2 ⭐️ на баланс.",
        "medium": "Средний приз! Вы получили скидку 15% на услуги.",
        "valuable": "Ого! Ценный приз. Свяжитесь с админом для получения подарка.",
        "super": "🔥 ДЖЕКПОТ! Вы получили доступ в закрытый клуб."
    }
    await callback.message.answer(f"🎲 Результат: {messages[prize]}")
    await callback.answer()

@router.callback_query(F.data == "spin_paid")
async def process_paid_spin(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if user['stars_balance'] < 5:
        await callback.answer("Недостаточно звезд на балансе!", show_alert=True)
        return

    async with aiosqlite.connect('roulette.db') as db:
        await db.execute('UPDATE users SET stars_balance = stars_balance - 5 WHERE user_id = ?', (user_id,))
        prize = random.choices(PRIZES, weights=WEIGHTS, k=1)[0]
        await db.execute(
            'INSERT INTO history (user_id, action, result, timestamp) VALUES (?, ?, ?, ?)', 
            (user_id, 'spin_paid', prize, datetime.now().isoformat())
        )
        
        if prize == "small":
            await db.execute('UPDATE users SET stars_balance = stars_balance + 2 WHERE user_id = ?', (user_id,))
        await db.commit()

    await callback.message.answer(f"🎰 Платный прокрут. Результат: {prize}")
    await callback.answer()

@router.callback_query(F.data == "buy_10")
async def buy_10_spins(callback: CallbackQuery):
    prices = [LabeledPrice(label="10 Вращений", amount=40)]
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title="Пакет вращений",
        description="10 дополнительных попыток для рулетки",
        payload="buy_10_payload",
        provider_token="",
        currency="XTR",
        prices=prices
    )
    await callback.answer()

@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@router.message(F.successful_payment)
async def process_successful_payment(message: Message):
    user_id = message.from_user.id
    payload = message.successful_payment.invoice_payload
    
    if payload == "buy_10_payload":
        async with aiosqlite.connect('roulette.db') as db:
            await db.execute('UPDATE users SET free_spins = free_spins + 10 WHERE user_id = ?', (user_id,))
            await db.execute(
                'INSERT INTO history (user_id, action, result, timestamp) VALUES (?, ?, ?, ?)', 
                (user_id, 'buy_stars', '10_spins', datetime.now().isoformat())
            )
            await db.commit()
            
        await message.answer("✅ Оплата прошла успешно! Вам начислено 10 вращений.")

async def main():
    await init_db()
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())