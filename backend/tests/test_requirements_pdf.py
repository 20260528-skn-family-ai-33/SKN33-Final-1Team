"""PDF 파싱·렌더링은 실제로 수행하고 OCR·LLM 추론을 대체해 연결 계약을 검증한다."""
import io
import base64
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from backend.app import api
from backend.app import requirements_pdf as pdf
from backend.app import paddle_ocr
from backend.app.ocr_runtime import OCRRuntimeError
from PIL import Image
from scripts import local_ollama


@pytest.fixture(autouse=True)
def paddle_stub(monkeypatch):
    monkeypatch.setattr(local_ollama, 'requirements_status', lambda: 'ready')
    calls = []
    def recognize(path, directory, *, timeout):
        with Image.open(path) as image:
            dimensions = list(image.size)
            assert image.mode == 'RGB' and max(dimensions) <= 1800
        assert 1 <= timeout <= 240
        calls.append((path, dimensions))
        return {'status': 'success', 'engine': 'paddleocr', 'input_dimensions': dimensions,
                'records': [{'id': 'paddle_000', 'text': 'Product: Pear Latte. Price: 5,900 won. Format: PNG.',
                             'confidence': .98, 'bbox_xyxy': [30, 100, 500, 120],
                             'recognition_engine': 'paddleocr'}]}
    monkeypatch.setattr(paddle_ocr, 'recognize_paddle', recognize)
    return calls


@pytest.mark.parametrize('status,message', [('unavailable', '서버가 꺼져'), ('missing_model', '설치되지'), ('missing_vision', '이미지를 지원하지')])
def test_model_readiness_fails_before_expensive_pdf_ocr(monkeypatch, status, message):
    monkeypatch.setattr(local_ollama, 'requirements_status', lambda: status)
    monkeypatch.setattr(pdf, '_read_pages', lambda *args: pytest.fail('unavailable model must fail before OCR'))
    with pytest.raises(pdf.DocumentError) as error:
        pdf.extract_pdf(document_bytes())
    assert error.value.status == 503 and message in str(error.value)


def document_bytes(text="Product: Pear Latte. Price: 5,900 won. Format: PNG.", pages=1, encrypted=False):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=600, height=800)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 30 700 Td ({text}) Tj ET".encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    if encrypted:
        writer.encrypt('secret')
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def condition(**patch):
    result = {"label": "가격", "requirement": "5,900원", "level": "명시 없음", "inspection": "",
              "type": "information", "source_id": "p1-s1", **patch}
    result.setdefault('values', [result['requirement']])
    return result


def paddle_text(monkeypatch, text):
    """수준·본문 근거가 실제로 있는 OCR 입력을 구성한다."""
    def recognize(path, directory, **kwargs):
        with Image.open(path) as image:
            dimensions = list(image.size)
        return {'status': 'success', 'engine': 'paddleocr', 'input_dimensions': dimensions,
                'records': [{'text': line, 'confidence': .98, 'bbox_xyxy': [30, 100+i*60, 900, 120+i*60]}
                            for i, line in enumerate(text.splitlines())]}
    monkeypatch.setattr(paddle_ocr, 'recognize_paddle', recognize)


def test_real_pdf_rendering_and_grounded_conditions(monkeypatch, paddle_stub):
    calls = []
    def model(messages, schema, name, deadline):
        calls.append(messages)
        assert schema['$defs']['Condition']['properties']['source_id']['enum'] == ['p1-s1']
        return {"conditions": [condition(), condition()]}
    monkeypatch.setattr(pdf, '_chat', model)
    result = pdf.extract_pdf(document_bytes())
    assert result['requirements'] == '가격: 5,900원'
    assert len(result['conditions']) == 1 and result['scanned_pages'] == []
    assert 'Price: 5,900 won.' in calls[0][1]['content']
    assert 'Price: 5,900 won.' in result['conditions'][0]['source']
    assert len(paddle_stub) == 1
    assert result['conditions'][0]['source_kind'] == 'pdf_page+paddleocr'
    assert result['model'] == 'qwen3.5:4b'
    assert result['conditions'][0]['approval_status'] == 'pending'
    assert result['conditions'][0]['requirement'] == '5,900원'
    assert result['ocr']['engine'] == 'paddleocr'
    assert result['ocr']['pages'][0]['records'][0]['confidence'] == .98
    assert result['ocr']['pages'][0]['records'][0]['bbox_xyxy'] == [30, 100, 500, 120]
    assert result['input_mode'] == 'pdf_page_images+paddleocr_text'
    image_messages = [m for m in calls[0] if 'images' in m]
    assert len(image_messages) == 1
    with Image.open(io.BytesIO(base64.b64decode(image_messages[0]['images'][0]))) as image:
        assert image.format == 'PNG' and image.mode == 'RGB'
        assert max(image.size) <= pdf.VISION_MAX_EDGE
        assert list(image.size) == result['vision']['pages'][0]['dimensions']
    assert 'image_base64' not in json.dumps(result)


