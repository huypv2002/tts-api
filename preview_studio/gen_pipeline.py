# -*- coding: utf-8 -*-
"""
Gen pipeline — concurrent TTS:

  • max_workers (admin) = số luồng TTS ĐỒNG THỜI
  • 1 proxy + max_workers=3 → 3 worker TTS dùng chung 1 proxy (đúng ý admin)
  • Nhiều proxy → chia đều workers (vd 2 proxy + 5 workers → 3+2)
  • Token pool / proxy ≈ số slot TTS (mint ∥ TTS)
  • 401 → rotate IP có lock (nhiều worker cùng lane không rotate chồng)
  • provider: proxyxoay_net | proxyxoay_shop | static

Một event loop / batch.
"""
from __future__ import annotations

import asyncio
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

from app_paths import ensure_sys_path  # noqa: E402

ensure_sys_path()

from fast_tts import (  # noqa: E402
    DEFAULT_MODEL,
    DEFAULT_VOICE,
    TokenPool,
    call_tts,
    close_hsw_farm,
    detect_proxy_provider,
    get_hsw_farm,
    log,
    normalize_proxy,
    parse_proxy_cooldown,
    resolve_proxy_line,
    rotate_proxy_line,
    start_hsw_farm,
)

CHANGE_IP_SETTLE = 8.0
LANDING_EXTRA_WAIT = 45.0
# Khoảng cách tối thiểu giữa 2 lần GỌI xoay (gói 1 phút = 60s)
# API còn trả thời gian còn lại (s/ms) — ưu tiên số từ API khi fail.
MIN_CHANGE_IP_GAP = 60.0
MIN_CHANGE_IP_GAP_SHOP = 60.0  # proxyxoay.shop gói xoay 1 phút/lần
MAX_ATTEMPTS_PER_JOB = 40
SOFT_FAIL_BEFORE_ROTATE = 3

TOKENS_PER_LANE = 3  # mặc định; sẽ scale theo số slot/proxy
MAX_WORKERS = 5  # hard cap = admin max_workers
MAX_PROXIES = 5
MAX_HSW_PAGES = 8
# Alias cũ
MAX_LANES = MAX_WORKERS

# Nối đuôi: lệch pha giữa các luồng TTS (giây)
STAGGER_MIN_S = 1.0
STAGGER_MAX_S = 3.0


def _host_of(proxy: Optional[str]) -> str:
    if not proxy:
        return "direct"
    s = proxy.split("@")[-1]
    return s.rsplit(":", 1)[0]


def _is_hard_401(err: Exception | str) -> bool:
    """401 / landing / unusual → cần đổi IP (không chỉ soft retry)."""
    m = str(err).lower()
    return (
        "401" in m
        or "tts_landing" in m
        or "sign_in_required" in m
        or "landing page" in m
        or "unusual" in m
        or "detected_unusual" in m
    )


def _is_retryable(err: Exception | str) -> bool:
    m = str(err).lower()
    return any(
        x in m
        for x in (
            "401",
            "429",
            "landing",
            "sign_in",
            "getcaptcha",
            "image_challenge",
            "timeout",
            "connect",
            "reset",
            "proxy",
            "hsw",
            "unusual",
            "rate",
            "token-pool",
            "starve",
            "proxyxoay",
        )
    )


def _parse_wait(err: Exception | str, default: float = 60.0) -> float:
    """
    Thời gian CÒN LẠI đến lần xoay được phép (giây).
    Ưu tiên parse_proxy_cooldown (ms/s/field API / "Con 35s").
    Gói 1 phút xoay 1 lần → thường 0–60s, không hardcode 120.
    """
    return float(parse_proxy_cooldown(err, default=default))


