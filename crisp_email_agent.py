#!/usr/bin/env python3

"""
Crisp Email Agent
=================

Ubuntu + Playwright UI automation for Crisp.

Workflow:
CSV
 -> New email conversation
 -> fill recipient/name/subject
 -> insert existing Crisp shortcut
 -> send
 -> add pending-cart segment
 -> resolve conversation
 -> mark Mail Sent=TRUE
 -> next recipient

CSV columns:
Full Name, Contact Email, Reference, Mail Sent

Only rows with:
- Reference == "Shahriar"
- Contact Email present
- Mail Sent not already checked

DRY_RUN is intentionally FALSE because real sending is required.
"""

import csv
import os
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


# ============================================================
# CONFIG
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

CSV_FILE = Path(os.environ.get("CRISP_CSV_FILE", str(BASE_DIR / "wowinvoice_users.csv"))).expanduser().resolve()

SHORTCUT = os.environ.get("CRISP_SHORTCUT", "recommend-offer")

SUBJECT = os.environ.get("CRISP_SUBJECT", "[Action Required] Exclusive VIP Access👀")

REFERENCE_FILTER = os.environ.get("CRISP_REFERENCE_FILTER", "Shahriar")

# Segment to add after sending
SEGMENT_NAME = os.environ.get("CRISP_SEGMENT", "pending-cart")

# Keep FALSE: actual email sending.
DRY_RUN = os.environ.get("CRISP_DRY_RUN", "true").lower() in {"1", "true", "yes", "on"}

PROFILE_DIR = Path(os.environ.get("CRISP_PROFILE_DIR", str(Path.home() / ".crisp_email_agent" / "browser_profile"))).expanduser().resolve()

CRISP_URL = "https://app.crisp.chat/"

ELEMENT_TIMEOUT = 15000

CREATE_WAIT = 2.5

SHORTCUT_WAIT = 1.5

SEND_WAIT = 4

BETWEEN_EMAILS = 3

# The desktop GUI creates this file to ask for a graceful stop. The agent
# only checks it BETWEEN recipients, so a send is never interrupted halfway.
STOP_FILE = os.environ.get("CRISP_STOP_FILE", "").strip()


def stop_requested():
    return bool(STOP_FILE) and Path(STOP_FILE).exists()


# ============================================================
# CSV
# ============================================================

def clean(value):
    return str(value or "").strip()


def is_checked(value):
    value = clean(value).lower()

    return value in {
        "true",
        "1",
        "yes",
        "y",
        "checked",
        "sent",
        "x",
        "✓",
        "☑",
    }


