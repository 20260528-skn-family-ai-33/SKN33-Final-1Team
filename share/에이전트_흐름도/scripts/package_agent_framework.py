"""Build an offline, portable team handoff from an explicit file allowlist.

Run: python -m scripts.package_agent_framework
Only Python's standard library is needed. No application, DB or model is loaded.
"""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import re
import tempfile
from urllib.parse import unquote, urlsplit
import zipfile

from scripts.build_agent_flow_html import build as build_flow
from scripts.build_business_flow import build as build_business_flow

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs/agent-framework"
RELEASE = "agent-framework-2026-11-02"
PLAN = "pdf-qwen-rag-workflow-plan-2026-11-02"
STYLE = """:root{font-family:-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;color:#203d34;background:#f4f3ee;font-size:16px;line-height:1.8}
*{box-sizing:border-box}body{max-width:1160px;margin:auto;padding:48px 40px 70px}nav{display:flex;gap:24px;flex-wrap:wrap;border-bottom:1px solid #d4dccf;padding-bottom:20px}a{color:#315b48;text-underline-offset:5px}h1{font-size:42px;letter-spacing:-.04em;line-height:1.3;margin:44px 0 20px}h2{font-size:22px;margin:36px 0 14px}.eyebrow{letter-spacing:.2em;font-size:11px;font-weight:bold;margin-top:40px}.lead{font-size:18px;max-width:850px}.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;margin:32px 0}.card{border:1px solid #cdd7c8;background:#fffefb;border-radius:12px;padding:28px;text-decoration:none}.card strong{display:block;font-size:22px;margin-bottom:12px}.card span{color:#64766c}pre{overflow:auto;padding:20px;background:#fffefb;border:1px solid #d4dccf;border-radius:10px;line-height:1.65}code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.88em}li{margin:8px 0}.note{border-left:3px solid #315b48;padding:12px 20px;background:#e9eee3}footer{margin-top:45px;padding-top:20px;border-top:1px solid #d4dccf;color:#64766c;font-size:13px}
"""


def page(title, body):
    return (f'<!doctype html>\n<html lang="ko"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{title} · Creative Checker</title><style>{STYLE}</style></head>'
            '<body><nav><a href="index.html">공유 자료</a><a href="flow.html">업무 흐름</a>'
            f'<a href="../planning/{PLAN}.html">전체 계획</a><a href="guide.html">개발 안내</a></nav>'
            f'<main>{body}</main><footer>Creative Checker · 공통 프레임워크 · 2026-11-02 · '
            'HTML 열람에는 서버·Python·인터넷 연결이 필요하지 않습니다.</footer></body></html>\n')


