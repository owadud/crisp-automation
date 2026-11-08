"""Lists (CSV files or Google Sheet tabs) whose recorded sends feed Campaign Results.

A list the app (or an older version, or another computer) sent from records
each send in its own rows: Mail Sent, Sent At, Status, Conversation link.
"Keep in sync" lists are re-read before every status refresh, so sends made
anywhere show up here. Lists are only READ; nothing is written to them.
Saved in <data_dir>/results_sources.json. No Qt here.
"""

from __future__ import annotations

import csv
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import campaign_results as cr  # noqa: E402
import send_control as sc  # noqa: E402


def source_label(src: dict) -> str:
    if src.get("kind") == "sheet":
        return f"Google Sheet · tab {src.get('tab') or '?'}"
    return Path(src.get("csv", "")).name or "CSV"


def same_source(a: dict, b: dict) -> bool:
    keys = ("kind", "csv", "sheet_url", "tab", "campaign", "reference")
    return all(str(a.get(k, "")).strip().lower() == str(b.get(k, "")).strip().lower() for k in keys)


class SyncSources:
    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.items: list[dict] = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.items = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.items, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, src: dict) -> dict:
        for item in self.items:
            if same_source(item, src):
                item.update({k: v for k, v in src.items() if k != "id"})
                self.save()
                return item
        item = dict(src, id=uuid.uuid4().hex[:8])
        self.items.append(item)
        self.save()
        return item

    def remove(self, sid: str) -> None:
        self.items = [i for i in self.items if i.get("id") != sid]
        self.save()

    def mark(self, sid: str, report: dict) -> None:
        for item in self.items:
            if item.get("id") == sid:
                item["last_synced"] = sc.stamp()
                item["last_result"] = {k: report.get(k, 0) for k in ("found", "added", "skipped")}
        self.save()


def read_rows(src: dict, sheet_factory) -> tuple[list[dict], list[str]]:
    if src.get("kind") == "sheet":
        sheet = sheet_factory(src["sheet_url"], src["tab"], src.get("key", ""))
        return sheet.download()
    with open(Path(src["csv"]).expanduser(), encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def sync(sources: list[dict], sheet_factory, history_path: Path) -> list[dict]:
    """Read each list and add its recorded sends to history (never twice).
    One report per list; a list that cannot be read reports its error."""
    reports = []
    for src in sources:
        report = {"id": src.get("id", ""), "label": source_label(src), "campaign": src.get("campaign", ""),
                  "found": 0, "added": 0, "skipped": 0, "other_reference": 0, "no_link": 0, "error": ""}
        try:
            rows, header = read_rows(src, sheet_factory)
            found, counts = cr.sends_from_list(rows, header, src["campaign"], src.get("reference", ""),
                                               report["label"])
            report.update(counts)
            report["added"], report["skipped"] = cr.import_into_history(history_path, found)
        except Exception as exc:  # noqa: BLE001  (shown to the user, the other lists still sync)
            report["error"] = str(exc) or type(exc).__name__
        reports.append(report)
    return reports


def report_text(reports: list[dict]) -> str:
    lines = []
    for r in reports:
        if r["error"]:
            lines.append(f"{r['label']} → {r['campaign']}: could not read it ({r['error']})")
            continue
        line = f"{r['label']} → {r['campaign']}: {r['added']} new, {r['skipped']} already there"
        if r["other_reference"]:
            line += f", {r['other_reference']} with another Reference left out"
        if r["no_link"]:
            line += f", {r['no_link']} without a conversation link"
        lines.append(line + ".")
    return "\n".join(lines)
