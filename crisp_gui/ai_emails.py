"""AI-written emails: the AI writes the BODY of each person's email from their own row
(Plan, Plugins, spend, tags ...) and the Products tab (the only offers it may use).

Your greeting, signature and unsubscribe line stay as you wrote them; the body goes where
{ai_email} is in your message (or right after the greeting line).

Flow: Generate -> every email is checked (links, coupons, prices and discounts must be in
the Products tab; length; no {fields}) -> clean ones are ready, flagged ones wait in Lists
until you edit or approve them -> sending uses exactly the stored body. People without a
ready body are skipped, never sent half-written.

What the AI receives: your subject and message, the Products tab, and per person a first
name, the company guessed from the email domain and the columns of their row (never an
email address; people are identified by a number). Same provider and key as Settings > AI.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path

from . import ai_openers as ao

import personal as pz            # same row reading as the message fields (merged headers, tags)
import recipient_rules as rules

BATCH = 8
MIN_WORDS, MAX_WORDS = 25, 230
COST_PER_RECIPIENT = 0.02        # US$, rough: Claude Opus 5.5, ~200-word bodies, batches of 8
PLACEHOLDER = "{ai_email}"
# Columns never sent to the AI (sending state, contact data, notes for the team).
PRIVATE = {"contactemail", "email", "emailaddress", "mailsent", "sentat", "status", "error", "conversationlink",
           "reference", "email1", "email2", "phone", "address", "notes"}

SYSTEM = """You write the middle part of a short, personal sales email from a WordPress plugin company's
support team, one email per recipient. The sender's own greeting and signature are added around your text.

For each recipient pick ONE offer from the product catalog that fits them best:
- Free users (Plan says Free, or a plugin is marked Free): invite them to upgrade a plugin they already use to
  Pro, with that plugin's offer from the catalog.
- Pro / paying users: cross-sell ONE catalog plugin they do not have yet that fits what they use (for example
  a store plugin user gets a plugin that grows a store), or upsell a bundle if the catalog has one and they
  already use several plugins.
- Never offer a plugin they already have in Pro. Use "Best for" / "Pairs well with" in the catalog when given.

Rules:
- Use ONLY plugin names, prices, discounts, coupons and links that appear in the catalog, copied exactly.
  Never invent a price, discount, coupon, deadline, feature or link.
- Mention what they already use, by name, so it is clearly written for them. Never invent facts about the
  person or their business.
- 60 to 150 words, 2 or 3 short paragraphs, friendly and helpful, not pushy. Plain text; links as
  Markdown [text](url). One clear call to action with the catalog link.
