"""Runtime/owner settings boundaries before any database or model access."""
from datetime import datetime,timezone
import json

import pytest

from backend.agent_framework.cli import main
from backend.agent_framework.contracts import FrameworkError,StepRecord
from backend.agent_framework.runtime import Workflow
from backend.agent_framework.adapters.synthetic import sample_inputs,make_agents


@pytest.mark.parametrize('command',['worker','postgres-check','postgres-serve'])
def test_runtime_rejects_owner_credentials_before_connections(tmp_path,capsys,command):
    env=tmp_path/'owner.env'
    env.write_text('APP_ENV=test\nDB_OWNER_PASSWORD=never-print-this-secret\n', encoding="utf-8")
    args=[command,'--env-file',str(env)]
    if command!='postgres-check':args+=['--config','examples/agent_framework/team_config.synthetic.json']
    assert main(args)==2
    output=capsys.readouterr()
    assert json.loads(output.out)['error']['code']=='POSTGRES_CONFIGURATION_INVALID'
    assert 'never-print-this-secret' not in output.out+output.err


def test_deferred_commands_make_no_adapter_calls_and_restore_in_isolation():
    from backend.agent_framework.contracts import Approval
    class Forbidden:
        def call(self,*_):raise AssertionError('admission must never invoke an adapter')
        extract=retrieve=inspect=validate=report=call
    from backend.agent_framework.ports import Agents
    no=Forbidden();context,document,asset=sample_inputs()
    flow=Workflow(Agents(no,no,no,no))
    first=flow.start(context,document,asset,defer=True)
    assert first.execution_status=='queued' and not first.step_records
    baseline=Workflow(make_agents()).start(context,document,asset)
    flow2=Workflow(Agents(no,no,no,no));flow2.restore(baseline,document,asset)
    queued=flow2.approve(context.run_id,Approval(context=context,actor_id=context.actor_id,decision='approve_criteria'),defer=True)
    assert queued.execution_status=='queued' and len(queued.approvals)==1
    assert len(queued.step_records)==1


def test_recovered_unknown_duration_is_not_invented():
    now=datetime.now(timezone.utc)
    record=StepRecord(ordinal=1,role='A02',status='outcome_unknown',recorded_at=now,started_at=now,
        finished_at=now,duration_ms=None,error=FrameworkError(code='EXECUTION_OUTCOME_UNKNOWN',message='Unknown',outcome_unknown=True))
    assert record.duration_ms is None
