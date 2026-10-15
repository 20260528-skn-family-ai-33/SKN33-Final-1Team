-- 크리에이티브체커 관계도 가져오기용: 논리명 한글, 물리명 영어
-- 작성 기준일: 2026년 10월 14일
-- 테이블과 컬럼 및 외래키 제약명은 소문자 영어와 밑줄로 표기합니다.
-- 표와 속성의 한글 논리명은 설명 문장에 그대로 보존합니다.
-- 기존 모델의 엔터티와 속성 및 키와 관계와 자료형은 유지합니다.
-- 자료형은 관계도 가져오기를 위한 임시 지정이며 운영 배포용 완성 스키마가 아닙니다.
-- 모든 표를 먼저 정의하고 뒤에서 관계와 한글 설명을 추가합니다.
-- 실제 데이터 생성이나 삭제 및 검수 실행 문장은 포함하지 않습니다.

-- 사용자와 자료 관리

-- 사용자
CREATE TABLE "users" (
    "user_id" UUID NOT NULL,
    "email" VARCHAR(254) NOT NULL,
    "display_name" VARCHAR(100) NOT NULL,
    "is_active" BOOLEAN NOT NULL,
    "created_at" TIMESTAMP NOT NULL,
    PRIMARY KEY ("user_id"),
    UNIQUE ("email")
);

-- 프로젝트
CREATE TABLE "projects" (
    "project_id" UUID NOT NULL,
    "created_by" UUID NOT NULL,
    "name" VARCHAR(200) NOT NULL,
    "purpose" TEXT,
    "use_channels" TEXT,
    "due_at" TIMESTAMP,
    "business_state" VARCHAR(40) NOT NULL,
    "created_at" TIMESTAMP NOT NULL,
    PRIMARY KEY ("project_id")
);

-- 프로젝트참여자
CREATE TABLE "project_members" (
    "project_id" UUID NOT NULL,
    "user_id" UUID NOT NULL,
    "role" VARCHAR(40) NOT NULL,
    "is_active" BOOLEAN NOT NULL,
    "created_at" TIMESTAMP NOT NULL,
    PRIMARY KEY ("project_id", "user_id")
);

-- 자료
CREATE TABLE "assets" (
    "asset_id" UUID NOT NULL,
    "project_id" UUID NOT NULL,
    "parent_asset_id" UUID,
    "asset_kind" VARCHAR(50) NOT NULL,
    "name" VARCHAR(200) NOT NULL,
    PRIMARY KEY ("asset_id")
);

-- 자료버전
CREATE TABLE "asset_versions" (
    "asset_version_id" UUID NOT NULL,
    "asset_id" UUID NOT NULL,
    "version_no" INTEGER NOT NULL,
    "original_file_id" UUID,
    "display_file_id" UUID,
    "original_sha256" VARCHAR(64) NOT NULL,
    "original_width" INTEGER,
    "original_height" INTEGER,
    "display_width" INTEGER,
    "display_height" INTEGER,
    "verification_state" VARCHAR(40) NOT NULL,
    "measurement_metadata" TEXT,
    "created_at" TIMESTAMP NOT NULL,
    PRIMARY KEY ("asset_version_id"),
    UNIQUE ("asset_id", "version_no")
);

-- 저장파일
CREATE TABLE "stored_files" (
    "file_id" UUID NOT NULL,
    "project_id" UUID NOT NULL,
    "parent_file_id" UUID,
    "import_package_id" UUID,
    "file_role" VARCHAR(50) NOT NULL,
    "original_name" VARCHAR(255) NOT NULL,
    "detected_mime" VARCHAR(100),
    "byte_size" BIGINT,
    "sha256" VARCHAR(64),
    "state" VARCHAR(40) NOT NULL,
    "source_metadata" TEXT,
    "created_at" TIMESTAMP NOT NULL,
    PRIMARY KEY ("file_id")
);

-- 이미지좌표계
CREATE TABLE "image_coordinate_frames" (
    "frame_id" UUID NOT NULL,
    "asset_version_id" UUID NOT NULL,
    "file_id" UUID,
    "parent_frame_id" UUID,
    "frame_kind" VARCHAR(50) NOT NULL,
    "width" INTEGER NOT NULL,
    "height" INTEGER NOT NULL,
    "verification_state" VARCHAR(40) NOT NULL,
    "transform_jsonb" TEXT,
    PRIMARY KEY ("frame_id")
);

-- 외부식별별칭
CREATE TABLE "record_aliases" (
    "alias_id" UUID NOT NULL,
    "asset_version_id" UUID NOT NULL,
    "import_package_id" UUID,
    "namespace" VARCHAR(100) NOT NULL,
    "external_record_id" VARCHAR(200) NOT NULL,
    PRIMARY KEY ("alias_id")
);

-- 데이터규격
CREATE TABLE "schema_definitions" (
    "schema_id" UUID NOT NULL,
    "schema_name" VARCHAR(150) NOT NULL,
    "schema_version" VARCHAR(40) NOT NULL,
    "specification_file_id" UUID,
    "validation_schema_file_id" UUID,
    "availability_state" VARCHAR(40) NOT NULL,
    "field_catalog_jsonb" TEXT,
    PRIMARY KEY ("schema_id"),
    UNIQUE ("schema_name", "schema_version")
);

-- 인계묶음
CREATE TABLE "import_packages" (
    "import_id" UUID NOT NULL,
    "project_id" UUID NOT NULL,
    "manifest_file_id" UUID,
    "replaces_import_id" UUID,
    "source_namespace" VARCHAR(100) NOT NULL,
    "external_record_id" VARCHAR(200),
    "package_fingerprint" VARCHAR(64) NOT NULL,
    "hash_check_state" VARCHAR(40) NOT NULL,
    "import_state" VARCHAR(40) NOT NULL,
    "reported_validation_jsonb" TEXT,
    "imported_at" TIMESTAMP NOT NULL,
    PRIMARY KEY ("import_id"),
    UNIQUE ("project_id", "source_namespace", "package_fingerprint")
);

-- 감사이력
CREATE TABLE "audit_events" (
    "event_id" UUID NOT NULL,
    "project_id" UUID NOT NULL,
    "actor_user_id" UUID,
    "action" VARCHAR(100) NOT NULL,
    "target_type" VARCHAR(100) NOT NULL,
    "target_id" UUID,
    "event_metadata" TEXT,
    "occurred_at" TIMESTAMP NOT NULL,
    PRIMARY KEY ("event_id")
);

-- 문자인식과 포스터 관찰

-- 문자인식실행
CREATE TABLE "ocr_runs" (
    "ocr_run_id" UUID NOT NULL,
    "asset_version_id" UUID NOT NULL,
    "attempt_id" UUID,
    "import_package_id" UUID,
    "schema_id" UUID NOT NULL,
    "source_file_id" UUID NOT NULL,
    "frame_id" UUID NOT NULL,
    "execution_status" VARCHAR(40) NOT NULL,
    "engine_name" VARCHAR(100),
    "engine_version" VARCHAR(100),
    "model_name" VARCHAR(200),
    "device" VARCHAR(100),
    "reported_coordinate_space" VARCHAR(100) NOT NULL,
    "engine_settings" TEXT,
    "started_at" TIMESTAMP,
    "completed_at" TIMESTAMP,
    PRIMARY KEY ("ocr_run_id")
);

-- 인식문구
CREATE TABLE "ocr_tokens" (
    "token_id" UUID NOT NULL,
    "ocr_run_id" UUID NOT NULL,
    "external_token_id" VARCHAR(150) NOT NULL,
    "token_order" INTEGER NOT NULL,
    "text" TEXT NOT NULL,
    "confidence" NUMERIC(8,6),
    "bbox_xyxy" TEXT,
    "polygon" TEXT,
    PRIMARY KEY ("token_id"),
    UNIQUE ("ocr_run_id", "external_token_id"),
    UNIQUE ("ocr_run_id", "token_order")
);

-- 포스터추출
CREATE TABLE "poster_extractions" (
    "extraction_id" UUID NOT NULL,
    "asset_version_id" UUID NOT NULL,
    "schema_id" UUID NOT NULL,
    "ocr_run_id" UUID,
    "predecessor_id" UUID,
    "source_file_id" UUID,
    "version_no" INTEGER NOT NULL,
    "method" VARCHAR(50) NOT NULL,
    "extractor_version" VARCHAR(100),
    "payload_jsonb" TEXT NOT NULL,
    "title_official" VARCHAR(255),
    "year_value" INTEGER,
    "start_year" INTEGER,
    "start_month" INTEGER,
    "start_day" INTEGER,
    "end_year" INTEGER,
    "end_month" INTEGER,
    "end_day" INTEGER,
    "start_date" DATE,
    "end_date" DATE,
    "compatibility_state" VARCHAR(40) NOT NULL,
    "validation_state" VARCHAR(40) NOT NULL,
    "checks_jsonb" TEXT,
    PRIMARY KEY ("extraction_id"),
    UNIQUE ("asset_version_id", "version_no")
);

-- 포스터항목관찰
CREATE TABLE "poster_field_observations" (
    "observation_id" UUID NOT NULL,
    "extraction_id" UUID NOT NULL,
    "field_code" VARCHAR(100) NOT NULL,
    "field_path" VARCHAR(255) NOT NULL,
    "presence_status" VARCHAR(40) NOT NULL,
    "resolution_status" VARCHAR(40) NOT NULL,
    "raw_text" TEXT,
    "normalized_value" TEXT,
    "source_reason" TEXT,
    PRIMARY KEY ("observation_id"),
    UNIQUE ("extraction_id", "field_path")
);

-- 근거
CREATE TABLE "evidence_records" (
    "evidence_id" UUID NOT NULL,
    "observation_id" UUID,
    "item_result_id" UUID,
    "reference_field_result_id" UUID,
    "human_decision_id" UUID,
    "asset_version_id" UUID,
    "source_file_id" UUID,
    "frame_id" UUID,
    "source_ocr_run_id" UUID,
    "reference_record_id" UUID,
    "source_kind" VARCHAR(40) NOT NULL,
    "field_pointer" VARCHAR(255),
    "page_no" INTEGER,
    "text" TEXT,
    "bbox_xyxy" TEXT,
    "method_code" VARCHAR(100) NOT NULL,
    "limitations_jsonb" TEXT,
    PRIMARY KEY ("evidence_id")
);

-- 근거문구연결
CREATE TABLE "evidence_ocr_tokens" (
    "evidence_id" UUID NOT NULL,
    "token_id" UUID NOT NULL,
    "token_order" INTEGER NOT NULL,
    PRIMARY KEY ("evidence_id", "token_id")
);

-- 검수 기준과 실행

-- 검수기준묶음
CREATE TABLE "criteria_sets" (
    "criteria_set_id" UUID NOT NULL,
    "project_id" UUID NOT NULL,
    "name" VARCHAR(200) NOT NULL,
    PRIMARY KEY ("criteria_set_id")
);

-- 검수기준초안
CREATE TABLE "criteria_drafts" (
    "draft_id" UUID NOT NULL,
    "criteria_set_id" UUID NOT NULL,
    "target_asset_version_id" UUID NOT NULL,
    "revision" INTEGER NOT NULL,
    "raw_requirements" TEXT NOT NULL,
    "items_jsonb" TEXT NOT NULL,
    "state" VARCHAR(40) NOT NULL,
    "updated_by" UUID NOT NULL,
    "updated_at" TIMESTAMP NOT NULL,
    PRIMARY KEY ("draft_id")
);

-- 확정검수기준
CREATE TABLE "criteria_versions" (
    "criteria_version_id" UUID NOT NULL,
    "criteria_set_id" UUID NOT NULL,
    "target_asset_version_id" UUID NOT NULL,
    "source_draft_id" UUID,
    "source_criteria_version_id" UUID,
    "import_package_id" UUID,
    "version_no" INTEGER NOT NULL,
    "source_draft_revision" INTEGER,
    "source_requirements_text" TEXT,
    "confirmed_by" UUID,
    "confirmed_at" TIMESTAMP,
    "origin_kind" VARCHAR(40) NOT NULL,
    "source_confirmation_state" VARCHAR(40) NOT NULL,
    PRIMARY KEY ("criteria_version_id"),
    UNIQUE ("criteria_set_id", "version_no")
);

-- 검수기준항목
CREATE TABLE "criteria_items" (
    "criteria_item_id" UUID NOT NULL,
    "criteria_version_id" UUID NOT NULL,
    "position" INTEGER NOT NULL,
    "category" VARCHAR(50) NOT NULL,
    "rule_code" VARCHAR(100),
    "text" TEXT NOT NULL,
    "expected_value" TEXT,
    "is_required" BOOLEAN NOT NULL,
    "source_asset_version_id" UUID,
    "source_reference_record_id" UUID,
    "source_locator" TEXT,
    "applicability_jsonb" TEXT,
    "check_scope_jsonb" TEXT,
    PRIMARY KEY ("criteria_item_id"),
    UNIQUE ("criteria_version_id", "position")
);

-- 검수실행
CREATE TABLE "review_runs" (
    "run_id" UUID NOT NULL,
    "project_id" UUID NOT NULL,
    "asset_version_id" UUID NOT NULL,
    "criteria_version_id" UUID NOT NULL,
    "submission_version_id" UUID,
    "retry_of_run_id" UUID,
    "execution_status" VARCHAR(40) NOT NULL,
    "overall_verdict" VARCHAR(40),
    "orchestration_mode" VARCHAR(40) NOT NULL,
    "workflow_version" VARCHAR(100),
    "aggregation_policy_version" VARCHAR(100) NOT NULL,
    "conflict_policy_version" VARCHAR(100),
    "idempotency_key" VARCHAR(200) NOT NULL,
    "requested_config" TEXT,
    "started_at" TIMESTAMP,
    "finished_at" TIMESTAMP,
    "sealed_at" TIMESTAMP,
    PRIMARY KEY ("run_id"),
    UNIQUE ("project_id", "idempotency_key")
);

