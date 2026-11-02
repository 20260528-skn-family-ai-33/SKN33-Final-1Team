# Creative Checker — Graphify AST audit

> **Static code relationships only.** This graph does not demonstrate real Qwen execution,
> PostgreSQL/Neo4j connectivity, durable workers, or production deployment.
> The separate clickable business flow is a curated workflow illustration, not this AST graph.

- Tool: official `graphifyy==0.9.100`; directed build, community detection and native HTML exporter.
- Corpus: **25 Python files**, 13,311 words, exclusively `backend/agent_framework/**/*.py`.
- Git baseline: `eaab8701871769afec1a07c638d269abd9af06c1`. The files are current working-tree sources; their SHA-256 values are recorded in `source-manifest.json`.
- Raw AST: **404 nodes / 1619 edges**.
- Native directed graph: **439 nodes / 1527 edges / 11 communities**.
- **Integrity limits:** raw dangling endpoints 134, missing endpoints 0, self-loops 2, same-endpoint directed edges collapsed 91.
- Build added **35 implicit/external placeholder nodes**. These have no extracted in-scope definition; they are not deployment components.
- The native view has collapsed parallel relations. **`extraction.json` retains every raw relationship and source location**; `diagnostics.json` and `diagnostics.txt` retain the official diagnostic.
- Raw edge confidence: `{'EXTRACTED': 1372, 'INFERRED': 247}`. `INFERRED` edges are extractor resolution inferences, not verified runtime calls.
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
nodes: 404
unverified_code_nodes: 0
raw_edges: 1619
valid_candidate_edges: 1485
missing_endpoint_edges: 0
dangling_endpoint_edges: 134
external_reference_edges: 0
self_loop_edges: 2
exact_duplicate_edges: 0
directed_unique_endpoint_pairs: 1394
directed_same_endpoint_collapsed_edges: 91
undirected_unique_endpoint_pairs: 1390
undirected_same_endpoint_collapsed_edges: 95
same_endpoint_group_count: 85
relation_variant_groups: 79
source_file_variant_groups: 0
source_location_variant_groups: 1
context_variant_groups: 5
post_build_graph_type: DiGraph
post_build_edges: 1527
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
  - adapters_synthetic_make_agents -> ports_agents edges=3 relations=['calls', 'references', 'uses'] locations=['L146', 'L151'] contexts=['', 'call', 'return_type']
  - composition_build_agents -> ports_agents edges=3 relations=['calls', 'references', 'uses'] locations=['L169', 'L172'] contexts=['', 'call', 'return_type']
