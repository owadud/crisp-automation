"""Shared Sheet: per-recipient claims, shared history, heartbeats and expiry.
Two (or more) simulated runners work on one fake Google Sheet."""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import claims as cl  # noqa: E402
import recipient_rules as rules  # noqa: E402
import send_control as sc  # noqa: E402
import sheet_source as ss  # noqa: E402
from fake_sheets import FakeClient, FakeSpreadsheet  # noqa: E402
from test_sheet_source import HEADER, SID, URL, sheet_agent, run  # noqa: E402,F401

T0 = datetime(2026, 11, 5, 10, 0, tzinfo=sc.DHAKA)
EMAILS = [f"p{i}@x.com" for i in range(1, 13)]


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(ss.time, "sleep", lambda s: None)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    def advance(self, minutes):
        self.now += timedelta(minutes=minutes)


def book_with(emails=EMAILS):
    return FakeSpreadsheet({"Customers": [HEADER] + [[e.split("@")[0], e, "", "FALSE"] for e in emails]})


def runner(book, name, campaign="VIP", clock=None, batch=3, recent_days=7):
    src = ss.SheetSource(URL, "Customers", "unused.json", client=FakeClient(book, SID)).connect()
    src.download()
    c = cl.Claims(src, campaign, runner=name, run_id=f"run-{name}", batch=batch, clock=clock or Clock(),
                  recent_days=recent_days)
    c.start(heartbeat=False)
    return c


def send_all(c, emails, sent_log, stop_after=None):
    """A fake agent loop: claim a small batch, Sending, Sent. Returns sends made."""
    done = 0
    for i, e in enumerate(emails):
        if stop_after is not None and done >= stop_after:
            break
        if e not in c.mine:
            c.claim_batch(emails[i:])
            if e not in c.mine:
                continue
        try:
            c.begin_send(e)
        except cl.ClaimLost:
            continue
        sent_log.append((c.runner, e))
        c.finish(e, sc.SENT)
        done += 1
    return done


# ------------------------------------------------------------------ log rules (pure)
def row(at, email, state, run, note="", runner="R", campaign="C"):
    return [at, email, state, runner, run, campaign, note]


def test_replay_owner_final_states_and_sending_protection():
    log = [cl.CLAIM_HEADER,
           row("t1", "a@x.com", "Claimed", "A"),
           row("t2", "a@x.com", "Claimed", "B"),
           row("t3", "a@x.com", "Released", "A", "expired: no heartbeat"),
           row("t4", "a@x.com", "Sending", "A"),                     # A came back too late: void
           row("t5", "b@x.com", "Claimed", "A"),
           row("t6", "b@x.com", "Sending", "A"),
           row("t7", "b@x.com", "Released", "A", "expired: no heartbeat"),   # never releases Sending
           row("t8", "c@x.com", "Claimed", "A"),
           row("t9", "c@x.com", "Sending", "A"),
           row("t10", "c@x.com", "Released", "A", "manual: checked in Crisp by Ana")]
    claims = cl.replay(log)
    assert [c.state for c in claims["a@x.com"]] == ["Released", "Claimed"]
    assert cl.owner(claims["a@x.com"]).run == "B"
    assert claims["b@x.com"][0].state == "Sending"
    assert claims["c@x.com"][0].state == "Released"


# ------------------------------------------------------------------ two runners at once
def test_two_runners_split_the_list_and_never_overlap():
    book = book_with()
    a, b = runner(book, "Ana"), runner(book, "Bo")
    log = []
    # interleave: each takes a small batch, then the other
    for _ in range(6):
        for r in (a, b):
            free = [e for e in EMAILS if not any((x, e) in log for x in ("Ana", "Bo"))]
            send_all(r, free, log, stop_after=2)
    emails = [e for _r, e in log]
    assert sorted(emails) == sorted(EMAILS) and len(emails) == len(set(emails))
    assert {r for r, _e in log} == {"Ana", "Bo"}


def test_a_batch_is_small_never_the_whole_list():
    book = book_with()
    a = runner(book, "Ana", batch=3)
    assert a.claim_batch(EMAILS) == EMAILS[:3]
    log = book.tabs[cl.CLAIMS_TAB].values
    assert sum(1 for r in log if r[2] == "Claimed") == 3


