"""Light and dark palettes, the application stylesheet, and live switching."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str            # window background
    sidebar: str
    surface: str       # cards
    surface_alt: str   # inputs, subtle fills
    hover: str
    selected: str
    border: str
    border_strong: str
    text: str
    text_muted: str
    text_faint: str
    accent: str
    accent_hover: str
    accent_text: str
    accent_soft: str
    success: str
    success_soft: str
    warning: str
    warning_soft: str
    danger: str
    danger_hover: str
    danger_soft: str
    log_bg: str
    log_text: str
    shadow: str


LIGHT = Palette(
    name="light",
    bg="#FBFBFC", sidebar="#F4F4F6", surface="#FFFFFF", surface_alt="#F7F7F9",
    hover="#EDEDF1", selected="#E7E7EC", border="#E6E6EB", border_strong="#D4D4DC",
    text="#18181B", text_muted="#63636E", text_faint="#9A9AA5",
    accent="#5B5BD6", accent_hover="#4C4CC4", accent_text="#FFFFFF", accent_soft="#EEEEFC",
    success="#18794E", success_soft="#E9F6EF",
    warning="#AD5700", warning_soft="#FFF4E5",
    danger="#CD2B31", danger_hover="#B5262B", danger_soft="#FDEDED",
    log_bg="#FAFAFB", log_text="#2B2B31", shadow="#14000000",
)

DARK = Palette(
    name="dark",
    bg="#0F0F12", sidebar="#141417", surface="#18181C", surface_alt="#1E1E23",
    hover="#232329", selected="#2A2A31", border="#26262C", border_strong="#34343C",
    text="#EDEDF0", text_muted="#9B9BA6", text_faint="#6B6B75",
    accent="#7B7BEB", accent_hover="#8D8DF2", accent_text="#FFFFFF", accent_soft="#25254A",
    success="#4CC38A", success_soft="#13291F",
    warning="#F1A10D", warning_soft="#2E2210",
    danger="#FF6369", danger_hover="#FF7A7F", danger_soft="#3A1618",
    log_bg="#131316", log_text="#D6D6DC", shadow="#66000000",
)

_FONT_CANDIDATES = (
    "Inter", "Segoe UI Variable Text", "Segoe UI", "SF Pro Text", ".AppleSystemUIFont", "Helvetica Neue",
    "Ubuntu", "Cantarell", "Noto Sans", "DejaVu Sans",
)
_MONO_CANDIDATES = (
    "JetBrains Mono", "Cascadia Mono", "Consolas", "SF Mono", "Menlo",
    "Ubuntu Mono", "DejaVu Sans Mono", "Liberation Mono", "Monospace",
)


def _first_available(candidates):
    families = set(QFontDatabase.families())
    for name in candidates:
        if name in families:
            return name
    return candidates[-1]


def build_stylesheet(p: Palette, ui_font: str, mono_font: str) -> str:
    return f"""
* {{ font-family: "{ui_font}"; font-size: 13px; color: {p.text}; outline: 0; }}
QMainWindow, QWidget#appRoot, QWidget#page, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {p.bg}; }}
QDialog {{ background: {p.surface}; }}
QLabel {{ background: transparent; }}
QToolTip {{ background: {p.surface}; color: {p.text}; border: 1px solid {p.border_strong}; padding: 6px 8px; border-radius: 6px; }}

/* sidebar ------------------------------------------------------------ */
QFrame#sidebar {{ background: {p.sidebar}; border: none; border-right: 1px solid {p.border}; }}
QLabel#brandName {{ font-size: 14px; font-weight: 600; }}
QLabel#brandSub {{ font-size: 11px; color: {p.text_faint}; }}
QLabel#sidebarSection {{ font-size: 11px; font-weight: 600; color: {p.text_faint}; padding: 0 10px; }}
QPushButton#navItem {{
    text-align: left; padding: 0 10px; min-height: 32px; max-height: 32px;
    border: none; border-radius: 6px; background: transparent;
    color: {p.text_muted}; font-weight: 500;
}}
QPushButton#navItem:hover {{ background: {p.hover}; color: {p.text}; }}
QPushButton#navItem:checked {{ background: {p.selected}; color: {p.text}; font-weight: 600; }}
QLabel#sidebarFoot {{ font-size: 11px; color: {p.text_faint}; padding: 0 10px; }}

