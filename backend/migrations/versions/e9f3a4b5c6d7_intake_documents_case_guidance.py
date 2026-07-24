"""intake passport documents + per-case saved route guidance (applicant journey)

Revision ID: e9f3a4b5c6d7
Revises: d8e2f3a4b5c6
Create Date: 2026-07-24
"""
from alembic import op
import sqlalchemy as sa


revision = 'e9f3a4b5c6d7'
down_revision = 'd8e2f3a4b5c6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'route_intake_documents',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('org_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=64), nullable=False),
        sa.Column('intake_id', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('mime', sa.String(length=64), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('content', sa.LargeBinary(), nullable=True),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('doc_type', sa.String(length=48), nullable=False),
        sa.Column('execution_class', sa.String(length=32), nullable=False),
        sa.Column('page_classification', sa.JSON(), nullable=False),
        sa.Column('extracted_fields', sa.JSON(), nullable=False),
        sa.Column('passport_profile', sa.JSON(), nullable=False),
        sa.Column('quality_warnings', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_route_intake_documents_org_id'),
                    'route_intake_documents', ['org_id'], unique=False)
    op.create_index(op.f('ix_route_intake_documents_intake_id'),
                    'route_intake_documents', ['intake_id'], unique=False)
    op.create_index(op.f('ix_route_intake_documents_sha256'),
                    'route_intake_documents', ['sha256'], unique=False)

    op.create_table(
        'case_route_guidance',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('org_id', sa.String(length=64), nullable=False),
        sa.Column('case_id', sa.String(length=32), nullable=False),
        sa.Column('intake_id', sa.String(length=32), nullable=True),
        sa.Column('route_key', sa.String(length=400), nullable=False),
        sa.Column('disposition', sa.String(length=48), nullable=False),
        sa.Column('continuation_kind', sa.String(length=32), nullable=False),
        sa.Column('guidance', sa.JSON(), nullable=False),
        sa.Column('checklist', sa.JSON(), nullable=False),
        sa.Column('audit_job_id', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_case_route_guidance_org_id'),
                    'case_route_guidance', ['org_id'], unique=False)
    op.create_index(op.f('ix_case_route_guidance_case_id'),
                    'case_route_guidance', ['case_id'], unique=True)
    op.create_index(op.f('ix_case_route_guidance_intake_id'),
                    'case_route_guidance', ['intake_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_case_route_guidance_intake_id'), table_name='case_route_guidance')
    op.drop_index(op.f('ix_case_route_guidance_case_id'), table_name='case_route_guidance')
    op.drop_index(op.f('ix_case_route_guidance_org_id'), table_name='case_route_guidance')
    op.drop_table('case_route_guidance')
    op.drop_index(op.f('ix_route_intake_documents_sha256'), table_name='route_intake_documents')
    op.drop_index(op.f('ix_route_intake_documents_intake_id'), table_name='route_intake_documents')
    op.drop_index(op.f('ix_route_intake_documents_org_id'), table_name='route_intake_documents')
    op.drop_table('route_intake_documents')
