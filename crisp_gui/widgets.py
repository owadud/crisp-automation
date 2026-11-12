"""Reusable themed widgets."""

from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QAbstractButton, QButtonGroup, QDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from .icons import icon
from .theme import Palette, ThemeManager


def label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    if wrap:
        w.setWordWrap(True)
    return w


def button(text: str, name: str | None = None, icon_name: str | None = None,
           tm: ThemeManager | None = None, color_role: str = "text") -> QPushButton:
    """Push button whose optional icon is redrawn when the theme changes."""
    b = QPushButton(text)
    if name:
        b.setObjectName(name)
    b.setCursor(Qt.PointingHandCursor)
    if icon_name and tm is not None:
        def paint(p: Palette, b=b):
            role = color_role
            if b.objectName() in ("primary", "danger"):
                role = "accent_text"
            elif b.objectName() == "dangerOutline":
                role = "danger"
            b.setIcon(icon(icon_name, getattr(p, role)))
        tm.changed.connect(paint)
        paint(tm.palette)
        b.setIconSize(QSize(14, 14))
    return b


def divider() -> QFrame:
    f = QFrame()
    f.setObjectName("divider")
    f.setFrameShape(QFrame.NoFrame)
    return f


class Card(QFrame):
    def __init__(self, title: str = "", hint: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(20, 18, 20, 20)
        self.body.setSpacing(14)
        if title or hint:
            head = QHBoxLayout()
            head.setSpacing(8)
            texts = QVBoxLayout()
            texts.setSpacing(2)
            if title:
                self.title = label(title, "cardTitle")
                texts.addWidget(self.title)
            if hint:
                self.hint = label(hint, "cardHint", wrap=True)
                texts.addWidget(self.hint)
            head.addLayout(texts, 1)
            self.head_actions = QHBoxLayout()
            self.head_actions.setSpacing(6)
            head.addLayout(self.head_actions)
            self.body.addLayout(head)


class MetricCard(QFrame):
    def __init__(self, title: str, caption: str = ""):
        super().__init__()
        self.setObjectName("card")
        self.setMinimumHeight(104)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(6)
        self.title = label(title, "metricLabel")
        self.value = label("—", "metricValue")
        self.caption = label(caption, "metricCaption")
        self.caption.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lay.addWidget(self.title)
        lay.addWidget(self.value)
        lay.addWidget(self.caption)
        lay.addStretch()

    def set(self, value, caption: str | None = None):
        self.value.setText(str(value))
        if caption is not None:
            self.caption.setText(caption)
            self.caption.setToolTip(caption)


class Field(QWidget):
    """Label + line edit + help/validation text."""

    def __init__(self, title: str, help_text: str = "", placeholder: str = ""):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.title = label(title, "fieldLabel")
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.setClearButtonEnabled(False)
        self.help_text = help_text
        self.message = label(help_text, "fieldHelp", wrap=True)
        self.message.setVisible(bool(help_text))
        lay.addWidget(self.title)
        lay.addWidget(self.edit)
        lay.addWidget(self.message)

    def text(self) -> str:
        return self.edit.text().strip()

    def set_state(self, kind: str = "help", text: str | None = None):
        """kind: help | error | ok"""
        names = {"help": "fieldHelp", "error": "fieldError", "ok": "fieldOk"}
        msg = self.help_text if text is None else text
        self.message.setObjectName(names.get(kind, "fieldHelp"))
        self.message.setText(msg)
        self.message.setVisible(bool(msg))
        self.edit.setProperty("invalid", kind == "error")
        for w in (self.message, self.edit):
            w.style().unpolish(w)
            w.style().polish(w)


class ToggleSwitch(QAbstractButton):
    """iOS-style switch. Behaves like a checkable button (toggled signal)."""

    def __init__(self, tm: ThemeManager, parent=None):
        super().__init__(parent)
        self.tm = tm
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(40, 22)
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)
        tm.changed.connect(lambda *_: self.update())

    def _animate(self, checked):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def get_knob(self):
        return self._pos

    def set_knob(self, value):
        self._pos = value
        self.update()

    knob = Property(float, get_knob, set_knob)

    def setChecked(self, checked):  # keep knob in sync without animation lag
        super().setChecked(checked)
        self._anim.stop()
        self._pos = 1.0 if checked else 0.0
        self.update()

    def sizeHint(self):
        return QSize(40, 22)

    def paintEvent(self, _):
        p = self.tm.palette
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        off = QColor(p.border_strong)
        on = QColor(p.accent)
        track = QColor(
            int(off.red() + (on.red() - off.red()) * self._pos),
            int(off.green() + (on.green() - off.green()) * self._pos),
            int(off.blue() + (on.blue() - off.blue()) * self._pos),
        )
        if not self.isEnabled():
            track.setAlpha(110)
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(0, 0, 40, 22), 11, 11)
        painter.setBrush(QColor("#FFFFFF"))
        x = 2 + self._pos * 18
        painter.drawEllipse(QRectF(x, 2, 18, 18))
        if self.hasFocus():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QColor(p.accent))
            painter.drawRoundedRect(QRectF(0.5, 0.5, 39, 21), 11, 11)


