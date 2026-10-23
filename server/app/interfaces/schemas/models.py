from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.domain.enums import ChatRequestStatus, RunStatus


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
    guidance: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
    ]


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
    run_status: RunStatus | None = None
    request_status: ChatRequestStatus | None = None
    created_at: datetime


class ProjectOptionRead(BaseModel):
    id: UUID
    title: str


class PendingSelectionRead(BaseModel):
    turn_id: UUID
    context_version: int
    projects: list[ProjectOptionRead]
    reason: str | None = None


class ChatRead(BaseModel):
    conversation_id: UUID
    context_version: int
    selected_project_id: UUID | None = None
    active_feature_run_id: UUID | None = None
    pending_selection: PendingSelectionRead | None = None
    turns: list[ConversationTurnRead]


class ChatMessageCreate(BaseModel):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]
    request_selection: bool = False


class ChatMessageCreated(BaseModel):
    turn_id: UUID


class ChatSelectionCreate(BaseModel):
    context_version: int = Field(ge=0)
    choice: Literal["continue_current", "existing_project", "new_project"]
    feature_mode: Literal["continue", "new"]
    project_id: UUID | None = None
    title: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
        | None
    ) = None

    @model_validator(mode="after")
    def valid_choice(self) -> ChatSelectionCreate:
        if self.choice == "continue_current":
            valid = (
                self.feature_mode == "continue" and self.project_id is None and self.title is None
            )
        elif self.choice == "existing_project":
            valid = self.project_id is not None and self.title is None
        else:
            valid = (
                self.feature_mode == "new" and self.project_id is None and self.title is not None
            )
        if not valid:
            raise ValueError("selection fields do not match the chosen action")
        return self


class ChatSelectionCreated(BaseModel):
    run_id: UUID
    project_id: UUID
    context_version: int


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
