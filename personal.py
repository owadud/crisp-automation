"""Personalized API messages: placeholders filled from each recipient's own Sheet / CSV row.

  {Order Value} {Country} ...      any column of the file (case and spaces do not matter)
  {Country|your country}           text after | is used when the cell is empty
  {first_name} {name}              the recipient's name
  {spent} {orders}                 the Order Value / Order Count columns
  {plugins} {plugin_count}         plugins found in the tags ("PostX Users", "WowStore AppSumo" ...)
  {missing_plugins} {missing_count}  bundle plugins they do not have yet
  {tags}                           every tag in the row
  {bundle: PostX, WowStore, ...}   the bundle's plugins (prints nothing)
  {if spent >= 599}...{else}...{endif}   also <= > < = != contains, "not contains", or just {if plugins}

A column with an empty header (a merged header cell such as "User Details") belongs to
the header on its left, so all its tags are read together.
"""

from __future__ import annotations

import html as _html
import re

import recipient_rules as rules

SMART = ("first_name", "name", "spent", "orders", "plugins", "plugin_count",
         "missing_plugins", "missing_count", "tags")
SPENT_KEYS = ("ordervalue", "spent", "totalspent", "purchasedamount", "purchaseamount", "amount",
              "totalvalue", "lifetimevalue", "revenue")
ORDER_KEYS = ("ordercount", "orders", "totalorders", "numberoforders")
EMAIL_KEYS = ("contactemail", "email", "emailaddress")
_PLUGIN_TAG = re.compile(r"^\s*(.+?)\s+(users|active|appsumo)\s*$", re.I)
_NOT_PLUGIN = ("all", "wpxpo", "verified", "email")
_BUNDLE = re.compile(r"\{\s*bundle\s*:([^{}]*)\}[ \t]*\n?", re.I)
_IF = re.compile(r"\{\s*if\s+([^{}]+?)\s*\}(.*?)(?:\{\s*else\s*\}(.*?))?\{\s*endif\s*\}", re.I | re.S)
_FIELD = re.compile(r"\{([^{}]+)\}")
_COND = re.compile(r"^(.+?)\s+(not contains|contains|>=|<=|!=|=|>|<)\s+(.+)$", re.I)
# Columns that "Fill plugins from WPXPO" adds (wpxpo_account.COLUMNS): a message using one on a list
# that was not filled yet gets a hint saying how to add them.
WPXPO_COLUMNS = ("Pro Plugins", "Remaining Plugins", "Expired Plugins", "Plan",
                 "Near Site Limit", "Account Total")
SAMPLE = {"fields": {"Full Name": "Ann Lee", "Order Value": "$820.50", "Order Count": "5",
                     "Country": "Canada", "User Details": "PostX Users, WowStore Users, All WooCommerce Users"},
          "cells": ["PostX Users", "WowStore Users", "All WooCommerce Users"]}


