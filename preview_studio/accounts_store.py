# -*- coding: utf-8 -*-
"""Local accounts + packages DB (JSON) — tool only."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Optional

from app_paths import app_dir  # noqa: E402

_APP_DIR = app_dir()
ACCOUNTS_FILE = os.path.join(_APP_DIR, "accounts.json")
PROXIES_FILE = os.path.join(_APP_DIR, "proxies.json")
PACKAGES_FILE = os.path.join(_APP_DIR, "packages.json")
CONFIG_FILE = os.path.join(_APP_DIR, "preview_studio_config.json")

MAX_WORKERS_HARD = 5
DEFAULT_CHAR_QUOTA = 1_000_000  # 1 triệu ký tự
UNLIMITED_CHARS = -1  # char_quota = -1 → không giới hạn
# Must match CF Worker PROXY_SEAL_KEY (or API_SECRET / default)
DEFAULT_PROXY_SEAL_KEY = "huytts2026"


def is_unlimited_quota(quota) -> bool:
    """quota <= 0 or == UNLIMITED_CHARS → unlimited."""
    try:
        q = int(quota)
    except (TypeError, ValueError):
        return False
    return q <= 0 or q == UNLIMITED_CHARS


def _hash_pw(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


def proxy_seal_key() -> str:
    """Seal key: env TTS_PROXY_SEAL_KEY → config → default (same as Worker)."""
    env = (os.environ.get("TTS_PROXY_SEAL_KEY") or os.environ.get("PROXY_SEAL_KEY") or "").strip()
    if env:
        return env
    try:
        if os.path.exists(CONFIG_FILE):
            cfg = json.loads(open(CONFIG_FILE, encoding="utf-8").read())
            k = (cfg.get("proxy_seal_key") or "").strip()
            if k:
                return k
    except Exception:
        pass
    return DEFAULT_PROXY_SEAL_KEY


def _keystream(key: bytes, nonce: bytes, n: int) -> bytes:
    out = bytearray()
    i = 0
    while len(out) < n:
        ctr = i.to_bytes(4, "big")
        out.extend(hashlib.sha256(key + nonce + ctr).digest())
        i += 1
    return bytes(out[:n])


def unseal_proxy_blob(sealed_b64: str, passphrase: Optional[str] = None) -> Optional[dict]:
    """
    Decrypt proxy payload from CF login (seal_version=1).
    Format: base64(nonce16 || tag16 || ciphertext)
    """
    raw = (sealed_b64 or "").strip()
    if not raw:
        return None
    try:
        blob = base64.b64decode(raw)
        if len(blob) < 33:
            return None
        nonce, tag, ct = blob[:16], blob[16:32], blob[32:]
        key = hashlib.sha256((passphrase or proxy_seal_key()).encode("utf-8")).digest()
        expect = hmac.new(key, nonce + ct, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(expect, tag):
            return None
        stream = _keystream(key, nonce, len(ct))
        plain = bytes(a ^ b for a, b in zip(ct, stream))
        data = json.loads(plain.decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _read(path: str, default: dict) -> dict:
    if not os.path.exists(path):
        return dict(default)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return dict(default)


def _write(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── packages (gói ký tự) ──────────────────────────────────────────────────

def list_packages() -> list[dict]:
    data = _read(PACKAGES_FILE, {"packages": []})
    return list(data.get("packages") or [])


def ensure_default_packages() -> None:
    data = _read(PACKAGES_FILE, {"packages": []})
    pkgs = data.setdefault("packages", [])
    if not pkgs:
        pkgs.extend(
            [
                {
                    "id": "pkg_1m",
                    "name": "Gói 1 triệu",
                    "chars": 1_000_000,
                    "note": "1.000.000 ký tự",
                },
                {
                    "id": "pkg_5m",
                    "name": "Gói 5 triệu",
                    "chars": 5_000_000,
                    "note": "5.000.000 ký tự",
                },
                {
                    "id": "pkg_10m",
                    "name": "Gói 10 triệu",
                    "chars": 10_000_000,
                    "note": "10.000.000 ký tự",
                },
                {
                    "id": "pkg_50m",
                    "name": "Gói 50 triệu",
                    "chars": 50_000_000,
                    "note": "50.000.000 ký tự",
                },
                {
                    "id": "pkg_unlimited",
                    "name": "Unlimited",
                    "chars": UNLIMITED_CHARS,
                    "note": "Không giới hạn ký tự",
                },
            ]
        )
        _write(PACKAGES_FILE, data)
        return
    # migrate: ensure unlimited package exists
    if not any(p.get("id") == "pkg_unlimited" for p in pkgs):
        pkgs.append(
            {
                "id": "pkg_unlimited",
                "name": "Unlimited",
                "chars": UNLIMITED_CHARS,
                "note": "Không giới hạn ký tự",
            }
        )
        _write(PACKAGES_FILE, data)


def save_package(pkg: dict) -> dict:
    data = _read(PACKAGES_FILE, {"packages": []})
    pkgs = data.setdefault("packages", [])
    pid = pkg.get("id") or secrets.token_hex(4)
    pkg["id"] = pid
    pkg["chars"] = int(pkg.get("chars") or 0)
    for i, p in enumerate(pkgs):
        if p.get("id") == pid:
            pkgs[i] = {**p, **pkg}
            _write(PACKAGES_FILE, data)
            return pkgs[i]
    pkgs.append(pkg)
    _write(PACKAGES_FILE, data)
    return pkg


def delete_package(pkg_id: str) -> bool:
    data = _read(PACKAGES_FILE, {"packages": []})
    before = len(data.get("packages") or [])
    data["packages"] = [p for p in (data.get("packages") or []) if p.get("id") != pkg_id]
    _write(PACKAGES_FILE, data)
    return len(data["packages"]) < before


# ── proxies (pool) ────────────────────────────────────────────────────────

def list_proxies() -> list[dict]:
    return list(_read(PROXIES_FILE, {"proxies": []}).get("proxies") or [])


def public_proxy(p: dict, reveal: bool = False) -> dict:
    out = {
        "id": p.get("id"),
        "label": p.get("label") or "",
        "enabled": bool(p.get("enabled", True)),
        "provider": p.get("provider") or "proxyxoay_net",
        "host": p.get("host") or "",
        "port": int(p.get("port") or 0),
        "api_key": p.get("api_key") or "",
        "username": p.get("username") or "",
        "note": p.get("note") or "",
    }
    if not reveal:
        if out["username"]:
            out["username_mask"] = out["username"][:3] + "***"
        out["password"] = ""
        if out["api_key"] and len(out["api_key"]) > 8:
            out["api_key_mask"] = out["api_key"][:8] + "…"
    else:
        out["password"] = p.get("password") or ""
    return out


def save_proxy(proxy: dict) -> dict:
    data = _read(PROXIES_FILE, {"proxies": []})
    rows = data.setdefault("proxies", [])
    pid = proxy.get("id") or "px_" + secrets.token_hex(4)
    proxy["id"] = pid
    proxy["port"] = int(proxy.get("port") or 0)
    proxy["enabled"] = bool(proxy.get("enabled", True))
    for i, p in enumerate(rows):
        if p.get("id") == pid:
            # keep password if blank on update
            if not proxy.get("password") and p.get("password"):
                proxy["password"] = p["password"]
            rows[i] = {**p, **proxy}
            _write(PROXIES_FILE, data)
            return public_proxy(rows[i], reveal=True)
    rows.append(proxy)
    _write(PROXIES_FILE, data)
    return public_proxy(proxy, reveal=True)


def delete_proxy(proxy_id: str) -> bool:
    data = _read(PROXIES_FILE, {"proxies": []})
    before = len(data.get("proxies") or [])
    data["proxies"] = [p for p in (data.get("proxies") or []) if p.get("id") != proxy_id]
    _write(PROXIES_FILE, data)
    # unlink from accounts
    acc = _load()
    for a in acc.get("accounts") or []:
        if a.get("proxy_id") == proxy_id:
            a["proxy_id"] = ""
    _save(acc)
    return len(data["proxies"]) < before


def get_proxy(proxy_id: str) -> Optional[dict]:
    for p in list_proxies():
        if p.get("id") == proxy_id:
            # list_proxies returns raw from file via list_proxies - need full
            break
    data = _read(PROXIES_FILE, {"proxies": []})
    for p in data.get("proxies") or []:
        if p.get("id") == proxy_id:
            return dict(p)
    return None


# ── accounts ──────────────────────────────────────────────────────────────

def _load() -> dict:
    return _read(ACCOUNTS_FILE, {"accounts": []})


def _save(data: dict) -> None:
    _write(ACCOUNTS_FILE, data)


def list_accounts() -> list[dict]:
    return [public_account(a) for a in (_load().get("accounts") or [])]


def public_account(a: dict) -> dict:
    quota = int(a.get("char_quota") or 0)
    used = int(a.get("chars_used") or 0)
    unlimited = is_unlimited_quota(quota)
    return {
        "id": a.get("id"),
        "username": a.get("username"),
        "role": a.get("role") or "user",
        "enabled": bool(a.get("enabled", True)),
        "note": a.get("note") or "",
        "char_quota": quota,
        "chars_used": used,
        "chars_left": -1 if unlimited else max(0, quota - used),
        "unlimited": unlimited,
        "package_id": a.get("package_id") or "",
        "package_name": a.get("package_name") or "",
        "max_workers": min(MAX_WORKERS_HARD, max(1, int(a.get("max_workers") or 1))),
        "max_chars": int(a.get("max_chars") or 0),
        "proxy_id": a.get("proxy_id") or "",
        "has_proxy": bool(
            (a.get("proxy_id") and get_proxy(a.get("proxy_id") or ""))
            or (a.get("proxy_host") and a.get("proxy_username"))
            or a.get("proxy_api_key")
            or (isinstance(a.get("proxies"), list) and len(a.get("proxies") or []) > 0)
        ),
        "proxy_host": a.get("proxy_host") or "",
        "proxy_port": int(a.get("proxy_port") or 0),
        "proxy_label": a.get("proxy_label") or "",
        "created_at": a.get("created_at"),
    }


def create_account(
    username: str,
    password: str,
    note: str = "",
    role: str = "user",
    char_quota: int = DEFAULT_CHAR_QUOTA,
    max_workers: int = 2,
    package_id: str = "",
    package_name: str = "",
    proxy_id: str = "",
    proxy: Optional[dict] = None,
) -> dict:
    username = (username or "").strip()
    if not username or not password:
        raise ValueError("username/password required")
    data = _load()
    for a in data.get("accounts") or []:
        if a.get("username") == username:
            raise ValueError("username already exists")
    salt = secrets.token_hex(8)
    mw = min(MAX_WORKERS_HARD, max(1, int(max_workers or 1)))
    row: dict[str, Any] = {
        "id": secrets.token_hex(8),
        "username": username,
        "password_salt": salt,
        "password_hash": _hash_pw(password, salt),
        "role": role if role in ("admin", "user") else "user",
        "enabled": True,
        "note": note or "",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "char_quota": int(char_quota or DEFAULT_CHAR_QUOTA),
        "chars_used": 0,
        "package_id": package_id or "",
        "package_name": package_name or "",
        "max_workers": mw,
        "proxy_id": proxy_id or "",
        "proxy_provider": "proxyxoay_net",
        "proxy_api_key": "",
        "proxy_username": "",
        "proxy_password": "",
        "proxy_host": "",
        "proxy_port": 0,
        "proxy_label": "",
    }
    if proxy:
        for k in list(row.keys()):
            if k.startswith("proxy_") and k in proxy:
                row[k] = proxy[k]
    data.setdefault("accounts", []).append(row)
    _save(data)
    return public_account(row)


# Web admin (Cloudflare D1) — accounts created on web login here
AUTH_API_BASES = [
    b.rstrip("/")
    for b in (
        os.environ.get("TTS_AUTH_API", "").strip(),
        "https://tts-origin.liveyt.pro/admin/api",
        "https://tts-admin-web.kh431248.workers.dev/api",
    )
    if b and b.strip()
]
# de-dupe preserve order
_seen: set[str] = set()
AUTH_API_BASES = [b for b in AUTH_API_BASES if not (b in _seen or _seen.add(b))]  # type: ignore[func-returns-value]


def _apply_proxy_payload(row: dict, px: dict) -> dict:
    """Merge decrypted proxy payload into account row + proxies.json."""
    if not px:
        return row
    uid = row.get("id") or "acc"
    px_id = px.get("id") or row.get("proxy_id") or f"acc_{str(uid)[:8]}"
    provider = px.get("provider") or "proxyxoay_net"
    row["proxy_id"] = px_id
    row["proxy_provider"] = provider
    row["proxy_api_key"] = px.get("api_key") or ""
    row["proxy_username"] = px.get("username") or ""
    row["proxy_password"] = px.get("password") or ""
    row["proxy_host"] = px.get("host") or ""
    row["proxy_port"] = int(px.get("port") or 0)
    row["proxy_label"] = px.get("label") or px_id
    row["shop_nhamang"] = px.get("shop_nhamang") or "random"
    row["shop_tinhthanh"] = px.get("shop_tinhthanh", 0)
    row["shop_whitelist"] = px.get("shop_whitelist") or ""
    row["shop_method"] = px.get("shop_method") or "GET"

    pdata = _read(PROXIES_FILE, {"proxies": []})
    proxies = pdata.setdefault("proxies", [])
    note = f"from account {row.get('username')}"
    if "shop" in str(provider).lower():
        note = (
            f"SHOP|nhamang={row['shop_nhamang']}|"
            f"tinhthanh={row['shop_tinhthanh']}|"
            f"whitelist={row['shop_whitelist']}|"
            f"method={row['shop_method']}"
        )
    entry = {
        "id": px_id,
        "label": row["proxy_label"],
        "enabled": True,
        "provider": provider,
        "host": row["proxy_host"],
        "port": row["proxy_port"],
        "username": row["proxy_username"],
        "password": row["proxy_password"],
        "api_key": row["proxy_api_key"],
        "shop_nhamang": row["shop_nhamang"],
        "shop_tinhthanh": row["shop_tinhthanh"],
        "shop_whitelist": row["shop_whitelist"],
        "shop_method": row["shop_method"],
        "note": note,
    }
    hit = False
    for j, p in enumerate(proxies):
        if p.get("id") == px_id:
            proxies[j] = {**p, **entry}
            hit = True
            break
    if not hit:
        proxies.append(entry)
    # Prefer assigned account proxy: enable it, keep others as configured
    _write(PROXIES_FILE, pdata)
    return row


def _upsert_remote_account(account: dict, password: str) -> dict:
    """Cache D1 account into local accounts.json (incl. hash for offline)."""
    data = _load()
    accounts = data.setdefault("accounts", [])
    uid = account.get("id") or secrets.token_hex(8)
    salt = account.get("password_salt") or secrets.token_hex(8)
    phash = account.get("password_hash") or _hash_pw(password, salt)

    # Decrypt sealed proxies list from web login (many-to-many)
    sealed_proxies = account.get("proxies_sealed") or ""
    proxies_list = []
    if sealed_proxies:
        payload = unseal_proxy_blob(str(sealed_proxies))
        if payload and isinstance(payload.get("proxies"), list):
            proxies_list = payload["proxies"]
    
    # Legacy: single proxy_sealed (backward compat)
    if not proxies_list:
        sealed = account.get("proxy_sealed") or ""
        px_payload = unseal_proxy_blob(str(sealed)) if sealed else None
        if px_payload and isinstance(px_payload, dict):
            proxies_list = [px_payload]

    row: dict[str, Any] = {
        "id": uid,
        "username": account.get("username"),
        "password_salt": salt,
        "password_hash": phash,
        "role": account.get("role") or "user",
        "enabled": bool(account.get("enabled", True)),
        "note": account.get("note") or "",
        "created_at": account.get("created_at")
        or time.strftime("%Y-%m-%dT%H:%M:%S"),
        "char_quota": int(account.get("char_quota") or DEFAULT_CHAR_QUOTA),
        "chars_used": int(account.get("chars_used") or 0),
        "package_id": account.get("package_id") or "",
        "package_name": account.get("package_name") or "",
        "max_workers": min(
            MAX_WORKERS_HARD, max(1, int(account.get("max_workers") or 1))
        ),
        # 0 = use studio default (300); >0 = per-user chunk limit from CF admin
        "max_chars": int(account.get("max_chars") or 0),
        "proxies": proxies_list,  # Store full proxies list
        "has_proxy": len(proxies_list) > 0,
        "source": "cloudflare-d1",
        "presence_token": account.get("presence_token") or "",
    }

    found = False
    for i, a in enumerate(accounts):
        if a.get("username") == row["username"] or a.get("id") == uid:
            # keep local chars_used if higher? prefer remote
            # keep previous presence_token if server omitted it
            if not row.get("presence_token") and a.get("presence_token"):
                row["presence_token"] = a.get("presence_token")
            accounts[i] = {**a, **row}
            row = accounts[i]
            found = True
            break
    if not found:
        accounts.append(row)
    _save(data)
    return dict(row)


def authenticate_remote(username: str, password: str) -> Optional[dict]:
    """Login against Cloudflare D1 (web-created accounts)."""
    import urllib.error
    import urllib.request

    payload = json.dumps(
        {"username": username, "password": password}
    ).encode("utf-8")
    # CF Bot Fight blocks default Python-urllib UA (error 1010) — spoof curl
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "curl/8.7.1",
    }
    last_net_err: Optional[Exception] = None
    wrong_password = False

    for base in AUTH_API_BASES:
        url = f"{base}/user/login"
        req = urllib.request.Request(
            url, data=payload, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8")
                detail = (
                    json.loads(body).get("detail")
                    if body and body.strip().startswith("{")
                    else body
                )
            except Exception:
                detail = None
            # wrong credentials
            if e.code == 401 and detail and "wrong" in str(detail).lower():
                wrong_password = True
                continue
            if e.code in (401, 403):
                # may be wrong route / bot — try next base
                last_net_err = RuntimeError(detail or f"HTTP {e.code}")
                continue
            last_net_err = RuntimeError(detail or f"auth server HTTP {e.code}")
            continue
        except Exception as e:
            last_net_err = e
            continue

        if data.get("ok") and data.get("account"):
            acc = data["account"]
            # top-level or nested presence token for gen-online heartbeats
            pt = data.get("presence_token") or acc.get("presence_token") or ""
            if pt:
                acc = {**acc, "presence_token": pt}
            return _upsert_remote_account(acc, password)

    if wrong_password:
        return None
    if last_net_err is not None:
        raise RuntimeError(
            f"không kết nối được server account: {last_net_err}"
        ) from last_net_err
    return None


def authenticate(username: str, password: str) -> Optional[dict]:
    """Try Cloudflare D1 first (web accounts), then local accounts.json."""
    username = (username or "").strip()
    if not username or not password:
        return None

    # 1) Remote D1 (accounts created on web admin)
    remote_err: Optional[Exception] = None
    try:
        remote = authenticate_remote(username, password)
        if remote:
            return remote
    except Exception as e:
        remote_err = e

    # 2) Local JSON fallback (admin / offline cache)
    for a in _load().get("accounts") or []:
        if a.get("username") != username:
            continue
        if not a.get("enabled", True):
            return None
        salt = a.get("password_salt") or ""
        if a.get("password_hash") == _hash_pw(password, salt):
            return dict(a)
        return None

    # wrong password remote+local — if network failed, surface that
    if remote_err is not None:
        raise remote_err
    return None


def update_account(account_id: str, **fields: Any) -> Optional[dict]:
    data = _load()
    for a in data.get("accounts") or []:
        if a.get("id") != account_id:
            continue
        allowed = {
            "note",
            "role",
            "enabled",
            "char_quota",
            "chars_used",
            "package_id",
            "package_name",
            "max_workers",
            "proxy_id",
            "proxy_provider",
            "proxy_api_key",
            "proxy_username",
            "proxy_password",
            "proxy_host",
            "proxy_port",
            "proxy_label",
        }
        for k, v in fields.items():
            if k not in allowed or v is None:
                continue
            if k == "max_workers":
                a[k] = min(MAX_WORKERS_HARD, max(1, int(v)))
            elif k in ("char_quota", "chars_used", "proxy_port"):
                a[k] = int(v)
            elif k == "enabled":
                a[k] = bool(v)
            else:
                a[k] = v
        if fields.get("password"):
            salt = secrets.token_hex(8)
            a["password_salt"] = salt
            a["password_hash"] = _hash_pw(str(fields["password"]), salt)
        # apply package template
        if fields.get("package_id"):
            for pkg in list_packages():
                if pkg.get("id") == fields["package_id"]:
                    a["package_id"] = pkg["id"]
                    a["package_name"] = pkg.get("name") or ""
                    a["char_quota"] = int(pkg.get("chars") or a.get("char_quota") or 0)
                    break
        _save(data)
        return public_account(a)
    return None


def delete_account(account_id: str) -> bool:
    data = _load()
    before = len(data.get("accounts") or [])
    data["accounts"] = [
        a for a in (data.get("accounts") or []) if a.get("id") != account_id
    ]
    _save(data)
    return len(data["accounts"]) < before


def get_account(account_id: str) -> Optional[dict]:
    for a in _load().get("accounts") or []:
        if a.get("id") == account_id:
            return dict(a)
    return None


def consume_chars(account_id: str, n: int) -> tuple[bool, str]:
    """Deduct characters after successful TTS. Returns (ok, message)."""
    data = _load()
    for a in data.get("accounts") or []:
        if a.get("id") != account_id:
            continue
        quota = int(a.get("char_quota") or 0)
        used = int(a.get("chars_used") or 0)
        # unlimited: vẫn cộng used để thống kê, không chặn
        if not is_unlimited_quota(quota) and used + n > quota:
            return False, f"Hết gói ký tự (đã dùng {used}/{quota})"
        a["chars_used"] = used + n
        _save(data)
        return True, "ok"
    return False, "Không tìm thấy tài khoản"


# ── Gen presence (online = đang gen TTS) → CF admin ─────────────────────────

def _presence_token_for(account: Optional[dict]) -> str:
    if not account:
        return ""
    tok = (account.get("presence_token") or "").strip()
    if tok:
        return tok
    uid = account.get("id") or ""
    if not uid:
        return ""
    full = get_account(str(uid))
    return ((full or {}).get("presence_token") or "").strip()


def report_gen_presence(
    account: Optional[dict],
    action: str = "heartbeat",
    *,
    kind: str = "preview",
    workers: int = 0,
    ok: int = 0,
    fail: int = 0,
    total: int = 0,
    label: str = "",
    session_id: str = "",
    blocking: bool = False,
) -> bool:
    """
    Báo admin: user đang gen / heartbeat / stop.
    Fire-and-forget by default (không chặn pipeline TTS).
    """
    token = _presence_token_for(account)
    if not token:
        return False

    payload = {
        "token": token,
        "action": str(action or "heartbeat"),
        "kind": str(kind or "preview")[:32],
        "workers": max(0, int(workers or 0)),
        "ok": max(0, int(ok or 0)),
        "fail": max(0, int(fail or 0)),
        "total": max(0, int(total or 0)),
        "label": str(label or "")[:120],
        "session_id": str(session_id or "")[:64],
        "client": "preview_studio",
    }

    def _send() -> bool:
        import urllib.request

        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "curl/8.7.1",
            "X-Presence-Token": token,
        }
        for base in AUTH_API_BASES:
            url = f"{base}/user/presence"
            req = urllib.request.Request(
                url, data=body, headers=headers, method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read().decode("utf-8") or "{}")
                return bool(data.get("ok"))
            except Exception:
                continue
        return False

    if blocking:
        try:
            return _send()
        except Exception:
            return False

    try:
        import threading

        threading.Thread(target=_send, name="gen-presence", daemon=True).start()
        return True
    except Exception:
        return False


def report_gen_start(
    account: Optional[dict],
    *,
    kind: str = "preview",
    workers: int = 1,
    total: int = 0,
    label: str = "",
    session_id: str = "",
) -> bool:
    return report_gen_presence(
        account,
        "start",
        kind=kind,
        workers=workers,
        ok=0,
        fail=0,
        total=total,
        label=label,
        session_id=session_id,
    )


def report_gen_heartbeat(
    account: Optional[dict],
    *,
    kind: str = "preview",
    workers: int = 0,
    ok: int = 0,
    fail: int = 0,
    total: int = 0,
    label: str = "",
    session_id: str = "",
) -> bool:
    return report_gen_presence(
        account,
        "heartbeat",
        kind=kind,
        workers=workers,
        ok=ok,
        fail=fail,
        total=total,
        label=label,
        session_id=session_id,
    )


def report_gen_stop(
    account: Optional[dict],
    *,
    kind: str = "preview",
    ok: int = 0,
    fail: int = 0,
    total: int = 0,
    session_id: str = "",
) -> bool:
    return report_gen_presence(
        account,
        "stop",
        kind=kind,
        ok=ok,
        fail=fail,
        total=total,
        session_id=session_id,
    )


def check_chars(account: dict, n: int) -> tuple[bool, str]:
    quota = int(account.get("char_quota") or 0)
    used = int(account.get("chars_used") or 0)
    if is_unlimited_quota(quota) or account.get("unlimited"):
        return True, "ok"
    if used + n > quota:
        return False, (
            f"Hết gói ký tự (còn {max(0, quota - used):,}, cần {n:,} ký tự)"
        )
    return True, "ok"


def ensure_default_account() -> None:
    ensure_default_packages()
    data = _load()
    if data.get("accounts"):
        # migrate missing fields
        changed = False
        for a in data["accounts"]:
            if "char_quota" not in a:
                a["char_quota"] = DEFAULT_CHAR_QUOTA
                a["chars_used"] = int(a.get("chars_used") or 0)
                a["max_workers"] = min(
                    MAX_WORKERS_HARD, max(1, int(a.get("max_workers") or 2))
                )
                a["role"] = a.get("role") or (
                    "admin" if a.get("username") == "admin" else "user"
                )
                a["enabled"] = a.get("enabled", True)
                changed = True
        if changed:
            _save(data)
        return
    create_account(
        "admin",
        "admin123",
        note="admin local — đổi mật khẩu",
        role="admin",
        char_quota=50_000_000,
        max_workers=5,
        package_name="Gói 50 triệu",
        package_id="pkg_50m",
    )


def resolve_proxy_for_account(account: dict) -> Optional[str]:
    """Build proxy URL from account.
    
    Priority:
    1. account["proxies"] list (many-to-many from Cloudflare)
    2. Legacy fields (proxy_id, proxy_host...)
    """
    # 1) NEW: Read from proxies list (many-to-many)
    proxies_list = account.get("proxies") or []
    if proxies_list:
        # Build URL from first enabled proxy
        for p in proxies_list:
            if not p.get("enabled", True):
                continue
            provider = (p.get("provider") or "").strip()
            api_key = (p.get("api_key") or "").strip()
            host = (p.get("host") or "").strip()
            port = int(p.get("port") or 0)
            user = (p.get("username") or "").strip()
            pw = (p.get("password") or "").strip()
            
            # For shop provider, return placeholder (will resolve dynamically)
            if provider == "proxyxoay_shop" and api_key:
                return f"shop://{api_key}"
            
            # For net/static provider, build URL
            if host and port:
                if user and pw:
                    return f"http://{user}:{pw}@{host}:{port}"
                return f"http://{host}:{port}"
    
    # 2) LEGACY: Read from proxy_id link
    pid = (account.get("proxy_id") or "").strip()
    if pid:
        p = get_proxy(pid)
        if p and p.get("enabled", True):
            host = (p.get("host") or "").strip()
            port = int(p.get("port") or 0)
            user = (p.get("username") or "").strip()
            pw = (p.get("password") or "").strip()
            if host and port:
                if user and pw:
                    return f"http://{user}:{pw}@{host}:{port}"
                return f"http://{host}:{port}"
    
    # 3) LEGACY: Read from inline fields
    host = (account.get("proxy_host") or "").strip()
    port = int(account.get("proxy_port") or 0)
    user = (account.get("proxy_username") or "").strip()
    pw = (account.get("proxy_password") or "").strip()
    if not host or not port:
        return None
    if user and pw:
        return f"http://{user}:{pw}@{host}:{port}"
    return f"http://{host}:{port}"


# backward-compat alias
def build_proxy_url(account: dict) -> Optional[str]:
    return resolve_proxy_for_account(account)


def list_proxy_lines_for_gen(
    account: dict | None = None,
    max_lanes: int = 5,
) -> list[dict]:
    """
    Build proxy lines for multi-lane gen.
    1) Account-bound proxy first (from login decrypt)
    2) Other enabled proxies in pool (scale multi-key)
    """
    max_n = max(1, min(5, int(max_lanes or 1)))
    lines: list[dict] = []
    seen: set[str] = set()

    def _add(p: dict) -> None:
        if not p or not p.get("enabled", True):
            return
        pid = str(p.get("id") or "")
        key = (p.get("api_key") or "").strip()
        host = (p.get("host") or "").strip()
        prov = (p.get("provider") or "proxyxoay_net").lower()
        dedupe = pid or key or f"{host}:{p.get('port')}"
        if not dedupe or dedupe in seen:
            return
        # shop / net with key OK; static needs host:port
        if "shop" in prov:
            if not key:
                return
        elif not key and not (host and int(p.get("port") or 0)):
            return
        seen.add(dedupe)
        lines.append(
            {
                "id": pid or f"px_{len(lines)+1}",
                "label": p.get("label") or pid or host or "proxy",
                "enabled": True,
                "provider": p.get("provider") or "proxyxoay_net",
                "host": host,
                "port": int(p.get("port") or 0),
                "username": p.get("username") or "",
                "password": p.get("password") or "",
                "api_key": key,
                "shop_nhamang": p.get("shop_nhamang") or p.get("nhamang") or "random",
                "shop_tinhthanh": p.get("shop_tinhthanh")
                if p.get("shop_tinhthanh") is not None
                else p.get("tinhthanh", 0),
                "shop_whitelist": p.get("shop_whitelist") or p.get("whitelist") or "",
                "shop_method": p.get("shop_method") or "GET",
                "note": p.get("note") or "",
            }
        )

    # 0) NEW: account["proxies"] list from Cloudflare sync (many-to-many)
    if account:
        account_proxies = account.get("proxies") or []
        for p in account_proxies:
            if len(lines) >= max_n:
                break
            _add(p)
    
    # 1) LEGACY: account-bound single proxy (backward compat)
    if account and len(lines) < max_n:
        pid = account.get("proxy_id") or ""
        if pid:
            px = get_proxy(pid)
            if px:
                _add(px)
        if not lines and (
            account.get("proxy_api_key")
            or (account.get("proxy_host") and account.get("proxy_port"))
        ):
            _add(
                {
                    "id": account.get("proxy_id") or "acc_proxy",
                    "label": account.get("proxy_label") or "account",
                    "enabled": True,
                    "provider": account.get("proxy_provider") or "proxyxoay_net",
                    "host": account.get("proxy_host") or "",
                    "port": int(account.get("proxy_port") or 0),
                    "username": account.get("proxy_username") or "",
                    "password": account.get("proxy_password") or "",
                    "api_key": account.get("proxy_api_key") or "",
                    "shop_nhamang": account.get("shop_nhamang") or "random",
                    "shop_tinhthanh": account.get("shop_tinhthanh", 0),
                    "shop_whitelist": account.get("shop_whitelist") or "",
                    "shop_method": account.get("shop_method") or "GET",
                }
            )

    # 2) FALLBACK: local proxies.json pool (if account has no proxies)
    if not lines:
        data = _read(PROXIES_FILE, {"proxies": []})
        for p in data.get("proxies") or []:
            if len(lines) >= max_n:
                break
            _add(p)

    return lines[:max_n]
