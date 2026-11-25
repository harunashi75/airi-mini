import asyncio
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import ExitStack, asynccontextmanager
import json
import logging
from pathlib import Path
import secrets
from threading import Event, Lock, Timer
import webbrowser

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError
import uvicorn
from core.backup import export_bundle, import_bundle, preview_bundle
from core.brain import Brain
from core.i18n import t
from core.settings import load_settings
from core.suggestions import MemorySuggestion
from core.storage import ROOT, DataDirectoryBusy, StorageError, lock_data_directory
from init_data import initialize_data

logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=32000)
    session_id: str
    replace_last: bool = False  # regenerate or edit the last exchange


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class ImportRequest(BaseModel):
    bundle: dict
    include_profile: bool = False
    dry_run: bool = False


class MemoryRequest(BaseModel):
    fact: str = Field(min_length=1, max_length=1000)


def public_error(exc):
    if isinstance(exc, KeyError):
        return 404, t("session.not_found")
    if isinstance(exc, (ValueError, ValidationError)):
        return 400, str(exc) if not isinstance(exc, ValidationError) else t("api.invalid_json")
    if isinstance(exc, StorageError):
        return 503, str(exc)
    return 500, t("api.failed")

MAX_PENDING = 8


class JobLimiter:
    """Bounded count of queued or running jobs; safe to release from any thread."""

    def __init__(self, limit=MAX_PENDING):
        self.limit = limit
        self._count = 0
        self._lock = Lock()

    @property
    def pending(self):
        with self._lock:
            return self._count

    def try_acquire(self):
        with self._lock:
            if self._count >= self.limit:
                return False
            self._count += 1
            return True

    def release(self, *_):
        with self._lock:
            self._count -= 1


def produce_stream(brain, message, session_id, emit, cancelled, replace_last=False):
    """Run in the inference worker: forward generated tokens as events."""
    if cancelled.is_set():
        return
    iterator = brain.stream(message, session_id, replace_last)
    try:
        for token in iterator:
            if cancelled.is_set():
                return
            if isinstance(token, MemorySuggestion):
                emit({"type": "suggestion", "fact": token.fact})
            else:
                emit({"type": "delta", "text": token})
        if not cancelled.is_set():
            emit({"type": "done"})
    except Exception as exc:
        code, detail = public_error(exc)
        if code >= 500:
            logger.exception("Échec de génération")
        if not cancelled.is_set():
            emit({"type": "error", "message": detail})
    finally:
        iterator.close()


def make_emitter(loop, queue, cancelled):
    """Thread-side emitter: blocks on a full queue, but gives up once the client is gone."""
    def emit(event):
        future = asyncio.run_coroutine_threadsafe(queue.put(event), loop)
        while not cancelled.is_set():
            try:
                future.result(timeout=0.2)
                return
            except FutureTimeout:
                continue
        future.cancel()
    return emit


async def ndjson_events(queue, cancelled, request):
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1)
            except asyncio.TimeoutError:
                if await request.is_disconnected():
                    break
                continue
            yield json.dumps(event, ensure_ascii=False) + "\n"
            if event["type"] in ("done", "error"):
                break
    finally:
        cancelled.set()


