"""Message editor for sending without the browser (Crisp API or My email).

Visual mode: write like in an email app (bold shows bold, links show as links, images show
as images). It is saved as the simple Markdown the app has always stored (**bold**, *italic*,
[text](url), - lists ...), one line per line, so Crisp API sending and old campaigns work as before.
HTML mode (My email only): paste a full HTML email; it is sent exactly as written.
A/B versions are tabs; they are saved with a line with only --- between them.

Behaves like a QPlainTextEdit for the rest of the app: toPlainText / setPlainText /
textChanged / setPlaceholderText.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QUrl, Signal
from PySide6.QtGui import (
    QColor, QFont, QImage, QKeySequence, QPainter, QShortcut, QSyntaxHighlighter, QTextBlockFormat,
    QTextCharFormat, QTextCursor, QTextDocument, QTextFormat, QTextImageFormat, QTextListFormat,
)
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QInputDialog, QLabel, QMenu, QPlainTextEdit, QSplitter, QTabBar, QTextBrowser,
    QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

from . import widgets as W
from .widgets import SegmentedControl, label

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mail_sender  # noqa: E402  (the same HTML the email gets)
import personal as pz  # noqa: E402  (same field filling as the agent)
import send_control as sc  # noqa: E402  (share links -> direct image links)

# Insert field menu: (label, text inserted). None = a separator.
FIELDS = (
    ("First name", "{first_name}"),
    ("Full name", "{name}"),
    ("AI opening line", "{ai_opener}"),
    None,
    ("Amount spent  {spent}", "{spent}"),
    ("Number of orders  {orders}", "{orders}"),
    ("Plugins they use  {plugins}", "{plugins}"),
    ("Bundle plugins they don't have  {missing_plugins}", "{missing_plugins}"),
    ("Bundle list (set once)  {bundle: ...}", "{bundle: PostX, WowStore, WowAddons, WholesaleX, WowRevenue}"),
    ("Any column  {Country|there}", "{Country|there}"),
    None,
    ("Only if spent 599 or more  {if ...}", "{if spent >= 599}You've already spent {spent} with us.{endif}"),
    ("If / otherwise  {if ...}{else}", "{if tags contains WooCommerce}Text for store owners.{else}Text for everyone else.{endif}"),
)
SEPARATOR = "\n---\n"
QUOTE = QTextFormat.UserProperty + 1
SAMPLE_NAME = "Ann Lee"

def _web_view():
    """A real browser view for the preview (same engine as Chrome), when the app loaded it at start.
    Tests and computers without it use Qt's simple HTML viewer instead."""
    if "PySide6.QtWebEngineWidgets" not in sys.modules or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return None
    try:
        from PySide6.QtWebEngineCore import QWebEnginePage
        from PySide6.QtWebEngineWidgets import QWebEngineView
    except Exception:  # noqa: BLE001
        return None

    class Page(QWebEnginePage):
        def acceptNavigationRequest(self, url, kind, main_frame):   # noqa: N802 (Qt name)
            return kind != QWebEnginePage.NavigationType.NavigationTypeLinkClicked   # links don't leave the preview

    view = QWebEngineView()
    view.setPage(Page(view))
    return view


# Tests answer the link / image prompts through this: hook(title) -> text or None.
PROMPT_HOOK = None


def _ask(parent, title: str, prompt: str, text: str = "") -> str | None:
    if PROMPT_HOOK is not None:
        return PROMPT_HOOK(title)
    value, ok = QInputDialog.getText(parent, title, prompt, text=text)
    return value.strip() if ok and value.strip() else None


def split_versions(text: str) -> list[str]:
    parts, cur = [], []
    for line in str(text or "").split("\n"):
        if re.fullmatch(r"\s*-{3,}\s*", line):
            parts.append("\n".join(cur).strip("\n"))
            cur = []
        else:
            cur.append(line)
    parts.append("\n".join(cur).strip("\n"))
    return parts or [""]


# ------------------------------------------------------------------ Markdown <-> document
_INLINE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)\)"
    r"|\[(?P<ltext>[^\]]+)\]\((?P<href>[^)\s]+)\)"
    r"|\*\*(?P<bold>.+?)\*\*"
    r"|~~(?P<strike>.+?)~~"
    r"|`(?P<code>[^`]+)`"
    r"|(?<![\w*])\*(?!\s)(?P<italic>.+?)(?<!\s)\*(?![\w*])")