/* header ------------------------------------------------------------- */
QFrame#topBar {{ background: {p.bg}; border: none; border-bottom: 1px solid {p.border}; }}
QLabel#pageTitle {{ font-size: 15px; font-weight: 600; }}
QLabel#pageSubtitle {{ color: {p.text_muted}; }}

/* cards -------------------------------------------------------------- */
QFrame#card {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 10px; }}
QLabel#cardTitle {{ font-size: 13px; font-weight: 600; }}
QLabel#cardHint, QLabel#muted {{ color: {p.text_muted}; }}
QLabel#faint {{ color: {p.text_faint}; font-size: 12px; }}
QLabel#metricLabel {{ color: {p.text_muted}; font-size: 12px; font-weight: 500; }}
QLabel#metricValue {{ font-size: 26px; font-weight: 600; }}
QLabel#metricCaption {{ color: {p.text_faint}; font-size: 12px; }}
QLabel#kvKey {{ color: {p.text_muted}; }}
QLabel#kvValue {{ font-weight: 500; }}
QLabel#groupTitle {{ font-size: 11px; font-weight: 600; color: {p.text_muted}; padding: 0 4px; }}
QLabel#rowTitle {{ font-weight: 600; }}
QFrame#divider {{ background: {p.border}; border: none; max-height: 1px; min-height: 1px; }}
QLabel#bigStep {{ font-size: 14px; font-weight: 600; }}

/* fields ------------------------------------------------------------- */
QLabel#fieldLabel {{ font-size: 12px; font-weight: 500; color: {p.text}; }}
QLabel#fieldHelp {{ font-size: 12px; color: {p.text_faint}; }}
QLabel#fieldError {{ font-size: 12px; color: {p.danger}; }}
QLabel#fieldOk {{ font-size: 12px; color: {p.success}; }}
QLineEdit {{
    background: {p.surface}; border: 1px solid {p.border_strong}; border-radius: 6px;
    padding: 0 10px; min-height: 34px; max-height: 34px;
    selection-background-color: {p.accent}; selection-color: {p.accent_text};
}}
QLineEdit:hover {{ border-color: {p.text_faint}; }}
QLineEdit:focus {{ border: 1px solid {p.accent}; }}
QLineEdit[invalid="true"] {{ border: 1px solid {p.danger}; }}
QLineEdit:disabled {{ background: {p.surface_alt}; color: {p.text_muted}; border-color: {p.border}; }}

/* buttons ------------------------------------------------------------ */
QPushButton {{
    background: {p.surface}; color: {p.text}; border: 1px solid {p.border_strong};
    border-radius: 6px; padding: 0 12px; min-height: 32px; font-weight: 500;
}}
QPushButton:hover {{ background: {p.hover}; }}
QPushButton:pressed {{ background: {p.selected}; }}
QPushButton:disabled {{ color: {p.text_faint}; background: {p.surface_alt}; border-color: {p.border}; }}
QPushButton#primary {{ background: {p.accent}; color: {p.accent_text}; border: 1px solid {p.accent}; }}
QPushButton#primary:hover {{ background: {p.accent_hover}; border-color: {p.accent_hover}; }}
QPushButton#primary:disabled {{ background: {p.surface_alt}; color: {p.text_faint}; border-color: {p.border}; }}
QPushButton#danger {{ background: {p.danger}; color: #FFFFFF; border: 1px solid {p.danger}; }}
QPushButton#danger:hover {{ background: {p.danger_hover}; border-color: {p.danger_hover}; }}
QPushButton#dangerOutline {{ background: {p.surface}; color: {p.danger}; border: 1px solid {p.border_strong}; }}
QPushButton#dangerOutline:hover {{ background: {p.danger_soft}; border-color: {p.danger}; }}
QPushButton#dangerOutline:disabled {{ color: {p.text_faint}; background: {p.surface_alt}; border-color: {p.border}; }}
QPushButton#ghost {{ background: transparent; border: 1px solid transparent; color: {p.text_muted}; }}
QPushButton#ghost:hover {{ background: {p.hover}; color: {p.text}; }}
QPushButton#link {{ background: transparent; border: none; color: {p.accent}; padding: 0; min-height: 20px; }}
QPushButton#link:hover {{ text-decoration: underline; }}

/* segmented control --------------------------------------------------- */
QFrame#segmented {{ background: {p.surface_alt}; border: 1px solid {p.border}; border-radius: 7px; }}
QPushButton#segment {{ background: transparent; border: 1px solid transparent; border-radius: 5px; min-height: 28px; padding: 0 14px; color: {p.text_muted}; }}
QPushButton#segment:hover {{ color: {p.text}; }}
QPushButton#segment:checked {{ background: {p.surface}; border: 1px solid {p.border_strong}; color: {p.text}; font-weight: 600; }}

