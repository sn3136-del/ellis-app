"""global route coverage: pair policies, portal families, family adapters,
orchestrator runs/tasks

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-07-25
"""
from alembic import op
import sqlalchemy as sa


revision = 'c5d6e7f8a9b0'
down_revision = 'b4c5d6e7f8a9'
branch_labels = None
depends_on = None


def _stamped(*extra):
    return [
        sa.Column('id', sa.String(length=32), nullable=False),
        *extra,
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        'global_route_pair_policies',
        *_stamped(
            sa.Column('snapshot_date', sa.String(length=10), nullable=False),
            sa.Column('pair_key', sa.String(length=120), nullable=False),
            sa.Column('passport_nationality', sa.String(length=3), nullable=False),
            sa.Column('travel_document_type', sa.String(length=48), nullable=False),
            sa.Column('destination_country', sa.String(length=3), nullable=False),
            sa.Column('disposition', sa.String(length=48), nullable=False),
            sa.Column('route_outcome', sa.String(length=48), nullable=False),
            sa.Column('primary_category', sa.String(length=48), nullable=False),
            sa.Column('max_stay_days', sa.Integer(), nullable=True),
            sa.Column('source', sa.String(length=32), nullable=False),
            sa.Column('verification_status', sa.String(length=16), nullable=False),
            sa.Column('source_ref', sa.String(length=400), nullable=False),
            sa.Column('official_source_urls', sa.JSON(), nullable=False),
            sa.Column('retrieved_at', sa.String(length=32), nullable=False),
            sa.Column('dataset_date', sa.String(length=10), nullable=False),
            sa.Column('portal_family_id', sa.String(length=120), nullable=False),
            sa.Column('release_status', sa.String(length=32), nullable=False),
            sa.Column('release_reason', sa.String(length=400), nullable=False),
            sa.Column('notes', sa.Text(), nullable=False),
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('pair_key', name='uq_global_pair_key'),
    )
    for col in ('pair_key', 'passport_nationality', 'destination_country',
                'disposition', 'route_outcome', 'source', 'verification_status',
                'portal_family_id', 'release_status', 'snapshot_date'):
        op.create_index(f'ix_global_route_pair_policies_{col}',
                        'global_route_pair_policies', [col])

    op.create_table(
        'portal_families',
        *_stamped(
            sa.Column('family_id', sa.String(length=120), nullable=False),
            sa.Column('name', sa.String(length=200), nullable=False),
            sa.Column('kind', sa.String(length=48), nullable=False),
            sa.Column('operator', sa.String(length=200), nullable=False),
            sa.Column('operator_kind', sa.String(length=32), nullable=False),
            sa.Column('base_url', sa.String(length=400), nullable=False),
            sa.Column('hostnames', sa.JSON(), nullable=False),
            sa.Column('destinations', sa.JSON(), nullable=False),
            sa.Column('nationality_scope', sa.JSON(), nullable=False),
            sa.Column('supported_outcomes', sa.JSON(), nullable=False),
            sa.Column('account_required', sa.Boolean(), nullable=False),
            sa.Column('verification_status', sa.String(length=32), nullable=False),
            sa.Column('verification_evidence', sa.JSON(), nullable=False),
            sa.Column('last_verified_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('stale', sa.Boolean(), nullable=False),
            sa.Column('active', sa.Boolean(), nullable=False),
            sa.Column('notes', sa.Text(), nullable=False),
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('family_id', name='uq_portal_family_id'),
    )
    op.create_index('ix_portal_families_family_id', 'portal_families', ['family_id'])
    op.create_index('ix_portal_families_kind', 'portal_families', ['kind'])
    op.create_index('ix_portal_families_verification_status', 'portal_families',
                    ['verification_status'])

    op.create_table(
        'portal_family_adapters',
        *_stamped(
            sa.Column('family_id', sa.String(length=120), nullable=False),
            sa.Column('build_request_id', sa.String(length=32), nullable=False),
            sa.Column('candidate_id', sa.String(length=32), nullable=False),
            sa.Column('representative_route_key', sa.String(length=400), nullable=False),
            sa.Column('status', sa.String(length=48), nullable=False),
            sa.Column('gate_report', sa.JSON(), nullable=False),
            sa.Column('released', sa.Boolean(), nullable=False),
            sa.Column('release_tier', sa.String(length=16), nullable=False),
            sa.Column('last_error', sa.Text(), nullable=False),
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('family_id', name='uq_family_adapter'),
    )
    op.create_index('ix_portal_family_adapters_family_id', 'portal_family_adapters',
                    ['family_id'])
    op.create_index('ix_portal_family_adapters_candidate_id', 'portal_family_adapters',
                    ['candidate_id'])
    op.create_index('ix_portal_family_adapters_released', 'portal_family_adapters',
                    ['released'])
    op.create_index('ix_portal_family_adapters_status', 'portal_family_adapters',
                    ['status'])

    op.create_table(
        'global_build_runs',
        *_stamped(
            sa.Column('command', sa.String(length=64), nullable=False),
            sa.Column('params', sa.JSON(), nullable=False),
            sa.Column('status', sa.String(length=16), nullable=False),
            sa.Column('checkpoint', sa.JSON(), nullable=False),
            sa.Column('stats', sa.JSON(), nullable=False),
            sa.Column('stopped_reason', sa.String(length=300), nullable=False),
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_global_build_runs_command', 'global_build_runs', ['command'])
    op.create_index('ix_global_build_runs_status', 'global_build_runs', ['status'])

    op.create_table(
        'global_build_tasks',
        *_stamped(
            sa.Column('run_id', sa.String(length=32), nullable=False),
            sa.Column('kind', sa.String(length=48), nullable=False),
            sa.Column('subject', sa.String(length=200), nullable=False),
            sa.Column('dedup_key', sa.String(length=300), nullable=False),
            sa.Column('status', sa.String(length=16), nullable=False),
            sa.Column('attempts', sa.Integer(), nullable=False),
            sa.Column('max_attempts', sa.Integer(), nullable=False),
            sa.Column('error', sa.Text(), nullable=False),
            sa.Column('error_class', sa.String(length=16), nullable=False),
            sa.Column('result', sa.JSON(), nullable=False),
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('dedup_key', name='uq_global_task_dedup'),
    )
    for col in ('run_id', 'kind', 'subject', 'dedup_key', 'status'):
        op.create_index(f'ix_global_build_tasks_{col}', 'global_build_tasks', [col])


def downgrade() -> None:
    for t in ('global_build_tasks', 'global_build_runs', 'portal_family_adapters',
              'portal_families', 'global_route_pair_policies'):
        op.drop_table(t)
