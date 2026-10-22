from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self
from uuid import UUID, uuid4

from app.application.graph import CodingGraph
from app.domain.enums import EventKind
from app.domain.errors import ResolverError
from app.domain.models import CheckReport, Plan

SCENARIO_DIR = Path(__file__).parent / "scenarios"


@dataclass
class FakeProject:
    id: UUID
    owner_id: UUID
    title: str
    summary: str = ""


@dataclass
class FakeRepo:
    projects: dict[UUID, FakeProject] = field(default_factory=dict)
    events: list[tuple] = field(default_factory=list)
    plans: dict[UUID, Plan] = field(default_factory=dict)
    reviews: list[tuple] = field(default_factory=list)
    runs: dict[UUID, dict] = field(default_factory=dict)
    turns: dict[UUID, list] = field(default_factory=dict)
    graph_events: list[tuple] = field(default_factory=list)

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

    async def add_review(self, run_id: UUID, feedback) -> None:
        self.reviews.append((run_id, feedback))

    async def add_graph_event(self, graph_run_id: UUID, node: str, payload: dict) -> None:
        self.graph_events.append((graph_run_id, node, payload))

    async def list_conversation_turns(self, owner_id: UUID, project_id: UUID | None = None):
        return self.turns.get(project_id, [])

    async def project_index(self, owner_id: UUID):
        return [
            {"id": str(project.id), "title": project.title, "summary": project.summary}
            for project in self.projects.values()
            if project.owner_id == owner_id
        ]

    async def recent_runs(self, owner_id: UUID, limit: int = 10):
        return []


class FakeWorkspace:
    def __init__(self, report: CheckReport | None = None) -> None:
        self.files: dict[str, str] = {}
        self.report = report or CheckReport(
            file_structure_ok=True, js_syntax_ok=True, files_checked=1, syntax_checked=[]
        )

    async def read_all(self, project_id: UUID) -> dict[str, str]:
        return dict(self.files)

    async def write_file(
        self, project_id: UUID, path: str, content: str, allowed_paths: set[str]
    ) -> None:
        if path not in allowed_paths:
            raise ValueError("write outside plan")
        self.files[path] = content

    async def check_readable(self, project_id: UUID, paths: list[str]) -> CheckReport:
        return self.report


class FakeMemory:
    async def load_project_memory(self, owner_id: UUID, project_id: UUID) -> str:
        return ""


class ScriptedLLM:
    def __init__(self, replies: list[dict]) -> None:
        self.replies = list(replies)

    async def complete(self, role: str, messages, schema) -> dict:
        if not self.replies:
            raise AssertionError(f"unexpected {role} call")
        return self.replies.pop(0)


def planner(*files: str) -> dict:
    return {
        "summary": f"build {len(files)} files",
        "steps": [
            {
                "name": Path(path).stem,
                "file_path": path,
                "file_type": Path(path).suffix.lstrip("."),
                "description": f"complete {path}",
            }
            for path in files
        ],
    }


def coder(*files: str) -> list[dict]:
    return [{"file_path": path, "content": f"/* {Path(path).stem} */\n"} for path in files]


def reviewer(verdict: str) -> dict:
    return {"verdict": verdict, "summary": f"review {verdict}", "issues": []}


@dataclass
class ScenarioResult:
    name: str
    approved: bool
    handoff: bool
    attempts_used: int
    checks_ok: bool
    checksum_ok: bool
    resolver_ok: bool
    ownership_ok: bool = True
    error: str | None = None


