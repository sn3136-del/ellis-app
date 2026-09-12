"""guard-20260912 T9: the consistency sweep's finding fingerprint on issues

Revision ID: d3e4f5a6b7c8
Revises: a2b3c4d5e6f7
Create Date: 2026-09-12

Additive and idempotent: database_issue_reports is created by create_all on
most installations (db._ensure_columns adds the column there too), so the
column and its index are added only when the table exists and lacks them.
"""
from alembic import op
import sqlalchemy as sa

revision = 'd3e4f5a6b7c8'
down_revision = 'a2b3c4d5e6f7'
branch_labels = None
depends_on = None

TABLE = 'database_issue_reports'
INDEX = 'ix_database_issue_reports_fingerprint'


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE not in inspector.get_table_names():
        return
    columns = {c['name'] for c in inspector.get_columns(TABLE)}
    if 'fingerprint' not in columns:
        op.add_column(TABLE, sa.Column('fingerprint', sa.String(length=64), nullable=False,
                                       server_default=''))
    indexes = {i['name'] for i in inspector.get_indexes(TABLE)}
    if INDEX not in indexes:
        op.create_index(INDEX, TABLE, ['fingerprint'])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE not in inspector.get_table_names():
        return
    if INDEX in {i['name'] for i in inspector.get_indexes(TABLE)}:
        op.drop_index(INDEX, table_name=TABLE)
    if 'fingerprint' in {c['name'] for c in inspector.get_columns(TABLE)}:
        op.drop_column(TABLE, 'fingerprint')
