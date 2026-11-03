"""Deployment preparation must not start models or overwrite existing services."""
import hashlib
import importlib.util
import json
from pathlib import Path
import socket

import pytest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('qwen_server_preparation',ROOT/'deploy/qwen-a02/server.py')
server=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(server)


@pytest.fixture
def config():return server.load_config(ROOT/'deploy/qwen-a02/server.json')


def test_plan_never_queries_gpu_starts_a_process_or_prints_secret(config,monkeypatch,capsys):
    def forbidden(*args,**kwargs):raise AssertionError('Plan must have no process side effects')
    monkeypatch.setattr(server.subprocess,'run',forbidden)
    monkeypatch.setattr(server.os,'execve',forbidden)
    monkeypatch.setenv('VLLM_API_KEY','secret-that-must-not-appear-in-output')
    assert server.main(['plan'])==0
    output=capsys.readouterr().out
    assert 'secret-that-must-not-appear-in-output' not in output
    assert json.loads(output)['server_started'] is False


@pytest.mark.parametrize('rows,expected',[
    ([{'total_mib':16380,'free_mib':10352}],['GPU_MEMORY_NOT_AVAILABLE']),
    ([{'total_mib':16380,'free_mib':16000}],[]),
    ([],['EXACTLY_ONE_GPU_REQUIRED']),
    ([{'total_mib':16380,'free_mib':16000}]*2,['EXACTLY_ONE_GPU_REQUIRED'])])
def test_busy_gpu_is_rejected_even_if_utilization_would_be_zero(config,rows,expected):
    assert server.gpu_blockers(config,rows)==expected


def test_serve_with_failed_preflight_does_not_execute_any_process(monkeypatch):
    monkeypatch.setattr(server,'check',lambda *_args,**_kwargs:{'blockers':['GPU_MEMORY_NOT_AVAILABLE']})
    def forbidden(*args):raise AssertionError('Busy GPU must not start a second model')
    monkeypatch.setattr(server.os,'execve',forbidden)
    assert server.main(['serve'])==2


def test_existing_listener_is_preserved_and_reported(config,monkeypatch):
    monkeypatch.setattr(server,'gpu_rows',lambda:[{'name':'fixture','total_mib':16380,'free_mib':16000}])
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));sock.listen()
        config['port']=sock.getsockname()[1]
        assert 'SERVER_PORT_UNAVAILABLE' in server.check(config)['blockers']
        assert sock.getsockname()[1]==config['port']


def test_same_size_weight_corruption_is_detected(config,tmp_path,monkeypatch):
    raw=b'test-only-weight-content';weights=tmp_path/'model';weights.mkdir();path=weights/'sample.safetensors'
    path.write_bytes(raw)
    (tmp_path/'model-manifest.json').write_text(json.dumps({'files':[{
        'name':path.name,'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}]}))
    monkeypatch.setattr(server,'HERE',tmp_path);config['model_directory']=str(weights)
    assert server.verify_model(config)==[]
    path.write_bytes(b'x'*len(raw))
    assert server.verify_model(config)==['MODEL_SHA256_MISMATCH:'+path.name]


@pytest.mark.parametrize('field,value',[('host','0.0.0.0'),('venv_directory','/root/paddlegpu')])
def test_config_cannot_target_existing_environment_or_public_bind(config,tmp_path,field,value):
    config[field]=value;p=tmp_path/'server.json';p.write_text(json.dumps(config))
    with pytest.raises(ValueError):server.load_config(p)


def test_worker_config_uses_same_model_and_local_tunnel(config):
    from urllib.parse import urlsplit
    from backend.agent_framework.composition import load_config
    worker=load_config(ROOT/'deploy/qwen-a02/worker-config.example.json')
    endpoint=urlsplit(worker.pdf_extraction.endpoint)
    assert worker.pdf_extraction.model==config['model'] and worker.pdf_extraction.mode=='qwen'
    assert endpoint.hostname=='127.0.0.1' and endpoint.port==config['local_tunnel_port']
    assert endpoint.path=='/v1/chat/completions'
