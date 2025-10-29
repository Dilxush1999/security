import asyncio
import os
import json
import io
from dotenv import load_dotenv
import sqlite3
import pandas as pd
from openpyxl import Workbook
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BufferedInputFile
from datetime import datetime, timedelta
import pytz
import time
import re  # Link tekshirish uchun

load_dotenv()
API_TOKEN = os.getenv("BOT_TOKEN")

ADMIN_IDS = []
welcome_settings = {}
joined_times = {}

class WelcomeStates(StatesGroup):
    waiting_for_message = State()
    waiting_for_duration = State()

class GroupSelectionStates(StatesGroup):
    waiting_for_group_id = State()

def load_config():
    global ADMIN_IDS, welcome_settings
    default_welcome = {
        "enabled": True,
        "message": "Xush kelibsiz! Guruh qoidalarini o'qing va hurmat bilan muloqot qiling.",
        "mute_enabled": True,
        "mute_duration": 300
    }
    if os.path.exists('config.json'):
        with open('config.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
        ADMIN_IDS = data.get("ADMIN_IDS", [1223308504])
        loaded_welcome = data.get("welcome_settings", {})
        welcome_settings = {**default_welcome, **loaded_welcome}
    else:
        ADMIN_IDS = [1223308504]
        welcome_settings = default_welcome
        save_config()

def save_config():
    data = {
        "ADMIN_IDS": ADMIN_IDS,
        "welcome_settings": welcome_settings
    }
    with open('config.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def init_db():
    conn = sqlite3.connect("groups.db")
    cursor = conn.cursor()
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS groups (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, chat_id INTEGER NOT NULL UNIQUE)"
    )
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, group_id INTEGER, user_id INTEGER, type TEXT, banned_item TEXT, details TEXT)"
    )
    conn.commit()
    return conn

def create_empty_excel(file_path):
    try:
        wb = Workbook()
        wb.save(file_path)
        print(f"{file_path} fayli yaratildi.")
    except Exception as e:
        print(f"{file_path} faylini yaratishda xato: {e}")

def check_and_create_files():
    files = ["taqiq.xlsx", "taqiq_audio.xlsx", "all.xlsx"]
    for file in files:
        if not os.path.exists(file):
            create_empty_excel(file)
    os.makedirs("groups", exist_ok=True)

def add_group(conn, name, chat_id):
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO groups (name, chat_id) VALUES (?, ?)", (name, chat_id))
        conn.commit()
        # Default settings yaratish
        get_group_settings(chat_id)
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        # Agar allaqachon mavjud bo'lsa, default settings tekshirish
        get_group_settings(chat_id)
        return None

def get_group_by_id(conn, gid):
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM groups WHERE id = ?", (gid,))
    return cursor.fetchone()

def get_group_by_chat_id(conn, chat_id):
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM groups WHERE chat_id = ?", (chat_id,))
    return cursor.fetchone()

def get_all_groups(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM groups")
    return cursor.fetchall()

def get_group_settings(chat_id):
    file_path = f"groups/{chat_id}.json"
    default_settings = {
        "text": True,
        "photo": True,
        "video": True,
        "sticker": True,
        "voice": True,
        "audio": True,
        "document": True,
        "link": True,
        "poll": True,
        "file": True
    }
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            settings = json.load(f)
            # Default bilan merge
            for key, value in default_settings.items():
                if key not in settings:
                    settings[key] = value
            return settings
    else:
        save_group_settings(chat_id, default_settings)
        return default_settings

def save_group_settings(chat_id, settings):
    file_path = f"groups/{chat_id}.json"
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=4, ensure_ascii=False)

def update_all_groups(key, value):
    groups = get_all_groups(conn)
    for group in groups:
        chat_id = group[2]
        settings = get_group_settings(chat_id)
        settings[key] = value
        save_group_settings(chat_id, settings)

def load_banned_words(file_path="taqiq.xlsx"):
    try:
        df = pd.read_excel(file_path, usecols=[0], header=None)
        words = df[0].dropna().str.lower().str.strip().tolist()
        print(f"Yuklangan taqiqlangan so'zlar: {words}")
        return words
    except Exception as e:
        print(f"Taqiqlangan so'zlar yuklashda xato: {e}")
        return []

def load_banned_audio_names(file_path="taqiq_audio.xlsx"):
    try:
        df = pd.read_excel(file_path, usecols=[0], header=None)
        audios = df[0].dropna().str.lower().str.strip().tolist()
        print(f"Yuklangan taqiqlangan audio nomlari: {audios}")
        return audios
    except Exception as e:
        print(f"Taqiqlangan audio yuklashda xato: {e}")
        return []

def load_banned_file_names(file_path="all.xlsx"):
    try:
        df = pd.read_excel(file_path, usecols=[0], header=None)
        files = df[0].dropna().str.lower().str.strip().tolist()
        print(f"Yuklangan taqiqlangan fayl nomlari: {files}")
        return files
    except Exception as e:
        print(f"Taqiqlangan fayllar yuklashda xato: {e}")
        return []

