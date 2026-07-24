# -*- coding: utf-8 -*-
"""
local_tts.py — Local TTS batch helpers.

Kiến trúc đã sửa (gen 100%):
  _ProxyGate  — lưu proxy URL hiện tại, cập nhật ngay sau rotate,
                expose get_proxy() để worker luôn dùng IP mới nhất.
  synthesize_one — nhận gate, lấy proxy qua gate.get_proxy() mỗi lần gọi.
  synthesize_batch_async — truyền gate xuống worker; worker không dùng
                            closure proxy cũ nữa.
"""
from __future__ import annotations

import asyncio
import os
import random
import re
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from app_paths import ensure_sys_path  # noqa: E402

ensure_sys_path()

from fast_tts import (  # noqa: E402
    API_BASE,
    DEFAULT_MODEL,
    DEFAULT_VOICE,
    call_tts,
    close_hsw_farm,
    normalize_proxy,
    proxyxoay_net_change_ip,
    proxyxoay_net_from_status,
    solve_token,
    start_hsw_farm,
)


# ══════════════════════════════════════════════════════════════════════════════
# Voice info helper
# ══════════════════════════════════════════════════════════════════════════════

def fetch_voice_info(voice_id: str) -> dict:
    """Lấy metadata voice public (không cần API key)."""
    import json, urllib.error, urllib.request

    vid = (voice_id or "").strip()
    if not vid:
        raise ValueError("voice_id trống")
    if "voice_id=" in vid:
        m = re.search(r"voice_id=([A-Za-z0-9]+)", vid)
        if m:
            vid = m.group(1)
    elif "/" in vid:
        vid = vid.rstrip("/").split("/")[-1]

    url = f"{API_BASE}/v1/shared-voices/{vid}"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "curl/8.7.1"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="ignore")[:200]
        except Exception:
            pass
        if e.code == 404:
            raise RuntimeError(f"Không tìm thấy giọng «{vid}» trong thư viện public") from e
        raise RuntimeError(f"Lỗi máy chủ ({e.code}): {body or e.reason}") from e
    except Exception as e:
        raise RuntimeError(f"Lỗi mạng khi lấy thông tin giọng: {e}") from e

    if not isinstance(data, dict) or not data.get("voice_id"):
        raise RuntimeError("Dữ liệu giọng trả về không hợp lệ")

    langs = []
    for vl in data.get("verified_languages") or []:
        if isinstance(vl, dict) and vl.get("language"):
            langs.append(str(vl["language"]))
    seen: set[str] = set()
    lang_list = [x for x in langs if not (x in seen or seen.add(x))]  # type: ignore[func-returns-value]
    primary_lang = data.get("language") or (lang_list[0] if lang_list else "en") or "en"

    return {
        "voice_id": data.get("voice_id") or vid,
        "name": data.get("name") or vid,
        "description": data.get("description") or "",
        "language": primary_lang,
        "locale": data.get("locale") or "",
        "gender": data.get("gender") or "",
        "age": data.get("age") or "",
        "accent": data.get("accent") or "",
        "category": data.get("category") or "",
        "use_case": data.get("use_case") or "",
        "descriptive": data.get("descriptive") or "",
        "preview_url": data.get("preview_url") or "",
        "verified_languages": lang_list,
        "free_users_allowed": bool(data.get("free_users_allowed")),
        "raw": data,
    }


# ══════════════════════════════════════════════════════════════════════════════
# HSW Farm helpers — per event-loop singleton
# ══════════════════════════════════════════════════════════════════════════════

_farm_ready = False
_farm_loop_id: Optional[int] = None
_farm_size = 0
_farm_gate = threading.Lock()
_loop_locks: dict[int, asyncio.Lock] = {}


def _loop_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lid = id(loop)
    if lid not in _loop_locks:
        _loop_locks[lid] = asyncio.Lock()
    return _loop_locks[lid]


