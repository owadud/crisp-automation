"""Admin reporting: every teammate's app sends a short report after each real run to a private
Google Sheet that the owner (admin) controls, so the admin can see all usage in one place.

Like the read tracker, the collector is a free Google Apps Script under the admin's own Google
account. Each run reports who and which computer, the app version, the campaign and mode, the
sent/failed/skipped counts, and the full list of recipients emailed in that run. Only the admin
(who has the admin key) can read it back; teammates' apps can only append with the write key.

Nothing here is hidden: a teammate who opens the app's code or Settings can see that run reports
are sent and where.
"""

from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG = Path.home() / ".crisp_email_agent" / "admin.json"
MAX_ROWS_PER_POST = 400          # keep each request small for Apps Script

APPS_SCRIPT = r"""// Crisp Automation: admin usage collector. Paste ALL of this into Extensions > Apps Script
// of a NEW Google Sheet that only you (the admin) use, then Deploy > New deployment > Web app:
//   Execute as: Me    Who has access: Anyone
// Copy the web app address (ends with /exec) into the app: Settings > Admin reporting.
const ADMIN_KEY = '__ADMIN__';   // lets only your app READ the usage
const WRITE_KEY = '__WRITE__';   // lets your teammates' apps APPEND their run reports

function sheet_(ss, name, header) {
  let t = ss.getSheetByName(name);
  if (!t) { t = ss.insertSheet(name); t.appendRow(header); }
  return t;
}
function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function doPost(e) {
  let body = {};
  try { body = JSON.parse((e && e.postData && e.postData.contents) || '{}'); } catch (err) { return json_({ok:false}); }
  if (body.write !== WRITE_KEY) return json_({ok:false, error:'bad write key'});
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const stamp = new Date().toISOString();
  if (body.run) {
    const r = body.run;
    sheet_(ss, 'runs', ['Reported','Runner','Host','Version','Campaign','Mode','Sent','Failed','Skipped',
      'Eligible','Started','Finished','Error']).appendRow([stamp, r.runner, r.host, r.version, r.campaign,
      r.mode, r.sent, r.failed, r.skipped, r.eligible, r.started, r.finished, r.error]);
  }
  if (body.rows && body.rows.length) {
    const s = sheet_(ss, 'sends', ['Reported','Runner','Host','Campaign','Row','Email','Name','Result','Error','Time','Link']);
    const data = body.rows.map(function (r) {
      return [stamp, body.runner, body.host, body.campaign, r.row, r.email, r.name, r.result, r.error, r.time, r.link];
    });
    s.getRange(s.getLastRow() + 1, 1, data.length, data[0].length).setValues(data);
  }
  return json_({ok:true});
}

function doGet(e) {
  const p = (e && e.parameter) || {};
  if (p.ping) return ContentService.createTextOutput(p.key === ADMIN_KEY ? 'ok' : 'bad key');
  if (p.key !== ADMIN_KEY) return json_({columns:[], rows:[]});
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const tab = ss.getSheetByName(p.what === 'sends' ? 'sends' : 'runs');
  if (!tab) return json_({columns:[], rows:[]});
  const vals = tab.getDataRange().getValues();
  const head = vals.shift() || [];
  const rows = vals.filter(function (r) { return !p.since || String(r[0]) > p.since; })
    .map(function (r) { return r.map(function (c) { return c instanceof Date ? c.toISOString() : String(c); }); });
  return json_({columns: head, rows: rows});
}
"""


def new_key() -> str:
    return secrets.token_urlsafe(18)


def script_for(admin_key: str, write_key: str) -> str:
    return APPS_SCRIPT.replace("__ADMIN__", admin_key).replace("__WRITE__", write_key)


def valid_url(url: str) -> bool:
    import re
    return bool(re.match(r"^https://script\.google\.com/macros/s/[\w-]+/exec$", str(url or "").strip()))


def load_config(path: Path | None = None) -> dict:
    try:
        data = json.loads(Path(path or CONFIG).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict, path: Path | None = None) -> Path:
    import os
    path = Path(path or CONFIG)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)
    return path


def reporting(cfg: dict) -> bool:
    """This computer should send run reports to the admin."""
    return bool(cfg.get("report") and valid_url(cfg.get("report_url", "")) and cfg.get("write_key"))


def is_admin(cfg: dict) -> bool:
    """This computer can READ all usage (it holds the admin key)."""
    return bool(valid_url(cfg.get("report_url", "")) and cfg.get("admin_key"))


def teammate_config(cfg: dict) -> dict:
    """What to hand a teammate: report on, the URL and the write key — never the admin read key."""
    return {"report": True, "report_url": cfg.get("report_url", ""), "write_key": cfg.get("write_key", "")}


