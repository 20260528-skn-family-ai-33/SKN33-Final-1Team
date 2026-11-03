"""Render the A07 report-request contract proposal as an offline HTML document, without app I/O."""
from pathlib import Path
from html import escape
from urllib.parse import quote
import re

from scripts.build_agent_handoff import markdown, STYLE, SCRIPT, Audit

DIAGRAM = '''<section class="proposal-flow" aria-label="실행 기록에서 A07 보고 요청으로 넘어가는 값. 지금 넘기는 두 값과 제안으로 추가하는 두 값">
<div class="flow-head"><strong>실행 기록에 이미 있는 값과 A07이 받는 값</strong><span>제안 · 공통 코드 미수정</span></div>
<div class="flow-grid"><div class="flow-card"><small>01 / RUN SNAPSHOT</small><strong>실행 기록</strong><ul><li><code>validated</code><em>A06 판정</em></li><li><code>evidence</code><em>기준 원문</em></li><li><code>criteria</code><em>승인 조건</em></li><li><code>inspection</code><em>검사 관측</em></li></ul></div><span class="flow-arrow" aria-hidden="true">→</span><div class="flow-card"><small>02 / REPORT REQUEST</small><strong>보고 요청</strong><ul><li><code>validated</code><em>지금</em></li><li><code>evidence</code><em>지금</em></li><li class="add"><code>criteria</code><em>추가 · 선택</em></li><li class="add"><code>inspection</code><em>추가 · 선택</em></li></ul></div><span class="flow-arrow" aria-hidden="true">→</span><div class="flow-card"><small>03 / A07 REPORT</small><strong>보고 초안</strong><ul><li>판정<em>A06 그대로</em></li><li>기준 원문<em>인용</em></li><li class="add">기준값 · 필수 여부<em>추가</em></li><li class="add">읽은 값 · 위치<em>추가</em></li></ul></div></div>
<p class="flow-note">추가하는 두 필드는 비어 있어도 됩니다. 판정 규칙과 다른 역할의 계약은 바뀌지 않습니다.</p></section>'''
EXTRA = '''.proposal-flow{margin:28px 42px 0;padding:24px;background:var(--card);border:1px solid var(--line);border-radius:12px}.flow-head{display:flex;justify-content:space-between;flex-wrap:wrap;gap:12px;font-size:13px;margin-bottom:20px}.flow-head span{font-size:11px;color:var(--muted)}.flow-grid{display:grid;grid-template-columns:minmax(0,1fr) 20px minmax(0,1fr) 20px minmax(0,1fr);gap:10px}.flow-card{min-width:0;border:1px solid #cfdbc7;padding:17px;border-radius:8px;background:#f7f9ef}.flow-card small{font:9px ui-monospace,Consolas,monospace;letter-spacing:.8px;color:var(--muted)}.flow-card strong{display:block;font-size:15px;margin:9px 0 12px}.flow-card ul{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}.flow-card li{display:flex;justify-content:space-between;align-items:baseline;gap:8px;margin:0;padding:6px 10px;font-size:12px;line-height:1.6;border:1px solid var(--line);border-radius:6px;background:var(--card)}.flow-card li code{background:none;padding:0}.flow-card li em{font-style:normal;font-size:10px;color:var(--muted);white-space:nowrap}.flow-card li.add{border:1px dashed var(--green);background:var(--lime)}.flow-card li.add em{color:var(--green);font-weight:700}.flow-arrow{align-self:center;text-align:center;font-size:20px;color:var(--green)}.flow-note{font-size:12px;color:var(--muted);margin:16px 0 0}blockquote{margin:16px 0;padding:12px 18px;border-left:3px solid var(--green);border-radius:0 8px 8px 0;background:var(--card);color:var(--muted)}blockquote p{margin:0}@media(max-width:1050px){.proposal-flow{margin:24px 28px 0}}@media(max-width:760px){.proposal-flow{margin:20px 20px 0;padding:18px}.flow-grid{grid-template-columns:minmax(0,1fr)}.flow-arrow{transform:rotate(90deg)}}@media print{.proposal-flow{margin:20px 0;break-inside:avoid}}'''


