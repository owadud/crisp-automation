"""Phase 5: structured @@EVENT lines, GUI reads events first with text fallback,
and the 'Check Crisp' health check. Real main() with the browser stubbed."""

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crisp_gui import run_state as rs  # noqa: E402

from test_safety import _FakePlaywright, write_csv  # noqa: E402


@pytest.fixture
def load_agent(tmp_path, monkeypatch):
    pytest.importorskip("playwright")

    def load(rows, **env):
        csv_path = write_csv(tmp_path / "agent.csv", rows)
        base = {"CRISP_CSV_FILE": str(csv_path), "CRISP_REFERENCE_FILTER": "R", "CRISP_GUI_MODE": "1",
                "CRISP_DRY_RUN": "true", "CRISP_ROW_FROM": "", "CRISP_ROW_TO": "", "CRISP_DNC_FILE": "",
                "CRISP_SKIP_FAILED": "true", "CRISP_MAX_SKIPS": "5", "CRISP_MAX_FAILS_IN_ROW": "3",
                "CRISP_HISTORY_FILE": str(tmp_path / "history.csv"), "CRISP_RUN_DIR": "",
                "CRISP_DELAY": "0", "CRISP_RANDOM_DELAY": "false", "CRISP_MAX_PER_RUN": "",
                "CRISP_DAILY_CAP": "", "CRISP_SEND_WINDOW": "", "CRISP_START_AT": "", "CRISP_RETRY_ONLY": "false",
                "CRISP_HEALTH_CHECK": "false"}
        base.update(env)
        for k, v in base.items():
            monkeypatch.setenv(k, v)
        sys.modules.pop("crisp_email_agent", None)
        return importlib.import_module("crisp_email_agent")
    yield load
    sys.modules.pop("crisp_email_agent", None)


def stub_browser(mod, monkeypatch, fail=()):
    monkeypatch.setattr(mod, "sync_playwright", _FakePlaywright)
    monkeypatch.setattr(mod, "wait_for_crisp", lambda page: mod.emit("logged_in"))
    monkeypatch.setattr(mod, "recover_page", lambda page: True)

    def process(page, row):
        if row["Contact Email"] in fail:
            raise RuntimeError("Subject not found")
        return True
    monkeypatch.setattr(mod, "process_recipient", process)


def events_of(out):
    return [json.loads(line[len("@@EVENT "):]) for line in out.splitlines() if line.startswith("@@EVENT ")]


def parse(lines, dry=True):
    p = rs.RunParser(dry_run=dry)
    p.feed(("\n".join(lines) + "\n").encode("utf-8"))
    return p.finish(0)


ROWS = [(n, f"{n}@x.com", "R", "") for n in ("a", "b", "c")]


def test_main_emits_the_expected_events(load_agent, monkeypatch, capsys):
    mod = load_agent(ROWS)
    stub_browser(mod, monkeypatch, fail={"b@x.com"})
    mod.main()
    ev = [e for e in events_of(capsys.readouterr().out) if e["event"] != "waiting"]
    assert [e["event"] for e in ev] == [
        "run_start", "eligible", "logged_in",
        "recipient", "success",
        "recipient", "skipped",
        "recipient", "success",
        "summary", "retry"]
    assert ev[1] == {"event": "eligible", "count": 3}
    assert ev[5]["email"] == "b@x.com" and ev[5]["row"] == 2
    assert ev[6] == {"event": "skipped", "email": "b@x.com", "error": "RuntimeError: Subject not found"}
    assert ev[-2] == {"event": "summary", "completed": 2, "failed": 0, "skipped": 1}


