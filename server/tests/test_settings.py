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
