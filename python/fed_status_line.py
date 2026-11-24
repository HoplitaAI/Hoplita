from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

from federation_widget import _size, plan_summary
from hoplita_i18n import t

_LEVEL_COL = ("#38bdf8", "#f5b800", "#ef4444")
_LEVEL_EDGE = ("#1f2a3d", "#f5b800", "#ef4444")


def meters(data):
    lim = (data or {}).get("limits") or {}
    use = (data or {}).get("usage") or {}
    rows = [("fed.plan_sync", use.get("sync_bytes"), lim.get("max_sync_bytes"), True),
            ("fed.plan_contacts", use.get("contacts"), lim.get("max_contacts"), False),
            ("fed.plan_children", use.get("children"), lim.get("max_children"), False),
            ("fed.plan_outbox", use.get("outbox_bytes"), lim.get("max_inbox_bytes"), True),
            ("fed.plan_inbox", use.get("inbox_envelopes"),
             lim.get("max_inbox_envelopes"), False)]
    out = []
    for key, u, l, size in rows:
        u, l = int(u or 0), int(l or 0)
        frac = u / float(l) if l else 0.0
        lv = 2 if frac >= 1 else 1 if frac >= 0.8 else 0
        if lv or key not in ("fed.plan_outbox", "fed.plan_inbox"):
            out.append((key, u, l, size, lv))
    return out


def state_of(data):
    if data is None:
        return "none"
    if not isinstance(data, dict):
        return "unreachable"
    if data.get("registered") is False:
        return "unfederated"
    if data.get("error") or not (data.get("limits") or data.get("usage")):
        return "unreachable"
    return "plan"


class _Bar(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(54, 6)
        self.frac, self.col = 0.0, QColor(_LEVEL_COL[0])

    def set(self, frac, col):
        self.frac, self.col = max(0.0, min(1.0, frac)), QColor(col)
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#243047"))
        p.drawRoundedRect(QRectF(0, 0, self.width(), 6), 3, 3)
        if self.frac > 0:
            p.setBrush(self.col)
            p.drawRoundedRect(QRectF(0, 0, max(6.0, self.width() * self.frac), 6), 3, 3)


class _ElideLabel(QLabel):

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text if len(text) > 24 else "")

    def paintEvent(self, ev):
        if self.fontMetrics().horizontalAdvance(self.text()) <= self.contentsRect().width():
            return super().paintEvent(ev)
        p = QPainter(self)
        p.setPen(self.palette().color(self.foregroundRole()))
        p.drawText(self.contentsRect(), int(self.alignment()) | Qt.TextFlag.TextSingleLine,
                   self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight,
                                                 self.contentsRect().width()))


class _Meter(QWidget):
    def __init__(self):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.name = QLabel()
        self.bar = _Bar()
        self.val = QLabel()
        h.addWidget(self.name)
        h.addWidget(self.bar, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(self.val)

    def paint(self, key, used, limit, as_size, level):
        fmt = _size if as_size else str
        self.name.setText(t(key))
        self.val.setText(f"{fmt(used)} / {fmt(limit) if limit else '∞'}")
        col = _LEVEL_COL[level]
        self.val.setStyleSheet(f"color:{col if level else '#cbd5e1'}; "
                               f"font-weight:{700 if level else 400};")
        self.bar.setVisible(bool(limit))
        if limit:
            self.bar.set(used / float(limit), col)


class FedStatusLine(QWidget):
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("fed_status")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 4, 12, 4)
        h.setSpacing(14)
        self.who = _ElideLabel()
        self.who.setObjectName("fed_who")
        h.addWidget(self.who)
        self.tier = _ElideLabel()
        self.tier.setObjectName("fed_tier")
        h.addWidget(self.tier)
        self.lend = _ElideLabel()
        self.lend.setObjectName("fed_lend")
        self.lend.setVisible(False)
        h.addWidget(self.lend)
        self.note = _ElideLabel()
        self.note.setObjectName("fed_note")
        h.addWidget(self.note)
        self._meters_at = h.count()
        self.meter_w = []
        h.addStretch(1)
        self.tail = QHBoxLayout()
        self.tail.setContentsMargins(0, 0, 0, 0)
        self.tail.setSpacing(4)
        h.addLayout(self.tail)
        self.state = "none"
        self.level = 0
        self.set_quota(None)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(ev)

    def _style(self, edge):
        self.setStyleSheet(
            f"#fed_status {{ background:#0f1626; border-bottom:1px solid {edge}; }} "
            f"#fed_status QLabel {{ color:#94a3b8; background:transparent; "
            f"font-size:13px; }} "
            f"#fed_who {{ color:#cbd5e1; }} "
            f"#fed_status #fed_tier {{ color:#e9d5ff; background:#31184d; "
            f"border-radius:8px; "
            f"padding:1px 9px; font-weight:700; font-size:12px; }} "
            f"#fed_status #fed_lend {{ color:#e6edf7; background:#14324a; "
            f"border-radius:8px; padding:1px 9px; font-size:12px; }}")

    def set_quota(self, data, handle="", server=""):
        st = state_of(data)
        self.state = st
        if isinstance(data, dict):
            handle = handle or data.get("handle") or ""
            server = server or data.get("server") or ""
        who = "☁ " + (f"{handle}@{server}" if handle and server
                      else handle or t("fedline.title"))
        rows = meters(data) if st == "plan" else []
        self.level = max([r[4] for r in rows] or [0])
        self.who.setText(who)
        self.tier.setVisible(st == "plan")
        if st == "plan":
            self.tier.setText(str(data.get("tier") or "?").upper())
        self.note.setText({"none": t("fedline.asking"),
                           "unfederated": t("fedline.unfederated"),
                           "unreachable": t("fedline.unreachable"),
                           "plan": ""}[st])
        self.note.setVisible(st != "plan")
        lay = self.layout()
        while len(self.meter_w) < len(rows):
            m = _Meter()
            lay.insertWidget(self._meters_at + len(self.meter_w), m)
            self.meter_w.append(m)
        for i, m in enumerate(self.meter_w):
            if i < len(rows):
                m.paint(*rows[i])
                m.setVisible(True)
            else:
                m.setVisible(False)
        self._style(_LEVEL_EDGE[self.level])
        tip = {"unfederated": t("fedline.unfederated_tip"),
               "unreachable": t("fedline.unreachable_tip",
                                error=str((data or {}).get("error") or "")[:200]
                                if isinstance(data, dict) else ""),
               "none": ""}.get(st)
        if st == "plan":
            summ = plan_summary(data)
            tip = (summ[3] + "<br><br>" if summ else "") + t("fed.plan_tip")
        self.setToolTip(tip or "")

    def set_lending(self, live, today):
        live = [r for r in (live or []) if isinstance(r, dict)]
        today = today or {}
        if not live and not today:
            self.lend.setVisible(False)
            return
        if live:
            what = ", ".join(f"{r.get('alias', '?')} → {r.get('peer', '?')}" for r in live[:3])
            self.lend.setText("⇅ " + t("fedline.lending", what=what))
        else:
            calls = sum(int(v.get("calls", 0)) for v in today.values())
            self.lend.setText(t("fedline.lent_today", calls=calls))
        rows = [t("fedline.lent_row", peer=k[0], alias=k[1], calls=v.get("calls", 0),
                  prompt=v.get("prompt", 0), completion=v.get("completion", 0))
                for k, v in sorted(today.items())]
        self.lend.setToolTip(t("fedline.lent_tip", rows="<br>".join(rows) or "—"))
        self.lend.setVisible(True)
