import time

from PyQt6.QtCore import QEvent, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PyQt6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QScrollArea, QSizePolicy, QToolTip, QVBoxLayout,
                             QWidget)

from hoplita_i18n import t

_INK = "#e6edf7"
_RAIL = "#475569"
_CROSS = "#8796ad"
_CROSS_HOT = "#f87171"
_CROSS_BED = "#3a1d24"
_STATE_COL = {"working": "#38bdf8", "waiting": "#f5b800",
              "ended": "#64748b", "idle": "#64748b"}
_MAX_LINES = 24
_FRAME_MS = 33
_TURN_S = 1.1
_LAND_S = 0.6
_SETTLE_S = 0.45
_SLIDE_PX = 10
_PLAIN_SPIN = "◐"


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


def _ease_out(p):
    p = min(1.0, max(0.0, p))
    return 1.0 - (1.0 - p) ** 3


def _mono():
    f = QFont()
    f.setFamilies(["DejaVu Sans Mono", "Menlo", "Consolas", "monospace"])
    f.setPixelSize(12)
    return f


def _tint(colour, alpha):
    c = QColor(colour)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def _draw_arc(p, cx, cy, r, colour, now, width=1.6):
    rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
    p.setPen(QPen(_tint(colour, 0.22), width))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(rect)
    p.setPen(QPen(QColor(colour), width, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap))
    start = -int(((now % _TURN_S) / _TURN_S) * 360 * 16)
    p.drawArc(rect, start, -270 * 16)


def line_key(line):
    key = line.get("key")
    if key:
        return str(key)
    return f"{line.get('kind', '')}:{str(line.get('text') or '').split('  ')[0]}"


class _Spin(QWidget):

    def __init__(self):
        super().__init__()
        self._colour = _STATE_COL["working"]
        self._now = 0.0
        self.setFixedSize(16, 16)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def frame(self, now):
        self._now = now
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        _draw_arc(p, 8, 8, 5.0, self._colour, self._now, 1.8)
        p.end()