def test_race_on_the_same_recipient_has_exactly_one_winner():
    book = book_with(["race@x.com"])
    a, b = runner(book, "Ana", batch=1), runner(book, "Bo", batch=1)
    real = b.snapshot
    calls = []

    def b_snapshot(create=False):
        snap = real(create)
        calls.append(1)
        if len(calls) == 1:              # both saw the recipient free; A claims before B appends
            assert a.claim_batch(["race@x.com"]) == ["race@x.com"]
        return snap
    b.snapshot = b_snapshot
    assert b.claim_batch(["race@x.com"]) == []                  # B appended too, re-read, lost
    states = [(r[3], r[2]) for r in book.tabs[cl.CLAIMS_TAB].values[1:]]
    assert states == [("Ana", "Claimed"), ("Bo", "Claimed"), ("Bo", "Released")]   # the loser wrote why
    assert b.mine == [] and a.mine == ["race@x.com"]


def test_race_where_both_append_before_either_rereads():
    book = book_with(["race@x.com"])
    a, b = runner(book, "Ana", batch=1), runner(book, "Bo", batch=1)
    a._append(cl.CLAIMS_TAB, cl.CLAIM_HEADER, [a._row("race@x.com", cl.CLAIMED)])
    b._append(cl.CLAIMS_TAB, cl.CLAIM_HEADER, [b._row("race@x.com", cl.CLAIMED)])
    a.mine, b.mine = ["race@x.com"], ["race@x.com"]
    a.begin_send("race@x.com")                                   # earliest claim wins
    with pytest.raises(cl.ClaimLost):
        b.begin_send("race@x.com")                               # loser stops before the click


# ------------------------------------------------------------------ runners that die
def test_runner_dies_before_send_claims_expire_and_are_taken_over():
    book = book_with()
    clock = Clock()
    a = runner(book, "Ana", clock=clock)
    b = runner(book, "Bo", clock=clock)
    assert a.claim_batch(EMAILS) == EMAILS[:3]                   # Ana claims, then her PC dies
    clock.advance(2)
    b.heartbeat()
    assert b.claim_batch(EMAILS) == EMAILS[3:6]                  # not expired yet: Bo skips them
    clock.advance(4)                                             # 6 min without Ana's heartbeat
    b.heartbeat()
    got = b.claim_batch(EMAILS)
    assert got == EMAILS[:3]
    notes = [r[6] for r in book.tabs[cl.CLAIMS_TAB].values if r[2] == "Released"]
    assert any(n.startswith("expired: no heartbeat from Ana") and "released by Bo" in n for n in notes)
    with pytest.raises(cl.ClaimLost):
        a.begin_send(EMAILS[0])                                  # Ana comes back: she lost it


def test_runner_dies_after_the_send_click_is_never_resent():
    book = book_with(["x@x.com", "y@x.com"])
    clock = Clock()
    a = runner(book, "Ana", clock=clock, batch=1)
    a.claim_batch(["x@x.com"])
    a.begin_send("x@x.com")                                      # clicked Send, then crashed
    clock.advance(120)
    b = runner(book, "Bo", clock=clock, campaign="Other", recent_days=0)
    b.heartbeat()
    assert b.claim_batch(["x@x.com", "y@x.com"]) == ["y@x.com"]  # x is never taken
    snap = b.snapshot()
    assert "sending by Ana" in snap.blocker("x@x.com", b.run) and "check Crisp" in snap.blocker("x@x.com", b.run)
    assert ("x@x.com", snap.claims["x@x.com"][0].updated_at, "VIP", sc.UNCONFIRMED) in snap.history()
    b.resolve_manually("x@x.com", a.run, was_sent=True, who="Bo")   # a person checked Crisp
    assert b.snapshot().claims["x@x.com"][0].state == cl.SENT


def test_stop_releases_only_claims_that_never_reached_sending():
    book = book_with()
    a = runner(book, "Ana", batch=4)
    a.claim_batch(EMAILS)
    a.begin_send(EMAILS[0])
    assert a.release_all("stopped") == 3
    states = {c.email: c.state for cs in a.snapshot().claims.values() for c in cs}
    assert states[EMAILS[0]] == cl.SENDING and all(states[e] == cl.RELEASED for e in EMAILS[1:4])


# ------------------------------------------------------------------ campaigns and shared history
def test_two_campaigns_at_once_never_email_the_same_person_twice():
    both = EMAILS[:6]
    book = book_with(EMAILS)
    a = runner(book, "Ana", campaign="Spring")
    b = runner(book, "Bo", campaign="Upsell", recent_days=1)
    log = []
    for _ in range(4):
        send_all(a, both + EMAILS[6:9], log, stop_after=2)
        send_all(b, both + EMAILS[9:], log, stop_after=2)
    emails = [e for _r, e in log]
    assert len(emails) == len(set(emails)) and set(emails) == set(EMAILS)


