"""Phase 3 safety: duplicates, invalid emails, do-not-contact, lock, backup,
skip-before-Send and auto-stop. The browser is stubbed; no Crisp needed."""

import csv
import importlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import recipient_rules as rules  # noqa: E402
from crisp_gui import run_state as rs  # noqa: E402
from crisp_gui.csv_stats import read_stats  # noqa: E402

HEADER = ("Full Name", "Contact Email", "Reference", "Mail Sent")


def write_csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)
    return path


@pytest.mark.parametrize("email, bad", [
    ("ok@example.com", False), ("first.last+tag@sub.domain.co.uk", False),
    ("a@b", True), ("no-at-sign.com", True), ("two@@x.com", True), ("a..b@x.com", True),
    ("user@gmial.com", True), ("user@hotmial.com", True), ("user@example.con", True), ("", True),
])
def test_email_validation(email, bad):
    assert bool(rules.email_problem(email)) is bad


def test_duplicates_invalid_and_do_not_contact(tmp_path):
    p = write_csv(tmp_path / "r.csv", [
        ("A", "a@x.com", "R", "TRUE"),     # 1 already sent
        ("B", " A@X.com", "R", ""),        # 2 duplicate of a sent email
        ("C", "c@x.com", "R", ""),         # 3 eligible
        ("D", "C@x.com ", "R", ""),        # 4 duplicate of row 3
        ("E", "e@gmial.com", "R", ""),     # 5 invalid (typo)
        ("F", "f@x.com", "R", ""),         # 6 do-not-contact
        ("G", "g@x.com", "R", ""),         # 7 eligible
    ])
    dnc = write_csv(tmp_path / "dnc.csv", [("F", "F@X.com", "", "")])
    s = read_stats(str(p), "R", dnc_path=str(dnc))
    assert (s.eligible, s.duplicates, s.invalid, s.do_not_contact, s.marked_sent) == (2, 2, 1, 1, 1)
    assert s.invalid_rows == [(5, "e@gmial.com", "looks like a typo of gmail.com")]
    assert "2 duplicates skipped" in s.preview()
    bad = read_stats(str(p), "R", dnc_path=str(tmp_path / "missing.csv"))
    assert bad.dnc_error


# ------------------------------------------------------------------ agent
@pytest.fixture
def load_agent(tmp_path, monkeypatch):
    pytest.importorskip("playwright")

    def load(rows, **env):
        csv_path = write_csv(tmp_path / "agent.csv", rows)
        base = {"CRISP_CSV_FILE": str(csv_path), "CRISP_REFERENCE_FILTER": "R", "CRISP_GUI_MODE": "1",
                "CRISP_DRY_RUN": "true", "CRISP_ROW_FROM": "", "CRISP_ROW_TO": "", "CRISP_DNC_FILE": "",
                "CRISP_SKIP_FAILED": "false", "CRISP_MAX_SKIPS": "5", "CRISP_MAX_FAILS_IN_ROW": "3",
                "CRISP_HISTORY_FILE": str(tmp_path / "history.csv"), "CRISP_RUN_DIR": "",
                "CRISP_DELAY": "", "CRISP_RANDOM_DELAY": "false", "CRISP_MAX_PER_RUN": "",
                "CRISP_DAILY_CAP": "", "CRISP_SEND_WINDOW": "", "CRISP_START_AT": "", "CRISP_RETRY_ONLY": "false"}
        base.update(env)
        for k, v in base.items():
            monkeypatch.setenv(k, v)
        sys.modules.pop("crisp_email_agent", None)
        mod = importlib.import_module("crisp_email_agent")
        mod.BETWEEN_EMAILS = 0
        return mod, csv_path
    yield load
    sys.modules.pop("crisp_email_agent", None)


def test_agent_and_preview_skip_the_same_rows(load_agent, tmp_path):
    rows = [("A", "a@x.com", "R", "TRUE"), ("B", "a@x.com", "R", ""), ("C", "c@x.com", "R", ""),
            ("E", "e@x.con", "R", ""), ("F", "f@x.com", "R", "")]
    dnc = tmp_path / "dnc.txt"
    dnc.write_text("f@x.com\n", encoding="utf-8")
    mod, csv_path = load_agent(rows, CRISP_DNC_FILE=str(dnc))
    agent_rows, _ = mod.load_csv()
    picked = [r["Contact Email"] for _, r in mod.get_eligible(agent_rows)]
    assert picked == ["c@x.com"]
    assert read_stats(str(csv_path), "R", dnc_path=str(dnc)).eligible == 1


def test_lock_blocks_second_run_and_replaces_stale_lock(load_agent):
    mod, csv_path = load_agent([("C", "c@x.com", "R", "")])
    lock = mod.acquire_lock()
    assert lock.exists()
    with pytest.raises(RuntimeError, match="already in use"):
        mod.acquire_lock()          # same PC, process alive -> refused
    mod.release_lock()
    assert not lock.exists()
    import socket
    lock.write_text(f"pid=999999\nhost={socket.gethostname()}\nstarted=old\n", encoding="utf-8")
    mod.acquire_lock()              # dead process -> stale lock replaced
    assert f"pid={os.getpid()}" in lock.read_text(encoding="utf-8")
    mod.release_lock()


