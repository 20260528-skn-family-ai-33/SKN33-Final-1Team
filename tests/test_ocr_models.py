"""Model setup uses tiny mock downloads; these tests never download real models."""
import hashlib
import io
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from app import ocr_models as models


@pytest.fixture
def cache(monkeypatch, tmp_path):
    contents = {name: f'test-only-{name}-model'.encode() for name in ('det', 'cls', 'rec')}
    manifest = {
        name: {'filename': f'{name}.onnx', 'url': f'https://models.example.test/{name}.onnx',
               'sha256': hashlib.sha256(content).hexdigest()}
        for name, content in contents.items()
    }
    directory = tmp_path / '한글 모델 캐시'
    monkeypatch.setenv('LATTE_OCR_MODEL_DIR', str(directory))
    monkeypatch.setattr(models, '_MODELS', manifest)
    monkeypatch.setattr(models, 'build_opener', lambda *a: pytest.fail('Unexpected network setup'))
    return SimpleNamespace(directory=directory, contents=contents, manifest=manifest)


class MockResponse(io.BytesIO):
    def __init__(self, content, url):
        super().__init__(content)
        self.url = url
    def geturl(self):
        return self.url


def mock_downloads(monkeypatch, cache, *, transform=lambda content: content):
    requests = []
    def open_request(request, *, timeout):
        requests.append(request.full_url)
        assert timeout == 30
        name = request.full_url.rsplit('/', 1)[-1].split('.')[0]
        return MockResponse(transform(cache.contents[name]), request.full_url)
    monkeypatch.setattr(models, 'build_opener', lambda *a: SimpleNamespace(open=open_request))
    return requests


def write_valid_cache(cache):
    cache.directory.mkdir(parents=True)
    for name, content in cache.contents.items():
        (cache.directory / cache.manifest[name]['filename']).write_bytes(content)


def test_readiness_does_not_create_directories_or_download_missing_models(cache):
    assert models.model_directory() == cache.directory
    assert not models.models_ready()
    assert not cache.directory.exists()
    with pytest.raises(RuntimeError, match='준비되지'):
        models.model_paths()
    assert not cache.directory.exists()


def test_torch_readiness_requires_separate_models_and_dictionary(monkeypatch, cache):
    content = b'gpu-recognizer'
    dictionary = b'local-korean-dictionary'
    manifest = {
        'rec': {'filename': 'gpu-rec.pth', 'url': 'https://models.example.test/gpu-rec.pth',
                'sha256': hashlib.sha256(content).hexdigest()},
        'dict': {'filename': 'korean_dict.txt', 'url': 'https://models.example.test/korean_dict.txt',
                 'sha256': hashlib.sha256(dictionary).hexdigest()},
    }
    monkeypatch.setattr(models, '_TORCH_MODELS', manifest)
    write_valid_cache(cache)
    assert models.models_ready('onnx')
    assert not models.models_ready('torch')
    (cache.directory / 'gpu-rec.pth').write_bytes(content)
    assert not models.models_ready('torch')
    (cache.directory / 'korean_dict.txt').write_bytes(dictionary)
    assert models.models_ready('torch')
    assert models.model_paths(profile='torch')['dict'].read_bytes() == dictionary
    (cache.directory / 'korean_dict.txt').write_bytes(b'corrupt dictionary')
    with pytest.raises(RuntimeError, match='SHA-256.*korean_dict.txt'):
        models.model_paths(profile='torch')


def test_missing_one_of_three_models_blocks_readiness_and_loading(cache):
    write_valid_cache(cache)
    (cache.directory / 'rec.onnx').unlink()
    assert not models.models_ready()
    with pytest.raises(RuntimeError, match='rec.onnx'):
        models.model_paths()


def test_corrupted_local_model_fails_hash_validation_even_when_files_exist(cache):
    write_valid_cache(cache)
    (cache.directory / 'rec.onnx').write_bytes(b'corrupted recognizer')
    # The status endpoint intentionally probes presence; loading verifies exact contents.
    assert models.models_ready()
    with pytest.raises(RuntimeError, match='SHA-256.*rec.onnx'):
        models.model_paths()


