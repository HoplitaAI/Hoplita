#!/usr/bin/env python3
import os as _os

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QScrollArea, QCheckBox,
                             QSizePolicy, QDialog)
from PyQt6.QtCore import Qt, pyqtSignal

try:
    from hoplita_i18n import t
except Exception:
    def t(k, **kw):
        return k

_SYSTEM = {"run_command", "exec_command", "edit_file", "code_write_file", "code_apply_patch",
           "call_api", "screenshot_web", "browse_web",
           "hands_click", "hands_type", "hands_key", "edit_my_config",
           "mail_send"}

from tool_families import (_EXACT, _PREFIX, _FED_ACCOUNT, _FED_CONTACTS,
                           _FED_INBOX, _SUBJECT, OTHER, _ORDER,
                           tool_category, group_tools)

_ICON = {"system": "📂", "code": "💻", "web": "🌐", "mail": "✉", "desktop": "🖱", "notes": "📝",
         "memory": "🧠", "tasks": "✅", "calendar": "📅", "cron": "⏰",
         "background": "👁",
         "chat": "💬", "canvas": "🎨", "whiteboard": "🧭", "creds": "🔐", "agents": "🤖",
         "device": "🖥", "fedaccount": "🪪", "fedcontacts": "👥",
         "fedshare": "📤", "fedcode": "🔀", "fedsync": "🔄", OTHER: "🔧"}


def category_label(cat):
    if cat.startswith("mcp:"):
        return f"🔌 {cat[4:]}"
    return f"{_ICON.get(cat, '🔧')} {t('fwcat.' + cat)}"


_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TICK = next((p for p in (_os.path.join(_HERE, "icons", "tick.svg"),
                          _os.path.join(_HERE, "..", "icons", "tick.svg"))
              if _os.path.exists(p)), "")
_TICK_CSS = """
QCheckBox { spacing:0; margin:0 2px 0 0; }
QCheckBox::indicator { width:17px; height:17px; border-radius:4px;
                       border:1px solid #3a4a63; background:#0f1626; }
QCheckBox::indicator:hover { border-color:#7aa2e8; }
QCheckBox::indicator:checked { border-color:#3f9a6a; background:#132b21;%s }
QCheckBox::indicator:checked:hover { background:#1b4030; }
QCheckBox::indicator:indeterminate { border-color:#b8892f; background:#2b2313; }
""" % (f'\n                       image:url("{_TICK.replace(_os.sep, "/")}");' if _TICK else "")

_FS_NAME = 13
_FS_DESC = 12
_FS_BADGE = 10
_FS_HEAD = 13
_FS_COUNT = 12

_NAME_ON = f"color:#e6edf7; font-family:monospace; font-size:{_FS_NAME}px;"
_NAME_OFF = (f"color:#6b7c93; font-family:monospace; font-size:{_FS_NAME}px; "
             "text-decoration:line-through;")
_HEAD_CSS = (f"color:#cfe0ff; font-size:{_FS_HEAD}px; font-weight:600;")
_HEAD_N_CSS = f"color:#7c8aa3; font-size:{_FS_COUNT}px;"


