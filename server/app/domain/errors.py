from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base class for expected domain failures."""


class ValidationError(DomainError):
    pass


class PlanGuardError(DomainError):
    pass


class RunConflictError(DomainError):
    pass


class InvalidTransitionError(DomainError):
    pass


class ModelError(DomainError):
    def __init__(
        self,
        message: str,
        tag: str = "model_error",
        telemetry: dict[str, Any] | None = None,
    ) -> None:
        self.tag = tag
        self.telemetry = telemetry
        super().__init__(message)


class MissingCredentialsError(DomainError):
    def __init__(self, setting: str) -> None:
        self.setting = setting
        super().__init__(f"missing required setting: {setting}")


class WorkspaceError(DomainError):
    pass
