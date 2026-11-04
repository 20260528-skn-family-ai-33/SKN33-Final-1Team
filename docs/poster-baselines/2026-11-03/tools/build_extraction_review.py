"""Stage 3: draft review references with spans from the actual submitted PDFs.

Reads immutable source PDFs. Never writes to client-inputs, calls a model,
updates a database, supplies missing client answers or records human approval.
This artifact format is for review, not the framework runtime contract.
"""
from __future__ import annotations

import hashlib
from bisect import bisect_left
from html import escape
import importlib.metadata
import json
from pathlib import Path

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / 'workflow'
INPUT = WORKFLOW / 'client-inputs'
OUT = WORKFLOW / 'reviewer'

ALLOWANCES = {
    'CON-T06': '줄바꿈·공백 차이는 허용한다. 이 항목의 필수 표기는 한글 프로그램명과 작곡가명이며, 영문 부제 전체와 작품 번호는 이번 요청 범위에 포함하지 않는다.',
    'FES-T08': '안내 취지를 유지한 문장 표현은 담당자 확인 후 사용할 수 있다.',
}
REPLY_POLICY = '위 필수 사항의 회신을 받은 뒤 표기 내용을 확정합니다. 확인되지 않은 값은 임의로 정하지 말고 담당자에게 문의해 주세요.'
QUALITATIVE = '색감·구성·가독성은 시안에서 함께 확인합니다. 특정 색상 코드나 정량 점수를 지정한 조건은 아닙니다.'
ADMIN = '행사에 표시할 정보와 계약상 납품기한은 구분합니다. 실제 계약 당사자·대금·지급 일정·수정 횟수는 별도 협의할 업무 정보이며 포스터 표시 요청에 포함하지 않습니다.'
RIGHTS = '사진·인물·기관 표장·글꼴 등의 사용권과 편집 원본·인쇄물 납품 범위는 별도 자료로 확인합니다. 이 문서는 권리 보유나 사용 허락을 확정한 문서가 아닙니다.'


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha_file(path):
    return sha_bytes(path.read_bytes())


def norm(text):
    return ''.join(c for c in text if not c.isspace())


def normalized_map(text):
    kept = [(i, c) for i, c in enumerate(text) if not c.isspace()]
    return ''.join(c for _, c in kept), [i for i, _ in kept]


def source_span(text, needle, page_number, *, start_after=0, end_before=None):
    cleaned, positions = normalized_map(text)
    query = norm(needle)
    lower = bisect_left(positions, start_after)
    upper = len(cleaned) if end_before is None else bisect_left(positions, end_before)
    found = cleaned.find(query, lower, upper)
    if not query or found < 0:
        raise ValueError(f'Quote not found on page {page_number}: {needle}')
    if cleaned.find(query, found + 1, upper) >= 0:
        raise ValueError(f'Ambiguous quote on page {page_number}: {needle}')
    start, end = positions[found], positions[found + len(query) - 1] + 1
    quote = text[start:end]
    assert norm(quote) == query
    return {
        'page': page_number, 'quote': quote,
        'quote_sha256': sha_bytes(quote.encode('utf-8')),
        'start': start, 'end': end, 'offset_unit': 'unicode_codepoints',
        'page_text_sha256': sha_bytes(text.encode('utf-8')),
    }


def source_range(text, start_needle, end_needle, page_number):
    first = source_span(text, start_needle, page_number)
    last = source_span(text, end_needle, page_number)
    assert first['end'] <= last['start']
    quote = text[first['start']:last['start']].rstrip()
    return source_span(text, quote, page_number)


