"""Stage 2: author three text-only fictional client briefs from stage 1.

No reference poster, reviewer answer key or model/database operation is included.
Progress is advanced separately, after document rendering and verification.
"""
from __future__ import annotations

import argparse
from html import escape
from pathlib import Path
import hashlib
import json

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, PageBreak, Spacer, Table, TableStyle

from build import p, heading, table, GREEN, MUTED, WIDTH


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / 'workflow'
OUT = WORKFLOW / 'client-inputs'
SCENARIOS = WORKFLOW / '01_시나리오_설계.json'
DATE = '2026-11-03'
VERSION = '0.2'

GAP_TEXT = {
    'EXH-NEW004': '관람시간은 확인 중입니다. 관람 가능한 요일·시작 시각·종료 시각과 휴관일 안내가 필요합니다. 구체적인 내용은 담당자 회신 후 반영해 주세요.',
    'CON-NEW043': '관람연령은 확인 중입니다. 최소 관람연령을 확인한 뒤 포스터에 안내해야 합니다. 구체적인 연령과 표기 문구는 담당자 회신 후 반영해 주세요.',
    'FES-NEW072': '우천 시 운영 안내는 확인 중입니다. 비가 오는 경우의 운영 방침을 포스터에 안내해야 합니다. 방침과 표기 문구는 담당자 회신 후 반영해 주세요.',
}

INTRO = {
    'EXH-NEW004': '전시의 주제와 개막일, 관람 장소가 명확하게 전달되는 디지털 홍보 포스터를 의뢰합니다. 옛 극장과 영화 자료의 분위기를 살리되 제목과 안내 정보를 구분해 주세요.',
    'CON-NEW043': '교향악단 정기연주회의 작품명, 공연 일시·장소와 출연 정보를 알리는 디지털 홍보 포스터를 의뢰합니다. 한글 정보와 외국어 작품명을 함께 읽을 수 있도록 구성해 주세요.',
    'FES-NEW072': '탄소중립 행사와 체험·공연 프로그램을 알리는 디지털 홍보 포스터를 의뢰합니다. 주요 제목, 행사 시간·장소, 프로그램과 교통 안내가 구분되도록 구성해 주세요.',
}