def test_backup_copy_has_dated_name(load_agent):
    mod, csv_path = load_agent([("C", "c@x.com", "R", "")])
    backup = mod.backup_csv()
    assert backup.name.startswith("agent.") and backup.name.endswith(".bak.csv")
    assert backup.read_bytes() == csv_path.read_bytes()


class _FakePlaywright:
    def __init__(self):
        page = SimpleNamespace(goto=lambda *a, **k: None, keyboard=SimpleNamespace(press=lambda *a: None))
        context = SimpleNamespace(pages=[page], new_page=lambda: page, close=lambda: None)
        self.chromium = SimpleNamespace(launch_persistent_context=lambda **k: context)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def run_main(mod, monkeypatch, capsys, fail):
    """fail: {email: 'before' | 'after'} -> process_recipient raises there."""
    monkeypatch.setattr(mod, "sync_playwright", _FakePlaywright)
    monkeypatch.setattr(mod, "wait_for_crisp", lambda page: None)
    monkeypatch.setattr(mod, "recover_page", lambda page: True)

    def process(page, row):
        email = row["Contact Email"]
        print(f"  Recipient: {email}")
        if fail.get(email) == "after":
            mod.SEND_CLICKED = True
            raise RuntimeError("Crisp did not confirm the send")
        if fail.get(email) == "before":
            raise RuntimeError("Subject not found")
        print("  [DRY RUN] Send skipped.")
        return True
    monkeypatch.setattr(mod, "process_recipient", process)
    mod.main()
    out = capsys.readouterr().out
    parser = rs.RunParser(dry_run=True)
    parser.feed(out.encode("utf-8"))
    return out, parser.finish(0)


ROWS5 = [(n, f"{n}@x.com", "R", "") for n in ("a", "b", "c", "d", "e")]


def test_default_stops_at_first_failure(load_agent, monkeypatch, capsys):
    mod, _ = load_agent(ROWS5)
    out, st = run_main(mod, monkeypatch, capsys, {"b@x.com": "before"})
    assert (st.completed, st.failed, st.skipped) == (1, 1, 0)
    assert "RETRY: b@x.com" in out and "c@x.com" not in out.split("RETRY")[0].split("Recipient: b@x.com")[1]
    assert st.phase == rs.FAILED


def test_skip_before_send_continues_and_lists_retry(load_agent, monkeypatch, capsys):
    mod, _ = load_agent(ROWS5, CRISP_SKIP_FAILED="true")
    out, st = run_main(mod, monkeypatch, capsys, {"b@x.com": "before", "d@x.com": "before"})
    assert (st.completed, st.failed, st.skipped) == (3, 0, 2)
    assert [r[0] for r in st.retry] == ["b@x.com", "d@x.com"]
    assert st.phase == rs.FINISHED


def test_failure_after_send_always_stops(load_agent, monkeypatch, capsys):
    mod, _ = load_agent(ROWS5, CRISP_SKIP_FAILED="true")
    out, st = run_main(mod, monkeypatch, capsys, {"b@x.com": "after"})
    assert (st.completed, st.failed, st.skipped) == (1, 1, 0)
    assert "Recipient: c@x.com" not in out


def test_auto_stop_after_failures_in_a_row(load_agent, monkeypatch, capsys):
    mod, _ = load_agent(ROWS5, CRISP_SKIP_FAILED="true", CRISP_MAX_FAILS_IN_ROW="2")
    out, st = run_main(mod, monkeypatch, capsys, {e: "before" for e in ("a@x.com", "b@x.com", "c@x.com")})
    assert (st.skipped, st.failed) == (1, 1)
    assert "2 failures in a row: auto-stop." in out
    assert "Recipient: c@x.com" not in out


def test_max_skips_limit(load_agent, monkeypatch, capsys):
    mod, _ = load_agent(ROWS5, CRISP_SKIP_FAILED="true", CRISP_MAX_SKIPS="1")
    out, st = run_main(mod, monkeypatch, capsys, {"a@x.com": "before", "c@x.com": "before"})
    assert (st.skipped, st.completed, st.failed) == (1, 1, 1)


def test_unexpanded_shortcut_is_never_sent(load_agent, monkeypatch):
    """The composer still showing the shortcut NAME must not count as inserted,
    and Send must refuse before clicking (2026-11-14 bf-offer-3 incident)."""
    agent, _ = load_agent([("A", "a@x.com", "R", "")], CRISP_SHORTCUT="bf-offer-3", CRISP_DRY_RUN="false")
    clicked = []
    button = SimpleNamespace(click=lambda **k: clicked.append(1))
    monkeypatch.setattr(agent, "find_composer", lambda page: object())
    monkeypatch.setattr(agent, "find_send_button", lambda page: button)
    monkeypatch.setattr(agent, "box", lambda loc: None)
    monkeypatch.setattr(agent, "enabled", lambda loc: True)

    monkeypatch.setattr(agent, "get_composer_text", lambda c: "bf-offer-3")
    assert agent.shortcut_content_present(None) is False
    with pytest.raises(RuntimeError, match="nothing was sent"):
        agent.send_message(None)
    assert clicked == [] and agent.SEND_CLICKED is False

    monkeypatch.setattr(agent, "get_composer_text", lambda c: "Hi there, here is your Black Friday offer...")
    assert agent.shortcut_content_present(None) is True
