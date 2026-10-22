from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from app.domain.enums import ConversationSender, EventKind, RunStatus
from app.domain.models import CheckReport, Feedback, Plan, ProjectContext, RunState


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

    async def delete_project(self, project_id: UUID) -> None: ...

    async def check_readable(self, project_id: UUID, paths: list[str]) -> CheckReport: ...


class ConversationTurnContext(Protocol):
    role: ConversationSender
    content: str


class RunRepository(Protocol):
    async def commit(self) -> None: ...

    async def get_run(self, run_id: UUID) -> RunState | None: ...

    async def get_or_create_graph_run(self, run_id: UUID, round: int, thread_id: str) -> UUID: ...

    async def get_project_context(self, project_id: UUID) -> ProjectContext | None: ...

    async def get_owned_project_context(
        self, project_id: UUID, owner_id: UUID
    ) -> ProjectContext | None: ...

    async def create_project_context(
        self, owner_id: UUID, title: str, summary: str = ""
    ) -> ProjectContext: ...

    async def project_index(self, owner_id: UUID) -> list[dict[str, Any]]: ...

    async def recent_runs(self, owner_id: UUID, limit: int = 10) -> list[dict[str, Any]]: ...

    async def list_conversation_turns(
        self, owner_id: UUID, project_id: UUID | None = None
    ) -> list[ConversationTurnContext]: ...

    async def create_run(
        self,
        owner_id: UUID,
        message: str,
        project_id: UUID | None,
    ) -> RunState: ...

    async def update_run(self, run_id: UUID, **fields: Any) -> RunState | None: ...

    async def append_event(
        self, run_id: UUID, kind: EventKind, payload: dict[str, Any]
    ) -> None: ...

    async def add_review(self, run_id: UUID, feedback: Feedback) -> None: ...

    async def add_plan(self, run_id: UUID, plan: Plan) -> None: ...

    async def add_graph_event(
        self, graph_run_id: UUID, node: str, payload: dict[str, Any]
    ) -> None: ...

    async def associate_unassigned_turns_with_project(
        self, run_id: UUID, project_id: UUID
    ) -> None: ...

    async def ensure_artifacts_for_run(
        self, run_id: UUID, code: list[dict[str, Any]]
    ) -> dict[str, UUID]: ...

    async def heartbeat(self, worker_id: str, run_id: UUID | None) -> None: ...

    async def release(self, run_id: UUID) -> None: ...

    async def add_conversation_turn(
        self,
        owner_id: UUID,
        content: str,
        role: ConversationSender,
        run_id: UUID | None = None,
        project_id: UUID | None = None,
    ) -> ConversationTurnContext: ...

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
    async def load_project_memory(self, owner_id: UUID, project_id: UUID) -> str: ...

    async def project_index(self, owner_id: UUID) -> list[dict[str, Any]]: ...

    async def recent_runs(self, owner_id: UUID, limit: int = 10) -> list[dict[str, Any]]: ...

    async def save_round(
        self,
        owner_id: UUID,
        project_id: UUID,
        title: str | None,
        outcome: str,
        summary: str = "",
        last_feature: str = "",
    ) -> None: ...

    async def delete_project(self, owner_id: UUID, project_id: UUID) -> None: ...


class RateLimiter(Protocol):
    async def consume(self, bucket: str, key: str) -> RateLimitResult: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


__all__ = [
    "Clock",
    "ConversationTurnContext",
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
