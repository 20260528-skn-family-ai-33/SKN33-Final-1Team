"""Prevent a raw template or redirect page from replacing the usable HTML flow."""
from pathlib import Path
import json
import re

import pytest

from scripts.build_agent_flow_html import render, render_sources, source_payload, _PayloadReader

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "scripts" / "templates" / "agent-flow.html.in"


def test_wrong_or_duplicated_template_cannot_be_rendered():
    redirect = (SOURCE.parent / "agent-flow.html").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one"):
        render(redirect, {}, {})
    template = SOURCE.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one"):
        render(template + "__FLOW_DATA__", {}, {})


def test_generated_flow_contains_real_json_and_old_entry_opens_it():
    folder = ROOT / "docs" / "agent-framework"
    flow = json.loads((folder / "flow-data.json").read_text(encoding="utf-8"))
    canonical = (folder / "flow.html").read_text(encoding="utf-8")
    reader = _PayloadReader()
    reader.feed(canonical)
    assert json.loads(reader.payloads["flow-data"]) == flow
    assert len(flow["nodes"]) > 0
    assert "__FLOW_DATA__" not in canonical
    template = SOURCE.read_text(encoding="utf-8")
    assert render(template, flow, json.loads(reader.payloads["code-graph-meta"])) == canonical
    redirect = (SOURCE.parent / "agent-flow.html").read_text(encoding="utf-8")
    assert 'content="0;url=../../docs/agent-framework/flow.html"' in redirect
    assert 'href="../../docs/agent-framework/flow.html"' in redirect
    assert (SOURCE.parent / "../../docs/agent-framework/flow.html").resolve() == folder / "flow.html"


def test_source_viewer_keeps_code_as_text_and_rejects_absolute_or_traversal_paths():
    source = {'backend/agent_framework/example.py': {'text': '</script><script>alert(1)</script>\n한글 & < >', 'sha256': 'test'}}
    html = render_sources(source)
    payload = re.search(r'<script type="application/json" id="source-data">(.*?)</script>', html, re.S).group(1)
    assert json.loads(payload) == source
    assert '</script>' not in payload
    assert 'content.textContent=text' in html
    assert "Object.prototype.hasOwnProperty.call(sources,path)" in html
    for invalid in ('/Users/example/a.py', '../a.py', 'backend/../a.py', 'C:/code/a.py', r'backend\a.py', '.', 'a.py ', 'bad\nname.py'):
        with pytest.raises(ValueError, match='repository-relative'):
            render_sources({invalid: {'text': '', 'sha256': ''}})


def test_every_flow_source_is_embedded_and_links_to_a_real_line():
    folder = ROOT / 'docs' / 'agent-framework'
    flow = json.loads((folder / 'flow-data.json').read_text(encoding='utf-8'))
    html = (folder / 'source.html').read_text(encoding='utf-8')
    payload = re.search(r'<script type="application/json" id="source-data">(.*?)</script>', html, re.S).group(1)
    sources = json.loads(payload)
    assert sources == source_payload(flow)
    assert html == render_sources(sources)
    for node in flow['nodes']:
        for reference in node['files']:
            assert 1 <= reference['line'] <= len(sources[reference['path']]['text'].splitlines())
    canonical = (folder / 'flow.html').read_text(encoding='utf-8')
    assert "'source.html?file='+encodeURIComponent(f.path)+'#L'+f.line" in canonical
    assert "line.id='L'+(index+1)" in html
