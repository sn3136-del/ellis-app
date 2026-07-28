"""Record (and verify) a route's official published fee.

The fee an applicant is asked to pay must be attributable: FeeRecord is
versioned, carries the official source URL and authority, goes stale after
fees.FEE_MAX_AGE_DAYS, and is only used when the portal renders no
machine-readable amount of its own — a figure read off the live page always
wins. Verifying is a deliberate human act (fees.review_fee), which is what
running this script represents.

  python seed_route_fee.py            # Vietnam e-visa, tourist, US$25
"""
from __future__ import annotations

from app.db import SessionLocal
from app import fees

# The Immigration Department's published e-visa fee for a single-entry
# tourist e-visa. Confirmed against the official portal's fee page.
VIETNAM_TOURIST = {
    "destination": "Vietnam",
    "visa_type": "tourist",
    "government_fee_cents": 2500,
    "service_fee_cents": 0,
    "currency": "USD",
    "refundability": "Non-refundable once the application is submitted.",
    "payment_methods": ["card"],
    "payment_timing": "at_submission",
    "source_url": "https://evisa.gov.vn/",
    "source_authority": "Immigration Department, Ministry of Public Security",
    "conditions": ["single entry"],
}


def main() -> int:
    db = SessionLocal()
    try:
        existing = fees.verified_current_fee(
            db, destination=VIETNAM_TOURIST["destination"],
            visa_type=VIETNAM_TOURIST["visa_type"])
        if existing is not None:
            print(f"already verified: v{existing.version} "
                  f"{existing.government_fee_cents / 100:.2f} {existing.currency}")
            return 0
        fields = {k: v for k, v in VIETNAM_TOURIST.items()
                  if k not in ("destination", "visa_type")}
        rec = fees.create_fee(db, destination=VIETNAM_TOURIST["destination"],
                              visa_type=VIETNAM_TOURIST["visa_type"],
                              actor="operator", **fields)
        fees.review_fee(db, fee_id=rec.id, decision="verified", actor="operator")
        print(f"recorded + verified v{rec.version}: "
              f"{rec.government_fee_cents / 100:.2f} {rec.currency} "
              f"({rec.source_authority})")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
