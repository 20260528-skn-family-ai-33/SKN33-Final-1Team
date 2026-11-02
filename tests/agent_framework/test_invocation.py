"""Standalone and workflow roles share the same defensive call boundary."""
from dataclasses import replace
import inspect
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from backend.agent_framework.adapters.generate_examples import role_examples
from backend.agent_framework.adapters.synthetic import make_agents, sample_inputs
from backend.agent_framework.contracts import (
    Approval, Context, CriteriaDraft, Criterion, DocumentRef, ExtractRequest,
)
from backend.agent_framework.invocation import InvocationFailure, ROLE_CONTRACTS, invoke_role
from backend.agent_framework.ports import AdapterFailure
from backend.agent_framework.runtime import Workflow


def returned(role, value):
    return SimpleNamespace(**{ROLE_CONTRACTS[role][1]: lambda _request: value})


@pytest.mark.parametrize("role", tuple(ROLE_CONTRACTS))
def test_shared_boundary_accepts_every_fixture_and_raw_response(role):
    request, expected = role_examples()[role]
    adapter = getattr(make_agents(), ROLE_CONTRACTS[role][0])
    assert invoke_role(role, adapter, request, require_synthetic=True) == expected
    assert invoke_role(role, returned(role, expected.model_dump()), request.model_dump()) == expected


def test_wrong_model_type_is_rejected_even_when_fields_are_identical():
    class OtherDraft(BaseModel):
        contract_version: str
        context: Context
        criteria: tuple[Criterion, ...]
        warnings: tuple[str, ...]
        synthetic: bool

    request, expected = role_examples()["A02"]
    misleading = OtherDraft(**expected.model_dump())
    with pytest.raises(InvocationFailure) as failure:
        invoke_role("A02", returned("A02", misleading), request)
    assert failure.value.code == "CONTRACT_INVALID"


@pytest.mark.parametrize("tamper", ("copy", "construct", "wrong_model"))
def test_invalid_requests_never_reach_adapter(tamper):
    request, _ = role_examples()["A02"]
    if tamper == "copy":
        request = request.model_copy(update={"document": request.document.model_copy(update={"page_count": 0})})
    elif tamper == "construct":
        request = ExtractRequest.model_construct(**{**request.model_dump(), "synthetic": "not-a-boolean"})
    else:
        request = DocumentRef(**request.document.model_dump())
    called = []
    with pytest.raises(InvocationFailure) as failure:
        invoke_role("A02", SimpleNamespace(extract=lambda value: called.append(value)), request)
    assert failure.value.code == "CONTRACT_INVALID"
    assert called == []


def test_nested_input_scope_is_checked_before_remote_work():
    request, result = role_examples()["A04_A05"]
    wrong = request.evidence.model_copy(update={"context": request.context.model_copy(update={"project_id": "other"})})
    called = []
    with pytest.raises(InvocationFailure) as failure:
        invoke_role("A04_A05", SimpleNamespace(inspect=lambda value: called.append(value) or result),
                    request.model_copy(update={"evidence": wrong}))
    assert failure.value.code == "CONTEXT_MISMATCH"
    assert not called


@pytest.mark.parametrize("role,code", (("A02", "DOCUMENT_VERSION_MISMATCH"),
    ("A03", "SOURCE_HASH_MISMATCH"), ("A06", "OBSERVATION_ASSET_MISMATCH"),
    ("A07", "CONTEXT_MISMATCH")))
def test_shared_input_policy_prevents_calls_for_inconsistent_prior_stage_data(role, code):
    request, result = role_examples()[role]
    if role == "A02":
        request = request.model_copy(update={"document": request.document.model_copy(
            update={"document_version_id": "another-document"})})
    elif role == "A03":
        criteria = (request.criteria[0].model_copy(update={"source": request.criteria[0].source.model_copy(
            update={"source_hash": "0" * 64})}),) + request.criteria[1:]
        request = request.model_copy(update={"criteria": criteria})
    elif role == "A06":
        first = request.inspection.observations[0]
        observations = (first.model_copy(update={"image": first.image.model_copy(
            update={"asset_hash": "0" * 64})}),) + request.inspection.observations[1:]
        request = request.model_copy(update={"inspection": request.inspection.model_copy(
            update={"observations": observations})})
    else:
        request = request.model_copy(update={"validated": request.validated.model_copy(
            update={"context": request.context.model_copy(update={"run_id": "another-run"})})})
    calls = []
    adapter = SimpleNamespace(**{ROLE_CONTRACTS[role][1]: lambda value: calls.append(value) or result})
    with pytest.raises(InvocationFailure) as failure:
        invoke_role(role, adapter, request)
    assert failure.value.code == code and not calls


def test_adapter_mutation_cannot_replace_scope_or_mutate_callers_input():
    request, expected = role_examples()["A02"]
    original = request.model_copy(deep=True)

    def attack(value):
        object.__setattr__(value.context, "project_id", "forged")
        return expected.model_copy(update={"context": value.context})

    with pytest.raises(InvocationFailure) as failure:
        invoke_role("A02", SimpleNamespace(extract=attack), request)
    assert failure.value.code == "CONTEXT_MISMATCH"
    assert request == original


