from __future__ import annotations

import pytest

from app.application.processor import RunProcessor
from app.domain.enums import ConversationSender, EventKind, RunStatus
from app.infrastructure.persistence.repository import SqlAlchemyRepository
from tests.conftest import create_test_user

pytestmark = pytest.mark.usefixtures("require_database")


class EmptyWorkspace:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.artifacts: dict[str, str] = {}
        self.preview: dict[str, str] = {}

    async def read_all(self, project_id):
        return self.files

    async def publish(self, project_id, files, artifact_ids):
        self.files = dict(files)
        self.preview = dict(files)

    async def write_artifact(self, artifact_id, path, content):
        self.artifacts[path] = content


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
                "attempts_total": 1,
                "feedback": {"summary": "Looks good.", "issues": []},
            },
            RunStatus.COMPLETED,
            "Approved. Generated 1 files. Looks good.",
            id="approved",
        ),
        pytest.param(
            {
                "approved": False,
                "handoff": True,
                "attempts_used": 1,
                "attempts_total": 1,
                "feedback": {
                    "verdict": "escalate",
                    "summary": "The reviewer needs a product decision.",
                    "issues": [{"message": "Choose a checkout flow", "file_path": "index.html"}],
                },
            },
            RunStatus.AWAITING_USER,
            "This run needs your guidance. The reviewer needs a product decision.",
            id="escalation",
        ),
        pytest.param(
            {
                "approved": False,
                "handoff": False,
                "attempts_used": 3,
                "attempts_total": 3,
                "feedback": {
                    "verdict": "reject",
                    "summary": "The reviewer needs more detail.",
                    "issues": [{"message": "Fix the heading", "file_path": "index.html"}],
                },
            },
            RunStatus.AWAITING_USER,
            "This run needs your guidance. The reviewer needs more detail.",
            id="third-rejection",
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
        await repo.update_run(
            run.id,
            status=RunStatus.RUNNING,
            attempts_used=result["attempts_used"],
            attempts_total=result["attempts_total"],
            round=2,
        )
        current_project = await repo.create_project(user.id, "Currently selected project")
        current_run = await repo.create_run(user.id, "Current feature", current_project.id)
        await repo.update_run(current_run.id, status=RunStatus.COMPLETED)
        conversation = await repo.get_conversation(user.id)
        conversation.selected_project_id = current_project.id
        conversation.active_feature_run_id = current_run.id
        conversation.context_version = 7
        await repo.commit()
    plan = {"summary": "preserved plan", "steps": []}
    code = [
        {
            "file_path": "index.html",
            "content": "<h1>preserved</h1>",
            "checksum": "a" * 64,
            "file_type": "html",
        }
    ]
    checks = {"file_structure_ok": True, "js_syntax_ok": True, "issues": []}
    previous_preview = {"approved.html": "<h1>last approved</h1>"}
    processor.workspace.files = {"index.html": "<h1>preserved</h1>"}
    processor.workspace.preview = dict(previous_preview)
    await processor._finalize(
        run.id,
        {
            "project_id": str(project.id),
            **result,
            "attempts_total": result["attempts_total"],
            "plan": plan,
            "code": code,
            "checks": checks,
        },
    )
    await processor._fail(run.id, RuntimeError("late worker failure"))

    async with session_factory() as session:
        repo = SqlAlchemyRepository(session)
        turns = await repo.list_conversation_turns(user.id)
        saved_run = await repo.get_run_model(run.id)
        conversation = await repo.get_conversation(user.id)
        events = await repo.list_events(run.id)
    assert [(turn.role, turn.content) for turn in turns] == [
        (ConversationSender.USER, "request"),
        (ConversationSender.USER, "Current feature"),
        (ConversationSender.ASSISTANT, expected_reply),
    ]
    assert [turn.run_id for turn in turns] == [run.id, current_run.id, run.id]
    assert [turn.project_id for turn in turns] == [project.id, current_project.id, project.id]
    assert saved_run.status == expected_status
    assert saved_run.plan == plan
    assert saved_run.latest_code == code
    assert saved_run.feedback == result["feedback"]
    assert saved_run.checks == checks
    assert saved_run.attempts_used == result["attempts_used"]
    assert saved_run.attempts_total == result["attempts_total"]
    assert saved_run.round == 2
    assert processor.workspace.files == {"index.html": "<h1>preserved</h1>"}
    if expected_status == RunStatus.AWAITING_USER:
        assert processor.workspace.artifacts == {"index.html": "<h1>preserved</h1>"}
        assert processor.workspace.preview == previous_preview
    else:
        assert processor.workspace.preview == processor.workspace.files
    assert conversation.selected_project_id == current_project.id
    assert conversation.active_feature_run_id == current_run.id
    assert conversation.context_version == 7
    replies = [event for event in events if event.kind == EventKind.CHAT_REPLY]
    assert len(replies) == 1
    assert replies[0].payload["message"] == expected_reply
    assert sum(event.kind == EventKind.RUN_END for event in events) == 1


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
        events = await repo.list_events(run.id)
    assert [(turn.role, turn.content) for turn in turns] == [
        (ConversationSender.USER, "request"),
        (ConversationSender.ASSISTANT, "RuntimeError: provider unavailable"),
    ]
    assert [turn.run_id for turn in turns] == [run.id, run.id]
    assert saved_run.status == RunStatus.FAILED
    assert [event.kind for event in events].count(EventKind.ERROR) == 1
    assert [event.kind for event in events].count(EventKind.CHAT_REPLY) == 1
    assert [event.kind for event in events].count(EventKind.RUN_END) == 1
