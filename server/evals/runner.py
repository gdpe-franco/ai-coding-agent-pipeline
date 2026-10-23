from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self
from uuid import UUID, uuid4

from app.application.graph import CodingGraph
from app.domain.errors import MissingCredentialsError, ModelError
from app.domain.models import CheckReport, Plan
from app.infrastructure.llm.client import ProviderRegistry
from app.settings import Settings

SCENARIO_DIR = Path(__file__).parent / "scenarios"


@dataclass
class FakeProject:
    id: UUID
    title: str
    summary: str = ""


@dataclass
class FakeRepo:
    projects: dict[UUID, FakeProject] = field(default_factory=dict)
    messages: dict[UUID, list[dict[str, str]]] = field(default_factory=dict)
    events: list[tuple] = field(default_factory=list)
    plans: dict[UUID, Plan] = field(default_factory=dict)
    runs: dict[UUID, dict[str, Any]] = field(default_factory=dict)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def get_project_context(self, project_id: UUID):
        return self.projects.get(project_id)

    async def list_chat_messages(self, chat_id: UUID):
        return [type("Message", (), item)() for item in self.messages.get(chat_id, [])]

    async def update_run(self, run_id: UUID, **fields) -> None:
        self.runs.setdefault(run_id, {}).update(fields)

    async def append_event(self, run_id, kind, payload, execution_id=None) -> None:
        self.events.append((run_id, str(kind), payload, execution_id))

    async def add_plan(self, run_id: UUID, plan: Plan) -> None:
        self.plans[run_id] = plan


class FakeWorkspace:
    def __init__(self, report: CheckReport | None = None, existing: dict[str, str] | None = None):
        self.files = dict(existing or {})
        self.report = report or CheckReport(
            file_structure_ok=True, js_syntax_ok=True, files_checked=1
        )

    async def read_all(self, project_id: UUID) -> dict[str, str]:
        return dict(self.files)

    async def write_file(self, project_id: UUID, path: str, content: str, allowed_paths: set[str]):
        if path not in allowed_paths:
            raise ValueError(f"write outside plan: {path}")
        self.files[path] = content

    async def check_readable(self, project_id: UUID, paths: list[str]) -> CheckReport:
        return self.report


class FakeMemory:
    async def load_project_memory(self, project_id: UUID) -> str:
        return "project conventions"


class ScriptedLLM:
    def __init__(self, replies: list[dict]) -> None:
        self.replies = list(replies)

    async def complete(self, role: str, messages, schema) -> dict:
        if not self.replies:
            raise AssertionError(f"unexpected {role} call")
        return self.replies.pop(0)


def planner(*files: str) -> dict:
    return {
        "summary": f"Build {len(files)} files",
        "steps": [
            {
                "name": Path(path).stem,
                "file_path": path,
                "file_type": Path(path).suffix.lstrip("."),
                "description": f"Complete {path}",
            }
            for path in files
        ],
    }


def coder(*files: str, prefix: str = "generated") -> list[dict]:
    return [
        {"file_path": path, "content": f"<!-- {prefix}: {Path(path).stem} -->\n"} for path in files
    ]


def reviewer(verdict: str) -> dict:
    return {"verdict": verdict, "summary": f"review {verdict}", "issues": []}


@dataclass
class ScenarioResult:
    name: str
    approved: bool
    handoff: bool
    coder_generations: int
    checks_ok: bool
    checksum_ok: bool
    file_preservation_ok: bool
    error: str | None = None


async def run_scenario(name: str, config: dict[str, Any]) -> ScenarioResult:
    repo = FakeRepo()
    workspace = FakeWorkspace(config.get("report"), config.get("existing_files"))
    project_id, chat_id, run_id, execution_id = uuid4(), uuid4(), uuid4(), uuid4()
    repo.projects[project_id] = FakeProject(project_id, config["project_title"])
    repo.messages[chat_id] = [{"role": "user", "content": config["request"]}]
    clients = {role: ScriptedLLM(config.get(role, [])) for role in ("planner", "coder", "reviewer")}
    graph = CodingGraph(
        llm_clients=clients,
        workspace=workspace,
        memory=FakeMemory(),
        repo_factory=lambda: repo,
    )
    error = None
    try:
        result = await graph.ainvoke(
            {
                "run_id": str(run_id),
                "owner_id": str(uuid4()),
                "chat_id": str(chat_id),
                "project_id": str(project_id),
                "execution_id": str(execution_id),
                "message": config["request"],
                "guidance": None,
            },
            {},
        )
    except ModelError as exc:
        result = {}
        error = str(exc)

    code = result.get("code", [])
    checksum_ok = all(
        hashlib.sha256(item["content"].encode()).hexdigest() == item["checksum"] for item in code
    )
    checks = result.get("checks") or {}
    checks_ok = bool(checks.get("file_structure_ok") and checks.get("js_syntax_ok"))
    preserved = all(
        workspace.files.get(path) == content
        for path, content in config.get("existing_files", {}).items()
        if path not in {step["file_path"] for step in (result.get("plan") or {}).get("steps", [])}
    )
    return ScenarioResult(
        name=name,
        approved=bool(result.get("approved")),
        handoff=bool(result.get("handoff")),
        coder_generations=int(result.get("coder_generations", 0)),
        checks_ok=checks_ok,
        checksum_ok=checksum_ok,
        file_preservation_ok=preserved,
        error=error,
    )


