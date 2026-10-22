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
from aiogram.enums import ParseMode, ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

# ============================================================
# CONFIGURATION
# ============================================================
BOT_TOKEN = BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_GROUP_ID = -1004380534371
OFFICIAL_CHANNEL_ID = -1004361362556
OFFICIAL_CHANNEL_LINK = "https://t.me/SLSleaguer"
TRANSFERS_CHANNEL_ID = -1004240688431
CREATOR_ID = 7762496102
ADMIN_ID = 7908057052
DB_NAME = "sl27.db"
MAX_CLUB_PLAYERS = 18
WITHDRAWAL_TIMEOUT_MINUTES = 10
APPLICATION_QUESTIONS = [
    "Сколько вам лет?",
    "Есть ли у вас опыт управления клубом или командой? Опишите кратко.",
    "Сколько времени в неделю вы готовы уделять клубу и матчам?",
    "Почему именно вы должны стать владельцем клуба? Чем будете полезны лиге?",
    "Какой клуб вы хотите взять?",
]
ROBLOX_NICK_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

if not BOT_TOKEN or BOT_TOKEN == "PASTE_NEW_BOT_TOKEN_HERE":
    raise RuntimeError("Укажи новый токен в переменной окружения BOT_TOKEN.")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# ============================================================
# DATABASE + MIGRATIONS
# ============================================================
db = sqlite3.connect(DB_NAME, check_same_thread=False)
db.row_factory = sqlite3.Row
cur = db.cursor()

SCHEMA = [
"""CREATE TABLE IF NOT EXISTS users (
 user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, created_at TEXT,
 roblox_nick TEXT, roblox_id INTEGER, verified INTEGER DEFAULT 0)""",
"""CREATE TABLE IF NOT EXISTS questions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, username TEXT,
 question TEXT NOT NULL, answer TEXT, status TEXT DEFAULT 'pending', created_at TEXT)""",
"""CREATE TABLE IF NOT EXISTS withdrawals (
 id INTEGER PRIMARY KEY AUTOINCREMENT, club1 TEXT NOT NULL, club2 TEXT NOT NULL,
 message_id INTEGER, channel_message_id INTEGER, status TEXT DEFAULT 'open', created_at TEXT)""",
"""CREATE TABLE IF NOT EXISTS withdrawal_players (
 id INTEGER PRIMARY KEY AUTOINCREMENT, withdrawal_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
 username TEXT, club TEXT NOT NULL, club_number INTEGER DEFAULT 1, status TEXT DEFAULT 'pending',
 stage TEXT DEFAULT 'pending', accepted_at TEXT, vip_sent_at TEXT, result_deadline TEXT,
 result_sent INTEGER DEFAULT 0, tp_reason TEXT, created_at TEXT)""",
"""CREATE TABLE IF NOT EXISTS submissions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, club TEXT NOT NULL,
 submission_type TEXT NOT NULL, text TEXT, photo_id TEXT, caption TEXT,
 status TEXT DEFAULT 'sent', created_at TEXT)""",
"""CREATE TABLE IF NOT EXISTS clubs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
 owner_id INTEGER, created_at TEXT)""",
"""CREATE TABLE IF NOT EXISTS club_players (
 id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER NOT NULL,
 user_id INTEGER NOT NULL UNIQUE, joined_at TEXT)""",
"""CREATE TABLE IF NOT EXISTS sign_offers (
 id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER NOT NULL, from_user_id INTEGER NOT NULL,
 user_id INTEGER NOT NULL, status TEXT DEFAULT 'pending', created_at TEXT)""",
"""CREATE TABLE IF NOT EXISTS free_agents (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, username TEXT,
 roblox_nick TEXT, ps TEXT, status TEXT DEFAULT 'pending', reject_reason TEXT,
 admin_message_id INTEGER, created_at TEXT)""",
"""CREATE TABLE IF NOT EXISTS club_applications (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, username TEXT,
 roblox_nick TEXT, answers TEXT, club TEXT, status TEXT DEFAULT 'pending',
 reject_reason TEXT, admin_message_id INTEGER, created_at TEXT)""",
"""CREATE TABLE IF NOT EXISTS reason_requests (
 id INTEGER PRIMARY KEY AUTOINCREMENT, prompt_message_id INTEGER NOT NULL,
 kind TEXT NOT NULL, target_id INTEGER NOT NULL, done INTEGER DEFAULT 0, created_at TEXT)""",
"""CREATE TABLE IF NOT EXISTS cooldowns (
 user_id INTEGER NOT NULL, kind TEXT NOT NULL, until_at TEXT NOT NULL,
 reason TEXT, PRIMARY KEY(user_id, kind))""",
"""CREATE TABLE IF NOT EXISTS club_announcements (
 id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER NOT NULL, owner_id INTEGER NOT NULL,
 text TEXT NOT NULL, created_at TEXT NOT NULL)""",
]
for sql in SCHEMA:
    cur.execute(sql)

