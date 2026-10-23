from __future__ import annotations

import json
from typing import Any

from app.domain.models import CheckReport, Plan


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def planner_messages(
    message: str,
    memory: str,
    chat_history: list[dict[str, str]],
    guidance: str | None,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are the Planner for a static web app generator. Produce 1-8 files that satisfy "
                "the request. Each step is one complete file. Use only html, css, js, md, json, svg, "
                "png, or ico. Paths must be relative, use forward slashes, and contain no '..'. "
                "Keep the app self-contained with relative local asset paths. Prefer a working "
                "single-page result over unnecessary files."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Request:\n{message}\n\nGuidance:\n{guidance or 'None'}\n\n"
                f"Project chat history:\n{_json(chat_history)}\n\nProject memory:\n{memory or 'None'}"
            ),
        },
    ]


def coder_messages(
    step_name: str,
    file_path: str,
    file_type: str,
    plan_summary: str,
    description: str,
    existing_files: dict[str, str],
    memory: str,
    guidance: str | None,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are the Coder. Write one complete file, never a patch. Return the same "
                "file_path you were given. The content must be the full final file. Obey the "
                "requested file type and keep local assets relative."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Plan summary: {plan_summary}\n\n"
                f"File step: {step_name}\nPath: {file_path}\nType: {file_type}\n"
                f"Description: {description}\n\nGuidance:\n{guidance or 'None'}\n\n"
                f"Existing project files:\n{_json(existing_files)}\n\nProject memory:\n{memory or 'None'}"
            ),
        },
    ]


def reviewer_messages(
    plan: Plan, code: list[dict[str, Any]], checks: CheckReport, memory: str
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are the Reviewer. Decide whether the generated static app satisfies the plan. "
                "Deterministic checks already ran; approval is impossible when they fail. Return "
                "approve, reject, or escalate. Reject with concrete issues the Coder can fix. "
                "Escalate only when the request or plan is fundamentally ambiguous or unsafe."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Plan:\n{_json(plan)}\n\nGenerated files:\n{_json(code)}\n\n"
                f"Deterministic checks:\n{_json(checks)}\n\nProject memory:\n{memory or 'None'}"
            ),
        },
    ]
