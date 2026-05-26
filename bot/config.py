"""
bot/config.py — All configuration loaded from environment variables / .env file.

Create a .env file in the project root (never commit it to GitHub):

    BOT_TOKEN=your_bot_token_here
    ADMIN_IDS=123456789
    LOG_CHANNEL_ID=-1001234567890
    UPDATES_CHANNEL_URL=https://t.me/yourchannel
    DEVELOPER_USERNAME=yourusername
"""

import os
from pathlib import Path

# Load .env file if python-dotenv is installed
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _required(key: str) -> str:
    val = os.environ.get(key, "").strip().strip('"').strip("'")
    if not val:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return val


def _optional(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip().strip('"').strip("'")


# ════════════════════════════════════════════════════════
#  REQUIRED
# ════════════════════════════════════════════════════════
BOT_TOKEN      = _required("BOT_TOKEN")
LOG_CHANNEL_ID = int(_required("LOG_CHANNEL_ID"))
ADMIN_IDS: list = [
    int(x.strip().strip('"').strip("'"))
    for x in _required("ADMIN_IDS").split(",")
    if x.strip().strip('"').strip("'")
]

# ════════════════════════════════════════════════════════
#  OPTIONAL (with safe defaults)
# ════════════════════════════════════════════════════════
UPDATES_CHANNEL_URL = _optional("UPDATES_CHANNEL_URL", "https://t.me/updates")
DEVELOPER_USERNAME  = _optional("DEVELOPER_USERNAME",  "developer")

# ════════════════════════════════════════════════════════
#  SCAN SETTINGS
# ════════════════════════════════════════════════════════
DOMAIN_WORKERS = int(_optional("DOMAIN_WORKERS", "15"))
CHUNK_SIZE     = int(_optional("CHUNK_SIZE",     "50"))
SOURCE_TIMEOUT = int(_optional("SOURCE_TIMEOUT", "20"))

# ════════════════════════════════════════════════════════
#  PATHS
# ════════════════════════════════════════════════════════
RESUME_DIR = Path(_optional("RESUME_DIR", "/tmp/resume_data"))
USERS_FILE = Path(_optional("USERS_FILE", "/tmp/users.json"))
LOG_FILE   = Path(_optional("LOG_FILE",   "/tmp/subhunter.log"))

RESUME_DIR.mkdir(parents=True, exist_ok=True)

# ════════════════════════════════════════════════════════
#  CONVERSATION STATES
# ════════════════════════════════════════════════════════
CHOOSING_MODE     = 0
WAITING_DOMAIN    = 1
WAITING_FILE      = 2
ADMIN_BROADCAST   = 3
ADMIN_BAN_INPUT   = 4
ADMIN_UNBAN_INPUT = 5

# ════════════════════════════════════════════════════════
#  GLOBAL SHARED STATE
# ════════════════════════════════════════════════════════
ACTIVE_SCANS: dict     = {}
LAST_FINAL_FILES: dict = {}
