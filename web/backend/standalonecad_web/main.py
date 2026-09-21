from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .auth import websocket_user_id
from .cad_runtime import runtime_manager
from .config import settings
from .database import SessionLocal, init_database
from .models import User
from .providers.app_server import app_server_manager
from .routes import ai, auth, cad, manufacturing, mcp, projects
from .routes.projects import owned_project
from .schemas import PromptRequest


async def idle_reaper() -> None:
    while True:
        await asyncio.sleep(60)
        await asyncio.to_thread(runtime_manager.evict_idle)
        await asyncio.to_thread(app_server_manager.evict_idle)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database()
    task = asyncio.create_task(idle_reaper())
    yield
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.to_thread(runtime_manager.close_all)
    await asyncio.to_thread(app_server_manager.close_all)


app = FastAPI(title="CADia Web", version=__version__, lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; object-src 'none'; "
        "img-src 'self' data: blob:; font-src 'self'; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self' ws: wss:",
    )
    if request.url.scheme == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.url.path.startswith("/assets/"):
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
    return response
app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(projects.router, prefix=settings.api_prefix)
app.include_router(cad.router, prefix=settings.api_prefix)
app.include_router(manufacturing.router, prefix=settings.api_prefix)
app.include_router(ai.router, prefix=settings.api_prefix)
app.include_router(mcp.router, prefix=settings.api_prefix)


@app.get(f"{settings.api_prefix}/health")
def health():
    return {"ok": True, "service": "cadia-web", "version": __version__, "cad_kernel": "CadQuery/OCP", "manufacturing_api": True}


@app.websocket(f"{settings.api_prefix}/ws/projects/{{project_id}}")
async def project_socket(websocket: WebSocket, project_id: str):
    try:
        origin = websocket.headers.get("origin")
        if origin and origin not in settings.cors_origins:
            raise RuntimeError("WebSocket origin is not allowed.")
        user_id = websocket_user_id(dict(websocket.cookies), websocket.headers.get("authorization"))
        with SessionLocal() as db:
            if db.get(User, user_id) is None:
                raise RuntimeError("Sign-in is required.")
            owned_project(db, user_id, project_id)
    except Exception:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    send_lock = asyncio.Lock()
    active_task: asyncio.Task | None = None

    async def send(payload):
        async with send_lock:
            await websocket.send_json(payload)

    async def run_prompt(payload: PromptRequest):
        loop = asyncio.get_running_loop()
        progress_queue: asyncio.Queue = asyncio.Queue()

        def on_event(kind: str, value):
            loop.call_soon_threadsafe(progress_queue.put_nowait, {"type": kind, "payload": value})

        async def pump_progress():
            while True:
                item = await progress_queue.get()
                await send(item)

        pump = asyncio.create_task(pump_progress())
        try:
            await send({"type": "busy", "payload": True})
            result = await asyncio.to_thread(ai.execute_prompt_sync, user_id, project_id, payload, on_event)
            await send({"type": "result", "payload": result})
            runtime = runtime_manager.get(user_id, project_id)
            mesh = await asyncio.to_thread(runtime.mesh, True)
            await send({"type": "mesh", "payload": mesh})
        except Exception as exc:
            await send({"type": "error", "payload": str(exc)})
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            await send({"type": "busy", "payload": False})

    try:
        runtime = await asyncio.to_thread(runtime_manager.get, user_id, project_id)
        await send({"type": "state", "payload": runtime.state()})
        await send({"type": "mesh", "payload": await asyncio.to_thread(runtime.mesh, True)})
        while True:
            message = await websocket.receive_json()
            kind = message.get("type")
            if kind == "prompt":
                if active_task and not active_task.done():
                    await send({"type": "error", "payload": "A modeling task is already running."})
                    continue
                try:
                    payload = PromptRequest.model_validate(message.get("payload") or {})
                except Exception as exc:
                    await send({"type": "error", "payload": str(exc)})
                    continue
                active_task = asyncio.create_task(run_prompt(payload))
            elif kind == "cancel":
                await asyncio.to_thread(ai.cancel_prompt, user_id, project_id)
            elif kind == "refresh":
                await send({"type": "state", "payload": runtime.state()})
                await send({"type": "mesh", "payload": await asyncio.to_thread(runtime.mesh, True)})
            elif kind == "ping":
                await send({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        if active_task and not active_task.done():
            await asyncio.to_thread(ai.cancel_prompt, user_id, project_id)
            try:
                await asyncio.wait_for(active_task, timeout=5)
            except Exception:
                active_task.cancel()


frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.exists():
    assets = frontend_dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        requested = (frontend_dist / path).resolve()
        if frontend_dist.resolve() in requested.parents and requested.is_file():
            return FileResponse(requested)
        return FileResponse(frontend_dist / "index.html")
