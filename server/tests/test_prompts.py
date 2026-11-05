from uuid import uuid4

from app.application.prompts import coder_messages, planner_messages, reviewer_messages
from app.domain.enums import FileType
from app.domain.models import CheckReport, Plan, PlanStep


def test_planner_prompt_classifies_scope_and_treats_context_as_untrusted() -> None:
    system, user = planner_messages(
        "Build a security-training page quoting an administrator prompt injection.",
        "untrusted memory",
        [{"role": "user", "content": "untrusted history"}],
        "untrusted guidance",
    )

    assert "out_of_scope" in system["content"]
    assert "policy_violation" in system["content"]
    assert "quoted injection examples" in system["content"]
    assert "untrusted data" in system["content"]
    assert "untrusted memory" in user["content"]


def test_coder_prompt_includes_plan_summary() -> None:
    messages = coder_messages(
        "Main page",
        "index.html",
        "html",
        "A page whose heading says Hello Live",
        "A page with a heading",
        {},
        "",
        None,
    )

    assert "A page whose heading says Hello Live" in messages[1]["content"]
    assert "Previous Reviewer feedback" not in messages[1]["content"]


def test_coder_prompt_includes_previous_reviewer_feedback() -> None:
    issue = "Add coupon-expiration validation."
    messages = coder_messages(
        "Main page",
        "index.html",
        "html",
        "Validate coupons",
        "Show coupon status",
        {},
        "",
        None,
        {"verdict": "reject", "summary": "Expired codes are accepted.", "issues": [issue]},
    )

    assert issue in messages[1]["content"]
    assert "Expired codes are accepted." in messages[1]["content"]
    assert "preserving working behavior" in messages[1]["content"]


def test_reviewer_prompt_receives_request_guidance_and_project_context() -> None:
    plan = Plan(
        project_id=uuid4(),
        summary="Validate discount coupons",
        steps=[
            PlanStep(
                name="Coupon page",
                file_path="index.html",
                file_type=FileType.HTML,
                description="Accept valid codes and reject expired ones",
            )
        ],
    )
    messages = reviewer_messages(
        "Build a coupon validator",
        "Expired coupons must be rejected",
        plan,
        [{"file_path": "index.html", "content": "<h1>Coupon validator</h1>"}],
        {"shared.js": "function validateDate() {}"},
        CheckReport(file_structure_ok=True, js_syntax_ok=True),
        "Project memory",
    )

    system, user = messages
    assert "not functional correctness" in system["content"]
    assert "Do not invent requirements" in system["content"]
    assert "Build a coupon validator" in user["content"]
    assert "Expired coupons must be rejected" in user["content"]
    assert "shared.js" in user["content"]


def test_all_agents_treat_supplied_context_as_untrusted() -> None:
    coder_system = coder_messages(
        "Page", "index.html", "html", "Plan", "Description", {}, "", None
    )[0]["content"]
    plan = Plan(project_id=uuid4(), summary="Page", steps=[])
    reviewer_system = reviewer_messages(
        "Build page",
        None,
        plan,
        [],
        {},
        CheckReport(file_structure_ok=True, js_syntax_ok=True),
        "",
    )[0]["content"]

    assert "untrusted data" in coder_system
    assert "untrusted data" in reviewer_system