note: normal graph.json is post-build; raw producer loss must be measured earlier.
```

---

# Graph Report - agent_framework  (2026-11-02)

## Corpus Check
- Corpus is ~13,734 words - fits in a single context window. You may not need a graph.

## Summary
- 439 nodes · 1527 edges · 11 communities (9 shown, 2 thin omitted)
- Extraction: 87% EXTRACTED · 13% INFERRED · 0% AMBIGUOUS · INFERRED: 201 edges (avg confidence: 0.93)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `eaab8701`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Evidence and verdict validation
- Checks relationships
- Run input contracts
- Postgres relationships
- Postgres Api relationships
- Synthetic data and observations
- CLI and fixture composition
- Workflow and human approvals
- Durable Worker relationships

## God Nodes (most connected - your core abstractions)
1. `Context` - 53 edges
2. `Workflow` - 47 edges
3. `AdapterFailure` - 39 edges
4. `Contract` - 36 edges
5. `DocumentRef` - 32 edges
6. `AssetRef` - 32 edges
7. `InspectRequest` - 28 edges
8. `PostgresStore` - 28 edges
9. `create_app()` - 24 edges
10. `ExtractRequest` - 23 edges

## Surprising Connections (you probably didn't know these)
- `CreativeCheckerCatalog` --uses--> `AdapterFailure`  [INFERRED]
  adapters/project_files.py → ports.py
- `CreativeCheckerStorageReader` --uses--> `AdapterFailure`  [INFERRED]
  adapters/project_files.py → ports.py
- `LegacyPosterCatalog` --uses--> `AdapterFailure`  [INFERRED]
  adapters/project_files.py → ports.py
- `LegacyStorageReader` --uses--> `AdapterFailure`  [INFERRED]
  adapters/project_files.py → ports.py
- `_same_context()` --uses--> `Context`  [INFERRED]
  adapters/synthetic.py → contracts.py

## Import Cycles
- None detected.

## Communities (11 total, 2 thin omitted)

### Community 0 - "Evidence and verdict validation"
Cohesion: 0.09
Nodes (37): FileBackedExtractor, FileBackedInspector, QwenAdapterStub, _SyntheticValidator, CriteriaDraft, EvidenceBundle, ExtractRequest, InspectionResult (+29 more)

### Community 1 - "Checks relationships"
Cohesion: 0.07
Nodes (32): CheckSuiteError, _invalid_constant(), load_suite(), PreparedCase, PreparedSuite, _read_json(), RoleCheckCase, RoleCheckSuite (+24 more)

### Community 2 - "Run input contracts"
Cohesion: 0.08
Nodes (22): _authorize_error(), _creative_key(), CreativeCheckerCatalog, CreativeCheckerStorageReader, _fail(), LegacyPosterCatalog, LegacyStorageReader, _maximum() (+14 more)

### Community 3 - "Postgres relationships"
Cohesion: 0.08
Nodes (12): steps(), FrameworkError, StepRecord, StepTimeline, Claim, _PhaseState, PostgresQueue, coordinator() (+4 more)

### Community 4 - "Postgres Api relationships"
Cohesion: 0.09
Nodes (24): _invalid_constant(), load_config(), _unique_object(), create_postgres_app(), approve(), history(), invoke(), lifespan() (+16 more)

### Community 5 - "Synthetic data and observations"
Cohesion: 0.09
Nodes (17): generate(), main(), role_examples(), _hash(), make_agents(), _same_context(), SyntheticAdapters, source() (+9 more)

### Community 6 - "CLI and fixture composition"
Cohesion: 0.11
Nodes (21): create_app(), adapter_status(), approve(), configuration_status(), health(), invoke(), read(), revise() (+13 more)

### Community 7 - "Workflow and human approvals"
Cohesion: 0.17
Nodes (10): _demo(), Approval, RunSnapshot, run_flow_check(), approve(), _boundary(), _copy(), _GraphState (+2 more)

### Community 8 - "Durable Worker relationships"
Cohesion: 0.16
Nodes (10): execute_claim(), execute_role(), node(), Lease, accept_role_output(), _contract(), invoke_role(), _policy() (+2 more)

## Knowledge Gaps
- **2 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Context` connect `Run input contracts` to `Evidence and verdict validation`, `Postgres Api relationships`, `Synthetic data and observations`?**
  _High betweenness centrality (0.024) - this node is a cross-community bridge._
- **Are the 16 inferred relationships involving `Workflow` (e.g. with `Approval` and `AssetRef`) actually correct?**
  _`Workflow` has 16 INFERRED edges - model-reasoned connections that need verification._
- **Should `Evidence and verdict validation` be split into smaller, more focused modules?**
  _Cohesion score 0.09272300469483569 - nodes in this community are weakly interconnected._
- **Why does `Workflow` connect `Workflow and human approvals` to `Evidence and verdict validation`, `Checks relationships`, `Run input contracts`, `Postgres relationships`, `Synthetic data and observations`, `CLI and fixture composition`?**
  _High betweenness centrality (0.009) - this node is a cross-community bridge._
- **Should `Checks relationships` be split into smaller, more focused modules?**
  _Cohesion score 0.06663141195134849 - nodes in this community are weakly interconnected._
- **Why does `invoke_role()` connect `Durable Worker relationships` to `Evidence and verdict validation`, `CLI and fixture composition`?**
  _High betweenness centrality (0.006) - this node is a cross-community bridge._
- **Should `Run input contracts` be split into smaller, more focused modules?**
  _Cohesion score 0.08087431693989071 - nodes in this community are weakly interconnected._