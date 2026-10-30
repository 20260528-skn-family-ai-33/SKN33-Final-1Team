"""PDF 기대값 전달·문구 근거·파일 원본 측정값의 회귀 검사. 실제 모델은 실행하지 않는다."""
import pytest

from backend.app.document_conditions import render_condition
from backend.app.review_rules import review_criteria
from backend.app.structured_review import compare


def line(identifier, text, box, score=.99):
    return dict(id=identifier, text=text, bbox_xyxy=box, confidence=score, recognition_engine='paddleocr')


def criterion(label, value, inspection='', *, level='필수', kind='information'):
    document = dict(label=label, requirement=value, inspection=inspection, level=level)
    return dict(id='test', text=render_condition(document), type=kind, source='test.pdf · OCR 판독문', document_condition=document)


def check(c, records=(), source=None):
    return compare([c], review_criteria([c], list(records), multiline=True), list(records), source or {})[0]


def test_document_instructions_are_not_searched_as_printed_words():
    c = criterion('행사 시간', '17:30', '17:30을 표기한다. 공백 차이는 허용하되 시간 값 변경은 불가하다.')
    actual = check(c, [line('time', '17:30', [10, 10, 110, 40])])
    assert actual['status'] == 'pass' and actual['evidence'][0]['ocr_id'] == 'time'
    assert actual['observed_text'] == '17:30'


def test_angled_neighboring_location_fragments_are_combined_but_unrelated_rows_are_not():
    c = criterion('행사 장소', '광교호수공원 재미난 밭', '붙여쓰기와 줄바꿈을 허용한다.')
    left = line('park', '광교호수공원', [1356, 2015.5, 1852.5, 2274])
    right = line('field', '재미난밭', [1791.5, 2161, 2142, 2434.5])
    assert check(c, [left, right])['status'] == 'pass'
    right['bbox_xyxy'] = [1791.5, 2600, 2142, 2800]
    assert check(c, [left, right])['status'] == 'review'


@pytest.mark.parametrize('correct_column,score,status', [(True,.99,'pass'),(False,.99,'review'),(True,.85,'review')])
def test_lineup_text_needs_supported_date_in_same_column(correct_column, score, status):
    c = criterion('18일 대표 출연진', '정동환(멜로망스) x Funk Messengers',
                  '9월 18일 라인업 영역에 표기한다. x 또는 ×, 대소문자·공백 차이는 허용한다.')
    rows = [line('date', '9.18.(금)', [10 if correct_column else 500, 10, 100 if correct_column else 600, 40]),
            line('artist', '정동환(멜로망스)× Funk Messengers', [10, 100, 400, 140], score)]
    result = check(c, rows)
    assert result['status'] == status
    if status == 'pass':
        assert {e['ocr_id'] for e in result['evidence']} == {'date', 'artist'}


def test_passing_printed_text_does_not_prove_additional_layout_constraints():
    result = check(criterion('상품명', '테스트 라떼', '왼쪽 위에 배치한다.'),
                   [line('name', '테스트 라떼', [10, 10, 100, 40])])
    assert result['status'] == 'review' and result['evidence']


def test_lineup_does_not_skip_a_nearer_different_date_or_extra_visual_constraints():
    c = criterion('18일 대표 출연진', '정동환', '9월 18일 라인업 영역에 표기한다.')
    rows = [line('older', '9.18.(금)', [10, 10, 100, 40]),
            line('nearer', '9.19.(토)', [10, 60, 100, 90]),
            line('artist', '정동환', [10, 100, 400, 140])]
    assert check(c, rows)['status'] == 'review'
    c = criterion('18일 대표 출연진', '정동환', '9월 18일 라인업 영역에 표기한다. 글꼴은 고딕체를 쓴다.')
    assert check(c, [rows[0], rows[2]])['status'] == 'review'


def test_dates_do_not_borrow_expected_year_from_pdf():
    c = criterion('행사 날짜와 요일', '2026.09.18.(금)-2026.09.19.(토)', '앞자리 0 차이는 허용한다.')
    actual = check(c, [line('d1', '9.18.(금)', [10, 10, 100, 40]), line('d2', '9.19.(토)', [500, 10, 600, 40])])
    assert actual['status'] == 'review' and '연도' in actual['reason']
    assert len(actual['evidence']) == 2