-- 작업처리시도
CREATE TABLE "review_attempts" (
    "attempt_id" UUID NOT NULL,
    "run_id" UUID NOT NULL,
    "work_unit_id" UUID,
    "attempt_no" INTEGER NOT NULL,
    "stage" VARCHAR(50) NOT NULL,
    "execution_status" VARCHAR(40) NOT NULL,
    "actual_runtime" TEXT,
    "error_jsonb" TEXT,
    "started_at" TIMESTAMP,
    "finished_at" TIMESTAMP,
    PRIMARY KEY ("attempt_id"),
    UNIQUE ("work_unit_id", "attempt_no")
);

-- 검수항목결과
CREATE TABLE "item_results" (
    "result_id" UUID NOT NULL,
    "run_id" UUID NOT NULL,
    "attempt_id" UUID NOT NULL,
    "work_unit_id" UUID,
    "agent_execution_id" UUID,
    "adjudicates_conflict_id" UUID,
    "criteria_item_id" UUID NOT NULL,
    "ocr_run_id" UUID,
    "extraction_id" UUID,
    "reference_check_id" UUID,
    "execution_status" VARCHAR(40) NOT NULL,
    "verdict" VARCHAR(40),
    "observed_value" TEXT,
    "reason" TEXT NOT NULL,
    "method_code" VARCHAR(100),
    "limitations_jsonb" TEXT,
    "next_action_jsonb" TEXT,
    PRIMARY KEY ("result_id"),
    UNIQUE ("attempt_id", "criteria_item_id")
);

-- 현재항목채택
CREATE TABLE "run_item_selections" (
    "run_id" UUID NOT NULL,
    "criteria_item_id" UUID NOT NULL,
    "result_id" UUID NOT NULL,
    "selection_event_id" UUID,
    "selection_revision" INTEGER NOT NULL,
    "selected_at" TIMESTAMP NOT NULL,
    PRIMARY KEY ("run_id", "criteria_item_id")
);

-- 에이전트와 병렬 작업

-- 에이전트정의
CREATE TABLE "agent_definitions" (
    "agent_definition_id" UUID NOT NULL,
    "project_id" UUID NOT NULL,
    "agent_key" VARCHAR(150) NOT NULL,
    "role" VARCHAR(100) NOT NULL,
    "definition_version" INTEGER NOT NULL,
    "prompt_file_id" UUID,
    "input_schema_id" UUID,
    "output_schema_id" UUID,
    "contract_version" VARCHAR(100) NOT NULL,
    "model_name" VARCHAR(200),
    "model_config" TEXT,
    "tool_policy" TEXT,
    "context_policy" TEXT,
    "state" VARCHAR(40) NOT NULL,
    PRIMARY KEY ("agent_definition_id"),
    UNIQUE ("project_id", "agent_key", "definition_version")
);

-- 검수작업
CREATE TABLE "review_work_units" (
    "work_unit_id" UUID NOT NULL,
    "run_id" UUID NOT NULL,
    "unit_key" VARCHAR(200) NOT NULL,
    "stage" VARCHAR(50) NOT NULL,
    "executor_kind" VARCHAR(40) NOT NULL,
    "agent_definition_id" UUID,
    "output_kind" VARCHAR(40) NOT NULL,
    "is_required" BOOLEAN NOT NULL,
    "scope_jsonb" TEXT,
    "retry_policy" TEXT,
    "completion_policy" TEXT,
    "state" VARCHAR(40) NOT NULL,
    "blocked_reason" TEXT,
    "input_manifest_file_id" UUID,
    "active_attempt_id" UUID,
    "selected_attempt_id" UUID,
    PRIMARY KEY ("work_unit_id"),
    UNIQUE ("run_id", "unit_key")
);

-- 작업기준배정
CREATE TABLE "work_unit_criteria" (
    "work_unit_id" UUID NOT NULL,
    "criteria_item_id" UUID NOT NULL,
    "binding_role" VARCHAR(40) NOT NULL,
    "is_output_required" BOOLEAN NOT NULL,
    "bound_conflict_id" UUID,
    "bound_candidate_set_version" INTEGER,
    "bound_candidate_set_hash" VARCHAR(64),
    PRIMARY KEY ("work_unit_id", "criteria_item_id")
);

-- 작업의존관계
CREATE TABLE "work_unit_dependencies" (
    "dependent_work_unit_id" UUID NOT NULL,
    "upstream_work_unit_id" UUID NOT NULL,
    "input_role" VARCHAR(100) NOT NULL,
    "gate_policy" VARCHAR(40) NOT NULL,
    "accepted_source_attempt_id" UUID,
    "source_ocr_run_id" UUID,
    "source_extraction_id" UUID,
    "source_reference_check_id" UUID,
    "source_file_id" UUID,
    PRIMARY KEY ("dependent_work_unit_id", "upstream_work_unit_id", "input_role")
);

-- 에이전트실행
CREATE TABLE "agent_executions" (
    "agent_execution_id" UUID NOT NULL,
    "agent_definition_id" UUID NOT NULL,
    "attempt_id" UUID,
    "draft_id" UUID,
    "draft_revision" INTEGER,
    "draft_analysis_key" VARCHAR(200),
    "execution_scope" VARCHAR(40) NOT NULL,
    "invocation_no" INTEGER NOT NULL,
    "parent_execution_id" UUID,
    "retry_of_execution_id" UUID,
    "input_manifest_file_id" UUID NOT NULL,
    "output_file_id" UUID,
    "execution_status" VARCHAR(40) NOT NULL,
    "output_validation_state" VARCHAR(40) NOT NULL,
    "write_admission_status" VARCHAR(40) NOT NULL,
    "actual_model_name" VARCHAR(200),
    "actual_model_revision" VARCHAR(150),
    "output_summary_jsonb" TEXT,
    "error_jsonb" TEXT,
    "started_at" TIMESTAMP,
    "finished_at" TIMESTAMP,
    PRIMARY KEY ("agent_execution_id"),
    UNIQUE ("attempt_id", "invocation_no"),
    UNIQUE ("draft_id", "draft_revision", "draft_analysis_key", "invocation_no")
);

-- 도구호출
CREATE TABLE "agent_tool_calls" (
    "tool_call_id" UUID NOT NULL,
    "agent_execution_id" UUID NOT NULL,
    "logical_call_key" VARCHAR(200) NOT NULL,
    "delivery_no" INTEGER NOT NULL,
    "retry_of_tool_call_id" UUID,
    "tool_name" VARCHAR(150) NOT NULL,
    "tool_version" VARCHAR(100) NOT NULL,
    "operation_kind" VARCHAR(50) NOT NULL,
    "arguments_file_id" UUID NOT NULL,
    "response_file_id" UUID,
    "state" VARCHAR(40) NOT NULL,
    "provider_request_id" VARCHAR(200),
    "error_jsonb" TEXT,
    "started_at" TIMESTAMP,
    "finished_at" TIMESTAMP,
    PRIMARY KEY ("tool_call_id"),
    UNIQUE ("agent_execution_id", "logical_call_key", "delivery_no")
);

-- 결과충돌
CREATE TABLE "result_conflicts" (
    "conflict_id" UUID NOT NULL,
    "run_id" UUID NOT NULL,
    "criteria_item_id" UUID NOT NULL,
    "conflict_type" VARCHAR(100) NOT NULL,
    "state" VARCHAR(40) NOT NULL,
    "candidate_set_version" INTEGER NOT NULL,
    "candidate_set_hash" VARCHAR(64) NOT NULL,
    "conflict_policy_version" VARCHAR(100) NOT NULL,
    "summary_jsonb" TEXT,
    "resolution_event_id" UUID,
    "opened_at" TIMESTAMP NOT NULL,
    "resolved_at" TIMESTAMP,
    PRIMARY KEY ("conflict_id"),
    UNIQUE ("run_id", "criteria_item_id")
);

-- 충돌후보
CREATE TABLE "conflict_candidates" (
    "conflict_id" UUID NOT NULL,
    "result_id" UUID NOT NULL,
    "added_in_version" INTEGER NOT NULL,
    "candidate_role" VARCHAR(40) NOT NULL,
    PRIMARY KEY ("conflict_id", "result_id")
);

-- 결과채택이력
CREATE TABLE "result_selection_events" (
    "selection_event_id" UUID NOT NULL,
    "run_id" UUID NOT NULL,
    "criteria_item_id" UUID NOT NULL,
    "selection_revision" INTEGER NOT NULL,
    "previous_event_id" UUID,
    "decision_key" VARCHAR(200) NOT NULL,
    "selected_result_id" UUID NOT NULL,
    "conflict_id" UUID,
    "candidate_set_version" INTEGER,
    "candidate_set_hash" VARCHAR(64),
    "selection_policy_version" VARCHAR(100) NOT NULL,
    "decision_kind" VARCHAR(50) NOT NULL,
    "reason" TEXT NOT NULL,
    "actor_kind" VARCHAR(40) NOT NULL,
    "actor_user_id" UUID,
    "actor_agent_execution_id" UUID,
    "decision_file_id" UUID,
    "decided_at" TIMESTAMP NOT NULL,
    PRIMARY KEY ("selection_event_id"),
    UNIQUE ("run_id", "criteria_item_id", "selection_revision"),
    UNIQUE ("run_id", "criteria_item_id", "decision_key")
);

-- 외부 행사 기준 비교

-- 외부기준수집본
CREATE TABLE "reference_snapshots" (
    "snapshot_id" UUID NOT NULL,
    "project_id" UUID NOT NULL,
    "source_system" VARCHAR(200) NOT NULL,
    "dataset_version" VARCHAR(100) NOT NULL,
    "source_url" TEXT,
    "retrieved_at" TIMESTAMP,
    "source_file_id" UUID NOT NULL,
    "normalized_file_id" UUID,
    "schema_id" UUID,
    "normalizer_version" VARCHAR(100) NOT NULL,
    "content_hash" VARCHAR(64) NOT NULL,
    PRIMARY KEY ("snapshot_id")
);

-- 외부행사기준
CREATE TABLE "reference_records" (
    "reference_record_id" UUID NOT NULL,
    "snapshot_id" UUID NOT NULL,
    "external_record_key" VARCHAR(200) NOT NULL,
    "provider_name" VARCHAR(200),
    "reference_date" DATE,
    "title" VARCHAR(255),
    "start_date" DATE,
    "end_date" DATE,
    "venue" TEXT,
    "payload_jsonb" TEXT NOT NULL,
    PRIMARY KEY ("reference_record_id"),
    UNIQUE ("snapshot_id", "external_record_key")
);

-- 외부기준비교
CREATE TABLE "reference_checks" (
    "reference_check_id" UUID NOT NULL,
    "extraction_id" UUID NOT NULL,
    "version_no" INTEGER NOT NULL,
    "predecessor_id" UUID,
    "selected_reference_record_id" UUID,
    "match_status" VARCHAR(40) NOT NULL,
    "comparison_execution_status" VARCHAR(40) NOT NULL,
    "comparison_policy_version" VARCHAR(100) NOT NULL,
    "selected_by" UUID,
    "selection_reason" TEXT,
    "source_result_file_id" UUID,
    "reported_summary_jsonb" TEXT,
    "verification_state" VARCHAR(40) NOT NULL,
    PRIMARY KEY ("reference_check_id"),
    UNIQUE ("extraction_id", "version_no")
);

-- 외부기준후보
CREATE TABLE "reference_candidates" (
    "reference_check_id" UUID NOT NULL,
    "reference_record_id" UUID NOT NULL,
    "rank" INTEGER,
    "match_score" NUMERIC(12,6),
    "match_evidence_jsonb" TEXT,
    PRIMARY KEY ("reference_check_id", "reference_record_id")
);

-- 외부항목비교결과
CREATE TABLE "reference_field_results" (
    "comparison_result_id" UUID NOT NULL,
    "reference_check_id" UUID NOT NULL,
    "field_path" VARCHAR(255) NOT NULL,
    "comparison_status" VARCHAR(40) NOT NULL,
    "poster_value" TEXT,
    "reference_value" TEXT,
    "reason" TEXT,
    PRIMARY KEY ("comparison_result_id"),
    UNIQUE ("reference_check_id", "field_path")
);

-- 납품과 보완 및 승인

-- 납품묶음버전
CREATE TABLE "submission_versions" (
    "submission_version_id" UUID NOT NULL,
    "project_id" UUID NOT NULL,
    "version_no" INTEGER NOT NULL,
    "previous_submission_id" UUID,
    "primary_asset_version_id" UUID NOT NULL,
    "declarations_jsonb" TEXT,
    "submitted_by" UUID NOT NULL,
    "submitted_at" TIMESTAMP NOT NULL,
    "sealed_at" TIMESTAMP NOT NULL,
    PRIMARY KEY ("submission_version_id"),
    UNIQUE ("project_id", "version_no")
);

-- 납품자료연결
CREATE TABLE "submission_assets" (
    "submission_version_id" UUID NOT NULL,
    "asset_version_id" UUID NOT NULL,
    "submission_role" VARCHAR(50) NOT NULL,
    "position" INTEGER NOT NULL,
    "submitted_metadata" TEXT,
    PRIMARY KEY ("submission_version_id", "asset_version_id", "submission_role")
);

-- 사람판정정정
CREATE TABLE "human_result_decisions" (
    "decision_id" UUID NOT NULL,
    "result_id" UUID NOT NULL,
    "supersedes_decision_id" UUID,
    "actor_user_id" UUID NOT NULL,
    "verdict" VARCHAR(40) NOT NULL,
    "reason" TEXT NOT NULL,
    "decided_at" TIMESTAMP NOT NULL,
    PRIMARY KEY ("decision_id")
);

