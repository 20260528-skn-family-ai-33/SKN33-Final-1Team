"""Real sample PDF bytes + mocked protocol only; no live Qwen or database."""
from hashlib import sha256
import json
from pathlib import Path

import httpx
import pymupdf
import pytest

from backend.agent_framework.a02 import (RegisteredPDFExtractor,QwenProvider,MockProvider,prepare_sources)
from backend.agent_framework.a02_configuration import PDFExtractionConfiguration
from backend.agent_framework.adapters.synthetic import sample_inputs
from backend.agent_framework.composition import load_config,build_role
from backend.agent_framework.contracts import DocumentRef,ExtractRequest
from backend.agent_framework.files import VerifiedDocument
from backend.agent_framework.invocation import invoke_role,accept_role_output
from backend.agent_framework.ports import AdapterFailure

SAMPLES=Path(__file__).resolve().parents[2]/'docs/poster-baselines/2026-11-03/workflow/client-inputs'


def document(case='EXH-NEW004'):
    raw=next((SAMPLES/case).glob('*.pdf')).read_bytes()
    context,_,_=sample_inputs()
    context=context.model_copy(update={'asset_version_id':None})
    ref=DocumentRef(file_id='registered-pdf',document_version_id=context.document_version_id,
        sha256=sha256(raw).hexdigest(),page_count=2)
    return VerifiedDocument(context,ref,raw,2)


def request(doc):return ExtractRequest(context=doc.context,document=doc.reference)


def candidate(sources):
    source=sources[0]
    quote=next(line.strip() for line in source.text.splitlines() if '표기한다' in line)
    return {'text':quote,'kind':'text','operator':'contains','expected':quote[0],
        'required':True,'unit':None,'block_id':source.block_id,'page':source.page,'quote':quote,'unresolved':False}


def raw_reply(item):return json.dumps({'candidates':[item]},ensure_ascii=False).encode()


@pytest.mark.parametrize('case',('EXH-NEW004','CON-NEW043','FES-NEW072'))
def test_mock_uses_actual_pdf_only_with_explicit_unapproved_trace(case):
    doc=document(case);settings=PDFExtractionConfiguration(mode='mock')
    result=invoke_role('A02',RegisteredPDFExtractor(lambda _:doc,settings),request(doc))
    assert result.synthetic and result.extraction.mode=='mock'
    assert len(result.extraction.sources)==2 and result.extraction.unresolved_criterion_ids
    assert all(c.expected=='' and c.operator=='manual' and not c.human_added for c in result.criteria)
    for c in result.criteria:
        source=next(s for s in result.extraction.sources if s.block_id==c.source.block_id)
        assert c.source.quote in source.text and c.source.source_hash==sha256(c.source.quote.encode()).hexdigest()


@pytest.mark.parametrize('mutation,code',(
    ('quote','A02_SOURCE_MISMATCH'),('page','A02_SOURCE_MISMATCH'),('block','A02_SOURCE_MISMATCH'),
    ('text','A02_SOURCE_MISMATCH'),('expected','A02_EXPECTED_UNGROUNDED'),('unit','A02_UNIT_UNGROUNDED'),('actor','A02_RESPONSE_INVALID'),
    ('missing','A02_UNRESOLVED_VALUE_INVENTED')))
def test_rejects_fabricated_sources_values_and_model_owned_identity(mutation,code):
    doc=document();settings=PDFExtractionConfiguration(mode='mock');sources=prepare_sources(doc,settings)
    item=candidate(sources)
    if mutation=='quote':item.update(text='Fabricated quote',quote='Fabricated quote')
    if mutation=='page':item['page']=2
    if mutation=='block':item['block_id']='absent-block'
    if mutation=='text':item['text']='Uncited paraphrase'
    if mutation=='expected':item['expected']='not in PDF 999999'
    if mutation=='unit':item['unit']='invented-unit'
    if mutation=='actor':item['actor_id']='model-cannot-select-actor'
    if mutation=='missing':item['unresolved']=True
    adapter=RegisteredPDFExtractor(lambda _:doc,settings,provider=lambda _:raw_reply(item))
    with pytest.raises(AdapterFailure) as error:invoke_role('A02',adapter,request(doc))
    assert error.value.code==code


