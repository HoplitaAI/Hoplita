import html as _h

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPlainTextEdit,
                             QSizePolicy, QSplitter, QToolButton, QVBoxLayout,
                             QWidget)

from hoplita_i18n import t

INK = "#e6edf7"
LABEL = "#8296b1"
STAMP = "#64748b"
BED = "#0b1220"
PANEL = "#111a2b"
LINE = "#1f2a44"
ERROR = "#f87171"


class _Pane(QWidget):

    def __init__(self, title, empty, mono, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.title = QLabel(title.upper())
        self.title.setStyleSheet(f"color:{LABEL}; font-size:11px; font-weight:600; "
                                 "letter-spacing:1px;")
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFrameShape(QFrame.Shape.NoFrame)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.text.setPlaceholderText(empty)
        self.text.setStyleSheet(
            f"QPlainTextEdit {{ background:{BED}; color:{INK}; border:1px solid {LINE}; "
            "border-radius:6px; padding:6px; }")
        f = QFont()
        if mono:
            f.setFamilies(["DejaVu Sans Mono", "Menlo", "Consolas", "monospace"])
            f.setPixelSize(11)
        else:
            f.setPixelSize(12)
        self.text.setFont(f)
        lay.addWidget(self.title)
        lay.addWidget(self.text, 1)

    def clear(self):
        self.text.clear()

    def add(self, html_line):
        sb = self.text.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 2
        self.text.appendHtml(html_line)
        if at_bottom:
            sb.setValue(sb.maximum())

    def plain(self):
        return self.text.toPlainText()

    def html(self):
        return self.text.document().toHtml()


class TurnView(QWidget):

    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("turnView")
        self.setMinimumWidth(300)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.setAutoFillBackground(True)
        self.setStyleSheet(f"#turnView {{ background:{PANEL}; }}")
        self._rid = 0
        self._shown = 0
        self._tool_lines = 0
        self._rounds = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 10, 12, 10)
        lay.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.heading = QLabel("")
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setStyleSheet(f"color:{INK}; font-size:13px; font-weight:600;")
        self.close_button = QToolButton()
        self.close_button.setText("✕")
        self.close_button.setAutoRaise(True)
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setToolTip(t("tu.hide"))
        self.close_button.setStyleSheet(
            f"QToolButton {{ color:{LABEL}; border:none; font-size:13px; padding:2px 6px; }} "
            f"QToolButton:hover {{ color:{INK}; }}")
        self.close_button.clicked.connect(self.closed.emit)
        head.addWidget(self.heading, 1)
        head.addWidget(self.close_button)
        lay.addLayout(head)
        self.split = QSplitter(Qt.Orientation.Vertical)
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(8)
        self.split.setStyleSheet(f"QSplitter::handle {{ background:{PANEL}; }}")
        self.reasoning = _Pane(t("tu.reasoning"), t("tu.reasoning_none"), mono=False)
        self.tools = _Pane(t("tu.card_title", n=0), t("tu.tools_none"), mono=True)
        self.split.addWidget(self.reasoning)
        self.split.addWidget(self.tools)
        self.split.setStretchFactor(0, 2)
        self.split.setStretchFactor(1, 3)
        lay.addWidget(self.split, 1)

    def sizeHint(self):
        return QSize(520, 520)

    def rid(self):
        return self._rid

    def lines_shown(self):
        return self._shown

    def show_turn(self, rid, rec, heading):
        self._rid = int(rid)
        self._shown = 0
        self._tool_lines = 0
        self._rounds = 0
        self.reasoning.clear()
        self.tools.clear()
        self.follow(rec, heading)
        if rec.get("done"):
            for pane in (self.reasoning, self.tools):
                pane.text.moveCursor(QTextCursor.MoveOperation.Start)
                pane.text.verticalScrollBar().setValue(0)

    def follow(self, rec, heading):
        lines = list(rec.get("lines") or [])
        for ts, kind, text in lines[self._shown:]:
            self._add(ts, kind, text)
        self._shown = len(lines)
        self.heading.setText(heading)
        self.tools.title.setText(t("tu.card_title", n=self._tool_lines).upper())

    def _add(self, ts, kind, text):
        stamp = f'<span style="color:{STAMP}">[{_h.escape(str(ts))}]</span> '
        kind = str(kind)
        if kind.startswith("thought"):
            try:
                n = int(kind.split("/", 1)[1])
            except (IndexError, ValueError):
                n = len(str(text))
            self._rounds += 1
            label = _h.escape(t("tu.think", n=n))
            if not text:
                self.reasoning.add(f'{stamp}<span style="color:{LABEL}">{label}'
                                   f'{_h.escape(t("tu.think_gone"))}</span>')
                return
            self.reasoning.add(f'{stamp}<span style="color:{LABEL}">{label}</span>')
            self.reasoning.add(f'<span style="color:{INK};white-space:pre-wrap">'
                               f'{_h.escape(str(text)).replace(chr(10), "<br>")}</span><br>')
            return
        self._tool_lines += 1
        col = ERROR if kind == "error" else INK
        self.tools.add(f'{stamp}<span style="color:{col};white-space:pre-wrap">'
                       f'{_h.escape(str(text)).replace(chr(10), "<br>")}</span>')