BANNED_WORDS = load_banned_words()
BANNED_AUDIO_NAMES = load_banned_audio_names()
BANNED_FILE_NAMES = load_banned_file_names()

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)
conn = init_db()

def log_banned_event(group_id, user_id, event_type, banned_item, details=""):
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO logs (group_id, user_id, type, banned_item, details) VALUES (?, ?, ?, ?, ?)",
        (group_id, user_id, event_type, banned_item, details)
    )
    conn.commit()

@router.message(F.new_chat_members)
async def on_new_member_join(message: types.Message):
    if message.chat.type in ("group", "supergroup"):
        me = await bot.get_me()
        try:
            chat_member = await bot.get_chat_member(message.chat.id, me.id)
            if chat_member.status not in ("administrator", "creator"):
                return
        except Exception:
            return

        if welcome_settings["enabled"]:
            welcome_msg = welcome_settings["message"]
            await message.reply(welcome_msg)

        if welcome_settings["mute_enabled"]:
            for member in message.new_chat_members:
                if member.id != me.id:  # Bot o'zini mute qilmasin
                    try:
                        current_time = int(time.time())
                        until_date = current_time + welcome_settings["mute_duration"]
                        print(f"Mute qilinmoqda: user {member.id} gacha {until_date} (hozir {current_time}), duration: {welcome_settings['mute_duration']}")
                        await bot.restrict_chat_member(
                            message.chat.id,
                            member.id,
                            permissions=types.ChatPermissions(can_send_messages=False),
                            until_date=until_date
                        )
                        print(f"Yangi a'zo {member.id} {welcome_settings['mute_duration']} sekundga mute qilindi. Until: {until_date}")
                    except Exception as e:
                        print(f"Mute qilishda xato: {e}")

    for member in message.new_chat_members:
        if member.id == (await bot.get_me()).id:
            group_name = message.chat.title or "Noma'lum guruh"
            group_id = add_group(conn, group_name, message.chat.id)
            if group_id:
                joined_times[message.chat.id] = message.date.timestamp()
                await message.reply(f"Men guruhga qo'shildim! Guruh tartib raqami: {group_id}")
            else:
                await message.reply("Men allaqachon ushbu guruhda!")

@router.message(Command("start"))
async def send_welcome(message: types.Message):
    if message.from_user.id in ADMIN_IDS:
        try:
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="Guruhlar soni", callback_data="group_count")],
                [types.InlineKeyboardButton(text="Guruhlar ro'yxati", callback_data="groups_list_cb")],
                [types.InlineKeyboardButton(text="Statistika", callback_data="stats_cb")],
                [types.InlineKeyboardButton(text="Guruhdagi ta'qiqlar", callback_data="show_group_restrictions")],
                [types.InlineKeyboardButton(text="Welcome sozlamalari", callback_data="show_welcome_settings")]
            ])
            await message.reply("Admin panelga xush kelibsiz!", reply_markup=keyboard)
        except Exception as e:
            await message.reply(f"Admin panelni ochishda xatolik: {str(e)}")
            print(f"Admin panel da xato: {e}")
    else:
        if message.chat.type == "private":
            bot_info = await bot.get_me()
            add_to_group_url = f"https://t.me/{bot_info.username}?startgroup=true"
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="Guruhga qo'shish", url=add_to_group_url)],
                [types.InlineKeyboardButton(text="Yordam", callback_data="help")],
                [types.InlineKeyboardButton(text="Sozlamalar", callback_data="settings")]
            ])
            await message.reply(
                "Iltimos, meni guruhga qo'shib adminlik bering.",
                reply_markup=keyboard
            )
        elif message.chat.type in ("group", "supergroup"):
            me = await bot.get_me()
            try:
                chat_member = await bot.get_chat_member(message.chat.id, me.id)
                if chat_member.status not in ("administrator", "creator"):
                    await message.reply("Iltimos, meni guruhda admin qiling!")
                else:
                    await message.reply("Ushbu bot ish faoliyatida!")
            except Exception as e:
                await message.reply(f"Xatolik yuz berdi: {str(e)}")
                print(f"/start da xato: {e}")

@router.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.reply("Faqat adminlar uchun!")
        return
    try:
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="Guruhlar soni", callback_data="group_count")],
            [types.InlineKeyboardButton(text="Guruhlar ro'yxati", callback_data="groups_list_cb")],
            [types.InlineKeyboardButton(text="Statistika", callback_data="stats_cb")],
            [types.InlineKeyboardButton(text="Guruhdagi ta'qiqlar", callback_data="show_group_restrictions")],
            [types.InlineKeyboardButton(text="Welcome sozlamalari", callback_data="show_welcome_settings")]
        ])
        await message.reply("Admin panelga xush kelibsiz!", reply_markup=keyboard)
    except Exception as e:
        await message.reply(f"Admin panelni ochishda xatolik: {str(e)}")
        print(f"Admin panel da xato: {e}")

