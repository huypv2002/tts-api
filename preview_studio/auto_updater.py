# -*- coding: utf-8 -*-
"""
Auto-update TTS Studio from GitHub Releases (Nuitka portable ZIP).

Adapted from Veo3 gui_app_clone/src/core/updater.py:
  - Check release tag / asset updated_at
  - Download ZIP with progress
  - Extract → _updater.bat → replace files → restart EXE

Rolling tag: tts-studio-windows-latest (compare asset updated_at).
Also supports semver tags via APP_VERSION when present.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
import urllib.error
import urllib.request
import zipfile
from typing import Callable, Optional, Tuple

from PySide6.QtCore import QThread, Signal

from version import (
    APP_VERSION,
    ASSET_NAME,
    EXE_NAME,
    GITHUB_OWNER,
    GITHUB_REPO,
    RELEASE_TAG,
)

try:
    from app_paths import app_dir as _app_dir_fn
except Exception:  # pragma: no cover
    def _app_dir_fn() -> str:  # type: ignore
        if getattr(sys, "frozen", False):
            return os.path.dirname(os.path.abspath(sys.executable))
        return os.path.dirname(os.path.abspath(__file__))


RELEASES_TAG_API = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/tags/{RELEASE_TAG}"
)
RELEASES_LATEST_API = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)

ProgressCb = Optional[Callable[[int], None]]
LogCb = Optional[Callable[[str], None]]


def _log(msg: str, log_cb: LogCb = None) -> None:
    line = str(msg)
    print(f"[updater] {line}")
    try:
        path = os.path.join(_app_dir_fn(), "updater.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    if log_cb:
        try:
            log_cb(line)
        except Exception:
            pass


def _state_path() -> str:
    return os.path.join(_app_dir_fn(), "update_state.json")


def load_update_state() -> dict:
    try:
        p = _state_path()
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception:
        pass
    return {}


def save_update_state(data: dict) -> None:
    try:
        with open(_state_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _parse_version(tag: str) -> tuple:
    tag = (tag or "").lstrip("vV").strip()
    parts = []
    for p in tag.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            # non-numeric tags (rolling) → (0,)
            parts.append(0)
    return tuple(parts) if parts else (0,)


def _http_get_json(url: str, timeout: float = 20.0) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"TTSStudio-Updater/{APP_VERSION}",
    }
    # Optional token for private repos
    tok = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _pick_asset(release: dict) -> Optional[dict]:
    for asset in release.get("assets") or []:
        name = asset.get("name") or ""
        if name == ASSET_NAME or name.endswith(".zip"):
            if name == ASSET_NAME:
                return asset
    for asset in release.get("assets") or []:
        if str(asset.get("name") or "").endswith(".zip"):
            return asset
    return None


def check_for_update() -> Tuple[bool, dict]:
    """
    Returns (has_update, info).
    info keys: tag, download_url, notes, asset_updated_at, size, error
    """
    info: dict = {
        "tag": "",
        "download_url": "",
        "notes": "",
        "asset_updated_at": "",
        "size": 0,
        "error": "",
        "local_version": APP_VERSION,
    }
    try:
        release = None
        try:
            release = _http_get_json(RELEASES_TAG_API)
        except Exception as e1:
            try:
                release = _http_get_json(RELEASES_LATEST_API)
            except Exception as e2:
                info["error"] = f"API: {e1} / {e2}"
                return False, info

        if not release or release.get("message"):
            info["error"] = str((release or {}).get("message") or "empty release")
            return False, info

        tag = str(release.get("tag_name") or "")
        info["tag"] = tag
        info["notes"] = str(release.get("body") or "")[:2000]
        asset = _pick_asset(release)
        if not asset:
            info["error"] = f"Không thấy asset {ASSET_NAME}"
            return False, info

        dl = str(asset.get("browser_download_url") or "")
        updated = str(asset.get("updated_at") or release.get("published_at") or "")
        size = int(asset.get("size") or 0)
        info["download_url"] = dl
        info["asset_updated_at"] = updated
        info["size"] = size
        if not dl:
            info["error"] = "missing download_url"
            return False, info

        state = load_update_state()
        last = str(state.get("asset_updated_at") or "")
        # First run after install: seed state without forcing update
        if not last:
            # Still offer update if remote semver > local when tag is v*
            if tag.startswith("v") and _parse_version(tag) > _parse_version(APP_VERSION):
                return True, info
            # Rolling: treat unknown local as up-to-date after first check seed
            save_update_state(
                {
                    "asset_updated_at": updated,
                    "tag": tag,
                    "app_version": APP_VERSION,
                    "checked_once": True,
                }
            )
            return False, info

        if updated and updated != last:
            return True, info
        if tag.startswith("v") and _parse_version(tag) > _parse_version(APP_VERSION):
            return True, info
        return False, info
    except Exception as e:
        info["error"] = str(e)
        return False, info


def download_update(
    download_url: str,
    progress_cb: ProgressCb = None,
    log_cb: LogCb = None,
    stop_flag: Optional[Callable[[], bool]] = None,
) -> Tuple[bool, str]:
    """
    Download + extract ZIP. Returns (ok, new_app_dir_or_error).
    """
    try:
        app_dir = _app_dir_fn()
        update_dir = os.path.join(app_dir, "_update_tmp")
        if os.path.exists(update_dir):
            shutil.rmtree(update_dir, ignore_errors=True)
        os.makedirs(update_dir, exist_ok=True)
        zip_path = os.path.join(update_dir, ASSET_NAME)

        _log(f"Tải {download_url}", log_cb)
        headers = {
            "User-Agent": f"TTSStudio-Updater/{APP_VERSION}",
            "Accept": "application/octet-stream",
        }
        tok = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        req = urllib.request.Request(download_url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=600) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            downloaded = 0
            with open(zip_path, "wb") as f:
                while True:
                    if stop_flag and stop_flag():
                        return False, "Đã hủy"
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0 and progress_cb:
                        progress_cb(int(downloaded * 100 / total))
        if progress_cb:
            progress_cb(100)

        extract_dir = os.path.join(update_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        new_app_dir = None
        # Prefer folder containing EXE_NAME
        for root, _dirs, files in os.walk(extract_dir):
            if EXE_NAME in files or "TTSStudio.exe" in files or "PreviewStudio.exe" in files:
                new_app_dir = root
                break
        if not new_app_dir:
            # single top-level dir
            items = os.listdir(extract_dir)
            if len(items) == 1 and os.path.isdir(os.path.join(extract_dir, items[0])):
                new_app_dir = os.path.join(extract_dir, items[0])
            else:
                new_app_dir = extract_dir

        # Verify exe exists
        candidates = [EXE_NAME, "TTSStudio.exe", "PreviewStudio.exe"]
        found = any(os.path.isfile(os.path.join(new_app_dir, n)) for n in candidates)
        if not found:
            return False, f"Không thấy EXE trong ZIP (cần {EXE_NAME})"
        _log(f"Extract OK → {new_app_dir}", log_cb)
        return True, new_app_dir
    except Exception as e:
        _log(f"download fail: {e}\n{traceback.format_exc()}", log_cb)
        return False, str(e)


def apply_update(new_app_dir: str, log_cb: LogCb = None) -> None:
    """
    Write _updater.bat, launch CREATE_NEW_CONSOLE, force-exit app.
    Preserves: output/, accounts.json, proxies.json, packages.json,
    preview_studio_config.json, login_temp.json, update_state.json, _update_tmp
    """
    app_dir = _app_dir_fn()
    current_pid = os.getpid()
    bat_path = os.path.join(app_dir, "_updater.bat")
    _log(f"apply_update app_dir={app_dir} new={new_app_dir} pid={current_pid}", log_cb)

    # Detect which exe name to start
    start_exe = EXE_NAME
    for n in (EXE_NAME, "TTSStudio.exe", "PreviewStudio.exe"):
        if os.path.isfile(os.path.join(new_app_dir, n)):
            start_exe = n
            break

    keep_files = {
        "_updater.bat",
        "_update_tmp",
        "accounts.json",
        "proxies.json",
        "packages.json",
        "preview_studio_config.json",
        "login_temp.json",
        "update_state.json",
        "updater.log",
        "studio_boot.log",
    }
    keep_dirs = {"output", "_update_tmp", "data"}

    bat_content = f'''@echo off
chcp 65001 >nul
title Updating TTS Studio...
echo ============================================
echo   Dang cap nhat TTS Studio...
echo ============================================
echo.

set /a count=0
:wait_loop
tasklist /FI "PID eq {current_pid}" 2>nul | find /I "{current_pid}" >nul
if not errorlevel 1 (
    set /a count+=1
    if %count% GEQ 40 (
        echo Timeout — force kill...
        taskkill /PID {current_pid} /F >nul 2>&1
        timeout /t 2 /nobreak >nul
        goto :do_update
    )
    timeout /t 1 /nobreak >nul
    goto :wait_loop
)

:do_update
echo Dang cap nhat files...
timeout /t 1 /nobreak >nul

:: Xoa files cu (giu config/data)
for %%F in ("{app_dir}\\*") do (
    set "n=%%~nxF"
    if /I not "%%~nxF"=="_updater.bat" if /I not "%%~nxF"=="accounts.json" if /I not "%%~nxF"=="proxies.json" if /I not "%%~nxF"=="packages.json" if /I not "%%~nxF"=="preview_studio_config.json" if /I not "%%~nxF"=="login_temp.json" if /I not "%%~nxF"=="update_state.json" if /I not "%%~nxF"=="updater.log" (
        del /F /Q "%%F" >nul 2>&1
    )
)
for /D %%D in ("{app_dir}\\*") do (
    if /I not "%%~nxD"=="output" if /I not "%%~nxD"=="_update_tmp" if /I not "%%~nxD"=="data" (
        rmdir /S /Q "%%D" >nul 2>&1
    )
)

echo Copy ban moi...
for %%F in ("{new_app_dir}\\*") do (
    copy /Y "%%F" "{app_dir}\\" >nul 2>&1
)
for /D %%D in ("{new_app_dir}\\*") do (
    if /I not "%%~nxD"=="output" if /I not "%%~nxD"=="data" (
        xcopy /E /I /Y "%%D" "{app_dir}\\%%~nxD" >nul 2>&1
    )
)

echo Don dep...
rmdir /S /Q "{app_dir}\\_update_tmp" >nul 2>&1

echo Khoi dong ban moi...
if exist "{app_dir}\\{start_exe}" (
    start "" "{app_dir}\\{start_exe}"
) else if exist "{app_dir}\\TTS Studio.exe" (
    start "" "{app_dir}\\TTS Studio.exe"
) else if exist "{app_dir}\\TTSStudio.exe" (
    start "" "{app_dir}\\TTSStudio.exe"
) else (
    start "" "{app_dir}\\CHAY-STUDIO.bat"
)

echo Cap nhat thanh cong!
timeout /t 2 /nobreak >nul
del /F /Q "%~f0" >nul 2>&1
exit
'''
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(bat_content)

    if sys.platform == "win32":
        CREATE_NEW_CONSOLE = 0x00000010
        subprocess.Popen(
            ["cmd", "/c", bat_path],
            creationflags=CREATE_NEW_CONSOLE,
            cwd=app_dir,
        )
    else:
        # Dev on macOS/Linux: just open folder
        _log(f"Non-Windows: extracted at {new_app_dir}", log_cb)
        return

    os._exit(0)


class UpdateChecker(QThread):
    """Background check GitHub release."""

    result = Signal(bool, dict)  # has_update, info

    def run(self):
        has, info = check_for_update()
        self.result.emit(has, info)


class UpdateDownloader(QThread):
    progress = Signal(int)
    finished = Signal(bool, str)

    def __init__(self, download_url: str, parent=None):
        super().__init__(parent)
        self.download_url = download_url
        self._stopped = False

    def stop(self):
        self._stopped = True

    def run(self):
        ok, path = download_update(
            self.download_url,
            progress_cb=lambda p: self.progress.emit(p),
            stop_flag=lambda: self._stopped,
        )
        self.finished.emit(ok, path)
