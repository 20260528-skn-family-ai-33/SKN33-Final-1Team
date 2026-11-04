"""Loopback-only development API contract. Never mounted into the product API.

This service has no authentication or durable storage. IDs supplied in requests
are test context, not proof of identity. Bind to 127.0.0.1, never a shared host.
"""
from fastapi import FastAPI, HTTPException

from . import CONTRACT_VERSION
from .composition import TeamConfiguration, build_agents, describe_agents, describe_configuration
from .contracts import Approval, AssetRef, Context, Contract, DocumentRef, RunSnapshot, StepTimeline
from .ports import Agents
from .runtime import Workflow


class StartRequest(Contract):
    context: Context
    document: DocumentRef
    asset: AssetRef


class RevisionRequest(Contract):
    context: Context
    asset: AssetRef


def create_app(agents: Agents | None = None, *, profile: str | None = None,
               configuration: TeamConfiguration | None = None) -> FastAPI:
    if configuration is not None:
        if agents is not None:
            raise ValueError("Choose explicit agents or a team configuration, not both")
        configuration = TeamConfiguration.model_validate(configuration.model_dump(warnings=False))
        if profile is not None and profile != configuration.profile:
            raise ValueError("profile differs from the team configuration")
        profile = configuration.profile
        agents = build_agents(configuration)
    profile = "development" if profile is None else profile
    if agents is None:
        from .adapters.synthetic import make_agents
        agents = make_agents()
    workflow = Workflow(agents, profile=profile)
    app = FastAPI(title="Creative Checker — Development Framework",
                  version=CONTRACT_VERSION,
                  description="Development only. In-memory state, synthetic default adapters, no authentication. "
                              "Not the front-v2/M008 production API. Bind to loopback only.")
    app.state.workflow = workflow

    def adapter_status():
        roles = describe_agents(agents)
        kinds = {details["kind"] for details in roles.values()} - {"reference"}
        aggregate = next(iter(kinds)) if len(kinds) == 1 else "mixed"
        return {"default_adapters": aggregate, "roles": roles, "connection_verified": False}

    @app.middleware("http")
    async def development_boundary(request, call_next):
        # Host headers are not authentication; reject obvious accidental exposure.
        from starlette.responses import JSONResponse
        host = request.url.hostname
        if host not in {"127.0.0.1", "localhost", "::1", "testserver"}:
            return JSONResponse(status_code=403, content={"code": "DEVELOPMENT_LOOPBACK_ONLY"})
        response = await call_next(request)
        response.headers["X-Creative-Checker-Profile"] = profile
        response.headers["Cache-Control"] = "no-store"
        return response

    def invoke(action, *args):
        try:
            return action(*args)
        except KeyError as exc:
            raise HTTPException(404, detail={"code": "RUN_NOT_FOUND"}) from exc
        except ValueError as exc:
            raise HTTPException(409, detail={"code": "WORKFLOW_CONFLICT", "message": str(exc)}) from exc

    prefix = "/api/framework/v1"

    @app.get(prefix + "/health")
    def health():
        return {"contract_version": CONTRACT_VERSION, "profile": profile, "storage": "memory",
                "authenticated": False, "production_ready": False,
                **adapter_status()}

    @app.get(prefix + "/configuration")
    def configuration_status():
        return {"profile": profile, "storage": "memory", "production_ready": False,
                "declared": describe_configuration(configuration) if configuration is not None else None,
                "initialized": adapter_status(),
                "notice": "Configuration declarations and adapter type classification only; no model/database connectivity verification."}

    @app.post(prefix + "/runs", response_model=RunSnapshot, status_code=201)
    def start(request: StartRequest):
        return invoke(workflow.start, request.context, request.document, request.asset)

    @app.get(prefix + "/runs/{run_id}", response_model=RunSnapshot)
    def read(run_id: str):
        return invoke(workflow.get, run_id)

    @app.get(prefix + "/runs/{run_id}/steps", response_model=StepTimeline)
    def steps(run_id: str):
        return invoke(workflow.get_steps, run_id)

    @app.post(prefix + "/runs/{run_id}/approvals", response_model=RunSnapshot)
    def approve(run_id: str, request: Approval):
        return invoke(workflow.approve, run_id, request)

    @app.post(prefix + "/runs/{run_id}/revisions", response_model=RunSnapshot, status_code=201)
    def revise(run_id: str, request: RevisionRequest):
        return invoke(workflow.rereview, run_id, request.context, request.asset)

    return app
