from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.domain.enums import EventKind, FileType, RunStatus
from app.infrastructure.persistence.models import RateLimitCounter, Run, WorkerHeartbeat
from app.infrastructure.persistence.repository import SqlAlchemyRepository
from app.infrastructure.workspace.web import WebWorkspace
from app.worker import run_maintenance
from tests.conftest import create_test_chat, create_test_user

pytestmark = pytest.mark.usefixtures("require_database")


class FakeSaver:
    def __init__(self) -> None:
        self.deleted_threads: list[str] = []

    async def adelete_thread(self, thread_id: str) -> None:
        self.deleted_threads.append(thread_id)


async def test_maintenance_archives_old_runs_and_preserves_project_state(
    session_factory, repo_factory, tmp_path
) -> None:
    now = datetime(2026, 10, 23, tzinfo=UTC)
    old = now - timedelta(days=31)
    recent = now - timedelta(days=29)
    async with session_factory() as session:
        owner = await create_test_user(session, "retention@example.com")
        repo = SqlAlchemyRepository(session)
        _project_chat, project, old_run, _ = await create_test_chat(
            session, owner.id, "Old completed project"
        )
        await repo.update_run(old_run.id, status=RunStatus.COMPLETED)
        artifact = await repo.add_artifact(
            old_run.id, old_run.execution_id, "index.html", FileType.HTML, "a" * 64
        )
        await repo.append_event(old_run.id, EventKind.RUN_COMPLETED, {}, old_run.execution_id)

        _recent_chat, _recent_project, recent_run, _ = await create_test_chat(
            session, owner.id, "Recent completed project"
        )
        await repo.update_run(recent_run.id, status=RunStatus.COMPLETED)
        await repo.append_event(recent_run.id, EventKind.RUN_COMPLETED, {}, recent_run.execution_id)

        _failed_chat, _failed_project, old_failed, _ = await create_test_chat(
            session, owner.id, "Old failed project"
        )
        await repo.update_run(old_failed.id, status=RunStatus.FAILED)
        await repo.append_event(old_failed.id, EventKind.RUN_FAILED, {}, old_failed.execution_id)

        _handoff_chat, _handoff_project, old_handoff, _ = await create_test_chat(
            session, owner.id, "Old handoff project"
        )
        await repo.update_run(old_handoff.id, status=RunStatus.AWAITING_USER)
        await repo.append_event(old_handoff.id, EventKind.HANDOFF, {}, old_handoff.execution_id)

        for run_id, timestamp in (
            (old_run.id, old),
            (recent_run.id, recent),
            (old_failed.id, old),
            (old_handoff.id, old),
        ):
            await session.execute(update(Run).where(Run.id == run_id).values(updated_at=timestamp))
        session.add_all(
            [
                RateLimitCounter(
                    key_hash=b"e" * 32,
                    window_start=old,
                    count=1,
                    expires_at=now - timedelta(seconds=1),
                ),
                RateLimitCounter(
                    key_hash=b"f" * 32,
                    window_start=now,
                    count=1,
                    expires_at=now + timedelta(days=1),
                ),
                WorkerHeartbeat(worker_id="stale", last_seen_at=now - timedelta(seconds=120)),
                WorkerHeartbeat(worker_id="live", last_seen_at=now - timedelta(seconds=1)),
            ]
        )
        await session.commit()

    workspace = WebWorkspace(tmp_path)
    await workspace.write_file(project.id, "index.html", "latest source", {"index.html"})
    await workspace.publish(project.id, {"index.html": "latest preview"}, {})
    await workspace.write_artifact(artifact.id, "index.html", "old run source")
    saver = FakeSaver()

    await run_maintenance(repo_factory, workspace, saver, now=now, stale_after_seconds=60)

    archive_file = (
        workspace.root
        / "archived"
        / str(project.id)
        / str(old_run.id)
        / str(old_run.execution_id)
        / "index.html"
    )
    assert archive_file.read_text(encoding="utf-8") == "old run source"
    assert await workspace.read_file(project.id, "index.html") == "latest source"
    assert await workspace.read_preview(project.id, "index.html") == b"latest preview"
    await workspace.write_artifact(artifact.id, "index.html", "changed after archive")
    await run_maintenance(repo_factory, workspace, saver, now=now, stale_after_seconds=60)
    assert archive_file.read_text(encoding="utf-8") == "old run source"

    async with session_factory() as session:
        saved = {run_id: await session.get(Run, run_id) for run_id in (old_run.id, recent_run.id)}
        counters = list((await session.execute(select(RateLimitCounter))).scalars())
        heartbeats = list((await session.execute(select(WorkerHeartbeat))).scalars())
    assert saved[old_run.id].archived is True
    assert saved[old_run.id].updated_at == old
    assert saved[recent_run.id].archived is False
    assert set(saver.deleted_threads) == {
        f"{old_run.id}:{old_run.execution_id}",
        f"{old_failed.id}:{old_failed.execution_id}",
    }
    assert f"{recent_run.id}:{recent_run.execution_id}" not in saver.deleted_threads
    assert f"{old_handoff.id}:{old_handoff.execution_id}" not in saver.deleted_threads
    assert [(counter.key_hash, counter.count) for counter in counters] == [(b"f" * 32, 1)]
    assert [heartbeat.worker_id for heartbeat in heartbeats] == ["live"]
