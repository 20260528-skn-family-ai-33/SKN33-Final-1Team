export type ID = string;
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export interface ListResponse<T> { items: T[] }
export interface User { id: ID; email: string; name: string; is_admin: boolean; must_change_password?: boolean }
export interface Session { user: User; csrf_token: string }
export type Role = 'approver' | 'editor' | 'viewer';
export interface Project {
  id: ID; name: string; description: string; role: Role; archived: boolean;
  current_document_id: ID | null; current_criteria_id: ID | null;
  graph_status?: string; created_at?: string;
}
export interface Member { user_id: ID; role: Role; user?: User; name?: string; email?: string }
export interface Source { kind: 'document' | 'user'; block_id?: string | null; page?: number | null; quote?: string }
export interface DocumentBlock { id: ID; text: string; page?: number | null; kind?: string }
export type CriterionKind = 'exact_text' | 'date' | 'amount' | 'quantity' | 'logo' | 'layout' | 'color' | 'unsupported';
export interface Criterion {
  id: ID; text: string; category: 'creative' | 'business' | 'other'; required: boolean;
  kind: CriterionKind; expected: Json; target: 'all' | 'poster' | 'video'; asset_ids: ID[];
  source: Source; supported: boolean; notes: string;
}
export interface CriteriaGroup { id: ID; operator: 'all' | 'any'; criterion_ids: ID[]; condition: Json; exception: Json }
export interface CriteriaVersion {
  id: ID; version: number; status: string; document_id: ID; items: Criterion[]; groups: CriteriaGroup[];
  approved_by?: ID | null; approved_at?: string | null; created_at?: string;
}
export interface ServerIssue { code?: string; message?: string; [key: string]: unknown }
export interface DocumentVersion {
  id: ID; project_id: ID; version: number; filename: string; file_id: ID; status: string;
  blocks: DocumentBlock[]; criteria?: Criterion[] | null; groups?: CriteriaGroup[]; errors: (ServerIssue | string)[]; created_at: string;
}
export interface Logo {
  id: ID; name: string; file_id: ID; filename?: string; version?: number; status: string;
  approved_by?: ID | null; approved_at?: string | null; created_at?: string;
}
export interface AssetVersion {
  id: ID; version: number; file_id: ID; filename: string; metadata: Record<string, Json>; created_at: string;
  change_request_id?: ID | null;
}
export interface Asset { id: ID; project_id: ID; name: string; kind: 'poster' | 'video'; versions: AssetVersion[]; needs_review: boolean }
export type Verdict = 'pass' | 'fail' | 'review';
export type RunState = 'queued' | 'running' | 'waiting_review' | 'completed' | 'failed' | 'cancel_requested' | 'cancelled';
export interface Evidence { file_id: ID; frame_ms?: number | null; bbox?: [number, number, number, number] | null; text?: string }
export interface Result {
  criterion_id: ID; execution_status: 'completed' | 'failed' | 'unsupported'; verdict: Verdict;
  observed: Json; expected: Json; reason: string; evidence: Evidence[]; limitations: string[];
}
export interface Run {
  id: ID; project_id: ID; asset_id: ID; asset_version_id: ID; criteria_version_id: ID; state: RunState;
  stage: string; verdict: Verdict | null; progress: { completed: number; total: number; unit?: string };
  results: Result[]; report: Json; errors: (ServerIssue | string)[]; events: HistoryEvent[];
  snapshot: RunSnapshot | null; created_at: string; attempt?: number; previous_run_id?: ID | null; approval?: Approval | null;
}
export interface RunSnapshot {
  inference_profile?: { model: string; declared_revision: string; pipeline_sha256: string };
  asset?: { id: ID; kind: Asset['kind']; name: string }; asset_version?: AssetVersion;
  criteria?: CriteriaVersion; criteria_version_id?: ID; document_id?: ID;
  document?: { id: ID; version: number; file_id: ID; filename: string };
  items?: Criterion[]; groups?: CriteriaGroup[]; logos?: Logo[];
  previous_run_id?: ID | null; change_request_id?: ID | null;
}
export interface ChangeRequestItem { criterion_id: ID; message: string }
export interface ChangeRequest {
  id: ID; run_id: ID; status: string; items: ChangeRequestItem[]; message: string;
  approved_by?: ID | null; approved_at?: string | null; created_at?: string;
}
export interface Approval {
  id: ID; run_id: ID; exception_reason?: string | null; exception_criterion_ids: ID[];
  approved_by?: ID; approved_at?: string; created_at?: string; target_type?: string; target_id?: ID;
  original_verdict?: Verdict | null; snapshot?: unknown;
}
export interface HistoryResponse { items: HistoryEvent[]; approvals?: Approval[] }
export interface HistoryEvent {
  id?: ID; project_id?: ID; run_id?: ID | null; kind?: string; event_type?: string; type?: string; action?: string; message?: string;
  actor_id?: ID; actor_name?: string; user_id?: ID; created_at?: string; at?: string;
  payload?: Json; detail?: Json; data?: Json;
}
export interface GraphNode { id: ID; label: string; kind: string; [key: string]: unknown }
export interface GraphEdge { source: ID; target: ID; kind: string }
export interface Graph { status: string; synced_at?: string | null; nodes: GraphNode[]; edges: GraphEdge[]; truncated?: boolean }
export interface ImpactItem { run_id: ID; asset_version_id: ID | null; criteria_version_id: ID; approval_ids: ID[] }
export interface Impact { status?: string; criterion_id?: ID; nodes?: GraphNode[]; edges?: GraphEdge[]; items?: ImpactItem[]; truncated?: boolean }
