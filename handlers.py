"""
SubHunter Bot v5.0 — User-Facing Handlers
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
/start, /scan, /file, /resume, /status, /cancel, /help, /about.

Fixes in v5.0:
  - Force join system for 2 channels (check before EVERY action)
  - /resume now also checks rate limiter
  - start_ts closure bug fixed (passed as param)
  - Force join buttons use PTB 22.7 style= colors
"""

import asyncio
import time
import logging
from io import BytesIO

from telegram import (
    Update, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler
from telegram.error import TelegramError

import config
from config import (
    CHOOSING_MODE, WAITING_DOMAIN, WAITING_FILE,
    VERSION, LOG_CHANNEL_ID, UPDATES_CHANNEL_URL, DEVELOPER_USERNAME,
    FORCE_JOIN_CHANNELS, FORCE_JOIN_LINKS,
)
from db import db
from scanner import (
    scan_domain, run_file_scan, rate_limiter,
    find_user_resumes, load_resume, SOURCE_COUNT,
)
from utils import (
    is_valid_domain, clean_domain, scan_id_for,
    progress_bar, build_single_content, build_file_content,
    make_bytes, create_task,
)
from admin import cmd_admin, admin_callback

log = logging.getLogger("SubHunter.Handlers")

ACTIVE_SCANS: set = set()
SCAN_LOCK          = asyncio.Lock()


# ════════════════════════════════════════════════════════════════
#   H E L P E R S
# ════════════════════════════════════════════════════════════════

def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["🔍 Scan Domain", "📂 Scan File"], ["📋 Status", "ℹ️ Help"]],
        resize_keyboard=True,
    )


def join_keyboard() -> InlineKeyboardMarkup:
    """
    Force join inline keyboard — one button per channel + a check button.
    PTB 22.7: style='primary' (blue) for join, style='success' (green) for check.
    """
    rows = []
    for i, (ch, link) in enumerate(zip(FORCE_JOIN_CHANNELS, FORCE_JOIN_LINKS), 1):
        label = link if link else ch
        rows.append([
            InlineKeyboardButton(
                f"📢 Join Channel {i}",
                url=label,
                style="primary",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            "✅ I've Joined — Check",
            callback_data="force_join_check",
            style="success",
        )
    ])
    return InlineKeyboardMarkup(rows)


async def check_force_join(bot, user_id: int) -> bool:
    """
    Returns True if user has joined ALL required channels.
    Returns False if any channel membership check fails.
    Skips check if no channels configured.
    """
    if not FORCE_JOIN_CHANNELS:
        return True

    for channel in FORCE_JOIN_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status in ("left", "kicked", "banned"):
                return False
        except TelegramError as e:
            log.warning(f"[ForceJoin] Could not check {channel} for user {user_id}: {e}")
            # If we can't check (bot not in channel), skip this channel
            continue
    return True


async def send_join_prompt(update: Update) -> None:
    """Send the force-join message with channel buttons."""
    ch_lines = ""
    for i, (ch, link) in enumerate(zip(FORCE_JOIN_CHANNELS, FORCE_JOIN_LINKS), 1):
        label = link if link else ch
        ch_lines += f"  {i}. {label}\n"

    await update.effective_message.reply_text(
        f"⚠️ <b>Join Required</b>\n\n"
        f"You must join our channels to use this bot:\n\n"
        f"{ch_lines}\n"
        f"After joining, tap the <b>✅ I've Joined — Check</b> button below.",
        parse_mode=ParseMode.HTML,
        reply_markup=join_keyboard(),
    )


async def notify_new_user(bot, user: dict) -> None:
    if not LOG_CHANNEL_ID:
        return
    try:
        name  = user.get("first_name", "?")
        uname = f"@{user['username']}" if user.get("username") else "no username"
        uid   = user.get("user_id", "?")
        text  = (
            f"👤 <b>New User</b>\n"
            f"Name: {name} ({uname})\n"
            f"ID: <code>{uid}</code>\n"
            f"Total users: {db.total_count()}"
        )
        await bot.send_message(LOG_CHANNEL_ID, text, parse_mode=ParseMode.HTML)
    except Exception as e:
        log.debug(f"[notify_new_user] Failed: {e}")