@router.message(Command("stats"))
async def stats_command(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.reply("Faqat adminlar uchun!")
        return

    cursor = conn.cursor()
    # Oxirgi 24 soat
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("SELECT COUNT(*) FROM logs WHERE timestamp > ?", (yesterday,))
    total_today = cursor.fetchone()[0]

    # Turlarga ko'ra
    cursor.execute("SELECT type, COUNT(*) FROM logs WHERE timestamp > ? GROUP BY type", (yesterday,))
    type_stats = cursor.fetchall()

    # Eng ko'p taqiqlangan item
    cursor.execute("SELECT banned_item, COUNT(*) FROM logs WHERE timestamp > ? GROUP BY banned_item ORDER BY COUNT(*) DESC LIMIT 5", (yesterday,))
    top_banned = cursor.fetchall()

    stats_text = f"Statistika (oxirgi 24 soat):\nJami taqiqlangan: {total_today}\n\nTurlarga ko'ra:\n"
    for t, count in type_stats:
        stats_text += f"{t.capitalize()}: {count}\n"

    stats_text += "\nEng ko'p taqiqlanganlar:\n"
    for item, count in top_banned:
        stats_text += f"{item}: {count}\n"

    await message.reply(stats_text)

@router.callback_query(F.data == "stats_cb")
async def stats_callback(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Faqat adminlar uchun!", show_alert=True)
        return
    cursor = conn.cursor()
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("SELECT COUNT(*) FROM logs WHERE timestamp > ?", (yesterday,))
    total_today = cursor.fetchone()[0]

    cursor.execute("SELECT type, COUNT(*) FROM logs WHERE timestamp > ? GROUP BY type", (yesterday,))
    type_stats = cursor.fetchall()

    cursor.execute("SELECT banned_item, COUNT(*) FROM logs WHERE timestamp > ? GROUP BY banned_item ORDER BY COUNT(*) DESC LIMIT 5", (yesterday,))
    top_banned = cursor.fetchall()

    stats_text = f"Statistika (oxirgi 24 soat):\nJami taqiqlangan: {total_today}\n\nTurlarga ko'ra:\n"
    for t, count in type_stats:
        stats_text += f"{t.capitalize()}: {count}\n"

    stats_text += "\nEng ko'p taqiqlanganlar:\n"
    for item, count in top_banned:
        stats_text += f"{item}: {count}\n"

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Orqaga", callback_data="back_admin")]
    ])
    await callback.message.edit_text(stats_text, reply_markup=keyboard)
    await callback.answer()

@router.message(WelcomeStates.waiting_for_message)
async def process_welcome_message(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.reply("Faqat adminlar uchun!")
        await state.clear()
        return
    welcome_settings["message"] = message.text
    save_config()
    await message.reply(f"Welcome matni yangilandi: {message.text}")
    await state.clear()
    await show_welcome_settings_from_message(message)

@router.message(WelcomeStates.waiting_for_duration)
async def process_mute_duration(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.reply("Faqat adminlar uchun!")
        await state.clear()
        return
    try:
        duration = int(message.text)
        if duration < 30:
            await message.reply("Minimal mute vaqti 30 soniya bo'lishi kerak! (Telegram qoidalari bo'yicha)")
            await state.clear()
            return
        if duration > 366 * 24 * 3600:
            await message.reply("Maksimal mute vaqti 366 kun bo'lishi kerak! (Telegram qoidalari bo'yicha)")
            await state.clear()
            return
        welcome_settings["mute_duration"] = duration
        save_config()
        await message.reply(f"Mute vaqti yangilandi: {duration} sekund")
        await state.clear()
        await show_welcome_settings_from_message(message)
    except ValueError:
        await message.reply("Iltimos, raqam kiriting!")
        await state.clear()

async def show_welcome_settings_from_message(message: types.Message):
    status = "✅" if welcome_settings["enabled"] else "❌"
    mute_status = "✅" if welcome_settings["mute_enabled"] else "❌"
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=f"Welcome yoqish/o'chirish {status}", callback_data="toggle_welcome")],
        [types.InlineKeyboardButton(text=f"Yangi a'zolar mute {mute_status}", callback_data="toggle_mute")],
        [types.InlineKeyboardButton(text="Matn o'zgartirish", callback_data="edit_welcome_msg")],
        [types.InlineKeyboardButton(text="Mute vaqti o'zgartirish", callback_data="edit_mute_duration")],
        [types.InlineKeyboardButton(text="Orqaga", callback_data="back_admin")]
    ])
    await message.reply(
        f"Welcome sozlamalari:\nEnabled: {status}\nMute: {mute_status}\nMatn: {welcome_settings['message'][:50]}...\nMute vaqti: {welcome_settings['mute_duration']} sekund",
        reply_markup=keyboard
    )

@router.callback_query(F.data == "help")
async def help_callback(callback: types.CallbackQuery):
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Orqaga", callback_data="back")]
    ])
    try:
        await callback.message.edit_text(
            "Bot taqiqlangan so'zlar, audio va fayllarni guruhda tekshiradi.\n"
            "Buyruqlar:\n/start - Boshlash\n/update_lists - Ro'yxatni yangilash\n/admin - Admin panel\n/stats - Statistika\n/groups - Guruhlar ro'yxati",
            reply_markup=keyboard
        )
        await callback.answer()
    except Exception as e:
        await callback.answer(f"Xatolik: {str(e)}", show_alert=True)
        print(f"Help callback da xato: {e}")

