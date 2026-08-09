"""H1B ORM models: the two-party dimension and the multi-filing pipeline.

New tables only — existing tables gain nothing here. The party columns added to
legacy tables (party_id and the like) are declared on the models in
app/models.py; empty string means "legacy beneficiary" so every pre-H1B flow is
untouched. These tables are registered in privacy._CASE_CHILD_MODELS so export
and erasure stay complete.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import String, Integer, Boolean, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ..models import TimestampMixin


def _uuid() -> str:
    return uuid.uuid4().hex


PARTY_ROLES = ("beneficiary", "petitioner")

# Filing steps. Every step is a real government filing (or the consular leg)
# executed as its own linked child case with the standard machinery.
STEP_KEYS = ("lca", "registration", "i129", "ds160_consular")

STEP_STATUSES = ("blocked", "ready", "in_progress", "awaiting_government",
                 "verified", "failed")


class CaseParty(Base, TimestampMixin):
    """A human/organization role on a parent H1B case. Exactly one row per
    (case, role). user_id stays empty until a real account operates the party."""
    __tablename__ = "case_parties"
    __table_args__ = (UniqueConstraint("application_id", "role"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(64), index=True)
    application_id: Mapped[str] = mapped_column(String(32), index=True)
    role: Mapped[str] = mapped_column(String(24))          # beneficiary | petitioner
    party_kind: Mapped[str] = mapped_column(String(16), default="person")
    user_id: Mapped[str] = mapped_column(String(64), default="")
    display_name: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str] = mapped_column(String(320), default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    employer_profile_id: Mapped[str] = mapped_column(String(32), default="")
    # Party-scoped answers. Beneficiary answers stay on the parent case
    # (compatibility with intake/passport/OCR); petitioner answers live here and
    # on EmployerProfile. Filing workflows receive an explicitly assembled
    # merge, never both dicts wholesale (the flat namespace is party-blind).
    answers: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="active")


class EmployerProfile(Base, TimestampMixin):
    """Org-level petitioner facts, reused across beneficiaries. These become
    statements on ETA-9035 / I-129, so none of the nullable attestation fields
    may ever be defaulted — an absent answer is asked, never assumed."""
    __tablename__ = "employer_profiles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(64), index=True)
    legal_name: Mapped[str] = mapped_column(String(240), default="")
    trade_name: Mapped[str] = mapped_column(String(240), default="")
    fein: Mapped[str] = mapped_column(String(10), default="")   # digits only
    naics_code: Mapped[str] = mapped_column(String(8), default="")
    address_line1: Mapped[str] = mapped_column(String(240), default="")
    address_line2: Mapped[str] = mapped_column(String(240), default="")
    city: Mapped[str] = mapped_column(String(120), default="")
    state: Mapped[str] = mapped_column(String(64), default="")
    postal_code: Mapped[str] = mapped_column(String(16), default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    year_established: Mapped[int] = mapped_column(Integer, default=0)
    total_employees: Mapped[int] = mapped_column(Integer, default=0)
    gross_annual_income_cents: Mapped[int] = mapped_column(Integer, default=0)
    net_annual_income_cents: Mapped[int] = mapped_column(Integer, default=0)
    h1b_dependent: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    willful_violator: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    signatory_name: Mapped[str] = mapped_column(String(200), default="")
    signatory_title: Mapped[str] = mapped_column(String(200), default="")
    signatory_email: Mapped[str] = mapped_column(String(320), default="")
    signatory_phone: Mapped[str] = mapped_column(String(64), default="")
    # Foreign-parent petitioners (Trip.com Group) evidence the corporate
    # relationship on the I-129.
    parent_company_name: Mapped[str] = mapped_column(String(240), default="")
    parent_company_country: Mapped[str] = mapped_column(String(80), default="")
    created_by: Mapped[str] = mapped_column(String(64), default="")


class H1bCaseStep(Base, TimestampMixin):
    """One government filing in the pipeline. child_case_id stays empty until
    the step is released; receipts are written only from verified evidence."""
    __tablename__ = "h1b_case_steps"
    __table_args__ = (UniqueConstraint("application_id", "step_key"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(64), index=True)
    application_id: Mapped[str] = mapped_column(String(32), index=True)  # parent case
    step_key: Mapped[str] = mapped_column(String(24))
    child_case_id: Mapped[str] = mapped_column(String(32), default="")
    acting_party: Mapped[str] = mapped_column(String(24))  # petitioner | beneficiary
    depends_on: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="blocked")
    # Receipt facts, each persisted only alongside government-host evidence.
    lca_number: Mapped[str] = mapped_column(String(32), default="")
    beneficiary_confirmation_number: Mapped[str] = mapped_column(String(40), default="")
    uscis_receipt_number: Mapped[str] = mapped_column(String(16), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