class _Tree(QWidget):
    stop = pyqtSignal(str, str, str)

    def __init__(self):
        super().__init__()
        self.setObjectName("ovwTree")
        self.setFont(_mono())
        self._fm = QFontMetricsF(self.font())
        self._lh = int(self._fm.height()) + 3
        self._cell = self._fm.horizontalAdvance(_PLAIN_SPIN)
        self._cross_w = self._fm.horizontalAdvance("✕") + 10
        self._space = self._fm.horizontalAdvance(" ")
        self._recs = []
        self._now = time.monotonic()
        self._hover = -1
        self._hover_x = False
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)

    def set_lines(self, lines, now):
        prev = {r["key"]: r for r in self._recs}
        born = now - _LAND_S if not prev else now
        recs = []
        dirty = []
        for ln in [x for x in lines if isinstance(x, dict)][:_MAX_LINES]:
            key = line_key(ln)
            spin = ln.get("glyph") == "spin"
            rec = dict(ln)
            rec["key"] = key
            rec["spin"] = spin
            old = prev.get(key)
            if old is None:
                rec["born"] = born
                rec["settled"] = 0.0
            else:
                rec["born"] = old["born"]
                rec["settled"] = now if (old["spin"] and not spin) else old["settled"]
            recs.append(rec)
        n_old = len(self._recs)
        for i, rec in enumerate(recs):
            old = self._recs[i] if i < n_old else None
            if old is None or any(old.get(k) != rec.get(k) for k in
                                  ("key", "prefix", "glyph", "text", "color", "cancel")):
                dirty.append(i)
        self._recs = recs
        self._now = now
        if len(recs) != n_old:
            self.updateGeometry()
            self.update()
        else:
            for i in dirty:
                self.update(self._line_rect(i))

    def records(self):
        return [{k: v for k, v in r.items() if k != "_fit"} for r in self._recs]

    def text_lines(self):
        out = []
        for r in self._recs:
            glyph = _PLAIN_SPIN if r["spin"] else str(r.get("glyph") or "")
            out.append(f"{r.get('prefix') or ''}{glyph} {r.get('text') or ''}".rstrip())
        return out

    def moving(self, now):
        for r in self._recs:
            if r["spin"] or now - r["born"] < _LAND_S or \
                    (r["settled"] and now - r["settled"] < _SETTLE_S):
                return True
        return False

    def frame(self, now):
        self._now = now
        for i, r in enumerate(self._recs):
            if r["spin"] or now - r["born"] < _LAND_S or \
                    (r["settled"] and now - r["settled"] < _SETTLE_S):
                self.update(self._line_rect(i))

    def _split(self, r, avail):
        text = str(r.get("text") or "")
        cached = r.get("_fit")
        if cached and cached[0] == avail:
            return cached[1], cached[2]
        fm = self._fm
        body, tail = (text.rsplit("  ", 1) + [""])[:2] if "  " in text else (text, "")
        room = avail - (fm.horizontalAdvance(tail) + 2 * self._space if tail else 0)
        if room < 4 * self._cell:
            body, tail, room = text, "", avail
        body = fm.elidedText(body, Qt.TextElideMode.ElideRight, max(1.0, room))
        r["_fit"] = (avail, body, tail)
        return body, tail

    def sizeHint(self):
        return QSize(120, len(self._recs) * self._lh)

    def minimumSizeHint(self):
        return QSize(0, len(self._recs) * self._lh)

    def _line_rect(self, i):
        return QRect(0, i * self._lh, self.width(), self._lh)

    def line_at(self, pos):
        i = int(pos.y()) // self._lh if self._lh else -1
        return i if 0 <= i < len(self._recs) else -1

    def cross_rect(self, i):
        if not (0 <= i < len(self._recs)) or not self._recs[i].get("cancel"):
            return QRect()
        return QRect(int(self.width() - self._cross_w), i * self._lh,
                     int(self._cross_w), self._lh)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setFont(self.font())
        now = self._now
        region = ev.region()
        w = self.width()
        lh = self._lh
        fm = self._fm
        for i, r in enumerate(self._recs):
            y = i * lh
            if not region.intersects(QRect(0, y, w, lh)):
                continue
            colour = r.get("color") or "#cbd5e1"
            land = _ease_out((now - r["born"]) / _LAND_S)
            settle = _ease_out((now - r["settled"]) / _SETTLE_S) if r["settled"] else 1.0
            if land < 1.0:
                p.fillRect(QRectF(0, y, w, lh), _tint(colour, 0.20 * (1.0 - land)))
            elif settle < 1.0:
                p.fillRect(QRectF(0, y, w, lh), _tint(colour, 0.14 * (1.0 - settle)))
            x = _SLIDE_PX * (1.0 - land)
            prefix = str(r.get("prefix") or "")
            if prefix:
                p.setPen(QColor(_RAIL))
                p.drawText(QRectF(x, y, w, lh), Qt.AlignmentFlag.AlignVCenter, prefix)
                x += fm.horizontalAdvance(prefix)
            gx = x
            if r["spin"]:
                _draw_arc(p, gx + self._cell / 2.0, y + lh / 2.0, 4.6, colour, now)
            else:
                glyph = str(r.get("glyph") or "")
                if glyph:
                    p.setPen(QColor(colour))
                    if settle < 1.0:
                        s = 1.0 + 0.6 * (1.0 - settle)
                        p.save()
                        p.translate(gx + self._cell / 2.0, y + lh / 2.0)
                        p.scale(s, s)
                        p.drawText(QRectF(-self._cell / 2.0, -lh / 2.0, self._cell * 2, lh),
                                   Qt.AlignmentFlag.AlignVCenter, glyph)
                        p.restore()
                    else:
                        p.drawText(QRectF(gx, y, w, lh), Qt.AlignmentFlag.AlignVCenter, glyph)
            x = gx + max(self._cell, fm.horizontalAdvance(
                _PLAIN_SPIN if r["spin"] else str(r.get("glyph") or ""))) + self._space
            right = w - (self._cross_w if r.get("cancel") else 2)
            body, tail = self._split(r, right - x)
            p.setPen(QColor(colour))
            if tail:
                tw = fm.horizontalAdvance(tail)
                p.drawText(QRectF(right - tw, y, tw, lh), Qt.AlignmentFlag.AlignVCenter, tail)
                right -= tw + 2 * self._space
            p.drawText(QRectF(x, y, max(1.0, right - x), lh),
                       Qt.AlignmentFlag.AlignVCenter, body)
            if r.get("cancel"):
                hot = i == self._hover and self._hover_x
                cr = self.cross_rect(i)
                if hot:
                    d = QRectF(cr.center().x() - 9, cr.center().y() - 9, 18, 18)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(_CROSS_BED))
                    p.drawEllipse(d)
                p.setPen(QColor(_CROSS_HOT if hot else _CROSS))
                p.drawText(QRectF(cr), Qt.AlignmentFlag.AlignCenter, "✕")
        p.end()

    def _hit(self, pos):
        i = self.line_at(pos)
        return i, (i >= 0 and self.cross_rect(i).contains(pos))

    def mouseMoveEvent(self, ev):
        i, on_x = self._hit(ev.position().toPoint())
        if (i, on_x) != (self._hover, self._hover_x):
            for j in (self._hover, i):
                if j >= 0:
                    self.update(self._line_rect(j))
            self._hover, self._hover_x = i, on_x
        ev.ignore()

    def leaveEvent(self, ev):
        if self._hover >= 0:
            self.update(self._line_rect(self._hover))
        self._hover, self._hover_x = -1, False
        super().leaveEvent(ev)

    def mousePressEvent(self, ev):
        _i, on_x = self._hit(ev.position().toPoint())
        if on_x and ev.button() == Qt.MouseButton.LeftButton:
            ev.accept()
        else:
            ev.ignore()

    def mouseReleaseEvent(self, ev):
        i, on_x = self._hit(ev.position().toPoint())
        if on_x and ev.button() == Qt.MouseButton.LeftButton:
            cancel = self._recs[i].get("cancel") or []
            if len(cancel) >= 2:
                self.stop.emit(str(cancel[0]), str(cancel[1]),
                               str(cancel[2]) if len(cancel) > 2 else "")
            ev.accept()
        else:
            ev.ignore()

    def event(self, ev):
        if ev.type() == QEvent.Type.ToolTip:
            i, _on_x = self._hit(ev.pos())
            tip = str(self._recs[i].get("tip") or "") if i >= 0 else ""
            if tip and tip != str(self._recs[i].get("text") or ""):
                QToolTip.showText(ev.globalPos(), tip, self, self._line_rect(i))
            else:
                QToolTip.hideText()
            return True
        return super().event(ev)