def _normalize_line(raw: dict | str) -> dict:
    """Accept str URL or full proxy dict (net / shop / static)."""
    if isinstance(raw, str):
        s = (raw or "").strip()
        # accounts.build_proxy_url đôi khi trả shop://{api_key}
        if s.startswith("shop://"):
            key = s[len("shop://") :].strip()
            return {
                "id": "px_shop",
                "provider": "proxyxoay_shop",
                "api_key": key,
                "url": "",
            }
        return {
            "id": "px_inline",
            "url": normalize_proxy(s) or s,
            "provider": "static",
            "api_key": "",
        }
    line = dict(raw)
    # Bỏ url rác shop:// nếu lỡ gắn
    u = str(line.get("url") or "")
    if u.startswith("shop://"):
        key = u[len("shop://") :].strip()
        if key and not line.get("api_key"):
            line["api_key"] = key
        line["url"] = ""
        line["provider"] = line.get("provider") or "proxyxoay_shop"
    line["provider"] = detect_proxy_provider(
        line.get("provider"), line.get("host")
    )
    line["api_key"] = (line.get("api_key") or "").strip()
    return line


def _distribute_slots(total_workers: int, n_proxies: int) -> list[int]:
    """
    Chia luồng TTS đều cho các proxy.
      1 proxy, 3 workers → [3]
      2 proxy, 5 workers → [3, 2]
      3 proxy, 2 workers → [1, 1]  (chỉ dùng 2 proxy đầu)
    """
    tw = max(1, int(total_workers))
    np = max(1, int(n_proxies))
    if tw <= np:
        return [1] * tw  # caller cắt lines[:tw]
    base, extra = divmod(tw, np)
    return [base + (1 if i < extra else 0) for i in range(np)]


