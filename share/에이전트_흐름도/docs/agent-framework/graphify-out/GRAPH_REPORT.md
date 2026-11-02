# Creative Checker — Graphify AST audit

> **Static code relationships only.** This graph does not demonstrate real Qwen execution,
> PostgreSQL/Neo4j connectivity, durable workers, or production deployment.
> The separate clickable business flow is a curated workflow illustration, not this AST graph.

- Tool: official `graphifyy==0.9.100`; directed build, community detection and native HTML exporter.
- Corpus: **12 Python files**, 5,064 words, exclusively `backend/agent_framework/**/*.py`.
- Git baseline: `87a3b9eaf92c2a8c14f823053beb58d7812e06c3`. The files are current working-tree sources; their SHA-256 values are recorded in `source-manifest.json`.
- Raw AST: **158 nodes / 678 edges**.
- Native directed graph: **173 nodes / 633 edges / 14 communities**.
- **Integrity limits:** raw dangling endpoints 33, missing endpoints 0, self-loops 1, same-endpoint directed edges collapsed 45.
- Build added **15 implicit/external placeholder nodes**. These have no extracted in-scope definition; they are not deployment components.
- The native view has collapsed parallel relations. **`extraction.json` retains every raw relationship and source location**; `diagnostics.json` and `diagnostics.txt` retain the official diagnostic.
- Raw edge confidence: `{'EXTRACTED': 563, 'INFERRED': 115}`. `INFERRED` edges are extractor resolution inferences, not verified runtime calls.
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
[graphify] MultiDiGraph edge-collapse diagnostic
input: <in-memory>
input_stage: provided JSON (normal graph.json is post-build)
effective_directed: <direct-call>
nodes: 158
unverified_code_nodes: 0
raw_edges: 678
valid_candidate_edges: 645
missing_endpoint_edges: 0
dangling_endpoint_edges: 33
external_reference_edges: 0
self_loop_edges: 1
exact_duplicate_edges: 0
directed_unique_endpoint_pairs: 600
directed_same_endpoint_collapsed_edges: 45
undirected_unique_endpoint_pairs: 600
undirected_same_endpoint_collapsed_edges: 45
same_endpoint_group_count: 41
relation_variant_groups: 38
source_file_variant_groups: 0
source_location_variant_groups: 0
context_variant_groups: 3
post_build_graph_type: DiGraph
post_build_edges: 633
producer_suppression_sites: 12
producer_suppression_examples:
  - L1830 seen_ids arity=unknown
  - L2393 seen_ids arity=unknown
  - L2395 seen_doc_refs arity=unknown
  - L3062 seen_ids arity=unknown
  - L3209 seen_ids arity=unknown
  - L4350 seen_keys arity=unknown
  - L4536 seen_keys arity=unknown
  - L8160 seen_ids arity=unknown
examples:
  - adapters_synthetic_sample_inputs -> contracts_context edges=3 relations=['calls', 'references', 'uses'] locations=['L30', 'L34', 'L36'] contexts=['', 'call', 'generic_arg']
  - adapters_synthetic_sample_inputs -> contracts_documentref edges=3 relations=['calls', 'references', 'uses'] locations=['L30', 'L34', 'L41'] contexts=['', 'call', 'generic_arg']
  - adapters_synthetic_sample_inputs -> contracts_assetref edges=3 relations=['calls', 'references', 'uses'] locations=['L30', 'L34', 'L45'] contexts=['', 'call', 'generic_arg']
  - adapters_synthetic_make_agents -> ports_agents edges=3 relations=['calls', 'references', 'uses'] locations=['L174', 'L179'] contexts=['', 'call', 'return_type']
  - adapters_generate_examples_generate -> path edges=2 relations=['references'] locations=['L31'] contexts=['generic_arg', 'parameter_type']
