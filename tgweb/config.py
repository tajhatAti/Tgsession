"""TgWeb configuration — এই ফাইলটাই এডিট করতে হবে (credentials + settings).

API_ID / API_HASH: https://my.telegram.org (My APIs থেকে নিন)
PASSWORD:          ওয়েবসাইটটি খুলতে যে পাসওয়ার্ড লাগবে (public URL-এ সুরক্ষা)
SESSION_FILE:      লগইন থাকা StringSession এই ফাইলে সেভ হয়
"""
import hashlib
import logging
import os

# app root = parent of the tgweb/ package
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(APP_DIR, "static")

# ============================================================================
#  EDIT THESE CONSTANTS
# ============================================================================
API_ID = 37109385                                   # https://my.telegram.org
API_HASH = "b50a9ccaf4a0352b895a9fb2998c7f0d"       # https://my.telegram.org
PASSWORD = "Ahad@2026tg"   # site password protecting this app on a public URL
# ============================================================================

SESSION_FILE = "tgsess.txt"
AUTH_COOKIE = "tgw_auth"
AUTH_TTL = 30 * 24 * 3600
AUTH_KEY = hashlib.sha256(b"tgw-auth-v1|" + PASSWORD.encode("utf-8")).digest()
MAX_UPLOAD = 2 * 1024 * 1024 * 1024   # Telegram user accounts: 2 GB per file

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("tgweb")
