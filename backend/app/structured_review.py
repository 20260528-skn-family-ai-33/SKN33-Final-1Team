"""Compare a restricted set of grounded requirements with OCR and source metadata.

This does not identify image objects, invent text, or equate model confidence
with correctness. Unsupported, ambiguous and ungrounded cases remain review.
"""
from __future__ import annotations

import re
import unicodedata

from .review_rules import _evidence, _strong

_DOCUMENT_VISUAL_HINT = re.compile(r'가독성|로고|배치|잘리|잘림|가려|가림|위치|상단|하단|왼쪽|오른쪽|중앙|색상|색깔|컬러|글꼴|폰트')


def _compact(value):
    # Common printed middle-dot variants are treated as the same separator.
    value = unicodedata.normalize('NFC', value).translate(str.maketrans({'•': '·', 'ㆍ': '·'}))
    return re.sub(r'\s+', '', value)


def _ocr_runs(records):
    """Horizontal neighboring OCR boxes only; never join separate rows."""
    rows = []
    for record in sorted(records, key=lambda r: ((r['bbox_xyxy'][1] + r['bbox_xyxy'][3]) / 2, r['bbox_xyxy'][0])):
        x1, y1, x2, y2 = record['bbox_xyxy']
        compatible = []
        for row in rows:
            anchor = row[0]['bbox_xyxy']
            overlap = min(y2, anchor[3]) - max(y1, anchor[1])
            if overlap >= .6 * min(y2 - y1, anchor[3] - anchor[1]):
                compatible.append(row)
        if len(compatible) == 1:
            compatible[0].append(record)
        else:
            rows.append([record])
    runs = []
    for row in rows:
        row.sort(key=lambda r: r['bbox_xyxy'][0])
        group = []
        for record in row:
            box = record['bbox_xyxy']
            if group:
                prev = group[-1]['bbox_xyxy']
                if box[0] - prev[2] > 3 * max(box[3] - box[1], prev[3] - prev[1]):
                    runs.append(group)
                    group = []
            group.append(record)
        if group:
            runs.append(group)
    return runs


def _match(target, runs):
    target = _compact(target)
    matches = []
    if not target:
        return matches
    for run in runs:
        for start in range(len(run)):
            for end in range(start + 1, min(len(run), start + 12) + 1):
                group = run[start:end]
                text = _compact(''.join(r['text'] for r in group))
                # Do not let a price such as 3,000 match 13,000.
                prefix = r'(?<!\d)' if target[0].isdigit() else ''
                suffix = r'(?!\d)' if target[-1].isdigit() else ''
                if re.search(prefix + re.escape(target) + suffix, text):
                    matches.append(group)
                    break
    return matches


def _set_match(result, target, groups):
    if not groups:
        return False
    supported = [group for group in groups if _strong(group)]
    group = min(supported or groups, key=len)
    result['observed_text'] = ' '.join(r['text'] for r in group)
    result['evidence'] = _evidence(group)
    result['check_method'] = 'ocr_adjacent_text'
    if supported:
        result['status'] = 'pass'
        result['reason'] = f'서로 인접한 OCR 영역에서 “{target}”을 확인했습니다. 공백만 정규화했으며 원본 확인을 권장합니다.'
    else:
        result['status'] = 'review'
        result['reason'] = f'“{target}”과 비슷한 OCR 영역이 있지만 인식 근거가 충분하지 않아 원본 확인이 필요합니다.'
    return True


