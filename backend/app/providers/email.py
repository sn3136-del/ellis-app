"""Transactional email provider (Phase 7 setup + Phase 8 delivery).

Supports SMTP and a generic API-based transactional provider, plus a local
recorder so the pipeline runs without email credentials in dev/test. An email
sender ADDRESS alone is never sufficient to send — a valid SMTP or API
credential is required, and this is enforced by config_valid().

No passport numbers or other sensitive identity values are ever placed in an
email body by this layer; callers pass already-safe content.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EmailConfig:
    provider: str = "none"        # smtp | api | local | none
    sender: str = ""
    reply_to: str = ""
    # SMTP
    host: str = ""
    port: int = 587
    username: str = ""
    # API provider
    api_endpoint: str = ""
    # The single secret (SMTP password or API key), revealed from the vault at
    # send time — never stored in this object beyond the call, never logged.
    credential: str = ""


def config_valid(cfg: EmailConfig) -> tuple[bool, str]:
    """A sender address alone is not enough. Sending needs real credentials."""
    if cfg.provider in ("none", ""):
        return False, "no email provider configured"
    if not cfg.sender:
        return False, "sender address required"
    if cfg.provider == "smtp":
        if not cfg.host:
            return False, "SMTP host required"
        if not cfg.credential:
            return False, "SMTP credentials required (an email address alone is not sufficient)"
        return True, "ok"
    if cfg.provider == "api":
        if not cfg.api_endpoint:
            return False, "API endpoint required"
        if not cfg.credential:
            return False, "API credentials required (an email address alone is not sufficient)"
        return True, "ok"
    if cfg.provider == "local":
        return True, "ok"   # dev recorder — never claims real delivery
    return False, f"unknown provider '{cfg.provider}'"


class LocalEmailProvider:
    """Dev/test recorder. Records the message and NEVER claims real delivery."""
    mode = "local_recorded"

    def __init__(self, recorder=None):
        self._recorder = recorder

    def verify(self) -> dict:
        return {"ok": True, "mode": self.mode, "delivered": False,
                "note": "local recorder — no real email is sent"}

    def send(self, *, to: str, subject: str, body: str) -> dict:
        if self._recorder:
            self._recorder(to=to, subject=subject, body=body)
        return {"ok": True, "mode": self.mode, "delivered": False,
                "note": "recorded locally; configure SMTP or an API provider for real delivery"}


class SmtpEmailProvider:  # pragma: no cover - needs a real SMTP server
    mode = "smtp"

    def __init__(self, cfg: EmailConfig):
        self.cfg = cfg

    def verify(self) -> dict:
        import smtplib
        try:
            with smtplib.SMTP(self.cfg.host, self.cfg.port, timeout=10) as s:
                s.starttls()
                if self.cfg.username:
                    s.login(self.cfg.username, self.cfg.credential)
            return {"ok": True, "mode": self.mode, "note": "SMTP connection + auth ok"}
        except Exception as e:
            return {"ok": False, "mode": self.mode, "detail": _redact(str(e))}

    def send(self, *, to: str, subject: str, body: str) -> dict:
        import smtplib
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["From"] = self.cfg.sender
        msg["To"] = to
        if self.cfg.reply_to:
            msg["Reply-To"] = self.cfg.reply_to
        msg["Subject"] = subject
        msg.set_content(body)
        try:
            with smtplib.SMTP(self.cfg.host, self.cfg.port, timeout=15) as s:
                s.starttls()
                if self.cfg.username:
                    s.login(self.cfg.username, self.cfg.credential)
                s.send_message(msg)
            return {"ok": True, "mode": self.mode, "delivered": True}
        except Exception as e:
            return {"ok": False, "mode": self.mode, "delivered": False, "detail": _redact(str(e))}


class ApiEmailProvider:  # pragma: no cover - needs a real provider
    mode = "api"

    def __init__(self, cfg: EmailConfig):
        self.cfg = cfg

    def verify(self) -> dict:
        return {"ok": bool(self.cfg.api_endpoint and self.cfg.credential), "mode": self.mode,
                "note": "API credentials present" if self.cfg.credential else "missing API credentials"}

    def send(self, *, to: str, subject: str, body: str) -> dict:
        import httpx
        try:
            r = httpx.post(self.cfg.api_endpoint,
                           headers={"authorization": f"Bearer {self.cfg.credential}"},
                           json={"from": self.cfg.sender, "to": to, "subject": subject,
                                 "text": body, "reply_to": self.cfg.reply_to}, timeout=15)
            r.raise_for_status()
            return {"ok": True, "mode": self.mode, "delivered": True}
        except Exception as e:
            return {"ok": False, "mode": self.mode, "delivered": False, "detail": _redact(str(e))}


def _redact(msg: str) -> str:
    # Never surface a credential or full endpoint in an error; keep a short,
    # non-sensitive category only.
    return (msg[:60] + "…") if len(msg) > 60 else msg


def get_provider(cfg: EmailConfig, *, recorder=None):
    if cfg.provider == "smtp":
        return SmtpEmailProvider(cfg)
    if cfg.provider == "api":
        return ApiEmailProvider(cfg)
    return LocalEmailProvider(recorder=recorder)
