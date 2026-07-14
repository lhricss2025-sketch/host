# -*- coding: utf-8 -*-
import telebot
import subprocess
import os
import zipfile
import tempfile
import shutil
from telebot import types
import time
from datetime import datetime, timedelta
import psutil
import sqlite3
import json
import logging
import signal
import threading
import re
import sys
import atexit
import requests
import random
import hashlib
from flask import Flask
from threading import Thread

# ============================================
# TURSO DATABASE SETUP
# ============================================

# Try to import Turso
try:
    import libsql_experimental as libsql
    TURSO_AVAILABLE = True
except ImportError:
    TURSO_AVAILABLE = False
    print("⚠️ Turso not installed, using local SQLite")

# Get Turso credentials from environment variables (Railway)
TURSO_URL = os.environ.get('TURSO_URL', '')
TURSO_TOKEN = os.environ.get('TURSO_TOKEN', '')

def get_db_connection():
    """Get database connection (Turso or fallback to local SQLite)"""
    if TURSO_AVAILABLE and TURSO_URL and TURSO_TOKEN:
        try:
            conn = libsql.connect(database=TURSO_URL, auth_token=TURSO_TOKEN)
            print("✅ Connected to Turso database!")
            return conn
        except Exception as e:
            print(f"❌ Turso connection failed: {e}")
            print("⚠️ Falling back to local SQLite...")
            return sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    else:
        return sqlite3.connect(DATABASE_PATH, check_same_thread=False)

# ============================================
# CONFIGURATION
# ============================================

TOKEN = '7626175588:AAExy_qI9eplPj9qa3w0BIBRy1Y6HIzf2fc'  # Apna token
OWNER_ID = 7847937078  # Apna ID
ADMIN_ID = 7847937078  # Apna ID
YOUR_USERNAME = '@SENZO_DEV'  # Apna username
UPDATE_CHANNEL = 'https://t.me/senzo_devs'  # Apna channel

# Brand Name
BRAND_NAME = "SENZO DEV"
BRAND_EMOJI = "🐺"

# ============================================
# 🖼️ START IMAGE CONFIGURATION
# ============================================

# Default banner (change using /setbanner command)
START_IMAGE_URL = "https://i.postimg.cc/your-image.jpg"

# Default description (change using /setdesc command)
START_DESCRIPTION = """
🚀 <b>Upload & Host Your Bots</b>
📤 <b>Supported:</b> Python • Node.js • ZIP
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

FREE_USER_LIMIT = 10
SUBSCRIBED_USER_LIMIT = 15
ADMIN_LIMIT = 999
OWNER_LIMIT = float('inf')

os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(IROTECH_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

bot = telebot.TeleBot(TOKEN, parse_mode='HTML')
bot_scripts = {}
user_subscriptions = {}
user_files = {}
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
# DATABASE FUNCTIONS (Turso + Fallback)
# ============================================

def init_db():
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        c.execute('''CREATE TABLE IF NOT EXISTS subscriptions
        (user_id INTEGER PRIMARY KEY, expiry TEXT)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS user_files
        (user_id INTEGER, file_name TEXT, file_type TEXT, upload_time TEXT,
        file_size INTEGER, PRIMARY KEY (user_id, file_name))''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS active_users
        (user_id INTEGER PRIMARY KEY, username TEXT, first_seen TEXT, last_seen TEXT)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS admins
        (user_id INTEGER PRIMARY KEY)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS bot_logs
        (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action TEXT,
        details TEXT, timestamp TEXT)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS running_scripts
        (script_key TEXT PRIMARY KEY, user_id INTEGER, file_name TEXT,
        start_time TEXT, pid INTEGER)''')
        
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
        
        c.execute('SELECT user_id, file_name, file_type FROM user_files')
        for user_id, file_name, file_type in c.fetchall():
            if user_id not in user_files:
                user_files[user_id] = []
            user_files[user_id].append((file_name, file_type))
        
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

def save_user_file_db(user_id, file_name, file_type, file_size=0):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('''INSERT OR REPLACE INTO user_files
        (user_id, file_name, file_type, upload_time, file_size)
        VALUES (?, ?, ?, ?, ?)''',
        (user_id, file_name, file_type, datetime.now().isoformat(), file_size))
        conn.commit()
        conn.close()
        log_action(user_id, "FILE_UPLOAD", f"Uploaded {file_name}")
    except Exception as e:
        logger.error(f"{BRAND_NAME} Error saving file: {e}")

def remove_user_file_db(user_id, file_name):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('DELETE FROM user_files WHERE user_id = ? AND file_name = ?', (user_id, file_name))
        conn.commit()
        conn.close()
        log_action(user_id, "FILE_DELETE", f"Deleted {file_name}")
    except Exception as e:
        logger.error(f"{BRAND_NAME} Error removing file: {e}")

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
    except Exception as e:
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
    
    # Check subscription
    if user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now():
        return SUBSCRIBED_USER_LIMIT
    
    return min(base_limit + extra_bots, FREE_USER_LIMIT)

def get_current_bot_count(user_id):
    return len(user_files.get(user_id, []))

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
        except:
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
    except Exception as e:
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
    running_bots = len([k for k, v in bot_scripts.items() if v.get('process') and is_bot_running_check(k)])
    
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT SUM(points) FROM user_points')
        total_points = c.fetchone()[0] or 0
        c.execute('SELECT COUNT(*) FROM referrals')
        total_refs = c.fetchone()[0]
        conn.close()
    except:
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

def get_user_file_count(user_id):
    return len(user_files.get(user_id, []))

def is_bot_running_check(script_key):
    script_info = bot_scripts.get(script_key)
    if script_info and script_info.get('process'):
        try:
            proc = psutil.Process(script_info['process'].pid)
            return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except:
            return False
    return False

def is_bot_running(script_owner_id, file_name):
    script_key = f"{script_owner_id}_{file_name}"
    return is_bot_running_check(script_key)

def cleanup_script(script_key):
    if script_key in bot_scripts:
        script_info = bot_scripts[script_key]
        if 'log_file' in script_info and hasattr(script_info['log_file'], 'close'):
            try:
                if not script_info['log_file'].closed:
                    script_info['log_file'].close()
            except:
                pass
        del bot_scripts[script_key]

def kill_process_tree(process_info):
    try:
        if 'log_file' in process_info and hasattr(process_info['log_file'], 'close'):
            try:
                if not process_info['log_file'].closed:
                    process_info['log_file'].close()
            except:
                pass
        process = process_info.get('process')
        if process and hasattr(process, 'pid'):
            try:
                parent = psutil.Process(process.pid)
                children = parent.children(recursive=True)
                for child in children:
                    try:
                        child.terminate()
                    except:
                        pass
                gone, alive = psutil.wait_procs(children, timeout=2)
                for p in alive:
                    try:
                        p.kill()
                    except:
                        pass
                try:
                    parent.terminate()
                    parent.wait(timeout=2)
                except:
                    parent.kill()
            except:
                pass
    except:
        pass

# ============================================
# ANIMATION FUNCTIONS
# ============================================

def send_animated_message(chat_id, final_text, animation_type="loading", duration=2, steps=4):
    try:
        action_map = {
            "loading": "Authenticating session",
            "upload": "Uploading file",
            "download": "Downloading file",
            "delete": "Deleting file",
            "run": "Starting script",
            "stop": "Stopping script",
            "install": "Installing dependencies",
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
                except:
                    pass
            time.sleep(duration / steps)
        try:
            bot.edit_message_text(final_text, chat_id, msg.message_id, parse_mode='HTML')
        except:
            bot.send_message(chat_id, final_text, parse_mode='HTML')
        return msg
    except Exception as e:
        logger.error(f"{BRAND_NAME} Animation error: {e}")
        return bot.send_message(chat_id, final_text, parse_mode='HTML')

def send_spinner_animation(chat_id, text, duration=2):
    return send_animated_message(chat_id, text, "loading", duration)

# ============================================
# PACKAGE INSTALLATION
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

def attempt_install_pip(module_name, message):
    package_name = TELEGRAM_MODULES.get(module_name.lower(), module_name)
    if package_name is None:
        return False
    try:
        msg = send_spinner_animation(message.chat.id, f"Installing {package_name}...", duration=2)
        command = [sys.executable, '-m', 'pip', 'install', package_name]
        result = subprocess.run(command, capture_output=True, text=True, check=False,
                                encoding='utf-8', errors='ignore', timeout=120)
        if result.returncode == 0:
            try:
                bot.edit_message_text(
                    f"✅ <b>Package Installed!</b>\n📦 <code>{package_name}</code> installed successfully!",
                    message.chat.id, msg.message_id, parse_mode='HTML'
                )
            except:
                bot.send_message(message.chat.id, f"✅ Package {package_name} installed!", parse_mode='HTML')
            return True
        else:
            error_msg = result.stderr[:500] if result.stderr else result.stdout[:500]
            try:
                bot.edit_message_text(
                    f"❌ <b>Installation Failed</b>\n<code>{error_msg}</code>",
                    message.chat.id, msg.message_id, parse_mode='HTML'
                )
            except:
                pass
            return False
    except:
        return False

def attempt_install_npm(module_name, user_folder, message):
    try:
        msg = send_spinner_animation(message.chat.id, f"Installing npm: {module_name}...", duration=2)
        command = ['npm', 'install', module_name]
        result = subprocess.run(command, capture_output=True, text=True, check=False,
                                cwd=user_folder, encoding='utf-8', errors='ignore', timeout=120)
        if result.returncode == 0:
            try:
                bot.edit_message_text(
                    f"✅ <b>NPM Package Installed!</b>\n📦 <code>{module_name}</code>",
                    message.chat.id, msg.message_id, parse_mode='HTML'
                )
            except:
                pass
            return True
        return False
    except FileNotFoundError:
        bot.send_message(message.chat.id, "❌ NPM not found!")
        return False
    except:
        return False

# ============================================
# SCRIPT RUNNING FUNCTIONS
# ============================================

def run_script(script_path, script_owner_id, user_folder, file_name, message_obj, attempt=1):
    max_attempts = 3
    if attempt > max_attempts:
        bot.send_message(message_obj.chat.id, f"❌ Failed to run '{file_name}' after {max_attempts} attempts.")
        return
    script_key = f"{script_owner_id}_{file_name}"
    try:
        if not os.path.exists(script_path):
            bot.send_message(message_obj.chat.id, f"❌ Script '{file_name}' not found!")
            return
        
        check_result = subprocess.run(
            [sys.executable, '-c', f'import ast; ast.parse(open("{script_path}").read())'],
            capture_output=True, text=True, timeout=10
        )
        if check_result.returncode != 0:
            bot.send_message(message_obj.chat.id,
                             f"⚠️ <b>Syntax Error in Script</b>\n<code>{check_result.stderr[:500]}</code>",
                             parse_mode='HTML')
            return
        
        terminal_msg = f"""
