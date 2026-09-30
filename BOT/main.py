import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage


# ============================================================
# НАСТРОЙКИ
# ============================================================

import os

BOT_TOKEN = os.getenv("BOT_TOKEN")

# АДМИН-ГРУППА
ADMIN_GROUP_ID = -1004380534371

# ОФИЦИАЛЬНЫЙ КАНАЛ
OFFICIAL_CHANNEL_ID = -1004361362556
OFFICIAL_CHANNEL_LINK = "https://t.me/SLSleaguer"

# ОСНОВНЫЕ АДМИНЫ
CREATOR_ID = 7762496102
ADMIN_ID = 7908057052

# БАЗА
DB_NAME = "sl27.db"

# 10 минут на отписку
WITHDRAWAL_TIMEOUT_MINUTES = 10

# 5 часов на результат
RESULT_TIMEOUT_HOURS = 5


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)


# ============================================================
# BOT
# ============================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher(
    storage=MemoryStorage()
)


# ============================================================
# DATABASE
# ============================================================

db = sqlite3.connect(
    DB_NAME,
    check_same_thread=False
)

db.row_factory = sqlite3.Row

cur = db.cursor()


# ============================================================
# USERS
# ============================================================

cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    created_at TEXT
)
""")


# ============================================================
# SCHEDULES
# ============================================================

cur.execute("""
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    created_at TEXT
)
""")


# ============================================================
# RESULTS
# ============================================================

cur.execute("""
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    created_at TEXT
)
""")


# ============================================================
# MVP
# ============================================================

cur.execute("""
CREATE TABLE IF NOT EXISTS mvps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT,
    photo_id TEXT,
    created_at TEXT
)
""")


# ============================================================
# QUESTIONS
# ============================================================

cur.execute("""
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    username TEXT,
    question TEXT NOT NULL,
    answer TEXT,
    status TEXT DEFAULT 'pending',
    created_at TEXT
)
""")


# ============================================================
# WITHDRAWALS
# ============================================================

cur.execute("""
CREATE TABLE IF NOT EXISTS withdrawals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    club1 TEXT NOT NULL,
    club2 TEXT NOT NULL,
    message_id INTEGER,
    channel_message_id INTEGER,
    status TEXT DEFAULT 'open',
    created_at TEXT
)
""")


# ============================================================
# WITHDRAWAL PLAYERS
# ============================================================

cur.execute("""
CREATE TABLE IF NOT EXISTS withdrawal_players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    withdrawal_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    username TEXT,
    club TEXT NOT NULL,
    club_number INTEGER DEFAULT 1,
    status TEXT DEFAULT 'pending',
    stage TEXT DEFAULT 'pending',
    accepted_at TEXT,
    vip_sent_at TEXT,
    result_deadline TEXT,
    result_sent INTEGER DEFAULT 0,
    tp_reason TEXT,
    created_at TEXT
)
""")


# ============================================================
# SUBMISSIONS
# ============================================================

cur.execute("""
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    club TEXT NOT NULL,
    submission_type TEXT NOT NULL,
    text TEXT,
    photo_id TEXT,
    caption TEXT,
    status TEXT DEFAULT 'sent',
    created_at TEXT
)
""")


# ============================================================
# МИГРАЦИЯ СТАРОЙ БАЗЫ
# ============================================================

def ensure_column(
    table_name,
    column_name,
    definition
):
    columns = cur.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()

    existing = [
        row["name"]
        for row in columns
    ]

    if column_name not in existing:
        cur.execute(
            f"ALTER TABLE {table_name} "
            f"ADD COLUMN {column_name} {definition}"
        )


ensure_column(
    "withdrawal_players",
    "club_number",
    "INTEGER DEFAULT 1"
)

ensure_column(
    "withdrawal_players",
    "stage",
    "TEXT DEFAULT 'pending'"
)

ensure_column(
    "withdrawal_players",
    "accepted_at",
    "TEXT"
)

ensure_column(
    "withdrawal_players",
    "vip_sent_at",
    "TEXT"
)

ensure_column(
    "withdrawal_players",
    "result_deadline",
    "TEXT"
)

ensure_column(
    "withdrawal_players",
    "result_sent",
    "INTEGER DEFAULT 0"
)

ensure_column(
    "withdrawal_players",
    "tp_reason",
    "TEXT"
)


db.commit()


# ============================================================
# FSM STATES
# ============================================================

class UserState(StatesGroup):
    waiting_question = State()
    waiting_vip = State()
    waiting_result = State()


class AdminState(StatesGroup):
    waiting_schedule = State()
    waiting_result = State()
    waiting_mvp = State()
    waiting_withdrawal = State()
    waiting_post = State()
    waiting_question_answer = State()


# ============================================================
# HELPERS
# ============================================================

def now():
    return datetime.now()


def now_string():
    return now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def parse_datetime(value):
    if not value:
        return None

    try:
        return datetime.strptime(
            value,
            "%Y-%m-%d %H:%M:%S"
        )
    except Exception:
        return None


def safe(value):
    if value is None:
        return ""

    return escape(
        str(value)
    )


def get_user_name(user):
    if user.username:
        return f"@{user.username}"

    return (
        user.first_name
        or "Игрок"
    )


# ============================================================
# SAVE USER
# ============================================================

def save_user(message: Message):

    user = message.from_user

    cur.execute("""
        INSERT INTO users (
            user_id,
            username,
            first_name,
            created_at
        )
        VALUES (?, ?, ?, ?)

        ON CONFLICT(user_id)
        DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name
    """, (
        user.id,
        user.username,
        user.first_name,
        now_string()
    ))

    db.commit()


def save_callback_user(
    callback: CallbackQuery
):

    user = callback.from_user

    cur.execute("""
        INSERT INTO users (
            user_id,
            username,
            first_name,
            created_at
        )
        VALUES (?, ?, ?, ?)

        ON CONFLICT(user_id)
        DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name
    """, (
        user.id,
        user.username,
        user.first_name,
        now_string()
    ))

    db.commit()


# ============================================================
# ADMIN CHECK
# ============================================================

async def is_admin(
    user_id: int
) -> bool:

    if user_id in (
        CREATOR_ID,
        ADMIN_ID
    ):
        return True

    try:

        member = await bot.get_chat_member(
            ADMIN_GROUP_ID,
            user_id
        )

        return member.status in (
            "administrator",
            "creator"
        )

    except Exception as e:

        logging.warning(
            "Ошибка проверки админа %s: %s",
            user_id,
            e
        )

        return False


# ============================================================
# MAIN MENU
# ============================================================

def main_menu():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📅 Расписание",
                    callback_data="menu_schedule"
                ),
                InlineKeyboardButton(
                    text="📊 Результаты",
                    callback_data="menu_results"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏆 MVP",
                    callback_data="menu_mvp"
                ),
                InlineKeyboardButton(
                    text="❓ Задать вопрос",
                    callback_data="menu_question"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 Официальный канал",
                    url=OFFICIAL_CHANNEL_LINK
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔧 Админ-панель",
                    callback_data="menu_admin"
                )
            ]
        ]
    )


# ============================================================
# ADMIN MENU
# ============================================================

def admin_menu():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📢 Создать отписку",
                    callback_data="admin_withdrawal"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📅 Изменить расписание",
                    callback_data="admin_schedule"
                ),
                InlineKeyboardButton(
                    text="📊 Добавить результат",
                    callback_data="admin_result"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏆 Установить MVP",
                    callback_data="admin_mvp"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📣 Сделать пост",
                    callback_data="admin_post"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❓ Вопросы игроков",
                    callback_data="admin_questions"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="back_main"
                )
            ]
        ]
    )


# ============================================================
# ОТПИСКА — ДИЗАЙН
# ============================================================

def get_club_status(
    withdrawal_id,
    club_number
):

    row = cur.execute("""
        SELECT status
        FROM withdrawal_players
        WHERE withdrawal_id = ?
          AND club_number = ?
          AND status IN (
              'pending',
              'accepted',
              'tp'
          )
        ORDER BY id DESC
        LIMIT 1
    """, (
        withdrawal_id,
        club_number
    )).fetchone()

    if not row:
        return "free"

    if row["status"] == "accepted":
        return "accepted"

    if row["status"] == "tp":
        return "tp"

    return "pending"


def withdrawal_text(
    club1,
    club2,
    status1,
    status2
):

    symbol1 = "✅" if status1 == "accepted" else "❌"
    symbol2 = "✅" if status2 == "accepted" else "❌"

    return (
        "╭─────── ✦ MATCH ✦ ───────╮\n\n"
        f"⚽ <b>{safe(club1)}</b>   {symbol1} 🔑\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"⚽ <b>{safe(club2)}</b>   {symbol2}\n\n"
        "╰─────────────────────────╯"
    )


# ============================================================
# ОТПИСКА — КНОПКИ
# ============================================================

def withdrawal_keyboard(
    withdrawal_id,
    club1,
    club2
):

    status1 = get_club_status(
        withdrawal_id,
        1
    )

    status2 = get_club_status(
        withdrawal_id,
        2
    )

    buttons = []

    # --------------------------------------------------------
    # КЛУБ 1
    # --------------------------------------------------------

    if status1 == "accepted":

        buttons.append([
            InlineKeyboardButton(
                text=f"✅ {club1}",
                callback_data="already_done"
            )
        ])

    elif status1 == "tp":

        buttons.append([
            InlineKeyboardButton(
                text=f"⚠️ {club1} — ТП",
                callback_data="already_done"
            )
        ])

    else:

        buttons.append([
            InlineKeyboardButton(
                text=f"⚽ Отписаться — {club1}",
                callback_data=f"withdraw:{withdrawal_id}:1"
            )
        ])

    # --------------------------------------------------------
    # КЛУБ 2
    # --------------------------------------------------------

    if status2 == "accepted":

        buttons.append([
            InlineKeyboardButton(
                text=f"✅ {club2}",
                callback_data="already_done"
            )
        ])

    elif status2 == "tp":

        buttons.append([
            InlineKeyboardButton(
                text=f"⚠️ {club2} — ТП",
                callback_data="already_done"
            )
        ])

    else:

        buttons.append([
            InlineKeyboardButton(
                text=f"⚽ Отписаться — {club2}",
                callback_data=f"withdraw:{withdrawal_id}:2"
            )
        ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


# ============================================================
# ADMIN REQUEST BUTTONS
# ============================================================

def withdrawal_admin_keyboard(
    request_id
):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Принять",
                    callback_data=f"accept_withdraw:{request_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отказать",
                    callback_data=f"reject_withdraw:{request_id}"
                )
            ]
        ]
    )


def processed_withdrawal_keyboard(
    accepted
):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅" if accepted else "❌",
                    callback_data="already_done"
                )
            ]
        ]
    )


# ============================================================
# QUESTION BUTTONS
# ============================================================

def question_admin_keyboard(
    question_id
):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Ответить",
                    callback_data=f"answer_question:{question_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Закрыть",
                    callback_data=f"close_question:{question_id}"
                )
            ]
        ]
    )


# ============================================================
# SIMPLE PROCESSED
# ============================================================

@dp.callback_query(
    F.data == "already_done"
)
async def already_done(
    callback: CallbackQuery
):

    await callback.answer(
        "Уже обработано.",
        show_alert=True
    )


# ============================================================
# START
# ============================================================

@dp.message(
    Command("start")
)
async def start_handler(
    message: Message,
    state: FSMContext
):

    await state.clear()

    save_user(message)

    await message.answer(
        "✦ <b>SL27 LEAGUE</b> ✦\n\n"
        "Добро пожаловать!\n\n"
        "Выберите раздел:",
        reply_markup=main_menu()
    )


# ============================================================
# MYID
# ============================================================

@dp.message(
    Command("myid")
)
async def myid_handler(
    message: Message
):

    await message.answer(
        "🆔 <b>Ваш Telegram ID:</b>\n\n"
        f"<code>{message.from_user.id}</code>"
    )


# ============================================================
# ADMIN
# ============================================================

@dp.message(
    Command("admin")
)
async def admin_handler(
    message: Message
):

    if not await is_admin(
        message.from_user.id
    ):

        await message.answer(
            "❌ У вас нет доступа."
        )

        return

    await message.answer(
        "╭────── ✦ ADMIN ✦ ──────╮\n\n"
        "🔧 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
        "╰────────────────────────╯",
        reply_markup=admin_menu()
    )


# ============================================================
# CANCEL
# ============================================================

@dp.message(
    Command("cancel")
)
async def cancel_handler(
    message: Message,
    state: FSMContext
):

    await state.clear()

    await message.answer(
        "❌ Действие отменено.",
        reply_markup=main_menu()
    )


# ============================================================
# HELP
# ============================================================

@dp.message(
    Command("help")
)
async def help_handler(
    message: Message
):

    await message.answer(
        "ℹ️ <b>SL27 BOT</b>\n\n"
        "/start — главное меню\n"
        "/myid — ваш ID\n"
        "/admin — админ-панель\n"
        "/cancel — отменить действие\n"
        "/help — помощь"
    )


# ============================================================
# BACK MAIN
# ============================================================

@dp.callback_query(
    F.data == "back_main"
)
async def back_main(
    callback: CallbackQuery,
    state: FSMContext
):

    await state.clear()

    await callback.message.edit_text(
        "✦ <b>SL27 LEAGUE</b> ✦\n\n"
        "Выберите раздел:",
        reply_markup=main_menu()
    )

    await callback.answer()


# ============================================================
# ADMIN MENU
# ============================================================

@dp.callback_query(
    F.data == "menu_admin"
)
async def menu_admin(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "❌ Нет доступа.",
            show_alert=True
        )

        return

    await callback.message.edit_text(
        "╭────── ✦ ADMIN ✦ ──────╮\n\n"
        "🔧 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
        "╰────────────────────────╯",
        reply_markup=admin_menu()
    )

    await callback.answer()


# ============================================================
# SCHEDULE VIEW
# ============================================================

@dp.callback_query(
    F.data == "menu_schedule"
)
async def menu_schedule(
    callback: CallbackQuery
):

    row = cur.execute("""
        SELECT text
        FROM schedules
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()

    if row:

        text = (
            "╭──── ✦ <b>SCHEDULE</b> ✦ ────╮\n\n"
            f"{safe(row['text'])}\n\n"
            "╰──────────────────────────╯"
        )

    else:

        text = (
            "📅 <b>РАСПИСАНИЕ</b>\n\n"
            "Расписание пока отсутствует."
        )

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Назад",
                        callback_data="back_main"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# ADMIN SCHEDULE