def test_pdf_model_visual_label_does_not_disable_clear_price_check(monkeypatch):
    monkeypatch.setattr(pdf, '_chat', lambda *args: {'conditions': [condition(type='visual')]})
    result = pdf.extract_pdf(document_bytes())
    assert result['conditions'][0]['type'] == 'information'
    assert result['conditions'][0]['model_type'] == 'visual'


@pytest.mark.parametrize('data,status', [(b'', 400), (b'not pdf', 415), (b'%PDF-invalid', 422),
                                        (document_bytes(encrypted=True), 422), (document_bytes(pages=13), 413)],
                         ids=['empty', 'not-pdf', 'corrupt', 'encrypted', 'too-many-pages'])
def test_invalid_documents_do_not_call_llm(monkeypatch, data, status):
    monkeypatch.setattr(pdf, '_chat', lambda *args: pytest.fail('invalid PDF called LLM'))
    with pytest.raises(pdf.DocumentError) as error:
        pdf.extract_pdf(data)
    assert error.value.status == status


@pytest.mark.parametrize('conditions', [[], [condition(source_id='p2-s1')], [condition(source_id='invented')],
                                      [condition(label='항목명')], [condition(type='other')], [condition(values=[])]])
def test_empty_or_ungrounded_model_output_is_not_accepted(monkeypatch, conditions):
    monkeypatch.setattr(pdf, '_chat', lambda *args: {'conditions': conditions})
    with pytest.raises(pdf.DocumentError):
        pdf.extract_pdf(document_bytes())


def test_scanned_page_sends_paddle_text_and_page_image_together(monkeypatch, paddle_stub):
    calls = []
    def model(messages, schema, name, deadline):
        calls.append(messages)
        assert 'Price: 5,900 won.' in messages[1]['content']
        assert len(messages[2]['images']) == 1
        assert 'page 1' in messages[2]['content']
        return {'conditions': [condition()]}
    monkeypatch.setattr(pdf, '_chat', model)
    result = pdf.extract_pdf(document_bytes(text=''))
    assert len(calls) == 1 and len(paddle_stub) == 1 and result['scanned_pages'] == [1]
    assert result['warnings']


def test_embedded_text_is_not_substituted_for_paddle_reading(monkeypatch):
    def model(messages, *args):
        assert 'Price: 5,900 won.' in messages[1]['content']
        assert '9,900' not in messages[1]['content']
        return {'conditions': [condition()]}
    monkeypatch.setattr(pdf, '_chat', model)
    pdf.extract_pdf(document_bytes(text='Hidden native text: Price: 9,900 won. Format: JPG.'))


@pytest.mark.parametrize('code,status', [('timeout', 504), ('model_not_ready', 503), ('paddle_failed', 502)])
def test_paddle_failure_cannot_fall_back_to_llm_vision(monkeypatch, code, status):
    def fail(*args, **kwargs):
        raise OCRRuntimeError('private path/token', code=code)
    monkeypatch.setattr(paddle_ocr, 'recognize_paddle', fail)
    monkeypatch.setattr(pdf, '_chat', lambda *args: pytest.fail('OCR failed but LLM was called'))
    with pytest.raises(pdf.DocumentError) as error:
        pdf.extract_pdf(document_bytes())
    assert error.value.status == status and 'private' not in str(error.value)


def test_page_numbers_and_ocr_metadata_survive_multiple_pages(monkeypatch):
    monkeypatch.setattr(pdf, '_chat', lambda messages, *args: {'conditions': [condition(source_id='p2-s1')]
        if json.loads(messages[1]['content'])['extraction_pages'] == [2] else []})
    result = pdf.extract_pdf(document_bytes(pages=2))
    assert result['conditions'][0]['page'] == 2
    assert [p['page'] for p in result['ocr']['pages']] == [1, 2]


def test_page_batches_keep_images_sources_and_page_order(monkeypatch):
    calls = []
    def model(messages, schema, *args):
        context = json.loads(messages[1]['content'])
        numbers = context['extraction_pages']
        calls.append(numbers)
        assert len(messages[2:]) == len(numbers) <= pdf.PAGES_PER_CALL
        assert [s['page'] for s in context['sources']] == numbers
        assert schema['$defs']['Condition']['properties']['source_id']['enum'] == [f'p{n}-s1' for n in numbers]
        return {'conditions': [condition(label=f'페이지{numbers[0]}', source_id=f'p{numbers[0]}-s1')]}
    monkeypatch.setattr(pdf, '_chat', model)
    result = pdf.extract_pdf(document_bytes(pages=4))
    assert calls == [[1], [2], [3], [4]]
    assert [c['page'] for c in result['conditions']] == [1, 2, 3, 4]


