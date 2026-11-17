import math
import time

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import (QColor, QFont, QFontMetricsF, QPainter, QPainterPath,
                         QPen)
from PyQt6.QtWidgets import QSizePolicy, QWidget

from hoplita_i18n import t

INK = "#e6edf7"
RAIL = "#2a3547"
BED = "#0b1220"
PANEL = "#111a2b"
RUN = "#38bdf8"
FAIL = "#fca5a5"
AGENT = "#f5b800"
MODEL = "#c4b5fd"
MAX_SLOTS = 12
BELT_KEEP = 40
BOX_H = 24.0
ROW_H = 30.0
FRAME_MS = 33
EASE_S = 0.35
MODEL_SLOT = "model"


def _ease(p):
    p = min(1.0, max(0.0, p))
    return 1.0 - (1.0 - p) ** 3


def _tint(colour, alpha):
    c = QColor(colour)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def _clock(secs):
    secs = max(0.0, float(secs))
    if secs < 60:
        return f"{secs:.1f} s" if secs < 10 else f"{int(secs)} s"
    if secs < 3600:
        return f"{int(secs // 60)} min {int(secs % 60):02d} s"
    return f"{int(secs // 3600)} h {int((secs % 3600) // 60):02d} min"


class Machine:

    def __init__(self):
        self.slots = {}
        self.belt = []
        self.seq = 0
        self.running = []
        self.thinking = False
        self.think_t0 = 0.0
        self.model = ""
        self.round = (0, 0)
        self.turn_t0 = 0.0
        self.turn_calls = 0
        self.alive = False
        self.thoughts = 0
        self.touched = 0.0
        self._add_slot(MODEL_SLOT)

    def _add_slot(self, name):
        if name not in self.slots:
            self.slots[name] = {"count": 0, "fails": 0, "time": 0.0,
                                "last_args": "", "last_dur": None, "first": len(self.slots)}
        return self.slots[name]

    def event(self, ev, now=None):
        now = now or time.time()
        kind = ev.get("kind")
        self.touched = now
        if kind == "turn":
            self.alive = True
            self.turn_t0 = now
            self.turn_calls = 0
            self.round = (0, 0)
            self.thoughts = 0
        elif kind == "end":
            self.alive = False
            self.thinking = False
            for seq in list(self.running):
                self._close(seq, None, now, now - self._call(seq)["t0"])
        elif kind == "round":
            self.round = (int(ev.get("n") or 0), int(ev.get("max") or 0))
        elif kind == "model":
            self.thinking = True
            self.think_t0 = now
            self.model = str(ev.get("model") or self.model or "")
            self.slots[MODEL_SLOT]["count"] += 1
        elif kind == "model_done":
            self.thinking = False
            d = float(ev.get("duration") or (now - self.think_t0))
            s = self.slots[MODEL_SLOT]
            s["time"] += d
            s["last_dur"] = d
        elif kind == "thought":
            self.thoughts += 1
        elif kind == "call":
            tool = str(ev.get("tool") or "?")
            self.seq += 1
            call = {"seq": self.seq, "tool": tool, "args": str(ev.get("args") or "")[:200],
                    "agent": str(ev.get("agent") or ""), "t0": now, "t1": None,
                    "ok": None, "dur": 0.0}
            self.belt.append(call)
            del self.belt[:-BELT_KEEP]
            self.running.append(self.seq)
            s = self._add_slot(tool)
            s["count"] += 1
            s["last_args"] = call["args"]
            self.turn_calls += 1
        elif kind == "result":
            tool = str(ev.get("tool") or "?")
            agent = str(ev.get("agent") or "")
            seq = next((q for q in reversed(self.running)
                        if self._call(q) and self._call(q)["tool"] == tool
                        and self._call(q)["agent"] == agent), None)
            if seq is None:
                seq = next((q for q in reversed(self.running)
                            if self._call(q) and self._call(q)["tool"] == tool), None)
            if seq is not None:
                ok = ev.get("ok")
                ok = True if ok is None else bool(ok)
                dur = ev.get("duration")
                dur = float(dur) if dur is not None else now - self._call(seq)["t0"]
                self._close(seq, ok, now, dur)

    def _call(self, seq):
        return next((c for c in self.belt if c["seq"] == seq), None)

    def _close(self, seq, ok, now, dur):
        c = self._call(seq)
        if seq in self.running:
            self.running.remove(seq)
        if c is None:
            return
        c["t1"] = now
        c["ok"] = ok
        c["dur"] = max(0.0, float(dur))
        s = self._add_slot(c["tool"])
        s["time"] += c["dur"]
        s["last_dur"] = c["dur"]
        if ok is False:
            s["fails"] += 1

    def current(self):
        if self.running:
            c = self._call(self.running[-1])
            if c is not None:
                return c["tool"]
        return MODEL_SLOT

    def slot_names(self):
        names = sorted(self.slots, key=lambda n: self.slots[n]["first"])
        return names[:MAX_SLOTS]

    def busy(self):
        return bool(self.running) or self.thinking

    def totals(self):
        n = len(self.belt)
        fails = sum(1 for c in self.belt if c["ok"] is False)
        secs = sum(c["dur"] for c in self.belt if c["t1"] is not None)
        return n, fails, secs


