"""Pick the date and time (Dhaka) at which a campaign starts by itself."""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import QDateTimeEdit, QDialog, QHBoxLayout, QVBoxLayout

from .widgets import button, label

# Tests answer through this: hook(default "YYYY-MM-DD HH:MM") -> "YYYY-MM-DD HH:MM" or None.
SCHEDULE_HOOK = None


class ScheduleDialog(QDialog):
    def __init__(self, parent, name: str, default: datetime, now: datetime):
        super().__init__(parent)
        self.setWindowTitle("Schedule campaign")
        self.setModal(True)
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        lay.addWidget(label(f"Schedule “{name}”", "dialogTitle", wrap=True))
        lay.addWidget(label("Dhaka time. The app must be open and the computer awake at that time; if it is "
                            "not, the campaign is marked missed and nothing is sent late.", "dialogBody", wrap=True))
        self.when = QDateTimeEdit(QDateTime(default.year, default.month, default.day, default.hour, default.minute, 0))
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("yyyy-MM-dd  HH:mm")
        self.when.setMinimumDateTime(QDateTime(now.year, now.month, now.day, now.hour, now.minute, 0))
        lay.addWidget(self.when)
        row = QHBoxLayout()
        row.addStretch()
        cancel = button("Cancel")
        cancel.clicked.connect(self.reject)
        ok = button("Next", "primary")
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)

    def value(self) -> str:
        return self.when.dateTime().toString("yyyy-MM-dd HH:mm")


def ask_when(parent, name: str, now: datetime) -> str | None:
    """'YYYY-MM-DD HH:MM' (Dhaka) or None. Default: the next full hour."""
    default = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    if SCHEDULE_HOOK is not None:
        return SCHEDULE_HOOK(default.strftime("%Y-%m-%d %H:%M"))
    dlg = ScheduleDialog(parent, name, default, now)
    return dlg.value() if dlg.exec() == QDialog.Accepted else None
