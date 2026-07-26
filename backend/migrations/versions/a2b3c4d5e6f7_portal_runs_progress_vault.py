"""Durable background portal runs, applicant-safe progress events, and the
persistent encrypted vault backend.

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-07-25
"""
from alembic import op
import sqlalchemy as sa


revision = 'a2b3c4d5e6f7'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'portal_runs',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('org_id', sa.String(64), nullable=False, index=True),
        sa.Column('application_id', sa.String(32), sa.ForeignKey('visa_applications.id'),
                  nullable=False, index=True),
        sa.Column('status', sa.String(24), nullable=False, server_default='queued', index=True),
        sa.Column('signal_name', sa.String(40), nullable=False, server_default='start'),
        sa.Column('signal_kwargs', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('allow_submit', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('claimed_by', sa.String(80), nullable=False, server_default=''),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('current_step_key', sa.String(64), nullable=False, server_default=''),
        sa.Column('last_checkpoint_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error', sa.String(300), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        'case_progress_events',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('application_id', sa.String(32), nullable=False, index=True),
        sa.Column('step_key', sa.String(64), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='active'),
        sa.Column('at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        'vault_secrets',
        sa.Column('ref', sa.String(120), primary_key=True),
        sa.Column('ciphertext', sa.Text(), nullable=False),
        sa.Column('meta', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('vault_secrets')
    op.drop_table('case_progress_events')
    op.drop_table('portal_runs')
