"""Freeze approved provenance; no search role, database, model or network call.

PG -> Neo4j -> PG context preparation is a separate integration task. This
function only freezes the sources already present in the approved criteria.
"""
from hashlib import sha256
import json

from .contracts import Context, Criterion, Evidence, EvidenceBundle
from .validation import _criteria, validate_evidence


def freeze_approved_context(
    context: Context, criteria: tuple[Criterion, ...], *, synthetic: bool = False,
) -> EvidenceBundle:
    _criteria(context, criteria)
    snapshot = json.dumps({
        'context': context.model_dump(mode='json'),
        'criteria': [criterion.model_dump(mode='json') for criterion in criteria],
    }, sort_keys=True, ensure_ascii=False).encode()
    bundle = EvidenceBundle(
        context=context, context_id='approved-' + sha256(snapshot).hexdigest(),
        status='complete', synthetic=synthetic,
        evidence=tuple(Evidence(
            evidence_id='criterion-' + sha256(criterion.criterion_id.encode()).hexdigest(),
            project_id=context.project_id, kind='current_requirement',
            criterion_ids=(criterion.criterion_id,),
            source_record_id=criterion.source.block_id, source=criterion.source,
        ) for criterion in criteria),
        warnings=('Approved-criteria context only; no PostgreSQL/Neo4j relationship lookup was executed.',),
    )
    validate_evidence(context, criteria, bundle)
    return bundle
