from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.enums import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    EventKind,
    FileType,
    MessageSender,
    RunStatus,
)
from app.domain.errors import RunConflictError
from app.domain.models import Plan, ProjectContext, RunState
from app.domain.policies import can_continue
from app.domain.serialization import plan_payload
from app.infrastructure.persistence.models import (
    Artifact,
    Chat,
    ChatMessage,
    Project,
    RateLimitCounter,
    Run,
    RunEvent,
    WorkerHeartbeat,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _chat_title(message: str) -> str:
    title = " ".join(message.split())
    return title[:80] or "New chat"


def _run_state(run: Run) -> RunState:
    project = run.chat.project
    return RunState(
        id=run.id,
        owner_id=project.owner_id,
        chat_id=run.chat_id,
        project_id=project.id,
        execution_id=run.execution_id,
        message=run.message,
        status=run.status,
        user_guidance=run.user_guidance,
        plan=run.plan,
        latest_code=run.latest_code or [],
        feedback=run.feedback,
        checks=run.checks,
        chat_reply=run.chat_reply,
        error=run.error,
    )


def _project_context(project: Project) -> ProjectContext:
    return ProjectContext(id=project.id, title=project.title, summary=project.summary)


class SqlAlchemyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def commit(self) -> None:
        await self.session.commit()

    async def _chat_by_creation_key(
        self, owner_id: uuid.UUID, creation_key: uuid.UUID
    ) -> tuple[Chat, Project, Run] | None:
        result = await self.session.execute(
            select(Chat, Project, Run)
            .join(Project, Project.id == Chat.project_id)
            .join(Run, Run.chat_id == Chat.id)
            .where(Project.owner_id == owner_id, Chat.creation_key == creation_key)
            .order_by(Run.created_at)
            .limit(1)
            .options(selectinload(Chat.project), selectinload(Chat.runs))
        )
        row = result.first()
        if row is None:
            return None
        chat, project, run = row
        return chat, project, run

    async def create_chat(
        self,
        owner_id: uuid.UUID,
        creation_key: uuid.UUID,
        message_key: uuid.UUID,
        content: str,
    ) -> tuple[Chat, Project, Run, bool]:
        creation_key = uuid.uuid5(uuid.NAMESPACE_URL, f"chat-create:{owner_id}:{creation_key}")
        existing = await self._chat_by_creation_key(owner_id, creation_key)
        if existing is not None:
            chat, project, run = existing
            if run.message != content:
                raise RunConflictError("Idempotency-Key was already used for a different request")
            return chat, project, run, False

        project = Project(owner_id=owner_id, title=_chat_title(content), summary="")
        self.session.add(project)
        await self.session.flush()
        inserted = await self.session.execute(
            pg_insert(Chat)
            .values(project_id=project.id, creation_key=creation_key)
            .on_conflict_do_nothing(index_elements=[Chat.creation_key])
            .returning(Chat.id)
        )
        chat_id = inserted.scalar_one_or_none()
        if chat_id is None:
            await self.session.rollback()
            existing = await self._chat_by_creation_key(owner_id, creation_key)
            if existing is None:
                raise RunConflictError("Idempotency-Key could not be resolved")
            if existing[2].message != content:
                raise RunConflictError("Idempotency-Key was already used for a different request")
            return (*existing, False)
        chat = await self.session.get(Chat, chat_id)
        assert chat is not None
        message = await self._new_message(chat.id, MessageSender.USER, content, message_key)
        run = Run(chat_id=chat.id, message=content)
        self.session.add(run)
        await self.session.flush()
        message.run_id = run.id
        await self._append_event(run, EventKind.RUN_QUEUED, {"message_id": str(message.id)})
        chat.updated_at = _now()
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            existing = await self._chat_by_creation_key(owner_id, creation_key)
            if existing is not None:
                if existing[2].message != content:
                    raise RunConflictError(
                        "Idempotency-Key was already used for a different request"
                    )
                return (*existing, False)
            raise
        return chat, project, run, True

    async def create_chat_message(
        self,
        owner_id: uuid.UUID,
        chat_id: uuid.UUID,
        idempotency_key: uuid.UUID,
        content: str,
        max_runs_per_project: int,
    ) -> tuple[ChatMessage, Run, bool]:
        chat = await self._owned_chat_for_update(owner_id, chat_id)
        if chat is None:
            return None
        existing = await self.session.execute(
            select(ChatMessage)
            .options(selectinload(ChatMessage.run))
            .where(ChatMessage.chat_id == chat.id, ChatMessage.idempotency_key == idempotency_key)
        )
        prior = existing.scalar_one_or_none()
        if prior is not None and prior.run is not None:
            if prior.content != content:
                raise RunConflictError("Idempotency-Key was already used for a different message")
            return prior, prior.run, False

        active = await self._active_run(chat.id)
        if active is not None:
            raise RunConflictError("This chat already has a non-terminal run")
        count = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(Run).where(Run.chat_id == chat.id)
                )
            ).scalar_one()
        )
        if count >= max_runs_per_project:
            raise RunConflictError("This project has reached its run limit")

        message = await self._new_message(chat.id, MessageSender.USER, content, idempotency_key)
        run = Run(chat_id=chat.id, message=content)
        self.session.add(run)
        await self.session.flush()
        message.run_id = run.id
        await self._append_event(run, EventKind.RUN_QUEUED, {"message_id": str(message.id)})
        chat.updated_at = _now()
        await self.session.commit()
        return message, run, True

    async def continue_run(
        self,
        owner_id: uuid.UUID,
        run_id: uuid.UUID,
        idempotency_key: uuid.UUID,
        guidance: str,
    ) -> tuple[ChatMessage, Run, bool] | None:
        run = await self.get_owned_run_for_update(owner_id, run_id)
        if run is None:
            return None
        existing = await self.session.execute(
            select(ChatMessage)
            .options(selectinload(ChatMessage.run))
            .where(
                ChatMessage.chat_id == run.chat_id, ChatMessage.idempotency_key == idempotency_key
            )
        )
        prior = existing.scalar_one_or_none()
        if prior is not None:
            if prior.content != guidance:
                raise RunConflictError("Idempotency-Key was already used for different guidance")
            return prior, run, False
        if not can_continue(run.status):
            raise RunConflictError("Only awaiting_user runs can be continued")

        message = await self._new_message(
            run.chat_id, MessageSender.USER, guidance, idempotency_key, run_id=run.id
        )
        run.status = RunStatus.PENDING
        run.execution_id = uuid.uuid4()
        run.user_guidance = guidance
        run.chat_reply = None
        run.error = None
        run.locked_by = None
        run.locked_at = None
        run.heartbeat_at = None
        await self._append_event(
            run,
            EventKind.GUIDANCE,
            {"message_id": str(message.id), "execution_id": str(run.execution_id)},
        )
        await self._append_event(run, EventKind.RUN_QUEUED, {"execution_id": str(run.execution_id)})
        run.chat.updated_at = _now()
        await self.session.commit()
        return message, run, True

    async def _new_message(
        self,
        chat_id: uuid.UUID,
        role: MessageSender,
        content: str,
        idempotency_key: uuid.UUID,
        *,
        run_id: uuid.UUID | None = None,
    ) -> ChatMessage:
        sequence = (
            int(
                (
                    await self.session.execute(
                        select(func.coalesce(func.max(ChatMessage.sequence), 0)).where(
                            ChatMessage.chat_id == chat_id
                        )
                    )
                ).scalar_one()
            )
            + 1
        )
        message = ChatMessage(
            chat_id=chat_id,
            run_id=run_id,
            idempotency_key=idempotency_key,
            role=role,
            content=content,
            sequence=sequence,
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def _append_event(
        self,
        run: Run,
        kind: EventKind | str,
        payload: dict[str, Any],
        execution_id: uuid.UUID | None = None,
    ) -> RunEvent:
        sequence = (
            int(
                (
                    await self.session.execute(
                        select(func.coalesce(func.max(RunEvent.sequence), 0)).where(
                            RunEvent.run_id == run.id
                        )
                    )
                ).scalar_one()
            )
            + 1
        )
        event = RunEvent(
            run_id=run.id,
            execution_id=execution_id if execution_id is not None else run.execution_id,
            sequence=sequence,
            kind=str(kind),
            payload=payload,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def append_event(
        self,
        run_id: uuid.UUID,
        kind: EventKind | str,
        payload: dict[str, Any],
        execution_id: uuid.UUID | None = None,
    ) -> None:
        run = await self.session.get(Run, run_id)
        if run is None:
            return
        await self._append_event(run, kind, payload, execution_id)

    async def get_run(self, run_id: uuid.UUID) -> RunState | None:
        run = await self.session.scalar(
            select(Run)
            .options(selectinload(Run.chat).selectinload(Chat.project))
            .where(Run.id == run_id)
        )
        return _run_state(run) if run is not None else None

    async def get_run_model(self, run_id: uuid.UUID) -> Run | None:
        return await self.session.scalar(
            select(Run)
            .options(selectinload(Run.chat).selectinload(Chat.project))
            .where(Run.id == run_id)
        )

    async def get_owned_run(self, owner_id: uuid.UUID, run_id: uuid.UUID) -> Run | None:
        return await self.session.scalar(
            select(Run)
            .join(Chat, Chat.id == Run.chat_id)
            .join(Project, Project.id == Chat.project_id)
            .options(selectinload(Run.chat).selectinload(Chat.project))
            .where(Run.id == run_id, Project.owner_id == owner_id)
        )

    async def get_owned_run_for_update(self, owner_id: uuid.UUID, run_id: uuid.UUID) -> Run | None:
        return await self.session.scalar(
            select(Run)
            .join(Chat, Chat.id == Run.chat_id)
            .join(Project, Project.id == Chat.project_id)
            .options(selectinload(Run.chat).selectinload(Chat.project))
            .where(Run.id == run_id, Project.owner_id == owner_id)
            .with_for_update(of=Run)
        )

    async def close_run(self, owner_id: uuid.UUID, run_id: uuid.UUID) -> Run | None:
        run = await self.get_owned_run_for_update(owner_id, run_id)
        if run is None:
            return None
        if run.status != RunStatus.AWAITING_USER:
            raise RunConflictError("Only awaiting_user runs can be closed")
        reply = "Run closed by the user. The generated files and unresolved issues are preserved."
        run.status = RunStatus.FAILED
        run.chat_reply = reply
        run.locked_by = None
        run.locked_at = None
        run.heartbeat_at = None
        await self._append_event(run, EventKind.RUN_CLOSED, {"message": reply})
        await self._new_message(
            run.chat_id,
            MessageSender.ASSISTANT,
            reply,
            uuid.uuid5(uuid.NAMESPACE_URL, f"assistant:{run_id}:closed"),
            run_id=run_id,
        )
        run.chat.updated_at = _now()
        await self.session.commit()
        return await self.get_owned_run(owner_id, run_id)

    async def update_run(self, run_id: uuid.UUID, **fields: Any) -> RunState | None:
        if fields:
            await self.session.execute(update(Run).where(Run.id == run_id).values(**fields))
            await self.session.commit()
        return await self.get_run(run_id)

    async def finalize_run(
        self,
        run_id: uuid.UUID,
        status: RunStatus,
        reply: str,
        event_kind: EventKind | str,
        event_payload: dict[str, Any],
        **fields: Any,
    ) -> bool:
        run = await self.session.scalar(
            select(Run).options(selectinload(Run.chat)).where(Run.id == run_id).with_for_update()
        )
        if run is None or run.status != RunStatus.RUNNING:
            return False
        for name, value in fields.items():
            setattr(run, name, value)
        run.status = status
        run.chat_reply = reply
        await self._append_event(run, event_kind, event_payload)
        key = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"assistant:{run_id}:{run.execution_id}:{status.value}",
        )
        await self._new_message(run.chat_id, MessageSender.ASSISTANT, reply, key, run_id=run.id)
        run.chat.updated_at = _now()
        await self.session.commit()
        return True

    async def add_chat_message(
        self,
        chat_id: uuid.UUID,
        content: str,
        role: MessageSender,
        run_id: uuid.UUID | None = None,
        idempotency_key: uuid.UUID | None = None,
    ) -> ChatMessage:
        chat = await self.session.get(Chat, chat_id)
        if chat is None:
            raise ValueError("chat does not exist")
        message = await self._new_message(
            chat_id, role, content, idempotency_key or uuid.uuid4(), run_id=run_id
        )
        chat.updated_at = _now()
        await self.session.flush()
        return message

    async def add_plan(self, run_id: uuid.UUID, plan: Plan) -> None:
        await self.session.execute(
            update(Run).where(Run.id == run_id).values(plan=plan_payload(plan))
        )
        await self.session.flush()

    async def list_events(self, run_id: uuid.UUID, after: int | None = None) -> list[RunEvent]:
        stmt = select(RunEvent).where(RunEvent.run_id == run_id)
        if after is not None:
            stmt = stmt.where(RunEvent.sequence > after)
        return list((await self.session.execute(stmt.order_by(RunEvent.sequence))).scalars())

    async def _active_run(self, chat_id: uuid.UUID) -> Run | None:
        return await self.session.scalar(
            select(Run).where(Run.chat_id == chat_id, Run.status.in_(list(ACTIVE_STATUSES)))
        )

    async def active_run_for_chat(self, chat_id: uuid.UUID) -> Run | None:
        return await self._active_run(chat_id)

    async def latest_run_for_chat(self, chat_id: uuid.UUID) -> Run | None:
        return await self.session.scalar(
            select(Run).where(Run.chat_id == chat_id).order_by(Run.created_at.desc()).limit(1)
        )

    async def count_runs_for_chat(self, chat_id: uuid.UUID) -> int:
        return int(
            (
                await self.session.execute(
                    select(func.count()).select_from(Run).where(Run.chat_id == chat_id)
                )
            ).scalar_one()
        )

    async def list_runs_for_chat(self, owner_id: uuid.UUID, chat_id: uuid.UUID) -> list[Run]:
        return list(
            (
                await self.session.execute(
                    select(Run)
                    .join(Chat, Chat.id == Run.chat_id)
                    .join(Project, Project.id == Chat.project_id)
                    .options(selectinload(Run.chat))
                    .where(Chat.id == chat_id, Project.owner_id == owner_id)
                    .order_by(Run.created_at.desc())
                )
            ).scalars()
        )

    async def list_messages_for_chat(
        self, owner_id: uuid.UUID, chat_id: uuid.UUID, after: int | None = None
    ) -> list[ChatMessage]:
        stmt = (
            select(ChatMessage)
            .join(Chat, Chat.id == ChatMessage.chat_id)
            .join(Project, Project.id == Chat.project_id)
            .options(selectinload(ChatMessage.run))
            .where(Chat.id == chat_id, Project.owner_id == owner_id)
        )
        if after is not None:
            stmt = stmt.where(ChatMessage.sequence > after)
        return list((await self.session.execute(stmt.order_by(ChatMessage.sequence))).scalars())

    async def list_chat_messages(self, chat_id: uuid.UUID) -> list[ChatMessage]:
        return list(
            (
                await self.session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.chat_id == chat_id)
                    .order_by(ChatMessage.sequence)
                )
            ).scalars()
        )

    async def get_owned_chat(self, owner_id: uuid.UUID, chat_id: uuid.UUID) -> Chat | None:
        return await self.session.scalar(
            select(Chat)
            .join(Project, Project.id == Chat.project_id)
            .options(selectinload(Chat.project))
            .where(Chat.id == chat_id, Project.owner_id == owner_id)
        )

    async def get_owned_chat_for_update(
        self, owner_id: uuid.UUID, chat_id: uuid.UUID
    ) -> Chat | None:
        return await self._owned_chat_for_update(owner_id, chat_id)

    async def get_owned_chat_for_project_for_update(
        self, owner_id: uuid.UUID, project_id: uuid.UUID
    ) -> Chat | None:
        return await self.session.scalar(
            select(Chat)
            .join(Project, Project.id == Chat.project_id)
            .options(selectinload(Chat.project))
            .where(Chat.project_id == project_id, Project.owner_id == owner_id)
            .with_for_update(of=Chat)
        )

    async def _owned_chat_for_update(self, owner_id: uuid.UUID, chat_id: uuid.UUID) -> Chat | None:
        return await self.session.scalar(
            select(Chat)
            .join(Project, Project.id == Chat.project_id)
            .options(selectinload(Chat.project))
            .where(Chat.id == chat_id, Project.owner_id == owner_id)
            .with_for_update(of=Chat)
        )

    async def list_chats(self, owner_id: uuid.UUID) -> list[Chat]:
        return list(
            (
                await self.session.execute(
                    select(Chat)
                    .join(Project, Project.id == Chat.project_id)
                    .options(selectinload(Chat.project))
                    .where(Project.owner_id == owner_id)
                    .order_by(Chat.updated_at.desc())
                )
            ).scalars()
        )

    async def rename_chat(self, owner_id: uuid.UUID, chat_id: uuid.UUID, title: str) -> Chat | None:
        chat = await self._owned_chat_for_update(owner_id, chat_id)
        if chat is None:
            return None
        chat.project.title = title
        chat.updated_at = _now()
        await self.session.commit()
        return chat

    async def delete_chat(self, owner_id: uuid.UUID, chat_id: uuid.UUID) -> uuid.UUID | None:
        chat = await self._owned_chat_for_update(owner_id, chat_id)
        if chat is None:
            return None
        if await self._active_run(chat_id) is not None:
            raise RunConflictError("A chat with a non-terminal run cannot be deleted")
        project_id = chat.project_id
        await self.session.execute(delete(Project).where(Project.id == project_id))
        await self.session.commit()
        return project_id

    async def get_run_statuses(
        self, owner_id: uuid.UUID, run_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, RunStatus]:
        if not run_ids:
            return {}
        rows = await self.session.execute(
            select(Run.id, Run.status)
            .join(Chat, Chat.id == Run.chat_id)
            .join(Project, Project.id == Chat.project_id)
            .where(Project.owner_id == owner_id, Run.id.in_(run_ids))
        )
        return {run_id: run_status for run_id, run_status in rows}

    async def get_artifact(self, artifact_id: uuid.UUID) -> Artifact | None:
        return await self.session.get(Artifact, artifact_id)

    async def list_artifact_ids_for_run(self, run_id: uuid.UUID) -> list[uuid.UUID]:
        return list(
            (
                await self.session.execute(select(Artifact.id).where(Artifact.run_id == run_id))
            ).scalars()
        )

    async def artifacts_for_run(
        self, run_id: uuid.UUID, execution_id: uuid.UUID | None = None
    ) -> list[Artifact]:
        stmt = select(Artifact).where(Artifact.run_id == run_id)
        if execution_id is not None:
            stmt = stmt.where(Artifact.execution_id == execution_id)
        return list(
            (
                await self.session.execute(stmt.order_by(Artifact.created_at, Artifact.file_path))
            ).scalars()
        )

    async def add_artifact(
        self,
        run_id: uuid.UUID,
        execution_id: uuid.UUID,
        file_path: str,
        file_type: FileType,
        checksum: str,
    ) -> Artifact:
        artifact = Artifact(
            run_id=run_id,
            execution_id=execution_id,
            file_path=file_path,
            file_type=file_type,
            checksum=checksum,
        )
        self.session.add(artifact)
        await self.session.flush()
        return artifact

    async def ensure_artifacts_for_run(
        self, run_id: uuid.UUID, execution_id: uuid.UUID, code: list[dict[str, Any]]
    ) -> dict[str, uuid.UUID]:
        existing = await self.artifacts_for_run(run_id, execution_id=execution_id)
        by_path = {artifact.file_path: artifact.id for artifact in existing}
        for item in code:
            if item["file_path"] not in by_path:
                file_type = FileType(item["file_path"].rsplit(".", 1)[-1].lower())
                artifact = await self.add_artifact(
                    run_id, execution_id, item["file_path"], file_type, item["checksum"]
                )
                by_path[item["file_path"]] = artifact.id
        return by_path

    async def completed_runs_to_archive(self, cutoff: datetime, limit: int = 50) -> list[Run]:
        stmt = (
            select(Run)
            .join(Chat, Chat.id == Run.chat_id)
            .options(selectinload(Run.chat))
            .where(
                Run.status == RunStatus.COMPLETED,
                Run.archived.is_(False),
                Run.updated_at < cutoff,
            )
            .order_by(Run.updated_at)
            .with_for_update(skip_locked=True, of=Run)
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def mark_run_archived(self, run_id: uuid.UUID) -> None:
        await self.session.execute(
            update(Run)
            .where(Run.id == run_id, Run.status == RunStatus.COMPLETED)
            .values(archived=True, updated_at=Run.updated_at)
        )
        await self.session.flush()

    async def terminal_checkpoint_threads(self, cutoff: datetime) -> list[str]:
        rows = await self.session.execute(
            select(Run.id, RunEvent.execution_id)
            .join(RunEvent, RunEvent.run_id == Run.id)
            .where(Run.status.in_(list(TERMINAL_STATUSES)), Run.updated_at < cutoff)
            .distinct()
        )
        return [f"{run_id}:{execution_id}" for run_id, execution_id in rows if execution_id]

    async def delete_expired_rate_limits(self, cutoff: datetime) -> int:
        result = await self.session.execute(
            delete(RateLimitCounter).where(RateLimitCounter.expires_at < cutoff)
        )
        return result.rowcount or 0

    async def delete_stale_worker_heartbeats(self, cutoff: datetime) -> int:
        result = await self.session.execute(
            delete(WorkerHeartbeat).where(WorkerHeartbeat.last_seen_at < cutoff)
        )
        return result.rowcount or 0

    async def delete_run(self, run_id: uuid.UUID) -> None:
        await self.session.execute(
            update(ChatMessage).where(ChatMessage.run_id == run_id).values(run_id=None)
        )
        await self.session.execute(delete(Run).where(Run.id == run_id))
        await self.session.commit()

    async def get_project(self, project_id: uuid.UUID) -> Project | None:
        return await self.session.scalar(
            select(Project).options(selectinload(Project.chat)).where(Project.id == project_id)
        )

    async def get_owned_project(self, project_id: uuid.UUID, owner_id: uuid.UUID) -> Project | None:
        return await self.session.scalar(
            select(Project)
            .options(selectinload(Project.chat))
            .where(Project.id == project_id, Project.owner_id == owner_id)
        )

    async def get_project_context(self, project_id: uuid.UUID) -> ProjectContext | None:
        project = await self.get_project(project_id)
        return _project_context(project) if project else None

    async def get_project_by_preview(self, preview_id: uuid.UUID) -> Project | None:
        return await self.session.scalar(select(Project).where(Project.preview_id == preview_id))

    async def list_projects(self, owner_id: uuid.UUID) -> list[Project]:
        return list(
            (
                await self.session.execute(
                    select(Project)
                    .options(selectinload(Project.chat))
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

    async def list_artifact_ids_for_project(self, project_id: uuid.UUID) -> list[uuid.UUID]:
        return list(
            (
                await self.session.execute(
                    select(Artifact.id)
                    .join(Run, Run.id == Artifact.run_id)
                    .join(Chat, Chat.id == Run.chat_id)
                    .where(Chat.project_id == project_id)
                )
            ).scalars()
        )

    async def claim(self, worker_id: str, stale_after_seconds: int = 60) -> RunState | None:
        now = _now()
        stale = Run.heartbeat_at < (now - timedelta(seconds=stale_after_seconds))
        run = await self.session.scalar(
            select(Run)
            .where(
                (Run.status == RunStatus.PENDING)
                | ((Run.status == RunStatus.RUNNING) & (Run.heartbeat_at.is_(None) | stale))
            )
            .order_by(Run.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if run is None:
            await self.session.commit()
            return None
        run.status = RunStatus.RUNNING
        run.locked_by = worker_id
        run.locked_at = now
        run.heartbeat_at = now
        await self.heartbeat(worker_id, run.id)
        await self.session.commit()
        return await self.get_run(run.id)

    async def heartbeat(self, worker_id: str, run_id: uuid.UUID | None) -> None:
        await self.session.execute(
            pg_insert(WorkerHeartbeat)
            .values(worker_id=worker_id, current_run_id=run_id, last_seen_at=_now())
            .on_conflict_do_update(
                index_elements=[WorkerHeartbeat.worker_id],
                set_={"current_run_id": run_id, "last_seen_at": _now()},
            )
        )
        if run_id is not None:
            await self.session.execute(
                update(Run).where(Run.id == run_id).values(heartbeat_at=_now())
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
        return await self.session.scalar(select(func.max(WorkerHeartbeat.last_seen_at)))


__all__ = ["SqlAlchemyRepository"]
