"""Render the offline A02/front-v2 paragraph guide without a model or DB call."""
from html import escape
from pathlib import Path
from scripts.build_agent_handoff import markdown,STYLE,Audit


def build():
    source=Path(__file__).resolve().parents[1]/'docs/agent-framework/A02_프론트_요구사항_문단연결.md'
    body,headings=markdown(source.read_text(encoding='utf-8'))
    toc=''.join(f'<a href="#{escape(i)}">{escape(title)}</a>' for i,title in headings)
    html=f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>A02 · 요구사항 문단 연결</title><style>{STYLE}</style></head><body><div class="shell"><aside><a class="brand" href="index.html">Creative Checker ↗</a><div class="edition">A02 / FRONT-V2</div><nav class="toc">{toc}</nav></aside><div class="content"><header class="topbar"><span>PDF → QWEN → REVIEW BRIEF</span><a href="{source.name}">Markdown 원본 ↗</a></header><article>{body}</article></div></div></body></html>'''
    audit=Audit();audit.feed(html)
    if audit.resources or len(audit.ids)!=len(set(audit.ids)):raise ValueError('invalid offline guide')
    if any(link[1:] not in audit.ids for link in audit.links if link.startswith('#')):raise ValueError('unresolved section')
    source.with_suffix('.html').write_text(html,encoding='utf-8',newline='\n')
    print('Built A02 frontend guide:',len(headings),'sections')


if __name__=='__main__':build()
