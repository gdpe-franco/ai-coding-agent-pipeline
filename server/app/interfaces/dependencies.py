from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi_users import FastAPIUsers
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.manager import BaseUserManager, UUIDIDMixin
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.persistence.models import User
from app.infrastructure.persistence.repository import SqlAlchemyRepository

bearer_scheme = HTTPBearer(auto_error=False)


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    def __init__(self, user_db: SQLAlchemyUserDatabase, secret_key: str) -> None:
        super().__init__(user_db)
        self.reset_password_token_secret = secret_key
        self.verification_token_secret = secret_key


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    container = request.app.state.container
    async with container.session_factory() as session:
        yield session


async def get_repo(session: AsyncSession = Depends(get_session)) -> SqlAlchemyRepository:
    return SqlAlchemyRepository(session)


async def get_user_manager(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    manager = UserManager(
        SQLAlchemyUserDatabase(session, User),
        request.app.state.container.settings.secret_key,
    )
    yield manager


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = jwt.decode(
            credentials.credentials,
            request.app.state.container.settings.secret_key,
            algorithms=["HS256"],
            audience="fastapi-users:auth",
        )
        user_id = uuid.UUID(str(payload.get("sub")))
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user")
    return user


def build_fastapi_users(settings) -> FastAPIUsers:
    from fastapi_users.authentication import AuthenticationBackend, BearerTransport, JWTStrategy

    bearer_transport = BearerTransport(tokenUrl="/api/v1/auth/login")

    def get_jwt_strategy() -> JWTStrategy:
        return JWTStrategy(secret=settings.secret_key, lifetime_seconds=60 * 60 * 24 * 7)

    backend = AuthenticationBackend(
        name="jwt", transport=bearer_transport, get_strategy=get_jwt_strategy
    )
    return FastAPIUsers(get_user_manager, [backend]), backend
