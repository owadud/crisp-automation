"""Subject selection against a mock of Crisp's dialog (headless Chromium).

Reproduces the bug where only the first 10 characters of the subject
("⏳ Final Ho") were used, and checks the agent now always ends with the
FULL subject or refuses to create the conversation.
"""

import importlib.util
import json
import os
from pathlib import Path
from urllib.parse import quote

import pytest

ROOT = Path(__file__).resolve().parent.parent
MOCK = (ROOT / "tests" / "crisp_subject_mock.html").resolve()
SUBJECT = "⏳ Final Hours: Your VIP Discount Ends Tonight"
OTHERS = ["[Action Required] Exclusive VIP Access👀", "[Action Required] Pick Up Where You Left Off"]

sync_api = pytest.importorskip("playwright.sync_api")


def load_agent(path, subject, name):
    os.environ["CRISP_SUBJECT"] = subject
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def open_mock(browser, empty, saved):
    page = browser.new_page()
    url = MOCK.as_uri() + f"?empty={quote(json.dumps(empty))}&saved={quote(json.dumps(saved))}"
    page.goto(url)
    return page


def run_fill(agent, page):
    agent.CREATE_WAIT = 0
    try:
        agent.fill_new_conversation(page, "customer@example.com", "WPXPO User")
        return page.evaluate("window.created"), None
    except Exception as exc:  # noqa: BLE001
        return page.evaluate("window.created"), exc


def test_full_subject_when_not_in_initial_menu(browser):
    """The screenshot case: subject not listed on empty click, found after typing."""
    agent = load_agent(ROOT / "crisp_email_agent.py", SUBJECT, "agent_fixed_1")
    page = open_mock(browser, OTHERS, OTHERS + [SUBJECT])
    created, err = run_fill(agent, page)
    assert err is None, err
    assert created == SUBJECT


def test_full_subject_when_listed_on_empty_click(browser):
    agent = load_agent(ROOT / "crisp_email_agent.py", SUBJECT, "agent_fixed_2")
    page = open_mock(browser, OTHERS + [SUBJECT], OTHERS + [SUBJECT])
    created, err = run_fill(agent, page)
    assert err is None, err
    assert created == SUBJECT


def test_full_subject_even_if_not_saved_in_crisp(browser):
    """Only the typed-text row exists; it must be the FULL subject."""
    agent = load_agent(ROOT / "crisp_email_agent.py", SUBJECT, "agent_fixed_3")
    page = open_mock(browser, OTHERS, OTHERS)
    created, err = run_fill(agent, page)
    assert err is None, err
    assert created == SUBJECT


def test_never_creates_with_a_different_subject(browser):
    """If the full subject can't be set, the agent must stop, not create."""
    agent = load_agent(ROOT / "crisp_email_agent.py", SUBJECT, "agent_fixed_4")
    page = open_mock(browser, OTHERS, OTHERS)
    # Break typing: the input silently keeps only 10 characters.
    page.evaluate("""() => {
        const i = document.getElementById('subject');
        i.addEventListener('input', () => { if (i.value.length > 10) i.value = i.value.slice(0, 10); }, true);
    }""")
    created, err = run_fill(agent, page)
    assert created is None
    assert err is not None


def test_existing_subject_still_works(browser):
    """The subject that already worked in production must still work."""
    agent = load_agent(ROOT / "crisp_email_agent.py", OTHERS[0], "agent_fixed_5")
    page = open_mock(browser, OTHERS, OTHERS)
    created, err = run_fill(agent, page)
    assert err is None, err
    assert created == OTHERS[0]


ORIGINAL = os.environ.get("CRISP_ORIGINAL_AGENT")


@pytest.mark.skipif(not ORIGINAL, reason="set CRISP_ORIGINAL_AGENT to the old agent to reproduce the bug")
def test_original_agent_reproduces_truncation(browser):
    agent = load_agent(Path(ORIGINAL), SUBJECT, "agent_original")
    page = open_mock(browser, OTHERS, OTHERS)   # subject not listed, as in the screenshot
    created, _ = run_fill(agent, page)
    assert created == "⏳ Final Ho"
