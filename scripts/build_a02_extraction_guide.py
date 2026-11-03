"""Offline A02 extraction guide. No model, database or network initialization."""
from html import escape
from pathlib import Path
from scripts.build_agent_handoff import markdown,STYLE,Audit


def build():
    source=Path(__file__).resolve().parents[1]/'docs/agent-framework/A02_조건추출_연결가이드.md'
    body,headings=markdown(source.read_text(encoding='utf-8'))
    toc=''.join(f'<a href="#{escape(i)}">{escape(title)}</a>' for i,title in headings)
    html=f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>A02 조건 추출 연결 · Creative Checker</title><style>{STYLE}</style></head><body><div class="shell"><aside><a class="brand" href="index.html">Creative Checker ↗</a><div class="edition">A02 / GROUNDED DRAFT</div><nav class="toc" aria-label="문서 목차">{toc}</nav><div class="side-foot">2026.11.03 · 5단계<br>PDF-only · mock 검증 · 실제 Qwen 미호출</div></aside><div class="content"><header class="topbar"><span class="crumb">PDF / WORKER / UNAPPROVED DRAFT</span><div class="tools"><a href="{source.name}">Markdown 원본 ↗</a><a href="../poster-baselines/2026-11-03/workflow/index.html">진행 현황 ↗</a></div></header><div class="hero"><span class="eyebrow">READ THE SOURCE. KEEP THE TRACE.</span><h1>원문에서 추출하고,<br>근거와 함께 저장합니다.</h1><p>PDF-only A02 실행과 미승인 초안의 저장·재조회를 연결했습니다.<br>mock 결과와 실제 Qwen 추론을 구분합니다.</p><div class="meta"><span>1 ROLE</span><span>0 LIVE MODEL CALLS</span><span>HUMAN APPROVAL PENDING</span></div></div><article>{body}</article><footer class="footer">격리 PostgreSQL·가짜 HTTP transport 검증 · 운영 DB 미적용<br>본문·표·인용문의 의미 정확도와 기준 승인은 별도 확인 대상입니다.</footer></div></div></body></html>'''
    audit=Audit();audit.feed(html)
    if audit.resources or len(audit.ids)!=len(set(audit.ids)):raise ValueError('invalid offline guide')
    if any(link[1:] not in audit.ids for link in audit.links if link.startswith('#')):raise ValueError('unresolved section')
    source.with_suffix('.html').write_text(html,encoding='utf-8')
    print('Built A02 extraction guide:',len(headings),'sections')


if __name__=='__main__':build()
