"""Render the shared contract without network access or application side effects."""
from html import escape
import json
from pathlib import Path
import re
from sync_contract_docs import ORGANIZED_FILES, refresh_document_links

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'docs/planning/contract-standard-v1.json'
DESTINATION = ROOT / 'docs/planning/organized/공통실행계약.html'


def table(headers, rows):
    head = ''.join(f'<th scope="col">{escape(h)}</th>' for h in headers)
    body = ''.join('<tr>' + ''.join(f'<td>{cell}</td>' for cell in row) + '</tr>' for row in rows)
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def bullets(items):
    return '<ul>' + ''.join(f'<li>{escape(item)}</li>' for item in items) + '</ul>'


def code(value):
    return f'<code>{escape(str(value))}</code>'


def document_processing_html(spec):
    """Render the independent document lifecycle from its canonical JSON."""
    document = spec.get('document_processing')
    if not document:
        return ''
    output = '<h2>문서 처리의 단계·실패·복구를 별도로 기록합니다.</h2>'
    output += f'<p class="note"><strong>{escape(document["design_status"])}</strong> · {escape(document["current_runtime"])}</p>'
    output += '<p>기본 대상: ' + escape(' · '.join(document['target_formats'])) + '. 후속 후보: ' + escape(' · '.join(document['future_formats'])) + '.</p>'
    output += table(['단계', '선행 조건', '완료 조건'], [
        [escape(stage['label']) + '<br>' + code(stage['id']), escape(stage['condition']), escape(stage['completion'])]
        for stage in document['stages']
    ])
    output += table(['단계별 상태', '화면 표시'], [[code(state['value']), escape(state['label'])] for state in document['stage_statuses']])
    output += bullets(document['status_rules'])
    output += '<h3 id="document-processing-budget">재처리와 예산</h3>'
    output += bullets(document['retry_rules'])
    output += '<h3>기준 확정의 차단·해제 조건</h3>' + bullets(document['confirmation_rules'])
    output += '<h3>처리 레코드</h3>' + table(['필드', '의미'], [[code(field), escape(description)] for field, description in document['record_fields'].items()])
    output += '<h3>오류 레코드</h3><p>' + ' · '.join(code(field) for field in document['error_fields']) + '</p>'
    output += bullets(document.get('error_schema_rules', []))
    output += '<h3>원인별 사용자 안내·복구 조건</h3>'
    for error in document['error_catalog']:
        output += '<details id="error-' + escape(error['code']) + '"><summary>' + code(error['code']) + ' · ' + escape(error['cause']) + '</summary>'
        output += table(['항목', '계약'], [
            ['대표 단계 / 상태', code(error['stage']) + ' / ' + code(error['stage_status'])],
            ['최초 검출 단계 허용값', ' · '.join(code(stage) for stage in error.get('allowed_stages', [error['stage']]))],
            ['사용자 안내', escape(error['user_message'])],
            ['다음 행동', escape(' · '.join(error['cta']))],
            ['자동 재시도', '일시 오류·예산 확인 후 허용' if error['retryable'] else '단순 자동 반복 금지'],
            ['재처리 조건', escape(error['retry_conditions'])],
            ['확정 차단 해제', escape(error['confirmation_release_conditions'])],
        ]) + '</details>'
    output += '<h3>미확정 선택 정책</h3>' + bullets(document['optional_unresolved_policies'])
    output += '<p><a href="./02_기능정의서.html#document-recovery">기능별 복구 정의</a> · <a href="./02_기능정의서.html#document-recovery-tests">기존 시험 ID에 연결한 복구 시험 계획</a> · <a href="./05_사용자별_기능정의서.html#permissions-detail">역할별 처리 권한 제안</a></p>'
    return output