def test_later_batch_error_never_returns_partial_document(monkeypatch):
    def model(messages, *args):
        number = json.loads(messages[1]['content'])['extraction_pages'][0]
        if number == 4:
            raise pdf.DocumentError('추출 시간 초과', 504)
        return {'conditions': [condition(label=f'가격{number}', source_id=f'p{number}-s1')]}
    monkeypatch.setattr(pdf, '_chat', model)
    with pytest.raises(pdf.DocumentError) as error:
        pdf.extract_pdf(document_bytes(pages=4))
    assert error.value.status == 504


def test_schema_bypass_cannot_cite_page_outside_image_batch(monkeypatch):
    monkeypatch.setattr(pdf, '_chat', lambda *args: {'conditions': [condition(source_id='p4-s1')]})
    with pytest.raises(pdf.DocumentError, match='페이지 근거'):
        pdf.extract_pdf(document_bytes(pages=4))


def test_repeated_header_citations_are_relinked_to_actual_ocr_row():
    sources = [{'id': 'p1-s1', 'page': 1, 'text': 'CC-GC-2026-001 검토용 초안'},
               {'id': 'p1-s2', 'page': 1, 'text': 'R01 필수 | 행사명 | 김천김밥축제 2026 | 오탈자 불가'},
               {'id': 'p2-s1', 'page': 2, 'text': 'R03 필수 | 행사 장소 | 직지문화공원 사명대사공원 | 모두 포함'}]
    c = pdf.Condition(label='행사 장소', requirement='직지문화공원 사명대사공원',values=['직지문화공원','사명대사공원'],
                      inspection='모두 포함', level='필수', type='information', source_id='p1-s1')
    source, alignment = pdf._link_source(c, sources)
    assert source['id'] == 'p2-s1' and source['page'] == 2
    assert alignment['relinked'] and alignment['model_source_id'] == 'p1-s1'
    with pytest.raises(pdf.DocumentError, match='근거의 연결'):
        pdf._link_source(c, sources[:1])


@pytest.mark.parametrize('omit', [False, True])
def test_numbered_requirement_table_excludes_contract_metadata_and_requires_every_row(monkeypatch, omit):
    def recognize(path, directory, **kwargs):
        with Image.open(path) as image:
            dimensions = list(image.size)
        return {'status': 'success', 'engine': 'paddleocr', 'input_dimensions': dimensions,
                'records': [{'text': text, 'bbox_xyxy': [30, y, 900, y+20], 'confidence': .98}
                            for text, y in [('계약 금액 1,100,000원', 10),
                                            ('R01 필수 | 가격 | 5,900원 | 숫자 일치', 100),
                                            ('R02 선택 | 파일 형식 | PNG 또는 JPG | 둘 중 하나 허용', 200)]]}
    monkeypatch.setattr(paddle_ocr, 'recognize_paddle', recognize)
    def model(messages, schema, *args):
        sources = json.loads(messages[1]['content'])['sources']
        assert [s['requirement_id'] for s in sources] == ['R01', 'R02']
        assert '계약 금액' not in messages[1]['content']
        rows = [condition(inspection='숫자 일치', source_id='p1-s2')]
        if not omit:
            rows.append(condition(label='파일 형식', requirement='PNG 또는 JPG', level='선택',
                                  inspection='둘 중 하나 허용', source_id='p1-s3'))
        return {'conditions': rows}
    monkeypatch.setattr(pdf, '_chat', model)
    if omit:
        with pytest.raises(pdf.DocumentError, match='일부 항목'):
            pdf.extract_pdf(document_bytes())
    else:
        result = pdf.extract_pdf(document_bytes())
        assert [c['requirement_id'] for c in result['conditions']] == ['R01', 'R02']
        assert result['source_selection']['mode'] == 'numbered_requirement_rows'
        assert '표 밖' in result['warnings'][1]


def test_multimodal_chat_uses_json_schema_and_disables_thinking(monkeypatch):
    from backend.app import model_ocr
    calls = []
    def request(route, payload, **kwargs):
        calls.append(payload)
        return {'done': True, 'done_reason': 'stop', 'message': {'content': '{"conditions":[]}'}}
    monkeypatch.setattr(model_ocr, 'ollama_request', request)
    assert pdf._chat([{'role': 'user', 'content': 'PDF', 'images': ['base64']}], {'type': 'object'}, 'qwen3.5:4b', float('inf')) == {'conditions': []}
    assert calls[0]['think'] is False and calls[0]['stream'] is False
    assert calls[0]['format'] == {'type': 'object'}
    assert calls[0]['messages'][0]['images'] == ['base64']


def test_linebreaks_in_model_condition_are_joined_without_losing_words(monkeypatch):
    monkeypatch.setattr(pdf, '_chat', lambda *args: {'conditions': [condition(requirement='5,900원\n(변경 불가)')]})
    result = pdf.extract_pdf(document_bytes())
    assert result['requirements'] == '가격: 5,900원 (변경 불가)'


