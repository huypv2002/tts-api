#!/usr/bin/env python3
"""
fast_tts.py — HSW token + anonymous TTS (local tool, max speed)

Pipeline:
  1) tls_client: checksiteconfig → req JWT          (qua proxy job)
  2) HSW Farm: K Camoufox pages, hsw(req) song song (mặc định NO proxy)
  3) tls_client: getcaptcha → generated_pass_UUID   (qua proxy job)
  4) httpx: POST .../anonymous (CÙNG proxy) → MP3

Local multi-worker:
  TokenPool pre-warm token (TTL ~50s) → TTS workers chỉ pop token + call_tts.
  Dùng qua fast_tts_loop.py hoặc import TokenPool / start_hsw_farm.

Usage:
  python3 fast_tts.py "Hello" --proxy http://user:pass@host:port
  python3 fast_tts.py "Hello" --proxy-key PROXYXOAY_KEY
  python3 fast_tts.py "Hello" --auto-proxy
  python3 fast_tts.py --token-only

Env:
  HTTP_PROXY / HTTPS_PROXY / PROXY  — proxy URL
  PROXYXOAY_KEY                     — proxyxoay key
  HSW_WORKERS=3                     — số page HSW song song (default auto 2–4)
  HSW_VIA_PROXY=0                   — 1 = browser HSW đi proxy (chậm hơn)
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import json
import os
import random
import re
import sys
import time
import warnings
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

warnings.filterwarnings("ignore")

try:
    import tls_client
except ImportError:
    print("Missing tls_client: pip3 install tls-client", file=sys.stderr)
    sys.exit(1)

try:
    import httpx
except ImportError:
    print("Missing httpx: pip3 install httpx", file=sys.stderr)
    sys.exit(1)

try:
    import jwt as pyjwt
except ImportError:
    pyjwt = None

try:
    from camoufox.async_api import AsyncCamoufox
except ImportError:
    print("Missing camoufox: pip3 install camoufox && camoufox fetch", file=sys.stderr)
    sys.exit(1)

SITEKEY = "8e58fe8c-1a48-4f94-88ae-8e90b586a192"
HOST = "elevenlabs.io"
API_BASE = "https://api.elevenlabs.io"
DEFAULT_VOICE = "NOpBlnGInO9m6vDvFkFC"
DEFAULT_MODEL = "eleven_v3"

_hsw_js_cache: dict[str, str] = {}
_hcaptcha_ver_cache: dict[str, tuple[float, str]] = {}  # proxy_key -> (ts, version)
_HCAPTCHA_VER_TTL = 300.0

# ── HSW Farm (local tool): K parallel pages, default NO proxy ─────────────
# HSW is WASM/JS compute — browser does not need residential proxy.
# Materials + getcaptcha + TTS still go through the job proxy.
_HSW_RECYCLE_EVERY = 50  # relaunch a page after N solves (leak guard)


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def default_hsw_workers() -> int:
    """Auto-size HSW pages from CPU (capped — Camoufox is heavy)."""
    n = _env_int("HSW_WORKERS", 0)
    if n > 0:
        return max(1, min(n, 8))
    cpus = os.cpu_count() or 4
    # 2–4 pages is the sweet spot on most laptops/servers
    return max(2, min(4, max(2, cpus // 3)))


def _proxy_key(proxy_http: str | None) -> str:
    return proxy_http or "__direct__"


# Module-level farm (lazy). Prefer get_hsw_farm() / start_hsw_farm().
_hsw_farm: "HswFarm | None" = None
_hsw_farm_lock: asyncio.Lock | None = None


def _farm_lock() -> asyncio.Lock:
    global _hsw_farm_lock
    if _hsw_farm_lock is None:
        _hsw_farm_lock = asyncio.Lock()
    return _hsw_farm_lock

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
SEC_CH = '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"'

ELEVENLABS_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json",
    "origin": "https://elevenlabs.io",
    "referer": "https://elevenlabs.io/",
    "sec-ch-ua": SEC_CH,
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": UA,
}


def log(msg: str) -> None:
    print(msg, flush=True)


def _decode_jwt_payload(token: str) -> dict:
    if pyjwt is not None:
        return pyjwt.decode(token, options={"verify_signature": False})
    # fallback without PyJWT
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part))


def _tls_session(proxy_http: str | None):
    session = tls_client.Session(
        client_identifier="chrome_130",
        random_tls_extension_order=True,
    )
    session.headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://newassets.hcaptcha.com",
        "referer": "https://newassets.hcaptcha.com/",
        "sec-ch-ua": SEC_CH,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "user-agent": UA,
    }
    if proxy_http:
        session.proxies = {"http": proxy_http, "https": proxy_http}
    return session


def get_hcaptcha_materials(proxy_http: str | None) -> tuple[str, str, dict]:
    """checksiteconfig → (req_token, version, config). Caches api.js version briefly."""
    session = _tls_session(proxy_http)
    t0 = time.time()
    pk = _proxy_key(proxy_http)
    now = time.time()
    version = None
    cached = _hcaptcha_ver_cache.get(pk)
    if cached and (now - cached[0]) < _HCAPTCHA_VER_TTL:
        version = cached[1]
    if not version:
        api_js = session.get(
            "https://hcaptcha.com/1/api.js?render=explicit&onload=hcaptchaOnLoad"
        ).text
        versions = re.findall(r"v1/([A-Za-z0-9]+)/static", api_js)
        version = versions[1] if len(versions) > 1 else (versions[0] if versions else "unknown")
        _hcaptcha_ver_cache[pk] = (now, version)

    config = session.post(
        "https://api2.hcaptcha.com/checksiteconfig",
        params={
            "v": version,
            "host": HOST,
            "sitekey": SITEKEY,
            "sc": "1",
            "swa": "1",
            "spst": "1",
        },
    ).json()

    if "c" not in config or "req" not in config.get("c", {}):
        raise RuntimeError(f"checksiteconfig failed: {json.dumps(config)[:200]}")

    log(f"  [1/4] materials OK version={version} ({time.time()-t0:.1f}s)")
    return config["c"]["req"], version, config


def _fetch_hsw_js(req_token: str, proxy_http: str | None) -> tuple[str, str]:
    decoded = _decode_jwt_payload(req_token)
    cache_key = decoded["l"]
    if cache_key in _hsw_js_cache:
        return cache_key, _hsw_js_cache[cache_key]
    session = _tls_session(proxy_http)
    hsw_url = "https://newassets.hcaptcha.com" + cache_key + "/hsw.js"
    hsw_js = session.get(hsw_url).text
    if not hsw_js or "function" not in hsw_js:
        raise RuntimeError("hsw.js fetch invalid")
    _hsw_js_cache[cache_key] = hsw_js
    return cache_key, hsw_js


class _HswPage:
    """One Camoufox page that can run hsw(req) under its own lock."""

    __slots__ = ("idx", "page", "lock", "js_key", "solves", "alive")

    def __init__(self, idx: int, page):
        self.idx = idx
        self.page = page
        self.lock = asyncio.Lock()
        self.js_key: str | None = None
        self.solves = 0
        self.alive = True


class HswFarm:
    """
    Parallel HSW solvers for local multi-worker tools.

    Default: 1 Camoufox browser, K pages, NO residential proxy on the browser.
    Concurrent hsw() runs on different pages → K× HSW throughput vs old global lock.
    """

    def __init__(
        self,
        size: int | None = None,
        via_proxy: bool | None = None,
    ):
        self.size = max(1, size if size is not None else default_hsw_workers())
        # HSW_VIA_PROXY=1 forces browser through job proxy (slower; only if no-proxy fails A/B)
        self.via_proxy = (
            _env_bool("HSW_VIA_PROXY", False) if via_proxy is None else via_proxy
        )
        self._browser = None
        self._cm = None  # AsyncCamoufox context manager (for clean stop)
        self._pages: list[_HswPage] = []
        self._free: asyncio.Queue | None = None
        self._start_lock = asyncio.Lock()
        self._started = False
        self._browser_proxy: str | None = None  # only used if via_proxy
        self.total_solves = 0

    @property
    def workers(self) -> int:
        return self.size

    async def start(self, proxy_http: str | None = None) -> None:
        async with self._start_lock:
            if self._started and self._pages:
                # via_proxy: relaunch only if browser proxy string changed
                if self.via_proxy and self._browser_proxy != (proxy_http or None):
                    await self._shutdown_unlocked()
                else:
                    return
            await self._launch(proxy_http if self.via_proxy else None)

    async def _launch(self, browser_proxy: str | None) -> None:
        t0 = time.time()
        opts: dict = {
            "headless": True,
            "os": "windows",
            "window": (1280, 720),
        }
        if browser_proxy:
            opts["proxy"] = {"server": browser_proxy}
        cm = AsyncCamoufox(**opts)
        self._cm = cm
        if hasattr(cm, "start"):
            self._browser = await cm.start()
        else:
            self._browser = await cm.__aenter__()
        self._browser_proxy = browser_proxy
        self._pages = []
        self._free = asyncio.Queue()
        for i in range(self.size):
            page = await _camoufox_new_page(self._browser)
            await self._prep_page(page)
            slot = _HswPage(i, page)
            self._pages.append(slot)
            await self._free.put(slot)
        self._started = True
        mode = f"proxy={browser_proxy}" if browser_proxy else "no-proxy"
        log(
            f"  [hsw-farm] started size={self.size} {mode} "
            f"in {time.time()-t0:.1f}s"
        )

    async def _prep_page(self, page) -> None:
        try:
            await page.route(
                f"https://{HOST}/hsw",
                lambda r: r.fulfill(
                    status=200,
                    content_type="text/html",
                    body="<html><head></head><body></body></html>",
                ),
            )
        except Exception:
            pass
        try:
            await page.goto(
                f"https://{HOST}/hsw", wait_until="domcontentloaded", timeout=12000
            )
        except Exception:
            await page.goto("about:blank", wait_until="domcontentloaded", timeout=8000)
        try:
            await page.evaluate(
                "Object.defineProperty(navigator, 'webdriver', {get: () => false})"
            )
        except Exception:
            pass

    async def _inject(self, slot: _HswPage, hsw_js: str, cache_key: str) -> None:
        if slot.js_key == cache_key:
            try:
                if await slot.page.evaluate("typeof hsw === 'function'"):
                    return
            except Exception:
                pass
        injected = False
        try:
            await slot.page.add_script_tag(content=hsw_js)
            if await slot.page.evaluate("typeof hsw === 'function'"):
                injected = True
        except Exception:
            pass
        if not injected:
            await slot.page.evaluate(hsw_js)
            if not await slot.page.evaluate("typeof hsw === 'function'"):
                raise RuntimeError("hsw function not available after inject")
        slot.js_key = cache_key

    async def _recycle_page(self, slot: _HswPage, hsw_js: str, cache_key: str) -> None:
        """Replace a dead/leaky page; keep the rest of the farm warm."""
        try:
            await slot.page.close()
        except Exception:
            pass
        page = await _camoufox_new_page(self._browser)
        await self._prep_page(page)
        slot.page = page
        slot.js_key = None
        slot.solves = 0
        slot.alive = True
        await self._inject(slot, hsw_js, cache_key)

    async def solve(self, req_token: str, proxy_http: str | None = None) -> str:
        """Run hsw(req). proxy_http only used for hsw.js download + via_proxy mode."""
        t0 = time.time()
        # hsw.js fetch can use job proxy (TLS) or direct — job proxy is safer on locked nets
        cache_key, hsw_js = await asyncio.to_thread(_fetch_hsw_js, req_token, proxy_http)
        log(f"  [2/4] hsw.js ready ({len(hsw_js)//1024}KB) farm={self.size}")

        await self.start(proxy_http)
        assert self._free is not None

        slot: _HswPage = await self._free.get()
        try:
            async with slot.lock:
                try:
                    if slot.solves >= _HSW_RECYCLE_EVERY:
                        await self._recycle_page(slot, hsw_js, cache_key)
                    else:
                        await self._inject(slot, hsw_js, cache_key)
                    result = await slot.page.evaluate("(req) => hsw(req)", req_token)
                    if not result:
                        raise RuntimeError("hsw() returned empty")
                except Exception as e:
                    msg = str(e).lower()
                    if any(
                        x in msg
                        for x in (
                            "webassembly",
                            "hsw",
                            "target closed",
                            "ismobile",
                            "destroyed",
                            "crashed",
                        )
                    ):
                        _hsw_js_cache.pop(cache_key, None)
                    log(
                        f"  [2/4] HSW page#{slot.idx} fail, recycle: "
                        f"{type(e).__name__}: {e}"[:160]
                    )
                    await self._recycle_page(slot, hsw_js, cache_key)
                    result = await slot.page.evaluate("(req) => hsw(req)", req_token)
                    if not result:
                        raise RuntimeError("hsw() returned empty after recycle") from e

                slot.solves += 1
                self.total_solves += 1
                log(
                    f"  [2/4] HSW solved ({time.time()-t0:.1f}s, "
                    f"len={len(str(result))}, page#{slot.idx} "
                    f"n={slot.solves} farm_total={self.total_solves})"
                )
                return result
        finally:
            await self._free.put(slot)

    async def warm(self, proxy_http: str | None = None) -> None:
        """Pre-launch farm + inject current hsw.js so first job is fast."""
        try:
            req, _, _ = await asyncio.to_thread(get_hcaptcha_materials, proxy_http)
            cache_key, hsw_js = await asyncio.to_thread(_fetch_hsw_js, req, proxy_http)
            await self.start(proxy_http)
            assert self._free is not None
            # inject on every page so all K are ready
            for _ in range(self.size):
                slot = await self._free.get()
                try:
                    async with slot.lock:
                        await self._inject(slot, hsw_js, cache_key)
                finally:
                    await self._free.put(slot)
            log(f"  [warm] HSW farm ready size={self.size}")
        except Exception as e:
            log(f"  [warm] skip: {e}")

    async def _shutdown_unlocked(self) -> None:
        """
        Fully tear down Camoufox so Windows Proactor does not print
        'I/O operation on closed pipe' during GC.
        """
        for slot in self._pages:
            try:
                await slot.page.close()
            except Exception:
                pass
        self._pages.clear()

        browser = self._browser
        cm = self._cm
        self._browser = None
        self._cm = None
        self._browser_proxy = None
        self._started = False
        self._free = None

        # Prefer official stop paths in order
        for closer in (
            (lambda: browser.stop() if browser is not None and hasattr(browser, "stop") else None),
            (lambda: browser.close() if browser is not None and hasattr(browser, "close") else None),
            (lambda: cm.__aexit__(None, None, None) if cm is not None and hasattr(cm, "__aexit__") else None),
            (lambda: cm.stop() if cm is not None and hasattr(cm, "stop") else None),
        ):
            try:
                coro = closer()
                if coro is not None:
                    await coro
            except Exception:
                pass

        # Let subprocess pipes drain (Windows asyncio Proactor)
        try:
            await asyncio.sleep(0.35)
        except Exception:
            pass

    async def close(self) -> None:
        async with self._start_lock:
            await self._shutdown_unlocked()


async def get_hsw_farm(
    size: int | None = None,
    via_proxy: bool | None = None,
) -> HswFarm:
    """Lazy singleton farm (shared by solve_token / loop / token pool)."""
    global _hsw_farm
    async with _farm_lock():
        if _hsw_farm is None:
            _hsw_farm = HswFarm(size=size, via_proxy=via_proxy)
        elif size is not None and size != _hsw_farm.size and not _hsw_farm._started:
            _hsw_farm.size = max(1, size)
        return _hsw_farm


async def start_hsw_farm(
    size: int | None = None,
    proxy_http: str | None = None,
    via_proxy: bool | None = None,
    warm: bool = True,
) -> HswFarm:
    """Explicit start for local tools (call once at loop startup)."""
    farm = await get_hsw_farm(size=size, via_proxy=via_proxy)
    if warm:
        await farm.warm(proxy_http)
    else:
        await farm.start(proxy_http)
    return farm


async def close_hsw_farm() -> None:
    global _hsw_farm
    async with _farm_lock():
        if _hsw_farm is not None:
            try:
                await _hsw_farm.close()
            except Exception as e:
                log(f"  [hsw-farm] close: {e}"[:120])
            _hsw_farm = None
        # Windows: give event loop a tick so subprocess transports finish
        if sys.platform == "win32":
            try:
                await asyncio.sleep(0.2)
            except Exception:
                pass


async def solve_hsw(req_token: str, proxy_http: str | None) -> str:
    """Camoufox HSW via parallel farm (backward-compatible entry)."""
    farm = await get_hsw_farm()
    return await farm.solve(req_token, proxy_http)


async def _camoufox_new_page(browser):
    """
    Create a page without triggering Playwright 1.61+ Firefox CDP bug:
      Browser.setDefaultViewport ... isMobile not described in this scheme
    """
    # BrowserContext already (persistent_context=True path)
    if hasattr(browser, "pages") and not hasattr(browser, "new_context"):
        try:
            return await browser.new_page()
        except Exception:
            pages = browser.pages
            if pages:
                return pages[0]
            raise

    # Prefer context with no viewport (skips mobile viewport flags when possible)
    last_err: Exception | None = None
    for kwargs in (
        {"viewport": None},
        {"no_viewport": True},
        {"viewport": {"width": 1280, "height": 720}},
        {},
    ):
        try:
            if hasattr(browser, "new_context"):
                ctx = await browser.new_context(**kwargs)
                page = await ctx.new_page()
                return page
        except TypeError:
            # older playwright may not accept no_viewport / viewport=None
            continue
        except Exception as e:
            last_err = e
            msg = str(e)
            if "isMobile" not in msg and "setDefaultViewport" not in msg:
                # unrelated error — keep trying lighter opts only if viewport-related
                if "viewport" not in msg.lower():
                    continue
            continue

    # Last resort: direct new_page
    try:
        return await browser.new_page()
    except Exception as e:
        last_err = e

    # Monkey-patch: strip isMobile from CDP if playwright still injects it
    try:
        return await _new_page_strip_ismobile(browser)
    except Exception as e:
        last_err = e

    raise RuntimeError(
        f"Camoufox new_page failed (playwright/camoufox viewport mismatch). "
        f"Pin playwright<1.61 on the server. Last error: {last_err}"
    )


async def _new_page_strip_ismobile(browser):
    """
    Patch the browser connection session to drop isMobile from setDefaultViewport.
    Works around Playwright 1.61 + Camoufox Firefox Juggler incompatibility.
    """
    # Get underlying connection from first context or browser
    browser_impl = getattr(browser, "_impl_obj", browser)
    conn = getattr(browser_impl, "_connection", None) or getattr(
        getattr(browser_impl, "_browser", None), "_connection", None
    )
    if conn is None:
        # try contexts
        for ctx in getattr(browser, "contexts", []) or []:
            impl = getattr(ctx, "_impl_obj", ctx)
            conn = getattr(impl, "_connection", None)
            if conn:
                break
    if conn is None:
        raise RuntimeError("cannot access playwright connection to patch viewport")

    original = conn.send

    async def send_filtered(method, params=None, *args, **kwargs):
        if params is None:
            params = {}
        # method may be str like "Browser.setDefaultViewport"
        m = method if isinstance(method, str) else str(method)
        if "setDefaultViewport" in m and isinstance(params, dict):
            params = dict(params)
            vp = params.get("viewport")
            if isinstance(vp, dict) and "isMobile" in vp:
                vp = dict(vp)
                vp.pop("isMobile", None)
                params["viewport"] = vp
            params.pop("isMobile", None)
        return await original(method, params, *args, **kwargs)

    conn.send = send_filtered  # type: ignore
    try:
        if hasattr(browser, "new_context"):
            ctx = await browser.new_context(viewport={"width": 1280, "height": 720})
            return await ctx.new_page()
        return await browser.new_page()
    finally:
        conn.send = original  # type: ignore


def submit_captcha(
    hsw_token: str, version: str, config: dict, proxy_http: str | None
) -> str:
    """getcaptcha → generated_pass_UUID."""
    t0 = time.time()
    session = _tls_session(proxy_http)
    motion = {
        "st": int(time.time() * 1000),
        "dct": int(time.time() * 1000),
        "mm": [
            [random.randint(100, 800), random.randint(100, 600), random.randint(10, 500)]
            for _ in range(3)
        ],
    }
    data = {
        "v": version,
        "sitekey": SITEKEY,
        "host": HOST,
        "hl": "en",
        "motionData": json.dumps(motion),
        "n": hsw_token,
        "c": json.dumps(config["c"]),
    }
    resp = session.post(f"https://api2.hcaptcha.com/getcaptcha/{SITEKEY}", data=data)
    result = resp.json()

    if "generated_pass_UUID" in result:
        token = result["generated_pass_UUID"]
        log(f"  [3/4] token OK ({time.time()-t0:.1f}s) prefix={token[:28]}... len={len(token)}")
        return token
    if "tasklist" in result:
        raise RuntimeError("image_challenge — proxy/IP needs visual captcha")
    raise RuntimeError(f"getcaptcha failed: {json.dumps(result)[:200]}")


def extract_audio_from_stream(text: str) -> bytes:
    output = bytearray()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        chunk = parsed.get("audio_base64")
        if not chunk:
            continue
        try:
            output.extend(base64.b64decode(chunk, validate=True))
        except binascii.Error:
            output.extend(base64.b64decode(chunk))
    return bytes(output)


async def call_tts(
    text: str,
    hcaptcha_token: str,
    proxy_http: str | None,
    voice_id: str,
    model_id: str,
    language_code: str,
    speed: float,
) -> bytes:
    """POST anonymous stream endpoint (httpx, same proxy as token)."""
    t0 = time.time()
    url = f"{API_BASE}/v1/text-to-speech/{voice_id}/stream/with-timestamps/anonymous"
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {"speed": speed, "stability": 0.5, "similarity_boost": 0.75},
        "hcaptcha_token": hcaptcha_token,
        "language_code": language_code,
    }

    async with httpx.AsyncClient(
        proxy=proxy_http,
        timeout=120.0,
        verify=False,
        headers=ELEVENLABS_HEADERS,
    ) as client:
        resp = await client.post(url, json=payload)

    region = resp.headers.get("x-region", "?")
    log(f"  [4/4] TTS status={resp.status_code} x-region={region} ({time.time()-t0:.1f}s)")

    if resp.status_code == 200:
        audio = extract_audio_from_stream(resp.text)
        if audio:
            return audio
        if resp.content and len(resp.content) > 100 and not resp.text.lstrip().startswith("{"):
            return resp.content
        raise RuntimeError("200 but no audio_base64")

    body = resp.text[:400]
    raise RuntimeError(f"TTS HTTP {resp.status_code}: {body}")


async def solve_token(proxy_http: str | None) -> str:
    """
    Full captcha token (on-demand).
    materials + getcaptcha via proxy; HSW via farm (default no browser proxy).
    """
    t0 = time.time()
    req, version, config = await asyncio.to_thread(get_hcaptcha_materials, proxy_http)
    hsw = await solve_hsw(req, proxy_http)
    token = await asyncio.to_thread(submit_captcha, hsw, version, config, proxy_http)
    log(f"  [token] full solve {time.time()-t0:.1f}s")
    return token


async def warm_hsw(proxy_http: str | None = None) -> None:
    """Pre-launch HSW farm so first request is fast."""
    farm = await get_hsw_farm()
    await farm.warm(proxy_http)


class TokenRecord:
    """One captcha token ↔ one TTS call. No TTL — only gen/proxy validity."""

    __slots__ = ("token", "proxy", "gen")

    def __init__(self, token: str, proxy: str | None, gen: int):
        self.token = token
        self.proxy = proxy
        self.gen = gen


class TokenPool:
    """
    1 token = 1 TTS call. Không quan tâm TTL.

    refillers giữ queue sẵn ≈ target (= số TTS workers) để worker không chờ.
    take() lấy đúng 1 token cho 1 call_tts.
    Chỉ vứt token khi rotate proxy (gen đổi) — không drop vì “hết hạn”.
    """

    def __init__(
        self,
        proxy: str | None,
        target: int = 3,
        refillers: int | None = None,
        farm: HswFarm | None = None,
        **_ignored,  # accept legacy ttl=… without error
    ):
        self.proxy = proxy
        # target = số token sẵn ≈ số TTS call song song (không phải TTL buffer)
        self.target = max(1, target)
        self.refillers = max(
            1, refillers if refillers is not None else min(self.target, 3)
        )
        self.farm = farm
        self.gen = 0
        self._q: asyncio.Queue[TokenRecord] = asyncio.Queue()
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._lock = asyncio.Lock()
        self.stats = {
            "produced": 0,
            "consumed": 0,
            "stale": 0,
            "invalidated": 0,
            "errors": 0,
        }

    @property
    def ready(self) -> int:
        return self._q.qsize()

    async def start(self) -> None:
        if self._tasks:
            return
        if self.farm is None:
            self.farm = await get_hsw_farm()
            await self.farm.start(self.proxy)
        self._stop.clear()
        for i in range(self.refillers):
            self._tasks.append(
                asyncio.create_task(self._refill_loop(i + 1), name=f"token-refill-{i+1}")
            )
        log(
            f"  [token-pool] start 1token=1tts target={self.target} "
            f"refillers={self.refillers} proxy={'yes' if self.proxy else 'direct'}"
        )

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self._drain()

    async def _drain(self) -> int:
        n = 0
        while not self._q.empty():
            try:
                self._q.get_nowait()
                n += 1
            except asyncio.QueueEmpty:
                break
        if n:
            self.stats["invalidated"] += n
        return n

    async def on_proxy_changed(self, proxy: str | None, reason: str = "") -> None:
        """Rotate IP → vứt token cũ (cùng exit IP mới mới dùng được)."""
        async with self._lock:
            self.proxy = proxy
            self.gen += 1
            dropped = await self._drain()
        log(
            f"  [token-pool] invalidate gen={self.gen} dropped={dropped} "
            f"reason={reason or 'proxy-change'}"
        )

    def _usable(self, rec: TokenRecord) -> bool:
        return rec.gen == self.gen and rec.proxy == self.proxy

    async def take(self, timeout: float = 90.0) -> str:
        """Lấy đúng 1 token cho 1 TTS. Chờ refillers; hết timeout → mint on-demand."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                rec = self._q.get_nowait()
            except asyncio.QueueEmpty:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                try:
                    rec = await asyncio.wait_for(
                        self._q.get(), timeout=min(0.5, remaining)
                    )
                except asyncio.TimeoutError:
                    continue

            if not self._usable(rec):
                self.stats["stale"] += 1
                continue
            self.stats["consumed"] += 1
            log(f"  [token-pool] take → tts ready={self.ready}/{self.target}")
            return rec.token

        log("  [token-pool] starve → on-demand 1 token for 1 tts")
        return await solve_token(self.proxy)

    async def _mint_one(self, rid: int) -> TokenRecord | None:
        """Mint đúng 1 token (1 TTS)."""
        async with self._lock:
            proxy = self.proxy
            gen = self.gen
        try:
            t0 = time.time()
            req, version, config = await asyncio.to_thread(
                get_hcaptcha_materials, proxy
            )
            assert self.farm is not None
            hsw = await self.farm.solve(req, proxy)
            if gen != self.gen or proxy != self.proxy:
                log(f"  [token-pool R{rid}] discard stale mint gen={gen}→{self.gen}")
                return None
            token = await asyncio.to_thread(
                submit_captcha, hsw, version, config, proxy
            )
            if gen != self.gen:
                return None
            rec = TokenRecord(token, proxy, gen)
            self.stats["produced"] += 1
            log(
                f"  [token-pool R{rid}] +1 token ({time.time()-t0:.1f}s) "
                f"ready≈{self.ready + 1}/{self.target}"
            )
            return rec
        except Exception as e:
            self.stats["errors"] += 1
            log(f"  [token-pool R{rid}] mint fail: {type(e).__name__}: {e}"[:160])
            await asyncio.sleep(0.6)
            return None

    async def _refill_loop(self, rid: int) -> None:
        """Giữ sẵn ~target token (= số TTS song song). Mỗi token = 1 call TTS."""
        while not self._stop.is_set():
            try:
                if self.ready >= self.target:
                    await asyncio.sleep(0.1)
                    continue
                rec = await self._mint_one(rid)
                if rec is None:
                    await asyncio.sleep(0.15)
                    continue
                await self._q.put(rec)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.stats["errors"] += 1
                log(f"  [token-pool R{rid}] loop: {e}"[:140])
                await asyncio.sleep(0.8)


