"""A/B test: several Crisp shortcuts in one campaign, split evenly, each send
labelled with its shortcut and version so Campaign Results can compare them."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import campaign_results as cr  # noqa: E402
import send_control as sc  # noqa: E402
from crisp_gui.preview_dialog import preview_rows  # noqa: E402
from test_v36 import agent, write  # noqa: E402,F401

HEADER = ["Full Name", "Contact Email", "Reference", "Mail Sent"]


def test_parse_variants():
    assert sc.parse_variants("!subs-small", "") == [("!subs-small", "")]          # old behaviour
    assert sc.parse_variants("!a, !b", "v1, v2") == [("!a", "v1"), ("!b", "v2")]
    assert sc.parse_variants("!a\n!b\n!c", "v1") == [("!a", "v1"), ("!b", "!b"), ("!c", "!c")]
    for shortcuts, versions, msg in (("", "", "Enter a Crisp shortcut"), ("!a, !A", "", "listed twice"),
                                     ("!a", "v1, v2", "one label per shortcut"),
                                     ("!a, !b", "v1, v1", "different version label")):
        with pytest.raises(ValueError, match=msg):
            sc.parse_variants(shortcuts, versions)


def test_pick_keeps_the_split_even_and_counts_history(tmp_path):
    h = tmp_path / "history.csv"
    variants = [("!a", "v1"), ("!b", "v2")]
    for shortcut in ("!a", "!a", "!b"):
        sc.append_history(h, Email="x@x.com", Result="Sent", Mode="live", Campaign="VIP", Shortcut=shortcut)
    sc.append_history(h, Email="y@x.com", Result="Sent", Mode="dry", Campaign="VIP", Shortcut="!b")  # dry: no
    sc.append_history(h, Email="z@x.com", Result="Sent", Mode="live", Campaign="Other", Shortcut="!b")
    assert sc.variant_counts(h, "vip", variants) == [2, 1]
    assert sc.pick_variant([2, 1]) == 1 and sc.pick_variant([1, 1]) == 0


def test_live_run_alternates_shortcuts_and_records_versions(agent, tmp_path, capsys):
    rows = [[f"P{i}", f"p{i}@x.com", "Shahriar", "FALSE"] for i in range(5)]
    path = write(tmp_path / "r.csv", HEADER, rows)
    mod = agent(path, CRISP_SHORTCUT="!subs-small, !subs-big", CRISP_SHORTCUT_VERSION="v1, v2")
    used = []

    def process(page, row):
        used.append((row["Contact Email"], mod.SHORTCUT, mod.SHORTCUT_VERSION))
        return True
    mod.process_recipient = process
    mod.main()
    out = capsys.readouterr().out
    assert "A/B test, split evenly: !subs-small (v1) · !subs-big (v2)" in out
    assert [u[1] for u in used] == ["!subs-small", "!subs-big", "!subs-small", "!subs-big", "!subs-small"]
    hist = sc.read_history(tmp_path / "history.csv")
    assert [(r["Shortcut"], r["Version"]) for r in hist] == [(s, v) for _e, s, v in used]

    # a later run of the same campaign continues the even split (3 small, 2 big so far)
    path2 = write(tmp_path / "r2.csv", HEADER, [["Q", "q@x.com", "Shahriar", "FALSE"]])
    mod = agent(path2, CRISP_SHORTCUT="!subs-small, !subs-big", CRISP_SHORTCUT_VERSION="v1, v2")
    used2 = []
    mod.process_recipient = lambda page, row: used2.append(mod.SHORTCUT) or True
    mod.main()
    assert used2 == ["!subs-big"]

    # Campaign Results compares the two versions
    store = cr.ResultsStore(tmp_path / "results.json")
    store.sync_history(sc.read_history(tmp_path / "history.csv"))
    versions = {g["version"]: g["sent"] for g in cr.compare(store.rows("VIP"), "version")}
    assert versions == {"v1": 3, "v2": 3}


def test_one_shortcut_works_as_before(agent, tmp_path, capsys):
    path = write(tmp_path / "r.csv", HEADER, [["A", "a@x.com", "Shahriar", "FALSE"]])
    mod = agent(path, CRISP_SHORTCUT="!subs-small", CRISP_SHORTCUT_VERSION="")
    used = []
    mod.process_recipient = lambda page, row: used.append((mod.SHORTCUT, mod.SHORTCUT_VERSION)) or True
    mod.main()
    out = capsys.readouterr().out
    assert used == [("!subs-small", "")] and "Shortcut: !subs-small" in out and "A/B" not in out


def test_bad_labels_stop_before_any_send(agent, tmp_path, capsys):
    path = write(tmp_path / "r.csv", HEADER, [["A", "a@x.com", "Shahriar", "FALSE"]])
    mod = agent(path, CRISP_SHORTCUT="!a", CRISP_SHORTCUT_VERSION="v1, v2")
    sent = []
    mod.process_recipient = lambda page, row: sent.append(1) or True
    with pytest.raises(RuntimeError, match="one label per shortcut"):
        mod.main()
    assert sent == []


def test_preview_table_shows_the_expected_shortcut_per_row():
    plan = [{"row": i, "email": f"p{i}@x.com", "name": "P", "fallback": False, "reference": "",
             "action": "Send" if i != 2 else "Skip: duplicate", "detail": "", "send": i != 2} for i in range(1, 5)]
    rows = preview_rows(plan, {"subject": "S", "shortcut": "!a, !b", "shortcut_version": "v1, v2", "segment": ""})
    assert [r[4] for r in rows] == ["!a (v1)", "", "!b (v2)", "!a (v1)"]
