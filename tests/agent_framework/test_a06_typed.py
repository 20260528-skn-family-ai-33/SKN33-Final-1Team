"""A06 settles dates and file properties by code. Hand-set outcomes, not model accuracy measurements."""
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID

import pytest

from backend.agent_framework.contracts import (AssetRef, Context, OriginalFile, RequirementDetails, RunSnapshot, SourceRef,
                                               ValidateRequest)
from backend.agent_framework.validation import ReferenceValidator, validate_validated
from tests.agent_framework.test_validation import changed, request, text_request

DATE_NOTATION = '허용 범위: 날짜 구분자와 앞자리 0의 차이는 허용한다. 연도·월·일·요일 의미는 동일해야 한다.'
JPEG = OriginalFile(file_id='original-1', sha256='b' * 64, format='JPEG', width=3000, height=2000, color_mode='RGB')


def detailed(field, expected, observed='', *, target='poster_text', kind='text', operator='contains', unit=None,
             normalized=None, allowance=None, quote=None, original=None, **observation):
    """One structured criterion (what A02 produces) with one inspector answer."""
    req = request()
    quote = quote or '기준: ' + expected + ('\n' + allowance if allowance else '')
    source = SourceRef(document_version_id='d1', page=1, block_id='b1', quote=quote,
                       source_hash=sha256(quote.encode()).hexdigest())

    def cited(text):
        return source.model_copy(update={'quote': text, 'source_hash': sha256(text.encode()).hexdigest()})

    details = RequirementDetails(target=target, field=field, expected_source=cited(expected), normalized_expected=normalized,
                                 qualifiers=(cited(allowance),) if allowance else ())
    criterion = req.criteria[0].model_copy(update={'text': quote, 'kind': kind, 'operator': operator, 'expected': expected,
                                                   'unit': unit, 'source': source, 'details': details})
    evidence = req.evidence.model_copy(update={'evidence': (req.evidence.evidence[0].model_copy(update={'source': source}),)})
    answer = req.inspection.observations[0].model_copy(update={'observed': observed, **observation})
    data = req.model_copy(update={'criteria': (criterion,), 'evidence': evidence,
                                  'inspection': req.inspection.model_copy(update={'observations': (answer,)})}).model_dump(mode='json')
    return ValidateRequest.model_validate({**data, 'original': original.model_dump(mode='json') if original else None})


def judged(req):
    result = ReferenceValidator().validate(req)
    return result.items[0].verdict, result.items[0].issues


@pytest.mark.parametrize('observed,allowance,verdict,issues', [
    ('2026.5.14(목)', None, 'pass', ()),
    ('2026. 5. 14.', DATE_NOTATION, 'pass', ()),
    ('MAY 14, 2026', DATE_NOTATION, 'pass', ()),
    ('2026.5.14 – 8.30', None, 'pass', ()),
    ('2026.5.15', None, 'fail', ('value_mismatch',)),
    # 표기 차이만 허용한 문장이면 다른 날짜는 불충족이다
    ('2026.5.15', DATE_NOTATION, 'fail', ('value_mismatch',)),
    # 다른 값도 받아들일 수 있는 문장이면 다른 날짜를 불충족으로 단정하지 않는다
    ('2026.5.15', '허용 범위: 영문 날짜 표기도 가능하다.', 'review', ('qualifier_interpretation_required',)),
    ('2026.5.14', '허용 범위: 영문 날짜 표기도 가능하다.', 'pass', ()),
    # 포스터만으로 정할 수 없는 문장은 같은 날짜여도 사람이 본다
    ('2026.5.14', '허용 범위: 일정이 바뀌면 담당자 공지를 따른다.', 'review', ('qualifier_interpretation_required',)),
    # 연도가 없으면 같은 날인지 코드가 정하지 못한다
    ('5.14(목)', None, 'review', ('date_comparison_review_required',)),
    ('오월 십사일', DATE_NOTATION, 'review', ('date_comparison_review_required',)),
])
def test_a_start_date_is_settled_when_the_poster_states_the_whole_date(observed, allowance, verdict, issues):
    req = detailed('start_date', '2026년 5월 14일', observed, operator='equals', normalized='2026-05-14', allowance=allowance)
    assert judged(req) == (verdict, issues)


def test_an_end_date_reads_the_last_date_of_a_range():
    quote = '기간: 2026.10.23 ~ 10.25'
    arguments = dict(normalized='2026-10-25', quote=quote)
    assert judged(detailed('end_date', '10.25', '2026.10.23 ~ 10.25', **arguments)) == ('pass', ())
    assert judged(detailed('end_date', '10.25', '2026.10.23 ~ 2026.10.26', **arguments)) == ('fail', ('value_mismatch',))
    assert judged(detailed('end_date', '10.25', '10.25', **arguments)) == ('review', ('date_comparison_review_required',))


