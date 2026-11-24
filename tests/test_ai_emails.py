"""AI-written email bodies: Products tab, per-person briefs, checks, review store, sending."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import personal as pz  # noqa: E402
from crisp_gui import ai_emails as ae  # noqa: E402
from test_api_sending import FakeAPI, api_agent  # noqa: E402,F401
from test_v36 import agent, write  # noqa: E402,F401

PRODUCTS = [["Plugin", "What it does", "Pro price", "Offer", "Coupon", "Link"],
            ["WowSubscriptions", "Sell subscriptions", "$79", "30% off", "SUBS30", "https://wpxpo.com/subs"],
            ["WowRevenue", "Upsells and bundles", "$59", "40% off", "REV40", "https://wpxpo.com/rev"]]
LIST = [["Full Name", "Email", "Plan", "Plugins", "Order Value", "Mail Sent"],
        ["Ann Lee", "ann@shop.com", "Pro", "WowStore, WowInvoice", "$300", ""],
        ["Bo Chan", "bo@x.com", "Free", "PostX", "-", ""]]
GOOD = ("Since you run WowStore and WowInvoice, WowSubscriptions lets you sell subscription boxes and "
        "memberships with recurring payments, right inside your store. Your customers pay automatically each "
        "month and you keep them for longer. Existing customers get 30% off with SUBS30 this week only. "
        "[See WowSubscriptions](https://wpxpo.com/subs)")


def tokens():
    return ae.allowed_tokens(ae.products_from_grid(PRODUCTS), "Hi {first_name}")


def test_checks_only_allow_what_the_products_tab_has():
    links, toks = tokens()
    assert ae.check_body(GOOD, links, toks) == ""
    assert "link" in ae.check_body(GOOD.replace("/subs", "/other"), links, toks)
    assert "price" in ae.check_body(GOOD.replace("30%", "50%"), links, toks)
    assert "coupon" in ae.check_body(GOOD.replace("SUBS30", "FREE2026"), links, toks)
    assert ae.check_body("Short text.", links, toks) == "too short"
    assert "placeholders" in ae.check_body(GOOD + " {first_name}", links, toks)


def test_brief_has_plan_and_plugins_but_never_the_email():
    index, _ = pz.build_index(LIST)
    brief = ae.person_brief(0, "ann@shop.com", "Ann Lee", index["ann@shop.com"])
    assert brief["data"]["Plan"] == "Pro" and brief["plugins_in_use"] == ["WowStore", "WowInvoice"]
    assert "ann@shop.com" not in json.dumps(brief) and "Mail Sent" not in brief["data"]
    assert ae.owned_pro(index["ann@shop.com"]) == ["WowStore", "WowInvoice"]
    assert ae.owned_pro(index["bo@x.com"]) == []


def test_generate_writes_and_flags_per_person():
    index, _ = pz.build_index(LIST)
    answer = {"emails": [{"id": 0, "offer": "WowSubscriptions", "body": GOOD},
                         {"id": 1, "offer": "WowRevenue", "body": GOOD.replace("SUBS30", "MADEUP99")}]}
    seen = []

    def http(method, url, key, body):
        seen.append(body)
        return {"choices": [{"message": {"content": json.dumps(answer)}, "finish_reason": "stop"}]}
    out = ae.generate([("ann@shop.com", "Ann Lee"), ("bo@x.com", "Bo Chan")], index,
                      ae.products_from_grid(PRODUCTS), "Offer", "Hi {first_name},\n\n{ai_email}\n\nOwadud",
                      settings={"provider": "openai", "api_key": "k", "model": "m", "base_url": ""}, http=http)
    assert out["ann@shop.com"]["check"] == "" and out["ann@shop.com"]["offer"] == "WowSubscriptions"
    assert "coupon" in out["bo@x.com"]["check"]
    sent = json.dumps(seen[0])
    assert "WowSubscriptions" in sent and "ann@shop.com" not in sent


def test_store_keeps_approved_and_counts(tmp_path):
    st = ae.EmailStore(tmp_path, "BF")
    st.merge({"a@x.com": {"body": GOOD, "offer": "W", "check": ""}, "b@x.com": {"body": "x", "check": "too short"}})
    assert st.counts(["a@x.com", "b@x.com", "c@x.com"]) == (1, 1, 1)
    st.approve("b@x.com", "Fixed text by hand.")
    st.merge({"b@x.com": {"body": "AI again", "check": ""}})
    assert st.get("b@x.com") == "Fixed text by hand." and st.ready("b@x.com")


def test_only_ready_ai_emails_are_sent(api_agent, tmp_path):
    path = write(tmp_path / "r.csv", ["Full Name", "Contact Email", "Reference", "Mail Sent"],
                 [["Ann Lee", "ann@x.com", "Shahriar", "FALSE"], ["Bo Chan", "bo@x.com", "Shahriar", "FALSE"]])
    store = tmp_path / "ai.json"
    store.write_text(json.dumps({"ann@x.com": {"body": "Ann's own offer.", "check": "", "state": "generated"},
                                 "bo@x.com": {"body": "Bad.", "check": "too short", "state": "generated"}}),
                     encoding="utf-8")
    mod = api_agent(path, messages=("Hi {first_name},\nBest, Owadud",), CRISP_AI_EMAILS=str(store))
    mod.main()
    texts = [c[2] for c in FakeAPI.calls if c[0] == "send"]
    assert texts == ["Hi Ann,\n\nAnn's own offer.\n\nBest, Owadud"]


def test_lists_shows_flagged_emails_and_double_click_approves(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import test_gui as tg
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from crisp_gui.config import Preferences
    from crisp_gui.main_window import LISTS, MainWindow
    from crisp_gui.theme import ThemeManager
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("CRISP_MAIL_CONFIG", str(tmp_path / "mail.json"))
    prefs = Preferences(QSettings(str(tmp_path / "p.ini"), QSettings.IniFormat))
    prefs.set_campaign_value("csv", str(tg.make_csv(tmp_path / "list.csv", n=2)))
    w = MainWindow(ThemeManager(app, "light"), prefs, agent_script=tg.FAKE, data_dir=tmp_path / "data")
    try:
        c = w.campaign
        c.show_step("message")                    # the AI options are in step 2
        w._send_mode_changed("api")
        c.api_message.setPlainText("Hi {first_name},\n\nOwadud")
        c.ai_enabled.setChecked(True)
        c.ai_kind.set_value("body")
        w._ai_kind_changed("body")
        assert c.ai_products.isVisibleTo(c) and w.campaign_values()["ai_emails_file"]
        store = w._ai_email_store()
        store.merge({"u0@example.com": {"body": GOOD, "offer": "WowSubscriptions", "check": ""},
                     "u1@example.com": {"body": "Too short.", "offer": "WowRevenue", "check": "too short"}})
        w.navigate(LISTS)
        w.refresh_lists()
        cells = {r[1]: r[-1] for r in w.list_rows.rows}
        assert cells["u0@example.com"].startswith("Since you run") and cells["u1@example.com"].startswith("⚠ too short")
        row = next(i for i, r in enumerate(w.list_rows.rows) if r[1] == "u1@example.com")
        monkeypatch.setattr(ae, "EDIT_HOOK", lambda email, cur, why: "A longer body written by hand for Bo.")
        w._edit_ai_line(w.list_rows.index(row, 0))
        assert w._ai_email_store().ready("u1@example.com")
        assert w._ai_email_store().counts(["u0@example.com", "u1@example.com"]) == (2, 0, 0)
    finally:
        w.close()
