"""Settings > "Send again to people already emailed": follow-ups and new campaigns."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import claims as cl  # noqa: E402
import recipient_rules as rules  # noqa: E402
import send_control as sc  # noqa: E402

ROWS = [
    {"Contact Email": "sent@x.com", "Full Name": "A", "Mail Sent": "TRUE"},
    {"Contact Email": "camp@x.com", "Full Name": "B", "Mail Sent": "FALSE"},
    {"Contact Email": "new@x.com", "Full Name": "C", "Mail Sent": "FALSE"},
    {"Contact Email": "new@x.com", "Full Name": "C again", "Mail Sent": "FALSE"},
    {"Contact Email": "bad@", "Full Name": "D", "Mail Sent": "FALSE"},
    {"Contact Email": "stop@x.com", "Full Name": "E", "Mail Sent": "TRUE"},
]


class History:
    def campaign_sent(self, email):
        return ("2026-11-01 10:00", "Promo", "Sent") if email == "camp@x.com" else None

    def recent(self, email):
        return None

    def claimed(self, email):
        return ""


def outcomes(allow):
    return [o for _i, _r, o, _d in rules.classify(ROWS, "", 1, len(ROWS), {"stop@x.com"}, history=History(),
                                                  allow_resend=allow)]


def test_off_keeps_every_skip():
    assert outcomes(False) == [rules.ALREADY_SENT, rules.CAMPAIGN_SENT, rules.ELIGIBLE, rules.DUPLICATE,
                               rules.INVALID, rules.ALREADY_SENT]


def test_on_sends_again_but_once_per_run_and_never_to_bad_or_do_not_contact():
    assert outcomes(True) == [rules.ELIGIBLE, rules.ELIGIBLE, rules.ELIGIBLE, rules.DUPLICATE,
                              rules.INVALID, rules.DO_NOT_CONTACT]


def test_shared_sheet_sends_before_the_run_do_not_block_but_sends_during_it_do():
    start = datetime(2026, 11, 15, 12, 0, tzinfo=sc.DHAKA)
    old = cl.Claim(email="a@x.com", run="r1", runner="Ann", campaign="Promo", state=cl.SENT,
                   claimed_at="2026-11-10 09:00:00", updated_at="2026-11-10 09:00:00", order=1)
    during = cl.Claim(email="a@x.com", run="r2", runner="Bob", campaign="Promo", state=cl.SENT,
                      claimed_at="2026-11-15 12:05:00", updated_at="2026-11-15 12:05:00", order=2)
    now = start + timedelta(minutes=10)
    assert cl.sent_blocks(old, "Promo", 7, now)                       # off: blocked
    assert cl.sent_blocks(old, "Promo", 7, now, resend_before=start) == ""
    assert cl.sent_blocks(during, "Promo", 7, now, resend_before=start)