╔══════════════════════════════════════╗
║      🚀 <b>{BRAND_NAME}: STARTING SCRIPT</b> 🚀 ║
╠══════════════════════════════════════╣
║ 📄 File: <code>{file_name[:25]}</code>
║ 👤 User: {script_owner_id}
║ 🔄 Attempt: {attempt}/{max_attempts}
╚══════════════════════════════════════╝
"""
        msg = send_animated_message(message_obj.chat.id, terminal_msg, "run", duration=2)
        log_file_path = os.path.join(LOGS_DIR, f"{script_key}.log")
        log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')
        process = subprocess.Popen(
            [sys.executable, script_path],
            cwd=user_folder,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='ignore'
        )
        bot_scripts[script_key] = {
            'process': process,
            'file_name': file_name,
            'user_id': script_owner_id,
            'start_time': datetime.now(),
            'log_file': log_file,
            'log_path': log_file_path,
            'script_key': script_key,
            'script_path': script_path
        }
        time.sleep(2)
        if process.poll() is None:
            success_msg = f"""
╔══════════════════════════════════════╗
║     ✅ <b>{BRAND_NAME}: SCRIPT RUNNING</b> ✅   ║
╠══════════════════════════════════════╣
║ 📄 <b>File:</b> <code>{file_name[:25]}</code>
║ 🆔 <b>PID:</b> {process.pid}
║ ⏱️ <b>Started:</b> {datetime.now().strftime('%H:%M:%S')}
╚══════════════════════════════════════╝
"""
            try:
                bot.edit_message_text(success_msg, message_obj.chat.id, msg.message_id, parse_mode='HTML')
            except:
                bot.send_message(message_obj.chat.id, success_msg, parse_mode='HTML')
            log_action(script_owner_id, "SCRIPT_START", f"Started {file_name} (PID: {process.pid})")
        else:
            log_file.close()
            with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                error_output = f.read()[-1000:]
            match = re.search(r"ModuleNotFoundError: No module named '(.+?)'", error_output)
            if match:
                module_name = match.group(1).strip()
                if attempt_install_pip(module_name, message_obj):
                    time.sleep(1)
                    run_script(script_path, script_owner_id, user_folder, file_name, message_obj, attempt + 1)
                    return
            error_msg = f"""
╔══════════════════════════════════════╗
║     ❌ <b>{BRAND_NAME}: SCRIPT FAILED</b> ❌     ║
╠══════════════════════════════════════╣
║ 📄 <b>File:</b> <code>{file_name[:25]}</code>
║ ❗ <b>Exit Code:</b> {process.returncode}
╠══════════════════════════════════════╣
<code>{error_output[:400]}</code>
╚══════════════════════════════════════╝
"""
            try:
                bot.edit_message_text(error_msg, message_obj.chat.id, msg.message_id, parse_mode='HTML')
            except:
                bot.send_message(message_obj.chat.id, error_msg, parse_mode='HTML')
            cleanup_script(script_key)
    except Exception as e:
        logger.error(f"{BRAND_NAME} Error running script: {e}")
        bot.send_message(message_obj.chat.id, f"❌ Error: {str(e)[:200]}")

def run_js_script(script_path, script_owner_id, user_folder, file_name, message_obj, attempt=1):
    max_attempts = 3
    if attempt > max_attempts:
        bot.send_message(message_obj.chat.id, f"❌ Failed to run '{file_name}' after {max_attempts} attempts.")
        return
    script_key = f"{script_owner_id}_{file_name}"
    try:
        if not os.path.exists(script_path):
            bot.send_message(message_obj.chat.id, f"❌ Script '{file_name}' not found!")
            return
        
        terminal_msg = f"""
╔══════════════════════════════════════╗
║      🟢 <b>{BRAND_NAME}: STARTING NODE.JS</b> 🟢║
╠══════════════════════════════════════╣
║ 📄 File: <code>{file_name[:25]}</code>
║ 👤 User: {script_owner_id}
║ 🔄 Attempt: {attempt}/{max_attempts}
╚══════════════════════════════════════╝
"""
        msg = send_animated_message(message_obj.chat.id, terminal_msg, "run", duration=2)
        log_file_path = os.path.join(LOGS_DIR, f"{script_key}.log")
        log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')
        process = subprocess.Popen(
            ['node', script_path],
            cwd=user_folder,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='ignore'
        )
        bot_scripts[script_key] = {
            'process': process,
            'file_name': file_name,
            'user_id': script_owner_id,
            'start_time': datetime.now(),
            'log_file': log_file,
            'log_path': log_file_path,
            'script_key': script_key,
            'script_path': script_path,
            'type': 'js'
        }
        time.sleep(2)
        if process.poll() is None:
            success_msg = f"""
