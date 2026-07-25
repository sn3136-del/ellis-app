"""Global route coverage: every lawful nationality -> residence -> destination
tourism/short-stay combination resolves to exactly ONE defined, honest outcome.

Layered, normalized model (no redundant per-combination copies):
  1. RoutePairPolicy — one row per (nationality, travel document, destination)
     pair: the entry disposition and its provenance/verification level.
  2. PortalFamily — deduplicated real official portals/platforms; one adapter
     per family, reused by every route that applies through that family.
  3. Jurisdiction rules (visa_snapshot.ConsularJurisdictionRule) — the only
     layer where lawful residence changes the answer (which post/center).
  4. resolver.resolve_route() — deterministic assembly of a full route record
     for ANY input tuple from layers 1-3. Nationality, issuing country,
     document type and residence are independent inputs, never conflated.

Honesty invariants (enforced in code + tests):
  - reference-dataset rows are PROVISIONAL, never verified, never released;
  - a pair with no data is an explicit UNRESOLVED outcome, never a guess;
  - released coverage counts ONLY verified adapters / verified non-portal
    workflows / explicit legally-unavailable results.
"""
from __future__ import annotations

# The exact outcome vocabulary from the coverage brief, plus the explicit
# honest not-yet-resolved value (never counted as supported).
ROUTE_OUTCOMES = (
    "VISA_EXEMPT",
    "ENTRY_PREPARATION",
    "ELECTRONIC_AUTHORIZATION",
    "EVISA",
    "VISA_ON_ARRIVAL",
    "EMBASSY_OR_CONSULATE_APPLICATION",
    "AUTHORIZED_VISA_CENTER",
    "MAIL_APPLICATION",
    "APPOINTMENT_REQUIRED",
    "NO_AVAILABLE_TOURIST_ROUTE",
    "REQUIRES_MANUAL_JURISDICTION_SELECTION",
    "UNRESOLVED",
)

# Outcomes that need NO portal-submission adapter (non-portal workflows).
NON_PORTAL_OUTCOMES = (
    "VISA_EXEMPT", "ENTRY_PREPARATION", "VISA_ON_ARRIVAL",
    "EMBASSY_OR_CONSULATE_APPLICATION", "AUTHORIZED_VISA_CENTER",
    "MAIL_APPLICATION", "APPOINTMENT_REQUIRED",
)

# Outcomes applied through an online portal (adapter-eligible).
ELECTRONIC_OUTCOMES = ("ELECTRONIC_AUTHORIZATION", "EVISA")

# Terminal outcomes that are neither supported nor pending.
UNAVAILABLE_OUTCOMES = ("NO_AVAILABLE_TOURIST_ROUTE",)

PAIR_SOURCES = ("none", "reference_dataset", "official_research")
VERIFICATION_LEVELS = ("unresolved", "provisional", "verified")

# Route-level release classification for the coverage dashboard. Only the
# released_* values (and legally_unavailable) count toward coverage.
RELEASE_STATUSES = (
    "unresolved",            # no defined outcome yet — never claimed supported
    "defined_provisional",   # outcome defined from reference data only
    "defined_verified",      # outcome verified against official sources,
                             # workflow/adapter not yet released
    "released_workflow",     # verified non-portal workflow released
    "released_adapter",      # verified real-portal adapter released
    "legally_unavailable",   # explicit no-lawful-tourist-route result
)

PORTAL_FAMILY_KINDS = (
    "evisa_portal", "eta_portal", "arrival_card", "appointment_portal",
    "visa_center_portal", "immigration_authority", "status_check_portal",
)

FAMILY_VERIFICATION = (
    "seed_unverified",          # curated seed; official identity NOT confirmed
    "verified_official_domain", # hostname matches the government allowlist
    "verified_live",            # official domain + live credential-free recon
    "conflicted",               # evidence disagrees (e.g. wrong destination)
    "unavailable",              # confirmed unreachable / withdrawn
)
