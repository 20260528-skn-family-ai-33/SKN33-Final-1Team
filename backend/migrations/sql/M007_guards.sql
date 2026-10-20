-- Frozen M007 guards. SECURITY INVOKER; no application credentials or session bypass.
CREATE FUNCTION public.cc_fail(rule_name text) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
  RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = rule_name,
    MESSAGE = 'Creative Checker invariant violated: ' || rule_name;
END $$;

CREATE FUNCTION public.cc_lock_project(project_uuid uuid) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
  -- Fresh command snapshots after a waited row lock are required for write-skew checks.
  IF current_setting('transaction_isolation') <> 'read committed' THEN
    PERFORM public.cc_fail('cc_guard_isolation');
  END IF;
  PERFORM 1 FROM public.projects WHERE project_id = project_uuid FOR NO KEY UPDATE;
END $$;

CREATE FUNCTION public.cc_check_owner(project_uuid uuid) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
  PERFORM public.cc_lock_project(project_uuid);
  IF EXISTS (SELECT 1 FROM public.projects WHERE project_id = project_uuid)
     AND NOT EXISTS (SELECT 1 FROM public.project_members m JOIN public.users u USING (user_id)
       WHERE m.project_id = project_uuid AND m.role = 'owner' AND m.is_active AND u.is_active) THEN
    PERFORM public.cc_fail('ct_project_owner');
  END IF;
END $$;

CREATE FUNCTION public.cc_check_bounds(box jsonb, points jsonb, frame_uuid uuid, rule_name text) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE f public.image_coordinate_frames;
BEGIN
  IF box IS NULL AND points IS NULL THEN RETURN; END IF;
  SELECT * INTO STRICT f FROM public.image_coordinate_frames WHERE frame_id = frame_uuid;
  IF box IS NOT NULL AND ((box->>2)::numeric > f.width OR (box->>3)::numeric > f.height) THEN
    PERFORM public.cc_fail(rule_name);
  END IF;
  IF points IS NOT NULL AND EXISTS (SELECT 1 FROM jsonb_array_elements(points) p
      WHERE (p->>0)::numeric > f.width OR (p->>1)::numeric > f.height) THEN
    PERFORM public.cc_fail(rule_name);
  END IF;
END $$;

CREATE FUNCTION public.cc_check_frame(frame_uuid uuid) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE f public.image_coordinate_frames; v public.asset_versions; sf public.stored_files;
BEGIN
  SELECT * INTO STRICT f FROM public.image_coordinate_frames WHERE frame_id = frame_uuid;
  SELECT * INTO STRICT v FROM public.asset_versions WHERE asset_version_id = f.asset_version_id;
  SELECT * INTO STRICT sf FROM public.stored_files WHERE file_id = f.file_id;
  IF sf.file_role NOT IN ('original_image','display_image','ocr_input')
     OR (f.frame_kind = 'original' AND (f.file_id <> v.original_file_id OR f.width <> v.original_width OR f.height <> v.original_height))
     OR (f.frame_kind = 'display' AND (f.file_id <> v.display_file_id OR f.width <> v.display_width OR f.height <> v.display_height))
     OR (f.frame_kind = 'ocr_input' AND sf.file_role <> 'ocr_input') THEN
    PERFORM public.cc_fail('ct_lineage_image_coordinate_frames');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.stored_files WHERE file_id = v.original_file_id
       AND file_role = 'original_image' AND sha256 = v.original_sha256)
     OR NOT EXISTS (SELECT 1 FROM public.stored_files WHERE file_id = v.display_file_id AND file_role = 'display_image') THEN
    PERFORM public.cc_fail('ct_lineage_image_coordinate_frames');
  END IF;
END $$;

CREATE FUNCTION public.cc_lock_live_run(run_uuid uuid, attempt_uuid uuid) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE r public.review_runs; a public.review_attempts;
BEGIN
  SELECT * INTO STRICT r FROM public.review_runs WHERE run_id = run_uuid FOR UPDATE;
  IF r.sealed_at IS NOT NULL THEN PERFORM public.cc_fail('cc_run_append_context'); END IF;
  SELECT * INTO STRICT a FROM public.review_attempts WHERE attempt_id = attempt_uuid FOR UPDATE;
  IF a.run_id <> r.run_id THEN PERFORM public.cc_fail('cc_run_append_context'); END IF;
  IF r.origin_kind = 'service_execution' THEN
    IF r.execution_status <> 'running' OR r.active_attempt_id IS DISTINCT FROM a.attempt_id
       OR a.execution_status <> 'active' OR (r.executor_kind = 'postgres_worker'
          AND (a.lease_expires_at IS NULL OR a.lease_expires_at <= clock_timestamp())) THEN
      PERFORM public.cc_fail('cc_run_append_context');
    END IF;
  ELSIF r.execution_status <> 'imported' OR a.execution_status <> 'imported' THEN
    PERFORM public.cc_fail('cc_run_append_context');
  END IF;
END $$;

CREATE FUNCTION public.cc_schema_files(spec_uuid uuid, validation_uuid uuid, schema_hash text) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
  IF (spec_uuid IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.stored_files WHERE file_id = spec_uuid AND file_role = 'schema_specification'))
     OR (validation_uuid IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.stored_files WHERE file_id = validation_uuid
       AND file_role = 'validation_schema' AND (schema_hash IS NULL OR sha256 = schema_hash))) THEN
    PERFORM public.cc_fail('tr_schema_definitions_transition');
  END IF;
END $$;