def test_a_single_event_date_does_not_accept_a_period():
    arguments = dict(normalized='2025-11-02')
    assert judged(detailed('event_date', '2025년 11월 2일', '2025.11.2(일) 12:00~19:00', **arguments)) == ('pass', ())
    assert judged(detailed('event_date', '2025년 11월 2일', '2025.11.2 ~ 11.4', **arguments)) == (
        'review', ('date_comparison_review_required',))


def test_a_whole_period_in_one_criterion_is_still_left_to_a_person():
    req = detailed('event_period', '2026.10.23 ~ 10.25', '2026.10.23 ~ 10.25')
    assert judged(req) == ('review', ('date_comparison_review_required',))


def test_a_matching_date_does_not_override_the_inspectors_failure_or_absence():
    arguments = dict(operator='equals', normalized='2026-05-14')
    assert judged(detailed('start_date', '2026년 5월 14일', '2026.5.14', verdict='fail', **arguments)) == (
        'review', ('verdict_conflict',))
    assert judged(detailed('start_date', '2026년 5월 14일', '', observation_status='absent', verdict='fail', **arguments)) == (
        'fail', ('observed_absence',))
    assert judged(detailed('start_date', '2026년 5월 14일', '2026.5.14', uncertainty='흐림', **arguments)) == (
        'review', ('uncertain_observation',))


@pytest.mark.parametrize('field,expected,operator,observed,allowance,verdict,issues', [
    ('weekday', '목요일', 'equals', '(목)', None, 'pass', ()),
    ('weekday', '목요일', 'equals', 'THU', DATE_NOTATION, 'pass', ()),
    ('weekday', '목요일', 'equals', '(금)', DATE_NOTATION, 'fail', ('value_mismatch',)),
    ('year', '2026년', 'equals', '2026', None, 'pass', ()),
    ('year', '2026년', 'equals', '2026.5.14', None, 'pass', ()),
    ('year', '2026년', 'equals', '2025', None, 'fail', ('value_mismatch',)),
    # 팀 샘플의 연도 허용 문장은 다른 표기도 받아들이므로 다른 연도를 불충족으로 단정하지 않는다
    ('year', '2026년', 'equals', '2025', '허용 범위: 2026 또는 다른 연도 정보와 함께 해석 가능한 ‘26년’ 표기를 허용한다.',
     'review', ('qualifier_interpretation_required',)),
    ('viewing_hours', '12시부터 19시까지', 'contains', '12:00~19:00', None, 'pass', ()),
    ('viewing_hours', '12시부터 19시까지', 'contains', 'PM 12:00 – PM 7:00', None, 'pass', ()),
    ('viewing_hours', '12시부터 19시까지', 'contains', '12:00~18:00', None, 'fail', ('value_mismatch',)),
    # 글자로는 '19시'가 들어 있어도 가리키는 시각이 다르다
    ('event_time', '19시', 'contains', '19시 30분', None, 'fail', ('value_mismatch',)),
    # 오전·오후가 한쪽에만 있으면 코드가 정하지 않고 기존 글자 비교 결과를 따른다
    ('event_time', '19시 30분', 'contains', '7:30', None, 'fail', ('value_mismatch',)),
    ('event_time', '19시 30분', 'contains', '7:30', DATE_NOTATION, 'review', ('qualifier_interpretation_required',)),
])
def test_weekdays_years_and_times_pass_in_another_notation(field, expected, operator, observed, allowance, verdict, issues):
    assert judged(detailed(field, expected, observed, operator=operator, allowance=allowance)) == (verdict, issues)


def test_plain_wording_and_unstructured_criteria_are_compared_as_before():
    assert judged(detailed('venue', '열린문화공간 후소 전시실', '열린문화공간 후소 전시실')) == ('pass', ())
    assert judged(detailed('venue', '열린문화공간 후소 전시실', '후소 전시실')) == ('fail', ('value_mismatch',))
    # 구조 정보가 없는 기준은 날짜처럼 보여도 글자대로 비교한다
    legacy = ReferenceValidator().validate(text_request('2026.5.14', 'equals', observed='2026년 5월 14일'))
    assert (legacy.items[0].verdict, legacy.items[0].issues) == ('fail', ('value_mismatch',))


