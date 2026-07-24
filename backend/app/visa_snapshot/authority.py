"""Official-source authority classification for snapshot evidence.

A source is authoritative ONLY when (brief section 12):
  - its final hostname is an official government / immigration / foreign-
    ministry / embassy / consular domain (proper suffix match), OR
  - an official government page (itself on a government domain) directly links
    to the exact external contractor (official_linking_source evidence).

Everything else is non_authoritative and can never verify a rule.
"""
from __future__ import annotations

from urllib.parse import urlparse

# Government second-level suffix patterns used worldwide. Proper suffix match
# only (host == suffix or host endswith "." + suffix). This is a curated,
# versioned allowlist — additions require evidence review.
GOV_SUFFIXES = (
    # generic
    "gov", "mil",
    # gov.<cc> / gob.<cc> / gouv.<cc> / go.<cc> and country-specific forms
    *[f"gov.{cc}" for cc in (
        "uk", "au", "cn", "hk", "mo", "tw", "sg", "my", "ph", "vn", "bd", "pk", "lk",
        "np", "mm", "kh", "la", "bn", "in", "il", "tr", "sa", "ae", "qa", "kw", "bh",
        "om", "jo", "eg", "ma", "dz", "tn", "ly", "ng", "gh", "ke", "tz", "ug", "rw",
        "et", "za", "bw", "na", "zm", "zw", "mw", "mz", "sc", "mu", "fj", "pg", "ws",
        "to", "vu", "sb", "ki", "fm", "mh", "pw", "nr", "tv", "ck", "nu", "ir", "iq",
        "sy", "ye", "af", "kz", "kg", "uz", "tm", "tj", "az", "ge", "am", "by", "ru",
        "ua", "md", "rs", "me", "mk", "al", "ba", "bg", "ro", "gr", "cy", "mt", "ie",
        "it", "es", "pt", "pl", "cz", "sk", "hu", "si", "hr", "lv", "lt", "ee", "fi",
        "se", "no", "dk", "is", "nl", "be", "lu", "li", "at", "ch", "de", "fr", "mc",
        "sm", "ad", "va", "gi", "je", "gg", "im", "bm", "ky", "vg", "tc", "ai", "ms",
        "fk", "sh", "ag", "bb", "bs", "bz", "dm", "gd", "gy", "jm", "kn", "lc", "vc",
        "sr", "tt", "cu", "do", "ht", "mx", "gt", "hn", "sv", "ni", "cr", "pa", "co",
        "ve", "ec", "pe", "bo", "py", "uy", "ar", "cl", "br", "ca", "us", "kr", "jp",
        "th", "id", "mn", "bt", "mv", "tl", "ao", "cd", "cg", "cm", "cf", "td", "ne",
        "ml", "bf", "ci", "sn", "gm", "gw", "gn", "sl", "lr", "tg", "bj", "ga", "gq",
        "st", "cv", "km", "dj", "so", "sd", "ss", "er", "bi", "ls", "sz", "mg", "ps",
        "kp", "sb", "ws",
    )],
    *[f"gob.{cc}" for cc in ("mx", "ar", "cl", "pe", "bo", "ec", "es", "sv", "hn",
                             "ni", "pa", "do", "gt", "ve", "cu", "py")],
    *[f"gouv.{cc}" for cc in ("fr", "sn", "ci", "bj", "ml", "bf", "td", "mc", "nc",
                              "qc.ca", "ht", "mg", "km", "cm", "ga")],
    *[f"go.{cc}" for cc in ("jp", "kr", "th", "id", "tz", "ke", "ug", "cr", "tj")],
    # country-specific official patterns
    "govt.nz", "gc.ca", "canada.ca", "admin.ch", "belgium.be", "europa.eu",
    "government.nl", "rijksoverheid.nl", "overheid.nl", "government.se",
    "regeringen.se", "government.no", "regjeringen.no", "um.dk", "gov.gl",
    "government.is", "government.ru", "kdmid.ru", "mid.ru", "mfa.gov",
    "state.gov", "usembassy.gov", "travel.state.gov", "gov.br", "gov.co", "gov.pl",
    "government.bg", "gov.lv", "gov.si", "gov.cz", "gov.hu", "gov.hr", "gov.rs",
    "bund.de", "auswaertiges-amt.de", "diplo.de", "diplomatie.gouv.fr",
    "esteri.it", "exteriores.gob.es", "mofa.go.jp", "mofa.go.kr", "mofa.gov",
    "fmprc.gov.cn", "mfa.gov.cn", "immd.gov.hk", "boca.gov.tw", "mofa.gov.tw",
    "ica.gov.sg", "mfa.gov.sg", "homeaffairs.gov.au", "immigration.govt.nz",
    "cic.gc.ca", "ircc.canada.ca", "uscis.gov", "cbp.gov", "dhs.gov",
)

AUTHORITY_KINDS = (
    "destination_government", "destination_foreign_ministry",
    "embassy_or_consulate", "official_application_portal",
    "authorized_visa_center", "licensed_structured_provider",
    "human_reviewed_evidence", "non_authoritative",
)


def hostname(url: str) -> str:
    return (urlparse(url).netloc or url).split("@")[-1].split(":")[0].lower()


def is_government_host(host: str) -> bool:
    host = (host or "").lower()
    return any(host == s or host.endswith("." + s) for s in GOV_SUFFIXES)


def registrable_domain(host: str) -> str:
    """Best-effort registrable domain (one label above the effective TLD). For a
    government host the effective TLD is the matched GOV_SUFFIX (e.g. gov.cn,
    gob.mx), so us.china-embassy.gov.cn -> china-embassy.gov.cn and
    www.inm.gob.mx -> inm.gob.mx. Used to keep internal-link following on the
    same official site."""
    host = (host or "").lower().strip(".")
    if not host:
        return ""
    # Longest matching gov suffix wins (gov.cn beats a bare 'cn').
    suffix = ""
    for s in sorted(GOV_SUFFIXES, key=len, reverse=True):
        if host == s or host.endswith("." + s):
            suffix = s
            break
    if suffix:
        if host == suffix:
            return host
        label = host[: -(len(suffix) + 1)].rsplit(".", 1)[-1]
        return f"{label}.{suffix}" if label else host
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def classify_evidence(evidence: dict) -> str:
    """Return the verification status for one evidence record:
    'verified' (official domain), 'verified_via_official_link' handled by the
    caller when official_linking_source is itself a government URL, else
    'unverified'. Never trusts the record's own source_authority claim."""
    host = evidence.get("final_hostname") or hostname(evidence.get("final_url", ""))
    if is_government_host(host):
        return "verified"
    link = evidence.get("official_linking_source") or ""
    if link and is_government_host(hostname(link)):
        return "verified"   # contractor endorsed by an official page
    return "unverified"
