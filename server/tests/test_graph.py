from __future__ import annotations

from dataclasses import dataclass, field
from typing import Self
from uuid import UUID, uuid4

import pytest

from app.application.graph import CodingGraph
from app.domain.enums import EventKind
from app.domain.errors import ModelError
from app.domain.models import CheckReport, Plan


@dataclass
class FakeProject:
    id: UUID
    title: str = "Project"
    summary: str = ""


@dataclass
class FakeMessage:
    role: str
    content: str


@dataclass
class FakeRepo:
    projects: dict[UUID, FakeProject] = field(default_factory=dict)
    events: list[tuple] = field(default_factory=list)
    plans: dict[UUID, Plan] = field(default_factory=dict)
    runs: dict[UUID, dict] = field(default_factory=dict)
    messages: dict[UUID, list[FakeMessage]] = field(default_factory=dict)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def get_project_context(self, project_id: UUID):
        return self.projects.get(project_id)

    async def list_chat_messages(self, chat_id: UUID):
        return self.messages.get(chat_id, [])

    async def update_run(self, run_id: UUID, **fields) -> None:
        self.runs.setdefault(run_id, {}).update(fields)

    async def append_event(
        self, run_id: UUID, kind: EventKind | str, payload: dict, execution_id=None
    ) -> None:
        self.events.append((run_id, str(kind), payload, execution_id))

    async def add_plan(self, run_id: UUID, plan: Plan) -> None:
        self.plans[run_id] = plan


class FakeWorkspace:
    def __init__(self, report: CheckReport | None = None) -> None:
        self.files: dict[str, str] = {}
        self.report = report or CheckReport(file_structure_ok=True, js_syntax_ok=True)

    async def read_all(self, project_id: UUID) -> dict[str, str]:
        return dict(self.files)

    async def write_file(
        self, project_id: UUID, path: str, content: str, allowed_paths: set[str]
    ) -> None:
        if path not in allowed_paths:
            raise ValueError("outside plan")
        self.files[path] = content

    async def check_readable(self, project_id: UUID, paths: list[str]) -> CheckReport:
        return self.report


class FakeMemory:
    async def load_project_memory(self, project_id: UUID) -> str:
        return f"memory for {project_id}"


class ScriptedLLM:
    def __init__(self, replies: list[dict]) -> None:
        self.replies = list(replies)
        self.calls: list[str] = []
        self.messages: list[list[dict[str, str]]] = []

    async def complete(self, role: str, messages, schema) -> dict:
        self.calls.append(role)
        self.messages.append(messages)
        if not self.replies:
            raise AssertionError(f"unexpected {role} call")
        return self.replies.pop(0)


def planner_reply(path: str = "index.html", file_type: str = "html") -> dict:
    return {
        "summary": "one page",
        "steps": [
            {
                "name": "page",
                "file_path": path,
                "file_type": file_type,
                "description": "main page",
            }
        ],
    }


def make_graph(*, planner=None, coder=None, reviewer=None, report=None):
    repo = FakeRepo()
    workspace = FakeWorkspace(report)
    clients = {
        "planner": ScriptedLLM(planner or []),
        "coder": ScriptedLLM(coder or []),
        "reviewer": ScriptedLLM(reviewer or []),
    }
    graph = CodingGraph(
        llm_clients=clients,
        workspace=workspace,
        memory=FakeMemory(),
        repo_factory=lambda: repo,
    )
    return graph, repo, workspace, clients


def base_state(**overrides) -> dict:
    state = {
        "run_id": str(uuid4()),
        "owner_id": str(uuid4()),
        "chat_id": str(uuid4()),
        "project_id": str(uuid4()),
        "execution_id": str(uuid4()),
        "message": "build a coupon app",
        "guidance": None,
    }
    state.update(overrides)
    return state


