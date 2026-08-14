"""The named human acts each prepared filing leaves, and the tap count they imply.

One authority. Every payload a filing cockpit renders gets its ``human_acts``
and ``taps_to_done`` from here, so the number beside the button and the acts
listed under it can never disagree: the tap count IS the count of the acts.
Nothing is transcribed from a spec document, and the frontend keeps no copy.

Act keys are pinned to the UI's localized vocabulary (cockpit.act.{key}):
login, review, sign, pay, submit, captcha, biometrics. The renderer localizes
the visible sentence from the key; ``who`` / ``why`` / ``ellis_does`` ride
alongside as the server's own words.

``conditional`` marks an act the portal may or may not demand (a CAPTCHA, a
fee screen that only some routes show). taps_to_done counts unconditional
acts as ``min`` and all acts as ``max`` — a range is the honest answer when
the portal itself decides.

A filing with no portal (the public access file) or no taps (a mailed paper
packet) reports ``known: False`` with the reason, never an invented number.
"""
from __future__ import annotations

# The UI's act vocabulary (visaBackend.js HUMAN_ACT_KEYS). A key outside this
# tuple would render as a blank line in the cockpit; the test pins the match.
ACT_VOCABULARY = ("login", "review", "sign", "pay", "submit", "captcha",
                  "biometrics")


def _act(key: str, who: str, why: str, ellis_does: str, *,
         conditional: bool = False, non_delegable: bool = False) -> dict:
    assert key in ACT_VOCABULARY, key
    return {"key": key, "who": who, "why": why, "ellis_does": ellis_does,
            "conditional": conditional, "non_delegable": non_delegable}


# ---------------------------------------------------------------- registries

def _flag_acts(filing: str) -> list[dict]:
    """FLAG filings (ETA-9035 LCA, ETA-9141 PWD): the employer's account,
    the employer's statements, the employer's submission."""
    return [
        _act("login", "the employer's authorized user, in their own FLAG "
             "account",
             "the account is personal under the portal's terms of use; "
             "credentials are never shared with Ellis",
             "prepares every answer the form asks, each with its source",
             non_delegable=True),
        _act("review", "the employer",
             f"every statement on the {filing} is the employer's own "
             "declaration to the Department of Labor",
             "shows each prepared value beside the question it answers"),
        _act("submit", "the employer's authorized user",
             "filing is the employer's legal act, not a technical step",
             "stops at the point of submission, always"),
    ]


