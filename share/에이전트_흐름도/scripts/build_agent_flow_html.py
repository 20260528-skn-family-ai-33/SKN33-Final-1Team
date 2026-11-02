"""Build the standalone workflow explorer; no app import, model or DB connection."""
import json
from html.parser import HTMLParser
from hashlib import sha256
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "docs" / "agent-framework"


def load_flow():
    data = json.loads((FOLDER / "flow-data.json").read_text(encoding="utf-8"))
    ids = {n["id"] for n in data["nodes"]}
    if len(ids) != len(data["nodes"]):
        raise ValueError("duplicate node ID")
    for node in data["nodes"]:
        if node["kind"] not in {"agent", "approval", "input", "output", "dependency"}:
            raise ValueError("invalid node kind")
        if node["status"] not in {"ready", "adapter", "planned"}:
            raise ValueError("invalid node status")
        for reference in node["files"]:
            path = (ROOT / reference["path"]).resolve()
            if not path.is_relative_to(ROOT) or not path.is_file():
                raise ValueError(f"invalid source: {reference}")
            if not 1 <= reference["line"] <= len(path.read_text(encoding="utf-8").splitlines()):
                raise ValueError(f"invalid source line: {reference}")
    for role in data["roles"]:
        if not set(role["nodes"]) <= ids:
            raise ValueError("role filter has unknown workflow nodes")
    edges = {(e["from"], e["to"]) for e in data["edges"]}
    if any(a not in ids or b not in ids for a, b in edges):
        raise ValueError("dangling workflow edge")
    for name, path in data["scenarios"].items():
        if not path or not set(path) <= ids:
            raise ValueError(f"invalid scenario {name}")
        if not all((a, b) in edges for a, b in zip(path, path[1:])):
            raise ValueError(f"disconnected scenario {name}")
    return data


def embedded_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def portable_source_path(name):
    """Only repository-relative, normalized paths may enter the offline viewer."""
    path = PurePosixPath(name)
    return bool(path.parts) and not path.is_absolute() and str(path) == name and not any(
        part in {".", ".."} or part.endswith((" ", ".")) or any(c in part for c in '\\:*?"<>|') or any(ord(c) < 32 for c in part)
        for part in path.parts
    )


def source_payload(data):
    names = {reference["path"] for node in data["nodes"] for reference in node["files"]}
    names.update({"docs/agent-framework/graphify-out/GRAPH_REPORT.md",
                  "docs/agent-framework/verification-2026-11-02.md",
                  "docs/agent-framework/a03-retirement-2026-11-02.md",
                  "examples/agent_framework/README.md",
                  "examples/agent_framework/team_adapters.py",
                  "examples/agent_framework/team_config.synthetic.json",
                  "examples/agent_framework/team_config.template.json"})
    names.update(path.relative_to(ROOT).as_posix() for path in (ROOT / "backend" / "agent_framework").rglob("*.py"))
    sources = {}
    for name in sorted(names):
        path = (ROOT / name).resolve()
        if not portable_source_path(name) or not path.is_relative_to(ROOT) or not path.is_file():
            raise ValueError(f"invalid portable source path: {name}")
        sources[name] = {"text": path.read_text(encoding="utf-8"), "sha256": sha256(path.read_bytes()).hexdigest()}
    return sources


def render_sources(sources):
    if not sources or any(not portable_source_path(name) for name in sources):
        raise ValueError("source viewer requires repository-relative source paths")
    template = r'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Creative Checker · 코드 근거</title><style>
