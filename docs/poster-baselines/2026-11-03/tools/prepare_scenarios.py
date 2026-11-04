"""Build stage-one scenario documents from the preserved three-sample set.

Python standard library only. No model, database, original PDF or poster edits.
Existing workflow progress is preserved on subsequent regeneration.
"""
from pathlib import Path
from html import escape
import hashlib
import json


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'workflow'
STEPS = [
    ('시나리오·요구사항 설정', '기존 3종의 문구·시각·파일 조건과 의도적 보완 항목 1개씩 설계'),
    ('클라이언트 제출용 PDF 작성', '설정한 시나리오로 개발용 가상 의뢰서 PDF·수정용 MD 작성'),
    ('추출 결과 검토표 준비', 'PDF별 조건·기대값·페이지·인용·보완 예상 결과를 별도 정리'),
    ('PDF 등록·PostgreSQL 연결', '기존 파일 저장소 재사용, 문서 버전·해시·프로젝트 접근 연결'),
    ('A02 조건 추출 연결', '모의 응답과 실제 Qwen 실행을 구분하여 추출·출처 검증·저장 연결'),
    ('보완·승인·재조회 연결', '사람 보완의 출처·기준 버전·승인 기록과 재시작 후 조회 검증'),
    ('포스터 검수·재검수 시험', '승인 기준에 맞춘 정상/날짜 오류/필수 문구 누락 사례로 관통 확인'),
]
EXPECTED = {
    'EXH-NEW004': [
        ['수원의 영화', '수원의 극장'], '2026',
        {'date': '2026-05-14', 'weekday': '목요일', 'meaning': '개막일'},
        '열린문화공간 후소 전시실',
    ],
    'CON-NEW043': [
        ['수원시립교향악단', '제303회 정기연주회'],
        ['H.Berlioz', 'Symphonie fantastique'],
        {'date': '2026-04-29', 'weekday': '수요일', 'time': '19:30'},
        '수원SK아트리움 대공연장',
        {'지휘': '최희준', '첼로': '정우찬'},
        ['생상스 첼로 협주곡 1번', '베를리오즈 환상 교향곡'],
    ],
    'FES-NEW072': [
        '2025 탄소중립 한마당 축제', ['오늘무해!', 'FESTIVAL'],
        {'date': '2025-11-02', 'weekday': '일요일', 'start': '12:00', 'end': '19:00'},
        '고색역 상부공원 일원', '탄소중립 그린도시 기념식',
        ['탄소중립 어린이 직업체험', 'KidZaniaGo!'],
        ['탄소공감', '버스킹', '영화상영회'],
        '주차공간 협소로 가급적 대중교통 이용 권장',
    ],
}
GAPS = {
    'EXH-NEW004': {
        'id': 'EXH-G01', 'field': '관람시간',
        'statement': '포스터에 관람 가능 요일과 시간을 안내한다. 구체적인 운영시간은 클라이언트 확인 후 확정한다.',
        'question': '관람 가능한 요일·시작/종료 시간과 휴관일이 있으면 알려주세요.',
        'reason': '개막일과 관람 운영시간을 구분하고, 운영시간을 모델이 임의 생성하지 않는지 확인한다.',
    },
    'CON-NEW043': {
        'id': 'CON-G01', 'field': '관람연령',
        'statement': '포스터에 관람 가능한 연령을 안내한다. 최소 관람연령은 클라이언트 확인 후 확정한다.',
        'question': '이 공연의 최소 관람연령과 포스터에 사용할 확정 문구를 알려주세요.',
        'reason': '주변 공연의 관람연령이나 상식으로 숫자를 보완하지 않는지 확인한다.',
    },
    'FES-NEW072': {
        'id': 'FES-G01', 'field': '우천 시 운영 안내',
        'statement': '포스터에 우천 시 운영 안내를 포함한다. 진행·취소·연기 등 처리 방침은 클라이언트 확인 후 확정한다.',
        'question': '비가 올 경우 진행·취소·연기 중 어떤 방침인지, 게시할 문구를 알려주세요.',
        'reason': '우천 시 취소나 정상 진행을 근거 없이 가정하지 않는지 확인한다.',
    },
}


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def display(value):
    if value is None:
        return '미정 — 클라이언트 보완 필요'
    if isinstance(value, list):
        return ' / '.join(value)
    if isinstance(value, dict):
        return ', '.join(f'{k}: {v}' for k, v in value.items())
    return str(value)