class SegmentedControl(QFrame):
    changed = Signal(str)

    def __init__(self, options, tm: ThemeManager | None = None):
        """options: list of (key, text, icon_name|None)"""
        super().__init__()
        self.setObjectName("segmented")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        for key, text, icon_name in options:
            b = button(text, "segment", icon_name, tm, "text_muted")
            b.setCheckable(True)
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def set_value(self, key):
        if key in self.buttons:
            self.buttons[key].setChecked(True)

    def value(self):
        for key, b in self.buttons.items():
            if b.isChecked():
                return key
        return None


class StatusPill(QWidget):
    """Coloured dot + text, e.g. 'Running'."""

    TONES = ("neutral", "accent", "success", "warning", "danger")

    def __init__(self, tm: ThemeManager):
        super().__init__()
        self.tm = tm
        self.tone = "neutral"
        self.text = "Idle"
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        tm.changed.connect(lambda *_: self.update())

    def set(self, text: str, tone: str):
        self.text, self.tone = text, tone
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text) + 30, 24)

    def _colors(self):
        p = self.tm.palette
        return {
            "neutral": (p.text_faint, p.surface_alt),
            "accent": (p.accent, p.accent_soft),
            "success": (p.success, p.success_soft),
            "warning": (p.warning, p.warning_soft),
            "danger": (p.danger, p.danger_soft),
        }[self.tone]

    def paintEvent(self, _):
        fg, bg = self._colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        painter.setPen(QColor(self.tm.palette.border))
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(fg))
        painter.drawEllipse(QRectF(10, self.height() / 2 - 3, 6, 6))
        painter.setPen(QColor(self.tm.palette.text))
        f = painter.font()
        f.setWeight(f.Weight.Medium)
        painter.setFont(f)
        painter.drawText(QRectF(21, 0, self.width() - 21, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self.text)


class Banner(QFrame):
    def __init__(self, tm: ThemeManager, kind: str = "warning", text: str = ""):
        super().__init__()
        self.tm = tm
        self.kind = kind
        self.setObjectName({"warning": "bannerWarning", "danger": "bannerDanger"}.get(kind, "bannerInfo"))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(10)
        self.icon = QLabel()
        self.icon.setFixedSize(16, 16)
        self.icon.setAlignment(Qt.AlignTop)
        self.text = label(text, "bannerText", wrap=True)
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.icon, 0, Qt.AlignTop)
        lay.addWidget(self.text, 1)
        tm.changed.connect(self._paint_icon)
        self._paint_icon(tm.palette)

    def _paint_icon(self, p: Palette):
        color = {"warning": p.warning, "danger": p.danger}.get(self.kind, p.accent)
        self.icon.setPixmap(icon("alert", color).pixmap(16, 16))

    def set_text(self, text: str):
        self.text.setText(text)


