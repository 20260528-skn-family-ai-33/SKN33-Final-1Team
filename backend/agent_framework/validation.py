"""A06 reference policy and boundary checks; these do not establish model accuracy."""
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re

from .contracts import (Context, CriteriaDraft, DocumentRef, EvidenceBundle,
                        InspectRequest, InspectionResult, ReportDraft, ReportRequest,
                        ValidateRequest, ValidatedItem, ValidatedResult)
from .qualifier_rules import format_free, may_reject, needs_person, notation_only, qualifier_text
from .typed_values import DATE_ROLES, MATCH, MISMATCH, UNKNOWN, compare_typed, file_check, typed_kind


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
        direct_input = source.kind == 'direct_input'
        if direct_input:
            if (source.document_version_id == context.document_version_id
                    or not criterion.human_added or criterion.value_source is None):
                raise ValueError('DIRECT_INPUT_SOURCE_INVALID')
        elif source.document_version_id != context.document_version_id:
            raise ValueError("CRITERION_DOCUMENT_MISMATCH")
        if sha256(source.quote.encode("utf-8")).hexdigest() != source.source_hash:
            raise ValueError("SOURCE_HASH_MISMATCH")
        details = criterion.details
        if details is not None:
            for reference in details.qualifiers+((details.expected_source,) if details.expected_source is not None else ()):
                if (reference.document_version_id!=source.document_version_id or reference.kind!=source.kind or reference.page!=source.page
                        or reference.block_id!=source.block_id or reference.quote not in source.quote
                        or not reference.quote.strip() or sha256(reference.quote.encode()).hexdigest()!=reference.source_hash):
                    raise ValueError('DETAILS_SOURCE_MISMATCH')
            if (details.expected_source is not None and details.expected_source.quote!=criterion.expected
                    and details.normalized_expected!=criterion.expected):
                raise ValueError('DETAILS_EXPECTED_MISMATCH')
            if criterion.expected and not criterion.human_added and details.expected_source is None:
                raise ValueError('DETAILS_EXPECTED_SOURCE_MISSING')
            if details.normalized_expected is not None:
                from .a02_semantic import verify_normalized_date
                from .ports import AdapterFailure
                try:verify_normalized_date(details.expected_source.quote if details.expected_source is not None else criterion.expected,
                    details.normalized_expected,details.field,source.quote)
                except AdapterFailure:raise ValueError('DETAILS_NORMALIZED_VALUE_INVALID') from None
        value=criterion.value_source
        if value is not None:
            if not criterion.human_added or not value.reference.strip() or not value.quote.strip():
                raise ValueError('VALUE_SOURCE_INVALID')
            if sha256(value.quote.encode()).hexdigest()!=value.quote_sha256:
                raise ValueError('VALUE_SOURCE_HASH_MISMATCH')
            grounded_value = (details.expected_source.quote if details is not None
                and details.expected_source is not None and details.normalized_expected == criterion.expected
                and direct_input else criterion.expected)
            if grounded_value and grounded_value not in value.quote:
                raise ValueError('VALUE_SOURCE_EXPECTED_UNGROUNDED')
            if criterion.unit is not None and (not criterion.unit.strip() or criterion.unit not in value.quote):
                raise ValueError('VALUE_SOURCE_UNIT_UNGROUNDED')


def validate_draft(context: Context, document: DocumentRef, draft: CriteriaDraft, *, approved_version=False) -> None:
    ensure_context(context, draft)
    if document.document_version_id != context.document_version_id:
        raise ValueError("DOCUMENT_VERSION_MISMATCH")
    _criteria(context, draft.criteria)
    if any(c.source.kind is not None for c in draft.criteria) and (not approved_version or draft.extraction is not None):
        raise ValueError('DRAFT_SOURCE_KIND_INVALID')
    if any(c.source.page > document.page_count for c in draft.criteria if c.source.kind is None):
        raise ValueError("SOURCE_PAGE_OUT_OF_RANGE")
    if draft.extraction is not None:
        from .a02 import validate_trace
        validate_trace(document,draft)


