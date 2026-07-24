# -*- coding: utf-8 -*-
"""User-facing safety: product naming, quiet tech logs, sanitized errors.

Ship builds (Nuitka frozen) hide stack traces, provider names, HSW/token
pipeline chatter. Dev source runs stay verbose unless STUDIO_QUIET=1.
"""
from __future__ import annotations

import base64
import os
import re
from typing import Optional


# Public product strings (no third-party brand)
PRODUCT_NAME = "TTS Studio"
PRODUCT_TITLE = "TTS Studio"
PRODUCT_LOGIN_TITLE = "TTS Studio — Đăng nhập"
PRODUCT_FOOTER = "© 2026 TTS Studio · tài khoản do quản trị viên cấp"
PRODUCT_TAGLINE = "Đăng nhập để tiếp tục"


def _env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def is_frozen_app() -> bool:
    try:
        import sys

        if getattr(sys, "frozen", False):
            return True
    except Exception:
        pass
    try:
        return bool(__compiled__)  # type: ignore[name-defined]
    except NameError:
        return False


def quiet_tech_logs() -> bool:
    """True → no pipeline dump to console / ship log files."""
    if _env_truthy("STUDIO_VERBOSE"):
        return False
    if _env_truthy("STUDIO_QUIET"):
        return True
    return is_frozen_app()


def b64d(s: str) -> str:
    """Decode base64 ASCII string (hide plain URLs/keys in source scan)."""
    try:
        return base64.b64decode(s.encode("ascii")).decode("utf-8")
    except Exception:
        return ""


# ── Error / status sanitization ───────────────────────────────────────────

_TECH_RES = [
    re.compile(r"https?://[^\s\"'<>]+", re.I),
    re.compile(r"\b(?:api\.)?elevenlabs\.io\b", re.I),
    re.compile(r"\belevenlabs\b", re.I),
    re.compile(r"\bproxyxoay(?:\.shop|\.net)?\b", re.I),
    re.compile(r"\bcamoufox\b", re.I),
    re.compile(r"\bhcaptcha\b", re.I),
    re.compile(r"\bHSW\b", re.I),
    re.compile(r"\bhsw\b", re.I),
    re.compile(r"\bgetcaptcha\b", re.I),
    re.compile(r"\bchecksiteconfig\b", re.I),
    re.compile(r"\banonymous\b", re.I),
    re.compile(r"\blanding\s*page\b", re.I),
    re.compile(r"\bTTS_LANDING[_\w]*\b", re.I),
    re.compile(r"\bsign_in_required\b", re.I),
    re.compile(r"\bauthentication_error\b", re.I),
    re.compile(r"\bimage_challenge\b", re.I),
    re.compile(r"\bx-region\b", re.I),
    re.compile(r"\basia-southeast\d*\b", re.I),
    re.compile(r"\bworkers\.dev\b", re.I),
    re.compile(r"\bliveyt\.pro\b", re.I),
    re.compile(r"\bgithub\.com/[^\s]+", re.I),
    re.compile(r"\bapi\.github\.com\b", re.I),
    re.compile(r"traceback \(most recent", re.I),
    re.compile(r'File "[^"]+", line \d+', re.I),
    re.compile(r"\bP1_eyJ[A-Za-z0-9_\-.]+\b"),
    re.compile(r"\btoken\b.{0,20}\b(prefix|OK|len)\b", re.I),
    re.compile(r"http://[^\s]+"),
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}:\d+\b"),  # host:port IP
]

