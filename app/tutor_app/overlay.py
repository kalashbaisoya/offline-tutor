"""Capture overlay shown during an activation.

The screen is frozen (the screenshot is shown full-screen), the mic is live, and the pointer becomes a pen: the
student circles whatever they mean ("yeh wala formula"). Several circles are allowed; their union is the focus region.
Sending is done from the floating circle itself (it stays on top of this overlay): click it again, or press the
hotkey / Enter. Esc / ✕ = cancel (nothing is kept). Right-click or Backspace = undo a circle.
"""

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

BANNER = ("●  Listening: ask your question and circle anything on screen.   "
          "Click the red circle again to send   ·   Esc = cancel   ·   right-click = undo circle")


def pil_to_pixmap(img):
    img = img.convert("RGB")
    q = QImage(img.tobytes(), img.width, img.height, img.width * 3, QImage.Format_RGB888)
    return QPixmap.fromImage(q.copy())


class CaptureOverlay(QWidget):
    done = Signal(object)  # dict(region=PIL|None) on send, None on cancel

    def __init__(self, shot, screen, level_fn):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.shot, self.level_fn = shot, level_fn
        self.pixmap = pil_to_pixmap(shot)
        self.strokes, self.current = [], None
        self._closed = False
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self.setCursor(QCursor(Qt.CrossCursor))
        self.setFocusPolicy(Qt.StrongFocus)

        bar = QWidget(self)
        bar.setStyleSheet("background: rgba(20,20,24,215); border-radius: 10px;")
        lay = QHBoxLayout(bar)
        self.label = QLabel(BANNER)
        self.label.setStyleSheet("color: white; font: 13px 'Segoe UI';")
        cancel = QPushButton("✕")
        cancel.setToolTip("Cancel (Esc)")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setStyleSheet("QPushButton { background: #5f6368; color: white; border: 0; border-radius: 6px; "
                             "padding: 4px 10px; font: bold 13px 'Segoe UI'; }")
        lay.addWidget(self.label)
        lay.addWidget(cancel)
        bar.adjustSize()
        bar.move((screen.geometry().width() - bar.width()) // 2, 16)
        self.bar = bar
        cancel.clicked.connect(self.cancel)
        self._meter = QTimer(self, interval=60, timeout=self.update)
        self._meter.start()

    # --- result
    def region_image(self):
        if not self.strokes:
            return None
        rect = QRectF()
        for s in self.strokes:
            rect = rect.united(s.boundingRect())
        sx, sy = self.shot.width / self.width(), self.shot.height / self.height()
        pad = 10
        box = (max(0, int((rect.left() - pad) * sx)), max(0, int((rect.top() - pad) * sy)),
               min(self.shot.width, int((rect.right() + pad) * sx)), min(self.shot.height, int((rect.bottom() + pad) * sy)))
        if box[2] - box[0] < 12 or box[3] - box[1] < 12:
            return None
        return self.shot.crop(box)

    def send(self):
        self._finish(dict(region=self.region_image()))

    def cancel(self):
        self._finish(None)

    def _finish(self, result):
        if self._closed:
            return
        self._closed = True
        self._meter.stop()
        self.close()
        self.done.emit(result)

    # --- input
    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.send()
        elif e.key() == Qt.Key_Escape:
            self.cancel()
        elif e.key() == Qt.Key_Backspace and self.strokes:
            self.strokes.pop()
            self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.current = QPainterPath(e.position())
        elif e.button() == Qt.RightButton and self.strokes:
            self.strokes.pop()
        self.update()

    def mouseMoveEvent(self, e):
        if self.current is not None:
            self.current.lineTo(e.position())
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.current is not None:
            if self.current.boundingRect().width() > 8 or self.current.boundingRect().height() > 8:
                self.current.closeSubpath()
                self.strokes.append(self.current)
            self.current = None
            self.update()

    # --- drawing
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.drawPixmap(self.rect(), self.pixmap)
        veil = QPainterPath()
        veil.addRect(QRectF(self.rect()))
        for s in self.strokes:
            veil = veil.subtracted(s)
        p.fillPath(veil, QColor(0, 0, 0, 70 if self.strokes else 35))
        pen = QPen(QColor("#ffca28"), 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        for s in self.strokes + ([self.current] if self.current is not None else []):
            p.drawPath(s)
        # mic level meter under the banner
        lvl = min(1.0, (self.level_fn() or 0.0) * 12)
        x, y = self.bar.x(), self.bar.y() + self.bar.height() + 6
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 20, 24, 200))
        p.drawRoundedRect(QRectF(x, y, self.bar.width(), 6), 3, 3)
        p.setBrush(QColor("#e53935"))
        p.drawRoundedRect(QRectF(x, y, self.bar.width() * lvl, 6), 3, 3)

    def showEvent(self, e):
        super().showEvent(e)
        self.activateWindow()
        self.raise_()
        self.setFocus()
