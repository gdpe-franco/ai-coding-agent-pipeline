from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from app.application.agent_schemas import INTENT_SCHEMA, IntentResponse
from app.application.ports import ChatRequestRepositoryFactory, LLMClient
from app.application.prompts import intent_messages
from app.domain.enums import ChatIntentAction

logger = logging.getLogger(__name__)
MAX_RECENT_TURNS = 10
MAX_RECENT_CHARS = 4000


def bounded_recent_turns(turns: list[dict[str, str]]) -> list[dict[str, str]]:
    remaining = MAX_RECENT_CHARS
    bounded = []
    for turn in reversed(turns[-MAX_RECENT_TURNS:]):
        if remaining == 0:
            break
        content = turn["content"][-remaining:]
        bounded.append({"role": turn["role"], "content": content})
        remaining -= len(content)
    return list(reversed(bounded))


class ChatRequestProcessor:
    def __init__(
        self,
        repo_factory: ChatRequestRepositoryFactory,
        llm: LLMClient,
        worker_id: str,
        heartbeat_seconds: float,
        stale_seconds: int,
        max_runs_per_project: int,
    ) -> None:
        self.repo_factory = repo_factory
        self.llm = llm
        self.worker_id = worker_id
        self.heartbeat_seconds = heartbeat_seconds
        self.stale_seconds = stale_seconds
        self.max_runs_per_project = max_runs_per_project

    async def process_one(self) -> bool:
        async with self.repo_factory() as repo:
            request = await repo.claim_chat_request(self.worker_id, self.stale_seconds)
        if request is None:
            return False

        heartbeat = asyncio.create_task(self._heartbeat(request.id))
        try:
            if request.project_id is None or request.project_title is None:
                action = ChatIntentAction.REQUEST_SELECTION
                reason = "The selected project is no longer available. Choose another project."
                intent_error = False
            else:
                response = await self.llm.complete(
                    "planner",
                    intent_messages(
                        request.message,
                        request.project_title,
                        request.feature_title,
                        bounded_recent_turns(request.recent_turns),
                    ),
                    INTENT_SCHEMA,
                )
                intent = IntentResponse.model_validate(response)
                action = ChatIntentAction(intent.action)
                reason = intent.reason
                intent_error = False
        except Exception:
            logger.warning("intent detection failed for chat request %s", request.id, exc_info=True)
            action = ChatIntentAction.REQUEST_SELECTION
            reason = "We couldn't determine the right project or feature. Choose one to continue."
            intent_error = True
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass

        try:
            async with self.repo_factory() as repo:
                await repo.resolve_chat_request(
                    request.id,
                    request.owner_id,
                    self.worker_id,
                    action,
                    reason,
                    self.max_runs_per_project,
                    intent_error=intent_error,
                )
        except Exception:
            logger.exception("could not resolve chat request %s", request.id)
        return True

    async def _heartbeat(self, request_id: UUID) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            try:
                async with self.repo_factory() as repo:
                    await repo.heartbeat_chat_request(request_id, self.worker_id)
                    await repo.heartbeat(self.worker_id, None)
            except Exception:
                logger.warning("chat-request heartbeat failed", exc_info=True)


__all__ = ["ChatRequestProcessor", "bounded_recent_turns"]
