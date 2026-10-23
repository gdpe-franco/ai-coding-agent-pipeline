from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.persistence.models import User
from app.infrastructure.persistence.repository import SqlAlchemyRepository
from app.main import create_app
from app.settings import Settings

APP_TABLES = (
    "run_events",
    "artifacts",
    "chat_messages",
    "runs",
    "chats",
    "projects",
    "users",
    "worker_heartbeats",
    "rate_limit_counters",
)


def _connect() -> psycopg.Connection:
    return psycopg.connect(Settings().psycopg_url, connect_timeout=2)


@pytest.fixture(scope="session")
def database_available() -> bool:
    try:
        with _connect():
            return True
    except Exception:  # noqa: BLE001 - tests skip without a database
        return False


@pytest.fixture(scope="session")
def require_database(database_available: bool) -> None:
    if not database_available:
        pytest.skip("PostgreSQL is not available for integration tests")


@pytest.fixture(autouse=True)
def clean_tables(database_available: bool) -> Iterator[None]:
    if not database_available:
        yield
        return
    with _connect() as connection:
        with connection.cursor() as cursor:
            for table in APP_TABLES:
                cursor.execute(f"TRUNCATE TABLE {table} CASCADE")
        connection.commit()
    yield


@pytest.fixture
async def db_engine():
    engine = create_async_engine(Settings().database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(db_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
def repo_factory(session_factory):
    @asynccontextmanager
    async def repository() -> AsyncIterator[SqlAlchemyRepository]:
        async with session_factory() as session:
            yield SqlAlchemyRepository(session)

    return repository


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        secret_key="test-secret-key-that-is-long-enough-32",
        rate_limit_enabled=False,
        data_dir=tmp_path / "data",
        static_dir=tmp_path / "static",
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def register_and_login(client: TestClient, email: str) -> str:
    response = client.post(
        "/api/v1/auth/register", json={"email": email, "password": "password123"}
    )
    assert response.status_code == 201, response.text
    response = client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": "password123"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def create_test_user(session: AsyncSession, email: str) -> User:
    user = User(
        email=email,
        hashed_password="x",
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    session.add(user)
    await session.flush()
    return user


async def create_test_chat(session: AsyncSession, owner_id, message: str = "Build a project"):
    return await SqlAlchemyRepository(session).create_chat(owner_id, uuid4(), uuid4(), message)