class MachineView(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("machineView")
        self.setMinimumWidth(300)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.setAutoFillBackground(False)
        self._m = Machine()
        self._now = time.time()
        self._needle = 0.0
        self._needle_from = 0.0
        self._needle_to = 0.0
        self._needle_t0 = 0.0
        self._target = MODEL_SLOT
        self._boxes = {}
        self._rows = 0
        self._lit = {}
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._frame)
        self._ui = QFont()
        self._ui.setPixelSize(12)
        self._big = QFont()
        self._big.setPixelSize(17)
        self._big.setWeight(QFont.Weight.DemiBold)
        self._mono = QFont()
        self._mono.setFamilies(["DejaVu Sans Mono", "Menlo", "Consolas", "monospace"])
        self._mono.setPixelSize(11)
        self._small = QFont()
        self._small.setPixelSize(10)
        self._fm_small = QFontMetricsF(self._small)

    def set_machine(self, m):
        if m is not self._m:
            self._m = m
            self._boxes = {}
            self._lit = {}
            self._needle = self._needle_to = self._slot_angle(self._m.current())
            self._needle_t0 = 0.0
        self.poke()

    def machine(self):
        return self._m

    def poke(self):
        now = time.time()
        self._now = now
        target = self._m.current()
        if target != self._target:
            self._aim(target, now)
            self._lit[target] = now
        elif self._m.busy():
            self._lit[target] = now
        self._place(now)
        if self.isVisible() and not self._timer.isActive() and self._moving(now):
            self._timer.start()
        self.update()

    def sizeHint(self):
        return QSize(380, 520)

    def showEvent(self, ev):
        super().showEvent(ev)
        self.poke()

    def hideEvent(self, ev):
        super().hideEvent(ev)
        self._timer.stop()

    def _slot_angle(self, name):
        names = self._m.slot_names()
        n = len(names)
        i = names.index(name) if name in names else 0
        if n <= 1:
            return 270.0
        return 135.0 + 270.0 * i / (n - 1)

    def _aim(self, target, now):
        self._target = target
        self._needle_from = self._needle
        self._needle_to = self._slot_angle(target)
        self._needle_t0 = now

    def items(self, now=None):
        return [{"seq": c["seq"], "tool": c["tool"], "agent": c["agent"], "t0": c["t0"],
                 "t1": c["t1"], "ok": c["ok"], "dur": c["dur"], "n": 1}
                for c in self._m.belt]

    def _place(self, now):
        w = self.width()
        left, right = 14.0, w - 14.0
        x, row = left, 0
        seen = set()
        items = self.items(now)
        for c in items:
            bw = self._box_w(c, now)
            if x > left and x + bw > right:
                x, row = left, row + 1
            seen.add(c["seq"])
            b = self._boxes.get(c["seq"])
            if b is None:
                self._boxes[c["seq"]] = {"x": float(w), "x_from": float(w), "x_to": x,
                                         "t0": now, "row": row}
            else:
                b["row"] = row
                if abs(b["x_to"] - x) > 4:
                    b["x_from"], b["x_to"], b["t0"] = b["x"], x, now
                else:
                    b["x_to"] = x
                    if now - b["t0"] >= EASE_S:
                        b["x"] = x
            x += bw + 6
        self._rows = row + 1 if items else 0
        for seq in [s for s in self._boxes if s not in seen]:
            del self._boxes[seq]

    def _box_label(self, c):
        return c["tool"]

    def _box_w(self, c, now):
        d = c["dur"] if c["t1"] is not None else now - c["t0"]
        by_time = min(120.0, 48.0 + 14.0 * math.log2(1.0 + max(0.0, d)))
        return max(self._fm_small.horizontalAdvance(self._box_label(c)) + 14.0, by_time)

    def _moving(self, now):
        if self._m.busy():
            return True
        if now - self._needle_t0 < EASE_S:
            return True
        for b in self._boxes.values():
            if now - b["t0"] < EASE_S:
                return True
        if any(now - t < EASE_S for t in self._lit.values()):
            return True
        return False

    def _frame(self):
        now = time.time()
        self._now = now
        target = self._m.current()
        if target != self._target:
            self._aim(target, now)
            self._lit[target] = now
        self._place(now)
        p = _ease((now - self._needle_t0) / EASE_S)
        self._needle = self._needle_from + (self._needle_to - self._needle_from) * p
        for b in self._boxes.values():
            q = _ease((now - b["t0"]) / EASE_S)
            b["x"] = b["x_from"] + (b["x_to"] - b["x_from"]) * q
        if not self._moving(now):
            self._needle = self._needle_to
            self._timer.stop()
        self.update()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._place(self._now)
        for b in self._boxes.values():
            b["x"] = b["x_to"]
            b["t0"] = 0.0

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.fillRect(self.rect(), QColor(BED))
        w, h = self.width(), self.height()
        now = self._now
        self._place(now)
        belt_h = min(h * 0.45, 56 + max(1, self._rows) * ROW_H)
        self._paint_dial(p, QRectF(0, 0, w, h - belt_h), now)
        self._paint_belt(p, QRectF(0, h - belt_h, w, belt_h), now)
        p.end()

    def _paint_dial(self, p, area, now):
        m = self._m
        names = m.slot_names()
        cx = area.center().x()
        font = QFont(self._ui)

        def _longest(f):
            fm_ = QFontMetricsF(f)
            return max((fm_.horizontalAdvance(t("machine.model_slot") if n == MODEL_SLOT else n)
                        + fm_.horizontalAdvance(f"×{m.slots[n]['count']}") + 6
                        for n in names), default=60.0)
        longest = _longest(font)
        want = area.width() / 2.0 - 52 - 30
        if longest > want > 0:
            font.setPixelSize(max(9, int(12 * want / longest)))
            longest = _longest(font)
        fm = QFontMetricsF(font)
        r = max(52.0, min(area.width() / 2.0 - longest - 30.0, (area.height() - 170) / 2.0))
        cy = area.top() + 36 + r
        rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
        p.setPen(QPen(QColor(RAIL), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(rect, int(-135 * 16), int(-270 * 16))
        p.setFont(font)
        for name in names:
            a = math.radians(self._slot_angle(name))
            lit = now - self._lit.get(name, 0.0) < 1.2
            run = name == m.current() and m.busy()
            col = QColor(RUN if run else INK)
            p.setPen(QPen(col, 2.5 if run else 1.5))
            p.drawLine(QPointF(cx + math.cos(a) * (r - 7), cy + math.sin(a) * (r - 7)),
                       QPointF(cx + math.cos(a) * (r + 7), cy + math.sin(a) * (r + 7)))
            s = m.slots[name]
            label = t("machine.model_slot") if name == MODEL_SLOT else name
            count = f"×{s['count']}" if s["count"] else ""
            cw = fm.horizontalAdvance(count) + (6 if count else 0)
            lw = fm.horizontalAdvance(label)
            lx = cx + math.cos(a) * (r + 16)
            ly = cy + math.sin(a) * (r + 16)
            if math.cos(a) < -0.3:
                x0 = lx - lw - cw
            elif math.cos(a) > 0.3:
                x0 = lx
            else:
                x0 = lx - (lw + cw) / 2.0
            x0 = min(max(x0, area.left() + 8), area.right() - 8 - lw - cw)
            y0 = ly - fm.height() / 2.0
            if lit or run:
                pad = QRectF(x0 - 6, y0 - 2, lw + cw + 12, fm.height() + 4)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_tint(RUN if run else INK, 0.18 if run else 0.10))
                p.drawRoundedRect(pad, 6, 6)
            p.setPen(QColor(INK))
            p.drawText(QRectF(x0, y0, lw + 2, fm.height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
            if count:
                p.setPen(QColor(RUN if run else MODEL if name == MODEL_SLOT else INK))
                p.drawText(QRectF(x0 + lw + 6, y0, cw, fm.height()),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, count)
        fm = QFontMetricsF(self._ui)
        a = math.radians(self._needle)
        busy = m.busy()
        col = QColor(RUN if busy else INK)
        p.setPen(QPen(_tint(col, 0.35), 1.5))
        p.drawLine(QPointF(cx, cy), QPointF(cx + math.cos(a) * (r - 22), cy + math.sin(a) * (r - 22)))
        path = QPainterPath()
        tipx, tipy = cx + math.cos(a) * (r + 1), cy + math.sin(a) * (r + 1)
        bx, by = cx + math.cos(a) * (r - 24), cy + math.sin(a) * (r - 24)
        nx, ny = -math.sin(a) * 5, math.cos(a) * 5
        path.moveTo(tipx, tipy)
        path.lineTo(bx + nx, by + ny)
        path.lineTo(bx - nx, by - ny)
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_tint(col, 0.95))
        p.drawPath(path)
        p.setBrush(QColor(BED))
        p.setPen(QPen(col, 2))
        p.drawEllipse(QPointF(cx, cy), 6, 6)
        p.setFont(self._small)
        fs = QFontMetricsF(self._small)
        if m.alive:
            p.setPen(QColor(INK))
            p.drawText(QRectF(cx - 60, cy + 12, 120, fs.height()), Qt.AlignmentFlag.AlignCenter,
                       _clock(now - m.turn_t0))
        cur = m.current()
        p.setFont(self._big)
        fb = QFontMetricsF(self._big)
        top = cy + r * 0.72 + 24
        if cur == MODEL_SLOT:
            head = m.model or t("machine.model_slot")
            if busy:
                sub = t("machine.thinking", n=m.round[0], max=m.round[1], ago=_clock(now - m.think_t0))
            elif m.alive:
                sub = t("machine.between", n=m.round[0], max=m.round[1])
            else:
                sub = t("machine.idle")
            args = ""
        else:
            c = m._call(m.running[-1]) if m.running else None
            head = cur
            sub = (t("machine.running", ago=_clock(now - c["t0"])) if c else "")
            if c and c["agent"]:
                sub = t("machine.by_agent", agent=c["agent"]) + " · " + sub
            args = c["args"] if c else ""
        head = fb.elidedText(head, Qt.TextElideMode.ElideMiddle, area.width() - 40)
        p.setPen(QColor(RUN if busy else INK))
        p.drawText(QRectF(area.left() + 20, top, area.width() - 40, fb.height()),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, head)
        top += fb.height() + 2
        p.setFont(self._ui)
        p.setPen(QColor(INK))
        p.drawText(QRectF(area.left() + 20, top, area.width() - 40, fm.height()),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(sub, Qt.TextElideMode.ElideRight, area.width() - 40))
        top += fm.height() + 6
        if args:
            p.setFont(self._mono)
            fmm = QFontMetricsF(self._mono)
            p.setPen(QColor(INK))
            box = QRectF(area.left() + 24, top, area.width() - 48, fmm.height() * 2 + 4)
            p.drawText(box, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
                                | Qt.TextFlag.TextWrapAnywhere),
                       fmm.elidedText(args, Qt.TextElideMode.ElideRight, box.width() * 2 - 20))
        p.setFont(self._small)
        p.setPen(QColor(INK))
        if m.alive:
            line = t("machine.turn_line", calls=m.turn_calls, ago=_clock(now - m.turn_t0))
        else:
            line = t("machine.title")
        p.drawText(QRectF(area.left() + 14, area.bottom() - fs.height() - 6, area.width() - 28, fs.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)

    def _paint_belt(self, p, area, now):
        m = self._m
        p.fillRect(area, QColor(PANEL))
        p.setPen(QPen(QColor(RAIL), 1))
        p.drawLine(QPointF(area.left(), area.top()), QPointF(area.right(), area.top()))
        fm = QFontMetricsF(self._small)
        rows_fit = max(1, int((area.height() - 56) // ROW_H))
        first = max(0, self._rows - rows_fit)
        items = self.items(now)
        hidden = sum(1 for c in items
                     if c["seq"] in self._boxes and self._boxes[c["seq"]]["row"] < first)
        p.setClipRect(QRectF(area.left() + 8, area.top() + 1, area.width() - 16,
                             area.height() - fm.height() - 12))
        p.setFont(self._small)
        for c in items:
            b = self._boxes.get(c["seq"])
            if b is None or b["row"] < first:
                continue
            bw = self._box_w(c, now)
            x = b["x"]
            if x + bw < area.left() or x > area.right():
                continue
            y = area.top() + 12 + (b["row"] - first) * ROW_H
            rect = QRectF(x, y, bw, BOX_H)
            run = c["t1"] is None
            col = RUN if run else (FAIL if c["ok"] is False else (AGENT if c["agent"] else INK))
            p.setPen(QPen(QColor(col), 1.5 if run else 1.0))
            p.setBrush(_tint(col, 0.18 if run else 0.07))
            p.drawRoundedRect(rect, 5, 5)
            p.setPen(QColor(INK))
            p.drawText(rect.adjusted(4, 0, -4, 0), Qt.AlignmentFlag.AlignCenter, self._box_label(c))
        p.setClipping(False)
        n, fails, secs = m.totals()
        p.setFont(self._small)
        p.setPen(QColor(INK))
        line = t("machine.totals", n=n, fails=fails, secs=_clock(secs), open=len(m.running))
        if hidden:
            line += "  ·  " + t("machine.earlier", n=hidden)
        p.drawText(QRectF(area.left() + 14, area.bottom() - fm.height() - 6, area.width() - 28, fm.height() + 4),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
