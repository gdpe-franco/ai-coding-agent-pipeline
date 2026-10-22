from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from app.domain.enums import FileType
from app.domain.models import Code, Feedback, Plan, PlanStep


def plan_payload(plan: Plan) -> dict[str, Any]:
    return {
        "id": str(plan.id),
        "project_id": str(plan.project_id),
        "summary": plan.summary,
        "steps": [
            {
                "id": str(step.id),
                "name": step.name,
                "file_path": step.file_path,
                "file_type": step.file_type.value,
                "description": step.description,
            }
            for step in plan.steps
        ],
    }


def plan_from_payload(payload: dict[str, Any]) -> Plan:
    return Plan(
        id=UUID(payload["id"]),
        project_id=UUID(payload["project_id"]),
        summary=payload.get("summary", ""),
        steps=[
            PlanStep(
                id=UUID(item["id"]),
                name=item["name"],
                file_path=item["file_path"],
                file_type=FileType(item["file_type"]),
                description=item["description"],
            )
            for item in payload.get("steps", [])
        ],
    )


def code_payload(code: Code) -> dict[str, Any]:
    return {
        "file_path": code.file_path,
        "content": code.content,
        "checksum": code.checksum,
        "file_type": code.file_type,
    }


def checksum(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def feedback_payload(feedback: Feedback) -> dict[str, Any]:
    return {
        "verdict": feedback.verdict.value,
        "summary": feedback.summary,
        "issues": [
            {"message": issue.message, "file_path": issue.file_path} for issue in feedback.issues
        ],
    }
