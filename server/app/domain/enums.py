from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_USER = "awaiting_user"
    COMPLETED = "completed"
    FAILED = "failed"


TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED}
ACTIVE_STATUSES = {RunStatus.PENDING, RunStatus.RUNNING, RunStatus.AWAITING_USER}


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class ReviewerVerdict(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    ESCALATE = "escalate"


class MessageSender(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class EventKind(StrEnum):
    RUN_QUEUED = "run_queued"
    EXECUTION_STARTED = "execution_started"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    GENERATION = "generation"
    HANDOFF = "handoff"
    GUIDANCE = "guidance"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_CLOSED = "run_closed"
    ARTIFACT_PUBLISHED = "artifact_published"


class FileType(StrEnum):
    HTML = "html"
    CSS = "css"
    JS = "js"
    MD = "md"
    JSON = "json"
    SVG = "svg"
    PNG = "png"
    ICO = "ico"


ALLOWED_FILE_TYPES = {item.value for item in FileType}
