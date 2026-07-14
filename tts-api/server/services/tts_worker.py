from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from ..config import AUDIO_DIR, PARENT, load_settings
from ..db import Database
from .proxy_pool import ProxySlot, SlotState, pool

# IP / edge blocks — must rotate exit IP then retry SAME job
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
    "http 403",
    "http 401",
    "http 429",
    "forbidden",
    "access denied",
    "image_challenge",
    "unauthorized",
)

# Network flake — soft retry; rotate after repeated hits on same slot
TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "i/o timeout",
    "dial tcp",
    "connection reset",
    "connection refused",
    "connection aborted",
    "broken pipe",
    "eof",
    "tlsclient",
    "connecterror",
    "proxyerror",
    "network is unreachable",
    "temporarily unavailable",
    "server disconnected",
    "remoteprotocolerror",
    "ssl",
    "certificate",
    "connect timeout",
    "read timeout",
    "pool timeout",
)

# True terminal — do not spin forever
FATAL_MARKERS = (
    "empty text",
    "empty after normalize",
)

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


def _blob(err: BaseException) -> str:
    return f"{type(err).__name__}: {err}".lower()


def _is_transient(err: BaseException) -> bool:
    return any(m in _blob(err) for m in TRANSIENT_MARKERS)


def _is_block(err: BaseException) -> bool:
    if _is_transient(err):
        return False
    return any(m in _blob(err) for m in BLOCK_MARKERS)