def create_app(brain_factory=None, *, web_root: Path = ROOT / "web", desktop_token=None, data_examples: Path | None = None):
    @asynccontextmanager
    async def lifespan(application):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        application.state.data_directory_busy = False
        with ExitStack() as resources:
            try:
                resources.enter_context(lock_data_directory())
            except DataDirectoryBusy:
                application.state.data_directory_busy = True
                raise
            if data_examples is not None:
                initialize_data(data_examples)
            settings = load_settings()
            executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="airi")
            resources.callback(executor.shutdown, wait=True, cancel_futures=True)
            application.state.executor = executor
            application.state.token = secrets.token_urlsafe(32)
            application.state.limiter = JobLimiter()
            application.state.ready = False
            application.state.loop = asyncio.get_running_loop()
            brain = await application.state.loop.run_in_executor(executor, brain_factory or Brain)
            application.state.brain = brain
            application.state.ready = True
            try:
                if getattr(application.state, "launch_browser", False) and settings.open_browser:
                    Timer(0.5, lambda: webbrowser.open(f"http://127.0.0.1:{settings.port}")).start()
                yield
            finally:
                application.state.ready = False
                await application.state.loop.run_in_executor(executor, brain.close)

    application = FastAPI(title="AIRI Mini", lifespan=lifespan)

    @application.middleware("http")
    async def protect_local_api(request: Request, call_next):
        # Reject foreign Host headers and writes from other websites, including DNS rebinding.
        host = request.url.hostname
        if host not in ("127.0.0.1", "localhost", "::1"):
            return JSONResponse({"detail": t("api.bad_host")}, status_code=403)
        if desktop_token and not secrets.compare_digest(
            request.headers.get("X-Airi-Desktop", "").encode(), desktop_token.encode()
        ):
            return JSONResponse({"detail": t("api.desktop_only")}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            token = getattr(application.state, "token", "")
            if not token or not secrets.compare_digest(request.headers.get("X-Airi-Token", "").encode(), token.encode()):
                return JSONResponse({"detail": t("api.reload")}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    async def run_job(function, *args):
        if not application.state.ready:
            raise HTTPException(503, t("api.not_ready"))
        limiter = application.state.limiter
        if not limiter.try_acquire():
            raise HTTPException(429, t("api.busy"))
        try:
            future = application.state.loop.run_in_executor(application.state.executor, function, *args)
        except BaseException:
            limiter.release()
            raise
        future.add_done_callback(limiter.release)
        try:
            return await asyncio.shield(future)
        except Exception as exc:
            code, detail = public_error(exc)
            if code >= 500:
                logger.exception("Échec d'une opération AIRI")
            raise HTTPException(code, detail) from exc

    @application.get("/", response_class=HTMLResponse)
    def index():
        return (web_root / "index.html").read_text(encoding="utf-8")

    @application.get("/assets/{name}")
    def asset(name: str):
        if name == "logo.svg":
            return FileResponse(web_root.parent / "desktop" / name, media_type="image/svg+xml")
        if name not in ("app.js", "style.css", "menus.js", "menus.css"):
            raise HTTPException(404)
        return FileResponse(web_root / name)

    @application.get("/api/health")
    async def health():
        return {"ready": application.state.ready, "pending": application.state.limiter.pending}

    @application.get("/api/bootstrap")
    async def bootstrap():
        brain = application.state.brain
        return {"token": application.state.token, "max_message_chars": brain.settings.max_message_chars,
                "user_name": brain.user_name, "assistant_name": brain.assistant_name, "language": brain.settings.language}

    @application.get("/api/sessions")
    async def sessions():
        return await run_job(application.state.brain.sessions.list)

    @application.post("/api/sessions")
    async def new_session():
        session = await run_job(application.state.brain.sessions.create)
        return session.model_dump()

    @application.get("/api/sessions/search")
    async def search_sessions(q: str = ""):
        return await run_job(application.state.brain.sessions.search, q[:200])

    @application.get("/api/sessions/{session_id}")
    async def get_session(session_id: str):
        session = await run_job(application.state.brain.sessions.get, session_id)
        return session.model_dump()

    @application.patch("/api/sessions/{session_id}")
    async def rename_session(session_id: str, req: RenameRequest):
        session = await run_job(application.state.brain.sessions.rename, session_id, req.title)
        return {"id": session.id, "title": session.title, "updated_at": session.updated_at}

    @application.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str):
        await run_job(application.state.brain.sessions.delete, session_id)
        return {"ok": True}

    @application.get("/api/data/export")
    async def export_data():
        return await run_job(export_bundle, application.state.brain)

    @application.post("/api/data/import")
    async def import_data(req: ImportRequest):
        if req.dry_run:
            return await run_job(preview_bundle, application.state.brain, req.bundle)
        return await run_job(import_bundle, application.state.brain, req.bundle, req.include_profile)

    @application.get("/api/memory")
    async def get_memory():
        return await run_job(lambda: {"facts": list(application.state.brain.memory_manager.long_term)})

    @application.post("/api/memory")
    async def add_memory(req: MemoryRequest):
        await run_job(application.state.brain.memory_manager.add_fact, req.fact)
        return {"ok": True}

    @application.delete("/api/memory")
    async def remove_memory(req: MemoryRequest):
        return {"removed": await run_job(application.state.brain.memory_manager.remove_fact, req.fact)}

    @application.post("/api/chat")
    async def chat(req: ChatRequest):
        response = await run_job(application.state.brain.think, req.message, req.session_id, req.replace_last)
        return {"response": response}

    @application.post("/api/chat/stream")
    async def stream_chat(req: ChatRequest, request: Request):
        if not application.state.ready:
            raise HTTPException(503, t("api.not_ready"))
        limiter = application.state.limiter
        if not limiter.try_acquire():
            raise HTTPException(429, t("api.queue_full"))
        loop = application.state.loop
        queue = asyncio.Queue(maxsize=32)
        cancelled = Event()
        try:
            future = loop.run_in_executor(
                application.state.executor, produce_stream, application.state.brain,
                req.message, req.session_id, make_emitter(loop, queue, cancelled), cancelled, req.replace_last)
        except BaseException:
            limiter.release()
            raise
        future.add_done_callback(limiter.release)
        return StreamingResponse(ndjson_events(queue, cancelled, request),
                                 media_type="application/x-ndjson", headers={"X-Accel-Buffering": "no"})

    return application


app = create_app()

if __name__ == "__main__":
    settings = load_settings()
    app.state.launch_browser = True
    uvicorn.run(app, host="127.0.0.1", port=settings.port, log_level="info")
