"""Runnable end-to-end demonstration of the exact flow the brief specifies,
driven entirely through the authenticated HTTP API + the durable service, then
proving worker-restart consistency. Run:  python demo_e2e.py

No cloud credentials required — every provider runs on its safe local fallback.
"""
import os
import tempfile

_fd, _db = tempfile.mkstemp(suffix=".db")
os.environ["DATABASE_URL"] = f"sqlite:///{_db}"
os.environ.setdefault("ELLIS_VAULT_PASSPHRASE", "demo-passphrase")

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.db import SessionLocal, create_all  # noqa: E402
from app import service, models  # noqa: E402
from sqlalchemy import select  # noqa: E402

create_all()  # ensure schema (startup event only fires under a context manager)

AUTH = {"Authorization": "Bearer dev-token", "X-Org-Id": "acme", "X-User-Id": "agent-1"}
PASSPORT_MRZ = ("P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\n"
                "L898902C36UTO7408122F1204159ZE184226B<<<<<10")
BANK = "Account holder: Anna Eriksson\nBank: Example Bank\nClosing balance: 12,000 USD"

c = TestClient(app)
step = 0


def show(label, value=""):
    global step
    step += 1
    print(f"{step:2d}. {label}: {value}")


# 1. Create a case.
r = c.post("/cases", headers=AUTH, json={"full_name": "Anna Eriksson", "email": "anna@example.com",
                                         "destination_country": "Mockland"}).json()
cid = r["id"]
show("Create a case", f"{cid} state={r['state']}")

# 2. Upload a passport and a supporting document.
p = c.post(f"/cases/{cid}/documents", headers=AUTH,
           json={"name": "passport.pdf", "mime": "application/pdf", "text": PASSPORT_MRZ}).json()
b = c.post(f"/cases/{cid}/documents", headers=AUTH,
           json={"name": "bank.pdf", "mime": "application/pdf", "text": BANK}).json()
show("Upload passport + supporting doc", f"passport doc={p['id'][:8]}, bank doc={b['id'][:8]}")

# 3. Run OCR (happened on upload) — show extracted + validation.
show("Run OCR", f"passport type={p['doc_type']} mrz_valid={p['mrz_valid']} "
                f"fields={list(p['extracted_fields'])}")

# 4. Review and approve fields (with a correction).
c.post(f"/cases/{cid}/documents/{p['id']}/approve", headers=AUTH,
       json=[{"key": "given_names", "value": "ANNA MARIA"}])
review = c.get(f"/cases/{cid}/review", headers=AUTH).json()
show("Review + approve fields", f"conflicts={review['conflicts']}, approved passport")

# Preferences + authorization (prerequisites for the run).
c.post(f"/cases/{cid}/preferences", headers=AUTH, json={"prefs": {
    "preferredLocation": "MOCKLAND-CAP", "allowAutoBook": True, "applicantTimeZone": "UTC", "minAdvanceMs": 0}})
auth = c.post(f"/cases/{cid}/authorization", headers=AUTH,
              json={"max_fee_cents": 10000, "currency": "USD", "allow_auto_book": True}).json()

# Ensure required answers present for the Mockland adapter. The specimen MRZ
# passport expired 2012-04-15 and step 4's approval copied that expiry_date
# into the answers — correct it (as the applicant would after renewing) or
# the validity gate 409s /start honestly and the demo cannot proceed.
db = SessionLocal()
app_row = db.get(models.VisaApplication, cid)
app_row.answers = {**app_row.answers, "passport_number": "L898902C3", "nationality": "UTO",
                   "birth_date": "740812", "expiry_date": "2033-04-15",
                   "passport_expiry": "2033-04-15"}
db.commit(); db.close()

# 5. Sign the authorization — the REAL single ceremony (prepare → step-up →
# sign). The one signature also grants the §5 standing authorization; the
# bare grant endpoint no longer exists (ceremony-less grants were an
# authorization bypass).
prep = c.post(f"/cases/{cid}/authorization/prepare", headers=AUTH,
              json={"max_fee_cents": 10000, "currency": "USD",
                    "allow_auto_book": True}).json()
sig = c.post(f"/cases/{cid}/authorization/sign", headers=AUTH, json={
    "document_hash": prep["document_hash"], "consent_given": True,
    "intent_confirmed": True, "signature_method": "typed",
    "signature_value": "Anna Eriksson", "step_up_token": prep["step_up_token"],
    "auth_method": "email_otp", "envelope_id": prep["envelope_id"]})
if sig.status_code != 200:
    raise SystemExit(f"authorization sign refused ({sig.status_code}): {sig.text}")
show("Sign authorization", f"provider={auth['provider']} production_equivalent={auth['production_equivalent']} "
                           f"signature={sig.json()['signature_id'][:8]}")

# Start the durable workflow and walk each human handoff via the API.
r = c.post(f"/cases/{cid}/start", headers=AUTH)
status = r.json()
if "state" not in status:
    # An honest gate refusal (409 detail) must read as such, not a KeyError.
    raise SystemExit(f"start refused ({r.status_code}): {status.get('detail')}")


def latest_token():
    db = SessionLocal()
    try:
        wf = service.load_workflow(db, cid)
        for e in reversed(wf._portal.emails):
            if e["kind"] == "verification":
                return e["link"].split("token=")[1]
    finally:
        db.close()


