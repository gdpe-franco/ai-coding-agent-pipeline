from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.infrastructure.persistence.models import User
from app.infrastructure.persistence.repository import SqlAlchemyRepository
from app.interfaces.dependencies import get_current_user, get_repo
from app.interfaces.schemas.models import ChatRead, ConversationTurnRead

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("", response_model=ChatRead)
async def get_chat(
    after: int | None = Query(default=None, ge=0),
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> ChatRead:
    conversation = await repo.get_or_create_conversation(user.id)
    await repo.session.commit()
    turns = await repo.list_conversation_turns(user.id, after=after)
    return ChatRead(
        conversation_id=conversation.id,
        context_version=conversation.context_version,
        selected_project_id=conversation.selected_project_id,
        active_feature_run_id=conversation.active_feature_run_id,
        pending_selection=None,
        turns=[
            ConversationTurnRead(
                id=turn.id,
                role=turn.role.value,
                content=turn.content,
                sequence=turn.sequence,
                project_id=turn.project_id,
                run_id=turn.run_id,
                created_at=turn.created_at,
            )
            for turn in turns
        ],
    )