def load_csv():
    if not CSV_FILE.exists():
        raise FileNotFoundError(
            f"CSV file not found:\n{CSV_FILE}"
        )

    with CSV_FILE.open(
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        reader = csv.DictReader(f)

        if not reader.fieldnames:
            raise RuntimeError(
                "CSV has no header."
            )

        fieldnames = list(reader.fieldnames)

        rows = list(reader)

    required = {
        "Full Name",
        "Contact Email",
        "Reference",
        "Mail Sent",
    }

    missing = required - set(fieldnames)

    if missing:
        raise RuntimeError(
            "Missing CSV columns: "
            + ", ".join(sorted(missing))
        )

    return rows, fieldnames


def save_csv(rows, fieldnames):

    temp = CSV_FILE.with_name(
        CSV_FILE.stem + ".tmp.csv"
    )

    with temp.open(
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            extrasaction="ignore"
        )

        writer.writeheader()
        writer.writerows(rows)

    os.replace(temp, CSV_FILE)


def get_eligible(rows):

    result = []

    for index, row in enumerate(rows):

        reference = clean(
            row.get("Reference")
        )

        email = clean(
            row.get("Contact Email")
        )

        sent = row.get(
            "Mail Sent",
            ""
        )

        if (
            reference.lower()
            != REFERENCE_FILTER.lower()
        ):
            continue

        if not email:
            continue

        if is_checked(sent):
            continue

        result.append(
            (index, row)
        )

    return result


# ============================================================
# GENERIC PLAYWRIGHT HELPERS
# ============================================================

def visible(locator):

    try:
        return locator.is_visible()

    except Exception:
        return False


def enabled(locator):

    try:
        return locator.is_enabled()

    except Exception:
        return True


def box(locator):

    try:
        return locator.bounding_box()

    except Exception:
        return None


def click_first_visible(locators):

    for locator in locators:

        try:

            count = locator.count()

            for i in range(count):

                item = locator.nth(i)

                if visible(item):

                    item.click()

                    return True

        except Exception:
            continue

    return False


# ============================================================
# CRISP: WAIT / NEW CONVERSATION
# ============================================================

def wait_for_crisp(page, timeout_seconds=300):
    """Wait until the authenticated Crisp Inbox is actually available.

    The agent proceeds automatically after login; no terminal Enter or GUI
    Continue button is required. The inbox route plus its visible navigation
    text are used to avoid starting while the login page is still loading.
    """
    print("Waiting for Crisp Inbox login...")
    deadline = time.time() + timeout_seconds
    last_notice = 0

    while time.time() < deadline:
        if page.is_closed():
            raise RuntimeError("Crisp browser was closed before the Inbox loaded.")

        try:
            current_url = page.url.lower()
            body_text = page.locator("body").inner_text(timeout=1500).lower()
            inbox_route = "/inbox" in current_url
            inbox_navigation = "inbox" in body_text and "filters" in body_text

            if inbox_route and inbox_navigation:
                # Let the SPA finish rendering the Inbox controls.
                page.wait_for_timeout(1200)
                print("Crisp Inbox detected. Starting automatically.")
                return
        except Exception:
            pass

        now = time.time()
        if now - last_notice >= 10:
            print("Still waiting for Crisp login / Inbox to finish loading...")
            last_notice = now
        time.sleep(1)

    raise TimeoutError(
        "Crisp Inbox was not detected within 5 minutes. "
        "Complete login and make sure the Inbox page is open."
    )


def wait_for_new_email_dialog(
    page,
    timeout_ms
):

    deadline = (
        time.time()
        + timeout_ms / 1000
    )

    selectors = [
        'input[placeholder*="email of the user" i]',
    ]

    while time.time() < deadline:

        for selector in selectors:

            try:

                locator = page.locator(
                    selector
                )

                for i in range(
                    locator.count()
                ):

                    item = locator.nth(i)

                    if visible(item):
                        return True

            except Exception:
                pass

        time.sleep(0.1)

    return False


def open_new_conversation(page):

    """
    Crisp's current UI has a + beside Filters.

    We first try semantic selectors, then locate the +
    relative to Filters.
    """

    selectors = [
        'button[aria-label*="new" i]',
        'button[aria-label*="create" i]',
        'button[aria-label*="conversation" i]',
        'button[title*="new" i]',
        'button[title*="create" i]',
        'button:has-text("New conversation")',
        'button:has-text("New Conversation")',
    ]

    for selector in selectors:

        try:

            locator = page.locator(
                selector
            )

            for i in range(
                locator.count()
            ):

                item = locator.nth(i)

                if not visible(item):
                    continue

                item.click()

                if wait_for_new_email_dialog(
                    page,
                    4000
                ):
                    return

        except Exception:
            continue

    # --------------------------------------------------------
    # Current Crisp layout: Filters + plus
    # --------------------------------------------------------

    try:

        filters = page.get_by_text(
            "Filters",
            exact=True
        ).last

        filters.wait_for(
            state="visible",
            timeout=8000
        )

        filters_box = box(
            filters
        )

        if not filters_box:

            raise RuntimeError(
                "Could not determine Filters position."
            )

        y = (
            filters_box["y"]
            + filters_box["height"] / 2
        )

        start_x = (
            filters_box["x"]
            + filters_box["width"]
            + 10
        )

        for x in range(
            int(start_x),
            int(start_x + 90),
            8
        ):

            page.mouse.click(
                x,
                y
            )

            if wait_for_new_email_dialog(
                page,
                800
            ):
                return

    except Exception:
        pass

    raise RuntimeError(
        "Could not open Crisp's + / New conversation dialog."
    )


def _norm(text):
    """Normalise text for comparing subjects (emoji variation selectors, spaces)."""
    import unicodedata
    text = unicodedata.normalize("NFKC", text or "")
    for ch in ("\ufe0f", "\u200b", "\u200d", "\u00a0"):
        text = text.replace(ch, " " if ch == "\u00a0" else "")
    return re.sub(r"\s+", " ", text).strip().casefold()


def _subject_input(page):
    return page.locator('input[placeholder*="subject of the email" i]').first


def _menu_rows(page):
    """Visible rows of Crisp's custom select menu -> [(locator, text)]."""
    rows = []
    wrappers = page.locator(".c-base-select-menu-item__wrapper")
    for i in range(wrappers.count()):
        w = wrappers.nth(i)
        try:
            if not w.is_visible():
                continue
            label = w.locator(".c-base-select-menu-item__label").first
            text = label.inner_text().strip() if label.count() else w.inner_text().strip()
            rows.append((w, text))
        except Exception:
            continue
    return rows


def _find_subject_row(page):
    """Only a row whose text is EXACTLY the full subject is accepted.

    (A partial match used to accept Crisp's echo of a half-typed subject,
    e.g. '⏳ Final Ho', which then became the email subject.)
    """
    want = _norm(SUBJECT)
    rows = _menu_rows(page)
    for idx, (w, text) in enumerate(rows):
        if _norm(text) == want:
            return idx, w, rows
    return None, None, rows


def _subject_value(field):
    try:
        return field.input_value()
    except Exception:
        return None


def _subject_is_full(field):
    value = _subject_value(field)
    return value is not None and _norm(value) == _norm(SUBJECT)


def _debug_subject(page, tag):
    try:
        shot = BASE_DIR / f"debug_{tag}.png"
        page.screenshot(path=str(shot))
        print(f"  Debug screenshot saved: {shot}", flush=True)
    except Exception as exc:
        print(f"  Debug screenshot failed: {exc!r}", flush=True)
    try:
        html = page.evaluate(
            """() => {
                const el = document.querySelector('.c-base-select-menu-item__wrapper');
                const root = el ? (el.closest('[class*=select-menu]') || el.parentElement) : null;
                return root ? root.outerHTML.slice(0, 6000) : 'NO MENU ELEMENT FOUND';
            }"""
        )
        out = BASE_DIR / f"debug_{tag}.html"
        out.write_text(html, encoding="utf-8")
        print(f"  Debug menu HTML saved: {out}", flush=True)
    except Exception as exc:
        print(f"  Debug HTML dump failed: {exc!r}", flush=True)


def pick_email_suggestion(page, email_field, email):
    """Best-effort: click the matching contact suggestion like a human does."""
    try:
        page.wait_for_timeout(700)
        for w, text in _menu_rows(page):
            if _norm(email) in _norm(text):
                w.click(timeout=2000)
                page.wait_for_timeout(400)
                print("  Email suggestion selected.", flush=True)
                return
    except Exception:
        pass


def select_subject_from_dropdown(page):
    """Replicates the manual flow recorded in subject_probe_report.txt:
    click the EMPTY Subject input -> menu opens -> click the matching row
    (div.c-base-select-menu-item__wrapper). Returns True if the menu closed
    after the click. Enter is only used while the menu is open."""
    field = _subject_input(page)
    full_subject = SUBJECT.strip()

    for attempt in range(1, 5):
        try:
            field.scroll_into_view_if_needed()
            field.fill("")
            field.click()
            page.wait_for_timeout(900)

            idx, row, rows = _find_subject_row(page)
            if row is None and attempt >= 2:
                # Some Crisp builds only list the subject after typing.
                # Type the FULL subject (never a prefix) and wait until the
                # field really contains all of it before looking at the menu.
                field.press_sequentially(full_subject, delay=45)
                for _ in range(20):
                    if _subject_is_full(field):
                        break
                    page.wait_for_timeout(150)
                if not _subject_is_full(field):
                    print(
                        f"  Subject typing incomplete: {_subject_value(field)!r}; retrying.",
                        flush=True,
                    )
                    continue
                page.wait_for_timeout(900)
                idx, row, rows = _find_subject_row(page)

            print(f"  Subject menu rows (attempt {attempt}): {[t for _, t in rows]}", flush=True)
            if row is None:
                continue

            row.scroll_into_view_if_needed()
            b = row.bounding_box()

            # Strategy 1: normal click
            try:
                row.hover(timeout=2000)
                page.wait_for_timeout(200)
                row.click(timeout=2500)
            except Exception as exc:
                print(f"  Row click warning: {exc!r}", flush=True)
            page.wait_for_timeout(700)

            # Strategy 2: raw mouse down/up on the row centre
            if _find_subject_row(page)[1] is not None and b:
                cx, cy = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
                page.mouse.move(cx, cy)
                page.wait_for_timeout(150)
                page.mouse.down()
                page.wait_for_timeout(80)
                page.mouse.up()
                page.wait_for_timeout(700)

            # Strategy 3: keyboard, only while the menu is still open
            if _find_subject_row(page)[1] is not None:
                try:
                    field.focus()
                    for _ in range(idx + 1):
                        page.keyboard.press("ArrowDown")
                        page.wait_for_timeout(120)
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(700)
                except Exception:
                    pass

            if _find_subject_row(page)[1] is None:
                val = _subject_value(field)
                if not _subject_is_full(field):
                    # Menu closed but the field doesn't hold the whole subject.
                    print(
                        f"  Subject field shows {val!r}, not the full subject; retrying.",
                        flush=True,
                    )
                    continue
                print(f"  Subject chosen from dropdown. Input value: {val!r}", flush=True)
                return True
        except Exception as exc:
            print(f"  Subject select attempt {attempt} error: {exc!r}", flush=True)
            page.wait_for_timeout(500)

    _debug_subject(page, "subject_select_failed")
    return False


def dialog_closed(page, timeout_ms=8000):
    try:
        _subject_input(page).wait_for(state="hidden", timeout=timeout_ms)
        return True
    except Exception:
        return False


def click_create_conversation(page):
    create_button = page.get_by_role(
        "button",
        name=re.compile(r"Create Conversation", re.I)
    ).first
    create_button.wait_for(state="visible", timeout=ELEMENT_TIMEOUT)

    for _ in range(40):
        try:
            if create_button.is_enabled():
                break
        except Exception:
            pass
        time.sleep(0.25)
    if not create_button.is_enabled():
        raise RuntimeError("Create Conversation button is disabled.")

    try:
        create_button.click(timeout=4000)
    except Exception:
        bb = create_button.bounding_box()
        if not bb:
            raise
        page.mouse.click(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
    print("  Create Conversation clicked.", flush=True)


def fill_new_conversation(
    page,
    email,
    name
):
    email_field = page.locator(
        'input[placeholder*="email of the user" i]'
    ).first
    name_field = page.locator(
        'input[placeholder*="full name of the user" i]'
    ).first
    subject_field = _subject_input(page)

    email_field.wait_for(state="visible", timeout=ELEMENT_TIMEOUT)
    name_field.wait_for(state="visible", timeout=ELEMENT_TIMEOUT)
    subject_field.wait_for(state="visible", timeout=ELEMENT_TIMEOUT)

    email_field.fill(email)
    pick_email_suggestion(page, email_field, email)
    name_field.click()
    name_field.fill(name or email.split("@")[0])

    for attempt in range(1, 4):
        chosen = select_subject_from_dropdown(page)
        if not chosen:
            print("  Subject could not be chosen from the dropdown.", flush=True)
            break

        # Last check right before Create: never create a conversation with a
        # partial or different subject.
        if not _subject_is_full(subject_field):
            print(
                f"  Subject changed to {_subject_value(subject_field)!r} before Create; retrying.",
                flush=True,
            )
            continue

        click_create_conversation(page)

        if dialog_closed(page, 8000):
            print("  New-conversation dialog closed; conversation created.", flush=True)
            time.sleep(CREATE_WAIT)
            return

        print(f"  Dialog still open after Create (attempt {attempt}); retrying.", flush=True)

    _debug_subject(page, "create_failed")
    raise RuntimeError(
        "Crisp did not accept the subject / create the conversation. "
        "See debug_subject_select_failed.* or debug_create_failed.* in the project folder."
    )


# ============================================================
# CRISP - COMPOSER
# ============================================================

def find_composer(page):

    selectors = [
        'textarea[placeholder*="Send your message" i]',
        'textarea[placeholder*="message" i]',
        '[contenteditable="true"]',
        '[role="textbox"]',
    ]

    candidates = []

    for selector in selectors:

        try:

            locator = page.locator(
                selector
            )

            for i in range(
                locator.count()
            ):

                item = locator.nth(i)

                if not visible(item):
                    continue

                item_box = box(
                    item
                )

                if not item_box:
                    continue

                if item_box["y"] < 350:
                    continue

                candidates.append(
                    (
                        item_box["y"],
                        item_box["x"],
                        item
                    )
                )

        except Exception:
            continue

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item[0],
        reverse=True
    )

    return candidates[0][2]


def get_composer_text(composer):

    if composer is None:
        return ""

    try:

        value = composer.input_value()

        if value is not None:
            return clean(value)

    except Exception:
        pass

    try:

        return clean(
            composer.inner_text()
        )

    except Exception:
        pass

    try:

        return clean(
            composer.text_content()
        )

    except Exception:
        pass

    return ""


def composer_is_empty(page):

    composer = find_composer(
        page
    )

    if not composer:
        return False

    text = get_composer_text(
        composer
    )

    return not text.strip()


# ============================================================
# CRISP - SHORTCUT
# ============================================================

def _debug_dump(page, tag):
    """Save a screenshot and print Shortcut-related elements to help debugging."""
    try:
        shot = BASE_DIR / f"debug_{tag}.png"
        page.screenshot(path=str(shot))
        print(f"  Debug screenshot saved: {shot}", flush=True)
    except Exception as exc:
        print(f"  Debug screenshot failed: {exc!r}", flush=True)
    try:
        items = page.evaluate(
            """() => [...document.querySelectorAll('button,[role=tab],[role=button],span,div,a')]
              .filter(e => e.children.length <= 2 && /short/i.test((e.innerText||'').trim()) && (e.innerText||'').trim().length < 40)
              .slice(0, 15)
              .map(e => { const r = e.getBoundingClientRect();
                  return {tag: e.tagName, text: e.innerText.trim(), cls: (e.className||'').toString().slice(0,60),
                          x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}; })"""
        )
        print(f"  Elements containing 'short': {items}", flush=True)
    except Exception as exc:
        print(f"  Element dump failed: {exc!r}", flush=True)


def open_shortcut_picker(page):
    """Open Crisp's Shortcuts tab (never Ctrl+K, never Escape).

    The old working version clicked the lowest visible 'Shortcuts' text.
    This version does the same, but first waits for the Create dialog to close
    and for the composer to appear, and polls for the tab instead of checking once.
    """

    # 1) Wait for the 'New conversation' dialog to close.
    try:
        page.locator('input[placeholder*="subject of the email" i]').first.wait_for(
            state="hidden", timeout=10000
        )
    except Exception:
        print("  Warning: new-conversation dialog still looks open.", flush=True)

    # 2) Wait for the conversation composer to be present.
    deadline = time.time() + 12
    while time.time() < deadline:
        try:
            if find_composer(page):
                break
        except Exception:
            pass
        time.sleep(0.4)

    # 3) Poll for a visible 'Shortcuts' element; click the lowest one on screen.
    deadline = time.time() + 12
    while time.time() < deadline:
        found = []
        locators = [
            page.get_by_text("Shortcuts", exact=True),
            page.get_by_text(re.compile(r"^\s*Shortcuts?\s*$", re.I)),
            page.get_by_role("button", name=re.compile(r"shortcuts?", re.I)),
            page.get_by_role("tab", name=re.compile(r"shortcuts?", re.I)),
        ]
        for loc in locators:
            try:
                for i in range(loc.count()):
                    item = loc.nth(i)
                    if not visible(item):
                        continue
                    item_box = box(item)
                    if item_box and item_box["width"] > 0 and item_box["height"] > 0:
                        found.append((item_box["y"], item, item_box))
            except Exception:
                continue

        if found:
            found.sort(key=lambda x: x[0], reverse=True)
            _, item, item_box = found[0]
            try:
                item.click(timeout=3000)
            except Exception:
                page.mouse.click(
                    item_box["x"] + item_box["width"] / 2,
                    item_box["y"] + item_box["height"] / 2,
                )
            page.wait_for_timeout(700)
            print("  Crisp Shortcuts tab opened.", flush=True)
            return
        time.sleep(0.5)

    _debug_dump(page, "shortcuts_tab_missing")
    raise RuntimeError(
        "Could not locate the visible 'Shortcuts' tab in Crisp "
        "(see debug_shortcuts_tab_missing.png in the project folder)."
    )


def shortcut_content_present(page):

    composer = find_composer(
        page
    )

    if composer:

        text = get_composer_text(
            composer
        )

        if (
            len(text) > 20
            and SHORTCUT not in text
        ):
            return True

        if (
            "Your Early Bird Price"
            in text
        ):
            return True

        if (
            "Here's the part I wanted to share"
            in text
        ):
            return True

        if "Best regards" in text:
            return True

    try:

        body = page.locator(
            "body"
        ).inner_text()

        markers = [
            "Your Early Bird Price",
            "Here's the part I wanted to share",
            "Best regards",
        ]

        if any(
            marker in body
            for marker in markers
        ):
            return True

    except Exception:
        pass

    return False


def insert_shortcut(page):

    print(
        f"  Opening shortcut picker: {SHORTCUT}"
    )

    open_shortcut_picker(
        page
    )

    composer = find_composer(
        page
    )

    if not composer:

        raise RuntimeError(
            "Could not find Crisp message composer."
        )

    composer.click()

    print(
        "  Typing shortcut..."
    )

    page.keyboard.type(
        SHORTCUT,
        delay=35
    )

    time.sleep(
        SHORTCUT_WAIT
    )

    print(
        "  Selecting shortcut..."
    )

    # --------------------------------------------------------
    # This is the original working selection method.
    # --------------------------------------------------------

    page.keyboard.press(
        "Enter"
    )

    time.sleep(1)

    if shortcut_content_present(
        page
    ):

        print(
            "  Shortcut inserted successfully."
        )

        return

    # --------------------------------------------------------
    # Fallback
    # --------------------------------------------------------

    page.keyboard.press(
        "ArrowDown"
    )

    time.sleep(0.2)

    page.keyboard.press(
        "Enter"
    )

    deadline = (
        time.time()
        + 8
    )

    while time.time() < deadline:

        if shortcut_content_present(
            page
        ):

            print(
                "  Shortcut inserted successfully."
            )

            return

        time.sleep(0.25)

    # --------------------------------------------------------
    # Mouse fallback
    # --------------------------------------------------------

    try:

        exact = page.get_by_text(
            SHORTCUT,
            exact=True
        )

        for i in range(
            exact.count()
        ):

            item = exact.nth(i)

            if not visible(item):
                continue

            item_box = box(
                item
            )

            if not item_box:
                continue

            x = (
                item_box["x"]
                + item_box["width"] / 2
            )

            y = (
                item_box["y"]
                + item_box["height"] / 2
            )

            page.mouse.click(
                x,
                y
            )

            time.sleep(1)

            if shortcut_content_present(
                page
            ):

                print(
                    "  Shortcut inserted successfully."
                )

                return

    except Exception:
        pass

    raise RuntimeError(
        "Shortcut could not be inserted."
    )


# ============================================================
# CRISP - SEND
# ============================================================

def find_send_button(page):

    semantic_selectors = [

        'button[aria-label*="send" i]',

        'button[title*="send" i]',

        'button[data-testid*="send" i]',

        '[role="button"][aria-label*="send" i]',

        '[role="button"][title*="send" i]',
    ]

    for selector in semantic_selectors:

        try:

            locator = page.locator(
                selector
            )

            for i in range(
                locator.count()
            ):

                item = locator.nth(i)

                if (
                    visible(item)
                    and enabled(item)
                ):
                    return item

        except Exception:
            continue

    # --------------------------------------------------------
    # Find composer
    # --------------------------------------------------------

    composer = find_composer(
        page
    )

    if not composer:
        return None

    composer_box = box(
        composer
    )

    if not composer_box:
        return None

    cx = composer_box["x"]
    cy = composer_box["y"]
    cw = composer_box["width"]
    ch = composer_box["height"]

    right = cx + cw
    bottom = cy + ch

    candidates = []

    try:

        buttons = page.locator(
            "button"
        )

        for i in range(
            buttons.count()
        ):

            button = buttons.nth(i)

            if not visible(button):
                continue

            if not enabled(button):
                continue

            button_box = box(
                button
            )

            if not button_box:
                continue

            bx = button_box["x"]
            by = button_box["y"]
            bw = button_box["width"]
            bh = button_box["height"]

            if (
                bw < 20
                or bh < 20
            ):
                continue

            horizontal_ok = (
                bx >= right - 180
                and bx <= right + 20
            )

            vertical_ok = (
                by >= bottom - 100
                and by <= bottom + 20
            )

            if (
                horizontal_ok
                and vertical_ok
            ):

                candidates.append(
                    (
                        bx,
                        by,
                        bw,
                        bh,
                        button
                    )
                )

    except Exception:
        pass

    if candidates:

        candidates.sort(
            key=lambda item: (
                item[0] + item[2],
                item[1] + item[3]
            ),
            reverse=True
        )

        return candidates[0][4]

    return None


def send_message(page):

    if DRY_RUN:

        print(
            "  [DRY RUN] Send skipped."
        )

        return True

    print(
        "  Finding Send button..."
    )

    button = find_send_button(
        page
    )

    if not button:

        raise RuntimeError(
            "Could not find Crisp's blue Send button."
        )

    button_box = box(
        button
    )

    if button_box:

        print(
            "  Send button found at "
            f"x={button_box['x']:.0f}, "
            f"y={button_box['y']:.0f}, "
            f"w={button_box['width']:.0f}, "
            f"h={button_box['height']:.0f}"
        )

    else:

        print(
            "  Send button found."
        )

    if not enabled(button):

        raise RuntimeError(
            "Crisp Send button is disabled."
        )

    before = get_composer_text(
        find_composer(page)
    )

    if len(before.strip()) < 5:

        raise RuntimeError(
            "Composer appears empty. "
            "Refusing to send."
        )

    print(
        "  Clicking Send..."
    )

    button.click(
        force=True
    )

    # --------------------------------------------------------
    # Verify send
    # --------------------------------------------------------
    # The sent text must no longer be present in ANY visible composer box.
    # (The old check only looked at the lowest box, which can be a different
    # element after Crisp re-renders the conversation.)

    key = re.sub(r"\s+", " ", before).strip()[:30]

    deadline = time.time() + SEND_WAIT + 6

    while time.time() < deadline:

        texts = all_composer_texts(page)

        still_has_text = any(
            key and key in re.sub(r"\s+", " ", t)
            for t in texts
        )

        if not still_has_text:
            print("  Email sent successfully.", flush=True)
            return True

        time.sleep(0.4)

    # Not confirmed: collect evidence, but do NOT retry automatically
    # (a retry could send a duplicate email).
    try:
        shot = BASE_DIR / "debug_send_unconfirmed.png"
        page.screenshot(path=str(shot))
        print(f"  Debug screenshot saved: {shot}", flush=True)
    except Exception:
        pass
    try:
        for i, t in enumerate(all_composer_texts(page)):
            print(f"  Composer box {i} text now: {t[:120]!r}", flush=True)
        btn_now = find_send_button(page)
        print(
            "  Send button still found: "
            f"{bool(btn_now)} | enabled: {enabled(btn_now) if btn_now else None}",
            flush=True,
        )
    except Exception:
        pass

    raise RuntimeError(
        "Send button was clicked, but Crisp did not confirm the send. "
        "The email MAY already have been sent - check the conversation in "
        "Crisp before running again, to avoid duplicates."
    )


def all_composer_texts(page):
    """Text of every visible composer-like box in the lower part of the page."""
    texts = []
    for selector in (
        'textarea[placeholder*="message" i]',
        '[contenteditable="true"]',
        '[role="textbox"]',
    ):
        try:
            loc = page.locator(selector)
            for i in range(loc.count()):
                item = loc.nth(i)
                if not visible(item):
                    continue
                bb = box(item)
                if not bb or bb["y"] < 350:
                    continue
                texts.append(get_composer_text(item))
        except Exception:
            continue
    return texts


# ============================================================
# CRISP - ADD SEGMENT
# ============================================================

def add_segment(page):

    print(
        f"  Adding segment: {SEGMENT_NAME}"
    )

    # Find the actual Crisp segment input.
    segment_field = page.locator(
        'input[placeholder="Segment conversation…"]'
    ).last

    try:
        segment_field.wait_for(
            state="visible",
            timeout=10000
        )

    except Exception:

        segment_field = page.locator(
            'input[placeholder*="Segment conversation"]'
        ).last

        segment_field.wait_for(
            state="visible",
            timeout=10000
        )

    # Focus the field.
    segment_field.scroll_into_view_if_needed()

    segment_field.click(
        force=True
    )

    segment_field.focus()

    time.sleep(0.3)

    # Clear anything already inside.
    try:

        segment_field.fill("")

    except Exception:

        page.keyboard.press(
            "Control+A"
        )

        page.keyboard.press(
            "Backspace"
        )

    # Type exactly like the working debug version.
    print(
        f"  Typing segment: {SEGMENT_NAME}"
    )

    for character in SEGMENT_NAME:

        page.keyboard.insert_text(
            character
        )

        time.sleep(0.08)

    # Crisp automatically adds the segment after Enter.
    time.sleep(0.5)

    page.keyboard.press(
        "Enter"
    )

    time.sleep(1.5)

    # Verify that the segment was actually attached.
    labels = page.locator(
        ".c-profile-card-segments__segment "
        ".c-base-tag__label"
    )

    for i in range(labels.count()):

        item = labels.nth(i)

        try:

            if not visible(item):
                continue

            text = (
                item.inner_text()
                or ""
            ).strip()

            if (
                text.lower()
                == SEGMENT_NAME.lower()
            ):

                print(
                    f"  Segment '{SEGMENT_NAME}' "
                    "added successfully."
                )

                return True

        except Exception:
            continue

    raise RuntimeError(
        f"Segment '{SEGMENT_NAME}' was not added."
    )
# ============================================================
# CRISP - RESOLVE
# ============================================================

def resolve_conversation(page):

    if DRY_RUN:

        print(
            "  [DRY RUN] Resolve skipped."
        )

        return True

    print(
        "  Resolving conversation..."
    )

    try:

        # Crisp shortcut:
        # Ctrl + Alt + R = Resolve conversation

        print(
            "  Pressing Ctrl+Alt+R..."
        )

        page.keyboard.press(
            "Control+Alt+r"
        )

        time.sleep(1.5)

        print(
            "  Conversation resolved."
        )

        return True

    except Exception as e:

        print(
            f"  Warning: Could not resolve conversation: {e}"
        )

        return False

# ============================================================
# ONE RECIPIENT
# ============================================================

def process_recipient(
    page,
    row
):

    name = clean(
        row.get("Full Name")
    )

    email = clean(
        row.get("Contact Email")
    )

    print(
        f"  Recipient: {email} | {name}"
    )

    # --------------------------------------------------------
    # 1. New conversation
    # --------------------------------------------------------

    open_new_conversation(
        page
    )

    # --------------------------------------------------------
    # 2. Fill details
    # --------------------------------------------------------

    fill_new_conversation(
        page,
        email,
        name
    )

    # --------------------------------------------------------
    # 3. Existing shortcut
    # --------------------------------------------------------

    insert_shortcut(
        page
    )

    # --------------------------------------------------------
    # 4. Send
    # --------------------------------------------------------

    sent = send_message(
        page
    )

    if not sent:
        return False

    # --------------------------------------------------------
    # 5. CSV is updated AFTER successful send.
    #
    # Segment/Resolve happen after this.
    # This prevents duplicate emails if either post-send
    # action has a temporary UI problem.
    # --------------------------------------------------------

    return True


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print("Crisp Email Agent")
    print("=" * 60)

    print(
        f"CSV: {CSV_FILE}"
    )

    print(
        f"Shortcut: {SHORTCUT}"
    )

    print(
        f"Subject: {SUBJECT}"
    )

    print(
        f"Reference filter: {REFERENCE_FILTER}"
    )

    print(
        f"Segment: {SEGMENT_NAME}"
    )

    print(
        f"Dry run: {DRY_RUN}"
    )

    print()

    rows, fieldnames = load_csv()

    eligible = get_eligible(
        rows
    )

    print(
        f"Eligible recipients: "
        f"{len(eligible)}"
    )

    print()

    if not eligible:

        print(
            "No eligible recipients."
        )

        return

    print("Launching Chromium. Complete your Crisp login when the browser opens.")

    with sync_playwright() as p:

        context = (
            p.chromium.launch_persistent_context(
                user_data_dir=str(
                    PROFILE_DIR
                ),
                headless=False,
                viewport={
                    "width": 1600,
                    "height": 900
                },
                args=[
                    "--start-maximized"
                ]
            )
        )

        if context.pages:

            page = context.pages[0]

        else:

            page = context.new_page()

        try:

            page.goto(
                CRISP_URL,
                wait_until="domcontentloaded",
                timeout=60000
            )

        except Exception:
            pass

        wait_for_crisp(
            page
        )

        print()
        print("Browser is open. Waiting for automatic Inbox detection.")
        print("Complete Crisp login in Chromium if requested.")
        # wait_for_crisp() above returns only after the authenticated Inbox
        # is detected, so no manual Enter prompt is needed here.

        completed = 0
        failed = 0

        for position, (
            row_index,
            row
        ) in enumerate(
            eligible,
            start=1
        ):

            email = clean(
                row.get("Contact Email")
            )

            name = clean(
                row.get("Full Name")
            )

            if stop_requested():
                print()
                print("Stop requested. Stopping before the next recipient.")
                break

            print()
            print("=" * 60)

            print(
                f"[{position}/{len(eligible)}] "
                f"{email} | {name}"
            )

            print("=" * 60)

            try:

                # ------------------------------------------------
                # SEND
                # ------------------------------------------------

                sent = process_recipient(
                    page,
                    row
                )

                if not sent:

                    failed += 1

                    print(
                        "  Email was not sent."
                    )

                    print(
                        "  Agent stopped safely."
                    )

                    break

                # ------------------------------------------------
                # MAIL SENT CSV UPDATE
                # ------------------------------------------------

                if not DRY_RUN:

                    rows[row_index][
                        "Mail Sent"
                    ] = "TRUE"

                    save_csv(
                        rows,
                        fieldnames
                    )

                    print(
                        "  Mail Sent marked TRUE."
                    )

                # ------------------------------------------------
                # SEGMENT (skip all post-send actions in dry-run)
                # ------------------------------------------------

                if DRY_RUN:
                    print("  [DRY RUN] Segment and resolve skipped.")
                else:
                    try:

                        add_segment(
                            page
                        )

                    except Exception as e:

                        print(
                            f"  WARNING: Segment failed: {e}"
                        )

                        print(
                            "  Email was already sent "
                            "and CSV was updated."
                        )

                    # ------------------------------------------------
                    # RESOLVE
                    # ------------------------------------------------

                    try:

                        resolve_conversation(
                            page
                        )

                    except Exception as e:

                        print(
                            f"  WARNING: Resolve failed: {e}"
                        )

                        print(
                            "  Email was already sent "
                            "and CSV was updated."
                        )

                completed += 1

                print(
                    "  SUCCESS."
                )

            except KeyboardInterrupt:

                print()
                print(
                    "Stopped by user."
                )

                break

            except Exception as e:

                failed += 1

                print(
                    f"  FAILED: "
                    f"{type(e).__name__}: {e}"
                )

                print(
                    "  No Mail Sent mark was added."
                )

                print(
                    "  Agent stopped rather than "
                    "risk sending to the wrong person."
                )

                break

            if position < len(
                eligible
            ):

                time.sleep(
                    BETWEEN_EMAILS
                )

        print()
        print("=" * 60)

        print(
            f"Completed: {completed}"
        )

        print(
            f"Failed:    {failed}"
        )

        print(
            f"CSV:       {CSV_FILE}"
        )

        print("=" * 60)

        print()
        print(
            "Browser will remain open for review."
        )

        if os.environ.get("CRISP_GUI_MODE") != "1":
            try:
                input("Press ENTER to close the browser...")
            except KeyboardInterrupt:
                pass

        try:

            context.close()

        except Exception:
            pass


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "\nStopped by user."
        )

    except Exception as exc:

        print(
            f"\nERROR: "
            f"{type(exc).__name__}: {exc}"
        )

        # In the GUI nobody can press Enter, so waiting here would hang the
        # process forever. Exit with a failure code instead.
        if os.environ.get("CRISP_GUI_MODE") == "1":
            raise SystemExit(1)

        try:

            input(
                "Press ENTER to exit..."
            )

        except (KeyboardInterrupt, EOFError):
            pass
