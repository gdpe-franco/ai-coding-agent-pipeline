from __future__ import annotations

from typing import Any, TypedDict
from uuid import UUID, uuid4

from langgraph.graph import END, START, StateGraph

from app.application.agent_schemas import (
    CODER_SCHEMA,
    PLANNER_SCHEMA,
    RESOLVER_SCHEMA,
    REVIEWER_SCHEMA,
    CoderResponse,
    PlannerResponse,
    ResolverResponse,
    ReviewerResponse,
)
from app.application.prompts import (
    coder_messages,
    planner_messages,
    resolver_messages,
    reviewer_messages,
)
from app.domain.enums import EventKind, FileType, ReviewerVerdict
from app.domain.errors import ModelError, ResolverError
from app.domain.models import Code, Feedback, Issue, Plan, PlanStep, validate_file_type
from app.infrastructure.persistence.repository import (
    checksum,
    code_payload,
    feedback_payload,
    plan_from_payload,
    plan_payload,
)


class GraphState(TypedDict, total=False):
    run_id: str
    owner_id: str
    project_id: str | None
    project_title: str
    message: str
    guidance: str | None
    memory: str
    plan: dict[str, Any]
    code: list[dict[str, Any]]
    feedback: dict[str, Any]
    checks: dict[str, Any]
    attempts_used: int
    attempts_total: int
    round: int
    graph_run_id: str
    skip_resolve: bool
    approved: bool
    handoff: bool
    error: str | None