async def log_scan_to_channel(
    bot, user: dict, domains: list, all_subs: set, elapsed: float, filename: str
) -> None:
    if not LOG_CHANNEL_ID:
        return
    try:
        name  = user.get("first_name", "?")
        uname = f"@{user['username']}" if user.get("username") else "no username"
        uid   = user.get("user_id", "?")
        text  = (
            f"📊 <b>Scan Completed</b>\n"
            f"User   : {name} ({uname}) — <code>{uid}</code>\n"
            f"Domains: {len(domains)}\n"
            f"Found  : {len(all_subs)} subdomains\n"
            f"File   : {filename}\n"
            f"Time   : {elapsed}s"
        )
        await bot.send_message(LOG_CHANNEL_ID, text, parse_mode=ParseMode.HTML)
    except Exception as e:
        log.debug(f"[log_scan_to_channel] Failed: {e}")


# ════════════════════════════════════════════════════════════════
#   F O R C E  J O I N  C A L L B A C K
# ════════════════════════════════════════════════════════════════

async def force_join_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the 'I've Joined — Check' button press."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    joined  = await check_force_join(ctx.bot, user_id)

    if joined:
        await query.edit_message_text(
            "✅ <b>Verified!</b> You can now use the bot.\n\n"
            "Send /start to begin.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await query.answer(
            "❌ You haven't joined all channels yet. Please join and try again.",
            show_alert=True,
        )
    return CHOOSING_MODE


# ════════════════════════════════════════════════════════════════
#   /start
# ════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user   = update.effective_user
    is_new = db.register(user)
    user_d = db.get(user.id)

    if db.is_banned(user.id):
        await update.message.reply_text(
            "🚫 You are banned from using this bot.", reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    # Force join check
    if not await check_force_join(ctx.bot, user.id):
        await send_join_prompt(update)
        return CHOOSING_MODE

    greeting = "👋 Welcome back" if not is_new else "👋 Welcome"
    text = (
        f"{greeting}, <b>{user.first_name}</b>!\n\n"
        f"🔍 <b>SubHunter v{VERSION}</b> — OSINT Subdomain Finder\n\n"
        f"I scan <b>{SOURCE_COUNT} OSINT sources</b> simultaneously to find subdomains:\n"
        f"• Single domain scan — instant results\n"
        f"• Bulk file scan — up to {config.MAX_DOMAINS_PER_FILE} domains with progress tracking\n"
        f"• Resume interrupted scans automatically\n\n"
        f"📢 Updates: {UPDATES_CHANNEL_URL}\n\n"
        f"<i>⚠️ Privacy Notice: Scan metadata (domain count, result count) "
        f"is logged for abuse monitoring. Your results are sent only to you.</i>"
    )
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard()
    )

    if is_new:
        create_task(notify_new_user(ctx.bot, user_d), name="notify_new_user")

    return CHOOSING_MODE


