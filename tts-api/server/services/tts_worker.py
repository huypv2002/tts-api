from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

from ..config import AUDIO_DIR, PARENT, load_settings
from ..db import Database
from .proxy_pool import pool

BLOCK_MARKERS = (
    "quota_exceeded",
    "sign_in_required",
    "detected_unusual_activity",
    "unusual activity",
    "rate limit",
    "free tier",
    "tts http 403",
    "tts http 401",
    "tts http 429",
    "forbidden",
    "access denied",
)

TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "i/o timeout",
    "dial tcp",
    "connection reset",
    "connection refused",
    "tlsclient",
    "connecterror",
    "proxyerror",
    "network is unreachable",
    "temporarily unavailable",
    "server disconnected",
    "ssl",
)


def _is_transient(err: BaseException) -> bool:
    blob = f"{type(err).__name__}: {err}".lower()
    return any(m in blob for m in TRANSIENT_MARKERS)


def _is_block(err: BaseException) -> bool:
    if _is_transient(err):
        return False
    msg = str(err).lower()
    return any(m in msg for m in BLOCK_MARKERS)


def _import_tts():
    """Lazy import so admin API can boot without Camoufox installed."""
    if str(PARENT) not in sys.path:
        sys.path.insert(0, str(PARENT))
    try:
        from fast_tts import call_tts, solve_token  # noqa: WPS433
    except SystemExit as e:
        raise RuntimeError(
            "fast_tts failed to import (install camoufox + tls-client on the server: "
            "pip install camoufox tls-client && camoufox fetch)"
        ) from e
    except Exception as e:
        raise RuntimeError(f"fast_tts import error: {e}") from e
    return solve_token, call_tts


class WorkerManager:
    def __init__(self, db: Database):
        self.db = db
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()

    async def start(self) -> None:
        settings = load_settings()
        n = max(1, int(settings.get("worker_count") or 4))
        self._stop.clear()
        for i in range(n):
            self._tasks.append(asyncio.create_task(self._loop(i + 1), name=f"w{i+1}"))

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _loop(self, wid: int) -> None:
        while not self._stop.is_set():
            try:
                job = await self.db.claim_next_job()
                if not job:
                    await asyncio.sleep(0.4)
                    continue
                await self._run_job(wid, job)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[worker {wid}] loop error: {e}", flush=True)
                await asyncio.sleep(1.0)

    async def _run_job(self, wid: int, job: dict) -> None:
        settings = load_settings()
        jid = job["id"]
        text = job["text"]
        voice = job.get("voice") or settings["default_voice"]
        model = job.get("model") or settings["default_model"]
        lang = job.get("lang") or settings["default_lang"]
        speed = float(job.get("speed") or 1.0)
        max_attempts = 6
        t0 = time.time()

        for attempt in range(1, max_attempts + 1):
            if self._stop.is_set():
                return
            slot = await pool.lease(timeout=90.0)
            if not slot:
                await self.db.update_job(
                    jid,
                    status="failed",
                    error="no proxy slot available",
                    finished_at=_iso(),
                    duration_ms=int((time.time() - t0) * 1000),
                )
                return

            out = AUDIO_DIR / f"{jid}.mp3"
            try:
                print(
                    f"[W{wid}] job={jid[:8]} attempt={attempt} slot={slot.id} "
                    f"exit={slot.exit_ip or '?'}",
                    flush=True,
                )
                solve_token, call_tts = _import_tts()
                token = await solve_token(slot.proxy_url)
                audio = await call_tts(
                    text, token, slot.proxy_url, voice, model, lang, speed
                )
                out.write_bytes(audio)
                await pool.release_ok(slot)
                await self.db.update_job(
                    jid,
                    status="done",
                    audio_path=str(out),
                    audio_bytes=len(audio),
                    proxy_id=slot.id,
                    exit_ip=slot.exit_ip,
                    finished_at=_iso(),
                    duration_ms=int((time.time() - t0) * 1000),
                    error=None,
                )
                if job.get("api_key_id"):
                    await self.db.record_success_usage(
                        job["api_key_id"], jid, job["text_chars"]
                    )
                print(f"[W{wid}] DONE {jid[:8]} {len(audio)}B slot={slot.id}", flush=True)
                return
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                err_short = " ".join(err.replace("\n", " ").split())[:300]
                print(f"[W{wid}] FAIL {jid[:8]}: {err_short[:180]}", flush=True)
                # Known Windows fix: pin playwright<1.61 (isMobile viewport crash)
                if "isMobile" in err or "setDefaultViewport" in err:
                    err_short = (
                        "Camoufox/Playwright viewport bug (isMobile). "
                        "On Windows run: fix_playwright.bat then restart start_all.bat "
                        f"| {err_short[:160]}"
                    )
                if _is_block(e):
                    await pool.release_block_and_rotate(slot, err_short)
                    await self.db.update_job(jid, error=err_short, proxy_id=slot.id)
                    await asyncio.sleep(0.5)
                    continue  # retry job on new IP / other slot
                if _is_transient(e):
                    await pool.release_transient(slot, err_short)
                    await asyncio.sleep(min(1.0 * attempt, 4.0))
                    continue
                await pool.release_transient(slot, err_short)
                await self.db.update_job(
                    jid,
                    status="failed",
                    error=err_short,
                    proxy_id=slot.id,
                    finished_at=_iso(),
                    duration_ms=int((time.time() - t0) * 1000),
                )
                return

        await self.db.update_job(
            jid,
            status="failed",
            error="max attempts exceeded",
            finished_at=_iso(),
            duration_ms=int((time.time() - t0) * 1000),
        )


def _iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