def _insert_inline(cur: QTextCursor, text: str, fmt: QTextCharFormat, accent: str) -> None:
    pos = 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            cur.insertText(text[pos:m.start()], fmt)
        f = QTextCharFormat(fmt)
        if m.group("src"):
            img = QTextImageFormat()
            img.setName(m.group("src"))
            img.setProperty(QTextFormat.ImageAltText, m.group("alt") or "image")
            cur.insertImage(img)
        elif m.group("href"):
            f.setAnchor(True)
            f.setAnchorHref(m.group("href"))
            f.setForeground(QColor(accent))
            f.setFontUnderline(True)
            _insert_inline(cur, m.group("ltext"), f, accent)
        elif m.group("bold") is not None:
            f.setFontWeight(QFont.Bold)
            _insert_inline(cur, m.group("bold"), f, accent)
        elif m.group("strike") is not None:
            f.setFontStrikeOut(True)
            _insert_inline(cur, m.group("strike"), f, accent)
        elif m.group("code") is not None:
            f.setFontFixedPitch(True)
            f.setFontFamilies(["monospace"])
            cur.insertText(m.group("code"), f)
        else:
            f.setFontItalic(True)
            _insert_inline(cur, m.group("italic"), f, accent)
        pos = m.end()
    if pos < len(text):
        cur.insertText(text[pos:], fmt)


def load_markdown(doc: QTextDocument, md: str, accent: str = "#5b5bd6") -> None:
    """The app's Markdown -> rich text, one line = one paragraph."""
    md = sc.repair_markdown(md)                       # older saves: markers around line breaks
    doc.clear()
    cur = QTextCursor(doc)
    plain = QTextCharFormat()
    lists = {}                                        # style -> the list the previous line was in
    for i, line in enumerate(str(md or "").split("\n")):
        if i:
            cur.insertBlock(QTextBlockFormat(), plain)
        bullet = re.match(r"^\s*[-*]\s+(.*)$", line)
        number = re.match(r"^\s*\d+\.\s+(.*)$", line)
        quote = re.match(r"^>\s?(.*)$", line)
        if bullet or number:
            style = QTextListFormat.ListDisc if bullet else QTextListFormat.ListDecimal
            prev = lists.get(style)
            if prev is not None and cur.block().previous().textList() is prev:
                prev.add(cur.block())
            else:
                lists.clear()
                lists[style] = cur.createList(style)
            line = (bullet or number).group(1)
        else:
            lists.clear()
            if quote:
                bf = QTextBlockFormat()
                bf.setProperty(QUOTE, True)
                bf.setLeftMargin(14)
                cur.setBlockFormat(bf)
                line = quote.group(1)
        _insert_inline(cur, line, plain, accent)
    set_email_spacing(doc)
    doc.setModified(False)


LINE_HEIGHT = 130      # percent of Arial 14px line spacing (~16px) = 21px = the email CSS line-height 1.5 (14px x 1.5)


def set_email_spacing(doc: QTextDocument) -> None:
    """Every line gets the email's line spacing and no extra paragraph space; new lines
    typed with Enter copy it from the line above."""
    bf = QTextBlockFormat()
    bf.setLineHeight(LINE_HEIGHT, 1)                 # 1 = proportional height
    bf.setTopMargin(0)
    bf.setBottomMargin(0)
    block = doc.begin()
    while block.isValid():                           # only lines that differ (pasted text): undo stays clean
        f = block.blockFormat()
        if f.lineHeight() != LINE_HEIGHT or f.topMargin() or f.bottomMargin():
            cur = QTextCursor(block)
            cur.mergeBlockFormat(bf)
        block = block.next()


def _wrap_md(text: str, left: str, right: str) -> str:
    """Markers hug the words: '**Buy now** ' not '**Buy now **' (that would not be bold).
    A line break inside (Shift+Enter, pasted text) ends the markers on each line."""
    if "\n" in text:
        return "\n".join(_wrap_md(part, left, right) for part in text.split("\n"))
    core = text.strip(" ")
    if not core:
        return text
    lead, trail = text[:len(text) - len(text.lstrip(" "))], text[len(text.rstrip(" ")):]
    return f"{lead}{left}{core}{right}{trail}"


def _link_md(text: str, href: str) -> str:
    """[text](url), with spaces and line breaks kept outside the brackets; a link on
    nothing but a line break is just that line break (no empty link)."""
    if "\n" in text:
        return "\n".join(_link_md(part, href) for part in text.split("\n"))
    core = text.strip(" ")
    if not core:
        return text
    lead, trail = text[:len(text) - len(text.lstrip(" "))], text[len(text.rstrip(" ")):]
    return f"{lead}[{core}]({href}){trail}"


