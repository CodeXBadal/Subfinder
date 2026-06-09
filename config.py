"""
SubHunter Bot v5.0 — Configuration
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
All sensitive values loaded from environment variables.
Copy .env.example → .env and fill in your values.
"""

import os
import logging
from pathlib import Path

VERSION = "5.0"

# ════════════════════════════════════════════════════════════════
#   B O T  T O K E N
# ════════════════════════════════════════════════════════════════
BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError(
        "\n\n"
        "╔══════════════════════════════════════════════════╗\n"
        "║  FATAL: BOT_TOKEN is not set!                   ║\n"
        "║                                                  ║\n"
        "║  Fix:  export BOT_TOKEN=your_token              ║\n"
        "║  Or:   add BOT_TOKEN=... to your .env file      ║\n"
        "╚══════════════════════════════════════════════════╝\n"
    )

# ════════════════════════════════════════════════════════════════
#   A D M I N  &  C H A N N E L  C O N F I G
# ════════════════════════════════════════════════════════════════
_admin_env = os.environ.get("ADMIN_IDS", "")
ADMIN_IDS: list = [
    int(x.strip()) for x in _admin_env.split(",") if x.strip().isdigit()
]
if not ADMIN_IDS:
    raise RuntimeError(
        "\n\nFATAL: ADMIN_IDS is not set!\n"
        "Example (single):   export ADMIN_IDS=123456789\n"
        "Example (multiple): export ADMIN_IDS=123456789,987654321\n"
        "Get your ID: message @userinfobot on Telegram\n"
    )

LOG_CHANNEL_ID: int      = int(os.environ.get("LOG_CHANNEL_ID", "0"))
UPDATES_CHANNEL_URL: str = os.environ.get("UPDATES_CHANNEL_URL", "https://t.me/yourchannel")
DEVELOPER_USERNAME: str  = os.environ.get("DEVELOPER_USERNAME", "yourhandle")

# ════════════════════════════════════════════════════════════════
#   F O R C E  J O I N  (2 channels)
# ════════════════════════════════════════════════════════════════
# Set these to your channel usernames (with @) or numeric IDs.
# Example: FORCE_JOIN_CHANNEL_1=@mychannel1
# Leave empty to disable force join.
FORCE_JOIN_CHANNEL_1: str = os.environ.get("FORCE_JOIN_CHANNEL_1", "")
FORCE_JOIN_CHANNEL_2: str = os.environ.get("FORCE_JOIN_CHANNEL_2", "")

# Human-readable invite links shown to users (can be different from above if private)
FORCE_JOIN_LINK_1: str = os.environ.get("FORCE_JOIN_LINK_1", "")
FORCE_JOIN_LINK_2: str = os.environ.get("FORCE_JOIN_LINK_2", "")

# Collect non-empty channels into a list for easy iteration
FORCE_JOIN_CHANNELS: list = [
    c for c in [FORCE_JOIN_CHANNEL_1, FORCE_JOIN_CHANNEL_2] if c.strip()
]
FORCE_JOIN_LINKS: list = [
    l for l in [FORCE_JOIN_LINK_1, FORCE_JOIN_LINK_2] if l.strip()
]

# ════════════════════════════════════════════════════════════════
#   O P T I O N A L  A P I  K E Y S
# ════════════════════════════════════════════════════════════════
VIRUSTOTAL_API_KEY: str = os.environ.get("VIRUSTOTAL_API_KEY", "")
# Get a free key at: https://www.virustotal.com/gui/my-apikey

# ════════════════════════════════════════════════════════════════
#   S C A N  E N G I N E  S E T T I N G S
# ════════════════════════════════════════════════════════════════
DOMAIN_WORKERS: int       = int(os.environ.get("DOMAIN_WORKERS", "15"))
CHUNK_SIZE: int           = int(os.environ.get("CHUNK_SIZE", "50"))
SOURCE_TIMEOUT: int       = int(os.environ.get("SOURCE_TIMEOUT", "20"))
MAX_DOMAINS_PER_FILE: int = int(os.environ.get("MAX_DOMAINS_PER_FILE", "500"))
SCAN_COOLDOWN: int        = int(os.environ.get("SCAN_COOLDOWN_SECONDS", "60"))

# Retry settings for rate-limited sources
SOURCE_RETRY_COUNT: int   = int(os.environ.get("SOURCE_RETRY_COUNT", "2"))
SOURCE_RETRY_DELAY: float = float(os.environ.get("SOURCE_RETRY_DELAY", "3.0"))

# ════════════════════════════════════════════════════════════════
#   S T O R A G E  P A T H S
# ════════════════════════════════════════════════════════════════
DATA_DIR: Path = Path(os.environ.get("DATA_DIR", "/app/data"))
RESUME_DIR: Path = DATA_DIR / "resume_data"
USERS_FILE: Path = DATA_DIR / "users.json"
LOG_FILE: str    = os.environ.get("LOG_FILE", str(DATA_DIR / "subhunter.log"))

# Create directories at import time
DATA_DIR.mkdir(parents=True, exist_ok=True)
RESUME_DIR.mkdir(parents=True, exist_ok=True)

# ════════════════════════════════════════════════════════════════
#   L O G G I N G
# ════════════════════════════════════════════════════════════════
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO").upper()

# ════════════════════════════════════════════════════════════════
#   H E A L T H  S E R V E R
# ════════════════════════════════════════════════════════════════
HEALTH_PORT: int           = int(os.environ.get("HEALTH_PORT", "8080"))
ENABLE_HEALTH_SERVER: bool = (
    os.environ.get("ENABLE_HEALTH_SERVER", "true").lower() == "true"
)

# ════════════════════════════════════════════════════════════════
#   C O N V E R S A T I O N  S T A T E S
# ════════════════════════════════════════════════════════════════
CHOOSING_MODE     = 0
WAITING_DOMAIN    = 1
WAITING_FILE      = 2
ADMIN_BROADCAST   = 3
ADMIN_BAN_INPUT   = 4
ADMIN_UNBAN_INPUT = 5
