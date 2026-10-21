import asyncio
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta
from html import escape

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)


# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

# АДМИН-ГРУППА
ADMIN_GROUP_ID = -1004380534371

# ОФИЦИАЛЬНЫЙ КАНАЛ
OFFICIAL_CHANNEL_ID = -1004361362556
OFFICIAL_CHANNEL_LINK = "https://t.me/SLSleaguer"

# КАНАЛ «СВОБОДНЫЕ АГЕНТЫ И ТРАНСФЕРЫ»
# !!! ВСТАВЬТЕ СЮДА ID ВТОРОГО КАНАЛА (бот должен быть админом канала) !!!
TRANSFERS_CHANNEL_ID = -1004240688431

# ОСНОВНЫЕ АДМИНЫ
CREATOR_ID = 7762496102
ADMIN_ID = 7908057052

# БАЗА
DB_NAME = "sl27.db"

# 10 минут на отписку
WITHDRAWAL_TIMEOUT_MINUTES = 10

# 5 часов на результат
RESULT_TIMEOUT_HOURS = 5

# Максимум игроков в составе клуба (без владельца)
MAX_CLUB_PLAYERS = 18

# Вопросы для «Заявки на получение клуба».
# ПОСЛЕДНИЙ вопрос всегда считается названием клуба.
APPLICATION_QUESTIONS = [
    "Сколько вам лет?",
    "Есть ли у вас опыт управления клубом или командой? Опишите кратко.",
    "Сколько времени в неделю вы готовы уделять клубу и матчам?",
    "Почему именно вы должны стать владельцем клуба? Чем будете полезны лиге?",
    "Какой клуб вы хотите взять?",
]


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
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher(storage=MemoryStorage())


# ============================================================
# DATABASE
# ============================================================

