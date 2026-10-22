from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import update

from app.domain.enums import RunStatus
from app.infrastructure.persistence.models import Run
from app.infrastructure.persistence.repository import SqlAlchemyRepository
from app.infrastructure.rate_limit.postgres import (
    BUCKET_LIMITS,
    PostgresRateLimiter,
    RateLimitUnavailable,
)
from tests.conftest import create_test_user

pytestmark = pytest.mark.usefixtures("require_database")


async def _seed_run(session_factory):
    async with session_factory() as session:
        repo = SqlAlchemyRepository(session)
        user = await create_test_user(session, f"{uuid4().hex}@example.com")
        run = await repo.create_run(user.id, "build", None)
        return run.id


async def test_claim_pending_and_stale_running(session_factory) -> None:
    run_id = await _seed_run(session_factory)
    async with session_factory() as session:
        repo = SqlAlchemyRepository(session)
        claimed = await repo.claim("worker-a")
        assert claimed is not None and claimed.id == run_id
        assert claimed.status == RunStatus.RUNNING
        await repo.release(run_id)

    async with session_factory() as session:
        repo = SqlAlchemyRepository(session)
        assert await repo.claim("worker-b") is None

    async with session_factory() as session:
        await session.execute(
            update(Run)
            .where(Run.id == run_id)
            .values(heartbeat_at=datetime.now(UTC) - timedelta(seconds=120))
        )
        await session.commit()

    async with session_factory() as session:
        repo = SqlAlchemyRepository(session)
        claimed = await repo.claim("worker-c")
        assert claimed is not None and claimed.id == run_id


async def test_awaiting_user_is_not_claimable(session_factory) -> None:
    run_id = await _seed_run(session_factory)
    async with session_factory() as session:
        await session.execute(
            update(Run).where(Run.id == run_id).values(status=RunStatus.AWAITING_USER)
        )
        await session.commit()
    async with session_factory() as session:
        assert await SqlAlchemyRepository(session).claim("worker") is None


async def test_rate_limiter_counts_and_blocks(session_factory) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    limiter = PostgresRateLimiter(session_factory, "secret", limits={"auth": 2}, clock=lambda: now)
    first = await limiter.consume("auth", "1.2.3.4")
    second = await limiter.consume("auth", "1.2.3.4")
    third = await limiter.consume("auth", "1.2.3.4")
    assert first.allowed and second.allowed
    assert not third.allowed
    assert third.remaining == 0
    assert third.retry_after == 60

    other = await limiter.consume("auth", "5.6.7.8")
    assert other.allowed


async def test_rate_limiter_raises_when_store_is_down() -> None:
    class BrokenFactory:
        def __call__(self):
            raise RuntimeError("down")

    limiter = PostgresRateLimiter(BrokenFactory(), "secret")
    with pytest.raises(RateLimitUnavailable):
        await limiter.consume("auth", "ip")


def test_bucket_limits_match_contract() -> None:
    assert BUCKET_LIMITS == {
        "auth": 5,
        "run_create": 5,
        "write": 30,
        "read": 120,
        "preview_document": 60,
        "preview_asset": 600,
        "public": 30,
    }


def test_hmac_keys_are_scoped() -> None:
    class Factory:
        def __call__(self):
            raise AssertionError

    limiter = PostgresRateLimiter(Factory(), "secret")
    assert limiter._hash("auth", "ip") != limiter._hash("read", "ip")
    assert len(limiter._hash("auth", "ip")) == 32
