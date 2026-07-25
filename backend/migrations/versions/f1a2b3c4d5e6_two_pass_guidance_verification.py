"""two-pass Kimi guidance: verification column on the guidance cache

Revision ID: f1a2b3c4d5e6
Revises: e9f3a4b5c6d7
Create Date: 2026-07-24
"""
from alembic import op
import sqlalchemy as sa


revision = 'f1a2b3c4d5e6'
down_revision = 'e9f3a4b5c6d7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('kimi_route_guidance_cache') as batch:
        batch.add_column(sa.Column('verification', sa.JSON(), nullable=False,
                                   server_default='{}'))
    with op.batch_alter_table('stored_documents') as batch:
        batch.add_column(sa.Column('passport_profile', sa.JSON(), nullable=False,
                                   server_default='{}'))


def downgrade() -> None:
    with op.batch_alter_table('stored_documents') as batch:
        batch.drop_column('passport_profile')
    with op.batch_alter_table('kimi_route_guidance_cache') as batch:
        batch.drop_column('verification')
