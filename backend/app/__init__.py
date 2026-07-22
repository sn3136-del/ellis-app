"""Ellis tourist-visa processing backend.

A FastAPI service that owns the production concerns the Electron client cannot:
multi-tenant persistence, durable resumable workflows, encrypted credential
storage, AI (Kimi K3) tool-calling, OCR, and the live provider integrations.

Every external provider is behind a capability check with a local test double,
so the whole service runs and its tests pass with zero cloud credentials; each
integration names the one setting that activates the real provider.
"""

__version__ = "0.1.0"