class _Card(QFrame):
    clicked = pyqtSignal(str)
    stop = pyqtSignal(str, str, str)

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
        self.spin = _Spin()
        self.state = QLabel()
        self.state.setObjectName("ovwState")
        self.title = QLabel()
        self.title.setObjectName("ovwTitle")
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.when = QLabel()
        self.when.setObjectName("ovwWhen")
        head.addWidget(self.spin)
        head.addWidget(self.state)
        head.addWidget(self.title, 1)
        head.addWidget(self.when)
        lay.addLayout(head)
        for name in ("job", "goal"):
            lb = QLabel()
            lb.setObjectName("ovw_" + name)
            lb.setWordWrap(True)
            lb.setTextFormat(Qt.TextFormat.RichText)
            lay.addWidget(lb)
            setattr(self, name, lb)
        self.tree = _Tree()
        self.tree.stop.connect(self.stop.emit)
        lay.addSpacing(4)
        lay.addWidget(self.tree)
        lay.addSpacing(4)
        for name in ("last", "foot"):
            lb = QLabel()
            lb.setObjectName("ovw_" + name)
            lb.setWordWrap(True)
            lb.setTextFormat(Qt.TextFormat.RichText)
            lay.addWidget(lb)
            setattr(self, name, lb)
        self._skin = None
        self._state = "idle"
        self._texts = {}

    def _set(self, label, text):
        if self._texts.get(label) != text:
            self._texts[label] = text
            label.setText(text)

    def moving(self, now):
        return self._state == "working" or self.tree.moving(now)

    def frame(self, now):
        if self._state == "working":
            self.spin.frame(now)
        self.tree.frame(now)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.tab_id)
        super().mouseReleaseEvent(ev)

    def paint(self, row, now):
        st = row.get("state", "idle")
        col = row.get("color") or "#3b82f6"
        scol = _STATE_COL.get(st, "#64748b")
        skin = (st, col, bool(row.get("active")))
        if skin != self._skin:
            self._skin = skin
            self.setStyleSheet(
                f"#ovwCard {{ background:#111a2b; border:1px solid "
                f"{_INK if row.get('active') else '#1f2a3d'}; "
                f"border-left:3px solid {col}; border-radius:8px; }} "
                f"#ovwCard:hover {{ background:#16223a; }} "
                f"#ovwCard QLabel {{ background:transparent; border:none; }} "
                f"#ovwTitle {{ color:{_INK}; font-size:14px; font-weight:600; }} "
                f"#ovwState {{ color:{scol}; font-size:13px; font-weight:600; }} "
                f"#ovwWhen {{ color:{scol}; font-size:12px; }} "
                f"#ovw_job, #ovw_goal {{ color:#cbd5e1; font-size:12px; }} "
                f"#ovw_last {{ color:{_INK}; font-size:12px; }} "
                f"#ovw_foot {{ color:#7c8ba1; font-size:12px; }}")
        self._state = st
        self.spin.setVisible(st == "working")
        glyph = {"waiting": "⏸ ", "ended": "⏹ ", "idle": "· "}.get(st, "")
        self._set(self.state, f"{glyph}{t('ovw.state_' + st)}")
        self._set(self.title, row.get("title", ""))
        self._set(self.when, ago(row["since"]) if row.get("since") is not None else "")
        job = row.get("job") or ""
        if job:
            nxt = row.get("next_run") or ""
            self._set(self.job, "⏰ " + _esc(job)
                      + (" · " + _esc(t("ovw.next", when=nxt)) if nxt else ""))
        self.job.setVisible(bool(job))
        goal = row.get("goal") or ""
        self._set(self.goal, "🎯 " + _esc(goal[:160]))
        self.goal.setVisible(bool(goal))
        lines = row.get("tree") or []
        self.tree.set_lines(lines, now)
        self.tree.setVisible(bool(lines))
        last = " ".join(str(row.get("last") or "").split())
        if len(last) > 220:
            last = last[:220] + "…"
        self._set(self.last, _esc(last) if last else
                  f"<i>{_esc(t('ovw.empty'))}</i>")
        foot = [x for x in row.get("foot") or [] if x]
        self._set(self.foot, " · ".join(_esc(x) for x in foot))
        self.foot.setVisible(bool(foot))

    def tree_text(self):
        return self.tree.text_lines()