def ensure_column(table, column, definition):
    existing = {r["name"] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

for table, col, definition in [
    ("users", "roblox_nick", "TEXT"), ("users", "roblox_id", "INTEGER"),
    ("users", "verified", "INTEGER DEFAULT 0"),
    ("withdrawal_players", "club_number", "INTEGER DEFAULT 1"),
    ("withdrawal_players", "stage", "TEXT DEFAULT 'pending'"),
    ("withdrawal_players", "accepted_at", "TEXT"),
    ("withdrawal_players", "vip_sent_at", "TEXT"),
    ("withdrawal_players", "result_deadline", "TEXT"),
    ("withdrawal_players", "result_sent", "INTEGER DEFAULT 0"),
    ("withdrawal_players", "tp_reason", "TEXT"),
]:
    ensure_column(table, col, definition)
db.commit()

# ============================================================
# STATES
# ============================================================
class UserState(StatesGroup):
    waiting_roblox_nick = State()
    waiting_roblox_confirm = State()
    waiting_question = State()
    waiting_ps = State()
    waiting_app_answer = State()
    waiting_vip = State()
    waiting_result = State()
    waiting_club_announcement = State()

class AdminState(StatesGroup):
    waiting_post = State()
    waiting_question_answer = State()
    waiting_withdrawal = State()
    waiting_reason = State()

# ============================================================
# HELPERS
# ============================================================
def now_string():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

COOLDOWN_FREE_AGENT_SECONDS = 2 * 60 * 60
COOLDOWN_SIGN_SECONDS = 2 * 24 * 60 * 60
COOLDOWN_CAREER_SECONDS = 15 * 24 * 60 * 60
COOLDOWN_ANNOUNCEMENT_SECONDS = 24 * 60 * 60


def set_cooldown(user_id, kind, seconds, reason=""):
    until = (datetime.now() + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("INSERT INTO cooldowns(user_id,kind,until_at,reason) VALUES(?,?,?,?) ON CONFLICT(user_id,kind) DO UPDATE SET until_at=excluded.until_at, reason=excluded.reason", (user_id, kind, until, reason))
    db.commit()
    return until


def clear_cooldown(user_id, kind):
    cur.execute("DELETE FROM cooldowns WHERE user_id=? AND kind=?", (user_id, kind))
    db.commit()


def cooldown_remaining(user_id, kind):
    row = cur.execute("SELECT until_at,reason FROM cooldowns WHERE user_id=? AND kind=?", (user_id, kind)).fetchone()
    if not row:
        return 0, None
    try:
        seconds = int((datetime.strptime(row["until_at"], "%Y-%m-%d %H:%M:%S") - datetime.now()).total_seconds())
    except ValueError:
        seconds = 0
    if seconds <= 0:
        clear_cooldown(user_id, kind)
        return 0, None
    return seconds, row["reason"]


def human_duration(seconds):
    days, rem = divmod(max(0, seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    bits = []
    if days: bits.append(f"{days} дн.")
    if hours: bits.append(f"{hours} ч.")
    if minutes and not days: bits.append(f"{minutes} мин.")
    return " ".join(bits) or "меньше минуты"


async def publish_transfer_post(text):
    try:
        await bot.send_message(TRANSFERS_CHANNEL_ID, text)
        return True
    except Exception as exc:
        logging.warning("Не удалось опубликовать в канале трансферов: %s", exc)
        return False

def safe(value):
    return escape(str(value if value is not None else ""))

def parse_args(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""

def upsert_user(user):
    cur.execute("""INSERT INTO users(user_id, username, first_name, created_at)
        VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
        username=excluded.username, first_name=excluded.first_name""",
        (user.id, user.username, user.first_name, now_string()))
    db.commit()

def save_user(message):
    if message.from_user:
        upsert_user(message.from_user)

def save_callback_user(callback):
    upsert_user(callback.from_user)

def get_db_user(user_id):
    return cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

def is_verified(user_id):
    row = cur.execute("SELECT verified FROM users WHERE user_id=?", (user_id,)).fetchone()
    return bool(row and row["verified"])

def find_user_by_nick(nick):
    return cur.execute("SELECT * FROM users WHERE verified=1 AND roblox_nick=? COLLATE NOCASE", (nick,)).fetchone()

def get_club_by_name(name):
    return cur.execute("SELECT * FROM clubs WHERE name=? COLLATE NOCASE", (name,)).fetchone()

def get_owned_club(user_id):
    return cur.execute("SELECT * FROM clubs WHERE owner_id=? LIMIT 1", (user_id,)).fetchone()

def get_player_club(user_id):
    return cur.execute("SELECT c.* FROM club_players cp JOIN clubs c ON c.id=cp.club_id WHERE cp.user_id=?", (user_id,)).fetchone()

def get_user_club(user_id):
    club = get_owned_club(user_id)
    if club:
        return club, "owner"
    club = get_player_club(user_id)
    return (club, "player") if club else (None, None)

def person_label(row):
    if not row:
        return "—"
    nick = row["roblox_nick"] or row["first_name"] or "Игрок"
    return f"{nick} (@{row['username']})" if row["username"] else nick


def telegram_contact(user_id, username=None):
    """Возвращает красивую HTML-ссылку на Telegram-профиль игрока."""
    if username:
        clean_username = str(username).lstrip("@")
        return f'<a href="https://t.me/{escape(clean_username, quote=True)}">@{safe(clean_username)}</a>'
    return f'<a href="tg://user?id={int(user_id)}">Открыть профиль</a>'


def transfer_player_line(user_id, username, roblox_nick):
    contact = telegram_contact(user_id, username)
    return f"🎮 <b>Roblox:</b> {safe(roblox_nick or 'Не указан')}\n💬 <b>Telegram:</b> {contact}"

async def is_admin(user_id):
    if user_id in (CREATOR_ID, ADMIN_ID):
        return True
    try:
        member = await bot.get_chat_member(ADMIN_GROUP_ID, user_id)
        return member.status in ("administrator", "creator")
    except Exception as exc:
        logging.warning("Не удалось проверить администратора %s: %s", user_id, exc)
        return False

async def can_use_admin_command(message):
    if message.chat.id != ADMIN_GROUP_ID and message.chat.type != ChatType.PRIVATE:
        return False
    return await is_admin(message.from_user.id)

async def safe_send(chat_id, text, **kwargs):
    try:
        await bot.send_message(chat_id, text, **kwargs)
        return True
    except Exception as exc:
        logging.warning("Не удалось отправить сообщение %s: %s", chat_id, exc)
        return False

async def publish_to_transfers(text):
    try:
        await bot.send_message(TRANSFERS_CHANNEL_ID, text)
        return True
    except Exception as exc:
        logging.error("Ошибка публикации в канал трансферов: %s", exc)
        return False

async def set_user_state(user_id, state):
    key = StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
    await FSMContext(storage=dp.storage, key=key).set_state(state)

async def require_verified(callback):
    if is_verified(callback.from_user.id):
        return True
    await callback.answer("Сначала зарегистрируйтесь через /start.", show_alert=True)
    return False

async def show_screen(callback, text, kb=None):
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        await callback.message.answer(text, reply_markup=kb)


def back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")]])

def done_keyboard(text="✅ Обработано"):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, callback_data="already_done")]])

def question_admin_keyboard(question_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Ответить", callback_data=f"answer_question:{question_id}")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data=f"close_question:{question_id}")],
    ])

def request_review_keyboard(kind, item_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"review:{kind}:accept:{item_id}"),
        InlineKeyboardButton(text="❌ Отказать", callback_data=f"review:{kind}:reject:{item_id}"),
    ]])

# ============================================================
# MENUS (NO SCHEDULE / RESULTS / MVP)
# ============================================================
def build_main_menu(is_admin_user=False, has_club=False):
    rows = [
        [InlineKeyboardButton(text="❓ Задать вопрос", callback_data="menu_question")],
        [InlineKeyboardButton(text="📝 Заявка на получение клуба", callback_data="menu_club_app")],
        [InlineKeyboardButton(text="🆓 Свободный агент", callback_data="menu_agent"),
         InlineKeyboardButton(text="👤 Мой профиль", callback_data="menu_profile")],
    ]
    rows.append([InlineKeyboardButton(text="🏁 Завершить карьеру", callback_data="career_end")])
    if has_club:
        rows.append([InlineKeyboardButton(text="🏟 Мой клуб", callback_data="menu_myclub")])
    rows.append([InlineKeyboardButton(text="📢 Официальный канал", url=OFFICIAL_CHANNEL_LINK)])
    if is_admin_user:
        rows.append([InlineKeyboardButton(text="🔧 Админ-панель", callback_data="menu_admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def main_menu_for(user_id):
    club, _ = get_user_club(user_id)
    return build_main_menu(await is_admin(user_id), club is not None)

def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Создать отписку", callback_data="admin_withdrawal")],
        [InlineKeyboardButton(text="📣 Сделать пост", callback_data="admin_post")],
        [InlineKeyboardButton(text="❓ Вопросы игроков", callback_data="admin_questions")],
        [InlineKeyboardButton(text="📋 Заявки на клуб", callback_data="admin_applications")],
        [InlineKeyboardButton(text="🆓 Заявки свободных агентов", callback_data="admin_agents")],
        [InlineKeyboardButton(text="⏳ Кулдауны", callback_data="admin_cooldowns")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="back_main")],
    ])

# ============================================================
# ROBLOX API — KEEP ORIGINAL VERIFICATION FLOW
# ============================================================
async def fetch_roblox_user(nick):
    timeout = aiohttp.ClientTimeout(total=12)
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
            for _ in range(3):
                async with session.get(
                    "https://thumbnails.roblox.com/v1/users/avatar",
                    params={"userIds": roblox_id, "size": "420x420", "format": "Png", "isCircular": "false"},
                ) as resp:
                    if resp.status == 200:
                        thumbs = (await resp.json()).get("data") or []
                        if thumbs and thumbs[0].get("state") == "Completed" and thumbs[0].get("imageUrl"):
                            image_url = thumbs[0]["imageUrl"]
                            break
                await asyncio.sleep(1)
            return "ok", {"id": roblox_id, "name": found.get("name", nick), "display": found.get("displayName"), "image": image_url}
    except Exception as exc:
        logging.warning("Ошибка запроса к Roblox: %s", exc)
        return "error", None

@dp.message(Command("start"))
async def start_handler(message: Message, state: FSMContext):
    await state.clear()
    if message.chat.type != ChatType.PRIVATE:
        return
    save_user(message)
    if is_verified(message.from_user.id):
        await message.answer("━━━━━━━━━━━━━━━━━━\n⚽ <b>SL27 LEAGUE</b>\n━━━━━━━━━━━━━━━━━━\n\nДобро пожаловать в систему лиги!\nВыберите нужный раздел в меню ниже.", reply_markup=await main_menu_for(message.from_user.id))
        return
    await state.set_state(UserState.waiting_roblox_nick)
    await message.answer("━━━━━━━━━━━━━━━━━━\n⚽ <b>SL27 LEAGUE</b>\n━━━━━━━━━━━━━━━━━━\n\nДля регистрации отправьте свой <b>ник Roblox</b>.\n\n<i>Бот проверит аккаунт и покажет его аватар для подтверждения.</i>")

