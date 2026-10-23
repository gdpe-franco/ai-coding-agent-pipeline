from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status

from app.domain.enums import TERMINAL_STATUSES
from app.domain.errors import RunConflictError, WorkspaceError
from app.infrastructure.persistence.models import Run, User
from app.infrastructure.persistence.repository import SqlAlchemyRepository
from app.interfaces.dependencies import get_current_user, get_repo
from app.interfaces.schemas.models import (
    ArtifactRead,
    ContinuationRequest,
    EventRead,
    RunArtifactRead,
    RunRead,
)

router = APIRouter(prefix="/runs", tags=["runs"])


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


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


async def _owned_run(repo: SqlAlchemyRepository, run_id: UUID, user: User) -> Run:
    run = await repo.get_owned_run(user.id, run_id)
    if run is None:
        raise _not_found()
    return run


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
    after: int | None = Query(default=None, ge=0),
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> list[EventRead]:
    await _owned_run(repo, run_id, user)
    return [
        EventRead(
            id=event.id,
            sequence=event.sequence,
            execution_id=event.execution_id,
            kind=event.kind,
            payload=event.payload,
            created_at=event.created_at,
        )
        for event in await repo.list_events(run_id, after=after)
    ]


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
        execution_id=artifact.execution_id,
        file_path=artifact.file_path,
        file_type=artifact.file_type.value,
        checksum=artifact.checksum,
        content=content,
    )


@router.get("/{run_id}/artifacts", response_model=list[RunArtifactRead])
async def list_artifacts(
    run_id: UUID,
    execution_id: UUID | None = Query(default=None),
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> list[RunArtifactRead]:
    await _owned_run(repo, run_id, user)
    return [
        RunArtifactRead(
            id=artifact.id,
            execution_id=artifact.execution_id,
            file_path=artifact.file_path,
            file_type=artifact.file_type.value,
            checksum=artifact.checksum,
            created_at=artifact.created_at,
        )
        for artifact in await repo.artifacts_for_run(run_id, execution_id=execution_id)
    ]


@router.post(
    "/{run_id}/continuations", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED
)
async def continue_run(
    run_id: UUID,
    payload: ContinuationRequest,
    response: Response,
    idempotency_key: UUID = Header(alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> RunRead:
    try:
        result = await repo.continue_run(user.id, run_id, idempotency_key, payload.guidance)
    except RunConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if result is None:
        raise _not_found()
    _, run, created = result
    if not created:
        response.status_code = status.HTTP_200_OK
    resumed = await repo.get_owned_run(user.id, run.id)
    if resumed is None:
        raise _not_found()
    return _run_read(resumed)


@router.post("/{run_id}/close", response_model=RunRead)
async def close_run(
    run_id: UUID,
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> RunRead:
    try:
        run = await repo.close_run(user.id, run_id)
    except RunConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if run is None:
        raise _not_found()
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
    for artifact_id in await repo.list_artifact_ids_for_run(run_id):
        await request.app.state.container.workspace.delete_artifact(artifact_id)
    await request.app.state.container.workspace.delete_run_archive(run.chat.project_id, run.id)
    await repo.delete_run(run_id)
