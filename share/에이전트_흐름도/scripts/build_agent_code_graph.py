#!/usr/bin/env python3
"""Build official Graphify AST artifacts for backend/agent_framework only.

Run with graphifyy==0.9.100 in a disposable environment. Example:
  /tmp/graphify-venv/bin/python scripts/build_agent_code_graph.py \
    --vendor-dir /tmp/graphify-assets

No application modules, credentials, database, model API or service are loaded.
A supplied vendor directory must contain the SRI-verified vis-network 9.1.6
script and its licenses. Alternatively --download-assets explicitly fetches
those three pinned public artifacts. Package/cache installation is external.
"""
from __future__ import annotations
import argparse
import base64
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256, sha384
from html.parser import HTMLParser
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.request import Request, urlopen

VERSION = '0.9.100'
VIS_URL = 'https://unpkg.com/vis-network@9.1.6/standalone/umd/vis-network.min.js'
VIS_SRI = 'sha384-Ux6phic9PEHJ38YtrijhkzyJ8yQlH8i/+buBR8s3mAZOJrP1gwyvAcIYl3GWtpX1'
ASSETS = {
    'vis-network.min.js': VIS_URL,
    'LICENSE-APACHE-2.0.txt': 'https://unpkg.com/vis-network@9.1.6/LICENSE-APACHE-2.0',
    'LICENSE-MIT.txt': 'https://unpkg.com/vis-network@9.1.6/LICENSE-MIT',
}


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def portable_provenance(extraction, diagnosis, repo):
    """Keep source/tool identity without the builder's machine-specific paths."""
    for key in ('extracted_sources', 'failed_sources', 'skipped_sources'):
        if key in extraction:
            extraction[key] = [
                Path(value).resolve().relative_to(repo.resolve()).as_posix()
                if isinstance(value, str) and Path(value).is_absolute() else value
                for value in extraction[key]
            ]
    producer = diagnosis.get('producer_suppression', {})
    if producer.get('path'):
        producer['path'] = 'graphify/extract.py'
        producer['path_base'] = f'installed graphifyy=={VERSION} package'


def git(repo, *args):
    return subprocess.check_output(['git','-C',str(repo),*args],text=True).strip()


def community_names(graph, communities):
    """Human-curated labels matched by a representative AST node, not inferred edges."""
    anchors = [
        ('ports_agents','CLI and fixture composition'),
        ('runtime_workflow','Workflow and human approvals'),
        ('validation_referencevalidator','Evidence and verdict validation'),
        ('contracts_criterion','Synthetic data and observations'),
        ('contracts_context','Run input contracts'),
        ('contracts_evidencebundle','Approved context contracts'),
        ('api_create_app','Development HTTP routes'),
        ('contracts_criteriadraft','Extraction contracts and adapters'),
        ('contracts_reportdraft','Reporting contracts and adapters'),
        ('contracts_inspectionresult','Inspection contracts and ports'),
        ('ports_adapterfailure','Adapter error boundaries'),
        ('contracts_imageevidence_valid_box','Contract field validation'),
        ('adapters_init','Adapter package scope'),
        ('init','Framework package scope'),
    ]
    labels={}
    for cid,nodes in communities.items():
        node_set=set(nodes)
        selected=next((label for anchor,label in anchors if anchor in node_set),None)
        if selected is None:
            names=[graph.nodes[n].get('source_file','') for n in nodes]
            primary=Counter(p for p in names if p).most_common(1)
            selected=(Path(primary[0][0]).stem.replace('_',' ').title()+' relationships') if primary else 'External symbol references'
        labels[cid]=selected
    return labels


class ResourceAudit(HTMLParser):
    def __init__(self):super().__init__();self.external=[]
    def handle_starttag(self, tag, attrs):
        values=dict(attrs)
        candidates=[]
        if tag in ('script','img','iframe','audio','video','source'):candidates.append(values.get('src',''))
        if tag=='link' and values.get('rel') in ('stylesheet','preload','modulepreload'):candidates.append(values.get('href',''))
        self.external.extend(value for value in candidates if value.startswith(('https://','http://','//')))


