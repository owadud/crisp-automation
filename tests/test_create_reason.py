"""When Crisp refuses to create a conversation, the agent reports what Crisp shows."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_campaign_results import agent  # noqa: E402,F401

PAGE = """<!doctype html><html><body>
<div class="c-base-modal"><input placeholder="Enter the email of the user">
  <div class="c-field-error">This email address is invalid</div>
  <div class="c-base-toast c-base-toast--warning" role="alert">Contact is blocked</div>
  <div class="c-base-toast" style="display:none">Hidden old toast</div>
</div></body></html>"""


def test_crisp_messages_reads_visible_errors_only(agent):
    pw_mod = pytest.importorskip("playwright.sync_api")
    with pw_mod.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(PAGE)
        said = agent.crisp_messages(page)
        browser.close()
    assert said == ["This email address is invalid", "Contact is blocked"]


def test_crisp_messages_never_raises(agent):
    class Broken:
        def evaluate(self, js):
            raise RuntimeError("page closed")
    assert agent.crisp_messages(Broken()) == []
