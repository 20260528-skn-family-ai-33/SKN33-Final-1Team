"""mcp-serve startup and tool receipts without a database or a model."""
import json
import os

import pytest

from backend.agent_framework import mcp_commands, mcp_server
from backend.agent_framework.adapters.synthetic import sample_inputs
from backend.agent_framework.cli import main
from backend.agent_framework.graph_store import FrameworkGraph
from backend.agent_framework.mcp_evidence import PostgresEvidence
from backend.agent_framework.mcp_postgres import McpStore, PostgresRuns
from backend.agent_framework.mcp_server import FrameworkTools, ToolRefusal

CONFIG = 'examples/agent_framework/team_config.synthetic.json'
APP_ENV = ('PERSISTENCE_MODE=postgres\nAPP_ENV=test\nDB_HOST=127.0.0.1\nDB_PORT=5432\nPOSTGRES_DB=creative_checker\n'
           'DB_APP_USER=creative_checker_app\nDB_APP_PASSWORD=unit-only-secret\n')


@pytest.fixture
def env_file(tmp_path):
    # activate_runtime rewrites managed process variables; restore them after each run.
    saved = dict(os.environ)
    path = tmp_path / 'app.env'
    path.write_text(APP_ENV, encoding='utf-8')
    yield path
    os.environ.clear()
    os.environ.update(saved)


class Engine:
    disposed = False

    def dispose(self):
        self.disposed = True


@pytest.fixture
def started(monkeypatch):
    """Everything mcp-serve hands to the MCP app; no database, no server."""
    import uvicorn
    seen = {'engine': Engine()}
    monkeypatch.setattr(mcp_commands, 'create_app_engine', lambda settings: seen['engine'])
    monkeypatch.setattr(mcp_commands, '_ready', lambda engine, settings: seen.update(ready=True))
    monkeypatch.setattr(mcp_server, 'create_mcp_app', lambda backend, *, token, evidence:
                        seen.update(backend=backend, token=token, evidence=evidence) or 'app')
    monkeypatch.setattr(uvicorn, 'run', lambda app, **options: seen.update(app=app, options=options))
    monkeypatch.setenv('AGENT_FRAMEWORK_MCP_TOKEN', 't' * 32)
    return seen


def test_mcp_serve_requires_its_arguments():
    with pytest.raises(SystemExit):
        main(['mcp-serve'])


@pytest.mark.parametrize('token', ['', 'short'])
def test_mcp_serve_requires_a_token_before_any_connection(env_file, capsys, monkeypatch, token):
    monkeypatch.setattr(mcp_commands, 'create_app_engine', lambda *_: pytest.fail('engine opened without a token'))
    monkeypatch.setenv('AGENT_FRAMEWORK_MCP_TOKEN', token)
    assert main(['mcp-serve', '--env-file', str(env_file), '--config', CONFIG]) == 2
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'MCP_TOKEN_REQUIRED'


def test_mcp_serve_binds_postgres_tools_on_loopback(env_file, started):
    assert main(['mcp-serve', '--env-file', str(env_file), '--config', CONFIG]) == 0
    backend, evidence = started['backend'], started['evidence']
    assert isinstance(backend, PostgresRuns) and isinstance(backend.store, McpStore)
    assert backend.store.engine is started['engine']
    # Evidence tools read through the same requester-rechecking store.
    assert isinstance(evidence, PostgresEvidence) and evidence.store is backend.store
    assert isinstance(evidence.graph, FrameworkGraph)
    assert started['token'] == 't' * 32 and started['app'] == 'app' and started['ready']
    assert started['options'] == {'host': '127.0.0.1', 'port': 8013, 'workers': 1, 'reload': False}
    assert started['engine'].disposed


def test_mcp_serve_uses_the_projector_settings_and_no_web_or_model_secret(env_file, started):
    env_file.write_text(APP_ENV + 'CC_NEO4J_URI=bolt://127.0.0.1:7687\nCC_NEO4J_PASSWORD=unit-only\n'
                        'CSRF_SECRET=web-only\nCC_QWEN_API_KEY=model-only\n', encoding='utf-8')
    assert main(['mcp-serve', '--env-file', str(env_file), '--config', CONFIG]) == 0
    assert started['evidence'].graph.uri == 'bolt://127.0.0.1:7687'
    assert 'CSRF_SECRET' not in os.environ and 'CC_QWEN_API_KEY' not in os.environ


def test_no_tool_can_write_the_graph_or_approve():
    # An exact list: a new tool has to be added here on purpose.
    assert set(FrameworkTools(object(), object()).names) == {
        'get_run', 'get_steps', 'get_approval_history', 'start_run', 'request_rereview',
        'get_approved_criteria', 'get_history', 'verify_sources', 'get_relations', 'get_change_impact'}


def test_tool_receipts_keep_backend_refusal_code_and_raise_other_errors():
    class Backend:
        def get(self, run_id):
            raise ToolRefusal('FRAMEWORK_ACCESS_REVOKED', 'Project execution access was revoked.')

        def rereview(self, parent_run_id, context, asset):
            raise ConnectionError('commit acknowledgement lost')

    tools = FrameworkTools(Backend())
    assert tools.call('get_run', {'run_id': 'any'}) == {'state': 'failed', 'error': {
        'code': 'FRAMEWORK_ACCESS_REVOKED', 'message': 'Project execution access was revoked.'}}
    child, _, asset = sample_inputs('demo-run-2', 'poster-v2', 'demo-run-1')
    # Not a refusal: the write may have been applied, so no failed receipt is invented.
    with pytest.raises(ConnectionError):
        tools.call('request_rereview', {'parent_run_id': 'demo-run-1', 'context': child.model_dump(mode='json'),
                                        'asset': asset.model_dump(mode='json')})
