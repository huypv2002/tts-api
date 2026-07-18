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
except ImportError as _camoufox_imp_err:  # noqa: F841
    AsyncCamoufox = None  # type: ignore[misc, assignment]
    # Không sys.exit — app UI vẫn mở; lỗi hiện khi start HSW/TTS
    def _camoufox_missing(*_a, **_k):  # type: ignore[misc]
        raise RuntimeError(
            "Thiếu package camoufox trong bản build (không phải thiếu folder browser).\n"
            f"Import error: {_camoufox_imp_err}\n"
            "Cần rebuild Nuitka với --include-package=camoufox --include-package=playwright.\n"
            "Portable: vẫn cần folder camoufox-browser/ cạnh EXE."
        )

    AsyncCamoufox = _camoufox_missing  # type: ignore[misc, assignment]

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
        if AsyncCamoufox is None or not callable(AsyncCamoufox):
            raise RuntimeError(
                "Package camoufox chưa được đóng gói vào EXE. "
                "Cần bản build có --include-package=camoufox."
            )
        # Portable: trỏ Camoufox vào camoufox-browser/ cạnh EXE (hoặc auto-fetch)
        try:
            from app_paths import ensure_camoufox_browser, setup_portable_runtime

            setup_portable_runtime()
            fox_dir = await asyncio.to_thread(ensure_camoufox_browser, True)
            log(f"  [hsw-farm] camoufox dir={fox_dir}")
        except Exception as e:
            log(f"  [hsw-farm] camoufox setup warn: {e}")
            # nếu chỉ thiếu browser folder nhưng package OK — vẫn thử launch (camoufox tự fetch)
        opts: dict = {
            "headless": True,
            "os": "windows",
            "window": (1280, 720),
        }
        if browser_proxy:
            opts["proxy"] = {"server": browser_proxy}
        try:
            cm = AsyncCamoufox(**opts)
        except TypeError:
            # fallback nếu AsyncCamoufox bị gán stub
            raise RuntimeError(
                "Không khởi tạo được Camoufox. Kiểm tra package + folder camoufox-browser/."
            ) from None
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

    async def _inject_fresh(self, slot: _HswPage, hsw_js: str, cache_key: str) -> None:
        """Single inject on a page that does not already have hsw()."""
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

    async def _inject(self, slot: _HswPage, hsw_js: str, cache_key: str) -> None:
        # Reuse only if same hsw.js already on page (single inject).
        if slot.js_key == cache_key:
            try:
                if await slot.page.evaluate("typeof hsw === 'function'"):
                    return
            except Exception:
                pass
        # Different key or broken page → new page (never double-inject).
        try:
            already = await slot.page.evaluate("typeof hsw === 'function'")
        except Exception:
            already = False
        if already or slot.js_key is not None:
            await self._recycle_page(slot, hsw_js, cache_key)
            return
        await self._inject_fresh(slot, hsw_js, cache_key)

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
        await self._inject_fresh(slot, hsw_js, cache_key)

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
        """
        Pre-launch Camoufox pages only.

        KHÔNG pre-inject hsw.js: inject sớm (materials/HSW của warm) làm
        proof getcaptcha bị reject (trả lại c.type=hsw). Inject đúng lúc
        solve với req thật của job.
        """
        try:
            await self.start(proxy_http)
            log(f"  [warm] HSW farm ready size={self.size} (browser only, no inject)")
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
    """getcaptcha → generated_pass_UUID (logic cũ: 1 lần, không multi-round)."""
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
    try:
        result = resp.json()
    except Exception:
        raise RuntimeError(f"getcaptcha non-json: {resp.text[:200]}")

    if "generated_pass_UUID" in result:
        token = result["generated_pass_UUID"]
        log(
            f"  [3/4] token OK ({time.time()-t0:.1f}s) "
            f"prefix={token[:28]}... len={len(token)}"
        )
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


