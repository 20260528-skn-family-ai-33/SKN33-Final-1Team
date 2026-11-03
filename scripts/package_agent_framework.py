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
<a class="card" href="Mac_Windows_공통실행_가이드.html"><strong>Mac / Windows 공통 실행</strong><span>같은 Linux 컨테이너 · PDF 볼륨 · API·worker · RunPod SSH 터널</span></a>
<a class="card" href="flow.html"><strong>01 / 업무 흐름</strong><span>4개 구성 역할 · 공통 입력 고정 · 조건부 승인·재검수</span></a>
<a class="card" href="../planning/{PLAN}.html"><strong>02 / 전체 계획</strong><span>목표 구조, 역할별 계약, 저장·복구, 구현 단계와 완료 기준</span></a>
<a class="card" href="graphify-out/graph.html"><strong>03 / Graphify 코드 지도</strong><span>현재 Python 코드의 클래스·함수·호출 관계</span></a>
<a class="card" href="guide.html"><strong>04 / 개발 시작하기</strong><span>역할별 구성 · 설정 진단 · macOS·Windows 개발 API 실행</span></a>
<a class="card" href="에이전트_프레임워크_가이드.html"><strong>05 / 담당자별 연결 가이드</strong><span>수정 파일 · factory · 역할별 테스트 · 실제 기능 인수 순서</span></a>
<a class="card" href="에이전트_프레임워크_PostgreSQL_실행가이드.html"><strong>06 / PostgreSQL 실행·worker</strong><span>기존 DB · 실행·단계·승인 기록 · 독립 worker · lease 복구</span></a>
<a class="card" href="A02_PDF_Qwen_저장승인_구조설계.html"><strong>07 / A02 연결 구조 설계</strong><span>PDF · Qwen GPU 서버 · 기존 DB · 기준 승인·재조회 · 구현 예정안</span></a>
<a class="card" href="PDF_등록_버전저장_가이드.html"><strong>08 / PDF 등록·버전 저장</strong><span>4단계 구현 · AF002 · 원본 해시 · 프로젝트 권한 · 재조회</span></a>
<a class="card" href="A02_조건추출_연결가이드.html"><strong>09 / A02 조건 추출 연결</strong><span>5단계 구현 · PDF-only · 별도 worker · 출처 검증 · 미승인 초안</span></a>
<a class="card" href="기준_보완승인_가이드.html"><strong>10 / 기준 보완·승인</strong><span>6단계 구현 · AF003 · 보완 출처 · 기준 버전 · 승인·재조회</span></a>
<a class="card" href="포스터_검수재검수_가이드.html"><strong>11 / 포스터 검수·재검수</strong><span>7단계 구현 · 기존 이미지 저장 · 승인 기준 고정 · 새 입력 확인 · 독립 worker</span></a>
</div>
<h2>열람 방법</h2><ol><li>Git으로 받은 프로젝트에서 <code>docs/agent-framework/index.html</code>을 Chrome으로 엽니다.</li>
<li>개발할 때는 프로젝트 루트의 코드·예제·테스트를 사용합니다. share 흐름도는 열람용 사본이며 개발은 루트 코드를 기준으로 합니다. 기존 ZIP은 이전 이력입니다.</li>
<li>Mac·Windows 모두 동일합니다. HTML과 하위 폴더를 함께 이동하면 링크가 유지됩니다. 코드 근거 링크는 브라우저에서 행 번호까지 표시합니다.</li></ol>
<p class="note">공통 계약·파일 접근·메모리 개발 도구에 더해 기존 DB·인증을 사용하는 PostgreSQL API·worker를 제공합니다. 실모델·실검색·Neo4j projection·front-v2·운영 배포는 후속 범위입니다.</p>
<p>담당자별 연결 위치와 인수 명령은 <a href="에이전트_프레임워크_가이드.html">담당자 가이드</a>에서 확인합니다. 외부 기술 문서 링크를 열 때만 인터넷 연결이 필요합니다.</p>
"""), encoding="utf-8")
    (DOCS / "guide.html").write_text(page("개발 시작하기", """
