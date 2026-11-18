"""GIFs and images in emails: share links become direct image links (editor, preview, send)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mail_sender as ms  # noqa: E402
import send_control as sc  # noqa: E402
from test_api_sending import FakeAPI, api_agent  # noqa: E402,F401
from test_v36 import agent, write  # noqa: E402,F401

FID = "1jwMivXiVIYpAMIMurshYtRTzpGe57o09"
DIRECT = f"https://lh3.googleusercontent.com/d/{FID}"


def test_share_links_become_direct_image_links():
    assert sc.direct_image_url(f"https://drive.google.com/file/d/{FID}/view?usp=sharing") == DIRECT
    assert sc.direct_image_url(f"https://drive.google.com/open?id={FID}") == DIRECT
    assert sc.direct_image_url("https://www.dropbox.com/s/abc/demo.gif?dl=0") == \
        "https://www.dropbox.com/s/abc/demo.gif?raw=1"
    assert sc.direct_image_url("https://giphy.com/gifs/happy-dance-l0MYt5jPR6QX5pnqM") == \
        "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif"
    assert sc.direct_image_url("https://example.com/a.gif") == "https://example.com/a.gif"


def test_messages_get_direct_links_in_markdown_and_html():
    md = f"Hi\n\n![demo](https://drive.google.com/file/d/{FID}/view?usp=sharing)"
    assert sc.fix_image_links(md).endswith(f"![demo]({DIRECT})")
    page = f'<p><img src="https://drive.google.com/file/d/{FID}/view" alt="demo"></p>'
    assert f'src="{DIRECT}"' in sc.fix_image_links(page)
    html = ms.to_html(sc.fix_image_links(md))
    assert "<img src=" in html and "lh3.googleusercontent.com" in html


def test_sent_message_uses_the_direct_gif_link(api_agent, tmp_path):
    path = write(tmp_path / "r.csv", ["Full Name", "Contact Email", "Reference", "Mail Sent"],
                 [["Ann Lee", "ann@x.com", "Shahriar", "FALSE"]])
    mod = api_agent(path, messages=(f"Hi {{first_name}}\n\n![demo](https://drive.google.com/file/d/{FID}/view)",))
    mod.main()
    sent = [c[2] for c in FakeAPI.calls if c[0] == "send"]
    assert sent == [f"Hi Ann\n\n![demo]({DIRECT})"]


def test_old_drive_links_and_broken_markers_are_repaired():
    old = f"![g](https://drive.usercontent.google.com/download?id={FID}&export=view)"
    assert sc.fix_image_links(old) == f"![g]({DIRECT})"
    broken = "**Deals:\n**\nhttps://x.com/offer[\n](https://x.com/offer)\n**Question:\n**"
    assert sc.repair_markdown(broken) == "**Deals:**\n\nhttps://x.com/offer\n\n**Question:**\n"
    html = ms.to_html("Visit https://x.com/offer?ref=1, or [our page](https://x.com/p).")
    assert '<a href="https://x.com/offer?ref=1">https://x.com/offer?ref=1</a>,' in html
    assert html.count("<a href") == 2


def test_editor_keeps_markers_off_soft_line_breaks():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QFont, QTextCharFormat, QTextCursor, QTextDocument
    from PySide6.QtWidgets import QApplication
    from crisp_gui import message_editor as me
    QApplication.instance() or QApplication([])
    doc = QTextDocument()
    cur = QTextCursor(doc)
    bold = QTextCharFormat()
    bold.setFontWeight(QFont.Bold)
    cur.insertText("Deals:\u2028", bold)
    cur.insertText("https://x.com/offer", QTextCharFormat())
    link = QTextCharFormat()
    link.setAnchor(True)
    link.setAnchorHref("https://x.com/offer")
    cur.insertText("\u2028", link)
    assert me.to_markdown(doc) == "**Deals:**\nhttps://x.com/offer"
