#!/usr/bin/env python3
"""Read-only probe: how does Crisp show message status and times?

Opens Crisp with the app's saved login. You open a few example conversations
yourself; for each one, press Enter here and the probe records ONLY the page
structure around the messages: CSS class names, status words, the FORMAT of
times (e.g. "99:99" instead of the real time), tooltip/aria/title/datetime
attributes (sanitised) and the shape of the conversation address.

It never clicks, types, hovers, sends, resolves or changes anything. Message
text, names and email addresses are not saved.

Run (close the app's own run first, the browser profile can only be open once):
  Windows:  .venv\\Scripts\\python.exe tools\\crisp_status_probe.py
  Ubuntu:   .venv/bin/python tools/crisp_status_probe.py
Self-test without Crisp:  ... tools/crisp_status_probe.py --selftest
Output: dev/crisp_status_probe_<date>.json
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRISP_URL = "https://app.crisp.chat/"
PROFILE_DIR = Path(os.environ.get("CRISP_PROFILE_DIR",
                                  str(Path.home() / ".crisp_email_agent" / "browser_profile"))).expanduser()
OUT_DIR = ROOT / "dev"

# Runs inside the page. Read-only: querySelectorAll / getAttribute only.
EXTRACT_JS = r"""
() => {
  const KEY = /message|bubble|status|state|time|date|ago|read|deliver|seen|sent|fail|error|bounce|warn|tooltip|check|tick|receipt|fingerprint|from|operator|visitor|user|author|reply|thread|conversation-chat|composer/i;
  const WORDS = /^(sent|sending|delivered|delivery|read|seen|opened|open|not delivered|undelivered|failed|failure|bounced|bounce|error|pending|queued|unread|received|replied|reply|email|via email|sent via email|today|yesterday|now|just now|ago|at|on|am|pm|delivered to email|read in email|could not be delivered|not sent|retry|monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun|january|february|march|april|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|second|seconds|minute|minutes|hour|hours|day|days|[smhd]|[\s:.,\/\-·•()]+)+$/i;
  const EMAIL = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;
  const shape = s => (s || '').replace(EMAIL, '<email>').replace(/[A-Za-z]/g, 'a').replace(/[0-9]/g, '9').slice(0, 60);
  const safeText = s => {
    s = (s || '').replace(/\s+/g, ' ').trim();
    if (!s) return '';
    if (s.length <= 60 && WORDS.test(s.replace(/[0-9]/g, ' '))) return s.replace(/[0-9]/g, '9');
    return '[shape ' + shape(s) + ']';
  };
  const safeAttr = (name, v) => {
    if (v == null) return null;
    if (name === 'href' || name === 'src') return v.replace(/[0-9a-f]{8}-[0-9a-f-]{27,}/gi, '<uuid>').replace(/session_[A-Za-z0-9-]+/g, 'session_<id>').slice(0, 120);
    return safeText(v);
  };
  const cls = el => (typeof el.className === 'string' ? el.className : (el.getAttribute('class') || '')).trim();
  const attrs = el => {
    const out = {};
    for (const a of el.attributes) {
      if (a.name === 'class' || a.name === 'style') continue;
      if (/^(title|aria-|data-|datetime|role|alt|href)/.test(a.name)) out[a.name] = safeAttr(a.name, a.value);
    }
    return out;
  };
  const chain = el => { const c = []; let p = el.parentElement; for (let i = 0; p && i < 4; i++, p = p.parentElement) c.push(cls(p).split(/\s+/).slice(0, 3).join('.')); return c; };
  const own = el => [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ');
  const hits = [];
  for (const el of document.querySelectorAll('body *')) {
    const c = cls(el);
    const a = attrs(el);
    if (!KEY.test(c) && !Object.keys(a).some(k => /title|aria-label|datetime|data-/.test(k))) continue;
    if (/^(SCRIPT|STYLE|PATH|USE)$/.test(el.tagName)) continue;
    const r = el.getBoundingClientRect();
    hits.push({tag: el.tagName, cls: c.slice(0, 200), attrs: a, text: safeText(own(el)),
               visible: r.width > 0 && r.height > 0, parents: chain(el)});
    if (hits.length >= 700) break;
  }
  const counts = {};
  for (const el of document.querySelectorAll('body *')) for (const k of cls(el).split(/\s+/)) if (k.startsWith('c-')) counts[k] = (counts[k] || 0) + 1;
  const icons = {};
  for (const u of document.querySelectorAll('use')) { const h = (u.getAttribute('xlink:href') || u.getAttribute('href') || '').split('#')[1]; if (h) icons[h] = (icons[h] || 0) + 1; }
  // Full structure of the last 12 messages (every child: status line, icons, tooltips, times).
  const messages = [];
  const msgEls = [...document.querySelectorAll('[class*="c-conversation-box-content-message "], [class~="c-conversation-box-content-message"]')]
    .filter(el => /(^|\s)c-conversation-box-content-message(\s|$)/.test(cls(el))).slice(-12);
  for (const m of msgEls) {
    const nodes = [];
    const walk = (el, depth) => {
      if (nodes.length >= 160 || /^(SCRIPT|STYLE)$/.test(el.tagName)) return;
      const r = el.getBoundingClientRect();
      nodes.push({depth, tag: el.tagName, cls: cls(el).slice(0, 220), attrs: attrs(el), text: safeText(own(el)),
                  visible: r.width > 0 && r.height > 0,
                  icon: el.tagName.toLowerCase() === 'use' ? ((el.getAttribute('xlink:href') || el.getAttribute('href') || '').split('#')[1] || '') : ''});
      for (const ch of el.children) walk(ch, depth + 1);
    };
    walk(m, 0);
    messages.push({cls: cls(m), attrs: attrs(m), nodes});
  }
  const dates = [...document.querySelectorAll('[class*="c-conversation-box-content__date"]')].slice(-6)
    .map(el => ({cls: cls(el).slice(0, 120), text: safeText(el.textContent), attrs: attrs(el)}));
  return {
    messages, dates,
    url_shape: location.pathname.replace(/[0-9a-f]{8}-[0-9a-f-]{27,}/gi, '<uuid>').replace(/session_[A-Za-z0-9-]+/g, 'session_<id>'),
    has_session_in_url: /session_/.test(location.href),
    elements: hits, class_counts: counts, icons: icons,
  };
}
"""

SAMPLE_PAGE = """<!doctype html><html><body>
<div class="c-conversation-chat"><div class="c-message c-message--operator">
  <div class="c-message__content">Hello Jane, this is a secret offer for jane@example.com</div>
  <span class="c-message__time" title="Tuesday, November 4, 2026 10:02 AM">10:02</span>
  <span class="c-message__status c-message__status--read" aria-label="Read in email">Read</span></div>
<div class="c-message c-message--visitor"><div class="c-message__content">Thanks!</div>
  <time datetime="2026-11-04T11:20:00Z" class="c-message__time">11:20</time></div></div>
<svg><use xlink:href="/spritemap.svg#icon-check_double"></use></svg>
</body></html>"""


def save(captures: list, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"captured_at": datetime.now().isoformat(timespec="seconds"),
                                "captures": captures}, indent=1, ensure_ascii=False), encoding="utf-8")


def selftest() -> int:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(SAMPLE_PAGE)
        data = page.evaluate(EXTRACT_JS)
        browser.close()
    dump = json.dumps(data)
    assert "jane@example.com" not in dump and "secret offer" not in dump and "Thanks!" not in dump, "leak"
    texts = {e["text"] for e in data["elements"]}
    assert "Read" in texts and "99:99" in texts, texts
    titles = [e["attrs"].get("title") for e in data["elements"] if e["attrs"].get("title")]
    assert titles == ["Tuesday, November 9, 9999 99:99 AM"], titles
    assert data["icons"] == {"icon-check_double": 1}
    assert data["messages"] == []  # sample page uses different class names
    print("Self-test OK: status words and time formats kept; message text and emails removed.")
    return 0


def pick_page(ctx):
    """The tab showing a Crisp conversation (not a blank or other tab)."""
    pages = [p for p in ctx.pages if not p.is_closed()]
    crisp = [p for p in pages if "app.crisp.chat" in (p.url or "")]
    conv = [p for p in crisp if "session_" in (p.url or "")]
    return (conv or crisp or pages)[-1]


def wait_for_inbox(page, seconds=300) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        try:
            if "/inbox" in page.url.lower():
                body = page.locator("body").inner_text(timeout=1500).lower()
                if "inbox" in body and "filters" in body:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def auto_capture(ctx, out, captures, minutes=15, wanted=8) -> None:
    """No typing: every conversation left open ~5 s in Chromium is captured once."""
    print("AUTOMATIC MODE - you only click in the Chromium window:")
    print("  1. Click an example conversation in the Inbox (same tab) and wait ~5 seconds.")
    print("  2. This window prints 'Captured conversation N'. Then click the next one.")
    print("  3. Good examples: one only sent, one delivered/read, one replied, one failed (if any).")
    print(f"  4. When finished, just CLOSE the Chromium window (or it stops after {minutes} min / {wanted} captures).")
    seen, last_key, since, last_note = set(), None, time.time(), 0.0
    end = time.time() + minutes * 60
    while time.time() < end and len(captures) < wanted:
        try:
            pages = [pg for pg in ctx.pages if not pg.is_closed()]
            if not pages:
                break
            # Read the LIVE address from every tab (Crisp changes it without a reload).
            page, key = pages[-1], None
            for pg in pages:
                href = pg.evaluate("location.href")
                m = re.search(r"session_[A-Za-z0-9-]+", href or "")
                if m:
                    page, key = pg, m.group(0)
        except Exception as exc:
            if "closed" in str(exc).lower() or not ctx.pages:
                break
            print(f"(waiting: {str(exc)[:120]})")
            time.sleep(1)
            continue
        if time.time() - last_note >= 5:
            state = ("conversation open" + (" (already captured)" if key in seen else ", reading in a moment...")
                     if key else "no conversation open yet - click one in the Inbox")
            print(f"[{datetime.now():%H:%M:%S}] {state}")
            last_note = time.time()
        if key != last_key:
            last_key, since = key, time.time()
        elif key and key not in seen and time.time() - since >= 3:
            try:
                data = page.evaluate(EXTRACT_JS)
            except Exception as exc:
                if "closed" in str(exc).lower():
                    break
                print(f"Could not read this conversation yet: {str(exc)[:160]}")
                page.wait_for_timeout(1000)
                continue
            seen.add(key)
            data["label"] = f"conversation {len(captures) + 1}"
            captures.append(data)
            save(captures, out)
            found = len(data.get("messages") or [])
            print(f"Captured conversation {len(captures)} ({found} messages"
                  + ("" if found else ", layout not recognised - saved anyway") + "). "
                  "Click the next one, or close Chromium to finish.")
        try:
            page.wait_for_timeout(1000)     # keeps the browser connection updated
        except Exception:
            break


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    from playwright.sync_api import sync_playwright
    out = OUT_DIR / f"crisp_status_probe_{datetime.now():%Y-%m-%d_%H%M%S}.json"
    captures = []
    print("Crisp status probe (read-only: nothing is clicked, typed or sent).")
    print(f"Browser profile: {PROFILE_DIR}")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE_DIR), headless=False,
                                                   viewport={"width": 1600, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(CRISP_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        print("Sign in to Crisp in the Chromium window if it asks (only you type there).")
        print()
        if "--manual" not in sys.argv:
            auto_capture(ctx, out, captures)
            try:
                ctx.close()
            except Exception:
                pass
            if captures:
                print(f"Done. {len(captures)} capture(s) saved to: {out}")
                print("Tell Claude it is done; it can read that file on this computer.")
            else:
                print("Nothing captured.")
            return 0
        print("In the Chromium window, open an example conversation, e.g.:")
        print("  1) sent only   2) delivered or read   3) the customer replied   4) bounced/failed (if any)")
        print("Then come back here, type a short label (sent / read / replied / failed / ...) and press Enter.")
        print("Type q and press Enter to finish.")
        while True:
            try:
                label = input("\nLabel for the open conversation (q = finish): ").strip()
            except EOFError:
                break
            if label.lower() in ("q", "quit", "exit"):
                break
            page = pick_page(ctx)
            try:
                page.wait_for_timeout(800)
                data = page.evaluate(EXTRACT_JS)
            except Exception as exc:
                print(f"Could not read the page: {exc}")
                continue
            data["label"] = label or f"capture {len(captures) + 1}"
            if not data["has_session_in_url"] and "/inbox" not in data["url_shape"]:
                print("Note: this page does not look like an open conversation; saved anyway.")
            captures.append(data)
            save(captures, out)
            print(f"Saved capture {len(captures)}: {len(data['messages'])} messages read "
                  f"(conversation open: {'yes' if data['has_session_in_url'] else 'NO - open one and try again'}).")
        try:
            ctx.close()
        except Exception:
            pass
    if captures:
        print(f"\nDone. {len(captures)} capture(s) saved to:\n  {out}")
        print("Tell Claude it is done; it can read that file on this computer.")
    else:
        print("\nNothing captured.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
