"""로컬 모델의 자동 시작은 설치·다운로드 없이 수행하고 소유한 프로세스만 정리한다."""
from unittest.mock import Mock
import subprocess

import pytest

from scripts import local_ollama as runtime
from backend.app import model_ocr


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'ROOT', tmp_path)
    monkeypatch.setenv('RUN_TARGET', 'local')
    for name in ('LEGACY_READ_ONLY', 'REQUIREMENTS_LLM_AUTOSTART', 'OCR_OLLAMA_URL', 'REQUIREMENTS_LLM_MODEL'):
        monkeypatch.delenv(name, raising=False)
    child = Mock()
    child.poll.return_value = None
    child.wait.return_value = 0
    spawn = Mock(return_value=child)
    monkeypatch.setattr(runtime.subprocess, 'Popen', spawn)
    monkeypatch.setattr(runtime, 'configuration', lambda: ('/installed/ollama', {'OLLAMA_NO_CLOUD': '1'}))
    return child, spawn


def test_new_model_server_is_ready_during_app_and_terminated_afterwards(service, monkeypatch):
    child, spawn = service
    monkeypatch.setattr(runtime, 'requirements_status', Mock(side_effect=['unavailable', 'ready']))
    with runtime.requirements_server():
        assert spawn.call_args.args[0] == ['/installed/ollama', 'serve']
        child.terminate.assert_not_called()
    child.terminate.assert_called_once()
    child.wait.assert_called_once_with(timeout=10)


@pytest.mark.parametrize('status', ['ready', 'missing_model', 'missing_vision'])
def test_existing_service_is_reused_and_never_stopped(service, monkeypatch, status):
    child, spawn = service
    monkeypatch.setattr(runtime, 'requirements_status', lambda: status)
    with runtime.requirements_server():
        pass
    spawn.assert_not_called()
    child.terminate.assert_not_called()


@pytest.mark.parametrize('name,value', [('RUN_TARGET', 'runpod'), ('LEGACY_READ_ONLY', '1'), ('REQUIREMENTS_LLM_AUTOSTART', '0')])
def test_disabled_modes_do_not_probe_or_launch(service, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    probe = Mock(side_effect=AssertionError('must not probe'))
    monkeypatch.setattr(runtime, 'requirements_status', probe)
    with runtime.requirements_server():
        pass
    service[1].assert_not_called()


def test_custom_origin_is_not_replaced_by_default_service(service, monkeypatch):
    monkeypatch.setenv('OCR_OLLAMA_URL', 'http://127.0.0.1:11434')
    monkeypatch.setattr(runtime, 'requirements_status', lambda: 'unavailable')
    with runtime.requirements_server():
        pass
    service[1].assert_not_called()


def test_spawn_failure_does_not_block_poster_app(service, monkeypatch):
    service[1].side_effect = OSError('not installed')
    monkeypatch.setattr(runtime, 'requirements_status', lambda: 'unavailable')
    with runtime.requirements_server():
        pass


def test_owned_server_is_cleaned_up_when_app_fails(service, monkeypatch):
    child, _ = service
    monkeypatch.setattr(runtime, 'requirements_status', Mock(side_effect=['unavailable', 'ready']))
    child.wait.side_effect = [subprocess.TimeoutExpired('ollama', 10), 0]
    with pytest.raises(RuntimeError, match='app failed'):
        with runtime.requirements_server():
            raise RuntimeError('app failed')
    child.terminate.assert_called_once()
    child.kill.assert_called_once()


@pytest.mark.parametrize('models,status', [([], 'missing_model'), ([{'name': 'qwen3.5:4b'}], 'ready'), (None, 'unavailable')])
def test_readiness_checks_installed_model_without_inference(service, monkeypatch, models, status):
    request = Mock(side_effect=[{'models': models}, {'capabilities': ['completion', 'vision']}])
    monkeypatch.setattr(model_ocr, 'ollama_request', request)
    assert runtime.requirements_status() == status
    assert request.call_args_list[0].args == ('/api/tags',)
    assert request.call_count == (2 if status == 'ready' else 1)


def test_installed_text_only_model_fails_vision_readiness(service, monkeypatch):
    monkeypatch.setenv('REQUIREMENTS_LLM_MODEL', 'text-only:4b')
    request = Mock(side_effect=[{'models': [{'name': 'text-only:4b'}]}, {'capabilities': ['completion']}])
    monkeypatch.setattr(model_ocr, 'ollama_request', request)
    assert runtime.requirements_status() == 'missing_vision'


def test_pull_uses_same_configured_model_as_extractor(service, monkeypatch):
    monkeypatch.setenv('REQUIREMENTS_LLM_MODEL', 'custom-vision:4b')
    monkeypatch.setattr(runtime.sys, 'argv', ['local_ollama.py', 'pull'])
    call = Mock(return_value=0)
    monkeypatch.setattr(runtime.subprocess, 'call', call)
    assert runtime.main() == 0
    assert call.call_args.args[0] == ['/installed/ollama', 'pull', 'custom-vision:4b']


def test_unreachable_model_has_distinct_status(service, monkeypatch):
    monkeypatch.setattr(model_ocr, 'ollama_request', Mock(side_effect=ConnectionError('private details')))
    assert runtime.requirements_status() == 'unavailable'