def test_recommended_level_and_exceptions_are_not_lost(monkeypatch):
    paddle_text(monkeypatch, '가격 5,900원 권장')
    monkeypatch.setattr(pdf, '_chat', lambda *args: {'conditions': [condition(
        level='권장', inspection='공백 차이 허용\n필수 불합격 사유로 사용하지 않음')]})
    result = pdf.extract_pdf(document_bytes())
    assert result['requirements'] == '가격: 5,900원'
    assert result['conditions'][0]['level'] == '권장'
    assert result['conditions'][0]['inspection'] == '공백 차이 허용 필수 불합격 사유로 사용하지 않음'


def test_compact_condition_limit_is_checked_before_returning_to_ui(monkeypatch):
    monkeypatch.setattr(pdf, 'MAX_REQUIREMENTS_CHARS', 5)
    monkeypatch.setattr(pdf, '_chat', lambda *args: {'conditions': [condition()]})
    with pytest.raises(pdf.DocumentError) as error:
        pdf.extract_pdf(document_bytes())
    assert error.value.status == 413


def test_same_compact_text_cannot_silently_lose_different_levels(monkeypatch):
    paddle_text(monkeypatch, 'R01 필수 | 가격 | 5,900원\nR02 선택 | 가격 | 5,900원')
    monkeypatch.setattr(pdf, '_chat', lambda *args: {'conditions': [condition(level='필수'), condition(level='선택', source_id='p1-s2')]})
    with pytest.raises(pdf.DocumentError, match='서로 다른 수준'):
        pdf.extract_pdf(document_bytes())


def test_duplicate_notice_merges_complementary_details_and_both_page_sources():
    sources = [{'id': 'p1-s1', 'page': 1, 'text': '필수 고지 | 시즌 한정 · 소진 시 종료'},
               {'id': 'p2-s1', 'page': 2, 'text': 'IMG-06 | 필수 고지 | 시즌 한정 · 소진 시 종료 | 누락 없이 표기'}]
    first = pdf.Condition.model_validate(condition(label='필수 고지', requirement='시즌 한정 · 소진 시 종료',
                                                   type='visual', source_id='p1-s1'))
    second = first.model_copy(update={'source_id': 'p2-s1', 'inspection': '누락 없이 표기한다.'})
    result = pdf._merge_conditions([(first, sources[:1]), (second, sources[1:])], sources)
    assert len(result) == 1 and result[0]['type'] == 'information'
    assert result[0]['inspection'] == '누락 없이 표기한다.'
    assert [s['page'] for s in result[0]['source_references']] == [1, 2]
    assert result[0]['approval_status'] == 'pending'


def test_duplicate_details_are_not_dropped_or_truncated_at_snapshot_boundary():
    from backend.app.document_conditions import DocumentCondition
    source = {'id': 'p1-s1', 'page': 1, 'text': '가격 5,900원'}
    rows = [pdf.Condition.model_validate(condition(inspection=detail))
            for detail in ['가독성 기준 ' + '가' * 180, '배치 기준 ' + '나' * 180]]
    result = pdf._merge_conditions([(c, [source]) for c in rows], [source])[0]
    assert all(c.inspection in result['inspection'] for c in rows)
    structured = DocumentCondition.model_validate({k: result[k] for k in ('label', 'requirement', 'level', 'inspection')})
    assert len(structured.inspection) > 200


def test_missing_level_does_not_conflict_with_explicit_level_but_or_and_punctuation_stay_distinct():
    source = {'id': 'p1-s1', 'page': 1, 'text': '브랜드 | A 또는 B | 필수'}
    rows = [pdf.Condition.model_validate(condition(label='브랜드', requirement=value, level=level))
            for value, level in [('A 또는 B', '명시 없음'), ('A 또는 B', '필수'), ('A 또는 B!', '필수')]]
    result = pdf._merge_conditions([(c, [source]) for c in rows], [source])
    assert len(result) == 2 and result[0]['level'] == '필수'
    assert result[0]['requirement'] == 'A 또는 B'


@pytest.mark.parametrize('label', ['캠페인', '캠페인 문구', '판매 가격', '필수 고지', '파일규격',
                                  '이미지 비율', '최소 이미지 폭', '색상 모드'])
def test_brief_text_and_metadata_labels_are_information_even_if_model_says_visual(label):
    from backend.app.document_conditions import condition_type
    assert condition_type(label, 'visual') == 'information'


@pytest.mark.parametrize('text,expected', [
    ('크림과 녹색을 중심으로 따뜻한 분위기를 표현한다.', True),
    ('음료 한 잔과 과일 이미지를 활용하여 상품을 표현한다.', True),
    ('표기는 읽기 쉬운 크기와 대비를 확보한다.', True),
    ('SNS 4:5 권장 · 최소 폭 1024px · PNG · RGB', True),
    ('브랜드 | A 또는 B', True),
    ('계약 금액 1,100,000원', False), ('DOCUMENT CONTROL', False), ('제작 및 납품 제작팀', False),
])
def test_supplement_selection_includes_body_directions_but_not_contract_metadata(text, expected):
    assert pdf._supplement_source({'text': text}) is expected