def _metadata(criterion, result, source):
    text = criterion['text']
    dimensions = source.get('oriented_dimensions')
    if not isinstance(dimensions, list) or len(dimensions) != 2:
        return False
    width, height = dimensions
    size = re.fullmatch(r'\s*이미지\s*크기\s*[:：]\s*가로\s*폭\s*(\d+)\s*px\s*이상\s*', text, re.I)
    fmt = re.fullmatch(r'\s*파일\s*형식\s*[:：]\s*(PNG|JPEG|JPG|WEBP)\s*', text, re.I)
    mode = re.fullmatch(r'\s*색상\s*모드\s*[:：]\s*(RGB|RGBA|CMYK|L)\s*', text, re.I)
    aspect = re.search(r'가로\s*[:：]\s*세로\s*(\d+)\s*[:：]\s*(\d+)', text)
    if size:
        expected, actual = int(size[1]), width
        passed = actual >= expected
        reason = f'이미지 가로 폭은 {actual}px이고 기준은 {expected}px 이상입니다.'
    elif fmt and source.get('actual_format'):
        expected = fmt[1].upper().replace('JPG', 'JPEG')
        actual = source['actual_format']
        passed = actual == expected
        reason = f'원본 파일 형식은 {actual}이고 기준은 {expected}입니다.'
    elif mode:
        if not source.get('original_mode'):
            result['status'] = 'review'
            result['reason'] = '업로드된 원본 색상 모드가 기록되지 않아 확인할 수 없습니다. 변환된 RGB는 원본 모드를 뜻하지 않습니다.'
            return True
        expected, actual = mode[1].upper(), source['original_mode']
        passed = actual == expected
        reason = f'원본 색상 모드는 {actual}이고 기준은 {expected}입니다.'
    elif aspect:
        a, b = map(int, aspect.groups())
        if not a or not b or '세로형' not in text:
            return False
        tolerance = 1.0 if re.search(r'[±]\s*1\s*px', text, re.I) else 0.0
        error_px = abs(width - height * a / b)
        passed = height > width and error_px <= tolerance
        reason = f'방향 보정 이미지가 {width}×{height}px입니다. {a}:{b} 기준 가로 오차는 {error_px:.2f}px입니다.'
        if '권장' in text:
            reason += ' 이 항목은 권장 조건입니다.'
    else:
        return False
    result.update(status='pass' if passed else 'fail', observed_text=str(actual) if not aspect or size or fmt or mode else f'{width}×{height}px',
                  reason=reason, evidence=[], check_method='source_image_metadata')
    return True


def _price(criterion, result, item, records):
    if not re.match(r'^\s*가격\s*[:：]', criterion['text']) or item.get('field') != 'fee' or item.get('operator') != 'equals':
        return False
    value = item.get('value')
    if not isinstance(value, str) or not re.fullmatch(r'\s*[\d,]+\s*원\s*', value):
        return False
    expected = int(re.sub(r'[^\d]', '', value))
    matches = []
    for record in records:
        for found in re.finditer(r'(?<!\d)(\d[\d,]*)\s*원(?!\w)', record['text']):
            matches.append((int(found.group(1).replace(',', '')), record))
    amounts = {amount for amount, _ in matches}
    if len(amounts) != 1:
        return False
    actual = next(iter(amounts))
    evidence = [record for amount, record in matches if amount == actual]
    if not _strong(evidence, conflict=actual != expected):
        return False
    result.update(status='pass' if actual == expected else 'review',
        observed_text=' / '.join(r['text'] for r in evidence), evidence=_evidence(evidence),
        reason=f'요구 가격은 {expected:,}원이고 OCR에서 확인된 유일한 가격은 {actual:,}원입니다. 같은 상품의 가격인지 원본에서 확인해 주세요.',
        check_method='ocr_unique_price')
    return True


def _date_hint(result, item, runs):
    """Show date fragments without silently supplying an unobserved separator/year."""
    if item.get('field') != 'event_date' or item.get('operator') != 'equals':
        return False
    value = item.get('value')
    if not isinstance(value, str):
        return False
    match = re.fullmatch(r'\s*(\d{4})[./-](\d{1,2})[./-](\d{1,2})\s*[~∼〜～–—-]\s*(\d{4})[./-](\d{1,2})[./-](\d{1,2})\s*', value)
    if not match:
        return False
    year1, month1, day1, year2, month2, day2 = map(int, match.groups())
    if year1 != year2:
        return False
    for run in runs:
        for left, right in zip(run, run[1:]):
            first = re.fullmatch(r'\s*(\d{4})[./-](\d{1,2})[./-](\d{1,2})\.?\s*', left['text'])
            second = re.fullmatch(r'\s*(\d{1,2})[./-](\d{1,2})\.?\s*', right['text'])
            if first and second and tuple(map(int, first.groups())) == (year1, month1, day1) and tuple(map(int, second.groups())) == (month2, day2):
                result.update(status='review', observed_text=left['text'] + ' / ' + right['text'],
                    evidence=_evidence([left, right]), check_method='ocr_date_fragments',
                    reason='OCR에서 시작 날짜와 같은 줄의 종료 월·일을 읽었습니다. 범위 구분자와 종료 연도는 OCR에서 확인되지 않아 원본 확인이 필요합니다.')
                return True
    return False


