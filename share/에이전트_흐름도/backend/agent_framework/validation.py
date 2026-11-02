"""A06 reference policy and boundary checks; these do not establish model accuracy."""
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from .contracts import (Context, CriteriaDraft, DocumentRef, EvidenceBundle,
                        InspectRequest, InspectionResult, ReportDraft, ReportRequest,
                        RetrieveRequest, ValidateRequest, ValidatedItem, ValidatedResult)


def ensure_context(expected: Context, result) -> None:
    if result.context != expected:
        raise ValueError("CONTEXT_MISMATCH: project/run/version snapshot changed")


def _unique(values, label):
    values = tuple(values)
    if len(set(values)) != len(values):
        raise ValueError(f"DUPLICATE_{label}")


def _criteria(context, criteria):
    _unique((c.criterion_id for c in criteria), "CRITERION")
    for criterion in criteria:
        source = criterion.source
        if source.document_version_id != context.document_version_id:
            raise ValueError("CRITERION_DOCUMENT_MISMATCH")
        if sha256(source.quote.encode("utf-8")).hexdigest() != source.source_hash:
            raise ValueError("SOURCE_HASH_MISMATCH")


def validate_draft(context: Context, document: DocumentRef, draft: CriteriaDraft) -> None:
    ensure_context(context, draft)
    if document.document_version_id != context.document_version_id:
        raise ValueError("DOCUMENT_VERSION_MISMATCH")
    _criteria(context, draft.criteria)
    if any(c.source.page > document.page_count for c in draft.criteria):
        raise ValueError("SOURCE_PAGE_OUT_OF_RANGE")


def validate_evidence(request: RetrieveRequest, bundle: EvidenceBundle) -> None:
    ensure_context(request.context, bundle)
    _criteria(request.context, request.criteria)
    criteria = {c.criterion_id: c for c in request.criteria}
    _unique((e.evidence_id for e in bundle.evidence), "EVIDENCE")
    if bundle.status == "empty" and bundle.evidence:
        raise ValueError("EMPTY_RETRIEVAL_HAS_EVIDENCE")
    if (bundle.status == "complete" and bundle.graph_revision is not None
            and bundle.graph_revision < bundle.postgres_revision):
        raise ValueError("STALE_GRAPH_MARKED_COMPLETE")
    for evidence in bundle.evidence:
        if evidence.project_id != request.context.project_id:
            raise ValueError("EVIDENCE_PROJECT_MISMATCH")
        if not set(evidence.criterion_ids) <= criteria.keys():
            raise ValueError("UNKNOWN_EVIDENCE_CRITERION")
        if sha256(evidence.source.quote.encode("utf-8")).hexdigest() != evidence.source.source_hash:
            raise ValueError("EVIDENCE_HASH_MISMATCH")
        if evidence.kind == "current_requirement":
            if any(evidence.source != criteria[cid].source for cid in evidence.criterion_ids):
                raise ValueError("EVIDENCE_SOURCE_MISMATCH")
        if evidence.kind == "approved_exception":
            if (evidence.source.document_version_id != request.context.document_version_id
                    or evidence.applicable_asset_version_id != request.context.asset_version_id):
                raise ValueError("EXCEPTION_SCOPE_MISMATCH")


def validate_inspection(request: InspectRequest, result: InspectionResult) -> None:
    ensure_context(request.context, result)
    validate_evidence(RetrieveRequest(context=request.context, criteria=request.criteria), request.evidence)
    if request.asset.asset_version_id != request.context.asset_version_id:
        raise ValueError("ASSET_VERSION_MISMATCH")
    ids = {c.criterion_id for c in request.criteria}
    evidence = {e.evidence_id: e for e in request.evidence.evidence}
    _unique((o.criterion_id for o in result.observations), "OBSERVATION")
    for observation in result.observations:
        if observation.criterion_id not in ids:
            raise ValueError("UNKNOWN_OBSERVATION_CRITERION")
        if (observation.image.asset_version_id != request.asset.asset_version_id
                or observation.image.asset_hash != request.asset.sha256):
            raise ValueError("OBSERVATION_ASSET_MISMATCH")
        _unique(observation.evidence_ids, "CITATION")
        for eid in observation.evidence_ids:
            if eid not in evidence:
                raise ValueError("UNKNOWN_CITATION")
            source = evidence[eid]
            if (source.kind == "historical_context"
                    or observation.criterion_id not in source.criterion_ids):
                raise ValueError("INVALID_OBSERVATION_CITATION")


def aggregate(criteria, items):
    required_ids = {c.criterion_id for c in criteria if c.required}
    verdicts = [i.verdict for i in items if i.criterion_id in required_ids]
    if "fail" in verdicts:
        return "fail"
    if "review" in verdicts or not verdicts:
        return "review"
    return "pass"