async def ensure_farm(proxy: Optional[str] = None, hsw_workers: int = 2) -> None:
    """Start HSW farm once per event loop (safe with concurrent tasks)."""
    global _farm_ready, _farm_loop_id, _farm_size
    loop = asyncio.get_running_loop()
    lid = id(loop)
    need = max(1, min(5, int(hsw_workers or 1)))

    async with _loop_lock():
        if _farm_ready and _farm_loop_id == lid and _farm_size >= need:
            return
        if _farm_ready and _farm_loop_id != lid:
            try:
                await close_hsw_farm()
            except Exception:
                pass
            _farm_ready = False
            _farm_loop_id = None
            _farm_size = 0

        if not _farm_ready or _farm_size < need:
            if _farm_ready:
                try:
                    await close_hsw_farm()
                except Exception:
                    pass
                _farm_ready = False
            await start_hsw_farm(size=need, proxy_http=proxy, via_proxy=False, warm=True)
            with _farm_gate:
                _farm_ready = True
                _farm_loop_id = lid
                _farm_size = need


# ══════════════════════════════════════════════════════════════════════════════
# Error classifiers
# ══════════════════════════════════════════════════════════════════════════════

_THROTTLE_MARKERS = (
    "401", "429", "403",
    "unusual", "rate", "too many",
    "image_challenge", "proxy",
    "timeout", "connect", "reset",
    "landing", "sign_in_required",
    "tts_landing_limit", "getcaptcha", "hsw",
    "net_limit", "net_auth", "net_throttle", "net_captcha", "net_challenge", "net_http",
    "net_proxy", "net_pause", "net_stale", "net_runtime",
)

_LANDING_MARKERS = (
    "tts_landing_limit",
    "landing page",
    "sign_in_required",
    "limit of available requests",
    "net_limit",
    "net_auth",
)


def _is_throttle(err: Exception | str) -> bool:
    msg = str(err).lower()
    return any(x in msg for x in _THROTTLE_MARKERS)


def _is_landing(err: Exception | str) -> bool:
    msg = str(err).lower()
    return any(x in msg for x in _LANDING_MARKERS)


def _parse_cooldown(err: Exception | str, default: float = 45.0) -> float:
    msg = str(err)
    m = re.search(r"(\d+)\s*(phút|minute|min)", msg, re.I)
    if m:
        return max(default, float(m.group(1)) * 60 + 5)
    m = re.search(r"(\d+)\s*(giây|second|sec)", msg, re.I)
    if m:
        return max(8.0, float(m.group(1)) + 2)
    return default


# ══════════════════════════════════════════════════════════════════════════════
# _ProxyGate — kiến trúc mới
# ══════════════════════════════════════════════════════════════════════════════

