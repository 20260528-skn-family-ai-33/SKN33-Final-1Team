"""M002 files: frozen explicit DDL; no file IO, mutable schema import, FK or seed."""
from alembic import op
from alembic.util import CommandError
from sqlalchemy import BigInteger, CheckConstraint, Column, Integer, PrimaryKeyConstraint, Text, UniqueConstraint, VARCHAR, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

revision = "M002"
down_revision = "M001"
branch_labels = None
depends_on = None


def upgrade():
    # Duplicate target objects cause transactional failure; never overwrite or adopt them.

    op.create_table(
        "assets",
        Column("asset_id", UUID(as_uuid=True), nullable=False, comment="자료 ID"),
        Column("project_id", UUID(as_uuid=True), nullable=False, comment="프로젝트 ID"),
        Column("parent_asset_id", UUID(as_uuid=True), nullable=True, comment="상위 자료 ID"),
        Column("asset_kind", VARCHAR(50), nullable=False, server_default=text("'poster_image'"), comment="자료 종류"),
        Column("name", VARCHAR(200), nullable=False, comment="자료명"),
        Column("created_by", UUID(as_uuid=True), nullable=False, comment="등록 사용자 ID"),
        Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="등록 시각"),
        Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="수정 시각"),
        PrimaryKeyConstraint("asset_id", name="pk_assets"),
        UniqueConstraint("project_id", "asset_id", name="uq_assets_project_asset"),
        CheckConstraint("isfinite(created_at) AND isfinite(updated_at) AND updated_at >= created_at", name="ck_assets_times"),
        CheckConstraint("asset_kind = 'poster_image' AND name ~ '[^[:space:]]' AND (parent_asset_id IS NULL OR parent_asset_id <> asset_id)", name="ck_assets_domain"),
        comment="자료", schema="public",
    )

    op.create_table(
        "asset_versions",
        Column("asset_version_id", UUID(as_uuid=True), nullable=False, comment="자료 버전 ID"),
        Column("project_id", UUID(as_uuid=True), nullable=False, comment="프로젝트 ID"),
        Column("asset_id", UUID(as_uuid=True), nullable=False, comment="자료 ID"),
        Column("version_no", Integer, nullable=False, comment="자료 안의 버전 번호"),
        Column("original_file_id", UUID(as_uuid=True), nullable=False, comment="원본 파일 ID"),
        Column("display_file_id", UUID(as_uuid=True), nullable=False, comment="표시 파일 ID"),
        Column("original_sha256", VARCHAR(64), nullable=False, comment="원본 바이트 SHA256"),
        Column("original_width", Integer, nullable=False, comment="원본 가로 픽셀"),
        Column("original_height", Integer, nullable=False, comment="원본 세로 픽셀"),
        Column("display_width", Integer, nullable=False, comment="표시 가로 픽셀"),
        Column("display_height", Integer, nullable=False, comment="표시 세로 픽셀"),
        Column("verification_state", VARCHAR(40), nullable=False, server_default=text("'unverified'"), comment="원본 확인 상태"),
        Column("measurement_metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"), comment="형식 치수 및 정규화 측정"),
        Column("created_by", UUID(as_uuid=True), nullable=False, comment="등록 사용자 ID"),
        Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="생성 시각"),
        PrimaryKeyConstraint("asset_version_id", name="pk_asset_versions"),
        UniqueConstraint("asset_id", "version_no", name="uq_asset_versions_asset_version"),
        UniqueConstraint("project_id", "asset_version_id", name="uq_asset_versions_project_version"),
        CheckConstraint("original_sha256 ~ '^[0-9a-f]{64}$'", name="ck_asset_versions_hashes"),
        CheckConstraint("isfinite(created_at)", name="ck_asset_versions_times"),
        CheckConstraint("jsonb_typeof(measurement_metadata) = 'object'", name="ck_asset_versions_json_shapes"),
        CheckConstraint("version_no >= 1 AND original_width > 0 AND original_height > 0 AND display_width > 0 AND display_height > 0 AND verification_state = 'unverified'", name="ck_asset_versions_domain"),
        comment="자료 버전", schema="public",
    )

    op.create_table(
        "stored_files",
        Column("file_id", UUID(as_uuid=True), nullable=False, comment="파일 ID"),
        Column("project_id", UUID(as_uuid=True), nullable=False, comment="프로젝트 ID"),
        Column("parent_file_id", UUID(as_uuid=True), nullable=True, comment="원천 파일 ID"),
        Column("import_package_id", UUID(as_uuid=True), nullable=True, comment="인계 묶음 ID"),
        Column("file_role", VARCHAR(50), nullable=False, comment="파일 역할"),
        Column("original_name", Text, nullable=True, comment="클라이언트 원래 이름"),
        Column("storage_backend", VARCHAR(20), nullable=False, server_default=text("'local'"), comment="저장소 종류"),
        Column("storage_key", VARCHAR(512), nullable=False, comment="서버 발급 저장소 상대 키"),
        Column("detected_mime", VARCHAR(100), nullable=True, comment="내용으로 확인한 MIME"),
        Column("byte_size", BigInteger, nullable=False, comment="실제 바이트 수"),
        Column("sha256", VARCHAR(64), nullable=False, comment="실제 바이트 SHA256"),
        Column("state", VARCHAR(40), nullable=False, server_default=text("'stored'"), comment="파일 보존 상태"),
        Column("source_metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"), comment="출처 및 변환 정보"),
        Column("created_by", UUID(as_uuid=True), nullable=True, comment="등록 사용자 ID"),
        Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="등록 시각"),
        Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="상태 변경 시각"),
        PrimaryKeyConstraint("file_id", name="pk_stored_files"),
        UniqueConstraint("project_id", "file_id", name="uq_stored_files_project_file"),
        UniqueConstraint("storage_backend", "storage_key", name="uq_stored_files_storage_location"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_stored_files_hashes"),
        CheckConstraint("isfinite(created_at) AND isfinite(updated_at) AND updated_at >= created_at", name="ck_stored_files_times"),
        CheckConstraint("jsonb_typeof(source_metadata) = 'object'", name="ck_stored_files_json_shapes"),
        CheckConstraint("""storage_backend = 'local' AND storage_key ~ '[^[:space:]]'
            AND file_role IN ('original_image','display_image','ocr_input','source_json',
                              'import_manifest','schema_specification','validation_schema')
            AND state IN ('stored','missing') AND byte_size >= 0
            AND (parent_file_id IS NULL OR parent_file_id <> file_id)
            AND (file_role NOT IN ('original_image','display_image','ocr_input')
                 OR (byte_size > 0 AND detected_mime IS NOT NULL AND detected_mime ~ '[^[:space:]]'))""", name="ck_stored_files_domain"),
        comment="저장 파일", schema="public",
    )

    op.create_table(
        "image_coordinate_frames",
        Column("frame_id", UUID(as_uuid=True), nullable=False, comment="좌표계 ID"),
        Column("project_id", UUID(as_uuid=True), nullable=False, comment="프로젝트 ID"),
        Column("asset_version_id", UUID(as_uuid=True), nullable=False, comment="자료 버전 ID"),
        Column("file_id", UUID(as_uuid=True), nullable=False, comment="기준 파일 ID"),
        Column("parent_frame_id", UUID(as_uuid=True), nullable=True, comment="상위 프레임 ID"),
        Column("frame_kind", VARCHAR(50), nullable=False, comment="프레임 종류"),
        Column("coordinate_space", VARCHAR(100), nullable=False, comment="입출력 좌표 기준"),
        Column("width", Integer, nullable=False, comment="가로 픽셀"),
        Column("height", Integer, nullable=False, comment="세로 픽셀"),
        Column("verification_state", VARCHAR(40), nullable=False, server_default=text("'unverified'"), comment="좌표 대응 확인 상태"),
        Column("transform_jsonb", JSONB(none_as_null=True), nullable=True, comment="상위 좌표로의 변환 정보"),
        Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="생성 시각"),
        PrimaryKeyConstraint("frame_id", name="pk_image_coordinate_frames"),
        UniqueConstraint("project_id", "asset_version_id", "frame_id", name="uq_image_coordinate_frames_project_asset_frame"),
        CheckConstraint("isfinite(created_at)", name="ck_image_coordinate_frames_times"),
        CheckConstraint("transform_jsonb IS NULL OR jsonb_typeof(transform_jsonb) = 'object'", name="ck_image_coordinate_frames_json_shapes"),
        CheckConstraint("""frame_kind IN ('original','display','ocr_input','crop') AND coordinate_space ~ '[^[:space:]]'
            AND width > 0 AND height > 0 AND verification_state = 'unverified'
            AND (parent_frame_id IS NULL OR parent_frame_id <> frame_id)
            AND ((parent_frame_id IS NULL AND transform_jsonb IS NULL)
                 OR (parent_frame_id IS NOT NULL AND transform_jsonb IS NOT NULL))""", name="ck_image_coordinate_frames_domain"),
        comment="이미지 좌표계", schema="public",
    )

    op.create_table(
        "record_aliases",
        Column("alias_id", UUID(as_uuid=True), nullable=False, comment="별칭 ID"),
        Column("project_id", UUID(as_uuid=True), nullable=False, comment="프로젝트 ID"),
        Column("asset_version_id", UUID(as_uuid=True), nullable=False, comment="자료 버전 ID"),
        Column("import_package_id", UUID(as_uuid=True), nullable=True, comment="인계 묶음 ID"),
        Column("namespace", VARCHAR(100), nullable=False, comment="식별자 발급 출처"),
        Column("external_record_id", VARCHAR(200), nullable=False, comment="외부 레코드 ID 원문"),
        Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="대응 등록 시각"),
        PrimaryKeyConstraint("alias_id", name="pk_record_aliases"),
        UniqueConstraint("project_id", "import_package_id", "namespace", "external_record_id", name="uq_record_aliases_external_record", postgresql_nulls_not_distinct=True),
        CheckConstraint("isfinite(created_at)", name="ck_record_aliases_times"),
        CheckConstraint("namespace ~ '[^[:space:]]' AND external_record_id ~ '[^[:space:]]'", name="ck_record_aliases_domain"),
        comment="외부 식별 별칭", schema="public",
    )


def downgrade():
    raise CommandError("M002 destructive downgrade is disabled; use a reviewed restore or recreate an isolated test DB.")
