from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import FileType
from app.domain.models import validate_relative_path


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlannerStep(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    file_path: str = Field(min_length=1, max_length=300)
    file_type: FileType
    description: str = Field(min_length=1, max_length=1000)

    @field_validator("file_path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        return validate_relative_path(value)


class PlannerResponse(StrictModel):
    summary: str = Field(min_length=1, max_length=2000)
    steps: list[PlannerStep] = Field(min_length=1, max_length=8)
    suggested_project_title: str | None = Field(
        ..., description="A concise 2–6 word project name in the user's language, or null."
    )

    @model_validator(mode="before")
    @classmethod
    def default_missing_title(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            title = value.get("suggested_project_title")
            if title is not None and not isinstance(title, str):
                value["suggested_project_title"] = None
            else:
                value.setdefault("suggested_project_title", None)
        return value


class CoderResponse(StrictModel):
    file_path: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1)

    @field_validator("file_path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        return validate_relative_path(value)

    @field_validator("content", mode="before")
    @classmethod
    def strip_null_bytes(cls, value: object) -> object:
        if isinstance(value, str) and "\x00" in value:
            return value.replace("\x00", "")
        return value


class ReviewerIssue(StrictModel):
    message: str = Field(min_length=1, max_length=1000)
    file_path: str | None


class ReviewerResponse(StrictModel):
    verdict: Literal["approve", "reject", "escalate"]
    summary: str = Field(min_length=1, max_length=2000)
    issues: list[ReviewerIssue] = Field(max_length=20)


PLANNER_SCHEMA = PlannerResponse.model_json_schema()
CODER_SCHEMA = CoderResponse.model_json_schema()
REVIEWER_SCHEMA = ReviewerResponse.model_json_schema()
