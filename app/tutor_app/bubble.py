"""The floating circle: always on top, draggable. Left click = activate (share screen + voice); right click = menu."""

import math

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

COLORS = {"loading": "#8e8e96", "idle": "#1a73e8", "listening": "#e53935", "thinking": "#f9a825",
          "speaking": "#2e7d32", "error": "#6d4c41"}
GLYPH = {"loading": "…", "idle": "?", "listening": "➤", "thinking": "", "speaking": "", "error": "!"}
TIPS = {"loading": "Offline Tutor: loading the model…",
        "idle": "Offline Tutor: click, then speak and circle anything on screen. Right-click for options.",
        "listening": "Listening to your voice and screen. Click me again to send (Esc cancels).",
        "thinking": "Thinking… (click to interrupt and ask again)",
        "speaking": "Speaking… (click to interrupt and ask again)",
        "error": "Offline Tutor: something went wrong (right-click for details)"}


class Bubble(QWidget):
    clicked = Signal()
    menu_requested = Signal(QPoint)
    moved = Signal(QPoint)
    SIZE = 64

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.state, self._phase = "loading", 0.0
        self._press, self._dragging = None, False
        self._anim = QTimer(self, interval=40, timeout=self._tick)
        self._anim.start()
        self.set_state("loading")

    def set_state(self, state):
        self.state = state
        self.setToolTip(TIPS[state])
        self.update()

    def _tick(self):
        if self.state in ("thinking", "listening", "loading", "speaking"):
            self._phase = (self._phase + 0.08) % (2 * math.pi)
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(COLORS[self.state])
        r = self.rect().adjusted(6, 6, -6, -6)
        if self.state == "listening":  # pulsing halo
            halo = QColor(c)
            halo.setAlpha(int(60 + 50 * math.sin(self._phase * 2)))
            p.setPen(Qt.NoPen)
            p.setBrush(halo)
            p.drawEllipse(self.rect().adjusted(1, 1, -1, -1))
        g = QRadialGradient(r.center(), r.width() / 2)
        g.setColorAt(0, c.lighter(130))
        g.setColorAt(1, c)
        p.setPen(QPen(QColor(255, 255, 255, 200), 2))
        p.setBrush(g)
        p.drawEllipse(r)
        if self.state in ("thinking", "loading"):  # spinner arc
            p.setPen(QPen(QColor("white"), 3, cap=Qt.RoundCap))
            arc = QRectF(r.adjusted(12, 12, -12, -12))
            p.drawArc(arc, int(-math.degrees(self._phase) * 16 * 3), 100 * 16)
        if self.state == "speaking":  # animated sound bars
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("white"))
            cx, cy = r.center().x(), r.center().y()
            for i in range(4):
                h = 6 + 14 * abs(math.sin(self._phase * 3 + i * 0.9))
                p.drawRoundedRect(QRectF(cx - 13 + i * 7, cy - h / 2, 4, h), 2, 2)
        if GLYPH[self.state]:
            p.setPen(QColor("white"))
            f = QFont("Segoe UI", 18, QFont.Bold)
            p.setFont(f)
            p.drawText(r, Qt.AlignCenter, GLYPH[self.state])

    # --- click vs drag
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press, self._dragging = e.globalPosition().toPoint(), False
            self._origin = self.pos()
        elif e.button() == Qt.RightButton:
            self.menu_requested.emit(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        if self._press is None:
            return
        delta = e.globalPosition().toPoint() - self._press
        if self._dragging or delta.manhattanLength() > 5:
            self._dragging = True
            self.move(self._origin + delta)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self._press is None:
            return
        if self._dragging:
            self.moved.emit(self.pos())
        else:
            self.clicked.emit()
        self._press, self._dragging = None, False