def test_preparation_downloads_to_same_directory_then_atomically_publishes(monkeypatch, cache):
    requests = mock_downloads(monkeypatch, cache)
    real_replace = models.os.replace
    replaced = []
    def verify_replace(source, destination):
        assert source.parent == destination.parent == cache.directory
        assert source.suffix == '.download'
        assert not destination.exists()
        name = destination.stem
        assert source.read_bytes() == cache.contents[name]
        replaced.append(name)
        return real_replace(source, destination)
    monkeypatch.setattr(models.os, 'replace', verify_replace)
    paths = models.prepare_models()
    assert len(requests) == 3
    assert set(replaced) == {'det', 'cls', 'rec'}
    assert {name: path.read_bytes() for name, path in paths.items()} == cache.contents
    assert sorted(path.name for path in cache.directory.iterdir()) == ['cls.onnx', 'det.onnx', 'rec.onnx']


def test_valid_cache_is_reused_without_http_requests(monkeypatch, cache):
    write_valid_cache(cache)
    monkeypatch.setattr(models, 'build_opener', lambda *a: SimpleNamespace(
        open=lambda *a, **k: pytest.fail('Valid model cache must not be downloaded again')))
    before = {path.name: path.stat().st_mtime_ns for path in cache.directory.iterdir()}
    assert set(models.prepare_models()) == {'det', 'cls', 'rec'}
    assert {path.name: path.stat().st_mtime_ns for path in cache.directory.iterdir()} == before


def test_only_corrupt_model_is_downloaded_and_replaced(monkeypatch, cache):
    write_valid_cache(cache)
    (cache.directory / 'rec.onnx').write_bytes(b'corrupt')
    requests = mock_downloads(monkeypatch, cache)
    models.prepare_models()
    assert requests == [cache.manifest['rec']['url']]
    assert (cache.directory / 'rec.onnx').read_bytes() == cache.contents['rec']


def test_bad_download_hash_keeps_existing_file_and_removes_partial_download(monkeypatch, cache):
    write_valid_cache(cache)
    destination = cache.directory / 'rec.onnx'
    destination.write_bytes(b'old corrupt file')
    mock_downloads(monkeypatch, cache, transform=lambda _: b'incorrect replacement')
    with pytest.raises(RuntimeError, match='SHA-256'):
        models.prepare_models()
    assert destination.read_bytes() == b'old corrupt file'
    assert not list(cache.directory.glob('*.download'))


@pytest.mark.parametrize('failure', ['read', 'oversize', 'replace'])
def test_setup_failure_removes_temporary_files(monkeypatch, cache, failure):
    requests = mock_downloads(monkeypatch, cache)
    if failure == 'read':
        class InterruptedResponse(MockResponse):
            def read(self, size=-1):
                if self.tell():
                    raise OSError('interrupted download')
                return super().read(3)
        monkeypatch.setattr(models, 'build_opener', lambda *a: SimpleNamespace(
            open=lambda request, **k: InterruptedResponse(cache.contents['det'], request.full_url)))
    elif failure == 'oversize':
        monkeypatch.setattr(models, '_MAX_DOWNLOAD_BYTES', 1)
    else:
        def fail_replace(*a):
            raise PermissionError('replace denied')
        monkeypatch.setattr(models.os, 'replace', fail_replace)
    with pytest.raises(RuntimeError, match='준비에 실패'):
        models.prepare_models()
    assert list(cache.directory.iterdir()) == []


def test_non_https_final_download_url_is_rejected_before_writing(monkeypatch, cache):
    monkeypatch.setattr(models, 'build_opener', lambda *a: SimpleNamespace(
        open=lambda request, **k: MockResponse(cache.contents['det'], 'http://insecure.example.test/model')))
    with pytest.raises(RuntimeError, match='HTTPS'):
        models.prepare_models()
    assert list(cache.directory.iterdir()) == []


def test_redirect_handler_rejects_http_downgrade():
    handler = models._HTTPSRedirectHandler()
    with pytest.raises(HTTPError, match='HTTPS'):
        handler.redirect_request(Request('https://models.example.test/model'), None, 302, 'Found', {},
                                 'http://insecure.example.test/model')