- No greeting, no sign-off, no subject line, no placeholders or braces, no email addresses, no mention of AI.
- Write in the language of the sender's message.
Return one entry per recipient id, with the plugin you offered in "offer"."""

JSON_RULE = ('\nAnswer with JSON only, exactly: {"emails": [{"id": <recipient id>, "offer": "<plugin>", '
             '"body": "<email body>"}]}')

SCHEMA = {
    "type": "object",
    "properties": {
        "emails": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "offer": {"type": "string"}, "body": {"type": "string"}},
                "required": ["id", "offer", "body"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["emails"],
    "additionalProperties": False,
}


# ------------------------------------------------------------------ reading the list and the Products tab
def grid_from_csv(path) -> list[list[str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle))


def products_from_grid(grid) -> list[dict]:
    """Products tab -> [{header: value}] (rows without a plugin name are left out)."""
    if not grid:
        return []
    header = [str(h or "").strip() for h in grid[0]]
    out = []
    for raw in grid[1:]:
        item = {h: str(raw[i]).strip() for i, h in enumerate(header) if h and i < len(raw) and str(raw[i]).strip()}
        if item and str(next(iter(item.values()), "")).strip():
            out.append(item)
    return out


def allowed_tokens(products, template: str = "") -> tuple[set, set]:
    """(links, other tokens: coupons, prices, percents) the AI may use."""
    text = " ".join(" ".join(p.values()) for p in products) + " " + str(template or "")
    links = {_norm_link(u) for u in re.findall(r"https?://[^\s)\]>\"']+", text)}
    tokens = {t.upper().rstrip(".,") for t in re.findall(r"[A-Za-z0-9$€£%.,]+", text)}
    return links, tokens


def _norm_link(url: str) -> str:
    return url.strip().rstrip(".,;:!?").rstrip("/").lower()


def person_brief(i: int, email: str, name: str, info: dict | None) -> dict:
    """What the AI sees for one person: no email address."""
    brief = ao.recipient_brief(i, email, name)
    brief.pop("domain", None)
    fields = (info or {}).get("fields", {})
    data = {}
    for header, value in fields.items():
        k = pz.key(header)
        if k in PRIVATE or k in ("fullname", "name") or "@" in value:
            continue
        data[header] = value[:160]
    owned = pz.plugins_of(info)
    if owned:
        brief["plugins_in_use"] = owned
    brief["data"] = data
    return brief


# ------------------------------------------------------------------ checks
def check_body(text: str, links: set, tokens: set, offer: str = "", owned_pro=()) -> str:
    """'' if the body can be sent as written, else why it needs a look."""
    t = str(text or "").strip()
    if not t:
        return "empty"
    words = len(t.split())
    if words < MIN_WORDS:
        return "too short"
    if words > MAX_WORDS:
        return "too long"
    if "{" in t or "}" in t:
        return "has {placeholders}"
    if re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", t):
        return "has an email address"
    for url in re.findall(r"https?://[^\s)\]>\"']+", t):
        if _norm_link(url) not in links:
            return f"link not in Products: {url[:60]}"
    for amount in re.findall(r"[$€£]\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?%", t):
        if amount.replace(" ", "").upper() not in tokens:
            return f"price or discount not in Products: {amount}"
    for code in re.findall(r"\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{5,}\b", t):
        if code not in tokens:
            return f"coupon not in Products: {code}"
    if offer and pz.key(offer) in {pz.key(p) for p in owned_pro}:
        return f"offers {offer}, which they already have in Pro"
    return ""


def owned_pro(info: dict | None) -> list[str]:
    """Plugins this person has in Pro: a Plan column saying Pro covers what they use; 'X Pro' names one."""
    fields = (info or {}).get("fields", {})
    plan = next((v for h, v in fields.items() if pz.key(h) == "plan"), "")
    pro_col = next((v for h, v in fields.items() if pz.key(h) == "proplugins"), "")
    if pro_col:                       # filled from the WPXPO account: every one of them is a paid Pro plugin
        return [p.strip() for p in pro_col.split(",") if p.strip()]
    listed = " , ".join(v for h, v in fields.items() if pz.key(h) in ("plugins", "plugin", "pluginsused", "products"))
    used = pz.plugins_of(info)
    named = [m.group(1) for m in re.finditer(r"([A-Za-z][\w-]*)\s*\(?Pro\b", plan + " , " + listed)
             if m.group(1).lower() != "pro"]
    if named:
        return named
    return used if re.fullmatch(r"\s*pro\s*", plan, re.I) else []


# ------------------------------------------------------------------ generation
def _openai_batch(s: dict, briefs: list, context: str, http) -> tuple[list, str]:
    body = {"model": ao.current_model(s), "temperature": 0.7, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SYSTEM + JSON_RULE + "\n\n" + context},
                         {"role": "user", "content": "Recipients:\n" + json.dumps(briefs, ensure_ascii=False)}]}
    try:
        data = http("POST", ao.base_url(s) + "/chat/completions", s["api_key"], body)
    except ao.ApiError as exc:
        if exc.status != 400:
            raise
        body.pop("response_format")
        data = http("POST", ao.base_url(s) + "/chat/completions", s["api_key"], body)
    choice = (data.get("choices") or [{}])[0]
    if choice.get("finish_reason") == "content_filter":
        return [], "refused"
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", str((choice.get("message") or {}).get("content") or "").strip())
    return json.loads(text).get("emails", []), ""


def _claude_batch(api, briefs: list, context: str) -> tuple[list, str]:
    resp = api.beta.messages.create(
        model=ao.MODEL,
        max_tokens=12000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        system=[{"type": "text", "text": SYSTEM}, {"type": "text", "text": context,
                                                   "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": "Recipients:\n" + json.dumps(briefs, ensure_ascii=False)}],
    )
    if resp.stop_reason == "refusal":
        return [], "refused"
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    return json.loads(text).get("emails", []), ""


def generate(people, index: dict, products: list, subject: str, message: str, make_client=ao.client,
             progress=None, stop=None, settings: dict | None = None, http=ao._http) -> dict:
    """people: [(email, name)]; index: personal.build_index(...)[0]. Returns {email: {body, offer, check}}
    plus "_errors": [text]. Never raises for one bad batch; stops on a refused API key."""
    out, errors = {}, []
    s = settings or ao.load_settings()
    claude = s["provider"] == "anthropic"
    name = ao.provider_name(s)
    api = make_client() if claude else None
    links, tokens = allowed_tokens(products, message)
    context = (f"Product catalog (the only offers you may use):\n{json.dumps(products, ensure_ascii=False)[:20000]}"
               f"\n\nSubject: {subject}\n\nSender's message (your text goes where {PLACEHOLDER} is, or after the "
               f"greeting):\n{message[:6000]}")
    for start in range(0, len(people), BATCH):
        if stop is not None and stop():
            errors.append("Stopped.")
            break
        batch = people[start:start + BATCH]
        infos = [index.get(rules.normalize_email(e)) for e, _n in batch]
        briefs = [person_brief(start + j, e, n, infos[j]) for j, (e, n) in enumerate(batch)]
        where = f"Rows {start + 1}-{start + len(batch)}"
        try:
            items, why = (_claude_batch(api, briefs, context) if claude
                          else _openai_batch(s, briefs, context, http))
        except ao.ApiError as exc:
            if exc.status in (401, 403):
                errors.append(f"{name} refused the API key (Settings > AI). Nothing more was written.")
                break
            errors.append(f"{where}: {exc}"[:300])
            continue
        except (ValueError, AttributeError):
            errors.append(f"{where}: unreadable answer; try again.")
            continue
        except Exception as exc:  # noqa: BLE001  (Claude SDK errors)
            if type(exc).__name__ in ("AuthenticationError", "PermissionDeniedError"):
                errors.append(f"{name} refused the API key (Settings > AI). Nothing more was written.")
                break
            errors.append(f"{where}: {type(exc).__name__}: {exc}"[:300])
            continue
        if why:
            errors.append(f"{where}: {name} declined; write these emails yourself.")
            continue
        by_id = {}
        for x in items if isinstance(items, list) else []:
            if isinstance(x, dict):
                try:
                    by_id[int(x.get("id", -1))] = (str(x.get("body", "")).strip(), str(x.get("offer", "")).strip())
                except (TypeError, ValueError):
                    continue
        for j, (email, _n) in enumerate(batch):
            body, offer = by_id.get(start + j, ("", ""))
            if body:
                out[rules.normalize_email(email)] = {
                    "body": body, "offer": offer,
                    "check": check_body(body, links, tokens, offer, owned_pro(infos[j]))}
        if progress is not None:
            progress(min(start + len(batch), len(people)), len(people))
    out["_errors"] = errors
    return out


# ------------------------------------------------------------------ stored emails per campaign
class EmailStore:
    """data/ai_emails/<campaign>.json: {email: {body, offer, check, state: generated|approved, at}}.
    'approved' = you edited or approved it in Lists: it is sent even if a check flagged it."""

    def __init__(self, folder: Path, campaign: str):
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", campaign or "campaign").strip("_")[:60] or "campaign"
        self.path = Path(folder) / f"{safe}.json"
        try:
            self.items: dict = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.items = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.items, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def item(self, email: str) -> dict:
        return self.items.get(rules.normalize_email(email)) or {}

    def get(self, email: str) -> str:
        return self.item(email).get("body", "")

    def ready(self, email: str) -> bool:
        return ready(self.item(email))

    def merge(self, generated: dict) -> int:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        n = 0
        for email, item in generated.items():
            if email.startswith("_"):
                continue
            if (self.items.get(email) or {}).get("state") == "approved":
                continue                                   # never overwrite what you approved
            self.items[email] = dict(item, state="generated", at=now)
            n += 1
        self.save()
        return n

    def approve(self, email: str, text: str) -> None:
        email = rules.normalize_email(email)
        if text.strip():
            old = self.items.get(email) or {}
            self.items[email] = {"body": text.strip(), "offer": old.get("offer", ""), "check": "",
                                 "state": "approved", "at": datetime.now().strftime("%Y-%m-%d %H:%M")}
        else:
            self.items.pop(email, None)
        self.save()

    def counts(self, emails) -> tuple[int, int, int]:
        """(ready, need a look, missing) for these emails."""
        ready_n = look = missing = 0
        for e in emails:
            item = self.item(e)
            if not item.get("body"):
                missing += 1
            elif ready(item):
                ready_n += 1
            else:
                look += 1
        return ready_n, look, missing


def ready(item: dict) -> bool:
    return bool((item or {}).get("body")) and (not item.get("check") or item.get("state") == "approved")


# Tests answer the edit box through this: hook(email, current, why) -> new text or None.
EDIT_HOOK = None


def edit_email(parent, email: str, current: str, why: str = "") -> str | None:
    if EDIT_HOOK is not None:
        return EDIT_HOOK(email, current, why)
    from PySide6.QtWidgets import QInputDialog
    note = f"Needs a look: {why}.\n" if why else ""
    text, ok = QInputDialog.getMultiLineText(
        parent, "AI email", f"{note}Email body for {email}. OK approves it for sending (empty = don't send):",
        current)
    return text.strip() if ok else None