@pytest.mark.parametrize('dimensions,status', [([2527,3572],'pass'),([600,848],'fail'),(None,'review')])
def test_exact_dimensions_are_checked_against_oriented_source(dimensions, status):
    result = check(criterion('이미지 크기', '가로 2527 x 세로 3572픽셀, 세로형'), source={'oriented_dimensions':dimensions})
    assert result['status'] == status and result['check_method'] == 'source_image_metadata'


@pytest.mark.parametrize('patch,status', [({},'pass'),({'original_mode':'CMYK'},'fail'),
                                         ({'actual_format':'PNG'},'fail'),({'original_extension':'.png'},'fail'),
                                         ({'original_frames':2},'fail'),({'original_mode':None},'review')])
def test_file_spec_needs_original_format_mode_extension_and_frame_count(patch,status):
    c=criterion('파일 형식과 색상', '정적 JPEG 이미지 1개, 확장자 .jpg, RGB 색상 모드',
                '실제 파일 형식·전체 디코딩·색상 모드를 확인한다.')
    source={'actual_format':'JPEG','original_mode':'RGB','original_extension':'.jpg','original_frames':1,**patch}
    actual=check(c,source=source)
    assert actual['status']==status and len(actual['metadata_checks'])==4


def test_file_metadata_pass_does_not_hide_extra_visual_requirement():
    result=check(criterion('이미지 크기','가로 2527 x 세로 3572픽셀, 세로형','로고를 왼쪽 위에 배치한다.'),
                 source={'oriented_dimensions':[2527,3572]})
    assert result['status']=='review'
    assert all(c['status']=='pass' for c in result['metadata_checks'])


@pytest.mark.parametrize('dimensions,status', [([1024,1280],'pass'),([1025,1280],'pass'),
                                             ([1024,1281],'pass'),([1026,1280],'fail'),(None,'review')])
def test_brief_recommended_aspect_ratio_uses_only_its_explicit_pixel_tolerance(dimensions, status):
    result = check(criterion('이미지 비율','4:5','픽셀 반올림 차이는 ±1px까지 허용한다.',level='권장'),
                   source={'oriented_dimensions':dimensions})
    assert result['status']==status and result['check_method']=='source_image_metadata'
    if dimensions==[1025,1280]:
        assert check(criterion('이미지 비율','4:5'),source={'oriented_dimensions':dimensions})['status']=='fail'


@pytest.mark.parametrize('width,status', [(1024,'pass'),(1023,'fail'),(1200,'pass'),(None,'review')])
def test_brief_minimum_width_needs_no_printed_pixel_text(width, status):
    result=check(criterion('최소 이미지 폭','1024px 이상'),source={'oriented_dimensions':[width,1280] if width else None})
    assert result['status']==status and result['check_method']=='source_image_metadata'


@pytest.mark.parametrize('label,value,source,status', [
    ('파일 형식','PNG',{'actual_format':'PNG'},'pass'),
    ('파일 형식','PNG',{'actual_format':'JPEG'},'fail'),
    ('색상 모드','RGB',{'original_mode':'RGB'},'pass'),
    ('색상 모드','RGB',{'original_mode':'CMYK'},'fail'),
    ('색상 모드','RGB',{},'review'),
])
def test_brief_separate_format_and_color_mode_use_original_metadata(label,value,source,status):
    assert check(criterion(label,value),source=source)['status']==status


def test_optional_original_is_not_treated_as_visual_failure():
    actual=check(criterion('편집 가능한 원본','PSD 원본은 범위에 포함하지 않는다.',level='선택'))
    assert actual['status']=='review' and actual['check_method']=='optional_delivery'
    assert '미제출' in actual['reason']


def test_stale_pdf_metadata_cannot_pass_an_edited_expectation():
    c=criterion('행사 시간','17:30')
    c['text']='행사 시간: [필수] 18:30'
    actual=check(c,[line('time','17:30',[10,10,100,40])])
    assert actual['status']=='review' and actual.get('check_method')!='document_expected_text'