def block_markdown(block) -> str:
    out = []
    it = block.begin()
    while not it.atEnd():
        frag = it.fragment()
        it += 1
        if not frag.isValid():
            continue
        f = frag.charFormat()
        if f.isImageFormat():
            img = f.toImageFormat()
            alt = img.property(QTextFormat.ImageAltText) or "image"
            out.append(f"![{alt}]({img.name()})" * len(frag.text()))
            continue
        t = frag.text().replace(" ", "\n").replace(" ", " ")
        if f.fontFixedPitch():
            t = _wrap_md(t, "`", "`")
        else:
            if f.fontStrikeOut():
                t = _wrap_md(t, "~~", "~~")
            if f.fontItalic():
                t = _wrap_md(t, "*", "*")
            if f.fontWeight() >= QFont.DemiBold:
                t = _wrap_md(t, "**", "**")
        if f.isAnchor() and f.anchorHref():
            out.append(("LINK", f.anchorHref(), t))
        else:
            out.append(t)
    # one link across several fragments (e.g. half of it bold) becomes one [text](url)
    md, i = [], 0
    while i < len(out):
        part = out[i]
        if isinstance(part, tuple):
            href, texts = part[1], [part[2]]
            while i + 1 < len(out) and isinstance(out[i + 1], tuple) and out[i + 1][1] == href:
                i += 1
                texts.append(out[i][2])
            md.append(_link_md("".join(texts), href))
        else:
            md.append(part)
        i += 1
    return "".join(md)


def to_markdown(doc: QTextDocument) -> str:
    lines = []
    block = doc.begin()
    while block.isValid():
        text = block_markdown(block)
        lst = block.textList()
        if lst is not None:
            numbered = lst.format().style() in (QTextListFormat.ListDecimal, QTextListFormat.ListLowerAlpha,
                                                QTextListFormat.ListUpperAlpha)
            prefix = f"{lst.itemNumber(block) + 1}. " if numbered else "- "
        elif block.blockFormat().property(QUOTE):
            prefix = "> "
        else:
            prefix = ""
        lines.append(prefix + text)
        block = block.next()
    return "\n".join(lines).rstrip("\n")


