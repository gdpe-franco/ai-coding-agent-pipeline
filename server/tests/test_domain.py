from __future__ import annotations

import pytest

from app.application.agent_schemas import (
    CoderResponse,
    PlannerResponse,
    ResolverResponse,
    ReviewerResponse,
)
from app.domain.enums import ReviewerVerdict
from app.domain.models import CheckReport, validate_file_type, validate_relative_path


def test_relative_path_rejects_unsafe_values() -> None:
    for value in ["/index.html", "../secret.txt", "a/../../b", "a\\b", "", "a b.html"]:
        with pytest.raises(ValueError):
            validate_relative_path(value)


def test_relative_path_accepts_nested_paths() -> None:
    assert validate_relative_path("assets/app.js") == "assets/app.js"


def test_file_type_allowlist() -> None:
    assert validate_file_type("HTML") == "html"
    with pytest.raises(ValueError):
        validate_file_type("exe")


def test_check_report_ok_requires_both_stages() -> None:
    assert CheckReport(file_structure_ok=True, js_syntax_ok=True).ok
    assert not CheckReport(file_structure_ok=True, js_syntax_ok=False).ok
    assert not CheckReport(file_structure_ok=False, js_syntax_ok=True).ok


def test_planner_schema_enforces_step_budget_and_paths() -> None:
    step = {
        "name": "page",
        "file_path": "index.html",
        "file_type": "html",
        "description": "main page",
    }
    assert PlannerResponse(summary="ok", steps=[step]).steps[0].file_path == "index.html"
    with pytest.raises(ValueError):
        PlannerResponse(summary="ok", steps=[step] * 9)
    with pytest.raises(ValueError):
        PlannerResponse(summary="ok", steps=[{**step, "file_path": "../evil.html"}])


def test_coder_schema_rejects_empty_content() -> None:
    with pytest.raises(ValueError):
        CoderResponse(file_path="index.html", content="")


def test_reviewer_schema_verdicts() -> None:
    for verdict in ReviewerVerdict:
        parsed = ReviewerResponse(verdict=verdict.value, summary="s")
        assert parsed.verdict == verdict.value
    with pytest.raises(ValueError):
        ReviewerResponse(verdict="maybe", summary="s")


def test_resolver_schema_allows_null_project() -> None:
    parsed = ResolverResponse(project_id=None, title="New", confidence=0.4, reason="no match")
    assert parsed.project_id is None
