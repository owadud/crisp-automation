"""Campaign Results through the Crisp REST API (read-only), with fake API responses
shaped like the documented fields (state; messages with from/timestamp/delivered/read/ignored)."""

import io
import json
import os
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import campaign_results as cr  # noqa: E402
import crisp_api as ca  # noqa: E402
import send_control as sc  # noqa: E402
from test_gui import app, dialogs, env, wait_until, win  # noqa: E402,F401

WID = "5b6a1eda-9935-421d-9474-ba8062966110"
DHAKA = timezone(timedelta(hours=6))
SENT_AT = "2026-11-05 20:08:15"


def ms(stamp):
    return int(datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=DHAKA).timestamp() * 1000)


def link(sid):
    return f"https://app.crisp.chat/website/{WID}/inbox/session_{sid}-1111-2222-3333-444455556666/"


def ours(**kw):
    return dict({"from": "operator", "type": "text", "origin": "email", "timestamp": ms(SENT_AT) + 2000,
                 "delivered": "", "read": ""}, **kw)


def reply(stamp="2026-11-06 09:30:00"):
    return {"from": "user", "type": "text", "origin": "email", "timestamp": ms(stamp)}


# ------------------------------------------------------------------ status rules
@pytest.mark.parametrize("msgs, status", [
    ([ours()], ca.SENT),
    ([ours(delivered="email")], ca.DELIVERED),
    ([ours(delivered="email", read="email")], ca.READ),
    ([ours(delivered="email"), reply()], ca.REPLIED),
    ([ours(ignored={"channel": "email", "type": "email", "reason": "unsubscribed"})], ca.FAILED),
])
def test_status_from_api_messages(msgs, status):
    r = ca.analyze({"state": "resolved"}, msgs, SENT_AT)
    assert r["status"] == status and r["problem"] == ""


def test_reply_time_is_exact_dhaka_and_state_gives_open():
    r = ca.analyze({"state": "unresolved"}, [ours(), reply("2026-11-06 09:30:00")], SENT_AT)
    assert (r["reply_at"], r["reply_kind"], r["open"]) == ("2026-11-06 09:30", "exact", True)
    assert ca.analyze({"state": "resolved"}, [ours()], SENT_AT)["open"] is False
    failed = ca.analyze({}, [ours(ignored={"reason": "unsubscribed"})], SENT_AT)
    assert failed["reason"] == "Not delivered: unsubscribed"


def test_earlier_messages_and_notes_are_not_ours():
    old = ours(timestamp=ms("2026-10-01 10:00:00"), read="email")
    note = ours(type="note", timestamp=ms(SENT_AT) + 1000)
    r = ca.analyze({}, [old, note, ours()], SENT_AT)
    assert r["status"] == ca.SENT                          # the old read message is not this campaign's
    assert ca.analyze({}, [reply()], SENT_AT)["problem"] == "no_our_message"


def test_links_and_website_id():
    assert ca.parse_link(link("abc12345")) == (WID, "session_abc12345-1111-2222-3333-444455556666")
    assert ca.parse_link("https://example.com/x") == ("", "")
    assert ca.website_from_links([link("a1b2c3d4"), "", link("e5f6a7b8")]) == WID


def test_config_is_saved_locally(tmp_path):
    path = tmp_path / "cfg" / "crisp_api.json"
    assert not ca.configured(ca.load_config(path))
    ca.save_config({"website_id": WID, "identifier": " id ", "key": "k"}, path)
    assert ca.load_config(path) == {"website_id": WID, "identifier": "id", "key": "k"}