def _document_metadata(condition, result, source):
    """문서의 설명문 대신 명시한 파일 기대값을 원본 측정값과 비교한다."""
    from .document_conditions import field_for_label
    field = field_for_label(condition['label'])
    expected = condition['requirement']
    checks = []
    size = re.fullmatch(r'가로\s*(\d+)\s*[x×]\s*세로\s*(\d+)\s*픽셀\s*[,，]?\s*(세로형|가로형)?', expected)
    spec = re.fullmatch(r'정적\s*(JPEG|JPG|PNG|WEBP)\s*이미지\s*1\s*개\s*[,，]\s*확장자\s*(\.[a-z]+)\s*[,，]\s*(RGB|RGBA|CMYK|L)\s*색상\s*모드', expected, re.I)
    if field == 'dimensions' and size:
        dimensions = source.get('oriented_dimensions')
        wanted = [int(size[1]), int(size[2])]
        checks.append(dict(name='dimensions', expected=wanted, observed=dimensions,
                           status='review' if not dimensions else 'pass' if dimensions == wanted else 'fail'))
        if size[3] and dimensions:
            direction = dimensions[1] > dimensions[0] if size[3] == '세로형' else dimensions[0] > dimensions[1]
            checks.append(dict(name='orientation', expected=size[3], observed=dimensions, status='pass' if direction else 'fail'))
    elif field == 'file' and spec:
        for name, wanted, actual in (
            ('format', spec[1].upper().replace('JPG', 'JPEG'), source.get('actual_format')),
            ('extension', spec[2].lower(), source.get('original_extension')),
            ('color_mode', spec[3].upper(), source.get('original_mode')),
            ('frames', 1, source.get('original_frames')),
        ):
            checks.append(dict(name=name, expected=wanted, observed=actual,
                               status='review' if actual is None else 'pass' if wanted == actual else 'fail'))
    elif field == 'dimensions' and re.fullmatch(r'(?:최소)?이미지폭|최소폭', _compact(condition['label'])):
        minimum = re.fullmatch(r'(\d+)\s*(?:px|픽셀)(?:\s*이상)?', expected, re.I)
        if not minimum:
            return False
        dimensions = source.get('oriented_dimensions')
        actual = dimensions[0] if dimensions else None
        wanted = int(minimum[1])
        checks.append(dict(name='minimum_width', expected=wanted, observed=actual,
                           status='review' if actual is None else 'pass' if actual >= wanted else 'fail'))
    elif field == 'dimensions' and re.fullmatch(r'이미지비율|화면비율|종횡비', _compact(condition['label'])):
        ratio = re.fullmatch(r'(\d+)\s*:\s*(\d+)', expected)
        if not ratio or not all(int(v) > 0 for v in ratio.groups()):
            return False
        dimensions = source.get('oriented_dimensions')
        tolerance = re.search(r'±\s*(\d+)\s*(?:px|픽셀)', condition['inspection'], re.I)
        pixels = int(tolerance[1]) if tolerance else 0
        a, b = map(int, ratio.groups())
        matches = bool(dimensions) and abs(dimensions[0]*b - dimensions[1]*a) <= max(a, b)*pixels
        checks.append(dict(name='aspect_ratio', expected=expected, observed=dimensions, tolerance_px=pixels,
                           status='review' if not dimensions else 'pass' if matches else 'fail'))
    elif field == 'file' and re.fullmatch(r'PNG|JPEG|JPG|WEBP', expected, re.I):
        wanted = expected.upper().replace('JPG', 'JPEG')
        actual = source.get('actual_format')
        checks.append(dict(name='format', expected=wanted, observed=actual,
                           status='review' if actual is None else 'pass' if wanted == actual else 'fail'))
    elif field == 'file' and _compact(condition['label']) == '색상모드' and re.fullmatch(r'RGB|RGBA|CMYK|L', expected):
        actual = source.get('original_mode')
        checks.append(dict(name='color_mode', expected=expected, observed=actual,
                           status='review' if actual is None else 'pass' if expected == actual else 'fail'))
    else:
        return False
    status = 'fail' if any(c['status'] == 'fail' for c in checks) else 'review' if any(c['status'] == 'review' for c in checks) else 'pass'
    result.update(status=status, check_method='source_image_metadata', evidence=[], metadata_checks=checks,
                  observed_text=' / '.join(f"{c['name']}: {c['observed']}" for c in checks),
                  reason='입력한 파일 조건을 업로드 원본의 측정값과 비교했습니다.'
                  + (' 원본에 기록되지 않은 값이 있어 확인이 필요합니다.' if status == 'review' else ''))
    inspection = condition['inspection']
    if field == 'file':
        # 원본 색상 모드는 위에서 측정한 파일 속성이며 포스터 색상 판정과 구분한다.
        inspection = re.sub(r'(?:원본\s*)?색상\s*모드', '', inspection)
    if status != 'fail' and _DOCUMENT_VISUAL_HINT.search(inspection):
        result.update(status='review', reason='파일 측정값은 비교했지만 판단 기준에 추가된 시각·배치 조건은 확인이 필요합니다.')
    return True