/* banners and pills ---------------------------------------------------- */
QFrame#bannerWarning {{ background: {p.warning_soft}; border: 1px solid {p.warning}; border-radius: 8px; }}
QFrame#bannerDanger {{ background: {p.danger_soft}; border: 1px solid {p.danger}; border-radius: 8px; }}
QFrame#bannerInfo {{ background: {p.accent_soft}; border: 1px solid {p.border}; border-radius: 8px; }}
QLabel#bannerText {{ color: {p.text}; }}

/* progress ------------------------------------------------------------- */
QProgressBar {{ background: {p.surface_alt}; border: 1px solid {p.border}; border-radius: 4px; min-height: 8px; max-height: 8px; }}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 3px; }}
QProgressBar[state="failed"]::chunk {{ background: {p.danger}; }}
QProgressBar[state="done"]::chunk {{ background: {p.success}; }}

/* log ------------------------------------------------------------------ */
QPlainTextEdit#log {{
    background: {p.log_bg}; color: {p.log_text}; border: 1px solid {p.border}; border-radius: 8px;
    padding: 10px; font-family: "{mono_font}"; font-size: 12px;
    selection-background-color: {p.accent}; selection-color: {p.accent_text};
}}

/* scrollbars ----------------------------------------------------------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.border_strong}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {p.text_faint}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p.border_strong}; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* dialogs -------------------------------------------------------------- */
QLabel#dialogTitle {{ font-size: 15px; font-weight: 600; }}
QLabel#dialogBody {{ color: {p.text_muted}; }}
QFrame#dialogDetails {{ background: {p.surface_alt}; border: 1px solid {p.border}; border-radius: 8px; }}
QMenu {{ background: {p.surface}; border: 1px solid {p.border_strong}; border-radius: 6px; padding: 4px; }}
QMenu::item {{ padding: 6px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background: {p.hover}; }}
"""


def _qpalette(p: Palette) -> QPalette:
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(p.bg))
    pal.setColor(QPalette.WindowText, QColor(p.text))
    pal.setColor(QPalette.Base, QColor(p.surface))
    pal.setColor(QPalette.AlternateBase, QColor(p.surface_alt))
    pal.setColor(QPalette.Text, QColor(p.text))
    pal.setColor(QPalette.Button, QColor(p.surface))
    pal.setColor(QPalette.ButtonText, QColor(p.text))
    pal.setColor(QPalette.Highlight, QColor(p.accent))
    pal.setColor(QPalette.HighlightedText, QColor(p.accent_text))
    pal.setColor(QPalette.ToolTipBase, QColor(p.surface))
    pal.setColor(QPalette.ToolTipText, QColor(p.text))
    pal.setColor(QPalette.PlaceholderText, QColor(p.text_faint))
    pal.setColor(QPalette.Link, QColor(p.accent))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(p.text_faint))
    return pal


class ThemeManager(QObject):
    """Applies a palette to the whole application and announces changes.

    The stylesheet is set on QApplication, so every window and dialog
    (including ones created later) picks up the current theme.
    """

    changed = Signal(object)  # Palette

    def __init__(self, app: QApplication, mode: str = "light"):
        super().__init__(app)
        self._app = app
        self.mode = mode
        self.ui_font = _first_available(_FONT_CANDIDATES)
        self.mono_font = _first_available(_MONO_CANDIDATES)
        self.palette = LIGHT
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._system_changed)
        self.apply(mode)

    def system_is_dark(self) -> bool:
        hints = QGuiApplication.styleHints()
        try:
            return hints.colorScheme() == Qt.ColorScheme.Dark
        except AttributeError:
            return False

    def resolve(self, mode: str) -> Palette:
        if mode == "dark":
            return DARK
        if mode == "system":
            return DARK if self.system_is_dark() else LIGHT
        return LIGHT

    def apply(self, mode: str) -> None:
        self.mode = mode
        self.palette = self.resolve(mode)
        self._app.setPalette(_qpalette(self.palette))
        self._app.setStyleSheet(build_stylesheet(self.palette, self.ui_font, self.mono_font))
        self.changed.emit(self.palette)

    def _system_changed(self, *_):
        if self.mode == "system":
            self.apply("system")
