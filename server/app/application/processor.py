from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from app.application.graph import CodingGraph
from app.application.ports import RepositoryFactory
from app.domain.enums import EventKind, RunStatus
from app.domain.models import RunState

logger = logging.getLogger(__name__)


class RunProcessor:
    def __init__(
        self,
        repo_factory: RepositoryFactory,
        graph: CodingGraph,
        workspace,
        memory,
        settings,
        worker_id: str,
    ) -> None:
        self.repo_factory = repo_factory
        self.graph = graph
        self.workspace = workspace
        self.memory = memory
        self.settings = settings
        self.worker_id = worker_id

    async def process(self, run: RunState) -> None:
        thread_id = f"{run.id}:{run.execution_id}"
        heartbeat = asyncio.create_task(self._heartbeat_loop(run.id))
        try:
            async with self.repo_factory() as repo:
                await repo.append_event(
                    run.id,
                    EventKind.EXECUTION_STARTED,
                    {"worker_id": self.worker_id, "execution_id": str(run.execution_id)},
                    run.execution_id,
                )
                await repo.commit()

            state = {
                "run_id": str(run.id),
                "owner_id": str(run.owner_id),
                "chat_id": str(run.chat_id),
                "project_id": str(run.project_id),
                "execution_id": str(run.execution_id),
                "message": run.message,
                "guidance": run.user_guidance,
                "plan": run.plan or {},
                "code": run.latest_code or [],
                "feedback": run.feedback or {},
                "checks": run.checks or {},
            }
            result = await self.graph.ainvoke(state, {"configurable": {"thread_id": thread_id}})
            await self._finalize(run.id, result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("run %s failed", run.id)
            await self._fail(run.id, exc)
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.warning("worker heartbeat shutdown failed", exc_info=True)
            async with self.repo_factory() as repo:
                await repo.release(run.id)

    async def _heartbeat_loop(self, run_id: UUID) -> None:
        while True:
            await asyncio.sleep(self.settings.worker_heartbeat_seconds)
            try:
                async with self.repo_factory() as repo:
                    await repo.heartbeat(self.worker_id, run_id)
            except Exception:
                logger.warning("worker heartbeat failed", exc_info=True)

    async def _finalize(self, run_id: UUID, result: dict[str, Any]) -> None:
        async with self.repo_factory() as repo:
            run = await repo.get_run(run_id)
            if run is None or run.status != RunStatus.RUNNING:
                return
            approved = bool(result.get("approved"))
            handoff = bool(result.get("handoff"))
            project_id = run.project_id
            project = await repo.get_project_context(project_id)
            if project is None:
                raise RuntimeError("run's chat project is unavailable")

            files = await self.workspace.read_all(project_id)
            code = result.get("code", [])
            artifact_ids = await repo.ensure_artifacts_for_run(run_id, run.execution_id, code)
            await repo.commit()
            if approved:
                await self.workspace.publish(project_id, files, artifact_ids)
                await repo.append_event(
                    run_id,
                    EventKind.ARTIFACT_PUBLISHED,
                    {"artifact_ids": [str(artifact_id) for artifact_id in artifact_ids.values()]},
                    run.execution_id,
                )
                await repo.commit()
            else:
                for item in code:
                    artifact_id = artifact_ids.get(item["file_path"])
                    if artifact_id:
                        await self.workspace.write_artifact(
                            artifact_id, item["file_path"], item["content"]
                        )

            feedback = result.get("feedback") or {}
            summary = feedback.get("summary") or "Run finished."
            if approved:
                status = RunStatus.COMPLETED
                reply = f"Approved. Generated {len(files)} files. {summary}"
                event_kind = EventKind.RUN_COMPLETED
                event_payload = {"summary": summary, "files": len(files)}
                await self.memory.save_project_memory(
                    project_id, f"Completed request: {run.message}\nResult: {summary}"[:4000]
                )
                await repo.update_project_summary(project_id, summary[:2000])
            elif handoff:
                status = RunStatus.AWAITING_USER
                reply = f"This run needs your guidance. {summary}"
                event_kind = EventKind.HANDOFF
                event_payload = {"summary": summary, "issues": feedback.get("issues", [])}
            else:
                await self._mark_failed(
                    repo,
                    run,
                    "The reviewer rejected the result but the run did not reach a handoff.",
                )
                return

            await repo.finalize_run(
                run_id,
                status,
                reply,
                event_kind,
                event_payload,
                plan=result.get("plan"),
                latest_code=code,
                feedback=feedback,
                checks=result.get("checks"),
                error=None,
            )

    async def _fail(self, run_id: UUID, exc: Exception) -> None:
        async with self.repo_factory() as repo:
            run = await repo.get_run(run_id)
            if run is None or run.status != RunStatus.RUNNING:
                return
            await self._mark_failed(
                repo,
                run,
                f"{type(exc).__name__}: {exc}"[:2000],
                tag=getattr(exc, "tag", "internal_error"),
            )

    async def _mark_failed(
        self, repo, run: RunState, message: str, tag: str = "internal_error"
    ) -> None:
        await repo.finalize_run(
            run.id,
            RunStatus.FAILED,
            message,
            EventKind.RUN_FAILED,
            {"tag": tag, "message": message[:2000]},
            error=message[:2000],
        )


__all__ = ["RunProcessor"]