CREATE FUNCTION public.cc_transition() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE allowed text[]; o jsonb; n jsonb; r public.review_runs; a public.review_attempts;
  cv public.criteria_versions; d public.criteria_drafts; old_state text; new_state text; cap text;
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF TG_TABLE_NAME = 'schema_definitions' THEN
      PERFORM public.cc_schema_files(NEW.specification_file_id, NEW.validation_schema_file_id, NEW.schema_sha256);
    END IF;
    IF TG_TABLE_NAME = 'review_runs' AND to_jsonb(NEW)->>'origin_kind' = 'service_execution' THEN
      PERFORM public.cc_lock_project(NEW.project_id);
      SELECT * INTO STRICT cv FROM public.criteria_versions WHERE criteria_version_id = NEW.criteria_version_id;
      IF NEW.execution_status <> 'queued' OR cv.confirmation_state <> 'confirmed' OR cv.sealed_at IS NULL
         OR NOT EXISTS (SELECT 1 FROM public.projects WHERE project_id = NEW.project_id AND business_state = 'active') THEN
        PERFORM public.cc_fail(TG_NAME);
      END IF;
      IF cv.source_draft_id IS NOT NULL THEN
        SELECT * INTO STRICT d FROM public.criteria_drafts WHERE draft_id = cv.source_draft_id FOR UPDATE;
        IF d.revision IS DISTINCT FROM cv.source_draft_revision OR d.state <> 'editing' THEN
          PERFORM public.cc_fail(TG_NAME);
        END IF;
      END IF;
    ELSIF TG_TABLE_NAME = 'review_attempts' THEN
      SELECT * INTO STRICT r FROM public.review_runs WHERE run_id = NEW.run_id FOR UPDATE;
      IF r.sealed_at IS NOT NULL
         OR (r.origin_kind = 'service_execution' AND (r.execution_status <> 'queued' OR NEW.execution_status <> 'active'))
         OR (r.origin_kind <> 'service_execution' AND (r.execution_status <> 'imported' OR NEW.execution_status <> 'imported'))
         OR (r.executor_kind = 'postgres_worker' AND (NEW.lease_expires_at IS NULL OR NEW.lease_expires_at <= clock_timestamp())) THEN
        PERFORM public.cc_fail(TG_NAME);
      END IF;
    ELSIF TG_TABLE_NAME = 'ocr_runs' AND to_jsonb(NEW)->>'attempt_id' IS NOT NULL THEN
      SELECT * INTO STRICT a FROM public.review_attempts WHERE attempt_id = NEW.attempt_id;
      PERFORM public.cc_lock_live_run(a.run_id, a.attempt_id);
    END IF;
    RETURN NEW;
  END IF;
  o := to_jsonb(OLD); n := to_jsonb(NEW);
  allowed := CASE TG_TABLE_NAME
    WHEN 'schema_definitions' THEN ARRAY['specification_file_id','validation_schema_file_id','schema_sha256','availability_state','access_scope','field_catalog_jsonb']
    WHEN 'stored_files' THEN ARRAY['import_package_id','state','updated_at']
    WHEN 'import_packages' THEN ARRAY['manifest_file_id','package_fingerprint','fingerprint_version','hash_check_state','import_state','local_validation_jsonb']
    WHEN 'ocr_runs' THEN ARRAY['source_file_id','execution_status','engine_name','engine_version','model_name','device','execution_target','pipeline_name','pipeline_version','reported_coordinate_space','engine_settings','runtime_metadata','error_jsonb','started_at','completed_at','sealed_at']
    WHEN 'poster_extractions' THEN ARRAY['sealed_at']
    WHEN 'criteria_versions' THEN ARRAY['sealed_at']
    WHEN 'criteria_drafts' THEN ARRAY['revision','raw_requirements','items_jsonb','source_documents_jsonb','state','updated_by','updated_at']
    WHEN 'review_runs' THEN ARRAY['execution_status','current_stage','progress','overall_verdict','available_at','active_attempt_id','selected_attempt_id','error_jsonb','result_manifest_hash','updated_at','started_at','finished_at','sealed_at']
    WHEN 'review_attempts' THEN ARRAY['stage','execution_status','dispatch_state','lease_expires_at','heartbeat_at','request_id','remote_job_id','actual_runtime','retryable','timing_jsonb','cost_amount','currency','error_jsonb','finished_at']
  END;
  IF (n - allowed) IS DISTINCT FROM (o - allowed) THEN PERFORM public.cc_fail(TG_NAME); END IF;
  IF TG_TABLE_NAME = 'schema_definitions' THEN
    PERFORM public.cc_schema_files(NEW.specification_file_id, NEW.validation_schema_file_id, NEW.schema_sha256);
    IF (OLD.availability_state = 'available' AND
        (n - ARRAY['access_scope']) IS DISTINCT FROM (o - ARRAY['access_scope']))
       OR (OLD.availability_state = 'available' AND NEW.availability_state <> 'available') THEN
      PERFORM public.cc_fail(TG_NAME);
    END IF;
  ELSIF TG_TABLE_NAME = 'stored_files' THEN
    IF OLD.import_package_id IS NOT NULL AND OLD.import_package_id IS DISTINCT FROM NEW.import_package_id THEN
      PERFORM public.cc_fail(TG_NAME);
    END IF;
  ELSIF TG_TABLE_NAME = 'import_packages' THEN
    IF (OLD.import_state = 'failed' AND NEW.import_state <> 'received') OR OLD.import_state = 'completed'
       OR (OLD.manifest_file_id IS NOT NULL AND OLD.manifest_file_id IS DISTINCT FROM NEW.manifest_file_id)
       OR (OLD.package_fingerprint IS NOT NULL AND
           (OLD.package_fingerprint IS DISTINCT FROM NEW.package_fingerprint OR OLD.fingerprint_version IS DISTINCT FROM NEW.fingerprint_version))
       OR NOT (NEW.import_state = OLD.import_state OR (OLD.import_state = 'received' AND NEW.import_state IN ('completed','needs_review','failed'))
         OR (OLD.import_state = 'needs_review' AND NEW.import_state IN ('completed','failed'))
         OR (OLD.import_state = 'failed' AND NEW.import_state = 'received'
             AND current_user = pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid = TG_RELID))
             AND EXISTS (SELECT 1 FROM public.audit_events WHERE project_id = OLD.project_id
               AND target_id = OLD.import_id AND target_type = 'import_package' AND action = 'import.revalidate'))) THEN
      PERFORM public.cc_fail(TG_NAME);
    END IF;
  ELSIF TG_TABLE_NAME IN ('poster_extractions','criteria_versions') THEN
    IF OLD.sealed_at IS NOT NULL OR NEW.sealed_at IS NULL THEN PERFORM public.cc_fail(TG_NAME); END IF;
  ELSIF TG_TABLE_NAME = 'ocr_runs' THEN
    IF OLD.sealed_at IS NOT NULL
       OR (OLD.execution_status IN ('completed','failed','imported') AND
           ((n - ARRAY['sealed_at']) IS DISTINCT FROM (o - ARRAY['sealed_at']) OR NEW.sealed_at IS NULL))
       OR NOT (OLD.execution_status = NEW.execution_status
          OR (OLD.execution_status = 'queued' AND NEW.execution_status IN ('running','failed'))
          OR (OLD.execution_status = 'running' AND NEW.execution_status IN ('completed','failed'))) THEN
      PERFORM public.cc_fail(TG_NAME);
    END IF;
    IF OLD.source_file_id IS NOT NULL AND OLD.source_file_id IS DISTINCT FROM NEW.source_file_id THEN
      PERFORM public.cc_fail(TG_NAME);
    END IF;
  ELSIF TG_TABLE_NAME = 'criteria_drafts' THEN
    IF OLD.state = 'archived' OR NEW.state NOT IN ('editing','archived') THEN PERFORM public.cc_fail(TG_NAME); END IF;
    IF (n->'raw_requirements',n->'items_jsonb',n->'source_documents_jsonb') IS DISTINCT FROM (o->'raw_requirements',o->'items_jsonb',o->'source_documents_jsonb') THEN
      IF NEW.revision <> OLD.revision + 1 THEN PERFORM public.cc_fail(TG_NAME); END IF;
    ELSIF NEW.revision <> OLD.revision THEN PERFORM public.cc_fail(TG_NAME);
    END IF;
  ELSIF TG_TABLE_NAME = 'review_runs' THEN
    IF OLD.sealed_at IS NOT NULL OR OLD.execution_status IN ('completed','failed')
       OR (OLD.started_at IS NOT NULL AND OLD.started_at IS DISTINCT FROM NEW.started_at) THEN
      PERFORM public.cc_fail(TG_NAME);
    END IF;
    IF OLD.origin_kind <> 'service_execution' THEN
      IF (n - ARRAY['selected_attempt_id','result_manifest_hash','sealed_at','updated_at']) IS DISTINCT FROM
         (o - ARRAY['selected_attempt_id','result_manifest_hash','sealed_at','updated_at']) THEN PERFORM public.cc_fail(TG_NAME); END IF;
    ELSE
      IF NOT (OLD.execution_status = NEW.execution_status
          OR (OLD.execution_status = 'queued' AND NEW.execution_status IN ('running','failed'))
          OR (OLD.execution_status = 'running' AND NEW.execution_status IN ('queued','completed','failed'))) THEN
        PERFORM public.cc_fail(TG_NAME);
      END IF;
      IF OLD.execution_status = 'running' AND NEW.execution_status = 'queued' THEN
        SELECT * INTO STRICT a FROM public.review_attempts WHERE attempt_id = OLD.active_attempt_id FOR UPDATE;
        cap := OLD.retry_policy_jsonb->>'max_attempts';
        IF cap IS NULL OR cap !~ '^[1-9][0-9]*$' THEN PERFORM public.cc_fail(TG_NAME); END IF;
        IF OLD.executor_kind <> 'postgres_worker' OR a.execution_status <> 'failed' OR a.retryable IS DISTINCT FROM true
           OR a.dispatch_state IS DISTINCT FROM 'not_dispatched'
           OR cap::numeric > 3 OR a.attempt_no >= cap::numeric THEN PERFORM public.cc_fail(TG_NAME); END IF;
      END IF;
    END IF;
  ELSIF TG_TABLE_NAME = 'review_attempts' THEN
    SELECT * INTO STRICT r FROM public.review_runs WHERE run_id = OLD.run_id FOR UPDATE;
    IF OLD.execution_status <> 'active' OR r.sealed_at IS NOT NULL OR r.execution_status <> 'running'
       OR r.active_attempt_id IS DISTINCT FROM OLD.attempt_id
       OR NEW.execution_status NOT IN ('active','succeeded','failed','unknown') THEN PERFORM public.cc_fail(TG_NAME); END IF;
    IF r.executor_kind = 'postgres_worker' AND
       (NEW.lease_expires_at IS NULL OR (NEW.execution_status IN ('active','succeeded') AND OLD.lease_expires_at <= clock_timestamp())) THEN
      PERFORM public.cc_fail(TG_NAME);
    END IF;
    IF OLD.heartbeat_at IS NOT NULL AND NEW.heartbeat_at < OLD.heartbeat_at THEN PERFORM public.cc_fail(TG_NAME); END IF;
    IF OLD.lease_expires_at IS NOT NULL AND NEW.lease_expires_at < OLD.lease_expires_at THEN PERFORM public.cc_fail(TG_NAME); END IF;
    IF NOT coalesce((OLD.dispatch_state IS NOT DISTINCT FROM NEW.dispatch_state
      OR (OLD.dispatch_state IS NULL AND NEW.dispatch_state = 'not_dispatched')
      OR (OLD.dispatch_state = 'not_dispatched' AND NEW.dispatch_state = 'dispatching')
      OR (OLD.dispatch_state = 'dispatching' AND NEW.dispatch_state IN ('submitted','unknown'))
      OR (OLD.dispatch_state = 'submitted' AND NEW.dispatch_state = 'unknown')),false) THEN PERFORM public.cc_fail(TG_NAME); END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION public.cc_gate_owner(observation_uuid uuid, result_uuid uuid) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE root_id uuid; sealed timestamptz; run_uuid uuid; attempt_uuid uuid;