@pytest.mark.parametrize('fail_supplement', [False, True])
def test_id_table_without_level_is_completed_by_body_directions(monkeypatch, fail_supplement):
    def recognize(path, directory, **kwargs):
        with Image.open(path) as image:
            dimensions = list(image.size)
        texts = ['문서 관리 ID TEST-001', '브랜드 | A 또는 B',
                 'IMG-01 | 상품명 | 테스트 라떼', 'IMG-02 | 가격 | 5,900원',
                 '크림과 녹색을 중심으로 따뜻한 분위기를 표현한다.']
        return {'status': 'success', 'engine': 'paddleocr', 'input_dimensions': dimensions,
                'records': [{'text': text, 'bbox_xyxy': [30, 100+i*60, 900, 120+i*60], 'confidence': .98}
                            for i, text in enumerate(texts)]}
    monkeypatch.setattr(paddle_ocr, 'recognize_paddle', recognize)
    calls = []
    def model(messages, *args):
        context = json.loads(messages[1]['content'])
        calls.append(context['phase'])
        by_text = {s['text']: s['id'] for s in context['sources']}
        if context['phase'] == 'primary':
            assert all(s.get('requirement_id') for s in context['sources'])
            return {'conditions': [condition(source_id=by_text['IMG-02 | 가격 | 5,900원']),
                condition(label='상품명', requirement='테스트 라떼', source_id=by_text['IMG-01 | 상품명 | 테스트 라떼'])]}
        if fail_supplement:
            raise pdf.DocumentError('보완 분석 실패', 502)
        assert '문서 관리' not in messages[1]['content']
        return {'conditions': [condition(label='브랜드', requirement='A 또는 B', source_id=by_text['브랜드 | A 또는 B']),
            condition(label='색상과 분위기', requirement='크림과 녹색을 중심으로 따뜻한 분위기를 표현한다.',
                      type='visual', source_id=by_text['크림과 녹색을 중심으로 따뜻한 분위기를 표현한다.'])]}
    monkeypatch.setattr(pdf, '_chat', model)
    if fail_supplement:
        with pytest.raises(pdf.DocumentError, match='보완 분석 실패'):
            pdf.extract_pdf(document_bytes())
    else:
        result = pdf.extract_pdf(document_bytes())
        assert len(result['conditions']) == 4
        assert result['source_selection']['requirement_ids'] == ['IMG-01', 'IMG-02']
        assert result['conditions'][-1]['type'] == 'visual'
    assert calls == ['primary', 'supplement']


def test_supplement_cannot_silently_drop_a_selected_body_direction(monkeypatch):
    paddle_text(monkeypatch, 'R01 필수 | 가격 | 5,900원\nR02 필수 | 상품명 | 테스트 라떼\n크림과 녹색을 중심으로 표현한다.')
    def chat(messages, *args):
        context = json.loads(messages[1]['content'])
        return {'conditions': [] if context['phase'] == 'supplement' else [
            condition(level='필수'), condition(label='상품명', requirement='테스트 라떼', source_id='p1-s2', level='필수')]}
    monkeypatch.setattr(pdf, '_chat', chat)
    with pytest.raises(pdf.DocumentError, match='표 밖의 제작 지시 1개'):
        pdf.extract_pdf(document_bytes())


def test_model_mandatory_guess_without_source_level_is_not_copied(monkeypatch):
    monkeypatch.setattr(pdf, '_chat', lambda *args: {'conditions': [condition(level='필수')]})
    result = pdf.extract_pdf(document_bytes())
    assert result['conditions'][0]['level'] == '명시 없음'
    assert result['conditions'][0]['source_references'][0]['model_level'] == '필수'


def test_mixed_file_spec_splits_recommended_ratio_from_other_metadata():
    c = pdf.Condition.model_validate(condition(label='파일규격',
        requirement='4:5 권장(±1px 허용), 폭 1024px 이상, PNG, RGB',
        level='권장', inspection='4:5 비율의 픽셀 반올림 차이는 ±1px까지 허용한다.'))
    parts = pdf._atomic_conditions(c)
    assert [(p.label, p.requirement, p.level) for p in parts] == [
        ('이미지 비율', '4:5', '권장'), ('최소 이미지 폭', '1024px 이상', '명시 없음'),
        ('파일 형식', 'PNG', '명시 없음'), ('색상 모드', 'RGB', '명시 없음')]
    assert parts[0].inspection == c.inspection and all(not p.inspection for p in parts[1:])
    alternative = c.model_copy(update={'requirement': c.requirement + ' 또는 CMYK'})
    assert len(pdf._atomic_conditions(alternative)) == 1
    unknown = c.model_copy(update={'requirement': c.requirement + ', 300dpi'})
    assert len(pdf._atomic_conditions(unknown)) == 1
    portrait = c.model_copy(update={'requirement':'세로형, PNG, RGB'})
    assert pdf._atomic_conditions(portrait)[0].requirement == portrait.requirement


