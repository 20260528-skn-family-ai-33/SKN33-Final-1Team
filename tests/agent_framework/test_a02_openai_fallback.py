"""A02 OpenAI 대체 추출(2026-11-12) — 가짜 HTTP만 쓰며 실제 모델 결과가 아니다."""
from hashlib import sha256
import json

import httpx
import pytest

from backend.agent_framework.a02 import OpenAIFallbackProvider,QwenProvider,RegisteredPDFExtractor
from backend.agent_framework.a02_configuration import PDFExtractionConfiguration
from backend.agent_framework.contracts import ExtractRequest
from backend.agent_framework.invocation import invoke_role
from backend.agent_framework.openai_fallback import (WARNING,FallbackSettings,fallback_settings,should_fall_back,
    strict_schema)
from backend.agent_framework.ports import AdapterFailure
from tests.agent_framework.test_a02_semantic import model_reply,text_document

FALLBACK=FallbackSettings(model='gpt-6-luna',api_key='sk-test')


def qwen(status=500,content=None):
    settings=PDFExtractionConfiguration(mode='qwen',endpoint='https://qwen.invalid/v1/chat/completions')
    def handler(request):
        if content is None:return httpx.Response(status)
        return httpx.Response(200,json={'model':settings.model,'choices':[{'finish_reason':'stop','message':{'content':content}}]})
    return settings,QwenProvider(settings,transport=httpx.MockTransport(handler))


def openai(calls,reply=None,*,status=200,finish='stop',model='gpt-6-luna'):
    content=json.dumps(model_reply() if reply is None else reply,ensure_ascii=False)
    def handler(request):
        calls.append(json.loads(request.content))
        if status!=200:return httpx.Response(status,json={'error':{'code':'x'}})
        return httpx.Response(200,json={'model':model,'choices':[{'finish_reason':finish,'message':{'content':content,'refusal':None}}]})
    return httpx.MockTransport(handler)


def run(settings,provider,fallback):
    doc=text_document()
    request=ExtractRequest(context=doc.context,document=doc.reference,synthetic=True)
    return invoke_role('A02',RegisteredPDFExtractor(lambda _:doc,settings,provider=provider,fallback=fallback),request)


def test_qwen_failure_calls_openai_once_and_keeps_source_validation():
    settings,provider=qwen(500);calls=[]
    draft=run(settings,provider,OpenAIFallbackProvider(settings,FALLBACK,transport=openai(calls)))
    assert len(calls)==1 and draft.extraction.mode=='openai' and draft.extraction.model=='gpt-6-luna'
    assert WARNING in draft.warnings and 'qwen_failed_qwen_outcome_unknown' in draft.warnings
    assert {c.expected for c in draft.criteria}>={'김천김밥축제','직지문화공원'}
    body=calls[0]
    assert 'chat_template_kwargs' not in body and 'max_tokens' not in body and 'temperature' not in body
    assert body['model']=='gpt-6-luna' and body['reasoning_effort']=='low' and body['max_completion_tokens']>=16384
    schema=body['response_format']['json_schema']
    assert schema['strict'] is True
    def check(node):
        if isinstance(node,dict):
            if isinstance(node.get('properties'),dict):assert set(node['required'])==set(node['properties'])
            for child in node.values():check(child)
        elif isinstance(node,list):
            for child in node:check(child)
    check(schema['schema'])


def test_invalid_qwen_answer_also_falls_back():
    settings,provider=qwen(content='{"requirements": "not a list"}');calls=[]
    draft=run(settings,provider,OpenAIFallbackProvider(settings,FALLBACK,transport=openai(calls)))
    assert len(calls)==1 and 'qwen_failed_a02_response_invalid' in draft.warnings


def test_openai_answer_is_rejected_by_the_same_source_check():
    settings,provider=qwen(500);calls=[]
    reply=model_reply();reply['requirements'][0]['expected']='원문에 없는 축제'
    draft=run(settings,provider,OpenAIFallbackProvider(settings,FALLBACK,transport=openai(calls,reply)))
    # Qwen 결과와 같은 규칙: 원문에 없는 값은 기준이 되지 못하고 거부 후보로 남는다.
    assert len(calls)==1 and '원문에 없는 축제' not in {c.expected for c in draft.criteria}
    assert len(draft.extraction.rejected_candidates)==1


@pytest.mark.parametrize('changes,code',[({'status':401},'OPENAI_HTTP_REJECTED'),({'finish':'length'},'OPENAI_OUTPUT_INCOMPLETE'),
    ({'model':'gpt-4o'},'OPENAI_MODEL_MISMATCH')])