BEGIN
  IF observation_uuid IS NOT NULL THEN
    SELECT extraction_id INTO STRICT root_id FROM public.poster_field_observations WHERE observation_id = observation_uuid;
    SELECT sealed_at INTO STRICT sealed FROM public.poster_extractions WHERE extraction_id = root_id FOR UPDATE;
    IF sealed IS NOT NULL THEN PERFORM public.cc_fail('cc_evidence_append_context'); END IF;
  ELSIF result_uuid IS NOT NULL THEN
    SELECT run_id, attempt_id INTO STRICT run_uuid, attempt_uuid FROM public.item_results WHERE result_id = result_uuid;
    PERFORM public.cc_lock_live_run(run_uuid, attempt_uuid);
  ELSE PERFORM public.cc_fail('cc_evidence_append_context');
  END IF;
END $$;

CREATE FUNCTION public.cc_append_gate() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE sealed timestamptz; root_id uuid; a uuid; e public.evidence_records;
BEGIN
  CASE TG_TABLE_NAME
    WHEN 'ocr_tokens' THEN
      SELECT sealed_at INTO STRICT sealed FROM public.ocr_runs WHERE ocr_run_id = NEW.ocr_run_id FOR UPDATE;
    WHEN 'poster_field_observations' THEN
      SELECT sealed_at INTO STRICT sealed FROM public.poster_extractions WHERE extraction_id = NEW.extraction_id FOR UPDATE;
    WHEN 'criteria_items' THEN
      SELECT sealed_at INTO STRICT sealed FROM public.criteria_versions WHERE criteria_version_id = NEW.criteria_version_id FOR UPDATE;
    WHEN 'item_results' THEN
      PERFORM public.cc_lock_live_run(NEW.run_id, NEW.attempt_id);
    WHEN 'run_item_selections' THEN
      SELECT attempt_id INTO STRICT a FROM public.item_results WHERE result_id = NEW.result_id;
      PERFORM public.cc_lock_live_run(NEW.run_id, a);
    WHEN 'evidence_records' THEN
      PERFORM public.cc_gate_owner(NEW.observation_id, NEW.item_result_id);
    WHEN 'evidence_ocr_tokens' THEN
      SELECT * INTO STRICT e FROM public.evidence_records WHERE evidence_id = NEW.evidence_id;
      PERFORM public.cc_gate_owner(e.observation_id, e.item_result_id);
  END CASE;
  IF sealed IS NOT NULL THEN PERFORM public.cc_fail(TG_NAME); END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION public.cc_stored_import_gate() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE state text;
