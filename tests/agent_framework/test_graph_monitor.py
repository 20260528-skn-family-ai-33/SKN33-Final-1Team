from copy import deepcopy
from datetime import datetime,timedelta,timezone
import pytest
from backend.agent_framework.graph_monitor import (MonitorPolicy,classify,snapshot,save_snapshot,
    load_snapshot,Transitions,observe)


def metrics(**project):
    return {'projects':[{'project_id':'project','required_revision':3,'applied_revision':2,
        'consecutive_failures':0,'has_error':False,'pending_events':1,'oldest_pending_seconds':30,**project}],
        'truncated':False,'recent_contexts':0,'recent_sql_fallbacks':0}


@pytest.mark.parametrize('age,state',[(119,'healthy'),(120,'warning'),(500,'warning')])
def test_pending_work_is_only_late_after_threshold(age,state):
    result,alerts=classify(metrics(oldest_pending_seconds=age),{'project':2},MonitorPolicy())
    assert result==state
    assert bool(alerts)==(state=='warning')


def test_failure_threshold_and_missing_projection_are_distinct():
    state,alerts=classify(metrics(consecutive_failures=3),{'project':2},MonitorPolicy())
    assert state=='critical' and [a['code'] for a in alerts]==['CONSECUTIVE_SYNC_FAILURES']
    state,alerts=classify(metrics(required_revision=2,pending_events=0),{},MonitorPolicy())
    assert state=='critical' and [a['code'] for a in alerts]==['GRAPH_PROJECTION_MISSING']


def test_newer_graph_from_concurrent_delivery_is_not_a_false_failure():
    state,alerts=classify(metrics(required_revision=2,pending_events=0),{'project':3},MonitorPolicy())
    assert state=='healthy' and not alerts


def test_unreachable_graph_does_not_report_every_projection_as_deleted():
    state,alerts=classify(metrics(),{},MonitorPolicy(),graph_available=False)
    assert state=='critical' and [a['code'] for a in alerts]==['NEO4J_UNAVAILABLE']


def test_sql_fallback_rate_has_a_minimum_sample_and_window():
    m=metrics(required_revision=2,pending_events=0)
    m.update(recent_contexts=2,recent_sql_fallbacks=2)
    assert classify(deepcopy(m),{'project':2},MonitorPolicy())[0]=='healthy'
    m.update(recent_contexts=5,recent_sql_fallbacks=3)
    state,alerts=classify(m,{'project':2},MonitorPolicy())
    assert state=='warning' and alerts[0]['code']=='FREQUENT_SQL_FALLBACK'


def test_changes_log_once_and_recovery_logs_again():
    policy=MonitorPolicy();transitions=Transitions()
    critical=snapshot('critical',[{'code':'NEO4J_UNAVAILABLE','severity':'critical'}],policy,{},None)
    assert transitions.event(critical)['state']=='critical'
    critical['observed_at']=datetime.now(timezone.utc).isoformat()
    assert transitions.event(critical) is None
    healthy=snapshot('healthy',[],policy,{},None)
    assert transitions.event(healthy)['state']=='healthy'
    assert transitions.event(healthy) is None


def test_atomic_snapshot_and_staleness(tmp_path):
    now=datetime.now(timezone.utc);path=tmp_path/'monitor.json'
    value=snapshot('healthy',[],MonitorPolicy(),{},None);value['observed_at']=now.isoformat()
    save_snapshot(path,value)
    assert load_snapshot(path,now=now)==(value,True)
    assert load_snapshot(path,now=now+timedelta(seconds=61))[1] is False
    assert load_snapshot(path,now=now-timedelta(seconds=1))[1] is False
    assert list(tmp_path.glob('*.tmp'))==[]


@pytest.mark.parametrize('raw',['not json','[]','{"format":"wrong"}'])
def test_bad_snapshot_is_unknown_instead_of_healthy(tmp_path,raw):
    path=tmp_path/'state.json';path.write_text(raw)
    value,fresh=load_snapshot(path)
    assert not fresh and value['state']=='unknown'


@pytest.mark.parametrize('value',[0,-1,True,3601])
def test_monitor_policy_rejects_invalid_values(value):
    with pytest.raises(ValueError):MonitorPolicy(lag_seconds=value)


def test_postgres_failure_is_not_reported_as_zero_pending_work(monkeypatch):
    from backend.agent_framework import graph_monitor
    def unavailable(*a,**kw):raise ConnectionError('sensitive diagnostic must not be copied')
    monkeypatch.setattr(graph_monitor,'read_metrics',unavailable)
    result=observe(object(),object(),MonitorPolicy())
    assert result['state']=='critical' and result['metrics'] is None
    assert result['dependencies']['neo4j']=='not_checked'
    assert 'sensitive' not in str(result)