# proxyxoay.net rotating residential API
# Docs: https://proxyxoay.net/api-document/rotating
PROXYXOAY_NET_STATUS = "https://proxyxoay.net/api/rotating-proxy/key-status/{key}"
PROXYXOAY_NET_CHANGE_IP = "https://proxyxoay.net/api/rotating-proxy/change-key-ip/{key}"
# legacy shop API (old keys)
PROXYXOAY_SHOP_API = "https://proxyxoay.shop/api/get.php"
DIRECT_IP_CACHE: str | None = None
CFG_PROXYXOAY = Path(__file__).resolve().parent / ".proxyxoay.json"


def normalize_proxy(p: str | None) -> str | None:
    if not p:
        return None
    p = p.strip()
    if not p:
        return None
    if "://" not in p:
        p = "http://" + p
    return p


def get_direct_ip() -> str:
    global DIRECT_IP_CACHE
    if DIRECT_IP_CACHE:
        return DIRECT_IP_CACHE
    try:
        r = httpx.get("https://api.ipify.org?format=json", timeout=10.0)
        DIRECT_IP_CACHE = r.json().get("ip", "")
    except Exception:
        DIRECT_IP_CACHE = ""
    return DIRECT_IP_CACHE or ""


def probe_proxy_exit(proxy_http: str, timeout: float = 8.0) -> dict:
    """Verify CONNECT tunnel changes exit IP."""
    t0 = time.time()
    try:
        with httpx.Client(proxy=proxy_http, timeout=timeout, verify=False) as client:
            r = client.get("https://api.ipify.org?format=json")
            exit_ip = r.json().get("ip")
        direct = get_direct_ip()
        ok = bool(exit_ip) and exit_ip != direct
        return {
            "proxy": proxy_http,
            "exit_ip": exit_ip,
            "direct_ip": direct,
            "changed": ok,
            "ms": int((time.time() - t0) * 1000),
        }
    except Exception as e:
        return {
            "proxy": proxy_http,
            "error": str(e)[:120],
            "changed": False,
            "ms": int((time.time() - t0) * 1000),
        }


