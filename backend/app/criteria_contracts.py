"""Shared legacy poster and video request contract without starting an API."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.documents.conditions import DocumentCondition, matches_condition_text

CRITERIA_MAX_JSON = 250_000


class Criterion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=500)
    type: Literal["information", "visual"]
    source: str = Field(default="user", min_length=1, max_length=500)
    document_condition: DocumentCondition | None = None

    @model_validator(mode='after')
    def consistent_document_condition(self):
        if self.document_condition and not matches_condition_text(self.document_condition.model_dump(), self.text):
            raise ValueError('PDF 조건 기대값과 표시 문구가 다릅니다. 편집한 항목의 이전 문서 구조를 제거하세요.')
        return self
