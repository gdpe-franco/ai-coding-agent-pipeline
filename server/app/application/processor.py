from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from app.application.graph import CodingGraph
from app.application.ports import RepositoryFactory
from app.domain.enums import ConversationSender, EventKind, RunStatus
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

    async def process(self, run) -> None:
        thread_id = f"{run.id}:{run.round}"
        heartbeat = asyncio.create_task(self._heartbeat_loop(run.id))
        try:
            async with self.repo_factory() as repo:
                graph_run_id = await repo.get_or_create_graph_run(run.id, run.round, thread_id)
                await repo.append_event(
                    run.id,
                    EventKind.RUN_START,
                    {"round": run.round, "worker_id": self.worker_id},
                )
                await repo.commit()

            state = {
                "run_id": str(run.id),
                "owner_id": str(run.owner_id),
                "project_id": str(run.project_id) if run.project_id else None,
                "message": run.message,
                "guidance": run.user_guidance,
                "plan": run.plan or {},
                "code": run.latest_code or [],
                "feedback": run.feedback or {},
                "checks": run.checks or {},
                "attempts_used": run.attempts_used,
                "attempts_total": run.attempts_total,
                "round": run.round,
                "graph_run_id": str(graph_run_id),
                "skip_resolve": run.project_id is not None,
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
            project_id = UUID(result["project_id"]) if result.get("project_id") else None
            approved = bool(result.get("approved"))
            handoff = bool(result.get("handoff")) or (
                not approved and result.get("attempts_used", 0) >= self.settings.max_coder_attempts
            )
            if not project_id:
                raise RuntimeError("graph finished without a project")
            project = await repo.get_project_context(project_id)
            if project is None:
                raise RuntimeError("graph finished with an unknown project")
            await repo.associate_unassigned_turns_with_project(run_id, project_id)

            files = await self.workspace.read_all(project_id)
            artifact_ids = await repo.ensure_artifacts_for_run(run_id, result.get("code", []))
            await repo.commit()
            if approved:
                await self.workspace.publish(project_id, files, artifact_ids)
            else:
                for item in result.get("code", []):
                    artifact_id = artifact_ids.get(item["file_path"])
                    if artifact_id:
                        await self.workspace.write_artifact(
                            artifact_id, item["file_path"], item["content"]
                        )

            feedback = result.get("feedback") or {}
            summary = feedback.get("summary") or "Run finished."
            if approved:
                reply = f"Approved. Generated {len(files)} files. {summary}"
                await repo.update_run(
                    run_id,
                    status=RunStatus.COMPLETED,
                    chat_reply=reply,
                    plan=result.get("plan"),
                    latest_code=result.get("code", []),
                    feedback=feedback,
                    checks=result.get("checks"),
                    error=None,
                )
                await repo.add_conversation_turn(
                    run.owner_id, reply, ConversationSender.ASSISTANT, run_id, project_id
                )
            elif handoff:
                reply = f"This run needs your guidance. {summary}"
                await repo.update_run(
                    run_id,
                    status=RunStatus.AWAITING_USER,
                    chat_reply=reply,
                    plan=result.get("plan"),
                    latest_code=result.get("code", []),
                    feedback=feedback,
                    checks=result.get("checks"),
                    error=None,
                )
                await repo.append_event(
                    run_id,
                    EventKind.HANDOFF,
                    {
                        "summary": summary,
                        "issues": feedback.get("issues", []),
                        "attempts_used": result.get("attempts_used", 0),
                        "attempts_total": result.get("attempts_total", 0),
                    },
                )
                await repo.add_conversation_turn(
                    run.owner_id, reply, ConversationSender.ASSISTANT, run_id, project_id
                )
            else:
                await self._mark_failed(
                    repo,
                    run,
                    "The reviewer rejected the result but the run did not reach a handoff.",
                )
                await repo.commit()
                return
            await repo.update_project_summary(project_id, summary[:2000])
            await repo.append_event(
                run_id,
                EventKind.RUN_END,
                {
                    "status": RunStatus.COMPLETED.value
                    if approved
                    else RunStatus.AWAITING_USER.value
                },
            )
            await repo.commit()
            await self.memory.save_round(
                run.owner_id,
                project_id,
                project.title,
                "completed" if approved else "awaiting_user",
                summary=summary,
                last_feature=run.message[:500],
            )

    async def _fail(self, run_id: UUID, exc: Exception) -> None:
        async with self.repo_factory() as repo:
            run = await repo.get_run(run_id)
            if run is None or run.status != RunStatus.RUNNING:
                return
            await self._mark_failed(
                repo, run, f"{type(exc).__name__}: {exc}", tag=getattr(exc, "tag", "internal_error")
            )
            await repo.commit()

    async def _mark_failed(
        self, repo, run: RunState, message: str, tag: str = "internal_error"
    ) -> None:
        await repo.update_run(
            run.id, status=RunStatus.FAILED, error=message[:2000], chat_reply=message[:2000]
        )
        await repo.append_event(run.id, EventKind.ERROR, {"tag": tag, "message": message[:2000]})
        await repo.append_event(run.id, EventKind.RUN_END, {"status": RunStatus.FAILED.value})
        if run.project_id:
            await repo.associate_unassigned_turns_with_project(run.id, run.project_id)
        await repo.add_conversation_turn(
            run.owner_id,
            message[:2000],
            ConversationSender.ASSISTANT,
            run.id,
            run.project_id,
        )
        if run.project_id:
            project = await repo.get_project_context(run.project_id)
            await self.memory.save_round(
                run.owner_id,
                run.project_id,
                project.title if project else None,
                "failed",
                summary=message[:2000],
                last_feature=run.message[:500],
            )