_MAP_PHRASES = [
    (
        re.compile(
            r"net_limit|landing|sign_in_required|tts_landing|free landing", re.I
        ),
        "Hết lượt tạo trên đường truyền hiện tại. Đang chuyển kết nối…",
    ),
    (
        re.compile(r"net_challenge|image_challenge|visual captcha", re.I),
        "Đường truyền cần xác minh thêm. Đang thử lại…",
    ),
    (
        re.compile(r"net_captcha|getcaptcha|checksiteconfig|\bhsw\b", re.I),
        "Xác minh kết nối thất bại. Đang thử lại…",
    ),
    (
        re.compile(r"net_auth|\b401\b|unusual activity|authentication", re.I),
        "Kết nối bị từ chối. Đang thử lại với đường truyền khác…",
    ),
    (
        re.compile(r"net_throttle|\b429\b|rate.?limit|too many", re.I),
        "Quá nhiều yêu cầu. Đang chờ rồi thử lại…",
    ),
    (
        re.compile(r"net_runtime|camoufox|playwright", re.I),
        "Thiếu thành phần runtime. Cài lại bản portable đầy đủ.",
    ),
    (
        re.compile(
            r"net_http|net_proxy|net_pause|net_stale|proxy|timeout|timed out|connection|reset by peer|errno",
            re.I,
        ),
        "Lỗi kết nối mạng. Đang thử lại…",
    ),
    (
        re.compile(r"ffmpeg|ffprobe", re.I),
        "Thiếu công cụ ghép audio. Liên hệ quản trị viên.",
    ),
    (
        re.compile(r"quota|hết gói|char", re.I),
        "Hết hạn mức ký tự. Liên hệ quản trị viên.",
    ),
]


def _strip_tech(text: str) -> str:
    s = text or ""
    for rx in _TECH_RES:
        s = rx.sub("…", s)
    # collapse noise
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def sanitize_user_error(err: object, *, fallback: str = "Đã xảy ra lỗi. Vui lòng thử lại.") -> str:
    """Map raw exception/log → short Vietnamese message for MessageBox/status."""
    if err is None:
        return fallback
    raw = str(err).strip()
    if not raw:
        return fallback
    # Prefer first mapped phrase over raw dump
    for rx, msg in _MAP_PHRASES:
        if rx.search(raw):
            return msg
    cleaned = _strip_tech(raw)
    # Drop stack-like leftovers
    if "Traceback" in cleaned or "File \"" in cleaned or len(cleaned) > 220:
        return fallback
    if not cleaned or cleaned in (".", "…", "… …"):
        return fallback
    # If still looks like code/JSON technical dump
    if re.search(r"[{}\[\]\\\\]|Exception|Error:|RuntimeError|TypeError", cleaned):
        if re.search(r"(Lỗi|Thiếu|Hết|Không|Vui lòng)", cleaned):
            return cleaned[:180]
        return fallback
    return cleaned[:180]


def sanitize_status(status: str) -> str:
    """Row/status line shown in UI during gen."""
    s = (status or "").strip()
    if not s:
        return s
    low = s.lower()
    if "proxy" in low or "đổi ip" in low or "xoay" in low:
        return "Đang chờ kết nối…"
    if "landing" in low or "401" in low or "token" in low or "hsw" in low:
        return "Đang thử lại…"
    if "chờ thử" in low:
        return "Chờ thử lại…"
    return _strip_tech(s)[:80] or "Đang xử lý…"


def sanitize_log_line(msg: str) -> str:
    """Optional UI log line (progress bar text)."""
    s = (msg or "").strip()
    if not s:
        return s
    if quiet_tech_logs():
        # Keep simple progress emoji lines without host/token
        if s.startswith(("▶", "✅", "❌", "⚠", "📦", "Hoàn", "Đang", "Sẵn")):
            return sanitize_user_error(s, fallback="Đang xử lý…") if "❌" in s else _strip_tech(s)[:120]
        if any(k in s.lower() for k in ("hsw", "token", "proxy", "landing", "farm", "http")):
            return ""
    return _strip_tech(s)[:160]


def tech_log(msg: str) -> None:
    """Internal debug print — silent in ship builds."""
    if quiet_tech_logs():
        return
    try:
        print(msg, flush=True)
    except Exception:
        pass


def write_diag(msg: str, path: Optional[str] = None) -> None:
    """Minimal ship-safe boot line (no paths to browser/tokens)."""
    if quiet_tech_logs():
        # Only high-level lifecycle, no absolute paths of third-party tools
        safe = _strip_tech(str(msg)[:200])
        if any(k in safe.lower() for k in ("camoufox", "hsw", "token", "proxyxoay", "eleven")):
            safe = "runtime step"
        msg = safe
    if path:
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(msg.rstrip() + "\n")
        except Exception:
            pass
    elif not quiet_tech_logs():
        tech_log(msg)