def key(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def number(text):
    """'$1,088.16' -> 1088.16; '-' or '' -> None."""
    t = re.sub(r"[^0-9.\-]", "", str(text or "").replace(",", ""))
    try:
        return float(t) if t not in ("", "-", ".") else None
    except ValueError:
        return None


def nice_list(items) -> str:
    items = list(items)
    return items[0] if len(items) == 1 else (", ".join(items[:-1]) + " and " + items[-1] if items else "")


# ------------------------------------------------------------------ reading the file
def build_index(grid) -> tuple[dict, list[str]]:
    """grid = header row + data rows (lists of cells) -> ({email: info}, headers).
    info = {"fields": {header: text}, "cells": [every non-empty cell]}. First row of an email wins."""
    if not grid:
        return {}, []
    header = [str(h or "").strip() for h in grid[0]]
    owner, last = [], ""
    for h in header:
        last = h or last
        owner.append(last)
    keyed = [key(h) for h in header]
    email_col = next((keyed.index(k) for k in EMAIL_KEYS if k in keyed), None)
    index = {}
    if email_col is None:
        return index, [h for h in dict.fromkeys(owner) if h]
    for raw in grid[1:]:
        raw = list(raw)
        email = rules.normalize_email(raw[email_col] if email_col < len(raw) else "")
        if not email or email in index:
            continue
        fields, cells = {}, []
        for i, value in enumerate(raw):
            value = str(value or "").strip()
            if not value or i >= len(owner) or not owner[i]:
                continue
            cells.append(value)
            fields.setdefault(owner[i], []).append(value)
        index[email] = {"fields": {h: ", ".join(v) for h, v in fields.items()}, "cells": cells}
    return index, [h for h in dict.fromkeys(owner) if h]


def plugins_of(info) -> list[str]:
    """Plugins from a 'Plugins' column ("WowStore, WowInvoice Pro") and from tags ("PostX Users")."""
    found = {}
    for header, value in (info or {}).get("fields", {}).items():
        if key(header) in ("plugins", "plugin", "pluginsused", "products", "proplugins"):
            for part in re.split(r"[,;/|]+", value):
                name = re.sub(r"\s*\((free|pro)\)\s*$|\s+(free|pro)\s*$", "", part.strip(), flags=re.I).strip()
                if name:
                    found.setdefault(key(name), name)
    for cell in (info or {}).get("cells", []):
        m = _PLUGIN_TAG.match(cell)
        if m and key(m.group(1).split()[0]) not in _NOT_PLUGIN:
            found.setdefault(key(m.group(1)), m.group(1).strip())
    return list(found.values())


def bundle_of(text) -> list[str]:
    m = _BUNDLE.search(str(text or ""))
    return [p.strip() for p in m.group(1).split(",") if p.strip()] if m else []


# ------------------------------------------------------------------ values
def _column(info, names) -> str:
    fields = (info or {}).get("fields", {})
    for k in names:
        for h, v in fields.items():
            if key(h) == k:
                return "" if v.strip() in ("-", "—") else v
    return ""


def values(name, info, bundle) -> dict:
    """Smart field values for one recipient."""
    first = (name or "").split()[0] if (name or "").split() else ""
    owned = plugins_of(info)
    owned_keys = {key(p) for p in owned}
    missing = [b for b in bundle if key(b) not in owned_keys]
    return {"firstname": first, "name": name or "", "spent": _column(info, SPENT_KEYS),
            "orders": _column(info, ORDER_KEYS), "plugins": nice_list(owned), "plugincount": str(len(owned)),
            "missingplugins": nice_list(missing), "missingcount": str(len(missing)) if bundle else "",
            "tags": ", ".join((info or {}).get("cells", []))}


def lookup(field, info, smart) -> str:
    k = key(field)
    if k in smart:
        return smart[k]
    for h, v in (info or {}).get("fields", {}).items():
        if key(h) == k:
            return v
    return ""


def _test(cond, info, smart) -> bool:
    m = _COND.match(cond.strip())
    if not m:
        v = lookup(cond, info, smart).strip()
        return bool(v) and v not in ("0", "-")
    left, op, right = m.group(1), m.group(2).lower(), m.group(3).strip().strip("\"'")
    have = lookup(left, info, smart)
    if op in ("contains", "not contains"):
        hit = right.lower() in have.lower()
        return hit if op == "contains" else not hit
    a, b = number(have), number(right)
    if a is not None and b is not None:
        return {">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b, "=": a == b, "!=": a != b}[op]
    if op in ("=", "!="):
        same = have.strip().lower() == right.lower()
        return same if op == "=" else not same
    return False      # a number comparison with no number in the cell


# ------------------------------------------------------------------ render / check
_HTML_START = re.compile(r"\s*(<!--.*?-->\s*)*<(!doctype|html|head|body|table|div|p|center|span|style|meta|h[1-6]|img|a)\b",
                         re.I | re.S)
_CODE_BLOCK = re.compile(r"<(style|script)\b.*?</\1\s*>|\{\{.*?\}\}", re.I | re.S)
# Tags written {{ like.this }} (Crisp, Mailchimp ...) are not this app's fields. These are filled when
# the email is sent from your own account; any other one is reported before sending.
MAIL_TAGS = {"url.unsubscribe", "unsubscribe", "unsubscribe_url", "unsub"}
_DOUBLE = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")


def is_html(text) -> bool:
    """A message pasted as HTML code (My email only), rather than the editor's text."""
    return bool(_HTML_START.match(str(text or "")))


def _hide_code(text: str) -> tuple[str, list[str]]:
    """<style>/<script> blocks are full of { } that are CSS, not fields: set them aside, and
    {{ mail tags }} too (they are filled when the email is sent)."""
    kept = []

    def keep(m):
        kept.append(m.group(0))
        return f"\x00{len(kept) - 1}\x00"
    return _CODE_BLOCK.sub(keep, text), kept


def _show_code(text: str, kept: list[str]) -> str:
    return re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], text)


