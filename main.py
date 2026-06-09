"""
SubHunter Bot v5.0 — Entry Point
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Startup, handler registration, health server, graceful shutdown.

Fixes in v5.0:
  - post_shutdown registered via .builder() not direct assignment
  - CallbackQueryHandler group fixed (inline buttons work correctly)
  - Force join callback registered globally
  - PTB 22.7 compatible

Run:
  python main.py
"""

import asyncio
import logging
import signal
import sys
import os
from threading import Thread

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import config

# ── Logging setup ─────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
    ],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)

log = logging.getLogger("SubHunter.Main")

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)

from config import (
    BOT_TOKEN, VERSION,
    CHOOSING_MODE, WAITING_DOMAIN, WAITING_FILE,
    ADMIN_BROADCAST, ADMIN_BAN_INPUT, ADMIN_UNBAN_INPUT,
    ENABLE_HEALTH_SERVER, HEALTH_PORT,
)

from handlers import (
    cmd_start, cmd_help, cmd_about, cmd_status, cmd_cancel,
    cmd_scan, cmd_file, cmd_resume,
    handle_domain_input, handle_file_input,
    handle_text, button_handler,
)
from admin import (
    cmd_admin, admin_callback,
    handle_broadcast_input, handle_ban_input, handle_unban_input,
)
from scanner import close_session


# ════════════════════════════════════════════════════════════════
#   H E A L T H  S E R V E R
# ════════════════════════════════════════════════════════════════

def start_health_server() -> None:
    if not ENABLE_HEALTH_SERVER:
        return

    from http.server import HTTPServer, BaseHTTPRequestHandler

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"status":"ok","bot":"SubHunter","version":"' + VERSION.encode() + b'"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass

    def _serve():
        try:
            server = HTTPServer(("0.0.0.0", HEALTH_PORT), HealthHandler)
            log.info(f"[Health] Server listening on :{HEALTH_PORT}/health")
            server.serve_forever()
        except OSError as e:
            log.warning(f"[Health] Could not start health server: {e}")

    t = Thread(target=_serve, daemon=True, name="health-server")
    t.start()


# ════════════════════════════════════════════════════════════════
#   H A N D L E R  R E G I S T R A T I O N
# ════════════════════════════════════════════════════════════════

def build_application() -> Application:
    # FIX: post_shutdown registered via builder, NOT direct assignment
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_shutdown(post_shutdown)
        .build()
    )

    # ── ConversationHandler (group 0) ────────────────────────
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start",  cmd_start),
            CommandHandler("scan",   cmd_scan),
            CommandHandler("file",   cmd_file),
            CommandHandler("resume", cmd_resume),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
        ],
        states={
            CHOOSING_MODE: [
                CommandHandler("scan",   cmd_scan),
                CommandHandler("file",   cmd_file),
                CommandHandler("resume", cmd_resume),
                CommandHandler("status", cmd_status),
                CommandHandler("help",   cmd_help),
                CommandHandler("about",  cmd_about),
                CommandHandler("admin",  cmd_admin),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
            ],
            WAITING_DOMAIN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_domain_input),
                CommandHandler("cancel", cmd_cancel),
            ],
            WAITING_FILE: [
                MessageHandler(filters.Document.ALL, handle_file_input),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_file_input),
                CommandHandler("cancel", cmd_cancel),
            ],
            ADMIN_BROADCAST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast_input),
                CommandHandler("cancel", cmd_cancel),
            ],
            ADMIN_BAN_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ban_input),
                CommandHandler("cancel", cmd_cancel),
            ],
            ADMIN_UNBAN_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_unban_input),
                CommandHandler("cancel", cmd_cancel),
            ],
        },
        fallbacks=[
            CommandHandler("start",  cmd_start),
            CommandHandler("cancel", cmd_cancel),
            CommandHandler("status", cmd_status),
            CommandHandler("help",   cmd_help),
            CommandHandler("about",  cmd_about),
            CommandHandler("admin",  cmd_admin),
        ],
        allow_reentry=True,
        name="main_conv",
        persistent=False,
    )

    app.add_handler(conv, group=0)

    # ── FIX: Global CallbackQueryHandler in group 1 ──────────
    # Handles ALL inline button presses (force_join_check, adm_* buttons).
    # group=1 means it runs after ConversationHandler but still processes
    # callback_query updates correctly.
    # The button_handler function correctly returns CHOOSING_MODE which
    # is informational only outside a conversation — no state breakage.
    app.add_handler(CallbackQueryHandler(button_handler), group=1)

    # ── Standalone commands outside conversation ─────────────
    app.add_handler(CommandHandler("help",  cmd_help),  group=2)
    app.add_handler(CommandHandler("about", cmd_about), group=2)
    app.add_handler(CommandHandler("admin", cmd_admin), group=2)

    return app


# ════════════════════════════════════════════════════════════════
#   S H U T D O W N  H O O K S
# ════════════════════════════════════════════════════════════════

async def post_shutdown(app: Application) -> None:
    """Clean up shared resources on shutdown."""
    await close_session()
    log.info("[Main] Shared aiohttp session closed.")


# ════════════════════════════════════════════════════════════════
#   M A I N
# ════════════════════════════════════════════════════════════════

def main() -> None:
    log.info(f"╔══════════════════════════════════════╗")
    log.info(f"║  SubHunter Bot v{VERSION:<21} ║")
    log.info(f"║  Data dir: {str(config.DATA_DIR):<25} ║")
    log.info(f"║  Admin IDs: {str(config.ADMIN_IDS):<24} ║")
    if config.FORCE_JOIN_CHANNELS:
        log.info(f"║  Force Join: {str(config.FORCE_JOIN_CHANNELS):<22} ║")
    log.info(f"╚══════════════════════════════════════╝")

    start_health_server()

    app = build_application()

    def handle_sigterm(signum, frame):
        log.info("[Main] SIGTERM received — shutting down gracefully…")
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)

    log.info("[Main] Starting polling…")
    app.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
        close_loop=False,
    )


if __name__ == "__main__":
    main()
