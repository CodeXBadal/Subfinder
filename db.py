"""
SubHunter Bot v5.0 — User Database
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Thread-safe JSON-backed user storage.

Fixes:
  - _save() fully protected by lock (no partial write race)
  - increment_scans() takes user_id (not chat_id)
  - Atomic writes via temp-file + os.replace
  - last_name correctly updated even when empty string
"""

import os
import json
import shutil
import threading
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import config

log = logging.getLogger("SubHunter.DB")


class UserDB:
    """
    Thread-safe user database backed by a JSON file.
    RLock used so _save() can be called from within a locked section.
    """

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._data: dict = self._load()
        log.info(f"[UserDB] Loaded {self.total_count()} users from {path}")

    def _load(self) -> dict:
        if self.path.exists():
            try:
                with open(self.path, encoding="utf-8") as f:
                    data = json.load(f)
                if "users" not in data:
                    data["users"] = {}
                return data
            except json.JSONDecodeError as e:
                log.error(f"[UserDB] users.json is corrupt: {e}")
                bak = Path(str(self.path) + ".bak")
                if bak.exists():
                    log.warning("[UserDB] Trying .bak file...")
                    try:
                        with open(bak, encoding="utf-8") as f:
                            data = json.load(f)
                        log.info("[UserDB] Recovered from .bak file ✅")
                        return data
                    except Exception as e2:
                        log.error(f"[UserDB] .bak also failed: {e2}")
                log.error("[UserDB] No valid backup. Starting with empty DB.")
            except Exception as e:
                log.error(f"[UserDB] Load failed: {e}")
        return {"users": {}}

    def _save(self) -> None:
        """
        Atomically write user data to disk.
        FIX: Entire operation (serialize + write) is under the lock to prevent
        concurrent _save() calls from writing stale data to the tmp file.
        """
        tmp_path = str(self.path) + ".tmp"
        bak_path = str(self.path) + ".bak"

        with self._lock:
            data_json = json.dumps(self._data, indent=2, ensure_ascii=False)

            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(data_json)
                    f.flush()
                    os.fsync(f.fileno())

                if self.path.exists():
                    shutil.copy2(str(self.path), bak_path)

                os.replace(tmp_path, self.path)

            except Exception as e:
                log.error(f"[UserDB] Save failed: {e}")
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass

    def register(self, user) -> bool:
        """Register or update a user. Returns True if brand-new."""
        uid = str(user.id)
        with self._lock:
            is_new = uid not in self._data["users"]
            if is_new:
                self._data["users"][uid] = {
                    "user_id":     user.id,
                    "username":    user.username or "",
                    "first_name":  user.first_name or "",
                    "last_name":   user.last_name or "",
                    "join_date":   datetime.now().isoformat(),
                    "last_seen":   datetime.now().isoformat(),
                    "is_banned":   False,
                    "total_scans": 0,
                }
            else:
                self._data["users"][uid]["last_seen"]  = datetime.now().isoformat()
                self._data["users"][uid]["username"]   = user.username or ""
                self._data["users"][uid]["first_name"] = user.first_name or ""
                self._data["users"][uid]["last_name"]  = user.last_name or ""
        self._save()
        return is_new

    def get(self, user_id: int) -> Optional[dict]:
        with self._lock:
            u = self._data["users"].get(str(user_id))
            return dict(u) if u else None

    def is_banned(self, user_id: int) -> bool:
        with self._lock:
            u = self._data["users"].get(str(user_id))
            return u.get("is_banned", False) if u else False

    def ban(self, user_id: int) -> bool:
        uid = str(user_id)
        with self._lock:
            if uid not in self._data["users"]:
                return False
            self._data["users"][uid]["is_banned"] = True
        self._save()
        log.info(f"[UserDB] User {user_id} → BANNED")
        return True

    def unban(self, user_id: int) -> bool:
        uid = str(user_id)
        with self._lock:
            if uid not in self._data["users"]:
                return False
            self._data["users"][uid]["is_banned"] = False
        self._save()
        log.info(f"[UserDB] User {user_id} → UNBANNED")
        return True

    def increment_scans(self, user_id: int) -> None:
        """Always pass user_id NOT chat_id."""
        uid = str(user_id)
        with self._lock:
            if uid not in self._data["users"]:
                log.warning(f"[UserDB] increment_scans: user {user_id} not in DB")
                return
            self._data["users"][uid]["total_scans"] = (
                self._data["users"][uid].get("total_scans", 0) + 1
            )
        self._save()

    def all_users(self) -> list:
        with self._lock:
            return [dict(u) for u in self._data["users"].values()]

    def total_count(self) -> int:
        with self._lock:
            return len(self._data["users"])

    def banned_count(self) -> int:
        with self._lock:
            return sum(
                1 for u in self._data["users"].values()
                if u.get("is_banned")
            )


db = UserDB(config.USERS_FILE)