def test_ratio_alias_and_level_suffix_do_not_make_duplicate_checklist_lines():
    c = pdf.Condition.model_validate(condition(label='SNS 비율', requirement='4:5 권장', values=['4:5']))
    part, = pdf._atomic_conditions(c)
    assert part.label == '이미지 비율' and part.requirement == '4:5' and part.level == '권장'
    with_tolerance = c.model_copy(update={'requirement':'4:5 권장 (±1px 허용)'})
    part, = pdf._atomic_conditions(with_tolerance)
    assert part.requirement=='4:5' and '±1px' in part.inspection
    sns = c.model_copy(update={'label':'이미지 비율','requirement':'SNS 세로형 4:5 권장'})
    part, = pdf._atomic_conditions(sns)
    assert part.requirement=='4:5' and part.level=='권장'


def test_separate_pixel_rounding_exception_is_merged_into_the_same_page_ratio():
    ratio=pdf.Condition.model_validate(condition(label='이미지 비율',requirement='4:5'))
    exception=pdf.Condition.model_validate(condition(label='픽셀 허용 오차',
        requirement='4:5 비율의 픽셀 반올림 차이는 ± 1px까지 허용한다.'))
    existing=[(ratio,[{'page':1}])]
    linked=pdf._attach_ratio_exception(exception,existing,1)
    assert linked.requirement=='4:5' and linked.inspection==exception.requirement
    assert linked.label=='이미지 비율'
    assert pdf._attach_ratio_exception(exception,existing,2)==exception


def test_brand_text_mislabeled_as_logo_is_merged_using_the_actual_table_label():
    sources=[{'id':'p1-s1','page':1,'text':'IMG-01 | 브랜드 | A 또는 B 표기','requirement_id':'IMG-01'}]
    brand=pdf.Condition.model_validate(condition(label='브랜드',requirement='A 또는 B',values=['A 또는 B']))
    logo=pdf.Condition.model_validate(condition(label='로고',requirement='A 또는 B 표기',values=['A 또는 B'],type='visual'))
    result=pdf._merge_conditions([(brand,sources),(logo,sources)],sources)
    assert len(result)==1 and result[0]['type']=='information' and result[0]['text']=='브랜드: A 또는 B'


def test_repeated_body_file_spec_requires_all_properties_before_counting_as_covered():
    table={'id':'p1-s1','page':1,'text':'IMG-07 | 파일규격 | 4:5 권장(±1px 허용), 폭 1024px 이상, PNG, RGB','requirement_id':'IMG-07'}
    body={'id':'p1-s2','page':1,'text':'SNS 세로형 4:5 권장·최소 폭 1024px·PNG·RGB'}
    c=pdf.Condition.model_validate(condition(label='파일규격',requirement='4:5 권장(±1px 허용), 폭 1024px 이상, PNG, RGB'))
    parts=pdf._atomic_conditions(c)
    all_parts=pdf._merge_conditions([(p,[table]) for p in parts],[table,body])
    assert all(any(s['source_id']=='p1-s2' for s in item['source_references']) for item in all_parts)
    incomplete=pdf._merge_conditions([(p,[table]) for p in parts[:-1]],[table,body])
    assert all(not any(s['source_id']=='p1-s2' for s in item['source_references']) for item in incomplete)


def test_date_format_exception_attaches_only_to_a_unique_period_on_the_same_page():
    date = pdf.Condition.model_validate(condition(label='판매 기간', requirement='2026.11.01 - 11.30'))
    exception = pdf.Condition.model_validate(condition(label='기간 표기',
        requirement='기간의 0 표기 및 구분자가 달라도 실제 시작일과 종료일이 같으면 허용한다.'))
    existing = [(date, [{'page': 1}])]
    linked = pdf._attach_date_exception(exception, existing, 1)
    assert linked.requirement == date.requirement and linked.inspection == exception.requirement
    assert pdf._attach_date_exception(exception, existing, 2) == exception
    assert pdf._attach_date_exception(exception, existing + [(date.model_copy(update={'requirement':'2027.01.01 - 01.10'}), [{'page':1}])], 1) == exception


def test_inspection_source_and_misplaced_campaign_sentence_are_preserved():
    sources = [{'id':'p1-s1','page':1,'text':'판매 기간 | 2026.11.01 - 11.30'},
               {'id':'p1-s2','page':1,'text':'기간의 0 표기 및 구분자가 달라도 시작일과 종료일이 같으면 허용한다. 캠페인 문구 “가을의 한 잔”도 누락 없이 표기한다.'},
               {'id':'p1-s3','page':1,'text':'캠페인 | 가을의 한 잔'}]
    date = pdf.Condition.model_validate(condition(label='판매 기간',requirement='2026.11.01 - 11.30',inspection=sources[1]['text']))
    campaign = pdf.Condition.model_validate(condition(label='캠페인 문구',requirement='가을의 한 잔',source_id='p1-s3'))
    result = pdf._merge_conditions([(date,sources),(campaign,sources)],sources)
    assert '캠페인' not in result[0]['inspection'] and '구분자' in result[0]['inspection']
    assert '누락 없이' in result[1]['inspection']
    assert any(s['source_id']=='p1-s2' for s in result[0]['source_references'])