def measured(field, expected, *, original=JPEG, **options):
    options.setdefault('target', 'file_metadata')
    options.setdefault('operator', 'equals')
    return detailed(field, expected, options.pop('observed', '모델이 읽었다고 한 값'), original=original, **options)


@pytest.mark.parametrize('field,expected,options,original,verdict,issues,shown', [
    ('pixel_width', '3000', dict(kind='number', unit='픽셀'), JPEG, 'pass', (), ('3000', '픽셀')),
    ('pixel_height', '2000', dict(kind='number', unit='픽셀'), JPEG, 'pass', (), ('2000', '픽셀')),
    ('pixel_width', '2400', dict(kind='number', unit='픽셀'), JPEG, 'fail', ('value_mismatch',), ('3000', '픽셀')),
    ('pixel_height', '1800', dict(kind='number', unit='px', operator='gte'), JPEG, 'pass', (), ('2000', 'px')),
    ('file_format', 'JPEG (.jpg 또는 .jpeg)', {}, JPEG, 'pass', (), ('JPEG', None)),
    ('file_format', 'PNG', {}, JPEG, 'fail', ('value_mismatch',), ('JPEG', None)),
    ('file_format', 'PNG', dict(allowance='허용 범위: 인쇄소와 협의한 형식도 가능하다.'), JPEG,
     'review', ('qualifier_interpretation_required',), ('JPEG', None)),
    ('color_mode', 'RGB', {}, JPEG, 'pass', (), ('RGB', None)),
    ('color_mode', 'CMYK', {}, JPEG, 'fail', ('value_mismatch',), ('RGB', None)),
    ('color_mode', 'RGB', {}, JPEG.model_copy(update={'format': 'PNG', 'color_mode': 'RGBA'}), 'pass', (), ('RGBA', None)),
])
def test_file_requirements_are_settled_from_the_uploaded_original(field, expected, options, original, verdict, issues, shown):
    result = ReferenceValidator().validate(measured(field, expected, original=original, **options))
    item = result.items[0]
    assert (item.verdict, item.issues) == (verdict, issues)
    # 결과에 보이는 값은 모델의 답이 아니라 측정한 파일이다
    assert (item.observed, item.observed_unit, item.observation_status, item.image) == (*shown, 'observed', None)
    assert item.evidence_ids == ('e1',)
    assert item.comparison_observed == shown[0]


def test_the_inspectors_answer_about_a_file_property_is_not_weighed():
    for changes in (dict(uncertainty='확인하지 못함'), dict(observation_status='unknown'), dict(verdict='fail'),
                    dict(verdict='review'), dict(observation_status='absent', verdict='fail', observed='')):
        assert judged(measured('pixel_width', '3000', kind='number', unit='픽셀', **changes)) == ('pass', ())
    without_answer = measured('pixel_width', '2400', kind='number', unit='픽셀')
    without_answer = without_answer.model_copy(update={
        'inspection': without_answer.inspection.model_copy(update={'observations': ()})})
    assert judged(without_answer) == ('fail', ('value_mismatch',))


@pytest.mark.parametrize('field,expected,options,original', [
    ('pixel_width', '3000', dict(kind='number', unit='픽셀'), None),          # 측정값 없이 접수된 실행
    ('pixel_width', '3000', dict(kind='number', unit='mm'), JPEG),
    ('file_format', 'PDF', {}, JPEG),
    ('color_mode', 'RGB', {}, JPEG.model_copy(update={'format': 'PNG', 'color_mode': 'P'})),
    ('resolution_dpi', '300', dict(kind='number', unit='dpi'), JPEG),
    ('logo', '주최 로고', dict(target='poster_visual', kind='visual', operator='present'), JPEG),
    ('quantity', '1', dict(target='delivery', kind='number', unit='점'), JPEG),
])
def test_what_the_file_cannot_settle_stays_with_a_person(field, expected, options, original):
    verdict, issues = judged(measured(field, expected, original=original, observed='3000', **options))
    assert verdict == 'review' and 'typed_attribute_review_required' in issues


def test_a_measured_file_still_needs_complete_approved_evidence():
    req = changed(measured('pixel_width', '3000', kind='number', unit='픽셀'), ['evidence', 'status'], 'stale')
    result = ReferenceValidator().validate(req)
    assert (result.items[0].verdict, result.items[0].issues) == ('review', ('context_incomplete',))
    assert result.items[0].comparison_observed is None
    needs_person = measured('pixel_width', '3000', kind='number', unit='픽셀', allowance='허용 범위: 별도 협의한 크기는 예외로 한다.')
    assert judged(needs_person) == ('review', ('qualifier_interpretation_required',))