note: normal graph.json is post-build; raw producer loss must be measured earlier.
```

---

# Graph Report - agent_framework  (2026-11-02)

## Corpus Check
- Corpus is ~5,064 words - fits in a single context window. You may not need a graph.

## Summary
- 173 nodes · 633 edges · 14 communities (11 shown, 3 thin omitted)
- Extraction: 85% EXTRACTED · 15% INFERRED · 0% AMBIGUOUS · INFERRED: 93 edges (avg confidence: 0.94)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `87a3b9ea`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- CLI and fixture composition
- Workflow and human approvals
- Evidence and verdict validation
- Synthetic data and observations
- Run input contracts
- Retrieval contracts and adapters
- Development HTTP routes
- Extraction contracts and adapters
- Reporting contracts and adapters
- Inspection contracts and ports
- Adapter error boundaries

## God Nodes (most connected - your core abstractions)
1. `Workflow` - 41 edges
2. `AdapterFailure` - 26 edges
3. `Contract` - 23 edges
4. `SyntheticAdapters` - 22 edges
5. `InspectRequest` - 21 edges
6. `RetrieveRequest` - 20 edges
7. `ReportRequest` - 19 edges
8. `ExtractRequest` - 17 edges
9. `Context` - 16 edges
10. `CriteriaDraft` - 16 edges

## Surprising Connections (you probably didn't know these)
- `_same_context()` --uses--> `Context`  [INFERRED]
  adapters/synthetic.py → contracts.py
- `SyntheticAdapters` --uses--> `CriteriaDraft`  [INFERRED]
  adapters/synthetic.py → contracts.py
- `SyntheticAdapters` --uses--> `EvidenceBundle`  [INFERRED]
  adapters/synthetic.py → contracts.py
- `SyntheticAdapters` --uses--> `ExtractRequest`  [INFERRED]
  adapters/synthetic.py → contracts.py
- `SyntheticAdapters` --uses--> `InspectionResult`  [INFERRED]
  adapters/synthetic.py → contracts.py

## Import Cycles
- None detected.

## Communities (14 total, 3 thin omitted)

### Community 0 - "CLI and fixture composition"
Cohesion: 0.11
Nodes (14): generate(), main(), role_examples(), make_agents(), _emit(), _error(), _factory(), _has_synthetic() (+6 more)

### Community 1 - "Workflow and human approvals"
Cohesion: 0.24
Nodes (8): _demo(), Approval, RunSnapshot, _boundary(), _copy(), _GraphState, _Run, Workflow

### Community 2 - "Evidence and verdict validation"
Cohesion: 0.19
Nodes (15): _check_role(), ValidatedItem, ValidatedResult, ValidateRequest, Validator, aggregate(), _criteria(), ensure_context() (+7 more)

### Community 3 - "Synthetic data and observations"
Cohesion: 0.23
Nodes (9): _hash(), SyntheticAdapters, source(), Criterion, Evidence, ImageEvidence, Observation, RoleRequest (+1 more)

### Community 4 - "Run input contracts"
Cohesion: 0.38
Nodes (7): sample_inputs(), RevisionRequest, StartRequest, AssetRef, Context, Contract, DocumentRef

### Community 5 - "Retrieval contracts and adapters"
Cohesion: 0.38
Nodes (4): PostgresNeo4jRetrieverStub, EvidenceBundle, RetrieveRequest, Retriever

### Community 6 - "Development HTTP routes"
Cohesion: 0.33
Nodes (6): create_app(), approve(), invoke(), read(), revise(), start()

### Community 7 - "Extraction contracts and adapters"
Cohesion: 0.46
Nodes (4): QwenAdapterStub, CriteriaDraft, ExtractRequest, Extractor

### Community 8 - "Reporting contracts and adapters"
Cohesion: 0.48
Nodes (3): ReportDraft, ReportRequest, Reporter

### Community 9 - "Inspection contracts and ports"
Cohesion: 0.52
Nodes (3): InspectionResult, InspectRequest, Inspector

### Community 10 - "Adapter error boundaries"
Cohesion: 0.33
Nodes (3): _same_context(), record(), AdapterFailure

## Knowledge Gaps
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Workflow` connect `Workflow and human approvals` to `CLI and fixture composition`, `Evidence and verdict validation`, `Run input contracts`, `Retrieval contracts and adapters`, `Extraction contracts and adapters`, `Reporting contracts and adapters`, `Inspection contracts and ports`, `Adapter error boundaries`?**
  _High betweenness centrality (0.013) - this node is a cross-community bridge._
- **Are the 18 inferred relationships involving `Workflow` (e.g. with `Approval` and `AssetRef`) actually correct?**
  _`Workflow` has 18 INFERRED edges - model-reasoned connections that need verification._
- **Should `CLI and fixture composition` be split into smaller, more focused modules?**
  _Cohesion score 0.11174242424242424 - nodes in this community are weakly interconnected._
- **Why does `make_agents()` connect `CLI and fixture composition` to `Evidence and verdict validation`, `Synthetic data and observations`?**
  _High betweenness centrality (0.012) - this node is a cross-community bridge._
- **Are the 14 inferred relationships involving `SyntheticAdapters` (e.g. with `CriteriaDraft` and `Criterion`) actually correct?**
  _`SyntheticAdapters` has 14 INFERRED edges - model-reasoned connections that need verification._
- **Why does `SyntheticAdapters` connect `Synthetic data and observations` to `CLI and fixture composition`, `Retrieval contracts and adapters`, `Extraction contracts and adapters`, `Reporting contracts and adapters`, `Inspection contracts and ports`, `Adapter error boundaries`?**
  _High betweenness centrality (0.010) - this node is a cross-community bridge._