class ToolPicker(QWidget):

    changed = pyqtSignal(set)

    def __init__(self, parent=None, compact=False):
        super().__init__(parent)
        self._tools = {}
        self._on = set()
        self._rows = {}
        self._groups = []
        self._collapsed = set()
        self._quiet = False
        self._compact = compact

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("fw.search_ph"))
        self.search.setClearButtonEnabled(True)
        self.search.setStyleSheet(
            "QLineEdit { background:#0d1526; color:#e6edf7; "
            "border:1px solid #24304a; border-radius:7px; padding:6px 9px; "
            f"font-size:{_FS_DESC}px; }} QLineEdit:focus "
            "{ border-color:#4f7fd0; }")
        self.search.textChanged.connect(self._apply_filter)
        bar.addWidget(self.search, 1)
        for _key, _on in (("fw.enable_shown", True), ("fw.disable_shown", False)):
            b = QPushButton(t(_key))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(t("fw.shown_tip"))
            b.setStyleSheet(
                "QPushButton { background:#182338; color:#cbd5e1; border:1px "
                "solid #2b3a55; border-radius:7px; padding:6px 11px; "
                f"font-size:{_FS_DESC}px; }} "
                "QPushButton:hover { background:#22314c; }")
            b.clicked.connect(lambda _c=False, v=_on: self._set_shown(v))
            bar.addWidget(b)
        self._fold = QPushButton(t("fw.fold_all"))
        self._fold.setCursor(Qt.CursorShape.PointingHandCursor)
        self._fold.setStyleSheet(
            "QPushButton { background:#182338; color:#cbd5e1; border:1px "
            "solid #2b3a55; border-radius:7px; padding:6px 11px; "
            f"font-size:{_FS_DESC}px; }} "
            "QPushButton:hover { background:#22314c; }")
        self._fold.clicked.connect(self._toggle_fold_all)
        bar.addWidget(self._fold)
        lay.addLayout(bar)

        self.count = QLabel("")
        self.count.setStyleSheet(f"color:#7c8aa3; font-size:{_FS_COUNT}px;")
        lay.addWidget(self.count)

        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QScrollArea.Shape.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._box = QWidget()
        self._box.setObjectName("fwBox")
        self._box.setStyleSheet("QWidget#fwBox { background:#111a2b; }")
        self._box_lay = QVBoxLayout(self._box)
        self._box_lay.setContentsMargins(2, 0, 12, 0)
        self._box_lay.setSpacing(0)
        sc.setWidget(self._box)
        lay.addWidget(sc, 1)
        if compact:
            sc.setMinimumHeight(240)

    def set_tools(self, tools, enabled):
        self._tools = dict(tools or {})
        self._on = set(enabled or ())
        self._rebuild()

    def enabled(self):
        return set(self._on)

    def has_catalog(self):
        return bool(self._tools)

    def categories(self):
        return group_tools(self._tools)

    def _clean(self, desc):
        d = str(desc or "")
        if d.startswith("["):
            d = d.split("]", 1)[-1]
        return " ".join(d.split("COST WARNING")[0].split("Args:")[0].split())

    def _rebuild(self):
        while self._box_lay.count():
            it = self._box_lay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        self._rows = {}
        self._groups = []
        _stripe = 0
        for cat, names in group_tools(self._tools):
            head, head_cb, caret = self._make_header(cat, names)
            self._box_lay.addWidget(head)
            rows = []
            for name in names:
                row = self._make_row(name, _stripe)
                _stripe += 1
                self._box_lay.addWidget(row)
                rows.append(name)
            self._groups.append({"cat": cat, "names": rows, "head": head,
                                 "cb": head_cb, "caret": caret})
        self._box_lay.addStretch()
        self._apply_filter()

    def _make_header(self, cat, names):
        head = QWidget()
        head.setObjectName("fwHead")
        head.setStyleSheet(
            "QWidget#fwHead { background:#1a2740; border-radius:5px; "
            "margin-top:6px; } "
            "QWidget#fwHead QLabel { background:transparent; }")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(2, 4, 6, 4)
        hl.setSpacing(8)
        cb = QCheckBox()
        cb.setStyleSheet(_TICK_CSS)
        cb.setTristate(True)
        cb.setCursor(Qt.CursorShape.PointingHandCursor)
        cb.setToolTip(t("fw.cat_tip"))
        cb.clicked.connect(lambda _c=False, c=cat: self._toggle_category(c))
        hl.addWidget(cb)
        caret = QLabel("▾")
        caret.setStyleSheet(f"color:#7c8aa3; font-size:{_FS_COUNT}px;")
        hl.addWidget(caret)
        lbl = QLabel(category_label(cat))
        lbl.setStyleSheet(_HEAD_CSS)
        hl.addWidget(lbl)
        n = QLabel("")
        n.setObjectName("fwHeadN")
        n.setStyleSheet(_HEAD_N_CSS)
        hl.addWidget(n)
        hl.addStretch()
        head.mousePressEvent = lambda _e, c=cat: self._toggle_collapse(c)
        head.setCursor(Qt.CursorShape.PointingHandCursor)
        head._n_label = n
        return head, cb, caret

    def _make_row(self, name, stripe):
        desc = self._clean(self._tools[name])
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(18, 2, 2, 2)
        rl.setSpacing(8)
        cb = QCheckBox()
        cb.setStyleSheet(_TICK_CSS)
        cb.setChecked(name in self._on)
        cb.setCursor(Qt.CursorShape.PointingHandCursor)
        rl.addWidget(cb)
        lbl = QLabel(name)
        lbl.setFixedWidth(228)
        lbl.setStyleSheet(_NAME_ON if name in self._on else _NAME_OFF)
        rl.addWidget(lbl)
        cb.toggled.connect(lambda on, n=name, la=lbl: (
            la.setStyleSheet(_NAME_ON if on else _NAME_OFF),
            self._toggle(n, on)))
        _bw = QWidget()
        _bl = QHBoxLayout(_bw)
        _bl.setContentsMargins(0, 0, 0, 0)
        _bl.setSpacing(4)
        for _txt, _fg, _bg in self._badges(name):
            bg = QLabel(_txt)
            bg.setStyleSheet(
                f"QLabel {{ color:{_fg}; background:{_bg}; "
                f"font-size:{_FS_BADGE}px; border-radius:4px; "
                f"padding:1px 6px; }}")
            _bl.addWidget(bg)
        _bl.addStretch()
        _bw.setFixedWidth(120)
        rl.addWidget(_bw)
        d = QLabel(desc[:130] + ("…" if len(desc) > 130 else ""))
        d.setStyleSheet(f"color:#8b99b0; font-size:{_FS_DESC}px;")
        d.setToolTip(desc[:600])
        d.setSizePolicy(QSizePolicy.Policy.Ignored,
                        QSizePolicy.Policy.Preferred)
        rl.addWidget(d, 1)
        row.setObjectName("fwRow")
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        _bw.setObjectName("fwBadges")
        row.setStyleSheet(
            "QWidget#fwRow { background:transparent; border-bottom:1px solid #18233a; } "
            "QWidget#fwRow:hover { background:#142037; } "
            "QWidget#fwRow QLabel, QWidget#fwBadges { background:transparent; }")
        self._rows[name] = (cb, row)
        return row

    def _badges(self, name):
        out = []
        if "." in name:
            out.append((t("fw.badge_external"), "#c4b5fd", "#241c3a"))
        if name in _SYSTEM:
            out.append((t("fw.badge_system"), "#fca5a5", "#331a1a"))
        return out

    def _toggle(self, name, on):
        if on:
            self._on.add(name)
        else:
            self._on.discard(name)
        if not self._quiet:
            self._paint_count()
            self.changed.emit(set(self._on))

    def _visible(self):
        return [n for n, (_cb, row) in self._rows.items() if row.isVisibleTo(self)]

    def _matches(self, name, q):
        if not q:
            return True
        return (q in name.lower()
                or q in self._clean(self._tools.get(name, "")).lower()
                or q in tool_category(name).lower())

    def _shown_in(self, cat):
        q = (self.search.text() or "").strip().lower()
        for g in self._groups:
            if g["cat"] == cat:
                return [n for n in g["names"] if self._matches(n, q)]
        return []

    def _set_many(self, names, value):
        if not names:
            return
        _was = self._quiet
        self._quiet = True
        try:
            for n in names:
                self._rows[n][0].setChecked(value)
        finally:
            self._quiet = _was
        self._paint_count()
        self.changed.emit(set(self._on))

    def _toggle_category(self, cat):
        names = self._shown_in(cat)
        if not names:
            return
        self._set_many(names, not all(n in self._on for n in names))

    def _set_shown(self, value):
        self._set_many(self._visible(), value)

    def _toggle_collapse(self, cat):
        if cat in self._collapsed:
            self._collapsed.discard(cat)
        else:
            self._collapsed.add(cat)
        self._apply_filter()

    def _toggle_fold_all(self):
        cats = [g["cat"] for g in self._groups]
        if len(self._collapsed) >= len(cats) and cats:
            self._collapsed.clear()
        else:
            self._collapsed = set(cats)
        self._apply_filter()

    def _apply_filter(self, *_a):
        q = (self.search.text() or "").strip().lower()
        for g in self._groups:
            hits = [n for n in g["names"] if self._matches(n, q)]
            folded = g["cat"] in self._collapsed and not q
            for n in g["names"]:
                self._rows[n][1].setVisible(n in hits and not folded)
            g["head"].setVisible(bool(hits))
            g["caret"].setText("▸" if folded else "▾")
        self._paint_count()

    def _paint_count(self):
        tot = len(self._tools)
        on = len(self._on & set(self._tools))
        q = (self.search.text() or "").strip().lower()
        shown = sum(1 for n in self._tools if self._matches(n, q))
        txt = t("fw.count", on=on, total=tot)
        if shown != tot:
            txt += "  ·  " + t("fw.count_shown", n=shown)
        self.count.setText(txt)
        for g in self._groups:
            hits = [n for n in g["names"] if self._matches(n, q)]
            _on = sum(1 for n in hits if n in self._on)
            g["head"]._n_label.setText(f"{_on}/{len(hits)}")
            g["cb"].setCheckState(
                Qt.CheckState.Checked if hits and _on == len(hits) else
                Qt.CheckState.Unchecked if _on == 0 else
                Qt.CheckState.PartiallyChecked)



