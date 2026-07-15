# -*- coding: utf-8 -*-
"""Paths for source run vs Nuitka frozen (onefile / standalone)."""
from __future__ import annotations

import os
import sys


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


def ensure_sys_path() -> None:
    """Make studio + repo root importable (fast_tts)."""
    studio = app_dir() if is_frozen() else os.path.dirname(os.path.abspath(__file__))
    # When frozen, fast_tts is compiled into the binary; still put dirs on path.
    root = studio if is_frozen() else os.path.dirname(studio)
    for p in (studio, root):
        if p and p not in sys.path:
            sys.path.insert(0, p)
