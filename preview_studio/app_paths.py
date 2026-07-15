# -*- coding: utf-8 -*-
"""Paths for source run vs Nuitka frozen (onefile / standalone).

Portable layout (user chỉ cần giải nén folder + double-click EXE):

  TTS-Studio-Nuitka/
    TTS Studio.exe
    bin/ffmpeg.exe          (optional, preferred)
    bin/ffprobe.exe
    camoufox-browser/       (Camoufox Firefox binary — bắt buộc cho TTS)
    silent_*.mp3
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


def is_frozen() -> bool:
    # Nuitka sets sys.frozen; also detect __compiled__ on this module
    if getattr(sys, "frozen", False):
        return True
    try:
        return bool(__compiled__)  # type: ignore[name-defined]
    except NameError:
        return False


def app_dir() -> str:
    """
    Writable directory next to the executable (or preview_studio/ when source).
    Config, accounts.json, output/, crash log live here.

    macOS .app: use folder containing PreviewStudio.app (not Contents/MacOS).
    """
    if is_frozen():
        exe = os.path.abspath(sys.executable)
        # .../Something.app/Contents/MacOS/binary → parent of .app
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
    """
    Bundled read-only assets (silent_*.mp3, etc.).
    Nuitka onefile extracts beside __file__ of this module.
    """
    if is_frozen():
        # Prefer directory of this compiled module (contains include-data-files)
        here = os.path.dirname(os.path.abspath(__file__))
        if here and os.path.isdir(here):
            return here
        return app_dir()
    return os.path.dirname(os.path.abspath(__file__))


def portable_bin_dir() -> str:
    """bin/ cạnh exe — chứa ffmpeg.exe / ffprobe.exe."""
    return os.path.join(app_dir(), "bin")


def portable_camoufox_dir() -> str:
    """
    Thư mục browser Camoufox portable cạnh exe.
    CI copy toàn bộ camoufox INSTALL_DIR vào đây.
    """
    return os.path.join(app_dir(), "camoufox-browser")


def _looks_like_camoufox_install(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    try:
        names = set(os.listdir(path))
    except OSError:
        return False
    if not names:
        return False
    # version.json + binary / .app
    markers = (
        "version.json",
        "camoufox.exe",
        "camoufox-bin",
        "Camoufox.app",
        "camoufox",
    )
    if any(m in names for m in markers):
        return True
    # nested mac layout
    return any(os.path.isdir(os.path.join(path, n)) for n in names)


def find_portable_ffmpeg() -> Optional[str]:
    """ffmpeg cạnh exe: bin/ffmpeg.exe | ffmpeg.exe | PATH."""
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
    Gọi càng sớm càng tốt (trước khi start HSW / Camoufox).

    - Trỏ camoufox.pkgman.INSTALL_DIR → camoufox-browser/ cạnh exe
    - Thêm bin/ vào PATH để ffmpeg được tìm thấy
    """
    info: dict = {
        "app_dir": app_dir(),
        "camoufox_dir": portable_camoufox_dir(),
        "camoufox_ok": False,
        "ffmpeg": find_portable_ffmpeg() or "",
        "patched": False,
    }
    # PATH: bin cạnh exe
    bind = portable_bin_dir()
    if os.path.isdir(bind):
        os.environ["PATH"] = bind + os.pathsep + os.environ.get("PATH", "")

    fox = portable_camoufox_dir()
    info["camoufox_ok"] = _looks_like_camoufox_install(fox)

    # Patch camoufox INSTALL_DIR nếu package đã import được
    try:
        import camoufox.pkgman as pkgman

        if info["camoufox_ok"]:
            pkgman.INSTALL_DIR = Path(fox)
            info["patched"] = True
            # một số bản cache path qua env
            os.environ["CAMOUFOX_INSTALL_DIR"] = fox
        else:
            # Chưa có browser bundled: cài vào folder portable (không rơi vào cache user)
            try:
                Path(fox).mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            # Vẫn trỏ INSTALL_DIR về portable để fetch ghi đúng chỗ
            pkgman.INSTALL_DIR = Path(fox)
            info["patched"] = True
            os.environ["CAMOUFOX_INSTALL_DIR"] = fox
    except Exception as e:
        info["camoufox_error"] = str(e)[:200]

    return info


def ensure_camoufox_browser(download_if_missing: bool = True) -> str:
    """
    Đảm bảo có Camoufox binary. Trả về path thư mục install.
    Portable: dùng camoufox-browser/ cạnh exe; thiếu thì fetch vào đó.
    """
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
            f"Thiếu Camoufox browser tại {fox}. "
            "Hãy dùng bản full portable hoặc chạy: camoufox fetch"
        )

    try:
        import camoufox.pkgman as pkgman

        pkgman.INSTALL_DIR = Path(fox)
        Path(fox).mkdir(parents=True, exist_ok=True)
        # Cài vào INSTALL_DIR đã patch
        pkgman.CamoufoxFetcher().install()
        if not _looks_like_camoufox_install(fox):
            # fallback: copy từ cache mặc định nếu fetcher ghi chỗ khác
            default = Path(pkgman.user_cache_dir("camoufox"))
            if default.is_dir() and default != Path(fox):
                import shutil

                if Path(fox).exists():
                    shutil.rmtree(fox, ignore_errors=True)
                shutil.copytree(str(default), fox)
        pkgman.INSTALL_DIR = Path(fox)
        return fox
    except Exception as e:
        raise RuntimeError(
            f"Không cài được Camoufox browser: {e}\n"
            f"Thư mục mong đợi: {fox}"
        ) from e


def ensure_sys_path() -> None:
    """Make studio + repo root importable (fast_tts)."""
    studio = app_dir() if is_frozen() else os.path.dirname(os.path.abspath(__file__))
    # When frozen, fast_tts is compiled into the binary; still put dirs on path.
    root = studio if is_frozen() else os.path.dirname(studio)
    for p in (studio, root):
        if p and p not in sys.path:
            sys.path.insert(0, p)