def test_undecided_source_cannot_be_filled_even_when_provider_claims_resolved():
    doc=document();settings=PDFExtractionConfiguration(mode='mock');sources=prepare_sources(doc,settings)
    item=candidate(sources);source=sources[1]
    quote=next(line.strip() for line in source.text.splitlines() if '확인 중' in line)
    item.update(text=quote,quote=quote,expected=quote[0],block_id=source.block_id,page=2)
    with pytest.raises(AdapterFailure,match='PDF extraction') as error:
        RegisteredPDFExtractor(lambda _:doc,settings,provider=lambda _:raw_reply(item)).extract(request(doc))
    assert error.value.code=='A02_UNRESOLVED_VALUE_INVENTED'


@pytest.mark.parametrize('raw', (b'{',b'{"candidates":[],"candidates":[]}',b'{"candidates":[]}',b'{"candidates":NaN}',b'x'*(256*1024+1)))
def test_invalid_or_oversized_json_never_becomes_a_draft(raw):
    doc=document();adapter=RegisteredPDFExtractor(lambda _:doc,PDFExtractionConfiguration(mode='mock'),provider=lambda _:raw)
    with pytest.raises(AdapterFailure):adapter.extract(request(doc))


def test_trace_tampering_is_rechecked_at_common_role_boundary():
    doc=document();adapter=RegisteredPDFExtractor(lambda _:doc,PDFExtractionConfiguration(mode='mock'))
    result=adapter.extract(request(doc));source=result.extraction.sources[0]
    bad=source.model_copy(update={'text':'tampered'})
    result=result.model_copy(update={'extraction':result.extraction.model_copy(update={'sources':(bad,result.extraction.sources[1])})})
    with pytest.raises(AdapterFailure) as error:accept_role_output('A02',request(doc),result)
    assert error.value.code=='EXTRACTION_SOURCE_HASH_MISMATCH'


@pytest.mark.parametrize('kind,code',(('blank','PDF_TEXT_UNAVAILABLE'),('image','PDF_IMAGE_PAGE_UNSUPPORTED'),('limit','PDF_TEXT_LIMIT_EXCEEDED'),('parser','PDF_PARSER_VERSION_MISMATCH')))
def test_unsupported_input_fails_before_provider(kind,code):
    doc=document();settings=PDFExtractionConfiguration(mode='mock');calls=[]
    if kind=='limit':settings=settings.model_copy(update={'max_text_chars':100})
    if kind=='parser':settings=settings.model_copy(update={'parser_version':'0.0.0'})
    if kind in ('blank','image'):
        with pymupdf.open() as pdf:
            page=pdf.new_page()
            if kind=='image':
                page.insert_text((30,40),'Partial text layer')
                page.insert_image(pymupdf.Rect(20,60,120,160),stream=b'P6\n1 1\n255\n\xff\x00\x00')
            raw=pdf.tobytes()
        ref=doc.reference.model_copy(update={'sha256':sha256(raw).hexdigest(),'page_count':1})
        doc=VerifiedDocument(doc.context,ref,raw,1)
    with pytest.raises(AdapterFailure) as error:
        RegisteredPDFExtractor(lambda _:doc,settings,provider=lambda _:calls.append(1)).extract(request(doc))
    assert error.value.code==code and not calls


def qwen_settings():return PDFExtractionConfiguration(mode='qwen',endpoint='https://qwen.invalid/v1/chat/completions')


