"""Versioned JSON contracts shared by agents, adapters, fixtures and API clients."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")]
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Verdict = Literal["pass", "fail", "review"]
ObservationStatus = Literal["observed", "absent", "unreadable", "not_checked", "unknown"]
ExecutionStatus = Literal["queued", "extracting", "reviewing", "waiting_criteria", "waiting_review",
                          "waiting_revision", "completed", "failed", "outcome_unknown"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: Literal["3.0.0"] = "3.0.0"


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
    expected: str = Field(description=(
        "Text equals/contains comparisons need a non-whitespace expected value. "
        "Blank expectations remain unresolved (review) and cannot pass."
    ))
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


class Evidence(Contract):
    evidence_id: Identifier
    project_id: Identifier
    kind: Literal["current_requirement", "approved_exception", "historical_context"]
    criterion_ids: tuple[Identifier, ...] = Field(min_length=1)
    source_record_id: Identifier
    source: SourceRef
    approval_id: Identifier | None = None
    # Exception eligibility must be checked by the common context preparation service.
    applicable_asset_version_id: Identifier | None = None

    @model_validator(mode="after")
    def exception_has_scope(self):
        if self.kind == "approved_exception" and (not self.approval_id or not self.applicable_asset_version_id):
            raise ValueError("approved_exception requires approval_id and asset scope")
        return self


class EvidenceBundle(Contract):
    context: Context
    context_id: Identifier
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
    observation_status: ObservationStatus = "unknown"
    observed_unit: str | None = None
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
    criterion_text: str
    kind: Literal["text", "number", "visual", "unsupported"]
    operator: Literal["equals", "contains", "gte", "lte", "present", "manual"]
    required: bool
    expected: str
    expected_unit: str | None = None
    observed: str | None = None
    observed_unit: str | None = None
    observation_status: ObservationStatus = "unknown"
    comparison_expected: str | None = None
    comparison_observed: str | None = None
    comparison_unit: str | None = None
    image: ImageEvidence | None = None
    evidence_ids: tuple[Identifier, ...] = ()
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


class StepRecord(Contract):
    """One role boundary, including validation; never a raw provider payload."""
    ordinal: int = Field(ge=1, strict=True)
    role: Literal["A02", "A04_A05", "A06", "A07"]
    status: Literal["running", "completed", "failed", "outcome_unknown", "reused"]
    recorded_at: AwareDatetime
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    duration_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    error: FrameworkError | None = None
    synthetic: bool = False
    source_run_id: Identifier | None = None

    @model_validator(mode="after")
    def consistent_lifecycle(self):
        if self.status == "reused":
            if (self.role != "A02" or self.source_run_id is None or self.started_at is not None
                    or self.finished_at is not None or self.duration_ms is not None or self.error is not None):
                raise ValueError("reused criteria must name their source without claiming an invocation")
            return self
        if self.source_run_id is not None or self.started_at is None:
            raise ValueError("an invoked step requires a start time and no reuse source")
        if self.status == "running":
            if self.finished_at is not None or self.duration_ms is not None or self.error is not None:
                raise ValueError("a running step cannot have a terminal outcome")
        else:
            if (self.finished_at is None or self.finished_at < self.started_at
                    or self.duration_ms is None and self.status != "outcome_unknown"):
                raise ValueError("a finished step requires ordered timestamps and a duration")
            failed = self.status in {"failed", "outcome_unknown"}
            if failed != (self.error is not None):
                raise ValueError("only failed steps carry an error")
            if self.error and self.error.outcome_unknown != (self.status == "outcome_unknown"):
                raise ValueError("error ambiguity must match step status")
        return self


class StepTimeline(Contract):
    context: Context
    profile: Literal["development", "test"]
    storage: Literal["memory", "postgres"] = "memory"
    execution_status: ExecutionStatus
    records: tuple[StepRecord, ...] = ()


class RunSnapshot(Contract):
    context: Context
    profile: Literal["development", "test"]
    synthetic: bool
    execution_status: ExecutionStatus
    machine_verdict: Verdict | None = None
    human_decision: Literal["unreviewed", "changes_approved", "final_approved", "exception_approved"] = "unreviewed"
    criteria: CriteriaDraft | None = None
    evidence: EvidenceBundle | None = None
    inspection: InspectionResult | None = None
    validated: ValidatedResult | None = None
    report: ReportDraft | None = None
    approvals: tuple[Approval, ...] = ()
    stages: tuple[str, ...] = ()
    step_records: tuple[StepRecord, ...] = ()
    error: FrameworkError | None = None


CONTRACT_TYPES = (Context, DocumentRef, AssetRef, Criterion, ExtractRequest, CriteriaDraft,
                  EvidenceBundle, InspectRequest, InspectionResult,
                  ValidateRequest, ValidatedResult, ReportRequest, ReportDraft, Approval,
                  FrameworkError, StepRecord, StepTimeline, RunSnapshot)
