from __future__ import annotations

import pytest

from app.application.processor import RunProcessor
from app.domain.enums import ConversationSender, RunStatus
from app.infrastructure.persistence.repository import SqlAlchemyRepository
from tests.conftest import create_test_user

pytestmark = pytest.mark.usefixtures("require_database")


class EmptyWorkspace:
    async def read_all(self, project_id):
        return {}

    async def publish(self, project_id, files, artifact_ids):
        pass


class EmptyMemory:
    async def save_round(self, *args, **kwargs):
        pass


@pytest.fixture
async def processor_context(session_factory, repo_factory, settings):
    async with session_factory() as session:
        user = await create_test_user(session, "chat-results@example.com")
        await session.commit()
    processor = RunProcessor(
        repo_factory=repo_factory,
        graph=None,
        workspace=EmptyWorkspace(),
        memory=EmptyMemory(),
        settings=settings,
        worker_id="test-worker",
    )
    return session_factory, processor, user


@pytest.mark.parametrize(
    ("result", "expected_status", "expected_reply"),
    [
        pytest.param(
            {
                "approved": True,
                "handoff": False,
                "attempts_used": 1,
                "feedback": {"summary": "Looks good.", "issues": []},
            },
            RunStatus.COMPLETED,
            "Approved. Generated 0 files. Looks good.",
            id="approved",
        ),
        pytest.param(
            {
                "approved": False,
                "handoff": True,
                "attempts_used": 3,
                "feedback": {"summary": "The reviewer needs more detail.", "issues": []},
            },
            RunStatus.AWAITING_USER,
            "This run needs your guidance. The reviewer needs more detail.",
            id="handoff",
        ),
    ],
)
async def test_finalize_outcome_is_saved_and_not_overwritten_by_late_failure(
    processor_context, result, expected_status, expected_reply
) -> None:
    session_factory, processor, user = processor_context
    async with session_factory() as session:
        repo = SqlAlchemyRepository(session)
        project = await repo.create_project(user.id, "Result project")
        run = await repo.create_run(user.id, "request", project.id)
        await repo.update_run(run.id, status=RunStatus.RUNNING)
    await processor._finalize(
        run.id,
        {
            "project_id": str(project.id),
            **result,
            "attempts_total": 3,
            "code": [],
            "checks": {},
        },
    )
    await processor._fail(run.id, RuntimeError("late worker failure"))

    async with session_factory() as session:
        repo = SqlAlchemyRepository(session)
        turns = await repo.list_conversation_turns(user.id)
        saved_run = await repo.get_run_model(run.id)
    assert [(turn.role, turn.content) for turn in turns] == [
        (ConversationSender.USER, "request"),
        (ConversationSender.ASSISTANT, expected_reply),
    ]
    assert [turn.run_id for turn in turns] == [run.id, run.id]
    assert all(turn.project_id == project.id for turn in turns)
    assert saved_run.status == expected_status


async def test_failure_is_saved_as_assistant_turn(processor_context) -> None:
    session_factory, processor, user = processor_context
    async with session_factory() as session:
        repo = SqlAlchemyRepository(session)
        run = await repo.create_run(user.id, "request", None)
        await repo.update_run(run.id, status=RunStatus.RUNNING)
    await processor._fail(run.id, RuntimeError("provider unavailable"))

    async with session_factory() as session:
        repo = SqlAlchemyRepository(session)
        turns = await repo.list_conversation_turns(user.id)
        saved_run = await repo.get_run_model(run.id)
    assert [(turn.role, turn.content) for turn in turns] == [
        (ConversationSender.USER, "request"),
        (ConversationSender.ASSISTANT, "RuntimeError: provider unavailable"),
    ]
    assert [turn.run_id for turn in turns] == [run.id, run.id]
    assert saved_run.status == RunStatus.FAILED
