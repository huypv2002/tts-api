from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import ROOT, load_settings
from .db import Database
from .routes import admin, public
from .services.proxy_pool import pool
from .services.tts_worker import WorkerManager

STATIC = ROOT / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    db = Database()
    await db.connect()
    # re-queue jobs stuck in running after crash
    try:
        await db.conn.execute(
            "UPDATE jobs SET status = 'queued', error = 'requeued after restart' "
            "WHERE status = 'running'"
        )
        await db.conn.commit()
    except Exception:
        pass
    pool.load()
    # warm proxy URLs in background-ish
    for s in pool.slots.values():
        if s.enabled:
            try:
                await pool.ensure_url(s)
            except Exception:
                pass
    workers = WorkerManager(db)
    await workers.start()
    app.state.db = db
    app.state.workers = workers
    app.state.started = time.time()
    app.state.settings = settings
    print(
        f"[tts-api] started workers={settings.get('worker_count')} "
        f"proxies={len(pool.slots)} port={settings.get('port')}",
        flush=True,
    )
    yield
    await workers.stop()
    await db.close()


app = FastAPI(
    title="TTS API",
    version="1.0.0",
    description="Multi-tenant TTS API with proxy pool + admin dashboard",
    lifespan=lifespan,
)

settings = load_settings()
origins = settings.get("cors_origins") or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(public.router)
app.include_router(admin.router)

if STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/")
async def root():
    return RedirectResponse("/admin/")


@app.get("/admin")
@app.get("/admin/")
async def admin_ui():
    index = STATIC / "admin" / "index.html"
    if not index.exists():
        return {"error": "admin UI missing"}
    # Prevent Cloudflare/browser serving stale blank HTML
    return FileResponse(
        index,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


def run():
    import uvicorn

    s = load_settings()
    uvicorn.run(
        "server.main:app",
        host=s.get("host", "0.0.0.0"),
        port=int(s.get("port", 8787)),
        reload=False,
        factory=False,
    )


if __name__ == "__main__":
    run()