def build_pages():
    (DOCS / "index.html").write_text(page("팀 공유 자료", f"""
<p class="eyebrow">A SHARED MAP FOR INDEPENDENT DEVELOPMENT</p>
<h1>같은 흐름에서,<br>각자의 역할을 개발합니다.</h1>
<p class="lead">PDF·Qwen·PostgreSQL·Neo4j 기반 RAG와 승인·재검수의 전체 계획, 현재 공통 프레임워크, 코드 관계를 한곳에서 확인하세요.</p>
<div class="cards">
<a class="card" href="../../index.html"><strong>01 / 통합문서 기반 업무 흐름</strong><span>사람·시스템·근거 3개 레인 · 승인·비교·이력 · 문서 반영 기준</span></a>
<a class="card" href="../planning/{PLAN}.html"><strong>02 / 전체 계획</strong><span>목표 구조, 역할별 계약, 저장·복구, 구현 단계와 완료 기준</span></a>
<a class="card" href="graphify-out/graph.html"><strong>03 / Graphify 코드 지도</strong><span>현재 Python 코드의 클래스·함수·호출 관계</span></a>
<a class="card" href="guide.html"><strong>04 / 개발 시작하기</strong><span>macOS·Windows 실행 명령과 담당별 연결 위치</span></a>
</div>
<p><a href="flow.html">공유 코드 기준의 현재 프레임워크 지도 ↗</a></p><h2>열람 방법</h2><ol><li>ZIP이라면 먼저 <strong>전체 압축을 해제</strong>하세요. 압축 프로그램 안에서 HTML만 바로 열지 마세요.</li>
<li>압축 해제한 폴더의 <code>index.html</code>을 Chrome으로 엽니다. Git으로 받은 경우 <code>docs/agent-framework/index.html</code>을 엽니다.</li>
<li>Mac·Windows 모두 동일합니다. HTML과 하위 폴더를 함께 이동하면 링크가 유지됩니다. 코드 근거 링크는 브라우저에서 행 번호까지 표시합니다.</li></ol>
<p class="note">현재 제공 범위는 공통 계약·합성 어댑터·메모리 기반 승인 및 재검수입니다. 실제 PDF/Qwen·PostgreSQL/Neo4j 검색·MCP·영속 저장·front-v2 연결은 담당자가 구현할 후속 범위입니다.</p>
<p>파일 목록과 무결성 정보는 ZIP의 <code>MANIFEST.json</code>에 있습니다. 외부 기술 문서 링크를 열 때만 인터넷 연결이 필요합니다.</p>
"""), encoding="utf-8")
    (DOCS / "guide.html").write_text(page("개발 시작하기", """
<p class="eyebrow">COMMON CONTRACTS / INDEPENDENT ADAPTERS</p><h1>개발 시작하기.</h1>
<p class="lead">HTML만 읽을 때는 설치가 필요 없습니다. 합성 데모와 테스트를 실행하려면 Python 3.12 또는 3.13과 uv를 준비하세요.</p>
<p>ZIP은 문서와 공통 프레임워크 개발 소스 묶음입니다. 전체 제품 서버·front-v2·운영 DB는 포함하지 않습니다. 팀의 제품 통합 작업은 Git 저장소에서 진행하세요.</p>
<h2>1. 공통 의존성 설치</h2><p>터미널에서 <code>pyproject.toml</code>과 <code>uv.lock</code>이 있는 폴더로 이동합니다. ZIP은 압축을 해제한 최상위 폴더입니다.</p>
<pre><code>uv sync --locked --group dev</code></pre><p>의존성을 처음 설치할 때는 인터넷이 필요합니다. 아래 명령은 가상환경을 별도로 활성화하지 않아도 실행됩니다.</p>
<h2>2. macOS 터미널</h2><pre><code>.venv/bin/python -m backend.agent_framework demo
.venv/bin/python -m pytest tests/agent_framework -q</code></pre>
<h2>3. Windows PowerShell</h2><pre><code>.\\.venv\\Scripts\\python.exe -m backend.agent_framework demo
.\\.venv\\Scripts\\python.exe -m pytest tests/agent_framework -q</code></pre>
<h2>4. 담당 역할 연결</h2><p>공통 포트에 맞춘 어댑터를 만든 뒤 factory를 지정합니다. <code>A02</code>, <code>A03</code>, <code>A04_A05</code>, <code>A06</code>, <code>A07</code> 단독 실행과 합성 예제를 제공합니다.</p>
<ul><li><a href="source.html?file=docs%2Fagent-framework%2FREADME.md">전체 개발 가이드와 역할·계약 규칙</a></li>
<li><a href="source.html?file=backend%2Fagent_framework%2Fports.py#L1">입출력 포트와 어댑터 연결 규격</a></li>
<li><a href="source.html?file=examples%2Fagent_framework%2FREADME.md">합성 JSON 예제 안내</a></li>
<li><a href="source.html?file=docs%2Fagent-framework%2Fverification-2026-11-02.md">기존 로컬 검증 기록</a></li></ul>
<p class="note">demo의 사람 승인은 테스트 값이고 실행 상태는 메모리에만 저장됩니다. 실제 모델·DB 연결이나 운영 권한·복구를 검증하는 명령이 아닙니다.</p>
<h2>자료 다시 만들기</h2><p>문서나 공통 소스를 수정하면 저장소 루트에서 다음 명령으로 HTML 스냅샷과 공유 ZIP을 함께 갱신합니다. Python 표준 라이브러리만 사용합니다.</p>
<pre><code>python -m scripts.package_agent_framework</code></pre>
<p>Graphify 코드 지도는 기존 AST 스냅샷입니다. 코드 구조가 바뀌면 별도 환경에서 <code>scripts/build_agent_code_graph.py</code>로 먼저 갱신하세요. 전체 계획 HTML과 MD는 함께 관리합니다.</p>
"""), encoding="utf-8")


def allowed_files():
    paths = {".gitattributes", "backend/__init__.py", "PRODUCT.md", "pyproject.toml", "uv.lock",
             ".github/workflows/agent-framework.yml", "scripts/build_agent_code_graph.py",
             "scripts/build_agent_flow_html.py", "scripts/export_agent_framework.py",
             "scripts/package_agent_framework.py", "scripts/build_business_flow.py", "scripts/templates/business-flow.html.in",
             "docs/reference/creative-checker-master-2026-10-30.html", "scripts/templates/agent-flow.html.in",
             "scripts/templates/agent-flow.html", f"docs/planning/{PLAN}.md", f"docs/planning/{PLAN}.html"}
    for folder, extensions in (("backend/agent_framework", {".py"}),
                               ("tests/agent_framework", {".py"}),
                               ("examples/agent_framework", {".py", ".json", ".md"}),
                               ("docs/agent-framework", {".html", ".json", ".md", ".txt"})):
        paths.update(p.relative_to(ROOT).as_posix() for p in (ROOT / folder).rglob("*")
                     if p.is_file() and p.suffix in extensions and "__pycache__" not in p.parts)
    for name in sorted(paths):
        source = ROOT / name
        if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(ROOT):
            raise ValueError(f"Missing or unsafe bundle source: {name}")
        yield name, source.read_bytes()


class HtmlLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        for attr in ("href", "src"):
            if attrs.get(attr):
                self.links.append((tag, attr, attrs[attr]))


def validate_tree(root):
    """Check portable names, case-exact local links and offline HTML resources."""
    files = {p.relative_to(root).as_posix(): p for p in root.rglob("*") if p.is_file()}
    folded = set()
    for name in files:
        if name.casefold() in folded:
            raise ValueError(f"Case-insensitive name collision: {name}")
        folded.add(name.casefold())
        for part in PurePosixPath(name).parts:
            if (re.search(r'[<>:"\\|?*\x00-\x1f]', part) or part.endswith((".", " "))
                    or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part)):
                raise ValueError(f"Windows-incompatible filename: {name}")
    local_count = external_count = 0
    for name, path in files.items():
        if path.suffix != ".html":
            continue
        parser = HtmlLinks()
        parser.feed(path.read_text(encoding="utf-8"))
        for tag, attr, value in parser.links:
            url = urlsplit(value)
            if url.scheme in ("http", "https"):
                if tag != "a":
                    raise ValueError(f"External auto-loaded resource in {name}: {value}")
                external_count += 1
                continue
            if url.scheme or url.netloc or url.path.startswith(("/", "\\")) or "\\" in url.path:
                raise ValueError(f"Nonportable URL in {name}: {value}")
            target = (path.parent / unquote(url.path)).resolve() if url.path else path.resolve()
            if not target.is_relative_to(root.resolve()) or not target.is_file():
                raise ValueError(f"Missing bundled link in {name}: {value}")
            if target.relative_to(root.resolve()).as_posix() not in files:
                raise ValueError(f"Case-mismatched bundled link in {name}: {value}")
            if not url.path and url.fragment and unquote(url.fragment) not in parser.ids:
                raise ValueError(f"Missing page anchor in {name}: {value}")
            local_count += 1
    return {"files": len(files), "local_html_links": local_count,
            "external_reference_links": external_count, "external_auto_loaded_resources": 0,
            "windows_filename_check": "passed", "relative_link_check": "passed",
            "browser_rendering_verified": False, "windows_execution_verified": False}


def build():
    build_pages()
    build_flow()
    build_business_flow()
    payload = dict(allowed_files())
    payload["index.html"] = (ROOT / "index.html").read_bytes()
    payload["README.md"] = """# Creative Checker 팀 공유 묶음

1. ZIP 전체를 압축 해제하세요. 압축 프로그램 안에서 HTML만 열지 마세요.
2. 이 폴더의 `index.html`을 Chrome으로 여세요. macOS·Windows 공통입니다.
3. 폴더 구조를 유지하세요. HTML·스타일·그래프·코드 뷰어가 오프라인으로 열립니다.

전체 계획, 업무 흐름, Graphify AST 코드 지도, 개발 가이드, 공통 프레임워크,
합성 예제, 스키마, 테스트와 재생성 스크립트를 포함합니다.
전체 제품 서버/front-v2/운영 DB/실모델은 포함하지 않습니다.
실제 모델·검색·영속 저장·인증 연결은 후속 개발 범위입니다.

개발 명령은 `docs/agent-framework/guide.html`을 보세요.
`pyproject.toml`·`uv.lock`은 저장소 정본을 그대로 담았습니다.
MANIFEST.json의 SHA-256으로 각 파일의 무결성을 확인할 수 있습니다.
작성자의 로컬 Codex 채팅은 배포하지 않으며 계획에 출처 ID만 기록했습니다.
""".encode()
    with tempfile.TemporaryDirectory(prefix="agent-share-") as tmp:
        staging = Path(tmp) / "공유 자료 with spaces" / RELEASE
        for name, content in payload.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        checks = validate_tree(staging)
    manifest = {"release": RELEASE, "entrypoint": "index.html", "scope": "offline documentation and common framework development subset",
                "verification": checks, "files": [{"path": name, "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest()} for name, content in sorted(payload.items())]}
    payload["MANIFEST.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    share = ROOT / "share"
    share.mkdir(exist_ok=True)
    archive = share / f"{RELEASE}.zip"
    with tempfile.NamedTemporaryFile(dir=share, suffix=".tmp", delete=False) as temp:
        temporary = Path(temp.name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            for name, content in sorted(payload.items()):
                info = zipfile.ZipInfo(f"{RELEASE}/{name}", date_time=(2026, 11, 2, 0, 0, 0))
                info.create_system = 0  # Portable regular files; no Unix links or resource forks.
                info.compress_type = zipfile.ZIP_DEFLATED
                bundle.writestr(info, content)
        with zipfile.ZipFile(temporary) as bundle:
            if bundle.testzip() is not None:
                raise ValueError("ZIP CRC verification failed")
        temporary.replace(archive)
    finally:
        temporary.unlink(missing_ok=True)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(json.dumps({"archive": archive.relative_to(ROOT).as_posix(), "bytes": archive.stat().st_size,
                      "sha256": digest, "verification": checks}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    build()