def _is_fatal(err: BaseException) -> bool:
    return any(m in _blob(err) for m in FATAL_MARKERS)


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
    """
    Job reliability policy (near-100% when proxy line is alive):

      for attempt in 1..job_max_attempts (default 50) within job_max_seconds (30m):
        lease proxy (heal stuck slots while waiting)
        solve_token + call_tts
        OK  → done
        block / 401 / image_challenge → rotate THIS slot → retry SAME job
        transient / dial fail → soft retry; every 2nd hit also rotate
        no proxy ready → wait & heal, NEVER fail until budget exhausted
        fatal config only → fail early
    """

    def __init__(self, db: Database):
        self.db = db
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self._settings_cache: dict | None = None
        self._settings_at = 0.0

    def _settings(self) -> dict:
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
        pool.start_background()
        asyncio.create_task(self._warm_background(), name="hsw-warm")
        for i in range(n):
            self._tasks.append(asyncio.create_task(self._loop(i + 1), name=f"w{i+1}"))

    async def _warm_background(self) -> None:
        await asyncio.sleep(0.5)
        try:
            _import_tts()
            if _warm_hsw is None:
                return
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

    async def _lease_dedicated(self, key_row: dict) -> ProxySlot:
        """Build a one-off ProxySlot from api_key proxyxoay binding."""
        host = (key_row.get("proxy_host") or "").strip()
        port = int(key_row.get("proxy_port") or 0)
        user = (key_row.get("proxy_username") or "").strip()
        pw = (key_row.get("proxy_password") or "").strip()
        api_key = (key_row.get("proxy_api_key") or "").strip()
        provider = key_row.get("proxy_provider") or "proxyxoay_net"
        # For shop provider, host/port/user may not be set (resolved dynamically)
        if provider != "proxyxoay_shop" and (not host or not port or not user):
            raise RuntimeError("incomplete dedicated proxy on api key")
        if provider == "proxyxoay_shop" and not api_key:
            raise RuntimeError("shop dedicated proxy requires proxy_api_key")
        slot = ProxySlot(
            id=f"key{key_row['id']}",
            label=key_row.get("proxy_label") or f"account-{key_row.get('name')}",
            enabled=True,
            provider=provider,
            api_key=api_key,
            username=user,
            password=pw,
            host=host,
            port=port,
            shop_nhamang=key_row.get("proxy_shop_nhamang") or "random",
            shop_tinhthanh=int(key_row.get("proxy_shop_tinhthanh") or 0),
            shop_whitelist=key_row.get("proxy_shop_whitelist") or "",
            shop_method=key_row.get("proxy_shop_method") or "GET",
        )
        if api_key:
            await asyncio.to_thread(pool.resolve_url_sync, slot)
        else:
            slot.proxy_url = pool._build_url(slot)
        if not slot.proxy_url:
            slot.proxy_url = f"http://{user}:{pw}@{host}:{port}"
        slot.set_state(SlotState.BUSY)
        return slot

    async def _run_job(self, wid: int, job: dict) -> None:
        settings = self._settings()
        jid = job["id"]
        text = job["text"]
        voice = job.get("voice") or settings["default_voice"]
        model = job.get("model") or settings["default_model"]
        lang = job.get("lang") or settings["default_lang"]
        speed = float(job.get("speed") or 1.0)

        max_attempts = max(5, int(settings.get("job_max_attempts") or 50))
        max_seconds = max(60, int(settings.get("job_max_seconds") or 1800))
        lease_timeout = float(settings.get("proxy_lease_timeout_s") or 25)
        t0 = time.time()
        solve_token, call_tts = _import_tts()

        attempt = 0
        soft_on_slot = 0  # consecutive soft fails without rotate
        dedicated = False
        key_row = None
        if job.get("api_key_id"):
            try:
                key_row = await self.db.get_api_key(int(job["api_key_id"]))
            except Exception:
                key_row = None

        while attempt < max_attempts and (time.time() - t0) < max_seconds:
            if self._stop.is_set():
                return
            attempt += 1

            slot = None
            dedicated = False
            # Prefer account-bound proxyxoay (API key binding)
            has_dedicated_proxy = key_row and (
                (key_row.get("proxy_host") and key_row.get("proxy_username"))
                or (key_row.get("proxy_provider") == "proxyxoay_shop" and key_row.get("proxy_api_key"))
            )
            if has_dedicated_proxy:
                try:
                    slot = await self._lease_dedicated(key_row)
                    dedicated = True
                except Exception as e:
                    print(
                        f"[W{wid}] dedicated proxy fail: {e} — fallback pool",
                        flush=True,
                    )
                    slot = None
                    dedicated = False
            if slot is None:
                slot = await pool.lease(timeout=lease_timeout)
            if not slot:
                # Do NOT fail the job — heal + wait for proxy to come back
                await pool.heal_stuck_slots()
                wait_msg = (
                    f"waiting for proxy (attempt {attempt}/{max_attempts}, "
                    f"elapsed {int(time.time()-t0)}s)"
                )
                print(f"[W{wid}] {jid[:8]} {wait_msg}", flush=True)
                await self.db.update_job(
                    jid,
                    error=wait_msg,
                    attempts=attempt,
                )
                await asyncio.sleep(min(2.0 + attempt * 0.1, 8.0))
                continue

            out = AUDIO_DIR / f"{jid}.mp3"
            try:
                print(
                    f"[W{wid}] job={jid[:8]} attempt={attempt}/{max_attempts} "
                    f"slot={slot.id} exit={slot.exit_ip or '?'}",
                    flush=True,
                )
                await self.db.update_job(
                    jid,
                    error=None,
                    proxy_id=slot.id,
                    exit_ip=slot.exit_ip,
                    attempts=attempt,
                )
                t_token = time.time()
                token = await solve_token(slot.proxy_url)
                t_tts = time.time()
                audio = await call_tts(
                    text, token, slot.proxy_url, voice, model, lang, speed
                )
                out.write_bytes(audio)
                if dedicated:
                    pass  # dedicated slot not in pool
                else:
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
                    attempts=attempt,
                )
                if job.get("api_key_id"):
                    await self.db.record_success_usage(
                        job["api_key_id"], jid, job["text_chars"]
                    )
                print(
                    f"[W{wid}] DONE {jid[:8]} {len(audio)}B slot={slot.id} "
                    f"token={t_tts-t_token:.1f}s tts={time.time()-t_tts:.1f}s "
                    f"total={time.time()-t0:.1f}s attempts={attempt}",
                    flush=True,
                )
                return
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                err_short = " ".join(err.replace("\n", " ").split())[:300]
                print(
                    f"[W{wid}] FAIL {jid[:8]} a={attempt}: {err_short[:160]}",
                    flush=True,
                )

                async def _release_soft(msg: str) -> None:
                    if dedicated:
                        return
                    await pool.release_transient(slot, msg)

                async def _release_rotate(msg: str) -> None:
                    if dedicated:
                        # rotate dedicated proxyxoay line via provider API
                        try:
                            await asyncio.to_thread(pool.rotate_sync, slot)
                        except Exception as re:
                            print(f"[W{wid}] dedicated rotate: {re}", flush=True)
                        return
                    await pool.release_block_and_rotate(slot, msg)

                if "isMobile" in err or "setDefaultViewport" in err:
                    err_short = (
                        "Camoufox/Playwright viewport bug (isMobile). "
                        "On Windows: fix_playwright.bat then restart | "
                        f"{err_short[:120]}"
                    )
                    await _release_soft(err_short)
                    await self.db.update_job(jid, error=err_short, proxy_id=slot.id)
                    if attempt >= 3:
                        await self.db.update_job(
                            jid,
                            status="failed",
                            error=err_short,
                            finished_at=_iso(),
                            duration_ms=int((time.time() - t0) * 1000),
                            attempts=attempt,
                        )
                        return
                    await asyncio.sleep(1.0)
                    continue

                if _is_fatal(e):
                    await _release_soft(err_short)
                    await self.db.update_job(
                        jid,
                        status="failed",
                        error=err_short,
                        proxy_id=slot.id,
                        finished_at=_iso(),
                        duration_ms=int((time.time() - t0) * 1000),
                        attempts=attempt,
                    )
                    return

                # ── block / captcha / 401 → rotate IP, retry SAME job ──
                if _is_block(e):
                    soft_on_slot = 0
                    await _release_rotate(err_short)
                    await self.db.update_job(
                        jid,
                        error=f"block→rotate a{attempt}: {err_short[:180]}",
                        proxy_id=slot.id,
                        attempts=attempt,
                    )
                    await asyncio.sleep(0.4)
                    continue

                # ── transient network ──
                if _is_transient(e):
                    soft_on_slot += 1
                    if soft_on_slot >= 2:
                        soft_on_slot = 0
                        await _release_rotate(f"transient×2→rotate: {err_short}")
                        await self.db.update_job(
                            jid,
                            error=f"transient→rotate a{attempt}: {err_short[:160]}",
                            proxy_id=slot.id,
                            attempts=attempt,
                        )
                    else:
                        await _release_soft(err_short)
                        await self.db.update_job(
                            jid,
                            error=f"transient a{attempt}: {err_short[:160]}",
                            proxy_id=slot.id,
                            attempts=attempt,
                        )
                    await asyncio.sleep(min(0.5 * attempt, 3.0))
                    continue

                # ── unknown: assume risk → rotate + retry (do not drop job) ──
                soft_on_slot = 0
                await _release_rotate(f"unknown→rotate: {err_short}")
                await self.db.update_job(
                    jid,
                    error=f"retry a{attempt}: {err_short[:180]}",
                    proxy_id=slot.id,
                    attempts=attempt,
                )
                await asyncio.sleep(0.5)
                continue

        # Budget exhausted — last resort fail (should be rare)
        await self.db.update_job(
            jid,
            status="failed",
            error=(
                f"gave up after {attempt} attempts / {int(time.time()-t0)}s "
                f"(max_attempts={max_attempts}, max_seconds={max_seconds})"
            ),
            finished_at=_iso(),
            duration_ms=int((time.time() - t0) * 1000),
            attempts=attempt,
        )


def _iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