def load_proxyxoay_cfg() -> dict:
    if CFG_PROXYXOAY.exists():
        try:
            return json.loads(CFG_PROXYXOAY.read_text())
        except Exception:
            return {}
    return {}


def proxyxoay_net_from_status(key: str) -> str:
    """
    proxyxoay.net rotating: key-status → host:port + user:pass
    GET https://proxyxoay.net/api/rotating-proxy/key-status/{api_key}
    """
    r = httpx.get(PROXYXOAY_NET_STATUS.format(key=key), timeout=20.0)
    data = r.json()
    if data.get("status") != 200:
        raise RuntimeError(f"key-status fail: {data}")
    d = data["data"]
    auth = d.get("authentication") or ""
    user = d.get("username") or (auth.split(":")[0] if ":" in auth else "")
    password = d.get("password") or (auth.split(":")[1] if ":" in auth else "")
    conn = d.get("proxy_connection") or {}
    host = conn.get("ip")
    port = conn.get("http_ipv4")
    if not host or not port or str(port) in ("-1", "0", ""):
        raise RuntimeError(f"no http_ipv4 in status: {conn}")
    if user and password:
        url = f"http://{user}:{password}@{host}:{port}"
    else:
        url = f"http://{host}:{port}"
    log(
        f"  [proxyxoay.net] {d.get('package_name')} | {host}:{port} "
        f"exp={d.get('expired_at')} status={d.get('status_str')}"
    )
    return url


