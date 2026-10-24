from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.domain.enums import RunStatus
from app.domain.errors import RunConflictError
from app.infrastructure.persistence.models import Run
from app.infrastructure.persistence.repository import SqlAlchemyRepository
from tests.conftest import create_test_chat, create_test_user

pytestmark = pytest.mark.usefixtures("require_database")


@pytest.mark.parametrize("limit", [1, 2, 10])
async def test_project_run_limit_allows_the_cap_and_rejects_the_next_run(
    session_factory, limit: int
) -> None:
    async with session_factory() as session:
        owner = await create_test_user(session, f"run-limit-{limit}@example.com")
        chat, project, run, _ = await create_test_chat(session, owner.id, "First request")
        repo = SqlAlchemyRepository(session)

        for number in range(1, limit):
            await repo.update_run(run.id, status=RunStatus.COMPLETED)
            message, run, created = await repo.create_chat_message(
                owner.id,
                chat.id,
                uuid4(),
                f"Request {number + 1}",
                max_runs_per_project=limit,
            )
            assert created
            assert message.run_id == run.id

        await repo.update_run(run.id, status=RunStatus.COMPLETED)
        with pytest.raises(RunConflictError, match="reached its run limit"):
            await repo.create_chat_message(
                owner.id,
                chat.id,
                uuid4(),
                "Beyond the cap",
                max_runs_per_project=limit,
            )

        count = await session.scalar(
            select(func.count()).select_from(Run).where(Run.chat_id == chat.id)
        )

    assert project.id == chat.project_id
    assert count == limit
