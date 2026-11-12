"""AI opening lines: Claude writes one short, personal opening sentence per recipient.
Your approved message stays exactly as written; only {ai_opener} is filled in.

Flow: generate first -> review/edit in Lists -> sending uses exactly the stored lines.
What Claude receives: your subject and message, and per person only a first name,
a company name guessed from the email domain, and that domain (never the full
email address; people are identified by a row number). The Anthropic API key is
stored only on this computer (~/.crisp_email_agent/anthropic.json).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

CONFIG = Path.home() / ".crisp_email_agent" / "anthropic.json"
MODEL = "claude-opus-5-5"
BATCH = 20
MAX_CHARS = 300
COST_PER_RECIPIENT = 0.002      # US$, rough: Claude Opus 5.5, short lines, batches of 20

FREE_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "hotmail.com", "outlook.com", "live.com", "msn.com",
    "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "gmx.com", "gmx.de", "web.de",
    "yandex.com", "yandex.ru", "mail.ru", "zoho.com", "qq.com", "163.com", "126.com", "hotmail.co.uk",
    "yahoo.co.uk", "outlook.de", "rediffmail.com", "inbox.com", "mail.com", "fastmail.com", "tutanota.com",
}

SYSTEM = """You write the first sentence of a short business email, one per recipient.

The email below is the sender's approved message; your sentence goes right before it (after the greeting).
Rules:
- One or two sentences, at most 220 characters, plain text, no line breaks.
- Warm, specific and natural; it must lead naturally into the message below.
- Use the company name only if one is given; never invent facts about the person or company
  (no claims about what they sell, their size, or news you cannot know).
- If the domain is a free email provider, write a friendly line without a company.
- No greeting (the email already starts with one), no sign-off, no links, no email addresses,
  no placeholders or braces, no mention of AI.
