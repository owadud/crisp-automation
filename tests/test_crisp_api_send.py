"""Phase B, step 1: one test email through the Crisp API (create conversation, set
email/name/subject, post the message, read it back). Writes are never repeated after
an uncertain error. Fake Crisp API only."""

import io
import json
import os
import sys
import urllib.error
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import crisp_api as ca  # noqa: E402
from test_gui import app, dialogs, env, wait_until, win  # noqa: E402,F401

WID = "5b6a1eda-9935-421d-9474-ba8064296611"
SID = "session_11111111-2222-3333-4444-555555555555"


class FakeWriter:
    """A tiny in-memory Crisp: one conversation, messages become 'delivered' when read back."""

    def __init__(self, fail_on=None, fail_code=500):
        self.calls, self.meta, self.messages, self.state = [], {}, [], "pending"
        self.fail_on, self.fail_code = fail_on, fail_code

    def __call__(self, req):
        method, path = req.get_method(), req.full_url.split("/v1", 1)[1].split("?")[0]
        body = json.loads(req.data.decode()) if req.data else None
        self.calls.append((method, path, body))
        if self.fail_on and (method, path.rsplit("/", 1)[-1]) == self.fail_on:
            if self.fail_code == "network":
                raise urllib.error.URLError("connection reset")
            code, self.fail_on = self.fail_code, (self.fail_on if self.fail_code >= 500 else None)
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(b'{"reason":"busy"}'))
        base = f"/website/{WID}/conversation"
        data = None
        if (method, path) == ("POST", base):
            data = {"session_id": SID}
        elif (method, path) == ("PATCH", f"{base}/{SID}/meta"):
            self.meta.update(body)
        elif (method, path) == ("POST", f"{base}/{SID}/message"):
            self.messages.append(dict(body, timestamp=1, delivered="", read=""))
        elif (method, path) == ("GET", f"{base}/{SID}/messages"):
            for m in self.messages:
                m["delivered"] = "email"                       # Crisp delivered it by email
            data = self.messages
        elif (method, path) == ("GET", f"{base}/{SID}"):
            data = {"state": self.state}
        return io.BytesIO(json.dumps({"error": False, "data": data}).encode())


def api(fake):
    return ca.CrispAPI(WID, "id", "key", opener=fake, sleep=lambda s: None)


def test_send_test_creates_sets_posts_and_reads_back():
    fake = FakeWriter()
    r = ca.send_test(api(fake), "me@example.com", "Hello", "Body text", poll=lambda s: None)
    writes = [(m, p.rsplit("/", 1)[-1]) for m, p, _b in fake.calls if m != "GET"]
    assert writes == [("POST", "conversation"), ("PATCH", "meta"), ("POST", "message")]
    assert fake.meta == {"email": "me@example.com", "nickname": "me", "subject": "Hello"}
    sent = [b for m, p, b in fake.calls if m == "POST" and p.endswith("/message")][0]
    assert sent == {"type": "text", "from": "operator", "origin": "email", "content": "Body text"}
    assert r["error"] == "" and r["delivered"] == "email" and r["visible_message"]
    assert r["link"] == f"https://app.crisp.chat/website/{WID}/inbox/{SID}/"


@pytest.mark.parametrize("code", [500, "network"])
def test_an_uncertain_message_write_is_never_repeated(code):
    fake = FakeWriter(fail_on=("POST", "message"), fail_code=code)
    r = ca.send_test(api(fake), "me@example.com", "Hello", "Body", poll=lambda s: None)
    posts = [1 for m, p, _b in fake.calls if m == "POST" and p.endswith("/message")]
    assert posts == [1]                                         # exactly one attempt
    assert "may or may not have been done" in r["error"]


def test_rate_limit_on_a_write_is_retried_because_nothing_happened():
    fake = FakeWriter(fail_on=("POST", "message"), fail_code=429)
    r = ca.send_test(api(fake), "me@example.com", "Hello", "Body", poll=lambda s: None)
    posts = [1 for m, p, _b in fake.calls if m == "POST" and p.endswith("/message")]
    assert posts == [1, 1] and r["error"] == "" and len(fake.messages) == 1


def test_settings_test_email_needs_confirmation(win, dialogs, monkeypatch):
    fake = FakeWriter()
    monkeypatch.setattr(win, "_api_factory", lambda cfg: api(fake))
    monkeypatch.setattr(ca, "send_test", lambda a, *args, **kw: ORIGINAL(a, *args, poll=lambda s: None, **kw))
    ca.save_config({"website_id": WID, "identifier": "id", "key": "key"}, win.api_config_path)
    s = win.settings
    s.api_test_to.edit.setText("me@example.com")
    s.api_test_subject.edit.setText("Hello")
    dialogs["answers"] = [False]
    win.send_api_test()                                          # user says no
    assert fake.calls == [] and dialogs["seen"][-1] == "Send one real email to me@example.com through the Crisp API?"
    win.send_api_test()                                          # user says yes
    assert wait_until(lambda: "Crisp reports the message: delivered = email" in s.api_test_result.text(), 10)
    assert "check the inbox" in s.api_test_result.text() and s.api_test_open.isVisibleTo(s)


ORIGINAL = ca.send_test