def validate_evidence_envelope(context: Context, bundle: EvidenceBundle) -> None:
    """Validate self-contained provenance without claiming source authenticity.

    Historical context may cite an older document in the same project. Current
    requirements and approved exceptions must reference this document, and an
    exception must also apply to this asset version. This cannot prove actual
    permission, approval validity, database freshness or source-file contents.
    """
    ensure_context(context, bundle)
    _unique((e.evidence_id for e in bundle.evidence), "EVIDENCE")
    if bundle.status == "empty" and bundle.evidence:
        raise ValueError("EMPTY_CONTEXT_HAS_EVIDENCE")
    if (bundle.status == "complete" and bundle.graph_revision is not None
            and bundle.graph_revision < bundle.postgres_revision):
        raise ValueError("STALE_GRAPH_MARKED_COMPLETE")
    for evidence in bundle.evidence:
        if evidence.project_id != context.project_id:
            raise ValueError("EVIDENCE_PROJECT_MISMATCH")
        if sha256(evidence.source.quote.encode("utf-8")).hexdigest() != evidence.source.source_hash:
            raise ValueError("EVIDENCE_HASH_MISMATCH")
        if evidence.value_source is not None:
            value=evidence.value_source
            if (not value.quote.strip() or not value.reference.strip()
                    or sha256(value.quote.encode()).hexdigest()!=value.quote_sha256):
                raise ValueError('EVIDENCE_VALUE_SOURCE_INVALID')
        if evidence.kind == 'current_requirement':
            if evidence.source.kind == 'direct_input':
                if evidence.source.document_version_id == context.document_version_id or evidence.value_source is None:
                    raise ValueError('EVIDENCE_SOURCE_MISMATCH')
            elif evidence.source.document_version_id != context.document_version_id:
                raise ValueError('EVIDENCE_SOURCE_MISMATCH')
        if evidence.kind == "approved_exception":
            if (evidence.source.document_version_id != context.document_version_id
                    or evidence.applicable_asset_version_id != context.asset_version_id):
                raise ValueError("EXCEPTION_SCOPE_MISMATCH")


def validate_evidence(context, criteria_input, bundle: EvidenceBundle) -> None:
    ensure_context(context, bundle)
    _criteria(context, criteria_input)
    validate_evidence_envelope(context, bundle)
    criteria = {c.criterion_id: c for c in criteria_input}
    for evidence in bundle.evidence:
        if not set(evidence.criterion_ids) <= criteria.keys():
            raise ValueError("UNKNOWN_EVIDENCE_CRITERION")
        if evidence.kind == "current_requirement":
            if any(evidence.source != criteria[cid].source for cid in evidence.criterion_ids):
                raise ValueError("EVIDENCE_SOURCE_MISMATCH")
            if any(evidence.value_source!=criteria[cid].value_source for cid in evidence.criterion_ids):
                raise ValueError('EVIDENCE_VALUE_SOURCE_MISMATCH')


def validate_inspection(request: InspectRequest, result: InspectionResult) -> None:
    ensure_context(request.context, result)
    validate_evidence(request.context, request.criteria, request.evidence)
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
        if "missing_expected_value" in expected.issues and item.verdict != "review":
            raise ValueError("VALIDATOR_UNRESOLVED_EXPECTATION")
        if expected.verdict == "review" and item.verdict != "review":
            raise ValueError("VALIDATOR_UNRESOLVED_OBSERVATION")
        if expected.verdict == "pass" and item.verdict == "fail":
            raise ValueError("VALIDATOR_UNSUPPORTED_FAILURE")
        if expected.verdict == "fail" and item.verdict != "fail":
            raise ValueError("VALIDATOR_HID_CONFIRMED_FAILURE")
        if not set(expected.issues) <= set(item.issues):
            raise ValueError("VALIDATOR_DROPPED_LIMITATIONS")
        if item.verdict == "pass" and item.issues:
            raise ValueError("VALIDATOR_UNSUPPORTED_PASS")
        if item.model_dump(exclude={"verdict", "issues"}) != expected.model_dump(exclude={"verdict", "issues"}):
            raise ValueError("VALIDATOR_CHANGED_EVIDENCE")


def validate_report_request(request: ReportRequest) -> None:
    """Check supplied A07 provenance before a reporter can consume it.

    Item details allow aggregation and citation checks, but cannot authenticate
    the approved criteria set or image contents. The A06 boundary must compare
    those details with the original request before constructing ReportRequest.
    """
    ensure_context(request.context, request.validated)
    validate_evidence_envelope(request.context, request.evidence)
    _unique((item.criterion_id for item in request.validated.items), "VALIDATED_ITEM")
    criteria = {item.criterion_id for item in request.validated.items}
    for evidence in request.evidence.evidence:
        if not set(evidence.criterion_ids) <= criteria:
            raise ValueError("UNKNOWN_EVIDENCE_CRITERION")
    if aggregate(request.validated.items, request.validated.items) != request.validated.verdict:
        raise ValueError("AGGREGATE_VERDICT_MISMATCH")
    evidence_by_id = {e.evidence_id: e for e in request.evidence.evidence}
    for item in request.validated.items:
        if item.image and item.image.asset_version_id != request.context.asset_version_id:
            raise ValueError("OBSERVATION_ASSET_MISMATCH")
        _unique(item.evidence_ids, "CITATION")
        for eid in item.evidence_ids:
            if eid not in evidence_by_id:
                raise ValueError("UNKNOWN_CITATION")
            source = evidence_by_id[eid]
            if source.kind == "historical_context" or item.criterion_id not in source.criterion_ids:
                raise ValueError("INVALID_OBSERVATION_CITATION")