-- 검수보고서
CREATE TABLE "review_reports" (
    "report_id" UUID NOT NULL,
    "run_id" UUID NOT NULL,
    "report_version" INTEGER NOT NULL,
    "report_file_id" UUID,
    "snapshot_jsonb" TEXT NOT NULL,
    "manifest_hash" VARCHAR(64) NOT NULL,
    "generated_at" TIMESTAMP NOT NULL,
    PRIMARY KEY ("report_id"),
    UNIQUE ("run_id", "report_version")
);

-- 사람승인이력
CREATE TABLE "approval_events" (
    "approval_event_id" UUID NOT NULL,
    "report_id" UUID NOT NULL,
    "actor_user_id" UUID NOT NULL,
    "purpose" VARCHAR(100) NOT NULL,
    "action" VARCHAR(50) NOT NULL,
    "reason" TEXT,
    "exception_jsonb" TEXT,
    "target_manifest_hash" VARCHAR(64) NOT NULL,
    "occurred_at" TIMESTAMP NOT NULL,
    PRIMARY KEY ("approval_event_id")
);

-- 보완작업
CREATE TABLE "remediation_tasks" (
    "task_id" UUID NOT NULL,
    "project_id" UUID NOT NULL,
    "source_run_id" UUID NOT NULL,
    "source_result_id" UUID NOT NULL,
    "criteria_item_id" UUID NOT NULL,
    "task_type" VARCHAR(50) NOT NULL,
    "request_text" TEXT NOT NULL,
    "assignee_user_id" UUID,
    "due_at" TIMESTAMP,
    "completion_criteria" TEXT NOT NULL,
    "state" VARCHAR(40) NOT NULL,
    "dedupe_key" VARCHAR(200) NOT NULL,
    PRIMARY KEY ("task_id"),
    UNIQUE ("project_id", "dedupe_key")
);

-- 보완제출이력
CREATE TABLE "remediation_submissions" (
    "task_submission_id" UUID NOT NULL,
    "task_id" UUID NOT NULL,
    "submission_version_id" UUID NOT NULL,
    "verification_run_id" UUID,
    "verification_result_id" UUID,
    "resolution_state" VARCHAR(40) NOT NULL,
    "verified_by" UUID,
    "submitted_at" TIMESTAMP NOT NULL,
    "verified_at" TIMESTAMP,
    PRIMARY KEY ("task_submission_id")
);

-- 표 사이의 관계

ALTER TABLE "projects" ADD CONSTRAINT "fk_projects_created_by"
    FOREIGN KEY ("created_by") REFERENCES "users" ("user_id");

ALTER TABLE "project_members" ADD CONSTRAINT "fk_project_members_project_id"
    FOREIGN KEY ("project_id") REFERENCES "projects" ("project_id");

ALTER TABLE "project_members" ADD CONSTRAINT "fk_project_members_user_id"
    FOREIGN KEY ("user_id") REFERENCES "users" ("user_id");

ALTER TABLE "assets" ADD CONSTRAINT "fk_assets_project_id"
    FOREIGN KEY ("project_id") REFERENCES "projects" ("project_id");

ALTER TABLE "assets" ADD CONSTRAINT "fk_assets_parent_asset_id"
    FOREIGN KEY ("parent_asset_id") REFERENCES "assets" ("asset_id");

ALTER TABLE "asset_versions" ADD CONSTRAINT "fk_asset_versions_asset_id"
    FOREIGN KEY ("asset_id") REFERENCES "assets" ("asset_id");

ALTER TABLE "asset_versions" ADD CONSTRAINT "fk_asset_versions_original_file_id"
    FOREIGN KEY ("original_file_id") REFERENCES "stored_files" ("file_id");

ALTER TABLE "asset_versions" ADD CONSTRAINT "fk_asset_versions_display_file_id"
    FOREIGN KEY ("display_file_id") REFERENCES "stored_files" ("file_id");

ALTER TABLE "stored_files" ADD CONSTRAINT "fk_stored_files_project_id"
    FOREIGN KEY ("project_id") REFERENCES "projects" ("project_id");

ALTER TABLE "stored_files" ADD CONSTRAINT "fk_stored_files_parent_file_id"
    FOREIGN KEY ("parent_file_id") REFERENCES "stored_files" ("file_id");

ALTER TABLE "stored_files" ADD CONSTRAINT "fk_stored_files_import_package_id"
    FOREIGN KEY ("import_package_id") REFERENCES "import_packages" ("import_id");

ALTER TABLE "image_coordinate_frames" ADD CONSTRAINT "fk_image_coordinate_frames_asset_version_id"
    FOREIGN KEY ("asset_version_id") REFERENCES "asset_versions" ("asset_version_id");

ALTER TABLE "image_coordinate_frames" ADD CONSTRAINT "fk_image_coordinate_frames_file_id"
    FOREIGN KEY ("file_id") REFERENCES "stored_files" ("file_id");

ALTER TABLE "image_coordinate_frames" ADD CONSTRAINT "fk_image_coordinate_frames_parent_frame_id"
    FOREIGN KEY ("parent_frame_id") REFERENCES "image_coordinate_frames" ("frame_id");

ALTER TABLE "record_aliases" ADD CONSTRAINT "fk_record_aliases_asset_version_id"
    FOREIGN KEY ("asset_version_id") REFERENCES "asset_versions" ("asset_version_id");

ALTER TABLE "record_aliases" ADD CONSTRAINT "fk_record_aliases_import_package_id"
    FOREIGN KEY ("import_package_id") REFERENCES "import_packages" ("import_id");

ALTER TABLE "schema_definitions" ADD CONSTRAINT "fk_schema_definitions_specification_file_id"
    FOREIGN KEY ("specification_file_id") REFERENCES "stored_files" ("file_id");

ALTER TABLE "schema_definitions" ADD CONSTRAINT "fk_schema_definitions_validation_schema_file_id"
    FOREIGN KEY ("validation_schema_file_id") REFERENCES "stored_files" ("file_id");

ALTER TABLE "import_packages" ADD CONSTRAINT "fk_import_packages_project_id"
    FOREIGN KEY ("project_id") REFERENCES "projects" ("project_id");

ALTER TABLE "import_packages" ADD CONSTRAINT "fk_import_packages_manifest_file_id"
    FOREIGN KEY ("manifest_file_id") REFERENCES "stored_files" ("file_id");

ALTER TABLE "import_packages" ADD CONSTRAINT "fk_import_packages_replaces_import_id"
    FOREIGN KEY ("replaces_import_id") REFERENCES "import_packages" ("import_id");

ALTER TABLE "audit_events" ADD CONSTRAINT "fk_audit_events_project_id"
    FOREIGN KEY ("project_id") REFERENCES "projects" ("project_id");

ALTER TABLE "audit_events" ADD CONSTRAINT "fk_audit_events_actor_user_id"
    FOREIGN KEY ("actor_user_id") REFERENCES "users" ("user_id");

ALTER TABLE "ocr_runs" ADD CONSTRAINT "fk_ocr_runs_asset_version_id"
    FOREIGN KEY ("asset_version_id") REFERENCES "asset_versions" ("asset_version_id");

ALTER TABLE "ocr_runs" ADD CONSTRAINT "fk_ocr_runs_attempt_id"
    FOREIGN KEY ("attempt_id") REFERENCES "review_attempts" ("attempt_id");

ALTER TABLE "ocr_runs" ADD CONSTRAINT "fk_ocr_runs_import_package_id"
    FOREIGN KEY ("import_package_id") REFERENCES "import_packages" ("import_id");

ALTER TABLE "ocr_runs" ADD CONSTRAINT "fk_ocr_runs_schema_id"
    FOREIGN KEY ("schema_id") REFERENCES "schema_definitions" ("schema_id");

ALTER TABLE "ocr_runs" ADD CONSTRAINT "fk_ocr_runs_source_file_id"
    FOREIGN KEY ("source_file_id") REFERENCES "stored_files" ("file_id");

ALTER TABLE "ocr_runs" ADD CONSTRAINT "fk_ocr_runs_frame_id"
    FOREIGN KEY ("frame_id") REFERENCES "image_coordinate_frames" ("frame_id");

ALTER TABLE "ocr_tokens" ADD CONSTRAINT "fk_ocr_tokens_ocr_run_id"
    FOREIGN KEY ("ocr_run_id") REFERENCES "ocr_runs" ("ocr_run_id");

ALTER TABLE "poster_extractions" ADD CONSTRAINT "fk_poster_extractions_asset_version_id"
    FOREIGN KEY ("asset_version_id") REFERENCES "asset_versions" ("asset_version_id");

ALTER TABLE "poster_extractions" ADD CONSTRAINT "fk_poster_extractions_schema_id"
    FOREIGN KEY ("schema_id") REFERENCES "schema_definitions" ("schema_id");

ALTER TABLE "poster_extractions" ADD CONSTRAINT "fk_poster_extractions_ocr_run_id"
    FOREIGN KEY ("ocr_run_id") REFERENCES "ocr_runs" ("ocr_run_id");

ALTER TABLE "poster_extractions" ADD CONSTRAINT "fk_poster_extractions_predecessor_id"
    FOREIGN KEY ("predecessor_id") REFERENCES "poster_extractions" ("extraction_id");

ALTER TABLE "poster_extractions" ADD CONSTRAINT "fk_poster_extractions_source_file_id"
    FOREIGN KEY ("source_file_id") REFERENCES "stored_files" ("file_id");

ALTER TABLE "poster_field_observations" ADD CONSTRAINT "fk_poster_field_observations_extraction_id"
    FOREIGN KEY ("extraction_id") REFERENCES "poster_extractions" ("extraction_id");

ALTER TABLE "evidence_records" ADD CONSTRAINT "fk_evidence_records_observation_id"
    FOREIGN KEY ("observation_id") REFERENCES "poster_field_observations" ("observation_id");

ALTER TABLE "evidence_records" ADD CONSTRAINT "fk_evidence_records_item_result_id"
    FOREIGN KEY ("item_result_id") REFERENCES "item_results" ("result_id");

ALTER TABLE "evidence_records" ADD CONSTRAINT "fk_evidence_records_reference_field_result_id"
    FOREIGN KEY ("reference_field_result_id") REFERENCES "reference_field_results" ("comparison_result_id");

ALTER TABLE "evidence_records" ADD CONSTRAINT "fk_evidence_records_human_decision_id"
    FOREIGN KEY ("human_decision_id") REFERENCES "human_result_decisions" ("decision_id");

ALTER TABLE "evidence_records" ADD CONSTRAINT "fk_evidence_records_asset_version_id"
    FOREIGN KEY ("asset_version_id") REFERENCES "asset_versions" ("asset_version_id");

ALTER TABLE "evidence_records" ADD CONSTRAINT "fk_evidence_records_source_file_id"
    FOREIGN KEY ("source_file_id") REFERENCES "stored_files" ("file_id");

ALTER TABLE "evidence_records" ADD CONSTRAINT "fk_evidence_records_frame_id"
    FOREIGN KEY ("frame_id") REFERENCES "image_coordinate_frames" ("frame_id");

ALTER TABLE "evidence_records" ADD CONSTRAINT "fk_evidence_records_source_ocr_run_id"
    FOREIGN KEY ("source_ocr_run_id") REFERENCES "ocr_runs" ("ocr_run_id");

ALTER TABLE "evidence_records" ADD CONSTRAINT "fk_evidence_records_reference_record_id"
    FOREIGN KEY ("reference_record_id") REFERENCES "reference_records" ("reference_record_id");

ALTER TABLE "evidence_ocr_tokens" ADD CONSTRAINT "fk_evidence_ocr_tokens_evidence_id"
    FOREIGN KEY ("evidence_id") REFERENCES "evidence_records" ("evidence_id");

ALTER TABLE "evidence_ocr_tokens" ADD CONSTRAINT "fk_evidence_ocr_tokens_token_id"
    FOREIGN KEY ("token_id") REFERENCES "ocr_tokens" ("token_id");

ALTER TABLE "criteria_sets" ADD CONSTRAINT "fk_criteria_sets_project_id"
    FOREIGN KEY ("project_id") REFERENCES "projects" ("project_id");

ALTER TABLE "criteria_drafts" ADD CONSTRAINT "fk_criteria_drafts_criteria_set_id"
    FOREIGN KEY ("criteria_set_id") REFERENCES "criteria_sets" ("criteria_set_id");

ALTER TABLE "criteria_drafts" ADD CONSTRAINT "fk_criteria_drafts_target_asset_version_id"
    FOREIGN KEY ("target_asset_version_id") REFERENCES "asset_versions" ("asset_version_id");

ALTER TABLE "criteria_drafts" ADD CONSTRAINT "fk_criteria_drafts_updated_by"
    FOREIGN KEY ("updated_by") REFERENCES "users" ("user_id");

ALTER TABLE "criteria_versions" ADD CONSTRAINT "fk_criteria_versions_criteria_set_id"
    FOREIGN KEY ("criteria_set_id") REFERENCES "criteria_sets" ("criteria_set_id");

ALTER TABLE "criteria_versions" ADD CONSTRAINT "fk_criteria_versions_target_asset_version_id"
    FOREIGN KEY ("target_asset_version_id") REFERENCES "asset_versions" ("asset_version_id");

ALTER TABLE "criteria_versions" ADD CONSTRAINT "fk_criteria_versions_source_draft_id"
    FOREIGN KEY ("source_draft_id") REFERENCES "criteria_drafts" ("draft_id");

ALTER TABLE "criteria_versions" ADD CONSTRAINT "fk_criteria_versions_source_criteria_version_id"
    FOREIGN KEY ("source_criteria_version_id") REFERENCES "criteria_versions" ("criteria_version_id");