CLIENT_ALLOWANCES = {
    'CON-T06': '줄바꿈·공백 차이는 허용한다. 이 항목의 필수 표기는 한글 프로그램명과 작곡가명이며, 영문 부제 전체와 작품 번호는 이번 요청 범위에 포함하지 않는다.',
    'FES-T08': '안내 취지를 유지한 문장 표현은 담당자 확인 후 사용할 수 있다.',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def footer(canvas, doc):
    canvas.setStrokeColor(GREEN)
    canvas.line(46, A4[1] - 37, A4[0] - 46, A4[1] - 37)
    canvas.setFont('Korean', 7.4)
    canvas.setFillColor(MUTED)
    canvas.drawString(46, 28, '가상 클라이언트 제작 의뢰서 v0.2 / 개발용 / 실제 발주 아님')
    canvas.drawRightString(A4[0] - 46, 28, f'{DATE}  |  {doc.page} / 2')


def title(label, subtitle):
    return [p('POSTER DESIGN BRIEF / FICTIONAL CLIENT', 8, 11, MUTED),
            p(label, 22, 29, GREEN), p(subtitle, 10, 15, MUTED), Spacer(1, 9)]


def file_values(case):
    values = {r['id'].rsplit('-F', 1)[-1]: r['expected_value']
              for r in case['requirements'] if r['kind'] == 'file_metadata'}
    dims = values['02']
    return values['01'], dims['width'], dims['height'], values['03']


def write_pdf(case, path):
    text_rows = [r for r in case['requirements'] if r['kind'] == 'text' and r['value_status'] == 'provided']
    visual_rows = [r for r in case['requirements'] if r['kind'] == 'visual']
    gap = next(r for r in case['requirements'] if r['value_status'] == 'needs_client_input')
    fmt, width, height, mode = file_values(case)
    story = title(case['category'] + ' 포스터 제작 의뢰서', case['title'])
    story += [p('기존 포스터를 참고하여 작성한 개발용 가상 의뢰서입니다. 실제 기관이나 인물의 발주·승인 사실을 나타내지 않습니다.', 8.6, 13, MUTED)]
    story += [table([
        ['의뢰 개요', '내용'],
        ['문서 식별', case['id'] + ' / client-draft-v' + VERSION],
        ['과업 분야', '시각디자인 / 디지털 홍보 포스터'],
        ['결과물', '아래 요구사항을 반영한 디지털 포스터 파일 1점'],
    ])]
    story += heading('1. 제작 목적')
    story += [p(INTRO[case['id']])]
    story += heading('2. 포스터에 표기할 내용')
    rows = [['항목', '표기 내용과 허용 범위']]
    for n, r in enumerate(text_rows, 1):
        allowance = CLIENT_ALLOWANCES.get(r['id'], r['allowance'])
        rows.append([str(n), r['statement'] + '\n허용 범위: ' + allowance])
    story += [table(rows, [42, WIDTH - 42])]
    story += [PageBreak()]
    story += title('시각 구성·납품·확인 사항', case['id'] + ' / 제작 의뢰 세부내역')
    story += heading('3. 시각 구성')
    for n, r in enumerate(visual_rows, 1):
        story += [p(f'{n}. {r["statement"]}')]
    story += [p('색감·구성·가독성은 시안에서 함께 확인합니다. 특정 색상 코드나 정량 점수를 지정한 조건은 아닙니다.', 8.7, 13, MUTED)]
    story += heading('4. 디지털 납품 규격')
    story += [table([
        ['항목', '요청 규격'],
        ['파일 형식', fmt + ' (.jpg 또는 .jpeg)'],
        ['픽셀 크기', f'가로 {width}픽셀 × 세로 {height}픽셀'],
        ['색상 모드', mode],
    ])]
    story += heading('5. 담당자 확인 후 반영할 필수 사항')
    gap_box = Table([[p(gap['field'] + ' - 확인 중', 11, 17, colors.HexColor('#875b1e'))],
                     [p(GAP_TEXT[case['id']])]], colWidths=[WIDTH], hAlign='LEFT')
    gap_box.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fff1dc')),
        ('BOX', (0, 0), (-1, -1), .6, colors.HexColor('#d8bc8a')),
        ('LEFTPADDING', (0, 0), (-1, -1), 13), ('RIGHTPADDING', (0, 0), (-1, -1), 13),
        ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story += [gap_box, Spacer(1, 8), p('위 필수 사항의 회신을 받은 뒤 표기 내용을 확정합니다. 확인되지 않은 값은 임의로 정하지 말고 담당자에게 문의해 주세요.')]
    story += heading('6. 협의 및 확인 범위')
    story += [p('행사에 표시할 정보와 계약상 납품기한은 구분합니다. 실제 계약 당사자·대금·지급 일정·수정 횟수는 별도 협의할 업무 정보이며 포스터 표시 요청에 포함하지 않습니다.'),
              p('사진·인물·기관 표장·글꼴 등의 사용권과 편집 원본·인쇄물 납품 범위는 별도 자료로 확인합니다. 이 문서는 권리 보유나 사용 허락을 확정한 문서가 아닙니다.'),
              p('변경 내용은 새 문서 버전으로 기록하고 담당자와 표기 기준을 확인합니다. 이전 초안의 확인 기록을 변경된 기준의 승인으로 사용하지 않습니다.')]
    pdf = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=46, rightMargin=46,
                            topMargin=51, bottomMargin=49, invariant=1,
                            title=case['title'] + ' - 가상 제작 의뢰서 v0.2',
                            author='Creative Checker fictional client document')
    pdf.build(story, onFirstPage=footer, onLaterPages=footer)


