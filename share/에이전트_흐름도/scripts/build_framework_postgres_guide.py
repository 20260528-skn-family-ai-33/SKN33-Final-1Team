"""Offline HTML from the PostgreSQL framework Markdown; no DB or app imports."""
from html import escape
from pathlib import Path

from scripts.build_agent_handoff import markdown,STYLE,SCRIPT,Audit


def build():
    folder=Path(__file__).resolve().parents[1]/'docs/agent-framework'
    source=folder/'에이전트_프레임워크_PostgreSQL_실행가이드.md'
    body,headings=markdown(source.read_text(encoding='utf-8'))
    toc=''.join(f'<a href="#{escape(identifier)}">{escape(title)}</a>' for identifier,title in headings)
    html=f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PostgreSQL 프레임워크 실행가이드 · Creative Checker</title><style>{STYLE}</style></head>
<body><div class="shell"><aside><a class="brand" href="index.html">Creative Checker ↗</a><div class="edition">POSTGRES / FRAMEWORK AF001</div><div class="nav-label">CONTENTS</div><nav class="toc" aria-label="문서 목차">{toc}</nav><div class="side-foot"><a href="에이전트_프레임워크_가이드.html">담당자별 연결 가이드 ↗</a><br>기존 DB · 별도 worker · 명시적 적용</div></aside><div class="content">
<header class="topbar"><span class="crumb">FRAMEWORK / PERSISTENT EXECUTION</span><div class="tools"><a href="{source.name}">Markdown 원본 ↗</a><div class="platform" role="group" aria-label="실행 명령 운영체제"><button type="button" data-platform="macos" aria-pressed="true">macOS</button><button type="button" data-platform="windows" aria-pressed="false">Windows</button></div></div></header>
<div class="hero"><span class="eyebrow">M008 DATABASE / AF001 EXTENSION</span><h1>실행을 저장하고,<br>승인에서 이어갑니다.</h1><p>기존 PostgreSQL·사용자·프로젝트·인증을 재사용하는<br>새 프레임워크 API와 독립 worker의 시작 방법.</p><div class="meta"><span>02 PROCESSES</span><span>06 EXTENSION TABLES</span><span>EXPLICIT MIGRATION</span></div></div>
<article>{body}</article><footer class="footer">개발·테스트용 PostgreSQL 경로 · 실모델·운영 적용은 별도 검증<br>문서는 서버·인터넷 없이 열 수 있습니다.</footer></div></div><div id="copy-status" role="status" aria-live="polite"></div><script>{SCRIPT}</script></body></html>'''
    audit=Audit();audit.feed(html)
    if len(audit.ids)!=len(set(audit.ids)) or audit.resources:raise ValueError('invalid offline guide')
    source.with_suffix('.html').write_text(html,encoding='utf-8')
    print(f'Built {source.with_suffix(".html").name}: {len(headings)} sections')


if __name__=='__main__':build()