@pytest.mark.parametrize('value,inspection,expected', [
    ('직지문화공원, 사명대사공원, 친환경생태공원','세 장소명을 모두 표기한다.',
     ['직지문화공원','사명대사공원','친환경생태공원']),
    ('직지문화공원 또는 사명대사공원','둘 중 하나를 표기한다.', ['직지문화공원 또는 사명대사공원']),
    ('직지문화공원, 사명대사공원 중 하나','후보를 모두 확인한다.', ['직지문화공원, 사명대사공원 중 하나']),
    ('광교호수공원 재미난 밭','지명을 모두 표기한다.', ['광교호수공원 재미난 밭']),
    ('서울, 강남구','주소를 표기한다.', ['서울, 강남구']),
], ids=['all-required', 'or', 'one-of', 'compound-name', 'address'])
def test_atomic_places_do_not_change_alternatives_or_compound_names(value,inspection,expected):
    c=pdf.Condition.model_validate(condition(label='장소',requirement=value,inspection=inspection))
    parts=pdf._atomic_conditions(c)
    assert [p.requirement for p in parts]==expected
    assert all(p.inspection==inspection and p.source_id==c.source_id for p in parts)


@pytest.mark.parametrize('label,value,values', [
    ('장소','광교호수공원 재미난 밭',['광교호수공원','재미난 밭']),
    ('18일 출연진','정동환 x Funk Messengers',['정동환','Funk Messengers']),
    ('이미지 크기','가로 2527 x 세로 3572픽셀, 세로형',['2527 x 3572픽셀','세로형']),
    ('문구 가독성','행사명과 날짜를 배경과 구별되는 대비로 표시한다.',['행사명','날짜']),
], ids=['one-place','collaboration','file-size','visual-predicate'])
def test_model_values_do_not_fragment_a_single_condition(label,value,values):
    c=pdf.Condition.model_validate(condition(label=label,requirement=value,values=values,inspection='모두 확인한다.'))
    assert [p.requirement for p in pdf._atomic_conditions(c)]==[value]


def test_multiple_required_places_and_visual_sentences_stay_compact(monkeypatch):
    text = '다음 항목 필수: 행사명 김천김밥축제 기간 2026.10.23 ~ 10.25 장소 직지문화공원 사명대사공원 친환경생태공원 김천시 로고가 표시되어 있어야 합니다. 문구와 이미지가 잘리지 않아야 합니다.'
    def recognize(path, directory, **kwargs):
        with Image.open(path) as image:
            dimensions = list(image.size)
        return {'status':'success','engine':'paddleocr','input_dimensions':dimensions,
                'records':[{'text':text,'bbox_xyxy':[30,100,900,120],'confidence':.98}]}
    monkeypatch.setattr(paddle_ocr, 'recognize_paddle', recognize)
    places = ['직지문화공원','사명대사공원','친환경생태공원']
    values = [('행사명','김천김밥축제'),('기간','2026.10.23 ~ 10.25'),
              ('장소', ', '.join(places)),
              ('로고','김천시 로고가 표시되어 있어야 합니다.'),('잘림','문구와 이미지가 잘리지 않아야 합니다.')]
    monkeypatch.setattr(pdf, '_chat', lambda *args: {'conditions':[condition(label=label,requirement=value,
        values=places if label=='장소' else [value],
        type='visual' if label in {'로고','잘림'} else 'information',level='필수',
        inspection='세 장소명을 모두 표기한다.' if label=='장소' else '원본과 대조한다.')
        for label,value in values]})
    result = pdf.extract_pdf(document_bytes())
    assert result['requirements'].splitlines() == [
        '행사명: 김천김밥축제','기간: 2026.10.23 ~ 10.25',
        '장소: 직지문화공원','장소: 사명대사공원','장소: 친환경생태공원',
        '김천시 로고가 표시되어 있어야 합니다.','문구와 이미지가 잘리지 않아야 합니다.']
    assert all(c['inspection'] and c['level']=='필수' for c in result['conditions'])


