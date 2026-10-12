#!/usr/bin/env python3
"""Refresh the four split HTML documents from current contracts and evidence.

Keep original feature/requirement IDs, layout, search, diagrams and UI drafts.
Run from the repository root with python scripts/refresh_planning_documents.py.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import build_contract_standard
import sync_contract_docs as contracts

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / 'docs/planning'
OUT = PLAN / 'organized'
SNAPSHOT = PLAN / 'implementation-snapshot-2026-10-06.json'
DATA = json.loads(SNAPSHOT.read_text())
E = contracts.E
table = contracts.table
section = contracts.replace_section


def block(text, key, body):
    start, end = f'<!-- REFRESH-{key}:START -->', f'<!-- REFRESH-{key}:END -->'
    text = re.sub(re.escape(start) + r'.*?' + re.escape(end), '', text, flags=re.S)
    return text + start + body + end


def intro_block(content, key, body):
    start, end = f'<!-- REFRESH-{key}:START -->', f'<!-- REFRESH-{key}:END -->'
    content = re.sub(re.escape(start) + r'.*?' + re.escape(end), '', content, flags=re.S)
    match = re.search(r'</h2>', content)
    if not match:
        raise ValueError('missing section title')
    return content[:match.end()] + start + body + end + content[match.end():]


def replace_detail(text, identifier, transform):
    pattern = r'(<details\b[^>]*\bid="' + re.escape(identifier) + r'"[^>]*>)(.*?)(</details>)'
    text, count = re.subn(pattern, lambda m: m[1] + transform(m[2]) + m[3], text, count=1, flags=re.S)
    if count != 1:
        raise ValueError(f'missing detail {identifier}')
    return text


def replace_cell(content, label, value):
    pattern = r'(<tr><td>' + re.escape(label) + r'</td><td>).*?(</td>)'
    content, count = re.subn(pattern, lambda m: m[1] + E(value) + m[2], content, count=1, flags=re.S)
    if count != 1:
        raise ValueError(f'missing table label {label}')
    return content


def replace_table_after_heading(content, heading, headers, rows):
    pattern = r'(<h3>' + re.escape(heading) + r'</h3>).*?</table></div>'
    content, count = re.subn(pattern, lambda m: m[1] + table(headers, rows), content, count=1, flags=re.S)
    if count != 1:
        raise ValueError(f'missing table {heading}')
    return content


def note(text):
    return '<p class="note">' + E(text) + '</p>'


def topic_table():
    return table(['주제', '샘플', '실행하는 문구 검사', '시각 검사 상태'], [
        [t['name'], f"{t['count']}장", t['text_checks'], t['unimplemented'] + ' 미구현'] for t in DATA['topics']
    ]) + note(DATA['sample_caution'])


def current_summary():
    return table(['구분', '2026.10.06 적용 내용'], [
        ['현재 코드', DATA['current_stack']],
        ['실제 실행 경로', DATA['current_flow']],
        ['기준의 출처', DATA['criteria_caution']],
        ['개발 예정', DATA['planned']],
        ['직전 검증 기록', DATA['test_record']['description'] + '. ' + DATA['test_record']['latest_example']],
    ])


def change_summary():
    return table(['수정 사항', '네 문서의 공통 적용 기준'], [
        ['상태', 'verdict / execution_status / run.status / task.status / approval.status를 별도로 기록한다. 실행 완료는 충족이나 승인과 다르다.'],
        ['항목 ID', '공개 키 rule_id. 기존 rules[].id는 읽기 호환, 같은 입력의 두 값 충돌·버전 내 중복 ID는 거절한다. 원본 바이트·기준 해시·기존 확정 기록은 보존한다.'],
        ['반복 한도', '도구 재시도 추가 2회(총 3번), 에이전트 재검사 추가 1회(총 2번)는 초기 설계값. 현재 자동 반복은 미구현·추가 반복 0회. Q03은 별도의 파일·시간·비용 운영 한도.'],
        ['표시 원칙', DATA['unresolved']],
    ])


def refresh_common(text):
    text = text.replace('종류별 문서 · 2026.10.05', '개정본 · 2026.10.06')
    text = text.replace('최신 기준 · 8개 에이전트', '공통 계약 v1 · 8개 역할 설계')
    old = '구현 상태는 기존 기록 기준이며 이번 문서 분리에서 앱·모델을 재실행하지 않았습니다.'
    text = text.replace(old, '2026.10.06 채팅 결정·공통 계약·현재 코드와 직전 실행 기록을 반영했습니다. 이번 문서 갱신에서는 앱·모델 실험을 새로 수행하지 않았습니다.')
    text = text.replace('>기존 구현 기록</a>', '>구현·검증 기록</a>')
    revision = ('<div class="note" style="max-width:1348px;margin:16px auto 24px"><strong>2026.10.06 개정본</strong><p>상태 5영역·rule_id·반복 한도를 통일하고, '
                'Django/FastAPI·공통 OCR·3주제 18장 구현 기록을 반영했습니다. 8개 에이전트·LLM·RAG·업무 후속 처리는 설계 상태로 구분합니다.</p>'
                '<p><a href="../implementation-snapshot-2026-10-06.json">구현 기준 원본</a> · '
                '<a href="../contract-standard-v1.json">공통 계약 원본</a></p></div>')
    # Put revision information in the existing banner; no extra tabs or screens.
    pattern = r'<!-- REFRESH-REVISION:START -->.*?<!-- REFRESH-REVISION:END -->'
    text = re.sub(pattern, '', text, flags=re.S)
    position = text.index('<!-- CONTRACT-LINK:END -->') + len('<!-- CONTRACT-LINK:END -->')
    text = text[:position] + '<!-- REFRESH-REVISION:START -->' + revision + '<!-- REFRESH-REVISION:END -->' + text[position:]
    return text


def refresh_plan(text):
    def scope(content):
        content = intro_block(content, 'CURRENT', current_summary())
        label = '현재 구현 기록' if '<td>현재 구현 기록</td>' in content else '기존 구현 기록'
        content = replace_cell(content, label, DATA['current_stack'] + '. ' + DATA['current_flow'])
        content = content.replace('<td>기존 구현 기록</td>', '<td>현재 구현 기록</td>')
        content = content.replace('README의 이전 실행 기록 기준입니다. 이번 문서 분리에서 앱을 다시 실행하지 않았습니다.', '현재 코드와 README의 직전 실행 기록 기준. CPU/MPS는 실기 확인, Windows/CUDA/Intel Mac은 미검증.')
        content = content.replace('동일 원본 그룹에서 파생한 딸기라떼 이미지 6종', '딸기라떼·페스티벌·크루아상 v2, 총 18장(주제당 6장)')
        return block(content, 'TOPICS', '<h3>현재 시연 데이터</h3>' + topic_table() + '<h3>표준 계약의 기획 반영</h3>' + change_summary())
    text = section(text, 'scope', scope)
    text = section(text, 'decisions', lambda c: c.replace('기존 Mac Vision 시제품은 별도로 기록합니다.', '현재 OCR은 RapidOCR의 CPU/CUDA/MPS 선택 기반을 사용합니다. LLM 팀 운영과 OCR 로컬 장치 선택을 구분합니다.').replace('이번 문서 분리만으로 기능 구현 상태를 변경하지 않습니다.', '현재 코드를 반영하되 LLM·RAG·8개 역할 협업과 후속 업무 완료로 확대 집계하지 않습니다.').replace('기존 자료는 수정하지 않습니다.', '원 요구사항과 이전 역할·선택 기록을 유지하고 최신 적용 내용은 이 개정본에 별도로 표시합니다.'))
    return text


def refresh_functions(text):
    text = section(text, 'overview', lambda c: intro_block(c, 'CURRENT', current_summary()).replace('구현 상태와 샘플 실행 언급은 원본 기능정의서 및 README의 기존 기록을 옮긴 것입니다. 이번 문서 분리에서 앱·모델·테스트를 실행하거나 완료 상태를 새로 판정하지 않았습니다.', '기능별 구현 범위는 2026.10.06 코드와 직전 검증 기록으로 갱신했습니다. 자동 테스트 125개와 원 요구사항 69개 전체 수용 시험은 별개입니다.').replace('기존 기록의 상태 집계', '설계 대비 구현 수준 · 2026.10.06'))
    def features(content):
        content = content.replace('‘기존 기록’ 표시는 현재 재검증을 뜻하지 않습니다.', '구현 수준은 현재 코드·직전 검증 기록 기준입니다. 부분 구현은 아래 전체 수용 조건의 완료를 뜻하지 않습니다.')
        content = content.replace(' · 기존 기록', ' · 10.06 기준').replace('기존 기록의 구현 상태', '구현 상태').replace('기존 기록의 확인 범위', '현재 구현 범위')
        for identifier, value in DATA['feature_updates'].items():
            def update(body, value=value):
                body, count = re.subn(r'(<dt>현재 구현 범위</dt><dd>).*?(</dd>)', lambda m: m[1] + E(value) + m[2], body, count=1, flags=re.S)
                if count != 1:
                    raise ValueError(f'missing feature implementation {identifier}')
                return body
            content = replace_detail(content, identifier, update)
        return content
    text = section(text, 'features', features)
    text = replace_detail(text, 'F-09', lambda body: re.sub(
        r'(<dt>예외·제한</dt><dd>).*?(</dd>)',
        lambda m: m[1] + E('근거 부족은 충족·해당 없음으로 바꾸지 않는다. 판정은 충족·미충족·확인 필요·null(미판정)로 구분한다. 해당 없음은 후속 지원 설계이며 현재 미구현이다. 도구 실행 실패는 execution_status=failed와 verdict=null로 기록하고, 정상 실행 후 판독이 불확실하면 확인 필요로 남긴다. 미검사는 not_run으로 별도 표시한다.') + m[2],
        body, count=1, flags=re.S))
    text = section(text, 'decisions', lambda c: block(c, 'CHANGES', '<h3>이번 개정의 적용 범위</h3>' + change_summary() + '<p>원 요구사항의 ID·원문·시험 기준을 보존합니다. 현재 구현 상태는 기능 카드의 설명을 우선하며 이전 원본의 과거 Mac OCR·6장·테스트 10개 기록과 혼용하지 않습니다.</p>'))
    return text


def refresh_screens(text):
    text = section(text, 'overview', lambda c: c.replace('샘플 기준 확정 → PNG/JPEG 접수 → Mac Vision 문자 검사 → 기본 근거·결과·실행 이력 조회. 딸기 개수·음료 외관은 미실행으로 표시합니다. README 기록을 기준으로 옮겼으며 이번 문서 작업에서 앱을 재실행하지 않았습니다.', DATA['current_flow'] + '. 기본 OCR은 RapidOCR이며 객체 수량·외관은 확인 필요/미실행입니다. 3주제 18장을 선택하고 크루아상은 발주 조건을 별도로 읽습니다.'))
    updates = {
        'SC-01': '부분 기반: 3주제·18장 선택, 크루아상 발주 선택, PNG/JPEG 접수·검증과 조건 연결 구현. 발주 목록·문서/증빙 접수·사용 계획·SVG 검토는 설계 대상.',
        'SC-02': DATA['feature_updates']['F-03'] + ' rule_id·topic_id·condition_id·criteria_version으로 항목과 확정 범위를 구분한다.',
        'SC-03': DATA['feature_updates']['F-06'] + ' ' + DATA['feature_updates']['F-09'],
        'SC-06': DATA['feature_updates']['F-08'] + ' 실제 MCP/실행 이력은 구현, 자연어 분류·8개 역할 협업·비용/취소는 설계 대상.',
        'SC-09': '공용 GPU·LLM 모델 서버·다중 사용자 운영 화면은 설계 대상. 현재 OCR의 장치 선택은 환경변수로 설정하며 실제 선택·사용 장치는 실행 근거에 기록한다. Windows/CUDA/Intel Mac 실기는 미검증.',
    }
    for identifier, value in updates.items():
        text = replace_detail(text, identifier, lambda c, value=value: replace_cell(c, '현재 구현과 차이', value))
    text = text.replace('개발용 딸기라떼 파생 6장과 독립 평가 건수를 구분한다.', '개발용 3주제 18장과 독립 평가 건수를 구분한다.')
    def states(content):
        return block(content, 'DISPLAY', '<h3>현재 화면에 적용할 표시 규칙</h3>' + table(['위치', '화면 표시·동작'], [
            ['기준 카드', '주제·선택 발주·확정 기준 버전·rule_id를 연결한다. 현재 JSON 입력과 향후 문서 추출 초안을 구분하고 원문 출처는 실제 값이 있을 때 표시한다.'],
            ['결과 카드', '판정과 항목 실행 상태를 별도 배지로 표시한다. 명확한 불일치는 미충족/실행 완료, 도구 실패는 미판정/실행 실패, 미지원 개수 검사는 확인 필요/미실행이다.'],
            ['이력·진행', 'run.status는 전체 실행 집계. 과거 항목 completed는 실행 완료로 읽고 원본을 유지한다. waiting_human·취소·재개 버튼은 실제 기능 연결 전 설계 예시로 표시한다.'],
            ['근거', 'OCR 원문·좌표·검사 방법·실제 CPU/CUDA/MPS 장치를 확인한다. GPU 실패 뒤 CPU 전환을 GPU 성공으로 표시하지 않는다.'],
            ['보완·승인', 'task.status와 approval.status를 각각 표시한다. 수정본 제출·작업 해결·검수 완료 중 어느 하나도 최종 승인을 대신하지 않는다. 현재 업무 화면은 미구현이다.'],
        ]))
    text = section(text, 'states', states)
    text = section(text, 'sources', lambda c: c.replace('화면·모델 실행은 이번에 재검증하지 않았습니다.', '현재 구현 범위는 코드·직전 실행 기록을 반영했습니다. 이번 갱신에서는 화면·모델 실험을 새로 수행하지 않았습니다.'))
    return text


def refresh_technical(text):
    def architecture(content):
        content = content.replace('FastAPI + HTML·CSS·JavaScript', 'Django + FastAPI 단일 ASGI + HTML·CSS·JavaScript')
        content = replace_table_after_heading(content, '현재 확인된 구현 기록', ['현재 코드·직전 실행 기록', '추가 구현·검증 대상'], [
            [DATA['current_stack'], 'MySQL 전환·Qdrant·다중 사용자 권한·공용 배포'],
            ['세 주제의 JSON 기준 조회·사람 확정, 크루아상 발주별 조건, 원본 해시 보존', '실제 명세서·계약서 전체에서 기준·출처 추출·사람 보정'],
            [DATA['ocr']['default'] + ' / ' + DATA['ocr']['cpu'] + ' / ' + DATA['ocr']['gpu'], 'Windows·CUDA·Intel Mac 실기, 독립 OCR 평가·PaddleOCR 비교·시각 검사'],
            ['실제 stdio MCP inspect_image, rule_id 정규화, 판정·실행 상태·근거·이력', 'LLM·분류·RAG·LangGraph 8역할·보완·재검수·최종 승인'],
        ])
        content = content.replace('README의 기존 검증 기록은 API·검사 로직 테스트 10개와 개발 샘플 6장 실행입니다. 브랜드 미검출은 확인 필요로 보존했고 개수·외관은 미실행입니다. 이번 문서 분리에서는 앱·모델을 재실행하거나 성능을 재측정하지 않았습니다.', DATA['test_record']['description'] + '. Mac CPU/MPS 실제 실행 기록을 반영하며 이번 문서 갱신에서 성능을 재측정하지 않았습니다.')
        return content
    text = section(text, 'architecture', architecture)
    def technology(content):
        content = content.replace('<th scope="col">원문 상태</th>', '<th scope="col">현재 상태 / 선정 수준</th>')
        rows = {
            '개발·학습': ['Python · PyTorch · HF Transformers', '모델 로딩·학습·추론. 현재 OCR은 PyTorch/ONNX 실행 기반 사용', '공통 설계', 'Python/PyTorch OCR 사용 · HF 학습 예정'],
            'OCR': ['RapidOCR 3.9.0 · ONNX Runtime 1.23.2 · PyTorch 2.10.0', '이미지 문자·좌표 추출, CPU/CUDA/MPS 선택·CPU 전환. PaddleOCR는 독립 비교 후보', '현재 연결', 'CPU/MPS 실기 확인 · Windows/CUDA/Intel Mac 미검증'],
            'MCP': ['실제 stdio MCP 서버', '현재 inspect_image 호출. 문서·검색·보고서·업무 티켓 도구로 확장', '현재 기반 + 확장 설계', '실호출 구현 · 나머지 도구 미구현'],
            '저장소': ['현재 Django ORM/SQLite + 파일 → 목표 MySQL + Qdrant + 파일', '업무·버전 정본 / 검색 인덱스 / 원본·근거·보고서 저장', '현재 기반 + 전환 설계', 'MySQL/Qdrant 미연결'],
            '웹·백엔드': ['Django 5.2.17 + FastAPI 0.142.2 단일 ASGI + HTML/CSS/JS', '기준 확정·이미지 접수·결과·이력. 문서·보완·승인은 확장 대상', '현재 연결', '일부 화면 구현 · 업무 전체 미구현'],
        }
        for label, values in rows.items():
            pattern = r'<tr><td>' + re.escape(label) + r'</td>.*?</tr>'
            replacement = '<tr>' + ''.join('<td>' + E(v) + '</td>' for v in [label] + values) + '</tr>'
            content, count = re.subn(pattern, lambda m, replacement=replacement: replacement, content, count=1, flags=re.S)
            if count != 1:
                raise ValueError(f'missing technology {label}')
        content = content.replace('기존 Mac Vision OCR은 현재 구현 이력으로 남기며 공통 배포에는 PaddleOCR를 우선 검증합니다.', '기본 OCR은 공통 RapidOCR이고 Apple Vision은 Mac 비교 옵션입니다. PaddleOCR는 별도 비교 후보로 둡니다. 로컬 OCR의 MPS 사용과 공용 CUDA LLM 운영을 구분합니다.')
        return block(content, 'DEVICES', '<h3>OCR 장치 선택과 검증 범위</h3>' + table(['환경', '자동 선택', '검증 상태'], [
            ['Windows / Linux · NVIDIA CUDA 사용 가능', 'CUDA', '선택·전환 로직 구현. 이 장비에서 Windows/CUDA 실기 미검증'],
            ['Windows / Linux · GPU 없음 또는 CUDA 사용 불가', 'CPU', '공통 ONNX 경로. Windows/Linux 실기는 별도 확인'],
            ['Intel Mac', 'CPU', '설치 의존성·실기 검증 별도 필요'],
            ['Apple Silicon Mac · MPS 사용 가능', 'MPS', DATA['ocr']['verified']],
            ['Apple Silicon Mac · MPS 사용 불가', 'CPU', 'CPU 경로 실기 확인'],
            ['GPU 준비·실행 실패', 'CPU로 전환', '전환 사유·실제 장치 기록. CPU도 실패하면 failed/null'],
        ]) + note(DATA['ocr']['model_note']) + '<p>장치 선택은 하드웨어 보유 여부와 런타임의 실제 사용 가능 여부를 함께 확인합니다. CUDA 빌드·드라이버 호환은 설치 환경에서 검증하며 Docker만으로 보장하지 않습니다.</p>')
    text = section(text, 'technology', technology)
    text = section(text, 'evaluation', lambda c: block(c.replace('현재 딸기라떼 6장은 한 개 개발·시연 원본 그룹이다. 독립 원본 6건 또는 일반화 정확도 평가로 표현하지 않는다.', '현재 3주제 18장은 개발·시연 자료다. 딸기라떼 6장 등 같은 원본 그룹의 파생물을 독립 사례로 세거나 일반화 정확도 평가로 표현하지 않는다.'), 'EVIDENCE', '<h3>현재 검증 기록과 평가의 경계</h3>' + topic_table() + note(DATA['test_record']['description']) + '<p>같은 원본에서 만든 정상·변형 이미지는 학습/검증/시험으로 흩어 분할하지 않습니다. Ragas는 검색 문맥·설명 품질 평가에 사용하고 OCR/시각 오류 탐지, 오통과율, 요청 분류, 업무 완료율은 각각 별도 평가합니다.</p>'))
    text = section(text, 'choices', lambda c: c.replace('PaddleOCR 우선 비교 · Qwen3-VL 시각 검수 후속 검증', '현재 RapidOCR 연결 · PaddleOCR 별도 비교 · Qwen3-VL 시각 검수 후속 검증').replace('원본은 수정하지 않고 이력으로 보존합니다.', '이전 역할·선택 기록은 이력으로 보존하고 최신 공통 계약과 구현 범위는 이 개정본을 따릅니다.'))
    return text


def refresh_feedback_metadata():
    """Keep review additions and the user's five filenames after regeneration."""
    standard = json.loads((PLAN / 'contract-standard-v1.json').read_text())
    if 'document_processing' not in standard:
        return
    revision = '2026-10-06-review-feedback-v1'
    filenames = [*contracts.ORGANIZED_FILES.values(), contracts.USER_ROLE_FILE, 'index.html']
    for filename in filenames:
        path = OUT / filename
        path.write_text(contracts.refresh_document_links(path.read_text(), filename))
    path = OUT / 'index.html'
    text = path.read_text().replace('하나의 통합 문서를 목적에 맞춰 나눈 네 가지 설계 문서',
                                    '기획·기능·화면·기술과 역할별 요구사항을 연결한 다섯 문서')
    text = text.replace('네 문서에서 같은 기준으로 읽을 수 있으며', '다섯 문서에서 같은 기준으로 읽을 수 있으며')
    start, end = '<!-- REVIEW-FEEDBACK:START -->', '<!-- REVIEW-FEEDBACK:END -->'
    body = ('<div class="note" id="review-feedback"><strong>검토 의견 반영 · 2026.10.06</strong>'
            '<p>문서 판독 실패는 원인·다음 행동·확정 차단 해제 조건을 함께 안내합니다. 역할별 요구사항은 기존 05 문서에서 찾고, '
            '상세 조건은 기존 33개 기능·69개 요구사항 ID로 연결합니다. 추가 내용은 설계이며 실제 문서 추출·권한 구현은 개발 예정입니다.</p>'
            '<p><a href="./02_기능정의서.html#document-recovery">문서 판독 복구</a> · '
            '<a href="./05_사용자별_기능정의서.html#permissions-detail">역할별 상세 권한</a> · '
            '<a href="./화면설계서_UI_양식.html">화면설계서 UI v3</a></p></div>')
    text = re.sub(re.escape(start) + r'.*?' + re.escape(end), '', text, flags=re.S)
    text = re.sub(r'(<main\b[^>]*>)', lambda m: m[1] + start + body + end, text, count=1)
    path.write_text(text)
    map_path = OUT / 'document-map.json'
    mapping = json.loads(map_path.read_text())
    mapping['revision'] = revision
    mapping['purpose'] = '검토 의견을 기획·기능·화면·기술·사용자별 정의서와 화면설계서에 일관되게 반영'
    for source in mapping['sources']:
        source['sha256'] = hashlib.sha256((ROOT / source['path']).read_bytes()).hexdigest()
    for doc in mapping['documents']:
        doc['revision'] = revision
        doc['sha256'] = hashlib.sha256((OUT / doc['file']).read_bytes()).hexdigest()
    for doc in mapping.get('companion_documents', []):
        if doc['id'] == 'user-roles':
            doc['file'] = contracts.USER_ROLE_FILE
            doc['purpose'] = '7역할별 요구사항·35개 행위·권한·범위·선행조건·허용/거절·기능/요구사항/시험 추적'
            doc['revision'] = revision
            doc['sha256'] = hashlib.sha256((OUT / doc['file']).read_bytes()).hexdigest()
    mapping['review_feedback'] = {
        'as_of': '2026-10-06',
        'preserved': ['33개 기능 ID', '69개 원 요구사항 ID·원문', '기존 시험 ID', '검수→보완→재검수→최종 승인'],
        'added': ['문서 4단계·8상태·14원인 복구', '확정 차단·버전·재처리 예산', '7역할·35개 상세 행위', 'SC-01/02/08 UI 예시'],
        'status': '설계·미구현 / 추가 수용 시험 미실행',
    }
    mapping['contract_standard']['sha256'] = hashlib.sha256((OUT / '공통실행계약.html').read_bytes()).hexdigest()
    ui_file = '화면설계서_UI_양식.html'
    mapping['screen_design'] = {'file': ui_file, 'version': '3.0', 'screens': 10, 'notes': 50,
                                'sha256': hashlib.sha256((OUT / ui_file).read_bytes()).hexdigest()}
    pdf = ROOT / 'output/pdf/화면설계서_v3.pdf'
    if pdf.exists():
        mapping['screen_design']['pdf'] = {'path': str(pdf.relative_to(ROOT)),
                                           'sha256': hashlib.sha256(pdf.read_bytes()).hexdigest()}
    map_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + '\n')
    guide_path = OUT / '사용안내.txt'
    guide = guide_path.read_text().replace('\n사용자별_기능정의서.html:', '\n' + contracts.USER_ROLE_FILE + ':')
    marker = '\n검토 의견 반영 · 2026-10-06\n'
    guide = guide.split(marker)[0] + marker
    guide += ('다섯 문서를 함께 개정했습니다. 원 기능 33개·요구사항 69개·기존 시험 ID는 유지합니다.\n'
              '02 #document-recovery: 원인·사용자 안내·CTA·재처리·확정 차단 해제.\n'
              '05 #permissions-detail: 7역할·35개 상세 행위, 범위·권한·허용/거절·원 요구사항/시험 연결.\n'
              '화면설계서_UI_양식.html: v3.0, SC-01/02/08 문서 실패·부분 판독·예산 예시, 총 10화면·50설명.\n'
              '공통실행계약.html #document-processing: 문서 독립 4단계·8상태·14원인; 기존 검수 상태 5영역과 구분.\n'
              '문서 추출·자동 재처리·다중 사용자 권한은 설계·미구현이며 추가 수용 시험은 미실행입니다.\n'
              '최신 설계는 번호가 있는 다섯 문서이며, 통합 v2/v3 원본은 이전 상세 기록을 함께 보존합니다.\n')
    guide_path.write_text(guide)


