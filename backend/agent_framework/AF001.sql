-- Additive extension in the EXISTING M008 database. Public tables are unchanged.
CREATE SCHEMA agent_framework;
REVOKE ALL ON SCHEMA agent_framework FROM PUBLIC;
CREATE TABLE agent_framework.schema_version (
  singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  revision text NOT NULL, ddl_sha256 varchar(64) NOT NULL,
  structure_json jsonb NOT NULL
);
CREATE TABLE agent_framework.runs (
  run_id varchar(160) PRIMARY KEY,
  project_id uuid NOT NULL REFERENCES public.projects(project_id),
  actor_id uuid NOT NULL REFERENCES public.users(user_id),
  parent_run_id varchar(160),
  input_json jsonb NOT NULL, input_sha256 varchar(64) NOT NULL,
  configuration_json jsonb NOT NULL, configuration_sha256 varchar(64) NOT NULL,
  runtime_sha256 varchar(64) NOT NULL,
  snapshot_json jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(run_id, project_id), UNIQUE(parent_run_id),
  FOREIGN KEY(parent_run_id, project_id) REFERENCES agent_framework.runs(run_id, project_id)
);
CREATE TABLE agent_framework.jobs (
  job_id uuid PRIMARY KEY, run_id varchar(160) NOT NULL,
  project_id uuid NOT NULL,
  phase text NOT NULL CHECK(phase IN ('extract','review')),
  state text NOT NULL CHECK(state IN ('queued','running','completed','failed','outcome_unknown')),
  attempt_no integer NOT NULL DEFAULT 0 CHECK(attempt_no BETWEEN 0 AND 3),
  worker_token uuid, lease_expires_at timestamptz, heartbeat_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  finished_at timestamptz, error_json jsonb,
  UNIQUE(run_id,phase),
  FOREIGN KEY(run_id,project_id) REFERENCES agent_framework.runs(run_id,project_id),
  CHECK((state='running' AND worker_token IS NOT NULL AND lease_expires_at IS NOT NULL)
     OR (state<>'running' AND worker_token IS NULL AND lease_expires_at IS NULL))
);
CREATE INDEX jobs_ready ON agent_framework.jobs(created_at,job_id) WHERE state='queued';
CREATE INDEX jobs_expired ON agent_framework.jobs(lease_expires_at) WHERE state='running';
CREATE TABLE agent_framework.steps (
  run_id varchar(160) NOT NULL REFERENCES agent_framework.runs(run_id),
  ordinal integer NOT NULL CHECK(ordinal>0),
  role text NOT NULL CHECK(role IN ('A02','A04_A05','A06','A07')),
  state text NOT NULL CHECK(state IN ('running','completed','failed','outcome_unknown','reused')),
  record_json jsonb NOT NULL, input_sha256 varchar(64) NOT NULL,
  output_json jsonb, output_sha256 varchar(64),
  PRIMARY KEY(run_id,ordinal), UNIQUE(run_id,role),
  CHECK((state='completed' AND output_json IS NOT NULL AND output_sha256 IS NOT NULL)
     OR (state<>'completed' AND output_json IS NULL AND output_sha256 IS NULL))
);
CREATE TABLE agent_framework.approvals (
  approval_id uuid PRIMARY KEY, run_id varchar(160) NOT NULL,
  project_id uuid NOT NULL, actor_id uuid NOT NULL REFERENCES public.users(user_id),
  decision text NOT NULL CHECK(decision IN ('approve_criteria','approve_changes','approve_final','approve_exception')),
  payload_json jsonb NOT NULL, payload_sha256 varchar(64) NOT NULL,
  approved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(run_id,decision),
  FOREIGN KEY(run_id,project_id) REFERENCES agent_framework.runs(run_id,project_id)
);
CREATE TABLE agent_framework.events (
  event_id uuid PRIMARY KEY, run_id varchar(160) NOT NULL REFERENCES agent_framework.runs(run_id),
  kind text NOT NULL, metadata_json jsonb NOT NULL DEFAULT '{}',
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION agent_framework.sealed_step() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.state <> 'running' THEN RAISE EXCEPTION 'Completed step records are immutable'; END IF;
  IF NEW.state = 'running' THEN RAISE EXCEPTION 'A running step can only be closed'; END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION agent_framework.sealed_step() FROM PUBLIC;
CREATE TRIGGER sealed_step BEFORE UPDATE ON agent_framework.steps
FOR EACH ROW EXECUTE FUNCTION agent_framework.sealed_step();