@dp.message(UserState.waiting_roblox_nick)
async def receive_roblox_nick(message: Message, state: FSMContext):
    save_user(message)
    if not message.text:
        await message.answer("❌ Отправьте ник текстом.")
        return
    nick = message.text.strip().lstrip("@")
    if not ROBLOX_NICK_RE.fullmatch(nick):
        await message.answer("❌ Некорректный ник. Допустимы 3–20 символов: латиница, цифры и _. Попробуйте ещё раз:")
        return
    wait = await message.answer("🔎 Ищу игрока в Roblox...")
    status, info = await fetch_roblox_user(nick)
    try:
        await wait.delete()
    except Exception:
        pass
    if status == "error":
        await message.answer("⚠️ Не удалось связаться с Roblox. Попробуйте ещё раз чуть позже.")
        return
    if status == "notfound":
        await message.answer(f"❌ Игрок <b>{safe(nick)}</b> не найден в Roblox.\n\nПроверьте ник и отправьте его ещё раз:")
        return
    await state.set_state(UserState.waiting_roblox_confirm)
    await state.update_data(rb_id=info["id"], rb_name=info["name"])
    caption = "🎮 <b>Найден аккаунт Roblox</b>\n\n" + f"👤 Ник: <b>{safe(info['name'])}</b>\n"
    if info["display"] and info["display"] != info["name"]:
        caption += f"✨ Отображаемое имя: {safe(info['display'])}\n"
    caption += "\n<b>Это вы?</b>"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Да", callback_data="rbx_yes"), InlineKeyboardButton(text="❌ Нет", callback_data="rbx_no")]])
    if info["image"]:
        try:
            await message.answer_photo(info["image"], caption=caption, reply_markup=kb)
            return
        except Exception as exc:
            logging.warning("Не удалось отправить аватар: %s", exc)
    await message.answer(caption, reply_markup=kb)

@dp.callback_query(F.data.in_({"rbx_yes", "rbx_no"}))
async def roblox_confirm(callback: CallbackQuery, state: FSMContext):
    save_callback_user(callback)
    if await state.get_state() != UserState.waiting_roblox_confirm.state:
        await callback.answer("⚠️ Сессия устарела. Напишите /start", show_alert=True)
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    if callback.data == "rbx_no":
        await state.set_state(UserState.waiting_roblox_nick)
        await callback.message.answer("🎮 Хорошо, отправьте ваш ник в Roblox ещё раз:")
        await callback.answer()
        return
    data = await state.get_data()
    rb_id, rb_name = data.get("rb_id"), data.get("rb_name")
    if not rb_id or not rb_name:
        await state.clear()
        await callback.answer("⚠️ Ошибка. Напишите /start", show_alert=True)
        return
    other = cur.execute("SELECT user_id FROM users WHERE roblox_id=? AND verified=1 AND user_id!=?", (rb_id, callback.from_user.id)).fetchone()
    if other:
        await state.clear()
        await callback.message.answer("❌ Этот аккаунт Roblox уже привязан к другому пользователю. Обратитесь к администрации, если это ошибка.")
        await callback.answer()
        return
    cur.execute("UPDATE users SET roblox_nick=?, roblox_id=?, verified=1 WHERE user_id=?", (rb_name, rb_id, callback.from_user.id))
    db.commit()
    await state.clear()
    await callback.message.answer(f"✅ <b>Регистрация завершена!</b>\n\n🎮 Roblox: <b>{safe(rb_name)}</b>\n\n✦ <b>SL27 LEAGUE</b> ✦\nВыберите раздел:", reply_markup=await main_menu_for(callback.from_user.id))
    await callback.answer()

# ============================================================
# MAIN MENU BUTTONS
# ============================================================
@dp.callback_query(F.data == "already_done")
async def already_done(callback: CallbackQuery):
    await callback.answer("Уже обработано.", show_alert=True)

@dp.callback_query(F.data == "back_main")
async def back_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_screen(callback, "✦ <b>SL27 LEAGUE</b> ✦\n\nВыберите раздел:", await main_menu_for(callback.from_user.id))
    await callback.answer()

@dp.callback_query(F.data == "menu_profile")
async def menu_profile(callback: CallbackQuery):
    if not await require_verified(callback): return
    user = get_db_user(callback.from_user.id)
    club, role = get_user_club(callback.from_user.id)
    role_text = "Владелец" if role == "owner" else "Игрок" if role == "player" else "Без клуба"
    text = ("👤 <b>ВАШ ПРОФИЛЬ</b>\n\n"
            f"Telegram: {safe('@' + user['username'] if user['username'] else user['first_name'])}\n"
            f"Telegram ID: <code>{user['user_id']}</code>\n"
            f"Roblox: <b>{safe(user['roblox_nick'] or 'не привязан')}</b>\n"
            f"Статус: {role_text}\n")
    if club: text += f"Клуб: <b>{safe(club['name'])}</b>\n"
    await show_screen(callback, text, back_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "menu_question")
async def menu_question(callback: CallbackQuery, state: FSMContext):
    if not await require_verified(callback): return
    await state.set_state(UserState.waiting_question)
    await callback.message.answer("❓ Напишите ваш вопрос администрации одним сообщением. Для отмены отправьте /cancel.")
    await callback.answer()