# ------------------------------------------------------------------ the HTTP client (fake transport)
class FakeCrisp:
    """Answers GET requests like api.crisp.chat; records what was sent."""

    def __init__(self, conversations, fail=None):
        self.conversations, self.fail, self.seen = conversations, list(fail or []), []

    def __call__(self, req):
        self.seen.append((req.get_method(), req.full_url, dict(req.header_items())))
        if self.fail:
            code = self.fail.pop(0)
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(b"{}"))
        path = req.full_url.split("/v1", 1)[1].split("?")[0]
        parts = path.strip("/").split("/")
        if parts == ["website", WID]:
            data = {"name": "wpxpo", "domain": "wpxpo.com"}
        else:
            sid = parts[3]
            if sid not in self.conversations:
                raise urllib.error.HTTPError(req.full_url, 404, "x", {}, io.BytesIO(b"{}"))
            conv = self.conversations[sid]
            data = conv["messages"] if parts[-1] == "messages" else {"state": conv["state"]}
        body = json.dumps({"error": False, "reason": "resolved", "data": data}).encode()
        return io.BytesIO(body)


def sid(name):
    return f"session_{name}-1111-2222-3333-444455556666"


def test_client_headers_read_only_and_errors():
    fake = FakeCrisp({sid("aaaa1111"): {"state": "resolved", "messages": [ours(read="email")]}})
    api = ca.CrispAPI(WID, "ident", "secret", opener=fake, sleep=lambda s: None)
    assert ca.check(api, link("aaaa1111"), SENT_AT)["status"] == ca.READ
    method, url, headers = fake.seen[-1]
    assert method == "GET" and url.startswith(f"https://api.crisp.chat/v1/website/{WID}/conversation/")
    assert "timestamp_after=" in url
    assert headers["X-crisp-tier"] == "website" and headers["Authorization"].startswith("Basic ")
    assert all(m == "GET" for m, _u, _h in fake.seen)                 # never writes to Crisp
    assert ca.check(api, link("bbbb2222"), SENT_AT) == {"problem": "not_found"}
    with pytest.raises(ca.CrispAPIError, match="refused the API token"):
        ca.CrispAPI(WID, "i", "k", opener=FakeCrisp({}, fail=[401]), sleep=lambda s: None).website()
    retried = FakeCrisp({}, fail=[429, 503])
    assert ca.CrispAPI(WID, "i", "k", opener=retried, sleep=lambda s: None).website()["name"] == "wpxpo"


# ------------------------------------------------------------------ in the app
def test_settings_save_test_and_refresh_through_the_api(win, dialogs, monkeypatch, tmp_path):
    hist = tmp_path / "data" / "history.csv"
    for name, email in (("aaaa1111", "a@x.com"), ("bbbb2222", "b@x.com"), ("cccc3333", "c@x.com")):
        sc.append_history(hist, Time=SENT_AT, Email=email, Result="Sent", Mode="live", Campaign="Wow",
                          Link=link(name))
    fake = FakeCrisp({sid("aaaa1111"): {"state": "unresolved", "messages": [ours(delivered="email"), reply()]},
                      sid("bbbb2222"): {"state": "resolved", "messages": [ours(delivered="email", read="email")]}})
    monkeypatch.setattr(win, "_api_factory", lambda cfg: ca.CrispAPI(cfg["website_id"], cfg["identifier"],
                                                                     cfg["key"], opener=fake,
                                                                     sleep=lambda s: None))
    s = win.settings
    win._load_api_fields()
    assert s.api_website.text() == WID                                  # from the conversation links
    s.api_identifier.edit.setText("ident")
    s.api_key.edit.setText("secret")
    win.save_api_token()
    assert wait_until(lambda: "Connected to wpxpo" in s.api_status.text(), 10)
    assert Path(win.api_config_path).parent == tmp_path                 # never the real token file

    win.navigate(3)
    win.refresh_statuses()
    assert wait_until(lambda: not win._api_checking, 15)
    assert any(t.startswith("Read 3 conversation(s) through the Crisp API") for t in dialogs["seen"])
    rows = {r["email"]: r for r in win.results.rows("Wow")}
    assert rows["a@x.com"]["status"] == cr.REPLIED and rows["b@x.com"]["status"] == cr.READ
    assert rows["c@x.com"]["status"] == cr.SENT                          # not in Crisp any more: unchanged
    assert "1 no longer in Crisp" in win.results_page.progress_text.text()
    assert not win.runner.is_running()                                   # no browser
