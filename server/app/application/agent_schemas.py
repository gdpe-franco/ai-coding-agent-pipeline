from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class CoderResponse(StrictModel):
    file_path: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1)

    @field_validator("file_path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        return validate_relative_path(value)


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