def main():
    contracts.main()
    for name, transform in [('기획서', refresh_plan), ('기능정의서', refresh_functions),
                            ('화면정의서', refresh_screens), ('기술스펙정의서', refresh_technical)]:
        filename = contracts.ORGANIZED_FILES[f'{name}.html']
        path = OUT / filename
        path.write_text(contracts.refresh_document_links(transform(refresh_common(path.read_text())), filename))
    path = OUT / 'index.html'
    text = refresh_common(path.read_text())
    text = text.replace('이번 분리는 문서 편집 작업이며 앱 구현과 외부 요구사항 승인 상태를 바꾸지 않습니다.', '이번 개정은 최신 상태·항목 ID·반복 계약과 현재 코드·직전 검증 기록을 반영합니다. 네 문서에서 같은 기준으로 읽을 수 있으며 후속 개발 완료를 뜻하지 않습니다.')
    path.write_text(contracts.refresh_document_links(text, path.name))
    build_contract_standard.build()

    map_path = OUT / 'document-map.json'
    mapping = json.loads(map_path.read_text())
    mapping['date'] = DATA['as_of']
    mapping['revision'] = DATA['revision']
    mapping['purpose'] = DATA['purpose']
    mapping['refresh_script'] = 'scripts/refresh_planning_documents.py'
    sources = [s['path'] for s in mapping['sources']]
    for source in ['docs/planning/implementation-snapshot-2026-10-06.json', 'docs/planning/functional-definition-v3.json',
                   'app/contracts.py', 'app/topics.py', 'app/ocr_device.py', 'requirements.lock.txt']:
        if source not in sources:
            sources.append(source)
    mapping['sources'] = [{'path': p, 'sha256': hashlib.sha256((ROOT / p).read_bytes()).hexdigest()} for p in sources]
    for doc in mapping['documents']:
        doc['file'] = contracts.ORGANIZED_FILES.get(doc['file'], doc['file'])
        doc['updated_at'] = DATA['as_of']
        doc['revision'] = DATA['revision']
        doc['sha256'] = hashlib.sha256((OUT / doc['file']).read_bytes()).hexdigest()
    mapping['implementation_basis'] = DATA['evidence_basis']
    mapping['unresolved'] = [DATA['unresolved']]
    map_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + '\n')
    guide_path = OUT / '사용안내.txt'
    guide = re.sub(r'^정리일:.*$', '정리일: 2026-10-05 / 최신 개정: 2026-10-06', guide_path.read_text(), flags=re.M)
    guide = guide.replace('현재 구현 표시는 기존 README의 기록 기준이고, 모델·앱을 이번에 다시 실행하지 않았습니다.', '현재 구현 표시는 2026-10-06 코드·README 직전 검증 기록 기준이며 이번 문서 갱신에서 모델·앱 실험을 새로 수행하지 않았습니다.')
    marker = '\n네 문서 개정본 · 2026-10-06\n'
    guide = guide.split(marker)[0] + marker
    guide += '최신 상태·rule_id·반복 한도와 Django/FastAPI·RapidOCR·3주제 18장 기록을 반영했습니다.\n'
    guide += '재생성: python scripts/refresh_planning_documents.py (저장소 루트).\n'
    guide += '구현 기준: ../implementation-snapshot-2026-10-06.json / 공통 계약: ../contract-standard-v1.json\n'
    guide += DATA['unresolved'] + '\n'
    guide_path.write_text(guide)
    refresh_feedback_metadata()
    print('Refreshed four HTML documents, index and source map for 2026-10-06.')


if __name__ == '__main__':
    main()
