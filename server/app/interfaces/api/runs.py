from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.domain.enums import TERMINAL_STATUSES, ConversationSender, EventKind, RunStatus
from app.domain.errors import WorkspaceError
from app.infrastructure.persistence.models import Run, User
from app.infrastructure.persistence.repository import SqlAlchemyRepository
from app.interfaces.dependencies import get_current_user, get_repo
from app.interfaces.schemas.models import (
    ArtifactRead,
    EventRead,
    ResumeRequest,
    RunCreate,
    RunCreated,
    RunRead,
)

router = APIRouter(prefix="/runs", tags=["runs"])


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _run_read(run: Run) -> RunRead:
    return RunRead(
        id=run.id,
        project_id=run.project_id,
        message=run.message,
        status=run.status.value,
        attempts_used=run.attempts_used,
        attempts_total=run.attempts_total,
        round=run.round,
        user_guidance=run.user_guidance,
        plan=run.plan,
        latest_code=run.latest_code or [],
        feedback=run.feedback,
        checks=run.checks,
        chat_reply=run.chat_reply,
        error=run.error,
    )


async def _owned_run(repo: SqlAlchemyRepository, run_id: UUID, user: User) -> Run:
    run = await repo.get_run_model(run_id)
    if run is None or run.owner_id != user.id:
        raise _not_found()
    return run


@router.post("", response_model=RunCreated, status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    payload: RunCreate,
    request: Request,
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> RunCreated:
    settings = request.app.state.container.settings
    if payload.project_id is not None:
        project = await repo.get_owned_project(payload.project_id, user.id)
        if project is None:
            raise _not_found()
        if await repo.active_run_for_project(payload.project_id) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A run is already active for this project",
            )
        count = await repo.count_runs_for_project(payload.project_id)
        if count >= settings.max_runs_per_project:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Project run cap reached",
            )
    else:
        if await repo.active_projectless_run(user.id) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A project-less run is already active",
            )

    previous = (
        await repo.latest_run_for_project(payload.project_id)
        if payload.project_id is not None
        else None
    )
    run = await repo.create_run(
        user.id,
        payload.message,
        payload.project_id,
        plan=previous.plan if previous else None,
        latest_code=previous.latest_code if previous else None,
        feedback=previous.feedback if previous else None,
        checks=previous.checks if previous else None,
    )
    return RunCreated(run_id=run.id, project_id=run.project_id)


@router.get("/{run_id}", response_model=RunRead)
async def get_run(
    run_id: UUID,
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> RunRead:
    return _run_read(await _owned_run(repo, run_id, user))


@router.get("/{run_id}/events", response_model=list[EventRead])
async def list_events(
    run_id: UUID,
    after: datetime | None = Query(default=None),
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> list[EventRead]:
    await _owned_run(repo, run_id, user)
    events = await repo.list_events(run_id, after=after)
    return [
        EventRead(
            id=event.id, kind=event.kind.value, payload=event.payload, created_at=event.created_at
        )
        for event in events
    ]


@router.get("/{run_id}/trace")
async def get_trace(
    run_id: UUID,
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> dict:
    await _owned_run(repo, run_id, user)
    graph_runs = await repo.get_trace(run_id)
    return {
        "run_id": str(run_id),
        "rounds": [
            {
                "id": str(graph_run.id),
                "round": graph_run.round,
                "thread_id": graph_run.thread_id,
                "created_at": graph_run.created_at,
                "events": [
                    {
                        "node": event.node,
                        "payload": event.payload,
                        "created_at": event.created_at,
                    }
                    for event in sorted(graph_run.events, key=lambda item: item.created_at)
                ],
            }
            for graph_run in graph_runs
        ],
    }


@router.get("/{run_id}/artifacts/{artifact_id}", response_model=ArtifactRead)
async def get_artifact(
    run_id: UUID,
    artifact_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> ArtifactRead:
    await _owned_run(repo, run_id, user)
    artifact = await repo.get_artifact(artifact_id)
    if artifact is None or artifact.run_id != run_id:
        raise _not_found()
    try:
        content = await request.app.state.container.workspace.read_artifact(
            artifact_id, artifact.file_path
        )
    except (FileNotFoundError, WorkspaceError) as exc:
        raise _not_found() from exc
    return ArtifactRead(
        id=artifact.id,
        file_path=artifact.file_path,
        file_type=artifact.file_type.value,
        checksum=artifact.checksum,
        content=content,
    )


@router.post("/{run_id}/resume", response_model=RunRead)
async def resume_run(
    run_id: UUID,
    payload: ResumeRequest,
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> RunRead:
    run = await _owned_run(repo, run_id, user)
    if run.status != RunStatus.AWAITING_USER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only awaiting_user runs can be resumed",
        )
    run.round += 1
    run.attempts_used = 0
    run.user_guidance = payload.guidance
    run.status = RunStatus.PENDING
    run.chat_reply = None
    run.error = None
    run.locked_by = None
    run.locked_at = None
    run.heartbeat_at = None
    await repo.add_conversation_turn(
        run.owner_id, payload.guidance, ConversationSender.USER, run.id, run.project_id
    )
    await repo.append_event(
        run.id, EventKind.GUIDANCE, {"guidance": payload.guidance, "round": run.round}
    )
    await repo.session.commit()
    await repo.session.refresh(run)
    return _run_read(run)


@router.post("/{run_id}/close", response_model=RunRead)
async def close_run(
    run_id: UUID,
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> RunRead:
    run = await _owned_run(repo, run_id, user)
    if run.status != RunStatus.AWAITING_USER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only awaiting_user runs can be closed",
        )
    message = "Run closed by the user. The generated files and unresolved issues are preserved."
    run.status = RunStatus.FAILED
    run.chat_reply = message
    run.locked_by = None
    run.locked_at = None
    run.heartbeat_at = None
    await repo.append_event(run.id, EventKind.ABANDONMENT, {"message": message})
    await repo.append_event(run.id, EventKind.CHAT_REPLY, {"message": message})
    await repo.append_event(run.id, EventKind.RUN_END, {"status": RunStatus.FAILED.value})
    await repo.add_conversation_turn(
        run.owner_id, message, ConversationSender.ASSISTANT, run.id, run.project_id
    )
    await repo.session.commit()
    await repo.session.refresh(run)
    return _run_read(run)


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_run(
    run_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> None:
    run = await _owned_run(repo, run_id, user)
    if run.status not in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Only terminal runs can be deleted"
        )
    artifact_ids = await repo.list_artifact_ids_for_run(run_id)
    for artifact_id in artifact_ids:
        await request.app.state.container.workspace.delete_artifact(artifact_id)
    await repo.delete_run(run_id)