ALTER TABLE "criteria_versions" ADD CONSTRAINT "fk_criteria_versions_import_package_id"
    FOREIGN KEY ("import_package_id") REFERENCES "import_packages" ("import_id");

ALTER TABLE "criteria_versions" ADD CONSTRAINT "fk_criteria_versions_confirmed_by"
    FOREIGN KEY ("confirmed_by") REFERENCES "users" ("user_id");

ALTER TABLE "criteria_items" ADD CONSTRAINT "fk_criteria_items_criteria_version_id"
    FOREIGN KEY ("criteria_version_id") REFERENCES "criteria_versions" ("criteria_version_id");

ALTER TABLE "criteria_items" ADD CONSTRAINT "fk_criteria_items_source_asset_version_id"
    FOREIGN KEY ("source_asset_version_id") REFERENCES "asset_versions" ("asset_version_id");

ALTER TABLE "criteria_items" ADD CONSTRAINT "fk_criteria_items_source_reference_record_id"
    FOREIGN KEY ("source_reference_record_id") REFERENCES "reference_records" ("reference_record_id");

ALTER TABLE "review_runs" ADD CONSTRAINT "fk_review_runs_project_id"
    FOREIGN KEY ("project_id") REFERENCES "projects" ("project_id");

ALTER TABLE "review_runs" ADD CONSTRAINT "fk_review_runs_asset_version_id"
    FOREIGN KEY ("asset_version_id") REFERENCES "asset_versions" ("asset_version_id");

ALTER TABLE "review_runs" ADD CONSTRAINT "fk_review_runs_criteria_version_id"
    FOREIGN KEY ("criteria_version_id") REFERENCES "criteria_versions" ("criteria_version_id");

ALTER TABLE "review_runs" ADD CONSTRAINT "fk_review_runs_submission_version_id"
    FOREIGN KEY ("submission_version_id") REFERENCES "submission_versions" ("submission_version_id");

ALTER TABLE "review_runs" ADD CONSTRAINT "fk_review_runs_retry_of_run_id"
    FOREIGN KEY ("retry_of_run_id") REFERENCES "review_runs" ("run_id");

ALTER TABLE "review_attempts" ADD CONSTRAINT "fk_review_attempts_run_id"
    FOREIGN KEY ("run_id") REFERENCES "review_runs" ("run_id");

ALTER TABLE "review_attempts" ADD CONSTRAINT "fk_review_attempts_work_unit_id"
    FOREIGN KEY ("work_unit_id") REFERENCES "review_work_units" ("work_unit_id");

ALTER TABLE "item_results" ADD CONSTRAINT "fk_item_results_run_id"
    FOREIGN KEY ("run_id") REFERENCES "review_runs" ("run_id");

ALTER TABLE "item_results" ADD CONSTRAINT "fk_item_results_attempt_id"
    FOREIGN KEY ("attempt_id") REFERENCES "review_attempts" ("attempt_id");

ALTER TABLE "item_results" ADD CONSTRAINT "fk_item_results_work_unit_id"
    FOREIGN KEY ("work_unit_id") REFERENCES "review_work_units" ("work_unit_id");

ALTER TABLE "item_results" ADD CONSTRAINT "fk_item_results_agent_execution_id"
    FOREIGN KEY ("agent_execution_id") REFERENCES "agent_executions" ("agent_execution_id");

ALTER TABLE "item_results" ADD CONSTRAINT "fk_item_results_adjudicates_conflict_id"
    FOREIGN KEY ("adjudicates_conflict_id") REFERENCES "result_conflicts" ("conflict_id");

ALTER TABLE "item_results" ADD CONSTRAINT "fk_item_results_criteria_item_id"
    FOREIGN KEY ("criteria_item_id") REFERENCES "criteria_items" ("criteria_item_id");

ALTER TABLE "item_results" ADD CONSTRAINT "fk_item_results_ocr_run_id"
    FOREIGN KEY ("ocr_run_id") REFERENCES "ocr_runs" ("ocr_run_id");

ALTER TABLE "item_results" ADD CONSTRAINT "fk_item_results_extraction_id"
    FOREIGN KEY ("extraction_id") REFERENCES "poster_extractions" ("extraction_id");

ALTER TABLE "item_results" ADD CONSTRAINT "fk_item_results_reference_check_id"
    FOREIGN KEY ("reference_check_id") REFERENCES "reference_checks" ("reference_check_id");

ALTER TABLE "run_item_selections" ADD CONSTRAINT "fk_run_item_selections_run_id"
    FOREIGN KEY ("run_id") REFERENCES "review_runs" ("run_id");

ALTER TABLE "run_item_selections" ADD CONSTRAINT "fk_run_item_selections_criteria_item_id"
    FOREIGN KEY ("criteria_item_id") REFERENCES "criteria_items" ("criteria_item_id");

ALTER TABLE "run_item_selections" ADD CONSTRAINT "fk_run_item_selections_result_id"
    FOREIGN KEY ("result_id") REFERENCES "item_results" ("result_id");

ALTER TABLE "run_item_selections" ADD CONSTRAINT "fk_run_item_selections_selection_event_id"
    FOREIGN KEY ("selection_event_id") REFERENCES "result_selection_events" ("selection_event_id");

ALTER TABLE "agent_definitions" ADD CONSTRAINT "fk_agent_definitions_project_id"
    FOREIGN KEY ("project_id") REFERENCES "projects" ("project_id");

ALTER TABLE "agent_definitions" ADD CONSTRAINT "fk_agent_definitions_prompt_file_id"
    FOREIGN KEY ("prompt_file_id") REFERENCES "stored_files" ("file_id");

ALTER TABLE "agent_definitions" ADD CONSTRAINT "fk_agent_definitions_input_schema_id"
    FOREIGN KEY ("input_schema_id") REFERENCES "schema_definitions" ("schema_id");

ALTER TABLE "agent_definitions" ADD CONSTRAINT "fk_agent_definitions_output_schema_id"
    FOREIGN KEY ("output_schema_id") REFERENCES "schema_definitions" ("schema_id");

ALTER TABLE "review_work_units" ADD CONSTRAINT "fk_review_work_units_run_id"
    FOREIGN KEY ("run_id") REFERENCES "review_runs" ("run_id");

ALTER TABLE "review_work_units" ADD CONSTRAINT "fk_review_work_units_agent_definition_id"
    FOREIGN KEY ("agent_definition_id") REFERENCES "agent_definitions" ("agent_definition_id");

ALTER TABLE "review_work_units" ADD CONSTRAINT "fk_review_work_units_input_manifest_file_id"
    FOREIGN KEY ("input_manifest_file_id") REFERENCES "stored_files" ("file_id");

ALTER TABLE "review_work_units" ADD CONSTRAINT "fk_review_work_units_active_attempt_id"
    FOREIGN KEY ("active_attempt_id") REFERENCES "review_attempts" ("attempt_id");

ALTER TABLE "review_work_units" ADD CONSTRAINT "fk_review_work_units_selected_attempt_id"
    FOREIGN KEY ("selected_attempt_id") REFERENCES "review_attempts" ("attempt_id");

ALTER TABLE "work_unit_criteria" ADD CONSTRAINT "fk_work_unit_criteria_work_unit_id"
    FOREIGN KEY ("work_unit_id") REFERENCES "review_work_units" ("work_unit_id");

ALTER TABLE "work_unit_criteria" ADD CONSTRAINT "fk_work_unit_criteria_criteria_item_id"
    FOREIGN KEY ("criteria_item_id") REFERENCES "criteria_items" ("criteria_item_id");

ALTER TABLE "work_unit_criteria" ADD CONSTRAINT "fk_work_unit_criteria_bound_conflict_id"
    FOREIGN KEY ("bound_conflict_id") REFERENCES "result_conflicts" ("conflict_id");

ALTER TABLE "work_unit_dependencies" ADD CONSTRAINT "fk_work_unit_dependencies_dependent_work_unit_id"
    FOREIGN KEY ("dependent_work_unit_id") REFERENCES "review_work_units" ("work_unit_id");

ALTER TABLE "work_unit_dependencies" ADD CONSTRAINT "fk_work_unit_dependencies_upstream_work_unit_id"
    FOREIGN KEY ("upstream_work_unit_id") REFERENCES "review_work_units" ("work_unit_id");

ALTER TABLE "work_unit_dependencies" ADD CONSTRAINT "fk_work_unit_dependencies_accepted_source_attempt_id"
    FOREIGN KEY ("accepted_source_attempt_id") REFERENCES "review_attempts" ("attempt_id");

ALTER TABLE "work_unit_dependencies" ADD CONSTRAINT "fk_work_unit_dependencies_source_ocr_run_id"
    FOREIGN KEY ("source_ocr_run_id") REFERENCES "ocr_runs" ("ocr_run_id");

ALTER TABLE "work_unit_dependencies" ADD CONSTRAINT "fk_work_unit_dependencies_source_extraction_id"
    FOREIGN KEY ("source_extraction_id") REFERENCES "poster_extractions" ("extraction_id");

ALTER TABLE "work_unit_dependencies" ADD CONSTRAINT "fk_work_unit_dependencies_source_reference_check_id"
    FOREIGN KEY ("source_reference_check_id") REFERENCES "reference_checks" ("reference_check_id");

ALTER TABLE "work_unit_dependencies" ADD CONSTRAINT "fk_work_unit_dependencies_source_file_id"
    FOREIGN KEY ("source_file_id") REFERENCES "stored_files" ("file_id");

ALTER TABLE "agent_executions" ADD CONSTRAINT "fk_agent_executions_agent_definition_id"
    FOREIGN KEY ("agent_definition_id") REFERENCES "agent_definitions" ("agent_definition_id");

ALTER TABLE "agent_executions" ADD CONSTRAINT "fk_agent_executions_attempt_id"
    FOREIGN KEY ("attempt_id") REFERENCES "review_attempts" ("attempt_id");

ALTER TABLE "agent_executions" ADD CONSTRAINT "fk_agent_executions_draft_id"
    FOREIGN KEY ("draft_id") REFERENCES "criteria_drafts" ("draft_id");

ALTER TABLE "agent_executions" ADD CONSTRAINT "fk_agent_executions_parent_execution_id"
    FOREIGN KEY ("parent_execution_id") REFERENCES "agent_executions" ("agent_execution_id");

ALTER TABLE "agent_executions" ADD CONSTRAINT "fk_agent_executions_retry_of_execution_id"
    FOREIGN KEY ("retry_of_execution_id") REFERENCES "agent_executions" ("agent_execution_id");

ALTER TABLE "agent_executions" ADD CONSTRAINT "fk_agent_executions_input_manifest_file_id"
    FOREIGN KEY ("input_manifest_file_id") REFERENCES "stored_files" ("file_id");

ALTER TABLE "agent_executions" ADD CONSTRAINT "fk_agent_executions_output_file_id"
    FOREIGN KEY ("output_file_id") REFERENCES "stored_files" ("file_id");

ALTER TABLE "agent_tool_calls" ADD CONSTRAINT "fk_agent_tool_calls_agent_execution_id"
    FOREIGN KEY ("agent_execution_id") REFERENCES "agent_executions" ("agent_execution_id");

ALTER TABLE "agent_tool_calls" ADD CONSTRAINT "fk_agent_tool_calls_retry_of_tool_call_id"
    FOREIGN KEY ("retry_of_tool_call_id") REFERENCES "agent_tool_calls" ("tool_call_id");

ALTER TABLE "agent_tool_calls" ADD CONSTRAINT "fk_agent_tool_calls_arguments_file_id"
    FOREIGN KEY ("arguments_file_id") REFERENCES "stored_files" ("file_id");

ALTER TABLE "agent_tool_calls" ADD CONSTRAINT "fk_agent_tool_calls_response_file_id"
    FOREIGN KEY ("response_file_id") REFERENCES "stored_files" ("file_id");

ALTER TABLE "result_conflicts" ADD CONSTRAINT "fk_result_conflicts_run_id"
    FOREIGN KEY ("run_id") REFERENCES "review_runs" ("run_id");

ALTER TABLE "result_conflicts" ADD CONSTRAINT "fk_result_conflicts_criteria_item_id"
    FOREIGN KEY ("criteria_item_id") REFERENCES "criteria_items" ("criteria_item_id");

ALTER TABLE "result_conflicts" ADD CONSTRAINT "fk_result_conflicts_resolution_event_id"
    FOREIGN KEY ("resolution_event_id") REFERENCES "result_selection_events" ("selection_event_id");

ALTER TABLE "conflict_candidates" ADD CONSTRAINT "fk_conflict_candidates_conflict_id"
    FOREIGN KEY ("conflict_id") REFERENCES "result_conflicts" ("conflict_id");

ALTER TABLE "conflict_candidates" ADD CONSTRAINT "fk_conflict_candidates_result_id"
    FOREIGN KEY ("result_id") REFERENCES "item_results" ("result_id");

ALTER TABLE "result_selection_events" ADD CONSTRAINT "fk_result_selection_events_run_id"
    FOREIGN KEY ("run_id") REFERENCES "review_runs" ("run_id");

ALTER TABLE "result_selection_events" ADD CONSTRAINT "fk_result_selection_events_criteria_item_id"
    FOREIGN KEY ("criteria_item_id") REFERENCES "criteria_items" ("criteria_item_id");

ALTER TABLE "result_selection_events" ADD CONSTRAINT "fk_result_selection_events_previous_event_id"
    FOREIGN KEY ("previous_event_id") REFERENCES "result_selection_events" ("selection_event_id");

ALTER TABLE "result_selection_events" ADD CONSTRAINT "fk_result_selection_events_selected_result_id"
    FOREIGN KEY ("selected_result_id") REFERENCES "item_results" ("result_id");