╔══════════════════════════════════════╗
║     ✅ <b>{BRAND_NAME}: NODE.JS RUNNING</b> ✅  ║
╠══════════════════════════════════════╣
║ 📄 <b>File:</b> <code>{file_name[:25]}</code>
║ 🆔 <b>PID:</b> {process.pid}
║ ⏱️ <b>Started:</b> {datetime.now().strftime('%H:%M:%S')}
╚══════════════════════════════════════╝
"""
            try:
                bot.edit_message_text(success_msg, message_obj.chat.id, msg.message_id, parse_mode='HTML')
            except:
                bot.send_message(message_obj.chat.id, success_msg, parse_mode='HTML')
        else:
            log_file.close()
            with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                error_output = f.read()[-1000:]
            match = re.search(r"Cannot find module '(.+?)'", error_output)
            if match:
                module_name = match.group(1).strip()
                if attempt_install_npm(module_name, user_folder, message_obj):
                    time.sleep(1)
                    run_js_script(script_path, script_owner_id, user_folder, file_name, message_obj, attempt + 1)
                    return
            error_msg = f"""
╔══════════════════════════════════════╗
║     ❌ <b>{BRAND_NAME}: NODE.JS FAILED</b> ❌    ║
╠══════════════════════════════════════╣
║ 📄 <b>File:</b> <code>{file_name[:25]}</code>
║ ❗ <b>Exit Code:</b> {process.returncode}
╠══════════════════════════════════════╣
<code>{error_output[:400]}</code>
╚══════════════════════════════════════╝
"""
            try:
                bot.edit_message_text(error_msg, message_obj.chat.id, msg.message_id, parse_mode='HTML')
            except:
                bot.send_message(message_obj.chat.id, error_msg, parse_mode='HTML')
            cleanup_script(script_key)
    except FileNotFoundError:
        bot.send_message(message_obj.chat.id, "❌ Node.js not found!")
    except Exception as e:
        logger.error(f"{BRAND_NAME} Error running JS script: {e}")
        bot.send_message(message_obj.chat.id, f"❌ Error: {str(e)[:200]}")

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

def get_file_actions_keyboard(file_name, is_running=False):
    markup = types.InlineKeyboardMarkup(row_width=2)
    if is_running:
        markup.add(
            types.InlineKeyboardButton("🛑 Stop", callback_data=f"stop_{file_name}"),
            types.InlineKeyboardButton("📋 Logs", callback_data=f"logs_{file_name}")
        )
        markup.add(
            types.InlineKeyboardButton("🔄 Restart", callback_data=f"restart_{file_name}")
        )
    else:
        markup.add(
            types.InlineKeyboardButton("▶️ Run", callback_data=f"run_{file_name}"),
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f"delete_{file_name}")
        )
        markup.add(
            types.InlineKeyboardButton("📥 Download", callback_data=f"download_{file_name}"),
            types.InlineKeyboardButton("📝 Edit", callback_data=f"edit_{file_name}")
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

📢 <b>Use the buttons below to navigate!</b> ⬇️
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
║ <b>📤 File Management:</b>
║ • /upload - Upload a file
║ • /files - View your files
║ • /delete - Delete a file
║
║ <b>🤖 Bot Control:</b>
║ • /run - Run a script
║ • /stop - Stop a running script
║ • /logs - View script logs
║ • /running - See running scripts
║
║ <b>⭐ Points & Referrals:</b>
║ • /points - Check your points
║ • /referral - Get your referral link
║ • /referrals - View referral history
║
║ <b>📊 Information:</b>
║ • /stats - Bot statistics
║ • /speed - Check bot speed
║ • /status - Your account status
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
    except:
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
    except:
        bot.send_message(message.chat.id, speed_text, parse_mode='HTML')

@bot.message_handler(commands=['running'])
def running_command(message):
    user_id = message.from_user.id
    msg = send_spinner_animation(message.chat.id, f"Fetching {BRAND_NAME} bots...", duration=1)
    running_bots = []
    for script_key, info in bot_scripts.items():
        if is_bot_running_check(script_key):
            if user_id == OWNER_ID or user_id in admin_ids or info.get('user_id') == user_id:
                uptime = datetime.now() - info.get('start_time', datetime.now())
                running_bots.append({
                    'key': script_key,
                    'file': info.get('file_name', 'Unknown'),
                    'user': info.get('user_id', 'Unknown'),
                    'pid': info.get('process', {}).pid if info.get('process') else 'N/A',
                    'uptime': str(uptime).split('.')[0]
                })
    if running_bots:
        text = f"""
╔══════════════════════════════════════╗
║      🟢 <b>{BRAND_NAME} BOTS</b> 🟢           ║
╠══════════════════════════════════════╣
"""
        for i, bot_info in enumerate(running_bots, 1):
            text += f"""║ {i}. 📄 <code>{bot_info['file'][:20]}</code>
║    👤 User: {bot_info['user']}
║    🆔 PID: {bot_info['pid']}
║    ⏱️ Uptime: {bot_info['uptime']}
║ ──────────────────────────────────
"""
        text += "╚══════════════════════════════════════╝"
    else:
        text = f"""
╔══════════════════════════════════════╗
║      🔴 <b>NO {BRAND_NAME} BOTS</b> 🔴        ║
╠══════════════════════════════════════╣
║
║  No scripts are currently running.
║  Upload a file and run it!
║
╚══════════════════════════════════════╝
"""
    try:
        bot.edit_message_text(text, message.chat.id, msg.message_id, parse_mode='HTML')
    except:
        bot.send_message(message.chat.id, text, parse_mode='HTML')

@bot.message_handler(commands=['points'])
def points_command(message):
    show_my_points(message)

@bot.message_handler(commands=['referral'])
def referral_command(message):
    show_referral_system(message)

@bot.message_handler(commands=['referrals'])
def referrals_command(message):
    user_id = message.from_user.id
    show_referral_history_for_command(message, user_id)

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
        except:
            failed += 1
        if (i + 1) % 10 == 0:
            try:
                bar = "🟩" * ((i + 1) // (total // 4) if total > 0 else 0) + "⬜" * (4 - (i + 1) // (total // 4) if total > 0 else 4)
                bar = bar[:4].ljust(4, "⬜")
                bot.edit_message_text(
                    f"⚙️ Loading... ({int((i+1)/total*100)}%)\n[{bar}] Broadcasting...",
                    message.chat.id, progress_msg.message_id
                )
            except:
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
    except:
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
    except:
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
        except:
            pass
    else:
        bot.reply_to(message, "❌ Failed to add points!")

@bot.message_handler(commands=['setbanner'])
def set_banner_command(message):
    """Set new banner image (Admin only)"""
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

<b>Supported Hosts:</b>
• https://telegra.ph/
• https://imgur.com/
• https://postimg.cc/
• Any direct image URL
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
    except:
        bot.reply_to(message, "❌ Cannot access the URL! Please check and try again.")
        return
    
    START_IMAGE_URL = new_url
    bot.reply_to(message, f"""
✅ <b>Banner Updated!</b>

🖼️ <b>New Banner:</b>
<code>{START_IMAGE_URL}</code>

💡 <b>Test it:</b> Send /start to see the new banner!
""", parse_mode='HTML')
    
    log_action(user_id, "BANNER_CHANGE", f"Changed banner to {START_IMAGE_URL}")

@bot.message_handler(commands=['setdesc'])
def set_description_command(message):
    """Set new start description (Admin only)"""
    user_id = message.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.reply_to(message, "❌ Admin only!")
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, """
📖 <b>Usage:</b> /setdesc &lt;description&gt;

<b>Example:</b>
/setdesc 🚀 Upload & Host Your Bots
📤 Python • Node.js • ZIP
⭐ Earn Points • Refer & Win

<b>Use HTML tags:</b>
• <b>bold</b>
• <i>italic</i>
• <code>code</code>
""", parse_mode='HTML')
        return
    
    global START_DESCRIPTION
    new_desc = parts[1].strip()
    
    START_DESCRIPTION = new_desc
    bot.reply_to(message, f"""
