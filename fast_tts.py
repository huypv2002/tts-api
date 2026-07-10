#!/usr/bin/env python3
"""
fast_tts.py — HSW token + anonymous TTS (port từ appTTs token_solver + tts_engine)

Pipeline (nhanh nhất đã chứng minh trên appTTs):
  1) tls_client: checksiteconfig → req JWT
  2) Camoufox: inject hsw.js → hsw(req)
  3) tls_client: getcaptcha → generated_pass_UUID
  4) httpx: POST .../anonymous (CÙNG proxy) → MP3

Usage:
  python3 fast_tts.py "Hello" --proxy http://user:pass@host:port
  python3 fast_tts.py "Hello" --proxy-key PROXYXOAY_KEY
  python3 fast_tts.py "Hello" --auto-proxy          # quét free proxy đổi IP
  HTTP_PROXY=http://host:port python3 fast_tts.py "test" -o out.mp3
  python3 fast_tts.py --token-only

Env:
  HTTP_PROXY / HTTPS_PROXY / PROXY  — proxy URL
  PROXYXOAY_KEY                     — key proxyxoay.shop
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
    """checksiteconfig → (req_token, version, config)."""
    session = _tls_session(proxy_http)
    t0 = time.time()
    api_js = session.get(
        "https://hcaptcha.com/1/api.js?render=explicit&onload=hcaptchaOnLoad"
    ).text
    versions = re.findall(r"v1/([A-Za-z0-9]+)/static", api_js)
    version = versions[1] if len(versions) > 1 else (versions[0] if versions else "unknown")

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


async def solve_hsw(req_token: str, proxy_http: str | None) -> str:
    """Camoufox: inject hsw.js → hsw(req)."""
    t0 = time.time()
    decoded = _decode_jwt_payload(req_token)
    cache_key = decoded["l"]

    if cache_key in _hsw_js_cache:
        hsw_js = _hsw_js_cache[cache_key]
        log(f"  [2/4] hsw.js cache hit ({len(hsw_js)//1024}KB)")
    else:
        session = _tls_session(proxy_http)
        hsw_url = "https://newassets.hcaptcha.com" + cache_key + "/hsw.js"
        hsw_js = session.get(hsw_url).text
        if not hsw_js or "function" not in hsw_js:
            raise RuntimeError("hsw.js fetch invalid")
        _hsw_js_cache[cache_key] = hsw_js
        log(f"  [2/4] hsw.js fetched ({len(hsw_js)//1024}KB)")

    opts: dict = {
        "headless": True,
        "os": "windows",
        "window": (1280, 720),
    }
    if proxy_http:
        # camoufox expects server URL; auth embedded in URL is fine
        opts["proxy"] = {"server": proxy_http}

    browser = None
    page = None
    try:
        # Prefer async context manager; fall back to .start() for older camoufox
        cm = AsyncCamoufox(**opts)
        if hasattr(cm, "start"):
            browser = await cm.start()
        else:
            browser = await cm.__aenter__()

        page = await _camoufox_new_page(browser)

        await page.route(
            f"https://{HOST}/hsw",
            lambda r: r.fulfill(
                status=200,
                content_type="text/html",
                body="<html><head></head><body></body></html>",
            ),
        )
        await page.goto(f"https://{HOST}/hsw", wait_until="domcontentloaded", timeout=15000)
        await page.evaluate(
            "Object.defineProperty(navigator, 'webdriver', {get: () => false})"
        )

        injected = False
        try:
            await page.add_script_tag(content=hsw_js)
            await asyncio.sleep(0.15)
            if await page.evaluate("typeof hsw === 'function'"):
                injected = True
        except Exception:
            pass

        if not injected:
            await page.evaluate(
                f"""(function() {{
                    const s = document.createElement('script');
                    s.textContent = {json.dumps(hsw_js)};
                    document.head.appendChild(s);
                }})();"""
            )
            await asyncio.sleep(0.15)
            if not await page.evaluate("typeof hsw === 'function'"):
                await page.evaluate(hsw_js)
                await asyncio.sleep(0.15)

        if not await page.evaluate("typeof hsw === 'function'"):
            raise RuntimeError("hsw function not available after inject")

        try:
            result = await page.evaluate("(req) => hsw(req)", req_token)
        except Exception as e:
            # stale hsw.js cache / wasm fail → clear & rethrow for retry
            if "WebAssembly" in str(e) or "hsw" in str(e).lower():
                _hsw_js_cache.pop(cache_key, None)
                _hsw_js_cache.clear()
            raise
        if not result:
            raise RuntimeError("hsw() returned empty")
        log(f"  [2/4] HSW solved ({time.time()-t0:.1f}s, len={len(str(result))})")
        return result
    finally:
        try:
            if page is not None:
                await page.close()
        except Exception:
            pass
        try:
            if browser is not None:
                if hasattr(browser, "stop"):
                    await browser.stop()
                elif hasattr(browser, "close"):
                    await browser.close()
        except Exception:
            pass


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
    req, version, config = await asyncio.to_thread(get_hcaptcha_materials, proxy_http)
    hsw = await solve_hsw(req, proxy_http)
    token = await asyncio.to_thread(submit_captcha, hsw, version, config, proxy_http)
    return token


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
                log(f"✅ Saved {path} ({len(audio)} bytes) total={time.time()-t_all:.1f}s proxy={proxy}")
                return 0
            except Exception as e:
                last_err = e
                log(f"❌ {type(e).__name__}: {e}")
                # rotate proxy on auth/risk failures
                msg = str(e).lower()
                if any(x in msg for x in ("401", "unusual", "429", "image_challenge", "proxy")):
                    break  # next proxy
                if attempt < retries:
                    await asyncio.sleep(1.2)

    log(f"FAILED after all proxies: {last_err}")
    return 1


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