# ════════════════════════════════════════════════════════════════
#   /help
# ════════════════════════════════════════════════════════════════

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_force_join(ctx.bot, update.effective_user.id):
        await send_join_prompt(update)
        return CHOOSING_MODE

    text = (
        f"📖 <b>SubHunter v{VERSION} — Help</b>\n\n"
        "<b>Commands:</b>\n"
        "/start  — Show main menu\n"
        "/scan   — Scan a single domain\n"
        "/file   — Upload a .txt file for bulk scan\n"
        "/resume — Resume an interrupted file scan\n"
        "/status — Check if a scan is running\n"
        "/cancel — Cancel current scan or action\n\n"
        "<b>How to use:</b>\n"
        "1. Tap <b>🔍 Scan Domain</b> and send a domain (e.g. example.com)\n"
        "2. For bulk scans, tap <b>📂 Scan File</b> and upload a .txt file "
        f"with one domain per line (max {config.MAX_DOMAINS_PER_FILE})\n"
        "3. If a scan is interrupted, use /resume to pick up where you left off\n\n"
        f"📢 Updates: {UPDATES_CHANNEL_URL}\n"
        f"👨‍💻 Developer: @{DEVELOPER_USERNAME}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    return CHOOSING_MODE


# ════════════════════════════════════════════════════════════════
#   /about
# ════════════════════════════════════════════════════════════════

async def cmd_about(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_force_join(ctx.bot, update.effective_user.id):
        await send_join_prompt(update)
        return CHOOSING_MODE

    text = (
        f"🤖 <b>SubHunter v{VERSION}</b>\n\n"
        f"An OSINT subdomain enumeration bot powered by <b>{SOURCE_COUNT} API sources</b>.\n\n"
        "<b>Sources include:</b>\n"
        "crt.sh • HackerTarget • AlienVault • URLScan\n"
        "Anubis • CertSpotter • JLDC • RapidDNS\n"
        "Columbus • LeakIX • Wayback Machine\n"
        "ShrewdEye • VirusTotal*\n\n"
        "<i>*VirusTotal requires an API key (free at virustotal.com)</i>\n\n"
        f"👨‍💻 Developer: @{DEVELOPER_USERNAME}\n"
        f"📢 Channel: {UPDATES_CHANNEL_URL}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    return CHOOSING_MODE


# ════════════════════════════════════════════════════════════════
#   /status
# ════════════════════════════════════════════════════════════════

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    async with SCAN_LOCK:
        running = chat_id in ACTIVE_SCANS

    if running:
        await update.message.reply_text(
            "⏳ A scan is currently running for this chat.\n"
            "Use /cancel to stop it."
        )
    else:
        await update.message.reply_text(
            "✅ No active scan. Ready to scan!\n"
            "Use /scan or tap <b>🔍 Scan Domain</b>.",
            parse_mode=ParseMode.HTML,
        )
    return CHOOSING_MODE


# ════════════════════════════════════════════════════════════════
#   /cancel
# ════════════════════════════════════════════════════════════════

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    async with SCAN_LOCK:
        was_running = chat_id in ACTIVE_SCANS
        ACTIVE_SCANS.discard(chat_id)

    ctx.user_data.clear()
    if was_running:
        await update.message.reply_text(
            "🛑 Scan cancelled.\n"
            "Resume it anytime with /resume.",
            reply_markup=main_keyboard(),
        )
    else:
        await update.message.reply_text(
            "✅ Cancelled.", reply_markup=main_keyboard()
        )
    return ConversationHandler.END


# ════════════════════════════════════════════════════════════════
#   /scan
# ════════════════════════════════════════════════════════════════

async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user

    if db.is_banned(user.id):
        await update.message.reply_text("🚫 You are banned.")
        return ConversationHandler.END

    if not await check_force_join(ctx.bot, user.id):
        await send_join_prompt(update)
        return CHOOSING_MODE

    await update.message.reply_text(
        "🔍 <b>Single Domain Scan</b>\n\n"
        "Send a domain to scan (e.g. <code>example.com</code>)\n"
        "Use /cancel to go back.",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardMarkup(
            [["❌ Cancel"]], resize_keyboard=True
        ),
    )
    return WAITING_DOMAIN


# ════════════════════════════════════════════════════════════════
#   Handle domain text input
# ════════════════════════════════════════════════════════════════

async def handle_domain_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    text    = update.message.text.strip()

    if text == "❌ Cancel" or text.startswith("/cancel"):
        return await cmd_cancel(update, ctx)

    if db.is_banned(user.id):
        await update.message.reply_text("🚫 You are banned.")
        return ConversationHandler.END

    if not await check_force_join(ctx.bot, user.id):
        await send_join_prompt(update)
        return CHOOSING_MODE

    allowed, wait = await rate_limiter.check(user.id)
    if not allowed:
        await update.message.reply_text(
            f"⏳ Please wait <b>{wait}s</b> before starting another scan.",
            parse_mode=ParseMode.HTML,
        )
        return WAITING_DOMAIN

    domain = is_valid_domain(text)
    if not domain:
        await update.message.reply_text(
            "❌ Invalid domain. Please send a valid domain like <code>example.com</code>.",
            parse_mode=ParseMode.HTML,
        )
        return WAITING_DOMAIN

    async with SCAN_LOCK:
        if chat_id in ACTIVE_SCANS:
            await update.message.reply_text(
                "⚠️ A scan is already running. Use /cancel to stop it first."
            )
            return WAITING_DOMAIN
        ACTIVE_SCANS.add(chat_id)

    status = await update.message.reply_text(
        f"🔍 Scanning <code>{domain}</code> across {SOURCE_COUNT} sources…",
        parse_mode=ParseMode.HTML,
    )
    start_t = time.time()

    try:
        subs = await scan_domain(domain)
    except Exception as e:
        log.error(f"[scan_domain] Error for {domain}: {e}")
        subs = set()
    finally:
        async with SCAN_LOCK:
            ACTIVE_SCANS.discard(chat_id)

    elapsed = round(time.time() - start_t, 1)
    count   = len(subs)

    db.increment_scans(user.id)

    result_text = (
        f"✅ <b>Scan Complete</b>\n\n"
        f"🎯 Domain  : <code>{domain}</code>\n"
        f"🔍 Sources : {SOURCE_COUNT}\n"
        f"📂 Found   : <b>{count}</b> subdomains\n"
        f"⏱️ Time    : {elapsed}s"
    )

    if count > 0:
        content  = build_single_content(domain, subs, elapsed, SOURCE_COUNT)
        filename = f"{domain}_subdomains.txt"
        await status.edit_text(result_text, parse_mode=ParseMode.HTML)
        await update.message.reply_document(
            document=make_bytes(content, filename),
            caption=f"📋 {count} subdomains found for <code>{domain}</code>",
            parse_mode=ParseMode.HTML,
            filename=filename,
        )
    else:
        await status.edit_text(
            result_text + "\n\n<i>No subdomains found.</i>",
            parse_mode=ParseMode.HTML,
        )

    user_d = db.get(user.id)
    create_task(
        log_scan_to_channel(ctx.bot, user_d or {}, [domain], subs, elapsed, f"{domain}_subdomains.txt"),
        name="log_scan_meta"
    )

    await update.message.reply_text(
        "✅ Done! Send another domain or /cancel.", reply_markup=main_keyboard()
    )
    return CHOOSING_MODE


# ════════════════════════════════════════════════════════════════
#   /file
# ════════════════════════════════════════════════════════════════

async def cmd_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user

    if db.is_banned(user.id):
        await update.message.reply_text("🚫 You are banned.")
        return ConversationHandler.END

    if not await check_force_join(ctx.bot, user.id):
        await send_join_prompt(update)
        return CHOOSING_MODE

    await update.message.reply_text(
        f"📂 <b>Bulk File Scan</b>\n\n"
        f"Upload a <code>.txt</code> file with one domain per line.\n"
        f"Maximum: <b>{config.MAX_DOMAINS_PER_FILE} domains</b> per file.\n\n"
        "Use /cancel to go back.",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardMarkup([["❌ Cancel"]], resize_keyboard=True),
    )
    return WAITING_FILE


# ════════════════════════════════════════════════════════════════
#   Handle file upload
# ════════════════════════════════════════════════════════════════

async def handle_file_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user    = update.effective_user
    chat_id = update.effective_chat.id

    if update.message.text and (
        update.message.text.strip() == "❌ Cancel"
        or update.message.text.strip().startswith("/cancel")
    ):
        return await cmd_cancel(update, ctx)

    if not update.message.document:
        await update.message.reply_text("📂 Please upload a .txt file.")
        return WAITING_FILE

    if db.is_banned(user.id):
        await update.message.reply_text("🚫 You are banned.")
        return ConversationHandler.END

    if not await check_force_join(ctx.bot, user.id):
        await send_join_prompt(update)
        return CHOOSING_MODE

    allowed, wait = await rate_limiter.check(user.id)
    if not allowed:
        await update.message.reply_text(
            f"⏳ Please wait <b>{wait}s</b> before starting another scan.",
            parse_mode=ParseMode.HTML,
        )
        return WAITING_FILE

    doc = update.message.document
    if not doc.file_name.endswith(".txt"):
        await update.message.reply_text("❌ Only .txt files are supported.")
        return WAITING_FILE

    if doc.file_size and doc.file_size > 10 * 1024 * 1024:
        await update.message.reply_text("❌ File too large. Maximum size is 10MB.")
        return WAITING_FILE

    status = await update.message.reply_text("📥 Downloading and parsing file…")

    try:
        tg_file   = await doc.get_file()
        raw_bytes = await tg_file.download_as_bytearray()
        content   = raw_bytes.decode("utf-8", errors="ignore")
    except Exception as e:
        await status.edit_text(f"❌ Failed to download file: {e}")
        return WAITING_FILE

    seen: set         = set()
    raw_domains: list = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        d = is_valid_domain(line)
        if d and d not in seen:
            seen.add(d)
            raw_domains.append(d)

    if not raw_domains:
        await status.edit_text("❌ No valid domains found in the file.")
        return WAITING_FILE

    if len(raw_domains) > config.MAX_DOMAINS_PER_FILE:
        await status.edit_text(
            f"❌ File contains <b>{len(raw_domains)}</b> domains.\n"
            f"Maximum allowed: <b>{config.MAX_DOMAINS_PER_FILE}</b>.\n\n"
            f"Please split the file and try again.",
            parse_mode=ParseMode.HTML,
        )
        return WAITING_FILE

    async with SCAN_LOCK:
        if chat_id in ACTIVE_SCANS:
            await status.edit_text(
                "⚠️ A scan is already running. Use /cancel to stop it first."
            )
            return WAITING_FILE
        ACTIVE_SCANS.add(chat_id)

    basename     = doc.file_name.replace(".txt", "")
    total        = len(raw_domains)
    total_chunks = (total + config.CHUNK_SIZE - 1) // config.CHUNK_SIZE

    await status.edit_text(
        f"✅ File loaded: <b>{total}</b> domains in <b>{total_chunks}</b> chunks.\n"
        f"🔍 Starting scan…",
        parse_mode=ParseMode.HTML,
    )

    create_task(
        _run_bg_file_scan(ctx.bot, chat_id, user.id, status.message_id, raw_domains, basename),
        name=f"file_scan_{chat_id}"
    )

    await update.message.reply_text(
        "⏳ Scanning in background. Results will be sent as each chunk completes.\n"
        "Use /status to check progress or /cancel to stop.",
        reply_markup=main_keyboard(),
    )
    return CHOOSING_MODE


# ════════════════════════════════════════════════════════════════
#   Background file scan
# ════════════════════════════════════════════════════════════════

async def _run_bg_file_scan(
    bot, chat_id: int, user_id: int,
    status_msg_id: int, domains: list, basename: str,
    start_chunk: int = 0, prev_subs: set = None,
) -> None:
    """FIX: start_ts passed as param to on_all_done to avoid closure bug."""

    last_edit_ts = 0.0

    async def safe_edit(text: str) -> None:
        nonlocal last_edit_ts
        now = time.time()
        if now - last_edit_ts < 3.0:
            return
        last_edit_ts = now
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text=text,
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    total        = len(domains)
    total_chunks = (total + config.CHUNK_SIZE - 1) // config.CHUNK_SIZE
    start_ts     = time.time()  # FIX: defined before closures capture it

    async def on_progress(done, total_d, domain, count, ci, cn):
        bar = progress_bar(done, total_d)
        await safe_edit(
            f"🔍 <b>Scanning</b> — Chunk {cn}/{total_chunks}\n"
            f"{bar}\n"
            f"📌 Last: <code>{domain}</code> → {count} found"
        )

    async def on_chunk_done(cn, total_cn, chunk_subs, content, base):
        if chunk_subs:
            filename = f"{base}_chunk{cn}of{total_cn}.txt"
            await bot.send_document(
                chat_id=chat_id,
                document=make_bytes(content, filename),
                caption=(
                    f"📋 Chunk {cn}/{total_cn} complete\n"
                    f"Found: <b>{len(chunk_subs)}</b> subdomains"
                ),
                parse_mode=ParseMode.HTML,
                filename=filename,
            )

    # FIX: start_ts is captured correctly via nonlocal (defined above)
    async def on_all_done(all_subs, all_domains, base):
        async with SCAN_LOCK:
            ACTIVE_SCANS.discard(chat_id)

        elapsed = round(time.time() - start_ts, 1)

        if all_subs:
            content  = build_file_content(all_domains, all_subs, elapsed, base, SOURCE_COUNT)
            filename = f"{base}_MERGED.txt"
            await bot.send_document(
                chat_id=chat_id,
                document=make_bytes(content, filename),
                caption=(
                    f"✅ <b>All {len(all_domains)} domains scanned!</b>\n"
                    f"🔍 Total found : <b>{len(all_subs)}</b> unique subdomains\n"
                    f"⏱️ Total time   : {elapsed}s"
                ),
                parse_mode=ParseMode.HTML,
                filename=filename,
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"✅ Scan complete. <b>No subdomains found</b> "
                    f"across {len(all_domains)} domains ({elapsed}s)."
                ),
                parse_mode=ParseMode.HTML,
            )

        user_d = db.get(user_id)
        create_task(
            log_scan_to_channel(bot, user_d or {}, all_domains, all_subs, elapsed, f"{base}_MERGED.txt"),
            name="log_file_scan_meta"
        )

    try:
        await run_file_scan(
            bot=bot,
            chat_id=chat_id,
            user_id=user_id,
            status_msg_id=status_msg_id,
            domains=domains,
            basename=basename,
            on_progress=on_progress,
            on_chunk_done=on_chunk_done,
            on_all_done=on_all_done,
            start_chunk=start_chunk,
            prev_subs=prev_subs,
        )
    except Exception as e:
        log.error(f"[BGFileScan] Error for chat {chat_id}: {e}", exc_info=True)
        async with SCAN_LOCK:
            ACTIVE_SCANS.discard(chat_id)
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"❌ Scan failed unexpectedly: {e}\nResume with /resume.",
            )
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════
#   /resume — FIX: rate limiter check added
# ════════════════════════════════════════════════════════════════

async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    user    = update.effective_user

    if db.is_banned(user.id):
        await update.message.reply_text("🚫 You are banned.")
        return ConversationHandler.END

    if not await check_force_join(ctx.bot, user.id):
        await send_join_prompt(update)
        return CHOOSING_MODE

    # FIX: rate limiter also applies to resume
    allowed, wait = await rate_limiter.check(user.id)
    if not allowed:
        await update.message.reply_text(
            f"⏳ Please wait <b>{wait}s</b> before starting another scan.",
            parse_mode=ParseMode.HTML,
        )
        return CHOOSING_MODE

    user_resumes = find_user_resumes(chat_id)
    if not user_resumes:
        await update.message.reply_text(
            "📂 No interrupted scans found for your account.\n"
            "Start a new scan with /file or /scan."
        )
        return CHOOSING_MODE

    f, state = user_resumes[0]
    domains     = state.get("domains", [])
    start_chunk = state.get("start_chunk", 0)
    prev_subs   = set(state.get("all_subs", []))
    basename    = state.get("basename", "resume")
    total_chunks = (len(domains) + config.CHUNK_SIZE - 1) // config.CHUNK_SIZE

    async with SCAN_LOCK:
        if chat_id in ACTIVE_SCANS:
            await update.message.reply_text("⚠️ A scan is already running.")
            return CHOOSING_MODE
        ACTIVE_SCANS.add(chat_id)

    status = await update.message.reply_text(
        f"▶️ <b>Resuming scan</b>\n\n"
        f"📂 File       : {basename}\n"
        f"📊 Domains    : {len(domains)}\n"
        f"⏭️ Resuming from chunk {start_chunk + 1}/{total_chunks}\n"
        f"✅ Already found: {len(prev_subs)} subdomains",
        parse_mode=ParseMode.HTML,
    )

    create_task(
        _run_bg_file_scan(
            ctx.bot, chat_id, user.id, status.message_id,
            domains, basename, start_chunk, prev_subs
        ),
        name=f"resume_{chat_id}"
    )

    await update.message.reply_text(
        "⏳ Resume scan started!", reply_markup=main_keyboard()
    )
    return CHOOSING_MODE


# ════════════════════════════════════════════════════════════════
#   Inline keyboard callback dispatcher
# ════════════════════════════════════════════════════════════════

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query:
        return CHOOSING_MODE

    if query.data == "force_join_check":
        return await force_join_callback(update, ctx)

    if query.data and query.data.startswith("adm_"):
        return await admin_callback(update, ctx)

    await query.answer()
    return CHOOSING_MODE


# ════════════════════════════════════════════════════════════════
#   Text button router (ReplyKeyboard)
# ════════════════════════════════════════════════════════════════

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    mapping = {
        "🔍 Scan Domain": cmd_scan,
        "📂 Scan File":   cmd_file,
        "📋 Status":      cmd_status,
        "ℹ️ Help":        cmd_help,
    }
    handler = mapping.get(text)
    if handler:
        return await handler(update, ctx)
    await update.message.reply_text(
        "Use the menu buttons or /help to see available commands.",
        reply_markup=main_keyboard(),
    )
    return CHOOSING_MODE
