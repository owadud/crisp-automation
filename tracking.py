"""Open and click tracking for emails sent from your own account (My email), for this
computer only. It works like Snov.io's tracker:

  * every email gets a unique id, an invisible 1x1 image and links that go through your
    tracker web address, so a mail app that loads the image (= the email was opened) or a
    click is written down with the time;
  * the tracker is a small Google Apps Script under YOUR Google account (free): it writes
    each open / click into a private Google Sheet and hands the list to this app.

Limits (the same for every pixel tracker): no "time spent reading"; Apple Mail can load
images by itself (an early 'open'); Gmail caches images (repeat opens may count once);
people who block images show no open, but their clicks still count.
"""

from __future__ import annotations

import csv
import json
import re
import secrets
import urllib.parse
import urllib.request
from pathlib import Path

LEDGER_COLUMNS = ["Time", "Id", "Email", "Name", "Campaign", "Subject"]

APPS_SCRIPT = r"""// Crisp Automation: open and click tracker. Paste ALL of this into Extensions > Apps Script
// of a NEW Google Sheet that only you use, then Deploy > New deployment > Web app:
//   Execute as: Me    Who has access: Anyone
// Copy the web app address (it ends with /exec) into the app: Settings > Your email account.
const KEY = '__KEY__';   // lets only your app read the list

function doGet(e) {
  const p = (e && e.parameter) || {};
  const sheet = SpreadsheetApp.getActiveSpreadsheet();
  let tab = sheet.getSheetByName('opens');
  if (!tab) { tab = sheet.insertSheet('opens'); tab.appendRow(['Time', 'Event', 'Id', 'Link']); }
  if (p.ping) {
    return ContentService.createTextOutput(p.key === KEY ? 'ok' : 'wrong key');
  }
  if (p.list) {
    if (p.key !== KEY) return ContentService.createTextOutput('[]');
    const rows = tab.getDataRange().getValues().slice(1)
      .filter(r => !p.since || String(r[0]) > p.since)
      .map(r => [r[0] instanceof Date ? r[0].toISOString() : String(r[0]), r[1], r[2], r[3]]);
    return ContentService.createTextOutput(JSON.stringify(rows)).setMimeType(ContentService.MimeType.JSON);
  }
  const id = String(p.i || '').slice(0, 40);
  const link = String(p.u || '');
  if (id) tab.appendRow([new Date().toISOString(), link ? 'click' : 'open', id, link.slice(0, 500)]);
  if (link && /^https?:\/\//i.test(link)) {
    const safe = link.replace(/"/g, '%22').replace(/</g, '%3C');
    return HtmlService.createHtmlOutput(
      '<meta http-equiv="refresh" content="0;url=' + safe + '">' +
      '<script>window.top.location.href = "' + safe + '";</script>' +
      '<p style="font-family:Arial">Opening <a href="' + safe + '" target="_top">' + safe + '</a></p>');
  }
  return ContentService.createTextOutput('');
}
"""


def new_key() -> str:
    return secrets.token_urlsafe(18)


def script_for(key: str) -> str:
    return APPS_SCRIPT.replace("__KEY__", key)


def new_id() -> str:
    return secrets.token_urlsafe(9)


def configured(cfg: dict) -> bool:
    return bool(cfg.get("track") and valid_url(cfg.get("track_url", "")) and cfg.get("track_key"))


def valid_url(url: str) -> bool:
    return bool(re.match(r"^https://script\.google\.com/macros/s/[\w-]+/exec$", str(url or "").strip()))


# ------------------------------------------------------------------ the email
def _with(url: str, **params) -> str:
    return url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)


def add_tracking(page: str, base_url: str, track_id: str, links: bool = False) -> str:
    """The HTML of one email with the invisible image, and (if links) tracked links. Plain-text
    parts are left alone, and mailto: links (unsubscribe) are never rewritten."""
    def link(m):
        quote, href = m.group(1), m.group(2)
        if not re.match(r"^https?://", href, re.I) or href.startswith(base_url):
            return m.group(0)
        return f"href={quote}{_with(base_url, i=track_id, u=href.replace('&amp;', '&'))}{quote}"

    if links:
        page = re.sub(r"""href=(["'])(.*?)\1""", link, page, flags=re.I | re.S)
    pixel = (f'<img src="{_with(base_url, i=track_id)}" width="1" height="1" alt="" '
             'style="display:block;width:1px;height:1px;border:0;opacity:0" />')
    m = re.search(r"</body\s*>", page, re.I)
    return page[:m.start()] + pixel + page[m.start():] if m else page + pixel


