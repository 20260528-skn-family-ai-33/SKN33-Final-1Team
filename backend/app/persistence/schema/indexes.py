"""C07a index definitions; register explicitly on a copied metadata for preview.

M007 is still a draft until C07b adds guards. Importing this module never
registers indexes on the current M006 metadata, connects, or executes DDL.
"""
from sqlalchemy import Index, text


def register_indexes(target_metadata):
    """Register the reviewed 27 indexes once on an explicitly supplied metadata."""
    return (
        Index(
            'uq_users_developer_kind', target_metadata.tables["public.users"].c.account_kind,
            unique=True, postgresql_where=text("account_kind = 'developer'"),
        ),
        Index(
            'ix_user_sessions_user_expiry', target_metadata.tables["public.user_sessions"].c.user_id, target_metadata.tables["public.user_sessions"].c.expires_at,
            unique=False,
        ),
        Index(
            'ix_project_members_user_projects', target_metadata.tables["public.project_members"].c.user_id, target_metadata.tables["public.project_members"].c.is_active, target_metadata.tables["public.project_members"].c.project_id,
            unique=False,
        ),
        Index(
            'ix_assets_project_recent', target_metadata.tables["public.assets"].c.project_id, target_metadata.tables["public.assets"].c.created_at.desc(), target_metadata.tables["public.assets"].c.asset_id.desc(),
            unique=False,
        ),
        Index(
            'ix_assets_parent', target_metadata.tables["public.assets"].c.project_id, target_metadata.tables["public.assets"].c.parent_asset_id,
            unique=False, postgresql_where=text('parent_asset_id IS NOT NULL'),
        ),
        Index(
            'ix_stored_files_parent', target_metadata.tables["public.stored_files"].c.project_id, target_metadata.tables["public.stored_files"].c.parent_file_id,
            unique=False, postgresql_where=text('parent_file_id IS NOT NULL'),
        ),
        Index(
            'ix_stored_files_import', target_metadata.tables["public.stored_files"].c.project_id, target_metadata.tables["public.stored_files"].c.import_package_id,
            unique=False, postgresql_where=text('import_package_id IS NOT NULL'),
        ),
        Index(
            'ix_image_coordinate_frames_parent', target_metadata.tables["public.image_coordinate_frames"].c.project_id, target_metadata.tables["public.image_coordinate_frames"].c.asset_version_id, target_metadata.tables["public.image_coordinate_frames"].c.parent_frame_id,
            unique=False, postgresql_where=text('parent_frame_id IS NOT NULL'),
        ),
        Index(
            'ix_record_aliases_asset', target_metadata.tables["public.record_aliases"].c.project_id, target_metadata.tables["public.record_aliases"].c.asset_version_id,
            unique=False,
        ),
        Index(
            'ix_import_packages_project_recent', target_metadata.tables["public.import_packages"].c.project_id, target_metadata.tables["public.import_packages"].c.imported_at.desc(), target_metadata.tables["public.import_packages"].c.import_id.desc(),
            unique=False,
        ),
        Index(
            'ix_audit_events_project_recent', target_metadata.tables["public.audit_events"].c.project_id, target_metadata.tables["public.audit_events"].c.occurred_at.desc(), target_metadata.tables["public.audit_events"].c.event_id.desc(),
            unique=False,
        ),
        Index(
            'ix_audit_events_target', target_metadata.tables["public.audit_events"].c.project_id, target_metadata.tables["public.audit_events"].c.target_type, target_metadata.tables["public.audit_events"].c.target_id, target_metadata.tables["public.audit_events"].c.occurred_at.desc(),
            unique=False,
        ),
        Index(
            'ix_ocr_runs_attempt', target_metadata.tables["public.ocr_runs"].c.project_id, target_metadata.tables["public.ocr_runs"].c.asset_version_id, target_metadata.tables["public.ocr_runs"].c.attempt_id,
            unique=False, postgresql_where=text('attempt_id IS NOT NULL'),
        ),
        Index(
            'ix_ocr_runs_asset_recent', target_metadata.tables["public.ocr_runs"].c.project_id, target_metadata.tables["public.ocr_runs"].c.asset_version_id, target_metadata.tables["public.ocr_runs"].c.created_at.desc(), target_metadata.tables["public.ocr_runs"].c.ocr_run_id.desc(),
            unique=False,
        ),
        Index(
            'ix_evidence_records_observation', target_metadata.tables["public.evidence_records"].c.project_id, target_metadata.tables["public.evidence_records"].c.asset_version_id, target_metadata.tables["public.evidence_records"].c.observation_id,
            unique=False, postgresql_where=text('observation_id IS NOT NULL'),
        ),
        Index(
            'ix_evidence_records_result', target_metadata.tables["public.evidence_records"].c.project_id, target_metadata.tables["public.evidence_records"].c.asset_version_id, target_metadata.tables["public.evidence_records"].c.item_result_id,
            unique=False, postgresql_where=text('item_result_id IS NOT NULL'),
        ),
        Index(
            'ix_evidence_ocr_tokens_token', target_metadata.tables["public.evidence_ocr_tokens"].c.token_id, target_metadata.tables["public.evidence_ocr_tokens"].c.evidence_id,
            unique=False,
        ),
        Index(
            'ix_criteria_sets_project_recent', target_metadata.tables["public.criteria_sets"].c.project_id, target_metadata.tables["public.criteria_sets"].c.created_at.desc(), target_metadata.tables["public.criteria_sets"].c.criteria_set_id.desc(),
            unique=False,
        ),
        Index(
            'ix_criteria_drafts_target_recent', target_metadata.tables["public.criteria_drafts"].c.project_id, target_metadata.tables["public.criteria_drafts"].c.criteria_set_id, target_metadata.tables["public.criteria_drafts"].c.target_asset_version_id, target_metadata.tables["public.criteria_drafts"].c.updated_at.desc(), target_metadata.tables["public.criteria_drafts"].c.draft_id.desc(),
            unique=False,
        ),
        Index(
            'ix_criteria_versions_set_version', target_metadata.tables["public.criteria_versions"].c.project_id, target_metadata.tables["public.criteria_versions"].c.criteria_set_id, target_metadata.tables["public.criteria_versions"].c.version_no.desc(),
            unique=False,
        ),
        Index(
            'ix_criteria_versions_draft', target_metadata.tables["public.criteria_versions"].c.project_id, target_metadata.tables["public.criteria_versions"].c.criteria_set_id, target_metadata.tables["public.criteria_versions"].c.target_asset_version_id, target_metadata.tables["public.criteria_versions"].c.source_draft_id,
            unique=False, postgresql_where=text('source_draft_id IS NOT NULL'),
        ),
        Index(
            'ix_review_runs_project_recent', target_metadata.tables["public.review_runs"].c.project_id, target_metadata.tables["public.review_runs"].c.created_at.desc(), target_metadata.tables["public.review_runs"].c.run_id.desc(),
            unique=False,
        ),
        Index(
            'ix_review_runs_project_status', target_metadata.tables["public.review_runs"].c.project_id, target_metadata.tables["public.review_runs"].c.execution_status, target_metadata.tables["public.review_runs"].c.created_at.desc(), target_metadata.tables["public.review_runs"].c.run_id.desc(),
            unique=False,
        ),
        Index(
            'ix_review_runs_queue', target_metadata.tables["public.review_runs"].c.available_at, target_metadata.tables["public.review_runs"].c.created_at, target_metadata.tables["public.review_runs"].c.run_id,
            unique=False, postgresql_where=text("origin_kind = 'service_execution' AND executor_kind = 'postgres_worker' AND execution_status = 'queued'"),
        ),
        Index(
            'ix_review_runs_retry_source', target_metadata.tables["public.review_runs"].c.project_id, target_metadata.tables["public.review_runs"].c.asset_version_id, target_metadata.tables["public.review_runs"].c.criteria_version_id, target_metadata.tables["public.review_runs"].c.retry_of_run_id,
            unique=False, postgresql_where=text('retry_of_run_id IS NOT NULL'),
        ),
        Index(
            'uq_review_attempts_active_run', target_metadata.tables["public.review_attempts"].c.run_id,
            unique=True, postgresql_where=text("execution_status = 'active'"),
        ),
        Index(
            'ix_review_attempts_lease', target_metadata.tables["public.review_attempts"].c.lease_expires_at, target_metadata.tables["public.review_attempts"].c.attempt_id,
            unique=False, postgresql_where=text("execution_status = 'active' AND lease_expires_at IS NOT NULL"),
        ),
    )