def load_scenario(filename: str) -> dict[str, Any]:
    return json.loads((SCENARIO_DIR / filename).read_text(encoding="utf-8"))


async def missing_credentials_result() -> dict[str, str]:
    client = ProviderRegistry(
        Settings(
            secret_key="evaluation-secret",
            azure_openai_api_key="",
            azure_openai_endpoint="",
            azure_openai_deployment_planner="",
        )
    ).build()["planner"]
    try:
        await client.complete("planner", [], {})
    except MissingCredentialsError as exc:
        return {"status": "passed", "error": str(exc)}
    raise AssertionError("missing Azure credentials were not rejected")


async def main() -> None:
    coupon = load_scenario("coupon.json")
    flappy = load_scenario("flappy.json")
    coupon_files = ("coupon.html", "coupon.css", "coupon.js", "README.md")
    flappy_files = ("index.html", "game.js", "style.css")
    scenarios = [
        await run_scenario(
            coupon["name"],
            {
                "project_title": "Coupon validator",
                "request": coupon["request"],
                "planner": [planner(*coupon_files)],
                "coder": coder(*coupon_files),
                "reviewer": [reviewer("approve")],
            },
        ),
        await run_scenario(
            flappy["name"],
            {
                "project_title": "Flappy Bird",
                "request": flappy["request"],
                "planner": [planner(*flappy_files)],
                "coder": coder(*flappy_files, prefix="first")
                + coder(*flappy_files, prefix="revised"),
                "reviewer": [reviewer("reject"), reviewer("approve")],
            },
        ),
        await run_scenario(
            "follow-up-file-preservation",
            {
                "project_title": "Existing project",
                "request": "Add a settings panel",
                "existing_files": {"keep.md": "unchanged", "index.html": "old"},
                "planner": [planner("index.html")],
                "coder": coder("index.html"),
                "reviewer": [reviewer("approve")],
            },
        ),
        await run_scenario(
            "honest-handoff",
            {
                "project_title": "Ambiguous project",
                "request": "Make it better",
                "planner": [planner("index.html")],
                "coder": coder("index.html"),
                "reviewer": [{"verdict": "escalate", "summary": "Needs guidance", "issues": []}],
            },
        ),
        await run_scenario(
            "malformed-planner-output",
            {
                "project_title": "Malformed project",
                "request": "Build a page",
                "planner": [{"unexpected": "shape"}],
            },
        ),
    ]
    coupon_result = scenarios[0]
    metrics = {
        "sample_size": len(scenarios),
        "check_pass_rate": sum(item.checks_ok for item in scenarios if item.error is None)
        / max(1, sum(item.error is None for item in scenarios)),
        "checksum_integrity": all(item.checksum_ok for item in scenarios),
        "file_preservation": all(item.file_preservation_ok for item in scenarios),
        "coupon_policy_approved": coupon_result.approved and coupon_result.coder_generations == 1,
        "handoff_correct": scenarios[3].handoff and not scenarios[3].approved,
        "malformed_output_blocked": scenarios[4].error is not None and not scenarios[4].approved,
    }
    print(
        json.dumps(
            {
                "fake_port_policy": metrics,
                "missing_credentials": await missing_credentials_result(),
                "real_provider_quality": {
                    "status": "blocked",
                    "provider": "Azure OpenAI",
                    "sample_size": 0,
                    "first_pass_coupon_approval": None,
                    "reason": "No real-provider coupon sample was run; fake-port results are not quality evidence.",
                },
                "scenarios": [item.__dict__ for item in scenarios],
            },
            indent=2,
        )
    )
    if not all(
        metrics[key]
        for key in (
            "checksum_integrity",
            "file_preservation",
            "coupon_policy_approved",
            "handoff_correct",
            "malformed_output_blocked",
        )
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
