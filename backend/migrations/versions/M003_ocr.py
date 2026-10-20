"""M003 OCR: frozen explicit DDL; no file IO, mutable schema import, FK or seed."""
from alembic import op
from alembic.util import CommandError
from sqlalchemy import Boolean, CheckConstraint, Column, Date, Double, Integer, PrimaryKeyConstraint, Text, UniqueConstraint, VARCHAR, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

revision = "M003"
down_revision = "M002"
branch_labels = None
depends_on = None


def upgrade():
    # A conflicting target object fails atomically; never overwrite or adopt it.

    op.create_table(
        "schema_definitions",
        Column('schema_id', UUID(as_uuid=True), nullable=False, comment='규격 PK'),
        Column('schema_name', VARCHAR(150), nullable=False, comment='실제 선언된 규격 이름'),
        Column('schema_version', VARCHAR(40), nullable=False, comment='실제 선언된 규격 버전'),
        Column('specification_file_id', UUID(as_uuid=True), nullable=True, comment='HTML·설명 문서 파일'),
        Column('validation_schema_file_id', UUID(as_uuid=True), nullable=True, comment='실행 가능한 JSON Schema 파일'),
        Column('schema_sha256', VARCHAR(64), nullable=True, comment='실제 검증 파일 바이트의 SHA256'),
        Column('availability_state', VARCHAR(40), nullable=False, server_default=text("'metadata_only'"), comment='`metadata_only`·`available`'),
        Column('access_scope', VARCHAR(40), nullable=False, server_default=text("'metadata_only'"), comment='`metadata_only`·`service_shared`. 규격의 실제 사용 허용 범위'),
        Column('field_catalog_jsonb', JSONB(none_as_null=True), nullable=True, comment='확인한 규격의 필드 카탈로그'),
        Column('created_at', TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='등록 시각'),
        PrimaryKeyConstraint('schema_id', name="pk_schema_definitions"),
        UniqueConstraint('schema_name', 'schema_version', name="uq_schema_definitions_name_version"),
        CheckConstraint("schema_sha256 IS NULL OR schema_sha256 ~ '^[0-9a-f]{64}$'", name="ck_schema_definitions_hashes"),
        CheckConstraint('(created_at IS NULL OR isfinite(created_at))', name="ck_schema_definitions_times"),
        CheckConstraint("(field_catalog_jsonb IS NULL OR jsonb_typeof(field_catalog_jsonb) = 'object')", name="ck_schema_definitions_json_shapes"),
        CheckConstraint("""schema_name ~ '[^[:space:]]' AND schema_version ~ '[^[:space:]]' AND availability_state IN ('metadata_only','available')
         AND access_scope IN ('metadata_only','service_shared')
         AND (availability_state <> 'available' OR (validation_schema_file_id IS NOT NULL AND schema_sha256 IS NOT NULL))
         AND (schema_sha256 IS NULL OR validation_schema_file_id IS NOT NULL)
         AND (access_scope <> 'service_shared' OR availability_state = 'available')""", name="ck_schema_definitions_domain"),
        comment='규격 정의', schema="public",
    )

    op.create_table(
        "import_packages",
        Column('import_id', UUID(as_uuid=True), nullable=False, comment='인계 묶음 PK'),
        Column('project_id', UUID(as_uuid=True), nullable=False, comment='수신 프로젝트'),
        Column('manifest_file_id', UUID(as_uuid=True), nullable=True, comment='실제 받은 원문 manifest 또는 부재를 명시하고 생성한 로컬 접수 manifest'),
        Column('replaces_import_id', UUID(as_uuid=True), nullable=True, comment='실제로 확인한 이전 묶음'),
        Column('source_namespace', VARCHAR(100), nullable=False, comment='인계 출처 구분'),
        Column('external_record_id', VARCHAR(200), nullable=True, comment='외부 레코드 ID 원문'),
        Column('package_fingerprint', VARCHAR(64), nullable=True, comment='실제 구성 파일 이름·해시로 계산한 묶음 지문'),
        Column('fingerprint_version', VARCHAR(40), nullable=True, comment='지문 직렬화·계산 정책 버전'),
        Column('hash_check_state', VARCHAR(40), nullable=False, server_default=text("'not_checked'"), comment='`not_checked`·`matched`·`mismatch`'),
        Column('import_state', VARCHAR(40), nullable=False, server_default=text("'received'"), comment='`received`·`completed`·`needs_review`·`failed`'),
        Column('reported_validation_jsonb', JSONB(none_as_null=True), nullable=True, comment='전달자가 보고한 검사 결과'),
        Column('local_validation_jsonb', JSONB(none_as_null=True), nullable=True, comment='이번 수신에서 실제 수행한 검사와 미확인 범위'),
        Column('provenance_jsonb', JSONB(none_as_null=True), nullable=True, comment='출처·원문 추적 ID·계보'),
        Column('imported_by', UUID(as_uuid=True), nullable=True, comment='수신 사용자. 시스템 작업은 NULL 가능'),
        Column('imported_at', TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='접수 시각'),
        PrimaryKeyConstraint('import_id', name="pk_import_packages"),
        UniqueConstraint('project_id', 'import_id', name="uq_import_packages_project_import"),
        UniqueConstraint('project_id', 'source_namespace', 'package_fingerprint', name="uq_import_packages_package_fingerprint"),
        CheckConstraint("package_fingerprint IS NULL OR package_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_import_packages_hashes"),
        CheckConstraint('(imported_at IS NULL OR isfinite(imported_at))', name="ck_import_packages_times"),
        CheckConstraint("(reported_validation_jsonb IS NULL OR jsonb_typeof(reported_validation_jsonb) = 'object') AND (local_validation_jsonb IS NULL OR jsonb_typeof(local_validation_jsonb) = 'object') AND (provenance_jsonb IS NULL OR jsonb_typeof(provenance_jsonb) = 'object')", name="ck_import_packages_json_shapes"),
        CheckConstraint("""source_namespace ~ '[^[:space:]]' AND hash_check_state IN ('not_checked','matched','mismatch')
         AND import_state IN ('received','completed','needs_review','failed')
         AND ((package_fingerprint IS NULL AND fingerprint_version IS NULL)
              OR (package_fingerprint IS NOT NULL AND fingerprint_version IS NOT NULL AND fingerprint_version ~ '[^[:space:]]'))
         AND (replaces_import_id IS NULL OR replaces_import_id <> import_id)
         AND (import_state <> 'completed' OR (manifest_file_id IS NOT NULL AND package_fingerprint IS NOT NULL
              AND fingerprint_version IS NOT NULL AND local_validation_jsonb IS NOT NULL AND hash_check_state = 'matched'))""", name="ck_import_packages_domain"),
        comment='인계 묶음', schema="public",
    )

    op.create_table(
        "audit_events",
        Column('event_id', UUID(as_uuid=True), nullable=False, comment='이벤트 PK'),
        Column('project_id', UUID(as_uuid=True), nullable=False, comment='업무 프로젝트'),
        Column('actor_kind', VARCHAR(20), nullable=False, comment='`user`·`developer`·`system`'),
        Column('actor_user_id', UUID(as_uuid=True), nullable=True, comment='연결 가능한 실제 DB 사용자'),
        Column('action', VARCHAR(100), nullable=False, comment='발생한 업무 동작'),
        Column('target_type', VARCHAR(100), nullable=False, comment='대상 업무 종류'),
        Column('target_id', UUID(as_uuid=True), nullable=True, comment='실제 존재하는 대상 ID'),
        Column('request_id', Text, nullable=True, comment='요청 추적 ID'),
        Column('event_metadata', JSONB(none_as_null=True), nullable=True, comment='상태 변경·안전한 사유·미확인 정보'),
        Column('occurred_at', TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='발생 시각'),
        PrimaryKeyConstraint('event_id', name="pk_audit_events"),
        CheckConstraint('(occurred_at IS NULL OR isfinite(occurred_at))', name="ck_audit_events_times"),
        CheckConstraint("(event_metadata IS NULL OR jsonb_typeof(event_metadata) = 'object')", name="ck_audit_events_json_shapes"),
        CheckConstraint("""action ~ '[^[:space:]]' AND target_type ~ '[^[:space:]]' AND actor_kind IN ('user','developer','system')
         AND (actor_kind <> 'user' OR actor_user_id IS NOT NULL)
         AND (actor_kind <> 'system' OR actor_user_id IS NULL)""", name="ck_audit_events_domain"),
        comment='감사 이벤트', schema="public",
    )

    op.create_table(
        "ocr_runs",
        Column('ocr_run_id', UUID(as_uuid=True), nullable=False, comment='OCR 실행 PK'),
        Column('project_id', UUID(as_uuid=True), nullable=False, comment='대상 프로젝트'),
        Column('asset_version_id', UUID(as_uuid=True), nullable=False, comment='대상 이미지 버전'),
        Column('attempt_id', UUID(as_uuid=True), nullable=True, comment='서비스의 처리 시도'),
        Column('import_package_id', UUID(as_uuid=True), nullable=True, comment='외부 OCR 결과의 수신 근거'),
        Column('schema_id', UUID(as_uuid=True), nullable=True, comment='실제 선언된 수신 규격'),
        Column('source_file_id', UUID(as_uuid=True), nullable=True, comment='원문 OCR JSON 파일. 결과 생성 전·실패 시 없을 수 있음'),
        Column('input_file_id', UUID(as_uuid=True), nullable=True, comment='실제 추론에 사용한 이미지 파일'),
        Column('frame_id', UUID(as_uuid=True), nullable=False, comment='저장한 OCR 토큰 좌표의 기준 프레임'),
        Column('execution_status', VARCHAR(40), nullable=False, comment='`queued`·`running`·`completed`·`failed`·`imported`'),
        Column('source_status', VARCHAR(40), nullable=True, comment='원문이 보고한 OCR 상태'),
        Column('engine_name', VARCHAR(100), nullable=True, comment='실제 실행·출력이 보고한 엔진'),
        Column('engine_version', VARCHAR(100), nullable=True, comment='확인한 엔진 버전'),
        Column('model_name', VARCHAR(200), nullable=True, comment='확인한 대표 모델 이름'),
        Column('device', VARCHAR(100), nullable=True, comment='실제 보고된 장치'),
        Column('execution_target', VARCHAR(20), nullable=True, comment='실제 보고된 `local`·`runpod`'),
        Column('pipeline_name', VARCHAR(100), nullable=True, comment='실행 경로 이름'),
        Column('pipeline_version', VARCHAR(100), nullable=True, comment='확인한 경로 버전'),
        Column('reported_coordinate_space', VARCHAR(100), nullable=True, comment='원문이 선언한 좌표 기준'),
        Column('coordinate_space', VARCHAR(100), nullable=False, comment='실제 DB 토큰 좌표의 기준'),
        Column('engine_settings', JSONB(none_as_null=True), nullable=True, comment='실행에 사용한 비밀정보 제외 설정'),
        Column('runtime_metadata', JSONB(none_as_null=True), nullable=True, comment='모델별 해시·revision, 시도 내역, 원문 추적 ID·시간 범위·출처'),
        Column('error_jsonb', JSONB(none_as_null=True), nullable=True, comment='실패 코드·단계·안전한 사유'),
        Column('started_at', TIMESTAMP(timezone=True), nullable=True, comment='서비스가 확인한 시작 시각'),
        Column('completed_at', TIMESTAMP(timezone=True), nullable=True, comment='서비스가 확인한 종료 시각'),
        Column('sealed_at', TIMESTAMP(timezone=True), nullable=True, comment='원문·토큰을 함께 동결한 로컬 시각. 생성 트랜잭션 종료 시 필수'),
        Column('created_at', TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='등록 시각'),
        PrimaryKeyConstraint('ocr_run_id', name="pk_ocr_runs"),
        UniqueConstraint('project_id', 'asset_version_id', 'ocr_run_id', name="uq_ocr_runs_project_asset_run"),
        CheckConstraint('(created_at IS NULL OR isfinite(created_at)) AND (started_at IS NULL OR isfinite(started_at)) AND (completed_at IS NULL OR isfinite(completed_at)) AND (sealed_at IS NULL OR isfinite(sealed_at)) AND (started_at IS NULL OR completed_at IS NULL OR completed_at >= started_at)', name="ck_ocr_runs_times"),
        CheckConstraint("(engine_settings IS NULL OR jsonb_typeof(engine_settings) = 'object') AND (runtime_metadata IS NULL OR jsonb_typeof(runtime_metadata) = 'object') AND (error_jsonb IS NULL OR jsonb_typeof(error_jsonb) = 'object')", name="ck_ocr_runs_json_shapes"),
        CheckConstraint("""coordinate_space ~ '[^[:space:]]' AND execution_status IN ('queued','running','completed','failed','imported')
         AND (attempt_id IS NOT NULL OR import_package_id IS NOT NULL)
         AND (execution_target IS NULL OR execution_target IN ('local','runpod'))
         AND (sealed_at IS NULL OR execution_status IN ('completed','failed','imported'))
         AND (execution_status <> 'completed' OR (attempt_id IS NOT NULL AND input_file_id IS NOT NULL
              AND source_file_id IS NOT NULL AND started_at IS NOT NULL AND completed_at IS NOT NULL))
         AND (execution_status <> 'failed' OR (completed_at IS NOT NULL AND error_jsonb IS NOT NULL))
         AND (execution_status <> 'imported' OR (source_file_id IS NOT NULL AND started_at IS NULL AND completed_at IS NULL))""", name="ck_ocr_runs_domain"),
        comment='OCR 실행', schema="public",
    )

    op.create_table(
        "ocr_tokens",
        Column('token_id', UUID(as_uuid=True), nullable=False, comment='내부 문구 PK'),
        Column('project_id', UUID(as_uuid=True), nullable=False, comment='소속 프로젝트'),
        Column('asset_version_id', UUID(as_uuid=True), nullable=False, comment='대상 이미지 버전'),
        Column('ocr_run_id', UUID(as_uuid=True), nullable=False, comment='소속 OCR 실행'),
        Column('external_token_id', VARCHAR(150), nullable=False, comment='원문 문구 ID의 문자열 표현'),
        Column('external_token_type', VARCHAR(10), nullable=False, comment='원문 ID의 `string`·`integer` 구분'),
        Column('token_order', Integer, nullable=False, comment='해당 실행 안의 0 이상 순서'),
        Column('text', Text, nullable=False, comment='OCR이 읽은 문구'),
        Column('confidence', Double, nullable=True, comment='실제 보고된 0~1의 유한 점수'),
        Column('bbox_xyxy', JSONB(none_as_null=True), nullable=True, comment='기준 프레임 안의 사각형 4개 수치'),
        Column('polygon', JSONB(none_as_null=True), nullable=True, comment='기준 프레임 안의 다각형 좌표열'),
        Column('recognition_engine', VARCHAR(100), nullable=True, comment='문구별 실제 인식 엔진'),
        Column('requires_review', Boolean, nullable=True, comment='원문이 명시한 보류 여부. 미제공은 미확인'),
        Column('source_record_jsonb', JSONB(none_as_null=True), nullable=False, comment='ID 타입·후보·이전 문구·점수·보류 사유를 포함한 원문 레코드'),
        Column('created_at', TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='등록 시각'),
        PrimaryKeyConstraint('token_id', name="pk_ocr_tokens"),
        UniqueConstraint('ocr_run_id', 'external_token_type', 'external_token_id', name="uq_ocr_tokens_source_token"),
        UniqueConstraint('ocr_run_id', 'token_order', name="uq_ocr_tokens_position"),
        UniqueConstraint('project_id', 'asset_version_id', 'ocr_run_id', 'token_id', name="uq_ocr_tokens_project_asset_run_token"),
        CheckConstraint('(created_at IS NULL OR isfinite(created_at))', name="ck_ocr_tokens_times"),
        CheckConstraint("""jsonb_typeof(source_record_jsonb) = 'object' AND (CASE WHEN bbox_xyxy IS NULL THEN TRUE
            WHEN jsonb_typeof(bbox_xyxy) = 'array' THEN CASE
                WHEN jsonb_array_length(bbox_xyxy) = 4
                     AND NOT jsonb_path_exists(bbox_xyxy, 'strict $[*] ? (@.type() != "number")')
                THEN (bbox_xyxy->>0)::numeric >= 0 AND (bbox_xyxy->>1)::numeric >= 0
                     AND (bbox_xyxy->>0)::numeric < (bbox_xyxy->>2)::numeric
                     AND (bbox_xyxy->>1)::numeric < (bbox_xyxy->>3)::numeric
                ELSE FALSE END
            ELSE FALSE END) AND (CASE WHEN polygon IS NULL THEN TRUE
            WHEN jsonb_typeof(polygon) = 'array' THEN CASE
                WHEN jsonb_array_length(polygon) >= 3
                     AND NOT jsonb_path_exists(polygon, 'strict $[*] ? (@.type() != "array" || @.size() != 2)')
                THEN NOT jsonb_path_exists(polygon, 'strict $[*][*] ? (@.type() != "number")')
                ELSE FALSE END
            ELSE FALSE END)""", name="ck_ocr_tokens_json_shapes"),
        CheckConstraint("""external_token_id ~ '[^[:space:]]' AND external_token_type IN ('string','integer') AND token_order >= 0
         AND (external_token_type <> 'integer' OR external_token_id ~ '^(0|-?[1-9][0-9]*)$') AND (confidence IS NULL OR confidence BETWEEN 0 AND 1)""", name="ck_ocr_tokens_domain"),
        comment='OCR 문구', schema="public",
    )

    op.create_table(
        "poster_extractions",
        Column('extraction_id', UUID(as_uuid=True), nullable=False, comment='추출본 PK'),
        Column('project_id', UUID(as_uuid=True), nullable=False, comment='대상 프로젝트'),
        Column('asset_version_id', UUID(as_uuid=True), nullable=False, comment='대상 이미지 버전'),
        Column('schema_id', UUID(as_uuid=True), nullable=True, comment='실제 선언된 추출 표준'),
        Column('ocr_run_id', UUID(as_uuid=True), nullable=True, comment='사용한 OCR 실행'),
        Column('import_package_id', UUID(as_uuid=True), nullable=True, comment='수신 묶음'),
        Column('predecessor_id', UUID(as_uuid=True), nullable=True, comment='같은 이미지 버전의 이전 추출본'),
        Column('source_file_id', UUID(as_uuid=True), nullable=True, comment='추출 원문 JSON 파일'),
        Column('version_no', Integer, nullable=False, comment='이미지 버전 안의 1 이상 추출 버전'),
        Column('method', VARCHAR(50), nullable=False, comment='실제 추출·변환 방법'),
        Column('extractor_version', VARCHAR(100), nullable=True, comment='확인한 추출기 버전'),
        Column('payload_jsonb', JSONB(none_as_null=True), nullable=False, comment='상태·후보·경고·부분 날짜를 포함한 전체 추출 내용'),
        Column('title_official', Text, nullable=True, comment='실제 추출본에서 투영한 제목'),
        Column('location_raw', Text, nullable=True, comment='실제 추출본에서 투영한 장소 원문'),
        Column('year_value', Integer, nullable=True, comment='근거가 있는 연도'),
        Column('start_year', Integer, nullable=True, comment='시작 연도'),
        Column('start_month', Integer, nullable=True, comment='시작 월'),
        Column('start_day', Integer, nullable=True, comment='시작 일'),
        Column('end_year', Integer, nullable=True, comment='종료 연도'),
        Column('end_month', Integer, nullable=True, comment='종료 월'),
        Column('end_day', Integer, nullable=True, comment='종료 일'),
        Column('start_date', Date, nullable=True, comment='완전하고 유효한 시작 날짜'),
        Column('end_date', Date, nullable=True, comment='완전하고 유효한 종료 날짜'),
        Column('compatibility_state', VARCHAR(40), nullable=False, comment='`native`·`legacy`·`pending_enrichment`'),
        Column('validation_state', VARCHAR(40), nullable=False, server_default=text("'unchecked'"), comment='`unchecked`·`valid`·`invalid`·`schema_unavailable`'),
        Column('checks_jsonb', JSONB(none_as_null=True), nullable=True, comment='실제 검사·경고·실행하지 못한 검사'),
        Column('provenance_jsonb', JSONB(none_as_null=True), nullable=True, comment='생성 주체·변환·원문 추적 정보'),
        Column('projection_version', VARCHAR(100), nullable=False, comment='제목·날짜·관찰 행을 만드는 변환 정책 버전'),
        Column('sealed_at', TIMESTAMP(timezone=True), nullable=True, comment='추출·관찰·관찰 근거를 함께 동결한 로컬 시각. 생성 트랜잭션 종료 시 필수'),
        Column('created_at', TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='생성 시각'),
        PrimaryKeyConstraint('extraction_id', name="pk_poster_extractions"),
        UniqueConstraint('asset_version_id', 'version_no', name="uq_poster_extractions_asset_version"),
        UniqueConstraint('project_id', 'asset_version_id', 'extraction_id', name="uq_poster_extractions_project_asset_extraction"),
        CheckConstraint('(created_at IS NULL OR isfinite(created_at)) AND (sealed_at IS NULL OR isfinite(sealed_at))', name="ck_poster_extractions_times"),
        CheckConstraint("jsonb_typeof(payload_jsonb) = 'object' AND (checks_jsonb IS NULL OR jsonb_typeof(checks_jsonb) = 'object') AND (provenance_jsonb IS NULL OR jsonb_typeof(provenance_jsonb) = 'object')", name="ck_poster_extractions_json_shapes"),
        CheckConstraint("""method ~ '[^[:space:]]' AND projection_version ~ '[^[:space:]]' AND version_no >= 1
         AND compatibility_state IN ('native','legacy','pending_enrichment')
         AND validation_state IN ('unchecked','valid','invalid','schema_unavailable')
         AND (validation_state <> 'valid' OR (schema_id IS NOT NULL AND checks_jsonb IS NOT NULL))
         AND (predecessor_id IS NULL OR predecessor_id <> extraction_id)
         AND (year_value IS NULL OR year_value BETWEEN 1 AND 9999) AND (start_year IS NULL OR start_year BETWEEN 1 AND 9999) AND (start_month IS NULL OR start_month BETWEEN 1 AND 12) AND (start_day IS NULL OR start_day BETWEEN 1 AND 31) AND (start_date IS NULL OR (isfinite(start_date) AND start_year IS NOT NULL AND start_month IS NOT NULL AND start_day IS NOT NULL AND EXTRACT(YEAR FROM start_date) = start_year AND EXTRACT(MONTH FROM start_date) = start_month AND EXTRACT(DAY FROM start_date) = start_day)) AND (end_year IS NULL OR end_year BETWEEN 1 AND 9999) AND (end_month IS NULL OR end_month BETWEEN 1 AND 12) AND (end_day IS NULL OR end_day BETWEEN 1 AND 31) AND (end_date IS NULL OR (isfinite(end_date) AND end_year IS NOT NULL AND end_month IS NOT NULL AND end_day IS NOT NULL AND EXTRACT(YEAR FROM end_date) = end_year AND EXTRACT(MONTH FROM end_date) = end_month AND EXTRACT(DAY FROM end_date) = end_day))""", name="ck_poster_extractions_domain"),
        comment='행사 정보 추출본', schema="public",
    )

    op.create_table(
        "poster_field_observations",
        Column('observation_id', UUID(as_uuid=True), nullable=False, comment='관찰 PK'),
        Column('project_id', UUID(as_uuid=True), nullable=False, comment='대상 프로젝트'),
        Column('asset_version_id', UUID(as_uuid=True), nullable=False, comment='대상 이미지 버전'),
        Column('extraction_id', UUID(as_uuid=True), nullable=False, comment='원본 추출 스냅샷'),
        Column('field_code', VARCHAR(100), nullable=False, comment='명시한 추출 형식의 필드 코드'),
        Column('field_path', VARCHAR(255), nullable=False, comment='원래 payload 안의 필드 경로'),
        Column('presence_status', VARCHAR(40), nullable=False, server_default=text("'not_checked'"), comment='`present`·`illegible`·`absent`·`not_checked`'),
        Column('resolution_status', VARCHAR(40), nullable=False, server_default=text("'unprocessed'"), comment='`resolved`·`review_needed`·`unprocessed`'),
        Column('raw_text', Text, nullable=True, comment='실제 원문 문구'),
        Column('normalized_value', JSONB(none_as_null=True), nullable=True, comment='문자열·배열·부분값 등 필드 내용'),
        Column('source_status', VARCHAR(40), nullable=True, comment='`extracted`·`needs_review` 등 원문 추출기 상태'),
        Column('source_reason', Text, nullable=True, comment='원문 사유'),
        Column('adapter_version', VARCHAR(100), nullable=False, comment='필드 대응·상태 변환 정책 버전'),
        Column('normalization_jsonb', JSONB(none_as_null=True), nullable=True, comment='변환 단계·후보·원래 OCR ID·미확인 정보'),
        Column('created_at', TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='투영 생성 시각'),
        PrimaryKeyConstraint('observation_id', name="pk_poster_field_observations"),
        UniqueConstraint('extraction_id', 'field_path', name="uq_poster_field_observations_extraction_path"),
        UniqueConstraint('project_id', 'asset_version_id', 'observation_id', name="uq_poster_field_observations_project_asset_observation"),
        CheckConstraint('(created_at IS NULL OR isfinite(created_at))', name="ck_poster_field_observations_times"),
        CheckConstraint("(normalization_jsonb IS NULL OR jsonb_typeof(normalization_jsonb) = 'object')", name="ck_poster_field_observations_json_shapes"),
        CheckConstraint("""field_code ~ '[^[:space:]]' AND field_path ~ '[^[:space:]]' AND adapter_version ~ '[^[:space:]]' AND presence_status IN ('present','illegible','absent','not_checked')
         AND resolution_status IN ('resolved','review_needed','unprocessed')
         AND (resolution_status <> 'resolved' OR (presence_status = 'present' AND normalized_value IS NOT NULL AND normalized_value <> 'null'::jsonb))
         AND (presence_status <> 'absent' OR normalized_value IS NULL)""", name="ck_poster_field_observations_domain"),
        comment='필드별 관찰', schema="public",
    )

    op.create_table(
        "evidence_records",
        Column('evidence_id', UUID(as_uuid=True), nullable=False, comment='근거 PK'),
        Column('project_id', UUID(as_uuid=True), nullable=False, comment='대상 프로젝트'),
        Column('observation_id', UUID(as_uuid=True), nullable=True, comment='소유 필드 관찰'),
        Column('item_result_id', UUID(as_uuid=True), nullable=True, comment='소유 항목 결과'),
        Column('asset_version_id', UUID(as_uuid=True), nullable=False, comment='근거 대상 이미지 버전'),
        Column('source_file_id', UUID(as_uuid=True), nullable=True, comment='실제 근거 파일'),
        Column('frame_id', UUID(as_uuid=True), nullable=True, comment='bbox·polygon의 기준 프레임'),
        Column('source_ocr_run_id', UUID(as_uuid=True), nullable=True, comment='실제 출처 OCR 실행'),
        Column('source_kind', VARCHAR(40), nullable=False, comment='첫 범위의 `ocr`·`image`·`document`'),
        Column('field_pointer', VARCHAR(255), nullable=True, comment='원문 필드·목록 항목의 구체 경로'),
        Column('page_no', Integer, nullable=True, comment='원문이 식별한 페이지'),
        Column('text', Text, nullable=True, comment='근거 원문 발췌'),
        Column('bbox_xyxy', JSONB(none_as_null=True), nullable=True, comment='근거 사각형'),
        Column('polygon', JSONB(none_as_null=True), nullable=True, comment='근거 다각형'),
        Column('confidence', Double, nullable=True, comment='원문이 보고한 근거 점수'),
        Column('method_code', VARCHAR(100), nullable=False, comment='실제 근거 획득 방법'),
        Column('locator_jsonb', JSONB(none_as_null=True), nullable=True, comment='문서 위치·세부 출처'),
        Column('limitations_jsonb', JSONB(none_as_null=True), nullable=True, comment='확인 한계·보류 사유'),
        Column('created_at', TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='생성 시각'),
        PrimaryKeyConstraint('evidence_id', name="pk_evidence_records"),
        UniqueConstraint('project_id', 'asset_version_id', 'evidence_id', name="uq_evidence_records_project_asset_evidence"),
        UniqueConstraint('project_id', 'asset_version_id', 'source_ocr_run_id', 'evidence_id', name="uq_evidence_records_ocr_evidence"),
        CheckConstraint('(created_at IS NULL OR isfinite(created_at))', name="ck_evidence_records_times"),
        CheckConstraint("""(locator_jsonb IS NULL OR jsonb_typeof(locator_jsonb) = 'object') AND (limitations_jsonb IS NULL OR jsonb_typeof(limitations_jsonb) IN ('object','array')) AND (CASE WHEN bbox_xyxy IS NULL THEN TRUE
            WHEN jsonb_typeof(bbox_xyxy) = 'array' THEN CASE
                WHEN jsonb_array_length(bbox_xyxy) = 4
                     AND NOT jsonb_path_exists(bbox_xyxy, 'strict $[*] ? (@.type() != "number")')
                THEN (bbox_xyxy->>0)::numeric >= 0 AND (bbox_xyxy->>1)::numeric >= 0
                     AND (bbox_xyxy->>0)::numeric < (bbox_xyxy->>2)::numeric
                     AND (bbox_xyxy->>1)::numeric < (bbox_xyxy->>3)::numeric
                ELSE FALSE END
            ELSE FALSE END) AND (CASE WHEN polygon IS NULL THEN TRUE
            WHEN jsonb_typeof(polygon) = 'array' THEN CASE
                WHEN jsonb_array_length(polygon) >= 3
                     AND NOT jsonb_path_exists(polygon, 'strict $[*] ? (@.type() != "array" || @.size() != 2)')
                THEN NOT jsonb_path_exists(polygon, 'strict $[*][*] ? (@.type() != "number")')
                ELSE FALSE END
            ELSE FALSE END)""", name="ck_evidence_records_json_shapes"),
        CheckConstraint("""method_code ~ '[^[:space:]]' AND ((observation_id IS NOT NULL AND item_result_id IS NULL) OR (observation_id IS NULL AND item_result_id IS NOT NULL))
         AND source_kind IN ('ocr','image','document')
         AND (source_kind <> 'ocr' OR source_ocr_run_id IS NOT NULL)
         AND (source_kind <> 'image' OR (source_file_id IS NOT NULL AND frame_id IS NOT NULL))
         AND (source_kind <> 'document' OR (source_file_id IS NOT NULL AND
              ((field_pointer IS NOT NULL AND field_pointer ~ '[^[:space:]]') OR page_no IS NOT NULL
               OR (locator_jsonb IS NOT NULL AND locator_jsonb <> '{}'::jsonb))))
         AND ((bbox_xyxy IS NULL AND polygon IS NULL) OR frame_id IS NOT NULL)
         AND (page_no IS NULL OR page_no >= 1) AND (confidence IS NULL OR confidence BETWEEN 0 AND 1)""", name="ck_evidence_records_domain"),
        comment='근거', schema="public",
    )

    op.create_table(
        "evidence_ocr_tokens",
        Column('evidence_id', UUID(as_uuid=True), nullable=False, comment='소유 근거'),
        Column('token_id', UUID(as_uuid=True), nullable=False, comment='실제 OCR 문구'),
        Column('project_id', UUID(as_uuid=True), nullable=False, comment='공통 프로젝트'),
        Column('asset_version_id', UUID(as_uuid=True), nullable=False, comment='공통 이미지 버전'),
        Column('ocr_run_id', UUID(as_uuid=True), nullable=False, comment='공통 OCR 실행'),
        Column('token_order', Integer, nullable=False, comment='해당 근거 안의 0 이상 연결 순서'),
        Column('created_at', TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='연결 생성 시각'),
        PrimaryKeyConstraint('evidence_id', 'token_id', name="pk_evidence_ocr_tokens"),
        UniqueConstraint('evidence_id', 'token_order', name="uq_evidence_ocr_tokens_position"),
        CheckConstraint('(created_at IS NULL OR isfinite(created_at))', name="ck_evidence_ocr_tokens_times"),
        CheckConstraint('token_order >= 0', name="ck_evidence_ocr_tokens_domain"),
        comment='근거와 OCR 문구 연결', schema="public",
    )


def downgrade():
    raise CommandError("M003 destructive downgrade is disabled; use a reviewed restore or recreate an isolated test DB.")