def _document_matches(condition, records, *, plain=False):
    """문서에서 허용한 문자 차이만 정규화하고 원래 OCR 근거를 반환한다."""
    inspection = condition['inspection']
    from .document_conditions import field_for_label
    cross = bool(re.search(r'[x×]\s*또는\s*[x×]', inspection, re.I)) or (plain and field_for_label(condition['label']) == 'participants')
    case = bool(re.search(r'대소문자.*허용', inspection))
    def transform(text):
        if cross:
            text = text.replace('×', 'x')
        return text.casefold() if case else text
    transformed = [{**r, 'text': transform(r['text'])} for r in records]
    groups = _match(transform(condition['requirement']), _ocr_runs(transformed))
    by_id = {r['id']: r for r in records}
    return [[by_id[r['id']] for r in group] for group in groups]


def _curved_location_matches(expected, records):
    """비스듬한 장소 문구도 겹치는 가까운 박스만 연결한다. 다른 줄은 합치지 않는다."""
    target, matches = _compact(expected), []
    def walk(group):
        text = _compact(''.join(r['text'] for r in group))
        if target in text:
            matches.append(group)
            return
        if len(group) >= 3 or not target.startswith(text):
            return
        last = group[-1]['bbox_xyxy']
        for record in records:
            box = record['bbox_xyxy']
            overlap = min(box[3], last[3]) - max(box[1], last[1])
            if ((box[0] + box[2]) > (last[0] + last[2])
                    and overlap >= .3 * min(box[3] - box[1], last[3] - last[1])
                    and box[0] - last[2] <= 3 * max(box[3] - box[1], last[3] - last[1])):
                walk([*group, record])
    for record in records:
        walk([record])
    return matches


def _lineup_date(condition, groups, records, *, plain=False):
    """일자별 출연진은 문구뿐 아니라 위쪽의 같은 열 날짜도 근거로 확인한다."""
    date = re.search(r'(\d{1,2})\s*월\s*(\d{1,2})\s*일', condition['inspection'])
    day = re.fullmatch(r'(\d{1,2})일(?:대표)?(?:출연진|라인업)', _compact(condition['label'])) if plain else None
    if date:
        wanted = tuple(map(int, date.groups()))
    elif day:
        # 입력에 없는 월을 채우지 않는다. 같은 일이 여러 월에 있으면 모호하므로 보류한다.
        candidates = {tuple(map(int, match)) for record in records
                      for match in re.findall(r'(?<!\d)(\d{1,2})[./](\d{1,2})(?!\d)', record['text'])
                      if int(match[1]) == int(day[1])}
        if len(candidates) != 1:
            return None
        wanted = next(iter(candidates))
    else:
        return None
    for group in groups:
        if not _strong(group):
            continue
        left = min(r['bbox_xyxy'][0] for r in group)
        right = max(r['bbox_xyxy'][2] for r in group)
        top = min(r['bbox_xyxy'][1] for r in group)
        headers = []
        for record in records:
            dates = re.findall(r'(?<!\d)(\d{1,2})[./](\d{1,2})(?!\d)', record['text'])
            box = record['bbox_xyxy']
            if (len(dates) == 1
                    and box[3] <= top and min(right, box[2]) - max(left, box[0]) >= .5 * (box[2] - box[0])):
                headers.append((top - box[3], record, tuple(map(int, dates[0]))))
        if headers:
            # 같은 열 위에 다른 날짜가 끼어 있으면 더 먼 날짜를 가져오지 않는다.
            _, record, observed = min(headers, key=lambda item: item[0])
            if observed == wanted and _strong([record]):
                return [*group, record]
    return None


