from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT.parent  # tts-preview (fast_tts.py lives here)
DATA = ROOT / "data"
DB_PATH = DATA / "db" / "tts.db"
AUDIO_DIR = DATA / "audio"
SETTINGS_PATH = ROOT / "config" / "settings.json"
SETTINGS_EXAMPLE = ROOT / "config" / "settings.example.json"
PROXIES_PATH = ROOT / "config" / "proxies.json"
PROXIES_EXAMPLE = ROOT / "config" / "proxies.example.json"
LEGACY_PROXYXOAY = PARENT / ".proxyxoay.json"

load_dotenv(ROOT / ".env")


def _ensure_dirs() -> None:
    (DATA / "db").mkdir(parents=True, exist_ok=True)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    (ROOT / "config").mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, default: dict) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_settings() -> dict:
    _ensure_dirs()
    base = _read_json(SETTINGS_EXAMPLE, {})
    cur = _read_json(SETTINGS_PATH, {})
    merged = {**base, **cur}
    # env overrides
    if os.environ.get("TTS_ADMIN_PASSWORD"):
        merged["admin_password"] = os.environ["TTS_ADMIN_PASSWORD"]
    if os.environ.get("TTS_ADMIN_SECRET"):
        merged["admin_session_secret"] = os.environ["TTS_ADMIN_SECRET"]
    if os.environ.get("TTS_HOST"):
        merged["host"] = os.environ["TTS_HOST"]
    if os.environ.get("TTS_PORT"):
        merged["port"] = int(os.environ["TTS_PORT"])
    if os.environ.get("TTS_PUBLIC_BASE_URL"):
        merged["public_base_url"] = os.environ["TTS_PUBLIC_BASE_URL"].rstrip("/")
    # safe defaults
    merged.setdefault("admin_password", "admin123")
    merged.setdefault("admin_session_secret", secrets.token_hex(24))
    merged.setdefault("default_max_chars", 950)
    merged.setdefault("hard_max_chars", 1000)
    merged.setdefault("default_quota_chars_day", 50000)
    merged.setdefault("default_quota_jobs_day", 200)
    merged.setdefault("default_max_concurrent", 2)
    merged.setdefault("inflight_per_proxy", 3)
    merged.setdefault("worker_count", 6)
    merged.setdefault("host", "0.0.0.0")
    merged.setdefault("port", 8787)
    merged.setdefault("public_base_url", "")
    merged.setdefault("cors_origins", ["*"])
    merged.setdefault("default_voice", "NOpBlnGInO9m6vDvFkFC")
    merged.setdefault("default_model", "eleven_v3")
    merged.setdefault("default_lang", "en")
    if not SETTINGS_PATH.exists():
        _write_json(SETTINGS_PATH, {k: v for k, v in merged.items() if k not in ()})
    return merged


def save_settings(patch: dict) -> dict:
    cur = load_settings()
    cur.update(patch)
    # don't dump secrets empty
    _write_json(
        SETTINGS_PATH,
        {k: v for k, v in cur.items()},
    )
    return cur


def load_proxies_file() -> list[dict]:
    _ensure_dirs()
    if not PROXIES_PATH.exists():
        # bootstrap from example + legacy .proxyxoay.json
        data = _read_json(PROXIES_EXAMPLE, {"proxies": []})
        proxies = list(data.get("proxies") or [])
        if LEGACY_PROXYXOAY.exists():
            try:
                legacy = json.loads(LEGACY_PROXYXOAY.read_text(encoding="utf-8"))
                proxies = [
                    {
                        "id": "px1",
                        "label": legacy.get("note") or "proxyxoay primary",
                        "enabled": True,
                        "provider": "proxyxoay_net",
                        "api_key": legacy.get("api_key") or "",
                        "username": legacy.get("username") or "",
                        "password": legacy.get("password") or "",
                        "host": legacy.get("host") or "",
                        "port": int(legacy.get("http_port") or legacy.get("port") or 8570),
                    }
                ]
            except Exception:
                pass
        _write_json(PROXIES_PATH, {"proxies": proxies})
        return proxies
    data = _read_json(PROXIES_PATH, {"proxies": []})
    return list(data.get("proxies") or [])


def save_proxies_file(proxies: list[dict]) -> None:
    _write_json(PROXIES_PATH, {"proxies": proxies})