_BTN_CSS = ("QPushButton { background:#182338; color:#cbd5e1; border:1px "
            "solid #2b3a55; border-radius:7px; padding:7px 14px; "
            f"font-size:{_FS_DESC}px; }} "
            "QPushButton:hover { background:#22314c; }")
_BTN_MAIN_CSS = ("QPushButton { background:#1f3b5c; color:#e6edf7; border:1px "
                 "solid #2f5a8a; border-radius:7px; padding:7px 16px; "
                 f"font-size:{_FS_DESC}px; font-weight:600; }} "
                 "QPushButton:hover { background:#27497a; }")


class TabFirewallDialog(QDialog):

    def __init__(self, parent, tab_title, offered, blocked):
        super().__init__(parent)
        self._offered = dict(offered or {})
        self._prev = set(blocked or ())
        self._cleared = False
        self.setWindowTitle(t("fwtab.title", tab=tab_title or ""))
        self.setModal(True)
        self.resize(960, 640)
        self.setStyleSheet("QDialog { background:#0b1220; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(8)
        head = QLabel(t("fwtab.title", tab=tab_title or ""))
        head.setStyleSheet(f"color:#e6edf7; font-size:{_FS_NAME + 2}px; "
                           "font-weight:600;")
        lay.addWidget(head)
        hint = QLabel(t("fwtab.hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:#8b99b0; font-size:{_FS_DESC}px;")
        lay.addWidget(hint)
        self.picker = ToolPicker()
        self.picker.set_tools(self._offered, set(self._offered) - self._prev)
        if not self._offered:
            self.picker.count.setText(t("tools.mcp_unavailable"))
        lay.addWidget(self.picker, 1)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_clear = QPushButton(t("fwtab.clear"))
        self.btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear.setToolTip(t("fwtab.clear_tip"))
        self.btn_clear.setStyleSheet(_BTN_CSS)
        self.btn_clear.clicked.connect(self._clear)
        row.addWidget(self.btn_clear)
        row.addStretch()
        self.btn_cancel = QPushButton(t("fwtab.cancel"))
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.setStyleSheet(_BTN_CSS)
        self.btn_cancel.clicked.connect(self.reject)
        row.addWidget(self.btn_cancel)
        self.btn_save = QPushButton(t("fwtab.save"))
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.setStyleSheet(_BTN_MAIN_CSS)
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self.accept)
        row.addWidget(self.btn_save)
        lay.addLayout(row)

    def _clear(self):
        self._cleared = True
        self.accept()

    def blocked(self):
        if self._cleared:
            return set()
        if not self.picker.has_catalog():
            return set(self._prev)
        return set(self._offered) - self.picker.enabled()