BEGIN
  IF TG_OP = 'UPDATE' AND OLD.import_package_id IS NOT DISTINCT FROM NEW.import_package_id THEN RETURN NEW; END IF;
  IF NEW.import_package_id IS NOT NULL THEN
    SELECT import_state INTO STRICT state FROM public.import_packages WHERE import_id = NEW.import_package_id FOR UPDATE;
    IF state NOT IN ('received','needs_review') THEN PERFORM public.cc_fail(TG_NAME); END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION public.cc_check_evidence(evidence_uuid uuid) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE e public.evidence_records; expected_ocr uuid; f public.image_coordinate_frames;
BEGIN
  SELECT * INTO STRICT e FROM public.evidence_records WHERE evidence_id = evidence_uuid;
  IF e.observation_id IS NOT NULL THEN
    SELECT x.ocr_run_id INTO STRICT expected_ocr FROM public.poster_field_observations o
      JOIN public.poster_extractions x USING (extraction_id) WHERE o.observation_id = e.observation_id;
  ELSE
    SELECT ocr_run_id INTO STRICT expected_ocr FROM public.item_results WHERE result_id = e.item_result_id;
  END IF;
  IF e.source_kind = 'ocr' AND (expected_ocr IS NULL OR e.source_ocr_run_id IS DISTINCT FROM expected_ocr) THEN
    PERFORM public.cc_fail('cc_evidence_source');
  END IF;
  IF e.frame_id IS NOT NULL THEN
    SELECT * INTO STRICT f FROM public.image_coordinate_frames WHERE frame_id = e.frame_id;
    IF (e.source_kind = 'image' AND e.source_file_id IS DISTINCT FROM f.file_id)
       OR (e.source_kind = 'ocr' AND NOT EXISTS (SELECT 1 FROM public.ocr_runs
             WHERE ocr_run_id = e.source_ocr_run_id AND frame_id = e.frame_id)) THEN
      PERFORM public.cc_fail('cc_evidence_source');
    END IF;
    PERFORM public.cc_check_bounds(e.bbox_xyxy, e.polygon, e.frame_id, 'cc_evidence_bounds');
  END IF;
END $$;