def value_text(value):
    if value is None:
        return '미정 (null)'
    if isinstance(value, list):
        return ' / '.join(value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def anchor_for_file(req):
    key = req['id'].rsplit('-F', 1)[-1]
    if key == '01':
        return 'JPEG (.jpg 또는 .jpeg)'
    if key == '02':
        value = req['expected_value']
        return f'가로 {value["width"]}픽셀 × 세로 {value["height"]}픽셀'
    return 'RGB'


def semantic(req):
    if req['value_status'] == 'needs_client_input':
        return 'missing_required_value'
    if req['kind'] == 'visual':
        return 'qualitative_description'
    if req['kind'] == 'file_metadata':
        return 'file_dimensions' if isinstance(req['expected_value'], dict) else 'file_property'
    value = req['expected_value']
    if isinstance(value, dict):
        return 'named_roles' if '지휘' in value else 'date_time_fields'
    return 'all_listed_parts_required' if isinstance(value, list) else 'text_meaning_with_stated_allowances'


def build():
    OUT.mkdir(exist_ok=True)
    design_path = WORKFLOW / '01_시나리오_설계.json'
    manifest_path = INPUT / 'manifest.json'
    design = json.loads(design_path.read_text(encoding='utf-8'))
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    assert sha_file(design_path) == manifest['source_scenario_sha256']
    cases, source_docs = [], []
    for case, document in zip(design['cases'], manifest['documents'], strict=True):
        assert case['id'] == document['case_id']
        path = INPUT / document['pdf']
        assert sha_file(path) == document['pdf_sha256']
        reader = PdfReader(path)
        assert len(reader.pages) == document['page_count'] == 2
        pages = [p.extract_text() or '' for p in reader.pages]
        relative = '../client-inputs/' + document['pdf']
        source_docs.append({
            'case_id': case['id'], 'document': relative,
            'document_version': document['document_version'], 'pdf_sha256': document['pdf_sha256'],
            'pages': [{'page': i, 'text': text, 'text_sha256': sha_bytes(text.encode('utf-8'))}
                      for i, text in enumerate(pages, 1)],
        })
        rows = []
        text_requirements = [r for r in case['requirements']
                             if r['kind'] == 'text' and r['value_status'] == 'provided']
        for req in case['requirements']:
            unresolved = req['value_status'] == 'needs_client_input'
            if unresolved:
                span = source_range(pages[1], req['field'] + ' - 확인 중', REPLY_POLICY, 2)
            elif req['kind'] == 'file_metadata':
                span = source_span(pages[1], anchor_for_file(req), 2)
            else:
                page = 1 if req['kind'] == 'text' else 2
                span = source_span(pages[page - 1], req['statement'], page)
            row = {
                'reference_id': req['id'], 'kind': req['kind'],
                'requirement': req['statement'], 'expected_value': req['expected_value'],
                'value_semantics': semantic(req), 'value_status': req['value_status'],
                'required': req['required_in_scenario'],
                'verification_route': req['verification_route'],
                'source': span, 'supporting_sources': [],
                'reference_status': 'draft_for_human_review', 'human_verified': False,
                'human_reviewer': None, 'human_reviewed_at': None,
                'poster_verdict': None,
            }
            if req['kind'] == 'text' and not unresolved:
                allowance = ALLOWANCES.get(req['id'], req['allowance'])
                row['allowance'] = allowance
                position = next(i for i, r in enumerate(text_requirements) if r['id'] == req['id'])
                boundary = (source_span(pages[0], text_requirements[position + 1]['statement'], 1)['start']
                            if position + 1 < len(text_requirements) else len(pages[0]))
                row['supporting_sources'].append(source_span(
                    pages[0], '허용 범위: ' + allowance, 1,
                    start_after=span['end'], end_before=boundary,
                ))
                row['review_note'] = '조건의 의미·기대값과 명시된 허용 범위가 보존되는지 확인한다.'
            elif req['kind'] == 'visual':
                row['supporting_sources'].append(source_span(pages[1], QUALITATIVE, 2))
                row['review_note'] = '정성 조건으로 유지한다. 문서가 지정하지 않은 RGB 코드·색차 임계값·확정 통과 판정을 생성하지 않는다.'
            elif unresolved:
                row['supporting_sources'].append(source_span(pages[1], REPLY_POLICY, 2))
                row.update({
                    'field': req['field'], 'clarification_question_draft': req['question'],
                    'client_response': None, 'blocks_criteria_approval': True,
                    'review_note': '미정 필수 사항을 경고·질문 또는 미확정 조건으로 남긴다. 값을 추정하거나 빈 문자열의 자동 통과로 바꾸지 않는다.',
                })
            else:
                row['review_note'] = '문서에 적힌 파일 속성·픽셀 치수를 기준으로 확인한다. 이미지 문자 판독과 파일 메타데이터 검사를 구분한다.'
            rows.append(row)
        exclusions = [
            {'topic': '문서 작성일', 'expected_treatment': '문서 메타데이터로 보관. 행사일·공연일의 기대값으로 사용하지 않는다.',
             'source': source_span(pages[0], '2026-11-03', 1)},
            {'topic': '문서 식별·버전', 'expected_treatment': '문서 메타데이터로 보관. 포스터에 인쇄할 필수 문구로 만들지 않는다.',
             'source': source_span(pages[0], case['id'] + ' / client-draft-v0.2', 1)},
            {'topic': '계약행정·납기', 'expected_treatment': '별도 업무 정보. 금액·지급일 등을 포스터 이미지의 필수 표시 조건으로 강제하지 않는다.',
             'source': source_span(pages[1], ADMIN, 2)},
            {'topic': '권리·원본·인쇄물 증빙', 'expected_treatment': '별도 증빙 확인 사항으로 보존한다. 포스터 픽셀만으로 권리 보유나 증빙 충족을 확정하지 않는다.',
             'source': source_span(pages[1], RIGHTS, 2)},
        ]
        if case['id'] == 'CON-NEW043':
            exclusions.append({
                'topic': '영문 부제 전체·작품 번호',
                'expected_treatment': '현재 필수 표기 범위 밖. 한글 프로그램·작곡가 조건 및 관람연령 미정과 구분한다.',
                'source': source_span(pages[0], '허용 범위: ' + ALLOWANCES['CON-T06'], 1),
            })
        cases.append({
            'case_id': case['id'], 'title': case['title'],
            'document': relative, 'document_version': document['document_version'],
            'pdf_sha256': document['pdf_sha256'], 'human_verified': False,
            'criteria_approval_status': 'unapproved', 'gold_verdict': None,
            'reference_rows': rows, 'scope_notes': exclusions,
        })
    counts = {
        'documents': len(cases), 'source_pages': sum(len(c['pages']) for c in source_docs),
        'reference_rows': sum(len(c['reference_rows']) for c in cases),
        'provided_values': sum(r['value_status'] == 'provided' for c in cases for r in c['reference_rows']),
        'required_unresolved': sum(r['value_status'] == 'needs_client_input' for c in cases for r in c['reference_rows']),
        'scope_notes': sum(len(c['scope_notes']) for c in cases),
    }
    assert counts['reference_rows'] == 36 and counts['provided_values'] == 33 and counts['required_unresolved'] == 3
    output = {
        'schema_version': 'extraction-review-reference-0.1', 'stage': 3, 'created_at': '2026-11-03',
        'artifact_role': 'reviewer_only_reference_draft', 'is_runtime_contract': False,
        'reference_status': 'draft_for_human_review', 'human_verified': False,
        'source_kind': manifest['source_kind'], 'is_independent_evaluation_set': False,
        'criteria_approval_status': 'unapproved', 'gold_verdict': None,
        'a02_inference_executed': False, 'database_changed': False,
        'client_manifest_sha256': sha_file(manifest_path), 'scenario_sha256': sha_file(design_path),
        'text_extraction': {'tool': 'pypdf', 'version': importlib.metadata.version('pypdf'),
                            'quote_rule': 'quote is an exact raw extracted-page substring; search ignores whitespace only; hashes and offsets use the raw text'},
        'input_boundary': 'Do not pass reviewer references, page-text copies, scenarios or original posters to A02. Use only the submitted client PDF as document input.',
        'review_policy': [
            '36개 행은 검토용 기준점이다. 모델의 기준 ID·행 개수를 동일하게 강제하지 않는다. 합치기·나누기가 있어도 의미·기대값·출처의 포함 여부를 검토한다.',
            '모든 필수 조건·허용 범위와 페이지·인용의 정합성을 확인한다. 순수 JSON 형식 성공과 의미 정확도는 별개다.',
            '관람시간·관람연령·우천 시 운영 안내의 답변은 없다. 값을 추정하거나 미정 항목을 조용히 누락하지 않는다.',
            '문서 작성일·업무 메타데이터·권리 증빙을 포스터 자동 통과 조건과 혼동하지 않는다.',
            'AI가 작성한 검토표 초안이다. 사람의 정답 검토·기준 승인·모델 성능 평가를 수행한 것으로 보고하지 않는다.',
        ],
        'counts': counts, 'cases': cases,
    }
    (OUT / 'source-pages.json').write_text(json.dumps({
        'artifact_role': 'reviewer_only_pdf_text_copy',
        'text_extraction': output['text_extraction'], 'documents': source_docs,
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (OUT / '03_추출_검토표.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    write_documents(output)
    print(json.dumps({'stage': 3, 'counts': counts, 'human_verified': False, 'a02_inference_executed': False}, ensure_ascii=False))


def write_documents(data):
    md = ['# 3단계: A02 추출 결과 검토표 초안', '',
          '2026-11-03 · 검토자용 자료 · 사람 검토·기준 승인 전 · 모델 실행 전', '',
          '[열람 페이지](index.html) · [구조화 검토표](03_추출_검토표.json) · [페이지 텍스트 사본](source-pages.json) · [제출용 PDF](../client-inputs/index.html)', '',
          '## 사용 기준', '',
          '제출 PDF 3건·6쪽을 읽어 조건 후보 36개와 범위 확인 13개를 정리했다. 기존 시나리오 값은 기대값 초안의 참고이며, 페이지·인용은 실제 제출 PDF 텍스트에서 다시 연결했다.', '',
          '모든 기대값은 사람 확인 전이다. 시각 조건의 존재, 미정 필수 조건, 자동 판정 가능 여부를 구분한다. 이 파일과 JSON·페이지 사본은 A02 입력에 넣지 않는다.', '']
    md += ['- ' + policy for policy in data['review_policy']]
    sections = []
    for c in data['cases']:
        md += ['', '## ' + c['title'], '',
               f'- ID: `{c["case_id"]}` / 문서 버전: `{c["document_version"]}`',
               f'- [제출 PDF]({c["document"]}) / SHA-256: `{c["pdf_sha256"]}`', '',
               '| 검토 ID | 조건·기대값 | 상태·확인 방법 | PDF 원문 근거 |', '|---|---|---|---|']
        rows = []
        for r in c['reference_rows']:
            pending = r['value_status'] == 'needs_client_input'
            state = '필수 보완 필요' if pending else ('정성 검토' if r['kind'] == 'visual' else '기대값 제시')
            source = r['source'];quote = source['quote']
            proof = f'{source["page"]}쪽: “{quote.replace(chr(10), " ")}”'
            md.append(f'| {r["reference_id"]} | {r["requirement"]}<br>기대값: {value_text(r["expected_value"])} | {state} / 사람 미검토 | {proof} |')
            allowance = ''
            if r.get('allowance'):
                allowance = '<p class="small">허용 범위: ' + escape(r['allowance']) + '</p>'
                support = r['supporting_sources'][0]
                md.append(f'| ↳ 허용 범위 | {r["allowance"]} | 원문 확인 | {support["page"]}쪽: “{support["quote"].replace(chr(10), " ")}” |')
            if pending:
                md += [f'| ↳ 보완 질문 | {r["clarification_question_draft"]} | 답변 없음 / 승인 차단 | 2쪽 회신 후 확정 문구 참고 |']
            rows.append('<tr' + (' class="pending"' if pending else '') + '><td><strong>' + escape(r['reference_id']) + '</strong><small>' + state + ' · 사람 미검토</small></td><td>' + escape(r['requirement']) + '<pre>' + escape(value_text(r['expected_value'])) + '</pre>' + allowance + '<p class="small">' + escape(r['review_note']) + '</p></td><td><a href="' + c['document'] + '#page=' + str(source['page']) + '">' + str(source['page']) + '쪽 열기 ↗</a><blockquote>' + escape(quote) + '</blockquote><details><summary>인용 검증 정보</summary><small>인용 SHA-256: ' + escape(source['quote_sha256']) + '<br>문자 범위: ' + str(source['start']) + ':' + str(source['end']) + '</small></details></td></tr>')
        md += ['', '### 자동 포스터 판정과 구분할 정보', '', '| 정보 | 처리 기준 | 원문 |', '|---|---|---|']
        scope_rows = []
        for note in c['scope_notes']:
            source = note['source'];quote = source['quote'].replace('\n', ' ')
            md.append(f'| {note["topic"]} | {note["expected_treatment"]} | {source["page"]}쪽: “{quote}” |')
            scope_rows.append('<li><strong>' + escape(note['topic']) + '</strong>: ' + escape(note['expected_treatment']) + '<blockquote>' + escape(source['quote']) + '</blockquote></li>')
        sections.append('<section><header><small>' + escape(c['case_id']) + ' / ' + escape(c['document_version']) + '</small><h2>' + escape(c['title']) + '</h2><a href="' + c['document'] + '">제출 PDF ↗</a></header><table><thead><tr><th>검토 항목</th><th>기대값·확인 기준</th><th>실제 PDF 인용</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table><details class="scope"><summary>업무·메타데이터·증빙의 범위 확인</summary><ul>' + ''.join(scope_rows) + '</ul></details></section>')
    md += ['', '## 검토 결과를 기록할 때', '',
           '기준 ID를 그대로 맞추는 대신 조건의 누락·임의 생성·값 변경·출처 오류·보완 필요 표시를 확인한다. 현재 행은 모두 사람 미검토이며 `poster_verdict=null`이다. 추후 사람이 검토하면 검토자·시각·문서 해시를 함께 기록한다.', '',
           'PDF가 변경되면 인용·페이지·해시를 다시 검증한다. 원본 PDF의 줄바꿈이 포함된 정확한 인용, 페이지 텍스트 SHA-256, 문자 범위는 JSON에 보관했다. JSON의 이 인용은 페이지 텍스트의 실제 부분 문자열이다.', '']
    (OUT / '03_추출_검토표.md').write_text('\n'.join(md), encoding='utf-8')
    head = '''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>3단계 · 추출 결과 검토표</title><style>*{box-sizing:border-box}body{margin:0;background:#f3f3ec;color:#173f35;font:15px/1.65 Arial,'Apple SD Gothic Neo','Malgun Gothic',sans-serif}main{max-width:1450px;margin:auto;padding:45px 30px}h1{font-size:34px;letter-spacing:-1.3px}h2{font-size:23px;line-height:1.4}a{color:inherit}nav{display:flex;gap:18px;flex-wrap:wrap}.intro{padding:20px;background:#e5ecd7;margin:22px 0}section{background:#fffef8;border:1px solid #d7dfca;padding:25px;margin:25px 0}header{margin-bottom:18px}small,.small{display:block;font-size:12px;color:#62775e}table{width:100%;border-collapse:collapse;font-size:13px;table-layout:fixed}td,th{padding:12px;vertical-align:top;text-align:left;border-bottom:1px solid #d8dfce;overflow-wrap:anywhere}th{background:#edf1e5}th:first-child{width:17%}th:nth-child(2){width:45%}th:last-child{width:38%}.pending{background:#fff0da}pre{font-family:inherit;white-space:pre-wrap;background:#f0f4e7;padding:9px;margin:9px 0;overflow-wrap:anywhere}blockquote{white-space:pre-wrap;border-left:2px solid #b4c2a0;padding:7px 10px;margin:10px 0;color:#40563b;background:#f6f7f0}details{font-size:12px}summary{cursor:pointer}.scope{margin-top:20px;padding:16px;background:#f0f3e8}footer{padding:20px;border-top:1px solid #b1bea2}@media(max-width:900px){main{padding:22px 12px}section{padding:12px}h1{font-size:27px}table{font-size:12px}td,th{padding:7px}}@media print{main{padding:0}body{background:white}nav{display:none}tr{break-inside:avoid}}</style><main><header><small>CREATIVE CHECKER / REVIEWER ONLY / STAGE 03</small><h1>원문과 대조하는 추출 검토표.</h1><p>PDF 3건 · 6쪽 · 조건 후보 36개 · 필수 보완 항목 3개</p><nav><a href="03_추출_검토표.md">검토표 MD</a><a href="03_추출_검토표.json">검토표 JSON</a><a href="README.md">검토 방법</a><a href="../client-inputs/index.html">제출용 PDF</a><a href="../진행현황.json">최신 진행 기록</a></nav></header><div class="intro"><strong>검토자용 초안입니다.</strong> 사람의 정답 검토·기준 승인·모델 추출은 아직 없습니다. 이 표와 JSON·페이지 텍스트 사본을 A02 입력에 넣지 않습니다. 모델이 조건을 합치거나 나누더라도 의미·기대값·근거의 포함 여부로 검토합니다.</div>'''
    tail = '<footer>검토표의 인용은 실제 제출 PDF의 페이지 텍스트에서 연결했습니다. 각 행은 사람 확인 전이며 미정 필수 값은 null입니다. 포스터 판정을 미리 통과로 지정하지 않았습니다.</footer></main></html>'
    html = head + ''.join(sections) + tail
    (OUT / 'index.html').write_text('\n'.join(line.rstrip() for line in html.splitlines()), encoding='utf-8')


if __name__ == '__main__':
    build()