@dp.message(UserState.waiting_question)
async def receive_question(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer("Отправьте вопрос текстом.")
        return
    save_user(message)
    cur.execute("INSERT INTO questions(user_id, username, question, created_at) VALUES(?,?,?,?)", (message.from_user.id, message.from_user.username, message.text.strip(), now_string()))
    qid = cur.lastrowid
    db.commit()
    sent = await safe_send(ADMIN_GROUP_ID, f"❓ <b>ВОПРОС #{qid}</b>\nОт: {safe(get_db_user(message.from_user.id)['roblox_nick'])} (<code>{message.from_user.id}</code>)\n\n{safe(message.text.strip())}", reply_markup=question_admin_keyboard(qid))
    await state.clear()
    await message.answer("✅ Вопрос отправлен администрации." if sent else "⚠️ Вопрос сохранён, но не удалось отправить его в админ-группу. Сообщите администрации.", reply_markup=await main_menu_for(message.from_user.id))

@dp.callback_query(F.data == "menu_club_app")
async def menu_club_app(callback: CallbackQuery, state: FSMContext):
    if not await require_verified(callback): return
    pending = cur.execute("SELECT id FROM club_applications WHERE user_id=? AND status='pending'", (callback.from_user.id,)).fetchone()
    if pending:
        await callback.answer("У вас уже есть заявка на рассмотрении.", show_alert=True)
        return
    await state.set_state(UserState.waiting_app_answer)
    await state.update_data(app_index=0, app_answers=[])
    await callback.message.answer("📝 <b>Заявка на получение клуба</b>\n\n" + APPLICATION_QUESTIONS[0] + "\n\nДля отмены отправьте /cancel.")
    await callback.answer()

@dp.message(UserState.waiting_app_answer)
async def receive_app_answer(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer("Пожалуйста, ответьте текстом.")
        return
    data = await state.get_data()
    answers = data.get("app_answers", [])
    index = data.get("app_index", 0)
    answers.append(message.text.strip())
    index += 1
    if index < len(APPLICATION_QUESTIONS):
        await state.update_data(app_index=index, app_answers=answers)
        await message.answer(APPLICATION_QUESTIONS[index])
        return
    club_name = answers[-1].strip()
    cur.execute("INSERT INTO club_applications(user_id, username, roblox_nick, answers, club, created_at) VALUES(?,?,?,?,?,?)",
                (message.from_user.id, message.from_user.username, (get_db_user(message.from_user.id) or {}).get("roblox_nick") if False else get_db_user(message.from_user.id)["roblox_nick"], json.dumps(answers, ensure_ascii=False), club_name, now_string()))
    app_id = cur.lastrowid
    db.commit()
    summary = "\n".join(f"<b>{safe(q)}</b>\n{safe(a)}" for q, a in zip(APPLICATION_QUESTIONS, answers))
    sent_msg = await bot.send_message(ADMIN_GROUP_ID, f"📝 <b>ЗАЯВКА НА КЛУБ #{app_id}</b>\nИгрок: {safe(get_db_user(message.from_user.id)['roblox_nick'])} (<code>{message.from_user.id}</code>)\n\n{summary}", reply_markup=request_review_keyboard("app", app_id))
    cur.execute("UPDATE club_applications SET admin_message_id=? WHERE id=?", (sent_msg.message_id, app_id))
    db.commit()
    await state.clear()
    await message.answer("✅ Заявка отправлена администрации на рассмотрение.", reply_markup=await main_menu_for(message.from_user.id))

@dp.callback_query(F.data == "menu_agent")
async def menu_agent(callback: CallbackQuery, state: FSMContext):
    if not await require_verified(callback): return
    remaining, why = cooldown_remaining(callback.from_user.id, "free_agent")
    if remaining:
        await callback.answer(f"Свободный агент будет доступен через {human_duration(remaining)}.", show_alert=True); return
    remaining, why = cooldown_remaining(callback.from_user.id, "career")
    if remaining:
        await callback.answer(f"После завершения карьеры вернуться можно через {human_duration(remaining)}.", show_alert=True); return
    pending = cur.execute("SELECT id FROM free_agents WHERE user_id=? AND status='pending'", (callback.from_user.id,)).fetchone()
    if pending:
        await callback.answer("Ваша заявка свободного агента уже рассматривается.", show_alert=True)
        return
    club, role = get_user_club(callback.from_user.id)
    if club and role == "owner":
        await callback.answer("Владелец клуба не может подать заявку свободного агента.", show_alert=True)
        return
    if club and role == "player":
        remaining, _ = cooldown_remaining(callback.from_user.id, "leave_club")
        if remaining:
            await callback.answer(f"После подписания выйти из клуба можно через {human_duration(remaining)}.", show_alert=True)
            return
        cur.execute("DELETE FROM club_players WHERE user_id=?", (callback.from_user.id,))
        db.commit()
        clear_cooldown(callback.from_user.id, "sign_lock")
        await safe_send(callback.from_user.id, f"Вы покинули клуб <b>{safe(club['name'])}</b> и теперь можете подать заявку свободного агента.")
    await state.set_state(UserState.waiting_ps)
    await callback.message.answer("━━━━━━━━━━━━━━━━━━\n🆓 <b>ЗАЯВКА СВОБОДНОГО АГЕНТА</b>\n━━━━━━━━━━━━━━━━━━\n\nРасскажите о себе: позиция, игровой опыт и другая полезная информация.\n\n<i>После проверки администрацией ваша анкета появится в канале трансферов.</i>\nДля отмены отправьте /cancel.")
    await callback.answer()

@dp.message(UserState.waiting_ps)
async def receive_ps(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer("Отправьте информацию текстом.")
        return
    user = get_db_user(message.from_user.id)
    cur.execute("INSERT INTO free_agents(user_id, username, roblox_nick, ps, created_at) VALUES(?,?,?,?,?)", (message.from_user.id, message.from_user.username, user["roblox_nick"], message.text.strip(), now_string()))
    agent_id = cur.lastrowid
    db.commit()
    sent = await bot.send_message(
        ADMIN_GROUP_ID,
        "━━━━━━━━━━━━━━━━━━\n"
        f"🆓 <b>ЗАЯВКА СВОБОДНОГО АГЕНТА #{agent_id}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🎮 <b>Roblox:</b> {safe(user['roblox_nick'])}\n"
        f"💬 <b>Telegram:</b> {telegram_contact(message.from_user.id, message.from_user.username)}\n"
        f"🆔 <code>{message.from_user.id}</code>\n\n"
        f"📝 <b>Анкета</b>\n{safe(message.text.strip())}",
        reply_markup=request_review_keyboard("agent", agent_id),
    )
    cur.execute("UPDATE free_agents SET admin_message_id=? WHERE id=?", (sent.message_id, agent_id))
    db.commit()
    await state.clear()
    await message.answer("✅ Заявка свободного агента отправлена администрации.", reply_markup=await main_menu_for(message.from_user.id))

@dp.callback_query(F.data == "menu_myclub")
async def menu_myclub(callback: CallbackQuery):
    if not await require_verified(callback): return
    club, role = get_user_club(callback.from_user.id)
    if not club:
        await callback.answer("Вы пока не состоите в клубе.", show_alert=True)
        return
    owner = role == "owner"
    players = cur.execute("SELECT u.* FROM club_players cp JOIN users u ON u.user_id=cp.user_id WHERE cp.club_id=? ORDER BY cp.joined_at", (club["id"],)).fetchall()
    text = f"🏟 <b>{safe(club['name'])}</b>\n\nВаша роль: {'Владелец' if owner else 'Игрок'}\nСостав: {len(players)}/{MAX_CLUB_PLAYERS}\n"
    if players:
        text += "\n<b>Игроки:</b>\n" + "\n".join(f"• {safe(person_label(p))}" for p in players)
    if owner:
        text += "\n\nКоманды владельца:\n/sign RobloxNick — предложить контракт\n/release RobloxNick — отпустить игрока"
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📣 Объявление о наборе", callback_data="club_announce")], [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")]])
    else:
        kb = back_keyboard()
    await show_screen(callback, text, kb)
    await callback.answer()

# ============================================================
# ADMIN BUTTONS + QUESTION HANDLING
# ============================================================
@dp.callback_query(F.data == "menu_admin")
async def menu_admin(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    await show_screen(callback, "🔧 <b>АДМИН-ПАНЕЛЬ</b>\n\nВыберите действие:", admin_menu())
    await callback.answer()

@dp.message(Command("admin"))
async def admin_handler(message: Message):
    if not await is_admin(message.from_user.id):
        await message.answer("❌ У вас нет доступа.")
        return
    await message.answer("🔧 <b>АДМИН-ПАНЕЛЬ</b>\n\nВыберите действие:", reply_markup=admin_menu())

@dp.callback_query(F.data == "admin_post")
async def admin_post_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True); return
    await state.set_state(AdminState.waiting_post)
    await callback.message.answer("📣 Отправьте текст поста. Он будет опубликован в официальном канале. Для отмены — /cancel.")
    await callback.answer()

@dp.message(AdminState.waiting_post)
async def admin_post_receive(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return
    if not message.text:
        await message.answer("Пока поддерживаются текстовые посты. Отправьте текст или /cancel."); return
    try:
        await bot.send_message(OFFICIAL_CHANNEL_ID, message.text)
        await message.answer("✅ Пост опубликован.")
    except Exception as exc:
        logging.exception("Ошибка публикации поста")
        await message.answer(f"❌ Не удалось опубликовать пост. Проверьте права бота и ID канала.\n{safe(exc)}")
    await state.clear()

@dp.callback_query(F.data == "admin_questions")
async def admin_questions(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True); return
    rows = cur.execute("SELECT * FROM questions WHERE status='pending' ORDER BY id DESC LIMIT 10").fetchall()
    if not rows:
        await callback.answer("Новых вопросов нет.", show_alert=True); return
    await callback.message.answer("❓ <b>Открытые вопросы</b>")
    for row in rows:
        await bot.send_message(callback.from_user.id, f"❓ <b>Вопрос #{row['id']}</b>\nПользователь: <code>{row['user_id']}</code>\n\n{safe(row['question'])}", reply_markup=question_admin_keyboard(row["id"]))
    await callback.answer("Список отправлен в личные сообщения.")

@dp.callback_query(F.data.startswith("answer_question:"))
async def answer_question_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True); return
    qid = int(callback.data.split(":")[1])
    row = cur.execute("SELECT * FROM questions WHERE id=? AND status='pending'", (qid,)).fetchone()
    if not row:
        await callback.answer("Вопрос уже закрыт.", show_alert=True); return
    await state.set_state(AdminState.waiting_question_answer)
    await state.update_data(question_id=qid)
    await callback.message.answer(f"Напишите ответ на вопрос #{qid}. Для отмены — /cancel.")
    await callback.answer()

