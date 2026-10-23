from __future__ import annotations

import mimetypes
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.domain.errors import WorkspaceError
from app.infrastructure.persistence.models import Project, User
from app.infrastructure.persistence.repository import SqlAlchemyRepository
from app.interfaces.dependencies import get_current_user, get_repo
from app.interfaces.schemas.models import (
    FileList,
    PreviewUrlRead,
    ProjectRead,
)

router = APIRouter(prefix="/projects", tags=["projects"])


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _project_read(project: Project) -> ProjectRead:
    return ProjectRead(
        id=project.id,
        chat_id=project.chat.id,
        title=project.title,
        summary=project.summary,
        preview_id=project.preview_id,
        status=project.status.value,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.get("", response_model=list[ProjectRead])
async def list_projects(
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> list[ProjectRead]:
    return [_project_read(project) for project in await repo.list_projects(user.id)]


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(
    project_id: UUID,
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> ProjectRead:
    project = await repo.get_owned_project(project_id, user.id)
    if project is None:
        raise _not_found()
    return _project_read(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> None:
    project = await repo.get_owned_project(project_id, user.id)
    if project is None:
        raise _not_found()
    chat = await repo.get_owned_chat_for_project_for_update(user.id, project_id)
    if chat is None:
        raise _not_found()
    if await repo.active_run_for_chat(chat.id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A project with a non-terminal run cannot be deleted",
        )
    container = request.app.state.container
    artifact_ids = await repo.list_artifact_ids_for_project(project_id)
    for artifact_id in artifact_ids:
        await container.workspace.delete_artifact(artifact_id)
    await container.workspace.delete_project(project_id)
    await container.memory.delete_project(project_id)
    await repo.delete_chat(user.id, chat.id)


@router.get("/{project_id}/files", response_model=FileList)
async def list_files(
    project_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> FileList:
    project = await repo.get_owned_project(project_id, user.id)
    if project is None:
        raise _not_found()
    return FileList(files=await request.app.state.container.workspace.list_files(project_id))


@router.get("/{project_id}/files/{path:path}")
async def read_file(
    project_id: UUID,
    path: str,
    request: Request,
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> Response:
    project = await repo.get_owned_project(project_id, user.id)
    if project is None:
        raise _not_found()
    try:
        content = await request.app.state.container.workspace.read_file(project_id, path)
        media_type = mimetypes.guess_type(path)[0] or "text/plain"
        return Response(content=content, media_type=media_type)
    except FileNotFoundError as exc:
        raise _not_found() from exc
    except WorkspaceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/{project_id}/preview-url", response_model=PreviewUrlRead)
async def preview_url(
    project_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    repo: SqlAlchemyRepository = Depends(get_repo),
) -> PreviewUrlRead:
    project = await repo.get_owned_project(project_id, user.id)
    if project is None:
        raise _not_found()
    settings = request.app.state.container.settings
    base = settings.preview_host.rstrip("/")
    return PreviewUrlRead(url=f"{base}/preview/{project.preview_id}/")