def prepare(text, folder):
    # 코드 줄 링크는 같은 폴더의 소스 뷰어로, HTML 판이 있는 문서 링크는 HTML 판으로 연결한다.
    text = re.sub(r"\]\(\.\./\.\./([^)#]+\.py)#L(\d+)\)",
                  lambda m: f"](source.html?file={quote(m[1], safe='')}#L{m[2]})", text)
    def page(match):
        name, anchor = match[1], match[2]
        if not (folder / f"{name}.html").exists():
            return match[0]
        return f"]({name}.html#{re.sub(r'-+', '-', anchor)})"
    text = re.sub(r"\]\(([^)/#]+)\.md#([^)]+)\)", page, text)
    # 공통 inline()은 링크 글자 안의 `코드`를 되돌리지 못하므로(NUL이 남는다) 링크 글자에서만 백틱을 뺀다.
    return re.sub(r"\[([^\]]*`[^\]]*)\]\(", lambda m: "[" + m[1].replace("`", "") + "](", text)


def build():
    folder = Path(__file__).resolve().parents[1] / 'docs/agent-framework'
    source = folder / 'A07_보고요청_계약보완_제안.md'
    body, headings = markdown(prepare(source.read_text(encoding='utf-8'), folder))
    # 작은 Markdown 변환기가 지원하지 않는 인용문과 표 칸 안 줄바꿈만 여기서 처리한다.
    body = re.sub(r"<p>&gt; (.+?)</p>", r"<blockquote><p>\1</p></blockquote>", body).replace('&lt;br&gt;', '<br>')
    toc = ''.join(f'<a href="#{escape(identifier)}">{escape(title)}</a>' for identifier, title in headings)
    html = f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>A07 보고 요청 계약 보완 제안 · Creative Checker</title><style>{STYLE}{EXTRA}</style></head><body><div class="shell"><aside><a class="brand" href="index.html">Creative Checker ↗</a><div class="edition">A07 / CONTRACT PROPOSAL</div><nav class="toc" aria-label="문서 목차">{toc}</nav><div class="side-foot">2026.11.03 · 제안(합의 전)<br>공통 코드는 아직 바꾸지 않음</div></aside><div class="content"><header class="topbar"><span class="crumb">A07 / REPORT REQUEST / CONTRACT 2.0.0</span><div class="tools"><a href="{source.name}">Markdown 원본 ↗</a><a href="에이전트_프레임워크_가이드.html">공통 가이드 ↗</a></div></header><div class="hero"><span class="eyebrow">SAME VERDICT. A CLEARER REPORT.</span><h1>보고 요청에<br>조건과 관측을 더합니다.</h1><p>A07이 기준값과 포스터에서 읽은 값을 함께 보여 주도록 <code>ReportRequest</code>에 선택 필드 두 개를 추가하자는 제안입니다.<br>판정 규칙과 다른 역할의 계약은 그대로이며, 공통 기반 담당과 합의하기 전 문서입니다.</p></div>{DIAGRAM}<article>{body}</article><footer class="footer">Markdown 정본에서 생성 · 서버 없이 열람 · 외부 자동 로드 없음 · 합의 전 제안이며 공통 코드에는 반영되지 않았습니다.</footer></div></div><div id="copy-status" role="status" aria-live="polite"></div><script>{SCRIPT}</script></body></html>'''
    audit = Audit(); audit.feed(html)
    if audit.resources or len(audit.ids) != len(set(audit.ids)) or '\x00' in html:
        raise ValueError('external resources, duplicate IDs or unreplaced inline token')
    if any(link[1:] not in audit.ids for link in audit.links if link.startswith('#')):
        raise ValueError('unresolved section link')
    for link in audit.links:
        name, _, anchor = link.partition('#')
        if name.endswith('.html') and '?' not in name and anchor:
            other = Audit(); other.feed((folder / name).read_text(encoding='utf-8'))
            if anchor not in other.ids:
                raise ValueError(f'unresolved link {link}')
    target = source.with_suffix('.html')
    target.write_text(html, encoding='utf-8')
    print('Built', target.name, ':', len(headings), 'sections,', len(audit.links), 'links')


if __name__ == '__main__':
    build()
