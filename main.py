# -*- coding: utf-8 -*-
import telebot
import subprocess
import os
import zipfile
import shutil
from telebot import types
from telebot.apihelper import ApiTelegramException
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
import html
import resource
from functools import wraps
from flask import Flask
from threading import Thread


def esc(value):
    """HTML-escapes dynamic content before it goes into an HTML-parsed Telegram message.
    Without this, any '<', '>', or '&' in a filename, error message, program log,
    or even a user's Telegram display name breaks Telegram's HTML parser and the
    whole message silently fails to send."""
    return html.escape(str(value), quote=False)

# ============================================
# TURSO DATABASE SETUP
# ============================================

try:
    import libsql_experimental as libsql
    TURSO_AVAILABLE = True
except ImportError:
    TURSO_AVAILABLE = False
    print("⚠️ Turso not installed, using local SQLite")

TURSO_URL = os.environ.get('TURSO_URL', '').strip()
TURSO_TOKEN = os.environ.get('TURSO_TOKEN', '').strip()

# ============================================
# TELEGRAM AS STORAGE (files offload to owner's private channel)
# ============================================
# STORAGE_CHANNEL_ID: chat id of the PRIVATE channel/group where user bot files are archived.
#   The bot MUST be an ADMIN in this channel (with post + delete messages rights).
#   Get it by forwarding a message from the channel to @userinfobot, or from channel info.
STORAGE_CHANNEL_ID = os.environ.get('STORAGE_CHANNEL_ID', '').strip()