<p class="eyebrow">COMMON CONTRACTS / INDEPENDENT ADAPTERS</p><h1>개발 시작하기.</h1>
<p class="lead">HTML만 읽을 때는 설치가 필요 없습니다. 합성 데모와 테스트를 실행하려면 Python 3.12 또는 3.13과 uv를 준비하세요.</p>
<p>Git 저장소의 프로젝트 루트가 개발 기준입니다. <a href="에이전트_프레임워크_가이드.html">담당자별 연결 가이드</a>에서 수정할 파일과 테스트 명령을 확인하세요.</p>
<h2>1. 공통 의존성 설치</h2><p>터미널에서 <code>pyproject.toml</code>과 <code>uv.lock</code>이 있는 프로젝트 루트로 이동합니다.</p>
<pre><code>uv sync --locked --group dev</code></pre><p>의존성을 처음 설치할 때는 인터넷이 필요합니다. 아래 명령은 가상환경을 별도로 활성화하지 않아도 실행됩니다.</p>
<h2>2. macOS 터미널</h2><pre><code>.venv/bin/python -m backend.agent_framework demo
.venv/bin/python -m pytest tests/agent_framework -q</code></pre>
<h2>3. Windows PowerShell</h2><pre><code>.\\.venv\\Scripts\\python.exe -m backend.agent_framework demo
.\\.venv\\Scripts\\python.exe -m pytest tests/agent_framework -q</code></pre>
<h2>4. 담당 역할 연결</h2><p>공통 포트에 맞춘 어댑터를 만든 뒤 factory를 지정합니다. <code>A02</code>, <code>A04_A05</code>, <code>A06</code>, <code>A07</code> 단독 실행과 합성 예제를 제공합니다.</p>
<ul><li><a href="source.html?file=docs%2Fagent-framework%2FREADME.md">전체 개발 가이드와 역할·계약 규칙</a></li>
<li><a href="source.html?file=backend%2Fagent_framework%2Fports.py#L1">입출력 포트와 어댑터 연결 규격</a></li>
<li><a href="source.html?file=examples%2Fagent_framework%2FREADME.md">합성 JSON 예제 안내</a></li>
<li><a href="source.html?file=docs%2Fagent-framework%2Fverification-2026-11-02.md">기존 로컬 검증 기록</a></li></ul>
<h2>5. 역할별 설정과 개발 API</h2><p>전체 역할을 한 factory에 묶지 않고, 담당 역할의 factory만 설정할 수 있습니다. 설정을 검사하는 doctor는 모듈을 import하거나 모델·DB에 연결하지 않습니다.</p>
<pre><code># macOS
.venv/bin/python -m backend.agent_framework doctor --config examples/agent_framework/team_config.synthetic.json
.venv/bin/python -m backend.agent_framework role A04_A05 --input examples/agent_framework/A04_A05.json --config examples/agent_framework/team_config.synthetic.json
.venv/bin/python -m backend.agent_framework serve --config examples/agent_framework/team_config.synthetic.json --port 8012

# Windows PowerShell
.\\.venv\\Scripts\\python.exe -m backend.agent_framework doctor --config examples/agent_framework/team_config.synthetic.json
.\\.venv\\Scripts\\python.exe -m backend.agent_framework serve --config examples/agent_framework/team_config.synthetic.json --port 8012</code></pre>
<p>serve는 별도 개발 API를 127.0.0.1에서 실행합니다. 구성 파일의 factory는 신뢰된 로컬 코드만 지정하세요. 역할 단독 실행은 선택한 역할만 초기화하고, API 시작은 전체 역할을 초기화합니다. 자동 승인이나 실서비스 연결 확인을 수행하지 않습니다.</p>
<ul><li><a href="source.html?file=examples%2Fagent_framework%2Fteam_config.synthetic.json">합성 전체 구성</a></li><li><a href="source.html?file=examples%2Fagent_framework%2Fteam_config.template.json">미설정 역할 템플릿</a></li><li><a href="source.html?file=examples%2Fagent_framework%2Fteam_adapters.py">역할별 factory 시작 코드</a></li></ul>
<p class="note">demo의 사람 승인은 테스트 값이고 실행 상태는 메모리에만 저장됩니다. 실제 모델·DB 연결이나 운영 권한·복구를 검증하는 명령이 아닙니다.</p>
<h2>6. 담당 어댑터의 계약 검사</h2><p>check는 선택한 역할에 여러 요청 예제를 넣고 기대한 결과와 비교합니다. 정상 불합격·확인 필요 판정도 유효한 결과로 검사하며, 호출 결과가 불명확하면 후속 사례를 중단합니다.</p>
<pre><code># macOS
.venv/bin/python -m backend.agent_framework check A06 --config examples/agent_framework/team_config.synthetic.json --cases examples/agent_framework/checks/A06.suite.json