CREATE FUNCTION public.cc_check_ocr(root_uuid uuid) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE r public.ocr_runs; f public.image_coordinate_frames; t record;
BEGIN
  SELECT * INTO STRICT r FROM public.ocr_runs WHERE ocr_run_id = root_uuid;
  IF r.sealed_at IS NULL OR r.execution_status NOT IN ('completed','failed','imported') THEN
    PERFORM public.cc_fail('ct_ocr_snapshot');
  END IF;
  SELECT * INTO STRICT f FROM public.image_coordinate_frames WHERE frame_id = r.frame_id;
  IF r.coordinate_space <> f.coordinate_space
     OR (r.source_file_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.stored_files
           WHERE file_id = r.source_file_id AND file_role = 'source_json'))
     OR (r.input_file_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.stored_files
           WHERE file_id = r.input_file_id AND file_role IN ('ocr_input','original_image','display_image'))) THEN
    PERFORM public.cc_fail('ct_ocr_snapshot');
  END IF;
  PERFORM public.cc_check_frame(r.frame_id);
  FOR t IN SELECT bbox_xyxy, polygon FROM public.ocr_tokens WHERE ocr_run_id = root_uuid LOOP
    PERFORM public.cc_check_bounds(t.bbox_xyxy, t.polygon, r.frame_id, 'ct_ocr_snapshot');
  END LOOP;
END $$;

CREATE FUNCTION public.cc_check_extraction(root_uuid uuid) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE x public.poster_extractions; e record;
BEGIN
  SELECT * INTO STRICT x FROM public.poster_extractions WHERE extraction_id = root_uuid;
  IF x.sealed_at IS NULL THEN PERFORM public.cc_fail('ct_extraction_snapshot'); END IF;
  FOR e IN SELECT ev.evidence_id FROM public.evidence_records ev JOIN public.poster_field_observations o USING (observation_id)
     WHERE o.extraction_id = root_uuid LOOP
    PERFORM public.cc_check_evidence(e.evidence_id);
  END LOOP;
END $$;

CREATE FUNCTION public.cc_check_criteria(root_uuid uuid) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE v public.criteria_versions; size bigint; first_position integer; last_position integer;
BEGIN
  SELECT * INTO STRICT v FROM public.criteria_versions WHERE criteria_version_id = root_uuid;
  SELECT count(*),min(position),max(position) INTO size,first_position,last_position
    FROM public.criteria_items WHERE criteria_version_id = root_uuid;
  IF v.sealed_at IS NULL OR size NOT BETWEEN 1 AND 100 OR first_position <> 1 OR last_position <> size THEN
    PERFORM public.cc_fail('ct_criteria_snapshot');
  END IF;
END $$;

CREATE FUNCTION public.cc_check_review(run_uuid uuid) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE r public.review_runs; a public.review_attempts; active_count bigint; item_count bigint; selected_count bigint;
  e record; expected_verdict text;
BEGIN
  SELECT * INTO STRICT r FROM public.review_runs WHERE run_id = run_uuid;
  SELECT count(*) INTO active_count FROM public.review_attempts WHERE run_id = run_uuid AND execution_status = 'active';
  IF r.execution_status = 'running' THEN
    SELECT * INTO a FROM public.review_attempts WHERE attempt_id = r.active_attempt_id;
    IF active_count <> 1 OR a.attempt_id IS NULL OR a.run_id <> run_uuid OR a.execution_status <> 'active'
       OR (r.executor_kind = 'postgres_worker' AND a.lease_expires_at IS NULL) THEN
      PERFORM public.cc_fail('ct_review_integrity');
    END IF;
  ELSIF active_count <> 0 THEN PERFORM public.cc_fail('ct_review_integrity');
  END IF;
  IF EXISTS (SELECT 1 FROM public.review_attempts WHERE run_id = run_uuid
       AND ((r.origin_kind = 'service_execution' AND execution_status = 'imported')
         OR (r.origin_kind <> 'service_execution' AND execution_status <> 'imported'))) THEN
    PERFORM public.cc_fail('ct_review_integrity');
  END IF;
  IF r.origin_kind = 'service_execution' AND NOT EXISTS (SELECT 1 FROM public.criteria_versions
       WHERE criteria_version_id = r.criteria_version_id AND confirmation_state = 'confirmed' AND sealed_at IS NOT NULL) THEN
    PERFORM public.cc_fail('ct_review_integrity');
  END IF;
  IF r.selected_attempt_id IS NOT NULL THEN
    SELECT * INTO a FROM public.review_attempts WHERE attempt_id = r.selected_attempt_id;
    IF a.run_id <> run_uuid OR a.execution_status <> (CASE WHEN r.origin_kind = 'service_execution' THEN 'succeeded' ELSE 'imported' END) THEN
      PERFORM public.cc_fail('ct_review_integrity');
    END IF;
  END IF;
  IF r.execution_status = 'imported' AND (r.sealed_at IS NULL OR r.result_manifest_hash IS NULL OR r.manifest_hash_version IS NULL) THEN
    PERFORM public.cc_fail('ct_review_integrity');
  END IF;
  IF EXISTS (SELECT 1 FROM public.item_results i LEFT JOIN public.ocr_runs o ON o.ocr_run_id = i.ocr_run_id
      LEFT JOIN public.poster_extractions x ON x.extraction_id = i.extraction_id WHERE i.run_id = run_uuid AND
      (i.result_origin <> r.origin_kind
       OR (r.origin_kind = 'service_execution' AND i.input_manifest_hash IS DISTINCT FROM r.input_manifest_hash)
       OR (i.ocr_run_id IS NOT NULL AND o.attempt_id IS DISTINCT FROM i.attempt_id)
       OR (i.extraction_id IS NOT NULL AND x.ocr_run_id IS DISTINCT FROM i.ocr_run_id))) THEN
    PERFORM public.cc_fail('ct_review_integrity');
  END IF;
  IF EXISTS (SELECT 1 FROM public.run_item_selections s JOIN public.item_results i USING (result_id)
      WHERE s.run_id = run_uuid AND (i.attempt_id IS DISTINCT FROM r.selected_attempt_id
       OR s.selection_kind <> CASE WHEN r.origin_kind = 'service_execution' THEN 'pipeline_final' ELSE 'import_projection' END)) THEN
    PERFORM public.cc_fail('ct_review_integrity');
  END IF;
  IF r.execution_status NOT IN ('completed','imported') AND EXISTS (SELECT 1 FROM public.item_results WHERE run_id = run_uuid) THEN
    PERFORM public.cc_fail('ct_review_integrity');
  END IF;
  IF r.execution_status = 'completed' THEN
    SELECT count(*) INTO item_count FROM public.criteria_items WHERE criteria_version_id = r.criteria_version_id;
    SELECT count(*) INTO selected_count FROM public.run_item_selections WHERE run_id = run_uuid;
    IF item_count = 0 OR selected_count <> item_count
       OR (SELECT count(*) FROM public.item_results WHERE run_id = run_uuid AND attempt_id = r.selected_attempt_id) <> item_count THEN
      PERFORM public.cc_fail('ct_review_integrity');
    END IF;
    IF r.aggregation_policy_version = 'all_selected_v1' THEN
      SELECT CASE WHEN bool_or(i.verdict = 'fail') THEN 'fail'
                  WHEN bool_or(i.execution_status <> 'completed' OR i.verdict = 'review') THEN 'review' ELSE 'pass' END
        INTO expected_verdict FROM public.run_item_selections s JOIN public.item_results i USING (result_id) WHERE s.run_id = run_uuid;
      IF r.overall_verdict IS DISTINCT FROM expected_verdict THEN PERFORM public.cc_fail('ct_review_integrity'); END IF;
    END IF;
  END IF;
  FOR e IN SELECT ev.evidence_id FROM public.evidence_records ev JOIN public.item_results i ON i.result_id = ev.item_result_id WHERE i.run_id = run_uuid LOOP
    PERFORM public.cc_check_evidence(e.evidence_id);
  END LOOP;