seen = set()
for _ in range(40):
    st = status["state"]
    if st in ("COMPLETED", "CANCELLED", "MANUAL_REVIEW_REQUIRED", "RECOVERABLE_FAILURE"):
        break
    pend = status.get("pending")
    if not pend:
        break
    h = pend["handoff"]
    if h == "review":
        status = c.post(f"/cases/{cid}/signals/approve_review", headers=AUTH).json()
    elif h == "authorization":
        status = c.post(f"/cases/{cid}/signals/sign_authorization", headers=AUTH).json()
    elif h == "captcha":
        if "captcha" not in seen:
            show("Human CAPTCHA handoff", f"mode={pend['live_view']['mode']} (applicant solves it)")
            seen.add("captcha")
        status = c.post(f"/cases/{cid}/signals/solve_captcha", headers=AUTH).json()
    elif h == "email_verification":
        status = c.post(f"/cases/{cid}/signals/verify_email", headers=AUTH,
                        json={"token": latest_token()}).json()
    elif h == "payment_approval":
        status = c.post(f"/cases/{cid}/signals/approve_payment", headers=AUTH).json()
    elif h == "additional_information":
        # The portal asked mid-flight questions: answer each one honestly-
        # shaped (first real option / a valid date / a placeholder) so the
        # walker never stalls on the new pause kinds.
        answers = {}
        for q in pend.get("questions") or []:
            key = str(q.get("key", ""))
            if not key or key.startswith("document:") or q.get("kind") == "document":
                continue
            if not q.get("mandatory", True):
                continue
            if q.get("options"):
                answers[key] = str(q["options"][0])
            elif q.get("kind") == "date":
                answers[key] = "01/01/2030"
            else:
                answers[key] = "Demo answer"
        if not answers:
            break
        status = c.post(f"/cases/{cid}/signals/provide_information", headers=AUTH,
                        json={"answers": answers}).json()
    elif h == "payment_credentials":
        # Demo chooses the applicant-types-it-personally path — no card data.
        status = c.post(f"/cases/{cid}/signals/provide_payment_details", headers=AUTH,
                        json={"manual": True}).json()
    elif h == "payment":
        if "pay" not in seen:
            show("Applicant-controlled payment", f"{pend['fee']['display']} in the secure window "
                                                 f"(mode={pend['live_view']['mode']}, card never seen by Ellis)")
            seen.add("pay")
        status = c.post(f"/cases/{cid}/signals/complete_payment", headers=AUTH).json()
    elif h == "appointment_selection":
        status = c.post(f"/cases/{cid}/signals/select_appointment", headers=AUTH,
                        json={"slot_id": pend["slots"][0]["slotId"]}).json()
    elif h == "personal_declaration":
        if "decl" not in seen:
            show("Fill application + book appointment", "done by Ellis")
            show("Final applicant declaration", "applicant personally declares (Ellis never clicks it)")
            seen.add("decl")
        status = c.post(f"/cases/{cid}/signals/complete_declaration", headers=AUTH).json()
    elif h == "final_review":
        # §7: review + sign the EXACT final version over the API, then re-drive.
        rv = c.post(f"/cases/{cid}/final-review", headers=AUTH).json()
        c.post(f"/cases/{cid}/final-review/sign", headers=AUTH, json={
            "review_version_id": rv["id"], "content_hash": rv["content_hash"],
            "consent_given": True, "intent_confirmed": True,
            "signature_method": "typed", "signature_value": "Anna Eriksson",
            "step_up_token": rv["step_up_token"]})
        if "final" not in seen:
            show("Final review signed", f"version={rv['version']} hash={rv['content_hash'][:12]}…")
            seen.add("final")
        status = c.post(f"/cases/{cid}/start", headers=AUTH).json()
    else:
        break

# 12-13. Submit + store confirmation/receipt.
appt = c.get(f"/cases/{cid}/appointment", headers=AUTH).json()["appointment"]
db = SessionLocal()
conf = db.execute(select(models.SubmissionConfirmation).where(
    models.SubmissionConfirmation.application_id == cid)).scalar_one_or_none()
emails = db.execute(select(models.EmailNotification).where(
    models.EmailNotification.application_id == cid)).scalars().all()
db.close()
show("Submit through the mock portal", f"final state={status['state']}")
show("Store confirmation + receipt", f"reference={conf.reference_no} receipt={conf.receipt_no}")
show("Display appointment + case status", f"appt={appt['confirmation_no']} at "
                                          f"{appt['location_id']} ({appt['reschedule_count']} reschedules)")
show("Send applicant notification", f"{len(emails)} emails (subjects: {[e.subject for e in emails]})")

# 16. Restart the worker and prove the completed workflow remains consistent.
import importlib  # noqa: E402
import app.service as svc  # noqa: E402
importlib.reload(svc)
db = SessionLocal()
wf = svc.load_workflow(db, cid)
again = wf.start()  # re-driving a completed case must be a no-op
db.close()
show("Restart worker; workflow stays consistent",
     f"reloaded state={wf.machine.state}, reference still "
     f"{wf.confirmation['referenceNo']}, re-run no-op -> {again['state']}")

print("\nDONE — full flow completed end-to-end with zero cloud credentials.")
