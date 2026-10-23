from __future__ import annotations

from app.domain.enums import RunStatus
from app.domain.models import CheckReport, Feedback


def generation_budget_exhausted(generations: int, max_generations: int) -> bool:
    return generations >= max_generations


def requires_handoff(
    feedback: Feedback, generations: int, max_generations: int, checks: CheckReport
) -> bool:
    if feedback.verdict.value == "escalate":
        return True
    if feedback.verdict.value == "approve" and checks.ok:
        return False
    return generation_budget_exhausted(generations, max_generations)


def is_approved(feedback: Feedback, checks: CheckReport) -> bool:
    return feedback.verdict.value == "approve" and checks.ok


def can_continue(status: RunStatus) -> bool:
    return status == RunStatus.AWAITING_USER
