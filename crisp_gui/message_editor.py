"""Message editor for sending through the Crisp API: a formatting toolbar that writes
the Markdown Crisp itself stores (**bold**, *italic*, lists, links ...), plus a preview.

Behaves like a QPlainTextEdit for the rest of the app: toPlainText / setPlainText /
textChanged / setPlaceholderText.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import QInputDialog, QPlainTextEdit, QTextBrowser, QVBoxLayout, QWidget

from .widgets import ResponsiveRow, button, label

# Tests answer the link / image prompts through this: hook(title) -> text or None.
PROMPT_HOOK = None


def _ask(parent, title: str, prompt: str) -> str | None:
    if PROMPT_HOOK is not None:
        return PROMPT_HOOK(title)
    text, ok = QInputDialog.getText(parent, title, prompt)
    return text.strip() if ok and text.strip() else None


class MessageEditor(QWidget):
    textChanged = Signal()

    def __init__(self, tm=None):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        bar = ResponsiveRow(min_item_width=70, spacing=4)     # wraps on narrow windows
        self.buttons = {}
        for key, text, tip in (
                ("bold", "B", "Bold (Ctrl+B)"), ("italic", "I", "Italic (Ctrl+I)"),
                ("strike", "S", "Strikethrough"), ("bullets", "• List", "Bulleted list"),
                ("numbers", "1. List", "Numbered list"), ("quote", "❝ Quote", "Quote"),
                ("code", "</>", "Code"), ("link", "Link", "Link (Ctrl+K)"), ("image", "Image", "Image from a URL"),
                ("first_name", "{first_name}", "The recipient's first name"),
                ("name", "{name}", "The recipient's full name"),
                ("ai_opener", "{ai_opener}", "Where the AI opening line goes (optional)"),
                ("divider", "A/B ---", "Start another version for an A/B test"),
                ("preview", "Preview", "Show how the email will look")):
            b = button(text, "ghost")
            b.setToolTip(tip)
            b.setAccessibleName(tip)
            b.clicked.connect(lambda _c=False, k=key: self.apply(k))
            bar.add(b)
            self.buttons[key] = b
        self.buttons["preview"].setCheckable(True)
        lay.addWidget(bar)
        self.edit = QPlainTextEdit()
        self.edit.textChanged.connect(self._changed)
        lay.addWidget(self.edit, 1)
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(False)
        self.preview.hide()
        lay.addWidget(self.preview, 1)
        self.count = label("", "faint")
        lay.addWidget(self.count)
        for keys, action in (("Ctrl+B", "bold"), ("Ctrl+I", "italic"), ("Ctrl+K", "link")):
            QShortcut(QKeySequence(keys), self.edit, activated=lambda a=action: self.apply(a))
        self._changed()

    # ---------------------------------------------------------- QPlainTextEdit-like API
    def toPlainText(self) -> str:
        return self.edit.toPlainText()

    def setPlainText(self, text: str) -> None:
        self.edit.setPlainText(text)

    def setPlaceholderText(self, text: str) -> None:
        self.edit.setPlaceholderText(text)

    def setMinimumHeight(self, h: int) -> None:   # noqa: N802 (Qt name)
        self.edit.setMinimumHeight(h)

    # ---------------------------------------------------------- editing
    def _changed(self):
        text = self.edit.toPlainText()
        versions = len([p for p in text.split("\n---\n") if p.strip()]) if text.strip() else 0
        self.count.setText(f"{len(text)} characters" + (f" · {versions} versions (A/B)" if versions > 1 else ""))
        if self.preview.isVisible():
            self.preview.setMarkdown(text)
        self.textChanged.emit()

    def _wrap(self, left: str, right: str | None = None, placeholder: str = "text"):
        right = left if right is None else right
        cur = self.edit.textCursor()
        sel = cur.selectedText().replace(" ", "\n")
        if sel:
            cur.insertText(f"{left}{sel}{right}")
        else:
            cur.insertText(f"{left}{placeholder}{right}")
            cur.movePosition(QTextCursor.Left, QTextCursor.MoveAnchor, len(right))
            cur.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor, len(placeholder))
            self.edit.setTextCursor(cur)

    def _prefix_lines(self, numbered: bool = False, prefix: str = "- "):
        cur = self.edit.textCursor()
        start, end = cur.selectionStart(), cur.selectionEnd()
        cur.setPosition(start)
        cur.movePosition(QTextCursor.StartOfBlock)
        cur.setPosition(end, QTextCursor.KeepAnchor)
        cur.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
        lines = cur.selectedText().split(" ")
        out = [f"{i}. {l}" if numbered else f"{prefix}{l}" for i, l in enumerate(lines, start=1)]
        cur.insertText("\n".join(out))

    def _insert(self, text: str):
        self.edit.textCursor().insertText(text)

    def apply(self, action: str):
        if action == "bold":
            self._wrap("**")
        elif action == "italic":
            self._wrap("*")
        elif action == "strike":
            self._wrap("~~")
        elif action == "code":
            self._wrap("`", placeholder="code")
        elif action == "bullets":
            self._prefix_lines()
        elif action == "numbers":
            self._prefix_lines(numbered=True)
        elif action == "quote":
            self._prefix_lines(prefix="> ")
        elif action == "link":
            url = _ask(self, "Link", "Web address (https://…):")
            if url:
                cur = self.edit.textCursor()
                text = cur.selectedText() or "link text"
                cur.insertText(f"[{text}]({url})")
        elif action == "image":
            url = _ask(self, "Image", "Image web address (https://…):")
            if url:
                self._insert(f"![image]({url})")
        elif action in ("first_name", "name", "ai_opener"):
            self._insert("{" + action + "}")
        elif action == "divider":
            cur = self.edit.textCursor()
            cur.movePosition(QTextCursor.End)
            self.edit.setTextCursor(cur)
            self._insert(("\n" if self.edit.toPlainText() and not self.edit.toPlainText().endswith("\n") else "")
                         + "---\n")
        elif action == "preview":
            on = self.buttons["preview"].isChecked()
            if on:
                self.preview.setMarkdown(self.edit.toPlainText().replace("{first_name}", "Ann")
                                         .replace("{name}", "Ann Lee"))
            self.preview.setVisible(on)
            self.edit.setVisible(not on)
        self.edit.setFocus()
