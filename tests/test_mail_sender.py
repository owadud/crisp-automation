"""Sending from your own mail account (mail_sender.py, Send with: My email), with a fake SMTP server."""

import json
import smtplib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mail_sender as ms  # noqa: E402
import send_control as sc  # noqa: E402
from test_v36 import agent, read, write  # noqa: E402,F401

CFG = {"provider": "gmail", "email": "me@example.com", "password": "app-pass", "from_name": "Owadud",
       "reply_to": "support@example.com", "host": "smtp.example.com", "port": 465, "security": "ssl",
       "daily_limit": 0, "unsubscribe": True}


class FakeSMTP:
    """Records logins and messages; `fail` makes the next send raise."""
    instances = []
    fail = None          # None | "refused" | "drop"
    noop_code = 250

    def __init__(self, host, port, security):
        self.sent, self.logins = [], []
        FakeSMTP.instances.append(self)

    def login(self, user, password):
        self.logins.append((user, password))

    def noop(self):
        return (FakeSMTP.noop_code, b"OK")

    def send_message(self, msg):
        if FakeSMTP.fail == "refused":
            raise smtplib.SMTPRecipientsRefused({msg["To"]: (550, b"No such user")})
        if FakeSMTP.fail == "drop":
            raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
        self.sent.append(msg)
        return {}

    def quit(self):
        pass


@pytest.fixture(autouse=True)
def fresh():
    FakeSMTP.instances, FakeSMTP.fail, FakeSMTP.noop_code = [], None, 250


def sender():
    return ms.MailSender(CFG, smtp_factory=FakeSMTP)


def test_message_has_html_plain_reply_to_and_unsubscribe():
    msg = ms.build_message(CFG, "ann@x.com", "Offer", "Hi **Ann**,\n\n- PostX\n- WowStore\n\n[Buy](https://x.com)",
                           to_name="Ann Lee")
    assert msg["From"] == "Owadud <me@example.com>" and msg["To"] == "Ann Lee <ann@x.com>"
    assert msg["Reply-To"] == "support@example.com"
    assert msg["List-Unsubscribe"] == "<mailto:support@example.com?subject=unsubscribe>"
    plain, html = (part.get_content() for part in msg.iter_parts())
    assert "Hi Ann," in plain and "Buy (https://x.com)" in plain and "unsubscribe" in plain
    assert "<b>Ann</b>" in html and "<ul><li>PostX</li><li>WowStore</li></ul>" in html
    assert '<a href="https://x.com">Buy</a>' in html


def test_send_logs_in_once_and_reconnects_when_dropped():
    s = sender().connect()
    s.send("a@x.com", "S", "one")
    FakeSMTP.noop_code = 421                      # the server closed the idle connection
    s.send("b@x.com", "S", "two")
    assert len(FakeSMTP.instances) == 2 and FakeSMTP.instances[0].logins == [("me@example.com", "app-pass")]
    assert [m["To"] for i in FakeSMTP.instances for m in i.sent] == ["a@x.com", "b@x.com"]


def test_refused_is_not_sent_but_a_dropped_connection_may_be():
    s = sender().connect()
    FakeSMTP.fail = "refused"
    with pytest.raises(ms.MailError) as refused:
        s.send("a@x.com", "S", "t")
    assert not refused.value.uncertain and "No such user" in str(refused.value)
    FakeSMTP.fail = "drop"
    with pytest.raises(ms.MailError) as dropped:
        s.send("a@x.com", "S", "t")
    assert dropped.value.uncertain


def test_problem_names_what_is_missing(tmp_path):
    assert "email address" in ms.problem({})
    assert ms.problem(CFG) == ""
    path = ms.save_config(CFG, tmp_path / "mail.json")
    assert ms.load_config(path) == CFG


def mail_agent(agent, tmp_path, monkeypatch, path, messages, **env):
    cfg = tmp_path / "mail.json"
    ms.save_config(dict(CFG, **env.pop("cfg", {})), cfg)
    mod = agent(path, CRISP_SEND_MODE="email", CRISP_API_MESSAGES=json.dumps(list(messages)),
                CRISP_MAIL_CONFIG=str(cfg), CRISP_SUBJECT="Bundle offer", CRISP_SEGMENT="", **env)
    real = ms.MailSender
    monkeypatch.setattr(ms, "MailSender", lambda c: real(c, smtp_factory=FakeSMTP))

    def no_browser(*a, **k):
        raise AssertionError("the browser must not start when sending from your email")
    monkeypatch.setattr(mod, "sync_playwright", no_browser)
    return mod


