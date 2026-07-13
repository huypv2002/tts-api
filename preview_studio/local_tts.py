# -*- coding: utf-8 -*-
"""Local preview TTS via fast_tts (HSW + anonymous) — no tts-api server."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

# repo root = parent of preview_studio/
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fast_tts import (  # noqa: E402
    DEFAULT_MODEL,
    DEFAULT_VOICE,
    call_tts,
    close_hsw_farm,
    normalize_proxy,
    solve_token,
    start_hsw_farm,
)


_farm_ready = False
_farm_lock = asyncio.Lock()


async def ensure_farm(proxy: Optional[str] = None, hsw_workers: int = 2) -> None:
    global _farm_ready
    async with _farm_lock:
        if _farm_ready:
            return
        await start_hsw_farm(
            size=max(1, hsw_workers),
            proxy_http=proxy,
            via_proxy=False,
            warm=True,
        )
        _farm_ready = True


async def synthesize_one(
    text: str,
    out_path: str,
    proxy: Optional[str],
    voice: str = DEFAULT_VOICE,
    model: str = DEFAULT_MODEL,
    lang: str = "en",
    speed: float = 1.0,
    hsw_workers: int = 2,
) -> dict:
    proxy = normalize_proxy(proxy)
    await ensure_farm(proxy, hsw_workers=hsw_workers)
    token = await solve_token(proxy)
    audio = await call_tts(text, token, proxy, voice, model, lang, speed)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_bytes(audio)
    return {"bytes": len(audio), "path": out_path}


def synthesize_one_sync(
    text: str,
    out_path: str,
    proxy: Optional[str],
    voice: str = DEFAULT_VOICE,
    model: str = DEFAULT_MODEL,
    lang: str = "en",
    speed: float = 1.0,
    hsw_workers: int = 2,
) -> dict:
    return asyncio.run(
        synthesize_one(
            text, out_path, proxy, voice, model, lang, speed, hsw_workers
        )
    )


async def shutdown_farm() -> None:
    global _farm_ready
    try:
        await close_hsw_farm()
    except Exception:
        pass
    _farm_ready = False