@dp.message(AdminState.waiting_question_answer)
async def answer_question_receive(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return
    data = await state.get_data(); qid = data.get("question_id")
    row = cur.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
    if not row:
        await message.answer("Вопрос не найден."); await state.clear(); return
    cur.execute("UPDATE questions SET answer=?, status='answered' WHERE id=?", (message.text or "", qid)); db.commit()
    sent = await safe_send(row["user_id"], f"💬 <b>Ответ администрации на вопрос #{qid}</b>\n\n{safe(message.text or '')}")
    await message.answer("✅ Ответ сохранён и отправлен игроку." if sent else "⚠️ Ответ сохранён, но игроку не удалось написать в личные сообщения.")
    await state.clear()

@dp.callback_query(F.data.startswith("close_question:"))
async def close_question(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True); return
    qid = int(callback.data.split(":")[1])
    cur.execute("UPDATE questions SET status='closed' WHERE id=? AND status='pending'", (qid,)); db.commit()
    await callback.message.edit_reply_markup(reply_markup=done_keyboard("❌ Закрыто"))
    await callback.answer("Вопрос закрыт.")

@dp.callback_query(F.data == "admin_applications")
async def admin_applications(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id): await callback.answer("Нет доступа.", show_alert=True); return
    rows = cur.execute("SELECT * FROM club_applications WHERE status='pending' ORDER BY id DESC LIMIT 10").fetchall()
    if not rows: await callback.answer("Новых заявок на клуб нет.", show_alert=True); return
    for row in rows:
        answers = json.loads(row["answers"] or "[]")
        body = "\n".join(f"<b>{safe(q)}</b>\n{safe(a)}" for q, a in zip(APPLICATION_QUESTIONS, answers))
        await bot.send_message(callback.from_user.id, f"📝 <b>ЗАЯВКА #{row['id']}</b>\nИгрок: {safe(row['roblox_nick'])} (<code>{row['user_id']}</code>)\n\n{body}", reply_markup=request_review_keyboard("app", row["id"]))
    await callback.answer("Заявки отправлены в личные сообщения.")

@dp.callback_query(F.data == "admin_agents")
async def admin_agents(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id): await callback.answer("Нет доступа.", show_alert=True); return
    rows = cur.execute("SELECT * FROM free_agents WHERE status='pending' ORDER BY id DESC LIMIT 10").fetchall()
    if not rows: await callback.answer("Новых заявок свободных агентов нет.", show_alert=True); return
    for row in rows:
        await bot.send_message(callback.from_user.id, f"🆓 <b>СВОБОДНЫЙ АГЕНТ #{row['id']}</b>\nИгрок: {safe(row['roblox_nick'])} (<code>{row['user_id']}</code>)\n\n{safe(row['ps'])}", reply_markup=request_review_keyboard("agent", row["id"]))
    await callback.answer("Заявки отправлены в личные сообщения.")

@dp.callback_query(F.data.startswith("review:"))
async def review_application(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id): await callback.answer("Нет доступа.", show_alert=True); return
    _, kind, decision, item_id = callback.data.split(":")
    item_id = int(item_id)
    table = "club_applications" if kind == "app" else "free_agents"
    row = cur.execute(f"SELECT * FROM {table} WHERE id=? AND status='pending'", (item_id,)).fetchone()
    if not row: await callback.answer("Заявка уже обработана.", show_alert=True); return
    if decision == "accept":
        cur.execute(f"UPDATE {table} SET status='accepted' WHERE id=?", (item_id,)); db.commit()
        if kind == "agent":
            set_cooldown(row["user_id"], "free_agent", COOLDOWN_FREE_AGENT_SECONDS, "Повторная заявка свободного агента")
            player_data = get_db_user(row["user_id"])
            username = (player_data["username"] if player_data else None) or row["username"]
            post = (
                "📋 <b>PLAYER AVAILABLE | SL27</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"👤 Игрок: <code>{safe(row['roblox_nick'])}</code> ({'<code>@' + safe(str(username).lstrip('@')) + '</code>' if username else '<code>username не указан</code>'})\n\n"
                "🔎 Статус: <b>Ищет новый клуб</b>\n\n"
                f"📝 Информация: {safe(row['ps'] or 'Игрок не добавил описание.')}\n"
                "━━━━━━━━━━━━━━━━━━"
            )
            posted = await publish_transfer_post(post)
            note = "\nПрофиль опубликован в канале трансферов." if posted else "\nНе удалось опубликовать в канале трансферов — проверьте права бота."
            await safe_send(row["user_id"], "✅ <b>Заявка одобрена!</b>\n\nВаша анкета опубликована в канале трансферов." + ("\n\n⏳ Повторно подать заявку можно через 2 часа." if posted else "\n\n⚠️ Администрации нужно проверить права бота на публикацию."))
        else:
            await safe_send(row["user_id"], "✅ Ваша заявка одобрена администрацией." + (f"\nКлуб: {safe(row['club'])}" if kind == "app" else ""))
        await callback.message.edit_reply_markup(reply_markup=done_keyboard("✅ Принято"))
        await callback.answer("Заявка принята.")
    else:
        await state.set_state(AdminState.waiting_reason)
        await state.update_data(reason_kind=kind, reason_id=item_id, reason_admin_message=callback.message.message_id)
        await callback.message.answer(f"Напишите причину отказа по заявке #{item_id}. Она будет отправлена игроку.")
        await callback.answer()

@dp.message(AdminState.waiting_reason)
async def reject_reason_receive(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id): await state.clear(); return
    data = await state.get_data(); kind = data.get("reason_kind"); item_id = data.get("reason_id")
    table = "club_applications" if kind == "app" else "free_agents"
    row = cur.execute(f"SELECT * FROM {table} WHERE id=? AND status='pending'", (item_id,)).fetchone()
    if not row: await message.answer("Заявка уже обработана."); await state.clear(); return
    reason = (message.text or "Причина не указана").strip()
    cur.execute(f"UPDATE {table} SET status='rejected', reject_reason=? WHERE id=?", (reason, item_id)); db.commit()
    await safe_send(row["user_id"], f"❌ Ваша заявка отклонена.\nПричина: {safe(reason)}")
    await message.answer("Отказ сохранён, игрок уведомлён.")
    await state.clear()

# ============================================================
# CLUBS AND TRANSFERS
# ============================================================
@dp.message(Command("clubs"))
async def clubs_command(message: Message):
    rows = cur.execute("SELECT * FROM clubs ORDER BY name COLLATE NOCASE").fetchall()
    if not rows:
        await message.answer("Список клубов пока пуст. Администратор может добавить клуб командой /addclub Название.")
        return
    lines = ["🏟 <b>КЛУБЫ SL27</b>\n"]
    for club in rows:
        owner = cur.execute("SELECT * FROM users WHERE user_id=?", (club["owner_id"],)).fetchone() if club["owner_id"] else None
        count = cur.execute("SELECT COUNT(*) AS n FROM club_players WHERE club_id=?", (club["id"],)).fetchone()["n"]
        lines.append(f"• <b>{safe(club['name'])}</b> — владелец: {safe(person_label(owner) if owner else 'свободен')} · состав {count}/{MAX_CLUB_PLAYERS}")
    await message.answer("\n".join(lines))

@dp.message(Command("addclub"))
async def addclub_command(message: Message):
    if not await can_use_admin_command(message): await message.answer("Нет доступа."); return
    name = parse_args(message)
    if not name: await message.answer("Использование: /addclub Название клуба"); return
    try:
        cur.execute("INSERT INTO clubs(name, created_at) VALUES(?,?)", (name, now_string())); db.commit()
        await message.answer(f"Клуб <b>{safe(name)}</b> добавлен.")
    except sqlite3.IntegrityError:
        await message.answer("Такой клуб уже существует.")

@dp.message(Command("giveclub"))
async def giveclub_command(message: Message):
    if not await can_use_admin_command(message): await message.answer("Нет доступа."); return
    args = parse_args(message)
    if "|" not in args: await message.answer("Использование: /giveclub RobloxNick | Название клуба"); return
    nick, club_name = [x.strip() for x in args.split("|", 1)]
    user = find_user_by_nick(nick); club = get_club_by_name(club_name)
    if not user: await message.answer("Игрок не найден или не прошёл проверку Roblox."); return
    if not club: await message.answer("Клуб не найден. Добавьте его командой /addclub Название."); return
    if get_owned_club(user["user_id"]): await message.answer("У игрока уже есть клуб."); return
    if get_player_club(user["user_id"]): await message.answer("Игрок уже состоит в клубе. Сначала освободите его."); return
    cur.execute("UPDATE clubs SET owner_id=NULL WHERE owner_id=?", (user["user_id"],))
    cur.execute("UPDATE clubs SET owner_id=? WHERE id=?", (user["user_id"], club["id"]))
    db.commit()
    await message.answer(f"✅ Игрок <b>{safe(user['roblox_nick'])}</b> назначен владельцем клуба <b>{safe(club['name'])}</b>.")
    await safe_send(user["user_id"], f"👑 Вам выдан клуб <b>{safe(club['name'])}</b>!")

@dp.message(Command("takeclub"))
async def takeclub_command(message: Message):
    if not await can_use_admin_command(message): await message.answer("Нет доступа."); return
    arg = parse_args(message)
    if not arg: await message.answer("Использование: /takeclub RobloxNick или название клуба"); return
    club = get_club_by_name(arg)
    user = find_user_by_nick(arg)
    if club:
        owner_id = club["owner_id"]
        cur.execute("UPDATE clubs SET owner_id=NULL WHERE id=?", (club["id"],)); db.commit()
        await message.answer(f"Владелец клуба <b>{safe(club['name'])}</b> снят.")
        if owner_id: await safe_send(owner_id, f"Администрация сняла вас с должности владельца клуба <b>{safe(club['name'])}</b>.")
        return
    if user:
        owned = get_owned_club(user["user_id"])
        if not owned: await message.answer("У этого игрока нет клуба."); return
        cur.execute("UPDATE clubs SET owner_id=NULL WHERE owner_id=?", (user["user_id"],)); db.commit()
        await message.answer(f"Игрок снят с владения клубом <b>{safe(owned['name'])}</b>.")
        await safe_send(user["user_id"], "Администрация сняла вас с должности владельца клуба.")
        return
    await message.answer("Не найден клуб или зарегистрированный Roblox-ник игрока.")

@dp.message(Command("unlink"))
async def unlink_command(message: Message):
    if not await can_use_admin_command(message): await message.answer("Нет доступа."); return
    nick = parse_args(message)
    user = find_user_by_nick(nick)
    if not user: await message.answer("Проверенный игрок с таким Roblox-ником не найден."); return
    cur.execute("UPDATE users SET roblox_nick=NULL, roblox_id=NULL, verified=0 WHERE user_id=?", (user["user_id"],)); db.commit()
    await message.answer(f"Привязка Roblox для <code>{user['user_id']}</code> сброшена.")
    await safe_send(user["user_id"], "Администрация сбросила привязку Roblox. Для регистрации напишите /start.")

@dp.message(Command("sign"))
async def sign_command(message: Message):
    owner_club = get_owned_club(message.from_user.id)
    if not owner_club: await message.answer("Команда доступна только владельцу клуба."); return
    nick = parse_args(message)
    if not nick: await message.answer("Использование: /sign RobloxNick"); return
    player = find_user_by_nick(nick)
    if not player: await message.answer("Игрок не найден или не прошёл проверку Roblox."); return
    retired, _ = cooldown_remaining(player["user_id"], "career")
    if retired: await message.answer(f"Игрок завершил карьеру и сможет вернуться через {human_duration(retired)}."); return
    if player["user_id"] == message.from_user.id: await message.answer("Нельзя предложить контракт самому себе."); return
    if get_user_club(player["user_id"])[0]: await message.answer("Игрок уже состоит в клубе."); return
    remaining, _ = cooldown_remaining(player["user_id"], "sign_lock")
    if remaining: await message.answer(f"Игрок недавно покинул/сменил клуб. Другие владельцы смогут предложить контракт через {human_duration(remaining)}."); return
    count = cur.execute("SELECT COUNT(*) AS n FROM club_players WHERE club_id=?", (owner_club["id"],)).fetchone()["n"]
    if count >= MAX_CLUB_PLAYERS: await message.answer("Состав клуба уже заполнен."); return
    existing = cur.execute("SELECT id FROM sign_offers WHERE user_id=? AND status='pending'", (player["user_id"],)).fetchone()
    if existing: await message.answer("У игрока уже есть ожидающее предложение."); return
    cur.execute("INSERT INTO sign_offers(club_id, from_user_id, user_id, created_at) VALUES(?,?,?,?)", (owner_club["id"], message.from_user.id, player["user_id"], now_string())); offer_id = cur.lastrowid; db.commit()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Принять", callback_data=f"sign:accept:{offer_id}"), InlineKeyboardButton(text="❌ Отклонить", callback_data=f"sign:reject:{offer_id}")]])
    await safe_send(player["user_id"], f"⚽ Клуб <b>{safe(owner_club['name'])}</b> предлагает вам контракт. Принять?", reply_markup=kb)
    await message.answer("Предложение контракта отправлено игроку.")

@dp.callback_query(F.data.startswith("sign:"))
async def sign_response(callback: CallbackQuery):
    _, action, offer_id = callback.data.split(":"); offer_id = int(offer_id)
    offer = cur.execute("SELECT * FROM sign_offers WHERE id=? AND user_id=? AND status='pending'", (offer_id, callback.from_user.id)).fetchone()
    if not offer: await callback.answer("Предложение не найдено или уже обработано.", show_alert=True); return
    club = cur.execute("SELECT * FROM clubs WHERE id=?", (offer["club_id"],)).fetchone()
    if action == "accept":
        if get_user_club(callback.from_user.id)[0]: await callback.answer("Вы уже состоите в клубе.", show_alert=True); return
        count = cur.execute("SELECT COUNT(*) AS n FROM club_players WHERE club_id=?", (offer["club_id"],)).fetchone()["n"]
        if count >= MAX_CLUB_PLAYERS: await callback.answer("Состав клуба заполнен.", show_alert=True); return
        cur.execute("INSERT INTO club_players(club_id, user_id, joined_at) VALUES(?,?,?)", (offer["club_id"], callback.from_user.id, now_string()))
        cur.execute("UPDATE sign_offers SET status='accepted' WHERE id=?", (offer_id,)); db.commit()
        clear_cooldown(callback.from_user.id, "free_agent")
        clear_cooldown(callback.from_user.id, "career")
        set_cooldown(callback.from_user.id, "leave_club", COOLDOWN_SIGN_SECONDS, "Недавно подписан в клуб")
        set_cooldown(callback.from_user.id, "sign_lock", COOLDOWN_SIGN_SECONDS, "Недавно подписан в клуб")
        player_data = get_db_user(callback.from_user.id)
        player_nick = (player_data["roblox_nick"] if player_data else None) or callback.from_user.full_name
        player_username = callback.from_user.username or (player_data["username"] if player_data else None)
        post_ok = await publish_transfer_post(
            "⚡️ <b>TRANSFER NEWS | SL27</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"💠 Игрок: <code>{safe(player_nick)}</code> ({'<code>@' + safe(str(player_username).lstrip('@')) + '</code>' if player_username else '<code>username не указан</code>'})\n"
            f"🔄 Статус: Свободный агент → <b>{safe(club['name'])}</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Добро пожаловать в новый клуб!"
        )
        await callback.message.edit_reply_markup(reply_markup=done_keyboard("✅ Принято"))
        await callback.answer("Вы вступили в клуб!")
        await safe_send(offer["from_user_id"], f"✅ <b>Контракт принят!</b>\n\nИгрок: <b>{safe(player_nick)}</b>\nTelegram: {telegram_contact(callback.from_user.id, player_username)}\nКлуб: <b>{safe(club['name'])}</b>\n\n" + ("📢 Трансфер опубликован в канале." if post_ok else "⚠️ Не удалось опубликовать трансфер. Проверьте права бота в канале."))
    else:
        cur.execute("UPDATE sign_offers SET status='rejected' WHERE id=?", (offer_id,)); db.commit()
        await callback.message.edit_reply_markup(reply_markup=done_keyboard("❌ Отклонено")); await callback.answer("Предложение отклонено.")
        await safe_send(offer["from_user_id"], f"Игрок отклонил предложение клуба <b>{safe(club['name'])}</b>.")

@dp.message(Command("release"))
async def release_command(message: Message):
    club = get_owned_club(message.from_user.id)
    if not club: await message.answer("Команда доступна только владельцу клуба."); return
    nick = parse_args(message)
    if not nick: await message.answer("Использование: /release RobloxNick"); return
    player = find_user_by_nick(nick)
    if not player: await message.answer("Игрок не найден."); return
    row = cur.execute("SELECT id FROM club_players WHERE club_id=? AND user_id=?", (club["id"], player["user_id"])).fetchone()
    if not row: await message.answer("Этот игрок не состоит в вашем клубе."); return
    cur.execute("DELETE FROM club_players WHERE id=?", (row["id"],)); db.commit()
    clear_cooldown(player["user_id"], "leave_club")
    clear_cooldown(player["user_id"], "sign_lock")
    clear_cooldown(player["user_id"], "free_agent")
    await message.answer(f"Игрок <b>{safe(player['roblox_nick'])}</b> отпущен из клуба. Кулдауны сняты.")
    await safe_send(player["user_id"], f"Вас отпустили из клуба <b>{safe(club['name'])}</b>.")

# ============================================================
# CAREER, OWNER ANNOUNCEMENTS AND COOLDOWN ADMINISTRATION
# ============================================================
@dp.callback_query(F.data == "career_end")
async def career_end(callback: CallbackQuery):
    """Показывает подтверждение, не завершая карьеру сразу."""
    if not await require_verified(callback):
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, завершить", callback_data="career_end_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="career_end_cancel")],
    ])
    await callback.message.answer(
        "⚠️ <b>Вы уверены, что хотите завершить карьеру?</b>\n\n"
        "Вы выйдете из своего клуба, а если владеете клубом — он останется без владельца. "
        "Повторная подача заявки будет доступна через 15 дней.",
        reply_markup=keyboard,
    )
    await callback.answer()

