from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from app.application.processor import RunProcessor
from app.domain.enums import RunStatus
from app.infrastructure.persistence.models import Artifact, ChatMessage, Run
from app.infrastructure.persistence.repository import SqlAlchemyRepository
from tests.conftest import create_test_chat, create_test_user


class FakeGraph:
    def __init__(self, result: dict | Exception) -> None:
        self.result = result
        self.states: list[dict] = []

    async def ainvoke(self, state: dict, config: dict) -> dict:
        self.states.append(state)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeWorkspace:
    def __init__(self) -> None:
        self.artifacts: dict[tuple[str, str], str] = {}
        self.files = {"index.html": "<h1>approved</h1>"}

    async def read_all(self, project_id):
        return dict(self.files)

    async def publish(self, project_id, files, artifact_ids):
        self.published = (project_id, files, artifact_ids)
        for path, content in files.items():
            artifact_id = artifact_ids.get(path)
            if artifact_id is not None:
                self.artifacts[(str(artifact_id), path)] = content

    async def write_artifact(self, artifact_id, path, content):
        self.artifacts[(str(artifact_id), path)] = content


class FakeMemory:
    async def save_project_memory(self, project_id, summary):
        self.saved = (project_id, summary)


def _result(
    *,
    approved: bool,
    handoff: bool = False,
    content: str = "<h1>approved</h1>",
    checksum: str = "a" * 64,
) -> dict:
    code = [
        {
            "file_path": "index.html",
            "content": content,
            "checksum": checksum,
            "file_type": "html",
        }
    ]
    return {
        "approved": approved,
        "handoff": handoff,
        "project_id": None,
        "plan": {"summary": "Build page", "steps": []},
        "code": code,
        "feedback": {
            "summary": "reviewed",
            "issues": [],
            "verdict": "approve" if approved else "escalate",
        },
        "checks": {"file_structure_ok": True, "js_syntax_ok": True, "issues": []},
    }


async def _running_run(session_factory, email: str):
    async with session_factory() as session:
        owner = await create_test_user(session, email)
        chat, project, run, _ = await create_test_chat(session, owner.id, "Build the app")
    async with session_factory() as session:
        running = await SqlAlchemyRepository(session).claim("worker")
    assert running is not None and running.id == run.id
    return chat, project, running


async def test_processor_finalizes_run_in_its_chat_and_publishes_approved_files(
    session_factory, repo_factory, tmp_path
) -> None:
    chat, project, running = await _running_run(session_factory, "processor@example.com")
    graph = FakeGraph(_result(approved=True))
    workspace, memory = FakeWorkspace(), FakeMemory()
    processor = RunProcessor(
        repo_factory,
        graph,
        workspace,
        memory,
        SimpleNamespace(worker_heartbeat_seconds=1),
        "worker-a",
    )

    await processor.process(running)

    async with session_factory() as session:
        saved = await session.get(Run, running.id)
        messages = list(
            (
                await session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.chat_id == chat.id)
                    .order_by(ChatMessage.sequence)
                )
            ).scalars()
        )
    assert saved.status == RunStatus.COMPLETED
    assert saved.chat_id == chat.id
    assert workspace.published[0] == project.id
    assert memory.saved == (project.id, "Completed request: Build the app\nResult: reviewed")
    assert graph.states[0]["execution_id"] == str(running.execution_id)
    assert [(message.role.value, message.run_id) for message in messages] == [
        ("user", running.id),
        ("assistant", running.id),
    ]


async def test_processor_preserves_handoff_without_approval(session_factory, repo_factory) -> None:
    _chat, _project, running = await _running_run(session_factory, "handoff@example.com")
    memory = FakeMemory()
    processor = RunProcessor(
        repo_factory,
        FakeGraph(_result(approved=False, handoff=True)),
        FakeWorkspace(),
        memory,
        SimpleNamespace(worker_heartbeat_seconds=1),
        "worker-a",
    )

    await processor.process(running)

    async with session_factory() as session:
        saved = await session.get(Run, running.id)
    assert saved.status == RunStatus.AWAITING_USER
    assert saved.feedback["verdict"] == "escalate"
    assert "needs your guidance" in saved.chat_reply
    assert not hasattr(memory, "saved")


async def test_continuation_keeps_artifacts_for_each_execution(
    session_factory, repo_factory
) -> None:
    _chat, _project, first_execution = await _running_run(
        session_factory, "execution-history@example.com"
    )
    workspace = FakeWorkspace()
    settings = SimpleNamespace(worker_heartbeat_seconds=1)
    first = RunProcessor(
        repo_factory,
        FakeGraph(_result(approved=False, handoff=True)),
        workspace,
        FakeMemory(),
        settings,
        "worker-a",
    )
    await first.process(first_execution)

    async with repo_factory() as repo:
        continued = await repo.continue_run(
            first_execution.owner_id, first_execution.id, uuid4(), "Keep the layout"
        )
    assert continued is not None
    second_execution = continued[1]
    assert second_execution.execution_id != first_execution.execution_id
    async with repo_factory() as repo:
        running = await repo.claim("worker-b")
    assert running is not None and running.id == first_execution.id
    workspace.files["index.html"] = "<h1>revised</h1>"
    second = RunProcessor(
        repo_factory,
        FakeGraph(_result(approved=True, content="<h1>revised</h1>", checksum="b" * 64)),
        workspace,
        FakeMemory(),
        settings,
        "worker-b",
    )
    await second.process(running)

    async with session_factory() as session:
        artifacts = list(
            (
                await session.execute(select(Artifact).where(Artifact.run_id == first_execution.id))
            ).scalars()
        )
    assert {artifact.execution_id for artifact in artifacts} == {
        first_execution.execution_id,
        second_execution.execution_id,
    }
    assert len({artifact.id for artifact in artifacts}) == 2
    assert set(workspace.artifacts.values()) == {
        "<h1>approved</h1>",
        "<h1>revised</h1>",
    }


async def test_processor_records_failure_and_keeps_chat_message(
    session_factory, repo_factory
) -> None:
    chat, _project, running = await _running_run(session_factory, "failure@example.com")
    processor = RunProcessor(
        repo_factory,
        FakeGraph(RuntimeError("provider unavailable")),
        FakeWorkspace(),
        FakeMemory(),
        SimpleNamespace(worker_heartbeat_seconds=1),
        "worker-a",
    )

    await processor.process(running)

    async with session_factory() as session:
        saved = await session.get(Run, running.id)
        messages = list(
            (
                await session.execute(select(ChatMessage).where(ChatMessage.chat_id == chat.id))
            ).scalars()
        )
    assert saved.status == RunStatus.FAILED
    assert "provider unavailable" in saved.error
    assert [message.content for message in messages] == [
        "Build the app",
        "RuntimeError: provider unavailable",
    ]
