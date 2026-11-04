"""Render the offline A02 usage guide; never start services or access a database."""
from html import escape
from pathlib import Path

from scripts.build_agent_handoff import Audit, SCRIPT, STYLE, markdown


def build():
    source = Path(__file__).resolve().parents[1] / 'docs/agent-framework/A02_실행_사용가이드.md'
    body, headings = markdown(source.read_text(encoding='utf-8'))
    toc = ''.join(f'<a href="#{escape(identifier)}">{escape(title)}</a>' for identifier, title in headings)
    html = f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>A02 실행·사용 가이드 · Creative Checker</title><style>{STYLE}
.intro h1{{display:block;font-size:34px;line-height:1.35;letter-spacing:-1px;margin:14px 0}}
</style></head>
<body><div class="shell"><aside><a class="brand" href="README.md">Creative Checker ↗</a>
<div class="edition">A02 / DESKTOP RUNBOOK</div><nav class="toc" aria-label="실행 순서 목차">{toc}</nav>
<div class="side-foot">2026.11.04 · Mac / Windows<br>Docker · Qwen · PostgreSQL<br>기존 front-v2 데스크톱</div></aside>
<div class="content"><header class="topbar"><span class="crumb">DOCKER → QWEN → PDF → REVIEW BRIEF</span>
<a href="{source.name}">Markdown 원본 ↗</a></header><article>{body}</article>
<footer class="footer">저장소 상대 경로 · 외부 자원 자동 로드 없음 · 코드 블록 복사 지원<br>
A02 추출·문단 반영과 사람의 승인·포스터 실제 판정을 구분합니다.</footer></div></div>
<div id="copy-status" role="status" aria-live="polite"></div><script>{SCRIPT}</script></body></html>
'''
    audit = Audit()
    audit.feed(html)
    if audit.resources or len(audit.ids) != len(set(audit.ids)):
        raise ValueError('Invalid offline guide: external resources or duplicate IDs')
    if any(link[1:] not in audit.ids for link in audit.links if link.startswith('#')):
        raise ValueError('Unresolved table-of-contents anchor')
    source.with_suffix('.html').write_text(html, encoding='utf-8', newline='\n')
    print(f'Built A02 usage guide: {len(headings)} sections, external resources 0')


if __name__ == '__main__':
    build()