def proxyxoay_net_change_ip(key: str) -> None:
    """Rotate exit IP (package may limit interval, e.g. 4 minutes)."""
    r = httpx.get(PROXYXOAY_NET_CHANGE_IP.format(key=key), timeout=30.0)
    data = r.json()
    log(f"  [proxyxoay.net] change-ip → {data.get('message') or data}")
    if data.get("status") != 200:
        raise RuntimeError(f"change-ip fail: {data}")
    time.sleep(3)  # docs: đợi vài giây


def fetch_proxyxoay(key: str | None = None, change_ip: bool = False) -> str:
    """
    Resolve residential rotating proxy URL.
    Prefer proxyxoay.net API; fallback shop get.php for old keys.
    """
    cfg = load_proxyxoay_cfg()
    key = (key or cfg.get("api_key") or os.environ.get("PROXYXOAY_KEY") or "").strip()
    if not key:
        # static host from config file
        if cfg.get("host") and cfg.get("http_port"):
            u = cfg.get("username") or ""
            p = cfg.get("password") or ""
            if u and p:
                return f"http://{u}:{p}@{cfg['host']}:{cfg['http_port']}"
            return f"http://{cfg['host']}:{cfg['http_port']}"
        raise RuntimeError("proxyxoay key empty (set .proxyxoay.json or PROXYXOAY_KEY)")

    # proxyxoay.net rotating (primary)
    try:
        if change_ip:
            try:
                proxyxoay_net_change_ip(key)
            except Exception as e:
                # still usable with current IP if cooldown
                log(f"  [proxyxoay.net] change-ip skipped: {e}")
        return proxyxoay_net_from_status(key)
    except Exception as e_net:
        log(f"  [proxyxoay.net] status fail: {e_net}")

    # legacy shop API (old keys only)
    url = f"{PROXYXOAY_SHOP_API}?key={key}&nhamang=random&tinhthanh=0&whitelist="
    try:
        r = httpx.get(url, timeout=15.0)
        data = r.json()
        if data.get("status") == 100:
            raw = str(data["proxyhttp"]).rstrip(":")
            log(f"  [proxyxoay.shop] got {raw}")
            return normalize_proxy(raw)  # type: ignore
        log(f"  [proxyxoay.shop] {data}")
    except Exception as e:
        log(f"  [proxyxoay.shop] {e}")

    # static config fallback
    cfg = load_proxyxoay_cfg()
    if cfg.get("host") and cfg.get("http_port"):
        u, p = cfg.get("username", ""), cfg.get("password", "")
        if u and p:
            return f"http://{u}:{p}@{cfg['host']}:{cfg['http_port']}"
        return f"http://{cfg['host']}:{cfg['http_port']}"
    raise RuntimeError("cannot resolve proxyxoay proxy URL")