def build():
    spec = json.loads(SOURCE.read_text(encoding='utf-8'))
    template = (DESTINATION.parent / ORGANIZED_FILES['기술스펙정의서.html']).read_text(encoding='utf-8')
    style = re.search(r'<style>(.*?)</style>', template, re.S).group(1)
    shared_script = re.findall(r'<script>(.*?)</script>', template, re.S)[-1]
    rows = []
    for domain in spec['state_domains']:
        values = '<br>'.join(
            f'{code("null" if v["value"] is None else v["value"])} → {escape(v["label"])}'
            + (' <span class="badge warm">후속 지원</span>' if domain['current'] and not v['supported'] else '')
            for v in domain['values'])
        implementation = '현재 지원' if domain['current'] else '설계 계약 · 미구현'
        rows.append([escape(domain['title']), code(domain['field']), values, implementation])
    states = '<h2>판정·실행·보완·승인을 구분합니다.</h2>'
    states += '<p>각 문서와 화면은 아래 필드·값·표시 이름을 공통으로 사용합니다. 실행 완료는 조건 충족 또는 최종 승인을 뜻하지 않습니다.</p>'
    states += table(['영역', '필드', '값 → 표시 이름', '지원 범위'], rows)
    for domain in spec['state_domains']:
        states += f'<h3>{escape(domain["title"])} 원칙</h3><p>{escape(domain["note"])}</p>'
    states += '<p class="note">전체 항목에서 조건 미충족이 발견돼도 검사 자체가 모두 수행됐다면 run.status는 completed입니다. 보완 작업의 resolved는 해당 작업의 해결이며 최종 approval.status와 별도로 관리합니다.</p>'

    identity = spec['identity']
    identity_rows = [
        ['공개 항목 키', code(identity['canonical_field']), '같은 검수 의미의 항목은 기대값 변경 후에도 ID 유지'],
        ['현재 논리 항목', code(' + '.join(identity['current_logical_key'])), '주제 간 C01 충돌 방지'],
        ['현재 조건 스냅샷', code(' + '.join(identity['current_snapshot_key'])), '당시 발주 조건과 기준 내용 특정'],
        ['실행별 결과', code(' + '.join(identity['result_key'])), 'run에 기록된 기준 스냅샷으로 근거 추적'],
        ['실제 서비스의 논리 항목', code(' + '.join(identity['planned_logical_key'])), '기준 묶음 ID를 도입하는 후속 설계'],
        ['실제 서비스의 항목 버전', code(' + '.join(identity['planned_snapshot_key'])), '같은 항목의 과거 기준 보존'],
        ['선택적 DB 내부 키', code(identity['optional_internal_key']), 'MySQL 외래키가 필요할 때만 도입'],
    ]
    ids = '<h2>항목 ID와 기준 버전을 별도로 관리합니다.</h2>'
    ids += table(['용도', '식별자', '원칙'], identity_rows)
    ids += bullets(identity['notes'] + identity['rules'])
    example = {'topic_id': 'strawberry-latte', 'criteria_version': '기존 원본 해시 유지',
               'rules': [{'rule_id': 'C01', 'id': 'C01', 'name': '상품명', 'expected': '딸기라떼'}]}
    ids += '<h3>현재 API의 호환 출력 예시</h3><p>rule_id를 표준 키로 제공합니다. id는 기존 화면·클라이언트가 읽을 수 있는 동일 값의 호환 별칭입니다.</p>'
    ids += f'<pre>{escape(json.dumps(example, ensure_ascii=False, indent=2))}</pre>'

    policy = spec['retry_policy']
    retries = '<h2>도구 오류 재시도와 에이전트 재검사를 구분합니다.</h2>'
    retries += '<p class="note">초기 설계값입니다. 현재 앱에는 자동 재시도·에이전트 재검사 루프가 구현돼 있지 않습니다. 정책을 문서에 정의한 것으로 구현 완료를 집계하지 않습니다.</p>'
    retries += table(['구분', '추가 횟수', '범위·실행 조건'], [
        ['도구 오류 재시도', f'{policy["tool_max_retries"]}회 · 최초 포함 총 3번 시도', escape(policy['tool_trigger'])],
        ['에이전트 재검사', f'{policy["agent_max_rechecks"]}회 · 최초 포함 총 2번 검사', escape(policy['agent_trigger'])],
        ['전체 실행 한도', code(policy['limits_question']) + '에서 실측 후 확정', '횟수와 별개로 호출·시간·비용 예산을 적용'],
    ])
    retries += '<h3>카운터와 중단 조건</h3>'
    retries += table(['카운터', '보존 범위'], [
        ['도구 오류 재시도', code(' + '.join(policy['tool_counter_key']))],
        ['에이전트 재검사', code(' + '.join(policy['agent_counter_key']))],
    ])
    retries += f'<p>{escape(policy["counting"])}</p>' + bullets(policy['rules'])
    retries += '<h3>딸기라떼 OCR 예시</h3><p>통신 장애는 같은 호출을 최대 두 번 더 시도하는 대상입니다. 정상 OCR 관찰이 불분명하면 영역을 확대해 한 번 추가 검사합니다. 읽은 할인율이 기준과 명확히 다르면 미충족으로 기록합니다. 사람에게 넘기는 사유와 마지막 근거를 보존합니다.</p>'

    compatibility = '<h2>기존 기록과 현재 지원 범위를 보존합니다.</h2>'
    compatibility += bullets([
        '기준 원본 파일의 바이트를 바꾸지 않아 기존 해시와 확정 버전을 유지합니다.',
        'id와 rule_id 입력을 함께 받을 때 값이 다르면 거절하고, 동일 기준의 중복 rule_id도 거절합니다.',
        '과거 저장 결과의 completed는 원본을 보존하고 실행 완료로 표시합니다. 신규 처리 결과는 succeeded로 정규화해 저장하며 전체 run의 completed는 그대로 유지합니다.',
        '기존 미실행 결과의 확인 필요 판정은 호환 범위로 보존하고 항목 실행 상태를 함께 표시합니다.',
        '자동 반복, 사람 대기·취소, 보완 작업·최종 승인, 해당 없음 판정은 해당 기능 구현과 시험을 거친 뒤 지원합니다.',
        '과거 v1/v2 문서는 당시 기록으로 보존하고 최신 정의는 이 계약과 종류별 문서에서 확인합니다.',
    ])
    compatibility += '<h3>숫자 범위 표시</h3><p>숫자 범위는 <strong>4–6개</strong>, <strong>1–2회</strong>처럼 표시합니다. HTML 생성 이전의 원문 단계부터 이 기호를 사용해 Markdown 취소선 오변환을 피합니다.</p>'
    compatibility += '<h3>공통 원본과 재생성</h3><p><a href="../contract-standard-v1.json">공통 계약 JSON</a>을 원본으로 관리합니다. 원본 변경 후 관련 정의서와 공통 계약 HTML을 아래 명령으로 다시 생성합니다.</p><pre>python scripts/sync_contract_docs.py\npython scripts/build_contract_standard.py</pre>'
    compatibility += '<p>문서 간 ID·상태·반복 정책이 충돌하면 공통 계약을 기준으로 개별 설명을 수정합니다. 실제 코드의 지원 범위와 설계 제안을 별도 표기합니다.</p>'

    panels = [('states', '상태 표준', states), ('identity', '항목 ID·버전', ids),
              ('retry', '재시도·재검사', retries)]
    document_html = document_processing_html(spec)
    if document_html:
        panels.append(('document-processing', '문서 판독·복구', document_html))
    panels.append(('compatibility', '호환·표시 원칙', compatibility))
    tabs = ''.join(f'<button type="button" role="tab" id="tab-{key}" data-panel="{key}" aria-controls="{key}" aria-selected="{str(i == 0).lower()}" tabindex="{0 if i == 0 else -1}"><span>{i + 1:02}</span>{label}</button>'
                   for i, (key, label, _) in enumerate(panels))
    sections = '\n'.join(f'<section class="doc-panel" role="tabpanel" tabindex="0" id="{key}" aria-labelledby="tab-{key}">{body}</section>' for key, _, body in panels)
    nav = ''.join(f'<a href="./{name}.html">{name}</a>' for name in ['기획서', '기능정의서', '화면정의서', '기술스펙정의서'])
    output = f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="판정·실행·업무 상태, rule_id와 기준 버전, 재시도·재검사의 공통 계약"><title>동작그만 밑장빼기냐 · 공통 실행 계약</title><style>{style}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--soft);padding:18px;border-radius:12px}}
