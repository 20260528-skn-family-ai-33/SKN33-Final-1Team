"""Versioned JSON contracts shared by agents, adapters, fixtures and API clients."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator,model_serializer

Identifier = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")]
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Verdict = Literal["pass", "fail", "review"]
ObservationStatus = Literal["observed", "absent", "unreadable", "not_checked", "unknown"]
ExecutionStatus = Literal["queued", "extracting", "reviewing", "waiting_criteria", "waiting_asset", "waiting_review",
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
    media_type: Literal["application/pdf","text/plain"] = "application/pdf"
    page_count: int = Field(ge=1)

    @model_validator(mode='after')
    def logical_text_block(self):
        if self.media_type=='text/plain' and self.page_count!=1:raise ValueError('free input has one logical source block')
        return self


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


class CriteriaValueSource(Contract):
    record_id: Identifier
    purpose: Literal['human_review','synthetic_test']
    reference: str = Field(min_length=1,max_length=500)
    quote: str = Field(min_length=1,max_length=4000)
    quote_sha256: Hash
    submitted_by: Identifier
    submitted_at: AwareDatetime
    synthetic: bool

    @model_validator(mode='after')
    def keep_test_provenance(self):
        if self.purpose=='synthetic_test' and not self.synthetic:raise ValueError('test provenance cannot lose its synthetic label')
        return self


class RequirementPresentation(Contract):
    label: str = Field(min_length=1,max_length=80,pattern=r'^[^:\r\n]+$')
    value: str = Field(min_length=1,max_length=400)


class RequirementDetails(Contract):
    """Source-grounded compilation metadata, not semantic approval."""
    compiler: Literal['literal-requirements-v1','qwen-semantic-v1'] = 'literal-requirements-v1'
    target: Literal['poster_text','poster_visual','file_metadata','delivery','unspecified']
    field: str = Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')
    expected_source: SourceRef | None = None
    normalized_expected: str | None = Field(default=None,max_length=100)
    presentation: RequirementPresentation | None = None
    qualifiers: tuple[SourceRef,...] = ()
    origin: Literal['selected_source','model_structured','retained_unresolved_source'] = 'selected_source'

    @model_validator(mode='after')
    def field_matches_target(self):
        scopes={'poster_text':{'wording','title_phrase','event_name','year','event_date','start_date','end_date','event_period','weekday','venue','viewing_hours'},
            'poster_visual':{'visual_requirements','logo','visual_color','composition','imagery'},'file_metadata':{'pixel_width','pixel_height','file_format','color_mode'},
            'delivery':{'quantity'},'unspecified':{'unspecified'}}
        known={field:target for target,fields in scopes.items() for field in fields}
        if self.field in known and known[self.field]!=self.target:raise ValueError('requirement field does not match its target')
        return self

    @model_serializer(mode='wrap')
    def preserve_legacy_details(self,handler):
        data=handler(self)
        if self.normalized_expected is None:data.pop('normalized_expected',None)
        if self.presentation is None:data.pop('presentation',None)
        return data


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
    value_source: CriteriaValueSource | None = None
    details: RequirementDetails | None = None

    @model_serializer(mode='wrap')
    def preserve_legacy_value_shape(self,handler):
        data=handler(self)
        if self.value_source is None:data.pop('value_source',None)
        if self.details is None:data.pop('details',None)
        return data


class RoleRequest(Contract):
    # Preserve provenance even when only a previous draft's criteria are passed on.
    synthetic: bool = False


class ExtractRequest(RoleRequest):
    context: Context
    document: DocumentRef


class PDFTextSource(Contract):
    page: int = Field(ge=1,le=50,strict=True)
    block_id: Identifier
    text: str = Field(min_length=1,max_length=60000)
    text_sha256: Hash


class ExtractionRejection(Contract):
    candidate_index: int = Field(ge=1, le=128, strict=True)
    code: Identifier
    source: SourceRef | None = None


class ExtractionTrace(Contract):
    mode: Literal['mock','qwen']
    model: str = Field(min_length=1,max_length=200)
    parser: str = Field(min_length=1,max_length=100)
    policy: Literal['a02-text-v1','a02-source-compile-v2','a02-semantic-v3','a02-semantic-v4'] = 'a02-text-v1'
    input_kind: Literal['pdf','free_text'] = 'pdf'
    document_sha256: Hash
    prompt_sha256: Hash
    response_sha256: Hash
    sources: tuple[PDFTextSource,...] = Field(min_length=1,max_length=50)
    skipped_pdf_pages: tuple[int,...] = Field(default=(), max_length=49)
    unresolved_criterion_ids: tuple[Identifier,...] = ()
    rejected_candidates: tuple[ExtractionRejection, ...] = Field(default=(), max_length=128)

    @model_serializer(mode='wrap')
    def preserve_legacy_trace(self,handler):
        data=handler(self)
        if self.input_kind=='pdf':data.pop('input_kind',None)
        if not self.rejected_candidates:data.pop('rejected_candidates',None)
        if not self.skipped_pdf_pages:data.pop('skipped_pdf_pages',None)
        return data


class CriteriaDraft(Contract):
    context: Context
    criteria: tuple[Criterion, ...] = Field(min_length=1)
    warnings: tuple[str, ...] = ()
    synthetic: bool = False
    extraction: ExtractionTrace | None = None

    @model_serializer(mode='wrap')
    def preserve_legacy_wire_shape(self,handler):
        data=handler(self)
        if self.extraction is None:data.pop('extraction',None)
        return data


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
    value_source: CriteriaValueSource | None = None

    @model_serializer(mode='wrap')
    def preserve_legacy_value_shape(self,handler):
        data=handler(self)
        if self.value_source is None:data.pop('value_source',None)
        return data

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
    polish_status: Literal['failed','outcome_unknown'] | None = None

    @model_serializer(mode='wrap')
    def preserve_legacy_polish_shape(self,handler):
        data=handler(self)
        if self.polish_status is None:data.pop('polish_status',None)
        return data


class FrameworkError(Contract):
    code: str
    message: str
    retryable: bool = False
    outcome_unknown: bool = False


class HumanReview(Contract):
    criterion_id: Identifier
    verdict: Literal['pass','fail']
    observed: str = Field(strict=True,min_length=1,max_length=4000)
    reference: str = Field(strict=True,min_length=1,max_length=1000)
    reason: str = Field(strict=True,min_length=1,max_length=4000)
    checked_asset: bool = Field(strict=True)

    @model_validator(mode='after')
    def verified_review(self):
        if not self.checked_asset or not all(v.strip() for v in (self.observed,self.reference,self.reason)):
            raise ValueError('direct asset inspection and nonblank evidence are required')
        return self


class Approval(Contract):
    context: Context
    actor_id: Identifier
    decision: Literal["approve_criteria", "approve_changes", "approve_final", "approve_exception"]
    reason: str = ""
    criterion_ids: tuple[Identifier, ...] = ()
    approved_criteria_version_id: Identifier | None = None
    criteria_sha256: Hash | None = None
    human_reviews: tuple[HumanReview,...] = Field(default=(),max_length=128)

    @model_serializer(mode='wrap')
    def preserve_legacy_target_shape(self,handler):
        data=handler(self)
        if self.approved_criteria_version_id is None:
            data.pop('approved_criteria_version_id',None);data.pop('criteria_sha256',None)
        if not self.human_reviews:data.pop('human_reviews',None)
        return data

    @model_validator(mode="after")
    def scoped_exception(self):
        if self.human_reviews:
            if self.decision not in ('approve_final','approve_changes') or not self.reason.strip():
                raise ValueError('human reviews require a final/change approval and reason')
            if len({r.criterion_id for r in self.human_reviews})!=len(self.human_reviews):
                raise ValueError('duplicate human review')
        if (self.approved_criteria_version_id is None)!=(self.criteria_sha256 is None):
            raise ValueError('criteria approval target requires both version and hash')
        if self.approved_criteria_version_id is not None and self.decision!='approve_criteria':
            raise ValueError('criteria target is only for criteria approval')
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


class ApprovedBaseline(Contract):
    source_run_id: Identifier
    criteria_version_id: Identifier
    criteria_sha256: Hash
    approval_id: Identifier
    document_sha256: Hash


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
    approved_criteria_version_id: Identifier | None = None
    baseline: ApprovedBaseline | None = None

    @model_serializer(mode='wrap')
    def preserve_legacy_approval_shape(self,handler):
        data=handler(self)
        if self.approved_criteria_version_id is None:data.pop('approved_criteria_version_id',None)
        if self.baseline is None:data.pop('baseline',None)
        return data


CONTRACT_TYPES = (Context, DocumentRef, AssetRef, Criterion,CriteriaValueSource, ExtractRequest, CriteriaDraft,PDFTextSource,ExtractionTrace,
                  EvidenceBundle, InspectRequest, InspectionResult,
                  ValidateRequest, ValidatedResult, ReportRequest, ReportDraft, HumanReview, Approval,
                  FrameworkError, StepRecord, StepTimeline, RunSnapshot,ApprovedBaseline)