# Model hỗ trợ language_code (anonymous probe): v3 / turbo_v2_5 / flash_v2_5
# multilingual_v2 + turbo_v2 + flash_v2: không gửi language_code (400 nếu ép vi)
MODELS_WITH_LANGUAGE_CODE = frozenset(
    {
        "eleven_v3",
        "eleven_turbo_v2_5",
        "eleven_flash_v2_5",
    }
)


def model_accepts_language_code(model_id: str) -> bool:
    mid = (model_id or "").strip()
    return mid in MODELS_WITH_LANGUAGE_CODE


async def call_tts(
    text: str,
    hcaptcha_token: str,
    proxy_http: str | None,
    voice_id: str,
    model_id: str,
    language_code: str,
    speed: float,
) -> bytes:
    """POST anonymous stream endpoint (httpx, same proxy as token).

    Chỉ gửi speed — stability / similarity_boost để API dùng mặc định.
    language_code chỉ gửi khi model hỗ trợ (tránh 400 trên multilingual_v2…).
    """
    t0 = time.time()
    url = f"{API_BASE}/v1/text-to-speech/{voice_id}/stream/with-timestamps/anonymous"
    # clamp speed like ElevenLabs client ranges
    speed = max(0.7, min(1.2, float(speed or 1.0)))
    mid = (model_id or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    payload: dict = {
        "text": text,
        "model_id": mid,
        "voice_settings": {
            "speed": speed,
        },
        "hcaptcha_token": hcaptcha_token,
    }
    lang = (language_code or "").strip()
    if lang and model_accepts_language_code(mid):
        payload["language_code"] = lang

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

    body = (resp.text or "")[:500]
    if resp.status_code == 401:
        low = body.lower()
        if "landing page" in low or "sign_in_required" in low or "limit of available" in low:
            raise RuntimeError(
                "TTS_LANDING_LIMIT: IP này đã hết lượt free landing page. "
                "Cần đổi IP proxy và chờ trước khi gen tiếp. "
                f"chi tiết={body[:180]}"
            )
        raise RuntimeError(
            f"TTS HTTP 401 (token/IP bị từ chối — cần token mới hoặc đổi IP proxy): {body}"
        )
    if resp.status_code == 429:
        raise RuntimeError(f"TTS HTTP 429 (quá nhiều request — chậm lại): {body}")
    raise RuntimeError(f"TTS HTTP {resp.status_code}: {body}")


async def solve_token(proxy_http: str | None) -> str:
    """
    Full captcha token (on-demand) — logic cũ:
    materials + getcaptcha qua proxy; HSW qua farm (default no browser proxy).
    1 vòng HSW → getcaptcha; fail thì raise (retry/đổi IP ở pipeline).
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
    """
    One captcha token ↔ one TTS call on THE SAME proxy exit.
    materials + getcaptcha + call_tts MUST all use rec.proxy.
    """

    __slots__ = ("token", "proxy", "gen")

    def __init__(self, token: str, proxy: str | None, gen: int):
        self.token = token
        self.proxy = proxy  # exact URL used when solving captcha
        self.gen = gen


class TokenPool:
    """
    1 token = 1 TTS call trên ĐÚNG proxy lúc solve.

    Nối đuôi + SONG SONG với TTS:
      • Refiller mint với self.proxy; TokenRecord.proxy gắn vĩnh viễn
      • take() → (token, proxy) — caller PHẢI call_tts(token, proxy đó)
      • Đổi IP → gen++ + drop queue (token IP cũ không tái dùng)

    Chỉ vứt token khi rotate proxy (gen đổi).
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
        # target = số token sẵn trong pool (buffer nối đuôi)
        self.target = max(1, target)
        self.refillers = max(
            1, refillers if refillers is not None else min(self.target, 3)
        )
        self.farm = farm
        self.gen = 0
        self._q: asyncio.Queue[TokenRecord] = asyncio.Queue()
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()  # take() / TTS → wake refill ngay
        self._wake_epoch = 0  # chống miss wakeup khi clear+wait
        self._tasks: list[asyncio.Task] = []
        self._lock = asyncio.Lock()
        self._inflight = 0  # số mint đang chạy (đặt chỗ)
        # Pause mint khi lane đang đổi IP / cooldown (tránh đốt token trên IP chết)
        self._paused = False
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

    @property
    def inflight(self) -> int:
        return self._inflight

    def _need_more(self) -> bool:
        """Còn slot trống? (ready + đang mint < target)."""
        return (self.ready + self._inflight) < self.target

    def _kick(self) -> None:
        """Đánh thức refiller (sau take / đang TTS / mint xong / đổi proxy)."""
        self._wake_epoch += 1
        self._wake.set()

    def kick_refill(self) -> None:
        """Public: gọi khi bắt đầu TTS để mint chạy song song I/O TTS."""
        self._kick()

    async def start(self) -> None:
        if self._tasks:
            return
        if self.farm is None:
            self.farm = await get_hsw_farm()
            await self.farm.start(self.proxy)
        self._stop.clear()
        self._kick()  # warm: mint ngay lên target
        for i in range(self.refillers):
            self._tasks.append(
                asyncio.create_task(self._refill_loop(i + 1), name=f"token-refill-{i+1}")
            )
        log(
            f"  [token-pool] start nối-đuôi∥TTS target={self.target} "
            f"refillers={self.refillers} proxy={'yes' if self.proxy else 'direct'}"
        )

    async def stop(self) -> None:
        self._stop.set()
        self._kick()
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

    async def set_paused(self, paused: bool, reason: str = "") -> None:
        """
        Pause: dừng mint + vứt token sẵn (IP đang bad / đang rotate).
        Resume: caller phải gọi on_proxy_changed(new_url) hoặc set_paused(False)
        sau khi IP ổn.
        """
        async with self._lock:
            self._paused = bool(paused)
            dropped = 0
            if self._paused:
                self.gen += 1  # invalidate inflight mints
                dropped = await self._drain()
        if self._paused:
            log(
                f"  [token-pool] PAUSE gen={self.gen} dropped={dropped} "
                f"reason={reason or 'pause'}"
            )
        else:
            log(f"  [token-pool] RESUME reason={reason or 'resume'}")
            self._kick()

    async def on_proxy_changed(self, proxy: str | None, reason: str = "") -> None:
        """Rotate IP → vứt token cũ; kick mint lại trên IP mới."""
        async with self._lock:
            self.proxy = proxy
            self.gen += 1
            self._paused = False  # IP mới → cho mint lại
            dropped = await self._drain()
            # inflight mints sẽ discard vì gen lệch
        log(
            f"  [token-pool] invalidate gen={self.gen} dropped={dropped} "
            f"reason={reason or 'proxy-change'}"
        )
        self._kick()

    def _usable(self, rec: TokenRecord) -> bool:
        # Token chỉ hợp lệ nếu gen + proxy URL khớp pool hiện tại
        return (
            rec.gen == self.gen
            and bool(rec.token)
            and (rec.proxy or None) == (self.proxy or None)
        )

    def _proxy_host(self, proxy: str | None) -> str:
        if not proxy:
            return "direct"
        return proxy.split("@")[-1]

    async def take(self, timeout: float = 90.0) -> tuple[str, str | None]:
        """
        Lấy 1 token + proxy URL đã dùng khi solve.
        Caller MUST: call_tts(text, token, proxy) — không được đổi proxy.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._paused:
                # Lane đang đổi IP — không take / không starve mint
                await asyncio.sleep(0.35)
                continue
            try:
                rec = self._q.get_nowait()
            except asyncio.QueueEmpty:
                self._kick()
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                try:
                    rec = await asyncio.wait_for(
                        self._q.get(), timeout=min(0.4, remaining)
                    )
                except asyncio.TimeoutError:
                    continue

            if self._paused or not self._usable(rec):
                self.stats["stale"] += 1
                self._kick()
                continue
            self.stats["consumed"] += 1
            # Nối đuôi: vừa lấy 1 → mint bù ngay (∥ TTS phía sau)
            self._kick()
            log(
                f"  [token-pool] take → ready={self.ready}/{self.target} "
                f"inflight={self._inflight} "
                f"bind={self._proxy_host(rec.proxy)} (token⇄proxy)"
            )
            return rec.token, rec.proxy

        if self._paused:
            raise RuntimeError("token-pool đang pause (đổi IP) — thử lại")

        # Starve: solve on-demand trên ĐÚNG self.proxy hiện tại
        log("  [token-pool] starve → on-demand 1 token trên proxy pool")
        self._kick()
        async with self._lock:
            if self._paused:
                raise RuntimeError("token-pool đang pause (đổi IP) — thử lại")
            px = self.proxy
            gen = self.gen
        token = await solve_token(px)
        # Nếu vừa rotate trong lúc solve → token có thể stale; caller retry
        if gen != self.gen or px != self.proxy or self._paused:
            raise RuntimeError(
                "token solved nhưng proxy đã rotate — thử lại (token⇄proxy)"
            )
        return token, px

    async def _try_reserve(self) -> bool:
        """Đặt chỗ 1 mint nếu còn slot. Atomic với ready+inflight."""
        async with self._lock:
            if (self.ready + self._inflight) >= self.target:
                return False
            self._inflight += 1
            return True

    async def _release_reserve(self) -> None:
        async with self._lock:
            self._inflight = max(0, self._inflight - 1)

    async def _mint_one(self, rid: int) -> TokenRecord | None:
        """
        Mint 1 token trên snapshot proxy (materials + getcaptcha cùng IP).
        HSW farm no-proxy (compute); HTTP captcha bám proxy.
        """
        async with self._lock:
            proxy = self.proxy
            gen = self.gen
        if not proxy:
            log(f"  [token-pool R{rid}] skip mint — no proxy bound")
            await asyncio.sleep(0.5)
            return None
        try:
            t0 = time.time()
            # Cùng proxy cho materials + getcaptcha (= IP sẽ dùng cho TTS)
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
            if gen != self.gen or proxy != self.proxy:
                return None
            # Gắn cứng proxy lúc solve — TTS phải dùng đúng URL này
            rec = TokenRecord(token, proxy, gen)
            self.stats["produced"] += 1
            log(
                f"  [token-pool R{rid}] +1 ({time.time()-t0:.1f}s) "
                f"ready≈{self.ready + 1}/{self.target} "
                f"bind={self._proxy_host(proxy)} ∥tts"
            )
            return rec
        except Exception as e:
            self.stats["errors"] += 1
            log(f"  [token-pool R{rid}] mint fail: {type(e).__name__}: {e}"[:160])
            await asyncio.sleep(0.5)
            return None

    async def _wait_slot(self) -> None:
        """Chờ pool còn chỗ trống hoặc bị kick (không miss wakeup)."""
        while not self._need_more() and not self._stop.is_set():
            ep = self._wake_epoch
            self._wake.clear()
            # kick xảy ra giữa check và clear?
            if self._wake_epoch != ep or self._need_more() or self._stop.is_set():
                return
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=0.2)
            except asyncio.TimeoutError:
                pass

    async def _refill_loop(self, rid: int) -> None:
        """
        Background mint — chạy song song với call_tts.
        take()/kick_refill() đánh thức ngay khi có slot trống.
        """
        while not self._stop.is_set():
            try:
                if self._paused:
                    await asyncio.sleep(0.4)
                    continue
                if not self._need_more():
                    await self._wait_slot()
                    continue

                if not await self._try_reserve():
                    # refiller khác vừa đặt chỗ hết slot
                    await asyncio.sleep(0.03)
                    continue

                try:
                    if self._paused:
                        continue
                    rec = await self._mint_one(rid)
                finally:
                    await self._release_reserve()

                if rec is None:
                    self._kick()
                    await asyncio.sleep(0.12)
                    continue

                if self._paused or not self._usable(rec):
                    continue

                await self._q.put(rec)
                # yield ngay để TTS / refiller khác xen kẽ
                await asyncio.sleep(0)
                if self._need_more():
                    self._kick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.stats["errors"] += 1
                log(f"  [token-pool R{rid}] loop: {e}"[:140])
                await asyncio.sleep(0.6)
                self._kick()


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
    """Rotate exit IP (package may limit interval, e.g. 1–4 minutes)."""
    r = httpx.get(PROXYXOAY_NET_CHANGE_IP.format(key=key), timeout=30.0)
    data = r.json()
    log(f"  [proxyxoay.net] change-ip → {data.get('message') or data}")
    if data.get("status") != 200:
        wait_s = parse_proxy_cooldown(data, default=60.0)
        raise RuntimeError(
            f"change-ip fail: {data.get('message') or data} | wait_s={wait_s:.1f}"
        )
    time.sleep(3)  # docs: đợi vài giây


def parse_proxyhttp(raw: str) -> str:
    """
    Parse proxyxoay.shop proxyhttp field → http:// URL.
    Forms: host:port | host:port:: | host:port:user:pass
    """
    s = (raw or "").strip()
    if not s:
        raise RuntimeError("empty proxyhttp")
    if "://" in s:
        return normalize_proxy(s) or s
    # strip trailing empty segments from "ip:port::"
    while s.endswith(":"):
        s = s[:-1]
    parts = s.split(":")
    if len(parts) < 2:
        raise RuntimeError(f"bad proxyhttp: {raw!r}")
    host, port = parts[0], parts[1]
    if not host or not port:
        raise RuntimeError(f"bad proxyhttp host/port: {raw!r}")
    if len(parts) >= 4:
        user = parts[2]
        password = ":".join(parts[3:])
        if user or password:
            return f"http://{user}:{password}@{host}:{port}"
    return f"http://{host}:{port}"


def parse_proxy_cooldown(
    source: object,
    *,
    default: float = 60.0,
    min_s: float = 3.0,
    max_s: float = 600.0,
) -> float:
    """
    Số giây CÒN LẠI trước lần xoay proxy kế tiếp.

    Gói xoay 1 phút/lần: API thường trả thời gian còn lại (giây hoặc ms).
    Ví dụ:
      message: "Con 35s moi co the doi proxy"
      field: time=35000 (ms) / thoigian=35 / wait=35
      net: "Please wait 60 seconds"
    """
    default = float(default if default is not None else 60.0)

    def _from_number(n: float, force_ms: bool = False) -> float | None:
        if n != n or n < 0:  # NaN
            return None
        # Rõ là ms
        if force_ms or n >= 1000:
            return max(min_s, min(max_s, n / 1000.0 + 1.5))
        # 0–600: coi là giây (còn lại trong chu kỳ 1–10 phút)
        if n <= max_s:
            return max(min_s, min(max_s, n + 1.5))
        return None

    def _from_text(msg: str) -> float | None:
        if not msg:
            return None
        # ms trước (35000ms, 35000 ms)
        m = re.search(r"(\d+(?:\.\d+)?)\s*ms\b", msg, re.I)
        if m:
            return _from_number(float(m.group(1)), force_ms=True)
        # phút
        m = re.search(r"(\d+(?:\.\d+)?)\s*(phút|minute|min)\b", msg, re.I)
        if m:
            return max(min_s, min(max_s, float(m.group(1)) * 60 + 2))
        # giây / s  — "Con 35s", "Còn 35 giây", "wait 60 seconds"
        m = re.search(
            r"(?:c[oò]n\s*)?(\d+(?:\.\d+)?)\s*(?:giây|seconds?|secs?|s)\b",
            msg,
            re.I,
        )
        if m:
            return _from_number(float(m.group(1)), force_ms=False)
        # "Con 35 moi…" không unit
        m = re.search(r"c[oò]n\s+(\d+(?:\.\d+)?)\b", msg, re.I)
        if m:
            return _from_number(float(m.group(1)), force_ms=False)
        # wait=35000 / remain: 35000
        m = re.search(
            r"(?:wait|remain(?:ing)?|cooldown|ttl|left)\s*[=:]?\s*(\d+(?:\.\d+)?)",
            msg,
            re.I,
        )
        if m:
            return _from_number(float(m.group(1)))
        return None

    # dict từ API
    if isinstance(source, dict):
        # Ưu tiên field chuyên cooldown (hay là ms)
        for k in (
            "time",
            "Time",
            "TIME",
            "ms",
            "remain",
            "remaining",
            "remain_time",
            "remaining_time",
            "countdown",
            "cooldown",
            "wait",
            "wait_time",
            "ttl",
            "thoigian",
            "thoi_gian",
            "thoigianconlai",
            "thoi_gian_con_lai",
            "next_change",
            "nextChange",
            "change_after",
            "retry_after",
        ):
            if k not in source or source[k] is None or source[k] == "":
                continue
            try:
                n = float(source[k])
            except (TypeError, ValueError):
                # field string "35s"
                got = _from_text(str(source[k]))
                if got is not None:
                    return got
                continue
            # heuristic: key có 'ms' hoặc value ≥ 1000 → ms
            force_ms = "ms" in k.lower() or n >= 1000
            got = _from_number(n, force_ms=force_ms)
            if got is not None:
                return got
        # message trong body
        for k in ("message", "msg", "error", "detail", "Message"):
            if source.get(k):
                got = _from_text(str(source.get(k)))
                if got is not None:
                    return got
        # dump toàn dict thành text
        got = _from_text(str(source))
        if got is not None:
            return got
        return max(min_s, min(max_s, default))

    got = _from_text(str(source or ""))
    if got is not None:
        return got
    return max(min_s, min(max_s, default))


def proxyxoay_shop_get(
    key: str,
    *,
    nhamang: str = "random",
    tinhthanh: str | int = 0,
    whitelist: str = "",
    method: str = "GET",
) -> dict:
    """
    proxyxoay.shop rotating get.
    GET/POST https://proxyxoay.shop/api/get.php
    success status=100 → proxyhttp / proxysocks5
    error status=101 / 102 — thường kèm thời gian còn lại (s hoặc ms)
      gói xoay 1 phút/lần → status=101 "Con Xs moi co the doi proxy"
    """
    key = (key or "").strip()
    if not key:
        raise RuntimeError("proxyxoay.shop key empty")
    params = {
        "key": key,
        "nhamang": (nhamang or "random").strip() or "random",
        "tinhthanh": str(tinhthanh if tinhthanh is not None else 0),
        "whitelist": whitelist or "",
    }
    m = (method or "GET").upper()
    if m == "POST":
        r = httpx.post(PROXYXOAY_SHOP_API, data=params, timeout=25.0)
    else:
        r = httpx.get(PROXYXOAY_SHOP_API, params=params, timeout=25.0)
    try:
        data = r.json()
    except Exception:
        raise RuntimeError(f"proxyxoay.shop non-json: {r.text[:200]}") from None
    st = data.get("status")
    if st != 100:
        # Gắn wait_s vào exception để pipeline nghỉ đúng chu kỳ xoay
        wait_s = parse_proxy_cooldown(data, default=60.0)
        msg = data.get("message") or data.get("msg") or data
        raise RuntimeError(
            f"proxyxoay.shop status={st}: {msg} | wait_s={wait_s:.1f}"
        )
    return data


def proxyxoay_shop_from_key(
    key: str,
    *,
    nhamang: str = "random",
    tinhthanh: str | int = 0,
    whitelist: str = "",
    method: str = "GET",
) -> str:
    """get.php → HTTP proxy URL."""
    data = proxyxoay_shop_get(
        key,
        nhamang=nhamang,
        tinhthanh=tinhthanh,
        whitelist=whitelist,
        method=method,
    )
    raw = str(data.get("proxyhttp") or "")
    url = parse_proxyhttp(raw)
    log(
        f"  [proxyxoay.shop] {data.get('Nha Mang') or '?'} / "
        f"{data.get('Vi Tri') or '?'} → {url.split('@')[-1]} "
        f"msg={str(data.get('message') or '')[:60]}"
    )
    return url


def detect_proxy_provider(provider: str | None = None, host: str | None = None) -> str:
    """Return 'proxyxoay_net' | 'proxyxoay_shop' | 'static'."""
    p = (provider or "").strip().lower()
    h = (host or "").strip().lower()
    if "shop" in p or "proxyxoay.shop" in h:
        return "proxyxoay_shop"
    if "net" in p or "proxyxoay.net" in h or "vipvn" in h:
        return "proxyxoay_net"
    if p in ("static", "http", "manual"):
        return "static"
    # default net rotating (current product default)
    if p:
        return p if p.startswith("proxyxoay") else "proxyxoay_net"
    return "proxyxoay_net" if h else "static"


def resolve_proxy_line(line: dict) -> str:
    """
    Resolve one proxy line dict → http:// URL.
    line keys: provider, api_key, host, port, username, password,
               shop_nhamang, shop_tinhthanh, shop_whitelist, shop_method, url
    """
    if line.get("url"):
        return normalize_proxy(str(line["url"])) or str(line["url"])
    provider = detect_proxy_provider(line.get("provider"), line.get("host"))
    key = (line.get("api_key") or "").strip()
    host = (line.get("host") or "").strip()
    port = int(line.get("port") or 0)
    user = (line.get("username") or "").strip()
    pw = (line.get("password") or "").strip()

    if provider == "proxyxoay_shop" and key:
        return proxyxoay_shop_from_key(
            key,
            nhamang=line.get("shop_nhamang") or line.get("nhamang") or "random",
            tinhthanh=line.get("shop_tinhthanh")
            if line.get("shop_tinhthanh") is not None
            else line.get("tinhthanh", 0),
            whitelist=line.get("shop_whitelist") or line.get("whitelist") or "",
            method=line.get("shop_method") or "GET",
        )
    if provider == "proxyxoay_net" and key:
        try:
            return proxyxoay_net_from_status(key)
        except Exception as e:
            log(f"  [proxyxoay.net] status fail, try static creds: {e}")
    # static host:port
    if host and port:
        if user and pw:
            return f"http://{user}:{pw}@{host}:{port}"
        return f"http://{host}:{port}"
    if key:
        # last resort: try both providers
        try:
            return proxyxoay_net_from_status(key)
        except Exception:
            return proxyxoay_shop_from_key(key)
    raise RuntimeError(f"cannot resolve proxy line id={line.get('id')}")


def rotate_proxy_line(line: dict) -> str:
    """
    Change IP for one line → new http:// URL.
    - proxyxoay_net: change-key-ip + key-status
    - proxyxoay_shop: get.php again (new exit) — gói thường 1 phút/lần
    - static / thiếu key: raise (caller nghỉ, không giả vờ đổi IP)
    """
    provider = detect_proxy_provider(line.get("provider"), line.get("host"))
    key = (line.get("api_key") or "").strip()
    if provider == "proxyxoay_shop" and key:
        # luôn gọi API mới — đừng dùng url cache
        line.pop("url", None)
        url = proxyxoay_shop_from_key(
            key,
            nhamang=line.get("shop_nhamang") or line.get("nhamang") or "random",
            tinhthanh=line.get("shop_tinhthanh")
            if line.get("shop_tinhthanh") is not None
            else line.get("tinhthanh", 0),
            whitelist=line.get("shop_whitelist") or line.get("whitelist") or "",
            method=line.get("shop_method") or "GET",
        )
        line["url"] = url
        return url
    if provider == "proxyxoay_net" and key:
        proxyxoay_net_change_ip(key)
        # status sau change — bỏ url cache
        line.pop("url", None)
        url = proxyxoay_net_from_status(key)
        line["url"] = url
        return url
    raise RuntimeError(
        f"không xoay được proxy provider={provider} (cần api_key shop/net)"
    )


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

    # legacy shop API (old keys)
    try:
        return proxyxoay_shop_from_key(key)
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

