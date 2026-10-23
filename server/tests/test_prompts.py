from app.application.prompts import coder_messages


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