def load_proxy_candidates() -> list[str]:
    """Local lists + public free lists."""
    pool: list[str] = []
    base = Path(__file__).resolve().parent
    for name in ("proxy5_working.txt", "proxy5_vietnam.txt", "working_proxies.txt"):
        f = base / name
        if f.exists():
            for line in f.read_text().splitlines():
                line = line.strip()
                if re.match(r"^\d+\.\d+\.\d+\.\d+:\d+$", line):
                    pool.append(line)
                elif "://" in line or "@" in line:
                    pool.append(line)

    # prior lab results
    lab = base / "lab_network_ip_tts_results.json"
    if lab.exists():
        try:
            data = json.loads(lab.read_text())
            for g in data.get("goodProxies") or []:
                if g.get("proxy"):
                    pool.append(g["proxy"])
        except Exception:
            pass

    urls = [
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    ]
    for url in urls:
        try:
            t = httpx.get(url, timeout=12.0).text
            n = [
                ln.strip()
                for ln in t.splitlines()
                if re.match(r"^\d+\.\d+\.\d+\.\d+:\d+$", ln.strip())
            ]
            log(f"  [pool] {url.split('/')[-1]}: {len(n)}")
            pool.extend(n)
        except Exception as e:
            log(f"  [pool] fail {e}")

    # unique + shuffle
    seen = set()
    out = []
    for p in pool:
        if p not in seen:
            seen.add(p)
            out.append(p)
    random.shuffle(out)
    return out


