"""Fill plugin columns from the WPXPO account (the same lookup as the License Checker extension).

For each email, account.wpxpo.com returns that customer's orders: product, date, price and
licenses (status, sites used / limit). This module turns them into plain columns for the
recipient list, so message fields ({plugins}, {if ...}) and AI-written emails know exactly
which plugins each person owns:

  Pro Plugins        WowRevenue, WowStore, WholesaleX, WowAddons   (paid and licence active)
  Remaining Plugins  PostX, WowShipping, ...   (bundle plugins they don't have and that are not expired:
                     never bought, refunded, pending or abandoned)
  Plugin Count       4
  Plan               Lifetime | Yearly | Mixed
  Expired Plugins    (paid, but every licence expired: renewal offers)
  Near Site Limit    WholesaleX (20/20)
  Active Licenses    4
  Expired Licenses   0
  First Purchase     2024-08-28
  Last Purchase      2025-04-17
  Account Total      $580   (paid orders only: refunded / pending / abandoned are left out)
  Account Orders     4
  Plugin Details     WowStore · Lifetime · active · 3/Unlimited sites; ...
  WPXPO Checked      2026-11-24 (or "no orders" / an error)

Each order's status is the word in brackets in its product name: "(complete)", "(refunded)" ...
The columns sit together at the right of the list, in this order (see organize()).

The API key is saved only on this computer (~/.crisp_email_agent/wpxpo.json), never in the app.
"""

from __future__ import annotations

import csv
import html
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

API_URL = "https://account.wpxpo.com/wp-json/license/v1/validate"
CONFIG = Path.home() / ".crisp_email_agent" / "wpxpo.json"
PAUSE = 1.2                   # seconds between lookups: gentle on the account server
COLUMNS = ["Pro Plugins", "Remaining Plugins", "Plugin Count", "Plan", "Expired Plugins", "Near Site Limit",
           "Active Licenses", "Expired Licenses", "First Purchase", "Last Purchase", "Account Total",
           "Account Orders", "Plugin Details", "WPXPO Checked"]
CHECKED = "WPXPO Checked"
REMAINING = "Remaining Plugins"

# The 10 plugins of the WPXPO bundle (wpxpo.com/bundle): what "Remaining Plugins" is measured against.
BUNDLE = ["PostX", "WowStore", "WowAddons", "WowRevenue", "WholesaleX", "WowShipping", "WowOptin",
          "WowInvoice", "WowRecommend", "WowSubscription"]
ALIASES = {"productx": "WowStore", "wowsubscriptions": "WowSubscription", "wowrecommendation": "WowRecommend",
           "wowshippings": "WowShipping"}
PAID = {"complete", "completed", "processing", "partially-refunded", "partially refunded"}
# Not-paid order statuses -> the group shown in Remaining Plugins (first listed = shown first)
UNPAID = {"refunded": "Refunded", "pending": "Pending", "on-hold": "Pending", "on hold": "Pending",
          "pending payment": "Pending", "abandoned": "Abandoned", "checkout-draft": "Abandoned",
          "draft": "Abandoned", "cancelled": "Cancelled", "canceled": "Cancelled", "failed": "Failed"}
# Header notes in the Google Sheet (hover a header to read them)
NOTES = {
    "Pro Plugins": "Filled by Crisp Automation from the WPXPO account: paid orders with an active licence.",
    REMAINING: "Bundle plugins this person does not have and that are not expired (never bought, refunded, "
               "pending or abandoned). Expired ones are in Expired Plugins. Bundle = " + ", ".join(BUNDLE) + ".",
    "Account Total": "Paid orders only (refunded, pending and abandoned orders are left out).",
    CHECKED: "When this row was looked up. Rows looked up in the last 30 days are skipped next time.",
}


class LookupError_(RuntimeError):
    """auth = the key was refused (stop everything); otherwise only this email failed."""

    def __init__(self, message: str, auth: bool = False):
        super().__init__(message)
        self.auth = auth