def test_a_custom_validator_cannot_pass_a_measured_difference():
    req = measured('pixel_width', '2400', kind='number', unit='픽셀')
    result = ReferenceValidator().validate(req)
    forged = result.model_copy(update={'verdict': 'pass', 'items': (
        result.items[0].model_copy(update={'verdict': 'pass', 'issues': ()}),)})
    with pytest.raises(ValueError, match='VALIDATOR_UNSUPPORTED_PASS'):
        validate_validated(req, forged)


def test_requests_without_measured_facts_keep_their_stored_shape():
    plain = request()
    assert 'original' not in plain.model_dump(mode='json')
    assert ValidateRequest.model_validate(plain.model_dump(mode='json')) == plain
    with_facts = measured('pixel_width', '3000', kind='number', unit='픽셀')
    data = with_facts.model_dump(mode='json')
    assert data['original'] == JPEG.model_dump(mode='json')
    assert ValidateRequest.model_validate(data) == with_facts


def test_the_worker_hands_pinned_file_facts_to_a06_only():
    from backend.agent_framework.adapters.generate_examples import role_examples
    from backend.agent_framework.durable_worker import stage_request
    from backend.agent_framework.postgres import digest, payload

    examples = role_examples()
    inspect_input, inspection = examples['A04_A05']
    snapshot = RunSnapshot(context=inspect_input.context, profile='test', synthetic=True, execution_status='reviewing',
                           criteria=examples['A02'][1], evidence=inspect_input.evidence, inspection=inspection)
    fixed = {'document': payload(examples['A02'][0].document), 'asset': payload(inspect_input.asset)}
    plain = stage_request(snapshot, fixed, 'A06')
    assert plain.original is None and digest(plain) == digest(examples['A06'][0])
    pinned = stage_request(snapshot, fixed | {'original': payload(JPEG)}, 'A06')
    assert pinned.original == JPEG and digest(pinned) != digest(plain)
    assert 'original' not in payload(stage_request(snapshot, fixed | {'original': payload(JPEG)}, 'A04_A05'))


PROJECT, VERSION, FILE = (str(UUID(int=n)) for n in (1, 2, 3))


class Storage:
    """Stands in for the stored original: the reader checks size and hash before handing bytes over."""
    def __init__(self, content):
        self.content = content

    @contextmanager
    def verified(self, storage_key, size, digest):
        from backend.app.storage.local import StorageError
        if len(self.content) != size or sha256(self.content).hexdigest() != digest:
            raise StorageError('STORAGE_CORRUPT')
        yield BytesIO(self.content)


def encoded(mode, size, format, **options):
    from PIL import Image
    stream = BytesIO()
    Image.new(mode, size).save(stream, format=format, **options)
    return stream.getvalue()


def stored(content, media_type, width, height):
    from backend.agent_framework.files import StoredFileMetadata
    from backend.app.storage.local import key
    context = Context(project_id=PROJECT, run_id='r1', actor_id='u1', document_version_id='d1',
                      criteria_version_id='c1', asset_version_id=VERSION)
    reference = AssetRef(file_id=FILE, asset_version_id=VERSION, sha256=sha256(content).hexdigest(),
                         media_type=media_type, width=width, height=height)
    metadata = StoredFileMetadata(file_id=FILE, project_id=PROJECT, version_id=VERSION, kind='poster',
                                  storage_key=key(PROJECT, FILE), media_type=media_type, byte_size=len(content),
                                  sha256=reference.sha256, width=width, height=height, coordinate_space='encoded')
    return context, reference, metadata


def rotated_jpeg():
    from PIL import Image
    exif = Image.Exif()
    exif[274] = 6   # 옆으로 누운 채 저장되어 표시할 때 돌려 보는 사진
    return encoded('RGB', (40, 30), 'JPEG', exif=exif)