def test_http_mock_transport_checks_one_live_protocol_response_without_real_model():
    doc=document();settings=qwen_settings();sources=prepare_sources(doc,settings);seen=[]
    def respond(req):
        seen.append(req);body=json.loads(req.content)
        assert req.headers['Authorization']=='Bearer test-key-not-real'
        assert body['model']=='Qwen/Qwen3.5-4B' and body['stream'] is False
        assert 'actor_id' not in body['messages'][1]['content']
        assert body['response_format']['json_schema']['schema']
        return httpx.Response(200,json={'model':settings.model,'choices':[{'finish_reason':'stop','message':{'content':raw_reply(candidate(sources)).decode()}}]})
    provider=QwenProvider(settings,api_key='test-key-not-real',transport=httpx.MockTransport(respond))
    result=invoke_role('A02',RegisteredPDFExtractor(lambda _:doc,settings,provider=provider),request(doc))
    assert len(seen)==1 and not result.synthetic and result.extraction.mode=='qwen'
    assert 'test-key-not-real' not in result.model_dump_json()


@pytest.mark.parametrize('failure,unknown,code',(
    ('timeout',True,'QWEN_OUTCOME_UNKNOWN'),('server',True,'QWEN_OUTCOME_UNKNOWN'),
    ('rate',False,'QWEN_HTTP_REJECTED'),('redirect',False,'QWEN_HTTP_REJECTED'),
    ('incomplete',False,'QWEN_OUTPUT_INCOMPLETE'),('badjson',False,'A02_RESPONSE_INVALID')))
def test_http_failure_never_retries_repairs_or_falls_back(failure,unknown,code):
    calls=[];settings=qwen_settings();doc=document()
    def respond(req):
        calls.append(1)
        if failure=='timeout':raise httpx.ReadTimeout('DO_NOT_LEAK_SECRET',request=req)
        if failure=='server':return httpx.Response(500,text='DO_NOT_LEAK_SECRET')
        if failure=='rate':return httpx.Response(429)
        if failure=='redirect':return httpx.Response(302,headers={'Location':'https://other.invalid'})
        if failure=='badjson':return httpx.Response(200,content=b'{')
        return httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'{}'}}]})
    provider=QwenProvider(settings,transport=httpx.MockTransport(respond))
    with pytest.raises(AdapterFailure) as error:
        RegisteredPDFExtractor(lambda _:doc,settings,provider=provider).extract(request(doc))
    assert len(calls)==1 and error.value.code==code and error.value.outcome_unknown is unknown
    assert 'DO_NOT_LEAK_SECRET' not in str(error.value)


def test_registered_role_cannot_be_built_without_project_storage_context():
    cfg=load_config('examples/agent_framework/team_config.pdf-mock.json')
    with pytest.raises(AdapterFailure) as error:build_role(cfg,'A02')
    assert error.value.code=='ADAPTER_CONTEXT_REQUIRED'


def test_legacy_json_does_not_gain_null_trace_or_configuration_fields():
    from backend.agent_framework.adapters.synthetic import make_agents
    cfg=load_config('examples/agent_framework/team_config.synthetic.json')
    assert 'pdf_extraction' not in cfg.model_dump(mode='json')
    context,ref,_=sample_inputs()
    draft=make_agents().extractor.extract(ExtractRequest(context=context,document=ref))
    assert 'extraction' not in draft.model_dump(mode='json')


@pytest.mark.parametrize('values',(
    {'mode':'mock','endpoint':'https://unexpected.invalid/v1/chat/completions'},
    {'mode':'qwen'},
    {'mode':'qwen','endpoint':'https://key:secret@qwen.invalid/v1/chat/completions'},
    {'mode':'qwen','endpoint':'https://qwen.invalid/v1/chat/completions?api_key=secret'},
    {'mode':'qwen','endpoint':'file:///tmp/model'},
    {'mode':'qwen','endpoint':'https://qwen.invalid/v1/chat/completions','max_text_chars':60001},
))
def test_model_mode_limits_and_endpoint_are_trusted_explicit_configuration(values):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):PDFExtractionConfiguration.model_validate(values)
