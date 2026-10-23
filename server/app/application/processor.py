from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from app.application.graph import CodingGraph
from app.application.ports import RepositoryFactory
from app.domain.enums import EventKind, RunStatus
from app.domain.errors import MissingCredentialsError, ModelError, WorkspaceError
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
            file_count = len(code)
            file_count_label = f"{file_count} {'file' if file_count == 1 else 'files'}"
            if approved:
                status = RunStatus.COMPLETED
                reply = (
                    f"Your changes are ready.\n\n{summary}\n\n"
                    f"I updated {file_count_label}. File checks and required JavaScript syntax checks passed."
                )
                event_kind = EventKind.RUN_COMPLETED
                event_payload = {"summary": summary, "files": len(files)}
                await self.memory.save_project_memory(
                    project_id, f"Completed request: {run.message}\nResult: {summary}"[:4000]
                )
            elif handoff:
                status = RunStatus.AWAITING_USER
                reply = (
                    f"I’ve made the requested changes, but I need your guidance before I can finish.\n\n"
                    f"{summary}\n\nI updated {file_count_label}. Your last approved preview is unchanged."
                )
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
                project_summary=summary[:2000] if approved else None,
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
            diagnostic = (
                str(exc)
                if isinstance(exc, MissingCredentialsError)
                else f"{type(exc).__name__}: {getattr(exc, 'tag', 'internal_error')}"
            )
            await self._mark_failed(
                repo,
                run,
                self._failure_reply(exc),
                tag=getattr(exc, "tag", "internal_error"),
                diagnostic=diagnostic,
            )

    @staticmethod
    def _failure_reply(exc: Exception) -> str:
        if isinstance(exc, MissingCredentialsError):
            return (
                f"I couldn’t start this run because the operator configuration is missing `"
                f"{exc.setting}`. Please contact the service operator."
            )
        if isinstance(exc, ModelError):
            return (
                "The AI service couldn’t complete this request. Your run history is saved; "
                "you can try again or start another request."
            )
        if isinstance(exc, WorkspaceError):
            return (
                "The generated files couldn’t be saved safely. The run has stopped; "
                "your last approved preview is unchanged."
            )
        return (
            "Something went wrong while working on this request. The run has stopped, "
            "and its technical details are available in run history."
        )

    async def _mark_failed(
        self,
        repo,
        run: RunState,
        message: str,
        tag: str = "internal_error",
        diagnostic: str | None = None,
    ) -> None:
        await repo.finalize_run(
            run.id,
            RunStatus.FAILED,
            message,
            EventKind.RUN_FAILED,
            {"tag": tag, "message": (diagnostic or message)[:2000]},
            error=(diagnostic or message)[:2000],
        )


__all__ = ["RunProcessor"]