@router.callback_query(F.data == "settings")
async def settings_callback(callback: types.CallbackQuery):
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Orqaga", callback_data="back")]
    ])
    try:
        await callback.message.edit_text(
            "Sozlamalar hozircha mavjud emas.",
            reply_markup=keyboard
        )
        await callback.answer()
    except Exception as e:
        await callback.answer(f"Xatolik: {str(e)}", show_alert=True)
        print(f"Settings callback da xato: {e}")

@router.callback_query(F.data == "back")
async def back_callback(callback: types.CallbackQuery):
    if callback.from_user.id in ADMIN_IDS:
        try:
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="Guruhlar soni", callback_data="group_count")],
                [types.InlineKeyboardButton(text="Guruhlar ro'yxati", callback_data="groups_list_cb")],
                [types.InlineKeyboardButton(text="Statistika", callback_data="stats_cb")],
                [types.InlineKeyboardButton(text="Guruhdagi ta'qiqlar", callback_data="show_group_restrictions")],
                [types.InlineKeyboardButton(text="Welcome sozlamalari", callback_data="show_welcome_settings")]
            ])
            await callback.message.edit_text(
                "Admin panelga xush kelibsiz!",
                reply_markup=keyboard
            )
            await callback.answer()
        except Exception as e:
            await callback.answer(f"Xatolik: {str(e)}", show_alert=True)
            print(f"Back admin da xato: {e}")
    else:
        bot_info = await bot.get_me()
        add_to_group_url = f"https://t.me/{bot_info.username}?startgroup=true"
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="Guruhga qo'shish", url=add_to_group_url)],
            [types.InlineKeyboardButton(text="Yordam", callback_data="help")],
            [types.InlineKeyboardButton(text="Sozlamalar", callback_data="settings")]
        ])
        try:
            await callback.message.edit_text(
                "Iltimos, meni guruhga qo'shib adminlik bering.",
                reply_markup=keyboard
            )
            await callback.answer()
        except Exception as e:
            await callback.answer(f"Xatolik: {str(e)}", show_alert=True)
            print(f"Back callback da xato: {e}")