def _title_blocks(expected, records):
    """실제로 읽힌 연도와 바로 아래 제목만 연결한다. 다른 열·먼 날짜를 끌어오지 않는다."""
    target = _compact(expected)
    prefix = re.fullmatch(r'((?:19|20)\d{2})(.+)', target)
    suffix = re.fullmatch(r'(.+?)((?:19|20)\d{2})', target)
    if prefix:
        year, name = prefix.groups()
    elif suffix:
        name, year = suffix.groups()
    else:
        return []
    runs = _ocr_runs(records)
    titles, years = _match(name, runs), _match(year, runs)
    matches = []
    for title in titles:
        box = [min(r['bbox_xyxy'][0] for r in title),min(r['bbox_xyxy'][1] for r in title),
               max(r['bbox_xyxy'][2] for r in title),max(r['bbox_xyxy'][3] for r in title)]
        for group in years:
            if any(r in title for r in group) or _compact(''.join(r['text'] for r in group)) != year:
                continue
            ybox = [min(r['bbox_xyxy'][0] for r in group),min(r['bbox_xyxy'][1] for r in group),
                    max(r['bbox_xyxy'][2] for r in group),max(r['bbox_xyxy'][3] for r in group)]
            overlap = min(box[2],ybox[2])-max(box[0],ybox[0])
            if (ybox[1]<box[1] and ybox[3]<=box[1]+.2*(box[3]-box[1])
                    and box[1]-ybox[3]<=2*max(box[3]-box[1],ybox[3]-ybox[1])
                    and overlap>=.5*min(box[2]-box[0],ybox[2]-ybox[0])):
                matches.append([*group,*title])
    return matches


