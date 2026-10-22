from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.enums import (
    ACTIVE_STATUSES,
    ConversationSender,
    EventKind,
    FileType,
    ReviewerVerdict,
    RunStatus,
)
from app.domain.models import Code, Feedback, Issue, Plan, PlanStep, RunState
from app.infrastructure.persistence.models import (
    Artifact,
    Conversation,
    ConversationTurn,
    GraphEvent,
    GraphRun,
    Project,
    Review,
    Run,
    RunEvent,
    WorkerHeartbeat,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _run_state(run: Run) -> RunState:
    return RunState(
        id=run.id,
        owner_id=run.owner_id,
        project_id=run.project_id,
        message=run.message,
        status=run.status,
        attempts_used=run.attempts_used,
        attempts_total=run.attempts_total,
        round=run.round,
        user_guidance=run.user_guidance,
        plan=run.plan,
        latest_code=run.latest_code or [],
        feedback=run.feedback,
        checks=run.checks,
        chat_reply=run.chat_reply,
        error=run.error,
    )


class SqlAlchemyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create_conversation(self, owner_id: uuid.UUID) -> Conversation:
        await self.session.execute(
            pg_insert(Conversation)
            .values(id=uuid.uuid4(), owner_id=owner_id, context_version=0)
            .on_conflict_do_nothing(index_elements=[Conversation.owner_id])
        )
        return (
            await self.session.execute(
                select(Conversation).where(Conversation.owner_id == owner_id)
            )
        ).scalar_one()

    async def get_run(self, run_id: uuid.UUID) -> RunState | None:
        run = await self.session.get(Run, run_id)
        return _run_state(run) if run else None

    async def get_run_model(self, run_id: uuid.UUID) -> Run | None:
        return await self.session.get(Run, run_id)

    async def create_run(
        self,
        owner_id: uuid.UUID,
        message: str,
        project_id: uuid.UUID | None,
        *,
        plan: dict[str, Any] | None = None,
        latest_code: list[dict[str, Any]] | None = None,
        feedback: dict[str, Any] | None = None,
        checks: dict[str, Any] | None = None,
    ) -> RunState:
        run = Run(
            owner_id=owner_id,
            message=message,
            project_id=project_id,
            plan=plan,
            latest_code=latest_code or [],
            feedback=feedback,
            checks=checks,
        )
        self.session.add(run)
        await self.session.flush()
        await self.session.commit()
        return _run_state(run)

    async def update_run(self, run_id: uuid.UUID, **fields: Any) -> RunState | None:
        if fields:
            await self.session.execute(update(Run).where(Run.id == run_id).values(**fields))
            await self.session.commit()
        return await self.get_run(run_id)

    async def append_event(
        self, run_id: uuid.UUID, kind: EventKind, payload: dict[str, Any]
    ) -> None:
        self.session.add(RunEvent(run_id=run_id, kind=kind, payload=payload))
        await self.session.flush()

    async def add_review(self, run_id: uuid.UUID, feedback: Feedback) -> None:
        self.session.add(
            Review(
                run_id=run_id,
                verdict=feedback.verdict,
                summary=feedback.summary,
                issues=[
                    {"message": issue.message, "file_path": issue.file_path}
                    for issue in feedback.issues
                ],
                suggested_code=[
                    {"file_path": code.file_path, "content": code.content}
                    for code in feedback.suggested_code
                ],
            )
        )
        await self.session.flush()

    async def add_plan(self, run_id: uuid.UUID, plan: Plan) -> None:
        await self.session.execute(
            update(Run).where(Run.id == run_id).values(plan=plan_payload(plan))
        )
        await self.session.flush()

    async def list_events(self, run_id: uuid.UUID, after: datetime | None = None):
        stmt = select(RunEvent).where(RunEvent.run_id == run_id)
        if after is not None:
            stmt = stmt.where(RunEvent.created_at > after)
        stmt = stmt.order_by(RunEvent.created_at, RunEvent.id)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_trace(self, run_id: uuid.UUID):
        graph_runs = list(
            (
                await self.session.execute(
                    select(GraphRun)
                    .options(selectinload(GraphRun.events))
                    .where(GraphRun.run_id == run_id)
                    .order_by(GraphRun.round)
                )
            ).scalars()
        )
        return graph_runs

    async def add_graph_run(self, run_id: uuid.UUID, round_number: int, thread_id: str) -> GraphRun:
        graph_run = GraphRun(run_id=run_id, round=round_number, thread_id=thread_id)
        self.session.add(graph_run)
        await self.session.flush()
        return graph_run

    async def get_or_create_graph_run(
        self, run_id: uuid.UUID, round_number: int, thread_id: str
    ) -> GraphRun:
        graph_run = (
            await self.session.execute(
                select(GraphRun).where(GraphRun.run_id == run_id, GraphRun.round == round_number)
            )
        ).scalar_one_or_none()
        if graph_run is None:
            graph_run = await self.add_graph_run(run_id, round_number, thread_id)
            await self.session.commit()
        return graph_run

    async def add_graph_event(
        self, graph_run_id: uuid.UUID, node: str, payload: dict[str, Any]
    ) -> None:
        self.session.add(GraphEvent(graph_run_id=graph_run_id, node=node, payload=payload))
        await self.session.flush()

    async def add_artifact(
        self,
        run_id: uuid.UUID,
        file_path: str,
        file_type: FileType,
        checksum: str,
    ) -> Artifact:
        artifact = Artifact(
            run_id=run_id, file_path=file_path, file_type=file_type, checksum=checksum
        )
        self.session.add(artifact)
        await self.session.flush()
        return artifact

    async def get_artifact(self, artifact_id: uuid.UUID) -> Artifact | None:
        return await self.session.get(Artifact, artifact_id)

    async def list_artifact_ids_for_run(self, run_id: uuid.UUID) -> list[uuid.UUID]:
        return list(
            (
                await self.session.execute(select(Artifact.id).where(Artifact.run_id == run_id))
            ).scalars()
        )

    async def list_artifact_ids_for_project(self, project_id: uuid.UUID) -> list[uuid.UUID]:
        return list(
            (
                await self.session.execute(
                    select(Artifact.id)
                    .join(Run, Run.id == Artifact.run_id)
                    .where(Run.project_id == project_id)
                )
            ).scalars()
        )

    async def delete_run(self, run_id: uuid.UUID) -> None:
        await self.session.execute(delete(Run).where(Run.id == run_id))
        await self.session.commit()

    async def latest_run_for_project(self, project_id: uuid.UUID) -> Run | None:
        return (
            await self.session.execute(
                select(Run)
                .where(Run.project_id == project_id)
                .order_by(Run.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def count_runs_for_project(self, project_id: uuid.UUID) -> int:
        return int(
            (
                await self.session.execute(
                    select(func.count()).select_from(Run).where(Run.project_id == project_id)
                )
            ).scalar_one()
        )

    async def active_run_for_project(self, project_id: uuid.UUID) -> Run | None:
        return (
            await self.session.execute(
                select(Run).where(
                    Run.project_id == project_id, Run.status.in_(list(ACTIVE_STATUSES))
                )
            )
        ).scalar_one_or_none()

    async def active_projectless_run(self, owner_id: uuid.UUID) -> Run | None:
        return (
            await self.session.execute(
                select(Run).where(
                    Run.owner_id == owner_id,
                    Run.project_id.is_(None),
                    Run.status.in_(list(ACTIVE_STATUSES)),
                )
            )
        ).scalar_one_or_none()

    # Projects

    async def create_project(self, owner_id: uuid.UUID, title: str, summary: str = "") -> Project:
        project = Project(
            owner_id=owner_id, title=title.strip() or "Untitled project", summary=summary
        )
        self.session.add(project)
        await self.session.commit()
        return project

    async def get_project(self, project_id: uuid.UUID) -> Project | None:
        return await self.session.get(Project, project_id)

    async def get_owned_project(self, project_id: uuid.UUID, owner_id: uuid.UUID) -> Project | None:
        return (
            await self.session.execute(
                select(Project).where(Project.id == project_id, Project.owner_id == owner_id)
            )
        ).scalar_one_or_none()

    async def get_project_by_preview(self, preview_id: uuid.UUID) -> Project | None:
        return (
            await self.session.execute(select(Project).where(Project.preview_id == preview_id))
        ).scalar_one_or_none()

    async def list_projects(self, owner_id: uuid.UUID) -> list[Project]:
        return list(
            (
                await self.session.execute(
                    select(Project)
                    .where(Project.owner_id == owner_id)
                    .order_by(Project.updated_at.desc())
                )
            ).scalars()
        )

    async def update_project_summary(self, project_id: uuid.UUID, summary: str) -> None:
        await self.session.execute(
            update(Project).where(Project.id == project_id).values(summary=summary)
        )
        await self.session.commit()

    async def delete_project(self, project_id: uuid.UUID) -> None:
        await self.session.execute(delete(Project).where(Project.id == project_id))
        await self.session.commit()

    async def project_index(self, owner_id: uuid.UUID) -> list[dict[str, Any]]:
        projects = await self.list_projects(owner_id)
        return [
            {
                "id": str(project.id),
                "title": project.title,
                "summary": project.summary,
                "status": project.status.value,
            }
            for project in projects
        ]

    async def recent_runs(self, owner_id: uuid.UUID, limit: int = 10) -> list[dict[str, Any]]:
        runs = list(
            (
                await self.session.execute(
                    select(Run)
                    .where(Run.owner_id == owner_id)
                    .order_by(Run.created_at.desc())
                    .limit(limit)
                )
            ).scalars()
        )
        return [
            {
                "id": str(run.id),
                "project_id": str(run.project_id) if run.project_id else None,
                "message": run.message,
                "status": run.status.value,
                "round": run.round,
            }
            for run in runs
        ]

    # Conversations

    async def has_user_turn(self, run_id: uuid.UUID) -> bool:
        return (
            await self.session.execute(
                select(func.count())
                .select_from(ConversationTurn)
                .where(
                    ConversationTurn.run_id == run_id,
                    ConversationTurn.role == ConversationSender.USER,
                )
            )
        ).scalar_one() > 0

    async def add_conversation_turn(
        self,
        owner_id: uuid.UUID,
        content: str,
        role: ConversationSender,
        run_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
    ) -> None:
        conversation = await self.get_or_create_conversation(owner_id)
        sequence = (
            int(
                (
                    await self.session.execute(
                        select(func.coalesce(func.max(ConversationTurn.sequence), 0)).where(
                            ConversationTurn.conversation_id == conversation.id
                        )
                    )
                ).scalar_one()
            )
            + 1
        )
        self.session.add(
            ConversationTurn(
                conversation_id=conversation.id,
                project_id=project_id,
                run_id=run_id,
                role=role,
                content=content,
                sequence=sequence,
            )
        )
        await self.session.flush()

    async def list_conversation_turns(
        self,
        owner_id: uuid.UUID,
        project_id: uuid.UUID | None = None,
        after: int | None = None,
    ) -> list[ConversationTurn]:
        conversation = (
            await self.session.execute(
                select(Conversation).where(Conversation.owner_id == owner_id)
            )
        ).scalar_one_or_none()
        if conversation is None:
            return []
        stmt = select(ConversationTurn).where(ConversationTurn.conversation_id == conversation.id)
        if project_id is not None:
            stmt = stmt.where(ConversationTurn.project_id == project_id)
        if after is not None:
            stmt = stmt.where(ConversationTurn.sequence > after)
        return list(
            (await self.session.execute(stmt.order_by(ConversationTurn.sequence))).scalars()
        )

    # Queue

    async def claim(self, worker_id: str, stale_after_seconds: int = 60) -> RunState | None:
        cutoff = _now() - timedelta(seconds=stale_after_seconds)
        subquery = (
            select(Run.id)
            .where(
                (Run.status == RunStatus.PENDING)
                | (
                    (Run.status == RunStatus.RUNNING)
                    & ((Run.heartbeat_at < cutoff) | (Run.heartbeat_at.is_(None)))
                )
            )
            .order_by(Run.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        stmt = (
            update(Run)
            .where(Run.id.in_(subquery))
            .values(
                status=RunStatus.RUNNING,
                locked_by=worker_id,
                locked_at=_now(),
                heartbeat_at=_now(),
            )
            .returning(Run)
        )
        run = (await self.session.execute(stmt)).scalar_one_or_none()
        await self.session.commit()
        return _run_state(run) if run else None

    async def heartbeat(self, worker_id: str, run_id: uuid.UUID | None) -> None:
        stmt = (
            pg_insert(WorkerHeartbeat)
            .values(worker_id=worker_id, current_run_id=run_id, last_seen_at=_now())
            .on_conflict_do_update(
                index_elements=[WorkerHeartbeat.worker_id],
                set_={"current_run_id": run_id, "last_seen_at": _now()},
            )
        )
        await self.session.execute(stmt)
        if run_id is not None:
            await self.session.execute(
                update(Run)
                .where(Run.id == run_id, Run.locked_by == worker_id)
                .values(heartbeat_at=_now())
            )
        await self.session.commit()

    async def release(self, run_id: uuid.UUID) -> None:
        await self.session.execute(
            update(Run)
            .where(Run.id == run_id)
            .values(locked_by=None, locked_at=None, heartbeat_at=_now())
        )
        await self.session.commit()

    async def latest_heartbeat(self) -> datetime | None:
        return (
            await self.session.execute(select(func.max(WorkerHeartbeat.last_seen_at)))
        ).scalar_one_or_none()

    async def record_review(self, run_id: uuid.UUID, feedback: Feedback) -> None:
        await self.add_review(run_id, feedback)


def plan_payload(plan: Plan) -> dict[str, Any]:
    return {
        "id": str(plan.id),
        "project_id": str(plan.project_id),
        "summary": plan.summary,
        "steps": [
            {
                "id": str(step.id),
                "name": step.name,
                "file_path": step.file_path,
                "file_type": step.file_type.value
                if hasattr(step.file_type, "value")
                else step.file_type,
                "description": step.description,
            }
            for step in plan.steps
        ],
    }


def plan_from_payload(payload: dict[str, Any]) -> Plan:
    steps = [
        PlanStep(
            id=uuid.UUID(item["id"]),
            name=item["name"],
            file_path=item["file_path"],
            file_type=FileType(item["file_type"]),
            description=item["description"],
        )
        for item in payload.get("steps", [])
    ]
    return Plan(
        id=uuid.UUID(payload["id"]),
        project_id=uuid.UUID(payload["project_id"]),
        summary=payload.get("summary", ""),
        steps=steps,
    )


def code_payload(code: Code) -> dict[str, Any]:
    return {
        "file_path": code.file_path,
        "content": code.content,
        "checksum": code.checksum,
        "file_type": code.file_type.value if hasattr(code.file_type, "value") else code.file_type,
    }


def checksum(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def feedback_payload(feedback: Feedback) -> dict[str, Any]:
    return {
        "verdict": feedback.verdict.value,
        "summary": feedback.summary,
        "issues": [
            {"message": issue.message, "file_path": issue.file_path} for issue in feedback.issues
        ],
    }


def feedback_from_payload(payload: dict[str, Any]) -> Feedback:
    return Feedback(
        verdict=ReviewerVerdict(payload["verdict"]),
        summary=payload.get("summary", ""),
        issues=[
            Issue(message=item.get("message", ""), file_path=item.get("file_path"))
            for item in payload.get("issues", [])
        ],
    )


__all__ = [
    "SqlAlchemyRepository",
    "checksum",
    "code_payload",
    "feedback_from_payload",
    "feedback_payload",
    "plan_from_payload",
    "plan_payload",
]