class _ProxyGate:
    """
    Cổng proxy toàn batch với proxy URL được cập nhật sau mỗi rotate.

    THAY ĐỔI SO VỚI CŨ:
    - _current_proxy: proxy URL đang dùng, được cập nhật sau rotate.
    - get_proxy(): worker gọi mỗi lần thử để luôn dùng IP mới nhất.
    - Sau rotate: cập nhật _current_proxy ngay → worker tiếp theo dùng
      IP mới, không cần khởi động lại batch.
    """

    def __init__(self, initial_proxy: Optional[str], max_workers: int, proxy_api_key: str = ""):
        from fast_tts import log
        self._log = log
        self.max_workers = max(1, min(5, int(max_workers or 1)))
        self.cur_workers = self.max_workers
        self.proxy_api_key = (proxy_api_key or "").strip()

        # ── proxy state (trái tim của fix) ──────────────────────────────────
        self._current_proxy: Optional[str] = normalize_proxy(initial_proxy)
        self._proxy_lock = asyncio.Lock()   # bảo vệ _current_proxy khi cập nhật

        # ── concurrency / pause ──────────────────────────────────────────────
        self._active = 0
        self._cond = asyncio.Condition()
        self._recover_lock = asyncio.Lock()
        self._paused_until = 0.0

        # ── stats ────────────────────────────────────────────────────────────
        self._fail_streak = 0
        self._ok_streak = 0
        self._last_rotate = 0.0
        self.min_rotate_gap = 55.0

    # ── proxy access ─────────────────────────────────────────────────────────

    def get_proxy(self) -> Optional[str]:
        """Worker gọi mỗi lần thử — trả về proxy URL hiện tại (sau rotate)."""
        return self._current_proxy

    async def _do_rotate(self, landing: bool) -> None:
        """
        Đổi exit IP, cập nhật _current_proxy, trả về sau khi IP settle.
        Không throws — nếu rotate fail thì chờ cooldown rồi thử tiếp.
        """
        if not self.proxy_api_key:
            return

        gap_left = self.min_rotate_gap - (time.time() - self._last_rotate)
        if gap_left > 0:
            self._log(f"  [proxy] chờ {gap_left:.0f}s rotate cooldown…")
            await asyncio.sleep(gap_left)

        self._log("  [net] rotate…")
        try:
            await asyncio.to_thread(proxyxoay_net_change_ip, self.proxy_api_key)
            self._last_rotate = time.time()

            settle = 12.0 if landing else 6.0
            self._log(f"  [proxy] đợi IP settle {settle:.0f}s…")
            await asyncio.sleep(settle)

            new_url = await asyncio.to_thread(proxyxoay_net_from_status, self.proxy_api_key)
            async with self._proxy_lock:
                self._current_proxy = new_url
            self._log("  [net] rotate ok")

        except Exception as e:
            cool = _parse_cooldown(e, default=180.0 if landing else 90.0)
            self._log(f"  [proxy] chưa đổi IP được — chờ {cool:.0f}s ({e})")
            await asyncio.sleep(cool)
            # thử lấy lại URL mới nhất (IP có thể tự đổi trong lúc chờ)
            try:
                new_url = await asyncio.to_thread(proxyxoay_net_from_status, self.proxy_api_key)
                async with self._proxy_lock:
                    self._current_proxy = new_url
            except Exception:
                pass

    # ── concurrency control ───────────────────────────────────────────────────

    async def acquire(self, should_stop: Optional[Callable] = None) -> None:
        async with self._cond:
            while True:
                if should_stop and should_stop():
                    raise asyncio.CancelledError("đã dừng")
                now = time.time()
                wait = self._paused_until - now
                if wait > 0:
                    self._log(f"  [proxy] đang chờ proxy ổn định… còn {wait:.0f}s")
                    try:
                        await asyncio.wait_for(self._cond.wait(), timeout=min(2.0, wait))
                    except asyncio.TimeoutError:
                        pass
                    continue
                if self._active >= self.cur_workers:
                    await self._cond.wait()
                    continue
                self._active += 1
                return

    async def release(self) -> None:
        async with self._cond:
            self._active = max(0, self._active - 1)
            self._cond.notify_all()

    async def report_ok(self) -> None:
        self._fail_streak = 0
        self._ok_streak += 1
        if self._ok_streak >= 5 and self.cur_workers < self.max_workers:
            async with self._cond:
                self.cur_workers = min(self.max_workers, self.cur_workers + 1)
                self._cond.notify_all()
            self._ok_streak = 0
            self._log(f"  [proxy] tăng luồng → {self.cur_workers}/{self.max_workers}")

    async def report_fail(self, err: Exception | str) -> None:
        if not _is_throttle(err):
            return
        # Chỉ 1 coroutine recover tại một thời điểm.
        # Các worker khác chờ xong pause rồi return — không rotate thêm.
        if self._recover_lock.locked():
            # Chờ đến khi lock được nhả VÀ pause hết
            deadline = time.time() + 600  # tối đa 10 phút chờ
            while (self._recover_lock.locked() or time.time() < self._paused_until) \
                    and time.time() < deadline:
                await asyncio.sleep(0.5)
            return

        async with self._recover_lock:
            self._fail_streak += 1
            self._ok_streak = 0
            landing = _is_landing(err)

            # Giảm luồng ngay
            async with self._cond:
                self.cur_workers = 1
                self._cond.notify_all()
            self._log("  [proxy] giảm còn 1 luồng (tránh đốt IP free landing)")

            # Rotate IP (cập nhật _current_proxy bên trong)
            await self._do_rotate(landing)

            # Thời gian pause thêm sau khi IP đã settle
            if landing:
                pause_s = max(45.0, 60.0 + self._fail_streak * 20.0)
                pause_s = min(pause_s, 240.0)
            else:
                pause_s = max(8.0, 10.0 + self._fail_streak * 8.0)
            if "429" in str(err).lower():
                pause_s = max(pause_s, 30.0)

            self._paused_until = time.time() + pause_s
            why = "hết lượt free landing" if landing else "token/IP bị block"
            self._log(
                f"  [proxy] ⏸ TẠM DỪNG tất cả {pause_s:.0f}s "
                f"({why}, streak={self._fail_streak})"
            )

            end = self._paused_until
            while time.time() < end:
                left = end - time.time()
                self._log(f"  [proxy] …còn {left:.0f}s trước khi chạy lại")
                await asyncio.sleep(min(10.0, max(0.5, left)))

            async with self._cond:
                self._paused_until = 0.0
                self._cond.notify_all()
            self._log("  [proxy] ▶ proxy sẵn sàng — tiếp tục tạo audio (1 luồng)")


