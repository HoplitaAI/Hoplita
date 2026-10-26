import time

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QScrollArea, QVBoxLayout, QWidget)

from hoplita_i18n import t

_SPIN = "◐◓◑◒"
_STATE_COL = {"working": "#38bdf8", "waiting": "#f5b800",
              "ended": "#64748b", "idle": "#64748b"}


def ago(secs):
    secs = max(0, int(secs))
    if secs < 60:
        return t("ovw.secs", n=secs)
    if secs < 3600:
        return t("ovw.mins", n=secs // 60)
    if secs < 86400:
        return t("ovw.hours", n=secs // 3600)
    return t("ovw.days", n=secs // 86400)


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


class _Card(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, tab_id):
        super().__init__()
        self.tab_id = tab_id
        self.setObjectName("ovwCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.state = QLabel()
        self.state.setObjectName("ovwState")
        self.title = QLabel()
        self.title.setObjectName("ovwTitle")
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.when = QLabel()
        self.when.setObjectName("ovwWhen")
        head.addWidget(self.state)
        head.addWidget(self.title, 1)
        head.addWidget(self.when)
        lay.addLayout(head)
        self.lines = []
        for name in ("job", "goal", "doing", "last", "foot"):
            lb = QLabel()
            lb.setObjectName("ovw_" + name)
            lb.setWordWrap(True)
            lb.setTextFormat(Qt.TextFormat.RichText)
            lay.addWidget(lb)
            setattr(self, name, lb)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.tab_id)
        super().mouseReleaseEvent(ev)

    def paint(self, row, spin):
        st = row.get("state", "idle")
        col = row.get("color") or "#3b82f6"
        scol = _STATE_COL.get(st, "#64748b")
        self.setStyleSheet(
            f"#ovwCard {{ background:#111a2b; border:1px solid "
            f"{'#e6edf7' if row.get('active') else '#1f2a3d'}; "
            f"border-left:3px solid {col}; border-radius:8px; }} "
            f"#ovwCard:hover {{ background:#16223a; }} "
            f"#ovwCard QLabel {{ background:transparent; border:none; }} "
            f"#ovwTitle {{ color:#e6edf7; font-size:14px; font-weight:600; }} "
            f"#ovwState {{ color:{scol}; font-size:13px; font-weight:600; }} "
            f"#ovwWhen {{ color:{scol}; font-size:12px; }} "
            f"#ovw_job, #ovw_goal, #ovw_doing {{ color:#cbd5e1; font-size:12px; }} "
            f"#ovw_last {{ color:#94a3b8; font-size:12px; }} "
            f"#ovw_foot {{ color:#7c8ba1; font-size:11px; }}")
        glyph = {"working": spin, "waiting": "⏸", "ended": "⏹",
                 "idle": "·"}.get(st, "·")
        self.state.setText(f"{glyph} {t('ovw.state_' + st)}")
        self.title.setText(row.get("title", ""))
        self.when.setText(ago(row["since"]) if row.get("since") is not None else "")
        job = row.get("job") or ""
        if job:
            nxt = row.get("next_run") or ""
            self.job.setText("⏰ " + _esc(job)
                             + (" · " + _esc(t("ovw.next", when=nxt)) if nxt else ""))
        self.job.setVisible(bool(job))
        goal = row.get("goal") or ""
        self.goal.setText("🎯 " + _esc(goal[:160]))
        self.goal.setVisible(bool(goal))
        doing = row.get("doing") or ""
        self.doing.setText("→ " + _esc(doing[:160]))
        self.doing.setVisible(bool(doing))
        last = " ".join(str(row.get("last") or "").split())
        if len(last) > 220:
            last = last[:220] + "…"
        self.last.setText(_esc(last) if last else
                          f"<i>{_esc(t('ovw.empty'))}</i>")
        foot = [x for x in row.get("foot") or [] if x]
        self.foot.setText(" · ".join(_esc(x) for x in foot))
        self.foot.setVisible(bool(foot))


class TabsOverview(QWidget):
    open_tab = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("tabsOverview")
        self.setStyleSheet("#tabsOverview { background:#0b1220; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(10)
        self.head = QLabel()
        self.head.setObjectName("ovwHead")
        self.head.setStyleSheet("QLabel { color:#e6edf7; font-size:15px; "
                                "font-weight:600; background:transparent; }")
        outer.addWidget(self.head)
        self.sub = QLabel(t("ovw.sub"))
        self.sub.setWordWrap(True)
        self.sub.setStyleSheet("QLabel { color:#8296b1; font-size:12px; "
                               "background:transparent; }")
        outer.addWidget(self.sub)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background:transparent; }")
        body = QWidget()
        body.setStyleSheet("background:transparent;")
        self.grid = QGridLayout(body)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(10)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.cards = {}
        self._order = []
        self._cols = 0
        self._rows = []
        self._spin = 0

    def _columns(self):
        w = self.width() or 900
        return max(1, min(3, w // 380))

    def update_rows(self, rows):
        self._rows = list(rows)
        self._spin = (self._spin + 1) % len(_SPIN)
        n = len(rows)
        busy = sum(1 for r in rows if r.get("state") == "working")
        wait = sum(1 for r in rows if r.get("state") == "waiting")
        parts = [t("ovw.head_tabs", n=n), t("ovw.head_busy", n=busy)]
        if wait:
            parts.append(t("ovw.head_wait", n=wait))
        self.head.setText(" · ".join(parts))
        ids = [r["id"] for r in rows]
        cols = self._columns()
        if ids != self._order or cols != self._cols:
            while self.grid.count():
                self.grid.takeAt(0)
            for gone in set(self.cards) - set(ids):
                self.cards.pop(gone).deleteLater()
            for i, tid in enumerate(ids):
                card = self.cards.get(tid)
                if card is None:
                    card = _Card(tid)
                    card.clicked.connect(self.open_tab.emit)
                    self.cards[tid] = card
                self.grid.addWidget(card, i // cols, i % cols)
            for c in range(3):
                self.grid.setColumnStretch(c, 1 if c < cols else 0)
            last_row = (len(ids) - 1) // cols + 1 if ids else 0
            for r in range(self.grid.rowCount() + 1):
                self.grid.setRowStretch(r, 1 if r == last_row else 0)
            self._order, self._cols = ids, cols
        for r in rows:
            self.cards[r["id"]].paint(r, _SPIN[self._spin])

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self._rows and self._columns() != self._cols:
            self.update_rows(self._rows)


def state_since(started, now=None):
    if not started:
        return None
    return (now or time.time()) - started