# ============================================================

@dp.callback_query(
    F.data == "admin_schedule"
)
async def admin_schedule_start(
    callback: CallbackQuery,
    state: FSMContext
):

    if not await is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "❌ Нет доступа.",
            show_alert=True
        )

        return

    await state.set_state(
        AdminState.waiting_schedule
    )

    await callback.message.answer(
        "📅 Отправьте новое расписание.\n\n"
        "Старое будет заменено.\n\n"
        "/cancel — отмена"
    )

    await callback.answer()


@dp.message(
    AdminState.waiting_schedule
)
async def admin_schedule_save(
    message: Message,
    state: FSMContext
):

    if not await is_admin(
        message.from_user.id
    ):

        await state.clear()
        return

    if not message.text:

        await message.answer(
            "❌ Отправьте расписание текстом."
        )

        return

    cur.execute(
        "DELETE FROM schedules"
    )

    cur.execute("""
        INSERT INTO schedules (
            text,
            created_at
        )
        VALUES (?, ?)
    """, (
        message.text,
        now_string()
    ))

    db.commit()

    await state.clear()

    await message.answer(
        "✅ Расписание обновлено.",
        reply_markup=admin_menu()
    )


# ============================================================
# RESULTS VIEW
# ============================================================

@dp.callback_query(
    F.data == "menu_results"
)
async def menu_results(
    callback: CallbackQuery
):

    rows = cur.execute("""
        SELECT text
        FROM results
        ORDER BY id DESC
        LIMIT 10
    """).fetchall()

    if not rows:

        text = (
            "╭──── ✦ <b>RESULTS</b> ✦ ────╮\n\n"
            "Результатов пока нет.\n\n"
            "╰─────────────────────────╯"
        )

    else:

        parts = [
            "╭──── ✦ <b>RESULTS</b> ✦ ────╮",
            ""
        ]

        for row in rows:

            parts.append(
                f"⚽ {safe(row['text'])}"
            )

        parts.extend([
            "",
            "╰─────────────────────────╯"
        ])

        text = "\n".join(parts)

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Назад",
                        callback_data="back_main"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# ADMIN RESULT
