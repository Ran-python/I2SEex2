"""Teaching Platform backend - entry point.

Run (from ./backend, with the venv active):
    python app.py
or
    uvicorn app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core import config
from core.ai import AIError, extract_ai_config, list_provider_models, test_ai_connection
from core.database import init_db
from modules.attendance.router import router as attendance_router
from modules.comments.router import router as comments_router
from modules.homework.router import router as homework_router
from modules.polls.router import router as polls_router
from modules.roster.router import router as roster_router
from modules.summary.router import router as summary_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    bar = "=" * 64
    print(f"\n{bar}\n  Teaching Platform API")
    print(f"  Local:    http://localhost:{config.PORT}/api/health")
    if config.PUBLIC_APP_URL:
        print(f"  Public:   {config.PUBLIC_APP_URL}")
    print(f"  Database: {config.DATABASE_SUMMARY}")
    if config.FRONTEND_DIST_DIR.exists():
        print(f"  Serving built frontend at http://localhost:{config.PORT}/")
    print(f"{bar}\n", flush=True)
    yield


app = FastAPI(title="Teaching Platform API", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://.*$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(roster_router)
app.include_router(attendance_router)
app.include_router(comments_router)
app.include_router(polls_router)
app.include_router(summary_router)
app.include_router(homework_router)


class PinVerifyRequest(BaseModel):
    pin: str


class AITestRequest(BaseModel):
    provider: str | None = None
    api_key: str | None = None
    model: str | None = None


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/auth/verify-pin")
def verify_lecturer_pin(payload: PinVerifyRequest):
    if (payload.pin or "").strip() != config.LECTURER_PIN:
        raise HTTPException(status_code=403, detail="Incorrect Lecturer PIN. Access denied.")
    return {"ok": True}


@app.get("/api/ai/models")
def ai_models(request: Request, provider: str | None = None, api_key: str | None = None, refresh: bool = False):
    try:
        cfg = extract_ai_config(
            request,
            provider_override=provider,
            api_key_override=api_key,
        )
        return list_provider_models(cfg, force_refresh=refresh)
    except AIError as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.post("/api/ai/test")
def ai_test(payload: AITestRequest, request: Request):
    try:
        cfg = extract_ai_config(
            request,
            provider_override=payload.provider,
            api_key_override=payload.api_key,
            model_override=payload.model,
        )
        return test_ai_connection(cfg)
    except AIError as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


# --- Optional single-port mode: serve the built React app (npm run build) -------
if config.FRONTEND_DIST_DIR.exists():
    assets_dir = config.FRONTEND_DIST_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        dist = config.FRONTEND_DIST_DIR.resolve()
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and dist in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")


if __name__ == "__main__":
    uvicorn.run("app:app", host=config.HOST, port=config.PORT, reload=True)