def _score_exit_with_iplocate(exit_ip: str) -> dict:
    """
    IPLocate.io = geo/privacy intelligence ONLY (does NOT sell/route proxy).
    Docs: https://www.iplocate.io/docs
    Key: env IPLOCATE_API_KEY or .iplocate_key
    Use to rank free-proxy exits before HSW+TTS.
    """
    try:
        key = ""
        kf = Path(__file__).resolve().parent / ".iplocate_key"
        if kf.exists():
            key = kf.read_text().strip()
        key = os.environ.get("IPLOCATE_API_KEY", key).strip()
        if not key:
            return {"score": 0, "note": "no iplocate key"}
        url = f"https://iplocate.io/api/lookup/{exit_ip}?apikey={key}"
        r = httpx.get(url, timeout=12.0, headers={"X-API-Key": key, "Accept": "application/json"})
        info = r.json()
        if r.status_code != 200:
            return {"score": 0, "error": str(info)[:100]}
        p = info.get("privacy") or {}
        asn_type = (info.get("asn") or {}).get("type") or (info.get("company") or {}).get("type") or "?"
        s = 50
        if p.get("is_abuser"):
            s -= 50
        if p.get("is_proxy"):
            s -= 40
        if p.get("is_vpn"):
            s -= 40
        if p.get("is_tor"):
            s -= 45
        if p.get("is_hosting"):
            s -= 35
        if asn_type == "isp":
            s += 30
        elif asn_type == "hosting":
            s -= 25
        elif asn_type == "business":
            s += 5
        return {
            "score": s,
            "country": info.get("country_code"),
            "asn_type": asn_type,
            "asn": (info.get("asn") or {}).get("name"),
            "is_proxy": p.get("is_proxy"),
            "is_vpn": p.get("is_vpn"),
            "is_hosting": p.get("is_hosting"),
            "is_abuser": p.get("is_abuser"),
        }
    except Exception as e:
        return {"score": 0, "error": str(e)[:80]}


