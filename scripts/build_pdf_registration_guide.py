"""Offline HTML from the PDF registration guide; no application or DB startup."""
from html import escape
from pathlib import Path

from scripts.build_agent_handoff import markdown,STYLE,Audit


def build():
    source=Path(__file__).resolve().parents[1]/'docs/agent-framework/PDF_등록_버전저장_가이드.md'
    body,headings=markdown(source.read_text(encoding='utf-8'))
    toc=''.join(f'<a href="#{escape(identifier)}">{escape(title)}</a>' for identifier,title in headings)
    html=f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>PDF 등록·버전 저장 · Creative Checker</title><style>{STYLE}</style></head>
<body><div class="shell"><aside><a class="brand" href="index.html">Creative Checker ↗</a><div class="edition">PDF REGISTRATION / AF002</div><nav class="toc" aria-label="문서 목차">{toc}</nav><div class="side-foot">2026.11.03 · 4단계<br>기존 DB · 인증 · 파일 저장 재사용</div></aside><div class="content"><header class="topbar"><span class="crumb">A02 / DOCUMENT INPUT</span><div class="tools"><a href="{source.name}">Markdown 원본 ↗</a><a href="../poster-baselines/2026-11-03/workflow/index.html">작업 현황 ↗</a></div></header>
<div class="hero"><span class="eyebrow">ONE PDF. AN IMMUTABLE VERSION.</span><h1>원본을 등록하고,<br>같은 버전을 다시 읽습니다.</h1><p>PDF 바이트·문서 버전·해시를 기존 프로젝트에 연결합니다.<br>실제 Qwen 추출과 기준 승인은 다음 단계입니다.</p><div class="meta"><span>20 MiB</span><span>50 PAGES</span><span>EXPLICIT AF002</span></div></div><article>{body}</article><footer class="footer">격리 PostgreSQL 검증 · 운영 DB 적용 및 모델 실행 없음<br>HTML은 서버·외부 리소스 없이 열람합니다.</footer></div></div></body></html>'''
    audit=Audit();audit.feed(html)
    if audit.resources or len(audit.ids)!=len(set(audit.ids)):raise ValueError('invalid offline guide')
    if any(link[1:] not in audit.ids for link in audit.links if link.startswith('#')):raise ValueError('unresolved section')
    source.with_suffix('.html').write_text(html,encoding='utf-8')
    print('Built PDF registration guide:',len(headings),'sections')


if __name__=='__main__':build()