# Windows PowerShell
.\\.venv\\Scripts\\python.exe -m backend.agent_framework check A06 --config examples/agent_framework/team_config.synthetic.json --cases examples/agent_framework/checks/A06.suite.json</code></pre>
<p>기본 묶음은 4개 역할의 합성 사례 15개입니다. 실제 PDF·포스터 파일은 포함하지 않으므로 실서비스 어댑터에는 별도의 개발 입력을 준비하세요. 상대 입력 경로는 suite 파일 기준이며, <code>--output 보고서.json</code>을 지정하면 실패 결과도 저장합니다. 보고서의 passed는 기대와의 일치이고 실모델 정확도나 DB 연결 증명이 아닙니다.</p>
<h2>7. 실제 파일 읽기 연결부 확인</h2><p>FileAccess는 기존 파일·버전·권한 조회와 저장소를 재사용하고 실제 바이트의 해시·형식·치수를 검사합니다. 아래 예제는 생성 PDF·PNG를 기존 파일 서비스로 임시 저장한 뒤 읽으며, 운영 DB·모델에 연결하지 않습니다.</p>
<pre><code># macOS
.venv/bin/python -m examples.agent_framework.file_access_demo

# Windows PowerShell
.\\.venv\\Scripts\\python.exe -m examples.agent_framework.file_access_demo</code></pre>
<p>재구축 creative_checker 경로는 PDF·포스터, M008의 기존 catalog는 포스터를 지원합니다. AF002를 활성화하면 <a href="PDF_등록_버전저장_가이드.html">새 PDF 등록·버전 저장</a>을 사용할 수 있습니다. M008의 기존 POSIX 저장소는 Windows 지원 대상이 아닙니다. 제품 API에 자동 연결하지 않으며 인증이 확인된 주체와 기존 저장소를 호출자가 주입합니다. 원본 바이트와 EXIF 표시 좌표는 구분합니다.</p>
<h2>8. 단계별 실행 기록</h2><p>demo 출력의 <code>runs[].step_records</code> 또는 같은 Workflow 객체의 <code>get_steps(run_id)</code>에서 역할별 상태·UTC 시작/종료 시각·소요 시간·오류 코드를 확인합니다. 개발 API는 <code>GET /api/framework/v1/runs/실행ID/steps</code>를 제공합니다. 어댑터가 실행 중이어도 조회할 수 있으며 시작·승인 요청은 동기식으로 완료를 기다립니다.</p>
<p>상태는 running·completed·failed·outcome_unknown·reused로 구분합니다. 재검수의 A02는 부모 실행의 조건 재사용을 표시하며 새로운 호출 시간이나 소요 시간을 만들지 않습니다. 오류 뒤에는 후속 역할을 실행하지 않고 자동 재시도도 하지 않습니다. 사람 승인 대기는 역할 소요 시간에 포함하지 않습니다. 단계 completed는 검수 통과나 최종 승인이 아닙니다.</p>
<p class="note">demo·serve·check-flow는 메모리 개발 도구입니다. 역할 단독 role/check 명령은 Workflow 이력을 만들지 않습니다. 기존 DB·인증을 사용하는 postgres-serve·worker는 <a href="에이전트_프레임워크_PostgreSQL_실행가이드.html">별도 PostgreSQL 실행가이드</a>를 따릅니다. 운영 배포·실모델·front-v2는 후속 단계입니다.</p>
<h2>9. 설정한 어댑터의 전체 흐름 검증</h2><p>check-flow는 구성한 네 역할로 조건 승인→검사·보고→수정 승인→새 버전 전체 재검수→최종 테스트 승인을 확인합니다. 오류·기대 불일치·결과 불명 뒤에는 후속 동작을 건너뛰며 자동 재시도하지 않습니다.</p>
<pre><code># macOS
.venv/bin/python -m backend.agent_framework check-flow --config examples/agent_framework/team_config.synthetic.json --case examples/agent_framework/workflow.synthetic.json
.venv/bin/python -m examples.agent_framework.file_flow_demo

