"""M004 criteria: frozen explicit DDL; no live metadata import, FK, API or seed."""
from alembic import op
from alembic.util import CommandError
from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, Integer, PrimaryKeyConstraint, Text, UniqueConstraint, VARCHAR, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

revision = "M004"
down_revision = "M003"
branch_labels = None
depends_on = None


def upgrade():
    # Existing target objects fail atomically; never overwrite or adopt them.

    op.create_table(
        "criteria_sets",
        Column('criteria_set_id', UUID(as_uuid=True), nullable=False, comment='기준 묶음 PK'),
        Column('project_id', UUID(as_uuid=True), nullable=False, comment='소속 프로젝트'),
        Column('name', VARCHAR(200), nullable=False, comment='기준 묶음 표시명'),
        Column('created_by', UUID(as_uuid=True), nullable=True, comment='실제 확인 가능한 등록 사용자'),
        Column('created_at', TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='로컬 등록 시각'),
        Column('updated_at', TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='표시명 변경 시각'),
        PrimaryKeyConstraint('criteria_set_id', name="pk_criteria_sets"),
        UniqueConstraint('project_id', 'criteria_set_id', name="uq_criteria_sets_project_set"),
        CheckConstraint('isfinite(created_at) AND isfinite(updated_at) AND updated_at >= created_at', name="ck_criteria_sets_times"),
        CheckConstraint("name ~ '[^[:space:]]'", name="ck_criteria_sets_domain"),
        comment='검수 기준 묶음', schema="public",
    )

    op.create_table(
        "criteria_drafts",
        Column('draft_id', UUID(as_uuid=True), nullable=False, comment='초안 PK'),
        Column('project_id', UUID(as_uuid=True), nullable=False, comment='소속 프로젝트'),
        Column('criteria_set_id', UUID(as_uuid=True), nullable=False, comment='기준 묶음'),
        Column('target_asset_version_id', UUID(as_uuid=True), nullable=False, comment='확정 대상으로 고정할 이미지 버전'),
        Column('revision', BigInteger, nullable=False, server_default=text('1'), comment='초안 수정 번호'),
        Column('raw_requirements', Text, nullable=False, comment='편집 중 요구사항 원문'),
        Column('items_jsonb', JSONB(none_as_null=True), nullable=False, comment='순서·임시 ID·문구·종류·출처·선택 상태를 포함한 편집 배열'),
        Column('source_documents_jsonb', JSONB(none_as_null=True), nullable=True, comment='실제 제공된 출처 문서·위치 메타데이터'),
        Column('state', VARCHAR(40), nullable=False, server_default=text("'editing'"), comment='`editing`·`archived`'),
        Column('updated_by', UUID(as_uuid=True), nullable=False, comment='마지막 편집 사용자'),
        Column('created_at', TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='초안 생성 시각'),
        Column('updated_at', TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='최종 수정 시각'),
        PrimaryKeyConstraint('draft_id', name="pk_criteria_drafts"),
        UniqueConstraint('project_id', 'criteria_set_id', 'target_asset_version_id', 'draft_id', name="uq_criteria_drafts_project_set_asset_draft"),
        CheckConstraint('isfinite(created_at) AND isfinite(updated_at) AND updated_at >= created_at', name="ck_criteria_drafts_times"),
        CheckConstraint("jsonb_typeof(items_jsonb) = 'array' AND (source_documents_jsonb IS NULL OR jsonb_typeof(source_documents_jsonb) = 'array')", name="ck_criteria_drafts_json_shapes"),
        CheckConstraint("revision >= 1 AND state IN ('editing','archived')", name="ck_criteria_drafts_domain"),
        comment='편집 초안', schema="public",
    )

    op.create_table(
        "criteria_versions",
        Column('criteria_version_id', UUID(as_uuid=True), nullable=False, comment='기준 버전 PK'),
        Column('project_id', UUID(as_uuid=True), nullable=False, comment='소속 프로젝트·버전 발급 단위'),
        Column('criteria_set_id', UUID(as_uuid=True), nullable=False, comment='소속 기준 묶음'),
        Column('target_asset_version_id', UUID(as_uuid=True), nullable=False, comment='검수 대상 이미지 버전'),
        Column('source_draft_id', UUID(as_uuid=True), nullable=True, comment='서비스 확정에 사용한 초안'),
        Column('source_criteria_version_id', UUID(as_uuid=True), nullable=True, comment='내용을 복사한 이전 기준 버전'),
        Column('import_package_id', UUID(as_uuid=True), nullable=True, comment='외부 인계 묶음'),
        Column('version_no', Integer, nullable=False, comment='서버가 발급하는 프로젝트별 증가 번호'),
        Column('source_draft_revision', BigInteger, nullable=True, comment='확정 당시 초안 수정 번호'),
        Column('source_draft_hash', VARCHAR(64), nullable=True, comment='확정 당시 전체 초안 내용의 SHA256'),
        Column('source_requirements_text', Text, nullable=True, comment='확정 당시 요구사항 원문 복사본'),
        Column('source_documents_jsonb', JSONB(none_as_null=True), nullable=True, comment='확정 당시 출처 메타데이터 복사본'),
        Column('criteria_hash', VARCHAR(64), nullable=False, comment='저장할 기준 스냅샷의 SHA256'),
        Column('hash_version', VARCHAR(40), nullable=False, comment='초안·기준 해시의 직렬화 규격 버전'),
        Column('origin_kind', VARCHAR(40), nullable=False, comment='`service_confirmation`·`imported`·`legacy_snapshot`'),
        Column('confirmation_state', VARCHAR(40), nullable=False, comment='로컬 확정 상태 `confirmed`·`unconfirmed`'),
        Column('source_confirmation_state', Text, nullable=True, comment='출처가 보고한 확정 상태 원문'),
        Column('confirmed_by', UUID(as_uuid=True), nullable=True, comment='로컬 서버 확정 사용자'),
        Column('confirmed_at', TIMESTAMP(timezone=True), nullable=True, comment='로컬 서버 확정 시각'),
        Column('source_version_jsonb', JSONB(none_as_null=True), nullable=True, comment='원래 브라우저 정수·외부 버전 값과 타입'),
        Column('source_snapshot_jsonb', JSONB(none_as_null=True), nullable=True, comment='수신 당시 기준 스냅샷. 누락 필드·JSON null·출처 상태 보존'),
        Column('source_file_id', UUID(as_uuid=True), nullable=True, comment='실제 보존한 원문 JSON 파일'),
        Column('confirmation_key', UUID(as_uuid=True), nullable=True, comment='서비스 기준 확정 요청의 멱등키'),
        Column('confirmation_request_hash', VARCHAR(64), nullable=True, comment='사용자·프로젝트·초안 revision·입력을 포함한 요청 지문'),
        Column('request_hash_version', VARCHAR(40), nullable=True, comment='요청 지문 정규화 규격 버전'),
        Column('sealed_at', TIMESTAMP(timezone=True), nullable=True, comment='항목 전체를 동결한 시각. 생성 트랜잭션 종료 시 필수'),
        Column('created_at', TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='로컬 버전 등록 시각'),
        PrimaryKeyConstraint('criteria_version_id', name="pk_criteria_versions"),
        UniqueConstraint('project_id', 'version_no', name="uq_criteria_versions_project_version"),
        UniqueConstraint('project_id', 'criteria_version_id', name="uq_criteria_versions_project_id"),
        UniqueConstraint('project_id', 'target_asset_version_id', 'criteria_version_id', name="uq_criteria_versions_project_asset_version"),
        UniqueConstraint('confirmed_by', 'confirmation_key', name="uq_criteria_versions_confirm_request"),
        CheckConstraint("""criteria_hash ~ '^[0-9a-f]{64}$'
         AND (source_draft_hash IS NULL OR source_draft_hash ~ '^[0-9a-f]{64}$')
         AND (confirmation_request_hash IS NULL OR confirmation_request_hash ~ '^[0-9a-f]{64}$')""", name="ck_criteria_versions_hashes"),
        CheckConstraint("""isfinite(created_at) AND (confirmed_at IS NULL OR isfinite(confirmed_at))
         AND (sealed_at IS NULL OR isfinite(sealed_at))""", name="ck_criteria_versions_times"),
        CheckConstraint("(source_documents_jsonb IS NULL OR jsonb_typeof(source_documents_jsonb) = 'array') AND (source_snapshot_jsonb IS NULL OR jsonb_typeof(source_snapshot_jsonb) = 'object')", name="ck_criteria_versions_json_shapes"),
        CheckConstraint("""version_no >= 1 AND (source_draft_revision IS NULL OR source_draft_revision >= 1)
         AND hash_version ~ '[^[:space:]]'
         AND (source_criteria_version_id IS NULL OR source_criteria_version_id <> criteria_version_id)
         AND (
             (origin_kind = 'service_confirmation' AND confirmation_state = 'confirmed'
              AND source_draft_id IS NOT NULL AND source_draft_revision IS NOT NULL AND source_draft_hash IS NOT NULL
              AND source_requirements_text IS NOT NULL AND source_requirements_text ~ '[^[:space:]]'
              AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL AND confirmation_key IS NOT NULL
              AND confirmation_request_hash IS NOT NULL AND request_hash_version IS NOT NULL
              AND request_hash_version ~ '[^[:space:]]')
             OR
             (origin_kind IN ('imported','legacy_snapshot') AND confirmation_state = 'unconfirmed'
              AND source_snapshot_jsonb IS NOT NULL
              AND confirmed_by IS NULL AND confirmed_at IS NULL AND confirmation_key IS NULL
              AND confirmation_request_hash IS NULL AND request_hash_version IS NULL
              AND ((origin_kind = 'imported' AND import_package_id IS NOT NULL)
                   OR (origin_kind = 'legacy_snapshot' AND source_version_jsonb IS NOT NULL)))
         )""", name="ck_criteria_versions_domain"),
        comment='불변 기준 버전', schema="public",
    )

    op.create_table(
        "criteria_items",
        Column('criteria_item_id', UUID(as_uuid=True), nullable=False, comment='불변 기준 항목 PK'),
        Column('project_id', UUID(as_uuid=True), nullable=False, comment='소속 프로젝트'),
        Column('target_asset_version_id', UUID(as_uuid=True), nullable=False, comment='부모 버전과 같은 검수 대상 이미지'),
        Column('criteria_version_id', UUID(as_uuid=True), nullable=False, comment='소속 불변 기준 버전'),
        Column('external_item_id', Text, nullable=True, comment='브라우저·외부 원문의 항목 문자열 ID'),
        Column('position', Integer, nullable=False, comment='선택 항목만 복사한 1부터의 순서'),
        Column('category', VARCHAR(50), nullable=False, comment='`information`·`visual`·수입 분류 미확인 `unknown`'),
        Column('rule_code', VARCHAR(100), nullable=True, comment='실제 지정된 검사 규칙 코드'),
        Column('field_code', VARCHAR(100), nullable=True, comment='실제 지정된 정보 필드 코드'),
        Column('text', Text, nullable=False, comment='사용자가 확정하거나 원문에서 수신한 기준 문구'),
        Column('source_text', Text, nullable=True, comment='현재 API의 `source` 등 출처 설명 복사본'),
        Column('expected_value', JSONB(none_as_null=True), nullable=True, comment='원문에서 명시한 구조화 기대값'),
        Column('is_required', Boolean, nullable=True, comment='명시된 필수 조건 여부. 미제공은 NULL'),
        Column('source_asset_version_id', UUID(as_uuid=True), nullable=True, comment='실제 등록된 출처 자료 버전'),
        Column('source_locator', JSONB(none_as_null=True), nullable=True, comment='확인한 페이지·필드 경로·위치'),
        Column('applicability_jsonb', JSONB(none_as_null=True), nullable=True, comment='실제 지정된 적용 조건'),
        Column('check_scope_jsonb', JSONB(none_as_null=True), nullable=True, comment='실제 지정된 검사 범위'),
        Column('source_item_jsonb', JSONB(none_as_null=True), nullable=True, comment='수신 항목 원문. 필드 누락·명시적 null·외부 분류 보존'),
        PrimaryKeyConstraint('criteria_item_id', name="pk_criteria_items"),
        UniqueConstraint('criteria_version_id', 'position', name="uq_criteria_items_position"),
        UniqueConstraint('criteria_version_id', 'external_item_id', name="uq_criteria_items_external_id"),
        UniqueConstraint('project_id', 'target_asset_version_id', 'criteria_version_id', 'criteria_item_id', name="uq_criteria_items_project_asset_version_item"),
        CheckConstraint("(source_locator IS NULL OR jsonb_typeof(source_locator) = 'object') AND (applicability_jsonb IS NULL OR jsonb_typeof(applicability_jsonb) = 'object') AND (check_scope_jsonb IS NULL OR jsonb_typeof(check_scope_jsonb) = 'object') AND (source_item_jsonb IS NULL OR jsonb_typeof(source_item_jsonb) = 'object')", name="ck_criteria_items_json_shapes"),
        CheckConstraint("""position >= 1 AND category IN ('information','visual','unknown') AND text ~ '[^[:space:]]'
         AND (external_item_id IS NULL OR external_item_id ~ '[^[:space:]]')""", name="ck_criteria_items_domain"),
        comment='기준 버전의 항목', schema="public",
    )


def downgrade():
    raise CommandError("M004 destructive downgrade is disabled; use a reviewed restore or recreate an isolated test DB.")