def _int_env(name, default, minimum=0):
    try:
        value = int(os.environ.get(name, str(default)) or str(default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)

MAX_UPLOAD_MB = _int_env('MAX_UPLOAD_MB', 0)  # 0 = no configured per-file cap
# These are safety limits for archive extraction, not Telegram upload caps.
# They prevent zip-slip and decompression-bomb attacks while keeping ordinary bot zips unrestricted.
MAX_ZIP_ENTRIES = _int_env('MAX_ZIP_ENTRIES', 10000, 1)
MAX_ZIP_UNCOMPRESSED_MB = _int_env('MAX_ZIP_UNCOMPRESSED_MB', 4096, 1)
WATCHDOG_INTERVAL_SECONDS = _int_env('WATCHDOG_INTERVAL_SECONDS', 60, 1)
MAX_AUTO_RESTARTS_PER_HOUR = _int_env('MAX_AUTO_RESTARTS_PER_HOUR', 5, 1)

DB_BACKEND = 'sqlite'
DB_LAST_ERROR = ''


def _local_db_connection():
    return sqlite3.connect(DATABASE_PATH, check_same_thread=False, isolation_level=None)


def get_db_connection():
    """Return Turso when configured and reachable, otherwise an explicit local fallback.

    The fallback keeps the process alive, but DB_BACKEND/DB_LAST_ERROR make the loss of
    persistence visible in diagnostics instead of falsely claiming Turso is connected.
    """
    global DB_BACKEND, DB_LAST_ERROR
    if TURSO_URL or TURSO_TOKEN:
        if not (TURSO_AVAILABLE and TURSO_URL and TURSO_TOKEN):
            DB_BACKEND = 'sqlite-fallback'
            DB_LAST_ERROR = 'Turso package, URL, or token is missing'
            print(f'❌ Turso is configured but unavailable: {DB_LAST_ERROR}')
            return _local_db_connection()
        if not (TURSO_URL.startswith('libsql://') or TURSO_URL.startswith('https://')):
            DB_BACKEND = 'sqlite-fallback'
            DB_LAST_ERROR = 'TURSO_URL must start with libsql:// or https://'
            print(f'❌ Turso is configured but invalid: {DB_LAST_ERROR}')
            return _local_db_connection()
        try:
            conn = libsql.connect(database=TURSO_URL, auth_token=TURSO_TOKEN)
            DB_BACKEND = 'turso'
            DB_LAST_ERROR = ''
            return conn
        except Exception as e:
            DB_BACKEND = 'sqlite-fallback'
            DB_LAST_ERROR = str(e)[:300]
            print(f'❌ Turso connection failed; using local SQLite fallback: {DB_LAST_ERROR}')
            return _local_db_connection()
    DB_BACKEND = 'sqlite'
    DB_LAST_ERROR = ''
    return _local_db_connection()

# ============================================
# CONFIGURATION
# ============================================

TOKEN = os.environ.get('BOT_TOKEN', '')

def _clean_env_value(value):
    """Normalize values copied into Railway variables without weakening validation."""
    if value is None:
        return ''
    value = str(value).strip().lstrip('\ufeff')
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()
    return value


def _required_int_env(name, aliases=()):
    """Read a required numeric env var, accepting common legacy spellings."""
    names = (name,) + tuple(aliases)
    raw = ''
    used_name = name
    for candidate in names:
        candidate_value = _clean_env_value(os.environ.get(candidate))
        if candidate_value:
            raw = candidate_value
            used_name = candidate
            break
    if not raw:
        print(f'❌ FATAL: {name} environment variable is required. Set {name}=your_numeric_telegram_user_id in the same Railway service/environment, then redeploy.')
        sys.exit(1)
    try:
        parsed = int(raw)
    except ValueError:
        print(f'❌ FATAL: {used_name} must contain only a numeric Telegram user ID, for example 123456789. Received an invalid value.')
        sys.exit(1)
    if parsed <= 0:
        print(f'❌ FATAL: {used_name} must be a positive numeric Telegram user ID.')
        sys.exit(1)
    return parsed


OWNER_ID = _required_int_env('OWNER_ID', aliases=('OWNERID', 'OWNER_USER_ID'))
ADMIN_ID = _required_int_env('ADMIN_ID', aliases=('ADMINID', 'ADMIN_USER_ID')) if any(_clean_env_value(os.environ.get(k)) for k in ('ADMIN_ID', 'ADMINID', 'ADMIN_USER_ID')) else OWNER_ID
YOUR_USERNAME = os.environ.get('USERNAME', '@Senzo268')
UPDATE_CHANNEL = os.environ.get('CHANNEL', 'https://telegram.me/Senzo_Official')

BRAND_NAME = "SENZO DEV"
BRAND_EMOJI = "🐺"

START_IMAGE_URL = "https://i.postimg.cc/Jn3JGHwS/cvn-on-Tik-Tok.jpg"

START_DESCRIPTION = """
🚀 <b>Upload & Host Your Bots</b>
📤 <b>Supported:</b> ANY file type • ZIP auto-deploy
⭐ <b>Earn Points:</b> 1 Point per Referral
🎯 <b>Points unlock extra bot slots</b> (free tier capped at 10)
💎 <b>Free:</b> Starts with 2 bots; level bonuses also increase quota
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
VENV_DIR_NAME = '.bot_venv'
MAX_SINGLE_FILE_BYTES = _int_env('MAX_SINGLE_FILE_MB', 512, 1) * 1024 * 1024
MAX_DEPENDENCY_INSTALL_MB = _int_env('MAX_DEPENDENCY_INSTALL_MB', 1024, 64) * 1024 * 1024

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

bot = telebot.TeleBot(TOKEN, parse_mode='HTML', threaded=True, num_threads=10)

# script_key (bot_id) -> {process, log_file, log_path, start_time, entry_file, entry_type, folder, user_id, bot_name}
bot_scripts = {}
user_subscriptions = {}
zip_browser_tokens = {}
# Telegram Bot API has a finite send_document limit. Keep a conservative preflight
# so users receive a clear message instead of a predictable HTTP 413.
TELEGRAM_SEND_SAFE_MB = _int_env('TELEGRAM_SEND_SAFE_MB', 49, 1)
TELEGRAM_SEND_SAFE_BYTES = TELEGRAM_SEND_SAFE_MB * 1024 * 1024
bot_crash_counts = {}

# user_id -> [ {bot_id, bot_name, folder, entry_file, entry_type, upload_time, file_count} ]
user_bots = {}

# Protect shared dictionaries because TeleBot handlers, watchdog, and process threads
# can mutate them concurrently. The lock is intentionally re-entrant for nested helpers.
state_lock = threading.RLock()
shutdown_lock = threading.Lock()
shutdown_started = False

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
    logger.info(f"🌐 Starting Flask keep-alive on port {port}...")
    try:
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"🌐 Flask error: {e}")

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
        entry_file TEXT, entry_type TEXT, file_count INTEGER, upload_time TEXT,
        was_running INTEGER DEFAULT 0)''')
        c.execute('''CREATE TABLE IF NOT EXISTS stored_files
        (bot_id TEXT PRIMARY KEY,
         chat_id TEXT,
         file_size INTEGER,
         stored_at TEXT,
         owner_username TEXT,
         owner_chat_id TEXT,
         file_id TEXT,
         message_id TEXT,
         storage_chat_id TEXT)''')

        # Backward-compatible migrations for databases created by earlier versions.
        for table, column, definition in (
            ('hosted_bots', 'was_running', 'INTEGER DEFAULT 0'),
            ('stored_files', 'file_id', 'TEXT'),
            ('stored_files', 'message_id', 'TEXT'),
            ('stored_files', 'storage_chat_id', 'TEXT'),
        ):
            try:
                c.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
            except Exception:
                pass


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

        now = datetime.now()
        c.execute('SELECT user_id, expiry FROM subscriptions')
        for user_id, expiry in c.fetchall():
            try:
                parsed_expiry = datetime.fromisoformat(expiry)
                if parsed_expiry > now:
                    user_subscriptions[user_id] = {'expiry': parsed_expiry}
                else:
                    c.execute('DELETE FROM subscriptions WHERE user_id = ?', (user_id,))
            except (TypeError, ValueError):
                c.execute('DELETE FROM subscriptions WHERE user_id = ?', (user_id,))

        c.execute('SELECT bot_id, user_id, bot_name, folder_name, entry_file, entry_type, file_count, upload_time, was_running FROM hosted_bots')
        for bot_id, uid, bot_name, folder_name, entry_file, entry_type, file_count, upload_time, was_running in c.fetchall():
            user_bots.setdefault(uid, []).append({
                'bot_id': bot_id,
                'bot_name': bot_name,
                'folder': os.path.join(get_user_folder(uid), folder_name),
                'folder_name': folder_name,
                'entry_file': entry_file,
                'entry_type': entry_type,
                'file_count': file_count,
                'upload_time': upload_time,
                'user_id': uid,
                'was_running': bool(was_running)
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
    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('''INSERT OR REPLACE INTO hosted_bots
        (bot_id, user_id, bot_name, folder_name, entry_file, entry_type, file_count, upload_time, was_running)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT was_running FROM hosted_bots WHERE bot_id = ?), 0))''',
        (bot_id, user_id, bot_name, folder_name, entry_file, entry_type, file_count, datetime.now().isoformat(), bot_id))
        conn.commit()
        log_action(user_id, "BOT_UPLOAD", f"Uploaded {esc(bot_name)}")
        return True
    except Exception as e:
        logger.error(f"{BRAND_NAME} Error saving hosted bot: {e}")
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def set_bot_running_state(bot_id, is_running):
    """Persist whether a bot should be resumed after a process restart."""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('UPDATE hosted_bots SET was_running = ? WHERE bot_id = ?', (1 if is_running else 0, bot_id))
        conn.commit()
        conn.close()
        with state_lock:
            _uid, entry = find_bot_anywhere(bot_id)
            if entry is not None:
                entry['was_running'] = bool(is_running)
        return True
    except Exception as e:
        logger.error(f"{BRAND_NAME} Error saving running state for {bot_id}: {e}")
        return False

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
        return True
    except Exception as e:
        logger.error(f"{BRAND_NAME} Error saving subscription: {e}")
        return False

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

def get_active_subscription(user_id):
    """Return an active subscription and remove expired in-memory entries."""
    with state_lock:
        subscription = user_subscriptions.get(user_id)
        if not subscription:
            return None
        if subscription.get('expiry') and subscription['expiry'] > datetime.now():
            return subscription
        user_subscriptions.pop(user_id, None)
        return None


def get_user_max_bots(user_id):
    base_limit = 2
    points = get_user_points(user_id)['points']
    extra_bots = points // 5
    if user_id == OWNER_ID or user_id in admin_ids:
        return float('inf')
    if get_active_subscription(user_id):
        return SUBSCRIBED_USER_LIMIT + (points // 10) * PER_LEVEL_BONUS_BOTS
    return min(base_limit + extra_bots + (points // 10) * PER_LEVEL_BONUS_BOTS, FREE_USER_LIMIT)

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

# ============================================
# LEVEL / TIER SYSTEM
# ============================================
# Per-tier perks (additive on top of the existing points logic - nothing breaks):
#   FREE       - default level, starts with 2 slots and unlocks up to 10 via points, 500 MB storage quota
#   SUBSCRIBED - 15 bot slots + level bonuses, 2 GB storage quota (active subscription)
#   PRO        - unlimited slots & storage (admin/owner)
#
# A user's LEVEL is shown via /level and is derived automatically:
#   level = points // 10  (level 0 = free, level 1 = 10 pts, ...)
#   'PRO' badge when subscription active, 'ADMIN' for owner/admins. Free slots remain capped at 10.
PER_LEVEL_BONUS_BOTS = 2        # +2 extra bot slots per level (stacks with points // 5 rule)
PER_LEVEL_QUOTA_MB = 250        # +250 MB storage quota per level
FREE_QUOTA_MB = 500
SUBSCRIBED_QUOTA_MB = 2 * 1024


def get_user_level(user_id):
    points = get_user_points(user_id)['points']
    level = points // 10
    if user_id == OWNER_ID or user_id in admin_ids:
        return 'ADMIN', level
    if get_active_subscription(user_id):
        return 'PRO', level
    return 'FREE', level


def get_user_quota_mb(user_id):
    tier, level = get_user_level(user_id)
    if tier == 'ADMIN':
        return None  # None = unlimited
    base = SUBSCRIBED_QUOTA_MB if tier == 'PRO' else FREE_QUOTA_MB
    return base + level * PER_LEVEL_QUOTA_MB


def get_user_folder_size_mb(user_id):
    total = 0
    with state_lock:
        bots = list(user_bots.get(user_id, []))
    for b in bots:
        folder = b.get('folder')
        if folder and os.path.isdir(folder):
            for root, _dirs, files in os.walk(folder):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
    return total / (1024 * 1024)


def can_user_upload_space(user_id):
    quota = get_user_quota_mb(user_id)
    if quota is None:
        return True, ""
    used = get_user_folder_size_mb(user_id)
    if used < quota:
        return True, ""
    return False, f"Your storage quota is full ({used:.0f}/{quota:.0f} MB). Delete an old bot to free space, or earn levels (more points = more quota)."



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
        return False, f"❌ Error: {esc(str(e)[:50])}"

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
    if get_active_subscription(user_id):
        return SUBSCRIBED_USER_LIMIT
    return FREE_USER_LIMIT

def is_bot_running_check(script_key):
    with state_lock:
        script_info = bot_scripts.get(script_key)
        process = script_info.get('process') if script_info else None
    if process:
        try:
            proc = psutil.Process(process.pid)
            return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except Exception:
            return False
    return False

def cleanup_script(script_key):
    with state_lock:
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
    with state_lock:
        for b in user_bots.get(user_id, []):
            if b['bot_id'] == bot_id:
                return b
    return None

def find_bot_anywhere(bot_id):
    """Admins need to reach a bot without knowing which user owns it."""
    with state_lock:
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
IGNORED_DIRS = {'__pycache__', 'node_modules', '.git', '.idea', '.vscode', 'venv', '.venv', VENV_DIR_NAME}

def contains_host_secret(folder):
    '''Reject accidental copies of this host's token; never let an upload poll this bot.'''
    if not TOKEN:
        return False
    secret = TOKEN.encode('utf-8')
    try:
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith('__MACOSX')]
            for name in files:
                path = os.path.join(root, name)
                try:
                    if os.path.getsize(path) > MAX_SINGLE_FILE_BYTES:
                        continue
                    with open(path, 'rb') as fh:
                        if secret in fh.read():
                            return True
                except (OSError, UnicodeError):
                    continue
    except OSError:
        return False
    return False

def safe_extract_zip(zip_path, destination):
    """Extract a ZIP without zip-slip, symlink, entry-count, or bomb surprises."""
    destination = os.path.abspath(destination)
    max_total = MAX_ZIP_UNCOMPRESSED_MB * 1024 * 1024
    with zipfile.ZipFile(zip_path, 'r') as archive:
        members = archive.infolist()
        if len(members) > MAX_ZIP_ENTRIES:
            raise ValueError(f'ZIP contains too many entries (limit {MAX_ZIP_ENTRIES})')
        total_size = 0
        for info in members:
            name = info.filename.replace('\\', '/')
            if not name or name.startswith('/') or name.startswith('\\'):
                raise ValueError('ZIP contains an absolute path')
            parts = [part for part in name.split('/') if part not in ('', '.') ]
            if any(part == '..' for part in parts):
                raise ValueError('ZIP contains a path traversal entry')
            # Do not extract symbolic links from untrusted archives.
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError('ZIP contains an unsupported symbolic link')
            total_size += max(0, info.file_size)
            if total_size > max_total:
                raise ValueError(f'ZIP uncompressed size exceeds {MAX_ZIP_UNCOMPRESSED_MB} MB safety limit')
            target = os.path.abspath(os.path.join(destination, *parts))
            if os.path.commonpath([destination, target]) != destination:
                raise ValueError('ZIP entry escapes destination')
        os.makedirs(destination, exist_ok=True)
        for info in members:
            name = info.filename.replace('\\', '/')
            parts = [part for part in name.split('/') if part not in ('', '.') ]
            target = os.path.abspath(os.path.join(destination, *parts))
            if name.endswith('/') or info.is_dir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with archive.open(info, 'r') as src, open(target, 'wb') as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)


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
                candidate_path = os.path.abspath(os.path.join(folder, candidate))
                if os.path.commonpath([os.path.abspath(folder), candidate_path]) == os.path.abspath(folder) and os.path.isfile(candidate_path):
                    return os.path.relpath(candidate_path, folder), 'js'
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
# TELEGRAM-AS-STORAGE ENGINE
# ============================================
# Offloads every uploaded bot's FILES to the owner's private Telegram channel.
# Railway's disk keeps ONLY what is needed for running bots. This is what keeps
# disk space from ever filling up - files live on Telegram (free, unlimited),
# and their permanent address (message id in the channel) is saved in the
# Turso/SQLite DB. Flow: UPLOAD -> disk, STOP -> archive to channel + disk wipe,
# RUN -> restore from channel -> start, DELETE -> remove channel copy too.

def _storage_enabled():
    return bool(STORAGE_CHANNEL_ID)


def get_stored_record(bot_id):
    if not _storage_enabled():
        return None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT chat_id, file_size, stored_at, owner_username, owner_chat_id, file_id, message_id, storage_chat_id FROM stored_files WHERE bot_id = ?', (bot_id,))
        row = c.fetchone()
        conn.close()
        if row:
            # New rows have an explicit file_id. Older rows used chat_id for a
            # Telegram identifier, but a numeric value is normally a message_id,
            # not a document file_id, so do not use it for restore.
            explicit_file_id = row[5]
            legacy_value = row[0]
            usable_file_id = explicit_file_id or (
                legacy_value if legacy_value and not str(legacy_value).lstrip('-').isdigit() else None
            )
            return {'chat_id': row[0], 'file_size': row[1], 'stored_at': row[2],
                    'owner_username': row[3], 'owner_chat_id': row[4],
                    'file_id': usable_file_id, 'message_id': row[6],
                    'storage_chat_id': row[7] or STORAGE_CHANNEL_ID}
    except Exception as e:
        logger.error(f"{BRAND_NAME} stored_files read error: {e}")
    return None


def save_stored_record(bot_id, file_id, file_size, owner_username, owner_chat_id, message_id=None):
    if not _storage_enabled():
        return False
    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO stored_files '
                  '(bot_id, chat_id, file_size, stored_at, owner_username, owner_chat_id, file_id, message_id, storage_chat_id) '
                  'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                  (bot_id, str(file_id), int(file_size), datetime.now().isoformat(),
                   owner_username, str(owner_chat_id), str(file_id),
                   str(message_id) if message_id is not None else None, str(STORAGE_CHANNEL_ID)))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"{BRAND_NAME} stored_files save error: {e}")
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def remove_stored_record(bot_id):
    if not _storage_enabled():
        return
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('DELETE FROM stored_files WHERE bot_id = ?', (bot_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"{BRAND_NAME} stored_files delete error: {e}")


def delete_stored_telegram_message(record):
    """Delete a new-format archive message; never mistake a file_id for a message ID."""
    if not record:
        return False
    message_id = record.get('message_id')
    storage_chat_id = record.get('storage_chat_id') or STORAGE_CHANNEL_ID
    if message_id is None or not str(message_id).lstrip('-').isdigit():
        logger.warning(f'{BRAND_NAME} archive row has no numeric message_id; retaining Telegram copy')
        return False
    try:
        bot.delete_message(storage_chat_id, int(message_id))
        return True
    except Exception as e:
        logger.warning(f'{BRAND_NAME} could not delete Telegram archive message {message_id}: {e}')
        return False


def archive_bot_to_telegram(bot_id, folder, bot_name, user_id, username=None, message_obj=None):
    """Zip the bot folder and upload it to the storage channel.

    The local folder is removed only after Telegram returns both a document file_id
    and message_id and the metadata is saved. If Telegram rejects the archive (for
    example because of its size limit), the local folder remains as a fallback."""
    if not _storage_enabled() or not os.path.isdir(folder):
        return False
    archive_path = None
    try:
        user_info = _resolve_username(user_id)
        display_name = username or user_info.get('username') or 'Unknown'
        caption = ("\U0001F4E6 " + BRAND_NAME + " Archive\n"
                   "\U0001F464 User: @" + html.escape(str(display_name), quote=False) + "\n"
                   "\U0001F194 Chat ID: " + str(user_id) + "\n"
                   "\U0001F916 Bot: " + html.escape(str(bot_name), quote=False) + "\n"
                   "\U0001F550 " + datetime.now().strftime('%Y-%m-%d %H:%M'))
        archive_base = os.path.join(TMP_DIR, f"arc_{bot_id}_{uuid.uuid4().hex[:8]}")
        archive_path = shutil.make_archive(archive_base, 'zip', folder)
        file_size = os.path.getsize(archive_path)
        if file_size > TELEGRAM_SEND_SAFE_BYTES:
            raise RuntimeError(f'Archive is {format_size(file_size)}; Telegram-safe send threshold is {TELEGRAM_SEND_SAFE_MB} MB')
        with open(archive_path, 'rb') as f:
            sent = bot.send_document(STORAGE_CHANNEL_ID, f, caption=caption, parse_mode='HTML')
        stored_file_id = getattr(getattr(sent, 'document', None), 'file_id', None)
        stored_message_id = getattr(sent, 'message_id', None)
        if not stored_file_id or stored_message_id is None:
            raise RuntimeError('Telegram did not return both document file_id and message_id')
        if not save_stored_record(bot_id, stored_file_id, file_size, display_name, user_id, stored_message_id):
            raise RuntimeError('Telegram upload succeeded but archive metadata could not be saved; local files were retained')
        logger.info(f"{BRAND_NAME} Archived {bot_name} ({bot_id}) to channel file {stored_file_id[:20]}... ({file_size} bytes)")
        if message_obj:
            try:
                bot.send_message(
                    message_obj.chat.id,
                    f"\U0001F4BE <b>{html.escape(str(bot_name), quote=False)}</b>'s files archived to the owner's "
                    f"storage channel (user @{html.escape(str(display_name), quote=False)}, ID {user_id}). "
                    f"Disk freed: {format_size(file_size)}.",
                    parse_mode='HTML')
            except Exception:
                pass
        shutil.rmtree(folder, ignore_errors=True)
        return True
    except Exception as e:
        logger.error(f"{BRAND_NAME} Archive error for {bot_id}: {e}")
        try:
            if message_obj:
                bot.send_message(
                    message_obj.chat.id,
                    f"\u26A0\uFE0F Could not archive <b>{html.escape(str(bot_name), quote=False)}</b>'s files to the storage "
                    f"channel ({html.escape(str(e)[:100], quote=False)}). The files stay on disk as a fallback.",
                    parse_mode='HTML')
        except Exception:
            pass
        return False
    finally:
        if archive_path and os.path.exists(archive_path):
            try:
                os.remove(archive_path)
            except OSError:
                pass


def restore_bot_from_telegram(bot_entry, message_obj=None):
    """Pull the bot's zip back from the storage channel and extract it to its folder.
    Returns True when the entry file exists afterwards."""
    bot_id = bot_entry['bot_id']
    folder = bot_entry['folder']
    entry_file = bot_entry.get('entry_file')
    script_path = os.path.join(folder, entry_file) if entry_file else None
    if os.path.isdir(folder) and script_path and os.path.exists(script_path):
        return True  # already on disk
    record = get_stored_record(bot_id)
    if not record:
        return False
    try:
        os.makedirs(folder, exist_ok=True)
        tmp_zip = os.path.join(TMP_DIR, f"res_{bot_id}.zip")
        try:
            file_id = record.get('file_id') or record.get('chat_id')
            if not file_id or file_id == str(record.get('message_id') or ''):
                raise RuntimeError('Archive record has no usable Telegram document file_id; legacy row cannot be restored safely')
            file_info = bot.get_file(file_id)
            with open(tmp_zip, 'wb') as f:
                f.write(bot.download_file(file_info.file_path))
            safe_extract_zip(tmp_zip, folder)
        finally:
            if os.path.exists(tmp_zip):
                os.remove(tmp_zip)
        flatten_single_wrapper_folder(folder)
        entry_file2, entry_type2 = find_entry_point(folder)
        bot_entry['entry_file'] = entry_file2
        bot_entry['entry_type'] = entry_type2
        logger.info(f"{BRAND_NAME} Restored {bot_id} from Telegram archive file {str(record.get('file_id') or record.get('chat_id'))[:20]}...")
        if message_obj:
            try:
                bot.send_message(
                    message_obj.chat.id,
                    f"\U0001F4E5 Restoring <b>{html.escape(str(bot_entry['bot_name']), quote=False)}</b>'s files from the storage "
                    f"channel... ({format_size(record['file_size'] or 0)})",
                    parse_mode='HTML')
            except Exception:
                pass
        return entry_file2 is not None and os.path.exists(os.path.join(folder, entry_file2))
    except Exception as e:
        logger.error(f"{BRAND_NAME} Restore error for {bot_id}: {e}")
        return False


def archive_running_bot(bot_id):
    """Stop+archive a running bot (used by admin stop / stop-all / exit cleanup)."""
    with state_lock:
        info = bot_scripts.get(bot_id)
    if not info:
        return False
    owner_id = info.get('user_id')
    username = _resolve_username(owner_id).get('username')
    _owner_id, entry = find_bot_anywhere(bot_id)
    if not entry:
        return False
    return archive_bot_to_telegram(
        bot_id, entry['folder'], entry['bot_name'], owner_id,
        username=username)


def _resolve_username(user_id):
    try:
        user = bot.get_chat(user_id)
        return {'username': user.username, 'first_name': user.first_name}
    except Exception:
        return {'username': None, 'first_name': None}




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

def bot_venv_path(folder):
    return os.path.join(folder, VENV_DIR_NAME)

def bot_python(folder):
    venv = bot_venv_path(folder)
    return os.path.join(venv, 'Scripts' if os.name == 'nt' else 'bin', 'python')

def ensure_bot_venv(folder):
    venv = bot_venv_path(folder)
    py = bot_python(folder)
    if not os.path.isfile(py):
        subprocess.run([sys.executable, '-m', 'venv', venv, '--clear'], check=True, timeout=60)
    return py

def get_bot_env(folder):
    env = get_sandboxed_env()
    env['PYTHONNOUSERSITE'] = '1'
    env['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'
    env['PIP_NO_INPUT'] = '1'
    env['BOT_HOST_ROOT'] = ''
    return env

def bot_resource_limits():
    # Best-effort limits for hosted code; this is not a substitute for containers/VMs.
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (300, 300))
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE, (2 * 1024 * 1024 * 1024, 2 * 1024 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    except (ValueError, OSError):
        pass


def auto_install_bulk_dependencies(folder, message_obj=None):
    """Runs once right after extraction: requirements.txt for python, package.json for node.
    Always reports back to chat when done — success, failure, or timeout — so it never looks stuck."""
    req_path = os.path.join(folder, 'requirements.txt')
    if os.path.exists(req_path):
        if message_obj:
            bot.send_message(message_obj.chat.id, "📦 <b>requirements.txt</b> found — installing packages, this can take a minute...", parse_mode='HTML')
        try:
            result = subprocess.run(
                [ensure_bot_venv(folder), '-m', 'pip', 'install', '-r', req_path, '--disable-pip-version-check', '--no-input', '--no-cache-dir'],
                capture_output=True, text=True, timeout=240, encoding='utf-8', errors='ignore',
                env=get_bot_env(folder)
            )
            if message_obj:
                if result.returncode == 0:
                    bot.send_message(message_obj.chat.id, "✅ <b>requirements.txt</b> installed successfully.", parse_mode='HTML')
                else:
                    tail = (result.stderr or result.stdout or "")[-500:]
                    bot.send_message(message_obj.chat.id,
                                     f"⚠️ <b>Some packages in requirements.txt failed to install</b> — will still try to run, "
                                     f"and auto-install anything still missing on crash.\n<code>{esc(tail)}</code>",
                                     parse_mode='HTML')
        except subprocess.TimeoutExpired:
            logger.error(f"{BRAND_NAME} requirements.txt install timed out for {folder}")
            if message_obj:
                bot.send_message(message_obj.chat.id, "⏱️ <b>requirements.txt install timed out</b> after 4 minutes — trying to run the bot anyway.", parse_mode='HTML')
        except Exception as e:
            logger.error(f"{BRAND_NAME} requirements.txt install error: {e}")
            if message_obj:
                bot.send_message(message_obj.chat.id, f"⚠️ requirements.txt install error: {esc(str(e)[:200])}", parse_mode='HTML')

    pkg_path = os.path.join(folder, 'package.json')
    if os.path.exists(pkg_path):
        if message_obj:
            bot.send_message(message_obj.chat.id, "📦 <b>package.json</b> found — running npm install, this can take a minute...", parse_mode='HTML')
        try:
            result = subprocess.run(
                ['npm', 'install', '--no-audit', '--no-fund'],
                cwd=folder, capture_output=True, text=True, timeout=240, encoding='utf-8', errors='ignore',
                env=get_sandboxed_env()
            )
            if message_obj:
                if result.returncode == 0:
                    bot.send_message(message_obj.chat.id, "✅ <b>npm install</b> completed successfully.", parse_mode='HTML')
                else:
                    tail = (result.stderr or result.stdout or "")[-500:]
                    bot.send_message(message_obj.chat.id,
                                     f"⚠️ <b>npm install had errors</b> — will still try to run.\n<code>{esc(tail)}</code>",
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
                bot.send_message(message_obj.chat.id, f"⚠️ npm install error: {esc(str(e)[:200])}", parse_mode='HTML')

def attempt_install_pip(module_name, message, folder):
    package_name = TELEGRAM_MODULES.get(module_name.lower(), module_name)
    if not package_name:
        return False
    try:
        msg = send_spinner_animation(message.chat.id, f"Installing {package_name}...", duration=2)
        command = [ensure_bot_venv(folder), '-m', 'pip', 'install', package_name, '--disable-pip-version-check', '--no-input', '--no-cache-dir']
        result = subprocess.run(command, capture_output=True, text=True, check=False,
                                encoding='utf-8', errors='ignore', timeout=150, env=get_bot_env(folder))
        if result.returncode == 0:
            try:
                bot.edit_message_text(
                    f"✅ <b>Package Installed!</b>\n📦 <code>{esc(package_name)}</code> installed successfully!",
                    message.chat.id, msg.message_id, parse_mode='HTML'
                )
            except Exception:
                bot.send_message(message.chat.id, f"✅ Package {esc(package_name)} installed!", parse_mode='HTML')
            return True
        else:
            error_msg = result.stderr[:500] if result.stderr else result.stdout[:500]
            try:
                bot.edit_message_text(
                    f"❌ <b>Installation Failed</b>\n<code>{esc(error_msg)}</code>",
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
                                cwd=folder, encoding='utf-8', errors='ignore', timeout=150, env=get_bot_env(folder))
        if result.returncode == 0:
            try:
                bot.edit_message_text(
                    f"✅ <b>NPM Package Installed!</b>\n📦 <code>{esc(module_name)}</code>",
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

def get_sandboxed_env():
    """Environment for hosted (untrusted) bot subprocesses. Strips this hosting bot's own
    secrets (BOT_TOKEN, OWNER_ID, admin config, DB credentials) so an uploaded bot can never
    accidentally (or deliberately) read them and start polling with THIS bot's own token —
    which is exactly what causes commands/admin access to appear to 'shift' between bots."""
    env = os.environ.copy()
    for key in ('BOT_TOKEN', 'OWNER_ID', 'ADMIN_ID', 'USERNAME', 'CHANNEL',
                'TURSO_URL', 'TURSO_TOKEN', 'PORT'):
        env.pop(key, None)
    return env

def ensure_bot_files_available(bot_entry, message_obj=None):
    """Make sure a bot's entry file is present, restoring it once when archived."""
    folder = bot_entry.get('folder') or ''
    entry_file = bot_entry.get('entry_file')
    if folder and os.path.isdir(folder) and entry_file and os.path.isfile(os.path.join(folder, entry_file)):
        return True
    if _storage_enabled() and restore_bot_from_telegram(bot_entry, message_obj):
        return bool(bot_entry.get('entry_file') and os.path.isfile(os.path.join(bot_entry['folder'], bot_entry['entry_file'])))
    return False


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
        set_bot_running_state(bot_id, False)
        bot.send_message(message_obj.chat.id, f"❌ Failed to run '{esc(bot_name)}' after {max_attempts} attempts — check logs for the root cause.")
        return

    if not entry_type or not entry_file:
        set_bot_running_state(bot_id, False)
        bot.send_message(message_obj.chat.id, f"⚠️ <b>{esc(bot_name)}</b> has no runnable .py or .js entry file — stored, but nothing to execute.", parse_mode='HTML')
        return

    if not ensure_bot_files_available(bot_entry, message_obj):
        set_bot_running_state(bot_id, False)
        bot.send_message(
            message_obj.chat.id,
            f"❌ <b>{esc(bot_name)}</b>'s files are unavailable locally and no usable archived copy was found. "
            f"The bot record was kept; re-upload the bot after checking storage-channel permissions.",
            parse_mode='HTML'
        )
        return
    folder = bot_entry['folder']
    entry_file = bot_entry.get('entry_file')
    script_path = os.path.join(folder, entry_file) if entry_file else ''
    if not entry_file or not os.path.isfile(script_path):
        set_bot_running_state(bot_id, False)
        bot.send_message(message_obj.chat.id, f"❌ Entry file '{esc(entry_file or 'unknown')}' not found inside <b>{esc(bot_name)}</b>'s folder.", parse_mode='HTML')
        return

    if entry_type == 'py':
        check_result = subprocess.run(
            [sys.executable, '-m', 'py_compile', script_path],
            capture_output=True, text=True, timeout=15, env=get_sandboxed_env()
        )
        if check_result.returncode != 0:
            set_bot_running_state(bot_id, False)
            bot.send_message(message_obj.chat.id,
                             f"⚠️ <b>Syntax Error in {esc(bot_name)}</b>\n<code>{esc(check_result.stderr[:600])}</code>",
                             parse_mode='HTML')
            return
    elif entry_type == 'js' and not NODE_AVAILABLE:
        set_bot_running_state(bot_id, False)
        bot.send_message(message_obj.chat.id,
                         f"❌ <b>{esc(bot_name)}</b> needs Node.js, but this host has none installed. "
                         f"On Railway, add a nixpacks.toml with the nodejs package (included in the deployment files).",
                         parse_mode='HTML')
        return

    terminal_msg = f"""
╔══════════════════════════════════════╗
║      🚀 <b>{BRAND_NAME}: STARTING BOT</b> 🚀  ║
╠══════════════════════════════════════╣
║ 🤖 Bot: <code>{esc(bot_name[:25])}</code>
║ 📄 Entry: <code>{esc(entry_file[:25])}</code>
║ 🔄 Attempt: {attempt}/{max_attempts}
╚══════════════════════════════════════╝
"""
    msg = send_animated_message(message_obj.chat.id, terminal_msg, "run", duration=2)
    log_file_path = os.path.join(LOGS_DIR, f"{bot_id}.log")
    log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')

    interpreter = [bot_python(folder), script_path] if entry_type == 'py' else ['node', script_path]

    try:
        process = subprocess.Popen(
            interpreter,
            cwd=folder,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='ignore',
            env=get_bot_env(folder),
            preexec_fn=bot_resource_limits if os.name == 'posix' else None
        )
    except FileNotFoundError:
        log_file.close()
        set_bot_running_state(bot_id, False)
        bot.send_message(message_obj.chat.id, "❌ Node.js runtime not found on this host!" if entry_type == 'js' else "❌ Python runtime not found!")
        return
    except Exception:
        log_file.close()
        set_bot_running_state(bot_id, False)
        raise

    with state_lock:
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
║ 🤖 <b>Bot:</b> <code>{esc(bot_name[:25])}</code>
║ 🆔 <b>PID:</b> {process.pid}
║ ⏱️ <b>Started:</b> {datetime.now().strftime('%H:%M:%S')}
╚══════════════════════════════════════╝
"""
        try:
            bot.edit_message_text(success_msg, message_obj.chat.id, msg.message_id, parse_mode='HTML')
        except Exception:
            bot.send_message(message_obj.chat.id, success_msg, parse_mode='HTML')
        set_bot_running_state(bot_id, True)
        log_action(owner_id, "BOT_START", f"Started {esc(bot_name)} (PID: {process.pid})")
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
            if attempt_install_pip(module_name, message_obj, folder):
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
║ 🤖 <b>Bot:</b> <code>{esc(bot_name[:25])}</code>
║ ❗ <b>Exit Code:</b> {process.returncode}
╠══════════════════════════════════════╣
<code>{esc(error_output[:500])}</code>
╚══════════════════════════════════════╝
"""
    try:
        bot.edit_message_text(error_msg, message_obj.chat.id, msg.message_id, parse_mode='HTML')
    except Exception:
        bot.send_message(message_obj.chat.id, error_msg, parse_mode='HTML')
    set_bot_running_state(bot_id, False)
    cleanup_script(bot_id)

def run_bot_instance_safe(bot_entry, message_obj, attempt=1, admin_id=None):
    """Thin safety wrapper — any uncaught exception here gets reported to chat instead of
    silently killing the background thread (which is what made things look 'stuck')."""
    try:
        run_bot_instance(bot_entry, message_obj, attempt, admin_id)
    except Exception as e:
        set_bot_running_state(bot_entry.get('bot_id'), False)
        logger.error(f"{BRAND_NAME} run_bot_instance crashed for {bot_entry.get('bot_name')}: {e}")
        try:
            bot.send_message(
                message_obj.chat.id,
                f"❌ <b>Unexpected error while starting</b> <code>{bot_entry.get('bot_name', 'bot')}</code>:\n"
                f"<code>{esc(str(e)[:300])}</code>",
                parse_mode='HTML'
            )
        except Exception:
            pass

# ============================================
# SELF-HEALING: STARTUP RESUME + CRASH WATCHDOG
# ============================================

class _SystemMessage:
    """Minimal message-like stand-in so startup/watchdog code (which has no real
    Telegram message to reply to) can reuse the exact same run flow as a normal
    user-triggered run, and still notify the right chat."""
    class _Chat:
        def __init__(self, chat_id):
            self.id = chat_id
    def __init__(self, chat_id):
        self.chat = self._Chat(chat_id)

def resume_persisted_bots():
    logger.info(f'{BRAND_NAME} Startup resume: checking persisted running state...')
    candidates = []
    with state_lock:
        bot_groups = [(uid, list(bots)) for uid, bots in user_bots.items()]
    for uid, bots in bot_groups:
        for entry in bots:
            if entry.get('was_running'):
                candidates.append((uid, entry))

    def resume_one(uid, entry):
        if not ensure_bot_files_available(entry, _SystemMessage(uid)):
            set_bot_running_state(entry['bot_id'], False)
            logger.error(f'{BRAND_NAME} Startup resume skipped unavailable bot {entry["bot_id"]}')
            return
        run_bot_instance_safe(entry, _SystemMessage(uid), attempt=1, admin_id=None)

    for uid, entry in candidates:
        threading.Thread(target=resume_one, args=(uid, entry), daemon=True).start()
    logger.info(f'{BRAND_NAME} Startup resume: queued {len(candidates)} bot(s)')
    if candidates:
        try:
            bot.send_message(OWNER_ID,
                             f'🔄 <b>{BRAND_NAME} restarted.</b> Queued {len(candidates)} previously-running bot(s).\n'
                             f'💾 Database backend: <code>{esc(DB_BACKEND)}</code>', parse_mode='HTML')
        except Exception:
            pass

def bot_watchdog_loop():
    """Background loop: every few minutes, checks every bot that's supposed to be
    running. If one crashed on its own (not manually stopped by a user), it gets
    auto-restarted — up to a limit per hour, so a genuinely broken bot doesn't
    crash-loop forever and eat resources. This is the 'never goes offline
    unexpectedly' mechanism, within honest limits."""
    while True:
        time.sleep(WATCHDOG_INTERVAL_SECONDS)
        try:
            with state_lock:
                bot_ids = list(bot_scripts.keys())
            for bot_id in bot_ids:
                if is_bot_running_check(bot_id):
                    continue
                with state_lock:
                    info = bot_scripts.get(bot_id)
                if not info:
                    continue
                owner_id = info.get('user_id')
                bot_name = info.get('bot_name', bot_id)
                cleanup_script(bot_id)

                now = time.time()
                with state_lock:
                    crashes = bot_crash_counts.setdefault(bot_id, [])
                    crashes[:] = [t for t in crashes if now - t < 3600]

                if len(crashes) >= MAX_AUTO_RESTARTS_PER_HOUR:
                    logger.error(f"{BRAND_NAME} Watchdog: {bot_name} crashed too many times — giving up auto-restart for now")
                    try:
                        bot.send_message(
                            owner_id,
                            f"❌ <b>{esc(bot_name)}</b> has crashed {len(crashes)} times in the last hour — "
                            f"auto-restart is pausing to avoid a crash loop. Check 📋 Logs for the real error, "
                            f"fix it, then run it manually.",
                            parse_mode='HTML'
                        )
                    except Exception:
                        pass
                    continue

                with state_lock:
                    crashes.append(now)
                with state_lock:
                    owner_bots = list(user_bots.get(owner_id, []))
                b = next((x for x in owner_bots if x['bot_id'] == bot_id), None)
                if not b or not os.path.exists(b['folder']):
                    if b and ensure_bot_files_available(b):
                        pass  # restored, fall through to restart
                    else:
                        continue

                logger.info(f"{BRAND_NAME} Watchdog: restarting crashed bot {bot_name} (attempt {len(crashes)}/{MAX_AUTO_RESTARTS_PER_HOUR} this hour)")
                try:
                    bot.send_message(owner_id, f"🔄 <b>{esc(bot_name)}</b> crashed — auto-restarting...", parse_mode='HTML')
                except Exception:
                    pass
                threading.Thread(target=run_bot_instance_safe, args=(b, _SystemMessage(owner_id))).start()
        except Exception as e:
            logger.error(f"{BRAND_NAME} Watchdog loop error: {e}")

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

def safe_command(func):
    """Guarantees a visible reply even if a handler throws unexpectedly.
    Without this, pyTelegramBotAPI just swallows the exception and the user
    sees literally nothing — which is exactly the 'no response' bug class."""
    @wraps(func)
    def wrapper(message, *args, **kwargs):
        try:
            return func(message, *args, **kwargs)
        except Exception as e:
            logger.error(f"{BRAND_NAME} Handler '{func.__name__}' crashed: {e}")
            try:
                bot.reply_to(message, f"❌ <b>Something went wrong running this command.</b>\n<code>{esc(str(e)[:300])}</code>", parse_mode='HTML')
            except Exception:
                pass
    return wrapper

@bot.message_handler(commands=['start'])
@safe_command
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
👋 <b>Welcome, {esc(message.from_user.first_name)}</b>!

<b>📌 Your Status:</b>
🤖 <b>Bots:</b> {current_bots}/{max_bots if max_bots != float('inf') else '∞'}
⭐ <b>Points:</b> {points_data['points']}
👥 <b>Referrals:</b> {points_data['total_referrals']}
💳 <b>Status:</b> {'👑 Owner' if user_id == OWNER_ID else '⭐ Admin' if user_id in admin_ids else '🌟 Premium' if get_active_subscription(user_id) else '👤 Free'}

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
@safe_command
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
@safe_command
def stats_command(message):
    msg = send_spinner_animation(message.chat.id, f"Gathering {BRAND_NAME} stats...", duration=2)
    stats_text = create_system_stats_message()
    try:
        bot.edit_message_text(stats_text, message.chat.id, msg.message_id, parse_mode='HTML')
    except Exception:
        bot.send_message(message.chat.id, stats_text, parse_mode='HTML')

@bot.message_handler(commands=['speed'])
@safe_command
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
@safe_command
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


@bot.message_handler(commands=['level'])
@safe_command
def level_command(message):
    user_id = message.from_user.id
    tier, lvl = get_user_level(user_id)
    points = get_user_points(user_id)['points']
    quota = get_user_quota_mb(user_id)
    used = get_user_folder_size_mb(user_id)
    with state_lock:
        running_ids = [k for k, info in bot_scripts.items() if info.get('user_id') == user_id]
    running = len([k for k in running_ids if is_bot_running_check(k)])
    badge = {'ADMIN': '\U0001F451', 'PRO': '\u2B50', 'FREE': '\U0001F193'}[tier]
    quota_line = f"{used:.0f}/{quota} MB" if quota else "Unlimited"
    next_lvl_pts = (lvl + 1) * 10
    W = '\u2550' * 38
    lines = [
        '\u2554' + W + '\u2557',
        '\u2551       \U0001F396\uFE0F <b>' + BRAND_NAME + ': YOUR LEVEL</b> \U0001F396\uFE0F      \u2551',
        '\u2560' + W + '\u2563',
        '\u2551',
        '\u2551  ' + badge + ' <b>Tier:</b> ' + str(tier) + ' \u00B7 <b>Level:</b> ' + str(lvl),
        '\u2551  \u2B50 <b>Points:</b> ' + str(points),
        '\u2551  \U0001F4BE <b>Storage quota:</b> ' + str(quota_line),
        '\u2551  \U0001F501 <b>Currently running:</b> ' + str(running),
        '\u2551  \U0001F4E6 <b>Hosted bots:</b> ' + str(get_current_bot_count(user_id)) + '/' + str(get_user_max_bots(user_id)),
        '\u2551',
        '\u2551  \U0001F4C8 Next level at ' + str(next_lvl_pts) + ' points',
        '\u2551     (+' + str(PER_LEVEL_BONUS_BOTS) + ' slots, +' + str(PER_LEVEL_QUOTA_MB) + ' MB quota; free tier capped at 10 slots)',
        '\u2551',
        '\u255A' + W + '\u255D',
    ]
    text = '\n' + '\n'.join(lines) + '\n'
    bot.send_message(message.chat.id, text, parse_mode='HTML')
@bot.message_handler(commands=['points'])
@safe_command
def points_command(message):
    show_my_points(message)

@bot.message_handler(commands=['referral'])
@safe_command
def referral_command(message):
    show_referral_system(message)

@bot.message_handler(commands=['referrals'])
@safe_command
def referrals_command(message):
    show_referral_history_for_command(message, message.from_user.id)

@bot.message_handler(commands=['lock'])
@safe_command
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
║  By: {esc(message.from_user.first_name)}
║  Time: {datetime.now().strftime('%H:%M:%S')}
║
╚══════════════════════════════════════╝
"""
    send_animated_message(message.chat.id, lock_text, "terminal", duration=1)

@bot.message_handler(commands=['broadcast'])
@safe_command
def broadcast_command(message):
    user_id = message.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.reply_to(message, "❌ You don't have permission!")
        return
    msg = bot.reply_to(message, "📢 Send the message you want to broadcast:")
    bot.register_next_step_handler(msg, process_broadcast)

@safe_command
def process_broadcast(message):
    broadcast_text = message.text
    if not broadcast_text:
        bot.reply_to(message, "❌ Please send a text message!")
        return
    if broadcast_text.startswith('/'):
        bot.reply_to(message, "⚠️ That looked like a command, not a broadcast message — broadcast cancelled so it doesn't get sent to everyone. Run /broadcast again if you actually want to broadcast something.")
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
{esc(broadcast_text)}
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

@bot.message_handler(commands=['subscriptions', 'subs'])
@safe_command
def subscriptions_view_command(message):
    show_subscriptions(message)

@bot.message_handler(commands=['subscribe'])
@safe_command
def subscribe_command(message):
    user_id = message.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.reply_to(message, f"❌ You don't have permission! (Your ID: <code>{user_id}</code> is not recognized as owner/admin — check OWNER_ID/ADMIN_ID env vars on Railway)", parse_mode='HTML')
        return
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "Usage: <code>/subscribe &lt;user_id&gt; &lt;days&gt;</code>", parse_mode='HTML')
        return
    try:
        target_user = int(parts[1])
        days = int(parts[2])
    except ValueError:
        bot.reply_to(message, "❌ Invalid user ID or days!")
        return
    if days < 1:
        bot.reply_to(message, "❌ Subscription days must be at least 1.")
        return
    expiry = datetime.now() + timedelta(days=days)
    if not save_subscription(target_user, expiry):
        bot.reply_to(message, "❌ Subscription could not be saved to the database. No access was activated.")
        return
    with state_lock:
        user_subscriptions[target_user] = {'expiry': expiry}
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
        p_msg = '\u2b50 <b>PREMIUM ACTIVATED</b> \u2b50\n\n'
        p_msg += 'Congratulations! You now have <b>PRO Tier</b> access.\n\n'
        p_msg += '\u2551  \u2b50 <b>Tier:</b> PRO\n'
        p_msg += '\U0001f4be <b>Storage:</b> 2GB + Bonus\n'
        p_msg += '\U0001f916 <b>Slots:</b> 15 + Bonus\n'
        p_msg += '\u23f0 <b>Expires:</b> ' + expiry.strftime('%Y-%m-%d %H:%M') + '\n\n'
        p_msg += 'Use /level to see your new limits!'
        bot.send_message(target_user, p_msg, parse_mode='HTML')
    except Exception:
        pass

@bot.message_handler(commands=['unsubscribe'])
@safe_command
def unsubscribe_command(message):
    user_id = message.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.reply_to(message, "❌ Admin only!")
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "Usage: <code>/unsubscribe &lt;user_id&gt;</code>", parse_mode='HTML')
        return
    try:
        target_user = int(parts[1])
    except ValueError:
        bot.reply_to(message, "❌ Invalid user ID!")
        return
    
    with state_lock:
        user_subscriptions.pop(target_user, None)
    delete_ok = True
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('DELETE FROM subscriptions WHERE user_id = ?', (target_user,))
        conn.commit()
        conn.close()
    except Exception as e:
        delete_ok = False
        logger.error(f"Error deleting sub: {e}")
    if not delete_ok:
        bot.reply_to(message, f"⚠️ Subscription was removed from memory, but database cancellation failed for <code>{target_user}</code>. Retry after checking the database connection.", parse_mode='HTML')
        return
    bot.reply_to(message, f"✅ Subscription cancelled for user <code>{target_user}</code> (if one existed).", parse_mode='HTML')
    try:
        bot.send_message(target_user, "⚠️ Your Premium subscription has been cancelled by an administrator.")
    except Exception:
        pass


@bot.message_handler(commands=['addpoints'])
@safe_command
def add_points_command(message):
    user_id = message.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.reply_to(message, "❌ Admin only!")
        return
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "Usage: /addpoints &lt;user_id&gt; &lt;points&gt;")
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
@safe_command
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
@safe_command
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
@safe_command
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

@bot.message_handler(commands=['diagnostics', 'diag'])
@safe_command
def diagnostics_command(message):
    user_id = message.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.reply_to(message, f"❌ Admin only! (Your ID: <code>{user_id}</code>)", parse_mode='HTML')
        return

    tg_storage = 'ON (Telegram channel) ✅' if _storage_enabled() else 'OFF (files stay on disk)'
    channel_line = ('\n📬 <b>Archive channel:</b> <code>' + esc(STORAGE_CHANNEL_ID) + '</code>') if _storage_enabled() else ''
    if DB_BACKEND == 'turso':
        storage = 'Turso (connection succeeded ✅)'
    elif DB_BACKEND == 'sqlite-fallback':
        storage = 'Local SQLite fallback (⚠️ Turso unavailable; not persistent on Railway without a Volume)'
    else:
        storage = 'Local SQLite (⚠️ not persistent on Railway without a Volume)'
    db_error_line = f'\n⚠️ <b>Database error:</b> <code>{esc(DB_LAST_ERROR)}</code>' if DB_LAST_ERROR else ''
    disk_ok = os.access(UPLOAD_BOTS_DIR, os.W_OK)
    total_bots = sum(len(b) for b in user_bots.values())
    running = len([k for k in bot_scripts if is_bot_running_check(k)])
    crash_summary = ", ".join(f"{bid}: {len(times)}" for bid, times in bot_crash_counts.items() if times) or "none"

    text = f"""
🔧 <b>{BRAND_NAME} Diagnostics</b>

💾 <b>Storage backend:</b> {storage}{db_error_line}
📨 <b>Telegram file archiving:</b> {tg_storage}{channel_line}
📁 <b>Upload dir writable:</b> {'✅' if disk_ok else '❌'}
🤖 <b>Total hosted bots (tracked):</b> {total_bots}
🟢 <b>Currently running:</b> {running}
⏱️ <b>Uptime since last restart:</b> {get_uptime()}
🔁 <b>Watchdog interval:</b> {WATCHDOG_INTERVAL_SECONDS}s, max {MAX_AUTO_RESTARTS_PER_HOUR} auto-restarts/hour per bot
⚠️ <b>Bots with recent crashes:</b> {crash_summary}

<b>What this means:</b> if the storage backend above says local SQLite/disk and you're on Railway without a Volume, everything gets wiped on every container restart — that's the #1 cause of bots disappearing after a few days. Attach a Railway Volume (mounted at this app's working directory) or configure TURSO_URL/TURSO_TOKEN to fix this permanently.
"""
    bot.send_message(message.chat.id, text, parse_mode='HTML')

# ============================================
# TEXT MESSAGE HANDLERS
# ============================================

def _normalize_button_text(text):
    """Strips emoji/punctuation and lowercases, so 'Subscriptions' matches '💳 Subscriptions'
    even if a user types the plain word instead of tapping the actual keyboard button."""
    return re.sub(r'[^a-z0-9]+', '', text.lower())

@bot.message_handler(content_types=['text'])
@safe_command
def handle_text(message):
    user_id = message.from_user.id
    text = message.text
    active_users.add(user_id)

    if bot_locked and user_id not in admin_ids and user_id != OWNER_ID:
        bot.reply_to(message, "🔒 Bot is locked!")
        return

    norm = _normalize_button_text(text)

    if text == "📢 Updates Channel" or norm == "updateschannel":
        bot.send_message(message.chat.id, f"📢 Join our {BRAND_NAME} updates:\n{UPDATE_CHANNEL}")
    elif text == "📤 Upload File" or norm == "uploadfile":
        handle_upload_request(message)
    elif text == "📂 Check Files" or norm == "checkfiles":
        show_user_files(message)
    elif text in ("🟢 Running Bots", "🟢 My Running Bots") or norm in ("runningbots", "myrunningbots"):
        running_command(message)
    elif text == "⚡ Bot Speed" or norm == "botspeed":
        speed_command(message)
    elif text in ("📊 Statistics", "📊 My Stats") or norm in ("statistics", "mystats"):
        stats_command(message)
    elif text == "⭐ My Points" or norm == "mypoints":
        show_my_points(message)
    elif text == "🎯 Referral System" or norm == "referralsystem":
        show_referral_system(message)
    elif text == "💳 Subscriptions" or norm in ("subscriptions", "subscription", "subs"):
        show_subscriptions(message)
    elif text == "📢 Broadcast" or norm == "broadcast":
        broadcast_command(message)
    elif text == "🔒 Lock Bot" or norm == "lockbot":
        lock_command(message)
    elif text == "👑 Admin Panel" or norm == "adminpanel":
        show_admin_panel(message)
    elif text == "🖼️ Change Banner" or norm == "changebanner":
        set_banner_command(message)
    elif text == "📞 Contact Owner" or norm == "contactowner":
        bot.send_message(message.chat.id, f"📞 Contact: {YOUR_USERNAME}")
    elif text.startswith('/'):
        bot.reply_to(message, "❓ Unknown command. Send /help to see everything I understand.")
    else:
        bot.reply_to(message, "❓ I didn't recognize that. Use the buttons below, or send /help.")

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
        bot.reply_to(message, f"❌ Admin only! (Your ID: <code>{user_id}</code>)", parse_mode='HTML')
        return
    with state_lock:
        subscription_items = list(user_subscriptions.items())
    active_subs = {uid: data for uid, data in subscription_items
                   if data.get('expiry') and data['expiry'] > datetime.now()}
    text = f"""
╔══════════════════════════════════════╗
║     💳 <b>{BRAND_NAME}: SUBSCRIPTIONS</b> 💳    ║
╠══════════════════════════════════════╣
║
║  Active: {len(active_subs)}
║  Total Ever: {len(active_subs)} active records loaded
║
"""
    for uid, data in list(active_subs.items())[:10]:
        remaining = data['expiry'] - datetime.now()
        text += f"║  👤 {uid}: {remaining.days}d left\n"
    text += """║
╠══════════════════════════════════════╣
║  Add sub: /subscribe &lt;id&gt; &lt;days&gt;
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
║  • Active Subs: {len([u for u, d in list(user_subscriptions.items()) if d.get('expiry') and d['expiry'] > datetime.now()])}
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
        text += f"║  {i}. 👤 {esc(username)} ({referred_id})\n"
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
@safe_command
def handle_document(message):
    user_id = message.from_user.id

    can_upload, status = can_user_upload(user_id)
    if not can_upload:
        bot.reply_to(message, f"❌ Can't upload! {status}")
        return

    raw_file_name = message.document.file_name or f"file_{uuid.uuid4().hex[:8]}"
    file_name = os.path.basename(str(raw_file_name).replace('\\', '/'))
    if not file_name or file_name in ('.', '..'):
        bot.reply_to(message, '❌ Invalid file name.')
        return
    file_size = message.document.file_size or 0
    file_ext = file_name.rsplit('.', 1)[-1].lower() if '.' in file_name else 'bin'
    if MAX_UPLOAD_MB > 0 and file_size > MAX_UPLOAD_MB * 1024 * 1024:
        bot.reply_to(message, f"❌ File too large! Max {MAX_UPLOAD_MB} MB per file. (This file: {format_size(file_size)})")
        return
    can_space, space_msg = can_user_upload_space(user_id)
    if not can_space:
        bot.reply_to(message, f"❌ {space_msg}")
        return

    upload_text = f"""
╔══════════════════════════════════════╗
║      📤 <b>{BRAND_NAME}: UPLOADING</b> 📤     ║
╠══════════════════════════════════════╣
║
║  📄 File: <code>{esc(file_name[:25])}</code>
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
                upload_text + f"║  ❌ Download failed: {esc(str(e)[:40])}\n╚══════════════════════════════════════╝",
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
        if file_size > MAX_SINGLE_FILE_BYTES:
            raise ValueError(f'File exceeds the safety limit of {MAX_SINGLE_FILE_BYTES // (1024 * 1024)} MB')
        if file_ext == 'zip':
            tmp_zip = os.path.join(TMP_DIR, f"{bot_id}.zip")
            with open(tmp_zip, 'wb') as f:
                f.write(downloaded_file)

            try:
                safe_extract_zip(tmp_zip, bot_folder)
            except (zipfile.BadZipFile, ValueError) as zip_error:
                shutil.rmtree(bot_folder, ignore_errors=True)
                if os.path.exists(tmp_zip):
                    os.remove(tmp_zip)
                error_text = 'Invalid or corrupted ZIP!' if isinstance(zip_error, zipfile.BadZipFile) else str(zip_error)[:160]
                bot.edit_message_text(
                    upload_text + f"║  ❌ {esc(error_text)}\n╚══════════════════════════════════════╝",
                    message.chat.id, progress_msg.message_id, parse_mode='HTML'
                )
                return
            finally:
                if os.path.exists(tmp_zip):
                    os.remove(tmp_zip)

            flatten_single_wrapper_folder(bot_folder)
            if contains_host_secret(bot_folder):
                raise ValueError('Upload contains this host bot token; rejected to prevent bot hijacking')
            bot_name = file_name.rsplit('.', 1)[0]
        else:
            # ANY extension is accepted and stored — .py/.js run, everything else is support data
            target_path = os.path.join(bot_folder, file_name)
            with open(target_path, 'wb') as f:
                f.write(downloaded_file)
            if contains_host_secret(bot_folder):
                raise ValueError('Upload contains this host bot token; rejected to prevent bot hijacking')
            bot_name = file_name

        file_count = count_files(bot_folder)
        entry_file, entry_type = find_entry_point(bot_folder)

        with state_lock:
            user_bots.setdefault(user_id, []).append({
                'bot_id': bot_id,
            'bot_name': bot_name,
            'folder': bot_folder,
            'folder_name': folder_name,
            'entry_file': entry_file,
            'entry_type': entry_type,
            'file_count': file_count,
                'upload_time': datetime.now().isoformat(),
                'user_id': user_id,
                'was_running': False
            })
        metadata_saved = save_hosted_bot_db(bot_id, user_id, bot_name, folder_name, entry_file, entry_type, file_count)
        if not metadata_saved:
            logger.error(f"{BRAND_NAME} Bot {bot_id} is running, but its metadata was not persisted; verify the database backend before redeploying.")

        entry_display = entry_file if entry_file else "None found"
        success_text = upload_text + f"""║  ✅ Deployed!
║  🎖️ Your level: {get_user_level(user_id)[0]} Lv.{get_user_level(user_id)[1]}
║  📁 Files: {file_count}
║  🎯 Entry: <code>{esc(entry_display[:25] if entry_file else entry_display)}</code>
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
                upload_text + f"║  ❌ Error: {esc(str(e)[:40])}\n╚══════════════════════════════════════╝",
                message.chat.id, progress_msg.message_id, parse_mode='HTML'
            )
        except Exception:
            bot.reply_to(message, f"❌ Upload failed: {esc(str(e)[:100])}")
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
                f"<code>{esc(str(e)[:300])}</code>\n\nThe files are still saved — check 📂 Check Files to retry running it.",
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

        elif data.startswith("zip_folder_"):
            show_admin_zip_browser(call, data[11:])
        elif data.startswith("zip_back_"):
            show_admin_zip_browser(call, data[9:])
        elif data.startswith("zip_file_"):
            download_admin_zip_file(call, data[9:])
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
        bot.answer_callback_query(call.id, f"❌ Error: {esc(str(e)[:50])}")

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
        text += f"{i}. 👤 {esc(username)} ({referred_id}) — {timestamp[:16]}\n"

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
    if not ensure_bot_files_available(b, call.message):
        bot.answer_callback_query(call.id, "❌ Files unavailable; restore failed.")
        return
    bot.answer_callback_query(call.id, "🚀 Starting...")
    threading.Thread(target=run_bot_instance_safe, args=(b, call.message)).start()

