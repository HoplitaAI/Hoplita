
import json

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QLineEdit, QComboBox, QDialog, QGroupBox,
    QMessageBox, QMenu, QDialogButtonBox, QStyledItemDelegate, QStyle,
    QTabWidget, QTextEdit, QPlainTextEdit, QCheckBox, QScrollArea,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QRect, QPointF, QTimer
from PyQt6.QtGui import (
    QColor, QAction, QPainter, QBrush, QFont, QPainterPath, QPen,
)

from hoplita_i18n import t


class _McpWorker(QThread):
    done = pyqtSignal(object)

    def __init__(self, mcp_call, tool, args):
        super().__init__()
        self._mcp_call = mcp_call
        self.tool = tool
        self.args = args

    def run(self):
        try:
            raw = self._mcp_call(self.tool, self.args)
            if isinstance(raw, str) and raw.startswith("❌"):
                self.done.emit({"error": raw})
            else:
                self.done.emit(json.loads(raw) if isinstance(raw, str) else raw)
        except Exception as e:
            self.done.emit({"error": str(e)})


class _PostWorker(QThread):
    done = pyqtSignal(object)

    def __init__(self, url, body, timeout):
        super().__init__()
        self.url, self.body, self.timeout = url, body, timeout

    def run(self):
        try:
            import requests as _rq
            r = _rq.post(self.url, json=self.body, timeout=self.timeout)
            self.done.emit(r.json())
        except Exception as e:
            self.done.emit({"error": str(e)})


