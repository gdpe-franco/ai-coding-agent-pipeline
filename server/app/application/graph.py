from __future__ import annotations

from typing import Any, TypedDict
from uuid import UUID, uuid4

from langgraph.graph import END, START, StateGraph

from app.application.agent_schemas import (
    CODER_SCHEMA,
    PLANNER_SCHEMA,
    REVIEWER_SCHEMA,
    CoderResponse,
    PlannerResponse,
    ReviewerResponse,
)
from app.application.ports import RepositoryFactory
from app.application.prompts import (
    coder_messages,
    planner_messages,
    reviewer_messages,
)
from app.domain.enums import EventKind, FileType, ReviewerVerdict
from app.domain.errors import ModelError
from app.domain.models import Code, Feedback, Issue, Plan, PlanStep, validate_file_type
from app.domain.policies import generation_budget_exhausted, is_approved, requires_handoff
from app.domain.serialization import (
    checksum,
    code_payload,
    feedback_payload,
    plan_from_payload,
    plan_payload,
)


class GraphState(TypedDict, total=False):
    run_id: str
    owner_id: str
    chat_id: str
    project_id: str
    execution_id: str
    message: str
    guidance: str | None
    memory: str
    plan: dict[str, Any]
    code: list[dict[str, Any]]
    feedback: dict[str, Any]
    checks: dict[str, Any]
    coder_generations: int
    approved: bool
    handoff: bool
    error: str | None


class CodingGraph:
    def __init__(
        self,
        llm_clients: dict[str, Any],
        workspace: Any,
        memory: Any,
        repo_factory: RepositoryFactory,
        max_coder_generations: int = 3,
        checkpointer: Any | None = None,
    ) -> None:
        self.llm_clients = llm_clients
        self.workspace = workspace
        self.memory = memory
        self.repo_factory = repo_factory
        self.max_coder_generations = max_coder_generations
        self.graph = self._build(checkpointer)

    def _build(self, checkpointer: Any | None):
        workflow = StateGraph(GraphState)
        workflow.add_node("planner", self.planner)
        workflow.add_node("coder", self.coder)
        workflow.add_node("reviewer", self.reviewer)
        workflow.add_edge(START, "planner")
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

    async def planner(self, state: GraphState) -> GraphState:
        async with self.repo_factory() as repo:
            project_id = UUID(state["project_id"])
            run_id = UUID(state["run_id"])
            execution_id = UUID(state["execution_id"])
            await repo.append_event(
                run_id, EventKind.STEP_STARTED, {"step_key": "planner"}, execution_id
            )
            await repo.commit()
            memory = await self.memory.load_project_memory(project_id)
            turns = await repo.list_chat_messages(UUID(state["chat_id"]))
            chat_history = [
                {
                    "role": turn.role.value if hasattr(turn.role, "value") else turn.role,
                    "content": turn.content,
                }
                for turn in turns[-20:]
            ]
            raw = await self.llm_clients["planner"].complete(
                "planner",
                planner_messages(state["message"], memory, chat_history, state.get("guidance")),
                PLANNER_SCHEMA,
            )
            try:
                parsed = PlannerResponse.model_validate(raw)
            except Exception as exc:
                raise ModelError(f"invalid planner output: {exc}", tag="planner_error") from exc
            suggested_title = parsed.suggested_project_title
            if suggested_title:
                suggested_title = " ".join(suggested_title.split())
                if len(suggested_title) <= 80 and 2 <= len(suggested_title.split()) <= 6:
                    await repo.suggest_initial_chat_title(
                        UUID(state["owner_id"]),
                        UUID(state["chat_id"]),
                        run_id,
                        suggested_title,
                    )
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
            await repo.add_plan(run_id, plan)
            await repo.append_event(
                run_id,
                EventKind.STEP_COMPLETED,
                {"step_key": "planner", "plan": plan_payload(plan)},
                execution_id,
            )
            await repo.commit()
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
        async with self.repo_factory() as repo:
            run_id = UUID(state["run_id"])
            execution_id = UUID(state["execution_id"])
            coder_generations = state.get("coder_generations", 0) + 1
            await repo.append_event(
                run_id,
                EventKind.STEP_STARTED,
                {"step_key": "coder", "generation": coder_generations},
                execution_id,
            )
            await repo.commit()
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
                        plan.summary,
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
                EventKind.GENERATION,
                {
                    "step_key": "coder",
                    "generation": coder_generations,
                    "files": [
                        {"file_path": item["file_path"], "checksum": item["checksum"]}
                        for item in code
                    ],
                },
                execution_id,
            )
            await repo.append_event(
                run_id,
                EventKind.STEP_COMPLETED,
                {"step_key": "coder", "generation": coder_generations},
                execution_id,
            )
            await repo.update_run(run_id, latest_code=code)
            await repo.commit()
            return {
                "code": code,
                "coder_generations": coder_generations,
            }

    async def reviewer(self, state: GraphState) -> GraphState:
        async with self.repo_factory() as repo:
            run_id = UUID(state["run_id"])
            execution_id = UUID(state["execution_id"])
            await repo.append_event(
                run_id, EventKind.STEP_STARTED, {"step_key": "reviewer"}, execution_id
            )
            await repo.commit()
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
                EventKind.STEP_COMPLETED,
                {
                    "step_key": "reviewer",
                    "verdict": verdict.value,
                    "summary": feedback.summary,
                    "issues": [
                        {"message": issue.message, "file_path": issue.file_path}
                        for issue in feedback.issues
                    ],
                    "checks": checks_data,
                },
                execution_id,
            )
            await repo.commit()
            return {
                "feedback": feedback_data,
                "checks": checks_data,
                "approved": is_approved(feedback, check_report),
                "handoff": requires_handoff(
                    feedback,
                    state.get("coder_generations", 0),
                    self.max_coder_generations,
                    check_report,
                ),
            }

    def route_after_review(self, state: GraphState) -> str:
        if state.get("error"):
            return "end"
        if state.get("approved"):
            return "end"
        if state.get("handoff"):
            return "end"
        if generation_budget_exhausted(
            state.get("coder_generations", 0), self.max_coder_generations
        ):
            return "end"
        return "coder"
