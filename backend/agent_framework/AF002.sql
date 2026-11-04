-- PDF metadata only. Existing M008 tables and AF001.sql are unchanged.
CREATE TABLE agent_framework.document_versions (
  document_version_id uuid PRIMARY KEY,
  document_id uuid NOT NULL,
  project_id uuid NOT NULL REFERENCES public.projects(project_id),
  version_no bigint NOT NULL CHECK (version_no > 0),
  previous_version_id uuid,
  file_id uuid NOT NULL UNIQUE,
  storage_key text NOT NULL UNIQUE,
  original_name varchar(255) NOT NULL,
  media_type text NOT NULL CHECK (media_type = 'application/pdf'),
  byte_size bigint NOT NULL CHECK (byte_size BETWEEN 5 AND 20971520),
  sha256 varchar(64) NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
  page_count integer NOT NULL CHECK (page_count BETWEEN 1 AND 50),
  created_by uuid NOT NULL REFERENCES public.users(user_id),
  request_key uuid NOT NULL,
  input_sha256 varchar(64) NOT NULL CHECK (input_sha256 ~ '^[a-f0-9]{64}$'),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (project_id, request_key),
  UNIQUE (document_id, version_no),
  UNIQUE (document_version_id, document_id, project_id),
  FOREIGN KEY (previous_version_id, document_id, project_id)
    REFERENCES agent_framework.document_versions(document_version_id, document_id, project_id),
  CHECK ((version_no = 1 AND previous_version_id IS NULL)
      OR (version_no > 1 AND previous_version_id IS NOT NULL)),
  CHECK (storage_key = 'objects/projects/' || project_id::text || '/files/' || file_id::text || '/payload.bin')
);
CREATE INDEX document_versions_project ON agent_framework.document_versions(project_id, document_id, version_no DESC);
CREATE FUNCTION agent_framework.sealed_document_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Registered document versions are immutable';
END $$;
REVOKE ALL ON FUNCTION agent_framework.sealed_document_version() FROM PUBLIC;
CREATE TRIGGER sealed_document_version BEFORE UPDATE OR DELETE ON agent_framework.document_versions
FOR EACH ROW EXECUTE FUNCTION agent_framework.sealed_document_version();
