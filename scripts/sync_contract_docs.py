#!/usr/bin/env python3
"""Synchronize current planning documents from the common execution contract.

Historical v1/v2 functional documents and unrelated UI drafts are preserved.
Run this again after changing docs/planning/contract-standard-v1.json.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import html
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / 'docs/planning'
ORGANIZED = PLAN / 'organized'
ORGANIZED_FILES = {
    '기획서.html': '01_기획서.html',
    '기능정의서.html': '02_기능정의서.html',
    '화면정의서.html': '03_화면정의서.html',
    '기술스펙정의서.html': '04_기술스펙정의서.html',
}
USER_ROLE_FILE = '05_사용자별_기능정의서.html'
STANDARD = json.loads((PLAN / 'contract-standard-v1.json').read_text())
E = html.escape
POLICY = STANDARD['retry_policy']
TOOL_RETRIES = POLICY['tool_max_retries']
AGENT_RECHECKS = POLICY['agent_max_rechecks']
REPEAT_TEXT = (f'초기 설계값은 도구 재시도 추가 {TOOL_RETRIES}회(총 {TOOL_RETRIES + 1}번 시도), '
               f'에이전트 재검사 추가 {AGENT_RECHECKS}회(총 {AGENT_RECHECKS + 1}번 검사)이다. '
               '자동 반복·체크포인트 재개는 현재 미구현이며 현재 실행의 추가 반복은 0회다. '
               'Q03에서는 파일·픽셀·전체 실행의 시간·비용 운영 한도만 실측 후 확정한다.')


def table(headers, rows):
    return ('<div class="table-wrap"><table><thead><tr>'
            + ''.join(f'<th scope="col">{E(h)}</th>' for h in headers)
            + '</tr></thead><tbody>'
            + ''.join('<tr>' + ''.join(f'<td>{E(str(v))}</td>' for v in row) + '</tr>' for row in rows)
            + '</tbody></table></div>')


def refresh_document_links(text, filename):
    """Keep the user's numbered filenames and the companion entry points."""
    for old, new in ORGANIZED_FILES.items():
        text = text.replace(f'href="./{old}', f'href="./{new}')
    text = text.replace('href="./사용자별_기능정의서.html', f'href="./{USER_ROLE_FILE}')
    nav_pattern = r'(<nav\b[^>]*class="doc-nav"[^>]*>)(.*?)(</nav>)'
    def update_nav(match):
        body = match[2]
        if '사용자별_기능정의서.html' not in body:
            body += f'<a href="./{USER_ROLE_FILE}">사용자별 기능정의서</a>'
        return match[1] + body + match[3]
    text = re.sub(nav_pattern, update_nav, text, count=1, flags=re.S)
    text = re.sub(r'<!-- USER-ROLE-LINK:START -->.*?<!-- USER-ROLE-LINK:END -->', '', text, flags=re.S)
    if filename == '02_기능정의서.html':
        entry = ('<div class="note"><strong>사용자 관점에서 먼저 살펴보기</strong>'
                 f'<p><a href="./{USER_ROLE_FILE}">사용자별 기능정의서</a>에서 발주자·제작자·검수 담당자의 행동과 권한을 확인한 뒤, '
                 '기능·요구사항 ID를 눌러 이 문서의 상세 조건으로 이동할 수 있습니다. 사용자 역할·권한 배정은 검토 제안입니다. 상세 수용 기준은 이 문서, 상태·항목 ID·반복 정책은 '
                 '<a href="./공통실행계약.html">공통 실행 계약</a>을 기준으로 확인합니다.</p></div>')
        text = replace_section(text, 'overview', lambda body: re.sub(r'(</h2>)', lambda m: m[1] + '<!-- USER-ROLE-LINK:START -->' + entry + '<!-- USER-ROLE-LINK:END -->', body, count=1))
    elif filename == 'index.html':
        entry = (f'<a class="link-card" href="./{USER_ROLE_FILE}" style="display:block;text-decoration:none;margin:0 0 20px">'
                 '<span class="eyebrow">COMPANION / USER ROLES · 2026.10.06</span><span class="arrow" aria-hidden="true">↗</span>'
                 '<strong>사용자별 기능정의서</strong><p>누가 무엇을 하고, 어느 단계에서 확인·승인하는가</p>'
                 '<p class="muted">4종 상세 문서를 돕는 사용자 관점 안내 · 역할·권한 배정은 검토 제안</p>'
                 '<p class="muted">사용자 역할·권한·업무 인계 → 33개 기능·69개 요구사항 상세 연결</p></a>')
        text = re.sub(r'(<main\b[^>]*>)', lambda m: m[1] + '<!-- USER-ROLE-LINK:START -->' + entry + '<!-- USER-ROLE-LINK:END -->', text, count=1)
    return text


