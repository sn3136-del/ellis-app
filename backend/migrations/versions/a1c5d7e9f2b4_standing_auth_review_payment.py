"""Standing authorization + final review versions + exact-amount payment
authorizations (brief §5-§7).

Revision ID: a1c5d7e9f2b4
Revises: 949b2638a102
Create Date: 2026-07-23
"""
from alembic import op
import sqlalchemy as sa

revision = "a1c5d7e9f2b4"
down_revision = "949b2638a102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "applicant_standing_authorizations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("org_id", sa.String(64), nullable=False),
        sa.Column("application_id", sa.String(32), nullable=False),
        sa.Column("applicant_id", sa.String(32), nullable=False),
        sa.Column("route_key", sa.String(400), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("text_version", sa.String(20), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("ui_locale", sa.String(12), nullable=False),
        sa.Column("permitted_actions", sa.JSON(), nullable=False),
        sa.Column("appointment_preferences", sa.JSON(), nullable=False),
        sa.Column("payment_confirmation_required", sa.Boolean(), nullable=False),
        sa.Column("signature_policy", sa.String(40), nullable=False),
        sa.Column("granted_by", sa.String(64), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(300), nullable=False),
        sa.Column("superseded_by", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_applicant_standing_authorizations_org_id",
                    "applicant_standing_authorizations", ["org_id"])
    op.create_index("ix_applicant_standing_authorizations_application_id",
                    "applicant_standing_authorizations", ["application_id"])

    op.create_table(
        "application_review_versions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("org_id", sa.String(64), nullable=False),
        sa.Column("application_id", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("package", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("route_version", sa.String(64), nullable=False),
        sa.Column("adapter_version", sa.String(64), nullable=False),
        sa.Column("signed", sa.Boolean(), nullable=False),
        sa.Column("signature_id", sa.String(32), nullable=False),
        sa.Column("invalidated", sa.Boolean(), nullable=False),
        sa.Column("invalidated_reason", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_application_review_versions_org_id",
                    "application_review_versions", ["org_id"])
    op.create_index("ix_application_review_versions_application_id",
                    "application_review_versions", ["application_id"])
    op.create_index("ix_application_review_versions_content_hash",
                    "application_review_versions", ["content_hash"])

    op.create_table(
        "payment_authorizations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("org_id", sa.String(64), nullable=False),
        sa.Column("application_id", sa.String(32), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("government_fee_cents", sa.Integer(), nullable=False),
        sa.Column("service_fee_cents", sa.Integer(), nullable=False),
        sa.Column("payee", sa.String(200), nullable=False),
        sa.Column("refundability", sa.String(200), nullable=False),
        sa.Column("fee_source_url", sa.String(500), nullable=False),
        sa.Column("fee_version", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("approved_by", sa.String(64), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_reason", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_payment_authorizations_org_id", "payment_authorizations", ["org_id"])
    op.create_index("ix_payment_authorizations_application_id",
                    "payment_authorizations", ["application_id"])
    op.create_index("ix_payment_authorizations_status", "payment_authorizations", ["status"])


def downgrade() -> None:
    op.drop_table("payment_authorizations")
    op.drop_table("application_review_versions")
    op.drop_table("applicant_standing_authorizations")
