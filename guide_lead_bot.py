"""
Telegram-бот для сбора лидов с проверкой подписки на канал.

Механика:
1. Пользователь жмёт кнопку "Забрать гайд" в посте канала -> диплинк на бота (/start)
2. Бот фиксирует лида в SQLite + шлёт уведомление в админ-чат
3. Бот проверяет подписку на канал lab100_mv
4. Если не подписан - просит подписаться, показывает кнопку "Я подписался"
5. Если подписан - отправляет PDF-гайд

Установка зависимостей:
    pip install aiogram==3.15.0

Перед запуском заполните переменные в блоке CONFIG ниже
(или, что безопаснее, задайте их через переменные окружения на хостинге).
"""

import asyncio
import csv
import logging
import os
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
)

# ========================= CONFIG =========================
# Токен от @BotFather
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН_СЮДА")

# Username канала без @
CHANNEL_USERNAME = "lab100_mv"

# ID чата/группы, куда будут прилетать уведомления о новых лидах.
# Чтобы узнать ID: напишите боту @userinfobot в личку или добавьте
# его в свой админ-чат - он покажет chat_id.
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

# Список user_id админов, которым доступна команда /export
ADMIN_USER_IDS = {int(os.getenv("ADMIN_USER_ID", "0"))}

# Путь к PDF-файлу гайда (положите файл рядом со скриптом)
GUIDE_PDF_PATH = "guide.pdf"

DB_PATH = "leads.db"
# ============================================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ---------------------- БАЗА ДАННЫХ ----------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS leads (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            first_seen TEXT,
            subscribed INTEGER DEFAULT 0,
            guide_sent INTEGER DEFAULT 0,
            guide_sent_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def upsert_lead(user_id: int, username: str, first_name: str) -> bool:
    """Сохраняет лида. Возвращает True, если это новый лид."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT user_id FROM leads WHERE user_id = ?", (user_id,))
    is_new = cur.fetchone() is None
    if is_new:
        conn.execute(
            "INSERT INTO leads (user_id, username, first_name, first_seen) VALUES (?, ?, ?, ?)",
            (user_id, username, first_name, datetime.utcnow().isoformat()),
        )
    conn.commit()
    conn.close()
    return is_new


def mark_guide_sent(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE leads SET subscribed = 1, guide_sent = 1, guide_sent_at = ? WHERE user_id = ?",
        (datetime.utcnow().isoformat(), user_id),
    )
    conn.commit()
    conn.close()


def export_leads_to_csv(path: str = "leads_export.csv") -> str:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "SELECT user_id, username, first_name, first_seen, subscribed, guide_sent, guide_sent_at FROM leads ORDER BY first_seen DESC"
    )
    rows = cur.fetchall()
    conn.close()
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["user_id", "username", "first_name", "first_seen", "subscribed", "guide_sent", "guide_sent_at"]
        )
        writer.writerows(rows)
    return path


# ---------------------- ЛОГИКА ПОДПИСКИ ----------------------
async def is_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning(f"Не удалось проверить подписку для {user_id}: {e}")
        return False


def subscribe_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Перейти в канал", url=f"https://t.me/{CHANNEL_USERNAME}")],
            [InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub")],
        ]
    )


async def send_guide(chat_id: int, user_id: int):
    if not os.path.exists(GUIDE_PDF_PATH):
        await bot.send_message(chat_id, "Гайд временно недоступен, мы уже разбираемся 🙏")
        logger.error(f"Файл гайда не найден: {GUIDE_PDF_PATH}")
        return
    await bot.send_document(
        chat_id,
        FSInputFile(GUIDE_PDF_PATH),
        caption="Вот ваш гайд! Спасибо, что подписались 🙌",
    )
    mark_guide_sent(user_id)


# ---------------------- ХЕНДЛЕРЫ ----------------------
@dp.message(CommandStart())
async def handle_start(message: Message):
    user = message.from_user
    is_new = upsert_lead(user.id, user.username or "", user.first_name or "")

    if is_new and ADMIN_CHAT_ID:
        uname = f"@{user.username}" if user.username else "(без username)"
        await bot.send_message(
            ADMIN_CHAT_ID,
            f"🆕 Новый лид!\nИмя: {user.first_name}\nUsername: {uname}\nID: {user.id}",
        )

    if await is_subscribed(user.id):
        await send_guide(message.chat.id, user.id)
    else:
        await message.answer(
            "Чтобы получить гайд, подпишитесь на наш канал и нажмите кнопку ниже 👇",
            reply_markup=subscribe_keyboard(),
        )


@dp.callback_query(F.data == "check_sub")
async def handle_check_sub(callback: CallbackQuery):
    user_id = callback.from_user.id
    if await is_subscribed(user_id):
        await callback.message.edit_text("Спасибо за подписку! Отправляю гайд 📄")
        await send_guide(callback.message.chat.id, user_id)
    else:
        await callback.answer("Пока не вижу подписку 🤔 Подпишитесь и попробуйте снова", show_alert=True)


@dp.message(Command("export"))
async def handle_export(message: Message):
    if message.from_user.id not in ADMIN_USER_IDS:
        return
    path = export_leads_to_csv()
    await message.answer_document(FSInputFile(path), caption="Выгрузка всех лидов")


# ---------------------- ЗАПУСК ----------------------
async def main():
    init_db()
    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