def state_table():
    rows = []
    for domain in STANDARD['state_domains']:
        values = []
        for value in domain['values']:
            key = 'null' if value['value'] is None else value['value']
            suffix = '' if value['supported'] else '（설계·미구현）'
            values.append(f"{key}: {value['label']}{suffix}")
        note = domain['note']
        if domain.get('future_values'):
            note += ' 후속 값: ' + ' · '.join(f'{k}: {v}（설계·미구현）' for k, v in domain['future_values'].items())
        rows.append([domain['title'], domain['field'], ' / '.join(values), note])
    return table(['구분', '필드', '값과 화면 표시', '적용·지원 범위'], rows)


def identity_html():
    identity = STANDARD['identity']
    rows = [
        ['공개 항목 키', identity['canonical_field'], 'rules[].id는 읽기 호환 별칭. 둘 다 있으면 같은 값이어야 하며 버전 내 중복 ID는 거절한다.'],
        ['현재 논리 항목', ' + '.join(identity['current_logical_key']), 'C01 등은 전역 고유하지 않다.'],
        ['현재 조건 스냅샷', ' + '.join(identity['current_snapshot_key']), 'condition_id가 없는 주제는 null 문맥으로 기록한다.'],
        ['실행별 결과', ' + '.join(identity['result_key']), '기준 전체 스냅샷은 해당 run에 보존한다.'],
        ['목표 논리 항목', ' + '.join(identity['planned_logical_key']), '프로젝트와 발주 기준 묶음 안에서 안정적으로 식별한다.'],
        ['목표 조건 스냅샷', ' + '.join(identity['planned_snapshot_key']), '같은 항목의 기대값 변경은 rule_id 유지 + 새 기준 버전으로 기록한다.'],
        ['선택 DB 내부 키', identity['optional_internal_key'], 'MySQL 외래키가 필요할 때만 도입. 공개 rule_id를 대체하지 않는다.'],
    ]
    return ('<h3>항목 ID와 기준 버전</h3>' + table(['대상', '식별 키', '원칙'], rows)
            + '<p>항목 분할·병합 또는 의미 변경에는 새 rule_id를 부여합니다. 원본 기준 파일 바이트는 그대로 보존하며, '
              '입력의 id 별칭만 정규화해 기존 criteria_version과 확정 기록을 유지합니다.</p>')


def retry_html():
    rows = [
        ['도구 재시도', f'추가 {TOOL_RETRIES}회 / 총 {TOOL_RETRIES + 1}번', POLICY['tool_trigger'], ' + '.join(POLICY['tool_counter_key'])],
        ['에이전트 재검사', f'추가 {AGENT_RECHECKS}회 / 총 {AGENT_RECHECKS + 1}번', POLICY['agent_trigger'], ' + '.join(POLICY['agent_counter_key'])],
    ]
    return ('<h3>반복 한도와 재검수 구분</h3><p class="note">' + E(REPEAT_TEXT) + '</p>'
            + table(['구분', '초기 설계값', '허용 사유', '카운터 범위'], rows)
            + '<ul>' + ''.join('<li>' + E(r) + '</li>' for r in POLICY['rules']) + '</ul>')


def generated_block(name, content):
    return f'<!-- CONTRACT-{name}:START -->{content}<!-- CONTRACT-{name}:END -->'