HEADER = ["Full Name", "Contact Email", "Reference", "Mail Sent", "Order Value"]


def test_campaign_sends_personalized_emails_from_my_account(agent, tmp_path, monkeypatch):
    path = write(tmp_path / "r.csv", HEADER, [["Ann Lee", "ann@x.com", "Shahriar", "FALSE", "$900"],
                                              ["Bo Chan", "bo@x.com", "Shahriar", "FALSE", "$100"]])
    mod = mail_agent(agent, tmp_path, monkeypatch, path, ("Hi {first_name}, you spent {Order Value}.",))
    mod.main()
    sent = [m for i in FakeSMTP.instances for m in i.sent]
    assert [(m["To"], m["Subject"]) for m in sent] == [("Ann Lee <ann@x.com>", "Bundle offer"),
                                                        ("Bo Chan <bo@x.com>", "Bundle offer")]
    assert "Hi Ann, you spent $900." in sent[0].get_body(("plain",)).get_content()
    assert len(FakeSMTP.instances) == 1                                   # one login for the whole run
    assert [r[3] for r in read(path)[1:]] == ["TRUE", "TRUE"]
    assert len(sc.read_history(tmp_path / "history.csv")) == 2


def test_dry_run_neither_logs_in_nor_sends(agent, tmp_path, monkeypatch, capsys):
    path = write(tmp_path / "r.csv", HEADER, [["Ann Lee", "ann@x.com", "Shahriar", "FALSE", "$900"]])
    mod = mail_agent(agent, tmp_path, monkeypatch, path, ("Hi {first_name}",), CRISP_DRY_RUN="true")
    mod.main()
    assert FakeSMTP.instances == []
    assert "[DRY RUN] Your email account: would send" in capsys.readouterr().out


def test_account_daily_limit_caps_the_run(agent, tmp_path, monkeypatch):
    path = write(tmp_path / "r.csv", HEADER, [[f"P {i}", f"p{i}@x.com", "Shahriar", "FALSE", ""] for i in range(3)])
    mod = mail_agent(agent, tmp_path, monkeypatch, path, ("Hi",), cfg={"daily_limit": 2})
    mod.main()
    assert len([m for i in FakeSMTP.instances for m in i.sent]) == 2


def test_missing_account_stops_before_anything(agent, tmp_path, monkeypatch):
    path = write(tmp_path / "r.csv", HEADER, [["Ann Lee", "ann@x.com", "Shahriar", "FALSE", ""]])
    mod = mail_agent(agent, tmp_path, monkeypatch, path, ("Hi",), cfg={"password": ""})
    with pytest.raises(RuntimeError, match="password"):
        mod.main()
    assert FakeSMTP.instances == []


