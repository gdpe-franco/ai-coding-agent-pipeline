from __future__ import annotations

from uuid import uuid4

import pytest

from app.domain.enums import RunStatus
from app.domain.models import initial_chat_title
from app.infrastructure.persistence.repository import SqlAlchemyRepository
from tests.conftest import create_test_user

pytestmark = pytest.mark.usefixtures("require_database")


async def test_planner_title_updates_only_the_untouched_initial_chat(session_factory) -> None:
    message = "Can you make a coupon site that checks a code?"
    async with session_factory() as session:
        owner = await create_test_user(session, "auto-title@example.com")
        repo = SqlAlchemyRepository(session)
        chat, project, initial_run, _ = await repo.create_chat(owner.id, uuid4(), message)
        assert project.title == initial_chat_title(message)

        assert await repo.suggest_initial_chat_title(
            owner.id, chat.id, initial_run.id, "Coupon Validator"
        )
        assert project.title == "Coupon Validator"

        await repo.update_run(initial_run.id, status=RunStatus.COMPLETED)
        later_message, later_run, _ = await repo.create_chat_message(
            owner.id, chat.id, uuid4(), "Add expiry dates", max_runs_per_project=100
        )
        assert later_message.run_id == later_run.id
        assert not await repo.suggest_initial_chat_title(
            owner.id, chat.id, later_run.id, "Expiry Date Manager"
        )
        assert project.title == "Coupon Validator"


async def test_manual_rename_wins_if_planner_finishes_later(session_factory) -> None:
    message = "Build a coupon validator"
    async with session_factory() as session:
        owner = await create_test_user(session, "manual-title@example.com")
        repo = SqlAlchemyRepository(session)
        chat, project, run, _ = await repo.create_chat(owner.id, uuid4(), message)

        await repo.rename_chat(owner.id, chat.id, "My coupon project")

        assert not await repo.suggest_initial_chat_title(
            owner.id, chat.id, run.id, "Coupon Validator"
        )
        assert project.title == "My coupon project"
