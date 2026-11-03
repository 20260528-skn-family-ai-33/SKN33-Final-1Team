"""Offline criteria revision/approval guide; no app, database or model startup."""
from html import escape
from pathlib import Path
from scripts.build_agent_handoff import markdown,STYLE,Audit


def build():
    source=Path(__file__).resolve().parents[1]/'docs/agent-framework/기준_보완승인_가이드.md'
    body,headings=markdown(source.read_text(encoding='utf-8'))
    toc=''.join(f'<a href="#{escape(i)}">{escape(title)}</a>' for i,title in headings)
    html=f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>기준 보완·승인 · Creative Checker</title><style>{STYLE}</style></head><body><div class="shell"><aside><a class="brand" href="index.html">Creative Checker ↗</a><div class="edition">CRITERIA REVIEW / AF003</div><nav class="toc" aria-label="문서 목차">{toc}</nav><div class="side-foot">2026.11.03 · 6단계<br>변경 이력 · 명시적 승인 · 재조회</div></aside><div class="content"><header class="topbar"><span class="crumb">SUPPLEMENT / VERSION / APPROVAL</span><div class="tools"><a href="{source.name}">Markdown 원본 ↗</a><a href="../poster-baselines/2026-11-03/workflow/index.html">진행 현황 ↗</a></div></header><div class="hero"><span class="eyebrow">A CLEAR SOURCE. AN EXACT APPROVAL.</span><h1>보완은 새 버전으로,<br>승인은 정확한 기준에.</h1><p>원본 초안과 보완의 출처를 보존하고 승인 대상을 고정합니다.<br>개발용 보완·승인 테스트와 실제 사람의 결정을 구분합니다.</p><div class="meta"><span>IMMUTABLE VERSIONS</span><span>EXPLICIT AF003</span><span>WAITING FOR POSTER</span></div></div><article>{body}</article><footer class="footer">격리 PostgreSQL의 합성 보완·승인 검증 · 운영 DB 미적용<br>실제 클라이언트 회신·기준 승인·Qwen 추론은 수행하지 않았습니다.</footer></div></div></body></html>'''
    audit=Audit();audit.feed(html)
    if audit.resources or len(audit.ids)!=len(set(audit.ids)):raise ValueError('invalid offline guide')
    if any(link[1:] not in audit.ids for link in audit.links if link.startswith('#')):raise ValueError('unresolved section')
    source.with_suffix('.html').write_text(html,encoding='utf-8')
    print('Built criteria review guide:',len(headings),'sections')


if __name__=='__main__':build()
