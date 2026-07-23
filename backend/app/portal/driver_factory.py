"""The single place a portal driver is chosen, keyed off the runtime mode.

Absolute real-only execution boundary (brief section 3):
  - test / local_mock_demo  -> MockPortal is permitted (labeled MOCK end to end).
  - tripcom_evaluation / staging / production -> MockPortal must NEVER be
    constructed or bound. A route without an individually approved live adapter
    stops fail-closed with a typed RealOnlyStop instead of falling back.

No other module may construct MockPortal for runtime use. In real-only modes
the adapter registry stays EMPTY until an individually production-approved
live adapter is registered, so metadata reads honestly report none and
classification returns UNSUPPORTED.
"""
from __future__ import annotations

from ..config import settings
from .contract import clear_registry, select_adapter

# Stop statuses the workflow may surface when a real capability is unavailable.
STOP_UNSUPPORTED = "UNSUPPORTED"
STOP_APPLICANT_ACTION_REQUIRED = "APPLICANT_ACTION_REQUIRED"
STOP_MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
STOP_PORTAL_UNAVAILABLE = "PORTAL_UNAVAILABLE"
STOP_OUTCOME_UNCERTAIN = "OUTCOME_UNCERTAIN"
STOP_STATUSES = (STOP_UNSUPPORTED, STOP_APPLICANT_ACTION_REQUIRED,
                 STOP_MANUAL_REVIEW_REQUIRED, STOP_PORTAL_UNAVAILABLE,
                 STOP_OUTCOME_UNCERTAIN)


class RealOnlyStop(Exception):
    """Raised when a live-mode workflow needs a capability that is not
    available for real. Carries a stop status; never silently degraded."""

    def __init__(self, status: str, detail: str, *, route: dict | None = None):
        assert status in STOP_STATUSES
        self.status = status
        self.detail = detail
        self.route = route or {}
        super().__init__(f"{status}: {detail}")


def mock_portal_allowed() -> bool:
    return settings().mock_portal_allowed


def build_runtime_portal(portal_state: dict | None = None):
    """Build the per-case portal for actual workflow execution.

    Mock-allowed modes: MockPortal (rehydrated from state when present).
    Real-only modes: RealOnlyStop — there is no live portal driver yet, and a
    mock must never stand in for one.
    """
    s = settings()
    if s.mock_portal_allowed:
        from .mock_portal import MockPortal
        return MockPortal.from_state(portal_state) if portal_state else MockPortal()
    raise RealOnlyStop(
        STOP_PORTAL_UNAVAILABLE,
        f"runtime mode '{s.runtime_mode}' requires an individually approved live "
        "portal adapter; MockPortal is prohibited and no live driver is bound",
    )


def register_adapters_for_mode():
    """(Re)build the adapter registry for the current mode and return the
    portal used for binding (None in real-only modes).

    Mock-allowed modes register the demo adapters bound to a mock portal.
    Real-only modes register only production-approved live adapters — today
    there are none, so the registry stays empty and selection fails closed.
    """
    clear_registry()
    if not settings().mock_portal_allowed:
        # Real-only: intentionally nothing. A live adapter is registered here
        # only after individual production approval (adapters_admin lifecycle).
        return None
    from .adapters.mockland import build_mockland_adapter
    from .adapters.vietnam_evisa import build_vietnam_evisa_adapter
    from .mock_portal import MockPortal
    portal = MockPortal()
    build_mockland_adapter(portal)
    build_vietnam_evisa_adapter(portal)
    return portal


def register_runtime_adapters(portal) -> None:
    """Register adapters bound to an EXISTING per-case portal (execution path).
    Real-only modes register nothing (see register_adapters_for_mode)."""
    clear_registry()
    if settings().mock_portal_allowed:
        from .adapters.mockland import build_mockland_adapter
        from .adapters.vietnam_evisa import build_vietnam_evisa_adapter
        build_mockland_adapter(portal)
        build_vietnam_evisa_adapter(portal)


def bind_live_adapter(adapter, *, session=None, page=None, state_probe=None,
                      require_real_key: bool = True):
    """Bind a production-approved adapter to the REAL Browserbase live driver.

    This is the activation path: it is reached only for an adapter that has
    passed the adapters_admin lifecycle to production_approved+enabled, in a
    real-only runtime mode, with a real Browserbase key. Any of those missing
    raises RealOnlyStop from the driver's own fail-closed constructor — a live
    driver can never be bound speculatively, and MockPortal is never a fallback
    here. Returns the adapter with its `.driver` replaced by the live driver."""
    from .live_driver import BrowserbaseLiveViewDriver
    adapter.driver = BrowserbaseLiveViewDriver(
        adapter, session=session, page=page, state_probe=state_probe,
        require_real_key=require_real_key)
    return adapter


def select_runtime_adapter(country: str, visa_type: str):
    """Select the adapter for execution. Mock-allowed modes keep the Mockland
    demo fallback; real-only modes have NO fallback — unknown/unsupported
    routes stop with UNSUPPORTED."""
    s = settings()
    adapter = select_adapter(country, visa_type)
    if adapter:
        return adapter
    if s.mock_portal_allowed:
        return select_adapter("Mockland", "tourist")
    raise RealOnlyStop(
        STOP_UNSUPPORTED,
        f"no production-approved live adapter exists for destination "
        f"'{country}' ({visa_type}) in runtime mode '{s.runtime_mode}'",
        route={"destination_country": country, "visa_type": visa_type},
    )


def select_metadata_adapter(country: str, visa_type: str):
    """Non-raising selection for metadata/classification reads. Returns None in
    real-only modes when no live adapter exists (classified UNSUPPORTED)."""
    adapter = select_adapter(country, visa_type)
    if adapter:
        return adapter
    if settings().mock_portal_allowed:
        return select_adapter("Mockland", "tourist")
    return None
