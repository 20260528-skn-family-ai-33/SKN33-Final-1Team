"""Human review commands are separate from untrusted model responses."""
from typing import Annotated,Literal
from uuid import UUID
from pydantic import AwareDatetime,Field,model_validator

from .contracts import (Approval,Context,Contract,Criterion,CriteriaValueSource,EvidenceBundle,Hash,Identifier)

Purpose=Literal['human_review','synthetic_test']
Text=Annotated[str,Field(strict=True,min_length=1,max_length=4000)]


class SupplementInput(Contract):
    reference: str = Field(strict=True,min_length=1,max_length=500)
    quote: Text

    @model_validator(mode='after')
    def not_blank(self):
        if not self.reference.strip() or not self.quote.strip():raise ValueError('source must be nonblank')
        return self


class CriterionResolution(Contract):
    kind: Literal['text','number','visual','unsupported']
    operator: Literal['equals','contains','gte','lte','present','manual']
    expected: str = Field(strict=True,max_length=4000)
    unit: str | None = Field(default=None,strict=True,max_length=80)
    required: bool = Field(strict=True)


class CriteriaEdit(Contract):
    criterion_id: Identifier
    action: Literal['amend','exclude']
    reason: Text
    source: SupplementInput
    resolution: CriterionResolution | None = None

    @model_validator(mode='after')
    def consistent_edit(self):
        if not self.reason.strip():raise ValueError('reason must be nonblank')
        if (self.action=='amend')!=(self.resolution is not None):raise ValueError('only amendments require a resolution')
        if self.resolution is not None:
            r=self.resolution
            if r.expected and r.expected not in self.source.quote:raise ValueError('expected must occur in supplement')
            if r.unit is not None and (not r.unit.strip() or r.unit not in self.source.quote):raise ValueError('unit must occur in supplement')
        return self


class CriteriaVersionRequest(Contract):
    parent_version_id: UUID | None
    expected_base_sha256: Hash
    purpose: Purpose
    edits: tuple[CriteriaEdit,...] = Field(default=(),max_length=128)

    @model_validator(mode='after')
    def distinct_edits(self):
        if len({edit.criterion_id for edit in self.edits})!=len(self.edits):raise ValueError('duplicate edit')
        return self


class RecordedCriteriaEdit(Contract):
    criterion_id: Identifier
    action: Literal['amend','exclude']
    reason: Text
    source: CriteriaValueSource


class CriteriaVersion(Contract):
    criteria_version_id: UUID
    source_run_id: Identifier
    context: Context
    parent_version_id: UUID | None
    version_no: int = Field(ge=1,strict=True)
    source_draft_sha256: Hash
    document_sha256: Hash
    criteria: tuple[Criterion,...] = Field(min_length=1,max_length=128)
    exclusions: tuple[RecordedCriteriaEdit,...] = Field(default=(),max_length=128)
    changes: tuple[RecordedCriteriaEdit,...] = Field(default=(),max_length=128)
    created_by: Identifier
    created_at: AwareDatetime
    purpose: Purpose
    synthetic: bool


class CriteriaVersionRecord(Contract):
    version: CriteriaVersion
    sha256: Hash
    approval_blockers: dict[str,tuple[str,...]]


class CriteriaApprovalRequest(Contract):
    criteria_version_id: UUID
    expected_sha256: Hash
    purpose: Purpose
    reason: Text
    reviewed_all_criteria: bool = Field(strict=True)

    @model_validator(mode='after')
    def explicit_review(self):
        if not self.reviewed_all_criteria or not self.reason.strip():raise ValueError('explicit review and reason are required')
        return self


class CriteriaApprovalRecord(Contract):
    approval_id: UUID
    approval: Approval
    approved_at: AwareDatetime
    criteria: CriteriaVersionRecord
    evidence: EvidenceBundle
    purpose: Purpose
    synthetic: bool
