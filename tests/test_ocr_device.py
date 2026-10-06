"""Hardware policy and accelerator failures; no real GPU or network required."""
from types import SimpleNamespace
import sys

from PIL import Image
import pytest

from app import ocr, ocr_device, ocr_models
from app.inspector import inspect_image


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.delenv('LATTE_OCR_BACKEND', raising=False)
    monkeypatch.delenv('LATTE_OCR_DEVICE', raising=False)
    availability = ocr_device.accelerator_availability
    availability.cache_clear()
    cache = ocr._rapid_engine
    cache.cache_clear()
    yield
    cache.cache_clear()
    availability.cache_clear()


@pytest.mark.parametrize('system,machine,cuda,mps,expected', [
    ('Windows', 'AMD64', True, False, 'cuda'),
    ('Windows', 'AMD64', False, False, 'cpu'),
    ('Linux', 'x86_64', True, False, 'cuda'),
    ('Linux', 'x86_64', False, False, 'cpu'),
    ('Darwin', 'arm64', False, True, 'mps'),
    ('Darwin', 'arm64', False, False, 'cpu'),
    ('Darwin', 'x86_64', False, True, 'cpu'),
    ('Darwin', 'x86_64', True, False, 'cpu'),
])
def test_auto_selects_only_supported_available_device(monkeypatch, system, machine, cuda, mps, expected):
    monkeypatch.setattr(ocr_device.platform, 'system', lambda: system)
    monkeypatch.setattr(ocr_device.platform, 'machine', lambda: machine)
    monkeypatch.setattr(ocr_device, 'accelerator_availability', lambda: {'cuda': cuda, 'mps': mps})
    selection = ocr_device.select_device()
    assert selection.requested == 'auto'
    assert selection.device == expected


def test_explicit_cpu_never_imports_or_probes_gpu(monkeypatch):
    monkeypatch.setenv('LATTE_OCR_DEVICE', 'cpu')
    monkeypatch.setattr(ocr_device, 'accelerator_availability', lambda: pytest.fail('CPU must not need Torch'))
    assert ocr_device.select_device().device == 'cpu'


@pytest.mark.parametrize('system,machine,requested', [
    ('Windows', 'AMD64', 'mps'), ('Darwin', 'arm64', 'cuda'), ('Darwin', 'x86_64', 'mps'),
])
def test_incompatible_explicit_device_falls_back_with_reason(monkeypatch, system, machine, requested):
    monkeypatch.setattr(ocr_device.platform, 'system', lambda: system)
    monkeypatch.setattr(ocr_device.platform, 'machine', lambda: machine)
    monkeypatch.setenv('LATTE_OCR_DEVICE', requested)
    selection = ocr_device.select_device()
    assert selection.device == 'cpu'
    assert selection.reason


def test_invalid_device_configuration_is_not_silently_ignored(monkeypatch):
    monkeypatch.setenv('LATTE_OCR_DEVICE', 'gpu')
    with pytest.raises(ValueError, match='LATTE_OCR_DEVICE'):
        ocr_device.select_device()
    assert ocr.method_name() == 'ocr/unconfigured'


@pytest.mark.parametrize('failure', ['load', 'inference'])
def test_gpu_failure_retries_cpu_and_reports_actual_device(monkeypatch, tmp_path, failure):
    path = tmp_path / '딸기 라떼.png'
    Image.new('RGB', (100, 50)).save(path)
    monkeypatch.setattr(ocr, 'select_device', lambda: ocr_device.DeviceSelection('auto', 'mps'))
    calls = []
    result = SimpleNamespace(boxes=[[[10, 10], [30, 10], [30, 20], [10, 20]]], txts=['딸기라떼'], scores=[.95])
    def engine(directory, device='cpu'):
        calls.append(device)
        if device == 'mps':
            if failure == 'load':
                raise RuntimeError('GPU initialization failed')
            def fail(_):
                raise RuntimeError('GPU out of memory')
            return fail
        return lambda _: result
    monkeypatch.setattr(ocr, '_rapid_engine', engine)
    payload = ocr._read_rapid(path)
    assert calls == ['mps', 'cpu']
    assert payload['device'] == 'cpu'
    assert payload['device_selected'] == 'mps'
    assert payload['method'] == ocr.METHODS['rapidocr']
    assert 'MPS 실행 실패로 CPU 전환' in payload['fallback_reason']
    assert payload['lines'][0]['text'] == '딸기라떼'


def test_failed_cpu_retry_does_not_generate_successful_inspection(monkeypatch, tmp_path):
    path = tmp_path / 'failed.png'
    Image.new('RGB', (100, 50)).save(path)
    monkeypatch.setattr(ocr, 'select_device', lambda: ocr_device.DeviceSelection('auto', 'cuda'))
    monkeypatch.setattr('app.inspector.ALLOWED_ROOTS', (tmp_path,))
    def unavailable(*args, **kwargs):
        raise RuntimeError('Both engines unavailable')
    monkeypatch.setattr(ocr, '_rapid_engine', unavailable)
    result = inspect_image(path, [{'id': 'C01', 'expected': '딸기라떼'}])
    assert result['trace'][0]['status'] == 'failed'
    assert result['results'][0]['execution_status'] == 'failed'
    assert result['results'][0]['verdict'] is None