END $$;

CREATE FUNCTION public.cc_check_import(root_uuid uuid) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM public.import_packages p WHERE p.import_id = root_uuid AND p.import_state = 'completed'
       AND NOT EXISTS (SELECT 1 FROM public.stored_files f WHERE f.file_id = p.manifest_file_id
         AND f.import_package_id = p.import_id AND f.project_id = p.project_id
         AND f.file_role = 'import_manifest' AND f.state = 'stored')) THEN
    PERFORM public.cc_fail('ct_import_integrity');
  END IF;
END $$;

CREATE FUNCTION public.cc_check_lineage(table_name text, row_uuid uuid) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE key_column text; parent_column text; cycle boolean; p uuid; child_version bigint; parent_version bigint;
BEGIN
  SELECT x.key_name,x.parent_name INTO key_column,parent_column FROM (VALUES
    ('assets','asset_id','parent_asset_id'),('stored_files','file_id','parent_file_id'),
    ('image_coordinate_frames','frame_id','parent_frame_id'),('import_packages','import_id','replaces_import_id'),
    ('poster_extractions','extraction_id','predecessor_id'),('criteria_versions','criteria_version_id','source_criteria_version_id'),
    ('review_runs','run_id','retry_of_run_id')) x(table_name,key_name,parent_name) WHERE x.table_name = cc_check_lineage.table_name;
  IF key_column IS NULL THEN PERFORM public.cc_fail('cc_lineage_target'); END IF;
  EXECUTE format('SELECT project_id FROM public.%I WHERE %I=$1',table_name,key_column) INTO p USING row_uuid;
  IF table_name = 'assets' THEN PERFORM public.cc_lock_project(p); END IF;
  EXECUTE format('WITH RECURSIVE chain AS (
    SELECT %1$I id,%2$I parent,ARRAY[%1$I] path,false cycle FROM public.%3$I WHERE %1$I=$1
    UNION ALL SELECT t.%1$I,t.%2$I,c.path||t.%1$I,t.%1$I=ANY(c.path)
      FROM public.%3$I t JOIN chain c ON t.%1$I=c.parent WHERE NOT c.cycle)
    SELECT coalesce(bool_or(cycle),false) FROM chain',key_column,parent_column,table_name) INTO cycle USING row_uuid;
  IF cycle THEN PERFORM public.cc_fail('ct_lineage_'||table_name); END IF;
  IF table_name IN ('poster_extractions','criteria_versions') THEN
    EXECUTE format('SELECT c.version_no,p.version_no FROM public.%1$I c JOIN public.%1$I p ON p.%2$I=c.%3$I WHERE c.%2$I=$1',
      table_name,key_column,parent_column) INTO child_version,parent_version USING row_uuid;
    IF parent_version >= child_version THEN PERFORM public.cc_fail('ct_lineage_'||table_name); END IF;
  ELSIF table_name = 'review_runs' THEN
    IF EXISTS (SELECT 1 FROM public.review_runs c JOIN public.review_runs p ON p.run_id = c.retry_of_run_id
         WHERE c.run_id = row_uuid AND p.execution_status NOT IN ('completed','failed','imported')) THEN
      PERFORM public.cc_fail('ct_lineage_review_runs');
    END IF;
  ELSIF table_name = 'image_coordinate_frames' THEN
    PERFORM public.cc_check_frame(row_uuid);
  END IF;
END $$;