ALTER TABLE "result_selection_events" ADD CONSTRAINT "fk_result_selection_events_conflict_id"
    FOREIGN KEY ("conflict_id") REFERENCES "result_conflicts" ("conflict_id");

ALTER TABLE "result_selection_events" ADD CONSTRAINT "fk_result_selection_events_actor_user_id"
    FOREIGN KEY ("actor_user_id") REFERENCES "users" ("user_id");

ALTER TABLE "result_selection_events" ADD CONSTRAINT "fk_result_selection_events_actor_agent_execution_id"
    FOREIGN KEY ("actor_agent_execution_id") REFERENCES "agent_executions" ("agent_execution_id");

ALTER TABLE "result_selection_events" ADD CONSTRAINT "fk_result_selection_events_decision_file_id"
    FOREIGN KEY ("decision_file_id") REFERENCES "stored_files" ("file_id");

ALTER TABLE "reference_snapshots" ADD CONSTRAINT "fk_reference_snapshots_project_id"
    FOREIGN KEY ("project_id") REFERENCES "projects" ("project_id");

ALTER TABLE "reference_snapshots" ADD CONSTRAINT "fk_reference_snapshots_source_file_id"
    FOREIGN KEY ("source_file_id") REFERENCES "stored_files" ("file_id");

ALTER TABLE "reference_snapshots" ADD CONSTRAINT "fk_reference_snapshots_normalized_file_id"
    FOREIGN KEY ("normalized_file_id") REFERENCES "stored_files" ("file_id");

ALTER TABLE "reference_snapshots" ADD CONSTRAINT "fk_reference_snapshots_schema_id"
    FOREIGN KEY ("schema_id") REFERENCES "schema_definitions" ("schema_id");

ALTER TABLE "reference_records" ADD CONSTRAINT "fk_reference_records_snapshot_id"
    FOREIGN KEY ("snapshot_id") REFERENCES "reference_snapshots" ("snapshot_id");

ALTER TABLE "reference_checks" ADD CONSTRAINT "fk_reference_checks_extraction_id"
    FOREIGN KEY ("extraction_id") REFERENCES "poster_extractions" ("extraction_id");

ALTER TABLE "reference_checks" ADD CONSTRAINT "fk_reference_checks_predecessor_id"
    FOREIGN KEY ("predecessor_id") REFERENCES "reference_checks" ("reference_check_id");

ALTER TABLE "reference_checks" ADD CONSTRAINT "fk_reference_checks_selected_reference_record_id"
    FOREIGN KEY ("selected_reference_record_id") REFERENCES "reference_records" ("reference_record_id");

ALTER TABLE "reference_checks" ADD CONSTRAINT "fk_reference_checks_selected_by"
    FOREIGN KEY ("selected_by") REFERENCES "users" ("user_id");

ALTER TABLE "reference_checks" ADD CONSTRAINT "fk_reference_checks_source_result_file_id"
    FOREIGN KEY ("source_result_file_id") REFERENCES "stored_files" ("file_id");

ALTER TABLE "reference_candidates" ADD CONSTRAINT "fk_reference_candidates_reference_check_id"
    FOREIGN KEY ("reference_check_id") REFERENCES "reference_checks" ("reference_check_id");

ALTER TABLE "reference_candidates" ADD CONSTRAINT "fk_reference_candidates_reference_record_id"
    FOREIGN KEY ("reference_record_id") REFERENCES "reference_records" ("reference_record_id");

ALTER TABLE "reference_field_results" ADD CONSTRAINT "fk_reference_field_results_reference_check_id"
    FOREIGN KEY ("reference_check_id") REFERENCES "reference_checks" ("reference_check_id");

ALTER TABLE "submission_versions" ADD CONSTRAINT "fk_submission_versions_project_id"
    FOREIGN KEY ("project_id") REFERENCES "projects" ("project_id");

ALTER TABLE "submission_versions" ADD CONSTRAINT "fk_submission_versions_previous_submission_id"
    FOREIGN KEY ("previous_submission_id") REFERENCES "submission_versions" ("submission_version_id");

ALTER TABLE "submission_versions" ADD CONSTRAINT "fk_submission_versions_primary_asset_version_id"
    FOREIGN KEY ("primary_asset_version_id") REFERENCES "asset_versions" ("asset_version_id");

ALTER TABLE "submission_versions" ADD CONSTRAINT "fk_submission_versions_submitted_by"
    FOREIGN KEY ("submitted_by") REFERENCES "users" ("user_id");

ALTER TABLE "submission_assets" ADD CONSTRAINT "fk_submission_assets_submission_version_id"
    FOREIGN KEY ("submission_version_id") REFERENCES "submission_versions" ("submission_version_id");

ALTER TABLE "submission_assets" ADD CONSTRAINT "fk_submission_assets_asset_version_id"
    FOREIGN KEY ("asset_version_id") REFERENCES "asset_versions" ("asset_version_id");

ALTER TABLE "human_result_decisions" ADD CONSTRAINT "fk_human_result_decisions_result_id"
    FOREIGN KEY ("result_id") REFERENCES "item_results" ("result_id");

ALTER TABLE "human_result_decisions" ADD CONSTRAINT "fk_human_result_decisions_supersedes_decision_id"
    FOREIGN KEY ("supersedes_decision_id") REFERENCES "human_result_decisions" ("decision_id");

ALTER TABLE "human_result_decisions" ADD CONSTRAINT "fk_human_result_decisions_actor_user_id"
    FOREIGN KEY ("actor_user_id") REFERENCES "users" ("user_id");

ALTER TABLE "review_reports" ADD CONSTRAINT "fk_review_reports_run_id"
    FOREIGN KEY ("run_id") REFERENCES "review_runs" ("run_id");

ALTER TABLE "review_reports" ADD CONSTRAINT "fk_review_reports_report_file_id"
    FOREIGN KEY ("report_file_id") REFERENCES "stored_files" ("file_id");

ALTER TABLE "approval_events" ADD CONSTRAINT "fk_approval_events_report_id"
    FOREIGN KEY ("report_id") REFERENCES "review_reports" ("report_id");

ALTER TABLE "approval_events" ADD CONSTRAINT "fk_approval_events_actor_user_id"
    FOREIGN KEY ("actor_user_id") REFERENCES "users" ("user_id");

ALTER TABLE "remediation_tasks" ADD CONSTRAINT "fk_remediation_tasks_project_id"
    FOREIGN KEY ("project_id") REFERENCES "projects" ("project_id");

ALTER TABLE "remediation_tasks" ADD CONSTRAINT "fk_remediation_tasks_source_run_id"
    FOREIGN KEY ("source_run_id") REFERENCES "review_runs" ("run_id");

ALTER TABLE "remediation_tasks" ADD CONSTRAINT "fk_remediation_tasks_source_result_id"
    FOREIGN KEY ("source_result_id") REFERENCES "item_results" ("result_id");

ALTER TABLE "remediation_tasks" ADD CONSTRAINT "fk_remediation_tasks_criteria_item_id"
    FOREIGN KEY ("criteria_item_id") REFERENCES "criteria_items" ("criteria_item_id");

ALTER TABLE "remediation_tasks" ADD CONSTRAINT "fk_remediation_tasks_assignee_user_id"
    FOREIGN KEY ("assignee_user_id") REFERENCES "users" ("user_id");

ALTER TABLE "remediation_submissions" ADD CONSTRAINT "fk_remediation_submissions_task_id"
    FOREIGN KEY ("task_id") REFERENCES "remediation_tasks" ("task_id");

ALTER TABLE "remediation_submissions" ADD CONSTRAINT "fk_remediation_submissions_submission_version_id"
    FOREIGN KEY ("submission_version_id") REFERENCES "submission_versions" ("submission_version_id");

ALTER TABLE "remediation_submissions" ADD CONSTRAINT "fk_remediation_submissions_verification_run_id"
    FOREIGN KEY ("verification_run_id") REFERENCES "review_runs" ("run_id");

ALTER TABLE "remediation_submissions" ADD CONSTRAINT "fk_remediation_submissions_verification_result_id"
    FOREIGN KEY ("verification_result_id") REFERENCES "item_results" ("result_id");

ALTER TABLE "remediation_submissions" ADD CONSTRAINT "fk_remediation_submissions_verified_by"
    FOREIGN KEY ("verified_by") REFERENCES "users" ("user_id");

-- 한글 논리명과 설명

COMMENT ON TABLE "users" IS '사용자';
COMMENT ON COLUMN "users"."user_id" IS '사용자식별자';
COMMENT ON COLUMN "users"."email" IS '이메일';
COMMENT ON COLUMN "users"."display_name" IS '표시이름';
COMMENT ON COLUMN "users"."is_active" IS '활성여부';
COMMENT ON COLUMN "users"."created_at" IS '등록시각';

COMMENT ON TABLE "projects" IS '프로젝트';
COMMENT ON COLUMN "projects"."project_id" IS '프로젝트식별자';
COMMENT ON COLUMN "projects"."created_by" IS '생성자식별자';
COMMENT ON COLUMN "projects"."name" IS '프로젝트명';
COMMENT ON COLUMN "projects"."purpose" IS '발주목적';
COMMENT ON COLUMN "projects"."use_channels" IS '예정사용처';
COMMENT ON COLUMN "projects"."due_at" IS '납기시각';
COMMENT ON COLUMN "projects"."business_state" IS '업무상태';
COMMENT ON COLUMN "projects"."created_at" IS '등록시각';

COMMENT ON TABLE "project_members" IS '프로젝트참여자';
COMMENT ON COLUMN "project_members"."project_id" IS '프로젝트식별자';
COMMENT ON COLUMN "project_members"."user_id" IS '사용자식별자';
COMMENT ON COLUMN "project_members"."role" IS '참여역할';
COMMENT ON COLUMN "project_members"."is_active" IS '활성여부';
COMMENT ON COLUMN "project_members"."created_at" IS '참여시각';

COMMENT ON TABLE "assets" IS '자료';
COMMENT ON COLUMN "assets"."asset_id" IS '자료식별자';
COMMENT ON COLUMN "assets"."project_id" IS '프로젝트식별자';
COMMENT ON COLUMN "assets"."parent_asset_id" IS '상위자료식별자';
COMMENT ON COLUMN "assets"."asset_kind" IS '자료종류';
COMMENT ON COLUMN "assets"."name" IS '자료명';

COMMENT ON TABLE "asset_versions" IS '자료버전';
COMMENT ON COLUMN "asset_versions"."asset_version_id" IS '자료버전식별자';
COMMENT ON COLUMN "asset_versions"."asset_id" IS '자료식별자';
COMMENT ON COLUMN "asset_versions"."version_no" IS '버전번호';
COMMENT ON COLUMN "asset_versions"."original_file_id" IS '원본파일식별자';
COMMENT ON COLUMN "asset_versions"."display_file_id" IS '표시파일식별자';
COMMENT ON COLUMN "asset_versions"."original_sha256" IS '원본파일해시';
COMMENT ON COLUMN "asset_versions"."original_width" IS '원본가로크기';
COMMENT ON COLUMN "asset_versions"."original_height" IS '원본세로크기';
COMMENT ON COLUMN "asset_versions"."display_width" IS '표시가로크기';
COMMENT ON COLUMN "asset_versions"."display_height" IS '표시세로크기';
COMMENT ON COLUMN "asset_versions"."verification_state" IS '원본확인상태';
COMMENT ON COLUMN "asset_versions"."measurement_metadata" IS '파일측정내용';
COMMENT ON COLUMN "asset_versions"."created_at" IS '등록시각';

COMMENT ON TABLE "stored_files" IS '저장파일';
COMMENT ON COLUMN "stored_files"."file_id" IS '파일식별자';
COMMENT ON COLUMN "stored_files"."project_id" IS '프로젝트식별자';
COMMENT ON COLUMN "stored_files"."parent_file_id" IS '원천파일식별자';
COMMENT ON COLUMN "stored_files"."import_package_id" IS '인계묶음식별자';
COMMENT ON COLUMN "stored_files"."file_role" IS '파일역할';
COMMENT ON COLUMN "stored_files"."original_name" IS '원본파일명';
COMMENT ON COLUMN "stored_files"."detected_mime" IS '확인된파일형식';
COMMENT ON COLUMN "stored_files"."byte_size" IS '파일크기';
COMMENT ON COLUMN "stored_files"."sha256" IS '파일해시';
COMMENT ON COLUMN "stored_files"."state" IS '저장상태';
COMMENT ON COLUMN "stored_files"."source_metadata" IS '출처내용';
COMMENT ON COLUMN "stored_files"."created_at" IS '등록시각';

COMMENT ON TABLE "image_coordinate_frames" IS '이미지좌표계';
COMMENT ON COLUMN "image_coordinate_frames"."frame_id" IS '좌표계식별자';
COMMENT ON COLUMN "image_coordinate_frames"."asset_version_id" IS '자료버전식별자';
COMMENT ON COLUMN "image_coordinate_frames"."file_id" IS '기준파일식별자';
COMMENT ON COLUMN "image_coordinate_frames"."parent_frame_id" IS '상위좌표계식별자';
COMMENT ON COLUMN "image_coordinate_frames"."frame_kind" IS '좌표기준종류';
COMMENT ON COLUMN "image_coordinate_frames"."width" IS '기준가로크기';
COMMENT ON COLUMN "image_coordinate_frames"."height" IS '기준세로크기';
COMMENT ON COLUMN "image_coordinate_frames"."verification_state" IS '확인상태';
COMMENT ON COLUMN "image_coordinate_frames"."transform_jsonb" IS '좌표변환내용';

COMMENT ON TABLE "record_aliases" IS '외부식별별칭';
COMMENT ON COLUMN "record_aliases"."alias_id" IS '별칭식별자';
COMMENT ON COLUMN "record_aliases"."asset_version_id" IS '자료버전식별자';
COMMENT ON COLUMN "record_aliases"."import_package_id" IS '인계묶음식별자';
COMMENT ON COLUMN "record_aliases"."namespace" IS '식별출처';
COMMENT ON COLUMN "record_aliases"."external_record_id" IS '외부식별값';

