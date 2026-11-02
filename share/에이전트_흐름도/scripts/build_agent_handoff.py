"""Build the offline team handoff from its deliberately small Markdown subset.

No application import, external asset, dependency installation or service call.
Supports headings, paragraphs, flat lists, tables and fenced code used by this
document. Fails on unclosed fences; text is escaped before entering the page.
"""
from html import escape
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "docs/agent-framework"
SOURCE = FOLDER / "에이전트_프레임워크_가이드.md"
TARGET = SOURCE.with_suffix(".html")


def inline(text):
    tokens = []
    def protect(value):
        tokens.append(value)
        return f"\x00{len(tokens)-1}\x00"
    text = re.sub(r"`([^`]+)`", lambda m: protect(f"<code>{escape(m[1])}</code>"), text)
    def link(match):
        label, destination = match[1], match[2]
        if destination.startswith("../../") and destination.endswith(".py"):
            destination = "source.html?file=" + quote(destination[6:], safe="")
        return protect(f'<a href="{escape(destination, quote=True)}">{escape(label)}</a>')
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, text)
    text = escape(text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    for index, value in enumerate(tokens):
        text = text.replace(f"\x00{index}\x00", value)
    return text


def slug(text):
    return re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", text.lower())).strip("-")


def markdown(text):
    lines, out, headings = text.splitlines(), [], []
    position = 0
    while position < len(lines):
        line = lines[position]
        if not line.strip():
            position += 1
            continue
        if line.startswith("```"):
            language = line[3:].strip()
            code = []
            position += 1
            while position < len(lines) and lines[position] != "```":
                code.append(lines[position]); position += 1
            if position == len(lines):
                raise ValueError("unclosed code fence")
            shell = language == "sh"
            label = "TERMINAL" if shell else language.upper()
            out.append(f'<div class="code-block" data-language="{escape(language)}">'
                       f'<div class="code-bar"><span>{escape(label)}</span><button class="copy" type="button">복사</button></div>'
                       f'<pre><code>{escape(chr(10).join(code))}</code></pre></div>')
            position += 1
            continue
        heading = re.match(r"^(#{1,3}) (.+)$", line)
        if heading:
            level, title = len(heading[1]), heading[2]
            identifier = slug(title)
            if level == 2:
                headings.append((identifier, title))
            out.append(f'<h{level} id="{identifier}">{inline(title)}</h{level}>')
            position += 1
            continue
        if line.startswith("|"):
            rows = []
            while position < len(lines) and lines[position].startswith("|"):
                row = [cell.strip() for cell in lines[position].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", cell) for cell in row):
                    rows.append(row)
                position += 1
            header = "".join(f"<th>{inline(cell)}</th>" for cell in rows[0])
            body = "".join("<tr>" + "".join(f"<td>{inline(cell)}</td>" for cell in row) + "</tr>" for row in rows[1:])
            out.append(f'<div class="table-wrap"><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>')
            continue
        if re.match(r"^(?:\d+\. |[-*] )", line):
            ordered = bool(re.match(r"^\d+\. ", line))
            pattern = r"^\d+\. " if ordered else r"^[-*] "
            items = []
            while position < len(lines) and re.match(pattern, lines[position]):
                items.append("<li>" + inline(re.sub(pattern, "", lines[position])) + "</li>")
                position += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
            continue
        paragraph = []
        while position < len(lines) and lines[position].strip():
            if paragraph and re.match(r"^(?:#{1,3} |```|\||\d+\. |[-*] )", lines[position]):
                break
            paragraph.append(lines[position]); position += 1
        out.append("<p>" + inline(" ".join(paragraph)) + "</p>")
    # Section containers make sidebar navigation, printing and observation simple.
    body = "\n".join(out)
    body = body.replace('<h2 ', '</section><section class="chapter"><h2 ')
    return '<section class="intro">' + body + '</section>', headings


STYLE = """
:root{--paper:#f5f5ef;--card:#fffef9;--ink:#19352b;--muted:#69786c;--line:#d9dfd2;--green:#315b48;--lime:#e5edca;font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;color:var(--ink);background:var(--paper);font-synthesis:none}*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:100px}body{margin:0;font-size:14px;line-height:1.85}a{color:var(--green);text-underline-offset:4px}button,input{font:inherit}button{cursor:pointer}a:focus-visible,button:focus-visible{outline:3px solid #8ba461;outline-offset:4px}.shell{max-width:1520px;margin:auto;display:grid;grid-template-columns:260px minmax(0,1fr)}aside{position:sticky;top:0;height:100vh;padding:32px 24px 24px;display:flex;flex-direction:column;border-right:1px solid var(--line)}.brand{font-size:14px;font-weight:750;text-decoration:none;letter-spacing:-.4px}.edition{font:10px ui-monospace,Consolas,monospace;letter-spacing:1.7px;margin:8px 0 30px;color:var(--muted)}.nav-label{font-size:10px;letter-spacing:2px;color:#889582;margin-bottom:10px}.toc{display:flex;flex-direction:column;gap:3px;overflow-y:auto}.toc a{display:block;text-decoration:none;color:var(--muted);font-size:12px;line-height:1.6;padding:9px 11px;border-radius:6px;border-left:2px solid transparent}.toc a:hover{background:#ebefe1}.toc a.active{background:var(--lime);color:var(--ink);border-left-color:var(--green);font-weight:700}.side-foot{margin-top:auto;padding-top:25px;font-size:11px;color:var(--muted)}.content{min-width:0}.topbar{position:sticky;top:0;z-index:10;display:flex;justify-content:space-between;align-items:center;gap:18px;padding:17px 42px;border-bottom:1px solid var(--line);background:#f5f5eff5;backdrop-filter:blur(12px)}.crumb{font-size:11px;letter-spacing:.03em;color:var(--muted)}.tools{display:flex;align-items:center;gap:16px}.tools a{font-size:11px}.platform{display:flex;background:#e9ede2;border-radius:7px;padding:3px;gap:3px}.platform button{font-size:11px;padding:6px 12px;border:0;background:transparent;color:var(--muted);border-radius:5px}.platform button[aria-pressed=true]{background:var(--card);color:var(--ink);box-shadow:0 1px 4px #18362512}.hero{padding:46px 42px 28px;border-bottom:1px solid var(--line)}.eyebrow{font:10px ui-monospace,Consolas,monospace;letter-spacing:2.4px;color:var(--green)}.hero h1{font-size:42px;line-height:1.3;letter-spacing:-1.8px;margin:17px 0}.hero p{color:var(--muted);margin:0;max-width:760px;font-size:14px}.pills{display:flex;gap:8px;flex-wrap:wrap;margin-top:24px}.pills a{border:1px solid #cbd7c3;border-radius:5px;padding:5px 12px;text-decoration:none;font-size:11px;background:var(--card)}.meta{display:flex;gap:28px;margin-top:24px;font:10px ui-monospace,Consolas,monospace;letter-spacing:.4px;color:var(--muted)}article{padding:0 42px 30px}.intro{padding:26px 0 18px}.intro h1{display:none}.intro p:first-of-type{font-size:11px;color:var(--muted)}.chapter{padding:28px 0 34px;border-top:1px solid var(--line)}h2{font-size:23px;line-height:1.5;letter-spacing:-.7px;margin:0 0 22px}h3{font-size:17px;margin:25px 0 12px}p{margin:12px 0;word-break:keep-all;overflow-wrap:anywhere}strong{font-weight:700}code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.87em;background:#e9eee2;padding:2px 5px;border-radius:3px;overflow-wrap:anywhere}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:9px;margin:22px 0;background:var(--card)}table{border-collapse:collapse;width:100%;font-size:12px;line-height:1.75}th{text-align:left;font-size:10px;color:#68795f;letter-spacing:.3px;background:#edf1e5;white-space:nowrap}td,th{padding:13px 15px;border-bottom:1px solid var(--line);vertical-align:top}tr:last-child td{border-bottom:0}td code{background:transparent;padding:0;font-size:11px}.code-block{margin:20px 0;border:1px solid #d1daca;border-radius:8px;overflow:hidden;background:var(--card)}.code-bar{display:flex;align-items:center;justify-content:space-between;padding:8px 14px;background:#ecf0e5;border-bottom:1px solid var(--line);font:10px ui-monospace,Consolas,monospace;color:var(--muted);letter-spacing:1px}.copy{font:11px -apple-system,BlinkMacSystemFont,"Malgun Gothic",sans-serif;letter-spacing:0;border:1px solid #ccd5c3;background:#fffef9;border-radius:4px;padding:3px 10px;color:var(--green)}pre{overflow:auto;margin:0;padding:18px 20px;font-size:12px;line-height:1.9;tab-size:4}pre code{background:none;padding:0;border-radius:0;white-space:pre;overflow-wrap:normal;font-size:inherit}ol,ul{padding-left:24px}li{padding-left:4px;margin:8px 0}.footer{padding:22px 42px 40px;font-size:11px;border-top:1px solid var(--line);color:var(--muted)}#copy-status{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:var(--ink);color:var(--card);padding:9px 18px;border-radius:7px;font-size:12px;z-index:20}#copy-status:empty{display:none}@media(max-width:1050px){.shell{grid-template-columns:210px minmax(0,1fr)}aside{padding:25px 15px}.hero,article{padding-left:28px;padding-right:28px}.topbar{padding:15px 28px}.hero h1{font-size:34px}}@media(max-width:760px){.shell{display:block}aside{position:static;height:auto;border-right:0;border-bottom:1px solid var(--line);padding:20px}.edition{margin-bottom:12px}.toc{display:none}.nav-label,.side-foot{display:none}.topbar{position:static;padding:12px 20px}.crumb{display:none}.tools{width:100%;justify-content:space-between}.hero{padding:30px 20px}article{padding:0 20px}.hero h1{font-size:30px}.meta{gap:12px;flex-wrap:wrap}}@media print{aside,.topbar,.copy,.pills,#copy-status{display:none!important}.shell{display:block}.hero,article{padding-left:0;padding-right:0}body{background:white;font-size:10pt}.hero{padding-top:0}.chapter{break-before:auto}h2{break-after:avoid}.code-block,.table-wrap{break-inside:avoid}pre{white-space:pre-wrap;overflow-wrap:anywhere}pre code{white-space:pre-wrap;font-size:9pt}a{color:inherit;text-decoration:none}.footer{padding:20px 0}}
"""

SCRIPT = r"""
(() => {
  'use strict';
  const blocks=[...document.querySelectorAll('.code-block[data-language="sh"]')];
  const originals=new Map(blocks.map(block=>[block,block.querySelector('code').textContent]));
  function platform(name){
    for(const button of document.querySelectorAll('[data-platform]')) button.setAttribute('aria-pressed',String(button.dataset.platform===name));
    for(const block of blocks){
      const original=originals.get(block);
      block.querySelector('code').textContent=name==='windows'?original.replaceAll('.venv/bin/python','.\\.venv\\Scripts\\python.exe').replace(/^cp /gm,'Copy-Item '):original;
      block.querySelector('.code-bar span').textContent=name==='windows'?'WINDOWS / POWERSHELL':'MACOS / TERMINAL';
    }
  }
  for(const button of document.querySelectorAll('[data-platform]')) button.addEventListener('click',()=>platform(button.dataset.platform));
  platform('macos');
  let timer;
  const status=document.getElementById('copy-status');
  function fallbackCopy(value){
    const area=document.createElement('textarea');area.value=value;area.style.position='fixed';area.style.opacity='0';document.body.append(area);area.select();
    const copied=document.execCommand('copy');area.remove();if(!copied)throw new Error('copy unavailable');
  }
  for(const button of document.querySelectorAll('.copy')) button.addEventListener('click',async()=>{
    const value=button.closest('.code-block').querySelector('code').textContent;
    try{if(navigator.clipboard?.writeText){try{await navigator.clipboard.writeText(value);}catch(_){fallbackCopy(value);}}else fallbackCopy(value);status.textContent='명령을 복사했습니다.';}
    catch(_){status.textContent='코드를 선택한 뒤 직접 복사해주세요.';}
    clearTimeout(timer);timer=setTimeout(()=>{status.textContent='';},2000);button.focus();
  });
  const links=[...document.querySelectorAll('.toc a')];
  if('IntersectionObserver' in window){
    const observer=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting){for(const link of links){const active=link.getAttribute('href')==='#'+entry.target.id;link.classList.toggle('active',active);if(active)link.setAttribute('aria-current','location');else link.removeAttribute('aria-current');}}},{rootMargin:'-8% 0px -72% 0px'});
    for(const heading of document.querySelectorAll('article h2'))observer.observe(heading);
  }
})();
"""


class Audit(HTMLParser):
    def __init__(self):
        super().__init__(); self.ids = []; self.links = []; self.resources = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs: self.ids.append(attrs["id"])
        if tag == "a": self.links.append(attrs.get("href", ""))
        if tag in {"script", "img", "iframe", "link"} and (attrs.get("src") or attrs.get("href")):
            self.resources.append(attrs.get("src") or attrs.get("href"))


def build():
    body, headings = markdown(SOURCE.read_text(encoding="utf-8"))
    toc = "".join(f'<a href="#{escape(identifier)}">{escape(title)}</a>' for identifier, title in headings)
    pills = "".join(f'<a href="#{escape(identifier)}">{escape(title.split(" — ")[0].split(". ")[-1])}</a>'
                    for identifier, title in headings if re.search(r"A0[234678]", title))
    html = f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>담당자별 연결 가이드 · Creative Checker</title><style>{STYLE}</style></head>
<body><div class="shell"><aside><a class="brand" href="index.html">Creative Checker<span aria-hidden="true"> ↗</span></a><div class="edition">DEVELOPER HANDOFF / 01</div>
<div class="nav-label">CONTENTS</div><nav class="toc" aria-label="문서 목차">{toc}</nav>
<div class="side-foot">Git 저장소 기준 · 2026.11.02<br>공통 계약 2.0.0<br><a href="flow.html">업무 흐름도 열기 ↗</a></div></aside>
<div class="content"><header class="topbar"><span class="crumb">FRAMEWORK / TEAM INTEGRATION</span><div class="tools"><a href="에이전트_프레임워크_가이드.md">Markdown 원본 ↗</a><div class="platform" role="group" aria-label="실행 명령 운영체제"><button type="button" data-platform="macos" aria-pressed="true">macOS</button><button type="button" data-platform="windows" aria-pressed="false">Windows</button></div></div></header>
<div class="hero"><span class="eyebrow">ONE CONTRACT. EACH ROLE.</span><h1>담당자별 연결 가이드.</h1><p>어디를 수정하고, 무엇을 반환하며, 어떻게 확인하는지.<br>각 역할의 시작 코드부터 테스트와 승인·재검수 연결까지 정리했습니다.</p><div class="pills">{pills}</div><div class="meta"><span>04 ROLE ADAPTERS</span><span>06 WORKFLOW ACTIONS</span><span>GIT SOURCE OF TRUTH</span></div></div>
<article>{body}</article><footer class="footer">Markdown 원본에서 생성한 문서 · 코드 링크는 같은 저장소의 소스 뷰어로 연결됩니다.<br>메모리 개발 도구와 PostgreSQL API·worker를 구분합니다. 실모델·관계 조회와 운영 배포 여부는 실행 증거로 확인합니다.</footer></div></div><div id="copy-status" role="status" aria-live="polite"></div><script>{SCRIPT}</script></body></html>
'''
    audit = Audit(); audit.feed(html)
    if len(audit.ids) != len(set(audit.ids)) or audit.resources:
        raise ValueError("duplicate IDs or automatic external resources")
    if any(link[1:] not in audit.ids for link in audit.links if link.startswith("#")):
        raise ValueError("unresolved table-of-contents anchor")
    TARGET.write_text(html, encoding="utf-8")
    (FOLDER / "team-integration-guide.html").write_text(
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta http-equiv="refresh" content="0;url=에이전트_프레임워크_가이드.html">'
        '<title>에이전트 프레임워크 가이드</title></head><body>'
        '<a href="에이전트_프레임워크_가이드.html">한글 이름의 가이드 열기</a></body></html>\n',
        encoding="utf-8")
    (FOLDER / "team-integration-guide.md").write_text(
        '# 문서 위치 변경\n\n[에이전트 프레임워크 가이드](에이전트_프레임워크_가이드.md)로 이동했습니다.\n',
        encoding="utf-8")
    print(f"Built {TARGET.relative_to(ROOT)}: {len(headings)} sections, {len(audit.links)} links, external resources 0")


if __name__ == "__main__":
    build()