def render(text, name, info=None, bundle=None) -> tuple[str, list[str]]:
    """(finished message, fields that came out empty) for one recipient."""
    text = str(text or "")
    if is_html(text):
        body, kept = _hide_code(text)
        out, empty = _render(body, name, info, bundle, escape=True)
        return _show_code(out, kept), empty
    return _render(text, name, info, bundle)


def _render(text, name, info=None, bundle=None, escape=False) -> tuple[str, list[str]]:
    bundle = bundle if bundle is not None else bundle_of(text)
    text = _BUNDLE.sub("", text)
    smart = values(name, info, bundle)
    text = _IF.sub(lambda m: m.group(2) if _test(m.group(1), info, smart) else (m.group(3) or ""), text)
    empty = []

    def fill(m):
        field, _bar, fallback = m.group(1).partition("|")
        v = lookup(field, info, smart).strip()
        if not v and not _bar:
            empty.append(field.strip())
        out = v or fallback.strip()
        return _html.escape(out, quote=False) if escape else out    # a name like "A&B" stays valid HTML

    text = _FIELD.sub(fill, text)
    if escape:
        return text.strip(), empty
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, empty


def check(texts, headers) -> list[str]:
    """Problems in the message texts (unknown fields, unclosed {if}) before anything is sent."""
    known = {key(s) for s in SMART} | {key(h) for h in headers} | {"aiopener", "aiemail"}
    problems = []
    for n, text in enumerate(texts, start=1):
        where = f"Message {n}: " if len(texts) > 1 else ""
        for tag in _DOUBLE.findall(str(text or "")):
            if tag.strip().lower() not in MAIL_TAGS:
                problems.append(f"{where}{{{{ {tag} }}}} can't be filled by this app. For names use {{first_name}} "
                                "or {name}; for the unsubscribe link use {{ url.unsubscribe }}.")
        body = _BUNDLE.sub("", _hide_code(str(text or ""))[0])
        opens, closes = len(re.findall(r"\{\s*if\s", body, re.I)), len(re.findall(r"\{\s*endif\s*\}", body, re.I))
        if opens != closes:
            problems.append(f"{where}{opens} {{if ...}} but {closes} {{endif}}. Close every {{if}} with {{endif}}.")
        for m in _IF.finditer(body):
            if _IF.search(m.group(2) + (m.group(3) or "")) or re.search(r"\{\s*if\s", m.group(2), re.I):
                problems.append(f"{where}an {{if}} inside another {{if}} is not supported.")
            cm = _COND.match(m.group(1).strip())
            field = cm.group(1) if cm else m.group(1)
            if key(field) not in known:
                problems.append(f"{where}{{if {m.group(1)}}} uses '{field.strip()}', which is not a column.")
        plain = _IF.sub(lambda m: m.group(2) + (m.group(3) or ""), body)
        for m in _FIELD.finditer(plain):
            field = m.group(1).partition("|")[0].strip()
            if key(field) in ("else", "endif"):
                continue
            if key(field) not in known:
                problems.append(f"{where}{{{field}}} is not a column in the file or a known field.")
            if key(field) in ("missingplugins", "missingcount") and not bundle_of(text):
                problems.append(f"{where}{{{field}}} needs the bundle list: add {{bundle: PostX, WowStore, ...}}.")
    missing_wpxpo = [c for c in WPXPO_COLUMNS if key(c) not in known and any(
        re.search(r"\{[^{}]*" + re.escape(c), str(t or ""), re.I) for t in texts)]
    if missing_wpxpo:
        problems.append(f"{', '.join(missing_wpxpo)}: this list has no such column yet. Fill it first: "
                        "campaign step 1 Recipients > Plugin data > Fill plugins from WPXPO.")
    return list(dict.fromkeys(problems))