✅ <b>Description Updated!</b>

📝 <b>New Description:</b>
{new_desc}

💡 <b>Test it:</b> Send /start to see the new description!
""", parse_mode='HTML')
    
    log_action(user_id, "DESC_CHANGE", f"Changed description")

@bot.message_handler(commands=['settings'])
def settings_command(message):
    """View current start settings (Admin only)"""
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

━━━━━━━━━━━━━━━━━━━━━━━━

<b>Commands:</b>
• /setbanner &lt;url&gt; - Change banner
• /setdesc &lt;text&gt; - Change description
• /settings - View settings
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

💡 <b>How to get more slots:</b>
1. Share your referral link
2. Get 1 point per referral
3. 5 points = 1 extra bot slot

🎯 <b>You need {needed} more points!</b>
Use 🎯 Referral System to get more!
""", parse_mode='HTML')
        return
    
    current_count = get_user_file_count(user_id)
    limit = get_user_file_limit(user_id)
    
    upload_text = f"""
╔══════════════════════════════════════╗
║       📤 <b>{BRAND_NAME}: FILE UPLOAD</b> 📤   ║
╠══════════════════════════════════════╣
║
║  Send your file now!
║
║  <b>Supported formats:</b>
║  • Python (.py)
║  • JavaScript (.js)
║  • ZIP archives (.zip)
║
║  📁 Files: {current_count}/{int(limit) if limit != float('inf') else '∞'}
║  ⭐ Points: {get_user_points(user_id)['points']}
║
╚══════════════════════════════════════╝
"""
    bot.send_message(message.chat.id, upload_text, parse_mode='HTML')

def show_user_files(message):
    user_id = message.from_user.id
    msg = send_spinner_animation(message.chat.id, f"Loading {BRAND_NAME} files...", duration=1)
    files = user_files.get(user_id, [])
    if not files:
        text = f"""
╔══════════════════════════════════════╗
║       📂 <b>{BRAND_NAME}: YOUR FILES</b> 📂   ║
╠══════════════════════════════════════╣
║
║  You haven't uploaded any files yet!
║
║  Use 📤 Upload File to get started.
║
╚══════════════════════════════════════╝
"""
        try:
            bot.edit_message_text(text, message.chat.id, msg.message_id, parse_mode='HTML')
        except:
            bot.send_message(message.chat.id, text, parse_mode='HTML')
        return
    
    text = f"""
╔══════════════════════════════════════╗
║       📂 <b>{BRAND_NAME}: YOUR FILES</b> 📂   ║
╠══════════════════════════════════════╣
"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    for i, (file_name, file_type) in enumerate(files, 1):
        is_running = is_bot_running(user_id, file_name)
        status = "🟢" if is_running else "🔴"
        type_icon = "🐍" if file_type == "py" else "🟨" if file_type == "js" else "📦"
        text += f"║ {i}. {status} {type_icon} <code>{file_name[:25]}</code>\n"
        markup.add(types.InlineKeyboardButton(
            f"{status} {file_name[:15]}",
            callback_data=f"file_{file_name}"
        ))
    text += "╚══════════════════════════════════════╝\nSelect a file for actions:"
    try:
        bot.edit_message_text(text, message.chat.id, msg.message_id, parse_mode='HTML', reply_markup=markup)
    except:
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
    except:
        total_users_with_points = 0
        total_points = 0
        total_referrals = 0
    
    total_files = sum(len(files) for files in user_files.values())
    total_users = len(user_files)
    
    admin_text = f"""
╔══════════════════════════════════════╗
║       👑 <b>{BRAND_NAME}: ADMIN PANEL</b> 👑   ║
╠══════════════════════════════════════╣
║
║  <b>📊 General Statistics:</b>
║  • Total Users: {len(active_users)}
║  • Users with Files: {total_users}
║  • Total Files: {total_files}
║  • Active Subs: {len([u for u, d in user_subscriptions.items() if d['expiry'] > datetime.now()])}
║  • Running Bots: {len([k for k in bot_scripts if is_bot_running_check(k)])}
║
║  <b>⭐ Points System:</b>
║  • Users with Points: {total_users_with_points}
║  • Total Points: {total_points}
║  • Total Referrals: {total_referrals}
║
║  <b>🔧 Commands:</b>
║  • /broadcast - Send to all
║  • /subscribe - Add subscription
║  • /lock - Lock/unlock bot
║  • /addadmin - Add admin
║  • /removeadmin - Remove admin
║  • /stopall - Stop all bots
║  • /addpoints - Add points to user
║  • /setbanner - Change banner
║  • /setdesc - Change description
║  • /settings - View settings
║
╚══════════════════════════════════════╝
"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📂 All User Files", callback_data="admin_view_all_files"),
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
║  💡 <b>How it works:</b>
║  • Share your referral link
║  • Get 1 point per referral
║  • 5 points = 1 extra bot slot
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
║  <b>📋 How It Works:</b>
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
║  <b>💡 Pro Tip:</b>
║  Share in Telegram groups!
║  Each referral = closer to more bots!
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
        text = """
📊 <b>No Referrals Yet</b>

Start sharing your referral link to earn points!
Each referral = 1 point ✨
5 points = 1 extra bot slot 🚀
"""
        bot.send_message(message.chat.id, text, parse_mode='HTML')
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
        username = "Unknown"
        try:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute('SELECT username FROM active_users WHERE user_id = ?', (referred_id,))
            result = c.fetchone()
            if result:
                username = result[0] or str(referred_id)
            conn.close()
        except:
            pass
        text += f"║  {i}. 👤 {username} ({referred_id})\n"
        text += f"║     🕐 {timestamp[:16]}\n"
    
    text += """
╚══════════════════════════════════════╝
"""
    bot.send_message(message.chat.id, text, parse_mode='HTML')

# ============================================
# FILE UPLOAD HANDLER
# ============================================