CREATE FUNCTION public.cc_snapshot_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE r public.review_runs; parent_uuid uuid; project_uuid uuid; d public.criteria_drafts; cv public.criteria_versions;
BEGIN
  CASE TG_NAME
    WHEN 'ct_ocr_snapshot' THEN PERFORM public.cc_check_ocr(NEW.ocr_run_id);
    WHEN 'ct_extraction_snapshot' THEN PERFORM public.cc_check_extraction(NEW.extraction_id);
    WHEN 'ct_criteria_snapshot' THEN
      PERFORM public.cc_check_criteria(NEW.criteria_version_id);
      IF TG_OP = 'INSERT' THEN
        SELECT * INTO STRICT cv FROM public.criteria_versions WHERE criteria_version_id = NEW.criteria_version_id;
        IF cv.origin_kind = 'service_confirmation' THEN
          PERFORM public.cc_lock_project(cv.project_id);
          SELECT * INTO STRICT d FROM public.criteria_drafts WHERE draft_id = cv.source_draft_id FOR UPDATE;
          IF d.revision IS DISTINCT FROM cv.source_draft_revision OR d.raw_requirements IS DISTINCT FROM cv.source_requirements_text THEN
            PERFORM public.cc_fail(TG_NAME);
          END IF;
        END IF;
      END IF;
    WHEN 'ct_review_integrity' THEN
      parent_uuid := CASE WHEN TG_TABLE_NAME = 'review_runs' THEN NEW.run_id ELSE NEW.run_id END;
      IF TG_TABLE_NAME IN ('item_results','run_item_selections') THEN
        SELECT * INTO STRICT r FROM public.review_runs WHERE run_id = parent_uuid;
        IF r.execution_status NOT IN ('completed','imported') OR r.sealed_at IS NULL THEN PERFORM public.cc_fail(TG_NAME); END IF;
      ELSE
        PERFORM public.cc_check_review(parent_uuid);
      END IF;
    WHEN 'ct_import_integrity' THEN PERFORM public.cc_check_import(NEW.import_id);
    WHEN 'ct_project_owner' THEN
      IF TG_TABLE_NAME = 'users' THEN
        FOR project_uuid IN SELECT project_id FROM public.project_members WHERE user_id = NEW.user_id ORDER BY project_id LOOP
          PERFORM public.cc_check_owner(project_uuid);
        END LOOP;
      ELSE PERFORM public.cc_check_owner(NEW.project_id);
      END IF;
    ELSE
      parent_uuid := (to_jsonb(NEW)->>TG_ARGV[0])::uuid;
      PERFORM public.cc_check_lineage(TG_TABLE_NAME,parent_uuid);
  END CASE;
  RETURN NULL;
END $$;

CREATE TRIGGER tr_schema_definitions_transition BEFORE INSERT OR UPDATE ON public.schema_definitions
FOR EACH ROW EXECUTE FUNCTION public.cc_transition();

CREATE TRIGGER tr_stored_files_transition BEFORE UPDATE ON public.stored_files
FOR EACH ROW EXECUTE FUNCTION public.cc_transition();

CREATE TRIGGER tr_import_packages_transition BEFORE UPDATE ON public.import_packages
FOR EACH ROW EXECUTE FUNCTION public.cc_transition();

CREATE TRIGGER tr_ocr_runs_transition BEFORE INSERT OR UPDATE ON public.ocr_runs
FOR EACH ROW EXECUTE FUNCTION public.cc_transition();

CREATE TRIGGER tr_poster_extractions_transition BEFORE UPDATE ON public.poster_extractions
FOR EACH ROW EXECUTE FUNCTION public.cc_transition();

CREATE TRIGGER tr_criteria_versions_transition BEFORE UPDATE ON public.criteria_versions
FOR EACH ROW EXECUTE FUNCTION public.cc_transition();

CREATE TRIGGER tr_criteria_drafts_transition BEFORE UPDATE ON public.criteria_drafts
FOR EACH ROW EXECUTE FUNCTION public.cc_transition();

CREATE TRIGGER tr_review_runs_transition BEFORE INSERT OR UPDATE ON public.review_runs
FOR EACH ROW EXECUTE FUNCTION public.cc_transition();

CREATE TRIGGER tr_review_attempts_transition BEFORE INSERT OR UPDATE ON public.review_attempts
FOR EACH ROW EXECUTE FUNCTION public.cc_transition();

CREATE TRIGGER tr_ocr_tokens_append_gate BEFORE INSERT ON public.ocr_tokens
FOR EACH ROW EXECUTE FUNCTION public.cc_append_gate();

CREATE TRIGGER tr_poster_field_observations_append_gate BEFORE INSERT ON public.poster_field_observations
FOR EACH ROW EXECUTE FUNCTION public.cc_append_gate();

CREATE TRIGGER tr_criteria_items_append_gate BEFORE INSERT ON public.criteria_items
FOR EACH ROW EXECUTE FUNCTION public.cc_append_gate();

CREATE TRIGGER tr_item_results_append_gate BEFORE INSERT ON public.item_results
FOR EACH ROW EXECUTE FUNCTION public.cc_append_gate();

CREATE TRIGGER tr_run_item_selections_append_gate BEFORE INSERT ON public.run_item_selections
FOR EACH ROW EXECUTE FUNCTION public.cc_append_gate();

CREATE TRIGGER tr_evidence_records_append_gate BEFORE INSERT ON public.evidence_records
FOR EACH ROW EXECUTE FUNCTION public.cc_append_gate();

CREATE TRIGGER tr_evidence_ocr_tokens_append_gate BEFORE INSERT ON public.evidence_ocr_tokens
FOR EACH ROW EXECUTE FUNCTION public.cc_append_gate();

CREATE CONSTRAINT TRIGGER ct_ocr_snapshot AFTER INSERT OR UPDATE ON public.ocr_runs
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.cc_snapshot_guard();

CREATE CONSTRAINT TRIGGER ct_extraction_snapshot AFTER INSERT OR UPDATE ON public.poster_extractions
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.cc_snapshot_guard();

CREATE CONSTRAINT TRIGGER ct_criteria_snapshot AFTER INSERT OR UPDATE ON public.criteria_versions
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.cc_snapshot_guard();

CREATE CONSTRAINT TRIGGER ct_review_integrity AFTER INSERT OR UPDATE ON public.review_runs
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.cc_snapshot_guard();

