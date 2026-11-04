import pytest
from fastapi.testclient import TestClient

from backend.agent_framework.api import create_app
from backend.agent_framework.adapters.synthetic import make_agents, sample_inputs
from backend.agent_framework.contracts import Approval

PREFIX = "/api/framework/v1"


def start_payload(run_id="demo-run-1", asset_version_id="poster-v1", parent_run_id=None):
    context, document, asset = sample_inputs(run_id, asset_version_id, parent_run_id)
    return {"context": context.model_dump(mode="json"), "document": document.model_dump(mode="json"),
            "asset": asset.model_dump(mode="json")}


def approval(context, decision, **kwargs):
    return {"context": context, "actor_id": context["actor_id"], "decision": decision, **kwargs}


def test_frontend_contract_full_flow_and_explicit_fixture_labels():
    with TestClient(create_app()) as client:
        health = client.get(PREFIX + "/health")
        assert health.json()["production_ready"] is False
        assert health.json()["default_adapters"] == "synthetic"
        assert health.headers["X-Creative-Checker-Profile"] == "development"
        payload = start_payload()
        run = PREFIX + "/runs/demo-run-1"
        response = client.post(PREFIX + "/runs", json=payload)
        assert response.status_code == 201, response.text
        assert response.json()["execution_status"] == "waiting_criteria"
        assert response.json()["synthetic"] is True
        assert client.post(PREFIX + "/runs", json=payload).status_code == 409
        response = client.post(run + "/approvals", json=approval(payload["context"], "approve_criteria"))
        assert response.status_code == 200, response.text
        assert response.json()["machine_verdict"] == "pass"
        assert response.json()["human_decision"] == "unreviewed"
        response = client.post(run + "/approvals", json=approval(payload["context"], "approve_changes", reason="test revision"))
        assert response.json()["execution_status"] == "waiting_revision"
        revised = start_payload("demo-run-2", "poster-v2", "demo-run-1")
        response = client.post(run + "/revisions", json={"context": revised["context"], "asset": revised["asset"]})
        assert response.status_code == 201, response.text
        assert response.json()["approvals"] == []
        run2 = PREFIX + "/runs/demo-run-2"
        assert client.post(run2 + "/approvals", json=approval(revised["context"], "approve_final")).status_code == 409
        response = client.post(run2 + "/approvals", json=approval(revised["context"], "approve_criteria"))
        assert response.json()["execution_status"] == "waiting_review"
        response = client.post(run2 + "/approvals", json=approval(revised["context"], "approve_final"))
        assert response.json()["execution_status"] == "completed"
        assert client.get(run2).json()["human_decision"] == "final_approved"
        assert client.get(run).json()["human_decision"] == "changes_approved"


def test_rejects_foreign_context_schema_drift_and_early_approval():
    with TestClient(create_app()) as client:
        payload = start_payload()
        client.post(PREFIX + "/runs", json=payload)
        path = PREFIX + "/runs/demo-run-1/approvals"
        assert client.post(path, json=approval(payload["context"], "approve_final")).status_code == 409
        bad = approval({**payload["context"], "project_id": "foreign"}, "approve_criteria")
        assert client.post(path, json=bad).status_code == 409
        payload["context"]["contract_version"] = "2.0.0"
        assert client.post(PREFIX + "/runs", json=payload).status_code == 422
        assert client.get(PREFIX + "/runs/missing").status_code == 404


def test_unconfigured_model_stays_failed_and_is_not_replaced_by_a_fixture():
    from dataclasses import replace
    from backend.agent_framework.adapters.templates import QwenAdapterStub

    agents = replace(make_agents(), extractor=QwenAdapterStub())
    with TestClient(create_app(agents)) as client:
        response = client.post(PREFIX + "/runs", json=start_payload())
        assert response.status_code == 201
        assert response.json()["execution_status"] == "failed"
        assert response.json()["error"]["code"] == "ADAPTER_NOT_CONFIGURED"
        assert response.json()["machine_verdict"] is None


def test_new_process_has_no_runs_and_production_profile_is_rejected():
    with TestClient(create_app()) as first:
        first.post(PREFIX + "/runs", json=start_payload())
    with TestClient(create_app()) as second:
        assert second.get(PREFIX + "/runs/demo-run-1").status_code == 404
    with pytest.raises(ValueError):
        create_app(profile="production")


def test_obvious_nonlocal_host_is_rejected():
    with TestClient(create_app(), base_url="http://public.example.com") as client:
        assert client.get(PREFIX + "/health").status_code == 403