# ══════════════════════════════════════════════════════════════════════════════
# synthesize_one — nhận gate thay vì proxy string cố định
# ══════════════════════════════════════════════════════════════════════════════

async def synthesize_one(
    text: str,
    out_path: str,
    gate: "_ProxyGate",
    voice: str = DEFAULT_VOICE,
    model: str = DEFAULT_MODEL,
    lang: str = "en",
    speed: float = 1.0,
    hsw_workers: int = 5,
) -> dict:
    """
    1 token = 1 TTS. Proxy lấy từ gate.get_proxy() — luôn dùng IP mới nhất.

    QUAN TRỌNG: gọi ensure_farm(gate.get_proxy()) mỗi lần thử để farm biết
    IP hiện tại. Farm chạy no-proxy mặc định nên không thực sự cần thiết,
    nhưng đảm bảo farm sống nếu loop bị reset.
    """
    proxy = gate.get_proxy()
    await ensure_farm(proxy, hsw_workers=hsw_workers)

    # Lấy proxy mới nhất ngay trước solve (gate có thể đã rotate)
    proxy = gate.get_proxy()
    token = await solve_token(proxy)

    # Lấy lại một lần nữa sau solve (nếu rotate xảy ra concurrent)
    proxy = gate.get_proxy()
    # stability/similarity: không truyền — TTS dùng mặc định API
    audio = await call_tts(text, token, proxy, voice, model, lang, speed)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_bytes(audio)
    return {"bytes": len(audio), "path": out_path}


# ══════════════════════════════════════════════════════════════════════════════
# synthesize_batch_async — truyền gate xuống worker
# ══════════════════════════════════════════════════════════════════════════════

