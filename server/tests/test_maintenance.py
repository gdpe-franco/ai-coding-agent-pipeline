from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.domain.enums import FileType, RunStatus
from app.infrastructure.persistence.models import RateLimitCounter, Run, WorkerHeartbeat
from app.infrastructure.persistence.repository import SqlAlchemyRepository
from app.infrastructure.workspace.web import WebWorkspace
from app.worker import run_maintenance
from tests.conftest import create_test_user

pytestmark = pytest.mark.usefixtures("require_database")


class FakeSaver:
    def __init__(self) -> None:
        self.deleted_threads: list[str] = []

    async def adelete_thread(self, thread_id: str) -> None:
        self.deleted_threads.append(thread_id)


async def test_maintenance_archives_old_completed_and_preserves_current_project_files(
    session_factory, repo_factory, tmp_path
) -> None:
    now = datetime(2026, 10, 23, tzinfo=UTC)
    old = now - timedelta(days=31)
    recent = now - timedelta(days=29)
    async with session_factory() as session:
        owner = await create_test_user(session, "retention@example.com")
        repo = SqlAlchemyRepository(session)
        project = await repo.create_project(owner.id, "Current files")
        old_completed = await repo.create_run(owner.id, "old completed", project.id)
        await repo.update_run(old_completed.id, status=RunStatus.COMPLETED)
        old_completed_thread = await repo.add_graph_run(
            old_completed.id, 1, f"{old_completed.id}:1"
        )
        artifact = await repo.add_artifact(old_completed.id, "index.html", FileType.HTML, "a" * 64)

        recent_project = await repo.create_project(owner.id, "Recent")
        recent_completed = await repo.create_run(owner.id, "recent completed", recent_project.id)
        await repo.update_run(recent_completed.id, status=RunStatus.COMPLETED)
        recent_thread = await repo.add_graph_run(recent_completed.id, 1, f"{recent_completed.id}:1")

        failed_project = await repo.create_project(owner.id, "Failed")
        old_failed = await repo.create_run(owner.id, "old failed", failed_project.id)
        await repo.update_run(old_failed.id, status=RunStatus.FAILED)
        failed_thread = await repo.add_graph_run(old_failed.id, 1, f"{old_failed.id}:1")

        handoff_project = await repo.create_project(owner.id, "Handoff")
        old_handoff = await repo.create_run(owner.id, "old handoff", handoff_project.id)
        await repo.update_run(old_handoff.id, status=RunStatus.AWAITING_USER)
        handoff_thread = await repo.add_graph_run(old_handoff.id, 1, f"{old_handoff.id}:1")

        for run_id, timestamp in (
            (old_completed.id, old),
            (recent_completed.id, recent),
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
        workspace.root / "archived" / str(project.id) / str(old_completed.id) / "index.html"
    )
    assert archive_file.read_text(encoding="utf-8") == "old run source"
    assert await workspace.read_file(project.id, "index.html") == "latest source"
    assert await workspace.read_preview(project.id, "index.html") == b"latest preview"

    await workspace.write_artifact(artifact.id, "index.html", "changed after archiving")
    await run_maintenance(repo_factory, workspace, saver, now=now, stale_after_seconds=60)

    assert archive_file.read_text(encoding="utf-8") == "old run source"
    async with session_factory() as session:
        saved_completed = await session.get(Run, old_completed.id)
        saved_recent = await session.get(Run, recent_completed.id)
        saved_failed = await session.get(Run, old_failed.id)
        saved_handoff = await session.get(Run, old_handoff.id)
        counters = list((await session.execute(select(RateLimitCounter))).scalars())
        heartbeats = list((await session.execute(select(WorkerHeartbeat))).scalars())
    assert saved_completed.archived is True
    assert saved_completed.updated_at == old
    assert saved_recent.archived is False
    assert saved_failed.archived is False
    assert saved_handoff.archived is False
    assert set(saver.deleted_threads) == {
        old_completed_thread.thread_id,
        failed_thread.thread_id,
    }
    assert recent_thread.thread_id not in saver.deleted_threads
    assert handoff_thread.thread_id not in saver.deleted_threads
    assert [(counter.key_hash, counter.count) for counter in counters] == [(b"f" * 32, 1)]
    assert [heartbeat.worker_id for heartbeat in heartbeats] == ["live"]
