"""Phase B: campaigns sent through the Crisp REST API (no browser), with a fake Crisp.
Same safety as the browser: never repeat an uncertain send, Unconfirmed after the
send step, Failed (not sent) when Crisp refuses, dry run creates nothing."""

import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import crisp_api as ca  # noqa: E402
import send_control as sc  # noqa: E402
from crisp_gui import campaign_store as cs  # noqa: E402
from test_v36 import agent, read, write  # noqa: E402,F401

WID = "5b6a1eda-9935-421d-9474-ba8064296611"
HEADER = ["Full Name", "Contact Email", "Reference", "Mail Sent"]


class FakeAPI:
    """Stands in for crisp_api.CrispAPI inside the agent."""
    calls = []
    fail_send = None            # None | "uncertain" | "refused"

    def __init__(self, website_id, identifier, key):
        self.n = 0

    def website(self):
        FakeAPI.calls.append(("website",))
        return {"name": "wpxpo"}

    def create_conversation(self):
        self.n += 1
        sid = f"session_{len(FakeAPI.calls):08d}-0000-0000-0000-000000000000"
        FakeAPI.calls.append(("create", sid))
        return sid

    def set_meta(self, sid, **meta):
        FakeAPI.calls.append(("meta", sid, meta))

    def send_text(self, sid, text, origin="email"):
        FakeAPI.calls.append(("send", sid, text, origin))
        if FakeAPI.fail_send == "uncertain":
            err = ca.CrispAPIError("Crisp server error 502 while writing: it may or may not have been done.")
            err.uncertain = True
            raise err
        if FakeAPI.fail_send == "refused":
            raise ca.CrispAPIError("Crisp API error 400: invalid_data.", 400)

    def set_state(self, sid, state):
        FakeAPI.calls.append(("state", sid, state))

    def link(self, sid):
        return f"https://app.crisp.chat/website/{WID}/inbox/{sid}/"


@pytest.fixture
def api_agent(agent, tmp_path, monkeypatch):
    cfg = tmp_path / "crisp_api.json"
    ca.save_config({"website_id": WID, "identifier": "id", "key": "key"}, cfg)
    FakeAPI.calls, FakeAPI.fail_send = [], None

    def load(csv_path, messages=("Hi {first_name}, our offer.",), **env):
        mod = agent(csv_path, CRISP_SEND_MODE="api", CRISP_API_MESSAGES=json.dumps(list(messages)),
                    CRISP_API_CONFIG=str(cfg), CRISP_SUBJECT="Lifetime deal", CRISP_SEGMENT="api-test", **env)
        monkeypatch.setattr(mod.crisp_api, "CrispAPI", FakeAPI)

        def no_browser(*a, **k):
            raise AssertionError("the browser must not start in API mode")
        monkeypatch.setattr(mod, "sync_playwright", no_browser)
        return mod
    return load


def rows(n=3):
    return [[f"Ann Lee {i}", f"p{i}@x.com", "Shahriar", "FALSE"] for i in range(n)]


def test_split_messages_and_api_variants():
    assert sc.split_messages("A\nline 2\n---\nB\n -----  \n") == ["A\nline 2", "B"]
    assert sc.split_messages("  ") == []
    assert sc.api_variants("A\n---\nB", "v1, v2") == [("api-1", "v1"), ("api-2", "v2")]
    assert sc.api_variants("only one", "") == [("api-1", "")]


def test_api_campaign_sends_without_a_browser(api_agent, tmp_path, capsys):
    path = write(tmp_path / "r.csv", HEADER, rows())
    mod = api_agent(path)
    mod.main()
    out = capsys.readouterr().out
    sends = [c for c in FakeAPI.calls if c[0] == "send"]
    metas = [c for c in FakeAPI.calls if c[0] == "meta"]
    assert len(sends) == 3 and sends[0][2] == "Hi Ann, our offer." and sends[0][3] == "email"
    assert metas[0][2] == {"email": "p0@x.com", "nickname": "Ann Lee 0", "subject": "Lifetime deal",
                           "segments": ["api-test"]}
    assert [c[0] for c in FakeAPI.calls[:4]] == ["website", "create", "meta", "send"]   # meta before send
    assert sum(1 for c in FakeAPI.calls if c[0] == "state" and c[2] == "resolved") == 3
    assert "Crisp API connected: wpxpo. No browser is used." in out
    assert [r[3] for r in read(path)[1:]] == ["TRUE", "TRUE", "TRUE"]
    hist = sc.read_history(tmp_path / "history.csv")
    assert all(h["Result"] == "Sent" and h["Link"].startswith(f"https://app.crisp.chat/website/{WID}/inbox/session_")
               for h in hist)


