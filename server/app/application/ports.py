from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from app.domain.enums import EventKind, MessageSender, RunStatus
from app.domain.models import (
    CheckReport,
    Plan,
    ProjectContext,
    RunState,
)


@dataclass(slots=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_at: datetime
    retry_after: int


class LLMClient(Protocol):
    async def complete(
        self, role: str, messages: list[dict[str, str]], schema: dict[str, Any]
    ) -> dict[str, Any]: ...


class Workspace(Protocol):
    async def list_files(self, project_id: UUID) -> list[str]: ...

    async def read_file(self, project_id: UUID, path: str) -> str: ...

    async def read_all(self, project_id: UUID) -> dict[str, str]: ...

    async def write_file(
        self, project_id: UUID, path: str, content: str, allowed_paths: set[str]
    ) -> None: ...

    async def publish(
        self, project_id: UUID, files: dict[str, str], artifact_ids: dict[str, UUID]
    ) -> None: ...

    async def write_artifact(self, artifact_id: UUID, path: str, content: str) -> None: ...

    async def delete_artifact(self, artifact_id: UUID) -> None: ...

    async def read_artifact(self, artifact_id: UUID, path: str) -> str: ...

    async def read_preview(self, project_id: UUID, path: str) -> bytes: ...

    async def archive_run(self, project_id: UUID, run_id: UUID, files: dict[str, str]) -> None: ...

    async def delete_run_archive(self, project_id: UUID, run_id: UUID) -> None: ...

    async def delete_project(self, project_id: UUID) -> None: ...

    async def check_readable(self, project_id: UUID, paths: list[str]) -> CheckReport: ...


class ChatMessageContext(Protocol):
    role: MessageSender
    content: str


class RunRepository(Protocol):
    async def commit(self) -> None: ...

    async def get_run(self, run_id: UUID) -> RunState | None: ...

    async def get_project_context(self, project_id: UUID) -> ProjectContext | None: ...

    async def list_chat_messages(self, chat_id: UUID) -> list[ChatMessageContext]: ...

    async def update_run(self, run_id: UUID, **fields: Any) -> RunState | None: ...

    async def finalize_run(
        self,
        run_id: UUID,
        status: RunStatus,
        reply: str,
        event_kind: EventKind | str,
        event_payload: dict[str, Any],
        **fields: Any,
    ) -> bool: ...

    async def append_event(
        self,
        run_id: UUID,
        kind: EventKind | str,
        payload: dict[str, Any],
        execution_id: UUID | None = None,
    ) -> None: ...

    async def add_plan(self, run_id: UUID, plan: Plan) -> None: ...

    async def ensure_artifacts_for_run(
        self, run_id: UUID, execution_id: UUID, code: list[dict[str, Any]]
    ) -> dict[str, UUID]: ...

    async def heartbeat(self, worker_id: str, run_id: UUID | None) -> None: ...

    async def release(self, run_id: UUID) -> None: ...

    async def add_chat_message(
        self,
        chat_id: UUID,
        content: str,
        role: MessageSender,
        run_id: UUID | None = None,
        idempotency_key: UUID | None = None,
    ) -> ChatMessageContext: ...

    async def update_project_summary(self, project_id: UUID, summary: str) -> None: ...


class RepositoryFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[RunRepository]: ...


class RunQueue(Protocol):
    async def claim(self, worker_id: str, stale_after_seconds: int = 60) -> RunState | None: ...

    async def heartbeat(self, worker_id: str, run_id: UUID | None) -> None: ...

    async def release(self, run_id: UUID) -> None: ...


class EventSink(Protocol):
    async def emit(self, run_id: UUID, kind: EventKind, payload: dict[str, Any]) -> None: ...


class MemoryStore(Protocol):
    async def load_project_memory(self, project_id: UUID) -> str: ...

    async def save_project_memory(self, project_id: UUID, summary: str) -> None: ...

    async def delete_project(self, project_id: UUID) -> None: ...


class RateLimiter(Protocol):
    async def consume(self, bucket: str, key: str) -> RateLimitResult: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


__all__ = [
    "ChatMessageContext",
    "Clock",
    "EventSink",
    "LLMClient",
    "MemoryStore",
    "RateLimitResult",
    "RateLimiter",
    "RepositoryFactory",
    "RunQueue",
    "RunRepository",
    "RunStatus",
    "Workspace",
]