# ------------------------------------------------------------------ key (this computer only)
def config_path() -> Path:
    return Path(os.environ.get("CRISP_WPXPO_CONFIG") or CONFIG)


def load_key() -> str:
    try:
        return str(json.loads(config_path().read_text(encoding="utf-8")).get("key", "")).strip()
    except (OSError, ValueError, AttributeError):
        return ""


def save_key(key: str) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"key": key.strip()}), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


def remove_key() -> None:
    try:
        config_path().unlink()
    except OSError:
        pass


# ------------------------------------------------------------------ the request
def _ssl_context():
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


def _post(body: dict, timeout: float = 30) -> dict:
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "CrispAutomation"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            return json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise LookupError_("The WPXPO account refused the API key (Settings > Accounts > WPXPO account).",
                               auth=True) from None
        raise LookupError_(f"WPXPO account error {exc.code}.") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise LookupError_(f"WPXPO account not reachable ({exc}).") from None


def lookup(email: str, key: str, post=_post) -> list[dict]:
    """Every order of this customer (empty list = no orders)."""
    data = post({"key": key, "email": str(email or "").strip()})
    if not data.get("success"):
        msg = re.sub(r"<[^>]+>", " ", str(data.get("message") or "")).strip()
        if re.search(r"invalid|unauthori[sz]ed|key", msg, re.I) and not re.search(r"no (order|purchase)", msg, re.I):
            raise LookupError_("The WPXPO account refused the API key (Settings > Accounts > WPXPO account).",
                               auth=True)
        return []                                   # e.g. "no orders found for this email"
    return parse_orders(str(data.get("message") or ""))


# ------------------------------------------------------------------ reading the answer
_LICENSE = re.compile(r"([a-f0-9]{32})\s*\((\w+)\)\s*\[([^\]]+)\]", re.I)