@router.message(F.text | F.audio | F.document | F.video | F.animation | F.voice | F.photo | F.sticker | F.poll)
async def check_messages(message: types.Message):
    if message.chat.type in ("group", "supergroup"):
        me = await bot.get_me()
        try:
            chat_member = await bot.get_chat_member(message.chat.id, me.id)
            if chat_member.status not in ("administrator", "creator"):
                return
        except Exception as e:
            print(f"Adminlik tekshirishda xato: {e}")
            return

        # Admin emasligini tekshirish
        if message.from_user.id in ADMIN_IDS:
            return  # Adminlar taqiqlanmaydi

        group_name = message.chat.title or "Noma'lum guruh"
        group_id = message.chat.id
        group_username = f"@{message.chat.username}" if message.chat.username else "N/A"
        user_id = message.from_user.id
        username = f"@{message.from_user.username}" if message.from_user.username else "N/A"
        joined_time = joined_times.get(message.chat.id, 0)
        is_after_join = message.date.timestamp() > joined_time
        # Vaqtni UTC+05:00 (Toshkent) ga moslash
        tz = pytz.timezone("Asia/Tashkent")
        message_time = message.date.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")

        group_settings = get_group_settings(group_id)
        action = "keep"  # Default
        msg_type = "text"

        # Taqiqlangan so'zlar tekshiruvi (text uchun)
        if message.text:
            text_lower = message.text.lower()
            words = text_lower.split()
            for word in BANNED_WORDS:
                if word in words:
                    log_banned_event(group_id, user_id, "text", word, message.text)
                    for admin_id in ADMIN_IDS:
                        try:
                            await bot.send_message(
                                admin_id,
                                f"Guruhda taqiqlangan so‘z aniqlandi!\nGuruh nomi: {group_name}\nGuruh ID: {group_id}\nGuruh username: {group_username}\nFoydalanuvchi ID: {user_id}\nUsername: {username}\nSo‘z: {word}\nXabar: {message.text}\nVaqt: {message_time}"
                            )
                            await bot.forward_message(admin_id, message.chat.id, message.message_id)
                        except Exception as e:
                            print(f"Adminlarga xabar yuborishda xato: {e}")
                    if not group_settings.get("text", True):
                        action = "delete"
                    msg_type = "text"
                    break

            # Link tekshiruvi
            if action == "keep" and any(entity.type == "url" for entity in (message.entities or [])):
                log_banned_event(group_id, user_id, "link", "URL", message.text)
                for admin_id in ADMIN_IDS:
                    try:
                        await bot.send_message(
                            admin_id,
                            f"Guruhda taqiqlangan link aniqlandi!\nGuruh nomi: {group_name}\nGuruh ID: {group_id}\nGuruh username: {group_username}\nFoydalanuvchi ID: {user_id}\nUsername: {username}\nXabar: {message.text}\nVaqt: {message_time}"
                        )
                        await bot.forward_message(admin_id, message.chat.id, message.message_id)
                    except Exception as e:
                        print(f"Adminlarga link xabar yuborishda xato: {e}")
                if not group_settings.get("link", True):
                    action = "delete"
                msg_type = "link"
            else:
                if not group_settings.get("text", True):
                    action = "delete"

        # Boshqa turlar
        elif message.photo:
            if not group_settings.get("photo", True):
                action = "delete"
            msg_type = "photo"
        elif message.video:
            if not group_settings.get("video", True):
                action = "delete"
            msg_type = "video"
        elif message.sticker:
            if not group_settings.get("sticker", True):
                action = "delete"
            msg_type = "sticker"
        elif message.voice:
            if not group_settings.get("voice", True):
                action = "delete"
            msg_type = "voice"
        elif message.audio:
            if not group_settings.get("audio", True):
                action = "delete"
            msg_type = "audio"
            # Audio nomini tekshirish (musiqa uchun)
            if message.audio and message.audio.title:
                base_name = message.audio.title.lower().strip()
                base_words = base_name.split()
                for banned in BANNED_AUDIO_NAMES:
                    if banned in base_words:
                        log_banned_event(group_id, user_id, "audio", banned, message.audio.title)
                        for admin_id in ADMIN_IDS:
                            try:
                                await bot.send_message(
                                    admin_id,
                                    f"Guruhda taqiqlangan audio aniqlandi!\nGuruh nomi: {group_name}\nGuruh ID: {group_id}\nGuruh username: {group_username}\nFoydalanuvchi ID: {user_id}\nUsername: {username}\nAudio: {message.audio.title}\nTaqiqlangan: {banned}\nVaqt: {message_time}"
                                )
                                await bot.forward_message(admin_id, message.chat.id, message.message_id)
                            except Exception as e:
                                print(f"Adminlarga audio yuborishda xato: {e}")
                        break
        elif message.document:
            file_name = message.document.file_name or "Noma'lum fayl"
            base_name = os.path.splitext(file_name)[0].lower().strip()
            base_words = base_name.split()
            for banned in BANNED_FILE_NAMES:
                if banned in base_words:
                    log_banned_event(group_id, user_id, "document", banned, file_name)
                    for admin_id in ADMIN_IDS:
                        try:
                            await bot.send_message(
                                admin_id,
                                f"Guruhda taqiqlangan fayl aniqlandi!\nGuruh nomi: {group_name}\nGuruh ID: {group_id}\nGuruh username: {group_username}\nFoydalanuvchi ID: {user_id}\nUsername: {username}\nFayl: {file_name}\nTaqiqlangan: {banned}\nVaqt: {message_time}"
                            )
                            await bot.forward_message(admin_id, message.chat.id, message.message_id)
                        except Exception as e:
                            print(f"Adminlarga fayl yuborishda xato: {e}")
                    if not group_settings.get("document", True):
                        action = "delete"
                    break
            else:
                if not group_settings.get("file", True):
                    action = "delete"
            msg_type = "document"
        elif message.poll:
            if not group_settings.get("poll", True):
                action = "delete"
            msg_type = "poll"
        else:
            return  # Noma'lum tur

        # Action bo'yicha bajarish
        if action == "delete":
            try:
                await bot.delete_message(message.chat.id, message.message_id)
                print(f"{msg_type} o'chirildi")
                if is_after_join:
                    await message.reply(f"{msg_type.capitalize()} yuborish taqiqlangan! Xabar o'chirildi.")
            except Exception as e:
                print(f"{msg_type} o'chirishda xato: {e}")

@router.message(Command("update_lists"))
async def update_lists(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.reply("Faqat adminlar uchun!")
        return
    global BANNED_WORDS, BANNED_AUDIO_NAMES, BANNED_FILE_NAMES
    BANNED_WORDS = load_banned_words()
    BANNED_AUDIO_NAMES = load_banned_audio_names()
    BANNED_FILE_NAMES = load_banned_file_names()
    await message.reply(f"Taqiqlangan ro'yxatlar yangilandi!\nSo'zlar: {len(BANNED_WORDS)} ta\nAudio: {len(BANNED_AUDIO_NAMES)} ta\nFayllar: {len(BANNED_FILE_NAMES)} ta")

@router.message(Command("groups"))
async def groups_list(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.reply("Faqat adminlar uchun!")
        return
    groups = get_all_groups(conn)
    if not groups:
        await message.reply("Hozircha guruhlar yo'q.")
        return
    df = pd.DataFrame(groups, columns=['Tartib raqami', 'Guruh nomi', 'Guruh ID si'])
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False, encoding='utf-8')
    csv_buffer.seek(0)
    await message.reply_document(
        BufferedInputFile(csv_buffer.getvalue().encode('utf-8'), filename='guruhlar.csv'),
        caption=f"Jami {len(groups)} ta guruh ma'lumotlari fayl sifatida yuborildi."
    )

@router.callback_query(F.data == "groups_list_cb")
async def groups_list_cb(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Faqat adminlar uchun!", show_alert=True)
        return
    groups = get_all_groups(conn)
    if not groups:
        await callback.message.edit_text("Hozircha guruhlar yo'q.")
        await callback.answer()
        return
    df = pd.DataFrame(groups, columns=['Tartib raqami', 'Guruh nomi', 'Guruh ID si'])
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False, encoding='utf-8')
    csv_buffer.seek(0)
    await callback.message.reply_document(
        BufferedInputFile(csv_buffer.getvalue().encode('utf-8'), filename='guruhlar.csv'),
        caption=f"Jami {len(groups)} ta guruh ma'lumotlari fayl sifatida yuborildi."
    )
    await callback.answer("Guruhlar ro'yxati yuborildi!")

@router.callback_query(F.data == "group_count")
async def show_group_count(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Faqat adminlar uchun!", show_alert=True)
        return
    try:
        groups = get_all_groups(conn)
        total_groups = len(groups)
        admin_groups = 0
        me = await bot.get_me()
        for group in groups:
            chat_id = group[2]
            try:
                chat_member = await bot.get_chat_member(chat_id, me.id)
                if chat_member.status in ("administrator", "creator"):
                    admin_groups += 1
            except Exception as e:
                print(f"Guruh {chat_id} tekshirishda xato: {e}")
                continue
        await callback.message.edit_text(
            f"Jami guruhlar: {total_groups}\nAdminlik berilgan guruhlar: {admin_groups}",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="Orqaga", callback_data="back_admin")]
            ])
        )
        await callback.answer("Ma'lumot yangilandi!")
    except Exception as e:
        await callback.answer(f"Guruhlar ro'yxatini olishda xatolik: {str(e)}", show_alert=True)
        print(f"Guruhlar callback da xato: {e}")

