# -*- coding: utf-8 -*-
"""Paths for source run vs Nuitka frozen (standalone / onefile).

Portable layout (user giải nén folder + double-click EXE):

  TTS-Studio-Nuitka/
    TTS Studio.exe          (+ DLL/Qt nếu standalone)
    bin/ffmpeg.exe
    bin/ffprobe.exe
    runtime/                (browser runtime — neutral name)
    silent_*.mp3
    studio_boot.log         (minimal lifecycle only)
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional


def is_frozen() -> bool:
    if getattr(sys, "frozen", False):
        return True
    # Nuitka marks compiled modules
    try:
        return bool(__compiled__)  # type: ignore[name-defined]
    except NameError:
        pass
    # Heuristic: running from .exe on Windows
    try:
        exe = os.path.abspath(sys.executable or "")
        if exe.lower().endswith(".exe") and "python" not in os.path.basename(exe).lower():
            return True
    except Exception:
        pass
    return False


def exe_file() -> str:
    """Absolute path to the running executable (or script)."""
    candidates = []
    try:
        if sys.argv and sys.argv[0]:
            candidates.append(os.path.abspath(sys.argv[0]))
    except Exception:
        pass
    try:
        if sys.executable:
            candidates.append(os.path.abspath(sys.executable))
    except Exception:
        pass
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return os.path.abspath(candidates[0] if candidates else ".")


def app_dir() -> str:
    """
    Writable directory next to the executable (or preview_studio/ when source).
    Config, accounts.json, output/, crash log live here.

    macOS .app: use folder containing *.app (not Contents/MacOS).
    """
    if is_frozen():
        exe = exe_file()
        norm = exe.replace("\\", "/")
        if ".app/Contents/MacOS" in norm:
            cur = exe
            while cur and not cur.endswith(".app"):
                parent = os.path.dirname(cur)
                if parent == cur:
                    break
                cur = parent
            if cur.endswith(".app"):
                return os.path.dirname(cur)
        return os.path.dirname(exe)
    return os.path.dirname(os.path.abspath(__file__))


def resource_dir() -> str:
    """Bundled read-only assets (silent_*.mp3, Qt data files)."""
    if is_frozen():
        here = os.path.dirname(os.path.abspath(__file__))
        if here and os.path.isdir(here):
            return here
        return app_dir()
    return os.path.dirname(os.path.abspath(__file__))


def portable_bin_dir() -> str:
    return os.path.join(app_dir(), "bin")


def portable_camoufox_dir() -> str:
    """Prefer neutral folder name; keep legacy name for old zips."""
    base = app_dir()
    for name in ("runtime", "camoufox-browser"):
        p = os.path.join(base, name)
        if _looks_like_camoufox_install(p):
            return p
    # default target for new installs / CI
    return os.path.join(base, "runtime")


def boot_log_path() -> str:
    return os.path.join(app_dir(), "studio_boot.log")


def write_boot_log(msg: str) -> str:
    """Append line to studio_boot.log next to EXE. Returns log path."""
    path = boot_log_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass
    return path


def _looks_like_camoufox_install(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    try:
        names = set(os.listdir(path))
    except OSError:
        return False
    if not names:
        return False
    markers = (
        "version.json",
        "camoufox.exe",
        "camoufox-bin",
        "Camoufox.app",
        "camoufox",
    )
    if any(m in names for m in markers):
        return True
    return any(os.path.isdir(os.path.join(path, n)) for n in names)


def find_portable_ffmpeg() -> Optional[str]:
    import shutil

    base = app_dir()
    candidates = [
        os.path.join(base, "bin", "ffmpeg.exe"),
        os.path.join(base, "bin", "ffmpeg"),
        os.path.join(base, "ffmpeg.exe"),
        os.path.join(base, "ffmpeg"),
        os.path.join(resource_dir(), "bin", "ffmpeg.exe"),
        os.path.join(resource_dir(), "ffmpeg.exe"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")


def find_portable_ffprobe() -> Optional[str]:
    import shutil

    base = app_dir()
    candidates = [
        os.path.join(base, "bin", "ffprobe.exe"),
        os.path.join(base, "bin", "ffprobe"),
        os.path.join(base, "ffprobe.exe"),
        os.path.join(base, "ffprobe"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return shutil.which("ffprobe") or shutil.which("ffprobe.exe")


def setup_portable_runtime() -> dict:
    """
    Gọi sớm: PATH bin/ + patch camoufox INSTALL_DIR → camoufox-browser/.
    """
    info: dict = {
        "app_dir": app_dir(),
        "exe": exe_file(),
        "frozen": is_frozen(),
        "camoufox_dir": portable_camoufox_dir(),
        "camoufox_ok": False,
        "ffmpeg": find_portable_ffmpeg() or "",
        "patched": False,
    }
    try:
        from user_safe import quiet_tech_logs

        _quiet = quiet_tech_logs()
    except Exception:
        _quiet = is_frozen()

    if _quiet:
        write_boot_log(f"setup frozen={info['frozen']} ok")
    else:
        write_boot_log(
            f"setup_portable frozen={info['frozen']} app_dir={info['app_dir']} exe={info['exe']}"
        )

    bind = portable_bin_dir()
    if os.path.isdir(bind):
        os.environ["PATH"] = bind + os.pathsep + os.environ.get("PATH", "")
        if not _quiet:
            write_boot_log(f"PATH prepend bin={bind}")

    fox = portable_camoufox_dir()
    info["camoufox_ok"] = _looks_like_camoufox_install(fox)
    write_boot_log("runtime_browser_ok" if info["camoufox_ok"] else "runtime_browser_missing")

    try:
        import camoufox.pkgman as pkgman

        try:
            Path(fox).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        pkgman.INSTALL_DIR = Path(fox)
        os.environ["CAMOUFOX_INSTALL_DIR"] = fox
        info["patched"] = True
        if not _quiet:
            write_boot_log(f"camoufox INSTALL_DIR patched → {fox}")
    except Exception as e:
        info["camoufox_error"] = str(e)[:200]
        write_boot_log("runtime_browser_patch_fail" if _quiet else f"camoufox patch fail: {e}")

    info["ffmpeg"] = find_portable_ffmpeg() or ""
    write_boot_log("ffmpeg_ok" if info["ffmpeg"] else "ffmpeg_missing")
    return info


def ensure_camoufox_browser(download_if_missing: bool = True) -> str:
    setup_portable_runtime()
    fox = portable_camoufox_dir()
    if _looks_like_camoufox_install(fox):
        try:
            import camoufox.pkgman as pkgman

            pkgman.INSTALL_DIR = Path(fox)
        except Exception:
            pass
        return fox

    if not download_if_missing:
        raise FileNotFoundError(
            "Thiếu thành phần runtime. Hãy cài lại bản portable đầy đủ."
        )

    try:
        import camoufox.pkgman as pkgman

        pkgman.INSTALL_DIR = Path(fox)
        Path(fox).mkdir(parents=True, exist_ok=True)
        write_boot_log("runtime_fetch_start")
        pkgman.CamoufoxFetcher().install()
        if not _looks_like_camoufox_install(fox):
            default = Path(pkgman.user_cache_dir("camoufox"))
            if default.is_dir() and default != Path(fox):
                import shutil

                if Path(fox).exists():
                    shutil.rmtree(fox, ignore_errors=True)
                shutil.copytree(str(default), fox)
        pkgman.INSTALL_DIR = Path(fox)
        write_boot_log("runtime_fetch_done")
        return fox
    except Exception as e:
        write_boot_log("runtime_fetch_fail")
        raise RuntimeError(
            "Không cài được thành phần runtime. Cài lại bản portable đầy đủ."
        ) from e


def ensure_sys_path() -> None:
    studio = app_dir() if is_frozen() else os.path.dirname(os.path.abspath(__file__))
    root = studio if is_frozen() else os.path.dirname(studio)
    for p in (studio, root):
        if p and p not in sys.path:
            sys.path.insert(0, p)


def show_fatal_dialog(title: str, message: str) -> None:
    """Best-effort error UI (Qt or Win32 MessageBox) — sanitized for ship."""
    try:
        from user_safe import PRODUCT_NAME, sanitize_user_error

        safe_title = title if title.startswith("TTS") else f"{PRODUCT_NAME}"
        safe_msg = sanitize_user_error(message, fallback="Ứng dụng gặp lỗi khởi động.")
    except Exception:
        safe_title = title or "TTS Studio"
        safe_msg = "Ứng dụng gặp lỗi khởi động. Liên hệ quản trị viên."
    write_boot_log(f"FATAL {safe_title}")
    try:
        from PySide6 import QtWidgets

        app = QtWidgets.QApplication.instance()
        if app is None:
            app = QtWidgets.QApplication(sys.argv)
        QtWidgets.QMessageBox.critical(None, safe_title, safe_msg[:500])
        return
    except Exception:
        pass
    try:
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, safe_msg[:500], safe_title[:120], 0x10)
    except Exception:
        pass
