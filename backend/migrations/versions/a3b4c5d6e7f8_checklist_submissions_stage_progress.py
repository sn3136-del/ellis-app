"""Checklist submissions (explicit per-requirement document Submit) + durable
case stage progress (document-intake completion).

Revision ID: a3b4c5d6e7f8
Revises: f1a2b3c4d5e6
Create Date: 2026-07-24
"""
from alembic import op
import sqlalchemy as sa


revision = 'a3b4c5d6e7f8'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'checklist_submissions',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('org_id', sa.String(length=64), nullable=False),
        sa.Column('application_id', sa.String(length=32), nullable=False),
        sa.Column('item_id', sa.String(length=120), nullable=False),
        sa.Column('document_id', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('match_verdict', sa.String(length=20), nullable=False),
        sa.Column('detected_type', sa.String(length=60), nullable=False),
        sa.Column('confirmed_by_applicant', sa.Boolean(), nullable=False),
        sa.Column('provenance', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['application_id'], ['visa_applications.id']),
        sa.ForeignKeyConstraint(['document_id'], ['stored_documents.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('application_id', 'item_id',
                            name='uq_checklist_submission_item'),
    )
    op.create_index(op.f('ix_checklist_submissions_org_id'),
                    'checklist_submissions', ['org_id'], unique=False)
    op.create_index(op.f('ix_checklist_submissions_application_id'),
                    'checklist_submissions', ['application_id'], unique=False)

    op.create_table(
        'case_stage_progress',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('org_id', sa.String(length=64), nullable=False),
        sa.Column('application_id', sa.String(length=32), nullable=False),
        sa.Column('stage', sa.String(length=40), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('detail', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['application_id'], ['visa_applications.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('application_id', 'stage', name='uq_case_stage'),
    )
    op.create_index(op.f('ix_case_stage_progress_org_id'),
                    'case_stage_progress', ['org_id'], unique=False)
    op.create_index(op.f('ix_case_stage_progress_application_id'),
                    'case_stage_progress', ['application_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_case_stage_progress_application_id'),
                  table_name='case_stage_progress')
    op.drop_index(op.f('ix_case_stage_progress_org_id'),
                  table_name='case_stage_progress')
    op.drop_table('case_stage_progress')
    op.drop_index(op.f('ix_checklist_submissions_application_id'),
                  table_name='checklist_submissions')
    op.drop_index(op.f('ix_checklist_submissions_org_id'),
                  table_name='checklist_submissions')
    op.drop_table('checklist_submissions')