def replace_section(text, section_id, transform):
    pattern = re.compile(r'(<section\b[^>]*\bid="' + re.escape(section_id) + r'"[^>]*>)(.*?)(</section>)', re.S)
    text, count = pattern.subn(lambda m: m[1] + transform(m[2]) + m[3], text, count=1)
    if count != 1:
        raise ValueError(f'missing section {section_id}')
    return text


def remove_generated(text, name):
    return re.sub(r'<!-- CONTRACT-' + name + r':START -->.*?<!-- CONTRACT-' + name + r':END -->', '', text, flags=re.S)


def common_link(text, organized):
    href = './공통실행계약.html' if organized else './organized/공통실행계약.html'
    content = ('<p class="note" style="margin:16px auto;max-width:1180px;padding:14px 18px">'
               '<strong>공통 계약 · 2026.10.06:</strong> 상태 5영역·rule_id·반복 한도는 '
               f'<a href="{href}">공통 실행 계약</a>을 따릅니다. '
               f'도구 재시도 추가 {TOOL_RETRIES}회·에이전트 재검사 추가 {AGENT_RECHECKS}회는 초기 설계값이며 자동 반복은 미구현입니다.</p>')
    text = remove_generated(text, 'LINK')
    text = text.replace('</header>', '</header>' + generated_block('LINK', content), 1)
    if organized and not re.search(r'<nav\b[^>]*class="doc-nav"[^>]*>.*?href="\./공통실행계약\.html".*?</nav>', text, re.S):
        text = text.replace('</nav></header>', '<a href="./공통실행계약.html">공통 실행 계약</a></nav></header>', 1)
    return text


def leaf_changes(old, new):
    changes = {}
    if isinstance(old, dict) and isinstance(new, dict):
        for key in old.keys() & new.keys():
            changes.update(leaf_changes(old[key], new[key]))
    elif isinstance(old, list) and isinstance(new, list):
        for a, b in zip(old, new):
            changes.update(leaf_changes(a, b))
    elif isinstance(old, str) and isinstance(new, str) and old != new:
        changes[old] = new
    return changes


def replace_leaves(text, changes):
    for old, new in sorted(changes.items(), key=lambda x: len(x[0]), reverse=True):
        text = text.replace(old, new).replace(E(old), E(new))
    return text


def replace_embedded_json(text, element_id, data):
    pattern = r'(<script\b(?=[^>]*\bid="' + element_id + r'")[^>]*>).*?(</script>)'
    payload = json.dumps(data, ensure_ascii=False).replace('</', '<\\/')
    text, count = re.subn(pattern, lambda m: m[1] + payload + m[2], text, count=1, flags=re.S)
    if count != 1:
        raise ValueError(f'missing embedded JSON {element_id}')
    return text


