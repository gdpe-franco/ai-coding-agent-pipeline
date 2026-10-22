from __future__ import annotations

from app.domain.enums import TERMINAL_STATUSES, RunStatus
from app.domain.models import CheckReport, Feedback


def attempts_exhausted(attempts_used: int, max_attempts: int) -> bool:
    return attempts_used >= max_attempts


def requires_handoff(
    feedback: Feedback, attempts_used: int, max_attempts: int, checks: CheckReport
) -> bool:
    if feedback.verdict.value == "escalate":
        return True
    if feedback.verdict.value == "approve" and checks.ok:
        return False
    return attempts_exhausted(attempts_used, max_attempts)


def is_approved(feedback: Feedback, checks: CheckReport) -> bool:
    return feedback.verdict.value == "approve" and checks.ok


def can_resume(status: RunStatus) -> bool:
    return status == RunStatus.AWAITING_USER


def can_close(status: RunStatus) -> bool:
    return status == RunStatus.AWAITING_USER


def can_delete(status: RunStatus) -> bool:
    return status in TERMINAL_STATUSES