# ------------------------------------------------------------------ what was sent (this computer)
def ledger_path(data_dir) -> Path:
    return Path(data_dir) / "tracking_sent.csv"


def record_sent(data_dir, time: str, track_id: str, email: str, name: str, campaign: str, subject: str) -> None:
    path = ledger_path(data_dir)
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(LEDGER_COLUMNS)
        w.writerow([time, track_id, email, name, campaign, subject])


def read_ledger(data_dir) -> dict[str, dict]:
    path = ledger_path(data_dir)
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {r["Id"]: r for r in csv.DictReader(f) if r.get("Id")}


# ------------------------------------------------------------------ reading the tracker
def _get(url: str, timeout: float = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "CrispAutomation"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def ping(cfg: dict, get=_get) -> str:
    """'' when the tracker answers with the right key, else what is wrong."""
    url = str(cfg.get("track_url", "")).strip()
    if not valid_url(url):
        return "Paste the web app address from Apps Script (it starts with https://script.google.com/macros/s/ " \
               "and ends with /exec)."
    try:
        said = get(_with(url, ping=1, key=cfg.get("track_key", ""))).strip()
    except Exception as exc:  # noqa: BLE001
        return f"The tracker did not answer ({exc}). Check that it is deployed with access: Anyone."
    if said == "ok":
        return ""
    if said == "wrong key":
        return "The tracker answers, but with a different key: copy the setup script again, paste it over the " \
               "old one in Apps Script, and deploy a new version."
    return "That address did not answer like the tracker. Deploy it as a Web app with access: Anyone."


def fetch_events(cfg: dict, get=_get) -> list[dict]:
    """Every open / click the tracker wrote down: [{time, event, id, link}]."""
    url = str(cfg.get("track_url", "")).strip()
    rows = json.loads(get(_with(url, list=1, key=cfg.get("track_key", ""))) or "[]")
    return [{"time": str(r[0]), "event": str(r[1]), "id": str(r[2]), "link": str(r[3] if len(r) > 3 else "")}
            for r in rows if isinstance(r, list) and len(r) >= 3]


def local_time(iso: str) -> str:
    """'2026-11-17T08:15:02.123Z' -> '2026-11-17 14:15' in this computer's time zone."""
    from datetime import datetime
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return t.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso[:16].replace("T", " ")


def summarize(events: list[dict], ledger: dict[str, dict]) -> dict[tuple[str, str], dict]:
    """(campaign, email) -> {opens, first_open, last_open, clicks, last_click, links}."""
    out: dict[tuple[str, str], dict] = {}
    for ev in sorted(events, key=lambda e: e["time"]):
        sent = ledger.get(ev["id"])
        if not sent:
            continue
        k = (sent["Campaign"], sent["Email"].strip().lower())
        s = out.setdefault(k, {"opens": 0, "first_open": "", "last_open": "", "clicks": 0, "last_click": "",
                               "links": []})
        when = local_time(ev["time"])
        if ev["event"] == "click":
            s["clicks"] += 1
            s["last_click"] = when
            if ev["link"] and ev["link"] not in s["links"]:
                s["links"].append(ev["link"])
            if not s["first_open"]:                 # a click means it was opened (images may be off)
                s["first_open"] = when
            s["last_open"] = s["last_open"] or when
        else:
            s["opens"] += 1
            s["first_open"] = s["first_open"] or when
            s["last_open"] = when
    return out


def summary_text(s: dict) -> str:
    parts = []
    if s["opens"]:
        parts.append(f"opened {s['opens']}×" + (f", last {s['last_open'][11:]}" if s["opens"] > 1 else ""))
    if s["clicks"]:
        parts.append(f"{s['clicks']} click{'s' if s['clicks'] != 1 else ''}, last {s['last_click'][11:]}")
    return " · ".join(parts)
