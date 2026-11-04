-- Immutable human criteria revisions; existing AF001/AF002 and public M008 stay unchanged.
CREATE TABLE agent_framework.criteria_versions (
  criteria_version_id uuid PRIMARY KEY,
  source_run_id varchar(160) NOT NULL,
  project_id uuid NOT NULL,
  document_id uuid NOT NULL,
  document_version_id uuid NOT NULL,
  parent_version_id uuid,
  version_no bigint NOT NULL CHECK (version_no > 0),
  request_key uuid NOT NULL,
  request_json jsonb NOT NULL,
  request_sha256 varchar(64) NOT NULL CHECK (request_sha256 ~ '^[a-f0-9]{64}$'),
  snapshot_json jsonb NOT NULL,
  snapshot_sha256 varchar(64) NOT NULL CHECK (snapshot_sha256 ~ '^[a-f0-9]{64}$'),
  created_by uuid NOT NULL REFERENCES public.users(user_id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(source_run_id,request_key), UNIQUE(source_run_id,version_no),
  UNIQUE(criteria_version_id,source_run_id,project_id),
  FOREIGN KEY(source_run_id,project_id) REFERENCES agent_framework.runs(run_id,project_id),
  FOREIGN KEY(document_version_id,document_id,project_id)
    REFERENCES agent_framework.document_versions(document_version_id,document_id,project_id),
  FOREIGN KEY(parent_version_id,source_run_id,project_id)
    REFERENCES agent_framework.criteria_versions(criteria_version_id,source_run_id,project_id),
  CHECK ((version_no=1 AND parent_version_id IS NULL) OR (version_no>1 AND parent_version_id IS NOT NULL))
);
CREATE TABLE agent_framework.criteria_approval_bindings (
  approval_id uuid PRIMARY KEY REFERENCES agent_framework.approvals(approval_id),
  source_run_id varchar(160) NOT NULL UNIQUE,
  project_id uuid NOT NULL,
  criteria_version_id uuid NOT NULL UNIQUE,
  criteria_sha256 varchar(64) NOT NULL CHECK (criteria_sha256 ~ '^[a-f0-9]{64}$'),
  request_key uuid NOT NULL,
  request_json jsonb NOT NULL,
  request_sha256 varchar(64) NOT NULL CHECK (request_sha256 ~ '^[a-f0-9]{64}$'),
  evidence_json jsonb NOT NULL,
  synthetic boolean NOT NULL,
  FOREIGN KEY(source_run_id,project_id) REFERENCES agent_framework.runs(run_id,project_id),
  FOREIGN KEY(criteria_version_id,source_run_id,project_id)
    REFERENCES agent_framework.criteria_versions(criteria_version_id,source_run_id,project_id)
);
CREATE FUNCTION agent_framework.sealed_criteria_record() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Criteria revisions and approval bindings are immutable';
END $$;
REVOKE ALL ON FUNCTION agent_framework.sealed_criteria_record() FROM PUBLIC;
CREATE TRIGGER sealed_criteria_record BEFORE UPDATE OR DELETE ON agent_framework.criteria_versions
FOR EACH ROW EXECUTE FUNCTION agent_framework.sealed_criteria_record();
CREATE TRIGGER sealed_criteria_record BEFORE UPDATE OR DELETE ON agent_framework.criteria_approval_bindings
FOR EACH ROW EXECUTE FUNCTION agent_framework.sealed_criteria_record();
