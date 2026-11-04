"""Explicit test-only inspector. The trusted manifest supplies scripted fields."""
from hashlib import sha256
import json
import os
from pathlib import Path
from backend.agent_framework.contracts import Observation,ImageEvidence,InspectionResult
from backend.agent_framework.ports import AdapterFailure


class ScriptedPosterInspector:
    def __init__(self,manifest):self.records={row['display_sha256']:row for row in manifest['records']}
    def inspect_poster(self,request,poster):
        if sha256(poster.content).hexdigest()!=poster.reference.sha256:
            raise AdapterFailure('TEST_POSTER_HASH_MISMATCH','Test poster bytes differ.')
        entry=self.records.get(poster.reference.sha256)
        if entry is None:raise AdapterFailure('TEST_POSTER_NOT_REGISTERED','No scripted observation exists for this image.')
        observations=[]
        for criterion in request.criteria:
            field=criterion.value_source.reference.rsplit(':',1)[-1] if criterion.value_source else ''
            if field not in entry['fields']:raise AdapterFailure('TEST_FIELD_UNSUPPORTED','Only explicit fixture field mappings are supported.')
            # No expected-value copying or comparison. A06 independently detects
            # the deliberately wrong date despite this inspector's claimed pass.
            observed=entry['fields'][field]
            observations.append(Observation(criterion_id=criterion.criterion_id,
                observed=observed or '[synthetic confirmed absence]',verdict='pass' if observed else 'fail',
                observation_status='observed' if observed else 'absent',
                image=ImageEvidence(asset_version_id=poster.reference.asset_version_id,asset_hash=poster.reference.sha256),
                evidence_ids=tuple(e.evidence_id for e in request.evidence.evidence if criterion.criterion_id in e.criterion_ids)))
        return InspectionResult(context=request.context,observations=tuple(observations),synthetic=True)


def make_inspector():
    path=Path(os.environ['FRAMEWORK_POSTER_TEST_MANIFEST'])
    return ScriptedPosterInspector(json.loads(path.read_text(encoding='utf-8')))