class KeyValueList(QWidget):
    def __init__(self):
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(10)
        self.grid.setColumnStretch(1, 1)
        self.values = {}

    def add(self, key: str, title: str):
        row = self.grid.rowCount()
        k = label(title, "kvKey")
        v = label("—", "kvValue", wrap=True)
        v.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.grid.addWidget(k, row, 0, Qt.AlignTop)
        self.grid.addWidget(v, row, 1)
        self.values[key] = v

    def set(self, key: str, text: str, tooltip: str = ""):
        self.values[key].setText(text or "—")
        self.values[key].setToolTip(tooltip)


class ResponsiveRow(QWidget):
    """Lays children out in N columns, dropping to fewer when narrow."""

    def __init__(self, min_item_width: int = 190, spacing: int = 12):
        super().__init__()
        self.items = []
        self.min_item_width = min_item_width
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(spacing)
        self._cols = 0

    def add(self, widget: QWidget):
        self.items.append(widget)
        self._relayout(force=True)

    def shown(self) -> list:
        """Children not hidden on purpose (a hidden one leaves no gap)."""
        return [w for w in self.items
                if not (w.testAttribute(Qt.WA_WState_ExplicitShowHide) and w.testAttribute(Qt.WA_WState_Hidden))]

    def columns_for(self, width: int) -> int:
        items = self.shown()
        n = len(items) or 1
        # Never fit more columns than the widest child allows (long labels).
        item_w = max([self.min_item_width] + [w.minimumSizeHint().width() for w in items])
        cols = max(1, min(n, (width + self.grid.spacing()) // (item_w + self.grid.spacing())))
        if n == 4 and cols == 3:
            cols = 2  # 2x2 looks better than 3+1
        return cols

    def _relayout(self, force=False):
        cols = self.columns_for(max(self.width(), 1))
        if cols == self._cols and not force:
            return
        self._cols = cols
        for w in self.items:
            self.grid.removeWidget(w)
        for c in range(len(self.items)):
            self.grid.setColumnStretch(c, 0)
        for i, w in enumerate(self.shown()):
            self.grid.addWidget(w, i // cols, i % cols)
        for c in range(cols):
            self.grid.setColumnStretch(c, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()


class LogHighlighter(QSyntaxHighlighter):
    """Colours important agent lines; everything else uses the log colour."""

    def __init__(self, document, tm: ThemeManager):
        super().__init__(document)
        self.tm = tm
        tm.changed.connect(lambda *_: self.rehighlight())

    def _fmt(self, color: str, bold: bool = False) -> QTextCharFormat:
        f = QTextCharFormat()
        f.setForeground(QColor(color))
        if bold:
            f.setFontWeight(600)
        return f

    def highlightBlock(self, text: str):
        p = self.tm.palette
        s = text.strip()
        fmt = None
        if s.startswith(("FAILED:", "ERROR:")) or s.startswith("Failed:") and not s.endswith(" 0"):
            fmt = self._fmt(p.danger, True)
        elif s.startswith(("WARNING", "Warning")) or "MAY already have been sent" in s:
            fmt = self._fmt(p.warning)
        elif s in ("SUCCESS.", "Email sent successfully.") or s.startswith("Completed:"):
            fmt = self._fmt(p.success, True)
        elif s.startswith("[DRY RUN]"):
            fmt = self._fmt(p.accent)
        elif s.startswith("[") and "/" in s.split("]")[0]:
            fmt = self._fmt(p.text, True)
        elif set(s) == {"="}:
            fmt = self._fmt(p.text_faint)
        elif s.startswith("›"):
            fmt = self._fmt(p.text_muted)
        if fmt is not None:
            self.setFormat(0, len(text), fmt)


class Dialog(QDialog):
    """Themed confirmation / message dialog."""

    def __init__(self, parent, title: str, body: str, details=None,
                 confirm: str = "OK", cancel: str | None = "Cancel",
                 tone: str = "primary", tm: ThemeManager | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setMaximumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        self.title_label = label(title, "dialogTitle", wrap=True)
        lay.addWidget(self.title_label)
        self.body_label = label(body, "dialogBody", wrap=True)
        self.body_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.body_label)
        if details:
            box = QFrame()
            box.setObjectName("dialogDetails")
            grid = QGridLayout(box)
            grid.setContentsMargins(14, 12, 14, 12)
            grid.setHorizontalSpacing(16)
            grid.setVerticalSpacing(8)
            grid.setColumnStretch(1, 1)
            for r, (k, v) in enumerate(details):
                grid.addWidget(label(k, "kvKey"), r, 0, Qt.AlignTop)
                val = label(v, "kvValue", wrap=True)
                val.setTextInteractionFlags(Qt.TextSelectableByMouse)
                grid.addWidget(val, r, 1)
            lay.addWidget(box)
        lay.addSpacing(6)
        row = QHBoxLayout()
        row.addStretch()
        self.cancel_button = None
        if cancel:
            self.cancel_button = button(cancel)
            self.cancel_button.clicked.connect(self.reject)
            row.addWidget(self.cancel_button)
        self.confirm_button = button(confirm, "danger" if tone == "danger" else "primary")
        self.confirm_button.clicked.connect(self.accept)
        row.addWidget(self.confirm_button)
        lay.addLayout(row)
        # Safe default: Enter cancels a dangerous action.
        if tone == "danger" and self.cancel_button is not None:
            self.cancel_button.setDefault(True)
            self.cancel_button.setFocus()
        else:
            self.confirm_button.setDefault(True)

    def showEvent(self, event):
        """Wrapped text needs height that depends on the width: grow to fit it, so no
        line is squeezed or cut (seen on Ubuntu, where the first layout pass is too short)."""
        super().showEvent(event)
        self.fit_text()

    def fit_text(self):
        lay = self.layout()
        if lay is None or not lay.hasHeightForWidth():
            return
        need = lay.totalHeightForWidth(self.width())
        if need > 0:
            self.setMinimumHeight(need)
            if self.height() < need:
                self.resize(self.width(), need)


# Overridable in tests so dialogs can be answered automatically.
DIALOG_HOOK = None


def ask(parent, title, body, details=None, confirm="OK", cancel="Cancel", tone="primary") -> bool:
    if DIALOG_HOOK is not None:
        return bool(DIALOG_HOOK(title, body, details, confirm))
    dlg = Dialog(parent, title, body, details, confirm, cancel, tone)
    return dlg.exec() == QDialog.Accepted


def inform(parent, title, body, details=None):
    if DIALOG_HOOK is not None:
        DIALOG_HOOK(title, body, details, "OK")
        return
    Dialog(parent, title, body, details, "OK", None).exec()


class Collapsible(QWidget):
    """'Advanced' section: a header button that shows or hides its body (keyboard: Space/Enter)."""

    def __init__(self, title: str, open_: bool = False):
        super().__init__()
        self._title = title
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)
        self.head = button("", "ghost")
        self.head.setCheckable(True)
        self.head.setAccessibleName(title)
        self.head.toggled.connect(self.set_open)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.head)
        row.addStretch()
        lay.addLayout(row)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(16)
        lay.addWidget(self.body)
        self.set_open(open_)

    def add(self, widget):
        self.body_layout.addWidget(widget)
        return widget

    def is_open(self) -> bool:
        return self.body.isVisibleTo(self)

    def set_open(self, on: bool):
        self.head.blockSignals(True)
        self.head.setChecked(on)
        self.head.blockSignals(False)
        self.head.setText(("▾ " if on else "▸ ") + self._title)
        self.body.setVisible(on)