def test_an_uncertain_send_stops_as_unconfirmed_and_is_not_repeated(api_agent, tmp_path, capsys):
    path = write(tmp_path / "r.csv", HEADER, rows())
    mod = api_agent(path)
    FakeAPI.fail_send = "uncertain"
    mod.main()
    out = capsys.readouterr().out
    assert len([c for c in FakeAPI.calls if c[0] == "send"]) == 1          # once, then the run stops
    assert sc.read_history(tmp_path / "history.csv")[0]["Result"] == "Unconfirmed"
    assert "MAY already have been sent" in out
    assert [r[3] for r in read(path)[1:]] == ["FALSE", "FALSE", "FALSE"]   # never marked sent


def test_a_refused_message_is_failed_not_unconfirmed(api_agent, tmp_path, capsys):
    path = write(tmp_path / "r.csv", HEADER, rows(2))
    mod = api_agent(path)
    FakeAPI.fail_send = "refused"
    mod.main()
    hist = sc.read_history(tmp_path / "history.csv")
    assert hist[0]["Result"] == "Failed" and "did not accept the message" in hist[0]["Error"]
    assert len([c for c in FakeAPI.calls if c[0] == "send"]) == 1


def test_dry_run_creates_nothing_in_crisp(api_agent, tmp_path, capsys):
    path = write(tmp_path / "r.csv", HEADER, rows(2))
    mod = api_agent(path, CRISP_DRY_RUN="true")
    mod.main()
    assert [c[0] for c in FakeAPI.calls] == ["website"]                     # only the token check
    assert "[DRY RUN] Crisp API: would create the conversation" in capsys.readouterr().out
    assert [r[3] for r in read(path)[1:]] == ["FALSE", "FALSE"]


def test_ab_message_versions_alternate_and_are_recorded(api_agent, tmp_path):
    path = write(tmp_path / "r.csv", HEADER, rows(4))
    mod = api_agent(path, messages=("Version A", "Version B"), CRISP_SHORTCUT_VERSION="v1, v2")
    mod.main()
    texts = [c[2] for c in FakeAPI.calls if c[0] == "send"]
    assert texts == ["Version A", "Version B", "Version A", "Version B"]
    hist = sc.read_history(tmp_path / "history.csv")
    assert [(h["Shortcut"], h["Version"]) for h in hist] == [("api-1", "v1"), ("api-2", "v2")] * 2


def test_missing_token_or_message_stops_before_anything(api_agent, tmp_path, monkeypatch):
    path = write(tmp_path / "r.csv", HEADER, rows(1))
    mod = api_agent(path, messages=())
    with pytest.raises(RuntimeError, match="needs the message text"):
        mod.main()
    assert FakeAPI.calls == []


def test_an_api_campaign_needs_a_message_not_a_shortcut(tmp_path):
    st = cs.CampaignStore(tmp_path / "c.json")
    base = {"source": "csv", "csv": "x.csv", "subject": "S", "segment": "g", "send_mode": "api"}
    assert st.add("A", dict(base)).missing() == ["api_message"]
    assert st.add("B", dict(base, api_message="Hello")).status == cs.READY


def test_live_confirmation_names_the_api_and_the_message(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import test_gui as tg
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from crisp_gui import widgets as W
    from crisp_gui.config import Preferences
    from crisp_gui.main_window import MainWindow
    from crisp_gui.theme import ThemeManager
    app = QApplication.instance() or QApplication([])
    cfg = tmp_path / "crisp_api.json"
    ca.save_config({"website_id": WID, "identifier": "id", "key": "key"}, cfg)
    monkeypatch.setenv("CRISP_API_CONFIG", str(cfg))
    seen = []
    monkeypatch.setattr(W, "DIALOG_HOOK", lambda title, body, rows, confirm: seen.append((title, body, rows)) or False)
    prefs = Preferences(QSettings(str(tmp_path / "p.ini"), QSettings.IniFormat))
    prefs.set_campaign_value("csv", str(tg.make_csv(tmp_path / "list.csv", n=2)))
    w = MainWindow(ThemeManager(app, "light"), prefs, agent_script=tg.FAKE, data_dir=tmp_path / "data")
    try:
        w._send_mode_changed("api")
        w.campaign.api_message.setPlainText("Hi {first_name}, the offer.")
        w.campaign.dry.setChecked(False)
        w.start_run()
        title, body, rows_ = seen[-1]
        assert title == "Send 2 real emails?" and "Send with: Crisp API (no browser)" in body
        assert "the offer" in body and not w.runner.is_running()           # cancelled
        assert rows_[0][4] == "api-1"                                       # message version per row
    finally:
        w.close()
