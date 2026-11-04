-- Rebuildable relationship projection; public M008 and approval records stay unchanged.
CREATE TABLE agent_framework.graph_state (
  project_id uuid PRIMARY KEY REFERENCES public.projects(project_id),
  revision bigint NOT NULL DEFAULT 0 CHECK(revision >= 0),
  applied_revision bigint NOT NULL DEFAULT 0 CHECK(applied_revision >= 0 AND applied_revision <= revision),
  lease_token uuid,
  lease_expires_at timestamptz,
  available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  attempt_no integer NOT NULL DEFAULT 0 CHECK(attempt_no >= 0),
  error_json jsonb,
  synced_at timestamptz,
  CHECK ((lease_token IS NULL) = (lease_expires_at IS NULL))
);
CREATE TABLE agent_framework.graph_outbox (
  event_id uuid PRIMARY KEY,
  project_id uuid NOT NULL REFERENCES agent_framework.graph_state(project_id),
  revision bigint NOT NULL CHECK(revision > 0),
  kind text NOT NULL,
  target_id text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  processed_at timestamptz,
  UNIQUE(project_id,revision)
);
CREATE INDEX af_graph_delivery ON agent_framework.graph_state(available_at)
  WHERE revision > applied_revision;
CREATE TABLE agent_framework.evidence_contexts (
  run_id varchar(160) PRIMARY KEY REFERENCES agent_framework.runs(run_id),
  input_sha256 varchar(64) NOT NULL CHECK(input_sha256 ~ '^[a-f0-9]{64}$'),
  context_json jsonb NOT NULL,
  context_sha256 varchar(64) NOT NULL CHECK(context_sha256 ~ '^[a-f0-9]{64}$'),
  trace_json jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER sealed_evidence_context BEFORE UPDATE OR DELETE ON agent_framework.evidence_contexts
  FOR EACH ROW EXECUTE FUNCTION agent_framework.sealed_criteria_record();
