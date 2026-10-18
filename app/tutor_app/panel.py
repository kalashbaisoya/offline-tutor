"""Chat window: Markdown + LaTeX answers (offline KaTeX), a typing box, stop / new-chat buttons."""

import base64
import io
import json

from PySide6.QtCore import QTimer, QUrl, Qt, Signal
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from . import config


def thumb_data_url(img, max_w=420):
    if img is None:
        return ""
    img = img.copy()
    img.thumbnail((max_w, max_w))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


class ChatPanel(QWidget):
    send_text = Signal(str)
    stop_requested = Signal()
    new_chat = Signal()

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Offline Tutor")
        self.resize(480, 640)
        self.view = QWebEngineView()
        self.status = QLabel("Loading the model…")
        self.status.setStyleSheet("color: #6b6b76; padding: 2px 4px;")
        self.input = QLineEdit()
        self.input.setPlaceholderText("Type a question (screen is not shared when typing)…")
        send, self.stop_btn, new = QPushButton("Send"), QPushButton("Stop"), QPushButton("New chat")
        self.stop_btn.setEnabled(False)
        row = QHBoxLayout()
        for w in (self.input, send, self.stop_btn, new):
            row.addWidget(w)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self.status)
        lay.addWidget(self.view, 1)
        lay.addLayout(row)

        self.input.returnPressed.connect(self._send)
        send.clicked.connect(self._send)
        self.stop_btn.clicked.connect(self.stop_requested)
        new.clicked.connect(self._new)

        self._loaded, self._queue = False, []
        self._texts, self._dirty = {}, set()
        self.view.loadFinished.connect(self._on_loaded)
        self.view.load(QUrl.fromLocalFile(str(config.ASSETS / "chat.html")))
        self._timer = QTimer(self, interval=80, timeout=self._flush)  # throttle re-rendering while streaming
        self._timer.start()

    # --- JS bridge
    def _on_loaded(self, ok):
        self._loaded = True
        for js in self._queue:
            self.view.page().runJavaScript(js)
        self._queue.clear()

    def _js(self, fn, *args):
        js = f"{fn}({', '.join(json.dumps(a) for a in args)})"
        if self._loaded:
            self.view.page().runJavaScript(js)
        else:
            self._queue.append(js)

    def _flush(self):
        for mid in list(self._dirty):
            self._js("setAssistant", mid, self._texts[mid], False)
        self._dirty.clear()

    # --- API used by the controller
    def add_user(self, mid, text, thumb_img=None, meta=""):
        self._js("addUser", mid, text, thumb_data_url(thumb_img), meta)

    def append(self, mid, delta):
        self._texts[mid] = self._texts.get(mid, "") + delta
        self._dirty.add(mid)

    def finish(self, mid):
        self._dirty.discard(mid)
        self._js("setAssistant", mid, self._texts.get(mid, ""), True)
        self.set_busy(False)

    def note(self, text):
        self._js("note", text)

    def set_status(self, text):
        self.status.setText(text)

    def set_busy(self, busy):
        self.stop_btn.setEnabled(busy)

    def place_near(self, bubble_geom, screen_geom):
        x = bubble_geom.left() - self.width() - 12
        if x < screen_geom.left():
            x = bubble_geom.right() + 12
        y = min(max(screen_geom.top() + 8, bubble_geom.top() - self.height() // 2), screen_geom.bottom() - self.height() - 8)
        self.move(x, y)

    def _send(self):
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.send_text.emit(text)

    def _new(self):
        self._texts.clear()
        self._js("clearLog")
        self.new_chat.emit()
