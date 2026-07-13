#!/usr/bin/env python3
"""
fast_tts_loop.py — Local multi-worker TTS tool (max speed)

Architecture:
  HSW Farm (K pages, no-proxy) ──► TokenPool refillers ──► ready tokens (TTL ~50s)
  TTS workers (N) ── take token ──► call_tts (critical path ≈ TTS only)
  1 IP block → rotate proxyxoay → invalidate token pool → tiếp

Mặc định: call đến khi bị chặn mới rotate (không cap sớm).
--workers N  : số TTS song song
--hsw-workers: số page HSW song song (default auto 2–4)
--token-target: số token sẵn ≈ số TTS workers (1 token = 1 TTS, không TTL)

Lỗi mạng/timeout → RETRY cùng job/IP, không rotate.
Chỉ rotate khi lỗi block thật (403/quota/unusual…).

Usage:
  python3 fast_tts_loop.py --count 1000 --workers 6 --hsw-workers 3
  python3 fast_tts_loop.py --count 20 --text "Xin chào" --lang vi

Env / config: .proxyxoay.json
  HSW_WORKERS, HSW_VIA_PROXY
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path

import httpx

from fast_tts import (
    TokenPool,
    call_tts,
    close_hsw_farm,
    default_hsw_workers,
    fetch_proxyxoay,
    load_proxyxoay_cfg,
    log,
    probe_proxy_exit,
    proxyxoay_net_from_status,
    solve_token,
    start_hsw_farm,
    DEFAULT_MODEL,
    DEFAULT_VOICE,
)

# TTS chunk size: hard max 1000 chars; pack/cut ≤950, always on word boundary
DEFAULT_CHUNK_MAX = 950
DEFAULT_CHUNK_HARD_MAX = 1000
DEFAULT_CHUNK_MIN = 120

# Real IP/quota blocks from ElevenLabs edge — rotate on these only
BLOCK_MARKERS = (
    "quota_exceeded",
    "sign_in_required",
    "detected_unusual_activity",
    "unusual activity",
    "rate limit",
    "free tier",
    "landing page",
    "your client does not have permission",
    "access denied",
    "too many requests",
    "tts http 403",
    "tts http 401",
    "tts http 429",
    "http 403",
    "http 401",
    "http 429",
    "forbidden",
)

# Proxy/network flakiness — always retry same job, never rotate
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
    "temporarily unavailable",
    "network is unreachable",
    "proxyerror",
    "connecterror",
    "readerror",
    "writeerror",
    "server disconnected",
    "remoteprotocolerror",
    "pool timeout",
    "connect timeout",
    "read timeout",
    "ssl",
    "certificate",
)


def is_transient(err: BaseException) -> bool:
    msg = str(err).lower()
    name = type(err).__name__.lower()
    blob = f"{name}: {msg}"
    return any(m in blob for m in TRANSIENT_MARKERS)


def _split_long_on_words(text: str, max_chars: int) -> list[str]:
    """
    Split a long string into pieces ≤ max_chars, never mid-word.
    Prefers break at space; falls back to hard cut only if a single word
    exceeds max_chars (rare).
    """
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    out: list[str] = []
    rest = text
    while rest:
        if len(rest) <= max_chars:
            out.append(rest.strip())
            break
        window = rest[: max_chars + 1]
        # last space within limit → cut before it
        sp = window.rfind(" ", 0, max_chars + 1)
        if sp <= 0:
            # no space: one giant token — hard cut at max_chars
            piece = rest[:max_chars]
            rest = rest[max_chars:].lstrip()
        else:
            piece = rest[:sp].rstrip()
            rest = rest[sp + 1 :].lstrip()
        if piece:
            out.append(piece)
    return out


def load_text_chunks(
    text_file: Path | None,
    plain_text: str | None,
    max_chars: int = DEFAULT_CHUNK_MAX,
    min_chars: int = DEFAULT_CHUNK_MIN,
    hard_max: int = DEFAULT_CHUNK_HARD_MAX,
) -> list[str]:
    """
    Load source text and split into TTS-sized chunks.
    - Target pack size: max_chars (default 950, always ≤ hard_max 1000)
    - Cuts only on word boundaries (never mid-word)
    Prefer --text-file; fall back to --text.
    """
    max_chars = min(max_chars, hard_max)
    raw = ""
    if text_file is not None:
        p = Path(text_file)
        if not p.is_file():
            raise FileNotFoundError(f"text file not found: {p}")
        raw = p.read_text(encoding="utf-8", errors="replace")
    elif plain_text:
        raw = plain_text
    else:
        raise ValueError("need --text-file or --text")

    # normalize whitespace / blank lines
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw).strip()
    if not raw:
        raise ValueError("empty text source")

    # sentence split (keep punctuation)
    parts = re.split(r"(?<=[.!?])\s+", raw.replace("\n", " "))
    sents = [s.strip() for s in parts if s and s.strip()]
    if not sents:
        sents = [raw]

    chunks: list[str] = []
    buf = ""

    def flush_buf() -> None:
        nonlocal buf
        b = buf.strip()
        if not b:
            buf = ""
            return
        if len(b) <= max_chars:
            chunks.append(b)
        else:
            chunks.extend(_split_long_on_words(b, max_chars))
        buf = ""

    for s in sents:
        s = re.sub(r"\s+", " ", s).strip()
        if not s:
            continue

        # sentence itself longer than limit → flush buf, word-split sentence
        if len(s) > max_chars:
            flush_buf()
            chunks.extend(_split_long_on_words(s, max_chars))
            continue

        if not buf:
            buf = s
        elif len(buf) + 1 + len(s) <= max_chars:
            buf = f"{buf} {s}"
        else:
            # would exceed → flush current (complete words/sentences), start new
            flush_buf()
            buf = s

    flush_buf()

    # drop empties; never emit over hard_max (safety)
    cleaned: list[str] = []
    for c in chunks:
        c = c.strip()
        if not c:
            continue
        if len(c) > hard_max:
            cleaned.extend(_split_long_on_words(c, max_chars))
        else:
            cleaned.append(c)

    # dedupe consecutive identical chunks (file may repeat sections)
    deduped: list[str] = []
    for c in cleaned:
        if not deduped or deduped[-1] != c:
            deduped.append(c)

    # verify no mid-word junk and all ≤ hard_max
    for c in deduped:
        if len(c) > hard_max:
            raise RuntimeError(f"chunk exceeds hard_max: {len(c)}")
    return deduped or _split_long_on_words(raw, max_chars)


def is_ip_blocked(err: BaseException) -> bool:
    """True only for real ban/quota — NOT for network timeouts."""
    if is_transient(err):
        return False
    msg = str(err).lower()
    return any(m in msg for m in BLOCK_MARKERS)


def wait_and_change_ip(api_key: str, max_wait: int = 300) -> str:
    """Poll change-key-ip until success; return new proxy URL."""
    url = f"https://proxyxoay.net/api/rotating-proxy/change-key-ip/{api_key}"
    deadline = time.time() + max_wait
    while time.time() < deadline:
        r = httpx.get(url, timeout=30.0)
        data = r.json()
        msg = data.get("message") or ""
        log(f"  [rotate] {data.get('status')} {msg}")
        if data.get("status") == 200:
            time.sleep(4)
            proxy = proxyxoay_net_from_status(api_key)
            info = probe_proxy_exit(proxy, timeout=15.0)
            log(f"  [rotate] new exit={info.get('exit_ip') or info}")
            return proxy
        m = re.search(r"(\d+)\s*giây", msg)
        wait = int(m.group(1)) + 2 if m else 15
        wait = min(max(wait, 5), 90)
        log(f"  [rotate] sleep {wait}s...")
        time.sleep(wait)
    log("  [rotate] timeout waiting change-ip — use current IP")
    return proxyxoay_net_from_status(api_key)


async def one_tts(
    text: str,
    proxy: str,
    voice_id: str,
    model_id: str,
    lang: str,
    speed: float,
    out_path: Path,
    token_pool: TokenPool | None = None,
) -> dict:
    """
    Hot path: pop pre-warmed token (if pool) → TTS only.
    Fallback: on-demand solve_token when pool missing/starved.
    """
    t0 = time.time()
    t_token = time.time()
    if token_pool is not None:
        token = await token_pool.take(timeout=90.0)
    else:
        token = await solve_token(proxy)
    token_s = time.time() - t_token
    t_tts = time.time()
    audio = await call_tts(text, token, proxy, voice_id, model_id, lang, speed)
    tts_s = time.time() - t_tts
    out_path.write_bytes(audio)
    total = time.time() - t0
    log(f"  [timing] token={token_s:.1f}s tts={tts_s:.1f}s total={total:.1f}s")
    return {
        "ok": True,
        "bytes": len(audio),
        "file": str(out_path),
        "seconds": round(total, 2),
        "token_s": round(token_s, 2),
        "tts_s": round(tts_s, 2),
    }


class SharedPool:
    """
    Shared state for parallel workers on ONE proxyxoay rotating line.

    Architecture:
      - 1 gateway proxy + exit IP rotate via change-key-ip
      - TokenPool pre-warms captcha tokens for current proxy
      - N TTS workers take tokens (not each solving HSW inline)
      - HSW Farm (K) fills TokenPool in background
      - Block → single-flight rotate + invalidate tokens + pause workers
    """

    def __init__(
        self,
        api_key: str,
        proxy: str,
        exit_ip: str,
        count: int,
        outdir: Path,
        max_per_ip: int,
        resume_ok: int,
        token_pool: TokenPool | None = None,
    ):
        self.api_key = api_key
        self.proxy = proxy
        self.exit_ip = exit_ip
        self.count = count
        self.outdir = outdir
        self.max_per_ip = max_per_ip
        self.token_pool = token_pool

        self.ok = resume_ok
        self.fail_block = 0
        self.fail_transient = 0
        self.next_n = resume_ok + 1
        self.in_flight = 0
        self.per_ip = 0
        self.block_streak = 0  # only real blocks, never timeouts
        self.ip_gen = 0
        self.ip_stats: dict[str, int] = {}
        self.history: list = []

        self.lock = asyncio.Lock()
        self.rotate_lock = asyncio.Lock()
        # set = IP usable; clear = đang rotate, worker phải chờ
        self.ip_ready = asyncio.Event()
        self.ip_ready.set()
        self.done = asyncio.Event()
        if resume_ok >= count:
            self.done.set()

    async def wait_ip_ready(self) -> None:
        """Block until not rotating (avoids burning HSW on dead IP)."""
        await self.ip_ready.wait()

    async def snapshot(self) -> tuple[str, str, int, int]:
        async with self.lock:
            return self.proxy, self.exit_ip, self.ip_gen, self.per_ip

    async def claim_job(self) -> int | None:
        """Reserve a job number. Returns n or None if no more work."""
        async with self.lock:
            if self.ok >= self.count or self.done.is_set():
                self.done.set()
                return None
            if self.ok + self.in_flight >= self.count:
                return None
            if self.max_per_ip > 0 and self.per_ip >= self.max_per_ip:
                return None
            n = self.next_n
            self.next_n += 1
            self.in_flight += 1
            return n

    async def release_inflight(self) -> None:
        async with self.lock:
            self.in_flight = max(0, self.in_flight - 1)

    async def need_early_rotate(self) -> bool:
        async with self.lock:
            return (
                self.max_per_ip > 0
                and self.per_ip >= self.max_per_ip
                and self.ok < self.count
            )

    async def on_success(self, n: int, rec: dict, exit_ip: str, ip_gen: int) -> bool:
        """Return True if success counted. False if dropped (stale gen)."""
        async with self.lock:
            self.in_flight = max(0, self.in_flight - 1)
            if ip_gen != self.ip_gen:
                log(f"  [drop] late OK #{n} (stale gen={ip_gen} now={self.ip_gen})")
                try:
                    Path(rec["file"]).unlink(missing_ok=True)
                except Exception:
                    pass
                return False
            self.ok += 1
            self.per_ip += 1
            self.block_streak = 0
            self.ip_stats[exit_ip] = self.ip_stats.get(exit_ip, 0) + 1
            rec.update(
                {
                    "n": n,
                    "exit_ip": exit_ip,
                    "ip_gen": ip_gen,
                    "ok_on_ip": self.per_ip,
                    "ok_total": self.ok,
                }
            )
            self.history.append(rec)
            log(
                f"  ✅ #{n} {rec['bytes']}B {rec['seconds']}s → {Path(rec['file']).name} "
                f"| gen={ip_gen} ok_on_ip={self.per_ip} total={self.ok}/{self.count}"
            )
            if self.ok >= self.count:
                self.done.set()
            return True

    async def on_transient(self, n: int, err: BaseException, wid: int, attempt: int) -> None:
        async with self.lock:
            self.fail_transient += 1
        msg = f"{type(err).__name__}: {err}"
        log(f"  ↻ [W{wid}] #{n} transient (try {attempt}): {msg[:160]} → retry same job/IP")

    async def on_block(
        self, n: int, err: BaseException, exit_ip: str, ip_gen: int, wid: int
    ) -> int | None:
        """
        Real block (401 sign_in_required / 403 / quota…).
        Returns blocked gen that still needs rotate, or None if already rotated.
        """
        async with self.lock:
            if ip_gen != self.ip_gen:
                log(
                    f"  ⛔ [W{wid}] #{n} block on stale gen={ip_gen} "
                    f"(now gen={self.ip_gen}) → skip rotate, will retry new IP"
                )
                return None
            self.fail_block += 1
            self.block_streak += 1
            msg = f"{type(err).__name__}: {err}"
            self.history.append(
                {
                    "n": n,
                    "ok": False,
                    "error": msg[:400],
                    "exit_ip": exit_ip,
                    "ip_gen": ip_gen,
                    "ok_on_ip": self.per_ip,
                    "worker": wid,
                    "kind": "block",
                }
            )
            log(
                f"  ⛔ [W{wid}] #{n} BLOCK gen={ip_gen} ok_on_ip={self.per_ip}: {msg[:180]}"
            )
            log(f"  → sẽ rotate IP rồi RETRY cùng job#{n} (không bỏ job)")
            return ip_gen  # rotate for this gen

    async def rotate(
        self, reason: str, wid: int, for_gen: int | None = None
    ) -> None:
        """
        Single-flight rotate. If for_gen is set, only rotate when still on that gen
        (prevents 2–3 workers all 401 at once from burning multiple change-ip).

        While rotating: clear ip_ready so other workers pause (no extra 401 spam).
        """
        async with self.rotate_lock:
            async with self.lock:
                if for_gen is not None and self.ip_gen != for_gen:
                    log(
                        f"  [rotate/W{wid}] skip — already gen={self.ip_gen} "
                        f"(block was gen={for_gen})"
                    )
                    return
                # already rotating by peer who holds path? (re-check)
                old_ip = self.exit_ip
                old_gen = self.ip_gen
                ok_on_ip = self.per_ip
                # pause everyone before network call
                self.ip_ready.clear()

            log(
                f"  [rotate/W{wid}] {reason} | IP {old_ip} gen={old_gen} "
                f"after {ok_on_ip} ok → change-ip (workers pause)"
            )
            try:
                # drop tokens bound to dead IP immediately (stop wasting HSW)
                if self.token_pool is not None:
                    await self.token_pool.on_proxy_changed(
                        None, reason=f"pre-rotate {reason}"
                    )
                proxy = await asyncio.to_thread(wait_and_change_ip, self.api_key)
                info = await asyncio.to_thread(probe_proxy_exit, proxy, 15.0)
                exit_ip = info.get("exit_ip") or "?"
                async with self.lock:
                    if self.ip_gen == old_gen:
                        self.proxy = proxy
                        self.exit_ip = exit_ip
                        self.per_ip = 0
                        self.block_streak = 0
                        self.ip_gen += 1
                        log(
                            f"  [rotate] new exit={exit_ip} gen={self.ip_gen} "
                            f"(was gen={old_gen} ok_on_ip was {ok_on_ip})"
                        )
                    else:
                        log(f"  [rotate] skip apply (gen already {self.ip_gen})")
                if self.token_pool is not None:
                    await self.token_pool.on_proxy_changed(
                        proxy, reason=f"new-exit={exit_ip}"
                    )
            finally:
                # always resume workers even if change-ip failed
                self.ip_ready.set()


async def worker(
    wid: int,
    pool: SharedPool,
    chunks: list[str],
    voice_id: str,
    model_id: str,
    lang: str,
    speed: float,
    max_transient_retries: int,
) -> None:
    log(f"  [W{wid}] started")
    n_chunks = len(chunks)
    while not pool.done.is_set():
        if await pool.need_early_rotate():
            async with pool.lock:
                gen_now = pool.ip_gen
            await pool.rotate(f"max_per_ip={pool.max_per_ip}", wid, for_gen=gen_now)
            continue

        n = await pool.claim_job()
        if n is None:
            if pool.done.is_set():
                break
            if await pool.need_early_rotate():
                async with pool.lock:
                    gen_now = pool.ip_gen
                await pool.rotate(f"max_per_ip={pool.max_per_ip}", wid, for_gen=gen_now)
                continue
            await asyncio.sleep(0.15)
            continue

        # job#n → chunk index 0-based (cycle if count > chunks)
        chunk_idx = (n - 1) % n_chunks
        text = chunks[chunk_idx]
        preview = text[:70].replace("\n", " ")

        # Keep same job number across transient retries / mid-flight rotate
        attempt = 0
        counted = False
        while not pool.done.is_set() and not counted:
            # không take token khi đang change-ip
            await pool.wait_ip_ready()
            if pool.done.is_set():
                break

            proxy, exit_ip, ip_gen, per_ip = await pool.snapshot()
            ready = pool.token_pool.ready if pool.token_pool else -1
            out = pool.outdir / f"tts_{n:04d}_w{wid}_{int(time.time())}.mp3"
            attempt += 1
            log(
                f"\n══ [W{wid}] job#{n} chunk={chunk_idx+1}/{n_chunks} "
                f"({len(text)}c) attempt={attempt} "
                f"ip={exit_ip} gen={ip_gen} ok_on_ip={per_ip} "
                f"tokens={ready} total={pool.ok}/{pool.count} ══"
            )
            log(f"  … \"{preview}{'…' if len(text) > 70 else ''}\"")
            try:
                rec = await one_tts(
                    text,
                    proxy,
                    voice_id,
                    model_id,
                    lang,
                    speed,
                    out,
                    token_pool=pool.token_pool,
                )
                counted = await pool.on_success(n, rec, exit_ip, ip_gen)
                if not counted:
                    # IP rotated under us — retry same n on new gen
                    log(f"  ↻ [W{wid}] #{n} stale gen mid-flight → retry after rotate")
                    attempt = 0
                    await pool.wait_ip_ready()
                    continue
            except Exception as e:
                if is_transient(e):
                    await pool.on_transient(n, e, wid, attempt)
                    # still same job / same IP; soft backoff
                    if attempt >= max_transient_retries:
                        log(
                            f"  ↻ [W{wid}] #{n} too many transients "
                            f"({attempt}), still retry after 3s (no rotate)"
                        )
                        await asyncio.sleep(3.0)
                        attempt = 0  # reset counter but keep job n
                    else:
                        await asyncio.sleep(min(1.0 + attempt * 0.5, 4.0))
                    continue

                if is_ip_blocked(e):
                    # 401 sign_in_required / quota:
                    #  - 1 worker rotate (for_gen)
                    #  - worker khác skip + wait_ip_ready
                    #  - rồi RETRY cùng job trên IP mới
                    blocked_gen = await pool.on_block(n, e, exit_ip, ip_gen, wid)
                    if blocked_gen is not None:
                        await pool.rotate("block", wid, for_gen=blocked_gen)
                    else:
                        log(
                            f"  ⏸ [W{wid}] #{n} chờ rotate của peer "
                            f"(không change-ip thêm)"
                        )
                        await pool.wait_ip_ready()
                    attempt = 0
                    continue

                # unknown error: treat like soft transient (retry, no rotate)
                await pool.on_transient(n, e, wid, attempt)
                log(f"  ↻ [W{wid}] #{n} unknown err, retry (no rotate): {e!s:.120}")
                await asyncio.sleep(2.0)
                continue

        if not counted and pool.done.is_set():
            await pool.release_inflight()

    log(f"  [W{wid}] exit")


async def run_loop(
    chunks: list[str],
    count: int,
    outdir: Path,
    voice_id: str,
    model_id: str,
    lang: str,
    speed: float,
    max_per_ip: int,
    soft_cap_per_ip: int,
    workers: int,
    max_transient_retries: int,
    hsw_workers: int | None = None,
    token_target: int | None = None,
    hsw_via_proxy: bool = False,
) -> int:
    cfg = load_proxyxoay_cfg()
    api_key = (cfg.get("api_key") or "").strip()
    if not api_key:
        log("ERROR: missing api_key in .proxyxoay.json")
        return 2

    outdir.mkdir(parents=True, exist_ok=True)
    existing = sorted(outdir.glob("tts_*.mp3"))
    resume_ok = len(existing)

    proxy = fetch_proxyxoay(api_key, change_ip=False)
    info = probe_proxy_exit(proxy, timeout=15.0)
    exit_ip = info.get("exit_ip") or "?"
    avg_c = sum(len(c) for c in chunks) // max(1, len(chunks))

    k = hsw_workers if hsw_workers and hsw_workers > 0 else default_hsw_workers()
    # 1 token = 1 TTS: giữ sẵn ≈ số workers (pipeline), không TTL
    target = token_target if token_target and token_target > 0 else workers
    refillers = min(max(2, k), max(1, target))

    log(
        f"start proxy exit={exit_ip} target={count} resume_ok={resume_ok} "
        f"tts_workers={workers} hsw_workers={k} token_target={target} "
        f"max_per_ip={max_per_ip or 'off'} soft_cap≈{soft_cap_per_ip}"
    )
    log(
        f"  text chunks={len(chunks)} avg={avg_c}c "
        f"min={min(len(c) for c in chunks)}c max={max(len(c) for c in chunks)}c"
    )
    log(f"  first: \"{chunks[0][:90]}{'…' if len(chunks[0]) > 90 else ''}\"")
    if resume_ok >= count:
        log(f"already have {resume_ok} >= {count} — nothing to do")
        return 0

    # 1) warm HSW farm  2) start token pre-warm  3) fire TTS workers
    farm = await start_hsw_farm(
        size=k, proxy_http=proxy, via_proxy=hsw_via_proxy, warm=True
    )
    token_pool = TokenPool(
        proxy=proxy,
        target=target,
        refillers=refillers,
        farm=farm,
    )
    await token_pool.start()

    # small head-start so first jobs often hit warm tokens
    for _ in range(40):
        if token_pool.ready >= min(2, target) or token_pool.ready >= target:
            break
        await asyncio.sleep(0.25)
    log(f"  [token-pool] head-start ready={token_pool.ready}/{target}")

    pool = SharedPool(
        api_key=api_key,
        proxy=proxy,
        exit_ip=exit_ip,
        count=count,
        outdir=outdir,
        max_per_ip=max_per_ip,
        resume_ok=resume_ok,
        token_pool=token_pool,
    )

    try:
        tasks = [
            asyncio.create_task(
                worker(
                    i + 1,
                    pool,
                    chunks,
                    voice_id,
                    model_id,
                    lang,
                    speed,
                    max_transient_retries,
                ),
                name=f"tts-w{i+1}",
            )
            for i in range(max(1, workers))
        ]
        await asyncio.gather(*tasks)
    finally:
        await token_pool.stop()
        await close_hsw_farm()

    summary = {
        "timestamp": datetime.now().isoformat(),
        "ok": pool.ok,
        "fail_block": pool.fail_block,
        "fail_transient": pool.fail_transient,
        "workers": workers,
        "hsw_workers": k,
        "token_target": target,
        "token_pool_stats": token_pool.stats,
        "ip_stats": pool.ip_stats,
        "history_tail": pool.history[-200:],
    }
    summary_path = outdir / "loop_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    log(
        f"\n══ DONE ok={pool.ok} block_fail={pool.fail_block} "
        f"transient={pool.fail_transient} tts_w={workers} hsw_w={k} ══"
    )
    log(f"  token-pool stats: {json.dumps(token_pool.stats)}")
    log(f"  per-IP successes: {json.dumps(pool.ip_stats)}")
    log(f"  summary → {summary_path}")
    return 0 if pool.ok >= count else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Local fast TTS: HSW farm + token pool + parallel TTS workers"
    )
    ap.add_argument(
        "--text-file",
        default=str(Path(__file__).resolve().parent / "long_text.txt"),
        help="file văn bản nguồn (mặc định: long_text.txt cạnh script)",
    )
    ap.add_argument(
        "--text",
        default=None,
        help="text thẳng (chỉ dùng nếu không có --text-file / file thiếu)",
    )
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--outdir", default="tts_loop_out")
    ap.add_argument("--voice", default=DEFAULT_VOICE)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--lang", default="en")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument(
        "--workers",
        type=int,
        default=4,
        help="số TTS workers song song (default 4)",
    )
    ap.add_argument(
        "--hsw-workers",
        type=int,
        default=0,
        help="số page HSW song song (0=auto 2–4, env HSW_WORKERS)",
    )
    ap.add_argument(
        "--token-target",
        type=int,
        default=0,
        help="số token sẵn trước (= số TTS song song, 0 = workers). 1 token = 1 TTS",
    )
    ap.add_argument(
        "--hsw-via-proxy",
        action="store_true",
        help="HSW browser đi qua proxy (chậm hơn; chỉ khi no-proxy fail)",
    )
    ap.add_argument(
        "--max-per-ip",
        type=int,
        default=0,
        help="0 = tắt: call đến khi block mới rotate",
    )
    ap.add_argument("--soft-cap", type=int, default=5)
    ap.add_argument(
        "--chunk-max",
        type=int,
        default=DEFAULT_CHUNK_MAX,
        help="max chars mỗi chunk TTS (mặc định 950, hard cap 1000, cắt theo từ)",
    )
    ap.add_argument(
        "--max-transient-retries",
        type=int,
        default=8,
        help="số lần log backoff trước khi delay dài hơn (vẫn retry, không rotate)",
    )
    args = ap.parse_args()

    text_file = Path(args.text_file) if args.text_file else None
    if text_file and not text_file.is_file():
        if args.text:
            log(f"WARN: text-file missing ({text_file}), fallback --text")
            text_file = None
        else:
            log(f"ERROR: text-file not found: {text_file}")
            return 2

    try:
        chunks = load_text_chunks(
            text_file=text_file,
            plain_text=args.text,
            max_chars=max(80, min(args.chunk_max, DEFAULT_CHUNK_HARD_MAX)),
            hard_max=DEFAULT_CHUNK_HARD_MAX,
        )
    except Exception as e:
        log(f"ERROR loading text: {e}")
        return 2

    log("fast_tts_loop — HSW farm + token pool + TTS workers")
    log(
        f"  count={args.count} outdir={args.outdir} "
        f"tts_workers={args.workers} hsw_workers={args.hsw_workers or 'auto'} "
        f"token_target={args.token_target or 'auto'} "
        f"max_per_ip={args.max_per_ip or 'off'} "
        f"text_file={text_file or '(inline)'} chunks={len(chunks)}"
    )

    return asyncio.run(
        run_loop(
            chunks=chunks,
            count=args.count,
            outdir=Path(args.outdir),
            voice_id=args.voice,
            model_id=args.model,
            lang=args.lang,
            speed=args.speed,
            max_per_ip=args.max_per_ip,
            soft_cap_per_ip=args.soft_cap,
            workers=max(1, args.workers),
            max_transient_retries=max(1, args.max_transient_retries),
            hsw_workers=args.hsw_workers or None,
            token_target=args.token_target or None,
            hsw_via_proxy=bool(args.hsw_via_proxy),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