def validate_report(request: ReportRequest, result: ReportDraft) -> None:
    validate_report_request(request)
    ensure_context(request.context, result)
    if result.verdict != request.validated.verdict:
        raise ValueError("REPORT_CHANGED_VERDICT")
    _unique(result.evidence_ids, "REPORT_CITATION")
    allowed = {e.evidence_id for e in request.evidence.evidence}
    if not set(result.evidence_ids) <= allowed:
        raise ValueError("UNKNOWN_REPORT_CITATION")
    if request.evidence.evidence and not result.evidence_ids:
        raise ValueError("REPORT_CITATION_REQUIRED")


_NUMBER = re.compile(
    r"(?P<value>[+-]?(?:[0-9]+|[1-9][0-9]{0,2}(?:,[0-9]{3})+)(?:\.[0-9]+)?)"
    r"(?:[ \t]*(?P<unit>[A-Za-z가-힣%₩$€£°/²³]+))?"
)


def _number(raw: str, declared_unit: str | None) -> tuple[Decimal, str | None]:
    """One decimal and an optional literal unit; never extract digits from prose."""
    match = _NUMBER.fullmatch(raw.strip())
    if not match or declared_unit is not None and not declared_unit.strip():
        raise ValueError("invalid numeric value or unit")
    unit = declared_unit.strip() if declared_unit is not None else None
    suffix = match["unit"]
    if suffix and unit and suffix != unit:
        raise ValueError("numeric suffix conflicts with declared unit")
    return Decimal(match["value"].replace(",", "")), suffix or unit