def stop_user_bot(call, bot_id):
    user_id = call.from_user.id
    with state_lock:
        script_info = bot_scripts.get(bot_id)
    if not script_info:
        bot.answer_callback_query(call.id, "❌ Not running!")
        return
    bot.answer_callback_query(call.id, "🛑 Stopping...")
    b = find_bot_by_id(user_id, bot_id)
    if script_info:
        kill_process_tree(script_info)
        set_bot_running_state(bot_id, False)
        cleanup_script(bot_id)
        time.sleep(1)
        bot_name = b['bot_name'] if b else bot_id
        if b:
            archive_bot_to_telegram(bot_id, b['folder'], b['bot_name'], user_id)
        success_text = f"✅ <b>Stopped!</b>\n🤖 <code>{esc(bot_name[:25])}</code>"
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("▶️ Run Again", callback_data=f"run_{bot_id}"),
            types.InlineKeyboardButton("🔙 Back", callback_data="back_to_files")
        )
        try:
            bot.edit_message_text(success_text, call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=markup)
        except Exception:
            bot.send_message(call.message.chat.id, success_text, parse_mode='HTML', reply_markup=markup)
        log_action(user_id, "BOT_STOP", f"Stopped {esc(bot_name)}")

def restart_user_bot(call, bot_id):
    with state_lock:
        script_info = bot_scripts.get(bot_id)
    if script_info:
        kill_process_tree(script_info)
        set_bot_running_state(bot_id, False)
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
        if _storage_enabled():
            record = get_stored_record(bot_id)
            delete_stored_telegram_message(record)
            remove_stored_record(bot_id)
        shutil.rmtree(b['folder'], ignore_errors=True)
        with state_lock:
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
        bot.answer_callback_query(call.id, f"❌ Error: {esc(str(e)[:30])}")

