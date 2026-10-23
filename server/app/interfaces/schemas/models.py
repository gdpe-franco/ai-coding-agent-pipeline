from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from app.domain.enums import RunStatus


class ProjectRead(BaseModel):
    id: UUID
    chat_id: UUID
    title: str
    summary: str
    preview_id: UUID
    preview_ready: bool
    status: str
    created_at: datetime
    updated_at: datetime


class FileList(BaseModel):
    files: list[str]


class ChatCreate(BaseModel):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]


class ChatCreated(BaseModel):
    chat_id: UUID
    project_id: UUID
    run_id: UUID


class ChatRead(BaseModel):
    id: UUID
    project_id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ChatRename(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class ChatMessageCreate(BaseModel):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]


class ChatMessageRead(BaseModel):
    id: UUID
    chat_id: UUID
    run_id: UUID | None = None
    role: str
    content: str
    sequence: int
    run_status: RunStatus | None = None
    created_at: datetime


class ChatMessageCreated(BaseModel):
    message_id: UUID
    run_id: UUID
    status: RunStatus


class RunRead(BaseModel):
    id: UUID
    chat_id: UUID
    project_id: UUID
    execution_id: UUID
    message: str
    status: str
    user_guidance: str | None = None
    plan: dict[str, Any] | None = None
    latest_code: list[dict[str, Any]] = Field(default_factory=list)
    feedback: dict[str, Any] | None = None
    checks: dict[str, Any] | None = None
    chat_reply: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class ContinuationRequest(BaseModel):
    guidance: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
    ]


class EventRead(BaseModel):
    id: UUID
    sequence: int
    execution_id: UUID | None = None
    kind: str
    payload: dict[str, Any]
    created_at: datetime


class ArtifactRead(BaseModel):
    id: UUID
    execution_id: UUID
    file_path: str
    file_type: str
    checksum: str
    content: str


class RunArtifactRead(BaseModel):
    id: UUID
    execution_id: UUID
    file_path: str
    file_type: str
    checksum: str
    created_at: datetime


class PreviewUrlRead(BaseModel):
    url: str


class HealthChecks(BaseModel):
    database: str
    filesystem: str
    worker: str


class HealthRead(BaseModel):
    status: str
    database: str
    filesystem: str
    worker: str
    checks: HealthChecks
