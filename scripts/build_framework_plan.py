"""Render the current framework plan as offline HTML from its Markdown source."""
from pathlib import Path
from html import escape

from scripts.build_agent_handoff import markdown, STYLE, SCRIPT, Audit


def build():
    folder = Path(__file__).resolve().parents[1] / 'docs/planning'
    source = folder / 'pdf-qwen-rag-workflow-plan-2026-11-02.md'
    body, headings = markdown(source.read_text(encoding='utf-8'))
    toc = ''.join(f'<a href="#{escape(identifier)}">{escape(title)}</a>' for identifier, title in headings)
    html = f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>프레임워크 전체 계획 · Creative Checker</title><style>{STYLE}</style></head>
<body><div class="shell"><aside><a class="brand" href="../agent-framework/index.html">Creative Checker ↗</a><div class="edition">FRAMEWORK PLAN / 2026.11.02</div><nav class="toc" aria-label="문서 목차">{toc}</nav><div class="side-foot">A03 폐기 · 네 구성 역할<br>공통 입력 준비와 관계 조회 목표 구분</div></aside><div class="content"><header class="topbar"><span class="crumb">PDF / QWEN / APPROVAL WORKFLOW</span><div class="tools"><a href="{source.name}">Markdown 원본 ↗</a><a href="../agent-framework/에이전트_프레임워크_가이드.html">담당자 연결 가이드 ↗</a></div></header><div class="hero"><span class="eyebrow">CURRENT DECISIONS. EXPLICIT BOUNDARIES.</span><h1>승인 기준부터,<br>새 버전의 재검수까지.</h1><p>11월 2일 통합문서의 역할·관계 문맥·승인 정책을 반영한 전체 계획입니다.<br>현재 구현과 실제 모델·Neo4j·제품 연결의 남은 범위를 구분합니다.</p></div><article>{body}</article><footer class="footer">Markdown 정본에서 생성 · 외부 자동 로드 없음 · 운영 배포 증거와 별도</footer></div></div><script>{SCRIPT}</script></body></html>'''
    audit = Audit()
    audit.feed(html)
    if audit.resources or len(audit.ids) != len(set(audit.ids)):
        raise ValueError('external resources or duplicate IDs')
    if any(link[1:] not in audit.ids for link in audit.links if link.startswith('#')):
        raise ValueError('unresolved plan anchor')
    source.with_suffix('.html').write_text(html, encoding='utf-8')
    print('Built framework plan:', len(headings), 'sections')


if __name__ == '__main__':
    build()
