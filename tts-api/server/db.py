from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Optional

import aiosqlite

from .config import DB_PATH, load_settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_api_key(prefix: str = "tts") -> str:
    return f"{prefix}_{secrets.token_urlsafe(24)}"


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  key_hash TEXT NOT NULL UNIQUE,
  key_prefix TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  max_chars INTEGER,
  quota_chars_day INTEGER,
  quota_jobs_day INTEGER,
  max_concurrent INTEGER,
  chars_used_day INTEGER NOT NULL DEFAULT 0,
  jobs_used_day INTEGER NOT NULL DEFAULT 0,
  usage_day TEXT,
  total_chars INTEGER NOT NULL DEFAULT 0,
  total_jobs INTEGER NOT NULL DEFAULT 0,
  note TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  last_used_at TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  api_key_id INTEGER,
  status TEXT NOT NULL,
  text TEXT NOT NULL,
  text_chars INTEGER NOT NULL,
  voice TEXT,
  model TEXT,
  lang TEXT,
  speed REAL DEFAULT 1.0,
  audio_path TEXT,
  audio_bytes INTEGER,
  error TEXT,
  proxy_id TEXT,
  exit_ip TEXT,
  attempts INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  duration_ms INTEGER,
  FOREIGN KEY(api_key_id) REFERENCES api_keys(id)
);

