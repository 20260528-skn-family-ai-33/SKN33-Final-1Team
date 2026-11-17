"""No-addition results and safe failure diagnostics across the worker boundary."""
from datetime import datetime, timezone
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.agent_framework.a02 import PROMPT_HASH
from backend.agent_framework.contracts import (
    CriteriaDraft, ExtractRequest, ExtractionRejection, ExtractionTrace, PDFTextSource, SourceRef,
)
from backend.agent_framework.criteria_contracts import CriteriaAddition, CriteriaVersionRequest
from backend.agent_framework.criteria_review import revise_criteria
from backend.agent_framework.durable_worker import stage_request
from backend.agent_framework.invocation import InvocationFailure, accept_role_output
from backend.agent_framework.ports import AdapterFailure
from backend.agent_framework.postgres import digest
from backend.agent_framework.runtime import Workflow
from tests.agent_framework.test_a02_semantic import text_document
from tests.agent_framework.test_criteria_review import draft


def no_addition_result(request, text='요구사항에 맞는지 검수해줘'):
    source = PDFTextSource(page=1, block_id='input-text', text=text,
        text_sha256=sha256(text.encode()).hexdigest())
    trace = ExtractionTrace(mode='mock', model='mock-line-fixture-v1', parser='utf8-source-v1',
        policy='a02-text-v1', input_kind='free_text', document_sha256=request.document.sha256,
        prompt_sha256=PROMPT_HASH, response_sha256=sha256(b'no-new-requirements').hexdigest(),
        sources=(source,), outcome='no_new_requirements')
    return CriteriaDraft(context=request.context, criteria=(), synthetic=True, extraction=trace)


def rejection(document_version_id, text='공연 시간은 추후 확정한다.'):
    return ExtractionRejection(candidate_index=1, code='A02_UNRESOLVED_VALUE_INVENTED',
        fields=('presentation',), source=SourceRef(document_version_id=document_version_id,
            page=1, block_id='input-text', quote=text, source_hash=sha256(text.encode()).hexdigest()))


@pytest.mark.parametrize('addition,media_type,allowed', (
    (None, 'text/plain', False), ({}, 'text/plain', True), ({}, 'application/pdf', False),
))
def test_only_recorded_text_addition_enables_empty_result(addition, media_type, allowed):
    document = text_document('요구사항에 맞는지 검수해줘')
    fixed = {'document':document.reference.model_copy(update={'media_type':media_type}).model_dump()}
    if addition is not None:
        fixed['criteria_addition'] = addition
    snapshot = SimpleNamespace(context=document.context, synthetic=True)
    request = stage_request(snapshot, fixed, 'A02')
    assert request.allow_no_additional_requirements is allowed
    if not allowed:
        assert 'allow_no_additional_requirements' not in request.model_dump()


def test_empty_result_is_valid_only_for_explicit_addition_request():
    document = text_document('요구사항에 맞는지 검수해줘')
    request = ExtractRequest(context=document.context, document=document.reference)
    result = no_addition_result(request)
    with pytest.raises(InvocationFailure) as error:
        accept_role_output('A02', request, result)
    assert error.value.code == 'A02_NO_VALID_REQUIREMENTS'
    addition = request.model_copy(update={'allow_no_additional_requirements':True})
    assert accept_role_output('A02', addition, result) == result
    bad = addition.model_copy(update={'document':document.reference.model_copy(update={'media_type':'application/pdf'})})
    with pytest.raises(InvocationFailure) as error:
        accept_role_output('A02', bad, result)
    assert error.value.code == 'CONTRACT_INVALID'


@pytest.mark.parametrize('select_one', (False, True))
def test_no_addition_revision_preserves_existing_criteria_and_selected_exclusions(select_one):
    original = draft()
    before = original.model_dump_json()
    document = text_document('요구사항에 맞는지 검수해줘')
    request = ExtractRequest(context=document.context, document=document.reference,
        allow_no_additional_requirements=True)
    added = no_addition_result(request)
    ids = (original.criteria[0].criterion_id,) if select_one else None
    admission = CriteriaAddition(source_run_id=original.context.run_id, request_key=uuid4(),
        parent_version_id=None, expected_base_sha256=digest(original), criterion_ids=ids)
    command = CriteriaVersionRequest(parent_version_id=None, expected_base_sha256=digest(original),
        purpose='human_review')
    result = revise_criteria(original, None, command, version_id=uuid4(), created_by=original.context.actor_id,
        created_at=datetime.now(timezone.utc), addition=(admission, added))
    assert original.model_dump_json() == before
    assert result.criteria == (original.criteria[:1] if select_one else original.criteria)
    assert result.addition_run_ids == (added.context.run_id,)
    assert len(result.exclusions) == (len(original.criteria)-1 if select_one else 0)
    assert not any(change.action == 'add' for change in result.changes)


def test_safe_error_channel_preserves_failed_item_and_hash_without_raw_messages():
    detail = rejection('document-1')
    error = AdapterFailure('A02_UNRESOLVED_VALUE_INVENTED', 'never expose raw response or secret',
        extraction_rejections=(detail,), response_sha256='f'*64)
    saved = Workflow._step_error(error)
    assert saved.extraction_rejections == (detail,)
    assert saved.response_sha256 == 'f'*64
    assert 'secret' not in saved.model_dump_json()
    legacy = Workflow._step_error(AdapterFailure('A02_RESPONSE_INVALID', 'raw'))
    assert 'extraction_rejections' not in legacy.model_dump()
    assert 'response_sha256' not in legacy.model_dump()


def test_invalid_diagnostics_do_not_turn_error_reporting_into_another_failure():
    error = AdapterFailure('A02_RESPONSE_INVALID', 'raw')
    error.extraction_rejections = ({'candidate_index':1, 'code':'X', 'provider_raw':'secret'},)
    error.response_sha256 = 'invalid'
    saved = Workflow._step_error(error)
    assert saved.code == 'A02_RESPONSE_INVALID'
    assert not saved.extraction_rejections and saved.response_sha256 is None
    assert 'secret' not in saved.model_dump_json()
