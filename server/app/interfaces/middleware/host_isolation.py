from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class HostIsolationMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, app_hosts: set[str], preview_host: str) -> None:
        super().__init__(app)
        self.app_hosts = app_hosts
        self.preview_host = preview_host

    async def dispatch(self, request: Request, call_next):
        host = (request.url.hostname or "").rstrip(".").lower()
        path = request.url.path
        is_preview = path == "/preview" or path.startswith("/preview/")

        # Docker's local health probe uses loopback rather than a public hostname.
        if path == "/health" and host in {"127.0.0.1", "::1", "localhost"}:
            return await call_next(request)
        if host == self.preview_host:
            allowed = is_preview
        else:
            allowed = host in self.app_hosts and not is_preview
        if not allowed:
            return JSONResponse(
                status_code=404,
                media_type="application/problem+json",
                content={"type": "about:blank", "title": "Not found", "status": 404},
            )
        return await call_next(request)
