from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.settings import Settings


@pytest.mark.parametrize("secret_key", ["short", "change-me-generate-a-strong-value"])
def test_production_rejects_weak_or_placeholder_secret(secret_key: str) -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(app_env="production", secret_key=secret_key)


def test_production_accepts_generated_secret() -> None:
    settings = Settings(app_env="production", secret_key="a" * 64)
    assert settings.app_env == "production"


def test_demo_default_run_limit_is_ten() -> None:
    settings = Settings(secret_key="test-secret", _env_file=None)

    assert settings.max_runs_per_project == 10


@pytest.mark.parametrize("max_generations", [0, -1])
def test_max_coder_generations_must_be_at_least_one(max_generations: int) -> None:
    with pytest.raises(ValidationError, match="max_coder_generations"):
        Settings(
            app_env="development",
            secret_key="test-secret",
            max_coder_generations=max_generations,
        )


def test_max_coder_generations_is_configurable_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("MAX_CODER_GENERATIONS", "2")

    settings = Settings(app_env="development", secret_key="test-secret")

    assert settings.max_coder_generations == 2