:root{font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;color:#172c24;background:#f5f5ef}*{box-sizing:border-box}body{margin:0;min-width:1000px}a{color:inherit;text-underline-offset:4px}header{padding:22px 32px;border-bottom:1px solid #d8ded3;display:flex;justify-content:space-between;gap:24px}nav{display:flex;gap:24px;font-size:13px}main{max-width:1640px;margin:auto;padding:32px}h1{font-size:28px;margin:0 0 12px}.note{color:#66776d;font-size:13px;line-height:1.8}.filebar{display:flex;gap:16px;align-items:center;margin:24px 0 12px}select{font:13px ui-monospace,Consolas,monospace;max-width:100%;padding:12px;border:1px solid #cdd9cb;background:#fffef9;border-radius:7px;color:inherit;flex:1}.hash{font:11px ui-monospace,Consolas,monospace;overflow-wrap:anywhere;color:#66776d}.code{margin-top:20px;border:1px solid #d8ded3;border-radius:10px;background:#fffef9;overflow:auto;padding:16px 0}.line{display:flex;min-width:max-content;font:12px/1.8 ui-monospace,SFMono-Regular,Consolas,monospace;scroll-margin:20px}.line.selected,.line:target{background:#e3ecc2}.number{width:65px;flex-shrink:0;text-align:right;padding-right:20px;color:#899585;text-decoration:none;user-select:none}.text{white-space:pre;padding-right:24px}.error{padding:20px;background:#fff0e8;border:1px solid #c48d76}button{font:inherit}a:focus-visible,select:focus-visible{outline:3px solid #77994b;outline-offset:3px}
</style></head><body><header><strong>Creative Checker · 코드 근거</strong><nav><a href="index.html">공유 시작</a><a href="flow.html">업무 흐름</a><a href="guide.html">개발 가이드</a></nav></header>
<main><h1>흐름과 연결된 소스.</h1><p class="note">저장소 기준 상대 경로와 실제 줄 번호입니다. 소스가 이 HTML에 내장되어 있어 macOS·Windows에서 서버 없이 열 수 있습니다.<br>생성 시점의 코드 사본이며, 실제 모델·DB 실행 결과를 나타내지는 않습니다.</p>
<div class="filebar"><label for="file">파일</label><select id="file" aria-label="소스 파일 선택"></select></div><p id="path" class="note"></p><p id="hash" class="hash"></p><p id="error" class="error" hidden></p><div id="code" class="code" aria-label="소스 코드"></div>
</main><script type="application/json" id="source-data">__SOURCES__</script><script>
(() => {
  'use strict';
  const sources=JSON.parse(document.getElementById('source-data').textContent);
  const paths=Object.keys(sources), select=document.getElementById('file'), code=document.getElementById('code');
  for(const path of paths){const option=document.createElement('option');option.value=path;option.textContent=path;select.append(option);}
  const requested=new URLSearchParams(location.search).get('file'), path=requested===null?paths[0]:requested;
  if(!Object.prototype.hasOwnProperty.call(sources,path)){
    const error=document.getElementById('error');error.hidden=false;error.textContent='묶음에 포함되지 않은 파일입니다. 목록에서 파일을 선택해 주세요.';code.hidden=true;select.selectedIndex=-1;
  }else{
    select.value=path;document.getElementById('path').textContent=path;document.getElementById('hash').textContent='SHA-256 · '+sources[path].sha256;
    const lines=sources[path].text.replace(/\r\n/g,'\n').split('\n');if(lines[lines.length-1]==='')lines.pop();
    const fragment=document.createDocumentFragment();
    lines.forEach((text,index)=>{const line=document.createElement('div'),number=document.createElement('a'),content=document.createElement('span');line.id='L'+(index+1);line.className='line';number.className='number';number.href='#'+line.id;number.textContent=String(index+1);number.setAttribute('aria-label',String(index+1)+'번째 줄');content.className='text';content.textContent=text||' ';line.append(number,content);fragment.append(line);});code.append(fragment);
  }
  select.addEventListener('change',()=>{if(Object.prototype.hasOwnProperty.call(sources,select.value))location.href='source.html?file='+encodeURIComponent(select.value)+'#L1';});
  function focusLine(){for(const line of code.querySelectorAll('.selected'))line.classList.remove('selected');if(!/^#L[1-9][0-9]*$/.test(location.hash))return;const line=document.getElementById(location.hash.slice(1));if(line){line.classList.add('selected');line.scrollIntoView({block:'center'});}}
  window.addEventListener('hashchange',focusLine);requestAnimationFrame(focusLine);
})();
</script></body></html>
'''
    return template.replace('__SOURCES__', embedded_json(sources))


class _PayloadReader(HTMLParser):
    def __init__(self):
        super().__init__()
        self.payloads = {}
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("id") in {"flow-data", "code-graph-meta"}:
            name = attrs["id"]
            if name in self.payloads or attrs.get("type") != "application/json":
                raise ValueError(f"duplicate or invalid JSON script: {name}")
            self.payloads[name] = ""
            self.current = name

    def handle_data(self, data):
        if self.current is not None:
            self.payloads[self.current] += data

    def handle_endtag(self, tag):
        if tag == "script":
            self.current = None


def render(template, data, metadata):
    tokens = {"__FLOW_DATA__": data, "__CODE_GRAPH_META__": metadata}
    for token in tokens:
        if template.count(token) != 1:
            raise ValueError(f"Build input must contain exactly one {token}; use agent-flow.html.in")
    result = template
    for token, value in tokens.items():
        result = result.replace(token, embedded_json(value))
    reader = _PayloadReader()
    reader.feed(result)
    expected = {"flow-data": data, "code-graph-meta": metadata}
    if reader.payloads.keys() != expected.keys():
        raise ValueError("Missing generated JSON payload")
    for name, value in expected.items():
        if json.loads(reader.payloads[name]) != value:
            raise ValueError(f"Generated payload mismatch: {name}")
    return result


def build():
    data = load_flow()
    graph = json.loads((FOLDER / "graphify-out" / "graph.json").read_text(encoding="utf-8"))
    summary = json.loads((FOLDER / "graphify-out" / "build-summary.json").read_text(encoding="utf-8"))
    metadata = {"version": summary["version"], "files": summary["source_files"], "nodes": len(graph["nodes"]),
                "edges": len(graph.get("links", graph.get("edges", [])))}
    # The full summary remains available beside the native code graph.
    metadata["build_summary"] = summary
    template = (ROOT / "scripts" / "templates" / "agent-flow.html.in").read_text(encoding="utf-8")
    result = render(template, data, metadata)
    output = FOLDER / "flow.html"
    output.write_text(result, encoding="utf-8")
    sources = source_payload(data)
    source_output = FOLDER / "source.html"
    source_output.write_text(render_sources(sources), encoding="utf-8")
    audit = {"workflow_nodes": len(data["nodes"]), "workflow_edges": len(data["edges"]),
             "scenarios": {k: len(v) for k, v in data["scenarios"].items()},
             "flow_data_sha256": sha256((FOLDER / "flow-data.json").read_bytes()).hexdigest(),
             "html_sha256": sha256(output.read_bytes()).hexdigest(), "code_graph": metadata,
             "source_files": len(sources), "source_html_sha256": sha256(source_output.read_bytes()).hexdigest(),
             "verification": "Data/reference checks and static HTML/JavaScript checks; browser rendering not verified."}
    (FOLDER / "flow-build.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Built {output}: {len(data['nodes'])} nodes, {len(data['edges'])} edges")


if __name__ == "__main__":
    build()
