"""Per-row preview: exactly what a run would do with each row.

Shown from "Preview rows…" and before every real run (as the confirmation).
Building it never sends anything and never writes the CSV, Sheet or history.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QHeaderView, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from . import widgets as W

COLUMNS = ("Row", "Email", "Name used", "Subject", "Shortcut", "Segment", "Reference", "Action")


def preview_rows(plan, campaign: dict) -> list[tuple]:
    """Table rows (strings) from CsvStats.plan + the campaign fields.
    A/B test: the expected shortcut per row (the real run balances by actual sends)."""
    import send_control as sc
    try:
        variants = sc.parse_variants(campaign.get("shortcut", ""), campaign.get("shortcut_version", ""))
    except ValueError:
        variants = [(campaign.get("shortcut", ""), "")]
    counts = [0] * len(variants)
    out = []
    for p in plan:
        shortcut = variants[0][0]
        if len(variants) > 1 and p["send"]:
            i = sc.pick_variant(counts)
            counts[i] += 1
            shortcut = f"{variants[i][0]} ({variants[i][1]})"
        elif len(variants) > 1:
            shortcut = ""
        name = p["name"] + (" (from email)" if p.get("fallback") else "") if p["email"] else ""
        action = p["action"] + (f" ({p['detail']})" if p.get("detail") and not p["send"] else "")
        out.append((str(p["row"]), p["email"], name, campaign.get("subject", ""), shortcut,
                    campaign.get("segment", ""), p.get("reference", ""), action))
    return out


class PreviewDialog(QDialog):
    def __init__(self, parent, title: str, summary: str, rows: list[tuple], confirm: str | None = None,
                 danger: bool = False, columns=COLUMNS):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(1100, 640)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(W.label(title, "dialogTitle", wrap=True))
        body = W.label(summary, "dialogBody", wrap=True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(body)
        self.only_skipped = QCheckBox("Show only skipped rows (who and why)")
        lay.addWidget(self.only_skipped)
        self.only_skipped.setVisible(columns is COLUMNS)
        self.table = QTableWidget(len(rows), len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        self._send = []
        for r, values in enumerate(rows):
            self._send.append(values[-1] == "Send")
            for c, value in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        for c in range(len(columns) - 1):
            self.table.setColumnWidth(c, min(self.table.columnWidth(c), 260))
        lay.addWidget(self.table, 1)
        self.only_skipped.toggled.connect(self._filter)
        row = QHBoxLayout()
        row.addStretch()
        close = W.button("Cancel" if confirm else "Close")
        close.clicked.connect(self.reject)
        row.addWidget(close)
        if confirm:
            self.confirm_button = W.button(confirm, "danger" if danger else "primary")
            self.confirm_button.clicked.connect(self.accept)
            row.addWidget(self.confirm_button)
            close.setDefault(danger)
        lay.addLayout(row)

    def _filter(self, only_skipped: bool):
        for r, send in enumerate(self._send):
            self.table.setRowHidden(r, only_skipped and send)


def show_preview(parent, title, summary, rows, confirm=None, danger=False, columns=COLUMNS) -> bool:
    """True if confirmed (always True without a confirm button). Tests answer via DIALOG_HOOK."""
    if W.DIALOG_HOOK is not None:
        return bool(W.DIALOG_HOOK(title, summary, rows, confirm or "Close"))
    dlg = PreviewDialog(parent, title, summary, rows, confirm, danger, columns)
    return dlg.exec() == QDialog.Accepted or confirm is None