@dp.callback_query(F.data == "career_end_cancel")
async def career_end_cancel(callback: CallbackQuery):
    await callback.message.edit_text("Действие отменено. Ваша карьера не завершена.")
    await callback.answer("Отменено")

@dp.callback_query(F.data == "career_end_confirm")
async def career_end_confirm(callback: CallbackQuery):
    if not await require_verified(callback):
        return
    owned = get_owned_club(callback.from_user.id)
    if owned:
        cur.execute("UPDATE clubs SET owner_id=NULL WHERE id=?", (owned["id"],))
        db.commit()
        await safe_send(callback.from_user.id, f"Вы отказались от владения клубом <b>{safe(owned['name'])}</b>; теперь у клуба нет владельца.")
    club = get_player_club(callback.from_user.id)
    if club:
        cur.execute("DELETE FROM club_players WHERE user_id=?", (callback.from_user.id,)); db.commit()
        await safe_send(callback.from_user.id, f"Вы вышли из клуба <b>{safe(club['name'])}</b>.")
    set_cooldown(callback.from_user.id, "career", COOLDOWN_CAREER_SECONDS, "Завершение карьеры")
    clear_cooldown(callback.from_user.id, "sign_lock")
    clear_cooldown(callback.from_user.id, "leave_club")
    await callback.message.edit_text("🏁 Вы завершили карьеру. Вернуться в лигу и снова подать заявку можно через 15 дней.")
    await callback.answer("Карьера завершена.")