COMMENT ON TABLE "schema_definitions" IS '데이터규격';
COMMENT ON COLUMN "schema_definitions"."schema_id" IS '규격식별자';
COMMENT ON COLUMN "schema_definitions"."schema_name" IS '규격명';
COMMENT ON COLUMN "schema_definitions"."schema_version" IS '규격버전';
COMMENT ON COLUMN "schema_definitions"."specification_file_id" IS '설명파일식별자';
COMMENT ON COLUMN "schema_definitions"."validation_schema_file_id" IS '검증규격파일식별자';
COMMENT ON COLUMN "schema_definitions"."availability_state" IS '확보상태';
COMMENT ON COLUMN "schema_definitions"."field_catalog_jsonb" IS '항목목록';

COMMENT ON TABLE "import_packages" IS '인계묶음';
COMMENT ON COLUMN "import_packages"."import_id" IS '인계묶음식별자';
COMMENT ON COLUMN "import_packages"."project_id" IS '프로젝트식별자';
COMMENT ON COLUMN "import_packages"."manifest_file_id" IS '목록파일식별자';
COMMENT ON COLUMN "import_packages"."replaces_import_id" IS '이전인계식별자';
COMMENT ON COLUMN "import_packages"."source_namespace" IS '인계출처';
COMMENT ON COLUMN "import_packages"."external_record_id" IS '외부기록식별값';
COMMENT ON COLUMN "import_packages"."package_fingerprint" IS '묶음내용해시';
COMMENT ON COLUMN "import_packages"."hash_check_state" IS '해시확인상태';
COMMENT ON COLUMN "import_packages"."import_state" IS '수입상태';
COMMENT ON COLUMN "import_packages"."reported_validation_jsonb" IS '보고된검증내용';
COMMENT ON COLUMN "import_packages"."imported_at" IS '접수시각';

COMMENT ON TABLE "audit_events" IS '감사이력';
COMMENT ON COLUMN "audit_events"."event_id" IS '감사이력식별자';
COMMENT ON COLUMN "audit_events"."project_id" IS '프로젝트식별자';
COMMENT ON COLUMN "audit_events"."actor_user_id" IS '행위자식별자';
COMMENT ON COLUMN "audit_events"."action" IS '행위종류';
COMMENT ON COLUMN "audit_events"."target_type" IS '대상종류';
COMMENT ON COLUMN "audit_events"."target_id" IS '대상식별값';
COMMENT ON COLUMN "audit_events"."event_metadata" IS '행위내용';
COMMENT ON COLUMN "audit_events"."occurred_at" IS '발생시각';

COMMENT ON TABLE "ocr_runs" IS '문자인식실행';
COMMENT ON COLUMN "ocr_runs"."ocr_run_id" IS '문자인식실행식별자';
COMMENT ON COLUMN "ocr_runs"."asset_version_id" IS '자료버전식별자';
COMMENT ON COLUMN "ocr_runs"."attempt_id" IS '처리시도식별자';
COMMENT ON COLUMN "ocr_runs"."import_package_id" IS '인계묶음식별자';
COMMENT ON COLUMN "ocr_runs"."schema_id" IS '규격식별자';
COMMENT ON COLUMN "ocr_runs"."source_file_id" IS '결과파일식별자';
COMMENT ON COLUMN "ocr_runs"."frame_id" IS '좌표계식별자';
COMMENT ON COLUMN "ocr_runs"."execution_status" IS '실행상태';
COMMENT ON COLUMN "ocr_runs"."engine_name" IS '인식엔진명';
COMMENT ON COLUMN "ocr_runs"."engine_version" IS '엔진버전';
COMMENT ON COLUMN "ocr_runs"."model_name" IS '인식모델명';
COMMENT ON COLUMN "ocr_runs"."device" IS '실행장치';
COMMENT ON COLUMN "ocr_runs"."reported_coordinate_space" IS '보고된좌표기준';
COMMENT ON COLUMN "ocr_runs"."engine_settings" IS '실행설정내용';
COMMENT ON COLUMN "ocr_runs"."started_at" IS '시작시각';
COMMENT ON COLUMN "ocr_runs"."completed_at" IS '완료시각';

COMMENT ON TABLE "ocr_tokens" IS '인식문구';
COMMENT ON COLUMN "ocr_tokens"."token_id" IS '인식문구식별자';
COMMENT ON COLUMN "ocr_tokens"."ocr_run_id" IS '문자인식실행식별자';
COMMENT ON COLUMN "ocr_tokens"."external_token_id" IS '외부문구식별값';
COMMENT ON COLUMN "ocr_tokens"."token_order" IS '문구순서';
COMMENT ON COLUMN "ocr_tokens"."text" IS '인식원문';
COMMENT ON COLUMN "ocr_tokens"."confidence" IS '인식신뢰도';
COMMENT ON COLUMN "ocr_tokens"."bbox_xyxy" IS '사각영역좌표';
COMMENT ON COLUMN "ocr_tokens"."polygon" IS '다각형영역좌표';

COMMENT ON TABLE "poster_extractions" IS '포스터추출';
COMMENT ON COLUMN "poster_extractions"."extraction_id" IS '추출식별자';
COMMENT ON COLUMN "poster_extractions"."asset_version_id" IS '자료버전식별자';
COMMENT ON COLUMN "poster_extractions"."schema_id" IS '규격식별자';
COMMENT ON COLUMN "poster_extractions"."ocr_run_id" IS '문자인식실행식별자';
COMMENT ON COLUMN "poster_extractions"."predecessor_id" IS '이전추출식별자';
COMMENT ON COLUMN "poster_extractions"."source_file_id" IS '원문파일식별자';
COMMENT ON COLUMN "poster_extractions"."version_no" IS '추출버전번호';
COMMENT ON COLUMN "poster_extractions"."method" IS '추출방식';
COMMENT ON COLUMN "poster_extractions"."extractor_version" IS '추출기버전';
COMMENT ON COLUMN "poster_extractions"."payload_jsonb" IS '전체추출내용';
COMMENT ON COLUMN "poster_extractions"."title_official" IS '행사대표제목';
COMMENT ON COLUMN "poster_extractions"."year_value" IS '개최연도';
COMMENT ON COLUMN "poster_extractions"."start_year" IS '시작연도';
COMMENT ON COLUMN "poster_extractions"."start_month" IS '시작월';
COMMENT ON COLUMN "poster_extractions"."start_day" IS '시작일자';
COMMENT ON COLUMN "poster_extractions"."end_year" IS '종료연도';
COMMENT ON COLUMN "poster_extractions"."end_month" IS '종료월';
COMMENT ON COLUMN "poster_extractions"."end_day" IS '종료일자';
COMMENT ON COLUMN "poster_extractions"."start_date" IS '완전한시작날짜';
COMMENT ON COLUMN "poster_extractions"."end_date" IS '완전한종료날짜';
COMMENT ON COLUMN "poster_extractions"."compatibility_state" IS '호환상태';
COMMENT ON COLUMN "poster_extractions"."validation_state" IS '구조검증상태';
COMMENT ON COLUMN "poster_extractions"."checks_jsonb" IS '확인필요내용';

COMMENT ON TABLE "poster_field_observations" IS '포스터항목관찰';
COMMENT ON COLUMN "poster_field_observations"."observation_id" IS '관찰식별자';
COMMENT ON COLUMN "poster_field_observations"."extraction_id" IS '추출식별자';
COMMENT ON COLUMN "poster_field_observations"."field_code" IS '항목코드';
COMMENT ON COLUMN "poster_field_observations"."field_path" IS '항목경로';
COMMENT ON COLUMN "poster_field_observations"."presence_status" IS '표기판독상태';
COMMENT ON COLUMN "poster_field_observations"."resolution_status" IS '해결상태';
COMMENT ON COLUMN "poster_field_observations"."raw_text" IS '인쇄원문';
COMMENT ON COLUMN "poster_field_observations"."normalized_value" IS '정규화내용';
COMMENT ON COLUMN "poster_field_observations"."source_reason" IS '미확인사유';

COMMENT ON TABLE "evidence_records" IS '근거';
COMMENT ON COLUMN "evidence_records"."evidence_id" IS '근거식별자';
COMMENT ON COLUMN "evidence_records"."observation_id" IS '관찰식별자';
COMMENT ON COLUMN "evidence_records"."item_result_id" IS '검수항목결과식별자';
COMMENT ON COLUMN "evidence_records"."reference_field_result_id" IS '외부항목비교결과식별자';
COMMENT ON COLUMN "evidence_records"."human_decision_id" IS '사람정정식별자';
COMMENT ON COLUMN "evidence_records"."asset_version_id" IS '자료버전식별자';
COMMENT ON COLUMN "evidence_records"."source_file_id" IS '출처파일식별자';
COMMENT ON COLUMN "evidence_records"."frame_id" IS '좌표계식별자';
COMMENT ON COLUMN "evidence_records"."source_ocr_run_id" IS '문자인식실행식별자';
COMMENT ON COLUMN "evidence_records"."reference_record_id" IS '외부행사기준식별자';
COMMENT ON COLUMN "evidence_records"."source_kind" IS '근거종류';
COMMENT ON COLUMN "evidence_records"."field_pointer" IS '상세항목경로';
COMMENT ON COLUMN "evidence_records"."page_no" IS '문서쪽번호';
COMMENT ON COLUMN "evidence_records"."text" IS '근거원문';
COMMENT ON COLUMN "evidence_records"."bbox_xyxy" IS '영역좌표';
COMMENT ON COLUMN "evidence_records"."method_code" IS '확인방법';
COMMENT ON COLUMN "evidence_records"."limitations_jsonb" IS '검사한계내용';

COMMENT ON TABLE "evidence_ocr_tokens" IS '근거문구연결';
COMMENT ON COLUMN "evidence_ocr_tokens"."evidence_id" IS '근거식별자';
COMMENT ON COLUMN "evidence_ocr_tokens"."token_id" IS '인식문구식별자';
COMMENT ON COLUMN "evidence_ocr_tokens"."token_order" IS '문구연결순서';

COMMENT ON TABLE "criteria_sets" IS '검수기준묶음';
COMMENT ON COLUMN "criteria_sets"."criteria_set_id" IS '기준묶음식별자';
COMMENT ON COLUMN "criteria_sets"."project_id" IS '프로젝트식별자';
COMMENT ON COLUMN "criteria_sets"."name" IS '기준묶음명';

COMMENT ON TABLE "criteria_drafts" IS '검수기준초안';
COMMENT ON COLUMN "criteria_drafts"."draft_id" IS '기준초안식별자';
COMMENT ON COLUMN "criteria_drafts"."criteria_set_id" IS '기준묶음식별자';
COMMENT ON COLUMN "criteria_drafts"."target_asset_version_id" IS '대상자료버전식별자';
COMMENT ON COLUMN "criteria_drafts"."revision" IS '초안수정번호';
COMMENT ON COLUMN "criteria_drafts"."raw_requirements" IS '요구사항원문';
COMMENT ON COLUMN "criteria_drafts"."items_jsonb" IS '편집항목내용';
COMMENT ON COLUMN "criteria_drafts"."state" IS '초안상태';
COMMENT ON COLUMN "criteria_drafts"."updated_by" IS '수정자식별자';
COMMENT ON COLUMN "criteria_drafts"."updated_at" IS '수정시각';

COMMENT ON TABLE "criteria_versions" IS '확정검수기준';
COMMENT ON COLUMN "criteria_versions"."criteria_version_id" IS '확정기준식별자';
COMMENT ON COLUMN "criteria_versions"."criteria_set_id" IS '기준묶음식별자';
COMMENT ON COLUMN "criteria_versions"."target_asset_version_id" IS '대상자료버전식별자';
COMMENT ON COLUMN "criteria_versions"."source_draft_id" IS '원천초안식별자';
COMMENT ON COLUMN "criteria_versions"."source_criteria_version_id" IS '복사원본기준식별자';
COMMENT ON COLUMN "criteria_versions"."import_package_id" IS '인계묶음식별자';
COMMENT ON COLUMN "criteria_versions"."version_no" IS '기준버전번호';
COMMENT ON COLUMN "criteria_versions"."source_draft_revision" IS '원천초안수정번호';
COMMENT ON COLUMN "criteria_versions"."source_requirements_text" IS '확정당시요구원문';
COMMENT ON COLUMN "criteria_versions"."confirmed_by" IS '확정자식별자';
COMMENT ON COLUMN "criteria_versions"."confirmed_at" IS '확정시각';
COMMENT ON COLUMN "criteria_versions"."origin_kind" IS '생성경로';
COMMENT ON COLUMN "criteria_versions"."source_confirmation_state" IS '원천확정상태';

COMMENT ON TABLE "criteria_items" IS '검수기준항목';
COMMENT ON COLUMN "criteria_items"."criteria_item_id" IS '기준항목식별자';
COMMENT ON COLUMN "criteria_items"."criteria_version_id" IS '확정기준식별자';
COMMENT ON COLUMN "criteria_items"."position" IS '항목순서';
COMMENT ON COLUMN "criteria_items"."category" IS '항목분류';
COMMENT ON COLUMN "criteria_items"."rule_code" IS '검사규칙코드';
COMMENT ON COLUMN "criteria_items"."text" IS '기준문장';
COMMENT ON COLUMN "criteria_items"."expected_value" IS '기대값내용';
COMMENT ON COLUMN "criteria_items"."is_required" IS '필수여부';
COMMENT ON COLUMN "criteria_items"."source_asset_version_id" IS '원천문서버전식별자';
COMMENT ON COLUMN "criteria_items"."source_reference_record_id" IS '원천외부기준식별자';
COMMENT ON COLUMN "criteria_items"."source_locator" IS '원문위치내용';
COMMENT ON COLUMN "criteria_items"."applicability_jsonb" IS '적용조건내용';
COMMENT ON COLUMN "criteria_items"."check_scope_jsonb" IS '검사범위내용';

