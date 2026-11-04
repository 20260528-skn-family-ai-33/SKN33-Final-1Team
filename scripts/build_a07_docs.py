"""Render the A07 documents (contract proposal, progress note) as offline HTML, without app I/O."""
from dataclasses import dataclass
from pathlib import Path
from html import escape
from urllib.parse import quote
import re

from backend.a07_reporter.prompt import SYSTEM_PROMPT
from scripts.build_agent_handoff import markdown, STYLE, SCRIPT, Audit

PROPOSAL_DIAGRAM = '''<section class="proposal-flow" aria-label="실행 기록에서 A07 보고 요청으로 넘어가는 값. 지금 넘기는 두 값과 제안으로 추가하는 두 값">
<div class="flow-head"><strong>실행 기록에 이미 있는 값과 A07이 받는 값</strong><span>제안 · 공통 코드 미수정</span></div>
<div class="flow-grid"><div class="flow-card"><small>01 / RUN SNAPSHOT</small><strong>실행 기록</strong><ul><li><code>validated</code><em>A06 판정</em></li><li><code>evidence</code><em>기준 원문</em></li><li><code>criteria</code><em>승인 조건</em></li><li><code>inspection</code><em>검사 관측</em></li></ul></div><span class="flow-arrow" aria-hidden="true">→</span><div class="flow-card"><small>02 / REPORT REQUEST</small><strong>보고 요청</strong><ul><li><code>validated</code><em>지금</em></li><li><code>evidence</code><em>지금</em></li><li class="add"><code>criteria</code><em>추가 · 선택</em></li><li class="add"><code>inspection</code><em>추가 · 선택</em></li></ul></div><span class="flow-arrow" aria-hidden="true">→</span><div class="flow-card"><small>03 / A07 REPORT</small><strong>보고 초안</strong><ul><li>판정<em>A06 그대로</em></li><li>기준 원문<em>인용</em></li><li class="add">기준값 · 필수 여부<em>추가</em></li><li class="add">읽은 값 · 위치<em>추가</em></li></ul></div></div>
<p class="flow-note">추가하는 두 필드는 비어 있어도 됩니다. 판정 규칙과 다른 역할의 계약은 바뀌지 않습니다.</p></section>'''
PROGRESS_DIAGRAM = '''<section class="proposal-flow" aria-label="A07 작업 세 갈래의 상태. 규칙 기반 보고기와 계약 3.0.0 연결은 완료, Qwen 문장 다듬기는 초안 코드와 서버 대기">
<div class="flow-head"><strong>A07 작업 세 갈래</strong><span>2026-11-03 기준 · 점선은 아직 끝나지 않은 일</span></div>
<div class="flow-grid three"><div class="flow-card"><small>01 / RULE REPORTER</small><strong>규칙 기반 보고기</strong><ul><li>판정 순서 정렬<em>완료</em></li><li>기준 원문 인용<em>완료</em></li><li>수정 · 확인 요청<em>완료</em></li><li>check A07 6사례<em>통과</em></li></ul></div><div class="flow-card"><small>02 / CONTRACT 3.0.0</small><strong>A06 PR #53 연결</strong><ul><li>새 사유 코드 7개<em>완료</em></li><li>기준 · 읽은 값 · 위치 줄<em>완료</em></li><li>PR #53 병합 · main 합침<em>완료</em></li><li>check A07 6사례(3.0.0)<em>통과</em></li></ul></div><div class="flow-card"><small>03 / QWEN POLISH</small><strong>문장 다듬기</strong><ul><li>프롬프트 · 응답 검사<em>초안</em></li><li>호출 코드(가짜 서버)<em>초안</em></li><li class="wait">팀 공용 Qwen 서버<em>대기</em></li><li class="wait">읽기 평가(팀원)<em>대기</em></li></ul></div></div>
<p class="flow-note">사실(조건 ID · 판정 · 기준 원문)은 코드가 쓰고, Qwen은 설명과 할 일 두 문장만 씁니다. 실제 Qwen 서버로는 아직 돌려 보지 않았습니다.</p></section>'''
EXTRA = '''.proposal-flow{margin:28px 42px 0;padding:24px;background:var(--card);border:1px solid var(--line);border-radius:12px}.flow-head{display:flex;justify-content:space-between;flex-wrap:wrap;gap:12px;font-size:13px;margin-bottom:20px}.flow-head span{font-size:11px;color:var(--muted)}.flow-grid{display:grid;grid-template-columns:minmax(0,1fr) 20px minmax(0,1fr) 20px minmax(0,1fr);gap:10px}.flow-grid.three{grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.flow-card{min-width:0;border:1px solid #cfdbc7;padding:17px;border-radius:8px;background:#f7f9ef}.flow-card small{font:9px ui-monospace,Consolas,monospace;letter-spacing:.8px;color:var(--muted)}.flow-card strong{display:block;font-size:15px;margin:9px 0 12px}.flow-card ul{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}.flow-card li{display:flex;justify-content:space-between;align-items:baseline;gap:8px;margin:0;padding:6px 10px;font-size:12px;line-height:1.6;border:1px solid var(--line);border-radius:6px;background:var(--card)}.flow-card li code{background:none;padding:0}.flow-card li em{font-style:normal;font-size:10px;color:var(--muted);white-space:nowrap}.flow-card li.add{border:1px dashed var(--green);background:var(--lime)}.flow-card li.add em{color:var(--green);font-weight:700}.flow-card li.wait{border-style:dashed;background:transparent;color:var(--muted)}.flow-arrow{align-self:center;text-align:center;font-size:20px;color:var(--green)}.flow-note{font-size:12px;color:var(--muted);margin:16px 0 0}blockquote{margin:16px 0;padding:12px 18px;border-left:3px solid var(--green);border-radius:0 8px 8px 0;background:var(--card);color:var(--muted)}blockquote p{margin:0}@media(max-width:1050px){.proposal-flow{margin:24px 28px 0}}@media(max-width:760px){.proposal-flow{margin:20px 20px 0;padding:18px}.flow-grid,.flow-grid.three{grid-template-columns:minmax(0,1fr)}.flow-arrow{transform:rotate(90deg)}}@media print{.proposal-flow{margin:20px 0;break-inside:avoid}}'''


