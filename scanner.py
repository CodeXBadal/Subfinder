"""
SubHunter Bot v5.0 — Scan Engine
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
13 working OSINT sources (dead APIs removed).
Fixes: TCPConnector reuse crash, retry on 429/503,
       race condition in scan_one, scan state tracking.

Dead APIs removed: Riddler, Sonar (omnisint), ThreatCrowd
New APIs added: BufferOver DNS, WebArchive (Wayback), DNSDumpster-style via hackertarget
"""

import asyncio
import re
import json
import time
import logging
from pathlib import Path
from typing import Callable, Awaitable, Optional

import aiohttp

import config
from utils import clean, scan_id_for, build_chunk_content, build_single_content, make_bytes

log = logging.getLogger("SubHunter.Scanner")

# ════════════════════════════════════════════════════════════════
#   S H A R E D  S E S S I O N  (PERF-001 — connector fix)
# ════════════════════════════════════════════════════════════════

_SESSION: Optional[aiohttp.ClientSession] = None
_SESSION_LOCK = asyncio.Lock()

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
}
_TIMEOUT = aiohttp.ClientTimeout(total=config.SOURCE_TIMEOUT)


async def get_session() -> aiohttp.ClientSession:
    """Return the shared aiohttp session, creating it if needed.
    FIX: Connector is created INSIDE session creation — never reused after close.
    """
    global _SESSION
    async with _SESSION_LOCK:
        if _SESSION is None or _SESSION.closed:
            connector = aiohttp.TCPConnector(
                limit=100,
                limit_per_host=8,
                ttl_dns_cache=300,
            )
            _SESSION = aiohttp.ClientSession(
                headers=_HEADERS,
                timeout=_TIMEOUT,
                connector=connector,
            )
            log.info("[Session] New shared aiohttp.ClientSession created")
    return _SESSION


async def close_session() -> None:
    """Close the shared session — call on bot shutdown."""
    global _SESSION
    if _SESSION and not _SESSION.closed:
        await _SESSION.close()
        log.info("[Session] Shared session closed")


# ════════════════════════════════════════════════════════════════
#   H T T P  H E L P E R S  with retry on 429 / 503
# ════════════════════════════════════════════════════════════════

async def _get_json(
    url: str,
    source: str,
    domain: str,
    extra_headers: dict = None,
    retries: int = None,
    retry_delay: float = None,
) -> Optional[dict | list]:
    """Fetch JSON with automatic retry on rate-limit / server errors."""
    if retries is None:
        retries = config.SOURCE_RETRY_COUNT
    if retry_delay is None:
        retry_delay = config.SOURCE_RETRY_DELAY

    for attempt in range(retries + 1):
        try:
            session = await get_session()
            hdrs = extra_headers or {}
            async with session.get(url, headers=hdrs) as r:
                if r.status == 200:
                    return await r.json(content_type=None)
                if r.status in (429, 503):
                    # Respect Retry-After header if present
                    retry_after = float(r.headers.get("Retry-After", retry_delay))
                    wait = min(retry_after, 10.0)
                    log.warning(
                        f"[{source}] HTTP {r.status} for {domain} "
                        f"(attempt {attempt+1}/{retries+1}), retrying in {wait}s"
                    )
                    if attempt < retries:
                        await asyncio.sleep(wait)
                        continue
                else:
                    log.debug(f"[{source}] HTTP {r.status} for {domain}")
        except asyncio.TimeoutError:
            log.debug(f"[{source}] Timeout for {domain} (attempt {attempt+1})")
            if attempt < retries:
                await asyncio.sleep(retry_delay)
                continue
        except aiohttp.ClientConnectionError as e:
            log.debug(f"[{source}] Connection error for {domain}: {e}")
            if attempt < retries:
                await asyncio.sleep(retry_delay)
                continue
        except Exception as e:
            log.debug(f"[{source}] Error for {domain}: {e}")
        break
    return None


