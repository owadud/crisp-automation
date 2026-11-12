"""AI opening lines (fake Claude), the message editor toolbar, and dialogs that fit
their text. No real API calls."""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import send_control as sc  # noqa: E402
from crisp_gui import ai_openers as ai  # noqa: E402
from crisp_gui import message_editor as me  # noqa: E402
from test_gui import app, dialogs, env, make_csv, wait_until, win  # noqa: E402,F401


# ------------------------------------------------------------------ insertion rule
def test_insert_opener():
    assert sc.insert_opener("Hi {first_name},\n\nOffer.", "Nice store!") == "Hi {first_name},\n\nNice store!\n\nOffer."
    assert sc.insert_opener("Hi,\n\n{ai_opener}\n\nOffer.", "") == "Hi,\n\nOffer."
    assert sc.insert_opener("Offer.", "Hello there.") == "Hello there.\n\nOffer."
    assert sc.insert_opener("Offer.", "") == "Offer."


# ------------------------------------------------------------------ generation with a fake Claude
class FakeClaude:
    def __init__(self, answer=None, stop_reason="end_turn", raise_exc=None):
        self.calls, self.answer, self.stop_reason, self.raise_exc = [], answer, stop_reason, raise_exc
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        if self.raise_exc:
            raise self.raise_exc
        briefs = json.loads(kw["messages"][0]["content"].split("\n", 1)[1])
        lines = self.answer or [{"id": b["id"], "opener": f"Hello from {b['company'] or 'you'}."} for b in briefs]
        return SimpleNamespace(stop_reason=self.stop_reason, content=[SimpleNamespace(type="text",
                                                                                      text=json.dumps({"openers": lines}))])


def test_generate_sends_no_email_addresses_and_checks_lines():
    people = [("ann@acme-store.com", "Ann Lee"), ("bob@gmail.com", "Bob")]
    fake = FakeClaude()
    out = ai.generate(people, "Subject", "Hi {first_name},\n\nOffer.", make_client=lambda: fake)
    kw = fake.calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default"
    assert kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["output_config"]["effort"] == "low" and kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["system"][-1]["cache_control"] == {"type": "ephemeral"}
    sent = kw["messages"][0]["content"]
    assert "ann@" not in sent and "bob@" not in sent and "Acme Store" in sent and '"free_email": true' in sent
    assert out["ann@acme-store.com"] == {"opener": "Hello from Acme Store.", "check": ""}
    assert out["_errors"] == []
    bad = ai.generate(people[:1], "S", "M", make_client=lambda: FakeClaude(answer=[{"id": 0, "opener": "See www.x.com"}]))
    assert bad["ann@acme-store.com"]["check"] == "has a link or an address"


def test_generate_handles_refusal_and_a_bad_key():
    people = [("ann@acme.com", "Ann")]
    r = ai.generate(people, "S", "M", make_client=lambda: FakeClaude(stop_reason="refusal"))
    assert "declined" in r["_errors"][0] and "ann@acme.com" not in r

    class AuthenticationError(Exception):
        pass
    r = ai.generate(people * 30, "S", "M", make_client=lambda: FakeClaude(raise_exc=AuthenticationError("bad key")))
    assert r["_errors"] == ["Anthropic refused the API key (Settings > AI). Nothing more was generated."]


