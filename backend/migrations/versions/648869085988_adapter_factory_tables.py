"""adapter_factory tables (brief §32)

Revision ID: 648869085988
Revises: a1c5d7e9f2b4
Create Date: 2026-07-23
"""
from alembic import op
import sqlalchemy as sa


revision = '648869085988'
down_revision = 'a1c5d7e9f2b4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('adapter_build_requests',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('org_id', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('application_id', sa.String(length=32), nullable=False),
    sa.Column('route_key', sa.String(length=400), nullable=False),
    sa.Column('destination', sa.String(length=80), nullable=False),
    sa.Column('visa_type', sa.String(length=80), nullable=False),
    sa.Column('research_version', sa.String(length=64), nullable=False),
    sa.Column('portal_evidence', sa.JSON(), nullable=False),
    sa.Column('jurisdiction_evidence', sa.JSON(), nullable=False),
    sa.Column('standing_authorization_id', sa.String(length=32), nullable=False),
    sa.Column('runtime_mode', sa.String(length=30), nullable=False),
    sa.Column('consent_given', sa.Boolean(), nullable=False),
    sa.Column('consent_text_version', sa.String(length=20), nullable=False),
    sa.Column('consent_locale', sa.String(length=12), nullable=False),
    sa.Column('consent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('consent_by', sa.String(length=64), nullable=False),
    sa.Column('state', sa.String(length=40), nullable=False),
    sa.Column('state_history', sa.JSON(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('progress', sa.JSON(), nullable=False),
    sa.Column('error', sa.Text(), nullable=False),
    sa.Column('current_candidate_id', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('adapter_candidate_versions',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('candidate_id', sa.String(length=32), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('specification_id', sa.String(length=32), nullable=False),
    sa.Column('manifest', sa.JSON(), nullable=False),
    sa.Column('flow', sa.JSON(), nullable=False),
    sa.Column('field_mappings', sa.JSON(), nullable=False),
    sa.Column('document_mappings', sa.JSON(), nullable=False),
    sa.Column('evidence_rules', sa.JSON(), nullable=False),
    sa.Column('recovery', sa.JSON(), nullable=False),
    sa.Column('kill_switch_key', sa.String(length=120), nullable=False),
    sa.Column('rollback_to_version', sa.Integer(), nullable=False),
    sa.Column('known_limitations', sa.JSON(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('storage_dir', sa.String(length=400), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=False),
    sa.Column('repair_of_version', sa.Integer(), nullable=False),
    sa.Column('quarantined', sa.Boolean(), nullable=False),
    sa.Column('quarantine_reason', sa.String(length=300), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('adapter_candidates',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('build_request_id', sa.String(length=32), nullable=False),
    sa.Column('route_key', sa.String(length=400), nullable=False),
    sa.Column('adapter_id', sa.String(length=120), nullable=False),
    sa.Column('current_version', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('adapter_id')
    )
    op.create_table('adapter_checkpoints',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('execution_id', sa.String(length=32), nullable=False),
    sa.Column('node_id', sa.String(length=120), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('detail', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('adapter_executions',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('org_id', sa.String(length=64), nullable=False),
    sa.Column('application_id', sa.String(length=32), nullable=False),
    sa.Column('candidate_id', sa.String(length=32), nullable=False),
    sa.Column('candidate_version', sa.Integer(), nullable=False),
    sa.Column('tier', sa.String(length=30), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('current_node', sa.String(length=120), nullable=False),
    sa.Column('error', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('adapter_failures',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('candidate_id', sa.String(length=32), nullable=False),
    sa.Column('candidate_version', sa.Integer(), nullable=False),
    sa.Column('execution_id', sa.String(length=32), nullable=False),
    sa.Column('node_id', sa.String(length=120), nullable=False),
    sa.Column('failure_class', sa.String(length=60), nullable=False),
    sa.Column('sanitized_detail', sa.JSON(), nullable=False),
    sa.Column('irreversible_context', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('adapter_kill_switches',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('candidate_id', sa.String(length=32), nullable=False),
    sa.Column('engaged', sa.Boolean(), nullable=False),
    sa.Column('reason', sa.String(length=300), nullable=False),
    sa.Column('engaged_by', sa.String(length=64), nullable=False),
    sa.Column('engaged_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('adapter_outcome_evidence',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('execution_id', sa.String(length=32), nullable=False),
    sa.Column('node_id', sa.String(length=120), nullable=False),
    sa.Column('strength', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=60), nullable=False),
    sa.Column('hostname', sa.String(length=200), nullable=False),
    sa.Column('endpoint_pattern', sa.String(length=300), nullable=False),
    sa.Column('method', sa.String(length=10), nullable=False),
    sa.Column('status_code', sa.Integer(), nullable=False),
    sa.Column('content_type', sa.String(length=100), nullable=False),
    sa.Column('response_keys', sa.JSON(), nullable=False),
    sa.Column('state_category', sa.String(length=60), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('adapter_recon_artifacts',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('recon_job_id', sa.String(length=32), nullable=False),
    sa.Column('page_key', sa.String(length=120), nullable=False),
    sa.Column('hostname', sa.String(length=200), nullable=False),
    sa.Column('url_pattern', sa.String(length=500), nullable=False),
    sa.Column('structure', sa.JSON(), nullable=False),
    sa.Column('content_class', sa.String(length=40), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('adapter_recon_jobs',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('build_request_id', sa.String(length=32), nullable=False),
    sa.Column('org_id', sa.String(length=64), nullable=False),
    sa.Column('portal_hostnames', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('pages_observed', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('adapter_releases',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('candidate_id', sa.String(length=32), nullable=False),
    sa.Column('candidate_version', sa.Integer(), nullable=False),
    sa.Column('route_key', sa.String(length=400), nullable=False),
    sa.Column('tier', sa.String(length=30), nullable=False),
    sa.Column('released_by', sa.String(length=64), nullable=False),
    sa.Column('release_kind', sa.String(length=40), nullable=False),
    sa.Column('evidence_package', sa.JSON(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('revoked_by', sa.String(length=64), nullable=False),
    sa.Column('revoked_reason', sa.String(length=300), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('adapter_repair_attempts',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('candidate_id', sa.String(length=32), nullable=False),
    sa.Column('failed_version', sa.Integer(), nullable=False),
    sa.Column('new_version', sa.Integer(), nullable=False),
    sa.Column('failure_class', sa.String(length=60), nullable=False),
    sa.Column('sanitized_evidence', sa.JSON(), nullable=False),
    sa.Column('outcome', sa.String(length=30), nullable=False),
    sa.Column('stop_reason', sa.String(length=300), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('adapter_review_tasks',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('candidate_id', sa.String(length=32), nullable=False),
    sa.Column('candidate_version', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=60), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('resolved_by', sa.String(length=64), nullable=False),
    sa.Column('resolution_note', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('adapter_rollbacks',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('candidate_id', sa.String(length=32), nullable=False),
    sa.Column('from_version', sa.Integer(), nullable=False),
    sa.Column('to_version', sa.Integer(), nullable=False),
    sa.Column('tier', sa.String(length=30), nullable=False),
    sa.Column('performed_by', sa.String(length=64), nullable=False),
    sa.Column('reason', sa.String(length=300), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('adapter_runtime_bindings',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('route_key', sa.String(length=400), nullable=False),
    sa.Column('tier', sa.String(length=30), nullable=False),
    sa.Column('candidate_id', sa.String(length=32), nullable=False),
    sa.Column('candidate_version', sa.Integer(), nullable=False),
    sa.Column('release_id', sa.String(length=32), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('adapter_specifications',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('build_request_id', sa.String(length=32), nullable=False),
    sa.Column('route_key', sa.String(length=400), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('portal_operator', sa.String(length=200), nullable=False),
    sa.Column('allowed_hostnames', sa.JSON(), nullable=False),
    sa.Column('allowed_redirect_hosts', sa.JSON(), nullable=False),
    sa.Column('flow', sa.JSON(), nullable=False),
    sa.Column('field_mappings', sa.JSON(), nullable=False),
    sa.Column('document_mappings', sa.JSON(), nullable=False),
    sa.Column('generation_basis', sa.JSON(), nullable=False),
    sa.Column('generator', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('adapter_test_artifacts',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('test_run_id', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=60), nullable=False),
    sa.Column('content', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('adapter_test_plans',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('candidate_version_id', sa.String(length=32), nullable=False),
    sa.Column('layers', sa.JSON(), nullable=False),
    sa.Column('required_for_release', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('adapter_test_runs',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('candidate_version_id', sa.String(length=32), nullable=False),
    sa.Column('layer', sa.String(length=60), nullable=False),
    sa.Column('classification', sa.String(length=60), nullable=False),
    sa.Column('passed', sa.Boolean(), nullable=False),
    sa.Column('summary', sa.JSON(), nullable=False),
    sa.Column('executed_by', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('portal_automation_policy_reviews',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('route_key', sa.String(length=400), nullable=False),
    sa.Column('portal_operator', sa.String(length=200), nullable=False),
    sa.Column('hostnames', sa.JSON(), nullable=False),
    sa.Column('decision', sa.String(length=30), nullable=False),
    sa.Column('basis', sa.JSON(), nullable=False),
    sa.Column('reviewed_by', sa.String(length=64), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_portal_automation_policy_reviews_route_key'), table_name='portal_automation_policy_reviews')
    op.drop_index(op.f('ix_portal_automation_policy_reviews_decision'), table_name='portal_automation_policy_reviews')
    op.drop_table('portal_automation_policy_reviews')
    op.drop_index(op.f('ix_adapter_test_runs_candidate_version_id'), table_name='adapter_test_runs')
    op.drop_table('adapter_test_runs')
    op.drop_index(op.f('ix_adapter_test_plans_candidate_version_id'), table_name='adapter_test_plans')
    op.drop_table('adapter_test_plans')
    op.drop_index(op.f('ix_adapter_test_artifacts_test_run_id'), table_name='adapter_test_artifacts')
    op.drop_table('adapter_test_artifacts')
    op.drop_index(op.f('ix_adapter_specifications_build_request_id'), table_name='adapter_specifications')
    op.drop_table('adapter_specifications')
    op.drop_index(op.f('ix_adapter_runtime_bindings_tier'), table_name='adapter_runtime_bindings')
    op.drop_index(op.f('ix_adapter_runtime_bindings_route_key'), table_name='adapter_runtime_bindings')
    op.drop_index(op.f('ix_adapter_runtime_bindings_active'), table_name='adapter_runtime_bindings')
    op.drop_table('adapter_runtime_bindings')
    op.drop_index(op.f('ix_adapter_rollbacks_candidate_id'), table_name='adapter_rollbacks')
    op.drop_table('adapter_rollbacks')
    op.drop_index(op.f('ix_adapter_review_tasks_status'), table_name='adapter_review_tasks')
    op.drop_index(op.f('ix_adapter_review_tasks_candidate_id'), table_name='adapter_review_tasks')
    op.drop_table('adapter_review_tasks')
    op.drop_index(op.f('ix_adapter_repair_attempts_candidate_id'), table_name='adapter_repair_attempts')
    op.drop_table('adapter_repair_attempts')
    op.drop_index(op.f('ix_adapter_releases_tier'), table_name='adapter_releases')
    op.drop_index(op.f('ix_adapter_releases_route_key'), table_name='adapter_releases')
    op.drop_index(op.f('ix_adapter_releases_candidate_id'), table_name='adapter_releases')
    op.drop_table('adapter_releases')
    op.drop_index(op.f('ix_adapter_recon_jobs_status'), table_name='adapter_recon_jobs')
    op.drop_index(op.f('ix_adapter_recon_jobs_org_id'), table_name='adapter_recon_jobs')
    op.drop_index(op.f('ix_adapter_recon_jobs_build_request_id'), table_name='adapter_recon_jobs')
    op.drop_table('adapter_recon_jobs')
    op.drop_index(op.f('ix_adapter_recon_artifacts_recon_job_id'), table_name='adapter_recon_artifacts')
    op.drop_table('adapter_recon_artifacts')
    op.drop_index(op.f('ix_adapter_outcome_evidence_execution_id'), table_name='adapter_outcome_evidence')
    op.drop_table('adapter_outcome_evidence')
    op.drop_index(op.f('ix_adapter_kill_switches_candidate_id'), table_name='adapter_kill_switches')
    op.drop_table('adapter_kill_switches')
    op.drop_index(op.f('ix_adapter_failures_candidate_id'), table_name='adapter_failures')
    op.drop_table('adapter_failures')
    op.drop_index(op.f('ix_adapter_executions_status'), table_name='adapter_executions')
    op.drop_index(op.f('ix_adapter_executions_org_id'), table_name='adapter_executions')
    op.drop_index(op.f('ix_adapter_executions_candidate_id'), table_name='adapter_executions')
    op.drop_index(op.f('ix_adapter_executions_application_id'), table_name='adapter_executions')
    op.drop_table('adapter_executions')
    op.drop_index(op.f('ix_adapter_checkpoints_execution_id'), table_name='adapter_checkpoints')
    op.drop_table('adapter_checkpoints')
    op.drop_index(op.f('ix_adapter_candidates_status'), table_name='adapter_candidates')
    op.drop_index(op.f('ix_adapter_candidates_route_key'), table_name='adapter_candidates')
    op.drop_index(op.f('ix_adapter_candidates_build_request_id'), table_name='adapter_candidates')
    op.drop_table('adapter_candidates')
    op.drop_index(op.f('ix_adapter_candidate_versions_candidate_id'), table_name='adapter_candidate_versions')
    op.drop_table('adapter_candidate_versions')
    op.drop_index(op.f('ix_adapter_build_requests_state'), table_name='adapter_build_requests')
    op.drop_index(op.f('ix_adapter_build_requests_route_key'), table_name='adapter_build_requests')
    op.drop_index(op.f('ix_adapter_build_requests_org_id'), table_name='adapter_build_requests')
    op.drop_index(op.f('ix_adapter_build_requests_application_id'), table_name='adapter_build_requests')
    op.drop_table('adapter_build_requests')