@bot.message_handler(content_types=['document'])
def handle_document(message):
    user_id = message.from_user.id
    
    can_upload, status = can_user_upload(user_id)
    if not can_upload:
        bot.reply_to(message, f"❌ Can't upload! {status}")
        return
    
    current_count = get_user_file_count(user_id)
    limit = get_user_file_limit(user_id)
    if current_count >= limit:
        bot.reply_to(message, f"❌ File limit reached! ({current_count}/{int(limit) if limit != float('inf') else '∞'})")
        return
    
    file_name = message.document.file_name
    file_size = message.document.file_size
    file_ext = file_name.split('.')[-1].lower() if '.' in file_name else ''
    allowed_extensions = ['py', 'js', 'zip', 'json', 'txt', 'env', 'yml', 'yaml']
    if file_ext not in allowed_extensions:
        bot.reply_to(message, f"❌ Unsupported type: .{file_ext}")
        return
    
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
        try:
            bot.edit_message_text(
                upload_text + "║  📥 Processing...\n╚══════════════════════════════════════╝",
                message.chat.id, progress_msg.message_id, parse_mode='HTML'
            )
        except:
            pass
        
        user_folder = get_user_folder(user_id)
        file_path = os.path.join(user_folder, file_name)
        
        if file_ext == 'zip':
            with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp:
                tmp.write(downloaded_file)
                tmp_path = tmp.name
            try:
                with zipfile.ZipFile(tmp_path, 'r') as zip_ref:
                    zip_ref.extractall(user_folder)
                    extracted_files = []
                    for root, dirs, files in os.walk(user_folder):
                        for f in files:
                            if f.endswith(('.py', '.js')):
                                extracted_files.append(f)
                                if user_id not in user_files:
                                    user_files[user_id] = []
                                if (f, f.split('.')[-1]) not in user_files[user_id]:
                                    user_files[user_id].append((f, f.split('.')[-1]))
                                    save_user_file_db(user_id, f, f.split('.')[-1], 0)
                    os.unlink(tmp_path)
                    success_text = upload_text + f"""║  ✅ ZIP Extracted!
║  📁 Files: {len(extracted_files)}
╚══════════════════════════════════════╝
"""
            except zipfile.BadZipFile:
                bot.edit_message_text(
                    upload_text + "║  ❌ Invalid ZIP!\n╚══════════════════════════════════════╝",
                    message.chat.id, progress_msg.message_id, parse_mode='HTML'
                )
                return
        else:
            with open(file_path, 'wb') as f:
                f.write(downloaded_file)
            if user_id not in user_files:
                user_files[user_id] = []
            user_files[user_id] = [(n, t) for n, t in user_files[user_id] if n != file_name]
            user_files[user_id].append((file_name, file_ext))
            save_user_file_db(user_id, file_name, file_ext, file_size)
            success_text = upload_text + f"""║  ✅ Upload Complete!
╚══════════════════════════════════════╝
"""
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        if file_ext in ['py', 'js']:
            markup.add(
                types.InlineKeyboardButton("▶️ Run Now", callback_data=f"run_{file_name}"),
                types.InlineKeyboardButton("📂 View Files", callback_data="back_to_files")
            )
        else:
            markup.add(types.InlineKeyboardButton("📂 View Files", callback_data="back_to_files"))
        
        try:
            bot.edit_message_text(success_text, message.chat.id, progress_msg.message_id,
                                  parse_mode='HTML', reply_markup=markup)
        except:
            bot.send_message(message.chat.id, success_text, parse_mode='HTML', reply_markup=markup)
    except Exception as e:
        logger.error(f"{BRAND_NAME} Upload error: {e}")
        try:
            bot.edit_message_text(
                upload_text + f"║  ❌ Error: {str(e)[:30]}\n╚══════════════════════════════════════╝",
                message.chat.id, progress_msg.message_id, parse_mode='HTML'
            )
        except:
            bot.reply_to(message, f"❌ Upload failed: {str(e)[:100]}")

# ============================================
# CALLBACK QUERY HANDLER
# ============================================

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id
    data = call.data
    
    try:
        # --- Referral callbacks ---
        if data == "share_referral":
            share_referral(call)
        elif data == "referral_history":
            show_referral_history(call)
        elif data == "refresh_points":
            refresh_points(call)
        elif data == "copy_referral_link":
            copy_referral_link(call)
        
        # --- File callbacks ---
        elif data.startswith("file_"):
            file_name = data[5:]
            show_file_actions(call, file_name)
        elif data.startswith("run_"):
            file_name = data[4:]
            run_user_script(call, file_name)
        elif data.startswith("stop_"):
            file_name = data[5:]
            stop_user_script(call, file_name)
        elif data.startswith("delete_"):
            file_name = data[7:]
            delete_user_file(call, file_name)
        elif data.startswith("download_"):
            file_name = data[9:]
            download_user_file(call, file_name)
        elif data.startswith("logs_"):
            file_name = data[5:]
            show_script_logs(call, file_name)
        elif data.startswith("restart_"):
            file_name = data[8:]
            restart_user_script(call, file_name)
        elif data == "back_to_files":
            show_user_files_callback(call)
        elif data.startswith("confirm_delete_"):
            file_name = data[15:]
            confirm_delete_file(call, file_name)
        elif data.startswith("cancel_delete_"):
            bot.answer_callback_query(call.id, "❌ Cancelled")
            show_user_files_callback(call)
        
        # --- Admin callbacks ---
        elif data == "admin_view_all_files":
            show_all_user_files_for_admin(call)
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
            class FakeMessage:
                def __init__(self, call):
                    self.chat = call.message.chat
                    self.from_user = call.from_user
            show_admin_panel(FakeMessage(call))
            bot.answer_callback_query(call.id)
        
        elif data.startswith("admin_user_"):
            target_user = int(data[11:])
            show_admin_user_files(call, target_user)
        
        elif data.startswith("admin_file_"):
            parts = data.split("_", 2)
            if len(parts) == 3:
                target_user = int(parts[1])
                file_name = parts[2]
                show_admin_file_actions(call, target_user, file_name)
        
        elif data.startswith("admin_download_"):
            parts = data.split("_", 2)
            if len(parts) == 3:
                target_user = int(parts[1])
                file_name = parts[2]
                download_user_file_as_admin(call, target_user, file_name)
        
        elif data.startswith("admin_run_"):
            parts = data.split("_", 2)
            if len(parts) == 3:
                target_user = int(parts[1])
                file_name = parts[2]
                run_user_script_as_admin(call, target_user, file_name)
        
        elif data.startswith("admin_stop_"):
            parts = data.split("_", 2)
            if len(parts) == 3:
                target_user = int(parts[1])
                file_name = parts[2]
                stop_user_script_as_admin(call, target_user, file_name)
        
        elif data.startswith("admin_delete_"):
            parts = data.split("_", 2)
            if len(parts) == 3:
                target_user = int(parts[1])
                file_name = parts[2]
                delete_user_file_as_admin(call, target_user, file_name)
        
        elif data.startswith("admin_logs_"):
            parts = data.split("_", 2)
            if len(parts) == 3:
                target_user = int(parts[1])
                file_name = parts[2]
                show_admin_script_logs(call, target_user, file_name)
                
    except Exception as e:
        logger.error(f"{BRAND_NAME} Callback error: {e}")
        bot.answer_callback_query(call.id, f"❌ Error: {str(e)[:50]}")

# ============================================
# REFERRAL CALLBACK FUNCTIONS
# ============================================

def share_referral(call):
    user_id = call.from_user.id
    referral_link = get_user_referral_link(user_id)
    
    share_text = f"""
🎯 <b>Join {BRAND_NAME} Bot Hosting!</b>

🚀 Host your own Telegram bots!
📤 Upload & run Python/Node.js scripts
🎁 Get FREE points for referrals!

<b>🔗 Click to join:</b>
{referral_link}

<b>💡 Benefits:</b>
• 2 free bot slots
• Earn 1 point per referral
• 5 points = 1 extra bot slot
• Unlimited potential! 🚀

Join now! 🤖
"""
    
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("📤 Share via Telegram", switch_inline_query=share_text),
        types.InlineKeyboardButton("🔗 Copy Link", callback_data="copy_referral_link")
    )
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="refresh_points"))
    
    bot.edit_message_text(
        f"""
📤 <b>Share Your Referral Link!</b>

<b>🔗 Your Link:</b>
<code>{referral_link}</code>

Share with friends and earn points! 🎉

💡 <b>How to share:</b>
1. Copy the link above
2. Send it to friends/groups
3. When they join, you get 1 point!
""",
        call.message.chat.id,
        call.message.message_id,
        parse_mode='HTML',
        reply_markup=markup
    )
    bot.answer_callback_query(call.id)

def copy_referral_link(call):
    user_id = call.from_user.id
    referral_link = get_user_referral_link(user_id)
    bot.answer_callback_query(call.id, f"✅ Link copied: {referral_link}")
    bot.send_message(call.message.chat.id, f"📋 <b>Your Referral Link:</b>\n<code>{referral_link}</code>", parse_mode='HTML')

def show_referral_history(call):
    user_id = call.from_user.id
    ref_stats = get_referral_stats(user_id)
    
    if ref_stats['total'] == 0:
        text = """
📊 <b>No Referrals Yet</b>

Start sharing your referral link to earn points!
Each referral = 1 point ✨
5 points = 1 extra bot slot 🚀
"""
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("📤 Share Now", callback_data="share_referral"))
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="refresh_points"))
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              parse_mode='HTML', reply_markup=markup)
        bot.answer_callback_query(call.id)
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
        username = "Unknown"
        try:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute('SELECT username FROM active_users WHERE user_id = ?', (referred_id,))
            result = c.fetchone()
            if result:
                username = result[0] or str(referred_id)
            conn.close()
        except:
            pass
        text += f"║  {i}. 👤 {username} ({referred_id})\n"
        text += f"║     🕐 {timestamp[:16]}\n"
    
    text += """
╚══════════════════════════════════════╝
"""
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📤 Share More", callback_data="share_referral"))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="refresh_points"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                          parse_mode='HTML', reply_markup=markup)
    bot.answer_callback_query(call.id)

