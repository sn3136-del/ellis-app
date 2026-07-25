"""Stored-document language detection + machine-translation artifacts: keep the
OCR text for applicant-requested translation, the detected language, and the
source-document link on translation artifacts.

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-07-25
"""
from alembic import op
import sqlalchemy as sa


revision = 'b4c5d6e7f8a9'
down_revision = 'a3b4c5d6e7f8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('stored_documents') as batch:
        batch.add_column(sa.Column('ocr_text', sa.Text(), nullable=False,
                                   server_default=''))
        batch.add_column(sa.Column('language', sa.JSON(), nullable=False,
                                   server_default='{}'))
        batch.add_column(sa.Column('translation_of', sa.String(length=32),
                                   nullable=False, server_default=''))


def downgrade() -> None:
    with op.batch_alter_table('stored_documents') as batch:
        batch.drop_column('translation_of')
        batch.drop_column('language')
        batch.drop_column('ocr_text')