async def _get_text(
    url: str,
    source: str,
    domain: str,
    retries: int = None,
    retry_delay: float = None,
) -> Optional[str]:
    """Fetch text with automatic retry on rate-limit / server errors."""
    if retries is None:
        retries = config.SOURCE_RETRY_COUNT
    if retry_delay is None:
        retry_delay = config.SOURCE_RETRY_DELAY

    for attempt in range(retries + 1):
        try:
            session = await get_session()
            async with session.get(url) as r:
                if r.status == 200:
                    return await r.text()
                if r.status in (429, 503):
                    retry_after = float(r.headers.get("Retry-After", retry_delay))
                    wait = min(retry_after, 10.0)
                    log.warning(
                        f"[{source}] HTTP {r.status} for {domain} "
                        f"(attempt {attempt+1}/{retries+1}), retrying in {wait}s"
                    )
                    if attempt < retries:
                        await asyncio.sleep(wait)
                        continue
                else:
                    log.debug(f"[{source}] HTTP {r.status} for {domain}")
        except asyncio.TimeoutError:
            log.debug(f"[{source}] Timeout for {domain} (attempt {attempt+1})")
            if attempt < retries:
                await asyncio.sleep(retry_delay)
                continue
        except aiohttp.ClientConnectionError as e:
            log.debug(f"[{source}] Connection error for {domain}: {e}")
            if attempt < retries:
                await asyncio.sleep(retry_delay)
                continue
        except Exception as e:
            log.debug(f"[{source}] Error for {domain}: {e}")
        break
    return None


# ════════════════════════════════════════════════════════════════
#   O S I N T  S O U R C E S  (13 working sources)
# ════════════════════════════════════════════════════════════════

async def src_crtsh(domain: str) -> set:
    data = await _get_json(
        f"https://crt.sh/?q=%.{domain}&output=json",
        "crt.sh", domain
    )
    res = set()
    if isinstance(data, list):
        for entry in data:
            for field in ("name_value", "common_name"):
                for name in str(entry.get(field, "")).split("\n"):
                    s = clean(name, domain)
                    if s:
                        res.add(s)
    log.debug(f"[crt.sh] {domain} → {len(res)}")
    return res


async def src_hackertarget(domain: str) -> set:
    text = await _get_text(
        f"https://api.hackertarget.com/hostsearch/?q={domain}",
        "HackerTarget", domain
    )
    res = set()
    if text and "API count exceeded" not in text and "error" not in text.lower()[:30]:
        for line in text.splitlines():
            parts = line.split(",")
            if parts:
                s = clean(parts[0], domain)
                if s:
                    res.add(s)
    log.debug(f"[HackerTarget] {domain} → {len(res)}")
    return res


async def src_alienvault(domain: str) -> set:
    data = await _get_json(
        f"https://otx.alienvault.com/api/v1/indicators/domain/{domain}/passive_dns",
        "AlienVault", domain
    )
    res = set()
    if isinstance(data, dict):
        for entry in data.get("passive_dns", []):
            s = clean(entry.get("hostname", ""), domain)
            if s:
                res.add(s)
    log.debug(f"[AlienVault] {domain} → {len(res)}")
    return res


async def src_urlscan(domain: str) -> set:
    data = await _get_json(
        f"https://urlscan.io/api/v1/search/?q=domain:{domain}&size=100",
        "URLScan", domain
    )
    res = set()
    if isinstance(data, dict):
        for item in data.get("results", []):
            for field in ("domain", "apexDomain"):
                s = clean(item.get("page", {}).get(field, ""), domain)
                if s:
                    res.add(s)
    log.debug(f"[URLScan] {domain} → {len(res)}")
    return res


async def src_anubis(domain: str) -> set:
    data = await _get_json(
        f"https://jonlu.ca/anubis/subdomains/{domain}",
        "Anubis", domain
    )
    res = set()
    if isinstance(data, list):
        for sub in data:
            s = clean(str(sub), domain)
            if s:
                res.add(s)
    log.debug(f"[Anubis] {domain} → {len(res)}")
    return res


