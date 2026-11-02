"""Offline framework state/adapter boundary tests; no DB, server or real model."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from threading import Barrier

import pytest

from backend.agent_framework.contracts import (
    Approval, AssetRef, Context, CriteriaDraft, Criterion, DocumentRef, Evidence,
    EvidenceBundle, ImageEvidence, InspectionResult, Observation, ReportDraft,
    SourceRef,
)
from backend.agent_framework.ports import AdapterFailure, Agents
from backend.agent_framework.runtime import Workflow
from backend.agent_framework.validation import ReferenceValidator


def hashed(text):
    return sha256(text.encode()).hexdigest()


def context(**changes):
    return Context(project_id='project-1', run_id=changes.pop('run_id','run-1'), actor_id='person-1',
                   document_version_id='document-v1', criteria_version_id='criteria-v1',
                   asset_version_id=changes.pop('asset_version_id','asset-v1'), **changes)


def document():
    return DocumentRef(file_id='pdf-1', document_version_id='document-v1', sha256=hashed('pdf'), page_count=1)


def asset(version='asset-v1'):
    return AssetRef(file_id=version, asset_version_id=version, sha256=hashed(version), width=1200, height=800)


def approval(ctx, decision, **fields):
    return Approval(context=ctx, actor_id=ctx.actor_id, decision=decision, **fields)


class Examples:
    def __init__(self, *, count=1, observed='Hello', uncertainty=None, synthetic=True):
        self.calls=[]
        self.count=count
        self.observed=observed
        self.uncertainty=uncertainty
        self.synthetic=synthetic
        self.retrieval_status='complete'
        self.fail_at=None
        self.transforms={}

    def called(self, name, value=None):
        self.calls.append(name)
        if self.fail_at and self.fail_at[0]==name:
            raise self.fail_at[1]
        return self.transforms.get(name,lambda item:item)(value)

    def extract(self, request):
        source=SourceRef(document_version_id=request.context.document_version_id,page=1,
                         block_id='block-1',quote='Display Hello',source_hash=hashed('Display Hello'))
        criteria=tuple(Criterion(criterion_id=f'criterion-{index}',text='Display greeting',kind='text',
                                  expected='Hello',operator='equals',source=source)
                       for index in range(1,self.count+1))
        return self.called('extract',CriteriaDraft(context=request.context,criteria=criteria,synthetic=self.synthetic))

    def inspect(self, request):
        observations=[]
        for index,item in enumerate(request.criteria,1):
            evidence_ids=tuple(e.evidence_id for e in request.evidence.evidence if item.criterion_id in e.criterion_ids)
            observations.append(Observation(criterion_id=item.criterion_id,observed=self.observed,verdict='pass',
                image=ImageEvidence(asset_version_id=request.asset.asset_version_id,asset_hash=request.asset.sha256,bbox=(0.1,0.1,0.5,0.5)),
                evidence_ids=evidence_ids,uncertainty=self.uncertainty))
        return self.called('inspect',InspectionResult(context=request.context,observations=tuple(observations),synthetic=False))

    def validate(self, request):
        return self.called('validate',ReferenceValidator().validate(request))

    def report(self, request):
        return self.called('report',ReportDraft(context=request.context,verdict=request.validated.verdict,
                summary='Synthetic fixture report; not a real model result.',
                evidence_ids=tuple(e.evidence_id for e in request.evidence.evidence),synthetic=False))

    def agents(self):
        return Agents(extractor=self,inspector=self,validator=self,reporter=self)


def reviewed(example=None):
    example=example or Examples()
    wf=Workflow(example.agents(),profile='test')
    ctx=context()
    first=wf.start(ctx,document(),asset())
    assert first.execution_status=='waiting_criteria'
    return wf,ctx,example,wf.approve(ctx.run_id,approval(ctx,'approve_criteria'))


def test_actual_graph_nodes_wait_for_human_criteria_and_preserve_three_states():
    ex=Examples();wf=Workflow(ex.agents(),profile='test');ctx=context()
    started=wf.start(ctx,document(),asset())
    assert started.execution_status=='waiting_criteria'
    assert started.machine_verdict is None and started.human_decision=='unreviewed'
    assert ex.calls==['extract']
    result=wf.approve(ctx.run_id,approval(ctx,'approve_criteria'))
    assert result.execution_status=='waiting_review' and result.machine_verdict=='pass'
    assert result.stages==('A01','A02','A04_A05','A06','A07')
    assert ex.calls==['extract','inspect','validate','report']
    assert result.human_decision=='unreviewed'
    assert all(x.synthetic for x in (result,result.criteria,result.evidence,result.inspection,result.validated,result.report))
    final=wf.approve(ctx.run_id,approval(ctx,'approve_final'))
    assert final.execution_status=='completed' and final.human_decision=='final_approved'
    assert final.machine_verdict=='pass' and final.synthetic
    assert len(final.approvals)==2


def test_memory_only_and_production_rejected():
    ex=Examples();wf,ctx,_,_=reviewed(ex)
    assert wf.persistence=='memory_only'
    with pytest.raises(ValueError,match='production'):
        Workflow(ex.agents(),profile='production')
    with pytest.raises(KeyError):
        Workflow(ex.agents()).get(ctx.run_id)


def test_duplicate_run_and_approval_cannot_repeat_adapters():
    wf,ctx,ex,result=reviewed()
    before=tuple(ex.calls)
    with pytest.raises(ValueError,match='already exists'):
        wf.start(ctx,document(),asset())
    with pytest.raises(ValueError,match='already recorded'):
        wf.approve(ctx.run_id,approval(ctx,'approve_criteria'))
    assert tuple(ex.calls)==before and wf.get(ctx.run_id)==result
    final=wf.approve(ctx.run_id,approval(ctx,'approve_final'))
    with pytest.raises(ValueError):wf.approve(ctx.run_id,approval(ctx,'approve_final'))
    assert wf.get(ctx.run_id)==final


@pytest.mark.parametrize('field,value',[
    ('project_id','another-project'),('run_id','another-run'),('actor_id','another-person'),
    ('document_version_id','document-v0'),('criteria_version_id','criteria-v0'),('asset_version_id','asset-v0'),
])
def test_old_or_foreign_approval_context_is_rejected_without_mutating_run(field,value):
    wf,ctx,_,current=reviewed()
    wrong=ctx.model_copy(update={field:value})
    with pytest.raises(ValueError):wf.approve(ctx.run_id,approval(wrong,'approve_final'))
    assert wf.get(ctx.run_id)==current


def test_approval_actor_must_match_context_but_no_authentication_is_simulated():
    wf,ctx,_,current=reviewed()
    value=Approval(context=ctx,actor_id='other-person',decision='approve_final')
    with pytest.raises(ValueError):wf.approve(ctx.run_id,value)
    assert wf.get(ctx.run_id)==current


def test_concurrent_criteria_approvals_execute_graph_once():
    ex=Examples();wf=Workflow(ex.agents(),profile='test');ctx=context();wf.start(ctx,document(),asset())
    barrier=Barrier(2)
    def approve(_):
        barrier.wait()
        try:return wf.approve(ctx.run_id,approval(ctx,'approve_criteria')).execution_status
        except ValueError:return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(approve,range(2)))
    assert sorted(results)==['rejected','waiting_review']
    assert ex.calls==['extract','inspect','validate','report']


def test_rereview_requires_new_run_asset_and_new_criteria_approval():
    wf,ctx,ex,_=reviewed()
    newctx=context(run_id='run-2',asset_version_id='asset-v2',parent_run_id=ctx.run_id)
    with pytest.raises(ValueError):wf.rereview(ctx.run_id,newctx,asset('asset-v2'))
    parent=wf.approve(ctx.run_id,approval(ctx,'approve_changes',reason='Adjust the layout.'))
    assert parent.execution_status=='waiting_revision'
    with pytest.raises(ValueError):wf.rereview(ctx.run_id,newctx.model_copy(update={'asset_version_id':'asset-v1'}),asset())
    child=wf.rereview(ctx.run_id,newctx,asset('asset-v2'))
    assert child.execution_status=='waiting_criteria' and not child.approvals
    assert child.machine_verdict is None and child.human_decision=='unreviewed'
    assert child.evidence is child.inspection is child.validated is child.report is None
    assert child.context.parent_run_id==ctx.run_id and child.criteria.context==newctx
    assert ex.calls.count('extract')==1
    assert wf.get(ctx.run_id)==parent
    with pytest.raises(ValueError):wf.approve(newctx.run_id,approval(ctx,'approve_criteria'))
    with pytest.raises(ValueError):wf.approve(newctx.run_id,approval(newctx,'approve_final'))
    result=wf.approve(newctx.run_id,approval(newctx,'approve_criteria'))
    assert result.execution_status=='waiting_review' and result.machine_verdict=='pass'
    assert ex.calls.count('inspect')==2
    assert wf.approve(newctx.run_id,approval(newctx,'approve_final')).execution_status=='completed'
    with pytest.raises(ValueError):wf.rereview(ctx.run_id,newctx.model_copy(update={'run_id':'run-3'}),asset('asset-v2'))


def test_child_run_cannot_be_started_without_validating_parent():
    ex=Examples();wf=Workflow(ex.agents())
    with pytest.raises(ValueError):wf.start(context(parent_run_id='missing-parent'),document(),asset())
    assert ex.calls==[]


def test_exception_requires_all_nonpass_required_targets_and_keeps_machine_failure():
    wf,ctx,_,result=reviewed(Examples(count=2,observed='Different'))
    assert result.machine_verdict=='fail'
    with pytest.raises(ValueError):wf.approve(ctx.run_id,approval(ctx,'approve_final'))
    with pytest.raises(ValueError):wf.approve(ctx.run_id,approval(ctx,'approve_exception',reason='Accept discrepancy',criterion_ids=('criterion-1',)))
    with pytest.raises(ValueError):wf.approve(ctx.run_id,approval(ctx,'approve_exception',reason='Accept discrepancy',criterion_ids=('criterion-1','criterion-2','unknown')))
    final=wf.approve(ctx.run_id,approval(ctx,'approve_exception',reason='Accept these two documented discrepancies',criterion_ids=('criterion-1','criterion-2')))
    assert final.execution_status=='completed' and final.human_decision=='exception_approved'
    assert final.machine_verdict=='fail' and final.validated==result.validated


@pytest.mark.parametrize('kind',['uncertain','missing'])
def test_exception_cannot_turn_missing_or_unknown_evidence_into_approval(kind):
    ex=Examples(uncertainty='not readable' if kind=='uncertain' else None)
    if kind=='missing':ex.transforms['inspect']=lambda result:result.model_copy(update={'observations':()})
    wf,ctx,_,result=reviewed(ex)
    assert result.machine_verdict=='review'
    with pytest.raises(ValueError):wf.approve(ctx.run_id,approval(ctx,'approve_exception',reason='force approval',criterion_ids=('criterion-1',)))
    assert wf.get(ctx.run_id)==result


@pytest.mark.parametrize('stage',['extract','inspect','validate','report'])
def test_output_context_boundary_failure_stops_remaining_stages(stage):
    ex=Examples()
    ex.transforms[stage]=lambda result:result.model_copy(update={'context':result.context.model_copy(update={'project_id':'forged'})})
    wf=Workflow(ex.agents(),profile='test');ctx=context();result=wf.start(ctx,document(),asset())
    if stage!='extract':result=wf.approve(ctx.run_id,approval(ctx,'approve_criteria'))
    assert result.execution_status=='failed' and result.error.code=='contract_violation'
    assert ex.calls[-1]==stage and result.context==ctx


def test_pydantic_model_copy_cannot_bypass_output_validation():
    ex=Examples();ex.transforms['inspect']=lambda value:value.model_copy(update={'observations':'invalid'})
    wf,ctx,_,result=reviewed(ex)
    assert result.execution_status=='failed' and result.error.code=='contract_violation'
    assert 'validate' not in ex.calls


def test_report_cannot_change_verdict_or_create_citations():
    for change in ({'verdict':'fail'},{'evidence_ids':('made-up',)}):
        ex=Examples();ex.transforms['report']=lambda value,patch=change:value.model_copy(update=patch)
        wf,ctx,_,result=reviewed(ex)
        assert result.execution_status=='failed' and result.machine_verdict=='pass'
        assert result.report is None


def test_unknown_remote_outcome_has_no_automatic_retry():
    ex=Examples();ex.fail_at=('inspect',AdapterFailure('remote_timeout','Completion cannot be established.',retryable=False,outcome_unknown=True))
    wf,ctx,_,result=reviewed(ex)
    assert result.execution_status=='outcome_unknown' and result.error.outcome_unknown
    assert ex.calls.count('inspect')==1 and 'report' not in ex.calls
    with pytest.raises(ValueError):wf.approve(ctx.run_id,approval(ctx,'approve_criteria'))
    assert ex.calls.count('inspect')==1


def test_retryable_adapter_failure_is_metadata_and_is_not_automatically_replayed():
    ex=Examples();ex.fail_at=('inspect',AdapterFailure('inspection_unavailable','Unavailable.',retryable=True))
    _,_,_,result=reviewed(ex)
    assert result.execution_status=='failed' and result.error.retryable
    assert ex.calls.count('inspect')==1


def test_provider_exception_is_sanitized_and_cannot_claim_success():
    ex=Examples();ex.fail_at=('extract',RuntimeError('secret provider token'))
    result=Workflow(ex.agents()).start(context(),document(),asset())
    assert result.execution_status=='failed' and result.error.code=='adapter_failed'
    assert 'secret provider token' not in result.model_dump_json()


def test_returned_snapshots_cannot_mutate_runtime_state():
    wf,ctx,_,snapshot=reviewed()
    object.__setattr__(snapshot.context,'project_id','tampered')
    object.__setattr__(snapshot.validated,'verdict','fail')
    current=wf.get(ctx.run_id)
    assert current.context.project_id=='project-1' and current.validated.verdict=='pass'


def test_different_input_versions_are_rejected_before_any_adapter_runs():
    ex=Examples();wf=Workflow(ex.agents())
    with pytest.raises(ValueError):wf.start(context(),document().model_copy(update={'document_version_id':'v2'}),asset())
    with pytest.raises(ValueError):wf.start(context(),document(),asset('v2'))
    assert ex.calls==[]


def test_rereview_cannot_relabel_reused_draft_as_a_new_criteria_version():
    wf,ctx,ex,_=reviewed()
    parent=wf.approve(ctx.run_id,approval(ctx,'approve_changes',reason='Revise the poster'))
    changed=context(run_id='run-2',asset_version_id='asset-v2',parent_run_id=ctx.run_id)
    changed=changed.model_copy(update={'criteria_version_id':'criteria-v2'})
    with pytest.raises(ValueError,match='criteria'):
        wf.rereview(ctx.run_id,changed,asset('asset-v2'))
    assert wf.get(ctx.run_id)==parent
    with pytest.raises(KeyError):wf.get(changed.run_id)
    assert ex.calls.count('extract')==1
    # Rejection does not consume the parent's change request.
    accepted=changed.model_copy(update={'criteria_version_id':ctx.criteria_version_id,'actor_id':'another-worker'})
    child=wf.rereview(ctx.run_id,accepted,asset('asset-v2'))
    assert child.execution_status=='waiting_criteria' and child.context.actor_id=='another-worker'


def test_conflicting_model_verdict_is_not_eligible_for_human_exception():
    ex=Examples()
    def conflict(result):
        return result.model_copy(update={'observations':tuple(
            observation.model_copy(update={'verdict':'fail'}) for observation in result.observations)})
    ex.transforms['inspect']=conflict
    wf,ctx,_,result=reviewed(ex)
    assert result.machine_verdict=='review'
    assert 'verdict_conflict' in result.validated.items[0].issues
    with pytest.raises(ValueError,match='unknown or unsupported'):
        wf.approve(ctx.run_id,approval(ctx,'approve_exception',reason='Ignore the conflict',criterion_ids=('criterion-1',)))
    assert wf.get(ctx.run_id)==result


@pytest.mark.parametrize('kind',['review_without_reason','unknown_review_issue','unknown_failure_issue'])
def test_custom_validator_cannot_expand_exception_policy(kind):
    ex=Examples(observed='Different' if kind=='unknown_failure_issue' else 'Hello')
    def custom(value):
        issue=() if kind=='review_without_reason' else ('unknown_visual_evidence',)
        items=tuple(item.model_copy(update={
            'verdict':'fail' if kind=='unknown_failure_issue' else 'review',
            'issues':item.issues+issue}) for item in value.items)
        return value.model_copy(update={'verdict':'fail' if kind=='unknown_failure_issue' else 'review','items':items})
    ex.transforms['validate']=custom
    wf,ctx,_,result=reviewed(ex)
    assert result.execution_status=='waiting_review'
    with pytest.raises(ValueError,match='unknown or unsupported'):
        wf.approve(ctx.run_id,approval(ctx,'approve_exception',reason='Try to accept unestablished evidence',criterion_ids=('criterion-1',)))
    assert wf.get(ctx.run_id)==result


def test_definite_failure_without_uncertainty_issues_can_be_explicitly_accepted():
    ex=Examples()
    ex.transforms['extract']=lambda value:value.model_copy(update={'criteria':tuple(
        item.model_copy(update={'kind':'visual','operator':'present'}) for item in value.criteria)})
    ex.transforms['inspect']=lambda value:value.model_copy(update={'observations':tuple(
        item.model_copy(update={'verdict':'fail'}) for item in value.observations)})
    wf,ctx,_,result=reviewed(ex)
    assert result.machine_verdict=='fail' and result.validated.items[0].issues==()
    final=wf.approve(ctx.run_id,approval(ctx,'approve_exception',reason='Accept the stated visual discrepancy',criterion_ids=('criterion-1',)))
    assert final.human_decision=='exception_approved' and final.machine_verdict=='fail'


@pytest.mark.parametrize('operator', ['equals', 'contains'])
@pytest.mark.parametrize('other_failure', [False, True], ids=['only-missing', 'with-failure'])
def test_blank_text_expectation_blocks_final_and_exception_approval(operator, other_failure):
    ex = Examples(count=2 if other_failure else 1, observed='Unrelated phrase')

    def missing_expected(draft):
        criterion = draft.criteria[0].model_copy(update={'expected': ' ', 'operator': operator})
        return draft.model_copy(update={'criteria': (criterion, *draft.criteria[1:])})

    ex.transforms['extract'] = missing_expected
    wf, ctx, _, result = reviewed(ex)
    assert result.execution_status == 'waiting_review'
    assert result.machine_verdict == ('fail' if other_failure else 'review')
    assert result.validated.items[0].verdict == 'review'
    assert result.validated.items[0].issues == ('missing_expected_value',)
    if other_failure:
        assert result.validated.items[1].verdict == 'fail'
        assert result.validated.items[1].issues == ('value_mismatch',)
    with pytest.raises(ValueError, match='requires a pass'):
        wf.approve(ctx.run_id, approval(ctx, 'approve_final'))
    with pytest.raises(ValueError, match='unknown or unsupported'):
        wf.approve(ctx.run_id, approval(ctx, 'approve_exception',
            reason='Incomplete expectations cannot be accepted as exceptions',
            criterion_ids=tuple(item.criterion_id for item in result.criteria.criteria)))
    assert wf.get(ctx.run_id) == result
