from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Self
from uuid import UUID, uuid4

import pytest

from app.application.graph import CodingGraph
from app.domain.enums import ConversationSender, EventKind
from app.domain.errors import ModelError, ResolverError
from app.domain.models import CheckReport, Plan


@dataclass
class FakeProject:
    id: UUID
    owner_id: UUID
    title: str = "Project"
    summary: str = ""


@dataclass
class FakeRepo:
    projects: dict[UUID, FakeProject] = field(default_factory=dict)
    events: list[tuple] = field(default_factory=list)
    graph_events: list[tuple] = field(default_factory=list)
    plans: dict[UUID, Plan] = field(default_factory=dict)
    reviews: list[tuple] = field(default_factory=list)
    runs: dict[UUID, dict] = field(default_factory=dict)
    turns: dict[UUID, list] = field(default_factory=dict)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def get_owned_project_context(self, project_id: UUID, owner_id: UUID):
        project = self.projects.get(project_id)
        if project is None or project.owner_id != owner_id:
            return None
        return project

    async def create_project_context(
        self, owner_id: UUID, title: str, summary: str = ""
    ) -> FakeProject:
        project = FakeProject(id=uuid4(), owner_id=owner_id, title=title, summary=summary)
        self.projects[project.id] = project
        self.turns[project.id] = []
        return project

    async def update_run(self, run_id: UUID, **fields) -> None:
        self.runs.setdefault(run_id, {}).update(fields)

    async def append_event(self, run_id: UUID, kind: EventKind, payload: dict) -> None:
        self.events.append((run_id, kind, payload))

    async def add_plan(self, run_id: UUID, plan: Plan) -> None:
        self.plans[run_id] = plan
        self.runs.setdefault(run_id, {})["plan"] = plan

    async def add_review(self, run_id: UUID, feedback) -> None:
        self.reviews.append((run_id, feedback))

    async def add_graph_event(self, graph_run_id: UUID, node: str, payload: dict) -> None:
        self.graph_events.append((graph_run_id, node, payload))

    async def list_conversation_turns(self, owner_id: UUID, project_id: UUID | None = None):
        return self.turns.get(project_id, [])

    async def project_index(self, owner_id: UUID):
        return []

    async def recent_runs(self, owner_id: UUID, limit: int = 10):
        return []


class FakeWorkspace:
    def __init__(self, report: CheckReport | None = None) -> None:
        self.files: dict[str, str] = {}
        self.report = report or CheckReport(file_structure_ok=True, js_syntax_ok=True)
        self.attempts: list[dict] = []

    async def read_all(self, project_id: UUID) -> dict[str, str]:
        return dict(self.files)

    async def write_file(
        self, project_id: UUID, path: str, content: str, allowed_paths: set[str]
    ) -> None:
        if path not in allowed_paths:
            raise ValueError("outside plan")
        self.files[path] = content
        self.attempts.append({"path": path, "content": content})

    async def check_readable(self, project_id: UUID, paths: list[str]) -> CheckReport:
        return self.report


class FakeMemory:
    def __init__(self) -> None:
        self.loaded: list[tuple[UUID, UUID]] = []

    async def load_project_memory(self, owner_id: UUID, project_id: UUID) -> str:
        self.loaded.append((owner_id, project_id))
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


def make_graph(*, resolver=None, planner=None, coder=None, reviewer=None, report=None):
    repo = FakeRepo()
    workspace = FakeWorkspace(report)
    clients = {
        "resolver": ScriptedLLM(resolver or []),
        "planner": ScriptedLLM(planner or []),
        "coder": ScriptedLLM(coder or []),
        "reviewer": ScriptedLLM(reviewer or []),
    }
    graph = CodingGraph(
        llm_clients=clients,
        workspace=workspace,
        memory=FakeMemory(),
        repo_factory=lambda: repo,
        max_coder_attempts=3,
    )
    return graph, repo, workspace, clients


