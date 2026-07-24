"""adapter capability releases (per-capability automatic release)

Revision ID: c7f1a2b3d4e5
Revises: 648869085988
Create Date: 2026-07-24
"""
from alembic import op
import sqlalchemy as sa


revision = 'c7f1a2b3d4e5'
down_revision = '648869085988'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'adapter_capability_releases',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('route_key', sa.String(length=400), nullable=False),
        sa.Column('capability', sa.String(length=40), nullable=False),
        sa.Column('candidate_id', sa.String(length=32), nullable=False),
        sa.Column('candidate_version', sa.Integer(), nullable=False),
        sa.Column('released_by', sa.String(length=64), nullable=False),
        sa.Column('evidence', sa.JSON(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('revoked_by', sa.String(length=64), nullable=False),
        sa.Column('revoked_reason', sa.String(length=300), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_adapter_capability_releases_route_key'),
                    'adapter_capability_releases', ['route_key'], unique=False)
    op.create_index(op.f('ix_adapter_capability_releases_capability'),
                    'adapter_capability_releases', ['capability'], unique=False)
    op.create_index(op.f('ix_adapter_capability_releases_candidate_id'),
                    'adapter_capability_releases', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_adapter_capability_releases_active'),
                    'adapter_capability_releases', ['active'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_adapter_capability_releases_active'),
                  table_name='adapter_capability_releases')
    op.drop_index(op.f('ix_adapter_capability_releases_candidate_id'),
                  table_name='adapter_capability_releases')
    op.drop_index(op.f('ix_adapter_capability_releases_capability'),
                  table_name='adapter_capability_releases')
    op.drop_index(op.f('ix_adapter_capability_releases_route_key'),
                  table_name='adapter_capability_releases')
    op.drop_table('adapter_capability_releases')
