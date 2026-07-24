# -*- coding: utf-8 -*-
"""App version + update metadata (values decoded at runtime)."""
from __future__ import annotations

import base64

APP_VERSION = "2.0.0"
APP_NAME = "TTS Studio"


def _d(s: str) -> str:
    try:
        return base64.b64decode(s.encode("ascii")).decode("utf-8")
    except Exception:
        return ""


# Rolling release targets (not shown in UI)
GITHUB_OWNER = _d("aHV5cHYyMDAy")
GITHUB_REPO = _d("dHRzLWFwaQ==")
RELEASE_TAG = "tts-studio-windows-latest"
ASSET_NAME = "TTS-Studio-Nuitka-windows.zip"
EXE_NAME = "TTS Studio.exe"
