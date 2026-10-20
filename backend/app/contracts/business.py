"""Strict C09 HTTP input contracts. Missing source metadata remains null."""
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)


class ProjectCreate(Strict):
    name: str = Field(min_length=1, max_length=120)

    @field_validator('name')
    @classmethod
    def nonblank(cls, value):
        if not value.strip(): raise ValueError('Blank name')
        return value


class ProjectPatch(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = None

    @model_validator(mode='after')
    def check(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError('Supply a change')
        if self.name is not None and not self.name.strip(): raise ValueError('Blank name')
        return self


class SetCreate(ProjectCreate):
    name: str = Field(min_length=1, max_length=200)


class Item(Strict):
    id: str = Field(min_length=1, max_length=100)
    text: str = Field(max_length=500)
    type: Literal['information', 'visual']
    source: str = Field(min_length=1, max_length=500)
    selected: bool

    @field_validator('id', 'source')
    @classmethod
    def nonblank(cls, value):
        if not value.strip(): raise ValueError('Blank item identifier/source')
        return value


class Locator(Strict):
    page_no: Annotated[int, Field(ge=1)] | None
    field_pointer: Annotated[str, Field(max_length=255)] | None


class Source(Strict):
    asset_version_id: str
    locator: Locator | None

    @field_validator('asset_version_id')
    @classmethod
    def uuid(cls, value): return str(UUID(value))


class Content(Strict):
    raw_requirements: str = Field(min_length=1, max_length=20000)
    items: list[Item] = Field(max_length=100)
    source_documents: list[Source] | None = Field(default=None, max_length=20)

    @model_validator(mode='after')
    def check(self):
        if not self.raw_requirements.strip(): raise ValueError('Blank requirements')
        if len({x.id for x in self.items}) != len(self.items): raise ValueError('Duplicate item id')
        return self


class DraftCreate(Content):
    criteria_set_id: str
    image_id: str

    @field_validator('criteria_set_id', 'image_id')
    @classmethod
    def uuid(cls, value): return str(UUID(value))


class Revision(Strict):
    expected_revision: str = Field(pattern=r'^[1-9][0-9]{0,18}$')

    @field_validator('expected_revision')
    @classmethod
    def bounded(cls, value):
        if int(value) > 9223372036854775807: raise ValueError('Revision too large')
        return value


class DraftPut(Content, Revision):
    source_documents: list[Source] | None = Field(max_length=20)


class Confirm(Revision):
    draft_id: str

    @field_validator('draft_id')
    @classmethod
    def uuid(cls, value): return str(UUID(value))


def draft_payload(row):
    return dict(project_id=str(row['project_id']), criteria_set_id=str(row['criteria_set_id']),
                image_id=str(row['target_asset_version_id']), raw_requirements=row['raw_requirements'],
                source_documents=row['source_documents_jsonb'], items=row['items_jsonb'])


def selected_items(payload):
    selected = [item for item in payload['items'] if item['selected']]
    if not selected or any(not item['text'].strip() for item in selected):
        from ..security.errors import AuthError
        raise AuthError('INVALID_CRITERIA_SELECTION', 422)
    return [dict(external_item_id=item['id'], position=n, category=item['type'], text=item['text'],
                 source_text=item['source'], rule_code=None, field_code=None, expected_value=None,
                 is_required=None, source_asset_version_id=None, source_locator=None,
                 applicability=None, check_scope=None, source_item=item.copy())
            for n, item in enumerate(selected, 1)]
