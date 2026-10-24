from __future__ import annotations

import logging
import time
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
from app.application.usage import merge_usage
from app.domain.enums import EventKind, FileType, ReviewerVerdict
from app.domain.errors import ModelError
from app.domain.models import Code, Feedback, Issue, Plan, PlanStep, validate_file_type
from app.domain.policies import is_approved, requires_handoff
from app.domain.serialization import (
    checksum,
    code_payload,
    feedback_payload,
    plan_from_payload,
    plan_payload,
)

logger = logging.getLogger(__name__)


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
    handoff_reason: str | None
    token_usage: dict[str, Any]
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
        config = dict(config)
        config["recursion_limit"] = max(
            config.get("recursion_limit", 25), self.max_coder_generations * 2 + 2
        )
        return await self.graph.ainvoke(state, config=config)

    async def planner(self, state: GraphState) -> GraphState:
        step_started = time.perf_counter()
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
            llm_result = await self.llm_clients["planner"].complete(
                "planner",
                planner_messages(state["message"], memory, chat_history, state.get("guidance")),
                PLANNER_SCHEMA,
            )
            step_usage = llm_result.telemetry()
            try:
                parsed = PlannerResponse.model_validate(llm_result.content)
            except Exception as exc:
                raise ModelError(
                    f"invalid planner output: {exc}",
                    tag="planner_error",
                    telemetry=step_usage,
                ) from exc
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
                {
                    "step_key": "planner",
                    "plan": plan_payload(plan),
                    "model_usage": step_usage,
                },
                execution_id,
            )
            await repo.commit()
            token_usage = merge_usage(state.get("token_usage"), step_usage)
            logger.info(
                "agent_step_completed",
                extra={
                    "event": "agent_step_completed",
                    "run_id": state["run_id"],
                    "execution_id": state["execution_id"],
                    "step_key": "planner",
                    "duration_ms": round((time.perf_counter() - step_started) * 1000),
                    "model_usage": step_usage,
                },
            )
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
                "token_usage": token_usage,
            }

    async def coder(self, state: GraphState) -> GraphState:
        step_started = time.perf_counter()
        async with self.repo_factory() as repo:
            run_id = UUID(state["run_id"])
            execution_id = UUID(state["execution_id"])
            previous_generations = state.get("coder_generations", 0)
            coder_generations = previous_generations + 1
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
            generation_usage = None
            for step in plan.steps:
                existing = await self.workspace.read_all(plan.project_id)
                try:
                    llm_result = await self.llm_clients["coder"].complete(
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
                            state.get("feedback") if previous_generations else None,
                        ),
                        CODER_SCHEMA,
                    )
                except ModelError as exc:
                    generation_usage = merge_usage(generation_usage, exc.telemetry)
                    raise ModelError(str(exc), tag=exc.tag, telemetry=generation_usage) from exc
                generation_usage = merge_usage(generation_usage, llm_result.telemetry())
                try:
                    parsed = CoderResponse.model_validate(llm_result.content)
                except Exception as exc:
                    raise ModelError(
                        f"invalid coder output for {step.file_path}: {exc}",
                        tag="coder_error",
                        telemetry=generation_usage,
                    ) from exc
                if parsed.file_path != step.file_path:
                    raise ModelError(
                        f"coder returned {parsed.file_path}, expected {step.file_path}",
                        tag="coder_error",
                        telemetry=generation_usage,
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
                    "model_usage": generation_usage,
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
            token_usage = merge_usage(state.get("token_usage"), generation_usage)
            logger.info(
                "agent_step_completed",
                extra={
                    "event": "agent_step_completed",
                    "run_id": state["run_id"],
                    "execution_id": state["execution_id"],
                    "step_key": "coder",
                    "generation": coder_generations,
                    "files_written": len(code),
                    "duration_ms": round((time.perf_counter() - step_started) * 1000),
                    "model_usage": generation_usage,
                },
            )
            return {
                "code": code,
                "coder_generations": coder_generations,
                "token_usage": token_usage,
            }

    async def reviewer(self, state: GraphState) -> GraphState:
        step_started = time.perf_counter()
        async with self.repo_factory() as repo:
            run_id = UUID(state["run_id"])
            execution_id = UUID(state["execution_id"])
            await repo.append_event(
                run_id, EventKind.STEP_STARTED, {"step_key": "reviewer"}, execution_id
            )
            await repo.commit()
            plan = plan_from_payload(state["plan"])
            code = state.get("code", [])
            generated_paths = {item["file_path"] for item in code}
            existing_files = {
                path: content
                for path, content in (await self.workspace.read_all(plan.project_id)).items()
                if path not in generated_paths
            }
            check_report = await self.workspace.check_readable(
                plan.project_id, [step.file_path for step in plan.steps]
            )
            llm_result = await self.llm_clients["reviewer"].complete(
                "reviewer",
                reviewer_messages(
                    state["message"],
                    state.get("guidance"),
                    plan,
                    code,
                    existing_files,
                    check_report,
                    state.get("memory", ""),
                ),
                REVIEWER_SCHEMA,
            )
            step_usage = llm_result.telemetry()
            try:
                parsed = ReviewerResponse.model_validate(llm_result.content)
            except Exception as exc:
                raise ModelError(
                    f"invalid reviewer output: {exc}",
                    tag="reviewer_error",
                    telemetry=step_usage,
                ) from exc
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
                    "model_usage": step_usage,
                },
                execution_id,
            )
            await repo.commit()
            token_usage = merge_usage(state.get("token_usage"), step_usage)
            logger.info(
                "agent_step_completed",
                extra={
                    "event": "agent_step_completed",
                    "run_id": state["run_id"],
                    "execution_id": state["execution_id"],
                    "step_key": "reviewer",
                    "duration_ms": round((time.perf_counter() - step_started) * 1000),
                    "model_usage": step_usage,
                },
            )
            handoff = requires_handoff(
                feedback,
                state.get("coder_generations", 0),
                self.max_coder_generations,
                check_report,
            )
            handoff_reason = (
                "reviewer_escalated"
                if verdict == ReviewerVerdict.ESCALATE
                else "generation_budget_exhausted"
                if handoff
                else None
            )
            return {
                "feedback": feedback_data,
                "checks": checks_data,
                "approved": is_approved(feedback, check_report),
                "handoff": handoff,
                "handoff_reason": handoff_reason,
                "token_usage": token_usage,
            }

    def route_after_review(self, state: GraphState) -> str:
        if state.get("error"):
            return "end"
        if state.get("approved"):
            return "end"
        if state.get("handoff"):
            return "end"
        return "coder"
