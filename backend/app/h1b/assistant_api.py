"""Ask Ellis (H1B) endpoints: the grounded assistant turn and the
deterministic guided walkthrough. Implementation lives in h1b/assistant.py;
this file only binds it to the router main.py already includes.

Doctrine: grounding is assembled server-side and party-scoped — the model
never sees the other party's private facts; action tools execute as the
CALLING principal through the party-gated api helpers; the attorney
disclaimer rides on every payload."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..db import get_session
from ..security import Principal, get_principal
from . import assistant

router = APIRouter(prefix="/h1b", tags=["h1b"])


class HistoryTurn(BaseModel):
    role: str = "user"
    text: str = ""


class AssistantBody(BaseModel):
    message: str
    locale: str = "en"
    # Bounded by contract: at most the last 20 turns ride along.
    history: list[HistoryTurn] = Field(default_factory=list, max_length=20)


@router.post("/cases/{case_id}/assistant")
def ask_ellis(case_id: str, body: AssistantBody,
              principal: Principal = Depends(get_principal),
              db=Depends(get_session)):
    parent = assistant.load_parent_case(db, case_id, principal)
    return assistant.run_assistant(
        db, principal, parent, message=body.message, locale=body.locale,
        history=[t.model_dump() for t in body.history])


@router.get("/cases/{case_id}/walkthrough")
def get_walkthrough(case_id: str, locale: str = "en",
                    principal: Principal = Depends(get_principal),
                    db=Depends(get_session)):
    parent = assistant.load_parent_case(db, case_id, principal)
    return assistant.build_walkthrough(db, principal, parent, locale=locale)