COMMENT ON TABLE "review_runs" IS '검수실행';
COMMENT ON COLUMN "review_runs"."run_id" IS '검수실행식별자';
COMMENT ON COLUMN "review_runs"."project_id" IS '프로젝트식별자';
COMMENT ON COLUMN "review_runs"."asset_version_id" IS '대상자료버전식별자';
COMMENT ON COLUMN "review_runs"."criteria_version_id" IS '확정기준식별자';
COMMENT ON COLUMN "review_runs"."submission_version_id" IS '납품묶음식별자';
COMMENT ON COLUMN "review_runs"."retry_of_run_id" IS '원재검수실행식별자';
COMMENT ON COLUMN "review_runs"."execution_status" IS '실행상태';
COMMENT ON COLUMN "review_runs"."overall_verdict" IS '종합판정';
COMMENT ON COLUMN "review_runs"."orchestration_mode" IS '실행구성방식';
COMMENT ON COLUMN "review_runs"."workflow_version" IS '실행흐름버전';
COMMENT ON COLUMN "review_runs"."aggregation_policy_version" IS '통합정책버전';
COMMENT ON COLUMN "review_runs"."conflict_policy_version" IS '충돌정책버전';
COMMENT ON COLUMN "review_runs"."idempotency_key" IS '접수중복방지키';
COMMENT ON COLUMN "review_runs"."requested_config" IS '실행요청내용';
COMMENT ON COLUMN "review_runs"."started_at" IS '시작시각';
COMMENT ON COLUMN "review_runs"."finished_at" IS '완료시각';
COMMENT ON COLUMN "review_runs"."sealed_at" IS '최종확정시각';

COMMENT ON TABLE "review_attempts" IS '작업처리시도';
COMMENT ON COLUMN "review_attempts"."attempt_id" IS '처리시도식별자';
COMMENT ON COLUMN "review_attempts"."run_id" IS '검수실행식별자';
COMMENT ON COLUMN "review_attempts"."work_unit_id" IS '검수작업식별자';
COMMENT ON COLUMN "review_attempts"."attempt_no" IS '시도번호';
COMMENT ON COLUMN "review_attempts"."stage" IS '처리단계';
COMMENT ON COLUMN "review_attempts"."execution_status" IS '실행상태';
COMMENT ON COLUMN "review_attempts"."actual_runtime" IS '실제실행내용';
COMMENT ON COLUMN "review_attempts"."error_jsonb" IS '실패내용';
COMMENT ON COLUMN "review_attempts"."started_at" IS '시작시각';
COMMENT ON COLUMN "review_attempts"."finished_at" IS '종료시각';

COMMENT ON TABLE "item_results" IS '검수항목결과';
COMMENT ON COLUMN "item_results"."result_id" IS '검수항목결과식별자';
COMMENT ON COLUMN "item_results"."run_id" IS '검수실행식별자';
COMMENT ON COLUMN "item_results"."attempt_id" IS '처리시도식별자';
COMMENT ON COLUMN "item_results"."work_unit_id" IS '검수작업식별자';
COMMENT ON COLUMN "item_results"."agent_execution_id" IS '에이전트실행식별자';
COMMENT ON COLUMN "item_results"."adjudicates_conflict_id" IS '해결대상충돌식별자';
COMMENT ON COLUMN "item_results"."criteria_item_id" IS '기준항목식별자';
COMMENT ON COLUMN "item_results"."ocr_run_id" IS '문자인식실행식별자';
COMMENT ON COLUMN "item_results"."extraction_id" IS '추출식별자';
COMMENT ON COLUMN "item_results"."reference_check_id" IS '외부기준비교식별자';
COMMENT ON COLUMN "item_results"."execution_status" IS '실행상태';
COMMENT ON COLUMN "item_results"."verdict" IS '판정';
COMMENT ON COLUMN "item_results"."observed_value" IS '관찰값내용';
COMMENT ON COLUMN "item_results"."reason" IS '판정사유';
COMMENT ON COLUMN "item_results"."method_code" IS '검사방법';
COMMENT ON COLUMN "item_results"."limitations_jsonb" IS '검사한계내용';
COMMENT ON COLUMN "item_results"."next_action_jsonb" IS '후속조치내용';

COMMENT ON TABLE "run_item_selections" IS '현재항목채택';
COMMENT ON COLUMN "run_item_selections"."run_id" IS '검수실행식별자';
COMMENT ON COLUMN "run_item_selections"."criteria_item_id" IS '기준항목식별자';
COMMENT ON COLUMN "run_item_selections"."result_id" IS '채택결과식별자';
COMMENT ON COLUMN "run_item_selections"."selection_event_id" IS '채택이력식별자';
COMMENT ON COLUMN "run_item_selections"."selection_revision" IS '채택수정번호';
COMMENT ON COLUMN "run_item_selections"."selected_at" IS '채택시각';

COMMENT ON TABLE "agent_definitions" IS '에이전트정의';
COMMENT ON COLUMN "agent_definitions"."agent_definition_id" IS '에이전트정의식별자';
COMMENT ON COLUMN "agent_definitions"."project_id" IS '프로젝트식별자';
COMMENT ON COLUMN "agent_definitions"."agent_key" IS '에이전트명';
COMMENT ON COLUMN "agent_definitions"."role" IS '업무역할';
COMMENT ON COLUMN "agent_definitions"."definition_version" IS '정의버전번호';
COMMENT ON COLUMN "agent_definitions"."prompt_file_id" IS '지시문파일식별자';
COMMENT ON COLUMN "agent_definitions"."input_schema_id" IS '입력규격식별자';
COMMENT ON COLUMN "agent_definitions"."output_schema_id" IS '출력규격식별자';
COMMENT ON COLUMN "agent_definitions"."contract_version" IS '입출력규약버전';
COMMENT ON COLUMN "agent_definitions"."model_name" IS '요청모델명';
COMMENT ON COLUMN "agent_definitions"."model_config" IS '모델설정내용';
COMMENT ON COLUMN "agent_definitions"."tool_policy" IS '허용도구내용';
COMMENT ON COLUMN "agent_definitions"."context_policy" IS '자료접근범위';
COMMENT ON COLUMN "agent_definitions"."state" IS '사용상태';

COMMENT ON TABLE "review_work_units" IS '검수작업';
COMMENT ON COLUMN "review_work_units"."work_unit_id" IS '검수작업식별자';
COMMENT ON COLUMN "review_work_units"."run_id" IS '검수실행식별자';
COMMENT ON COLUMN "review_work_units"."unit_key" IS '작업구분키';
COMMENT ON COLUMN "review_work_units"."stage" IS '처리단계';
COMMENT ON COLUMN "review_work_units"."executor_kind" IS '처리주체종류';
COMMENT ON COLUMN "review_work_units"."agent_definition_id" IS '에이전트정의식별자';
COMMENT ON COLUMN "review_work_units"."output_kind" IS '산출물종류';
COMMENT ON COLUMN "review_work_units"."is_required" IS '필수작업여부';
COMMENT ON COLUMN "review_work_units"."scope_jsonb" IS '검사범위내용';
COMMENT ON COLUMN "review_work_units"."retry_policy" IS '재시도정책내용';
COMMENT ON COLUMN "review_work_units"."completion_policy" IS '완료정책내용';
COMMENT ON COLUMN "review_work_units"."state" IS '작업상태';
COMMENT ON COLUMN "review_work_units"."blocked_reason" IS '대기사유';
COMMENT ON COLUMN "review_work_units"."input_manifest_file_id" IS '입력목록파일식별자';
COMMENT ON COLUMN "review_work_units"."active_attempt_id" IS '현재처리시도식별자';
COMMENT ON COLUMN "review_work_units"."selected_attempt_id" IS '채택처리시도식별자';

COMMENT ON TABLE "work_unit_criteria" IS '작업기준배정';
COMMENT ON COLUMN "work_unit_criteria"."work_unit_id" IS '검수작업식별자';
COMMENT ON COLUMN "work_unit_criteria"."criteria_item_id" IS '기준항목식별자';
COMMENT ON COLUMN "work_unit_criteria"."binding_role" IS '배정역할';
COMMENT ON COLUMN "work_unit_criteria"."is_output_required" IS '출력필수여부';
COMMENT ON COLUMN "work_unit_criteria"."bound_conflict_id" IS '검토대상충돌식별자';
COMMENT ON COLUMN "work_unit_criteria"."bound_candidate_set_version" IS '검토후보집합버전';
COMMENT ON COLUMN "work_unit_criteria"."bound_candidate_set_hash" IS '검토후보집합해시';

COMMENT ON TABLE "work_unit_dependencies" IS '작업의존관계';
COMMENT ON COLUMN "work_unit_dependencies"."dependent_work_unit_id" IS '후행작업식별자';
COMMENT ON COLUMN "work_unit_dependencies"."upstream_work_unit_id" IS '선행작업식별자';
COMMENT ON COLUMN "work_unit_dependencies"."input_role" IS '입력역할';
COMMENT ON COLUMN "work_unit_dependencies"."gate_policy" IS '선행완료조건';
COMMENT ON COLUMN "work_unit_dependencies"."accepted_source_attempt_id" IS '채택선행시도식별자';
COMMENT ON COLUMN "work_unit_dependencies"."source_ocr_run_id" IS '입력문자인식식별자';
COMMENT ON COLUMN "work_unit_dependencies"."source_extraction_id" IS '입력추출식별자';
COMMENT ON COLUMN "work_unit_dependencies"."source_reference_check_id" IS '입력외부비교식별자';
COMMENT ON COLUMN "work_unit_dependencies"."source_file_id" IS '입력파일식별자';

COMMENT ON TABLE "agent_executions" IS '에이전트실행';
COMMENT ON COLUMN "agent_executions"."agent_execution_id" IS '에이전트실행식별자';
COMMENT ON COLUMN "agent_executions"."agent_definition_id" IS '에이전트정의식별자';
COMMENT ON COLUMN "agent_executions"."attempt_id" IS '처리시도식별자';
COMMENT ON COLUMN "agent_executions"."draft_id" IS '기준초안식별자';
COMMENT ON COLUMN "agent_executions"."draft_revision" IS '초안수정번호';
COMMENT ON COLUMN "agent_executions"."draft_analysis_key" IS '초안분석요청키';
COMMENT ON COLUMN "agent_executions"."execution_scope" IS '실행범위';
COMMENT ON COLUMN "agent_executions"."invocation_no" IS '호출순번';
COMMENT ON COLUMN "agent_executions"."parent_execution_id" IS '상위실행식별자';
COMMENT ON COLUMN "agent_executions"."retry_of_execution_id" IS '재호출원본실행식별자';
COMMENT ON COLUMN "agent_executions"."input_manifest_file_id" IS '입력목록파일식별자';
COMMENT ON COLUMN "agent_executions"."output_file_id" IS '출력파일식별자';
COMMENT ON COLUMN "agent_executions"."execution_status" IS '실행상태';
COMMENT ON COLUMN "agent_executions"."output_validation_state" IS '출력검증상태';
COMMENT ON COLUMN "agent_executions"."write_admission_status" IS '업무반영상태';
COMMENT ON COLUMN "agent_executions"."actual_model_name" IS '실제모델명';
COMMENT ON COLUMN "agent_executions"."actual_model_revision" IS '실제모델버전';
COMMENT ON COLUMN "agent_executions"."output_summary_jsonb" IS '출력요약내용';
COMMENT ON COLUMN "agent_executions"."error_jsonb" IS '실패내용';
COMMENT ON COLUMN "agent_executions"."started_at" IS '시작시각';
COMMENT ON COLUMN "agent_executions"."finished_at" IS '종료시각';

COMMENT ON TABLE "agent_tool_calls" IS '도구호출';
COMMENT ON COLUMN "agent_tool_calls"."tool_call_id" IS '도구호출식별자';
COMMENT ON COLUMN "agent_tool_calls"."agent_execution_id" IS '에이전트실행식별자';
COMMENT ON COLUMN "agent_tool_calls"."logical_call_key" IS '논리호출구분키';
COMMENT ON COLUMN "agent_tool_calls"."delivery_no" IS '전송시도번호';
COMMENT ON COLUMN "agent_tool_calls"."retry_of_tool_call_id" IS '원호출식별자';
COMMENT ON COLUMN "agent_tool_calls"."tool_name" IS '도구명';
COMMENT ON COLUMN "agent_tool_calls"."tool_version" IS '도구버전';
COMMENT ON COLUMN "agent_tool_calls"."operation_kind" IS '동작종류';
COMMENT ON COLUMN "agent_tool_calls"."arguments_file_id" IS '요청파일식별자';
COMMENT ON COLUMN "agent_tool_calls"."response_file_id" IS '응답파일식별자';
COMMENT ON COLUMN "agent_tool_calls"."state" IS '호출상태';
COMMENT ON COLUMN "agent_tool_calls"."provider_request_id" IS '외부요청식별값';
COMMENT ON COLUMN "agent_tool_calls"."error_jsonb" IS '실패내용';
COMMENT ON COLUMN "agent_tool_calls"."started_at" IS '시작시각';
COMMENT ON COLUMN "agent_tool_calls"."finished_at" IS '종료시각';