CREATE TABLE IF NOT EXISTS usage_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  api_key_id INTEGER,
  job_id TEXT,
  chars INTEGER NOT NULL,
  ok INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  day TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_sessions (
  token TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  expires_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_usage_day ON usage_events(day);
CREATE INDEX IF NOT EXISTS idx_keys_hash ON api_keys(key_hash);
"""


class Database:
    def __init__(self, path=DB_PATH):
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        await self._bootstrap()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None
        return self._conn

    async def _bootstrap(self) -> None:
        settings = load_settings()
        cur = await self.conn.execute("SELECT COUNT(*) AS c FROM api_keys")
        row = await cur.fetchone()
        if row and row["c"] == 0:
            import os

            raw = os.environ.get("TTS_BOOTSTRAP_API_KEY") or new_api_key("tts")
            await self.create_api_key(
                name="bootstrap",
                raw_key=raw,
                max_chars=settings.get("default_max_chars"),
                quota_chars_day=settings.get("default_quota_chars_day"),
                quota_jobs_day=settings.get("default_quota_jobs_day"),
                max_concurrent=settings.get("default_max_concurrent"),
                note="auto-created on first boot — copy from data/bootstrap_key.txt",
            )
            boot = self.path.parent.parent / "bootstrap_key.txt"
            boot.write_text(
                f"Bootstrap API key (save this, shown once):\n{raw}\n",
                encoding="utf-8",
            )

    # ── API keys ──────────────────────────────────────────────────────────
    async def create_api_key(
        self,
        name: str,
        raw_key: str | None = None,
        max_chars: int | None = None,
        quota_chars_day: int | None = None,
        quota_jobs_day: int | None = None,
        max_concurrent: int | None = None,
        note: str = "",
    ) -> dict:
        settings = load_settings()
        raw = raw_key or new_api_key("tts")
        prefix = raw[:12] + "…"
        await self.conn.execute(
            """
            INSERT INTO api_keys
            (name, key_hash, key_prefix, enabled, max_chars, quota_chars_day,
             quota_jobs_day, max_concurrent, usage_day, created_at, note)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                hash_key(raw),
                prefix,
                max_chars if max_chars is not None else settings["default_max_chars"],
                quota_chars_day
                if quota_chars_day is not None
                else settings["default_quota_chars_day"],
                quota_jobs_day
                if quota_jobs_day is not None
                else settings["default_quota_jobs_day"],
                max_concurrent
                if max_concurrent is not None
                else settings["default_max_concurrent"],
                _day(),
                _now(),
                note,
            ),
        )
        await self.conn.commit()
        cur = await self.conn.execute(
            "SELECT * FROM api_keys WHERE key_hash = ?", (hash_key(raw),)
        )
        row = dict(await cur.fetchone())
        row["raw_key"] = raw  # only on create
        return row

    async def list_api_keys(self) -> list[dict]:
        cur = await self.conn.execute("SELECT * FROM api_keys ORDER BY id DESC")
        return [dict(r) for r in await cur.fetchall()]

    async def get_api_key_by_raw(self, raw: str) -> Optional[dict]:
        cur = await self.conn.execute(
            "SELECT * FROM api_keys WHERE key_hash = ? AND enabled = 1",
            (hash_key(raw),),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_api_key(self, key_id: int) -> Optional[dict]:
        cur = await self.conn.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def update_api_key(self, key_id: int, **fields: Any) -> Optional[dict]:
        allowed = {
            "name",
            "enabled",
            "max_chars",
            "quota_chars_day",
            "quota_jobs_day",
            "max_concurrent",
            "note",
        }
        sets = []
        vals = []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k} = ?")
                vals.append(v)
        if not sets:
            return await self.get_api_key(key_id)
        vals.append(key_id)
        await self.conn.execute(
            f"UPDATE api_keys SET {', '.join(sets)} WHERE id = ?", vals
        )
        await self.conn.commit()
        return await self.get_api_key(key_id)

    async def delete_api_key(self, key_id: int) -> bool:
        cur = await self.conn.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
        await self.conn.commit()
        return cur.rowcount > 0

    async def _roll_usage_day(self, key: dict) -> dict:
        today = _day()
        if key.get("usage_day") != today:
            await self.conn.execute(
                """
                UPDATE api_keys
                SET usage_day = ?, chars_used_day = 0, jobs_used_day = 0
                WHERE id = ?
                """,
                (today, key["id"]),
            )
            await self.conn.commit()
            key = await self.get_api_key(key["id"]) or key
        return key

    async def check_quota(self, key: dict, chars: int) -> tuple[bool, str]:
        key = await self._roll_usage_day(key)
        if key["jobs_used_day"] >= (key["quota_jobs_day"] or 0):
            return False, "daily job quota exceeded"
        if key["chars_used_day"] + chars > (key["quota_chars_day"] or 0):
            return False, "daily character quota exceeded"
        # concurrent
        cur = await self.conn.execute(
            """
            SELECT COUNT(*) AS c FROM jobs
            WHERE api_key_id = ? AND status IN ('queued','running')
            """,
            (key["id"],),
        )
        row = await cur.fetchone()
        if row and row["c"] >= (key["max_concurrent"] or 1):
            return False, "max concurrent jobs reached"
        max_chars = key.get("max_chars") or load_settings()["default_max_chars"]
        if chars > max_chars:
            return False, f"text exceeds max_chars={max_chars}"
        return True, "ok"

    async def record_success_usage(self, key_id: int, job_id: str, chars: int) -> None:
        await self.conn.execute(
            """
            UPDATE api_keys SET
              chars_used_day = chars_used_day + ?,
              jobs_used_day = jobs_used_day + 1,
              total_chars = total_chars + ?,
              total_jobs = total_jobs + 1,
              last_used_at = ?,
              usage_day = ?
            WHERE id = ?
            """,
            (chars, chars, _now(), _day(), key_id),
        )
        await self.conn.execute(
            """
            INSERT INTO usage_events (api_key_id, job_id, chars, ok, created_at, day)
            VALUES (?, ?, ?, 1, ?, ?)
            """,
            (key_id, job_id, chars, _now(), _day()),
        )
        await self.conn.commit()

    # ── jobs ──────────────────────────────────────────────────────────────
    async def create_job(self, job: dict) -> dict:
        await self.conn.execute(
            """
            INSERT INTO jobs
            (id, api_key_id, status, text, text_chars, voice, model, lang, speed,
             created_at, attempts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                job["id"],
                job.get("api_key_id"),
                job.get("status", "queued"),
                job["text"],
                job["text_chars"],
                job.get("voice"),
                job.get("model"),
                job.get("lang"),
                job.get("speed", 1.0),
                _now(),
            ),
        )
        await self.conn.commit()
        return await self.get_job(job["id"])  # type: ignore

    async def get_job(self, job_id: str) -> Optional[dict]:
        cur = await self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def update_job(self, job_id: str, **fields: Any) -> Optional[dict]:
        if not fields:
            return await self.get_job(job_id)
        sets = []
        vals = []
        for k, v in fields.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        vals.append(job_id)
        await self.conn.execute(
            f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", vals
        )
        await self.conn.commit()
        return await self.get_job(job_id)

    async def claim_next_job(self) -> Optional[dict]:
        """Atomically claim oldest queued job (serialized for shared SQLite conn)."""
        async with self._lock:
            cur = await self.conn.execute(
                """
                SELECT id FROM jobs WHERE status = 'queued'
                ORDER BY created_at ASC LIMIT 1
                """
            )
            row = await cur.fetchone()
            if not row:
                return None
            jid = row["id"]
            await self.conn.execute(
                """
                UPDATE jobs SET status = 'running', started_at = ?, attempts = attempts + 1
                WHERE id = ? AND status = 'queued'
                """,
                (_now(), jid),
            )
            await self.conn.commit()
            return await self.get_job(jid)

    async def list_jobs(self, limit: int = 50, status: str | None = None) -> list[dict]:
        if status:
            cur = await self.conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cur = await self.conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        rows = []
        for r in await cur.fetchall():
            d = dict(r)
            # don't dump full text in list
            t = d.get("text") or ""
            d["text_preview"] = (t[:80] + "…") if len(t) > 80 else t
            del d["text"]
            rows.append(d)
        return rows

    async def usage_summary(self, days: int = 7) -> dict:
        cur = await self.conn.execute(
            """
            SELECT day, SUM(chars) AS chars, SUM(ok) AS ok_jobs, COUNT(*) AS events
            FROM usage_events
            GROUP BY day ORDER BY day DESC LIMIT ?
            """,
            (days,),
        )
        by_day = [dict(r) for r in await cur.fetchall()]
        cur = await self.conn.execute(
            """
            SELECT status, COUNT(*) AS c FROM jobs GROUP BY status
            """
        )
        by_status = {r["status"]: r["c"] for r in await cur.fetchall()}
        cur = await self.conn.execute(
            "SELECT COUNT(*) AS c, COALESCE(SUM(total_chars),0) AS chars FROM api_keys"
        )
        keys = dict(await cur.fetchone())
        return {"by_day": by_day, "jobs_by_status": by_status, "keys": keys}

    # ── admin sessions ────────────────────────────────────────────────────
    async def create_session(self, token: str, ttl_sec: int = 86400 * 7) -> None:
        await self.conn.execute(
            "INSERT INTO admin_sessions (token, created_at, expires_at) VALUES (?, ?, ?)",
            (token, _now(), time.time() + ttl_sec),
        )
        await self.conn.commit()

    async def valid_session(self, token: str) -> bool:
        cur = await self.conn.execute(
            "SELECT expires_at FROM admin_sessions WHERE token = ?", (token,)
        )
        row = await cur.fetchone()
        if not row:
            return False
        if row["expires_at"] < time.time():
            await self.conn.execute(
                "DELETE FROM admin_sessions WHERE token = ?", (token,)
            )
            await self.conn.commit()
            return False
        return True

    async def delete_session(self, token: str) -> None:
        await self.conn.execute("DELETE FROM admin_sessions WHERE token = ?", (token,))
        await self.conn.commit()