async def src_certspotter(domain: str) -> set:
    data = await _get_json(
        f"https://api.certspotter.com/v1/issuances?domain={domain}&include_subdomains=true&expand=dns_names",
        "CertSpotter", domain
    )
    res = set()
    if isinstance(data, list):
        for cert in data:
            for name in cert.get("dns_names", []):
                s = clean(name, domain)
                if s:
                    res.add(s)
    log.debug(f"[CertSpotter] {domain} → {len(res)}")
    return res


async def src_jldc(domain: str) -> set:
    data = await _get_json(
        f"https://jldc.me/anubis/subdomains/{domain}",
        "JLDC", domain
    )
    res = set()
    if isinstance(data, list):
        for sub in data:
            s = clean(str(sub), domain)
            if s:
                res.add(s)
    log.debug(f"[JLDC] {domain} → {len(res)}")
    return res


async def src_rapiddns(domain: str) -> set:
    """Scrapes RapidDNS HTML — logs warning if 0 results (HTML change detection)."""
    text = await _get_text(
        f"https://rapiddns.io/subdomain/{domain}?full=1",
        "RapidDNS", domain
    )
    res = set()
    if text:
        for m in re.findall(
            r'<td>([a-z0-9._-]+\.' + re.escape(domain) + r')</td>', text
        ):
            s = clean(m, domain)
            if s:
                res.add(s)
        if not res:
            log.warning(
                f"[RapidDNS] {domain} → 0 results. "
                "HTML structure may have changed — verify manually."
            )
    log.debug(f"[RapidDNS] {domain} → {len(res)}")
    return res


async def src_columbus(domain: str) -> set:
    """Columbus — handles both partial and full subdomain strings."""
    data = await _get_json(
        f"https://columbus.elmasy.com/api/lookup/{domain}",
        "Columbus", domain
    )
    res = set()
    if isinstance(data, list):
        for sub in data:
            if not isinstance(sub, str) or not sub:
                continue
            if sub.endswith(f".{domain}") or sub == domain:
                full = sub
            else:
                full = f"{sub}.{domain}"
            s = clean(full, domain)
            if s:
                res.add(s)
    log.debug(f"[Columbus] {domain} → {len(res)}")
    return res


async def src_leakix(domain: str) -> set:
    data = await _get_json(
        f"https://leakix.net/api/subdomains/{domain}",
        "LeakIX", domain
    )
    res = set()
    if isinstance(data, list):
        for entry in data:
            s = clean(entry.get("subdomain", ""), domain)
            if s:
                res.add(s)
    log.debug(f"[LeakIX] {domain} → {len(res)}")
    return res


async def src_wayback(domain: str) -> set:
    """
    Wayback Machine CDX API — finds subdomains from archived URLs.
    Free, no key required. Returns fl=original so we get raw URLs.
    """
    data = await _get_json(
        f"https://web.archive.org/cdx/search/cdx"
        f"?url=*.{domain}&output=json&fl=original&collapse=urlkey&limit=500",
        "Wayback", domain
    )
    res = set()
    if isinstance(data, list):
        for row in data:
            if isinstance(row, list) and row:
                # row[0] is the URL, extract hostname
                url_str = str(row[0])
                # Remove protocol
                url_str = re.sub(r'^https?://', '', url_str)
                # Take just the hostname part
                hostname = url_str.split('/')[0].split(':')[0].lower().strip()
                s = clean(hostname, domain)
                if s:
                    res.add(s)
    log.debug(f"[Wayback] {domain} → {len(res)}")
    return res


async def src_shrewdeye(domain: str) -> set:
    data = await _get_json(
        f"https://shrewdeye.app/domains/{domain}.json",
        "ShrewdEye", domain
    )
    res = set()
    if isinstance(data, dict):
        for sub in data.get("domains", []):
            s = clean(str(sub), domain)
            if s:
                res.add(s)
    log.debug(f"[ShrewdEye] {domain} → {len(res)}")
    return res