CREATE CONSTRAINT TRIGGER ct_review_integrity AFTER INSERT OR UPDATE ON public.review_attempts
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.cc_snapshot_guard();

CREATE CONSTRAINT TRIGGER ct_review_integrity AFTER INSERT ON public.item_results
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.cc_snapshot_guard();

CREATE CONSTRAINT TRIGGER ct_review_integrity AFTER INSERT ON public.run_item_selections
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.cc_snapshot_guard();

CREATE CONSTRAINT TRIGGER ct_import_integrity AFTER INSERT OR UPDATE ON public.import_packages
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.cc_snapshot_guard();

CREATE CONSTRAINT TRIGGER ct_project_owner AFTER INSERT OR UPDATE ON public.projects
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.cc_snapshot_guard();

CREATE CONSTRAINT TRIGGER ct_project_owner AFTER INSERT OR UPDATE ON public.project_members
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.cc_snapshot_guard();

CREATE CONSTRAINT TRIGGER ct_project_owner AFTER INSERT OR UPDATE ON public.users
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.cc_snapshot_guard();

CREATE CONSTRAINT TRIGGER ct_lineage_assets AFTER INSERT OR UPDATE ON public.assets
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.cc_snapshot_guard('asset_id');

CREATE CONSTRAINT TRIGGER ct_lineage_stored_files AFTER INSERT OR UPDATE ON public.stored_files
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.cc_snapshot_guard('file_id');

CREATE CONSTRAINT TRIGGER ct_lineage_image_coordinate_frames AFTER INSERT OR UPDATE ON public.image_coordinate_frames
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.cc_snapshot_guard('frame_id');

CREATE CONSTRAINT TRIGGER ct_lineage_import_packages AFTER INSERT OR UPDATE ON public.import_packages
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.cc_snapshot_guard('import_id');

CREATE CONSTRAINT TRIGGER ct_lineage_poster_extractions AFTER INSERT OR UPDATE ON public.poster_extractions
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.cc_snapshot_guard('extraction_id');

CREATE CONSTRAINT TRIGGER ct_lineage_criteria_versions AFTER INSERT OR UPDATE ON public.criteria_versions
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.cc_snapshot_guard('criteria_version_id');

CREATE CONSTRAINT TRIGGER ct_lineage_review_runs AFTER INSERT OR UPDATE ON public.review_runs
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.cc_snapshot_guard('run_id');

CREATE TRIGGER tr_stored_files_import_gate BEFORE INSERT OR UPDATE OF import_package_id ON public.stored_files
FOR EACH ROW EXECUTE FUNCTION public.cc_stored_import_gate();

DO $$ DECLARE item record; BEGIN
  FOR item IN SELECT * FROM public.schema_definitions LOOP PERFORM public.cc_schema_files(item.specification_file_id,item.validation_schema_file_id,item.schema_sha256); END LOOP;
  FOR item IN SELECT project_id id FROM public.projects ORDER BY project_id LOOP PERFORM public.cc_check_owner(item.id); END LOOP;
  FOR item IN SELECT ocr_run_id id FROM public.ocr_runs ORDER BY ocr_run_id LOOP PERFORM public.cc_check_ocr(item.id); END LOOP;
  FOR item IN SELECT extraction_id id FROM public.poster_extractions ORDER BY extraction_id LOOP PERFORM public.cc_check_extraction(item.id); END LOOP;
  FOR item IN SELECT criteria_version_id id FROM public.criteria_versions ORDER BY criteria_version_id LOOP PERFORM public.cc_check_criteria(item.id); END LOOP;
  FOR item IN SELECT run_id id FROM public.review_runs ORDER BY run_id LOOP PERFORM public.cc_check_review(item.id); END LOOP;
  FOR item IN SELECT import_id id FROM public.import_packages ORDER BY import_id LOOP PERFORM public.cc_check_import(item.id); END LOOP;
  FOR item IN SELECT asset_id id FROM public.assets ORDER BY asset_id LOOP PERFORM public.cc_check_lineage('assets',item.id); END LOOP;
  FOR item IN SELECT file_id id FROM public.stored_files ORDER BY file_id LOOP PERFORM public.cc_check_lineage('stored_files',item.id); END LOOP;
  FOR item IN SELECT frame_id id FROM public.image_coordinate_frames ORDER BY frame_id LOOP PERFORM public.cc_check_lineage('image_coordinate_frames',item.id); END LOOP;
  FOR item IN SELECT import_id id FROM public.import_packages ORDER BY import_id LOOP PERFORM public.cc_check_lineage('import_packages',item.id); END LOOP;
  FOR item IN SELECT extraction_id id FROM public.poster_extractions ORDER BY extraction_id LOOP PERFORM public.cc_check_lineage('poster_extractions',item.id); END LOOP;
  FOR item IN SELECT criteria_version_id id FROM public.criteria_versions ORDER BY criteria_version_id LOOP PERFORM public.cc_check_lineage('criteria_versions',item.id); END LOOP;
  FOR item IN SELECT run_id id FROM public.review_runs ORDER BY run_id LOOP PERFORM public.cc_check_lineage('review_runs',item.id); END LOOP;
END $$;
REVOKE ALL ON FUNCTION public.cc_fail(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.cc_lock_project(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.cc_check_owner(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.cc_check_bounds(jsonb, jsonb, uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.cc_check_frame(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.cc_lock_live_run(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.cc_transition() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.cc_gate_owner(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.cc_append_gate() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.cc_stored_import_gate() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.cc_check_evidence(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.cc_check_ocr(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.cc_check_extraction(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.cc_check_criteria(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.cc_check_review(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.cc_check_import(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.cc_check_lineage(text, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.cc_snapshot_guard() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.cc_schema_files(uuid, uuid, text) FROM PUBLIC;