class TabsOverview(QWidget):
    open_tab = pyqtSignal(str)
    stop_turn = pyqtSignal(str)
    stop_job = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("tabsOverview")
        self.setStyleSheet("#tabsOverview { background:#0b1220; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(10)
        self.head = QLabel()
        self.head.setObjectName("ovwHead")
        self.head.setStyleSheet(f"QLabel {{ color:{_INK}; font-size:15px; "
                                "font-weight:600; background:transparent; }")
        outer.addWidget(self.head)
        self.sub = QLabel(t("ovw.sub"))
        self.sub.setWordWrap(True)
        self.sub.setStyleSheet(f"QLabel {{ color:{_INK}; font-size:12px; "
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
        self._head = ""
        self._pulse = QTimer(self)
        self._pulse.setInterval(_FRAME_MS)
        self._pulse.timeout.connect(self._frame)

    def _columns(self):
        w = self.width() or 900
        return max(1, min(3, w // 380))

    def _on_stop(self, kind, tab_id, job_id):
        if kind == "turn":
            self.stop_turn.emit(tab_id)
        elif kind == "job" and job_id:
            self.stop_job.emit(tab_id, job_id)

    def update_rows(self, rows):
        self._rows = list(rows)
        now = time.monotonic()
        n = len(rows)
        busy = sum(1 for r in rows if r.get("state") == "working")
        wait = sum(1 for r in rows if r.get("state") == "waiting")
        parts = [t("ovw.head_tabs", n=n), t("ovw.head_busy", n=busy)]
        if wait:
            parts.append(t("ovw.head_wait", n=wait))
        head = " · ".join(parts)
        if head != self._head:
            self._head = head
            self.head.setText(head)
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
                    card.stop.connect(self._on_stop)
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
            self.cards[r["id"]].paint(r, now)
        if any(c.moving(now) for c in self.cards.values()):
            if not self._pulse.isActive():
                self._pulse.start()
        else:
            self._pulse.stop()

    def _frame(self):
        if not self.isVisible():
            self._pulse.stop()
            return
        now = time.monotonic()
        alive = False
        for c in self.cards.values():
            if c.moving(now):
                alive = True
                c.frame(now)
        if not alive:
            self._pulse.stop()

    def tree_text(self, tab_id):
        c = self.cards.get(tab_id)
        return c.tree_text() if c is not None else []

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self._rows and self._columns() != self._cols:
            self.update_rows(self._rows)


def state_since(started, now=None):
    if not started:
        return None
    return (now or time.time()) - started
