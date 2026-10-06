"""Portable OCR contracts; all inference/model loading is stubbed locally."""
import copy
import sys
from types import SimpleNamespace

from PIL import Image
import pytest

from app import ocr
from app.inspector import inspect_text_rules


@pytest.fixture(autouse=True)
def isolate_backend(monkeypatch):
    monkeypatch.delenv('LATTE_OCR_BACKEND', raising=False)
    monkeypatch.setenv('LATTE_OCR_DEVICE', 'cpu')
    cached_engine = ocr._rapid_engine
    cached_engine.cache_clear()
    yield
    cached_engine.cache_clear()


@pytest.mark.parametrize('system', ['Darwin', 'Windows'])
def test_both_platforms_default_to_the_same_rapidocr_backend(monkeypatch, system):
    monkeypatch.setattr(ocr.platform, 'system', lambda: system)
    assert ocr.selected_backend() == 'rapidocr'
    assert ocr.method_name() == 'rapidocr-ppocrv5-korean/onnx-cpu-v1'


def test_apple_on_windows_fails_before_launching_a_process(monkeypatch, tmp_path):
    monkeypatch.setenv('LATTE_OCR_BACKEND', 'apple')
    monkeypatch.setattr(ocr.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(ocr.subprocess, 'run', lambda *a, **k: pytest.fail('Must not launch Apple OCR'))
    with pytest.raises(RuntimeError, match='Mac 전용'):
        ocr.read_image(tmp_path / 'unused.png')
    assert ocr.get_capabilities()['ocr_available'] is False


@pytest.mark.parametrize('installed,ready', [(False, False), (False, True), (True, False), (True, True)])
def test_capabilities_distinguish_packages_and_prepared_models(monkeypatch, installed, ready):
    monkeypatch.setattr(ocr.importlib.util, 'find_spec', lambda _: object() if installed else None)
    monkeypatch.setattr(ocr, 'models_ready', lambda: ready)
    capability = ocr.get_capabilities()
    assert capability['ocr_installed'] is installed
    assert capability['ocr_ready'] is ready
    assert capability['ocr_available'] is (installed and ready)
    assert capability['supported_rules'] == (['C01', 'C02', 'C03'] if installed and ready else [])
    assert capability['manual_review_rules'] == ['C04', 'C05']
    assert capability['external_transfer'] is False


def test_unknown_backend_has_an_explicit_failure(monkeypatch, tmp_path):
    monkeypatch.setenv('LATTE_OCR_BACKEND', 'unrecognized')
    assert not ocr.get_capabilities()['ocr_available']
    with pytest.raises(RuntimeError, match='rapidocr 또는 apple'):
        ocr.read_image(tmp_path / 'unused.png')


def test_normalization_clips_to_original_image_and_sorts_top_to_bottom():
    boxes = [
        [[30, 30], [70, 30], [70, 40], [30, 40]],
        [[-5, -2], [110, -2], [110, 10], [-5, 10]],
    ]
    lines = ocr.normalize_lines(boxes, ['할인', '  딸기라떼  '], [.6, .95], 100, 50)
    assert [line['text'] for line in lines] == ['딸기라떼', '할인']
    assert lines[0]['bbox'] == pytest.approx([0, 0, 1, .2])
    assert lines[1]['bbox'] == pytest.approx([.3, .6, .4, .2])
    assert lines[1]['confidence'] == .6


def test_empty_ocr_response_is_empty_not_a_synthetic_match():
    assert ocr.normalize_lines(None, None, None, 100, 50) == []
    assert ocr.normalize_lines([], [], [], 100, 50) == []


@pytest.mark.parametrize('boxes,texts,scores', [([], ['text'], []), (None, [], []), ([], None, []), ([], [], None)])
def test_mismatched_response_parts_are_rejected(boxes, texts, scores):
    with pytest.raises(ValueError, match='일치하지'):
        ocr.normalize_lines(boxes, texts, scores, 100, 50)


@pytest.mark.parametrize('box', [
    [[float('nan'), 0], [10, 0], [10, 10], [0, 10]],
    [[float('inf'), 0], [10, 0], [10, 10], [0, 10]],
    [[5, 0], [5, 0], [5, 10], [5, 10]],
    [[200, 0], [210, 0], [210, 10], [200, 10]],
    [[0, 0], [10, 0], [10, 10]],
])
def test_nonfinite_degenerate_and_outside_boxes_are_rejected(box):
    with pytest.raises(ValueError, match='좌표|영역'):
        ocr.normalize_lines([box], ['text'], [.9], 100, 50)


@pytest.mark.parametrize('score', [float('nan'), float('inf'), -.01, 1.01])
def test_invalid_confidence_is_rejected(score):
    with pytest.raises(ValueError, match='신뢰'):
        ocr.normalize_lines([[[0, 0], [10, 0], [10, 10], [0, 10]]], ['text'], [score], 100, 50)


def test_close_fragments_merge_without_losing_low_confidence_or_mutating_input():
    fragments = [
        {'text': '할인', 'bbox': [.26, .2, .1, .05], 'confidence': .4},
        {'text': '30%', 'bbox': [.1, .2, .15, .05], 'confidence': .99},
    ]
    before = copy.deepcopy(fragments)
    merged = ocr.merge_same_line_fragments(fragments)
    assert fragments == before
    assert len(merged) == 1
    assert merged[0]['text'] == '30% 할인'
    assert merged[0]['confidence'] == .4
    assert merged[0]['bbox'] == pytest.approx([.1, .2, .26, .05])
    result = inspect_text_rules(merged, [{'id': 'C02', 'expected': '30% 할인'}])[0]
    assert result['verdict'] == '확인 필요'


@pytest.mark.parametrize('second_box', [[.8, .1, .1, .05], [.26, .3, .1, .05], [.26, .1, .1, .02]])
def test_distant_different_row_or_different_size_fragments_stay_separate(second_box):
    fragments = [
        {'text': 'MORNING', 'bbox': [.1, .1, .15, .05], 'confidence': .99},
        {'text': 'CUP', 'bbox': second_box, 'confidence': .99},
    ]
    assert len(ocr.merge_same_line_fragments(fragments)) == 2


def test_rapid_engine_requires_verified_local_paths_for_all_three_models(monkeypatch, tmp_path, capsys):
    paths = {name: tmp_path / f'{name}.onnx' for name in ('det', 'cls', 'rec')}
    calls = []
    def verified_paths(*, verify):
        assert verify is True
        calls.append('verified')
        return paths
    def make_engine(*, params):
        calls.append(params)
        print('model initialization diagnostic')
        return object()
    fake_rapidocr = SimpleNamespace(
        RapidOCR=make_engine,
        EngineType=SimpleNamespace(ONNXRUNTIME='onnx'),
        OCRVersion=SimpleNamespace(PPOCRV5='v5', PPOCRV4='v4'),
        LangDet=SimpleNamespace(CH='ch'), LangRec=SimpleNamespace(KOREAN='korean'),
        ModelType=SimpleNamespace(MOBILE='mobile'),
    )
    monkeypatch.setattr(ocr, 'model_paths', verified_paths)
    monkeypatch.setitem(sys.modules, 'rapidocr', fake_rapidocr)
    engine = ocr._rapid_engine(str(tmp_path))
    assert ocr._rapid_engine(str(tmp_path)) is engine
    assert calls[0] == 'verified'
    params = calls[1]
    assert len(calls) == 2
    assert {params[f'{stage}.model_path'] for stage in ('Det', 'Cls', 'Rec')} == {str(path) for path in paths.values()}
    assert params['Rec.lang_type'] == 'korean'
    assert params['Global.text_score'] == 0
    assert params['Global.use_cls'] is False
    assert not params['EngineConfig.onnxruntime.use_cuda']
    assert not params['EngineConfig.onnxruntime.use_dml']
    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'model initialization diagnostic' in captured.err


def test_model_validation_failure_precedes_any_rapidocr_import_or_initialization(monkeypatch, tmp_path):
    def fail_models(*, verify):
        assert verify is True
        raise RuntimeError('OCR 모델의 SHA-256 검증에 실패했습니다')
    monkeypatch.setattr(ocr, 'model_paths', fail_models)
    monkeypatch.setitem(sys.modules, 'rapidocr', SimpleNamespace(
        RapidOCR=lambda **k: pytest.fail('Must validate models before creating the engine')))
    with pytest.raises(RuntimeError, match='SHA-256'):
        ocr._rapid_engine(str(tmp_path))


def test_rapidocr_receives_bytes_for_korean_paths_and_keeps_protocol_stdout_clean(monkeypatch, tmp_path, capsys):
    directory = tmp_path / '한글 경로'
    directory.mkdir()
    path = directory / '딸기 라떼.png'
    Image.new('RGB', (100, 50), 'white').save(path)
    def infer(image_bytes):
        assert image_bytes == path.read_bytes()
        print('inference diagnostic')
        return SimpleNamespace(
            boxes=[[[10, 10], [30, 10], [30, 20], [10, 20]]], txts=['딸기라떼'], scores=[.95])
    monkeypatch.setattr(ocr, '_rapid_engine', lambda _: infer)
    monkeypatch.setattr(ocr, 'get_capabilities', lambda: {'ocr_engine': 'stub local OCR'})
    payload = ocr._read_rapid(path)
    assert payload['coordinate_system'] == 'normalized_top_left_xywh'
    assert payload['lines'][0]['bbox'] == pytest.approx([.1, .2, .2, .2])
    assert payload['lines'][0]['text'] == '딸기라떼'
    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'inference diagnostic' in captured.err


def test_fragment_distance_uses_original_image_aspect_ratio():
    fragments = [
        {'text': 'MORNING', 'bbox': [.1, .1, .15, .05], 'confidence': .99},
        {'text': 'CUP', 'bbox': [.30, .1, .1, .05], 'confidence': .99},
    ]
    assert len(ocr.merge_same_line_fragments(fragments, height_to_width=.5)) == 2
    assert len(ocr.merge_same_line_fragments(fragments, height_to_width=2)) == 1


@pytest.mark.parametrize('orientation', [2, 3, 4, 5, 6, 7, 8])
def test_exif_transformed_image_cannot_produce_misaligned_evidence(monkeypatch, tmp_path, orientation):
    path = tmp_path / '회전 사진.jpg'
    exif = Image.Exif()
    exif[274] = orientation
    Image.new('RGB', (100, 50), 'white').save(path, exif=exif)
    monkeypatch.setattr(ocr, '_rapid_engine', lambda _: pytest.fail('Must reject before inference'))
    with pytest.raises(ValueError, match='EXIF 회전·반전'):
        ocr._read_rapid(path)


def test_garbled_percentage_is_uncertain_instead_of_a_definite_mismatch():
    result = inspect_text_rules(
        [{'text': '루%08', 'bbox': [.3, .2, .2, .05], 'confidence': .8}],
        [{'id': 'C02', 'expected': '30% 할인'}])[0]
    assert result['observed'] == '루%08'
    assert result['verdict'] == '확인 필요'


def test_mcp_environment_passes_only_explicit_ocr_settings(monkeypatch, tmp_path):
    from app.mcp_client import mcp_environment
    monkeypatch.setenv('LATTE_OCR_BACKEND', 'rapidocr')
    monkeypatch.setenv('LATTE_OCR_MODEL_DIR', str(tmp_path / '한글 모델'))
    monkeypatch.setenv('LATTE_RUNTIME_DIR', str(tmp_path / '별도 실행'))
    monkeypatch.setenv('UNRELATED_SECRET', 'must-not-be-forwarded')
    env = mcp_environment()
    assert env['LATTE_OCR_MODEL_DIR'] == str((tmp_path / '한글 모델').resolve())
    assert env['LATTE_RUNTIME_DIR'] == str((tmp_path / '별도 실행').resolve())
    assert env['LATTE_OCR_BACKEND'] == 'rapidocr'
    assert env['PYTHONIOENCODING'] == 'utf-8'
    assert 'UNRELATED_SECRET' not in env
