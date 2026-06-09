"""
SubHunter Bot v5.0 — Admin Panel
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Admin commands: stats, user list, ban/unban, broadcast.

Fixes:
  - Button colors using PTB 22.7 InlineKeyboardButton(style=...)
  - Ban/unban input returns correct keyboard type
  - Broadcast handles RetryAfter (FloodWait) correctly
  - Background tasks use utils.create_task for error logging
"""

import asyncio
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.error import RetryAfter, Forbidden, TelegramError
from telegram.ext import ContextTypes

import config
from config import ADMIN_BROADCAST, ADMIN_BAN_INPUT, ADMIN_UNBAN_INPUT, CHOOSING_MODE
from db import db
from utils import create_task

log = logging.getLogger("SubHunter.Admin")


# ════════════════════════════════════════════════════════════════
#   A C C E S S  G U A R D
# ════════════════════════════════════════════════════════════════

def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


async def guard(update: Update) -> bool:
    if is_admin(update.effective_user.id):
        return True
    await update.effective_message.reply_text("⛔ Access denied.")
    return False


# ════════════════════════════════════════════════════════════════
#   K E Y B O A R D S  (PTB 22.7 button colors)
# ════════════════════════════════════════════════════════════════

def admin_keyboard() -> InlineKeyboardMarkup:
    """
    PTB 22.7: InlineKeyboardButton supports style= parameter.
    'primary' = blue, 'success' = green, 'danger' = red
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Stats",    callback_data="adm_stats",     style="primary"),
            InlineKeyboardButton("👥 Users",    callback_data="adm_users",     style="primary"),
        ],
        [
            InlineKeyboardButton("🚫 Ban",      callback_data="adm_ban",       style="danger"),
            InlineKeyboardButton("✅ Unban",     callback_data="adm_unban",     style="success"),
        ],
        [
            InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast", style="primary"),
        ],
        [
            InlineKeyboardButton("❌ Close",     callback_data="adm_close",     style="danger"),
        ],
    ])


# ════════════════════════════════════════════════════════════════
#   /admin  COMMAND
# ════════════════════════════════════════════════════════════════

async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not await guard(update):
        return CHOOSING_MODE

    total  = db.total_count()
    banned = db.banned_count()
    active = total - banned

    text = (
        f"🔧 <b>SubHunter Admin Panel</b>\n\n"
        f"👤 Total users : <b>{total}</b>\n"
        f"✅ Active       : <b>{active}</b>\n"
        f"🚫 Banned       : <b>{banned}</b>\n"
        f"\n<i>Choose an action:</i>"
    )
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=admin_keyboard()
    )
    return CHOOSING_MODE


# ════════════════════════════════════════════════════════════════
#   C A L L B A C K  H A N D L E R
# ════════════════════════════════════════════════════════════════

async def admin_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data  = query.data

    if not is_admin(update.effective_user.id):
        await query.edit_message_text("⛔ Access denied.")
        return CHOOSING_MODE

    if data == "adm_stats":
        return await _show_stats(query, ctx)
    elif data == "adm_users":
        return await _show_users(query, ctx)
    elif data == "adm_ban":
        await query.edit_message_text(
            "🚫 <b>Ban a User</b>\n\nSend the user's Telegram ID:",
            parse_mode=ParseMode.HTML,
        )
        ctx.user_data["admin_action"] = "ban"
        return ADMIN_BAN_INPUT
    elif data == "adm_unban":
        await query.edit_message_text(
            "✅ <b>Unban a User</b>\n\nSend the user's Telegram ID:",
            parse_mode=ParseMode.HTML,
        )
        ctx.user_data["admin_action"] = "unban"
        return ADMIN_UNBAN_INPUT
    elif data == "adm_broadcast":
        await query.edit_message_text(
            "📢 <b>Broadcast</b>\n\nSend your message (HTML supported):\n"
            "Type /cancel to abort.",
            parse_mode=ParseMode.HTML,
        )
        return ADMIN_BROADCAST
    elif data == "adm_close":
        await query.delete_message()
        return CHOOSING_MODE

    return CHOOSING_MODE


# ════════════════════════════════════════════════════════════════
#   S T A T S
# ════════════════════════════════════════════════════════════════

async def _show_stats(query, ctx) -> int:
    users  = db.all_users()
    total  = len(users)
    banned = sum(1 for u in users if u.get("is_banned"))
    scans  = sum(u.get("total_scans", 0) for u in users)
    top    = sorted(users, key=lambda u: u.get("total_scans", 0), reverse=True)[:5]

    top_text = "\n".join(
        f"  {i+1}. {u.get('first_name','?')} — {u.get('total_scans',0)} scans"
        for i, u in enumerate(top)
    ) or "  No data"

    text = (
        f"📊 <b>Bot Statistics</b>\n\n"
        f"👤 Total users   : <b>{total}</b>\n"
        f"🚫 Banned         : <b>{banned}</b>\n"
        f"✅ Active          : <b>{total - banned}</b>\n"
        f"🔍 Total scans    : <b>{scans}</b>\n\n"
        f"🏆 <b>Top Scanners:</b>\n{top_text}"
    )
    await query.edit_message_text(
        text, parse_mode=ParseMode.HTML, reply_markup=admin_keyboard()
    )
    return CHOOSING_MODE


async def _show_users(query, ctx) -> int:
    users = db.all_users()
    if not users:
        await query.edit_message_text(
            "No users registered yet.", reply_markup=admin_keyboard()
        )
        return CHOOSING_MODE

    lines = [f"👥 <b>Users ({len(users)} total)</b>\n"]
    for u in sorted(users, key=lambda x: x.get("join_date", ""), reverse=True)[:30]:
        status = "🚫" if u.get("is_banned") else "✅"
        name   = u.get("first_name", "?")
        uname  = f"@{u['username']}" if u.get("username") else "no username"
        uid    = u.get("user_id", "?")
        scans  = u.get("total_scans", 0)
        lines.append(f"{status} {name} ({uname}) — ID: <code>{uid}</code> — {scans} scans")

    if len(users) > 30:
        lines.append(f"\n<i>…and {len(users)-30} more</i>")

    await query.edit_message_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=admin_keyboard()
    )
    return CHOOSING_MODE


# ════════════════════════════════════════════════════════════════
#   B A N  /  U N B A N  I N P U T
# ════════════════════════════════════════════════════════════════

async def handle_ban_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not await guard(update):
        return CHOOSING_MODE

    raw = update.message.text.strip()
    if not raw.isdigit():
        await update.message.reply_text("❌ Please send a valid numeric Telegram user ID.")
        return ADMIN_BAN_INPUT

    uid = int(raw)
    if uid in config.ADMIN_IDS:
        await update.message.reply_text("❌ Cannot ban an admin.")
        return ADMIN_BAN_INPUT

    success = db.ban(uid)
    if success:
        await update.message.reply_text(
            f"🚫 User <code>{uid}</code> has been banned.\n\nUse /admin to open the panel.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(
            f"❌ User <code>{uid}</code> not found in the database.",
            parse_mode=ParseMode.HTML,
        )
    return CHOOSING_MODE


async def handle_unban_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not await guard(update):
        return CHOOSING_MODE

    raw = update.message.text.strip()
    if not raw.isdigit():
        await update.message.reply_text("❌ Please send a valid numeric Telegram user ID.")
        return ADMIN_UNBAN_INPUT

    uid     = int(raw)
    success = db.unban(uid)
    if success:
        await update.message.reply_text(
            f"✅ User <code>{uid}</code> has been unbanned.\n\nUse /admin to open the panel.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(
            f"❌ User <code>{uid}</code> not found in the database.",
            parse_mode=ParseMode.HTML,
        )
    return CHOOSING_MODE


# ════════════════════════════════════════════════════════════════
#   B R O A D C A S T
# ════════════════════════════════════════════════════════════════

async def handle_broadcast_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not await guard(update):
        return CHOOSING_MODE

    message = update.message.text.strip()
    if not message:
        await update.message.reply_text("❌ Empty message. Broadcast cancelled.")
        return CHOOSING_MODE

    status_msg = await update.message.reply_text("📢 Starting broadcast…")
    create_task(
        _do_broadcast(ctx.bot, message, status_msg.chat_id, status_msg.message_id),
        name="broadcast"
    )
    return CHOOSING_MODE


async def _do_broadcast(bot, message: str, status_chat: int, status_msg_id: int) -> None:
    """Handles Telegram RetryAfter (flood wait). Logs Forbidden separately."""
    users   = db.all_users()
    active  = [u for u in users if not u.get("is_banned") and u.get("user_id")]
    success = 0
    fail    = 0
    blocked = 0

    for u in active:
        uid = u["user_id"]
        for attempt in range(2):
            try:
                await bot.send_message(
                    chat_id=uid,
                    text=message,
                    parse_mode=ParseMode.HTML,
                )
                success += 1
                break

            except RetryAfter as e:
                wait = int(e.retry_after) + 2
                log.warning(f"[Broadcast] FloodWait {wait}s")
                await asyncio.sleep(wait)
                if attempt == 1:
                    fail += 1

            except Forbidden:
                blocked += 1
                break

            except TelegramError as e:
                log.warning(f"[Broadcast] TelegramError for {uid}: {e}")
                fail += 1
                break

            except Exception as e:
                log.error(f"[Broadcast] Unexpected error for {uid}: {e}")
                fail += 1
                break

        await asyncio.sleep(0.05)  # ~20 msg/sec

    summary = (
        f"📢 <b>Broadcast Complete</b>\n\n"
        f"✅ Delivered  : {success}\n"
        f"🚫 Blocked    : {blocked}\n"
        f"❌ Failed     : {fail}\n"
        f"📬 Total      : {len(active)}"
    )
    try:
        await bot.edit_message_text(
            chat_id=status_chat,
            message_id=status_msg_id,
            text=summary,
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        await bot.send_message(
            chat_id=status_chat, text=summary, parse_mode=ParseMode.HTML
        )
    log.info(f"[Broadcast] Done: {success} ok / {blocked} blocked / {fail} failed")
