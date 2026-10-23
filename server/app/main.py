from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.infrastructure.container import Container
from app.interfaces.api import chats, health, preview, projects, runs
from app.interfaces.dependencies import build_fastapi_users
from app.interfaces.middleware.rate_limit import RateLimitMiddleware
from app.interfaces.schemas.users import UserCreate, UserRead, UserUpdate
from app.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    container = Container(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await container.close()

    app = FastAPI(title="AI Coding Agent Pipeline", version="0.1.0", lifespan=lifespan)
    app.state.container = container

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin, settings.public_url],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RateLimitMiddleware, container=container)

    fastapi_users, auth_backend = build_fastapi_users(settings)
    app.include_router(
        fastapi_users.get_auth_router(auth_backend), prefix="/api/v1/auth", tags=["auth"]
    )
    app.include_router(
        fastapi_users.get_register_router(UserRead, UserCreate),
        prefix="/api/v1/auth",
        tags=["auth"],
    )
    app.include_router(
        fastapi_users.get_reset_password_router(), prefix="/api/v1/auth", tags=["auth"]
    )
    app.include_router(
        fastapi_users.get_verify_router(UserRead), prefix="/api/v1/auth", tags=["auth"]
    )
    app.include_router(
        fastapi_users.get_users_router(UserRead, UserUpdate), prefix="/api/v1/users", tags=["users"]
    )

    app.include_router(health.router)
    app.include_router(preview.router)
    app.include_router(chats.router, prefix="/api/v1")
    app.include_router(projects.router, prefix="/api/v1")
    app.include_router(runs.router, prefix="/api/v1")

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return JSONResponse(
            status_code=exc.status_code,
            media_type="application/problem+json",
            headers=exc.headers,
            content={
                "type": "about:blank",
                "title": detail,
                "status": exc.status_code,
                "detail": detail,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            media_type="application/problem+json",
            content={
                "type": "about:blank",
                "title": "Validation failed",
                "status": 422,
                "detail": exc.errors(),
            },
        )

    if settings.static_dir.exists() and (settings.static_dir / "index.html").exists():
        static_root = settings.static_dir.resolve()

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str) -> FileResponse:
            if full_path.startswith(("api/", "preview/")):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
            candidate = (static_root / full_path).resolve()
            if full_path and static_root in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(static_root / "index.html")

    return app