# ============================================================

@dp.callback_query(
    F.data == "admin_result"
)
async def admin_result_start(
    callback: CallbackQuery,
    state: FSMContext
):

    if not await is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "❌ Нет доступа.",
            show_alert=True
        )

        return

    await state.set_state(
        AdminState.waiting_result
    )

    await callback.message.answer(
        "📊 Отправьте результат матча текстом.\n\n"
        "Например:\n"
        "<code>Al_Nassr 3:1 Netherlands</code>\n\n"
        "/cancel — отмена"
    )

    await callback.answer()


@dp.message(
    AdminState.waiting_result
)
async def admin_result_save(
    message: Message,
    state: FSMContext
):

    if not await is_admin(
        message.from_user.id
    ):

        await state.clear()
        return

    if not message.text:

        await message.answer(
            "❌ Отправьте результат текстом."
        )

        return

    result = message.text.strip()

    cur.execute("""
        INSERT INTO results (
            text,
            created_at
        )
        VALUES (?, ?)
    """, (
        result,
        now_string()
    ))

    db.commit()

    try:

        await bot.send_message(
            OFFICIAL_CHANNEL_ID,
            (
                "╭──── ✦ <b>RESULT</b> ✦ ────╮\n\n"
                f"⚽ <b>{safe(result)}</b>\n\n"
                "╰─────────────────────────╯"
            )
        )

        published = True

    except Exception as e:

        logging.error(
            "Ошибка публикации результата: %s",
            e
        )

        published = False

    await state.clear()

    await message.answer(
        "✅ Результат опубликован."
        if published
        else
        "✅ Результат сохранён, но в канал не отправлен.",
        reply_markup=admin_menu()
    )


# ============================================================
# MVP VIEW
# ============================================================

