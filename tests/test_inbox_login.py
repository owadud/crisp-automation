"""Browser mode: when is the logged-in Crisp Inbox ready? (Crisp dropped the word 'Inbox', Nov 2026.)"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_v36 import agent  # noqa: E402,F401

INBOX = "https://app.crisp.chat/website/5b6a1eda-9935-421d-9474-ba8064296611/inbox/"


def test_inbox_without_the_word_inbox_counts_as_logged_in(agent, tmp_path):
    from test_v36 import write
    mod = agent(write(tmp_path / "r.csv", ["Full Name", "Contact Email"], [["Ann", "a@x.com"]]))
    assert mod.inbox_is_open(INBOX, "All conversations\nFilters\nUnread\nAnn Lee")
    assert mod.inbox_is_open(INBOX + "session_abc/", "Filters Mine")


def test_login_page_or_loading_page_is_not_the_inbox(agent, tmp_path):
    from test_v36 import write
    mod = agent(write(tmp_path / "r.csv", ["Full Name", "Contact Email"], [["Ann", "a@x.com"]]))
    assert not mod.inbox_is_open("https://app.crisp.chat/initiate/login/", "Log in to Crisp\nEmail\nPassword")
    assert not mod.inbox_is_open(INBOX, "Filters", password_fields=1)
    assert not mod.inbox_is_open(INBOX, "")                          # still loading
    assert not mod.inbox_is_open("https://app.crisp.chat/", "Filters")