def find_working_proxies(max_test: int = 60, want: int = 5, use_iplocate: bool = True) -> list[str]:
    """Scan free proxies; keep those that actually change exit IP.
    Rank by IPLocate score when key present (.iplocate_key / IPLOCATE_API_KEY).
    """
    cands = load_proxy_candidates()[:max_test]
    log(f"  [auto-proxy] testing {len(cands)} (want {want} that change IP)...")
    direct = get_direct_ip()
    log(f"  [auto-proxy] direct IP={direct}")
    raw_good: list[dict] = []

    def one(raw: str) -> dict:
        return probe_proxy_exit(normalize_proxy(raw), timeout=7.0)  # type: ignore

    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = {ex.submit(one, c): c for c in cands}
        for fut in as_completed(futs):
            rec = fut.result()
            if rec.get("changed"):
                proxy = normalize_proxy(rec["proxy"]) or rec["proxy"]
                exit_ip = rec.get("exit_ip")
                intel = {}
                if use_iplocate and exit_ip:
                    intel = _score_exit_with_iplocate(exit_ip)
                score = intel.get("score", 0)
                log(
                    f"  ✓ {proxy} → {exit_ip} ({rec.get('ms')}ms) "
                    f"iplocate={score} {intel.get('country') or ''} "
                    f"asn={intel.get('asn_type') or '?'} "
                    f"pxy={intel.get('is_proxy')} host={intel.get('is_hosting')}"
                )
                raw_good.append({"proxy": proxy, "exit_ip": exit_ip, "intel": intel, "score": score})
                # gather more than want so ranking has room
                if len(raw_good) >= max(want * 3, want + 4):
                    break

    # prefer higher IPLocate score (cleaner residential-ish)
    raw_good.sort(key=lambda x: x.get("score", 0), reverse=True)
    # demote obvious dirty flags
    def rank_key(x):
        intel = x.get("intel") or {}
        pen = 0
        if intel.get("is_abuser"):
            pen -= 100
        if intel.get("is_proxy"):
            pen -= 50
        if intel.get("is_hosting"):
            pen -= 40
        if intel.get("is_vpn"):
            pen -= 40
        return x.get("score", 0) + pen

    raw_good.sort(key=rank_key, reverse=True)
    good = [x["proxy"] for x in raw_good[:want]]
    if raw_good:
        log("  [iplocate rank] top:")
        for x in raw_good[: min(8, len(raw_good))]:
            log(
                f"    score={x.get('score')} exit={x.get('exit_ip')} "
                f"{(x.get('intel') or {}).get('asn_type')} → {x.get('proxy')}"
            )
    log(f"  [auto-proxy] selected {len(good)} / found {len(raw_good)}")
    return good


