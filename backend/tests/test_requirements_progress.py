"""실모델 대신 단계별 지연을 주어 진행률·스트리밍·실패 시 잠금 해제를 검증한다."""
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from backend.app import api
from backend.app import requirements_pdf as pdf
from backend.tests.test_requirements_pdf import condition, document_bytes, paddle_stub


def upload(client, data=b'%PDF-test'):
    return client.post('/api/requirements/extract/stream', files={'document':('test.pdf',data,'application/pdf')})


def events(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_progress_tracks_completed_pdf_pages_and_model_batches(monkeypatch, paddle_stub):
    updates=[]
    calls=[]
    def chat(messages, *args):
        context=json.loads(messages[1]['content'])
        assert updates[-1]['stage']=='extracting'
        assert updates[-1]['completed_batches']==len(calls)
        calls.append(context['extraction_pages'])
        page=context['extraction_pages'][0]
        return {'conditions':[condition(label=f'가격{page}',source_id=f'p{page}-s1')]}
    monkeypatch.setattr(pdf,'_chat',chat)
    pdf.extract_pdf(document_bytes(pages=2),progress=updates.append)
    assert [u['percent'] for u in updates]==sorted(u['percent'] for u in updates)
    assert [u['completed_pages'] for u in updates if u['stage']=='ocr']==[0,0,1,1,2]
    assert [u['completed_batches'] for u in updates if u['stage']=='extracting']==[0,1,1,2]
    assert updates[-1]['stage']=='validating' and updates[-1]['percent']==96
    assert all(u['percent']<100 for u in updates)  # 입력 반영 전에는 완료로 처리하지 않는다.


def test_failed_model_does_not_report_completion(monkeypatch,paddle_stub):
    def fail(*args): raise pdf.DocumentError('분석 실패',502)
    monkeypatch.setattr(pdf,'_chat',fail)
    updates=[]
    with pytest.raises(pdf.DocumentError):pdf.extract_pdf(document_bytes(),progress=updates.append)
    assert updates[-1]['stage']=='extracting' and updates[-1]['percent']==50


def test_progress_crosses_process_boundary_without_model_execution(monkeypatch):
    original_popen=subprocess.Popen
    def spawn(command,**options):
        script='''import json,sys,time
from pathlib import Path
progress,output=map(Path,sys.argv[1:])
for percent in (25,75):
 temporary=progress.with_suffix('.tmp')
 temporary.write_text(json.dumps({'stage':'ocr','percent':percent,'message':'페이지 읽기'}))
 temporary.replace(progress)
 time.sleep(.35)
output.write_text(json.dumps({'requirements':'시간: 17:30','conditions':[]}))
'''
        return original_popen([sys.executable,'-c',script,command[-1],command[-2]],**options)
    monkeypatch.setattr(pdf.subprocess,'Popen',spawn)
    updates=[]
    result=pdf.extract_document(b'%PDF-test',progress=updates.append)
    assert result['requirements']=='시간: 17:30'
    assert [u['percent'] for u in updates]==[25,75]


def test_timed_out_progress_worker_is_killed_and_reaped(monkeypatch):
    child=Mock()
    child.poll.return_value=None
    monkeypatch.setattr(pdf.subprocess,'Popen',Mock(return_value=child))
    monkeypatch.setattr(pdf,'MAX_SECONDS',-10)
    with pytest.raises(pdf.DocumentError) as error:pdf.extract_document(b'%PDF-test',progress=lambda u:None)
    assert error.value.status==504
    child.kill.assert_called_once()
    child.wait.assert_called_once()


def test_stream_preserves_result_and_sends_actual_progress(tmp_path,monkeypatch):
    expected={'requirements':'시간: 17:30','conditions':[condition()], 'page_count':1}
    def extract(data,progress):
        progress({'stage':'extracting','percent':50,'message':'조건 분석 중'})
        progress({'stage':'validating','percent':96,'message':'결과 확인 중'})
        return expected
    monkeypatch.setattr(api,'extract_document',extract)
    with TestClient(api.create_app(review_root=tmp_path)) as client:
        response=upload(client)
    assert response.status_code==200 and 'application/x-ndjson' in response.headers['content-type']
    received=events(response)
    assert [e['percent'] for e in received[:-1]]==[2,50,96]
    assert received[-1]=={'type':'result','result':expected}
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('data,status',[(b'',400),(b'not pdf',415)],ids=['empty','not-pdf'])
def test_stream_validates_before_starting_worker(tmp_path,monkeypatch,data,status):
    def unexpected(*args,**kwargs):pytest.fail('invalid upload started worker')
    monkeypatch.setattr(api,'extract_document',unexpected)
    with TestClient(api.create_app(review_root=tmp_path)) as client:
        assert upload(client,data).status_code==status


def test_stream_failure_keeps_inference_locked_until_worker_finishes(tmp_path,monkeypatch):
    entered,release=Event(),Event()
    def extract(data,progress=None):
        entered.set()
        assert release.wait(5)
        raise pdf.DocumentError('모델 상태를 확인해 주세요.',503)
    monkeypatch.setattr(api,'extract_document',extract)
    with TestClient(api.create_app(review_root=tmp_path)) as client,ThreadPoolExecutor(max_workers=1) as pool:
        running=pool.submit(upload,client)
        assert entered.wait(5)
        try:
            assert upload(client).status_code==429
            assert client.post('/api/requirements/extract',files={'document':('a.pdf',b'%PDF-test','application/pdf')}).status_code==429
        finally:release.set()
        terminal=events(running.result())[-1]
        assert terminal=={'type':'error','message':'모델 상태를 확인해 주세요.','status':503}
        assert events(upload(client))[-1]['type']=='error'  # 오류 뒤에는 재시도 가능


def test_stream_unexpected_errors_do_not_disclose_internal_details(tmp_path,monkeypatch):
    def extract(*args,**kwargs):raise RuntimeError('/secret/key private-token')
    monkeypatch.setattr(api,'extract_document',extract)
    with TestClient(api.create_app(review_root=tmp_path)) as client:
        response=upload(client)
    assert events(response)[-1]['type']=='error'
    assert '/secret' not in response.text and 'private-token' not in response.text
