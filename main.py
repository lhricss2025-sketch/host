# -*- coding: utf-8 -*-
import telebot
import subprocess
import os
import zipfile
import shutil
from telebot import types
import time
from datetime import datetime, timedelta
import psutil
import sqlite3
import json
import logging
import threading
import re
import sys
import atexit
import requests
import uuid
from flask import Flask
from threading import Thread

# ============================================
# TURSO DATABASE SETUP
# ============================================

try:
    import libsql_experimental as libsql
    TURSO_AVAILABLE = True
except ImportError:
    TURSO_AVAILABLE = False
    print("⚠️ Turso not installed, using local SQLite")

TURSO_URL = os.environ.get('TURSO_URL', '')
TURSO_TOKEN = os.environ.get('TURSO_TOKEN', '')

def get_db_connection():
    """Get database connection (Turso or fallback to local SQLite)"""
    if TURSO_AVAILABLE and TURSO_URL and TURSO_TOKEN:
        try:
            conn = libsql.connect(database=TURSO_URL, auth_token=TURSO_TOKEN)
            return conn
        except Exception as e:
            print(f"❌ Turso connection failed: {e}")
            return sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    else:
        return sqlite3.connect(DATABASE_PATH, check_same_thread=False)

# ============================================
# CONFIGURATION
# ============================================

TOKEN = os.environ.get('BOT_TOKEN', '')
OWNER_ID = int(os.environ.get('OWNER_ID', 8105949422))
ADMIN_ID = int(os.environ.get('ADMIN_ID', 8105949422))
YOUR_USERNAME = os.environ.get('USERNAME', '@Senzo268')
UPDATE_CHANNEL = os.environ.get('CHANNEL', 'https://telegram.me/Senzo_Official')

BRAND_NAME = "SENZO DEV"
BRAND_EMOJI = "🐺"

START_IMAGE_URL = "https://i.postimg.cc/Jn3JGHwS/cvn-on-Tik-Tok.jpg"

START_DESCRIPTION = """
🚀 <b>Upload & Host Your Bots</b>
📤 <b>Supported:</b> ANY file type • ZIP auto-deploy
⭐ <b>Earn Points:</b> 1 Point per Referral
🎯 <b>5 Points</b> = 1 Extra Bot Slot
💎 <b>Free:</b> 2 Bots to Start
"""

# ============================================
# PATHS
# ============================================

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
IROTECH_DIR = os.path.join(BASE_DIR, 'inf')
DATABASE_PATH = os.path.join(IROTECH_DIR, 'bot_data.db')
LOGS_DIR = os.path.join(BASE_DIR, 'logs')
TMP_DIR = os.path.join(BASE_DIR, 'tmp_downloads')

FREE_USER_LIMIT = 10
SUBSCRIBED_USER_LIMIT = 15
ADMIN_LIMIT = 999
OWNER_LIMIT = float('inf')

os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(IROTECH_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(TMP_DIR, exist_ok=True)

# Clear any stale temp archives left over from a previous crashed run
for _f in os.listdir(TMP_DIR):
    try:
        os.remove(os.path.join(TMP_DIR, _f))
    except Exception:
        pass

if not TOKEN:
    print("❌ FATAL: BOT_TOKEN environment variable is not set. Set it in Railway → Variables.")
    sys.exit(1)

bot = telebot.TeleBot(TOKEN, parse_mode='HTML')

# script_key (bot_id) -> {process, log_file, log_path, start_time, entry_file, entry_type, folder, user_id, bot_name}
bot_scripts = {}
user_subscriptions = {}

# user_id -> [ {bot_id, bot_name, folder, entry_file, entry_type, upload_time, file_count} ]
user_bots = {}

active_users = set()
admin_ids = {ADMIN_ID, OWNER_ID}
bot_locked = False
bot_start_time = datetime.now()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOGS_DIR, 'bot.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================
# FLASK KEEP ALIVE
# ============================================

app = Flask('')

@app.route('/')
def home():
    return f"🤖 {BRAND_NAME} {BRAND_EMOJI} is Running!"

@app.route('/health')
def health():
    return {"status": "healthy", "uptime": get_uptime()}

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    print(f"✅ Flask Keep-Alive server started for {BRAND_NAME}.")

# ============================================
# DATABASE FUNCTIONS
# ============================================

def init_db():
    try:
        conn = get_db_connection()
        c = conn.cursor()

        c.execute('''CREATE TABLE IF NOT EXISTS subscriptions
        (user_id INTEGER PRIMARY KEY, expiry TEXT)''')

        c.execute('''CREATE TABLE IF NOT EXISTS hosted_bots
        (bot_id TEXT PRIMARY KEY, user_id INTEGER, bot_name TEXT, folder_name TEXT,
        entry_file TEXT, entry_type TEXT, file_count INTEGER, upload_time TEXT)''')

        c.execute('''CREATE TABLE IF NOT EXISTS active_users
        (user_id INTEGER PRIMARY KEY, username TEXT, first_seen TEXT, last_seen TEXT)''')

        c.execute('''CREATE TABLE IF NOT EXISTS admins
        (user_id INTEGER PRIMARY KEY)''')

        c.execute('''CREATE TABLE IF NOT EXISTS bot_logs
        (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action TEXT,
        details TEXT, timestamp TEXT)''')

        c.execute('''CREATE TABLE IF NOT EXISTS user_points
        (user_id INTEGER PRIMARY KEY,
         points INTEGER DEFAULT 0,
         total_referrals INTEGER DEFAULT 0,
         last_updated TEXT)''')

        c.execute('''CREATE TABLE IF NOT EXISTS referrals
        (id INTEGER PRIMARY KEY AUTOINCREMENT,
         referrer_id INTEGER,
         referred_user_id INTEGER UNIQUE,
         referred_at TEXT,
         points_awarded INTEGER DEFAULT 1)''')

        c.execute('''CREATE TABLE IF NOT EXISTS points_history
        (id INTEGER PRIMARY KEY AUTOINCREMENT,
         user_id INTEGER,
         points_change INTEGER,
         reason TEXT,
         timestamp TEXT)''')

        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
        if ADMIN_ID != OWNER_ID:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (ADMIN_ID,))

        conn.commit()
        conn.close()
        logger.info(f"{BRAND_NAME} Database initialized successfully.")
    except Exception as e:
        logger.error(f"{BRAND_NAME} Database error: {e}")

def load_data():
    try:
        conn = get_db_connection()
        c = conn.cursor()

        c.execute('SELECT user_id, expiry FROM subscriptions')
        for user_id, expiry in c.fetchall():
            try:
                user_subscriptions[user_id] = {'expiry': datetime.fromisoformat(expiry)}
            except ValueError:
                pass

        c.execute('SELECT bot_id, user_id, bot_name, folder_name, entry_file, entry_type, file_count, upload_time FROM hosted_bots')
        for bot_id, uid, bot_name, folder_name, entry_file, entry_type, file_count, upload_time in c.fetchall():
            user_bots.setdefault(uid, []).append({
                'bot_id': bot_id,
                'bot_name': bot_name,
                'folder': os.path.join(get_user_folder(uid), folder_name),
                'folder_name': folder_name,
                'entry_file': entry_file,
                'entry_type': entry_type,
                'file_count': file_count,
                'upload_time': upload_time,
                'user_id': uid
            })

        c.execute('SELECT user_id FROM active_users')
        active_users.update(user_id for (user_id,) in c.fetchall())

        c.execute('SELECT user_id FROM admins')
        admin_ids.update(user_id for (user_id,) in c.fetchall())

        conn.close()
        logger.info(f"{BRAND_NAME} Data loaded successfully.")
    except Exception as e:
        logger.error(f"{BRAND_NAME} Error loading data: {e}")