def refresh_points(call):
    user_id = call.from_user.id
    class FakeMessage:
        def __init__(self, call):
            self.chat = call.message.chat
            self.from_user = call.from_user
    show_my_points(FakeMessage(call))
    bot.answer_callback_query(call.id, "🔄 Refreshed!")

# ============================================
# FILE ACTION CALLBACK FUNCTIONS
# ============================================

def show_file_actions(call, file_name):
    user_id = call.from_user.id
    is_running = is_bot_running(user_id, file_name)
    file_type = "py"
    for name, ftype in user_files.get(user_id, []):
        if name == file_name:
            file_type = ftype
            break
    type_icon = "🐍" if file_type == "py" else "🟨" if file_type == "js" else "📄"
    status = "🟢 Running" if is_running else "🔴 Stopped"
    text = f"""
╔══════════════════════════════════════╗
║       📄 <b>{BRAND_NAME}: FILE</b> 📄         ║
╠══════════════════════════════════════╣
║
║  {type_icon} <b>Name:</b> <code>{file_name[:25]}</code>
║  📁 <b>Type:</b> {file_type.upper()}
║  📊 <b>Status:</b> {status}
║
╚══════════════════════════════════════╝
"""
    markup = get_file_actions_keyboard(file_name, is_running)
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              parse_mode='HTML', reply_markup=markup)
    except:
        bot.send_message(call.message.chat.id, text, parse_mode='HTML', reply_markup=markup)
    bot.answer_callback_query(call.id)

def run_user_script(call, file_name):
    user_id = call.from_user.id
    user_folder = get_user_folder(user_id)
    script_path = os.path.join(user_folder, file_name)
    if not os.path.exists(script_path):
        bot.answer_callback_query(call.id, "❌ File not found!")
        return
    if is_bot_running(user_id, file_name):
        bot.answer_callback_query(call.id, "⚠️ Already running!")
        return
    bot.answer_callback_query(call.id, "🚀 Starting...")
    if file_name.endswith('.py'):
        threading.Thread(target=run_script,
                         args=(script_path, user_id, user_folder, file_name, call.message)).start()
    elif file_name.endswith('.js'):
        threading.Thread(target=run_js_script,
                         args=(script_path, user_id, user_folder, file_name, call.message)).start()
    else:
        bot.send_message(call.message.chat.id, "❌ Unsupported type!")

def stop_user_script(call, file_name):
    user_id = call.from_user.id
    script_key = f"{user_id}_{file_name}"
    if script_key not in bot_scripts:
        bot.answer_callback_query(call.id, "❌ Script not running!")
        return
    bot.answer_callback_query(call.id, "🛑 Stopping...")
    stop_text = f"""
╔══════════════════════════════════════╗
║       🛑 <b>{BRAND_NAME}: STOPPING</b> 🛑     ║
╠══════════════════════════════════════╣
║
║  📄 <code>{file_name[:25]}</code>
║  ⏳ Please wait...
║
╚══════════════════════════════════════╝
"""
    try:
        bot.edit_message_text(stop_text, call.message.chat.id, call.message.message_id, parse_mode='HTML')
    except:
        pass
    script_info = bot_scripts.get(script_key)
    if script_info:
        kill_process_tree(script_info)
        cleanup_script(script_key)
        time.sleep(1)
        success_text = f"""
╔══════════════════════════════════════╗
║       ✅ <b>{BRAND_NAME}: STOPPED</b> ✅      ║
╠══════════════════════════════════════╣
║
║  📄 <code>{file_name[:25]}</code>
║  ✅ Successfully stopped!
║
╚══════════════════════════════════════╝
"""
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("▶️ Run Again", callback_data=f"run_{file_name}"),
            types.InlineKeyboardButton("🔙 Back", callback_data="back_to_files")
        )
        try:
            bot.edit_message_text(success_text, call.message.chat.id, call.message.message_id,
                                  parse_mode='HTML', reply_markup=markup)
        except:
            bot.send_message(call.message.chat.id, success_text, parse_mode='HTML', reply_markup=markup)
        log_action(user_id, "SCRIPT_STOP", f"Stopped {file_name}")

def delete_user_file(call, file_name):
    user_id = call.from_user.id
    if is_bot_running(user_id, file_name):
        bot.answer_callback_query(call.id, "⚠️ Stop the script first!")
        return
    confirm_text = f"""
╔══════════════════════════════════════╗
║      ⚠️ <b>{BRAND_NAME}: DELETE?</b> ⚠️      ║
╠══════════════════════════════════════╣
║
║  Are you sure?
║  📄 <code>{file_name[:25]}</code>
║
║  ⚠️ This cannot be undone!
║
╚══════════════════════════════════════╝
"""
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ Yes, Delete", callback_data=f"confirm_delete_{file_name}"),
        types.InlineKeyboardButton("❌ No", callback_data=f"cancel_delete_{file_name}")
    )
    try:
        bot.edit_message_text(confirm_text, call.message.chat.id, call.message.message_id,
                              parse_mode='HTML', reply_markup=markup)
    except:
        pass
    bot.answer_callback_query(call.id)

def confirm_delete_file(call, file_name):
    user_id = call.from_user.id
    user_folder = get_user_folder(user_id)
    file_path = os.path.join(user_folder, file_name)
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            if user_id in user_files:
                user_files[user_id] = [(n, t) for n, t in user_files[user_id] if n != file_name]
            remove_user_file_db(user_id, file_name)
            time.sleep(1)
            success_text = f"""
╔══════════════════════════════════════╗
║       ✅ <b>{BRAND_NAME}: DELETED</b> ✅       ║
╠══════════════════════════════════════╣
║
║  📄 <code>{file_name[:25]}</code>
║  ✅ Successfully deleted!
║
╚══════════════════════════════════════╝
"""
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("📂 Back to Files", callback_data="back_to_files"))
            try:
                bot.edit_message_text(success_text, call.message.chat.id, call.message.message_id,
                                      parse_mode='HTML', reply_markup=markup)
            except:
                bot.send_message(call.message.chat.id, success_text, parse_mode='HTML', reply_markup=markup)
            bot.answer_callback_query(call.id, "✅ Deleted!")
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ Error: {str(e)[:30]}")

def download_user_file(call, file_name):
    user_id = call.from_user.id
    user_folder = get_user_folder(user_id)
    file_path = os.path.join(user_folder, file_name)
    if not os.path.exists(file_path):
        bot.answer_callback_query(call.id, "❌ File not found!")
        return
    bot.answer_callback_query(call.id, "📥 Sending...")
    try:
        with open(file_path, 'rb') as f:
            bot.send_document(call.message.chat.id, f, caption=f"📄 {file_name}")
    except Exception as e:
        bot.send_message(call.message.chat.id, f"❌ Error: {str(e)[:100]}")

