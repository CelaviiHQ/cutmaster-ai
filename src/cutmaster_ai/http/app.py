"""FastAPI app for the CutMaster panel.

Binds to ``127.0.0.1`` only. The React panel (running inside Resolve's
Workflow Integration webview) fetches from this server.

Phase 2 scope: ``/ping`` health check + CORS for the embedded browser.
Cutmaster routes arrive in Phase 3.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import time

try:
    from fastapi import FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
except ImportError as exc:
    raise ImportError(
        "cutmaster-ai-panel requires FastAPI. Install with: pip install 'cutmaster-ai[panel]'"
    ) from exc

from .. import __version__
from ..licensing import current_tier
from ..logging_setup import configure_logging
from ..migrations.runner import apply_migrations
from ..plugins import discover_panel_routes, registered_plugins
from .routes import cutmaster as cutmaster_routes

log = logging.getLogger("cutmaster-ai-panel")

# Directory for the built React bundle (copied in by the panel build step).
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def create_app() -> FastAPI:
    """Build the FastAPI app. Factory form so tests can instantiate it directly."""
    app = FastAPI(
        title="cutmaster-ai panel",
        version=__version__,
        docs_url="/_docs",
        redoc_url=None,
    )

    # CORS — Resolve's embedded webview can load panels with a null or
    # file:// origin. Since we're bound to 127.0.0.1 only, allow-all is safe.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    access_log = logging.getLogger("cutmaster-ai.http.access")

    @app.middleware("http")
    async def access_log_middleware(request: Request, call_next):
        """Emit one structured record per HTTP request.

        Stays lightweight: method, path, status, duration_ms. The run_id
        ContextVar (if set by the route handler) is attached by the
        logging filter automatically.
        """
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = int((time.perf_counter() - start) * 1000)
        access_log.info(
            "%s %s → %d",
            request.method,
            request.url.path,
            response.status_code,
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response

    @app.get("/ping")
    def ping() -> dict:
        return {"ok": True, "service": "cutmaster-ai-panel", "version": __version__}

    @app.get("/pro/status")
    def pro_status() -> dict:
        return {"tier": current_tier(), "plugins": registered_plugins()}

    app.include_router(cutmaster_routes.router)

    # Panel-route plugins register last so they can include_router their
    # own prefixed routers without colliding with OSS endpoints.
    discover_panel_routes(app)

    # Serve the React bundle if it's been built. Panel build step copies
    # apps/panel/dist/ → src/cutmaster_ai/http/static/.
    if os.path.isdir(STATIC_DIR) and os.listdir(STATIC_DIR):
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="panel")

    return app


def _pick_free_port() -> int:
    """Ask the kernel for a free ephemeral port and release it immediately."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _default_state_db_path() -> str:
    """Default Panel SQLite location. Overridden by ``CUTMASTER_PANEL_DB``."""
    home = os.path.expanduser("~")
    return os.path.join(home, ".cutmaster", "panel", "state.db")


def main() -> None:
    """Entry point for the ``cutmaster-ai-panel`` console script.

    Prints ``PANEL_READY http://host:port`` as the first stdout line so
    supervisors (e.g. the Studio Swift shell) can discover a randomly
    assigned port. Set ``CUTMASTER_PANEL_PORT=0`` to force random allocation.
    """
    try:
        import uvicorn
    except ImportError as exc:
        raise ImportError(
            "cutmaster-ai-panel requires uvicorn. Install with: pip install 'cutmaster-ai[panel]'"
        ) from exc

    host = os.environ.get("CUTMASTER_PANEL_HOST", "127.0.0.1")
    port = int(os.environ.get("CUTMASTER_PANEL_PORT", "8765"))
    if port == 0:
        port = _pick_free_port()

    # Stdout must lead with PANEL_READY so a supervisor parsing line-by-line
    # can capture the URL before any other output. Configure logging after.
    print(f"PANEL_READY http://{host}:{port}", flush=True)
    sys.stdout.flush()

    configure_logging()
    log.info("Starting cutmaster-ai-panel on http://%s:%d", host, port)

    db_path = os.environ.get("CUTMASTER_PANEL_DB", _default_state_db_path())
    try:
        applied = apply_migrations(db_path)
        if applied:
            log.info("Applied %d migration(s): %s", len(applied), ", ".join(applied))
    except Exception:
        log.exception("Panel state migrations failed at %s", db_path)

    uvicorn.run(
        "cutmaster_ai.http.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