def test_adapter_mutation_cannot_expand_the_document_page_boundary():
    request, expected = role_examples()["A02"]

    def attack(value):
        object.__setattr__(value.document, "page_count", 50)
        criteria = tuple(item.model_copy(update={"source": item.source.model_copy(update={"page": 50})})
                         for item in expected.criteria)
        return expected.model_copy(update={"criteria": criteria})

    with pytest.raises(InvocationFailure) as failure:
        invoke_role("A02", SimpleNamespace(extract=attack), request)
    assert failure.value.code == "SOURCE_PAGE_OUT_OF_RANGE"
    assert request.document.page_count == 1


@pytest.mark.parametrize("role,field", (("A04_A05", "evidence"), ("A06", "evidence"),
                                       ("A06", "inspection"), ("A07", "validated"), ("A07", "evidence")))
def test_nested_synthetic_provenance_cannot_be_removed_by_adapter(role, field):
    request, response = role_examples()[role]
    updates = {"synthetic": False}
    for name in ("evidence", "inspection", "validated"):
        if hasattr(request, name):
            updates[name] = getattr(request, name).model_copy(update={"synthetic": name == field})
    request = request.model_copy(update=updates)
    response = response.model_copy(update={"synthetic": False})
    result = invoke_role(role, returned(role, response), request)
    assert result.synthetic is True
    assert request.synthetic is False and response.synthetic is False


def test_explicit_synthetic_mode_requires_adapter_label_before_propagation():
    request, response = role_examples()["A02"]
    with pytest.raises(InvocationFailure) as failure:
        invoke_role("A02", returned("A02", response.model_copy(update={"synthetic": False})),
                    request, require_synthetic=True)
    assert failure.value.code == "SYNTHETIC_LABEL_MISSING"


def test_adapter_owned_result_cannot_mutate_the_returned_result():
    request, response = role_examples()["A02"]
    original_run_id = request.context.run_id
    result = invoke_role("A02", returned("A02", response), request)
    object.__setattr__(response.context, "run_id", "later-mutation")
    assert result.context.run_id == original_run_id
    assert result.context.run_id != response.context.run_id


def test_async_method_is_not_started_and_wrapped_coroutine_is_closed():
    request, response = role_examples()["A02"]
    calls = []

    async def async_extract(value):
        calls.append(value)
        return response

    with pytest.raises(InvocationFailure) as failure:
        invoke_role("A02", SimpleNamespace(extract=async_extract), request)
    assert failure.value.code == "ASYNC_ADAPTER_UNSUPPORTED"
    assert not calls
    coroutine = async_extract(request)
    with pytest.raises(InvocationFailure) as failure:
        invoke_role("A02", returned("A02", coroutine), request)
    assert failure.value.code == "ASYNC_ADAPTER_UNSUPPORTED"
    assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED
    assert not calls


def test_provider_failure_is_preserved_and_never_retried():
    request, _ = role_examples()["A02"]
    original = AdapterFailure("REMOTE_UNKNOWN", "Outcome unknown.", outcome_unknown=True)
    calls = []

    def fail(value):
        calls.append(value)
        raise original

    with pytest.raises(AdapterFailure) as failure:
        invoke_role("A02", SimpleNamespace(extract=fail), request)
    assert failure.value is original and len(calls) == 1


def test_runtime_uses_identical_scope_boundary_and_stops_without_fallback():
    agents = make_agents()
    context, document, asset = sample_inputs()
    call_count = []

    def forged(request):
        call_count.append(True)
        result = agents.retriever.retrieve(request)
        return result.model_copy(update={"context": request.context.model_copy(update={"run_id": "other"})})

    adapter = SimpleNamespace(retrieve=forged)
    request, _ = role_examples()["A03"]
    with pytest.raises(InvocationFailure) as failure:
        invoke_role("A03", adapter, request)
    assert failure.value.code == "CONTEXT_MISMATCH"
    workflow = Workflow(replace(agents, retriever=adapter))
    workflow.start(context, document, asset)
    result = workflow.approve(context.run_id,
        Approval(context=context, actor_id=context.actor_id, decision="approve_criteria"))
    assert result.execution_status == "failed" and result.error.code == "contract_violation"
    assert result.evidence is result.inspection is result.report is None
    assert call_count == [True, True]


def test_runtime_rejects_async_stage_without_executing_or_advancing_it():
    calls = []

    async def extract(request):
        calls.append(request)

    workflow = Workflow(replace(make_agents(), extractor=SimpleNamespace(extract=extract)))
    result = workflow.start(*sample_inputs())
    assert result.execution_status == "failed" and result.error.code == "contract_violation"
    assert result.criteria is None and result.stages == ("A01",)
    assert not calls
