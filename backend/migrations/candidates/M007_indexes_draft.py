"""INCOMPLETE M007 INDEX DRAFT: staged test copies only, not an Alembic head.

C07b must complete guards before this revision can be promoted to versions/.
The test-only config gate prevents applying this incomplete draft by accident.
"""
from alembic import op
from alembic.util import CommandError
from sqlalchemy import text

revision = "M007"
down_revision = "M006"
branch_labels = None
depends_on = None


def upgrade():
    config = op.get_context().config
    if config is None or config.attributes.get("allow_c07a_index_draft") is not True:
        raise CommandError("Incomplete M007 index draft requires an explicit isolated C07a test configuration; complete C07b before promotion.")

    op.create_index(
        'uq_users_developer_kind', 'users', ['account_kind'],
        unique=True, schema="public", postgresql_where=text("account_kind = 'developer'"),
    )

    op.create_index(
        'ix_user_sessions_user_expiry', 'user_sessions', ['user_id', 'expires_at'],
        unique=False, schema="public",
    )

    op.create_index(
        'ix_project_members_user_projects', 'project_members', ['user_id', 'is_active', 'project_id'],
        unique=False, schema="public",
    )

    op.create_index(
        'ix_assets_project_recent', 'assets', ['project_id', text("created_at DESC"), text("asset_id DESC")],
        unique=False, schema="public",
    )

    op.create_index(
        'ix_assets_parent', 'assets', ['project_id', 'parent_asset_id'],
        unique=False, schema="public", postgresql_where=text('parent_asset_id IS NOT NULL'),
    )

    op.create_index(
        'ix_stored_files_parent', 'stored_files', ['project_id', 'parent_file_id'],
        unique=False, schema="public", postgresql_where=text('parent_file_id IS NOT NULL'),
    )

    op.create_index(
        'ix_stored_files_import', 'stored_files', ['project_id', 'import_package_id'],
        unique=False, schema="public", postgresql_where=text('import_package_id IS NOT NULL'),
    )

    op.create_index(
        'ix_image_coordinate_frames_parent', 'image_coordinate_frames', ['project_id', 'asset_version_id', 'parent_frame_id'],
        unique=False, schema="public", postgresql_where=text('parent_frame_id IS NOT NULL'),
    )

    op.create_index(
        'ix_record_aliases_asset', 'record_aliases', ['project_id', 'asset_version_id'],
        unique=False, schema="public",
    )

    op.create_index(
        'ix_import_packages_project_recent', 'import_packages', ['project_id', text("imported_at DESC"), text("import_id DESC")],
        unique=False, schema="public",
    )

    op.create_index(
        'ix_audit_events_project_recent', 'audit_events', ['project_id', text("occurred_at DESC"), text("event_id DESC")],
        unique=False, schema="public",
    )

    op.create_index(
        'ix_audit_events_target', 'audit_events', ['project_id', 'target_type', 'target_id', text("occurred_at DESC")],
        unique=False, schema="public",
    )

    op.create_index(
        'ix_ocr_runs_attempt', 'ocr_runs', ['project_id', 'asset_version_id', 'attempt_id'],
        unique=False, schema="public", postgresql_where=text('attempt_id IS NOT NULL'),
    )

    op.create_index(
        'ix_ocr_runs_asset_recent', 'ocr_runs', ['project_id', 'asset_version_id', text("created_at DESC"), text("ocr_run_id DESC")],
        unique=False, schema="public",
    )

    op.create_index(
        'ix_evidence_records_observation', 'evidence_records', ['project_id', 'asset_version_id', 'observation_id'],
        unique=False, schema="public", postgresql_where=text('observation_id IS NOT NULL'),
    )

    op.create_index(
        'ix_evidence_records_result', 'evidence_records', ['project_id', 'asset_version_id', 'item_result_id'],
        unique=False, schema="public", postgresql_where=text('item_result_id IS NOT NULL'),
    )

    op.create_index(
        'ix_evidence_ocr_tokens_token', 'evidence_ocr_tokens', ['token_id', 'evidence_id'],
        unique=False, schema="public",
    )

    op.create_index(
        'ix_criteria_sets_project_recent', 'criteria_sets', ['project_id', text("created_at DESC"), text("criteria_set_id DESC")],
        unique=False, schema="public",
    )

    op.create_index(
        'ix_criteria_drafts_target_recent', 'criteria_drafts', ['project_id', 'criteria_set_id', 'target_asset_version_id', text("updated_at DESC"), text("draft_id DESC")],
        unique=False, schema="public",
    )

    op.create_index(
        'ix_criteria_versions_set_version', 'criteria_versions', ['project_id', 'criteria_set_id', text("version_no DESC")],
        unique=False, schema="public",
    )

    op.create_index(
        'ix_criteria_versions_draft', 'criteria_versions', ['project_id', 'criteria_set_id', 'target_asset_version_id', 'source_draft_id'],
        unique=False, schema="public", postgresql_where=text('source_draft_id IS NOT NULL'),
    )

    op.create_index(
        'ix_review_runs_project_recent', 'review_runs', ['project_id', text("created_at DESC"), text("run_id DESC")],
        unique=False, schema="public",
    )

    op.create_index(
        'ix_review_runs_project_status', 'review_runs', ['project_id', 'execution_status', text("created_at DESC"), text("run_id DESC")],
        unique=False, schema="public",
    )

    op.create_index(
        'ix_review_runs_queue', 'review_runs', ['available_at', 'created_at', 'run_id'],
        unique=False, schema="public", postgresql_where=text("origin_kind = 'service_execution' AND executor_kind = 'postgres_worker' AND execution_status = 'queued'"),
    )

    op.create_index(
        'ix_review_runs_retry_source', 'review_runs', ['project_id', 'asset_version_id', 'criteria_version_id', 'retry_of_run_id'],
        unique=False, schema="public", postgresql_where=text('retry_of_run_id IS NOT NULL'),
    )

    op.create_index(
        'uq_review_attempts_active_run', 'review_attempts', ['run_id'],
        unique=True, schema="public", postgresql_where=text("execution_status = 'active'"),
    )

    op.create_index(
        'ix_review_attempts_lease', 'review_attempts', ['lease_expires_at', 'attempt_id'],
        unique=False, schema="public", postgresql_where=text("execution_status = 'active' AND lease_expires_at IS NOT NULL"),
    )


def downgrade():
    raise CommandError("M007 draft downgrade is disabled; recreate an isolated test database.")