# Guruh taqiqlari
@router.callback_query(F.data == "show_group_restrictions")
async def show_group_restrictions(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Faqat adminlar uchun!", show_alert=True)
        return
    if callback.message.chat.type == "private":
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="Barcha guruhlar uchun", callback_data="all_groups_restrictions")],
            [types.InlineKeyboardButton(text="Bitta guruh uchun", callback_data="select_single_group")],
            [types.InlineKeyboardButton(text="Orqaga", callback_data="back_admin")]
        ])
        await callback.message.edit_text(
            "Guruh taqiqlarini tanlang:",
            reply_markup=keyboard
        )
    else:
        chat_id = callback.message.chat.id
        await show_restrictions_for_group(callback, chat_id)
    await callback.answer()

@router.callback_query(F.data == "select_single_group")
async def select_single_group(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Faqat adminlar uchun!", show_alert=True)
        return
    await callback.message.edit_text("Guruh ID sini kiriting:")
    await state.set_state(GroupSelectionStates.waiting_for_group_id)
    await callback.answer()

@router.message(GroupSelectionStates.waiting_for_group_id)
async def process_group_id(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.reply("Faqat adminlar uchun!")
        await state.clear()
        return
    try:
        chat_id = int(message.text)
        group = get_group_by_chat_id(conn, chat_id)
        if not group:
            await message.reply("Bunday guruh topilmadi!")
            await state.clear()
            return
        await show_restrictions_for_group_from_pm(message, chat_id)
        await state.clear()
    except ValueError:
        await message.reply("Iltimos, to'g'ri guruh ID kiriting!")
    except Exception as e:
        await message.reply(f"Xatolik: {e}")
        print(f"Group ID process da xato: {e}")

async def show_restrictions_for_group_from_pm(message: types.Message, chat_id: int):
    group = get_group_by_chat_id(conn, chat_id)
    group_name = group[1] if group else f"Guruh {chat_id}"
    settings = get_group_settings(chat_id)
    text = f"{group_name} ({chat_id}) taqiq sozlamalari:"
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Xabar yuborish", callback_data=f"group_type_menu|text|{chat_id}")],
        [types.InlineKeyboardButton(text="Rasm yuborish", callback_data=f"group_type_menu|photo|{chat_id}")],
        [types.InlineKeyboardButton(text="Video yuborish", callback_data=f"group_type_menu|video|{chat_id}")],
        [types.InlineKeyboardButton(text="Stiker yuborish", callback_data=f"group_type_menu|sticker|{chat_id}")],
        [types.InlineKeyboardButton(text="Ovozli xabar yuborish", callback_data=f"group_type_menu|voice|{chat_id}")],
        [types.InlineKeyboardButton(text="Musiqa yuborish", callback_data=f"group_type_menu|audio|{chat_id}")],
        [types.InlineKeyboardButton(text="Fayl yuborish", callback_data=f"group_type_menu|document|{chat_id}")],
        [types.InlineKeyboardButton(text="Link havola yuborish", callback_data=f"group_type_menu|link|{chat_id}")],
        [types.InlineKeyboardButton(text="So'rovnoma yuborish", callback_data=f"group_type_menu|poll|{chat_id}")],
        [types.InlineKeyboardButton(text="Orqaga", callback_data="show_group_restrictions")]
    ])
    await message.reply(text, reply_markup=keyboard)