def localize_html(path, vendor):
    data=(vendor/'vis-network.min.js').read_bytes()
    integrity='sha384-'+base64.b64encode(sha384(data).digest()).decode()
    if integrity!=VIS_SRI:raise RuntimeError('vis-network integrity does not match official Graphify HTML')
    html=path.read_text(encoding='utf-8')
    tag=re.compile(r'<script\s+src="'+re.escape(VIS_URL)+r'"\s+integrity="'+re.escape(VIS_SRI)+r'"\s+crossorigin="anonymous"></script>')
    if len(tag.findall(html))!=1:raise RuntimeError('Official HTML dependency changed; inspect before embedding')
    script=data.decode('utf-8')
    # An inline source map would resolve relative to graph.html; remove its
    # optional debugging hint while preserving all executable library bytes.
    script=re.sub(r'(?m)^//# sourceMappingURL=.*$','',script)
    script=re.sub(r'</script',r'<\/script',script,flags=re.IGNORECASE)
    replacement='<!-- Official vis-network 9.1.6; source SRI verified. Licenses are adjacent. -->\n<script>\n'+script+'\n</script>'
    html=tag.sub(lambda _:replacement,html,count=1)
    path.write_text(html,encoding='utf-8')
    audit=ResourceAudit();audit.feed(html)
    if audit.external:raise RuntimeError('External auto-loaded HTML resources remain: '+repr(audit.external))
    return {'version':'9.1.6','source_url':VIS_URL,'source_sha256':sha256(data).hexdigest(),
            'source_sri':integrity,'bytes':len(data),'delivery':'inline','external_html_resources':audit.external,
            'verification_scope':'static HTML resource audit only; no browser execution'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--cache-dir',type=Path)
    parser.add_argument('--vendor-dir',type=Path)
    parser.add_argument('--download-assets',action='store_true')
    parser.add_argument('--force',action='store_true',help='Allow an intentional graph shrink after reviewing the source change')
    args=parser.parse_args()
    repo=args.repo.resolve();source=repo/'backend'/'agent_framework';out=repo/'docs'/'agent-framework'/'graphify-out'
    if importlib.metadata.version('graphifyy')!=VERSION:raise RuntimeError('Use pinned graphifyy=='+VERSION)
    with tempfile.TemporaryDirectory(prefix='cc-graphify-build-') as scratch_name:
        scratch=Path(scratch_name);cache=(args.cache_dir or scratch).resolve();cache.mkdir(parents=True,exist_ok=True)
        # Set before importing graphify: all extractor/stat caches remain temporary.
        os.environ['GRAPHIFY_OUT']=str(cache/'ast-output')
        from graphify.detect import detect,save_manifest
        from graphify.extract import extract
        from graphify.build import build_from_json
        from graphify.cluster import cluster,score_all
        from graphify.analyze import god_nodes,surprising_connections,suggest_questions
        from graphify.diagnostics import diagnose_extraction,format_diagnostic_report
        from graphify.export import to_json,to_html
        from graphify.report import generate

        detection=detect(source,cache_root=cache,follow_symlinks=False)
        expected=sorted(p.resolve() for p in source.rglob('*.py') if not p.is_symlink())
        detected=sorted(Path(p).resolve() for p in detection['files'].get('code',[]))
        # Explicit SQL migrations are reviewed/executed separately. Graphify's
        # AST scope remains Python even when it classifies .sql as code.
        excluded=[p for p in detected if p.suffix!='.py']
        if any(p.parent!=source or re.fullmatch(r'AF[0-9]{3}\.sql',p.name) is None for p in excluded):
            raise RuntimeError('Unexpected non-Python source in AST scope')
        code=[p for p in detected if p.suffix=='.py']
        detection['files']['code']=[str(p) for p in code]
        detection['total_words']=sum(len(p.read_text(encoding='utf-8').split()) for p in code)
        if not code or code!=expected or any(detection['files'].get(k) for k in ('document','paper','image','video')):
            raise RuntimeError('The detected corpus differs from the explicit code-only Python scope')
        extraction=extract(code,root=source,cache_root=cache,parallel=False)
        if extraction.get('failed_sources') or extraction.get('skipped_sources'):
            raise RuntimeError('Extraction did not cover every scoped source: inspect failed/skipped_sources')
        # Native DiGraph intentionally collapses same-endpoint relations; retain
        # the unmodified producer output separately so that loss remains auditable.
        graph=build_from_json(deepcopy(extraction),root=source,directed=True)
        if not graph.number_of_nodes():raise RuntimeError('Empty graph; existing outputs were not touched')
        if graph.number_of_nodes()>5000:raise RuntimeError('Large graph requires an explicit visualization decision')
        communities=cluster(graph);cohesion=score_all(graph,communities)
        labels=community_names(graph,communities)
        diagnosis=diagnose_extraction(extraction,root=source,directed=True)
        portable_provenance(extraction, diagnosis, repo)
        gods=god_nodes(graph);surprises=surprising_connections(graph,communities)
        questions=suggest_questions(graph,communities,labels)
        commit=git(repo,'rev-parse','HEAD')
        graph.graph['source_root']='backend/agent_framework'
        graph.graph['analysis_scope']='AST code relationships; not runtime traces or deployment evidence'
        out.mkdir(parents=True,exist_ok=True)
        if not to_json(graph,communities,str(out/'graph.json'),force=args.force,built_at_commit=commit,community_labels=labels):
            raise RuntimeError('Graphify refused a shrinking graph; review the change before --force')
        if not to_html(graph,communities,str(out/'graph.html'),community_labels=labels):
            raise RuntimeError('Official Graphify HTML renderer did not write the output')
        vendor=args.vendor_dir.resolve() if args.vendor_dir else scratch/'assets'
        if args.download_assets:
            vendor.mkdir(parents=True,exist_ok=True)
            for name,url in ASSETS.items():
                with urlopen(Request(url,headers={'User-Agent':'CreativeChecker-Graphify-artifact-builder'}),timeout=30) as response:
                    vendor.joinpath(name).write_bytes(response.read(4*1024*1024))
        asset=localize_html(out/'graph.html',vendor)
        for name in ('LICENSE-APACHE-2.0.txt','LICENSE-MIT.txt'):
            (out/name).write_bytes((vendor/name).read_bytes())
        node_ids={item['id'] for item in extraction['nodes']}
        implicit=[{'id':n,**dict(graph.nodes[n])} for n in graph if n not in node_ids]
        manifest=[{'path':p.relative_to(repo).as_posix(),'sha256':sha256(p.read_bytes()).hexdigest(),
                   'bytes':p.stat().st_size,'lines':len(p.read_text(encoding='utf-8').splitlines())} for p in code]
        token_cost={'input':0,'output':0}
        confidence_counts=dict(Counter(edge.get('confidence','UNSPECIFIED') for edge in extraction['edges']))
        report=generate(graph,communities,cohesion,labels,gods,surprises,detection,token_cost,str(source),suggested_questions=questions,built_at_commit=commit)
        header=f'''# Creative Checker — Graphify AST audit

> **Static code relationships only.** This graph does not demonstrate real Qwen execution,
> PostgreSQL/Neo4j connectivity, durable workers, or production deployment.
> The separate clickable business flow is a curated workflow illustration, not this AST graph.

- Tool: official `graphifyy=={VERSION}`; directed build, community detection and native HTML exporter.
- Corpus: **{len(code)} Python files**, {detection['total_words']:,} words, exclusively `backend/agent_framework/**/*.py`.
- Git baseline: `{commit}`. The files are current working-tree sources; their SHA-256 values are recorded in `source-manifest.json`.
- Raw AST: **{len(extraction['nodes'])} nodes / {len(extraction['edges'])} edges**.
- Native directed graph: **{graph.number_of_nodes()} nodes / {graph.number_of_edges()} edges / {len(communities)} communities**.
- **Integrity limits:** raw dangling endpoints {diagnosis['dangling_endpoint_edges']}, missing endpoints {diagnosis['missing_endpoint_edges']}, self-loops {diagnosis['self_loop_edges']}, same-endpoint directed edges collapsed {diagnosis['directed_same_endpoint_collapsed_edges']}.
- Build added **{len(implicit)} implicit/external placeholder nodes**. These have no extracted in-scope definition; they are not deployment components.
- The native view has collapsed parallel relations. **`extraction.json` retains every raw relationship and source location**; `diagnostics.json` and `diagnostics.txt` retain the official diagnostic.
- Raw edge confidence: `{confidence_counts}`. `INFERRED` edges are extractor resolution inferences, not verified runtime calls.
- Source direction, confidence, `_origin` and location metadata are retained as emitted by Graphify. AST name resolution can be incomplete or ambiguous.
- AST extraction model tokens: **0 input / 0 output**. No semantic extraction or model API call. Host-agent reasoning/labeling tokens were **not separately measured**; this is not a zero-cost claim for the whole conversation.
- Token-reduction benchmark not run: the requested scope is the flow/code graph artifact, not a compression experiment.
- `graph.html` uses the official renderer with only its pinned vis-network dependency embedded after SRI verification. **No automatic external HTML resources remain in a static audit. Browser rendering was not tested here.**
- Graphify detected this small corpus as fitting in one context; graph generation proceeded because the user explicitly requested it.
- Rebuild script: `scripts/build_agent_code_graph.py`. A disposable environment and asset cache are required; project dependencies and user profiles were not changed.
- These outputs are written into the working tree; no ignore, commit or push operation was performed.

- Portable sharing: source metadata uses repository-relative paths; the extractor path is package-relative. Nodes, edges and diagnostic counts are unchanged.

## Raw diagnostic

```text
{format_diagnostic_report(diagnosis).strip()}
```

---

'''
        (out/'GRAPH_REPORT.md').write_text(header+report,encoding='utf-8')
        dump(out/'extraction.json',extraction)
        dump(out/'diagnostics.json',diagnosis)
        (out/'diagnostics.txt').write_text(format_diagnostic_report(diagnosis),encoding='utf-8')
        dump(out/'source-manifest.json',{'git_commit':commit,'scope':'backend/agent_framework/**/*.py','files':manifest,
            'excluded_sql_migrations':[p.relative_to(repo).as_posix() for p in excluded]})
        dump(out/'implicit-nodes.json',implicit)
        dump(out/'communities.json',{'labels':labels,'communities':communities,'cohesion':cohesion,'god_nodes':gods,'surprising_connections':surprises,'suggested_questions':questions})
        dump(out/'asset-audit.json',asset)
        dump(out/'cost.json',{'ast_input_tokens':0,'ast_output_tokens':0,'semantic_extraction_performed':False,'host_reasoning_tokens':'not separately measured','benchmark_performed':False})
        # Explicit paths prevent manifest output from escaping the owned directory.
        save_manifest(detection['files'],manifest_path=str(out/'manifest.json'),root=source,kind='ast',scan_corpus=set(map(str,code)))
        summary={'tool':'graphifyy','version':VERSION,'source_files':len(code),'source_words':detection['total_words'],
                 'ast_nodes':len(extraction['nodes']),'ast_edges':len(extraction['edges']),
                 'nodes':graph.number_of_nodes(),'edges':graph.number_of_edges(),'communities':len(communities),
                 'directed':True,'edge_confidence_counts':confidence_counts,'diagnostics':{k:diagnosis[k] for k in ('dangling_endpoint_edges','missing_endpoint_edges','self_loop_edges','directed_same_endpoint_collapsed_edges')},
                 'implicit_nodes':len(implicit),'html_external_resources':asset['external_html_resources'],
                 'browser_verified':False,'built_at':datetime.now(timezone.utc).isoformat()}
        dump(out/'build-summary.json',summary)
        print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