def test_settings_save_sends_a_test_email_and_campaign_needs_no_segment(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import test_gui as tg
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from crisp_gui.config import Preferences
    from crisp_gui.main_window import MainWindow
    from crisp_gui.theme import ThemeManager
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("CRISP_MAIL_CONFIG", str(tmp_path / "mail.json"))
    prefs = Preferences(QSettings(str(tmp_path / "p.ini"), QSettings.IniFormat))
    prefs.set_campaign_value("csv", str(tg.make_csv(tmp_path / "list.csv", n=2)))
    w = MainWindow(ThemeManager(app, "light"), prefs, agent_script=tg.FAKE, data_dir=tmp_path / "data")
    try:
        w._mail_factory = lambda c: ms.MailSender(c, smtp_factory=FakeSMTP)
        s = w.settings
        assert s.mail_host.text() == "smtp.gmail.com" and s.mail_port.text() == "465"   # Gmail preset
        s.mail_email.edit.setText("me@example.com")
        s.mail_password.edit.setText("abcd efgh ijkl mnop")
        w.save_mail_account()
        for _ in range(200):
            app.processEvents()
            if s.mail_save.isEnabled():
                break
            import time
            time.sleep(0.01)
        assert "test email was sent" in s.mail_status.text()
        assert ms.load_config(tmp_path / "mail.json")["password"] == "abcdefghijklmnop"
        assert FakeSMTP.instances[0].sent[0]["To"] == "me@example.com"
        w._send_mode_changed("email")
        w.campaign.api_message.setPlainText("Hi {first_name}")
        w.campaign.segment.edit.setText("")
        w._validate_fields()
        assert w.campaign.segment.message.objectName() != "fieldError"
        assert w.campaign.api_message_help.objectName() == "fieldHelp"
    finally:
        w.close()


def test_pasted_html_is_sent_as_written_with_a_text_part():
    import personal as pz
    page = ("<!DOCTYPE html><html><head><style>.b{color:#fff} p { margin: 0 }</style></head><body>"
            "<p>Hi {first_name},</p><a class=\"b\" href=\"https://x.com/deal\">See the deal</a></body></html>")
    assert pz.is_html(page) and not pz.is_html("Hi **Ann**") and pz.check([page], ["Full Name"]) == []
    body, _ = pz.render(page, "Ann & Bo Lee", pz.SAMPLE)
    assert ".b{color:#fff} p { margin: 0 }" in body and "Hi Ann," in body       # CSS braces untouched
    msg = ms.build_message(CFG, "ann@x.com", "Offer", body)
    plain, html_part = [p.get_content() for p in msg.iter_parts()]
    assert "See the deal (https://x.com/deal)" in plain and "color:#fff" not in plain
    assert html_part.index("unsubscribe") < html_part.index("</body>")         # inside the page
    assert '<a class="b" href="https://x.com/deal">' in html_part


def test_tracked_campaign_writes_who_got_which_id(agent, tmp_path, monkeypatch):
    import tracking
    url = "https://script.google.com/macros/s/AKfycbxTEST/exec"
    path = write(tmp_path / "r.csv", HEADER, [["Ann Lee", "ann@x.com", "Shahriar", "FALSE", "$900"]])
    mod = mail_agent(agent, tmp_path, monkeypatch, path, ("Hi {first_name}.",),
                     cfg={"track": True, "track_url": url, "track_key": "k"}, CRISP_CAMPAIGN="Promo",
                     CRISP_DATA_DIR=str(tmp_path))
    mod.main()
    sent = [m for i in FakeSMTP.instances for m in i.sent]
    assert mod.DATA_DIR == tmp_path                                       # never the project folder
    ledger = tracking.read_ledger(tmp_path)
    (tid, rec), = ledger.items()
    assert (rec["Email"], rec["Campaign"]) == ("ann@x.com", "Promo")
    assert f'{url}?i={tid}' in sent[0].get_body(("html",)).get_content()


def test_crisp_style_unsubscribe_tag_becomes_a_link():
    import personal as pz
    page = ('<html><body><p>Hi {first_name}</p><a href="{{ url.unsubscribe }}">Unsubscribe</a></body></html>')
    assert pz.check([page], ["Full Name"]) == []                       # not "url.unsubscribe is not a column"
    assert "can't be filled" in pz.check(["<p>{{ contact.first }}</p>"], [])[0]
    body, _ = pz.render(page, "Ann Lee", pz.SAMPLE)
    msg = ms.build_message(dict(CFG, reply_to="support@x.com"), "ann@x.com", "Offer", body)
    html_part = list(msg.iter_parts())[1].get_content()
    assert 'href="mailto:support@x.com?subject=unsubscribe"' in html_part
    assert "take you off the list" not in html_part                      # its own link: no second one


def test_dry_run_log_shows_html_as_short_text(agent, tmp_path, monkeypatch, capsys):
    page = ("<!DOCTYPE html><html><head><style>p{color:red}</style></head><body><p>Hi {first_name},</p>"
            + "".join(f"<p>Line {i}</p>" for i in range(40)) + "</body></html>")
    path = write(tmp_path / "r.csv", HEADER, [["Ann Lee", "ann@x.com", "Shahriar", "FALSE", "$900"]])
    mod = mail_agent(agent, tmp_path, monkeypatch, path, (page,), CRISP_DRY_RUN="true",
                     CRISP_DATA_DIR=str(tmp_path))
    mod.main()
    out = capsys.readouterr().out
    assert "(HTML email," in out and "| Hi Ann," in out and "more line(s)" in out
    assert "<p>" not in out and "color:red" not in out                    # no code in the log
