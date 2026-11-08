import json
import os

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QWidget
from PyQt6.QtWebEngineWidgets import QWebEngineView

_PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "calendar_view", "index.html")

_KEYS = {
    "months": "cal.months", "months_in_date": "cal.months_in_date",
    "weekdays": "cal.weekdays",
    "weekdays_long": "cal.weekdays_long",
    "month": "cal.month", "week": "cal.week", "agenda": "cal.agenda",
    "today": "cal.today", "go_today": "cal.go_today",
    "today_prefix": "cal.today_prefix",
    "all": "cal.all", "all_projects": "cal.all_projects", "more": "cal.more",
    "none_today": "cal.none_today", "empty": "cal.empty",
    "new_here": "cal.new_here",
    "month_year": "cal.month_year", "week_same": "cal.week_same",
    "week_cross": "cal.week_cross", "day_long": "cal.day_long",
    "prev": "cal.prev", "next": "cal.next",
    "new": "todo.new_apt", "pending": "fed.pending_appts",
    "pending_tip": "fed.pending_appts_tip",
    "edit": "menu.edit", "delete": "menu.delete",
    "u_low": "urgency.low", "u_normal": "urgency.normal",
    "u_high": "urgency.high", "u_crit": "urgency.critical",
}


def labels(t):
    out = {}
    for name, key in _KEYS.items():
        try:
            out[name] = t(key)
        except Exception:
            out[name] = ""
    return out


class CalendarWidget(QWidget):

    action = pyqtSignal(dict)

    def __init__(self, parent=None):
        if not os.path.isfile(_PAGE):
            raise FileNotFoundError(_PAGE)
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = QWebEngineView()
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        try:
            from PyQt6.QtGui import QColor
            self.view.page().setBackgroundColor(QColor("#0b1220"))
        except Exception:
            pass
        self.view.setStyleSheet("background:#0b1220;")
        lay.addWidget(self.view)
        self._ready = False
        self._labels = None
        self._state = None
        self._pending = None
        self._mode = None
        self._polling = False
        self._poll = QTimer(self)
        self._poll.setInterval(300)
        self._poll.timeout.connect(self._take)
        self.view.loadFinished.connect(self._on_loaded)
        self.view.load(QUrl.fromLocalFile(_PAGE))

    def _js(self, code, cb=None):
        if not self._ready:
            return
        if cb is None:
            self.view.page().runJavaScript(code)
        else:
            self.view.page().runJavaScript(code, cb)

    def _on_loaded(self, ok):
        self._ready = bool(ok)
        if self._labels is not None:
            self._js("setLabels(" + json.dumps(self._labels) + ");")
        if self._state is not None:
            self._js("calState(" + json.dumps(self._state) + ");")
        if self._pending is not None:
            self._js("calPending(%d);" % self._pending)
        if self._mode is not None:
            self._js("calShow(" + json.dumps(self._mode) + ");")

    def set_labels(self, lab):
        self._labels = json.dumps(lab, ensure_ascii=False)
        self._js("setLabels(" + json.dumps(self._labels) + ");")

    def set_state(self, payload):
        try:
            self._state = json.dumps(payload, ensure_ascii=False, default=str)
        except Exception:
            return
        self._js("calState(" + json.dumps(self._state) + ");")

    def set_pending(self, n):
        self._pending = int(n or 0)
        self._js("calPending(%d);" % self._pending)

    def show_mode(self, name):
        self._mode = str(name)
        self._js("calShow(" + json.dumps(self._mode) + ");")

    def showEvent(self, e):
        super().showEvent(e)
        self._poll.start()

    def hideEvent(self, e):
        self._poll.stop()
        super().hideEvent(e)

    def _take(self):
        if not self._ready or self._polling:
            return
        self._polling = True
        self._js("calTake();", self._got)

    def _got(self, raw):
        self._polling = False
        if not raw or raw == "[]":
            return
        try:
            batch = json.loads(raw)
        except Exception:
            return
        for a in batch if isinstance(batch, list) else []:
            if isinstance(a, dict) and a.get("op"):
                self.action.emit(a)
