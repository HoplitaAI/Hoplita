import time

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

GREEN = "#3fb950"
AMBER = "#f5b800"
PERIOD_S = 1.4

PILOT_CSS = f"""
.pilot{{display:inline-block;width:10px;height:10px;border-radius:50%;
  background:{GREEN};flex-shrink:0;vertical-align:middle;
  animation:pilot {PERIOD_S}s ease-out infinite}}
.pilot.still{{background:{AMBER};animation:none}}
@keyframes pilot{{0%{{box-shadow:0 0 0 0 rgba(63,185,80,.75)}}
  70%{{box-shadow:0 0 0 9px rgba(63,185,80,0)}}
  100%{{box-shadow:0 0 0 0 rgba(63,185,80,0)}}}}
"""


class PilotLight(QWidget):

    _lit = set()
    _timer = None

    def __init__(self, parent=None, size=18):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.state = "off"
        self.hide()

    def on(self):
        self._set("on")

    def still(self):
        self._set("still")

    def off(self):
        self._set("off")

    def _set(self, st):
        if st == self.state:
            return
        self.state = st
        self.setVisible(st != "off")
        cls = type(self)
        if st == "on":
            cls._lit.add(self)
            if cls._timer is None:
                cls._timer = QTimer()
                cls._timer.setInterval(50)
                cls._timer.timeout.connect(cls._tick)
            if not cls._timer.isActive():
                cls._timer.start()
        else:
            cls._lit.discard(self)
        self.update()

    @classmethod
    def _tick(cls):
        for w in list(cls._lit):
            try:
                if w.isVisible():
                    w.update()
            except RuntimeError:
                cls._lit.discard(w)
        if not cls._lit and cls._timer is not None:
            cls._timer.stop()

    def paintEvent(self, _ev):
        if self.state == "off":
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        s = self.width()
        c = s / 2
        core = s * 0.28
        col = QColor(GREEN if self.state == "on" else AMBER)
        if self.state == "on":
            ph = (time.time() % PERIOD_S) / PERIOD_S
            halo = QColor(col)
            halo.setAlphaF(0.6 * (1 - ph))
            r = core + (c - core) * ph
            p.setBrush(halo)
            p.drawEllipse(QRectF(c - r, c - r, 2 * r, 2 * r))
        p.setBrush(col)
        p.drawEllipse(QRectF(c - core, c - core, 2 * core, 2 * core))


class PilotLine(QWidget):

    _BAND = {"on": "background:rgba(63,185,80,.10); border:1px solid rgba(63,185,80,.50);",
             "still": "background:rgba(245,184,0,.10); border:1px solid rgba(245,184,0,.55);",
             "off": "background:transparent; border:1px solid transparent;"}

    def __init__(self, parent=None, css="color:#e6edf7; font-size:14px;", hide_idle=False,
                 lines=0):
        super().__init__(parent)
        self._hide_idle = hide_idle
        self._clock = None
        self.setObjectName("pilotLine")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(8)
        self.light = PilotLight(self)
        self.label = QLabel("", self)
        self.label.setWordWrap(True)
        self.label.setStyleSheet(f"QLabel {{ {css} background:transparent; border:none; }}")
        h.addWidget(self.light, 0, Qt.AlignmentFlag.AlignTop)
        h.addWidget(self.label, 1)
        self._band("off")
        if lines:
            self.label.ensurePolished()
            self.label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            self.setFixedHeight(self.label.fontMetrics().lineSpacing() * lines + 14)
        if hide_idle:
            self.hide()

    def _band(self, st):
        self.setStyleSheet(f"#pilotLine {{ {self._BAND[st]} border-radius:8px; }}")

    def _say(self, text):
        self.label.setText(text)
        self.label.setToolTip(text)

    def working(self, text):
        self.light.on()
        self._band("on")
        self._say(text)
        self.show()

    def running(self, text):
        self._what, self._t0 = text, time.time()
        if self._clock is None:
            self._clock = QTimer(self)
            self._clock.setInterval(1000)
            self._clock.timeout.connect(self._tick_clock)
        self._clock.start()
        self._tick_clock()

    def _tick_clock(self):
        s = int(time.time() - self._t0)
        self.working(f"{self._what} · {s // 60}:{s % 60:02d}")

    def stalled(self, text):
        self.light.still()
        self._band("still")
        self._say(text)

    def done(self, text=""):
        if self._clock is not None:
            self._clock.stop()
        self.light.off()
        self._band("off")
        self._say(text)
        if self._hide_idle:
            self.setVisible(bool(text))

    def text(self):
        return self.label.text()

    def setText(self, text):
        if self.light.state == "off":
            self.done(text)
