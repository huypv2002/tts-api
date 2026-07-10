from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse

from ..config import AUDIO_DIR, load_settings
from ..db import Database
from ..schemas import TtsRequest
from ..services.chunker import HARD_MAX, load_text_chunks

router = APIRouter(prefix="/v1", tags=["public"])


def get_db(request: Request) -> Database:
    return request.app.state.db


async def require_api_key(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> dict:
    raw = None
    if x_api_key:
        raw = x_api_key.strip()
    elif authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
    if not raw:
        raise HTTPException(401, "Missing API key (X-API-Key or Authorization: Bearer)")
    db: Database = request.app.state.db
    key = await db.get_api_key_by_raw(raw)
    if not key:
        raise HTTPException(401, "Invalid API key")
    return key


@router.get("/health")
async def health(request: Request):
    from ..services.proxy_pool import pool

    return {
        "ok": True,
        "service": "tts-api",
        "version": "1.0.0",
        "uptime_s": int(time.time() - request.app.state.started),
        "proxy": pool.stats(),
        "workers": len(getattr(request.app.state, "workers", None)._tasks or [])
        if getattr(request.app.state, "workers", None)
        else 0,
    }


@router.post("/tts")
async def create_tts(
    body: TtsRequest,
    request: Request,
    key: dict = Depends(require_api_key),
    db: Database = Depends(get_db),
):
    settings = load_settings()
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "empty text")

    max_chars = key.get("max_chars") or settings["default_max_chars"]
    max_chars = min(int(max_chars), int(settings.get("hard_max_chars") or HARD_MAX))

    # single-chunk API: if longer, take first chunk or reject multi
    chunks = load_text_chunks(text, max_chars=max_chars)
    if not chunks:
        raise HTTPException(400, "empty after normalize")
    if len(text) > max_chars:
        # use first chunk only + warn, or require client to split
        piece = chunks[0]
        if body.wait:
            # still allow first chunk
            text = piece
        else:
            # store only first chunk; return note about remaining
            text = piece

    chars = len(text)
    ok, reason = await db.check_quota(key, chars)
    if not ok:
        raise HTTPException(429, reason)

    job_id = "job_" + secrets.token_hex(12)
    job = await db.create_job(
        {
            "id": job_id,
            "api_key_id": key["id"],
            "status": "queued",
            "text": text,
            "text_chars": chars,
            "voice": body.voice or settings["default_voice"],
            "model": body.model or settings["default_model"],
            "lang": body.language_code or settings["default_lang"],
            "speed": body.speed,
        }
    )

    base = settings.get("public_base_url") or str(request.base_url).rstrip("/")
    resp = {
        "id": job_id,
        "status": "queued",
        "chars": chars,
        "max_chars": max_chars,
        "poll_url": f"{base}/v1/tts/{job_id}",
        "audio_url": f"{base}/v1/tts/{job_id}/audio",
    }
    if len(chunks) > 1 or len(body.text.strip()) > max_chars:
        resp["note"] = (
            f"Text truncated to max_chars={max_chars} (word-boundary). "
            f"Source had ~{len(body.text)} chars / {len(chunks)} chunks if fully split."
        )

    if body.wait:
        # poll up to 90s
        deadline = time.time() + 90
        while time.time() < deadline:
            j = await db.get_job(job_id)
            if j and j["status"] in ("done", "failed"):
                return await job_status(job_id, request, key, db)
            await __import__("asyncio").sleep(0.5)
        resp["status"] = "running"
        resp["note"] = (resp.get("note") or "") + " wait timeout — poll poll_url"
    return resp


@router.get("/tts/{job_id}")
async def job_status(
    job_id: str,
    request: Request,
    key: dict = Depends(require_api_key),
    db: Database = Depends(get_db),
):
    j = await db.get_job(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    if j.get("api_key_id") != key["id"]:
        raise HTTPException(403, "not your job")
    settings = load_settings()
    base = settings.get("public_base_url") or str(request.base_url).rstrip("/")
    out = {
        "id": j["id"],
        "status": j["status"],
        "chars": j["text_chars"],
        "error": j.get("error"),
        "audio_bytes": j.get("audio_bytes"),
        "duration_ms": j.get("duration_ms"),
        "created_at": j.get("created_at"),
        "finished_at": j.get("finished_at"),
    }
    if j["status"] == "done":
        out["audio_url"] = f"{base}/v1/tts/{job_id}/audio"
    return out


@router.get("/tts/{job_id}/audio")
async def job_audio(
    job_id: str,
    key: dict = Depends(require_api_key),
    db: Database = Depends(get_db),
):
    j = await db.get_job(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    if j.get("api_key_id") != key["id"]:
        raise HTTPException(403, "not your job")
    if j["status"] != "done":
        raise HTTPException(409, f"job status={j['status']}")
    path = j.get("audio_path") or str(AUDIO_DIR / f"{job_id}.mp3")
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, "audio file missing")
    return FileResponse(p, media_type="audio/mpeg", filename=f"{job_id}.mp3")


@router.get("/me")
async def me(key: dict = Depends(require_api_key), db: Database = Depends(get_db)):
    key = await db.get_api_key(key["id"]) or key
    return {
        "id": key["id"],
        "name": key["name"],
        "max_chars": key["max_chars"],
        "quota_chars_day": key["quota_chars_day"],
        "quota_jobs_day": key["quota_jobs_day"],
        "chars_used_day": key["chars_used_day"],
        "jobs_used_day": key["jobs_used_day"],
        "max_concurrent": key["max_concurrent"],
        "total_chars": key["total_chars"],
        "total_jobs": key["total_jobs"],
    }
