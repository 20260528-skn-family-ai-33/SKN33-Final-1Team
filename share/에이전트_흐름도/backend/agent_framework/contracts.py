"""Versioned JSON contracts shared by agents, adapters, fixtures and API clients."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")]
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Verdict = Literal["pass", "fail", "review"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: Literal["1.0.0"] = "1.0.0"


class Context(Contract):
    project_id: Identifier
    run_id: Identifier
    actor_id: Identifier
    document_version_id: Identifier
    criteria_version_id: Identifier
    asset_version_id: Identifier | None = None
    parent_run_id: Identifier | None = None


class DocumentRef(Contract):
    file_id: Identifier
    document_version_id: Identifier
    sha256: Hash
    media_type: Literal["application/pdf"] = "application/pdf"
    page_count: int = Field(ge=1)


class AssetRef(Contract):
    file_id: Identifier
    asset_version_id: Identifier
    sha256: Hash
    media_type: Literal["image/png", "image/jpeg", "image/webp"] = "image/png"
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class SourceRef(Contract):
    document_version_id: Identifier
    page: int = Field(ge=1)
    block_id: Identifier
    quote: str = Field(min_length=1)
    source_hash: Hash


class Criterion(Contract):
    criterion_id: Identifier
    text: str = Field(min_length=1)
    kind: Literal["text", "number", "visual", "unsupported"]
    expected: str
    operator: Literal["equals", "contains", "gte", "lte", "present", "manual"]
    unit: str | None = None
    required: bool = True
    source: SourceRef
    human_added: bool = False


class RoleRequest(Contract):
    # Preserve provenance even when only a previous draft's criteria are passed on.
    synthetic: bool = False


class ExtractRequest(RoleRequest):
    context: Context
    document: DocumentRef


class CriteriaDraft(Contract):
    context: Context
    criteria: tuple[Criterion, ...] = Field(min_length=1)
    warnings: tuple[str, ...] = ()
    synthetic: bool = False


class RetrieveRequest(RoleRequest):
    context: Context
    criteria: tuple[Criterion, ...] = Field(min_length=1)
    purpose: Literal["inspection", "report"] = "inspection"


class Evidence(Contract):
    evidence_id: Identifier
    project_id: Identifier
    kind: Literal["current_requirement", "approved_exception", "historical_context"]
    criterion_ids: tuple[Identifier, ...] = Field(min_length=1)
    source_record_id: Identifier
    source: SourceRef
    approval_id: Identifier | None = None
    # Exception eligibility must be checked by the retrieval implementation.
    applicable_asset_version_id: Identifier | None = None

    @model_validator(mode="after")
    def exception_has_scope(self):
        if self.kind == "approved_exception" and (not self.approval_id or not self.applicable_asset_version_id):
            raise ValueError("approved_exception requires approval_id and asset scope")
        return self


class EvidenceBundle(Contract):
    context: Context
    retrieval_id: Identifier
    status: Literal["complete", "empty", "stale", "unavailable"]
    evidence: tuple[Evidence, ...] = ()
    warnings: tuple[str, ...] = ()
    postgres_revision: int = Field(default=0, ge=0)
    graph_revision: int | None = Field(default=None, ge=0)
    synthetic: bool = False


class InspectRequest(RoleRequest):
    context: Context
    criteria: tuple[Criterion, ...] = Field(min_length=1)
    asset: AssetRef
    evidence: EvidenceBundle


class ImageEvidence(Contract):
    asset_version_id: Identifier
    asset_hash: Hash
    # Normalized x1,y1,x2,y2; absence means no localization claim.
    bbox: tuple[float, float, float, float] | None = None

    @model_validator(mode="after")
    def valid_box(self):
        if self.bbox is not None:
            x1, y1, x2, y2 = self.bbox
            if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
                raise ValueError("bbox must be finite normalized x1,y1,x2,y2")
        return self


class Observation(Contract):
    criterion_id: Identifier
    observed: str
    verdict: Verdict
    image: ImageEvidence
    evidence_ids: tuple[Identifier, ...] = ()
    uncertainty: str | None = None


class InspectionResult(Contract):
    context: Context
    observations: tuple[Observation, ...]
    synthetic: bool = False


class ValidateRequest(RoleRequest):
    context: Context
    criteria: tuple[Criterion, ...] = Field(min_length=1)
    asset: AssetRef
    evidence: EvidenceBundle
    inspection: InspectionResult


class ValidatedItem(Contract):
    criterion_id: Identifier
    verdict: Verdict
    issues: tuple[str, ...] = ()


class ValidatedResult(Contract):
    context: Context
    verdict: Verdict
    items: tuple[ValidatedItem, ...] = Field(min_length=1)
    limitations: tuple[str, ...] = ()
    synthetic: bool = False


class ReportRequest(RoleRequest):
    context: Context
    validated: ValidatedResult
    evidence: EvidenceBundle


class ReportDraft(Contract):
    context: Context
    verdict: Verdict
    summary: str = Field(min_length=1)
    evidence_ids: tuple[Identifier, ...] = ()
    change_requests: tuple[str, ...] = ()
    synthetic: bool = False


class FrameworkError(Contract):
    code: str
    message: str
    retryable: bool = False
    outcome_unknown: bool = False


class Approval(Contract):
    context: Context
    actor_id: Identifier
    decision: Literal["approve_criteria", "approve_changes", "approve_final", "approve_exception"]
    reason: str = ""
    criterion_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def scoped_exception(self):
        if self.decision in ("approve_changes", "approve_exception") and not self.reason.strip():
            raise ValueError("a reason is required")
        if self.decision == "approve_exception" and not self.criterion_ids:
            raise ValueError("exception approval requires criterion_ids")
        return self


class RunSnapshot(Contract):
    context: Context
    profile: Literal["development", "test"]
    synthetic: bool
    execution_status: Literal["waiting_criteria", "waiting_review", "waiting_revision", "completed", "failed", "outcome_unknown"]
    machine_verdict: Verdict | None = None
    human_decision: Literal["unreviewed", "changes_approved", "final_approved", "exception_approved"] = "unreviewed"
    criteria: CriteriaDraft | None = None
    evidence: EvidenceBundle | None = None
    inspection: InspectionResult | None = None
    validated: ValidatedResult | None = None
    report: ReportDraft | None = None
    approvals: tuple[Approval, ...] = ()
    stages: tuple[str, ...] = ()
    error: FrameworkError | None = None


CONTRACT_TYPES = (Context, DocumentRef, AssetRef, Criterion, ExtractRequest, CriteriaDraft,
                  RetrieveRequest, EvidenceBundle, InspectRequest, InspectionResult,
                  ValidateRequest, ValidatedResult, ReportRequest, ReportDraft, Approval,
                  FrameworkError, RunSnapshot)
