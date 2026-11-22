"""Teaching Platform backend - entry point.

Run (from ./backend, with the venv active):
    python app.py
or
    uvicorn app:app --host 0.0.0.0 --port 8000

The server binds to 0.0.0.0 so students on the same Wi-Fi can reach it via the
lecturer's LAN IP. Students on any other network cannot reach it at all.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core import config
from core.database import init_db
from core.network import LanGuardMiddleware, get_client_ip, get_network_info, is_host_ip
from modules.attendance.router import router as attendance_router
from modules.roster.router import router as roster_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    info = get_network_info(force_refresh=True)
    bar = "=" * 64
    print(f"\n{bar}\n  Teaching Platform API")
    print(f"  Local:    http://localhost:{config.PORT}/api/health")
    print(f"  LAN:      http://{info.lan_ip}:{config.PORT}/api/health")
    print(f"  Subnet allowed: {info.subnet}")
    print(f"  Database: {config.DATABASE_SUMMARY}")
    if config.FRONTEND_DIST_DIR.exists():
        print(f"  Serving built frontend at http://{info.lan_ip}:{config.PORT}/")
    print(f"{bar}\n", flush=True)
    yield


app = FastAPI(title="Teaching Platform API", version="1.0.0", lifespan=lifespan)

# Middleware order: the last added runs first. CORS wraps the LAN guard so even
# 403 responses carry CORS headers.
app.add_middleware(LanGuardMiddleware)
app.add_middleware(
    CORSMiddleware,
    # localhost + private LAN ranges (10.x, 172.16-31.x, 192.168.x) on any port.
    allow_origin_regex=(
        r"^https?://(localhost|127\.0\.0\.1|10(\.\d{1,3}){3}|192\.168(\.\d{1,3}){2}"
        r"|172\.(1[6-9]|2\d|3[01])(\.\d{1,3}){2})(:\d+)?$"
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(roster_router)
app.include_router(attendance_router)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/network")
def network(request: Request):
    info = get_network_info()
    client_ip = get_client_ip(request)
    return {
        "lan_ip": info.lan_ip,
        "subnet": str(info.subnet),
        "backend_port": config.PORT,
        "client_ip": client_ip,
        "is_lecturer_host": is_host_ip(client_ip),
        "lecturer_host_only": config.LECTURER_HOST_ONLY,
    }


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