class CodingGraph:
    def __init__(
        self,
        llm_clients: dict[str, Any],
        workspace: Any,
        memory: Any,
        repo_factory: Any,
        max_coder_attempts: int = 3,
        checkpointer: Any | None = None,
    ) -> None:
        self.llm_clients = llm_clients
        self.workspace = workspace
        self.memory = memory
        self.repo_factory = repo_factory
        self.max_coder_attempts = max_coder_attempts
        self.graph = self._build(checkpointer)

    def _build(self, checkpointer: Any | None):
        workflow = StateGraph(GraphState)
        workflow.add_node("resolve", self.resolve)
        workflow.add_node("planner", self.planner)
        workflow.add_node("coder", self.coder)
        workflow.add_node("reviewer", self.reviewer)
        workflow.add_conditional_edges(
            START,
            lambda state: "planner" if state.get("skip_resolve") else "resolve",
            {"resolve": "resolve", "planner": "planner"},
        )
        workflow.add_edge("resolve", "planner")
        workflow.add_edge("planner", "coder")
        workflow.add_edge("coder", "reviewer")
        workflow.add_conditional_edges(
            "reviewer",
            self.route_after_review,
            {"coder": "coder", "end": END},
        )
        return workflow.compile(checkpointer=checkpointer)

    async def ainvoke(self, state: GraphState, config: dict[str, Any]) -> GraphState:
        return await self.graph.ainvoke(state, config=config)

    async def resolve(self, state: GraphState) -> GraphState:
        async with self.repo_factory() as session:
            repo = self._repo(session)
            project_id = state.get("project_id")
            if project_id:
                project = await repo.get_owned_project(UUID(project_id), UUID(state["owner_id"]))
                if project is None:
                    raise ResolverError("project is not owned by the run owner")
                title = project.title
                await self._graph_event(
                    repo, state, "resolve", {"project_id": str(project.id), "explicit": True}
                )
                await session.commit()
                return {"project_id": str(project.id), "project_title": title}

            client = self.llm_clients["resolver"]
            raw = await client.complete(
                "resolver",
                resolver_messages(
                    state["message"],
                    await repo.project_index(UUID(state["owner_id"])),
                    await repo.recent_runs(UUID(state["owner_id"])),
                ),
                RESOLVER_SCHEMA,
            )
            try:
                parsed = ResolverResponse.model_validate(raw)
            except Exception as exc:
                raise ResolverError(f"invalid resolver output: {exc}") from exc

            project = None
            if parsed.project_id is not None:
                project = await repo.get_owned_project(parsed.project_id, UUID(state["owner_id"]))
                if project is None:
                    raise ResolverError("resolver selected a project outside the owner's scope")
            if project is None:
                project = await repo.create_project(
                    UUID(state["owner_id"]), parsed.title, parsed.reason
                )
            await repo.update_run(UUID(state["run_id"]), project_id=project.id)
            await repo.append_event(
                UUID(state["run_id"]),
                EventKind.PROJECT_RESOLVED,
                {"project_id": str(project.id), "title": project.title, "reason": parsed.reason},
            )
            await session.commit()
            await self._graph_event(
                repo,
                state,
                "resolve",
                {"project_id": str(project.id), "created": parsed.project_id is None},
            )
            await session.commit()
            return {"project_id": str(project.id), "project_title": project.title}

    async def planner(self, state: GraphState) -> GraphState:
        async with self.repo_factory() as session:
            repo = self._repo(session)
            project_id = UUID(state["project_id"])  # type: ignore[arg-type]
            memory = await self.memory.load_project_memory(UUID(state["owner_id"]), project_id)
            turns = await repo.list_conversation_turns(UUID(state["owner_id"]), project_id)
            conversation = [
                {"role": turn.role.value, "content": turn.content} for turn in turns[-20:]
            ]
            raw = await self.llm_clients["planner"].complete(
                "planner",
                planner_messages(state["message"], memory, conversation, state.get("guidance")),
                PLANNER_SCHEMA,
            )
            try:
                parsed = PlannerResponse.model_validate(raw)
            except Exception as exc:
                raise ModelError(f"invalid planner output: {exc}", tag="planner_error") from exc
            plan = Plan(
                id=uuid4(),
                project_id=project_id,
                summary=parsed.summary,
                steps=[
                    PlanStep(
                        name=step.name,
                        file_path=step.file_path,
                        file_type=FileType(validate_file_type(step.file_type.value)),
                        description=step.description,
                    )
                    for step in parsed.steps
                ],
            )
            await repo.add_plan(UUID(state["run_id"]), plan)
            await repo.append_event(
                UUID(state["run_id"]), EventKind.PLANNER, {"plan": plan_payload(plan)}
            )
            await session.commit()
            await self._graph_event(repo, state, "planner", {"steps": len(plan.steps)})
            await session.commit()
            return {
                "plan": {
                    "id": str(plan.id),
                    "project_id": str(plan.project_id),
                    "summary": plan.summary,
                    "steps": [
                        {
                            "id": str(step.id),
                            "name": step.name,
                            "file_path": step.file_path,
                            "file_type": step.file_type.value,
                            "description": step.description,
                        }
                        for step in plan.steps
                    ],
                },
                "memory": memory,
            }

    async def coder(self, state: GraphState) -> GraphState:
        async with self.repo_factory() as session:
            repo = self._repo(session)
            run_id = UUID(state["run_id"])
            attempts_used = state.get("attempts_used", 0) + 1
            attempts_total = state.get("attempts_total", 0) + 1
            await repo.update_run(
                run_id, attempts_used=attempts_used, attempts_total=attempts_total
            )
            plan = plan_from_payload(state["plan"])
            allowed_paths = {step.file_path for step in plan.steps}
            code: list[dict[str, Any]] = []
            for step in plan.steps:
                existing = await self.workspace.read_all(plan.project_id)
                raw = await self.llm_clients["coder"].complete(
                    "coder",
                    coder_messages(
                        step.name,
                        step.file_path,
                        step.file_type.value,
                        step.description,
                        existing,
                        state.get("memory", ""),
                        state.get("guidance"),
                    ),
                    CODER_SCHEMA,
                )
                try:
                    parsed = CoderResponse.model_validate(raw)
                except Exception as exc:
                    raise ModelError(
                        f"invalid coder output for {step.file_path}: {exc}", tag="coder_error"
                    ) from exc
                if parsed.file_path != step.file_path:
                    raise ModelError(
                        f"coder returned {parsed.file_path}, expected {step.file_path}",
                        tag="coder_error",
                    )
                await self.workspace.write_file(
                    plan.project_id, parsed.file_path, parsed.content, allowed_paths
                )
                item = Code(
                    run_id=run_id,
                    file_path=parsed.file_path,
                    content=parsed.content,
                    checksum=checksum(parsed.content),
                    file_type=step.file_type.value,
                )
                code.append(code_payload(item))
                await repo.append_event(
                    run_id,
                    EventKind.CODER,
                    {"file_path": item.file_path, "checksum": item.checksum},
                )
            await repo.update_run(run_id, latest_code=code)
            await session.commit()
            await self._graph_event(
                repo, state, "coder", {"attempt": attempts_used, "files": len(code)}
            )
            await session.commit()
            return {
                "code": code,
                "attempts_used": attempts_used,
                "attempts_total": attempts_total,
            }

    async def reviewer(self, state: GraphState) -> GraphState:
        async with self.repo_factory() as session:
            repo = self._repo(session)
            run_id = UUID(state["run_id"])
            plan = plan_from_payload(state["plan"])
            check_report = await self.workspace.check_readable(
                plan.project_id, [step.file_path for step in plan.steps]
            )
            raw = await self.llm_clients["reviewer"].complete(
                "reviewer",
                reviewer_messages(
                    plan, state.get("code", []), check_report, state.get("memory", "")
                ),
                REVIEWER_SCHEMA,
            )
            try:
                parsed = ReviewerResponse.model_validate(raw)
            except Exception as exc:
                raise ModelError(f"invalid reviewer output: {exc}", tag="reviewer_error") from exc
            verdict = ReviewerVerdict(parsed.verdict)
            if verdict == ReviewerVerdict.APPROVE and not check_report.ok:
                verdict = ReviewerVerdict.REJECT
            issues = [
                Issue(message=issue.message, file_path=issue.file_path) for issue in parsed.issues
            ]
            issues.extend(Issue(message=item) for item in check_report.issues)
            feedback = Feedback(verdict=verdict, summary=parsed.summary, issues=issues)
            await repo.add_review(run_id, feedback)
            feedback_data = feedback_payload(feedback)
            checks_data = {
                "file_structure_ok": check_report.file_structure_ok,
                "js_syntax_ok": check_report.js_syntax_ok,
                "files_checked": check_report.files_checked,
                "syntax_checked": check_report.syntax_checked,
                "issues": check_report.issues,
            }
            await repo.update_run(run_id, feedback=feedback_data, checks=checks_data)
            await repo.append_event(
                run_id,
                EventKind.REVIEWER,
                {"verdict": verdict.value, "summary": feedback.summary, "issues": len(issues)},
            )
            await session.commit()
            await self._graph_event(
                repo, state, "reviewer", {"verdict": verdict.value, "checks_ok": check_report.ok}
            )
            await session.commit()
            return {
                "feedback": feedback_data,
                "checks": checks_data,
                "approved": verdict == ReviewerVerdict.APPROVE and check_report.ok,
                "handoff": verdict == ReviewerVerdict.ESCALATE,
            }

    def route_after_review(self, state: GraphState) -> str:
        if state.get("error"):
            return "end"
        if state.get("approved"):
            return "end"
        if state.get("handoff"):
            return "end"
        if state.get("attempts_used", 0) >= self.max_coder_attempts:
            return "end"
        return "coder"

    def _repo(self, session):
        from app.infrastructure.persistence.repository import SqlAlchemyRepository

        return SqlAlchemyRepository(session)

    async def _graph_event(
        self, repo, state: GraphState, node: str, payload: dict[str, Any]
    ) -> None:
        graph_run_id = state.get("graph_run_id")
        if graph_run_id:
            await repo.add_graph_event(UUID(graph_run_id), node, payload)
