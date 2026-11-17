"""Additional-input protocol and failure diagnostics; fake model transport only."""
from copy import deepcopy
from hashlib import sha256
import json

import httpx
import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from backend.agent_framework.a02 import OpenAIFallbackProvider,QwenProvider,RegisteredPDFExtractor,prepare_sources
from backend.agent_framework.a02_configuration import PDFExtractionConfiguration
from backend.agent_framework.a02_semantic import semantic_schema,semantic_prompt
from backend.agent_framework.a02_source_selection import index_sources
from backend.agent_framework.contracts import CriteriaDraft,ExtractRequest
from backend.agent_framework.invocation import invoke_role
from backend.agent_framework.model_route import using_route
from backend.agent_framework.openai_fallback import FallbackSettings,strict_schema
from backend.agent_framework.ports import AdapterFailure
from tests.agent_framework.test_a02_semantic import requirement,text_document


def extract_addition(text,reply,*,route='runpod',allow=True):
    doc=text_document(text)
    settings=PDFExtractionConfiguration(mode='qwen',endpoint='https://qwen.invalid/v1/chat/completions',presentation=True)
    calls=[];raw=json.dumps(reply,ensure_ascii=False)
    def handler(request):
        body=json.loads(request.content);calls.append(body)
        return httpx.Response(200,json={'model':body['model'],
            'choices':[{'finish_reason':'stop','message':{'content':raw,'refusal':None}}]})
    transport=httpx.MockTransport(handler)
    provider=QwenProvider(settings,transport=transport)
    fallback=OpenAIFallbackProvider(settings,FallbackSettings(model='gpt-6-luna',api_key='sk-test'),transport=transport)
    request=ExtractRequest(context=doc.context,document=doc.reference,synthetic=True,
        allow_no_additional_requirements=allow)
    with using_route(route):
        draft=invoke_role('A02',RegisteredPDFExtractor(lambda _:doc,settings,provider=provider,fallback=fallback),request)
    return draft,calls,raw


