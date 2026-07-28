"""Phase 6 — dynamic translation (token/identifier preservation, caching,
honest unavailability) + assistant identity (always Ellis, never the model,
never a government official/lawyer/embassy)."""
from tests.conftest import AUTH
from app import i18n


def _fake_translator(masked, target, source):
    # A stand-in that "translates" by tagging — the point is that it must not
    # touch the ⟦T…⟧ sentinels, so we can prove the tokens survive.
    return f"[{target}] " + masked


def test_protect_and_restore_roundtrip():
    text = ("Upload passport.pdf before 2026-10-10; the fee is $80. "
            "See https://evisa.gov.vn and enter passport L898902C3 for {name}.")
    masked, mapping = i18n.protect_tokens(text)
    # None of the protected tokens remain in the masked text.
    for tok in ("passport.pdf", "2026-10-10", "$80", "https://evisa.gov.vn", "L898902C3", "{name}"):
        assert tok not in masked, tok
    # Restoring returns the exact original.
    assert i18n.restore_tokens(masked, mapping) == text


def test_translate_preserves_identifiers_and_urls():
    text = "Enter passport L898902C3 at https://evisa.gov.vn by 2026-10-10 ({ref})."
    out = i18n.translate(text, "zh-CN", "en", translator=_fake_translator)
    assert out["status"] == "ok"
    # Identifiers / URLs / dates / placeholders are intact in the translation.
    for tok in ("L898902C3", "https://evisa.gov.vn", "2026-10-10", "{ref}"):
        assert tok in out["translated"], tok


def test_numeric_passport_number_is_masked_regression():
    # REGRESSION (review-confirmed): a purely-numeric passport/ID number (e.g. a
    # US 9-digit passport) must be masked, not sent verbatim to the translator.
    text = "Your passport 123456789 and ID 87654321 were received."
    masked, mapping = i18n.protect_tokens(text)
    assert "123456789" not in masked and "87654321" not in masked
    assert i18n.restore_tokens(masked, mapping) == text


def test_lowercase_alphanumeric_identifier_is_masked_regression():
    masked, mapping = i18n.protect_tokens("passport ab1234567 received")
    assert "ab1234567" not in masked
    assert i18n.restore_tokens(masked, mapping) == "passport ab1234567 received"


def test_sentinel_collision_is_safe_regression():
    # REGRESSION: text that already contains a sentinel-shaped substring must not
    # be corrupted on restore.
    text = "Ref ⟦T0⟧ and passport E12345678 at https://x.io"
    masked, mapping = i18n.protect_tokens(text)
    assert i18n.restore_tokens(masked, mapping) == text


def test_translate_caches_repeats():
    i18n.clear_cache()
    a = i18n.translate("Please review your details.", "zh-CN", "en", translator=_fake_translator)
    b = i18n.translate("Please review your details.", "zh-CN", "en", translator=_fake_translator)
    assert a["cached"] is False and b["cached"] is True
    assert a["translated"] == b["translated"]


def test_translate_unsupported_language():
    # French is a SUPPORTED dynamic language now (2026-07-27); a made-up code
    # is still refused honestly.
    out = i18n.translate("hello", "xx-QQ", "en", translator=_fake_translator)
    assert out["status"] == "unsupported_language"
    assert out["translated"] == "hello"
    assert "fr" in i18n.SUPPORTED_LANGS and "ar" in i18n.SUPPORTED_LANGS


def test_translate_catalog_masks_caches_and_degrades_honestly():
    i18n.clear_cache()
    calls = []

    def batch(items, target, source):
        calls.append(dict(items))
        out = {}
        for k, v in items.items():
            if k == "drops":
                continue                     # the model lost this one
            out[k] = f"[{target}] {v}"       # sentinels preserved verbatim
        return out

    entries = {"hello": "Hello {name}", "go": "Continue", "drops": "Lost in space"}
    r = i18n.translate_catalog(entries, "fr", batch_translator=batch)
    # Lost string keeps its ENGLISH original and the status says so.
    assert r["status"] == "partial"
    assert r["entries"]["drops"] == "Lost in space"
    # The {name} placeholder survived masking + restore byte-for-byte.
    assert "{name}" in r["entries"]["hello"]
    assert r["entries"]["go"].startswith("[fr] ")
    # Second call: everything translated comes from cache — no model calls
    # for those strings.
    r2 = i18n.translate_catalog({"hello": "Hello {name}", "go": "Continue"}, "fr",
                                batch_translator=batch)
    assert r2["status"] == "ok" and len(calls) == 1


