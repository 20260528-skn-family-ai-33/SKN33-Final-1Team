"""C06 named relationships. All tables exist before these constraints register; no IO."""
from sqlalchemy import ForeignKeyConstraint

from . import metadata

metadata.tables["public.user_sessions"].append_constraint(ForeignKeyConstraint(
    ['user_id'], ['public.users.user_id'],
    name='fk_user_sessions_user', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.projects"].append_constraint(ForeignKeyConstraint(
    ['created_by'], ['public.users.user_id'],
    name='fk_projects_created_by', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.project_members"].append_constraint(ForeignKeyConstraint(
    ['project_id'], ['public.projects.project_id'],
    name='fk_project_members_project', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.project_members"].append_constraint(ForeignKeyConstraint(
    ['user_id'], ['public.users.user_id'],
    name='fk_project_members_user', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.assets"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'parent_asset_id'], ['public.assets.project_id', 'public.assets.asset_id'],
    name='fk_assets_parent_asset', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.assets"].append_constraint(ForeignKeyConstraint(
    ['project_id'], ['public.projects.project_id'],
    name='fk_assets_project', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.assets"].append_constraint(ForeignKeyConstraint(
    ['created_by'], ['public.users.user_id'],
    name='fk_assets_created_by', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.asset_versions"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_id'], ['public.assets.project_id', 'public.assets.asset_id'],
    name='fk_asset_versions_asset', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.asset_versions"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'original_file_id'], ['public.stored_files.project_id', 'public.stored_files.file_id'],
    name='fk_asset_versions_original_file', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.asset_versions"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'display_file_id'], ['public.stored_files.project_id', 'public.stored_files.file_id'],
    name='fk_asset_versions_display_file', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.asset_versions"].append_constraint(ForeignKeyConstraint(
    ['created_by'], ['public.users.user_id'],
    name='fk_asset_versions_created_by', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.stored_files"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'parent_file_id'], ['public.stored_files.project_id', 'public.stored_files.file_id'],
    name='fk_stored_files_parent_file', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.stored_files"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'import_package_id'], ['public.import_packages.project_id', 'public.import_packages.import_id'],
    name='fk_stored_files_import_package', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.stored_files"].append_constraint(ForeignKeyConstraint(
    ['project_id'], ['public.projects.project_id'],
    name='fk_stored_files_project', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.stored_files"].append_constraint(ForeignKeyConstraint(
    ['created_by'], ['public.users.user_id'],
    name='fk_stored_files_created_by', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.image_coordinate_frames"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id'], ['public.asset_versions.project_id', 'public.asset_versions.asset_version_id'],
    name='fk_image_coordinate_frames_asset_version', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.image_coordinate_frames"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'file_id'], ['public.stored_files.project_id', 'public.stored_files.file_id'],
    name='fk_image_coordinate_frames_file', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.image_coordinate_frames"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id', 'parent_frame_id'], ['public.image_coordinate_frames.project_id', 'public.image_coordinate_frames.asset_version_id', 'public.image_coordinate_frames.frame_id'],
    name='fk_image_coordinate_frames_parent_frame', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.record_aliases"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id'], ['public.asset_versions.project_id', 'public.asset_versions.asset_version_id'],
    name='fk_record_aliases_asset_version', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.record_aliases"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'import_package_id'], ['public.import_packages.project_id', 'public.import_packages.import_id'],
    name='fk_record_aliases_import_package', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.schema_definitions"].append_constraint(ForeignKeyConstraint(
    ['specification_file_id'], ['public.stored_files.file_id'],
    name='fk_schema_definitions_specification_file', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.schema_definitions"].append_constraint(ForeignKeyConstraint(
    ['validation_schema_file_id'], ['public.stored_files.file_id'],
    name='fk_schema_definitions_validation_schema_file', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.import_packages"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'manifest_file_id'], ['public.stored_files.project_id', 'public.stored_files.file_id'],
    name='fk_import_packages_manifest_file', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.import_packages"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'replaces_import_id'], ['public.import_packages.project_id', 'public.import_packages.import_id'],
    name='fk_import_packages_replaces_import', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.import_packages"].append_constraint(ForeignKeyConstraint(
    ['project_id'], ['public.projects.project_id'],
    name='fk_import_packages_project', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.import_packages"].append_constraint(ForeignKeyConstraint(
    ['imported_by'], ['public.users.user_id'],
    name='fk_import_packages_imported_by', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.audit_events"].append_constraint(ForeignKeyConstraint(
    ['project_id'], ['public.projects.project_id'],
    name='fk_audit_events_project', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.audit_events"].append_constraint(ForeignKeyConstraint(
    ['actor_user_id'], ['public.users.user_id'],
    name='fk_audit_events_actor_user', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.ocr_runs"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id'], ['public.asset_versions.project_id', 'public.asset_versions.asset_version_id'],
    name='fk_ocr_runs_asset_version', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.ocr_runs"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id', 'attempt_id'], ['public.review_attempts.project_id', 'public.review_attempts.asset_version_id', 'public.review_attempts.attempt_id'],
    name='fk_ocr_runs_attempt', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.ocr_runs"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'import_package_id'], ['public.import_packages.project_id', 'public.import_packages.import_id'],
    name='fk_ocr_runs_import_package', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.ocr_runs"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'source_file_id'], ['public.stored_files.project_id', 'public.stored_files.file_id'],
    name='fk_ocr_runs_source_file', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.ocr_runs"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'input_file_id'], ['public.stored_files.project_id', 'public.stored_files.file_id'],
    name='fk_ocr_runs_input_file', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.ocr_runs"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id', 'frame_id'], ['public.image_coordinate_frames.project_id', 'public.image_coordinate_frames.asset_version_id', 'public.image_coordinate_frames.frame_id'],
    name='fk_ocr_runs_frame', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.ocr_runs"].append_constraint(ForeignKeyConstraint(
    ['schema_id'], ['public.schema_definitions.schema_id'],
    name='fk_ocr_runs_schema', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.ocr_tokens"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id', 'ocr_run_id'], ['public.ocr_runs.project_id', 'public.ocr_runs.asset_version_id', 'public.ocr_runs.ocr_run_id'],
    name='fk_ocr_tokens_ocr_run', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.poster_extractions"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id'], ['public.asset_versions.project_id', 'public.asset_versions.asset_version_id'],
    name='fk_poster_extractions_asset_version', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.poster_extractions"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id', 'ocr_run_id'], ['public.ocr_runs.project_id', 'public.ocr_runs.asset_version_id', 'public.ocr_runs.ocr_run_id'],
    name='fk_poster_extractions_ocr_run', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.poster_extractions"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'import_package_id'], ['public.import_packages.project_id', 'public.import_packages.import_id'],
    name='fk_poster_extractions_import_package', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.poster_extractions"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id', 'predecessor_id'], ['public.poster_extractions.project_id', 'public.poster_extractions.asset_version_id', 'public.poster_extractions.extraction_id'],
    name='fk_poster_extractions_predecessor', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.poster_extractions"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'source_file_id'], ['public.stored_files.project_id', 'public.stored_files.file_id'],
    name='fk_poster_extractions_source_file', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.poster_extractions"].append_constraint(ForeignKeyConstraint(
    ['schema_id'], ['public.schema_definitions.schema_id'],
    name='fk_poster_extractions_schema', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.poster_field_observations"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id', 'extraction_id'], ['public.poster_extractions.project_id', 'public.poster_extractions.asset_version_id', 'public.poster_extractions.extraction_id'],
    name='fk_poster_field_observations_extraction', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.evidence_records"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id'], ['public.asset_versions.project_id', 'public.asset_versions.asset_version_id'],
    name='fk_evidence_records_asset_version', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.evidence_records"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id', 'observation_id'], ['public.poster_field_observations.project_id', 'public.poster_field_observations.asset_version_id', 'public.poster_field_observations.observation_id'],
    name='fk_evidence_records_observation', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.evidence_records"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id', 'item_result_id'], ['public.item_results.project_id', 'public.item_results.asset_version_id', 'public.item_results.result_id'],
    name='fk_evidence_records_item_result', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.evidence_records"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'source_file_id'], ['public.stored_files.project_id', 'public.stored_files.file_id'],
    name='fk_evidence_records_source_file', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.evidence_records"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id', 'frame_id'], ['public.image_coordinate_frames.project_id', 'public.image_coordinate_frames.asset_version_id', 'public.image_coordinate_frames.frame_id'],
    name='fk_evidence_records_frame', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.evidence_records"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id', 'source_ocr_run_id'], ['public.ocr_runs.project_id', 'public.ocr_runs.asset_version_id', 'public.ocr_runs.ocr_run_id'],
    name='fk_evidence_records_source_ocr_run', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.evidence_ocr_tokens"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id', 'ocr_run_id', 'evidence_id'], ['public.evidence_records.project_id', 'public.evidence_records.asset_version_id', 'public.evidence_records.source_ocr_run_id', 'public.evidence_records.evidence_id'],
    name='fk_evidence_ocr_tokens_evidence', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.evidence_ocr_tokens"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id', 'ocr_run_id', 'token_id'], ['public.ocr_tokens.project_id', 'public.ocr_tokens.asset_version_id', 'public.ocr_tokens.ocr_run_id', 'public.ocr_tokens.token_id'],
    name='fk_evidence_ocr_tokens_token', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.criteria_sets"].append_constraint(ForeignKeyConstraint(
    ['project_id'], ['public.projects.project_id'],
    name='fk_criteria_sets_project', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.criteria_sets"].append_constraint(ForeignKeyConstraint(
    ['created_by'], ['public.users.user_id'],
    name='fk_criteria_sets_created_by', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.criteria_drafts"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'criteria_set_id'], ['public.criteria_sets.project_id', 'public.criteria_sets.criteria_set_id'],
    name='fk_criteria_drafts_criteria_set', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.criteria_drafts"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'target_asset_version_id'], ['public.asset_versions.project_id', 'public.asset_versions.asset_version_id'],
    name='fk_criteria_drafts_target_asset_version', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.criteria_drafts"].append_constraint(ForeignKeyConstraint(
    ['updated_by'], ['public.users.user_id'],
    name='fk_criteria_drafts_updated_by', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.criteria_versions"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'criteria_set_id'], ['public.criteria_sets.project_id', 'public.criteria_sets.criteria_set_id'],
    name='fk_criteria_versions_criteria_set', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.criteria_versions"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'target_asset_version_id'], ['public.asset_versions.project_id', 'public.asset_versions.asset_version_id'],
    name='fk_criteria_versions_target_asset_version', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.criteria_versions"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'criteria_set_id', 'target_asset_version_id', 'source_draft_id'], ['public.criteria_drafts.project_id', 'public.criteria_drafts.criteria_set_id', 'public.criteria_drafts.target_asset_version_id', 'public.criteria_drafts.draft_id'],
    name='fk_criteria_versions_source_draft', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.criteria_versions"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'source_criteria_version_id'], ['public.criteria_versions.project_id', 'public.criteria_versions.criteria_version_id'],
    name='fk_criteria_versions_source_criteria_version', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.criteria_versions"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'import_package_id'], ['public.import_packages.project_id', 'public.import_packages.import_id'],
    name='fk_criteria_versions_import_package', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.criteria_versions"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'source_file_id'], ['public.stored_files.project_id', 'public.stored_files.file_id'],
    name='fk_criteria_versions_source_file', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.criteria_versions"].append_constraint(ForeignKeyConstraint(
    ['confirmed_by'], ['public.users.user_id'],
    name='fk_criteria_versions_confirmed_by', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.criteria_items"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'target_asset_version_id', 'criteria_version_id'], ['public.criteria_versions.project_id', 'public.criteria_versions.target_asset_version_id', 'public.criteria_versions.criteria_version_id'],
    name='fk_criteria_items_criteria_version', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.criteria_items"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'source_asset_version_id'], ['public.asset_versions.project_id', 'public.asset_versions.asset_version_id'],
    name='fk_criteria_items_source_asset_version', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.review_runs"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id'], ['public.asset_versions.project_id', 'public.asset_versions.asset_version_id'],
    name='fk_review_runs_asset_version', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.review_runs"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id', 'criteria_version_id'], ['public.criteria_versions.project_id', 'public.criteria_versions.target_asset_version_id', 'public.criteria_versions.criteria_version_id'],
    name='fk_review_runs_criteria_version', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.review_runs"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id', 'criteria_version_id', 'retry_of_run_id'], ['public.review_runs.project_id', 'public.review_runs.asset_version_id', 'public.review_runs.criteria_version_id', 'public.review_runs.run_id'],
    name='fk_review_runs_retry_of_run', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.review_runs"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'import_package_id'], ['public.import_packages.project_id', 'public.import_packages.import_id'],
    name='fk_review_runs_import_package', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.review_runs"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'source_file_id'], ['public.stored_files.project_id', 'public.stored_files.file_id'],
    name='fk_review_runs_source_file', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.review_runs"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id', 'run_id', 'active_attempt_id'], ['public.review_attempts.project_id', 'public.review_attempts.asset_version_id', 'public.review_attempts.run_id', 'public.review_attempts.attempt_id'],
    name='fk_review_runs_active_attempt', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.review_runs"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id', 'run_id', 'selected_attempt_id'], ['public.review_attempts.project_id', 'public.review_attempts.asset_version_id', 'public.review_attempts.run_id', 'public.review_attempts.attempt_id'],
    name='fk_review_runs_selected_attempt', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.review_runs"].append_constraint(ForeignKeyConstraint(
    ['project_id'], ['public.projects.project_id'],
    name='fk_review_runs_project', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.review_runs"].append_constraint(ForeignKeyConstraint(
    ['requested_by'], ['public.users.user_id'],
    name='fk_review_runs_requested_by', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.review_attempts"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id', 'run_id'], ['public.review_runs.project_id', 'public.review_runs.asset_version_id', 'public.review_runs.run_id'],
    name='fk_review_attempts_run', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.item_results"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id', 'criteria_version_id', 'run_id'], ['public.review_runs.project_id', 'public.review_runs.asset_version_id', 'public.review_runs.criteria_version_id', 'public.review_runs.run_id'],
    name='fk_item_results_run', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.item_results"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id', 'run_id', 'attempt_id'], ['public.review_attempts.project_id', 'public.review_attempts.asset_version_id', 'public.review_attempts.run_id', 'public.review_attempts.attempt_id'],
    name='fk_item_results_attempt', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.item_results"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id', 'criteria_version_id', 'criteria_item_id'], ['public.criteria_items.project_id', 'public.criteria_items.target_asset_version_id', 'public.criteria_items.criteria_version_id', 'public.criteria_items.criteria_item_id'],
    name='fk_item_results_criteria_item', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.item_results"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id', 'ocr_run_id'], ['public.ocr_runs.project_id', 'public.ocr_runs.asset_version_id', 'public.ocr_runs.ocr_run_id'],
    name='fk_item_results_ocr_run', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.item_results"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id', 'extraction_id'], ['public.poster_extractions.project_id', 'public.poster_extractions.asset_version_id', 'public.poster_extractions.extraction_id'],
    name='fk_item_results_extraction', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))

metadata.tables["public.run_item_selections"].append_constraint(ForeignKeyConstraint(
    ['project_id', 'asset_version_id', 'criteria_version_id', 'run_id', 'criteria_item_id', 'result_id'], ['public.item_results.project_id', 'public.item_results.asset_version_id', 'public.item_results.criteria_version_id', 'public.item_results.run_id', 'public.item_results.criteria_item_id', 'public.item_results.result_id'],
    name='fk_run_item_selections_result', ondelete="RESTRICT", onupdate="RESTRICT",
    deferrable=False, match="SIMPLE", use_alter=True,
))