# ------------------------------------------------------------------ images from the web
class ImageLoader(QObject):
    """Fetches images by URL in the background and shows them in the editor and preview.
    Until one arrives (or if it can't be reached) a small 'image' box stands in."""

    loaded = Signal(str, QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cache: dict[str, QImage] = {}
        self.pending: set[str] = set()

    @staticmethod
    def placeholder(text: str = "Image") -> QImage:
        img = QImage(QSize(240, 48), QImage.Format_ARGB32)
        img.fill(QColor("#EEEEF2"))
        p = QPainter(img)
        p.setPen(QColor("#888895"))
        p.drawRect(0, 0, 239, 47)
        p.drawText(img.rect(), Qt.AlignCenter, text)
        p.end()
        return img

    def image(self, url: str) -> QImage:
        if url in self.cache:
            return self.cache[url]
        if url not in self.pending and url.startswith(("http://", "https://")):
            self.pending.add(url)
            threading.Thread(target=self._fetch, args=(url,), daemon=True).start()
        return self.placeholder("Loading image…" if url.startswith("http") else "Image")

    @staticmethod
    def gif_frame(data: bytes) -> QImage:
        """The first frame of a GIF that shows something (many GIFs start on a blank frame)."""
        from PySide6.QtCore import QBuffer, QByteArray
        from PySide6.QtGui import QImageReader
        buf = QBuffer()
        buf.setData(QByteArray(data))
        buf.open(QBuffer.ReadOnly)
        reader = QImageReader(buf, b"gif")
        first = QImage()
        for _ in range(min(max(reader.imageCount(), 1), 40)):
            frame = reader.read()
            if frame.isNull():
                break
            first = first if not first.isNull() else frame
            step = max(1, frame.width() // 24)
            if len({frame.pixel(x, y) for x in range(0, frame.width(), step)
                    for y in range(0, frame.height(), step)}) > 8:
                return frame
        return first

    def _fetch(self, url: str) -> None:
        img, gif = QImage(), False
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 CrispAutomation"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read(15_000_000)
            gif = data[:4] == b"GIF8"
            img = self.gif_frame(data) if gif else QImage.fromData(data)
        except Exception:  # noqa: BLE001
            pass
        if img.isNull():
            img = self.placeholder("Drive file not shared publicly" if "drive.usercontent" in url
                                   else "Image not reachable")
        else:
            if img.width() > 520:
                img = img.scaledToWidth(520, Qt.SmoothTransformation)
            if gif:                                   # the editor shows one frame; the email animates
                img = img.convertToFormat(QImage.Format_ARGB32)
                p = QPainter(img)
                p.fillRect(6, 6, 38, 20, QColor(0, 0, 0, 150))
                p.setPen(QColor("#FFFFFF"))
                p.drawText(6, 6, 38, 20, Qt.AlignCenter, "GIF")
                p.end()
        self.cache[url] = img
        self.loaded.emit(url, img)


class _ImageDoc(QTextDocument):
    def __init__(self, loader: ImageLoader, parent=None):
        super().__init__(parent)
        self.loader = loader

    def loadResource(self, kind, url):   # noqa: N802 (Qt name)
        if kind == QTextDocument.ImageResource:
            return self.loader.image(url.toString())
        return super().loadResource(kind, url)


# ------------------------------------------------------------------ highlighting
class FieldHighlighter(QSyntaxHighlighter):
    """{first_name} and other fields stand out; in HTML mode, tags are dimmed."""

    def __init__(self, doc, tm=None, html_tags=False):
        super().__init__(doc)
        self.tm, self.html_tags = tm, html_tags

    def highlightBlock(self, text):   # noqa: N802 (Qt name)
        p = self.tm.palette if self.tm else None
        if self.html_tags:
            tag = QTextCharFormat()
            tag.setForeground(QColor(p.text_muted if p else "#777"))
            for m in re.finditer(r"<[^>]*>", text):
                self.setFormat(m.start(), m.end() - m.start(), tag)
        field = QTextCharFormat()
        field.setForeground(QColor(p.accent if p else "#5b5bd6"))
        field.setBackground(QColor(p.accent_soft if p else "#EEEEFC"))
        field.setFontWeight(QFont.DemiBold)
        in_style = self.previousBlockState() == 1
        if self.html_tags and (in_style or re.search(r"<style\b", text, re.I)):
            self.setCurrentBlockState(0 if re.search(r"</style\s*>", text, re.I) else 1)
            return                                   # CSS braces are not fields
        self.setCurrentBlockState(0)
        for m in re.finditer(r"\{[^{}\n]+\}", text):
            self.setFormat(m.start(), m.end() - m.start(), field)


# ------------------------------------------------------------------ the editor
class MessageEditor(QWidget):
    textChanged = Signal()

    def __init__(self, tm=None):
        super().__init__()
        self.tm = tm
        self.mode = "visual"
        self.html_allowed = True
        self.versions = [""]
        self.current = 0
        self._loading = False
        self.loader = ImageLoader(self)
        self.loader.loaded.connect(self._image_arrived)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.mode_switch = SegmentedControl([("visual", "Visual", None), ("html", "HTML", None)], tm)
        self.mode_switch.set_value("visual")
        self.mode_switch.setToolTip("Visual: write like in an email app. HTML: paste the full HTML code of an "
                                    "email (My email only).")
        self.mode_switch.changed.connect(self.set_mode)
        top.addWidget(self.mode_switch)
        top.addStretch()
        self.preview_toggle = QToolButton()
        self.preview_toggle.setObjectName("toolText")
        self.preview_toggle.setText("Hide preview")
        self.preview_toggle.setCheckable(True)
        self.preview_toggle.setChecked(True)
        self.preview_toggle.setCursor(Qt.PointingHandCursor)
        self.preview_toggle.toggled.connect(self._toggle_preview)
        top.addWidget(self.preview_toggle)
        outer.addLayout(top)

        # tabs for A/B versions
        tabs_row = QHBoxLayout()
        tabs_row.setSpacing(4)
        self.tabs = QTabBar()
        self.tabs.setObjectName("versionTabs")
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        self.tabs.addTab("Version 1")
        self.tabs.currentChanged.connect(self._switch_version)
        tabs_row.addWidget(self.tabs)
        self.add_version_btn = self._tool("+ Version", "Add another version: recipients are split evenly "
                                          "between the versions (A/B test)", self.add_version, wide=True)
        tabs_row.addWidget(self.add_version_btn)
        self.remove_version_btn = self._tool("Remove this version", "Delete the version shown below",
                                             lambda: self.remove_version(self.current), wide=True)
        tabs_row.addWidget(self.remove_version_btn)
        tabs_row.addStretch()
        outer.addLayout(tabs_row)

        self.split = QSplitter(Qt.Horizontal)
        self.split.setChildrenCollapsible(False)
        outer.addWidget(self.split, 1)

        # writing side: one framed box with the toolbar on top
        self.box = QFrame()
        self.box.setObjectName("editorBox")
        box = QVBoxLayout(self.box)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        self.toolbar = QFrame()
        self.toolbar.setObjectName("editorToolbar")
        bar = QHBoxLayout(self.toolbar)
        bar.setContentsMargins(6, 4, 6, 4)
        bar.setSpacing(2)
        self.buttons = {}
        groups = (
            (("bold", "B", "Bold (Ctrl+B)"), ("italic", "I", "Italic (Ctrl+I)"),
             ("strike", "S", "Strikethrough")),
            (("bullets", "•≡", "Bulleted list"), ("numbers", "1≡", "Numbered list"), ("quote", "❝", "Quote")),
            (("link", "Link", "Link (Ctrl+K)"), ("image", "Image / GIF", "Image or GIF from a web address or a Google Drive / Dropbox / Giphy link"),
             ("code", "</>", "Code")),
        )
        for g, group in enumerate(groups):
            if g:
                bar.addWidget(self._sep())
            for key, text, tip in group:
                b = self._tool(text, tip, lambda _c=False, k=key: self.apply(k), wide=len(text) > 2)
                self.buttons[key] = b
                bar.addWidget(b)
        for key, css in (("bold", "font-weight: 700;"), ("italic", "font-style: italic;"),
                         ("strike", "text-decoration: line-through;")):
            self.buttons[key].setStyleSheet(css)
        bar.addWidget(self._sep())
        self.buttons["fields"] = self._tool("Insert field ▾", "Fill in data from each person's row: name, amount "
                                            "spent, plugins, any column", lambda: self.apply("fields"), wide=True)
        bar.addWidget(self.buttons["fields"])
        bar.addStretch()
        box.addWidget(self.toolbar)

        self.visual = QTextEdit()
        self.visual.setObjectName("messageVisual")
        self.visual.setDocument(_ImageDoc(self.loader, self.visual))
        self.visual.setAcceptRichText(True)
        self.visual.setFrameShape(QFrame.NoFrame)
        self.visual.document().setDocumentMargin(14)
        email_font = QFont("Arial")                  # same font, size and line spacing as the email
        email_font.setStyleHint(QFont.SansSerif)
        email_font.setPixelSize(14)
        self.visual.document().setDefaultFont(email_font)
        self.visual.textChanged.connect(self._changed)
        self.visual.cursorPositionChanged.connect(self._sync_buttons)
        self._hl_visual = FieldHighlighter(self.visual.document(), tm)
        box.addWidget(self.visual, 1)

        self.html = QPlainTextEdit()
        self.html.setObjectName("messageHtml")
        self.html.setFrameShape(QFrame.NoFrame)
        self.html.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        mono = QFont("monospace")
        mono.setStyleHint(QFont.Monospace)
        mono.setPixelSize(12)
        self.html.setFont(mono)
        self.html.document().setDocumentMargin(10)
        self.html.setPlaceholderText("Paste the full HTML of your email here (from Mailchimp, Stripo, BeeFree, "
                                     "your own template...).\n{first_name} and other fields work in it too.")
        self.html.textChanged.connect(self._changed)
        self._hl_html = FieldHighlighter(self.html.document(), tm, html_tags=True)
        self.html.hide()
        box.addWidget(self.html, 1)
        self.split.addWidget(self.box)

        # preview side: what one person receives
        self.preview_box = QFrame()
        self.preview_box.setObjectName("editorBox")
        pv = QVBoxLayout(self.preview_box)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)
        head = QFrame()
        head.setObjectName("editorToolbar")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(12, 8, 12, 8)
        self.preview_title = label(f"Preview · as {SAMPLE_NAME} sees it", "rowTitle")
        hl.addWidget(self.preview_title)
        hl.addStretch()
        pv.addWidget(head)
        self.phone = QToolButton()
        self.phone.setObjectName("toolText")
        self.phone.setText("Phone width")
        self.phone.setCheckable(True)
        self.phone.setToolTip("See the email as on a phone (375 pixels wide)")
        self.phone.setCursor(Qt.PointingHandCursor)
        self.phone.toggled.connect(self._phone_width)
        hl.addWidget(self.phone)
        self.web = _web_view()
        if self.web is not None:
            self.preview = self.web
        else:
            self.preview = QTextBrowser()
            self.preview.setDocument(_ImageDoc(self.loader, self.preview))
            self.preview.setFrameShape(QFrame.NoFrame)
            self.preview.setOpenExternalLinks(False)
            self.preview.setOpenLinks(False)
            self.preview.document().setDocumentMargin(16)
        self.preview.setObjectName("messagePreview")
        self._page = ""
        prow = QHBoxLayout()
        prow.setContentsMargins(0, 0, 0, 0)
        prow.addWidget(self.preview, 1)
        pv.addLayout(prow, 1)
        self.split.addWidget(self.preview_box)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 2)

        self.note = label("", "fieldError", wrap=True)
        self.note.hide()
        outer.addWidget(self.note)
        self.count = label("", "faint")
        outer.addWidget(self.count)

        for keys, action in (("Ctrl+B", "bold"), ("Ctrl+I", "italic"), ("Ctrl+K", "link")):
            QShortcut(QKeySequence(keys), self.visual, activated=lambda a=action: self.apply(a))
        if tm is not None:
            tm.changed.connect(lambda *_: self._refresh_preview())
        self._load_version()

    # ---------------------------------------------------------- small builders
    def _tool(self, caption: str, tip: str, slot, wide: bool = False) -> QToolButton:
        b = QToolButton()
        b.setObjectName("toolText" if wide else "toolBtn")
        b.setText(caption)
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.setCursor(Qt.PointingHandCursor)
        b.setAutoRaise(True)
        b.clicked.connect(slot)
        b.setMinimumWidth(b.sizeHint().width())          # never cut to "I…e"
        return b

    @staticmethod
    def _sep():
        s = QFrame()
        s.setObjectName("toolSep")
        s.setFixedSize(1, 18)
        return s

    def resizeEvent(self, event):   # noqa: N802 (Qt name)
        """Narrow window: the preview goes under the editor, so the toolbar keeps its full width."""
        super().resizeEvent(event)
        want = Qt.Vertical if self.width() < 820 else Qt.Horizontal
        if self.split.orientation() != want:
            self.split.setOrientation(want)
            self._size_preview()

    # ---------------------------------------------------------- QPlainTextEdit-like API
    def toPlainText(self) -> str:   # noqa: N802 (Qt name)
        self._store_current()
        versions = list(self.versions)
        while len(versions) > 1 and not versions[-1].strip():
            versions.pop()
        return SEPARATOR.join(v.strip("\n") for v in versions).strip("\n") if any(v.strip() for v in versions) \
            else ""

    def setPlainText(self, text: str) -> None:   # noqa: N802 (Qt name)
        text = str(text or "")
        self.versions = split_versions(text) if text.strip() else [""]
        html_mode = any(pz.is_html(v) for v in self.versions)
        self.mode = "html" if html_mode else "visual"
        self.mode_switch.set_value(self.mode)
        self._rebuild_tabs(0)
        self._load_version()
        self._changed()

    def setPlaceholderText(self, text: str) -> None:   # noqa: N802 (Qt name)
        self.visual.setPlaceholderText(text)

    def setMinimumHeight(self, h: int) -> None:   # noqa: N802 (Qt name)
        self.visual.setMinimumHeight(h)
        self.html.setMinimumHeight(h)
        self.preview.setMinimumHeight(h)

    def set_html_allowed(self, allowed: bool) -> None:
        """HTML can only be sent with My email; the Crisp API would show the code."""
        self.html_allowed = allowed
        self.mode_switch.buttons["html"].setEnabled(allowed or self.mode == "html")
        self.mode_switch.buttons["html"].setToolTip("" if allowed else "HTML works with Send with: My email.")
        self._update_note()

    # ---------------------------------------------------------- versions
    def _store_current(self) -> None:
        if self._loading or not (0 <= self.current < len(self.versions)):
            return
        self.versions[self.current] = (to_markdown(self.visual.document()) if self.mode == "visual"
                                       else self.html.toPlainText())

    def _load_version(self) -> None:
        self._loading = True
        text = self.versions[self.current] if self.current < len(self.versions) else ""
        if self.mode == "visual":
            load_markdown(self.visual.document(), text, self.tm.palette.accent if self.tm else "#5b5bd6")
        else:
            self.html.setPlainText(text)
        self.visual.setVisible(self.mode == "visual")
        self.toolbar.setVisible(self.mode == "visual")
        self.html.setVisible(self.mode == "html")
        self._loading = False
        self._size_preview()

    def _size_preview(self) -> None:
        """HTML emails are usually 600 px wide: give the preview room for the desktop layout."""
        total = sum(self.split.sizes())
        if self.split.orientation() == Qt.Horizontal and total > 0:
            want = 660 if self.mode == "html" else int(total * 0.4)
            self.split.setSizes([max(300, total - want), min(want, total - 300)])

    def _rebuild_tabs(self, index: int) -> None:
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        for i in range(len(self.versions)):
            self.tabs.addTab(f"Version {i + 1}")
        self.current = max(0, min(index, len(self.versions) - 1))
        self.tabs.setCurrentIndex(self.current)
        self.tabs.blockSignals(False)
        self.tabs.setVisible(len(self.versions) > 1)
        self.remove_version_btn.setVisible(len(self.versions) > 1)

    def _switch_version(self, index: int) -> None:
        if index < 0 or index == self.current:
            return
        self._store_current()
        self.current = index
        self._load_version()
        self._changed()

    def add_version(self) -> None:
        self._store_current()
        self.versions.append("")
        self._rebuild_tabs(len(self.versions) - 1)
        self._load_version()
        self._changed()
        (self.visual if self.mode == "visual" else self.html).setFocus()

    def remove_version(self, index: int) -> None:
        if len(self.versions) <= 1:
            return
        self._store_current()
        if self.versions[index].strip() and not W.ask(self, f"Delete Version {index + 1}?",
                                                      "Its text is removed from this campaign.", confirm="Delete"):
            return
        del self.versions[index]
        self._rebuild_tabs(min(self.current, len(self.versions) - 1))
        self._load_version()
        self._changed()

    # ---------------------------------------------------------- modes
    def set_mode(self, mode: str) -> None:
        if mode == self.mode:
            return
        self._store_current()
        if mode == "html":
            if not self.html_allowed:
                self.mode_switch.set_value(self.mode)
                W.inform(self, "HTML needs My email", "The Crisp API sends text, so HTML code would arrive as "
                         "code. Choose Send with: My email to send HTML.")
                return
            # start from what was written: the same email as HTML
            self.versions = [mail_sender.to_html(v) if v.strip() else "" for v in self.versions]
        else:
            if any(v.strip() for v in self.versions) and not W.ask(
                    self, "Switch to the Visual editor?", "The HTML is turned into simple text with bold, links, "
                    "lists and images. Colors, layout and styles are lost.", confirm="Switch"):
                self.mode_switch.set_value(self.mode)
                return
            converted = []
            for v in self.versions:
                doc = QTextDocument()
                doc.setHtml(v)
                converted.append(to_markdown(doc).strip("\n") if v.strip() else "")
            self.versions = converted
        self.mode = mode
        self.mode_switch.set_value(mode)
        self._load_version()
        self._changed()

    # ---------------------------------------------------------- live updates
    def _changed(self) -> None:
        if self._loading:
            return
        if getattr(self, "mode", "") == "visual" and not getattr(self, "_spacing", False):
            self._spacing = True                     # pasted lines: the email's spacing, not the page's
            try:
                set_email_spacing(self.visual.document())
            finally:
                self._spacing = False
        self._store_current()
        text = self.toPlainText()
        n = len([v for v in self.versions if v.strip()])
        words = len(re.findall(r"\w+", re.sub(r"<[^>]+>", " ", self.versions[self.current])))
        self.count.setText(f"{words} words" + (f" · {n} versions (A/B), split evenly" if n > 1 else "")
                           + (" · HTML" if self.mode == "html" else ""))
        self._update_note()
        self._refresh_preview()
        self.textChanged.emit()
        return text

    def _update_note(self) -> None:
        show = self.mode == "html" and not self.html_allowed
        self.note.setText("This message is HTML, which the Crisp API can't send. Choose Send with: My email, "
                          "or switch to Visual." if show else "")
        self.note.setVisible(show)

    def _refresh_preview(self) -> None:
        if not self.preview_box.isVisible() and not self.preview_toggle.isChecked():
            return
        text = self.versions[self.current] if self.current < len(self.versions) else ""
        filled = pz.render(sc.fix_image_links(sc.repair_markdown(text)), SAMPLE_NAME, pz.SAMPLE)[0] if text.strip() else ""
        page = filled if self.mode == "html" else mail_sender.to_html(filled)
        page = page if filled else '<p style="color:#9a9aa5;font-family:Arial">The email appears here as you write.</p>'
        if page == self._page:
            return
        self._page = page
        if self.web is not None:
            self.web.setHtml(page, QUrl("https://preview.invalid/"))   # images load from the web
        else:
            bar = self.preview.verticalScrollBar().value()
            self.preview.setHtml(page)
            self.preview.verticalScrollBar().setValue(bar)
        n = len(self.versions)
        self.preview_title.setText(f"Preview · Version {self.current + 1} · as {SAMPLE_NAME} sees it" if n > 1
                                   else f"Preview · as {SAMPLE_NAME} sees it")

    def preview_text(self) -> str:
        """What the preview shows, as text (tests, and a quick check)."""
        return mail_sender.html_to_text(self._page)

    def _phone_width(self, on: bool) -> None:
        self.preview.setMaximumWidth(375 if on else 16777215)

    def _toggle_preview(self, on: bool) -> None:
        self.preview_box.setVisible(on)
        self.preview_toggle.setText("Hide preview" if on else "Show preview")
        if on:
            self._refresh_preview()

    def _image_arrived(self, url: str, img: QImage) -> None:
        for view in (self.visual,) + (() if self.web is not None else (self.preview,)):
            doc = view.document()
            doc.addResource(QTextDocument.ImageResource, QUrl(url), img)
            doc.markContentsDirty(0, doc.characterCount())
            view.viewport().update()

    def _sync_buttons(self) -> None:
        f = self.visual.currentCharFormat()
        for key, on in (("bold", f.fontWeight() >= QFont.DemiBold), ("italic", f.fontItalic()),
                        ("strike", f.fontStrikeOut())):
            b = self.buttons[key]
            b.setCheckable(True)
            b.setChecked(on)

    # ---------------------------------------------------------- formatting
    def _merge(self, fmt: QTextCharFormat) -> None:
        cur = self.visual.textCursor()
        if not cur.hasSelection():
            cur.select(QTextCursor.WordUnderCursor)
        cur.mergeCharFormat(fmt)
        self.visual.mergeCurrentCharFormat(fmt)

    def _toggle_list(self, style) -> None:
        cur = self.visual.textCursor()
        lst = cur.currentList()
        cur.beginEditBlock()
        if lst is not None and lst.format().style() == style:
            for block in self._selected_blocks(cur):
                lst.remove(block)
                bf = block.blockFormat()
                bf.setIndent(0)
                QTextCursor(block).setBlockFormat(bf)
        else:
            fmt = QTextListFormat()
            fmt.setStyle(style)
            cur.createList(fmt)
        cur.endEditBlock()

    @staticmethod
    def _selected_blocks(cur):
        doc = cur.document()
        block = doc.findBlock(cur.selectionStart())
        last = doc.findBlock(cur.selectionEnd())
        out = []
        while block.isValid():
            out.append(block)
            if block == last:
                break
            block = block.next()
        return out

    def apply(self, action: str) -> None:
        if action in ("first_name", "name", "ai_opener"):
            self.insert_text("{" + action + "}")
            return
        if action == "fields":
            menu = QMenu(self)
            for item in FIELDS:
                if item is None:
                    menu.addSeparator()
                else:
                    menu.addAction(item[0], lambda v=item[1]: self.insert_text(v))
            b = self.buttons["fields"]
            menu.exec(b.mapToGlobal(b.rect().bottomLeft()))
            return
        if action == "divider":
            self.add_version()
            return
        if self.mode != "visual":
            return
        cur = self.visual.textCursor()
        f = QTextCharFormat()
        now = self.visual.currentCharFormat()
        if action == "bold":
            f.setFontWeight(QFont.Normal if now.fontWeight() >= QFont.DemiBold else QFont.Bold)
            self._merge(f)
        elif action == "italic":
            f.setFontItalic(not now.fontItalic())
            self._merge(f)
        elif action == "strike":
            f.setFontStrikeOut(not now.fontStrikeOut())
            self._merge(f)
        elif action == "code":
            fixed = not now.fontFixedPitch()
            f.setFontFixedPitch(fixed)
            f.setFontFamilies(["monospace"] if fixed else [self.visual.font().family()])
            self._merge(f)
        elif action == "bullets":
            self._toggle_list(QTextListFormat.ListDisc)
        elif action == "numbers":
            self._toggle_list(QTextListFormat.ListDecimal)
        elif action == "quote":
            for block in self._selected_blocks(cur):
                bf = block.blockFormat()
                on = not bf.property(QUOTE)
                bf.setProperty(QUOTE, on)
                bf.setLeftMargin(14 if on else 0)
                QTextCursor(block).setBlockFormat(bf)
        elif action == "link":
            current = now.anchorHref() if now.isAnchor() else ""
            url = _ask(self, "Link", "Web address (https://…):", current or "https://")
            if url:
                if not re.match(r"^(https?://|mailto:)", url):
                    url = "https://" + url
                f.setAnchor(True)
                f.setAnchorHref(url)
                f.setForeground(QColor(self.tm.palette.accent if self.tm else "#5b5bd6"))
                f.setFontUnderline(True)
                if cur.hasSelection():
                    cur.mergeCharFormat(f)
                else:
                    cur.insertText("link text", f)
                    self.visual.setCurrentCharFormat(QTextCharFormat())
        elif action == "image":
            url = _ask(self, "Image or GIF", "Image or GIF web address (https://…). A Google Drive, Dropbox or "
                       "Giphy share link works too (Drive: share it as 'Anyone with the link'):")
            if url:
                url = sc.direct_image_url(url)
                img = QTextImageFormat()
                img.setName(url)
                img.setProperty(QTextFormat.ImageAltText, "image")
                cur.insertImage(img)
        self.visual.setFocus()
        self._changed()

    def insert_text(self, text: str) -> None:
        if self.mode == "html":
            self.html.textCursor().insertText(text)
            self.html.setFocus()
        else:
            self.visual.textCursor().insertText(text, QTextCharFormat())
            self.visual.setFocus()
