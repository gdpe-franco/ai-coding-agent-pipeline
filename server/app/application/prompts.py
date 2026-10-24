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
                "single-page result over unnecessary files. Also suggest a concise 2–6 word project "
                "title in the user's language. Name the overall app/project, not just this change; "
                "return null if no useful title is clear."
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
    feedback: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    review = (
        "Previous Reviewer feedback (address these issues while preserving working behavior):\n"
        f"{_json(feedback)}\n\n"
        if feedback
        else ""
    )
    return [
        {
            "role": "system",
            "content": (
                "You are the Coder. Write one complete file, never a patch. Return the same "
                "file_path you were given. The content must be the full final file. Obey the "
                "requested file type and keep local assets relative. When previous Reviewer "
                "feedback is supplied, address it while preserving working behavior."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Plan summary: {plan_summary}\n\n"
                f"File step: {step_name}\nPath: {file_path}\nType: {file_type}\n"
                f"Description: {description}\n\n{review}"
                f"Guidance:\n{guidance or 'None'}\n\n"
                f"Existing project files:\n{_json(existing_files)}\n\nProject memory:\n{memory or 'None'}"
            ),
        },
    ]


def reviewer_messages(
    request: str,
    guidance: str | None,
    plan: Plan,
    code: list[dict[str, Any]],
    existing_files: dict[str, str],
    checks: CheckReport,
    memory: str,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are the Reviewer. Compare every explicit requirement in the request and latest "
                "guidance against the plan and generated project files. Judge requested behavior, not "
                "just file presence or syntax: deterministic checks only verify structure and JavaScript "
                "parsing, not functional correctness. Reject missing or contradicted behavior with "
                "concrete issues the Coder can fix. Do not invent requirements. Return approve, reject, "
                "or escalate; escalate only when the request or plan is fundamentally ambiguous or unsafe."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Original request:\n{request}\n\nLatest guidance:\n{guidance or 'None'}\n\n"
                f"Plan:\n{_json(plan)}\n\nGenerated files:\n{_json(code)}\n\n"
                f"Other existing project files:\n{_json(existing_files)}\n\n"
                f"Deterministic checks:\n{_json(checks)}\n\nProject memory:\n{memory or 'None'}"
            ),
        },
    ]