def test_table_columns_keep_wrapped_values_with_their_requirement():
    def record(text, box):
        return {'text': text, 'bbox_xyxy': box}
    records = [record('행사 장소', [100, 10, 170, 30]),
               record('첫 번째 공원', [200, 10, 300, 30]),
               record('두 번째 공원', [200, 34, 300, 54]),
               record('모든 장소 필수', [400, 10, 500, 30]),
               record('로고', [100, 80, 150, 100]),
               record('도시 이름', [200, 80, 300, 100])]
    text = pdf._layout_text(list(reversed(records)))
    assert text == '행사 장소 | 첫 번째 공원 두 번째 공원 | 모든 장소 필수\n\n로고 | 도시 이름'
    sources = pdf._sources([{'page': 2, 'text': text}])
    assert len(sources) == 2 and sources[0]['id'] == 'p2-s1'
    assert sources[0]['text'] == text.split('\n\n')[0]
    assert pdf._layout_text([]) == ''


def test_long_source_blocks_preserve_all_text_and_unique_ids():
    text = '가' * 400 + '나' * 400
    sources = pdf._sources([{'page': 1, 'text': text}])
    assert len({s['id'] for s in sources}) == len(sources)
    assert all(len(s['text']) <= 350 for s in sources)
    assert sources[0]['text'] + ''.join(s['text'][60:] for s in sources[1:]) == text


def test_limits_never_silently_truncate(monkeypatch):
    monkeypatch.setattr(pdf, 'MAX_TEXT_CHARS', 20)
    monkeypatch.setattr(pdf, '_chat', lambda *args: pytest.fail('overlong PDF called LLM'))
    with pytest.raises(pdf.DocumentError) as error:
        pdf.extract_pdf(document_bytes())
    assert error.value.status == 413


@pytest.mark.parametrize('reply', [{'done': False}, {'done': True, 'done_reason': 'length'},
                                  {'done': True, 'message': {'content': 'invalid JSON'}}])
def test_incomplete_or_invalid_llm_json_fails(monkeypatch, reply):
    from backend.app import model_ocr
    monkeypatch.setattr(model_ocr, 'ollama_request', lambda *a, **kw: reply)
    with pytest.raises(pdf.DocumentError) as error:
        pdf._chat([], {}, 'model', float('inf'))
    assert error.value.status == 502


def client(tmp_path):
    return TestClient(api.create_app(review_root=tmp_path))


def upload(client, data=b'%PDF-valid', filename='requirements.pdf'):
    return client.post('/api/requirements/extract', files={'document': (filename, data, 'application/pdf')})


def test_api_returns_conditions_without_creating_review_or_original_file(tmp_path, monkeypatch):
    expected = {'requirements': '가격: 5,900원', 'conditions': [condition()], 'page_count': 1}
    monkeypatch.setattr(api, 'extract_document', lambda data: expected)
    with client(tmp_path) as http:
        assert upload(http, filename='../../unsafe.pdf').json() == expected
    assert not list(tmp_path.iterdir())


def test_api_validates_upload_and_sanitizes_errors(tmp_path, monkeypatch):
    def fail(data): raise RuntimeError('private endpoint /secret/token')
    monkeypatch.setattr(api, 'extract_document', fail)
    monkeypatch.setattr(api, 'MAX_PDF_BYTES', 20)
    with client(tmp_path) as http:
        assert upload(http, b'').status_code == 400
        assert upload(http, b'not pdf').status_code == 415
        assert upload(http, b'%PDF-' + b'x' * 20).status_code == 413
        response = upload(http)
        assert response.status_code == 502 and 'secret' not in response.text


def test_concurrent_documents_are_bounded_and_lock_released(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    def extract(data):
        entered.set()
        assert release.wait(5)
        raise pdf.DocumentError('모델 실행 상태를 확인해 주세요.', 503)
    monkeypatch.setattr(api, 'extract_document', extract)
    with client(tmp_path) as http, ThreadPoolExecutor(max_workers=1) as executor:
        running = executor.submit(upload, http)
        assert entered.wait(5)
        try:
            assert upload(http).status_code == 429
        finally:
            release.set()
        assert running.result().status_code == 503
        assert upload(http).status_code == 503


def test_document_waits_for_existing_poster_inference_and_releases_its_lock(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    def recognize(path):
        entered.set()
        assert release.wait(5)
        return {'records': []}
    expected = {'requirements': '가격: 5,900원', 'conditions': [condition()], 'page_count': 1}
    monkeypatch.setattr(api, 'extract_document', lambda data: expected)
    image = io.BytesIO()
    Image.new('RGB', (100, 100), 'white').save(image, 'PNG')
    with TestClient(api.create_app(review_root=tmp_path, recognizer=recognize)) as http, ThreadPoolExecutor(max_workers=1) as executor:
        running = executor.submit(http.post, '/api/reviews',
            data={'criteria_version': '1', 'project_name': '문서 동시 실행 검사',
                  'criteria': json.dumps([{'id': 'price', 'text': '가격: 5,900원', 'type': 'information', 'source': 'user'}])},
            files={'image': ('poster.png', image.getvalue(), 'image/png')})
        try:
            assert entered.wait(5)
            response = upload(http)
            assert response.status_code == 429 and '이미지 검수' in response.text
        finally:
            release.set()
        assert running.result().status_code == 202
        assert upload(http).json() == expected