# Windows PowerShell
.\\.venv\\Scripts\\python.exe -m backend.agent_framework check-flow --config examples/agent_framework/team_config.synthetic.json --case examples/agent_framework/workflow.synthetic.json
.\\.venv\\Scripts\\python.exe -m examples.agent_framework.file_flow_demo</code></pre>
<p>--output 보고서.json으로 상태·판정·단계 시간·오류·정규 JSON 해시를 남깁니다. 전체 기대 일치 0, 실행 실패·불일치 1, 입력·설정 오류 2를 반환합니다. 기본 사례는 실제 미디어가 없는 합성 참조이므로 담당 어댑터에는 별도 개발 입력을 준비하세요.</p>
<p>file_flow_demo는 생성 PDF·두 PNG를 기존 파일 저장소와 임시 SQLite에 등록하고 설정 factory·파일 검증·텍스트/픽셀 테스트 콜백으로 불합격→수정본 통과→테스트 최종 승인을 확인합니다. 모델 호출은 0회이며 승인 기준 출처 고정·합성 보고·테스트 승인을 사용합니다. 예제의 임시 factory 의존성은 상시 서비스 설정용이 아닙니다.</p>
<h2>자료 다시 만들기</h2><p>문서나 공통 소스를 수정하면 프로젝트 루트에서 HTML을 갱신하여 Git으로 공유합니다. ZIP 생성은 필요하지 않습니다.</p>
<pre><code>python -m scripts.build_agent_flow_html
python -m scripts.build_agent_handoff
python -c "from scripts.package_agent_framework import build_pages; build_pages()"</code></pre>
<p>Graphify 코드 지도는 기존 AST 스냅샷입니다. 코드 구조가 바뀌면 별도 환경에서 <code>scripts/build_agent_code_graph.py</code>로 먼저 갱신하세요. 전체 계획 HTML과 MD는 함께 관리합니다.</p>
"""), encoding="utf-8")


def allowed_files():
    paths = {".gitattributes", "backend/__init__.py", "tests/__init__.py", "PRODUCT.md", "pyproject.toml", "uv.lock",
             ".github/workflows/agent-framework.yml", "scripts/build_agent_code_graph.py",
             "scripts/build_agent_flow_html.py", "scripts/export_agent_framework.py",
             "scripts/package_agent_framework.py", "scripts/templates/agent-flow.html.in",
             "scripts/templates/agent-flow.html", f"docs/planning/{PLAN}.md", f"docs/planning/{PLAN}.html"}
    # File-access adapters reuse these existing modules. Include their minimal
    # dependency closure so an extracted framework bundle can run its tests and
    # isolated file demo. Do not include product API/worker entrypoints or data.
    paths.update("backend/creative_checker/" + name + ".py" for name in (
        "__init__", "contracts", "database", "models", "settings", "storage", "tool_records"))
    paths.update(p.relative_to(ROOT).as_posix()
                 for p in (ROOT / "backend/creative_checker/services").glob("*.py"))
    paths.update({"backend/app/__init__.py", "backend/app/storage/__init__.py", "backend/app/storage/local.py",
                  "backend/app/hashing/__init__.py", "backend/app/hashing/canonical.py",
                  "backend/app/security/__init__.py", "backend/app/security/errors.py"})
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
    payload = dict(allowed_files())
    payload["index.html"] = ("<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
        "<meta http-equiv=\"refresh\" content=\"0;url=docs/agent-framework/index.html\">"
        "<title>Creative Checker 공유 자료</title></head><body>"
        "<a href=\"docs/agent-framework/index.html\">공유 자료 열기</a></body></html>\n").encode()
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
