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


@pytest.mark.parametrize('filename',['server.json','server-poster.json'])
def test_plan_never_queries_gpu_starts_a_process_or_prints_secret(filename,monkeypatch,capsys):
    def forbidden(*args,**kwargs):raise AssertionError('Plan must have no process side effects')
    monkeypatch.setattr(server.subprocess,'run',forbidden)
    monkeypatch.setattr(server.os,'execve',forbidden)
    monkeypatch.setenv('VLLM_API_KEY','secret-that-must-not-appear-in-output')
    assert server.main(['plan','--config',str(ROOT/'deploy/qwen-a02'/filename)])==0
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


@pytest.mark.parametrize('field,value',[
    ('host','0.0.0.0'),('venv_directory','/root/paddlegpu'),
    ('venv_directory','/workspace/creative-checker-a02/runtime/venv'),
    ('cache_directory','/workspace/creative-checker-a02/runtime/cache'),
    ('venv_directory','/opt/creative-checker-a02/runtime/../other'),
    ('cache_directory','/opt/creative-checker-a02/runtime'),
    ('model_directory','/workspace/another-project/models')])
def test_config_cannot_target_existing_environment_or_public_bind(config,tmp_path,field,value):
    config[field]=value;p=tmp_path/'server.json';p.write_text(json.dumps(config))
    with pytest.raises(ValueError):server.load_config(p)


def test_plan_separates_executable_runtime_from_global_model_storage(config):
    assert server.command(config)[0]=='/opt/creative-checker-a02/runtime/venv/bin/python'
    assert config['cache_directory']=='/opt/creative-checker-a02/runtime/cache'
    assert config['model_directory'].startswith('/workspace/creative-checker-a02/models/')


def test_worker_config_uses_same_model_and_local_tunnel(config):
    from urllib.parse import urlsplit
    from backend.agent_framework.composition import load_config
    worker=load_config(ROOT/'deploy/qwen-a02/worker-config.example.json')
    endpoint=urlsplit(worker.pdf_extraction.endpoint)
    assert worker.pdf_extraction.model==config['model'] and worker.pdf_extraction.mode=='qwen'
    assert endpoint.hostname=='127.0.0.1' and endpoint.port==config['local_tunnel_port']
    assert endpoint.path=='/v1/chat/completions'


def test_shared_text_server_accepts_auto_tool_requests_without_changing_model(config):
    argv=server.command(config)
    assert argv.count('--enable-auto-tool-choice')==1
    assert argv.count('--tool-call-parser')==1
    assert argv[argv.index('--tool-call-parser')+1]=='qwen3_coder'
    assert '--language-model-only' in argv
    assert argv[argv.index('--model')+1]==config['model_directory']


def test_poster_profile_enables_three_sequences_and_preserves_other_pinned_limits(config):
    poster=server.load_config(ROOT/'deploy/qwen-a02/server-poster.json')
    assert poster==config|{'language_model_only':False,'max_num_seqs':3}
    text_command=server.command(config)
    text_command.remove('--language-model-only')
    text_command[text_command.index('--max-num-seqs')+1]='3'
    assert server.command(poster)==text_command
    from backend.agent_framework.composition import load_config
    worker=load_config(ROOT/'deploy/qwen-a02/worker-config.poster.example.json')
    from urllib.parse import urlsplit
    assert worker.pdf_extraction.model==poster['model']
    assert urlsplit(worker.pdf_extraction.endpoint).port==poster['local_tunnel_port']


@pytest.mark.parametrize('value',[None,False,True,0,4,'3'])
def test_sequence_limit_requires_one_to_three_as_an_integer(config,tmp_path,value):
    config['max_num_seqs']=value
    path=tmp_path/'invalid.json';path.write_text(json.dumps(config))
    with pytest.raises(ValueError,match='INITIAL_A02_PROFILE_REQUIRED'):server.load_config(path)


@pytest.mark.parametrize('value',[1,2,3])
def test_supported_sequence_limits_reach_the_server_command(config,tmp_path,value):
    config['max_num_seqs']=value
    path=tmp_path/'server.json';path.write_text(json.dumps(config))
    argv=server.command(server.load_config(path))
    assert argv[argv.index('--max-num-seqs')+1]==str(value)


@pytest.mark.parametrize('value',[None,0,1,'false','true'])
def test_language_model_only_requires_an_explicit_boolean(config,tmp_path,value):
    config['language_model_only']=value
    path=tmp_path/'invalid.json';path.write_text(json.dumps(config))
    with pytest.raises(ValueError,match='INITIAL_A02_PROFILE_REQUIRED'):server.load_config(path)


def test_legacy_a02_only_configuration_keeps_its_previous_behavior(config,tmp_path):
    config.pop('enable_auto_tool_choice');config.pop('tool_call_parser')
    path=tmp_path/'legacy.json';path.write_text(json.dumps(config))
    argv=server.command(server.load_config(path))
    assert '--enable-auto-tool-choice' not in argv and '--tool-call-parser' not in argv


@pytest.mark.parametrize('automatic,parser',[(True,None),(True,'hermes'),('true','qwen3_coder'),(False,'qwen3_coder')])
def test_inconsistent_tool_options_are_rejected_before_start(config,tmp_path,automatic,parser):
    config.update(enable_auto_tool_choice=automatic,tool_call_parser=parser)
    path=tmp_path/'invalid.json';path.write_text(json.dumps(config))
    with pytest.raises(ValueError,match='INVALID_TOOL_CALLING_CONFIGURATION'):server.load_config(path)
