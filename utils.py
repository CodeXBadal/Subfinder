"""
SubHunter Bot v5.0 — Utility Functions
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Domain cleaning, validation, progress bars,
output file builders, and async task safety.
"""

import re
import io
import asyncio
import hashlib
import logging
from datetime import datetime
from typing import Coroutine, Any

import config

log = logging.getLogger("SubHunter.Utils")


# ════════════════════════════════════════════════════════════════
#   D O M A I N  H E L P E R S
# ════════════════════════════════════════════════════════════════

def clean_domain(d: str) -> str:
    """Strip protocol, trailing slashes, and path from a domain string."""
    return re.sub(r"^https?://", "", d.strip()).rstrip("/").split("/")[0].lower()


def clean(sub: str, domain: str) -> str | None:
    """
    Normalize a raw subdomain string from an API response.
    Returns the cleaned subdomain string, or None if invalid.
    """
    sub = sub.strip().lower()
    sub = re.sub(r"^\*\.", "", sub)        # Remove wildcard  *.sub → sub
    sub = re.sub(r"^https?://", "", sub)   # Remove protocol
    sub = sub.split("/")[0].strip().rstrip(".")
    if domain in sub and re.match(r'^[a-z0-9._-]+$', sub):
        return sub
    return None


def is_valid_domain(raw: str) -> str | None:
    """
    Validate and normalize a user-supplied domain string.
    Returns the normalized domain, or None if invalid.
    """
    d = clean_domain(raw)
    pat = r'^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$'
    return d if re.match(pat, d) else None


# ════════════════════════════════════════════════════════════════
#   P R O G R E S S  D I S P L A Y
# ════════════════════════════════════════════════════════════════

def progress_bar(done: int, total: int, width: int = 20) -> str:
    """Build a Unicode block-progress bar string."""
    pct  = done / total if total else 0
    fill = int(pct * width)
    bar  = "█" * fill + "░" * (width - fill)
    return f"[{bar}] {done}/{total} ({int(pct * 100)}%)"


# ════════════════════════════════════════════════════════════════
#   S C A N  I D
# ════════════════════════════════════════════════════════════════

def scan_id_for(chat_id: int, filename: str) -> str:
    """Generate a unique, filesystem-safe scan ID for a (chat, file) pair."""
    raw = f"{chat_id}:{filename}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


# ════════════════════════════════════════════════════════════════
#   O U T P U T  F I L E  B U I L D E R S
# ════════════════════════════════════════════════════════════════

def build_file_content(
    domains: list,
    all_subs: set,
    elapsed: float,
    basename: str,
    source_count: int,
    chunk_info: str = "",
) -> str:
    lines = [
        "# SubHunter Bot v5.0 — MERGED OUTPUT",
        f"# Domains  : {len(domains)}",
        f"# Targets  : {', '.join(domains[:10])}{'...' if len(domains) > 10 else ''}",
        f"# Found    : {len(all_subs)} unique subdomains",
        f"# Date     : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"# Time     : {elapsed}s",
        f"# Sources  : {source_count} APIs per domain",
    ]
    if chunk_info:
        lines.append(f"# Chunk    : {chunk_info}")
    lines += [f"# {'─' * 40}", ""]
    lines += sorted(all_subs)
    return "\n".join(lines)


def build_single_content(
    domain: str,
    subs: set,
    elapsed: float,
    source_count: int,
) -> str:
    lines = [
        "# SubHunter Bot v5.0",
        f"# Domain   : {domain}",
        f"# Found    : {len(subs)} unique subdomains",
        f"# Date     : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"# Time     : {elapsed}s",
        f"# Sources  : {source_count} APIs",
        f"# {'─' * 40}", "",
    ]
    lines += sorted(subs)
    return "\n".join(lines)


def build_chunk_content(
    chunk_number: int,
    total_chunks: int,
    domains_in_chunk: list,
    chunk_subs: set,
    elapsed: float,
) -> str:
    lines = [
        f"# SubHunter Bot v5.0 — CHUNK {chunk_number}/{total_chunks}",
        f"# Domains in chunk : {len(domains_in_chunk)}",
        f"# Targets  : {', '.join(domains_in_chunk[:10])}{'...' if len(domains_in_chunk) > 10 else ''}",
        f"# Found    : {len(chunk_subs)} unique subdomains in this chunk",
        f"# Date     : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"# Time     : {elapsed}s elapsed",
        f"# {'─' * 40}", "",
    ]
    lines += sorted(chunk_subs)
    return "\n".join(lines)


def make_bytes(content: str, filename: str) -> io.BytesIO:
    """Convert string content to a named BytesIO object for Telegram upload."""
    buf = io.BytesIO(content.encode("utf-8"))
    buf.name = filename
    return buf


# ════════════════════════════════════════════════════════════════
#   A S Y N C  T A S K  S A F E T Y
# ════════════════════════════════════════════════════════════════

def create_task(coro: Coroutine, name: str = "unnamed") -> asyncio.Task:
    """Create an asyncio background task with automatic error logging."""
    task = asyncio.create_task(coro, name=name)
    task.add_done_callback(lambda t: _task_error_cb(t, name))
    return task


def _task_error_cb(task: asyncio.Task, name: str) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error(f"[Task:{name}] Unhandled exception: {exc}", exc_info=exc)
