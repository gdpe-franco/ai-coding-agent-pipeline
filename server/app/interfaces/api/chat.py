from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.domain.errors import InvalidTransitionError, RunConflictError
from app.infrastructure.persistence.models import User
from app.infrastructure.persistence.repository import SqlAlchemyRepository
from app.interfaces.dependencies import get_current_user, get_repo
from app.interfaces.schemas.models import (
    ChatMessageCreate,
    ChatMessageCreated,
    ChatRead,
    ChatSelectionCreate,
    ChatSelectionCreated,
    ConversationTurnRead,
    PendingSelectionRead,
    ProjectOptionRead,
)

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/messages", response_model=ChatMessageCreated, status_code=status.HTTP_202_ACCEPTED)
async def submit_message(
    payload: ChatMessageCreate,
    request: Request,
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> ChatMessageCreated:
    try:
        turn = await repo.create_chat_request(
            user.id, payload.message, request_selection=payload.request_selection
        )
    except RunConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await repo.session.commit()
    return ChatMessageCreated(turn_id=turn.id)


@router.post(
    "/messages/{turn_id}/selection",
    response_model=ChatSelectionCreated,
    status_code=status.HTTP_202_ACCEPTED,
)
async def confirm_selection(
    turn_id: UUID,
    payload: ChatSelectionCreate,
    request: Request,
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> ChatSelectionCreated:
    try:
        selection = await repo.confirm_chat_selection(
            user.id,
            turn_id,
            payload.context_version,
            payload.choice,
            payload.feature_mode,
            request.app.state.container.settings.max_runs_per_project,
            project_id=payload.project_id,
            title=payload.title,
        )
    except (InvalidTransitionError, RunConflictError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if selection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return ChatSelectionCreated(
        run_id=selection.run_id,
        project_id=selection.project_id,
        context_version=selection.context_version,
    )


@router.post("/messages/{turn_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_selection(
    turn_id: UUID,
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> None:
    try:
        found = await repo.cancel_chat_request(user.id, turn_id)
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


@router.get("", response_model=ChatRead)
async def get_chat(
    after: int | None = Query(default=None, ge=0),
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> ChatRead:
    conversation = await repo.get_conversation(user.id)
    if conversation is None:
        conversation = await repo.get_or_create_conversation(user.id)
        await repo.session.commit()
    turns = await repo.list_conversation_turns(user.id, after=after)
    pending_turn = await repo.get_pending_chat_request(user.id)
    pending_selection = None
    if pending_turn is not None:
        projects = await repo.list_projects(user.id)
        pending_selection = PendingSelectionRead(
            turn_id=pending_turn.id,
            context_version=conversation.context_version,
            reason=(pending_turn.intent_result or {}).get("reason"),
            projects=[
                ProjectOptionRead(id=project.id, title=project.title) for project in projects
            ],
        )
    return ChatRead(
        conversation_id=conversation.id,
        context_version=conversation.context_version,
        selected_project_id=conversation.selected_project_id,
        active_feature_run_id=conversation.active_feature_run_id,
        pending_selection=pending_selection,
        turns=[
            ConversationTurnRead(
                id=turn.id,
                role=turn.role.value,
                content=turn.content,
                sequence=turn.sequence,
                project_id=turn.project_id,
                run_id=turn.run_id,
                request_status=turn.request_status,
                created_at=turn.created_at,
            )
            for turn in turns
        ],
    )
