"""Crisp REST API (v1) for Campaign Results: read-only status tracking.

Uses a Website Token (Crisp > Settings > Workspace Settings > Advanced
configuration > API Token). The token is sent only to api.crisp.chat and is
stored only on this computer (~/.crisp_email_agent/crisp_api.json), never in
the project folder. This module only READS: it never sends, edits or resolves.

Per conversation: GET the conversation (state) and GET its messages. Our sent
message is the first operator message at/after the send time; its "delivered"
/ "read" channels give Delivered / Read, an "ignored" delivery gives Failed,
and any later message from the user is a reply. No third-party imports.
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://api.crisp.chat/v1"
DHAKA = timezone(timedelta(hours=6))
CONFIG = Path.home() / ".crisp_email_agent" / "crisp_api.json"
USER_AGENT = "CrispAutomationDesktop"

SENT, FAILED, DELIVERED, READ, REPLIED = "Sent", "Failed", "Delivered", "Read", "Replied"
_LINK = re.compile(r"/website/([0-9a-f-]{20,})/inbox/(session_[0-9a-f-]{20,})", re.I)


class CrispAPIError(RuntimeError):
    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.code = code


# ------------------------------------------------------------------ settings (local only)
def load_config(path: Path = CONFIG) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return {k: str(data.get(k, "")).strip() for k in ("website_id", "identifier", "key")}
    except (OSError, ValueError):
        return {"website_id": "", "identifier": "", "key": ""}


def save_config(cfg: dict, path: Path = CONFIG) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({k: str(cfg.get(k, "")).strip() for k in ("website_id", "identifier", "key")}),
                   encoding="utf-8")
    try:
        tmp.chmod(0o600)          # only this user can read it (Linux / macOS)
    except OSError:
        pass
    tmp.replace(path)


def configured(cfg: dict) -> bool:
    return all(cfg.get(k) for k in ("website_id", "identifier", "key"))


def parse_link(link: str) -> tuple[str, str]:
    """Conversation link -> (website_id, session_id), or ('', '')."""
    m = _LINK.search(str(link or ""))
    return (m.group(1), m.group(2)) if m else ("", "")


def website_from_links(links) -> str:
    """The website ID most of the saved conversation links point at."""
    counts: dict = {}
    for link in links:
        wid = parse_link(link)[0]
        if wid:
            counts[wid] = counts.get(wid, 0) + 1
    return max(counts, key=counts.get) if counts else ""


# ------------------------------------------------------------------ client
class CrispAPI:
    def __init__(self, website_id: str, identifier: str, key: str, opener=None, sleep=time.sleep,
                 tries: int = 4):
        self.website_id = website_id.strip()
        token = base64.b64encode(f"{identifier.strip()}:{key.strip()}".encode()).decode()
        self._headers = {"Authorization": f"Basic {token}", "X-Crisp-Tier": "website",
                         "User-Agent": USER_AGENT, "Accept": "application/json"}
        self._open = opener or (lambda req: urllib.request.urlopen(req, timeout=30))
        self.sleep, self.tries = sleep, tries
        self.requests = 0

    def _get(self, path: str, params: dict | None = None):
        url = f"{API}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
        delay = 2.0
        for attempt in range(1, self.tries + 1):
            req = urllib.request.Request(url, headers=self._headers, method="GET")
            self.requests += 1
            try:
                with self._open(req) as resp:
                    body = json.loads(resp.read().decode("utf-8") or "{}")
                    return body.get("data")
            except urllib.error.HTTPError as exc:
                code = exc.code
                if code in (401, 403):
                    raise CrispAPIError("Crisp refused the API token (check the identifier, key and website ID, "
                                        "and that the token was not revoked).", code) from exc
                if code == 404:
                    raise CrispAPIError("not found", 404) from exc
                if code in (420, 429) or code >= 500:
                    if attempt == self.tries:
                        raise CrispAPIError("Crisp is limiting requests right now (daily quota or rate limit). "
                                            "Try again later.", code) from exc
                else:
                    raise CrispAPIError(f"Crisp API error {code}.", code) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == self.tries:
                    raise CrispAPIError(f"Could not reach Crisp ({exc}).") from exc
            self.sleep(delay)
            delay *= 2
        raise CrispAPIError("Crisp did not answer.")

    # -------------------------------------------------------------- read-only calls
    def website(self) -> dict:
        return self._get(f"/website/{self.website_id}") or {}

    def conversation(self, session_id: str) -> dict:
        return self._get(f"/website/{self.website_id}/conversation/{session_id}") or {}

    def messages(self, session_id: str, after_ms: int | None = None) -> list:
        params = {"timestamp_after": after_ms} if after_ms else None
        return self._get(f"/website/{self.website_id}/conversation/{session_id}/messages", params) or []


# ------------------------------------------------------------------ status of one conversation
def _ms_to_dhaka(ms) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, DHAKA).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        return None


def _stamp_to_ms(stamp: str) -> int | None:
    try:
        dt = datetime.strptime(str(stamp or "")[:16], "%Y-%m-%d %H:%M").replace(tzinfo=DHAKA)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


def analyze(conversation: dict, messages: list, sent_at: str = "") -> dict:
    """Same result shape as campaign_results.analyze: status, reply_at, reply_kind,
    reason, open, problem. Times are Dhaka time, exact (from Crisp)."""
    sent_ms = _stamp_to_ms(sent_at)
    msgs = sorted((m for m in messages or [] if isinstance(m, dict)), key=lambda m: m.get("timestamp") or 0)
    ours = [m for m in msgs if m.get("from") == "operator" and m.get("type") not in ("note", "event")]
    if sent_ms is not None:
        ours = [m for m in ours if (m.get("timestamp") or 0) >= sent_ms - 5 * 60_000] or ours
    if not ours:
        return {"problem": "no_our_message"}
    target = ours[0]
    status, reason = SENT, ""
    ignored = target.get("ignored")
    if ignored:
        status = FAILED
        info = ignored if isinstance(ignored, dict) else {}
        reason = "Not delivered" + (f": {info.get('reason')}" if info.get("reason") else "")
    elif target.get("read"):
        status = READ
    elif target.get("delivered"):
        status = DELIVERED
    reply_at = ""
    for m in msgs:
        if m.get("from") == "user" and m.get("type") not in ("note", "event") and \
                (m.get("timestamp") or 0) > (target.get("timestamp") or 0):
            dt = _ms_to_dhaka(m.get("timestamp"))
            reply_at = dt.strftime("%Y-%m-%d %H:%M") if dt else ""
            status = REPLIED
            break
    state = str((conversation or {}).get("state") or "")
    open_ = None if not state else state != "resolved"
    return {"status": status, "reply_at": reply_at, "reply_kind": "exact" if reply_at else "",
            "reason": reason, "open": open_, "problem": ""}


def check(api: CrispAPI, link: str, sent_at: str = "") -> dict:
    """Read one conversation. Raises CrispAPIError only for problems that should stop
    the whole check (token refused, quota); a missing conversation is a result."""
    _wid, session_id = parse_link(link)
    if not session_id:
        return {"problem": "no_link"}
    try:
        conv = api.conversation(session_id)
        after = _stamp_to_ms(sent_at)
        msgs = api.messages(session_id, after - 10 * 60_000 if after else None)
    except CrispAPIError as exc:
        if exc.code == 404:
            return {"problem": "not_found"}
        raise
    return analyze(conv, msgs, sent_at)