def validate_validated(request: ValidateRequest, result: ValidatedResult) -> None:
    ensure_context(request.context, result)
    _unique((i.criterion_id for i in result.items), "VALIDATED_ITEM")
    if {i.criterion_id for i in result.items} != {c.criterion_id for c in request.criteria}:
        raise ValueError("VALIDATION_MUST_COVER_ALL_CRITERIA")
    if aggregate(request.criteria, result.items) != result.verdict:
        raise ValueError("AGGREGATE_VERDICT_MISMATCH")
    # Independent of a team's custom A06 implementation, basic missing-data rules hold.
    safe = ReferenceValidator().validate(request)
    safe_items = {i.criterion_id: i for i in safe.items}
    for item in result.items:
        expected = safe_items[item.criterion_id]
        if expected.verdict != "pass" and item.verdict == "pass":
            raise ValueError("VALIDATOR_UNSUPPORTED_PASS")
        if expected.verdict == "fail" and item.verdict != "fail":
            raise ValueError("VALIDATOR_HID_CONFIRMED_FAILURE")
        if not set(expected.issues) <= set(item.issues):
            raise ValueError("VALIDATOR_DROPPED_LIMITATIONS")


def validate_report(request: ReportRequest, result: ReportDraft) -> None:
    ensure_context(request.context, request.validated)
    ensure_context(request.context, request.evidence)
    ensure_context(request.context, result)
    if result.verdict != request.validated.verdict:
        raise ValueError("REPORT_CHANGED_VERDICT")
    _unique(result.evidence_ids, "REPORT_CITATION")
    allowed = {e.evidence_id for e in request.evidence.evidence}
    if not set(result.evidence_ids) <= allowed:
        raise ValueError("UNKNOWN_REPORT_CITATION")
    if request.evidence.evidence and not result.evidence_ids:
        raise ValueError("REPORT_CITATION_REQUIRED")


class ReferenceValidator:
    """Conservative example A06. Visual observations remain a model's claims.

    No automatic exception application, semantic entailment or group policies.
    Those extensions must retain these coverage/citation/version boundaries.
    """

    def validate(self, request: ValidateRequest) -> ValidatedResult:
        validate_inspection(InspectRequest(context=request.context, criteria=request.criteria,
                                          asset=request.asset, evidence=request.evidence), request.inspection)
        observations = {o.criterion_id: o for o in request.inspection.observations}
        evidence = {e.evidence_id: e for e in request.evidence.evidence}
        items = []
        for criterion in request.criteria:
            issues = []
            observation = observations.get(criterion.criterion_id)
            verdict = observation.verdict if observation else "review"
            if not observation:
                issues.append("missing_observation")
            else:
                if observation.uncertainty:
                    issues.append("uncertain_observation")
                    verdict = "review"
                if not observation.observed.strip():
                    issues.append("missing_observation")
                    verdict = "review"
                if not any(evidence[eid].kind == "current_requirement" for eid in observation.evidence_ids):
                    issues.append("missing_citation")
                supported = {"text": {"equals", "contains"}, "number": {"equals", "gte", "lte"},
                             "visual": {"present"}}
                if criterion.operator not in supported.get(criterion.kind, set()):
                    issues.append("unsupported_criterion")
                    verdict = "review"
                elif criterion.kind == "number" and not observation.uncertainty and observation.observed.strip():
                    try:
                        observed, expected = Decimal(observation.observed), Decimal(criterion.expected)
                        if not observed.is_finite() or not expected.is_finite():
                            raise InvalidOperation
                        comparisons = {"equals": observed == expected, "gte": observed >= expected,
                                       "lte": observed <= expected}
                        if criterion.operator not in comparisons:
                            issues.append("unsupported_criterion")
                            verdict = "review"
                        elif not comparisons[criterion.operator]:
                            verdict = "fail"
                            issues.append("value_mismatch")
                        elif observation.verdict == "fail":
                            verdict = "review"
                            issues.append("verdict_conflict")
                    except InvalidOperation:
                        issues.append("numeric_observation_invalid")
                        verdict = "review"
                elif criterion.kind == "text" and not observation.uncertainty and observation.observed.strip():
                    matched = (criterion.expected == observation.observed if criterion.operator == "equals"
                               else criterion.expected in observation.observed)
                    if not matched:
                        verdict = "fail"
                        issues.append("value_mismatch")
                    elif observation.verdict == "fail":
                        verdict = "review"
                        issues.append("verdict_conflict")
            if not any(e.kind == "current_requirement" and criterion.criterion_id in e.criterion_ids
                       for e in evidence.values()):
                issues.append("missing_requirement_evidence")
            if request.evidence.status != "complete":
                issues.append("retrieval_incomplete")
            if issues and verdict == "pass":
                verdict = "review"
            items.append(ValidatedItem(criterion_id=criterion.criterion_id, verdict=verdict, issues=tuple(issues)))
        return ValidatedResult(context=request.context, verdict=aggregate(request.criteria, items),
                               items=tuple(items),
                               limitations=("Reference policy only; visual accuracy and source authority require real adapters.",),
                               synthetic=request.synthetic or request.inspection.synthetic or request.evidence.synthetic)