def download_user_bot(call, bot_id):
    user_id = call.from_user.id
    b = find_bot_by_id(user_id, bot_id)
    if not b:
        bot.answer_callback_query(call.id, "❌ Bot not found!")
        return
    bot.answer_callback_query(call.id, "📥 Preparing...")
    _send_bot_as_file(call.message.chat.id, b)

def _send_bot_as_file(chat_id, b):
    """Send a safe single-file download or a small whole-bot archive.

    Telegram cannot accept arbitrarily large documents. The function preflights the
    exact byte size and points the operator to the admin folder browser instead of
    making a request that will predictably fail with HTTP 413."""
    archive_path = None
    try:
        if not ensure_bot_files_available(b):
            bot.send_message(chat_id, '❌ Bot files are unavailable locally and could not be restored from the storage channel.')
            return
        files_in_folder = []
        for root, dirs, files in os.walk(b['folder']):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith('.') ]
            for name in files:
                full_path = os.path.join(root, name)
                if os.path.isfile(full_path):
                    files_in_folder.append(full_path)

        if not files_in_folder:
            bot.send_message(chat_id, '❌ No files found in this bot folder.')
            return
        if len(files_in_folder) == 1:
            single_path = files_in_folder[0]
            file_size = os.path.getsize(single_path)
            if file_size > TELEGRAM_SEND_SAFE_BYTES:
                bot.send_message(chat_id,
                                 f'⚠️ This file is {format_size(file_size)} and exceeds the Telegram-safe send threshold of {TELEGRAM_SEND_SAFE_MB} MB. '
                                 'Use the admin file browser or object storage/direct download for large files.')
                return
            with open(single_path, 'rb') as f:
                bot.send_document(chat_id, f, caption=f"📄 {b['bot_name']}")
        else:
            archive_base = os.path.join(TMP_DIR, f"dl_{b['bot_id']}_{uuid.uuid4().hex[:8]}")
            archive_path = shutil.make_archive(archive_base, 'zip', b['folder'])
            archive_size = os.path.getsize(archive_path)
            if archive_size > TELEGRAM_SEND_SAFE_BYTES:
                bot.send_message(chat_id,
                                 f'⚠️ The complete ZIP is {format_size(archive_size)} and exceeds the Telegram-safe send threshold of {TELEGRAM_SEND_SAFE_MB} MB. '
                                 'Open Admin Panel → All User Bots → Download to retrieve individual files, or use object storage/direct download.')
                return
            with open(archive_path, 'rb') as f:
                bot.send_document(chat_id, f, caption=f"📦 {b['bot_name']}.zip ({len(files_in_folder)} files)")
    except Exception as e:
        logger.error(f'{BRAND_NAME} user download error: {e}')
        bot.send_message(chat_id, f"❌ Download failed: {esc(str(e)[:160])}")
    finally:
        if archive_path and os.path.exists(archive_path):
            try:
                os.remove(archive_path)
            except OSError:
                pass