def test_recent_rule_off_lets_another_campaign_email_them_but_never_the_same_one():
    book = book_with(EMAILS[:3])
    a = runner(book, "Ana", campaign="Spring")
    log = []
    send_all(a, EMAILS[:3], log)
    b = runner(book, "Bo", campaign="Upsell", recent_days=0)
    send_all(b, EMAILS[:3], log)                                 # a different campaign: allowed
    again = runner(book, "Cy", campaign="Spring", recent_days=0)
    send_all(again, EMAILS[:3], log)                             # the same campaign: never twice
    assert sorted(e for r, e in log) == sorted(EMAILS[:3] * 2)


def test_shared_history_feeds_the_recent_and_campaign_rules(tmp_path):
    book = book_with(["a@x.com", "b@x.com", "c@x.com"])
    other = runner(book, "Ana", campaign="VIP", batch=3)
    other.claim_batch(["a@x.com", "b@x.com", "c@x.com"])
    other.begin_send("a@x.com")
    other.finish("a@x.com", sc.SENT)                               # sent on Ana's computer
    snap = cl.Claims(other.src, "VIP", clock=other.clock, recent_days=7).snapshot()
    index = sc.HistoryIndex(tmp_path / "empty.csv", "VIP", 7)      # this computer has no local history
    for item in snap.history():
        index.add(*item)
    index.blocker = lambda e: snap.blocker(e)
    rows = [{"Contact Email": e, "Mail Sent": "FALSE"} for e in ("a@x.com", "b@x.com", "c@x.com")]
    outcomes = [o for _i, _r, o, _d in rules.classify(rows, "", 1, 3, history=index)]
    assert outcomes == [rules.CAMPAIGN_SENT, rules.CLAIMED, rules.CLAIMED]


def test_a_lost_result_write_is_queued_and_still_blocks_others(tmp_path):
    book = book_with(["a@x.com"])
    a = runner(book, "Ana", batch=1)
    a.claim_batch(["a@x.com"])
    a.begin_send("a@x.com")
    q = ss.PendingQueue(tmp_path / "pending.json")
    q.add_claim(a.src.id, a.src.tab, a._row("a@x.com", cl.SENT))   # "Sent" could not be written
    b = runner(book, "Bo", batch=1, campaign="Other", recent_days=0)
    assert b.claim_batch(["a@x.com"]) == []                       # Sending still protects it
    assert q.flush(a.src) == []
    assert b.snapshot().claims["a@x.com"][0].state == cl.SENT


def test_schema_lock_lets_one_runner_change_columns_at_a_time():
    book = book_with(["a@x.com"])
    a, b = runner(book, "Ana"), runner(book, "Bo")
    assert a.schema_lock()
    waits = []
    assert not b.schema_lock(wait=waits.append, tries=2) and waits == [3, 3]
    a.schema_unlock()
    assert b.schema_lock(wait=waits.append, tries=2)


def test_team_panel_lines():
    book = book_with()
    clock = Clock()
    a = runner(book, "Ana", clock=clock)
    a.claim_batch(EMAILS)
    a.begin_send(EMAILS[0])
    a.finish(EMAILS[0], sc.SENT)
    a.heartbeat()
    clock.advance(7)
    team = {t["runner"]: t for t in cl.Claims(a.src, "", clock=clock).snapshot().team()}
    t = team["Ana"]
    assert (t["sent"], t["claimed"], t["state"]) == (1, 2, "Not responding") and t["last_seen_min"] == 7


# ------------------------------------------------------------------ the real agent on a shared Sheet
ROWS = [["A", "a@x.com", "", "FALSE"], ["B", "b@x.com", "", "FALSE"], ["C", "c@x.com", "", "FALSE"],
        ["D", "d@x.com", "", "FALSE"], ["A again", "a@x.com", "", "FALSE"]]


def test_agent_skips_what_another_runner_holds_and_marks_rows_by_email(sheet_agent, capsys):
    book = FakeSpreadsheet({"Customers": [HEADER] + [list(r) for r in ROWS]})
    other = runner(book, "Ana", campaign="Other", batch=1, clock=sc.now_dhaka)
    other.claim_batch(["c@x.com"])                                 # Ana is on c@ right now
    mod = sheet_agent(book, CRISP_REFERENCE_FILTER="")
    sent = []
    ws = book.tabs["Customers"]

    def process(page, row):
        email = row["Contact Email"]
        mod.before_send_click()
        sent.append(email)
        if email == "a@x.com":                                     # a teammate sorts/inserts rows mid-run
            ws.values.insert(1, ["New", "new@x.com", "", "FALSE"])
            ws.values[2:] = sorted(ws.values[2:], key=lambda r: r[1], reverse=True)
        return True
    mod.process_recipient = process
    mod.main()
    out = capsys.readouterr().out
    assert sent == ["a@x.com", "b@x.com", "d@x.com"]               # c@ held by Ana; duplicate a@ skipped
    assert "Shared Sheet: 3 free for this runner · 1 claimed by others" in out
    marks = sorted((r[1], r[3]) for r in ws.values[1:])
    assert marks == [("a@x.com", "FALSE"), ("a@x.com", "TRUE"), ("b@x.com", "TRUE"), ("c@x.com", "FALSE"),
                     ("d@x.com", "TRUE"), ("new@x.com", "FALSE")]   # one a@ row ticked, by email
    states = {c.email: c.state for cs in cl.read_snapshot(other.src).claims.values() for c in cs
              if c.runner != "Ana" and "@" in c.email}
    assert states == {"a@x.com": "Sent", "b@x.com": "Sent", "d@x.com": "Sent"}


