import json
import os

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QWidget
from PyQt6.QtWebEngineWidgets import QWebEngineView

_PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "council_view", "index.html")

_KEYS = {
    "title": "council.title", "subtitle": "council.subtitle",
    "how_1": "council.how_1", "how_2": "council.how_2",
    "how_3": "council.how_3", "how_4": "council.how_4",
    "how_ask": "council.how_ask", "the_matter": "council.the_matter",
    "final": "council.v_final",
    "ph_answer": "council.ph_answer", "ph_critique": "council.ph_critique",
    "ph_reform": "council.ph_reform", "ph_vote": "council.ph_vote",
    "stage_answer": "council.v_stage_answer",
    "stage_critique": "council.v_stage_critique",
    "stage_reform": "council.v_stage_reform",
    "stage_vote": "council.v_stage_vote",
    "round": "council.v_round", "deliberation": "council.v_deliberation",
    "placet": "council.vote_placet", "against": "council.vote_no",
    "unread": "council.vote_unreadable",
    "verdict_placet": "council.v_verdict_placet",
    "verdict_split": "council.v_verdict_split",
    "verdict_sub": "council.v_verdict_sub",
    "more": "council.v_more", "less": "council.v_less",
    "activity": "council.v_activity",
    "briefing": "council.briefing_head", "briefing_none": "council.briefing_none",
    "speaking": "council.v_speaking",
    "produced_nothing": "council.produced_nothing",
    "local": "council.v_local", "cloud": "council.v_cloud",
    "opens": "council.v_opens",
    "status_live": "council.v_status_live",
    "status_closed": "council.v_status_closed",
    "convening": "council.v_convening", "cancelled": "council.cancelled",
    "bench_hint": "council.v_bench_hint", "bench_back_hint": "council.v_bench_back_hint",
    "bench_count": "council.v_bench_count", "make_first": "council.v_make_first",
    "pool_empty": "council.pool_empty", "carry": "council.carry_context",
    "to_bench": "council.v_to_bench", "to_session": "council.v_to_session",
}


def labels(t):
    out = {}
    for name, key in _KEYS.items():
        try:
            out[name] = t(key)
        except Exception:
            out[name] = ""
    return out


class CouncilWidget(QWidget):

    _push_sig = pyqtSignal(str)
    _load_sig = pyqtSignal(str)
    bench_changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        if not os.path.isfile(_PAGE):
            raise FileNotFoundError(_PAGE)
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = QWebEngineView()
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.view.page().setBackgroundColor(Qt.GlobalColor.transparent)
        lay.addWidget(self.view)
        self._ready = False
        self._labels = None
        self._events = []
        self._bench = None
        self._poll = QTimer(self)
        self._poll.setInterval(250)
        self._poll.timeout.connect(self._take_bench)
        self.view.loadFinished.connect(self._on_loaded)
        self._push_sig.connect(self._apply_push)
        self._load_sig.connect(self._apply_load)
        self.view.load(QUrl.fromLocalFile(_PAGE))

    def _js(self, code):
        if self._ready:
            self.view.page().runJavaScript(code)

    def _on_loaded(self, ok):
        self._ready = bool(ok)
        if self._labels is not None:
            self._js("setLabels(" + json.dumps(self._labels) + ");")
        if self._bench is not None:
            self._js("councilBench(" + json.dumps(self._bench) + ");")
        self._js("councilLoad(" + json.dumps(json.dumps(self._events, ensure_ascii=False)) + ");")

    def set_labels(self, lab):
        self._labels = json.dumps(lab, ensure_ascii=False)
        self._js("setLabels(" + json.dumps(self._labels) + ");")

    def load(self, events):
        self._load_sig.emit(json.dumps(list(events or []), ensure_ascii=False))

    def push(self, ev):
        self._push_sig.emit(json.dumps(dict(ev or {}), ensure_ascii=False))

    def _apply_load(self, payload):
        self._events = json.loads(payload)
        self._js("councilLoad(" + json.dumps(payload) + ");")

    def _apply_push(self, payload):
        self._events.append(json.loads(payload))
        self._js("councilPush(" + json.dumps(payload) + ");")

    def set_bench(self, bench):
        self._bench = json.dumps(bench, ensure_ascii=False)
        self._js("councilBench(" + json.dumps(self._bench) + ");")

    def showEvent(self, e):
        super().showEvent(e)
        self._poll.start()

    def hideEvent(self, e):
        self._take_bench()
        self._poll.stop()
        super().hideEvent(e)

    def _take_bench(self):
        if self._ready:
            self.view.page().runJavaScript("councilTakeBench()", self._got_bench)

    def _got_bench(self, out):
        if not out:
            return
        try:
            self.bench_changed.emit(dict(json.loads(out)))
        except Exception:
            pass
