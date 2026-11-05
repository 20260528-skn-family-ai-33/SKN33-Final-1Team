"""Human observations supplement a completed review; machine evidence stays immutable."""

HUMAN_REVIEW_ISSUES=frozenset({
    'typed_attribute_review_required','qualifier_interpretation_required','normalized_value_review_required',
    'date_comparison_review_required','uncertain_observation','missing_observation','unreadable_observation',
    'observation_status_unknown','observation_not_checked','model_review','verdict_conflict',
    'numeric_observation_invalid','unit_mismatch',
})


def can_review(issues):
    return bool(issues) and set(issues)<=HUMAN_REVIEW_ISSUES


def validate_human_reviews(state,approval):
    items={item.criterion_id:item for item in state.validated.items}
    if approval.human_reviews:
        if state.context.asset_version_id is None or state.evidence is None or state.evidence.status!='complete':
            raise ValueError('human review requires the exact asset and complete criterion evidence')
        for review in approval.human_reviews:
            item=items.get(review.criterion_id)
            if item is None or item.verdict!='review' or not can_review(item.issues):
                raise ValueError('human review cannot override a confirmed verdict or unresolved criterion evidence')
            if not any(e.kind=='current_requirement' and item.criterion_id in e.criterion_ids for e in state.evidence.evidence):
                raise ValueError('human review requires current requirement evidence')
    if approval.decision=='approve_final':
        required=[item for item in items.values() if item.required]
        passed={r.criterion_id for r in approval.human_reviews if r.verdict=='pass'}
        if (not required or any(item.verdict=='fail' for item in required)
                or any(r.verdict=='fail' for r in approval.human_reviews)
                or any(item.verdict=='review' and item.criterion_id not in passed for item in required)):
            raise ValueError('final approval requires a pass or evidenced human review of every pending required item')