class ReferenceValidator:
    """Code-only A06: validate evidence before comparing an inspector's claims.

    Visual accuracy, source authority and semantic criterion classification still
    belong to upstream adapters and human criteria approval. No unit conversion,
    automatic exceptions, semantic matching or group policies are applied here.

    Two kinds of value are compared by what they denote instead of how they are
    spelled (typed_values.py): dates, weekdays, years and clock times read from the
    inspector's text, and file properties taken from the uploaded original that the
    server measured. Whatever code cannot read with certainty stays review.
    """

    def validate(self, request: ValidateRequest) -> ValidatedResult:
        validate_inspection(InspectRequest(context=request.context, criteria=request.criteria,
                                          asset=request.asset, evidence=request.evidence), request.inspection)
        observations = {o.criterion_id: o for o in request.inspection.observations}
        evidence = {e.evidence_id: e for e in request.evidence.evidence}
        items = []
        for criterion in request.criteria:
            issues = []
            qualifier = qualifier_text(criterion)
            details = criterion.details
            # A file property is settled from the uploaded original itself, not from what a model says it saw.
            measured = None if details is None else file_check(
                criterion, request.original,
                tuple(q.quote for q in details.qualifiers) or ((qualifier,) if qualifier else ()), _number, notation_only)
            typed = typed_kind(criterion)
            if details is not None:
                if details.target!='poster_text' and measured is None:issues.append('typed_attribute_review_required')
                if needs_person(qualifier):
                    issues.append('qualifier_interpretation_required')
                # One criterion holding a whole period is not split here; start and end dates are compared below.
                if details.field=='event_period':
                    issues.append('date_comparison_review_required')
            observation = observations.get(criterion.criterion_id)
            verdict = "review"
            comparison_expected = comparison_observed = comparison_unit = None
            supported = {"text": {"equals", "contains"}, "number": {"equals", "gte", "lte"},
                         "visual": {"present"}}
            if criterion.operator not in supported.get(criterion.kind, set()):
                issues.append("unsupported_criterion")
            if not criterion.expected.strip():
                issues.append("missing_expected_value")
            if not any(e.kind == "current_requirement" and criterion.criterion_id in e.criterion_ids
                       for e in evidence.values()):
                issues.append("missing_requirement_evidence")
            if request.evidence.status != "complete":
                issues.append("context_incomplete")
            if measured is not None:
                pass  # The inspector's reading of a file property is not weighed, present or not.
            elif observation is None:
                issues.append("missing_observation")
            else:
                if not any(evidence[eid].kind == "current_requirement" for eid in observation.evidence_ids):
                    issues.append("missing_citation")
                if observation.uncertainty:
                    issues.append("uncertain_observation")
                status_issue = {"unknown": "observation_status_unknown", "unreadable": "unreadable_observation",
                                "not_checked": "observation_not_checked"}.get(observation.observation_status)
                if status_issue:
                    issues.append(status_issue)
                if observation.observation_status == "observed" and not observation.observed.strip():
                    issues.append("missing_observation")
                if observation.verdict == "review":
                    issues.append("model_review")

            if not issues:
                if measured is not None:
                    comparison_expected, comparison_observed = measured.expected, measured.observed
                    comparison_unit = measured.unit
                    if measured.outcome == MATCH:
                        verdict = "pass"
                    elif not measured.rejects:
                        issues.append("qualifier_interpretation_required")
                    else:
                        verdict = "fail"
                        issues.append("value_mismatch")
                elif observation.observation_status == "absent":
                    if criterion.kind == "number":
                        issues.append("missing_observation")
                    elif (observation.verdict != "fail"
                          or criterion.expected.strip() and (
                              observation.observed == criterion.expected
                              or criterion.kind == "text" and criterion.operator == "contains"
                              and criterion.expected in observation.observed)):
                        issues.append("verdict_conflict")
                    else:
                        verdict = "fail"
                        issues.append("observed_absence")
                elif criterion.kind == "visual":
                    if observation.verdict == "pass":
                        verdict = "pass"
                    else:
                        issues.append("verdict_conflict")
                else:
                    typed_mismatch = False
                    if criterion.kind == "number":
                        try:
                            expected, expected_unit = _number(criterion.expected, criterion.unit)
                        except (ValueError, InvalidOperation):
                            issues.append("numeric_expectation_invalid")
                        try:
                            observed, observed_unit = _number(observation.observed, observation.observed_unit)
                        except (ValueError, InvalidOperation):
                            issues.append("numeric_observation_invalid")
                        if not issues:
                            if expected_unit != observed_unit:
                                issues.append("unit_mismatch")
                            else:
                                comparison_expected, comparison_observed = format(expected, "f"), format(observed, "f")
                                comparison_unit = expected_unit
                                matched = {"equals": observed == expected, "gte": observed >= expected,
                                           "lte": observed <= expected}[criterion.operator]
                    else:
                        comparison_expected, comparison_observed = criterion.expected, observation.observed
                        wanted, seen = format_free(criterion.expected, qualifier), format_free(observation.observed, qualifier)
                        matched = wanted == seen if criterion.operator == "equals" else wanted in seen
                        if typed is not None:
                            outcome = compare_typed(criterion, observation.observed)
                            if outcome != UNKNOWN:
                                matched, typed_mismatch = outcome == MATCH, outcome == MISMATCH
                            elif details.field in DATE_ROLES:
                                # Spelling never settles a date: 10.25 is also part of 2027.10.25.
                                issues.append("date_comparison_review_required")
                    if not issues:
                        if not matched and qualifier and not (
                                may_reject(qualifier) or typed_mismatch and notation_only(qualifier)):
                            issues.append("qualifier_interpretation_required")
                        elif not matched:
                            verdict = "fail"
                            issues.append("value_mismatch")
                        elif observation.verdict == "fail":
                            issues.append("verdict_conflict")
                        else:
                            verdict = "pass"

            if measured is not None:
                # What is shown as observed is the measured file, with the criterion's own sources cited.
                seen_value, seen_unit, seen_status, seen_image = measured.observed, measured.unit, "observed", None
                cited = tuple(e.evidence_id for e in evidence.values()
                              if e.kind == "current_requirement" and criterion.criterion_id in e.criterion_ids)
            elif observation is not None:
                seen_value, seen_unit, seen_status = observation.observed, observation.observed_unit, observation.observation_status
                seen_image, cited = observation.image, observation.evidence_ids
            else:
                seen_value = seen_unit = seen_image = None
                seen_status, cited = "not_checked", ()
            items.append(ValidatedItem(
                criterion_id=criterion.criterion_id, criterion_text=criterion.text, kind=criterion.kind,
                operator=criterion.operator, required=criterion.required, expected=criterion.expected,
                expected_unit=criterion.unit, observed=seen_value, observed_unit=seen_unit,
                observation_status=seen_status,
                comparison_expected=comparison_expected, comparison_observed=comparison_observed,
                comparison_unit=comparison_unit, image=seen_image,
                evidence_ids=cited, verdict=verdict, issues=tuple(issues),
            ))
        return ValidatedResult(context=request.context, verdict=aggregate(request.criteria, items),
                               items=tuple(items),
                               limitations=("Code validation only; visual accuracy and source authority are not independently verified.",),
                               synthetic=request.synthetic or request.inspection.synthetic or request.evidence.synthetic)
