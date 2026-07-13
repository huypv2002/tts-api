# -*- coding: utf-8 -*-
"""Local accounts + packages DB (JSON) — tool only."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from typing import Any, Optional

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.path.join(_APP_DIR, "accounts.json")
PROXIES_FILE = os.path.join(_APP_DIR, "proxies.json")
PACKAGES_FILE = os.path.join(_APP_DIR, "packages.json")

MAX_WORKERS_HARD = 5
DEFAULT_CHAR_QUOTA = 1_000_000  # 1 triệu ký tự


def _hash_pw(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


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
    if data.get("packages"):
        return
    data["packages"] = [
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
    ]
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
    return {
        "id": a.get("id"),
        "username": a.get("username"),
        "role": a.get("role") or "user",
        "enabled": bool(a.get("enabled", True)),
        "note": a.get("note") or "",
        "char_quota": quota,
        "chars_used": used,
        "chars_left": max(0, quota - used),
        "package_id": a.get("package_id") or "",
        "package_name": a.get("package_name") or "",
        "max_workers": min(MAX_WORKERS_HARD, max(1, int(a.get("max_workers") or 1))),
        "proxy_id": a.get("proxy_id") or "",
        "has_proxy": bool(
            (a.get("proxy_id") and get_proxy(a.get("proxy_id") or ""))
            or (a.get("proxy_host") and a.get("proxy_username"))
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


def authenticate(username: str, password: str) -> Optional[dict]:
    username = (username or "").strip()
    for a in _load().get("accounts") or []:
        if a.get("username") != username:
            continue
        if not a.get("enabled", True):
            return None
        salt = a.get("password_salt") or ""
        if a.get("password_hash") == _hash_pw(password, salt):
            return dict(a)
        return None
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
        if used + n > quota:
            return False, f"hết gói ký tự ({used}/{quota})"
        a["chars_used"] = used + n
        _save(data)
        return True, "ok"
    return False, "account not found"


def check_chars(account: dict, n: int) -> tuple[bool, str]:
    quota = int(account.get("char_quota") or 0)
    used = int(account.get("chars_used") or 0)
    if used + n > quota:
        return False, f"hết gói ký tự (còn {max(0, quota-used)}, cần {n})"
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
    """Prefer linked proxy_id from pool, else inline fields on account."""
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
