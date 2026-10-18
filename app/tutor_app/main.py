"""Offline Tutor: entry point and controller.

The floating circle is the only control:
  click (idle)             -> capture mode: the circle turns red (➤), the screen is frozen for circling, mic is live
  click again (red)        -> send: speech-to-text + OCR (circled part + whole screen) -> local model -> spoken answer
  click (thinking/talking) -> interrupt and start a new question
Screen and voice are captured only in capture mode. Answers are spoken sentence by sentence; the chat window is
optional (right-click menu), and opens by itself only when speaking is turned off.
"""

import ctypes
import ctypes.wintypes
import logging
import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QCursor, QGuiApplication
from PySide6.QtWidgets import QApplication, QMenu

from tutor.knowledge import KnowledgePack

from . import config, ocr
from .audio import Recorder, Transcriber
from .bubble import Bubble
from .capture import grab_monitor_at
from .engine import Conversation, LlamaServer
from .overlay import CaptureOverlay
from .panel import ChatPanel
from .speech_text import SentenceSplitter
from .tts import Speaker

log = logging.getLogger("tutor_app")


class Bridge(QObject):
    """Signals carry results from worker threads to the UI thread."""
    hotkey = Signal()
    user_msg = Signal(str, str, object, str)
    delta = Signal(str, str)
    done = Signal(str)
    note = Signal(str)
    refresh = Signal()


def cursor_physical():
    pt = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


