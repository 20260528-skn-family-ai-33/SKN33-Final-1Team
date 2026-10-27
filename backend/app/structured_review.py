"""Compare a restricted set of grounded requirements with OCR and source metadata.

This does not identify image objects, invent text, or equate model confidence
with correctness. Unsupported, ambiguous and ungrounded cases remain review.
"""
from __future__ import annotations

import re
import unicodedata

from .review_rules import _evidence, _strong


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


def compare(criteria, results, records, source):
    runs = _ocr_runs(records)
    for criterion, result in zip(criteria, results):
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
        # Explicit one-of wording can be evaluated without inventing an OR interpretation.
        one_of = re.fullmatch(r'\s*[^:：]+[:：]\s*(.+?)\s*(?:또는|혹은)\s*(.+?)\s*중\s*하나\s*(?:필수\s*)?(?:표기|기재|포함)\s*', criterion['text'])
        if one_of:
            for alternative in one_of.groups():
                alternative = alternative.strip()
                if len(alternative) >= 2 and _set_match(result, alternative, _match(alternative, runs)):
                    break
    return results