def show_bot_logs(call, bot_id):
    log_path = os.path.join(LOGS_DIR, f"{bot_id}.log")
    if not os.path.exists(log_path):
        bot.answer_callback_query(call.id, "📋 No logs yet")
        return
    try:
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            logs = f.read()[-2000:] or "No output yet..."
        log_text = f"📋 <b>{BRAND_NAME}: LOGS</b>\n\n<code>{esc(logs[:1800])}</code>"
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
        bot.answer_callback_query(call.id, f"❌ Error: {esc(str(e)[:30])}")

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
    text = f"📂 <b>{esc(username)}'s Bots</b> (ID: {target_user})\n\nSelect one:"
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

def _is_admin_user(user_id):
    return user_id == OWNER_ID or user_id in admin_ids


def _make_zip_browser_token(bot_id, relative_path, action):
    token = uuid.uuid4().hex[:18]
    zip_browser_tokens[token] = {
        'bot_id': str(bot_id),
        'path': relative_path or '',
        'action': action,
        'expires': time.time() + 1800,
    }
    now = time.time()
    for key, value in list(zip_browser_tokens.items()):
        if value.get('expires', 0) < now:
            zip_browser_tokens.pop(key, None)
    return token


def _resolve_zip_browser(call, token):
    if not _is_admin_user(call.from_user.id):
        bot.answer_callback_query(call.id, '❌ Admin only!')
        return None
    item = zip_browser_tokens.get(token)
    if not item or item.get('expires', 0) < time.time():
        zip_browser_tokens.pop(token, None)
        bot.answer_callback_query(call.id, '⚠️ Menu expired. Open the ZIP again.')
        return None
    owner_id, entry = find_bot_anywhere(item['bot_id'])
    if not entry:
        bot.answer_callback_query(call.id, '❌ Bot not found!')
        return None
    folder = os.path.abspath(entry.get('folder', ''))
    if not folder:
        bot.answer_callback_query(call.id, '❌ Bot folder unavailable!')
        return None
    if not ensure_bot_files_available(entry):
        bot.answer_callback_query(call.id, '❌ Files are not available locally or in storage.')
        return None
    relative = item.get('path', '') or ''
    relative = os.path.normpath(relative) if relative else ''
    if relative == '.':
        relative = ''
    target = os.path.abspath(os.path.join(folder, relative))
    try:
        if os.path.commonpath([folder, target]) != folder:
            bot.answer_callback_query(call.id, '❌ Invalid path!')
            return None
    except ValueError:
        bot.answer_callback_query(call.id, '❌ Invalid path!')
        return None
    return owner_id, entry, folder, relative, target


