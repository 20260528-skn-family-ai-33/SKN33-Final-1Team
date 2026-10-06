"""Produce the reviewed synthetic request draft; no model call or training.

Run from any directory with Python 3.12 or Python 3. This script writes only the
request corpus and its production/review evidence under data/collection.
The authored situations and paraphrase templates below are the complete source.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'data' / 'collection'
AUTHOR_INSTRUCTION = (
    '공유 case-specs의 가상 브랜드·상품·할인 조건을 사용한다. 신규 검수, '
    '재검수, 기준 변경, 상태 조회, 기타를 각 10개의 서로 다른 업무 상황과 '
    '상황별 5개의 표현 변형으로 작성한다. 원자료에 정답을 넣지 않는다. '
    '사람 검토 대기인 초도 합성 개발 자료이며 독립 성능 증거로 쓰지 않는다.'
)

# Each item is a distinct semantic situation, not a substituted date or name.
# context, requested action, intent-specific review rationale
SITUATIONS = {
    'new_inspection': [
        ('첫 납품 파일', '{b}의 {p} 광고 첫 납품 이미지를 받았습니다.', '확정된 {d}% 할인 조건과 상품명·브랜드 표기부터 전체 기준을 검사해 주세요.', '첫 납품 접수 후 처음 검수를 요청함'),
        ('두 디자인 후보', '{p} 광고의 A안과 B안을 처음 제출합니다.', '두 후보를 각각 신규 검수하고 어느 항목에 차이가 있는지 알려 주세요.', '처음 제출한 후보 두 개의 검사 요청'),
        ('모바일 배너 첫 제출', '{b}의 {p} 모바일 배너가 완성되어 처음 올립니다.', '작은 화면에서도 문구가 읽히는지와 확정 기준의 충족 여부를 신규로 확인해 주세요.', '신규 채널 납품 이미지 검사 요청'),
        ('다국어 병기 첫 제출', '{p} 광고에 영문 상품 설명을 함께 넣은 첫 시안입니다.', '추가 설명과 관계없이 {b} 표기와 {d}% 할인 문구 등 확정 조건을 검사해 주세요.', '다국어 설명을 추가한 첫 납품 검사 요청'),
        ('인쇄용 변환본 첫 제출', '{b}의 {p} 광고를 인쇄용 이미지로 처음 변환했습니다.', '변환 과정에서 글자가 흐려졌는지 포함해 처음 검수를 진행해 주세요.', '첫 변환 납품의 판독과 조건 검사 요청'),
        ('증빙 누락 최초 접수', '{p} 광고 첫 이미지에는 제작 근거 파일이 아직 첨부되지 않았습니다.', '이미지를 신규 검수하고 증빙 누락은 이미지의 픽셀 오류와 구분해 기록해 주세요.', '처음 접수한 불완전 제출 패키지의 검사 요청'),
        ('주문 간 자료 혼입', '{b}의 {p} 광고를 처음 접수하는데 다른 발주의 파일이 섞였을 수 있습니다.', '{order}의 기준과 이미지가 맞는지부터 신규 검수를 시작해 주세요.', '첫 납품의 발주 대응 검사 요청'),
        ('가려진 할인 배지', '처음 제출하는 {p} 광고는 할인 배지 일부가 장식에 가려져 있습니다.', '검수를 새로 실행하고 {d}% 표기를 읽기 어려우면 확인 필요로 남겨 주세요.', '첫 납품의 모호한 판독 상태를 포함한 검사 요청'),
        ('여러 파일 일괄 첫 접수', '{b}의 {p} 캠페인 광고 파일 세 개를 처음 한꺼번에 제출합니다.', '각 파일을 별도 신규 검수하고 파일별 결과와 근거를 정리해 주세요.', '여러 첫 납품 파일을 구분한 검사 요청'),
        ('고해상도 원본 첫 제출', '{p} 광고의 고해상도 원본 이미지가 처음 도착했습니다.', '{b} 표기와 {d}% 할인 조건을 포함한 전체 기준으로 신규 검사해 주세요.', '고해상도 첫 납품의 신규 검사 요청'),
    ],
    'reinspection': [
        ('할인 문구 수정', '이전에 지적된 {p} 광고의 {wrong}% 할인 문구를 {d}%로 수정했습니다.', '수정본을 재검수하고 기존 지적 해결과 새 오류를 함께 확인해 주세요.', '기존 검사에서 할인 오류를 수정한 뒤 재검수'),
        ('브랜드 누락 보완', '지난 검수에서 빠졌던 {b} 브랜드명을 {p} 광고 상단에 다시 넣었습니다.', '이번 수정본을 재검수해 브랜드 지적이 해결됐는지와 전체 조건을 확인해 주세요.', '기존 브랜드 지적 보완 후 재검수'),
        ('상품명 오탈자 수정', '이전 검사에서 발견된 상품명 오탈자를 고쳐 {p}로 표시했습니다.', '수정 이미지로 재검수하고 다른 항목도 다시 확인해 주세요.', '상품명 지적 수정 후 재검수'),
        ('가려진 문구 위치 수정', '지난 검수에서 확인 필요였던 {p} 광고 문구를 장식과 겹치지 않게 옮겼습니다.', '다시 검사해 판독 가능 여부와 새 배치의 기준 충족을 확인해 주세요.', '모호한 판독을 보완한 수정본 재검수'),
        ('증빙 파일 추가', '전에 {b}의 {p} 광고를 검사할 때 없던 제작 근거 파일을 이번에 첨부했습니다.', '보완한 제출 패키지를 재검수하고 증빙 누락 상태가 해소됐는지 알려 주세요.', '이전 증빙 누락을 보완한 제출 패키지 재검수'),
        ('전체 디자인 교체', '이미 검수한 {p} 광고의 디자인을 새 시안으로 교체했습니다.', '기존 결과를 그대로 쓰지 말고 교체본을 전체 기준으로 재검수해 주세요.', '기존 검사 뒤 전체 디자인 교체 재검수'),
        ('압축 손실 보완', '지난 검사에서 글자가 흐려졌던 {b}의 {p} 이미지를 압축 없이 다시 저장했습니다.', '이 수정본으로 재검수하고 문구 판독과 모든 조건을 다시 확인해 주세요.', '기존 흐림 지적 보완 재검수'),
        ('일부 지적 미해결', '앞선 {p} 광고 검사에서 상품명만 고쳤고 할인 배지는 아직 손대지 않았습니다.', '재검수를 실행해 해결된 지적과 남은 지적을 구분해 주세요.', '부분 수정 뒤 남은 위반을 구분하는 재검수'),
        ('잘못 제출한 수정본 교체', '{b}의 {p} 광고 재검수에 실수로 옛 파일을 냈고 이제 올바른 수정본을 올립니다.', '새 수정본으로 다시 검수하고 이전 파일 결과와 구분해 주세요.', '구버전 오제출 이후 올바른 수정본 재검수'),
        ('확정 기준 변경 후 재검수', '이전에 검사한 {p} 광고에 적용할 발주 기준이 새 버전으로 확정되어 있습니다.', '이미지를 최신 확정 기준으로 재검수하고 이전 결과와 달라진 항목을 알려 주세요.', '기준 변경 자체가 아닌 이미 확정된 기준을 적용한 재검수'),
    ],
    'criteria_change': [
        ('할인율 변경', '{b}의 {p} 캠페인 할인율을 바꾸려 합니다.', '발주 기준의 할인율을 {d}%에서 {wrong}%로 변경하는 초안을 만들어 주세요.', '할인 조건 변경 초안 요청'),
        ('상품명 수식어 추가', '{p} 광고에서 상품명을 더 구체적으로 표기하려 합니다.', '상품명 조건을 아이스 {p}로 변경하는 초안을 준비해 주세요.', '상품명 기준값 변경 초안 요청'),
        ('브랜드 표시 위치 변경', '{b} 브랜드가 {p} 광고 하단에 오도록 기준을 바꾸려 합니다.', '브랜드 표시 위치 조건의 변경 초안을 작성해 주세요.', '브랜드 위치 기준 변경 요청'),
        ('필수 문구 표시 크기 추가', '{b}의 {p} 광고에 가상 자료임을 더 분명하게 밝히려 합니다.', '기존 가상 광고 · 개발용 고지의 글자 높이를 원본 이미지에서 24픽셀 이상으로 정하는 새 조건의 변경 초안을 만들어 주세요.', '기존 필수 고지에 없던 최소 표시 크기 조건 추가 요청'),
        ('제출 형식 변경', '{p} 광고 납품 파일의 허용 형식을 정비하려 합니다.', '제출 형식 조건을 PNG로 한정하는 변경안을 준비해 주세요.', '파일 형식 기준 변경 요청'),
        ('모호한 외관 조건 구체화', '{p} 광고의 제품 외관 설명이 모호해서 담당자마다 다르게 이해합니다.', '외관 기준을 관찰 가능한 표현으로 구체화하는 변경 초안을 작성해 주세요.', '외관 기준 명확화 요청'),
        ('증빙 이력 항목 구체화', '{b}의 {p} 광고 납품 때 받는 제작 이력을 더 자세히 관리하려 합니다.', '각 편집 단계의 변경 전후 파일 SHA-256을 제작 근거에 필수로 남기는 새 세부 조건의 변경안을 준비해 주세요.', '기존 제작 근거 조건에 단계별 변경 전후 해시 요건 추가 요청'),
        ('문서 간 기준 충돌 조정', '{p} 광고의 의뢰서 할인율과 브랜드 가이드의 할인율이 서로 다릅니다.', '두 원문을 대조한 뒤 충돌 조정을 위한 기준 변경 초안을 만들어 주세요.', '문서 충돌 해소를 위한 기준 변경 요청'),
        ('구식 문구 조건 제거', '{b}의 {p} 광고 기준에 지난 캠페인의 행사 문구가 남아 있습니다.', '그 오래된 행사 문구 조건을 제거하는 변경 초안을 작성해 주세요.', '기존 기준 항목 제거 요청'),
        ('브랜드 반복 표기 횟수 제한', '{b}의 {p} 광고에서 브랜드명이 여러 곳에 반복되는 것을 줄이려 합니다.', '기존 브랜드명 조건에 {b}를 한 이미지에서 정확히 한 번만 표시하도록 횟수 제한을 추가하는 변경안을 준비해 주세요.', '기존 브랜드 문자열 조건에 노출 횟수 제한 추가 요청'),
    ],
    'status_query': [
        ('신규 접수 진행 상태', '방금 {b}의 {p} 광고를 신규 검수로 접수했습니다.', '현재 접수 상태와 검사 시작 여부를 알려 주세요.', '새 검사 실행 없이 접수 진행 상태 조회'),
        ('검사 대기 순서', '{p} 광고 검수가 대기 중이라고 표시됩니다.', '대기 상태가 유지되는지와 확인 가능한 진행 정보를 조회해 주세요.', '대기 상태와 진행 정보 조회'),
        ('최근 실행 결과', '{b}의 {p} 광고에서 여러 번 검수를 실행했습니다.', '가장 최근 실행의 상태와 결과 요약을 보여 주세요.', '최신 기존 실행 결과 조회'),
        ('미해결 지적 목록', '이전에 검사한 {p} 광고에서 아직 보완할 내용이 있는지 궁금합니다.', '미해결 항목과 확인 필요 항목을 조회해 주세요.', '기존 검수의 미해결 항목 조회'),
        ('현재 확정 기준 버전', '{b}의 {p} 광고 기준을 여러 차례 수정했습니다.', '현재 확정된 기준 버전이 무엇인지 조회해 주세요.', '기준 변경 없이 현재 버전 조회'),
        ('제출 증빙 상태', '{p} 광고의 제작 근거 파일을 이미 제출한 것으로 알고 있습니다.', '접수된 증빙 목록과 누락 상태를 조회해 주세요.', '증빙 추가 없이 접수 현황 조회'),
        ('수정본 재검수 완료 여부', '{b}의 {p} 광고 수정본 재검수를 앞서 요청했습니다.', '그 재검수가 끝났는지와 남은 확인 사항을 알려 주세요.', '기존 재검수 진행 상태 조회'),
        ('실패 실행 원인 조회', '{p} 광고의 지난 검사 실행이 실패로 표시됐습니다.', '저장된 실패 사유와 실행 상태를 조회해 주세요.', '검사 재실행 없이 기존 실패 기록 조회'),
        ('기준 승인 대기 상태', '{b}의 {p} 광고 기준 변경 초안을 앞서 등록했습니다.', '사람의 확인을 기다리는 상태인지와 확정 여부를 알려 주세요.', '기준 승인 실행 없이 대기 상태 조회'),
        ('변경 이력 조회', '{p} 광고가 처음 접수된 뒤 어떤 버전으로 처리됐는지 확인하려 합니다.', '기준과 이미지 버전의 기존 처리 이력을 보여 주세요.', '과거 기록과 버전 이력 조회'),
    ],
    'other': [
        ('광고 카피 제안', '{b}의 {p} 신제품을 소개할 광고 카피가 필요합니다.', '검수 업무와 별도로 짧은 홍보 문구 세 개를 제안해 주세요.', '검수 실행이 아닌 창작 요청'),
        ('색상 조합 아이디어', '{p} 광고를 새로 디자인할 예정입니다.', '검수 기준 변경이 아니라 어울리는 배경색 조합 아이디어를 알려 주세요.', '확정 기준 수정이 아닌 디자인 상담'),
        ('가상 브랜드 이름 제안', '{b} 외에 연습용 음료 브랜드를 하나 더 만들고 싶습니다.', '검수 작업 없이 새로운 가상 브랜드 이름 후보를 제안해 주세요.', '이름 창작 요청'),
        ('검수 용어 설명', '{p} 광고 결과를 이해하기 전에 검수 용어부터 알고 싶습니다.', '특정 실행 조회가 아니라 확인 필요와 미실행의 의미를 설명해 주세요.', '특정 프로젝트 상태가 아닌 일반 개념 설명'),
        ('작업 도구 사용법', '{p} 광고 제작에 쓸 이미지 편집 도구를 익히려 합니다.', '검수 실행 없이 PNG로 저장하는 일반적인 방법을 알려 주세요.', '검수와 무관한 일반 도구 사용 설명'),
        ('팀 회의 안내 초안', '{b}의 {p} 광고 작업에 참여하는 팀원들과 회의를 잡을 예정입니다.', '메시지를 보내지 말고 회의 안내 문구 초안만 작성해 주세요.', '검수 업무 명령이 아닌 안내문 작성'),
        ('발표 제목 제안', '{p} 광고를 활용한 개발 시연을 발표하려 합니다.', '검수 결과 조회 없이 발표 제목 후보를 제안해 주세요.', '발표용 창작 요청'),
        ('업무 범위 문의', '{b}의 {p} 광고 프로젝트에 영상도 쓰고 싶습니다.', '지금 이미지를 검사하지 말고 이 도구가 영상 검수도 지원하는지 설명해 주세요.', '신규 검사 접수가 아닌 지원 범위 문의'),
        ('음료 소개 글', '가상의 {p} 상품을 소개하는 연습 글이 필요합니다.', '발주 조건과 검수 기준을 바꾸지 않고 상품 소개 문장을 써 주세요.', '상품 소개 글 작성 요청'),
        ('팀 용어 번역', '{b}의 {p} 광고 작업 안내를 영문으로 설명하려 합니다.', '검수 실행 없이 수정본 제출이라는 말을 영어로 어떻게 표현하는지 알려 주세요.', '검수 흐름 수행이 아닌 번역 요청'),
    ],
}

PARAPHRASES = {
    'new_inspection': [
        '{context} {action}',
        '신규 납품 검수를 부탁드립니다. {context} {action}',
        '{context} 아직 검수한 적 없는 제출물입니다. {action}',
        '첫 검사 요청을 남깁니다. {context} {action}',
        '{context} 이 제출물을 처음 검사하는 건으로 접수해 주시겠어요? {action}',
    ],
    'reinspection': [
        '{context} {action}',
        '보완본 확인을 요청합니다. {context} {action}',
        '{context} 최초 검수가 아니라 기존 제출 건의 후속 검사입니다. {action}',
        '기존 검수 건을 이어서 처리해 주세요. {context} {action}',
        '{context} 이전 검사 이력에 연결해서 살펴봐 주시겠어요? {action}',
    ],
    'criteria_change': [
        '{context} {action} 확정 전 사람이 검토할 수 있게 해 주세요.',
        '검수 실행에 앞서 조건 변경을 요청합니다. {context} {action}',
        '{context} 기존 발주 조건을 수정하려는 요청입니다. {action}',
        '기준 수정 작업으로 접수해 주세요. {context} {action}',
        '{context} 이미지를 검사하는 대신 적용할 기준 초안을 고쳐 주시겠어요? {action}',
    ],
    'status_query': [
        '{context} {action}',
        '새 작업을 실행하지 않고 현황만 확인하려 합니다. {context} {action}',
        '{context} 저장된 정보를 기준으로 알려 주시겠어요? {action}',
        '진행 상황 조회를 부탁드립니다. {context} {action}',
        '{context} 추가 검사를 시작하지는 마세요. {action}',
    ],
    'other': [
        '{context} {action}',
        '별도 문의가 있습니다. {context} {action}',
        '{context} 다음 일반 요청을 도와주시겠어요? {action}',
        '광고 작업을 위한 상담을 부탁드립니다. {context} {action}',
        '{context} 현재 검수 건과는 별개로 부탁드립니다. {action}',
    ],
}


def norm(text):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', text)).strip().casefold()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')


def main():
    specs_path = BASE / 'case-specs.json'
    specs = json.loads(specs_path.read_text(encoding='utf-8'))
    created_at = datetime.now(ZoneInfo('Asia/Seoul')).isoformat(timespec='seconds')
    cases = specs['cases']
    records, labels, scenarios, production, reviews = [], [], [], [], []
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    spec_hash = hashlib.sha256(specs_path.read_bytes()).hexdigest()
    for intent_idx, intent in enumerate(specs['request_intents']):
        assert len(SITUATIONS[intent]) == 10 and len(PARAPHRASES[intent]) == 5
        for situation_idx, (name, context, action, reason) in enumerate(SITUATIONS[intent]):
            group = f'REQ-G-{intent_idx * 10 + situation_idx + 1:03d}'
            case = cases[(intent_idx * 10 + situation_idx) % len(cases)]
            values = {'b': case['brand'], 'p': case['product'], 'd': case['discount_percent'],
                      'wrong': case['wrong_discount_percent'], 'order': case['project_id']}
            context, action = context.format(**values), action.format(**values)
            scenarios.append({'request_group_id': group, 'intent_label': intent, 'situation': name,
                              'context': context, 'requested_action': action, 'semantic_rationale': reason,
                              'case_group_id': case['group_id'], 'variant_count': 5})
            group_ids = []
            for variant, template in enumerate(PARAPHRASES[intent], 1):
                request_id = f'REQ-{len(records) + 1:04d}'
                sentence = template.format(context=context, action=action)
                group_ids.append(request_id)
                records.append({'request_id': request_id, 'text': sentence, 'request_group_id': group,
                                'case_group_id': case['group_id'], 'project_id': case['project_id'],
                                'synthetic': True, 'source_method': 'AI-authored situations and paraphrases; deterministic Python rendering',
                                'created_at': created_at})
                labels.append({'request_id': request_id, 'intent_label': intent, 'request_group_id': group,
                               'human_review_status': 'pending', 'label_status': 'draft',
                               'ai_review': {'type': 'author_self_review', 'status': 'draft_checked',
                                             'rationale': reason, 'ambiguity': 'none identified by author',
                                             'independent_review': False}})
                production.append({'request_id': request_id, 'request_group_id': group,
                                   'case_group_id': case['group_id'], 'variant': variant,
                                   'created_at': created_at, 'tool': 'Codex authoring + Python standard library',
                                   'model_exact': '미확인', 'source_script': 'scripts/generate_requests.py',
                                   'source_script_sha256': script_hash, 'case_specs_sha256': spec_hash,
                                   'prompt_trace': AUTHOR_INSTRUCTION,
                                   'prompt_trace_kind': 'authoring instruction summary; exact production source is the hashed script',
                                   'situation': name,
                                   'paraphrase_template': template, 'authoring_method': 'AI-authored static draft; script makes no model API call'})
            reviews.append({'request_group_id': group, 'request_ids': group_ids, 'intended_label': intent,
                            'semantic_review_note': reason + '. 5개 표현은 같은 상황과 같은 요청 행동을 유지함.',
                            'review_method': 'AI author checked authored situation and all five rendered formulations',
                            'review_status': 'author_self_review_draft', 'human_review_status': 'pending'})
    assert len(records) == 250
    assert len({norm(r['text']) for r in records}) == 250
    assert len({r['request_id'] for r in records}) == 250
    group_counts = Counter(r['request_group_id'] for r in records)
    intent_counts = Counter(r['intent_label'] for r in labels)
    assert len(group_counts) == 50 and set(group_counts.values()) == {5}
    assert set(intent_counts) == set(specs['request_intents']) and set(intent_counts.values()) == {50}
    assert all('intent_label' not in r for r in records)
    assert {r['request_id'] for r in records} == {r['request_id'] for r in labels}
    write_jsonl(BASE / 'raw/synthetic/requests/requests.jsonl', records)
    write_jsonl(BASE / 'evidence/request-labels.jsonl', labels)
    write_jsonl(BASE / 'evidence/request-production.jsonl', production)
    write_json(BASE / 'evidence/request-scenarios.json', {'schema_version': '1.0', 'scenarios': scenarios})
    review = {'schema_version': '1.0', 'created_at': created_at, 'scope': specs['purpose'],
              'review_type': 'AI author self-review and deterministic structure validation',
              'independent_semantic_review': False, 'human_review_status': 'pending',
              'label_status': 'draft', 'record_count': len(records), 'normalized_unique_text_count': 250,
              'request_group_count': len(group_counts), 'variants_per_group': 5,
              'intent_counts': dict(intent_counts), 'human_reviewed_count': 0,
              'team_real_material_status': 'not provided; not read; unconfirmed',
              'training_performed': False, 'dataset_split_performed': False,
              'checks': {'unique_ids': True, 'normalized_text_unique': True, 'label_join_complete': True,
                         'raw_records_have_no_intent_label': True, 'case_specs_values_used': True,
                         'fifty_distinct_semantic_situations': True}, 'groups': reviews,
              'limitations': ['합성 요청이며 사람 검토는 대기 상태임.', '합성 상황과 유사 표현은 독립 일반화 성능 증거가 아님.',
                              '자동 구조 검사 통과와 사람 의미 검토 완료는 별개임.',
                              '의도별 공통 표현 틀이 반복되어 분류 단서가 강함. 자연 실무 요청 분포를 대표하지 않음.',
                              '원문 그룹을 학습·검증·시험에 나눠 넣지 않아야 함. 현재 분할과 학습은 하지 않음.']}
    write_json(BASE / 'evidence/request-review.json', review)
    print(json.dumps({'requests': len(records), 'groups': len(group_counts), 'intents': dict(intent_counts),
                      'normalized_unique_texts': 250, 'human_review_status': 'pending'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
