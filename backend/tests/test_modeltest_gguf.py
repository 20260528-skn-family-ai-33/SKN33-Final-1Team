"""Offline GGUF transport and owned-process checks; no GPU, downloads or real server."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from backend.app import modeltest_remote as remote


def test_registered_gguf_requires_prepared_binary_without_build_or_download(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs): pytest.fail('sample must not build or download')
    monkeypatch.setattr(sys, 'prefix', str(tmp_path))
    monkeypatch.setattr(remote, 'ensure_llama_server', unexpected)
    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(model_info=unexpected, hf_hub_download=unexpected))
    with pytest.raises(ValueError, match='준비가 필요'):
        remote.GGUFServer('a' * 40, local_files_only=True)


def test_paddle_gguf_sends_official_ocr_prompt_with_image_and_handles_truncation(tmp_path):
    image = tmp_path / 'image.png'; Image.new('RGB', (20, 20)).save(image)
    server = remote.GGUFServer.__new__(remote.GGUFServer); requests = []
    def request(path, payload):
        requests.append((path, payload)); return {'choices': [{'message': {'content': [{'type': 'text', 'text': '인식 문구'}]}, 'finish_reason': 'length'}]}
    server.request = request
    result = remote.run_paddle_gguf(SimpleNamespace(gguf=server), {'id': 'text_extract', 'model_id': remote.PADDLE_GGUF}, images=[image])
    assert result['text'] == '인식 문구' and '길이 제한' in result['note']
    path, payload = requests[0]
    assert path == '/v1/chat/completions' and payload['temperature'] == 0
    assert payload['messages'][0]['content'][1]['text'] == 'OCR:'
    assert payload['messages'][0]['content'][0]['image_url']['url'].startswith('data:image/jpeg;base64,')
    remote.run_paddle_gguf(SimpleNamespace(gguf=server), {'id': 'text_extract', 'model_id': remote.PADDLE_GGUF, 'prompt_override': 'Read the headline only'}, images=[image])
    assert requests[-1][1]['messages'][0]['content'][1]['text'] == 'Read the headline only'
    remote.validate_model_stage({'id': 'ocr-step', 'kind': 'text_extract', 'model_id': remote.PADDLE_GGUF})
    with pytest.raises(ValueError): remote.validate_model_stage({'id': 'conditions', 'model_id': remote.PADDLE_GGUF})
    server.request = lambda *_: {'choices': []}
    with pytest.raises(ValueError, match='응답 형식'): server.ocr(image)


def test_gguf_build_is_pinned_scoped_and_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'prefix', str(tmp_path)); monkeypatch.setattr(sys, 'executable', str(tmp_path / 'bin/python'))
    monkeypatch.setattr(remote.shutil, 'which', lambda name: '/usr/bin/' + name)
    calls = []
    def command(label, args, **kwargs):
        calls.append(args)
        if '--build' in args:
            binary = Path(args[2]) / 'bin/llama-server'; binary.parent.mkdir(parents=True); binary.write_text('fixture')
        return ''
    monkeypatch.setattr(remote, 'setup_command', command)
    binary = remote.ensure_llama_server(); count = len(calls)
    assert binary.is_relative_to(tmp_path)
    assert any('checkout' in args and remote.LLAMA_COMMIT in args for args in calls)
    assert any('-DGGML_CUDA=ON' in args and '-DLLAMA_OPENSSL=OFF' in args for args in calls)
    assert remote.ensure_llama_server() == binary and len(calls) == count


@pytest.mark.parametrize('ready', [True, False])
def test_gguf_process_uses_matching_weights_loopback_and_owned_cleanup(tmp_path, monkeypatch, ready):
    downloads = []; processes = []
    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(model_info=lambda *args, **kwargs: SimpleNamespace(sha='fixture-revision'),
        hf_hub_download=lambda repo, name, revision: downloads.append((name, revision)) or str(tmp_path / name)))
    monkeypatch.setattr(remote, 'ensure_llama_server', lambda: tmp_path / 'llama-server')
    class Process:
        returncode = None
        def poll(self): return self.returncode
        def terminate(self): self.returncode = 0
        def wait(self, **kwargs): return 0
    def spawn(args, **kwargs):
        process = Process(); processes.append((args, kwargs, process)); return process
    monkeypatch.setattr(remote.subprocess, 'Popen', spawn)
    def health(*args, **kwargs):
        if not ready: raise ValueError('fixture startup failure')
        return {'status': 'ok'}
    monkeypatch.setattr(remote.GGUFServer, 'request', health)
    if not ready:
        with pytest.raises(ValueError, match='fixture startup failure'): remote.GGUFServer()
        assert processes[0][2].returncode == 0
        assert processes[0][1]['stdout'].closed
        return
    server = remote.GGUFServer('requested-revision'); process = server.process
    assert downloads == [('PaddleOCR-VL-1.6-GGUF.gguf', 'fixture-revision'), ('PaddleOCR-VL-1.6-GGUF-mmproj.gguf', 'fixture-revision')]
    args, options, _ = processes[0]
    assert args[args.index('--host') + 1] == '127.0.0.1' and '--mmproj' in args
    assert options['env']['LLAMA_API_KEY'] == server.token and not options.get('start_new_session')
    server.close(); assert process.returncode == 0 and server.process is None and server.log is None
