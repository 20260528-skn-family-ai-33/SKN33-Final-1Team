"""Framework MCP tools over a real, restricted PostgreSQL 17 in a NEW test DB.

Run explicitly with -m db_integration and TEST_DB_ENV_FILE. The fixtures are the
PostgreSQL framework fixtures: a random creative_checker_test_* DB, a logged-in
owner and a project. MCP calls carry no session; they act as the run requester.
"""
import os,socket,subprocess,sys,time
from uuid import UUID,uuid4

import pytest
from sqlalchemy import select,insert,update

from tests.app.test_db_integration import database
from tests.app.test_v1_auth_integration import auth_database,email_user
from tests.agent_framework.test_postgres_integration import framework_database,runtime,approve,work
from tests.agent_framework.test_agents_mcp import TOKEN,mcp_server
from backend.app.services.business import table
from backend.agent_framework.adapters.synthetic import sample_inputs
from backend.agent_framework.mcp_postgres import McpStore,PostgresRuns
from backend.agent_framework.mcp_server import FrameworkTools,McpToolClient,overview
from backend.agent_framework.postgres import events,runs,payload

pytestmark=pytest.mark.db_integration


def backend(r):
    return PostgresRuns(McpStore(r.db.engine,r.configuration))


def run_input(r,context=None):
    return {'context':payload(context or r.context),'document':payload(r.document),'asset':payload(r.asset)}


def origins(r,run_id):
    with r.owner.begin() as c:
        rows=c.execute(select(events).where(events.c.run_id==run_id,events.c.kind=='run.created')).mappings().all()
    return [row['metadata_json'].get('origin') for row in rows]


def test_mcp_tools_drive_a_run_as_its_requester(runtime):
    r=runtime;tools=FrameworkTools(backend(r))
    first=tools.call('start_run',run_input(r))
    assert first['state']=='completed' and first['result']['execution_status']=='queued'
    assert tools.call('start_run',run_input(r))==first  # same fixed run is idempotent
    assert origins(r,r.context.run_id)==['mcp']
    work(r)  # The worker claims an MCP-created run: same configuration and code hashes.
    # Session-free tool reads equal the logged-in store reads.
    assert tools.call('get_run',{'run_id':r.context.run_id})['result']==overview(r.store.get(r.subject,r.context.run_id))
    assert (tools.call('get_steps',{'run_id':r.context.run_id})['result']
            ==r.store.get_steps(r.subject,r.context.run_id).model_dump(mode='json'))
    assert approve(r,'approve_criteria').status_code==200
    work(r)
    assert approve(r,'approve_changes',reason='Fix the poster').status_code==200
    history=tools.call('get_approval_history',{'run_id':r.context.run_id})['result']
    assert [item['decision'] for item in history]==['approve_criteria','approve_changes']
    revised,_,asset=sample_inputs('mcp-child-'+uuid4().hex,'poster-v2',r.context.run_id)
    revised=revised.model_copy(update={'project_id':r.context.project_id,'actor_id':r.context.actor_id})
    request=lambda context:{'parent_run_id':r.context.run_id,'context':payload(context),'asset':payload(asset)}
    foreign=tools.call('request_rereview',request(revised.model_copy(update={'actor_id':str(uuid4())})))
    assert foreign['error']['code']=='FRAMEWORK_ACTOR_MISMATCH'
    child=tools.call('request_rereview',request(revised))
    assert child['state']=='completed' and child['result']['parent_run_id']==r.context.run_id
    assert tools.call('request_rereview',request(revised))==child
    assert origins(r,revised.run_id)==['mcp']


def test_mcp_tools_recheck_current_membership(runtime):
    r=runtime;tools=FrameworkTools(backend(r))
    assert tools.call('start_run',run_input(r))['state']=='completed'
    assert tools.call('get_run',{'run_id':'missing-'+uuid4().hex})['error']['code']=='RUN_NOT_FOUND'
    stranger=r.context.model_copy(update={'run_id':'mcp-'+uuid4().hex,'actor_id':str(uuid4())})
    assert tools.call('start_run',run_input(r,stranger))['error']['code']=='FRAMEWORK_ACCESS_REVOKED'
    replacement=email_user(r.auth_database)
    with r.owner.begin() as c:
        c.execute(insert(table('project_members')).values(project_id=UUID(r.context.project_id),
            user_id=UUID(replacement['id']),role='owner',is_active=True))
        c.execute(update(table('project_members')).where(table('project_members').c.project_id==UUID(r.context.project_id),
            table('project_members').c.user_id==UUID(r.context.actor_id)).values(role='viewer'))
    assert tools.call('get_run',{'run_id':r.context.run_id})['error']['code']=='FRAMEWORK_ACCESS_REVOKED'
    later=r.context.model_copy(update={'run_id':'mcp-'+uuid4().hex})
    assert tools.call('start_run',run_input(r,later))['error']['code']=='FRAMEWORK_ACCESS_REVOKED'
    with r.owner.begin() as c:
        assert c.execute(select(runs).where(runs.c.run_id.in_((stranger.run_id,later.run_id)))).first() is None


def test_mcp_client_uses_postgres_tools_over_http(runtime):
    r=runtime
    with mcp_server(backend(r)) as url:
        client=McpToolClient(url,TOKEN,timeout=10)
        started=client.call('start_run',run_input(r))
        assert started['state']=='completed'
        assert client.call('get_run',{'run_id':r.context.run_id})['result']==started['result']
        assert client.call('get_run',{'run_id':'missing-'+uuid4().hex})['error']['code']=='RUN_NOT_FOUND'


def test_separate_mcp_serve_process_uses_the_app_only_env(runtime,tmp_path):
    r=runtime;settings=r.db.settings.database
    env=tmp_path/'mcp.env'
    env.write_text('\n'.join(['PERSISTENCE_MODE=postgres','APP_ENV=test','DB_HOST='+settings.host,
        'DB_PORT='+str(settings.port),'POSTGRES_DB='+settings.database,'DB_APP_USER='+settings.username,
        'DB_APP_PASSWORD='+settings.password.reveal()])+'\n',encoding='utf-8');env.chmod(0o600)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    log=(tmp_path/'mcp.log').open('w',encoding='utf-8')
    process=subprocess.Popen([sys.executable,'-m','backend.agent_framework','mcp-serve','--env-file',str(env),
        '--config','examples/agent_framework/team_config.synthetic.json','--port',str(port)],
        env={**os.environ,'AGENT_FRAMEWORK_MCP_TOKEN':TOKEN},stdout=log,stderr=subprocess.STDOUT)
    try:
        client=McpToolClient(f'http://127.0.0.1:{port}/mcp',TOKEN,timeout=10)
        deadline=time.monotonic()+30
        while True:  # Poll with a read only; a write is never retried.
            try:
                assert client.call('get_run',{'run_id':'missing-'+uuid4().hex})['error']['code']=='RUN_NOT_FOUND';break
            except Exception:
                if process.poll() is not None or time.monotonic()>deadline:
                    log.close();pytest.fail((tmp_path/'mcp.log').read_text(encoding='utf-8'))
                time.sleep(0.2)
        started=client.call('start_run',run_input(r))
        assert started['state']=='completed' and started['result']['execution_status']=='queued'
        assert origins(r,r.context.run_id)==['mcp']
    finally:
        process.terminate();process.wait(10);log.close()