def log_action(user_id, action, details):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('''INSERT INTO bot_logs (user_id, action, details, timestamp)
        VALUES (?, ?, ?, ?)''', (user_id, action, details, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"{BRAND_NAME} Error logging action: {e}")

def save_hosted_bot_db(bot_id, user_id, bot_name, folder_name, entry_file, entry_type, file_count):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('''INSERT OR REPLACE INTO hosted_bots
        (bot_id, user_id, bot_name, folder_name, entry_file, entry_type, file_count, upload_time)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (bot_id, user_id, bot_name, folder_name, entry_file, entry_type, file_count, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        log_action(user_id, "BOT_UPLOAD", f"Uploaded {bot_name}")
    except Exception as e:
        logger.error(f"{BRAND_NAME} Error saving hosted bot: {e}")

def remove_hosted_bot_db(bot_id):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('DELETE FROM hosted_bots WHERE bot_id = ?', (bot_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"{BRAND_NAME} Error removing hosted bot: {e}")

def save_active_user(user_id, username=None):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        now = datetime.now().isoformat()
        c.execute('''INSERT INTO active_users (user_id, username, first_seen, last_seen)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET last_seen = ?, username = ?''',
        (user_id, username, now, now, now, username))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"{BRAND_NAME} Error saving active user: {e}")

def save_subscription(user_id, expiry):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO subscriptions (user_id, expiry) VALUES (?, ?)',
        (user_id, expiry.isoformat()))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"{BRAND_NAME} Error saving subscription: {e}")

# ============================================
# POINTS SYSTEM
# ============================================

def get_user_referral_link(user_id):
    bot_username = bot.get_me().username
    return f"https://t.me/{bot_username}?start=ref_{user_id}"

def initialize_user_points(user_id):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT user_id FROM user_points WHERE user_id = ?', (user_id,))
        if c.fetchone():
            conn.close()
            return
        c.execute('''INSERT INTO user_points (user_id, points, total_referrals, last_updated)
        VALUES (?, 0, 0, ?)''', (user_id, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"{BRAND_NAME} Error initializing points: {e}")

def get_user_points(user_id):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT points, total_referrals FROM user_points WHERE user_id = ?', (user_id,))
        result = c.fetchone()
        conn.close()
        if result:
            return {'points': result[0], 'total_referrals': result[1]}
        return {'points': 0, 'total_referrals': 0}
    except Exception:
        return {'points': 0, 'total_referrals': 0}

def add_points(user_id, points, reason="Referral"):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('''UPDATE user_points
                     SET points = points + ?,
                         total_referrals = total_referrals + 1,
                         last_updated = ?
                     WHERE user_id = ?''',
                  (points, datetime.now().isoformat(), user_id))
        c.execute('''INSERT INTO points_history (user_id, points_change, reason, timestamp)
                     VALUES (?, ?, ?, ?)''',
                  (user_id, points, reason, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"{BRAND_NAME} Error adding points: {e}")
        return False

def get_user_max_bots(user_id):
    base_limit = 2
    points_data = get_user_points(user_id)
    points = points_data['points']
    extra_bots = points // 5
    if user_id == OWNER_ID or user_id in admin_ids:
        return float('inf')
    if user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now():
        return SUBSCRIBED_USER_LIMIT
    return min(base_limit + extra_bots, FREE_USER_LIMIT)

def get_current_bot_count(user_id):
    return len(user_bots.get(user_id, []))

def can_user_upload(user_id):
    max_bots = get_user_max_bots(user_id)
    current_bots = get_current_bot_count(user_id)
    if max_bots == float('inf'):
        return True, "Unlimited"
    if current_bots < max_bots:
        return True, f"{current_bots}/{max_bots}"
    else:
        needed_points = (current_bots - 2) * 5 + 5
        return False, f"Need {needed_points} points for next bot"

def process_referral_link(referred_user_id, referrer_id):
    try:
        if referrer_id == referred_user_id:
            return False, "❌ You can't refer yourself!"

        conn = get_db_connection()
        c = conn.cursor()

        c.execute('SELECT id FROM referrals WHERE referred_user_id = ?', (referred_user_id,))
        if c.fetchone():
            conn.close()
            return False, "❌ You have already been referred!"

        c.execute('SELECT id FROM referrals WHERE referrer_id = ? AND referred_user_id = ?',
                  (referrer_id, referred_user_id))
        if c.fetchone():
            conn.close()
            return False, "❌ This user has already referred you!"

        initialize_user_points(referrer_id)
        initialize_user_points(referred_user_id)
        add_points(referrer_id, 1, f"Referral from user {referred_user_id}")

        c.execute('''INSERT INTO referrals (referrer_id, referred_user_id, referred_at, points_awarded)
                     VALUES (?, ?, ?, ?)''',
                  (referrer_id, referred_user_id, datetime.now().isoformat(), 1))

        conn.commit()
        conn.close()

        try:
            referrer_points = get_user_points(referrer_id)
            bot.send_message(referrer_id, f"""
🎉 <b>𝐍𝐄𝐖 𝐑𝐄𝐅𝐄𝐑𝐑𝐀𝐋!</b>

Someone used your referral link!
👤 <b>New User:</b> {referred_user_id}
⭐ <b>Points Earned:</b> +1
📊 <b>Total Points:</b> {referrer_points['points']}
🎯 <b>Next Bot:</b> Need {(5 - (referrer_points['points'] % 5))} more points

Keep sharing your referral link! 🚀
""", parse_mode='HTML')
        except Exception:
            pass

        return True, "✅ Referral successful! You got 1 point! 🎉"

    except Exception as e:
        logger.error(f"{BRAND_NAME} Error processing referral: {e}")
        return False, f"❌ Error: {str(e)[:50]}"

def get_referral_stats(user_id):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM referrals WHERE referrer_id = ?', (user_id,))
        total_refs = c.fetchone()[0]
        points_data = get_user_points(user_id)
        c.execute('''SELECT referred_user_id, referred_at
                     FROM referrals
                     WHERE referrer_id = ?
                     ORDER BY referred_at DESC
                     LIMIT 10''', (user_id,))
        recent_refs = c.fetchall()
        conn.close()
        return {
            'total': total_refs,
            'points': points_data['points'],
            'recent': recent_refs
        }
    except Exception:
        return {'total': 0, 'points': 0, 'recent': []}

# ============================================
# UTILITY FUNCTIONS
# ============================================

def get_uptime():
    uptime = datetime.now() - bot_start_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{days}d {hours}h {minutes}m {seconds}s"

def format_size(size_bytes):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} PB"

def get_system_stats():
    cpu = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    return {
        'cpu': cpu,
        'memory_used': memory.percent,
        'memory_total': format_size(memory.total),
        'disk_used': disk.percent,
        'disk_total': format_size(disk.total),
        'uptime': get_uptime()
    }

def create_mini_bar(percentage, length=20):
    filled = int((percentage / 100) * length)
    bar = '█' * filled + '░' * (length - filled)
    return f"║ [{bar}]"

def create_system_stats_message():
    stats = get_system_stats()
    running_bots = len([k for k in bot_scripts if is_bot_running_check(k)])

    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT SUM(points) FROM user_points')
        total_points = c.fetchone()[0] or 0
        c.execute('SELECT COUNT(*) FROM referrals')
        total_refs = c.fetchone()[0]
        conn.close()
    except Exception:
        total_points = 0
        total_refs = 0

    msg = f"""
╔══════════════════════════════════════╗
║       📊 <b>{BRAND_NAME} STATS</b> 📊         ║
╠══════════════════════════════════════╣
║ 🖥️ <b>𝐂𝐏𝐔 𝐔𝐬𝐚𝐠𝐞:</b> {stats['cpu']}%
║ {create_mini_bar(stats['cpu'])}
║
║ 🧠 <b>𝐌𝐞𝐦𝐨𝐫𝐲:</b> {stats['memory_used']}% / {stats['memory_total']}
║ {create_mini_bar(stats['memory_used'])}
║
║ 💾 <b>𝐃𝐢𝐬𝐤:</b> {stats['disk_used']}% / {stats['disk_total']}
║ {create_mini_bar(stats['disk_used'])}
║
║ ⏱️ <b>𝐔𝐩𝐭𝐢𝐦𝐞:</b> {stats['uptime']}
║ 🤖 <b>𝐑𝐮𝐧𝐧𝐢𝐧𝐠 𝐁𝐨𝐭𝐬:</b> {running_bots}
║ 👥 <b>𝐓𝐨𝐭𝐚𝐥 𝐔𝐬𝐞𝐫𝐬:</b> {len(active_users)}
║ ⭐ <b>𝐓𝐨𝐭𝐚𝐥 𝐏𝐨𝐢𝐧𝐭𝐬:</b> {total_points}
║ 👥 <b>𝐓𝐨𝐭𝐚𝐥 𝐑𝐞𝐟𝐞𝐫𝐫𝐚𝐥𝐬:</b> {total_refs}
╚══════════════════════════════════════╝
"""
    return msg

# ============================================
# HELPER FUNCTIONS
# ============================================

def get_user_folder(user_id):
    user_folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(user_folder, exist_ok=True)
    return user_folder

def get_user_file_limit(user_id):
    if user_id == OWNER_ID:
        return OWNER_LIMIT
    if user_id in admin_ids:
        return ADMIN_LIMIT
    if user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now():
        return SUBSCRIBED_USER_LIMIT
    return FREE_USER_LIMIT

def is_bot_running_check(script_key):
    script_info = bot_scripts.get(script_key)
    if script_info and script_info.get('process'):
        try:
            proc = psutil.Process(script_info['process'].pid)
            return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except Exception:
            return False
    return False

def cleanup_script(script_key):
    if script_key in bot_scripts:
        script_info = bot_scripts[script_key]
        if 'log_file' in script_info and hasattr(script_info['log_file'], 'close'):
            try:
                if not script_info['log_file'].closed:
                    script_info['log_file'].close()
            except Exception:
                pass
        del bot_scripts[script_key]

def kill_process_tree(process_info):
    try:
        if 'log_file' in process_info and hasattr(process_info['log_file'], 'close'):
            try:
                if not process_info['log_file'].closed:
                    process_info['log_file'].close()
            except Exception:
                pass
        process = process_info.get('process')
        if process and hasattr(process, 'pid'):
            try:
                parent = psutil.Process(process.pid)
                children = parent.children(recursive=True)
                for child in children:
                    try:
                        child.terminate()
                    except Exception:
                        pass
                gone, alive = psutil.wait_procs(children, timeout=2)
                for p in alive:
                    try:
                        p.kill()
                    except Exception:
                        pass
                try:
                    parent.terminate()
                    parent.wait(timeout=2)
                except Exception:
                    parent.kill()
            except Exception:
                pass
    except Exception:
        pass

def find_bot_by_id(user_id, bot_id):
    for b in user_bots.get(user_id, []):
        if b['bot_id'] == bot_id:
            return b
    return None

def find_bot_anywhere(bot_id):
    """Admins need to reach a bot without knowing which user owns it."""
    for uid, blist in user_bots.items():
        for b in blist:
            if b['bot_id'] == bot_id:
                return uid, b
    return None, None

# ============================================
# ANIMATION FUNCTIONS
# ============================================

def send_animated_message(chat_id, final_text, animation_type="loading", duration=2, steps=4):
    try:
        action_map = {
            "loading": "Authenticating session",
            "upload": "Uploading file",
            "extract": "Extracting archive",
            "install": "Installing dependencies",
            "download": "Downloading file",
            "delete": "Deleting file",
            "run": "Starting script",
            "stop": "Stopping script",
            "terminal": "Initializing terminal"
        }
        action_text = action_map.get(animation_type, "Processing")
        msg = None
        for i in range(steps + 1):
            percent = int((i / steps) * 100)
            bar = "🟩" * i + "⬜" * (steps - i)
            display = f"⚙️ 𝐋ᴏᴀᴅɪɴɢ... ({percent}%)\n[{bar}] {action_text}..."
            if i == 0:
                msg = bot.send_message(chat_id, display)
            else:
                try:
                    bot.edit_message_text(display, chat_id, msg.message_id)
                except Exception:
                    pass
            time.sleep(duration / steps)
        try:
            bot.edit_message_text(final_text, chat_id, msg.message_id, parse_mode='HTML')
        except Exception:
            bot.send_message(chat_id, final_text, parse_mode='HTML')
        return msg
    except Exception as e:
        logger.error(f"{BRAND_NAME} Animation error: {e}")
        return bot.send_message(chat_id, final_text, parse_mode='HTML')

def send_spinner_animation(chat_id, text, duration=2):
    return send_animated_message(chat_id, text, "loading", duration)

# ============================================
# ENTRY-POINT DETECTION (any zip / any folder)
# ============================================

PY_ENTRY_CANDIDATES = ['main.py', 'bot.py', 'app.py', 'run.py', 'start.py', 'server.py']
JS_ENTRY_CANDIDATES = ['index.js', 'bot.js', 'app.js', 'main.js', 'server.js']
IGNORED_DIRS = {'__pycache__', 'node_modules', '.git', '.idea', '.vscode', 'venv', '.venv'}

def flatten_single_wrapper_folder(folder):
    """If the extracted zip is just one wrapper directory, move its contents up."""
    entries = [e for e in os.listdir(folder) if not e.startswith('__MACOSX')]
    if len(entries) == 1:
        only_path = os.path.join(folder, entries[0])
        if os.path.isdir(only_path):
            for item in os.listdir(only_path):
                shutil.move(os.path.join(only_path, item), os.path.join(folder, item))
            shutil.rmtree(only_path, ignore_errors=True)

def find_entry_point(folder):
    """Walks the bot folder and figures out what to run. Returns (relative_path, type) or (None, None)."""
    all_py, all_js = [], []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith('__MACOSX')]
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), folder)
            if f.endswith('.py'):
                all_py.append(rel)
            elif f.endswith('.js'):
                all_js.append(rel)

    # package.json "main" field takes priority for JS bots
    pkg_json = os.path.join(folder, 'package.json')
    if os.path.exists(pkg_json):
        try:
            with open(pkg_json, 'r', encoding='utf-8', errors='ignore') as fh:
                data = json.load(fh)
            main_field = data.get('main')
            if main_field:
                candidate = os.path.normpath(main_field)
                if os.path.exists(os.path.join(folder, candidate)):
                    return candidate, 'js'
        except Exception:
            pass

    for cand in PY_ENTRY_CANDIDATES:
        for p in all_py:
            if os.path.basename(p) == cand:
                return p, 'py'
    for cand in JS_ENTRY_CANDIDATES:
        for p in all_js:
            if os.path.basename(p) == cand:
                return p, 'js'

    # Fallback: prefer python (it's the more common bot-hosting case), root-level, shortest path
    if all_py:
        all_py.sort(key=lambda p: (p.count(os.sep), len(p)))
        return all_py[0], 'py'
    if all_js:
        all_js.sort(key=lambda p: (p.count(os.sep), len(p)))
        return all_js[0], 'js'

    return None, None

def check_node_available():
    try:
        result = subprocess.run(['node', '--version'], capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return False

NODE_AVAILABLE = check_node_available()

def count_files(folder):
    total = 0
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        total += len(files)
    return total

# ============================================
# AUTO DEPENDENCY INSTALL
# ============================================

TELEGRAM_MODULES = {
    'telebot': 'pytelegrambotapi',
    'telegram': 'python-telegram-bot',
    'pyrogram': 'pyrogram',
    'telethon': 'telethon',
    'aiogram': 'aiogram',
    'PIL': 'Pillow',
    'cv2': 'opencv-python',
    'sklearn': 'scikit-learn',
    'bs4': 'beautifulsoup4',
    'dotenv': 'python-dotenv',
    'yaml': 'pyyaml',
    'aiohttp': 'aiohttp',
    'numpy': 'numpy',
    'pandas': 'pandas',
    'requests': 'requests',
    'flask': 'flask',
    'django': 'django',
    'fastapi': 'fastapi',
}

def auto_install_bulk_dependencies(folder, message_obj=None):
    """Runs once right after extraction: requirements.txt for python, package.json for node.
    Always reports back to chat when done — success, failure, or timeout — so it never looks stuck."""
    req_path = os.path.join(folder, 'requirements.txt')
    if os.path.exists(req_path):
        if message_obj:
            bot.send_message(message_obj.chat.id, "📦 <b>requirements.txt</b> found — installing packages, this can take a minute...", parse_mode='HTML')
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '-r', req_path, '--disable-pip-version-check', '--no-input'],
                capture_output=True, text=True, timeout=240, encoding='utf-8', errors='ignore'
            )
            if message_obj:
                if result.returncode == 0:
                    bot.send_message(message_obj.chat.id, "✅ <b>requirements.txt</b> installed successfully.", parse_mode='HTML')
                else:
                    tail = (result.stderr or result.stdout or "")[-500:]
                    bot.send_message(message_obj.chat.id,
                                     f"⚠️ <b>Some packages in requirements.txt failed to install</b> — will still try to run, "
                                     f"and auto-install anything still missing on crash.\n<code>{tail}</code>",
                                     parse_mode='HTML')
        except subprocess.TimeoutExpired:
            logger.error(f"{BRAND_NAME} requirements.txt install timed out for {folder}")
            if message_obj:
                bot.send_message(message_obj.chat.id, "⏱️ <b>requirements.txt install timed out</b> after 4 minutes — trying to run the bot anyway.", parse_mode='HTML')
        except Exception as e:
            logger.error(f"{BRAND_NAME} requirements.txt install error: {e}")
            if message_obj:
                bot.send_message(message_obj.chat.id, f"⚠️ requirements.txt install error: {str(e)[:200]}", parse_mode='HTML')

    pkg_path = os.path.join(folder, 'package.json')
    if os.path.exists(pkg_path):
        if message_obj:
            bot.send_message(message_obj.chat.id, "📦 <b>package.json</b> found — running npm install, this can take a minute...", parse_mode='HTML')
        try:
            result = subprocess.run(
                ['npm', 'install', '--no-audit', '--no-fund'],
                cwd=folder, capture_output=True, text=True, timeout=240, encoding='utf-8', errors='ignore'
            )
            if message_obj:
                if result.returncode == 0:
                    bot.send_message(message_obj.chat.id, "✅ <b>npm install</b> completed successfully.", parse_mode='HTML')
                else:
                    tail = (result.stderr or result.stdout or "")[-500:]
                    bot.send_message(message_obj.chat.id,
                                     f"⚠️ <b>npm install had errors</b> — will still try to run.\n<code>{tail}</code>",
                                     parse_mode='HTML')
        except subprocess.TimeoutExpired:
            logger.error(f"{BRAND_NAME} npm install timed out for {folder}")
            if message_obj:
                bot.send_message(message_obj.chat.id, "⏱️ <b>npm install timed out</b> after 4 minutes — trying to run the bot anyway.", parse_mode='HTML')
        except FileNotFoundError:
            if message_obj:
                bot.send_message(message_obj.chat.id, "⚠️ npm not found on this host — skipping package.json install.")
        except Exception as e:
            logger.error(f"{BRAND_NAME} npm install error: {e}")
            if message_obj:
                bot.send_message(message_obj.chat.id, f"⚠️ npm install error: {str(e)[:200]}", parse_mode='HTML')

def attempt_install_pip(module_name, message):
    package_name = TELEGRAM_MODULES.get(module_name.lower(), module_name)
    if not package_name:
        return False
    try:
        msg = send_spinner_animation(message.chat.id, f"Installing {package_name}...", duration=2)
        command = [sys.executable, '-m', 'pip', 'install', package_name, '--disable-pip-version-check', '--no-input']
        result = subprocess.run(command, capture_output=True, text=True, check=False,
                                encoding='utf-8', errors='ignore', timeout=150)
        if result.returncode == 0:
            try:
                bot.edit_message_text(
                    f"✅ <b>Package Installed!</b>\n📦 <code>{package_name}</code> installed successfully!",
                    message.chat.id, msg.message_id, parse_mode='HTML'
                )
            except Exception:
                bot.send_message(message.chat.id, f"✅ Package {package_name} installed!", parse_mode='HTML')
            return True
        else:
            error_msg = result.stderr[:500] if result.stderr else result.stdout[:500]
            try:
                bot.edit_message_text(
                    f"❌ <b>Installation Failed</b>\n<code>{error_msg}</code>",
                    message.chat.id, msg.message_id, parse_mode='HTML'
                )
            except Exception:
                pass
            return False
    except Exception:
        return False

def attempt_install_npm(module_name, folder, message):
    try:
        msg = send_spinner_animation(message.chat.id, f"Installing npm: {module_name}...", duration=2)
        command = ['npm', 'install', module_name, '--no-audit', '--no-fund']
        result = subprocess.run(command, capture_output=True, text=True, check=False,
                                cwd=folder, encoding='utf-8', errors='ignore', timeout=150)
        if result.returncode == 0:
            try:
                bot.edit_message_text(
                    f"✅ <b>NPM Package Installed!</b>\n📦 <code>{module_name}</code>",
                    message.chat.id, msg.message_id, parse_mode='HTML'
                )
            except Exception:
                pass
            return True
        return False
    except FileNotFoundError:
        bot.send_message(message.chat.id, "❌ NPM not found on this host!")
        return False
    except Exception:
        return False

# ============================================
# SCRIPT RUNNING (unified for py + js, keyed by bot_id)
# ============================================

def run_bot_instance(bot_entry, message_obj, attempt=1, admin_id=None):
    """
    bot_entry: dict with bot_id, folder, entry_file, entry_type, bot_name, user_id
    Auto-retries on missing-module errors, installing whatever's missing.
    """
    max_attempts = 4
    bot_id = bot_entry['bot_id']
    folder = bot_entry['folder']
    entry_file = bot_entry['entry_file']
    entry_type = bot_entry['entry_type']
    bot_name = bot_entry['bot_name']
    owner_id = bot_entry['user_id']

    if attempt > max_attempts:
        bot.send_message(message_obj.chat.id, f"❌ Failed to run '{bot_name}' after {max_attempts} attempts — check logs for the root cause.")
        return

    if not entry_type or not entry_file:
        bot.send_message(message_obj.chat.id, f"⚠️ <b>{bot_name}</b> has no runnable .py or .js entry file — stored, but nothing to execute.", parse_mode='HTML')
        return

    script_path = os.path.join(folder, entry_file)
    if not os.path.exists(script_path):
        bot.send_message(message_obj.chat.id, f"❌ Entry file '{entry_file}' not found!")
        return

    if entry_type == 'py':
        check_result = subprocess.run(
            [sys.executable, '-m', 'py_compile', script_path],
            capture_output=True, text=True, timeout=15
        )
        if check_result.returncode != 0:
            bot.send_message(message_obj.chat.id,
                             f"⚠️ <b>Syntax Error in {bot_name}</b>\n<code>{check_result.stderr[:600]}</code>",
                             parse_mode='HTML')
            return
    elif entry_type == 'js' and not NODE_AVAILABLE:
        bot.send_message(message_obj.chat.id,
                         f"❌ <b>{bot_name}</b> needs Node.js, but this host has none installed. "
                         f"On Railway, add a nixpacks.toml with the nodejs package (included in the deployment files).",
                         parse_mode='HTML')
        return

    terminal_msg = f"""
╔══════════════════════════════════════╗
║      🚀 <b>{BRAND_NAME}: STARTING BOT</b> 🚀  ║
╠══════════════════════════════════════╣
║ 🤖 Bot: <code>{bot_name[:25]}</code>
║ 📄 Entry: <code>{entry_file[:25]}</code>
║ 🔄 Attempt: {attempt}/{max_attempts}
╚══════════════════════════════════════╝
"""
    msg = send_animated_message(message_obj.chat.id, terminal_msg, "run", duration=2)
    log_file_path = os.path.join(LOGS_DIR, f"{bot_id}.log")
    log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')

    interpreter = [sys.executable, script_path] if entry_type == 'py' else ['node', script_path]

    try:
        process = subprocess.Popen(
            interpreter,
            cwd=folder,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='ignore'
        )
    except FileNotFoundError:
        log_file.close()
        bot.send_message(message_obj.chat.id, "❌ Node.js runtime not found on this host!" if entry_type == 'js' else "❌ Python runtime not found!")
        return

    bot_scripts[bot_id] = {
        'process': process,
        'log_file': log_file,
        'log_path': log_file_path,
        'start_time': datetime.now(),
        'entry_file': entry_file,
        'entry_type': entry_type,
        'folder': folder,
        'user_id': owner_id,
        'bot_name': bot_name
    }

    time.sleep(2.5)
    if process.poll() is None:
        success_msg = f"""
╔══════════════════════════════════════╗
║     ✅ <b>{BRAND_NAME}: BOT RUNNING</b> ✅    ║
╠══════════════════════════════════════╣
║ 🤖 <b>Bot:</b> <code>{bot_name[:25]}</code>
║ 🆔 <b>PID:</b> {process.pid}
║ ⏱️ <b>Started:</b> {datetime.now().strftime('%H:%M:%S')}
╚══════════════════════════════════════╝
"""
        try:
            bot.edit_message_text(success_msg, message_obj.chat.id, msg.message_id, parse_mode='HTML')
        except Exception:
            bot.send_message(message_obj.chat.id, success_msg, parse_mode='HTML')
        log_action(owner_id, "BOT_START", f"Started {bot_name} (PID: {process.pid})")
        return

    # Process died almost immediately — read the tail of the log and try to self-heal
    log_file.close()
    with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
        error_output = f.read()[-1500:]

    if entry_type == 'py':
        match = re.search(r"ModuleNotFoundError: No module named '([\w\.\-]+)'", error_output)
        if match:
            module_name = match.group(1).strip().split('.')[0]
            cleanup_script(bot_id)
            if attempt_install_pip(module_name, message_obj):
                time.sleep(1)
                run_bot_instance(bot_entry, message_obj, attempt + 1)
                return
    else:
        match = re.search(r"Cannot find module '(.+?)'", error_output)
        if match:
            module_name = match.group(1).strip()
            cleanup_script(bot_id)
            if attempt_install_npm(module_name, folder, message_obj):
                time.sleep(1)
                run_bot_instance(bot_entry, message_obj, attempt + 1)
                return

    error_msg = f"""
╔══════════════════════════════════════╗
║     ❌ <b>{BRAND_NAME}: BOT FAILED</b> ❌      ║
╠══════════════════════════════════════╣
║ 🤖 <b>Bot:</b> <code>{bot_name[:25]}</code>
║ ❗ <b>Exit Code:</b> {process.returncode}
╠══════════════════════════════════════╣
<code>{error_output[:500]}</code>
╚══════════════════════════════════════╝
"""
    try:
        bot.edit_message_text(error_msg, message_obj.chat.id, msg.message_id, parse_mode='HTML')
    except Exception:
        bot.send_message(message_obj.chat.id, error_msg, parse_mode='HTML')
    cleanup_script(bot_id)

def run_bot_instance_safe(bot_entry, message_obj, attempt=1, admin_id=None):
    """Thin safety wrapper — any uncaught exception here gets reported to chat instead of
    silently killing the background thread (which is what made things look 'stuck')."""
    try:
        run_bot_instance(bot_entry, message_obj, attempt, admin_id)
    except Exception as e:
        logger.error(f"{BRAND_NAME} run_bot_instance crashed for {bot_entry.get('bot_name')}: {e}")
        try:
            bot.send_message(
                message_obj.chat.id,
                f"❌ <b>Unexpected error while starting</b> <code>{bot_entry.get('bot_name', 'bot')}</code>:\n"
                f"<code>{str(e)[:300]}</code>",
                parse_mode='HTML'
            )
        except Exception:
            pass

# ============================================
# KEYBOARD LAYOUTS
# ============================================

def get_main_keyboard(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    if user_id == OWNER_ID or user_id in admin_ids:
        markup.row("📢 Updates Channel", "📤 Upload File")
        markup.row("📂 Check Files", "🟢 Running Bots")
        markup.row("⚡ Bot Speed", "📊 Statistics")
        markup.row("⭐ My Points", "🎯 Referral System")
        markup.row("💳 Subscriptions", "📢 Broadcast")
        markup.row("🔒 Lock Bot", "👑 Admin Panel")
        markup.row("🖼️ Change Banner", "📞 Contact Owner")
    else:
        markup.row("📢 Updates Channel", "📤 Upload File")
        markup.row("📂 Check Files", "🟢 My Running Bots")
        markup.row("⚡ Bot Speed", "📊 My Stats")
        markup.row("⭐ My Points", "🎯 Referral System")
        markup.row("📞 Contact Owner")
    return markup

def get_bot_actions_keyboard(bot_id, is_running=False):
    markup = types.InlineKeyboardMarkup(row_width=2)
    if is_running:
        markup.add(
            types.InlineKeyboardButton("🛑 Stop", callback_data=f"stop_{bot_id}"),
            types.InlineKeyboardButton("📋 Logs", callback_data=f"logs_{bot_id}")
        )
        markup.add(types.InlineKeyboardButton("🔄 Restart", callback_data=f"restart_{bot_id}"))
    else:
        markup.add(
            types.InlineKeyboardButton("▶️ Run", callback_data=f"run_{bot_id}"),
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f"delete_{bot_id}")
        )
        markup.add(
            types.InlineKeyboardButton("📥 Download", callback_data=f"download_{bot_id}"),
            types.InlineKeyboardButton("📋 Logs", callback_data=f"logs_{bot_id}")
        )
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="back_to_files"))
    return markup

# ============================================
# COMMAND HANDLERS
# ============================================

@bot.message_handler(commands=['start'])
def start_command(message):
    user_id = message.from_user.id
    username = message.from_user.username or "Unknown"

    referrer_id = None
    if len(message.text.split()) > 1:
        param = message.text.split()[1].strip()
        if param.startswith("ref_"):
            try:
                referrer_id = int(param[4:])
            except ValueError:
                pass

    referral_msg = ""
    if referrer_id:
        success, msg = process_referral_link(user_id, referrer_id)
        if success:
            referral_msg = f"""
🎉 <b>𝐖𝐄𝐋𝐂𝐎𝐌𝐄! You joined via referral!</b>

{msg}

<b>📊 Your Benefits:</b>
• 2 free bot slots to start
• Share your referral link to earn more points
• 5 points = 1 extra bot slot

Start by uploading your first bot! 🚀
"""

    initialize_user_points(user_id)
    active_users.add(user_id)
    save_active_user(user_id, username)
    log_action(user_id, "START", "Started the bot")

    if bot_locked and user_id not in admin_ids and user_id != OWNER_ID:
        bot.reply_to(message, "🔒 Bot is locked.")
        return

    points_data = get_user_points(user_id)
    max_bots = get_user_max_bots(user_id)
    current_bots = get_current_bot_count(user_id)
    referral_link = get_user_referral_link(user_id)

    status_text = f"""
👋 <b>Welcome, {message.from_user.first_name}</b>!

<b>📌 Your Status:</b>
🤖 <b>Bots:</b> {current_bots}/{max_bots if max_bots != float('inf') else '∞'}
⭐ <b>Points:</b> {points_data['points']}
👥 <b>Referrals:</b> {points_data['total_referrals']}
💳 <b>Status:</b> {'👑 Owner' if user_id == OWNER_ID else '⭐ Admin' if user_id in admin_ids else '🌟 Premium' if user_id in user_subscriptions else '👤 Free'}

<b>🔗 Your Referral Link:</b>
<code>{referral_link}</code>
"""

    final_caption = f"""
<b>🤖 {BRAND_NAME} {BRAND_EMOJI} BOT HOSTING</b>

{START_DESCRIPTION}

━━━━━━━━━━━━━━━━━━━━━━━━

{referral_msg}

{status_text}

📢 <b>Just send a ZIP or any file — it auto-deploys!</b> ⬇️
"""

    try:
        bot.send_photo(
            message.chat.id,
            photo=START_IMAGE_URL,
            caption=final_caption,
            parse_mode='HTML',
            reply_markup=get_main_keyboard(user_id)
        )
    except Exception as e:
        logger.error(f"Image send failed: {e}")
        bot.send_message(
            message.chat.id,
            final_caption,
            parse_mode='HTML',
            reply_markup=get_main_keyboard(user_id)
        )

@bot.message_handler(commands=['help'])
def help_command(message):
    help_text = f"""
╔══════════════════════════════════════╗
║       📚 <b>{BRAND_NAME} HELP</b> 📚          ║
╠══════════════════════════════════════╣
║
║ <b>📤 Deploying a bot:</b>
║ • Just send a .zip — it extracts, installs
║   deps, finds the entry point, and runs
║   automatically. No buttons needed.
║ • Any single file also works (.py/.js run,
║   anything else is stored as support data).
║
║ <b>🤖 Bot Control:</b>
║ • /files - View your hosted bots
║ • /running - See running bots
║ • /stop - Stop via buttons in /files
║
║ <b>⭐ Points & Referrals:</b>
║ • /points - Check your points
║ • /referral - Get your referral link
║ • /referrals - View referral history
║
║ <b>📊 Information:</b>
║ • /stats - Bot statistics
║ • /speed - Check bot speed
║
║ <b>🔧 Other:</b>
║ • /start - Restart bot
║ • /help - This message
║
╚══════════════════════════════════════╝
"""
    bot.send_message(message.chat.id, help_text, parse_mode='HTML')

@bot.message_handler(commands=['stats'])
def stats_command(message):
    msg = send_spinner_animation(message.chat.id, f"Gathering {BRAND_NAME} stats...", duration=2)
    stats_text = create_system_stats_message()
    try:
        bot.edit_message_text(stats_text, message.chat.id, msg.message_id, parse_mode='HTML')
    except Exception:
        bot.send_message(message.chat.id, stats_text, parse_mode='HTML')

@bot.message_handler(commands=['speed'])
def speed_command(message):
    msg = send_spinner_animation(message.chat.id, f"Testing {BRAND_NAME} speed...", duration=2)
    start_time = time.time()
    latency = (time.time() - start_time) * 1000
    cpu = psutil.cpu_percent()
    memory = psutil.virtual_memory().percent
    speed_text = f"""
╔══════════════════════════════════════╗
║        ⚡ <b>{BRAND_NAME} SPEED</b> ⚡        ║
╠══════════════════════════════════════╣
║
║  🏓 <b>Latency:</b> {latency:.2f}ms
║  🖥️ <b>CPU:</b> {cpu}%
║  🧠 <b>Memory:</b> {memory}%
║  ⏱️ <b>Uptime:</b> {get_uptime()}
║
║  {'🟢 Excellent!' if latency < 100 else '🟡 Good' if latency < 500 else '🔴 Slow'}
║
╚══════════════════════════════════════╝
"""
    try:
        bot.edit_message_text(speed_text, message.chat.id, msg.message_id, parse_mode='HTML')
    except Exception:
        bot.send_message(message.chat.id, speed_text, parse_mode='HTML')

@bot.message_handler(commands=['running', 'files'])
def running_or_files_command(message):
    if message.text.startswith('/files'):
        show_user_files(message)
    else:
        running_command(message)

def running_command(message):
    user_id = message.from_user.id
    msg = send_spinner_animation(message.chat.id, f"Fetching {BRAND_NAME} bots...", duration=1)
    running_bots = []
    for script_key, info in bot_scripts.items():
        if is_bot_running_check(script_key):
            if user_id == OWNER_ID or user_id in admin_ids or info.get('user_id') == user_id:
                uptime = datetime.now() - info.get('start_time', datetime.now())
                running_bots.append({
                    'name': info.get('bot_name', 'Unknown'),
                    'user': info.get('user_id', 'Unknown'),
                    'pid': info.get('process').pid if info.get('process') else 'N/A',
                    'uptime': str(uptime).split('.')[0]
                })
    if running_bots:
        text = f"""
╔══════════════════════════════════════╗
║      🟢 <b>{BRAND_NAME} BOTS</b> 🟢           ║
╠══════════════════════════════════════╣
"""
        for i, info in enumerate(running_bots, 1):
            text += f"""║ {i}. 🤖 <code>{info['name'][:20]}</code>
║    👤 User: {info['user']}
║    🆔 PID: {info['pid']}
║    ⏱️ Uptime: {info['uptime']}
║ ──────────────────────────────────
"""
        text += "╚══════════════════════════════════════╝"
    else:
        text = f"""
╔══════════════════════════════════════╗
║      🔴 <b>NO {BRAND_NAME} BOTS</b> 🔴        ║
╠══════════════════════════════════════╣
║
║  No bots are currently running.
║  Upload a ZIP or file to deploy one!
║
╚══════════════════════════════════════╝
"""
    try:
        bot.edit_message_text(text, message.chat.id, msg.message_id, parse_mode='HTML')
    except Exception:
        bot.send_message(message.chat.id, text, parse_mode='HTML')

@bot.message_handler(commands=['points'])
def points_command(message):
    show_my_points(message)

@bot.message_handler(commands=['referral'])
def referral_command(message):
    show_referral_system(message)

@bot.message_handler(commands=['referrals'])
def referrals_command(message):
    show_referral_history_for_command(message, message.from_user.id)

@bot.message_handler(commands=['lock'])
def lock_command(message):
    global bot_locked
    user_id = message.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.reply_to(message, "❌ You don't have permission!")
        return
    bot_locked = not bot_locked
    status = "🔒 LOCKED" if bot_locked else "🔓 UNLOCKED"
    lock_text = f"""
╔══════════════════════════════════════╗
║         🔐 <b>{BRAND_NAME} STATUS</b> 🔐       ║
╠══════════════════════════════════════╣
║
║  Status: {status}
║  By: {message.from_user.first_name}
║  Time: {datetime.now().strftime('%H:%M:%S')}
║
╚══════════════════════════════════════╝
"""
    send_animated_message(message.chat.id, lock_text, "terminal", duration=1)

@bot.message_handler(commands=['broadcast'])
def broadcast_command(message):
    user_id = message.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.reply_to(message, "❌ You don't have permission!")
        return
    msg = bot.reply_to(message, "📢 Send the message you want to broadcast:")
    bot.register_next_step_handler(msg, process_broadcast)

def process_broadcast(message):
    broadcast_text = message.text
    if not broadcast_text:
        bot.reply_to(message, "❌ Please send a text message!")
        return
    progress_msg = bot.send_message(message.chat.id, f"📢 Starting {BRAND_NAME} broadcast...")
    success = 0
    failed = 0
    total = len(active_users)
    for i, user_id in enumerate(active_users):
        try:
            formatted_msg = f"""
╔══════════════════════════════════════╗
║      📢 <b>{BRAND_NAME} {BRAND_EMOJI} BROADCAST</b> 📢    ║
╠══════════════════════════════════════╣
║
{broadcast_text}
║
╚══════════════════════════════════════╝
"""
            bot.send_message(user_id, formatted_msg, parse_mode='HTML')
            success += 1
        except Exception:
            failed += 1
        if (i + 1) % 10 == 0 and total > 0:
            try:
                filled = min(4, (i + 1) // max(1, total // 4))
                bar = ("🟩" * filled).ljust(4, "⬜")
                bot.edit_message_text(
                    f"⚙️ Loading... ({int((i+1)/total*100)}%)\n[{bar}] Broadcasting...",
                    message.chat.id, progress_msg.message_id
                )
            except Exception:
                pass
    result_text = f"""
╔══════════════════════════════════════╗
║     ✅ <b>{BRAND_NAME} BROADCAST COMPLETE</b> ✅ ║
╠══════════════════════════════════════╣
║
║  📤 Total: {total}
║  ✅ Success: {success}
║  ❌ Failed: {failed}
║
╚══════════════════════════════════════╝
"""
    try:
        bot.edit_message_text(result_text, message.chat.id, progress_msg.message_id, parse_mode='HTML')
    except Exception:
        bot.send_message(message.chat.id, result_text, parse_mode='HTML')

@bot.message_handler(commands=['subscribe'])
def subscribe_command(message):
    user_id = message.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.reply_to(message, "❌ You don't have permission!")
        return
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "Usage: /subscribe <user_id> <days>")
        return
    try:
        target_user = int(parts[1])
        days = int(parts[2])
    except ValueError:
        bot.reply_to(message, "❌ Invalid user ID or days!")
        return
    expiry = datetime.now() + timedelta(days=days)
    user_subscriptions[target_user] = {'expiry': expiry}
    save_subscription(target_user, expiry)
    sub_text = f"""
╔══════════════════════════════════════╗
║      ✅ <b>{BRAND_NAME} SUBSCRIPTION</b> ✅   ║
╠══════════════════════════════════════╣
║
║  👤 User: {target_user}
║  📅 Days: {days}
║  ⏰ Expires: {expiry.strftime('%Y-%m-%d %H:%M')}
║
╚══════════════════════════════════════╝
"""
    send_animated_message(message.chat.id, sub_text, "loading", duration=1)
    try:
        bot.send_message(target_user, f"🎉 You've been subscribed for {days} days by {BRAND_NAME}!")
    except Exception:
        pass

@bot.message_handler(commands=['addpoints'])
def add_points_command(message):
    user_id = message.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.reply_to(message, "❌ Admin only!")
        return
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "Usage: /addpoints <user_id> <points>")
        return
    try:
        target_user = int(parts[1])
        points = int(parts[2])
    except ValueError:
        bot.reply_to(message, "❌ Invalid user ID or points!")
        return
    initialize_user_points(target_user)
    success = add_points(target_user, points, f"Admin added {points} points")
    if success:
        points_data = get_user_points(target_user)
        bot.reply_to(message, f"""
✅ <b>Points Added!</b>

👤 <b>User:</b> {target_user}
⭐ <b>Points Added:</b> +{points}
📊 <b>Total Points:</b> {points_data['points']}
🤖 <b>Max Bots:</b> {get_user_max_bots(target_user)}
""", parse_mode='HTML')
        try:
            bot.send_message(target_user, f"""
🎉 <b>You received {points} points!</b>

📊 <b>Your Points:</b> {points_data['points']}
🤖 <b>Max Bots:</b> {get_user_max_bots(target_user)}

Keep going! Share your referral link for more! 🚀
""", parse_mode='HTML')
        except Exception:
            pass
    else:
        bot.reply_to(message, "❌ Failed to add points!")

@bot.message_handler(commands=['setbanner'])
def set_banner_command(message):
    user_id = message.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.reply_to(message, "❌ Admin only!")
        return

    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, """
📖 <b>Usage:</b> /setbanner &lt;image_url&gt;

<b>Example:</b>
/setbanner https://telegra.ph/file/your-image.jpg
""", parse_mode='HTML')
        return

    global START_IMAGE_URL
    new_url = parts[1].strip()

    if not new_url.startswith(('http://', 'https://')):
        bot.reply_to(message, "❌ Invalid URL! Must start with http:// or https://")
        return

    try:
        response = requests.head(new_url, timeout=10)
        if response.status_code != 200:
            bot.reply_to(message, f"❌ Image not accessible! Status: {response.status_code}")
            return
    except Exception:
        bot.reply_to(message, "❌ Cannot access the URL! Please check and try again.")
        return

    START_IMAGE_URL = new_url
    bot.reply_to(message, f"""
✅ <b>Banner Updated!</b>

🖼️ <b>New Banner:</b>
<code>{START_IMAGE_URL}</code>
""", parse_mode='HTML')

    log_action(user_id, "BANNER_CHANGE", f"Changed banner to {START_IMAGE_URL}")

@bot.message_handler(commands=['setdesc'])
def set_description_command(message):
    user_id = message.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.reply_to(message, "❌ Admin only!")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, """
📖 <b>Usage:</b> /setdesc &lt;description&gt;
""", parse_mode='HTML')
        return

    global START_DESCRIPTION
    START_DESCRIPTION = parts[1].strip()
    bot.reply_to(message, f"""
✅ <b>Description Updated!</b>

📝 <b>New Description:</b>
{START_DESCRIPTION}
""", parse_mode='HTML')

    log_action(user_id, "DESC_CHANGE", "Changed description")

@bot.message_handler(commands=['settings'])
def settings_command(message):
    user_id = message.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.reply_to(message, "❌ Admin only!")
        return

    settings_text = f"""
⚙️ <b>Current Start Settings</b>

🖼️ <b>Banner URL:</b>
<code>{START_IMAGE_URL}</code>

📝 <b>Description:</b>
{START_DESCRIPTION}
"""
    bot.send_message(message.chat.id, settings_text, parse_mode='HTML')

# ============================================
# TEXT MESSAGE HANDLERS
# ============================================

@bot.message_handler(content_types=['text'])
def handle_text(message):
    user_id = message.from_user.id
    text = message.text
    active_users.add(user_id)

    if bot_locked and user_id not in admin_ids and user_id != OWNER_ID:
        bot.reply_to(message, "🔒 Bot is locked!")
        return

    if text == "📢 Updates Channel":
        bot.send_message(message.chat.id, f"📢 Join our {BRAND_NAME} updates:\n{UPDATE_CHANNEL}")
    elif text == "📤 Upload File":
        handle_upload_request(message)
    elif text == "📂 Check Files":
        show_user_files(message)
    elif text == "🟢 Running Bots" or text == "🟢 My Running Bots":
        running_command(message)
    elif text == "⚡ Bot Speed":
        speed_command(message)
    elif text == "📊 Statistics" or text == "📊 My Stats":
        stats_command(message)
    elif text == "⭐ My Points":
        show_my_points(message)
    elif text == "🎯 Referral System":
        show_referral_system(message)
    elif text == "💳 Subscriptions":
        show_subscriptions(message)
    elif text == "📢 Broadcast":
        broadcast_command(message)
    elif text == "🔒 Lock Bot":
        lock_command(message)
    elif text == "👑 Admin Panel":
        show_admin_panel(message)
    elif text == "🖼️ Change Banner":
        set_banner_command(message)
    elif text == "📞 Contact Owner":
        bot.send_message(message.chat.id, f"📞 Contact: {YOUR_USERNAME}")

def handle_upload_request(message):
    user_id = message.from_user.id
    can_upload, status = can_user_upload(user_id)

    if not can_upload:
        points_data = get_user_points(user_id)
        needed = 5 - (points_data['points'] % 5)
        bot.reply_to(message, f"""
❌ <b>Can't Upload More Bots!</b>

📊 <b>Your Status:</b>
• Current Bots: {get_current_bot_count(user_id)}
• Max Bots: {get_user_max_bots(user_id)}
• Points: {points_data['points']}

🎯 <b>You need {needed} more points!</b>
Use 🎯 Referral System to get more!
""", parse_mode='HTML')
        return

    upload_text = f"""
╔══════════════════════════════════════╗
║       📤 <b>{BRAND_NAME}: FILE UPLOAD</b> 📤   ║
╠══════════════════════════════════════╣
║
║  Send your ZIP or any file now!
║
║  <b>ZIP uploads:</b> auto-extracted, entry
║  point auto-detected, dependencies
║  auto-installed, and it auto-runs.
║
║  <b>Any other file:</b> stored, and if it's
║  .py/.js it runs automatically too.
║
║  📌 <b>1 upload = 1 bot slot</b> — whether
║  it's a single file or a whole ZIP.
║
║  📁 Bots: {get_current_bot_count(user_id)}/{int(get_user_max_bots(user_id)) if get_user_max_bots(user_id) != float('inf') else '∞'}
║  ⭐ Points: {get_user_points(user_id)['points']}
║
╚══════════════════════════════════════╝
"""
    bot.send_message(message.chat.id, upload_text, parse_mode='HTML')

def show_user_files(message):
    user_id = message.from_user.id
    msg = send_spinner_animation(message.chat.id, f"Loading {BRAND_NAME} bots...", duration=1)
    bots = user_bots.get(user_id, [])
    if not bots:
        text = f"""
╔══════════════════════════════════════╗
║       📂 <b>{BRAND_NAME}: YOUR BOTS</b> 📂    ║
╠══════════════════════════════════════╣
║
║  You haven't hosted any bots yet!
║  Send a ZIP or any file to get started.
║
╚══════════════════════════════════════╝
"""
        try:
            bot.edit_message_text(text, message.chat.id, msg.message_id, parse_mode='HTML')
        except Exception:
            bot.send_message(message.chat.id, text, parse_mode='HTML')
        return

    text = f"""
╔══════════════════════════════════════╗
║       📂 <b>{BRAND_NAME}: YOUR BOTS</b> 📂    ║
╠══════════════════════════════════════╣
"""
    markup = types.InlineKeyboardMarkup(row_width=1)
    for i, b in enumerate(bots, 1):
        is_running = is_bot_running_check(b['bot_id'])
        status = "🟢" if is_running else "🔴"
        type_icon = "🐍" if b['entry_type'] == 'py' else "🟨" if b['entry_type'] == 'js' else "📦"
        text += f"║ {i}. {status} {type_icon} <code>{b['bot_name'][:25]}</code>\n"
        markup.add(types.InlineKeyboardButton(
            f"{status} {type_icon} {b['bot_name'][:25]}",
            callback_data=f"bot_{b['bot_id']}"
        ))
    text += "╚══════════════════════════════════════╝\nSelect a bot for actions:"
    try:
        bot.edit_message_text(text, message.chat.id, msg.message_id, parse_mode='HTML', reply_markup=markup)
    except Exception:
        bot.send_message(message.chat.id, text, parse_mode='HTML', reply_markup=markup)

def show_subscriptions(message):
    user_id = message.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.reply_to(message, "❌ Admin only!")
        return
    active_subs = {uid: data for uid, data in user_subscriptions.items()
                   if data['expiry'] > datetime.now()}
    text = f"""
╔══════════════════════════════════════╗
║     💳 <b>{BRAND_NAME}: SUBSCRIPTIONS</b> 💳    ║
╠══════════════════════════════════════╣
║
║  Active: {len(active_subs)}
║  Total Ever: {len(user_subscriptions)}
║
"""
    for uid, data in list(active_subs.items())[:10]:
        remaining = data['expiry'] - datetime.now()
        text += f"║  👤 {uid}: {remaining.days}d left\n"
    text += """║
╠══════════════════════════════════════╣
║  Add sub: /subscribe <id> <days>
╚══════════════════════════════════════╝
"""
    bot.send_message(message.chat.id, text, parse_mode='HTML')

def show_admin_panel(message):
    user_id = message.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.reply_to(message, "❌ Admin only!")
        return

    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM user_points')
        total_users_with_points = c.fetchone()[0]
        c.execute('SELECT SUM(points) FROM user_points')
        total_points = c.fetchone()[0] or 0
        c.execute('SELECT COUNT(*) FROM referrals')
        total_referrals = c.fetchone()[0]
        conn.close()
    except Exception:
        total_users_with_points = 0
        total_points = 0
        total_referrals = 0

    total_bots = sum(len(b) for b in user_bots.values())
    total_users = len(user_bots)

    admin_text = f"""
╔══════════════════════════════════════╗
║       👑 <b>{BRAND_NAME}: ADMIN PANEL</b> 👑   ║
╠══════════════════════════════════════╣
║
║  <b>📊 General Statistics:</b>
║  • Total Users: {len(active_users)}
║  • Users with Bots: {total_users}
║  • Total Bots: {total_bots}
║  • Active Subs: {len([u for u, d in user_subscriptions.items() if d['expiry'] > datetime.now()])}
║  • Running Bots: {len([k for k in bot_scripts if is_bot_running_check(k)])}
║
║  <b>⭐ Points System:</b>
║  • Users with Points: {total_users_with_points}
║  • Total Points: {total_points}
║  • Total Referrals: {total_referrals}
║
╚══════════════════════════════════════╝
"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📂 All User Bots", callback_data="admin_view_all_files"),
        types.InlineKeyboardButton("🏆 Top Referrers", callback_data="admin_top_referrers"),
        types.InlineKeyboardButton("🛑 Stop All Bots", callback_data="admin_stopall"),
        types.InlineKeyboardButton("🔄 Refresh", callback_data="admin_refresh"),
        types.InlineKeyboardButton("📊 Full Stats", callback_data="admin_fullstats"),
        types.InlineKeyboardButton("📋 View Logs", callback_data="admin_logs")
    )
    bot.send_message(message.chat.id, admin_text, parse_mode='HTML', reply_markup=markup)

def show_my_points(message):
    user_id = message.from_user.id
    initialize_user_points(user_id)
    points_data = get_user_points(user_id)
    max_bots = get_user_max_bots(user_id)
    current_bots = get_current_bot_count(user_id)
    referral_link = get_user_referral_link(user_id)
    ref_stats = get_referral_stats(user_id)

    points = points_data['points']
    points_needed = 5 - (points % 5) if max_bots != float('inf') else 0
    progress = (points % 5) / 5 * 100 if max_bots != float('inf') else 100
    filled = int((points % 5) / 5 * 10) if max_bots != float('inf') else 10
    bar = "█" * filled + "░" * (10 - filled)

    status_text = f"""
╔══════════════════════════════════════╗
║        ⭐ <b>{BRAND_NAME}: MY POINTS</b> ⭐      ║
╠══════════════════════════════════════╣
║
║  📊 <b>Your Points:</b> {points}
║  👥 <b>Total Referrals:</b> {ref_stats['total']}
║  🤖 <b>Current Bots:</b> {current_bots}
║  🎯 <b>Max Bots:</b> {max_bots if max_bots != float('inf') else '∞'}
║
║  <b>Next Bot Progress:</b>
║  [{bar}] {int(progress)}%
║
"""
    if max_bots != float('inf'):
        if points_needed == 0:
            status_text += "║  ✅ <b>Ready for next bot!</b>\n"
        else:
            status_text += f"║  ⏳ <b>Need {points_needed} more points</b>\n"
    else:
        status_text += "║  👑 <b>Unlimited (Admin/Owner)</b>\n"

    status_text += f"""
║
║  🔗 <b>Your Referral Link:</b>
║  <code>{referral_link}</code>
║
╚══════════════════════════════════════╝
"""

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📤 Share Referral", callback_data="share_referral"),
        types.InlineKeyboardButton("📊 Referral History", callback_data="referral_history")
    )
    markup.add(types.InlineKeyboardButton("🔄 Refresh", callback_data="refresh_points"))

    bot.send_message(message.chat.id, status_text, parse_mode='HTML', reply_markup=markup)

def show_referral_system(message):
    user_id = message.from_user.id
    initialize_user_points(user_id)
    referral_link = get_user_referral_link(user_id)
    ref_stats = get_referral_stats(user_id)

    text = f"""
╔══════════════════════════════════════╗
║      🎯 <b>{BRAND_NAME}: REFERRAL SYSTEM</b> 🎯   ║
╠══════════════════════════════════════╣
║
║  1️⃣ Share your referral link
║  2️⃣ Friend clicks and joins
║  3️⃣ You get <b>1 point</b> ✨
║  4️⃣ 5 points = 1 extra bot slot 🚀
║
║  <b>📊 Your Stats:</b>
║  • Points: {ref_stats['points']}
║  • Referrals: {ref_stats['total']}
║
║  <b>🔗 Your Link:</b>
║  <code>{referral_link}</code>
║
╚══════════════════════════════════════╝
"""

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📤 Share Link", callback_data="share_referral"),
        types.InlineKeyboardButton("📊 My Referrals", callback_data="referral_history")
    )
    markup.add(types.InlineKeyboardButton("⭐ My Points", callback_data="refresh_points"))

    bot.send_message(message.chat.id, text, parse_mode='HTML', reply_markup=markup)

def show_referral_history_for_command(message, user_id):
    ref_stats = get_referral_stats(user_id)

    if ref_stats['total'] == 0:
        bot.send_message(message.chat.id, """
📊 <b>No Referrals Yet</b>

Start sharing your referral link to earn points!
""", parse_mode='HTML')
        return

    text = f"""
╔══════════════════════════════════════╗
║     📊 <b>REFERRAL HISTORY</b> 📊          ║
╠══════════════════════════════════════╣
║
║  👥 <b>Total Referrals:</b> {ref_stats['total']}
║  ⭐ <b>Total Points:</b> {ref_stats['points']}
║
║  <b>Recent Referrals:</b>
"""
    for i, (referred_id, timestamp) in enumerate(ref_stats['recent'][:10], 1):
        username = _lookup_username(referred_id)
        text += f"║  {i}. 👤 {username} ({referred_id})\n"
        text += f"║     🕐 {timestamp[:16]}\n"
    text += "╚══════════════════════════════════════╝"
    bot.send_message(message.chat.id, text, parse_mode='HTML')

def _lookup_username(uid):
    username = "Unknown"
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT username FROM active_users WHERE user_id = ?', (uid,))
        result = c.fetchone()
        if result:
            username = result[0] or str(uid)
        conn.close()
    except Exception:
        pass
    return username

# ============================================
# FILE / ZIP UPLOAD HANDLER — the core upgrade
# ============================================

@bot.message_handler(content_types=['document'])
def handle_document(message):
    user_id = message.from_user.id

    can_upload, status = can_user_upload(user_id)
    if not can_upload:
        bot.reply_to(message, f"❌ Can't upload! {status}")
        return

    file_name = message.document.file_name or f"file_{uuid.uuid4().hex[:8]}"
    file_size = message.document.file_size or 0
    file_ext = file_name.split('.')[-1].lower() if '.' in file_name else 'bin'

    upload_text = f"""
╔══════════════════════════════════════╗
║      📤 <b>{BRAND_NAME}: UPLOADING</b> 📤     ║
╠══════════════════════════════════════╣
║
║  📄 File: <code>{file_name[:25]}</code>
║  📦 Size: {format_size(file_size)}
║
"""
    progress_msg = bot.reply_to(message, upload_text + "║  ⏳ Downloading...\n╚══════════════════════════════════════╝", parse_mode='HTML')

    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
    except Exception as e:
        logger.error(f"{BRAND_NAME} Download error: {e}")
        try:
            bot.edit_message_text(
                upload_text + f"║  ❌ Download failed: {str(e)[:40]}\n╚══════════════════════════════════════╝",
                message.chat.id, progress_msg.message_id, parse_mode='HTML'
            )
        except Exception:
            pass
        return

    try:
        bot.edit_message_text(
            upload_text + "║  📥 Processing...\n╚══════════════════════════════════════╝",
            message.chat.id, progress_msg.message_id, parse_mode='HTML'
        )
    except Exception:
        pass

    # Every upload gets its own isolated folder — prevents filename clashes between bots
    bot_id = f"{user_id}_{uuid.uuid4().hex[:10]}"
    folder_name = bot_id
    user_folder = get_user_folder(user_id)
    bot_folder = os.path.join(user_folder, folder_name)
    os.makedirs(bot_folder, exist_ok=True)

    try:
        if file_ext == 'zip':
            tmp_zip = os.path.join(TMP_DIR, f"{bot_id}.zip")
            with open(tmp_zip, 'wb') as f:
                f.write(downloaded_file)

            try:
                with zipfile.ZipFile(tmp_zip, 'r') as zip_ref:
                    zip_ref.extractall(bot_folder)
            except zipfile.BadZipFile:
                shutil.rmtree(bot_folder, ignore_errors=True)
                os.remove(tmp_zip)
                bot.edit_message_text(
                    upload_text + "║  ❌ Invalid or corrupted ZIP!\n╚══════════════════════════════════════╝",
                    message.chat.id, progress_msg.message_id, parse_mode='HTML'
                )
                return
            finally:
                if os.path.exists(tmp_zip):
                    os.remove(tmp_zip)

            flatten_single_wrapper_folder(bot_folder)
            bot_name = file_name.rsplit('.', 1)[0]
        else:
            # ANY extension is accepted and stored — .py/.js run, everything else is support data
            target_path = os.path.join(bot_folder, file_name)
            with open(target_path, 'wb') as f:
                f.write(downloaded_file)
            bot_name = file_name

        file_count = count_files(bot_folder)
        entry_file, entry_type = find_entry_point(bot_folder)

        user_bots.setdefault(user_id, []).append({
            'bot_id': bot_id,
            'bot_name': bot_name,
            'folder': bot_folder,
            'folder_name': folder_name,
            'entry_file': entry_file,
            'entry_type': entry_type,
            'file_count': file_count,
            'upload_time': datetime.now().isoformat(),
            'user_id': user_id
        })
        save_hosted_bot_db(bot_id, user_id, bot_name, folder_name, entry_file, entry_type, file_count)

        entry_display = entry_file if entry_file else "None found"
        success_text = upload_text + f"""║  ✅ Deployed!
║  📁 Files: {file_count}
║  🎯 Entry: <code>{entry_display[:25] if entry_file else entry_display}</code>
╚══════════════════════════════════════╝
"""
        try:
            bot.edit_message_text(success_text, message.chat.id, progress_msg.message_id, parse_mode='HTML')
        except Exception:
            bot.send_message(message.chat.id, success_text, parse_mode='HTML')

    except Exception as e:
        logger.error(f"{BRAND_NAME} Upload/extract error: {e}")
        shutil.rmtree(bot_folder, ignore_errors=True)
        try:
            bot.edit_message_text(
                upload_text + f"║  ❌ Error: {str(e)[:40]}\n╚══════════════════════════════════════╝",
                message.chat.id, progress_msg.message_id, parse_mode='HTML'
            )
        except Exception:
            bot.reply_to(message, f"❌ Upload failed: {str(e)[:100]}")
        return

    bot_entry = find_bot_by_id(user_id, bot_id)
    if not bot_entry:
        return

    # Auto-install bulk deps (requirements.txt / package.json) then auto-run — no button press needed
    threading.Thread(target=_deploy_and_run, args=(bot_entry, message)).start()

def _deploy_and_run(bot_entry, message):
    try:
        auto_install_bulk_dependencies(bot_entry['folder'], message)
        if bot_entry['entry_file']:
            run_bot_instance_safe(bot_entry, message)
        else:
            bot.send_message(
                message.chat.id,
                f"📦 <b>{bot_entry['bot_name']}</b> stored — no runnable .py/.js entry point was found in it, "
                f"so nothing was started. Use 📂 Check Files to inspect or download it.",
                parse_mode='HTML'
            )
    except Exception as e:
        logger.error(f"{BRAND_NAME} Deploy thread crashed for {bot_entry.get('bot_name')}: {e}")
        try:
            bot.send_message(
                message.chat.id,
                f"❌ <b>Deployment hit an unexpected error</b> for <code>{bot_entry.get('bot_name', 'bot')}</code>:\n"
                f"<code>{str(e)[:300]}</code>\n\nThe files are still saved — check 📂 Check Files to retry running it.",
                parse_mode='HTML'
            )
        except Exception:
            pass

# ============================================
# CALLBACK QUERY HANDLER
# ============================================

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    data = call.data
    try:
        if data == "share_referral":
            share_referral(call)
        elif data == "referral_history":
            show_referral_history(call)
        elif data == "refresh_points":
            refresh_points(call)
        elif data == "copy_referral_link":
            copy_referral_link(call)

        elif data.startswith("bot_"):
            show_bot_actions(call, data[4:])
        elif data.startswith("run_"):
            run_user_bot(call, data[4:])
        elif data.startswith("stop_"):
            stop_user_bot(call, data[5:])
        elif data.startswith("delete_"):
            delete_user_bot_confirm(call, data[7:])
        elif data.startswith("confirm_delete_"):
            confirm_delete_bot(call, data[15:])
        elif data.startswith("cancel_delete_"):
            bot.answer_callback_query(call.id, "❌ Cancelled")
            show_user_files_callback(call)
        elif data.startswith("download_"):
            download_user_bot(call, data[9:])
        elif data.startswith("logs_"):
            show_bot_logs(call, data[5:])
        elif data.startswith("restart_"):
            restart_user_bot(call, data[8:])
        elif data == "back_to_files":
            show_user_files_callback(call)

        elif data == "admin_view_all_files":
            show_all_user_bots_for_admin(call)
        elif data == "admin_top_referrers":
            show_top_referrers(call)
        elif data == "admin_stopall":
            stop_all_bots(call)
        elif data == "admin_refresh":
            refresh_admin_panel(call)
        elif data == "admin_fullstats":
            stats_command(call.message)
        elif data == "admin_logs":
            show_admin_logs(call)
        elif data == "admin_back":
            _fake_message(call, show_admin_panel)
            bot.answer_callback_query(call.id)
        elif data.startswith("admin_user_"):
            show_admin_user_bots(call, int(data[11:]))
        elif data.startswith("admin_bot_"):
            show_admin_bot_actions(call, data[10:])
        elif data.startswith("admin_download_"):
            admin_download_bot(call, data[15:])
        elif data.startswith("admin_run_"):
            admin_run_bot(call, data[10:])
        elif data.startswith("admin_stop_"):
            admin_stop_bot(call, data[11:])
        elif data.startswith("admin_delete_"):
            admin_delete_bot(call, data[13:])
        elif data.startswith("admin_logs_"):
            admin_show_bot_logs(call, data[11:])

    except Exception as e:
        logger.error(f"{BRAND_NAME} Callback error: {e}")
        bot.answer_callback_query(call.id, f"❌ Error: {str(e)[:50]}")

def _fake_message(call, func):
    class FakeMessage:
        def __init__(self, call):
            self.chat = call.message.chat
            self.from_user = call.from_user
    func(FakeMessage(call))

# ============================================
# REFERRAL CALLBACKS
# ============================================

def share_referral(call):
    user_id = call.from_user.id
    referral_link = get_user_referral_link(user_id)
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("📤 Share via Telegram", switch_inline_query=f"Join {BRAND_NAME}! {referral_link}"),
        types.InlineKeyboardButton("🔗 Copy Link", callback_data="copy_referral_link")
    )
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="refresh_points"))
    bot.edit_message_text(
        f"📤 <b>Share Your Referral Link!</b>\n\n<code>{referral_link}</code>\n\nShare with friends and earn points! 🎉",
        call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=markup
    )
    bot.answer_callback_query(call.id)

def copy_referral_link(call):
    referral_link = get_user_referral_link(call.from_user.id)
    bot.answer_callback_query(call.id, "✅ Link ready below")
    bot.send_message(call.message.chat.id, f"📋 <b>Your Referral Link:</b>\n<code>{referral_link}</code>", parse_mode='HTML')

def show_referral_history(call):
    user_id = call.from_user.id
    ref_stats = get_referral_stats(user_id)
    markup = types.InlineKeyboardMarkup()
    if ref_stats['total'] == 0:
        markup.add(types.InlineKeyboardButton("📤 Share Now", callback_data="share_referral"))
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="refresh_points"))
        bot.edit_message_text("📊 <b>No Referrals Yet</b>\n\nStart sharing your referral link!",
                              call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=markup)
        bot.answer_callback_query(call.id)
        return

    text = f"📊 <b>REFERRAL HISTORY</b>\n\n👥 Total: {ref_stats['total']}\n⭐ Points: {ref_stats['points']}\n\n<b>Recent:</b>\n"
    for i, (referred_id, timestamp) in enumerate(ref_stats['recent'][:10], 1):
        username = _lookup_username(referred_id)
        text += f"{i}. 👤 {username} ({referred_id}) — {timestamp[:16]}\n"

    markup.add(types.InlineKeyboardButton("📤 Share More", callback_data="share_referral"))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="refresh_points"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=markup)
    bot.answer_callback_query(call.id)

def refresh_points(call):
    _fake_message(call, show_my_points)
    bot.answer_callback_query(call.id, "🔄 Refreshed!")

# ============================================
# BOT ACTION CALLBACKS (owner)
# ============================================

def show_bot_actions(call, bot_id):
    user_id = call.from_user.id
    b = find_bot_by_id(user_id, bot_id)
    if not b:
        bot.answer_callback_query(call.id, "❌ Bot not found!")
        return
    is_running = is_bot_running_check(bot_id)
    type_icon = "🐍" if b['entry_type'] == 'py' else "🟨" if b['entry_type'] == 'js' else "📄"
    status = "🟢 Running" if is_running else "🔴 Stopped"
    text = f"""
╔══════════════════════════════════════╗
║       🤖 <b>{BRAND_NAME}: BOT</b> 🤖          ║
╠══════════════════════════════════════╣
║
║  {type_icon} <b>Name:</b> <code>{b['bot_name'][:25]}</code>
║  🎯 <b>Entry:</b> <code>{(b['entry_file'] or 'None')[:25]}</code>
║  📁 <b>Files:</b> {b['file_count']}
║  📊 <b>Status:</b> {status}
║
╚══════════════════════════════════════╝
"""
    markup = get_bot_actions_keyboard(bot_id, is_running)
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=markup)
    except Exception:
        bot.send_message(call.message.chat.id, text, parse_mode='HTML', reply_markup=markup)
    bot.answer_callback_query(call.id)

def run_user_bot(call, bot_id):
    user_id = call.from_user.id
    b = find_bot_by_id(user_id, bot_id)
    if not b:
        bot.answer_callback_query(call.id, "❌ Bot not found!")
        return
    if is_bot_running_check(bot_id):
        bot.answer_callback_query(call.id, "⚠️ Already running!")
        return
    bot.answer_callback_query(call.id, "🚀 Starting...")
    threading.Thread(target=run_bot_instance_safe, args=(b, call.message)).start()

def stop_user_bot(call, bot_id):
    user_id = call.from_user.id
    if bot_id not in bot_scripts:
        bot.answer_callback_query(call.id, "❌ Not running!")
        return
    bot.answer_callback_query(call.id, "🛑 Stopping...")
    b = find_bot_by_id(user_id, bot_id)
    script_info = bot_scripts.get(bot_id)
    if script_info:
        kill_process_tree(script_info)
        cleanup_script(bot_id)
        time.sleep(1)
        bot_name = b['bot_name'] if b else bot_id
        success_text = f"✅ <b>Stopped!</b>\n🤖 <code>{bot_name[:25]}</code>"
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("▶️ Run Again", callback_data=f"run_{bot_id}"),
            types.InlineKeyboardButton("🔙 Back", callback_data="back_to_files")
        )
        try:
            bot.edit_message_text(success_text, call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=markup)
        except Exception:
            bot.send_message(call.message.chat.id, success_text, parse_mode='HTML', reply_markup=markup)
        log_action(user_id, "BOT_STOP", f"Stopped {bot_name}")

def restart_user_bot(call, bot_id):
    if bot_id in bot_scripts:
        kill_process_tree(bot_scripts[bot_id])
        cleanup_script(bot_id)
        time.sleep(1)
    run_user_bot(call, bot_id)

def delete_user_bot_confirm(call, bot_id):
    user_id = call.from_user.id
    b = find_bot_by_id(user_id, bot_id)
    if not b:
        bot.answer_callback_query(call.id, "❌ Bot not found!")
        return
    if is_bot_running_check(bot_id):
        bot.answer_callback_query(call.id, "⚠️ Stop the bot first!")
        return
    confirm_text = f"⚠️ <b>Delete '{b['bot_name'][:25]}'?</b>\n\nThis cannot be undone!"
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ Yes, Delete", callback_data=f"confirm_delete_{bot_id}"),
        types.InlineKeyboardButton("❌ No", callback_data=f"cancel_delete_{bot_id}")
    )
    try:
        bot.edit_message_text(confirm_text, call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=markup)
    except Exception:
        pass
    bot.answer_callback_query(call.id)

def confirm_delete_bot(call, bot_id):
    user_id = call.from_user.id
    b = find_bot_by_id(user_id, bot_id)
    if not b:
        bot.answer_callback_query(call.id, "❌ Bot not found!")
        return
    try:
        shutil.rmtree(b['folder'], ignore_errors=True)
        user_bots[user_id] = [x for x in user_bots.get(user_id, []) if x['bot_id'] != bot_id]
        remove_hosted_bot_db(bot_id)
        log_action(user_id, "BOT_DELETE", f"Deleted {b['bot_name']}")
        success_text = f"✅ <b>Deleted!</b>\n🤖 <code>{b['bot_name'][:25]}</code>"
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("📂 Back to Bots", callback_data="back_to_files"))
        try:
            bot.edit_message_text(success_text, call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=markup)
        except Exception:
            bot.send_message(call.message.chat.id, success_text, parse_mode='HTML', reply_markup=markup)
        bot.answer_callback_query(call.id, "✅ Deleted!")
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ Error: {str(e)[:30]}")

def download_user_bot(call, bot_id):
    user_id = call.from_user.id
    b = find_bot_by_id(user_id, bot_id)
    if not b:
        bot.answer_callback_query(call.id, "❌ Bot not found!")
        return
    bot.answer_callback_query(call.id, "📥 Preparing...")
    _send_bot_as_file(call.message.chat.id, b)

def _send_bot_as_file(chat_id, b):
    try:
        files_in_folder = []
        for root, dirs, files in os.walk(b['folder']):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            files_in_folder.extend(files)

        if len(files_in_folder) == 1:
            single_path = os.path.join(b['folder'], files_in_folder[0])
            with open(single_path, 'rb') as f:
                bot.send_document(chat_id, f, caption=f"📄 {b['bot_name']}")
        else:
            archive_base = os.path.join(TMP_DIR, f"dl_{b['bot_id']}")
            archive_path = shutil.make_archive(archive_base, 'zip', b['folder'])
            with open(archive_path, 'rb') as f:
                bot.send_document(chat_id, f, caption=f"📦 {b['bot_name']}.zip ({b['file_count']} files)")
            os.remove(archive_path)
    except Exception as e:
        bot.send_message(chat_id, f"❌ Download failed: {str(e)[:100]}")

def show_bot_logs(call, bot_id):
    log_path = os.path.join(LOGS_DIR, f"{bot_id}.log")
    if not os.path.exists(log_path):
        bot.answer_callback_query(call.id, "📋 No logs yet")
        return
    try:
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            logs = f.read()[-2000:] or "No output yet..."
        log_text = f"📋 <b>{BRAND_NAME}: LOGS</b>\n\n<code>{logs[:1800]}</code>"
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🔄 Refresh", callback_data=f"logs_{bot_id}"),
            types.InlineKeyboardButton("🔙 Back", callback_data=f"bot_{bot_id}")
        )
        try:
            bot.edit_message_text(log_text, call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=markup)
        except Exception:
            bot.answer_callback_query(call.id, "📋 Logs unchanged")
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ Error: {str(e)[:30]}")

def show_user_files_callback(call):
    _fake_message(call, show_user_files)
    bot.answer_callback_query(call.id)

# ============================================
# ADMIN CALLBACKS
# ============================================

def show_all_user_bots_for_admin(call):
    if call.from_user.id != OWNER_ID and call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    if not user_bots:
        bot.edit_message_text("📂 <b>No bots hosted yet!</b>", call.message.chat.id, call.message.message_id, parse_mode='HTML')
        bot.answer_callback_query(call.id)
        return

    markup = types.InlineKeyboardMarkup(row_width=1)
    for uid in sorted(user_bots.keys())[:25]:
        count = len(user_bots[uid])
        username = _lookup_username(uid)
        badge = "👑" if uid == OWNER_ID else "⭐" if uid in admin_ids else "👤"
        markup.add(types.InlineKeyboardButton(f"{badge} {username[:15]} ({count} bots)", callback_data=f"admin_user_{uid}"))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="admin_back"))

    text = f"📂 <b>{BRAND_NAME}: ALL USER BOTS</b>\n\nUsers: {len(user_bots)}\nTotal Bots: {sum(len(b) for b in user_bots.values())}"
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=markup)
    except Exception:
        bot.send_message(call.message.chat.id, text, parse_mode='HTML', reply_markup=markup)
    bot.answer_callback_query(call.id)

def show_admin_user_bots(call, target_user):
    if call.from_user.id != OWNER_ID and call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    bots = user_bots.get(target_user, [])
    if not bots:
        bot.answer_callback_query(call.id, "📂 No bots!")
        return
    username = _lookup_username(target_user)
    text = f"📂 <b>{username}'s Bots</b> (ID: {target_user})\n\nSelect one:"
    markup = types.InlineKeyboardMarkup(row_width=1)
    for b in bots[:25]:
        is_running = is_bot_running_check(b['bot_id'])
        status = "🟢" if is_running else "🔴"
        type_icon = "🐍" if b['entry_type'] == 'py' else "🟨" if b['entry_type'] == 'js' else "📦"
        markup.add(types.InlineKeyboardButton(f"{status} {type_icon} {b['bot_name'][:20]}", callback_data=f"admin_bot_{b['bot_id']}"))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="admin_view_all_files"))
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=markup)
    except Exception:
        bot.send_message(call.message.chat.id, text, parse_mode='HTML', reply_markup=markup)
    bot.answer_callback_query(call.id)

def show_admin_bot_actions(call, bot_id):
    if call.from_user.id != OWNER_ID and call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    owner_id, b = find_bot_anywhere(bot_id)
    if not b:
        bot.answer_callback_query(call.id, "❌ Bot not found!")
        return
    is_running = is_bot_running_check(bot_id)
    status = "🟢 Running" if is_running else "🔴 Stopped"
    text = f"""
👑 <b>Bot Management</b>

👤 Owner: {owner_id}
🤖 Name: <code>{b['bot_name'][:25]}</code>
🎯 Entry: <code>{(b['entry_file'] or 'None')[:25]}</code>
📊 Status: {status}
"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("📥 Download", callback_data=f"admin_download_{bot_id}"))
    if is_running:
        markup.add(
            types.InlineKeyboardButton("🛑 Stop", callback_data=f"admin_stop_{bot_id}"),
            types.InlineKeyboardButton("📋 Logs", callback_data=f"admin_logs_{bot_id}")
        )
    else:
        markup.add(
            types.InlineKeyboardButton("▶️ Run", callback_data=f"admin_run_{bot_id}"),
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f"admin_delete_{bot_id}")
        )
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data=f"admin_user_{owner_id}"))
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=markup)
    except Exception:
        bot.send_message(call.message.chat.id, text, parse_mode='HTML', reply_markup=markup)
    bot.answer_callback_query(call.id)

def admin_download_bot(call, bot_id):
    if call.from_user.id != OWNER_ID and call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    _, b = find_bot_anywhere(bot_id)
    if not b:
        bot.answer_callback_query(call.id, "❌ Bot not found!")
        return
    bot.answer_callback_query(call.id, "📥 Preparing...")
    _send_bot_as_file(call.message.chat.id, b)
    log_action(call.from_user.id, "ADMIN_DOWNLOAD", f"Downloaded {b['bot_name']}")

def admin_run_bot(call, bot_id):
    if call.from_user.id != OWNER_ID and call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    _, b = find_bot_anywhere(bot_id)
    if not b:
        bot.answer_callback_query(call.id, "❌ Bot not found!")
        return
    if is_bot_running_check(bot_id):
        bot.answer_callback_query(call.id, "⚠️ Already running!")
        return
    bot.answer_callback_query(call.id, f"🚀 Running {b['bot_name']}...")
    threading.Thread(target=run_bot_instance_safe, args=(b, call.message)).start()

def admin_stop_bot(call, bot_id):
    if call.from_user.id != OWNER_ID and call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    if bot_id not in bot_scripts:
        bot.answer_callback_query(call.id, "❌ Not running!")
        return
    bot.answer_callback_query(call.id, "🛑 Stopping...")
    kill_process_tree(bot_scripts[bot_id])
    cleanup_script(bot_id)
    time.sleep(1)
    bot.send_message(call.message.chat.id, "✅ Stopped!")

def admin_delete_bot(call, bot_id):
    if call.from_user.id != OWNER_ID and call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    owner_id, b = find_bot_anywhere(bot_id)
    if not b:
        bot.answer_callback_query(call.id, "❌ Bot not found!")
        return
    if is_bot_running_check(bot_id):
        bot.answer_callback_query(call.id, "⚠️ Stop first!")
        return
    try:
        shutil.rmtree(b['folder'], ignore_errors=True)
        user_bots[owner_id] = [x for x in user_bots.get(owner_id, []) if x['bot_id'] != bot_id]
        remove_hosted_bot_db(bot_id)
        bot.answer_callback_query(call.id, "✅ Deleted!")
        bot.send_message(call.message.chat.id, f"✅ Deleted {b['bot_name']} (owner: {owner_id})")
        show_admin_user_bots(call, owner_id)
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ Error: {str(e)[:30]}")

def admin_show_bot_logs(call, bot_id):
    if call.from_user.id != OWNER_ID and call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    log_path = os.path.join(LOGS_DIR, f"{bot_id}.log")
    if not os.path.exists(log_path):
        bot.answer_callback_query(call.id, "📋 No logs")
        return
    try:
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            logs = f.read()[-2000:] or "No output yet..."
        log_text = f"👑 <b>Bot Logs</b>\n\n<code>{logs[:1800]}</code>"
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🔄 Refresh", callback_data=f"admin_logs_{bot_id}"),
            types.InlineKeyboardButton("🔙 Back", callback_data=f"admin_bot_{bot_id}")
        )
        try:
            bot.edit_message_text(log_text, call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=markup)
        except Exception:
            bot.answer_callback_query(call.id, "📋 Logs unchanged")
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ Error: {str(e)[:30]}")

def show_top_referrers(call):
    if call.from_user.id != OWNER_ID and call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT user_id, points, total_referrals FROM user_points ORDER BY points DESC LIMIT 20')
        top_users = c.fetchall()
        conn.close()
        if not top_users:
            bot.answer_callback_query(call.id, "📊 No data!")
            return
        text = f"🏆 <b>{BRAND_NAME} TOP REFERRERS</b>\n\n"
        medals = ["🥇", "🥈", "🥉"]
        for i, (uid, points, refs) in enumerate(top_users, 1):
            medal = medals[i - 1] if i <= 3 else f"{i}."
            username = _lookup_username(uid)
            text += f"{medal} {username[:15]} | ⭐{points} | 👥{refs}\n"
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="admin_back"))
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=markup)
        bot.answer_callback_query(call.id)
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ {str(e)[:30]}")

def stop_all_bots(call):
    if call.from_user.id != OWNER_ID and call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    bot.answer_callback_query(call.id, f"🛑 Stopping all {BRAND_NAME} bots...")
    stopped = 0
    for bot_id in list(bot_scripts.keys()):
        try:
            kill_process_tree(bot_scripts[bot_id])
            cleanup_script(bot_id)
            stopped += 1
        except Exception:
            pass
    bot.send_message(call.message.chat.id, f"✅ Stopped {stopped} bots!")

def refresh_admin_panel(call):
    if call.from_user.id != OWNER_ID and call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    _fake_message(call, show_admin_panel)
    bot.answer_callback_query(call.id, "🔄 Refreshed!")

def show_admin_logs(call):
    if call.from_user.id != OWNER_ID and call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT user_id, action, details, timestamp FROM bot_logs ORDER BY id DESC LIMIT 20')
        logs = c.fetchall()
        conn.close()
        if logs:
            text = f"📋 <b>{BRAND_NAME}: RECENT LOGS</b>\n"
            for log in logs:
                text += f"👤 {log[0]} | {log[1]}\n{str(log[2])[:30]}...\n🕐 {log[3][:16]}\n"
        else:
            text = "📋 No logs."
        bot.send_message(call.message.chat.id, text[:4000], parse_mode='HTML')
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ Error: {str(e)[:30]}")

# ============================================
# CLEANUP ON EXIT
# ============================================

def cleanup_on_exit():
    logger.info(f"Cleaning up {BRAND_NAME}...")
    for bot_id in list(bot_scripts.keys()):
        try:
            kill_process_tree(bot_scripts[bot_id])
        except Exception:
            pass
    logger.info(f"{BRAND_NAME} Cleanup complete.")

atexit.register(cleanup_on_exit)

# ============================================
# MAIN
# ============================================

def main():
    logger.info("=" * 50)
    logger.info(f"🤖 Starting {BRAND_NAME} {BRAND_EMOJI} Bot...")

    init_db()
    load_data()

    logger.info(f"📁 Base Dir: {BASE_DIR}")
    logger.info(f"📁 Upload Dir: {UPLOAD_BOTS_DIR}")
    logger.info(f"💾 Database: {'Turso' if TURSO_URL and TURSO_TOKEN else 'Local SQLite'}")
    logger.info("=" * 50)

    keep_alive()
    while True:
        try:
            logger.info(f"🚀 Starting {BRAND_NAME} bot polling...")
            bot.infinity_polling(timeout=60, long_polling_timeout=30)
        except requests.exceptions.ConnectionError:
            logger.error(f"{BRAND_NAME} Connection error! Retrying...")
            time.sleep(10)
        except requests.exceptions.ReadTimeout:
            logger.error(f"{BRAND_NAME} Read timeout! Retrying...")
            time.sleep(5)
        except Exception as e:
            logger.error(f"{BRAND_NAME} error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