async def show_restrictions_for_group(callback: types.CallbackQuery, chat_id: int):
    group_name = callback.message.chat.title or f"Guruh {chat_id}"
    settings = get_group_settings(chat_id)
    text = f"{group_name} taqiq sozlamalari:"
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Xabar yuborish", callback_data=f"group_type_menu|text|{chat_id}")],
        [types.InlineKeyboardButton(text="Rasm yuborish", callback_data=f"group_type_menu|photo|{chat_id}")],
        [types.InlineKeyboardButton(text="Video yuborish", callback_data=f"group_type_menu|video|{chat_id}")],
        [types.InlineKeyboardButton(text="Stiker yuborish", callback_data=f"group_type_menu|sticker|{chat_id}")],
        [types.InlineKeyboardButton(text="Ovozli xabar yuborish", callback_data=f"group_type_menu|voice|{chat_id}")],
        [types.InlineKeyboardButton(text="Musiqa yuborish", callback_data=f"group_type_menu|audio|{chat_id}")],
        [types.InlineKeyboardButton(text="Fayl yuborish", callback_data=f"group_type_menu|document|{chat_id}")],
        [types.InlineKeyboardButton(text="Link havola yuborish", callback_data=f"group_type_menu|link|{chat_id}")],
        [types.InlineKeyboardButton(text="So'rovnoma yuborish", callback_data=f"group_type_menu|poll|{chat_id}")],
        [types.InlineKeyboardButton(text="Orqaga", callback_data="back_admin")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data.startswith("group_type_menu|"))
async def group_type_menu(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Faqat adminlar uchun!", show_alert=True)
        return
    parts = callback.data.split("|")
    type_key = parts[1]
    chat_id = int(parts[2])
    settings = get_group_settings(chat_id)
    current = settings.get(type_key, True)
    status = "✅" if current else "❌"
    type_names = {
        "text": "Xabar yuborish",
        "photo": "Rasm yuborish",
        "video": "Video yuborish",
        "sticker": "Stiker yuborish",
        "voice": "Ovozli xabar yuborish",
        "audio": "Musiqa yuborish",
        "document": "Fayl yuborish",
        "link": "Link havola yuborish",
        "poll": "So'rovnoma yuborish"
    }
    text = f"{type_names.get(type_key, type_key)} sozlamalari. Joriy holat: {status}"
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Ruxsat berish (True)", callback_data=f"group_set_true|{type_key}|{chat_id}")],
        [types.InlineKeyboardButton(text="Taqiqlash (False)", callback_data=f"group_set_false|{type_key}|{chat_id}")],
        [types.InlineKeyboardButton(text="Orqaga", callback_data=f"show_group_restrictions|{chat_id if callback.message.chat.type != 'private' else '0'}")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data.startswith("group_set_"))
async def group_set_restriction(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Faqat adminlar uchun!", show_alert=True)
        return
    parts = callback.data.split("|")
    is_true = parts[1] == "true"
    type_key = parts[2]
    chat_id = int(parts[3])
    settings = get_group_settings(chat_id)
    settings[type_key] = is_true
    save_group_settings(chat_id, settings)
    status = "✅" if is_true else "❌"
    text = f"{type_key} {status} qilindi."
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Orqaga", callback_data="show_group_restrictions")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer("Sozlama o'zgartirildi!")

# Barcha guruhlar uchun
@router.callback_query(F.data == "all_groups_restrictions")
async def show_all_restrictions(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Faqat adminlar uchun!", show_alert=True)
        return
    text = "Barcha guruhlar uchun taqiq sozlamalari (o'zgartirish barcha guruhlarga ta'sir qiladi):"
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Xabar yuborish", callback_data="all_type_menu|text")],
        [types.InlineKeyboardButton(text="Rasm yuborish", callback_data="all_type_menu|photo")],
        [types.InlineKeyboardButton(text="Video yuborish", callback_data="all_type_menu|video")],
        [types.InlineKeyboardButton(text="Stiker yuborish", callback_data="all_type_menu|sticker")],
        [types.InlineKeyboardButton(text="Ovozli xabar yuborish", callback_data="all_type_menu|voice")],
        [types.InlineKeyboardButton(text="Musiqa yuborish", callback_data="all_type_menu|audio")],
        [types.InlineKeyboardButton(text="Fayl yuborish", callback_data="all_type_menu|document")],
        [types.InlineKeyboardButton(text="Link havola yuborish", callback_data="all_type_menu|link")],
        [types.InlineKeyboardButton(text="So'rovnoma yuborish", callback_data="all_type_menu|poll")],
        [types.InlineKeyboardButton(text="Orqaga", callback_data="back_admin")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data.startswith("all_type_menu|"))
async def all_type_menu(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Faqat adminlar uchun!", show_alert=True)
        return
    parts = callback.data.split("|")
    type_key = parts[1]
    # Current ni hisoblash uchun birinchi guruhdan olish
    groups = get_all_groups(conn)
    current = True  # Default
    if groups:
        first_chat_id = groups[0][2]
        current = get_group_settings(first_chat_id).get(type_key, True)
    status = "✅" if current else "❌"
    type_names = {
        "text": "Xabar yuborish",
        "photo": "Rasm yuborish",
        "video": "Video yuborish",
        "sticker": "Stiker yuborish",
        "voice": "Ovozli xabar yuborish",
        "audio": "Musiqa yuborish",
        "document": "Fayl yuborish",
        "link": "Link havola yuborish",
        "poll": "So'rovnoma yuborish"
    }
    text = f"{type_names.get(type_key, type_key)} sozlamalari. Joriy holat (namuna): {status}"
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Ruxsat berish (True)", callback_data=f"all_set_true|{type_key}")],
        [types.InlineKeyboardButton(text="Taqiqlash (False)", callback_data=f"all_set_false|{type_key}")],
        [types.InlineKeyboardButton(text="Orqaga", callback_data="all_groups_restrictions")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data.startswith("all_set_"))
async def all_set_restriction(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Faqat adminlar uchun!", show_alert=True)
        return
    parts = callback.data.split("|")
    is_true = parts[1] == "true"
    type_key = parts[2]
    update_all_groups(type_key, is_true)
    status = "✅" if is_true else "❌"
    text = f"Barcha guruhlarda {type_key} {status} qilindi."
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Orqaga", callback_data="all_groups_restrictions")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer("Sozlama o'zgartirildi!")

@router.callback_query(F.data == "back_admin")
async def back_admin_callback(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Faqat adminlar uchun!", show_alert=True)
        return
    try:
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="Guruhlar soni", callback_data="group_count")],
            [types.InlineKeyboardButton(text="Guruhlar ro'yxati", callback_data="groups_list_cb")],
            [types.InlineKeyboardButton(text="Statistika", callback_data="stats_cb")],
            [types.InlineKeyboardButton(text="Guruhdagi ta'qiqlar", callback_data="show_group_restrictions")],
            [types.InlineKeyboardButton(text="Welcome sozlamalari", callback_data="show_welcome_settings")]
        ])
        await callback.message.edit_text(
            "Admin panelga xush kelibsiz!",
            reply_markup=keyboard
        )
        await callback.answer()
    except Exception as e:
        await callback.answer(f"Xatolik: {str(e)}", show_alert=True)
        print(f"Back admin da xato: {e}")

# Welcome sozlamalari
@router.callback_query(F.data == "show_welcome_settings")
async def show_welcome_settings(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Faqat adminlar uchun!", show_alert=True)
        return
    try:
        status = "✅" if welcome_settings.get("enabled", True) else "❌"
        mute_status = "✅" if welcome_settings.get("mute_enabled", True) else "❌"
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text=f"Welcome yoqish/o'chirish {status}", callback_data="toggle_welcome")],
            [types.InlineKeyboardButton(text=f"Yangi a'zolar mute {mute_status}", callback_data="toggle_mute")],
            [types.InlineKeyboardButton(text="Matn o'zgartirish", callback_data="edit_welcome_msg")],
            [types.InlineKeyboardButton(text="Mute vaqti o'zgartirish", callback_data="edit_mute_duration")],
            [types.InlineKeyboardButton(text="Orqaga", callback_data="back_admin")]
        ])
        await callback.message.edit_text(
            f"Welcome sozlamalari:\nEnabled: {status}\nMute: {mute_status}\nMatn: {welcome_settings.get('message', 'Noma\'lum')[:50]}...\nMute vaqti: {welcome_settings.get('mute_duration', 300)} sekund",
            reply_markup=keyboard
        )
        await callback.answer()
    except Exception as e:
        await callback.answer(f"Xatolik: {str(e)}", show_alert=True)
        print(f"Show welcome settings da xato: {e}")

