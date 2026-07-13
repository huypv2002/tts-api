# -*- coding: utf-8 -*-
"""Local accounts DB (JSON) — tool only, no tts-api server."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from typing import Any, Optional

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.path.join(_APP_DIR, "accounts.json")


def _hash_pw(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


def _load() -> dict:
    if not os.path.exists(ACCOUNTS_FILE):
        return {"accounts": []}
    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"accounts": []}


def _save(data: dict) -> None:
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def list_accounts() -> list[dict]:
    rows = []
    for a in _load().get("accounts") or []:
        rows.append(public_account(a))
    return rows


def public_account(a: dict) -> dict:
    return {
        "id": a.get("id"),
        "username": a.get("username"),
        "note": a.get("note") or "",
        "has_proxy": bool(a.get("proxy_host") and a.get("proxy_username")),
        "proxy_host": a.get("proxy_host") or "",
        "proxy_port": a.get("proxy_port") or 0,
        "proxy_label": a.get("proxy_label") or "",
        "proxy_username": (a.get("proxy_username") or "")[:3] + "***"
        if a.get("proxy_username")
        else "",
        "created_at": a.get("created_at"),
    }


def create_account(
    username: str,
    password: str,
    note: str = "",
    proxy: Optional[dict] = None,
) -> dict:
    username = (username or "").strip()
    if not username or not password:
        raise ValueError("username/password required")
    data = _load()
    for a in data["accounts"]:
        if a.get("username") == username:
            raise ValueError("username already exists")
    salt = secrets.token_hex(8)
    row: dict[str, Any] = {
        "id": secrets.token_hex(8),
        "username": username,
        "password_salt": salt,
        "password_hash": _hash_pw(password, salt),
        "note": note or "",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "proxy_provider": "proxyxoay_net",
        "proxy_api_key": "",
        "proxy_username": "",
        "proxy_password": "",
        "proxy_host": "",
        "proxy_port": 0,
        "proxy_label": "",
    }
    if proxy:
        row.update({k: proxy.get(k, row.get(k)) for k in row if k.startswith("proxy_")})
    data["accounts"].append(row)
    _save(data)
    return public_account(row)


def authenticate(username: str, password: str) -> Optional[dict]:
    username = (username or "").strip()
    for a in _load().get("accounts") or []:
        if a.get("username") != username:
            continue
        salt = a.get("password_salt") or ""
        if a.get("password_hash") == _hash_pw(password, salt):
            return dict(a)  # full row for session (includes proxy secrets)
        return None
    return None


def update_account(account_id: str, **fields: Any) -> Optional[dict]:
    data = _load()
    for a in data["accounts"]:
        if a.get("id") != account_id:
            continue
        allowed = {
            "note",
            "proxy_provider",
            "proxy_api_key",
            "proxy_username",
            "proxy_password",
            "proxy_host",
            "proxy_port",
            "proxy_label",
        }
        for k, v in fields.items():
            if k in allowed and v is not None:
                a[k] = v
        if fields.get("password"):
            salt = secrets.token_hex(8)
            a["password_salt"] = salt
            a["password_hash"] = _hash_pw(str(fields["password"]), salt)
        _save(data)
        return public_account(a)
    return None


def get_account(account_id: str) -> Optional[dict]:
    for a in _load().get("accounts") or []:
        if a.get("id") == account_id:
            return dict(a)
    return None


def ensure_default_account() -> None:
    """First run: create demo account if empty."""
    data = _load()
    if data.get("accounts"):
        return
    create_account("admin", "admin123", note="local default — đổi mật khẩu")


def build_proxy_url(account: dict) -> Optional[str]:
    host = (account.get("proxy_host") or "").strip()
    port = int(account.get("proxy_port") or 0)
    user = (account.get("proxy_username") or "").strip()
    pw = (account.get("proxy_password") or "").strip()
    if not host or not port:
        return None
    if user and pw:
        return f"http://{user}:{pw}@{host}:{port}"
    return f"http://{host}:{port}"
