"""Rebuild document artifacts from local inputs; no model, DB or network calls.

Run with a Python containing reportlab, pypdf and Pillow. Reading the generated
PDF/HTML does not require Python. Font paths are local build inputs, not links
in the delivered documents. Use --font for another embedded Korean TTF.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
from pathlib import Path

from PIL import Image
from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)


ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT / 'baseline_data.json').read_text(encoding='utf-8'))
GREEN = colors.HexColor('#173f35')
MUTED = colors.HexColor('#53665e')
PAPER = colors.HexColor('#f3f5ee')
WIDTH = A4[0] - 92


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def p(text, size=9.1, leading=14, color=colors.HexColor('#22362f')):
    return Paragraph(html.escape(str(text)).replace('\n', '<br/>'), ParagraphStyle(
        'body', fontName='Korean', fontSize=size, leading=leading,
        textColor=color, wordWrap='CJK', spaceAfter=6,
    ))


def title(text, subtitle):
    return [p('CREATIVE CHECKER / A02 DEVELOPMENT DOCUMENT', 8, 11, MUTED),
            p(text, 21, 29, GREEN), p(subtitle, 9, 14, MUTED), Spacer(1, 10)]


def heading(text):
    return [Spacer(1, 8), p(text, 12.3, 18, GREEN)]


def table(rows, widths=None, header=True):
    data = [[p(c, 8.6, 13) for c in row] for row in rows]
    t = Table(data, colWidths=widths or [WIDTH * .24, WIDTH * .76],
              repeatRows=1 if header else 0, hAlign='LEFT')
    commands = [
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LINEBELOW', (0, 0), (-1, 0), .7, GREEN),
        ('LINEBELOW', (0, 1), (-1, -1), .35, colors.HexColor('#d9dfd5')),
        ('LEFTPADDING', (0, 0), (-1, -1), 9),
        ('RIGHTPADDING', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
    ]
    if header:
        commands.append(('BACKGROUND', (0, 0), (-1, 0), PAPER))
    t.setStyle(TableStyle(commands))
    return t


def footer(canvas, doc):
    canvas.setStrokeColor(GREEN)
    canvas.line(46, A4[1] - 38, A4[0] - 46, A4[1] - 38)
    canvas.setFont('Korean', 7.4)
    canvas.setFillColor(MUTED)
    canvas.drawString(46, 29, '개발용 가상 초안 v0.1 / 사람 승인 전 / 실제 계약서 아님')
    canvas.drawRightString(A4[0] - 46, 29, f'2026-11-03   |   {doc.page}')


def write_pdf(path, story, name):
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=46, leftMargin=46,
                            topMargin=53, bottomMargin=51,
                            title=name, author='Creative Checker development draft')
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def base_rows(case=None):
    return [
        ['항목', '내용'],
        ['문서 / 기준 버전', f'{case["id"]} / v0.1' if case else '[문서 ID] / [버전]'],
        ['과업명', case['title'] + ' 포스터 디자인 기준' if case else '[포스터 과업명]'],
        ['디자인 분야', '시각디자인 / 포스터'],
        ['수요자 / 공급자', '미정 / 미정 - 실제 기관·인물을 계약 당사자로 지정하지 않음'],
        ['계약기간 / 납기', '미정 - 행사일과 납품기한을 동일하게 취급하지 않음'],
        ['금액 / 지급 조건', '미정 - 추출 시 임의 숫자·지급일을 보완하지 않음'],
        ['시안 수 / 수정 횟수', '미정 - 포스터 한 점을 비교 입력으로 선정한 사실과 별개'],
        ['결과물 범위', '개발 가정: 디지털 포스터 파일 1점. 편집 원본·인쇄물은 별도 합의 필요'],
        ['자료 상태', '포스터 관찰 기반 가상 요구사항 / human_verified=false / gold_verdict=null'],
    ]


REFERENCES = [
    ['참고 위치', '이 문서에 반영한 범위'],
    ['원본 PDF 2~3쪽', '전문상의 계약 개요·시각디자인 선택·시안 및 결과물·첨부 문서 구조를 참고한다. 빈칸을 사실로 채우지 않는다.'],
    ['원본 PDF 6쪽 / 제5조①', '용역 내용·범위는 첨부 개발 세부내역 및 견적서를 기준으로 한다는 구조를 참고한다. 포스터의 구체적인 문구·날짜·파일 규격은 별첨 초안에서 제안한다.'],
    ['원본 PDF 7쪽 / 제6조·제7조', '변경 서면 기록, 완료검사 및 수정·보완 후 재검사 흐름을 참고한다. 실제 당사자 간 변경·검수 합의를 대신하지 않는다.'],
    ['원본 PDF 2쪽 및 7~8쪽', '제3자 창작물 고지·지식재산권은 별도 자료로 확인할 영역이다. 포스터 픽셀만으로 권리 보유·허락을 판정하지 않는다.'],
    ['원본 PDF 11쪽 / 제19조②', '첨부 문서를 함께 관리하는 구조를 참고한다. 이 개발 초안은 실제 합의서가 아니며 계약 우선순위를 새로 결정하지 않는다.'],
]


def make_common():
    story = title('포스터 기준문서 공통 양식', '표준계약서 참고 / 작성자가 합의·보완할 기본 구조')
    story += [p(DATA['purpose']), table(base_rows())]
    story += heading('작성 원칙')
    story += [p('원본 표준계약서는 공통 계약 틀이다. 행사명·일시·장소·필수 문구·색상·크기 등 포스터별 조건은 별첨 개발 세부내역에 명시하고 사람이 확인한다.'),
              p('미정은 누락된 입력이다. 빈 기대값을 자동 충족으로 만들거나 이미지를 보고 계약금액·납품기한·권리 양도를 추정하지 않는다.'),
              p('A02 입력에는 기준문서 PDF를 넣는다. 포스터 원본은 후속 이미지 검수 입력이며 문서 추출 입력에 섞지 않는다.')]
    story += [PageBreak()]
    story += title('별첨 작성과 승인 절차', '원본 조항의 구조와 새로 제안한 포스터 조건을 구분')
    story += [table(REFERENCES)]
    story += heading('별첨에 채울 세 가지 항목')
    story += [p('1. 문구·날짜·장소: 요구 문장, 기대값, 허용 표기, 출처 위치를 적는다.'),
              p('2. 시각·파일: 색상·배치·사진 같은 정성 항목과 파일 형식·치수 같은 메타데이터 항목을 구분한다.'),
              p('3. 사람 확인: 실제 계약 당사자, 대금·납기, 표장·사진·글꼴 권리 증빙, 인쇄·편집 원본 범위를 확인한다.')]
    story += heading('개발용 승인·재검수 흐름')
    story += [p('PDF 등록 → A02 조건 초안 → 원문 대조·수정 → 명시적 기준 승인 → 포스터 검수 → 수정 요청 → 새 파일·새 버전 재검수 → 최종 승인 기록.'),
              p('원본 제7조②에는 14일 내 검사결과 통지와 관련된 내용이 있다. 이 자료는 계약 집행이 아닌 개발 초안이므로 시스템은 경과일만으로 사람의 승인 기록을 생성하지 않는다.'),
              p('버전·해시·승인 대상은 함께 기록한다. 변경된 문서·포스터에 이전 승인을 자동 승계하지 않는다.')]
    write_pdf(ROOT / 'common/공통_기준문서_양식_v0.1.pdf', story, '포스터 기준문서 공통 양식 v0.1')


def make_case(case):
    folder = ROOT / 'cases' / case['id']
    story = title(case['category'] + ' 포스터 기준문서', case['title'] + ' / ' + case['id'])
    story += [p(DATA['purpose']), table(base_rows(case))]
    story += heading('선정한 비교 입력')
    story += [p(case['selection_reason']), p(case['observed']), p(case['note'])]
    story += [PageBreak()]
    story += title('별첨 1. 문구·일정 요구사항', '이미지 관찰에서 역작성한 제안 / 발주자 승인 전')
    story += [p('아래 조건과 허용 범위는 개발 초안의 제안이다. 표준계약서가 정한 포스터 필수 문구가 아니며, 원 포스터의 실제 발주 요구사항으로 검증되지 않았다.')]
    rows = [['ID', '요구사항 / 허용 범위 / 관찰 출처']]
    for row in case['text_requirements']:
        rows.append([row['id'], row['requirement'] + '\n허용·검토: ' + row['allowance']
                     + '\n관찰 문구: “' + row['observed_quote'] + '”'])
    story += [table(rows, [73, WIDTH - 73])]
    story += [PageBreak()]
    story += title('별첨 2. 시각·납품·확인 항목', '자동 대조·정성 검토·증빙 확인을 구분')
    visual = [['ID', '시각 요소 요구 / 확인 방법']]
    visual += [[r['id'], r['requirement'] + '\n' + r['verification']] for r in case['visual_requirements']]
    story += [table(visual, [79, WIDTH - 79])]
    story += heading('개발용 파일 납품 규격 제안')
    story += [p(f'{case["format"]} / {case["color_mode"]} / 가로 {case["width"]} × 세로 {case["height"]}픽셀. 이 수치는 선택한 비교 입력 파일에서 확인한 메타데이터이며, 여기서는 테스트 규격 후보로 제안한다. 인쇄용 규격·해상도(DPI)·색상 프로파일·편집 원본 조건은 별도 확인한다.')]
    story += heading('아직 확정하지 않은 항목')
    for n, text in enumerate(case['pending'], 1):
        story += [p(f'{n}. {text}', 8.8, 13)]
    story += heading('변경·승인·재검수')
    story += [p('기준 승인 전에는 초안이다. 기준을 수정하면 새 문서/기준 버전을 발급하고, 수정 포스터는 새 파일 해시·새 버전으로 재검수한다. 최종 승인자는 사람이 기록한다.'),
              p('원본 표준계약서 6쪽 제5조, 7쪽 제6조·제7조, 11쪽 제19조의 문서·변경·검사 구조를 참고했다. 당사자·지급·지식재산권 조건에 대한 실제 계약을 체결한 문서가 아니다.'),
              p('평가 주의: 완성 포스터에서 역작성한 자료다. 실제 발주서·독립 평가 정답으로 사용할 수 없으며, 위반 사례와 사람 확정 정답은 별도로 준비한다.', 8.3, 12, MUTED)]
    write_pdf(folder / '기준문서_v0.1.pdf', story, case['title'] + ' 기준문서 v0.1')


def case_md(case):
    text = f'# {case["title"]} - 포스터 기준문서 v0.1\n\n'
    text += DATA['purpose'] + '\n\n'
    text += '| 항목 | 내용 |\n|---|---|\n'
    for row in base_rows(case)[1:]:
        text += f'| {row[0]} | {row[1]} |\n'
    text += '\n## 원본 포스터 관찰\n\n' + case['observed'] + '\n\n' + case['note'] + '\n'
    text += '\n## 별첨 1. 문구·일정 요구사항 후보\n\n'
    for item in case['text_requirements']:
        text += f'### {item["id"]}\n\n{item["requirement"]}\n\n- 허용·검토: {item["allowance"]}\n- 관찰 출처: 포스터 문구 “{item["observed_quote"]}”\n\n'
    text += '## 별첨 2. 시각 요소 후보\n\n'
    for item in case['visual_requirements']:
        text += f'- **{item["id"]}** {item["requirement"]} {item["verification"]}\n'
    text += f'\n## 개발용 파일 규격 제안\n\n{case["format"]}, {case["color_mode"]}, {case["width"]}×{case["height"]}px. 원본 파일 메타데이터를 테스트 규격 후보로 제안한 것이며 원래 발주 조건을 확보한 것은 아니다. 인쇄·편집 원본 규격은 미정이다.\n'
    text += '\n## 사람이 확인할 미정 사항\n\n' + '\n'.join('- ' + x for x in case['pending']) + '\n'
    text += '\n## 변경·승인·출처\n\n'
    text += '표준계약서 6쪽 제5조, 7쪽 제6조·제7조, 11쪽 제19조의 구조를 참고했다. 포스터 문구·시각·파일 조건은 AI가 관찰하여 역작성한 가상 제안이다. 기준 승인 전 초안이며 변경된 문서·포스터에 승인을 자동 승계하지 않는다.\n\n'
    text += '실제 계약·사람 확정 정답·독립 평가셋이 아니다. 이미지 검수 정답을 pass로 미리 지정하지 않았다.\n'
    return text


def write_html(manifest):
    cards = []
    for c in DATA['cases']:
        ident = c['id']
        cards.append(f'''<article><a href="cases/{ident}/poster.jpg"><img src="previews/{ident}.jpg" alt="{html.escape(c['title'])}"></a><div><small>{c['category']} · {c['poster_id']}</small><h2>{html.escape(c['title'])}</h2><p>{html.escape(c['selection_reason'])}</p><p class="meta">{c['width']} × {c['height']} px · {len(c['text_requirements'])}개 문구 조건 · 2개 시각 조건</p><nav><a href="cases/{ident}/기준문서_v0.1.pdf">기준문서 PDF ↗</a><a href="cases/{ident}/기준문서_v0.1.md">수정용 MD</a><a href="cases/{ident}/poster.jpg">포스터 원본</a></nav></div></article>''')
    content = '''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>포스터 기준문서 초안 3종</title><style>
    *{box-sizing:border-box}body{margin:0;background:#f3f3ec;color:#183b31;font-family:Arial,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;line-height:1.6}main{max-width:1380px;margin:0 auto;padding:56px 40px}header{border-bottom:1px solid #bdc8b8;padding-bottom:24px;margin-bottom:24px}h1{font-size:38px;letter-spacing:-1.8px;line-height:1.25;margin:14px 0}header small{letter-spacing:2px;font-size:11px}p{color:#526259}a{color:inherit}.notice{background:#e6ebd9;border:1px solid #c9d3b6;padding:15px 20px;margin:22px 0}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px}article{background:#fffef8;border:1px solid #d5dccc;overflow:hidden}article>a{height:320px;display:block;background:#e6e8dd;padding:14px}article img{width:100%;height:100%;object-fit:contain}article>div{padding:24px}h2{font-size:20px;line-height:1.4;min-height:58px;margin:10px 0 14px}article small,.meta{font-size:12px;color:#68776d}article p{font-size:14px}nav{display:flex;flex-wrap:wrap;gap:9px 14px;font-size:13px;margin-top:18px}nav a{border-bottom:1px solid #73876b;text-decoration:none}.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin:32px 0}.steps>div{padding:20px;border-top:1px solid #9aab8f}.steps h3{margin:0;font-size:16px}footer{font-size:12px;color:#677568;border-top:1px solid #bdc8b8;margin-top:32px;padding-top:20px}@media(max-width:900px){.grid,.steps{grid-template-columns:1fr}main{padding:28px 18px}h1{font-size:29px}}@media print{main{padding:0}.grid{grid-template-columns:repeat(3,1fr)}article>a{height:210px}nav{display:none}}
    </style><main><header><small>CREATIVE CHECKER / DOCUMENT BASELINES / 2026.11.03</small><h1>포스터에서 시작하는<br>세 가지 기준문서.</h1><p>표준계약서의 공통 틀과 포스터별 개발 세부내역을 연결한 A02 개발용 초안입니다.</p><nav><a href="common/공통_기준문서_양식_v0.1.pdf">공통 양식 PDF ↗</a><a href="common/공통_기준문서_양식_v0.1.md">공통 양식 MD</a><a href="sources/standard-contract-2025-033.pdf">표준계약서 참고 원본</a><a href="README.md">사용 안내</a></nav></header><div class="notice"><strong>사람 승인 전 초안</strong> · 완성 포스터를 보고 역작성한 가상 요구사항입니다. 실제 발주서나 독립 평가 정답이 아닙니다. 계약 당사자·금액·납기는 미정이며 A02·DB·모델 연결을 실행한 자료가 아닙니다.</div><section class="grid">'''
    content += ''.join(cards)
    content += '''</section><section class="steps"><div><h3>01 · PDF만 추출 입력</h3><p>각 사례의 기준문서 PDF를 A02 입력으로 사용합니다. 포스터와 이 열람 페이지는 조건 추출 입력에 섞지 않습니다.</p></div><div><h3>02 · 원문 대조와 승인</h3><p>문구·기대값·페이지 출처와 미정 항목을 확인한 뒤 기준 버전을 승인합니다.</p></div><div><h3>03 · 포스터 검사와 재검수</h3><p>원본 이미지로 검사하고 오타·누락·변경 사례와 사람 정답은 별도로 준비합니다.</p></div></section><footer>모든 링크·스타일·미리보기는 로컬 상대 경로입니다. Mac·Windows에서 폴더 구조를 유지한 채 index.html을 열 수 있습니다. 원본 파일과 해시는 manifest.json에 기록했습니다.</footer></main></html>'''
    (ROOT / 'index.html').write_text(content, encoding='utf-8')


def main():
    args = argparse.ArgumentParser()
    args.add_argument('--source-pdf', type=Path, help='First build only: supplied source PDF')
    args.add_argument('--images', type=Path, help='First build only: directory with NEW004/043/072.jpg')
    args.add_argument('--font', type=Path)
    opt = args.parse_args()
    fonts = [opt.font] if opt.font else [Path('/Library/Fonts/Arial Unicode.ttf'), Path('C:/Windows/Fonts/malgun.ttf'), Path('/usr/share/fonts/truetype/nanum/NanumGothic.ttf')]
    font = next((f for f in fonts if f and f.is_file()), None)
    if font is None:
        raise SystemExit('Pass --font with a Korean TrueType font.')
    pdfmetrics.registerFont(TTFont('Korean', str(font)))
    for name in ['common', 'sources', 'previews']:
        (ROOT / name).mkdir(exist_ok=True)
    src = ROOT / 'sources/standard-contract-2025-033.pdf'
    if opt.source_pdf:
        if sha(opt.source_pdf) != DATA['source_sha256']:
            raise SystemExit('Unexpected supplied source PDF hash')
        shutil.copyfile(opt.source_pdf, src)
    assert sha(src) == DATA['source_sha256']
    manifest = {k: DATA[k] for k in ['version', 'created_at', 'status', 'human_verified', 'source_kind', 'gold_verdict', 'purpose']}
    manifest['source_pdf'] = {'path': src.relative_to(ROOT).as_posix(), 'sha256': sha(src), 'pages': len(PdfReader(src).pages)}
    manifest['pairs'] = []
    make_common()
    for case in DATA['cases']:
        folder = ROOT / 'cases' / case['id']
        folder.mkdir(parents=True, exist_ok=True)
        poster = folder / 'poster.jpg'
        if opt.images:
            shutil.copyfile(opt.images / (case['poster_id'] + '.jpg'), poster)
        with Image.open(poster) as im:
            assert im.size == (case['width'], case['height'])
            assert im.mode == case['color_mode'] and im.format == case['format']
            preview = im.copy()
            preview.thumbnail((650, 800))
            preview.save(ROOT / 'previews' / (case['id'] + '.jpg'), quality=88)
        make_case(case)
        (folder / '기준문서_v0.1.md').write_text(case_md(case), encoding='utf-8')
        pdf = folder / '기준문서_v0.1.pdf'
        manifest['pairs'].append({
            'id': case['id'], 'poster_id': case['poster_id'], 'title': case['title'],
            'document': pdf.relative_to(ROOT).as_posix(), 'document_sha256': sha(pdf),
            'document_pages': len(PdfReader(pdf).pages),
            'poster': poster.relative_to(ROOT).as_posix(), 'poster_sha256': sha(poster),
            'width': case['width'], 'height': case['height'], 'format': case['format'],
            'color_mode': case['color_mode'],
            'text_requirement_candidates': len(case['text_requirements']),
            'visual_requirement_candidates': len(case['visual_requirements']),
            'file_metadata_proposal': True, 'approval_status': 'unapproved', 'gold_verdict': None,
        })
    common = ['# 포스터 기준문서 공통 양식 v0.1', '', DATA['purpose'], '', '## 계약·과업 개요', '', '| 항목 | 내용 |', '|---|---|']
    common += [f'| {r[0]} | {r[1]} |' for r in base_rows()[1:]]
    common += ['', '## 원본과의 연결', '', '| 참고 위치 | 반영한 범위 |', '|---|---|']
    common += [f'| {r[0]} | {r[1]} |' for r in REFERENCES[1:]]
    common += ['', '## 별첨 입력', '', '- 요구 문장·기대값·허용 범위·출처 위치', '- 문구/시각/파일 규격의 검사 방식', '- 미정 사항과 권리·납품 증빙', '', '## 승인', '', '추출 초안 → 원문 대조·수정 → 명시적 기준 승인 → 포스터 검수 → 새 버전 재검수 → 최종 승인 기록. 경과일만으로 자동 승인하지 않는다.', '']
    (ROOT / 'common/공통_기준문서_양식_v0.1.md').write_text('\n'.join(common), encoding='utf-8')
    write_html(manifest)
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'pdfs': 4, 'pairs': manifest['pairs']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
