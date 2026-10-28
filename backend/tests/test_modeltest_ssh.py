"""Offline SSH/runner checks. No real SSH, package install, model download or inference."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from backend.app import modeltest_remote as remote
from backend.app import modeltest_ssh as bridge


def config(flow='image'):
    ids = [name for name in remote.STAGES if name not in remote.catalog.EXTRA_STAGES and name not in ('logo_similarity', 'candidate_filter', 'text_generate') and (flow == 'video' or not name.startswith('logo_'))]
    return {'contract': remote.CONTRACT, 'flow': flow, 'requirements': '행사명을 표시해야 합니다.',
        'stages': [{'id': name, 'model_id': remote.QWEN} for name in ids],
        'sampling': {'unit': 'frames', 'interval': 30, 'max_frames': 3},
        'files': {'media': {'name': 'poster.png' if flow == 'image' else 'video.mp4', 'size': 5, 'type': ''},
                  'documents': [{'name': 'contract.txt', 'size': 4, 'type': ''}],
                  'references': [{'name': 'logo.png', 'size': 4, 'type': ''}] if flow == 'video' else []}}


def test_both_ssh_commands_and_quoted_windows_key_paths(tmp_path):
    key = tmp_path / 'my key'; key.write_text('test fixture, not a private key')
    tcp = bridge.parse_ssh(f'ssh root@203.0.113.10 -p 12345 -i "{key}"')
    assert tcp.port == 12345 and tcp.identity == str(key.resolve()) and not tcp.proxy
    proxy = bridge.parse_ssh(f'ssh pod-identifier@ssh.runpod.io -i "{key}"')
    assert proxy.proxy and proxy.port == 22
    args = bridge.ssh_arguments(tcp, 'fixed command')
    assert '-T' in args and args[-1] == 'fixed command' and 'StrictHostKeyChecking=yes' in args
    assert 'ForwardAgent=no' in args and 'ClearAllForwardings=yes' in args
    assert bridge.ssh_arguments(proxy, 'fixed command')[-1] == 'pod-identifier@ssh.runpod.io'
    assert '-tt' in bridge.ssh_arguments(proxy, 'fixed command')


@pytest.mark.parametrize('value', [
    'ssh root@203.0.113.10 ; whoami', 'ssh root@203.0.113.10 uname',
    'ssh -o ProxyCommand=evil root@203.0.113.10', 'ssh -L 8000:localhost:8000 root@203.0.113.10',
    'ssh root@203.0.113.10 -p 99999', 'ssh root@203.0.113.10 -p 22 -p 23',
    'ssh root@203.0.113.10\nevil', 'ssh root@evil.example', 'other root@203.0.113.10',
])
def test_command_options_cannot_execute_shell_or_tunnel(value):
    with pytest.raises(ValueError): bridge.parse_ssh(value)


def test_remote_contract_rejects_missing_dependencies_and_invalid_sampling():
    state = config('video'); assert remote.validate_config(state) is state
    state['stages'] = [item for item in state['stages'] if item['id'] != 'logo_extract']
    with pytest.raises(ValueError): remote.validate_config(state)
    state = config('video'); state['sampling']['interval'] = 0
    with pytest.raises(ValueError): remote.validate_config(state)
    state = config(); state['files']['documents'] = []
    with pytest.raises(ValueError): remote.validate_config(state)
    state = config(); state['stages'][0]['model_id'] = 'ShortModelName'
    with pytest.raises(ValueError): remote.validate_config(state)


def test_preview_is_bounded_jpeg_and_rejects_other_files(tmp_path):
    Image.new('RGB', (2000, 1000)).save(tmp_path / 'frame-00000030.jpg')
    result = remote.read_preview(tmp_path, 'frame-00000030.jpg')
    assert result['mime'] == 'image/jpeg'
    assert Image.open(io.BytesIO(base64.b64decode(result['data']))).size == (1280, 640)
    for name in ('../frame-00000030.jpg', 'process.json', 'runner.py', 'media-0.mp4', 'logo-0-0.png'):
        with pytest.raises(ValueError): remote.read_preview(tmp_path, name)


@pytest.mark.parametrize('cancelled', [False, True])
def test_dead_runner_reports_exit_signal_and_preserves_completed_results(tmp_path, monkeypatch, cancelled):
    job = {'status': 'running', 'started_at': 10, 'updated_at': 11, 'stages': [
        {'id': 'frames', 'status': 'completed', 'output': {'frames': []}},
        {'id': 'logo_similarity', 'status': 'running', 'started_at': 11}, {'id': 'logo_compare', 'status': 'queued'}]}
    (tmp_path / 'result.json').write_text(json.dumps(job))
    (tmp_path / 'exit.json').write_text(json.dumps({'returncode': -9, 'signal': 'SIGKILL', 'finished_at': 20}))
    (tmp_path / 'runner.log').write_text('native failure\n' + 'x' * 8000 + '\nCUDA fixture error')
    if cancelled: (tmp_path / 'cancel-requested').touch()
    value = remote.read_run(tmp_path)
    assert value['status'] == ('cancelled' if cancelled else 'failed')
    assert value['stages'][0] == job['stages'][0]
    assert value['stages'][1]['status'] == ('cancelled' if cancelled else 'failed')
    assert value['stages'][2]['status'] == ('cancelled' if cancelled else 'skipped')
    assert value['elapsed_seconds'] == 10 and value['timing_estimated']
    assert value['diagnostics']['returncode'] == -9 and value['diagnostics']['signal'] == 'SIGKILL'
    assert value['diagnostics']['log_tail'].endswith('CUDA fixture error') and len(value['diagnostics']['log_tail']) <= 6000
    if not cancelled: assert 'SIGKILL' in value['error']


def test_live_supervisor_does_not_report_failure_before_exit_is_saved(tmp_path, monkeypatch):
    job = {'status': 'running', 'stages': []}
    (tmp_path / 'result.json').write_text(json.dumps(job))
    (tmp_path / 'process.json').write_text(json.dumps({'pid': 10, 'start': 'worker'}))
    (tmp_path / 'supervisor.json').write_text(json.dumps({'pid': 20, 'start': 'supervisor'}))
    monkeypatch.setattr(remote, 'process_start', lambda pid: 'supervisor' if pid == 20 else None)
    assert remote.read_run(tmp_path) == job
    monkeypatch.setattr(remote, 'process_start', lambda _pid: None)
    assert '종료 코드·오류 로그가 남아 있지 않습니다' in remote.read_run(tmp_path)['error']


@pytest.mark.parametrize('failure', [False, True])
def test_supervisor_saves_exit_and_logs_before_emitting_terminal_result(tmp_path, monkeypatch, failure):
    runner = tmp_path / 'runner.py'; arguments = tmp_path / 'job.json'; arguments.write_text('{}')
    runner.write_text('''import json,sys
from pathlib import Path
root=Path(sys.argv[1]).parent
job={'status':'completed','stages':[]}
(root/'result.json').write_text(json.dumps(job))
print(sys.argv[2]+json.dumps({'kind':'run','data':job}),flush=True)
print('hf_abcdefghijklmnopqrstuvwxyz123456 https://example.invalid/?token=secret',file=sys.stderr,flush=True)
''' + ("raise RuntimeError('fixture native failure')\n" if failure else ''), encoding='utf-8')
    monkeypatch.setattr(remote, 'process_start', lambda _pid: 'fixture')
    events = []
    def emit(kind, value):
        assert (tmp_path / 'exit.json').is_file()
        events.append((kind, value))
    remote.supervise_run(runner, arguments, 'FIXTURE:', emit)
    assert len(events) == 1
    result = events[0][1]
    assert result['status'] == ('failed' if failure else 'completed')
    assert result['diagnostics']['returncode'] == (1 if failure else 0)
    assert result['diagnostics']['signal'] is None
    assert json.loads((tmp_path / 'result.json').read_text(encoding='utf-8')) == result
    assert 'hf_abcdefghijklmnopqrstuvwxyz' not in (tmp_path / 'runner.log').read_text(encoding='utf-8')
    assert 'token=secret' not in result['diagnostics']['log_tail']
    if failure: assert 'RuntimeError: fixture native failure' in result['diagnostics']['log_tail']


@pytest.mark.parametrize('corrupt', [False, True])
def test_remote_binary_upload_checks_exact_size_and_checksum_before_launch(tmp_path, monkeypatch, corrupt):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    is_dir = Path.is_dir
    monkeypatch.setattr(Path, 'is_dir', lambda path: False if str(path).replace('\\', '/') == '/workspace' else is_dir(path))
    python = tmp_path / '.cache/creative-checker-modeltest/venv/bin/python'
    python.parent.mkdir(parents=True); python.touch()
    data = b'\xff\x00\n\r\x80' * 50; document = b'fixture document'
    uploads = [{'kind': kind, 'stored_name': name, 'size': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
        for kind, name, body in [('media', 'media-0.png', data), ('documents', 'document-0.txt', document)]]
    incoming = (b'x' + data[1:] if corrupt else data) + document
    monkeypatch.setattr(remote.sys, 'stdin', io.TextIOWrapper(io.BytesIO(incoming)))
    launched = []
    monkeypatch.setattr(remote.subprocess, 'run', lambda args, **kwargs: launched.append((args, kwargs)))
    run_id = str(uuid4()); events = io.StringIO()
    boot = {'action': 'run', 'id': run_id, 'config': config(), 'uploads': uploads, 'binary_upload': True, 'digest': 'fixture', 'source': 'pass'}
    root = tmp_path / '.creative-checker-modeltest/runs' / run_id
    if corrupt:
        with pytest.raises(ValueError, match='checksum'): remote.remote_main(boot, events, 'EVENT:')
        assert not launched and not (root / 'receipt.json').exists()
    else:
        remote.remote_main(boot, events, 'EVENT:')
        assert (root / 'media-0.png').read_bytes() == data and (root / 'document-0.txt').read_bytes() == document
        assert len(launched) == 1 and launched[0][1]['start_new_session'] is True


@pytest.mark.parametrize('mode', ['term', 'kill', 'reused', 'wrong-command'])
def test_cancel_signals_only_the_recorded_runner_and_preserves_completed_stages(tmp_path, monkeypatch, mode):
    job = {'id': tmp_path.name, 'status': 'running', 'stages': [{'id': 'frames', 'status': 'completed'}, {'id': 'logo_extract', 'status': 'running'}]}
    (tmp_path / 'result.json').write_text(json.dumps(job), encoding='utf-8')
    (tmp_path / 'process.json').write_text(json.dumps({'pid': 123456, 'start': 'fixture-start'}))
    signals = []; alive = [True]
    monkeypatch.setattr(remote.signal, 'SIGKILL', 9, raising=False)
    monkeypatch.setattr(remote, 'process_start', lambda pid: ('different' if mode == 'reused' else 'fixture-start') if alive[0] else None)
    original = Path.read_bytes
    def read_bytes(path):
        if str(path).replace('\\', '/') == '/proc/123456/cmdline':
            return b'other-process' if mode == 'wrong-command' else b'\0'.join([str(tmp_path / 'runner.py').encode(), str(tmp_path / 'job.json').encode()])
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', read_bytes)
    monkeypatch.setattr(remote.os, 'getpgid', lambda pid: pid, raising=False)
    def kill(pid, sig):
        assert pid == 123456; signals.append(sig)
        if mode == 'term' or sig == 9: alive[0] = False
    monkeypatch.setattr(remote.os, 'killpg', kill, raising=False)
    monkeypatch.setattr(remote.os, 'kill', lambda *_: pytest.fail('must use the dedicated process group'))
    ticks = iter(range(100)); monkeypatch.setattr(remote.time, 'monotonic', lambda: next(ticks)); monkeypatch.setattr(remote.time, 'sleep', lambda _: None)
    if mode == 'wrong-command':
        with pytest.raises(ValueError, match='일치하지'): remote.cancel_run(tmp_path)
        assert signals == []; return
    result = remote.cancel_run(tmp_path)
    assert result['status'] == ('failed' if mode == 'reused' else 'cancelled')
    assert result['stages'][0]['status'] == 'completed'
    assert signals == ([] if mode == 'reused' else [remote.signal.SIGTERM] if mode == 'term' else [remote.signal.SIGTERM, 9])


def test_bridge_recovers_previous_run_and_previews_then_cancels_only_it(tmp_path, monkeypatch):
    identifier = str(uuid4()); calls = []
    job = {'contract': remote.CONTRACT, 'id': identifier, 'status': 'running', 'stages': [{'id': 'text_extract', 'status': 'running'}]}
    jpeg = io.BytesIO(); Image.new('RGB', (10, 10)).save(jpeg, format='JPEG')
    def exchange(connection, action, **kwargs):
        calls.append((action, kwargs))
        if action == 'probe': return {'ready': True, 'can_prepare': True, 'cancel': True}
        assert kwargs['id'] == identifier
        if action == 'status': return copy.deepcopy(job)
        if action == 'preview': return {'mime': 'image/jpeg', 'data': base64.b64encode(jpeg.getvalue()).decode()}
        if action == 'cancel': return {**job, 'status': 'cancelled', 'stages': [{'id': 'text_extract', 'status': 'cancelled'}]}
        pytest.fail(f'Unexpected action: {action}')
    monkeypatch.setattr(bridge, 'exchange', exchange)
    app = FastAPI(); app.include_router(bridge.modeltest_ssh_router(tmp_path / 'uploads'))
    headers = {'X-Modeltest-Local': '1'}
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as client:
        connection = client.post('/api/modeltest/ssh/connections', json={'command': 'ssh root@203.0.113.10'}, headers=headers).json()['connection_id']
        path = f'/api/modeltest/ssh/connections/{connection}/runs/{identifier}'
        assert client.get(path, headers=headers).json()['status'] == 'running'
        assert client.get(path + '/previews/frame-00000000.jpg', headers=headers).status_code == 200
        assert client.get(path + '/previews/frame-00000000.jpg', headers=headers).status_code == 200
        assert len([call for call in calls if call[0] == 'preview']) == 1
        before = len(calls)
        assert client.get(path + '/previews/process.json', headers=headers).status_code == 400
        assert client.get(path + '/previews/frame-00000000.jpg').status_code == 403
        assert len(calls) == before
        assert client.post(path + '/cancel', headers=headers).json()['status'] == 'cancelled'
        assert client.get(path, headers=headers).json()['status'] == 'cancelled'
        assert len([call for call in calls if call[0] == 'cancel']) == 1


@pytest.mark.parametrize('proxy', [False, True])
def test_framed_transfer_round_trips_bytes_without_ssh_or_network(tmp_path, monkeypatch, proxy):
    stub = tmp_path / 'remote_stub.py'
    stub.write_text('''import sys,json,base64,hashlib
def remote_main(boot,stream,prefix):
    for item in boot['uploads']:
        print(prefix+json.dumps({'kind':'upload','data':item['stored_name']}),flush=True)
        received=bytearray()
        if boot.get('binary_upload'):
            received.extend(sys.stdin.buffer.read(item['size']))
        else:
            while True:
                part=json.loads(sys.stdin.readline())
                if part.get('end'): break
                received.extend(base64.b64decode(part['chunk']))
        assert len(received)==item['size']
        assert hashlib.sha256(received).hexdigest()==item['sha256']
    print(prefix+json.dumps({'kind':'preview','data':{'name':'fixture-preview'}}),flush=True)
    print(prefix+json.dumps({'kind':'run','data':{'id':boot['id'],'status':'completed'}}),flush=True)
''', encoding='utf-8')
    monkeypatch.setattr(remote, '__file__', str(stub))
    simulated_shell = "import sys,shlex;line=sys.stdin.readline();exec(shlex.split(line.split(';',1)[1])[-1])"
    monkeypatch.setattr(bridge, 'ssh_arguments', lambda _connection, command: [sys.executable, '-u', '-c', simulated_shell if proxy else shlex.split(command)[-1]])
    data = bytes(range(256)) * 1000; source = tmp_path / 'payload.bin'; source.write_bytes(data)
    connection = bridge.SSHConnection('root', 'ssh.runpod.io' if proxy else '203.0.113.10', 22, None)
    events = []
    uploads = [{'kind': 'media', 'stored_name': name, 'path': str(source), 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()} for name in ('media-0.bin', 'reference-0.bin')]
    result = bridge.exchange(connection, 'run', id='test-run', uploads=uploads, on_event=lambda kind, value: events.append((kind, value)), timeout=10)
    assert result == {'id': 'test-run', 'status': 'completed'}
    progress = [value for kind, value in events if kind == 'progress']
    assert progress[0]['phase'] == 'connecting' and progress[-1]['phase'] == 'ready'
    assert progress[-1]['bytes_sent'] == progress[-1]['total_bytes'] == 2 * len(data)
    assert ('preview', {'name': 'fixture-preview'}) in events


@pytest.mark.parametrize('flow', ['image', 'video'])
def test_runner_passes_conditions_extractions_and_logo_crops_to_comparison(tmp_path, monkeypatch, flow):
    calls = []
    class FakeModels:
        kind = 'vision'
        def load(self, stage): self.stage = stage['id']
        def generate(self, prompt, images=(), require_vision=False):
            calls.append((self.stage, prompt, list(images))); return self.stage + ' output'
        def logos(self, _path, instruction=''): return [{'bbox': [2, 3, 12, 13]}]
    monkeypatch.setattr(remote, 'Models', FakeModels)
    monkeypatch.setattr(remote, 'process_start', lambda _pid: 'fixture')
    poster = tmp_path / 'poster.png'; Image.new('RGB', (20, 20)).save(poster)
    reference = tmp_path / 'reference.png'; Image.new('RGB', (20, 20)).save(reference)
    document = tmp_path / 'contract.txt'; document.write_text('행사명은 테스트입니다.', encoding='utf-8')
    monkeypatch.setattr(remote, 'frames_of', lambda *args: [{'index': 0, 'timestamp_seconds': 0.0, 'path': poster}])
    events = []
    remote.execute_run(config(flow), {'media': poster, 'documents': [document], 'references': [reference], 'container': 'mov'}, tmp_path,
                       lambda _kind, value: events.append(copy.deepcopy(value)))
    assert events[-1]['status'] == 'completed'
    assert len(events[-1]['stages']) == (8 if flow == 'video' else 5)
    comparison = next(prompt for stage, prompt, _images in calls if stage == 'text_compare')
    assert 'conditions output' in comparison and 'text_extract output' in comparison
    if flow == 'video':
        images = next(images for stage, _prompt, images in calls if stage == 'logo_compare')
        assert images[0].name.startswith('logo-') and images[1] == reference
        assert Image.open(images[0]).size == (10, 10)


@pytest.fixture
def local_client(tmp_path, monkeypatch):
    calls = []
    def fake_exchange(_connection, action, **kwargs):
        calls.append(action)
        if action in ('probe', 'prepare'):
            return {'contract': remote.CONTRACT, 'ready': True, 'can_prepare': True, 'stages': list(remote.STAGES), 'formats': remote.FORMATS, 'limits': remote.LIMITS, 'cancel': False}
        job = {'contract': remote.CONTRACT, 'id': kwargs['id'], 'status': 'completed',
               'stages': [{'id': item['id'], 'status': 'completed'} for item in kwargs['config']['stages']]}
        jpeg = io.BytesIO(); Image.new('RGB', (10, 10)).save(jpeg, format='JPEG')
        kwargs['on_event']('preview', {'name': 'preview-poster.jpg', 'mime': 'image/jpeg', 'data': base64.b64encode(jpeg.getvalue()).decode()})
        kwargs['on_event']('run', job); return job
    monkeypatch.setattr(bridge, 'exchange', fake_exchange)
    app = FastAPI(); app.include_router(bridge.modeltest_ssh_router(tmp_path / 'uploads'))
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as client:
        yield client, calls


def test_bridge_requires_local_custom_header_and_rejects_other_origins(local_client):
    client, calls = local_client
    for headers in ({}, {'X-Modeltest-Local': '1', 'Origin': 'https://evil.example'}):
        assert client.post('/api/modeltest/ssh/connections', json={'command': 'ssh root@203.0.113.10'}, headers=headers).status_code == 403
    assert calls == []


def test_bridge_accepts_files_and_deduplicates_the_same_request(local_client):
    client, calls = local_client; headers = {'X-Modeltest-Local': '1', 'Origin': 'http://127.0.0.1'}
    connected = client.post('/api/modeltest/ssh/connections', json={'command': 'ssh root@203.0.113.10 -p 12345'}, headers=headers)
    assert connected.status_code == 200
    identifier = connected.json()['connection_id']; request_id = str(uuid4()); headers['Idempotency-Key'] = request_id
    state = config(); files = [('media', ('poster.png', b'image', 'image/png')), ('documents', ('contract.txt', b'text', 'text/plain'))]
    path = f'/api/modeltest/ssh/connections/{identifier}/runs'
    first = client.post(path, data={'config': json.dumps(state)}, files=files, headers=headers)
    assert first.status_code == 202, first.text
    for _ in range(100):
        result = client.get(path + '/' + request_id, headers=headers)
        if result.json()['status'] == 'completed': break
        time.sleep(.01)
    assert result.json()['status'] == 'completed'
    preview = client.get(path + '/' + request_id + '/previews/preview-poster.jpg', headers=headers)
    assert preview.status_code == 200 and preview.json()['mime'] == 'image/jpeg'
    assert 'preview' not in calls, 'Streamed preview should not open another SSH session'
    second = client.post(path, data={'config': json.dumps(state)}, files=files, headers=headers)
    assert second.status_code == 202 and calls.count('run') == 1
    state['requirements'] = '다른 조건'
    conflict = client.post(path, data={'config': json.dumps(state)}, files=files, headers=headers)
    assert conflict.status_code == 409


def test_bridge_rejects_upload_metadata_mismatch(local_client):
    client, calls = local_client; headers = {'X-Modeltest-Local': '1'}
    identifier = client.post('/api/modeltest/ssh/connections', json={'command': 'ssh root@203.0.113.10'}, headers=headers).json()['connection_id']
    state = config(); headers['Idempotency-Key'] = str(uuid4())
    response = client.post(f'/api/modeltest/ssh/connections/{identifier}/runs', data={'config': json.dumps(state)},
        files=[('media', ('poster.png', b'wrong-size')), ('documents', ('contract.txt', b'text'))], headers=headers)
    assert response.status_code == 422 and 'run' not in calls


def test_similarity_bridge_accepts_hundred_image_candidates(local_client):
    client, calls = local_client; headers = {'X-Modeltest-Local': '1'}
    identifier = client.post('/api/modeltest/ssh/connections', json={'command': 'ssh root@203.0.113.10'}, headers=headers).json()['connection_id']
    state = {'contract': remote.CONTRACT, 'flow': 'similarity', 'resize': {'width': 100, 'height': 50},
             'stages': [{'id': 'logo_similarity', 'model_id': remote.SIGLIP2}, {'id': 'logo_compare', 'model_id': remote.XFEAT}],
             'files': {'media': {'name': 'base.png', 'size': 5}, 'documents': [], 'references': [{'name': f'{i}.png', 'size': 5} for i in range(100)]}}
    files = [('media', ('base.png', b'image', 'image/png')), *[('references', (f'{i}.png', b'image', 'image/png')) for i in range(100)]]
    headers['Idempotency-Key'] = str(uuid4())
    response = client.post(f'/api/modeltest/ssh/connections/{identifier}/runs', data={'config': json.dumps(state)}, files=files, headers=headers)
    assert response.status_code == 202, response.text
    for _ in range(100):
        result = client.get(f'/api/modeltest/ssh/connections/{identifier}/runs/{headers["Idempotency-Key"]}', headers=headers)
        if result.json()['status'] == 'completed': break
        time.sleep(.01)
    assert result.json()['status'] == 'completed' and calls.count('run') == 1


def test_environment_preparation_is_explicit_and_status_is_queryable(local_client):
    client, calls = local_client; headers = {'X-Modeltest-Local': '1'}
    identifier = client.post('/api/modeltest/ssh/connections', json={'command': 'ssh root@203.0.113.10'}, headers=headers).json()['connection_id']
    assert client.get(f'/api/modeltest/ssh/connections/{identifier}/health', headers=headers).json()['ready']
    assert calls == ['probe']
    assert client.post(f'/api/modeltest/ssh/connections/{identifier}/prepare', headers=headers).status_code == 202
    for _ in range(100):
        status = client.get(f'/api/modeltest/ssh/connections/{identifier}/health', headers=headers).json()
        if not status['preparing']: break
        time.sleep(.01)
    assert status['ready'] and calls == ['probe', 'prepare']


def test_prepare_repairs_existing_venv_and_keeps_files(tmp_path, monkeypatch):
    import venv
    from importlib import metadata
    root = tmp_path / 'runtime'; env = root / 'venv'
    venv.EnvBuilder(with_pip=False).create(env)
    keep = env / 'keep.txt'; keep.write_text('existing data')
    # The Pod layout is Unix even when this regression check runs on Windows.
    python = env / 'bin/python'; python.parent.mkdir(exist_ok=True); python.touch(exist_ok=True)
    assert 'include-system-site-packages = false' in (env / 'pyvenv.cfg').read_text()
    real_run = subprocess.run
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if 'venv' in args:
            # Exercise real stdlib repair without installing pip during this offline check.
            return real_run([*args, '--without-pip'], **kwargs)
        return subprocess.CompletedProcess(args, 0, stdout='')
    monkeypatch.setattr(remote.subprocess, 'run', run)
    monkeypatch.setattr(metadata, 'version', lambda name: {'torch': '2.8.0+cu128', 'torchvision': '0.23.0+cu128'}[name])
    remote.prepare_runtime(root)
    assert 'include-system-site-packages = true' in (env / 'pyvenv.cfg').read_text()
    assert keep.read_text() == 'existing data'
    assert any('pip' in args and 'install' in args for args in calls)
    assert (root / 'existing-torch.txt').read_text().splitlines() == ['torch==2.8.0+cu128', 'torchvision==0.23.0+cu128']


def test_setup_failure_includes_actual_stderr_and_redacts_credentials():
    code = 'import sys; print("progress"); print("ERROR: https://user:secret@example.com/pkg?token=hidden hf_abcdefghijklmnopqrstuvwxyz123456 hf_xet",file=sys.stderr); sys.exit(7)'
    with pytest.raises(ValueError) as caught:
        remote.setup_command('패키지 설치', [sys.executable, '-c', code])
    detail = str(caught.value)
    assert '패키지 설치 실패' in detail and '종료 코드 7' in detail and 'ERROR:' in detail
    assert 'secret' not in detail and 'hidden' not in detail and 'hf_abcdefghijklmnopqrstuvwxyz123456' not in detail
    assert 'hf_xet' in detail


def test_setup_timeout_retains_partial_error_output(monkeypatch):
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired('fixture', 5, output=b'ERROR: download stalled')
    monkeypatch.setattr(remote.subprocess, 'run', run)
    with pytest.raises(ValueError, match='download stalled'):
        remote.setup_command('패키지 설치', ['fixture'], timeout=5)


def test_probe_keeps_repair_available_for_broken_venv(tmp_path, monkeypatch):
    python = tmp_path / 'venv/bin/python'; python.parent.mkdir(parents=True); python.touch()
    broken = {'ready': False, 'can_prepare': False, 'missing': ['torch'], 'cuda': False}
    monkeypatch.setattr(remote, 'setup_command', lambda *args, **kwargs: json.dumps(broken))
    assert remote.probe_runtime(tmp_path, '', {'can_prepare': True})['can_prepare'] is True


def test_probe_streams_large_runner_source_without_large_arguments(tmp_path, monkeypatch):
    python = tmp_path / 'venv/bin/python'; python.parent.mkdir(parents=True); python.touch()
    source = '# ' + 'x' * 256000 + '\nMODULES = []\ndef runtime_health(): return {"ready": True, "can_prepare": False, "detail": "정상"}\n'
    real_run = subprocess.run
    def run(args, **kwargs):
        assert max(map(len, args)) < 1024 and kwargs['input'] == source
        return real_run([sys.executable, *args[1:]], **kwargs)
    monkeypatch.setattr(remote.subprocess, 'run', run)
    result = remote.probe_runtime(tmp_path, source, {'can_prepare': True})
    assert result == {'ready': True, 'can_prepare': True, 'detail': '정상'}


def test_remote_probe_and_prepare_report_repairable_failures(monkeypatch):
    monkeypatch.setattr(remote, 'runtime_health', lambda: {'ready': True, 'can_prepare': True})
    def fail(*args): raise ValueError('패키지 설치 실패\nERROR: incompatible versions')
    monkeypatch.setattr(remote, 'prepare_runtime', fail)
    monkeypatch.setattr(remote, 'probe_runtime', fail)
    for action, kind in [('probe', 'health'), ('prepare', 'error')]:
        stream = io.StringIO()
        remote.remote_main({'action': action, 'source': ''}, stream, 'PREFIX:')
        message = json.loads(stream.getvalue().removeprefix('PREFIX:'))
        assert message['kind'] == kind
        if action == 'probe':
            assert message['data']['can_prepare'] and not message['data']['ready']
        else: assert 'ERROR: incompatible versions' in message['data']


@pytest.mark.parametrize('proxy', [False, True])
def test_setup_error_crosses_ssh_channel_even_when_terminal_remains_open(tmp_path, monkeypatch, proxy):
    stub = tmp_path / 'error_stub.py'
    stub.write_text('''import sys,json
def remote_main(boot,stream,prefix):
    print(prefix+json.dumps({'kind':'error','data':'package install failed: dependency conflict'}),flush=True)
    sys.stdin.read()
''', encoding='utf-8')
    monkeypatch.setattr(remote, '__file__', str(stub))
    shell = "import sys,shlex;line=sys.stdin.readline();exec(shlex.split(line.split(';',1)[1])[-1])"
    monkeypatch.setattr(bridge, 'ssh_arguments', lambda _connection, command: [sys.executable, '-u', '-c', shell if proxy else shlex.split(command)[-1]])
    connection = bridge.SSHConnection('root', 'ssh.runpod.io' if proxy else '203.0.113.10', 22, None)
    with pytest.raises(ValueError, match='package install failed: dependency conflict'):
        bridge.exchange(connection, 'prepare', timeout=5)


def test_prepare_api_preserves_install_error_in_health(local_client, monkeypatch):
    client, calls = local_client; headers = {'X-Modeltest-Local': '1'}
    identifier = client.post('/api/modeltest/ssh/connections', json={'command': 'ssh root@203.0.113.10'}, headers=headers).json()['connection_id']
    detail = '패키지 설치 실패\nERROR: dependency conflict'
    def fail(*args, **kwargs): raise ValueError(detail)
    monkeypatch.setattr(bridge, 'exchange', fail)
    client.post(f'/api/modeltest/ssh/connections/{identifier}/prepare', headers=headers)
    for _ in range(100):
        status = client.get(f'/api/modeltest/ssh/connections/{identifier}/health', headers=headers).json()
        if not status['preparing']: break
        time.sleep(.01)
    assert not status['preparing'] and not status['ready'] and status['can_prepare']
    assert status['detail'] == detail