# ------------------------------------------------------------------ sending a report (teammates)
class _KeepPost(urllib.request.HTTPRedirectHandler):
    """Apps Script answers a POST with a 302 to googleusercontent.com; resend it as a POST so the
    body is not lost (the default handler would turn it into a GET)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return urllib.request.Request(newurl, data=req.data, headers=dict(req.header_items()),
                                      method=req.get_method())


def _post(url: str, payload: dict, timeout: float = 30) -> dict:
    opener = urllib.request.build_opener(_KeepPost())
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json",
                                                          "User-Agent": "CrispAutomation"})
    with opener.open(req, timeout=timeout) as resp:
        try:
            return json.loads(resp.read().decode("utf-8", "replace") or "{}")
        except ValueError:
            return {}


def build_run(runner: str, host: str, version: str, campaign: str, mode: str, sent: int, failed: int,
              skipped: int, eligible: int, started: str, finished: str, error: str) -> dict:
    return {"runner": runner, "host": host, "version": version, "campaign": campaign, "mode": mode,
            "sent": sent, "failed": failed, "skipped": skipped, "eligible": eligible,
            "started": started, "finished": finished, "error": error[:300]}


def send_report(cfg: dict, run: dict, rows: list[dict]) -> None:
    """Report one run. Never raises (reporting must not affect sending)."""
    url, write = cfg["report_url"].strip(), cfg.get("write_key", "")
    base = {"write": write, "runner": run.get("runner", ""), "host": run.get("host", ""),
            "campaign": run.get("campaign", "")}
    _post(url, dict(base, run=run, rows=rows[:MAX_ROWS_PER_POST]))
    for i in range(MAX_ROWS_PER_POST, len(rows), MAX_ROWS_PER_POST):
        _post(url, dict(base, rows=rows[i:i + MAX_ROWS_PER_POST]))


def report_from_files(cfg: dict, run: dict, report_rows: list[dict]) -> None:
    rows = [{"row": r.get("Row", ""), "email": r.get("Email", ""), "name": r.get("Name", ""),
             "result": r.get("Result", ""), "error": r.get("Error", ""), "time": r.get("Time", ""),
             "link": r.get("Conversation link", "")} for r in report_rows]
    send_report(cfg, run, rows)


# ------------------------------------------------------------------ reading usage (admin)
def _get(url: str, timeout: float = 45) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "CrispAutomation"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def ping(cfg: dict, get=_get) -> str:
    url = str(cfg.get("report_url", "")).strip()
    if not valid_url(url):
        return "Paste the web app address from Apps Script (it ends with /exec)."
    try:
        said = get(url + ("&" if "?" in url else "?") + urllib.parse.urlencode(
            {"ping": 1, "key": cfg.get("admin_key", "")})).strip()
    except Exception as exc:  # noqa: BLE001
        return f"The collector did not answer ({exc}). Check that it is deployed with access: Anyone."
    if said == "ok":
        return ""
    return "That address answered, but not with the admin key. Re-deploy the setup script, or check the key."


def fetch(cfg: dict, what: str, since: str = "", get=_get) -> dict:
    """{'columns': [...], 'rows': [[...], ...]} from the 'runs' or 'sends' tab."""
    url = str(cfg.get("report_url", "")).strip()
    q = urllib.parse.urlencode({"key": cfg.get("admin_key", ""), "what": what, "since": since})
    data = json.loads(get(url + ("&" if "?" in url else "?") + q) or "{}")
    return {"columns": data.get("columns", []), "rows": data.get("rows", [])}


def _col(columns: list, name: str) -> int:
    return columns.index(name) if name in columns else -1


def summarize_runs(data: dict) -> list[dict]:
    """Per runner: runs, totals, last seen, versions — newest activity first."""
    cols, rows = data.get("columns", []), data.get("rows", [])
    ci = {k: _col(cols, k) for k in ("Runner", "Host", "Version", "Sent", "Failed", "Skipped", "Finished")}
    by: dict[str, dict] = {}

    def num(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0
    for r in rows:
        def g(k):
            return r[ci[k]] if 0 <= ci[k] < len(r) else ""
        who = g("Runner") or "(unknown)"
        s = by.setdefault(who, {"runner": who, "runs": 0, "sent": 0, "failed": 0, "skipped": 0,
                                "last": "", "hosts": set(), "versions": set()})
        s["runs"] += 1
        s["sent"] += num(g("Sent"))
        s["failed"] += num(g("Failed"))
        s["skipped"] += num(g("Skipped"))
        s["last"] = max(s["last"], g("Finished") or "")
        if g("Host"):
            s["hosts"].add(g("Host"))
        if g("Version"):
            s["versions"].add(g("Version"))
    out = []
    for s in by.values():
        s["hosts"] = ", ".join(sorted(s["hosts"]))
        s["versions"] = ", ".join(sorted(s["versions"]))
        out.append(s)
    return sorted(out, key=lambda s: s["last"], reverse=True)