@pytest.mark.parametrize('text',[
    '검수해줘',
    '요구사항에 맞는지 검수해줘',
    '요구사항 문서에 맞게 포스터가 제작되었는지 검수해줘',
    '김미정님이 올린 PDF 기준으로 검수해줘',
    '미정님이 올린 PDF 기준으로 검수해줘',
    '추가 조건은 없어요. 미정 항목도 없으니 PDF 기준대로 검수해줘',
    '미확정 없음. 기존 PDF 기준으로 검수해줘',
    '미확정 항목은 없습니다. 기존 PDF 기준으로 검수해줘',
    '확인 중인 항목도 없어요. 기존 PDF 기준으로 검수해줘',
])
@pytest.mark.parametrize('route',['runpod','openai'])
def test_request_only_addition_keeps_explicit_empty_result_and_original_source(text,route,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','sk-test')
    draft,calls,raw=extract_addition(text,{'outcome':'no_new_requirements','requirements':[]},route=route)
    assert draft.criteria==() and draft.extraction.outcome=='no_new_requirements'
    assert draft.extraction.sources[0].text==text
    assert draft.extraction.response_sha256==sha256(raw.encode()).hexdigest()
    assert draft.extraction.prompt_sha256==sha256(semantic_prompt(presentation=True,addition=True).encode()).hexdigest()
    assert len(calls)==1 and 'no_new_requirements' in calls[0]['messages'][0]['content']
    assert 'no_new_requirements' in draft.warnings
    assert 'missing_values_require_client_clarification' not in draft.warnings
    assert Draft202012Validator(calls[0]['response_format']['json_schema']['schema']).is_valid(
        {'outcome':'no_new_requirements','requirements':[]})


def test_mixed_review_request_keeps_actual_new_condition():
    text='검수해주고 제목은 빨간색으로 해줘'
    item=requirement(1,'title_color','빨간색',target='poster_visual',kind='visual',operator='present',
        presentation={'label':'제목 색상','value':'빨간색'})
    draft,_,_=extract_addition(text,{'outcome':'requirements','requirements':[item]})
    assert draft.extraction.outcome=='requirements' and len(draft.criteria)==1
    assert draft.criteria[0].expected=='빨간색'


@pytest.mark.parametrize('text,field,target,kind',[
    ('제목 색상은 확인 중입니다.','title_color','poster_visual','visual'),
    ('행사 날짜는 미정입니다.','event_date','poster_text','text'),
    ('행사 날짜는 미확정입니다.','event_date','poster_text','text'),
    ('미정 항목은 없고 행사 날짜는 미정입니다.','event_date','poster_text','text'),
    ('행사 날짜는 미정이고 다른 조건은 없습니다.','event_date','poster_text','text'),
    ('검수해줘. 행사 날짜는 미정입니다.','event_date','poster_text','text'),
    ('행사 날짜는 추후 확정합니다.','event_date','poster_text','text'),
    ('주최 로고는 추후 회의에서 결정합니다.','logo','poster_visual','visual'),
    ('행사 장소는 나중에 안내합니다.','venue','poster_text','text'),
    ('행사 시간은 확정 예정입니다.','viewing_hours','poster_text','text'),
    ('행사 장소는 아직 정해지지 않았습니다.','venue','poster_text','text'),
    ('행사 날짜: TBD','event_date','poster_text','text'),
])
@pytest.mark.parametrize('route',['runpod','openai'])
def test_actual_pending_condition_is_preserved(text,field,target,kind,route,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','sk-test')
    item=requirement(1,field,'',target=target,kind=kind,operator='manual',unresolved=True,presentation=None)
    draft,_,_=extract_addition(text,{'outcome':'requirements','requirements':[item]},route=route)
    assert draft.extraction.unresolved_criterion_ids==('c001',)
    assert draft.criteria[0].expected=='' and draft.criteria[0].source.quote==text
    assert draft.criteria[0].details.field==field
    with pytest.raises(AdapterFailure) as error:
        extract_addition(text,{'outcome':'no_new_requirements','requirements':[]},route=route)
    assert error.value.code=='A02_NO_VALID_REQUIREMENTS'


@pytest.mark.parametrize('reply',[
    {'requirements':[]},
    {'outcome':'requirements','requirements':[]},
    {'outcome':'no_new_requirements','requirements':[requirement(1,'title','검수해줘')]},
])
def test_empty_or_contradictory_model_reply_is_not_no_new_requirements(reply):
    with pytest.raises(AdapterFailure) as error:extract_addition('검수해줘',reply)
    assert error.value.code=='A02_RESPONSE_INVALID'


def test_standalone_text_cannot_claim_no_additional_requirements():
    with pytest.raises(AdapterFailure) as error:
        extract_addition('검수해줘',{'outcome':'no_new_requirements','requirements':[]},allow=False)
    assert error.value.code=='A02_RESPONSE_INVALID'
    doc=text_document('검수해줘')
    with pytest.raises(ValidationError):CriteriaDraft(context=doc.context,criteria=())


def test_all_rejected_candidates_keep_sources_fields_and_response_hash():
    text='제목 색상은 확인 중입니다.'
    item=requirement(1,'title_color','확인 중',target='poster_visual',kind='visual',operator='present',unresolved=True,
        presentation={'label':'제목 색상','value':'확인 중'})
    reply={'outcome':'requirements','requirements':[item]}
    with pytest.raises(AdapterFailure) as error:extract_addition(text,reply)
    failure=error.value
    assert failure.code=='A02_UNRESOLVED_VALUE_INVENTED'
    rejected,=failure.extraction_rejections
    assert rejected.candidate_index==1 and rejected.source.quote==text
    assert rejected.fields==('expected','operator','presentation')
    assert failure.response_sha256==sha256(json.dumps(reply,ensure_ascii=False).encode()).hexdigest()


@pytest.mark.parametrize('field,value',[
    ('expected','확인 중'),('operator','contains'),('presentation',{'label':'시간','value':'확인 중'}),
    ('unit','분'),('normalized_expected','2026-11-17'),
])
def test_generation_schema_rejects_contradictory_pending_fields(field,value):
    doc=text_document('관람시간은 확인 중입니다.')
    settings=PDFExtractionConfiguration(mode='mock')
    index,_=index_sources(prepare_sources(doc,settings))
    schema=strict_schema(semantic_schema(index,presentation=True,addition=True))
    validator=Draft202012Validator(schema)
    item=requirement(1,'viewing_hours','',operator='manual',unresolved=True,presentation=None)
    reply={'outcome':'requirements','requirements':[item]}
    assert validator.is_valid(reply)
    bad=deepcopy(reply);bad['requirements'][0][field]=value
    assert not validator.is_valid(bad)


def test_legacy_request_and_trace_serialization_stays_unchanged():
    doc=text_document()
    request=ExtractRequest(context=doc.context,document=doc.reference)
    assert 'allow_no_additional_requirements' not in request.model_dump()
