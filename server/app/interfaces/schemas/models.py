from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=2000)


class ProjectRead(BaseModel):
    id: UUID
    title: str
    summary: str
    preview_id: UUID
    status: str
    created_at: datetime
    updated_at: datetime


class FileList(BaseModel):
    files: list[str]


class RunCreate(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    project_id: UUID | None = None


class RunCreated(BaseModel):
    run_id: UUID
    project_id: UUID | None


class RunRead(BaseModel):
    id: UUID
    project_id: UUID | None
    message: str
    status: str
    attempts_used: int
    attempts_total: int
    round: int
    user_guidance: str | None = None
    plan: dict[str, Any] | None = None
    latest_code: list[dict[str, Any]] = Field(default_factory=list)
    feedback: dict[str, Any] | None = None
    checks: dict[str, Any] | None = None
    chat_reply: str | None = None
    error: str | None = None


class ResumeRequest(BaseModel):
    guidance: str = Field(min_length=1, max_length=4000)


class EventRead(BaseModel):
    id: UUID
    kind: str
    payload: dict[str, Any]
    created_at: datetime


class ArtifactRead(BaseModel):
    id: UUID
    file_path: str
    file_type: str
    checksum: str
    content: str


class ConversationTurnRead(BaseModel):
    id: UUID
    role: str
    content: str
    sequence: int
    project_id: UUID | None = None
    run_id: UUID | None = None
    created_at: datetime


class ChatRead(BaseModel):
    conversation_id: UUID
    context_version: int
    selected_project_id: UUID | None = None
    active_feature_run_id: UUID | None = None
    pending_selection: dict[str, Any] | None = None
    turns: list[ConversationTurnRead]


class ChatMessageCreate(BaseModel):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]


class ChatMessageCreated(BaseModel):
    turn_id: UUID


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