def test_failed_fallback_reports_the_openai_code(changes,code):
    settings,provider=qwen(500);calls=[]
    with pytest.raises(AdapterFailure) as error:
        run(settings,provider,OpenAIFallbackProvider(settings,FALLBACK,transport=openai(calls,**changes)))
    assert error.value.code==code and 'QWEN_OUTCOME_UNKNOWN' in error.value.message


def test_without_fallback_the_qwen_failure_is_unchanged():
    settings,provider=qwen(500)
    with pytest.raises(AdapterFailure) as error:run(settings,provider,None)
    assert error.value.code=='QWEN_OUTCOME_UNKNOWN'


def test_settings_switch_key_order_and_document_errors():
    assert fallback_settings({})is None
    assert fallback_settings({'OPENAI_API_KEY':'sk-a','A02_OPENAI_FALLBACK':'0'}) is None
    chosen=fallback_settings({'creative_checker_openapi':'sk-c','OPENAI_API_KEY':'sk-b','A02_OPENAI_REASONING_EFFORT':'bogus'})
    assert chosen.api_key=='sk-b' and chosen.model=='gpt-6-luna' and chosen.reasoning_effort=='low'
    assert 'sk-b' not in repr(chosen)
    assert fallback_settings({'A02_OPENAI_API_KEY':'sk-a','OPENAI_API_KEY':'sk-b','A02_OPENAI_MODEL':'gpt-5.6-luna'}).model=='gpt-5.6-luna'
    assert not should_fall_back('PDF_TEXT_UNAVAILABLE') and not should_fall_back('DOCUMENT_TOO_LARGE')
    assert should_fall_back('QWEN_UNAVAILABLE') and should_fall_back('A02_SOURCE_MISMATCH')
    assert strict_schema({'properties':{'a':{},'b':{}},'required':['a']})['required']==['a','b']


def test_video_contract_falls_back_when_qwen_is_down():
    from backend.app.video import requirements as video
    from backend.agent_framework.a02_source_selection import index_sources
    text='R01 필수 내레이션 자막: 공연봄날과 함께\n해상도 1920 × 1080 px'
    data=text.encode()
    _,sources=video.read_sources(data,'brief.txt')
    index,_=index_sources(sources);first=next(iter(index.lines))
    last=list(index.lines)[-1]
    reply={'requirements':[{'target':'screen_text','label':'내레이션 자막','expected':'공연봄날과 함께',
        'source':{'start_line':first,'end_line':first},'item_id':'R01','required':True,'unresolved':False,'qualifiers':[]},
        {'target':'video_spec','label':'해상도','expected':'1920','source':{'start_line':first,'end_line':first},
         'item_id':'R99','required':True,'unresolved':False,'qualifiers':[]},
        {'target':'screen_text','label':'내레이션 자막','expected':'공연봄날과 함께',
         'source':{'start_line':first,'end_line':first},'item_id':'R01','required':True,'unresolved':False,
         'qualifiers':[{'start_line':last,'end_line':last}]}]}
    calls=[]
    down=httpx.MockTransport(lambda request:(_ for _ in ()).throw(httpx.ConnectError('down',request=request)))
    result=video.extract(data,'brief.txt',settings=video.Settings(endpoint='https://qwen.invalid/v1/chat/completions',
        model='Qwen/Qwen3.5-4B',api_key=None),transport=down,fallback=FALLBACK,fallback_transport=openai(calls,reply))
    assert len(calls)==1 and result['trace']['model']=='gpt-6-luna'
    assert result['trace']['fallback']=={'provider':'openai','qwen_failure':'QWEN_UNAVAILABLE'}
    assert WARNING in result['warnings'] and result['items'][0]['expected']=='공연봄날과 함께'
    # 원문 검증에 실패한 후보를 제외하고 유효한 자막은 남긴다.
    assert len(result['items'])==1
    assert [r['code'] for r in result['rejected_candidates']]==[
        'A02_ITEM_ID_UNGROUNDED','A02_QUALIFIER_UNGROUNDED']
    with pytest.raises(video.DocumentError) as error:
        video.extract(data,'brief.txt',settings=video.Settings(endpoint='https://qwen.invalid/v1/chat/completions',
            model='Qwen/Qwen3.5-4B',api_key=None),transport=down,fallback=None)
    assert error.value.code=='QWEN_UNAVAILABLE'