@pytest.mark.parametrize('content,media_type,size,facts', [
    (encoded('RGB', (40, 30), 'JPEG'), 'image/jpeg', (40, 30), ('JPEG', 40, 30, 'RGB')),
    (encoded('CMYK', (40, 30), 'JPEG'), 'image/jpeg', (40, 30), ('JPEG', 40, 30, 'CMYK')),
    (encoded('L', (40, 30), 'JPEG'), 'image/jpeg', (40, 30), ('JPEG', 40, 30, 'L')),
    (encoded('RGBA', (40, 30), 'PNG'), 'image/png', (40, 30), ('PNG', 40, 30, 'RGBA')),
    (encoded('P', (40, 30), 'PNG'), 'image/png', (40, 30), ('PNG', 40, 30, 'P')),
    (encoded('RGB', (40, 30), 'WEBP'), 'image/webp', (40, 30), ('WEBP', 40, 30, 'RGB')),
    # 크기는 화면에 보이는 방향으로 잰다
    (rotated_jpeg(), 'image/jpeg', (40, 30), ('JPEG', 30, 40, 'RGB')),
])
def test_the_uploaded_original_is_measured_from_its_verified_bytes(content, media_type, size, facts):
    from backend.agent_framework.poster_review import measure_original
    context, reference, metadata = stored(content, media_type, *size)
    result = measure_original(Storage(content), context, reference, metadata)
    assert (result.format, result.width, result.height, result.color_mode) == facts
    assert (result.file_id, result.sha256) == (FILE, sha256(content).hexdigest())


def test_an_original_that_does_not_match_its_record_is_not_measured():
    from backend.agent_framework.poster_review import measure_original
    content = encoded('RGB', (40, 30), 'JPEG')
    context, reference, metadata = stored(content, 'image/jpeg', 40, 30)
    assert measure_original(Storage(content + b'changed'), context, reference, metadata) is None
    # 기록된 형식이나 크기가 실제 파일과 다르면 측정값을 만들지 않는다
    assert measure_original(Storage(content), *stored(content, 'image/png', 40, 30)) is None
    assert measure_original(Storage(content), *stored(content, 'image/jpeg', 30, 40)) is None
    assert measure_original(Storage(b'not an image'), *stored(b'not an image', 'image/jpeg', 40, 30)) is None


class Rows:
    """Answers queries in order, so record checks run without a database."""
    def __init__(self, *rows):
        self.rows, self.statements = list(rows), []

    def execute(self, statement):
        self.statements.append(statement)
        row = self.rows.pop(0)
        return SimpleNamespace(mappings=lambda: SimpleNamespace(one_or_none=lambda: row))


def original_rows(**changes):
    from backend.app.storage.local import key
    image = {'original_file_id': UUID(FILE), 'original_sha256': 'c' * 64, 'original_width': 30, 'original_height': 40}
    file = {'file_id': UUID(FILE), 'state': 'stored', 'storage_backend': 'local', 'file_role': 'original_image',
            'parent_file_id': None, 'sha256': 'c' * 64, 'detected_mime': 'image/jpeg', 'byte_size': 1234,
            'storage_key': key(PROJECT, FILE)}
    return image, {**file, **changes}


def test_the_original_file_record_is_described_for_a_verified_read():
    from sqlalchemy.dialects import postgresql
    from backend.agent_framework.poster_review import original_metadata, same_original

    connection = Rows(*original_rows())
    reference, metadata = original_metadata(connection, UUID(PROJECT), UUID(VERSION))
    assert (reference.file_id, reference.media_type, reference.width, reference.height) == (FILE, 'image/jpeg', 30, 40)
    assert (metadata.byte_size, metadata.sha256, metadata.coordinate_space, metadata.kind) == (1234, 'c' * 64, 'encoded', 'poster')
    sql = [str(statement.compile(dialect=postgresql.dialect())) for statement in connection.statements]
    assert 'asset_versions' in sql[0] and 'stored_files' in sql[1]

    facts = JPEG.model_copy(update={'file_id': FILE, 'sha256': 'c' * 64})
    assert same_original(Rows(original_rows()[0]), UUID(PROJECT), UUID(VERSION), facts)
    assert not same_original(Rows(original_rows()[0]), UUID(PROJECT), UUID(VERSION), facts.model_copy(update={'sha256': 'd' * 64}))
    assert not same_original(Rows(None), UUID(PROJECT), UUID(VERSION), facts)


@pytest.mark.parametrize('changes', [
    {'state': 'deleted'}, {'file_role': 'display_image'}, {'parent_file_id': UUID(int=9)}, {'sha256': 'd' * 64},
    {'detected_mime': 'application/pdf'}, {'storage_key': 'objects/elsewhere'}, {'storage_backend': 's3'},
])
def test_an_inconsistent_original_record_is_not_used(changes):
    from backend.agent_framework.poster_review import original_metadata
    assert original_metadata(Rows(*original_rows(**changes)), UUID(PROJECT), UUID(VERSION)) is None
    assert original_metadata(Rows(None), UUID(PROJECT), UUID(VERSION)) is None
    assert original_metadata(Rows(original_rows()[0], None), UUID(PROJECT), UUID(VERSION)) is None