@dp.callback_query(F.data == "club_announce")
async def club_announce_start(callback: CallbackQuery, state: FSMContext):
    club = get_owned_club(callback.from_user.id)
    if not club:
        await callback.answer("Кнопка доступна только владельцу клуба.", show_alert=True); return
    remaining, _ = cooldown_remaining(callback.from_user.id, "announcement")
    if remaining:
        await callback.answer(f"Новое объявление можно отправить через {human_duration(remaining)}.", show_alert=True); return
    await state.set_state(UserState.waiting_club_announcement)
    await callback.message.answer("Напишите объявление о наборе игроков или новость клуба. Оно будет опубликовано в канале трансферов. Для отмены — /cancel.")
    await callback.answer()

@dp.message(UserState.waiting_club_announcement)
async def club_announce_receive(message: Message, state: FSMContext):
    club = get_owned_club(message.from_user.id)
    if not club:
        await state.clear(); await message.answer("Вы больше не являетесь владельцем клуба."); return
    text = (message.text or "").strip()
    if not text or len(text) > 1800:
        await message.answer("Отправьте текст объявления до 1800 символов."); return
    remaining, _ = cooldown_remaining(message.from_user.id, "announcement")
    if remaining:
        await state.clear(); await message.answer(f"Объявление будет доступно через {human_duration(remaining)}."); return
    post = f"📣 <b>ОБЪЯВЛЕНИЕ КЛУБА — {safe(club['name'])}</b>\n\n{safe(text)}\n\nДля связи с владельцем: {('@' + safe(message.from_user.username)) if message.from_user.username else 'напишите владельцу в Telegram'}"
    if await publish_transfer_post(post):
        set_cooldown(message.from_user.id, "announcement", COOLDOWN_ANNOUNCEMENT_SECONDS, "Объявление клуба")
        cur.execute("INSERT INTO club_announcements(club_id,owner_id,text,created_at) VALUES(?,?,?,?)", (club["id"], message.from_user.id, text, now_string())); db.commit()
        await message.answer("✅ <b>Объявление опубликовано!</b>\n\nСледующее объявление можно отправить через 24 часа.", reply_markup=await main_menu_for(message.from_user.id))
    else:
        await message.answer("Не удалось опубликовать объявление. Проверьте, что бот является администратором канала трансферов.")
    await state.clear()

@dp.callback_query(F.data == "admin_cooldowns")
async def admin_cooldowns_menu(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True); return
    await callback.message.answer("⏳ <b>Управление кулдаунами</b>\nКоманды работают в группе администраторов:\n<code>/cooldowns RobloxNick</code> — посмотреть кулдауны\n<code>/clearcooldown RobloxNick</code> или <code>/removecooldown RobloxNick</code> — снять ВСЕ кулдауны игрока")
    await callback.answer()

@dp.message(Command("cooldowns"))
async def cooldowns_command(message: Message):
    if message.chat.id != ADMIN_GROUP_ID or not await is_admin(message.from_user.id):
        await message.answer("Эту команду можно использовать только в группе администраторов."); return
    nick = parse_args(message)
    user = find_user_by_nick(nick)
    if not user:
        await message.answer("Проверенный игрок с таким Roblox-ником не найден."); return
    rows = cur.execute("SELECT kind,until_at,reason FROM cooldowns WHERE user_id=? ORDER BY kind", (user["user_id"],)).fetchall()
    active = []
    for row in rows:
        seconds, _ = cooldown_remaining(user["user_id"], row["kind"])
        if seconds: active.append(f"• {safe(row['kind'])}: {human_duration(seconds)} — {safe(row['reason'] or '')}")
    await message.answer(f"Кулдауны <b>{safe(nick)}</b>:\n" + ("\n".join(active) if active else "Активных кулдаунов нет."))

@dp.message(Command("clearcooldown", "removecooldown"))
async def clearcooldown_command(message: Message):
    if message.chat.id != ADMIN_GROUP_ID or not await is_admin(message.from_user.id):
        await message.answer("Снимать кулдауны можно только командой в группе администраторов."); return
    nick = parse_args(message)
    user = find_user_by_nick(nick)
    if not user:
        await message.answer("Проверенный игрок с таким Roblox-ником не найден."); return
    cur.execute("DELETE FROM cooldowns WHERE user_id=?", (user["user_id"],)); db.commit()
    await message.answer(f"Все кулдауны игрока <b>{safe(nick)}</b> сняты.")
    await safe_send(user["user_id"], "Администрация сняла ваши кулдауны.")

# ============================================================
# WITHDRAWAL / MATCH SIGN-UP
# ============================================================
def get_club_status(withdrawal_id, club_number):
    row = cur.execute("SELECT status FROM withdrawal_players WHERE withdrawal_id=? AND club_number=? AND status IN ('pending','accepted','tp') ORDER BY id DESC LIMIT 1", (withdrawal_id, club_number)).fetchone()
    if not row: return "free"
    return row["status"]

def withdrawal_text(club1, club2, status1, status2):
    symbol1 = "✅" if status1 == "accepted" else "⚠️" if status1 == "tp" else "❌"
    symbol2 = "✅" if status2 == "accepted" else "⚠️" if status2 == "tp" else "❌"
    return f"╭─────── ✦ MATCH ✦ ───────╮\n\n⚽ <b>{safe(club1)}</b>   {symbol1}\n━━━━━━━━━━━━━━━━━━━━\n⚽ <b>{safe(club2)}</b>   {symbol2}\n\n╰─────────────────────────╯"

def withdrawal_keyboard(withdrawal_id, club1, club2):
    buttons = []
    for number, club in ((1, club1), (2, club2)):
        status = get_club_status(withdrawal_id, number)
        if status == "accepted":
            buttons.append([InlineKeyboardButton(text=f"✅ {club}", callback_data="already_done")])
        elif status == "tp":
            buttons.append([InlineKeyboardButton(text=f"⚠️ {club} — ТП", callback_data="already_done")])
        else:
            buttons.append([InlineKeyboardButton(text=f"⚽ Отписаться — {club}", callback_data=f"withdraw:{withdrawal_id}:{number}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def withdrawal_admin_keyboard(request_id):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_withdraw:{request_id}"), InlineKeyboardButton(text="❌ Отказать", callback_data=f"reject_withdraw:{request_id}")]])

@dp.callback_query(F.data == "admin_withdrawal")
async def admin_withdrawal_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id): await callback.answer("Нет доступа.", show_alert=True); return
    await state.set_state(AdminState.waiting_withdrawal)
    await callback.message.answer("Отправьте названия двух клубов через |\nПример: Benfica | Brentford\nДля отмены — /cancel.")
    await callback.answer()

