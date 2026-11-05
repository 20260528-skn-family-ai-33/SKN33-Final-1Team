"""Explicit human decisions over synthetic evidence, never a real approval."""
from hashlib import sha256

import pytest
from pydantic import ValidationError

from backend.agent_framework.contracts import Approval,HumanReview,RequirementDetails
from backend.agent_framework.runtime import Workflow
from backend.agent_framework.agents.coordinator import allowed_actions
from tests.agent_framework.test_runtime import Examples,context,document,asset,approval


def example(target='poster_text',field='event_date'):
    ex=Examples(observed='Hello')
    def classify(draft):
        c=draft.criteria[0]
        source=c.source.model_copy(update={'quote':'Hello','source_hash':sha256(b'Hello').hexdigest()})
        details=RequirementDetails(target=target,field=field,expected_source=source)
        return draft.model_copy(update={'criteria':(c.model_copy(update={'details':details}),)})
    ex.transforms['extract']=classify
    return ex


def pending(ex=None):
    ex=ex or example();flow=Workflow(ex.agents(),profile='test');ctx=context()
    flow.start(ctx,document(),asset())
    return flow,ctx,flow.approve(ctx.run_id,approval(ctx,'approve_criteria'))


def human(verdict='pass',**changes):
    return {'criterion_id':'criterion-1','verdict':verdict,'observed':'직접 확인한 표시 Hello',
        'reference':'합성 포스터 오른쪽 상단','reason':'승인 기준과 직접 대조한 개발용 확인','checked_asset':True}|changes


@pytest.mark.parametrize('target,field',[('poster_text','event_date'),('poster_visual','logo'),
    ('file_metadata','file_format'),('delivery','quantity')])
def test_human_confirmation_completes_without_rewriting_machine_evidence(target,field):
    flow,ctx,before=pending(example(target,field))
    assert before.machine_verdict=='review'
    with pytest.raises(ValueError):flow.approve(ctx.run_id,approval(ctx,'approve_final'))
    after=flow.approve(ctx.run_id,approval(ctx,'approve_final',reason='담당자 직접 확인',human_reviews=[human()]))
    assert after.execution_status=='completed' and after.human_decision=='final_approved'
    assert after.machine_verdict=='review' and after.validated==before.validated and after.report==before.report
    assert after.approvals[-1].context==ctx and after.approvals[-1].actor_id==ctx.actor_id
    assert after.approvals[-1].human_reviews[0].reference==human()['reference']


def test_human_failure_requests_correction_and_does_not_carry_to_a_new_asset():
    flow,ctx,before=pending()
    with pytest.raises(ValueError):
        flow.approve(ctx.run_id,approval(ctx,'approve_final',reason='직접 확인',human_reviews=[human('fail')]))
    after=flow.approve(ctx.run_id,approval(ctx,'approve_changes',reason='직접 확인한 불충족 수정',human_reviews=[human('fail')]))
    assert after.machine_verdict=='review' and after.execution_status=='waiting_revision'
    child_ctx=context(run_id='child',asset_version_id='asset-v2',parent_run_id=ctx.run_id)
    flow.rereview(ctx.run_id,child_ctx,asset('asset-v2'))
    child=flow.approve(child_ctx.run_id,approval(child_ctx,'approve_criteria'))
    assert child.machine_verdict=='review' and not any(a.human_reviews for a in child.approvals)
    with pytest.raises(ValueError):flow.approve(child_ctx.run_id,approval(child_ctx,'approve_final'))


@pytest.mark.parametrize('patch',[{'checked_asset':False},{'checked_asset':1},{'observed':' '},{'reference':''},{'reason':'\n'}])
def test_direct_inspection_and_nonblank_evidence_are_mandatory(patch):
    with pytest.raises(ValidationError):HumanReview.model_validate(human(**patch))


@pytest.mark.parametrize('fault',['confirmed_fail','citation','unknown_code','missing_expected','stale_asset','duplicate','extra_id'])
def test_human_review_cannot_bypass_scope_evidence_or_confirmed_failure(fault):
    ex=Examples(observed='Different') if fault=='confirmed_fail' else example()
    if fault=='citation':
        ex.transforms['inspect']=lambda r:r.model_copy(update={'observations':tuple(o.model_copy(update={'evidence_ids':()}) for o in r.observations)})
    if fault=='unknown_code':
        ex.transforms['validate']=lambda r:r.model_copy(update={'items':tuple(i.model_copy(update={'issues':i.issues+('future_unknown',)}) for i in r.items)})
    if fault=='missing_expected':
        ex=Examples();ex.transforms['extract']=lambda d:d.model_copy(update={'criteria':tuple(c.model_copy(update={'expected':''}) for c in d.criteria)})
    flow,ctx,before=pending(ex)
    reviews=[human(criterion_id='other' if fault=='extra_id' else 'criterion-1')]
    if fault=='duplicate':reviews*=2
    if fault=='stale_asset':ctx=ctx.model_copy(update={'asset_version_id':'other-asset'})
    with pytest.raises(ValueError):
        flow.approve(ctx.run_id,approval(ctx,'approve_final',reason='test',human_reviews=reviews))
    assert flow.get(ctx.run_id)==before


def test_not_checked_requires_new_human_observation_and_a01_proposes_it():
    ex=example()
    ex.transforms['inspect']=lambda r:r.model_copy(update={'observations':tuple(o.model_copy(update={'observation_status':'not_checked','observed':''}) for o in r.observations)})
    flow,ctx,before=pending(ex)
    actions=allowed_actions({'execution_status':before.execution_status,'machine_verdict':before.machine_verdict,
        'validated_items':[i.model_dump() for i in before.validated.items]})
    assert 'request_human_review' in actions and 'request_exception_approval' not in actions
    assert flow.approve(ctx.run_id,approval(ctx,'approve_final',reason='test',human_reviews=[human()])).execution_status=='completed'


def test_legacy_approval_serialization_does_not_gain_empty_review_fields():
    assert 'human_reviews' not in approval(context(),'approve_final').model_dump(mode='json')
