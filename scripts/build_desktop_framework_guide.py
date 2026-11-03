"""Render the Mac/Windows common runtime guide without starting any service."""
from html import escape
from pathlib import Path
from scripts.build_agent_handoff import markdown,STYLE,Audit


def build():
    source=Path(__file__).resolve().parents[1]/'docs/agent-framework/Mac_Windows_공통실행_가이드.md'
    body,headings=markdown(source.read_text(encoding='utf-8'))
    toc=''.join(f'<a href="#{escape(identifier)}">{escape(title)}</a>' for identifier,title in headings)
    html=f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Mac·Windows 공통 실행 · Creative Checker</title><style>{STYLE}</style></head><body><div class="shell"><aside><a class="brand" href="index.html">Creative Checker ↗</a><div class="edition">MAC / WINDOWS / LINUX RUNTIME</div><nav class="toc">{toc}</nav><div class="side-foot">2026.11.03<br>공통 실행 · 원본 설정 보존</div></aside><div class="content"><header class="topbar"><span class="crumb">DESKTOP / DOCKER / RUNPOD</span><a href="{source.name}">Markdown 원본 ↗</a></header><div class="hero"><span class="eyebrow">THE SAME RUNTIME. ON EVERY DESKTOP.</span><h1>Mac과 Windows에서,<br>같은 흐름을 실행합니다.</h1><p>PC는 공통 실행기, 파일·API·worker는 Linux 컨테이너,<br>모델 추론은 RunPod에서 맡습니다.</p></div><article>{body}</article><footer class="footer">Mac Docker 실실행과 Windows/Linux CI를 확인했습니다.<br>Windows Docker Desktop 실기기·실제 Qwen 추론은 후속 검증입니다.</footer></div></div></body></html>'''
    audit=Audit();audit.feed(html)
    if audit.resources or len(audit.ids)!=len(set(audit.ids)):raise ValueError('Invalid offline guide')
    if any(link[1:] not in audit.ids for link in audit.links if link.startswith('#')):raise ValueError('Unresolved section')
    source.with_suffix('.html').write_text(html,encoding='utf-8',newline='\n')
    print('Built common desktop runtime guide:',len(headings),'sections')


if __name__=='__main__':build()
