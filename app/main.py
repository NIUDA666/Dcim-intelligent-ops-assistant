"""FastAPI 入口。

运行：
    python scripts/publish_configs.py
    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.container import AppContainer, build_container
from app.services.import_service import ImportValidationError
from app.services.query_service import QueryServiceError
from app.settings import settings


def create_app(container: AppContainer | None = None) -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description="配置驱动取证的监控问数与告警辅助分析演示服务",
        version="1.3.1",
    )
    app.state.container = container or build_container()
    app.include_router(router)

    @app.exception_handler(QueryServiceError)
    async def _query_error(_: Request, exc: QueryServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": str(exc)}},
        )

    @app.exception_handler(ImportValidationError)
    async def _import_error(_: Request, exc: ImportValidationError) -> JSONResponse:
        status_code = 404 if exc.code == "IMPORT_TASK_NOT_FOUND" else 422
        return JSONResponse(
            status_code=status_code,
            content={"error": {"code": exc.code, "message": str(exc)}},
        )

    frontend = Path(settings.frontend_dir)
    if frontend.is_dir():
        app.mount("/static", StaticFiles(directory=frontend), name="static")

        @app.get("/", include_in_schema=False)
        async def dcim_host() -> FileResponse:
            return FileResponse(frontend / "dcim.html")

        @app.get("/dcim", include_in_schema=False)
        async def dcim_page() -> FileResponse:
            return FileResponse(frontend / "dcim.html")

        @app.get("/debug", include_in_schema=False)
        async def debug_console() -> FileResponse:
            return FileResponse(frontend / "index.html")

    return app


app = create_app()
