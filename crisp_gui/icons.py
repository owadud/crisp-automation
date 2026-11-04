"""Small line icons drawn with QPainter so they recolor with the theme."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

_SCALE = 3  # render at 3x for crisp HiDPI output


def _pen(color, width=1.5):
    pen = QPen(QColor(color))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _draw(name: str, p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    p.setBrush(Qt.NoBrush)
    if name == "dashboard":
        for x, y, w, h in ((2.5, 2.5, 4.5, 6), (9, 2.5, 4.5, 3.5), (2.5, 10.5, 4.5, 3), (9, 8, 4.5, 5.5)):
            p.drawRoundedRect(QRectF(x, y, w, h), 1.2, 1.2)
    elif name == "campaign":
        p.drawRoundedRect(QRectF(2, 3.5, 12, 9), 1.6, 1.6)
        path = QPainterPath(QPointF(2.6, 4.5))
        path.lineTo(8, 8.6)
        path.lineTo(13.4, 4.5)
        p.drawPath(path)
    elif name == "logs":
        p.drawRoundedRect(QRectF(2, 2.5, 12, 11), 1.6, 1.6)
        path = QPainterPath(QPointF(4.8, 6))
        path.lineTo(6.8, 8)
        path.lineTo(4.8, 10)
        p.drawPath(path)
        p.drawLine(QPointF(8.5, 10), QPointF(11.2, 10))
    elif name == "settings":
        p.drawEllipse(QPointF(8, 8), 2.1, 2.1)
        for i in range(8):
            a = i * math.pi / 4
            p.drawLine(QPointF(8 + 4.2 * math.cos(a), 8 + 4.2 * math.sin(a)),
                       QPointF(8 + 5.8 * math.cos(a), 8 + 5.8 * math.sin(a)))
        p.drawEllipse(QPointF(8, 8), 4.2, 4.2)
    elif name == "play":
        path = QPainterPath(QPointF(5, 3.5))
        path.lineTo(12.5, 8)
        path.lineTo(5, 12.5)
        path.closeSubpath()
        p.setBrush(QColor(c))
        p.drawPath(path)
    elif name == "stop":
        p.setBrush(QColor(c))
        p.drawRoundedRect(QRectF(4, 4, 8, 8), 1.5, 1.5)
    elif name == "folder":
        path = QPainterPath(QPointF(2, 4.5))
        path.lineTo(6, 4.5)
        path.lineTo(7.5, 6)
        path.lineTo(14, 6)
        path.lineTo(14, 12.5)
        path.lineTo(2, 12.5)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "sun":
        p.drawEllipse(QPointF(8, 8), 2.8, 2.8)
        for i in range(8):
            a = i * math.pi / 4
            p.drawLine(QPointF(8 + 4.6 * math.cos(a), 8 + 4.6 * math.sin(a)),
                       QPointF(8 + 6 * math.cos(a), 8 + 6 * math.sin(a)))
    elif name == "moon":
        outer = QPainterPath()
        outer.addEllipse(QPointF(8, 8), 5.5, 5.5)
        cut = QPainterPath()
        cut.addEllipse(QPointF(11, 5.5), 4.6, 4.6)
        p.drawPath(outer.subtracted(cut))
    elif name == "monitor":
        p.drawRoundedRect(QRectF(2, 3, 12, 8), 1.4, 1.4)
        p.drawLine(QPointF(6, 13.5), QPointF(10, 13.5))
        p.drawLine(QPointF(8, 11), QPointF(8, 13.5))
    elif name == "copy":
        p.drawRoundedRect(QRectF(5.5, 5.5, 8, 8), 1.4, 1.4)
        path = QPainterPath(QPointF(10.5, 3.5))
        path.lineTo(10.5, 2.5)
        path.lineTo(2.5, 2.5)
        path.lineTo(2.5, 10.5)
        path.lineTo(3.5, 10.5)
        p.drawPath(path)
    elif name == "save":
        p.drawLine(QPointF(8, 2.5), QPointF(8, 10))
        path = QPainterPath(QPointF(4.8, 7))
        path.lineTo(8, 10.2)
        path.lineTo(11.2, 7)
        p.drawPath(path)
        p.drawLine(QPointF(3, 13.3), QPointF(13, 13.3))
    elif name == "trash":
        p.drawLine(QPointF(2.8, 4.5), QPointF(13.2, 4.5))
        p.drawLine(QPointF(6.5, 2.6), QPointF(9.5, 2.6))
        path = QPainterPath(QPointF(4, 4.5))
        path.lineTo(4.8, 13.4)
        path.lineTo(11.2, 13.4)
        path.lineTo(12, 4.5)
        p.drawPath(path)
    elif name == "refresh":
        rect = QRectF(3, 3, 10, 10)
        p.drawArc(rect, 40 * 16, 280 * 16)
        path = QPainterPath(QPointF(12.6, 2.6))
        path.lineTo(12.6, 5.6)
        path.lineTo(9.6, 5.6)
        p.drawPath(path)
    elif name == "check":
        path = QPainterPath(QPointF(3.5, 8.5))
        path.lineTo(6.5, 11.5)
        path.lineTo(12.5, 4.5)
        p.drawPath(path)
    elif name == "alert":
        path = QPainterPath(QPointF(8, 2.5))
        path.lineTo(14, 13)
        path.lineTo(2, 13)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(8, 6.5), QPointF(8, 9.2))
        p.drawPoint(QPointF(8, 11.2))
    elif name == "file":
        path = QPainterPath(QPointF(4, 2))
        path.lineTo(9.5, 2)
        path.lineTo(12.5, 5)
        path.lineTo(12.5, 14)
        path.lineTo(4, 14)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(6, 9), QPointF(10.5, 9))
        p.drawLine(QPointF(6, 11.3), QPointF(10.5, 11.3))
    elif name == "send":
        path = QPainterPath(QPointF(2.5, 7.5))
        path.lineTo(13.5, 2.5)
        path.lineTo(9, 13.5)
        path.lineTo(7.4, 8.6)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "dot":
        p.setBrush(QColor(c))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(8, 8), 3.2, 3.2)


def icon(name: str, color: str, size: int = 16) -> QIcon:
    pix = QPixmap(size * _SCALE, size * _SCALE)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(size * _SCALE / 16.0, size * _SCALE / 16.0)
    _draw(name, painter, color)
    painter.end()
    pix.setDevicePixelRatio(_SCALE)
    return QIcon(pix)


def logo_pixmap(accent: str, size: int = 28) -> QPixmap:
    """Rounded accent square with a white paper-plane mark."""
    pix = QPixmap(size * _SCALE, size * _SCALE)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(_SCALE, _SCALE)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(accent))
    p.drawRoundedRect(QRectF(0, 0, size, size), 7, 7)
    p.translate(size / 2 - 8, size / 2 - 8)
    p.setPen(_pen("#FFFFFF", 1.6))
    path = QPainterPath(QPointF(2.5, 7.5))
    path.lineTo(13.5, 2.5)
    path.lineTo(9, 13.5)
    path.lineTo(7.4, 8.6)
    path.closeSubpath()
    p.drawPath(path)
    p.end()
    pix.setDevicePixelRatio(_SCALE)
    return pix
