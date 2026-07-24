"""kimi route guidance cache (Kimi-primary immediate route decision)

Revision ID: d8e2f3a4b5c6
Revises: c7f1a2b3d4e5
Create Date: 2026-07-24
"""
from alembic import op
import sqlalchemy as sa


revision = 'd8e2f3a4b5c6'
down_revision = 'c7f1a2b3d4e5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'kimi_route_guidance_cache',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('cache_key', sa.String(length=200), nullable=False),
        sa.Column('route', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('guidance', sa.JSON(), nullable=False),
        sa.Column('missing_fields', sa.JSON(), nullable=False),
        sa.Column('contradictions', sa.JSON(), nullable=False),
        sa.Column('model', sa.String(length=64), nullable=False),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fresh_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_kimi_route_guidance_cache_cache_key'),
                    'kimi_route_guidance_cache', ['cache_key'], unique=True)
    op.create_index(op.f('ix_kimi_route_guidance_cache_status'),
                    'kimi_route_guidance_cache', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_kimi_route_guidance_cache_status'),
                  table_name='kimi_route_guidance_cache')
    op.drop_index(op.f('ix_kimi_route_guidance_cache_cache_key'),
                  table_name='kimi_route_guidance_cache')
    op.drop_table('kimi_route_guidance_cache')