class ProxyLane:
    """
    1 proxy key = 1 lane, CÓ THỂ nhiều worker TTS đồng thời:
      • TokenPool target ≥ số slot TTS
      • 3 worker trên 1 proxy = 3 TTS song song (admin max_workers=3)
      • 401 → PAUSE cả lane + dừng mint + rotate (retry đúng cooldown shop)
    """

    def __init__(self, line: dict, lane_id: int, tts_slots: int = 1):
        self.lane_id = lane_id
        self.tts_slots = max(1, min(MAX_WORKERS, int(tts_slots or 1)))
        self.line = _normalize_line(line)
        self.proxy_url = ""
        self.api_key = (self.line.get("api_key") or "").strip()
        self.provider = detect_proxy_provider(
            self.line.get("provider"), self.line.get("host")
        )
        self.line["provider"] = self.provider
        self.label = (
            self.line.get("label")
            or self.line.get("id")
            or f"lane{lane_id}"
        )
        self.pool: TokenPool | None = None
        # Mốc lần gọi API xoay/get gần nhất (shop get.php lúc start cũng tính)
        self._last_change = 0.0
        self._ok_on_ip = 0
        self._soft_fails = 0
        # Serialize rotate khi nhiều worker cùng lane
        self._rotate_lock = asyncio.Lock()
        self._rotate_gen = 0
        # Lane-wide pause: mọi worker phải chờ trước khi take/TTS
        self._paused_until = 0.0
        self._pause_reason = ""

    def _min_rotate_gap(self) -> float:
        """Gói xoay 1 phút → 60s (shop/net). Static: 0 (không xoay API)."""
        if self.provider == "proxyxoay_shop":
            return MIN_CHANGE_IP_GAP_SHOP
        if self.provider == "proxyxoay_net" and self.api_key:
            return MIN_CHANGE_IP_GAP
        return 0.0

    def _can_api_rotate(self) -> bool:
        if self.provider == "proxyxoay_shop" and self.api_key:
            return True
        if self.provider == "proxyxoay_net" and self.api_key:
            return True
        return False

    def _extend_pause(self, seconds: float, reason: str = "") -> None:
        until = time.time() + max(0.5, float(seconds or 0))
        if until > self._paused_until:
            self._paused_until = until
            self._pause_reason = reason or self._pause_reason or "pause"

    async def wait_ready(
        self,
        should_stop: Optional[Callable[[], bool]] = None,
        *,
        tag: str = "",
    ) -> None:
        """Chặn worker khi lane đang đổi IP / cooldown shop."""
        last_log = 0.0
        while True:
            left = self._paused_until - time.time()
            if left <= 0:
                return
            if should_stop and should_stop():
                raise asyncio.CancelledError("đã dừng")
            now = time.time()
            if now - last_log >= 5.0:
                log(
                    f"  [lane{self.lane_id}{('/' + tag) if tag else ''}] "
                    f"tạm dừng {left:.0f}s — {self._pause_reason or 'đổi IP'}"
                )
                last_log = now
            await asyncio.sleep(min(1.5, max(0.2, left)))

    async def start(self, farm, tokens_per_lane: int = 0) -> None:
        """
        Resolve exit IP. Shop: get.php (retry khi 101 cooldown).
        KHÔNG fallback vipvn7:8978 không auth — gây dial hcaptcha fail.
        """
        last_err: Exception | None = None
        for attempt in range(1, 5):
            try:
                self.proxy_url = await asyncio.to_thread(resolve_proxy_line, self.line)
                last_err = None
                break
            except Exception as e:
                last_err = e
                msg = str(e)
                wait_s = _parse_wait(e, default=60.0)
                # 101 / cooldown: đợi rồi gọi lại get.php — không static fallback
                if (
                    self.provider == "proxyxoay_shop"
                    and (
                        "status=101" in msg
                        or "status=102" in msg
                        or "doi proxy" in msg.lower()
                        or "wait_s=" in msg
                    )
                ):
                    if "status=102" in msg or "khong ton tai" in msg.lower():
                        log(
                            f"  [lane{self.lane_id}] SHOP KEY INVALID (102) — "
                            f"đổi key trên admin Proxies · {msg[:120]}"
                        )
                        raise RuntimeError(
                            f"Proxy shop key không tồn tại/hết hạn (102). "
                            f"Sửa API key trên admin → login lại studio. Chi tiết: {msg}"
                        ) from e
                    # cooldown
                    sleep_for = max(3.0, min(90.0, wait_s + 1.5))
                    log(
                        f"  [lane{self.lane_id}] shop cooldown "
                        f"({attempt}/4) chờ {sleep_for:.0f}s — {msg[:100]}"
                    )
                    await asyncio.sleep(sleep_for)
                    continue
                # net / static: chỉ fallback khi có user+pass thật (không bare host)
                host = (self.line.get("host") or "").strip()
                port = int(self.line.get("port") or 0)
                user = (self.line.get("username") or "").strip()
                pw = (self.line.get("password") or "").strip()
                if (
                    self.provider != "proxyxoay_shop"
                    and host
                    and port
                    and user
                    and pw
                ):
                    self.proxy_url = f"http://{user}:{pw}@{host}:{port}"
                    log(
                        f"  [lane{self.lane_id}] resolve API fail, "
                        f"static auth URL: {e}"
                    )
                    last_err = None
                    break
                if attempt >= 4:
                    break
                log(f"  [lane{self.lane_id}] resolve retry {attempt}/4: {msg[:120]}")
                await asyncio.sleep(min(15.0, 2.0 * attempt))
        if last_err is not None or not getattr(self, "proxy_url", None):
            raise RuntimeError(
                f"Không resolve được proxy lane{self.lane_id} "
                f"({self.label}/{self.provider}): {last_err}"
            ) from last_err
        self.line["url"] = self.proxy_url
        # shop: get.php lúc start đã tốn 1 slot chu kỳ xoay → chặn spam ngay
        # net: status chỉ đọc IP hiện tại, chưa change-ip → _last_change=0 OK
        if self.provider == "proxyxoay_shop" and self.api_key:
            self._last_change = time.time()
        # Pool ≥ số TTS song song (+1 buffer mint∥TTS), cap 6
        want = int(tokens_per_lane or 0)
        target = max(self.tts_slots, want or TOKENS_PER_LANE, self.tts_slots + 1)
        target = max(1, min(6, target))
        refillers = max(1, min(target, 3))
        self.pool = TokenPool(
            proxy=self.proxy_url,
            target=target,
            refillers=refillers,
            farm=farm,
        )
        await self.pool.start()
        gap = self._min_rotate_gap()
        log(
            f"  [lane{self.lane_id}] {self.label} · {self.provider} · "
            f"proxy={_host_of(self.proxy_url)} · "
            f"TTS×{self.tts_slots} · pool={target}"
            + (f" · xoay ≥{gap:.0f}s/lần" if gap else " · static/no-rotate-api")
        )

    async def stop(self) -> None:
        if self.pool is not None:
            try:
                await self.pool.stop()
            except Exception:
                pass
            self.pool = None

    async def on_success(self) -> None:
        self._ok_on_ip += 1
        self._soft_fails = 0

    async def on_401(self) -> None:
        """
        401 / unusual_activity:
          1) Pause + dừng mint ngay (không cho worker khác bắn IP chết)
          2) 1 worker rotate (retry đúng cooldown shop 35s…)
          3) Peer chỉ chờ pause xong
        """
        gen_before = self._rotate_gen
        # Pause sớm — kể cả worker chưa vào lock
        self._extend_pause(8.0, "401 — chuẩn bị đổi IP")
        if self.pool is not None:
            try:
                await self.pool.set_paused(True, reason="401")
            except Exception:
                pass

        async with self._rotate_lock:
            # Peer: đã có người rotate xong
            if self._rotate_gen != gen_before:
                log(
                    f"  [lane{self.lane_id}] 401 — peer đã xử lý, chờ lane sẵn sàng…"
                )
                await self.wait_ready()
                # đợi pool có token (mint lại sau resume)
                await asyncio.sleep(1.0 + random.uniform(0, 0.8))
                return
            log(
                f"  [lane{self.lane_id}] 401 sau {self._ok_on_ip} TTS OK — "
                f"PAUSE + đổi IP…"
            )
            self._ok_on_ip = 0
            self._soft_fails = 0
            # shop: settle ngắn hơn; unusual_activity cần IP mới thật
            extra = 12.0 if self.provider == "proxyxoay_shop" else LANDING_EXTRA_WAIT
            await self._rotate(reason="401/landing", extra_wait=extra)
            self._rotate_gen += 1

    async def on_soft_fail(self, err: Exception | str) -> None:
        self._soft_fails += 1
        # unusual_activity đôi khi vào soft path — treat như 401 nếu message khớp
        if _is_hard_401(err):
            await self.on_401()
            return
        if self._soft_fails >= SOFT_FAIL_BEFORE_ROTATE and self._can_api_rotate():
            gen_before = self._rotate_gen
            self._extend_pause(6.0, "lỗi lặp — đổi IP")
            if self.pool is not None:
                try:
                    await self.pool.set_paused(True, reason="soft-fail-rotate")
                except Exception:
                    pass
            async with self._rotate_lock:
                if self._rotate_gen != gen_before:
                    await self.wait_ready()
                    return
                log(
                    f"  [lane{self.lane_id}] {self._soft_fails} lỗi captcha/mạng — "
                    f"PAUSE + đổi IP…"
                )
                self._soft_fails = 0
                await self._rotate(reason="captcha/mạng lặp", extra_wait=10.0)
                self._rotate_gen += 1
        else:
            wait = 1.5 + self._soft_fails * 0.8
            log(
                f"  [lane{self.lane_id}] soft-fail #{self._soft_fails} — "
                f"chờ {wait:.1f}s"
            )
            await asyncio.sleep(wait)

    async def _rotate(self, reason: str = "", extra_wait: float = 0.0) -> None:
        """
        Caller must hold _rotate_lock.
        - shop: get.php (gói 1p/lần — tôn trọng _last_change + API wait)
        - net: change-ip + status (cũng có cooldown gói)
        - static / thiếu key: nghỉ dài, KHÔNG giả vờ đổi IP
        """
        # Chặn mọi worker / mint trong lúc rotate
        self._extend_pause(30.0, f"đang đổi IP ({reason})")
        if self.pool is not None:
            try:
                await self.pool.set_paused(True, reason=reason or "rotate")
            except Exception:
                pass

        if not self._can_api_rotate():
            wait = 90.0 + extra_wait
            self._extend_pause(wait, "không API rotate (static/thiếu key)")
            log(
                f"  [lane{self.lane_id}] {self.provider} không xoay API — "
                f"chờ {wait:.0f}s rồi mint lại cùng IP"
            )
            await asyncio.sleep(wait)
            self._paused_until = 0.0
            self._pause_reason = ""
            if self.pool is not None:
                await self.pool.on_proxy_changed(
                    self.proxy_url, reason=reason or "static-wait"
                )
            return

        max_tries = 5
        last_err: Exception | None = None
        min_gap = self._min_rotate_gap() or MIN_CHANGE_IP_GAP
        for attempt in range(1, max_tries + 1):
            now = time.time()
            # Chờ đủ chu kỳ local trước khi gọi API (tránh spam status=101)
            if self._last_change > 0 and min_gap > 0:
                gap = min_gap - (now - self._last_change)
                if gap > 0.5:
                    self._extend_pause(
                        gap + 1,
                        f"chờ chu kỳ xoay {min_gap:.0f}s (còn {gap:.0f}s)",
                    )
                    log(
                        f"  [lane{self.lane_id}] gói xoay {min_gap:.0f}s/lần "
                        f"({self.provider}) — còn {gap:.0f}s mới gọi API…"
                    )
                    await asyncio.sleep(gap)

            try:
                log(
                    f"  [lane{self.lane_id}] rotate try {attempt}/{max_tries} "
                    f"({reason}) provider={self.provider}…"
                )
                # shop: phải xóa url cache kẻo resolve trả IP cũ
                if self.provider == "proxyxoay_shop":
                    self.line.pop("url", None)
                new_url = await asyncio.to_thread(rotate_proxy_line, self.line)
                if not new_url or not str(new_url).startswith("http"):
                    raise RuntimeError(f"rotate trả URL lạ: {new_url!r}")
                old_host = _host_of(self.proxy_url)
                self.proxy_url = new_url
                self.line["url"] = new_url
                self._last_change = time.time()
                settle = CHANGE_IP_SETTLE + max(0.0, float(extra_wait or 0))
                if self.provider == "proxyxoay_shop":
                    # shop IP đổi nhanh hơn — settle vừa đủ
                    settle = max(6.0, CHANGE_IP_SETTLE + min(extra_wait, 8.0))
                elif self.provider == "proxyxoay_net":
                    # net: docs + landing — settle dài hơn chút
                    settle = max(settle, CHANGE_IP_SETTLE + 10.0)
                self._extend_pause(settle + 2, f"settle IP mới {_host_of(new_url)}")
                log(
                    f"  [lane{self.lane_id}] OK IP "
                    f"{old_host} → {_host_of(new_url)} · settle {settle:.0f}s · "
                    f"lần xoay sau ≥{min_gap:.0f}s"
                )
                await asyncio.sleep(settle)
                self._ok_on_ip = 0
                self._soft_fails = 0
                self._paused_until = 0.0
                self._pause_reason = ""
                if self.pool is not None:
                    await self.pool.on_proxy_changed(
                        new_url, reason=reason or "rotate"
                    )
                # cho pool mint vài token trước khi worker ào vào
                await asyncio.sleep(1.2)
                return
            except Exception as e:
                last_err = e
                # Ghi nhận đã “gọi API” (kể cả fail 101) để không spam
                # Không update _last_change nếu chưa từng success? 
                # → local gap dựa trên start shop hoặc lần success; API cool ưu tiên
                cool = _parse_wait(e, default=min_gap)
                if self._last_change > 0:
                    local_left = min_gap - (time.time() - self._last_change)
                    cool = max(cool, local_left, 3.0)
                else:
                    cool = max(cool, 3.0)
                self._extend_pause(
                    cool + 1,
                    f"API: còn {cool:.0f}s mới được xoay",
                )
                log(
                    f"  [lane{self.lane_id}] rotate fail "
                    f"{attempt}/{max_tries} [{self.provider}] — còn {cool:.0f}s "
                    f"mới xoay: {str(e)[:160]}"
                )
                await asyncio.sleep(cool)

        # Hết lượt: nghỉ dài, KHÔNG mint lại vội trên IP chết
        cool = float(min_gap or 60.0)
        self._extend_pause(cool, "rotate thất bại — nghỉ dài")
        log(
            f"  [lane{self.lane_id}] rotate hết {max_tries} lần — "
            f"nghỉ {cool:.0f}s: {last_err}"
        )
        await asyncio.sleep(cool)
        self._paused_until = 0.0
        self._pause_reason = ""
        # Thử resolve lại URL hiện tại
        # shop: get.php có thể 101 → giữ IP cũ; net: status đọc IP hiện tại
        try:
            old = self.line.pop("url", None)
            try:
                if self.provider == "proxyxoay_net" and self.api_key:
                    # chỉ status, không change-ip
                    new_url = await asyncio.to_thread(resolve_proxy_line, self.line)
                elif self.provider == "proxyxoay_shop" and self.api_key:
                    # có thể tốn slot — chỉ khi hết tries
                    new_url = await asyncio.to_thread(resolve_proxy_line, self.line)
                    self._last_change = time.time()
                else:
                    new_url = old or self.proxy_url
            except Exception:
                if old:
                    self.line["url"] = old
                new_url = self.proxy_url
            self.proxy_url = new_url or self.proxy_url
            self.line["url"] = self.proxy_url
            if self.pool is not None:
                await self.pool.on_proxy_changed(
                    self.proxy_url, reason="rotate-exhausted-resolve"
                )
        except Exception as e:
            log(f"  [lane{self.lane_id}] resolve sau rotate fail: {e}")
            if self.pool is not None:
                try:
                    await self.pool.set_paused(False, reason="rotate-exhausted")
                except Exception:
                    pass


