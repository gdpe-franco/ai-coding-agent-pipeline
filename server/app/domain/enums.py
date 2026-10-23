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


class ConversationSender(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ChatRequestStatus(StrEnum):
    RECEIVED = "received"
    CLASSIFYING = "classifying"
    NEEDS_SELECTION = "needs_selection"
    ENQUEUED = "enqueued"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ChatIntentAction(StrEnum):
    CONTINUE_CURRENT = "continue_current"
    REQUEST_SELECTION = "request_selection"


class EventKind(StrEnum):
    PROJECT_RESOLVED = "project_resolved"
    PLANNER = "planner"
    CODER = "coder"
    REVIEWER = "reviewer"
    CHAT_REPLY = "chat_reply"
    HANDOFF = "handoff"
    GUIDANCE = "guidance"
    RUN_START = "run_start"
    RUN_END = "run_end"
    ERROR = "error"
    ABANDONMENT = "abandonment"


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
