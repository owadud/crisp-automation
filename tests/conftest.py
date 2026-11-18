"""Shared test setup.

Qt's offscreen platform has no fonts of its own on Windows, so every letter is drawn
as a wide box and the window-width tests see overflow that real users never get.
Point it at the Windows fonts (only when nobody set QT_QPA_FONTDIR already)."""

import os
from pathlib import Path

_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
if os.name == "nt" and _FONTS.is_dir():
    os.environ.setdefault("QT_QPA_FONTDIR", str(_FONTS))