@dataclass(frozen=True)
class Page:
    source: str
    title: str
    edition: str
    side: str
    crumb: str
    eyebrow: str
    heading: str
    lead: str
    diagram: str
    must_contain: tuple[str, ...] = ()   # 코드와 같아야 하는 본문(예: 프롬프트)


PAGES = (
    Page('A07_보고요청_계약보완_제안.md', 'A07 보고 요청 계약 보완 제안', 'A07 / CONTRACT PROPOSAL',
         '2026.11.03 · 제안(전달 안 함)<br>A06 PR #53 병합으로 불필요', 'A07 / REPORT REQUEST / CONTRACT 2.0.0',
         'SAME VERDICT. A CLEARER REPORT.', '보고 요청에<br>조건과 관측을 더합니다.',
         'A07이 기준값과 포스터에서 읽은 값을 함께 보여 주도록 <code>ReportRequest</code>에 선택 필드 두 개를 추가하자는 제안입니다.'
         '<br>A06 PR #53이 같은 정보를 넘기게 되어 전달하지 않았고, 기록으로 남긴 문서입니다.', PROPOSAL_DIAGRAM),
    Page('A07_보고_진행정리_2026-11-03.md', 'A07 보고 진행 정리', 'A07 / PROGRESS NOTE',
         '2026.11.03 · 진행 정리<br>푸시 전 · Qwen 서버 미연결', 'A07 / REPORTER / PROGRESS',
         'FACTS BY CODE. WORDS BY QWEN.', 'A07 보고,<br>여기까지 왔습니다.',
         '규칙 기반 보고기, A06 PR #53(계약 3.0.0) 연결, Qwen 문장 다듬기 초안까지의 정리입니다.'
         '<br>작업은 <code>feat/a07-reporter</code> 브랜치에 있고 푸시는 하지 않았습니다.', PROGRESS_DIAGRAM,
         must_contain=(SYSTEM_PROMPT,)),
)


