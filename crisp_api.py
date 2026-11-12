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
        self.uncertain = False      # a write may have happened (never repeat it automatically)


def _reason(exc) -> str:
    """Crisp's own short reason from an error response, if any."""
    try:
        body = json.loads(exc.read().decode("utf-8") or "{}")
        return str(body.get("reason") or "")[:120]
    except Exception:  # noqa: BLE001
        return ""


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
        return self._request("GET", path, params=params)

    def _request(self, method: str, path: str, params: dict | None = None, body: dict | None = None):
        """GET is retried on rate limits, server and network errors. A WRITE is retried
        only when Crisp says it is rate limiting (420/429: nothing was done); after a
        server or network error a write may already have happened, so it raises with
        .uncertain = True and is never repeated automatically."""
        url = f"{API}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
        write = method != "GET"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = dict(self._headers, **({"Content-Type": "application/json"} if data is not None else {}))
        delay = 2.0
        for attempt in range(1, self.tries + 1):
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            self.requests += 1
            try:
                with self._open(req) as resp:
                    payload = json.loads(resp.read().decode("utf-8") or "{}")
                    return payload.get("data")
            except urllib.error.HTTPError as exc:
                code = exc.code
                reason = _reason(exc)
                if code in (401, 403):
                    raise CrispAPIError("Crisp refused the API token (check the identifier, key and website ID, "
                                        "and that the token was not revoked)." + (f" [{reason}]" if reason else ""),
                                        code) from exc
                if code == 404:
                    raise CrispAPIError("not found", 404) from exc
                if code in (420, 429):
                    if attempt == self.tries:
                        raise CrispAPIError("Crisp is limiting requests right now (daily quota or rate limit). "
                                            "Try again later.", code) from exc
                elif code >= 500:
                    if write:
                        err = CrispAPIError(f"Crisp server error {code} while writing: it may or may not have "
                                            "been done. Check Crisp before trying again.", code)
                        err.uncertain = True
                        raise err from exc
                    if attempt == self.tries:
                        raise CrispAPIError(f"Crisp server error {code}. Try again later.", code) from exc
                else:
                    raise CrispAPIError(f"Crisp API error {code}" + (f": {reason}" if reason else "") + ".",
                                        code) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if write:
                    err = CrispAPIError(f"Lost the connection to Crisp while writing ({exc}): it may or may not "
                                        "have been done. Check Crisp before trying again.")
                    err.uncertain = True
                    raise err from exc
                if attempt == self.tries:
                    raise CrispAPIError(f"Could not reach Crisp ({exc}).") from exc
            self.sleep(delay)
            delay *= 2
        raise CrispAPIError("Crisp did not answer.")

    # -------------------------------------------------------------- writes (sending)
    def create_conversation(self) -> str:
        data = self._request("POST", f"/website/{self.website_id}/conversation", body={}) or {}
        session_id = str(data.get("session_id") or "")
        if not session_id.startswith("session_"):
            raise CrispAPIError("Crisp did not return a conversation ID.")
        return session_id

    def set_meta(self, session_id: str, **meta) -> None:
        self._request("PATCH", f"/website/{self.website_id}/conversation/{session_id}/meta",
                      body={k: v for k, v in meta.items() if v not in (None, "", [])})

    def send_text(self, session_id: str, text: str, origin: str = "email") -> None:
        self._request("POST", f"/website/{self.website_id}/conversation/{session_id}/message",
                      body={"type": "text", "from": "operator", "origin": origin, "content": text})

    def set_state(self, session_id: str, state: str) -> None:
        self._request("PATCH", f"/website/{self.website_id}/conversation/{session_id}/state", body={"state": state})

    def link(self, session_id: str) -> str:
        return f"https://app.crisp.chat/website/{self.website_id}/inbox/{session_id}/"

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


# ------------------------------------------------------------------ one test email (Settings)
def send_test(api: CrispAPI, to_email: str, subject: str, text: str, name: str = "", origin: str = "email",
              wait_s: int = 45, poll=None, step: int = 15) -> dict:
    """Create ONE conversation to `to_email`, set email / name / subject, post the
    text as an operator message, then read it back a few times to see what Crisp
    reports (delivered / read / ignored). Never repeats a write. Returns a report."""
    report = {"session_id": "", "link": "", "steps": [], "delivered": "", "read": "", "ignored": "",
              "state": "", "visible_message": False, "error": ""}
    try:
        sid = api.create_conversation()
        report.update(session_id=sid, link=api.link(sid))
        report["steps"].append("conversation created")
        api.set_meta(sid, email=to_email.strip(), nickname=name or to_email.split("@")[0], subject=subject)
        report["steps"].append("email, name and subject set")
        api.send_text(sid, text, origin=origin)
        report["steps"].append(f"message posted (origin {origin})")
    except CrispAPIError as exc:
        report["error"] = str(exc)
        return report
    waited = 0
    sleep = poll or time.sleep
    while True:
        try:
            msgs = api.messages(sid)
            conv = api.conversation(sid)
        except CrispAPIError as exc:
            report["error"] = f"Sent, but could not read it back: {exc}"
            return report
        mine = [m for m in msgs if isinstance(m, dict) and m.get("from") == "operator"
                and m.get("type") == "text"]
        if mine:
            m = mine[-1]
            report.update(visible_message=True, delivered=str(m.get("delivered") or ""),
                          read=str(m.get("read") or ""),
                          ignored=json.dumps(m.get("ignored")) if m.get("ignored") else "")
        report["state"] = str(conv.get("state") or "")
        if report["delivered"] or report["ignored"] or waited >= wait_s:
            return report
        sleep(step)
        waited += step