async def synthesize_batch_async(
    jobs: list[dict],
    *,
    proxy: Optional[str],
    voice: str,
    model: str = DEFAULT_MODEL,
    lang: str = "en",
    speed: float = 1.0,
    workers: int = 5,
    hsw_workers: int = 5,
    proxy_api_key: str = "",
    max_attempts_per_job: int = 40,
    should_stop: Optional[Callable] = None,
    on_start: Optional[Callable] = None,
    on_done: Optional[Callable] = None,
    on_status: Optional[Callable] = None,
) -> tuple[int, int]:
    """
    Batch TTS với queue + ProxyGate. Lỗi 401/429 không bỏ đoạn:
    xếp lại hàng đợi, đổi IP, resume — đến khi xong hoặc hết lần thử.
    """
    from fast_tts import log

    proxy = normalize_proxy(proxy)
    await ensure_farm(proxy, hsw_workers=hsw_workers)

    # Resume: bỏ qua file đã có
    pending: list[dict] = []
    ok = fail = 0
    for j in jobs:
        p = j.get("out_path") or ""
        if p and Path(p).is_file() and Path(p).stat().st_size > 500:
            ok += 1
            if on_done:
                on_done(j["row"], True, p, "")
            continue
        j = dict(j)
        j["attempts"] = 0
        pending.append(j)

    if not pending:
        return ok, fail

    # Gate khởi tạo với proxy ban đầu; tự cập nhật sau rotate
    gate = _ProxyGate(
        initial_proxy=proxy,
        max_workers=workers,
        proxy_api_key=proxy_api_key or "",
    )

    q: asyncio.Queue[dict] = asyncio.Queue()
    for j in pending:
        await q.put(j)

    done_rows: set[int] = set()
    stats_lock = asyncio.Lock()
    target_todo = len(pending)
    finished_todo = 0
    workers_n = max(1, min(5, int(workers or 1)))

    async def worker_fn(wid: int) -> None:
        nonlocal ok, fail, finished_todo
        while True:
            if should_stop and should_stop():
                return
            async with stats_lock:
                if finished_todo >= target_todo:
                    return

            # Lấy job từ queue
            try:
                job = q.get_nowait()
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.35)
                async with stats_lock:
                    if finished_todo >= target_todo:
                        return
                if q.empty() and gate._active == 0:
                    await asyncio.sleep(0.5)
                    if q.empty() and gate._active == 0:
                        async with stats_lock:
                            if finished_todo >= target_todo:
                                return
                        return
                continue

            row = job["row"]
            if row in done_rows:
                continue

            # Chờ slot + pause
            try:
                await gate.acquire(should_stop=should_stop)
            except asyncio.CancelledError:
                await q.put(job)
                return

            try:
                if should_stop and should_stop():
                    await q.put(job)
                    return

                job["attempts"] = int(job.get("attempts") or 0) + 1
                att = job["attempts"]

                if on_status:
                    on_status(row, f"Đang tạo… (lần {att})")
                if on_start:
                    on_start(row)

                # ── HOT PATH: synthesize_one dùng gate.get_proxy() ──────────
                await synthesize_one(
                    text=job.get("text") or "",
                    out_path=job["out_path"],
                    gate=gate,                  # ← truyền gate, không proxy string
                    voice=voice,
                    model=model,
                    lang=lang,
                    speed=speed,
                    hsw_workers=hsw_workers,
                )
                await gate.report_ok()
                async with stats_lock:
                    if row not in done_rows:
                        done_rows.add(row)
                        ok += 1
                        finished_todo += 1
                if on_done:
                    on_done(row, True, job["out_path"], "")

            except Exception as e:
                try:
                    from user_safe import sanitize_user_error

                    msg = sanitize_user_error(e, fallback="Lỗi đoạn — đang thử lại…")
                except Exception:
                    msg = "Lỗi đoạn — đang thử lại…"
                log(f"  [batch W{wid}] đoạn {row+1} lỗi lần {att}: {type(e).__name__}")

                if _is_throttle(e):
                    if on_status:
                        on_status(row, "Đang chờ kết nối…")
                    await gate.report_fail(e)   # rotate + pause bên trong

                att_now = int(job.get("attempts") or 0)
                if att_now < max_attempts_per_job and not (should_stop and should_stop()):
                    if on_status:
                        on_status(row, f"Xếp lại (lần {att_now})")
                    await asyncio.sleep(0.3 + random.uniform(0, 0.5))
                    await q.put(job)
                else:
                    async with stats_lock:
                        if row not in done_rows:
                            done_rows.add(row)
                            fail += 1
                            finished_todo += 1
                    if on_done:
                        on_done(row, False, "", msg)
            finally:
                await gate.release()

    # Bắt đầu nhẹ: 2 luồng, tránh đốt IP free landing ngay từ đầu
    start_w = min(workers_n, 2)
    async with gate._cond:
        gate.cur_workers = start_w
    log(
        f"  [batch] {target_todo} đoạn · bắt đầu {start_w}/{workers_n} luồng · "
        f"retry ≤{max_attempts_per_job}/đoạn · "
        f"proxy_key={'có' if proxy_api_key else 'không'}"
    )

    tasks = [asyncio.create_task(worker_fn(i + 1), name=f"tts-w{i+1}") for i in range(workers_n)]
    await asyncio.gather(*tasks)
    return ok, fail


# ══════════════════════════════════════════════════════════════════════════════
# synthesize_one_sync — wrapper đồng bộ (backward compat)
# ══════════════════════════════════════════════════════════════════════════════

def synthesize_one_sync(
    text: str,
    out_path: str,
    proxy: Optional[str],
    voice: str = DEFAULT_VOICE,
    model: str = DEFAULT_MODEL,
    lang: str = "en",
    speed: float = 1.0,
    hsw_workers: int = 5,
) -> dict:
    """Single-shot sync helper. Dùng synthesize_batch_async cho multi-worker."""
    gate = _ProxyGate(
        initial_proxy=proxy,
        max_workers=1,
        proxy_api_key="",
    )

    async def _run() -> dict:
        await ensure_farm(gate.get_proxy(), hsw_workers=hsw_workers)
        return await synthesize_one(
            text, out_path, gate, voice, model, lang, speed, hsw_workers,
        )

    return asyncio.run(_run())


# ══════════════════════════════════════════════════════════════════════════════
# Farm shutdown helper
# ══════════════════════════════════════════════════════════════════════════════

async def shutdown_farm() -> None:
    global _farm_ready, _farm_loop_id, _farm_size
    try:
        await close_hsw_farm()
    except Exception:
        pass
    with _farm_gate:
        _farm_ready = False
        _farm_loop_id = None
        _farm_size = 0
    _loop_locks.clear()