async def test_execution_uses_only_its_chat_and_completes_review() -> None:
    graph, repo, workspace, clients = make_graph(
        planner=[planner_reply()],
        coder=[{"file_path": "index.html", "content": "<h1>ok</h1>"}],
        reviewer=[{"verdict": "approve", "summary": "good", "issues": []}],
    )
    state = base_state()
    chat_id, other_chat_id = UUID(state["chat_id"]), uuid4()
    project_id, other_project_id = UUID(state["project_id"]), uuid4()
    repo.projects[project_id] = FakeProject(project_id)
    repo.projects[other_project_id] = FakeProject(other_project_id)
    repo.messages[chat_id] = [FakeMessage("user", "selected chat history")]
    repo.messages[other_chat_id] = [FakeMessage("user", "private other-chat history")]

    result = await graph.ainvoke(state, {})

    assert result["approved"] is True
    assert result["coder_generations"] == 1
    assert workspace.files == {"index.html": "<h1>ok</h1>"}
    assert "selected chat history" in clients["planner"].messages[0][1]["content"]
    assert "private other-chat history" not in clients["planner"].messages[0][1]["content"]
    assert [event[1] for event in repo.events] == [
        EventKind.STEP_STARTED.value,
        EventKind.STEP_COMPLETED.value,
        EventKind.STEP_STARTED.value,
        EventKind.GENERATION.value,
        EventKind.STEP_COMPLETED.value,
        EventKind.STEP_STARTED.value,
        EventKind.STEP_COMPLETED.value,
    ]


async def test_escalation_hands_off_without_fabricating_approval() -> None:
    graph, _, _, _ = make_graph(
        planner=[planner_reply()],
        coder=[{"file_path": "index.html", "content": "<h1>ok</h1>"}],
        reviewer=[
            {
                "verdict": "escalate",
                "summary": "ambiguous",
                "issues": [{"message": "Need guidance", "file_path": "index.html"}],
            }
        ],
    )

    result = await graph.ainvoke(base_state(), {})

    assert result["approved"] is False
    assert result["handoff"] is True
    assert result["coder_generations"] == 1
    assert result["feedback"]["issues"] == [{"message": "Need guidance", "file_path": "index.html"}]


async def test_generation_budget_is_checkpoint_state_not_run_columns() -> None:
    graph, _, _, _ = make_graph(
        planner=[planner_reply()],
        coder=[{"file_path": "index.html", "content": f"<h1>{i}</h1>"} for i in range(3)],
        reviewer=[
            {"verdict": "reject", "summary": f"rejection {i}", "issues": []} for i in range(3)
        ],
    )

    result = await graph.ainvoke(base_state(), {})

    assert result["approved"] is False
    assert result["handoff"] is True
    assert result["coder_generations"] == 3
    assert result["code"][0]["content"] == "<h1>2</h1>"
    assert result["feedback"]["summary"] == "rejection 2"


@pytest.mark.parametrize(
    ("plan", "content", "report"),
    [
        pytest.param(
            planner_reply(),
            "<h1>ok</h1>",
            CheckReport(file_structure_ok=False, js_syntax_ok=True, issues=["missing file"]),
            id="file-structure",
        ),
        pytest.param(
            planner_reply("app.js", "js"),
            "const = ;",
            CheckReport(file_structure_ok=True, js_syntax_ok=False, issues=["bad js"]),
            id="javascript-syntax",
        ),
    ],
)
async def test_failed_checks_block_approval(plan, content, report) -> None:
    graph, _, _, _ = make_graph(
        planner=[plan],
        coder=[{"file_path": plan["steps"][0]["file_path"], "content": content} for _ in range(3)],
        reviewer=[{"verdict": "approve", "summary": "looks fine", "issues": []} for _ in range(3)],
        report=report,
    )

    result = await graph.ainvoke(base_state(), {})

    assert result["approved"] is False
    assert result["coder_generations"] == 3


@pytest.mark.parametrize(
    ("stage", "reply", "tag"),
    [
        pytest.param("planner", {"summary": "x"}, "planner_error", id="planner"),
        pytest.param("coder", {"file_path": "index.html"}, "coder_error", id="coder"),
        pytest.param("reviewer", {"verdict": "approve"}, "reviewer_error", id="reviewer"),
    ],
)
async def test_schema_errors_are_tagged_by_workflow_step(stage, reply, tag) -> None:
    replies = {
        "planner": planner_reply(),
        "coder": {"file_path": "index.html", "content": "<h1>ok</h1>"},
        "reviewer": {"verdict": "approve", "summary": "good", "issues": []},
    }
    replies[stage] = reply
    graph, _, _, _ = make_graph(
        planner=[replies["planner"]], coder=[replies["coder"]], reviewer=[replies["reviewer"]]
    )
    with pytest.raises(ModelError) as caught:
        await graph.ainvoke(base_state(), {})
    assert caught.value.tag == tag
