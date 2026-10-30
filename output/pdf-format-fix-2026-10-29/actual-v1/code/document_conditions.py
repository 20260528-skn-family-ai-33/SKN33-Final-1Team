"""PDF 조건의 기대값과 판단 설명을 분리하고, 명확한 항목만 검사 경로에 연결한다."""
from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_serializer


class DocumentSource(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)
    source_id: str = Field(pattern=r'^p\d+-s\d+$')
    page: int = Field(ge=1, le=12)
    source: str = Field(min_length=1, max_length=350)


class DocumentCondition(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)
    label: str = Field(min_length=1, max_length=80)
    requirement: str = Field(min_length=1, max_length=400)
    level: Literal['필수', '권장', '선택', '명시 없음']
    # 여러 페이지의 보완 설명을 합쳐도 확정 스냅샷에서 자르지 않는다.
    inspection: str = Field(max_length=1200)
    sources: list[DocumentSource] = Field(default_factory=list, max_length=400)

    @model_serializer(mode='wrap')
    def omit_empty_sources(self, handler):
        value = handler(self)
        if not self.sources:
            value.pop('sources', None)
        return value


def single_line(text):
    return ' '.join(text.split())


def render_condition(condition):
    """검수 입력은 기대값 한 줄만 표시한다. 설명과 수준은 구조에 보존한다."""
    label, value = single_line(condition['label']), single_line(condition['requirement'])
    field = field_for_label(label)
    qualified = re.search(r'전시|기념식|박람회', label) or single_line(label) == '행사 제목'
    label = label if qualified else {'title': '행사명', 'date': '기간', 'time': '시간', 'location': '장소'}.get(field, label)
    if field is None and re.search(r'(?:[아어]야\s*합니다|해야\s*합니다|[아어]야\s*한다|해야\s*한다|유지한다|사용한다|포함한다|사용하지\s*않는다)[.!?]?$', value):
        return value
    return f'{label}: {value}'


def _legacy_render_condition(condition):
    """이전 확정 기록을 읽을 때만 설명을 포함한 옛 표시 형식을 인정한다."""
    label, value = single_line(condition['label']), single_line(condition['requirement'])
    if condition['level'] != '명시 없음':
        value = f"[{condition['level']}] {value}"
    inspection = single_line(condition['inspection'])
    return f"{label}: {value}" + (f' / 판단 기준: {inspection}' if inspection else '')


def matches_condition_text(condition, text):
    return single_line(text) in {render_condition(condition), _legacy_render_condition(condition)}


def field_for_label(label):
    label = ''.join(c for c in unicodedata.normalize('NFKC', label) if c.isalnum())
    patterns = (
        ('dimensions', r'이미지(?:크기|규격|해상도)|원본(?:가로세로|크기)|(?:최소)?이미지폭|최소폭|이미지비율|화면비율|종횡비'),
        ('file', r'(?:파일(?:형식|포맷)(?:과색상)?|파일규격|파일사양|색상모드)'),
        ('optional_delivery', r'(?:편집가능한원본|편집원본|원본파일)'),
        ('date', r'(?:행사|축제|판매|전시)?(?:일정|날짜|기간|시작일|종료일)(?:(?:와|및)요일|표기)?'),
        ('time', r'(?:행사|시작|운영|기념식|박람회)?시간'),
        ('location', r'(?:행사|축제|전시)?장소|판매처'),
        ('participants', r'(?:\d+일)?(?:대표)?(?:출연진|라인업)'),
        ('title', r'(?:행사명|행사제목|전시명|축제명|이벤트명)(?:(?:과|및)연도)?'),
        ('text', r'(?:영문행사명|영문축제명|영문명|상품명|제품명|브랜드명?|필수문구|필수고지|고지문구|캠페인(?:문구)?|(?:판매)?가격|참여대상|참가비|전시구분|입장료|연락처)'),
    )
    for field, pattern in patterns:
        if re.fullmatch(pattern, label):
            return field
    return None


def condition_type(label, fallback):
    # 판단 설명에 '이미지', '가독성', '배치'가 있어도 날짜·파일 값까지 시각으로 바꾸지 않는다.
    if field_for_label(label):
        return 'information'
    if re.search(r'로고|이미지|그림|배경|색상|가독성|배치|분위기|선화|레코드|톤암|캐릭터', label):
        return 'visual'
    return fallback


def attached_condition(criterion):
    """화면에서 편집된 문구에 이전 PDF 기대값을 적용하지 않는다."""
    try:
        value = DocumentCondition.model_validate(criterion.get('document_condition')).model_dump()
    except (ValidationError, TypeError):
        return None
    return value if matches_condition_text(value, criterion['text']) else None


def plain_condition(criterion):
    """구조 정보가 없어도 명확한 '항목: 기대값' 입력은 같은 비교기로 전달한다.

    PDF 출처 문자열에서 예외·필수 수준을 추측해서 복원하지 않는다.
    설명이나 대안 연산이 붙은 문장은 기존 해석 경로에 남긴다.
    """
    if criterion.get('type') != 'information' or 'document_condition' in criterion:
        return None
    match = re.match(r'^([^:：]+)[:：]\s*(.+)$', single_line(criterion['text']))
    if not match:
        return None
    label, value = match.groups()
    if not field_for_label(label):
        return None
    if re.search(r'판단\s*기준|변경\s*불가|\[(?:필수|권장|선택)\]|또는|혹은|중\s*하나', value):
        return None
    try:
        return DocumentCondition(label=label, requirement=value, level='명시 없음', inspection='').model_dump()
    except ValidationError:
        return None


def from_exported_text(text):
    """이전 내보내기 기록의 재현 진단용. 운영 요청에 암묵적으로 적용하지 않는다."""
    label, sep, value = text.partition(':')
    if not sep:
        return None
    value, _, inspection = value.partition(' / 판단 기준: ')
    level = re.match(r'^\s*\[(필수|권장|선택)\]\s*', value)
    condition = dict(label=label.strip(), requirement=value[level.end():].strip() if level else value.strip(),
                     level=level[1] if level else '명시 없음', inspection=inspection.strip())
    try:
        return DocumentCondition.model_validate(condition).model_dump()
    except ValidationError:
        return None
