"""Starlette application entry point.

Run: ``.venv/bin/python -m app.server`` (http://127.0.0.1:8765).
"""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import api_v3, config, db, onboarding, worker
from .errors import AppError

log = logging.getLogger(__name__)


async def index(request) -> Response:
    path = config.WEB_DIR / "index.html"
    if not path.exists():
        return Response("web/index.html 不存在，请先构建前端", status_code=500)
    return FileResponse(path, media_type="text/html",
                        headers={"Cache-Control": "no-cache"})


async def favicon(request) -> Response:
    return Response(status_code=204)


class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    """Always revalidate frontend assets so a refresh loads the latest UI."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith(("/js/", "/css/", "/assets/")):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response


def _error_response(exc: AppError) -> JSONResponse:
    return JSONResponse(exc.as_dict(), status_code=exc.status_code)


async def app_error_handler(request, exc: AppError) -> JSONResponse:
    return _error_response(exc)


async def generic_error_handler(request, exc: Exception) -> JSONResponse:
    log.exception("unhandled API error")
    return JSONResponse({"error": {"code": "internal_error",
                                   "message": f"{type(exc).__name__}: {exc}"}},
                        status_code=500)


@contextlib.asynccontextmanager
async def lifespan(app):
    db.init_db()
    config.ensure_dirs()
    onboarding.ensure_guide()
    started = False
    disable = os.environ.get("SKILLOPT_DISABLE_WORKER", "").lower() in ("1", "true", "yes")
    if not disable:
        persistence_recover()
        worker.start_worker()
        started = True
        log.info("worker started")
    try:
        yield
    finally:
        if started:
            worker.stop_worker()
            log.info("worker stopped")


def persistence_recover() -> None:
    try:
        recovered = db_recovery()
    except Exception:
        log.exception("stale run recovery failed")
        return
    if recovered:
        log.warning("recovered %s stale runs", recovered)


def db_recovery() -> int:
    from . import persistence

    return len(persistence.recover_stale_runs())


def create_app(*, start_worker: bool | None = None) -> Starlette:
    routes = list(api_v3.build_routes())
    routes.extend([
        Route("/", index, methods=["GET"]),
        Route("/index.html", index, methods=["GET"]),
        Route("/favicon.ico", favicon, methods=["GET"]),
        Mount("/css", StaticFiles(directory=str(config.WEB_DIR / "css")), name="css"),
        Mount("/js", StaticFiles(directory=str(config.WEB_DIR / "js")), name="js"),
    ])
    if (config.WEB_DIR / "assets").exists():
        routes.append(Mount("/assets", StaticFiles(directory=str(config.WEB_DIR / "assets")),
                            name="assets"))
    app = Starlette(routes=routes, lifespan=lifespan,
                    middleware=[Middleware(NoCacheStaticMiddleware)],
                    exception_handlers={AppError: app_error_handler,
                                        Exception: generic_error_handler})
    app.state.worker_disabled = (start_worker is False
                                 or os.environ.get("SKILLOPT_DISABLE_WORKER", "")
                                 .lower() in ("1", "true", "yes"))
    return app


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("SKILLOPT_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    import uvicorn

    app = create_app()
    print(f"Skill 优化平台: http://{config.HOST}:{config.PORT}")
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