- Write in the language of the message.
Return one entry per recipient id."""

SCHEMA = {
    "type": "object",
    "properties": {
        "openers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "opener": {"type": "string"}},
                "required": ["id", "opener"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["openers"],
    "additionalProperties": False,
}


# ------------------------------------------------------------------ settings (local only)
def config_path() -> Path:
    return Path(os.environ.get("CRISP_AI_CONFIG") or CONFIG)


def load_key() -> str:
    try:
        return str(json.loads(config_path().read_text(encoding="utf-8")).get("api_key", "")).strip()
    except (OSError, ValueError):
        return ""


def save_key(key: str) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"api_key": key.strip()}), encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(path)


def available() -> bool:
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def client(key: str | None = None):
    import anthropic
    return anthropic.Anthropic(api_key=key or load_key(), max_retries=4)


# ------------------------------------------------------------------ recipients -> what Claude sees
def company_from_domain(domain: str) -> str:
    """'acme-store.co.uk' -> 'Acme Store'; free mail -> ''."""
    domain = str(domain or "").strip().lower()
    if not domain or domain in FREE_DOMAINS:
        return ""
    labels = [x for x in domain.split(".") if x]
    if len(labels) > 1:
        labels = labels[:-1]                                   # the top-level part (com, uk, de ...)
    if len(labels) > 1 and labels[-1] in ("co", "com", "org", "net", "ac", "gov"):
        labels = labels[:-1]                                   # co.uk, com.au ...
    base = labels[-1] if labels else ""
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", base) if w)


def recipient_brief(i: int, email: str, name: str) -> dict:
    domain = str(email or "").split("@")[-1].lower()
    first = (name or "").split()[0] if (name or "").split() else ""
    return {"id": i, "first_name": first, "company": company_from_domain(domain),
            "domain": domain, "free_email": domain in FREE_DOMAINS}


def check_line(text: str) -> str:
    """'' if the line can be used as written, else why it needs a look."""
    t = str(text or "").strip()
    if not t:
        return "empty"
    if len(t) > MAX_CHARS:
        return "too long"
    if "\n" in t or "{" in t or "}" in t:
        return "has line breaks or braces"
    if re.search(r"https?://|www\.|@", t):
        return "has a link or an address"
    return ""


# ------------------------------------------------------------------ generation
def generate(recipients, subject: str, message: str, make_client=client, progress=None, stop=None) -> dict:
    """recipients: [(email, name)]. Returns {email: {"opener", "check"}} for the ones written,
    plus "_errors": [text]. Never raises for one bad batch; stops on a refused API key."""
    out, errors = {}, []
    api = make_client()
    context = f"Subject: {subject}\n\nMessage:\n{message[:6000]}"
    for start in range(0, len(recipients), BATCH):
        if stop is not None and stop():
            errors.append("Stopped.")
            break
        batch = recipients[start:start + BATCH]
        briefs = [recipient_brief(start + j, e, n) for j, (e, n) in enumerate(batch)]
        try:
            resp = api.beta.messages.create(
                model=MODEL,
                max_tokens=8000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                system=[{"type": "text", "text": SYSTEM}, {"type": "text", "text": context,
                                                           "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": "Recipients:\n" + json.dumps(briefs, ensure_ascii=False)}],
            )
        except Exception as exc:  # noqa: BLE001
            kind = type(exc).__name__
            if kind in ("AuthenticationError", "PermissionDeniedError"):
                errors.append("Anthropic refused the API key (Settings > AI). Nothing more was generated.")
                break
            errors.append(f"Rows {start + 1}-{start + len(batch)}: {kind}: {exc}"[:300])
            continue
        if resp.stop_reason == "refusal":
            errors.append(f"Rows {start + 1}-{start + len(batch)}: Claude declined; write these lines yourself.")
            continue
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        try:
            items = json.loads(text).get("openers", [])
        except ValueError:
            errors.append(f"Rows {start + 1}-{start + len(batch)}: unreadable answer; try again.")
            continue
        by_id = {int(x.get("id", -1)): str(x.get("opener", "")).strip() for x in items if isinstance(x, dict)}
        for j, (email, _name) in enumerate(batch):
            line = by_id.get(start + j, "")
            if line:
                out[email.strip().lower()] = {"opener": line, "check": check_line(line)}
        if progress is not None:
            progress(min(start + len(batch), len(recipients)), len(recipients))
    out["_errors"] = errors
    return out


# ------------------------------------------------------------------ stored lines per campaign
class OpenerStore:
    """data/ai_openers/<campaign>.json: {email: {opener, state: generated|edited, check, at}}."""

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

    def get(self, email: str) -> str:
        return (self.items.get(str(email).strip().lower()) or {}).get("opener", "")

    def merge(self, generated: dict, keep_edited: bool = True) -> int:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        n = 0
        for email, item in generated.items():
            if email.startswith("_"):
                continue
            old = self.items.get(email)
            if keep_edited and old and old.get("state") == "edited":
                continue
            self.items[email] = {"opener": item["opener"], "state": "generated", "check": item.get("check", ""),
                                 "at": now}
            n += 1
        self.save()
        return n

    def edit(self, email: str, text: str) -> None:
        email = str(email).strip().lower()
        if text.strip():
            self.items[email] = {"opener": text.strip(), "state": "edited", "check": check_line(text), "at":
                                 datetime.now().strftime("%Y-%m-%d %H:%M")}
        else:
            self.items.pop(email, None)
        self.save()

    def counts(self, emails) -> tuple[int, int, int]:
        """(ready, need a look, missing) for these emails."""
        ready = look = missing = 0
        for e in emails:
            item = self.items.get(str(e).strip().lower())
            if not item:
                missing += 1
            elif item.get("check"):
                look += 1
            else:
                ready += 1
        return ready, look, missing


# Tests answer the edit box through this: hook(email, current) -> new text or None.
EDIT_HOOK = None


def edit_line(parent, email: str, current: str) -> str | None:
    if EDIT_HOOK is not None:
        return EDIT_HOOK(email, current)
    from PySide6.QtWidgets import QInputDialog
    text, ok = QInputDialog.getMultiLineText(parent, "AI opening line",
                                             f"Opening line for {email} (empty = send without one):", current)
    return text.strip() if ok else None
