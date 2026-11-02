"""OCR 문단을 표/카드 조건, 공통 규칙, 확인 메모와 출처 설명으로 구분한다."""
from __future__ import annotations

import re


ID = r'[A-Za-z]{1,8}[-_]?\d{1,5}(?:[-_][A-Za-z0-9]{1,12})*'
ROW = re.compile(rf'^({ID})(?=\s|\|)')
LEVEL = re.compile(r'^\s*(?:\|\s*)?(필수|권장|선택)(?=\s|\|)')


def compact(text):
    return re.sub(r'[\s|]', '', text)


def paragraph_role(text):
    value = compact(text)
    if re.match(r'^(?:문구(?:비교원칙|판정)|공통(?:비교|판정|표기)규칙)', value):
        return 'common_rule'
    if re.match(r'^확인필요[:：]', value):
        return 'note'
    if (re.search(r'계약\s*(?:금액|대금)|서명|문서\s*(?:ID|관리)|DOCUMENT CONTROL', text, re.I)
            or re.match(r'^(?:작성근거[:：]|공개게시물[:：]|이미지SHA|SHA-256|포스터관찰기반|가상테스트명세서)', value, re.I)
            or re.search(r'(?:테스트에사용|역작성).*(?:역작성|입증하지)', value)):
        return 'metadata'
    return 'other'


def _section(text):
    value = re.sub(r'^\d{1,2}[.\-]?', '', compact(text))
    if re.fullmatch(r'(?:필수|권장|선택)?(?:문구및행사정보|시각요소|납품파일기준|판정과확인필요사항|출처와자료구분)', value):
        level = next((word for word in ('필수', '권장', '선택') if value.startswith(word)), None)
        kind = 'visual' if '시각요소' in value else 'information'
        role = 'metadata' if '출처와자료' in value else 'procedure' if '판정과확인' in value else 'other'
        return {'level': level, 'condition_type': kind, 'section_role': role}
    # 다른 양식의 절 제목은 이전 절의 필수 수준을 이어받지 않는다.
    if re.fullmatch(r'\d{1,2}\s*\|\s*[^|]{2,40}', text.strip()):
        return {'section_role': 'other'}
    return None


def _card_body(text):
    return bool(re.search(r'다음\s*(?:문구|시각\s*요소)|납품.*(?:형식|크기)|포함해야|있어야|여야', text))


def build_sources(pages):
    """원문은 교정하지 않고, 같은 카드의 헤더와 본문만 함께 인용한다."""
    sources = []
    for page in pages:
        blocks = [text for text in page['text'].split('\n\n') if text.strip()]
        section = {}
        index = 1
        position = 0
        while position < len(blocks):
            text = blocks[position]
            position += 1
            heading = _section(text)
            row = ROW.match(text)
            properties = {}
            if row:
                columns = [c.strip() for c in text.split('|')]
                level = LEVEL.match(text[row.end():])
                # ID + 수준 또는 실제 표 열/카드 헤더가 있어야 조건 ID로 취급한다.
                if level or len(columns) >= 2:
                    properties.update(requirement_id=row[1], role='requirement')
                    if level:
                        properties['level'] = level[1]
                    label_index = 2 if len(columns) > 2 and columns[1] in {'필수','권장','선택'} else 1
                    if len(columns) > label_index:
                        properties['label'] = columns[label_index]
                    if not level and len(columns) == 2:
                        properties['card'] = True
                        if position < len(blocks) and not ROW.match(blocks[position]) and _card_body(blocks[position]):
                            body = blocks[position]
                            position += 1
                            text += '\n' + body
                            properties['condition_type'] = ('visual' if '시각 요소' in body else
                                                             'information' if '문구' in body or '납품' in body else
                                                             section.get('condition_type'))
                            if section.get('level'):
                                properties['level'] = section['level']
                                properties['level_source'] = section['source']
                        else:
                            properties['header_only'] = True
            if not properties:
                role = paragraph_role(text)
                if heading:
                    role = 'heading'
                elif role == 'other' and section.get('section_role') in {'procedure','metadata'}:
                    role = section['section_role']
                properties['role'] = role
            start = 0
            while start < len(text):
                end = min(start + 350, len(text))
                source = {'id':f"p{page['page']}-s{index}", 'page':page['page'],
                          'text':text[start:end], **properties}
                sources.append(source)
                index += 1
                if end == len(text):
                    break
                start = end - 60
            if heading:
                section = {**heading, 'source':{'source_id':source['id'], 'page':source['page'], 'source':source['text']}}
    return sources


def is_supplement(source, field_for_label):
    if source.get('requirement_id') or source.get('role', paragraph_role(source['text'])) != 'other':
        return False
    text = source['text']
    if re.search(r'(?:표기|표시|기재|포함|사용|활용|표현|확보|유지|배치|허용|권장|금지|준수)(?:한다|해야|하여|하되|할|하지|된다|합니다|해|\s*[:：])|(?:있어야|여야)\s*합니다|(?:최소|최대)\s*(?:폭|너비|높이)|\d+\s*:\s*\d+.*(?:px|픽셀)|(?:PNG|JPEG|RGB)\s*[·,|]|(?:배치|색감|가독성).*검토한다', text, re.I):
        return True
    columns = text.split('|')
    return len(columns) >= 2 and field_for_label(columns[0].strip()) is not None