def show_script_logs(call, file_name):
    user_id = call.from_user.id
    script_key = f"{user_id}_{file_name}"
    log_path = os.path.join(LOGS_DIR, f"{script_key}.log")
    if not os.path.exists(log_path):
        bot.answer_callback_query(call.id, "📋 No logs")
        return
    try:
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            logs = f.read()[-2000:]
            if not logs.strip():
                logs = "No output yet..."
        log_text = f"""
╔══════════════════════════════════════╗
║       📋 <b>{BRAND_NAME}: LOGS</b> 📋         ║
╠══════════════════════════════════════╣
║ 📄 <code>{file_name[:25]}</code>
╠══════════════════════════════════════╣
<code>{logs[:1500]}</code>
╚══════════════════════════════════════╝
"""
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🔄 Refresh", callback_data=f"logs_{file_name}"),
            types.InlineKeyboardButton("🔙 Back", callback_data=f"file_{file_name}")
        )
        try:
            bot.edit_message_text(log_text, call.message.chat.id, call.message.message_id,
                                  parse_mode='HTML', reply_markup=markup)
        except:
            bot.answer_callback_query(call.id, "📋 Logs unchanged")
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ Error: {str(e)[:30]}")

def restart_user_script(call, file_name):
    user_id = call.from_user.id
    script_key = f"{user_id}_{file_name}"
    if script_key in bot_scripts:
        script_info = bot_scripts.get(script_key)
        if script_info:
            kill_process_tree(script_info)
            cleanup_script(script_key)
            time.sleep(1)
    run_user_script(call, file_name)

def show_user_files_callback(call):
    class FakeMessage:
        def __init__(self, call):
            self.chat = call.message.chat
            self.from_user = call.from_user
    show_user_files(FakeMessage(call))
    bot.answer_callback_query(call.id)

# ============================================
# ADMIN CALLBACK FUNCTIONS
# ============================================

def show_all_user_files_for_admin(call):
    user_id = call.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    
    if not user_files:
        bot.edit_message_text(
            "📂 <b>No files found!</b>\n\nNo user has uploaded any files yet.",
            call.message.chat.id, call.message.message_id, parse_mode='HTML'
        )
        bot.answer_callback_query(call.id)
        return
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    user_list = sorted(user_files.keys())
    
    for uid in user_list[:20]:
        files_count = len(user_files[uid])
        username = "Unknown"
        try:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute('SELECT username FROM active_users WHERE user_id = ?', (uid,))
            result = c.fetchone()
            if result:
                username = result[0] or str(uid)
            conn.close()
        except:
            username = f"User_{uid}"
        
        badge = "👑" if uid == OWNER_ID else "⭐" if uid in admin_ids else "👤"
        markup.add(types.InlineKeyboardButton(
            f"{badge} {username[:12]} ({files_count} files)",
            callback_data=f"admin_user_{uid}"
        ))
    
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="admin_back"))
    
    text = f"""
╔══════════════════════════════════════╗
║    📂 <b>{BRAND_NAME}: ALL USER FILES</b> 📂  ║
╠══════════════════════════════════════╣
║
║  Total Users: {len(user_list)}
║  Total Files: {sum(len(files) for files in user_files.values())}
║
║  Click a user to view files:
║
╚══════════════════════════════════════╝
"""
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              parse_mode='HTML', reply_markup=markup)
    except:
        bot.send_message(call.message.chat.id, text, parse_mode='HTML', reply_markup=markup)
    bot.answer_callback_query(call.id)

def show_admin_user_files(call, target_user):
    user_id = call.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    
    files = user_files.get(target_user, [])
    if not files:
        bot.answer_callback_query(call.id, "📂 No files!")
        return
    
    username = "Unknown"
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT username FROM active_users WHERE user_id = ?', (target_user,))
        result = c.fetchone()
        if result:
            username = result[0] or str(target_user)
        conn.close()
    except:
        username = f"User_{target_user}"
    
    text = f"""
╔══════════════════════════════════════╗
║    📂 <b>{BRAND_NAME}: USER FILES</b> 📂    ║
╠══════════════════════════════════════╣
║
║  👤 <b>User:</b> {username}
║  🆔 <b>ID:</b> {target_user}
║  📁 <b>Files:</b> {len(files)}
║
║  <b>Select a file:</b>
║
╚══════════════════════════════════════╝
"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    for file_name, file_type in files[:20]:
        is_running = is_bot_running(target_user, file_name)
        status = "🟢" if is_running else "🔴"
        type_icon = "🐍" if file_type == "py" else "🟨" if file_type == "js" else "📦"
        markup.add(types.InlineKeyboardButton(
            f"{status} {type_icon} {file_name[:20]}",
            callback_data=f"admin_file_{target_user}_{file_name}"
        ))
    
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="admin_view_all_files"))
    
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              parse_mode='HTML', reply_markup=markup)
    except:
        bot.send_message(call.message.chat.id, text, parse_mode='HTML', reply_markup=markup)
    bot.answer_callback_query(call.id)

def show_admin_file_actions(call, target_user, file_name):
    admin_id = call.from_user.id
    if admin_id != OWNER_ID and admin_id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    
    is_running = is_bot_running(target_user, file_name)
    file_type = "py"
    for name, ftype in user_files.get(target_user, []):
        if name == file_name:
            file_type = ftype
            break
    
    username = "Unknown"
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT username FROM active_users WHERE user_id = ?', (target_user,))
        result = c.fetchone()
        if result:
            username = result[0] or str(target_user)
        conn.close()
    except:
        username = f"User_{target_user}"
    
    type_icon = "🐍" if file_type == "py" else "🟨" if file_type == "js" else "📄"
    status = "🟢 Running" if is_running else "🔴 Stopped"
    
    text = f"""
╔══════════════════════════════════════╗
║   👑 <b>{BRAND_NAME}: FILE MANAGEMENT</b> 👑    ║
╠══════════════════════════════════════╣
║
║  👤 <b>User:</b> {username}
║  🆔 <b>ID:</b> {target_user}
║  📄 <b>File:</b> <code>{file_name[:25]}</code>
║  📁 <b>Type:</b> {file_type.upper()}
║  📊 <b>Status:</b> {status}
║
╚══════════════════════════════════════╝
"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("📥 Download", callback_data=f"admin_download_{target_user}_{file_name}"))
    
    if is_running:
        markup.add(
            types.InlineKeyboardButton("🛑 Stop", callback_data=f"admin_stop_{target_user}_{file_name}"),
            types.InlineKeyboardButton("📋 Logs", callback_data=f"admin_logs_{target_user}_{file_name}")
        )
        markup.add(
            types.InlineKeyboardButton("🔄 Restart", callback_data=f"admin_run_{target_user}_{file_name}")
        )
    else:
        markup.add(
            types.InlineKeyboardButton("▶️ Run", callback_data=f"admin_run_{target_user}_{file_name}"),
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f"admin_delete_{target_user}_{file_name}")
        )
    
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data=f"admin_user_{target_user}"))
    
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              parse_mode='HTML', reply_markup=markup)
    except:
        bot.send_message(call.message.chat.id, text, parse_mode='HTML', reply_markup=markup)
    bot.answer_callback_query(call.id)

def download_user_file_as_admin(call, target_user, file_name):
    admin_id = call.from_user.id
    if admin_id != OWNER_ID and admin_id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    
    user_folder = get_user_folder(target_user)
    file_path = os.path.join(user_folder, file_name)
    
    if not os.path.exists(file_path):
        bot.answer_callback_query(call.id, "❌ File not found!")
        return
    
    bot.answer_callback_query(call.id, "📥 Downloading...")
    
    try:
        with open(file_path, 'rb') as f:
            bot.send_document(
                call.message.chat.id, 
                f,
                caption=f"""
╔══════════════════════════════════════╗
║      📥 <b>{BRAND_NAME}: FILE DOWNLOAD</b> 📥     ║
╠══════════════════════════════════════╣
║
║  👤 <b>User:</b> {target_user}
║  📄 <b>File:</b> <code>{file_name}</code>
║  📦 <b>Size:</b> {format_size(os.path.getsize(file_path))}
║  👑 <b>Downloaded by:</b> {admin_id}
║
╚══════════════════════════════════════╝
""",
                parse_mode='HTML'
            )
        log_action(admin_id, "ADMIN_DOWNLOAD", f"Downloaded {file_name} from user {target_user}")
    except Exception as e:
        bot.send_message(call.message.chat.id, f"❌ Download failed: {str(e)[:100]}")