db = sqlite3.connect(DB_NAME, check_same_thread=False)
db.row_factory = sqlite3.Row
cur = db.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    created_at TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    created_at TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    created_at TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS mvps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT,
    photo_id TEXT,
    created_at TEXT
)
""")

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

# ---------------- НОВОЕ ----------------

# Клубы. owner_id = NULL -> у клуба нет владельца
cur.execute("""
CREATE TABLE IF NOT EXISTS clubs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    owner_id INTEGER,
    created_at TEXT
)
""")

# Состав клуба (владелец сюда не входит)
cur.execute("""
CREATE TABLE IF NOT EXISTS club_players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    club_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL UNIQUE,
    joined_at TEXT
)
""")

# Предложения контракта (/sign)
cur.execute("""
CREATE TABLE IF NOT EXISTS sign_offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    club_id INTEGER NOT NULL,
    from_user_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    status TEXT DEFAULT 'pending',
    created_at TEXT
)
""")

# Заявки «Свободный агент»
cur.execute("""
CREATE TABLE IF NOT EXISTS free_agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    username TEXT,
    roblox_nick TEXT,
    ps TEXT,
    status TEXT DEFAULT 'pending',
    reject_reason TEXT,
    admin_message_id INTEGER,
    created_at TEXT
)
""")

# Заявки «Получение клуба»
cur.execute("""
CREATE TABLE IF NOT EXISTS club_applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    username TEXT,
    roblox_nick TEXT,
    answers TEXT,
    club TEXT,
    status TEXT DEFAULT 'pending',
    reject_reason TEXT,
    admin_message_id INTEGER,
    created_at TEXT
)
""")

# Запросы причины отказа в админ-группе
cur.execute("""
CREATE TABLE IF NOT EXISTS reason_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_message_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    done INTEGER DEFAULT 0,
    created_at TEXT
)
""")


# ============================================================
# МИГРАЦИЯ СТАРОЙ БАЗЫ
# ============================================================

def ensure_column(table_name, column_name, definition):
    columns = cur.execute(f"PRAGMA table_info({table_name})").fetchall()
    existing = [row["name"] for row in columns]
    if column_name not in existing:
        cur.execute(
            f"ALTER TABLE {table_name} "
            f"ADD COLUMN {column_name} {definition}"
        )


ensure_column("withdrawal_players", "club_number", "INTEGER DEFAULT 1")
ensure_column("withdrawal_players", "stage", "TEXT DEFAULT 'pending'")
ensure_column("withdrawal_players", "accepted_at", "TEXT")
ensure_column("withdrawal_players", "vip_sent_at", "TEXT")
ensure_column("withdrawal_players", "result_deadline", "TEXT")
ensure_column("withdrawal_players", "result_sent", "INTEGER DEFAULT 0")
ensure_column("withdrawal_players", "tp_reason", "TEXT")

# Roblox-привязка игроков
ensure_column("users", "roblox_nick", "TEXT")
ensure_column("users", "roblox_id", "INTEGER")
ensure_column("users", "verified", "INTEGER DEFAULT 0")

db.commit()


# ============================================================
# FSM STATES
# ============================================================

class UserState(StatesGroup):
    waiting_question = State()
    waiting_vip = State()
    waiting_result = State()
    # новое
    waiting_roblox_nick = State()
    waiting_roblox_confirm = State()
    waiting_ps = State()
    waiting_app_answer = State()


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
    return now().strftime("%Y-%m-%d %H:%M:%S")


def parse_datetime(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def safe(value):
    if value is None:
        return ""
    return escape(str(value))


def get_user_name(user):
    if user.username:
        return f"@{user.username}"
    return user.first_name or "Игрок"


def parse_args(message: Message) -> str:
    """Текст после команды: '/sign Nick' -> 'Nick'."""
    parts = (message.text or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _upsert_user(user):
    cur.execute("""
        INSERT INTO users (user_id, username, first_name, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name
    """, (user.id, user.username, user.first_name, now_string()))
    db.commit()


def save_user(message: Message):
    _upsert_user(message.from_user)


def save_callback_user(callback: CallbackQuery):
    _upsert_user(callback.from_user)


# ---------------- пользователи / клубы ----------------

def get_db_user(user_id):
    return cur.execute(
        "SELECT * FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()


def is_verified(user_id) -> bool:
    row = cur.execute(
        "SELECT verified FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    return bool(row and row["verified"])


def find_user_by_nick(nick):
    return cur.execute("""
        SELECT * FROM users
        WHERE verified = 1 AND roblox_nick = ? COLLATE NOCASE
    """, (nick,)).fetchone()


def get_club_by_name(name):
    return cur.execute(
        "SELECT * FROM clubs WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()


def get_owned_club(user_id):
    return cur.execute(
        "SELECT * FROM clubs WHERE owner_id = ? LIMIT 1", (user_id,)
    ).fetchone()


def get_player_club(user_id):
    return cur.execute("""
        SELECT c.* FROM club_players cp
        JOIN clubs c ON c.id = cp.club_id
        WHERE cp.user_id = ?
    """, (user_id,)).fetchone()


def get_user_club(user_id):
    """(club, role): role = 'owner' / 'player' / None"""
    club = get_owned_club(user_id)
    if club:
        return club, "owner"
    club = get_player_club(user_id)
    if club:
        return club, "player"
    return None, None


def person_label(row):
    """Ник в Roblox + @username (уже без escape)."""
    if not row:
        return "—"
    nick = row["roblox_nick"] or row["first_name"] or "Игрок"
    if row["username"]:
        return f"{nick} (@{row['username']})"
    return nick


async def safe_send(chat_id, text, **kwargs) -> bool:
    try:
        await bot.send_message(chat_id, text, **kwargs)
        return True
    except Exception as e:
        logging.warning("Не удалось отправить сообщение %s: %s", chat_id, e)
        return False


async def publish_to_transfers(text) -> bool:
    if not TRANSFERS_CHANNEL_ID:
        logging.warning("TRANSFERS_CHANNEL_ID не задан — пост не опубликован")
        return False
    try:
        await bot.send_message(TRANSFERS_CHANNEL_ID, text)
        return True
    except Exception as e:
        logging.error("Ошибка публикации в канал трансферов: %s", e)
        return False


async def set_user_state(user_id: int, state):
    """Поставить FSM-состояние ДРУГОМУ пользователю (в его ЛС)."""
    key = StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
    await FSMContext(storage=dp.storage, key=key).set_state(state)


async def require_verified(callback: CallbackQuery) -> bool:
    if is_verified(callback.from_user.id):
        return True
    await callback.answer(
        "❌ Сначала пройдите регистрацию: напишите боту /start",
        show_alert=True
    )
    return False


async def show_screen(callback: CallbackQuery, text, kb=None):
    """Редактирует сообщение, а если нельзя (например, фото) — шлёт новое."""
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        await callback.message.answer(text, reply_markup=kb)
        try:
            await callback.message.delete()
        except Exception:
            pass


def back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")
    ]])


def done_keyboard(text):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=text, callback_data="already_done")
    ]])


async def mark_admin_message(message_id, text):
    if not message_id:
        return
    try:
        await bot.edit_message_reply_markup(
            chat_id=ADMIN_GROUP_ID,
            message_id=message_id,
            reply_markup=done_keyboard(text)
        )
    except Exception:
        pass


# ============================================================
# ADMIN CHECK
# ============================================================

async def is_admin(user_id: int) -> bool:

    if user_id in (CREATOR_ID, ADMIN_ID):
        return True

    try:
        member = await bot.get_chat_member(ADMIN_GROUP_ID, user_id)
        return member.status in ("administrator", "creator")

    except Exception as e:
        logging.warning("Ошибка проверки админа %s: %s", user_id, e)
        return False


async def can_use_admin_command(message: Message) -> bool:
    """Админ-команды: только в админ-группе или в ЛС у админа."""
    if message.chat.id != ADMIN_GROUP_ID and message.chat.type != "private":
        return False
    return await is_admin(message.from_user.id)


# ============================================================
# MAIN MENU
# ============================================================

def build_main_menu(is_admin_user=False, has_club=False):

    rows = [
        [
            InlineKeyboardButton(text="📅 Расписание", callback_data="menu_schedule"),
            InlineKeyboardButton(text="📊 Результаты", callback_data="menu_results"),
        ],
        [
            InlineKeyboardButton(text="🏆 MVP", callback_data="menu_mvp"),
            InlineKeyboardButton(text="❓ Задать вопрос", callback_data="menu_question"),
        ],
        [
            InlineKeyboardButton(
                text="📝 Заявка на получение клуба",
                callback_data="menu_club_app"
            )
        ],
        [
            InlineKeyboardButton(text="🆓 Свободный агент", callback_data="menu_agent"),
            InlineKeyboardButton(text="👤 Мой профиль", callback_data="menu_profile"),
        ],
    ]

    if has_club:
        rows.append([
            InlineKeyboardButton(text="🏟 Мой клуб", callback_data="menu_myclub")
        ])

    rows.append([
        InlineKeyboardButton(text="📢 Официальный канал", url=OFFICIAL_CHANNEL_LINK)
    ])

    if is_admin_user:
        rows.append([
            InlineKeyboardButton(text="🔧 Админ-панель", callback_data="menu_admin")
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def main_menu_for(user_id: int):
    club, _ = get_user_club(user_id)
    return build_main_menu(
        is_admin_user=await is_admin(user_id),
        has_club=club is not None
    )


def admin_menu():

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Создать отписку", callback_data="admin_withdrawal")],
        [
            InlineKeyboardButton(text="📅 Изменить расписание", callback_data="admin_schedule"),
            InlineKeyboardButton(text="📊 Добавить результат", callback_data="admin_result"),
        ],
        [InlineKeyboardButton(text="🏆 Установить MVP", callback_data="admin_mvp")],
        [InlineKeyboardButton(text="📣 Сделать пост", callback_data="admin_post")],
        [InlineKeyboardButton(text="❓ Вопросы игроков", callback_data="admin_questions")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="back_main")],
    ])


# ============================================================
# ОТПИСКА — ДИЗАЙН
# ============================================================

def get_club_status(withdrawal_id, club_number):

    row = cur.execute("""
        SELECT status
        FROM withdrawal_players
        WHERE withdrawal_id = ?
          AND club_number = ?
          AND status IN ('pending', 'accepted', 'tp')
        ORDER BY id DESC
        LIMIT 1
    """, (withdrawal_id, club_number)).fetchone()

    if not row:
        return "free"
    if row["status"] == "accepted":
        return "accepted"
    if row["status"] == "tp":
        return "tp"
    return "pending"


def withdrawal_text(club1, club2, status1, status2):

    symbol1 = "✅" if status1 == "accepted" else "❌"
    symbol2 = "✅" if status2 == "accepted" else "❌"

    return (
        "╭─────── ✦ MATCH ✦ ───────╮\n\n"
        f"⚽ <b>{safe(club1)}</b>   {symbol1} 🔑\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"⚽ <b>{safe(club2)}</b>   {symbol2}\n\n"
        "╰─────────────────────────╯"
    )


def withdrawal_keyboard(withdrawal_id, club1, club2):

    buttons = []

    for number, club in ((1, club1), (2, club2)):

        status = get_club_status(withdrawal_id, number)

        if status == "accepted":
            buttons.append([InlineKeyboardButton(
                text=f"✅ {club}", callback_data="already_done"
            )])

        elif status == "tp":
            buttons.append([InlineKeyboardButton(
                text=f"⚠️ {club} — ТП", callback_data="already_done"
            )])

        else:
            buttons.append([InlineKeyboardButton(
                text=f"⚽ Отписаться — {club}",
                callback_data=f"withdraw:{withdrawal_id}:{number}"
            )])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def withdrawal_admin_keyboard(request_id):

    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_withdraw:{request_id}"),
        InlineKeyboardButton(text="❌ Отказать", callback_data=f"reject_withdraw:{request_id}"),
    ]])


def processed_withdrawal_keyboard(accepted):
    return done_keyboard("✅" if accepted else "❌")


def question_admin_keyboard(question_id):

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Ответить", callback_data=f"answer_question:{question_id}")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data=f"close_question:{question_id}")],
    ])


@dp.callback_query(F.data == "already_done")
async def already_done(callback: CallbackQuery):
    await callback.answer("Уже обработано.", show_alert=True)


# ============================================================
# ROBLOX
# ============================================================

ROBLOX_NICK_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")


async def fetch_roblox_user(nick: str):
    """
    Возвращает (status, info):
      ("ok", {"id", "name", "display", "image"})
      ("notfound", None)
      ("error", None)
    """
    timeout = aiohttp.ClientTimeout(total=10)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:

            async with session.post(
                "https://users.roblox.com/v1/usernames/users",
                json={"usernames": [nick], "excludeBannedUsers": False},
            ) as resp:
                if resp.status != 200:
                    return "error", None
                data = await resp.json()

            items = data.get("data") or []
            if not items:
                return "notfound", None

            found = items[0]
            roblox_id = found["id"]
            image_url = None

            # Картинка иногда готовится не сразу — пробуем до 3 раз
            for _ in range(3):
                async with session.get(
                    "https://thumbnails.roblox.com/v1/users/avatar",
                    params={
                        "userIds": roblox_id,
                        "size": "420x420",
                        "format": "Png",
                        "isCircular": "false",
                    },
                ) as resp:
                    if resp.status == 200:
                        thumbs = (await resp.json()).get("data") or []
                        if (
                            thumbs
                            and thumbs[0].get("state") == "Completed"
                            and thumbs[0].get("imageUrl")
                        ):
                            image_url = thumbs[0]["imageUrl"]
                            break
                await asyncio.sleep(1)

            return "ok", {
                "id": roblox_id,
                "name": found.get("name", nick),
                "display": found.get("displayName"),
                "image": image_url,
            }

    except Exception as e:
        logging.warning("Ошибка запроса к Roblox: %s", e)
        return "error", None


# ============================================================
# START + РЕГИСТРАЦИЯ ПО НИКУ ROBLOX
# ============================================================

@dp.message(Command("start"))
async def start_handler(message: Message, state: FSMContext):

    await state.clear()

    if message.chat.type != "private":
        return

    save_user(message)
    user_id = message.from_user.id

    # Уже зарегистрирован -> всегда сразу меню
    if is_verified(user_id):
        await message.answer(
            "✦ <b>SL27 LEAGUE</b> ✦\n\n"
            "Добро пожаловать!\n\n"
            "Выберите раздел:",
            reply_markup=await main_menu_for(user_id)
        )
        return

    await state.set_state(UserState.waiting_roblox_nick)

    await message.answer(
        "✦ <b>SL27 LEAGUE</b> ✦\n\n"
        "Добро пожаловать!\n\n"
        "🎮 Для начала напишите ваш <b>ник в Roblox</b>:"
    )


@dp.message(UserState.waiting_roblox_nick)
async def receive_roblox_nick(message: Message, state: FSMContext):

    save_user(message)

    if not message.text:
        await message.answer("❌ Отправьте ник текстом.")
        return

    nick = message.text.strip().lstrip("@")

    if not ROBLOX_NICK_RE.match(nick):
        await message.answer(
            "❌ Некорректный ник.\n\n"
            "Ник в Roblox — 3–20 символов: латиница, цифры и _.\n"
            "Попробуйте ещё раз:"
        )
        return

    wait = await message.answer("🔎 Ищу игрока в Roblox...")

    status, info = await fetch_roblox_user(nick)

    try:
        await wait.delete()
    except Exception:
        pass

    if status == "error":
        await message.answer(
            "⚠️ Не удалось связаться с Roblox. Попробуйте ещё раз чуть позже."
        )
        return

    if status == "notfound":
        await message.answer(
            f"❌ Игрок <b>{safe(nick)}</b> не найден в Roblox.\n\n"
            "Проверьте ник и отправьте его ещё раз:"
        )
        return

    await state.set_state(UserState.waiting_roblox_confirm)
    await state.update_data(rb_id=info["id"], rb_name=info["name"])

    caption = (
        "🎮 <b>Найден аккаунт Roblox</b>\n\n"
        f"👤 Ник: <b>{safe(info['name'])}</b>\n"
    )

    if info["display"] and info["display"] != info["name"]:
        caption += f"✨ Отображаемое имя: {safe(info['display'])}\n"

    caption += "\n<b>Это вы?</b>"

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да", callback_data="rbx_yes"),
        InlineKeyboardButton(text="❌ Нет", callback_data="rbx_no"),
    ]])

    if info["image"]:
        try:
            await message.answer_photo(info["image"], caption=caption, reply_markup=kb)
            return
        except Exception as e:
            logging.warning("Не удалось отправить аватар: %s", e)

    await message.answer(caption, reply_markup=kb)


@dp.callback_query(F.data.in_({"rbx_yes", "rbx_no"}))
async def roblox_confirm(callback: CallbackQuery, state: FSMContext):

    save_callback_user(callback)

    if await state.get_state() != UserState.waiting_roblox_confirm.state:
        await callback.answer(
            "⚠️ Сессия устарела. Напишите /start",
            show_alert=True
        )
        return

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    # ---------------- НЕТ ----------------

    if callback.data == "rbx_no":

        await state.set_state(UserState.waiting_roblox_nick)

        await callback.message.answer(
            "🎮 Хорошо, отправьте ваш ник в Roblox ещё раз:"
        )
        await callback.answer()
        return

    # ---------------- ДА ----------------

    data = await state.get_data()
    rb_id = data.get("rb_id")
    rb_name = data.get("rb_name")

    if not rb_id or not rb_name:
        await state.clear()
        await callback.answer("⚠️ Ошибка. Напишите /start", show_alert=True)
        return

    # Один аккаунт Roblox = один Telegram
    other = cur.execute("""
        SELECT user_id FROM users
        WHERE roblox_id = ? AND verified = 1 AND user_id != ?
    """, (rb_id, callback.from_user.id)).fetchone()

    if other:
        await state.clear()
        await callback.message.answer(
            "❌ Этот аккаунт Roblox уже привязан к другому пользователю.\n\n"
            "Если это ошибка — обратитесь к администрации."
        )
        await callback.answer()
        return

    cur.execute("""
        UPDATE users
        SET roblox_nick = ?, roblox_id = ?, verified = 1
        WHERE user_id = ?
    """, (rb_name, rb_id, callback.from_user.id))
    db.commit()

    await state.clear()

    await callback.message.answer(
        "✅ <b>Регистрация завершена!</b>\n\n"
        f"🎮 Roblox: <b>{safe(rb_name)}</b>\n\n"
        "✦ <b>SL27 LEAGUE</b> ✦\n"
        "Выберите раздел:",
        reply_markup=await main_menu_for(callback.from_user.id)
    )

    await callback.answer()


# ============================================================
# MYID / ADMIN / CANCEL / HELP
# ============================================================

@dp.message(Command("myid"))
async def myid_handler(message: Message):
    await message.answer(
        "🆔 <b>Ваш Telegram ID:</b>\n\n"
        f"<code>{message.from_user.id}</code>"
    )


@dp.message(Command("admin"))
async def admin_handler(message: Message):

    if not await is_admin(message.from_user.id):
        await message.answer("❌ У вас нет доступа.")
        return

    await message.answer(
        "╭────── ✦ ADMIN ✦ ──────╮\n\n"
        "🔧 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
        "╰────────────────────────╯",
        reply_markup=admin_menu()
    )


@dp.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext):

    await state.clear()

    if not is_verified(message.from_user.id):
        await message.answer(
            "❌ Действие отменено.\n\nДля регистрации нажмите /start"
        )
        return

    await message.answer(
        "❌ Действие отменено.",
        reply_markup=await main_menu_for(message.from_user.id)
    )


@dp.message(Command("help"))
async def help_handler(message: Message):

    user_id = message.from_user.id

    lines = [
        "ℹ️ <b>SL27 BOT</b>\n",
        "/start — главное меню",
        "/myid — ваш ID",
        "/clubs — список клубов",
        "/cancel — отменить действие",
        "/help — помощь",
    ]

    if get_owned_club(user_id):
        lines += [
            "",
            "👑 <b>Владелец клуба:</b>",
            "/sign ник — предложить контракт игроку",
            "/release ник — отпустить игрока из клуба",
        ]

    if await is_admin(user_id):
        lines += [
            "",
            "🔧 <b>Админ:</b>",
            "/admin — админ-панель",
            "/giveclub ник | клуб — выдать клуб игроку",
            "/takeclub ник или клуб — снять владельца",
            "/unlink ник — сбросить привязку Roblox",
        ]

    await message.answer("\n".join(lines))


@dp.callback_query(F.data == "back_main")
async def back_main(callback: CallbackQuery, state: FSMContext):

    await state.clear()

    await show_screen(
        callback,
        "✦ <b>SL27 LEAGUE</b> ✦\n\nВыберите раздел:",
        await main_menu_for(callback.from_user.id)
    )

    await callback.answer()


@dp.callback_query(F.data == "menu_admin")
async def menu_admin(callback: CallbackQuery):

    if not await is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return

    await callback.message.edit_text(
        "╭────── ✦ ADMIN ✦ ──────╮\n\n"
        "🔧 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
        "╰────────────────────────╯",
        reply_markup=admin_menu()
    )

    await callback.answer()
