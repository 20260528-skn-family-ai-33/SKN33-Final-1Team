-- Free input reuses immutable document versions and their AF003 approval FKs.
-- Existing PDF rows, public M008, storage paths and sealed-version trigger stay.
ALTER TABLE agent_framework.document_versions
  DROP CONSTRAINT document_versions_media_type_check;
ALTER TABLE agent_framework.document_versions
  ADD CONSTRAINT document_versions_media_type_check
  CHECK (media_type IN ('application/pdf', 'text/plain'));
ALTER TABLE agent_framework.document_versions
  DROP CONSTRAINT document_versions_byte_size_check;
ALTER TABLE agent_framework.document_versions
  ADD CONSTRAINT document_versions_byte_size_check CHECK (
    (media_type = 'application/pdf' AND byte_size BETWEEN 5 AND 20971520)
    OR (media_type = 'text/plain' AND byte_size BETWEEN 1 AND 240000 AND page_count = 1)
  );