def test_events_and_text_give_the_same_state(load_agent, monkeypatch, capsys):
    mod = load_agent(ROWS)
    stub_browser(mod, monkeypatch, fail={"b@x.com"})
    mod.main()
    lines = capsys.readouterr().out.splitlines()
    both = parse(lines)
    events_only = parse([ln for ln in lines if ln.startswith("@@EVENT") or ln.startswith("Dry run")])
    text_only = parse([ln for ln in lines if not ln.startswith("@@EVENT")])
    assert both.events_seen and events_only.events_seen and not text_only.events_seen
    for st in (both, events_only, text_only):
        assert (st.eligible, st.completed, st.failed, st.skipped, st.phase) == (3, 2, 0, 1, rs.FINISHED)
        assert [r[0] for r in st.retry] == ["b@x.com"]


def test_events_win_over_misleading_text():
    """Once events arrive, sentences can change without breaking the counts."""
    st = parse([
        '@@EVENT {"event": "eligible", "count": 2}',
        "Eligible recipients: 99",
        '@@EVENT {"event": "recipient", "position": 1, "total": 2, "email": "a@x.com", "name": "A"}',
        "SUCCESS.",
        "SUCCESS.",
        '@@EVENT {"event": "success", "email": "a@x.com", "result": "Dry run"}',
        '@@EVENT {"event": "summary", "completed": 1, "failed": 0, "skipped": 0}',
        "Completed: 7",
        "@@EVENT {not json",
    ])
    assert (st.eligible, st.completed) == (2, 1)


def test_unconfirmed_failure_event_sets_warning():
    st = parse(['@@EVENT {"event": "failed", "email": "a@x.com", "error": "x", "unconfirmed": true}'], dry=False)
    assert st.unconfirmed_send and st.failed == 1 and st.phase == rs.FAILED


class _Loc:
    def __init__(self, n):
        self.n = n

    def count(self):
        return self.n

    def nth(self, i):
        return self

    def wait_for(self, **kw):
        if not self.n:
            raise TimeoutError("not visible")

    def click(self, **kw):
        pass


def test_health_check_reports_without_sending(load_agent, monkeypatch, capsys):
    mod = load_agent(ROWS, CRISP_HEALTH_CHECK="true")
    created = []
    page = SimpleNamespace(goto=lambda *a, **k: None, keyboard=SimpleNamespace(press=lambda *a: None),
                           get_by_text=lambda *a, **k: _Loc(1), locator=lambda *a, **k: _Loc(0),
                           wait_for_timeout=lambda ms: None)
    context = SimpleNamespace(pages=[page], new_page=lambda: page, close=lambda: None)

    class PW(_FakePlaywright):
        def __init__(self):
            self.chromium = SimpleNamespace(launch_persistent_context=lambda **k: context)
    monkeypatch.setattr(mod, "sync_playwright", PW)
    monkeypatch.setattr(mod, "wait_for_crisp", lambda page: None)
    monkeypatch.setattr(mod, "open_new_conversation", lambda page: None)
    monkeypatch.setattr(mod, "_subject_input", lambda page: _Loc(1))
    monkeypatch.setattr(mod, "visible", lambda loc: loc.count() > 0)
    monkeypatch.setattr(mod, "process_recipient", lambda *a: created.append(a))
    monkeypatch.setattr(mod, "_menu_rows", lambda page: [(None, "Hi there"), (None, "Offer"), (None, "Offer")])
    monkeypatch.setattr(mod, "read_shortcut_names", lambda page: ["bf-offer-1", "bf-offer-2"])
    monkeypatch.setattr(mod.time, "time", iter(range(0, 10_000, 50)).__next__)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    mod.main()
    out = capsys.readouterr().out
    st = parse(out.splitlines())
    assert created == []
    assert st.health == [("Subject dropdown", True, "found in the New conversation dialog, 2 saved subject(s) read"),
                         ("Shortcuts tab", True, "visible in the conversation, 2 shortcut name(s) read"),
                         ("Segment field", False,
                          "not found (open a conversation with the profile panel visible)")]
    assert "Health check: 2 of 3 passed." in out
    assert st.crisp_lists == {"subjects": ["Hi there", "Offer"], "shortcuts": ["bf-offer-1", "bf-offer-2"]}
    text_only = parse([ln for ln in out.splitlines() if not ln.startswith("@@EVENT")])
    assert text_only.health == st.health
