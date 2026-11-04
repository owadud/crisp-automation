"""Phase 2: row range selection. Serial = position among data rows, header excluded."""

import csv
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crisp_gui.csv_stats import parse_range, read_stats  # noqa: E402

HEADER = ("Full Name", "Contact Email", "Reference", "Mail Sent")


def write_csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)
    return path


def sample(path):
    # serials:      1        2          3            4         5         6         7
    return write_csv(path, [
        ("A", "a@x.com", "R", ""), ("B", "b@x.com", "R", "TRUE"), ("C", "A@X.com ", "R", ""),
        ("D", "d@x.com", "Other", ""), ("E", "", "R", ""), ("F", "f@x.com", "R", ""),
        ("G", "g@x.com", "R", ""),
    ])


@pytest.mark.parametrize("f, t, expected", [
    ("", "", (1, 10, "", "")),
    ("3", "", (3, 10, "", "")),
    ("", "4", (1, 4, "", "")),
    ("2", "2", (2, 2, "", "")),
    ("0", "5", (0, 0, "From row must be 1 or more.", "from")),
    ("6", "5", (0, 0, "From row must not be after To row.", "from")),
    ("1", "11", (0, 0, "The file has only 10 rows.", "to")),
    ("11", "", (0, 0, "The file has only 10 rows.", "from")),
    ("x", "", (0, 0, "Enter a whole number.", "from")),
])
def test_parse_range(f, t, expected):
    assert parse_range(f, t, 10) == expected


def test_preview_counts_only_rows_in_range(tmp_path):
    p = sample(tmp_path / "r.csv")
    s = read_stats(str(p), "R", "2", "6")
    assert s.ok and not s.range_error
    # rows 2-6: B sent, C eligible, D other ref, E no email, F eligible
    assert (s.in_range, s.eligible, s.marked_sent, s.duplicates) == (5, 2, 1, 0)
    assert s.preview().startswith("Rows 2–6 of 7: 5 rows, 2 eligible, 1 already sent")

    whole = read_stats(str(p), "R")
    # a@x.com (row 1) and "A@X.com " (row 3) are the same email
    assert (whole.total, whole.eligible, whole.duplicates) == (7, 3, 1)  # duplicate skipped (Phase 3)
    assert "1 duplicate skipped" in whole.preview() and not whole.preview().startswith("Rows")


def test_invalid_range_is_reported_not_crashed(tmp_path):
    p = sample(tmp_path / "r.csv")
    s = read_stats(str(p), "R", "5", "3")
    assert s.ok and s.range_error and s.range_error_field == "from" and s.eligible == 0


@pytest.fixture
def agent_with_range(tmp_path, monkeypatch):
    pytest.importorskip("playwright")

    def load(row_from, row_to):
        csv_path = sample(tmp_path / "agent.csv")
        monkeypatch.setenv("CRISP_CSV_FILE", str(csv_path))
        monkeypatch.setenv("CRISP_REFERENCE_FILTER", "R")
        monkeypatch.setenv("CRISP_ROW_FROM", row_from)
        monkeypatch.setenv("CRISP_ROW_TO", row_to)
        sys.modules.pop("crisp_email_agent", None)
        return importlib.import_module("crisp_email_agent"), csv_path
    yield load
    sys.modules.pop("crisp_email_agent", None)


@pytest.mark.parametrize("f, t", [("", ""), ("2", "6"), ("3", ""), ("", "1"), ("7", "7")])
def test_agent_processes_exactly_the_previewed_rows(agent_with_range, f, t):
    mod, csv_path = agent_with_range(f, t)
    rows, _ = mod.load_csv()
    picked = mod.get_eligible(rows)
    assert len(picked) == read_stats(str(csv_path), "R", f, t).eligible
    start, end, _, _ = parse_range(f, t, len(rows))
    assert all(start <= i + 1 <= end for i, _ in picked)


def test_agent_refuses_invalid_range(agent_with_range):
    mod, _ = agent_with_range("5", "99")
    rows, _ = mod.load_csv()
    with pytest.raises(RuntimeError, match="Invalid row range"):
        mod.get_eligible(rows)