def _zip_file_icon(name):
    ext = os.path.splitext(name)[1].lower()
    if ext == '.py':
        return '🐍'
    if ext in ('.js', '.mjs', '.cjs', '.ts'):
        return '🟨'
    if ext in ('.json', '.yaml', '.yml', '.toml', '.ini', '.env'):
        return '⚙️'
    if ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'):
        return '🖼️'
    if ext in ('.txt', '.md', '.log'):
        return '📄'
    return '📃'


def show_admin_zip_browser(call, token_or_bot_id):
    if not _is_admin_user(call.from_user.id):
        bot.answer_callback_query(call.id, '❌ Admin only!')
        return
    if token_or_bot_id in zip_browser_tokens:
        item = zip_browser_tokens[token_or_bot_id]
        bot_id = item['bot_id']
        relative = item.get('path', '') or ''
    else:
        bot_id = token_or_bot_id
        relative = ''
    owner_id, entry = find_bot_anywhere(bot_id)
    if not entry:
        bot.answer_callback_query(call.id, '❌ Bot not found!')
        return
    folder = os.path.abspath(entry.get('folder', ''))
    if not ensure_bot_files_available(entry):
        bot.answer_callback_query(call.id, '❌ Bot files are not available locally or in storage.')
        return
    relative = os.path.normpath(relative) if relative else ''
    if relative == '.':
        relative = ''
    current = os.path.abspath(os.path.join(folder, relative))
    try:
        if os.path.commonpath([folder, current]) != folder or not os.path.isdir(current):
            bot.answer_callback_query(call.id, '❌ Folder not found!')
            return
    except ValueError:
        bot.answer_callback_query(call.id, '❌ Invalid folder!')
        return

    directories = []
    files = []
    try:
        for name in sorted(os.listdir(current), key=lambda value: value.lower()):
            if name.startswith('.') or name in IGNORED_DIRS:
                continue
            full = os.path.join(current, name)
            rel = os.path.relpath(full, folder)
            if os.path.isdir(full):
                directories.append((name, rel))
            elif os.path.isfile(full):
                files.append((name, rel))
    except OSError as exc:
        bot.answer_callback_query(call.id, f'❌ Cannot read folder: {str(exc)[:40]}')
        return

    shown_path = '/' + relative.replace(os.sep, '/') if relative else '/'
    text = (
        f"📦 <b>{esc(entry['bot_name'][:40])}</b>\n"
        f"👤 Owner ID: <code>{owner_id}</code>\n"
        f"📁 <b>Path:</b> <code>{esc(shown_path)}</code>\n\n"
        '📂 Folder ko open karein. 📄 File par click karke direct download karein.\n'
        f'📁 Folders: {len(directories)} | 📄 Files: {len(files)}'
    )
    markup = types.InlineKeyboardMarkup(row_width=2)
    for name, rel in directories[:30]:
        token = _make_zip_browser_token(bot_id, rel, 'folder')
        markup.add(types.InlineKeyboardButton('📁 ' + name[:28], callback_data='zip_folder_' + token))
    for name, rel in files[:30]:
        token = _make_zip_browser_token(bot_id, rel, 'file')
        markup.add(types.InlineKeyboardButton(_zip_file_icon(name) + ' ' + name[:28], callback_data='zip_file_' + token))
    if relative:
        parent = os.path.dirname(relative)
        token = _make_zip_browser_token(bot_id, parent, 'back')
        markup.add(types.InlineKeyboardButton('⬅️ Parent folder', callback_data='zip_back_' + token))
    markup.add(types.InlineKeyboardButton('🔙 Bot actions', callback_data='admin_bot_' + str(bot_id)))
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              parse_mode='HTML', reply_markup=markup)
    except Exception:
        bot.send_message(call.message.chat.id, text, parse_mode='HTML', reply_markup=markup)
    bot.answer_callback_query(call.id)


