from __future__ import annotations

import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse

from ..config import AUDIO_DIR, load_settings, save_settings
from ..db import Database
from ..schemas import (
    AdminLogin,
    ApiKeyCreate,
    ApiKeyUpdate,
    ProxyUpsert,
    SettingsUpdate,
)
from ..services.proxy_pool import pool

router = APIRouter(prefix="/admin/api", tags=["admin"])


def get_db(request: Request) -> Database:
    return request.app.state.db


async def require_admin(
    request: Request,
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
) -> str:
    token = x_admin_token or request.cookies.get("admin_token")
    if not token:
        raise HTTPException(401, "admin auth required")
    db: Database = request.app.state.db
    if not await db.valid_session(token):
        raise HTTPException(401, "invalid or expired session")
    return token


@router.post("/login")
async def login(body: AdminLogin, request: Request, db: Database = Depends(get_db)):
    settings = load_settings()
    expected = (settings.get("admin_password") or "").strip()
    got = (body.password or "").strip()
    if not expected or got != expected:
        raise HTTPException(401, "wrong password")
    token = secrets.token_urlsafe(32)
    await db.create_session(token)
    return {"ok": True, "token": token}


@router.post("/logout")
async def logout(token: str = Depends(require_admin), db: Database = Depends(get_db)):
    await db.delete_session(token)
    return {"ok": True}


@router.get("/dashboard")
async def dashboard(
    _: str = Depends(require_admin),
    db: Database = Depends(get_db),
):
    settings = load_settings()
    usage = await db.usage_summary(14)
    keys = await db.list_api_keys()
    jobs = await db.list_jobs(30)
    return {
        "settings": {
            k: v
            for k, v in settings.items()
            if k not in ("admin_password", "admin_session_secret")
        },
        "usage": usage,
        "keys_count": len(keys),
        "proxy": pool.stats(),
        "proxies": pool.list_public(),
        "recent_jobs": jobs,
    }


@router.get("/settings")
async def get_settings(_: str = Depends(require_admin)):
    s = load_settings()
    s = {k: v for k, v in s.items() if k not in ("admin_password", "admin_session_secret")}
    s["admin_password_set"] = True
    return s


@router.put("/settings")
async def put_settings(
    body: SettingsUpdate,
    _: str = Depends(require_admin),
    db: Database = Depends(get_db),
):
    patch = body.model_dump(exclude_none=True)
    apply_keys = bool(patch.pop("apply_to_all_keys", False))
    if "admin_password" in patch and not patch["admin_password"]:
        del patch["admin_password"]

    # validate max chars range
    if "default_max_chars" in patch:
        try:
            patch["default_max_chars"] = int(patch["default_max_chars"])
        except (TypeError, ValueError):
            raise HTTPException(400, "default_max_chars must be an integer")
        if patch["default_max_chars"] < 1 or patch["default_max_chars"] > 10000:
            raise HTTPException(400, "default_max_chars out of range (1-10000)")
    if "hard_max_chars" in patch:
        try:
            patch["hard_max_chars"] = int(patch["hard_max_chars"])
        except (TypeError, ValueError):
            raise HTTPException(400, "hard_max_chars must be an integer")

    s = save_settings(patch)

    keys_updated = 0
    if apply_keys:
        key_patch = {}
        if "default_max_chars" in patch:
            key_patch["max_chars"] = patch["default_max_chars"]
        if "default_quota_chars_day" in patch:
            key_patch["quota_chars_day"] = patch["default_quota_chars_day"]
        if "default_quota_jobs_day" in patch:
            key_patch["quota_jobs_day"] = patch["default_quota_jobs_day"]
        if "default_max_concurrent" in patch:
            key_patch["max_concurrent"] = patch["default_max_concurrent"]
        if key_patch:
            rows = await db.list_api_keys()
            for row in rows:
                await db.update_api_key(row["id"], **key_patch)
                keys_updated += 1

    out = {
        k: v
        for k, v in s.items()
        if k not in ("admin_password", "admin_session_secret")
    }
    out["keys_updated"] = keys_updated
    out["saved"] = True
    return out