@dp.message(AdminState.waiting_withdrawal)
async def admin_withdrawal_receive(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id): await state.clear(); return
    if "|" not in (message.text or ""):
        await message.answer("Формат: Клуб 1 | Клуб 2"); return
    club1, club2 = [x.strip() for x in message.text.split("|", 1)]
    if not club1 or not club2 or club1.casefold() == club2.casefold():
        await message.answer("Укажите два разных названия клубов."); return
    cur.execute("INSERT INTO withdrawals(club1, club2, created_at) VALUES(?,?,?)", (club1, club2, now_string())); wid = cur.lastrowid; db.commit()
    text = withdrawal_text(club1, club2, "pending", "pending")
    try:
        posted = await bot.send_message(OFFICIAL_CHANNEL_ID, text, reply_markup=withdrawal_keyboard(wid, club1, club2))
        cur.execute("UPDATE withdrawals SET channel_message_id=? WHERE id=?", (posted.message_id, wid)); db.commit()
        await message.answer(f"Отписка создана и опубликована. ID: {wid}")
    except Exception as exc:
        await message.answer(f"Не удалось опубликовать отписку. Проверьте права бота.\n{safe(exc)}")
    await state.clear()

@dp.callback_query(F.data.startswith("withdraw:"))
async def withdraw_signup(callback: CallbackQuery):
    if not await require_verified(callback): return
    _, wid, club_number = callback.data.split(":"); wid, club_number = int(wid), int(club_number)
    match = cur.execute("SELECT * FROM withdrawals WHERE id=? AND status='open'", (wid,)).fetchone()
    if not match: await callback.answer("Отписка закрыта.", show_alert=True); return
    club_name = match["club1"] if club_number == 1 else match["club2"]
    existing = cur.execute("SELECT id FROM withdrawal_players WHERE withdrawal_id=? AND club_number=? AND status IN ('pending','accepted','tp')", (wid, club_number)).fetchone()
    if existing: await callback.answer("Этот клуб уже отписан или обрабатывается.", show_alert=True); return
    cur.execute("INSERT INTO withdrawal_players(withdrawal_id,user_id,username,club,club_number,status,stage,created_at) VALUES(?,?,?,?,?,'pending','pending',?)", (wid, callback.from_user.id, callback.from_user.username, club_name, club_number, now_string())); pid = cur.lastrowid; db.commit()
    sent = await safe_send(ADMIN_GROUP_ID, f"📢 <b>ЗАПРОС НА ОТПИСКУ #{pid}</b>\nМатч: {safe(match['club1'])} — {safe(match['club2'])}\nКлуб: <b>{safe(club_name)}</b>\nИгрок: <code>{callback.from_user.id}</code>", reply_markup=withdrawal_admin_keyboard(pid))
    await callback.answer("Заявка отправлена администрации." if sent else "Заявка сохранена, но уведомить админов не удалось.", show_alert=True)

@dp.callback_query(F.data.startswith("accept_withdraw:"))
async def accept_withdraw(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id): await callback.answer("Нет доступа.", show_alert=True); return
    pid = int(callback.data.split(":")[1])
    row = cur.execute("SELECT * FROM withdrawal_players WHERE id=? AND status='pending'", (pid,)).fetchone()
    if not row: await callback.answer("Уже обработано.", show_alert=True); return
    cur.execute("UPDATE withdrawal_players SET status='accepted', stage='accepted', accepted_at=? WHERE id=?", (now_string(), pid)); db.commit()
    await safe_send(row["user_id"], f"✅ Отписка клуба <b>{safe(row['club'])}</b> принята. Отправьте подтверждение матча администрации в течение {WITHDRAWAL_TIMEOUT_MINUTES} минут.")
    await callback.message.edit_reply_markup(reply_markup=done_keyboard("✅ Принято")); await callback.answer("Принято.")

@dp.callback_query(F.data.startswith("reject_withdraw:"))
async def reject_withdraw(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id): await callback.answer("Нет доступа.", show_alert=True); return
    pid = int(callback.data.split(":")[1])
    row = cur.execute("SELECT * FROM withdrawal_players WHERE id=? AND status='pending'", (pid,)).fetchone()
    if not row: await callback.answer("Уже обработано.", show_alert=True); return
    cur.execute("UPDATE withdrawal_players SET status='rejected', stage='rejected' WHERE id=?", (pid,)); db.commit()
    await safe_send(row["user_id"], f"❌ Отписка клуба <b>{safe(row['club'])}</b> отклонена администрацией.")
    await callback.message.edit_reply_markup(reply_markup=done_keyboard("❌ Отказано")); await callback.answer("Отклонено.")

# Submissions from players after an accepted withdrawal are forwarded to admins.
@dp.message(F.photo)
async def receive_photo_submission(message: Message):
    if message.chat.type != ChatType.PRIVATE or not is_verified(message.from_user.id):
        return
    row = cur.execute("SELECT * FROM withdrawal_players WHERE user_id=? AND status='accepted' AND stage='accepted' ORDER BY id DESC LIMIT 1", (message.from_user.id,)).fetchone()
    if not row:
        return
    photo_id = message.photo[-1].file_id
    cur.execute("INSERT INTO submissions(user_id, club, submission_type, photo_id, caption, created_at) VALUES(?,?,?,?,?,?)", (message.from_user.id, row["club"], "photo", photo_id, message.caption, now_string())); db.commit()
    try:
        await bot.send_photo(ADMIN_GROUP_ID, photo_id, caption=f"📎 Подтверждение матча\nКлуб: {safe(row['club'])}\nИгрок: <code>{message.from_user.id}</code>\nID отписки: {row['id']}")
        await message.answer("✅ Подтверждение отправлено администрации.")
    except Exception:
        await message.answer("Не удалось переслать подтверждение. Попробуйте позже.")

@dp.message(F.video)
async def receive_video_submission(message: Message):
    if message.chat.type != ChatType.PRIVATE or not is_verified(message.from_user.id):
        return
    row = cur.execute("SELECT * FROM withdrawal_players WHERE user_id=? AND status='accepted' AND stage='accepted' ORDER BY id DESC LIMIT 1", (message.from_user.id,)).fetchone()
    if not row:
        return
    video_id = message.video.file_id
    cur.execute("INSERT INTO submissions(user_id, club, submission_type, text, caption, created_at) VALUES(?,?,?,?,?,?)", (message.from_user.id, row["club"], "video", video_id, message.caption, now_string())); db.commit()
    try:
        await bot.send_video(ADMIN_GROUP_ID, video_id, caption=f"📎 Подтверждение матча\nКлуб: {safe(row['club'])}\nИгрок: <code>{message.from_user.id}</code>\nID отписки: {row['id']}")
        await message.answer("✅ Видео отправлено администрации.")
    except Exception:
        await message.answer("Не удалось переслать видео. Попробуйте позже.")

# ============================================================
# BASIC COMMANDS
# ============================================================
@dp.message(Command("myid"))
async def myid_handler(message: Message):
    await message.answer(f"🆔 Ваш Telegram ID: <code>{message.from_user.id}</code>")

@dp.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext):
    await state.clear()
    if not is_verified(message.from_user.id):
        await message.answer("Действие отменено. Для регистрации нажмите /start.")
    else:
        await message.answer("Действие отменено.", reply_markup=await main_menu_for(message.from_user.id))

@dp.message(Command("help"))
async def help_handler(message: Message):
    lines = ["ℹ️ <b>SL27 BOT</b>", "/start — главное меню", "/myid — ваш Telegram ID", "/clubs — список клубов", "/cancel — отменить действие", "/help — помощь"]
    if get_owned_club(message.from_user.id):
        lines += ["", "👑 <b>Владелец клуба:</b>", "/sign RobloxNick — предложить контракт", "/release RobloxNick — отпустить игрока", "Кнопка «Объявление о наборе» — публикация в канале трансферов"]
    if await is_admin(message.from_user.id):
        lines += ["", "🔧 <b>Администратор:</b>", "/admin — админ-панель", "/addclub Название — добавить клуб", "/giveclub RobloxNick | Клуб — назначить владельца", "/takeclub RobloxNick или клуб — снять владельца", "/unlink RobloxNick — сбросить привязку"]
    await message.answer("\n".join(lines))

async def main():
    logging.info("SL27 BOT запускается")
    try:
        await dp.start_polling(bot)
    finally:
        db.close()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