def download_admin_zip_file(call, token):
    resolved = _resolve_zip_browser(call, token)
    if not resolved:
        return
    owner_id, entry, folder, relative, target = resolved
    if not os.path.isfile(target):
        bot.answer_callback_query(call.id, '❌ File not found!')
        return
    try:
        file_size = os.path.getsize(target)
        if file_size > TELEGRAM_SEND_SAFE_BYTES:
            bot.answer_callback_query(call.id, f'⚠️ File exceeds {TELEGRAM_SEND_SAFE_MB} MB Telegram-safe threshold')
            bot.send_message(call.message.chat.id,
                             f'⚠️ <code>{esc(relative)}</code> is {format_size(file_size)} and cannot be sent by Telegram in one document. '
                             'Use object storage/direct download or split it outside Telegram.', parse_mode='HTML')
            return
        bot.answer_callback_query(call.id, '📥 Sending file...')
        with open(target, 'rb') as handle:
            bot.send_document(
                call.message.chat.id,
                handle,
                caption=f"📄 <code>{esc(relative)}</code>\n👤 Owner ID: <code>{owner_id}</code>",
                parse_mode='HTML'
            )
        log_action(call.from_user.id, 'ADMIN_FILE_DOWNLOAD',
                   f'Downloaded {relative} from {entry["bot_name"]}')
    except Exception as exc:
        logger.error(f'{BRAND_NAME} individual file download error: {exc}')
        try:
            bot.send_message(call.message.chat.id,
                             f'❌ File download failed: <code>{esc(str(exc)[:160])}</code>',
                             parse_mode='HTML')
        except Exception:
            pass


