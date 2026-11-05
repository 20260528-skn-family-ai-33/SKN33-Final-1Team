"""Bounded synthetic streams; no network endpoint or large decompressed allocation."""
import gzip
import httpx
import pytest

from backend.agent_framework.agents.loop import OpenAICompatibleChat,MAX_RESPONSE_BYTES
from backend.agent_framework.ports import AdapterFailure
from backend.a07_reporter.qwen import QwenPolisher
from backend.a07_reporter.adapter import items
from tests.agent_framework.test_team_a07_qwen import fixture
from tests.agent_framework.test_team_a04_a05 import client,CRITERIA,model_image


def call_model(role,handler):
    if role=='A04_A05':return client(handler).inspect(CRITERIA[:1],model_image())
    if role=='A07':
        qwen=QwenPolisher('https://qwen.invalid',transport=httpx.MockTransport(handler))
        try:return qwen.polish(items(fixture('fail')))
        finally:qwen.close()
    chat=OpenAICompatibleChat('https://qwen.invalid');headers=dict(chat._http.headers);chat.close()
    chat._http=httpx.Client(headers=headers,transport=httpx.MockTransport(handler),trust_env=False)
    try:return chat.complete([],[])
    finally:chat.close()


@pytest.mark.parametrize('role',['A01_A08','A04_A05','A07'])
@pytest.mark.parametrize('mode',['oversize','gzip'])
def test_oversized_stream_stops_and_compressed_stream_is_never_decoded(role,mode):
    consumed=[];closed=[];calls=[]
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            if mode=='gzip':
                consumed.append(1);yield gzip.compress(b'x'*65536)
            else:
                consumed.append(MAX_RESPONSE_BYTES);yield b' '*MAX_RESPONSE_BYTES
                consumed.append(1);yield b' '
            raise AssertionError('response must stop before further reads')
        def close(self):closed.append(True)
    def respond(request):
        calls.append(1);assert request.headers['accept-encoding']=='identity'
        return httpx.Response(200,headers={'Content-Encoding':'gzip'} if mode=='gzip' else {},stream=Stream())
    if role=='A07':assert call_model(role,respond) is None
    else:
        with pytest.raises(AdapterFailure):call_model(role,respond)
    assert len(calls)==1 and closed==[True]
    assert consumed==([] if mode=='gzip' else [MAX_RESPONSE_BYTES,1])


@pytest.mark.parametrize('role',['A01_A08','A04_A05','A07'])
@pytest.mark.parametrize('failure',['http500','read_timeout'])
def test_unknown_inference_completion_is_single_attempt(role,failure):
    calls=[]
    def respond(request):
        calls.append(1)
        if failure=='read_timeout':raise httpx.ReadTimeout('synthetic')
        return httpx.Response(500)
    with pytest.raises(AdapterFailure) as error:call_model(role,respond)
    assert error.value.outcome_unknown and not error.value.retryable and len(calls)==1


@pytest.mark.parametrize('role',['A01_A08','A04_A05','A07'])
@pytest.mark.parametrize('failure',['connect_timeout','http429'])
def test_unsent_or_rejected_calls_are_known_failures_without_automatic_retry(role,failure):
    calls=[]
    def respond(request):
        calls.append(1)
        if failure=='connect_timeout':raise httpx.ConnectTimeout('synthetic')
        return httpx.Response(429)
    if role=='A07':assert call_model(role,respond) is None
    else:
        with pytest.raises(AdapterFailure) as error:call_model(role,respond)
        assert error.value.retryable and not error.value.outcome_unknown
    assert len(calls)==1