def test_the_real_sdk_accepts_the_request(tmp_path):
    """Build the request with the real Anthropic SDK against a fake HTTP endpoint."""
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta", "")
        body = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                "stop_reason": "end_turn", "stop_sequence": None, "content": [
                    {"type": "text", "text": json.dumps({"openers": [{"id": 0, "opener": "Hi from the test."}]})}],
                "usage": {"input_tokens": 1, "output_tokens": 1}}
        return httpx2.Response(200, json=body)
    client = anthropic.Anthropic(api_key="test-key", base_url="https://example.test",
                                 http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    out = ai.generate([("a@acme.com", "A")], "S", "M", make_client=lambda: client)
    assert out["a@acme.com"]["opener"] == "Hi from the test."
    assert seen["body"]["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in seen["beta"]


def test_store_keeps_edits_and_counts(tmp_path):
    st = ai.OpenerStore(tmp_path, "Spring sale!")
    st.merge({"a@x.com": {"opener": "One.", "check": ""}, "b@x.com": {"opener": "Two.", "check": "too long"},
              "_errors": []})
    st.edit("a@x.com", "My own line.")
    st.merge({"a@x.com": {"opener": "AI again.", "check": ""}})
    again = ai.OpenerStore(tmp_path, "Spring sale!")
    assert again.get("A@x.com") == "My own line." and again.path.name == "Spring_sale.json"
    assert again.counts(["a@x.com", "b@x.com", "c@x.com"]) == (1, 1, 1)


def test_company_names():
    assert ai.company_from_domain("shop.acme-store.co.uk") == "Acme Store"
    assert ai.company_from_domain("gmail.com") == ""


# ------------------------------------------------------------------ editor toolbar
def test_editor_toolbar_writes_crisp_markdown(app, monkeypatch):
    ed = me.MessageEditor()
    ed.setPlainText("Buy now")
    cur = ed.edit.textCursor()
    cur.select(cur.SelectionType.Document)
    ed.edit.setTextCursor(cur)
    ed.apply("bold")
    assert ed.toPlainText() == "**Buy now**"
    ed.setPlainText("one\ntwo")
    cur = ed.edit.textCursor()
    cur.select(cur.SelectionType.Document)
    ed.edit.setTextCursor(cur)
    ed.apply("bullets")
    assert ed.toPlainText() == "- one\n- two"
    monkeypatch.setattr(me, "PROMPT_HOOK", lambda title: "https://wpxpo.com")
    ed.setPlainText("")
    ed.apply("link")
    ed.apply("first_name")
    ed.apply("divider")
    assert ed.toPlainText() == "[link text](https://wpxpo.com){first_name}\n---\n"
    assert "2 versions" not in ed.count.text()


# ------------------------------------------------------------------ dialogs fit their text
def test_dialog_grows_to_fit_wrapped_text(app):
    from PySide6.QtWidgets import QLabel
    from crisp_gui import widgets as W
    d = W.Dialog(None, "Update to version 3.11?", "word " * 120, details=[("Current", "3.10"), ("New", "3.11")])
    d.show()
    app.processEvents()
    assert all(l.height() >= l.heightForWidth(l.width()) for l in d.findChildren(QLabel) if l.wordWrap())
    d.close()


# ------------------------------------------------------------------ in the app: generate, review, confirm
def test_ai_lines_generate_review_edit_and_confirm(win, dialogs, monkeypatch, tmp_path):
    monkeypatch.setenv("CRISP_AI_CONFIG", str(tmp_path / "anthropic.json"))
    monkeypatch.setenv("CRISP_API_CONFIG", str(tmp_path / "crisp_api.json"))
    import crisp_api as ca
    ca.save_config({"website_id": "w", "identifier": "i", "key": "k"}, tmp_path / "crisp_api.json")
    win.api_config_path = tmp_path / "crisp_api.json"
    ai.save_key("sk-test")
    monkeypatch.setattr(ai, "generate", lambda people, s, m, progress=None, **k: dict(
        {e: {"opener": f"Line for {n}.", "check": ""} for e, n in people}, _errors=[]))
    c = win.campaign
    win._send_mode_changed("api")
    c.api_message.setPlainText("Hi {first_name},\n\nOffer.")
    c.ai_enabled.setChecked(True)
    win.refresh_csv()
    win.generate_ai_openers()
    assert wait_until(lambda: not win._ai_busy, 10)
    assert dialogs["seen"][0].startswith("Write 3 opening line(s) with Claude?")
    assert "3 ready, 0 need a look, 0 missing" in c.ai_status.text()
    win.navigate(4)
    assert win.list_rows.columns[-1].startswith("AI opening line")
    first = next(i for i, r in enumerate(win.list_rows.rows) if r[7] == "Send")
    monkeypatch.setattr(ai, "EDIT_HOOK", lambda email, cur: "My own line.")
    win._edit_ai_line(win.list_rows.index(first, 0))
    assert win.list_rows.rows[first][-1] == "My own line."
    v = win.campaign_values()
    assert v["ai_openers_file"].endswith(".json") and "AI opening lines: 3 ready" in win._ai_text(v)
