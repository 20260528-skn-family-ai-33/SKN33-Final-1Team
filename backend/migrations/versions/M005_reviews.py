"""M005 reviews: frozen explicit DDL; all FK are added separately by M006."""
from alembic import op
from alembic.util import CommandError
from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, Integer, Numeric, PrimaryKeyConstraint, SmallInteger, Text, UniqueConstraint, VARCHAR, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

revision = "M005"
down_revision = "M004"
branch_labels = None
depends_on = None


def upgrade():
    # A conflicting target fails atomically; never overwrite or adopt it.

    op.create_table(
        "review_runs",
        Column('run_id', UUID(as_uuid=True), nullable=False, comment='검수 실행 PK'),
        Column('project_id', UUID(as_uuid=True), nullable=False, comment='소속 프로젝트'),
        Column('asset_version_id', UUID(as_uuid=True), nullable=False, comment='접수 시 고정한 이미지 버전'),
        Column('criteria_version_id', UUID(as_uuid=True), nullable=False, comment='접수 시 고정한 기준 버전'),
        Column('retry_of_run_id', UUID(as_uuid=True), nullable=True, comment='사용자가 명시적으로 재검수한 원 실행'),
        Column('requested_by', UUID(as_uuid=True), nullable=True, comment='실제 확인한 검수 요청 사용자'),
        Column('request_operation', VARCHAR(40), nullable=True, comment='`create_review`·`retry_review`'),
        Column('idempotency_key', UUID(as_uuid=True), nullable=True, comment='검수 생성·재검수 요청의 멱등키'),
        Column('request_hash', VARCHAR(64), nullable=True, comment='정규화한 요청 본문·요청 사용자·operation의 SHA256'),
        Column('request_hash_version', VARCHAR(40), nullable=True, comment='요청 지문 직렬화 규격 버전'),
        Column('origin_kind', VARCHAR(40), nullable=False, comment='`service_execution`·`imported_result`·`legacy_result`'),
        Column('import_package_id', UUID(as_uuid=True), nullable=True, comment='실제 외부 인계 묶음'),
        Column('source_file_id', UUID(as_uuid=True), nullable=True, comment='보존한 원문 결과 JSON 파일'),
        Column('source_snapshot_jsonb', JSONB(none_as_null=True), nullable=True, comment='수신 실행 원문·외부 ID·원래 시각·누락 필드'),
        Column('source_status', Text, nullable=True, comment='원문이 보고한 처리 상태'),
        Column('project_name_snapshot', Text, nullable=True, comment='접수 당시 표시명. 과거 기록은 실제 원래 이름'),
        Column('execution_status', VARCHAR(40), nullable=False, comment='`queued`·`running`·`completed`·`failed`·`imported`'),
        Column('current_stage', VARCHAR(50), nullable=True, comment='`queued`·`preprocessing`·`ocr`·`review`·`completed`·`failed`'),
        Column('progress', SmallInteger, nullable=True, comment='실제 단계에서 보고한 0~100의 화면 진행률'),
        Column('overall_verdict', VARCHAR(40), nullable=True, comment='고정 정책으로 계산한 자동 요약 `pass`·`fail`·`review`'),
        Column('orchestration_mode', VARCHAR(40), nullable=False, comment='첫 범위의 `single_pipeline`·수입 경로 `imported`'),
        Column('executor_kind', VARCHAR(40), nullable=False, comment='`background_tasks`·`postgres_worker`·`imported`'),
        Column('workflow_version', VARCHAR(100), nullable=True, comment='실제 실행할 파이프라인·판정 코드 식별 버전'),
        Column('aggregation_policy_version', VARCHAR(100), nullable=True, comment='요약·미완료 항목 처리 정책 버전'),
        Column('conflict_policy_version', VARCHAR(100), nullable=True, comment='첫 단일 파이프라인에서는 NULL'),
        Column('execution_target', VARCHAR(20), nullable=True, comment='요청 시 서버가 확정한 `local`·`runpod`'),
        Column('requested_config', JSONB(none_as_null=True), nullable=True, comment='접수 시 고정한 경로·모델·규칙·전처리 설정. 비밀정보 제외'),
        Column('input_manifest_hash', VARCHAR(64), nullable=True, comment='이미지·기준·설정·정책을 묶은 고정 입력 지문'),
        Column('manifest_hash_version', VARCHAR(40), nullable=True, comment='입력·최종 결과 manifest의 직렬화 규격 버전'),
        Column('retry_policy_jsonb', JSONB(none_as_null=True), nullable=True, comment='총 시도 상한·안전 재시도 분류·backoff·lease 정책'),
        Column('available_at', TIMESTAMP(timezone=True), nullable=True, comment='queued 실행의 다음 인수 가능 시각'),
        Column('active_attempt_id', UUID(as_uuid=True), nullable=True, comment='현재 실행 권한을 가진 시도'),
        Column('selected_attempt_id', UUID(as_uuid=True), nullable=True, comment='최종 결과 전체를 만든 채택 시도'),
        Column('error_jsonb', JSONB(none_as_null=True), nullable=True, comment='안전한 오류 코드·단계·사유·결과 불명 정보'),
        Column('result_manifest_hash', VARCHAR(64), nullable=True, comment='최종 채택 결과·근거 목록의 불변 지문'),
        Column('created_at', TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='로컬 접수·등록 시각'),
        Column('updated_at', TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='상태·단계 변경 시각'),
        Column('started_at', TIMESTAMP(timezone=True), nullable=True, comment='실행의 첫 처리 시작 시각'),
        Column('finished_at', TIMESTAMP(timezone=True), nullable=True, comment='최종 완료·실패 시각'),
        Column('sealed_at', TIMESTAMP(timezone=True), nullable=True, comment='서비스 결과 집합 또는 수입 원문·결과 연결을 구조 검증·동결한 로컬 시각'),
        PrimaryKeyConstraint('run_id', name="pk_review_runs"),
        UniqueConstraint('project_id', 'asset_version_id', 'run_id', name="uq_review_runs_project_asset_run"),
        UniqueConstraint('project_id', 'asset_version_id', 'criteria_version_id', 'run_id', name="uq_review_runs_project_asset_criteria_run"),
        UniqueConstraint('requested_by', 'request_operation', 'idempotency_key', name="uq_review_runs_request"),
        CheckConstraint("(request_hash IS NULL OR request_hash ~ '^[0-9a-f]{64}$') AND (input_manifest_hash IS NULL OR input_manifest_hash ~ '^[0-9a-f]{64}$') AND (result_manifest_hash IS NULL OR result_manifest_hash ~ '^[0-9a-f]{64}$')", name="ck_review_runs_hashes"),
        CheckConstraint('(created_at IS NULL OR isfinite(created_at)) AND (updated_at IS NULL OR isfinite(updated_at)) AND (available_at IS NULL OR isfinite(available_at)) AND (started_at IS NULL OR isfinite(started_at)) AND (finished_at IS NULL OR isfinite(finished_at)) AND (sealed_at IS NULL OR isfinite(sealed_at)) AND updated_at >= created_at AND (started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at)', name="ck_review_runs_times"),
        CheckConstraint("(source_snapshot_jsonb IS NULL OR jsonb_typeof(source_snapshot_jsonb) = 'object') AND (requested_config IS NULL OR jsonb_typeof(requested_config) = 'object') AND (retry_policy_jsonb IS NULL OR jsonb_typeof(retry_policy_jsonb) = 'object') AND (error_jsonb IS NULL OR jsonb_typeof(error_jsonb) = 'object')", name="ck_review_runs_json_shapes"),
        CheckConstraint("""(progress IS NULL OR progress BETWEEN 0 AND 100)
         AND (overall_verdict IS NULL OR overall_verdict IN ('pass','fail','review'))
         AND (execution_target IS NULL OR execution_target IN ('local','runpod'))
         AND (manifest_hash_version IS NULL OR manifest_hash_version ~ '[^[:space:]]')
         AND (retry_of_run_id IS NULL OR retry_of_run_id <> run_id)
         AND (
           (origin_kind = 'service_execution'
            AND orchestration_mode = 'single_pipeline' AND executor_kind IN ('background_tasks','postgres_worker')
            AND requested_by IS NOT NULL AND request_operation IS NOT NULL AND idempotency_key IS NOT NULL
            AND request_hash IS NOT NULL AND request_hash_version IS NOT NULL AND request_hash_version ~ '[^[:space:]]'
            AND workflow_version IS NOT NULL AND workflow_version ~ '[^[:space:]]'
            AND aggregation_policy_version IS NOT NULL AND aggregation_policy_version ~ '[^[:space:]]'
            AND conflict_policy_version IS NULL AND execution_target IS NOT NULL
            AND requested_config IS NOT NULL AND input_manifest_hash IS NOT NULL AND manifest_hash_version IS NOT NULL
            AND retry_policy_jsonb IS NOT NULL AND current_stage IS NOT NULL
            AND ((retry_of_run_id IS NULL AND request_operation = 'create_review')
                 OR (retry_of_run_id IS NOT NULL AND request_operation = 'retry_review'))
            AND (execution_status = 'completed' OR progress IS NULL OR progress < 100)
            AND (
               (execution_status = 'queued' AND current_stage = 'queued' AND available_at IS NOT NULL
                AND active_attempt_id IS NULL AND selected_attempt_id IS NULL AND finished_at IS NULL
                AND sealed_at IS NULL AND result_manifest_hash IS NULL AND overall_verdict IS NULL AND error_jsonb IS NULL)
               OR
               (execution_status = 'running' AND current_stage IN ('preprocessing','ocr','review')
                AND active_attempt_id IS NOT NULL AND started_at IS NOT NULL AND available_at IS NULL
                AND selected_attempt_id IS NULL AND finished_at IS NULL AND sealed_at IS NULL
                AND result_manifest_hash IS NULL AND overall_verdict IS NULL AND error_jsonb IS NULL)
               OR
               (execution_status = 'completed' AND current_stage = 'completed' AND progress IS NOT NULL AND progress = 100
                AND selected_attempt_id IS NOT NULL AND started_at IS NOT NULL AND finished_at IS NOT NULL
                AND sealed_at IS NOT NULL AND result_manifest_hash IS NOT NULL AND overall_verdict IS NOT NULL
                AND active_attempt_id IS NULL AND available_at IS NULL AND error_jsonb IS NULL)
               OR
               (execution_status = 'failed' AND current_stage = 'failed' AND finished_at IS NOT NULL AND error_jsonb IS NOT NULL
                AND active_attempt_id IS NULL AND selected_attempt_id IS NULL AND available_at IS NULL
                AND sealed_at IS NULL AND result_manifest_hash IS NULL AND overall_verdict IS NULL)
            ))
           OR
           (origin_kind IN ('imported_result','legacy_result') AND execution_status = 'imported'
            AND executor_kind = 'imported' AND orchestration_mode = 'imported'
            AND current_stage IS NULL AND progress IS NULL AND started_at IS NULL AND finished_at IS NULL
            AND available_at IS NULL AND active_attempt_id IS NULL AND overall_verdict IS NULL
            AND request_operation IS NULL AND idempotency_key IS NULL AND request_hash IS NULL AND request_hash_version IS NULL
            AND ((origin_kind = 'imported_result' AND import_package_id IS NOT NULL)
                 OR (origin_kind = 'legacy_result' AND source_snapshot_jsonb IS NOT NULL)))
         )""", name="ck_review_runs_domain"),
        comment='검수 실행', schema="public",
    )

    op.create_table(
        "review_attempts",
        Column('attempt_id', UUID(as_uuid=True), nullable=False, comment='처리 시도 PK'),
        Column('project_id', UUID(as_uuid=True), nullable=False, comment='실행과 같은 프로젝트'),
        Column('asset_version_id', UUID(as_uuid=True), nullable=False, comment='실행과 같은 이미지 버전'),
        Column('run_id', UUID(as_uuid=True), nullable=False, comment='부모 실행'),
        Column('attempt_no', Integer, nullable=False, comment='실행 안의 1부터 증가하는 전체 시도 번호'),
        Column('stage', VARCHAR(50), nullable=True, comment='시도의 현재·마지막 preprocessing/ocr/review 단계'),
        Column('execution_status', VARCHAR(40), nullable=False, comment='`active`·`succeeded`·`failed`·`unknown`·`imported`'),
        Column('dispatch_state', VARCHAR(40), nullable=True, comment='`not_dispatched`·`dispatching`·`submitted`·`unknown`'),
        Column('worker_token', UUID(as_uuid=True), nullable=True, comment='해당 시도의 실행·저장 권한을 식별하는 임의 토큰'),
        Column('lease_expires_at', TIMESTAMP(timezone=True), nullable=True, comment='독립 DB worker 권한의 만료 시각'),
        Column('heartbeat_at', TIMESTAMP(timezone=True), nullable=True, comment='독립 DB worker의 마지막 유효 heartbeat'),
        Column('request_id', Text, nullable=True, comment='실제 내부 호출 추적 ID'),
        Column('remote_job_id', Text, nullable=True, comment='실제 확인한 원격 작업 ID'),
        Column('requested_config', JSONB(none_as_null=True), nullable=True, comment='부모 실행의 고정 설정과 이번 시도의 허용된 실행 조건'),
        Column('actual_runtime', JSONB(none_as_null=True), nullable=True, comment='실제 엔진·모델·장치·fallback·시간·출처 정보'),
        Column('retryable', Boolean, nullable=True, comment='오류 분류기가 실제 확인한 재시도 가능성. 단독 승인 조건 아님'),
        Column('timing_jsonb', JSONB(none_as_null=True), nullable=True, comment='측정 구간·단위·실제 시간'),
        Column('cost_amount', Numeric(20, 8), nullable=True, comment='실제 확인한 비용. 미측정은 NULL'),
        Column('currency', VARCHAR(3), nullable=True, comment='비용에 실제 적용된 통화 코드'),
        Column('error_jsonb', JSONB(none_as_null=True), nullable=True, comment='안전한 오류 코드·원인·제출/결과 불명 정보'),
        Column('source_attempt_jsonb', JSONB(none_as_null=True), nullable=True, comment='외부 시도 정보·원래 번호·누락 필드'),
        Column('created_at', TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='로컬 시도 등록 시각'),
        Column('started_at', TIMESTAMP(timezone=True), nullable=True, comment='실제 시도 시작 시각'),
        Column('finished_at', TIMESTAMP(timezone=True), nullable=True, comment='succeeded/failed/unknown의 종료 시각'),
        PrimaryKeyConstraint('attempt_id', name="pk_review_attempts"),
        UniqueConstraint('run_id', 'attempt_no', name="uq_review_attempts_run_number"),
        UniqueConstraint('project_id', 'asset_version_id', 'attempt_id', name="uq_review_attempts_project_asset_attempt"),
        UniqueConstraint('project_id', 'asset_version_id', 'run_id', 'attempt_id', name="uq_review_attempts_project_asset_run_attempt"),
        CheckConstraint('(created_at IS NULL OR isfinite(created_at)) AND (started_at IS NULL OR isfinite(started_at)) AND (finished_at IS NULL OR isfinite(finished_at)) AND (lease_expires_at IS NULL OR isfinite(lease_expires_at)) AND (heartbeat_at IS NULL OR isfinite(heartbeat_at)) AND (started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at)', name="ck_review_attempts_times"),
        CheckConstraint("(requested_config IS NULL OR jsonb_typeof(requested_config) = 'object') AND (actual_runtime IS NULL OR jsonb_typeof(actual_runtime) = 'object') AND (timing_jsonb IS NULL OR jsonb_typeof(timing_jsonb) = 'object') AND (error_jsonb IS NULL OR jsonb_typeof(error_jsonb) = 'object') AND (source_attempt_jsonb IS NULL OR jsonb_typeof(source_attempt_jsonb) = 'object')", name="ck_review_attempts_json_shapes"),
        CheckConstraint("""attempt_no >= 1
         AND (stage IS NULL OR stage IN ('preprocessing','ocr','review'))
         AND (dispatch_state IS NULL OR dispatch_state IN ('not_dispatched','dispatching','submitted','unknown'))
         AND ((lease_expires_at IS NULL AND heartbeat_at IS NULL)
              OR (lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL AND lease_expires_at > heartbeat_at))
         AND ((cost_amount IS NULL AND currency IS NULL)
              OR (cost_amount IS NOT NULL AND currency IS NOT NULL AND cost_amount >= 0
                  AND cost_amount < 'Infinity'::numeric AND currency ~ '^[A-Z]{3}$'))
         AND (
           (execution_status = 'active' AND started_at IS NOT NULL AND worker_token IS NOT NULL AND finished_at IS NULL)
           OR
           (execution_status IN ('succeeded','failed','unknown') AND started_at IS NOT NULL
            AND worker_token IS NOT NULL AND finished_at IS NOT NULL)
           OR
           (execution_status = 'imported' AND worker_token IS NULL AND lease_expires_at IS NULL AND heartbeat_at IS NULL
            AND started_at IS NULL AND finished_at IS NULL AND source_attempt_jsonb IS NOT NULL)
         )""", name="ck_review_attempts_domain"),
        comment='실행 전체의 처리 시도', schema="public",
    )

    op.create_table(
        "item_results",
        Column('result_id', UUID(as_uuid=True), nullable=False, comment='항목 결과 PK'),
        Column('project_id', UUID(as_uuid=True), nullable=False, comment='실행과 같은 프로젝트'),
        Column('asset_version_id', UUID(as_uuid=True), nullable=False, comment='실행과 같은 이미지 버전'),
        Column('run_id', UUID(as_uuid=True), nullable=False, comment='소속 실행'),
        Column('attempt_id', UUID(as_uuid=True), nullable=False, comment='결과를 만든 시도 또는 명시적 수입 묶음'),
        Column('criteria_version_id', UUID(as_uuid=True), nullable=False, comment='실행과 같은 기준 버전'),
        Column('criteria_item_id', UUID(as_uuid=True), nullable=False, comment='해당 버전 안의 불변 기준 항목'),
        Column('ocr_run_id', UUID(as_uuid=True), nullable=True, comment='실제 사용한 OCR 실행'),
        Column('extraction_id', UUID(as_uuid=True), nullable=True, comment='실제 사용한 행사 정보 추출본'),
        Column('result_origin', VARCHAR(40), nullable=False, comment='`service_execution`·`imported_result`·`legacy_result`'),
        Column('execution_status', VARCHAR(40), nullable=False, comment='`completed`·`failed`·`unsupported`·`imported`'),
        Column('verdict', VARCHAR(40), nullable=True, comment='완료된 서비스 판정 `pass`·`fail`·`review`'),
        Column('source_status', Text, nullable=True, comment='외부 항목이 보고한 status/verdict 원문'),
        Column('observed_value', JSONB(none_as_null=True), nullable=True, comment='실제 관찰값. 현재 observed_text는 JSON 문자열로 보존'),
        Column('reason', Text, nullable=True, comment='실제 판정·실패·미지원 사유'),
        Column('method_code', VARCHAR(100), nullable=True, comment='실제 수행·보고된 검사 방법'),
        Column('checker_version', VARCHAR(100), nullable=True, comment='실제 검사기 코드·규칙 버전'),
        Column('input_manifest_hash', VARCHAR(64), nullable=True, comment='부모 실행의 고정 입력 지문'),
        Column('limitations_jsonb', JSONB(none_as_null=True), nullable=True, comment='검사 범위·근거 부족·미지원 한계'),
        Column('next_action_jsonb', JSONB(none_as_null=True), nullable=True, comment='실제 제안된 후속 확인'),
        Column('error_jsonb', JSONB(none_as_null=True), nullable=True, comment='항목 처리 실패·미지원의 안전한 상세'),
        Column('source_result_jsonb', JSONB(none_as_null=True), nullable=True, comment='수신 항목 결과 원문·누락/JSON null·원래 근거'),
        Column('created_at', TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='결과 등록 시각'),
        PrimaryKeyConstraint('result_id', name="pk_item_results"),
        UniqueConstraint('attempt_id', 'criteria_item_id', name="uq_item_results_attempt_item"),
        UniqueConstraint('project_id', 'asset_version_id', 'result_id', name="uq_item_results_project_asset_result"),
        UniqueConstraint('project_id', 'asset_version_id', 'criteria_version_id', 'run_id', 'criteria_item_id', 'result_id', name="uq_item_results_selection_context"),
        CheckConstraint("(input_manifest_hash IS NULL OR input_manifest_hash ~ '^[0-9a-f]{64}$')", name="ck_item_results_hashes"),
        CheckConstraint('(created_at IS NULL OR isfinite(created_at))', name="ck_item_results_times"),
        CheckConstraint("(error_jsonb IS NULL OR jsonb_typeof(error_jsonb) = 'object') AND (source_result_jsonb IS NULL OR jsonb_typeof(source_result_jsonb) = 'object') AND (limitations_jsonb IS NULL OR jsonb_typeof(limitations_jsonb) IN ('object','array')) AND (next_action_jsonb IS NULL OR jsonb_typeof(next_action_jsonb) IN ('object','array'))", name="ck_item_results_json_shapes"),
        CheckConstraint("""(
           (result_origin = 'service_execution' AND reason IS NOT NULL AND reason ~ '[^[:space:]]'
            AND checker_version IS NOT NULL AND checker_version ~ '[^[:space:]]' AND input_manifest_hash IS NOT NULL
            AND ((execution_status = 'completed' AND verdict IS NOT NULL AND verdict IN ('pass','fail','review'))
                 OR (execution_status IN ('failed','unsupported') AND verdict IS NULL)))
           OR
           (result_origin IN ('imported_result','legacy_result') AND execution_status = 'imported'
            AND verdict IS NULL AND source_result_jsonb IS NOT NULL)
         )""", name="ck_item_results_domain"),
        comment='시도별 기준 항목 결과', schema="public",
    )

    op.create_table(
        "run_item_selections",
        Column('run_id', UUID(as_uuid=True), nullable=False, comment='소속 실행'),
        Column('criteria_item_id', UUID(as_uuid=True), nullable=False, comment='채택할 불변 기준 항목'),
        Column('result_id', UUID(as_uuid=True), nullable=False, comment='같은 실행·항목에서 고른 결과'),
        Column('project_id', UUID(as_uuid=True), nullable=False, comment='공통 프로젝트'),
        Column('asset_version_id', UUID(as_uuid=True), nullable=False, comment='공통 이미지 버전'),
        Column('criteria_version_id', UUID(as_uuid=True), nullable=False, comment='공통 기준 버전'),
        Column('selection_kind', VARCHAR(40), nullable=False, comment='`pipeline_final`·`import_projection`'),
        Column('selection_revision', BigInteger, nullable=False, server_default=text('1'), comment='채택 연결 revision. 첫 단일 실행에서는 1'),
        Column('selected_at', TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='로컬 채택·원문 투영 연결 시각'),
        PrimaryKeyConstraint('run_id', 'criteria_item_id', name="pk_run_item_selections"),
        CheckConstraint('(selected_at IS NULL OR isfinite(selected_at))', name="ck_run_item_selections_times"),
        CheckConstraint("selection_kind IN ('pipeline_final','import_projection') AND selection_revision = 1", name="ck_run_item_selections_domain"),
        comment='실행의 최종 항목 채택', schema="public",
    )


def downgrade():
    raise CommandError("M005 destructive downgrade is disabled; use a reviewed restore or recreate an isolated test DB.")