async def test_planner_uses_only_selected_project_history_and_memory() -> None:
    graph, repo, _, clients = make_graph(planner=[planner_reply()])
    owner_id, selected_project, other_project, run_id, graph_run_id = (uuid4() for _ in range(5))
    repo.turns[selected_project] = [
        SimpleNamespace(role=ConversationSender.USER, content="selected project history")
    ]
    repo.turns[other_project] = [
        SimpleNamespace(role=ConversationSender.USER, content="private other-project history")
    ]

    await graph.planner(
        {
            "owner_id": str(owner_id),
            "project_id": str(selected_project),
            "run_id": str(run_id),
            "graph_run_id": str(graph_run_id),
            "message": "Add a settings page",
        }
    )

    prompt = clients["planner"].messages[0][1]["content"]
    assert "selected project history" in prompt
    assert "private other-project history" not in prompt
    assert f"memory for {selected_project}" in prompt
    assert graph.memory.loaded == [(owner_id, selected_project)]


RESOLVER_NEW = {
    "project_id": None,
    "title": "Coupon app",
    "confidence": 0.5,
    "reason": "new request",
}


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


def base_state(**overrides) -> dict:
    state = {
        "run_id": str(uuid4()),
        "owner_id": str(uuid4()),
        "project_id": None,
        "message": "build a coupon app",
        "guidance": None,
        "attempts_used": 0,
        "attempts_total": 0,
        "round": 1,
        "graph_run_id": str(uuid4()),
        "skip_resolve": False,
    }
    state.update(overrides)
    return state


async def test_resolve_plan_code_approve() -> None:
    graph, repo, workspace, _clients = make_graph(
        resolver=[RESOLVER_NEW],
        planner=[planner_reply()],
        coder=[{"file_path": "index.html", "content": "<h1>ok</h1>"}],
        reviewer=[{"verdict": "approve", "summary": "good", "issues": []}],
    )
    result = await graph.ainvoke(base_state(), {})
    assert result["approved"] is True
    assert result["attempts_used"] == 1
    assert workspace.files == {"index.html": "<h1>ok</h1>"}
    assert len(repo.projects) == 1
    assert repo.plans
    kinds = [event[1] for event in repo.events]
    assert EventKind.PLANNER in kinds and EventKind.CODER in kinds and EventKind.REVIEWER in kinds


async def test_escalate_is_handoff() -> None:
    graph, _, _, _ = make_graph(
        resolver=[RESOLVER_NEW],
        planner=[planner_reply()],
        coder=[{"file_path": "index.html", "content": "<h1>ok</h1>"}],
        reviewer=[
            {
                "verdict": "escalate",
                "summary": "ambiguous",
                "issues": [{"message": "Need a product choice", "file_path": "index.html"}],
            }
        ],
    )
    result = await graph.ainvoke(base_state(), {})
    assert result["approved"] is False
    assert result["handoff"] is True
    assert result["attempts_used"] == 1
    assert result["feedback"]["issues"] == [
        {"message": "Need a product choice", "file_path": "index.html"}
    ]
    assert [(item["file_path"], item["content"]) for item in result["code"]] == [
        ("index.html", "<h1>ok</h1>")
    ]


async def test_three_rejections_stop_the_round() -> None:
    coder = [{"file_path": "index.html", "content": f"<h1>{i}</h1>"} for i in range(3)]
    reviewer = [
        {
            "verdict": "reject",
            "summary": f"rejection {i}",
            "issues": [{"message": f"issue {i}", "file_path": "index.html"}],
        }
        for i in range(3)
    ]
    graph, _, _, _ = make_graph(
        resolver=[RESOLVER_NEW], planner=[planner_reply()], coder=coder, reviewer=reviewer
    )
    result = await graph.ainvoke(base_state(), {})
    assert result["approved"] is False
    assert result["handoff"] is False
    assert result["attempts_used"] == 3
    assert result["attempts_total"] == 3
    assert result["round"] == 1
    assert [(item["file_path"], item["content"]) for item in result["code"]] == [
        ("index.html", "<h1>2</h1>")
    ]
    assert result["feedback"]["summary"] == "rejection 2"
    assert result["feedback"]["issues"] == [{"message": "issue 2", "file_path": "index.html"}]


