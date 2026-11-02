"""Rebuild this share folder's index.html from its reviewed business-flow data.

Standard library only. Does not change application code or call any service.
Run from this share folder: python -m scripts.build_business_flow
"""
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "docs/agent-framework"
REFERENCE = ROOT / "docs/reference/creative-checker-master-2026-10-30.html"


class HTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        for key in ("src", "href"):
            if attrs.get(key):
                self.links.append((tag, attrs[key]))


def validate(data):
    reference = HTML()
    reference.feed(REFERENCE.read_text(encoding="utf-8"))
    if data["meta"]["sourceSha256"] != sha256(REFERENCE.read_bytes()).hexdigest():
        raise ValueError("Reference content differs from reviewed source hash")
    ids = {n["id"] for n in data["nodes"]}
    if len(ids) != len(data["nodes"]):
        raise ValueError("Duplicate business node ID")
    cells = set()
    for node in data["nodes"]:
        if node["lane"] not in {"human", "system", "evidence"} or node["status"] not in {"target", "partial", "adapter", "decision"}:
            raise ValueError("Unsupported node category")
        if not isinstance(node["row"], int) or not 1 <= node["row"] <= 30:
            raise ValueError("Invalid row")
        cell = (node["lane"], node["row"])
        if cell in cells:
            raise ValueError(f"Overlapping graph cards: {cell}")
        cells.add(cell)
        if node["source"]["anchor"] not in reference.ids:
            raise ValueError(f"Missing reference section: {node['id']}")
        for ref in node["codeRefs"]:
            source = (ROOT / ref["path"]).resolve()
            if not source.is_relative_to(ROOT) or not source.is_file():
                raise ValueError(f"Source outside shared folder: {ref}")
            if not 1 <= ref["line"] <= len(source.read_text(encoding="utf-8").splitlines()):
                raise ValueError(f"Invalid code line: {ref}")
    pairs = set()
    for edge in data["edges"]:
        if edge["from"] not in ids or edge["to"] not in ids or edge["kind"] not in {"flow", "revision", "evidence", "exception"}:
            raise ValueError("Invalid business edge")
        pairs.add((edge["from"], edge["to"]))
    for scenario in data["scenarios"].values():
        path = scenario["path"]
        if not path or not set(path) <= ids or any((a, b) not in pairs for a, b in zip(path, path[1:])):
            raise ValueError("Disconnected scenario")
    for decision in data["decisions"]:
        if decision["anchor"] not in reference.ids:
            raise ValueError("Missing decision reference")


def build():
    data = json.loads((FOLDER / "business-flow.json").read_text(encoding="utf-8"))
    validate(data)
    template = (ROOT / "scripts/templates/business-flow.html.in").read_text(encoding="utf-8")
    if template.count("__BUSINESS_DATA__") != 1:
        raise ValueError("Expected one embedded payload slot")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    html = template.replace("__BUSINESS_DATA__", payload)
    parser = HTML()
    parser.feed(html)
    for tag, value in parser.links:
        url = urlsplit(value)
        if url.scheme in {"https", "http"} and tag == "a":
            continue
        if url.scheme or url.netloc or "\\" in url.path or url.path.startswith("/"):
            raise ValueError(f"Nonportable link: {value}")
        target = (ROOT / unquote(url.path)).resolve() if url.path else ROOT / "index.html"
        if not target.is_relative_to(ROOT) or not target.is_file():
            raise ValueError(f"Missing shared target: {value}")
    (ROOT / "index.html").write_text(html, encoding="utf-8", newline="\n")
    audit = {"source_sha256": data["meta"]["sourceSha256"], "business_nodes": len(data["nodes"]),
             "business_edges": len(data["edges"]), "scenario_steps": {name: len(s["path"]) for name, s in data["scenarios"].items()},
             "html_sha256": sha256((ROOT / "index.html").read_bytes()).hexdigest(),
             "validation": "Reference anchors, code paths/lines, scenarios and relative links checked; browser rendering not verified."}
    (FOLDER / "business-flow-build.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    manifest_path = ROOT / "MANIFEST.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        names = {entry["path"] for entry in manifest["files"]}
        names.update({"index.html", "scripts/build_business_flow.py", "scripts/templates/business-flow.html.in",
                      "docs/agent-framework/business-flow.json", "docs/agent-framework/business-flow-build.json",
                      "docs/reference/creative-checker-master-2026-10-30.html"})
        entries = []
        for name in sorted(names):
            path = (ROOT / name).resolve()
            if not path.is_relative_to(ROOT) or not path.is_file():
                raise ValueError(f"Missing manifest source: {name}")
            content = path.read_bytes()
            entries.append({"path": name, "bytes": len(content), "sha256": sha256(content).hexdigest()})
        manifest["files"] = entries
        manifest["scope"] = "Offline business workflow and preserved common-framework development snapshot"
        manifest["verification"] = {"business_flow": audit, "manifest_hashes_refreshed": True,
                                    "browser_rendering_verified": False, "windows_execution_verified": False}
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    build()