async def run_jobs(
    jobs: list[dict],
    *,
    proxy_url: str = "",
    proxy_api_key: str = "",
    proxy_lines: Optional[list[dict | str]] = None,
    voice: str = DEFAULT_VOICE,
    model: str = DEFAULT_MODEL,
    lang: str = "en",
    speed: float = 1.0,
    hsw_workers: int = 0,
    workers: int = 1,  # admin max_workers = số TTS đồng thời
    max_attempts: int = MAX_ATTEMPTS_PER_JOB,
    tokens_per_lane: int = TOKENS_PER_LANE,
    should_stop: Optional[Callable[[], bool]] = None,
    on_start: Optional[Callable[[int], None]] = None,
    on_status: Optional[Callable[[int, str], None]] = None,
    on_done: Optional[Callable[[int, bool, str, str], None]] = None,
) -> tuple[int, int]:
    """
    Concurrent TTS:
      workers (admin max_workers) = tổng số luồng TTS đồng thời
      1 proxy + workers=3 → 3 worker trên cùng 1 proxy
      nhiều proxy → chia đều workers
    """
    # Build lines
    lines: list[dict] = []
    if proxy_lines:
        for raw in proxy_lines:
            lines.append(_normalize_line(raw))
    elif proxy_url:
        pu = (proxy_url or "").strip()
        key = (proxy_api_key or "").strip()
        if pu.startswith("shop://"):
            key = key or pu[len("shop://") :].strip()
            lines.append(
                {
                    "id": "px0",
                    "provider": "proxyxoay_shop",
                    "api_key": key,
                }
            )
        else:
            # Heuristic: key-only shop thường đi kèm proxy_url rỗng / shop
            prov = "static"
            if key:
                # caller có thể set nhầm; ưu tiên net nếu URL http host:port
                if "shop" in pu.lower():
                    prov = "proxyxoay_shop"
                else:
                    prov = "proxyxoay_net"
            lines.append(
                {
                    "id": "px0",
                    "url": (normalize_proxy(pu) or pu) if pu.startswith("http") else "",
                    "api_key": key,
                    "provider": prov,
                    "host": "",
                    "port": 0,
                }
            )
            # nếu có http://user:pass@host:port — tách static-friendly
            if pu.startswith("http") and not key:
                lines[-1]["url"] = normalize_proxy(pu) or pu
                lines[-1]["provider"] = "static"
        lines = [_normalize_line(x) for x in lines]
    if not lines:
        raise RuntimeError("không có proxy line")

    total_workers = max(1, min(MAX_WORKERS, int(workers or 1)))
    lines = lines[:MAX_PROXIES]
    # Nếu workers < số proxy → chỉ dùng workers proxy đầu (mỗi cái 1 slot)
    slot_plan = _distribute_slots(total_workers, len(lines))
    if len(slot_plan) < len(lines):
        lines = lines[: len(slot_plan)]
    n_proxies = len(lines)
    total_slots = sum(slot_plan)

    tpl_base = max(1, int(tokens_per_lane or TOKENS_PER_LANE))
    # Ước lượng token pool tổng để size HSW farm
    est_tokens = sum(
        max(s, tpl_base, s + 1) for s in slot_plan
    )
    farm_size = max(
        3,
        min(
            MAX_HSW_PAGES,
            int(hsw_workers)
            if hsw_workers and hsw_workers > 0
            else max(est_tokens, total_slots * 2),
        ),
    )

    pending: list[dict] = []
    ok = fail = 0
    for j in jobs:
        p = j.get("out_path") or ""
        if p and Path(p).is_file() and Path(p).stat().st_size > 500:
            ok += 1
            if on_done:
                on_done(j["row"], True, p, "")
            continue
        jj = dict(j)
        jj["attempts"] = 0
        pending.append(jj)

    if not pending:
        return ok, fail

    # Shared HSW farm
    await start_hsw_farm(
        size=farm_size,
        proxy_http=None,
        via_proxy=False,
        warm=True,
    )
    farm = await get_hsw_farm()

    lanes: list[ProxyLane] = []
    for i, (line, slots) in enumerate(zip(lines, slot_plan)):
        lane = ProxyLane(line, lane_id=i + 1, tts_slots=slots)
        await lane.start(farm, tokens_per_lane=max(tpl_base, slots))
        lanes.append(lane)

    slot_desc = "+".join(str(s) for s in slot_plan)
    log(
        f"  [pipeline] {len(pending)} đoạn · {total_slots} luồng TTS "
        f"({n_proxies} proxy × [{slot_desc}]) · "
        f"nối đuôi {STAGGER_MIN_S:.0f}–{STAGGER_MAX_S:.0f}s · "
        f"HSW farm={farm_size} · giữ IP đến 401 · rotate có lock"
    )

    q: asyncio.Queue[dict | None] = asyncio.Queue()
    for job in pending:
        await q.put(job)
    # 1 poison pill / worker (không phải / proxy)
    for _ in range(total_slots):
        await q.put(None)

    ok_lock = asyncio.Lock()
    counters = {"ok": ok, "fail": fail}

    # Nối đuôi: lệch pha giữa các lần BẮT ĐẦU TTS (global, mọi worker)
    stagger_lock = asyncio.Lock()
    last_tts_start = 0.0  # monotonic-ish via time.time()

    async def wait_stagger(tag: str) -> None:
        """Chờ random 1–3s kể từ lần TTS start trước — kiểu nối đuôi."""
        nonlocal last_tts_start
        if total_slots <= 1:
            return
        async with stagger_lock:
            now = time.time()
            gap = random.uniform(STAGGER_MIN_S, STAGGER_MAX_S)
            wait = (last_tts_start + gap) - now if last_tts_start > 0 else 0.0
            if wait > 0.05:
                log(f"  [{tag}] nối đuôi chờ {wait:.1f}s (gap {gap:.1f}s)…")
                await asyncio.sleep(wait)
            last_tts_start = time.time()

    async def lane_worker(
        lane: ProxyLane, wid: int, start_delay: float = 0.0
    ) -> None:
        assert lane.pool is not None
        tag = f"L{lane.lane_id}w{wid}"
        # Lệch pha lúc mở worker (worker 2/3… không nhảy cùng worker 1)
        if start_delay > 0.05 and not (should_stop and should_stop()):
            log(f"  [{tag}] mở luồng sau {start_delay:.1f}s (nối đuôi)…")
            await asyncio.sleep(start_delay)
        while True:
            if should_stop and should_stop():
                break
            job = await q.get()
            if job is None:
                break

            row = job["row"]
            out_path = job["out_path"]
            text = job.get("text") or ""
            success = False
            last_err = ""

            while int(job.get("attempts") or 0) < max_attempts:
                if should_stop and should_stop():
                    break
                job["attempts"] = int(job.get("attempts") or 0) + 1
                att = job["attempts"]
                pool = lane.pool
                assert pool is not None

                try:
                    # Lane pause (401/đổi IP) — không take token / không TTS
                    if lane._paused_until > time.time():
                        if on_status:
                            on_status(row, "Đổi kết nối…")
                        await lane.wait_ready(
                            should_stop, tag=tag
                        )

                    if on_status:
                        # UI: plain Vietnamese — no L1/host/mint jargon
                        on_status(
                            row,
                            f"Đang chuẩn bị… (lần {att})"
                            if att > 1
                            else "Đang chuẩn bị…",
                        )
                    if on_start:
                        on_start(row)

                    # token⇄proxy: TTS MUST dùng đúng proxy lúc solve
                    token, px = await pool.take(timeout=120.0)
                    if not px:
                        raise RuntimeError("token không gắn proxy — bỏ qua")
                    if px != lane.proxy_url:
                        log(
                            f"  [{tag}] bind TTS proxy "
                            f"{_host_of(px)} (token solve IP)"
                        )
                    # IP vừa bị 401 trong lúc chờ token?
                    if lane._paused_until > time.time():
                        raise RuntimeError(
                            "token-pool/proxy đang đổi IP — thử lại"
                        )
                    pool.kick_refill()
                    await asyncio.sleep(0)

                    # Nối đuôi: không bắn TTS cùng lúc giữa các luồng
                    await wait_stagger(tag)
                    if lane._paused_until > time.time():
                        raise RuntimeError(
                            "token-pool/proxy đang đổi IP — thử lại"
                        )

                    if on_status:
                        on_status(row, "Đang tạo audio…")
                    log(
                        f"  [pipeline] đoạn {row+1} {tag}: "
                        f"TTS@{_host_of(px)} token⇄proxy "
                        f"ready={pool.ready}/{pool.target} inflight={pool.inflight}"
                    )
                    # Per-job voice (multi-voice dialogue) hoặc voice global
                    job_voice = (
                        (job.get("voice") or job.get("voice_id") or voice or "")
                        .strip()
                        or voice
                    )
                    audio = await call_tts(
                        text,
                        token,
                        px,  # cùng proxy key/IP đã solve captcha
                        job_voice,
                        model,
                        lang,
                        speed,
                    )
                    pool.kick_refill()
                    # Atomic write: tránh file dở dang bị đếm là "đã có"
                    out_p = Path(out_path)
                    out_p.parent.mkdir(parents=True, exist_ok=True)
                    tmp_p = out_p.with_suffix(
                        out_p.suffix + f".part.{os.getpid()}.{tag}"
                    )
                    try:
                        tmp_p.write_bytes(audio)
                        if tmp_p.stat().st_size < 500:
                            raise RuntimeError(
                                f"audio quá nhỏ ({tmp_p.stat().st_size}B)"
                            )
                        os.replace(str(tmp_p), str(out_p))
                    finally:
                        try:
                            if tmp_p.is_file():
                                tmp_p.unlink()
                        except Exception:
                            pass
                    await lane.on_success()
                    success = True
                    if on_done:
                        on_done(row, True, out_path, "")
                    break
                except Exception as e:
                    last_err = str(e)[:300]
                    log(
                        f"  [pipeline] đoạn {row+1} {tag} "
                        f"lỗi lần {att}: {last_err[:140]}"
                    )
                    try:
                        p = Path(out_path)
                        if p.is_file() and p.stat().st_size < 500:
                            p.unlink()
                    except Exception:
                        pass
                    if _is_hard_401(e):
                        if on_status:
                            on_status(row, "Đổi kết nối…")
                        await lane.on_401()
                    elif _is_retryable(e):
                        if on_status:
                            on_status(row, "Lỗi tạm — thử lại…")
                        await lane.on_soft_fail(e)
                    else:
                        if on_status:
                            on_status(row, f"Lỗi — thử lại (lần {att})")
                        await asyncio.sleep(1.0 + random.uniform(0, 0.5))

            async with ok_lock:
                if success:
                    counters["ok"] += 1
                    log(
                        f"  [{tag}] TTS OK "
                        f"(#{lane._ok_on_ip} trên IP lane{lane.lane_id})"
                    )
                else:
                    counters["fail"] += 1
                    if on_done:
                        on_done(row, False, "", last_err or "hết lần thử")

    # Lệch pha mở worker: w1=0, w2=+rand(1–3), w3=+rand(1–3)… (cộng dồn)
    tasks: list[asyncio.Task] = []
    open_delay = 0.0
    for lane, slots in zip(lanes, slot_plan):
        for w in range(1, slots + 1):
            tasks.append(
                asyncio.create_task(
                    lane_worker(lane, w, start_delay=open_delay),
                    name=f"L{lane.lane_id}w{w}",
                )
            )
            if total_slots > 1:
                open_delay += random.uniform(STAGGER_MIN_S, STAGGER_MAX_S)
    await asyncio.gather(*tasks)

    for lane in lanes:
        await lane.stop()
    try:
        await close_hsw_farm()
    except Exception:
        pass
    return counters["ok"], counters["fail"]
