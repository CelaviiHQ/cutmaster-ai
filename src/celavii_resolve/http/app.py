"""FastAPI app for the CutMaster panel.

Binds to ``127.0.0.1`` only. The React panel (running inside Resolve's
Workflow Integration webview) fetches from this server.

Phase 2 scope: ``/ping`` health check + CORS for the embedded browser.
Cutmaster routes arrive in Phase 3.
"""

from __future__ import annotations

import logging
import os

try:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
except ImportError as exc:
    raise ImportError(
        "celavii-resolve-panel requires FastAPI. Install with: pip install 'celavii-resolve[panel]'"
    ) from exc

from .. import __version__
from .routes import cutmaster as cutmaster_routes

log = logging.getLogger("celavii-resolve-panel")

# Directory for the built React bundle (copied in by the panel build step).
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def create_app() -> FastAPI:
    """Build the FastAPI app. Factory form so tests can instantiate it directly."""
    app = FastAPI(
        title="celavii-resolve panel",
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

    @app.get("/ping")
    def ping() -> dict:
        return {"ok": True, "service": "celavii-resolve-panel", "version": __version__}

    app.include_router(cutmaster_routes.router)

    # Serve the React bundle if it's been built. Panel build step copies
    # apps/panel/dist/ → src/celavii_resolve/http/static/.
    if os.path.isdir(STATIC_DIR) and os.listdir(STATIC_DIR):
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="panel")

    return app


def main() -> None:
    """Entry point for the ``celavii-resolve-panel`` console script."""
    try:
        import uvicorn
    except ImportError as exc:
        raise ImportError(
            "celavii-resolve-panel requires uvicorn. "
            "Install with: pip install 'celavii-resolve[panel]'"
        ) from exc

    host = os.environ.get("CELAVII_PANEL_HOST", "127.0.0.1")
    port = int(os.environ.get("CELAVII_PANEL_PORT", "8765"))

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
    log.info("Starting celavii-resolve-panel on http://%s:%d", host, port)

    uvicorn.run(
        "celavii_resolve.http.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