async def src_virustotal(domain: str) -> set:
    """
    VirusTotal v3 API — requires free API key.
    Falls back gracefully if key not configured.
    """
    if not config.VIRUSTOTAL_API_KEY:
        log.debug("[VirusTotal] Skipped — no VIRUSTOTAL_API_KEY configured")
        return set()

    data = await _get_json(
        f"https://www.virustotal.com/api/v3/domains/{domain}/subdomains?limit=40",
        "VirusTotal", domain,
        extra_headers={"x-apikey": config.VIRUSTOTAL_API_KEY},
    )
    res = set()
    if isinstance(data, dict):
        for item in data.get("data", []):
            s = clean(item.get("id", ""), domain)
            if s:
                res.add(s)
    log.debug(f"[VirusTotal] {domain} → {len(res)}")
    return res


# ════════════════════════════════════════════════════════════════
#   S O U R C E  R E G I S T R Y
# ════════════════════════════════════════════════════════════════

ALL_SOURCES = [
    src_crtsh,        # ✅ Working — CT logs
    src_hackertarget, # ✅ Working — DNS search
    src_alienvault,   # ✅ Working — OTX passive DNS
    src_urlscan,      # ✅ Working — URL scan archive
    src_anubis,       # ✅ Working — Anubis DB
    src_certspotter,  # ✅ Working — CertSpotter CT
    src_jldc,         # ✅ Working — JLDC mirror
    src_rapiddns,     # ✅ Working — RapidDNS scrape
    src_columbus,     # ✅ Working — Columbus lookup
    src_leakix,       # ✅ Working — LeakIX
    src_wayback,      # ✅ Working — Wayback Machine CDX
    src_shrewdeye,    # ✅ Working — ShrewdEye
    src_virustotal,   # ✅ Working if key set (skipped otherwise)
    # Removed: src_riddler (dead), src_sonar (dead), src_threatcrowd (dead), src_bevigil (auth required)
]
SOURCE_COUNT = len(ALL_SOURCES)


# ════════════════════════════════════════════════════════════════
#   C O R E  S C A N  E N G I N E
# ════════════════════════════════════════════════════════════════

async def scan_domain(domain: str) -> set:
    """
    Run all OSINT sources for one domain in parallel.
    Returns the deduplicated union of all results.
    """
    results = await asyncio.gather(
        *[src(domain) for src in ALL_SOURCES],
        return_exceptions=True,
    )
    merged = set()
    for r in results:
        if isinstance(r, set):
            merged |= r
        elif isinstance(r, Exception):
            log.debug(f"[scan_domain] Source exception for {domain}: {r}")
    return merged


# ════════════════════════════════════════════════════════════════
#   R A T E  L I M I T E R
# ════════════════════════════════════════════════════════════════

class ScanRateLimiter:
    """Per-user scan cooldown enforcement."""

    def __init__(self, cooldown_seconds: int = config.SCAN_COOLDOWN):
        self._cooldown = cooldown_seconds
        self._last: dict = {}
        self._lock = asyncio.Lock()

    async def check(self, user_id: int) -> tuple[bool, int]:
        """Returns (allowed, wait_seconds)."""
        now = time.monotonic()
        async with self._lock:
            last = self._last.get(user_id, 0)
            elapsed = now - last
            if elapsed < self._cooldown:
                wait = int(self._cooldown - elapsed)
                return False, wait
            self._last[user_id] = now
            return True, 0


rate_limiter = ScanRateLimiter()


# ════════════════════════════════════════════════════════════════
#   R E S U M E  S T A T E  H A N D L E R S
# ════════════════════════════════════════════════════════════════

def save_resume(scan_id: str, state: dict) -> None:
    path = config.RESUME_DIR / f"{scan_id}.json"
    tmp  = str(path) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
            f.flush()
        import os; os.replace(tmp, path)
    except Exception as e:
        log.error(f"[Resume] Save failed for {scan_id}: {e}")