@dp.callback_query(
    F.data == "menu_mvp"
)
async def menu_mvp(
    callback: CallbackQuery
):

    row = cur.execute("""
        SELECT text, photo_id
        FROM mvps
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()

    back = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data="back_main"
                )
            ]
        ]
    )

    if not row:

        await callback.message.edit_text(
            "🏆 <b>MVP</b>\n\n"
            "MVP пока не установлен.",
            reply_markup=back
        )

        await callback.answer()
        return

    caption = (
        "🏆 <b>MVP SL27</b>\n\n"
        f"{safe(row['text'] or '')}"
    )

    if row["photo_id"]:

        await callback.message.answer_photo(
            row["photo_id"],
            caption=caption,
            reply_markup=back
        )

        try:
            await callback.message.delete()
        except Exception:
            pass

    else:

        await callback.message.edit_text(
            caption,
            reply_markup=back
        )

    await callback.answer()


# ============================================================
# ADMIN MVP
# ============================================================

@dp.callback_query(
    F.data == "admin_mvp"
)
async def admin_mvp_start(
    callback: CallbackQuery,
    state: FSMContext
):

    if not await is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "❌ Нет доступа.",
            show_alert=True
        )

        return

    await state.set_state(
        AdminState.waiting_mvp
    )

    await callback.message.answer(
        "🏆 Отправьте MVP текстом или фотографией.\n\n"
        "/cancel — отмена"
    )

    await callback.answer()


@dp.message(
    AdminState.waiting_mvp,
    F.photo
)
async def admin_mvp_photo(
    message: Message,
    state: FSMContext
):

    if not await is_admin(
        message.from_user.id
    ):

        await state.clear()
        return

    photo_id = message.photo[-1].file_id

    caption = (
        message.caption
        or
        "MVP SL27"
    )

    cur.execute(
        "DELETE FROM mvps"
    )

    cur.execute("""
        INSERT INTO mvps (
            text,
            photo_id,
            created_at
        )
        VALUES (?, ?, ?)
    """, (
        caption,
        photo_id,
        now_string()
    ))

    db.commit()

    try:

        await bot.send_photo(
            OFFICIAL_CHANNEL_ID,
            photo_id,
            caption=(
                "╭──── ✦ <b>MVP</b> ✦ ────╮\n\n"
                f"🏆 <b>{safe(caption)}</b>\n\n"
                "╰─────────────────────────╯"
            )
        )

        published = True

    except Exception:

        published = False

    await state.clear()

    await message.answer(
        "✅ MVP опубликован."
        if published
        else
        "✅ MVP сохранён.",
        reply_markup=admin_menu()
    )


@dp.message(
    AdminState.waiting_mvp
)
async def admin_mvp_text(
    message: Message,
    state: FSMContext
):

    if not await is_admin(
        message.from_user.id
    ):

        await state.clear()
        return

    if not message.text:

        await message.answer(
            "❌ Отправьте текст или фотографию."
        )

        return

    value = message.text.strip()

    cur.execute(
        "DELETE FROM mvps"
    )

    cur.execute("""
        INSERT INTO mvps (
            text,
            photo_id,
            created_at
        )
        VALUES (?, NULL, ?)
    """, (
        value,
        now_string()
    ))

    db.commit()

    try:

        await bot.send_message(
            OFFICIAL_CHANNEL_ID,
            (
                "╭──── ✦ <b>MVP</b> ✦ ────╮\n\n"
                f"🏆 <b>{safe(value)}</b>\n\n"
                "╰─────────────────────────╯"
            )
        )

        published = True

    except Exception:

        published = False

    await state.clear()

    await message.answer(
        "✅ MVP опубликован."
        if published
        else
        "✅ MVP сохранён.",
        reply_markup=admin_menu()
    )


# ============================================================
# PLAYER QUESTION
# ============================================================

@dp.callback_query(
    F.data == "menu_question"
)
async def menu_question(
    callback: CallbackQuery,
    state: FSMContext
):

    await state.set_state(
        UserState.waiting_question
    )

    await callback.message.answer(
        "❓ <b>ВОПРОС АДМИНИСТРАЦИИ</b>\n\n"
        "Напишите вопрос одним сообщением.\n\n"
        "/cancel — отмена"
    )

    await callback.answer()


@dp.message(
    UserState.waiting_question
)
async def receive_question(
    message: Message,
    state: FSMContext
):

    save_user(message)

    if not message.text:

        await message.answer(
            "❌ Отправьте вопрос текстом."
        )

        return

    cur.execute("""
        INSERT INTO questions (
            user_id,
            username,
            question,
            status,
            created_at
        )
        VALUES (?, ?, ?, 'pending', ?)
    """, (
        message.from_user.id,
        message.from_user.username,
        message.text,
        now_string()
    ))

    question_id = cur.lastrowid

    db.commit()

    name = get_user_name(
        message.from_user
    )

    text = (
        "╭──── ✦ <b>QUESTION</b> ✦ ────╮\n\n"
        f"👤 {safe(name)}\n"
        f"🆔 <code>{message.from_user.id}</code>\n\n"
        f"💬 {safe(message.text)}\n\n"
        "╰────────────────────────────╯"
    )

    try:

        await bot.send_message(
            ADMIN_GROUP_ID,
            text,
            reply_markup=question_admin_keyboard(
                question_id
            )
        )

        await message.answer(
            "✅ Вопрос отправлен администраторам.",
            reply_markup=main_menu()
        )

    except Exception as e:

        logging.error(
            "Question error: %s",
            e
        )

        await message.answer(
            "⚠️ Не удалось отправить вопрос."
        )

    await state.clear()


# ============================================================
# ADMIN QUESTIONS
# ============================================================

@dp.callback_query(
    F.data == "admin_questions"
)
async def admin_questions(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "❌ Нет доступа.",
            show_alert=True
        )

        return

    rows = cur.execute("""
        SELECT
            id,
            user_id,
            username,
            question,
            created_at
        FROM questions
        WHERE status = 'pending'
        ORDER BY id ASC
        LIMIT 20
    """).fetchall()

    if not rows:

        await callback.message.answer(
            "📭 Новых вопросов нет."
        )

        await callback.answer()
        return

    for row in rows:

        name = (
            f"@{row['username']}"
            if row["username"]
            else "без username"
        )

        await callback.message.answer(
            (
                "╭──── ✦ <b>QUESTION</b> ✦ ────╮\n\n"
                f"👤 {safe(name)}\n"
                f"🆔 <code>{row['user_id']}</code>\n\n"
                f"💬 {safe(row['question'])}\n\n"
                f"🕐 {row['created_at']}\n\n"
                "╰────────────────────────────╯"
            ),
            reply_markup=question_admin_keyboard(
                row["id"]
            )
        )

    await callback.answer()


# ============================================================
# ANSWER QUESTION
# ============================================================

@dp.callback_query(
    F.data.startswith("answer_question:")
)
async def answer_question_start(
    callback: CallbackQuery,
    state: FSMContext
):

    if not await is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "❌ Нет доступа.",
            show_alert=True
        )

        return

    try:

        question_id = int(
            callback.data.split(":")[1]
        )

    except Exception:

        await callback.answer(
            "❌ Ошибка вопроса.",
            show_alert=True
        )

        return

    row = cur.execute("""
        SELECT status
        FROM questions
        WHERE id = ?
    """, (
        question_id,
    )).fetchone()

    if not row or row["status"] != "pending":

        await callback.answer(
            "⚠️ Вопрос уже обработан.",
            show_alert=True
        )

        return

    await state.set_state(
        AdminState.waiting_question_answer
    )

    await state.update_data(
        question_id=question_id
    )

    await callback.message.answer(
        "💬 Напишите ответ игроку.\n\n"
        "/cancel — отмена"
    )

    await callback.answer()


@dp.message(
    AdminState.waiting_question_answer
)
async def send_question_answer(
    message: Message,
    state: FSMContext
):

    if not await is_admin(
        message.from_user.id
    ):

        await state.clear()
        return

    if not message.text:

        await message.answer(
            "❌ Ответ должен быть текстом."
        )

        return

    data = await state.get_data()

    question_id = data.get(
        "question_id"
    )

    row = cur.execute("""
        SELECT user_id
        FROM questions
        WHERE id = ?
          AND status = 'pending'
    """, (
        question_id,
    )).fetchone()

    if not row:

        await state.clear()

        await message.answer(
            "❌ Вопрос уже обработан."
        )

        return

    try:

        await bot.send_message(
            row["user_id"],
            (
                "╭──── ✦ <b>ADMIN</b> ✦ ────╮\n\n"
                f"💬 {safe(message.text)}\n\n"
                "╰─────────────────────────╯"
            )
        )

    except Exception as e:

        await message.answer(
            f"⚠️ Не удалось отправить ответ:\n{safe(e)}"
        )

        await state.clear()
        return

    cur.execute("""
        UPDATE questions
        SET
            answer = ?,
            status = 'answered'
        WHERE id = ?
    """, (
        message.text,
        question_id
    ))

    db.commit()

    await state.clear()

    await message.answer(
        "✅ Ответ отправлен.",
        reply_markup=admin_menu()
    )


# ============================================================
# CLOSE QUESTION
# ============================================================

@dp.callback_query(
    F.data.startswith("close_question:")
)
async def close_question(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "❌ Нет доступа.",
            show_alert=True
        )

        return

    try:

        question_id = int(
            callback.data.split(":")[1]
        )

    except Exception:

        await callback.answer(
            "❌ Ошибка.",
            show_alert=True
        )

        return

    cur.execute("""
        UPDATE questions
        SET status = 'closed'
        WHERE id = ?
    """, (
        question_id,
    ))

    db.commit()

    try:

        await callback.message.edit_reply_markup(
            reply_markup=None
        )

    except Exception:
        pass

    await callback.answer(
        "✅ Закрыто."
    )


# ============================================================
# CREATE WITHDRAWAL
# ============================================================

@dp.callback_query(
    F.data == "admin_withdrawal"
)
async def admin_withdrawal_start(
    callback: CallbackQuery,
    state: FSMContext
):

    if not await is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "❌ Нет доступа.",
            show_alert=True
        )

        return

    await state.set_state(
        AdminState.waiting_withdrawal
    )

    await callback.message.answer(
        "📢 <b>СОЗДАНИЕ ОТПИСКИ</b>\n\n"
        "Введите два клуба через <code>|</code>.\n\n"
        "Пример:\n"
        "<code>Al_Nassr|Netherlands</code>\n\n"
        "Первый клуб получает 🔑 VIP.\n"
        "Оба клуба должны отправить результат.\n\n"
        f"⏱ На отписку: {WITHDRAWAL_TIMEOUT_MINUTES} минут\n"
        f"⏱ На результат: {RESULT_TIMEOUT_HOURS} часов\n\n"
        "/cancel — отмена"
    )

    await callback.answer()


@dp.message(
    AdminState.waiting_withdrawal
)
async def admin_withdrawal_create(
    message: Message,
    state: FSMContext
):

    if not await is_admin(
        message.from_user.id
    ):

        await state.clear()
        return

    if not message.text or "|" not in message.text:

        await message.answer(
            "❌ Формат:\n"
            "<code>Al_Nassr|Netherlands</code>"
        )

        return

    parts = [
        x.strip()
        for x in message.text.split("|")
        if x.strip()
    ]

    if len(parts) != 2:

        await message.answer(
            "❌ Нужно ровно два клуба."
        )

        return

    club1 = parts[0]
    club2 = parts[1]

    created = now_string()

    cur.execute("""
        INSERT INTO withdrawals (
            club1,
            club2,
            status,
            created_at
        )
        VALUES (?, ?, 'open', ?)
    """, (
        club1,
        club2,
        created
    ))

    withdrawal_id = cur.lastrowid

    db.commit()

    text = withdrawal_text(
        club1,
        club2,
        "free",
        "free"
    )

    try:

        sent = await bot.send_message(
            OFFICIAL_CHANNEL_ID,
            text,
            reply_markup=withdrawal_keyboard(
                withdrawal_id,
                club1,
                club2
            )
        )

        cur.execute("""
            UPDATE withdrawals
            SET channel_message_id = ?
            WHERE id = ?
        """, (
            sent.message_id,
            withdrawal_id
        ))

        db.commit()

        await message.answer(
            "✅ Отписка опубликована.",
            reply_markup=admin_menu()
        )

    except Exception as e:

        cur.execute("""
            UPDATE withdrawals
            SET status = 'error'
            WHERE id = ?
        """, (
            withdrawal_id,
        ))

        db.commit()

        await message.answer(
            f"❌ Не удалось опубликовать:\n{safe(e)}"
        )

    await state.clear()


# ============================================================
# PLAYER CHOOSES CLUB
#
# ЛЮБОЙ ИГРОК МОЖЕТ НАЖАТЬ,
# ЕСЛИ ЕГО ПРЕДЫДУЩУЮ ЗАЯВКУ ОТКЛОНИЛИ.
#
# Если клуб уже ПРИНЯТ — больше нажать нельзя.
# ============================================================

@dp.callback_query(
    F.data.startswith("withdraw:")
)
async def player_choose_withdrawal(
    callback: CallbackQuery
):

    save_callback_user(
        callback
    )

    parts = callback.data.split(":")

    if len(parts) != 3:

        await callback.answer(
            "❌ Ошибка.",
            show_alert=True
        )

        return

    try:

        withdrawal_id = int(
            parts[1]
        )

        club_number = int(
            parts[2]
        )

    except Exception:

        await callback.answer(
            "❌ Ошибка заявки.",
            show_alert=True
        )

        return

    withdrawal = cur.execute("""
        SELECT
            id,
            club1,
            club2,
            status,
            created_at,
            channel_message_id
        FROM withdrawals
        WHERE id = ?
    """, (
        withdrawal_id,
    )).fetchone()

    if not withdrawal:

        await callback.answer(
            "❌ Отписка не найдена.",
            show_alert=True
        )

        return

    if withdrawal["status"] != "open":

        await callback.answer(
            "❌ Эта отписка уже закрыта.",
            show_alert=True
        )

        return

    # --------------------------------------------------------
    # Проверяем 10 минут
    # --------------------------------------------------------

    created_at = parse_datetime(
        withdrawal["created_at"]
    )

    if created_at:

        deadline = (
            created_at
            + timedelta(
                minutes=WITHDRAWAL_TIMEOUT_MINUTES
            )
        )

        if now() >= deadline:

            await callback.answer(
                "⏱ Время на отписку уже закончилось.",
                show_alert=True
            )

            return

    if club_number == 1:

        club = withdrawal["club1"]

    elif club_number == 2:

        club = withdrawal["club2"]

    else:

        await callback.answer(
            "❌ Неверный клуб.",
            show_alert=True
        )

        return

    # --------------------------------------------------------
    # ЕСЛИ КЛУБ УЖЕ ПРИНЯТ
    # --------------------------------------------------------

    accepted = cur.execute("""
        SELECT id
        FROM withdrawal_players
        WHERE withdrawal_id = ?
          AND club_number = ?
          AND status = 'accepted'
        LIMIT 1
    """, (
        withdrawal_id,
        club_number
    )).fetchone()

    if accepted:

        await callback.answer(
            "❌ Этот клуб уже принят за игроком.",
            show_alert=True
        )

        return

    # --------------------------------------------------------
    # ВАЖНО:
    # НЕ ЗАПРЕЩАЕМ ИГРОКУ ПОВТОРНО ПОДАТЬ ЗАЯВКУ,
    # ЕСЛИ ПРЕДЫДУЩУЮ ОТКЛОНИЛИ.
    # --------------------------------------------------------

    pending_same_user = cur.execute("""
        SELECT id
        FROM withdrawal_players
        WHERE withdrawal_id = ?
          AND user_id = ?
          AND status = 'pending'
        LIMIT 1
    """, (
        withdrawal_id,
        callback.from_user.id
    )).fetchone()

    if pending_same_user:

        await callback.answer(
            "⏳ Ваша заявка уже рассматривается.",
            show_alert=True
        )

        return

    # --------------------------------------------------------
    # ЕСЛИ ЭТОТ ИГРОК БЫЛ REJECTED — МОЖЕТ НАЖАТЬ СНОВА
    # --------------------------------------------------------

    username = callback.from_user.username

    cur.execute("""
        INSERT INTO withdrawal_players (
            withdrawal_id,
            user_id,
            username,
            club,
            club_number,
            status,
            stage,
            created_at
        )
        VALUES (
            ?, ?, ?, ?, ?,
            'pending',
            'pending',
            ?
        )
    """, (
        withdrawal_id,
        callback.from_user.id,
        username,
        club,
        club_number,
        now_string()
    ))

    request_id = cur.lastrowid

    db.commit()

    player_name = get_user_name(
        callback.from_user
    )

    admin_text = (
        "╭──── ✦ <b>ОТПИСКА</b> ✦ ────╮\n\n"
        f"👤 Игрок: <b>{safe(player_name)}</b>\n"
        f"🆔 <code>{callback.from_user.id}</code>\n"
        f"⚽ Клуб: <b>{safe(club)}</b>\n"
        f"🔢 Клуб №{club_number}\n\n"
        "Игрок хочет отписаться.\n\n"
        "╰───────────────────────────╯"
    )

    try:

        await bot.send_message(
            ADMIN_GROUP_ID,
            admin_text,
            reply_markup=withdrawal_admin_keyboard(
                request_id
            )
        )

        await callback.answer(
            "✅ Заявка отправлена администраторам.",
            show_alert=True
        )

    except Exception as e:

        logging.error(
            "Ошибка отправки заявки: %s",
            e
        )

        await callback.answer(
            "⚠️ Не удалось отправить заявку.",
            show_alert=True
        )


# ============================================================
# ACCEPT WITHDRAWAL
# ============================================================

@dp.callback_query(
    F.data.startswith("accept_withdraw:")
)
async def accept_withdraw(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "❌ Нет доступа.",
            show_alert=True
        )

        return

    try:

        request_id = int(
            callback.data.split(":")[1]
        )

    except Exception:

        await callback.answer(
            "❌ Ошибка.",
            show_alert=True
        )

        return

    request = cur.execute("""
        SELECT
            wp.*,
            w.club1,
            w.club2,
            w.channel_message_id,
            w.status AS withdrawal_status
        FROM withdrawal_players wp
        JOIN withdrawals w
            ON w.id = wp.withdrawal_id
        WHERE wp.id = ?
    """, (
        request_id,
    )).fetchone()

    if not request:

        await callback.answer(
            "❌ Заявка не найдена.",
            show_alert=True
        )

        return

    if request["status"] != "pending":

        await callback.answer(
            "⚠️ Заявка уже обработана.",
            show_alert=True
        )

        return

    # --------------------------------------------------------
    # ЕСЛИ КЛУБ УЖЕ ПРИНЯЛИ ЗА ДРУГИМ
    # --------------------------------------------------------

    accepted_same_club = cur.execute("""
        SELECT id
        FROM withdrawal_players
        WHERE withdrawal_id = ?
          AND club_number = ?
          AND status = 'accepted'
          AND id != ?
        LIMIT 1
    """, (
        request["withdrawal_id"],
        request["club_number"],
        request_id
    )).fetchone()

    if accepted_same_club:

        cur.execute("""
            UPDATE withdrawal_players
            SET
                status = 'rejected',
                stage = 'rejected'
            WHERE id = ?
        """, (
            request_id,
        ))

        db.commit()

        try:

            await bot.send_message(
                request["user_id"],
                (
                    "❌ <b>Ваша заявка отклонена.</b>\n\n"
                    f"⚽ Клуб: <b>{safe(request['club'])}</b>\n\n"
                    "Клуб уже был принят за другим игроком."
                )
            )

        except Exception:
            pass

        try:

            await callback.message.edit_reply_markup(
                reply_markup=processed_withdrawal_keyboard(
                    False
                )
            )

        except Exception:
            pass

        await callback.answer(
            "❌ Клуб уже занят.",
            show_alert=True
        )

        return

    # --------------------------------------------------------
    # ПРИНИМАЕМ ЭТОГО ИГРОКА
    # --------------------------------------------------------

    accepted_at = now()

    # --------------------------------------------------------
    # КЛУБ 1 -> VIP
    # --------------------------------------------------------

    if request["club_number"] == 1:

        stage = "waiting_vip"

        result_deadline = None

        player_message = (
            "✅ <b>ОТПИСКА ПРИНЯТА</b>\n\n"
            f"⚽ Клуб: <b>{safe(request['club'])}</b>\n\n"
            "🔑 Отправьте VIP-ключ для игры.\n\n"
            "Отправьте его обычным текстом."
        )

    # --------------------------------------------------------
    # КЛУБ 2 -> СРАЗУ RESULT
    # --------------------------------------------------------

    else:

        stage = "waiting_result"

        result_deadline = (
            accepted_at
            + timedelta(
                hours=RESULT_TIMEOUT_HOURS
            )
        ).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        player_message = (
            "✅ <b>ОТПИСКА ПРИНЯТА</b>\n\n"
            f"⚽ Клуб: <b>{safe(request['club'])}</b>\n\n"
            "🖼 Отправьте результат матча фотографией.\n\n"
            f"⏱ У вас есть {RESULT_TIMEOUT_HOURS} часов."
        )

    cur.execute("""
        UPDATE withdrawal_players
        SET
            status = 'accepted',
            stage = ?,
            accepted_at = ?,
            result_deadline = ?
        WHERE id = ?
    """, (
        stage,
        accepted_at.strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        result_deadline,
        request_id
    ))

    # --------------------------------------------------------
    # ОТКЛОНЯЕМ ОСТАЛЬНЫЕ PENDING ЗАЯВКИ
    # ЭТОГО ЖЕ КЛУБА
    # --------------------------------------------------------

    others = cur.execute("""
        SELECT
            id,
            user_id
        FROM withdrawal_players
        WHERE withdrawal_id = ?
          AND club_number = ?
          AND status = 'pending'
          AND id != ?
    """, (
        request["withdrawal_id"],
        request["club_number"],
        request_id
    )).fetchall()

    for other in others:

        cur.execute("""
            UPDATE withdrawal_players
            SET
                status = 'rejected',
                stage = 'rejected'
            WHERE id = ?
        """, (
            other["id"],
        ))

        try:

            await bot.send_message(
                other["user_id"],
                (
                    "❌ <b>Ваша заявка не принята.</b>\n\n"
                    f"⚽ Клуб: <b>{safe(request['club'])}</b>\n\n"
                    "Другой игрок уже был принят на этот клуб."
                )
            )

        except Exception:
            pass

    db.commit()

    # --------------------------------------------------------
    # ОБНОВЛЯЕМ КАНАЛ
    # --------------------------------------------------------

    status1 = get_club_status(
        request["withdrawal_id"],
        1
    )

    status2 = get_club_status(
        request["withdrawal_id"],
        2
    )

    if request["channel_message_id"]:

        try:

            await bot.edit_message_text(
                chat_id=OFFICIAL_CHANNEL_ID,
                message_id=request["channel_message_id"],
                text=withdrawal_text(
                    request["club1"],
                    request["club2"],
                    status1,
                    status2
                ),
                reply_markup=withdrawal_keyboard(
                    request["withdrawal_id"],
                    request["club1"],
                    request["club2"]
                )
            )

        except Exception as e:

            logging.warning(
                "Ошибка обновления отписки в канале: %s",
                e
            )

    # --------------------------------------------------------
    # В АДМИН-ГРУППЕ
    # ❌/2 КНОПКИ -> ✅
    # --------------------------------------------------------

    try:

        await callback.message.edit_reply_markup(
            reply_markup=processed_withdrawal_keyboard(
                True
            )
        )

    except Exception:
        pass

    # --------------------------------------------------------
    # ЛС
    # --------------------------------------------------------

    try:

        await bot.send_message(
            request["user_id"],
            player_message
        )

    except Exception as e:

        logging.warning(
            "Ошибка сообщения игроку: %s",
            e
        )

    await callback.answer(
        "✅ Заявка принята."
    )


# ============================================================
# REJECT WITHDRAWAL
# ============================================================

@dp.callback_query(
    F.data.startswith("reject_withdraw:")
)
async def reject_withdraw(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "❌ Нет доступа.",
            show_alert=True
        )

        return

    try:

        request_id = int(
            callback.data.split(":")[1]
        )

    except Exception:

        await callback.answer(
            "❌ Ошибка.",
            show_alert=True
        )

        return

    request = cur.execute("""
        SELECT
            id,
            user_id,
            club,
            withdrawal_id,
            status
        FROM withdrawal_players
        WHERE id = ?
    """, (
        request_id,
    )).fetchone()

    if not request:

        await callback.answer(
            "❌ Заявка не найдена.",
            show_alert=True
        )

        return

    if request["status"] != "pending":

        await callback.answer(
            "⚠️ Уже обработано.",
            show_alert=True
        )

        return

    cur.execute("""
        UPDATE withdrawal_players
        SET
            status = 'rejected',
            stage = 'rejected'
        WHERE id = ?
    """, (
        request_id,
    ))

    db.commit()

    # --------------------------------------------------------
    # ОТКАЗ — ИГРОК ПОТОМ СМОЖЕТ НАЖАТЬ ОПЯТЬ
    # --------------------------------------------------------

    try:

        await bot.send_message(
            request["user_id"],
            (
                "❌ <b>ОТПИСКА ОТКЛОНЕНА</b>\n\n"
                f"⚽ Клуб: <b>{safe(request['club'])}</b>\n\n"
                "Вы можете снова нажать кнопку отписки."
            )
        )

    except Exception:
        pass

    # --------------------------------------------------------
    # В АДМИНКЕ ОСТАЁТСЯ ❌
    # --------------------------------------------------------

    try:

        await callback.message.edit_reply_markup(
            reply_markup=processed_withdrawal_keyboard(
                False
            )
        )

    except Exception:
        pass

    await callback.answer(
        "❌ Заявка отклонена."
    )


# ============================================================
# ACTIVE PLAYER
# ============================================================

def get_active_player(
    user_id
):

    return cur.execute("""
        SELECT
            wp.*,
            w.club1,
            w.club2
        FROM withdrawal_players wp
        JOIN withdrawals w
            ON w.id = wp.withdrawal_id
        WHERE wp.user_id = ?
          AND wp.status = 'accepted'
          AND wp.stage IN (
              'waiting_vip',
              'waiting_result'
          )
        ORDER BY wp.id DESC
        LIMIT 1
    """, (
        user_id,
    )).fetchone()


# ============================================================
# VIP
#
# Это STATE HANDLER.
# Поэтому обычные сообщения без состояния
# сюда НЕ ПОПАДАЮТ.
# ============================================================

@dp.message(
    UserState.waiting_vip
)
async def receive_vip(
    message: Message,
    state: FSMContext
):

    if not message.text:

        await message.answer(
            "❌ VIP нужно отправить текстом."
        )

        return

    player = get_active_player(
        message.from_user.id
    )

    if not player:

        await state.clear()

        return

    if player["stage"] != "waiting_vip":

        await state.clear()

        return

    # Только клуб 1
    if player["club_number"] != 1:

        await state.clear()

        return

    vip_text = message.text.strip()

    if not vip_text:

        await message.answer(
            "❌ VIP не может быть пустой."
        )

        return

    # Сохраняем VIP
    cur.execute("""
        INSERT INTO submissions (
            user_id,
            club,
            submission_type,
            text,
            photo_id,
            caption,
            status,
            created_at
        )
        VALUES (
            ?, ?, 'vip',
            ?, NULL, NULL, 'sent', ?
        )
    """, (
        message.from_user.id,
        player["club"],
        vip_text,
        now_string()
    ))

    # Теперь ждём результат 5 часов
    result_deadline = (
        now()
        + timedelta(
            hours=RESULT_TIMEOUT_HOURS
        )
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    cur.execute("""
        UPDATE withdrawal_players
        SET
            stage = 'waiting_result',
            vip_sent_at = ?,
            result_deadline = ?
        WHERE id = ?
    """, (
        now_string(),
        result_deadline,
        player["id"]
    ))

    db.commit()

    # --------------------------------------------------------
    # VIP В АДМИН-ГРУППУ
    # --------------------------------------------------------

    admin_text = (
        "╭────── ✦ <b>VIP</b> ✦ ──────╮\n\n"
        f"👤 Игрок: <b>{safe(get_user_name(message.from_user))}</b>\n"
        f"🆔 <code>{message.from_user.id}</code>\n"
        f"⚽ Клуб: <b>{safe(player['club'])}</b>\n\n"
        f"🔑 <b>VIP:</b>\n"
        f"{safe(vip_text)}\n\n"
        "╰───────────────────────────╯"
    )

    try:

        await bot.send_message(
            ADMIN_GROUP_ID,
            admin_text
        )

    except Exception as e:

        logging.error(
            "Ошибка отправки VIP в группу: %s",
            e
        )

    # --------------------------------------------------------
    # СЛЕДУЮЩИЙ ЭТАП
    # --------------------------------------------------------

    await state.clear()

    await message.answer(
        "✅ VIP отправлена администраторам.\n\n"
        "🖼 Теперь отправьте результат матча фотографией.\n\n"
        f"⏱ На результат: {RESULT_TIMEOUT_HOURS} часов."
    )


# ============================================================
# RESULT
# ============================================================

@dp.message(
    UserState.waiting_result,
    F.photo
)
async def receive_result_photo(
    message: Message,
    state: FSMContext
):

    player = get_active_player(
        message.from_user.id
    )

    if not player:

        await state.clear()
        return

    if player["stage"] != "waiting_result":

        await state.clear()
        return

    photo_id = message.photo[-1].file_id

    caption = (
        message.caption
        or ""
    )

    # Сохраняем result
    cur.execute("""
        INSERT INTO submissions (
            user_id,
            club,
            submission_type,
            text,
            photo_id,
            caption,
            status,
            created_at
        )
        VALUES (
            ?, ?, 'result',
            NULL, ?, ?, 'sent', ?
        )
    """, (
        message.from_user.id,
        player["club"],
        photo_id,
        caption,
        now_string()
    ))

    # Результат получен
    cur.execute("""
        UPDATE withdrawal_players
        SET
            stage = 'completed',
            result_sent = 1,
            result_deadline = NULL
        WHERE id = ?
    """, (
        player["id"],
    ))

    db.commit()

    # --------------------------------------------------------
    # RESULT В АДМИН-ГРУППУ
    # --------------------------------------------------------

    admin_caption = (
        "╭──── ✦ <b>RESULT</b> ✦ ────╮\n\n"
        f"👤 Игрок: <b>{safe(get_user_name(message.from_user))}</b>\n"
        f"🆔 <code>{message.from_user.id}</code>\n"
        f"⚽ Клуб: <b>{safe(player['club'])}</b>\n"
    )

    if caption:

        admin_caption += (
            f"\n📝 {safe(caption)}\n"
        )

    admin_caption += (
        "\n╰───────────────────────────╯"
    )

    try:

        await bot.send_photo(
            ADMIN_GROUP_ID,
            photo_id,
            caption=admin_caption
        )

    except Exception as e:

        logging.error(
            "Ошибка отправки результата в группу: %s",
            e
        )

    # Если есть подпись — сохраним её
    if caption:

        cur.execute("""
            INSERT INTO results (
                text,
                created_at
            )
            VALUES (?, ?)
        """, (
            caption,
            now_string()
        ))

        db.commit()

    await state.clear()

    await message.answer(
        "✅ Результат отправлен администраторам."
    )


# ============================================================
# RESULT, НО НЕ ФОТО
# ============================================================

@dp.message(
    UserState.waiting_result
)
async def result_need_photo(
    message: Message
):

    await message.answer(
        "❌ Результат нужно отправить фотографией."
    )


# ============================================================
# ADMIN POST
# ============================================================

@dp.callback_query(
    F.data == "admin_post"
)
async def admin_post_start(
    callback: CallbackQuery,
    state: FSMContext
):

    if not await is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "❌ Нет доступа.",
            show_alert=True
        )

        return

    await state.set_state(
        AdminState.waiting_post
    )

    await callback.message.answer(
        "📣 Отправьте текст поста.\n\n"
        "/cancel — отмена"
    )

    await callback.answer()


@dp.message(
    AdminState.waiting_post
)
async def admin_post_send(
    message: Message,
    state: FSMContext
):

    if not await is_admin(
        message.from_user.id
    ):

        await state.clear()
        return

    if not message.text:

        await message.answer(
            "❌ Отправьте текст."
        )

        return

    try:

        await bot.send_message(
            OFFICIAL_CHANNEL_ID,
            message.text
        )

        await message.answer(
            "✅ Пост опубликован.",
            reply_markup=admin_menu()
        )

    except Exception as e:

        await message.answer(
            f"❌ Ошибка:\n{safe(e)}"
        )

    await state.clear()


# ============================================================
# 10 МИНУТ — ПРОВЕРКА ОТПИСОК
# ============================================================

async def check_withdrawal_timeouts():

    rows = cur.execute("""
        SELECT
            id,
            club1,
            club2,
            channel_message_id,
            created_at,
            status
        FROM withdrawals
        WHERE status = 'open'
    """).fetchall()

    for withdrawal in rows:

        created_at = parse_datetime(
            withdrawal["created_at"]
        )

        if not created_at:
            continue

        deadline = (
            created_at
            + timedelta(
                minutes=WITHDRAWAL_TIMEOUT_MINUTES
            )
        )

        if now() < deadline:
            continue

        tp_clubs = []

        # ----------------------------------------------------
        # CLUB 1
        # ----------------------------------------------------

        club1_accepted = cur.execute("""
            SELECT id
            FROM withdrawal_players
            WHERE withdrawal_id = ?
              AND club_number = 1
              AND status = 'accepted'
            LIMIT 1
        """, (
            withdrawal["id"],
        )).fetchone()

        if not club1_accepted:

            # Если уже есть TP, не создаём второй
            club1_tp = cur.execute("""
                SELECT id
                FROM withdrawal_players
                WHERE withdrawal_id = ?
                  AND club_number = 1
                  AND status = 'tp'
                LIMIT 1
            """, (
                withdrawal["id"],
            )).fetchone()

            if not club1_tp:

                cur.execute("""
                    INSERT INTO withdrawal_players (
                        withdrawal_id,
                        user_id,
                        username,
                        club,
                        club_number,
                        status,
                        stage,
                        tp_reason,
                        created_at
                    )
                    VALUES (
                        ?, 0, NULL, ?, 1,
                        'tp',
                        'tp',
                        'withdrawal_timeout',
                        ?
                    )
                """, (
                    withdrawal["id"],
                    withdrawal["club1"],
                    now_string()
                ))

                tp_clubs.append(
                    withdrawal["club1"]
                )

        # ----------------------------------------------------
        # CLUB 2
        # ----------------------------------------------------

        club2_accepted = cur.execute("""
            SELECT id
            FROM withdrawal_players
            WHERE withdrawal_id = ?
              AND club_number = 2
              AND status = 'accepted'
            LIMIT 1
        """, (
            withdrawal["id"],
        )).fetchone()

        if not club2_accepted:

            club2_tp = cur.execute("""
                SELECT id
                FROM withdrawal_players
                WHERE withdrawal_id = ?
                  AND club_number = 2
                  AND status = 'tp'
                LIMIT 1
            """, (
                withdrawal["id"],
            )).fetchone()

            if not club2_tp:

                cur.execute("""
                    INSERT INTO withdrawal_players (
                        withdrawal_id,
                        user_id,
                        username,
                        club,
                        club_number,
                        status,
                        stage,
                        tp_reason,
                        created_at
                    )
                    VALUES (
                        ?, 0, NULL, ?, 2,
                        'tp',
                        'tp',
                        'withdrawal_timeout',
                        ?
                    )
                """, (
                    withdrawal["id"],
                    withdrawal["club2"],
                    now_string()
                ))

                tp_clubs.append(
                    withdrawal["club2"]
                )

        db.commit()

        # ----------------------------------------------------
        # ОТПРАВЛЯЕМ В КАНАЛ ОДНО СООБЩЕНИЕ
        # ----------------------------------------------------

        if tp_clubs:

            if len(tp_clubs) == 1:

                clubs_text = (
                    f"⚽ <b>{safe(tp_clubs[0])}</b>"
                )

            else:

                clubs_text = (
                    f"⚽ <b>{safe(tp_clubs[0])}</b>\n"
                    f"⚽ <b>{safe(tp_clubs[1])}</b>"
                )

            try:

                await bot.send_message(
                    OFFICIAL_CHANNEL_ID,
                    (
                        "⚠️ <b>ТЕХНИЧЕСКОЕ ПОРАЖЕНИЕ</b>\n\n"
                        "Клубы, которые не отписались:\n\n"
                        f"{clubs_text}\n\n"
                        "⏱ Время на отписку истекло."
                    )
                )

            except Exception as e:

                logging.error(
                    "Ошибка TP сообщения: %s",
                    e
                )

        # ----------------------------------------------------
        # ОБНОВЛЯЕМ СТАТУС В КАНАЛЕ
        # ----------------------------------------------------

        status1 = get_club_status(
            withdrawal["id"],
            1
        )

        status2 = get_club_status(
            withdrawal["id"],
            2
        )

        if withdrawal["channel_message_id"]:

            try:

                await bot.edit_message_text(
                    chat_id=OFFICIAL_CHANNEL_ID,
                    message_id=withdrawal["channel_message_id"],
                    text=withdrawal_text(
                        withdrawal["club1"],
                        withdrawal["club2"],
                        status1,
                        status2
                    ),
                    reply_markup=withdrawal_keyboard(
                        withdrawal["id"],
                        withdrawal["club1"],
                        withdrawal["club2"]
                    )
                )

            except Exception as e:

                logging.warning(
                    "Ошибка изменения канала: %s",
                    e
                )

        # ----------------------------------------------------
        # ЗАКРЫВАЕМ ОТПИСКУ
        # ----------------------------------------------------

        cur.execute("""
            UPDATE withdrawals
            SET status = 'closed'
            WHERE id = ?
        """, (
            withdrawal["id"],
        ))

        db.commit()


# ============================================================
# 5 ЧАСОВ — ПРОВЕРКА РЕЗУЛЬТАТОВ
# ============================================================

async def check_result_timeouts():

    rows = cur.execute("""
        SELECT
            wp.*,
            w.club1,
            w.club2
        FROM withdrawal_players wp
        JOIN withdrawals w
            ON w.id = wp.withdrawal_id
        WHERE wp.status = 'accepted'
          AND wp.stage = 'waiting_result'
          AND wp.result_sent = 0
          AND wp.result_deadline IS NOT NULL
    """).fetchall()

    for row in rows:

        deadline = parse_datetime(
            row["result_deadline"]
        )

        if not deadline:
            continue

        if now() < deadline:
            continue

        # ----------------------------------------------------
        # НЕ ОТПРАВИЛ РЕЗУЛЬТАТ -> ТП
        # ----------------------------------------------------

        cur.execute("""
            UPDATE withdrawal_players
            SET
                status = 'tp',
                stage = 'tp',
                tp_reason = 'result_timeout',
                result_deadline = NULL
            WHERE id = ?
              AND status = 'accepted'
              AND result_sent = 0
        """, (
            row["id"],
        ))

        db.commit()

        # ----------------------------------------------------
        # КАНАЛ
        # ----------------------------------------------------

        try:

            await bot.send_message(
                OFFICIAL_CHANNEL_ID,
                (
                    "⚠️ <b>ТЕХНИЧЕСКОЕ ПОРАЖЕНИЕ</b>\n\n"
                    f"⚽ <b>{safe(row['club'])}</b>\n\n"
                    "Результат матча не был отправлен "
                    f"за {RESULT_TIMEOUT_HOURS} часов."
                )
            )

        except Exception as e:

            logging.error(
                "Ошибка TP результата: %s",
                e
            )

        # ----------------------------------------------------
        # ИГРОКУ
        # ----------------------------------------------------

        if row["user_id"] != 0:

            try:

                await bot.send_message(
                    row["user_id"],
                    (
                        "⚠️ <b>ТЕХНИЧЕСКОЕ ПОРАЖЕНИЕ</b>\n\n"
                        f"⚽ Клуб: <b>{safe(row['club'])}</b>\n\n"
                        "Результат матча не был отправлен "
                        f"за {RESULT_TIMEOUT_HOURS} часов."
                    )
                )

            except Exception as e:

                logging.warning(
                    "Ошибка сообщения игроку: %s",
                    e
                )


# ============================================================
# ФОНОВЫЙ ТАЙМЕР
# ============================================================

async def deadline_checker():

    while True:

        try:

            await check_withdrawal_timeouts()

            await check_result_timeouts()

        except Exception as e:

            logging.exception(
                "Ошибка таймера: %s",
                e
            )

        await asyncio.sleep(10)


# ============================================================
# ВАЖНО:
# ЗДЕСЬ НЕТ @dp.message() БЕЗ УСЛОВИЯ.
#
# Поэтому если игрок напишет:
#
# "привет"
# "тест"
# "123"
#
# БОТ НЕ БУДЕТ ОТВЕЧАТЬ.
#
# Бот реагирует только на:
# /start
# /myid
# /admin
# /cancel
# /help
# состояния FSM
# кнопки
# ============================================================


# ============================================================
# MAIN
# ============================================================

async def main():

    if BOT_TOKEN == "ВСТАВЬТЕ_ТОКЕН_БОТА":

        print()
        print("=" * 60)
        print("ОШИБКА: ВСТАВЬТЕ ТОКЕН В BOT_TOKEN")
        print("=" * 60)
        print()

        return

    print("=" * 60)
    print("SL27 BOT ЗАПУЩЕН")
    print(f"ADMIN GROUP: {ADMIN_GROUP_ID}")
    print(f"OFFICIAL CHANNEL: {OFFICIAL_CHANNEL_ID}")
    print("=" * 60)

    # Убираем webhook
    try:

        await bot.delete_webhook(
            drop_pending_updates=False
        )

    except Exception as e:

        logging.warning(
            "Ошибка удаления webhook: %s",
            e
        )

    # Запускаем таймеры
    asyncio.create_task(
        deadline_checker()
    )

    # Запускаем бота
    await dp.start_polling(
        bot
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        print(
            "\nБот остановлен."
        )

    except Exception as e:

        logging.exception(
            "Критическая ошибка: %s",
            e
        )

    finally:

        db.close()