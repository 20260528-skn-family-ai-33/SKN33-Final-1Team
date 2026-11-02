# Creative Checker — Graphify AST audit

> **Static code relationships only.** This graph does not demonstrate real Qwen execution,
> PostgreSQL/Neo4j connectivity, durable workers, or production deployment.
> The separate clickable business flow is a curated workflow illustration, not this AST graph.

- Tool: official `graphifyy==0.9.100`; directed build, community detection and native HTML exporter.
- Corpus: **19 Python files**, 11,180 words, exclusively `backend/agent_framework/**/*.py`.
- Git baseline: `26b6df9affa10779dc003fc36fc1b896fe5b594a`. The files are current working-tree sources; their SHA-256 values are recorded in `source-manifest.json`.
- Raw AST: **324 nodes / 1281 edges**.
- Native directed graph: **346 nodes / 1205 edges / 8 communities**.
- **Integrity limits:** raw dangling endpoints 78, missing endpoints 0, self-loops 1, same-endpoint directed edges collapsed 76.
- Build added **22 implicit/external placeholder nodes**. These have no extracted in-scope definition; they are not deployment components.
- The native view has collapsed parallel relations. **`extraction.json` retains every raw relationship and source location**; `diagnostics.json` and `diagnostics.txt` retain the official diagnostic.
- Raw edge confidence: `{'EXTRACTED': 1066, 'INFERRED': 215}`. `INFERRED` edges are extractor resolution inferences, not verified runtime calls.
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
nodes: 324
unverified_code_nodes: 0
raw_edges: 1281
valid_candidate_edges: 1203
missing_endpoint_edges: 0
dangling_endpoint_edges: 78
external_reference_edges: 0
self_loop_edges: 1
exact_duplicate_edges: 0
directed_unique_endpoint_pairs: 1127
directed_same_endpoint_collapsed_edges: 76
undirected_unique_endpoint_pairs: 1126
undirected_same_endpoint_collapsed_edges: 77
same_endpoint_group_count: 71
relation_variant_groups: 65
source_file_variant_groups: 0
source_location_variant_groups: 1
context_variant_groups: 5
post_build_graph_type: DiGraph
post_build_edges: 1205
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
  - composition_build_agents -> ports_agents edges=3 relations=['calls', 'references', 'uses'] locations=['L170', 'L173'] contexts=['', 'call', 'return_type']
note: normal graph.json is post-build; raw producer loss must be measured earlier.
```

---

# Graph Report - agent_framework  (2026-11-02)

## Corpus Check
- Corpus is ~11,180 words - fits in a single context window. You may not need a graph.

## Summary
- 346 nodes · 1205 edges · 8 communities (6 shown, 2 thin omitted)
- Extraction: 85% EXTRACTED · 15% INFERRED · 0% AMBIGUOUS · INFERRED: 178 edges (avg confidence: 0.93)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `26b6df9a`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Synthetic data and observations
- Run input contracts
- Checks relationships
- Workflow and human approvals
- CLI and fixture composition
- Evidence and verdict validation

## God Nodes (most connected - your core abstractions)
1. `Workflow` - 44 edges
2. `AdapterFailure` - 42 edges
3. `Context` - 39 edges
4. `Contract` - 36 edges
5. `DocumentRef` - 29 edges
6. `AssetRef` - 29 edges
7. `SyntheticAdapters` - 26 edges
8. `InspectRequest` - 26 edges
9. `create_app()` - 24 edges
10. `ExtractRequest` - 21 edges

## Surprising Connections (you probably didn't know these)
- `FileBackedExtractor` --uses--> `VerifiedDocument`  [INFERRED]
  adapters/file_backed.py → files.py
- `CreativeCheckerCatalog` --uses--> `AdapterFailure`  [INFERRED]
  adapters/project_files.py → ports.py
- `CreativeCheckerStorageReader` --uses--> `AdapterFailure`  [INFERRED]
  adapters/project_files.py → ports.py
- `LegacyPosterCatalog` --uses--> `AdapterFailure`  [INFERRED]
  adapters/project_files.py → ports.py
- `LegacyStorageReader` --uses--> `AdapterFailure`  [INFERRED]
  adapters/project_files.py → ports.py

## Import Cycles
- None detected.

## Communities (8 total, 2 thin omitted)

### Community 0 - "Synthetic data and observations"
Cohesion: 0.08
Nodes (33): FileBackedExtractor, FileBackedInspector, generate(), main(), role_examples(), _same_context(), SyntheticAdapters, source() (+25 more)

### Community 1 - "Run input contracts"
Cohesion: 0.07
Nodes (26): _authorize_error(), _creative_key(), CreativeCheckerCatalog, CreativeCheckerStorageReader, _fail(), LegacyPosterCatalog, LegacyStorageReader, _maximum() (+18 more)

### Community 2 - "Checks relationships"
Cohesion: 0.06
Nodes (32): make_agents(), CheckSuiteError, _invalid_constant(), load_suite(), PreparedCase, PreparedSuite, _read_json(), RoleCheckCase (+24 more)

### Community 3 - "Workflow and human approvals"
Cohesion: 0.12
Nodes (13): _demo(), record(), Approval, FrameworkError, RunSnapshot, StepRecord, run_flow_check(), approve() (+5 more)

### Community 4 - "CLI and fixture composition"
Cohesion: 0.09
Nodes (26): create_app(), adapter_status(), approve(), configuration_status(), health(), invoke(), read(), revise() (+18 more)

### Community 5 - "Evidence and verdict validation"
Cohesion: 0.15
Nodes (22): _SyntheticValidator, ValidatedItem, ValidatedResult, _contract(), _input_policy(), invoke_role(), _output_policy(), _policy() (+14 more)

## Knowledge Gaps
- **2 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `SyntheticAdapters` connect `Synthetic data and observations` to `Checks relationships`?**
  _High betweenness centrality (0.007) - this node is a cross-community bridge._
- **Are the 17 inferred relationships involving `Workflow` (e.g. with `Approval` and `AssetRef`) actually correct?**
  _`Workflow` has 17 INFERRED edges - model-reasoned connections that need verification._
- **Should `Synthetic data and observations` be split into smaller, more focused modules?**
  _Cohesion score 0.07777777777777778 - nodes in this community are weakly interconnected._
- **Why does `Workflow` connect `Workflow and human approvals` to `Synthetic data and observations`, `Run input contracts`, `Checks relationships`, `CLI and fixture composition`?**
  _High betweenness centrality (0.006) - this node is a cross-community bridge._
- **Should `Run input contracts` be split into smaller, more focused modules?**
  _Cohesion score 0.0733162830349531 - nodes in this community are weakly interconnected._
- **Why does `invoke_role()` connect `Evidence and verdict validation` to `CLI and fixture composition`?**
  _High betweenness centrality (0.005) - this node is a cross-community bridge._
- **Should `Checks relationships` be split into smaller, more focused modules?**
  _Cohesion score 0.06286748077792854 - nodes in this community are weakly interconnected._