@pytest.mark.parametrize(
    ("plan", "content", "report"),
    [
        pytest.param(
            planner_reply(),
            "<h1>ok</h1>",
            CheckReport(
                file_structure_ok=False,
                js_syntax_ok=True,
                issues=["missing planned file: x"],
            ),
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
async def test_failed_deterministic_checks_block_approval(plan, content, report) -> None:
    graph, _, _, _ = make_graph(
        resolver=[RESOLVER_NEW],
        planner=[plan],
        coder=[{"file_path": plan["steps"][0]["file_path"], "content": content} for _ in range(3)],
        reviewer=[{"verdict": "approve", "summary": "looks fine", "issues": []} for _ in range(3)],
        report=report,
    )
    result = await graph.ainvoke(base_state(), {})
    assert result["approved"] is False
    assert result["attempts_used"] == 3


async def test_resume_starts_at_planner_and_preserves_total() -> None:
    project_id = uuid4()
    owner_id = uuid4()
    graph, repo, _, clients = make_graph(
        planner=[planner_reply()],
        coder=[{"file_path": "index.html", "content": "<h1>resumed</h1>"}],
        reviewer=[{"verdict": "approve", "summary": "good", "issues": []}],
    )
    repo.projects[project_id] = FakeProject(id=project_id, owner_id=owner_id)
    state = base_state(
        owner_id=str(owner_id),
        project_id=str(project_id),
        skip_resolve=True,
        attempts_total=3,
        guidance="make the coupon bold",
        round=2,
    )
    result = await graph.ainvoke(state, {})
    assert result["approved"] is True
    assert result["attempts_used"] == 1
    assert result["attempts_total"] == 4
    assert clients["planner"].calls == ["planner"]


async def test_explicit_project_skips_resolver() -> None:
    project_id = uuid4()
    owner_id = uuid4()
    graph, repo, _, clients = make_graph(
        planner=[planner_reply()],
        coder=[{"file_path": "index.html", "content": "<h1>x</h1>"}],
        reviewer=[{"verdict": "approve", "summary": "good", "issues": []}],
    )
    repo.projects[project_id] = FakeProject(id=project_id, owner_id=owner_id)
    state = base_state(owner_id=str(owner_id), project_id=str(project_id), skip_resolve=True)
    await graph.ainvoke(state, {})
    assert clients["resolver"].calls == []


@pytest.mark.parametrize(
    ("stage", "reply", "tag"),
    [
        pytest.param("planner", {"summary": "x"}, "planner_error", id="planner"),
        pytest.param("coder", {"file_path": "index.html"}, "coder_error", id="coder"),
        pytest.param("reviewer", {"verdict": "approve"}, "reviewer_error", id="reviewer"),
    ],
)
async def test_schema_errors_are_tagged_by_agent(stage, reply, tag) -> None:
    replies = {
        "resolver": RESOLVER_NEW,
        "planner": planner_reply(),
        "coder": {"file_path": "index.html", "content": "<h1>ok</h1>"},
        "reviewer": {"verdict": "approve", "summary": "good", "issues": []},
        stage: reply,
    }
    graph, _, _, _ = make_graph(
        resolver=[replies["resolver"]],
        planner=[replies["planner"]],
        coder=[replies["coder"]],
        reviewer=[replies["reviewer"]],
    )
    with pytest.raises(ModelError) as caught:
        await graph.ainvoke(base_state(), {})
    assert caught.value.tag == tag


async def test_resolver_cannot_attach_to_foreign_project() -> None:
    foreign = uuid4()
    owner = uuid4()
    graph, repo, _, _ = make_graph(
        resolver=[
            {
                "project_id": str(foreign),
                "title": "x",
                "confidence": 0.9,
                "reason": "match",
            }
        ]
    )
    repo.projects[foreign] = FakeProject(id=foreign, owner_id=uuid4())
    with pytest.raises(ResolverError):
        await graph.ainvoke(base_state(owner_id=str(owner)), {})
