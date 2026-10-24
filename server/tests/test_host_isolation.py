from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.interfaces.dependencies import get_session
from app.main import create_app
from app.settings import Settings


def test_app_and_preview_hosts_are_isolated(tmp_path) -> None:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("app", encoding="utf-8")
    app = create_app(
        Settings(
            app_env="development",
            secret_key="test-secret-key-that-is-long-enough-32",
            public_url="http://localhost:8000",
            frontend_origin="http://localhost:5173",
            preview_host="http://preview.localhost:8000",
            rate_limit_enabled=False,
            data_dir=tmp_path / "data",
            static_dir=static_dir,
        )
    )
    looked_up = False

    class Session:
        async def scalar(self, statement):
            nonlocal looked_up
            looked_up = True

    async def fake_session():
        yield Session()

    app.dependency_overrides[get_session] = fake_session
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/").text == "app"

        looked_up = False
        blocked_preview = client.get(f"/preview/{uuid4()}/index.html")
        assert blocked_preview.status_code == 404
        assert not looked_up

        assert client.get("/", headers={"Host": "preview.localhost"}).status_code == 404
        assert client.get("/", headers={"Host": "unknown.example"}).status_code == 404

        allowed_preview = client.get(
            f"/preview/{uuid4()}/index.html", headers={"Host": "preview.localhost"}
        )
        assert allowed_preview.status_code == 404
        assert looked_up


def test_preview_host_cannot_share_app_hostname(tmp_path) -> None:
    with pytest.raises(ValidationError, match="PREVIEW_HOST"):
        Settings(
            app_env="development",
            secret_key="test-secret-key-that-is-long-enough-32",
            public_url="https://app.example.test",
            frontend_origin="https://app.example.test",
            preview_host="http://app.example.test:8001",
            data_dir=tmp_path / "data",
        )
