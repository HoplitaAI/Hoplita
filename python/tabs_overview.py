import time

from PyQt6.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from hoplita_i18n import t
import pilot_light

_SPIN = "◐◓◑◒"
_STATE_COL = {"working": "#38bdf8", "waiting": "#f5b800",
              "ended": "#64748b", "idle": "#64748b"}


_AG_COL = {"pending": "#64748b", "running": "#f5b800", "done": "#22c55e",
           "failed": "#ef4444", "cancelled": "#7c5cff"}
_ORIGIN_COL = {"local": ("#3b82f6", "#16305e", "#93c5fd"),
               "out": ("#22c55e", "#0f3d24", "#86efac"),
               "in": ("#ef4444", "#5e1616", "#fca5a5")}
_STEP_COL = {"done": "#22c55e", "doing": "#f5b800", "failed": "#ef4444",
             "unfinished": "#a855f7"}
_MAX_AGENTS = 6


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


class _Led(QWidget):

    def __init__(self):
        super().__init__()
        self.setFixedSize(18, 18)
        self.status = "pending"

    def set_status(self, st):
        if st != self.status:
            self.status = st
            self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = QColor(pilot_light.GREEN if self.status == "running"
                     else _AG_COL.get(self.status, _AG_COL["pending"]))
        c = QRectF(4, 4, 10, 10)
        if self.status == "running":
            ph = (time.time() % pilot_light.PERIOD_S) / pilot_light.PERIOD_S
            halo = QColor(col)
            halo.setAlphaF(0.55 * (1 - ph))
            r = 5 + 4 * ph
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(halo)
            p.drawEllipse(QRectF(9 - r, 9 - r, 2 * r, 2 * r))
        elif self.status in ("done", "failed"):
            glow = QColor(col)
            glow.setAlphaF(0.3)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawEllipse(QRectF(2, 2, 14, 14))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawEllipse(c)


class _Rail(QWidget):

    def __init__(self):
        super().__init__()
        self.setFixedHeight(5)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.steps = []

    def set_steps(self, steps):
        if steps != self.steps:
            self.steps = list(steps)
            self.update()

    def paintEvent(self, _ev):
        n = len(self.steps)
        if not n:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        gap = 3.0
        w = (self.width() - gap * (n - 1)) / n
        for i, st in enumerate(self.steps):
            p.setBrush(QColor(_STEP_COL.get(st, "#2a3547")))
            p.drawRoundedRect(QRectF(i * (w + gap), 0, max(w, 1.0), 5), 2.5, 2.5)