def write_md(case, path):
    fmt, width, height, mode = file_values(case)
    lines = ['# ' + case['category'] + ' 포스터 제작 의뢰서', '', case['title'], '',
             '기존 포스터를 참고하여 작성한 개발용 가상 의뢰서입니다. 실제 기관이나 인물의 발주·승인 사실을 나타내지 않습니다.', '',
             '- 문서 식별: ' + case['id'] + ' / client-draft-v' + VERSION,
             '- 과업 분야: 시각디자인 / 디지털 홍보 포스터',
             '- 결과물: 아래 요구사항을 반영한 디지털 포스터 파일 1점', '',
             '## 1. 제작 목적', '', INTRO[case['id']], '', '## 2. 포스터에 표기할 내용', '']
    for r in case['requirements']:
        if r['kind'] == 'text' and r['value_status'] == 'provided':
            allowance = CLIENT_ALLOWANCES.get(r['id'], r['allowance'])
            lines += ['- ' + r['statement'], '  - 허용 범위: ' + allowance]
    lines += ['', '## 3. 시각 구성', '']
    lines += ['- ' + r['statement'] for r in case['requirements'] if r['kind'] == 'visual']
    lines += ['', '색감·구성·가독성은 시안에서 함께 확인합니다. 특정 색상 코드나 정량 점수를 지정한 조건은 아닙니다.', '',
              '## 4. 디지털 납품 규격', '', f'- 파일 형식: {fmt} (.jpg 또는 .jpeg)',
              f'- 픽셀 크기: 가로 {width}픽셀 × 세로 {height}픽셀', '- 색상 모드: ' + mode, '',
              '## 5. 담당자 확인 후 반영할 필수 사항', '', GAP_TEXT[case['id']], '',
              '위 필수 사항의 회신을 받은 뒤 표기 내용을 확정합니다. 확인되지 않은 값은 임의로 정하지 말고 담당자에게 문의해 주세요.', '',
              '## 6. 협의 및 확인 범위', '',
              '행사에 표시할 정보와 계약상 납품기한은 구분합니다. 실제 계약 당사자·대금·지급 일정·수정 횟수는 별도 협의할 업무 정보이며 포스터 표시 요청에 포함하지 않습니다.', '',
              '사진·인물·기관 표장·글꼴 등의 사용권과 편집 원본·인쇄물 납품 범위는 별도 자료로 확인합니다. 이 문서는 권리 보유나 사용 허락을 확정한 문서가 아닙니다.', '',
              '변경 내용은 새 문서 버전으로 기록하고 담당자와 표기 기준을 확인합니다. 이전 초안의 확인 기록을 변경된 기준의 승인으로 사용하지 않습니다.', '']
    path.write_text('\n'.join(lines), encoding='utf-8')


