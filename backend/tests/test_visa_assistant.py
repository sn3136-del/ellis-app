"""The visa Q&A composer: grounding by sentence, and a facts-only reply for
the turns the composer cannot serve."""
from app.visa_snapshot import assistant


def test_grounding_drops_only_the_sentence_with_a_foreign_number():
    allowed = {"30", "25"}
    reply = ("You need an e-visa before travel. It costs 25 USD and allows 30 days. "
             "Your passport must be valid for 6 months. Apply on the official portal.")
    out = assistant._ground_sentences(reply, allowed)
    assert "6 months" not in out
    assert "25 USD" in out and "30 days" in out and "official portal" in out
    # Nothing survives when every sentence carries an invented figure.
    assert assistant._ground_sentences("Fee is 99 USD.", allowed) is None
    # Chinese sentences split on their own full stops.
    zh = assistant._ground_sentences("费用为25美元。护照需有效6个月。可停留30天。", allowed)
    assert zh == "费用为25美元。可停留30天。"


def test_compose_reply_keeps_a_grounded_reply_with_one_stray_sentence(monkeypatch):
    from app.visa_snapshot import kimi_primary
    monkeypatch.setattr(kimi_primary, "_call", lambda *a, **k: {
        "reply": "You need a visa. The fee is 715 CNY. Bring a passport valid for 6 months."})
    out = {"guidance": {"disposition": "VISA_REQUIRED",
                        "government_fee": {"amount": 715, "currency": "CNY"}},
           "route": {"nationality": "CHN", "destination": "JPN"}}
    reply = assistant.compose_reply("Do I need a visa for Japan?", [], out, "en")
    assert reply == "You need a visa. The fee is 715 CNY."


def test_fallback_reply_speaks_from_the_facts_in_the_site_language():
    out = {"guidance": {"disposition": "VISA_REQUIRED",
                        "government_fee": {"amount": 715, "currency": "CNY"},
                        "permitted_stay": "15 days",
                        "processing_time": "5 working days",
                        "application_channel_detail": "Lodge through a designated agency."},
           "route": {"nationality": "CHN", "destination": "JPN"}, "focus": "fee"}
    en = assistant.fallback_reply(out, "how much is the japan visa", "en")
    assert en.startswith("A visa is required before you travel. The government fee is 715 CNY.")
    assert "15 days" in en and "designated agency" in en and en.endswith("The full record is shown below.")
    zh = assistant.fallback_reply(out, "日本签证多少钱", "zh")
    assert zh.startswith("出行前需要办理签证。政府费用为 715 CNY。") and zh.endswith("详情见下方完整记录。")
    tw = assistant.fallback_reply(out, "日本簽證多少錢", "zh-TW")
    assert "辦理" in tw and "詳情見下方完整記錄" in tw
    held = assistant.fallback_reply({"held": True, "route": {}}, "visa for bali", "en")
    assert held.startswith("We are checking this route")
    assert assistant.fallback_reply({"guidance": {}, "route": {}}, "x", "en") is None