def test_translate_catalog_chunks_large_catalogs():
    # Chunks run CONCURRENTLY (a 600-string catalog took minutes when they
    # were sequential), so assert the PARTITION, never the call order.
    i18n.clear_cache()
    import threading
    lock = threading.Lock()
    sizes = []

    def batch(items, target, source):
        with lock:
            sizes.append(len(items))
        return {k: f"[{target}] {v}" for k, v in items.items()}

    entries = {f"k{i}": f"String number {i}" for i in range(95)}
    r = i18n.translate_catalog(entries, "es", batch_translator=batch)
    assert r["status"] == "ok" and len(r["entries"]) == 95
    assert sorted(sizes) == [15, 40, 40]
    # Every key survives the concurrent merge exactly once.
    assert r["entries"]["k94"] == "[es] String number 94"


def test_translate_catalog_survives_one_failing_chunk():
    # One chunk erroring must not lose the others: its strings stay English
    # and the status says 'partial' rather than pretending success.
    i18n.clear_cache()

    def batch(items, target, source):
        if "k0" in items:
            raise RuntimeError("model hiccup")
        return {k: f"[{target}] {v}" for k, v in items.items()}

    entries = {f"k{i}": f"String number {i}" for i in range(95)}
    r = i18n.translate_catalog(entries, "es", batch_translator=batch)
    assert r["status"] == "partial"
    assert r["entries"]["k0"] == "String number 0"          # honest English
    assert r["entries"]["k94"] == "[es] String number 94"   # others translated


def test_translate_passthrough_same_language():
    out = i18n.translate("hello", "en", "en", translator=_fake_translator)
    assert out["status"] == "passthrough"


def test_translate_unavailable_is_honest_not_fabricated():
    # No live Kimi in hermetic tests → the default translator is unavailable and
    # we return the ORIGINAL text, never a fabricated translation.
    i18n.clear_cache()
    original = "This exact phrase is only used by the unavailability test."
    out = i18n.translate(original, "zh-CN", "en")
    assert out["status"] == "unavailable"
    assert out["translated"] == original


def test_identity_question_detection():
    for q in ("What is your name?", "who are you", "Introduce yourself",
              "你是谁", "你叫什么名字", "介绍一下你自己", "你是誰"):
        assert i18n.is_identity_question(q) is True
    assert i18n.is_identity_question("What documents do I need?") is False


def test_assistant_identity_is_ellis_in_every_language():
    for lang in ("en", "zh-CN", "zh-Hant"):
        ans = i18n.assistant_identity_answer(lang)
        assert "Ellis" in ans
        low = ans.lower()
        assert "kimi" not in low and "moonshot" not in low
    # English answer disclaims official/lawyer/embassy roles.
    en = i18n.assistant_identity_answer("en").lower()
    assert "government official" in en and "lawyer" in en and "embassy" in en


def test_system_identity_forbids_model_disclosure_and_official_claims():
    s = i18n.ELLIS_SYSTEM_IDENTITY.lower()
    assert "ellis" in s
    assert "kimi" in s and "moonshot" in s          # explicitly named as forbidden
    assert "government official" in s and "lawyer" in s and "embassy" in s


# ---- endpoints ----
def test_languages_endpoint(client):
    langs = client.get("/i18n/languages", headers=AUTH).json()["languages"]
    codes = {l["code"] for l in langs}
    # The dynamic-language rollout (2026-07-27) supersets the static three.
    assert {"en", "zh-CN", "zh-Hant", "fr", "es", "ar"} <= codes


def test_identity_endpoint(client):
    r = client.get("/assistant/identity", headers=AUTH, params={"lang": "zh-CN"}).json()
    assert r["name"] == "Ellis" and r["lang"] == "zh-CN"
    assert "Ellis" in r["answer"]


def test_translate_endpoint_unavailable_returns_original(client):
    r = client.post("/i18n/translate", headers=AUTH,
                    json={"text": "Please review.", "target_lang": "zh-CN"}).json()
    assert r["status"] == "unavailable" and r["translated"] == "Please review."
