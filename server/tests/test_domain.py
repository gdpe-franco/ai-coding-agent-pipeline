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

PLANNER_STEP = {
    "name": "page",
    "file_path": "index.html",
    "file_type": "html",
    "description": "main page",
}


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("/index.html", id="absolute"),
        pytest.param("../secret.txt", id="parent"),
        pytest.param("a/../../b", id="nested-parent"),
        pytest.param("a\\b", id="backslash"),
        pytest.param("", id="empty"),
        pytest.param("a b.html", id="space"),
    ],
)
def test_relative_path_rejects_unsafe_values(path: str) -> None:
    with pytest.raises(ValueError):
        validate_relative_path(path)


def test_relative_path_accepts_nested_paths() -> None:
    assert validate_relative_path("assets/app.js") == "assets/app.js"


def test_file_type_accepts_case_insensitively() -> None:
    assert validate_file_type("HTML") == "html"


def test_file_type_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        validate_file_type("exe")


@pytest.mark.parametrize(
    ("structure_ok", "syntax_ok", "expected"),
    [
        pytest.param(True, True, True, id="all-passed"),
        pytest.param(True, False, False, id="syntax-failed"),
        pytest.param(False, True, False, id="structure-failed"),
    ],
)
def test_check_report_ok_requires_both_stages(
    structure_ok: bool, syntax_ok: bool, expected: bool
) -> None:
    assert CheckReport(file_structure_ok=structure_ok, js_syntax_ok=syntax_ok).ok is expected


def test_planner_schema_accepts_valid_step() -> None:
    assert PlannerResponse(summary="ok", steps=[PLANNER_STEP]).steps[0].file_path == "index.html"


@pytest.mark.parametrize(
    "steps",
    [
        pytest.param([PLANNER_STEP] * 9, id="over-step-budget"),
        pytest.param([{**PLANNER_STEP, "file_path": "../evil.html"}], id="unsafe-path"),
    ],
)
def test_planner_schema_rejects_invalid_steps(steps: list[dict[str, str]]) -> None:
    with pytest.raises(ValueError):
        PlannerResponse(summary="ok", steps=steps)


def test_coder_schema_rejects_empty_content() -> None:
    with pytest.raises(ValueError):
        CoderResponse(file_path="index.html", content="")


@pytest.mark.parametrize("verdict", list(ReviewerVerdict), ids=lambda verdict: verdict.value)
def test_reviewer_schema_accepts_verdict(verdict: ReviewerVerdict) -> None:
    assert ReviewerResponse(verdict=verdict.value, summary="s").verdict == verdict.value


def test_reviewer_schema_rejects_unknown_verdict() -> None:
    with pytest.raises(ValueError):
        ReviewerResponse(verdict="maybe", summary="s")


def test_resolver_schema_allows_null_project() -> None:
    parsed = ResolverResponse(project_id=None, title="New", confidence=0.4, reason="no match")
    assert parsed.project_id is None