class _PermissionsDialog(QDialog):
    RESOURCES = ("notas", "proyectos", "citas", "sesiones")
    LEVELS = ("auto", "review", "no")

    def __init__(self, parent, contact):
        super().__init__(parent)
        self.contact = contact
        self.setWindowTitle(t("fed.perms_title"))
        self.setMinimumWidth(460)
        self.setStyleSheet("QDialog { background:#1e293b; }")
        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        who = QLabel(t("fed.perms_of", handle=contact["handle"],
                       trust=contact.get("trust_level", "")))
        who.setStyleSheet("QLabel { color:#e2e8f0; font-size:14px; }")
        lay.addWidget(who)

        box = QGroupBox(t("fed.perms_tree"))
        box.setStyleSheet(
            "QGroupBox { color:#94a3b8; border:1px solid #334155; border-radius:8px; "
            "margin-top:8px; padding:12px; font-weight:bold; }"
            "QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 4px; }")
        gl = QVBoxLayout(box)

        perms = contact.get("permissions") or {}
        level_labels = [t("fed.perm_auto"), t("fed.perm_review"), t("fed.perm_no")]
        self._combos = {}
        for rt in self.RESOURCES:
            row = QHBoxLayout()
            lbl = QLabel(t("fed.perm_" + rt))
            lbl.setStyleSheet("QLabel { color:#e2e8f0; font-weight:normal; }")
            lbl.setMinimumWidth(150)
            cmb = QComboBox()
            cmb.addItems(level_labels)
            cur = ((perms.get(rt) or {}).get("level")) or "no"
            cmb.setCurrentIndex(self.LEVELS.index(cur) if cur in self.LEVELS
                                else self.LEVELS.index("no"))
            cmb.setStyleSheet(
                "QComboBox { background:#0f172a; color:#e2e8f0; border:1px solid #334155; "
                "border-radius:6px; padding:5px; }")
            self._combos[rt] = cmb
            row.addWidget(lbl)
            row.addWidget(cmb, 1)
            gl.addLayout(row)

        hint = QLabel(t("fed.perms_tree_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("QLabel { color:#64748b; font-weight:normal; font-size:11px; }")
        gl.addWidget(hint)
        lay.addWidget(box)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def get_perms(self):
        return {rt: {"level": self.LEVELS[c.currentIndex()]}
                for rt, c in self._combos.items()}


class _ChildPackDialog(QDialog):

    CAPABILITIES = (
        ("web", "child.cap_web", "child.cap_web_tip"),
        ("research", "child.cap_research", "child.cap_research_tip"),
        ("files", "child.cap_files", "child.cap_files_tip"),
        ("code", "child.cap_code", "child.cap_code_tip"),
        ("terminal", "child.cap_terminal", "child.cap_terminal_tip"),
        ("write", "child.cap_write", "child.cap_write_tip"),
    )

    def _mcp_url(self):
        try:
            return self.parent()._mcp_url
        except Exception:
            return "http://127.0.0.1:9077"

    def _load_tools(self, pack):
        import requests as _rq
        try:
            r = _rq.get(f"{self._mcp_url()}/firewall", timeout=5)
            fw = r.json() if r.status_code == 200 else {}
        except Exception:
            fw = {}
        allt = fw.get("all_tools") or {}
        if isinstance(allt, list):
            allt = {n: "" for n in allt}
        if not allt:
            self.tools.set_tools({}, set())
            self.tools.count.setText(t("tools.mcp_unavailable"))
            return
        allt = {k: v for k, v in allt.items()
                if k not in set(fw.get("blocked") or ())}
        chosen = set(pack.get("tools") or [])
        if not chosen:
            try:
                r2 = _rq.post(f"{self._mcp_url()}/child/tools_for",
                              json={"capabilities": list(
                                  pack.get("capabilities") or [])}, timeout=5)
                chosen = set((r2.json() or {}).get("tools") or [])
            except Exception:
                chosen = set()
        self.tools.set_tools(allt, chosen & set(allt))

    def _preset_changed(self, _on=False):
        if getattr(self, "_loading_preset", False) or not hasattr(self, "tools"):
            return
        self._loading_preset = True
        try:
            self._load_tools({"capabilities": [c for c, cb in self.caps.items()
                                               if cb.isChecked()]})
        finally:
            self._loading_preset = False

    def __init__(self, parent, pack, folders, models, projects=(), nodes=()):
        super().__init__(parent)
        self.setWindowTitle(t("child.edit_title", name=pack.get("handle")
                              or pack.get("name", "")))
        self.setMinimumSize(560, 620)
        self.setStyleSheet("QDialog { background:#1e293b; }")
        _LBL = "QLabel { color:#e2e8f0; font-weight:normal; }"
        _IN = ("background:#0f172a; color:#e2e8f0; border:1px solid #334155; "
               "border-radius:6px; padding:6px;")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        _scroll = QScrollArea()
        _scroll.setWidgetResizable(True)
        _scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        _scroll.setStyleSheet("QScrollArea { border:0; background:transparent; }")
        _body = QWidget()
        _body.setObjectName("childBody")
        _body.setStyleSheet("QWidget#childBody { background:transparent; }")
        _scroll.setWidget(_body)
        outer.addWidget(_scroll, 1)
        lay = QVBoxLayout(_body)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(10)

        head = QLabel(t("child.edit_hint"))
        head.setWordWrap(True)
        head.setStyleSheet("QLabel { color:#94a3b8; font-size:11px; }")
        lay.addWidget(head)

        row = QHBoxLayout()
        av = QLabel(t("child.avatar"))
        av.setStyleSheet(_LBL)
        self.avatar = QLineEdit((pack.get("avatar") or "").strip())
        self.avatar.setMaxLength(4)
        self.avatar.setFixedWidth(70)
        self.avatar.setStyleSheet(f"QLineEdit {{ {_IN} }}")
        ml = QLabel(t("child.model"))
        ml.setStyleSheet(_LBL)
        self.model = QComboBox()
        self.model.setStyleSheet(f"QComboBox {{ {_IN} }}")
        self.model.addItem(t("child.model_inherit"), "")
        for m in models:
            self.model.addItem(m, m)
        cur = (pack.get("model") or "").strip()
        if cur and self.model.findData(cur) < 0:
            self.model.addItem(t("child.model_missing", name=cur), cur)
        self.model.setCurrentIndex(max(0, self.model.findData(cur)))
        row.addWidget(av); row.addWidget(self.avatar)
        row.addSpacing(12)
        row.addWidget(ml); row.addWidget(self.model, 1)
        lay.addLayout(row)

        nrow = QHBoxLayout()
        nl = QLabel(t("child.node"))
        nl.setStyleSheet(_LBL)
        self.node = QComboBox()
        self.node.setStyleSheet(f"QComboBox {{ {_IN} }}")
        self.node.addItem(t("child.node_here"), "")
        _cur_node = (pack.get("node") or "").strip()
        for h in nodes:
            self.node.addItem(f"🖥 {h}", h)
        if _cur_node and self.node.findData(_cur_node) < 0:
            self.node.addItem(t("child.model_missing", name=_cur_node), _cur_node)
        self.node.setCurrentIndex(max(0, self.node.findData(_cur_node)))
        nrow.addWidget(nl)
        nrow.addWidget(self.node, 1)
        lay.addLayout(nrow)

        rl = QLabel(t("child.role"))
        rl.setStyleSheet(_LBL)
        lay.addWidget(rl)
        self.role = QTextEdit(pack.get("role") or "")
        self.role.setStyleSheet(f"QTextEdit {{ {_IN} }}")
        self.role.setMinimumHeight(150)
        lay.addWidget(self.role)

        cap_box = QGroupBox(t("child.capabilities"))
        cap_box.setStyleSheet(
            "QGroupBox { color:#94a3b8; border:1px solid #334155; "
            "border-radius:8px; margin-top:8px; padding:10px; font-weight:bold; }"
            "QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 4px; }")
        cg = QHBoxLayout(cap_box)
        have = set(pack.get("capabilities") or [])
        self.caps = {}
        for c, _label, _tip in self.CAPABILITIES:
            cb = QCheckBox(t(_label))
            cb.setChecked(c in have)
            cb.setStyleSheet("QCheckBox { color:#e2e8f0; }")
            cb.setToolTip(t(_tip))
            cb.toggled.connect(self._preset_changed)
            self.caps[c] = cb
            cg.addWidget(cb)
        _pre = QLabel(t("child.caps_are_a_preset"))
        _pre.setWordWrap(True)
        _pre.setStyleSheet("color:#7c8aa3; font-size:11px;")
        cg.addWidget(_pre, 1)
        lay.addWidget(cap_box)

        fw_box = QGroupBox(t("child.tools_title"))
        fw_box.setStyleSheet(cap_box.styleSheet())
        fl = QVBoxLayout(fw_box)
        _fh = QLabel(t("child.tools_hint"))
        _fh.setWordWrap(True)
        _fh.setStyleSheet("color:#7c8aa3; font-size:12px;")
        fl.addWidget(_fh)
        from tools_firewall import ToolPicker
        self._orig_tools = [x for x in (pack.get("tools") or []) if x]
        self.tools = ToolPicker(compact=True)
        fl.addWidget(self.tools)
        lay.addWidget(fw_box, 1)
        self._load_tools(pack)

        sc_box = QGroupBox(t("child.scope"))
        sc_box.setStyleSheet(cap_box.styleSheet())
        sg = QVBoxLayout(sc_box)
        hint = QLabel(t("child.scope_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("QLabel { color:#64748b; font-weight:normal; font-size:11px; }")
        sg.addWidget(hint)
        inner = QWidget()
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(0, 0, 0, 0)
        chosen = set(pack.get("notes_folders") or [])
        self.folders = {}
        for f in sorted(set(folders) | chosen, key=str.lower):
            cb = QCheckBox(f if f in folders else t("child.folder_gone", name=f))
            cb.setChecked(f in chosen)
            cb.setStyleSheet("QCheckBox { color:#e2e8f0; }")
            self.folders[f] = cb
            iv.addWidget(cb)
        if not self.folders:
            none = QLabel(t("child.scope_no_folders"))
            none.setStyleSheet("QLabel { color:#64748b; font-weight:normal; }")
            iv.addWidget(none)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(inner)
        area.setMaximumHeight(130)
        area.setStyleSheet("QScrollArea { border:0; }")
        sg.addWidget(area)

        pchosen = set(pack.get("projects") or [])
        self.projects = {}
        if projects or pchosen:
            pl = QLabel(t("child.projects"))
            pl.setStyleSheet("QLabel { color:#94a3b8; font-weight:normal; "
                             "font-size:11px; }")
            pl.setWordWrap(True)
            sg.addWidget(pl)
            pin = QWidget()
            pv = QVBoxLayout(pin)
            pv.setContentsMargins(0, 0, 0, 0)
            for pr in sorted(set(projects) | pchosen, key=str.lower):
                cb = QCheckBox(pr if pr in projects
                               else t("child.folder_gone", name=pr))
                cb.setChecked(pr in pchosen)
                cb.setStyleSheet("QCheckBox { color:#e2e8f0; }")
                self.projects[pr] = cb
                pv.addWidget(cb)
            parea = QScrollArea()
            parea.setWidgetResizable(True)
            parea.setWidget(pin)
            parea.setMaximumHeight(110)
            parea.setStyleSheet("QScrollArea { border:0; }")
            sg.addWidget(parea)

        srow = QHBoxLayout()
        skl = QLabel(t("child.skills_folder"))
        skl.setStyleSheet(_LBL)
        self.skills = QComboBox()
        self.skills.setStyleSheet(f"QComboBox {{ {_IN} }}")
        self.skills.addItem(t("child.skills_default"), "")
        for f in sorted(folders, key=str.lower):
            self.skills.addItem(f, f)
        _sk = (pack.get("skills_folder") or "").strip()
        if _sk and self.skills.findData(_sk) < 0:
            self.skills.addItem(t("child.folder_gone", name=_sk), _sk)
        self.skills.setCurrentIndex(max(0, self.skills.findData(_sk)))
        srow.addWidget(skl)
        srow.addWidget(self.skills, 1)
        sg.addLayout(srow)
        lay.addWidget(sc_box)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)
        self.resize(940, 820)

    def _tools_value(self):
        if not self.tools.has_catalog():
            return sorted(self._orig_tools)
        return sorted(self.tools.enabled())

    def _caps_value(self):
        caps = [c for c, cb in self.caps.items() if cb.isChecked()]
        if self.tools.has_catalog() and not self.tools.enabled():
            return []
        return caps


    def values(self):
        return {"role": self.role.toPlainText().strip(),
                "model": self.model.currentData() or "",
                "avatar": self.avatar.text().strip(),
                "capabilities": self._caps_value(),
                "tools": self._tools_value(),
                "notes_folders": [f for f, cb in self.folders.items()
                                  if cb.isChecked()],
                "projects": [p for p, cb in self.projects.items()
                             if cb.isChecked()],
                "skills_folder": self.skills.currentData() or "",
                "node": self.node.currentData() or ""}


class _AddContactDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(t("fed.msg_add_title"))
        self.setMinimumWidth(380)
        self.setStyleSheet("QDialog { background:#1e293b; }")
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        self.handle = QLineEdit()
        self.handle.setPlaceholderText(t("fed.add_placeholder"))
        self.handle.setStyleSheet(
            "QLineEdit { background:#0f172a; color:#e2e8f0; border:1px solid #334155; "
            "border-radius:6px; padding:8px; }")
        lay.addWidget(self.handle)

        trow = QHBoxLayout()
        tl = QLabel(t("fed.trust_level"))
        tl.setStyleSheet("QLabel { color:#e2e8f0; }")
        self.trust = QComboBox()
        self.trust.addItems(["accept", "open", "analyze"])
        self.trust.setStyleSheet(
            "QComboBox { background:#0f172a; color:#e2e8f0; border:1px solid #334155; "
            "border-radius:6px; padding:6px; }")
        trow.addWidget(tl)
        trow.addWidget(self.trust, 1)
        lay.addLayout(trow)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self):
        return self.handle.text().strip(), self.trust.currentText()


class _LocalChildDialog(QDialog):

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(t("child.add_title"))
        self.setMinimumWidth(520)
        self.setStyleSheet("QDialog { background:#1e293b; } QLabel { color:#e2e8f0; } "
                           "QLineEdit, QPlainTextEdit { background:#0f172a; "
                           "color:#e2e8f0; border:1px solid #334155; "
                           "border-radius:6px; padding:7px; }")
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        intro = QLabel(t("child.add_intro"))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        lay.addWidget(QLabel(t("child.add_desc")))
        self.desc = QPlainTextEdit()
        self.desc.setPlaceholderText(t("child.add_desc_hint"))
        self.desc.setMinimumHeight(140)
        lay.addWidget(self.desc)
        lay.addWidget(QLabel(t("child.add_name_opt")))
        self.name = QLineEdit()
        lay.addWidget(self.name)
        bb = QDialogButtonBox()
        ok = bb.addButton(t("child.add_create"), QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton(QDialogButtonBox.StandardButton.Cancel)
        ok.clicked.connect(self._done)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _done(self):
        if not self.desc.toPlainText().strip():
            self.desc.setFocus()
            return
        self.accept()

    def values(self):
        return {"description": self.desc.toPlainText().strip(),
                "name": self.name.text().strip()}


class _BoxChildDialog(QDialog):

    _FIELD = ("QLineEdit, QComboBox { background:#0f172a; color:#e2e8f0; "
              "border:1px solid #334155; border-radius:6px; padding:7px; }")

    def __init__(self, parent, models):
        super().__init__(parent)
        self.setWindowTitle(t("child.box_title"))
        self.setMinimumWidth(560)
        self.setStyleSheet("QDialog { background:#1e293b; } "
                           "QLabel, QCheckBox { color:#e2e8f0; } " + self._FIELD)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        intro = QLabel(t("child.box_intro"))
        intro.setWordWrap(True)
        lay.addWidget(intro)

        lay.addWidget(QLabel(t("child.box_name")))
        self.name = QLineEdit()
        lay.addWidget(self.name)

        lay.addWidget(QLabel(t("child.box_model")))
        self.model = QComboBox()
        self.model.addItem(t("child.box_model_base"), "")
        for m in models:
            self.model.addItem(m, m)
        self.model.addItem(t("child.box_model_own"), "__own__")
        lay.addWidget(self.model)

        self.own = QWidget()
        ol = QVBoxLayout(self.own)
        ol.setContentsMargins(0, 0, 0, 0)
        self.url = QLineEdit()
        self.url.setPlaceholderText(t("child.box_url"))
        self.model_id = QLineEdit()
        self.model_id.setPlaceholderText(t("child.box_model_id"))
        self.key = QLineEdit()
        self.key.setPlaceholderText(t("child.box_key"))
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        for w in (self.url, self.model_id, self.key):
            ol.addWidget(w)
        self.own.setVisible(False)
        lay.addWidget(self.own)
        self.model.currentIndexChanged.connect(
            lambda _i: self.own.setVisible(self.model.currentData() == "__own__"))

        note = QLabel(t("child.box_key_note"))
        note.setWordWrap(True)
        note.setStyleSheet("QLabel { color:#fbbf24; }")
        lay.addWidget(note)

        self.search = QCheckBox(t("child.box_search"))
        lay.addWidget(self.search)

        bb = QDialogButtonBox()
        self.btn_launch = bb.addButton(t("child.box_launch"),
                                       QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_token = bb.addButton(t("child.box_token_only"),
                                      QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.launch = True
        self.btn_launch.clicked.connect(lambda: self._done(True))
        self.btn_token.clicked.connect(lambda: self._done(False))
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _done(self, launch):
        if not self.name.text().strip():
            self.name.setFocus()
            return
        self.launch = launch
        self.accept()

    def values(self):
        v = {"label": self.name.text().strip(), "search": self.search.isChecked(),
             "launch": self.launch}
        if self.model.currentData() == "__own__":
            v.update(base_url=self.url.text().strip(),
                     model_id=self.model_id.text().strip(),
                     api_key=self.key.text().strip())
        else:
            v["model"] = self.model.currentData() or ""
        return v


class _CopyableResult(QDialog):

    def __init__(self, parent, title, text):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(620)
        self.setStyleSheet("QDialog { background:#1e293b; }")
        lay = QVBoxLayout(self)
        box = QTextEdit()
        box.setReadOnly(True)
        box.setPlainText(text)
        box.setStyleSheet("QTextEdit { background:#0f172a; color:#e2e8f0; "
                          "border:1px solid #334155; border-radius:6px; "
                          "padding:8px; font-family:monospace; }")
        lay.addWidget(box)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        cp = bb.addButton(t("child.box_copy"), QDialogButtonBox.ButtonRole.ActionRole)
        from PyQt6.QtWidgets import QApplication
        cp.clicked.connect(lambda: QApplication.clipboard().setText(text))
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class _ContactDelegate(QStyledItemDelegate):

    _PALETTE = ["#6366f1", "#8b5cf6", "#ec4899", "#f59e0b", "#10b981",
                "#06b6d4", "#ef4444", "#3b82f6", "#14b8a6", "#f97316"]

    def _avatar_color(self, handle):
        return QColor(self._PALETTE[sum(ord(c) for c in handle) % len(self._PALETTE)])

    def sizeHint(self, option, index):
        return QSize(0, 64)

    def paint(self, painter, option, index):
        c = index.data(Qt.ItemDataRole.UserRole)
        if not c:
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)

        card = QRect(r.left() + 6, r.top() + 4, r.width() - 12, r.height() - 8)
        path = QPainterPath()
        path.addRoundedRect(QPointF(card.topLeft()).x(), QPointF(card.topLeft()).y(),
                            card.width(), card.height(), 12, 12)
        if selected:
            painter.fillPath(path, QColor("#334155"))
        elif hover:
            painter.fillPath(path, QColor("#273244"))
        else:
            painter.fillPath(path, QColor("#1e293b"))

        handle = c.get("handle", "?")
        online = c.get("online")
        status = c.get("status", "")
        kind = c.get("kind")
        local = bool(c.get("local"))

        av_d = 40
        av = QRect(card.left() + 12, card.top() + (card.height() - av_d) // 2, av_d, av_d)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor("#0f172a")))
        painter.drawEllipse(av)
        _ring = QPen(self._avatar_color(handle))
        _ring.setWidth(2)
        painter.setPen(_ring)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(av.adjusted(1, 1, -1, -1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QColor("#e2e8f0"))
        f = QFont(); f.setPointSize(15); f.setBold(True); painter.setFont(f)
        painter.drawText(av, Qt.AlignmentFlag.AlignCenter,
                         (c.get("avatar") or "").strip()[:2] or handle[:1].upper())

        if not local:
            dot_d = 13
            dot = QRect(av.right() - dot_d + 2, av.bottom() - dot_d + 2, dot_d, dot_d)
            painter.setBrush(QBrush(QColor("#1e293b")))
            painter.drawEllipse(dot.adjusted(-2, -2, 2, 2))
            painter.setBrush(QBrush(QColor("#22c55e" if online else "#64748b")))
            painter.drawEllipse(dot)

        tx = av.right() + 14
        painter.setPen(QColor("#f1f5f9"))
        f2 = QFont(); f2.setPointSize(11); f2.setBold(True); painter.setFont(f2)
        name = handle + ("   🧒" if local else ("   👶" if kind == "child" else ""))
        painter.drawText(tx, card.top() + 24, name)

        f3 = QFont(); f3.setPointSize(9); f3.setBold(False); painter.setFont(f3)
        if local:
            painter.setPen(QColor("#a5b4fc"))
            folders = c.get("notes_folders") or []
            sub = (t("fed.local_child") + " · "
                   + (t("fed.child_scope", folders=", ".join(folders[:3]))
                      if folders else t("fed.child_scope_all")))
        elif status == "pending":
            painter.setPen(QColor("#fbbf24"))
            sub = t("fed.pending")
        elif status == "pending_incoming":
            painter.setPen(QColor("#22c55e"))
            sub = t("fed.pending_incoming")
        else:
            painter.setPen(QColor("#94a3b8"))
            sub = t("fed.online") if online else t("fed.offline")
        painter.drawText(tx, card.top() + 42, sub)

        perms = c.get("permissions") or {}
        _ICON = {"notas": "N", "proyectos": "P", "citas": "C", "sesiones": "S"}
        _LVL = {"auto": ("#132e21", "#6ee7b7"), "review": ("#2b2113", "#fcd34d")}
        pills = []
        for rt, icon in _ICON.items():
            lv = ((perms.get(rt) or {}).get("level")) or "no"
            if lv in _LVL:
                bg, fg = _LVL[lv]
                pills.append((icon, bg, fg))
        px = card.right() - 14
        f4 = QFont(); f4.setPointSize(9); f4.setBold(True); painter.setFont(f4)
        for icon, bg, fg in reversed(pills):
            pw = 24
            pill = QRect(px - pw, card.top() + (card.height() - 22) // 2, pw, 22)
            pp = QPainterPath()
            pp.addRoundedRect(pill.x(), pill.y(), pill.width(), pill.height(), 11, 11)
            painter.fillPath(pp, QColor(bg))
            painter.setPen(QColor(fg))
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, icon)
            px -= pw + 6

        painter.restore()


class FederationWidget(QWidget):
    open_session_requested = pyqtSignal(str)

    @property
    def _mcp_url(self):
        win = self.window()
        url = getattr(win, "_mcp_url", None) if win is not self else None
        return url or "http://127.0.0.1:9077"

    def __init__(self, mcp_call, models_fn=None):
        super().__init__()
        self._mcp_call = mcp_call
        self._models_fn = models_fn or (lambda: [])
        self._contacts = []
        self._workers = []
        self._build_ui()
        self.refresh()
        self._auto = QTimer(self)
        self._auto.setInterval(5000)
        self._auto.timeout.connect(self._auto_refresh)
        self._auto.start()

    def _auto_refresh(self):
        if not self.isVisible():
            return
        if self._workers:
            return
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        self.header = QLabel(t("fed.loading_identity"))
        self.header.setWordWrap(True)
        self.header.setStyleSheet(
            "QLabel { background:#0f172a; color:#e2e8f0; padding:12px 14px; "
            "border-radius:8px; font-size:13px; }")
        root.addWidget(self.header)
        self.plan = QLabel("")
        self.plan.setWordWrap(True)
        self.plan.setVisible(False)
        self.plan.setStyleSheet(
            "QLabel { background:#0f172a; color:#cbd5e1; padding:10px 14px; "
            "border-radius:8px; font-size:13px; }")
        root.addWidget(self.plan)
        self._quota_at = 0.0

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            "QTabWidget::pane { border:none; background:transparent; top:0px; }"
            "QTabBar::tab { background:transparent; color:#94a3b8; "
            "padding:10px 16px; font-weight:600; font-size:12px; "
            "border:none; border-bottom:2px solid transparent; }"
            "QTabBar::tab:selected { background:#1e293b; color:#10b981; "
            "border-bottom:2px solid #10b981; }"
            "QTabBar::tab:hover { color:#f1f5f9; "
            "background:rgba(51,65,85,0.5); }")
        root.addWidget(self.tabs, 1)

        page_contacts = QWidget()
        contacts_lay = QVBoxLayout(page_contacts)
        contacts_lay.setContentsMargins(10, 10, 10, 10)
        contacts_lay.setSpacing(10)
        self.tabs.addTab(page_contacts, t("fed.tab_contacts"))

        bar = QHBoxLayout()
        title = QLabel(t("fed.contacts"))
        title.setStyleSheet("QLabel { color:#e2e8f0; font-weight:bold; font-size:15px; }")
        bar.addWidget(title)
        bar.addStretch(1)
        add_btn = QPushButton("+ " + t("fed.add"))
        add_btn.setStyleSheet(self._btn_css())
        add_btn.clicked.connect(self._on_add)
        bar.addWidget(add_btn)
        add_child_btn = QPushButton("+ 🧒 " + t("child.add"))
        add_child_btn.setStyleSheet(self._btn_css())
        add_child_btn.clicked.connect(self._on_add_child)
        bar.addWidget(add_child_btn)
        box_child_btn = QPushButton("+ 👶 " + t("child.box_add"))
        box_child_btn.setStyleSheet(self._btn_css())
        box_child_btn.clicked.connect(self._on_add_box_child)
        bar.addWidget(box_child_btn)
        refresh_btn = QPushButton(t("fed.refresh"))
        refresh_btn.setStyleSheet(self._btn_css())
        refresh_btn.clicked.connect(self.refresh)
        bar.addWidget(refresh_btn)
        leave_btn = QPushButton("🚪 " + t("fed.leave_fed"))
        leave_btn.setStyleSheet(self._btn_css("danger"))
        leave_btn.clicked.connect(self._on_leave)
        bar.addWidget(leave_btn)
        contacts_lay.addLayout(bar)

        _SCROLL = ("QScrollBar:vertical { background:transparent; width:8px; "
                   "margin:0; }"
                   "QScrollBar::handle:vertical { background:#334155; "
                   "border-radius:4px; min-height:28px; }"
                   "QScrollBar::handle:vertical:hover { background:#475569; }"
                   "QScrollBar::add-line:vertical, "
                   "QScrollBar::sub-line:vertical { height:0; border:none; }"
                   "QScrollBar::add-page:vertical, "
                   "QScrollBar::sub-page:vertical { background:transparent; }")
        self._scroll_css = _SCROLL
        self.list = QListWidget()
        self.list.setStyleSheet(
            "QListWidget { background:transparent; border:none; outline:none; }"
            + _SCROLL)
        self.list.setItemDelegate(_ContactDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.setSpacing(0)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_context_menu)
        self.list.itemDoubleClicked.connect(lambda it: self._edit_permissions(
            it.data(Qt.ItemDataRole.UserRole)))
        contacts_lay.addWidget(self.list, 1)

        hint = QLabel(t("fed.list_hint"))
        hint.setStyleSheet("QLabel { color:#64748b; font-size:11px; }")
        contacts_lay.addWidget(hint)

        page_act = QWidget()
        act_lay = QVBoxLayout(page_act)
        act_lay.setContentsMargins(10, 10, 10, 10)
        act_lay.setSpacing(10)
        self.tabs.addTab(page_act, t("fed.tab_activity"))

        act_bar = QHBoxLayout()
        act_title = QLabel(t("fed.activity_title"))
        act_title.setStyleSheet(
            "QLabel { color:#e2e8f0; font-weight:bold; font-size:15px; }")
        act_bar.addWidget(act_title)
        act_bar.addStretch(1)
        self.act_clear_btn = QPushButton("🗑 " + t("fed.activity_clear"))
        self.act_clear_btn.setStyleSheet(self._btn_css("danger"))
        self.act_clear_btn.clicked.connect(self._on_activity_clear)
        act_bar.addWidget(self.act_clear_btn)
        act_lay.addLayout(act_bar)

        self.activity = QListWidget()
        self.activity.setStyleSheet(
            "QListWidget { background:transparent; border:none; outline:none; "
            "color:#e2e8f0; font-size:12.5px; }"
            "QListWidget::item { padding:7px 4px; "
            "border-bottom:1px solid #1e293b; }" + self._scroll_css)
        act_lay.addWidget(self.activity, 1)

        act_hint = QLabel(t("fed.activity_hint"))
        act_hint.setWordWrap(True)
        act_hint.setStyleSheet("QLabel { color:#64748b; font-size:11px; }")
        act_lay.addWidget(act_hint)

    _CHIP = ("QPushButton { background:#1e293b; color:#cbd5e1; "
             "border:1px solid #334155; border-radius:8px; padding:6px 14px; "
             "font-size:12px; font-weight:600; }"
             "QPushButton:hover { background:#334155; color:#e2e8f0; }"
             "QPushButton:disabled { background:#0f172a; color:#475569; }")
    _CHIP_DANGER = ("QPushButton { background:#3f1d1d; color:#fecaca; "
                    "border:1px solid #7f1d1d; border-radius:8px; "
                    "padding:6px 14px; font-size:12px; font-weight:600; }"
                    "QPushButton:hover { background:#7f1d1d; color:#fee2e2; }"
                    "QPushButton:disabled { background:#0f172a; color:#475569; }")

    def _btn_css(self, kind="plain"):
        return self._CHIP_DANGER if kind == "danger" else self._CHIP

    def _run(self, tool, args, cb):
        w = _McpWorker(self._mcp_call, tool, args)
        w.done.connect(cb)
        w.done.connect(lambda *_: self._workers.remove(w) if w in self._workers else None)
        self._workers.append(w)
        w.start()

    def _on_leave(self):
        if QMessageBox.question(
                self, t("fed.leave_fed_title"), t("fed.leave_fed_msg"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._run("fed_leave", {}, self._on_leave_done)

    def _on_leave_done(self, data):
        if isinstance(data, dict) and data.get("needs_force_local"):
            if QMessageBox.question(
                    self, t("fed.leave_fed_title"), t("fed.leave_fed_force"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
                self._run("fed_leave", {"force_local": True}, self._on_leave_done)
            return
        if isinstance(data, dict) and data.get("error"):
            QMessageBox.warning(self, t("fed.leave_fed_title"), data["error"])
            return
        QMessageBox.information(self, t("fed.leave_fed_title"), t("fed.leave_fed_done"))
        self._contacts = []
        self.refresh()

    def refresh(self):
        if not self.header.text() or getattr(self, "_never_loaded", True):
            self.header.setText(t("fed.loading_identity"))
        self._run("fed_status", {}, self._on_status)
        import time as _time
        if _time.time() - self._quota_at >= 60:
            self._quota_at = _time.time()
            self._run("fed_quota", {}, self._on_quota)
        self._run("fed_list_contacts", {}, self._on_contacts)
        self._run("fed_list_appointment_bounces", {}, self._on_bounces)
        self._run("fed_activity", {"limit": 200}, self._on_activity)

    _ACT_ICON = {"commits": "⎇", "note_add": "📝", "note_edit": "✎",
                 "note_del": "🗑", "task_add": "☑", "task_edit": "✎",
                 "task_del": "🗑", "board_add": "🧭", "board_edit": "✎",
                 "board_del": "🗑", "child_task": "🧒"}

    def _on_activity(self, data):
        if not hasattr(self, "activity"):
            return
        groups = data.get("groups") or []
        snap = json.dumps([data.get("error"), groups],
                          sort_keys=True, default=str)
        if snap == getattr(self, "_activity_snapshot", None):
            return
        self._activity_snapshot = snap
        self.activity.clear()
        if data.get("error"):
            it = QListWidgetItem(f"⚠ {data['error']}")
            it.setForeground(QColor("#f87171"))
            self.activity.addItem(it)
            return
        if not groups:
            it = QListWidgetItem(t("fed.activity_empty"))
            it.setForeground(QColor("#64748b"))
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.activity.addItem(it)
            return
        for g in groups:
            n = int(g.get("n") or 1)
            kind = str(g.get("kind") or "")
            what = t(f"fed.act_{kind}" + ("_1" if n == 1 else ""), n=n)
            _res = g.get("resource", "")
            line = t("fed.activity_row" if _res else "fed.activity_row_nores",
                     who=g.get("peer", "?"), resource=_res, what=what,
                     when=str(g.get("ts", ""))[:16].replace("T", " "))
            titles = [x for x in (g.get("titles") or []) if x]
            if titles:
                line += "\n    " + " · ".join(f"«{x}»" for x in titles[:3])
            it = QListWidgetItem(f"{self._ACT_ICON.get(kind, '•')}  {line}")
            self.activity.addItem(it)

    def _on_activity_clear(self):
        if QMessageBox.question(
                self, t("fed.activity_clear"), t("fed.activity_clear_confirm"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._run("fed_activity_clear", {}, self._on_activity_cleared)

    def _on_activity_cleared(self, data):
        if data.get("error"):
            QMessageBox.warning(self, t("fed.activity_clear"), data["error"])
            return
        QMessageBox.information(
            self, t("fed.activity_clear"),
            t("fed.activity_cleared", n=data.get("deleted", 0)))
        self._run("fed_activity", {"limit": 200}, self._on_activity)

    def _on_bounces(self, data):
        if data.get("error"):
            return
        bounces = data.get("bounces") or []
        if not bounces:
            return
        who = ", ".join(b.get("from", "?") for b in bounces)
        QMessageBox.information(self, t("fed.appt_bounce_title"),
                                t("fed.appt_bounce_msg", who=who))

    def _on_status(self, data):
        self._never_loaded = False
        def _set(html):
            if self.header.text() != html:
                self.header.setText(html)
        if data.get("error"):
            _set(f"⚠ {data['error']}")
            return
        if not data.get("registered"):
            _set(t("fed.no_identity"))
            return
        sess = data.get("session")
        sess_txt = (t("fed.session_active") if sess == "ok"
                    else t("fed.session_expired") if sess == "token_invalido"
                    else t("fed.session_none"))
        self.header.setText(
            f"<b>{data.get('handle')}</b>  ·  {sess_txt}<br>"
            f"<span style='color:#64748b'>{t('fed.fingerprint')} "
            f"{data.get('fingerprint')} · {data.get('server_url')}</span>")

    @staticmethod
    def _size(n):
        n = float(n or 0)
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1000 or unit == "GB":
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}".replace(".0 ", " ")
            n /= 1000

    def _on_quota(self, data):
        lim = (data or {}).get("limits") or {}
        use = (data or {}).get("usage") or {}
        if (data or {}).get("error") or not (lim or use):
            self.plan.setVisible(False)
            return
        rows = [("fed.plan_sync", use.get("sync_bytes"), lim.get("max_sync_bytes"), True),
                ("fed.plan_outbox", use.get("outbox_bytes"), lim.get("max_inbox_bytes"), True),
                ("fed.plan_inbox", use.get("inbox_envelopes"), lim.get("max_inbox_envelopes"), False),
                ("fed.plan_contacts", use.get("contacts"), lim.get("max_contacts"), False),
                ("fed.plan_children", use.get("children"), lim.get("max_children"), False)]
        parts = []
        for key, u, l, size in rows:
            u = int(u or 0)
            l = int(l or 0)
            frac = (u / float(l)) if l else 0.0
            col = "#ef4444" if frac >= 1 else "#f59e0b" if frac >= 0.8 else "#cbd5e1"
            cap = (self._size(l) if size else str(l)) if l else "∞"
            val = f"{self._size(u) if size else u} / {cap}"
            parts.append(f"{t(key)} <span style='color:{col}'>{val}</span>")
        html = (f"{t('fed.plan_tier', tier=data.get('tier') or '?')}  ·  "
                + "  ·  ".join(parts))
        if self.plan.text() != html:
            self.plan.setText(html)
        self.plan.setToolTip(t("fed.plan_tip"))
        self.plan.setVisible(True)

    def _on_contacts(self, data):
        self._contacts = data.get("contacts", [])
        _ws = data.get("ws") or {}
        _ws_down = (not _ws.get("up")) and _ws.get("phase") not in (None, "idle")
        snap = json.dumps([data.get("error"), _ws_down, _ws.get("phase"),
                           self._contacts], sort_keys=True, default=str)
        if snap == getattr(self, "_contacts_snapshot", None):
            return
        self._contacts_snapshot = snap
        self.list.clear()
        if data.get("error"):
            self.list.addItem(QListWidgetItem(f"⚠ {data['error']}"))
            return
        if _ws_down:
            warn = QListWidgetItem("⚠ " + t("fed.ws_down",
                                            phase=str(_ws.get("phase")),
                                            err=str(_ws.get("error") or "")))
            warn.setForeground(QColor("#f5b800"))
            warn.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(warn)
        if not self._contacts:
            empty = QListWidgetItem("🤖   " + t("fed.no_contacts"))
            empty.setForeground(QColor("#64748b"))
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(empty)
            return
        for c in self._contacts:
            self.list.addItem(self._make_item(c))

    def _make_item(self, c):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, c)
        item.setSizeHint(QSize(0, 64))
        return item

    def _on_context_menu(self, pos):
        item = self.list.itemAt(pos)
        if not item:
            return
        c = item.data(Qt.ItemDataRole.UserRole)
        if not c:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background:#1e293b; color:#e2e8f0; border:1px solid #334155; "
            "border-radius:6px; padding:4px; }"
            "QMenu::item { padding:8px 24px; border-radius:4px; }"
            "QMenu::item:selected { background:#3b82f6; }")

        if c.get("local"):
            act_session = QAction("💬 " + t("fed.open_session"), self)
            act_session.triggered.connect(
                lambda: self.open_session_requested.emit(c.get("handle", "")))
            menu.addAction(act_session)
            act_edit = QAction("✎ " + t("child.edit_menu"), self)
            act_edit.triggered.connect(lambda: self.edit_local_child(c))
            menu.addAction(act_edit)
            menu.addSeparator()
            act_del = QAction("🗑 " + t("fed.delete_child_local"), self)
            act_del.triggered.connect(lambda: self._delete_local_child(c))
            menu.addAction(act_del)
            menu.exec(self.list.mapToGlobal(pos))
            return

        if c.get("status") == "pending_incoming":
            act_accept = QAction("✓ " + t("fed.accept"), self)
            act_accept.triggered.connect(lambda: self._accept(c))
            menu.addAction(act_accept)
        elif c.get("status") == "pending":
            act_wait = QAction("⏳ " + t("fed.pending"), self)
            act_wait.setEnabled(False)
            menu.addAction(act_wait)
        else:
            act_session = QAction("💬 " + t("fed.open_session"), self)
            act_session.triggered.connect(
                lambda: self.open_session_requested.emit(c.get("handle", "")))
            menu.addAction(act_session)
            act_pub = QAction("🌍 " + t("fed.browse_public"), self)
            act_pub.triggered.connect(lambda: self._browse_public(c))
            menu.addAction(act_pub)
            menu.addSeparator()

        act_perms = QAction("⚙ " + t("fed.edit_perms"), self)
        act_perms.triggered.connect(lambda: self._edit_permissions(c))
        menu.addAction(act_perms)

        menu.addSeparator()
        is_child = c.get("kind") == "child"
        rm_label = ("🗑 " + t("fed.revoke_child")) if is_child else ("🗑 " + t("fed.remove"))
        act_remove = QAction(rm_label, self)
        act_remove.triggered.connect(lambda: self._remove(c))
        menu.addAction(act_remove)

        menu.exec(self.list.mapToGlobal(pos))

    def _browse_public(self, c):
        handle = c.get("handle", "")

        def done(data):
            pubs = (data or {}).get("public_folders") or []
            dlg = QDialog(self)
            dlg.setWindowTitle("🌍 " + t("fed.public_dlg_title", handle=handle))
            dlg.setMinimumSize(420, 300)
            lay = QVBoxLayout(dlg)
            lst = QListWidget()
            if not pubs:
                lst.addItem(t("fed.public_none"))
                lst.setEnabled(False)
            for p in pubs:
                it = QListWidgetItem(f"📂 {p.get('folder_name', '?')}")
                it.setData(Qt.ItemDataRole.UserRole, p)
                lst.addItem(it)
            lay.addWidget(lst, 1)
            btns = QDialogButtonBox()
            sub = btns.addButton(t("fed.subscribe"),
                                 QDialogButtonBox.ButtonRole.AcceptRole)
            btns.addButton(QDialogButtonBox.StandardButton.Close)
            sub.setEnabled(bool(pubs))
            lay.addWidget(btns)

            def do_sub():
                it = lst.currentItem()
                p = it.data(Qt.ItemDataRole.UserRole) if it else None
                if not p:
                    return
                self._run("fed_subscribe_folder",
                          {"handle": handle, "folder_id": p.get("folder_id")},
                          lambda r: QMessageBox.information(
                              self, "🌍",
                              t("fed.subscribed_ok",
                                folder=p.get("folder_name", "?"),
                                handle=handle)
                              if not (r or {}).get("error")
                              else str(r.get("error"))[:200]))
                dlg.accept()
            sub.clicked.connect(do_sub)
            btns.rejected.connect(dlg.reject)
            dlg.exec()

        self._run("fed_public_folders", {"handle": handle}, done)

    def _on_add_child(self):
        dlg = _LocalChildDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        QMessageBox.information(self, t("child.add_title"),
                                t("child.add_designing"))
        w = _PostWorker(f"{self._mcp_url}/child/design", dlg.values(), 300)
        w.done.connect(self._on_child_designed)
        w.done.connect(lambda *_: self._workers.remove(w) if w in self._workers else None)
        self._workers.append(w)
        w.start()

    def _on_child_designed(self, data):
        data = data if isinstance(data, dict) else {}
        if data.get("error") or not data.get("child"):
            QMessageBox.warning(self, t("child.add_title"),
                                data.get("error") or json.dumps(data)[:400])
            return
        self.refresh()
        self.edit_local_child({"handle": data["child"]["name"], "local": True})

    def _on_add_box_child(self):
        dlg = _BoxChildDialog(self, self._pool_models())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        if v["launch"]:
            QMessageBox.information(self, t("child.box_title"),
                                    t("child.box_starting"))
        self._run("fed_spawn_child", v, self._on_box_child)

    def _on_box_child(self, data):
        data = data if isinstance(data, dict) else {}
        if data.get("error"):
            QMessageBox.warning(self, t("child.box_title"), data["error"])
            return
        _CopyableResult(self, t("child.box_title"),
                        data.get("message") or json.dumps(data, indent=1)).exec()
        self.refresh()

    def _on_add(self):
        dlg = _AddContactDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        handle, trust = dlg.values()
        if not handle:
            return
        self._run("fed_add_contact", {"handle": handle, "trust_level": trust},
                  self._on_added)

    def _on_added(self, data):
        if data.get("error"):
            QMessageBox.warning(self, t("fed.msg_add_title"), data["error"])
            return
        QMessageBox.information(self, t("fed.msg_add_title"),
                               data.get("message", t("fed.request_sent")))
        self.refresh()

    def _accept(self, c):
        self._run("fed_accept_contact", {"handle": c["handle"]}, self._on_generic)

    def _remove(self, c):
        handle = c["handle"]
        is_child = c.get("kind") == "child"
        title = t("fed.revoke_child") if is_child else t("fed.remove")
        msg = (t("fed.revoke_child_confirm", handle=handle) if is_child
               else t("fed.remove_confirm", handle=handle))
        r = QMessageBox.question(self, title, msg)
        if r != QMessageBox.StandardButton.Yes:
            return
        tool = "fed_revoke_child" if is_child else "fed_remove_contact"
        self._run(tool, {"handle": handle}, self._on_removed)

    def _on_removed(self, data):
        if data.get("error"):
            QMessageBox.warning(self, t("fed.remove"), data["error"])
        self.refresh()

    def edit_local_child(self, c):
        name = c.get("handle", "")
        kids = (self._sync("child_list", {}) or {}).get("children") or []
        pack = next((k for k in kids if k.get("name") == name), None)
        if pack is None:
            QMessageBox.warning(self, t("child.edit_menu"),
                                t("child.edit_gone", name=name))
            self.refresh()
            return
        folders = (self._sync("list_categories", {}) or {}).get("categories") or []
        projects = (self._sync("list_todo_projects", {}) or {}).get("projects") or []
        nodes = [c.get("handle") for c in
                 ((self._sync("fed_list_children", {}) or {}).get("children") or [])
                 if isinstance(c, dict) and c.get("handle")]
        dlg = _ChildPackDialog(self, pack, folders, self._pool_models(),
                               projects, nodes)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        res = self._sync("child_teach", {"name": name, **v}) or {}
        if res.get("error"):
            QMessageBox.warning(self, t("child.edit_menu"), res["error"])
        elif res.get("warning"):
            QMessageBox.information(self, t("child.edit_menu"), res["warning"])
        self.refresh()

    def _pool_models(self):
        try:
            return [m for m in (self._models_fn() or []) if m]
        except Exception:
            return []

    def _sync(self, tool, args):
        try:
            raw = self._mcp_call(tool, args)
            if isinstance(raw, str):
                raw = json.loads(raw)
            return raw if isinstance(raw, dict) else {}
        except Exception as e:
            return {"error": str(e)}

    def _delete_local_child(self, c):
        name = c.get("handle", "")
        if QMessageBox.question(
                self, t("fed.delete_child_local"),
                t("fed.delete_child_confirm", name=name),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._run("child_delete", {"name": name}, self._on_removed)

    def _edit_permissions(self, c):
        if not c:
            return
        dlg = _PermissionsDialog(self, c)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        perms = dlg.get_perms()
        self._perm_pending = len(perms)
        self._perm_error = None
        for rt, p in perms.items():
            self._run("fed_set_permissions",
                      {"handle": c["handle"], "resource_type": rt, "perms": p},
                      self._on_perm_saved)

    def _on_perm_saved(self, data):
        if data.get("error"):
            self._perm_error = data["error"]
        self._perm_pending = getattr(self, "_perm_pending", 1) - 1
        if self._perm_pending <= 0:
            if self._perm_error:
                QMessageBox.warning(self, t("fed.msg_perms_title"), self._perm_error)
            else:
                QMessageBox.information(self, t("fed.msg_perms_title"), t("fed.perms_updated"))
            self.refresh()

    def _on_generic(self, data):
        if data.get("error"):
            QMessageBox.warning(self, t("fed.msg_perms_title"), data["error"])
            return
        QMessageBox.information(self, t("fed.msg_perms_title"),
                               data.get("message", t("fed.perms_updated")))
        self.refresh()