def main():
    spec_path = PLAN / 'functional-definition-v3.json'
    old_spec = json.loads(spec_path.read_text())
    spec = copy.deepcopy(old_spec)
    features = {f['id']: f for f in spec['features']}
    features['F-06']['acceptance'][1] = ('전체 적용 항목은 execution_status의 실행 완료(succeeded)·미실행(not_run)·실행 실패(failed)로 '
                                       '서로 겹치지 않게 집계한다. 확인 필요는 verdict에 기록하며 실행 집계에 섞지 않는다.')
    f08 = features['F-08']
    f08['process'][1] = ('같은 입력의 일시적 도구 오류는 추가 ' + str(TOOL_RETRIES) + '회까지 재시도하는 설계이며, '
                        '근거 충돌·판독 불확실성의 에이전트 재검사는 추가 ' + str(AGENT_RECHECKS) + '회로 별도 집계한다. '
                        '체크포인트 재개·역할 인계로 횟수를 초기화하지 않고 입력 버전 변경은 새 실행으로 처리한다.')
    f08['exception'] = ('실행 실패는 execution_status=failed와 verdict=null(미판정)로 남긴다. ' + REPEAT_TEXT)
    f08['current'] = ('검사 요청의 멱등성·결과 저장 기반 있음. 자동 도구 재시도·에이전트 재검사·그래프 재개·후속 업무 멱등성은 미구현. '
                      '현재 추가 반복은 0회이며 실패를 실행 완료로 표시하지 않는다.')
    f08['acceptance'][1] = ('도구 재시도와 에이전트 재검사 카운터를 따로 검증하고, 최초 시도·재개·역할 인계의 횟수 계산이 공통 계약과 일치한다. '
                          '재전송에서 중복 파일 버전·작업·승인이 생성되지 않으며 쓰기 도구는 멱등성 키·처리 결과 조회 후 재시도한다.')
    f08['narrative']['solution'] = '입력 버전과 요청 키를 고정하고 도구 재시도·에이전트 재검사 한도를 분리해 체크포인트·중복 방지를 설계한다.'
    f08['w5h1']['how'] = f08['narrative']['solution']
    s01 = features['S-01']
    s01['process'][2] = ('관찰과 근거가 충돌하고 추가 근거 확보가 가능하면 검증 에이전트가 같은 run·rule_id에서 '
                        f'추가 {AGENT_RECHECKS}회(총 {AGENT_RECHECKS + 1}번 검사)까지 재검사를 요청한다. '
                        f'일시적 도구 오류의 추가 {TOOL_RETRIES}회 재시도는 별도 카운터이며 자동 반복은 현재 미구현이다.')
    s01['exception'] = ('역할명 나열만으로 다중 에이전트 구현을 대체하지 않는다. 에이전트 재검사 추가 '
                        f'{AGENT_RECHECKS}회 또는 전체 실행 시간·비용 한도에 도달하면 미해결 질문을 사람에게 넘긴다. '
                        '최종 승인 상태는 보완 작업·검수 실행 상태와 별도로 기록한다.')
    spec['contracts'][1][2] = '적용 기준 전체 목록과 항목 실행 상태(execution_status)·판정(verdict)을 분리. run.status는 실행 집계이며 최종 승인과 별개'
    spec['contracts'][2][2] = '판정은 충족/미충족/확인 필요/null(미판정). 해당 없음은 설계·미구현. 실행 실패는 failed와 null로 보존'
    spec['contracts'][3][1] = 'task_id · run_id · rule_ids · task.status · draft · confirmed_by · assignee · due_at · revision'
    spec['contracts'][3][2] = 'task.status: 초안/요청 확정/수정본 제출/해결. approval.status: 승인 대기/승인/보류는 별도 설계이며 두 업무 상태 모두 미구현'
    if not any(row[0] == '최종 승인' for row in spec['contracts']):
        spec['contracts'].append(['최종 승인', 'approval_id · run_id · criteria_version · asset_version · approval.status · decided_by · decided_at',
                                 '보완 작업 해결과 최종 승인은 다르다. 권한 있는 사람이 최신 버전을 확인한 뒤 승인/보류하며 현재 미구현'])
    spec['questions'][2]['proposal'] = ('파일·픽셀·전체 실행의 시간·비용 운영 한도를 실측 후 확정하고 경계 시험을 마련한다. '
                                       f'추가 도구 재시도 {TOOL_RETRIES}회·에이전트 재검사 {AGENT_RECHECKS}회는 공통 계약의 초기 설계값으로 분리하며 자동 반복은 미구현이다.')
    spec['rules'][3] = '미실행·실행 실패·확인 필요·미판정을 통과와 구분한다. 실행 실패의 verdict는 null이며 필수 항목이 남으면 자동 최종 승인하지 않는다.'
    spec['flow'][4][5] = f'재검사 이유와 추가 근거, 에이전트 추가 {AGENT_RECHECKS}회 / 도구 재시도 추가 {TOOL_RETRIES}회 별도 기록 · 자동 반복 미구현'
    spec['execution_contract'] = STANDARD
    spec['contract_standard_ref'] = 'contract-standard-v1.json'
    change_note = ('2026-10-06 공통 실행 계약 v1 반영: 상태 5영역·rule_id·기준 버전·도구 재시도와 에이전트 재검사 한도를 통일. '
                   '초기 반복 설계와 현재 미구현을 구분하며 원 요구사항·기능 ID·이전 역할 기록은 보존한다.')
    if change_note not in spec['change_notes']:
        spec['change_notes'].append(change_note)
    changes = leaf_changes(old_spec, spec)
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + '\n')

    functional_path = PLAN / 'functional-definition-v3.html'
    text = replace_leaves(functional_path.read_text(), changes)
    text = replace_embedded_json(text, 'spec-data', spec)
    text = common_link(text, False)
    text = remove_generated(text, 'SUMMARY')
    text = replace_section(text, 'panel-structure', lambda content: content + generated_block('SUMMARY', '<h3>공통 실행 계약</h3>' + state_table() + identity_html() + retry_html()))
    functional_path.write_text(text)

    for filename in [*ORGANIZED_FILES.values(), 'index.html']:
        path = ORGANIZED / filename
        text = replace_leaves(path.read_text(), changes)
        text = common_link(text, True)
        if filename == '02_기능정의서.html':
            def contracts(content):
                content = re.sub(r'<h3>(?:판정과 실행 상태|판정·실행·업무 상태의 공통 계약)</h3>.*?</table></div>', '<h3>판정·실행·업무 상태의 공통 계약</h3>' + state_table(), content, count=1, flags=re.S)
                content = re.sub(r'(<h3>저장·전달되는 논리 데이터</h3>).*?</table></div>', lambda m: m[1] + table(['대상', '필수 연결 필드', '검증 원칙'], spec['contracts']), content, count=1, flags=re.S)
                content = remove_generated(content, 'DETAILS')
                return content + generated_block('DETAILS', identity_html() + retry_html())
            text = replace_section(text, 'contracts', contracts)
            def original_requirements(content):
                # Feature acceptance may reuse an original requirement sentence.
                # Keep source requirements unchanged while updating authored features.
                for requirement in spec['requirements']:
                    pattern = (r'(<details\b[^>]*\bid="' + re.escape(requirement['id'])
                               + r'"[^>]*>)(.*?)(</details>)')
                    def preserve_test(match):
                        body = re.sub(r'(<dt>수용 기준</dt><dd>).*?(</dd>)',
                                      lambda field: field[1] + E(requirement['test']['criterion']) + field[2],
                                      match[2], count=1, flags=re.S)
                        return match[1] + body + match[3]
                    content = re.sub(pattern, preserve_test, content, count=1, flags=re.S)
                return content
            text = replace_section(text, 'requirements', original_requirements)
        elif filename == '04_기술스펙정의서.html':
            def data_contract(content):
                content = re.sub(r'<h3>공통 식별자</h3>.*?<h3>핵심 데이터 계약</h3>', '<h3>공통 식별자</h3><p>project_id · request_id · run_id · document_version · criteria_version · asset_version · rule_id · evidence_id를 결과와 연결합니다.</p><h3>핵심 데이터 계약</h3>', content, count=1, flags=re.S)
                content = re.sub(r'(<h3>핵심 데이터 계약</h3>).*?</table></div>', lambda m: m[1] + table(['대상', '필드', '보존·검증 원칙'], spec['contracts']), content, count=1, flags=re.S)
                return remove_generated(content, 'IDENTITY') + generated_block('IDENTITY', identity_html())
            text = replace_section(text, 'data', data_contract)
            def operations(content):
                content = re.sub(r'<h3>판정와 실행 상태를 따로 기록</h3>|<h3>판정과 실행 상태를 따로 기록</h3>', '<h3>판정·실행·업무 상태를 따로 기록</h3>', content)
                first_table = state_table() + table(['추가 영역', '값', '분리 원칙'], [['인덱싱 상태', '대기·처리·완료·실패', '검색 적재 작업의 별도 상태. 검수 실행이나 판정과 섞지 않는다.'], ['의도 분류', '신규 검수·재검수·기준 변경·상태 조회·기타', '업무 유형 라벨. 추가 질문은 판정이나 의도 라벨이 아닌 후속 설계의 흐름 제어다.']])
                content = re.sub(r'(<h3>판정·실행·업무 상태를 따로 기록</h3>).*?(<h3>재시도와 멱등성</h3>)', lambda m: m[1] + first_table + m[2], content, count=1, flags=re.S)
                content = re.sub(r'<p class="note">동일 항목의 자동 재검사는.*?</p>', '', content, count=1, flags=re.S)
                content = remove_generated(content, 'RETRY')
                content = content.replace('<h3>실제 MCP 호출과 추적</h3>', generated_block('RETRY', retry_html()) + '<h3>실제 MCP 호출과 추적</h3>', 1)
                return content
            text = replace_section(text, 'operations', operations)
        elif filename == '03_화면정의서.html':
            text = text.replace('상태별 작업 목록: 초안·사람 확정·수정 제출·해결 확인', '상태별 작업 목록: 초안·요청 확정·수정본 제출·해결（설계·미구현）')
            text = text.replace('초안·확정·제출·해결·승인을 별도 표시.', 'task.status의 초안·요청 확정·수정본 제출·해결과 approval.status의 승인 대기·승인·보류를 별도로 표시（설계·미구현）.')
            text = text.replace('대기·실행 중·사람 확인 대기·재시도·중단을 표시.', '현재 run.status는 실행 중·전체 실행 완료·부분 실행 완료·실행 실패를 표시. 실행 대기·사람 확인 대기·취소 완료와 재개·자동 반복은 설계·미구현으로 구분.')
            text = text.replace('같은 입력에서 허용된 중단 지점 재개. 입력이 바뀌면 새 run으로 처리.', REPEAT_TEXT + ' 같은 입력의 재개는 설계·미구현이며 입력 버전이 바뀌면 새 run으로 처리.')
            text = text.replace('미충족+완료, 확인 필요+미실행, 확인 필요+실패', '미충족+실행 완료, 확인 필요+미실행(기존 결과 호환), 미판정(null)+실행 실패')
            text = text.replace('<td>미충족</td><td>완료</td>', '<td>미충족</td><td>실행 완료</td>')
            def screens_states(content):
                for label in ['실행', '항목 판정', '보완', '승인']:
                    content = re.sub(r'<tr><td>' + label + r'</td>.*?</tr>', '', content, count=1, flags=re.S)
                content = content.replace('<em>확인 필요 / 실행 실패</em>', '<em>미판정(null) / 실행 실패</em>')
                content = remove_generated(content, 'STATES')
                return content + generated_block('STATES', '<h3>검수·보완·승인의 표준 상태</h3>' + state_table() + '<p>' + E(REPEAT_TEXT) + '</p>')
            text = replace_section(text, 'states', screens_states)
        path.write_text(refresh_document_links(text, filename))

    presentation_path = PLAN / 'presentation-plan-v2.html'
    text = common_link(presentation_path.read_text(), False)
    text = text.replace('criterion_id', 'rule_id')
    text = text.replace('실행 상태(성공·실패·미실행)와 항목 판정(일치·불일치·확인 필요)을 구분합니다.', '항목 실행 상태(실행 완료·실행 실패·미실행), 항목 판정(충족·미충족·확인 필요·미판정), 검수 실행·보완 작업·최종 승인 상태를 구분합니다. 해당 없음은 설계·미구현입니다.')
    text = text.replace('동일 항목 자동 재검사는 최대 2회로 시작하는 설정안을 검증합니다. 해결되지 않으면 사람 확인으로 전환합니다.', REPEAT_TEXT + ' 재개·역할 인계에서 카운터를 초기화하지 않으며 미해결 항목은 사람 확인으로 넘깁니다.')
    text = text.replace('재검사는 상한을 두고, 해결되지 않으면 사람 확인으로 전환합니다.', f'에이전트 재검사는 추가 {AGENT_RECHECKS}회, 일시적 도구 오류 재시도는 추가 {TOOL_RETRIES}회로 따로 제한하는 초기 설계입니다. 자동 반복은 미구현이며 미해결 항목은 사람에게 넘깁니다.')
    text = remove_generated(text, 'SUMMARY')
    text = replace_section(text, 'architecture', lambda content: content + generated_block('SUMMARY', '<h3>공통 상태·항목·반복 계약</h3>' + state_table() + identity_html() + retry_html()))
    m = re.search(r'<script\b(?=[^>]*\bid="plan-data")[^>]*>(.*?)</script>', text, re.S)
    data = json.loads(m[1])
    data['execution_contract'] = STANDARD
    data['contract_standard_ref'] = 'contract-standard-v1.json'
    presentation_path.write_text(replace_embedded_json(text, 'plan-data', data))

    for filename in ['functional-requirements-trace-v3.csv', 'functional-w5h1-v3.csv']:
        path = PLAN / filename
        with path.open(encoding='utf-8-sig', newline='') as file:
            rows = list(csv.reader(file))
        if filename == 'functional-requirements-trace-v3.csv':
            # This export represents original requirement/test text, not new
            # implementation acceptance criteria from a feature definition.
            requirements = {r['id']: r for r in spec['requirements']}
            for row in rows[1:]:
                requirement = requirements[row[0]]
                feature = features[requirement['functionId']]
                row[1:] = [requirement['test']['priority'], feature['id'], feature['name'],
                           feature['delivery'], feature['status'], requirement['text'],
                           requirement['test']['criterion']]
        else:
            for row in rows[1:]:
                feature = features[row[0]]
                context = feature['w5h1']
                story = feature['narrative']
                row[1:] = [feature['name'], context['what'], context['why'], context['who'],
                           context['when'], context['where'], context['how'],
                           story['expected_effect'], story['verification'], story['effect_status']]
        with path.open('w', encoding='utf-8-sig', newline='') as file:
            csv.writer(file, lineterminator='\n').writerows(rows)

    map_path = ORGANIZED / 'document-map.json'
    mapping = json.loads(map_path.read_text())
    for document in mapping['documents']:
        document['file'] = ORGANIZED_FILES.get(document['file'], document['file'])
    mapping['contract_standard'] = {'id': STANDARD['standard_id'], 'source': 'docs/planning/contract-standard-v1.json', 'file': '공통실행계약.html', 'updated_at': STANDARD['updated_at'], 'sync_script': 'scripts/sync_contract_docs.py'}
    for source in mapping['sources']:
        source['sha256'] = hashlib.sha256((ROOT / source['path']).read_bytes()).hexdigest()
    if not any(source['path'] == 'docs/planning/contract-standard-v1.json' for source in mapping['sources']):
        mapping['sources'].append({'path': 'docs/planning/contract-standard-v1.json', 'sha256': hashlib.sha256((PLAN / 'contract-standard-v1.json').read_bytes()).hexdigest()})
    else:
        for source in mapping['sources']:
            if source['path'] == 'docs/planning/contract-standard-v1.json':
                source['sha256'] = hashlib.sha256((PLAN / 'contract-standard-v1.json').read_bytes()).hexdigest()
    map_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + '\n')
    guide_path = ORGANIZED / '사용안내.txt'
    guide = guide_path.read_text().replace('같은 폴더의 HTML 다섯 개를 함께 보관하면', '같은 폴더의 HTML 문서를 함께 보관하면')
    guide = re.sub(r'(?m)^(기획서|기능정의서|화면정의서|기술스펙정의서)\.html:', lambda m: ORGANIZED_FILES[m[1] + '.html'] + ':', guide)
    marker = '\n공통 실행 계약 · 2026-10-06\n'
    guide = guide.split(marker)[0]
    guide += (marker + '공통실행계약.html: 판정·항목 실행·검수 실행·보완 작업·최종 승인 5영역, rule_id·기준 버전, 반복 한도.\n'
              '원본: ../contract-standard-v1.json. 문서 적용: python scripts/sync_contract_docs.py (저장소 루트에서 실행).\n'
              + REPEAT_TEXT + '\n도구 실행 실패의 판정은 null(미판정), 항목 completed는 succeeded의 과거 읽기 별칭입니다.\n')
    guide_path.write_text(guide)
    print('Synchronized current planning documents; preserved historical definitions and unrelated UI drafts.')


if __name__ == '__main__':
    main()
