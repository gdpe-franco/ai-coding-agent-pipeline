from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request, Response
from sqlalchemy import text

from app.interfaces.schemas.models import HealthChecks, HealthRead

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthRead)
async def health(request: Request, response: Response) -> HealthRead:
    container = request.app.state.container
    database = "ok"
    try:
        async with container.session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        database = "unavailable"

    data_dir = container.settings.data_dir
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        filesystem = "ok" if os.access(data_dir, os.W_OK) else "read-only"
    except OSError:
        filesystem = "unavailable"

    worker = "idle"
    try:
        async with container.session_factory() as session:
            from app.infrastructure.persistence.repository import SqlAlchemyRepository

            latest = await SqlAlchemyRepository(session).latest_heartbeat()
        if latest is not None and datetime.now(UTC) - latest < timedelta(seconds=30):
            worker = "alive"
    except Exception:  # noqa: BLE001
        worker = "unknown"

    status = "ok" if database == "ok" and filesystem == "ok" else "degraded"
    if status != "ok":
        response.status_code = 503
    return HealthRead(
        status=status,
        database=database,
        filesystem=filesystem,
        worker=worker,
        checks=HealthChecks(database=database, filesystem=filesystem, worker=worker),
    )
