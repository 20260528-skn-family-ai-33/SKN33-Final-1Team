"""Revision/provenance policies using preserved mock extraction, never real approval."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.agent_framework.contracts import CriteriaDraft
from backend.agent_framework.criteria_contracts import CriteriaVersionRequest,CriteriaApprovalRequest
from backend.agent_framework.criteria_review import revise_criteria,approval_blockers
from backend.agent_framework.postgres import digest
from backend.agent_framework.context import freeze_approved_context
from backend.agent_framework.validation import validate_evidence

SOURCE=Path(__file__).resolve().parents[2]/'docs/poster-baselines/2026-11-03/workflow/extraction-demo/EXH-NEW004_mock.json'


def draft():return CriteriaDraft.model_validate_json(SOURCE.read_text(encoding='utf-8'))


def edit(cid,*,action='amend',expected='개발 테스트용 확인값',kind='text',operator='contains'):
    value={'criterion_id':cid,'action':action,'reason':'개발용 보완·범위 검토 시뮬레이션',
        'source':{'reference':'synthetic:test-only','quote':'개발 테스트용 회신: '+expected}}
    if action=='amend':value['resolution']={'kind':kind,'operator':operator,'expected':expected,'required':True,'unit':None}
    return value


def version(source=None,*,purpose='synthetic_test'):
    source=source or draft();first=source.criteria[0].criterion_id
    edits=[edit(c.criterion_id,action='amend' if c.criterion_id==first else 'exclude') for c in source.criteria]
    request=CriteriaVersionRequest(parent_version_id=None,expected_base_sha256=digest(source),purpose=purpose,edits=edits)
    return revise_criteria(source,None,request,version_id=uuid4(),created_by=source.context.actor_id,created_at=datetime.now(timezone.utc))


def test_amendments_preserve_pdf_anchor_and_add_separate_declared_value_source():
    source=draft();before=source.model_dump_json();revised=version(source)
    criterion=revised.criteria[0]
    assert source.model_dump_json()==before
    assert criterion.source==source.criteria[0].source and criterion.text==source.criteria[0].text
    assert criterion.value_source.quote!=criterion.source.quote
    assert criterion.value_source.quote_sha256==sha256(criterion.value_source.quote.encode()).hexdigest()
    assert criterion.value_source.submitted_by==source.context.actor_id
    assert criterion.human_added and revised.synthetic
    assert not approval_blockers(revised.criteria)
    assert len(revised.exclusions)==len(source.criteria)-1


def test_frozen_evidence_carries_human_value_provenance_and_cannot_drop_it():
    revised=version();evidence=freeze_approved_context(revised.context,revised.criteria,synthetic=True)
    assert evidence.evidence[0].value_source==revised.criteria[0].value_source
    bad=evidence.evidence[0].model_copy(update={'value_source':None})
    with pytest.raises(ValueError,match='EVIDENCE_VALUE_SOURCE_MISMATCH'):
        validate_evidence(revised.context,revised.criteria,evidence.model_copy(update={'evidence':(bad,)}))


def test_excluded_condition_can_be_restored_only_as_a_new_revision_with_provenance():
    source=draft();one=version(source);excluded=one.exclusions[0].criterion_id
    request=CriteriaVersionRequest(parent_version_id=one.criteria_version_id,expected_base_sha256=digest(one),
        purpose='synthetic_test',edits=[edit(excluded,expected='새 버전에서 복원한 개발값')])
    two=revise_criteria(source,one,request,version_id=uuid4(),created_by=source.context.actor_id,created_at=datetime.now(timezone.utc))
    assert two.version_no==2 and two.parent_version_id==one.criteria_version_id
    assert len(one.criteria)==1 and len(two.criteria)==2
    assert excluded not in {c.criterion_id for c in two.exclusions}
    assert next(c for c in two.criteria if c.criterion_id==excluded).value_source.quote.endswith('새 버전에서 복원한 개발값')


@pytest.mark.parametrize('kind,operator,expected,issue',(
    ('text','contains','','missing_expected_value'),('text','equals',' \n ','missing_expected_value'),
    ('unsupported','manual','value','unsupported_or_manual_criterion'),('number','equals','NaN','numeric_expected_invalid'),
    ('number','gte','wrong-number','numeric_expected_invalid'),('visual','manual','value','unsupported_or_manual_criterion')))
def test_approval_blocks_unresolved_or_unsupported_conditions(kind,operator,expected,issue):
    source=draft();c=source.criteria[0].model_copy(update={'kind':kind,'operator':operator,'expected':expected})
    assert issue in approval_blockers((c,))[c.criterion_id]


@pytest.mark.parametrize('mutation',('unknown','all_excluded','duplicate','ungrounded','blank_reference','spoof_actor'))
def test_invalid_edits_cannot_hide_scope_or_attribute_false_provenance(mutation):
    source=draft();value=edit(source.criteria[0].criterion_id)
    body={'parent_version_id':None,'expected_base_sha256':digest(source),'purpose':'synthetic_test','edits':[value]}
    if mutation=='unknown':value['criterion_id']='absent'
    if mutation=='all_excluded':body['edits']=[edit(c.criterion_id,action='exclude') for c in source.criteria]
    if mutation=='duplicate':body['edits']=[value,value]
    if mutation=='ungrounded':value['resolution']['expected']='NOT_IN_SUPPLEMENT'
    if mutation=='blank_reference':value['source']['reference']='  '
    if mutation=='spoof_actor':value['source']['submitted_by']=str(uuid4())
    with pytest.raises(ValueError):
        req=CriteriaVersionRequest.model_validate(body)
        revise_criteria(source,None,req,version_id=uuid4(),created_by=source.context.actor_id,created_at=datetime.now(timezone.utc))


def test_mock_provenance_cannot_be_relabelled_as_real_by_a_request():
    result=version(purpose='human_review')
    assert result.synthetic and result.criteria[0].value_source.synthetic


@pytest.mark.parametrize('reviewed,reason',((False,'reason'),(1,'reason'),(True,'   ')))
def test_approval_requires_explicit_nonblank_review_intent(reviewed,reason):
    with pytest.raises(ValidationError):
        CriteriaApprovalRequest(criteria_version_id=uuid4(),expected_sha256='a'*64,purpose='synthetic_test',
            reason=reason,reviewed_all_criteria=reviewed)


@pytest.mark.parametrize('target,sha',((str(uuid4()),None),(None,'a'*64)))
def test_approval_target_requires_exact_version_and_hash_pair(target,sha):
    from backend.agent_framework.contracts import Approval
    with pytest.raises(ValidationError):
        Approval(context=draft().context,actor_id=draft().context.actor_id,decision='approve_criteria',
            approved_criteria_version_id=target,criteria_sha256=sha)


def test_legacy_workflow_cannot_bypass_version_review_by_supplying_target_fields():
    from backend.agent_framework.adapters.synthetic import make_agents,sample_inputs
    from backend.agent_framework.contracts import Approval
    from backend.agent_framework.runtime import Workflow
    context,document,asset=sample_inputs();flow=Workflow(make_agents());flow.start(context,document,asset)
    with pytest.raises(ValueError,match='immutable criteria review'):
        flow.approve(context.run_id,Approval(context=context,actor_id=context.actor_id,decision='approve_criteria',
            approved_criteria_version_id=str(uuid4()),criteria_sha256='a'*64))
