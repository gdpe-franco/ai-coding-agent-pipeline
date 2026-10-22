from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from app.domain.enums import (
    ALLOWED_FILE_TYPES,
    ConversationSender,
    EventKind,
    FileType,
    ProjectStatus,
    ReviewerVerdict,
    RunStatus,
)

_PATH_RE = re.compile(r"^[A-Za-z0-9_\-./]+$")


@dataclass(slots=True)
class Issue:
    message: str
    file_path: str | None = None


@dataclass(slots=True)
class PlanStep:
    name: str
    file_path: str
    file_type: FileType
    description: str
    id: UUID = field(default_factory=uuid4)
    artifact_ids: list[UUID] = field(default_factory=list)
    created_at: datetime | None = None


@dataclass(slots=True)
class Plan:
    project_id: UUID
    summary: str
    steps: list[PlanStep]
    id: UUID = field(default_factory=uuid4)
    created_at: datetime | None = None


@dataclass(slots=True)
class Code:
    run_id: UUID
    file_path: str
    content: str
    checksum: str
    file_type: str = FileType.HTML.value
    id: UUID = field(default_factory=uuid4)
    created_at: datetime | None = None


@dataclass(slots=True)
class CheckReport:
    file_structure_ok: bool
    js_syntax_ok: bool
    files_checked: int = 0
    syntax_checked: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.file_structure_ok and self.js_syntax_ok


@dataclass(slots=True)
class Feedback:
    verdict: ReviewerVerdict
    summary: str
    issues: list[Issue] = field(default_factory=list)
    suggested_code: list[Code] = field(default_factory=list)
    created_at: datetime | None = None


@dataclass(slots=True)
class RunState:
    id: UUID
    owner_id: UUID
    project_id: UUID | None
    message: str
    status: RunStatus
    attempts_used: int = 0
    attempts_total: int = 0
    round: int = 1
    user_guidance: str | None = None
    plan: dict | None = None
    latest_code: list[dict] = field(default_factory=list)
    feedback: dict | None = None
    checks: dict | None = None
    chat_reply: str | None = None
    error: str | None = None


def validate_relative_path(path: str) -> str:
    if not path or path.startswith("/") or ".." in path or "\\" in path:
        raise ValueError("file path must be relative and must not contain '..'")
    if not _PATH_RE.match(path):
        raise ValueError("file path contains unsupported characters")
    return path


def validate_file_type(file_type: str) -> str:
    value = file_type.lower()
    if value not in ALLOWED_FILE_TYPES:
        raise ValueError(f"file type is not allowed: {file_type}")
    return value


__all__ = [
    "CheckReport",
    "Code",
    "ConversationSender",
    "EventKind",
    "Feedback",
    "Issue",
    "Plan",
    "PlanStep",
    "ProjectStatus",
    "ReviewerVerdict",
    "RunState",
    "RunStatus",
    "validate_file_type",
    "validate_relative_path",
]
