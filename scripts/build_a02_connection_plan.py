"""Render the A02 structure proposal as an offline HTML document, without app I/O."""
from pathlib import Path
from html import escape

from scripts.build_agent_handoff import markdown, STYLE, SCRIPT, Audit

DIAGRAM = '''<section class="architecture" aria-label="계획 구조. API, A02 worker, 별도 Qwen 서버와 기존 PostgreSQL">
<div class="architecture-head"><strong>연결할 세 계층</strong><span>PDF 등록 구현 · Qwen/승인 연결은 후속 · 운영 미적용</span></div>
<div class="architecture-flow"><div class="architecture-card"><small>01 / EXISTING AUTH + API</small><strong>PDF 등록 · 추출 접수</strong><p>세션·프로젝트 권한 확인<br>문서·파일·실행 ID 발급</p></div><span class="architecture-arrow" aria-hidden="true">→</span><div class="architecture-card"><small>02 / FRAMEWORK WORKER</small><strong>A02 · PDF/Qwen 연결</strong><p>파일·출처·응답 검증<br>모델 호출 중 DB 잠금 해제</p></div><span class="architecture-arrow" aria-hidden="true">⇄</span><div class="architecture-card server"><small>03 / GPU MODEL SERVICE</small><strong>Qwen3.5-4B 서버</strong><p>OpenAI 호환 추론 API<br>서버 주소 · 아직 미구축</p></div></div>
<div class="architecture-store"><strong>기존 PostgreSQL + 기존 파일 저장 기능</strong><span>PDF 원본·메타데이터 → 조건 초안 → 사람의 기준 승인 → 승인 버전 재조회</span></div>
<p class="architecture-note">Qwen은 추론만 수행합니다. DB 저장·승인·권한은 업무 API와 프레임워크가 담당합니다. 독립 A03은 추가하지 않습니다.</p></section>'''
EXTRA = '''.architecture{margin:28px 42px 0;padding:24px;background:#fffef9;border:1px solid var(--line);border-radius:12px}.architecture-head{display:flex;justify-content:space-between;gap:12px;font-size:13px;margin-bottom:20px}.architecture-head span{font-size:11px;color:var(--muted)}.architecture-flow{display:grid;grid-template-columns:minmax(0,1fr) 20px minmax(0,1fr) 20px minmax(0,1fr);align-items:center;gap:10px}.architecture-card{border:1px solid #cfdbc7;padding:17px;border-radius:8px;min-height:140px;background:#f7f9ef}.architecture-card small{font:9px ui-monospace,Consolas,monospace;letter-spacing:.8px;color:var(--muted)}.architecture-card strong{display:block;font-size:15px;margin:9px 0}.architecture-card p{font-size:12px;line-height:1.75;margin:0;color:var(--muted)}.architecture-card.server{border-style:dashed;background:#fbf5e8}.architecture-arrow{text-align:center;font-size:20px;color:var(--green)}.architecture-store{display:flex;flex-direction:column;gap:6px;margin-top:18px;padding:17px;border-radius:8px;background:#e8eddf;font-size:13px}.architecture-store span,.architecture-note{font-size:12px;color:var(--muted)}.architecture-note{margin:16px 0 0}@media print{.architecture{margin:20px 0;break-inside:avoid}}'''


def build():
    folder = Path(__file__).resolve().parents[1] / 'docs/agent-framework'
    source = folder / 'A02_PDF_Qwen_저장승인_구조설계.md'
    body, headings = markdown(source.read_text(encoding='utf-8'))
    toc = ''.join(f'<a href="#{escape(identifier)}">{escape(title)}</a>' for identifier, title in headings)
    html = f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>A02 연결 구조 설계 · Creative Checker</title><style>{STYLE}{EXTRA}</style></head><body><div class="shell"><aside><a class="brand" href="index.html">Creative Checker ↗</a><div class="edition">A02 / STRUCTURE PROPOSAL</div><nav class="toc" aria-label="문서 목차">{toc}</nav><div class="side-foot">2026.11.03 · 단계별 구현<br>PDF 등록 구현 · Qwen/승인은 후속</div></aside><div class="content"><header class="topbar"><span class="crumb">PDF / QWEN / POSTGRESQL</span><div class="tools"><a href="{source.name}">Markdown 원본 ↗</a><a href="에이전트_프레임워크_가이드.html">공통 가이드 ↗</a></div></header><div class="hero"><span class="eyebrow">ONE DOCUMENT. A TRACEABLE APPROVAL.</span><h1>PDF에서 조건을 만들고,<br>승인한 기준을 다시 읽습니다.</h1><p>A02와 별도 Qwen 서버, 기존 PostgreSQL을 연결할 구조입니다.<br>PDF 등록은 4단계에 구현했으며 Qwen 추출·승인 연결은 후속입니다.</p></div>{DIAGRAM}<article>{body}</article><footer class="footer">Markdown 정본에서 생성 · 서버 없이 열람 · 외부 자동 로드 없음 · 실제 추론/배포 결과와 구분</footer></div></div><script>{SCRIPT}</script></body></html>'''
    audit = Audit(); audit.feed(html)
    if audit.resources or len(audit.ids) != len(set(audit.ids)):
        raise ValueError('external resources or duplicate IDs')
    if any(link[1:] not in audit.ids for link in audit.links if link.startswith('#')):
        raise ValueError('unresolved section link')
    target = source.with_suffix('.html')
    target.write_text(html, encoding='utf-8')
    print('Built', target.name, ':', len(headings), 'sections,', len(audit.links), 'links')


if __name__ == '__main__':
    build()
