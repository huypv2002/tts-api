from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Callable, Optional

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

# Shared wake signal when new jobs are enqueued (low latency claim)
job_wakeup = asyncio.Event()

_solve_token: Optional[Callable] = None
_call_tts: Optional[Callable] = None
_warm_hsw: Optional[Callable] = None


def notify_job() -> None:
    """Call after create_job so workers wake immediately."""
    try:
        job_wakeup.set()
    except Exception:
        pass


def _is_transient(err: BaseException) -> bool:
    blob = f"{type(err).__name__}: {err}".lower()
    return any(m in blob for m in TRANSIENT_MARKERS)


def _is_block(err: BaseException) -> bool:
    if _is_transient(err):
        return False
    msg = str(err).lower()
    return any(m in msg for m in BLOCK_MARKERS)


def _import_tts():
    """Import once and cache (avoid re-import cost / SystemExit path)."""
    global _solve_token, _call_tts, _warm_hsw
    if _solve_token is not None and _call_tts is not None:
        return _solve_token, _call_tts
    if str(PARENT) not in sys.path:
        sys.path.insert(0, str(PARENT))
    try:
        from fast_tts import call_tts, solve_token, warm_hsw  # noqa: WPS433
    except SystemExit as e:
        raise RuntimeError(
            "fast_tts failed to import (install camoufox + tls-client on the server: "
            "pip install camoufox tls-client && camoufox fetch)"
        ) from e
    except Exception as e:
        raise RuntimeError(f"fast_tts import error: {e}") from e
    _solve_token = solve_token
    _call_tts = call_tts
    _warm_hsw = warm_hsw
    return solve_token, call_tts


class WorkerManager:
    def __init__(self, db: Database):
        self.db = db
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self._settings_cache: dict | None = None
        self._settings_at = 0.0

    def _settings(self) -> dict:
        # cache settings 5s to avoid disk thrash
        now = time.time()
        if self._settings_cache and (now - self._settings_at) < 5:
            return self._settings_cache
        self._settings_cache = load_settings()
        self._settings_at = now
        return self._settings_cache

    async def start(self) -> None:
        settings = self._settings()
        n = max(1, int(settings.get("worker_count") or 4))
        self._stop.clear()
        # warm HSW browser in background (don't block API start)
        asyncio.create_task(self._warm_background(), name="hsw-warm")
        for i in range(n):
            self._tasks.append(asyncio.create_task(self._loop(i + 1), name=f"w{i+1}"))

    async def _warm_background(self) -> None:
        await asyncio.sleep(0.5)
        try:
            _import_tts()
            if _warm_hsw is None:
                return
            # warm with first ready proxy if any
            proxy = None
            for s in pool.slots.values():
                if s.enabled and s.proxy_url:
                    proxy = s.proxy_url
                    break
                if s.enabled:
                    try:
                        proxy = await pool.ensure_url(s)
                        break
                    except Exception:
                        continue
            await _warm_hsw(proxy)
            print("[worker] HSW warm complete", flush=True)
        except Exception as e:
            print(f"[worker] HSW warm skip: {e}", flush=True)

    async def stop(self) -> None:
        self._stop.set()
        job_wakeup.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _loop(self, wid: int) -> None:
        while not self._stop.is_set():
            try:
                job = await self.db.claim_next_job()
                if not job:
                    # sleep until notify or short poll (faster than fixed 400ms)
                    job_wakeup.clear()
                    try:
                        await asyncio.wait_for(job_wakeup.wait(), timeout=0.15)
                    except asyncio.TimeoutError:
                        pass
                    continue
                await self._run_job(wid, job)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[worker {wid}] loop error: {e}", flush=True)
                await asyncio.sleep(0.5)

    async def _run_job(self, wid: int, job: dict) -> None:
        settings = self._settings()
        jid = job["id"]
        text = job["text"]
        voice = job.get("voice") or settings["default_voice"]
        model = job.get("model") or settings["default_model"]
        lang = job.get("lang") or settings["default_lang"]
        speed = float(job.get("speed") or 1.0)
        max_attempts = 6
        t0 = time.time()
        solve_token, call_tts = _import_tts()

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
                t_token = time.time()
                token = await solve_token(slot.proxy_url)
                t_tts = time.time()
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
                print(
                    f"[W{wid}] DONE {jid[:8]} {len(audio)}B slot={slot.id} "
                    f"token={t_tts-t_token:.1f}s tts={time.time()-t_tts:.1f}s "
                    f"total={time.time()-t0:.1f}s",
                    flush=True,
                )
                return
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                err_short = " ".join(err.replace("\n", " ").split())[:300]
                print(f"[W{wid}] FAIL {jid[:8]}: {err_short[:180]}", flush=True)
                if "isMobile" in err or "setDefaultViewport" in err:
                    err_short = (
                        "Camoufox/Playwright viewport bug (isMobile). "
                        "On Windows run: fix_playwright.bat then restart start_all.bat "
                        f"| {err_short[:160]}"
                    )
                if _is_block(e):
                    await pool.release_block_and_rotate(slot, err_short)
                    await self.db.update_job(jid, error=err_short, proxy_id=slot.id)
                    await asyncio.sleep(0.3)
                    continue
                if _is_transient(e):
                    await pool.release_transient(slot, err_short)
                    await asyncio.sleep(min(0.5 * attempt, 2.5))
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
