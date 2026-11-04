"""Development MCP tools over the in-memory Workflow; approvals are never exposed.

Agents and MCP clients may read run state and submit admission/rereview commands.
Every state transition is still checked by Workflow. Human approval decisions
stay on the HTTP API, so a model can never approve criteria, changes or finals.
PostgresStore binding needs a service subject/authorization design and is follow-up.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
import hmac
import inspect
import json
import os
from typing import Any

import httpx
from pydantic import ValidationError

from .contracts import AssetRef, Context, DocumentRef, RunSnapshot
from .runtime import Workflow

TOOL_NAMES = ('get_run', 'get_steps', 'get_approval_history', 'start_run', 'request_rereview')


def overview(snapshot: RunSnapshot) -> dict:
    """Compact run view for model context: no source quotes, hashes or file bytes."""
    criteria = snapshot.criteria.criteria if snapshot.criteria else ()
    return {
        'run_id': snapshot.context.run_id, 'parent_run_id': snapshot.context.parent_run_id,
        'project_id': snapshot.context.project_id, 'asset_version_id': snapshot.context.asset_version_id,
        'execution_status': snapshot.execution_status, 'machine_verdict': snapshot.machine_verdict,
        'human_decision': snapshot.human_decision, 'synthetic': snapshot.synthetic,
        'criteria': [{'criterion_id': c.criterion_id, 'text': c.text, 'required': c.required} for c in criteria],
        'validated_items': [{'criterion_id': i.criterion_id, 'verdict': i.verdict, 'issues': list(i.issues)}
                            for i in (snapshot.validated.items if snapshot.validated else ())],
        'report': snapshot.report and {'summary': snapshot.report.summary,
                                       'change_requests': list(snapshot.report.change_requests)},
        'approvals': [a.decision for a in snapshot.approvals],
        'stages': list(snapshot.stages),
        'error': snapshot.error and {'code': snapshot.error.code, 'message': snapshot.error.message},
    }


def _failed(code: str, message: str) -> dict:
    return {'state': 'failed', 'error': {'code': code, 'message': message}}


class FrameworkTools:
    """Named tool capabilities; also usable in-process as a tool client."""

    def __init__(self, workflow: Workflow):
        self.workflow = workflow

    def get_run(self, run_id: str) -> dict:
        """Compact run state: status, verdicts, report and approval decisions."""
        return overview(self.workflow.get(run_id))

    def get_steps(self, run_id: str) -> dict:
        """Role step records (A02, A04_A05, A06, A07) without source text."""
        return self.workflow.get_steps(run_id).model_dump(mode='json')

    def get_approval_history(self, run_id: str) -> list:
        """Recorded human approval decisions for a run (read only)."""
        return [{'decision': a.decision, 'actor_id': a.actor_id, 'reason': a.reason,
                 'criterion_ids': list(a.criterion_ids)} for a in self.workflow.get(run_id).approvals]

    def start_run(self, context: dict, document: dict, asset: dict) -> dict:
        """Admit a new run; Workflow validates versions and run uniqueness."""
        return overview(self.workflow.start(Context.model_validate(context), DocumentRef.model_validate(document),
                                            AssetRef.model_validate(asset)))

    def request_rereview(self, parent_run_id: str, context: dict, asset: dict) -> dict:
        """Create a child run after an approved change request and a new asset version."""
        return overview(self.workflow.rereview(parent_run_id, Context.model_validate(context),
                                               AssetRef.model_validate(asset)))

    def call(self, name: str, arguments: dict) -> dict:
        """Receipt boundary shared by MCP and in-process callers."""
        if name not in TOOL_NAMES:
            return _failed('UNKNOWN_TOOL', 'This tool is not exposed.')
        try:
            return {'state': 'completed', 'result': getattr(self, name)(**arguments)}
        except TypeError:
            return _failed('INVALID_ARGUMENTS', 'Tool arguments do not match the tool signature.')
        except KeyError:
            return _failed('RUN_NOT_FOUND', 'The run does not exist in this runtime.')
        except ValidationError:
            return _failed('INVALID_INPUT', 'Tool input does not match the framework contract.')
        except ValueError as exc:
            # Workflow conflict messages are framework-authored, never provider text.
            return _failed('WORKFLOW_CONFLICT', str(exc))


class BearerMiddleware:
    def __init__(self, app, token: str):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            supplied = dict(scope.get('headers', [])).get(b'authorization', b'')
            if not hmac.compare_digest(supplied, b'Bearer ' + self.token.encode()):
                from starlette.responses import JSONResponse
                await JSONResponse({'error': 'unauthorized'}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _receipt_tool(tools: FrameworkTools, name: str):
    """MCP tool with the method's signature and docstring that returns a receipt."""
    method = getattr(tools, name)

    def tool(**arguments) -> dict:
        return tools.call(name, arguments)
    tool.__signature__ = inspect.signature(method).replace(return_annotation=dict)
    tool.__doc__ = method.__doc__
    return tool


def create_mcp_app(workflow: Workflow, *, token: str, host: str = '127.0.0.1'):
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    if not isinstance(token, str) or len(token) < 32:
        raise ValueError('MCP token must contain at least 32 characters')
    server = FastMCP('creative-checker-agent-framework', stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=[host, f'{host}:*', '127.0.0.1', '127.0.0.1:*',
                                                                     'localhost', 'localhost:*'], allowed_origins=[]))
    tools = FrameworkTools(workflow)
    for name in TOOL_NAMES:
        server.add_tool(_receipt_tool(tools, name), name=name)
    return BearerMiddleware(server.streamable_http_app(), token)


def create_development_app():
    """uvicorn factory: synthetic adapters, memory state, loopback only."""
    from .adapters.synthetic import make_agents
    return create_mcp_app(Workflow(make_agents()), token=os.environ.get('AGENT_FRAMEWORK_MCP_TOKEN', ''))


class McpToolError(RuntimeError):
    """The MCP server reported a tool exception or no result; a write's outcome is unknown."""


async def call_tool(url: str, token: str, name: str, arguments: dict, *, timeout: float = 60) -> dict:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with httpx.AsyncClient(headers={'Authorization': f'Bearer {token}'}, follow_redirects=False,
                                 timeout=httpx.Timeout(timeout, connect=10)) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=timeout)) as session:
                await session.initialize()
                response = await session.call_tool(name, arguments)
    # Interpret after the sessions close; raising inside them wraps the error in ExceptionGroups.
    # isError means the tool raised, possibly after a write was applied.
    if not response.isError:
        if response.structuredContent is not None:
            return response.structuredContent
        for content in response.content:
            if content.type == 'text':
                return json.loads(content.text)
    raise McpToolError('MCP tool raised or returned no result; the outcome is unknown.')


class McpToolClient:
    """Synchronous tool client for agents. Transport errors propagate to the agent loop."""

    def __init__(self, url: str, token: str, *, timeout: float = 60):
        self.url, self.token, self.timeout = url, token, timeout

    def call(self, name: str, arguments: dict[str, Any]) -> dict:
        try:
            return asyncio.run(call_tool(self.url, self.token, name, arguments, timeout=self.timeout))
        except BaseExceptionGroup as group:
            # anyio task groups wrap transport errors (e.g. a 401); surface a single cause.
            error: BaseException = group
            while isinstance(error, BaseExceptionGroup) and len(error.exceptions) == 1:
                error = error.exceptions[0]
            raise error if error is not group else group from None
