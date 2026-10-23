from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status

from app.domain.errors import RunConflictError
from app.infrastructure.persistence.models import Chat, ChatMessage, Run, User
from app.infrastructure.persistence.repository import SqlAlchemyRepository
from app.interfaces.dependencies import get_current_user, get_repo
from app.interfaces.schemas.models import (
    ChatCreate,
    ChatCreated,
    ChatMessageCreate,
    ChatMessageCreated,
    ChatMessageRead,
    ChatRead,
    ChatRename,
    RunRead,
)

router = APIRouter(prefix="/chats", tags=["chats"])


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _chat_read(chat: Chat) -> ChatRead:
    return ChatRead(
        id=chat.id,
        project_id=chat.project_id,
        title=chat.project.title,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
    )


def _run_read(run: Run) -> RunRead:
    return RunRead(
        id=run.id,
        chat_id=run.chat_id,
        project_id=run.chat.project_id,
        execution_id=run.execution_id,
        message=run.message,
        status=run.status.value,
        user_guidance=run.user_guidance,
        plan=run.plan,
        latest_code=run.latest_code or [],
        feedback=run.feedback,
        checks=run.checks,
        chat_reply=run.chat_reply,
        error=run.error,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _message_read(message: ChatMessage) -> ChatMessageRead:
    return ChatMessageRead(
        id=message.id,
        chat_id=message.chat_id,
        run_id=message.run_id,
        role=message.role.value,
        content=message.content,
        sequence=message.sequence,
        run_status=message.run.status if message.run is not None else None,
        created_at=message.created_at,
    )


@router.get("", response_model=list[ChatRead])
async def list_chats(
    user: User = Depends(get_current_user), repo: SqlAlchemyRepository = Depends(get_repo)
) -> list[ChatRead]:
    return [_chat_read(chat) for chat in await repo.list_chats(user.id)]


@router.post("", response_model=ChatCreated, status_code=status.HTTP_201_CREATED)
async def create_chat(
    payload: ChatCreate,
    response: Response,
    idempotency_key: UUID = Header(alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> ChatCreated:
    try:
        chat, project, run, created = await repo.create_chat(
            user.id, idempotency_key, payload.message
        )
    except RunConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    response.headers["Location"] = f"/api/v1/chats/{chat.id}"
    if not created:
        response.status_code = status.HTTP_200_OK
    return ChatCreated(chat_id=chat.id, project_id=project.id, run_id=run.id)


@router.get("/{chat_id}", response_model=ChatRead)
async def get_chat(
    chat_id: UUID,
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> ChatRead:
    chat = await repo.get_owned_chat(user.id, chat_id)
    if chat is None:
        raise _not_found()
    return _chat_read(chat)


@router.patch("/{chat_id}", response_model=ChatRead)
async def rename_chat(
    chat_id: UUID,
    payload: ChatRename,
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> ChatRead:
    chat = await repo.rename_chat(user.id, chat_id, payload.title)
    if chat is None:
        raise _not_found()
    return _chat_read(chat)


@router.get("/{chat_id}/messages", response_model=list[ChatMessageRead])
async def list_messages(
    chat_id: UUID,
    after: int | None = Query(default=None, ge=0),
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> list[ChatMessageRead]:
    if await repo.get_owned_chat(user.id, chat_id) is None:
        raise _not_found()
    return [
        _message_read(message)
        for message in await repo.list_messages_for_chat(user.id, chat_id, after=after)
    ]


@router.post(
    "/{chat_id}/messages",
    response_model=ChatMessageCreated,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_message(
    chat_id: UUID,
    payload: ChatMessageCreate,
    response: Response,
    request: Request,
    idempotency_key: UUID = Header(alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> ChatMessageCreated:
    try:
        message, run, created = await repo.create_chat_message(
            user.id,
            chat_id,
            idempotency_key,
            payload.message,
            request.app.state.container.settings.max_runs_per_project,
        )
    except RunConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if message is None:
        raise _not_found()
    response.headers["Location"] = f"/api/v1/runs/{run.id}"
    if not created:
        response.status_code = status.HTTP_200_OK
    return ChatMessageCreated(message_id=message.id, run_id=run.id, status=run.status)


@router.get("/{chat_id}/runs", response_model=list[RunRead])
async def list_runs(
    chat_id: UUID,
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> list[RunRead]:
    if await repo.get_owned_chat(user.id, chat_id) is None:
        raise _not_found()
    return [_run_read(run) for run in await repo.list_runs_for_chat(user.id, chat_id)]


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(
    chat_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> None:
    chat = await repo.get_owned_chat_for_update(user.id, chat_id)
    if chat is None:
        raise _not_found()
    if await repo.active_run_for_chat(chat_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A chat with a non-terminal run cannot be deleted",
        )
    project_id = chat.project_id
    artifact_ids = await repo.list_artifact_ids_for_project(project_id)
    for artifact_id in artifact_ids:
        await request.app.state.container.workspace.delete_artifact(artifact_id)
    await request.app.state.container.workspace.delete_project(project_id)
    await request.app.state.container.memory.delete_project(project_id)
    deleted_project_id = await repo.delete_chat(user.id, chat_id)
    if deleted_project_id is None:
        raise _not_found()