@pytest.mark.parametrize('device', ['cuda', 'mps'])
def test_gpu_engine_uses_verified_torch_models_and_local_korean_dictionary(monkeypatch, tmp_path, device):
    paths = {key: tmp_path / filename for key, filename in
             [('det', 'det.pth'), ('cls', 'cls.pth'), ('rec', 'rec.pth'), ('dict', 'korean_dict.txt')]}
    def verified(*, verify, profile):
        assert verify is True and profile == 'torch'
        return paths
    params_seen = []
    session = SimpleNamespace(session=SimpleNamespace(device=SimpleNamespace(type=device)))
    def create(*, params):
        params_seen.append(params)
        return SimpleNamespace(text_det=session, text_cls=session, text_rec=session)
    fake = SimpleNamespace(RapidOCR=create, EngineType=SimpleNamespace(ONNXRUNTIME='onnx', TORCH='torch'),
                           OCRVersion=SimpleNamespace(PPOCRV5='v5', PPOCRV4='v4'),
                           LangDet=SimpleNamespace(CH='ch'), LangRec=SimpleNamespace(KOREAN='korean'),
                           ModelType=SimpleNamespace(MOBILE='mobile'))
    monkeypatch.setattr(ocr, 'model_paths', verified)
    monkeypatch.setattr('app.ocr_rec.add_english_recognizer', lambda engine, paths: None)
    monkeypatch.setitem(sys.modules, 'rapidocr', fake)
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(set_num_threads=lambda _: None))
    ocr._rapid_engine(str(tmp_path), device)
    params = params_seen[0]
    assert params['Rec.rec_keys_path'] == str(paths['dict'])
    assert params['Rec.ocr_version'] == 'v4'
    assert params['Det.ocr_version'] == 'v5'
    assert all(params[f'{stage}.engine_type'] == 'torch' for stage in ('Det', 'Cls', 'Rec'))
    assert params['EngineConfig.torch.use_cuda'] == (device == 'cuda')
    assert params['EngineConfig.torch.use_mps'] == (device == 'mps')


def test_gpu_missing_models_health_labels_cpu_fallback(monkeypatch):
    monkeypatch.setattr(ocr, 'select_device', lambda: ocr_device.DeviceSelection('auto', 'mps'))
    monkeypatch.setattr(ocr.importlib.util, 'find_spec', lambda _: object())
    monkeypatch.setattr(ocr, 'models_ready', lambda profile='onnx': profile == 'onnx')
    capabilities = ocr.get_capabilities()
    assert capabilities['ocr_available']
    assert capabilities['ocr_device'] == 'cpu'
    assert capabilities['ocr_device_selected'] == 'mps'
    assert 'GPU 모델' in capabilities['ocr_fallback_reason']


def test_inspection_preserves_actual_cpu_fallback_and_manual_method(monkeypatch, tmp_path):
    monkeypatch.setattr('app.inspector.read_ocr', lambda _: {
        'method': ocr.METHODS['rapidocr'], 'device': 'cpu', 'fallback_reason': 'MPS failed',
        'lines': [{'text': '딸기라떼', 'confidence': .9, 'bbox': [.1, .1, .3, .1]}]})
    monkeypatch.setattr(ocr, 'select_device', lambda: ocr_device.DeviceSelection('auto', 'mps'))
    result = inspect_image(tmp_path / 'unused.png', [{'id': 'C01', 'expected': '딸기라떼'}, {'id': 'C04', 'expected': 3}])
    assert result['method'] == ocr.METHODS['rapidocr']
    assert result['results'][0]['method'] == ocr.METHODS['rapidocr']
    assert result['results'][1]['method'] == 'manual-review-required'
    assert result['trace'][0]['device'] == 'cpu'
    assert result['trace'][0]['fallback_reason'] == 'MPS failed'


def test_mcp_forwards_device_selection(monkeypatch):
    from app.mcp_client import mcp_environment
    monkeypatch.setenv('LATTE_OCR_DEVICE', 'cuda')
    assert mcp_environment()['LATTE_OCR_DEVICE'] == 'cuda'


def test_missing_torch_returns_cpu_without_crashing(monkeypatch):
    monkeypatch.setitem(sys.modules, 'torch', None)
    assert ocr_device.accelerator_availability() == {'cuda': False, 'mps': False}


@pytest.mark.parametrize('english_text,english_score,replaced', [
    ('MORNING CUP', .95, True), ('MORNING CUP', .3, False), ('한국어', .99, False),
])
def test_english_routing_preserves_korean_and_does_not_use_expected_text(english_text, english_score, replaced):
    from app.ocr_rec import KoreanEnglishRecognizer
    primary = SimpleNamespace(imgs=['korean crop', 'latin crop'], txts=('딸기라떼', 'MORNIG CUP'),
                              scores=[.9, .8], word_results=(None, None), elapse=.1)
    english = SimpleNamespace(txts=(english_text,), scores=[english_score], word_results=(None,), elapse=.05)
    calls = []
    def english_infer(args):
        calls.append(args.img)
        return english
    korean = type('KoreanStub', (), {'session': object(), '__call__': lambda self, args: primary})()
    result = KoreanEnglishRecognizer(korean, english_infer)(SimpleNamespace(return_word_box=False))
    assert calls == [['latin crop']]
    assert result.txts[0] == '딸기라떼'
    assert result.scores[0] == .9
    assert result.txts[1] == (english_text if replaced else 'MORNIG CUP')


def test_korean_only_results_do_not_call_english_model():
    from app.ocr_rec import KoreanEnglishRecognizer
    primary = SimpleNamespace(imgs=['crop'], txts=('30%할인',), scores=[.95], word_results=(None,), elapse=.1)
    korean = type('KoreanStub', (), {'session': object(), '__call__': lambda self, args: primary})()
    def unexpected(_):
        pytest.fail('Korean text must not be passed to the English alphabet recognizer')
    assert KoreanEnglishRecognizer(korean, unexpected)(SimpleNamespace(return_word_box=False)) is primary