@router.callback_query(F.data == "toggle_welcome")
async def toggle_welcome(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Faqat adminlar uchun!", show_alert=True)
        return
    welcome_settings["enabled"] = not welcome_settings.get("enabled", True)
    save_config()
    status = "✅" if welcome_settings["enabled"] else "❌"
    await callback.answer(f"Welcome {status} qilindi!")
    await show_welcome_settings(callback)

@router.callback_query(F.data == "toggle_mute")
async def toggle_mute(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Faqat adminlar uchun!", show_alert=True)
        return
    welcome_settings["mute_enabled"] = not welcome_settings.get("mute_enabled", True)
    save_config()
    status = "✅" if welcome_settings["mute_enabled"] else "❌"
    await callback.answer(f"Mute {status} qilindi!")
    await show_welcome_settings(callback)

@router.callback_query(F.data == "edit_welcome_msg")
async def edit_welcome_msg(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Faqat adminlar uchun!", show_alert=True)
        return
    await callback.message.edit_text("Yangi welcome matnini yuboring (faqat matn):")
    await state.set_state(WelcomeStates.waiting_for_message)
    await callback.answer()

@router.callback_query(F.data == "edit_mute_duration")
async def edit_mute_duration(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Faqat adminlar uchun!", show_alert=True)
        return
    await callback.message.edit_text("Yangi mute vaqtini sekundlarda kiriting (minimal 30 soniya, masalan, 300):")
    await state.set_state(WelcomeStates.waiting_for_duration)
    await callback.answer()

async def main():
    load_config()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    check_and_create_files()  # Fayllarni tekshirish va yaratish
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"Bot ishga tushirishda xatolik: {str(e)}")
    finally:
        conn.close()
