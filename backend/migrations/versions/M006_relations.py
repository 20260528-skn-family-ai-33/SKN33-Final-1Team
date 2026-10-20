"""M006 relations: 81 frozen, validated, immediate RESTRICT foreign keys."""
from alembic import op
from alembic.util import CommandError

revision = "M006"
down_revision = "M005"
branch_labels = None
depends_on = None


def upgrade():
    # No NOT VALID/adoption/drop: invalid existing rows abort the whole transaction.

    op.create_foreign_key(
        'fk_user_sessions_user', 'user_sessions', 'users',
        ['user_id'], ['user_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_projects_created_by', 'projects', 'users',
        ['created_by'], ['user_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_project_members_project', 'project_members', 'projects',
        ['project_id'], ['project_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_project_members_user', 'project_members', 'users',
        ['user_id'], ['user_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_assets_parent_asset', 'assets', 'assets',
        ['project_id', 'parent_asset_id'], ['project_id', 'asset_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_assets_project', 'assets', 'projects',
        ['project_id'], ['project_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_assets_created_by', 'assets', 'users',
        ['created_by'], ['user_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_asset_versions_asset', 'asset_versions', 'assets',
        ['project_id', 'asset_id'], ['project_id', 'asset_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_asset_versions_original_file', 'asset_versions', 'stored_files',
        ['project_id', 'original_file_id'], ['project_id', 'file_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_asset_versions_display_file', 'asset_versions', 'stored_files',
        ['project_id', 'display_file_id'], ['project_id', 'file_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_asset_versions_created_by', 'asset_versions', 'users',
        ['created_by'], ['user_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_stored_files_parent_file', 'stored_files', 'stored_files',
        ['project_id', 'parent_file_id'], ['project_id', 'file_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_stored_files_import_package', 'stored_files', 'import_packages',
        ['project_id', 'import_package_id'], ['project_id', 'import_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_stored_files_project', 'stored_files', 'projects',
        ['project_id'], ['project_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_stored_files_created_by', 'stored_files', 'users',
        ['created_by'], ['user_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_image_coordinate_frames_asset_version', 'image_coordinate_frames', 'asset_versions',
        ['project_id', 'asset_version_id'], ['project_id', 'asset_version_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_image_coordinate_frames_file', 'image_coordinate_frames', 'stored_files',
        ['project_id', 'file_id'], ['project_id', 'file_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_image_coordinate_frames_parent_frame', 'image_coordinate_frames', 'image_coordinate_frames',
        ['project_id', 'asset_version_id', 'parent_frame_id'], ['project_id', 'asset_version_id', 'frame_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_record_aliases_asset_version', 'record_aliases', 'asset_versions',
        ['project_id', 'asset_version_id'], ['project_id', 'asset_version_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_record_aliases_import_package', 'record_aliases', 'import_packages',
        ['project_id', 'import_package_id'], ['project_id', 'import_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_schema_definitions_specification_file', 'schema_definitions', 'stored_files',
        ['specification_file_id'], ['file_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_schema_definitions_validation_schema_file', 'schema_definitions', 'stored_files',
        ['validation_schema_file_id'], ['file_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_import_packages_manifest_file', 'import_packages', 'stored_files',
        ['project_id', 'manifest_file_id'], ['project_id', 'file_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_import_packages_replaces_import', 'import_packages', 'import_packages',
        ['project_id', 'replaces_import_id'], ['project_id', 'import_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_import_packages_project', 'import_packages', 'projects',
        ['project_id'], ['project_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_import_packages_imported_by', 'import_packages', 'users',
        ['imported_by'], ['user_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_audit_events_project', 'audit_events', 'projects',
        ['project_id'], ['project_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_audit_events_actor_user', 'audit_events', 'users',
        ['actor_user_id'], ['user_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_ocr_runs_asset_version', 'ocr_runs', 'asset_versions',
        ['project_id', 'asset_version_id'], ['project_id', 'asset_version_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_ocr_runs_attempt', 'ocr_runs', 'review_attempts',
        ['project_id', 'asset_version_id', 'attempt_id'], ['project_id', 'asset_version_id', 'attempt_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_ocr_runs_import_package', 'ocr_runs', 'import_packages',
        ['project_id', 'import_package_id'], ['project_id', 'import_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_ocr_runs_source_file', 'ocr_runs', 'stored_files',
        ['project_id', 'source_file_id'], ['project_id', 'file_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_ocr_runs_input_file', 'ocr_runs', 'stored_files',
        ['project_id', 'input_file_id'], ['project_id', 'file_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_ocr_runs_frame', 'ocr_runs', 'image_coordinate_frames',
        ['project_id', 'asset_version_id', 'frame_id'], ['project_id', 'asset_version_id', 'frame_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_ocr_runs_schema', 'ocr_runs', 'schema_definitions',
        ['schema_id'], ['schema_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_ocr_tokens_ocr_run', 'ocr_tokens', 'ocr_runs',
        ['project_id', 'asset_version_id', 'ocr_run_id'], ['project_id', 'asset_version_id', 'ocr_run_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_poster_extractions_asset_version', 'poster_extractions', 'asset_versions',
        ['project_id', 'asset_version_id'], ['project_id', 'asset_version_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_poster_extractions_ocr_run', 'poster_extractions', 'ocr_runs',
        ['project_id', 'asset_version_id', 'ocr_run_id'], ['project_id', 'asset_version_id', 'ocr_run_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_poster_extractions_import_package', 'poster_extractions', 'import_packages',
        ['project_id', 'import_package_id'], ['project_id', 'import_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_poster_extractions_predecessor', 'poster_extractions', 'poster_extractions',
        ['project_id', 'asset_version_id', 'predecessor_id'], ['project_id', 'asset_version_id', 'extraction_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_poster_extractions_source_file', 'poster_extractions', 'stored_files',
        ['project_id', 'source_file_id'], ['project_id', 'file_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_poster_extractions_schema', 'poster_extractions', 'schema_definitions',
        ['schema_id'], ['schema_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_poster_field_observations_extraction', 'poster_field_observations', 'poster_extractions',
        ['project_id', 'asset_version_id', 'extraction_id'], ['project_id', 'asset_version_id', 'extraction_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_evidence_records_asset_version', 'evidence_records', 'asset_versions',
        ['project_id', 'asset_version_id'], ['project_id', 'asset_version_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_evidence_records_observation', 'evidence_records', 'poster_field_observations',
        ['project_id', 'asset_version_id', 'observation_id'], ['project_id', 'asset_version_id', 'observation_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_evidence_records_item_result', 'evidence_records', 'item_results',
        ['project_id', 'asset_version_id', 'item_result_id'], ['project_id', 'asset_version_id', 'result_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_evidence_records_source_file', 'evidence_records', 'stored_files',
        ['project_id', 'source_file_id'], ['project_id', 'file_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_evidence_records_frame', 'evidence_records', 'image_coordinate_frames',
        ['project_id', 'asset_version_id', 'frame_id'], ['project_id', 'asset_version_id', 'frame_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_evidence_records_source_ocr_run', 'evidence_records', 'ocr_runs',
        ['project_id', 'asset_version_id', 'source_ocr_run_id'], ['project_id', 'asset_version_id', 'ocr_run_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_evidence_ocr_tokens_evidence', 'evidence_ocr_tokens', 'evidence_records',
        ['project_id', 'asset_version_id', 'ocr_run_id', 'evidence_id'], ['project_id', 'asset_version_id', 'source_ocr_run_id', 'evidence_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_evidence_ocr_tokens_token', 'evidence_ocr_tokens', 'ocr_tokens',
        ['project_id', 'asset_version_id', 'ocr_run_id', 'token_id'], ['project_id', 'asset_version_id', 'ocr_run_id', 'token_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_criteria_sets_project', 'criteria_sets', 'projects',
        ['project_id'], ['project_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_criteria_sets_created_by', 'criteria_sets', 'users',
        ['created_by'], ['user_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_criteria_drafts_criteria_set', 'criteria_drafts', 'criteria_sets',
        ['project_id', 'criteria_set_id'], ['project_id', 'criteria_set_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_criteria_drafts_target_asset_version', 'criteria_drafts', 'asset_versions',
        ['project_id', 'target_asset_version_id'], ['project_id', 'asset_version_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_criteria_drafts_updated_by', 'criteria_drafts', 'users',
        ['updated_by'], ['user_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_criteria_versions_criteria_set', 'criteria_versions', 'criteria_sets',
        ['project_id', 'criteria_set_id'], ['project_id', 'criteria_set_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_criteria_versions_target_asset_version', 'criteria_versions', 'asset_versions',
        ['project_id', 'target_asset_version_id'], ['project_id', 'asset_version_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_criteria_versions_source_draft', 'criteria_versions', 'criteria_drafts',
        ['project_id', 'criteria_set_id', 'target_asset_version_id', 'source_draft_id'], ['project_id', 'criteria_set_id', 'target_asset_version_id', 'draft_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_criteria_versions_source_criteria_version', 'criteria_versions', 'criteria_versions',
        ['project_id', 'source_criteria_version_id'], ['project_id', 'criteria_version_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_criteria_versions_import_package', 'criteria_versions', 'import_packages',
        ['project_id', 'import_package_id'], ['project_id', 'import_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_criteria_versions_source_file', 'criteria_versions', 'stored_files',
        ['project_id', 'source_file_id'], ['project_id', 'file_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_criteria_versions_confirmed_by', 'criteria_versions', 'users',
        ['confirmed_by'], ['user_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_criteria_items_criteria_version', 'criteria_items', 'criteria_versions',
        ['project_id', 'target_asset_version_id', 'criteria_version_id'], ['project_id', 'target_asset_version_id', 'criteria_version_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_criteria_items_source_asset_version', 'criteria_items', 'asset_versions',
        ['project_id', 'source_asset_version_id'], ['project_id', 'asset_version_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_review_runs_asset_version', 'review_runs', 'asset_versions',
        ['project_id', 'asset_version_id'], ['project_id', 'asset_version_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_review_runs_criteria_version', 'review_runs', 'criteria_versions',
        ['project_id', 'asset_version_id', 'criteria_version_id'], ['project_id', 'target_asset_version_id', 'criteria_version_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_review_runs_retry_of_run', 'review_runs', 'review_runs',
        ['project_id', 'asset_version_id', 'criteria_version_id', 'retry_of_run_id'], ['project_id', 'asset_version_id', 'criteria_version_id', 'run_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_review_runs_import_package', 'review_runs', 'import_packages',
        ['project_id', 'import_package_id'], ['project_id', 'import_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_review_runs_source_file', 'review_runs', 'stored_files',
        ['project_id', 'source_file_id'], ['project_id', 'file_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_review_runs_active_attempt', 'review_runs', 'review_attempts',
        ['project_id', 'asset_version_id', 'run_id', 'active_attempt_id'], ['project_id', 'asset_version_id', 'run_id', 'attempt_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_review_runs_selected_attempt', 'review_runs', 'review_attempts',
        ['project_id', 'asset_version_id', 'run_id', 'selected_attempt_id'], ['project_id', 'asset_version_id', 'run_id', 'attempt_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_review_runs_project', 'review_runs', 'projects',
        ['project_id'], ['project_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_review_runs_requested_by', 'review_runs', 'users',
        ['requested_by'], ['user_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_review_attempts_run', 'review_attempts', 'review_runs',
        ['project_id', 'asset_version_id', 'run_id'], ['project_id', 'asset_version_id', 'run_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_item_results_run', 'item_results', 'review_runs',
        ['project_id', 'asset_version_id', 'criteria_version_id', 'run_id'], ['project_id', 'asset_version_id', 'criteria_version_id', 'run_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_item_results_attempt', 'item_results', 'review_attempts',
        ['project_id', 'asset_version_id', 'run_id', 'attempt_id'], ['project_id', 'asset_version_id', 'run_id', 'attempt_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_item_results_criteria_item', 'item_results', 'criteria_items',
        ['project_id', 'asset_version_id', 'criteria_version_id', 'criteria_item_id'], ['project_id', 'target_asset_version_id', 'criteria_version_id', 'criteria_item_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_item_results_ocr_run', 'item_results', 'ocr_runs',
        ['project_id', 'asset_version_id', 'ocr_run_id'], ['project_id', 'asset_version_id', 'ocr_run_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_item_results_extraction', 'item_results', 'poster_extractions',
        ['project_id', 'asset_version_id', 'extraction_id'], ['project_id', 'asset_version_id', 'extraction_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )

    op.create_foreign_key(
        'fk_run_item_selections_result', 'run_item_selections', 'item_results',
        ['project_id', 'asset_version_id', 'criteria_version_id', 'run_id', 'criteria_item_id', 'result_id'], ['project_id', 'asset_version_id', 'criteria_version_id', 'run_id', 'criteria_item_id', 'result_id'], source_schema="public", referent_schema="public",
        ondelete="RESTRICT", onupdate="RESTRICT", deferrable=False, match="SIMPLE",
    )


def downgrade():
    raise CommandError("M006 destructive downgrade is disabled; use a reviewed restore or recreate an isolated test DB.")