class _AgentRow(QFrame):

    def __init__(self):
        super().__init__()
        self.setObjectName("ovwAgent")
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 6, 10, 6)
        h.setSpacing(8)
        self.led = _Led()
        h.addWidget(self.led, 0, Qt.AlignmentFlag.AlignTop)
        v = QVBoxLayout()
        v.setSpacing(3)
        self.goal = QLabel()
        self.goal.setObjectName("ovwAgGoal")
        self.goal.setTextFormat(Qt.TextFormat.PlainText)
        self.goal.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        v.addWidget(self.goal)
        self.meta = QLabel()
        self.meta.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(self.meta)
        self.rail = _Rail()
        v.addWidget(self.rail)
        self.now = QLabel()
        self.now.setObjectName("ovwAgNow")
        self.now.setTextFormat(Qt.TextFormat.PlainText)
        self.now.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        v.addWidget(self.now)
        h.addLayout(v, 1)
        self.mark = QLabel()
        self.mark.setObjectName("ovwAgMark")
        h.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignTop)
        self._goal = ""
        self._now = ""

    def _elide(self, lb, text):
        fm = lb.fontMetrics()
        lb.setText(fm.elidedText(text, Qt.TextElideMode.ElideRight,
                                 max(40, lb.width() or 260)))
        lb.setToolTip(text if len(text) > 40 else "")

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._elide(self.goal, self._goal)
        self._elide(self.now, self._now)

    def paint(self, a, spin):
        st = str(a.get("status") or "pending")
        if st not in _AG_COL:
            st = "pending"
        o = a.get("origin") if a.get("origin") in ("out", "in") else "local"
        stripe, obg, ofg = _ORIGIN_COL[o]
        border = {"running": "#f5b800", "done": "rgba(34,197,94,.55)",
                  "failed": "rgba(239,68,68,.6)"}.get(st, "#2a3547")
        self.setStyleSheet(
            f"#ovwAgent {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            f"stop:0 #1b2434, stop:1 #131a29); border:1px solid {border}; "
            f"border-left:3px solid {stripe}; border-radius:9px; }} "
            f"#ovwAgent QLabel {{ background:transparent; border:none; }} "
            f"#ovwAgGoal {{ color:#e6edf7; font-size:12px; "
            f"{'text-decoration:line-through;' if st == 'cancelled' else ''} }} "
            f"#ovwAgNow {{ color:#cbd5e1; font-size:12px; }} "
            f"#ovwAgMark {{ color:#94a3b8; font-size:13px; }}")
        self.led.set_status(st)
        self._goal = str(a.get("goal") or "")
        peer = _esc(a.get("peer") or t("ovw.remote"))
        where = "⌂" if o == "local" else ("→ " if o == "out" else "← ") + peer
        badge = ("<span style='background:{bg}; color:{fg}; font-size:12px; "
                 "font-weight:600;'>&nbsp;{txt}&nbsp;</span>")
        meta = [badge.format(bg=obg, fg=ofg, txt=where),
                badge.format(bg="#2a3346", fg="#cbd5e1",
                             txt=_esc(a.get("role") or "general")),
                badge.format(bg="#31184d", fg="#d8b4fe",
                             txt="🧠 " + _esc(a.get("model") or "base"))]
        if int(a.get("attempts") or 0) > 1:
            meta.append(f"<span style='color:#e6edf7; font-size:12px;'>"
                        f"{_esc(t('ovw.attempt', n=a['attempts']))}</span>")
        if a.get("error"):
            meta.append(f"<span style='color:#fca5a5; font-size:12px;'>"
                        f"{_esc(str(a['error'])[:60])}</span>")
        self.meta.setText(" ".join(meta))
        steps = [it for it in (a.get("intent") or []) if isinstance(it, dict)]
        self.rail.set_steps([str(it.get("status") or "") for it in steps])
        self.rail.setVisible(bool(steps))
        now = ""
        if steps:
            done = sum(1 for it in steps if it.get("status") == "done")
            cur = (next((it for it in steps if it.get("status") == "doing"), None)
                   or next((it for it in steps if not it.get("status")
                            or it.get("status") == "pending"), None))
            now = (("→ " + str(cur.get("goal") or "")) if cur
                   else "✓ " + str(steps[-1].get("goal") or ""))
            now += f"   {done}/{len(steps)}"
        self._now = now
        self.now.setVisible(bool(now))
        self._elide(self.goal, self._goal)
        self._elide(self.now, self._now)
        self.mark.setText({"running": spin, "done": "✓", "failed": "✗",
                           "cancelled": "⊘"}.get(st, "⋯"))


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
        for name in ("job", "goal", "doing", "agents_head", "last", "foot"):
            lb = QLabel()
            lb.setObjectName("ovw_" + name)
            lb.setWordWrap(True)
            lb.setTextFormat(Qt.TextFormat.RichText)
            lay.addWidget(lb)
            setattr(self, name, lb)
            if name == "agents_head":
                self.agents_box = QVBoxLayout()
                self.agents_box.setSpacing(5)
                lay.addLayout(self.agents_box)
        self.agent_rows = []

    def leds(self):
        return [r.led for r in self.agent_rows
                if not r.isHidden() and r.led.status == "running"]

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
            f"#ovw_agents_head {{ color:#c4b5fd; font-size:12px; "
            f"font-weight:600; padding-top:4px; }} "
            f"#ovw_last {{ color:#e6edf7; font-size:12px; }} "
            f"#ovw_foot {{ color:#7c8ba1; font-size:12px; }}")
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
        self._paint_agents(row.get("agents") or [], spin)
        last = " ".join(str(row.get("last") or "").split())
        if len(last) > 220:
            last = last[:220] + "…"
        self.last.setText(_esc(last) if last else
                          f"<i>{_esc(t('ovw.empty'))}</i>")
        foot = [x for x in row.get("foot") or [] if x]
        self.foot.setText(" · ".join(_esc(x) for x in foot))
        self.foot.setVisible(bool(foot))

    def _paint_agents(self, agents, spin):
        agents = [a for a in agents if isinstance(a, dict)]
        n = len(agents)
        if n:
            done = sum(1 for a in agents if a.get("status") == "done")
            run = sum(1 for a in agents if a.get("status") == "running")
            bad = sum(1 for a in agents if a.get("status") == "failed")
            head = [t("ovw.agents_head", n=n), t("ovw.agents_done", done=done, n=n)]
            if run:
                head.append(t("ovw.agents_running", n=run))
            if bad:
                head.append(t("ovw.agents_failed", n=bad))
            models = sorted({str(a.get("model") or "base") for a in agents
                             if (a.get("origin") or "local") == "local"})
            if len(models) > 1:
                head.append("🧠 " + ", ".join(models))
            self.agents_head.setText(_esc(" · ".join(head)))
        self.agents_head.setVisible(bool(n))
        order = {"running": 0, "pending": 1, "failed": 2, "done": 3, "cancelled": 4}
        shown = sorted(agents, key=lambda a: order.get(a.get("status"), 1))[:_MAX_AGENTS]
        while len(self.agent_rows) < len(shown):
            r = _AgentRow()
            self.agents_box.addWidget(r)
            self.agent_rows.append(r)
        for i, r in enumerate(self.agent_rows):
            if i < len(shown):
                r.paint(shown[i], spin)
                r.setVisible(True)
            else:
                r.setVisible(False)
        if n > len(shown):
            self.agents_head.setText(self.agents_head.text() + " · "
                                     + _esc(t("ovw.agents_more", n=n - len(shown))))


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
        self.sub.setStyleSheet("QLabel { color:#e6edf7; font-size:12px; "
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
        self._pulse = QTimer(self)
        self._pulse.setInterval(60)
        self._pulse.timeout.connect(self._pulse_leds)

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
                self.grid.addWidget(card, i // cols, i % cols,
                                    Qt.AlignmentFlag.AlignTop)
            for c in range(3):
                self.grid.setColumnStretch(c, 1 if c < cols else 0)
            last_row = (len(ids) - 1) // cols + 1 if ids else 0
            for r in range(self.grid.rowCount() + 1):
                self.grid.setRowStretch(r, 1 if r == last_row else 0)
            self._order, self._cols = ids, cols
        for r in rows:
            self.cards[r["id"]].paint(r, _SPIN[self._spin])
        if any(c.leds() for c in self.cards.values()):
            if not self._pulse.isActive():
                self._pulse.start()
        else:
            self._pulse.stop()

    def _pulse_leds(self):
        if not self.isVisible():
            self._pulse.stop()
            return
        for c in self.cards.values():
            for led in c.leds():
                led.update()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self._rows and self._columns() != self._cols:
            self.update_rows(self._rows)


def state_since(started, now=None):
    if not started:
        return None
    return (now or time.time()) - started
