CREATE TABLE agent_framework.video_approvals (
  run_id uuid PRIMARY KEY,
  project_id uuid NOT NULL REFERENCES public.projects(project_id),
  submitted_by uuid NOT NULL REFERENCES public.users(user_id),
  root_run_id uuid NOT NULL,
  parent_run_id uuid,
  asset_version integer NOT NULL CHECK (asset_version >= 1),
  criteria_version integer NOT NULL CHECK (criteria_version >= 1),
  criteria_sha256 varchar(64) NOT NULL CHECK (criteria_sha256 ~ '^[a-f0-9]{64}$'),
  asset_sha256 varchar(64) NOT NULL CHECK (asset_sha256 ~ '^[a-f0-9]{64}$'),
  command_id uuid NOT NULL,
  actor_id uuid NOT NULL REFERENCES public.users(user_id),
  decision text NOT NULL CHECK (decision IN ('approve_changes', 'approve_final')),
  reason varchar(500) NOT NULL,
  human_checks jsonb NOT NULL CHECK (jsonb_typeof(human_checks) = 'object'),
  approved_at timestamptz NOT NULL,
  CHECK (parent_run_id IS DISTINCT FROM run_id),
  CHECK ((asset_version = 1 AND parent_run_id IS NULL AND run_id = root_run_id)
    OR (asset_version > 1 AND parent_run_id IS NOT NULL AND run_id <> root_run_id))
);

CREATE INDEX af_video_approval_project ON agent_framework.video_approvals(project_id);
CREATE TRIGGER sealed_video_approval BEFORE UPDATE OR DELETE ON agent_framework.video_approvals
  FOR EACH ROW EXECUTE FUNCTION agent_framework.sealed_criteria_record();
