"""Exercise the executable boundaries that module moves can silently break."""
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from backend.app.modeltest.ssh import runner_source


@pytest.mark.parametrize(('old', 'current'), [
    ('adaptive_ocr', 'ocr.adaptive'),
    ('extract_event', 'reviews.events'),
    ('ocr_runtime', 'ocr.runtime'),
    ('requirements_pdf', 'documents.pdf'),
    ('paddle_worker', 'ocr.paddle_worker'),
    ('paddle_full_worker', 'ocr.paddle_full_worker'),
    ('paddle_condition_worker', 'ocr.paddle_condition_worker'),
    ('paddle_video_worker', 'ocr.paddle_video_worker'),
    ('paddle_ocr_server', 'ocr.paddle_server'),
    ('runpod_worker', 'ocr.runpod_worker'),
    ('logo_runpod_worker', 'logos.runpod_worker'),
    ('video_logo_worker', 'video.logos.worker'),
])
def test_existing_worker_entrypoints_resolve_to_the_implementation(old, current):
    assert importlib.import_module('backend.app.' + old) is importlib.import_module('backend.app.' + current)


@pytest.mark.parametrize('module', ['adaptive_ocr', 'extract_event', 'paddle_full_worker'])
def test_existing_cli_help_does_not_load_models(module):
    result = subprocess.run(
        [sys.executable, '-m', 'backend.app.' + module, '--help'],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        encoding='utf-8', timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert '--input' in result.stdout


def test_ssh_bundle_runs_without_the_repository_or_model_packages(tmp_path):
    # -I and an external cwd exercise the same import boundary as a bare Pod.
    script = '''
import json, sys
scope = {'__name__': 'modeltest_remote'}
exec(compile(sys.stdin.read(), '<ssh-bundle>', 'exec'), scope)
scope['validate_config']({'contract': 'modeltest.v1', 'flow': 'image',
    'requirements': 'Keep the title', 'stages': [{'id': 'conditions', 'model_id': scope['QWEN']}],
    'files': {'media': {'name': 'poster.png', 'size': 5, 'type': ''},
              'documents': [{'name': 'contract.txt', 'size': 4, 'type': ''}], 'references': []}})
assert callable(scope['adapters'].logo_diff_pair)
assert 'backend' not in sys.modules
assert not {'torch', 'transformers', 'paddleocr'} & sys.modules.keys()
print(json.dumps({'contract': scope['CONTRACT'], 'ready': True}))
'''
    result = subprocess.run(
        [sys.executable, '-I', '-X', 'utf8', '-c', script], cwd=tmp_path, input=runner_source(),
        capture_output=True, text=True, encoding='utf-8', timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'contract': 'modeltest.v1', 'ready': True}