</style></head><body><a class="skip" href="#main">본문으로 바로가기</a>
<header class="site-head"><div class="brand-row"><a class="brand" href="./index.html"><small>SKN33 FINAL · 1TEAM</small><span class="brand-mark">C</span>동작그만 밑장빼기냐</a><span class="badge">공통 계약 · 2026.10.06</span></div><nav class="doc-nav" aria-label="문서 종류"><a href="./index.html">전체 목차</a>{nav}<a href="./공통실행계약.html" aria-current="page">공통 실행 계약</a></nav></header>
<div class="hero"><span class="eyebrow">PROJECT DOCUMENTS / CONTRACT V1</span><h1>공통 실행 계약</h1><p>문서와 코드에서 함께 사용하는 상태·항목 ID·버전·반복 정책의 기준입니다.</p><span class="badge">5개 상태 영역</span><span class="badge">rule_id 기준</span><span class="badge warm">현재 지원과 설계 제안 구분</span></div>
<div class="layout"><aside class="side"><p class="caption">CONTENTS / {len(panels):02}</p><div class="tablist" role="tablist" aria-label="공통 실행 계약 목차" aria-orientation="vertical">{tabs}</div><p class="side-note">목차 버튼으로 이동합니다.<br>파일을 더블클릭해 브라우저에서 열 수 있습니다.</p><button type="button" data-print>전체 인쇄</button></aside><main id="main"><noscript><p class="note">JavaScript가 꺼져 있어 모든 내용을 순서대로 표시합니다.</p></noscript>
{sections}
</main></div><footer class="site-footer">공통 원본: contract-standard-v1.json · 초기 반복 정책의 운영값은 평가 후 확정</footer>
<script>{shared_script}</script></body></html>
'''
    DESTINATION.write_text(refresh_document_links(output, DESTINATION.name), encoding='utf-8')
    print(DESTINATION)


if __name__ == '__main__':
    build()