async def run_scenario(name: str, config: dict[str, Any]) -> ScenarioResult:
    repo = FakeRepo()
    workspace = FakeWorkspace()
    owner_id = uuid4()
    existing_project = None
    if config.get("existing_project"):
        existing_project = FakeProject(uuid4(), owner_id, "Existing project")
        repo.projects[existing_project.id] = existing_project
        repo.turns[existing_project.id] = []
    if config.get("foreign_project"):
        other = FakeProject(uuid4(), uuid4(), "Someone else's project")
        repo.projects[other.id] = other
        repo.turns[other.id] = []
        config["resolver"] = [
            {
                "project_id": str(other.id),
                "title": "foreign",
                "confidence": 0.9,
                "reason": "wrong owner",
            }
        ]

    clients = {
        risk: ScriptedLLM(config.get(risk, []))
        for risk in ("resolver", "planner", "coder", "reviewer")
    }
    graph = CodingGraph(
        llm_clients=clients,
        workspace=workspace,
        memory=FakeMemory(),
        repo_factory=lambda: repo,
        max_coder_attempts=3,
    )

    state = {
        "run_id": str(uuid4()),
        "owner_id": str(owner_id),
        "project_id": str(existing_project.id) if existing_project else None,
        "message": config["request"],
        "guidance": None,
        "attempts_used": 0,
        "attempts_total": 0,
        "round": 1,
        "graph_run_id": str(uuid4()),
        "skip_resolve": existing_project is not None,
    }

    error = None
    try:
        result = await graph.ainvoke(state, {})
    except ResolverError as exc:
        result = {}
        error = str(exc)

    code = result.get("code", [])
    checksum_ok = all(
        hashlib.sha256(item["content"].encode()).hexdigest() == item["checksum"] for item in code
    )
    checks = result.get("checks") or {}
    checks_ok = bool(checks.get("file_structure_ok") and checks.get("js_syntax_ok"))

    resolver_ok = True
    if config.get("foreign_project"):
        resolver_ok = error is not None and not repo.plans
    elif config.get("existing_project"):
        resolver_ok = result.get("project_id") == str(existing_project.id)
    elif config.get("expect_new_project"):
        resolver_ok = len(repo.projects) == (2 if config.get("foreign_project") else 1)

    return ScenarioResult(
        name=name,
        approved=bool(result.get("approved")),
        handoff=bool(result.get("handoff")),
        attempts_used=int(result.get("attempts_used", 0)),
        checks_ok=checks_ok,
        checksum_ok=checksum_ok,
        resolver_ok=resolver_ok,
        ownership_ok=resolver_ok if config.get("foreign_project") else True,
        error=error,
    )


def load_scenario(filename: str) -> dict[str, Any]:
    return json.loads((SCENARIO_DIR / filename).read_text(encoding="utf-8"))


async def main() -> None:
    coupon = load_scenario("coupon.json")
    flappy = load_scenario("flappy.json")
    files = ("index.html", "app.js")

    configs: list[tuple[str, dict[str, Any]]] = [
        (
            coupon["name"],
            {
                "request": coupon["request"],
                "expect_new_project": True,
                "resolver": [
                    {
                        "project_id": None,
                        "title": "Coupon validator",
                        "confidence": 0.9,
                        "reason": "new",
                    }
                ],
                "planner": [planner(*files)],
                "coder": coder(*files),
                "reviewer": [reviewer("approve")],
            },
        ),
        (
            flappy["name"],
            {
                "request": flappy["request"],
                "expect_new_project": True,
                "resolver": [
                    {"project_id": None, "title": "Flappy", "confidence": 0.9, "reason": "new"}
                ],
                "planner": [planner(*files)],
                "coder": coder(*files) + coder(*files),
                "reviewer": [reviewer("reject"), reviewer("approve")],
            },
        ),
        (
            "follow-up-existing-project",
            {
                "request": "Add a score counter to the coupon page.",
                "existing_project": True,
                "planner": [planner(*files)],
                "coder": coder(*files),
                "reviewer": [reviewer("approve")],
            },
        ),
        (
            "ambiguous-handoff",
            {
                "request": "Make it better.",
                "expect_new_project": True,
                "resolver": [
                    {"project_id": None, "title": "Unclear", "confidence": 0.2, "reason": "vague"}
                ],
                "planner": [planner(*files)],
                "coder": coder(*files),
                "reviewer": [reviewer("escalate")],
            },
        ),
        (
            "foreign-project-blocked",
            {
                "request": "Attach to a project I do not own.",
                "foreign_project": True,
                "planner": [],
                "coder": [],
                "reviewer": [],
            },
        ),
    ]

    results = [await run_scenario(name, config) for name, config in configs]
    first_pass = [
        result
        for result in results
        if result.name not in {"ambiguous-handoff", "foreign-project-blocked"}
    ]
    metrics = {
        "first_pass_approval": round(
            sum(result.approved and result.attempts_used == 1 for result in first_pass)
            / len(first_pass),
            2,
        ),
        "check_pass_rate": round(
            sum(result.checks_ok for result in results if result.error is None)
            / len([result for result in results if result.error is None]),
            2,
        ),
        "resolver_accuracy": round(sum(result.resolver_ok for result in results) / len(results), 2),
        "checksum_integrity": all(result.checksum_ok for result in results),
        "ownership_isolation": all(result.ownership_ok for result in results),
        "handoff_correct": next(
            result.handoff and not result.approved
            for result in results
            if result.name == "ambiguous-handoff"
        ),
    }
    payload = {
        "metrics": metrics,
        "scenarios": [result.__dict__ for result in results],
    }
    print(json.dumps(payload, indent=2))
    if not all(
        [
            metrics["checksum_integrity"],
            metrics["ownership_isolation"],
            metrics["handoff_correct"],
        ]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