@router.get("/keys")
async def list_keys(_: str = Depends(require_admin), db: Database = Depends(get_db)):
    rows = await db.list_api_keys()
    # never return hash; keep proxy bind fields for admin UI
    for r in rows:
        r.pop("key_hash", None)
        # cap display concurrent
        if r.get("max_concurrent") is not None:
            try:
                r["max_concurrent"] = max(1, min(5, int(r["max_concurrent"])))
            except Exception:
                pass
        r["has_proxy"] = bool(r.get("proxy_host") and r.get("proxy_username"))
    return {"keys": rows}


@router.post("/keys")
async def create_key(
    body: ApiKeyCreate,
    _: str = Depends(require_admin),
    db: Database = Depends(get_db),
):
    row = await db.create_api_key(
        name=body.name,
        max_chars=body.max_chars,
        quota_chars_day=body.quota_chars_day,
        quota_jobs_day=body.quota_jobs_day,
        max_concurrent=body.max_concurrent,
        note=body.note,
    )
    raw = row.get("raw_key")
    proxy_fields = {
        k: v
        for k, v in body.model_dump(exclude_none=True).items()
        if k.startswith("proxy_")
    }
    if proxy_fields:
        updated = await db.update_api_key(row["id"], **proxy_fields)
        if updated:
            row = updated
    return {
        "id": row["id"],
        "name": row["name"],
        "key": raw,
        "key_prefix": row["key_prefix"],
        "max_chars": row["max_chars"],
        "quota_chars_day": row["quota_chars_day"],
        "quota_jobs_day": row["quota_jobs_day"],
        "max_concurrent": row["max_concurrent"],
        "proxy_host": row.get("proxy_host") or "",
        "proxy_port": row.get("proxy_port") or 0,
        "has_proxy": bool(row.get("proxy_host") and row.get("proxy_username")),
        "note": "Save the key now — it will not be shown again.",
    }


@router.patch("/keys/{key_id}")
async def patch_key(
    key_id: int,
    body: ApiKeyUpdate,
    _: str = Depends(require_admin),
    db: Database = Depends(get_db),
):
    fields = body.model_dump(exclude_none=True)
    if "enabled" in fields:
        fields["enabled"] = 1 if fields["enabled"] else 0
    row = await db.update_api_key(key_id, **fields)
    if not row:
        raise HTTPException(404, "key not found")
    row.pop("key_hash", None)
    return row


@router.delete("/keys/{key_id}")
async def del_key(
    key_id: int, _: str = Depends(require_admin), db: Database = Depends(get_db)
):
    ok = await db.delete_api_key(key_id)
    if not ok:
        raise HTTPException(404)
    return {"ok": True}


@router.get("/proxies")
async def list_proxies(_: str = Depends(require_admin)):
    return {"proxies": pool.list_public(), "stats": pool.stats()}


@router.post("/proxies")
async def upsert_proxy(body: ProxyUpsert, _: str = Depends(require_admin)):
    slot = pool.upsert(body.model_dump())
    return slot.to_public()


@router.delete("/proxies/{sid}")
async def delete_proxy(sid: str, _: str = Depends(require_admin)):
    if not pool.delete(sid):
        raise HTTPException(404)
    return {"ok": True}


@router.post("/proxies/{sid}/rotate")
async def rotate_proxy(sid: str, _: str = Depends(require_admin)):
    try:
        return await pool.force_rotate(sid)
    except KeyError:
        raise HTTPException(404, "proxy not found")
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/jobs")
async def list_jobs(
    status: Optional[str] = None,
    limit: int = 50,
    _: str = Depends(require_admin),
    db: Database = Depends(get_db),
):
    return {"jobs": await db.list_jobs(limit=limit, status=status)}


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: str, _: str = Depends(require_admin), db: Database = Depends(get_db)
):
    j = await db.get_job(job_id)
    if not j:
        raise HTTPException(404)
    return j


@router.get("/jobs/{job_id}/audio")
async def admin_audio(
    job_id: str, _: str = Depends(require_admin), db: Database = Depends(get_db)
):
    j = await db.get_job(job_id)
    if not j or j["status"] != "done":
        raise HTTPException(404)
    from pathlib import Path

    p = Path(j.get("audio_path") or AUDIO_DIR / f"{job_id}.mp3")
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="audio/mpeg", filename=f"{job_id}.mp3")


@router.get("/usage")
async def usage(
    _: str = Depends(require_admin), db: Database = Depends(get_db)
):
    return await db.usage_summary(30)
