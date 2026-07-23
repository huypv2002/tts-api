# -*- coding: utf-8 -*-
"""App version for auto-update checks.

Bump APP_VERSION when shipping a new Nuitka build (semver).
Rolling release tag is tracked separately via asset updated_at.
"""

APP_VERSION = "1.1.0"
APP_NAME = "ElevenLabs Unlimited Studio"

# GitHub rolling release (CI publishes here)
GITHUB_OWNER = "huypv2002"
GITHUB_REPO = "tts-api"
RELEASE_TAG = "tts-studio-windows-latest"
ASSET_NAME = "TTS-Studio-Nuitka-windows.zip"
EXE_NAME = "TTS Studio.exe"
