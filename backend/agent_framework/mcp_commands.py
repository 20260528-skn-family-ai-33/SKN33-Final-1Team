"""`mcp-serve`: the framework MCP tools over PostgreSQL, on loopback only; approvals stay on HTTP."""
import json
import os

from backend.app.persistence.database import create_app_engine
from .composition import load_config
from .contracts import FrameworkError
from .graph_store import FrameworkGraph
from .postgres_commands import _ready, _values

DEFAULT_PORT = 8013


def postgres_mcp(args):
    from .mcp_evidence import PostgresEvidence
    from .mcp_postgres import McpStore, PostgresRuns
    from .mcp_server import create_mcp_app
    import uvicorn
    # The projector's profile: database plus optional CC_NEO4J_*; no web secrets, no model keys.
    _selected, settings = _values(args.env_file, 'framework-projection')
    # Same variable as the development MCP app; the env file never carries it.
    token = os.environ.get('AGENT_FRAMEWORK_MCP_TOKEN', '')
    if len(token) < 32:
        print(json.dumps({'error': FrameworkError(code='MCP_TOKEN_REQUIRED',
            message='Set AGENT_FRAMEWORK_MCP_TOKEN (32+ characters) in the process environment.').model_dump(mode='json')}))
        return 2
    # The worker only claims runs whose configuration hash matches its own config.
    configuration = load_config(args.config)
    engine = create_app_engine(settings)
    graph = FrameworkGraph()  # Without CC_NEO4J_* every read reports GRAPH_NOT_CONFIGURED; PostgreSQL answers.
    try:
        _ready(engine, settings)
        store = McpStore(engine, configuration)
        app = create_mcp_app(PostgresRuns(store), token=token, evidence=PostgresEvidence(store, graph))
        uvicorn.run(app, host='127.0.0.1', port=args.port, workers=1, reload=False)
        return 0
    finally:
        graph.close()
        engine.dispose()


def register(commands, port_type):
    command = commands.add_parser('mcp-serve', help='start the read/admission MCP tools over PostgreSQL on loopback; approvals are not exposed')
    command.add_argument('--env-file', required=True, help='explicit app-only env file')
    command.add_argument('--config', required=True, help='fixed trusted team adapter configuration')
    command.add_argument('--port', type=port_type, default=DEFAULT_PORT)
    command.set_defaults(handler=postgres_mcp)
