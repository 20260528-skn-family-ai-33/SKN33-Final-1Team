"""Persistent Docker configuration, read-only preflight and original preservation."""
import json
from pathlib import Path
import pytest
from scripts import framework_graph_runtime as runtime


def app():
    return {'DB_HOST':'localhost','DB_PORT':'5432','POSTGRES_DB':'example',
        'DB_APP_USER':'example_app','DB_APP_PASSWORD':'literal$(&value)=kept',
        'CSRF_SECRET':'web-only','A02_QWEN_API_KEY':'model-only'}


def test_projection_gets_only_database_and_graph_values():
    values=runtime.worker_values(app(),{'AF_NEO4J_PASSWORD':'graph-only'})
    assert values['DB_HOST']=='host.docker.internal'
    assert values['DB_APP_PASSWORD']=='literal$(&value)=kept'
    assert values['CC_NEO4J_URI']=='bolt://neo4j:7687'
    assert 'CSRF_SECRET' not in values and 'A02_QWEN_API_KEY' not in values


@pytest.mark.parametrize('key',['DB_OWNER_PASSWORD','POSTGRES_PASSWORD'])
def test_owner_credentials_are_rejected(key):
    with pytest.raises(runtime.RuntimeErrorCode,match='APP_CREDENTIALS_ONLY'):
        runtime.worker_values(app()|{key:'owner-only'},{'AF_NEO4J_PASSWORD':'graph-only'})


def test_mismatched_graph_password_is_not_silently_overridden():
    with pytest.raises(runtime.RuntimeErrorCode,match='GRAPH_PASSWORD_SETTINGS_DIFFER'):
        runtime.worker_values(app()|{'CC_NEO4J_PASSWORD':'different'},{'AF_NEO4J_PASSWORD':'graph-only'})


def test_prepare_preserves_originals_and_derived_settings_survive_function_exit(tmp_path):
    source=tmp_path/'원본.env';graph=tmp_path/'graph.env'
    source.write_text(''.join(k+'='+v+'\n' for k,v in app().items()))
    graph.write_text('AF_NEO4J_PASSWORD=literal$value#kept\n')
    before=(source.read_bytes(),graph.read_bytes())
    state=tmp_path/'한글 & $ team';folder=runtime.prepare(source,graph,'example:tag',state=state)
    assert (source.read_bytes(),graph.read_bytes())==before
    assert (folder/'worker.env').is_file()
    assert 'DB_APP_PASSWORD=literal$(&value)=kept' in (folder/'worker.env').read_text()
    assert "AF_NEO4J_PASSWORD='literal$value#kept'" in (folder/'compose.env').read_text()
    runtime.remember(folder,state=state);assert runtime.current(state=state)==folder
    assert runtime.prepare(source,graph,'example:tag',state=state)==folder
    assert runtime.prepare(source,graph,'example:new-tag',state=state)!=folder


def test_compose_always_selects_existing_development_project_and_has_no_delete_flag(tmp_path):
    command=runtime.compose_argv(tmp_path,'stop','projector','neo4j')
    assert command[command.index('--project-name')+1]=='creative-checker-agent-framework'
    assert '--volumes' not in command and 'down' not in command
    assert str(runtime.ROOT/'deploy/agent-framework/compose.neo4j.yaml') in command


def test_preflight_requires_af005_and_does_not_migrate(tmp_path,monkeypatch):
    seen=[]
    def command(argv):seen.append(argv);return json.dumps({'revision':'AF004'})
    monkeypatch.setattr(runtime,'command',command)
    with pytest.raises(runtime.RuntimeErrorCode,match='APPLY_AF005_EXPLICITLY_FIRST'):
        runtime.preflight(tmp_path,'example:tag')
    assert 'postgres-check' in seen[0] and 'migrate-postgres' not in seen[0]


def test_state_cannot_select_a_foreign_project_or_escape_directory(tmp_path):
    (tmp_path/'current.json').write_text(json.dumps({'version':1,'project':'foreign','folder':'../../outside'}))
    with pytest.raises(runtime.RuntimeErrorCode,match='RUNTIME_STATE_INVALID'):runtime.current(state=tmp_path)


def test_unavailable_monitor_metrics_are_unknown_instead_of_zero():
    summary=runtime.monitor_summary({'state':'critical','metrics':None,'alerts':[{'code':'POSTGRES_UNAVAILABLE'}]})
    assert summary['state']=='critical'
    assert summary['pending_events'] is None and summary['max_pending_seconds'] is None


def test_pending_revision_is_counted_even_if_outbox_record_is_missing():
    summary=runtime.monitor_summary({'state':'critical','alerts':[],
        'metrics':{'projects':[{'required_revision':3,'applied_revision':2,'pending_events':0,'oldest_pending_seconds':0}]}})
    assert summary['pending_projects']==1 and summary['pending_events']==0


def test_status_reads_all_services_and_repair_snapshot_without_enqueuing(tmp_path,monkeypatch,capsys):
    commands=[]
    def run(argv,**kwargs):
        commands.append(argv)
        if 'inspect' in argv:return json.dumps({'running':True,'health':'healthy','restart':'unless-stopped','count':0})
        if 'graph-monitor-status' in argv:return json.dumps({'state':'healthy','alerts':[],'metrics':{'projects':[]}})
        if 'graph-recovery-status' in argv:return json.dumps({'state':'healthy','automatic_repair':True,'actions':[],'issues':{},'candidates':{}})
        return 'test-container-id'
    monkeypatch.setattr(runtime,'command',run)
    assert runtime.status(tmp_path)==0
    value=json.loads(capsys.readouterr().out)
    assert {s['service'] for s in value['services']}=={'neo4j','projector','monitor','recovery'}
    assert value['recovery']['state']=='healthy'
    assert not any('graph-recovery' in argv or 'graph-rebuild' in argv or 'graph-init' in argv for argv in commands)
