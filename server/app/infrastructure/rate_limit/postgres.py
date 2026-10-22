from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ports import RateLimitResult
from app.infrastructure.persistence.models import RateLimitCounter

BUCKET_LIMITS: dict[str, int] = {
    "auth": 5,
    "run_create": 5,
    "write": 30,
    "read": 120,
    "preview_document": 60,
    "preview_asset": 600,
    "public": 30,
}
WINDOW_SECONDS = 60


class RateLimitUnavailable(RuntimeError):
    pass


class PostgresRateLimiter:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        secret_key: str,
        limits: dict[str, int] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.secret_key = secret_key.encode("utf-8")
        self.limits = limits or BUCKET_LIMITS
        self.clock = clock or (lambda: datetime.now(UTC))

    def _hash(self, bucket: str, key: str) -> bytes:
        return hmac.new(self.secret_key, f"{bucket}:{key}".encode(), hashlib.sha256).digest()

    async def consume(self, bucket: str, key: str) -> RateLimitResult:
        limit = self.limits.get(bucket, self.limits.get("public", 30))
        now = self.clock()
        window_start = now.replace(second=0, microsecond=0)
        reset_at = window_start + timedelta(seconds=WINDOW_SECONDS)
        expires_at = reset_at + timedelta(minutes=5)
        key_hash = self._hash(bucket, key)
        try:
            async with self.session_factory() as session:
                stmt = (
                    pg_insert(RateLimitCounter)
                    .values(
                        key_hash=key_hash,
                        window_start=window_start,
                        count=1,
                        expires_at=expires_at,
                    )
                    .on_conflict_do_update(
                        index_elements=[
                            RateLimitCounter.key_hash,
                            RateLimitCounter.window_start,
                        ],
                        set_={
                            "count": RateLimitCounter.count + 1,
                            "expires_at": expires_at,
                        },
                    )
                    .returning(RateLimitCounter.count)
                )
                count = (await session.execute(stmt)).scalar_one()
                await session.commit()
        except Exception as exc:
            raise RateLimitUnavailable("rate limiter store is unavailable") from exc

        remaining = max(0, limit - int(count))
        retry_after = max(1, int((reset_at - now).total_seconds()))
        return RateLimitResult(
            allowed=int(count) <= limit,
            limit=limit,
            remaining=remaining,
            reset_at=reset_at,
            retry_after=retry_after,
        )
