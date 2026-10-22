from __future__ import annotations

import mimetypes
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import WorkspaceError
from app.infrastructure.persistence.repository import SqlAlchemyRepository
from app.interfaces.dependencies import get_session

router = APIRouter(tags=["preview"])


@router.get("/preview/{preview_id}/{path:path}")
async def preview(
    preview_id: UUID,
    path: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    repo = SqlAlchemyRepository(session)
    project = await repo.get_project_by_preview(preview_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    relative = path or "index.html"
    try:
        content = await request.app.state.container.workspace.read_preview(project.id, relative)
    except (FileNotFoundError, WorkspaceError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from exc
    media_type = mimetypes.guess_type(relative)[0] or "application/octet-stream"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "X-Robots-Tag": "noindex",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        },
    )


@router.get("/preview/{preview_id}")
async def preview_root(
    preview_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    return await preview(preview_id, "", request, session)
