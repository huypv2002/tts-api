# -*- coding: utf-8 -*-
"""
Gen pipeline — multi-proxy scale:

  • N proxy key → N lane TTS song song (mỗi lane 1 slot)
  • Token pool mỗi lane target=3 → tổng 3N token
  • Mint ∥ TTS (nối đuôi trong mỗi lane)
  • Giữ IP đến 401 mới rotate (theo từng lane)
  • provider: proxyxoay_net | proxyxoay_shop | static

Một event loop / batch.
"""
from __future__ import annotations

import asyncio
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
    resolve_proxy_line,
    rotate_proxy_line,
    start_hsw_farm,
)

CHANGE_IP_SETTLE = 8.0
LANDING_EXTRA_WAIT = 45.0
MIN_CHANGE_IP_GAP = 55.0
MAX_ATTEMPTS_PER_JOB = 40
SOFT_FAIL_BEFORE_ROTATE = 3

TOKENS_PER_LANE = 3  # 2 key → pool 6, TTS 2
MAX_LANES = 5
MAX_HSW_PAGES = 8


def _host_of(proxy: Optional[str]) -> str:
    if not proxy:
        return "direct"
    s = proxy.split("@")[-1]
    return s.rsplit(":", 1)[0]


def _is_hard_401(err: Exception | str) -> bool:
    m = str(err).lower()
    return (
        "401" in m
        or "tts_landing" in m
        or "sign_in_required" in m
        or "landing page" in m
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


def _parse_wait(err: Exception | str, default: float) -> float:
    msg = str(err)
    m = re.search(r"(\d+)\s*(phút|minute|min)", msg, re.I)
    if m:
        return max(default, float(m.group(1)) * 60 + 5)
    m = re.search(r"(\d+)\s*(giây|second|sec)", msg, re.I)
    if m:
        return max(8.0, float(m.group(1)) + 2)
    return default


def _normalize_line(raw: dict | str) -> dict:
    """Accept str URL or full proxy dict."""
    if isinstance(raw, str):
        return {
            "id": "px_inline",
            "url": normalize_proxy(raw) or raw,
            "provider": "static",
            "api_key": "",
        }
    line = dict(raw)
    line["provider"] = detect_proxy_provider(
        line.get("provider"), line.get("host")
    )
    return line


class ProxyLane:
    """
    1 proxy key = 1 lane:
      • TokenPool target=3, refillers≈3
      • TTS 1 slot (worker riêng)
      • 401 → rotate IP chỉ lane này
    """

    def __init__(self, line: dict, lane_id: int):
        self.lane_id = lane_id
        self.line = _normalize_line(line)
        self.proxy_url = ""
        self.api_key = (self.line.get("api_key") or "").strip()
        self.provider = self.line.get("provider") or "static"
        self.label = (
            self.line.get("label")
            or self.line.get("id")
            or f"lane{lane_id}"
        )
        self.pool: TokenPool | None = None
        self._last_change = 0.0
        self._ok_on_ip = 0
        self._soft_fails = 0

    async def start(self, farm, tokens_per_lane: int = TOKENS_PER_LANE) -> None:
        try:
            self.proxy_url = await asyncio.to_thread(resolve_proxy_line, self.line)
        except Exception as e:
            # static fallback from host/user
            host = self.line.get("host")
            port = int(self.line.get("port") or 0)
            user = self.line.get("username") or ""
            pw = self.line.get("password") or ""
            if host and port:
                self.proxy_url = (
                    f"http://{user}:{pw}@{host}:{port}"
                    if user and pw
                    else f"http://{host}:{port}"
                )
                log(f"  [lane{self.lane_id}] resolve API fail, static: {e}")
            else:
                raise
        self.line["url"] = self.proxy_url
        target = max(1, int(tokens_per_lane))
        refillers = max(1, min(target, 3))
        self.pool = TokenPool(
            proxy=self.proxy_url,
            target=target,
            refillers=refillers,
            farm=farm,
        )
        await self.pool.start()
        log(
            f"  [lane{self.lane_id}] {self.label} · {self.provider} · "
            f"proxy={_host_of(self.proxy_url)} · pool={target}"
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
        log(
            f"  [lane{self.lane_id}] 401 sau {self._ok_on_ip} TTS OK — đổi IP…"
        )
        self._ok_on_ip = 0
        self._soft_fails = 0
        await self._rotate(reason="401/landing", extra_wait=LANDING_EXTRA_WAIT)

    async def on_soft_fail(self, err: Exception | str) -> None:
        self._soft_fails += 1
        if self._soft_fails >= SOFT_FAIL_BEFORE_ROTATE and self.api_key:
            log(
                f"  [lane{self.lane_id}] {self._soft_fails} lỗi captcha/mạng — đổi IP…"
            )
            self._soft_fails = 0
            await self._rotate(reason="captcha/mạng lặp", extra_wait=15.0)
        else:
            wait = 1.5 + self._soft_fails * 0.8
            log(
                f"  [lane{self.lane_id}] soft-fail #{self._soft_fails} — "
                f"chờ {wait:.1f}s"
            )
            await asyncio.sleep(wait)

    async def _rotate(self, reason: str = "", extra_wait: float = 0.0) -> None:
        if not self.api_key and self.provider != "proxyxoay_shop":
            wait = 90.0 + extra_wait
            log(f"  [lane{self.lane_id}] không API rotate — chờ {wait:.0f}s")
            await asyncio.sleep(wait)
            if self.pool is not None:
                await self.pool.on_proxy_changed(
                    self.proxy_url, reason=reason or "wait"
                )
            return

        now = time.time()
        gap = MIN_CHANGE_IP_GAP - (now - self._last_change)
        if gap > 0 and self.provider == "proxyxoay_net":
            log(f"  [lane{self.lane_id}] cooldown change-ip {gap:.0f}s…")
            await asyncio.sleep(gap)
        try:
            log(f"  [lane{self.lane_id}] rotate ({reason}) provider={self.provider}…")
            new_url = await asyncio.to_thread(rotate_proxy_line, self.line)
            self.proxy_url = new_url
            self._last_change = time.time()
            settle = CHANGE_IP_SETTLE + extra_wait
            if self.provider == "proxyxoay_shop":
                settle = max(5.0, CHANGE_IP_SETTLE + min(extra_wait, 10.0))
            log(f"  [lane{self.lane_id}] settle {settle:.0f}s → {_host_of(new_url)}")
            await asyncio.sleep(settle)
            self._ok_on_ip = 0
            self._soft_fails = 0
            if self.pool is not None:
                await self.pool.on_proxy_changed(new_url, reason=reason or "rotate")
        except Exception as e:
            cool = _parse_wait(e, default=120.0)
            log(f"  [lane{self.lane_id}] rotate fail — chờ {cool:.0f}s: {e}")
            await asyncio.sleep(cool)
            if self.pool is not None:
                await self.pool.on_proxy_changed(
                    self.proxy_url, reason="rotate-fail"
                )


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
    workers: int = 1,  # legacy: used as max lanes if proxy_lines empty
    max_attempts: int = MAX_ATTEMPTS_PER_JOB,
    tokens_per_lane: int = TOKENS_PER_LANE,
    should_stop: Optional[Callable[[], bool]] = None,
    on_start: Optional[Callable[[int], None]] = None,
    on_status: Optional[Callable[[int, str], None]] = None,
    on_done: Optional[Callable[[int, bool, str, str], None]] = None,
) -> tuple[int, int]:
    """
    Multi-lane:
      proxy_lines (ưu tiên) hoặc 1 line từ proxy_url+api_key
      N lanes → N TTS song song · pool 3N token
    """
    # Build lines
    lines: list[dict] = []
    if proxy_lines:
        for raw in proxy_lines:
            lines.append(_normalize_line(raw))
    elif proxy_url:
        lines.append(
            {
                "id": "px0",
                "url": normalize_proxy(proxy_url) or proxy_url,
                "api_key": proxy_api_key or "",
                "provider": "proxyxoay_net" if proxy_api_key else "static",
            }
        )
    if not lines:
        raise RuntimeError("không có proxy line")

    # Cap lanes
    max_w = max(1, min(MAX_LANES, int(workers or len(lines)) or 1))
    lines = lines[:max_w]
    n_lanes = len(lines)
    tpl = max(1, int(tokens_per_lane or TOKENS_PER_LANE))
    total_tokens = n_lanes * tpl
    farm_size = max(
        3,
        min(
            MAX_HSW_PAGES,
            int(hsw_workers) if hsw_workers and hsw_workers > 0 else total_tokens,
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
    for i, line in enumerate(lines):
        lane = ProxyLane(line, lane_id=i + 1)
        await lane.start(farm, tokens_per_lane=tpl)
        lanes.append(lane)

    log(
        f"  [pipeline] {len(pending)} đoạn · {n_lanes} lane TTS song song · "
        f"token pool {total_tokens} ({tpl}/lane) · HSW farm={farm_size} · "
        f"giữ IP đến 401"
    )

    q: asyncio.Queue[dict | None] = asyncio.Queue()
    for job in pending:
        await q.put(job)
    # poison pills
    for _ in lanes:
        await q.put(None)

    ok_lock = asyncio.Lock()
    counters = {"ok": ok, "fail": fail}

    async def lane_worker(lane: ProxyLane) -> None:
        assert lane.pool is not None
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
                    # đồng bộ lane URL với proxy của token (tránh lệch sau rotate)
                    if px != lane.proxy_url:
                        log(
                            f"  [lane{lane.lane_id}] bind TTS proxy "
                            f"{_host_of(px)} (token solve IP)"
                        )
                    pool.kick_refill()
                    await asyncio.sleep(0)

                    if on_status:
                        on_status(row, "Đang tạo audio…")
                    log(
                        f"  [pipeline] đoạn {row+1} lane{lane.lane_id}: "
                        f"TTS@{_host_of(px)} token⇄proxy "
                        f"ready={pool.ready}/{pool.target} inflight={pool.inflight}"
                    )
                    # stability/similarity: không truyền — TTS dùng mặc định API
                    audio = await call_tts(
                        text,
                        token,
                        px,  # cùng proxy key/IP đã solve captcha
                        voice,
                        model,
                        lang,
                        speed,
                    )
                    pool.kick_refill()
                    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
                    Path(out_path).write_bytes(audio)
                    await lane.on_success()
                    success = True
                    if on_done:
                        on_done(row, True, out_path, "")
                    break
                except Exception as e:
                    last_err = str(e)[:300]
                    log(
                        f"  [pipeline] đoạn {row+1} L{lane.lane_id} "
                        f"lỗi lần {att}: {last_err[:140]}"
                    )
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
                        f"  [lane{lane.lane_id}] TTS OK "
                        f"(#{lane._ok_on_ip} trên IP)"
                    )
                else:
                    counters["fail"] += 1
                    if on_done:
                        on_done(row, False, "", last_err or "hết lần thử")

    tasks = [
        asyncio.create_task(lane_worker(lane), name=f"lane-{lane.lane_id}")
        for lane in lanes
    ]
    await asyncio.gather(*tasks)

    for lane in lanes:
        await lane.stop()
    try:
        await close_hsw_farm()
    except Exception:
        pass
    return counters["ok"], counters["fail"]