def run_user_script_as_admin(call, target_user, file_name):
    admin_id = call.from_user.id
    if admin_id != OWNER_ID and admin_id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    
    user_folder = get_user_folder(target_user)
    script_path = os.path.join(user_folder, file_name)
    
    if not os.path.exists(script_path):
        bot.answer_callback_query(call.id, "❌ File not found!")
        return
    
    if is_bot_running(target_user, file_name):
        bot.answer_callback_query(call.id, "⚠️ Already running!")
        return
    
    bot.answer_callback_query(call.id, f"🚀 Running {file_name}...")
    
    bot.send_message(call.message.chat.id, f"""
╔══════════════════════════════════════╗
║      🚀 <b>{BRAND_NAME}: RUNNING SCRIPT</b> 🚀    ║
╠══════════════════════════════════════╣
║
║  👤 User: {target_user}
║  📄 File: <code>{file_name}</code>
║  👑 Admin: {admin_id}
║
╚══════════════════════════════════════╝
""", parse_mode='HTML')
    
    if file_name.endswith('.py'):
        threading.Thread(target=run_script,
                         args=(script_path, target_user, user_folder, file_name, call.message)).start()
    elif file_name.endswith('.js'):
        threading.Thread(target=run_js_script,
                         args=(script_path, target_user, user_folder, file_name, call.message)).start()

def stop_user_script_as_admin(call, target_user, file_name):
    admin_id = call.from_user.id
    if admin_id != OWNER_ID and admin_id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    
    script_key = f"{target_user}_{file_name}"
    if script_key not in bot_scripts:
        bot.answer_callback_query(call.id, "❌ Not running!")
        return
    
    bot.answer_callback_query(call.id, "🛑 Stopping...")
    
    bot.send_message(call.message.chat.id, f"""
╔══════════════════════════════════════╗
║      🛑 <b>{BRAND_NAME}: STOPPING SCRIPT</b> 🛑   ║
╠══════════════════════════════════════╣
║
║  👤 User: {target_user}
║  📄 File: <code>{file_name}</code>
║  👑 Admin: {admin_id}
║
╚══════════════════════════════════════╝
""", parse_mode='HTML')
    
    script_info = bot_scripts.get(script_key)
    if script_info:
        kill_process_tree(script_info)
        cleanup_script(script_key)
        time.sleep(1)
        bot.send_message(call.message.chat.id, f"""
✅ <b>Stopped!</b>
📄 <code>{file_name}</code>
👤 User: {target_user}
""", parse_mode='HTML')

def delete_user_file_as_admin(call, target_user, file_name):
    admin_id = call.from_user.id
    if admin_id != OWNER_ID and admin_id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    
    if is_bot_running(target_user, file_name):
        bot.answer_callback_query(call.id, "⚠️ Stop first!")
        return
    
    user_folder = get_user_folder(target_user)
    file_path = os.path.join(user_folder, file_name)
    
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            if target_user in user_files:
                user_files[target_user] = [(n, t) for n, t in user_files[target_user] if n != file_name]
            remove_user_file_db(target_user, file_name)
            bot.answer_callback_query(call.id, "✅ Deleted!")
            bot.send_message(call.message.chat.id, f"""
✅ <b>File Deleted!</b>
📄 <code>{file_name}</code>
👤 User: {target_user}
👑 Admin: {admin_id}
""", parse_mode='HTML')
            show_admin_user_files(call, target_user)
        else:
            bot.answer_callback_query(call.id, "❌ File not found!")
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ Error: {str(e)[:30]}")

def show_admin_script_logs(call, target_user, file_name):
    admin_id = call.from_user.id
    if admin_id != OWNER_ID and admin_id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    
    script_key = f"{target_user}_{file_name}"
    log_path = os.path.join(LOGS_DIR, f"{script_key}.log")
    
    if not os.path.exists(log_path):
        bot.answer_callback_query(call.id, "📋 No logs")
        return
    
    try:
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            logs = f.read()[-2000:]
            if not logs.strip():
                logs = "No output yet..."
        
        log_text = f"""
╔══════════════════════════════════════╗
║  👑 <b>{BRAND_NAME}: USER SCRIPT LOGS</b> 👑   ║
╠══════════════════════════════════════╣
║ 👤 <b>User:</b> {target_user}
║ 📄 <b>File:</b> <code>{file_name[:25]}</code>
╠══════════════════════════════════════╣
<code>{logs[:1500]}</code>
╚══════════════════════════════════════╝
"""
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🔄 Refresh", callback_data=f"admin_logs_{target_user}_{file_name}"),
            types.InlineKeyboardButton("🔙 Back", callback_data=f"admin_file_{target_user}_{file_name}")
        )
        try:
            bot.edit_message_text(log_text, call.message.chat.id, call.message.message_id,
                                  parse_mode='HTML', reply_markup=markup)
        except:
            bot.answer_callback_query(call.id, "📋 Logs unchanged")
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ Error: {str(e)[:30]}")

def show_top_referrers(call):
    admin_id = call.from_user.id
    if admin_id != OWNER_ID and admin_id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('''SELECT user_id, points, total_referrals 
                     FROM user_points 
                     ORDER BY points DESC 
                     LIMIT 20''')
        top_users = c.fetchall()
        conn.close()
        
        if not top_users:
            bot.answer_callback_query(call.id, "📊 No data!")
            return
        
        text = f"""
╔══════════════════════════════════════╗
║     🏆 <b>{BRAND_NAME} TOP REFERRERS</b> 🏆           ║
╠══════════════════════════════════════╣
"""
        
        medals = ["🥇", "🥈", "🥉"]
        for i, (uid, points, refs) in enumerate(top_users, 1):
            medal = medals[i-1] if i <= 3 else f"{i}."
            
            username = "Unknown"
            try:
                conn2 = get_db_connection()
                c2 = conn2.cursor()
                c2.execute('SELECT username FROM active_users WHERE user_id = ?', (uid,))
                result = c2.fetchone()
                if result:
                    username = result[0] or str(uid)
                conn2.close()
            except:
                pass
            
            text += f"║  {medal} {username[:15]:<15} | ⭐{points} | 👥{refs}\n"
        
        text += "╚══════════════════════════════════════╝"
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="admin_back"))
        
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              parse_mode='HTML', reply_markup=markup)
        bot.answer_callback_query(call.id)
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ {str(e)[:30]}")

def stop_all_bots(call):
    user_id = call.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    bot.answer_callback_query(call.id, f"🛑 Stopping all {BRAND_NAME} bots...")
    stopped = 0
    for script_key in list(bot_scripts.keys()):
        try:
            script_info = bot_scripts[script_key]
            kill_process_tree(script_info)
            cleanup_script(script_key)
            stopped += 1
        except:
            pass
    bot.send_message(call.message.chat.id, f"✅ Stopped {stopped} bots!")

def refresh_admin_panel(call):
    user_id = call.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    class FakeMessage:
        def __init__(self, call):
            self.chat = call.message.chat
            self.from_user = call.from_user
    show_admin_panel(FakeMessage(call))
    bot.answer_callback_query(call.id, "🔄 Refreshed!")

def show_admin_logs(call):
    user_id = call.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
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
                text += f"👤 {log[0]} | {log[1]}\n{log[2][:30]}...\n🕐 {log[3][:16]}\n"
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
    for script_key in list(bot_scripts.keys()):
        try:
            script_info = bot_scripts[script_key]
            kill_process_tree(script_info)
        except:
            pass
    logger.info(f"{BRAND_NAME} Cleanup complete.")

atexit.register(cleanup_on_exit)

# ============================================
# MAIN
# ============================================

def main():
    logger.info("=" * 50)
    logger.info(f"🤖 Starting {BRAND_NAME} {BRAND_EMOJI} Bot...")
    
    # Initialize database
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
