"""Build the offline stage-seven guide and gallery from explicit local fixtures."""
from html import escape
import json
from pathlib import Path
from scripts.build_agent_handoff import markdown,STYLE,Audit


def build():
    root=Path(__file__).resolve().parents[1]
    source=root/'docs/agent-framework/포스터_검수재검수_가이드.md'
    body,headings=markdown(source.read_text(encoding='utf-8'))
    toc=''.join(f'<a href="#{escape(i)}">{escape(title)}</a>' for i,title in headings)
    html=f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>포스터 검수·재검수 · Creative Checker</title><style>{STYLE}</style></head><body><div class="shell"><aside><a class="brand" href="index.html">Creative Checker ↗</a><div class="edition">POSTER REVIEW / AF003</div><nav class="toc" aria-label="문서 목차">{toc}</nav><div class="side-foot">2026.11.03 · 7단계<br>고정 기준 · 새 이미지 · 새 확인</div></aside><div class="content"><header class="topbar"><span class="crumb">APPROVED BASELINE / REVIEW / REVISION</span><div class="tools"><a href="{source.name}">Markdown 원본 ↗</a><a href="../poster-baselines/2026-11-03/workflow/poster-tests/index.html">시험 포스터 ↗</a></div></header><div class="hero"><span class="eyebrow">ONE BASELINE. EVERY POSTER VERIFIED.</span><h1>기준은 고정하고,<br>수정본은 다시 확인.</h1><p>기존 파일·PostgreSQL·worker로 승인 기준부터 재검수까지 연결합니다.<br>실제 모델과 사람의 승인은 개발용 시험과 구분합니다.</p><div class="meta"><span>EXISTING IMAGE STORAGE</span><span>PINNED AF003 BASELINE</span><span>EXPLICIT HUMAN DECISIONS</span></div></div><article>{body}</article><footer class="footer">실제 파일·격리 PostgreSQL 검증 · 관측/승인 입력은 합성 개발 자료<br>실제 Qwen·클라이언트 승인·front-v2·운영 DB 적용은 수행하지 않았습니다.</footer></div></div></body></html>'''
    audit=Audit();audit.feed(html)
    if audit.resources or len(audit.ids)!=len(set(audit.ids)):raise ValueError('invalid offline guide')
    if any(link[1:] not in audit.ids for link in audit.links if link.startswith('#')):raise ValueError('unresolved section')
    source.with_suffix('.html').write_text(html,encoding='utf-8')
    fixtures=root/'docs/poster-baselines/2026-11-03/workflow/poster-tests'
    manifest=json.loads((fixtures/'manifest.json').read_text(encoding='utf-8'))
    labels={'normal':('정상','pass'),'wrong_date':('날짜 오류','fail'),'missing_notice':('안내 누락','fail'),'revised':('수정본','pass')}
    cards=[]
    for row in manifest['records']:
        label,verdict=labels[row['variant']]
        cards.append(f'''<section><a href="{escape(row['png'])}"><img src="{escape(row['png'])}" alt="{escape(row['case_id']+' '+label)}" width="600" height="800"></a><div><small>{escape(row['case_id'])} / SYNTHETIC</small><h2>{label}</h2><p>스크립트 관측 기반 기대 결과: <b>{verdict}</b></p><p>원본 포스터 또는 실모델 결과가 아닙니다.</p><a href="{escape(row['svg'])}">SVG 원본 ↗</a><code>{row['png_sha256'][:16]}…</code></div></section>''')
    gallery='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>포스터 검수 개발 시험 자료</title><style>*{box-sizing:border-box}body{margin:0;background:#f3f3ec;color:#173f35;font:15px/1.6 Arial,'Apple SD Gothic Neo','Malgun Gothic',sans-serif}main{max-width:1480px;margin:auto;padding:42px 30px}h1{font-size:36px;letter-spacing:-1.5px;margin:12px 0}h2{font-size:23px;margin:7px 0}a{color:inherit}nav{display:flex;gap:24px}aside{background:#e5ead8;padding:18px;margin:24px 0}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:18px}section{background:#fffef9;border:1px solid #d4ddcb;overflow:hidden}img{display:block;width:100%;height:auto}section div{padding:16px}small{font-size:11px;letter-spacing:1px}p{font-size:13px}code{display:block;font-size:10px;color:#73816c;margin-top:12px}@media(max-width:900px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}</style><main><small>CREATIVE CHECKER / STAGE 07 / SYNTHETIC DEVELOPMENT</small><h1>정상부터 수정본까지, 12개 시험 입력.</h1><nav><a href="../../../../agent-framework/포스터_검수재검수_가이드.html">연결 가이드 ↗</a><a href="../07_검증기록.json">검증 기록 ↗</a><a href="manifest.json">해시·sidecar manifest ↗</a></nav><aside>새 SVG 코드로 만든 개발 자료입니다. 제목·날짜·안내 3개 합성 기준과 독립 sidecar 관측으로 배선만 시험합니다. 원본 포스터를 수정하지 않았으며 실제 OCR·Qwen 추론이나 사람의 승인 결과가 아닙니다.</aside><div class="grid">'''+''.join(cards)+'</div></main></html>'
    (fixtures/'index.html').write_text(gallery,encoding='utf-8')
    print('Built poster guide and gallery:',len(headings),'sections,',len(cards),'fixtures')


if __name__=='__main__':build()