def _lines(page: str) -> list[str]:
    text = re.sub(r"<br\s*/?>|</(div|p|li)>", "\n", page, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def parse_orders(page: str) -> list[dict]:
    """The API's HTML -> [{id, product, plugin, plan, date, price, licenses: [{status, used, limit}], sites}]."""
    orders, cur, field = [], None, ""
    for line in _lines(page):
        m = re.match(r"^(ID|Email|Product Name|Date|License|Active Sites|Price|Discount)\s*:\s*(.*)$", line, re.I)
        if m:
            field, value = m.group(1).lower(), m.group(2).strip()
            if field == "id":
                cur = {"id": re.sub(r"\D", "", value), "product": "", "date": "", "price": 0.0,
                       "licenses": [], "sites": []}
                orders.append(cur)
                continue
            if cur is None:
                continue
            if field == "product name":
                cur["product"] = value
            elif field == "date":
                cur["date"] = _iso(value)
            elif field == "price":
                cur["price"] = _money(value)
            elif field == "license":
                _add_licenses(cur, value)
            elif field == "active sites" and value:
                cur["sites"].append(value)
            continue
        if cur is not None and field == "active sites":
            cur["sites"].append(line)
        elif cur is not None and field == "license":
            _add_licenses(cur, line)
    for o in orders:
        o["plugin"], o["plan"] = plugin_and_plan(o["product"])
        o["status"] = order_status(o["product"])
    return orders


def order_status(product: str) -> str:
    """'WowAddons Pro – Lifetime (complete) - ...' -> 'complete' ('' when the name has no status)."""
    for m in re.finditer(r"\(([A-Za-z][A-Za-z -]{2,24})\)", str(product or "")):
        word = m.group(1).strip().lower()
        if word in PAID or word in UNPAID:
            return word
    return ""


def is_paid(order: dict) -> bool:
    return order.get("status", "") in PAID or not order.get("status")


def _add_licenses(order: dict, text: str) -> None:
    for m in _LICENSE.finditer(text):
        used, _sep, limit = m.group(3).partition("/")
        order["licenses"].append({"status": m.group(2).lower(), "used": used.strip(), "limit": limit.strip()})


def _iso(text: str) -> str:
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%d %B %Y"):
        try:
            return datetime.strptime(text.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return text.strip()


def _money(text: str) -> float:
    try:
        return float(re.sub(r"[^0-9.]", "", str(text).replace(",", "")) or 0)
    except ValueError:
        return 0.0


def plugin_and_plan(product: str) -> tuple[str, str]:
    """'WowAddons Pro – Lifetime Unlimited Sites (complete) - ...' -> ('WowAddons', 'Lifetime')."""
    p = str(product or "")
    m = re.match(r"^\s*(.+?)\s+Pro\b", p, re.I) or re.match(r"^\s*(.+?)\s*[–—\-]\s", p)
    name = (m.group(1) if m else p).strip()
    name = ALIASES.get(re.sub(r"[^a-z]", "", name.lower()), name)
    name = next((b for b in BUNDLE if b.lower() == re.sub(r"\s", "", name.lower())), name)
    low = p.lower()
    plan = ("Lifetime" if "lifetime" in low else "Yearly" if re.search(r"year|annual", low)
            else "Monthly" if "month" in low else "")
    return name, plan


# ------------------------------------------------------------------ one row of columns
def _money_text(total: float) -> str:
    return f"${total:,.0f}" if total == int(total) else f"${total:,.2f}"


def remaining(owned: list[str], expired: list[str]) -> str:
    """Bundle plugins neither owned nor expired, plain: 'PostX, WowShipping'. Expired ones have their own column."""
    return ", ".join(n for n in BUNDLE if n not in owned and n not in expired)


def summarize(orders: list[dict], today: str | None = None) -> dict:
    """Orders -> the columns above (as text, ready for a Sheet or CSV).
    Owned = a paid order (complete / processing) with an active licence. A bundle order owns all 10."""
    today = today or datetime.now().strftime("%Y-%m-%d")
    if not orders:
        return dict({c: "" for c in COLUMNS}, **{"Plugin Count": "0", "Account Orders": "0", "Account Total": "$0",
                                                  REMAINING: remaining([], []), CHECKED: f"{today} · no orders"})
    paid = [o for o in orders if is_paid(o)]
    by_plugin: dict[str, dict] = {}
    for o in sorted(paid, key=lambda o: o["date"]):
        p = by_plugin.setdefault(o["plugin"], {"plans": [], "licenses": []})
        if o["plan"] and o["plan"] not in p["plans"]:
            p["plans"].append(o["plan"])
        p["licenses"] += o["licenses"]
    owned, covered, expired_plugins, details, near = [], [], [], [], []
    for name, p in by_plugin.items():
        lic = p["licenses"]
        active = not lic or any(x["status"] == "active" for x in lic)
        state = "active" if lic and active else (lic[-1]["status"] if lic else "no licence")
        if active:
            owned += BUNDLE if "bundle" in name.lower() else [name]
            covered.append(name)
        elif all(x["status"] == "expired" for x in lic):
            expired_plugins.append(name)
        best = next((x for x in lic if x["status"] == "active"), lic[-1] if lic else None)
        sites = f"{best['used']}/{best['limit']} sites" if best else ""
        details.append(" · ".join(x for x in (name, "/".join(p["plans"]), state, sites) if x))
        for x in lic:
            if x["status"] == "active" and x["limit"].isdigit() and x["used"].isdigit() \
                    and int(x["used"]) >= int(x["limit"]):
                near.append(f"{name} ({x['used']}/{x['limit']})")
                break
    owned = list(dict.fromkeys(owned))
    for o in orders:                                   # unpaid orders: listed in details with their status
        if not is_paid(o):
            details.append(f"{o['plugin']} · {o['status']} order")
    statuses = [lic["status"] for o in paid for lic in o["licenses"]]
    plans = {pl for name, p in by_plugin.items() for pl in p["plans"]}
    dates = [o["date"] for o in paid if o["date"]]
    return {
        "Pro Plugins": ", ".join(owned),
        REMAINING: remaining(owned + covered, expired_plugins),
        "Plugin Count": str(len(owned)),
        "Plan": (plans.pop() if len(plans) == 1 else "Mixed" if plans else ""),
        "Expired Plugins": ", ".join(expired_plugins),
        "Near Site Limit": ", ".join(near),
        "Active Licenses": str(statuses.count("active")),
        "Expired Licenses": str(statuses.count("expired")),
        "First Purchase": min(dates) if dates else "",
        "Last Purchase": max(dates) if dates else "",
        "Account Total": _money_text(sum(o["price"] for o in paid)),
        "Account Orders": str(len(paid)),
        "Plugin Details": "; ".join(details),
        CHECKED: today,
    }


def organize(header: list[str]) -> list[str]:
    """The header with the WPXPO columns moved together to the right, in COLUMNS order
    (everything else keeps its place). Missing WPXPO columns are added."""
    others = [h for h in header if h not in COLUMNS]
    return others + COLUMNS


def checked_recently(value: str, days: int = 30, today: datetime | None = None) -> bool:
    try:
        when = datetime.strptime(str(value or "")[:10], "%Y-%m-%d")
    except ValueError:
        return False
    return ((today or datetime.now()) - when).days < days


# ------------------------------------------------------------------ many people
def fill(emails, key: str, post=_post, sleep=time.sleep, progress=None, stop=None, on_batch=None,
         batch: int = 25) -> tuple[dict, list[str]]:
    """Look up each email (a pause between them) -> ({email: columns}, [problems]).
    on_batch(partial_results) is called every `batch` people, so a stop or crash keeps what was found.
    Stops at once when the key is refused."""
    out, problems, pending, worked = {}, [], {}, False
    emails = list(dict.fromkeys(str(e).strip().lower() for e in emails if str(e or "").strip()))
    for n, email in enumerate(emails, start=1):
        if stop is not None and stop():
            problems.append("Stopped.")
            break
        try:
            cols = summarize(lookup(email, key, post))
            worked = True
        except LookupError_ as exc:
            # 'Invalid license Key or Emails' also comes for one odd email: stop only when nothing worked yet
            if exc.auth and not worked:
                problems.append(str(exc))
                break
            cols = dict({c: "" for c in COLUMNS}, **{CHECKED: f"error: {str(exc)[:80]}"})
            problems.append(f"{email}: {exc}")
        out[email] = cols
        pending[email] = cols
        if progress is not None:
            progress(n, len(emails))
        if on_batch is not None and len(pending) >= batch:
            on_batch(dict(pending))
            pending.clear()
        if n < len(emails):
            sleep(PAUSE)
    if on_batch is not None and pending:
        on_batch(dict(pending))
    return out, problems


# ------------------------------------------------------------------ writing a CSV list
def write_csv(path, results: dict) -> int:
    """Add / update the columns in a CSV list, keeping every other column and cell as it is
    (also columns with empty or repeated headers). Returns the number of rows updated."""
    path = Path(path)
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        grid = list(csv.reader(f))
    if not grid:
        return 0
    header = grid[0]
    keys = [re.sub(r"[^a-z0-9]", "", h.lower()) for h in header]
    email_col = next((keys.index(k) for k in ("contactemail", "email", "emailaddress") if k in keys), None)
    if email_col is None:
        raise ValueError("No email column in the list.")
    # rebuild every row in the organised column order (cells move with their column)
    old = list(header)
    new = organize(old)
    if new != old:
        taken, order = set(), []
        for h in new:                       # each new column -> the old column it comes from (None = new)
            i = next((i for i, x in enumerate(old) if x == h and i not in taken), None)
            if i is not None:
                taken.add(i)
            order.append(i)
        grid = [[(r[i] if i is not None and i < len(r) else "") for i in order] for r in grid]
        header = grid[0] = list(new)
    index = {c: header.index(c) for c in COLUMNS}
    n = 0
    for row in grid[1:]:
        email = str(row[email_col] if email_col < len(row) else "").strip().lower()
        cols = results.get(email)
        if not cols:
            continue
        row += [""] * (len(header) - len(row))
        for c, v in cols.items():
            row[index[c]] = v
        n += 1
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(grid)
    tmp.replace(path)
    return n