def prepare(text, folder):
    # 코드 줄 링크는 같은 폴더의 소스 뷰어로, HTML 판이 있는 문서 링크는 HTML 판으로 연결한다.
    text = re.sub(r"\]\(\.\./\.\./([^)#]+\.py)#L(\d+)\)",
                  lambda m: f"](source.html?file={quote(m[1], safe='')}#L{m[2]})", text)
    def page(match):
        name, anchor = match[1], match[2]
        if not (folder / f"{name}.html").exists() and name not in {Path(p.source).stem for p in PAGES}:
            return match[0]
        return f"]({name}.html" + (f"#{re.sub(r'-+', '-', anchor)})" if anchor else ")")
    text = re.sub(r"\]\(([^)/#]+)\.md(?:#([^)]+))?\)", page, text)
    # 공통 inline()은 링크 글자 안의 `코드`를 되돌리지 못하므로(NUL이 남는다) 링크 글자에서만 백틱을 뺀다.
    return re.sub(r"\[([^\]]*`[^\]]*)\]\(", lambda m: "[" + m[1].replace("`", "") + "](", text)


def render(folder, page):
    source = folder / page.source
    text = source.read_text(encoding='utf-8')
    if any(part not in text for part in page.must_contain):
        raise ValueError(f'{source.name}: text differs from the code it documents')
    body, headings = markdown(prepare(text, folder))
    # 작은 Markdown 변환기가 지원하지 않는 인용문과 표 칸 안 줄바꿈만 여기서 처리한다.
    body = re.sub(r"<p>&gt; (.+?)</p>", r"<blockquote><p>\1</p></blockquote>", body).replace('&lt;br&gt;', '<br>')
    toc = ''.join(f'<a href="#{escape(identifier)}">{escape(title)}</a>' for identifier, title in headings)
    html = f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(page.title)} · Creative Checker</title><style>{STYLE}{EXTRA}</style></head><body><div class="shell"><aside><a class="brand" href="index.html">Creative Checker ↗</a><div class="edition">{page.edition}</div><nav class="toc" aria-label="문서 목차">{toc}</nav><div class="side-foot">{page.side}</div></aside><div class="content"><header class="topbar"><span class="crumb">{page.crumb}</span><div class="tools"><a href="{source.name}">Markdown 원본 ↗</a><a href="에이전트_프레임워크_가이드.html">공통 가이드 ↗</a></div></header><div class="hero"><span class="eyebrow">{page.eyebrow}</span><h1>{page.heading}</h1><p>{page.lead}</p></div>{page.diagram}<article>{body}</article><footer class="footer">Markdown 정본에서 생성 · 서버 없이 열람 · 외부 자동 로드 없음 · 합의 전 제안과 미연결 기능은 본문에 따로 표시했습니다.</footer></div></div><div id="copy-status" role="status" aria-live="polite"></div><script>{SCRIPT}</script></body></html>'''
    audit = Audit(); audit.feed(html)
    if audit.resources or len(audit.ids) != len(set(audit.ids)) or '\x00' in html:
        raise ValueError('external resources, duplicate IDs or unreplaced inline token')
    if any(link[1:] not in audit.ids for link in audit.links if link.startswith('#')):
        raise ValueError('unresolved section link')
    return source.with_suffix('.html'), html, len(headings), audit.links


def build():
    folder = Path(__file__).resolve().parents[1] / 'docs/agent-framework'
    built = [render(folder, page) for page in PAGES]
    written = {target.name: html for target, html, _, _ in built}
    for target, html, sections, links in built:
        # 다른 HTML 문서로 가는 링크는 이번에 만든 문서나 이미 있는 문서의 실제 제목 위치로 가야 한다.
        for link in links:
            name, _, anchor = link.partition('#')
            if name.endswith('.html') and '?' not in name:
                other = Audit(); other.feed(written.get(name) or (folder / name).read_text(encoding='utf-8'))
                if anchor and anchor not in other.ids:
                    raise ValueError(f'{target.name}: unresolved link {link}')
        target.write_text(html, encoding='utf-8')
        print('Built', target.name, ':', sections, 'sections,', len(links), 'links')


if __name__ == '__main__':
    build()
