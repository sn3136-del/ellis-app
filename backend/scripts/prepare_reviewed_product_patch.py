"""Prepare an exact-match product correction for review; never writes a database.

The evidence catalog records observations, not a new automated source check.
Only the explicitly listed fields change. Existing products, release metadata,
and issues are outside this tool's authority.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json
import re


class PatchRejected(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def route_identity(route):
    route = dict(route or {})
    # The legacy canonical `default` cache lane is an ordinary passport. An
    # explicit other document is never normalised into that lane.
    route['travel_document_type'] = route.get('travel_document_type') or 'ordinary_passport'
    return tuple(str(route.get(k) or "").strip() for k in
                 ("passport_nationality", "destination_country",
                  "travel_purpose", "travel_document_type"))


def _norm(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _authority_matches(url, route, field):
    from app.visa_snapshot.evidence_validator import jurisdiction_matches
    if jurisdiction_matches(url, route.get("destination_country", "")):
        return True
    # Two narrowly scoped EU companion sources used by this reviewed batch.
    # Neither opens all EU websites to arbitrary destination-policy claims.
    if (url == "https://european-union.europa.eu/principles-countries-history/eu-countries/germany_en"
            and route.get("passport_nationality") == "DEU"
            and route.get("destination_country") == "FRA"
            and field in {"disposition", "requirement_detail", "source_url"}):
        return True
    return (url == "https://home-affairs.ec.europa.eu/document/download/409a8179-9885-49f8-ab40-9c1a07b1f581_en"
            and route.get("passport_nationality") == "RUS"
            and route.get("destination_country") == "ESP" and field == "notes")


def _fee_scope(value, evidence, route, product):
    """Bind fee evidence to the actual named visa and age/procedure tier.

    This bounded converter accepts only the reviewed Schengen/France fee
    contracts; another country's prices or a sibling's age tier cannot supply
    a fee merely because the same number appears on a government page.
    """
    if not isinstance(value, dict) or value.get("currency") != "EUR":
        return False
    name = str((product or {}).get("type") or "").lower()
    purpose = route.get("travel_purpose")
    if purpose == "work" or "long-stay" in name or "vls-ts" in name:
        if route.get("destination_country") != "FRA":
            return False
        for item in evidence:
            if item['source_url'] != 'https://www.france-visas.gouv.fr/documents/d/france-visas/frais-de-visa-anglais':
                continue
            match = re.search(r'Long-stay visa\s+(\d+(?:\.\d+)?) euros', _norm(item['quote']))
            if match and float(match.group(1)) == value.get('amount'):
                return True
        return False
    if route.get('destination_country') not in {'FRA', 'ESP'}:
        return False
    texts = [item['quote'] for item in evidence if item['source_url'] ==
             'https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02009R0810-20240628']
    text = _norm('\n'.join(texts))
    # The rule and its cohort must occur in this field's own evidence.
    if 'child under 6' in name or 'child under six' in name:
        return value.get('amount') == 0 and bool(re.search(
            r'visa fee (?:shall be |is )?waived.{0,110}children under six years', text, re.I))
    if 'child 6-11' in name:
        match = re.search(r'Children from the age of six years and below the age of 12 years shall pay a visa fee of EUR (\d+(?:\.\d+)?)', text)
    elif purpose == 'study' and 'short-stay' in name:
        return value.get('amount') == 0 and bool(re.search(
            r'visa fee (?:shall be |is )?waived.{0,450}school pupils, students, postgraduate students and accompanying teachers who undertake stays for the purpose of study or educational training', text, re.I))
    else:
        match = re.search(r'Applicants shall pay a visa fee of EUR (\d+(?:\.\d+)?)', text)
    return bool(match) and float(match.group(1)) == value.get('amount')


def _decision_scope(value, evidence, sources, route, product):
    """Named ordinary-passport EU/France/Spain proof, not a generic URL gate."""
    if (route.get('travel_document_type') != 'ordinary_passport'
            or route.get('destination_country') not in {'FRA', 'ESP'}):
        return False
    nat, purpose = route.get('passport_nationality'), route.get('travel_purpose')
    def passages(url):
        return '\n'.join(x['quote'] for x in evidence if x['source_url'] == url)
    eu = 'https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02018R1806-20251230'
    law = passages(eu)
    if nat == 'DEU':
        return (value == 'VISA_EXEMPT' and purpose == 'tourism'
                and route['destination_country'] == 'FRA'
                and 'EU Member State: since 1 January 1958' in passages('https://european-union.europa.eu/principles-countries-history/eu-countries/germany_en')
                and 'You can enter and be present for up to 3 months in France without special formalities.' in passages('https://www.service-public.gouv.fr/particuliers/vosdroits/F13512?lang=en'))
    if nat == 'HKG':
        return (value == 'VISA_EXEMPT' and purpose in {'tourism', 'business', 'transit'}
                and 'Nationals of third countries listed in Annex II shall be exempt' in law
                and 'Hong Kong SAR ( 14 )' in law
                and 'Hong Kong Special Administrative Region' in law)
    if value != 'VISA_REQUIRED' or purpose not in {'tourism', 'business', 'family_visit', 'study'}:
        return False
    if 'Nationals of third countries listed in Annex I shall be required to be in possession of a visa' not in law:
        return False
    from app.visa_snapshot.evidence_validator import _NATIONALITY_NAMES
    aliases = _NATIONALITY_NAMES.get(nat, ()) or {'SEN': ('Senegal',)}.get(nat, ())
    matching_table = False
    for item in evidence:
        if item['source_url'] != eu:
            continue
        source_text = sources[item['source_id']]['text']
        start = source_text.find('ANNEX I\nLIST OF THIRD COUNTRIES WHOSE NATIONALS ARE REQUIRED')
        end = source_text.find('ANNEX II', start)
        if start < 0 or end <= start:
            continue
        table = source_text[start:end].strip()
        if _norm(item['quote']) != _norm(table):
            continue
        if any(re.search(r'^\s*' + re.escape(alias) + r'\s*$', table, re.I | re.M) for alias in aliases):
            matching_table = True
    if not matching_table:
        return False
    if route['destination_country'] == 'ESP':
        return purpose == 'tourism' and 'purposes of tourism' in passages('https://www.exteriores.gob.es/Consulados/hongkong/en/ServiciosConsulares/Paginas/Consular/Visados-Schengen.aspx')
    if purpose in {'tourism', 'business', 'family_visit'}:
        return 'This type of visa is generally issued for tourism, business trips or family visits.' in passages('https://www.france-visas.gouv.fr/en/visa-de-court-sejour')
    student = passages('https://www.france-visas.gouv.fr/en/etudiant')
    name = str((product or {}).get('type') or '')
    if 'Long-stay' in name:
        return ('For a training or course of study longer than 3 months, you will be issued a long-stay visa' in student
                and 'For any stay in France exceeding 90 days, you are required to apply in advance for a long-stay' in passages('https://www.france-visas.gouv.fr/en/visa-de-long-sejour'))
    return 'For a training course not exceeding three months, you will be issued a short-stay visa' in student


def _proofs(values, proofs, sources, route=None, product=None):
    from app.visa_snapshot.authority import hostname, is_government_host
    if not isinstance(values, dict) or not isinstance(proofs, dict) or set(values) != set(proofs):
        raise PatchRejected("Every changed field requires its own proof or explicit unknown status")
    for field, value in values.items():
        proof = proofs[field]
        if not isinstance(proof, dict) or proof.get("verifier") != "ai":
            raise PatchRejected("The source review must be explicitly attributed to AI")
        if proof.get("status") == "unknown":
            if value is not None or not proof.get("reason"):
                raise PatchRejected("Unknown values must be null with an explanation")
            continue
        evidence = proof.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise PatchRejected("Missing field evidence")
        for item in evidence:
            source = sources.get(item.get("source_id"))
            if (not source or not source.get("ok") or not source.get("text")
                    or source.get("url") != item.get("source_url")
                    or not is_government_host(hostname(source["url"]))):
                raise PatchRejected("Unread or unofficial field source")
            if route and not _authority_matches(source['url'], route, field):
                raise PatchRejected("Source authority does not cover this destination and field")
            try:
                day = date.fromisoformat(str(source.get("checked_at", ""))[:10])
            except ValueError as exc:
                raise PatchRejected("Invalid source-read date") from exc
            if day > date.today():
                raise PatchRejected("Future source-read date")
            quote = item.get("quote")
            if not isinstance(quote, str) or not quote.strip() or _norm(quote) not in _norm(source["text"]):
                raise PatchRejected("Quoted field evidence is absent from its own captured page")
        if not proof.get("scope_note"):
            raise PatchRejected("Field evidence must state its product and applicability scope")
        if field in {"source_url", "official_portal_url"} and value not in {
                item["source_url"] for item in evidence}:
            raise PatchRejected("The changed source link has not itself been read")
        if field == "source_quote" and not any(
                _norm(value) in _norm(sources[item["source_id"]]["text"])
                for item in evidence):
            raise PatchRejected("The product source quote is not literal source text")
        if route and field == 'disposition' and not _decision_scope(value, evidence, sources, route, product):
            raise PatchRejected("Decision proof does not establish this nationality, document, purpose and product")
        if field in {"government_fee", "fee", "max_stay_days", "permitted_stay_days"} and value is not None:
            from app.visa_snapshot.evidence_validator import field_value_supported
            passages = "\n".join(item["quote"] for item in evidence)
            # Currency spelling changes are mechanical; a zero fee is derived
            # only from an explicit visa-fee waiver. It never implies exemption.
            numeric_text = re.sub(r"\beuros?\b|€", " EUR ", passages, flags=re.I)
            waived = (isinstance(value, dict) and value.get("amount") == 0
                      and bool(re.search(r"visa fee (?:shall be |is )?waived", passages, re.I)))
            if not waived and not field_value_supported(field, value, numeric_text):
                raise PatchRejected("Numeric field is not supported by its own quoted evidence: " + field)
            if route and field in {'government_fee', 'fee'} and not _fee_scope(value, evidence, route, product):
                raise PatchRejected("Fee evidence belongs to another product, age tier or procedure")


def prepare(guidance, route, entry, sources):
    """Return a detached candidate and scoped provenance; no release or TTL changes.

    A blocked route cannot be prepared for publication. The manifest retains
    its proposed work and original products for the reviewer to resolve.
    """
    from app.visa_snapshot.verified_overrides import OVERRIDABLE, _field_errors
    if not isinstance(guidance, dict) or not isinstance(entry, dict):
        raise PatchRejected("Expected guidance and a patch entry")
    if route_identity(route) != route_identity(entry.get("route") or {}):
        raise PatchRejected("Nationality, destination, purpose or document differs")
    route = dict(route, travel_document_type=route.get('travel_document_type') or 'ordinary_passport')
    if entry.get("publication_blocked"):
        raise PatchRejected("Unresolved product or entry facts require review before publication")
    fields = entry.get("fields") or {}
    if set(fields) - (set(OVERRIDABLE) - {"visa_products", "confidence"}):
        raise PatchRejected("Patch contains fields outside its correction authority")
    expected_fields = entry.get("expected_fields") or {}
    if set(expected_fields) != set(fields):
        raise PatchRejected("Changed route fields need exact preconditions")
    for field, expected in expected_fields.items():
        actual = {"present": field in guidance, "value": guidance.get(field)}
        if actual != expected:
            raise PatchRejected("Route field changed since the reviewed baseline: " + field)
    _proofs(fields, entry.get("field_provenance") or {}, sources, route)
    products = guidance.get("visa_products") or []
    if not isinstance(products, list) or any(not isinstance(p, dict) for p in products):
        raise PatchRejected("Malformed existing product list")
    candidate = deepcopy(guidance)
    candidate.update(deepcopy(fields))
    patches = entry.get("product_patches") or []
    seen = set()
    for patch in patches:
        name = (patch.get("match") or {}).get("type")
        positions = [i for i, p in enumerate(products) if p.get("type") == name]
        if len(positions) != 1 or positions[0] in seen:
            raise PatchRejected("Product match is missing, ambiguous or repeated: " + str(name))
        pos = positions[0]
        seen.add(pos)
        if digest(products[pos]) != patch.get("expected_product_sha256"):
            raise PatchRejected("Product changed since the reviewed baseline: " + str(name))
        updates = patch.get("fields") or {}
        if "type" in updates and updates["type"] != name:
            raise PatchRejected("Product identity cannot be silently renamed")
        if any(k in updates for k in ("held", "operator_released", "confidence", "verification",
                                      "fresh_until", "generated_at")):
            raise PatchRejected("Product patch cannot release or renew a record")
        _proofs(updates, patch.get("field_provenance") or {}, sources, route, products[pos])
        candidate.setdefault("visa_products", deepcopy(products))[pos].update(deepcopy(updates))
        prior_proofs = candidate["visa_products"][pos].get("field_provenance") or {}
        candidate["visa_products"][pos]["field_provenance"] = {
            **deepcopy(prior_proofs), **deepcopy(patch["field_provenance"])}
    # Null/zero fees and unknown details remain distinct from visa exemption.
    errors = _field_errors(candidate)
    if errors:
        raise PatchRejected("Candidate schema or verdict conflict: " + "; ".join(errors))
    return {"guidance": candidate,
            "field_provenance": deepcopy(entry.get("field_provenance") or {}),
            "product_field_provenance": {p["match"]["type"]: deepcopy(p["field_provenance"])
                                         for p in patches},
            "source_review": {"kind": "reviewed_product_patch", "verifier": "ai",
                              "reviewed_at": entry.get("reviewed_at"),
                              "new_grounded_check": False, "new_release": False,
                              "renew_fresh_until": False},
            "unchanged_product_count": len(products) - len(patches)}


def prepare_layers(raw_guidance, merged_guidance, route, entry, sources,
                   current_override_entries):
    """Validate both current layers before preparing their detached candidate.

    The caller supplies its actual current merge and scoped override entries.
    This function performs no I/O and no write; a later integrator must retain
    these preconditions in its atomic transaction. An old overlay must never
    silently shadow a corrected raw row, or lose its additional products.
    """
    baseline = entry.get("baseline") or {}
    if digest(raw_guidance) != baseline.get("raw_guidance_sha256"):
        raise PatchRejected("Raw guidance changed since the reviewed baseline")
    expected = sorted(x["entry_sha256"] for x in baseline.get("override_entries", []))
    if sorted(digest(x) for x in current_override_entries) != expected:
        raise PatchRejected("Override layer changed since the reviewed baseline")
    if digest(merged_guidance) != baseline.get("effective_guidance_sha256"):
        raise PatchRejected("Merged guidance changed since the reviewed baseline")
    return prepare(merged_guidance, route, entry, sources)