def test_agent_gives_up_a_recipient_taken_meanwhile(sheet_agent, capsys):
    book = FakeSpreadsheet({"Customers": [HEADER] + [list(r) for r in ROWS[:3]]})
    mod = sheet_agent(book, CRISP_REFERENCE_FILTER="", CRISP_CLAIM_BATCH="1")
    sent = []
    ws = book.tabs["Customers"]

    def process(page, row):
        email = row["Contact Email"]
        if email == "a@x.com":                                     # meanwhile someone ticked b@ by hand
            ws.values[2][3] = "TRUE"
        mod.before_send_click()
        sent.append(email)
        return True
    mod.process_recipient = process
    out, st = run_main(mod, capsys)
    assert sent == ["a@x.com", "c@x.com"] and st.elsewhere == 1 and st.completed == 2
    assert "already marked Mail Sent in the Sheet" in out


def test_agent_stop_releases_unsent_claims(sheet_agent, capsys, tmp_path):
    book = FakeSpreadsheet({"Customers": [HEADER] + [list(r) for r in ROWS[:4]]})
    stop = tmp_path / "stop"
    mod = sheet_agent(book, CRISP_REFERENCE_FILTER="", CRISP_CLAIM_BATCH="4", CRISP_STOP_FILE=str(stop))
    sent = []

    def process(page, row):
        mod.before_send_click()
        sent.append(row["Contact Email"])
        stop.write_text("stop")                                    # Stop pressed during the first send
        return True
    mod.process_recipient = process
    out, st = run_main(mod, capsys)
    assert sent == ["a@x.com"] and "Released 3 claimed recipient(s)" in out
    snap = cl.read_snapshot(ss.SheetSource(URL, "Customers", "k", client=FakeClient(book, SID)).connect())
    assert {c.email: c.state for cs in snap.claims.values() for c in cs if "@" in c.email} == {
        "a@x.com": "Sent", "b@x.com": "Released", "c@x.com": "Released", "d@x.com": "Released"}
    assert snap.team()[0]["state"] == "Stopped"


def test_dry_run_shows_shared_counts_and_writes_nothing(sheet_agent, capsys):
    book = FakeSpreadsheet({"Customers": [HEADER] + [list(r) for r in ROWS[:4]]})
    other = runner(book, "Ana", campaign="Other", batch=1, clock=sc.now_dhaka)
    other.claim_batch(["b@x.com"])
    before = json.dumps({t: w.values for t, w in book.tabs.items()})
    mod = sheet_agent(book, CRISP_REFERENCE_FILTER="", CRISP_DRY_RUN="true")
    out, st = run_main(mod, capsys)
    assert "Shared Sheet: 3 free for this runner · 1 claimed by others · 0 already sent" in out
    assert json.dumps({t: w.values for t, w in book.tabs.items()}) == before


def test_gui_preview_counts_claimed_by_others(tmp_path):
    from crisp_gui.csv_stats import read_stats
    book = book_with(["a@x.com", "b@x.com", "c@x.com"])
    other = runner(book, "Ana", campaign="Other", batch=1, clock=sc.now_dhaka)
    other.claim_batch(["b@x.com"])
    shared = tmp_path / "shared.json"
    shared.write_text(json.dumps(cl.read_raw(other.src)))
    csv_path = tmp_path / "p.csv"
    csv_path.write_text("Contact Email,Mail Sent\na@x.com,FALSE\nb@x.com,FALSE\nc@x.com,FALSE\n")
    st = read_stats(str(csv_path), "", shared_file=str(shared), campaign="VIP")
    assert st.claimed == 1 and st.eligible == 2 and "2 free for you to send" in st.preview()
    assert "1 claimed by other runners" in st.preview()


def run_main(mod, capsys):
    from crisp_gui import run_state as rs
    mod.main()
    out = capsys.readouterr().out
    p = rs.RunParser(dry_run=False)
    p.feed(out.encode("utf-8"))
    return out, p.finish(0)