def write_index(cases):
    cards = []
    for c in cases:
        cards.append(f'<article><small>{escape(c["case_id"])}</small><h2>{escape(c["title"])}</h2><p>가상 제작 의뢰서 · 2쪽 · 텍스트 PDF</p><nav><a href="{c["pdf"]}">의뢰서 PDF ↗</a><a href="{c["markdown"]}">수정용 MD</a></nav></article>')
    page = '''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>2단계 · 가상 클라이언트 제출 문서</title><style>*{box-sizing:border-box}body{margin:0;background:#f3f3ec;color:#173f35;font:15px/1.7 Arial,'Apple SD Gothic Neo','Malgun Gothic',sans-serif}main{max-width:1200px;margin:auto;padding:50px 32px}h1{font-size:35px;letter-spacing:-1px}h2{font-size:22px;min-height:65px;line-height:1.45}a{color:inherit}header{border-bottom:1px solid #a7b69c;padding-bottom:20px}.notice{padding:18px;background:#e7eddc;margin:25px 0}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}article{background:#fffef8;border:1px solid #d4ddc9;padding:26px}small{font-size:12px;color:#697d66}nav{display:flex;gap:18px;flex-wrap:wrap}article a{font-size:14px}footer{margin-top:30px;border-top:1px solid #a7b69c;padding-top:20px;color:#61755d}@media(max-width:850px){.grid{grid-template-columns:1fr}main{padding:24px 16px}h1{font-size:27px}h2{min-height:0}}</style><main><header><small>CREATIVE CHECKER / CLIENT INPUTS / STAGE 02</small><h1>클라이언트가 제출하는 세 가지 문서.</h1><p>전시·연주회·축제 시나리오에 맞춰 작성한 개발용 가상 제작 의뢰서입니다.</p><nav><a href="../index.html">시나리오 열람</a><a href="README.md">입력 사용 안내</a><a href="../진행현황.json">최신 진행 기록</a></nav></header><div class="notice">각 의뢰서에는 필수 사항 1개가 확인 중으로 남아 있습니다. 현재 사람의 기준 승인은 없으며 실제 발주·계약 문서나 독립 평가 정답이 아닙니다.</div><div class="grid">'''
    page += ''.join(cards)
    page += '''</div><footer><strong>A02에는 각 의뢰서 PDF만 입력합니다.</strong> 참고 포스터·시나리오 JSON·단계 3 검토표는 입력에 섞지 않습니다. 원본 포스터는 기존 폴더에 보존했습니다. 링크는 모두 상대 경로이며 외부 글꼴·CDN·스크립트를 사용하지 않습니다.</footer></main></html>'''
    (OUT / 'index.html').write_text(page, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--font', type=Path)
    opt = parser.parse_args()
    candidates = [opt.font] if opt.font else [Path('/Library/Fonts/Arial Unicode.ttf'), Path('C:/Windows/Fonts/malgun.ttf'), Path('/usr/share/fonts/truetype/nanum/NanumGothic.ttf')]
    font = next((x for x in candidates if x and x.is_file()), None)
    if font is None:
        raise SystemExit('Pass --font with an embeddable Korean TrueType font.')
    pdfmetrics.registerFont(TTFont('Korean', str(font)))
    design = json.loads(SCENARIOS.read_text(encoding='utf-8'))
    assert design['stage'] == 1 and len(design['cases']) == 3
    OUT.mkdir(exist_ok=True)
    manifest = {
        'artifact_version': VERSION, 'stage': 2, 'created_at': DATE,
        'source_scenario_sha256': sha(SCENARIOS),
        'source_kind': 'poster_derived_synthetic_with_explicit_new_gap',
        'human_verified': False, 'criteria_approval_status': 'unapproved',
        'gold_verdict': None, 'model_inference_executed': False, 'database_changed': False,
        'model_input': 'Each listed PDF only; other files are not extraction inputs.',
        'authoring_refinements': [
            '연주회 프로그램의 영문 부제 전체·작품 번호는 현재 필수 표기 범위에서 제외한다고 명시하여 관람연령 외의 추가 필수 미정 항목으로 해석되지 않게 했다.',
            '축제 교통 안내의 허용 표현은 담당자 확인 후 사용할 수 있는 문장으로 정리하고 내부 문자열 비교 방식 설명은 PDF에서 제외했다.',
        ],
        'documents': [],
    }
    for case in design['cases']:
        folder = OUT / case['id']
        folder.mkdir(exist_ok=True)
        stem = case['category'] + '_포스터_제작의뢰서_v0.2'
        pdf, md = folder / (stem + '.pdf'), folder / (stem + '.md')
        write_pdf(case, pdf)
        write_md(case, md)
        reader = PdfReader(pdf)
        if len(reader.pages) != 2:
            raise RuntimeError(f'{case["id"]}: expected 2 pages, found {len(reader.pages)}')
        manifest['documents'].append({
            'case_id': case['id'], 'title': case['title'],
            'document_version': 'client-draft-v' + VERSION,
            'pdf': pdf.relative_to(OUT).as_posix(), 'pdf_sha256': sha(pdf),
            'markdown': md.relative_to(OUT).as_posix(), 'markdown_sha256': sha(md),
            'page_count': len(reader.pages), 'input_role': 'a02_source_document',
            'approval_status': 'unapproved',
        })
    write_index(manifest['documents'])
    (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'stage': 2, 'pdf_count': len(manifest['documents']), 'pages': sum(d['page_count'] for d in manifest['documents']), 'documents': manifest['documents']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
