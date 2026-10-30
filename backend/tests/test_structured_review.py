from backend.app.structured_review import compare


def record(identifier, text, box, confidence=0.99):
    return dict(id=identifier, text=text, bbox_xyxy=box, confidence=confidence, recognition_engine='paddleocr')


def review(text, analysis=None, kind='information'):
    return (dict(id='a', text=text, type=kind),
            dict(criterion_id='a', type=kind, status='review', reason='확인 필요',
                 observed_text='', evidence=[], model_analysis=analysis or {'status':'unavailable'}))


def parsed(field, value):
    return {'status':'success', 'needs_review':[], 'conditions':[
        dict(type='information', field=field, operator='equals', value=value)]}


def test_adjacent_text_matches_but_separate_rows_do_not():
    criterion, result = review('문구: 좋은 하루', parsed('other_text', '좋은 하루'))
    same_line = [record('1','좋은',[0,0,20,10]), record('2','하루',[22,0,42,10])]
    assert compare([criterion], [result], same_line, {'oriented_dimensions':[100,100]})[0]['status'] == 'pass'
    criterion, result = review('문구: 좋은 하루', parsed('other_text', '좋은 하루'))
    different_lines = [record('1','좋은',[0,0,20,10]), record('2','하루',[0,20,20,30])]
    assert compare([criterion], [result], different_lines, {'oriented_dimensions':[100,100]})[0]['status'] == 'review'


def test_one_of_accepts_either_grounded_value_when_model_abstains():
    criterion, result = review('브랜드: 온결 또는 ONGYEOL 중 하나 필수 표기')
    rows = [record('1','온결',[0,0,20,10])]
    actual = compare([criterion], [result], rows, {'oriented_dimensions':[100,100]})[0]
    assert actual['status'] == 'pass'
    assert actual['evidence'][0]['ocr_id'] == '1'


def test_conflicting_price_stays_review_with_observed_amount():
    criterion, result = review('가격: 5,900원 (가격 변경 불가)', parsed('fee', '5,900원'))
    rows = [record('1','6,900원',[0,0,60,10])]
    actual = compare([criterion], [result], rows, {'oriented_dimensions':[100,100]})[0]
    assert actual['status'] == 'review'
    assert actual['observed_text'] == '6,900원'
    assert '5,900' in actual['reason'] and '6,900' in actual['reason']


def test_plain_fallback_keeps_existing_model_grounded_price_evidence():
    criterion, result = review('가격: 5,900원', parsed('fee', '5,900원'))
    actual=compare([criterion],[result],[record('price','6,900원',[0,0,60,10])],{})[0]
    assert actual['status']=='review' and actual['observed_text']=='6,900원'
    assert actual['check_method']=='ocr_unique_price'


def test_original_mode_is_not_inferred_from_normalized_rgb():
    criterion, result = review('색상 모드: RGB', kind='visual')
    actual = compare([criterion], [result], [], {'oriented_dimensions':[100,100], 'actual_format':'PNG'})[0]
    assert actual['status'] == 'review'
    assert '원본 색상 모드' in actual['reason']


def test_ratio_with_explicit_pixel_tolerance_uses_source_dimensions():
    criterion, result = review('이미지 방향 및 비율: SNS 세로형, 가로:세로 4:5 권장 (픽셀 반올림 오차 ±1px 허용)', kind='visual')
    actual = compare([criterion], [result], [], {'oriented_dimensions':[1122,1402]})[0]
    assert actual['status'] == 'pass'
