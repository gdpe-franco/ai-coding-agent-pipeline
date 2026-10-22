from __future__ import annotations

import hashlib
import ipaddress

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.infrastructure.rate_limit.postgres import RateLimitUnavailable


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, container) -> None:
        super().__init__(app)
        self.container = container

    async def dispatch(self, request: Request, call_next):
        if not self.container.settings.rate_limit_enabled:
            return await call_next(request)
        bucket = self._bucket(request)
        if bucket is None:
            return await call_next(request)
        key = self._key(request, bucket)
        try:
            result = await self.container.rate_limiter.consume(bucket, key)
        except RateLimitUnavailable:
            return JSONResponse(
                status_code=503,
                media_type="application/problem+json",
                content={
                    "type": "about:blank",
                    "title": "Rate limiter unavailable",
                    "status": 503,
                    "detail": "Rate limiting is temporarily unavailable.",
                },
            )
        if not result.allowed:
            return JSONResponse(
                status_code=429,
                media_type="application/problem+json",
                headers={
                    "Retry-After": str(result.retry_after),
                    "RateLimit-Limit": str(result.limit),
                    "RateLimit-Remaining": "0",
                    "RateLimit-Reset": str(result.retry_after),
                },
                content={
                    "type": "about:blank",
                    "title": "Too many requests",
                    "status": 429,
                    "detail": "Rate limit exceeded.",
                },
            )
        response = await call_next(request)
        response.headers["RateLimit-Limit"] = str(result.limit)
        response.headers["RateLimit-Remaining"] = str(result.remaining)
        response.headers["RateLimit-Reset"] = str(result.retry_after)
        return response

    def _bucket(self, request: Request) -> str | None:
        path = request.url.path
        if path == "/health" or not path.startswith(("/api/", "/preview/")):
            return None
        if path.startswith("/preview/"):
            suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
            return (
                "preview_asset"
                if suffix in {"css", "js", "png", "ico", "svg"}
                else "preview_document"
            )
        if path.startswith("/api/v1/auth"):
            return "auth"
        if path in {"/api/v1/runs", "/api/v1/chat/messages"} and request.method == "POST":
            return "run_create"
        if request.method in {"GET", "HEAD"}:
            return "read"
        return "write"

    def _key(self, request: Request, bucket: str) -> str:
        if bucket in {"auth", "public", "preview_document", "preview_asset"}:
            return self._client_ip(request)
        authorization = request.headers.get("authorization", "")
        if authorization:
            return "token:" + hashlib.sha256(authorization.encode()).hexdigest()
        return self._client_ip(request)

    def _client_ip(self, request: Request) -> str:
        peer = request.client.host if request.client else "0.0.0.0"
        networks = self.container.settings.trusted_proxy_networks
        if networks and self._trusted(peer, networks):
            forwarded = request.headers.get("cf-connecting-ip") or request.headers.get(
                "x-forwarded-for"
            )
            if forwarded:
                return forwarded.split(",")[0].strip()
        return peer

    @staticmethod
    def _trusted(peer: str, networks: list[str]) -> bool:
        try:
            address = ipaddress.ip_address(peer)
        except ValueError:
            return False
        for network in networks:
            try:
                if address in ipaddress.ip_network(network, strict=False):
                    return True
            except ValueError:
                continue
        return False