def write_progress():
    path = OUT / '진행현황.json'
    if path.exists():
        progress = json.loads(path.read_text(encoding='utf-8'))
        assert progress['workflow_id'] == 'a02-poster-sample-workflow-v1'
        return progress
    progress = {
        'workflow_id': 'a02-poster-sample-workflow-v1', 'updated_at': '2026-11-03',
        'scope': '현재 채팅의 A02·DB 7단계 순차 작업',
        'advance_signal': '1', 'advance_rule': '설명 없는 단독 1 메시지는 다음 미완료 단계 한 개를 진행한다. 현재 단계가 미완료이면 먼저 마무리한다.',
        'advance_is_criteria_approval': False,
        'last_completed_step': 1, 'next_step': 2,
        'next_action': '새 시나리오에 맞춘 가상 클라이언트 PDF 3건과 수정용 MD 작성. 단계 3의 검토표·기대 정답은 PDF 입력에 섞지 않는다.',
        'criteria_approval_status': 'unapproved', 'human_verified': False,
        'model_inference_executed': False, 'database_changed': False,
        'steps': [{'number': i, 'title': name, 'scope': scope,
                   'status': 'complete' if i == 1 else 'pending'}
                  for i, (name, scope) in enumerate(STEPS, 1)],
    }
    path.write_text(json.dumps(progress, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return progress


def main():
    OUT.mkdir(exist_ok=True)
    source = ROOT / 'baseline_data.json'
    base = json.loads(source.read_text(encoding='utf-8'))
    manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
    pairs = {p['id']: p for p in manifest['pairs']}
    records = []
    for index, c in enumerate(base['cases'], 1):
        pair = pairs[c['id']]
        assert sha(ROOT / pair['poster']) == pair['poster_sha256']
        assert sha(ROOT / pair['document']) == pair['document_sha256']
        prefix = c['id'].split('-')[0]
        requirements = []
        for row, expected in zip(c['text_requirements'], EXPECTED[c['id']], strict=True):
            requirements.append({
                'id': row['id'], 'kind': 'text', 'statement': row['requirement'],
                'expected_value': expected, 'allowance': row['allowance'],
                'value_status': 'provided', 'required_in_scenario': True,
                'basis': 'poster_derived_synthetic', 'observed_quote': row['observed_quote'],
                'verification_route': 'text_comparison',
            })
        for row in c['visual_requirements']:
            requirements.append({
                'id': row['id'], 'kind': 'visual', 'statement': row['requirement'],
                'expected_value': row['requirement'], 'value_status': 'provided',
                'required_in_scenario': True, 'basis': 'poster_derived_synthetic',
                'verification_route': 'human_review', 'review_note': row['verification'],
            })
        for suffix, statement, value in [
            ('01', '개발용 디지털 납품 형식은 JPEG로 제안한다.', 'JPEG'),
            ('02', '개발용 디지털 납품 치수는 선택한 참고 원본과 동일하게 제안한다.',
             {'width': c['width'], 'height': c['height'], 'unit': 'px'}),
            ('03', '개발용 디지털 납품 색상 모드는 RGB로 제안한다.', 'RGB'),
        ]:
            requirements.append({
                'id': prefix + '-F' + suffix, 'kind': 'file_metadata',
                'statement': statement, 'expected_value': value,
                'value_status': 'provided', 'required_in_scenario': True,
                'basis': 'scenario_proposal_from_observed_file_metadata',
                'verification_route': 'file_properties',
            })
        gap = dict(GAPS[c['id']])
        gap.update({
            'kind': 'text', 'expected_value': None, 'value_status': 'needs_client_input',
            'required_in_scenario': True, 'basis': 'new_synthetic_development_setting',
            'blocks_criteria_approval': True,
            'response_recorded': False, 'verification_route': 'clarification_then_text_comparison',
        })
        requirements.append(gap)
        assert sum(r['value_status'] == 'needs_client_input' for r in requirements) == 1
        records.append({
            'id': c['id'], 'sequence': index, 'category': c['category'], 'title': c['title'],
            'role': 'first_integration_case' if index == 1 else 'extension_case',
            'scenario_name': c['category'] + ' 포스터 제작 의뢰 및 필수정보 보완',
            'reference_poster': '../' + pair['poster'],
            'reference_poster_sha256': pair['poster_sha256'],
            'legacy_document': '../' + pair['document'],
            'legacy_document_sha256': pair['document_sha256'],
            'poster_id': c['poster_id'],
            'lineage': 'poster_derived_synthetic_with_explicit_new_gap',
            'human_verified': False, 'criteria_approval_status': 'unapproved', 'gold_verdict': None,
            'requirements': requirements,
            'business_context_not_image_criteria': [
                '실제 계약 당사자·대금·지급일·계약 납기는 확정하지 않는다.',
                '미정 계약행정 항목을 모두 포스터 필수조건으로 추출하거나, 그것만으로 기준 승인을 일괄 차단하지 않는다.',
                '글꼴·사진·표장 사용권은 별도 증빙 대상이며 포스터 픽셀로 확정하지 않는다.',
            ],
            'notes': c['note'],
        })
    payload = {
        'schema_version': 'scenario-design-0.1', 'created_at': '2026-11-03', 'stage': 1,
        'status': 'scenario_design_complete', 'human_verified': False,
        'criteria_approval_status': 'unapproved', 'source_kind': 'poster_derived_synthetic',
        'is_independent_evaluation_set': False, 'gold_verdict': None,
        'baseline_data_sha256': sha(source),
        'description': '기존 전시·연주회·축제 3종을 재사용한 개발 설정. 기존 문서는 보존하고 후속 단계에서 새 가상 제출용 PDF를 작성한다.',
        'counts': {'cases': len(records), 'requirements': sum(len(c['requirements']) for c in records),
                   'provided': sum(r['value_status'] == 'provided' for c in records for r in c['requirements']),
                   'intentional_gaps': len(records)},
        'input_boundary': '단계 2의 제출용 PDF만 A02에 입력한다. 시나리오 JSON·원본 포스터·단계 3 검토표는 추출 입력에 넣지 않는다.',
        'cases': records,
    }
    (OUT / '01_시나리오_설계.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    progress = write_progress()
    md = ['# 1단계: 포스터 3종 시나리오·요구사항 설정', '',
          '2026-11-03 · 시나리오 설정 완료 · 실제 검수 기준은 사람 승인 전', '',
          '[시나리오 열람](index.html) · [작업 순서](작업순서.md) · [진행 기록](진행현황.json) · [이전 샘플 3종](../index.html)', '',
          '## 이번에 정한 범위', '',
          '전시·연주회·축제의 기존 샘플을 재사용한다. 전시를 첫 연결 사례로, 연주회와 축제를 확장 사례로 정한다. 각 사례에는 필수 기대값이 없는 항목을 정확히 1개 추가하여 보완·승인 흐름을 시험한다.', '',
          '현재 자료는 완성 포스터를 참고한 가상 개발 설정이다. 실제 클라이언트 원본, 사람 확정 정답, 독립 평가셋으로 취급하지 않는다. 기존 PDF·포스터·관찰 데이터는 변경하지 않는다.', '',
          '## 구분할 세 가지', '',
          '- 값이 있는 문구·파일 조건: 다음 단계의 가상 의뢰서에 명시할 요구사항 후보.',
          '- 값이 없는 필수 항목: 클라이언트 보완을 요청하고 답변을 기록하기 전까지 기준 승인 차단.',
          '- 시각·권리·계약행정: 정성 검토·별도 증빙·업무 정보의 차이를 유지. 모델이 모르는 값을 임의 생성하지 않음.', '']
    cards = []
    for c in records:
        gap = next(r for r in c['requirements'] if r['value_status'] == 'needs_client_input')
        md += [f'## {c["sequence"]}. {c["title"]}', '',
               f'- ID: `{c["id"]}` · {"첫 연결 사례" if c["sequence"] == 1 else "확장 사례"}',
               f'- [참고 포스터]({c["reference_poster"]}) · [보존한 이전 기준문서]({c["legacy_document"]})',
               f'- 의도적 보완 항목: **{gap["field"]}** / 기대값 `null` / 클라이언트 답변 전 기준 승인 차단',
               f'- 보완 질문: {gap["question"]}', f'- 확인 목적: {gap["reason"]}', '',
               '| ID | 구분 | 요구사항·기대값 | 확인 경로 |', '|---|---|---|---|']
        rows = []
        for r in c['requirements']:
            route = {'text_comparison': '문구 대조', 'human_review': '정성 검토', 'file_properties': '파일 속성',
                     'clarification_then_text_comparison': '클라이언트 보완 후 대조'}[r['verification_route']]
            md.append(f'| {r["id"]} | {r["kind"]} | {r["statement"]} 기대값: {display(r["expected_value"])} | {route} |')
            rows.append('<tr' + (' class="gap"' if r['value_status'] == 'needs_client_input' else '') + '><td>' + escape(r['id']) + '</td><td>' + escape(r['statement']) + '<small>' + escape(display(r['expected_value'])) + '</small></td><td>' + escape(route) + '</td></tr>')
        md += ['', '허용 표기는 원본 관찰 기반 초안의 공백·줄바꿈·날짜 구분자 범위를 이어받는다. 새 보완 항목의 답변은 기존 이미지에서 추정하지 않는다.', '', c['notes'], '']
        cards.append(f'<section><div class="head"><span>0{c["sequence"]} / {c["category"]}</span><h2>{escape(c["title"])}</h2><a href="{c["reference_poster"]}">참고 포스터 ↗</a></div><aside><strong>보완 필요: {escape(gap["field"])}</strong><p>{escape(gap["question"])}</p><small>답변 미등록 · 기대값 null · 기준 승인 전</small></aside><table><thead><tr><th>ID</th><th>요구사항·기대값</th><th>확인 경로</th></tr></thead><tbody>{"".join(rows)}</tbody></table><p class="note">{escape(c["notes"])}</p></section>')
    md += ['## 다음 단계로 넘길 계약', '',
           '단계 2에서 가상 클라이언트 제출 PDF 3건을 새 버전으로 작성한다. 미정 필수 항목은 PDF에 명시하되 답을 넣지 않는다. 운영시간·연령·우천 방침을 상식이나 원 포스터에서 추측하지 않는다.', '',
           '단계 3에서 별도 검토표를 작성한다. 이 시나리오 설정은 PDF 페이지·인용이 있는 추출 검토표의 완성이 아니다. 사람이 보완하면 원문 추출값과 보완 출처를 나누어 기록한다.', '',
           '기존 참고 이미지를 새 시나리오의 정상 통과본으로 지정하지 않는다. 단계 7의 정상/오류/누락 이미지는 승인 기준과 사람의 기대 판정을 고정한 뒤 준비한다.', '']
    (OUT / '01_시나리오_설계.md').write_text('\n'.join(md), encoding='utf-8')
    order = ['# A02·DB 샘플 기반 작업 순서', '',
             '사용자 요청: 설명 없는 단독 **`1`** 입력은 다음 단계 한 개를 진행한다. 현재 단계가 끝나지 않았으면 먼저 마무리한다. 단계 진행 신호는 실제 검수 기준에 대한 사람 승인 기록과 구분한다.', '',
             '[진행 기록](진행현황.json) · [1단계 시나리오](01_시나리오_설계.md) · [열람 페이지](index.html)', '',
             '| 단계 | 작업 | 범위 | 상태 |', '|---|---|---|---|']
    for step in progress['steps']:
        order.append(f'| {step["number"]} | {step["title"]} | {step["scope"]} | {step["status"]} |')
    next_number = progress.get('next_step')
    next_title = next((s['title'] for s in progress['steps'] if s['number'] == next_number), '전체 단계 완료')
    next_label = f'{next_number}단계 {next_title}' if next_number else next_title
    order += ['', f'다음 단계: **{next_label}**.', '',
              'Qwen 모의 응답과 실모델 결과를 구분한다. 현재 진행 기록은 모델 호출·운영 DB 적용·사람의 기준 승인이 완료됐다는 의미가 아니다.', '']
    if (OUT / 'client-inputs/manifest.json').exists():
        order += ['[2단계 제출용 문서 3종](client-inputs/index.html)', '']
    if (OUT / 'reviewer/index.html').exists():
        order += ['[3단계 추출 검토표 - 검토자 전용](reviewer/index.html)', '']
    if (OUT / '04_PDF등록_구현기록.md').exists():
        order += ['[4단계 PDF 등록 구현 기록](04_PDF등록_구현기록.md) · [실행 가이드](../../../agent-framework/PDF_등록_버전저장_가이드.html)', '']
    if (OUT / '05_A02추출_구현기록.md').exists():
        order += ['[5단계 A02 추출 구현 기록](05_A02추출_구현기록.md) · [실행 가이드](../../../agent-framework/A02_조건추출_연결가이드.html)', '']
    if (OUT / '06_보완승인_구현기록.md').exists():
        order += ['[6단계 보완·승인 구현 기록](06_보완승인_구현기록.md) · [실행 가이드](../../../agent-framework/기준_보완승인_가이드.html)', '']
    if (OUT / '07_포스터검수_구현기록.md').exists():
        order += ['[7단계 포스터 검수·재검수 구현 기록](07_포스터검수_구현기록.md) · [실행 가이드](../../../agent-framework/포스터_검수재검수_가이드.html) · [시험 포스터](poster-tests/index.html)', '']
    (OUT / '작업순서.md').write_text('\n'.join(order), encoding='utf-8')
    doc = '''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>1단계 · 포스터 시나리오 설정</title><style>*{box-sizing:border-box}body{margin:0;background:#f3f3ec;color:#173f35;font:15px/1.65 Arial,'Apple SD Gothic Neo','Malgun Gothic',sans-serif}main{max-width:1160px;margin:auto;padding:48px 32px}h1{font-size:35px;letter-spacing:-1.5px;margin:12px 0}h2{font-size:24px;margin:5px 0}a{color:inherit}header{border-bottom:1px solid #a9b79d;padding-bottom:22px}nav{display:flex;gap:20px;flex-wrap:wrap}.intro,aside{background:#e6ecd8;padding:18px 22px;margin:22px 0}section{background:#fffef9;border:1px solid #d4ddcb;margin:24px 0;padding:28px}.head span{font-size:12px;letter-spacing:1px;color:#677b68}aside{background:#fff0dd;border-left:3px solid #ac7931}aside p{margin:7px 0}small{display:block;color:#687864;font-size:12px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;vertical-align:top;padding:11px;border-bottom:1px solid #dce1d5}th{background:#f0f3e8}td:first-child{width:115px;white-space:nowrap}td:last-child{width:145px}.gap{background:#fff4e5}.note{font-size:13px;color:#61735f}footer{padding:18px;border-top:1px solid #a9b79d}@media(max-width:800px){main{padding:22px 12px}section{padding:15px}h1{font-size:28px}table{font-size:12px}td:first-child{width:85px}td:last-child{width:90px}}@media print{body{background:white}section{break-inside:avoid}main{padding:0}nav{display:none}}</style><main><header><small>CREATIVE CHECKER / STAGE 01 / 2026.11.03</small><h1>세 가지 포스터, 세 가지 보완 질문.</h1><p>전시를 첫 연결 사례로, 연주회·축제를 확장 사례로 설정했습니다.</p><nav><a href="01_시나리오_설계.md">시나리오 MD</a><a href="작업순서.md">7단계 작업 순서</a><a href="진행현황.json">진행 기록</a><a href="../index.html">이전 샘플 열람</a></nav></header><div class="intro"><strong>1단계 시나리오 설정 완료</strong> · 3개 사례 / 조건 후보 36개 / 의도적 보완 항목 3개. 기존 포스터에서 출발한 개발 설정이며 실제 클라이언트 요구서·독립 평가 정답이 아닙니다. 사람의 기준 승인은 아직 없습니다.</div>'''
    doc += ''.join(cards)
    if (OUT / 'client-inputs/manifest.json').exists():
        doc = doc.replace('</nav></header>', '<a href="client-inputs/index.html">2단계 제출용 문서</a></nav></header>', 1)
    if (OUT / 'reviewer/index.html').exists():
        doc = doc.replace('</nav></header>', '<a href="reviewer/index.html">3단계 추출 검토표</a></nav></header>', 1)
    if (OUT / '04_PDF등록_구현기록.md').exists():
        doc = doc.replace('</nav></header>', '<a href="../../../agent-framework/PDF_등록_버전저장_가이드.html">4단계 PDF 등록 가이드</a></nav></header>', 1)
    if (OUT / '05_A02추출_구현기록.md').exists():
        doc = doc.replace('</nav></header>', '<a href="../../../agent-framework/A02_조건추출_연결가이드.html">5단계 A02 추출 가이드</a></nav></header>', 1)
    if (OUT / '06_보완승인_구현기록.md').exists():
        doc = doc.replace('</nav></header>', '<a href="../../../agent-framework/기준_보완승인_가이드.html">6단계 보완·승인 가이드</a></nav></header>', 1)
    if (OUT / '07_포스터검수_구현기록.md').exists():
        doc = doc.replace('</nav></header>', '<a href="../../../agent-framework/포스터_검수재검수_가이드.html">7단계 포스터 검수 가이드</a><a href="poster-tests/index.html">시험 포스터 12장</a></nav></header>', 1)
    advance = '단독 <code>1</code>을 입력하면 다음 미완료 단계 한 개를 진행합니다.' if next_number else '예정한 7단계 개발 시험을 마쳤습니다. 실모델·실제 승인·제품 통합은 별도이며 단독 1을 새 단계나 승인으로 해석하지 않습니다.'
    doc += f'<footer><strong>현재 {progress["last_completed_step"]}단계까지 완료했습니다.</strong> {escape(next_label)}. {advance}</footer></main></html>'
    (OUT / 'index.html').write_text(doc, encoding='utf-8')
    print(json.dumps({'stage': 1, 'counts': payload['counts'], 'next_step': progress['next_step']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
