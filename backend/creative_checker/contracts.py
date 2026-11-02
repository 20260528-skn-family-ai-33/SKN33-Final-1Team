"""The JSON request contracts consumed by the API, frontend, and worker."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Login(Input):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class PasswordChange(Input):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class CreateUser(Input):
    email: str = Field(min_length=3, max_length=320)
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=128)
    is_admin: bool = False
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class CreateProject(Input):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)


class SetMember(Input):
    user_id: UUID
    role: Literal["approver", "editor", "viewer"]


class ArchiveProject(Input):
    archived: bool


class CriterionSource(Input):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    kind: Literal["document", "user"]
    block_id: str | None = Field(default=None, max_length=100)
    page: int | None = Field(default=None, ge=1)
    quote: str = Field(default="", max_length=10000)


class Criterion(Input):
    id: UUID
    text: str = Field(min_length=1, max_length=10000)
    category: Literal["creative", "business", "other"]
    required: bool
    kind: Literal["exact_text", "date", "amount", "quantity", "logo", "layout", "color", "unsupported"]
    expected: Any = None
    target: Literal["all", "poster", "video"] = "all"
    asset_ids: list[UUID] = Field(default_factory=list, max_length=128)
    source: CriterionSource
    supported: bool = True
    notes: str = Field(default="", max_length=10000)


class CriterionGroup(Input):
    id: UUID
    operator: Literal["all", "any"]
    criterion_ids: list[UUID] = Field(min_length=1, max_length=128)
    condition: Any = None
    exception: Any = None


class CreateCriteria(Input):
    document_id: UUID
    items: list[Criterion] = Field(min_length=1, max_length=128)
    groups: list[CriterionGroup] = Field(default_factory=list, max_length=128)


class CreateRun(Input):
    asset_version_id: UUID
    criteria_version_id: UUID | None = None
    previous_run_id: UUID | None = None


class ChangeItem(Input):
    criterion_id: UUID
    message: str = Field(min_length=1, max_length=10000)


class CreateChangeRequest(Input):
    items: list[ChangeItem] = Field(min_length=1, max_length=128)
    message: str = Field(default="", max_length=10000)


class FinalApproval(Input):
    exception_reason: str | None = Field(default=None, max_length=10000)
    exception_criterion_ids: list[UUID] = Field(default_factory=list, max_length=128)
