"""HTTP front for the in-process MockPortal — the 'mock visa portal' service.

This exposes the same safe mock over HTTP so adapters/manual testing can hit a
running portal in the compose stack (never a real government site). Unit tests
use the in-process MockPortal directly; this is for staging/manual adapter work
and to give the compose stack a real, health-checked portal service.

State is process-local (one portal per running container) — good enough for a
mock. Run: uvicorn app.portal.http_mock:app --host 0.0.0.0 --port 8090
"""
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from .mock_portal import MockPortal, HOST

app = FastAPI(title="Ellis Mock Visa Portal", version="0.1.0")
_portal = MockPortal()


@app.get("/healthz")
def healthz():
    return {"ok": True, "portal": HOST, "note": "mock — never a real government portal"}


class Register(BaseModel):
    email: str
    password: str
    full_name: str = ""


class Verify(BaseModel):
    token: str


class Login(BaseModel):
    email: str
    password: str


class Apply(BaseModel):
    session_token: str
    answers: dict = {}


class Pay(BaseModel):
    session_token: str
    application_id: str


class Book(BaseModel):
    session_token: str
    application_id: str
    slot_id: str


class Declare(BaseModel):
    session_token: str
    application_id: str
    human_confirmed: str = "HUMAN_DECLARED"


class Submit(BaseModel):
    session_token: str
    application_id: str


@app.post("/register")
def register(b: Register):
    return _portal.register(email=b.email, password=b.password, full_name=b.full_name)


@app.post("/verify")
def verify(b: Verify):
    return _portal.verify_email(token=b.token)


@app.post("/login")
def login(b: Login):
    return _portal.login(email=b.email, password=b.password)


@app.post("/applications")
def create_application(b: Apply):
    return _portal.create_application(session_token=b.session_token, answers=b.answers)


@app.post("/pay")
def pay(b: Pay):
    return _portal.pay(session_token=b.session_token, application_id=b.application_id)


@app.get("/appointments")
def appointments(session_token: str):
    return _portal.search_appointments(session_token=session_token)


@app.post("/book")
def book(b: Book):
    return _portal.book_appointment(session_token=b.session_token, application_id=b.application_id, slot_id=b.slot_id)


@app.post("/declare")
def declare(b: Declare):
    return _portal.declare_personally(session_token=b.session_token, application_id=b.application_id,
                                      human_confirmed=b.human_confirmed)


@app.post("/submit")
def submit(b: Submit):
    return _portal.submit(session_token=b.session_token, application_id=b.application_id)


@app.get("/applications/{application_id}")
def state(application_id: str, session_token: str):
    return _portal.get_application_state(session_token=session_token, application_id=application_id)
