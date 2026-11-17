"""Typed value comparisons with hand-set outcomes; not a measurement of any model's reading accuracy."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.agent_framework import typed_values as tv
from backend.agent_framework.qualifier_rules import notation_only


def criterion(field, expected, *, kind='text', operator='contains', target='poster_text', normalized=None, unit=None):
    return SimpleNamespace(kind=kind, operator=operator, expected=expected, unit=unit,
                           details=SimpleNamespace(target=target, field=field, normalized_expected=normalized))


@pytest.mark.parametrize('expected,observed,role,outcome', [
    # 같은 날짜의 여러 표기
    (date(2026, 5, 14), '2026.5.14', 'start', 'match'),
    (date(2026, 5, 14), '2026. 5. 14.(목)', 'start', 'match'),
    (date(2026, 5, 14), '2026년 5월 14일 목요일', 'start', 'match'),
    (date(2026, 5, 14), '2026-05-14', 'start', 'match'),
    (date(2026, 5, 14), '2026/05/14 THU', 'start', 'match'),
    (date(2026, 5, 14), '2026 년 5 월 14 일', 'start', 'match'),
    (date(2026, 5, 14), 'MAY 14, 2026', 'start', 'match'),
    (date(2026, 5, 14), '14 May 2026', 'start', 'match'),
    (date(2026, 5, 14), '2026 MAY 14', 'start', 'match'),
    # 기간: 시작일은 첫 날짜, 종료일은 마지막 날짜
    (date(2026, 5, 14), '2026.5.14 – 8.30', 'start', 'match'),
    (date(2026, 8, 30), '2026.5.14 – 8.30', 'end', 'match'),
    (date(2026, 8, 30), '2026.05.14(목)~2026.08.30(일)', 'end', 'match'),
    (date(2026, 8, 30), '2026-05-14~2026-08-30', 'end', 'match'),
    (date(2026, 8, 30), '2026.5.14-8.30', 'end', 'match'),
    (date(2026, 10, 25), '2026.10.23 ~ 10.25', 'end', 'match'),
    (date(2026, 1, 5), '2025.12.20 ~ 1.5', 'end', 'match'),
    (date(2026, 5, 16), '2026.5.14-16', 'end', 'match'),
    (date(2026, 5, 16), '2026.5.14(목)~16(토)', 'end', 'match'),
    (date(2026, 5, 16), '2026.5.14 – 16일', 'end', 'match'),
    (date(2026, 5, 14), '2026.5.14-16', 'start', 'match'),
    (date(2026, 8, 30), '~ 2026.8.30', 'end', 'match'),
    (date(2026, 5, 14), '2026.5.14 ~ 미정', 'start', 'match'),
    # 연도까지 적힌 다른 날짜만 불일치로 확정한다
    (date(2026, 5, 14), '2026.5.15', 'start', 'mismatch'),
    (date(2026, 5, 15), '2026.5.14 – 2026.8.30', 'start', 'mismatch'),
    (date(2026, 8, 31), '2026.5.14 – 2026.8.30', 'end', 'mismatch'),
    (date(2026, 10, 25), '2027.10.25', 'end', 'mismatch'),
    (date(2025, 11, 2), '2025.11.3', 'single', 'mismatch'),
    (date(2026, 5, 1), '2026.5.14', 'single', 'mismatch'),          # 글자로는 2026.5.1이 들어 있다
    (date(2026, 4, 30), '2026.4.29(수) - 19:30', 'single', 'mismatch'),
    # 추론하거나 맞춘 연도로는 불일치를 확정하지 않는다
    (date(2026, 8, 31), '2026.5.14 – 8.30', 'end', 'unknown'),
    (date(2026, 5, 17), '2026.5.14-16', 'end', 'unknown'),
    (date(2026, 5, 14), 'SINCE 1996 5.14', 'start', 'unknown'),
    (date(2026, 5, 10), '2025.5.14 ~ 5.10', 'end', 'unknown'),      # 1년 가까이 넘어가는 기간은 읽지 않는다
    # 연도가 없거나 순서를 알 수 없는 표기
    (date(2026, 5, 14), '5.14(목)', 'start', 'unknown'),
    (date(2026, 5, 14), '5월 14일', 'start', 'unknown'),
    (date(2026, 5, 16), '5.14-16', 'end', 'unknown'),
    (date(2026, 5, 14), '14.5.2026', 'start', 'unknown'),
    (date(2026, 5, 14), '26.5.14', 'start', 'unknown'),
    (date(2026, 5, 14), '오월 십사일', 'start', 'unknown'),
    (date(2026, 5, 14), '', 'start', 'unknown'),
    (date(2025, 11, 2), '11.2 SUN', 'single', 'unknown'),
    # 따로 적힌 연도가 하나뿐이면 일치를 확인하는 데만 쓴다
    (date(2026, 5, 14), '2026 수원의 영화 5.14', 'start', 'match'),
    # 하루짜리 기준에 기간이 적혔거나 한쪽 끝이 없으면 판정하지 않는다
    (date(2025, 11, 2), '2025.11.2 ~ 11.4', 'single', 'unknown'),
    (date(2026, 5, 14), '2026.5.14-16', 'single', 'unknown'),
    (date(2026, 8, 30), '2026.5.14 ~ 미정', 'end', 'unknown'),
    (date(2026, 5, 14), '2026.5.14 ~ 미정', 'single', 'unknown'),
    (date(2026, 5, 14), '~ 2026.8.30', 'start', 'unknown'),
    (date(2026, 5, 15), '2026.5.14 ~', 'start', 'unknown'),
    (date(2026, 5, 14), '2026.5.14 - 3층 전시실', 'single', 'unknown'),
    (date(2026, 5, 14), '2026.4.1 예매 · 2026.5.14 개막 · 2026.8.30 폐막', 'start', 'unknown'),
    # 날짜 뒤에 시각이 이어지는 것은 기간이 아니다
    (date(2025, 11, 2), '2025.11.2(일) 12:00~19:00', 'single', 'match'),
    (date(2026, 4, 29), '2026.4.29(수) - 19:30', 'single', 'match'),
    (date(2026, 4, 29), '2026.4.29 (WED) – PM 7:30', 'single', 'match'),
    (date(2026, 4, 29), '2026.4.29 - 7시', 'single', 'match'),
    (date(2026, 4, 29), '2026.4.29(수) 19.30', 'start', 'match'),   # 점으로 적은 시각은 날짜가 아니다
    (date(2026, 5, 14), '2026.5.14 - 3층 전시실', 'start', 'match'),
])
def test_dates_are_compared_by_the_day_they_denote(expected, observed, role, outcome):
    assert tv.compare_date(expected, observed, role) == outcome


@pytest.mark.parametrize('expected,observed,outcome', [
    (3, '(목)', 'match'), (3, '2026.5.14(목)', 'match'), (3, 'THU', 'match'), (3, 'Thursday', 'match'),
    (3, '목요일', 'match'), (6, 'SUN', 'match'), (6, '일요일 SUNDAY', 'match'), (2, 'WED.', 'match'), (0, '(월)', 'match'),
    (3, '(금)', 'mismatch'), (3, 'FRI', 'mismatch'),
    (3, '5.14(목) ~ 8.30(일)', 'unknown'),     # 요일이 둘이면 어느 날의 것인지 정하지 않는다
    (3, '5월 14일', 'unknown'),                # 월·일의 글자는 요일이 아니다
    (3, 'thunder', 'unknown'), (3, '', 'unknown'),
])
def test_weekdays_are_read_in_korean_and_english(expected, observed, outcome):
    assert tv.compare_weekday(expected, observed) == outcome


@pytest.mark.parametrize('expected,observed,outcome', [
    (2026, '2026', 'match'), (2026, '2026.5.14', 'match'), (2026, '2026년', 'match'), (2026, '2025', 'mismatch'),
    (2026, "'26", 'unknown'), (2026, '2026 ~ 2027', 'unknown'), (2026, '', 'unknown'),
])
def test_a_year_is_settled_only_when_one_year_is_written(expected, observed, outcome):
    assert tv.compare_year(expected, observed) == outcome


@pytest.mark.parametrize('expected,observed,outcome', [
    ('12시부터 19시까지', '12:00~19:00', 'match'), ('12시부터 19시까지', '12:00 - 19:00', 'match'),
    ('12시부터 19시까지', 'PM 12:00 – PM 7:00', 'match'), ('12시부터 19시까지', '12시부터 19시까지', 'match'),
    ('12시부터 19시까지', '12:00 PM ~ 7:00 PM', 'match'),
    ('12시부터 19시까지', '12:00~19:00 (21:00 불꽃놀이)', 'match'),
    ('19시 30분', '18:30 입장 · 19:30 공연', 'match'),
    ('12시부터 19시까지', '12:00~18:00', 'mismatch'),
    # 사이에 다른 시각이 끼거나 개수가 다르면 같은 시간대라고 정하지 않는다
    ('12시부터 19시까지', '12:00 개장 · 18:30 입장 마감 · 19:00 폐장', 'unknown'),
    ('12시부터 19시까지', '10:00 · 12:00 · 15:00 · 19:00', 'unknown'),
    ('12시부터 19시까지', '11:00 · 18:30 · 20:00', 'unknown'),
    ('19시 30분', '19:30', 'match'), ('19시 30분', 'PM 7:30', 'match'), ('19시 30분', '7:30 PM', 'match'),
    ('19시 30분', '오후 7시 30분', 'match'), ('19시 30분', '오후 7시 반', 'match'),
    ('19시 30분', '19:00', 'mismatch'), ('19시 30분', '8:30', 'mismatch'), ('19시', '19시 30분', 'mismatch'),
    ('19시 30분', '7:30', 'unknown'),            # 오전·오후가 한쪽에만 있다
    ('7시 30분', '19:30', 'unknown'), ('10시', 'AM 10:00', 'unknown'),
    ('7시 30분', '7:30', 'match'),               # 양쪽 다 없으면 적힌 그대로 견준다
    ('오전 10시', 'AM 10:00', 'match'), ('오전 10시', '10 AM', 'match'), ('오전 10시', '22:00', 'mismatch'),
    ('19시 30분', '저녁 7시 30분', 'unknown'),   # 읽지 않는 시간 낱말이 있으면 비교하지 않는다
    ('12시부터 19시까지', '12:00 PM 7:00', 'unknown'),   # 앞 시각의 오후 표시를 다음 시각에 붙이지 않는다
    ('19시부터 21시까지', '오후 7시 ~ 9시', 'unknown'), ('19시부터 21시까지', '오후 7시 ~ 오후 9시', 'match'),
    ('19시 30분', 'AM 7:30 PM', 'unknown'), ('19시 30분', '16:9', 'unknown'), ('19시 30분', '', 'unknown'),
    ('19시 30분', '2026.4.29(수)', 'unknown'),
])
def test_clock_times_are_compared_by_the_time_they_denote(expected, observed, outcome):
    wanted = tv._bare_clocks(expected)
    assert wanted is not None
    assert tv.compare_clocks(wanted, observed) == outcome


@pytest.mark.parametrize('text,count', [
    ('12:00', 1), ('오후 7시', 1), ('PM 7:30', 1), ('10:00 ~ 18:00', 2),
    ('12시간', None), ('19시 30분 시작', None), ('19:30 (인터미션 15분)', None), ('서울시', None), ('3시리즈', None), ('', None),
])
def test_a_value_is_a_time_only_when_nothing_else_is_written(text, count):
    clocks = tv._bare_clocks(text)
    assert (None if clocks is None else len(clocks)) == count


@pytest.mark.parametrize('field,expected,kind', [
    ('start_date', '2026년 5월 14일', 'date'), ('end_date', '10.25', 'date'), ('event_date', '2025년 11월 2일', 'date'),
    ('event_period', '2026.10.23 ~ 10.25', None),
    ('year', '2026년', 'year'), ('year', '2026', 'year'), ('edition', '2026', None),
    ('weekday', '목요일', 'weekday'), ('weekday', '목', 'weekday'), ('wording', '목', None), ('wording', 'THU', 'weekday'),
    ('deadline', '2026.5.1', 'date'), ('deadline', '2026.5.1 까지 접수', None),
    ('viewing_hours', '12시부터 19시까지', 'time'), ('event_time', '19시 30분', 'time'), ('wording', '12시간', None),
    ('venue', '열린문화공간 후소 전시실', None), ('wording', '제303회 정기연주회', None),
    ('program_name', '첼로 협주곡 1번', None), ('title_phrase', '2025 탄소중립 한마당 축제', None),
])
def test_only_values_of_a_known_shape_take_a_typed_comparison(field, expected, kind):
    assert tv.typed_kind(criterion(field, expected)) == kind


def test_unstructured_and_non_text_criteria_keep_plain_comparison():
    plain = SimpleNamespace(kind='text', operator='contains', expected='2026.5.1', details=None)
    assert tv.typed_kind(plain) is None
    assert tv.typed_kind(criterion('logo', '2026.5.1', kind='visual', operator='present', target='poster_visual')) is None
    assert tv.typed_kind(criterion('start_date', '2026.5.1', operator='manual')) is None


@pytest.mark.parametrize('field,expected,normalized,observed,outcome', [
    ('start_date', '2026년 5월 14일', '2026-05-14', '2026.5.14(목)', 'match'),
    ('start_date', '2026년 5월 14일', None, '2026.5.14(목)', 'match'),
    ('start_date', '2026년 5월 14일(목)', None, '2026.5.14', 'match'),
    ('end_date', '10.25', None, '2026.10.25', 'unknown'),            # 기준에 연도가 없다
    ('end_date', '10.25', '2026-10-25', '2027.10.25', 'mismatch'),   # 검증된 변환값은 연도를 안다
    ('end_date', '10.25', '2026-10-25', '2026.10.23 ~ 10.25', 'match'),
    ('year', '2026년', None, '2026', 'match'), ('weekday', '목요일', None, '(목)', 'match'),
    ('viewing_hours', '12시부터 19시까지', None, '12:00~19:00', 'match'),
    ('deadline', '2026.5.1', None, '2026년 5월 1일', 'match'), ('deadline', '2026.5.1', None, '2026.5.14', 'mismatch'),
])
def test_a_criterion_is_compared_by_its_typed_value(field, expected, normalized, observed, outcome):
    assert tv.compare_typed(criterion(field, expected, normalized=normalized), observed) == outcome


@pytest.mark.parametrize('text,formats', [
    ('JPEG (.jpg 또는 .jpeg)', {'JPEG'}), ('JPEG', {'JPEG'}), ('jpg', {'JPEG'}), ('PNG 또는 JPEG', {'PNG', 'JPEG'}),
    ('파일 형식: JPEG (.jpg 또는 .jpeg)', {'JPEG'}), ('file format: PNG / WEBP', {'PNG', 'WEBP'}),
    ('PDF', {'PDF'}), ('AI 또는 PDF', {'AI', 'PDF'}),
    ('JPEG, PNG는 불가', None), ('고해상도 JPEG', None), ('main.jpg', None), ('', None),
])
def test_a_format_list_is_read_only_when_it_says_nothing_else(text, formats):
    assert tv._formats(text) == formats


def number(raw, unit):
    if not raw.strip().isdigit():
        raise ValueError('invalid')
    return Decimal(raw), unit


JPEG = SimpleNamespace(format='JPEG', width=3000, height=2000, color_mode='RGB', byte_size=None, dpi=None)


def checked(field, expected, *, kind='text', operator='equals', unit=None, qualifiers=(), original=JPEG):
    result = tv.file_check(criterion(field, expected, kind=kind, operator=operator, target='file_metadata', unit=unit),
                           original, qualifiers, number, notation_only)
    return None if result is None else (result.outcome, result.expected, result.observed, result.unit, result.rejects)


def image(mode, format='PNG'):
    return SimpleNamespace(format=format, width=10, height=10, color_mode=mode, byte_size=None, dpi=None)


@pytest.mark.parametrize('arguments,result', [
    (dict(field='pixel_width', expected='3000', kind='number', unit='픽셀'), ('match', '3000', '3000', '픽셀', True)),
    (dict(field='pixel_width', expected='3000', kind='number', unit='px'), ('match', '3000', '3000', 'px', True)),
    (dict(field='pixel_width', expected='3000', kind='number'), ('match', '3000', '3000', None, True)),
    (dict(field='pixel_width', expected='2400', kind='number', unit='픽셀'), ('mismatch', '2400', '3000', '픽셀', True)),
    (dict(field='pixel_height', expected='1800', kind='number', operator='gte', unit='픽셀'), ('match', '1800', '2000', '픽셀', True)),
    (dict(field='pixel_height', expected='1800', kind='number', operator='lte', unit='픽셀'), ('mismatch', '1800', '2000', '픽셀', True)),
    # 허용 문장을 코드가 읽지 못하면 차이를 불충족으로 확정하지 않는다
    (dict(field='pixel_width', expected='2400', kind='number', unit='픽셀', qualifiers=('가로 2400픽셀 이상 권장',)),
     ('mismatch', '2400', '3000', '픽셀', False)),
    (dict(field='file_format', expected='JPEG (.jpg 또는 .jpeg)', qualifiers=('파일 형식: JPEG (.jpg 또는 .jpeg)',)),
     ('match', 'JPEG', 'JPEG', None, True)),
    (dict(field='file_format', expected='PNG'), ('mismatch', 'PNG', 'JPEG', None, True)),
    # 형식만 나열한 원문 줄은 받아들이는 형식을 모두 알려 준다
    (dict(field='file_format', expected='PNG', qualifiers=('파일 형식: PNG 또는 JPEG',)), ('match', 'JPEG / PNG', 'JPEG', None, True)),
    (dict(field='file_format', expected='PNG', qualifiers=('PNG로 내되 담당자와 협의',)), ('mismatch', 'PNG', 'JPEG', None, False)),
    (dict(field='color_mode', expected='RGB'), ('match', 'RGB', 'RGB', None, True)),
    (dict(field='color_mode', expected='RGB 색상 모드'), ('match', 'RGB', 'RGB', None, True)),
    (dict(field='color_mode', expected='CMYK'), ('mismatch', 'CMYK', 'RGB', None, True)),
    (dict(field='color_mode', expected='RGB', original=image('RGBA')), ('match', 'RGB', 'RGBA', None, True)),
    (dict(field='color_mode', expected='RGB', original=image('L', 'JPEG')), ('mismatch', 'RGB', 'L', None, True)),
    (dict(field='color_mode', expected='CMYK', original=image('CMYK', 'JPEG')), ('match', 'CMYK', 'CMYK', None, True)),
])
def test_file_properties_are_settled_from_the_measured_original(arguments, result):
    assert checked(**arguments) == result


@pytest.mark.parametrize('arguments', [
    dict(field='pixel_width', expected='3000', kind='number', unit='mm'),      # 픽셀이 아닌 단위
    dict(field='pixel_width', expected='3000', unit='픽셀'),                    # 숫자 비교가 아닌 기준
    dict(field='pixel_width', expected='약 3000', kind='number', unit='픽셀'),
    dict(field='file_format', expected='PDF'),                                 # 올릴 수 없는 형식은 이 파일로 판정하지 않는다
    dict(field='file_format', expected='인쇄용 고해상도'),
    dict(field='color_mode', expected='흑백'),
    dict(field='color_mode', expected='RGB', original=image('P')),             # 팔레트 이미지
    dict(field='resolution_dpi', expected='300', kind='number', unit='dpi'),
    dict(field='custom_bleed', expected='3'),
    dict(field='pixel_width', expected='3000', kind='number', unit='픽셀', original=None),
])
def test_what_code_cannot_settle_is_left_to_a_person(arguments):
    assert checked(**arguments) is None


def test_only_file_criteria_are_measured():
    assert tv.file_check(criterion('venue', 'JPEG'), JPEG, (), number, notation_only) is None
    assert tv.file_check(SimpleNamespace(kind='text', operator='equals', expected='JPEG', unit=None, details=None),
                         JPEG, (), number, notation_only) is None


@pytest.mark.parametrize('sentence,only_notation', [
    ('', True),
    # 팀 샘플 의뢰서(docs/poster-baselines/2026-11-03)의 날짜·시각 허용 문장
    ('허용 범위: 날짜 구분자와 앞자리 0의 차이는 허용한다. 연도·월·일·요일 의미는 동일해야 한다.', True),
    ('허용 범위: 날짜 구분자·앞자리 0·시간 범위 구분자 차이는 허용한다. 시작과 종료 시각을 바꾸지 않는다.', True),
    ('허용 범위: 수요일/wed, 날짜 구분자와 0 표기는 허용한다. 날짜와 시각을 변경하지 않는다.', True),
    ('허용 범위: 줄바꿈·공백 차이는 허용한다.', True),
    ('개막일은 2026년 5월 14일로, 요일은 목요일로 표기한다.\n허용 범위: 날짜 구분자와 앞자리 0의 차이는 허용한다.', True),
    # 다른 값을 받아들일 수 있는 문장이 섞이면 표기 차이만이 아니다
    ('허용 범위: 2026 또는 다른 연도 정보와 함께 해석 가능한 ‘26년’ 표기를 허용한다.', False),
    ('허용 범위: 날짜 구분자 차이는 허용한다. 일정이 바뀌면 담당자가 다시 알린다.', False),
    ('허용 범위: 날짜 구분자와 약어 표기는 허용한다.', False),
    ('날짜 구분자 차이는 허용하지 않는다.', False),
    ('허용 범위: ±10픽셀 허용', False),
])
def test_only_notation_allowances_let_a_typed_difference_be_confirmed(sentence, only_notation):
    assert notation_only(sentence) is only_notation