def _document_compare(criterion, result, records, source):
    from .document_conditions import attached_condition, field_for_label, plain_condition
    condition = attached_condition(criterion)
    plain = condition is None
    if plain:
        condition = plain_condition(criterion)
    if condition is None:
        return False
    field = field_for_label(condition['label'])
    # 기존 날짜 정규화·OCR 출처 검증이 확정한 결과를 단순 문자열 비교로 덮지 않는다.
    if plain and (result['status'] == 'fail' or result['status'] == 'pass' and field != 'participants'):
        return False
    if _document_metadata(condition, result, source):
        return True
    if field == 'optional_delivery' and condition['level'] == '선택':
        result.update(status='review', observed_text='', evidence=[], check_method='optional_delivery',
                      reason='선택 제출 항목입니다. 미제출을 필수 불합격으로 판단하지 않으며, 별도 합의 여부를 확인해 주세요.')
        return True
    if criterion['type'] != 'information' or field not in {'title', 'date', 'time', 'location', 'participants', 'text'}:
        return False
    groups = _document_matches(condition, records, plain=plain)
    if not groups and field == 'location':
        groups = _curved_location_matches(condition['requirement'], records)
    if not groups and field == 'title':
        groups = _title_blocks(condition['requirement'], records)
    matched = _set_match(result, condition['requirement'], groups)
    if matched:
        result['check_method'] = 'plain_expected_text' if plain else 'document_expected_text'
        result['reason'] = ('입력한 기대 문구를 인접한 OCR 근거와 비교했습니다. 출연진의 x·× 표기는 같은 협연 구분자로 정규화합니다.'
                            if plain and field == 'participants' else '입력한 기대 문구를 인접한 OCR 근거와 비교했습니다.' if plain
                            else 'PDF의 기대 문구를 OCR 근거와 비교했습니다. 필수·권장 표시와 판단 설명은 검색 문구에서 분리했습니다.')
        if not any(_strong(group) for group in groups):
            result['reason'] += (' 영역 재인식으로 얻은 후보는 원본 대조 전 자동 확정하지 않았습니다.'
                                 if any(r.get('requires_review') for group in groups for r in group)
                                 else ' OCR 인식 근거가 충분하지 않아 확인이 필요합니다.')
        if field == 'participants' and not (plain and _compact(condition['label']) in {'출연진', '라인업', '대표출연진', '대표라인업'}):
            evidence = _lineup_date(condition, groups, records, plain=plain)
            if evidence:
                result.update(status='pass', evidence=_evidence(evidence),
                              observed_text=' / '.join(r['text'] for r in evidence),
                              check_method='plain_text_and_date_column' if plain else 'document_text_and_date_column',
                              reason='출연진 문구와 그 위 같은 열에 있는 날짜를 OCR 좌표·문구로 확인했습니다.')
            else:
                result.update(status='review', reason='출연진 문구는 읽었으나 지정한 날짜의 라인업 영역에 속하는지 확인할 근거가 부족합니다.')
        if _DOCUMENT_VISUAL_HINT.search(condition['inspection']):
            result.update(status='review', reason='문구는 OCR에서 확인했지만 판단 기준의 시각·배치 조건까지 확인되지는 않았습니다.')
        return True
    if field == 'date':
        fragments = [r for r in records if re.search(r'\d{1,2}[./]\d{1,2}', r['text'])]
        years = set(re.findall(r'(?<!\d)\d{4}(?!\d)', condition['requirement']))
        candidates = [r for r in records if r.get('requires_review') and _compact(r['text']) in years and r not in fragments]
        fragments += candidates
        result.update(status='review', check_method='plain_date_fragments' if plain else 'document_date_fragments', evidence=_evidence(fragments),
                      observed_text=' / '.join(r['text'] for r in fragments),
                      reason=('월·일과 연도 재인식 후보를 읽었지만 후보 검증과 전체 기간 대조를 위해 원본 확인이 필요합니다.' if candidates
                              else '월·일 날짜 조각은 읽었지만 요구한 연도·전체 기간을 독립적으로 확인하지 못했습니다. 입력한 연도를 이미지의 관찰값으로 대신하지 않았습니다.'))
    else:
        result.update(status='review', observed_text='', evidence=[], check_method='plain_expected_text' if plain else 'document_expected_text',
                      reason='입력한 기대 문구 전체를 OCR에서 확인하지 못했습니다. 인식 누락일 수 있어 이미지에서 없다고 판정하지 않았습니다.')
    return True


def compare(criteria, results, records, source):
    runs = _ocr_runs(records)
    for criterion, result in zip(criteria, results):
        if criterion.get('document_condition') is not None and _document_compare(criterion, result, records, source):
            continue
        if _metadata(criterion, result, source):
            continue
        analysis = result.get('model_analysis', {})
        conditions = analysis.get('conditions', []) if analysis.get('status') == 'success' else []
        if len(conditions) == 1 and not analysis.get('needs_review'):
            item = conditions[0]
            if _price(criterion, result, item, records):
                continue
            if _date_hint(result, item, runs):
                continue
            if item.get('type') == 'information' and item.get('operator') in ('equals', 'contains'):
                value = item.get('value')
                if isinstance(value, str) and value in criterion['text']:
                    # A recognized instruction is not part of the displayed product name.
                    value = re.sub(r'\s*\(\s*(?:한글|영문)\s*표기\s*(?:필수|권장)\s*\)\s*$', '', value)
                    if _set_match(result, value, _match(value, runs)):
                        continue
        if 'document_condition' not in criterion and _document_compare(criterion, result, records, source):
            continue
        # Explicit one-of wording can be evaluated without inventing an OR interpretation.
        one_of = re.fullmatch(r'\s*[^:：]+[:：]\s*(.+?)\s*(?:또는|혹은)\s*(.+?)\s*중\s*하나\s*(?:필수\s*)?(?:표기|기재|포함)\s*', criterion['text'])
        if one_of:
            for alternative in one_of.groups():
                alternative = alternative.strip()
                if len(alternative) >= 2 and _set_match(result, alternative, _match(alternative, runs)):
                    break
    return results