class Controller:
    def __init__(self, app):
        self.app, self.settings = app, config.load_settings()
        self.bridge = b = Bridge()
        self.server = LlamaServer()
        self.recorder, self.transcriber = Recorder(), Transcriber()
        self.speaker = Speaker(on_playing=lambda _: b.refresh.emit())
        self.speaker.voice, self.speaker.speed = self.settings["voice"], self.settings["speed"]
        self.speaker.hindi_voice = self.settings["hindi_voice"]
        pack = KnowledgePack.load(config.PACK) if config.PACK.exists() else None
        self.conv = Conversation(pack=pack)
        self.pool = ThreadPoolExecutor(2)      # turns; they wait on io_pool, never on pool
        self.io_pool = ThreadPoolExecutor(2)   # speech-to-text and region OCR, in parallel with full-screen OCR
        self.overlay, self.shot, self._panel_was_visible = None, None, False
        self.stop_event, self.answering = threading.Event(), False

        self.bubble, self.panel = Bubble(), ChatPanel()
        self._place_bubble()
        self.bubble.clicked.connect(self.on_circle)
        self.bubble.menu_requested.connect(self.show_menu)
        self.bubble.moved.connect(self._bubble_moved)
        self.panel.send_text.connect(self.ask_typed)
        self.panel.stop_requested.connect(self.interrupt)
        self.panel.new_chat.connect(self.conv.clear)
        b.hotkey.connect(self.on_circle)
        b.user_msg.connect(self.panel.add_user)
        b.delta.connect(self.panel.append)
        b.done.connect(self.panel.finish)
        b.note.connect(self.panel.note)
        b.refresh.connect(self.refresh)

        self.server.start()
        threading.Thread(target=self._warm_up, daemon=True).start()
        self._ready_timer = QTimer(interval=500, timeout=self._check_ready)
        self._ready_timer.start()
        self._start_hotkey()
        if pack is None:
            self.panel.note(f"Knowledge pack not found at {config.PACK}; answering without it.")
        self.bubble.show()

    # --- state shown by the circle
    def refresh(self):
        if self.server.error:
            state = "error"
        elif not self.server.ready.is_set():
            state = "loading"
        elif self.overlay is not None:
            state = "listening"
        elif self.speaker.playing:
            state = "speaking"
        elif self.answering or self.speaker.busy:
            state = "thinking"
        else:
            state = "idle"
        if state != self.bubble.state:
            self.bubble.set_state(state)
        self.panel.set_busy(self.answering or self.speaker.busy)

    # --- startup
    def _warm_up(self):
        for name, fn in (("OCR", ocr.warm_up), ("speech recognition", self.transcriber.warm_up),
                         ("voice", self.speaker.warm_up)):
            try:
                fn()
            except Exception as e:  # keep the app usable
                log.exception("%s warm-up failed", name)
                self.bridge.note.emit(f"{name} engine failed to load: {e}")

    def _check_ready(self):
        if self.server.ready.is_set() or self.server.error:
            self._ready_timer.stop()
            self.refresh()
            if self.server.error:
                self.panel.set_status("The model could not start.")
                self.panel.note(self.server.error)
                self._show_panel()
            else:
                self.panel.set_status("Ready. Click the circle to ask about your screen, or type below.")

    def _start_hotkey(self):
        combo = self.settings.get("hotkey")
        if not combo:
            return
        try:
            from pynput import keyboard
            self._hk = keyboard.GlobalHotKeys({combo: self.bridge.hotkey.emit})
            self._hk.daemon = True
            self._hk.start()
        except Exception:
            log.exception("hotkey %s could not be registered", combo)

    def _place_bubble(self):
        geo = QGuiApplication.primaryScreen().availableGeometry()
        pos = self.settings.get("bubble_pos")
        if pos and any(s.availableGeometry().contains(*pos) for s in QGuiApplication.screens()):
            self.bubble.move(*pos)
        else:
            self.bubble.move(geo.right() - 90, geo.bottom() - 160)

    def _bubble_moved(self, p):
        self.settings["bubble_pos"] = [p.x(), p.y()]
        config.save_settings(self.settings)

    def _show_panel(self):
        self.panel.place_near(self.bubble.frameGeometry(), self.bubble.screen().availableGeometry())
        self.panel.show()
        self.panel.raise_()

    def _auto_panel(self):
        """The chat window opens by itself only if answers are not spoken (or the student asked for it)."""
        return self.settings["show_chat"] or not self.settings["speak"]

    def notify(self, text, speak=False):
        self.panel.note(text)
        self.bubble.setToolTip(text)
        if speak and self.settings["speak"]:
            self.speaker.say(text)
        elif self._auto_panel():
            self._show_panel()

    # --- the circle
    def on_circle(self):
        if self.overlay is not None:          # red circle clicked again: send
            self.overlay.send()
            return
        if not self.server.ready.is_set():
            self.notify("The model is still loading. Please wait a few seconds.")
            self._show_panel()
            return
        self.interrupt()                      # a click while thinking/speaking starts a new question
        self.bubble.hide()
        panel_was_visible = self.panel.isVisible()
        self.panel.hide()
        self._panel_was_visible = panel_was_visible
        QTimer.singleShot(180, self._grab)    # let the compositor remove our windows before the screenshot

    def interrupt(self):
        self.stop_event.set()
        self.speaker.stop()
        self.refresh()

    def _grab(self):
        try:
            self.shot, _ = grab_monitor_at(*cursor_physical())
        except Exception as e:
            log.exception("screen capture failed")
            self.bubble.show()
            self.notify(f"Screen capture failed: {e}", speak=True)
            return
        mic_ok = True
        try:
            self.recorder.start()
        except Exception as e:
            log.exception("microphone failed")
            mic_ok = False
            self.notify(f"Microphone unavailable ({e}). Circle something and type your question in the chat window.")
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self.overlay = CaptureOverlay(self.shot, screen, lambda: self.recorder.level if mic_ok else 0.0)
        self.overlay.done.connect(self._capture_done)
        self.overlay.showFullScreen()
        self.refresh()
        self.bubble.show()                    # the circle stays above the overlay: clicking it again sends
        QTimer.singleShot(80, self.bubble.raise_)

    def _capture_done(self, result):
        audio = self.recorder.stop()
        self.overlay = None
        shot, self.shot = self.shot, None
        self.bubble.show()
        self.bubble.raise_()
        if self._auto_panel() or self._panel_was_visible:
            self._show_panel()
        if result is None:
            self.panel.note("Cancelled. Nothing was kept.")
            self.refresh()
            return
        self.answering = True
        self.stop_event = threading.Event()
        self.refresh()
        self.pool.submit(self._answer_activation, audio, shot, result["region"], self.stop_event)

    def _answer_activation(self, audio, shot, region_img, stop):
        mid = "m" + uuid.uuid4().hex[:8]
        try:
            f_text = self.io_pool.submit(self.transcriber.transcribe, audio)
            f_region = self.io_pool.submit(ocr.read_text, region_img) if region_img is not None else None
            full_text = ocr.read_text(shot)
            question = f_text.result()
            region_text = f_region.result() if f_region else ""
        except Exception as e:
            log.exception("speech/OCR failed")
            self._end_answer(f"Sorry, I could not read the screen or your voice: {e}")
            return
        if stop.is_set():
            self._end_answer()
            return
        if not question and region_img is None:
            self._end_answer("I didn't catch a question. Click the circle, speak, and circle what you mean.")
            return
        meta = "Screen shared" + (" · circled region" if region_img is not None else " · whole screen") + \
               f" · read {len(region_text or full_text)} characters"
        self.bridge.user_msg.emit(mid + "u", question or "*(no speech: explain the circled part)*",
                                  region_img if region_img is not None else shot, meta)
        msgs, turn = self.conv.build(question, region_text, full_text, self.settings["answer_language"])
        self._stream(mid, msgs, turn, stop)

    def ask_typed(self, text):
        if not self.server.ready.is_set():
            self.notify("The model is still loading. Please wait a few seconds.")
            return
        self.interrupt()
        mid = "m" + uuid.uuid4().hex[:8]
        self.panel.add_user(mid + "u", text)
        self.answering = True
        self.stop_event = threading.Event()
        self.refresh()
        msgs, turn = self.conv.build(text, setting=self.settings["answer_language"])
        self.pool.submit(self._stream, mid, msgs, turn, self.stop_event)

    def _stream(self, mid, msgs, turn, stop):
        speak = self.settings["speak"]
        splitter = SentenceSplitter()

        def on_delta(d):
            self.bridge.delta.emit(mid, d)
            if speak and not stop.is_set():
                for sentence in splitter.feed(d):
                    self.speaker.say(sentence, turn.lang)

        try:
            reply = self.server.stream(msgs, on_delta, stop)
            if speak and not stop.is_set():
                for sentence in splitter.flush():
                    self.speaker.say(sentence, turn.lang)
            self.conv.commit(turn, reply + (" [interrupted]" if stop.is_set() else ""))
        except Exception as e:
            log.exception("generation failed")
            self.bridge.delta.emit(mid, f"\n\n*(error: {e})*")
            if speak:
                self.speaker.say("Sorry, something went wrong while answering.")
        finally:
            self.bridge.done.emit(mid)
            self._end_answer()

    def _end_answer(self, message=None):
        if message:
            self.bridge.note.emit(message)
            if self.settings["speak"]:
                self.speaker.say(message)
        self.answering = False
        self.bridge.refresh.emit()

    # --- menu
    def show_menu(self, pos):
        m = QMenu()
        m.addAction("Show chat", self._show_panel)
        stop = m.addAction("Stop speaking", self.interrupt)
        stop.setEnabled(self.speaker.busy or self.answering)
        m.addSeparator()
        speak = QAction("Speak answers", m, checkable=True, checked=self.settings["speak"])
        speak.toggled.connect(lambda on: self._set("speak", on))
        m.addAction(speak)
        chat = QAction("Also open the chat window", m, checkable=True, checked=self.settings["show_chat"])
        chat.toggled.connect(lambda on: self._set("show_chat", on))
        m.addAction(chat)
        hindi = m.addMenu("Hinglish voice")
        hg = QActionGroup(hindi)
        for key, label in config.HINDI_VOICES.items():
            a = QAction(label, hindi, checkable=True, checked=self.settings["hindi_voice"] == key)
            a.triggered.connect(lambda _=False, k=key: self._set_hindi_voice(k))
            hg.addAction(a)
            hindi.addAction(a)
        voice = m.addMenu("English voice")
        vg = QActionGroup(voice)
        for sid, label in config.VOICES.items():
            a = QAction(label, voice, checkable=True, checked=self.settings["voice"] == sid)
            a.triggered.connect(lambda _=False, s=sid: self._set_voice(s))
            vg.addAction(a)
            voice.addAction(a)
        lang = m.addMenu("Answer language")
        lg = QActionGroup(lang)
        for key, label in (("auto", "Same as my question"), ("english", "Always English"), ("hinglish", "Always Hinglish")):
            a = QAction(label, lang, checkable=True, checked=self.settings["answer_language"] == key)
            a.triggered.connect(lambda _=False, k=key: self._set("answer_language", k))
            lg.addAction(a)
            lang.addAction(a)
        hk = self.settings.get("hotkey") or "off"
        m.addAction(f"Hotkey: {hk.replace('<', '').replace('>', '')}").setEnabled(False)
        m.addAction("Open logs folder", lambda: os.startfile(config.LOG_DIR))
        m.addSeparator()
        m.addAction("Quit", self.quit)
        m.exec(pos)

    def _set(self, key, value):
        self.settings[key] = value
        config.save_settings(self.settings)
        if key == "speak" and not value:
            self.speaker.stop()
            self.refresh()

    def _set_voice(self, sid):
        self._set("voice", sid)
        self.speaker.voice = sid
        self.speaker.say("This is my new voice.")

    def _set_hindi_voice(self, key):
        self._set("hindi_voice", key)
        self.speaker.hindi_voice = key
        self.speaker.say("Theek hai, ab Hinglish answers is awaaz mein honge. Parallel system ki reliability badh "
                         "jaati hai.", "hinglish")

    def quit(self):
        self.stop_event.set()
        self.speaker.stop()
        self.server.stop_server()
        self.app.quit()


def main():
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=config.LOG_DIR / "app.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:  # per-monitor DPI awareness (Qt 6 normally sets it already): screenshots and cursor in physical pixels
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except OSError:
        pass
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    ctrl = Controller(app)
    app.aboutToQuit.connect(ctrl.server.stop_server)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