COMMENT ON TABLE "result_conflicts" IS '결과충돌';
COMMENT ON COLUMN "result_conflicts"."conflict_id" IS '충돌식별자';
COMMENT ON COLUMN "result_conflicts"."run_id" IS '검수실행식별자';
COMMENT ON COLUMN "result_conflicts"."criteria_item_id" IS '기준항목식별자';
COMMENT ON COLUMN "result_conflicts"."conflict_type" IS '충돌종류';
COMMENT ON COLUMN "result_conflicts"."state" IS '처리상태';
COMMENT ON COLUMN "result_conflicts"."candidate_set_version" IS '후보집합버전';
COMMENT ON COLUMN "result_conflicts"."candidate_set_hash" IS '후보집합해시';
COMMENT ON COLUMN "result_conflicts"."conflict_policy_version" IS '충돌정책버전';
COMMENT ON COLUMN "result_conflicts"."summary_jsonb" IS '충돌설명내용';
COMMENT ON COLUMN "result_conflicts"."resolution_event_id" IS '해결채택이력식별자';
COMMENT ON COLUMN "result_conflicts"."opened_at" IS '발견시각';
COMMENT ON COLUMN "result_conflicts"."resolved_at" IS '해결시각';

COMMENT ON TABLE "conflict_candidates" IS '충돌후보';
COMMENT ON COLUMN "conflict_candidates"."conflict_id" IS '충돌식별자';
COMMENT ON COLUMN "conflict_candidates"."result_id" IS '검수항목결과식별자';
COMMENT ON COLUMN "conflict_candidates"."added_in_version" IS '추가당시집합버전';
COMMENT ON COLUMN "conflict_candidates"."candidate_role" IS '후보역할';

COMMENT ON TABLE "result_selection_events" IS '결과채택이력';
COMMENT ON COLUMN "result_selection_events"."selection_event_id" IS '채택이력식별자';
COMMENT ON COLUMN "result_selection_events"."run_id" IS '검수실행식별자';
COMMENT ON COLUMN "result_selection_events"."criteria_item_id" IS '기준항목식별자';
COMMENT ON COLUMN "result_selection_events"."selection_revision" IS '채택수정번호';
COMMENT ON COLUMN "result_selection_events"."previous_event_id" IS '이전채택이력식별자';
COMMENT ON COLUMN "result_selection_events"."decision_key" IS '결정중복방지키';
COMMENT ON COLUMN "result_selection_events"."selected_result_id" IS '채택결과식별자';
COMMENT ON COLUMN "result_selection_events"."conflict_id" IS '충돌식별자';
COMMENT ON COLUMN "result_selection_events"."candidate_set_version" IS '판단후보집합버전';
COMMENT ON COLUMN "result_selection_events"."candidate_set_hash" IS '판단후보집합해시';
COMMENT ON COLUMN "result_selection_events"."selection_policy_version" IS '채택정책버전';
COMMENT ON COLUMN "result_selection_events"."decision_kind" IS '결정종류';
COMMENT ON COLUMN "result_selection_events"."reason" IS '결정사유';
COMMENT ON COLUMN "result_selection_events"."actor_kind" IS '결정주체종류';
COMMENT ON COLUMN "result_selection_events"."actor_user_id" IS '결정자식별자';
COMMENT ON COLUMN "result_selection_events"."actor_agent_execution_id" IS '결정에이전트실행식별자';
COMMENT ON COLUMN "result_selection_events"."decision_file_id" IS '결정자료파일식별자';
COMMENT ON COLUMN "result_selection_events"."decided_at" IS '결정시각';

COMMENT ON TABLE "reference_snapshots" IS '외부기준수집본';
COMMENT ON COLUMN "reference_snapshots"."snapshot_id" IS '수집본식별자';
COMMENT ON COLUMN "reference_snapshots"."project_id" IS '프로젝트식별자';
COMMENT ON COLUMN "reference_snapshots"."source_system" IS '수집출처명';
COMMENT ON COLUMN "reference_snapshots"."dataset_version" IS '수집자료버전';
COMMENT ON COLUMN "reference_snapshots"."source_url" IS '출처주소';
COMMENT ON COLUMN "reference_snapshots"."retrieved_at" IS '수집시각';
COMMENT ON COLUMN "reference_snapshots"."source_file_id" IS '원문파일식별자';
COMMENT ON COLUMN "reference_snapshots"."normalized_file_id" IS '정규화파일식별자';
COMMENT ON COLUMN "reference_snapshots"."schema_id" IS '규격식별자';
COMMENT ON COLUMN "reference_snapshots"."normalizer_version" IS '정규화방법버전';
COMMENT ON COLUMN "reference_snapshots"."content_hash" IS '수집내용해시';

COMMENT ON TABLE "reference_records" IS '외부행사기준';
COMMENT ON COLUMN "reference_records"."reference_record_id" IS '외부행사기준식별자';
COMMENT ON COLUMN "reference_records"."snapshot_id" IS '수집본식별자';
COMMENT ON COLUMN "reference_records"."external_record_key" IS '원천기록식별값';
COMMENT ON COLUMN "reference_records"."provider_name" IS '등록기관명';
COMMENT ON COLUMN "reference_records"."reference_date" IS '자료기준일';
COMMENT ON COLUMN "reference_records"."title" IS '행사명';
COMMENT ON COLUMN "reference_records"."start_date" IS '시작날짜';
COMMENT ON COLUMN "reference_records"."end_date" IS '종료날짜';
COMMENT ON COLUMN "reference_records"."venue" IS '행사장소';
COMMENT ON COLUMN "reference_records"."payload_jsonb" IS '원천전체내용';

COMMENT ON TABLE "reference_checks" IS '외부기준비교';
COMMENT ON COLUMN "reference_checks"."reference_check_id" IS '외부기준비교식별자';
COMMENT ON COLUMN "reference_checks"."extraction_id" IS '추출식별자';
COMMENT ON COLUMN "reference_checks"."version_no" IS '비교버전번호';
COMMENT ON COLUMN "reference_checks"."predecessor_id" IS '이전비교식별자';
COMMENT ON COLUMN "reference_checks"."selected_reference_record_id" IS '채택외부기준식별자';
COMMENT ON COLUMN "reference_checks"."match_status" IS '기준연결상태';
COMMENT ON COLUMN "reference_checks"."comparison_execution_status" IS '비교실행상태';
COMMENT ON COLUMN "reference_checks"."comparison_policy_version" IS '비교정책버전';
COMMENT ON COLUMN "reference_checks"."selected_by" IS '선택자식별자';
COMMENT ON COLUMN "reference_checks"."selection_reason" IS '선택사유';
COMMENT ON COLUMN "reference_checks"."source_result_file_id" IS '비교원문파일식별자';
COMMENT ON COLUMN "reference_checks"."reported_summary_jsonb" IS '보고된비교요약';
COMMENT ON COLUMN "reference_checks"."verification_state" IS '자료확인상태';

COMMENT ON TABLE "reference_candidates" IS '외부기준후보';
COMMENT ON COLUMN "reference_candidates"."reference_check_id" IS '외부기준비교식별자';
COMMENT ON COLUMN "reference_candidates"."reference_record_id" IS '외부행사기준식별자';
COMMENT ON COLUMN "reference_candidates"."rank" IS '후보순위';
COMMENT ON COLUMN "reference_candidates"."match_score" IS '후보점수';
COMMENT ON COLUMN "reference_candidates"."match_evidence_jsonb" IS '연결근거내용';

COMMENT ON TABLE "reference_field_results" IS '외부항목비교결과';
COMMENT ON COLUMN "reference_field_results"."comparison_result_id" IS '외부항목비교결과식별자';
COMMENT ON COLUMN "reference_field_results"."reference_check_id" IS '외부기준비교식별자';
COMMENT ON COLUMN "reference_field_results"."field_path" IS '항목경로';
COMMENT ON COLUMN "reference_field_results"."comparison_status" IS '비교상태';
COMMENT ON COLUMN "reference_field_results"."poster_value" IS '포스터관찰값';
COMMENT ON COLUMN "reference_field_results"."reference_value" IS '외부기준값';
COMMENT ON COLUMN "reference_field_results"."reason" IS '비교사유';

COMMENT ON TABLE "submission_versions" IS '납품묶음버전';
COMMENT ON COLUMN "submission_versions"."submission_version_id" IS '납품묶음식별자';
COMMENT ON COLUMN "submission_versions"."project_id" IS '프로젝트식별자';
COMMENT ON COLUMN "submission_versions"."version_no" IS '납품버전번호';
COMMENT ON COLUMN "submission_versions"."previous_submission_id" IS '이전납품식별자';
COMMENT ON COLUMN "submission_versions"."primary_asset_version_id" IS '대표자료버전식별자';
COMMENT ON COLUMN "submission_versions"."declarations_jsonb" IS '제작신고내용';
COMMENT ON COLUMN "submission_versions"."submitted_by" IS '제출자식별자';
COMMENT ON COLUMN "submission_versions"."submitted_at" IS '제출시각';
COMMENT ON COLUMN "submission_versions"."sealed_at" IS '묶음확정시각';

COMMENT ON TABLE "submission_assets" IS '납품자료연결';
COMMENT ON COLUMN "submission_assets"."submission_version_id" IS '납품묶음식별자';
COMMENT ON COLUMN "submission_assets"."asset_version_id" IS '자료버전식별자';
COMMENT ON COLUMN "submission_assets"."submission_role" IS '제출자료역할';
COMMENT ON COLUMN "submission_assets"."position" IS '자료순서';
COMMENT ON COLUMN "submission_assets"."submitted_metadata" IS '제출설명내용';

COMMENT ON TABLE "human_result_decisions" IS '사람판정정정';
COMMENT ON COLUMN "human_result_decisions"."decision_id" IS '사람정정식별자';
COMMENT ON COLUMN "human_result_decisions"."result_id" IS '검수항목결과식별자';
COMMENT ON COLUMN "human_result_decisions"."supersedes_decision_id" IS '이전정정식별자';
COMMENT ON COLUMN "human_result_decisions"."actor_user_id" IS '정정자식별자';
COMMENT ON COLUMN "human_result_decisions"."verdict" IS '정정판정';
COMMENT ON COLUMN "human_result_decisions"."reason" IS '정정사유';
COMMENT ON COLUMN "human_result_decisions"."decided_at" IS '정정시각';

COMMENT ON TABLE "review_reports" IS '검수보고서';
COMMENT ON COLUMN "review_reports"."report_id" IS '보고서식별자';
COMMENT ON COLUMN "review_reports"."run_id" IS '검수실행식별자';
COMMENT ON COLUMN "review_reports"."report_version" IS '보고서버전번호';
COMMENT ON COLUMN "review_reports"."report_file_id" IS '보고서파일식별자';
COMMENT ON COLUMN "review_reports"."snapshot_jsonb" IS '고정된보고내용';
COMMENT ON COLUMN "review_reports"."manifest_hash" IS '보고내용해시';
COMMENT ON COLUMN "review_reports"."generated_at" IS '생성시각';

COMMENT ON TABLE "approval_events" IS '사람승인이력';
COMMENT ON COLUMN "approval_events"."approval_event_id" IS '승인이력식별자';
COMMENT ON COLUMN "approval_events"."report_id" IS '보고서식별자';
COMMENT ON COLUMN "approval_events"."actor_user_id" IS '승인자식별자';
COMMENT ON COLUMN "approval_events"."purpose" IS '승인목적';
COMMENT ON COLUMN "approval_events"."action" IS '승인행위';
COMMENT ON COLUMN "approval_events"."reason" IS '승인사유';
COMMENT ON COLUMN "approval_events"."exception_jsonb" IS '처리한예외내용';
COMMENT ON COLUMN "approval_events"."target_manifest_hash" IS '대상보고내용해시';
COMMENT ON COLUMN "approval_events"."occurred_at" IS '발생시각';

COMMENT ON TABLE "remediation_tasks" IS '보완작업';
COMMENT ON COLUMN "remediation_tasks"."task_id" IS '보완작업식별자';
COMMENT ON COLUMN "remediation_tasks"."project_id" IS '프로젝트식별자';
COMMENT ON COLUMN "remediation_tasks"."source_run_id" IS '원검수실행식별자';
COMMENT ON COLUMN "remediation_tasks"."source_result_id" IS '원항목결과식별자';
COMMENT ON COLUMN "remediation_tasks"."criteria_item_id" IS '기준항목식별자';
COMMENT ON COLUMN "remediation_tasks"."task_type" IS '보완종류';
COMMENT ON COLUMN "remediation_tasks"."request_text" IS '요청내용';
COMMENT ON COLUMN "remediation_tasks"."assignee_user_id" IS '담당자식별자';
COMMENT ON COLUMN "remediation_tasks"."due_at" IS '처리기한';
COMMENT ON COLUMN "remediation_tasks"."completion_criteria" IS '완료조건내용';
COMMENT ON COLUMN "remediation_tasks"."state" IS '처리상태';
COMMENT ON COLUMN "remediation_tasks"."dedupe_key" IS '작업중복방지키';

COMMENT ON TABLE "remediation_submissions" IS '보완제출이력';
COMMENT ON COLUMN "remediation_submissions"."task_submission_id" IS '보완제출식별자';
COMMENT ON COLUMN "remediation_submissions"."task_id" IS '보완작업식별자';
COMMENT ON COLUMN "remediation_submissions"."submission_version_id" IS '납품묶음식별자';
COMMENT ON COLUMN "remediation_submissions"."verification_run_id" IS '재검수실행식별자';
COMMENT ON COLUMN "remediation_submissions"."verification_result_id" IS '확인결과식별자';
COMMENT ON COLUMN "remediation_submissions"."resolution_state" IS '해결확인상태';
COMMENT ON COLUMN "remediation_submissions"."verified_by" IS '확인자식별자';
COMMENT ON COLUMN "remediation_submissions"."submitted_at" IS '제출시각';
COMMENT ON COLUMN "remediation_submissions"."verified_at" IS '확인시각';