def admin_download_bot(call, bot_id):
    if not _is_admin_user(call.from_user.id):
        bot.answer_callback_query(call.id, '❌ Admin only!')
        return
    _, entry = find_bot_anywhere(bot_id)
    if not entry:
        bot.answer_callback_query(call.id, '❌ Bot not found!')
        return
    show_admin_zip_browser(call, bot_id)
    log_action(call.from_user.id, 'ADMIN_OPEN_FILES', f'Opened files for {entry["bot_name"]}')

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
    if not ensure_bot_files_available(b, call.message):
        bot.answer_callback_query(call.id, '❌ Files unavailable; restore failed.')
        return
    bot.answer_callback_query(call.id, f"🚀 Running {b['bot_name']}...")
    threading.Thread(target=run_bot_instance_safe, args=(b, call.message)).start()

def admin_stop_bot(call, bot_id):
    if call.from_user.id != OWNER_ID and call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    with state_lock:
        script_info = bot_scripts.get(bot_id)
    if not script_info:
        bot.answer_callback_query(call.id, "❌ Not running!")
        return
    bot.answer_callback_query(call.id, "🛑 Stopping...")
    kill_process_tree(script_info)
    set_bot_running_state(bot_id, False)
    archive_running_bot(bot_id)
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
        if _storage_enabled():
            record = get_stored_record(bot_id)
            delete_stored_telegram_message(record)
            remove_stored_record(bot_id)
        shutil.rmtree(b['folder'], ignore_errors=True)
        with state_lock:
            user_bots[owner_id] = [x for x in user_bots.get(owner_id, []) if x['bot_id'] != bot_id]
        remove_hosted_bot_db(bot_id)
        bot.answer_callback_query(call.id, "✅ Deleted!")
        bot.send_message(call.message.chat.id, f"✅ Deleted {b['bot_name']} (owner: {owner_id})")
        show_admin_user_bots(call, owner_id)
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ Error: {esc(str(e)[:30])}")

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
        log_text = f"👑 <b>Bot Logs</b>\n\n<code>{esc(logs[:1800])}</code>"
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
        bot.answer_callback_query(call.id, f"❌ Error: {esc(str(e)[:30])}")

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
        bot.answer_callback_query(call.id, f"❌ {esc(str(e)[:30])}")

def stop_all_bots(call):
    if call.from_user.id != OWNER_ID and call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    bot.answer_callback_query(call.id, f"🛑 Stopping all {BRAND_NAME} bots...")
    stopped = 0
    for bot_id in list(bot_scripts.keys()):
        try:
            kill_process_tree(bot_scripts[bot_id])
            archive_running_bot(bot_id)
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
        bot.answer_callback_query(call.id, f"❌ Error: {esc(str(e)[:30])}")

# ============================================
# CLEANUP ON EXIT
# ============================================

def cleanup_on_exit():
    global shutdown_started
    with shutdown_lock:
        if shutdown_started:
            return
        shutdown_started = True
    logger.warning(f'📢 {BRAND_NAME} Shutdown: stopping bots and attempting best-effort archival; Railway termination time may be limited.')
    with state_lock:
        script_items = list(bot_scripts.items())
        bot_groups = [(uid, list(bots)) for uid, bots in user_bots.items()]
    # These processes are being stopped by a service restart, not by a user.
    # Keep was_running=True so startup resume can restore and relaunch them.
    for bot_id, script_info in script_items:
        try:
            kill_process_tree(script_info)
            set_bot_running_state(bot_id, True)
        except Exception:
            pass
    archived = 0
    if _storage_enabled():
        for uid, bots in bot_groups:
            for b in bots:
                folder = b.get('folder', '')
                if os.path.isdir(folder):
                    if archive_bot_to_telegram(b['bot_id'], folder, b['bot_name'], uid):
                        archived += 1
    logger.warning(f'{BRAND_NAME} Shutdown finished best-effort archival: {archived}/{sum(1 for _uid, bots in bot_groups for _b in bots)} bot(s) archived. Use explicit Stop/Archive or object storage for guarantees.')

atexit.register(cleanup_on_exit)

import signal
def handle_sigterm(signum, frame):
    logger.info("📢 SIGTERM received. Shutting down gracefully...")
    cleanup_on_exit()
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_sigterm)

# ============================================
# MAIN
# ============================================

def main():
    logger.info("=" * 50)
    logger.info(f"🤖 Starting {BRAND_NAME} {BRAND_EMOJI} Bot...")

    init_db()
    load_data()
    resume_persisted_bots()

    logger.info(f"📁 Base Dir: {BASE_DIR}")
    logger.info(f"📁 Upload Dir: {UPLOAD_BOTS_DIR}")
    logger.info(f"💾 Database backend: {DB_BACKEND}" + (f"; last error: {DB_LAST_ERROR}" if DB_LAST_ERROR else ''))
    logger.info(f"📨 Telegram storage: {'ON — channel ' + STORAGE_CHANNEL_ID if _storage_enabled() else 'OFF'}")
    logger.info("=" * 50)

    threading.Thread(target=bot_watchdog_loop, daemon=True).start()
    keep_alive()
    while True:
        try:
            logger.info(f"🚀 Starting {BRAND_NAME} bot polling...")
            bot.infinity_polling(timeout=60, long_polling_timeout=20, skip_pending=True, restart_on_change=False)
        except ApiTelegramException as e:
            if "Conflict" in str(e) or "409" in str(e):
                logger.error("⚠️ Conflict detected (409)! Another instance is running. Waiting 15s...")
                time.sleep(15)
            else:
                logger.error(f"❌ Telegram API error: {e}")
                time.sleep(5)
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