def test_legacy_confirmed_text_is_still_readable_after_compact_format_change():
    c=criterion('행사 시간','17:30','공백 차이는 허용한다.')
    c['text']='행사 시간: [필수] 17:30 / 판단 기준: 공백 차이는 허용한다.'
    assert check(c,[line('time','17:30',[10,10,100,40])])['status']=='pass'


def test_numeric_substring_does_not_pass_a_different_price():
    actual=check(criterion('가격','3,000원'),[line('price','13,000원',[10,10,100,40])])
    assert actual['status']=='review'


def plain(text, *, kind='information'):
    return dict(id='plain',text=text,type=kind,source='user')


def test_plain_location_uses_adjacent_fragments_without_pdf_metadata():
    rows=[line('park','광교호수공원',[1356,2015.5,1852.5,2274]),
          line('field','재미난밭',[1791.5,2161,2142,2434.5])]
    actual=check(plain('장소: 광교호수공원 재미난 밭'),rows)
    assert actual['status']=='pass' and actual['check_method']=='plain_expected_text'
    assert {e['ocr_id'] for e in actual['evidence']}=={'park','field'}
    rows[1]['bbox_xyxy']=[1791.5,2600,2142,2800]
    assert check(plain('장소: 광교호수공원 재미난 밭'),rows)['status']=='review'


@pytest.mark.parametrize('header,score,status', [('9.18.(금)',.99,'pass'),('9.19.(토)',.99,'review'),('9.18.(금)',.85,'review')])
def test_plain_day_specific_lineup_checks_date_and_cross_separator(header,score,status):
    rows=[line('date',header,[10,10,100,40]),line('artist','정동환×Funk Messengers',[10,100,400,140],score)]
    actual=check(plain('18일 대표 출연진: 정동환 x Funk Messengers'),rows)
    assert actual['status']==status
    if status=='pass':
        assert actual['check_method']=='plain_text_and_date_column'
        assert {e['ocr_id'] for e in actual['evidence']}=={'date','artist'}


def test_plain_day_label_does_not_choose_between_two_months():
    rows=[line('date','9.18.(금)',[10,10,100,40]),line('other','10.18.(일)',[500,10,600,40]),
          line('artist','정동환×Funk Messengers',[10,100,400,140])]
    assert check(plain('18일 출연진: 정동환 x Funk Messengers'),rows)['status']=='review'


def test_plain_metadata_compares_source_values_without_searching_for_pixel_text():
    source={'oriented_dimensions':[2527,3572],'actual_format':'JPEG','original_extension':'.jpg','original_mode':'RGB','original_frames':1}
    for text in ['이미지 크기: 가로 2527 x 세로 3572픽셀, 세로형',
                 '파일 형식과 색 상: 정적 JPEG 이미지 1개,확장자 .jpg, RGB 색상 모드']:
        actual=check(plain(text),source=source)
        assert actual['status']=='pass' and actual['check_method']=='source_image_metadata'
    assert check(plain('이미지 크기: 가로 2527 x 세로 3572픽셀, 세로형'),
                 source={'oriented_dimensions':[600,848]})['status']=='fail'


def test_plain_compact_date_keeps_the_existing_grounded_date_parser():
    c=plain('기간: 2026.10.23 ~ 10.25')
    rows=[line('date','2026.10.23 ~ 10.25',[10,10,300,40])]
    before=review_criteria([c],rows,multiline=True)[0]
    assert before['status']=='pass'
    assert compare([c],[before.copy()],rows,{})[0]==before


def test_plain_ocr_failure_stays_review_and_visual_checks_are_not_promoted():
    assert check(plain('행사명: 2026 수원 재즈 페스티벌'),
                 [line('wrong','2021 수원재시게시밀',[10,10,400,50],.71)])['status']=='review'
    assert check(plain('상단 로고 배치: 수원특례시',kind='visual'),
                 [line('logo','수원특례시',[10,10,150,50])])['status']=='review'


def test_plain_label_separator_does_not_rewrite_required_punctuation():
    c=plain('필수 문구: A：B')
    assert check(c,[line('ascii','A:B',[10,10,150,50])])['status']=='review'
    assert check(c,[line('original','A：B',[10,10,150,50])])['status']=='pass'