_REGISTRY: dict[str, list[dict]] = {
    "eta-9035": _flag_acts("LCA"),
    "eta-9141": _flag_acts("ETA-9141"),
    "eta-9141-2021": _flag_acts("ETA-9141"),
    "i-129": [
        _act("login", "the petitioner's authorized signatory, in the "
             "organization's own myUSCIS account",
             "the myUSCIS terms of use bind the account holder personally",
             "prepares the petition and every supporting answer",
             non_delegable=True),
        _act("sign", "the authorized signatory",
             "the I-129 declaration is made under penalty of perjury "
             "(28 U.S.C. 1746)",
             "leaves every signature line empty, on paper and on screen",
             non_delegable=True),
        _act("pay", "the petitioner",
             "Ellis never enters card or bank details for anyone",
             "states the exact fees and their legal basis"),
        _act("submit", "the authorized signatory",
             "filing the petition with USCIS is the petitioner's legal act",
             "stops at the point of submission, always"),
    ],
    "registration": [
        _act("login", "the petitioner's authorized signatory, in the "
             "organization's own myUSCIS account",
             "the myUSCIS terms of use bind the account holder personally",
             "prepares each beneficiary's registration data",
             non_delegable=True),
        _act("pay", "the petitioner",
             "Ellis never enters card or bank details for anyone",
             "states the registration fee per beneficiary"),
        _act("submit", "the authorized signatory",
             "the registration attestation is the petitioner's own",
             "stops at the point of submission, always"),
    ],
    # The public access file is the employer's OWN records file (20 CFR
    # 655.760): nothing is filed with any government, so the one act left is
    # the employer's review and retention of their own file.
    "paf": [
        _act("review", "the employer",
             "the file is the employer's own record; keeping it accurate and "
             "available is the employer's regulatory duty",
             "assembles each required item with its citation and an honest "
             "status"),
    ],
    # The mailed I-129 packet: wet ink, a check or G-1450, an envelope. Human
    # acts, not taps — taps_to_done reports unknown with the reason.
    "i-129-paper": [
        _act("sign", "the authorized signatory, in wet ink",
             "USCIS rejects unsigned paper filings; the declaration is made "
             "under penalty of perjury",
             "marks every line that needs ink on the cover checklist",
             non_delegable=True),
        _act("pay", "the petitioner, by check or Form G-1450",
             "Ellis never writes a check or enters card details",
             "states the exact fees the packet must carry"),
        _act("submit", "the petitioner",
             "mailing the packet to the lockbox is the filing itself",
             "prints the verified lockbox address on the cover sheet"),
    ],
    # Consular / tourist forms, keyed by the form spec's own submission mode.
    "consular-in-person": [
        _act("review", "the applicant",
             "the answers are the applicant's own statements",
             "fills the government's blank from the applicant's answers"),
        _act("sign", "the applicant, by hand",
             "the printed form's signature fields belong to the applicant",
             "marks where to sign and leaves the lines empty",
             non_delegable=True),
        _act("submit", "the applicant, in person",
             "lodging the application is the applicant's own act",
             "prepares the complete packet to bring"),
    ],
    "consular-online": [
        _act("login", "the applicant, in their own account on the official "
             "site",
             "government accounts are personal; credentials never pass "
             "through Ellis",
             "prepares every value ready to copy in",
             non_delegable=True),
        _act("review", "the applicant",
             "the answers are the applicant's own statements",
             "shows each value beside the official question it answers"),
        # The electronic declaration (a DS-160's "Sign and Submit" is a
        # signature in law). Conditional because some portals fold it into the
        # submit click rather than asking separately; non-delegable wherever
        # it appears.
        _act("sign", "the applicant",
             "the electronic declaration is the applicant's own signature",
             "leaves the declaration untouched, always",
             conditional=True, non_delegable=True),
        _act("pay", "the applicant",
             "Ellis never enters card or bank details for anyone",
             "states the official fee where the route publishes one",
             conditional=True),
        _act("captcha", "the applicant",
             "a human check exists to be cleared by a human; evading one "
             "risks the applicant's own application",
             "waits", conditional=True, non_delegable=True),
        _act("submit", "the applicant",
             "sending the application is the applicant's own act",
             "stops at the point of submission, always"),
    ],
}

# Filings whose completion is not a sequence of portal taps. ``taps_to_done``
# for these is honestly unknown, with the reason stated. Both consular routes
# belong here: an in-person lodging is ink, an appointment and biometrics, and
# the online prep sheet leaves the applicant to work the official site
# themselves — Ellis drives neither, so any count would be an invention.
_NO_TAPS_REASON = {
    "paf": ("kept in the employer's own records — nothing is filed with any "
            "government, so there is no portal and no tap count"),
    "i-129-paper": ("filed by mail — signatures, payment and the envelope "
                    "are acts, not taps"),
    "consular-in-person": ("lodged in person on paper — printing, wet-ink "
                           "signing and appearing are acts, not taps"),
    "consular-online": ("the applicant completes the official site "
                        "themselves from Ellis's prep sheet — Ellis does not "
                        "drive that portal, so there is no tap count to "
                        "promise"),
}


def acts_for(filing_key: str) -> list[dict]:
    """The named human acts for this filing. Unknown key -> empty list: an
    act is never invented for a filing this module has not studied."""
    return [dict(a) for a in _REGISTRY.get(filing_key, [])]


def consular_key(submission: str) -> str:
    """Map a consular form spec's submission mode to its acts key."""
    return ("consular-in-person" if submission == "in_person"
            else "consular-online")


def taps_to_done(filing_key: str) -> dict:
    """The tap count, counted from the acts themselves. min = acts that are
    always demanded; max = all acts including the portal-conditional ones."""
    if filing_key in _NO_TAPS_REASON:
        return {"known": False, "reason": _NO_TAPS_REASON[filing_key]}
    acts = _REGISTRY.get(filing_key)
    if not acts:
        return {"known": False,
                "reason": "no measured act list for this filing"}
    total = len(acts)
    unconditional = sum(1 for a in acts if not a["conditional"])
    return {"known": True, "min": unconditional, "max": total,
            "basis": "counted from the named human acts of this filing"}