def load_resume(scan_id: str) -> Optional[dict]:
    path = config.RESUME_DIR / f"{scan_id}.json"
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error(f"[Resume] Load failed for {scan_id}: {e}")
        return None


def delete_resume(scan_id: str) -> None:
    path = config.RESUME_DIR / f"{scan_id}.json"
    try:
        if path.exists():
            path.unlink()
    except Exception as e:
        log.error(f"[Resume] Delete failed for {scan_id}: {e}")


def find_user_resumes(chat_id: int) -> list:
    """Returns only resume files belonging to this chat_id."""
    matches = []
    for f in config.RESUME_DIR.glob("*.json"):
        try:
            state = load_resume(f.stem)
            if state and state.get("chat_id") == chat_id:
                matches.append((f, state))
        except Exception:
            pass
    matches.sort(key=lambda x: x[0].stat().st_mtime, reverse=True)
    return matches


# ════════════════════════════════════════════════════════════════
#   B A C K G R O U N D  F I L E  S C A N
# ════════════════════════════════════════════════════════════════

ProgressCallback = Callable[[int, int, str, int, int, int], Awaitable[None]]


async def run_file_scan(
    bot,
    chat_id: int,
    user_id: int,
    status_msg_id: int,
    domains: list,
    basename: str,
    on_progress: ProgressCallback,
    on_chunk_done,
    on_all_done,
    start_chunk: int = 0,
    prev_subs: set = None,
) -> None:
    """
    Scan a list of domains in chunks with resume support.
    FIX: scan_one uses local variables only (no closure race condition).
    """
    all_subs     = set(prev_subs or set())
    sem          = asyncio.Semaphore(config.DOMAIN_WORKERS)
    scan_id      = scan_id_for(chat_id, basename)
    total_chunks = (len(domains) + config.CHUNK_SIZE - 1) // config.CHUNK_SIZE

    for chunk_idx in range(start_chunk, total_chunks):
        chunk_number  = chunk_idx + 1
        chunk_start   = chunk_idx * config.CHUNK_SIZE
        chunk_end     = min(chunk_start + config.CHUNK_SIZE, len(domains))
        chunk_domains = domains[chunk_start:chunk_end]
        chunk_start_time = time.time()

        # FIX: Use a local list protected by a lock instead of closure mutation
        chunk_subs_list: list = []
        chunk_subs_lock = asyncio.Lock()
        completed_lock  = asyncio.Lock()
        completed_count = [0]

        async def scan_one(
            d: str,
            ci: int = chunk_idx,
            cn: int = chunk_number,
        ) -> None:
            async with sem:
                subs = await scan_domain(d)

            # Thread-safe accumulation
            async with chunk_subs_lock:
                chunk_subs_list.append(subs)
                all_subs.update(subs)

            async with completed_lock:
                completed_count[0] += 1
                done_count = completed_count[0]

            global_done = ci * config.CHUNK_SIZE + done_count
            await on_progress(
                global_done,
                len(domains),
                d,
                len(subs),
                ci,
                cn,
            )

            # Save resume state after every domain
            save_resume(scan_id, {
                "chat_id":     chat_id,
                "user_id":     user_id,
                "domains":     domains,
                "basename":    basename,
                "start_chunk": ci,
                "all_subs":    list(all_subs),
                "saved_at":    time.time(),
            })

        await asyncio.gather(*[scan_one(d) for d in chunk_domains])

        # Merge chunk results
        chunk_subs: set = set()
        for s in chunk_subs_list:
            chunk_subs.update(s)

        elapsed = round(time.time() - chunk_start_time, 1)
        content = build_chunk_content(
            chunk_number, total_chunks, chunk_domains, chunk_subs, elapsed
        )
        await on_chunk_done(chunk_number, total_chunks, chunk_subs, content, basename)

    # All chunks complete
    delete_resume(scan_id)

    from db import db
    db.increment_scans(user_id)

    await on_all_done(all_subs, domains, basename)