async def run_once(
    text: str,
    proxy_http: str | None,
    voice_id: str,
    model_id: str,
    language_code: str,
    speed: float,
    out_path: Path | None,
    token_only: bool,
    retries: int,
    proxy_list: list[str] | None = None,
    require_proxy: bool = False,
) -> int:
    """
    retries: attempts per proxy.
    proxy_list: if set, rotate across proxies on failure.
    """
    if require_proxy and not proxy_http and not proxy_list:
        log("ERROR: --require-proxy but no proxy available")
        return 2

    proxies: list[str | None]
    if proxy_list:
        proxies = list(proxy_list)
    elif proxy_http:
        proxies = [proxy_http]
    else:
        proxies = [None]

    last_err: Exception | None = None
    attempt_global = 0
    # warm 1-page farm for single-shot (still faster after first cold start)
    try:
        await start_hsw_farm(
            size=1,
            proxy_http=proxies[0] if proxies else None,
            warm=True,
        )
    except Exception as e:
        log(f"  [warm] optional skip: {e}")

    try:
        for proxy in proxies:
            if require_proxy and not proxy:
                continue
            # soft probe — free proxies flake; still try solve if probe slow/fails
            if proxy:
                info = await asyncio.to_thread(probe_proxy_exit, proxy, 12.0)
                if info.get("changed"):
                    log(f"  [use] {proxy} exit={info.get('exit_ip')}")
                elif info.get("error"):
                    log(f"  [try] {proxy} probe={info.get('error')[:80]} — still attempt HSW")
                else:
                    log(
                        f"  [skip] proxy same-as-direct: {proxy} exit={info.get('exit_ip')}"
                    )
                    continue

            for attempt in range(1, retries + 1):
                attempt_global += 1
                try:
                    log(
                        f"\n══ Attempt {attempt_global} "
                        f"proxy={proxy or 'DIRECT'} (try {attempt}/{retries}) ══"
                    )
                    t_all = time.time()
                    token = await solve_token(proxy)
                    if token_only:
                        print(token)
                        log(f"Total token-only: {time.time()-t_all:.1f}s")
                        return 0

                    audio = await call_tts(
                        text, token, proxy, voice_id, model_id, language_code, speed
                    )
                    path = out_path or Path(f"fast_tts_{int(time.time())}.mp3")
                    path.write_bytes(audio)
                    meta = {
                        "proxy": proxy,
                        "bytes": len(audio),
                        "file": str(path),
                        "seconds": round(time.time() - t_all, 2),
                    }
                    Path("fast_tts_last.json").write_text(json.dumps(meta, indent=2))
                    log(
                        f"✅ Saved {path} ({len(audio)} bytes) "
                        f"total={time.time()-t_all:.1f}s proxy={proxy}"
                    )
                    return 0
                except Exception as e:
                    last_err = e
                    log(f"❌ {type(e).__name__}: {e}")
                    msg = str(e).lower()
                    if any(
                        x in msg
                        for x in ("401", "unusual", "429", "image_challenge", "proxy")
                    ):
                        break  # next proxy
                    if attempt < retries:
                        await asyncio.sleep(1.2)

        log(f"FAILED after all proxies: {last_err}")
        return 1
    finally:
        await close_hsw_farm()


def resolve_proxy(cli_proxy: str | None) -> str | None:
    p = (
        cli_proxy
        or os.environ.get("PROXY")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("http_proxy")
        or os.environ.get("https_proxy")
        or ""
    ).strip()
    return normalize_proxy(p)


def main() -> int:
    ap = argparse.ArgumentParser(description="Fast HSW token + ElevenLabs anonymous TTS")
    ap.add_argument("text", nargs="?", default="Hello from fast HSW TTS via proxy.")
    ap.add_argument("--proxy", default=None, help="http://user:pass@host:port or host:port")
    ap.add_argument(
        "--proxy-key",
        default=None,
        help="proxyxoay.net rotating api_key (or .proxyxoay.json)",
    )
    ap.add_argument(
        "--proxyxoay",
        action="store_true",
        help="Use .proxyxoay.json / PROXYXOAY_KEY (proxyxoay.net residential)",
    )
    ap.add_argument(
        "--change-ip",
        action="store_true",
        help="Call proxyxoay change-key-ip before TTS (respect package interval)",
    )
    ap.add_argument(
        "--auto-proxy",
        action="store_true",
        help="Scan free proxies that change exit IP, then TTS through them",
    )
    ap.add_argument(
        "--require-proxy",
        action="store_true",
        default=True,
        help="Refuse DIRECT (default: on for this mode)",
    )
    ap.add_argument("--allow-direct", action="store_true", help="Allow DIRECT if no proxy")
    ap.add_argument("--voice", default=DEFAULT_VOICE)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--lang", default="en")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("-o", "--output", default=None)
    ap.add_argument("--token-only", action="store_true")
    ap.add_argument("--retries", type=int, default=2, help="retries per proxy")
    ap.add_argument("--max-test", type=int, default=50, help="auto-proxy max candidates")
    ap.add_argument("--want", type=int, default=4, help="auto-proxy how many to keep")
    args = ap.parse_args()

    require_proxy = not args.allow_direct
    proxy = resolve_proxy(args.proxy)
    proxy_list: list[str] | None = None

    # proxyxoay.net residential (preferred)
    px_key = (args.proxy_key or os.environ.get("PROXYXOAY_KEY") or "").strip()
    cfg = load_proxyxoay_cfg()
    if not px_key:
        px_key = (cfg.get("api_key") or "").strip()
    use_px = args.proxyxoay or bool(px_key) or bool(cfg.get("host"))

    if not proxy and use_px and not args.auto_proxy:
        log("  [mode] proxyxoay.net residential")
        try:
            proxy = fetch_proxyxoay(px_key or None, change_ip=args.change_ip)
        except Exception as e:
            log(f"  [proxyxoay] fail: {e}")
            # static fallback from config
            if cfg.get("host") and cfg.get("http_port"):
                u, p = cfg.get("username", ""), cfg.get("password", "")
                proxy = (
                    f"http://{u}:{p}@{cfg['host']}:{cfg['http_port']}"
                    if u and p
                    else f"http://{cfg['host']}:{cfg['http_port']}"
                )
                log(f"  [proxyxoay] static fallback {cfg['host']}:{cfg['http_port']}")

    if args.auto_proxy or (require_proxy and not proxy):
        if not proxy:
            log("  [mode] auto-proxy scan (no fixed proxy provided)")
            found = find_working_proxies(max_test=args.max_test, want=args.want)
            if not found:
                log("ERROR: no working proxy found that changes IP")
                return 2
            proxy_list = found
            proxy = found[0]

    out = Path(args.output) if args.output else None

    log("fast_tts — HSW + PROXY")
    log(f"  sitekey={SITEKEY}")
    log(f"  proxy={proxy or (proxy_list and proxy_list[0]) or 'DIRECT'}")
    if proxy_list:
        log(f"  proxy_list={len(proxy_list)}")
    log(f"  voice={args.voice} model={args.model} lang={args.lang}")
    log(f"  require_proxy={require_proxy}")

    return asyncio.run(
        run_once(
            text=args.text,
            proxy_http=proxy,
            voice_id=args.voice,
            model_id=args.model,
            language_code=args.lang,
            speed=args.speed,
            out_path=out,
            token_only=args.token_only,
            retries=args.retries,
            proxy_list=proxy_list,
            require_proxy=require_proxy,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())

