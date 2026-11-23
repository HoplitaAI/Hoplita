#!/usr/bin/env python3

import os
import platform
import sys

for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

import configparser
import hashlib
import subprocess

try:
    from hoplita_i18n import t, load_language
except ImportError:
    def t(key, **kwargs):
        text = key.split(".")[-1] if "." in key else key
        if kwargs:
            try: return text.format(**kwargs)
            except: pass
        return text
    def load_language(lang=None, lang_dir=None): pass

import base64
import difflib
import json
import re
import threading
import time
from datetime import datetime
from typing import Dict, List, Set

from hoplita_search import find_passages
import requests

try:
    import markdown as _markdown_lib
    _MARKDOWN_OK = True
except ImportError:
    _MARKDOWN_OK = False

_CODE_STYLE = "github-dark"
_CODE_BG = "#0f172a"
_CODE_HEAD_BG = "#111d33"
_CODE_BORDER = "#334155"

_MD_CELL = re.compile(r'<(th|td)(?: style="text-align: ?(left|right|center);?")?>')


def _style_md_tables(html):
    def cell(m):
        tag, align = m.group(1), m.group(2) or "left"
        bg = f' bgcolor="{_CODE_HEAD_BG}"' if tag == "th" else ""
        color = "color:#f1f5f9;" if tag == "th" else ""
        return (f'<{tag} align="{align}" valign="top"{bg} style="{color}'
                f'border:1px solid {_CODE_BORDER};padding:5px 10px;">')
    html = html.replace("<table>", '<table cellspacing="0" cellpadding="0" '
                        'style="border-collapse:collapse;margin:6px 0;">')
    return _MD_CELL.sub(cell, html)
_CODE_FG = "#e6edf7"

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QTextEdit, QTabWidget,
    QSplitter, QComboBox, QSlider, QSpinBox, QListWidget, QFrame,
    QListWidgetItem, QDialog, QDialogButtonBox, QMessageBox, QPlainTextEdit,
    QFileDialog, QScrollArea, QCalendarWidget,
    QSystemTrayIcon, QMenu, QInputDialog,
    QGroupBox, QFormLayout, QCheckBox, QTreeWidget, QTreeWidgetItem,
    QStackedWidget, QTabBar, QStyle, QStyleOptionButton
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QObject, QTimer, QPoint, QPropertyAnimation, QEasingCurve, QSettings
)

_SESS_CARD_CSS = (
    "#sesscard{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
    "stop:0 #1b2434, stop:1 #131a29);border:1px solid %s;"
    "border-radius:11px;}"
    "#sesscard:hover{border-color:#3b82f6;}")
if sys.platform == "darwin" and os.environ.get("XDG_CONFIG_HOME"):
    from PyQt6.QtCore import QSettings as _QS
    _QS.setDefaultFormat(_QS.Format.IniFormat)
    _QS.setPath(_QS.Format.IniFormat, _QS.Scope.UserScope, os.environ["XDG_CONFIG_HOME"])
from PyQt6.QtGui import (
    QFont, QColor, QTextCharFormat, QTextCursor, QIcon,
    QPixmap, QAction, QPainter,
    QShortcut, QKeySequence, QSyntaxHighlighter,
    QLinearGradient, QFontMetrics, QPen, QBrush, QTextOption
)

HOPLITA_VERSION = "1.0"
try:
    import hoplita_version as _hver
    HOPLITA_LABEL = _hver.label()
    HOPLITA_LABEL_LONG = _hver.long_label()
except Exception:
    HOPLITA_LABEL = HOPLITA_LABEL_LONG = f"v{HOPLITA_VERSION}"
IS_WINDOWS = platform.system() == "Windows"



TASK_PRIORITIES = ("Alta", "Media", "Baja")
APT_URGENCY_KEYS = ("low", "normal", "high", "critical")

_APT_URGENCY_LEGACY = {
    "critical": "critical", "Critical": "critical", "Critica": "critical", "Crítica": "critical",
    "high": "high", "High": "high", "Alta": "high",
    "normal": "normal", "medium": "normal", "Medium": "normal", "Normal": "normal", "Media": "normal",
    "low": "low", "Low": "low", "Baja": "low",
}

_TASK_PRIORITY_LEGACY = {
    "high": "Alta", "High": "Alta", "HIGH": "Alta",
    "medium": "Media", "Medium": "Media", "MEDIUM": "Media",
    "normal": "Media", "Normal": "Media", "NORMAL": "Media",
    "low": "Baja", "Low": "Baja", "LOW": "Baja",
}


def normalize_task_priority(p, default="Media"):
    if p in TASK_PRIORITIES:
        return p
    return _TASK_PRIORITY_LEGACY.get(p, default)


def normalize_apt_urgency(u, default="normal"):
    if u in APT_URGENCY_KEYS:
        return u
    return _APT_URGENCY_LEGACY.get(u, default)


def urgency_label(key):
    return t(f"urgency.{normalize_apt_urgency(key)}")


def urgency_labels():
    return [t(f"urgency.{k}") for k in APT_URGENCY_KEYS]





_OFFICE_MIMES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


class RAGDocumentStore:

    def __init__(self, config):
        self.config = config
        self.chunks = []
        self.chunk_metadata = []
        self.raw_texts = {}
        self.used_chunk_ids: Set[int] = set()
        import hoplita_paths
        base = hoplita_paths.hoplita_base()
        self._meta_path = os.path.join(base, "rag_meta.json")
        self._load_from_disk()

    def _load_from_disk(self):
        try:
            if os.path.exists(self._meta_path):
                with open(self._meta_path, "r", encoding="utf-8") as _f:
                    _d = json.load(_f)
                self.chunks = _d.get("chunks", [])
                self.chunk_metadata = _d.get("chunk_metadata", [])
                self.raw_texts = _d.get("raw_texts", {})
        except Exception as e:
            print(f"[RAG] ⚠ Error loading: {e}")

    def _save_to_disk(self):
        try:
            with open(self._meta_path, "w", encoding="utf-8") as f:
                json.dump({"chunks": self.chunks, "chunk_metadata": self.chunk_metadata,
                           "raw_texts": self.raw_texts}, f, ensure_ascii=False)
        except Exception as e:
            print(f"[RAG] ⚠ Error saving: {e}")

    def add_raw_text(self, text: str, source: str, metadata: dict = None):
        source_key = hashlib.md5(source.encode()).hexdigest()[:12]
        self.raw_texts[source_key] = {
            "content": text, "source": source, "metadata": metadata or {},
            "word_count": len(text.split()), "char_count": len(text),
            "timestamp": datetime.now().isoformat(),
        }
        print(f"✅ RAW text saved: {source} ({len(text.split())} words)")
        self._save_to_disk()

    def search_in_raw_text(self, query: str, context_words: int = 300) -> List[Dict]:
        results = []
        search_terms = self.extract_search_terms(query)
        for source_key, raw_data in self.raw_texts.items():
            content = raw_data["content"].lower()
            source = raw_data["source"]
            for term in search_terms:
                term_lower = term.lower()
                if term_lower in content:
                    start = 0
                    while True:
                        pos = content.find(term_lower, start)
                        if pos == -1:
                            break
                        words = raw_data["content"].split()
                        word_pos = len(raw_data["content"][:pos].split())
                        context_start = max(0, word_pos - context_words // 2)
                        context_end = min(len(words), word_pos + context_words // 2)
                        context = " ".join(words[context_start:context_end])
                        results.append({
                            "source": source, "term": term, "context": context,
                            "position": pos, "word_position": word_pos,
                            "score": self.calculate_relevance_score(term, context, query),
                        })
                        start = pos + len(term_lower)
        results = sorted(results, key=lambda x: x["score"], reverse=True)
        unique_results = []
        for result in results:
            is_duplicate = False
            for existing in unique_results:
                if (result["source"] == existing["source"]
                        and abs(result["word_position"] - existing["word_position"]) < 100):
                    is_duplicate = True
                    break
            if not is_duplicate:
                unique_results.append(result)
        return unique_results[:5]

    def extract_search_terms(self, query: str) -> List[str]:
        stop_words = {
            "de", "la", "el", "en", "y", "a", "que", "es", "se", "no", "te", "lo",
            "le", "da", "su", "por", "son", "con", "para", "una", "sus", "ser", "muy",
            "todo", "pero", "más", "hacer", "pueden", "tiempo", "sobre", "qué",
            "sabemos", "cómo", "dónde", "cuándo",
        }
        clean_query = re.sub(r"[^\w\s]", " ", query.lower())
        words = clean_query.split()
        terms = [word for word in words if len(word) > 2 and word not in stop_words]
        phrases = []
        for i in range(len(words) - 1):
            if len(words[i]) > 2 and len(words[i + 1]) > 2:
                phrase = f"{words[i]} {words[i + 1]}"
                if not any(sw in phrase for sw in stop_words):
                    phrases.append(phrase)
        return terms + phrases

    def calculate_relevance_score(self, term, context, original_query):
        score = 0.0
        context_lower = context.lower()
        term_lower = term.lower()
        score += context_lower.count(term_lower) * 1.0
        other_terms = self.extract_search_terms(original_query)
        for other_term in other_terms:
            if other_term != term and other_term.lower() in context_lower:
                score += 0.5
        score += len(term) * 0.1
        return score

    def get_raw_texts_for_summary(self) -> List[Dict]:
        return list(self.raw_texts.values())

    def create_chunks(self, text, source="", chunk_size=200, overlap=30):
        text = re.sub(r"\n+", "\n", text.strip())
        words = text.split()
        try:
            max_chars = self.config.getint("RAG", "max_chunk_chars", fallback=1600)
        except Exception:
            max_chars = 1600
        max_chars = max(200, max_chars)
        _w = len(words) or 1
        if len(text) / _w > 12:
            max_chars = min(max_chars, 700)
        chunks = []

        def _add(chunk_text):
            chunk_text = chunk_text.strip()
            if len(chunk_text) <= 50:
                return
            chunks.append({
                "id": hashlib.md5(chunk_text.encode()).hexdigest()[:8],
                "text": chunk_text, "source": source,
                "word_count": len(chunk_text.split()),
                "char_count": len(chunk_text)})

        for i in range(0, len(words), chunk_size - overlap):
            chunk_text = " ".join(words[i:i + chunk_size])
            if len(chunk_text) <= max_chars:
                _add(chunk_text)
                continue
            buf = ""
            for w in chunk_text.split(" "):
                while len(w) > max_chars:
                    _add(w[:max_chars])
                    w = w[max_chars:]
                if len(buf) + len(w) + 1 > max_chars:
                    _add(buf)
                    buf = w
                else:
                    buf = (buf + " " + w) if buf else w
            _add(buf)
        return chunks

    def add_chunks_to_index(self, chunks, source_info=""):
        if not chunks:
            return
        for chunk in chunks:
            self.chunks.append(chunk["text"])
            self.chunk_metadata.append({
                "id": chunk["id"], "source": source_info,
                "word_count": chunk["word_count"], "char_count": chunk["char_count"],
                "index": len(self.chunks) - 1,
            })
        self._save_to_disk()
        return len(chunks)

    def search_similar_chunks(self, query, k=5, exclude_used=True):
        if len(self.chunks) == 0:
            return []
        docs = {}
        for i, t_ in enumerate(self.chunks):
            if exclude_used and i in self.used_chunk_ids:
                continue
            meta = self.chunk_metadata[i] if i < len(self.chunk_metadata) else {}
            src = meta.get("source", "")
            d = docs.setdefault(src, {"source": src, "blocks": []})
            d["blocks"].append((i, i, t_))
        if not docs:
            return []
        found = find_passages(query, list(docs.values()), context=0, k=k)
        out = []
        for p_ in found["passages"]:
            i0 = p_["pieces"][0]
            meta = (self.chunk_metadata[i0]
                    if i0 < len(self.chunk_metadata) else {})
            out.append({"text": p_["text"], "score": p_["score"],
                        "metadata": meta, "chunk_index": i0,
                        "rank": len(out) + 1, "why": p_["matched"],
                        "chunks": list(p_["pieces"])})
        return out

    def mark_chunks_as_used(self, chunk_indices):
        for idx in chunk_indices:
            self.used_chunk_ids.add(idx)

    def reset_used_chunks(self):
        self.used_chunk_ids.clear()

    def get_stats(self):
        raw_stats = {
            "total_sources": len(self.raw_texts),
            "total_raw_words": sum(rt["word_count"] for rt in self.raw_texts.values()) if self.raw_texts else 0,
            "raw_sources": list(self.raw_texts.keys()) if self.raw_texts else [],
        }
        return {
            "total_chunks": len(self.chunks),
            "index_size": len(self.chunks),
            "sources": len(set(meta["source"] for meta in self.chunk_metadata)),
            "raw_texts": raw_stats,
            "used_chunks": len(self.used_chunk_ids),
            "available_chunks": len(self.chunks) - len(self.used_chunk_ids),
        }

    def clear_index(self):
        self.chunks = []
        self.chunk_metadata = []
        self.raw_texts = {}
        self.used_chunk_ids.clear()
        for p in (self._meta_path,):
            if os.path.exists(p): os.remove(p)



def _play_alert_sound_global():
    try:
        if IS_WINDOWS:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            time.sleep(0.3)
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        else:
            os.system(
                'paplay /usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga 2>/dev/null || '
                'aplay /usr/share/sounds/freedesktop/stereo/bell.oga 2>/dev/null || '
                'echo -e "\\a" || true &')
    except:
        try: QApplication.beep()
        except: pass


class CronScheduler(QThread):

    CHECK_INTERVAL_S = 30

    def __init__(self, cron_manager, execute_callback, signals):
        super().__init__()
        self.cron_manager = cron_manager
        self.execute_callback = execute_callback
        self.signals = signals
        self._running = True

    def run(self):
        print("[CRON] Scheduler started")
        armed = False
        while self._running:
            try:
                if not armed:
                    _n = self.cron_manager.arm_startup_jobs()
                    if _n is not None:
                        armed = True
                        if _n:
                            print(f"[CRON] {_n} job(s) armed for this start")
                for job in self.cron_manager.get_due_jobs():
                    if not self._running: break
                    self._exec(job)
            except Exception as e:
                print(f"[CRON] Scheduler error: {e}")
            for _ in range(self.CHECK_INTERVAL_S):
                if not self._running: break
                time.sleep(1)

    def _exec(self, job):
        jid, name = job["id"], job["name"]
        tgt = job.get("target", "local")
        tgt_label = "" if tgt == "local" else f" → {tgt}"
        self.signals.ensure_cron_tab.emit()
        _CT = "cron"
        if job.get("own_tab") and tgt == "local":
            self.signals.message_tab.emit(f"[CRON] {name} → ⏰ {name}", "system", _CT)
            self.signals.run_job_tab.emit(dict(job))
            self.cron_manager.log_execution(jid, "ok", "started in its tab")
            self.signals.refresh_cron.emit()
            return
        self.signals.message_tab.emit(f"[CRON] Running: {name}{tgt_label}", "system", _CT)
        try:
            result = self.execute_callback(job)
            self.signals.message_tab.emit(f"[CRON] {name} OK", "system", _CT)
            self.cron_manager.log_execution(jid, "ok", result or "no result")
        except Exception as e:
            self.signals.message_tab.emit(f"[CRON] {name} error: {e}", "error", _CT)
            self.cron_manager.log_execution(jid, "error", str(e))
        self.signals.refresh_cron.emit()

    def stop(self):
        self._running = False



def _add_redo_key(widget):
    try:
        from PyQt6.QtGui import QKeySequence, QShortcut
        from PyQt6.QtCore import Qt as _Qt
        sc = QShortcut(QKeySequence("Ctrl+Y"), widget)
        sc.setContext(_Qt.ShortcutContext.WidgetShortcut)
        sc.activated.connect(widget.redo)
    except Exception as e:
        print(f"[GUI] Ctrl+Y not bound: {e}", file=sys.stderr)


class ChatView(QTextEdit):
    RELAYOUT_MS = 120
    DRAG_MS = 300

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(self.RELAYOUT_MS)
        self._relayout_timer.timeout.connect(self._relayout_now)
        self._width_changed_at = 0.0
        self.follow = True
        self.verticalScrollBar().valueChanged.connect(self._follow_update)
        self.verticalScrollBar().rangeChanged.connect(self._range_changed)

    def _follow_update(self, value):
        sb = self.verticalScrollBar()
        self.follow = value >= sb.maximum() - max(24, sb.singleStep())

    def _range_changed(self, lo, hi):
        if self.follow:
            sb = self.verticalScrollBar()
            if sb.value() != hi:
                sb.setValue(hi)

    def load_html(self, html):
        self.setHtml(html)
        cur = self.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        self.setTextCursor(cur)
        self.scroll_bottom(force=True)

    def append_block(self, html):
        from PyQt6.QtGui import QTextBlockFormat, QTextCharFormat
        cur = QTextCursor(self.document())
        cur.movePosition(cur.MoveOperation.End)
        if not self.document().isEmpty():
            cur.insertBlock(QTextBlockFormat(), QTextCharFormat())
        cur.insertHtml(html)

    def scroll_bottom(self, force=False):
        if force:
            self.follow = True
        if not self.follow:
            return
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())
        QTimer.singleShot(0, lambda: sb.setValue(sb.maximum()))

    def resizeEvent(self, ev):
        from PyQt6.QtCore import QSize
        from PyQt6.QtGui import QResizeEvent
        if ev.oldSize().width() != ev.size().width() and ev.oldSize().width() > 0:
            now = time.monotonic()
            in_drag = (now - self._width_changed_at) * 1000 < self.DRAG_MS
            self._width_changed_at = now
            if in_drag:
                super().resizeEvent(QResizeEvent(
                    ev.size(), QSize(ev.size().width(), ev.oldSize().height())))
                self._relayout_timer.start()
                return
            self._relayout_timer.stop()
        super().resizeEvent(ev)

    def _relayout_now(self):
        from PyQt6.QtCore import QSize
        from PyQt6.QtGui import QResizeEvent
        super().resizeEvent(QResizeEvent(self.size(), QSize(-1, self.height())))


class TabTitleButton(QPushButton):

    def __init__(self, text, mark_w=0.0):
        super().__init__(text)
        self.spin = False
        self.spin_colour = "#38bdf8"
        self._mark_w = float(mark_w)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if not self.spin:
            return
        from tabs_overview import draw_arc
        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        cr = self.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, opt, self)
        tw = self.fontMetrics().horizontalAdvance(self.text())
        x0 = cr.x() + max(0.0, (cr.width() - tw) / 2.0)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_arc(p, x0 + self._mark_w / 2.0, cr.y() + cr.height() / 2.0, 4.6,
                 self.spin_colour, time.monotonic())
        p.end()


class CurtainGrip(QWidget):

    def __init__(self, curtain, on_resize, on_done):
        super().__init__(curtain)
        self._curtain = curtain
        self._on_resize = on_resize
        self._on_done = on_done
        self.setCursor(Qt.CursorShape.SplitHCursor)
        self.setFixedWidth(6)
        self._x0 = None
        self._w0 = 0

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._x0 = ev.globalPosition().x()
            self._w0 = self._curtain.width()

    def mouseMoveEvent(self, ev):
        if self._x0 is not None:
            self._on_resize(int(self._w0 - (ev.globalPosition().x() - self._x0)))

    def mouseReleaseEvent(self, ev):
        if self._x0 is not None:
            self._x0 = None
            self._on_done()

    def paintEvent(self, ev):
        from PyQt6.QtGui import QPainter, QColor
        p = QPainter(self)
        p.fillRect(0, 0, 1, self.height(), QColor("#334155"))
        p.end()


class ChatInput(QTextEdit):

    returnPressed = pyqtSignal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _add_redo_key(self)
        self.setAcceptDrops(True)
        self.setAcceptRichText(False)
        self.setTabChangesFocus(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.pending_files = []
        self._min_h, self._max_h = 40, 150
        self.setFixedHeight(self._min_h)
        self.document().contentsChanged.connect(self._autosize)
        self._hist = []
        self._hist_i = None
        self._draft = ""

    def remember(self, text):
        if text:
            if not self._hist or self._hist[-1] != text:
                self._hist.append(text)
                del self._hist[:-200]
        self._hist_i = None
        self._draft = ""

    def _hist_recall(self, step):
        if not self._hist:
            return False
        if self._hist_i is None:
            if step > 0:
                return False
            self._draft = self.toPlainText()
            self._hist_i = len(self._hist) - 1
        else:
            i = self._hist_i + step
            if i < 0:
                return True
            if i >= len(self._hist):
                self.setPlainText(self._draft)
                self._hist_i = None
                self.moveCursor(QTextCursor.MoveOperation.End)
                return True
            self._hist_i = i
        self.setPlainText(self._hist[self._hist_i])
        self.moveCursor(QTextCursor.MoveOperation.End)
        return True

    def text(self):
        return self.toPlainText()

    def setText(self, s):
        self.setPlainText(s)

    def _autosize(self):
        h = int(self.document().size().height()) + 16
        self.setFixedHeight(max(self._min_h, min(self._max_h, h)))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_V and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            clip = QApplication.clipboard()
            mime = clip.mimeData()
            if mime.hasImage():
                img = clip.image()
                if not img.isNull():
                    from PyQt6.QtCore import QBuffer, QIODevice
                    buf = QBuffer()
                    buf.open(QIODevice.OpenModeFlag.WriteOnly)
                    img.save(buf, "PNG")
                    raw = bytes(buf.data())
                    buf.close()
                    b64 = base64.b64encode(raw).decode()
                    n = len([f for f in self.pending_files if f[0].startswith("clipboard")])
                    name = f"clipboard_{n+1}.png" if n > 0 else "clipboard.png"
                    self.pending_files.append((name, b64, "image/png", len(raw)))
                    self._show_indicator()
                    return
            if mime.hasUrls():
                for url in mime.urls():
                    path = url.toLocalFile()
                    if path:
                        self._attach_file(path)
                if self.pending_files:
                    self._show_indicator()
                    return
        if (event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down)
                and event.modifiers() == Qt.KeyboardModifier.NoModifier):
            up = event.key() == Qt.Key.Key_Up
            edge = (self.textCursor().blockNumber() == 0 if up else
                    self.textCursor().blockNumber()
                    == self.document().blockCount() - 1)
            if edge and self._hist_recall(-1 if up else 1):
                return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.returnPressed.emit()
            return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        added = False
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and self._attach_file(path):
                added = True
        if added:
            self._show_indicator()
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def _attach_file(self, path):
        ext = os.path.splitext(path)[1].lower()
        mimes = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                 '.gif': 'image/gif', '.webp': 'image/webp', '.pdf': 'application/pdf',
                 '.txt': 'text/plain', '.md': 'text/markdown', '.csv': 'text/csv',
                 **_OFFICE_MIMES}
        mime = mimes.get(ext)
        if not mime:
            return False
        try:
            with open(path, 'rb') as f:
                raw = f.read()
            if len(raw) > 20 * 1024 * 1024:
                return False
            b64 = base64.b64encode(raw).decode()
            self.pending_files.append((os.path.basename(path), b64, mime, len(raw)))
            return True
        except:
            return False

    def _show_indicator(self):
        n = len(self.pending_files)
        if n == 0:
            self.setPlaceholderText(t("gui.chat_placeholder"))
        elif n == 1:
            name, _, _, size = self.pending_files[0]
            self.setPlaceholderText(f"📎 {name} ({size/1024:.0f}KB) — " + t("gui.attach_placeholder"))
        else:
            total_kb = sum(f[3] for f in self.pending_files) / 1024
            self.setPlaceholderText(t("ui.attach_ph", n=n, kb=f"{total_kb:.0f}"))

    def clear_pending(self):
        self.pending_files = []
        self.setPlaceholderText(t("gui.chat_placeholder"))


class ElidedLabel(QLabel):

    def __init__(self, text="", parent=None, hug=False):
        super().__init__(parent)
        self._full = ""
        self._hug = hug
        from PyQt6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Policy.Preferred if hug else QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)
        self.setText(text)

    def sizeHint(self):
        sh = super().sizeHint()
        if self._hug:
            m = self.contentsMargins()
            sh.setWidth(self.fontMetrics().horizontalAdvance(self._full)
                        + m.left() + m.right() + 2)
        return sh

    def minimumSizeHint(self):
        sh = super().minimumSizeHint()
        sh.setWidth(40)
        return sh

    changed = None

    def setText(self, text):
        self._full = str(text or "")
        self.setToolTip(self._full if self._full else "")
        self._fit()
        if self._hug:
            self.updateGeometry()
        if self.changed:
            try:
                self.changed(self._full)
            except Exception:
                pass

    def text(self):
        return self._full

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._fit()

    def _fit(self):
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(10, self.width()))
        QLabel.setText(self, shown)


class ElidedButton(QPushButton):

    def __init__(self, text="", parent=None, pad=24):
        super().__init__(parent)
        self._full = ""
        self._pad = pad
        from PyQt6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(60)
        self.setText(text)

    def setText(self, text):
        self._full = str(text or "")
        self.setToolTip(self._full)
        self._fit()

    def text(self):
        return self._full

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._fit()

    def _fit(self):
        shown = self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideRight, max(10, self.width() - self._pad))
        QPushButton.setText(self, shown)


class ShrinkRow(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        from PyQt6.QtWidgets import QLayout
        self._lay = QHBoxLayout(self)
        self._lay.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._pieces = []
        self._floor = None
        self._fitting = False
        self._fit_w = None
        self._more = QPushButton("⋯")
        self._more.setToolTip(t("ui.more_tip"))
        self._more.setCursor(Qt.CursorShape.PointingHandCursor)
        self._more.clicked.connect(self._more_menu)
        self._more.hide()

    def row(self):
        return self._lay

    def add(self, w, drop=None, stretch=0, align=None):
        if align is not None:
            self._lay.addWidget(w, stretch, align)
        else:
            self._lay.addWidget(w, stretch)
        if isinstance(w, QPushButton):
            self._pieces.append({"w": w, "rank": drop, "full": None, "short": None})
        self._floor = None
        return w

    def addStretch(self, n=0):
        self._lay.addStretch(n)

    def addSpacing(self, n):
        self._lay.addSpacing(n)

    @staticmethod
    def _short_of(btn, full):
        if not btn.icon().isNull():
            return ""
        head = full.strip().split(" ", 1)
        if len(head) == 2 and head[0] and not head[0][0].isalnum():
            return head[0]
        return full

    def _track(self, p):
        cur = QPushButton.text(p["w"])
        if p["full"] is None or (cur != p["short"] and cur != p["full"]):
            p["full"] = cur
            p["short"] = self._short_of(p["w"], cur)

    def _apply(self, compact, dropped):
        for p in self._pieces:
            self._track(p)
            short = compact is True or p["w"] in compact
            want = p["short"] if short else p["full"]
            if QPushButton.text(p["w"]) != want:
                QPushButton.setText(p["w"], want)
            p["w"].setVisible(p["w"] not in dropped)
        if dropped:
            if self._lay.indexOf(self._more) < 0:
                self._lay.addWidget(self._more)
            self._more.show()
        else:
            self._more.hide()

    def _need(self):
        self._lay.invalidate()
        return self._lay.minimumSize().width()

    def _droppables(self):
        return sorted((p for p in self._pieces if p["rank"] is not None),
                      key=lambda p: p["rank"])

    def _fit(self, width):
        if self._fitting:
            return
        self._fitting = True
        try:
            if self._floor is None:
                self._apply(True, {p["w"] for p in self._droppables()})
                self._floor = self._need()
                self.updateGeometry()
            order = self._droppables() + [p for p in self._pieces if p["rank"] is None]
            compact, dropped = set(), set()
            self._apply(compact, dropped)
            for p in order:
                if self._need() <= width:
                    return
                compact.add(p["w"])
                self._apply(compact, dropped)
            for p in self._droppables():
                if self._need() <= width:
                    break
                dropped.add(p["w"])
                self._apply(compact, dropped)
        finally:
            self._last = self._texts()
            self._fitting = False

    def _texts(self):
        return tuple(QPushButton.text(p["w"]) for p in self._pieces)

    def event(self, ev):
        from PyQt6.QtCore import QEvent
        if (ev.type() == QEvent.Type.LayoutRequest and not self._fitting
                and self._texts() != getattr(self, "_last", None)):
            self._floor = None
            QTimer.singleShot(0, lambda: self._fit(self.width()))
        return super().event(ev)

    def minimumSizeHint(self):
        from PyQt6.QtCore import QSize
        h = self._lay.minimumSize().height()
        return QSize(self._floor if self._floor is not None else 120, h)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if ev.size().width() != self._fit_w:
            self._fit_w = ev.size().width()
            self._fit(self._fit_w)

    def showEvent(self, ev):
        super().showEvent(ev)
        self._fit(self.width())

    def _more_menu(self):
        m = QMenu(self)
        for p in self._pieces:
            w = p["w"]
            if p["rank"] is None or w.isVisible():
                continue
            a = m.addAction(w.icon(), (p["full"] or "").strip() or w.toolTip())
            a.setToolTip(w.toolTip())
            a.setEnabled(w.isEnabled())
            a.triggered.connect(lambda _=False, b=w: b.click())
        m.exec(self._more.mapToGlobal(self._more.rect().bottomLeft()))


class WorkerSignals(QObject):
    message = pyqtSignal(str, str)
    wb_open = pyqtSignal(str, str)
    canvas_painted = pyqtSignal(str)
    message_tab = pyqtSignal(str, str, str)
    stream_token = pyqtSignal(str, str)
    stream_done = pyqtSignal(str)
    update_rag_status = pyqtSignal()
    refresh_todo = pyqtSignal()
    refresh_cron = pyqtSignal()
    refresh_notes = pyqtSignal()
    curate_done = pyqtSignal(dict)
    show_alert = pyqtSignal(str, str)
    switch_to_agents = pyqtSignal(str)
    plan_ack_close = pyqtSignal(object)
    switch_to_chat = pyqtSignal(str)
    task_state = pyqtSignal(str, bool)
    queue_changed = pyqtSignal(str)
    ensure_cron_tab = pyqtSignal()
    run_job_tab = pyqtSignal(dict)
    sticky_changed = pyqtSignal(str)
    token_usage = pyqtSignal(str, int)
    token_first = pyqtSignal(str, int, int)
    session_event = pyqtSignal(str, str, dict)
    session_closed = pyqtSignal(str, str)
    session_incoming = pyqtSignal(str, str, str)
    session_incoming_auto = pyqtSignal(str)
    share_incoming = pyqtSignal(str, str, str)
    plan_ack = pyqtSignal(object)
    send_text = pyqtSignal(str)
    cancel_ack_tab = pyqtSignal(str)
    intent_changed = pyqtSignal(str)
    council_event = pyqtSignal(str, dict)
    machine_event = pyqtSignal(str, dict)
    council_ack = pyqtSignal(object)
    hands_event = pyqtSignal(str, dict)
    config_event = pyqtSignal(str, dict)



class MCPManagerProxy:
    def __init__(self, mcp_url, manager_name, timeout=30):
        self.__dict__['_mcp_url'] = mcp_url
        self.__dict__['_mgr'] = manager_name
        self.__dict__['_timeout'] = timeout

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        def proxy_call(*args, **kwargs):
            try:
                r = requests.post(
                    f"{self._mcp_url}/data",
                    json={"manager": self._mgr, "method": name, "args": list(args), "kwargs": kwargs},
                    timeout=self._timeout
                )
                if r.status_code == 200:
                    data = r.json()
                    if "error" in data:
                        print(f"[MCP/{self._mgr}.{name}] Error: {data['error']}")
                        return None
                    return data.get("result")
                print(f"[MCP/{self._mgr}.{name}] HTTP {r.status_code}")
                return None
            except Exception as e:
                print(f"[MCP/{self._mgr}.{name}] {e}")
                return None
        return proxy_call




def _model_section(cfg, name):
    if not cfg or not name:
        return None
    want = f"MODEL:{name}"
    if cfg.has_section(want):
        return want
    low = want.lower()
    for sec in cfg.sections():
        if sec.lower() == low:
            return sec
    return None


class MetalLabel(QWidget):

    def __init__(self, text="", size=11, parent=None, dim=0.0):
        super().__init__(parent)
        self._text = text
        self._dim = max(0.0, min(0.8, dim))
        f = QFont()
        f.setPointSize(size)
        f.setWeight(QFont.Weight.Black)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, max(0.6, size * 0.11))
        self._font = f
        self._sheen = 0.5
        fm = QFontMetrics(f)
        self.setFixedSize(fm.horizontalAdvance(text) + 4, fm.height() + 2)

    def setText(self, text):
        self._text = text
        fm = QFontMetrics(self._font)
        self.setFixedSize(fm.horizontalAdvance(text) + 4, fm.height() + 2)
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        g = QLinearGradient(0, 0, 0, self.height())
        s = self._sheen

        def _m(hexcol):
            c = QColor(hexcol)
            if not self._dim:
                return c
            b_ = QColor("#0b1220")
            k = self._dim
            return QColor(int(c.red() * (1 - k) + b_.red() * k),
                          int(c.green() * (1 - k) + b_.green() * k),
                          int(c.blue() * (1 - k) + b_.blue() * k))

        g.setColorAt(0.0, _m("#7b8794"))
        g.setColorAt(max(0.0, s - 0.22), _m("#c7d0d9"))
        g.setColorAt(s, _m("#f4f8fb"))
        g.setColorAt(min(1.0, s + 0.16), _m("#9aa6b2"))
        g.setColorAt(1.0, _m("#5b6672"))
        p.setFont(self._font)
        p.setPen(QColor(0, 0, 0, 150))
        p.drawText(self.rect().adjusted(2, 2, 0, 0),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self._text)
        p.setPen(QPen(QBrush(g), 1))
        p.drawText(self.rect().adjusted(1, 0, 0, 0),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self._text)
        p.end()


class PygmentsHighlighter(QSyntaxHighlighter):

    def __init__(self, document, style="github-dark"):
        super().__init__(document)
        self._spans = {}
        self._formats = {}
        self._style = style
        self._lexer = None

    def set_language(self, filename, text=""):
        from pygments.lexers import get_lexer_for_filename, guess_lexer
        from pygments.util import ClassNotFound
        try:
            self._lexer = get_lexer_for_filename(filename, stripnl=False)
        except ClassNotFound:
            try:
                self._lexer = guess_lexer(text[:4000]) if text else None
            except ClassNotFound:
                self._lexer = None

    def _fmt(self, token):
        from pygments.styles import get_style_by_name
        if token in self._formats:
            return self._formats[token]
        if not hasattr(self, "_pstyle"):
            try:
                self._pstyle = dict(get_style_by_name(self._style))
            except Exception:
                self._pstyle = {}
        spec, tok = None, token
        while tok is not None:
            if tok in self._pstyle:
                spec = self._pstyle[tok]
                break
            tok = tok.parent
        f = QTextCharFormat()
        if spec:
            if spec.get("color"):
                f.setForeground(QColor("#" + spec["color"]))
            if spec.get("bold"):
                f.setFontWeight(QFont.Weight.Bold)
            if spec.get("italic"):
                f.setFontItalic(True)
        self._formats[token] = f
        return f

    def rehighlight_text(self, text):
        self._spans = {}
        if self._lexer is None:
            self.rehighlight()
            return
        line, col = 0, 0
        try:
            tokens = list(self._lexer.get_tokens_unprocessed(text))
        except Exception:
            tokens = []
        for start, token, value in tokens:
            for i, piece in enumerate(value.split("\n")):
                if i:
                    line += 1
                    col = 0
                if piece:
                    self._spans.setdefault(line, []).append(
                        (col, len(piece), token))
                    col += len(piece)
        self.rehighlight()

    def highlightBlock(self, text):
        for start, length, token in self._spans.get(self.currentBlock().blockNumber(), ()):
            self.setFormat(start, length, self._fmt(token))


_MD_FENCE = re.compile(r"^\s*(?:```|~~~)")
_MD_TABLE_RULE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def _md_content_lines(md):
    return [l for l in (md or "").splitlines()
            if l.strip() and not _MD_FENCE.match(l)
            and not _MD_TABLE_RULE.match(l)]


_MD_STRUCTURAL = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|#{1,6}\s|>|\|)")


def _md_hard_breaks(md):
    lines = (md or "").splitlines()
    out, i, in_fence = [], 0, False
    while i < len(lines):
        l = lines[i]
        if _MD_FENCE.match(l):
            in_fence = not in_fence
            out.append(l); i += 1; continue
        if in_fence or not l.strip():
            out.append(l); i += 1; continue
        j = i
        while j < len(lines) and lines[j].strip() and not _MD_FENCE.match(lines[j]):
            j += 1
        para = lines[i:j]
        indented = any(pl[:1] in (" ", "\t") for pl in para)
        for k, pl in enumerate(para):
            last = k == len(para) - 1
            if (last or indented or _MD_STRUCTURAL.match(pl)
                    or pl.endswith("  ") or pl.endswith("\\")):
                out.append(pl)
            else:
                out.append(pl + "  ")
        i = j
    return "\n".join(out) + ("\n" if (md or "").endswith("\n") else "")


def _md_unwrap_qt(saved):
    lines = (saved or "").splitlines()
    out, in_fence = [], False
    for l in lines:
        if _MD_FENCE.match(l):
            in_fence = not in_fence
            out.append(l); continue
        if in_fence or not l.strip() or not out or not out[-1].strip():
            out.append(l); continue
        prev = out[-1]
        if (_MD_STRUCTURAL.match(l) and not l.lstrip().startswith(">")) \
                or prev.lstrip().startswith(("|", "#")) or _MD_FENCE.match(prev):
            out.append(l); continue
        cont = l.strip()
        if prev.lstrip().startswith(">"):
            if not cont.startswith(">"):
                out.append(l); continue
            cont = cont[1:].strip()
        out[-1] = prev.rstrip() + " " + cont
    return "\n".join(out)


def _md_restore_adjacency(saved, baseline):
    base = [l for l in (baseline or "").splitlines()]
    adjacent = set()
    for a, b in zip(base, base[1:]):
        if a.strip() and b.strip():
            adjacent.add((a.strip(), b.strip()))
    if not adjacent:
        return saved
    lines = (saved or "").splitlines()
    out = []
    for idx, l in enumerate(lines):
        single_blank = (not l.strip() and 0 < idx < len(lines) - 1
                        and lines[idx - 1].strip() and lines[idx + 1].strip())
        if single_blank and (lines[idx - 1].strip(),
                             lines[idx + 1].strip()) in adjacent:
            continue
        out.append(l)
    return "\n".join(out)


def _md_view_faithful(md):
    md = md or ""
    if not md.strip():
        return True, ""
    try:
        from PyQt6.QtGui import QTextDocument
        _gh = QTextDocument.MarkdownFeature.MarkdownDialectGitHub
        doc = QTextDocument()
        doc.setMarkdown(_md_hard_breaks(md), _gh)
        shown = [l for l in doc.toPlainText().splitlines() if l.strip()]
        src = _md_content_lines(md)
        if len(shown) < len(src):
            return False, "lines %d->%d" % (len(src), len(shown))
        _bare = lambda s: re.sub(r"\s+", "", "".join(_md_content_lines(s)))
        _out = _bare(doc.toMarkdown(_gh))
        if _bare(md) != _out:
            for l in _md_content_lines(md):
                lb = re.sub(r"\s+", "", l)
                if lb and lb not in _out:
                    return False, "characters change in: %s" % l.strip()[:60]
            return False, "characters change"
    except Exception as e:
        return False, "check unavailable (%s)" % e
    return True, ""


def _md_plain_paste_faithful(text):
    text = text or ""
    if not text.strip():
        return True
    try:
        from PyQt6.QtGui import QTextDocument
        _gh = QTextDocument.MarkdownFeature.MarkdownDialectGitHub
        doc = QTextDocument()
        doc.setPlainText(text)
        back = doc.toMarkdown(_gh)
        _bare = lambda s_: re.sub(r"\s+", "", s_)
        return _bare(back) == _bare(text)
    except Exception:
        return False


class PlainTextEditor(QTextEdit):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptRichText(False)
        self.viewport().setAcceptDrops(False)
        _add_redo_key(self)
        self.paste_hook = None
        self.key_hook = None

    def keyPressEvent(self, e):
        hook = getattr(self, "key_hook", None)
        if hook is not None:
            try:
                if hook(e):
                    return
            except Exception as ex:
                print(f"[NOTES] key hook: {ex}", file=sys.stderr)
        super().keyPressEvent(e)

    def insertFromMimeData(self, source):
        hook = getattr(self, "paste_hook", None)
        if hook is not None:
            try:
                if hook(source):
                    return
            except Exception as e:
                print(f"[NOTES] paste hook: {e}", file=sys.stderr)
        super().insertFromMimeData(source)

    def mousePressEvent(self, e):
        from PyQt6.QtCore import Qt as _Qt
        if (e.button() == _Qt.MouseButton.LeftButton
                and not (e.modifiers() & _Qt.KeyboardModifier.ShiftModifier)):
            tc = self.textCursor()
            if tc.hasSelection():
                pos = self.cursorForPosition(e.pos()).position()
                if tc.selectionStart() <= pos <= tc.selectionEnd():
                    new = self.cursorForPosition(e.pos())
                    self.setTextCursor(new)
                    e.accept()
                    return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        super().mouseMoveEvent(e)


def _mac_menu_name(name):
    try:
        import ctypes, ctypes.util
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        ctypes.cdll.LoadLibrary(ctypes.util.find_library("Foundation"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]

        def send(obj, sel, *args, argtypes=()):
            fn = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                  *argtypes)(("objc_msgSend", objc))
            return fn(obj, objc.sel_registerName(sel), *args)

        def nsstr(text):
            return send(objc.objc_getClass(b"NSString"), b"stringWithUTF8String:",
                        text.encode(), argtypes=(ctypes.c_char_p,))

        info = send(send(objc.objc_getClass(b"NSBundle"), b"mainBundle"), b"infoDictionary")
        if info:
            send(info, b"setObject:forKey:", nsstr(name), nsstr("CFBundleName"),
                 argtypes=(ctypes.c_void_p, ctypes.c_void_p))
    except Exception as e:
        print(f"[GUI] menu bar name not set: {e}", file=sys.stderr)


class _MacLight(QWidget):

    clicked = pyqtSignal()

    _COLOURS = {"close": ("#ff5f57", "#e0443e"), "min": ("#febc2e", "#dea123"),
                "zoom": ("#28c840", "#1aab29")}
    _SIGNS = {"close": "×", "min": "−", "zoom": "+"}

    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._lit = False
        self.setFixedSize(12, 12)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def light(self, on):
        if on != self._lit:
            self._lit = on
            self.update()

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QColor, QPen, QFont
        fill, edge = self._COLOURS[self._kind]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(QColor(edge), 0.8))
        p.setBrush(QColor(fill))
        p.drawEllipse(self.rect().adjusted(1, 1, -1, -1))
        if self._lit:
            f = QFont()
            f.setPixelSize(11)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(0, 0, 0, 150))
            p.drawText(self.rect().adjusted(0, -1, 0, -1),
                       Qt.AlignmentFlag.AlignCenter, self._SIGNS[self._kind])
        p.end()


class CustomTitleBar(QWidget):

    def __init__(self, parent, title=""):
        super().__init__(parent)
        self._win = parent
        self.setObjectName("title_bar")
        self.setFixedHeight(38)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 8, 0)
        lay.setSpacing(4)

        self.title_lbl = QLabel(title)
        self.title_lbl.setObjectName("title_label")
        if sys.platform == "darwin":
            lay.setContentsMargins(14, 0, 14, 0)
            lights = QWidget(self)
            ll = QHBoxLayout(lights)
            ll.setContentsMargins(0, 0, 0, 0)
            ll.setSpacing(8)
            self._lights = []
            for kind, slot in (("close", self._close), ("min", self._minimize),
                               ("zoom", self._toggle_max)):
                b = _MacLight(kind, lights)
                b.setObjectName(f"title_mac_{kind}")
                b.clicked.connect(slot)
                ll.addWidget(b)
                self._lights.append(b)
            lights.installEventFilter(self)
            lights.setFixedWidth(lights.sizeHint().width())
            lay.addWidget(lights)
            lay.addStretch(1)
            self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(self.title_lbl)
            lay.addStretch(1)
            lay.addSpacing(lights.width())
            return
        lay.addWidget(self.title_lbl)
        lay.addStretch(1)

        for name, slot, obj in [
            ("—", self._minimize, "title_min"),
            ("▢", self._toggle_max, "title_max"),
            ("✕", self._close, "title_close"),
        ]:
            b = QPushButton(name)
            b.setObjectName(obj)
            b.setFixedSize(32, 26)
            b.clicked.connect(slot)
            lay.addWidget(b)

    def eventFilter(self, obj, ev):
        from PyQt6.QtCore import QEvent
        if ev.type() in (QEvent.Type.Enter, QEvent.Type.Leave):
            for b in getattr(self, "_lights", []):
                b.light(ev.type() == QEvent.Type.Enter)
        return False

    def _minimize(self):
        self._win.showMinimized()

    def _toggle_max(self):
        win = self._win
        if getattr(win, "_is_max", False):
            win.showNormal()
            geo = getattr(win, "_normal_geo", None)
            if geo is not None:
                win.setGeometry(geo)
            win._is_max = False
        else:
            win._normal_geo = win.geometry()
            win.showMaximized()
            win._is_max = True
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, win._apply_rounded_mask)

    def _close(self):
        self._win.close()

    def mousePressEvent(self, e):
        from PyQt6.QtCore import Qt as _Qt
        if e.button() == _Qt.MouseButton.LeftButton:
            wh = self._win.windowHandle()
            if wh is not None:
                wh.startSystemMove()
                e.accept()
                return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        self._toggle_max()


class _GlowFrame(QWidget):

    def __init__(self, margin, radius, accent, parent=None):
        super().__init__(parent)
        self._margin = margin
        self._radius = radius
        self._accent = accent
        self._glow_enabled = True

    def set_glow_enabled(self, on):
        if on != self._glow_enabled:
            self._glow_enabled = on
            self.update()

    def paintEvent(self, event):
        if not self._glow_enabled:
            return
        from PyQt6.QtCore import QRectF, Qt
        from PyQt6.QtGui import QPainter, QPen, QColor, QBrush
        m, r = self._margin, self._radius
        content = QRectF(m, m, self.width() - 2 * m, self.height() - 2 * m)
        if content.width() <= 0 or content.height() <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor("#0f172a")))
        p.drawRoundedRect(content, r, r)
        base = QColor(self._accent)
        layers = 12
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(layers):
            grow = (i + 1) * (m / layers)
            alpha = int(70 * (1.0 - i / layers) ** 1.8)
            pen = QPen(QColor(base.red(), base.green(), base.blue(), alpha))
            pen.setWidthF(1.4)
            p.setPen(pen)
            p.drawRoundedRect(content.adjusted(-grow, -grow, grow, grow),
                              r + grow, r + grow)
        pen = QPen(base)
        pen.setWidthF(1.6)
        p.setPen(pen)
        p.drawRoundedRect(content, r, r)
        p.end()


class _SweepLine(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pos = 0.0
        self.setFixedHeight(4)

    def set_pos(self, pos):
        self._pos = max(0.0, min(1.0, float(pos)))
        self.update()

    def paintEvent(self, _event):
        from PyQt6.QtCore import QRectF, Qt
        from PyQt6.QtGui import QColor, QLinearGradient, QPainter
        dim, mid, hot = QColor(120, 20, 20, 89), QColor(220, 40, 40, 191), QColor(255, 60, 60, 250)
        pos = self._pos
        g = QLinearGradient(0, 0, self.width(), 0)
        for at, col in ((0.0, dim), (pos - 0.20, dim), (pos - 0.06, mid), (pos, hot),
                        (pos + 0.06, mid), (pos + 0.20, dim), (1.0, dim)):
            g.setColorAt(max(0.0, min(1.0, at)), col)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(QRectF(self.rect()), 2, 2)


class _TabFrame(QFrame):

    def __init__(self):
        super().__init__()
        self._beat = None

    def set_beat(self, colour, alpha):
        beat = (str(colour), float(alpha)) if alpha > 0 else None
        if beat != self._beat:
            self._beat = beat
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._beat:
            return
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QColor, QPainter, QPainterPath
        col = QColor(self._beat[0])
        col.setAlphaF(self._beat[1])
        r = QRectF(self.rect())
        rad = 6.0
        path = QPainterPath()
        path.moveTo(r.left(), r.bottom())
        path.lineTo(r.left(), r.top() + rad)
        path.arcTo(r.left(), r.top(), 2 * rad, 2 * rad, 180, -90)
        path.lineTo(r.right() - rad, r.top())
        path.arcTo(r.right() - 2 * rad, r.top(), 2 * rad, 2 * rad, 90, -90)
        path.lineTo(r.right(), r.bottom())
        path.closeSubpath()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillPath(path, col)


class _ShrinkStack(QStackedWidget):

    def minimumSizeHint(self):
        from PyQt6.QtCore import QSize
        cur = self.currentWidget()
        if cur is None:
            return QSize(0, 0)
        return cur.minimumSizeHint().expandedTo(cur.minimumSize())


class _ShrinkTabs(QTabWidget):

    def minimumSizeHint(self):
        from PyQt6.QtCore import QSize
        base = super().minimumSizeHint()
        stack = self.findChild(QStackedWidget, "qt_tabwidget_stackedwidget")
        cur = self.currentWidget()
        if stack is None or cur is None:
            return base
        whole = stack.minimumSizeHint()
        mine = cur.minimumSizeHint().expandedTo(cur.minimumSize())
        return QSize(base.width() - max(0, whole.width() - mine.width()),
                     base.height() - whole.height() + mine.height())


class _LineNumbers(QWidget):

    def __init__(self, editor):
        super().__init__(editor)
        self._ed = editor

    def sizeHint(self):
        from PyQt6.QtCore import QSize
        return QSize(self._ed._gutter_width(), 0)

    def paintEvent(self, event):
        self._ed._paint_gutter(event)


class CodeEdit(QTextEdit):

    def __init__(self, parent=None):
        super().__init__(parent)
        _add_redo_key(self)
        self._gutter = _LineNumbers(self)
        self.document().blockCountChanged.connect(lambda _n: self._gutter_fit())
        self.verticalScrollBar().valueChanged.connect(
            lambda _v: self._gutter.update())
        self.textChanged.connect(self._gutter.update)
        self.cursorPositionChanged.connect(self._gutter.update)
        self._gutter_fit()

    def _gutter_width(self):
        n = max(1, self.document().blockCount())
        digits = len(str(n))
        return 12 + self.fontMetrics().horizontalAdvance("9") * max(3, digits)

    def _gutter_fit(self):
        w = self._gutter_width()
        self.setViewportMargins(w, 0, 0, 0)
        self._gutter.setGeometry(0, 0, w, self.height())
        self._gutter.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._gutter_fit()

    def setDocument(self, doc):
        super().setDocument(doc)
        try:
            doc.blockCountChanged.connect(lambda _n: self._gutter_fit())
        except Exception:
            pass
        self._gutter_fit()

    def _block_top_in_gutter(self, blk):
        r = self.document().documentLayout().blockBoundingRect(blk)
        return (r.top() - self.verticalScrollBar().value()
                + self.viewport().geometry().top())

    def _paint_gutter(self, event):
        from PyQt6.QtGui import QPainter, QColor
        p = QPainter(self._gutter)
        p.fillRect(event.rect(), QColor("#0d1526"))
        doc = self.document()
        lay = doc.documentLayout()
        cur_line = self.textCursor().blockNumber()
        w = self._gutter.width()
        fm = self.fontMetrics()
        _vp_top = self.viewport().geometry().top()
        blk = doc.begin()
        while blk.isValid():
            r = lay.blockBoundingRect(blk)
            r = r.translated(0, self._block_top_in_gutter(blk) - r.top())
            if r.bottom() < event.rect().top():
                blk = blk.next(); continue
            if r.top() > event.rect().bottom():
                break
            if blk.isVisible() and r.bottom() > _vp_top:
                _is_cur = blk.blockNumber() == cur_line
                p.setPen(QColor("#e6edf7"))
                p.drawText(0, int(r.top()), w - 7, fm.height(),
                           int(Qt.AlignmentFlag.AlignRight
                               | Qt.AlignmentFlag.AlignTop),
                           str(blk.blockNumber() + 1))
            blk = blk.next()
        p.end()

    _OPENERS = (":", "{", "(", "[")

    def keyPressEvent(self, e):
        from PyQt6.QtGui import QTextCursor
        key = e.key()
        mod = e.modifiers()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                and not (mod & Qt.KeyboardModifier.ShiftModifier):
            cur = self.textCursor()
            line = cur.block().text()
            indent = line[:len(line) - len(line.lstrip(" \t"))]
            if line.rstrip().endswith(self._OPENERS):
                indent += "\t" if indent.startswith("\t") else "    "
            super().keyPressEvent(e)
            self.textCursor().insertText(indent)
            return
        if key == Qt.Key.Key_Backtab or (key == Qt.Key.Key_Tab
                                         and (mod & Qt.KeyboardModifier.ShiftModifier)):
            self._shift_indent(-1)
            return
        if key == Qt.Key.Key_Tab and self.textCursor().hasSelection():
            self._shift_indent(+1)
            return
        super().keyPressEvent(e)

    def _shift_indent(self, direction):
        from PyQt6.QtGui import QTextCursor
        cur = self.textCursor()
        start, end = cur.selectionStart(), cur.selectionEnd()
        cur.beginEditBlock()
        cur.setPosition(start)
        first = cur.blockNumber()
        cur.setPosition(end)
        last = cur.blockNumber()
        for n in range(first, last + 1):
            b = self.document().findBlockByNumber(n)
            c = QTextCursor(b)
            if direction > 0:
                c.insertText("    ")
            else:
                txt = b.text()
                strip = 0
                while strip < 4 and strip < len(txt) and txt[strip] == " ":
                    strip += 1
                if strip == 0 and txt.startswith("\t"):
                    strip = 1
                for _ in range(strip):
                    c.deleteChar()
        cur.endEditBlock()

    def goto_line(self, n):
        from PyQt6.QtGui import QTextCursor
        n = max(1, int(n))
        blk = self.document().findBlockByNumber(n - 1)
        if not blk.isValid():
            blk = self.document().lastBlock()
        c = QTextCursor(blk)
        self.setTextCursor(c)
        try:
            y = self.document().documentLayout().blockBoundingRect(blk).top()
            sb = self.verticalScrollBar()
            sb.setValue(max(0, int(y) - self.viewport().height() // 2))
        except Exception:
            self.ensureCursorVisible()
        self.setFocus()


class _CodeSlot:
    __slots__ = ("code_bufs", "code_bufs_order", "code_open_path")

    def __init__(self):
        self.code_bufs, self.code_bufs_order, self.code_open_path = {}, [], ""


def _council_host(url):
    h = str(url or "").split("//")[-1]
    prev = None
    while h != prev:
        prev = h
        for suf in ("/", "/v1"):
            if h.endswith(suf):
                h = h[:-len(suf)]
    return h


def _esc(s):
    import html as _h
    return _h.escape(str(s or ""))


class ChatCtx:

    def __init__(self, tab_id, title):
        self.tab_id = tab_id
        self.title = title
        self.history = []
        self.last_extracted_idx = 0
        self.msg_queue = []
        self.turn_id = None
        self.bg_live = 0
        self.bg_labels = []
        self.bg_jobs = []
        self.turn_iter = (0, 0)
        self.injected_pending = []
        self.sticky = ""
        self.no_log = False
        self.goal = ""
        self.pinned_device = None
        import uuid as _u
        self.sync_id = _u.uuid4().hex[:12]
        self.context_limit = 0
        self.ctx_estimated = False
        self.last_injections = []
        self.last_prompt_tokens = 0
        self.fixed_context = []
        self.user_fixed_context = []
        self.model_override = ""
        self.pinned_skills = []
        self.current_instruction = ""
        self.agents = []
        self.intent = []
        self.code_project = ""
        self.blocked_tools = set()
        self.code_bufs = {}
        self.code_bufs_order = []
        self.code_open_path = ""
        self.turn_steps = []
        self.session_uuid = None
        self.job_id = ""
        self.job_name = ""
        self.job_alert = False
        self.job_session = None
        self.task_running = False
        self.stop_requested = __import__("threading").Event()
        self.compacting = False
        self.compact_pending = False
        self.ctx_measured = False
        self.pending_ack = None
        self.draft = ""
        self.draft_files = []
        self.draft_hist = []
        self.verify_cmd = ""
        self.turn_live = False
        self.council_members = None
        self.council_first = ""
        self.council_context = True
        self.council_html = ""
        self.council_events = []
        self.council_ack = None


_CMD_USAGE = {
    "searchrag": lambda: t("usage.searchrag"), "search": lambda: t("usage.search"),
    "include": lambda: t("usage.include"), "includeurl": lambda: t("usage.includeurl"),
    "includeurls": lambda: t("usage.includeurls"),
    "addtext": lambda: t("usage.addtext"), "fixed": lambda: t("usage.fixed"),
    "notes": lambda: t("usage.notes"), "multi": lambda: t("usage.multi"),
    "browse": lambda: t("usage.browse"), "skill": lambda: t("skill.usage"),
    "alert": lambda: t("usage.alert"),
}
_CMD_NO_ARGS = {"summary", "todo", "verify", "goal", "pin", "why", "model",
                "creds", "help", "queue", "qa", "compact", "context", "watch"}
_GUI_CMDS = set(_CMD_USAGE) | _CMD_NO_ARGS

CRON_TAB_ID = "cron"
BG_EVENT_PREFIX = "[BACKGROUND EVENT]"


def _canon_cmd(msg):
    m = re.match(r"@([a-z]+)(?=\s|$)", msg)
    if m and m.group(1) in _GUI_CMDS:
        return "/" + msg[1:]
    return msg



_TAB_TOOL_PREFIXES = ("code_", "git_", "fed_code_")
_TAB_TOOLS = {"working_memory_forget", "run_command", "wb_delete",
              "fed_share_code", "fed_accept_code"}


_SCREEN_TOOLS = ("sticky_note", "render_html", "dashboard_render", "canvas_undo",
                 "canvas_see", "canvas_eval", "alert")


def _tool_is_tabs(tool):
    return tool in _TAB_TOOLS or tool.startswith(_TAB_TOOL_PREFIXES)


def _Face_call_project(face):
    try:
        return getattr(face._turn_ctx(), "code_project", "") or ""
    except Exception:
        return ""


_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.I)


def _pictures_to_links(html):
    if "<img" not in (html or "").lower():
        return html
    import html as _h

    def _one(m):
        tag = m.group(0)
        src = re.search(r"""src\s*=\s*["']([^"']+)""", tag, re.I)
        alt = re.search(r"""alt\s*=\s*["']([^"']*)""", tag, re.I)
        if not src:
            return ""
        s = _h.unescape(src.group(1).strip())
        label = (_h.unescape(alt.group(1).strip()) if alt else "") \
            or os.path.basename(s.rstrip("/")) or s
        return (f'<a href="hoplita:canvas:{_h.escape(s)}" '
                f'style="color:#38bdf8;text-decoration:none;">'
                f'🖼 {_h.escape(label)}</a>')
    return _IMG_TAG_RE.sub(_one, html)


def config_change_lines(changes):
    out = []
    for c in changes:
        where = f"[{c.get('section', '')}] {c.get('key', '')}"
        if c.get("delete"):
            out.append(f"− {where}  ({c.get('old') or ''})")
        elif c.get("old") is None:
            out.append(f"+ {where} = {c.get('new') or ''}")
        else:
            out.append(f"~ {where}: {c.get('old')} → {c.get('new') or ''}")
    return "\n".join(out)


class ChatAssistantWindow(QMainWindow):
    _bg_done = pyqtSignal(object, object)

    def __init__(self):
        super().__init__()
        self._bg_done.connect(self._on_bg_done)
        self.config = self.load_config()
        load_language(self.config.get("APP", "language", fallback="en"))
        self._watch_config()
        self._chat_ctxs = {"local": ChatCtx("local", t("chat.tab_local"))}
        self._active_tab_id = "local"
        self._local_tab_seq = 0
        self._tls = threading.local()
        self._enchant_t0 = {}
        self._session_waiting = set()
        self._session_acked = set()
        self._session_models = {}
        self._session_queue = {}
        self._stream_bufs = {}
        self._stream_live_tab = None
        self._active_stream_live = False
        self._deferred_messages = []
        self.EXTRACT_EVERY_N = 10
        self._resize_margin = 10
        self._resize_edge = None
        self._is_max = False

        self.signals = WorkerSignals()
        self.signals.message.connect(self._on_thread_message)
        self.signals.wb_open.connect(self._on_wb_open)
        self.signals.canvas_painted.connect(self._on_canvas_painted)
        self.signals.message_tab.connect(self._on_thread_message_tab)
        self.signals.ensure_cron_tab.connect(self._ensure_cron_tab)
        self.signals.run_job_tab.connect(self._run_job_in_tab)
        self.signals.stream_token.connect(self._on_stream_token)
        self.signals.stream_done.connect(self._on_stream_done)
        self.signals.update_rag_status.connect(self._on_update_rag_status)
        self.signals.refresh_todo.connect(self._on_refresh_todo)
        self.signals.refresh_cron.connect(self._on_refresh_cron)
        self.signals.refresh_notes.connect(self._on_refresh_notes)
        self.signals.curate_done.connect(self._on_curate_done)
        self.signals.show_alert.connect(self._on_show_alert)
        self.signals.switch_to_agents.connect(self._on_switch_to_agents)
        self.signals.switch_to_chat.connect(self._on_switch_to_chat)
        self.signals.session_event.connect(self._on_session_event)
        self.signals.session_closed.connect(self._on_session_closed)
        self.signals.session_incoming.connect(self._on_session_incoming)
        self.signals.session_incoming_auto.connect(self._on_session_incoming_auto)
        self.signals.share_incoming.connect(self._on_share_incoming)
        self.signals.plan_ack.connect(self._on_plan_ack)
        self.signals.send_text.connect(lambda m: self.send_message((m, [])))
        self.signals.plan_ack_close.connect(self._on_plan_ack_close)
        self.signals.cancel_ack_tab.connect(self._cancel_pending_ack)
        self.signals.task_state.connect(self._on_task_state)
        self.signals.task_state.connect(self._machine_task_state)
        self.signals.machine_event.connect(self._on_machine_event)
        self.signals.queue_changed.connect(self._on_queue_changed)
        self.signals.token_usage.connect(self._on_token_usage)
        self.signals.token_first.connect(self._on_token_first)
        self.signals.sticky_changed.connect(self._sticky_paint)
        self.signals.intent_changed.connect(self._on_intent_changed)
        self.signals.council_event.connect(self._on_council_event)
        self.signals.council_ack.connect(self._on_council_ack)
        self.signals.hands_event.connect(self._on_hands_event)
        self.signals.config_event.connect(self._on_config_event)

        self.rag_manager = RAGDocumentStore(self.config)

        self._mcp_url = self.config.get("MCP", "url", fallback="http://127.0.0.1:9077")
        self._mcp_available = False
        self._wait_for_mcp(timeout_s=90)
        if not self._mcp_available:
            QMessageBox.critical(None, t("app.mcp_unavailable_title"),
                f"MCP server not responding at {self._mcp_url}\n\n"
                f"Start the server first and enter the vault password.")
            sys.exit(1)

        self.todo_manager = MCPManagerProxy(self._mcp_url, "todo")
        self.notes_manager = MCPManagerProxy(self._mcp_url, "notes")
        self.cron_manager = MCPManagerProxy(self._mcp_url, "cron")
        self.cred_manager = MCPManagerProxy(self._mcp_url, "cred")
        self._init_ui()

    def _turn_ctx(self):
        tid = getattr(self._tls, "tab_id", None) or self._active_tab_id
        return self._chat_ctxs.get(tid) or self._chat_ctxs["local"]

    def _view_ctx(self):
        return self._chat_ctxs.get(self._active_tab_id) or self._chat_ctxs["local"]

    @property
    def _task_running(self): return self._turn_ctx().task_running
    @_task_running.setter
    def _task_running(self, v): self._turn_ctx().task_running = v

    @property
    def _stop_requested(self): return self._turn_ctx().stop_requested
    @_stop_requested.setter
    def _stop_requested(self, v): self._turn_ctx().stop_requested = v

    @property
    def _compacting(self): return self._turn_ctx().compacting
    @_compacting.setter
    def _compacting(self, v): self._turn_ctx().compacting = v

    @property
    def conversation_history(self): return self._turn_ctx().history
    @conversation_history.setter
    def conversation_history(self, v): self._turn_ctx().history = v

    @property
    def _last_extracted_idx(self): return self._turn_ctx().last_extracted_idx
    @_last_extracted_idx.setter
    def _last_extracted_idx(self, v): self._turn_ctx().last_extracted_idx = v

    @property
    def _last_prompt_tokens(self): return self._turn_ctx().last_prompt_tokens
    @_last_prompt_tokens.setter
    def _last_prompt_tokens(self, v): self._turn_ctx().last_prompt_tokens = v

    @property
    def sticky(self): return self._turn_ctx().sticky
    @sticky.setter
    def sticky(self, v): self._turn_ctx().sticky = v

    @property
    def fixed_context(self): return self._turn_ctx().fixed_context
    @fixed_context.setter
    def fixed_context(self, v): self._turn_ctx().fixed_context = v

    @property
    def user_fixed_context(self): return self._turn_ctx().user_fixed_context
    @user_fixed_context.setter
    def user_fixed_context(self, v): self._turn_ctx().user_fixed_context = v

    @property
    def _chat_model_override(self): return self._turn_ctx().model_override
    @_chat_model_override.setter
    def _chat_model_override(self, v): self._turn_ctx().model_override = v

    @property
    def _pinned_skills(self): return self._turn_ctx().pinned_skills
    @_pinned_skills.setter
    def _pinned_skills(self, v): self._turn_ctx().pinned_skills = v

    @property
    def _current_instruction(self): return self._turn_ctx().current_instruction
    @_current_instruction.setter
    def _current_instruction(self, v): self._turn_ctx().current_instruction = v

    @property
    def _msg_queue(self): return self._view_ctx().msg_queue
    @_msg_queue.setter
    def _msg_queue(self, v): self._view_ctx().msg_queue = v

    def _wait_for_mcp(self, timeout_s=90):
        from PyQt6.QtWidgets import QProgressDialog
        from PyQt6.QtCore import Qt as _Qt
        import time as _time

        self._check_mcp()
        if self._mcp_available:
            return

        dlg = QProgressDialog(
            t("gui.waiting_mcp") + "\n\n" + t("gui.waiting_mcp_hint"),
            None, 0, 0)
        dlg.setWindowTitle("HOPLITA")
        dlg.setWindowModality(_Qt.WindowModality.ApplicationModal)
        dlg.setMinimumDuration(0)
        dlg.setCancelButton(None)
        dlg.setAttribute(_Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        dlg.setWindowFlag(_Qt.WindowType.WindowDoesNotAcceptFocus, True)
        dlg.show()
        try:
            _g = dlg.screen().availableGeometry()
            dlg.move(_g.center().x() - dlg.width() // 2,
                     _g.center().y() + _g.height() // 5)
        except Exception:
            pass
        from PyQt6.QtWidgets import QApplication as _QApp
        _QApp.processEvents()

        deadline = _time.time() + timeout_s
        import hoplita_paths
        _since = _time.time() - 5
        _gave = os.path.join(hoplita_paths.hoplita_base(), "hoplita-mcp.gaveup")
        _why = ""
        while _time.time() < deadline:
            _time.sleep(2)
            _QApp.processEvents()
            self._check_mcp()
            if self._mcp_available:
                break
            try:
                if os.path.getmtime(_gave) >= _since:
                    with open(_gave, encoding="utf-8") as _f:
                        _why = (json.load(_f) or {}).get("why", "")
                    break
            except (OSError, ValueError):
                pass
        dlg.close()
        if _why:
            QMessageBox.warning(self, "HOPLITA", t("gui.mcp_gave_up", why=_why))

    def _check_mcp(self):
        try:
            r = requests.get(f"{self._mcp_url}/health", timeout=3)
            if r.status_code == 200:
                data = r.json()
                self._mcp_available = True
                print(f"[MCP] ✓ {self._mcp_url} — {data.get('tool_count', 0)} tools, "
                      f"{data.get('experience_count', 0)} exp")
            else:
                print(f"[MCP] ⚠ HTTP {r.status_code}")
        except:
            print(f"[MCP] ❌ Not available at {self._mcp_url}")

    def _fed_action(self, tool, args):
        def done(res):
            print(f"[FED-GUI] {tool} → {res if isinstance(res, str) else 'ok'}")
            self._fed_cache_refresh()
        self._bg(lambda: self._mcp_call(tool, args), done)

    def _mark_push_failed(self, msg):
        try:
            from PyQt6.QtCore import QSettings
            QSettings("hoplita", "hoplita").setValue("last_push_error", msg)
        except Exception:
            pass

    def _clear_push_mark(self):
        try:
            from PyQt6.QtCore import QSettings
            QSettings("hoplita", "hoplita").remove("last_push_error")
        except Exception:
            pass

    def _warn_last_push_error(self):
        try:
            from PyQt6.QtCore import QSettings
            s = QSettings("hoplita", "hoplita")
            err = s.value("last_push_error")
            if err:
                s.remove("last_push_error")
                self.add_message(t("fed.push_failed_boot", err=err), "error")
        except Exception:
            pass

    def _fmt_sync_error(self, res):
        if res.get("error") == "quota_exceeded":
            return t("fed.quota_exceeded", resource=res.get("resource", "?"),
                     used=res.get("used", "?"), limit=res.get("limit", "?"),
                     incoming=res.get("incoming", "?"))
        if res.get("error") == "offline":
            _d = res.get("detail")
            return t("fed.sync_offline") + (f"\n({_d})" if _d else "")
        if res.get("error") == "decrypt_failed":
            return t("fed.sync_decrypt_failed")
        if res.get("error") == "sync_conflict":
            return t("fed.sync_conflict")
        return t("fed.sync_error", err=res["error"])

    def _periodic_sync(self):
        if getattr(self, "_sync_busy", False):
            return
        self._do_sync(pull=True)
        self._sync_enqueue(pull=False, manual=False)

    def _sync_enqueue(self, pull, manual):
        q = self.__dict__.setdefault("_sync_queue", [])
        for i, (p, m) in enumerate(q):
            if p == pull:
                q[i] = (p, m or manual)
                return
        q.append((pull, manual))

    def _sync_drain(self):
        q = self.__dict__.get("_sync_queue") or []
        if q and not getattr(self, "_sync_busy", False):
            pull, manual = q.pop(0)
            self._do_sync(pull=pull, manual=manual)

    def _do_sync(self, pull, manual=False):
        tool = "fed_sync_pull" if pull else "fed_sync_push"
        if getattr(self, "_sync_busy", False):
            if manual:
                self.add_message(t("fed.sync_busy"), "system")
            self._sync_enqueue(pull, manual)
            return
        self._sync_busy = True
        if manual:
            self.add_message(t("fed.sync_pulling") if pull
                             else t("fed.sync_pushing"), "system")

        def work():
            _wait = 1800
            ok, res, err = self._mcp_try(tool, {}, timeout=_wait)
            if (not pull and isinstance(res, dict) and res.get("needs_pull")):
                self._mcp_try("fed_sync_pull", {}, timeout=_wait)
                ok, res, err = self._mcp_try("fed_sync_push", {}, timeout=_wait)
            if not ok and not (isinstance(res, dict) and res.get("error")):
                return {"error": err or "no answer from the MCP"}
            if (not pull and isinstance(res, dict) and res.get("needs_pull")):
                return {"error": "sync_conflict"}
            return res

        def done(res):
            self._sync_busy = False
            try:
                report(res)
            finally:
                self._sync_drain()

        def report(res):
            if isinstance(res, dict) and res.get("skipped"):
                if manual:
                    self.add_message(t("fed.not_registered"), "system")
                return
            if isinstance(res, dict) and res.get("error"):
                localized = self._fmt_sync_error(res)
                if manual:
                    self.add_message(localized, "error")
                elif not pull:
                    self._mark_push_failed(localized)
                return
            if not pull:
                self._clear_push_mark()
            if pull and isinstance(res, dict) and res.get("merged", 0) > 0:
                self.refresh_all_views()
                self._reopen_pinned()
                if manual:
                    self.add_message(t("fed.sync_merged", n=res['merged'],
                                       v=res.get('version', '?')), "system")
                else:
                    print(f"[SYNC] {res['merged']} row(s) merged "
                          f"(v{res.get('version', '?')})", file=sys.stderr)
            elif manual:
                if pull:
                    self.add_message(t("fed.sync_nothing"), "system")
                elif res.get("unchanged"):
                    self.add_message(t("fed.sync_unchanged", v=res.get('version', '?'),
                                       rows=res.get('rows', '?')), "system")
                else:
                    self.add_message(t("fed.sync_uploaded", v=res.get('version', '?'),
                                       rows=res.get('rows', '?')), "system")

        self._bg(work, done)

    def refresh_all_views(self):
        for fn in ("refresh_notes_lists", "refresh_todo_lists", "refresh_cron_list",
                   "refresh_facts", "refresh_lessons"):
            try:
                getattr(self, fn, lambda: None)()
            except Exception:
                pass
        if getattr(self, "selected_category", None):
            self.refresh_notes_display()

    def _check_notices(self):
        def work():
            return self._mcp_call_json("fed_notices", {}, timeout=8)

        def done(res):
            notices = (res or {}).get("notices") if isinstance(res, dict) else None
            if not notices:
                return
            try:
                from PyQt6.QtCore import QSettings
                seen_raw = QSettings("hoplita", "hoplita").value("motd_seen") or ""
            except Exception:
                seen_raw = ""
            seen_raw = ",".join(x for x in (
                str(seen_raw), self.config.get("APP", "motd_seen", fallback="")) if x)
            seen_ids = set(x for x in seen_raw.split(",") if x)
            latest_motd = None
            for n in notices:
                nid = str(n.get("id"))
                if n.get("kind", "system") == "motd":
                    latest_motd = n
                    if nid in seen_ids:
                        continue
                    self.add_message(
                        t("motd.chat_line", text=n.get("text", "")), "system")
                    seen_ids.add(nid)
                else:
                    body = self._render_notice_body(n)
                    if body:
                        self.add_message(
                            body, "error" if n.get("level") == "critical"
                            else "system")
            if latest_motd is not None:
                try:
                    from PyQt6.QtCore import QSettings
                    _dis = str(QSettings("hoplita", "hoplita").value(
                        "motd_dismissed") or "").split(",")
                except Exception:
                    _dis = []
                if str(latest_motd.get("id", "")) not in _dis:
                    self._show_motd_banner(latest_motd)
            if seen_ids:
                self._remember_motd_seen(",".join(sorted(seen_ids)))

        self._bg(work, done)

    def _render_notice_body(self, n):
        code = n.get("code")
        if code:
            key = f"fed.{code}"
            loc = t(key, **(n.get("params") or {}))
            if loc != key:
                return loc
        return n.get("text") or code or ""

    def _show_motd_banner(self, m):
        c, bg = {"info": ("#7dd3fc", "#0c2c43"),
                 "warn": ("#fde68a", "#3a2c07"),
                 "critical": ("#fecaca", "#450a0a")}.get(
                     m.get("level"), ("#7dd3fc", "#0c2c43"))
        self.motd_banner.setStyleSheet(
            f"#motd_banner {{ background:{bg}; border-bottom:2px solid {c}; }} "
            f"#motd_banner QLabel {{ color:{c}; font-size:13px; }} "
            f"#motd_who {{ font-weight:800; letter-spacing:1px; "
            f"font-size:12px; }} "
            f"#motd_banner QPushButton {{ color:{c}; background:transparent; "
            f"border:none; font-weight:700; }}")
        _txt = (m.get("text", "") or "").strip()
        self._motd_full = _txt
        self._motd_id = str(m.get("id", ""))
        _one = " ".join(_txt.split())
        self._motd_text.setText(_one[:220] + ("…" if len(_one) > 220 else ""))
        self._motd_more.setVisible(len(_one) > 220 or "\n" in _txt)
        self.motd_banner.setVisible(True)

    def _refresh_plan(self):
        def work():
            ok, data, err = self._mcp_try("fed_quota", {}, timeout=20)
            data = dict(data or {})
            if not ok:
                data.setdefault("error", err or "?")
            return data

        def done(res):
            self.fed_status.set_quota(res)

        self._bg(work, done)

    def _open_federation_tab(self):
        panel = getattr(self, "federation_panel", None)
        if panel is not None:
            self.tabs.setCurrentWidget(panel)

    def _dismiss_motd(self):
        self.motd_banner.setVisible(False)
        try:
            from PyQt6.QtCore import QSettings
            _q = QSettings("hoplita", "hoplita")
            _seen = str(_q.value("motd_dismissed") or "")
            _id = str(getattr(self, "_motd_id", "") or "")
            if _id and _id not in _seen.split(","):
                _q.setValue("motd_dismissed",
                            ",".join(x for x in (_seen, _id) if x))
        except Exception:
            pass

    def _show_motd_full(self):
        dlg = QDialog(self)
        dlg.setWindowTitle(t("motd.from_server"))
        dlg.resize(620, 380)
        v = QVBoxLayout(dlg)
        box = QTextEdit()
        box.setReadOnly(True)
        box.setPlainText(getattr(self, "_motd_full", ""))
        v.addWidget(box, 1)
        b = QPushButton(t("btn.close"))
        b.clicked.connect(dlg.accept)
        v.addWidget(b)
        dlg.exec()

    def _remember_motd_seen(self, motd_id):
        try:
            from PyQt6.QtCore import QSettings
            QSettings("hoplita", "hoplita").setValue("motd_seen", motd_id)
        except Exception:
            pass

    def _code_rails_tick(self):
        if getattr(self, "_code_rail_busy", False):
            return
        self._code_rail_busy = True

        def done(res):
            self._code_rail_busy = False
            ok, r, _err = res if res else (False, {}, "")
            if not ok or not isinstance(r, dict):
                return
            for a in (r.get("applied") or []):
                if a.get("materialized"):
                    self.add_message(t("code.rail_materialized",
                                       project=a.get("project", "?"),
                                       path=a.get("path", "?")), "system")
                    self._fed_cache_refresh()
                elif a.get("error"):
                    self.add_message(t("code.rail_failed",
                                       project=a.get("project", "?"),
                                       err=str(a["error"])[:160]), "error")
                elif a.get("ref"):
                    self.add_message(t("code.rail_quarantined",
                                       project=a.get("project", "?"),
                                       who=a.get("from", "?"),
                                       n=len(a.get("commits") or [])), "system")
                    if getattr(self, "code_cowork_where", None) is not None \
                            and self._code_project:
                        self._code_cowork_paint(t("code.cowork_arrived",
                                                  who=a.get("from", "?")))
            for s in (r.get("served") or []):
                if s.get("error"):
                    self.add_message(t("code.rail_serve_failed",
                                       project=s.get("project", "?"),
                                       err=str(s["error"])[:160]), "error")
                elif s.get("parts"):
                    self.add_message(t("code.rail_served",
                                       project=s.get("project", "?"),
                                       who=s.get("to", "?")), "system")

        self._bg(lambda: self._mcp_try("fed_code_pull", {}, timeout=600), done)

    def _fed_cache_refresh(self):
        if getattr(self, "_fed_cache_busy", False):
            return
        self._fed_cache_busy = True
        def done(res):
            self._fed_cache_busy = False
            if isinstance(res, dict) and res.get("ok"):
                self._fed_cache = res
                self._populate_cron_targets()
                self._share_invites_from_cache(res)
                print(f"[FED-CACHE] ✓ {len(res.get('folders', {}))} folder(s), "
                      f"{len(res.get('projects', {}))} project(s), "
                      f"{len(res.get('contacts', []))} contact(s)")
            else:
                print(f"[FED-CACHE] ✗ overview failed: {res}")
        self._bg(lambda: self._mcp_call_json("fed_shared_overview", {},
                                             timeout=8), done)

    def _busy(self, on, msg=""):
        from PyQt6.QtWidgets import QApplication as _QA
        from PyQt6.QtCore import Qt as _Qt
        if on:
            _QA.setOverrideCursor(_Qt.CursorShape.BusyCursor)
        else:
            _QA.restoreOverrideCursor()
        lbl = getattr(self, "rag_status_label", None)
        if lbl is not None and msg:
            lbl.setText(msg)

    def _mcp_bg(self, tool, arguments, done=None, busy_msg=""):
        self._busy(True, busy_msg)

        def _work():
            return self._mcp_call(tool, arguments)

        def _done(res):
            self._busy(False)
            if done is not None:
                done(res)

        self._bg(_work, _done)

    def _merge_extra_body(self, payload, base_url, model):
        try:
            import hoplita_llmcfg
            return hoplita_llmcfg.merge_extra_body(
                self.config, payload, base_url, model,
                warn=lambda m: print(f"[CONFIG] {m}", flush=True))
        except Exception as e:
            print(f"[CONFIG] extra_body ignored: {e}", flush=True)
            return payload

    def _bg(self, fn, on_done=None):
        def run():
            try:
                res = fn()
            except Exception:
                res = None
            if on_done is not None:
                self._bg_done.emit(on_done, res)
        threading.Thread(target=run, daemon=True).start()

    def _on_bg_done(self, cb, res):
        if cb is None:
            return
        try:
            cb(res)
        except Exception as e:
            print(f"[GUI] bg callback failed: {e}")

    def _mcp_try(self, tool, arguments, timeout=10, url=None):
        try:
            if url is None:
                url = self._tab_url() if _tool_is_tabs(tool) else self._mcp_url
            r = requests.post(f"{url}/call",
                              json={"tool": tool, "arguments": arguments,
                                    "code_project": _Face_call_project(self),
                                    "turn_id": getattr(getattr(self, "_tls", None), "turn_id", "") or "",
                                    "background_job": getattr(getattr(self, "_tls", None), "bg_job", "") or ""},
                              headers=self._tab_headers(), timeout=timeout)
            if r.status_code != 200:
                return False, {}, self._http_error(r)
            result = (r.json() or {}).get("result", {})
            if not isinstance(result, dict):
                return True, {}, ""
            if result.get("error"):
                return False, result, str(result["error"])
            return True, result, ""
        except requests.exceptions.Timeout:
            return False, {}, t("mcp.err_timeout", s=timeout)
        except Exception as e:
            return False, {}, str(e)

    def _mcp_call_json(self, tool, arguments, timeout=10, url=None):
        ok, data, _ = self._mcp_try(tool, arguments, timeout, url=url)
        return data if ok else {}

    def _mcp_call(self, tool, arguments):
        try:
            timeout = 900 if tool in ("browse_web", "screenshot_web", "fed_spawn_child") else self.config.getint("CHAT", "timeout", fallback=120)
            print(f"[MCP_CALL] {tool} timeout={timeout}")
            url = self._tab_url() if _tool_is_tabs(tool) else self._mcp_url
            r = requests.post(f"{url}/call",
                              json={"tool": tool, "arguments": arguments,
                                    "code_project": _Face_call_project(self),
                                    "turn_id": getattr(getattr(self, "_tls", None), "turn_id", "") or "",
                                    "background_job": getattr(getattr(self, "_tls", None), "bg_job", "") or ""},
                              headers=self._tab_headers(), timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                result = data.get("result", {})
                if "error" in result:
                    return f"❌ {result['error']}"
                return json.dumps(result, ensure_ascii=False)
            try:
                _detail = str((r.json() or {}).get("detail") or "").strip()
            except Exception:
                _detail = ""
            return f"❌ {_detail}" if _detail else f"❌ MCP HTTP {r.status_code}"
        except Exception as e:
            return f"❌ MCP error: {e}"


    def _open_chat_session(self, peer):
        if not peer:
            return
        tid = self._ensure_green_tab(peer)
        try:
            self.tabs.setCurrentIndex(0)
        except Exception:
            pass
        self._switch_chat_tab(tid)
        _kids = {(c.get("handle") or "").lower()
                 for c in ((getattr(self, "_fed_cache", {}) or {}).get("children") or [])
                 if isinstance(c, dict)}
        key = ("fed.child_opened_chat" if self._is_local_child(peer)
               else "fed.child_remote_opened_chat" if peer.lower() in _kids
               else "fed.session_tab_ready")
        self._append_html_to_tab(tid, self._chat_frag(
            t(key, peer=peer), "#94a3b8", t("label.system")))

    def _session_command(self, active, msg):
        _cmd = msg.strip().split()[0].lower()
        if active.get("kind") == "red":
            self._append_html_to_tab(active["id"], self._chat_frag(
                t("fed.session_cmd_red", peer=active["peer"], cmd=_cmd),
                "#94a3b8", t("label.system")))
        elif _cmd == "/model" and len(msg.split()) > 1:
            self._append_html_to_tab(active["id"], self._chat_frag(
                t("fed.session_model_not_here", peer=active["peer"]),
                "#94a3b8", t("label.system")))
        elif _cmd == "/model":
            _m = self._session_models.get(active["id"])
            self._append_html_to_tab(active["id"], self._chat_frag(
                t("fed.session_model", peer=active["peer"], model=_m) if _m
                else t("fed.session_model_unknown", peer=active["peer"]),
                "#94a3b8", t("label.system")))
        else:
            self._append_html_to_tab(active["id"], self._chat_frag(
                t("fed.session_cmd_local", peer=active["peer"], cmd=_cmd),
                "#94a3b8", t("label.system")))

    def _session_no_ack(self, tid, peer):
        if tid in self._session_acked or self._tab_by_id(tid) is None:
            return
        self._append_html_to_tab(tid, self._chat_frag(
            t("fed.session_no_ack", peer=peer), "#f59e0b", t("label.system")))

    def _on_session_send(self, peer, message):
        def worker():
            try:
                r = requests.post(f"{self._mcp_url}/agent/session-send",
                                  json={"peer": peer, "message": message},
                                  timeout=10)
                if r.status_code != 200:
                    self.signals.session_event.emit(
                        peer, "out",
                        {"event": "error", "error": f"MCP HTTP {r.status_code}"})
            except Exception as e:
                self.signals.session_event.emit(
                    peer, "out", {"event": "error", "error": str(e)})

        threading.Thread(target=worker, daemon=True,
                         name=f"session-send-{peer}").start()

    def _on_session_inject(self, peer, message):
        def worker():
            try:
                r = requests.post(f"{self._mcp_url}/agent/session-inject",
                                  json={"peer": peer, "message": message},
                                  timeout=10)
                if r.status_code != 200:
                    self.signals.session_event.emit(
                        peer, "in",
                        {"event": "error", "error": f"MCP HTTP {r.status_code}"})
                else:
                    j = r.json()
                    if not j.get("ok"):
                        self.signals.session_event.emit(
                            peer, "in",
                            {"event": "error",
                             "error": j.get("error", "inject failed")})
            except Exception as e:
                self.signals.session_event.emit(
                    peer, "in", {"event": "error", "error": str(e)})

        threading.Thread(target=worker, daemon=True,
                         name=f"session-inject-{peer}").start()

    def _fed_lag_tag(self, ev):
        try:
            te = float(ev.get("t_exec") or 0)
            tr = float(ev.get("t_recv") or 0)
        except (TypeError, ValueError):
            return ""
        if not te or not tr:
            return ""
        lag = tr - te
        if lag < 3:
            return ""
        return " · " + t("fed.lag", s=int(round(lag)))

    def _on_session_event(self, peer, direction, ev):
        tid = f"green:{peer}" if direction == "out" else f"red:{peer}"
        col = "#22c55e" if direction == "out" else "#ef4444"
        if ev.get("event") in ("reply", "error", "cancelled", "session_closed"):
            self._session_waiting.discard(tid)
            self._enchant_t0.pop(tid, None)
            if tid == self._active_tab_id:
                self._set_enchanting(self._busy_now())
            self._tabs_working_check()
            _q = self._session_queue.get(tid) or []
            if _q and ev.get("event") == "reply":
                nxt = _q.pop(0)
                self._update_queue_label()
                QTimer.singleShot(300, lambda p=peer, m=nxt, d=direction, i=tid:
                                  self._send_queued_session(i, p, d, m))
        if ev.get("event") == "alert":
            self.signals.show_alert.emit(ev.get("title", "🔔"),
                                         ev.get("message", ""))
        if direction == "out" and ev.get("event") != "session_open":
            self._session_acked.add(tid)
        if direction == "out" and ev.get("event") == "llm_call" and ev.get("model"):
            self._session_models[tid] = str(ev.get("model"))
        if self._tab_by_id(tid) is not None:
            et = ev.get("event")
            if direction == "out":
                agent_label = f"🤖 {peer}"
            else:
                agent_label = "🤖 " + t("label.agent")
            if et == "reply":
                self._append_html_to_tab(tid, self._chat_frag(
                    ev.get("reply", ""), col, agent_label, rich=True))
            elif et == "user_message":
                _from = ev.get("from", peer)
                if _from == peer:
                    self._append_html_to_tab(tid, self._chat_frag(
                        ev.get("text", ""), "#38bdf8", f"👤 {_from}"))
            elif et == "session_open" and direction == "out":
                self._append_html_to_tab(tid, self._chat_frag(
                    t("fed.session_sent_waiting", peer=peer), "#94a3b8",
                    t("label.system")))
                self._session_acked.discard(tid)
                QTimer.singleShot(20000, lambda i=tid, p=peer: self._session_no_ack(i, p))
            elif et == "session_open":
                self._append_html_to_tab(tid, self._chat_frag(
                    t("fed.session_inbound_running", peer=peer), "#94a3b8",
                    t("label.system")))
            elif et == "session_ack":
                self._append_html_to_tab(tid, self._chat_frag(
                    t("fed.session_awaiting_approval" if ev.get("state") == "awaiting_approval"
                      else "fed.session_accepted", peer=peer),
                    "#94a3b8", t("label.system")))
            elif et == "tool_call":
                self._append_html_to_tab(tid, self._chat_frag(
                    f"🔧 {ev.get('tool')}" + self._fed_lag_tag(ev),
                    "#94a3b8", agent_label))
            elif et == "turn_start":
                self._append_html_to_tab(tid, self._chat_frag(
                    t("fed.session_thinking") + self._fed_lag_tag(ev),
                    "#94a3b8", agent_label))
            elif et == "canvas":
                _canvas_html = ev.get("html", "")
                if ev.get("append") and self._canvas(tid)["html"]:
                    _canvas_html = self._canvas(tid)["html"] + _canvas_html
                self._render_canvas_html(_canvas_html, origin=tid)
                self._append_html_to_tab(tid, self._chat_frag(
                    t("fed.session_canvas_rendered"), "#94a3b8", agent_label))
            elif et in ("error", "cancelled"):
                self._append_html_to_tab(tid, self._chat_frag(
                    f"❌ {ev.get('error', et)}", "#ef4444", agent_label))
        self._feed_me262_from_session(peer, direction, ev)

    def _on_session_closed(self, peer, direction):
        tid = f"green:{peer}" if direction == "out" else f"red:{peer}"
        tab = self._tab_by_id(tid)
        if tab is None:
            return
        self._append_html_to_tab(tid, self._chat_frag(
            t("fed.session_ended"), "#f59e0b", t("label.system")))
        tab["ended"] = True
        _c = self._chat_ctxs.get(tid)
        if _c is not None:
            _c.pending_ack = None
        self._render_chat_tabs()

    def _canvas_show_image(self, info):
        import html as _h
        src = str(info.get("src") or "")
        if not src:
            return
        cap = _h.escape(str(info.get("prompt") or ""))
        fig = (f'<figure style="display:inline-block;margin:12px;max-width:46%;'
               f'vertical-align:top;background:#0f172a;border:1px solid #1e293b;'
               f'border-radius:10px;padding:8px;">'
               f'<img src="{_h.escape(src)}" style="max-width:100%;border-radius:6px;display:block;">'
               f'<figcaption style="color:#94a3b8;font:12px sans-serif;margin-top:6px;">{cap}</figcaption>'
               f'</figure>')
        prev = self._canvas(self._emitting_tab())["html"]
        self._render_canvas_html((prev if "<figure" in prev else "") + fig)

    def _picture_result_to_canvas(self, result):
        try:
            data = json.loads(result) if isinstance(result, str) else result
        except Exception:
            return 0
        imgs = data.get("images") if isinstance(data, dict) else None
        if not isinstance(imgs, list):
            return 0
        n = 0
        for im in imgs:
            if not (isinstance(im, dict) and im.get("src")):
                continue
            self._canvas_show_image({"src": im["src"], "path": im.get("path", ""),
                                     "prompt": data.get("prompt", "")})
            self.thread_msg(t("image.generated", model=data.get("model") or "?",
                              path=im.get("path") or im["src"]), "system")
            n += 1
        return n

    def _office_view_to_canvas(self, info):
        try:
            with open(str(info.get("path") or ""), encoding="utf-8") as f:
                page = f.read()
        except OSError:
            return False
        self._render_canvas_html(page)
        return True

    @staticmethod
    def _canvas_local_pictures(html):
        import urllib.parse as _up
        out = []
        for m in re.finditer(r"""<img\b[^>]*\bsrc\s*=\s*["']([^"']+)""",
                             html or "", re.I):
            s = m.group(1).strip()
            if s.lower().startswith(("http://", "https://", "data:", "blob:", "//")):
                continue
            if s.lower().startswith("hoplita-media://"):
                try:
                    from dashboard_widget import media_path
                    s = media_path(s) or ""
                except Exception:
                    s = ""
            elif s.lower().startswith("file://"):
                s = _up.unquote(s[len("file://"):])
            p = os.path.expanduser(s) if s else ""
            if p and os.path.isfile(p) and p not in out:
                out.append(p)
        return out

    def _export_canvas_files(self, html, dest):
        import shutil
        pics = self._canvas_local_pictures(html)
        if len(pics) == 1:
            shutil.copyfile(pics[0], dest)
            return [dest]
        if pics:
            os.makedirs(dest, exist_ok=True)
            out = []
            for p in pics:
                q = os.path.join(dest, os.path.basename(p))
                shutil.copyfile(p, q)
                out.append(q)
            return out
        with open(dest, "w", encoding="utf-8") as f:
            f.write(html)
        return [dest]

    def _export_canvas(self):
        html = self._canvas(self._active_tab_id)["html"]
        if not html.strip():
            self.add_message(t("canvas.nothing"), "system")
            return
        from PyQt6.QtWidgets import QFileDialog
        pics = self._canvas_local_pictures(html)
        home = os.path.expanduser("~")
        if len(pics) == 1:
            dest, _ = QFileDialog.getSaveFileName(
                self, t("canvas.export_title"),
                os.path.join(home, os.path.basename(pics[0])))
        elif pics:
            dest = QFileDialog.getExistingDirectory(self, t("canvas.export_title"), home)
        else:
            dest, _ = QFileDialog.getSaveFileName(
                self, t("canvas.export_title"), os.path.join(home, "canvas.html"),
                "HTML (*.html)")
        if not dest:
            return
        try:
            saved = self._export_canvas_files(html, dest)
        except Exception as e:
            self.add_message(t("canvas.export_fail", err=e), "error")
            return
        self.add_message(t("canvas.exported", n=len(saved), path=saved[-1]), "system")

    def _canvas(self, tab_id):
        rec = self._canvases.get(tab_id)
        if rec is None:
            rec = self._canvases[tab_id] = {"html": "", "prev": "", "uuid": None,
                                            "open": False, "unseen": False}
        return rec

    def _render_canvas_html(self, html, origin=None):
        if self.dashboard is None or not (html or "").strip():
            return
        if origin is None:
            origin = getattr(self._tls, "tab_id", None) or ""
        tab = origin or self._active_tab_id
        rec = self._canvas(tab)
        _prev = rec["html"]
        if _prev and _prev != html:
            rec["prev"] = _prev
        rec["html"] = html
        try:
            from dashboard_widget import serve_local_images
            html = serve_local_images(html)
        except Exception:
            pass
        self.dashboard.render_html(html, key=tab)
        self.signals.canvas_painted.emit(origin)

    def _emitting_tab(self):
        return getattr(self._tls, "tab_id", None) or self._active_tab_id

    def _may_take_view(self, tab_id):
        if not tab_id or tab_id != self._active_tab_id:
            return False
        if self.tabs.currentIndex() != 0:
            return False
        entry = getattr(self, "entry", None)
        try:
            return not (entry is not None and entry.toPlainText().strip())
        except RuntimeError:
            return True

    def _on_canvas_painted(self, origin):
        tab = origin or self._active_tab_id
        rec = self._canvas(tab)
        if tab == self._active_tab_id and (
                rec["open"] or not origin or self._may_take_view(tab)):
            if not origin:
                self.tabs.setCurrentIndex(
                    self.tabs.indexOf(self.dashboard) if self._canvas_full else 0)
            self._canvas_open(tab)
            return
        rec["unseen"] = True
        self._render_chat_tabs()

    def _canvas_open(self, tab_id):
        rec = self._canvas(tab_id)
        rec["open"] = True
        rec["unseen"] = False
        if tab_id == self._active_tab_id and self.dashboard is not None:
            self.dashboard.show_tab(tab_id)
            self._curtain_set(self.dashboard, True)
        self._render_chat_tabs()

    def _toggle_canvas(self):
        if self.dashboard is None:
            return
        if self._canvas_full:
            self._canvas_set_full(False)
            return
        rec = self._canvas(self._active_tab_id)
        if rec["open"]:
            rec["open"] = False
            self._curtain_set(self.dashboard, False)
            self._render_chat_tabs()
        else:
            self._canvas_open(self._active_tab_id)

    def _canvas_sync(self):
        if self.dashboard is None:
            return False
        tab = self._active_tab_id
        rec = self._canvas(tab)
        before = (rec["open"], rec["unseen"])
        if self._canvas_full:
            rec["open"], rec["unseen"] = True, False
            self.dashboard.show_tab(tab)
            return (rec["open"], rec["unseen"]) != before
        if rec["unseen"] and not rec["open"] and self._may_take_view(tab):
            rec["open"] = True
        if rec["open"]:
            rec["unseen"] = False
        self.dashboard.show_tab(tab)
        self._curtain_set(self.dashboard, rec["open"])
        return (rec["open"], rec["unseen"]) != before

    def _canvas_full_toggle(self):
        self._canvas_set_full(not self._canvas_full)

    def _canvas_set_full(self, on):
        if self.dashboard is None or on == self._canvas_full:
            return
        self._canvas_full = on
        rec = self._canvas(self._active_tab_id)
        rec["open"], rec["unseen"] = True, False
        self.dashboard.show_tab(self._active_tab_id)
        _crec = self._curtains.get(self.dashboard)
        if on:
            if _crec is not None:
                if _crec["anim"] is not None:
                    _crec["anim"].stop()
                    _crec["anim"] = None
                _crec["open"] = False
                _crec["grip"].hide()
            _idx = self.tabs.insertTab(1, self.dashboard, t("tabs.dashboard"))
            self.tabs.setTabIcon(_idx, self._svg_icon("tab_canvas", "#94a3b8", 18))
            self.dashboard.setVisible(True)
            self.tabs.setCurrentIndex(_idx)
            self._canvas_full_btn.setText("⤡")
            self._canvas_full_btn.setToolTip(t("canvas.panel_tip"))
        else:
            _idx = self.tabs.indexOf(self.dashboard)
            if _idx >= 0:
                self.tabs.removeTab(_idx)
            self.dashboard.setParent(self._curtain_host)
            if _crec is not None:
                _crec["grip"].show()
            self.tabs.setCurrentIndex(0)
            self._curtain_set(self.dashboard, True)
            self._canvas_full_btn.setText("⤢")
            self._canvas_full_btn.setToolTip(t("canvas.full_tip"))
        self._render_chat_tabs()

    def _canvas_drop(self, tab_id):
        self._canvases.pop(tab_id, None)
        if self.dashboard is not None:
            self.dashboard.drop(tab_id)

    def _is_local_child(self, peer):
        cache = getattr(self, "_fed_cache", {}) or {}
        h = (peer or "").split(":", 1)[-1].strip().lower()
        if not h:
            return False
        return any(isinstance(c, dict) and c.get("local")
                   and (c.get("handle") or "").strip().lower() == h
                   for c in (cache.get("contacts") or []))

    def _feed_me262_from_session(self, peer, direction, ev):
        panel = getattr(self, "agents_panel", None)
        if panel is None:
            return
        et = ev.get("event")
        status = {"reply": "done", "error": "failed", "cancelled": "failed"}.get(
            et, "running")
        _mine = self._is_local_child(peer)
        if not hasattr(self, "_session_intent"):
            self._session_intent = {}
        if et == "intent":
            self._session_intent[peer] = [
                dict(i) for i in (ev.get("items") or []) if isinstance(i, dict)]
            self.signals.intent_changed.emit(self._active_tab_id or "")
        elif et in ("reply", "error", "cancelled"):
            self._session_intent.pop(peer, None)
            self.signals.intent_changed.emit(self._active_tab_id or "")
        _items = (getattr(self, "_session_intent", {}) or {}).get(peer) or []
        _now = next((i for i in _items
                     if str(i.get("status", "")).lower() in ("doing", "running")),
                    None) or next(
            (i for i in _items
             if str(i.get("status", "")).lower() not in ("done", "failed")), None)
        _goal = (str(_now.get("goal", "")).strip() if _now else "") \
            or (t("session.card_tool", tool=ev.get("tool"))
                if et == "tool_call" and ev.get("tool") else "") \
            or (t("session.card_goal_child", peer=peer) if _mine
                else t("session.card_goal", peer=peer))
        self._session_cards[peer] = {
            "id": peer[:8], "goal": _goal,
            "status": status, "origin": ("local" if _mine else direction),
            "peer": peer, "role": t("session.card_role_child") if _mine else ""}
        try:
            panel.update_state(self._agents_cards_for_active())
        except Exception:
            pass
        if et in ("reply", "error", "cancelled"):
            QTimer.singleShot(4000, lambda p=peer: (
                self._session_cards.pop(p, None),
                panel.update_state(self._agents_cards_for_active())))

    def _poll_session_events(self):
        if getattr(self, "_sess_poll_live", False):
            return
        self._sess_poll_live = True

        def work():
            try:
                r = requests.get(f"{self._mcp_url}/agent/session-events", timeout=3)
                return r.json() if r.status_code == 200 else None
            except Exception:
                return None

        self._bg(work, self._paint_session_events)

    def _paint_session_events(self, payload):
        self._sess_poll_live = False
        if not isinstance(payload, dict):
            return
        try:
            _seen = getattr(self, "_shares_shown", None)
            if _seen is None:
                _seen = set(); self._shares_shown = _seen
            for sh in payload.get("shares", []):
                key = (sh.get("from", ""), sh.get("folder_name", ""), sh.get("kind", ""))
                if not key[0] or key in _seen:
                    continue
                _seen.add(key)
                self.signals.share_incoming.emit(key[0], key[1], key[2] or "folder")
            for _bev in payload.get("background", []) or []:
                try:
                    self._deliver_background_event(_bev)
                except Exception as _e:
                    print(f"[BACKGROUND] deliver: {_e}", file=sys.stderr)
            if "background_live" in payload:
                self._paint_background_live(payload.get("background_live") or [])
            if "lend_live" in payload:
                self._paint_lend_live(payload.get("lend_live") or [])
            _wake = payload.get("wake", []) or []
            if _wake:
                kinds = {w.get("kind") for w in _wake}
                if kinds & {"notice", "resync"}:
                    self._schedule_wake_refresh("notices")
                if kinds & {"contacts", "resync"}:
                    self._schedule_wake_refresh("cache")
                if "content" in kinds:
                    self._schedule_wake_refresh("content")
            _fresh = list(payload.get("events", []) or [])
            _items = list(getattr(self, "_own_events_waiting", None) or []) + _fresh
            self._own_events_waiting = []
            if _fresh:
                print(f"[GUI] drained {len(_fresh)} session event(s)",
                      file=sys.stderr)
            for item in _items:
                peer = item.get("peer", "")
                direction = item.get("direction", "in")
                ev = item.get("event", {}) or {}
                if not peer:
                    continue
                if ev.get("event") in ("model_lent", "model_lent_done"):
                    self._on_model_lent(peer, ev)
                    continue
                if ev.get("event") == "session_refused":
                    _why = str(ev.get("why") or "no_permission")
                    if self._active_tab_id:
                        self._append_html_to_tab(self._active_tab_id, self._chat_frag(
                            t("fed.session_refused_" + _why, peer=peer),
                            "#f59e0b", t("label.system")))
                    continue
                if ev.get("event") == "session_closed":
                    self.signals.session_closed.emit(peer, direction)
                    continue
                _own = self._tab_by_sync_id(ev.get("tab"))
                _mine = bool(ev.get("tab") and direction == "in"
                             and peer and peer == self._fed_handle())
                if ev.get("mirror") and not _own:
                    continue
                if (not _own and _mine
                        and (not getattr(self, "_pinned_listed", False)
                             or getattr(self, "_pinned_waiting", None))):
                    self._own_events_waiting.append(item)
                    continue
                if not _own and _mine:
                    _own = self._own_tab_for(ev.get("tab"), ev)
                if _own:
                    if ev.get("event") == "tab_saved":
                        self._reload_tab_from_row(_own, ev.get("uuid") or "")
                        continue
                    _txt = self._session_event_text(ev)
                    if _txt:
                        self._append_html_to_tab(_own, self._chat_frag(
                            _txt, "#38bdf8", t("pin.from_elsewhere")))
                    continue
                if direction == "out":
                    self._ensure_green_tab(peer)
                else:
                    _tid = f"red:{peer}"
                    _existed = self._tab_by_id(_tid) is not None
                    self._ensure_red_tab(peer)
                    if not _existed:
                        self._switch_chat_tab(_tid)
                self.signals.session_event.emit(peer, direction, ev)
        except Exception:
            pass

    def _chat_tab_menu(self, tab_id, gpos):
        ctx = self._chat_ctxs.get(tab_id)
        tab = self._tab_by_id(tab_id)
        if tab is None:
            return
        menu = QMenu(self)
        if tab.get("kind") in ("green", "red"):
            act_close = menu.addAction(t("chat.tab_close"))
            if menu.exec(gpos) is act_close:
                self._close_chat_tab(tab_id)
            return
        pin = getattr(ctx, "pinned_device", None) if ctx is not None else None
        act_pin = act_unpin = act_why = None
        if pin:
            act_unpin = menu.addAction(t("pin.menu_unpin", name=pin.get("name", "?")))
        else:
            act_pin = menu.addAction(t("pin.menu_pin"))
        act_title = menu.addAction("✏ " + t("chat.tab_rename"))
        act_share = None
        _proj = getattr(ctx, "code_project", "") if ctx is not None else ""
        if _proj:
            act_share = menu.addAction("🤝 " + t("code.share_menu"))
            act_share.setToolTip(_proj)
        menu.addSeparator()
        _mode = ("incognito" if getattr(ctx, "no_log", False)
                 else "local" if getattr(ctx, "local_only", False)
                 else "full")
        log_menu = menu.addMenu("📓 " + t("log.menu"))
        acts_mode = {}
        for _m, _key in (("full", "log.mode_full"),
                         ("local", "log.mode_local"),
                         ("incognito", "log.mode_incognito")):
            a_ = log_menu.addAction(t(_key))
            a_.setCheckable(True)
            a_.setChecked(_mode == _m)
            acts_mode[a_] = _m
        _nfw = len(getattr(ctx, "blocked_tools", ()) or ()) if ctx else 0
        act_fw = menu.addAction(
            "🛡 " + (t("fwtab.menu_n", n=_nfw) if _nfw else t("fwtab.menu")))
        menu.addSeparator()
        act_why = menu.addAction(t("why.menu"))
        menu.addSeparator()
        act_close = menu.addAction(
            t("chat.tab_reset") if tab_id == "local" else t("chat.tab_close"))
        chosen = menu.exec(gpos)
        if chosen is None:
            return
        prev, self._active_tab_id = self._active_tab_id, tab_id
        try:
            if chosen is act_pin:
                self._handle_pin_command("")
            elif chosen is act_unpin:
                self._handle_pin_command("off")
            elif chosen is act_why:
                self._handle_why_command()
            elif chosen is act_title:
                self._rename_chat_tab(tab_id)
            elif act_share is not None and chosen is act_share:
                self._code_project = _proj
                self.code_project_edit.setText(_proj)
                if getattr(self, "_chat_stack", None) is not None \
                        and self._chat_stack.currentIndex() != getattr(
                            self, "_code_page_idx", -1):
                    self._toggle_code_view()
                self._code_activity(6, force=True)
            elif chosen in acts_mode:
                self._set_log_mode(tab_id, acts_mode[chosen])
            elif chosen is act_fw:
                self._edit_tab_firewall(tab_id)
        finally:
            self._active_tab_id = prev
        if chosen is act_close:
            self._close_chat_tab(tab_id)

    def _edit_tab_firewall(self, tab_id):
        ctx = self._chat_ctxs.get(tab_id)
        tab = self._tab_by_id(tab_id)
        if ctx is None or tab is None:
            return
        allt, gblocked = {}, set()
        try:
            r = requests.get(f"{self._tab_url(ctx)}/firewall", timeout=15)
            fw = r.json() if r.status_code == 200 else {}
            allt = fw.get("all_tools") or {}
            if isinstance(allt, list):
                allt = {n: "" for n in allt}
            gblocked = set(fw.get("blocked") or ())
        except Exception:
            allt = dict(getattr(self, "_all_tools", {}) or {})
            gblocked = set(getattr(self, "blocked_tools", ()) or ())
        offered = {k: v for k, v in allt.items() if k not in gblocked}
        from tools_firewall import TabFirewallDialog
        dlg = TabFirewallDialog(self, tab.get("title", ""), offered,
                                set(getattr(ctx, "blocked_tools", ()) or ()))
        if not dlg.exec():
            return
        self._set_tab_firewall(tab_id, dlg.blocked())

    def _set_tab_firewall(self, tab_id, blocked):
        ctx = self._chat_ctxs.get(tab_id)
        if ctx is None:
            return
        ctx.blocked_tools = set(n for n in (blocked or ()) if n)
        self.add_message(t("fwtab.set", n=len(ctx.blocked_tools))
                         if ctx.blocked_tools else t("fwtab.cleared"),
                         "system")
        if "run_command" in ctx.blocked_tools and \
                (getattr(ctx, "verify_cmd", "") or "").strip():
            self.add_message(t("fwtab.verify_warn", cmd=ctx.verify_cmd),
                             "error")
        self._render_chat_tabs()
        if getattr(ctx, "session_uuid", None):
            self._persist_tab(tab_id, quiet=True)

    def _rename_chat_tab(self, tab_id):
        ctx = self._chat_ctxs.get(tab_id)
        tab = self._tab_by_id(tab_id)
        if ctx is None or tab is None:
            return
        cur = getattr(ctx, "title", "") or tab.get("title", "")
        name, ok = QInputDialog.getText(self, t("chat.tab_rename_title"),
                                        t("chat.tab_rename_body"), text=cur)
        name = (name or "").strip()
        if not ok or not name or name == cur:
            return
        ctx.title = name
        tab["title"] = name
        self._render_chat_tabs()
        if getattr(ctx, "session_uuid", None):
            self._persist_tab(tab_id, name, quiet=True)
        self.add_message(t("chat.tab_renamed", name=name), "system")

    def _set_log_mode(self, tab_id, mode):
        ctx = self._chat_ctxs.get(tab_id)
        if ctx is None or mode not in ("full", "local", "incognito"):
            return
        ctx.no_log = (mode == "incognito")
        ctx.local_only = (mode == "local")
        self.add_message(t("log.set_full") if mode == "full"
                         else t("log.set_local") if mode == "local"
                         else t("log.set_incognito"), "system")
        self._render_chat_tabs()

    def _toggle_tab_logging(self, tab_id):
        ctx = self._chat_ctxs.get(tab_id)
        if ctx is None:
            return
        ctx.no_log = not getattr(ctx, "no_log", False)
        self.add_message(t("log.now_off") if ctx.no_log else t("log.now_on"),
                         "system")
        self._render_chat_tabs()

    def _tab_by_sync_id(self, sync_id):
        sync_id = (sync_id or "").strip()
        if not sync_id:
            return None
        for tid, c in (getattr(self, "_chat_ctxs", None) or {}).items():
            if getattr(c, "sync_id", "") == sync_id:
                return tid
        return None

    @staticmethod
    def _session_event_text(ev):
        e = ev.get("event")
        if e == "remote_input":
            return "👤 " + str(ev.get("message", ""))[:2000]
        if e == "user_message":
            return "👤 " + str(ev.get("text", ""))[:2000]
        if e == "tool_call":
            return f"🔧 {ev.get('tool', '?')}"
        if e == "reply":
            return str(ev.get("reply", ""))[:6000]
        if e == "error":
            return "❌ " + str(ev.get("error", ""))[:500]
        return None

    def _poll_pending_approvals(self):
        if getattr(self, "_approval_poll_live", False):
            return
        self._approval_poll_live = True

        def work():
            try:
                r = requests.get(f"{self._mcp_url}/agent/pending-approvals", timeout=3)
                return r.json() if r.status_code == 200 else None
            except Exception:
                return None

        self._bg(work, self._paint_pending_approvals)

    def _paint_pending_approvals(self, data):
        self._approval_poll_live = False
        if not isinstance(data, dict):
            return
        for p in data.get("pending", []):
            aid = p.get("id")
            if aid in self._approvals_shown:
                continue
            self._approvals_shown.add(aid)
            self.signals.session_incoming.emit(p.get("peer", ""),
                                               p.get("message", ""), aid or "")

    def _approval_still_pending(self, aid, box):
        try:
            r = requests.get(f"{self._mcp_url}/agent/pending-approvals", timeout=3)
            if r.status_code != 200:
                return
            live = {p.get("id") for p in r.json().get("pending", [])}
            expired = set(r.json().get("expired") or [])
        except Exception:
            return
        if aid not in live:
            box.setProperty("expired" if aid in expired else "withdrawn", True)
            box.done(0)

    def _on_session_incoming(self, peer, message, aid=""):
        from PyQt6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        if aid:
            self._approval_boxes[aid] = box
            watch = QTimer(box)
            watch.timeout.connect(lambda a=aid, b=box: self._approval_still_pending(a, b))
            watch.start(2000)
        box.setWindowTitle(t("session.incoming_title"))
        box.setText(t("session.incoming_ask", peer=peer))
        box.setInformativeText(message[:500])
        box.setIcon(QMessageBox.Icon.Question)
        yes = box.addButton(t("session.approve"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(t("session.reject"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        self._approval_boxes.pop(aid, None)
        if box.property("expired"):
            self.thread_msg(t("session.incoming_expired", peer=peer), "system")
            return
        if box.property("withdrawn"):
            self.thread_msg(t("session.incoming_withdrawn", peer=peer), "system")
            return
        approved = box.clickedButton() == yes
        landed = True
        try:
            r = requests.post(f"{self._mcp_url}/agent/resolve-approval",
                              json={"peer": peer, "approved": approved, "id": aid},
                              timeout=5)
            landed = bool((r.json() or {}).get("ok", True))
        except Exception as e:
            self.thread_msg(f"❌ resolve approval: {e}", "error")
        if not landed:
            self.thread_msg(t("session.incoming_expired", peer=peer), "system")
            return
        if approved:
            self.thread_msg("↘ " + t("fed.session_inbound_running", peer=peer),
                            "system")

    def _on_session_incoming_auto(self, peer):
        self.thread_msg("↘ " + t("fed.session_inbound_running", peer=peer),
                        "system")

    _WAKE_COALESCE_MS = 1500
    _WAKE_MIN_GAP_S = 20

    def _schedule_wake_refresh(self, what):
        pend = getattr(self, "_wake_pending", None)
        if pend is None:
            pend = set(); self._wake_pending = pend
        pend.add(what)
        tmr = getattr(self, "_wake_timer", None)
        if tmr is None:
            tmr = QTimer(self)
            tmr.setSingleShot(True)
            tmr.timeout.connect(self._run_wake_refresh)
            self._wake_timer = tmr
        if not tmr.isActive():
            tmr.start(self._WAKE_COALESCE_MS)

    def _run_wake_refresh(self):
        what = getattr(self, "_wake_pending", set())
        self._wake_pending = set()
        now = time.time()
        last = getattr(self, "_wake_last", {})
        if "content" in what:
            self.refresh_all_views()
        for key, fn in (("notices", self._check_notices),
                        ("cache", self._fed_cache_refresh)):
            if key not in what:
                continue
            if now - last.get(key, 0) < self._WAKE_MIN_GAP_S:
                continue
            last[key] = now
            fn()
        self._wake_last = last

    def _share_invites_from_cache(self, cache):
        seen = getattr(self, "_shares_shown", None)
        if seen is None:
            seen = set(); self._shares_shown = seen
        for kind in ("folders", "projects"):
            for name, info in ((cache.get(kind) or {}).items()):
                if info.get("role") != "member" or info.get("status") != "pending":
                    continue
                who = info.get("owner", "")
                key = (who, name, info.get("kind", "folder"))
                if not who or key in seen:
                    continue
                seen.add(key)
                self.signals.share_incoming.emit(who, name,
                                                 info.get("kind", "folder"))

    def _on_share_incoming(self, sender, folder_name, kind):
        from PyQt6.QtWidgets import QMessageBox
        is_project = (kind == "project")
        noun = t("share.project") if is_project else t("share.folder")
        box = QMessageBox(self)
        box.setWindowTitle(t("share.incoming_title"))
        box.setText(t("share.incoming_ask", who=sender, noun=noun, name=folder_name))
        box.setIcon(QMessageBox.Icon.Question)
        yes = box.addButton(t("share.accept"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(t("share.reject"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() != yes:
            return
        tool, args = (("fed_accept_project", {"project_name": folder_name, "handle": sender})
                      if is_project else
                      ("fed_accept_folder", {"handle": sender, "folder_name": folder_name}))

        def work():
            return self._mcp_call(tool, args)

        def done(res):
            if isinstance(res, str) and res.startswith("❌"):
                self.add_message(t("share.accept_fail", err=res), "error")
                return
            self.add_message(t("share.accepted", noun=noun, name=folder_name), "system")
            try:
                self._fed_cache_refresh()
                self.refresh_all_views()
            except Exception:
                pass

        self._bg(work, done)

    def _mcp_feedback(self, goal, steps, result, feedback, tags=None, duration=0,
                      good_steps=None, note=""):
        try:
            r = requests.post(f"{self._mcp_url}/feedback", json={
                "goal": goal, "steps": steps, "result": result[:1000],
                "feedback": feedback, "tags": tags or [], "duration_s": duration,
                "partial_good_steps": good_steps or [],
                "note": note or "",
            }, timeout=5)
            if r.status_code != 200:
                print(f"[FEEDBACK] MCP HTTP {r.status_code}: {r.text[:200]}",
                      file=sys.stderr)
        except Exception as e:
            print(f"[FEEDBACK] Could not reach the MCP at {self._mcp_url}: {e}",
                  file=sys.stderr)


    def _init_ui(self):
        from PyQt6.QtCore import Qt as _Qt
        import mac_dialogs
        mac_dialogs.install()
        self.setWindowTitle(t("app.title") + "  " + HOPLITA_LABEL)
        self.setWindowFlag(_Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(_Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(1000, 750)
        self._setup_ui()
        self._apply_dark_theme()
        self._setup_tray()
        QApplication.instance().installEventFilter(self)
        self._setup_shortcuts()
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.tabs.tabBarClicked.connect(self._on_tab_clicked)

        self.cron_scheduler = CronScheduler(
            self.cron_manager, self._execute_cron_instruction, self.signals)
        self.cron_scheduler.start()


    def _on_thread_message(self, text, sender):
        self.add_message(text, sender)

    def _on_thread_message_tab(self, text, sender, tab_id):
        self.add_message(text, sender, tab_id or None)

    def _on_stream_token(self, token, tab_id=""):
        tab_id = tab_id or self._active_tab_id
        self._stream_bufs[tab_id] = self._stream_bufs.get(tab_id, "") + token
        live = self._active_stream_live and self._stream_live_tab == tab_id
        if not live and tab_id != self._active_tab_id:
            return
        view = self._chat_view(tab_id)
        if not live:
            self._active_stream_live = True
            self._stream_live_tab = tab_id
            self._deferred_messages = []
            end_cursor = view.textCursor()
            end_cursor.movePosition(end_cursor.MoveOperation.End)
            self._stream_start_pos = end_cursor.position()
            self._stream_ts = datetime.now().strftime("%H:%M:%S")
            header = (f'<span style="color:#64748b">[{self._stream_ts}]</span> '
                      f'<span style="color:#e2e8f0;font-weight:bold">IA:</span> '
                      f'<span style="color:#e2e8f0">')
            view.append_block(header)
        cursor = QTextCursor(view.document())
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(token)
        if view.follow:
            sb = view.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _on_stream_done(self, tab_id=""):
        tab_id = tab_id or self._active_tab_id
        buf = self._stream_bufs.pop(tab_id, "")
        ts = getattr(self, "_stream_ts", datetime.now().strftime("%H:%M:%S"))
        live_here = self._active_stream_live and self._stream_live_tab == tab_id
        view = self._chat_view(tab_id)
        rate = self._rating_footer(tab_id, buf)
        if live_here and buf.strip() and _MARKDOWN_OK:
            start = getattr(self, "_stream_start_pos", None)
            if start is not None:
                keep = None if view.follow else view.verticalScrollBar().value()
                cursor = QTextCursor(view.document())
                cursor.setPosition(start)
                cursor.movePosition(cursor.MoveOperation.End,
                                    cursor.MoveMode.KeepAnchor)
                cursor.removeSelectedText()
                body = self._render_markdown(buf, "#e2e8f0")
                html = (f'<span style="color:#64748b">[{ts}]</span> '
                        f'<span style="color:#e2e8f0;font-weight:bold">IA:</span> '
                        f'{body}{rate}<br>')
                cursor.insertHtml(html)
                cursor.movePosition(cursor.MoveOperation.End)
                cursor.insertHtml('</span><br>')
                if keep is not None:
                    view.verticalScrollBar().setValue(keep)
        elif buf.strip():
            body = self._render_markdown(buf, "#e2e8f0") if _MARKDOWN_OK else buf
            html = (f'<span style="color:#64748b">[{ts}]</span> '
                    f'<span style="color:#e2e8f0;font-weight:bold">IA:</span> '
                    f'{body}{rate}<br>')
            self._append_html_to_tab(tab_id, html)
        if self._stream_live_tab == tab_id:
            self._active_stream_live = False
            self._stream_live_tab = None
            deferred = self._deferred_messages
            self._deferred_messages = []
            for msg, snd in deferred:
                self._paint_message(msg, snd, tab_id)
        view.scroll_bottom()

    def _rating_footer(self, tab_id, reply):
        if not (reply or "").strip():
            return ""
        ctx = self._chat_ctxs.get(tab_id)
        goal = ""
        for m in reversed(getattr(ctx, "history", []) or []):
            if m.get("role") == "user":
                c = m.get("content")
                goal = c if isinstance(c, str) else str(c)
                break
        self._rate_seq = getattr(self, "_rate_seq", 0) + 1
        rid = self._rate_seq
        store = getattr(self, "_rate_pending", None)
        if store is None:
            store = self._rate_pending = {}
        store[rid] = {"goal": (goal or "")[:400], "result": reply[:2000],
                      "tab": tab_id,
                      "cron_job_id": (getattr(self, "_current_cron_job_id", None)
                                      if tab_id == CRON_TAB_ID else None),
                      "steps": list(getattr(ctx, "turn_steps", []) or [])}
        for old in [k for k in store if k <= rid - 200]:
            store.pop(old, None)
        return (f'&nbsp;&nbsp;<a href="hoplita:rate:ok:{rid}" '
                f'style="text-decoration:none;font-size:12px;">👍</a>'
                f'&nbsp;<a href="hoplita:rate:bad:{rid}" '
                f'style="text-decoration:none;font-size:12px;">👎</a>')

    def _rate_reply(self, rid, verdict):
        rec = (getattr(self, "_rate_pending", {}) or {}).pop(rid, None)
        if rec is None:
            return
        note, keep, good = self._ask_rating_note(verdict, rec)
        if not keep:
            self._rate_pending[rid] = rec
            return
        steps = rec.get("steps") or [{"step": 1, "action": "reply",
                                      "detail": rec["result"][:200]}]
        if verdict != "ok" and good is not None:
            verdict = "partial" if good else "fail"
        self._mcp_feedback(goal=rec["goal"], steps=steps,
                           result=rec["result"], feedback=verdict,
                           tags=["gui"], note=note, good_steps=good or [])
        self.add_message(
            t("rate.thanks_partial", n=len(good)) if verdict == "partial"
            else t("rate.thanks_ok" if verdict == "ok" else "rate.thanks_bad"),
            "system")
        _cjid = rec.get("cron_job_id")
        if _cjid and verdict != "ok" and (note or "").strip():
            self.add_message(t("cron.regen_started"), "system")
            self._current_cron_job_id = _cjid
            self._mcp_call("set_cron_context", {"job_id": _cjid})
            self._spawn_turn(CRON_TAB_ID, self._cron_supervisor_turn,
                             note, t("cron.regen_prefix", job=_cjid))

    def _ask_rating_note(self, verdict, rec):
        dlg = QDialog(self)
        dlg.setWindowTitle(t("rate.title_ok" if verdict == "ok"
                             else "rate.title_bad"))
        dlg.resize(620, 300)
        v = QVBoxLayout(dlg)
        head = QLabel(t("rate.scope"))
        head.setWordWrap(True)
        head.setStyleSheet("QLabel { color:#e6edf7; font-size:12px; }")
        v.addWidget(head)
        what = QPlainTextEdit(f"» {rec['goal'][:300]}\n\n{rec['result'][:800]}")
        what.setReadOnly(True)
        what.setFixedHeight(110)
        what.setStyleSheet("QPlainTextEdit { background:#0f172a; color:#8296b1; "
                           "border:1px solid #334155; font-size:12px; padding:6px; }")
        v.addWidget(what)
        boxes = []
        steps = rec.get("steps") or []
        if steps:
            counts = {}
            for st in steps:
                a_ = st.get("action", "?")
                counts[a_] = counts.get(a_, 0) + 1
            top = sorted(counts.items(), key=lambda kv: -kv[1])
            summary = " · ".join(f"{n}× {name}" for name, n in top[:6])
            if len(top) > 6:
                summary += f" · +{len(top) - 6}"
            lbl = QLabel(t("rate.usage", n=len(steps), detail=summary))
            lbl.setWordWrap(True)
            repeated = top[0][1] if top else 0
            lbl.setStyleSheet(
                "QLabel { color:%s; font-size:12px; }"
                % ("#fbbf24" if repeated >= 5 else "#8296b1"))
            v.addWidget(lbl)
        if verdict != "ok" and len(steps) > 1:
            v.addWidget(QLabel(t("rate.steps_hint")))
            for i, st in enumerate(steps):
                cb = QCheckBox(f"{st.get('step', i + 1)}. {st.get('action', '?')}"
                               f"  {str(st.get('detail', ''))[:70]}")
                cb.setStyleSheet("QCheckBox { color:#cbd5e1; font-size:12px; }")
                v.addWidget(cb)
                boxes.append(cb)
        v.addWidget(QLabel(t("rate.why_ok" if verdict == "ok" else "rate.why_bad")))
        box = QPlainTextEdit()
        box.setPlaceholderText(t("rate.why_ph"))
        box.setStyleSheet("QPlainTextEdit { background:#1e293b; color:#f1f5f9; "
                          "border:1px solid #334155; padding:6px; }")
        v.addWidget(box, 1)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton(t("btn.cancel"))
        cancel.clicked.connect(dlg.reject)
        row.addWidget(cancel)
        save = QPushButton(t("rate.save"))
        save.setDefault(True)
        save.clicked.connect(dlg.accept)
        row.addWidget(save)
        v.addLayout(row)
        keep = dlg.exec() == QDialog.DialogCode.Accepted
        good = ([i for i, cb in enumerate(boxes) if cb.isChecked()]
                if boxes else None)
        return (box.toPlainText().strip() if keep else ""), keep, good

    def _stream_start(self, tab_id=""):
        self._stream_bufs[tab_id or self._active_tab_id] = ""

    def _on_update_rag_status(self):
        self.update_rag_status()

    def _on_refresh_todo(self):
        self.refresh_todo_lists()

    def _on_refresh_cron(self):
        _lst = getattr(self, "cron_jobs_list", None)
        _cur = None
        if _lst is not None and _lst.currentRow() >= 0:
            _it = _lst.item(_lst.currentRow())
            _cur = ((_it.data(Qt.ItemDataRole.UserRole) or {}).get("id")
                    if _it is not None else None)
        self.refresh_cron_list()
        if _lst is None or _cur is None:
            return
        for _i in range(_lst.count()):
            _j = _lst.item(_i).data(Qt.ItemDataRole.UserRole) or {}
            if _j.get("id") == _cur:
                _lst.setCurrentRow(_i)
                self._on_cron_job_selected(_i)
                break

    def _on_refresh_notes(self):
        try: self.refresh_notes_lists()
        except: pass

    def _on_switch_to_agents(self, tab_id=""):
        if getattr(self, "agents_panel", None) is None \
                or getattr(self, "_chat_stack", None) is None:
            return
        if tab_id and tab_id != self._active_tab_id:
            self._render_chat_tabs()
            return
        _ctx = self._chat_ctxs.get(self._active_tab_id)
        if getattr(_ctx, "pending_ack", None) is None \
                and not self._agents_cards_for_active():
            self._render_chat_tabs()
            return
        self._refresh_agents_view()
        self._chat_stack.setCurrentIndex(1)
        self._render_chat_tabs()

    _WB_WHATS = {"changes": ("code.wb_draw_changes", "code.wb_draw_changes_tip", "code.wb_what_changes"),
                 "commit": ("code.wb_draw_commit", "code.wb_draw_commit_tip", "code.wb_what_commit"),
                 "overview": ("code.wb_draw_overview", "code.wb_draw_overview_tip", "code.wb_what_overview"),
                 "history": ("code.wb_draw_history", "code.wb_draw_history_tip", "code.wb_what_history")}
    _WB_REFUSALS = {"no_changes": "code.wb_no_changes", "no_parent": "code.wb_no_parent",
                    "model_unknown": "code.wb_model_unknown", "up_to_date": "code.wb_up_to_date",
                    "not_updatable": "code.wb_not_updatable", "still_behind": "code.wb_still_behind",
                    "empty": "code.wb_empty", "no_answers": "code.wb_no_answers",
                    "nothing_incoming": "code.wb_nothing_incoming", "incoming_gone": "code.wb_incoming_gone",
                    "no_history": "code.wb_no_history"}

    _WB_STAGES = {"mapping": "code.wb_stage_mapping", "planning": "code.wb_stage_planning",
                  "exploring": "code.wb_stage_exploring", "writing": "code.wb_stage_writing",
                  "checking": "code.wb_stage_checking", "history": "code.wb_stage_history"}

    def _wb_phrase(self, what):
        if what in self._WB_WHATS:
            return self._WB_WHATS[what][2]
        if what == "incoming":
            return "code.wb_what_incoming"
        return "code.wb_what_redraw" if what == "redraw" else "code.wb_what_update"

    def _code_wb_shown(self, wid):
        self._wb_shown_id = wid or ""
        if wid and self._wb_to_follow:
            self._wb_to_follow = {j for j in self._wb_to_follow if self._wb_job_board(j) in ("", wid)}
        self._code_wb_poll()

    def _wb_job_board(self, jid):
        try:
            for j in requests.get(f"{self._wb_url()}/wb/api/draw", timeout=3).json().get("jobs", []):
                if j["id"] == jid:
                    return j.get("whiteboardId") or ""
        except Exception:
            pass
        return ""

    def _code_wb_redraw(self, wid=""):
        wid = wid or getattr(self, "_wb_shown_id", "")
        if not wid:
            return
        root = (self._code_project or "").rstrip("/\\")
        self._code_wb_start({"root": root, "what": "redraw", "whiteboardId": wid},
                            lambda: self._code_wb_redraw(wid))

    def _code_wb_delete(self, wid=""):
        wid = wid or getattr(self, "_wb_shown_id", "")
        if not wid:
            return
        try:
            r = requests.post(f"{self._wb_url()}/call", json={"tool": "wb_delete",
                                                             "arguments": {"whiteboardId": wid}}, timeout=15).json()
            r = r.get("result", r)
        except Exception as e:
            r = {"error": str(e)}
        if isinstance(r, dict) and r.get("error"):
            self.code_wb_status.setText(t("code.wb_failed", error=r["error"]))
            return
        self.code_wb_status.setText(t("code.wb_deleted"))
        if self.whiteboard is not None:
            self.whiteboard.refresh()

    _WB_BG, _WB_SEG, _WB_LINE, _WB_ON = "#0f172a", "#0b1224", "#1f2a44", "#1b2744"

    def _code_build_wb_page(self, _btn):
        page = QWidget()
        pv = QVBoxLayout(page)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)
        head = QWidget()
        head.setObjectName("wbHead")
        head.setStyleSheet("#wbHead { background:%s; }" % self._WB_BG)
        hv = QVBoxLayout(head)
        hv.setContentsMargins(16, 9, 16, 7)
        hv.setSpacing(5)
        hb = ShrinkRow()
        hb.row().setContentsMargins(0, 0, 0, 0)
        hb.row().setSpacing(10)
        hv.addWidget(hb)
        _h1 = QLabel("\U0001F9ED  " + t("code.wb_title"))
        _h1.setStyleSheet("QLabel { color:#e6edf7; font-size:12px; font-weight:700; letter-spacing:1.5px; }")
        hb.add(_h1)
        self.code_wb_where = ElidedLabel("", hug=True)
        self.code_wb_where.setStyleSheet(
            "QLabel { color:#e6edf7; font-family:monospace; font-size:12px; }")
        hb.add(self.code_wb_where)
        hb.addStretch(1)
        self.code_wb_model = QComboBox()
        self.code_wb_model.setToolTip(t("code.wb_model_tip"))
        self.code_wb_model.setCursor(Qt.CursorShape.PointingHandCursor)
        self.code_wb_model.activated.connect(self._code_wb_model_picked)
        self._wb_model_css = (
            "QComboBox { background:%s; color:#e6edf7; border:1px solid %%s; border-radius:10px;"
            " padding:5px 4px 5px 12px; font-size:12px; }"
            "QComboBox:hover { border-color:#334155; }"
            "QComboBox:disabled { color:#b6c2d6; }"
            "QComboBox::drop-down { border:none; width:24px; }"
            "QComboBox::down-arrow { image:url(%s); width:11px; height:11px; }"
            "QComboBox QAbstractItemView { background:%s; color:#e6edf7; border:1px solid %s;"
            " selection-background-color:%s; outline:0; padding:4px; }"
            % (self._WB_SEG, self._wb_chevron(), self._WB_BG, self._WB_LINE, self._WB_ON))
        self.code_wb_model.setStyleSheet(self._wb_model_css % self._WB_LINE)
        hb.add(self.code_wb_model)
        self._wb_status_row = QWidget()
        sr = QHBoxLayout(self._wb_status_row)
        sr.setContentsMargins(2, 0, 0, 0)
        sr.setSpacing(8)
        self.code_wb_status = ElidedLabel("")
        self.code_wb_status.setStyleSheet("QLabel { color:#e6edf7; font-size:12px; }")
        self.code_wb_status.changed = lambda text: self._wb_status_row.setVisible(
            bool(text) or self.code_wb_stop.isVisible())
        sr.addWidget(self.code_wb_status, 1)
        self.code_wb_stop = QPushButton(t("code.wb_stop"))
        self.code_wb_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.code_wb_stop.setStyleSheet(
            "QPushButton { background:transparent; color:#d29922; border:1px solid #4a3a17; border-radius:7px;"
            " padding:2px 10px; font-size:12px; } QPushButton:hover { background:#2a2112; }")
        self.code_wb_stop.clicked.connect(self._code_wb_stop)
        self.code_wb_stop.setVisible(False)
        self.code_wb_reads = QPushButton("")
        self.code_wb_reads.setCursor(Qt.CursorShape.PointingHandCursor)
        self.code_wb_reads.setToolTip(t("code.wb_reads_tip"))
        self.code_wb_reads.setStyleSheet(
            "QPushButton { background:transparent; color:#b6c2d6; border:1px solid %s; border-radius:7px;"
            " padding:2px 10px; font-size:12px; } QPushButton:hover { background:#131d33; color:#e6edf7; }"
            "QPushButton[alarm=\"true\"] { color:#f0a8a8; border-color:#7f1d1d; }" % self._WB_LINE)
        self.code_wb_reads.clicked.connect(self._wb_show_reads)
        self.code_wb_reads.setVisible(False)
        sr.addWidget(self.code_wb_reads)
        sr.addWidget(self.code_wb_stop)
        self._wb_status_row.setVisible(False)
        hv.addWidget(self._wb_status_row)
        pv.addWidget(head)
        self.whiteboard = None
        try:
            from whiteboard_widget import WhiteboardWidget
            self.whiteboard = WhiteboardWidget(self._wb_url)
            self.whiteboard.host_request.connect(self._on_wb_host)
            self.whiteboard.shown.connect(self._code_wb_shown)
            pv.addWidget(self.whiteboard, 1)
        except Exception as e:
            print(f"[GUI] whiteboard unavailable: {e}", file=sys.stderr)
            _na = QLabel(t("code.wb_unavailable", e=str(e)))
            _na.setWordWrap(True)
            _na.setStyleSheet("QLabel { color:#e6edf7; padding:20px; }")
            pv.addWidget(_na, 1)
        self._wb_timer = QTimer(self)
        self._wb_timer.setInterval(2000)
        self._wb_timer.timeout.connect(self._code_wb_poll)
        self._wb_to_follow = set()
        self._wb_remote_ok = set()
        self._wb_asked = set()
        self._wb_root_shown = None
        self._wb_pending = None
        self._wb_auto_updated = set()
        return page

    def _wb_models(self):
        try:
            base = self._resolve_endpoint("API")[2]
        except Exception:
            base = ""
        def short(m):
            m = (m or "").rsplit("/", 1)[-1]
            return re.sub(r"\.(gguf|bin|safetensors)$", "", m, flags=re.I) or "?"
        import hoplita_llmcfg
        b_alias = hoplita_llmcfg.base_alias(self.config)
        try:
            pool = self._model_pool()
        except Exception:
            pool = {}
        b_alias = next((a for a in pool if a.lower() == b_alias.lower()), "")
        self._wb_model_full = {"base": base}
        out = [("base", b_alias or short(base))]
        for a, m in pool.items():
            if a == b_alias:
                continue
            self._wb_model_full[a] = m.get("model") or ""
            out.append((a, a))
        return out

    def _wb_saved_model(self):
        try:
            from PyQt6.QtCore import QSettings
            saved = str(QSettings("hoplita", "hoplita").value("whiteboard/model") or "")
        except Exception:
            return ""
        import hoplita_llmcfg
        b_alias = hoplita_llmcfg.base_alias(self.config)
        return "base" if saved and b_alias and saved.lower() == b_alias.lower() else saved

    def _code_wb_fill_models(self):
        opts = self._wb_models()
        saved = self._wb_saved_model()
        cb = self.code_wb_model
        cb.blockSignals(True)
        cb.clear()
        known = [a for a, _l in opts]
        if len(opts) > 1 and saved not in known:
            cb.addItem(t("code.wb_model_pick"), None)
        for a, lbl in opts:
            cb.addItem(lbl, a)
            cb.setItemData(cb.count() - 1, getattr(self, "_wb_model_full", {}).get(a, ""),
                           Qt.ItemDataRole.ToolTipRole)
        pick = saved if saved in known else (opts[0][0] if len(opts) == 1 else None)
        cb.setCurrentIndex(max(0, cb.findData(pick)) if pick else 0)
        cb.setEnabled(len(opts) > 1)
        cb.blockSignals(False)
        self._wb_fit_pill()

    def _wb_chevron(self):
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            src = next((pth for pth in (os.path.join(here, "icons", "chevron_down.svg"),
                                         os.path.join(here, "..", "icons", "chevron_down.svg"))
                        if os.path.isfile(pth)), "")
            if not src:
                return ""
            import tempfile
            cache = os.path.join(tempfile.gettempdir(),
                                 f"hoplita-icons-{os.getuid() if hasattr(os, 'getuid') else 'user'}")
            os.makedirs(cache, exist_ok=True)
            out = os.path.join(cache, "chevron_down_7d8aa3.svg")
            with open(src, encoding="utf-8") as fh:
                svg = fh.read().replace("currentColor", "#7d8aa3")
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(svg)
            return out.replace(os.sep, "/")
        except Exception:
            return ""

    def _wb_fit_pill(self):
        cb = self.code_wb_model
        from PyQt6.QtGui import QFont, QFontMetrics
        f = QFont(cb.font())
        f.setPixelSize(12)
        fm = QFontMetrics(f)
        cb.setFixedWidth(fm.horizontalAdvance(cb.currentText()) + 12 + 24 + 4 + 2 + 14)
        widest = max([fm.horizontalAdvance(cb.itemText(i)) for i in range(cb.count())] or [0])
        cb.view().setMinimumWidth(widest + 40)

    def _code_wb_model(self):
        opts = self._wb_models()
        if len(opts) == 1:
            return opts[0][0]
        return self.code_wb_model.currentData()

    def _code_wb_model_picked(self, idx):
        alias = self.code_wb_model.itemData(idx)
        if not alias:
            return
        try:
            from PyQt6.QtCore import QSettings
            QSettings("hoplita", "hoplita").setValue("whiteboard/model", alias)
        except Exception:
            pass
        self._code_wb_fill_models()
        self.code_wb_model.setStyleSheet(self._wb_model_css % self._WB_LINE)
        if self._wb_pending:
            fn, self._wb_pending = self._wb_pending, None
            fn()

    def _wb_url(self):
        return (self._tab_url() or "").rstrip("/")

    def _code_wb_paint(self):
        root = (self._code_project or "").rstrip("/\\")
        if getattr(self, "code_wb_where", None) is None:
            return
        self.code_wb_where.setText(os.path.basename(root) if root else t("code.side_none"))
        self._code_wb_fill_models()
        wb = self.whiteboard
        if wb is None:
            return
        wb.mcp_url = self._wb_url()
        if not wb.loaded or (self._wb_root_shown and self._wb_root_shown != root):
            wb.show_whiteboard(None, root)
        self._wb_root_shown = root
        self._wb_timer.start()
        self._code_wb_poll()

    def _code_wb_home(self):
        wb = self.whiteboard
        if wb is not None:
            wb.mcp_url = self._wb_url()
            self._wb_root_shown = (self._code_project or "").rstrip("/\\")
            wb.show_whiteboard(None, self._wb_root_shown)

    def _code_wb_draw(self, what, ref=None, since="", until=""):
        root = (self._code_project or "").rstrip("/\\")
        if not root:
            self._code_say(t("code.no_project"), bad=True)
            return
        try:
            f = requests.get(f"{self._wb_url()}/wb/api/find",
                             params=dict({"root": root, "what": what}, **({"ref": ref} if ref else {}),
                                         **({"since": since, "until": until} if what == "history" else {})),
                             timeout=10).json()
        except Exception:
            f = {}
        wid = (f.get("running") or {}).get("whiteboardId") or f.get("whiteboardId")
        wb = self.whiteboard
        self._wb_to_follow = {f["running"]["id"]} if f.get("running") else set()
        if wid and wb is not None:
            wb.mcp_url = self._wb_url()
            wb.show_whiteboard(wid, root)
            self._wb_timer.start()
            self._code_wb_poll()
            return
        if f.get("running"):
            return
        self._code_wb_start(dict({"root": root, "what": what}, **({"ref": ref} if ref else {}),
                                 **({"since": since, "until": until} if what == "history" else {})),
                            lambda: self._code_wb_draw(what, ref, since, until))

    def _code_wb_update(self, wid, root, current="", auto=False):
        if auto:
            if (wid, current) in self._wb_auto_updated:
                return
            self._wb_auto_updated.add((wid, current))
        root = (root or self._code_project or "").rstrip("/\\")
        if not root or not wid:
            return
        self._code_wb_start({"root": root, "what": "update", "whiteboardId": wid},
                            lambda: self._code_wb_update(wid, root))

    def _code_wb_start(self, body, again):
        alias = self._code_wb_model()
        if not alias:
            self._wb_pending = again
            self.code_wb_status.setText(t("code.wb_pick_model"))
            self.code_wb_model.setStyleSheet(self._wb_model_css % "#d29922")
            return
        root_ = (body.get("root") or "").rstrip("/\\")
        _blocked = set()
        for _c in (getattr(self, "_chat_ctxs", {}) or {}).values():
            if (getattr(_c, "code_project", "") or "").rstrip("/\\") == root_:
                _blocked |= set(getattr(_c, "blocked_tools", ()) or ())
        try:
            r = requests.post(f"{self._wb_url()}/wb/api/draw",
                              json=dict(body, model=alias, blocked=sorted(_blocked),
                                        remoteOk=(root_, alias) in self._wb_remote_ok),
                              timeout=10).json()
        except Exception as e:
            self.code_wb_status.setText(t("code.wb_failed", error=str(e)))
            return
        if r.get("error"):
            self.code_wb_status.setText(t("code.wb_failed", error=r["error"]))
            return
        if r.get("id"):
            self._wb_to_follow.add(r["id"])
        self._wb_timer.start()
        self._code_wb_poll()

    def _wb_ask_remote(self, job):
        g = job.get("gate") or {}
        root_ = (job.get("root") or "").rstrip("/\\")
        yes = QMessageBox.question(
            self, t("code.wb_remote_title"),
            t("code.wb_remote_q", model=g.get("model", "?"), host=g.get("host", "?"),
              project=os.path.basename(root_) or root_),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if yes != QMessageBox.StandardButton.Yes:
            return
        alias = job.get("alias") or "base"
        self._wb_remote_ok.add((root_, alias))
        body = {"root": root_, "what": job.get("what")}
        if job.get("whiteboardId"):
            body["whiteboardId"] = job["whiteboardId"]
        self._code_wb_start(body, lambda: None)

    def _wb_show_reads(self):
        root_, tr = getattr(self, "_wb_last_trace", ("", []))
        dlg = QDialog(self)
        dlg.setWindowTitle(t("code.wb_reads_title"))
        dlg.resize(760, 480)
        dlg.setStyleSheet("QDialog { background:%s; } QLabel { color:#e6edf7; font-size:12px; }"
                          "QListWidget { background:%s; color:#e6edf7; border:1px solid %s; border-radius:8px;"
                          " font-family:monospace; font-size:12px; padding:4px; }"
                          % (self._WB_BG, self._WB_SEG, self._WB_LINE))
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(t("code.wb_reads_head", n=len(tr), root=root_)))
        lst = QListWidget()
        for x in tr:
            pth = str(x.get("path") or "")
            if root_ and pth.startswith(root_):
                pth = pth[len(root_):].lstrip("/\\") or "."
            it = QListWidgetItem(("⛔  " if x.get("refused") else "     ")
                                 + f"{x.get('tool', '?'):<16} {pth}"
                                 + (("   " + t("code.wb_reads_outside")) if x.get("refused") else ""))
            if x.get("refused"):
                it.setForeground(QColor("#f0a8a8"))
            lst.addItem(it)
        v.addWidget(lst, 1)
        dlg.exec()

    def _code_wb_stop(self):
        for jid in getattr(self, "_wb_running", []):
            try:
                requests.post(f"{self._wb_url()}/wb/api/draw/{jid}/cancel", timeout=5)
            except Exception:
                pass
        self._code_wb_poll()

    def _wb_job_to_report(self, jobs):
        shown = getattr(self, "_wb_shown_id", "") or ""
        if shown:
            for j in jobs:
                if j.get("whiteboardId") == shown:
                    return j
        if jobs and jobs[0].get("state") != "done":
            return jobs[0]
        return None

    def _code_wb_poll(self):
        if getattr(self, "_wb_poll_live", False):
            return
        root = (self._code_project or "").rstrip("/\\")
        url = f"{self._wb_url()}/wb/api/draw"
        self._wb_poll_live = True

        def work():
            try:
                return root, requests.get(url, params={"root": root},
                                          timeout=3).json().get("jobs", [])
            except Exception:
                return None

        self._bg(work, self._code_wb_jobs_paint)

    def _code_wb_jobs_paint(self, res):
        self._wb_poll_live = False
        if not res:
            return
        root, jobs = res
        running = [j for j in jobs if j.get("state") == "running"]
        self._wb_running = [j["id"] for j in running]
        self.code_wb_stop.setVisible(bool(running))
        if running:
            j = running[0]
            st = j.get("stage") or {}
            stage = t(self._WB_STAGES[st["key"]], n=st.get("n", 0), of=st.get("of", 0)) \
                if st.get("key") in self._WB_STAGES else ""
            self.code_wb_status.setText(t("code.wb_drawing",
                                          what=t(self._wb_phrase(j["what"])),
                                          model=j.get("model") or j.get("alias") or "",
                                          secs=int(time.time() - (j.get("started") or time.time())))
                                        + (f"  ·  {stage}" if stage else ""))
        elif self._wb_pending:
            self.code_wb_status.setText(t("code.wb_pick_model"))
        elif self._wb_job_to_report(jobs):
            j = self._wb_job_to_report(jobs)
            err = j.get("error") or ""
            if err == "remote_model" and j["id"] not in self._wb_asked:
                self._wb_asked.add(j["id"])
                QTimer.singleShot(0, lambda j=j: self._wb_ask_remote(j))
            if j.get("state") == "done":
                u = j.get("usage") or {}
                if j.get("modelFree"):
                    self.code_wb_status.setText(t("code.wb_followed_free"))
                    return
                self.code_wb_status.setText(t("code.wb_done", what=t(self._wb_phrase(j["what"])))
                                            + (("  ·  " + t("code.wb_cost", calls=u["calls"],
                                                            tin=f"{u['prompt']:,}", tout=f"{u['completion']:,}",
                                                            mins=round(((j.get("ended") or 0) - (j.get("started") or 0)) / 60, 1)))
                                               if u.get("calls") else ""))
            elif j.get("state") == "cancelled":
                self.code_wb_status.setText(t("code.wb_cancelled"))
            elif err == "remote_model":
                self.code_wb_status.setText(t("code.wb_remote_refused",
                                              model=(j.get("gate") or {}).get("model", "")))
            elif err in self._WB_REFUSALS:
                self.code_wb_status.setText(t(self._WB_REFUSALS[err]))
            else:
                self.code_wb_status.setText(t("code.wb_failed", error=err[:160]))
        else:
            self.code_wb_status.setText("")
        wb = self.whiteboard
        for j in jobs:
            wid = j.get("whiteboardId")
            if wid and j["id"] in self._wb_to_follow and wb is not None:
                self._wb_to_follow.discard(j["id"])
                if wid != wb.current:
                    wb.show_whiteboard(wid, root)
        _last = running[0] if running else self._wb_job_to_report(jobs)
        _tr = (_last or {}).get("trace") or []
        self._wb_last_trace = (_last or {}).get("root", ""), _tr
        if _tr:
            _out = sum(1 for x in _tr if x.get("refused"))
            self.code_wb_reads.setText(t("code.wb_reads", n=len(_tr))
                                       + (("  ·  " + t("code.wb_reads_refused", n=_out)) if _out else ""))
            self.code_wb_reads.setProperty("alarm", bool(_out))
            self.code_wb_reads.style().unpolish(self.code_wb_reads)
            self.code_wb_reads.style().polish(self.code_wb_reads)
        self.code_wb_reads.setVisible(bool(_tr))
        self._wb_status_row.setVisible(bool(self.code_wb_status.text()) or bool(running))
        on_page = (getattr(self, "_code_view", None) == 7
                   and getattr(self, "_code_main", None) is not None
                   and self._code_main.currentIndex() == 3)
        if not running and not on_page:
            self._wb_timer.stop()

    def _code_whiteboard(self):
        if not self._code_project:
            self._code_say(t("code.no_project"), bad=True)
            return
        self._code_activity(7, force=True)
        self._code_wb_draw("changes")

    def _on_wb_open(self, wid, tab_id):
        if self._may_take_view(tab_id):
            self._wb_show(wid)

    def _wb_show(self, wid=""):
        wb = getattr(self, "whiteboard", None)
        if wb is None:
            return
        self._on_switch_to_chat()
        if getattr(self, "_chat_stack", None) is not None and \
                self._chat_stack.currentIndex() != getattr(self, "_code_page_idx", -1):
            self._toggle_code_view()
        self._wb_root_shown = (self._code_project or "").rstrip("/\\")
        wb.loaded = True
        self._code_activity(7, force=True)
        if wid:
            wb.mcp_url = self._wb_url()
            wb.show_whiteboard(wid, self._wb_root_shown)

    def _on_wb_host(self, action, q):
        if action == "copy":
            QApplication.clipboard().setText(q.get("text", ""))
        elif action == "external":
            url = q.get("url", "")
            if url.startswith(("http://", "https://", "mailto:")):
                from PyQt6.QtGui import QDesktopServices
                from PyQt6.QtCore import QUrl as _QUrl
                QDesktopServices.openUrl(_QUrl(url))
        elif action == "open":
            self._wb_open_code(q)
        elif action == "update":
            self._code_wb_update(q.get("id", ""), q.get("root", ""), q.get("current", ""),
                                 auto=q.get("auto") == "1")
        elif action == "draw":
            if q.get("what") in self._WB_WHATS or q.get("what") == "incoming":
                self._code_wb_draw(q["what"], ref=q.get("ref") or None,
                                   since=q.get("since", ""), until=q.get("until", ""))
        elif action == "redraw":
            self._code_wb_redraw(q.get("id", ""))
        elif action == "delete":
            self._code_wb_delete(q.get("id", ""))

    def _wb_open_code(self, q):
        wid, file = q.get("whiteboard", ""), q.get("file", "")
        side = q.get("side") or "head"
        params = {"file": file, "side": side}
        if q.get("repositoryId") and q.get("commit") and q.get("commit") != "worktree":
            params.update({"repositoryId": q["repositoryId"], "head": q["commit"]})
            params["side"] = "head"
        try:
            r = requests.get(f"{self._wb_url()}/wb/api/{wid}/file", params=params, timeout=15).json()
        except Exception as e:
            self.add_message(t("msg.error", e=str(e)), "error")
            return
        if r.get("error"):
            self.add_message(t("msg.error", e=r["error"]), "error")
            return
        root, commit, text = r.get("root", ""), r.get("commit", ""), r.get("text", "")
        disk = os.path.join(root, file)
        same = False
        _isfile = False
        try:
            _raw, _m = self._proj_read(disk)
            _isfile = True
            same = commit == "worktree" or _raw.decode("utf-8", "replace") == text
        except Exception:
            same = False
        if same and _isfile:
            key = disk
        else:
            key = f"{disk}@{commit[:8]}"
            if key not in self._code_bufs:
                self._code_stash_current()
                self._code_bufs[key] = {"text": text, "dirty": False, "pos": 0,
                                        "scroll": 0, "mtime": None, "readonly": True}
                self._code_bufs_order.append(key)
        self._on_switch_to_chat()
        if getattr(self, "_chat_stack", None) is not None and \
                self._chat_stack.currentIndex() != getattr(self, "_code_page_idx", -1):
            self._toggle_code_view()
        if key == disk:
            self._code_open_in_tab(disk)
        else:
            self._code_stash_current()
            self._code_show_buf(key)
        try:
            a, b = int(q.get("line") or 1), int(q.get("end") or q.get("line") or 1)
        except ValueError:
            a = b = 1
        self._code_goto_line(a)
        doc = self.code_editor.document()
        ba, bb = doc.findBlockByNumber(max(0, a - 1)), doc.findBlockByNumber(max(0, b - 1))
        if ba.isValid() and bb.isValid():
            cur = self.code_editor.textCursor()
            cur.setPosition(ba.position())
            cur.setPosition(bb.position() + max(0, bb.length() - 1), cur.MoveMode.KeepAnchor)
            self.code_editor.setTextCursor(cur)

    def _on_switch_to_chat(self, tab_id=""):
        if tab_id:
            if tab_id != self._active_tab_id:
                return
            if getattr(self, "_chat_stack", None) is not None:
                self._chat_stack.setCurrentIndex(0)
                self._sync_input_placeholder()
                self._render_chat_tabs()
            return
        if getattr(self, "_chat_stack", None) is not None:
            self._chat_stack.setCurrentIndex(0)
            self._sync_input_placeholder()
            self._render_chat_tabs()
        try:
            self.tabs.setCurrentIndex(0)
        except Exception:
            pass

    def _on_show_alert(self, title, message):
        _play_alert_sound_global()
        if self.tray_icon and self.tray_icon.isVisible():
            self.tray_icon.showMessage(title, message, QSystemTrayIcon.MessageIcon.Warning, 15000)
        if getattr(self, "_hands_tab", None) is not None:
            self._hands_deferred_alerts.append((title, message))
            return
        dlg = QMessageBox(self)
        dlg.setWindowTitle(f"🔔 {title}")
        dlg.setText(message)
        dlg.setIcon(QMessageBox.Icon.Warning)
        dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
        dlg.exec()

    _hands_tab = None
    _hands_turn_id = ""
    _hands_hid_window = False
    _hands_deferred_alerts = []

    def _hands_hud(self):
        hud = getattr(self, "_hud", None)
        if hud is None:
            from hands_hud import HoplitaHud
            hud = HoplitaHud(self._asset_path("hoplita.png", "hoplita-tray.png"), t=t)
            hud.stop_clicked.connect(self._hands_stop)
            hud.pause_requested.connect(self._hands_pause)
            hud.back_requested.connect(self._hands_back)
            self._hud = hud
        return hud

    def _on_hands_event(self, tab, ev):
        et = ev.get("event")
        if et == "hands_consent":
            self._hands_consent(tab, ev)
            return
        if et == "hands_begin":
            self._hands_tab = tab
            self._hands_turn_id = ev.get("turn_id", "") or ""
            self._hands_deferred_alerts = []
            self._hands_stopping = ""
            self._hands_hid_window = self.isVisible()
            if self._hands_hid_window:
                self.hide()
            if sys.platform == "darwin":
                import mac_dialogs
                mac_dialogs.deactivate_app()
            hud = self._hands_hud()
            hud.set_state("working")
            hud.set_action("")
            hud.show_in_corner()
            if self.tray_icon:
                self.tray_icon.setToolTip(t("hands.tray_tip"))
        elif et == "hands_action":
            hud = getattr(self, "_hud", None)
            if hud is not None:
                hud.set_action(t("hands.hud_action",
                                 tool=str(ev.get("tool", "")).replace("hands_", ""),
                                 target=ev.get("target") or ""))
                if not hud.paused:
                    hud.set_state("error" if ev.get("ok") is False else "working")
        elif et == "hands_end":
            self._hands_finish()

    def _hands_finish(self):
        hud = getattr(self, "_hud", None)
        if hud is not None:
            hud.hide()
        if self._hands_hid_window:
            self.show()
            self.raise_()
            self.activateWindow()
        self._hands_hid_window = False
        self._hands_tab = None
        self._hands_turn_id = ""
        if self.tray_icon:
            self.tray_icon.setToolTip("HOPLITA Chat Assistant")
        pending, self._hands_deferred_alerts = self._hands_deferred_alerts, []
        for title, message in pending:
            self._on_show_alert(title, message)

    def _hands_post(self, action):
        turn_id = self._hands_turn_id

        def _go():
            try:
                requests.post(f"{self._tab_url()}/agent/hands",
                              json={"turn_id": turn_id, "action": action}, timeout=5)
            except Exception:
                pass
        threading.Thread(target=_go, daemon=True).start()

    def _hands_stop(self):
        tab = self._hands_tab
        if getattr(self, "_hands_stopping", "") == self._hands_turn_id and tab is not None:
            return
        self._hands_stopping = self._hands_turn_id
        self._hands_post("stop")
        ctx = self._chat_ctxs.get(tab) if tab else None
        if ctx is not None:
            try:
                self._cancel_pending_ack(tab)
            except Exception:
                pass
            ctx.stop_requested.set()
        turn_id = self._hands_turn_id
        if turn_id:
            def _go():
                try:
                    requests.post(f"{self._tab_url()}/agent/stop",
                                  json={"turn_id": turn_id}, timeout=5)
                except Exception:
                    pass
            threading.Thread(target=_go, daemon=True).start()
        hud = getattr(self, "_hud", None)
        if hud is not None:
            hud.set_state("paused")
            hud.set_action(t("hands.stopped"))
        self.add_message(t("hands.stopped"), "system")

    def _hands_pause(self, pause):
        self._hands_post("pause" if pause else "resume")
        hud = getattr(self, "_hud", None)
        if hud is not None:
            hud.set_state("paused" if pause else "working")
            hud.set_action(t("hands.paused") if pause else "")

    def _hands_back(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self._hands_hid_window = False

    def _hands_consent(self, tab, ev):
        ack_id = ev.get("ack_id", "")
        box = QMessageBox(self)
        box.setWindowTitle(t("hands.consent_title"))
        box.setText(t("hands.consent_text", model=ev.get("model", ""),
                      host=ev.get("host", "")))
        box.setIcon(QMessageBox.Icon.Question)
        yes = box.addButton(t("hands.consent_yes"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(t("hands.consent_no"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(yes)
        box.exec()
        decision = "run" if box.clickedButton() is yes else "cancel"
        self._hands_answer_consent(ack_id, decision)

    def _on_config_event(self, tab, ev):
        et = ev.get("event")
        if et == "config_changed":
            self.reload_config(push_to_mcp=False)
            return
        if et == "share_consent":
            self._on_share_consent(ev)
            return
        if et != "config_consent":
            return
        body = (t("selfcfg.consent_head") + "\n\n"
                + config_change_lines(ev.get("changes") or []))
        if ev.get("runs_program"):
            body += "\n\n" + t("selfcfg.consent_program")
        body += "\n\n" + t("selfcfg.consent_ask")
        box = QMessageBox(self)
        box.setWindowTitle(t("selfcfg.consent_title"))
        box.setText(body)
        box.setIcon(QMessageBox.Icon.Question)
        yes = box.addButton(t("selfcfg.consent_yes"), QMessageBox.ButtonRole.AcceptRole)
        no = box.addButton(t("selfcfg.consent_no"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        self._hands_answer_consent(ev.get("ack_id", ""),
                                   "run" if box.clickedButton() is yes else "cancel")

    def _on_share_consent(self, ev):
        args = ev.get("args") or {}
        lines = "\n".join(f"  {k}: {str(v)[:200]}" for k, v in args.items())
        body = (t("fed.consent_head") + "\n\n" + str(ev.get("tool", ""))
                + ("\n" + lines if lines else "") + "\n\n" + t("fed.consent_ask"))
        box = QMessageBox(self)
        box.setWindowTitle(t("fed.consent_title"))
        box.setText(body)
        box.setIcon(QMessageBox.Icon.Question)
        yes = box.addButton(t("fed.consent_yes"), QMessageBox.ButtonRole.AcceptRole)
        no = box.addButton(t("fed.consent_no"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        self._hands_answer_consent(ev.get("ack_id", ""),
                                   "run" if box.clickedButton() is yes else "cancel")

    def _hands_answer_consent(self, ack_id, decision):
        def _go():
            try:
                requests.post(f"{self._tab_url()}/agent/plan_ack",
                              json={"ack_id": ack_id, "decision": decision}, timeout=5)
            except Exception:
                pass
        threading.Thread(target=_go, daemon=True).start()

    def thread_msg(self, text, sender="system", keep=False):
        _ktab = getattr(self._tls, "council_tab", None)
        if _ktab and sender != "assistant":
            self.signals.council_event.emit(
                _ktab, {"type": "step", "text": str(text), "kind": sender})
            return
        tid = getattr(self._tls, "tab_id", None) or ""
        rid = 0 if keep else getattr(self._tls, "tu_rid", 0)
        if sender == "thought" and not rid:
            return
        if rid and sender in ("system", "error", "", "thought"):
            rec = self._tu_store().get(rid)
            if rec is not None:
                kind = (f"thought/{len(str(text))}" if sender == "thought"
                        else sender or "system")
                rec["lines"].append((datetime.now().strftime("%H:%M:%S"),
                                     kind, str(text)))
                if sender == "error":
                    rec["errors"] = int(rec.get("errors", 0)) + 1
                self.signals.message_tab.emit(str(rid), "tool_usage", tid)
                return
        self.signals.message_tab.emit(text, sender, tid)




    def load_config(self):
        config = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        import hoplita_paths
        config_dir = hoplita_paths.hoplita_base()
        os.makedirs(config_dir, exist_ok=True)
        config_file = os.path.join(config_dir, "config.ini")
        if not os.path.exists(config_file):
            self._create_default_config(config_file)
        import hoplita_llmcfg
        config, problem = hoplita_llmcfg.read_config(config_file, restore=True)
        if problem:
            print(f"[CONFIG] ⚠ {config_file}: {problem}", file=sys.stderr)
            try:
                QMessageBox.warning(None, t("cfg.load_problem_title"),
                                    t("cfg.load_problem_body", path=config_file, why=problem))
            except Exception:
                pass
        return config

    def _get_config_path(self):
        import hoplita_paths
        return hoplita_paths.config_path()

    def _create_default_config(self, config_file):
        default = """[APP]
language = en

[API]
base_url = http://127.0.0.1:8080/v1
api_key =
model = your-model

[RAG]
chunk_size = 25
chunk_overlap = 5
search_k = 5
max_chunk_chars = 1600
min_similarity_score = 0.18

[PERPLEXITY]
api_key =
model = sonar-pro
url = https://api.perplexity.ai

[CRAWL4AI]
url = http://localhost:8000
max_pages = 10
crawl_strategy = bfs
export_format = markdown
timeout = 60

[CHAT]
timeout = 120
max_tokens = 8000

[BROWSER]
base_url =
api_key =
model =
headless = true
viewport_width = 1366
viewport_height = 900
locale =
use_vision = auto
use_thinking =
wait_between_actions = 1.2
wait_network_idle = 3.0
max_steps_default = 30
max_actions_per_step = 3
default_skill_category = skills

[MCP]
url = http://127.0.0.1:9077
enabled = true
"""
        os.makedirs(os.path.dirname(config_file), exist_ok=True)
        with open(config_file, "w", encoding="utf-8") as f:
            f.write(default)


    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            print(t("init.tray_unavailable"), file=sys.stderr)
            self.tray_icon = None
            return
        self.tray_icon = QSystemTrayIcon(self)
        icon_path = self._get_icon_path()
        icon_loaded = False
        if icon_path and os.path.exists(icon_path):
            ico = QIcon(icon_path)
            if not ico.isNull():
                self.tray_icon.setIcon(ico)
                icon_loaded = True
        if not icon_loaded:
            pix = QPixmap(64, 64)
            pix.fill(QColor(0, 0, 0, 0))
            painter = QPainter(pix)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QColor("#10b981"))
            painter.setPen(QColor("#10b981"))
            painter.drawRoundedRect(4, 4, 56, 56, 12, 12)
            painter.setPen(QColor("#ffffff"))
            font = QFont("Arial", 28, QFont.Weight.Bold)
            painter.setFont(font)
            painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "H")
            painter.end()
            self.tray_icon.setIcon(QIcon(pix))

        tray_menu = QMenu()
        show_action = QAction(t("tray.show_hide"), self)
        show_action.triggered.connect(self._toggle_visibility)
        tray_menu.addAction(show_action)
        tray_menu.addSeparator()

        tray_menu.addSeparator()

        for label, slot in [
            (t("tray.stats_rag"), self.show_rag_stats),
            (t("tray.clear_rag"), self.clear_rag_index),
            (t("tray.reset_chunks"), self.reset_used_chunks),
            (t("tray.view_fixed"), self.view_fixed_contexts),
            (t("tray.clear_fixed"), self.clear_all_fixed_context),
            (t("tray.clear_chat"), self.clear_chat),
            (t("tray.reload_config"), self.reload_config),
        ]:
            a = QAction(label, self)
            a.triggered.connect(slot)
            tray_menu.addAction(a)

        tray_menu.addSeparator()
        quit_action = QAction(t("tray.quit"), self)
        quit_action.triggered.connect(self._on_quit)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.setToolTip("HOPLITA Chat Assistant")
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()
        self.tray_icon.setVisible(True)
        if IS_WINDOWS and self.tray_icon.isSystemTrayAvailable():
            QTimer.singleShot(500, lambda: self.tray_icon.showMessage(
                "HOPLITA",
                t("tray.bg_active"),
                QSystemTrayIcon.MessageIcon.Information, 5000))
        print(f"[TRAY] ✓ Icon created and visible (icon_loaded={icon_loaded}, "
              f"isVisible={self.tray_icon.isVisible()})", file=sys.stderr)

    def _logo_pixmap(self, height=46):
        p = self._asset_path("hoplita.png", "hoplita-tray.png")
        if not p:
            print("[LOGO] hoplita.png/.ico not found next to the script nor in "
                  "the data dir — header/tray will have no logo", file=sys.stderr)
            return None
        from PyQt6.QtGui import QPixmap
        pix = QPixmap(p)
        if pix.isNull():
            print(f"[LOGO] Qt could not load {p}", file=sys.stderr)
            return None
        return pix.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)

    def _asset_path(self, *names):
        bases = [os.path.dirname(os.path.abspath(__file__))]
        mei = getattr(sys, "_MEIPASS", None)
        if mei:
            bases.insert(0, mei)
        try:
            import hoplita_paths
            bases.append(hoplita_paths.hoplita_base())
        except Exception:
            bases.append("/opt/hoplita")
        for base in bases:
            for name in names:
                p = os.path.join(base, name)
                if os.path.exists(p):
                    return p
        return None

    def _get_icon_path(self):
        return self._asset_path("hoplita-tray.png", "hoplita.ico", "hoplita.png")

    def _toggle_visibility(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_visibility()

    def _on_quit(self):
        self._really_quit = True
        if hasattr(self, "cron_scheduler"):
            self.cron_scheduler.stop()
            self.cron_scheduler.wait(3000)
        if getattr(self, "terminal", None) is not None:
            try:
                self.terminal.shutdown()
            except Exception:
                pass
        try:
            import threading as _t
            _res = {}
            th = _t.Thread(target=lambda: _res.__setitem__(
                "r", self._mcp_call_json("fed_sync_push", {}, timeout=10)),
                daemon=True)
            th.start(); th.join(6)
            r = _res.get("r")
            if isinstance(r, dict) and r.get("skipped"):
                pass
            elif isinstance(r, dict) and r.get("error"):
                self._mark_push_failed(self._fmt_sync_error(r))
            elif isinstance(r, dict) and r.get("ok"):
                self._clear_push_mark()
        except Exception:
            pass
        if self.config.getboolean("APP", "quit_stops_mcp", fallback=True):
            try:
                requests.post(f"{self._mcp_url}/shutdown", timeout=4)
            except Exception:
                pass
        if getattr(self, "tray_icon", None) is not None:
            try:
                self.tray_icon.hide()
            except Exception:
                pass
        QApplication.quit()

    def closeEvent(self, event):
        if getattr(self, "_really_quit", False):
            event.accept()
            return
        if self.tray_icon and self.tray_icon.isVisible():
            self.hide()
            event.ignore()
        else:
            try:
                self._extract_pending(reason="close")
            except Exception:
                pass
            event.accept()


    def _setup_ui(self):
        central = _GlowFrame(self._GLOW_MARGIN, self._CORNER_RADIUS, self._ACCENT)
        central.setObjectName("root_glow")
        central.setStyleSheet("#root_glow { background: transparent; }")
        self.setCentralWidget(central)
        from PyQt6.QtWidgets import QLayout
        self.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._outer_layout = QVBoxLayout(central)
        m = self._GLOW_MARGIN
        self._outer_layout.setContentsMargins(m, m, m, m)
        self._outer_layout.setSpacing(0)
        inner = QWidget()
        inner.setObjectName("root_frame")
        self._inner_frame = inner
        self._outer_layout.addWidget(inner)
        main_layout = QVBoxLayout(inner)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.title_bar = CustomTitleBar(self, t("app.title") + "  " + HOPLITA_LABEL)
        main_layout.addWidget(self.title_bar)

        self.tabs = _ShrinkTabs()
        self.tabs.setUsesScrollButtons(True)
        self.tabs.tabBar().setExpanding(False)
        _brand = QWidget()
        _bl = QHBoxLayout(_brand)
        _bl.setContentsMargins(12, 0, 14, 0)
        _bl.setSpacing(10)
        _bl.addStretch()
        _wm = MetalLabel("HOPLITA", 17)
        _bl.addWidget(_wm)
        _sep = QLabel("▏")
        _sep.setStyleSheet("QLabel { color:#3a4552; }")
        _bl.addWidget(_sep)
        _gen = MetalLabel(HOPLITA_LABEL, 11, dim=0.12)
        _gen.setToolTip(t("app.build_tip"))
        _gen.setCursor(Qt.CursorShape.PointingHandCursor)
        _gen.mousePressEvent = lambda _e: self._show_build_panel()
        _bl.addWidget(_gen)
        _logo = self._logo_pixmap(36)
        if _logo is not None:
            _lb = QLabel()
            _lb.setPixmap(_logo)
            _lb.setToolTip("HOPLITA")
            _bl.addWidget(_lb)
        self.tabs.setCornerWidget(_brand, Qt.Corner.TopRightCorner)
        main_layout.addWidget(self.tabs)

        chat_widget = QWidget()
        chat_layout = QVBoxLayout(chat_widget)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(0)
        self._setup_header(chat_layout)
        self.dashboard = None
        self._canvases = {}
        self._canvas_full = False
        try:
            from dashboard_widget import DashboardWidget
            self.dashboard = DashboardWidget()
            self.dashboard.title_label.setText(t("tabs.dashboard"))
            for _label, _tip, _slot in (
                    (t("canvas.save_btn"), t("canvas.save_tip"), self._save_current_canvas),
                    (t("canvas.open_btn"), t("canvas.open_tip"), self._open_canvases_dialog),
                    (t("canvas.export_btn"), t("canvas.export_tip"), self._export_canvas)):
                self.dashboard.add_action(_label, _tip, _slot)
            self._canvas_full_btn = self.dashboard.add_action(
                "⤢", t("canvas.full_tip"), self._canvas_full_toggle)
            self.dashboard.setVisible(False)
        except Exception as e:
            import traceback
            self._dashboard_err = (e, traceback.format_exc())
            print(t("init.dashboard_fail", e=e), file=sys.stderr)

        self._setup_chat_area(chat_layout)
        self._setup_input_area(chat_layout)
        _i = self.tabs.addTab(chat_widget, t("tabs.chat")); self.tabs.setTabIcon(_i, self._svg_icon("tab_chat", "#94a3b8", 18))

        todo_widget = QWidget()
        todo_layout = QVBoxLayout(todo_widget)
        todo_layout.setContentsMargins(0, 0, 0, 0)
        self._setup_todo_page(todo_layout)
        _i = self.tabs.addTab(todo_widget, t("tabs.todo")); self.tabs.setTabIcon(_i, self._svg_icon("tab_todo", "#94a3b8", 18))

        notes_widget = QWidget()
        notes_layout = QVBoxLayout(notes_widget)
        notes_layout.setContentsMargins(0, 0, 0, 0)
        self._setup_notes_page(notes_layout)
        _i = self.tabs.addTab(notes_widget, t("tabs.notes")); self.tabs.setTabIcon(_i, self._svg_icon("tab_notes", "#94a3b8", 18))
        self._notes_tab_widget = notes_widget


        self._todo_widget_ref = todo_widget
        self._notes_widget_ref = notes_widget
        self._content_auto = QTimer(self)
        self._content_auto.setInterval(5000)
        self._content_auto.timeout.connect(self._auto_refresh_content_tabs)
        self._content_auto.start()
        self._fed_cache = {}
        self._fed_cache_timer = QTimer(self)
        self._fed_cache_timer.setInterval(300000)
        self._fed_cache_timer.timeout.connect(self._fed_cache_refresh)
        self._fed_cache_timer.start()
        QTimer.singleShot(1500, self._fed_cache_refresh)
        QTimer.singleShot(3500, lambda: self._do_sync(pull=True, manual=False))
        QTimer.singleShot(4200, self._check_notices)
        self._code_rail_timer = QTimer(self)
        self._code_rail_timer.setInterval(90000)
        self._code_rail_timer.timeout.connect(self._code_rails_tick)
        self._code_rail_timer.start()
        QTimer.singleShot(6000, self._code_rails_tick)
        QTimer.singleShot(1800, self._reopen_pinned)
        QTimer.singleShot(6000, self._reopen_pinned)
        QTimer.singleShot(300, self.update_rag_status)
        QTimer.singleShot(4800, self._warn_last_push_error)
        QTimer.singleShot(1200, self._sync_firewall)
        _every = self.config.getint("MCP", "sync_every_min", fallback=15)
        if _every > 0:
            self._sync_timer = QTimer(self)
            self._sync_timer.setInterval(_every * 60000)
            self._sync_timer.timeout.connect(self._periodic_sync)
            self._sync_timer.start()
        self._notices_timer = QTimer(self)
        self._notices_timer.setInterval(300000)
        self._notices_timer.timeout.connect(self._check_notices)
        self._notices_timer.start()

        cron_widget = QWidget()
        cron_layout = QVBoxLayout(cron_widget)
        cron_layout.setContentsMargins(0, 0, 0, 0)
        self._setup_cron_page(cron_layout)
        _i = self.tabs.addTab(cron_widget, t("tabs.cron")); self.tabs.setTabIcon(_i, self._svg_icon("tab_cron", "#94a3b8", 18))

        tools_widget = QWidget()
        tools_layout = QVBoxLayout(tools_widget)
        tools_layout.setContentsMargins(0, 0, 0, 0)
        self._setup_tools_page(tools_layout)
        _i = self.tabs.addTab(tools_widget, t("tabs.tools")); self.tabs.setTabIcon(_i, self._svg_icon("tab_tools", "#94a3b8", 18))

        self.terminal = None
        if not IS_WINDOWS:
            try:
                from terminal_widget import TerminalWidget
                self.terminal = TerminalWidget()
                _i = self.tabs.addTab(self.terminal, t("tabs.terminal")); self.tabs.setTabIcon(_i, self._svg_icon("tab_terminal", "#94a3b8", 18))
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                err_text = (
                    t("init.terminal_unavailable", e=e)
                    + f"\n\nTraceback:\n{tb}"
                )
                err_label = QLabel(err_text)
                err_label.setWordWrap(True)
                err_label.setStyleSheet(
                    "QLabel { color: #cd3131; background: #1e1e1e; padding: 20px; "
                    "font-family: monospace; font-size: 12px; }")
                err_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                self.tabs.addTab(err_label, t("tabs.terminal_err"))
                print(t("init.terminal_fail", e=e), file=sys.stderr)
                print(tb, file=sys.stderr)

        if self.dashboard is None and getattr(self, "_dashboard_err", None):
            _e, _tb = self._dashboard_err
            err_label = QLabel(
                t("init.dashboard_fail_label", e=_e) + f"\n\n{_tb}")
            err_label.setWordWrap(True)
            err_label.setStyleSheet(
                "QLabel { color: #cd3131; background: #1e1e1e; padding: 20px; "
                "font-family: monospace; font-size: 12px; }")
            err_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.tabs.addTab(err_label, t("tabs.dashboard_err"))

        try:
            self._setup_memory_tab()
        except Exception as e:
            print(t("init.memory_fail", e=e), file=sys.stderr)


        try:
            from federation_widget import FederationWidget
            self.federation_panel = FederationWidget(
                self._mcp_call,
                models_fn=lambda: list(self._model_pool().keys()))
            self.federation_panel.open_session_requested.connect(self._open_chat_session)
            self.federation_panel.config_changed.connect(
                lambda: self.reload_config(push_to_mcp=False))
            _i = self.tabs.addTab(self.federation_panel, t("tabs.federation"))
            self.tabs.setTabIcon(_i, self._svg_icon("tab_agents", "#94a3b8", 18))
        except Exception as e:
            self.federation_panel = None
            print(f"[init] Federation tab unavailable: {e}", file=sys.stderr)

    def _setup_shortcuts(self):
        def sc(seq, fn):
            s = QShortcut(QKeySequence(seq), self)
            s.activated.connect(fn)
            return s

        sc("Esc", self._shortcut_stop)
        sc("Ctrl+.", self._shortcut_stop)

        sc("Ctrl+=", lambda: self._chat_zoom(+1))
        sc("Ctrl++", lambda: self._chat_zoom(+1))
        sc("Ctrl+-", lambda: self._chat_zoom(-1))
        sc("Ctrl+0", self._chat_zoom_reset)

        sc("Ctrl+1", lambda: self.tabs.setCurrentIndex(0))
        if getattr(self, "agents_panel", None):
            sc("Ctrl+2", self._on_switch_to_agents)

        sc("Ctrl+L", self.clear_chat)

        def _on_code_view():
            _idx = getattr(self, "_code_page_idx", None)
            return (_idx is not None and getattr(self, "_chat_stack", None)
                    is not None and self._chat_stack.currentIndex() == _idx)

        def _quick_open():
            if _on_code_view():
                self._code_quick_open()

        def _global_search():
            if _on_code_view() and getattr(self, "_code_project", ""):
                self._code_activity(5)
                self.code_search_box.setFocus()
                self.code_search_box.selectAll()

        sc("Ctrl+P", _quick_open)
        sc("Ctrl+Shift+F", _global_search)
        sc("Ctrl+K", lambda: self.entry.setFocus())

    def _shortcut_stop(self):
        if self._task_running:
            self.request_stop()

    def _setup_memory_tab(self):
        mem_widget = QWidget()
        layout = QVBoxLayout(mem_widget)
        layout.setContentsMargins(8, 8, 8, 8)

        sub = QTabWidget()

        facts_w = QWidget()
        fl = QVBoxLayout(facts_w)
        fh = QHBoxLayout()
        fh.addWidget(QLabel(t("mem.facts_title")))
        fh.addStretch()
        refresh_facts_btn = QPushButton(t("btn.reload"))
        refresh_facts_btn.clicked.connect(self._load_memory_facts)
        fh.addWidget(refresh_facts_btn)
        fl.addLayout(fh)
        _hint = QLabel(t("mem.facts_hint_full"))
        _hint.setWordWrap(True)
        fl.addWidget(_hint)
        _ff = QHBoxLayout()
        _seg = QFrame()
        _seg.setObjectName("factsSeg")
        _seg.setStyleSheet(
            "#factsSeg { background:%s; border:1px solid %s; border-radius:10px; }"
            "#factsSeg QPushButton { background:transparent; color:#b6c2d6; border:none; border-radius:7px;"
            " padding:4px 13px; font-size:12px; }"
            "#factsSeg QPushButton:hover { background:#131d33; color:#e6edf7; }"
            "#factsSeg QPushButton:checked { background:%s; color:#e6edf7; }"
            % (self._WB_SEG, self._WB_LINE, self._WB_ON))
        _sl = QHBoxLayout(_seg)
        _sl.setContentsMargins(3, 3, 3, 3)
        _sl.setSpacing(2)
        self._facts_view = "live"
        self._facts_view_btns = {}
        for _v in ("live", "archive"):
            _b = QPushButton(t(f"mem.view_{_v}"))
            _b.setCheckable(True)
            _b.setChecked(_v == "live")
            _b.setCursor(Qt.CursorShape.PointingHandCursor)
            _b.clicked.connect(lambda _c=False, v=_v: self._set_facts_view(v))
            _sl.addWidget(_b)
            self._facts_view_btns[_v] = _b
        _ff.addWidget(_seg)
        self.facts_filter = QLineEdit()
        self.facts_filter.setPlaceholderText(t("mem.facts_filter_ph"))
        self.facts_filter.setClearButtonEnabled(True)
        self.facts_filter.textChanged.connect(self._filter_memory_facts)
        _ff.addWidget(self.facts_filter, 1)
        self.facts_sort = QComboBox()
        self.facts_sort.addItems([t("mem.sort_hits"), t("mem.sort_new"),
                                  t("mem.sort_old"), t("mem.sort_az")])
        _fm = QFont(); _fm.setPixelSize(13)
        self.facts_sort.setMinimumWidth(max(QFontMetrics(_fm).horizontalAdvance(
            self.facts_sort.itemText(i)) for i in range(self.facts_sort.count())) + 64)
        self.facts_sort.currentIndexChanged.connect(self._load_memory_facts)
        _ff.addWidget(self.facts_sort)
        fl.addLayout(_ff)
        self.facts_table = QListWidget()
        self.facts_table.itemDoubleClicked.connect(self._edit_fact_dialog)
        self.facts_table.setSelectionMode(
            QListWidget.SelectionMode.ExtendedSelection)
        self.facts_table.keyPressEvent = self._make_del_keyhandler(
            self.facts_table, self._delete_selected_fact)
        fl.addWidget(self.facts_table, 1)
        self.facts_count = QLabel("")
        self.facts_count.setStyleSheet("color:#e6edf7; font-size:12px;")
        fl.addWidget(self.facts_count)
        _fp = QHBoxLayout()
        self.facts_only_generic = QCheckBox(t("mem.only_generic"))
        self.facts_only_generic.setToolTip(t("mem.only_generic_tip"))
        self.facts_only_generic.stateChanged.connect(self._load_memory_facts)
        _fp.addWidget(self.facts_only_generic)
        _fp.addStretch()
        _danger = ("QPushButton { background:#3f1d1d; color:#fecaca; border:1px solid "
                   "#7f1d1d; border-radius:8px; padding:4px 12px; }")
        self.facts_purge_archive = QPushButton(t("mem.purge_archive"))
        self.facts_purge_archive.setToolTip(t("mem.purge_archive_tip"))
        self.facts_purge_archive.setStyleSheet(_danger)
        self.facts_purge_archive.clicked.connect(self._purge_fact_archive)
        self.facts_purge_archive.hide()
        _fp.addWidget(self.facts_purge_archive)
        _purge = QPushButton(t("mem.purge_all"))
        _purge.setToolTip(t("mem.purge_all_tip"))
        _purge.setStyleSheet(
            "QPushButton { background:#3f1d1d; color:#fecaca; border:1px solid "
            "#7f1d1d; border-radius:8px; padding:4px 12px; }")
        _purge.clicked.connect(self._purge_all_facts)
        self.facts_purge_all = _purge
        _fp.addWidget(_purge)
        fl.addLayout(_fp)
        facts_btns = QHBoxLayout()
        edit_fact_btn = QPushButton(t("btn.edit_selected"))
        edit_fact_btn.clicked.connect(lambda: self._edit_fact_dialog(
            self.facts_table.currentItem()))
        del_fact_btn = QPushButton(t("mem.del_fact"))
        del_fact_btn.setStyleSheet("color: #cd3131;")
        del_fact_btn.clicked.connect(self._delete_selected_fact)
        facts_btns.addWidget(edit_fact_btn)
        facts_btns.addWidget(del_fact_btn)
        facts_btns.addStretch()
        fl.addLayout(facts_btns)
        sub.addTab(facts_w, t("tabs.facts"))

        less_w = QWidget()
        ll = QVBoxLayout(less_w)
        lh = QHBoxLayout()
        lh.addWidget(QLabel(t("mem.lessons_title")))
        lh.addStretch()
        refresh_less_btn = QPushButton(t("btn.reload"))
        refresh_less_btn.clicked.connect(self._load_memory_lessons)
        lh.addWidget(refresh_less_btn)
        ll.addLayout(lh)
        ll.addWidget(QLabel(t("ui.select_lesson_hint")))
        self.lessons_table = QListWidget()
        self.lessons_table.setSelectionMode(
            QListWidget.SelectionMode.ExtendedSelection)
        self.lessons_table.itemDoubleClicked.connect(self._edit_lesson_dialog)
        self.lessons_table.keyPressEvent = self._make_del_keyhandler(
            self.lessons_table, self._delete_selected_lesson)
        ll.addWidget(self.lessons_table, 1)
        less_btns = QHBoxLayout()
        del_less_btn = QPushButton(t("mem.del_lesson"))
        del_less_btn.setStyleSheet("color: #cd3131;")
        del_less_btn.clicked.connect(self._delete_selected_lesson)
        less_btns.addWidget(del_less_btn)
        less_btns.addStretch()
        ll.addLayout(less_btns)
        sub.addTab(less_w, t("tabs.lessons"))

        exp_w = QWidget()
        el = QVBoxLayout(exp_w)
        eh = QHBoxLayout()
        eh.addWidget(QLabel(t("mem.exp_title")))
        eh.addStretch()
        refresh_exp_btn = QPushButton(t("btn.reload"))
        refresh_exp_btn.clicked.connect(self._load_memory_experience)
        eh.addWidget(refresh_exp_btn)
        el.addLayout(eh)
        _hint = QLabel(t("mem.exp_hint"))
        _hint.setWordWrap(True)
        el.addWidget(_hint)
        self.exp_table = QListWidget()
        self.exp_table.setSelectionMode(
            QListWidget.SelectionMode.ExtendedSelection)
        self.exp_table.itemDoubleClicked.connect(self._show_experience_dialog)
        self.exp_table.keyPressEvent = self._make_del_keyhandler(
            self.exp_table, self._delete_selected_experience)
        el.addWidget(self.exp_table, 1)
        _eb = QHBoxLayout()
        _del_exp = QPushButton(t("mem.del_exp"))
        _del_exp.setStyleSheet("color: #cd3131;")
        _del_exp.clicked.connect(self._delete_selected_experience)
        _eb.addWidget(_del_exp)
        _eb.addStretch()
        el.addLayout(_eb)
        sub.addTab(exp_w, t("tabs.experience"))

        log_w = QWidget()
        ll = QVBoxLayout(log_w)
        _lh = QHBoxLayout()
        self.log_search = QLineEdit()
        self.log_search.setPlaceholderText(t("mem.log_search_ph"))
        self.log_search.setClearButtonEnabled(True)
        self.log_search.returnPressed.connect(self._search_message_log)
        _lh.addWidget(self.log_search, 1)
        _lb = QPushButton(t("mem.log_search"))
        _lb.clicked.connect(self._search_message_log)
        _lh.addWidget(_lb)
        ll.addLayout(_lh)
        _lc = QHBoxLayout()
        self.log_count = QLabel("")
        self.log_count.setStyleSheet("QLabel { color:#e6edf7; font-size:12px; }")
        _lc.addWidget(self.log_count)
        _lc.addStretch()
        _fc = QPushButton(t("log.forget_conv"))
        _fc.setToolTip(t("log.forget_conv_tip"))
        _fc.clicked.connect(self._forget_conversation)
        _fa = QPushButton(t("log.forget_all"))
        _fa.setToolTip(t("log.forget_all_tip"))
        _fa.setStyleSheet("QPushButton { background:#3f1d1d; color:#fecaca; "
                          "border:1px solid #7f1d1d; border-radius:8px; "
                          "padding:3px 10px; }")
        _fa.clicked.connect(self._forget_all_log)
        _lc.addWidget(_fc)
        _lc.addWidget(_fa)
        ll.addLayout(_lc)
        self.log_results = QTextEdit()
        self.log_results.setReadOnly(True)
        ll.addWidget(self.log_results, 1)
        sub.addTab(log_w, t("tabs.conversations"))

        prop_w = QWidget()
        pl = QVBoxLayout(prop_w)
        _ph = QHBoxLayout()
        from pilot_light import PilotLine
        self.prop_count = PilotLine(lines=3)
        _ph.addWidget(self.prop_count, 1)
        _ph.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.prop_curate = QPushButton(t("prop.curate"))
        self.prop_curate.setToolTip(t("prop.curate_tip"))
        self.prop_curate.setStyleSheet(
            "QPushButton { background:#1e3a5f; color:#dbeafe; "
            "border:1px solid #3b82f6; border-radius:8px; padding:3px 12px; }"
            "QPushButton:disabled { background:#1a2330; color:#4b5563; "
            "border:1px solid #263041; }")
        self.prop_curate.clicked.connect(self._curate_memory)
        _ph.addWidget(self.prop_curate)
        _pr = QPushButton(t("btn.reload"))
        _pr.clicked.connect(self._load_proposals)
        _ph.addWidget(_pr)
        pl.addLayout(_ph)
        self.prop_list = QListWidget()
        self.prop_list.setWordWrap(True)
        self.prop_list.currentItemChanged.connect(self._prop_buttons_for)
        pl.addWidget(self.prop_list, 1)
        _pb = QHBoxLayout()
        _ac = self.prop_accept_btn = QPushButton(t("prop.accept"))
        _ac.setStyleSheet("QPushButton { background:#065f46; color:#d1fae5; "
                          "border:1px solid #10b981; border-radius:8px; "
                          "padding:4px 14px; font-weight:600; }")
        _ac.clicked.connect(lambda: self._decide_proposal(True))
        _no = self.prop_reject_btn = QPushButton(t("prop.reject"))
        _no.clicked.connect(lambda: self._decide_proposal(False))
        _all = QPushButton(t("prop.accept_all"))
        _all.setToolTip(t("prop.confirm_all", n="N"))
        _all.clicked.connect(self._decide_all_proposals)
        _pb.addWidget(_ac)
        _pb.addWidget(_no)
        _pb.addWidget(_all)
        _pb.addStretch()
        pl.addLayout(_pb)

        _al = QLabel(f"<b>{t('prop.auto_title')}</b>")
        _al.setStyleSheet("QLabel { color:#cfe0ff; font-size:13px; "
                          "padding-top:8px; }")
        pl.addWidget(_al)
        self.auto_hint = QLabel(t("prop.auto_hint"))
        self.auto_hint.setWordWrap(True)
        self.auto_hint.setStyleSheet("QLabel { color:#e6edf7; font-size:12px; }")
        pl.addWidget(self.auto_hint)
        self.auto_list = QListWidget()
        self.auto_list.setWordWrap(True)
        self.auto_list.setMaximumHeight(150)
        pl.addWidget(self.auto_list)
        _ub = QHBoxLayout()
        self.auto_undo = QPushButton(t("prop.undo"))
        self.auto_undo.setToolTip(t("prop.undo_tip"))
        self.auto_undo.setStyleSheet(
            "QPushButton { background:#3f2d1d; color:#fed7aa; border:1px solid "
            "#b45309; border-radius:8px; padding:4px 14px; }")
        self.auto_undo.clicked.connect(self._undo_auto_entry)
        _ub.addWidget(self.auto_undo)
        _ub.addStretch()
        pl.addLayout(_ub)
        sub.addTab(prop_w, t("tabs.proposals"))

        layout.addWidget(sub)
        self.mem_widget = mem_widget
        _i = self.tabs.addTab(mem_widget, t("tabs.memory")); self.tabs.setTabIcon(_i, self._svg_icon("tab_memory", "#94a3b8", 18))
        self._load_memory_facts()
        self._load_memory_lessons()
        self._load_memory_experience()

    def _make_del_keyhandler(self, widget, del_func):
        orig = QListWidget.keyPressEvent
        def handler(event):
            if event.key() == Qt.Key.Key_Delete:
                del_func()
            else:
                orig(widget, event)
        return handler

    def _delete_selected_fact(self):
        facts = [i.data(Qt.ItemDataRole.UserRole)
                 for i in self.facts_table.selectedItems()]
        facts = [f for f in facts if isinstance(f, dict) and f.get("id")]
        if not facts:
            return
        sample = "\n".join(f"  • {f.get('text','')[:70]}" for f in facts[:8])
        if len(facts) > 8:
            sample += "\n  " + t("mem.and_more", n=len(facts) - 8)
        confirm = QMessageBox.question(self, t("cred.confirm_delete_fact"),
            t("dlg.delete_facts_q", n=len(facts)) + f"\n\n{sample}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            requests.post(f"{self._mcp_url}/facts/purge", timeout=20,
                          json={"ids": [f["id"] for f in facts]})
        except Exception as e:
            QMessageBox.warning(self, t("dlg.error"), str(e))
        self._load_memory_facts()

    def _set_facts_view(self, view):
        self._facts_view = view
        for v, b in self._facts_view_btns.items():
            b.setChecked(v == view)
        self.facts_only_generic.setVisible(view == "live")
        self.facts_purge_archive.setVisible(view == "archive")
        self.facts_purge_all.setVisible(view == "live")
        self._load_memory_facts()

    def _purge_fact_archive(self):
        n = getattr(self, "_facts_archived", 0) or 0
        if not n:
            return
        confirm = QMessageBox.question(
            self, t("mem.purge_archive"),
            t("mem.purge_archive_q", n=n,
              size=self._human_bytes(getattr(self, "_facts_archived_bytes", 0))),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            r = requests.post(f"{self._mcp_url}/facts/purge", timeout=60,
                              json={"archived": True}).json()
            self.add_message(t("mem.archive_purged", n=r.get("deleted", 0)), "system")
        except Exception as e:
            QMessageBox.warning(self, t("dlg.error"), str(e))
        self._load_memory_facts()
        self._load_autolog()

    @staticmethod
    def _human_bytes(n):
        n = int(n or 0)
        if n < 1024:
            return f"{n} B"
        if n < 1024 * 1024:
            return f"{n / 1024:.1f} KB"
        return f"{n / 1024 / 1024:.1f} MB"

    def _purge_all_facts(self):
        n = self.facts_table.count()
        if not n:
            return
        txt, ok = QInputDialog.getText(self, t("mem.purge_all"),
                                       t("mem.purge_all_q", n=n))
        if not ok or (txt or "").strip().upper() != t("mem.purge_word"):
            self.add_message(t("mem.purge_cancelled"), "system")
            return
        try:
            r = requests.post(f"{self._mcp_url}/facts/purge", timeout=60,
                              json={"all": True,
                                    "confirm": t("mem.purge_word")}).json()
            self.add_message(t("mem.purged", n=r.get("deleted", 0)), "system")
        except Exception as e:
            QMessageBox.warning(self, t("dlg.error"), str(e))
        self._load_memory_facts()

    def _delete_selected_lesson(self):
        les = [i.data(Qt.ItemDataRole.UserRole)
               for i in self.lessons_table.selectedItems()]
        les = [l for l in les if isinstance(l, dict) and l.get("id")]
        if not les:
            return
        sample = "\n".join(f"  • {l.get('text','')[:70]}" for l in les[:8])
        if len(les) > 8:
            sample += "\n  " + t("mem.and_more", n=len(les) - 8)
        confirm = QMessageBox.question(self, t("dlg.delete_lesson"),
            t("dlg.delete_lessons_q", n=len(les)) + f"\n\n{sample}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return
        for l in les:
            try:
                requests.delete(f"{self._mcp_url}/lessons/{l['id']}", timeout=10)
            except Exception as e:
                QMessageBox.warning(self, t("dlg.error"), str(e))
                break
        self._load_memory_lessons()

    def _delete_selected_experience(self):
        _rows = getattr(self, "_exp_rows", []) or []
        exps = [_rows[self.exp_table.row(i)]
                for i in self.exp_table.selectedItems()
                if 0 <= self.exp_table.row(i) < len(_rows)]
        exps = [e for e in exps if isinstance(e, dict) and e.get("id")]
        if not exps:
            return
        sample = "\n".join(f"  • {(e.get('goal') or e.get('document',''))[:70]}"
                            for e in exps[:8])
        if len(exps) > 8:
            sample += "\n  " + t("mem.and_more", n=len(exps) - 8)
        confirm = QMessageBox.question(self, t("dlg.delete_exp"),
            t("dlg.delete_exps_q", n=len(exps)) + f"\n\n{sample}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return
        for e in exps:
            try:
                requests.delete(f"{self._mcp_url}/experience/{e['id']}", timeout=10)
            except Exception as ex:
                QMessageBox.warning(self, t("dlg.error"), str(ex))
                break
        self._load_memory_experience()

    def _on_tab_changed(self, index):
        self._maybe_refresh_memory(index)

    def _on_tab_clicked(self, index):
        self._maybe_refresh_memory(index)

    def _maybe_refresh_memory(self, index):
        try:
            if getattr(self, "mem_widget", None) is not None \
                    and self.tabs.widget(index) is self.mem_widget:
                self._load_memory_facts()
                self._load_memory_lessons()
        except Exception as e:
            print(f"[MEMORY] refresh on tab change failed: {e}", file=sys.stderr)

    def _load_proposals(self):
        if not hasattr(self, "prop_list"):
            return
        self.prop_list.clear()
        if self.prop_count.light.state == "off":
            self._watch_review()
        try:
            r = requests.get(f"{self._mcp_url}/memory/proposals", timeout=10).json()
        except Exception as e:
            self.prop_count.setText(t("mem.log_err", e=e)); return
        props = r.get("proposals") or []
        self.prop_count.setText(t("prop.count", n=len(props)))
        if not props:
            self.prop_list.addItem(t("prop.none"))
            return
        for p in props:
            _act = (p.get("action") or "").strip()
            _acc = ({"retire": lambda: t("prop.retire"),
                     "prune": lambda: t("prop.prune"),
                     "fade": lambda: t("prop.fade"),
                     "ask": lambda: t("prop.ask", days=(re.findall(r"\d+", p.get("why") or "") or ["?"])[0]),
                     "merge": lambda: t("prop.merge", new=p.get("new_text", ""),
                                        n=len(p.get("absorbed") or []) + 1),
                     "replace": lambda: t("prop.replace",
                                          new=p.get("new_text", ""))}
                    .get(_act, lambda: t("prop.unknown", action=_act)))()
            _quote = "\n".join(f"      «{q.get('text','')[:120]}»"
                               f"  ({q.get('ts','')[:10]})"
                               for q in (p.get("quotes") or [])[:2])
            _why = "" if _act == "ask" else f"   ↳ {p.get('why','')}\n"
            it = QListWidgetItem(
                f"{_acc}\n   {p.get('target_text') or p.get('target_id','')}\n"
                f"{_why}{_quote}")
            it.setData(Qt.ItemDataRole.UserRole, p)
            self.prop_list.addItem(it)

    def _load_autolog(self):
        if not hasattr(self, "auto_list"):
            return
        self.auto_list.clear()
        try:
            r = requests.get(f"{self._mcp_url}/memory/autolog",
                             timeout=10).json()
        except Exception as e:
            self.auto_hint.setText(t("mem.log_err", e=e)); return
        _last = (r.get("last_run") or "")[:16].replace("T", " ")
        self.auto_hint.setText(t("prop.auto_hint_run", when=_last) if _last
                               else t("prop.auto_hint"))
        rows = r.get("entries") or []
        if not rows:
            self.auto_list.addItem(t("prop.auto_none"))
            return
        for e in rows:
            _s = e.get("summary") or {}
            _when = (e.get("ts") or "")[:16].replace("T", " ")
            if e.get("kind") == "lessons_merged":
                _txt = t("prop.auto_merged", when=_when,
                         n=len(_s.get("replaced") or []),
                         merged=_s.get("merged", ""))
                _txt += "".join(f"\n      ← «{x}»"
                                for x in (_s.get("replaced") or [])[:4])
            elif str(e.get("kind", "")).startswith("facts_"):
                _a = e["kind"][6:]
                _key = {"merge": "prop.auto_fact_merge", "retire": "prop.auto_fact_retire",
                        "prune": "prop.auto_fact_prune",
                        "fade": "prop.auto_fact_fade"}.get(_a, "prop.auto_fact_rewrite")
                _txt = t(_key, when=_when, fact=_s.get("fact", ""),
                         new=_s.get("new_text", ""))
            else:
                _txt = t("prop.auto_retired", when=_when,
                         n=_s.get("count", 0))
                _txt += "".join(f"\n      ← «{x}»"
                                for x in (_s.get("texts") or [])[:4])
            if e.get("undo_gone"):
                _txt = t("prop.auto_undo_gone") + " " + _txt
            if e.get("undone"):
                _txt = t("prop.auto_undone") + " " + _txt
            it = QListWidgetItem(_txt)
            it.setData(Qt.ItemDataRole.UserRole, e)
            self.auto_list.addItem(it)

    def _undo_auto_entry(self):
        it = self.auto_list.currentItem()
        e = it.data(Qt.ItemDataRole.UserRole) if it else None
        if not isinstance(e, dict) or not e.get("id"):
            return
        if e.get("undone"):
            QMessageBox.information(self, t("tabs.proposals"),
                                    t("prop.already_undone"))
            return
        try:
            r = requests.post(
                f"{self._mcp_url}/memory/autolog/{e['id']}/undo",
                timeout=15).json()
        except Exception as ex:
            QMessageBox.warning(self, t("dlg.error"), str(ex)); return
        if r.get("error"):
            QMessageBox.warning(self, t("dlg.error"), str(r["error"]))
        self._load_autolog()
        self._load_memory_lessons()

    def _curate_memory(self):
        self.prop_curate.setEnabled(False)
        self.prop_count.working(t("prop.curating"))
        self._watch_review()

        def worker():
            try:
                r = requests.post(f"{self._mcp_url}/memory/review",
                                  json={"limit": -1}, timeout=1800).json()
            except Exception as e:
                r = {"error": str(e)}
            self.signals.curate_done.emit(r if isinstance(r, dict) else {})

        threading.Thread(target=worker, daemon=True, name="curate-mem").start()

    def _watch_review(self):
        tmr = getattr(self, "_review_timer", None)
        if tmr is None:
            tmr = self._review_timer = QTimer(self)
            tmr.setInterval(1000)
            tmr.timeout.connect(self._review_tick)
        if not tmr.isActive():
            tmr.start()
        self._review_tick()

    _REVIEW_STILL_S = {"classify": 330, "facts": 90, "lessons": 330, "duplicates": 330, "apply": 60}

    def _review_tick(self):
        if getattr(self, "_review_poll_live", False):
            return
        self._review_poll_live = True

        def work():
            try:
                return requests.get(f"{self._mcp_url}/memory/review/progress", timeout=2).json()
            except Exception:
                return None

        self._bg(work, self._review_paint)

    def _review_paint(self, p):
        self._review_poll_live = False
        if not isinstance(p, dict):
            return
        if not p.get("running"):
            if not self.prop_curate.isEnabled():
                return
            self._review_timer.stop()
            if self.prop_count.light.state != "off":
                self.prop_count.done()
                self._load_proposals()
                self._load_autolog()
            return
        self.prop_curate.setEnabled(False)
        mmss = lambda s: f"{int(s) // 60}:{int(s) % 60:02d}"
        ph = p.get("phase") or ""
        line = t("prop.phase_" + ph) if ph in self._REVIEW_STILL_S else t("prop.curating")
        if p.get("total"):
            line = t("prop.progress", what=line, done=p.get("done", 0) + 1,
                     total=p["total"], time=mmss(p.get("elapsed", 0)))
        else:
            line = t("prop.progress_one", what=line, time=mmss(p.get("elapsed", 0)))
        if p.get("current"):
            line += "\n" + t("prop.progress_on", text=p["current"])
        if p.get("still", 0) > (p.get("limit") or self._REVIEW_STILL_S.get(ph, 90)) + 30:
            self.prop_count.stalled(line + "\n" + t("prop.progress_still",
                                                     still=mmss(p["still"])))
        else:
            self.prop_count.working(line)

    def _on_curate_done(self, r):
        if getattr(self, "_review_timer", None) is not None:
            self._review_timer.stop()
        if hasattr(self, "prop_curate"):
            self.prop_curate.setEnabled(True)
        self.prop_count.done()
        self._load_proposals()
        self._load_autolog()
        if r.get("error"):
            _msg = t("mem.log_err", e=r["error"])
            self.prop_count.setText(_msg)
            if self.prop_list.count() == 1 and self.prop_list.item(0) \
                    .data(Qt.ItemDataRole.UserRole) is None:
                self.prop_list.item(0).setText(_msg)
            return
        _f = r.get("facts") or {}
        _d = r.get("duplicates") or {}
        _l = r.get("lessons") or {}
        _errs = [f"{_name}: {_h['error']}"
                 for _name, _h in ((t("mem.half_rules"), _l), (t("mem.half_facts"), _f),
                                   (t("mem.half_dups"), _d))
                 if isinstance(_h, dict) and _h.get("error")]
        if _errs:
            _msg = t("prop.half_err", errs=" · ".join(_errs)[:300])
        elif _f.get("errors"):
            _brk = (_f.get("llm_breaker") or {}).get("last_error", "")
            _msg = t("prop.fact_errors", failed=_f.get("errors", 0),
                     seen=_f.get("asked", 0),
                     why=(_f.get("error_last") or _brk or "?")[:200])
        elif (not _f.get("pool", 0) and not _f.get("asked", 0)
                and (_f.get("pool_stats") or {}).get("total", 0)):
            _ps = _f.get("pool_stats") or {}
            _msg = t("prop.pool_empty", total=_ps.get("total", 0),
                     recent=_ps.get("recently_reviewed", 0),
                     due=_ps.get("next_due_days") or 0,
                     waiting=_ps.get("awaiting_decision", 0),
                     rejected=_ps.get("rejected", 0))
        else:
            _msg = t("prop.reviewed",
                     n=len(r.get("applied") or []) if isinstance(r.get("applied"), list) else 0,
                     seen=_f.get("traced", _f.get("asked", 0)),
                     no_trace=_f.get("untraced", len(_f.get("no_trace") or [])),
                     dups=_d.get("looked_at", 0),
                     merged=len(_l.get("merged") or []),
                     pending=_f.get("pending", 0))
            _ex = r.get("expired") or {}
            if isinstance(_ex, dict) and (_ex.get("faded") or _ex.get("asked")):
                _msg = t("prop.expired", faded=_ex.get("faded", 0),
                         asked=len(_ex.get("asked") or [])) + "\n" + _msg
        self.prop_count.setText(_msg)
        if self.prop_list.count() == 1 \
                and self.prop_list.item(0).data(Qt.ItemDataRole.UserRole) is None:
            self.prop_list.item(0).setText(_msg)
        self._load_memory_lessons()

    def _prop_buttons_for(self, item, _prev=None):
        p = item.data(Qt.ItemDataRole.UserRole) if item else None
        ask = isinstance(p, dict) and p.get("action") == "ask"
        self.prop_accept_btn.setText(t("prop.ask_no") if ask else t("prop.accept"))
        self.prop_reject_btn.setText(t("prop.ask_yes") if ask else t("prop.reject"))

    def _decide_proposal(self, accept):
        item = self.prop_list.currentItem()
        p = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(p, dict):
            return
        self._post_decisions([p], bool(accept))

    def _decide_all_proposals(self):
        props = [self.prop_list.item(i).data(Qt.ItemDataRole.UserRole)
                 for i in range(self.prop_list.count())]
        props = [p for p in props if isinstance(p, dict) and p.get("id")]
        if not props:
            return
        if QMessageBox.question(
                self, t("tabs.proposals"), t("prop.confirm_all", n=len(props)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._post_decisions(props, True)

    def _post_decisions(self, props, accept):
        failed = []
        for p in props:
            try:
                requests.post(f"{self._mcp_url}/memory/proposals/{p['id']}",
                              json={"accept": bool(accept)}, timeout=20)
            except Exception as e:
                failed.append(f"{p.get('id')}: {e}")
        if failed:
            QMessageBox.warning(self, t("dlg.error"), "\n".join(failed[:10]))
        self._load_proposals()
        self._load_autolog()
        self._load_memory_facts()

    def _search_message_log(self):
        q = self.log_search.text().strip()
        if not q:
            return
        self.log_count.setText(t("mem.log_searching"))
        self.log_results.clear()

        def work():
            return self._mcp_call_json("session_moments",
                                       {"query": q, "limit": 8})

        def done(res):
            import html as _h
            if not isinstance(res, dict) or res.get("error"):
                self.log_count.setText(
                    t("mem.log_err", e=(res or {}).get("error", res)))
                return
            hits = res.get("moments") or []
            self.log_count.setText(t("mem.log_found", n=len(hits),
                                     total=res.get("total_logged", 0)))
            if not hits:
                self.log_results.setHtml(
                    f'<div style="color:#8296b1;padding:16px;">'
                    f'{_h.escape(t("mem.log_none"))}</div>')
                return
            out = []
            for hh in hits:
                _term = ", ".join(hh.get("matched_terms") or [])
                out.append(
                    f'<div style="color:#5b6b83;font-size:12px;margin-top:16px;">'
                    f'{_h.escape((hh.get("when") or "")[:16])}'
                    + (f' · <span style="color:#3b82f6;">{_h.escape(_term)}'
                       f'</span>' if _term else "") + '</div>')
                for c in (hh.get("messages") or []):
                    _yo = c.get("role") == "user"
                    out.append(
                        f'<div style="color:{"#e6edf7" if _yo else "#94a3b8"};'
                        f'{"border-left:3px solid #3b82f6;padding-left:6px;" if _yo else "padding-left:9px;"}">'
                        f'<b>{_h.escape(c.get("role", ""))}:</b> '
                        f'{_h.escape(c.get("text", "")[:600])}</div>')
            self.log_results.setHtml("".join(out))

        self._bg(work, done)

    def _forget_conversation(self):
        try:
            r = requests.get(f"{self._mcp_url}/log/sessions", timeout=10).json()
        except Exception as e:
            QMessageBox.warning(self, t("dlg.error"), str(e)); return
        ses = r.get("sessions") or []
        if not ses:
            QMessageBox.information(self, t("tabs.conversations"),
                                    t("log.nothing_logged")); return
        labels = [t("log.conv_row", n=x["n"], first=(x.get("first") or "")[:10],
                       last=(x.get("last") or "")[:10],
                       id=(x.get("session") or "—")[:16]) for x in ses]
        pick, ok = QInputDialog.getItem(self, t("log.forget_conv"),
                                        t("log.forget_which"), labels, 0, False)
        if not ok:
            return
        chosen = ses[labels.index(pick)]
        if QMessageBox.question(
                self, t("log.forget_conv"),
                t("log.forget_conv_q", n=chosen["n"]),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            res = requests.post(f"{self._mcp_url}/log/purge", timeout=30,
                                json={"session": chosen["session"]}).json()
            self.add_message(t("log.forgotten", n=res.get("deleted", 0)), "system")
        except Exception as e:
            QMessageBox.warning(self, t("dlg.error"), str(e))
        self.log_results.clear()
        self.log_count.setText("")

    def _forget_all_log(self):
        txt, ok = QInputDialog.getText(self, t("log.forget_all"),
                                       t("log.forget_all_q"))
        if not ok or (txt or "").strip().upper() != t("mem.purge_word"):
            self.add_message(t("mem.purge_cancelled"), "system"); return
        try:
            res = requests.post(f"{self._mcp_url}/log/purge", timeout=60,
                                json={"all": True,
                                      "confirm": t("mem.purge_word")}).json()
            self.add_message(t("log.forgotten", n=res.get("deleted", 0)), "system")
        except Exception as e:
            QMessageBox.warning(self, t("dlg.error"), str(e))
        self.log_results.clear()
        self.log_count.setText("")

    def _load_memory_facts(self):
        self.facts_table.clear()
        try:
            r = requests.get(f"{self._mcp_url}/facts/list", timeout=5)
            _j = r.json() if r.status_code == 200 else {}
            facts = _j.get("facts", [])
            self._facts_outside = _j.get("outside") or 0
            self._facts_archived = _j.get("archived") or 0
            self._facts_archived_bytes = _j.get("archived_bytes") or 0
        except Exception as e:
            self.facts_table.addItem(t("mem.facts_load_err", e=e))
            return
        _arch_view = getattr(self, "_facts_view", "live") == "archive"
        if getattr(self, "_facts_view_btns", None):
            self._facts_view_btns["live"].setText(
                t("mem.view_live") + f"  {len(facts) - self._facts_archived}")
            self._facts_view_btns["archive"].setText(
                t("mem.view_archive") + f"  {self._facts_archived}")
            self.facts_purge_archive.setEnabled(bool(self._facts_archived))
        facts = [f for f in facts
                 if (f.get("status") == "archived") == _arch_view]
        if not _arch_view and getattr(self, "facts_only_generic", None) is not None \
                and self.facts_only_generic.isChecked():
            facts = [f for f in facts if f.get("generic")]
        self._facts_all = list(facts)
        if not facts:
            self.facts_table.addItem(t("mem.archive_empty") if _arch_view
                                     else t("mem.no_facts"))
            self._filter_memory_facts()
            return
        _mode = (self.facts_sort.currentIndex()
                 if getattr(self, "facts_sort", None) is not None else 0)
        _when = lambda x: x.get("last_seen") or x.get("ts") or ""
        if _mode == 1:
            facts = sorted(facts, key=_when, reverse=True)
        elif _mode == 2:
            facts = sorted(facts, key=_when)
        elif _mode == 3:
            facts = sorted(facts, key=lambda x: (x.get("text", "") or "").lower())
        else:
            facts = sorted(facts, key=lambda x: x.get("hits", 1), reverse=True)
        for f in facts:
            hits = f.get("hits", 1)
            pin = "📌" if hits >= 3 else "  "
            tags = ", ".join(f.get("tags", []))
            _seen = (f.get("last_seen") or f.get("ts") or "")[:10]
            _life = {"permanent": t("mem.life_permanent"), "relevant": t("mem.life_relevant"),
                     "volatile": t("mem.life_volatile")}.get(f.get("life"), t("mem.life_unsorted"))
            if f.get("asking") and not _arch_view:
                _life += " · " + t("mem.left_asking")
            elif f.get("days_left") is not None and not _arch_view:
                _life += " · " + t("mem.left_volatile" if f.get("life") == "volatile"
                                   or f.get("until") else "mem.left_relevant", n=f["days_left"])
            _kb = f.get("kept_by") if not _arch_view else None
            if _kb:
                _life += " · " + t("mem.talked", d=(_kb.get("ts") or "")[:10])
            text = (f"{'⚠' if f.get('generic') else ' '}{pin} [{hits}×]  {_seen}  {_life}   "
                    f"{f.get('text', '')}")
            if _arch_view:
                _r = f.get("archived_reason") or ""
                _rk = {"superseded": "mem.arch_superseded", "merged": "mem.arch_merged",
                       "retire": "mem.arch_retire", "prune": "mem.arch_prune",
                       "fade": "mem.arch_fade", "ask": "mem.arch_ask"}.get(_r)
                _rl = t(_rk) if _rk else _r
                text = (f"{(f.get('archived_at') or '')[:10]}  ·  {_rl}   "
                        f"{f.get('text', '')}")
            if tags:
                text += f"   ⟨{tags}⟩"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, f)
            if _kb:
                item.setToolTip(t("mem.talked_tip", d=(_kb.get("ts") or "")[:16].replace("T", " "),
                                  text=_kb.get("text") or ""))
            self.facts_table.addItem(item)
        self._filter_memory_facts()

    def _filter_memory_facts(self, *_a):
        q = ((self.facts_filter.text() or "").strip().lower()
             if getattr(self, "facts_filter", None) is not None else "")
        shown = 0
        for i in range(self.facts_table.count()):
            it = self.facts_table.item(i)
            f = it.data(Qt.ItemDataRole.UserRole)
            if not isinstance(f, dict):
                continue
            hay = (f.get("text", "") or "").lower() + " " + \
                  " ".join(f.get("tags", []) or []).lower()
            hit = (not q) or (q in hay)
            it.setHidden(not hit)
            shown += 1 if hit else 0
        _tot = len(getattr(self, "_facts_all", []) or [])
        if getattr(self, "facts_count", None) is not None:
            _txt = (t("mem.facts_count", n=shown, total=_tot) if q
                    else t("mem.facts_total", total=_tot))
            _out = getattr(self, "_facts_outside", 0) or 0
            if _out:
                _txt += "   ·   " + t("mem.facts_outside", n=_out)
            if getattr(self, "_facts_view", "live") == "archive":
                _txt = t("mem.archive_info", n=_tot,
                         size=self._human_bytes(getattr(self, "_facts_archived_bytes", 0)))
            self.facts_count.setText(_txt)

    def _load_memory_lessons(self):
        self.lessons_table.clear()
        try:
            r = requests.get(f"{self._mcp_url}/lessons/list", timeout=5)
            lessons = r.json().get("lessons", []) if r.status_code == 200 else []
        except Exception as e:
            self.lessons_table.addItem(t("mem.lessons_load_err", e=e))
            return
        active = [l for l in lessons if not l.get("stale")]
        if not active:
            self.lessons_table.addItem(t("mem.no_lessons"))
            return
        for l in sorted(active, key=lambda x: x.get("confidence", 0), reverse=True):
            conf = l.get("confidence", 0)
            tags = ", ".join(l.get("tags", []))
            text = f"[{conf:.0%}] {l.get('text', '')}"
            if tags:
                text += f"   ⟨{tags}⟩"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, l)
            self.lessons_table.addItem(item)

    def _edit_fact_dialog(self, item):
        fact = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(fact, dict) or not fact.get("id"):
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(t("ui.edit_fact"))
        dlg.resize(500, 250)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(t("ui.fact_text")))
        text_edit = QTextEdit()
        text_edit.setAcceptRichText(False)
        text_edit.setPlainText(fact.get("text", ""))
        text_edit.setMaximumHeight(100)
        lay.addWidget(text_edit)
        lay.addWidget(QLabel("Tags (comma-separated):"))
        tags_entry = QLineEdit(", ".join(fact.get("tags", [])))
        lay.addWidget(tags_entry)
        hits_row = QHBoxLayout()
        hits_row.addWidget(QLabel(f"Reinforcements (hits): {fact.get('hits', 1)} "
                                  f"— ≥3 = pinned (always injected)"))
        lay.addLayout(hits_row)
        btn_row = QHBoxLayout()
        del_btn = QPushButton(t("ui.delete"))
        del_btn.setStyleSheet("color: #cd3131;")
        save_btn = QPushButton(t("ui.save"))
        cancel_btn = QPushButton(t("btn.cancel"))
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        lay.addLayout(btn_row)
        action = {"do": None}
        def _del():
            action["do"] = "delete"; dlg.accept()
        def _save():
            action["do"] = "save"; dlg.accept()
        del_btn.clicked.connect(_del)
        save_btn.clicked.connect(_save)
        cancel_btn.clicked.connect(dlg.reject)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            fid = fact["id"]
            if action["do"] == "delete":
                confirm = QMessageBox.question(self, t("cred.confirm_delete_fact"),
                    t("dlg.delete_fact_q") + f"\n\n{fact.get('text','')}",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if confirm == QMessageBox.StandardButton.Yes:
                    try:
                        requests.delete(f"{self._mcp_url}/facts/{fid}", timeout=5)
                    except Exception as e:
                        QMessageBox.warning(self, t("dlg.error"), str(e))
                    self._load_memory_facts()
            elif action["do"] == "save":
                new_text = text_edit.toPlainText().strip()
                new_tags = [t.strip() for t in tags_entry.text().split(",") if t.strip()]
                if not new_text:
                    QMessageBox.warning(self, t("dlg.empty_text"), t("dlg.fact_empty"))
                    return
                try:
                    requests.patch(f"{self._mcp_url}/facts/{fid}",
                                   json={"text": new_text, "tags": new_tags}, timeout=5)
                except Exception as e:
                    QMessageBox.warning(self, t("dlg.error"), str(e))
                self._load_memory_facts()

    def _edit_lesson_dialog(self, item):
        lesson = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(lesson, dict) or not lesson.get("id"):
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(t("ui.edit_lesson"))
        dlg.resize(500, 220)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(t("ui.lesson_text")))
        text_edit = QTextEdit()
        text_edit.setAcceptRichText(False)
        text_edit.setPlainText(lesson.get("text", ""))
        text_edit.setMaximumHeight(100)
        lay.addWidget(text_edit)
        btn_row = QHBoxLayout()
        del_btn = QPushButton(t("ui.delete"))
        del_btn.setStyleSheet("color: #cd3131;")
        cancel_btn = QPushButton("Close")
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)
        action = {"do": None}
        def _del():
            action["do"] = "delete"; dlg.accept()
        del_btn.clicked.connect(_del)
        cancel_btn.clicked.connect(dlg.reject)
        if dlg.exec() == QDialog.DialogCode.Accepted and action["do"] == "delete":
            lid = lesson["id"]
            try:
                requests.delete(f"{self._mcp_url}/lessons/{lid}", timeout=5)
            except Exception as e:
                QMessageBox.warning(self, t("dlg.error"), str(e))
            self._load_memory_lessons()


    def _setup_tools_page(self, parent_layout):
        header = QHBoxLayout()
        header.setContentsMargins(16, 12, 16, 6)
        header.addWidget(QLabel(f"<b>{t('tools.title')}</b>"))
        header.addStretch()
        refresh_btn = QPushButton("🔄")
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.setToolTip(t("tools.refresh_tip"))
        refresh_btn.clicked.connect(self._refresh_tools_list)
        header.addWidget(refresh_btn)
        parent_layout.addLayout(header)
        _hint = QLabel(t("tools.hint"))
        _hint.setWordWrap(True)
        _hint.setStyleSheet("color:#e6edf7; font-size:12px; padding:0 16px 4px;")
        parent_layout.addWidget(_hint)

        from tools_firewall import ToolPicker
        self.tool_picker = ToolPicker()
        self.tool_picker.changed.connect(self._on_firewall_changed)
        _wrap = QWidget()
        _wl = QVBoxLayout(_wrap)
        _wl.setContentsMargins(16, 0, 16, 10)
        _wl.addWidget(self.tool_picker)
        parent_layout.addWidget(_wrap, 1)

        self.blocked_tools = set()
        self._all_tools = {}

        from PyQt6.QtCore import QTimer
        QTimer.singleShot(2000, self._refresh_tools_list)
        self._approval_timer = QTimer(self)
        self._approval_timer.timeout.connect(self._poll_pending_approvals)
        self._approval_timer.start(3000)
        self._approvals_shown = set()
        self._approval_boxes = {}
        self._session_events_timer = QTimer(self)
        self._session_events_timer.timeout.connect(self._poll_session_events)
        self._session_events_timer.start(400)

    def _refresh_tools_list(self):
        def work():
            try:
                r = requests.get(f"{self._mcp_url}/firewall", timeout=5)
                return r.json() if r.status_code == 200 else None
            except Exception:
                return None

        def done(fw):
            if not isinstance(fw, dict):
                self.tool_picker.set_tools({}, set())
                self.tool_picker.count.setText(t("tools.mcp_unavailable"))
                return
            self.blocked_tools = set(fw.get("blocked", []))
            allt = fw.get("all_tools", {})
            if isinstance(allt, list):
                allt = {n: "" for n in allt}
            self._all_tools = allt
            self.tool_picker.set_tools(allt,
                                       set(allt) - self.blocked_tools)
        self._bg(work, done)

    def _on_firewall_changed(self, enabled):
        self.blocked_tools = set(self._all_tools) - set(enabled)
        _blocked = sorted(self.blocked_tools)

        def work():
            try:
                requests.post(f"{self._mcp_url}/firewall",
                              json={"blocked": _blocked}, timeout=5)
            except Exception:
                pass
        self._bg(work)

    def _sync_firewall(self):
        def work():
            try:
                r = requests.get(f"{self._mcp_url}/firewall", timeout=5)
                return r.json() if r.status_code == 200 else None
            except Exception:
                return None

        def done(res):
            if isinstance(res, dict):
                self.blocked_tools = set(res.get("blocked", []))
                if self.blocked_tools:
                    print(f"[FIREWALL] client lock: {sorted(self.blocked_tools)}",
                          file=sys.stderr)

        self._bg(work, done)

    def _setup_header(self, parent_layout):
        header = ShrinkRow()
        header.setObjectName("header_box")
        hl = header
        hl.row().setContentsMargins(16, 10, 16, 10)


        model = self._base_model_name()
        self.api_button = QPushButton(" " + t("ui.config_button"))
        self.api_button.setObjectName("api_button")
        self.api_button.setIcon(self._svg_icon("config", "#38bdf8", 18))
        self.api_button.setToolTip(t("ui.api_button_tip", model=model))
        self.api_button.clicked.connect(self.open_config_editor)
        hl.add(self.api_button)

        self.sync_pull_btn = QPushButton("⇊ Pull")
        self.sync_pull_btn.setToolTip(t("fed.sync_pull_tip"))
        self.sync_pull_btn.clicked.connect(lambda: self._do_sync(pull=True, manual=True))
        hl.add(self.sync_pull_btn, drop=8)
        self.sync_push_btn = QPushButton("⇈ Push")
        self.sync_push_btn.setToolTip(t("fed.sync_push_tip"))
        self.sync_push_btn.clicked.connect(lambda: self._do_sync(pull=False, manual=True))
        hl.add(self.sync_push_btn, drop=7)

        self.stats_btn = QPushButton(t("ui.usage_btn"))
        self.stats_btn.setToolTip(t("ui.stats_btn_tip"))
        self.stats_btn.clicked.connect(self._open_usage_dialog)
        hl.add(self.stats_btn, drop=6)



        self.browser_headless = self.config.getboolean("BROWSER", "headless", fallback=False)
        print(f"[BROWSER CONFIG] headless={self.browser_headless} (sections={self.config.sections()}, has BROWSER={self.config.has_section('BROWSER')})")
        if self.config.has_section("BROWSER"):
            print(f"[BROWSER CONFIG] items={dict(self.config.items('BROWSER'))}")
        self.headless_button = QPushButton(" HIDE" if self.browser_headless else " SHOW")
        self.headless_button.setToolTip(t("gui.headless_tooltip"))
        self.headless_button.clicked.connect(self._toggle_headless)
        self._update_headless_button()
        hl.add(self.headless_button, drop=5)

        self.rag_status_label = ElidedLabel(t("ui.rag_status_init"), hug=True)
        self.rag_status_label.setObjectName("status_label")
        hl.add(self.rag_status_label)

        self.fixed_status_label = ElidedLabel("/fixed: 0", hug=True)
        self.fixed_status_label.setObjectName("status_label")
        hl.add(self.fixed_status_label)

        hl.addStretch()

        for icon_name, label, slot, tip, icon_color, rank in [
            ("stats",  "Stats",   self.show_rag_stats,      t("btn.stats_tip"),  "#38bdf8", 4),
            ("rag",    "RAG",     self.clear_rag_index,     t("btn.rag_tip"),    "#fbbf24", 3),
            ("fixed",  "@fixed",  self.view_fixed_contexts, t("btn.fixed_tip"),  "#34d399", 2),
            ("config", t("btn.config_reload"), self.reload_config,      t("btn.config_tip"), "#a78bfa", 1),
            ("chat",   "Chat",    self.clear_chat,          t("btn.chat_tip"),   "#f87171", 9),
        ]:
            btn = QPushButton(f" {label}")
            btn.setIcon(self._svg_icon(icon_name, icon_color, 18))
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            hl.add(btn, drop=rank)

        parent_layout.addWidget(header)

    def _setup_chat_area(self, parent_layout):
        self._chat_tabs = [{
            "id": "local", "kind": "local", "peer": None,
            "title": t("chat.tab_local"), "history_ref": True,
        }]
        self._active_tab_id = "local"
        from fed_status_line import FedStatusLine
        self.fed_status = FedStatusLine()
        self.fed_status.clicked.connect(self._open_federation_tab)
        parent_layout.addWidget(self.fed_status)
        self._plan_timer = QTimer(self)
        self._plan_timer.timeout.connect(self._refresh_plan)
        self._plan_timer.start(60000)
        QTimer.singleShot(4000, self._refresh_plan)

        self._chat_tab_bar = QWidget()
        self._chat_tab_bar.setObjectName("chat_tab_bar")
        self._chat_tab_layout = QHBoxLayout(self._chat_tab_bar)
        self._chat_tab_layout.setContentsMargins(8, 4, 8, 0)
        self._chat_tab_layout.setSpacing(4)
        parent_layout.addWidget(self._chat_tab_bar)

        self.motd_banner = QWidget()
        self.motd_banner.setObjectName("motd_banner")
        _mb = QHBoxLayout(self.motd_banner)
        _mb.setContentsMargins(12, 7, 8, 7)
        _mb.setSpacing(10)
        self._motd_who = QLabel(t("motd.from_server"))
        self._motd_who.setObjectName("motd_who")
        _mb.addWidget(self._motd_who)
        self._motd_text = QLabel("")
        self._motd_text.setWordWrap(True)
        self._motd_text.setMaximumHeight(34)
        self._motd_text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        _mb.addWidget(self._motd_text, 1)
        self._motd_more = QPushButton(t("motd.read_all"))
        self._motd_more.setCursor(Qt.CursorShape.PointingHandCursor)
        self._motd_more.setVisible(False)
        self._motd_more.clicked.connect(self._show_motd_full)
        _mb.addWidget(self._motd_more)
        self._motd_close = QPushButton("✕")
        self._motd_close.setFixedWidth(26)
        self._motd_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._motd_close.setToolTip(t("motd.dismiss"))
        self._motd_close.clicked.connect(self._dismiss_motd)
        _mb.addWidget(self._motd_close)
        self.motd_banner.setVisible(False)
        parent_layout.addWidget(self.motd_banner)


        from PyQt6.QtWidgets import QStackedWidget
        self._chat_views = _ShrinkStack()
        self._chat_view_by_tab = {}
        self._chat_font_base = self.config.getint("CHAT", "font_size",
                                                  fallback=13)
        try:
            from PyQt6.QtCore import QSettings
            self._chat_font_pt = int(QSettings("hoplita", "hoplita").value(
                "chat_font_size", self._chat_font_base))
        except Exception:
            self._chat_font_pt = self._chat_font_base
        self._zoom_targets = []
        self.chat_display = self._show_chat_view(
            getattr(self, "_active_tab_id", None) or "local")
        from PyQt6.QtWidgets import QStackedWidget
        self._chat_stack = _ShrinkStack()
        _chat_page = QWidget()
        _cp = QVBoxLayout(_chat_page)
        _cp.setContentsMargins(0, 0, 0, 0)
        _cp.setSpacing(4)
        self._intent_strip = QLabel()
        self._intent_strip.setTextFormat(Qt.TextFormat.RichText)
        self._intent_strip.setWordWrap(True)
        self._intent_strip.setVisible(False)
        self._intent_strip.setCursor(Qt.CursorShape.PointingHandCursor)
        self._intent_strip.setToolTip(t("agents.intent_strip_tip"))
        self._intent_strip.setStyleSheet(
            "QLabel{background:#151b30;border:1px solid #2b3560;"
            "border-radius:8px;padding:5px 10px;color:#e6edf7;font-size:12px;}"
            "QLabel:hover{border-color:#6366f1;}")
        self._intent_strip.mousePressEvent = \
            lambda e: self._toggle_agents_view()
        _cp.addWidget(self._intent_strip)
        self._sticky_bar = QWidget()
        _sb = QVBoxLayout(self._sticky_bar)
        _sb.setContentsMargins(0, 0, 0, 0)
        _sb.setSpacing(2)
        self._sticky_head = ElidedButton(pad=26)
        self._sticky_head.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sticky_head.setStyleSheet(
            "QPushButton{background:#3a3413;border:1px solid #6b5d18;"
            "border-radius:8px;padding:5px 10px;color:#f5e6a3;font-size:13px;"
            "text-align:left;min-height:0;}"
            "QPushButton:hover{border-color:#c9a227;}")
        self._sticky_head.clicked.connect(self._sticky_toggle)
        _sb.addWidget(self._sticky_head)
        self._sticky_edit = PlainTextEditor()
        self._sticky_edit.setStyleSheet(
            "QTextEdit{background:#2e2910;color:#f5e6a3;border:1px solid "
            "#6b5d18;border-radius:8px;font-size:13px;}")
        self._sticky_edit.setMaximumHeight(230)
        self._sticky_edit.setVisible(False)
        self._sticky_edit.textChanged.connect(self._sticky_user_edited)
        self._zoom_targets.append((
            self._sticky_edit,
            "QTextEdit{background:#2e2910;color:#f5e6a3;border:1px solid "
            "#6b5d18;border-radius:8px;}", 0))
        _sb.addWidget(self._sticky_edit)
        self._sticky_open = False
        _cp.addWidget(self._sticky_bar)
        from machine_view import MachineView
        self.machine_view = MachineView()
        self._machines = {}
        self._chat_split = QSplitter(Qt.Orientation.Horizontal)
        self._chat_split.setChildrenCollapsible(False)
        self._chat_split.setHandleWidth(6)
        self._chat_split.setStyleSheet("QSplitter::handle { background:#0b1220; }")
        self._chat_split.addWidget(self._chat_views)
        _cp.addWidget(self._chat_split, 1)
        self._curtain_host = _chat_page
        self._curtains = {}
        try:
            self._machine_on = str(QSettings("hoplita", "hoplita").value("machine/visible", "0")) == "1"
        except Exception:
            self._machine_on = False
        self._curtain_add(self.machine_view, "machine/width", 380)
        from turn_view import TurnView
        self.turn_view = TurnView()
        self.turn_view.closed.connect(self._tu_close)
        self.turn_view.full_toggled.connect(self._tu_full_toggle)
        self._curtain_add(self.turn_view, "turn/width", 520)
        self._tu_shown = 0
        self._tu_full = False
        if self.dashboard is not None:
            self._curtain_add(self.dashboard, "canvas/width", 560)
            self._canvas_w = self._curtains[self.dashboard]["w"]
        if self._machine_on:
            self._curtains[self.machine_view]["open"] = True
            self.machine_view.show()
        self._chat_views.installEventFilter(self)
        self._chat_stack.addWidget(_chat_page)
        self._session_cards = {}
        try:
            from agents_widget import AgentsWidget
            self.agents_panel = AgentsWidget()
            self.agents_panel.set_labels(self._agents_labels())
            self.agents_panel.ack_decided.connect(self._on_ack_decided)
            self._chat_stack.addWidget(self.agents_panel)
            self._ack_showing_tab = None
        except Exception as e:
            self.agents_panel = None
            print(f"[agents] viewer unavailable: {e}", file=sys.stderr)
        _code_page = QWidget()
        _cl = QVBoxLayout(_code_page)
        _cl.setContentsMargins(0, 8, 0, 0)
        self._setup_code_page(_cl)
        self._code_page_idx = self._chat_stack.addWidget(_code_page)
        _council_page = QWidget()
        _kl = QVBoxLayout(_council_page)
        _kl.setContentsMargins(0, 8, 0, 0)
        self._setup_council_page(_kl)
        self._council_page_idx = self._chat_stack.addWidget(_council_page)
        from tabs_overview import TabsOverview
        self.tabs_overview = TabsOverview()
        self.tabs_overview.open_tab.connect(self._overview_open)
        self.tabs_overview.stop_turn.connect(self._overview_stop_turn)
        self.tabs_overview.stop_job.connect(self._overview_stop_job)
        self._overview_page_idx = self._chat_stack.addWidget(self.tabs_overview)
        self._ovw_timer = QTimer(self)
        self._ovw_timer.setInterval(1000)
        self._ovw_timer.timeout.connect(self._overview_tick)
        self._ovw_jobs = {}
        parent_layout.addWidget(self._chat_stack, 1)
        self._render_chat_tabs()
        self._sticky_paint()

    _PIN_PULSE_MS = 900

    def _start_pin_pulse(self):
        tmr = getattr(self, "_pin_timer", None)
        if tmr is None:
            tmr = QTimer(self)
            tmr.setInterval(self._PIN_PULSE_MS)
            tmr.timeout.connect(self._pin_pulse_tick)
            self._pin_timer = tmr
        if getattr(self, "_pin_buttons", None):
            if not tmr.isActive():
                tmr.start()
        elif tmr.isActive():
            tmr.stop()

    def _pin_pulse_tick(self):
        self._pin_phase = not getattr(self, "_pin_phase", False)
        vivo = self._pin_phase
        for frame in list(getattr(self, "_pin_buttons", []) or []):
            try:
                col = frame.property("_pin_col") or "#38bdf8"
                act = bool(frame.property("_pin_active"))
                frame.set_beat(col, (0.10 if act else 0.12) if vivo else 0.0)
            except RuntimeError:
                pass

    _WORK_TICK_MS = 33

    def _tab_working(self, tab_id):
        c = self._chat_ctxs.get(tab_id)
        return bool((c is not None and getattr(c, "task_running", False))
                    or tab_id in getattr(self, "_session_waiting", ()))

    def _tabs_working_check(self):
        now = frozenset(x["id"] for x in self._chat_tabs
                        if self._tab_working(x["id"]))
        if now != getattr(self, "_working_seen", frozenset()):
            self._render_chat_tabs()

    def _start_work_pulse(self):
        tmr = getattr(self, "_work_timer", None)
        if tmr is None:
            tmr = QTimer(self)
            tmr.setInterval(self._WORK_TICK_MS)
            tmr.timeout.connect(self._work_pulse_tick)
            self._work_timer = tmr
        if getattr(self, "_work_tabs", None):
            if not tmr.isActive():
                tmr.start()
        elif tmr.isActive():
            tmr.stop()

    def _work_pulse_tick(self):
        for btn in list(getattr(self, "_work_tabs", []) or []):
            try:
                btn.update()
            except RuntimeError:
                pass

    def _chat_tab_color(self, kind):
        return {"local": "#3b82f6", "green": "#22c55e", "red": "#ef4444",
                "cron": "#f59e0b"}.get(kind, "#3b82f6")

    def _hex_rgba(self, hexcol, a):
        h = hexcol.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"rgba({r},{g},{b},{a})"

    def _chat_tab_css(self, col, active, bg=None):
        if bg is None:
            bg = self._hex_rgba(col, 0.12) if active else "transparent"
        return (f"#chatTab {{ background:{bg}; border:none; "
                f"border-bottom:2px solid {col if active else 'transparent'}; "
                f"border-top-left-radius:6px; border-top-right-radius:6px; }} "
                f"#chatTab:hover {{ background:{bg if active else '#1b2536'}; }} "
                f"#chatTab QPushButton#tabTitle {{ background:transparent; border:none; "
                f"color:{'#e6edf7' if active else self._hex_rgba(col, 0.82)}; "
                f"padding:4px 6px 4px 11px; font-size:12px; font-weight:600; }} "
                f"#chatTab QPushButton#tabClose, #chatTab QPushButton#tabReset {{ "
                f"background:transparent; border:none; border-radius:9px; "
                f"color:{'#cbd5e1' if active else '#8796ad'}; font-size:14px; padding:0; }} "
                f"#chatTab QPushButton#tabClose:hover {{ background:#3a1d24; color:#f87171; }} "
                f"#chatTab QPushButton#tabReset:hover {{ background:#1b2744; color:#93c5fd; }}")

    _TAB_MIN_W = 124
    _TAB_MAX_W = 300
    _TAB_CHROME_W = 34

    def _strip_controls_w(self):
        got = getattr(self, "_strip_ctrl_w", 0)
        if got:
            return got + 40
        fm = QFontMetrics(self.font())
        keys = ("strip.history", "strip.save", "strip.tabs", "strip.canvas",
                "strip.machine", "strip.agents", "strip.code", "strip.council")
        return sum(fm.horizontalAdvance(t(k)) + 28 for k in keys) + 40

    def _chat_tabs_fit(self):
        tabs = list(self._chat_tabs)
        bar = getattr(self, "_chat_tab_bar", None)
        room = max(200, (bar.width() if bar is not None and bar.width() > 50
                         else self.width()) - self._strip_controls_w())
        per = room // max(1, len(tabs))
        if per >= self._TAB_MIN_W:
            return tabs, [], max(40, min(self._TAB_MAX_W, per) - self._TAB_CHROME_W)
        fit = max(1, room // self._TAB_MIN_W - 1)
        shown = tabs[:fit]
        active = next((x for x in tabs if x["id"] == self._active_tab_id), None)
        if active is not None and active not in shown:
            shown = shown[:-1] + [active] if len(shown) > 1 else [active]
        hidden = [x for x in tabs if x not in shown]
        return shown, hidden, self._TAB_MIN_W - self._TAB_CHROME_W

    def _chat_tabs_more_menu(self, anchor, hidden):
        menu = QMenu(self)
        for tab in hidden:
            a = menu.addAction(tab["title"])
            a.triggered.connect(lambda _=False, tid=tab["id"]: self._switch_chat_tab(tid))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _render_chat_tabs(self):
        _pinned_btns = []
        self._pin_buttons = _pinned_btns
        _work_tabs = []
        self._work_tabs = _work_tabs
        self._working_seen = frozenset(
            x["id"] for x in self._chat_tabs if self._tab_working(x["id"]))
        lay = self._chat_tab_layout
        lay.setSpacing(1)
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w:
                w.hide()
                w.deleteLater()
        _shown, _hidden, _title_w = self._chat_tabs_fit()
        _shown_hidden_before = _hidden
        _fm = QFontMetrics(self.font())
        for tab in _shown:
            active = tab["id"] == self._active_tab_id
            col = self._chat_tab_color(tab["kind"])
            _c = self._chat_ctxs.get(tab["id"])
            waiting = getattr(_c, "pending_ack", None) is not None
            if waiting:
                col = "#f5b800"
            _pin = getattr(_c, "pinned_device", None)
            _mark = f" 📍{_pin.get('name', '?')}" if _pin else ""
            if getattr(_c, "no_log", False):
                _mark = " 🕶" + _mark
            elif getattr(_c, "local_only", False):
                _mark = " 🔒" + _mark
            _nfw = len(getattr(_c, "blocked_tools", ()) or ())
            if _nfw:
                _mark = " 🛡" + _mark
            _ended = bool(tab.get("ended"))
            if _ended:
                col = "#64748b"
                _mark += " ⏹"
            _job = getattr(_c, "job_name", "") if _c is not None else ""
            if _job:
                _mark = " ⏰" + _mark
            _news = bool(getattr(self, "_canvases", None)
                         and self._canvas(tab["id"])["unseen"])
            if _news:
                _mark = " ▣" + _mark
            working = (not waiting and not _ended
                       and self._tab_working(tab["id"]))
            frame = _TabFrame()
            frame.setObjectName("chatTab")
            _fl = QHBoxLayout(frame)
            _fl.setContentsMargins(0, 0, 4, 0)
            _fl.setSpacing(0)
            _mark_w = _fm.horizontalAdvance("◐")
            _blank = " " * max(2, round(_fm.horizontalAdvance("◐ ") / max(1.0, _fm.horizontalAdvance(" "))))
            _eye = "👁 " if getattr(self._chat_ctxs.get(tab["id"]), "bg_live", 0) else ""
            _pre = "⏸ " if waiting else _blank if working else _eye
            _room = int(_title_w) - _fm.horizontalAdvance(_pre + _mark)
            _shown_label = _pre + _fm.elidedText(
                tab["title"], Qt.TextElideMode.ElideRight, max(24, _room)) + _mark
            btn = TabTitleButton(_shown_label, _mark_w)
            btn.spin = bool(working)
            btn.spin_colour = col
            btn.setObjectName("tabTitle")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            _tips = [tab["title"]]
            if waiting:
                _tips.append(t("plan.ack_waiting_tip"))
            elif working:
                _tips.append(t("chat.tab_working_tip"))
            elif _ended:
                _tips.append(t("fed.session_ended_tip"))
            elif _pin:
                _tips.append(t("pin.current", name=_pin.get("name", "?"),
                               id=_pin.get("id", "?")[:8]))
            if _nfw:
                _tips.append(t("fwtab.tip", n=_nfw))
            if _job:
                _tips.append(t("job.tab_tip", name=_job))
            if _news:
                _tips.append(t("canvas.new_in_tab"))
            _bgn = getattr(self._chat_ctxs.get(tab["id"]), "bg_live", 0)
            if _bgn:
                _tips.append(t("bg.tab_tip", n=_bgn, names=", ".join(
                    getattr(self._chat_ctxs.get(tab["id"]), "bg_labels", []) or [])))
            btn.setToolTip("\n".join(_tips))
            frame.setStyleSheet(self._chat_tab_css(col, active))
            if working:
                _work_tabs.append(btn)
            elif _pin:
                frame.setProperty("_pin_col", col)
                frame.setProperty("_pin_active", active)
                _pinned_btns.append(frame)
            btn.clicked.connect(lambda _=False, tid=tab["id"]: self._switch_chat_tab(tid))
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, tid=tab["id"], b=btn:
                    self._chat_tab_menu(tid, b.mapToGlobal(pos)))
            _fl.addWidget(btn)
            _primary = (tab["id"] == "local")
            x = QPushButton("↺" if _primary else "×")
            x.setObjectName("tabReset" if _primary else "tabClose")
            x.setFixedSize(18, 18)
            x.setCursor(Qt.CursorShape.PointingHandCursor)
            x.setToolTip(t("chat.tab_reset") if _primary else t("chat.tab_close"))
            x.clicked.connect(lambda _=False, tid=tab["id"]: self._close_chat_tab(tid))
            _fl.addWidget(x, 0, Qt.AlignmentFlag.AlignVCenter)
            lay.addWidget(frame)
        if _hidden:
            more = QPushButton(f"+{len(_hidden)} ▾")
            more.setObjectName("tabMore")
            more.setCursor(Qt.CursorShape.PointingHandCursor)
            more.setToolTip(t("chat.tab_more_tip", n=len(_hidden)))
            more.setStyleSheet(
                "QPushButton { background:#1e293b; color:#cbd5e1; border:1px solid "
                "#334155; border-radius:8px; padding:3px 10px; margin-left:4px; } "
                "QPushButton:hover { background:#334155; color:#ffffff; }")
            more.clicked.connect(lambda _=False, b=more, h=list(_hidden):
                                 self._chat_tabs_more_menu(b, h))
            lay.addWidget(more, 0, Qt.AlignmentFlag.AlignVCenter)
        plus = QPushButton("+")
        plus.setFixedSize(24, 24)
        plus.setCursor(Qt.CursorShape.PointingHandCursor)
        plus.setToolTip(t("chat.tab_new_tip"))
        plus.setStyleSheet(
            "QPushButton { background:rgba(59,130,246,0.22); color:#bfdbfe; "
            "border:1px solid #3b82f6; border-radius:12px; font-size:16px; "
            "font-weight:700; padding:0 0 2px 0; min-width:22px; max-width:22px; "
            "min-height:22px; max-height:22px; } "
            "QPushButton:hover { background:#3b82f6; color:#ffffff; }")
        plus.clicked.connect(self._new_local_tab)
        lay.addSpacing(8)
        lay.addWidget(plus, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch()
        _right_from = lay.count()
        for key, tip, slot in (
                ("strip.history", t("sess.history_tip"), self._open_sessions_dialog),
                ("strip.save", t("sess.save_tip"), self._save_current_session)):
            b = QPushButton(t(key))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tip)
            b.setStyleSheet(
                "QPushButton { background:#1e293b; color:#cbd5e1; border:1px solid #334155; "
                "border-radius:8px; padding:5px 10px; margin-left:4px; } "
                "QPushButton:hover { background:#334155; color:#e6edf7; }")
            b.clicked.connect(slot)
            lay.addWidget(b)
        if getattr(self, "_overview_page_idx", None) is not None:
            ocol = "#93c5fd"
            _n = len(self._chat_tabs)
            _b = len(getattr(self, "_working_seen", ()) or ())
            oon = self._chat_stack.currentIndex() == self._overview_page_idx
            ob = QPushButton(f"{t('strip.tabs')} {_b}/{_n}" if _b else t("strip.tabs"))
            ob.setCursor(Qt.CursorShape.PointingHandCursor)
            ob.setToolTip(t("ovw.toggle_tip"))
            ob.setStyleSheet(
                (f"QPushButton {{ background:{self._hex_rgba(ocol, 0.18)}; color:{ocol}; "
                 f"border:1px solid {ocol}; border-radius:8px; padding:5px 12px; "
                 f"font-weight:700; }}") if oon else
                (f"QPushButton {{ background:#1e293b; color:{ocol}; border:1px solid {ocol}; "
                 f"border-radius:8px; padding:5px 12px; }} "
                 f"QPushButton:hover {{ background:#334155; }}"))
            ob.clicked.connect(self._toggle_overview)
            lay.addWidget(ob)
        if getattr(self, "dashboard", None) is not None:
            _crec = self._canvas(self._active_tab_id)
            vcol = "#f5b800" if _crec["unseen"] and not _crec["open"] else "#93c5fd"
            vb = QPushButton(t("strip.canvas"))
            vb.setCursor(Qt.CursorShape.PointingHandCursor)
            vb.setToolTip(t("canvas.toggle_tip"))
            vb.setStyleSheet(
                (f"QPushButton {{ background:{self._hex_rgba(vcol, 0.18)}; color:{vcol}; "
                 f"border:1px solid {vcol}; border-radius:8px; padding:5px 12px; "
                 f"font-weight:700; }}") if _crec["open"] else
                (f"QPushButton {{ background:#1e293b; color:{vcol}; border:1px solid {vcol}; "
                 f"border-radius:8px; padding:5px 12px; }} "
                 f"QPushButton:hover {{ background:#334155; }}"))
            vb.clicked.connect(self._toggle_canvas)
            lay.addWidget(vb)
        if getattr(self, "machine_view", None) is not None:
            mcol = "#93c5fd"
            mon = bool(getattr(self, "_machine_on", False))
            mb = QPushButton(t("strip.machine"))
            mb.setCursor(Qt.CursorShape.PointingHandCursor)
            mb.setToolTip(t("machine.toggle_tip"))
            mb.setStyleSheet(
                (f"QPushButton {{ background:{self._hex_rgba(mcol, 0.18)}; color:{mcol}; "
                 f"border:1px solid {mcol}; border-radius:8px; padding:5px 12px; "
                 f"font-weight:700; }}") if mon else
                (f"QPushButton {{ background:#1e293b; color:{mcol}; border:1px solid {mcol}; "
                 f"border-radius:8px; padding:5px 12px; }} "
                 f"QPushButton:hover {{ background:#334155; }}"))
            mb.clicked.connect(self._toggle_machine)
            lay.addWidget(mb)
        if getattr(self, "agents_panel", None) is not None:
            n = len(self._agents_cards_for_active())
            showing = (getattr(self, "_chat_stack", None) is not None
                       and self._chat_stack.currentIndex() == 1)
            col = "#a855f7"
            tgl = QPushButton(f"{t('strip.agents')} {n}" if n else t("strip.agents"))
            tgl.setCursor(Qt.CursorShape.PointingHandCursor)
            tgl.setToolTip(t("agents.toggle_tip"))
            if showing:
                tgl.setStyleSheet(
                    f"QPushButton {{ background:{self._hex_rgba(col, 0.18)}; "
                    f"color:{col}; border:1px solid {col}; border-radius:8px; "
                    f"padding:5px 12px; font-weight:700; }}")
            else:
                tgl.setStyleSheet(
                    f"QPushButton {{ background:#1e293b; color:{col}; "
                    f"border:1px solid {col}; border-radius:8px; padding:5px 12px; }} "
                    f"QPushButton:hover {{ background:#334155; }}")
            tgl.clicked.connect(self._toggle_agents_view)
            lay.addWidget(tgl)
        if getattr(self, "_code_page_idx", None) is not None:
            ccol = "#38bdf8"
            on = self._chat_stack.currentIndex() == self._code_page_idx
            cb = QPushButton(t("strip.code"))
            cb.setCursor(Qt.CursorShape.PointingHandCursor)
            cb.setToolTip(t("code.toggle_tip"))
            cb.setStyleSheet(
                (f"QPushButton {{ background:{self._hex_rgba(ccol, 0.18)}; color:{ccol}; "
                 f"border:1px solid {ccol}; border-radius:8px; padding:5px 12px; "
                 f"font-weight:700; }}") if on else
                (f"QPushButton {{ background:#1e293b; color:{ccol}; border:1px solid {ccol}; "
                 f"border-radius:8px; padding:5px 12px; }} "
                 f"QPushButton:hover {{ background:#334155; }}"))
            cb.clicked.connect(self._toggle_code_view)
            lay.addWidget(cb)
        if getattr(self, "_council_page_idx", None) is not None:
            kcol = self._COUNCIL_COL
            _kc = self._chat_ctxs.get(self._active_tab_id)
            _seated = getattr(_kc, "council_members", None)
            _chairs = self._council_pool_names()
            if _seated is None:
                _seated = [n for n in _chairs if n != "base"]
            else:
                _seated = [n for n in _seated if n in _chairs]
            _waiting = getattr(_kc, "council_ack", None) is not None
            if _waiting:
                kcol = "#f5b800"
            kon = self._chat_stack.currentIndex() == self._council_page_idx
            kb = QPushButton(f"{t('strip.council')} {len(_seated)}" if _seated else t("strip.council"))
            kb.setCursor(Qt.CursorShape.PointingHandCursor)
            kb.setToolTip(t("council.ack_waiting_tip") if _waiting
                          else t("council.toggle_tip"))
            kb.setStyleSheet(
                (f"QPushButton {{ background:{self._hex_rgba(kcol, 0.18)}; "
                 f"color:{kcol}; border:1px solid {kcol}; border-radius:8px; "
                 f"padding:5px 12px; font-weight:700; }}") if kon else
                (f"QPushButton {{ background:#1e293b; color:{kcol}; "
                 f"border:1px solid {kcol}; border-radius:8px; "
                 f"padding:5px 12px; }} "
                 f"QPushButton:hover {{ background:#334155; }}"))
            kb.clicked.connect(self._toggle_council_view)
            lay.addWidget(kb)
        _cw = sum(lay.itemAt(i).widget().sizeHint().width() + lay.spacing()
                  for i in range(_right_from, lay.count()) if lay.itemAt(i).widget())
        _before = getattr(self, "_strip_ctrl_w", 0)
        self._strip_ctrl_w = _cw
        if _before != _cw and not getattr(self, "_strip_refit", False):
            self._strip_refit = True
            try:
                if self._chat_tabs_fit()[1] != _shown_hidden_before:
                    self._render_chat_tabs()
            finally:
                self._strip_refit = False
        self._start_pin_pulse()
        self._start_work_pulse()

    def _agents_cards_for_active(self):
        return self._agents_cards_for(self._tab_by_id(self._active_tab_id) or {})

    def _agents_cards_for(self, act):
        kind = act.get("kind")
        if kind in ("local", "cron"):
            ctx = self._chat_ctxs.get(act.get("id"))
            return list(getattr(ctx, "agents", None) or [])
        if kind in ("green", "red") and act.get("peer"):
            card = (getattr(self, "_session_cards", {}) or {}).get(act["peer"])
            return [card] if card else []
        return []

    def _sticky_toggle(self):
        self._sticky_open = not getattr(self, "_sticky_open", False)
        self._sticky_edit.setVisible(self._sticky_open)
        self._sticky_paint()

    def _sticky_user_edited(self):
        if getattr(self, "_sticky_painting", False):
            return
        self.sticky = self._sticky_edit.toPlainText()
        self._sticky_head_text()

    def _sticky_head_text(self):
        _n = len([l for l in (self.sticky or "").split("\n") if l.strip()])
        if not _n and not self._sticky_open:
            self._sticky_head.setText(t("sticky.head_empty"))
        else:
            self._sticky_head.setText(
                t("sticky.head_open" if self._sticky_open else "sticky.head", n=_n))

    def _sticky_paint(self, tab_id=None):
        if getattr(self, "_sticky_bar", None) is None:
            return
        if tab_id and tab_id != self._active_tab_id:
            return
        _txt = self._view_ctx().sticky or ""
        self._sticky_painting = True
        try:
            if self._sticky_edit.toPlainText() != _txt:
                self._sticky_edit.setPlainText(_txt)
        finally:
            self._sticky_painting = False
        self._sticky_bar.setVisible(True)
        self._sticky_edit.setVisible(self._sticky_open)
        self._sticky_head_text()

    def _toggle_agents_view(self):
        if getattr(self, "agents_panel", None) is None \
                or getattr(self, "_chat_stack", None) is None:
            return
        if self._chat_stack.currentIndex() == 1:
            self._chat_stack.setCurrentIndex(0)
        else:
            self._refresh_agents_view()
            self._chat_stack.setCurrentIndex(1)
        self._render_chat_tabs()

    _STICKY_MAX_LINES = 15

    _COUNCIL_COL = "#d4a54a"

    def _setup_council_page(self, parent_layout):
        from PyQt6.QtWidgets import (QCheckBox, QRadioButton, QButtonGroup,
                                     QTextBrowser)

        outer = QVBoxLayout()
        outer.setContentsMargins(14, 4, 14, 8)
        outer.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(12)
        glyph = QLabel()
        glyph.setPixmap(self._svg_icon("tab_council", self._COUNCIL_COL,
                                       46).pixmap(46, 46))
        glyph.setFixedSize(52, 52)
        head.addWidget(glyph)
        _tt = QVBoxLayout()
        _tt.setSpacing(1)
        _title = QLabel(t("council.title"))
        _title.setStyleSheet(f"color:{self._COUNCIL_COL}; font-size:19px; "
                             f"font-weight:700; letter-spacing:.5px;")
        _sub = QLabel(t("council.subtitle"))
        _sub.setStyleSheet("color:#e6edf7; font-size:12px;")
        _sub.setWordWrap(True)
        _tt.addWidget(_title)
        _tt.addWidget(_sub)
        head.addLayout(_tt, 1)
        outer.addLayout(head)

        bench = QWidget()
        bench.setStyleSheet(
            "QWidget#bench{background:#0f1626; border:1px solid #26324a; "
            "border-radius:10px;}")
        bench.setObjectName("bench")
        bl = QVBoxLayout(bench)
        bl.setContentsMargins(12, 9, 12, 9)
        bl.setSpacing(5)
        _bh = QLabel(t("council.bench_head"))
        _bh.setStyleSheet("color:#e6edf7; font-size:12px; font-weight:600;")
        bl.addWidget(_bh)
        self._council_rows = []
        self._council_first_group = QButtonGroup(bench)
        self._council_first_group.setExclusive(True)
        _seats = QVBoxLayout()
        _seats.setSpacing(3)
        bl.addLayout(_seats)
        self._council_seats_layout = _seats
        self._council_bench_empty = QLabel(t("council.pool_empty"))
        self._council_bench_empty.setStyleSheet("color:#ef4444; font-size:12px;")
        self._council_bench_empty.setWordWrap(True)
        self._council_bench_empty.setVisible(False)
        bl.addWidget(self._council_bench_empty)
        self._council_ctx_chk = QCheckBox(t("council.carry_context"))
        self._council_ctx_chk.setToolTip(t("council.carry_context_tip"))
        self._council_ctx_chk.setChecked(True)
        self._council_ctx_chk.setStyleSheet("color:#e6edf7; font-size:12px;")
        self._council_ctx_chk.toggled.connect(self._council_store_bench)
        bl.addWidget(self._council_ctx_chk)
        outer.addWidget(bench)

        self.council_view = QTextBrowser()
        self.council_view.setOpenExternalLinks(False)
        self.council_view.setStyleSheet(
            "QTextBrowser{background:#0b1220; border:1px solid #1f2937; "
            "border-radius:10px; padding:10px; color:#e6edf7;}")
        outer.addWidget(self.council_view, 1)
        self.council_web = None
        try:
            from council_widget import CouncilWidget, labels as _council_labels
            self.council_web = CouncilWidget()
            self.council_web.set_labels(_council_labels(t))
            self.council_web.bench_changed.connect(self._council_web_bench_changed)
            outer.addWidget(self.council_web, 1)
            self.council_view.setVisible(False)
            for _w in (glyph, _title, _sub, bench):
                _w.setVisible(False)
            outer.setContentsMargins(0, 0, 0, 8)
        except Exception as e:
            print(f"[council] drawn agora unavailable: {e}", file=sys.stderr)

        self._council_gate_bar = QWidget()
        self._council_gate_bar.setStyleSheet(
            "QWidget#gate{background:#2a2210; border:1px solid #a16207; "
            "border-radius:10px;}")
        self._council_gate_bar.setObjectName("gate")
        gl = QHBoxLayout(self._council_gate_bar)
        gl.setContentsMargins(12, 8, 12, 8)
        gl.setSpacing(10)
        self._council_gate_lbl = QLabel("")
        self._council_gate_lbl.setWordWrap(True)
        self._council_gate_lbl.setStyleSheet("color:#fbbf24; font-size:12px;")
        gl.addWidget(self._council_gate_lbl, 1)
        for _txt, _tip, _dec, _bg in (
                (t("council.gate_again"), t("council.gate_again_tip"),
                 "again", "#a16207"),
                (t("council.gate_stop"), t("council.gate_stop_tip"),
                 "stop", "#334155")):
            b = QPushButton(_txt)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(_tip)
            b.setStyleSheet(
                f"QPushButton{{background:{_bg}; color:#f8fafc; border:none; "
                f"border-radius:7px; padding:6px 14px; font-weight:600;}}"
                f"QPushButton:hover{{background:#475569;}}")
            b.clicked.connect(lambda _c=False, d=_dec: self._council_gate_answer(d))
            gl.addWidget(b)
        self._council_gate_bar.setVisible(False)
        outer.addWidget(self._council_gate_bar)

        parent_layout.addLayout(outer)
        self._council_welcome()

    def _council_pool_names(self):
        pool = self._model_pool()
        names = sorted(n for n, m in pool.items() if m.get("council", True))
        try:
            _bu, _, _bm = self._chat_base_endpoint()
        except Exception:
            _bu, _bm = "", ""
        _key = ((_bu or "").rstrip("/").lower(), (_bm or "").strip())
        if not any(((m.get("base_url") or "").rstrip("/").lower(),
                    (m.get("model") or "").strip()) == _key
                   for n, m in pool.items() if n in names):
            names.append("base")
        return names

    def _council_build_bench(self, ctx):
        from PyQt6.QtWidgets import QCheckBox, QRadioButton
        lay = self._council_seats_layout
        while lay.count():
            _row = lay.takeAt(0).layout()
            if _row is None:
                continue
            while _row.count():
                w = _row.takeAt(0).widget()
                if w is None:
                    continue
                if isinstance(w, QRadioButton):
                    self._council_first_group.removeButton(w)
                w.deleteLater()
            _row.deleteLater()
        self._council_rows = []
        pool = self._model_pool()
        names = self._council_pool_names()
        chosen = ctx.council_members
        if chosen is None:
            chosen = [n for n in names if n != "base"] or list(names)
        first = ctx.council_first or (chosen[0] if chosen else "")
        self._council_bench_empty.setVisible(len(names) <= 1)
        for n in names:
            row = QHBoxLayout()
            row.setSpacing(8)
            chk = QCheckBox(n)
            chk.setChecked(n in chosen)
            chk.setStyleSheet("QCheckBox{color:#e6edf7; font-size:13px; "
                              "font-weight:600;}")
            row.addWidget(chk)
            _m = pool.get(n) or {}
            if n == "base":
                _bu, _, _bm = self._chat_base_endpoint()
                _lbl, _url = t("council.seat_base") + f"  ·  {_bm}", _bu
            else:
                _lbl, _url = (_m.get("model") or "—"), (_m.get("base_url") or "")
            _host = _council_host(_url)
            _prof = (_m.get("profile") or "").strip()
            m = QLabel(_lbl + (f"  ·  {_prof[:34]}" if _prof else ""))
            m.setStyleSheet("color:#e6edf7; font-size:12px;")
            row.addWidget(m, 1)
            h = QLabel(_host or "?")
            h.setToolTip(t("council.seat_host_tip", url=_url or "?"))
            h.setStyleSheet("color:#e6edf7; font-size:12px; "
                            "font-family:monospace;")
            row.addWidget(h)
            rad = QRadioButton(t("council.first_mark"))
            rad.setToolTip(t("council.first_tip"))
            rad.setChecked(n == first)
            rad.setStyleSheet("QRadioButton{color:#e6edf7; font-size:12px;}"
                              "QRadioButton:checked{color:" +
                              self._COUNCIL_COL + ";}")
            self._council_first_group.addButton(rad)
            row.addWidget(rad)
            rad.toggled.connect(
                lambda on, c=chk: (c.setChecked(True) if on else None))
            chk.toggled.connect(self._council_seat_toggled)
            rad.toggled.connect(self._council_store_bench)
            lay.addLayout(row)
            self._council_rows.append((n, chk, rad))
        self._council_ctx_chk.setChecked(bool(ctx.council_context))

    def _council_bench(self):
        members = [n for n, chk, _ in self._council_rows if chk.isChecked()]
        first = next((n for n, _, rad in self._council_rows
                      if rad.isChecked() and n in members), "")
        return members, (first or (members[0] if members else ""))

    def _council_seat_toggled(self, *_a):
        self._council_keep_first_seated()
        self._council_store_bench()

    def _council_keep_first_seated(self):
        if getattr(self, "_council_fixing", False):
            return
        rows = self._council_rows
        seated = [(n, chk, rad) for n, chk, rad in rows if chk.isChecked()]
        if not seated or any(rad.isChecked() for _n, _c, rad in seated):
            return
        self._council_fixing = True
        try:
            seated[0][2].setChecked(True)
        finally:
            self._council_fixing = False

    def _council_store_bench(self, *_a):
        if getattr(self, "_council_building", False):
            return
        ctx = self._chat_ctxs.get(self._active_tab_id)
        if ctx is None:
            return
        members, first = self._council_bench()
        ctx.council_members = members
        ctx.council_first = first
        ctx.council_context = self._council_ctx_chk.isChecked()

    def _council_welcome(self):
        self.council_view.setHtml(
            "<div style='color:#7c8aa3; font-size:13px; line-height:1.7; "
            "padding:14px 6px;'>"
            f"<div style='color:{self._COUNCIL_COL}; font-weight:700; "
            f"font-size:14px; margin-bottom:6px;'>{_esc(t('council.how_head'))}</div>"
            f"{_esc(t('council.how_1'))}<br>{_esc(t('council.how_2'))}<br>"
            f"{_esc(t('council.how_3'))}<br>{_esc(t('council.how_4'))}"
            f"<div style='margin-top:12px; color:#8fa0b8;'>"
            f"{_esc(t('council.how_ask'))}</div></div>")

    def _council_view_placeholder(self, on_council):
        if hasattr(self, "entry"):
            self.entry.setPlaceholderText(
                t("council.input_ph") if on_council
                else t("gui.chat_placeholder"))

    def _sync_input_placeholder(self):
        stack = getattr(self, "_chat_stack", None)
        idx = stack.currentIndex() if stack is not None else 0
        if idx == getattr(self, "_council_page_idx", None):
            self._council_view_placeholder(True)
        else:
            self._code_view_placeholder(idx == getattr(self, "_code_page_idx",
                                                       None))

    def _machine(self, tab_id):
        m = self._machines.get(tab_id)
        if m is None:
            from machine_view import Machine
            m = self._machines[tab_id] = Machine()
        return m

    def _on_machine_event(self, tab_id, ev):
        if not tab_id:
            return
        m = self._machine(tab_id)
        m.event(ev)
        if tab_id == self._active_tab_id and self.machine_view.isVisible():
            if self.machine_view.machine() is not m:
                self.machine_view.set_machine(m)
            else:
                self.machine_view.poke()

    def _machine_task_state(self, tab_id, running):
        self._on_machine_event(tab_id, {"kind": "turn" if running else "end"})

    def _machine_emit(self, kind, **kw):
        tab = getattr(self._tls, "tab_id", None) or self._active_tab_id
        self.signals.machine_event.emit(tab or "", dict(kind=kind, **kw))

    def _toggle_machine(self):
        self._machine_on = not getattr(self, "_machine_on", False)
        if self._machine_on:
            self.machine_view.set_machine(self._machine(self._active_tab_id))
        self._curtain_set(self.machine_view, self._machine_on)
        try:
            QSettings("hoplita", "hoplita").setValue("machine/visible",
                                                    "1" if self._machine_on else "0")
        except Exception:
            pass
        self._render_chat_tabs()

    def _curtain_add(self, widget, key, default_w):
        try:
            w = int(QSettings("hoplita", "hoplita").value(key, default_w) or default_w)
        except Exception:
            w = default_w
        widget.setParent(self._curtain_host)
        widget.hide()
        grip = CurtainGrip(widget, lambda nw, wd=widget: self._curtain_resize(wd, nw),
                           lambda wd=widget: self._curtain_save(wd))
        self._curtains[widget] = {"w": max(240, w), "key": key, "grip": grip,
                                  "anim": None, "open": False}

    def _curtains_place(self):
        host = getattr(self, "_curtain_host", None)
        views = getattr(self, "_chat_views", None)
        if host is None or views is None:
            return
        top = views.mapTo(host, QPoint(0, 0))
        x = top.x() + views.width()
        h = views.height()
        for wd in (self.machine_view, getattr(self, "dashboard", None),
                   getattr(self, "turn_view", None)):
            rec = self._curtains.get(wd) if wd is not None else None
            if rec is None or not rec["open"]:
                continue
            x -= rec["w"]
            if rec["anim"] is not None:
                wd.resize(rec["w"], h)
            else:
                wd.setGeometry(x, top.y(), rec["w"], h)
            rec["grip"].setGeometry(0, 0, 6, h)
            rec["grip"].raise_()
            wd.raise_()

    def _curtain_set(self, widget, on):
        rec = self._curtains.get(widget)
        if rec is None:
            widget.setVisible(bool(on))
            return
        on = bool(on)
        if rec["open"] == on and widget.isVisible() == on and rec["anim"] is None:
            return
        rec["open"] = on
        if rec["anim"] is not None:
            rec["anim"].stop()
            rec["anim"] = None
        self._curtains_place()
        g = widget.geometry()
        out = QPoint(g.x() + g.width(), g.y())
        home = QPoint(g.x(), g.y())
        anim = QPropertyAnimation(widget, b"pos", self)
        anim.setDuration(220)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        if on:
            widget.move(out)
            widget.show()
            widget.raise_()
            anim.setStartValue(out)
            anim.setEndValue(home)
        else:
            anim.setStartValue(home)
            anim.setEndValue(out)
        anim.finished.connect(lambda wd=widget: self._curtain_done(wd))
        rec["anim"] = anim
        anim.start()

    def _curtain_done(self, widget):
        rec = self._curtains.get(widget)
        if rec is None:
            return
        rec["anim"] = None
        if not rec["open"]:
            widget.hide()
        self._curtains_place()

    def _curtain_resize(self, widget, new_w):
        rec = self._curtains.get(widget)
        if rec is None:
            return
        room = max(300, self._curtain_host.width() - 300)
        least = max(240, widget.minimumSizeHint().width())
        rec["w"] = max(least, min(int(new_w), room))
        self._curtains_place()

    def _curtain_save(self, widget):
        rec = self._curtains.get(widget)
        if rec is None:
            return
        try:
            QSettings("hoplita", "hoplita").setValue(rec["key"], int(rec["w"]))
        except Exception:
            pass
        if widget is getattr(self, "dashboard", None):
            self._canvas_w = rec["w"]

    def _toggle_overview(self):
        idx = getattr(self, "_overview_page_idx", None)
        if getattr(self, "_chat_stack", None) is None or idx is None:
            return
        if self._chat_stack.currentIndex() == idx:
            self._chat_stack.setCurrentIndex(0)
            self._ovw_timer.stop()
        else:
            self._chat_stack.setCurrentIndex(idx)
            self._ovw_fetch_jobs()
            self._overview_tick()
            self.tabs_overview.select(self._active_tab_id)
            self._ovw_timer.start()
        self._render_chat_tabs()

    def _overview_open(self, tab_id):
        self._chat_stack.setCurrentIndex(0)
        self._ovw_timer.stop()
        if tab_id != self._active_tab_id:
            self._switch_chat_tab(tab_id)
        self._render_chat_tabs()

    def _overview_stop_turn(self, tab_id):
        c = self._chat_ctxs.get(tab_id)
        if c is None:
            return
        self._cancel_pending_ack(tab_id)
        try:
            c.stop_requested.set()
        except Exception:
            pass
        tid = getattr(c, "turn_id", None)
        if tid:
            self._bg(lambda u=self._tab_url(c): requests.post(f"{u}/agent/stop",
                                                            json={"turn_id": tid}, timeout=15),
                     lambda _r: None)

    def _overview_stop_job(self, tab_id, job_id):
        c = self._chat_ctxs.get(tab_id)
        url = self._tab_url(c) if c is not None else self._mcp_url
        self._bg(lambda: requests.post(f"{url}/agent/background/stop",
                                       json={"job_id": job_id}, timeout=10),
                 lambda _r: None)

    @staticmethod
    def _tree_prefixes(lines):
        n = len(lines)
        for i, ln in enumerate(lines):
            d = int(ln.get("depth", 1))
            if d == 0:
                ln["prefix"] = ""
                continue
            later = lines[i + 1:]
            last1 = not any(int(x.get("depth", 1)) == 1 for x in later)
            if d == 1:
                ln["prefix"] = "└─ " if last1 else "├─ "
                continue
            last2 = not (i + 1 < n and int(lines[i + 1].get("depth", 1)) == 2)
            under_last = not any(int(x.get("depth", 1)) == 1 for x in later)
            ln["prefix"] = ("   " if under_last else "│  ") + ("└─ " if last2 else "├─ ")
        return lines

    def _tab_tree(self, tab, c, working, since, now):
        from tabs_overview import ago as tabs_overview_ago
        tid = tab["id"]
        lines = []
        if working and c is not None:
            it = getattr(c, "turn_iter", (0, 0)) or (0, 0)
            lines.append({"depth": 0, "kind": "turn", "key": "turn", "glyph": "spin",
                          "color": "#38bdf8",
                          "text": t("ovw.turn_line", i=it[0], n=it[1],
                                    ago=tabs_overview_ago(since or 0)),
                          "cancel": ["turn", tid, ""], "tip": t("ovw.stop_turn_tip")})
            steps = [s for s in (getattr(c, "turn_steps", []) or []) if isinstance(s, dict)]
            if len(steps) > 8:
                lines.append({"depth": 1, "kind": "earlier", "key": "earlier", "glyph": "·",
                              "color": "#64748b", "text": t("ovw.earlier", n=len(steps) - 8)})
                steps = steps[-8:]
            for si, s in enumerate(steps):
                done = bool(s.get("done")) or "result" in s
                okk = s.get("ok", True)
                detail = str(s.get("detail") or "")
                head = f"{s.get('action', '')}({detail[:70]}{'…' if len(detail) > 70 else ''})"
                if done:
                    dur = s.get("duration")
                    tail = f"  {float(dur):.1f}s" if dur is not None else ""
                else:
                    t0 = s.get("t0")
                    tail = f"  {tabs_overview_ago(now - float(t0))}…" if t0 else ""
                tip = f"{s.get('action', '')}({detail})"
                if s.get("result"):
                    tip += "\n→ " + str(s["result"])[:300]
                lines.append({"depth": 1, "kind": "step",
                              "key": f"step/{s.get('step') or s.get('t0') or si}",
                              "glyph": ("✓" if okk else "✗") if done else "spin",
                              "color": ("#e6edf7" if okk else "#fca5a5") if done else "#38bdf8",
                              "text": head + tail, "tip": tip})
            intent = [i for i in (getattr(c, "intent", []) or []) if isinstance(i, dict)]
            if intent:
                done_n = sum(1 for i in intent if str(i.get("status", "")).lower() == "done")
                cur = (next((i for i in intent if str(i.get("status", "")).lower() == "doing"), None)
                       or next((i for i in intent if str(i.get("status", "")).lower()
                                in ("", "pending")), None))
                lines.append({"depth": 1, "kind": "plan", "key": "plan",
                              "glyph": "◐" if cur else "✓", "color": "#c4b5fd",
                              "text": t("ovw.plan_line", done=done_n, n=len(intent),
                                        goal=str((cur or intent[-1]).get("goal", ""))[:70]),
                              "tip": "\n".join(f"[{i.get('status', '')}] {i.get('goal', '')}"
                                               for i in intent)})
        agents = [a for a in self._agents_cards_for(tab) if isinstance(a, dict)]
        if agents:
            _order = {"running": 0, "pending": 1, "failed": 2, "done": 3, "cancelled": 4}
            agents.sort(key=lambda a: _order.get(str(a.get("status") or "pending"), 1))
            _done = sum(1 for a in agents if a.get("status") == "done")
            _run = sum(1 for a in agents if a.get("status") == "running")
            _bad = sum(1 for a in agents if a.get("status") == "failed")
            _head = [t("ovw.agents_head", n=len(agents)),
                     t("ovw.agents_done", done=_done, n=len(agents))]
            if _run:
                _head.append(t("ovw.agents_running", n=_run))
            if _bad:
                _head.append(t("ovw.agents_failed", n=_bad))
            lines.append({"depth": 1, "kind": "agents", "key": "agents", "glyph": "▸",
                          "color": "#c4b5fd", "text": " · ".join(_head)})
        for a in agents:
            st = str(a.get("status") or "pending")
            o = a.get("origin") if a.get("origin") in ("out", "in") else "local"
            where = "⌂" if o == "local" else (("→ " if o == "out" else "← ")
                                             + str(a.get("peer") or t("ovw.remote")))
            parts = [f"{a.get('id', '')} {a.get('role') or 'general'}",
                     "🧠 " + str(a.get("model") or "base"), where]
            if a.get("tools") is not None:
                parts.append(t("ovw.agent_tools", n=int(a.get("tools") or 0)))
            if st not in ("running", "pending"):
                parts.append(st)
            lines.append({"depth": 1, "kind": "agent", "key": f"agent/{a.get('id', '')}",
                          "glyph": {"running": "spin", "done": "✓", "failed": "✗",
                                    "cancelled": "⊘"}.get(st, "○"),
                          "color": {"running": "#f5b800", "done": "#86efac",
                                    "failed": "#fca5a5"}.get(st, "#94a3b8"),
                          "text": " · ".join(parts), "tip": str(a.get("goal") or "")})
            _steps = [x for x in (a.get("steps") or []) if isinstance(x, dict)]
            for si, s in enumerate(_steps[-3:], start=max(0, len(_steps) - 3)):
                done = bool(s.get("done"))
                dur = s.get("duration")
                tail = (f"  {float(dur):.1f}s" if done and dur is not None
                        else (f"  {tabs_overview_ago(now - float(s['t0']))}…"
                              if not done and s.get("t0") else ""))
                lines.append({"depth": 2, "kind": "agent_step",
                              "key": f"agent/{a.get('id', '')}/{s.get('t0') or si}",
                              "glyph": ("✓" if s.get("ok", True) else "✗") if done else "spin",
                              "color": "#cbd5e1" if done else "#38bdf8",
                              "text": f"{s.get('tool', '')}({str(s.get('args') or '')[:50]}){tail}",
                              "tip": f"{s.get('tool', '')}({s.get('args', '')})"})
            its = [i for i in (a.get("intent") or []) if isinstance(i, dict)]
            if its and st == "running":
                cur = (next((i for i in its if str(i.get("status", "")).lower() == "doing"), None)
                       or next((i for i in its if str(i.get("status", "")).lower()
                                in ("", "pending")), None))
                if cur:
                    done_n = sum(1 for i in its if str(i.get("status", "")).lower() == "done")
                    lines.append({"depth": 2, "kind": "agent_plan",
                                  "key": f"agent/{a.get('id', '')}/plan", "glyph": "◐",
                                  "color": "#c4b5fd",
                                  "text": t("ovw.plan_line", done=done_n, n=len(its),
                                            goal=str(cur.get("goal", ""))[:60])})
        for j in (getattr(c, "bg_jobs", []) or []) if c is not None else []:
            if not isinstance(j, dict):
                continue
            if j.get("kind") == "watch":
                text = t("ovw.job_watch", label=j.get("label", ""), s=j.get("every_s", 0),
                         fires=j.get("fires", 0), last=str(j.get("last_preview") or "")[:60])
                glyph = "👁"
            else:
                text = t("ovw.job_run", label=j.get("label", ""), tool=j.get("tool", ""),
                         ago=tabs_overview_ago(j.get("age_s") or 0))
                glyph = "⚙"
            lines.append({"depth": 1, "kind": "job", "key": f"job/{j.get('id', '')}",
                          "glyph": glyph, "color": "#f5b800",
                          "text": text, "tip": t("ovw.stop_job_tip"),
                          "cancel": ["job", tid, str(j.get("id", ""))]})
        return self._tree_prefixes(lines)

    def _overview_tick(self):
        if self._chat_stack.currentIndex() != getattr(self, "_overview_page_idx", -1):
            self._ovw_timer.stop()
            return
        self._ovw_n = getattr(self, "_ovw_n", 0) + 1
        if self._ovw_n % 15 == 0:
            self._ovw_fetch_jobs()
        self.tabs_overview.update_rows(self._tabs_overview_rows())

    def _ovw_fetch_jobs(self):
        def work():
            return self._mcp_call_json("list_cron", {}, timeout=10)

        def done(res):
            jobs = (res or {}).get("jobs") if isinstance(res, dict) else None
            if isinstance(jobs, list):
                self._ovw_jobs = {j.get("id"): j for j in jobs if isinstance(j, dict)}
        self._bg(work, done)

    @staticmethod
    def _ovw_when(iso):
        try:
            d = datetime.fromisoformat(iso)
        except Exception:
            return ""
        return d.strftime("%H:%M") if d.date() == datetime.now().date() \
            else d.strftime("%d/%m %H:%M")

    def _tabs_overview_rows(self):
        now = time.time()
        rows = []
        for tab in self._chat_tabs:
            tid = tab["id"]
            c = self._chat_ctxs.get(tid)
            ended = bool(tab.get("ended"))
            waiting = getattr(c, "pending_ack", None) is not None
            working = self._tab_working(tid)
            state = ("ended" if ended else "waiting" if waiting
                     else "working" if working else "idle")
            if working:
                since = now - self._enchant_t0[tid] if tid in self._enchant_t0 else None
            else:
                la = getattr(c, "last_active", 0) or 0
                since = now - la if la else None
            doing = ""
            if working and c is not None:
                cur = next((i for i in getattr(c, "intent", []) or []
                            if str(i.get("status", "")).lower() == "doing"), None)
                if cur:
                    doing = str(cur.get("goal", ""))
                elif getattr(c, "turn_steps", None):
                    doing = str(c.turn_steps[-1].get("action", ""))
                else:
                    doing = t("ovw.thinking")
            last = ""
            if c is not None:
                last = next((m.get("content") for m in reversed(c.history or [])
                             if m.get("role") == "assistant"
                             and isinstance(m.get("content"), str)
                             and m["content"].strip()), "") or ""
            jid = getattr(c, "job_id", "") if c is not None else ""
            job = self._ovw_jobs.get(jid) or {}
            foot = []
            if c is not None:
                if getattr(c, "model_override", ""):
                    foot.append("🧠 " + c.model_override)
                if getattr(c, "msg_queue", None):
                    foot.append(t("ovw.queued", n=len(c.msg_queue)))
                _pin = getattr(c, "pinned_device", None)
                if _pin:
                    foot.append("📍" + str(_pin.get("name", "?")))
                if getattr(c, "local_only", False):
                    foot.append("🔒")
                if getattr(c, "blocked_tools", None):
                    foot.append("🛡")
                if getattr(c, "bg_live", 0):
                    foot.append("👁 " + str(c.bg_live))
            try:
                tree = self._tab_tree(tab, c, working, since, now)
            except Exception as _e:
                print(f"[OVERVIEW] tree of {tid}: {_e}", file=sys.stderr)
                tree = []
            rows.append({
                "id": tid, "title": tab.get("title", ""),
                "color": self._chat_tab_color(tab.get("kind", "local")),
                "active": tid == self._active_tab_id, "state": state,
                "since": since,
                "job": getattr(c, "job_name", "") if c is not None else "",
                "next_run": self._ovw_when(job.get("next") or "") if job else "",
                "goal": getattr(c, "goal", "") if c is not None else "",
                "doing": doing, "last": last, "foot": foot,
                "agents": self._agents_cards_for(tab), "tree": tree})
        return rows

    def _toggle_council_view(self):
        idx = getattr(self, "_council_page_idx", None)
        if getattr(self, "_chat_stack", None) is None or idx is None:
            return
        if self._chat_stack.currentIndex() == idx:
            self._chat_stack.setCurrentIndex(0)
        else:
            ctx = self._chat_ctxs.get(self._active_tab_id)
            if ctx is not None:
                self._council_building = True
                try:
                    self._council_build_bench(ctx)
                finally:
                    self._council_building = False
                if ctx.council_html:
                    self.council_view.setHtml(ctx.council_html)
                else:
                    self._council_welcome()
                if self.council_web is not None:
                    self.council_web.set_bench(self._council_web_bench(ctx))
                    self.council_web.load(ctx.council_events)
                self._council_show_gate(ctx.council_ack)
            self._chat_stack.setCurrentIndex(idx)
        _on = self._chat_stack.currentIndex() == idx
        self._council_view_placeholder(_on)
        self._render_chat_tabs()

    _COUNCIL_PHASE = {"answer": ("council.ph_answer", "#38bdf8"),
                      "critique": ("council.ph_critique", "#f97316"),
                      "reform": ("council.ph_reform", "#a855f7"),
                      "vote": ("council.ph_vote", "#22c55e")}

    def _paint_council_event(self, ev):
        self.signals.council_event.emit(
            getattr(self._tls, "tab_id", None) or self._active_tab_id,
            dict(ev or {}))

    def _council_append(self, tab_id, html):
        ctx = self._chat_ctxs.get(tab_id)
        if ctx is None:
            return
        ctx.council_html = (ctx.council_html or "") + html
        if tab_id != self._active_tab_id:
            return
        idx = getattr(self, "_council_page_idx", None)
        if idx is None or self._chat_stack.currentIndex() != idx:
            return
        self.council_view.setHtml(ctx.council_html)
        _sb = self.council_view.verticalScrollBar()
        _sb.setValue(_sb.maximum())

    def _council_block(self, who, phase, body, colour=None, badge=""):
        key, col = self._COUNCIL_PHASE.get(phase, ("", "#7c8aa3"))
        col = colour or col
        try:
            _body = self._render_markdown(str(body or ""), "#dbe4f0")
        except Exception:
            _body = _esc(body).replace("\n", "<br>")
        return (
            f"<div style='margin:10px 0 0; border-left:3px solid {col}; "
            f"padding:2px 0 2px 10px;'>"
            f"<div style='font-size:12px; color:{col}; font-weight:700; "
            f"letter-spacing:.3px;'>{_esc(who)}"
            f"<span style='color:#7c8aa3; font-weight:400;'> · "
            f"{_esc(t(key) if key else phase)}</span>{badge}</div>"
            f"<div style='color:#dbe4f0; font-size:13px; line-height:1.55; "
            f"margin-top:3px;'>{_body}</div></div>")

    def _on_council_event(self, tab_id, ev):
        self._council_record(tab_id, ev)
        et = ev.get("type")
        if et == "convene":
            members = ev.get("members") or []
            _where = ev.get("where") or {}
            _rows = ""
            for m in members:
                w = _where.get(m) or {}
                _host = _council_host(w.get("base_url"))
                _rows += (
                    f"<div style='color:#e6edf7; font-size:12px; "
                    f"margin-left:2px;'>· <b style='color:#93a1b5;'>{_esc(m)}</b>"
                    f"{' ← ' + _esc(t('council.first_mark')) if m == ev.get('first') else ''}"
                    f" &nbsp;{_esc(w.get('model') or '?')}"
                    f" &nbsp;<span style='color:#5b6b82;'>{_esc(_host or '?')}"
                    f"</span></div>")
            _topic = str(ev.get("topic") or "").strip()
            _tq = (f"<div style='margin:2px 0 8px; padding:8px 11px; "
                   f"border-radius:8px; background:#131b2c; "
                   f"border-left:3px solid {self._COUNCIL_COL};'>"
                   f"<div style='color:#e6edf7; font-size:12px; "
                   f"font-weight:700; letter-spacing:.4px;'>"
                   f"{_esc(t('council.the_matter'))}</div>"
                   f"<div style='color:#e6edf7; font-size:13px; "
                   f"margin-top:2px; white-space:pre-wrap;'>{_esc(_topic)}"
                   f"</div></div>") if _topic else ""
            self._council_append(tab_id, (
                f"<div style='color:{self._COUNCIL_COL}; font-size:13px; "
                f"font-weight:700; padding-bottom:4px;'>"
                f"{_esc(t('council.convened', n=len(members), first=ev.get('first', '?'), who=', '.join(members)))}"
                f"</div>{_rows}"
                f"<div style='border-bottom:1px solid #26324a; "
                f"margin:6px 0 8px;'></div>{_tq}"))
        elif et == "briefing":
            _b = str(ev.get("text") or "").strip()
            if _b:
                self._council_append(tab_id, (
                    f"<div style='margin:2px 0 6px; padding:7px 10px; "
                    f"border-radius:8px; background:#101728; "
                    f"border:1px dashed #2b3550;'>"
                    f"<div style='color:#e6edf7; font-size:12px; "
                    f"font-weight:700; letter-spacing:.4px;'>"
                    f"{_esc(t('council.briefing_head', n=len(_b)))}</div>"
                    f"<div style='color:#e6edf7; font-size:12px; "
                    f"margin-top:3px; white-space:pre-wrap; "
                    f"font-family:monospace;'>{_esc(_b[:1800])}"
                    f"{'…' if len(_b) > 1800 else ''}</div></div>"))
            else:
                self._council_append(tab_id, (
                    f"<div style='color:#e6edf7; font-size:12px; "
                    f"margin:2px 0 6px;'>{_esc(t('council.briefing_none'))}"
                    f"</div>"))
        elif et == "step":
            _col = "#ef4444" if ev.get("kind") == "error" else "#5b6b82"
            self._council_append(tab_id, (
                f"<div style='color:{_col}; font-size:12px; "
                f"font-family:monospace; margin:1px 0 1px 4px; "
                f"white-space:pre-wrap;'>{_esc(str(ev.get('text', ''))[:400])}"
                f"</div>"))
        elif et == "speak_start":
            key, col = self._COUNCIL_PHASE.get(ev.get("phase"), ("", "#7c8aa3"))
            self._council_append(tab_id, (
                f"<div style='color:#e6edf7; font-size:12px; margin-top:8px;'>"
                f"▸ {_esc(ev.get('member', '?'))} · "
                f"{_esc(t(key) if key else ev.get('phase', ''))}…</div>"))
        elif et == "speak_done":
            _silent = not ev.get("produced", True)
            self._council_append(tab_id, self._council_block(
                ev.get("member", "?"), ev.get("phase", ""),
                ev.get("text", ""), colour="#ef4444" if _silent else None,
                badge=("<span style='color:#ef4444; font-weight:700;'>  "
                       f"{_esc(t('council.produced_nothing'))}</span>"
                       if _silent else "")))
        elif et == "speak_failed":
            self._council_append(tab_id, self._council_block(
                ev.get("member", "?"), ev.get("phase", ""),
                ev.get("text") or t("council.member_failed",
                                    member=ev.get("member", "?"),
                                    e=ev.get("error", "")), colour="#ef4444"))
        elif et == "vote":
            placet = ev.get("verdict") == "placet"
            unread = not ev.get("verdict")
            col = "#22c55e" if placet else ("#f59e0b" if unread else "#ef4444")
            mark = ("✓ " + t("council.vote_placet")) if placet else (
                ("? " + t("council.vote_unreadable")) if unread
                else ("✗ " + t("council.vote_no")))
            self._council_append(tab_id, self._council_block(
                ev.get("member", "?"), "vote", ev.get("why", ""), colour=col,
                badge=(f"<span style='color:{col}; font-weight:700;'>"
                       f"  {_esc(mark)}</span>")))
        elif et == "round_done":
            placet = bool(ev.get("placet"))
            col = "#22c55e" if placet else "#f59e0b"
            self._council_append(tab_id, (
                f"<div style='margin:10px 0 2px; padding:6px 10px; "
                f"border-radius:8px; background:{self._hex_rgba(col, 0.10)}; "
                f"border:1px solid {col}; color:{col}; font-size:12px; "
                f"font-weight:700;'>"
                f"{_esc(t('council.round_placet', n=ev.get('round', 1)) if placet else t('council.round_split', n=ev.get('round', 1), d=len(ev.get('dissent') or [])))}"
                f"</div>"))
        elif et == "note":
            self._council_append(tab_id, (
                f"<div style='color:#e6edf7; font-size:12px; margin-top:8px;'>"
                f"{_esc(t(ev.get('key', ''), **(ev.get('args') or {})))}</div>"))
        elif et == "cancelled":
            self._council_append(tab_id, (
                "<div style='color:#ef4444; font-size:12px; margin-top:8px;'>"
                f"{_esc(t('council.cancelled'))}</div>"))
        elif et == "clear":
            ctx = self._chat_ctxs.get(tab_id)
            if ctx is not None:
                ctx.council_html = ""
            if tab_id == self._active_tab_id:
                self._council_welcome()

    def _council_web_bench(self, ctx):
        pool = self._model_pool()
        seats = []
        for n, chk, _rad in self._council_rows:
            _m = pool.get(n) or {}
            if n == "base":
                _url, _, _model = self._chat_base_endpoint()
                _label = t("council.seat_base") + " · " + (_model or "")
            else:
                _url, _model, _label = (_m.get("base_url") or ""), (_m.get("model") or ""), ""
            seats.append({"name": n, "model": _model, "label": _label,
                          "url": _url, "seated": chk.isChecked()})
        _members, first = self._council_bench()
        return {"seats": seats, "first": first,
                "context": bool(ctx.council_context)}

    def _council_web_bench_changed(self, b):
        ctx = self._chat_ctxs.get(self._active_tab_id)
        if ctx is None:
            return
        ctx.council_members = [str(m) for m in (b.get("members") or [])]
        ctx.council_first = str(b.get("first") or "")
        ctx.council_context = bool(b.get("context", True))
        self._council_building = True
        try:
            self._council_build_bench(ctx)
        finally:
            self._council_building = False

    def _council_record(self, tab_id, ev):
        ctx = self._chat_ctxs.get(tab_id)
        if ctx is None:
            return
        ev = dict(ev)
        ev.setdefault("_t", time.time())
        if ev.get("type") == "note" and ev.get("key"):
            ev["text"] = t(ev["key"], **(ev.get("args") or {}))
        if ev.get("type") in ("clear", "convene"):
            ctx.council_events = []
        if ev.get("type") != "clear":
            ctx.council_events.append(ev)
        web = getattr(self, "council_web", None)
        if web is None or tab_id != self._active_tab_id:
            return
        idx = getattr(self, "_council_page_idx", None)
        if idx is None or self._chat_stack.currentIndex() != idx:
            return
        if ev.get("type") == "clear":
            web.load([])
        else:
            web.push(ev)

    def _on_council_ack(self, box):
        tab = (box or {}).get("tab") or self._active_tab_id
        ctx = self._chat_ctxs.get(tab)
        if ctx is None:
            return
        ctx.council_ack = box
        if tab == self._active_tab_id:
            idx = getattr(self, "_council_page_idx", None)
            if idx is not None and self._chat_stack.currentIndex() != idx:
                self._toggle_council_view()
            self._council_show_gate(box)
        else:
            self.add_message(t("council.ack_waiting_tab"), "system")
        self._render_chat_tabs()

    def _council_show_gate(self, box):
        bar = getattr(self, "_council_gate_bar", None)
        if bar is None:
            return
        if not box:
            bar.setVisible(False)
            return
        d = box.get("dissent") or []
        who = ", ".join(str(x.get("member", "?")) for x in d) or "—"
        self._council_gate_lbl.setText(
            t("council.gate_question", n=box.get("round", 1), d=len(d), who=who))
        bar.setVisible(True)

    def _council_gate_answer(self, decision):
        ctx = self._chat_ctxs.get(self._active_tab_id)
        box = getattr(ctx, "council_ack", None) if ctx else None
        if not box:
            self._council_show_gate(None)
            return
        ctx.council_ack = None
        self._council_show_gate(None)
        self._render_chat_tabs()
        ack_id = box.get("ack_id")

        def work():
            try:
                r = requests.post(f"{self._tab_url()}/agent/council_ack",
                                  json={"ack_id": ack_id, "decision": decision},
                                  timeout=15)
                return r.json()
            except Exception as e:
                return {"accepted": False, "error": str(e)}

        def done(res):
            if not (res or {}).get("accepted"):
                self.add_message(t("council.gate_lost",
                                   why=str((res or {}).get("error", ""))[:160]),
                                 "error")
        self._bg(work, done)

    def _cancel_council_ack(self, tab_id):
        ctx = self._chat_ctxs.get(tab_id)
        if ctx is None or ctx.council_ack is None:
            return
        ctx.council_ack = None
        if tab_id == self._active_tab_id:
            self._council_show_gate(None)
        self._render_chat_tabs()

    def _handle_council_convene(self, topic, already_in_history=False):
        tab = getattr(self._tls, "tab_id", None) or self._active_tab_id
        ctx = self._chat_ctxs.get(tab)
        _chairs = self._council_pool_names()
        members = [n for n in (getattr(ctx, "council_members", None) or [])
                   if n in _chairs]
        first = getattr(ctx, "council_first", "") or ""
        if not members:
            self.thread_msg(t("council.err_no_members"), "error")
            return
        context, _hist = "", None
        if getattr(ctx, "council_context", True):
            context = self._council_context_text(ctx)
            _hist = self._council_messages(ctx, topic, already_in_history)
        self.signals.council_event.emit(tab, {"type": "clear"})
        self.signals.council_event.emit(tab, {"type": "briefing",
                                              "text": context})
        self.thread_msg(t("council.convening", n=len(members), first=first,
                          topic=topic[:60]), "system")
        self._task_begin()
        try:
            base_url, api_key, model = self._chat_base_endpoint()
            _payload = {"council": {"topic": topic, "members": members,
                                    "first": first, "context": context},
                        "endpoint": {"base_url": base_url, "api_key": api_key,
                                     "model": model}}
            if _hist is not None:
                _payload["messages"] = _hist
            final = self._stream_turn(_payload, model=model)
            if not already_in_history:
                self.conversation_history.append({"role": "user",
                                                  "content": topic})
            _rec = getattr(self._turn_ctx(), "turn_record", "") or ""
            self.conversation_history.append(
                {"role": "assistant",
                 "content": (final or "") + (("\n\n" + _rec) if _rec else "")})
            self.thread_msg(final, "assistant")
            self.signals.switch_to_chat.emit(self._emitting_tab())
        except Exception as e:
            self.thread_msg(t("council.error", e=e), "error")
            print(f"[COUNCIL] error: {e}", file=sys.stderr)
        finally:
            self._cancel_council_ack(tab)
            self._task_end()

    def _council_context_text(self, ctx):
        try:
            from hoplita_council import strip_record as _strip
        except Exception:
            def _strip(x):
                return x
        hist = list(getattr(ctx, "history", None) or self.conversation_history)
        out = []
        for m in hist[-8:]:
            role = m.get("role")
            if role not in ("user", "assistant"):
                continue
            txt = m.get("content")
            if not isinstance(txt, str) or not txt.strip():
                continue
            txt = _strip(txt) if role == "assistant" else txt
            if not txt.strip():
                continue
            who = t("label.user") if role == "user" else "HOPLITA"
            out.append(f"{who}: {txt.strip()[:1200]}")
        return "\n\n".join(out)

    def _council_messages(self, ctx, topic, already_in_history):
        try:
            from hoplita_council import strip_record as _strip
        except Exception:
            def _strip(x):
                return x
        hist = list(getattr(ctx, "history", None) or self.conversation_history)
        out = []
        for m in hist:
            role = m.get("role") if isinstance(m, dict) else None
            if role not in ("user", "assistant"):
                continue
            txt = m.get("content")
            if role == "assistant" and isinstance(txt, str):
                txt = _strip(txt)
            if not txt or (isinstance(txt, str) and not txt.strip()):
                continue
            out.append({"role": role, "content": txt})
        if not already_in_history:
            out.append({"role": "user", "content": topic})
        return out

    def _code_view_placeholder(self, on_code):
        if hasattr(self, "entry"):
            self.entry.setPlaceholderText(
                t("chat.input_ph_code") if on_code else t("gui.chat_placeholder"))

    def _toggle_code_view(self):
        idx = getattr(self, "_code_page_idx", None)
        if getattr(self, "_chat_stack", None) is None or idx is None:
            return
        if self._chat_stack.currentIndex() == idx:
            self._code_stash_current()
            self._chat_stack.setCurrentIndex(0)
        else:
            ctx = self._chat_ctxs.get(self._active_tab_id)
            if ctx is not None:
                self.code_project_edit.setText(ctx.code_project or "")
                self.code_verify.setText(getattr(ctx, "verify_cmd", "") or "")
                self.code_verify_badge.setText("")
                self._code_project = ctx.code_project or ""
                self._code_verify_load_list()
                self._code_run_paint()
                self._code_rebuild_tabs()
                _op = self._code_open_path
                if _op and _op in self._code_bufs:
                    self._code_show_buf(_op)
                else:
                    self._code_open_path = ""
                    self.code_editor.setPlainText("")
                    self._code_dirty = False
                if self._code_project:
                    self._code_refresh()
                else:
                    self._code_welcome()
            self._chat_stack.setCurrentIndex(idx)
        self._code_view_placeholder(self._chat_stack.currentIndex() == idx)
        self._render_chat_tabs()

    def _set_tab_agents(self, subs):
        for _s in (subs or []):
            _tg = str(_s.get("target", "local") or "local")
            if _tg != "local":
                _s.setdefault("origin", "out")
                _s.setdefault("peer", _tg.split(":", 1)[-1])
        ctx = self._turn_ctx()
        prev = {c.get("id"): c for c in (ctx.agents or [])}
        merged = []
        for s in (subs or []):
            s = dict(s)
            p = prev.get(s.get("id"))
            if p:
                for k in ("model", "role", "goal", "deps", "peer", "origin",
                          "steps", "tools"):
                    if not s.get(k) and p.get(k):
                        s[k] = p[k]
                if ("intent" not in s and p.get("intent")
                        and s.get("goal") == p.get("goal")):
                    s["intent"] = p["intent"]
            merged.append(s)
        ctx.agents = merged
        if getattr(self, "agents_panel", None) is not None:
            self.agents_panel.update_state(self._agents_cards_for_active())

    def _set_subtask_field(self, sid, key, value):
        ctx = self._turn_ctx()
        cards = [dict(c) for c in (ctx.agents or [])]
        hit = False
        for c in cards:
            if str(c.get("id")) == str(sid):
                c[key] = value
                hit = True
        if hit:
            ctx.agents = cards
            if getattr(self, "agents_panel", None) is not None:
                self.agents_panel.update_state(self._agents_cards_for_active())

    def _set_subtask_step(self, ev):
        if ev.get("type") == "step":
            self._machine_emit("call", tool=ev.get("tool", ""),
                               args=str(ev.get("args") or "")[:200],
                               agent=str(ev.get("id") or ""))
        else:
            self._machine_emit("result", tool=ev.get("tool", ""),
                               ok=bool(ev.get("ok", True)),
                               duration=float(ev.get("duration_s") or 0),
                               agent=str(ev.get("id") or ""))
        ctx = self._turn_ctx()
        cards = [dict(c) for c in (ctx.agents or [])]
        hit = False
        for c in cards:
            if str(c.get("id")) != str(ev.get("id")):
                continue
            steps = [dict(s) for s in (c.get("steps") or [])]
            if ev.get("type") == "step":
                steps.append({"tool": ev.get("tool", ""), "args": str(ev.get("args") or "")[:120],
                              "t0": time.time(), "done": False,
                              "background": ev.get("background") or ""})
            else:
                for s in reversed(steps):
                    if s.get("tool") == ev.get("tool") and not s.get("done"):
                        s["done"] = True
                        s["duration"] = float(ev.get("duration_s") or 0)
                        s["ok"] = bool(ev.get("ok", True))
                        break
            c["steps"] = steps[-12:]
            hit = True
        if hit:
            ctx.agents = cards
            if getattr(self, "agents_panel", None) is not None:
                self.agents_panel.update_state(self._agents_cards_for_active())

    def _set_subtask_intent(self, sid, items):
        ctx = self._turn_ctx()
        cards = [dict(c) for c in (ctx.agents or [])]
        hit = False
        for c in cards:
            if str(c.get("id")) == str(sid):
                c["intent"] = [dict(it) for it in (items or [])
                               if isinstance(it, dict)]
                hit = True
        if not hit:
            return
        ctx.agents = cards
        if getattr(self, "agents_panel", None) is not None:
            self.agents_panel.update_state(self._agents_cards_for_active())

    def _set_tab_intent(self, items):
        ctx = self._turn_ctx()
        ctx.intent = [dict(it) for it in (items or []) if isinstance(it, dict)]
        tab_id = getattr(self._tls, "tab_id", None) or self._active_tab_id
        self.signals.intent_changed.emit(tab_id or "")

    def _on_intent_changed(self, tab_id):
        if tab_id and tab_id != self._active_tab_id:
            return
        self._refresh_intent_strip()
        ctx = self._chat_ctxs.get(self._active_tab_id)
        if getattr(self, "agents_panel", None) is not None:
            self.agents_panel.set_intent(self._intent_for_active())

    def _intent_for_active(self):
        tab = self._tab_by_id(self._active_tab_id) or {}
        if tab.get("kind") in ("green", "red") and tab.get("peer"):
            return list(getattr(self, "_session_intent", {}).get(
                tab["peer"], []) or [])
        ctx = self._chat_ctxs.get(self._active_tab_id)
        return list(getattr(ctx, "intent", []) or [])

    def _refresh_intent_strip(self):
        strip = getattr(self, "_intent_strip", None)
        if strip is None:
            return
        items = self._intent_for_active()
        if not items:
            strip.setVisible(False)
            return

        def esc(s):
            return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;"))
        parts = []
        for it in items:
            st = it.get("status", "pending")
            g = esc(str(it.get("goal", ""))[:60])
            if st == "done":
                parts.append(f'<span style="color:#7dd3a0">✓ <s>{g}</s></span>')
            elif st == "failed":
                parts.append(f'<span style="color:#f19999">✗ <s>{g}</s></span>')
            elif st == "doing":
                parts.append(f'<span style="color:#f5b800">● {g}</span>')
            elif st == "unfinished":
                parts.append(f'<span style="color:#93a1b5">⊘ <s>{g}</s></span>')
            else:
                parts.append(f'<span style="color:#93a1b5">○ {g}</span>')
        strip.setText("🧭 " + " &nbsp;·&nbsp; ".join(parts))
        strip.setVisible(True)

    def _save_current_session(self):
        act = self._tab_by_id(self._active_tab_id)
        if not act or act.get("kind") != "local":
            self.add_message(t("sess.only_local"), "system")
            return
        ctx = self._chat_ctxs.get(self._active_tab_id)
        if ctx is None:
            return
        default = (ctx.title if ctx.session_uuid else "") \
            or self._session_default_name(ctx) or "Chat"
        name, ok = self._ask_session_name(ctx, default)
        if not ok:
            return
        name = (name or "").strip() or default
        self._persist_tab(self._active_tab_id, name)

    def _session_payload(self, tab_id, name):
        act = self._tab_by_id(tab_id)
        ctx = self._chat_ctxs.get(tab_id)
        if act is None or ctx is None:
            return None
        html = self._chat_view(tab_id).toHtml()
        sess = {
            "uuid": ctx.session_uuid,
            "title": name,
            "kind": act.get("kind", "local"),
            "model": ctx.model_override or "",
            "history": self._flatten_pictures(ctx.history),
            "html": html,
            "sticky": getattr(ctx, "sticky", "") or "",
            "user_fixed_context": ctx.user_fixed_context,
            "pinned_skills": ctx.pinned_skills,
            "tool_usage": {str(k): self._tu_for_save(v)
                           for k, v in sorted(self._tu_store().items())[-60:]
                           if v.get("tab") == tab_id and v.get("done")},
            "code_project": getattr(ctx, "code_project", "") or "",
            "pinned_device": getattr(ctx, "pinned_device", None),
            "sync_id": getattr(ctx, "sync_id", "") or "",
            "verify_cmd": getattr(ctx, "verify_cmd", "") or "",
            "goal": getattr(ctx, "goal", "") or "",
            "job_id": getattr(ctx, "job_id", "") or "",
            "job_name": getattr(ctx, "job_name", "") or "",
            "no_log": bool(getattr(ctx, "no_log", False)),
            "local_only": bool(getattr(ctx, "local_only", False)),
            "qa_override": getattr(ctx, "qa_override", "") or "",
            "blocked_tools": sorted(getattr(ctx, "blocked_tools", ()) or ()),
        }
        return sess

    def _persist_tab(self, tab_id, name=None, quiet=False):
        ctx = self._chat_ctxs.get(tab_id)
        act = self._tab_by_id(tab_id)
        if ctx is None or act is None or act.get("kind") not in ("local", "cron"):
            return
        if getattr(ctx, "no_log", False):
            if not quiet:
                self.add_message(t("log.wont_save"), "system")
            return
        if not name and ctx.session_uuid:
            name = ctx.title
            if self._title_is_glued(name):
                name = ""
        if not name and getattr(ctx, "job_name", ""):
            name = ctx.title
        name = name or self._session_default_name(ctx) or t("chat.tab_local")
        sess = self._session_payload(tab_id, name)
        if sess is None:
            return

        _surl = self._tab_url(ctx)

        def work():
            return self._mcp_call_json("chat_session_save", {"session": sess},
                                       timeout=30, url=_surl)

        def done(res):
            if isinstance(res, dict) and res.get("ok"):
                ctx.session_uuid = res.get("uuid")
                self._job_link_session(tab_id)
                try:
                    self._populate_cron_sessions()
                except Exception:
                    pass
                if tab_id == CRON_TAB_ID and ctx.session_uuid:
                    try:
                        from PyQt6.QtCore import QSettings
                        QSettings("hoplita", "hoplita").setValue(
                            "cron_session_uuid", ctx.session_uuid)
                    except Exception:
                        pass
                ctx.title = name
                act["title"] = name
                self._render_chat_tabs()
                if not quiet:
                    self.add_message(t("sess.saved", title=name), "system")
            elif not quiet:
                err = res if isinstance(res, str) else (res or {}).get("error", "?")
                self.add_message(t("sess.save_fail", err=err), "error")

        self._bg(work, done)

    @staticmethod
    def _title_is_glued(name):
        n = (name or "").strip()
        if not n:
            return False
        import fed_agent as _fedagent_mod
        if n.startswith(("[",) + _fedagent_mod._INJECTED_HEADS
                        + _fedagent_mod.HARNESS_TAGS):
            return True
        try:
            head = t("prompts.today", date="\x00").split("\x00")[0]
            head = re.sub(r"^\[[^\]]*\]\s*", "", head).strip()
            return bool(head) and n.startswith(head)
        except Exception:
            return False

    def _session_default_name(self, ctx):
        import fed_agent as _fedagent_mod
        for m in (ctx.history or []):
            if m.get("role") != "user":
                continue
            txt = (_fedagent_mod.user_said(m) or "").strip().replace("\n", " ")
            if txt.startswith(BG_EVENT_PREFIX):
                continue
            if txt:
                return txt[:48]
        return ""

    def _ask_session_name(self, ctx, default):
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                     QLineEdit, QPushButton, QLabel)
        dlg = QDialog(self)
        dlg.setWindowTitle(t("sess.name_title"))
        dlg.resize(460, 120)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(t("sess.name_prompt")))
        row = QHBoxLayout()
        edit = QLineEdit(default)
        row.addWidget(edit, 1)
        sug = QPushButton("✨ " + t("sess.suggest"))
        sug.setToolTip(t("sess.suggest_tip"))
        row.addWidget(sug)
        v.addLayout(row)
        br = QHBoxLayout()
        br.addStretch()
        cancel = QPushButton(t("btn.cancel"))
        okb = QPushButton(t("ui.save"))
        okb.setDefault(True)
        br.addWidget(cancel); br.addWidget(okb)
        v.addLayout(br)
        okb.clicked.connect(dlg.accept)
        cancel.clicked.connect(dlg.reject)
        edit.returnPressed.connect(dlg.accept)

        def suggest():
            sug.setEnabled(False); sug.setText("…")

            def work():
                return self._suggest_session_title(ctx)

            def done(res):
                sug.setEnabled(True); sug.setText("✨ " + t("sess.suggest"))
                if isinstance(res, str) and res.strip():
                    edit.setText(res.strip()[:60])
                    edit.selectAll()
            self._bg(work, done)
        sug.clicked.connect(suggest)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            return edit.text().strip(), True
        return "", False

    def _suggest_session_title(self, ctx):
        import fed_agent as _fedagent_mod
        convo = []
        for m in (ctx.history or [])[:12]:
            if m.get("role") == "user":
                c = _fedagent_mod.user_said(m)
            else:
                c = m.get("content")
            if isinstance(c, str) and c.strip():
                convo.append(f"{m.get('role', '?')}: {c[:300]}")
        text = "\n".join(convo)[:2000]
        if not text.strip():
            return ""
        try:
            base_url, api_key, model = self._resolve_endpoint("API")
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            payload = {"model": model, "temperature": 0.3, "max_tokens": 24,
                       "messages": [{"role": "system", "content": t("sess.title_prompt")},
                                    {"role": "user", "content": text}]}
            self._merge_extra_body(payload, base_url, model)
            r = requests.post(f"{base_url}/chat/completions", json=payload,
                              headers=headers, timeout=30)
            title = self.clean_ai_response(
                r.json()["choices"][0]["message"]["content"])
            return title.strip().strip('"').strip("«»").splitlines()[0][:60]
        except Exception:
            return ""

    def _open_sessions_dialog(self):
        def work():
            return self._mcp_call_json("chat_session_list", {})

        def done(res):
            sessions = (res or {}).get("sessions") if isinstance(res, dict) else None
            self._show_sessions_dialog(sessions or [])

        self._bg(work, done)

    def _show_sessions_dialog(self, sessions):
        dlg = QDialog(self)
        dlg.setWindowTitle(t("sess.dialog_title"))
        dlg.resize(540, 640)
        dlg.setStyleSheet("QDialog{background:#0b1220;}")
        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(18, 16, 18, 14)
        outer.setSpacing(10)
        head = QLabel(t("sess.dialog_title"))
        head.setStyleSheet(
            "QLabel{color:#f4ead8;font-size:17px;font-weight:600;"
            "letter-spacing:0.8px;background:transparent;border:none;}")
        outer.addWidget(head)
        hint = QLabel(t("sess.dialog_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("QLabel{color:#e6edf7;font-size:12px;"
                           "background:transparent;border:none;}")
        outer.addWidget(hint)
        if not sessions:
            empty = QLabel(t("sess.empty"))
            empty.setStyleSheet("QLabel{color:#64748b;padding:22px;}")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            outer.addWidget(empty)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        il = QVBoxLayout(inner)
        il.setSpacing(8)
        il.setContentsMargins(0, 0, 6, 0)
        _icon_css = (
            "QPushButton{background:#131a29;border:1px solid #2a3547;"
            "border-radius:7px;color:#cbd5e1;font-size:13px;min-height:0;"
            "padding:3px 0;}"
            "QPushButton:hover{border-color:%s;color:#f4f7fc;}")
        cards = {}
        _starred_n = sum(1 for s in sessions if s.get("starred"))
        _group_css = ("QLabel{color:#e6edf7;font-size:11px;font-weight:600;"
                      "letter-spacing:1.2px;background:transparent;border:none;"
                      "padding:6px 2px 0;}")
        if _starred_n:
            _gh = QLabel(t("sess.starred_head").upper())
            _gh.setObjectName("sessgroup")
            _gh.setStyleSheet(_group_css)
            il.addWidget(_gh)
        for i, s in enumerate(sessions):
            if _starred_n and i == _starred_n:
                _gh = QLabel(t("sess.recent_head").upper())
                _gh.setObjectName("sessgroup")
                _gh.setStyleSheet(_group_css)
                il.addWidget(_gh)
            _star = bool(s.get("starred"))
            row = QWidget()
            row.setObjectName("sesscard")
            row.setProperty("sess_uuid", s.get("uuid"))
            row.base_border = "#4d4120" if _star else "#2a3547"
            row.setStyleSheet(_SESS_CARD_CSS % row.base_border)
            cards[s.get("uuid")] = row
            rl = QVBoxLayout(row)
            rl.setContentsMargins(14, 10, 14, 9)
            rl.setSpacing(3)
            _tl = QHBoxLayout()
            _tl.setSpacing(6)
            if _star:
                _sg = QLabel("★")
                _sg.setObjectName("sessstarmark")
                _sg.setStyleSheet("QLabel{color:#f5b800;font-size:14px;"
                                  "background:transparent;border:none;}")
                _tl.addWidget(_sg, 0, Qt.AlignmentFlag.AlignTop)
            title = QLabel(s.get("title") or t("sess.untitled"))
            title.setTextFormat(Qt.TextFormat.PlainText)
            title.setToolTip(s.get("title") or t("sess.untitled"))
            title.setWordWrap(True)
            title.setStyleSheet(
                "QLabel{color:#f2e8d5;font-size:14px;font-weight:600;"
                "letter-spacing:0.4px;background:transparent;border:none;}")
            _tl.addWidget(title, 1)
            rl.addLayout(_tl)
            meta = QLabel(f"{s.get('n', 0)} msgs · "
                          f"{(s.get('updated_at') or '')[:16].replace('T', ' ')}")
            meta.setStyleSheet("QLabel{color:#e6edf7;font-size:12px;"
                               "background:transparent;border:none;}")
            rl.addWidget(meta)
            uid = s.get("uuid")
            acts = QHBoxLayout()
            acts.setSpacing(6)
            rest = QPushButton("📂")
            rest.setToolTip(t("sess.restore"))
            rest.setFixedSize(38, 26)
            rest.setCursor(Qt.CursorShape.PointingHandCursor)
            rest.setStyleSheet(_icon_css % "#3b82f6")
            rest.clicked.connect(lambda _=False, u=uid, pd=s.get("pinned_device"):
                                 (self._restore_session(u, pin=pd), dlg.accept()))
            acts.addWidget(rest)
            dele = QPushButton("🗑")
            dele.setToolTip(t("ui.delete"))
            dele.setFixedSize(38, 26)
            dele.setCursor(Qt.CursorShape.PointingHandCursor)
            dele.setStyleSheet(_icon_css % "#ef4444")
            dele.clicked.connect(lambda _=False, u=uid:
                                 self._delete_session(u, dlg))
            acts.addWidget(dele)
            star = QPushButton("★" if _star else "☆")
            star.setObjectName("sessstar")
            star.setToolTip(t("sess.unstar") if _star else t("sess.star"))
            star.setFixedSize(38, 26)
            star.setCursor(Qt.CursorShape.PointingHandCursor)
            star.setStyleSheet(_icon_css % "#f5b800")
            star.clicked.connect(lambda _=False, u=uid, on=not _star:
                                 self._star_session(u, on, dlg))
            acts.addWidget(star)
            acts.addStretch()
            rl.addLayout(acts)
            il.addWidget(row)
        il.addStretch()
        scroll.setWidget(inner)
        if sessions:
            outer.addWidget(self._sessions_prune_bar(sessions, dlg, cards))
        outer.addWidget(scroll, 1)
        close = QPushButton(t("btn.cancel"))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(
            "QPushButton{background:#131a29;border:1px solid #2a3547;"
            "border-radius:8px;padding:7px 18px;color:#93a1b5;}"
            "QPushButton:hover{border-color:#3b82f6;color:#e6edf7;}")
        _cl = QHBoxLayout()
        _cl.addStretch()
        _cl.addWidget(close)
        outer.addLayout(_cl)
        close.clicked.connect(dlg.reject)
        dlg.exec()

    def _star_session(self, uuid, on, dlg):
        def work():
            return self._mcp_call_json("chat_session_star",
                                       {"uuid": uuid, "starred": bool(on)})

        def done(res):
            if isinstance(res, dict) and res.get("error"):
                self.add_message(t("sess.star_fail", err=res["error"]), "system")
            dlg.accept()
            self._open_sessions_dialog()

        self._bg(work, done)

    def _sessions_prune_bar(self, sessions, dlg, cards):
        from PyQt6.QtWidgets import QDateEdit
        from PyQt6.QtCore import QDate, QSettings
        keep = {getattr(c, "session_uuid", None)
                for c in (getattr(self, "_chat_ctxs", {}) or {}).values()}
        keep |= {x.get("uuid") for x in sessions if x.get("pinned_device")}
        keep |= {x.get("uuid") for x in sessions if x.get("starred")}
        try:
            keep.add(QSettings("hoplita", "hoplita").value("cron_session_uuid", ""))
        except Exception:
            pass
        keep.discard(None)
        keep.discard("")
        box = QFrame()
        box.setObjectName("prunebar")
        box.setStyleSheet(
            "#prunebar{background:#101827;border:1px solid #2a3547;"
            "border-radius:10px;}"
            "QLabel{color:#e6edf7;font-size:12px;background:transparent;border:none;}"
            "QDateEdit{background:#131a29;border:1px solid #2a3547;"
            "border-radius:6px;color:#f4f7fc;padding:3px 6px;}")
        v = QVBoxLayout(box)
        v.setContentsMargins(12, 9, 12, 9)
        v.setSpacing(7)
        _btn_css = ("QPushButton{background:#131a29;border:1px solid #2a3547;"
                    "border-radius:7px;color:#cbd5e1;padding:4px 10px;}"
                    "QPushButton:hover{border-color:#3b82f6;color:#f4f7fc;}")
        r1 = QHBoxLayout()
        r1.setSpacing(6)
        r1.addWidget(QLabel(t("sess.prune_older")))
        today = QDate.currentDate()
        start = QDate(2000, 1, 1)
        d_from, d_to = QDateEdit(), QDateEdit()
        for d in (d_from, d_to):
            d.setCalendarPopup(True)
            d.setDisplayFormat("yyyy-MM-dd")
            d.setMinimumDate(start)
            d.setMaximumDate(today)
        d_from.setSpecialValueText(t("sess.prune_start"))
        d_from.setDate(start)
        d_to.setDate(today.addYears(-1))
        for key, back in (("sess.prune_1m", lambda d: d.addMonths(-1)),
                          ("sess.prune_6m", lambda d: d.addMonths(-6)),
                          ("sess.prune_1y", lambda d: d.addYears(-1))):
            b = QPushButton(t(key))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_btn_css)
            b.clicked.connect(lambda _=False, f=back: (
                d_from.setDate(start), d_to.setDate(f(today))))
            r1.addWidget(b)
        r1.addStretch()
        v.addLayout(r1)
        r2 = QHBoxLayout()
        r2.setSpacing(6)
        r2.addWidget(QLabel(t("sess.prune_from")))
        r2.addWidget(d_from)
        r2.addWidget(QLabel(t("sess.prune_to")))
        r2.addWidget(d_to)
        r2.addStretch()
        go = QPushButton()
        go.setCursor(Qt.CursorShape.PointingHandCursor)
        go.setStyleSheet(
            "QPushButton{background:#2a1418;border:1px solid #7f1d1d;"
            "border-radius:7px;color:#fecaca;padding:5px 12px;}"
            "QPushButton:hover{border-color:#ef4444;color:#fff1f1;}"
            "QPushButton:disabled{background:#131a29;border-color:#2a3547;"
            "color:#64748b;}")
        r2.addWidget(go)
        v.addLayout(r2)
        kept = QLabel()
        kept.setStyleSheet("QLabel{color:#93a1b5;font-size:11px;}")
        v.addWidget(kept)

        def _range():
            lo = "" if d_from.date() == start else d_from.date().toString("yyyy-MM-dd")
            return lo, d_to.date().toString("yyyy-MM-dd")

        def _refresh(*_):
            lo, hi = _range()
            n = msgs = held = 0
            for x in sessions:
                day = (x.get("updated_at") or x.get("created_at") or "")[:10]
                inside = bool(day) and (not lo or day >= lo) and day <= hi
                if inside and x.get("uuid") in keep:
                    held += 1
                    inside = False
                n += inside
                msgs += x.get("n", 0) if inside else 0
                c = cards.get(x.get("uuid"))
                if c is not None:
                    c.setStyleSheet(_SESS_CARD_CSS % (
                        "#7f1d1d" if inside else getattr(c, "base_border", "#2a3547")))
            go.setEnabled(n > 0)
            go.setText(t("sess.prune_go", n=n, msgs=msgs) if n else t("sess.prune_none"))
            kept.setText(t("sess.prune_kept", k=held) if held else "")
            kept.setVisible(bool(held))

        def _go():
            lo, hi = _range()
            go.setEnabled(False)

            def work():
                return self._mcp_call_json("chat_session_prune", {
                    "from": lo, "to": hi, "keep": sorted(keep)})

            def done(res):
                res = res if isinstance(res, dict) else {}
                if res.get("error"):
                    self.add_message(t("sess.prune_fail", err=res["error"]), "system")
                else:
                    self.add_message(t("sess.prune_done", n=res.get("n", 0),
                                       msgs=res.get("messages_purged", 0)), "system")
                dlg.accept()
                self._open_sessions_dialog()

            self._bg(work, done)

        d_from.dateChanged.connect(_refresh)
        d_to.dateChanged.connect(_refresh)
        go.clicked.connect(_go)
        _refresh()
        return box

    def _tab_by_session_uuid(self, uuid):
        for c in self._chat_ctxs.values():
            if uuid and getattr(c, "session_uuid", None) == uuid:
                return c.tab_id
        return None

    def _reload_tab_from_row(self, tab_id, uuid=""):
        ctx = self._chat_ctxs.get(tab_id)
        tab = self._tab_by_id(tab_id)
        if ctx is None or tab is None or getattr(ctx, "task_running", False):
            return
        uuid = uuid or getattr(ctx, "session_uuid", "") or ""
        if not uuid:
            return

        _url = self._tab_url(ctx)

        def work():
            return self._mcp_call_json("chat_session_load", {"uuid": uuid},
                                       url=_url)

        def done(res):
            sess = (res or {}).get("session") if isinstance(res, dict) else None
            if not isinstance(sess, dict) or getattr(ctx, "task_running", False):
                return
            ctx.session_uuid = sess.get("uuid") or uuid
            ctx.history = sess.get("history") or []
            self._chat_view(tab_id).load_html(sess.get("html", "") or "")

        self._bg(work, done)

    def _restore_session(self, uuid, quiet=False, pin=None, then=None):
        _open = self._tab_by_session_uuid(uuid)
        if _open is not None:
            if then is not None:
                return then(_open)
            self._switch_chat_tab(_open)
            return

        def work():
            if pin and pin.get("id"):
                _p, _remote = self._tab_runs_remotely(type("_P", (), {"pinned_device": pin})())
                if _remote:
                    ok, d, _ = self._mcp_try("chat_session_load", {"uuid": uuid}, timeout=30,
                                             url=f"{self._mcp_url}/tied/{pin['id']}")
                    if ok and d.get("session"):
                        return d
            return self._mcp_call_json("chat_session_load", {"uuid": uuid})

        def done(res):
            getattr(self, "_pinned_waiting", set()).discard(uuid)
            sess = (res or {}).get("session") if isinstance(res, dict) else None
            if not sess:
                if then is not None:
                    return then(None)
                self.add_message(t("sess.load_fail"), "error")
                return
            if sess.get("kind") == "cron":
                self._ensure_cron_tab()
                _cctx = self._chat_ctxs.get(CRON_TAB_ID)
                _ctab = self._tab_by_id(CRON_TAB_ID)
                if _cctx is not None and _ctab is not None:
                    _cctx.session_uuid = sess.get("uuid")
                    _cctx.history = sess.get("history") or []
                    self._chat_view(CRON_TAB_ID).load_html(sess.get("html", "") or "")
                    self._tu_restore(sess, CRON_TAB_ID)
                    if not quiet:
                        self._switch_chat_tab(CRON_TAB_ID)
                        self.add_message(t("sess.restored",
                                           title=t("cron.tab_title")), "system")
                return
            self._local_tab_seq += 1
            tid = f"local:{self._local_tab_seq}"
            title = sess.get("title") or t("chat.tab_local_n", n=self._local_tab_seq + 1)
            ctx = ChatCtx(tid, title)
            ctx.history = sess.get("history") or []
            ctx.model_override = sess.get("model") or ""
            ctx.sticky = sess.get("sticky") or ""
            ctx.user_fixed_context = sess.get("user_fixed_context") or []
            ctx.pinned_skills = sess.get("pinned_skills") or []
            ctx.code_project = sess.get("code_project") or ""
            ctx.pinned_device = sess.get("pinned_device") or None
            ctx.sync_id = sess.get("sync_id") or ctx.sync_id
            ctx.verify_cmd = sess.get("verify_cmd") or ""
            ctx.goal = sess.get("goal") or ""
            ctx.no_log = bool(sess.get("no_log"))
            ctx.local_only = bool(sess.get("local_only"))
            ctx.qa_override = sess.get("qa_override") or ""
            ctx.blocked_tools = set(sess.get("blocked_tools") or [])
            ctx.session_uuid = sess.get("uuid")
            ctx.job_id = sess.get("job_id") or ""
            ctx.job_name = sess.get("job_name") or ""
            ctx.job_session = ctx.session_uuid if ctx.job_id else None
            self._chat_ctxs[tid] = ctx
            self._tu_restore(sess, tid)
            self._chat_tabs.append({"id": tid, "kind": "local", "peer": None,
                                    "title": title, "history_ref": True})
            self._chat_view(tid).load_html(sess.get("html", "") or "")
            self._render_chat_tabs()
            try:
                _b, _k, _m = self._chat_base_endpoint()
                if _b and _m:
                    self._detect_ctx_async(f"{_b}|{_m}", _b, _k)
            except Exception:
                pass
            if then is not None:
                return then(tid)
            if not quiet:
                self._switch_chat_tab(tid)
                self.add_message(t("sess.restored", title=title), "system")
                if sess.get("compacted_n"):
                    self.add_message(
                        t("sess.restored_compacted",
                          n=sess.get("compacted_n", 0),
                          fresh=sess.get("fresh_n", 0),
                          since=sess.get("context_since") or "?"), "system")

        self._bg(work, done)

    def _reopen_pinned(self):
        def work():
            return self._mcp_call_json("chat_session_list", {})

        def done(res):
            _going_to = getattr(self, "_reopening_pinned", None)
            if _going_to is None:
                _going_to = self._reopening_pinned = set()
            _rows = ((res or {}).get("sessions") or []) if isinstance(res, dict) else []
            _rows = sorted(_rows, key=lambda r: r.get("created_at") or "")
            _convs = getattr(self, "_reopening_syncs", None)
            if _convs is None:
                _convs = self._reopening_syncs = set()
            for sess in _rows:
                _sid = sess.get("uuid")
                if not sess.get("pinned_device") or _sid in _going_to:
                    continue
                _sync = sess.get("sync_id") or ""
                if self._tab_by_session_uuid(_sid) is not None:
                    _convs.add(_sync)
                    continue
                if _sync and (_sync in _convs or self._tab_by_sync_id(_sync)):
                    continue
                if _sync:
                    _convs.add(_sync)
                _going_to.add(_sid)
                self._pinned_waiting.add(_sid)
                self._restore_session(_sid, quiet=True, pin=sess.get("pinned_device"))
            self._pinned_listed = True

        if not hasattr(self, "_pinned_waiting"):
            self._pinned_waiting = set()
        self._bg(work, done)

    def _reopen_cron_tab(self):
        try:
            from PyQt6.QtCore import QSettings
            uuid = QSettings("hoplita", "hoplita").value("cron_session_uuid", "")
        except Exception:
            uuid = ""
        if not uuid:
            return

        def work():
            return self._mcp_call_json("chat_session_load", {"uuid": uuid})

        def done(res):
            sess = (res or {}).get("session") if isinstance(res, dict) else None
            if not isinstance(sess, dict) or sess.get("deleted"):
                return
            self._ensure_cron_tab()
            ctx = self._chat_ctxs.get(CRON_TAB_ID)
            tab = self._tab_by_id(CRON_TAB_ID)
            if ctx is None or tab is None:
                return
            ctx.session_uuid = uuid
            ctx.history = (sess.get("history") or []) + (ctx.history or [])
            _view = self._chat_view(CRON_TAB_ID)
            _view.load_html((sess.get("html", "") or "") + _view.toHtml())

        self._bg(work, done)

    def _delete_session(self, uuid, dlg):
        _extra = []
        for _tid, _c in (getattr(self, "_chat_ctxs", {}) or {}).items():
            if getattr(_c, "session_uuid", "") != uuid:
                continue
            _extra += [i for i in (getattr(_c, "sync_id", "") or "",
                                   str(_tid or "")) if i]

        def work():
            return self._mcp_call_json("chat_session_delete",
                                       {"uuid": uuid, "conv_ids": _extra})

        def done(_res):
            dlg.accept()
            self._open_sessions_dialog()

        self._bg(work, done)

    def _canvas_default_title(self, html):
        import re as _re
        for pat in (r"<title[^>]*>(.*?)</title>", r"<h1[^>]*>(.*?)</h1>",
                    r"<h2[^>]*>(.*?)</h2>"):
            m = _re.search(pat, html or "", _re.I | _re.S)
            if m:
                txt = _re.sub(r"<[^>]+>", "", m.group(1)).strip()
                if txt:
                    return txt[:48]
        return "Canvas"

    def _save_current_canvas(self):
        _tab = self._active_tab_id
        html = self._canvas(_tab)["html"]
        if not html.strip():
            self.add_message(t("canvas.nothing"), "system")
            return
        from PyQt6.QtWidgets import QInputDialog
        default = self._canvas_default_title(html)
        name, ok = QInputDialog.getText(self, t("canvas.name_title"),
                                        t("canvas.name_prompt"), text=default)
        if not ok:
            return
        name = (name or "").strip() or default
        canvas = {"uuid": self._canvas(_tab)["uuid"],
                  "title": name, "html": html}

        def work():
            return self._mcp_call_json("canvas_save", {"canvas": canvas})

        def done(res):
            if isinstance(res, dict) and res.get("ok"):
                self._canvas(_tab)["uuid"] = res.get("uuid")
                self.add_message(t("canvas.saved", title=name), "system")
            else:
                err = res if isinstance(res, str) else (res or {}).get("error", "?")
                self.add_message(t("canvas.save_fail", err=err), "error")

        self._bg(work, done)

    def _open_canvases_dialog(self):
        def work():
            return self._mcp_call_json("canvas_list", {})

        def done(res):
            self._show_canvases_dialog((res or {}).get("canvases") or []
                                       if isinstance(res, dict) else [])

        self._bg(work, done)

    def _show_canvases_dialog(self, canvases):
        dlg = QDialog(self)
        dlg.setWindowTitle(t("canvas.dialog_title"))
        dlg.resize(560, 460)
        outer = QVBoxLayout(dlg)
        outer.addWidget(QLabel(t("canvas.dialog_hint")))
        if not canvases:
            outer.addWidget(QLabel(t("canvas.empty")))
        from PyQt6.QtWidgets import QScrollArea
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner = QWidget(); il = QVBoxLayout(inner); il.setSpacing(6)
        for c in canvases:
            row = QWidget(); rl = QHBoxLayout(row); rl.setContentsMargins(8, 6, 8, 6)
            meta = f"{(c.get('size', 0) // 1024)} KB · {(c.get('updated_at') or '')[:16].replace('T', ' ')}"
            lab = QLabel(f"<b>{c.get('title') or t('sess.untitled')}</b><br>"
                         f"<span style='color:#94a3b8'>{meta}</span>")
            lab.setTextFormat(Qt.TextFormat.RichText)
            rl.addWidget(lab, 1)
            uid = c.get("uuid")
            rest = QPushButton(t("sess.restore"))
            rest.clicked.connect(lambda _=False, u=uid: (self._restore_canvas(u), dlg.accept()))
            rl.addWidget(rest)
            dele = QPushButton(t("ui.delete"))
            dele.clicked.connect(lambda _=False, u=uid: self._delete_canvas(u, dlg))
            rl.addWidget(dele)
            row.setStyleSheet("QWidget { background:#0f172a; border:1px solid #1e293b; border-radius:8px; }")
            il.addWidget(row)
        il.addStretch(); scroll.setWidget(inner); outer.addWidget(scroll, 1)
        close = QPushButton(t("btn.cancel")); close.clicked.connect(dlg.reject)
        outer.addWidget(close)
        dlg.exec()

    def _restore_canvas(self, uuid):
        def work():
            return self._mcp_call_json("canvas_load", {"uuid": uuid})

        def done(res):
            c = (res or {}).get("canvas") if isinstance(res, dict) else None
            if not c or not (c.get("html") or "").strip():
                self.add_message(t("canvas.load_fail"), "error")
                return
            self._canvas(self._active_tab_id)["uuid"] = c.get("uuid")
            self._render_canvas_html(c["html"], origin="")
            self.add_message(t("canvas.restored", title=c.get("title", "")), "system")

        self._bg(work, done)

    def _delete_canvas(self, uuid, dlg):
        def work():
            return self._mcp_call_json("canvas_delete", {"uuid": uuid})

        def done(_res):
            dlg.accept()
            self._open_canvases_dialog()

        self._bg(work, done)

    def _own_tab_for(self, sync_id, ev):
        self._local_tab_seq += 1
        tid = f"local:{self._local_tab_seq}"
        first = str(ev.get("text") or ev.get("message") or "").strip()
        title = (first.splitlines()[0][:40] if first
                 else t("chat.tab_local_n", n=self._local_tab_seq + 1))
        ctx = ChatCtx(tid, title)
        ctx.sync_id = sync_id
        try:
            ok, d, _ = self._mcp_try("device_info", {}, timeout=10)
            if ok and isinstance(d, dict) and d.get("id"):
                ctx.pinned_device = {"id": d["id"], "name": d.get("name", "")}
        except Exception:
            pass
        self._chat_ctxs[tid] = ctx
        self._chat_tabs.append({"id": tid, "kind": "local", "peer": None,
                                "title": title, "history_ref": True})
        self._render_chat_tabs()
        return tid

    def _new_local_tab(self):
        self._make_local_tab()

    def _make_local_tab(self, title=None, switch=True):
        self._local_tab_seq += 1
        n = self._local_tab_seq
        tid = f"local:{n}"
        title = title or t("chat.tab_local_n", n=n + 1)
        self._chat_ctxs[tid] = ChatCtx(tid, title)
        self._chat_tabs.append({"id": tid, "kind": "local", "peer": None,
                                "title": title, "history_ref": True})
        self._render_chat_tabs()
        if switch:
            self._switch_chat_tab(tid)
        return tid

    _ALERT_MARK = "__ALERT_ON_COMPLETE__\n"

    def _run_job_in_tab(self, job):
        jid = job.get("id") or ""
        tid = next((k for k, c in self._chat_ctxs.items()
                    if jid and getattr(c, "job_id", "") == jid
                    and self._tab_by_id(k) is not None), None)
        if tid is not None:
            return self._job_feed(tid, job)
        uuid = ((job.get("context") or {}).get("session") or "").strip()
        _taken = uuid and any(getattr(c, "session_uuid", None) == uuid
                              and getattr(c, "job_id", "") != jid
                              for c in self._chat_ctxs.values())
        if uuid and not _taken:
            self._restore_session(uuid, quiet=True,
                                  then=lambda t_: self._job_feed(t_, job))
            return
        self._job_feed(None, job)

    def _job_feed(self, tid, job):
        name = job.get("name") or "job"
        if tid is None or self._chat_ctxs.get(tid) is None:
            tid = self._make_local_tab(name, switch=False)
        ctx = self._chat_ctxs[tid]
        _tab = self._tab_by_id(tid)
        if _tab is not None and getattr(ctx, "job_name", "") \
                and _tab.get("title") == ctx.job_name and name != ctx.job_name:
            _tab["title"] = ctx.title = name
        ctx.job_id = job.get("id") or ""
        ctx.job_name = name
        instr = job.get("instruction") or ""
        if instr.startswith(self._ALERT_MARK):
            instr = instr[len(self._ALERT_MARK):]
            ctx.job_alert = True
        head = t("job.run_msg", name=name, id=ctx.job_id)
        if any(str(m[0]).startswith(head) for m in ctx.msg_queue):
            return tid
        msg = head + "\n\n" + instr
        ctx.msg_queue.append((msg, []))
        self._render_chat_tabs()
        if tid == self._active_tab_id:
            self._update_queue_label()
        self._drain_tab_queue(tid)
        return tid

    def _job_after_turn(self, tab_id):
        ctx = self._chat_ctxs.get(tab_id)
        if ctx is None or not getattr(ctx, "job_id", ""):
            return
        if getattr(ctx, "job_alert", False):
            ctx.job_alert = False
            last = next((m.get("content") for m in reversed(ctx.history or [])
                         if m.get("role") == "assistant"
                         and isinstance(m.get("content"), str)), "")
            if last:
                self.signals.show_alert.emit(f"⏰ {ctx.job_name}", last[:500])
        self._job_link_session(tab_id)

    def _job_link_session(self, tab_id):
        ctx = self._chat_ctxs.get(tab_id)
        if ctx is None or not getattr(ctx, "job_id", ""):
            return
        uuid = getattr(ctx, "session_uuid", None)
        if uuid and uuid != getattr(ctx, "job_session", None):
            ctx.job_session = uuid
            jid, title = ctx.job_id, ctx.title

            def work():
                return self.cron_manager.update_job(jid, context={
                    "session": uuid, "title": title,
                    "dated": datetime.now().isoformat(timespec="seconds"),
                    "source": "tab", "text": "", "live": True})
            self._bg(work, lambda _r: None)

    def _ensure_cron_ctx(self):
        c = self._chat_ctxs.get(CRON_TAB_ID)
        if c is None:
            c = ChatCtx(CRON_TAB_ID, t("cron.tab_title"))
            c.sync_id = ""
            self._chat_ctxs[CRON_TAB_ID] = c
        return c

    def _ensure_cron_tab(self):
        self._ensure_cron_ctx()
        if self._tab_by_id(CRON_TAB_ID) is None:
            self._chat_tabs.append({
                "id": CRON_TAB_ID, "kind": "cron", "peer": None,
                "title": t("cron.tab_title"), "history_ref": True})
            self._render_chat_tabs()
            if not getattr(self, "_cron_rehydrated", False):
                self._cron_rehydrated = True
                self._reopen_cron_tab()

    def _tab_by_id(self, tab_id):
        for t_ in self._chat_tabs:
            if t_["id"] == tab_id:
                return t_
        return None

    def _switch_chat_tab(self, tab_id):
        if tab_id == self._active_tab_id:
            return
        if getattr(self, "machine_view", None) is not None:
            self.machine_view.set_machine(self._machine(tab_id))
        shown = getattr(self, "_tu_shown", 0)
        if shown and (self._tu_store().get(shown) or {}).get("tab") != tab_id:
            self._collapse_tool_usage(shown)
        self._stash_draft(self._active_tab_id)
        if getattr(self, "code_editor", None) is not None:
            try:
                self._code_stash_current()
                self._code_detach_editor()
            except Exception:
                pass
        tgt = self._tab_by_id(tab_id)
        if tgt is None:
            return
        self._active_tab_id = tab_id
        self._sticky_paint(tab_id)
        _c = self._chat_ctxs.get(tab_id)
        if hasattr(self, "_code_project"):
            self._code_project = getattr(_c, "code_project", "") or ""
            self.code_project_edit.setText(self._code_project)
        self._render_chat_tabs()
        self._refresh_chat_meta()
        self._restore_draft(tab_id)
        if self._canvas_sync():
            self._render_chat_tabs()
        self._show_chat_view(tab_id)

    def _new_chat_view(self):
        v = ChatView()
        v.setReadOnly(True)
        v.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        v.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        v.setFont(QFont("Consolas" if IS_WINDOWS else "Monospace", 12))
        v.setObjectName("chat_display")
        self._zoom_targets.append((v, "", 0))
        self._apply_zoom()
        v.viewport().setMouseTracking(True)
        v.viewport().installEventFilter(self)
        return v

    def _chat_view(self, tab_id):
        v = self._chat_view_by_tab.get(tab_id)
        if v is None:
            v = self._chat_view_by_tab[tab_id] = self._new_chat_view()
            self._chat_views.addWidget(v)
        return v

    def _show_chat_view(self, tab_id):
        v = self._chat_view(tab_id)
        self.chat_display = v
        self._chat_views.setCurrentWidget(v)
        return v

    def _drop_chat_view(self, tab_id):
        v = self._chat_view_by_tab.pop(tab_id, None)
        if v is None:
            return
        self._zoom_targets = [z for z in self._zoom_targets if z[0] is not v]
        self._chat_views.removeWidget(v)
        v.deleteLater()

    @property
    def _chat_follow(self):
        return self.chat_display.follow

    @_chat_follow.setter
    def _chat_follow(self, value):
        self.chat_display.follow = bool(value)

    def _stash_draft(self, tab_id):
        ctx = self._chat_ctxs.get(tab_id)
        entry = getattr(self, "entry", None)
        if ctx is None or entry is None:
            return
        ctx.draft = entry.toPlainText()
        ctx.draft_files = list(entry.pending_files)
        ctx.draft_hist = list(getattr(entry, "_hist", []))

    def _restore_draft(self, tab_id):
        ctx = self._chat_ctxs.get(tab_id)
        entry = getattr(self, "entry", None)
        if entry is None:
            return
        entry.setPlainText(getattr(ctx, "draft", "") or "")
        entry.moveCursor(QTextCursor.MoveOperation.End)
        entry.pending_files = list(getattr(ctx, "draft_files", None) or [])
        entry._hist = list(getattr(ctx, "draft_hist", None) or [])
        entry._hist_i = None
        entry._draft = ""
        if entry.pending_files:
            entry._show_indicator()

    def _refresh_chat_meta(self):
        if getattr(self, "_chat_stack", None) is not None:
            _ctx = self._chat_ctxs.get(self._active_tab_id)
            if getattr(_ctx, "pending_ack", None) is not None:
                self._show_ack_in_panel(self._active_tab_id)
            else:
                if getattr(self, "_ack_showing_tab", None) is not None \
                        and getattr(self, "agents_panel", None) is not None:
                    self.agents_panel.close_ack()
                    self._ack_showing_tab = None
                self._chat_stack.setCurrentIndex(0)
                self._council_show_gate(None)
        self._sync_input_placeholder()
        self._paint_token_meter()
        if getattr(self, "stop_btn", None) is not None:
            self.stop_btn.setVisible(self._task_running)
        self._refresh_intent_strip()
        if getattr(self, "agents_panel", None) is not None:
            _ic = self._chat_ctxs.get(self._active_tab_id)
            self.agents_panel.set_intent(self._intent_for_active())
        self._set_enchanting(self._busy_now())
        self._active_stream_live = False
        try:
            self._update_queue_label()
        except Exception:
            pass

    def _close_chat_tab(self, tab_id, notify=True):
        tab = self._tab_by_id(tab_id)
        if tab is None:
            return
        _c = self._chat_ctxs.get(tab_id)
        if _c is not None and _c.task_running:
            self._cancel_pending_ack(tab_id)
            _c.stop_requested.set()
        if _c is not None and tab.get("kind") == "local":
            _bkey = getattr(_c, "sync_id", "") or tab_id
            _buuid = getattr(_c, "session_uuid", None) or ""
            _burl = self._tab_url(_c)
            self._bg(lambda: requests.post(
                f"{_burl}/agent/background/stop",
                json={"conv": _buuid, "tab": _bkey}, timeout=5))
        if _c is not None and tab.get("kind") == "local":
            segment = _c.history[_c.last_extracted_idx:]
            if any(m.get("role") == "user" for m in segment):
                self._auto_extract_facts_async(segment)
                self._auto_extract_lessons_async(segment)
            _skey = getattr(_c, "sync_id", "") or tab_id
            _surl = self._tab_url(_c)
            self._bg(lambda: requests.post(
                f"{_surl}/agent/memory_snapshot",
                json={"session": _skey, "action": "reset"}, timeout=5))
        if tab_id == "local":
            if self._active_tab_id == "local" \
                    and getattr(self, "code_editor", None) is not None:
                try:
                    self._code_stash_current()
                    self._code_detach_editor()
                except Exception:
                    pass
            _old = self._chat_ctxs.get("local")
            if _old is not None:
                self._cancel_pending_ack("local")
                _old.stop_requested.set()
            self._chat_ctxs["local"] = ChatCtx("local", t("chat.tab_local"))
            tab["title"] = t("chat.tab_local")
            self._chat_view("local").clear()
            self._canvas_drop("local")
            if self._active_tab_id == "local":
                self._canvas_sync()
                if hasattr(self, "_code_project"):
                    self._code_close_project(silent=True)
                    self._code_view_placeholder(False)
                self._refresh_chat_meta()
            self._render_chat_tabs()
            return
        if tab.get("kind") == "cron":
            self._chat_tabs = [x for x in self._chat_tabs if x["id"] != tab_id]
            if self._active_tab_id == tab_id:
                self._active_tab_id = "local"
                self._show_chat_view("local")
                self._refresh_chat_meta()
                self._canvas_sync()
            self._render_chat_tabs()
            return
        peer = tab.get("peer")
        if tab.get("ended"):
            notify = False
        if notify and peer and tab["kind"] in ("green", "red"):
            _endpoint = ("session-stop" if tab["kind"] == "green"
                         else "session-close-inbound")

            def _notify(p=peer, ep=_endpoint):
                try:
                    requests.post(f"{self._mcp_url}/agent/{ep}",
                                  json={"peer": p}, timeout=5)
                except Exception:
                    pass
            threading.Thread(target=_notify, daemon=True).start()
        self._session_waiting.discard(tab_id)
        self._session_queue.pop(tab_id, None)
        self._enchant_t0.pop(tab_id, None)
        self._chat_tabs = [x for x in self._chat_tabs if x["id"] != tab_id]
        self._chat_ctxs.pop(tab_id, None)
        getattr(self, "_machines", {}).pop(tab_id, None)
        self._canvas_drop(tab_id)
        if self._active_tab_id == tab_id:
            self._active_tab_id = "local"
            self._show_chat_view("local")
            self._refresh_chat_meta()
            self._canvas_sync()
        self._drop_chat_view(tab_id)
        self._render_chat_tabs()
        self._set_enchanting(self._busy_now())
        self._update_queue_label()


    def _ensure_green_tab(self, peer):
        tid = f"green:{peer}"
        if self._tab_by_id(tid) is None:
            self._chat_tabs.append({
                "id": tid, "kind": "green", "peer": peer,
                "title": f"🟢 {peer}", "history_ref": False})
            self._render_chat_tabs()
        return tid

    def _ensure_red_tab(self, peer):
        tid = f"red:{peer}"
        if self._tab_by_id(tid) is None:
            self._chat_tabs.append({
                "id": tid, "kind": "red", "peer": peer,
                "title": f"🔴 {peer}", "history_ref": False})
            self._render_chat_tabs()
        return tid

    def _chat_scroll_bottom(self, force=False):
        self.chat_display.scroll_bottom(force)

    def _append_html_to_tab(self, tab_id, fragment):
        if self._tab_by_id(tab_id) is None:
            return
        view = self._chat_view(tab_id)
        view.append_block(fragment)
        view.scroll_bottom()

    def _chat_frag(self, text, color, label, rich=False):
        import html as _html
        ts = datetime.now().strftime("%H:%M:%S")
        if rich:
            body = self._render_markdown(text, color)
        else:
            body = (f'<span style="color:{color}">'
                    f'{_html.escape(text).replace(chr(10), "<br>")}</span>')
        return (f'<span style="color:#64748b">[{ts}]</span> '
                f'<span style="color:{color};font-weight:bold">{label}:</span> '
                f'{body}<br>')

    def _load_memory_experience(self):
        if not hasattr(self, "exp_table"):
            return
        self.exp_table.clear()
        ok, r, err = self._mcp_try("list_experience", {"limit": 200}, timeout=20)
        if not ok:
            self.exp_table.addItem(t("mem.exp_fail", err=err))
            return
        exps = r.get("experiences") or []
        self._exp_rows = exps
        if not exps:
            self.exp_table.addItem(t("mem.exp_empty"))
            return
        for e in exps:
            mark = {"ok": "✓", "partial": "~"}.get(e.get("feedback", ""), "✗")
            tags = ("  #" + " #".join(e["tags"])) if e.get("tags") else ""
            self.exp_table.addItem(
                f"{mark}  {(e.get('date') or '')[:16]}  {e.get('goal', '')}{tags}")

    def _show_experience_dialog(self, item):
        if item is None:
            return
        row = self.exp_table.row(item)
        rows = getattr(self, "_exp_rows", []) or []
        if not (0 <= row < len(rows)):
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(t("mem.exp_one_title"))
        dlg.resize(760, 520)
        v = QVBoxLayout(dlg)
        box = QPlainTextEdit(rows[row].get("doc", ""))
        box.setReadOnly(True)
        box.setStyleSheet("QPlainTextEdit { background:#0f172a; color:#e6edf7; "
                          "border:1px solid #334155; font-family:monospace; "
                          "font-size:12px; padding:8px; }")
        v.addWidget(box, 1)
        b = QPushButton(t("btn.close"))
        b.clicked.connect(dlg.accept)
        v.addWidget(b)
        dlg.exec()

    def _setup_enchant_bar(self, parent_layout):
        self._enchant_bar = QWidget()
        self._enchant_bar.setObjectName("enchant_bar")
        h = QHBoxLayout(self._enchant_bar)
        h.setContentsMargins(16, 5, 16, 5)
        h.setSpacing(10)
        self._enchant_logo = QLabel()
        _p = self._logo_pixmap(22)
        if _p is not None:
            self._enchant_logo.setPixmap(_p)
        h.addWidget(self._enchant_logo)
        self._enchant_text = QLabel(t("chat.enchanting"))
        self._enchant_text.setObjectName("enchant_text")
        h.addWidget(self._enchant_text)
        self._enchant_sweep = _SweepLine()
        self._enchant_sweep.setObjectName("enchant_sweep")
        h.addWidget(self._enchant_sweep, 1)
        self._enchant_bar.setVisible(False)
        parent_layout.addWidget(self._enchant_bar)
        self._enchant_phase = 0
        self._enchant_timer = QTimer(self)
        self._enchant_timer.setInterval(110)
        self._enchant_timer.timeout.connect(self._enchant_tick)

    def _enchant_tick(self):
        self._enchant_phase += 1
        ph = self._enchant_phase
        dots = "." * (1 + (ph // 3) % 3)
        t0 = self._enchant_t0.get(self._active_tab_id, 0.0)
        secs = int(time.time() - t0) if t0 else 0
        self._enchant_fit(len(str(secs)))
        self._enchant_text.setText(f"{t('chat.enchanting')}{dots}   {secs}s")
        tri = (ph % 48) / 24.0
        self._enchant_sweep.set_pos(tri if tri <= 1.0 else 2.0 - tri)

    def _enchant_fit(self, digits):
        lb = self._enchant_text
        key = (digits, lb.font().key())
        if getattr(self, "_enchant_fit_key", None) == key:
            return
        self._enchant_fit_key = key
        lb.ensurePolished()
        fm = lb.fontMetrics()
        widest = f"{t('chat.enchanting')}...   {'9' * digits}s"
        lb.setFixedSize(fm.horizontalAdvance(widest) + 2 * lb.margin() + 4,
                        max(lb.sizeHint().height(), fm.height()))

    def _busy_now(self):
        return bool(self._task_running or
                    self._active_tab_id in self._session_waiting)

    def _set_enchanting(self, on):
        bar = getattr(self, "_enchant_bar", None)
        if bar is None:
            return
        if on:
            self._enchant_t0.setdefault(self._active_tab_id, time.time())
            self._enchant_phase = 0
            bar.setVisible(True)
            self._enchant_timer.start()
            self._enchant_tick()
            self._chat_scroll_bottom()
        else:
            self._enchant_timer.stop()
            bar.setVisible(False)

    def _setup_input_area(self, parent_layout):
        self._setup_enchant_bar(parent_layout)
        input_widget = QWidget()
        input_widget.setObjectName("input_area")
        il = QHBoxLayout(input_widget)
        il.setContentsMargins(16, 12, 16, 12)

        self.entry = ChatInput()
        self.entry.setPlaceholderText(t("gui.chat_placeholder"))
        self.entry.returnPressed.connect(self.send_message)
        il.addWidget(self.entry, 1)

        self.send_btn = QPushButton(t("chat.send"))
        self.send_btn.setObjectName("send_button")
        self.send_btn.clicked.connect(lambda: self.send_message())
        il.addWidget(self.send_btn)

        self.stop_btn = QPushButton(t("btn.stop"))
        self.stop_btn.setObjectName("stop_button")
        self.stop_btn.clicked.connect(self.request_stop)
        self.stop_btn.setVisible(False)
        il.addWidget(self.stop_btn)

        self.upload_btn = QPushButton(t("notes.load_rag"))
        self.upload_btn.setToolTip(t("ui.upload_tip"))
        self.upload_btn.clicked.connect(self.on_upload_clicked)
        il.addWidget(self.upload_btn)

        self.token_label = QLabel("— / —")
        self.token_label.setObjectName("token_label")
        self.token_label.setToolTip(t("ui.token_tip"))
        il.addWidget(self.token_label)

        self.queue_label = QLabel("")
        self.queue_label.setObjectName("queue_label")
        self.queue_label.setWordWrap(True)
        self.queue_label.setVisible(False)
        self.queue_label.setTextFormat(Qt.TextFormat.RichText)
        self.queue_label.setOpenExternalLinks(False)
        self.queue_label.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.queue_label.linkActivated.connect(self._queue_link)
        self.queue_label.setStyleSheet(
            "#queue_label { background:transparent; color:#fbbf24; "
            "border:none; padding:6px 10px; }")
        self.queue_box = QScrollArea()
        self.queue_box.setObjectName("queue_box")
        self.queue_box.setFrameShape(QFrame.Shape.NoFrame)
        self.queue_box.setStyleSheet(
            "#queue_box { background:#1e293b; border:1px solid #f59e0b; border-radius:8px; } "
            "#queue_box > QWidget > QWidget { background:transparent; }")
        self.queue_box.setWidgetResizable(True)
        self.queue_box.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.queue_box.setMaximumHeight(170)
        self.queue_box.setWidget(self.queue_label)
        self.queue_box.setVisible(False)
        parent_layout.addWidget(self.queue_box)
        parent_layout.addWidget(input_widget)


    def _setup_todo_page(self, parent_layout):
        header = QHBoxLayout()
        header.setContentsMargins(16, 12, 16, 12)
        header.addWidget(QLabel(f"<b>{t('todo.title')}</b>"))
        header.addStretch()
        refresh_btn = QPushButton(t("cron.update"))
        refresh_btn.clicked.connect(self.refresh_todo_lists)
        header.addWidget(refresh_btn)
        parent_layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        parent_layout.addWidget(splitter, 1)

        proj_widget = QWidget()
        proj_layout = QVBoxLayout(proj_widget)
        proj_layout.setContentsMargins(8, 8, 8, 8)
        self._cal_btn = QPushButton(t("cal.title"))
        self._cal_btn.setCheckable(True)
        self._cal_btn.setIcon(self._svg_icon("calendar", "#e6edf7", 16))
        self._cal_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cal_btn.setStyleSheet(
            "QPushButton{text-align:left;background:transparent;border:1px solid transparent;"
            "border-radius:9px;padding:8px 10px;font-size:13px;font-weight:600;"
            "color:#f1f5f9;min-height:0;}"
            "QPushButton:hover{border-color:#2a3547;}"
            "QPushButton:checked{background:#1d2a44;border-color:#3b82f6;font-weight:700;}")
        self._cal_btn.clicked.connect(self._todo_show_calendar)
        proj_layout.addWidget(self._cal_btn)
        proj_layout.addSpacing(10)
        proj_layout.addWidget(QLabel(t("lbl.projects")))

        proj_btns = QHBoxLayout()
        new_proj_btn = QPushButton()
        new_proj_btn.setIcon(self._svg_icon("plus", "#e6edf7", 18))
        new_proj_btn.setToolTip(t("btn.new_project"))
        new_proj_btn.setMaximumWidth(40)
        new_proj_btn.clicked.connect(self._todo_add_project)
        proj_btns.addWidget(new_proj_btn)
        rename_proj_btn = QPushButton()
        rename_proj_btn.setIcon(self._svg_icon("edit", "#e6edf7", 18))
        rename_proj_btn.setToolTip(t("btn.rename_project"))
        rename_proj_btn.setMaximumWidth(40)
        rename_proj_btn.clicked.connect(self._todo_rename_project)
        proj_btns.addWidget(rename_proj_btn)
        del_proj_btn = QPushButton()
        del_proj_btn.setIcon(self._svg_icon("trash", "#fca5a5", 18))
        del_proj_btn.setToolTip(t("ui.del_project_tip"))
        del_proj_btn.setMaximumWidth(40)
        del_proj_btn.clicked.connect(self._todo_delete_project)
        proj_btns.addWidget(del_proj_btn)
        proj_btns.addStretch()
        proj_layout.addLayout(proj_btns)

        self.todo_projects_list = QListWidget()
        self.todo_projects_list.currentRowChanged.connect(self._on_project_selected)
        self.todo_projects_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.todo_projects_list.customContextMenuRequested.connect(self._project_context_menu)
        proj_layout.addWidget(self.todo_projects_list, 1)

        splitter.addWidget(proj_widget)

        task_widget = QWidget()
        task_layout = QVBoxLayout(task_widget)
        task_layout.setContentsMargins(8, 8, 8, 8)
        task_header = QHBoxLayout()
        task_header.addWidget(QLabel(f"<b>📋 {t('todo.tasks')}</b>"))
        task_header.addStretch()
        new_task_btn = QPushButton(t("todo.new_task"))
        new_task_btn.setIcon(self._svg_icon("plus", "#e6edf7", 16))
        new_task_btn.clicked.connect(self._new_task_dialog)
        task_header.addWidget(new_task_btn)
        task_layout.addLayout(task_header)
        self.tasks_list = QListWidget()
        self.tasks_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self.tasks_list,
                  activated=lambda: self._delete_selected_tasks())
        task_layout.addWidget(self.tasks_list, 1)

        from PyQt6.QtWidgets import QStackedWidget
        right_holder = QWidget()
        rh = QVBoxLayout(right_holder)
        rh.setContentsMargins(0, 0, 0, 0)
        rh.setSpacing(0)
        sub_bar = QHBoxLayout()
        sub_bar.setContentsMargins(8, 6, 8, 6)
        sub_bar.setSpacing(6)
        self._proj_sub_btns = []
        for i, (ico, key) in enumerate((("📋", "proj.sub_list"),
                                        ("🧩", "proj.sub_board"),
                                        ("🕸", "proj.sub_tree"),
                                        ("🕒", "proj.sub_timeline"))):
            b = QPushButton(f"{ico} {t(key)}")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton{background:#131a29;border:1px solid #2a3547;"
                "border-radius:9px;padding:5px 14px;color:#e6edf7;font-size:12px;}"
                "QPushButton:hover{border-color:#3b82f6;color:#e6edf7;}"
                "QPushButton:checked{background:#1d2a44;border-color:#3b82f6;"
                "color:#e6edf7;font-weight:700;}")
            b.clicked.connect(lambda _c, n=i: self._proj_subtab(n))
            sub_bar.addWidget(b)
            self._proj_sub_btns.append(b)
        sub_bar.addStretch()
        rh.addLayout(sub_bar)
        self._proj_stack = _ShrinkStack()
        self._proj_stack.addWidget(task_widget)
        try:
            from projects_widget import ProjectsBoardWidget
            self._proj_board = ProjectsBoardWidget()
            self._proj_board.set_labels(self._projects_labels())
            self._proj_board.edits.connect(self._on_board_edits)
            self._proj_stack.addWidget(self._proj_board)
        except Exception as e:
            self._proj_board = None
            print(f"[projects] board unavailable: {e}", file=sys.stderr)
        rh.addWidget(self._proj_stack, 1)
        self._proj_sub_btns[0].setChecked(True)
        self._proj_view = "list"
        self._todo_right = _ShrinkStack()
        self._todo_right.addWidget(right_holder)
        self._cal_active = False
        self._cal_last_apts = []
        self._todo_project_names = []
        self.appointments_list = None
        try:
            from calendar_widget import CalendarWidget, labels as _cal_labels
            self._calendar = CalendarWidget()
            self._calendar.set_labels(_cal_labels(t))
            self._calendar.action.connect(self._on_calendar_action)
            self._todo_right.addWidget(self._calendar)
        except Exception as e:
            self._calendar = None
            print(f"[calendar] page unavailable: {e}", file=sys.stderr)
            apt_widget = QWidget()
            apt_layout = QVBoxLayout(apt_widget)
            apt_layout.setContentsMargins(8, 8, 8, 8)
            apt_header = QHBoxLayout()
            apt_header.addWidget(QLabel(f"<b>{t('todo.appointments')}</b>"))
            apt_header.addStretch()
            pending_apt_btn = QPushButton(t("fed.pending_appts"))
            pending_apt_btn.setToolTip(t("fed.pending_appts_tip"))
            pending_apt_btn.clicked.connect(self._show_pending_appointments)
            apt_header.addWidget(pending_apt_btn)
            new_apt_btn = QPushButton(t("todo.new_apt"))
            new_apt_btn.setIcon(self._svg_icon("plus", "#e6edf7", 16))
            new_apt_btn.clicked.connect(lambda _c=False: self._new_apt_dialog())
            apt_header.addWidget(new_apt_btn)
            apt_layout.addLayout(apt_header)
            self.appointments_list = QListWidget()
            self.appointments_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.appointments_list.customContextMenuRequested.connect(self._apt_context_menu)
            apt_layout.addWidget(self.appointments_list, 1)
            self._todo_right.addWidget(apt_widget)
        splitter.addWidget(self._todo_right)

        splitter.setSizes([200, 700])

        self._selected_project = None
        self.refresh_todo_lists()


    _CODE_VIEWS = (("\u00b1",     "code.act_changes", "code.view_changes"),
                   ("\U0001F4C1", "code.act_files",   "code.view_tree"),
                   ("\u0192",     "code.act_symbols", "code.view_symbols"),
                   ("\u2713",     "code.act_verify",  "code.view_verify"),
                   ("\U0001F558", "code.act_history", "code.view_history"),
                   ("\U0001F50D", "code.act_search",  "code.view_search"),
                   ("\U0001F91D", "code.act_cowork",  "code.view_cowork"),
                   ("\U0001F9ED", "code.act_whiteboard", "code.view_whiteboard"))

    def _setup_code_page(self, parent_layout):
        self._code_project = ""
        self._code_diff_cache = {}

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        act = QWidget()
        act.setFixedWidth(64)
        act.setStyleSheet("background:#0b1220; border-right:1px solid #1f2937;")
        al = QVBoxLayout(act)
        al.setContentsMargins(0, 8, 0, 8)
        al.setSpacing(2)
        self._code_act_btns = []
        for i, (ico, cap, tip) in enumerate(self._CODE_VIEWS):
            b = QPushButton()
            b.setFixedSize(64, 52)
            b.setToolTip(t(tip))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            _bl = QVBoxLayout(b)
            _bl.setContentsMargins(0, 3, 0, 3)
            _bl.setSpacing(0)
            for _txt, _name in ((ico, "ico"), (t(cap), "cap")):
                _l = QLabel(_txt)
                _l.setObjectName(_name)
                _l.setAlignment(Qt.AlignmentFlag.AlignCenter)
                _l.setAttribute(
                    Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                _bl.addWidget(_l)
            b.clicked.connect(lambda _c, n=i: self._code_activity(n))
            al.addWidget(b)
            self._code_act_btns.append(b)
        al.addStretch()
        body.addWidget(act)

        self._code_main = _ShrinkStack()
        _work = QWidget()
        work = QHBoxLayout(_work)
        work.setContentsMargins(0, 0, 0, 0)
        work.setSpacing(0)

        self._code_side = QWidget()
        self._code_side.setFixedWidth(250)
        self._code_side.setStyleSheet(
            "background:#0d1526; border-right:1px solid #1f2937;")
        sv = QVBoxLayout(self._code_side)
        sv.setContentsMargins(10, 10, 8, 10)
        sv.setSpacing(6)
        self.code_side_head = QPushButton(t("code.side_none"))
        self.code_side_head.setCursor(Qt.CursorShape.PointingHandCursor)
        self.code_side_head.clicked.connect(self._code_projects_dialog)
        self.code_side_head.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.code_side_head.customContextMenuRequested.connect(
            lambda p: self._code_project_menu(
                self.code_side_head.mapToGlobal(p)))
        sv.addWidget(self.code_side_head)
        self.code_left_stack = _ShrinkStack()
        sv.addWidget(self.code_left_stack, 1)

        _FIELD = ("QLineEdit { background:#0f172a; color:#e6edf7; border:1px "
                  "solid #334155; border-radius:6px; padding:4px 8px; }")

        def _title(text):
            lb = QLabel(text)
            lb.setStyleSheet("QLabel { color:#e6edf7; font-size:12px; "
                             "letter-spacing:1px; }")
            return lb

        def _btn(label, slot, green=False):
            b = QPushButton(label)
            b.setStyleSheet(
                ("QPushButton { background:%s; color:%s; border:1px solid %s; "
                 "border-radius:8px; padding:5px 12px; font-size:12px; "
                 "font-weight:600; } "
                 "QPushButton:disabled { background:#111a2b; color:#44536b; "
                 "border-color:#25334a; }") %
                (("#065f46", "#d1fae5", "#10b981") if green
                 else ("#1e293b", "#cbd5e1", "#334155")))
            b.clicked.connect(slot)
            return b

        _pc = QWidget(); pc = QVBoxLayout(_pc)
        pc.setContentsMargins(0, 0, 0, 0); pc.setSpacing(6)
        pc.addWidget(_title(t("code.changed_files").upper()))
        self.code_only_mine = QCheckBox(t("code.only_mine"))
        self.code_only_mine.setChecked(True)
        self.code_only_mine.setToolTip(t("code.only_mine_hint"))
        self.code_only_mine.stateChanged.connect(
            lambda _st: self._code_refresh())
        pc.addWidget(self.code_only_mine)
        self.code_files = QListWidget()
        self.code_files.currentItemChanged.connect(self._code_show_diff)
        pc.addWidget(self.code_files, 1)
        _r = QHBoxLayout()
        _r.addWidget(_btn(t("code.refresh"), self._code_refresh))
        _r.addWidget(_btn(t("code.undo"), self._code_undo))
        pc.addLayout(_r)
        pc.addWidget(_btn("🧭 " + t("code.wb_btn"), self._code_whiteboard))
        self._code_keep_row = QWidget()
        keep = QVBoxLayout(self._code_keep_row)
        keep.setContentsMargins(0, 6, 0, 0); keep.setSpacing(4)
        self.code_msg = QLineEdit()
        self.code_msg.setPlaceholderText(t("code.commit_hint"))
        self.code_msg.setStyleSheet(_FIELD)
        self.code_msg.returnPressed.connect(self._code_commit)
        keep.addWidget(self.code_msg)
        keep.addWidget(_btn(t("code.commit"), self._code_commit, green=True))
        pc.addWidget(self._code_keep_row)
        self.code_left_stack.addWidget(_pc)

        _pt = QWidget(); pt = QVBoxLayout(_pt)
        pt.setContentsMargins(0, 0, 0, 0); pt.setSpacing(6)
        pt.addWidget(_title(t("code.all_files_label").upper()))
        self.code_project_edit = QLineEdit()
        self.code_project_edit.setPlaceholderText(t("code.project_hint"))
        self.code_project_edit.setStyleSheet(_FIELD)
        self.code_project_edit.returnPressed.connect(self._code_open_project)
        pt.addWidget(self.code_project_edit)
        _pr0 = QHBoxLayout(); _pr0.setSpacing(4)
        _pr0.addWidget(_btn(t("code.browse"), self._code_pick_project,
                            green=True))
        pt.addLayout(_pr0)
        _pr = QHBoxLayout(); _pr.setSpacing(4)
        self.code_open_btn = _btn(t("code.open"), self._code_open_project)
        _pr.addWidget(self.code_open_btn)
        self.code_project_edit.textChanged.connect(
            lambda _x: self._code_paint_side_head())
        self.code_close_btn = _btn(t("code.close_project"),
                                   self._code_close_project)
        self.code_close_btn.setToolTip(t("code.close_project_hint"))
        _pr.addWidget(self.code_close_btn)
        self.code_mem_btn = _btn("📓", self._code_memory_open)
        self.code_mem_btn.setToolTip(t("code.memory_hint"))
        self.code_mem_btn.setMaximumWidth(44)

        _pr.addWidget(self.code_mem_btn)
        pt.addLayout(_pr)
        self.code_tree = QTreeWidget()
        self.code_tree.setHeaderHidden(True)
        self.code_tree.itemActivated.connect(self._code_open_file)
        self.code_tree.itemClicked.connect(self._code_open_file)
        pt.addWidget(self.code_tree, 1)
        self.code_left_stack.addWidget(_pt)

        _po = QWidget(); po = QVBoxLayout(_po)
        po.setContentsMargins(0, 0, 0, 0); po.setSpacing(6)
        self.code_outline_label = _title(t("code.outline").upper())
        po.addWidget(self.code_outline_label)
        self.code_sym_find = QLineEdit()
        self.code_sym_find.setPlaceholderText(t("code.sym_find_ph"))
        self.code_sym_find.setStyleSheet(
            "QLineEdit { background:#0f172a; color:#e6edf7; border:1px solid "
            "#1f2937; border-radius:6px; padding:4px 8px; font-size:12px; }")
        self.code_sym_find.returnPressed.connect(self._code_find_symbol)
        self.code_sym_find.textChanged.connect(
            lambda _s: self._code_sym_clear_results())
        po.addWidget(self.code_sym_find)
        self.code_outline = QListWidget()
        self.code_outline.itemClicked.connect(self._code_goto_symbol)
        po.addWidget(self.code_outline, 1)
        self.code_left_stack.addWidget(_po)

        self.code_left_stack.addWidget(QWidget())

        _ph = QWidget(); ph = QVBoxLayout(_ph)
        ph.setContentsMargins(0, 0, 0, 0); ph.setSpacing(6)
        ph.addWidget(_title(t("code.history").upper()))
        self.code_hist_vs_now = QCheckBox(t("code.hist_vs_now"))
        self.code_hist_vs_now.setToolTip(t("code.hist_vs_now_hint"))
        self.code_hist_vs_now.stateChanged.connect(
            lambda _x: self._code_show_commit(self.code_hist.currentItem()))
        ph.addWidget(self.code_hist_vs_now)
        self.code_hist = QListWidget()
        self.code_hist.currentItemChanged.connect(self._code_show_commit)
        ph.addWidget(self.code_hist, 1)
        ph.addWidget(_btn(t("code.refresh"), self._code_load_history))
        self.code_left_stack.addWidget(_ph)

        _ps = QWidget(); ps = QVBoxLayout(_ps)
        ps.setContentsMargins(0, 0, 0, 0); ps.setSpacing(6)
        ps.addWidget(_title(t("code.search").upper()))
        self.code_search_box = QLineEdit()
        self.code_search_box.setPlaceholderText(t("code.search_hint"))
        self.code_search_box.setStyleSheet(_FIELD)
        self.code_search_box.returnPressed.connect(self._code_search_run)
        ps.addWidget(self.code_search_box)
        self.code_search_count = QLabel("")
        self.code_search_count.setStyleSheet(
            "QLabel { color:#e6edf7; font-size:12px; }")
        self.code_search_count.setWordWrap(True)
        ps.addWidget(self.code_search_count)
        self.code_search_list = QListWidget()
        self.code_search_list.itemClicked.connect(self._code_search_open)
        self.code_search_list.itemActivated.connect(self._code_search_open)
        ps.addWidget(self.code_search_list, 1)
        self.code_left_stack.addWidget(_ps)
        self.code_left_stack.addWidget(QWidget())
        self.code_left_stack.addWidget(QWidget())
        work.addWidget(self._code_side)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        self.code_tabs = QTabBar()
        self.code_tabs.setExpanding(False)
        self.code_tabs.setDrawBase(False)
        self.code_tabs.addTab(t("code.tab_diff"))
        self.code_tabs.setTabsClosable(True)
        self.code_tabs.setMovable(True)
        self._code_seal_diff_tab()
        self.code_tabs.tabMoved.connect(self._code_tab_moved)
        self.code_tabs.tabCloseRequested.connect(self._code_close_tab)
        self.code_tabs.currentChanged.connect(self._code_tab_clicked)
        self.code_tabs.setStyleSheet(
            "QTabBar { background:#0b1220; } "
            "QTabBar::tab { background:transparent; color:#e6edf7; "
            "padding:7px 14px; border-top:2px solid transparent; } "
            "QTabBar::tab:selected { background:#0f172a; color:#e6edf7; "
            "border-top:2px solid #3b82f6; }")
        rv.addWidget(self.code_tabs)

        self.code_right_stack = _ShrinkStack()
        self.code_diff = QTextEdit()
        self.code_diff.setReadOnly(True)
        self.code_diff.viewport().setMouseTracking(True)
        self.code_diff.viewport().installEventFilter(self)
        _diff_css = ("QTextEdit { background:#0f172a; color:#e6edf7; "
                     "border:none; padding:8px; }")
        self.code_diff.setStyleSheet(_diff_css)
        self._zoom_targets = getattr(self, "_zoom_targets", [])
        self._zoom_targets.append((self.code_diff, _diff_css, 0))
        self.code_right_stack.addWidget(self.code_diff)
        _edw = QWidget(); _ev = QVBoxLayout(_edw)
        _ev.setContentsMargins(0, 0, 0, 0)
        self.code_open_label = QLabel(t("code.no_file"))
        self.code_open_label.setStyleSheet(
            "QLabel { color:#e6edf7; font-family:monospace; font-size:12px; }")
        _ev.addWidget(self.code_open_label)
        _fb = QHBoxLayout()
        _fb.setContentsMargins(0, 2, 0, 2)
        _fb.setSpacing(4)
        self.code_find = QLineEdit()
        self.code_find.setPlaceholderText(t("code.find_ph"))
        self.code_find.setClearButtonEnabled(True)
        self.code_find.setStyleSheet(
            "QLineEdit { background:#0f172a; color:#e6edf7; border:1px solid "
            "#334155; border-radius:6px; padding:3px 8px; font-family:monospace; }")
        self.code_find.textChanged.connect(lambda _t: self._code_find_run(0))
        _fb.addWidget(self.code_find, 1)

        def _mini(text, tip, slot, checkable=False):
            _b = QPushButton(text)
            _b.setToolTip(tip)
            _b.setCheckable(checkable)
            _b.setFixedWidth(30)
            _b.setStyleSheet(
                "QPushButton { background:#1e293b; color:#cbd5e1; border:1px "
                "solid #334155; border-radius:6px; padding:3px 0; }"
                "QPushButton:checked { background:#1d4ed8; color:#eff6ff; "
                "border-color:#3b82f6; }")
            _b.clicked.connect(slot)
            _fb.addWidget(_b)
            return _b

        self.code_find_case = _mini(
            "Aa", t("code.find_case"), lambda: self._code_find_run(0), True)
        self.code_find_re = _mini(
            ".*", t("code.find_re"), lambda: self._code_find_run(0), True)
        _mini("◀", t("code.find_prev"), self._code_find_prev)
        _mini("▶", t("code.find_next"), self._code_find_next)
        self.code_find_count = QLabel("")
        self.code_find_count.setMinimumWidth(110)
        self.code_find_count.setStyleSheet(
            "QLabel { color:#e6edf7; font-size:12px; }")
        _fb.addWidget(self.code_find_count)
        self.code_find_toggle = _mini(
            "⇄", t("code.replace_toggle"), self._code_replace_toggle, True)
        self.code_goto = QLineEdit()
        self.code_goto.setPlaceholderText(t("code.goto_ph"))
        self.code_goto.setToolTip(t("code.goto_tip"))
        self.code_goto.setFixedWidth(74)
        self.code_goto.setStyleSheet(
            "QLineEdit { background:#0d1526; color:#e6edf7; "
            "border:1px solid #24304a; border-radius:6px; padding:2px 6px; "
            "font-family:monospace; font-size:12px; }")
        self.code_goto.returnPressed.connect(self._code_goto_go)
        _fb.addWidget(self.code_goto)
        _mini("↵", t("code.goto_tip"), self._code_goto_go)
        _ev.addLayout(_fb)

        self.code_replace_row = QWidget()
        _rb = QHBoxLayout(self.code_replace_row)
        _rb.setContentsMargins(0, 0, 0, 2)
        _rb.setSpacing(4)
        self.code_replace = QLineEdit()
        self.code_replace.setPlaceholderText(t("code.replace_ph"))
        self.code_replace.setStyleSheet(self.code_find.styleSheet())
        _rb.addWidget(self.code_replace, 1)
        for _txt, _slot in ((t("code.replace_one"), self._code_replace_one),
                            (t("code.replace_all"), self._code_replace_all)):
            _b = QPushButton(_txt)
            _b.setStyleSheet(
                "QPushButton { background:#1e293b; color:#cbd5e1; border:1px "
                "solid #334155; border-radius:6px; padding:3px 12px; }")
            _b.clicked.connect(_slot)
            _rb.addWidget(_b)
        self.code_replace_row.setVisible(False)
        _ev.addWidget(self.code_replace_row)
        self._code_find_hits = []
        self._code_find_at = -1

        self.code_editor = CodeEdit()
        self.code_editor.setAcceptRichText(False)
        self.code_editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        _f = QFont("Consolas" if IS_WINDOWS else "Monospace", 11)
        _f.setStyleHint(QFont.StyleHint.Monospace)
        self.code_editor.setFont(_f)
        self.code_editor.setTabStopDistance(
            4 * self.code_editor.fontMetrics().horizontalAdvance(" "))
        _ed_css = ("QTextEdit { background:#0f172a; color:#e6edf7; "
                   "border:1px solid #334155; border-radius:6px; padding:8px; }")
        self.code_editor.setStyleSheet(_ed_css)
        self._zoom_targets.append((self.code_editor, _ed_css, 1))
        self.code_editor.viewport().installEventFilter(self)
        getattr(self, "_apply_zoom", lambda: None)()
        self._code_hl = PygmentsHighlighter(self.code_editor.document())
        self._code_open_path = ""
        self._code_dirty = False
        self._code_diag_timer = QTimer(self)
        self._code_diag_timer.setSingleShot(True)
        self._code_diag_timer.setInterval(700)
        self._code_diag_timer.timeout.connect(self._code_run_diagnostics)
        self._code_diag_timer.timeout.connect(self._code_run_outline)
        self._code_buf_rev = 0
        self.code_editor.textChanged.connect(self._code_editor_changed)
        _ev.addWidget(self.code_editor, 1)
        from PyQt6.QtGui import QShortcut, QKeySequence
        for _seq, _slot in (("Ctrl+G", lambda: (self.code_goto.setFocus(),
                                                self.code_goto.selectAll())),
                            ("Ctrl+N", self._code_new_file)):
            _sc = QShortcut(QKeySequence(_seq), self.code_editor)
            _sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            _sc.activated.connect(_slot)
        _sv = QHBoxLayout()
        _sv.addStretch()
        _savebtn = QPushButton(t("code.save_file"))
        _savebtn.setStyleSheet(
            "QPushButton { background:#065f46; color:#d1fae5; border:1px solid #10b981; "
            "border-radius:8px; padding:5px 16px; font-weight:700; }")
        _savebtn.clicked.connect(self._code_save_file)
        _sv.addWidget(_savebtn)
        _ev.addLayout(_sv)
        self.code_right_stack.addWidget(_edw)
        for _seq, _slot in (("Ctrl+S", self._code_save_file),
                            ("Ctrl+F", self._code_find_focus),
                            ("F3", self._code_find_next),
                            ("Shift+F3", self._code_find_prev),
                            ("Ctrl+H", lambda: self._code_replace_toggle(True))):
            _s = QShortcut(QKeySequence(_seq), _edw)
            _s.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            _s.activated.connect(_slot)
        self.code_find.installEventFilter(self)
        self.code_replace.installEventFilter(self)
        rv.addWidget(self.code_right_stack, 1)
        work.addWidget(right, 1)
        self._code_main.addWidget(_work)
        self._code_main.addWidget(
            self._code_build_run_page(_title, _btn, _FIELD, _diff_css))
        self._code_main.addWidget(
            self._code_build_cowork_page(_title, _btn))
        self._code_main.addWidget(
            self._code_build_wb_page(_btn))
        body.addWidget(self._code_main, 1)
        parent_layout.addLayout(body, 1)

        stbar = QWidget()
        stbar.setFixedHeight(26)
        stbar.setStyleSheet("background:#1d4ed8;")
        sb = QHBoxLayout(stbar)
        sb.setContentsMargins(10, 0, 10, 0)
        sb.setSpacing(14)
        _ST = "QLabel { color:#eff6ff; font-size:12px; }"
        self.code_branch = QLabel("")
        self.code_branch.setStyleSheet(
            "QLabel { color:#eff6ff; font-family:monospace; font-size:12px; }")
        sb.addWidget(self.code_branch)
        self.code_mode_badge = QPushButton("")
        self.code_mode_badge.setCursor(Qt.CursorShape.PointingHandCursor)
        self.code_mode_badge.setFlat(True)
        self.code_mode_badge.setStyleSheet(
            "QPushButton { color:#eff6ff; font-size:12px; border:none; "
            "background:transparent; padding:0; text-align:left; } "
            "QPushButton:hover { text-decoration:underline; }")
        self.code_mode_badge.setVisible(False)
        self.code_mode_badge.clicked.connect(self._code_show_toolchain)
        sb.addWidget(self.code_mode_badge)
        self.code_verify_badge = QLabel("")
        self.code_verify_badge.setStyleSheet(
            "QLabel { color:#eff6ff; font-family:monospace; font-size:12px; }")
        sb.addWidget(self.code_verify_badge)
        self.code_status = QLabel("")
        self.code_status.setStyleSheet(_ST)
        sb.addWidget(self.code_status, 1)
        parent_layout.addWidget(stbar)

        self._code_view = 0
        self._code_side_open = True
        self._code_rail_used = False
        self._code_prev_view = 0
        self._code_welcome()

    def _code_build_run_page(self, _title, _btn, _FIELD, _console_css):
        page = QWidget()
        pv = QVBoxLayout(page)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)

        head = QWidget()
        head.setStyleSheet(
            "background:#0b1220; border-bottom:1px solid #1f2937;")
        hb = QHBoxLayout(head)
        hb.setContentsMargins(14, 8, 14, 8)
        hb.setSpacing(10)
        _h1 = QLabel("▶  " + t("code.run_title"))
        _h1.setStyleSheet(
            "QLabel { color:#e6edf7; font-size:13px; font-weight:700; "
            "letter-spacing:1px; }")
        hb.addWidget(_h1)
        self.code_run_where = QLabel("")
        self.code_run_where.setStyleSheet(
            "QLabel { color:#e6edf7; font-family:monospace; font-size:12px; }")
        hb.addWidget(self.code_run_where)
        hb.addStretch()
        pv.addWidget(head)

        form = QWidget()
        form.setStyleSheet("background:#0d1526; border-bottom:1px solid #1f2937;")
        fv = QVBoxLayout(form)
        fv.setContentsMargins(14, 12, 14, 12)
        fv.setSpacing(6)
        _big = _FIELD + " QLineEdit { font-size:13px; padding:8px 10px; }"
        fv.addWidget(_title(t("code.run_cmd_label").upper()))
        self.code_verify = QLineEdit()
        self.code_verify.setPlaceholderText(t("code.verify_hint"))
        self.code_verify.setToolTip(t("code.verify_tip"))
        self.code_verify.setStyleSheet(_big)
        self.code_verify.editingFinished.connect(self._code_verify_store)
        self.code_verify.returnPressed.connect(self._code_verify_run)
        fv.addWidget(self.code_verify)
        fv.addSpacing(4)
        fv.addWidget(_title(t("code.run_expect_label").upper()))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.code_verify_goal = QLineEdit()
        self.code_verify_goal.setPlaceholderText(t("code.check_goal_hint"))
        self.code_verify_goal.setToolTip(t("code.check_goal_tip"))
        self.code_verify_goal.setStyleSheet(_big)
        self.code_verify_goal.editingFinished.connect(
            self._code_check_goal_store)
        self.code_verify_goal.returnPressed.connect(self._code_verify_run)
        row.addWidget(self.code_verify_goal, 1)
        self.code_run_btn = _btn(t("code.run_go"), self._code_verify_run,
                                 green=True)
        self.code_run_btn.setToolTip(t("code.run_go_tip"))
        self.code_run_btn.setMinimumHeight(34)
        row.addWidget(self.code_run_btn)
        fv.addLayout(row)
        self.code_verify_goal_state = QLabel("")
        self.code_verify_goal_state.setWordWrap(True)
        self.code_verify_goal_state.setStyleSheet(
            "QLabel { color:#e6edf7; font-size:12px; }")
        fv.addWidget(self.code_verify_goal_state)
        pv.addWidget(form)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setStyleSheet("QSplitter::handle { background:#1f2937; }")
        split.setHandleWidth(1)

        left = QWidget()
        left.setStyleSheet("background:#0d1526;")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(12, 10, 10, 10)
        lv.setSpacing(6)
        lv.addWidget(_title(t("code.run_prev").upper()))
        self.code_verify_list = QListWidget()
        self.code_verify_list.setToolTip(t("code.run_prev_tip"))
        self.code_verify_list.itemClicked.connect(self._code_run_pick)
        lv.addWidget(self.code_verify_list, 1)
        self.code_run_hist = self.code_verify_list
        split.addWidget(left)

        right = QWidget()
        right.setStyleSheet("background:#0f172a;")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        self.code_run_banner = QLabel(t("code.run_idle"))
        self.code_run_banner.setWordWrap(True)
        self.code_run_banner.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._code_run_banner_paint(None)
        rv.addWidget(self.code_run_banner)
        self.code_run_out = QTextEdit()
        self.code_run_out.setReadOnly(True)
        self.code_run_out.setStyleSheet(_console_css)
        self._zoom_targets.append((self.code_run_out, _console_css, 0))
        self.code_run_out.viewport().installEventFilter(self)
        rv.addWidget(self.code_run_out, 1)
        _cb = QHBoxLayout()
        _cb.setContentsMargins(10, 6, 10, 8)
        _cb.setSpacing(6)
        _cb.addStretch()
        _cb.addWidget(_btn(t("code.run_clear"), self._code_run_clear))
        self.code_run_chat_btn = _btn(t("code.run_to_chat"),
                                      self._code_run_to_chat)
        self.code_run_chat_btn.setToolTip(t("code.run_to_chat_tip"))
        _cb.addWidget(self.code_run_chat_btn)
        rv.addLayout(_cb)
        split.addWidget(right)

        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([300, 1000])
        left.setMinimumWidth(220)
        pv.addWidget(split, 1)

        self._code_run_by_proj = {}
        self._code_running = False
        self._code_run_batch = {}
        return page

    def _code_activity(self, idx, force=False):
        if getattr(self, "code_left_stack", None) is None:
            return
        run_page = idx in (3, 6, 7)
        _rail_used = getattr(self, "_code_rail_used", False)
        if not force:
            self._code_rail_used = True
        if run_page:
            if not force and self._code_view == idx:
                self._code_activity(self._code_prev_view, force=True)
                return
            self._code_view = idx
            self._code_main.setCurrentIndex({3: 1, 6: 2, 7: 3}[idx])
            if idx == 3:
                self._code_run_paint()
            elif idx == 6:
                self._code_cowork_paint()
            else:
                self._code_wb_paint()
        elif (not force and idx == self._code_view and self._code_side_open
                and _rail_used):
            self._code_side_open = False
            self._code_side.setVisible(False)
            self._code_main.setCurrentIndex(0)
        else:
            self._code_view = idx
            self._code_prev_view = idx
            self.code_left_stack.setCurrentIndex(idx)
            self._code_side_open = True
            self._code_side.setVisible(True)
            self._code_main.setCurrentIndex(0)
        if not run_page and self._code_side_open:
            if not self._code_project:
                want = 0
            elif idx == 0:
                want = 0
            else:
                want = 1
            if self.code_right_stack.currentIndex() != want:
                if want in (0, 1):
                    self.code_tabs.blockSignals(True)
                    self.code_tabs.setCurrentIndex(want)
                    self.code_tabs.blockSignals(False)
                self.code_right_stack.setCurrentIndex(want)
        if idx == 1 and self._code_side_open \
                and self.code_tree.topLevelItemCount() == 0:
            self._code_load_tree()
        if idx == 4 and self._code_side_open \
                and self.code_hist.count() == 0:
            self._code_load_history()
        for i, b in enumerate(self._code_act_btns):
            on = (i == self._code_view
                  and (run_page or self._code_side_open))
            col = "#e6edf7"
            b.setStyleSheet(
                "QPushButton { border:none; background:%s; "
                "border-left:2px solid %s; } "
                "QLabel#ico { font-size:17px; color:%s; } "
                "QLabel#cap { font-size:12px; letter-spacing:0.3px; color:%s; }"
                % ("#111c30" if on else "transparent",
                   "#3b82f6" if on else "transparent", col, col))
        self._code_paint_side_head()

    def _code_paint_side_head(self):
        b = getattr(self, "code_side_head", None)
        if b is None:
            return
        p = getattr(self, "_code_project", "")
        b.setText(("\U0001F4C2  " + os.path.basename(p.rstrip("/\\"))) if p
                  else "·  " + t("code.side_none"))
        b.setToolTip(p or t("code.no_project"))
        for _w, _on in ((getattr(self, "code_open_btn", None),
                         bool(self.code_project_edit.text().strip())),
                        (getattr(self, "code_close_btn", None), bool(p)),
                        (getattr(self, "code_mem_btn", None), bool(p))):
            if _w is not None:
                _w.setEnabled(_on)
        b.setStyleSheet(
            "QPushButton { text-align:left; padding:4px 8px; font-size:12px; "
            "border:none; border-radius:6px; background:%s; color:%s; "
            "font-weight:600; } QPushButton:hover { background:#16233c; }"
            % (("#132038", "#cfe0ff") if p else ("transparent", "#5b6b83")))

    @staticmethod
    def _split_hunk_blocks(file_diff):
        head, hunks, cur = [], [], None
        for line in (file_diff or "").splitlines(True):
            if line.startswith("@@"):
                cur = [line]
                hunks.append(cur)
            elif cur is None:
                head.append(line)
            else:
                cur.append(line)
        return "".join(head), ["".join(h) for h in hunks]

    def _code_diff_event(self, event):
        from PyQt6.QtCore import QEvent, Qt as _Qt
        et = event.type()
        if et not in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
            return False
        href = self.code_diff.anchorAt(event.position().toPoint())
        if et == QEvent.Type.MouseMove:
            self.code_diff.viewport().setCursor(
                _Qt.CursorShape.PointingHandCursor if href.startswith("hoplita:")
                else _Qt.CursorShape.IBeamCursor)
            return False
        if href == "hoplita:pick":
            self._code_pick_project()
            return True
        if href == "hoplita:allchanges":
            self.code_only_mine.setChecked(False)
            return True
        if href.startswith("hoplita:open:"):
            p = href[len("hoplita:open:"):]
            if not (self._proj_stat([p]).get(p) or {}).get("isdir"):
                self._code_remember_project(p, forget=True)
                self._code_welcome()
                return True
            self.code_project_edit.setText(p)
            self._code_open_project()
            return True
        if not href.startswith("hoplita:reject:"):
            return False
        try:
            _, _, idx, fname = href.split(":", 3)
            idx = int(idx)
        except ValueError:
            return False
        _, blocks = self._split_hunk_blocks(self._code_diff_cache.get(fname, ""))
        header = blocks[idx].splitlines()[0] if 0 <= idx < len(blocks) else ""
        ok, r, err = self._mcp_try(
            "code_reject_hunk", {"path": self._code_project, "file": fname,
                                 "hunk": idx, "header": header,
                                 "patch": blocks[idx] if 0 <= idx < len(blocks) else ""},
            timeout=30)
        self._code_refresh(then=lambda: self._code_say(
            t("code.rejected", file=fname, n=idx + 1) if ok
            else t("code.reject_fail", err=err), bad=not ok))
        return True

    def _code_verify_store(self):
        ctx = getattr(self, "_chat_ctxs", {}).get(getattr(self, "_active_tab_id", None))
        if ctx is not None:
            ctx.verify_cmd = self.code_verify.text().strip()
        self._code_check_goal_show()
        self._code_verify_paint()

    def _code_verify_key(self):
        import hashlib
        p = self._code_project or "global"
        return "verify_cmds/" + hashlib.sha1(p.encode()).hexdigest()[:16]

    def _code_verify_load_list(self):
        from PyQt6.QtCore import QSettings
        try:
            raw = QSettings("hoplita", "hoplita").value(
                self._code_verify_key()) or "[]"
            lst = json.loads(raw)
        except Exception:
            lst = []
        self._code_verify_shelf = [c for c in lst
                                   if isinstance(c, str) and c.strip()]
        self._code_verify_paint()

    def _code_verify_save_list(self):
        from PyQt6.QtCore import QSettings
        try:
            QSettings("hoplita", "hoplita").setValue(
                self._code_verify_key(),
                json.dumps(getattr(self, "_code_verify_shelf", [])))
        except Exception:
            pass

    def _code_check_goal_key(self, proj=None):
        import hashlib as _hl
        p = proj if proj is not None else (self._code_project or "")
        return "verify_goals/" + _hl.sha1(
            (p or "global").encode()).hexdigest()[:16]

    def _code_check_goals(self, proj=None):
        p = proj if proj is not None else (self._code_project or "")
        cache = getattr(self, "_code_check_goals_cache", None)
        if cache is None:
            cache = self._code_check_goals_cache = {}
        if p not in cache:
            from PyQt6.QtCore import QSettings
            try:
                raw = QSettings("hoplita", "hoplita").value(
                    self._code_check_goal_key(p)) or "{}"
                g = json.loads(raw)
            except Exception:
                g = {}
            cache[p] = g if isinstance(g, dict) else {}
        return cache[p]

    def _code_check_goal_of(self, cmd, proj=None):
        return (self._code_check_goals(proj).get((cmd or "").strip())
                or "").strip()

    def _code_check_goal_store(self):
        cmd = self.code_verify.text().strip()
        if not cmd:
            return
        goals = self._code_check_goals()
        txt = self.code_verify_goal.text().strip()
        if txt:
            goals[cmd] = txt
        else:
            goals.pop(cmd, None)
        from PyQt6.QtCore import QSettings
        try:
            QSettings("hoplita", "hoplita").setValue(
                self._code_check_goal_key(), json.dumps(goals))
        except Exception:
            pass
        self._code_check_goal_paint()
        self._code_verify_paint()

    def _code_check_goal_paint(self):
        lb = getattr(self, "code_verify_goal_state", None)
        if lb is None:
            return
        txt = self.code_verify_goal.text().strip()
        if not txt:
            lb.setText(t("code.check_goal_none"))
            col = "#e6edf7"
        else:
            lb.setText(t("code.check_goal_judged"))
            col = "#22d3ee"
        lb.setStyleSheet("QLabel { color:%s; font-size:12px; }" % col)

    def _turn_check_goal(self):
        try:
            ctx = self._turn_ctx()
            cmd = (getattr(ctx, "verify_cmd", "") or "").strip()
            if not cmd:
                return ""
            return self._code_check_goal_of(
                cmd, proj=(getattr(ctx, "code_project", "") or ""))
        except Exception:
            return ""

    def _code_check_goal_show(self):
        w = getattr(self, "code_verify_goal", None)
        if w is None:
            return
        w.setText(self._code_check_goal_of(self.code_verify.text()))
        self._code_check_goal_paint()

    def _code_verify_paint(self):
        lw = getattr(self, "code_verify_list", None)
        if lw is None:
            return
        lw.clear()
        seen = set()
        for i, e in enumerate(self._code_run_log()):
            it = QListWidgetItem("%s %s  %.1fs  %s" % (
                "✓" if e["ok"] else "✗", e["when"], e["secs"], e["cmd"]))
            it.setData(Qt.ItemDataRole.UserRole, e["cmd"])
            it.setData(Qt.ItemDataRole.UserRole + 1, i)
            _tip = t("code.run_last_tip", when=e["when"], code=e["code"],
                     secs=("%.1f" % e["secs"]))
            if e.get("why"):
                _tip += "\n" + e["why"]
            it.setToolTip(_tip)
            it.setForeground(QColor("#3fb950" if e["ok"] else "#f85149"))
            lw.addItem(it)
            seen.add(e["cmd"])
        for c in getattr(self, "_code_verify_shelf", []):
            if c in seen:
                continue
            goal = self._code_check_goal_of(c)
            it = QListWidgetItem("○ " + c + ("  \U0001F3AF" if goal else ""))
            it.setData(Qt.ItemDataRole.UserRole, c)
            it.setData(Qt.ItemDataRole.UserRole + 1, -1)
            it.setToolTip(c + ("\n\U0001F3AF " + goal if goal else ""))
            lw.addItem(it)

    def _code_run_hist_paint(self):
        self._code_verify_paint()

    def _code_verify_remember(self, cmd):
        cmd = (cmd or "").strip()
        if not cmd:
            return
        shelf = getattr(self, "_code_verify_shelf", None)
        if shelf is None:
            shelf = self._code_verify_shelf = []
        if cmd in shelf:
            shelf.remove(cmd)
        shelf.insert(0, cmd)
        del shelf[12:]
        self._code_verify_save_list()

    def _code_verify_pick(self, item):
        if item is None:
            return
        cmd = item.data(Qt.ItemDataRole.UserRole) or ""
        self.code_verify.setText(cmd)
        self._code_verify_store()
        self._code_say(t("code.verify_active", cmd=cmd))

    def _code_run_pick(self, item):
        if item is None:
            return
        idx = item.data(Qt.ItemDataRole.UserRole + 1)
        self._code_verify_pick(item)
        if isinstance(idx, int) and idx >= 0:
            self._code_run_replay(idx)

    def _code_verify_run(self):
        self._code_verify_store()
        self._code_run_start([self.code_verify.text().strip()])

    def _code_run_start(self, cmds):
        cmds = [c.strip() for c in cmds if c and c.strip()]
        if not cmds:
            self._code_say(t("code.verify_empty"), bad=True)
            return
        if not self._code_project:
            self._code_say(t("code.no_project"), bad=True)
            return
        if getattr(self, "_code_running", False):
            self._code_say(t("code.run_busy"), bad=True)
            return
        self._code_running = True
        self._code_run_batch = {"total": len(cmds), "ok": 0, "bad": 0}
        for c in cmds:
            self._code_verify_remember(c)
        for b in (getattr(self, "code_run_btn", None),):
            if b is not None:
                b.setEnabled(False)
        con = getattr(self, "code_run_out", None)
        if con is not None:
            con.clear()
        self._code_run_next(list(cmds))

    def _code_run_next(self, queue):
        if not queue:
            self._code_running = False
            for b in (getattr(self, "code_run_btn", None),):
                if b is not None:
                    b.setEnabled(True)
            bat = getattr(self, "_code_run_batch", {})
            if bat.get("total", 0) > 1:
                self._code_run_banner_paint(
                    bat["bad"] == 0,
                    note=t("code.run_all_done", n=bat["total"],
                           ok=bat["ok"], bad=bat["bad"]))
                self._code_say(t("code.run_all_done", n=bat["total"],
                                 ok=bat["ok"], bad=bat["bad"]),
                               bad=bat["bad"] > 0)
            return
        cmd = queue.pop(0)
        self.code_verify_badge.setText(t("code.verify_running"))
        self._code_run_banner_paint(None, note=t("code.run_running", cmd=cmd))
        t0 = time.time()
        proj = self._code_project
        expect = self._code_check_goal_of(cmd)
        tmo = self.config.getint("CHAT", "terminal_timeout", fallback=300) + 10

        def work():
            res = self._mcp_try("run_command",
                                {"command": cmd, "cwd": proj}, timeout=tmo)
            okk, r, _err = res if res else (False, {}, "?")
            judge = None
            if okk and expect and (r or {}).get("exit_code", 1) == 0:
                try:
                    jr = requests.post(
                        f"{self._tab_url()}/code/judge",
                        json={"command": cmd, "expect": expect,
                              "output": (((r or {}).get("stdout") or "")
                                         + ((r or {}).get("stderr") or ""))[-6000:]},
                        timeout=180)
                    j = jr.json() if jr.status_code == 200 else {}
                    if j.get("judged"):
                        judge = (bool(j.get("ok")), str(j.get("why") or ""))
                except Exception:
                    judge = None
            return res, judge

        def done(res):
            pair = res if isinstance(res, tuple) and len(res) == 2 else (res, None)
            base, judge = pair
            ok, r, err = base if base else (False, {}, "?")
            self._code_run_land(cmd, ok, r or {}, err, time.time() - t0,
                                expect=expect, judge=judge)
            self._code_run_next(queue)

        self._bg(work, done)

    def _code_run_land(self, cmd, ok, r, err, secs, expect="", judge=None):
        if not ok:
            self.code_verify_badge.setText("")
            self._code_say(t("code.verify_fail_run", err=err), bad=True)
            self._code_run_show(cmd, False, t("code.verify_fail_run", err=err),
                                code=-1, secs=secs)
            self._code_run_batch["bad"] = self._code_run_batch.get("bad", 0) + 1
            return
        code = r.get("exit_code", 1)
        out = ((r.get("stdout") or "") + (r.get("stderr") or "")).strip()
        if code == 127:
            out = t("code.verify_not_a_cmd", word=cmd.split()[0])
        elif len(out) > 6000:
            out = (t("code.verify_out_cut", n=len(out) - 6000) + "\n\n"
                   + out[-6000:])
        passed = code == 0
        why = ""
        if expect and code == 0:
            if judge is None:
                why = t("code.check_goal_unjudged", expect=expect)
            else:
                gok, jwhy = judge
                passed = passed and gok
                why = ("✓ " if gok else "✗ ") + (jwhy or expect)
            out = (out + "\n\n" if out else "") + "\U0001F3AF " + why
        if code == 127:
            self._code_say(out, bad=True)
        elif judge is not None and not judge[0] and code == 0:
            self._code_say(t("code.verify_fail_goal", cmd=cmd, why=why),
                           bad=True)
        else:
            self._code_say(t("code.verify_pass_msg", cmd=cmd) if passed
                           else t("code.verify_fail_msg", cmd=cmd, code=code),
                           bad=not passed)
        self._code_run_batch["ok" if passed else "bad"] = \
            self._code_run_batch.get("ok" if passed else "bad", 0) + 1
        if cmd == self.code_verify.text().strip():
            self.code_verify_badge.setText(
                t("code.verify_pass") if passed
                else t("code.verify_fail", code=code))
            self.code_verify_badge.setStyleSheet(
                f"QLabel {{ color:{'#3fb950' if passed else '#f85149'}; "
                f"font-family:monospace; font-size:12px; font-weight:700; }}")
        self._code_run_show(cmd, passed, out, code=code, secs=secs, why=why)

    def _code_run_log(self):
        by = getattr(self, "_code_run_by_proj", None)
        if by is None:
            by = self._code_run_by_proj = {}
        return by.setdefault(self._code_project or "-", [])

    def _code_run_last(self, cmd):
        for e in self._code_run_log():
            if e["cmd"] == cmd:
                return e
        return None

    def _code_run_show(self, cmd, passed, body, code=0, secs=0.0, why=""):
        entry = {"cmd": cmd, "ok": bool(passed), "code": code,
                 "secs": float(secs), "when": time.strftime("%H:%M:%S"),
                 "out": body or "", "why": why or ""}
        log = self._code_run_log()
        log.insert(0, entry)
        del log[40:]
        self._code_run_banner_paint(passed, entry=entry)
        con = getattr(self, "code_run_out", None)
        if con is not None:
            import html as _h
            con.append(
                f'<div style="color:{"#3fb950" if passed else "#f85149"};'
                f'font-family:monospace;font-weight:700;white-space:pre-wrap;">'
                f'{_h.escape("$ " + cmd)}   '
                f'{_h.escape(t("code.run_took", secs="%.1f" % secs))}</div>'
                + (f'<div style="color:{"#c8d6ea" if passed else "#f85149"};'
                   f'font-family:monospace;white-space:pre-wrap;">'
                   f'{_h.escape(body)}</div>' if body else "")
                + '<div style="color:#334155;">─</div>')
            con.verticalScrollBar().setValue(
                con.verticalScrollBar().maximum())
        self._code_verify_paint()

    def _code_run_banner_paint(self, passed, entry=None, note=""):
        b = getattr(self, "code_run_banner", None)
        if b is None:
            return
        if entry is not None and not note:
            _short = (not entry["ok"] and entry["code"] == 0
                      and entry.get("why"))
            note = ((t("code.verify_pass_msg", cmd=entry["cmd"]) if entry["ok"]
                     else (t("code.verify_fail_goal", cmd=entry["cmd"],
                             why=entry["why"]) if _short
                           else t("code.verify_fail_msg", cmd=entry["cmd"],
                                  code=entry["code"])))
                    + "   ·   " + t("code.run_took",
                                    secs="%.1f" % entry["secs"])
                    + "   ·   " + entry["when"])
            if entry.get("why") and not _short:
                note += "\n\U0001F3AF " + entry["why"]
        col, bg = (("#c8d6ea", "#111c30") if passed is None
                   else (("#3fb950", "#052e16") if passed
                         else ("#f85149", "#3f1111")))
        b.setText(note or t("code.run_idle"))
        b.setStyleSheet(
            f"QLabel {{ color:{col}; background:{bg}; font-family:monospace; "
            f"font-weight:700; padding:10px 12px; "
            f"border-bottom:1px solid #1f2937; }}")

    def _code_run_replay(self, item):
        if item is None:
            return
        log = self._code_run_log()
        i = item if isinstance(item, int) else item.data(
            Qt.ItemDataRole.UserRole + 1)
        if not isinstance(i, int) or not (0 <= i < len(log)):
            return
        e = log[i]
        self._code_run_banner_paint(e["ok"], entry=e)
        con = getattr(self, "code_run_out", None)
        if con is not None:
            import html as _h
            con.setHtml(
                f'<div style="color:#e6edf7;font-family:monospace;'
                f'white-space:pre-wrap;">{_h.escape(t("code.run_replay"))}'
                '</div>'
                f'<div style="color:{"#3fb950" if e["ok"] else "#f85149"};'
                f'font-family:monospace;font-weight:700;white-space:pre-wrap;">'
                f'{_h.escape("$ " + e["cmd"])}</div>'
                + (f'<div style="color:{"#c8d6ea" if e["ok"] else "#f85149"};'
                   f'font-family:monospace;white-space:pre-wrap;">'
                   f'{_h.escape(e["out"])}</div>' if e["out"] else ""))

    def _code_run_clear(self):
        con = getattr(self, "code_run_out", None)
        if con is not None:
            con.clear()
        self._code_run_banner_paint(None)

    def _code_run_to_chat(self):
        log = self._code_run_log()
        if not log:
            self._code_say(t("code.run_nothing_yet"), bad=True)
            return
        e = log[0]
        entry = getattr(self, "entry", None)
        if entry is None:
            return
        entry.setPlainText(t("code.run_chat_prefill", cmd=e["cmd"],
                             code=e["code"], out=(e["out"] or "")[-3000:]))
        entry.setFocus()
        try:
            entry.moveCursor(QTextCursor.MoveOperation.End)
        except Exception:
            pass

    def _code_goal_ctx(self):
        ctxs = getattr(self, "_chat_ctxs", None) or {}
        return (ctxs.get(getattr(self, "_active_tab_id", None))
                or ctxs.get("local"))

    def _code_goal_paint(self):
        w = getattr(self, "code_goal", None)
        if w is None:
            return
        ctx = self._code_goal_ctx()
        txt = (getattr(ctx, "goal", "") or "") if ctx is not None else ""
        self._code_goal_loading = True
        try:
            if w.toPlainText() != txt:
                w.setPlainText(txt)
        finally:
            self._code_goal_loading = False
        lb = getattr(self, "code_goal_state", None)
        if lb is not None:
            lb.setText(t("code.goal_on") if txt.strip()
                       else t("code.goal_off"))
            lb.setStyleSheet("QLabel { color:%s; font-size:12px; }"
                             % ("#3fb950" if txt.strip() else "#e6edf7"))

    def _code_run_paint(self):
        w = getattr(self, "code_run_where", None)
        if w is None:
            return
        w.setText(self._code_project or t("code.no_project"))
        self._code_check_goal_show()
        self._code_verify_paint()
        if not getattr(self, "_code_running", False):
            log = self._code_run_log()
            self._code_run_banner_paint(log[0]["ok"] if log else None,
                                        entry=log[0] if log else None)

    @staticmethod
    def _branch_slug(message):
        import re as _re
        s = _re.sub(r"[^a-z0-9]+", "-", (message or "").lower()).strip("-")
        return "hoplita/" + (s[:40].rstrip("-") or "task")

    def _code_commit(self):
        if not self._code_project:
            self._code_say(t("code.no_project"), bad=True)
            return
        msg = self.code_msg.text().strip()
        if not msg:
            self._code_say(t("code.commit_needs_msg"), bad=True)
            return
        branch = ""
        okst, st, _ = self._mcp_try("git_status", {"path": self._code_project})
        if okst:
            branch = st.get("branch", "")
        if branch in ("main", "master"):
            newb = self._branch_slug(msg)
            okb, _, errb = self._mcp_try(
                "git_branch", {"path": self._code_project, "name": newb})
            if not okb:
                self._code_say(t("code.branch_fail", name=newb, err=errb), bad=True)
                return
        args = {"path": self._code_project, "message": msg}
        if not self.code_only_mine.isChecked():
            args["files"] = list(self._code_diff_cache)
        ok, r, err = self._mcp_try("git_commit", args, timeout=30)
        if not ok:
            self._code_say(t("code.commit_fail", err=err), bad=True)
            return
        self.code_msg.clear()
        self._code_refresh(then=lambda: self._code_say(
            t("code.committed", sha=r.get("sha", "?"),
              branch=r.get("branch", "?"), n=len(r.get("files", [])))))

    def _code_show_toolchain(self):
        tc = getattr(self, "_code_toolchain", None) or {}
        dlg = QDialog(self)
        dlg.setWindowTitle(t("code.toolchain_title"))
        dlg.resize(760, 420)
        v = QVBoxLayout(dlg)
        head = QLabel(t("code.toolchain_intro"))
        head.setWordWrap(True)
        v.addWidget(head)
        lines = []
        for L in tc.get("languages", []):
            state = (t("code.tc_ok") if not L.get("missing")
                     else t("code.tc_missing", what=", ".join(L["missing"])))
            lines.append(f"# {L['language']}  ·  {L['files']} file(s), "
                         f"{L['lines']} line(s)  ·  {state}")
            if L.get("install"):
                lines.append(L["install"])
            lines.append("")
        if tc.get("unknown"):
            lines.append("# " + t("code.tc_unknown", exts=", ".join(
                f"{n}x {e}" for e, n in sorted(tc["unknown"].items(),
                                               key=lambda kv: -kv[1]))))
        box = QPlainTextEdit("\n".join(lines).strip() or t("code.tc_nothing"))
        box.setReadOnly(True)
        box.setStyleSheet("QPlainTextEdit { background:#0f172a; color:#e6edf7; "
                          "border:1px solid #334155; font-family:monospace; "
                          "font-size:12px; padding:8px; }")
        v.addWidget(box, 1)
        row = QHBoxLayout()
        row.addStretch()
        cp = QPushButton(t("code.tc_copy"))
        cp.clicked.connect(lambda: (QApplication.clipboard().setText(box.toPlainText()),
                                    cp.setText(t("code.tc_copied"))))
        row.addWidget(cp)
        close = QPushButton(t("btn.close"))
        close.clicked.connect(dlg.accept)
        row.addWidget(close)
        v.addLayout(row)
        dlg.exec()

    def _code_set_mode(self, idx, user=False):
        if getattr(self, "code_right_stack", None) is None:
            return
        idx = 0 if idx == 0 else 1
        self.code_right_stack.setCurrentIndex(idx)
        if getattr(self, "code_tabs", None) is not None:
            _bufs = getattr(self, "_code_bufs_order", [])
            if idx == 0:
                _want = 0
            elif self._code_open_path in _bufs:
                _want = _bufs.index(self._code_open_path) + 1
            else:
                _want = None
            if _want is not None and self.code_tabs.currentIndex() != _want:
                self.code_tabs.blockSignals(True)
                self.code_tabs.setCurrentIndex(_want)
                self.code_tabs.blockSignals(False)
        if getattr(self, "code_left_stack", None) is not None:
            want = 0 if idx == 0 else (self._code_view if self._code_view in (1, 2) else 1)
            if self._code_view in (3, 6) and not user:
                self.code_left_stack.setCurrentIndex(want)
                self._code_prev_view = want
            else:
                self._code_activity(want, force=True)

    def _code_projects_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle(t("code.projects_title"))
        dlg.resize(820, 520)
        v = QVBoxLayout(dlg)
        head = QLabel(t("code.projects_intro"))
        head.setWordWrap(True)
        head.setStyleSheet("QLabel { color:#e6edf7; font-size:12px; }")
        v.addWidget(head)
        lst = QListWidget()
        v.addWidget(lst, 1)
        state = QLabel(t("code.cowork_loading"))
        state.setStyleSheet("QLabel { color:#e6edf7; font-size:12px; }")
        v.addWidget(state)

        def _open():
            it = lst.currentItem()
            if it is None:
                return
            row = it.data(Qt.ItemDataRole.UserRole) or {}
            if not row.get("exists"):
                state.setText(t("code.projects_gone", path=row.get("path", "")))
                state.setStyleSheet("QLabel { color:#f87171; font-size:12px; }")
                return
            self.code_project_edit.setText(row.get("path", ""))
            self._code_open_project()
            dlg.accept()

        lst.itemDoubleClicked.connect(lambda _i: _open())
        row = QHBoxLayout()
        row.addStretch()
        _ob = QPushButton(t("code.projects_open"))
        _ob.setStyleSheet(
            "QPushButton { background:#065f46; color:#d1fae5; border:1px solid "
            "#10b981; border-radius:8px; padding:6px 16px; font-weight:700; }")
        _ob.clicked.connect(_open)
        row.addWidget(_ob)
        _cl = QPushButton(t("btn.close"))
        _cl.clicked.connect(dlg.reject)
        row.addWidget(_cl)
        v.addLayout(row)

        def work():
            return self._mcp_try("code_projects", {}, timeout=60)

        def paint(res):
            ok, r, err = res if res else (False, {}, "?")
            rows = (r or {}).get("projects", []) if ok else []
            for p in rows:
                _bits = [p.get("path") or t("code.projects_nopath")]
                if p.get("notes"):
                    _bits.append(t("code.projects_notes", n=p["notes"]))
                if p.get("peers"):
                    _bits.append("🤝 " + ", ".join(p["peers"]))
                it = QListWidgetItem("%s %s\n     %s" % (
                    "📂" if p.get("exists") else "⚠",
                    p.get("name", "?"), "  ·  ".join(_bits)))
                it.setData(Qt.ItemDataRole.UserRole, p)
                if not p.get("exists"):
                    it.setForeground(QColor("#f59e0b"))
                lst.addItem(it)
            if not ok:
                state.setText(t("msg.error", e=err))
            elif not rows:
                state.setText(t("code.projects_none"))
            else:
                state.setText(t("code.projects_count", n=len(rows)))

        self._bg(work, paint)
        dlg.exec()

    def _code_project_menu(self, gpos):
        if not getattr(self, "_code_project", ""):
            return
        menu = QMenu(self)
        act_mem = menu.addAction("📓 " + t("code.memory_menu"))
        act_share = menu.addAction("🤝 " + t("code.share_menu"))
        menu.addSeparator()
        act_close = menu.addAction(t("code.close_project"))
        chosen = menu.exec(gpos)
        if chosen is act_mem:
            self._code_memory_open()
        elif chosen is act_share:
            self._code_share_dialog()
        elif chosen is act_close:
            self._code_close_project()

    def _show_build_panel(self):
        import html as _h
        dlg = QDialog(self)
        dlg.setWindowTitle(t("app.build_title"))
        dlg.resize(680, 380)
        v = QVBoxLayout(dlg)
        box = QTextEdit()
        box.setReadOnly(True)
        box.setStyleSheet("QTextEdit { background:#0f172a; color:#e6edf7; "
                          "border:1px solid #334155; font-family:monospace; "
                          "font-size:13px; padding:10px; }")
        v.addWidget(box, 1)
        row = QHBoxLayout()
        row.addStretch()
        _cp = QPushButton(t("code.tc_copy"))
        _cp.clicked.connect(lambda: (QApplication.clipboard().setText(
            box.toPlainText()), _cp.setText(t("code.tc_copied"))))
        row.addWidget(_cp)
        _cl = QPushButton(t("btn.close"))
        _cl.clicked.connect(dlg.accept)
        row.addWidget(_cl)
        v.addLayout(row)

        mine = HOPLITA_LABEL_LONG
        src = os.path.abspath(__file__)
        box.setHtml(
            f'<div style="color:#e6edf7;">{_h.escape(t("app.build_asking"))}'
            f'</div><div>{_h.escape(mine)}</div>')

        def work():
            try:
                rr = requests.get(f"{self._mcp_url}/health", timeout=6)
                return (True, rr.json() if rr.status_code == 200 else {},
                        "" if rr.status_code == 200 else f"HTTP {rr.status_code}")
            except Exception as e:
                return (False, {}, str(e)[:120])

        def paint(res):
            ok, r, err = res if res else (False, {}, "?")
            theirs = (r or {}).get("version", "")
            mine_build = HOPLITA_LABEL.split("·")[-1].strip()
            same = bool(theirs) and theirs == mine_build
            lines = [
                (t("app.build_client"), mine, src),
                (t("app.build_mcp"),
                 (r or {}).get("label", "") or t("app.build_unknown"),
                 "%s  ·  pid %s  ·  %s" % (
                     getattr(self, "_mcp_url", "")
                     or self.config.get("MCP", "url", fallback=""),
                     (r or {}).get("pid", "?"),
                     t("app.build_started",
                       when=(r or {}).get("started", "?")))),
            ]
            html = ""
            for title, label, sub in lines:
                html += (f'<div style="color:#e6edf7;font-size:12px;'
                         f'letter-spacing:1px;margin-top:10px;">'
                         f'{_h.escape(title)}</div>'
                         f'<div style="color:#e6edf7;font-weight:700;">'
                         f'{_h.escape(label)}</div>'
                         f'<div style="color:#e6edf7;font-size:12px;">'
                         f'{_h.escape(sub)}</div>')
            if not ok:
                verdict, col = t("app.build_no_mcp", err=err), "#f85149"
            elif same:
                verdict, col = t("app.build_same"), "#3fb950"
            else:
                verdict, col = t("app.build_differ", mcp=theirs or "?"), "#f85149"
            html += (f'<div style="color:{col};font-weight:700;'
                     f'margin-top:14px;">{_h.escape(verdict)}</div>')
            box.setHtml(html)

        self._bg(work, paint)
        dlg.exec()

    def _code_ask_project_name(self, path):
        base = os.path.basename(os.path.normpath(path)) or path
        try:
            ok, r, _ = self._mcp_try("code_project_name", {"path": path},
                                     timeout=15)
            if ok and isinstance(r, dict):
                if r.get("exists"):
                    return ""
                base = r.get("name") or base
        except Exception:
            pass
        txt, done = QInputDialog.getText(
            self, t("code.name_title"), t("code.name_ask", path=path),
            QLineEdit.EchoMode.Normal, base)
        if not done:
            return ""
        txt = (txt or "").strip()
        return "" if (not txt or txt == base) else txt

    def _code_build_cowork_page(self, _title, _btn):
        page = QWidget()
        pv = QVBoxLayout(page)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)
        head = QWidget()
        head.setStyleSheet(
            "background:#0b1220; border-bottom:1px solid #1f2937;")
        hb = QHBoxLayout(head)
        hb.setContentsMargins(14, 8, 14, 8)
        _h1 = QLabel("\U0001F91D  " + t("code.cowork_title"))
        _h1.setStyleSheet("QLabel { color:#e6edf7; font-size:13px; "
                          "font-weight:700; letter-spacing:1px; }")
        hb.addWidget(_h1)
        self.code_cowork_where = QLabel("")
        self.code_cowork_where.setStyleSheet(
            "QLabel { color:#e6edf7; font-family:monospace; font-size:12px; }")
        hb.addWidget(self.code_cowork_where)
        hb.addStretch()
        _ref = _btn(t("code.refresh"), self._code_cowork_paint)
        hb.addWidget(_ref)
        pv.addWidget(head)

        body = QWidget()
        body.setStyleSheet("background:#0d1526;")
        bl = QHBoxLayout(body)
        bl.setContentsMargins(14, 12, 14, 12)
        bl.setSpacing(16)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(6)
        _lead = QLabel(t("code.cowork_lead"))
        _lead.setWordWrap(True)
        _lead.setStyleSheet("QLabel { color:#e6edf7; font-size:12px; }")
        lv.addWidget(_lead)
        lv.addWidget(_title(t("code.cowork_peers").upper()))
        self.code_cowork_peers = QListWidget()
        self.code_cowork_peers.setMaximumHeight(110)
        lv.addWidget(self.code_cowork_peers)
        self.code_cowork_unshare = _btn(t("code.cowork_unshare"),
                                        self._code_cowork_unshare)
        self.code_cowork_unshare.setToolTip(t("code.cowork_unshare_tip"))
        lv.addWidget(self.code_cowork_unshare)
        lv.addWidget(_title(t("code.share_contacts").upper()))
        self.code_cowork_contacts = QListWidget()
        lv.addWidget(self.code_cowork_contacts, 1)
        self.code_cowork_invite = _btn(t("code.share_invite"),
                                       self._code_cowork_invite, green=True)
        lv.addWidget(self.code_cowork_invite)
        bl.addWidget(left, 1)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(6)
        rv.addWidget(_title(t("code.share_incoming").upper()))
        self.code_cowork_inbox = QListWidget()
        rv.addWidget(self.code_cowork_inbox, 1)
        rv.addWidget(_title(t("code.cowork_incoming").upper()))
        self.code_cowork_incoming = QListWidget()
        self.code_cowork_incoming.setMaximumHeight(120)
        self.code_cowork_incoming.setToolTip(t("code.cowork_incoming_tip"))
        rv.addWidget(self.code_cowork_incoming)
        _r1 = QHBoxLayout(); _r1.setSpacing(4)
        _r1.addWidget(_btn(t("code.cowork_review"), self._code_cowork_review))
        _wbb = _btn(t("code.cowork_board"), self._code_cowork_board)
        _wbb.setToolTip(t("code.cowork_board_tip"))
        _r1.addWidget(_wbb)
        self.code_cowork_take = _btn(t("code.cowork_take"),
                                     self._code_cowork_take, green=True)
        self.code_cowork_take.setToolTip(t("code.cowork_take_tip"))
        _r1.addWidget(self.code_cowork_take)
        rv.addLayout(_r1)
        self.code_cowork_accept = _btn(t("code.share_accept"),
                                       self._code_cowork_accept)
        rv.addWidget(self.code_cowork_accept)
        self.code_cowork_discard = _btn(t("code.cowork_discard"),
                                        self._code_cowork_discard)
        self.code_cowork_discard.setToolTip(t("code.cowork_discard_tip"))
        rv.addWidget(self.code_cowork_discard)
        _r2 = QHBoxLayout(); _r2.setSpacing(4)
        _r2.addWidget(_btn(t("code.cowork_pull"), self._code_cowork_pull))
        _r2.addWidget(_btn(t("code.cowork_push"), self._code_cowork_push))
        rv.addLayout(_r2)
        self.code_cowork_state = QLabel("")
        self.code_cowork_state.setWordWrap(True)
        self.code_cowork_state.setStyleSheet(
            "QLabel { color:#e6edf7; font-size:12px; }")
        rv.addWidget(self.code_cowork_state)
        bl.addWidget(right, 1)
        pv.addWidget(body, 1)
        return page

    def _code_cowork_say(self, msg, bad=False):
        w = getattr(self, "code_cowork_state", None)
        if w is not None:
            w.setText(msg)
            w.setStyleSheet("QLabel { color:%s; font-size:12px; }"
                            % ("#f87171" if bad else "#e6edf7"))

    def _code_cowork_paint(self, note=""):
        note = note if isinstance(note, str) else ""
        if getattr(self, "code_cowork_where", None) is None:
            return
        self.code_cowork_where.setText(
            self._code_project or t("code.no_project"))
        for w in (self.code_cowork_peers, self.code_cowork_contacts,
                  self.code_cowork_inbox, self.code_cowork_incoming):
            w.clear()
        proj = self._code_project
        self.code_cowork_invite.setEnabled(bool(proj))
        self.code_cowork_accept.setEnabled(True)
        self._code_cowork_say(t("code.cowork_loading"))

        def work():
            out = {}
            if proj:
                out["peers"] = self._mcp_try("fed_code_peers", {"path": proj},
                                             timeout=20)
                out["contacts"] = self._mcp_try("fed_list_contacts", {},
                                                timeout=20)
                out["incoming"] = self._mcp_try("fed_code_incoming",
                                                {"path": proj}, timeout=60)
            out["pending"] = self._mcp_try("fed_code_pending", {}, timeout=30)
            return out

        def done(res):
            res = res or {}
            okq, rq, _ = res.get("incoming", (False, {}, ""))
            _rows = (rq or {}).get("incoming", []) if okq else []
            for q in _rows:
                it = QListWidgetItem(t("code.cowork_incoming_row",
                                       peer=q.get("peer", "?"),
                                       branch=q.get("branch", "?"),
                                       n=q.get("commits", 0),
                                       how=(t("code.cowork_ff") if q.get("fast_forward")
                                            else t("code.cowork_merge"))))
                it.setToolTip("\n".join(q.get("subjects") or [])
                              + ("\n\n" + q.get("summary", "") if q.get("summary") else ""))
                it.setData(Qt.ItemDataRole.UserRole, q)
                self.code_cowork_incoming.addItem(it)
            if okq and not _rows:
                self.code_cowork_incoming.addItem(
                    QListWidgetItem(t("code.cowork_incoming_none")))
            elif not okq and proj:
                self.code_cowork_incoming.addItem(
                    QListWidgetItem(t("code.cowork_incoming_unknown")))
            okp, rp, _ = res.get("peers", (False, {}, ""))
            for pr in ((rp or {}).get("peers", []) if okp else []):
                it = QListWidgetItem("\U0001F517 %s   %s" % (
                    pr.get("handle", "?"), pr.get("last_tip", "")))
                self.code_cowork_peers.addItem(it)
            if okp and not (rp or {}).get("peers"):
                self.code_cowork_peers.addItem(
                    QListWidgetItem(t("code.cowork_no_peers")))
            okc, rc, errc = res.get("contacts", (False, {}, "?"))
            live = [c for c in ((rc or {}).get("contacts", []) if okc else [])
                    if str(c.get("status", "")).lower()
                    in ("", "active", "accepted", "ok")]
            for c in live:
                it = QListWidgetItem("%s  %s" % (c.get("name") or "",
                                                 c.get("handle", "")))
                it.setData(Qt.ItemDataRole.UserRole, c.get("handle", ""))
                self.code_cowork_contacts.addItem(it)
            oki, ri, erri = res.get("pending", (False, {}, "?"))
            ri = ri or {}
            for iv in ri.get("offers", []):
                _kb = int(iv.get("bytes") or 0) // 1024
                it = QListWidgetItem("\U0001F4E6 %s  ←  %s   (%s)" % (
                    iv.get("project", "?"), iv.get("from", "?"),
                    t("code.cowork_offer_size", kb=_kb)))
                it.setToolTip(t("code.cowork_offer_tip"))
                it.setData(Qt.ItemDataRole.UserRole, dict(iv, _ready=True,
                                                          _offer=True))
                self.code_cowork_inbox.addItem(it)
            for iv in ri.get("awaiting", []):
                _on = iv.get("peer_online")
                _key = ("code.cowork_awaiting_on" if _on is True
                        else "code.cowork_awaiting_off" if _on is False
                        else "code.cowork_awaiting_unknown")
                it = QListWidgetItem(t(_key, project=iv.get("project", "?"),
                                       from_who=iv.get("from", "?")))
                it.setToolTip(t("code.cowork_awaiting_tip"))
                it.setData(Qt.ItemDataRole.UserRole, dict(iv, _awaiting=True))
                self.code_cowork_inbox.addItem(it)
            rows = [(x, True) for x in ri.get("pending", [])] + \
                   [(x, False) for x in ri.get("arriving", [])]
            for iv, ready in rows:
                _tot = iv.get("parts_total") or 0
                it = QListWidgetItem("%s %s  \u2190  %s%s" % (
                    "\u2713" if ready else "\u23f3",
                    iv.get("project", "?"), iv.get("from", "?"),
                    "" if ready else "  (%s)" % t(
                        "code.cowork_parts", n=iv.get("parts_here", 0),
                        total=(_tot or "?"))))
                if iv.get("stuck_hint"):
                    it.setToolTip(t("code.cowork_stuck"))
                it.setData(Qt.ItemDataRole.UserRole,
                           dict(iv, _ready=ready))
                self.code_cowork_inbox.addItem(it)
            msgs = []
            if not proj:
                msgs.append(t("code.cowork_no_project"))
            elif not okc:
                msgs.append(t("msg.error", e=errc))
            elif not live:
                msgs.append(t("code.share_no_contacts"))
            if oki and not rows:
                msgs.append(t("code.share_no_incoming"))
            elif not oki:
                msgs.append(t("msg.error", e=erri))
            self._code_cowork_say(
                note or ("  ·  ".join(msgs) if msgs
                         else t("code.cowork_ready")))

        self._bg(work, done)

    def _code_cowork_invite(self):
        it = self.code_cowork_contacts.currentItem()
        if it is None:
            self._code_cowork_say(t("code.share_pick_contact"), bad=True)
            return
        handle = it.data(Qt.ItemDataRole.UserRole) or ""
        proj = self._code_project
        self.code_cowork_invite.setEnabled(False)
        self._code_cowork_say(t("code.share_sending", handle=handle))

        def work():
            return self._mcp_try("fed_share_code",
                                 {"path": proj, "handle": handle}, timeout=180)

        def done(res):
            self.code_cowork_invite.setEnabled(True)
            ok, r, err = res if res else (False, {}, "?")
            if not ok or (isinstance(r, dict) and r.get("error")):
                self._code_cowork_say(t("code.share_fail", err=(
                    (r or {}).get("error") or err)), bad=True)
                return
            self._code_cowork_paint(t("code.share_sent", handle=handle))

        self._bg(work, done)

    def _code_cowork_accept(self):
        it = self.code_cowork_inbox.currentItem()
        if it is None:
            self._code_cowork_say(t("code.share_pick_invite"), bad=True)
            return
        iv = it.data(Qt.ItemDataRole.UserRole) or {}
        if not iv.get("_ready", True):
            self._code_cowork_say(t("code.cowork_not_ready"), bad=True)
            return
        dest = QFileDialog.getExistingDirectory(
            self, t("code.share_where", project=iv.get("project", "")),
            os.path.expanduser("~"))
        if not dest:
            return
        self._code_cowork_say(t("code.share_accepting",
                                project=iv.get("project", "")))

        def work():
            return self._mcp_try("fed_accept_code",
                                 {"handle": iv.get("from", ""),
                                  "project": iv.get("project", ""),
                                  "dest_path": dest}, timeout=300)

        def done(res):
            ok, r, err = res if res else (False, {}, "?")
            if not ok or (isinstance(r, dict) and r.get("error")):
                self._code_cowork_say(t("code.share_fail", err=(
                    (r or {}).get("error") or err)), bad=True)
                return
            self._code_cowork_paint(t("code.share_accepted", path=dest))

        self._bg(work, done)

    def _code_cowork_unshare(self):
        if not self._code_project:
            self._code_cowork_say(t("code.cowork_no_project"), bad=True)
            return
        it = self.code_cowork_peers.currentItem()
        who = ""
        if it is not None:
            txt = it.text().strip()
            parts = [p for p in txt.replace("\U0001F517", " ").split() if p]
            who = parts[0] if parts else ""
        if not who:
            self._code_cowork_say(t("code.cowork_pick_peer"), bad=True)
            return
        if QMessageBox.question(
                self, t("code.cowork_unshare_title"),
                t("code.cowork_unshare_body", who=who,
                  name=os.path.basename(os.path.normpath(self._code_project))),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        proj = self._code_project

        def work():
            return self._mcp_try("fed_code_unshare",
                                 {"path": proj, "peer": who}, timeout=60)

        def done(res):
            ok, r, err = res if res else (False, {}, "?")
            if not ok or (isinstance(r, dict) and r.get("error")):
                self._code_cowork_say(t("code.share_fail", err=(
                    (r or {}).get("error") or err)), bad=True)
                return
            self._code_cowork_paint(t("code.cowork_unshared", who=who))

        self._bg(work, done)

    def _code_cowork_discard(self):
        it = self.code_cowork_inbox.currentItem()
        if it is None:
            self._code_cowork_say(t("code.share_pick_invite"), bad=True)
            return
        iv = it.data(Qt.ItemDataRole.UserRole) or {}
        if QMessageBox.question(
                self, t("code.cowork_discard_title"),
                t("code.cowork_discard_body",
                  project=iv.get("project", "?"), who=iv.get("from", "?")),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._code_cowork_say(t("code.cowork_discarding"))

        def work():
            return self._mcp_try("fed_code_discard",
                                 {"project": iv.get("project", ""),
                                  "handle": iv.get("from", "")}, timeout=120)

        def done(res):
            ok, r, err = res if res else (False, {}, "?")
            if not ok or (isinstance(r, dict) and r.get("error")):
                self._code_cowork_say(t("code.share_fail", err=(
                    (r or {}).get("error") or err)), bad=True)
                return
            self._code_cowork_paint(t("code.cowork_discarded"))

        self._bg(work, done)

    def _code_cowork_pull(self):
        self._code_cowork_say(t("code.cowork_pulling"))

        def work():
            r = self._mcp_try("fed_code_pull", {}, timeout=300)
            st = self._mcp_try("fed_code_state", {}, timeout=60)
            return r, st

        def done(res):
            (ok, r, err), (oks, st, _e) = res if res else ((False, {}, "?"), (False, {}, ""))
            if not ok:
                self._code_cowork_say(t("code.share_fail", err=err), bad=True)
                return
            r = r or {}
            applied = r.get("applied", []) or []
            good = [a for a in applied if a.get("ref") or a.get("materialized")]
            bad = [a for a in applied if a.get("error") or a.get("dropped")]
            parts = []
            for a in good:
                parts.append(t("code.cowork_pull_one", who=a.get("from", "?"),
                               n=len(a.get("commits") or []), project=a.get("project", "?")))
            for a in bad:
                parts.append(t("code.cowork_pull_bad", who=a.get("from", "?"),
                               why=str(a.get("error") or a.get("dropped"))[:120]))
            if r.get("waiting_elsewhere"):
                parts.append(t("code.cowork_pull_elsewhere", n=r["waiting_elsewhere"]))
            if not applied:
                _mb = (st or {}).get("mailbox", {}) if oks and isinstance(st, dict) else {}
                _desc = ", ".join(f"{k}: {v}" for k, v in _mb.items()) or t("code.cowork_mailbox_empty")
                parts.append(t("code.cowork_pull_nothing", state=_desc))
            self._code_cowork_paint(" · ".join(parts) if parts
                                    else t("code.cowork_pulled", n=len(good)))

        self._bg(work, done)

    def _code_cowork_push(self):
        proj = self._code_project
        if not proj:
            self._code_cowork_say(t("code.cowork_no_project"), bad=True)
            return
        self._code_cowork_say(t("code.cowork_pushing"))

        def work():
            return self._mcp_try("fed_code_push", {"path": proj}, timeout=300)

        def done(res):
            ok, r, err = res if res else (False, {}, "?")
            if not ok or (isinstance(r, dict) and r.get("error")):
                self._code_cowork_say(t("code.share_fail", err=(
                    (r or {}).get("error") or err)), bad=True)
                return
            r = r or {}
            if r.get("skipped"):
                self._code_cowork_say(t("code.cowork_push_skipped",
                                        why=str(r["skipped"])), bad=True)
                return
            peers = r.get("peers", {}) or {}
            sent = [h for h, v in peers.items() if str(v).startswith("sent")]
            errs = [f"{h}: {v}" for h, v in peers.items() if str(v).startswith("error")]
            same = [h for h, v in peers.items() if v == "up_to_date"]
            detail = "; ".join([f"{h}: {peers[h]}" for h in sent] + errs
                               + ([t("code.cowork_push_same", who=", ".join(same))] if same else []))
            self._code_cowork_say(t("code.cowork_pushed_detail", n=len(sent), detail=detail),
                                  bad=bool(errs))

        self._bg(work, done)

    def _code_cowork_selected_incoming(self):
        it = self.code_cowork_incoming.currentItem() if getattr(
            self, "code_cowork_incoming", None) is not None else None
        q = it.data(Qt.ItemDataRole.UserRole) if it is not None else None
        return q if isinstance(q, dict) else None

    def _code_cowork_review(self):
        q = self._code_cowork_selected_incoming()
        if not q:
            self._code_cowork_say(t("code.cowork_take_pick"), bad=True)
            return
        lines = [t("code.cowork_incoming_row", peer=q.get("peer", "?"),
                   branch=q.get("branch", "?"), n=q.get("commits", 0),
                   how=(t("code.cowork_ff") if q.get("fast_forward")
                        else t("code.cowork_merge")))]
        lines += list(q.get("subjects") or [])[:12]
        if q.get("summary"):
            lines.append(q["summary"])
        self._code_cowork_say("\n".join(lines))

    def _code_cowork_board(self):
        q = self._code_cowork_selected_incoming()
        if not q or not q.get("ref"):
            self._code_cowork_say(t("code.cowork_take_pick"), bad=True)
            return
        self._code_activity(7, force=True)
        self._code_wb_draw("incoming", ref=q["ref"])

    def _code_cowork_take(self):
        q = self._code_cowork_selected_incoming()
        proj = self._code_project
        if not q or not proj:
            self._code_cowork_say(t("code.cowork_take_pick"), bad=True)
            return
        self._code_cowork_say(t("code.cowork_taking", peer=q.get("peer", "?")))

        def work():
            return self._mcp_try("fed_code_take", {"path": proj, "peer": q.get("peer", ""),
                                                   "branch": q.get("branch", "main")},
                                 timeout=300)

        def done(res):
            ok, r, err = res if res else (False, {}, "?")
            r = r or {}
            if not ok or r.get("error"):
                if r.get("error") == "conflict":
                    self._code_cowork_paint(t("code.cowork_take_conflict",
                                              peer=q.get("peer", "?"),
                                              files=", ".join(r.get("files") or []) or "?",
                                              ref=r.get("ref", "")))
                else:
                    self._code_cowork_paint(t("code.share_fail", err=r.get("error") or err))
                return
            self._code_cowork_paint(t("code.cowork_taken", n=r.get("merged", 0),
                                      peer=q.get("peer", "?"),
                                      how=(t("code.cowork_ff") if r.get("fast_forward")
                                           else t("code.cowork_merge"))))
            try:
                self._code_refresh()
            except Exception:
                pass

        self._bg(work, done)

    def _code_share_dialog(self):
        self._code_activity(6, force=True)

    def _code_memory_open(self):
        proj = getattr(self, "_code_project", "")
        if not proj:
            return
        cat = os.path.basename(os.path.normpath(proj))
        try:
            existing = self.notes_manager.get_notes(cat) or []
        except Exception:
            existing = []
        if not existing:
            self.notes_manager.add_note(cat, "rules",
                                        t("code.memory_tpl_rules"))
            self.notes_manager.add_note(cat, "spec",
                                        t("code.memory_tpl_spec"))
            self.notes_manager.add_note(cat, "future",
                                        t("code.memory_tpl_future"))
            self.add_message(t("code.memory_started", name=cat), "system")
        self.refresh_notes_lists()
        nb = getattr(self, "_notes_tab_widget", None)
        if nb is not None:
            self.tabs.setCurrentWidget(nb)
        for i in range(self.categories_list.count()):
            if (self.categories_list.item(i).data(Qt.ItemDataRole.UserRole)
                    == cat):
                self.categories_list.setCurrentRow(i)
                break

    def _proj_get(self, ep, params=None, timeout=30):
        r = requests.get(f"{self._tab_url()}/code/{ep}", params=params or {},
                         headers=self._tab_headers(), timeout=timeout)
        if r.status_code != 200:
            raise OSError(self._http_error(r))
        return r.json()

    def _proj_post(self, ep, body, timeout=30):
        r = requests.post(f"{self._tab_url()}/code/{ep}", json=body,
                          headers=self._tab_headers(), timeout=timeout)
        if r.status_code != 200:
            raise OSError(self._http_error(r))
        return r.json()

    def _proj_read(self, path):
        import base64 as _b64
        d = self._proj_get("file", {"path": path})
        return _b64.b64decode(d.get("b64") or ""), d.get("mtime")

    def _proj_stat(self, paths):
        try:
            return (self._proj_post("stat", {"paths": list(paths)}) or {}).get("stat") or {}
        except Exception:
            return {}

    def _proj_mtime(self, path):
        return (self._proj_stat([path]).get(path) or {}).get("mtime")

    def _code_load_tree(self):
        self.code_tree.clear()
        if not self._code_project:
            return
        root_item = QTreeWidgetItem([os.path.basename(self._code_project) or "/"])
        self.code_tree.addTopLevelItem(root_item)
        try:
            tree = self._proj_get("tree", {"root": self._code_project, "limit": 4000})
        except Exception as e:
            self._code_say(t("msg.error", e=str(e)), bad=True)
            return
        nodes = {".": root_item}
        for d in tree.get("dirs") or []:
            parent = nodes.get(os.path.dirname(d) or ".")
            if parent is None:
                continue
            it = QTreeWidgetItem([os.path.basename(d)])
            parent.addChild(it)
            nodes[d] = it
        for f in tree.get("files") or []:
            parent = nodes.get(os.path.dirname(f) or ".")
            if parent is None:
                continue
            it = QTreeWidgetItem([os.path.basename(f)])
            it.setData(0, Qt.ItemDataRole.UserRole,
                       os.path.join(self._code_project, f))
            parent.addChild(it)
        root_item.setExpanded(True)

    def _code_open_file(self, item, _col=0):
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if not path:
            return
        self._code_open_in_tab(path)


    def _code_state(self):
        tid = getattr(self, "_active_tab_id", None)
        h = (getattr(self, "_chat_ctxs", {}) or {}).get(tid)
        if h is None:
            if not hasattr(self, "_code_orphan"):
                self._code_orphan = {}
            h = self._code_orphan.setdefault(str(tid), _CodeSlot())
        if not hasattr(h, "code_bufs"):
            h.code_bufs, h.code_bufs_order, h.code_open_path = {}, [], ""
        return h

    @property
    def _code_bufs(self):
        return self._code_state().code_bufs

    @property
    def _code_bufs_order(self):
        return self._code_state().code_bufs_order

    @property
    def _code_open_path(self):
        return self._code_state().code_open_path

    @_code_open_path.setter
    def _code_open_path(self, value):
        self._code_state().code_open_path = value

    def _code_detach_editor(self):
        ed = getattr(self, "code_editor", None)
        if ed is None:
            return
        from PyQt6.QtGui import QTextDocument
        ed.blockSignals(True)
        try:
            ed.setDocument(QTextDocument(ed))
        finally:
            ed.blockSignals(False)
        self._code_hl = None
        self._code_dirty = False
        self._code_sel_diag = []
        self._code_sel_find = []
        self._code_find_hits = []

    def _code_buf(self, path=None):
        return self._code_bufs.get(path or self._code_open_path)

    def _code_stash_current(self):
        p = getattr(self, "_code_open_path", "")
        b = self._code_buf(p) if p else None
        if not b:
            return
        b["text"] = self.code_editor.toPlainText()
        b["dirty"] = self._code_dirty
        b["pos"] = self.code_editor.textCursor().position()
        b["scroll"] = self.code_editor.verticalScrollBar().value()

    def _code_open_in_tab(self, path):
        if path in self._code_bufs:
            self._code_stash_current()
            self._code_show_buf(path)
            return
        try:
            raw, _mtime = self._proj_read(path)
        except Exception as e:
            self._code_say(t("msg.error", e=str(e)), bad=True)
            return
        if b"\0" in raw[:8000]:
            self._code_say(t("code.binary_file",
                             path=os.path.basename(path)), bad=True)
            return
        self._code_stash_current()
        self._code_bufs[path] = {"text": raw.decode("utf-8", errors="replace"),
                                 "dirty": False, "pos": 0, "scroll": 0,
                                 "mtime": _mtime}
        self._code_bufs_order.append(path)
        self._code_show_buf(path)

    def _code_show_buf(self, path):
        b = self._code_bufs.get(path)
        if b is None:
            return
        text = b["text"]
        self._code_open_path = path
        self._code_loading = True
        try:
            self.code_editor.blockSignals(True)
            if b.get("doc") is None:
                from PyQt6.QtGui import QTextDocument
                doc = QTextDocument(self.code_editor)
                doc.setDefaultFont(self.code_editor.font())
                self.code_editor.setDocument(doc)
                self.code_editor.setPlainText(text)
                hl = PygmentsHighlighter(doc)
                hl.set_language(path, text)
                hl.rehighlight_text(text)
                b["doc"], b["hl"] = doc, hl
                doc.setModified(False)
                doc.modificationChanged.connect(
                    lambda on, _p=path: self._code_modified(_p, on))
            else:
                self.code_editor.setDocument(b["doc"])
            self.code_editor.setReadOnly(bool(b.get("readonly")))
            self._code_hl = b["hl"]
            self.code_editor.setTabStopDistance(
                4 * self.code_editor.fontMetrics().horizontalAdvance(" "))
            self.code_editor.blockSignals(False)
        finally:
            self._code_loading = False
        self._code_dirty = b["dirty"]
        cur = self.code_editor.textCursor()
        cur.setPosition(min(b.get("pos", 0), len(text)))
        self.code_editor.setTextCursor(cur)
        self.code_editor.verticalScrollBar().setValue(b.get("scroll", 0))
        self._code_sel_diag = []
        self._code_sel_find = []
        self._code_find_hits = []
        self._code_find_at = -1
        self.code_editor.setExtraSelections([])
        self.code_find_count.setText("")
        self._code_paint_open_label()
        self._code_rebuild_tabs()
        self._code_set_mode(1)
        self._code_buf_rev += 1
        self._code_diag_timer.start()
        if self.code_find.text():
            self._code_find_run(0, keep=True)

    def _code_paint_open_label(self):
        rel = (os.path.relpath(self._code_open_path, self._code_project)
               if self._code_open_path and self._code_project
               else (self._code_open_path or t("code.no_file")))
        self.code_open_label.setText(("● " if self._code_dirty else "") + rel)

    def _code_rebuild_tabs(self):
        if getattr(self, "code_tabs", None) is None:
            return
        bufs = getattr(self, "_code_bufs_order", [])
        self.code_tabs.blockSignals(True)
        while self.code_tabs.count():
            self.code_tabs.removeTab(0)
        self.code_tabs.addTab(t("code.tab_diff"))
        self._code_seal_diff_tab()
        for p in bufs:
            b = self._code_bufs.get(p) or {}
            self.code_tabs.addTab(("● " if b.get("dirty") else "")
                                  + os.path.basename(p))
            self.code_tabs.setTabToolTip(self.code_tabs.count() - 1, p)
        cur = (bufs.index(self._code_open_path) + 1
               if self._code_open_path in bufs else 0)
        self.code_tabs.setCurrentIndex(cur)
        self.code_tabs.blockSignals(False)

    def _code_seal_diff_tab(self):
        for side in (QTabBar.ButtonPosition.RightSide,
                     QTabBar.ButtonPosition.LeftSide):
            if self.code_tabs.tabButton(0, side) is not None:
                self.code_tabs.setTabButton(0, side, None)

    def _code_tab_clicked(self, idx):
        bufs = getattr(self, "_code_bufs_order", [])
        if idx <= 0:
            self._code_stash_current()
            self._code_set_mode(0, user=True)
            return
        i = idx - 1
        if 0 <= i < len(bufs) and bufs[i] != self._code_open_path:
            self._code_stash_current()
            self._code_show_buf(bufs[i])
        else:
            self._code_set_mode(1, user=True)

    def _code_tab_moved(self, frm, to):
        bufs = getattr(self, "_code_bufs_order", [])
        i, j = frm - 1, to - 1
        if not (0 <= i < len(bufs)) or not (0 <= j < len(bufs)):
            self._code_rebuild_tabs()
            return
        bufs.insert(j, bufs.pop(i))
        self._code_rebuild_tabs()

    def _code_close_tab(self, idx):
        bufs = getattr(self, "_code_bufs_order", [])
        i = idx - 1
        if not (0 <= i < len(bufs)):
            return
        path = bufs[i]
        b = self._code_bufs.get(path) or {}
        if b.get("dirty") and not self._code_confirm_discard():
            self._code_rebuild_tabs()
            return
        self._code_bufs.pop(path, None)
        bufs.pop(i)
        if path == self._code_open_path:
            self._code_open_path = ""
            if bufs:
                self._code_show_buf(bufs[min(i, len(bufs) - 1)])
                return
            self.code_editor.blockSignals(True)
            self.code_editor.clear()
            self.code_editor.blockSignals(False)
            self._code_dirty = False
            self.code_open_label.setText(t("code.no_file"))
        self._code_rebuild_tabs()

    def _code_modified(self, path, on):
        b = self._code_bufs.get(path)
        if b is None or bool(b.get("dirty")) == bool(on):
            return
        b["dirty"] = bool(on)
        if path == self._code_open_path:
            self._code_dirty = bool(on)
            _rel = (os.path.relpath(path, self._code_project)
                    if self._code_project else path)
            self.code_open_label.setText(("● " if on else "") + _rel)
        self._code_rebuild_tabs()

    def _code_editor_changed(self):
        if not self._code_open_path or getattr(self, "_code_loading", False):
            return
        self._code_buf_rev += 1
        self._code_diag_timer.start()
        if self.code_find.text():
            self._code_find_run(0, keep=True)
        if self._code_dirty:
            self.code_open_label.setText(
                "● " + os.path.relpath(self._code_open_path,
                                       self._code_project))

    _CODE_FIND_PAINT_MAX = 2000

    def _code_find_matches(self):
        q = self.code_find.text()
        if not q:
            return [], ""
        text = self.code_editor.toPlainText()
        if self.code_find_re.isChecked():
            try:
                rx = re.compile(q, 0 if self.code_find_case.isChecked() else re.I)
            except re.error as e:
                return [], t("code.find_bad_re", e=str(e)[:60])
            return [(m.start(), m.end()) for m in rx.finditer(text)
                    if m.end() > m.start()], ""
        hay, needle = (text, q) if self.code_find_case.isChecked() \
            else (text.lower(), q.lower())
        out, i = [], hay.find(needle)
        while i >= 0:
            out.append((i, i + len(needle)))
            i = hay.find(needle, i + len(needle))
        return out, ""

    def _code_find_run(self, move=0, keep=False):
        from PyQt6.QtWidgets import QTextEdit as _QTE
        hits, errmsg = self._code_find_matches()
        self._code_find_hits = hits
        if errmsg or not hits:
            self._code_sel_find = []
            self._code_apply_sels()
            self._code_find_at = -1
            self.code_find_count.setText(
                errmsg if errmsg
                else ("" if not self.code_find.text() else t("code.find_none")))
            self.code_find_count.setStyleSheet(
                "QLabel { color:%s; font-size:12px; }"
                % ("#f87171" if (errmsg or self.code_find.text()) else "#e6edf7"))
            return
        pos = self.code_editor.textCursor().selectionStart()
        idx = next((i for i, (a, _b) in enumerate(hits) if a >= pos), 0)
        if move:
            if self._code_find_at >= 0:
                idx = (self._code_find_at + move) % len(hits)
            elif move < 0:
                idx = (idx - 1) % len(hits)
        self._code_find_at = idx

        sels = []
        for n, (a, b) in enumerate(hits[:self._CODE_FIND_PAINT_MAX]):
            sel = _QTE.ExtraSelection()
            cur = QTextCursor(self.code_editor.document())
            cur.setPosition(a)
            cur.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
            sel.cursor = cur
            fmt = QTextCharFormat()
            fmt.setBackground(QColor("#b45309" if n == idx else "#3f4d63"))
            fmt.setForeground(QColor("#ffffff"))
            sel.format = fmt
            sels.append(sel)
        self._code_sel_find = sels
        self._code_apply_sels()

        shown = min(len(hits), self._CODE_FIND_PAINT_MAX)
        self.code_find_count.setText(
            t("code.find_count", i=idx + 1, n=len(hits))
            + ("" if shown == len(hits) else " " + t("code.find_capped", c=shown)))
        self.code_find_count.setStyleSheet(
            "QLabel { color:#e6edf7; font-size:12px; }")
        if not keep:
            cur = self.code_editor.textCursor()
            cur.setPosition(hits[idx][0])
            cur.setPosition(hits[idx][1], QTextCursor.MoveMode.KeepAnchor)
            self.code_editor.setTextCursor(cur)
            self._code_center_on_cursor()

    def _code_find_key(self, obj, event):
        from PyQt6.QtCore import QEvent
        et = event.type()
        if et not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            return None
        key = event.key()
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if key not in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            return None
        if et == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
        if key == Qt.Key.Key_Escape:
            self.code_editor.setFocus()
        elif obj is self.code_replace:
            self._code_replace_one()
        else:
            self._code_find_prev() if shift else self._code_find_next()
        return True

    def _code_find_next(self):
        self._code_find_run(1)

    def _code_find_prev(self):
        self._code_find_run(-1)

    def _code_find_focus(self):
        sel = self.code_editor.textCursor().selectedText()
        if sel and "\u2029" not in sel:
            self.code_find.setText(sel)
        self.code_find.setFocus()
        self.code_find.selectAll()
        self._code_find_run(0, keep=True)

    def _code_replace_toggle(self, force=None):
        show = bool(force) if force is not None else \
            not self.code_replace_row.isVisible()
        self.code_replace_row.setVisible(show)
        self.code_find_toggle.setChecked(show)
        if show:
            self.code_replace.setFocus()

    def _code_replace_one(self):
        if not self._code_find_hits or self._code_find_at < 0:
            self._code_find_run(1)
            return
        a, b = self._code_find_hits[self._code_find_at]
        cur = self.code_editor.textCursor()
        cur.setPosition(a)
        cur.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
        cur.insertText(self.code_replace.text())
        self._code_find_at = -1
        self._code_find_run(0)

    def _code_replace_all(self):
        hits, errmsg = self._code_find_matches()
        if errmsg or not hits:
            self.code_find_count.setText(errmsg or t("code.find_none"))
            return
        rep = self.code_replace.text()
        cur = self.code_editor.textCursor()
        cur.beginEditBlock()
        for a, b in reversed(hits):
            cur.setPosition(a)
            cur.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
            cur.insertText(rep)
        cur.endEditBlock()
        self._code_find_at = -1
        self._code_find_run(0, keep=True)
        self.code_find_count.setText(t("code.replaced_n", n=len(hits)))

    def _code_center_on_cursor(self):
        self.code_editor.ensureCursorVisible()
        bar = self.code_editor.verticalScrollBar()
        r = self.code_editor.cursorRect(self.code_editor.textCursor())
        bar.setValue(bar.value() + r.top()
                     - self.code_editor.viewport().height() // 2)

    def _code_sym_clear_results(self):
        self._code_sym_hits = None

    def _code_find_symbol(self):
        q = (self.code_sym_find.text() or "").strip()
        if not q:
            return
        root = self._code_project or ""
        if not root:
            self._code_say(t("code.sym_no_project"), bad=True)
            return

        def work():
            return self._mcp_call_json("code_symbols",
                                       {"query": q, "path": root, "limit": 60})

        def done(res):
            hits = (res or {}).get("matches") if isinstance(res, dict) else None
            self.code_outline.clear()
            if not hits:
                self.code_outline_label.setText(
                    t("code.sym_none", q=q).upper())
                self._code_sym_hits = None
                return
            self.code_outline_label.setText(
                t("code.sym_found", n=len(hits), q=q).upper())
            self._code_sym_hits = {}
            for h in hits:
                _f, _l = h.get("file", ""), int(h.get("line", 0) or 0)
                it = QListWidgetItem(f"{h.get('symbol', '?')}   {_f}:{_l}")
                it.setToolTip(h.get("declaration", ""))
                it.setData(Qt.ItemDataRole.UserRole, _l)
                self._code_sym_hits[id(it)] = (_f, _l)
                self.code_outline.addItem(it)

        self._bg(work, done)

    def _code_goto_symbol(self, item):
        line = item.data(Qt.ItemDataRole.UserRole)
        if not line:
            return
        _hits = getattr(self, "_code_sym_hits", None)
        if _hits and id(item) in _hits:
            _f, _l = _hits[id(item)]
            _abs = _f if os.path.isabs(_f) else os.path.join(
                self._code_project or "", _f)
            self._code_open_in_tab(_abs)
            self._code_goto_line(_l)
            self.code_editor.setFocus()
            return
        doc = self.code_editor.document()
        block = doc.findBlockByNumber(max(0, int(line) - 1))
        if not block.isValid():
            return
        self.code_editor.setTextCursor(QTextCursor(block))
        self._code_center_on_cursor()
        self.code_editor.setFocus()

    def _code_run_outline(self):
        path = self._code_open_path
        if not path:
            return
        text = self.code_editor.toPlainText()
        rev = self._code_buf_rev

        def work():
            return self._mcp_try("code_outline",
                                 {"path": path, "content": text}, timeout=25)

        def done(res):
            if rev != self._code_buf_rev or path != self._code_open_path:
                return
            ok, r, _ = res if isinstance(res, tuple) else (False, {}, "")
            self.code_outline.clear()
            syms = (r or {}).get("symbols") or []
            GLYPH = {"class": "⬡", "struct": "⬡", "interface": "◇",
                     "enum": "∷", "module": "▣", "namespace": "▣",
                     "function": "ƒ", "method": "ƒ", "constructor": "ƒ",
                     "constant": "≡", "field": "▪", "property": "▪",
                     "variable": "·", "map": "·", "object": "·"}
            GROUP_ORDER = ("module", "class", "function", "constant",
                           "field", "variable", "other")
            GROUP_OF = {"module": "module", "namespace": "module",
                        "class": "class", "struct": "class",
                        "interface": "class", "enum": "class",
                        "function": "function", "method": "function",
                        "constructor": "function",
                        "constant": "constant",
                        "field": "field", "property": "field",
                        "variable": "variable", "map": "variable",
                        "object": "variable"}

            KCOLOR = {"class": "#e5c07b", "struct": "#e5c07b",
                      "interface": "#e5c07b", "enum": "#e5c07b",
                      "module": "#98c379", "namespace": "#98c379",
                      "function": "#61afef", "method": "#61afef",
                      "constructor": "#61afef",
                      "constant": "#56b6c2",
                      "field": "#d19a66", "property": "#d19a66",
                      "variable": "#abb2bf", "map": "#abb2bf",
                      "object": "#abb2bf"}

            def _add(sym):
                k = str(sym.get("kind", "")).lower()
                it = QListWidgetItem(
                    "  " * sym.get("depth", 0)
                    + f"{GLYPH.get(k, '•')} {sym.get('name', '?')}")
                it.setForeground(QColor(KCOLOR.get(k, "#e6edf7")))
                it.setToolTip(f"{sym.get('kind', '')} · "
                              f"L{sym.get('line', 1)}")
                it.setData(Qt.ItemDataRole.UserRole, sym.get("line", 1))
                self.code_outline.addItem(it)

            if any(s.get("depth", 0) for s in syms):
                for sym in syms:
                    _add(sym)
            else:
                groups = {}
                for sym in syms:
                    g = GROUP_OF.get(str(sym.get("kind", "")).lower(),
                                     "other")
                    groups.setdefault(g, []).append(sym)
                for g in GROUP_ORDER:
                    members = groups.get(g)
                    if not members:
                        continue
                    if len(groups) > 1:
                        hd = QListWidgetItem(f"— {g} —")
                        hd.setFlags(Qt.ItemFlag.NoItemFlags)
                        hd.setForeground(QColor("#e6edf7"))
                        self.code_outline.addItem(hd)
                    for sym in sorted(members,
                                      key=lambda s: s.get("line", 0)):
                        _add(sym)
            src = (r or {}).get("source", "?")
            self.code_outline_label.setText(
                t("code.outline_n", n=len(syms),
                  src=t("code.outline_lsp") if src == "lsp"
                  else t("code.outline_scan")))

        self._bg(work, done)

    def _code_run_diagnostics(self):
        path = self._code_open_path
        if not path:
            return
        text = self.code_editor.toPlainText()
        rev = self._code_buf_rev

        def work():
            return self._mcp_try("code_diagnostics",
                                 {"path": path, "content": text}, timeout=20)

        def done(res):
            if rev != self._code_buf_rev or path != self._code_open_path:
                return
            ok, r, _ = res if isinstance(res, tuple) else (False, {}, "")
            self._code_paint_diagnostics(r if ok else {})

        self._bg(work, done)

    def _code_apply_sels(self):
        self.code_editor.setExtraSelections(
            list(getattr(self, "_code_sel_diag", []))
            + list(getattr(self, "_code_sel_find", [])))

    def _code_paint_diagnostics(self, r):
        from PyQt6.QtWidgets import QTextEdit as _QTE
        diags = (r or {}).get("diagnostics") or []
        sels = []
        doc = self.code_editor.document()
        for d in diags:
            block = doc.findBlockByNumber(max(0, d.get("line", 1) - 1))
            if not block.isValid():
                continue
            sel = _QTE.ExtraSelection()
            cur = QTextCursor(block)
            cur.setPosition(block.position() + max(0, d.get("col", 1) - 1))
            end_line = d.get("end_line", d.get("line", 1))
            if end_line == d.get("line") and d.get("end_col", 0) > d.get("col", 1):
                cur.setPosition(block.position() + d["end_col"] - 1,
                                QTextCursor.MoveMode.KeepAnchor)
            else:
                cur.movePosition(QTextCursor.MoveOperation.EndOfBlock,
                                 QTextCursor.MoveMode.KeepAnchor)
            sel.cursor = cur
            fmt = QTextCharFormat()
            fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
            fmt.setUnderlineColor(QColor(
                "#f85149" if d.get("severity") == "error" else "#fbbf24"))
            fmt.setToolTip(f"{d.get('severity','')}: {d.get('message','')}"
                           + (f"  ({d['source']})" if d.get("source") else ""))
            sel.format = fmt
            sels.append(sel)
        self._code_sel_diag = sels
        self._code_apply_sels()
        rel = os.path.relpath(self._code_open_path, self._code_project)
        mark = "● " if self._code_dirty else ""
        if not (r or {}).get("available", False):
            _why = (r or {}).get("why") or ""
            self.code_open_label.setText(
                f"{mark}{rel}   —   {t('code.diag_none')}"
                + (f"  ({_why})" if _why else ""))
        elif diags:
            errs = sum(1 for d in diags if d.get("severity") == "error")
            self.code_open_label.setText(
                f"{mark}{rel}   —   " + t("code.diag_n", n=len(diags), e=errs,
                                          srv=r.get("server", "?")))
        else:
            self.code_open_label.setText(
                f"{mark}{rel}   —   " + t("code.diag_clean",
                                          srv=r.get("server", "?")))

    def _code_confirm_discard(self):
        r = QMessageBox.question(
            self, t("code.unsaved_title"), t("code.unsaved_body"),
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        return r == QMessageBox.StandardButton.Discard

    def _code_save_file(self):
        if not self._code_open_path:
            self._code_say(t("code.no_file"), bad=True)
            return
        if (self._code_bufs.get(self._code_open_path) or {}).get("readonly"):
            self._code_say(t("code.readonly_commit"), bad=True)
            return
        _b = self._code_buf()
        _disk_m = self._proj_mtime(self._code_open_path)
        if (_b is not None and _disk_m is not None
                and _b.get("mtime") is not None
                and _disk_m != _b.get("mtime")):
            _r = QMessageBox.question(
                self, t("code.disk_changed_title"),
                t("code.disk_changed_body",
                  path=os.path.basename(self._code_open_path)),
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Cancel)
            if _r != QMessageBox.StandardButton.Save:
                return
        ok, r, err = self._mcp_try(
            "code_write_file", {"path": self._code_open_path,
                                "content": self.code_editor.toPlainText()},
            timeout=30)
        if not ok:
            self._code_say(t("code.save_fail", err=err), bad=True)
            return
        self._code_dirty = False
        _b = self._code_buf()
        if _b is not None:
            _b["dirty"] = False
            _b["text"] = self.code_editor.toPlainText()
            if _b.get("doc") is not None:
                _b["doc"].setModified(False)
            _m = self._proj_mtime(self._code_open_path)
            if _m is not None:
                _b["mtime"] = _m
        self._code_paint_open_label()
        self._code_rebuild_tabs()
        rel = os.path.relpath(self._code_open_path, self._code_project)
        self._code_refresh(then=lambda: self._code_say(
            t("code.saved", path=rel, n=r.get("bytes", 0))))

    def _code_recents(self):
        from PyQt6.QtCore import QSettings
        try:
            out = json.loads(QSettings("hoplita", "hoplita")
                             .value("code_recents", "[]") or "[]")
        except (ValueError, TypeError):
            return []
        return [p for p in out if isinstance(p, str)][:6]

    def _code_remember_project(self, path, forget=False):
        from PyQt6.QtCore import QSettings
        keep = [p for p in self._code_recents() if p != path]
        if not forget and path and (self._proj_stat([path]).get(path) or {}).get("isdir"):
            keep.insert(0, path)
        QSettings("hoplita", "hoplita").setValue(
            "code_recents", json.dumps(keep[:6]))

    def _code_welcome(self):
        import html as _h
        rec = ""
        _recs = self._code_recents()
        _st = self._proj_stat(_recs)
        for p in _recs:
            if not (_st.get(p) or {}).get("isdir"):
                continue
            rec += (
                '<tr><td style="padding:3px 0;">'
                f'<a href="hoplita:open:{_h.escape(p)}" style="color:#7dd3fc;'
                'text-decoration:none;font-size:12px;">'
                f'{_h.escape(os.path.basename(p.rstrip("/")) or p)}</a>'
                f'<span style="color:#4a5b74;font-size:12px;">   {_h.escape(p)}'
                '</span></td></tr>')
        if rec:
            rec = (f'<p style="color:#e6edf7;font-size:12px;letter-spacing:1px;'
                   f'margin:22px 0 2px 0;">{t("code.welcome_recent").upper()}'
                   f'</p><table cellspacing="0" cellpadding="0">{rec}</table>')
        steps = ""
        for i, key in enumerate(("code.step1", "code.step2",
                                 "code.step3", "code.step4")):
            here = (i == 0)
            steps += (
                '<tr>'
                f'<td valign="top" style="color:{"#3b82f6" if here else "#3d4b63"};'
                f'font-size:13pt;font-weight:bold;padding:0 10px 8px 0;">{i + 1}</td>'
                f'<td valign="top" style="color:{"#c8d6ea" if here else "#6b7c96"};'
                f'font-size:12px;padding:0 0 8px 0;">{t(key)}'
                + (f'<span style="color:#3b82f6;font-size:12px;">'
                   f'&nbsp;&nbsp;← {t("code.step_here")}</span>' if here else "")
                + '</td></tr>')
        self.code_diff.setHtml(
            '<div style="padding:26px 30px;">'
            f'<div style="color:#e6edf7;font-size:17pt;font-weight:bold;">'
            f'{t("code.welcome_title")}</div>'
            f'<p style="color:#e6edf7;font-size:12px;max-width:520px;">'
            f'{t("code.welcome_lead")}</p>'
            '<p style="margin-top:18px;"><a href="hoplita:pick" '
            'style="color:#d1fae5;background:#065f46;text-decoration:none;'
            'font-size:12pt;font-weight:bold;">'
            f'&nbsp;&nbsp;{t("code.welcome_pick")}&nbsp;&nbsp;</a></p>'
            + rec +
            f'<p style="color:#e6edf7;font-size:12px;letter-spacing:1px;'
            f'margin:26px 0 6px 0;">{t("code.welcome_cycle").upper()}</p>'
            f'<table cellspacing="0" cellpadding="0">{steps}</table></div>')
        self.code_status.setText(t("code.no_project"))
        self.code_status.setStyleSheet(
            "QLabel { color:#e6edf7; font-size:12px; }")
        if getattr(self, "code_left_stack", None) is not None \
                and self._code_view == 0:
            self._code_activity(1, force=True)
        else:
            self._code_paint_side_head()
            if self._code_view in (3, 6) \
                    and getattr(self, "code_left_stack", None) is not None:
                self.code_left_stack.setCurrentIndex(1)
                self._code_prev_view = 1
                self._code_run_paint()
        if getattr(self, "code_right_stack", None) is not None:
            self.code_right_stack.setCurrentIndex(0)

    def _code_pick_project(self):
        _pin, _remote = self._tab_runs_remotely(self._turn_ctx())
        if _remote:
            from PyQt6.QtWidgets import QInputDialog
            d, okp = QInputDialog.getText(self, t("code.browse"),
                                          t("code.pick_remote", name=_pin.get("name", "?")),
                                          text=self._code_project or "")
            d = (d or "").strip() if okp else ""
        else:
            d = QFileDialog.getExistingDirectory(self, t("code.browse"),
                                                 self._code_project or os.path.expanduser("~"))
        if d:
            self.code_project_edit.setText(d)
            self._code_open_project()

    def _code_offer_git(self, path):
        try:
            p = path or ""
            _st = self._proj_stat([p]).get(p) or {}
            if not p or not _st.get("isdir"):
                return
            if self._proj_post("git", {"path": p, "action": "check"}).get("is_git"):
                return
            _empty = bool(_st.get("empty"))
        except Exception:
            return
        _msg = (t("code.git_offer_empty", path=p) if _empty
                else t("code.git_offer", path=p))
        if QMessageBox.question(
                self, t("code.git_offer_title"), _msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes) != QMessageBox.StandardButton.Yes:
            self._code_say(t("code.git_declined"), bad=False)
            return
        try:
            _r = self._proj_post("git", {"path": p, "action": "init"})
        except Exception as e:
            self._code_say(t("code.git_failed", err=str(e)[:120]), bad=True)
            return
        if not _r.get("ok"):
            self._code_say(t("code.git_failed", err=(_r.get("out") or "?")[:120]), bad=True)
            return
        self._code_say(t("code.git_done", path=p))

    def _code_open_project(self):
        p = self.code_project_edit.text().strip()
        if not p:
            return
        self._code_project = p
        ctx = getattr(self, "_chat_ctxs", {}).get(getattr(self, "_active_tab_id", None))
        if ctx is not None:
            ctx.code_project = p
        self._code_remember_project(p)
        self._code_verify_load_list()
        self._code_run_paint()
        self._code_refresh()
        _name = self._code_ask_project_name(p)

        def _knock():
            _args = {"path": p}
            if _name:
                _args["name"] = _name
            ok, r, _ = self._mcp_try("code_index", _args, timeout=60)
            if ok and isinstance(r, dict) and r.get("memory_born"):
                self.signals.message.emit(
                    t("code.project_born",
                      name=_name or os.path.basename(p.rstrip("/\\")) or p),
                    "system")
        threading.Thread(target=_knock, daemon=True,
                         name="code-open-knock").start()
        self._code_offer_git(p)
        self._code_activity(0, force=True)
        ok, tc, _ = self._mcp_try("code_toolchain", {"path": p}, timeout=30)
        self._code_toolchain = tc if ok else {}
        self.code_mode_badge.setVisible(bool(ok and tc.get("languages")))
        if not ok:
            return
        if tc.get("ready"):
            langs = ", ".join(L["language"] for L in tc.get("languages", []))
            self.code_mode_badge.setText(t("code.tooling_ok_badge", langs=langs))
            self.code_mode_badge.setStyleSheet(
                "QPushButton { color:#3fb950; font-size:12px; margin-left:10px; "
                "border:none; background:transparent; text-align:left; padding:0; } "
                "QPushButton:hover { text-decoration:underline; }")
        else:
            langs = ", ".join(L["language"] for L in tc.get("languages", [])
                              if L.get("missing")) or "?"
            self.code_mode_badge.setText(t("code.reduced_badge", langs=langs))
            self.code_mode_badge.setStyleSheet(
                "QPushButton { color:#fbbf24; font-size:12px; margin-left:10px; "
                "border:none; background:transparent; text-align:left; padding:0; } "
                "QPushButton:hover { color:#fcd34d; text-decoration:underline; }")

    def _code_goto_go(self):
        txt = (self.code_goto.text() or "").strip()
        if not txt:
            return
        try:
            n = int(txt.lstrip("+ ").split(":")[0])
        except ValueError:
            self._code_say(t("code.goto_nan", what=txt[:20]), bad=True)
            return
        if not self._code_open_path:
            self._code_say(t("code.no_file"), bad=True)
            return
        self.code_editor.goto_line(n)

    def _code_new_file(self):
        if not self._code_project:
            self._code_say(t("code.no_project"), bad=True)
            return
        from PyQt6.QtWidgets import QInputDialog
        name, okp = QInputDialog.getText(self, t("code.new_title"),
                                         t("code.new_prompt"))
        name = (name or "").strip().lstrip("/\\")
        if not okp or not name:
            return
        path = os.path.normpath(os.path.join(self._code_project, name))
        if not path.startswith(os.path.normpath(self._code_project) + os.sep):
            self._code_say(t("code.new_outside", name=name), bad=True)
            return
        if (self._proj_stat([path]).get(path) or {}).get("exists"):
            self._code_say(t("code.new_exists", name=name), bad=True)
            self._code_open_in_tab(path)
            return
        try:
            self._proj_post("mkdir", {"path": os.path.dirname(path)})
        except Exception as e:
            self._code_say(t("msg.error", e=str(e)), bad=True)
            return
        res = self._mcp_call_json("code_write_file",
                                  {"path": path, "content": ""}, timeout=30)
        if isinstance(res, dict) and res.get("error"):
            self._code_say(t("code.new_fail", name=name,
                             err=str(res["error"])[:160]), bad=True)
            return
        self._code_open_in_tab(path)
        self._code_load_tree()
        self._code_say(t("code.new_ok", name=name))

    def _code_close_project(self, silent=False):
        ctx = getattr(self, "_chat_ctxs", {}).get(
            getattr(self, "_active_tab_id", None))
        had = bool(getattr(self, "_code_project", "")
                   or getattr(ctx, "code_project", ""))
        if ctx is not None:
            ctx.code_project = ""
        self._code_project = ""
        self._code_open_path = ""
        self._code_dirty = False
        self._code_diff_cache = {}
        self._code_bufs.clear()
        del self._code_bufs_order[:]
        if getattr(self, "code_tabs", None) is not None:
            self._code_rebuild_tabs()
        self._code_toolchain = {}
        for _w, _how in ((getattr(self, "code_project_edit", None), "text"),
                         (getattr(self, "code_branch", None), "text"),
                         (getattr(self, "code_status", None), "text"),
                         (getattr(self, "code_find", None), "text"),
                         (getattr(self, "code_find_count", None), "text"),
                         (getattr(self, "code_verify_badge", None), "text"),
                         (getattr(self, "code_open_label", None), "text")):
            if _w is not None:
                _w.setText("")
        for _w in (getattr(self, "code_files", None),
                   getattr(self, "code_tree", None),
                   getattr(self, "code_outline", None),
                   getattr(self, "code_run_out", None)):
            if _w is not None:
                _w.clear()
        self._code_verify_load_list()
        self._code_run_paint()
        if getattr(self, "code_editor", None) is not None:
            self.code_editor.blockSignals(True)
            self.code_editor.clear()
            self.code_editor.blockSignals(False)
            self._code_sel_diag = []
            self._code_sel_find = []
            self._code_find_hits = []
        if getattr(self, "code_mode_badge", None) is not None:
            self.code_mode_badge.setVisible(False)
        if getattr(self, "code_open_label", None) is not None:
            self.code_open_label.setText(t("code.no_file"))
        self._code_welcome()
        if not silent and had:
            self.add_message(t("code.closed_msg"), "system")
        return had

    def _code_say(self, msg, bad=False, big=False):
        self.code_status.setText(msg)
        self.code_status.setStyleSheet(
            f"QLabel {{ color:{'#f87171' if bad else '#e6edf7'}; font-size:12px; }}")
        if big:
            import html as _h
            self.code_diff.setHtml(
                f'<div style="color:{"#f87171" if bad else "#e6edf7"};'
                f'font-size:12px;padding:24px;white-space:pre-wrap;">'
                f'{_h.escape(msg)}</div>')

    def _code_no_mine(self):
        if not self.code_only_mine.isChecked():
            return False
        ok, dd, _ = self._mcp_try("git_diff", {"path": self._code_project},
                                  timeout=30)
        n = len(self._split_diff(dd.get("diff", ""))) if ok else 0
        if not n:
            return False
        import html as _h
        self.code_diff.setHtml(
            '<div style="padding:24px;">'
            f'<div style="color:#c8d6ea;font-size:12px;">'
            f'{_h.escape(t("code.mine_none_but", n=n))}</div>'
            '<p style="margin-top:14px;"><a href="hoplita:allchanges" '
            'style="color:#7dd3fc;text-decoration:none;font-size:12px;">'
            f'→ {_h.escape(t("code.see_all"))}</a></p></div>')
        self.code_status.setText(t("code.mine_none_but", n=n))
        self.code_status.setStyleSheet(
            "QLabel { color:#e6edf7; font-size:12px; }")
        return True

    def _code_reload_clean_bufs(self):
        bufs = getattr(self, "_code_bufs", None)
        if not bufs:
            return
        _st = self._proj_stat(bufs.keys())
        for path, b in bufs.items():
            m = (_st.get(path) or {}).get("mtime")
            if m is None:
                continue
            if b.get("mtime") is None or m == b.get("mtime"):
                continue
            if b.get("dirty"):
                continue
            try:
                raw, m = self._proj_read(path)
            except Exception:
                continue
            b["text"] = raw.decode("utf-8", errors="replace")
            b["mtime"] = m
            if path != getattr(self, "_code_open_path", ""):
                b["doc"] = b["hl"] = None
            if path == getattr(self, "_code_open_path", ""):
                _pos = self.code_editor.textCursor().position()
                self._code_loading = True
                try:
                    self.code_editor.blockSignals(True)
                    self.code_editor.setPlainText(b["text"])
                    self.code_editor.blockSignals(False)
                    self._code_hl.rehighlight_text(b["text"])
                finally:
                    self._code_loading = False
                cur = self.code_editor.textCursor()
                cur.setPosition(min(_pos, len(b["text"])))
                self.code_editor.setTextCursor(cur)
                self._code_dirty = False
                self._code_paint_open_label()

    def _code_refresh(self, then=None):
        if then is not None and not callable(then):
            then = None
        self.code_files.clear()
        self.code_diff.clear()
        self._code_reload_clean_bufs()
        if not self._code_project:
            self._code_welcome()
            if then:
                then()
            return
        self._code_refresh_gen = getattr(self, "_code_refresh_gen", 0) + 1
        gen = self._code_refresh_gen
        project = self._code_project
        mine = self.code_only_mine.isChecked()

        def work():
            ok, st, err = self._mcp_try("git_status", {"path": project})
            if not ok:
                return {"stage": "status", "err": err}
            ok, dd, err = self._mcp_try(
                "git_diff", {"path": project, "since_checkpoint": mine},
                timeout=30)
            retried = False
            if not ok and err == "no_checkpoints":
                return {"stage": "no_checkpoints", "st": st}
            if not ok and err == "checkpoint_lost":
                retried = True
                ok, dd, err = self._mcp_try(
                    "git_diff", {"path": project}, timeout=30)
            if not ok:
                return {"stage": "diff", "err": err, "st": st,
                        "retried": retried}
            return {"stage": "ok", "st": st, "dd": dd, "retried": retried}

        def done(r):
            if gen != getattr(self, "_code_refresh_gen", 0) \
                    or project != self._code_project:
                return
            self._code_refresh_paint(r if isinstance(r, dict) else {})
            if then:
                then()

        self._bg(work, done)

    def _code_refresh_paint(self, r):
        st = r.get("st") or {}
        if r.get("stage") == "status":
            self.code_branch.setText("")
            self._code_say(t("code.status_fail", err=r.get("err", "")),
                           bad=True, big=True)
            return
        self.code_branch.setText(t("code.on_branch",
                                   branch=st.get("branch", "?")))
        if r.get("stage") == "no_checkpoints":
            if not self._code_no_mine():
                self._code_say(t("code.nothing_yet"), big=True)
            return
        warn = ""
        if r.get("retried"):
            self.code_only_mine.blockSignals(True)
            self.code_only_mine.setChecked(False)
            self.code_only_mine.blockSignals(False)
            warn = t("code.checkpoint_lost")
        if r.get("stage") == "diff":
            self._code_say(t("code.diff_fail", err=r.get("err", "")),
                           bad=True, big=True)
            return
        dd = r.get("dd") or {}
        self._code_diff_cache = self._split_diff(dd.get("diff", ""))
        files = list(self._code_diff_cache)
        if files:
            allitem = QListWidgetItem(t("code.all_files", n=len(files)))
            allitem.setData(Qt.ItemDataRole.UserRole, "*")
            self.code_files.addItem(allitem)
        for f in files:
            hunk = self._code_diff_cache[f].splitlines()
            adds = sum(1 for x in hunk if x.startswith("+") and not x.startswith("+++"))
            dels = sum(1 for x in hunk if x.startswith("-") and not x.startswith("---"))
            it = QListWidgetItem(f"{f}   +{adds} −{dels}")
            it.setData(Qt.ItemDataRole.UserRole, f)
            self.code_files.addItem(it)
        untracked = st.get("untracked", [])
        if not files:
            if not warn and self._code_no_mine():
                return
            msg = (t("code.clean") if not untracked
                   else t("code.clean_untracked", n=len(untracked)))
        else:
            self.code_files.setCurrentRow(0)
            msg = (t("code.n_changed", n=len(files)) +
                   (" · " + t("code.truncated") if dd.get("truncated") else ""))
        self._code_say(f"{warn} {msg}".strip() if warn else msg,
                       bad=bool(warn), big=not files)

    @staticmethod
    def _split_diff(diff_text):
        out, current, buf = {}, None, []
        for line in (diff_text or "").splitlines():
            if line.startswith("diff --git "):
                if current:
                    out[current] = "\n".join(buf)
                buf = [line]
                part = line.split(" b/", 1)
                current = part[1] if len(part) == 2 else line[11:]
            else:
                buf.append(line)
        if current:
            out[current] = "\n".join(buf)
        return out

    def _code_show_diff(self, item, _prev=None):
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        text = ("\n".join(self._code_diff_cache.values()) if key == "*"
                else self._code_diff_cache.get(key, ""))
        self.code_diff.setHtml(self._render_diff_html(text))

    @staticmethod
    def _render_diff_html(diff_text, reject=True):
        import html as _h
        rows = []
        cur_file, hunk_no = "", -1
        for line in (diff_text or "").splitlines():
            if line.startswith("diff --git "):
                part = line.split(" b/", 1)
                cur_file = part[1] if len(part) == 2 else ""
                hunk_no = -1
            elif line.startswith("@@"):
                hunk_no += 1
            e = _h.escape(line) or "&nbsp;"
            if line.startswith("+++") or line.startswith("---"):
                style = "color:#e6edf7;"
            elif line.startswith("diff --git "):
                style = "color:#e6edf7;font-weight:700;background:#111d33;"
            elif line.startswith("@@"):
                style = "color:#a371f7;background:#161b2e;"
                if reject and cur_file:
                    rows.append(
                        f'<div style="{style}white-space:pre-wrap;'
                        f'font-family:monospace;">{e}'
                        f'&nbsp;&nbsp;<a href="hoplita:reject:{hunk_no}:'
                        f'{_h.escape(cur_file)}" style="color:#f85149;'
                        f'text-decoration:none;">'
                        f'✕ {t("code.reject_hunk")}</a></div>')
                    continue
            elif line.startswith("+"):
                style = "color:#3fb950;background:#0d2a16;"
            elif line.startswith("-"):
                style = "color:#f85149;background:#2d0f11;"
            else:
                style = "color:#9aa8bd;"
            rows.append(f'<div style="{style}white-space:pre-wrap;'
                        f'font-family:monospace;">{e}</div>')
        return "".join(rows) or (
            '<div style="color:#e6edf7;font-family:monospace;">—</div>')

    def _code_search_run(self):
        q = self.code_search_box.text().strip()
        self.code_search_list.clear()
        self.code_search_count.setText("")
        if not q:
            return
        if not self._code_project:
            self._code_say(t("code.no_project"), bad=True)
            return
        ok, r, err = self._mcp_try(
            "code_search", {"path": self._code_project, "query": q,
                            "per_scope": 0},
            timeout=30)
        if not ok:
            self._code_say(t("msg.error", e=err), bad=True)
            return
        hits = (r or {}).get("results") or []
        for h in hits:
            it = QListWidgetItem(f"{h.get('file', '?')}:{h.get('line', 0)}\n"
                                 f"    {h.get('text', '')[:90]}")
            it.setData(Qt.ItemDataRole.UserRole,
                       (h.get("file", ""), int(h.get("line", 1) or 1)))
            it.setToolTip(h.get("text", ""))
            self.code_search_list.addItem(it)
        if not hits:
            self.code_search_count.setText(t("code.search_none"))
        else:
            label = t("code.search_total", h=r.get("hits_total", len(hits)),
                      f=r.get("files_total", 0))
            if (r.get("page") or {}).get("cursor"):
                label += t("code.search_page", n=len(hits))
            self.code_search_count.setText(label)

    def _code_search_open(self, item):
        if item is None:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        rel, line = data
        self._code_open_in_tab(os.path.join(self._code_project, rel))
        self._code_goto_line(line)

    def _code_goto_line(self, line):
        doc = self.code_editor.document()
        block = doc.findBlockByNumber(max(0, int(line) - 1))
        if not block.isValid():
            return
        cur = self.code_editor.textCursor()
        cur.setPosition(block.position())
        self.code_editor.setTextCursor(cur)
        self._code_center_on_cursor()
        self.code_editor.setFocus()

    @staticmethod
    def _code_quick_matches(paths, pattern):
        pat = (pattern or "").strip().lower()
        if not pat:
            return list(paths)[:50]
        starts, base_in, path_in = [], [], []
        for p in paths:
            b = os.path.basename(p).lower()
            if b.startswith(pat):
                starts.append(p)
            elif pat in b:
                base_in.append(p)
            elif pat in p.lower():
                path_in.append(p)
        return (starts + base_in + path_in)[:50]

    def _code_quick_open(self):
        if not self._code_project:
            return
        from PyQt6.QtWidgets import QDialog
        try:
            files = sorted(self._proj_get("tree", {"root": self._code_project,
                                                   "limit": 5000}).get("files") or [])
        except Exception as e:
            self._code_say(t("msg.error", e=str(e)), bad=True)
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(t("code.quickopen_title"))
        dlg.resize(560, 420)
        v = QVBoxLayout(dlg)
        box = QLineEdit()
        box.setPlaceholderText(t("code.quickopen_hint"))
        v.addWidget(box)
        lst = QListWidget()
        v.addWidget(lst, 1)

        def _fill(pat=""):
            lst.clear()
            for p in self._code_quick_matches(files, pat):
                lst.addItem(p)
            if lst.count():
                lst.setCurrentRow(0)

        def _open():
            it = lst.currentItem()
            if it is None:
                return
            dlg.accept()
            self._code_open_in_tab(
                os.path.join(self._code_project, it.text()))

        box.textChanged.connect(_fill)
        box.returnPressed.connect(_open)
        lst.itemActivated.connect(lambda _i: _open())
        _orig_key = box.keyPressEvent

        def _key(ev):
            if ev.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                row = lst.currentRow() + (1 if ev.key() == Qt.Key.Key_Down
                                          else -1)
                lst.setCurrentRow(max(0, min(lst.count() - 1, row)))
                return
            _orig_key(ev)

        box.keyPressEvent = _key
        _fill()
        box.setFocus()
        dlg.exec()

    def _code_load_history(self):
        self.code_hist.clear()
        if not self._code_project:
            self._code_say(t("code.no_project"), bad=True)
            return
        ok, r, err = self._mcp_try("git_log", {"path": self._code_project,
                                               "limit": 50}, timeout=30)
        if not ok:
            self._code_say(t("code.hist_fail", err=err), bad=True)
            return
        commits = (r or {}).get("commits") or []
        if not commits:
            self._code_say(t("code.hist_none"), big=True)
            return
        for c in commits:
            it = QListWidgetItem(f"{c.get('sha', '?')}  {c.get('subject', '')}"
                                 f"\n        {c.get('author', '')} · "
                                 f"{c.get('when', '')}")
            it.setData(Qt.ItemDataRole.UserRole, c.get("sha", ""))
            it.setToolTip(c.get("subject", ""))
            self.code_hist.addItem(it)

    def _code_show_commit(self, item, _prev=None):
        if item is None:
            return
        sha = item.data(Qt.ItemDataRole.UserRole)
        if not sha:
            return
        vs_now = self.code_hist_vs_now.isChecked()
        if vs_now:
            ok, r, err = self._mcp_try(
                "git_diff", {"path": self._code_project, "commit": sha},
                timeout=30)
        else:
            ok, r, err = self._mcp_try(
                "git_show", {"path": self._code_project, "sha": sha},
                timeout=30)
        if not ok:
            self._code_say(t("code.hist_fail", err=err), bad=True)
            return
        import html as _h
        head = (t("code.hist_head_vs", sha=sha) if vs_now
                else t("code.hist_head_commit", sha=sha,
                       subject=r.get("subject", "")))
        body = r.get("diff", "")
        if vs_now and not body.strip():
            body_html = (f'<div style="color:#3fb950;font-family:monospace;'
                         f'padding:8px 0;">{_h.escape(t("code.hist_same"))}'
                         f'</div>')
        else:
            body_html = self._render_diff_html(body, reject=False)
        note = ""
        if r.get("truncated"):
            note = (f'<div style="color:#f59e0b;font-family:monospace;'
                    f'">{_h.escape(t("code.truncated"))}</div>')
        self.code_diff.setHtml(
            f'<div style="color:#e6edf7;font-family:monospace;'
            f'padding:2px 0 8px 0;">{_h.escape(head)}</div>' + body_html + note)
        self._code_set_mode(0)

    def _code_undo(self):
        if not self._code_project:
            self._code_say(t("code.no_project"), bad=True)
            return
        ok, r, err = self._mcp_try("code_undo", {"path": self._code_project},
                                   timeout=30)
        if not ok:
            self._code_say(t("code.undo_fail", err=err), bad=True)
            return
        def _say_undo():
            if r.get("files_failed"):
                self._code_say(t("code.undo_partial",
                                 files=", ".join(r["files_failed"])), bad=True)
                return
            self._code_say(t("code.undone",
                             n=len(r.get("files_restored", [])),
                             d=len(r.get("files_removed", [])),
                             left=r.get("remaining_checkpoints", 0)))
        self._code_refresh(then=_say_undo)

    def _setup_notes_page(self, parent_layout):
        header = QHBoxLayout()
        header.setContentsMargins(16, 12, 16, 12)
        header.addWidget(QLabel(f"<b>{t('notes.title')}</b>"))
        header.addStretch()
        refresh_btn = QPushButton(t("cron.update"))
        refresh_btn.clicked.connect(self.refresh_notes_lists)
        header.addWidget(refresh_btn)
        parent_layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        parent_layout.addWidget(splitter, 1)

        cat_widget = QWidget()
        cat_widget.setMaximumWidth(200)
        cat_layout = QVBoxLayout(cat_widget)
        cat_layout.setContentsMargins(4, 4, 4, 4)
        cat_layout.addWidget(QLabel(f"<b>{t('notes.categories')}</b>"))
        cat_row = QHBoxLayout()
        self.category_entry = QLineEdit()
        self.category_entry.setPlaceholderText(t("notes.new_category"))
        cat_row.addWidget(self.category_entry, 1)
        add_cat_btn = QPushButton("➕")
        add_cat_btn.clicked.connect(self.add_category_from_ui)
        cat_row.addWidget(add_cat_btn)
        cat_layout.addLayout(cat_row)
        self.categories_list = QListWidget()
        self.categories_list.currentRowChanged.connect(self.on_category_selected)
        cat_layout.addWidget(self.categories_list, 1)
        splitter.addWidget(cat_widget)

        list_widget = QWidget()
        list_widget.setMaximumWidth(250)
        list_layout = QVBoxLayout(list_widget)
        list_layout.setContentsMargins(4, 4, 4, 4)
        self.notes_title_label = QLabel(f"<b>{t('notes.notes_list')}</b>")
        list_layout.addWidget(self.notes_title_label)
        self.notes_list = QListWidget()
        self.notes_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.notes_list.currentRowChanged.connect(self._on_note_selected)
        list_layout.addWidget(self.notes_list, 1)
        del_note_btn = QPushButton(t("notes.delete_note"))
        del_note_btn.clicked.connect(self._delete_selected_note)
        list_layout.addWidget(del_note_btn)
        sel_row = QHBoxLayout()
        sel_all_btn = QPushButton("☑ All")
        sel_all_btn.clicked.connect(lambda: self.notes_list.selectAll())
        sel_row.addWidget(sel_all_btn)
        clear_cat_btn = QPushButton("🗑 Clear")
        clear_cat_btn.setToolTip(t("ui.clear_cat_tip"))
        clear_cat_btn.clicked.connect(self._clear_category)
        clear_cat_btn.setStyleSheet("QPushButton { color: #f87171; }")
        sel_row.addWidget(clear_cat_btn)
        list_layout.addLayout(sel_row)
        splitter.addWidget(list_widget)

        editor_widget = QWidget()
        editor_layout = QVBoxLayout(editor_widget)
        editor_layout.setContentsMargins(4, 4, 4, 4)

        title_row = QHBoxLayout()
        self.note_title_entry = QLineEdit()
        self.note_title_entry.setPlaceholderText(t("notes.title_placeholder"))
        title_row.addWidget(self.note_title_entry, 1)
        attach_btn = QPushButton("📎")
        attach_btn.setToolTip(t("notes.attach"))
        attach_btn.clicked.connect(self.attach_file_to_note)
        title_row.addWidget(attach_btn)
        self.attached_file_label = QLabel("")
        title_row.addWidget(self.attached_file_label)
        clear_att_btn = QPushButton("❌")
        clear_att_btn.setToolTip(t("notes.attach_clear_tip"))
        clear_att_btn.clicked.connect(self.clear_attachment)
        clear_att_btn.setStyleSheet(
            "QPushButton { background:#1e293b; color:#f87171; border:1px "
            "solid #334155; border-radius:6px; padding:4px 8px; "
            "min-height:0; font-size:12px; }")
        title_row.addWidget(clear_att_btn)
        editor_layout.addLayout(title_row)
        self.current_attachment = None

        self._note_fmt_btns = []
        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(6)

        def _fbtn(label, tip, cb, css=""):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton { background:#1e293b; color:#cbd5e1; border:1px "
                "solid #334155; border-radius:6px; padding:4px 10px; "
                "min-height:0; font-size:12px; %s } "
                "QPushButton:hover { background:#1d4ed8; color:#eff6ff; }"
                % css)
            b.clicked.connect(cb)
            fmt_row.addWidget(b)
            self._note_fmt_btns.append(b)
            return b

        _fbtn("B", t("notes.fmt_bold_tip"),
              self._note_fmt_bold, "font-weight:700;")
        _fbtn("I", t("notes.fmt_italic_tip"),
              self._note_fmt_italic, "font-style:italic;")
        self._note_code_btn = _fbtn("<>", t("notes.fmt_code_tip"),
                                    self._note_fmt_code)
        self._note_code_btn.setCheckable(True)
        self.note_para_combo = QComboBox()
        for _k in ("notes.para_normal", "notes.para_title",
                   "notes.para_sub", "notes.para_section"):
            self.note_para_combo.addItem(t(_k))
        self.note_para_combo.setToolTip(t("notes.fmt_para_tip"))
        self.note_para_combo.activated.connect(self._note_fmt_heading)
        fmt_row.addWidget(self.note_para_combo)
        _fbtn("•", t("notes.fmt_list_tip"), self._note_fmt_list)
        fmt_row.addStretch()
        self.note_raw_btn = QPushButton("Raw")
        self.note_raw_btn.setCheckable(True)
        self.note_raw_btn.setToolTip(t("notes.raw_tip"))
        self.note_raw_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.note_raw_btn.setStyleSheet(
            "QPushButton { background:#1e293b; color:#cbd5e1; border:1px "
            "solid #334155; border-radius:6px; padding:4px 10px; "
            "min-height:0; font-size:12px; font-family:monospace; } "
            "QPushButton:checked { background:#1d4ed8; color:#eff6ff; }")
        self.note_raw_btn.toggled.connect(self._note_raw_toggle)
        fmt_row.addWidget(self.note_raw_btn)
        editor_layout.addLayout(fmt_row)

        self.note_content_text = PlainTextEditor()
        self.note_content_text.setAcceptRichText(True)
        self.note_content_text.setPlaceholderText(t("notes.content_placeholder"))
        self.note_content_text.setStyleSheet(
            "QTextEdit { background:#0f172a; color:#e2e8f0; font-size:13px; "
            "border:1px solid #1f2937; border-radius:8px; }")
        self.note_content_text.document().setDocumentMargin(12)
        self._note_loaded_md = None
        from PyQt6.QtGui import QKeySequence, QShortcut
        for _seq, _fn in (("Ctrl+B", self._note_fmt_bold),
                          ("Ctrl+I", self._note_fmt_italic)):
            _sc = QShortcut(QKeySequence(_seq), self.note_content_text)
            _sc.setContext(Qt.ShortcutContext.WidgetShortcut)
            _sc.activated.connect(_fn)
        self.note_raw_text = PlainTextEditor()
        self.note_raw_text.setStyleSheet(
            "QTextEdit { font-family: 'Consolas', 'JetBrains Mono', "
            "monospace; font-size: 13px; }")
        self.note_editor_stack = _ShrinkStack()
        self.note_editor_stack.addWidget(self.note_content_text)
        self.note_editor_stack.addWidget(self.note_raw_text)
        editor_layout.addWidget(self.note_editor_stack, 1)
        self.note_mode_note = QLabel("")
        self.note_mode_note.setWordWrap(True)
        self.note_mode_note.setStyleSheet(
            "color:#fbbf24; font-size:12px; padding:2px 4px;")
        self.note_mode_note.setVisible(False)
        editor_layout.addWidget(self.note_mode_note)
        self.note_content_text.paste_hook = self._note_paste_hook
        self.note_content_text.key_hook = self._note_code_key
        self._note_code_mode = False

        btn_row = QHBoxLayout()
        new_note_btn = QPushButton(t("notes.new_note"))
        new_note_btn.setToolTip(t("notes.new_note"))
        new_note_btn.clicked.connect(self._clear_note_editor)
        btn_row.addWidget(new_note_btn)
        add_note_btn = QPushButton(t("notes.add_note"))
        add_note_btn.clicked.connect(self.add_note_from_ui)
        btn_row.addWidget(add_note_btn)
        save_note_btn = QPushButton(t("notes.save_changes"))
        save_note_btn.clicked.connect(self._save_current_note)
        btn_row.addWidget(save_note_btn)
        btn_row.addStretch()
        editor_layout.addLayout(btn_row)

        splitter.addWidget(editor_widget)
        splitter.setSizes([180, 220, 600])

        self.selected_category = None
        self._current_note_id = None
        self.refresh_notes_lists()

    def _note_fmt_char(self, want, on_fmt, off_fmt):
        from PyQt6.QtGui import QTextCharFormat
        ed = self.note_content_text
        c = ed.textCursor()
        fmt = QTextCharFormat()
        (off_fmt if want(c.charFormat()) else on_fmt)(fmt)
        if c.hasSelection():
            c.mergeCharFormat(fmt)
        else:
            ed.mergeCurrentCharFormat(fmt)
        ed.setFocus()

    def _note_fmt_bold(self):
        from PyQt6.QtGui import QFont
        self._note_fmt_char(
            lambda f: f.fontWeight() >= QFont.Weight.Bold,
            lambda f: f.setFontWeight(QFont.Weight.Bold),
            lambda f: f.setFontWeight(QFont.Weight.Normal))

    def _note_fmt_italic(self):
        self._note_fmt_char(
            lambda f: f.fontItalic(),
            lambda f: f.setFontItalic(True),
            lambda f: f.setFontItalic(False))

    def _note_fmt_code(self):
        if not self.note_content_text.textCursor().hasSelection():
            return self._note_toggle_code_block()
        def _on(f):
            f.setFontFixedPitch(True)
            f.setFontFamilies(["monospace"])
            f.setBackground(QColor("#1e293b"))
        def _off(f):
            f.setFontFixedPitch(False)
            f.setFontFamilies([self.note_content_text.font().family()])
            f.clearBackground()
        self._note_fmt_char(lambda f: f.fontFixedPitch(), _on, _off)

    _NOTE_HEADINGS = (0, 1, 2, 3)

    def _note_fmt_heading(self, idx):
        from PyQt6.QtGui import (QFont, QTextBlockFormat, QTextCharFormat,
                                 QTextCursor)
        if not (0 <= idx < len(self._NOTE_HEADINGS)):
            return
        level = self._NOTE_HEADINGS[idx]
        ed = self.note_content_text
        c = ed.textCursor()
        d = ed.document()
        n0 = d.findBlock(c.selectionStart()).blockNumber()
        n1 = d.findBlock(c.selectionEnd()).blockNumber()
        c.beginEditBlock()
        for n in range(n0, n1 + 1):
            b = d.findBlockByNumber(n)
            line = QTextCursor(b)
            bf = QTextBlockFormat()
            bf.setHeadingLevel(level)
            line.mergeBlockFormat(bf)
            line.movePosition(line.MoveOperation.StartOfBlock)
            line.movePosition(line.MoveOperation.EndOfBlock,
                              line.MoveMode.KeepAnchor)
            cf = QTextCharFormat()
            cf.setFontWeight(QFont.Weight.Bold if level
                             else QFont.Weight.Normal)
            cf.setProperty(cf.Property.FontSizeAdjustment,
                           (4 - level) if level else 0)
            line.mergeCharFormat(cf)
        c.endEditBlock()
        ed.setFocus()

    def _note_fmt_list(self):
        from PyQt6.QtGui import QTextBlockFormat, QTextCursor, QTextListFormat
        ed = self.note_content_text
        c = ed.textCursor()
        d = ed.document()
        n0 = d.findBlock(c.selectionStart()).blockNumber()
        n1 = d.findBlock(c.selectionEnd()).blockNumber()
        blocks = [d.findBlockByNumber(n) for n in range(n0, n1 + 1)]
        c.beginEditBlock()
        if all(b.textList() for b in blocks):
            for b in blocks:
                b.textList().remove(b)
                line = QTextCursor(b)
                bf = QTextBlockFormat()
                bf.setIndent(0)
                line.mergeBlockFormat(bf)
        else:
            c.createList(QTextListFormat.Style.ListDisc)
        c.endEditBlock()
        ed.setFocus()

    def _note_raw_toggle(self, on):
        if on:
            self._note_enter_raw(self._note_editor_markdown())
        else:
            md = self.note_raw_text.toPlainText()
            ok, why = _md_view_faithful(md)
            if not ok:
                self._note_enter_raw(md, msg=t("notes.raw_forced", why=why))
                return
            self._note_enter_view(md)

    def _note_show_mode_note(self, msg=""):
        lbl = getattr(self, "note_mode_note", None)
        if lbl is None:
            return
        lbl.setText(msg or "")
        lbl.setVisible(bool(msg))

    def _note_enter_raw(self, md, msg="", focus=True):
        for b in getattr(self, "_note_fmt_btns", []):
            b.setEnabled(False)
        self.note_para_combo.setEnabled(False)
        self.note_raw_text.setPlainText(md)
        self.note_editor_stack.setCurrentIndex(1)
        btn = getattr(self, "note_raw_btn", None)
        if btn is not None and not btn.isChecked():
            btn.blockSignals(True)
            btn.setChecked(True)
            btn.blockSignals(False)
        self._note_show_mode_note(msg)
        if focus:
            self.note_raw_text.setFocus()

    def _note_enter_view(self, md, focus=True):
        from PyQt6.QtGui import QTextCharFormat
        for b in getattr(self, "_note_fmt_btns", []):
            b.setEnabled(True)
        self.note_para_combo.setEnabled(True)
        self.note_content_text.setMarkdown(_md_hard_breaks(md))
        self._note_restyle()
        self.note_content_text.setCurrentCharFormat(QTextCharFormat())
        self.note_content_text.document().setModified(False)
        self._note_loaded_md = md
        self.note_editor_stack.setCurrentIndex(0)
        btn = getattr(self, "note_raw_btn", None)
        if btn is not None and btn.isChecked():
            btn.blockSignals(True)
            btn.setChecked(False)
            btn.blockSignals(False)
        self._note_show_mode_note("")
        if focus:
            self.note_content_text.setFocus()

    _NOTE_CODE_BG = "#0d1526"
    _NOTE_CODE_FG = "#7dd3fc"

    def _note_code_formats(self):
        from PyQt6.QtGui import QTextBlockFormat, QTextCharFormat
        _P = QTextBlockFormat.Property
        bf = QTextBlockFormat()
        bf.setProperty(_P.BlockCodeFence, "```")
        bf.setProperty(_P.BlockCodeLanguage, "")
        bf.setBackground(QColor(self._NOTE_CODE_BG))
        bf.setLeftMargin(8)
        bf.setTopMargin(2)
        bf.setBottomMargin(2)
        cf = QTextCharFormat()
        cf.setFontFixedPitch(True)
        cf.setFontFamilies(["monospace"])
        cf.setForeground(QColor(self._NOTE_CODE_FG))
        return bf, cf

    def _note_in_code_block(self):
        from PyQt6.QtGui import QTextBlockFormat
        try:
            bf = self.note_content_text.textCursor().blockFormat()
            return bf.hasProperty(QTextBlockFormat.Property.BlockCodeFence)
        except Exception:
            return False

    def _note_toggle_code_block(self):
        if getattr(self, "_note_code_mode", False) or self._note_in_code_block():
            return self._note_close_code_block()
        return self._note_open_code_block()

    def _note_open_code_block(self):
        from PyQt6.QtGui import QTextCursor
        ed = self.note_content_text
        bf, cf = self._note_code_formats()
        c = ed.textCursor()
        c.beginEditBlock()
        if c.block().text().strip():
            c.movePosition(QTextCursor.MoveOperation.EndOfBlock)
            c.insertBlock()
        c.setBlockFormat(bf)
        c.setBlockCharFormat(cf)
        c.setCharFormat(cf)
        c.endEditBlock()
        ed.setTextCursor(c)
        ed.setCurrentCharFormat(cf)
        self._note_code_mode = True
        if getattr(self, "_note_code_btn", None) is not None:
            self._note_code_btn.setChecked(True)
        self._note_show_mode_note(t("notes.code_block_open"))
        ed.setFocus()

    def _note_close_code_block(self):
        from PyQt6.QtGui import (QTextBlockFormat, QTextCharFormat,
                                 QTextCursor)
        ed = self.note_content_text
        c = ed.textCursor()
        c.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        c.insertBlock(QTextBlockFormat(), QTextCharFormat())
        ed.setTextCursor(c)
        ed.setCurrentCharFormat(QTextCharFormat())
        self._note_code_mode = False
        if getattr(self, "_note_code_btn", None) is not None:
            self._note_code_btn.setChecked(False)
        self._note_show_mode_note("")
        ed.setFocus()

    def _note_code_key(self, e):
        from PyQt6.QtCore import Qt as _Qt
        if not getattr(self, "_note_code_mode", False):
            return False
        if e.key() not in (_Qt.Key.Key_Return, _Qt.Key.Key_Enter):
            return False
        if e.modifiers() & _Qt.KeyboardModifier.ShiftModifier:
            return False
        bf, cf = self._note_code_formats()
        c = self.note_content_text.textCursor()
        c.insertBlock(bf, cf)
        self.note_content_text.setTextCursor(c)
        self.note_content_text.setCurrentCharFormat(cf)
        return True

    def _note_paste_hook(self, source):
        if source is None or not source.hasText():
            return False
        text = source.text() or ""
        if not text.strip():
            return False
        if getattr(self, "_note_code_mode", False) or self._note_in_code_block():
            self._note_fill_code_block(text)
            return True
        ok, _why = _md_view_faithful(text)
        if ok and not _md_plain_paste_faithful(text):
            ok = False
        if ok:
            if source.hasHtml():
                self.note_content_text.textCursor().insertText(text)
                return True
            return False
        self._note_insert_code_lines(text)
        return True

    def _note_fill_code_block(self, text):
        ed = self.note_content_text
        bf, cf = self._note_code_formats()
        lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            return
        c = ed.textCursor()
        c.beginEditBlock()
        if c.hasSelection():
            c.removeSelectedText()
        c.setBlockFormat(bf)
        c.setBlockCharFormat(cf)
        for i, line in enumerate(lines):
            if i:
                c.insertBlock(bf, cf)
            c.insertText(line, cf)
        c.endEditBlock()
        ed.setTextCursor(c)
        ed.setCurrentCharFormat(cf)

    def _note_insert_code_lines(self, text):
        from PyQt6.QtGui import (QTextBlockFormat, QTextCharFormat,
                                 QTextCursor)
        ed = self.note_content_text
        lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            return
        _P = QTextBlockFormat.Property
        bf = QTextBlockFormat()
        bf.setProperty(_P.BlockCodeFence, "```")
        bf.setProperty(_P.BlockCodeLanguage, "")
        bf.setBackground(QColor("#0d1526"))
        bf.setLeftMargin(8)
        bf.setTopMargin(2)
        bf.setBottomMargin(2)
        cf = QTextCharFormat()
        cf.setFontFixedPitch(True)
        cf.setFontFamilies(["monospace"])
        cf.setForeground(QColor("#7dd3fc"))
        c = ed.textCursor()
        c.beginEditBlock()
        if c.hasSelection():
            c.removeSelectedText()
        if c.block().text().strip():
            c.movePosition(QTextCursor.MoveOperation.EndOfBlock)
            c.insertBlock()
        c.setBlockFormat(bf)
        c.setBlockCharFormat(cf)
        c.setCharFormat(cf)
        for i, line in enumerate(lines):
            if i:
                c.insertBlock(bf, cf)
            c.insertText(line, cf)
        c.insertBlock(QTextBlockFormat(), QTextCharFormat())
        c.endEditBlock()
        ed.setTextCursor(c)
        ed.setCurrentCharFormat(QTextCharFormat())

    def _note_restyle(self):
        from PyQt6.QtGui import (QColor, QTextBlockFormat, QTextCharFormat,
                                 QTextCursor)
        ed = self.note_content_text
        doc = ed.document()
        doc.setUndoRedoEnabled(False)
        blk = doc.begin()
        while blk.isValid():
            bf = QTextBlockFormat()
            lvl = blk.blockFormat().headingLevel()
            frags = []
            _it = blk.begin()
            while not _it.atEnd():
                if _it.fragment().isValid():
                    frags.append(_it.fragment())
                _it += 1
            in_code = (blk.blockFormat().hasProperty(
                QTextBlockFormat.Property.BlockCodeLanguage)
                or (blk.text().strip() and bool(frags) and all(
                    f.charFormat().fontFixedPitch() for f in frags)))
            if lvl:
                bf.setTopMargin(14)
                bf.setBottomMargin(6)
            elif in_code:
                bf.setBackground(QColor("#0d1526"))
                bf.setTopMargin(2)
                bf.setBottomMargin(2)
                bf.setLeftMargin(8)
            else:
                bf.setTopMargin(4)
                bf.setBottomMargin(4)
            line = QTextCursor(blk)
            line.mergeBlockFormat(bf)
            for frag in frags:
                cf = QTextCharFormat()
                if lvl:
                    cf.setForeground(QColor("#e6edf7"))
                elif frag.charFormat().fontFixedPitch():
                    cf.setBackground(QColor("#1e293b"))
                    cf.setForeground(QColor("#7dd3fc"))
                else:
                    continue
                span = QTextCursor(doc)
                span.setPosition(frag.position())
                span.setPosition(frag.position() + frag.length(),
                                 QTextCursor.MoveMode.KeepAnchor)
                span.mergeCharFormat(cf)
            blk = blk.next()
        doc.setUndoRedoEnabled(True)

    def _note_editor_markdown(self):
        if getattr(self, "note_editor_stack", None) is not None \
                and self.note_editor_stack.currentIndex() == 1:
            return self.note_raw_text.toPlainText()
        ed = self.note_content_text
        if not ed.document().isModified() \
                and getattr(self, "_note_loaded_md", None) is not None:
            return self._note_loaded_md
        from PyQt6.QtGui import QTextDocument
        return _md_restore_adjacency(
            _md_unwrap_qt(ed.document().toMarkdown(
                QTextDocument.MarkdownFeature.MarkdownDialectGitHub).strip("\n")),
            getattr(self, "_note_loaded_md", "") or "")

    def _on_note_selected(self, row):
        if row < 0:
            return
        item = self.notes_list.item(row)
        if not item:
            return
        note = item.data(Qt.ItemDataRole.UserRole)
        if not note or not isinstance(note, dict):
            return
        self.note_title_entry.setText(note.get("title", ""))
        _md = note.get("content", "") or ""
        self._note_enter_view(_md, focus=False)
        _ok, _why = _md_view_faithful(_md)
        if not _ok:
            self._note_enter_raw(_md, msg=t("notes.raw_forced", why=_why),
                                 focus=False)
        self._current_note_id = note.get("id")
        self._current_note_uuid = note.get("uuid")
        self._current_note_base_rev = note.get("rev", 1)
        att = note.get("attachment")
        if att and isinstance(att, dict) and att.get("filename"):
            self.current_attachment = att
            size_kb = att.get("size", 0) / 1024
            self.attached_file_label.setText(
                f"📎 {att['filename']} ({size_kb:.1f} KB)")
        else:
            self.current_attachment = None
            self.attached_file_label.clear()

    def _clear_note_editor(self):
        self._current_note_id = None
        self._current_note_uuid = None
        self._current_note_base_rev = 1
        self._note_loaded_md = None
        self.note_raw_text.clear()
        self.note_title_entry.clear()
        self.note_content_text.clear()
        if getattr(self, "note_raw_btn", None) is not None \
                and self.note_raw_btn.isChecked():
            self.note_raw_btn.setChecked(False)
        from PyQt6.QtGui import QTextCharFormat as _QTCF
        self.note_content_text.setCurrentCharFormat(_QTCF())
        self.current_attachment = None
        self.attached_file_label.clear()
        if hasattr(self, "notes_list"):
            self.notes_list.blockSignals(True)
            self.notes_list.clearSelection()
            self.notes_list.setCurrentRow(-1)
            self.notes_list.blockSignals(False)
        self.note_title_entry.setFocus()

    def _save_current_note(self):
        if not self._current_note_id or not self.selected_category:
            return
        title = self.note_title_entry.text().strip()
        content = self._note_editor_markdown().strip()
        if not title:
            return
        shared = self._mcp_call("fed_is_shared_folder",
                                {"folder_name": self.selected_category})
        is_shared = False
        try:
            import json as _json
            is_shared = _json.loads(shared).get("shared") if isinstance(shared, str) and not shared.startswith("❌") else False
        except Exception:
            is_shared = False

        _note_id = self._current_note_id
        _held = [(p, p.toPlainText()) for p in (self.note_content_text,
                                                 self.note_raw_text)]

        def _after_save(res, guarded=False):
            self._busy(False)
            why = self._note_save_failure(res)
            if why:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, t("notes.save_failed_title"),
                                    t("notes.save_failed", title=title, why=why))
                return
            data = res[1] or {}
            if guarded and data.get("conflict"):
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, t("fed.conflict_title"),
                                    data.get("warning", t("fed.conflict_msg")))
            elif guarded and self._current_note_id == _note_id:
                self._current_note_base_rev = int(self._current_note_base_rev or 1) + 1
            if self._current_note_id == _note_id and not (guarded and data.get("conflict")):
                for _p, _txt in _held:
                    if _p.toPlainText() == _txt:
                        _p.document().setModified(False)
            self.refresh_notes_lists()
            self.refresh_notes_display()

        def _save(tool, args, guarded=False):
            _to = self.config.getint("CHAT", "timeout", fallback=120)
            self._busy(True, t("msg.saving"))
            self._bg(lambda: self._mcp_try(tool, args, _to),
                     lambda res: _after_save(res, guarded))

        if is_shared:
            _save("fed_save_note_guarded", {
                "category": self.selected_category,
                "note_uuid": getattr(self, "_current_note_uuid", None),
                "title": title,
                "content": content,
                "base_rev": getattr(self, "_current_note_base_rev", 1),
            }, guarded=True)
        else:
            _save("update_note", {
                "category": self.selected_category,
                "note_id": self._current_note_id,
                "title": title,
                "content": content,
            })
        self.current_attachment = None
        self.attached_file_label.clear()

    @staticmethod
    def _note_save_failure(res):
        if not res:
            return t("notes.save_no_answer")
        ok, data, err = res
        data = data or {}
        if not ok or data.get("updated") is False or data.get("ok") is False:
            return str(data.get("message") or err or data.get("error") or t("notes.save_no_answer"))
        return None

    def _delete_selected_note(self):
        if not self.selected_category:
            return
        selected = self.notes_list.selectedItems()
        if not selected:
            return
        if len(selected) > 1:
            from PyQt6.QtWidgets import QMessageBox
            r = QMessageBox.question(self, t("dlg.confirm"),
                t("dlg.delete_n_notes", n=len(selected)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        _cat = self.selected_category
        _to_drop = [item.data(Qt.ItemDataRole.UserRole) for item in selected]
        _to_drop = [n for n in _to_drop
                   if n and isinstance(n, dict) and n.get("id")]

        def _work():
            for note in _to_drop:
                if note.get("uuid"):
                    self._mcp_call("fed_delete_note_guarded",
                                   {"category": _cat, "note_uuid": note["uuid"]})
                else:
                    self._mcp_call("delete_note",
                                   {"category": _cat, "note_id": note["id"]})
            return len(_to_drop)

        def _done(_n):
            self._busy(False)
            self._current_note_id = None
            self.note_title_entry.clear()
            self.note_content_text.clear()
            self.refresh_notes_lists()

        self._busy(True, t("msg.deleting"))
        self._bg(_work, _done)

    def _clear_category(self):
        if not self.selected_category:
            return
        count = self.notes_list.count()
        if count == 0:
            return
        from PyQt6.QtWidgets import QMessageBox
        r = QMessageBox.question(self, t("dlg.empty_category"),
            t("dlg.delete_cat_notes", n=count, cat=self.selected_category),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if r != QMessageBox.StandardButton.Yes:
            return
        cat = self.selected_category
        uuids = []
        for i in range(count):
            item = self.notes_list.item(i)
            note = item.data(Qt.ItemDataRole.UserRole) if item else None
            if note and isinstance(note, dict) and note.get("uuid"):
                uuids.append(note["uuid"])

        def work():
            res = self._mcp_call_json("fed_clear_category_guarded",
                                      {"category": cat}, timeout=120)
            if isinstance(res, dict) and res.get("mode") == "needs_per_note":
                for u in uuids:
                    self._mcp_call("fed_delete_note_guarded",
                                   {"category": cat, "note_uuid": u})
            return True

        def done(_):
            self._current_note_id = None
            self.note_title_entry.clear()
            self.note_content_text.clear()
            self.refresh_notes_lists()

        self._bg(work, done)


    def _apply_dark_theme(self):
        self.setStyleSheet("""
        QMainWindow, QWidget { background: #0f172a; color: #f1f5f9; }
        #header_box { background: #1e293b; border-bottom: 1px solid #334155; }
        QPushButton {
            background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #334155, stop:1 #1e293b);
            color: #f1f5f9; border: 1px solid #475569; border-radius: 8px;
            padding: 8px 16px; font-weight: 600; min-height: 28px;
        }
        QPushButton:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #475569, stop:1 #334155); }
        #api_button { background: #1e293b; border: 1px solid #0ea5e9; color: #38bdf8; font-weight: 700; }
        #api_button:hover { background: #223349; border-color: #38bdf8; }
        #send_button { background: #10b981; border: 1px solid #10b981; color: white; font-weight: 700; min-width: 90px; }
        #send_button:hover { background: #14c98e; }
        #stop_button { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #ef4444, stop:1 #b91c1c); border-color: #ef4444; color: white; font-weight: 700; min-width: 90px; }
        #enchant_bar { background: rgba(15,23,42,0.85); border-top: 1px solid #1e293b; }
        #enchant_text { color: #7dd3fc; font-size: 12px; font-weight: 700; letter-spacing: 2px; }
        #chat_display { background: #0f172a; color: #f1f5f9; border: none; border-radius: 12px; font-family: 'Consolas', 'JetBrains Mono', monospace; font-size: 13px; padding: 16px; }
        QToolTip { background: #1e293b; color: #e6edf7; border: 1px solid #475569; border-radius: 4px; padding: 4px 8px; font-size: 12px; }
        QLineEdit { background: #1e293b; color: #f1f5f9; border: 2px solid #334155; border-radius: 10px; padding: 10px 14px; font-size: 14px; }
        QLineEdit:focus { border-color: #10b981; }
        QTextEdit { background: #1e293b; color: #f1f5f9; border: 1px solid #334155; border-radius: 6px; padding: 8px; }
        QTabWidget::pane { border: none; background: #0f172a; }
        QTabBar::tab { background: transparent; color: #94a3b8; padding: 12px 14px; font-weight: 600; border-bottom: 2px solid transparent; }
        QTabBar::tab:selected { color: #10b981; border-bottom: 2px solid #10b981; background: #1e293b; }
        QTabBar::tab:hover { color: #f1f5f9; background: rgba(51,65,85,0.5); }
        QComboBox { background: #334155; color: #f1f5f9; border: 1px solid #475569; border-radius: 8px; padding: 8px 16px; }
        QComboBox QAbstractItemView { background: #1e293b; color: #f1f5f9; selection-background-color: #334155; }
        QSlider::groove:horizontal { background: #1e293b; height: 8px; border-radius: 4px; border: 1px solid #334155; }
        QSlider::handle:horizontal { background: #10b981; width: 20px; height: 20px; margin: -6px 0; border-radius: 10px; }
        QListWidget { background: #0f172a; color: #f1f5f9; border: 1px solid #334155; border-radius: 6px; }
        QListWidget::item { padding: 8px; border-bottom: 1px solid #1e293b; }
        QListWidget::item:selected { background: #334155; }
        QLabel { color: #f1f5f9; }
        #status_label { color: #e6edf7; font-size: 12px; }
        QScrollBar:vertical { background: transparent; width: 10px; }
        QScrollBar::handle:vertical { background: #475569; border-radius: 5px; min-height: 30px; }
        QScrollBar::handle:vertical:hover { background: #10b981; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        QScrollBar:horizontal { background: transparent; height: 10px; }
        QScrollBar::handle:horizontal { background: #475569; border-radius: 5px; min-width: 30px; }
        QScrollBar::handle:horizontal:hover { background: #10b981; }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
        QSplitter::handle { background: #334155; width: 2px; }
        QCalendarWidget { background: #1e293b; color: #f1f5f9; }
        QDateTimeEdit { background: #1e293b; color: #f1f5f9; border: 1px solid #334155; border-radius: 8px; padding: 6px 10px; }
        QDateTimeEdit::drop-down { border: none; width: 22px; }
        QCalendarWidget QWidget#qt_calendar_navigationbar { background: #0f172a; }
        QCalendarWidget QToolButton { color: #f1f5f9; background: transparent; border: none; padding: 4px 8px; }
        QCalendarWidget QToolButton:hover { background: #334155; border-radius: 6px; }
        QCalendarWidget QMenu { background: #1e293b; color: #f1f5f9; }
        QCalendarWidget QSpinBox { background: #1e293b; color: #f1f5f9; }
        QCalendarWidget QAbstractItemView { background: #0f172a; color: #f1f5f9; selection-background-color: #3b82f6; selection-color: #ffffff; alternate-background-color: #0f172a; }
        QCalendarWidget QAbstractItemView:disabled { color: #64748b; }
        QSpinBox { background: #1e293b; color: #f1f5f9; border: 1px solid #334155; border-radius: 4px; padding: 4px; }
        QMessageBox { background: #1e293b; color: #f1f5f9; }
        QDialog { background: #1e293b; color: #f1f5f9; }
        QGroupBox { border: 1px solid #334155; border-radius: 6px; margin-top: 8px; padding-top: 12px; }
        QGroupBox::title { color: #10b981; subcontrol-origin: margin; left: 10px; padding: 0 4px; }
        QCheckBox { color: #f1f5f9; }
        QCheckBox::indicator { width: 18px; height: 18px; }
        QCheckBox::indicator:unchecked { border: 2px solid #475569; border-radius: 4px; background: #1e293b; }
        QCheckBox::indicator:checked { border: 2px solid #10b981; border-radius: 4px; background: #10b981; }
        #input_area { background: #0f172a; border-top: 1px solid #334155; }
        #title_bar { background: #0b1120; border-bottom: 1px solid #1e293b; }
        #title_label { color: #e6edf7; font-size: 12px; font-weight: 600; }
        #title_min, #title_max, #title_close {
            background: transparent; border: none; color: #94a3b8;
            font-size: 13px; border-radius: 6px; padding: 0;
        }
        #title_min:hover, #title_max:hover { background: #1e293b; color: #f1f5f9; }
        #title_close:hover { background: #dc2626; color: white; }
        """)


    def _edge_at(self, pos):
        from PyQt6.QtCore import Qt as _Qt
        if self.isMaximized() or self.isFullScreen():
            return None
        m = self._resize_margin
        r = self.rect()
        x, y = pos.x(), pos.y()
        left = x <= m
        right = x >= r.width() - m
        top = y <= m
        bottom = y >= r.height() - m
        c = self._GLOW_MARGIN + self._CORNER_RADIUS
        if left or right:
            top = top or y <= c
            bottom = bottom or y >= r.height() - c
        if top or bottom:
            left = left or x <= c
            right = right or x >= r.width() - c
        edge = None
        if top and left:
            edge = _Qt.Edge.TopEdge | _Qt.Edge.LeftEdge
        elif top and right:
            edge = _Qt.Edge.TopEdge | _Qt.Edge.RightEdge
        elif bottom and left:
            edge = _Qt.Edge.BottomEdge | _Qt.Edge.LeftEdge
        elif bottom and right:
            edge = _Qt.Edge.BottomEdge | _Qt.Edge.RightEdge
        elif left:
            edge = _Qt.Edge.LeftEdge
        elif right:
            edge = _Qt.Edge.RightEdge
        elif top:
            edge = _Qt.Edge.TopEdge
        elif bottom:
            edge = _Qt.Edge.BottomEdge
        return edge

    def _cursor_for_edge(self, edge):
        from PyQt6.QtCore import Qt as _Qt
        if edge is None:
            return _Qt.CursorShape.ArrowCursor
        L, R = _Qt.Edge.LeftEdge, _Qt.Edge.RightEdge
        T, B = _Qt.Edge.TopEdge, _Qt.Edge.BottomEdge
        if edge in (T | L, B | R):
            return _Qt.CursorShape.SizeFDiagCursor
        if edge in (T | R, B | L):
            return _Qt.CursorShape.SizeBDiagCursor
        if edge in (L, R):
            return _Qt.CursorShape.SizeHorCursor
        return _Qt.CursorShape.SizeVerCursor

    _CORNER_RADIUS = 18
    _GLOW_MARGIN = 14
    _ACCENT = "#10b981"

    def _apply_rounded_mask(self):
        inner = getattr(self, "_inner_frame", None)
        cw = self.centralWidget()
        if inner is None or cw is None:
            return
        maximized = getattr(self, "_is_max", False) or self.isMaximized() or self.isFullScreen()
        if maximized:
            if self._outer_layout is not None:
                self._outer_layout.setContentsMargins(0, 0, 0, 0)
            if isinstance(cw, _GlowFrame):
                cw.set_glow_enabled(False)
            inner.clearMask()
            return
        if self._outer_layout is not None:
            m = self._GLOW_MARGIN
            self._outer_layout.setContentsMargins(m, m, m, m)
        if isinstance(cw, _GlowFrame):
            cw.set_glow_enabled(True)
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QPainterPath, QRegion
        r = self._CORNER_RADIUS
        path = QPainterPath()
        path.addRoundedRect(QRectF(inner.rect()), r, r)
        inner.setMask(QRegion(path.toFillPolygon().toPolygon()))
        cw.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._apply_rounded_mask)
        _rt = getattr(self, "_tabs_refit", None)
        if _rt is None and hasattr(self, "_chat_tab_layout"):
            _rt = self._tabs_refit = QTimer(self)
            _rt.setSingleShot(True)
            _rt.setInterval(120)
            _rt.timeout.connect(self._render_chat_tabs)
        if _rt is not None:
            _rt.start()

    def _keep_qa_review(self, ev):
        store = getattr(self, "_qa_reviews", None)
        if store is None:
            store = self._qa_reviews = {}
        import time as _time
        self._qa_review_seq = max(getattr(self, "_qa_review_seq", 0),
                                  int(_time.time() * 1000)) + 1
        rid = self._qa_review_seq
        store[rid] = {"ok": ev.get("ok"), "round": ev.get("round", 1),
                      "mode": ev.get("mode", ""), "why": str(ev.get("why") or ""),
                      "next": str(ev.get("next") or ""),
                      "reviewed": str(ev.get("reviewed") or ""),
                      "said": str(ev.get("said") or "")}
        for old in sorted(store)[:-200]:
            store.pop(old, None)
        return rid

    def _find_anchor(self, href):
        found = None
        blk = self.chat_display.document().begin()
        while blk.isValid():
            it = blk.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid() and frag.charFormat().anchorHref() == href:
                    found = (frag.position(), frag.length())
                it += 1
            blk = blk.next()
        return found

    @staticmethod
    def _qa_link_html(rid, hide=False):
        key, verb = ("qa.hide", "qa-hide") if hide else ("qa.view", "qa")
        return (f'<a href="hoplita:{verb}:{int(rid)}" style="color:#38bdf8;'
                f'text-decoration:none;">{t(key)}</a>')

    def _expand_qa_review(self, rid, event=None):
        import html as _h
        from PyQt6.QtWidgets import QToolTip
        rec = (getattr(self, "_qa_reviews", None) or {}).get(rid)
        if rec is None:
            if event is not None:
                QToolTip.showText(event.globalPosition().toPoint(),
                                  t("qa.view_gone"), self.chat_display)
            return
        hit = self._find_anchor(f"hoplita:qa:{rid}")
        if hit is None:
            return
        okv = rec.get("ok")
        accent = "#10b981" if okv is True else ("#fbbf24" if okv is None else "#f87171")
        ink, label_c, card_bg, inset_bg, line = "#e6edf7", "#8296b1", "#111a2e", "#0b1220", "#1f2a44"

        def _label(key):
            return (f'<p style="margin:10px 0 4px 0;color:{label_c};font-size:12px;'
                    f'font-weight:600;">{_h.escape(t(key).upper())}</p>')

        def _text(text):
            return (f'<p style="margin:0;color:{ink};white-space:pre-wrap;">'
                    f'{_h.escape(text)}</p>')
        why = (rec.get("why") or "").strip()
        nxt = (rec.get("next") or "").strip()
        said = (rec.get("said") or "").strip()
        try:
            import json as _json
            _is_obj = isinstance(_json.loads(said), dict)
        except Exception:
            _is_obj = False
        parts = [f'<p style="margin:0;color:{label_c};font-size:12px;font-weight:600;">'
                 f'{_h.escape(t("qa.card_title", n=rec.get("round", 1)).upper())}</p>']
        parts.append(_label("qa.card_verdict"))
        parts.append(_text(why or t("qa.card_no_remarks")))
        if nxt:
            parts.append(_label("qa.card_next"))
            parts.append(_text(nxt))
        if said and not _is_obj:
            parts.append(_label("qa.view_said"))
            parts.append(_text(said))
        parts.append(_label("qa.view_reviewed"))
        parts.append(f'<table width="100%" cellspacing="0" cellpadding="10" '
                     f'style="margin:0 0 4px 0;"><tr><td bgcolor="{inset_bg}" '
                     f'style="border:1px solid {line};"><pre style="white-space:pre-wrap;'
                     f'margin:0;color:{ink};font-family:monospace;">'
                     f'{_h.escape(rec.get("reviewed") or "—")}</pre></td></tr></table>')
        box = (f'<table width="100%" cellspacing="0" cellpadding="0" style="margin:6px 0 10px 0;">'
               f'<tr><td width="3" bgcolor="{accent}"></td>'
               f'<td bgcolor="{card_bg}" style="padding:10px 14px;border:1px solid {line};'
               f'border-left:none;">' + "".join(parts) + '</td></tr></table>')
        sb = self.chat_display.verticalScrollBar()
        keep = sb.value()
        cur = QTextCursor(self.chat_display.document())
        cur.setPosition(hit[0])
        cur.setPosition(hit[0] + hit[1], QTextCursor.MoveMode.KeepAnchor)
        cur.insertHtml(self._qa_link_html(rid, hide=True) + box)
        sb.setValue(keep)

    def _collapse_qa_review(self, rid):
        hit = self._find_anchor(f"hoplita:qa-hide:{rid}")
        if hit is None:
            return
        doc = self.chat_display.document()
        cur = QTextCursor(doc)
        cur.setPosition(hit[0] + hit[1])
        table = None
        for _ in range(4):
            if not cur.movePosition(QTextCursor.MoveOperation.NextBlock):
                break
            table = cur.currentTable()
            if table is not None:
                break
        if table is None:
            return
        from PyQt6.QtGui import QTextTable
        while isinstance(table.parentFrame(), QTextTable):
            table = table.parentFrame()
        sb = self.chat_display.verticalScrollBar()
        keep = sb.value()
        cur = QTextCursor(doc)
        cur.setPosition(hit[0])
        cur.setPosition(min(table.lastPosition() + 1, doc.characterCount() - 1),
                        QTextCursor.MoveMode.KeepAnchor)
        cur.insertHtml(self._qa_link_html(rid))
        sb.setValue(keep)

    TU_COLOR = "#a5b4fc"
    TU_ERROR_COLOR = "#f87171"
    _UNFOLDED_TURNS = ("_compact_run",)

    def _tu_store(self):
        store = getattr(self, "_tool_usage", None)
        if store is None:
            store = self._tool_usage = {}
        return store

    def _tu_begin(self, tab_id):
        store = self._tu_store()
        self._tu_seq = max(getattr(self, "_tu_seq", 0), int(time.time() * 1000)) + 1
        rid = self._tu_seq
        store[rid] = {"tab": tab_id, "t0": time.time(), "lines": [], "tools": 0,
                      "errors": 0, "done": False, "secs": 0.0, "painted": False}
        for old in sorted(store)[:-300]:
            store.pop(old, None)
        self._tls.tu_rid = rid
        return rid

    def _tu_end(self):
        rid = getattr(self._tls, "tu_rid", 0)
        self._tls.tu_rid = 0
        rec = self._tu_store().get(rid)
        if rec is None or rec.get("done"):
            return
        rec["done"] = True
        rec["secs"] = round(time.time() - rec.get("t0", time.time()), 1)
        if rec["lines"]:
            self.signals.message_tab.emit(str(rid), "tool_usage", rec.get("tab") or "")

    def _tu_tools(self, n):
        rec = self._tu_store().get(getattr(self._tls, "tu_rid", 0))
        if rec is not None:
            rec["tools"] = max(int(rec.get("tools", 0)), int(n))

    def _tu_summary(self, rec):
        secs = rec.get("secs") or (time.time() - rec.get("t0", time.time()))
        n = int(rec.get("tools", 0))
        txt = (t("tu.summary", n=n, s=f"{secs:.0f}") if n
               else t("tu.summary_none", s=f"{secs:.0f}"))
        if rec.get("errors"):
            txt += t("tu.errors", n=int(rec["errors"]))
        return txt

    def _tu_link_html(self, rid, hide=False):
        import html as _h
        rec = self._tu_store().get(rid) or {}
        verb, key = ("tu-hide", "tu.hide") if hide else ("tu", "tu.view")
        col = self.TU_ERROR_COLOR if rec.get("errors") else self.TU_COLOR
        return (f'<a href="hoplita:{verb}:{int(rid)}" style="color:{col};'
                f'text-decoration:none;">{_h.escape(self._tu_summary(rec))}'
                f'&nbsp;&nbsp;·&nbsp;&nbsp;{_h.escape(t(key))}</a>')

    def _tu_anchor_span(self, href, view=None):
        hit = self._tu_find_run({href: href}, view)
        return None if hit is None else (hit[1], hit[2])

    def _tu_find(self, rid, view=None):
        return self._tu_find_run({f"hoplita:tu:{int(rid)}": "tu",
                                  f"hoplita:tu-hide:{int(rid)}": "tu-hide"}, view)

    def _tu_find_run(self, hrefs, view=None):
        blk = (view or self.chat_display).document().lastBlock()
        while blk.isValid():
            it = blk.begin()
            s0 = e0 = val = None
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    v = hrefs.get(frag.charFormat().anchorHref())
                    if v is not None:
                        if s0 is None:
                            s0, val = frag.position(), v
                        e0 = frag.position() + frag.length()
                it += 1
            if s0 is not None:
                return (val, s0, e0)
            blk = blk.previous()
        return None

    def _paint_tool_usage(self, rid, tab_id):
        rec = self._tu_store().get(rid)
        if rec is None:
            return
        painted = rec.get("painted", True)
        if tab_id == self._active_tab_id:
            sb = self.chat_display.verticalScrollBar()
            keep, at_bottom = sb.value(), sb.value() >= sb.maximum() - 2
            hit = self._tu_find(rid) if painted else None
            if hit is not None:
                cur = QTextCursor(self.chat_display.document())
                cur.setPosition(hit[1])
                cur.setPosition(hit[2], QTextCursor.MoveMode.KeepAnchor)
                cur.insertHtml(self._tu_link_html(rid, hide=(hit[0] == "tu-hide")))
                sb.setValue(sb.maximum() if at_bottom else keep)
                if getattr(self, "_tu_shown", 0) == rid:
                    self.turn_view.follow(rec, self._tu_heading(rec))
                return
        else:
            view = self._chat_view(tab_id)
            hit = self._tu_find(rid, view) if painted else None
            if hit is not None:
                cur = QTextCursor(view.document())
                cur.setPosition(hit[1])
                cur.setPosition(hit[2], QTextCursor.MoveMode.KeepAnchor)
                cur.insertHtml(self._tu_link_html(rid, hide=(hit[0] == "tu-hide")))
                return
        rec["painted"] = True
        ts = datetime.now().strftime("%H:%M:%S")
        self._append_html_to_tab(tab_id, (
            f'<span style="color:#64748b">[{ts}]</span> '
            f'<span style="color:#22d3ee;font-weight:bold">{t("label.system")}:</span> '
            + self._tu_link_html(rid) + "<br>"))

    def _tu_heading(self, rec):
        when = datetime.fromtimestamp(rec.get("t0") or time.time()).strftime("%H:%M:%S")
        return f"{when}  \u00b7  {self._tu_summary(rec)}"

    def _tu_flip(self, rid, hide):
        rec = self._tu_store().get(rid)
        if rec is None:
            return
        tab = rec.get("tab") or ""
        if tab == self._active_tab_id:
            view = self.chat_display
        elif self._tab_by_id(tab) is not None:
            view = self._chat_view(tab)
        else:
            return
        hit = self._tu_find(rid, view)
        if hit is None:
            return
        sb = view.verticalScrollBar()
        keep = sb.value()
        cur = QTextCursor(view.document())
        cur.setPosition(hit[1])
        cur.setPosition(hit[2], QTextCursor.MoveMode.KeepAnchor)
        cur.insertHtml(self._tu_link_html(rid, hide=hide))
        sb.setValue(keep)

    @staticmethod
    def _tu_for_save(rec):
        out = {k: v for k, v in rec.items() if k != "open"}
        out["lines"] = [(ts, kind, "" if str(kind).startswith("thought") else text)
                        for ts, kind, text in (rec.get("lines") or [])]
        return out

    def _expand_tool_usage(self, rid, event=None):
        from PyQt6.QtWidgets import QToolTip
        rec = self._tu_store().get(rid)
        if rec is None:
            if event is not None:
                QToolTip.showText(event.globalPosition().toPoint(),
                                  t("tu.gone"), self.chat_display)
            return
        shown = getattr(self, "_tu_shown", 0)
        if shown and shown != rid:
            self._tu_flip(shown, hide=False)
        self._tu_shown = rid
        self._tu_flip(rid, hide=True)
        self.turn_view.show_turn(rid, rec, self._tu_heading(rec))
        if getattr(self, "_tu_full", False):
            self.tabs.setCurrentIndex(self.tabs.indexOf(self.turn_view))
        else:
            self._curtain_set(self.turn_view, True)

    def _collapse_tool_usage(self, rid):
        if getattr(self, "_tu_shown", 0) == rid:
            self._tu_shown = 0
            if getattr(self, "_tu_full", False):
                self._tu_set_full(False, reopen=False)
            else:
                self._curtain_set(self.turn_view, False)
        self._tu_flip(rid, hide=False)

    def _tu_close(self):
        shown = getattr(self, "_tu_shown", 0)
        if shown:
            self._collapse_tool_usage(shown)

    def _tu_full_toggle(self):
        self._tu_set_full(not getattr(self, "_tu_full", False))

    def _tu_set_full(self, on, reopen=True):
        if on == getattr(self, "_tu_full", False):
            return
        self._tu_full = on
        crec = self._curtains.get(self.turn_view)
        if on:
            if crec is not None:
                if crec["anim"] is not None:
                    crec["anim"].stop()
                    crec["anim"] = None
                crec["open"] = False
                crec["grip"].hide()
            idx = self.tabs.insertTab(1, self.turn_view, t("tu.tab"))
            self.tabs.setTabIcon(idx, self._svg_icon("tab_tools", "#94a3b8", 18))
            self.turn_view.setVisible(True)
            self.tabs.setCurrentIndex(idx)
        else:
            idx = self.tabs.indexOf(self.turn_view)
            if idx >= 0:
                self.tabs.removeTab(idx)
            self.turn_view.setParent(self._curtain_host)
            if crec is not None:
                crec["grip"].show()
            self.tabs.setCurrentIndex(0)
            if reopen:
                self._curtain_set(self.turn_view, True)
            else:
                self.turn_view.hide()
        self.turn_view.set_full(on)

    def _tu_restore(self, sess, tab_id):
        store = self._tu_store()
        for k, rec in (sess.get("tool_usage") or {}).items():
            try:
                rid = int(k)
            except (TypeError, ValueError):
                continue
            if not isinstance(rec, dict):
                continue
            rec = dict(rec)
            rec["tab"] = tab_id
            rec["done"] = True
            rec["lines"] = [tuple(x) for x in (rec.get("lines") or [])
                            if isinstance(x, (list, tuple)) and len(x) == 3]
            store[rid] = rec

    def _chat_anchor_event(self, event):
        from PyQt6.QtCore import QEvent, Qt as _Qt
        from PyQt6.QtWidgets import QToolTip
        et = event.type()
        if et not in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
            return False
        pos = event.position().toPoint()
        href = self.chat_display.anchorAt(pos)
        if et == QEvent.Type.MouseMove:
            self.chat_display.viewport().setCursor(
                _Qt.CursorShape.PointingHandCursor
                if href.startswith(("hoplita:copy:", "hoplita:rate:",
                                    "hoplita:canvas:", "hoplita:qa:",
                                    "hoplita:qa-hide:", "hoplita:tu:",
                                    "hoplita:tu-hide:"))
                else _Qt.CursorShape.IBeamCursor)
            return False
        if href.startswith("hoplita:tu-hide:"):
            try:
                self._collapse_tool_usage(int(href.rsplit(":", 1)[1]))
            except ValueError:
                return False
            return True
        if href.startswith("hoplita:tu:"):
            try:
                self._expand_tool_usage(int(href.rsplit(":", 1)[1]), event)
            except ValueError:
                return False
            return True
        if href.startswith("hoplita:qa-hide:"):
            try:
                self._collapse_qa_review(int(href.rsplit(":", 1)[1]))
            except ValueError:
                return False
            return True
        if href.startswith("hoplita:qa:"):
            try:
                self._expand_qa_review(int(href.rsplit(":", 1)[1]), event)
            except ValueError:
                return False
            return True
        if href.startswith("hoplita:canvas:"):
            self._canvas_show_image({"src": href[len("hoplita:canvas:"):]})
            return True
        if href.startswith("hoplita:rate:"):
            try:
                _, _, verdict, rid = href.split(":", 3)
                self._rate_reply(int(rid), "ok" if verdict == "ok" else "bad")
            except ValueError:
                return False
            return True
        if not href.startswith("hoplita:copy:"):
            return False
        try:
            code = (getattr(self, "_code_blocks", {}) or {})[int(href.rsplit(":", 1)[1])]
        except (KeyError, ValueError):
            return False
        QApplication.clipboard().setText(code)
        QToolTip.showText(event.globalPosition().toPoint(),
                          t("chat.code_copied"), self.chat_display)
        return True

    def _apply_zoom(self):
        n = getattr(self, "_chat_font_pt", 13)
        for w, base_css, delta in getattr(self, "_zoom_targets", []):
            try:
                if base_css:
                    css = base_css + f" QTextEdit {{ font-size: {n + delta}px; }}"
                else:
                    css = f"font-size: {n + delta}px;"
                w.setStyleSheet(css)
                w.ensurePolished()
            except RuntimeError:
                continue
        ed = getattr(self, "code_editor", None)
        if ed is not None:
            try:
                ed.setTabStopDistance(
                    4.0 * ed.fontMetrics().horizontalAdvance(" "))
            except RuntimeError:
                pass

    def _chat_zoom(self, delta):
        self._chat_font_pt = max(8, min(40,
                                        getattr(self, "_chat_font_pt", 13)
                                        + int(delta)))
        self._apply_zoom()
        try:
            from PyQt6.QtCore import QSettings
            QSettings("hoplita", "hoplita").setValue("chat_font_size",
                                                     self._chat_font_pt)
        except Exception:
            pass

    def _chat_zoom_reset(self):
        self._chat_zoom(getattr(self, "_chat_font_base", 13)
                        - getattr(self, "_chat_font_pt", 13))

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent, Qt as _Qt
        if obj is getattr(self, "_chat_views", None):
            if event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
                self._curtains_place()
            return False
        if (event.type() == QEvent.Type.Wheel
                and event.modifiers() & _Qt.KeyboardModifier.ControlModifier):
            for _w, _b, _d in getattr(self, "_zoom_targets", []):
                try:
                    if obj is _w.viewport():
                        self._chat_zoom(
                            +1 if event.angleDelta().y() > 0 else -1)
                        return True
                except RuntimeError:
                    continue
        cd = getattr(self, "chat_display", None)
        if cd is not None and obj is cd.viewport():
            return self._chat_anchor_event(event)
        dv = getattr(self, "code_diff", None)
        if dv is not None and obj is dv.viewport():
            return self._code_diff_event(event)
        if obj in (getattr(self, "code_find", None),
                   getattr(self, "code_replace", None)):
            r = self._code_find_key(obj, event)
            if r is not None:
                return r
        et = event.type()
        if et == QEvent.Type.MouseMove:
            gp = event.globalPosition().toPoint()
            local = self.mapFromGlobal(gp)
            if self.rect().contains(local):
                edge = self._edge_at(local)
                if edge is not None:
                    self.setCursor(self._cursor_for_edge(edge))
                    self._resize_edge = edge
                    return False
                elif self._resize_edge is not None:
                    self.unsetCursor()
                    self._resize_edge = None
        elif et == QEvent.Type.MouseButtonPress:
            if (getattr(event, "button", lambda: None)() == _Qt.MouseButton.LeftButton
                    and self._resize_edge is not None):
                wh = self.windowHandle()
                if wh is not None:
                    wh.startSystemResize(self._resize_edge)
                    return True
        return super().eventFilter(obj, event)

    def _svg_icon(self, name, color="#f1f5f9", size=20):
        from PyQt6.QtGui import QPixmap, QIcon, QPainter
        from PyQt6.QtCore import Qt as _Qt, QByteArray
        from PyQt6.QtSvg import QSvgRenderer
        base = os.path.dirname(os.path.abspath(__file__))
        for _cand in (os.path.join(base, "icons", f"{name}.svg"),
                      os.path.join(base, "..", "icons", f"{name}.svg")):
            if os.path.exists(_cand):
                path = _cand
                break
        else:
            return QIcon()
        try:
            with open(path, "r", encoding="utf-8") as f:
                svg = f.read()
            svg = svg.replace("currentColor", color)
            renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
            pix = QPixmap(size, size)
            pix.fill(_Qt.GlobalColor.transparent)
            p = QPainter(pix)
            renderer.render(p)
            p.end()
            return QIcon(pix)
        except Exception:
            return QIcon()

    def _toggle_headless(self):
        self.browser_headless = not self.browser_headless
        self._update_headless_button()
        mode = "headless (oculto)" if self.browser_headless else "visible"
        self.add_message(f"🌐 Browser: {mode}", "system")

    def _update_headless_button(self):
        if self.browser_headless:
            self.headless_button.setText(" HIDE")
            self.headless_button.setIcon(self._svg_icon("show", "#64748b", 18))
            self.headless_button.setStyleSheet("")
        else:
            self.headless_button.setText(" SHOW")
            self.headless_button.setIcon(self._svg_icon("show", "#fbbf24", 18))
            self.headless_button.setStyleSheet("background: #2a2416; color: #fbbf24; border: 1px solid #f59e0b; font-weight: bold; border-radius: 8px; padding: 8px 16px;")


    def _resolve_endpoint(self, section):
        import hoplita_llmcfg
        return hoplita_llmcfg.resolve_endpoint(self.config, section)

    def _base_model_name(self):
        return self._resolve_endpoint("API")[2] or "?"

    def _model_pool(self):
        pool = {}
        if not self.config.has_section("MODELS"):
            return pool
        for name, active in self.config.items("MODELS"):
            if active.strip().lower() not in ("1", "true", "yes", "on"):
                continue
            sec = _model_section(self.config, name)
            if not sec:
                continue
            base_url, api_key, model = self._resolve_endpoint(sec)
            profile = self.config.get(sec, "profile", fallback="").strip()
            council = (self.config.get(sec, "council", fallback="") or "").strip() \
                .lower() not in ("false", "0", "no", "off")
            pool[name] = {"base_url": base_url, "api_key": api_key,
                          "model": model, "profile": profile, "council": council,
                          "via": (self.config.get(sec, "via", fallback="") or "").strip()}
        return pool

    def _contact_online_cached(self, handle):
        try:
            for c in (getattr(self, "_fed_cache", None) or {}).get("contacts") or []:
                if c.get("handle") == handle:
                    return bool(c.get("online"))
        except Exception:
            pass
        return True

    def _lent_tag(self, m):
        via = (m or {}).get("via") or ""
        if not via:
            return ""
        tag = t("model.lent_tag", handle=via)
        if not self._contact_online_cached(via):
            tag += " · " + t("model.offline_tag")
        return f"   ({tag})"

    def _chat_base_endpoint(self):
        ov = self._chat_model_override
        if ov and ov not in ("base", "default"):
            pool = self._model_pool()
            if ov in pool:
                m = pool[ov]
                return m["base_url"], m["api_key"], m["model"]
        return self._resolve_endpoint("API")

    def _resolve_model_by_name(self, model_name):
        if model_name and model_name not in ("base", "default"):
            pool = self._model_pool()
            if model_name in pool:
                m = pool[model_name]
                return m["base_url"], m["api_key"], m["model"]
        return self._chat_base_endpoint()

    def _max_tokens(self):
        return self.config.getint("CHAT", "max_tokens", fallback=8000)


    def _render_markdown(self, text, base_color):
        if not _MARKDOWN_OK:
            return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        try:
            import re as _re
            def _fix_lists(txt):
                lines = txt.split("\n")
                out = []
                bullet = _re.compile(r'^\s*[-*+]\s+|^\s*\d+\.\s+')
                delim = _re.compile(r'^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$')
                fence = False
                for i, ln in enumerate(lines):
                    if ln.lstrip().startswith("```"):
                        fence = not fence
                    is_item = bool(bullet.match(ln))
                    prev = lines[i-1] if i > 0 else ""
                    prev_item = bool(bullet.match(prev))
                    if is_item and not prev_item and prev.strip() != "":
                        out.append("")
                    elif (not fence and ln.lstrip().startswith("|")
                          and prev.strip() and not prev.lstrip().startswith("|")
                          and i + 1 < len(lines) and delim.match(lines[i + 1])):
                        out.append("")
                    out.append(ln)
                return "\n".join(out)

            md = _markdown_lib.Markdown(
                extensions=["fenced_code", "codehilite", "tables", "sane_lists"],
                extension_configs={"codehilite": {"noclasses": True,
                                                  "pygments_style": _CODE_STYLE}})
            html = _pictures_to_links(md.convert(_fix_lists(text)))
            html = _style_md_tables(html)

            langs, in_fence = [], False
            for ln in text.split("\n"):
                if ln.lstrip().startswith("```"):
                    if not in_fence:
                        langs.append(ln.lstrip()[3:].strip().split()[:1])
                    in_fence = not in_fence
            langs = [(x[0] if x else "") for x in langs]

            store = getattr(self, "_code_blocks", None)
            if store is None:
                store = self._code_blocks = {}

            def _to_box(m):
                inner = m.group(1)
                inner = inner.replace("<br />", "").replace("<br>", "")
                cm = _re.search(r'<code>(.*?)</code>', inner, _re.DOTALL)
                code = cm.group(1) if cm else inner
                lang = langs.pop(0) if langs else ""
                import html as _htmlmod
                raw = _htmlmod.unescape(_re.sub(r"<[^>]+>", "", code))
                self._code_seq = getattr(self, "_code_seq", 0) + 1
                cid = self._code_seq
                store[cid] = raw
                for old in [k for k in store if k <= cid - 400]:
                    store.pop(old, None)
                return (
                    '<table width="100%" cellspacing="0" cellpadding="0" '
                    'style="margin:8px 0;"><tr>'
                    f'<td bgcolor="{_CODE_HEAD_BG}" style="padding:5px 10px;'
                    f'border:1px solid {_CODE_BORDER};border-right:none;">'
                    f'<span style="color:#8296b1;font-family:monospace;'
                    f'font-size:12px;">{lang or t("chat.code_plain")}</span></td>'
                    f'<td bgcolor="{_CODE_HEAD_BG}" align="right" '
                    f'style="padding:5px 10px;border:1px solid {_CODE_BORDER};'
                    f'border-left:none;">'
                    f'<a href="hoplita:copy:{cid}" style="color:#38bdf8;'
                    f'font-family:monospace;font-size:12px;'
                    f'text-decoration:none;">⧉ {t("chat.copy_code")}</a>'
                    '</td></tr><tr>'
                    f'<td colspan="2" bgcolor="{_CODE_BG}" style="padding:10px;'
                    f'border:1px solid {_CODE_BORDER};">'
                    f'<pre style="white-space:pre-wrap;margin:0;'
                    f'color:{_CODE_FG};font-family:monospace;">'
                    f'{code}</pre></td></tr></table>')
            html = _re.sub(
                r'<div class="codehilite"[^>]*>(.*?)</div>',
                _to_box, html, flags=_re.DOTALL)

            html = html.replace("<code>", '<code style="background:#1b2942;'
                                'padding:1px 5px;'
                                'font-family:monospace;color:#9ecbff">')

            _stripped = html.strip()
            if _stripped.startswith("<p>"):
                _close = _stripped.find("</p>")
                if _close != -1:
                    first = _stripped[3:_close]
                    rest = _stripped[_close + 4:].strip()
                    if not rest:
                        html = first
                    elif rest.startswith("<p>"):
                        html = first + "<br><br>" + rest
                    else:
                        html = first + "<br>" + rest
            return f'<span style="color:{base_color}">{html}</span>'
        except Exception:
            return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")

    def add_message(self, message, sender="user", tab_id=None):
        target = tab_id or self._active_tab_id
        if target == CRON_TAB_ID and self._tab_by_id(CRON_TAB_ID) is None:
            self._ensure_cron_tab()
        if self._active_stream_live and target == self._stream_live_tab:
            self._deferred_messages.append((message, sender))
            return
        self._paint_message(message, sender, tab_id)

    def _paint_message(self, message, sender="user", tab_id=None):
        timestamp = datetime.now().strftime("%H:%M:%S")
        message = self.clean_ai_response(message) if sender in ["assistant", "search", "summary", "rag", "todo", "fixed", "web", "browser"] else message
        colors = {
            "user": "#0ea5e9", "assistant": "#e2e8f0", "rag": "#a78bfa",
            "search": "#fb923c", "web": "#34d399", "system": "#22d3ee",
            "error": "#f87171", "summary": "#fb923c", "rawsearch": "#f472b6",
            "todo": "#c084fc", "fixed": "#a855f7", "browser": "#f59e0b",
        }
        labels = {
            "user": t("label.user"), "assistant": t("label.assistant"),
            "rag": t("label.rag"), "search": t("label.search"),
            "web": "Web", "summary": t("label.summary"),
            "rawsearch": t("label.rawsearch"),
            "error": t("label.error"), "system": t("label.system"),
            "todo": t("label.todo"), "fixed": t("label.fixed"),
            "browser": t("label.browser"),
        }
        color = colors.get(sender, "#94a3b8")
        label = labels.get(sender, t("label.system"))
        rich_senders = ("assistant", "summary", "rag", "web", "browser")
        if sender == "tool_usage":
            try:
                self._paint_tool_usage(int(message), tab_id or self._active_tab_id)
            except (TypeError, ValueError):
                pass
            return
        if sender == "qa_review":
            import html as _h
            rec = (getattr(self, "_qa_reviews", None) or {}).get(int(message)) or {}
            okv = rec.get("ok")
            if okv is True:
                verdict, color, label = t("qa.pass"), colors["system"], t("label.system")
            elif okv is None:
                verdict = t("qa.unjudged", why=rec.get("why", ""))
                color, label = "#fbbf24", t("label.system")
            else:
                verdict = t("qa.fail", why=rec.get("why", ""))
                color, label = colors["error"], t("label.error")
            body = (f'<span style="color:{color}">{_h.escape(verdict)}</span>'
                    f'&nbsp;&nbsp;<span style="color:#475569">·</span>&nbsp;&nbsp;'
                    + self._qa_link_html(int(message)))
        elif sender in rich_senders:
            body = self._render_markdown(message, color)
        else:
            safe = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
            body = f'<span style="color:{color}">{safe}</span>'
        rate = (self._rating_footer(tab_id or self._active_tab_id, message)
                if sender == "assistant" else "")
        html = (f'<span style="color:#64748b">[{timestamp}]</span> '
                f'<span style="color:{color};font-weight:bold">{label}:</span> '
                f'{body}{rate}<br>')
        self._append_html_to_tab(tab_id or self._active_tab_id, html)


    def update_rag_status(self):
        stats = self.rag_manager.get_stats()
        self.rag_status_label.setText(
            f"RAG: {stats['total_chunks']} chunks · RAW: {stats['raw_texts']['total_sources']} ({stats['raw_texts']['total_raw_words']:,} p) · Disp: {stats['available_chunks']}")
        self.fixed_status_label.setText(f"/fixed: {len(self.user_fixed_context)}")


    def _new_task_dialog(self):
        project = self._selected_project or "general"
        dlg = QDialog(self)
        dlg.setWindowTitle(t("gui.new_task_in", project=project))
        dlg.resize(500, 450)
        layout = QVBoxLayout(dlg)
        pr_row = QHBoxLayout()
        pr_row.addWidget(QLabel(t("todo.priority")))
        pr_combo = QComboBox()
        pr_combo.addItems(list(TASK_PRIORITIES))
        pr_combo.setCurrentIndex(1)
        pr_row.addWidget(pr_combo, 1)
        pr_row.addWidget(QLabel(t("lbl.project")))
        proj_combo = QComboBox()
        projects = self.todo_manager.get_projects() or ["general"]
        proj_combo.addItems(projects)
        idx = proj_combo.findText(project)
        proj_combo.setCurrentIndex(idx if idx >= 0 else 0)
        pr_row.addWidget(proj_combo, 1)
        layout.addLayout(pr_row)
        title_entry = QLineEdit()
        title_entry.setPlaceholderText(t("ui.title"))
        layout.addWidget(QLabel(t("todo.title_field")))
        layout.addWidget(title_entry)
        desc_edit = QTextEdit()
        desc_edit.setAcceptRichText(False)
        desc_edit.setPlaceholderText(t("ui.description"))
        layout.addWidget(QLabel(t("todo.description")))
        layout.addWidget(desc_edit, 1)
        urls_entry = QLineEdit()
        urls_entry.setPlaceholderText(t("gui.rag_urls_placeholder"))
        layout.addWidget(QLabel("RAG URLs:"))
        layout.addWidget(urls_entry)
        prog_row = QHBoxLayout()
        prog_label = QLabel("Progress: 0%")
        prog_row.addWidget(prog_label)
        prog_slider = QSlider(Qt.Orientation.Horizontal)
        prog_slider.setRange(0, 100)
        prog_slider.setSingleStep(5)
        prog_slider.valueChanged.connect(lambda v: prog_label.setText(f"Progress: {v}%"))
        prog_row.addWidget(prog_slider, 1)
        layout.addLayout(prog_row)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            title = title_entry.text().strip()
            if not title:
                QMessageBox.warning(self, t("dlg.missing_title"), t("dlg.title_required"))
                return
            _args = (pr_combo.currentText(), title,
                     desc_edit.toPlainText().strip(), urls_entry.text().strip(),
                     prog_slider.value(), proj_combo.currentText())
            self._busy(True, t("msg.saving"))
            self._bg(lambda: self.todo_manager.add_task(*_args),
                     lambda _r: (self._busy(False), self.refresh_todo_lists()))

    def _new_apt_dialog(self, preset_date=None):
        project = self._selected_project or "general"
        dlg = QDialog(self)
        dlg.setWindowTitle(t("gui.new_appt_in", project=project))
        dlg.resize(500, 450)
        layout = QVBoxLayout(dlg)
        date_row = QHBoxLayout()
        date_row.addWidget(QLabel(t("lbl.date")))
        date_edit = self._apt_date_edit(preset_date if isinstance(preset_date, str) else None)
        date_row.addWidget(date_edit, 1)
        layout.addLayout(date_row)
        urg_row = QHBoxLayout()
        urg_row.addWidget(QLabel(t("todo.urgency")))
        urg_combo = QComboBox()
        urg_combo.addItems(urgency_labels())
        urg_combo.setCurrentIndex(1)
        urg_row.addWidget(urg_combo, 1)
        urg_row.addWidget(QLabel(t("lbl.project")))
        proj_combo = QComboBox()
        projects = self.todo_manager.get_projects() or ["general"]
        proj_combo.addItems(projects)
        idx = proj_combo.findText(project)
        proj_combo.setCurrentIndex(idx if idx >= 0 else 0)
        urg_row.addWidget(proj_combo, 1)
        layout.addLayout(urg_row)
        title_entry = QLineEdit()
        title_entry.setPlaceholderText(t("ui.title"))
        layout.addWidget(QLabel(t("todo.title_field")))
        layout.addWidget(title_entry)
        desc_edit = QTextEdit()
        desc_edit.setAcceptRichText(False)
        desc_edit.setPlaceholderText(t("ui.description"))
        layout.addWidget(QLabel(t("todo.description")))
        layout.addWidget(desc_edit, 1)
        urls_entry = QLineEdit()
        urls_entry.setPlaceholderText(t("gui.rag_urls_placeholder"))
        layout.addWidget(QLabel("RAG URLs:"))
        layout.addWidget(urls_entry)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            date = date_edit.dateTime().toString("yyyy-MM-dd HH:mm")
            title = title_entry.text().strip()
            if not date or not title:
                QMessageBox.warning(self, t("dlg.missing_fields"), t("dlg.date_title_required"))
                return
            conflict = self._find_apt_conflict(date)
            if conflict:
                msg = t("dlg.appt_exists", date=date, proj=conflict.get('project','general'), title=conflict['title'])
                reply = QMessageBox.question(self, t("dlg.schedule_conflict"), msg,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if reply != QMessageBox.StandardButton.Yes:
                    return
            self.todo_manager.add_appointment(
                date, title, desc_edit.toPlainText().strip(),
                urls_entry.text().strip(), APT_URGENCY_KEYS[urg_combo.currentIndex()],
                proj_combo.currentText())
            self.refresh_todo_lists()

    def _find_apt_conflict(self, date_str, exclude_id=None):
        for apt in (self.todo_manager.get_appointments() or []):
            if exclude_id and apt.get("id") == exclude_id:
                continue
            if apt.get("date", "").strip() == date_str.strip():
                return apt
        return None


    def _load_todo_projects(self):
        prev = self._selected_project
        self.todo_projects_list.blockSignals(True)
        self.todo_projects_list.clear()
        ok, r, _ = self._mcp_try("list_todo_projects", {}, timeout=15)
        projects = ((r or {}).get("projects") if ok else None) \
            or (self.todo_manager.get_projects() or ["general"])
        _linked = (r or {}).get("code_linked", {}) if ok else {}
        for proj in projects:
            if proj in _linked:
                item = QListWidgetItem(f"💻 {proj}")
                item.setToolTip(t("todo.code_linked", path=_linked[proj]))
            else:
                item = QListWidgetItem(f"📁 {proj}")
            item.setData(Qt.ItemDataRole.UserRole, proj)
            self.todo_projects_list.addItem(item)
        target_idx = 0
        if prev:
            for i, p in enumerate(projects):
                if p == prev:
                    target_idx = i
                    break
        if self._cal_active:
            self.todo_projects_list.setCurrentRow(-1)
            self._selected_project = None
        else:
            self.todo_projects_list.setCurrentRow(target_idx)
            self._selected_project = projects[target_idx] if projects else None
        self._todo_project_names = list(projects)
        self.todo_projects_list.blockSignals(False)

    def _on_project_selected(self, row):
        if row < 0:
            self._selected_project = None
            return
        item = self.todo_projects_list.item(row)
        if not item:
            return
        self._cal_active = False
        self._cal_btn.setChecked(False)
        self._todo_right.setCurrentIndex(0)
        self._selected_project = item.data(Qt.ItemDataRole.UserRole)
        self._refresh_todo_items_only()
        self._refresh_project_board()

    def _todo_show_calendar(self):
        self._cal_active = True
        self._cal_btn.setChecked(True)
        self.todo_projects_list.blockSignals(True)
        self.todo_projects_list.setCurrentRow(-1)
        self.todo_projects_list.blockSignals(False)
        self._selected_project = None
        self._todo_right.setCurrentIndex(1)
        if self._calendar is not None:
            self._cal_push()
            self._cal_refresh_pending()

    def _cal_push(self):
        if self._calendar is None:
            return
        apts = [{"id": a.get("id"), "date": a.get("date", ""),
                 "title": a.get("title", ""),
                 "description": a.get("description", ""),
                 "urgency": normalize_apt_urgency(a.get("urgency")),
                 "project": a.get("project", "general")}
                for a in self._cal_last_apts]
        self._calendar.set_state({"appointments": apts,
                                  "projects": list(self._todo_project_names)})

    def _cal_refresh_pending(self):
        if self._calendar is None:
            return

        def work():
            import json as _js
            try:
                raw = self._mcp_call("fed_list_incoming_appointments", {})
                data = (_js.loads(raw) if isinstance(raw, str)
                        and not raw.startswith("❌") else {})
                return (len(data.get("appointments", []) or [])
                        if isinstance(data, dict) else 0)
            except Exception:
                return 0
        self._bg(work, lambda n: self._calendar.set_pending(n))

    def _on_calendar_action(self, a):
        op = a.get("op")
        wanted = str(a.get("id"))
        apt = next((x for x in self._cal_last_apts
                    if str(x.get("id")) == wanted), None)
        if op == "new":
            self._new_apt_dialog(a.get("date") or None)
        elif op == "edit" and apt:
            self.edit_appointment(apt)
        elif op == "delete" and apt:
            self.todo_manager.delete_appointment(apt["id"])
            self.refresh_todo_lists()
        elif op == "pending":
            self._show_pending_appointments()
            self._cal_refresh_pending()

    def _apt_date_edit(self, preset=None):
        from PyQt6.QtWidgets import QDateTimeEdit
        from PyQt6.QtCore import QDateTime, QTime
        w = QDateTimeEdit()
        w.setCalendarPopup(True)
        w.setDisplayFormat("yyyy-MM-dd HH:mm")
        dt = QDateTime()
        if preset:
            text = str(preset).strip()
            dt = QDateTime.fromString(text[:16], "yyyy-MM-dd HH:mm")
            if not dt.isValid():
                dt = QDateTime.fromString(text[:10], "yyyy-MM-dd")
        if not dt.isValid():
            now = QDateTime.currentDateTime()
            dt = QDateTime(now.date(), QTime((now.time().hour() + 1) % 24, 0))
        w.setDateTime(dt)
        return w

    def _todo_add_project(self):
        name, ok = QInputDialog.getText(self, t("btn.new_project"),
                                         "Project name:")
        if not ok or not name.strip():
            return
        result = self.todo_manager.add_project(name.strip())
        if result and result.get("error"):
            QMessageBox.warning(self, t("dlg.error"), result["error"])
            return
        self._selected_project = name.strip()
        self.refresh_todo_lists()

    def _todo_rename_project(self):
        old = self._selected_project
        if not old:
            QMessageBox.information(self, t("btn.rename"),
                "Select a project first.")
            return
        if old == "general":
            QMessageBox.information(self, t("btn.rename"),
                "The 'general' project cannot be renamed.")
            return
        new, ok = QInputDialog.getText(self, t("gui.rename_project"),
            f"New name for '{old}':", text=old)
        if not ok or not new.strip() or new.strip() == old:
            return
        result = self.todo_manager.rename_project(old, new.strip())
        if result and result.get("error"):
            QMessageBox.warning(self, t("dlg.error"), result["error"])
            return
        self._selected_project = new.strip()
        self.refresh_todo_lists()

    def _todo_delete_project(self):
        proj = self._selected_project
        if not proj:
            QMessageBox.information(self, t("dlg.delete"),
                t("dlg.select_project_first"))
            return
        if proj == "general":
            QMessageBox.information(self, t("dlg.delete"),
                t("dlg.general_cannot_delete"))
            return
        n_apt = len([a for a in (self.todo_manager.get_appointments() or [])
                     if a.get("project") == proj])
        n_task = len([t for t in (self.todo_manager.get_tasks() or [])
                      if t.get("project") == proj])
        msg = t("dlg.delete_project_q", proj=proj)
        if n_apt or n_task:
            msg += t("dlg.project_move_detail", n_apt=n_apt, n_task=n_task)
        reply = QMessageBox.question(self, t("dlg.delete_project"), msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._fed_action("fed_unshare_project", {"project_name": proj})
        result = self.todo_manager.delete_project(proj)
        if result and result.get("error"):
            QMessageBox.warning(self, t("dlg.error"), result["error"])
            return
        self._selected_project = "general"
        self.refresh_todo_lists()

    def _refresh_todo_items_only(self):
        proj = self._selected_project
        tasks = self.todo_manager.get_tasks(proj) or []
        apts = self.todo_manager.get_appointments() or []

        import json as _json
        snap = _json.dumps([proj, tasks, apts], sort_keys=True, default=str)
        if getattr(self, "_todo_snapshot", None) == snap:
            return
        self._todo_snapshot = snap

        def _sel_id(lst):
            it = lst.currentItem() if lst is not None else None
            return (it.data(Qt.ItemDataRole.UserRole) or {}).get("id") if it else None
        sel_task = _sel_id(self.tasks_list)
        sel_apt = _sel_id(self.appointments_list)
        self.tasks_list.blockSignals(True)

        self.tasks_list.clear()
        for task in tasks:
            pr = normalize_task_priority(task.get("priority"))
            emoji = {"Alta": "🔴", "Media": "🟡", "Baja": "🟢"}.get(pr, "⚪")
            p = task.get("progress", 0); bar = "█" * int(p / 10) + "░" * (10 - int(p / 10))
            text = f"{emoji} [{pr}] {task['title']} - {p}% {bar}"
            if task['description']: text += f"\n  {task['description'][:80]}"
            item = QListWidgetItem(text); item.setData(Qt.ItemDataRole.UserRole, task)
            self.tasks_list.addItem(item)
        self._cal_last_apts = list(apts)
        if self._calendar is not None:
            self._cal_push()
        else:
            self.appointments_list.blockSignals(True)
            self.appointments_list.clear()
            for apt in apts:
                urg = normalize_apt_urgency(apt.get("urgency"))
                emoji = {"low": "🟢", "normal": "🔵", "high": "🟡", "critical": "🔴"}.get(urg, "⚪")
                apt_proj = apt.get("project", "general")
                text = f"{emoji} ⟨{apt_proj}⟩ [{urgency_label(urg)}] {apt['date']} - {apt['title']}"
                if apt['description']: text += f"\n  {apt['description'][:80]}"
                item = QListWidgetItem(text); item.setData(Qt.ItemDataRole.UserRole, apt)
                self.appointments_list.addItem(item)

        def _restore(lst, sid):
            if lst is None or sid is None:
                return
            for i in range(lst.count()):
                d = lst.item(i).data(Qt.ItemDataRole.UserRole) or {}
                if d.get("id") == sid:
                    lst.setCurrentRow(i); break
        _restore(self.tasks_list, sel_task)
        _restore(self.appointments_list, sel_apt)
        self.tasks_list.blockSignals(False)
        if self.appointments_list is not None:
            self.appointments_list.blockSignals(False)
        self.tasks_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        try: self.tasks_list.customContextMenuRequested.disconnect()
        except: pass
        self.tasks_list.customContextMenuRequested.connect(self._task_context_menu)

    def _auto_refresh_content_tabs(self):
        if getattr(self, "_content_refreshing", False):
            return
        try:
            if self._todo_widget_ref.isVisible():
                self._content_refreshing = True
                proj = self._selected_project
                self._bg(lambda: (self.todo_manager.get_projects(),
                                  self.todo_manager.get_tasks(proj),
                                  self.todo_manager.get_appointments()),
                         lambda res: self._apply_todo_auto(res))
            elif self._notes_widget_ref.isVisible():
                self._content_refreshing = True
                self._bg(self.notes_manager.get_category_counts,
                         self._apply_notes_counts)
        except Exception:
            self._content_refreshing = False

    def _apply_todo_auto(self, res):
        try:
            if res is None:
                return
            import json as _json
            snap = _json.dumps(res, sort_keys=True, default=str)
            if getattr(self, "_todo_auto_snap", None) == snap:
                return
            self._todo_auto_snap = snap
            self.refresh_todo_lists()
        except Exception:
            pass
        finally:
            self._content_refreshing = False
        try:
            self._refresh_project_board()
        except Exception:
            pass

    def refresh_todo_lists(self):
        self._load_todo_projects()
        self._refresh_todo_items_only()

    def _proj_subtab(self, n):
        names = ("list", "board", "tree", "timeline")
        for i, b in enumerate(self._proj_sub_btns):
            b.setChecked(i == n)
        self._proj_view = names[n]
        if n == 0 or self._proj_board is None:
            if self._proj_board is None and n != 0:
                for i, b in enumerate(self._proj_sub_btns):
                    b.setChecked(i == 0)
                self._proj_view = "list"
            self._proj_stack.setCurrentIndex(0)
            return
        self._proj_stack.setCurrentIndex(1)
        self._proj_board.show_view(self._proj_view)
        self._refresh_project_board()

    def _projects_labels(self):
        return {k: t("proj." + k) for k in (
            "subcount", "board_empty", "timeline_empty", "cycle", "me",
            "save", "cancel", "deps_title", "deps_sub", "deps_tip",
            "deps_none_avail", "depends_on", "pnum_tip", "host_tip",
            "miss_deleted", "miss_elsewhere", "miss_unknown",
            "blocked_tip", "ready_tip",
            "ev_task_created", "ev_task_updated", "ev_task_deleted")}

    def _refresh_project_board(self, force=False):
        if self._proj_board is None or self._proj_view == "list":
            return
        proj = self._selected_project or "general"
        def apply(res):
            if not isinstance(res, dict):
                return
            import json as _json
            snap = _json.dumps(res, sort_keys=True, default=str)
            if not force and getattr(self, "_proj_board_snap", None) == snap:
                return
            self._proj_board_snap = snap
            self._proj_board.set_state(res)
        self._bg(lambda: self.todo_manager.get_project_board(proj), apply)

    def _project_is_shared(self, proj):
        import time as _t
        import json as _js
        cache = getattr(self, "_proj_shared_cache", None)
        if cache is None:
            cache = self._proj_shared_cache = {}
        hit = cache.get(proj)
        if hit and _t.time() - hit[0] < 60:
            return hit[1]
        shared = False
        try:
            sp = self._mcp_call("fed_is_shared_project", {"project_name": proj})
            shared = (bool(_js.loads(sp).get("shared"))
                      if isinstance(sp, str) and not sp.startswith("❌")
                      else False)
        except Exception:
            shared = False
        cache[proj] = (_t.time(), shared)
        return shared

    def _on_board_edits(self, batch):
        def work():
            import json as _js
            warns = []
            for e in (batch or []):
                try:
                    field = e.get("field")
                    if field not in ("progress", "deps"):
                        continue
                    proj = e.get("project") or "general"
                    if self._project_is_shared(proj) and e.get("uuid"):
                        r = self._mcp_call("fed_save_task_guarded", {
                            "project": proj, "task_uuid": e["uuid"],
                            "payload": {field: e.get("value")},
                            "base_rev": e.get("rev", 1)})
                        try:
                            d = (_js.loads(r) if isinstance(r, str)
                                 and not r.startswith("❌") else {})
                            if d.get("conflict") and d.get("warning"):
                                warns.append(d["warning"])
                        except Exception:
                            pass
                    elif field == "progress":
                        self.todo_manager.update_task(
                            e.get("id"), progress=int(e.get("value") or 0))
                    else:
                        self.todo_manager.update_task(
                            e.get("id"), deps=list(e.get("value") or []))
                except Exception:
                    pass
            return warns
        def done(warns):
            for w in (warns or []):
                QMessageBox.warning(self, t("fed.conflict_title"), w)
            self._refresh_project_board(force=True)
            self.refresh_todo_lists()
        self._bg(work, done)

    def _apt_context_menu(self, pos):
        item = self.appointments_list.itemAt(pos)
        if not item: return
        apt = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        edit_action = menu.addAction(t("menu.edit")); delete_action = menu.addAction(t("menu.delete"))
        action = menu.exec(self.appointments_list.mapToGlobal(pos))
        if action == edit_action: self.edit_appointment(apt)
        elif action == delete_action: self.todo_manager.delete_appointment(apt["id"]); self.refresh_todo_lists()

    def _task_context_menu(self, pos):
        item = self.tasks_list.itemAt(pos)
        if not item: return
        _sel = self.tasks_list.selectedItems()
        if len(_sel) > 1:
            menu = QMenu(self)
            del_n = menu.addAction(t("menu.delete_n", n=len(_sel)))
            if menu.exec(self.tasks_list.mapToGlobal(pos)) == del_n:
                self._delete_selected_tasks()
            return
        task = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        edit_action = menu.addAction(t("menu.edit")); delete_action = menu.addAction(t("menu.delete"))
        action = menu.exec(self.tasks_list.mapToGlobal(pos))
        if action == edit_action: self.edit_task(task)
        elif action == delete_action:
            self._delete_tasks([task])

    def _delete_selected_tasks(self):
        tasks = [i.data(Qt.ItemDataRole.UserRole) for i in self.tasks_list.selectedItems()]
        tasks = [tk for tk in tasks if isinstance(tk, dict) and tk.get("id")]
        if not tasks:
            return
        if len(tasks) > 1:
            r = QMessageBox.question(self, t("dlg.confirm"),
                                     t("dlg.delete_n_tasks", n=len(tasks)),
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        self._delete_tasks(tasks)

    def _delete_tasks(self, tasks):
        def _work():
            for task in tasks:
                proj = task.get("project", "general")
                if task.get("uuid"):
                    self._mcp_call("fed_delete_task_guarded", {"project": proj, "task_uuid": task["uuid"]})
                else:
                    self.todo_manager.delete_task(task["id"])
            return len(tasks)

        def _done(_n):
            self._busy(False)
            self.refresh_todo_lists()

        self._busy(True, t("msg.deleting"))
        self._bg(_work, _done)

    def _show_pending_appointments(self):
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                                     QPushButton)
        import json as _js
        try:
            raw = self._mcp_call("fed_list_incoming_appointments", {})
            data = _js.loads(raw) if isinstance(raw, str) and not raw.startswith("❌") else {}
        except Exception:
            data = {}
        apps = data.get("appointments", []) if isinstance(data, dict) else []
        dlg = QDialog(self)
        dlg.setWindowTitle(t("fed.pending_appts"))
        dlg.setMinimumWidth(480)
        dlg.setStyleSheet("QDialog { background:#1e293b; }")
        lay = QVBoxLayout(dlg); lay.setSpacing(10)
        if not apps:
            lbl = QLabel(t("fed.pending_appts_empty"))
            lbl.setStyleSheet("QLabel { color:#94a3b8; }")
            lay.addWidget(lbl)
        else:
            for a in apps:
                row = QHBoxLayout()
                txt = QLabel(f"<b>{a.get('title','(untitled)')}</b> — {a.get('date','')}"
                             f"<br><span style='color:#64748b'>{t('fed.from')}: "
                             f"{a.get('from','?')} · {a.get('urgency','Normal')}</span>")
                txt.setStyleSheet("QLabel { color:#e2e8f0; }")
                txt.setWordWrap(True)
                row.addWidget(txt, 1)
                btn = QPushButton("✓ " + t("fed.accept"))
                btn.setStyleSheet("QPushButton { background:#14532d; color:#86efac; "
                                  "border-radius:6px; padding:6px 10px; }")

                def _mk(app):
                    def _do():
                        try:
                            self._mcp_call("fed_accept_appointment",
                                           {"title": app.get("title"),
                                            "from_handle": app.get("from")})
                        except Exception:
                            pass
                        dlg.accept()
                        self.refresh_todo_lists()
                        self._show_pending_appointments()
                    return _do
                btn.clicked.connect(_mk(a))
                row.addWidget(btn)
                lay.addLayout(row)
        close = QPushButton(t("fed.close"))
        close.clicked.connect(dlg.reject)
        lay.addWidget(close)
        dlg.exec()

    def edit_appointment(self, apt):
        dlg = QDialog(self); dlg.setWindowTitle(t("dlg.edit_appointment", title=apt["title"])); dlg.resize(500, 400)
        layout = QVBoxLayout(dlg)
        date_row = QHBoxLayout(); date_row.addWidget(QLabel(t("lbl.date")))
        date_edit = self._apt_date_edit(apt.get("date")); date_row.addWidget(date_edit, 1); layout.addLayout(date_row)
        title_entry = QLineEdit(apt["title"]); layout.addWidget(QLabel(t("todo.title_field"))); layout.addWidget(title_entry)
        desc_edit = QTextEdit(); desc_edit.setAcceptRichText(False); desc_edit.setPlainText(apt["description"]); layout.addWidget(QLabel(t("todo.description"))); layout.addWidget(desc_edit, 1)
        urls_entry = QLineEdit(apt["rag_urls"]); layout.addWidget(QLabel("RAG URLs:")); layout.addWidget(urls_entry)
        urgency_row = QHBoxLayout(); urgency_row.addWidget(QLabel(t("todo.urgency"))); urgency_combo = QComboBox()
        urgency_combo.addItems(urgency_labels())
        urgency_combo.setCurrentIndex(APT_URGENCY_KEYS.index(
            normalize_apt_urgency(apt.get("urgency"))))
        urgency_row.addWidget(urgency_combo, 1)
        urgency_row.addWidget(QLabel(t("lbl.project")))
        edit_proj_combo = QComboBox()
        projects_list = self.todo_manager.get_projects() or ["general"]
        edit_proj_combo.addItems(projects_list)
        cur_proj = apt.get("project", "general")
        idx = edit_proj_combo.findText(cur_proj)
        edit_proj_combo.setCurrentIndex(idx if idx >= 0 else 0)
        urgency_row.addWidget(edit_proj_combo, 1)
        layout.addLayout(urgency_row)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject); layout.addWidget(btns)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_date = date_edit.dateTime().toString("yyyy-MM-dd HH:mm")
            if new_date != apt.get("date", "").strip():
                conflict = self._find_apt_conflict(new_date, exclude_id=apt["id"])
                if conflict:
                    msg = t("dlg.appt_exists2", date=new_date, proj=conflict.get('project','general'), title=conflict['title'])
                    reply = QMessageBox.question(self, t("dlg.schedule_conflict"), msg,
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No)
                    if reply != QMessageBox.StandardButton.Yes:
                        return
            self.todo_manager.update_appointment(apt["id"], date=new_date, title=title_entry.text(),
                description=desc_edit.toPlainText(), rag_urls=urls_entry.text(),
                urgency=APT_URGENCY_KEYS[urgency_combo.currentIndex()], project=edit_proj_combo.currentText())
            self.refresh_todo_lists()

    def edit_task(self, task):
        dlg = QDialog(self); dlg.setWindowTitle(t("dlg.edit_task", title=task["title"])); dlg.resize(500, 450)
        layout = QVBoxLayout(dlg)
        pr_row = QHBoxLayout(); pr_row.addWidget(QLabel(t("todo.priority"))); pr_combo = QComboBox()
        pr_combo.addItems(list(TASK_PRIORITIES))
        pr_combo.setCurrentIndex(TASK_PRIORITIES.index(
            normalize_task_priority(task.get("priority"))))
        pr_row.addWidget(pr_combo, 1)
        pr_row.addWidget(QLabel(t("lbl.project")))
        edit_proj_combo = QComboBox()
        projects_list = self.todo_manager.get_projects() or ["general"]
        edit_proj_combo.addItems(projects_list)
        cur_proj = task.get("project", "general")
        idx = edit_proj_combo.findText(cur_proj)
        edit_proj_combo.setCurrentIndex(idx if idx >= 0 else 0)
        pr_row.addWidget(edit_proj_combo, 1)
        layout.addLayout(pr_row)
        title_entry = QLineEdit(task["title"]); layout.addWidget(QLabel(t("todo.title_field"))); layout.addWidget(title_entry)
        desc_edit = QTextEdit(); desc_edit.setAcceptRichText(False); desc_edit.setPlainText(task["description"]); layout.addWidget(QLabel(t("todo.description"))); layout.addWidget(desc_edit, 1)
        urls_entry = QLineEdit(task["rag_urls"]); layout.addWidget(QLabel("RAG URLs:")); layout.addWidget(urls_entry)
        prog_row = QHBoxLayout(); prog_label = QLabel(f"Progress: {task.get('progress',0)}%"); prog_row.addWidget(prog_label)
        prog_slider = QSlider(Qt.Orientation.Horizontal); prog_slider.setRange(0, 100); prog_slider.setSingleStep(5)
        prog_slider.setValue(task.get("progress", 0))
        prog_slider.valueChanged.connect(lambda v: prog_label.setText(f"Progress: {v}%"))
        prog_row.addWidget(prog_slider, 1); layout.addLayout(prog_row)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject); layout.addWidget(btns)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            target_proj = edit_proj_combo.currentText()
            cur_proj = task.get("project", "general")
            payload = {"title": title_entry.text(),
                       "description": desc_edit.toPlainText(),
                       "progress": prog_slider.value(),
                       "priority": pr_combo.currentText(),
                       "rag_urls": urls_entry.text()}
            is_shared = False
            try:
                import json as _jts
                sp = self._mcp_call("fed_is_shared_project", {"project_name": cur_proj})
                is_shared = _jts.loads(sp).get("shared") if isinstance(sp, str) and not sp.startswith("❌") else False
            except Exception:
                is_shared = False
            if is_shared and target_proj == cur_proj and task.get("uuid"):
                res = self._mcp_call("fed_save_task_guarded", {
                    "project": cur_proj,
                    "task_uuid": task.get("uuid"),
                    "payload": payload,
                    "base_rev": task.get("rev", 1),
                })
                try:
                    import json as _jts2
                    d = _jts2.loads(res) if isinstance(res, str) and not res.startswith("❌") else {}
                    if d.get("conflict"):
                        from PyQt6.QtWidgets import QMessageBox
                        QMessageBox.warning(self, t("fed.conflict_title"),
                                            d.get("warning", t("fed.conflict_msg")))
                except Exception:
                    pass
            else:
                self.todo_manager.update_task(task["id"], priority=pr_combo.currentText(), title=title_entry.text(),
                    description=desc_edit.toPlainText(), rag_urls=urls_entry.text(),
                    progress=prog_slider.value(), project=target_proj)
            self.refresh_todo_lists()


    def add_category_from_ui(self):
        name = self.category_entry.text().strip()
        if not name: return
        if self.notes_manager.add_category(name): self.category_entry.clear(); self.refresh_notes_lists()
        else: QMessageBox.warning(self, t("dlg.error"), t("dlg.cat_exists", name=name))

    def on_category_selected(self, row):
        if row < 0: self.selected_category = None; return
        item = self.categories_list.item(row)
        if not item:
            self.selected_category = None; return
        cat = item.data(Qt.ItemDataRole.UserRole)
        if cat is None:
            txt = item.text()
            cat = txt[2:].rsplit(" (", 1)[0].strip() if txt.startswith("📁") else txt
        self.selected_category = cat
        self.notes_title_label.setText(f"<b>📄 NOTES - {cat}</b>")
        self._clear_note_editor()
        self.refresh_notes_display()

    def refresh_notes_lists(self):
        self._apply_notes_counts(self.notes_manager.get_category_counts())

    def _apply_notes_counts(self, counts):
        try:
            self._apply_notes_counts_impl(counts)
        finally:
            self._content_refreshing = False

    def _apply_notes_counts_impl(self, counts):
        current = self.selected_category
        if counts is None:
            return
        cats = list(counts.keys())

        _cache = getattr(self, "_fed_cache", {}) or {}
        _pub = {p.get("folder_name"): p.get("subscribers", 0)
                for p in (_cache.get("public") or [])}
        _ro = {n for n, i in (_cache.get("folders") or {}).items()
               if i.get("readonly")}

        try:
            _realms = self.notes_manager.get_realms() or {}
        except Exception:
            _realms = {}
        try:
            _locked = self.notes_manager.get_local_only() or {}
        except Exception:
            _locked = {}
        try:
            _skills = {k for k, v in
                       (self.notes_manager.get_skill_cats() or {}).items() if v}
        except Exception:
            _skills = set()
        _skills.add(self._default_skill_cat())

        _shared = {}
        for _n, _i in (_cache.get("folders") or {}).items():
            if _i.get("readonly"):
                continue
            if _i.get("role") == "owner":
                _ms = [m.get("handle", "?") for m in (_i.get("members") or [])]
                if _ms:
                    _who = ", ".join(_ms[:3])
                    if len(_ms) > 3:
                        _who += f" +{len(_ms) - 3}"
                    _shared[_n] = ("out", _who)
            elif _i.get("role") == "member":
                _shared[_n] = ("in", _i.get("owner") or "?")

        prev = getattr(self, "_notes_snapshot", None)
        snap = (tuple(cats), tuple(sorted(counts.items())),
                tuple(sorted(_pub.items())), tuple(sorted(_ro)),
                tuple(sorted(_realms.items())),
                tuple(sorted(_locked.items())),
                tuple(sorted(_skills)),
                tuple(sorted(_shared.items())))
        if prev == snap:
            return
        self._notes_snapshot = snap

        self.categories_list.blockSignals(True)
        self.categories_list.clear()
        for realm_key, realm_label in (("dev", t("notes.realm_dev")),
                                       ("personal", t("notes.realm_personal")),
                                       ("system", t("notes.realm_system"))):
            members = [c for c in cats
                       if _realms.get(c, "personal") == realm_key]
            if not members:
                continue
            head = QListWidgetItem(realm_label)
            head.setFlags(Qt.ItemFlag.NoItemFlags)
            self.categories_list.addItem(head)
            for cat in members:
                n = counts.get(cat)
                _ic = "⚙️" if realm_key == "system" else "📁"
                label = f"{_ic} {cat} ({n})" if n is not None else f"{_ic} {cat}"
                if cat in _pub:
                    label += f"   📢 {_pub[cat]}"
                elif cat in _ro:
                    label += "   🔒📥"
                _sh = _shared.get(cat)
                if _sh:
                    label += "   📤" if _sh[0] == "out" else "   📥"
                if cat in _skills:
                    label += "   🧠"
                if _locked.get(cat):
                    label += "   🔒"
                it = QListWidgetItem(label)
                _tips = []
                if _sh:
                    _tips.append(t("notes.shared_out_tip", who=_sh[1])
                                 if _sh[0] == "out"
                                 else t("notes.shared_in_tip", who=_sh[1]))
                if realm_key == "system":
                    _k = "notes.system_tip_" + cat.replace("-", "_")
                    _txt = t(_k)
                    _tips.append(t("notes.system_tip") if _txt == _k else _txt)
                if cat in _skills:
                    _tips.append(t("notes.skills_tip"))
                if _locked.get(cat):
                    _tips.append(t("notes.local_only_tip"))
                elif cat in _pub:
                    _tips.append(t("fed.public_badge_tip", n=_pub[cat]))
                elif cat in _ro:
                    _tips.append(t("fed.readonly_badge_tip"))
                if _tips:
                    it.setToolTip("\n".join(_tips))
                it.setData(Qt.ItemDataRole.UserRole, cat)
                self.categories_list.addItem(it)
        if current:
            for i in range(self.categories_list.count()):
                if (self.categories_list.item(i).data(Qt.ItemDataRole.UserRole)
                        == current):
                    self.categories_list.setCurrentRow(i)
                    break
        self.categories_list.blockSignals(False)
        self.categories_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        try: self.categories_list.customContextMenuRequested.disconnect()
        except: pass
        self.categories_list.customContextMenuRequested.connect(self._cat_context_menu)

        if current and prev is not None:
            prev_counts = dict(prev[1])
            if prev_counts.get(current) != counts.get(current):
                self.refresh_notes_display()

    def _cat_context_menu(self, pos):
        try:
            self._cat_context_menu_impl(pos)
        except Exception as e:
            print(f"[GUI] category menu failed: {e}")

    def _default_skill_cat(self):
        try:
            return (self.config.get("BROWSER", "default_skill_category",
                                    fallback="skills") or "skills").strip()
        except Exception:
            return "skills"

    def _cat_context_menu_impl(self, pos):
        item = self.categories_list.itemAt(pos)
        if not item: return
        cat_real = item.data(Qt.ItemDataRole.UserRole)
        if cat_real is None:
            txt = item.text()
            cat_real = txt[2:].rsplit(" (", 1)[0].strip() if txt.startswith("📁") else txt
        cat = cat_real; menu = QMenu(self)
        try:
            _realm = self.notes_manager.get_realm(cat)
        except Exception:
            _realm = "personal"
        _is_dev = (_realm == "dev")
        realm_action = None
        if _realm != "system":
            realm_action = menu.addAction(t("notes.to_personal") if _is_dev
                                          else t("notes.to_dev"))
        try:
            _is_locked = bool((self.notes_manager.get_local_only() or {})
                              .get(cat))
        except Exception:
            _is_locked = False
        lock_action = menu.addAction(t("notes.to_federable") if _is_locked
                                     else t("notes.to_local_only"))
        try:
            _skill_cats = self.notes_manager.get_skill_cats() or {}
        except Exception:
            _skill_cats = {}
        _is_skill = bool(_skill_cats.get(cat))
        skill_action = None
        if cat != self._default_skill_cat():
            skill_action = menu.addAction(t("notes.from_skills") if _is_skill
                                          else t("notes.to_skills"))
        menu.addSeparator()
        delete_action = menu.addAction(t("menu.delete_cat")); rag_action = menu.addAction("📖 Load into RAG")
        share_menu = menu.addMenu("🔗 " + t("fed.share_with"))
        self._populate_share_submenu(share_menu, cat)
        info = (self._fed_cache.get("folders") or {}).get(cat, {})
        if not self._fed_cache:
            menu.addAction("⏳ " + t("fed.cache_syncing")).setEnabled(False)
            self._fed_cache_refresh()
        remove_actions = {}
        delete_all_action = leave_keep_action = leave_del_action = None
        if info.get("role") == "owner" and info.get("members"):
            sub = menu.addMenu("👥 " + t("fed.shared_with_n",
                                         n=len(info["members"])))
            for m in info["members"]:
                mark = "✓" if m.get("status") == "accepted" else "⏳"
                a = sub.addAction(f"{mark} {m['handle']} — "
                                  + t("fed.remove_member"))
                remove_actions[a] = m["handle"]
            delete_all_action = menu.addAction(
                "🗑 " + t("fed.delete_for_all"))
        elif info.get("role") == "member":
            menu.addAction("👤 " + t("fed.shared_by",
                                     owner=info.get("owner", "?"))).setEnabled(False)
            leave_keep_action = menu.addAction("🚪 " + t("fed.leave_keep"))
            leave_del_action = menu.addAction("🚪🗑 " + t("fed.leave_delete"))
        action = menu.exec(self.categories_list.mapToGlobal(pos))
        if skill_action is not None and action == skill_action:
            self.notes_manager.set_skill_cat(cat, not _is_skill)
            self._notes_snapshot = None
            self.refresh_notes_lists()
            self.add_message(t("notes.skills_off", cat=cat) if _is_skill
                             else t("notes.skills_on", cat=cat), "system")
            return
        if realm_action is not None and action == realm_action:
            self.notes_manager.set_realm(cat, "personal" if _is_dev else "dev")
            self._notes_snapshot = None
            self.refresh_notes_lists()
            return
        if action == lock_action:
            _is_pub = any(p.get("folder_name") == cat for p in
                          ((self._fed_cache or {}).get("public") or []))
            if not _is_locked and (_is_pub or info):
                self.add_message(t("notes.lock_shared_first", cat=cat),
                                 "system")
                return
            self.notes_manager.set_local_only(cat, not _is_locked)
            self.add_message(t("notes.unlocked" if _is_locked
                               else "notes.locked", cat=cat), "system")
            self._notes_snapshot = None
            self.refresh_notes_lists()
            return
        if action in remove_actions:
            self._fed_action("fed_unshare_folder",
                             {"folder_name": cat,
                              "peer": remove_actions[action]})
            return
        if delete_all_action and action == delete_all_action:
            if QMessageBox.question(self, t("dlg.confirm"),
                    t("fed.delete_for_all_q", cat=cat,
                      n=len(info.get("members", [])))) == QMessageBox.StandardButton.Yes:
                self._fed_action("fed_delete_shared_folder",
                                 {"folder_name": cat})
                self.selected_category = None
                QTimer.singleShot(1200, self.refresh_notes_lists)
            return
        if leave_keep_action and action == leave_keep_action:
            self._fed_action("fed_unshare_folder", {"folder_name": cat})
            return
        if leave_del_action and action == leave_del_action:
            self._fed_action("fed_unshare_folder", {"folder_name": cat})
            self.notes_manager.delete_category(cat)
            self.selected_category = None
            self.refresh_notes_lists()
            return
        if action == delete_action:
            if QMessageBox.question(self, t("dlg.confirm"), t("dlg.delete_cat_q", cat=cat)) == QMessageBox.StandardButton.Yes:
                self._fed_action("fed_unshare_folder", {"folder_name": cat})
                self.notes_manager.delete_category(cat); self.selected_category = None; self.refresh_notes_lists()
        elif action == rag_action:
            threading.Thread(target=self.load_notes_to_rag, args=(cat,), daemon=True).start()

    def _project_context_menu(self, pos):
        try:
            self._project_context_menu_impl(pos)
        except Exception as e:
            print(f"[GUI] project menu failed: {e}")

    def _project_context_menu_impl(self, pos):
        item = self.todo_projects_list.itemAt(pos)
        if not item:
            return
        proj = item.data(Qt.ItemDataRole.UserRole)
        if not proj:
            return
        menu = QMenu(self)
        share_menu = menu.addMenu("🔗 " + t("fed.share_with"))
        self._populate_share_submenu(share_menu, proj, is_project=True)
        info = (self._fed_cache.get("projects") or {}).get(proj, {})
        if not self._fed_cache:
            menu.addAction("⏳ " + t("fed.cache_syncing")).setEnabled(False)
            self._fed_cache_refresh()
        remove_actions = {}
        delete_all_action = leave_keep_action = leave_del_action = None
        if info.get("role") == "owner" and info.get("members"):
            sub = menu.addMenu("👥 " + t("fed.shared_with_n",
                                         n=len(info["members"])))
            for m in info["members"]:
                mark = "✓" if m.get("status") == "accepted" else "⏳"
                a = sub.addAction(f"{mark} {m['handle']} — "
                                  + t("fed.remove_member"))
                remove_actions[a] = m["handle"]
            delete_all_action = menu.addAction("🗑 " + t("fed.delete_for_all"))
        elif info.get("role") == "member":
            menu.addAction("👤 " + t("fed.shared_by",
                                     owner=info.get("owner", "?"))).setEnabled(False)
            leave_keep_action = menu.addAction("🚪 " + t("fed.leave_keep"))
            leave_del_action = menu.addAction("🚪🗑 " + t("fed.leave_delete"))
        action = menu.exec(self.todo_projects_list.mapToGlobal(pos))
        if action in remove_actions:
            self._fed_action("fed_unshare_project",
                             {"project_name": proj,
                              "peer": remove_actions[action]})
            return
        if delete_all_action and action == delete_all_action:
            if QMessageBox.question(self, t("dlg.confirm"),
                    t("fed.delete_for_all_q", cat=proj,
                      n=len(info.get("members", [])))) == QMessageBox.StandardButton.Yes:
                self._fed_action("fed_delete_shared_project",
                                 {"project_name": proj})
                QTimer.singleShot(1200, self.refresh_todo_lists)
            return
        if leave_keep_action and action == leave_keep_action:
            self._fed_action("fed_unshare_project", {"project_name": proj})
            return
        if leave_del_action and action == leave_del_action:
            self._fed_action("fed_unshare_project", {"project_name": proj})
            self.todo_manager.delete_project(proj)
            self.refresh_todo_lists()
            return

    def _populate_share_submenu(self, submenu, folder_name, is_project=False):
        submenu.setStyleSheet(
            "QMenu { background:#1e293b; color:#e2e8f0; border:1px solid #334155; "
            "border-radius:6px; padding:4px; }"
            "QMenu::item { padding:6px 20px; border-radius:4px; }"
            "QMenu::item:selected { background:#3b82f6; }")

        if not is_project:
            pub = submenu.addAction("🌍 " + t("fed.make_public"))
            pub.triggered.connect(
                lambda checked=False: self._do_publish_folder(folder_name))
            submenu.addSeparator()

        cache = getattr(self, "_fed_cache", {}) or {}
        data = ({"contacts": cache.get("contacts")}
                if cache.get("contacts") is not None
                else {"error": t("fed.cache_syncing")})

        if data.get("error"):
            a = submenu.addAction("⚠ " + str(data["error"])[:40]); a.setEnabled(False)
            return
        accepted = [c for c in data.get("contacts", [])
                    if c.get("status") == "accepted"]
        if not accepted:
            a = submenu.addAction(t("fed.no_contacts_share")); a.setEnabled(False)
            return
        for c in accepted:
            h = c.get("handle", "")
            pres = "🟢" if c.get("online") else "⚫"
            act = submenu.addAction(f"{pres} {h}")
            act.triggered.connect(
                lambda checked=False, handle=h: (
                    self._do_share_project(handle, folder_name) if is_project
                    else self._do_share_folder(handle, folder_name)))

    def _do_share_project(self, handle, project_name):
        def worker():
            r = self._mcp_call("fed_share_project",
                               {"project_name": project_name, "handle": handle})
            ok = not (isinstance(r, str) and r.startswith("❌")) and (
                not isinstance(r, dict) or not r.get("error"))
            self.signals.message.emit(
                (t("fed.shared_ok", folder=project_name, handle=handle) if ok
                 else t("fed.shared_err", folder=project_name)),
                "system" if ok else "error")
        threading.Thread(target=worker, daemon=True, name="fed-share-proj").start()

    def _do_publish_folder(self, folder_name):
        def worker():
            r = self._mcp_call_json("fed_publish_folder",
                                    {"folder_name": folder_name})
            ok = isinstance(r, dict) and r.get("ok")
            err = (r or {}).get("error") if isinstance(r, dict) else str(r)
            self.signals.message.emit(
                (t("fed.published_ok", folder=folder_name) if ok
                 else t("fed.published_err", folder=folder_name,
                        e=str(err)[:120])),
                "system" if ok else "error")
        threading.Thread(target=worker, daemon=True, name="fed-publish").start()

    def _do_share_folder(self, handle, folder_name):
        def worker():
            link = self._mcp_call("fed_share_folder",
                                  {"handle": handle, "folder_name": folder_name})
            self._mcp_call("fed_folder_push", {"folder_name": folder_name})
            ok = not (isinstance(link, str) and link.startswith("❌"))
            self.signals.message.emit(
                (t("fed.shared_ok", folder=folder_name, handle=handle) if ok
                 else t("fed.shared_err", folder=folder_name)),
                "system" if ok else "error")
        threading.Thread(target=worker, daemon=True, name="fed-share").start()

    def add_note_from_ui(self):
        if not self.selected_category: QMessageBox.warning(self, t("dlg.error"), t("dlg.select_category")); return
        title = self.note_title_entry.text().strip()
        if not title: QMessageBox.warning(self, t("dlg.error"), t("dlg.title_required")); return
        content = self._note_editor_markdown().strip()
        _cat, _adj = self.selected_category, self.current_attachment
        self.note_title_entry.clear(); self.note_content_text.clear()
        self.current_attachment = None; self.attached_file_label.clear()
        self._busy(True, t("msg.saving"))
        self._bg(lambda: self.notes_manager.add_note(_cat, title, content, _adj),
                 lambda _r: (self._busy(False), self.refresh_notes_display()))

    def refresh_notes_display(self):
        cat = self.selected_category
        if not cat:
            self.notes_list.blockSignals(True)
            self.notes_list.clear()
            self.notes_list.blockSignals(False)
            return
        self._notes_disp_gen = getattr(self, "_notes_disp_gen", 0) + 1
        gen = self._notes_disp_gen
        if getattr(self, "_notes_disp_cat", None) != cat:
            self.notes_list.blockSignals(True)
            self.notes_list.clear()
            self.notes_list.addItem("⏳ …")
            self.notes_list.blockSignals(False)
        self._bg(lambda: self.notes_manager.get_notes(cat),
                 lambda notes: self._apply_notes_display(gen, cat, notes))

    def _apply_notes_display(self, gen, cat, notes):
        if gen != getattr(self, "_notes_disp_gen", 0) or cat != self.selected_category:
            return
        if notes is None:
            return
        self._notes_disp_cat = cat
        sel = self.notes_list.currentItem()
        sel_note = sel.data(Qt.ItemDataRole.UserRole) \
            if sel and isinstance(sel.data(Qt.ItemDataRole.UserRole), dict) else {}
        sel_uuid, sel_id = sel_note.get("uuid"), sel_note.get("id")
        self.notes_list.blockSignals(True)
        self.notes_list.clear()
        restore_row = -1
        for i, note in enumerate(notes):
            att = " 📎" if note.get("attachment") else ""
            text = f"📄 {note['title']}{att}"
            item = QListWidgetItem(text); item.setData(Qt.ItemDataRole.UserRole, note)
            self.notes_list.addItem(item)
            if (sel_uuid and note.get("uuid") == sel_uuid) \
                    or (not sel_uuid and sel_id and note.get("id") == sel_id):
                restore_row = i
        if restore_row >= 0:
            self.notes_list.setCurrentRow(restore_row)
        self.notes_list.blockSignals(False)
        if restore_row >= 0:
            new = notes[restore_row]
            _pane = (self.note_raw_text
                     if self.note_editor_stack.currentIndex() == 1
                     else self.note_content_text)
            _editing = _pane.document().isModified()
            if not _editing and (new.get("content") != sel_note.get("content")
                                 or new.get("rev") != sel_note.get("rev")):
                self._on_note_selected(restore_row)
        self.notes_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        try: self.notes_list.customContextMenuRequested.disconnect()
        except: pass
        self.notes_list.customContextMenuRequested.connect(self._note_context_menu)

    def _note_context_menu(self, pos):
        item = self.notes_list.itemAt(pos)
        if not item: return
        note = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        edit_action = menu.addAction(t("menu.edit")); delete_action = menu.addAction(t("menu.delete"))
        dl_action = menu.addAction("💾 Download attachment") if note.get("attachment") else None
        exec_action = menu.addAction("🌐 Run as Browse")
        action = menu.exec(self.notes_list.mapToGlobal(pos))
        if action == edit_action:
            row = self.notes_list.row(item)
            if row >= 0:
                self.notes_list.setCurrentRow(row)
        elif action == delete_action:
            if QMessageBox.question(self, t("dlg.confirm"), t("dlg.delete_note_q")) == QMessageBox.StandardButton.Yes:
                _cat = self.selected_category

                def _del():
                    if note.get("uuid"):
                        return self._mcp_call(
                            "fed_delete_note_guarded",
                            {"category": _cat, "note_uuid": note["uuid"]})
                    return self.notes_manager.delete_note(_cat, note["id"])

                self._busy(True, t("msg.deleting"))
                self._bg(_del, lambda _r: (self._busy(False),
                                           self.refresh_notes_display()))
        elif dl_action and action == dl_action: self.download_attachment(note["attachment"])
        elif action == exec_action:
            content = note.get("content", "")
            if content.strip():
                self._run_in_tab(self._active_tab_id, self._handle_browse_command, content)


    def attach_file_to_note(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select file")
        if path:
            if os.path.getsize(path) > 5 * 1024 * 1024: QMessageBox.warning(self, t("dlg.error"), t("err.file_too_big")); return
            self.current_attachment = self.encode_file_to_base64(path)
            if self.current_attachment:
                self.attached_file_label.setText(f"📎 {self.current_attachment['filename']} ({self.current_attachment['size']/1024:.1f} KB)")

    def clear_attachment(self):
        self.current_attachment = None; self.attached_file_label.clear()

    def encode_file_to_base64(self, filepath):
        try:
            import mimetypes
            with open(filepath, 'rb') as f: data = f.read()
            mimetype, _ = mimetypes.guess_type(filepath)
            return {"filename": os.path.basename(filepath), "data": base64.b64encode(data).decode('utf-8'),
                    "mimetype": mimetype or "application/octet-stream", "size": len(data)}
        except Exception as e: print(f"Error: {e}"); return None

    def download_attachment(self, attachment):
        path, _ = QFileDialog.getSaveFileName(self, "Save", attachment["filename"])
        if path:
            try:
                with open(path, 'wb') as f: f.write(base64.b64decode(attachment["data"]))
                QMessageBox.information(self, t("dlg.ok"), t("msg.saved_path", path=path))
            except Exception as e: QMessageBox.warning(self, t("dlg.error"), str(e))


    def request_stop(self):
        if not self._task_running:
            return
        self._cancel_pending_ack(self._active_tab_id)
        self._stop_requested.set()
        self.add_message(t("msg.stopping"), "system")
        _tid = getattr(self._chat_ctxs.get(self._active_tab_id), "turn_id", None)
        if _tid:
            def _stop_now(tid=_tid):
                try:
                    requests.post(f"{self._tab_url()}/agent/stop", json={"turn_id": tid}, timeout=15)
                except Exception:
                    pass
            threading.Thread(target=_stop_now, daemon=True, name="stop-now").start()

    def _task_begin(self):
        tid = getattr(self._tls, "tab_id", None) or self._active_tab_id
        self._stop_requested.clear()
        self._tls.stop_event = self._stop_requested
        self._task_running = True
        _c = self._chat_ctxs.get(tid)
        if _c is not None:
            _c.ctx_measured = False
        self.signals.task_state.emit(tid, True)

    def _task_end(self):
        tid = getattr(self._tls, "tab_id", None) or self._active_tab_id
        self._task_running = False
        self._stop_requested.clear()
        self._tls.stop_event = None
        try:
            ctx = self._chat_ctxs.get(tid)
            if ctx is not None and getattr(ctx, "intent", None):
                _open = {"pending", "doing", "", None}
                if any(str(i.get("status", "")).lower() in _open
                       for i in ctx.intent):
                    for i in ctx.intent:
                        if str(i.get("status", "")).lower() in _open:
                            i["status"] = "unfinished"
                    self.signals.intent_changed.emit(tid or "")
            if ctx is not None and getattr(ctx, "agents", None):
                _dead = {"pending", "running", "waiting", "", None}
                _changed = False
                for c in ctx.agents:
                    if str(c.get("status", "")).lower() in _dead:
                        c["status"] = "cancelled"
                        c["why"] = t("agents.turn_ended")
                        _changed = True
                if _changed and getattr(self, "agents_panel", None) is not None:
                    self.agents_panel.update_state(
                        self._agents_cards_for_active())
        except Exception:
            pass
        self.signals.task_state.emit(tid, False)

    def _stopped(self):
        _ev = getattr(self._tls, "stop_event", None)
        if _ev is not None:
            return _ev.is_set()
        return self._stop_requested.is_set()

    @staticmethod
    def _question_to_queue(msg):
        c = msg.get("content")
        if isinstance(c, str):
            return (c, [])
        text, files = [], []
        for i, part in enumerate(c or []):
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                text.append(part.get("text", ""))
            elif part.get("type") == "image_url":
                m = re.match(r"^data:([\w/.+-]+);base64,(.+)$",
                             str((part.get("image_url") or {}).get("url") or ""), re.S)
                if m:
                    files.append((f"picture_{i}.{m.group(1).split('/')[-1]}", m.group(2),
                                  m.group(1), len(m.group(2)) * 3 // 4))
        return ("\n".join(text), files)

    def _compact_by_turn(self, hist, keep_recent):
        _c = self._turn_ctx()
        instr = {"role": "user", "content": t("prompts.compact_turn", k=keep_recent)}
        try:
            reply = self.call_chat_with_tools(list(hist) + [instr], max_iterations=1,
                                              compact=True) or ""
        except Exception as e:
            print(f"[COMPACT] the turn over the cached prompt failed: {e}", file=sys.stderr)
            reply = ""
        finally:
            _c.turn_last_user = None
            _c.turn_transcript = []
        reply = (reply or "").strip()
        import fed_agent as _fa_cmp
        if len(reply) < 40 or any(s_ in reply for s_ in _fa_cmp.RECORD_SENTINELS):
            return ""
        return reply

    def _auto_compact(self):
        try:
            hist = self.conversation_history
            keep_recent = self.config.getint("CHAT", "compact_keep_recent", fallback=6)
            if len(hist) <= keep_recent + 2:
                self.thread_msg(t("ctx.compact_nothing", n=len(hist),
                                  keep=keep_recent), "system")
                return
            self.thread_msg(t("msg.compacting"), "system", keep=True)
            _c = self._turn_ctx()
            _summary = self._compact_by_turn(hist, keep_recent)
            payload = {"summary": _summary,
                       "messages": [m for m in self._flatten_pictures(hist, every_list=True)
                                    if isinstance(m.get("content"), str)],
                       "keep_recent": keep_recent,
                       "conv": [i for i in (
                           getattr(_c, "session_uuid", "") or "",
                           getattr(_c, "sync_id", "") or "",
                           getattr(_c, "tab_id", "") or "") if i]}
            if self._chat_model_override:
                payload["endpoint"] = dict(zip(
                    ("base_url", "api_key", "model"),
                    self._resolve_model_by_name(self._chat_model_override)))
            r = requests.post(f"{self._tab_url()}/memory/compact", json=payload,
                              timeout=(10, None))
            data = r.json() if r.status_code == 200 else {}
            if not data.get("compacted"):
                self.thread_msg(t("msg.compact_fail",
                                  why=data.get("why") or f"HTTP {r.status_code}"),
                                "system", keep=True)
                return
            self.conversation_history = data["messages"]
            self._turn_ctx().compacted_ok = True
            if data.get("tokens_after"):
                _cc = self._turn_ctx()
                _cc.last_prompt_tokens = int(data["tokens_after"])
                _cc.ctx_estimated = True
                self.signals.token_usage.emit(getattr(_cc, "tab_id", "") or "", int(data["tokens_after"]))
            self._last_extracted_idx = 0
            self.signals.update_rag_status.emit()
            self.thread_msg(
                t("msg.compacted", n=data.get("replaced", 0),
                  total=data.get("summarised_total", data.get("replaced", 0)),
                  since=data.get("since") or "?"), "system", keep=True)
            if data.get("tokens_before"):
                f = lambda n: f"{n/1000:.1f}k" if n >= 1000 else str(n)
                heavy = "; ".join(
                    f"{f(b.get('tokens', 0))} {b.get('role', '')}: "
                    f"{(b.get('head') or '')[:40]}"
                    for b in (data.get("recent_heaviest") or [])[:3])
                self.thread_msg(t("msg.compacted_size",
                                  before=f(data["tokens_before"]),
                                  after=f(data.get("tokens_after", 0)),
                                  recent=f(data.get("recent_tokens", 0)),
                                  heavy=heavy or "—"), "system", keep=True)
            try:
                self._persist_tab(getattr(self._tls, "tab_id", None)
                                  or self._active_tab_id, quiet=True)
            except Exception as _e:
                print(f"[COMPACT] could not persist: {_e}", file=sys.stderr)
        except Exception as e:
            print(f"[COMPACT] error: {e}", file=sys.stderr)
            self.thread_msg(t("msg.compact_fail", why=str(e)[:300]), "error", keep=True)
        finally:
            self._compacting = False

    def _turn_trace_note(self, failed=""):
        _c = self._turn_ctx()
        pasos = list(getattr(_c, "turn_steps", []) or [])
        qa = list(getattr(_c, "turn_qa", []) or [])
        if not pasos and not qa:
            return ""
        lines = []
        for p in pasos[:12]:
            _r = (p.get("result") or "").replace("\n", " ").strip()
            _shown = _r[:1000]
            _full = int(p.get("result_len", len(_r)))
            _cut = t("chat.turn_trace_cut") if _full > len(_shown) else ""
            lines.append(f"  {p.get('action', '?')}({p.get('detail', '')[:90]})"
                          + (f" → {_shown}{_cut}" if _r else ""))
        if len(pasos) > 12:
            lines.append(t("chat.turn_trace_more", n=len(pasos) - 12))
        for v in qa:
            if not v.get("ok"):
                lines.append(t("chat.turn_qa_fail", why=v.get("why", "")))
        if qa and qa[-1].get("ok"):
            lines.append(t("chat.turn_qa_ok"))
        if failed:
            lines.append(t("chat.turn_trace_failed", e=str(failed)[:200]))
        return t("chat.turn_trace", steps="\n".join(lines))

    _turn_locks = {}

    def _run_in_tab(self, tab_id, target, *args, mark_busy=False):
        if mark_busy:
            ctx = self._chat_ctxs.get(tab_id)
            if ctx is not None:
                ctx.task_running = True

        def run():
            self._tls.tab_id = tab_id
            try:
                target(*args)
            finally:
                self._tls.tab_id = None
        threading.Thread(target=run, daemon=True).start()

    def _spawn_turn(self, tab_id, target, *args):
        lock = self._turn_locks.setdefault(tab_id, threading.Lock())
        with lock:
            ctx = self._chat_ctxs.get(tab_id)
            if ctx is not None and getattr(ctx, "turn_live", False):
                return False
            if ctx is not None:
                ctx.turn_live = True
        self._run_in_tab(tab_id, self._turn_wrapper, target, *args,
                         mark_busy=True)
        return True

    def _turn_wrapper(self, target, *args):
        tab_id = getattr(self._tls, "tab_id", None)
        if getattr(target, "__name__", "") in self._UNFOLDED_TURNS:
            self._tls.tu_rid = 0
        else:
            self._tu_begin(tab_id)
        try:
            target(*args)
        finally:
            self._tu_end()
            ctx = self._chat_ctxs.get(tab_id)
            if ctx is not None:
                ctx.turn_live = False
                if getattr(ctx, "task_running", False):
                    try:
                        self._task_end()
                    except Exception:
                        ctx.task_running = False

    def _on_task_state(self, tab_id, running):
        ctx = self._chat_ctxs.get(tab_id)
        if ctx is not None:
            ctx.task_running = running
            ctx.last_active = time.time()
        if running:
            self._enchant_t0[tab_id] = time.time()
        else:
            self._enchant_t0.pop(tab_id, None)
        if tab_id == self._active_tab_id:
            self.stop_btn.setVisible(running)
            self.send_btn.setEnabled(True)
            self._set_enchanting(self._busy_now())
        self._tabs_working_check()
        if not running:
            self._rescue_injected(tab_id)
            self._autosave_tab(tab_id)
            self._job_after_turn(tab_id)
            self._drain_tab_queue(tab_id)
            if tab_id == self._active_tab_id:
                self._update_queue_label()

    def _on_queue_changed(self, tab_id):
        if not tab_id or tab_id == self._active_tab_id:
            self._update_queue_label()

    def _autosave_tab(self, tab_id):
        ctx = self._chat_ctxs.get(tab_id)
        tab = self._tab_by_id(tab_id)
        if ctx is None or tab is None or tab.get("kind") not in ("local", "cron"):
            return
        if getattr(ctx, "no_log", False):
            return
        if tab.get("kind") == "local" and \
                len([m for m in (ctx.history or []) if m.get("role") == "user"]) < 1:
            return
        try:
            _res = self._persist_tab(tab_id, quiet=True)
            if isinstance(_res, dict) and (not _res or _res.get("error")):
                raise RuntimeError(_res.get("error") if _res else
                                   "the MCP did not answer the save (timeout?)")
        except Exception as e:
            print(f"[SESS] autosave: {e}", file=sys.stderr)
            if not getattr(ctx, "_autosave_warned", False):
                ctx._autosave_warned = True
                self.thread_msg(t("sess.autosave_failed", e=str(e)[:120]), "error")

    def _rescue_injected(self, tab_id):
        ctx = self._chat_ctxs.get(tab_id)
        left = list(getattr(ctx, "injected_pending", None) or [])
        if not left:
            return
        ctx.injected_pending = []
        for m in left:
            ctx.msg_queue.append((m, []))
        self.signals.message_tab.emit(t("queue.injected_missed", n=len(left)),
                                      "system", tab_id)
        self._update_queue_label()

    def _drain_tab_queue(self, tab_id):
        ctx = self._chat_ctxs.get(tab_id)
        if ctx is None or ctx.task_running or not ctx.msg_queue:
            return
        item = ctx.msg_queue.pop(0)
        if tab_id == self._active_tab_id:
            self._update_queue_label()
        if tab_id == self._active_tab_id \
                and not str(item[0]).startswith(BG_EVENT_PREFIX):
            self.send_message(_queued=item)
        else:
            self._run_queued_in_tab(tab_id, item)

    def _run_queued_in_tab(self, tab_id, item):
        msg, _pending = item
        ctx = self._chat_ctxs.get(tab_id)
        if ctx is None or not msg:
            return
        _c = _canon_cmd(msg)
        if re.fullmatch(r"/[a-z]+", _c) or (
                (_m := re.match(r"/([a-z]+)\s", _c)) and _m.group(1) in _GUI_CMDS):
            self._append_html_to_tab(tab_id, self._chat_frag(
                t("queue.cmd_skipped", cmd=_c.split()[0]), "#f59e0b", "⏭"))
            return
        if msg.startswith(BG_EVENT_PREFIX):
            self._append_html_to_tab(tab_id, self._background_event_line(msg))
        else:
            self._append_html_to_tab(tab_id, self._chat_frag(
                msg, "#0ea5e9", "👤 " + t("label.user")))
        ctx.history.append({"role": "user", "content": msg})
        if not self._spawn_turn(tab_id, self.call_normal_ai, msg):
            ctx.msg_queue.insert(0, (msg, []))
            self._update_queue_label()

    def _bg_tab_key(self, tab_id, ctx):
        return getattr(ctx, "sync_id", "") or tab_id

    def _tab_for_conversation(self, conv, tab):
        for tid, c in self._chat_ctxs.items():
            if self._tab_by_id(tid) is None:
                continue
            if conv and getattr(c, "session_uuid", None) == conv:
                return tid
        for tid, c in self._chat_ctxs.items():
            if tab and self._tab_by_id(tid) is not None \
                    and self._bg_tab_key(tid, c) == tab:
                return tid
        return None

    def _deliver_background_event(self, ev):
        tid = self._tab_for_conversation(ev.get("conv") or "",
                                         ev.get("tab") or "")
        if tid is not None:
            self._feed_background_event(tid, ev)
            return
        uuid = ev.get("conv") or ""
        if uuid and uuid != (ev.get("tab") or ""):
            self._restore_session(uuid, quiet=True,
                                  then=lambda t_: self._feed_background_event(t_, ev))

    def _feed_background_event(self, tid, ev):
        ctx = self._chat_ctxs.get(tid)
        if ctx is None:
            return
        msg = ev.get("message") or ev.get("text") or ""
        if not msg:
            return
        if getattr(ctx, "task_running", False) and getattr(ctx, "turn_id", None) \
                and self._inject_into_turn(ctx.turn_id, msg, kind="background"):
            return
        ctx.msg_queue.append((msg, []))
        self._render_chat_tabs()
        if tid == self._active_tab_id:
            self._update_queue_label()
        self._drain_tab_queue(tid)

    def _background_event_line(self, msg):
        body = msg.split("\n", 1)[1] if "\n" in msg else msg
        head = body.split("\n", 1)[0]
        return self._chat_frag(head[:300], "#f5b800", "⚙ " + t("bg.label"))

    def _on_model_lent(self, peer, ev):
        alias = str(ev.get("alias") or "?")
        key = (peer, alias)
        told = self.__dict__.setdefault("_lend_announced", set())
        today = self.__dict__.setdefault("_lend_today", {})
        if ev.get("event") == "model_lent":
            told.add(key)
            self.add_message(t("lend.notice_start", peer=peer, alias=alias,
                               model=ev.get("model") or "?"), "system")
            return
        row = today.setdefault(key, {"calls": 0, "prompt": 0, "completion": 0})
        row["calls"] += 1
        row["prompt"] += int(ev.get("prompt_tokens") or 0)
        row["completion"] += int(ev.get("completion_tokens") or 0)
        if key in told:
            told.discard(key)
            self.add_message(t("lend.notice_done", peer=peer, alias=alias,
                               prompt=ev.get("prompt_tokens", 0),
                               completion=ev.get("completion_tokens", 0),
                               seconds=ev.get("seconds", 0), status=ev.get("status", "?")),
                             "system")
        self._paint_lend_live(getattr(self, "_lend_live_rows", []))

    def _paint_lend_live(self, rows):
        self._lend_live_rows = rows or []
        fs = getattr(self, "fed_status", None)
        if fs is not None and hasattr(fs, "set_lending"):
            fs.set_lending(self._lend_live_rows, getattr(self, "_lend_today", {}))

    def _paint_background_live(self, rows):
        by_conv, by_tab = {}, {}
        for r in rows or []:
            if r.get("conv"):
                by_conv[r["conv"]] = r
            if r.get("tab"):
                by_tab[r["tab"]] = r
        changed = False
        for tid, c in self._chat_ctxs.items():
            r = (by_conv.get(getattr(c, "session_uuid", None) or "")
                 or by_tab.get(self._bg_tab_key(tid, c)))
            n = int(r.get("n", 0) or 0) if r else 0
            labels = [str(x) for x in (r.get("labels") or [])] if r else []
            jobs = [j for j in (r.get("jobs") or []) if isinstance(j, dict)] if r else []
            if n != getattr(c, "bg_live", 0) or labels != getattr(c, "bg_labels", []):
                c.bg_live, c.bg_labels = n, labels
                changed = True
            c.bg_jobs = jobs
        if changed:
            self._render_chat_tabs()

    def _cmd_watch(self, msg):
        args = msg.split(None, 2)[1:]
        ctx = self._view_ctx()
        tab_id = self._active_tab_id
        conv = getattr(ctx, "session_uuid", None) or ""
        tkey = self._bg_tab_key(tab_id, ctx)
        url = self._tab_url()
        sub = (args[0] if args else "").lower()
        if not args or sub in ("list", "ls"):
            def work():
                return requests.get(f"{url}/agent/background",
                                    params={"conv": conv, "tab": tkey},
                                    timeout=10).json()

            def done(res):
                from tabs_overview import ago
                jobs = [j for j in ((res or {}).get("jobs") or [])
                        if j.get("state") == "live"]
                if not jobs:
                    self.add_message(t("bg.watch_none"), "system")
                    return
                lines = [t("bg.watch_row", id=j.get("id"), label=j.get("label"),
                           kind=j.get("kind"), age=ago(j.get("age_s") or 0),
                           last=str(j.get("last_preview") or j.get("tool") or "")[:80])
                         for j in jobs]
                self.add_message(t("bg.watch_list", n=len(jobs)) + "\n"
                                 + "\n".join(lines), "system")
            self._bg(work, done)
            return
        if sub == "stop":
            target = (args[1] if len(args) > 1 else "").strip()
            if not target:
                self.add_message(t("bg.watch_usage"), "system")
                return
            payload = ({"conv": conv, "tab": tkey} if target.lower() in ("all", "todos")
                       else {"job_id": target})

            def work2():
                return requests.post(f"{url}/agent/background/stop",
                                     json=payload, timeout=10).json()

            def done2(res):
                n = len((res or {}).get("stopped") or [])
                self.add_message(t("bg.watch_stopped", n=n) if n
                                 else t("bg.watch_stop_none", id=target), "system")
            self._bg(work2, done2)
            return
        if not sub.isdigit() or len(args) < 2:
            self.add_message(t("bg.watch_usage"), "system")
            return
        rest = args[1].strip()
        a = {"conv": conv, "tab": tkey, "every_s": int(sub)}
        if rest.lower().startswith("cmd:"):
            a["command"] = rest[4:].strip()
            a["label"] = a["command"][:40]
        else:
            a["file"] = rest
            a["label"] = os.path.basename(rest.rstrip("/\\")) or rest

        def work3():
            return requests.post(f"{url}/agent/background/watch", json=a,
                                 timeout=30).json()

        def done3(res):
            if not isinstance(res, dict) or res.get("error"):
                self.add_message(t("bg.watch_error", why=str(
                    (res or {}).get("error") if isinstance(res, dict) else res)[:200]),
                    "error")
                return
            self.add_message(t("bg.watch_started", id=res.get("job_id"),
                               label=res.get("label"), every=res.get("every_s"),
                               now=str(res.get("now") or "")[:120]), "system")
        self._bg(work3, done3)

    def _send_queued_session(self, tab_id, peer, direction, msg):
        self._session_waiting.add(tab_id)
        self._enchant_t0[tab_id] = time.time()
        if tab_id == self._active_tab_id:
            self._set_enchanting(True)
        self._tabs_working_check()
        self._append_html_to_tab(tab_id, self._chat_frag(
            msg, "#38bdf8", "👤 " + t("label.user")))
        if direction == "out":
            self._on_session_send(peer, msg)
        else:
            self._on_session_inject(peer, msg)

    def _update_queue_label(self):
        lbl = getattr(self, "queue_label", None)
        box = getattr(self, "queue_box", None)
        if lbl is None:
            return
        _sq = self._session_queue.get(self._active_tab_id)
        q = ([(m, None) for m in _sq] if _sq is not None
             and self._active_tab_id not in self._chat_ctxs else self._msg_queue)
        _c = self._chat_ctxs.get(self._active_tab_id)
        sent = list(getattr(_c, "injected_pending", None) or [])
        _compact = bool(getattr(_c, "compact_pending", False))
        if not q and not sent and not _compact:
            lbl.clear(); lbl.setVisible(False)
            if box is not None:
                box.setVisible(False)
            return

        import html as _html

        def _cut(m):
            return _html.escape((m[:60] + "…") if len(m) > 60 else (m or "📎"))

        def _link(href, text):
            return f'<a href="{href}" style="color:#fbbf24;text-decoration:none">{text}</a>'

        lines = []
        _x = lambda href: _link(href, "✕") + "&nbsp;"
        if sent:
            lines.append(_html.escape(t("queue.box_sent", n=len(sent))))
            lines += [f"&nbsp;&nbsp;{_x(f'sx:{i}')}{_link(f's:{i}', _cut(m))}"
                      for i, m in enumerate(sent, 1)]
        if q:
            lines.append(_html.escape(t("queue.box_header", n=len(q))))
            lines += [f"&nbsp;&nbsp;{_x(f'x:{i}')}{_link(f'q:{i}', f'{i}. {_cut(m)}')}"
                      for i, (m, _p) in enumerate(q, 1)]
        if _compact:
            lines.append(_x("compact") + _link("compact", _html.escape(t("queue.box_compact"))))
        lbl.setVisible(True)
        if box is not None:
            box.setVisible(True)
        lbl.setText("<br>".join(lines))
        lbl.updateGeometry()
        if box is not None:
            width = box.viewport().width() or box.width() or self.width()
            box.setFixedHeight(min(170, lbl.heightForWidth(max(200, width)) + 4))

    def _queue_link(self, href):
        if href == "compact":
            _c = self._chat_ctxs.get(self._active_tab_id)
            if _c is not None and getattr(_c, "compact_pending", False):
                _c.compact_pending = False
                self.add_message(t("ctx.compact_unqueued"), "system")
            self._update_queue_label()
            return
        if href.startswith("s:") or href.startswith("sx:"):
            try:
                self._injected_take(int(href.split(":", 1)[1]),
                                    drop=href.startswith("sx:"))
            except ValueError:
                pass
            return
        if href.startswith("q:") or href.startswith("x:"):
            try:
                self._queue_take(int(href[2:]), drop=href.startswith("x:"))
            except ValueError:
                pass

    def _queue_take(self, n, drop=False):
        q = self._msg_queue
        if not (1 <= n <= len(q)):
            self.add_message(t("msg.queue_bad_index", n=n, total=len(q)), "system")
            return False
        msg, pending = q.pop(n - 1)
        if drop:
            self.add_message(t("msg.queue_dropped", n=n), "system")
        else:
            self.entry.setText(msg or "")
            self.entry.pending_files = list(pending or [])
            try:
                self.entry._show_indicator()
            except Exception:
                pass
            self.entry.setFocus()
            self.add_message(t("msg.queue_pulled", n=n), "system")
        self._update_queue_label()
        return True

    def _injected_take(self, n, drop=False):
        ctx = self._view_ctx()
        sent = list(getattr(ctx, "injected_pending", None) or [])
        if not (1 <= n <= len(sent)):
            self._update_queue_label()
            return False
        msg = sent[n - 1]
        if not self._withdraw_from_turn(getattr(ctx, "turn_id", None), msg):
            self.add_message(t("msg.injected_read"), "system")
            self._update_queue_label()
            return False
        try:
            ctx.injected_pending.remove(msg)
        except ValueError:
            pass
        if drop:
            self.add_message(t("msg.injected_dropped"), "system")
        else:
            self.entry.setText(msg)
            self.entry.setFocus()
            self.add_message(t("msg.injected_pulled"), "system")
        self._update_queue_label()
        return True

    def _active_model_section(self):
        ov = self._chat_model_override
        if ov and ov != "base":
            sec = _model_section(self.config, ov)
            if sec:
                return sec
        return "API"

    _MIN_SANE_CONTEXT = 1024

    def _context_limit(self, measured_only=False):
        _tc = getattr(self, "_turn_ctx", None)
        _own = int(getattr(_tc(), "context_limit", 0) or 0) if callable(_tc) else 0
        if _own > 0:
            return _own
        _pin, _remote = self._tab_runs_remotely(_tc()) if callable(_tc) else (None, False)
        if _remote:
            key = f"tied:{_pin['id']}"
            cache = getattr(self, "_ctx_limit_cache", None)
            if not isinstance(cache, dict):
                cache = self._ctx_limit_cache = {}
            if key in cache:
                return cache[key]
            self._detect_ctx_async(key, None, None, url=self._tab_url(_tc()))
            return 0 if measured_only else 20000
        sec = self._active_model_section()
        cl = self.config.getint(sec, "context_length", fallback=0)
        if cl <= 0:
            cl = self.config.getint("CHAT", "context_length", fallback=0)
        for _s in (sec, "CHAT"):
            if _s != "BROWSER" and self.config.has_option(_s, "num_ctx") \
                    and not getattr(self, "_ctx_alias_said", False):
                self._ctx_alias_said = True
                self.add_message(t("chat.ctx_alias_dead", sec=_s), "error")
        if 0 < cl < self._MIN_SANE_CONTEXT:
            if not getattr(self, "_ctx_absurd_said", False):
                self._ctx_absurd_said = True
                self.add_message(t("chat.ctx_absurd", n=cl,
                                   min=self._MIN_SANE_CONTEXT), "error")
            print(f"[CTX] context_length={cl} is not a window; ignoring it",
                  file=sys.stderr)
            cl = 0
        if cl > 0:
            return cl
        base_url, api_key, model = self._chat_base_endpoint()
        key = f"{base_url}|{model}"
        cache = getattr(self, "_ctx_limit_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._ctx_limit_cache = cache
        if key in cache:
            return cache[key]
        self._detect_ctx_async(key, base_url, api_key)
        if measured_only:
            return 0
        return 20000

    def _detect_ctx_async(self, key, base_url, api_key, url=None):
        pend = getattr(self, "_ctx_detecting", None)
        if pend is None:
            pend = set()
            self._ctx_detecting = pend
        if key in pend:
            return
        pend.add(key)
        def done(n):
            pend.discard(key)
            if n:
                cache = getattr(self, "_ctx_limit_cache", None)
                if cache is None:
                    cache = self._ctx_limit_cache = {}
                cache[key] = n
                f = lambda x: f"{x/1000:.1f}k" if x >= 1000 else str(x)
                self._paint_token_meter()
        self._bg(lambda: self._detect_context_length(base_url, api_key, url=url), done)

    def _detect_context_length(self, base_url=None, api_key=None, url=None):
        try:
            params = {}
            if base_url:
                _b, _k, _m = self._chat_base_endpoint()
                params = {"base_url": base_url, "api_key": api_key or "",
                          "model": _m if base_url == _b else ""}
            r = requests.get(f"{url or self._tab_url()}/agent/context_limit",
                             params=params, timeout=30)
            return int(r.json().get("limit", 0)) if r.status_code == 200 else 0
        except Exception:
            return 0

    def _on_token_usage(self, tab_id, prompt_tokens):
        tab_id = tab_id or self._active_tab_id
        ctx = self._chat_ctxs.get(tab_id)
        if ctx is not None:
            ctx.last_prompt_tokens = prompt_tokens

        def fmt(n):
            return f"{n/1000:.1f}k" if n >= 1000 else str(n)

        if tab_id == self._active_tab_id:
            if not self._paint_meter_numbers(prompt_tokens, getattr(ctx, "ctx_estimated", False)):
                return
            _lc = getattr(ctx, "last_llm_call", None) or {}
            self.token_label.setToolTip(t(
                "chat.tokens_tip", model=_lc.get("model") or "?",
                msgs=_lc.get("n_messages", "?"), tools=_lc.get("n_tools", "?"),
                kb=_lc.get("payload_kb", "?"),
                call=getattr(ctx, "last_tokens_iter", 1) or 1))


    def _paint_token_meter(self):
        if getattr(self, "token_label", None) is None:
            return
        ctx = self._chat_ctxs.get(self._active_tab_id)
        self._paint_meter_numbers(int(getattr(ctx, "last_prompt_tokens", 0) or 0),
                                  getattr(ctx, "ctx_estimated", False))

    def _paint_meter_numbers(self, n, estimated):
        lim = self._context_limit(measured_only=True)
        f = lambda x: f"{x/1000:.1f}k" if x >= 1000 else str(x)
        prefix = "~" if estimated else ""
        if not lim:
            self.token_label.setText(f"{prefix}{f(n)} / ?")
            self.token_label.setStyleSheet("color: #94a3b8; font-weight: 600;")
            self.token_label.setToolTip(t("chat.ctx_unknown_tip", sec=self._active_model_section()))
            return False
        self.token_label.setText(f"{prefix}{f(n)} / {f(lim)}")
        pct = n / lim
        color = "#ef4444" if pct >= 0.9 else "#eab308" if pct >= 0.7 else "#22c55e"
        self.token_label.setStyleSheet(f"color: {color}; font-weight: 600;")
        return True

    def _on_token_first(self, tab_id, prompt_tokens, iteration):
        if iteration and iteration > 1:
            return
        ctx = self._chat_ctxs.get(tab_id or self._active_tab_id)
        if ctx is None or ctx.compacting or prompt_tokens <= 2000:
            return
        if getattr(ctx, "ctx_measured", False):
            return
        ctx.ctx_measured = True
        threshold = self.config.getfloat("CHAT", "compact_threshold",
                                         fallback=0.85)
        lim = int(getattr(ctx, "context_limit", 0) or 0) or (
            self._context_limit(measured_only=True)
            if (tab_id or self._active_tab_id) == self._active_tab_id else 0)
        _reserve = min(self._max_tokens(), lim // 2) if lim else 0
        if lim and ((prompt_tokens + _reserve) / lim) >= threshold:
            ctx.compact_pending = True
            if (tab_id or self._active_tab_id) == self._active_tab_id:
                self._update_queue_label()
        elif not lim:
            ctx.ctx_measured = False


    def send_message(self, _queued=None):
        if isinstance(_queued, (tuple, list)) and len(_queued) == 2:
            msg, pending = _queued
        else:
            _queued = None
            msg = self.entry.text().strip()
            pending = list(self.entry.pending_files)
        if not msg and not pending: return
        if _queued is None and msg:
            self.entry.remember(msg)

        _idx = getattr(self, "_code_page_idx", None)
        if (_idx is not None and getattr(self, "_chat_stack", None) is not None
                and self._chat_stack.currentIndex() == _idx):
            self._chat_stack.setCurrentIndex(0)
            self._code_view_placeholder(False)
            self._render_chat_tabs()

        _kidx = getattr(self, "_council_page_idx", None)
        _on_council = (_kidx is not None
                       and getattr(self, "_chat_stack", None) is not None
                       and self._chat_stack.currentIndex() == _kidx)

        active = self._tab_by_id(self._active_tab_id)
        _in_session = bool(active and active.get("kind") in ("green", "red")
                           and active.get("peer"))

        if not _in_session:
            msg = _canon_cmd(msg)

        if _queued is None and not _in_session and _canon_cmd(msg).startswith("/watch"):
            self._cmd_watch(_canon_cmd(msg))
            self.entry.clear(); self.entry.clear_pending()
            return

        if _queued is None and not _in_session and msg.startswith("/queue"):
            n = len(self._msg_queue)
            _qargs = msg.split()[1:]
            _qm = re.match(r"^(?:del|drop|borrar|quitar)\s+(\d+)$", " ".join(_qargs).strip().lower())
            if "clear" in msg or "vaciar" in msg:
                self._msg_queue.clear()
                _cq = self._chat_ctxs.get(self._active_tab_id)
                if _cq is not None:
                    _cq.compact_pending = False
                    for _m in list(getattr(_cq, "injected_pending", None) or []):
                        if self._withdraw_from_turn(getattr(_cq, "turn_id", None), _m):
                            _cq.injected_pending.remove(_m)
                            n += 1
                self._update_queue_label()
                self.add_message(t("msg.queue_cleared", n=n), "system")
            elif _qm:
                self.entry.clear(); self.entry.clear_pending()
                self._queue_take(int(_qm.group(1)), drop=True)
                return
            elif len(_qargs) == 1 and _qargs[0].isdigit():
                self.entry.clear(); self.entry.clear_pending()
                self._queue_take(int(_qargs[0]))
                return
            else:
                self.add_message(t("msg.queue_status", n=n), "system")
            self.entry.clear(); self.entry.clear_pending()
            return

        _is_cmd = bool(msg) and msg.lstrip().startswith("/") and not pending
        if _queued is None and not _in_session and self._task_running \
                and not _is_cmd:
            _ctx = self._view_ctx()
            if msg and not pending and getattr(_ctx, "turn_id", None):
                if self._inject_into_turn(_ctx.turn_id, msg):
                    self.add_message(t("chat.injected_sent", msg=msg), "system")
                    _ctx.injected_pending.append(msg)
                    self._update_queue_label()
                    self.entry.clear(); self.entry.clear_pending()
                    return
            self._msg_queue.append((msg, list(pending)))
            self._update_queue_label()
            self.entry.clear(); self.entry.clear_pending()
            if not self._task_running:
                self._drain_tab_queue(self._active_tab_id)
            return
        if _in_session and pending:
            self.add_message(t("fed.session_no_files"), "error")
            self.entry.clear(); self.entry.clear_pending()
            return

        display_msg = msg or ""
        if pending:
            names = ", ".join(f[0] for f in pending)
            total_kb = sum(f[3] for f in pending) / 1024
            display_msg = f"📎 {len(pending)} file(s): {names} ({total_kb:.0f}KB)" + (f"\n{msg}" if msg else "")
        self._chat_follow = True
        if _in_session:
            self._append_html_to_tab(active["id"], self._chat_frag(
                display_msg, "#38bdf8", "👤 " + t("label.user")))
        else:
            self.add_message(display_msg, "user")
        if _queued is None:
            self.entry.clear()
            self.entry.clear_pending()

        if _in_session and msg.strip().startswith("/"):
            self._session_command(active, msg)
            return
        if _in_session:
            if active["id"] in self._session_waiting:
                self._session_queue.setdefault(active["id"], []).append(msg)
                self._update_queue_label()
                self.add_message(t("queue.session_queued",
                                   n=len(self._session_queue[active["id"]])), "system")
                return
            self._session_waiting.add(active["id"])
            self._enchant_t0[active["id"]] = time.time()
            if active["id"] == self._active_tab_id:
                self._set_enchanting(True)
            self._tabs_working_check()
            if active.get("kind") == "green":
                self._on_session_send(active["peer"], msg)
            else:
                self._on_session_inject(active["peer"], msg)
            return

        _is_cmd = bool(re.fullmatch(r"[@/][a-z]+", msg)
                       or ((_cm := re.match(r"/([a-z]+)\s", msg))
                           and _cm.group(1) in _GUI_CMDS))
        if not pending:
            if not _is_cmd:
                self.conversation_history.append({"role": "user", "content": msg})
        else:
            images_b64 = []
            text_parts = []

            for name, b64, fmime, _ in pending:
                if fmime.startswith("image/"):
                    images_b64.append((name, b64, fmime))
                elif fmime in _OFFICE_MIMES.values():
                    try:
                        import hoplita_office as _ho
                        import tempfile
                        _ext = next(e for e, m in _OFFICE_MIMES.items() if m == fmime)
                        with tempfile.NamedTemporaryFile(suffix=_ext, delete=False) as tmp:
                            tmp.write(base64.b64decode(b64)); tmp_path = tmp.name
                        try:
                            _otext = _ho.plain_text(tmp_path, 8000)
                        finally:
                            os.unlink(tmp_path)
                    except Exception as _e:
                        _otext = f"(could not read: {_e})"
                    text_parts.append(f"[{name}]\n{_otext}")
                elif fmime == "application/pdf":
                    try:
                        raw = base64.b64decode(b64)
                        import tempfile
                        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                            tmp.write(raw); tmp_path = tmp.name
                        import hoplita_pdf
                        _txt, _err = hoplita_pdf.plain_text(tmp_path, 8000)
                        pdf_text = _txt if not _err else f"(could not extract text: {_err})"
                        os.unlink(tmp_path)
                    except:
                        pdf_text = "(error reading PDF)"
                    text_parts.append(f"[PDF: {name}]\n{pdf_text}")
                else:
                    try:
                        text_parts.append(f"[{name}]\n{base64.b64decode(b64).decode('utf-8', errors='replace')[:8000]}")
                    except:
                        text_parts.append(f"[{name}] (error reading)")

            prompt = msg or t("prompts.analyze_files")
            if text_parts:
                prompt = "\n\n".join(text_parts) + f"\n\n{prompt}"

            if images_b64:
                content = [{"type": "text", "text": prompt}]
                for name, b64, fmime in images_b64:
                    content.append({"type": "image_url",
                                    "image_url": {"url": f"data:{fmime};base64,{b64}"}})
                self.conversation_history.append({"role": "user", "content": content})
            else:
                self.conversation_history.append({"role": "user", "content": prompt})

        _tab = self._active_tab_id
        _bare = re.fullmatch(r"[@/]([a-z]+)", msg.strip())
        if _bare:
            _name = _bare.group(1)
            if _name in _CMD_USAGE:
                self.add_message(_CMD_USAGE[_name](), "system")
                return
            if _name not in _CMD_NO_ARGS:
                _near = difflib.get_close_matches(
                    _name, list(_CMD_USAGE) + list(_CMD_NO_ARGS), 1, 0.6)
                self.add_message(
                    t("cmd.unknown", cmd=_name,
                      hint=(f" {t('cmd.did_you_mean', name=_near[0])}" if _near else "")),
                    "error")
                return

        if msg.startswith("/searchrag "):
            self._run_in_tab(_tab, self.search_with_rag, msg[11:])
        elif msg.startswith("/search "):
            self._run_in_tab(_tab, self.search_with_perplexity, msg[8:])
        elif msg.startswith("/include "):
            self._run_in_tab(_tab, self.pin_file_to_context, msg[9:].strip())
        elif msg.startswith("/includeurls "):
            self._run_in_tab(_tab, self.process_include_file, msg[13:].strip())
        elif msg.startswith("/includeurl "):
            self._run_in_tab(_tab, self.process_single_url, msg[12:].strip())
        elif msg.startswith("/addtext "):
            self._run_in_tab(_tab, self.process_text_file, msg[9:].strip())
        elif msg.startswith("/summary"):
            focus = msg[9:].strip() if len(msg) > 8 and msg[8] == " " else None
            self._run_in_tab(_tab, self.generate_hierarchical_summary, focus)
        elif msg.startswith("/todo"):
            self._run_in_tab(_tab, self.chat_with_todo, msg[5:].strip())
        elif msg.startswith("/fixed "):
            text = msg[7:].strip()
            if text: self.add_user_fixed_context(text)
            else: self.add_message(t("usage.fixed"), "system")
        elif msg.startswith("/verify"):
            self._handle_verify_command(msg[7:].strip())
        elif msg.startswith("/goal"):
            self._handle_goal_command(msg[5:].strip())
        elif msg.startswith("/pin"):
            self._handle_pin_command(msg[4:].strip())
        elif msg.startswith("/why"):
            self._handle_why_command(msg[4:].strip())
        elif msg.strip() == "/context":
            self._run_in_tab(_tab, self._handle_context_command)
        elif msg.strip() == "/compact":
            self._compact_now()
        elif msg.startswith("/notes "):
            self._run_in_tab(_tab, self.load_notes_to_rag, msg[7:].strip())
        elif msg.startswith("/multi "):
            task = msg[7:].strip()
            if task:
                if not self._spawn_turn(self._active_tab_id,
                                         self._handle_multi_command, task):
                    self.add_message(t("queue.turn_busy"), "system")
        elif msg.startswith("/qa"):
            self._handle_qa_command(msg[3:].strip())
        elif msg.startswith("/model"):
            self._handle_model_command(msg[6:].strip())
        elif msg.startswith("/browse "):
            self._run_in_tab(_tab, self._handle_browse_command, msg[8:].strip())
        elif msg.startswith("/skill "):
            self._dispatch_skill(msg[7:].strip())
        elif msg.startswith("/alert "):
            text = msg[7:].strip()
            if text:
                self.signals.show_alert.emit("🔔 Manual Alert", text)
            else:
                self.add_message(t("usage.alert"), "system")
        elif msg.startswith("/creds"):
            self._show_credentials_dialog()
        elif msg == "/help":
            help_text = t("help.commands")
            self.add_message(help_text, "system")
        elif _on_council:
            if not self._spawn_turn(self._active_tab_id,
                                    self._handle_council_convene, msg, True):
                self._msg_queue.append((msg, []))
                self._update_queue_label()
                self.add_message(t("queue.turn_busy"), "system")
        elif self._tab_by_id(self._active_tab_id) and \
                self._tab_by_id(self._active_tab_id).get("kind") == "cron":
            self._spawn_turn(self._active_tab_id, self._cron_supervisor_turn, msg)
        else:
            if not self._spawn_turn(self._active_tab_id, self.call_normal_ai,
                                     msg):
                self._msg_queue.append((msg, []))
                self._update_queue_label()
                self.add_message(t("queue.turn_busy"), "system")

    def _dispatch_skill(self, raw_input):
        default_cat = self.config.get("BROWSER", "default_skill_category", fallback="skills")
        if raw_input.lower().startswith("add ") or raw_input.lower() == "add":
            self._add_skill(raw_input[3:].strip(), default_cat)
            return
        low = raw_input.lower()
        if low == "pinned" or low == "pins":
            self._list_pinned_skills()
            return
        if low.startswith("pin ") or low == "pin":
            self._pin_skill(raw_input[3:].strip(), default_cat)
            return
        if low.startswith("unpin ") or low == "unpin":
            self._unpin_skill(raw_input[5:].strip())
            return
        parts = raw_input.split(" ", 1)
        cats = self.notes_manager.get_categories()
        cats_lower = {c.lower(): c for c in cats}
        if len(parts) >= 2 and parts[0].lower() in cats_lower:
            category = cats_lower[parts[0].lower()]
            search = parts[1]
        else:
            category = default_cat
            search = raw_input
        self._run_in_tab(self._active_tab_id, self._handle_skill,
                         search, category)

    def _add_skill(self, arg, category):
        arg = arg.strip()
        if not arg:
            self.add_message(t("skill.usage"), "error")
            return
        title, content = None, None
        path = os.path.expanduser(arg)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception as e:
                self.add_message(t("skill.read_err", path=path, e=str(e)), "error")
                return
            title = os.path.splitext(os.path.basename(path))[0]
        elif ":" in arg:
            title, content = arg.split(":", 1)
            title = title.strip()
            content = content.strip()
        else:
            if "/" in arg or arg.startswith("~") or arg.lower().endswith(".md"):
                self.add_message(t("skill.not_a_file", path=path), "error")
            else:
                self.add_message(t("skill.usage"), "error")
            return
        if not title or not content:
            self.add_message(t("skill.usage"), "error")
            return
        try:
            self._mcp_call("add_note", {"category": category,
                                        "title": title, "content": content})
            self.add_message(t("skill.added", title=title, cat=category,
                               n=len(content)), "system")
            if hasattr(self, "_load_notes_categories"):
                self._load_notes_categories()
        except Exception as e:
            self.add_message(t("skill.save_err", e=str(e)), "error")

    def _pin_skill(self, search_term, category):
        search_term = search_term.strip()
        if not search_term:
            self.add_message(t("skill.pin_usage"), "error")
            return
        results, _ = self._search_notes_hybrid(search_term, category)
        if not results:
            results, _ = self._search_notes_hybrid(search_term, None)
        if not results or not (isinstance(results[0], dict) and "note" in results[0]):
            self.add_message(t("skill.not_found_pin", term=search_term), "error")
            return
        note = results[0]["note"]
        title = note.get("title", "")
        content = note.get("content", "").strip()
        if not content:
            self.add_message(t("msg.note_empty", title=title), "error")
            return
        if any(s["title"] == title for s in self._pinned_skills):
            self.add_message(t("skill.already_pinned", title=title), "system")
            return
        self._pinned_skills.append({"title": title, "content": content})
        self.add_message(t("skill.pinned", title=title,
                           n=len(self._pinned_skills)), "system")

    def _unpin_skill(self, arg):
        arg = arg.strip()
        if arg.lower() == "all":
            n = len(self._pinned_skills)
            self._pinned_skills = []
            self.add_message(t("skill.unpinned_all", n=n), "system")
            return
        before = len(self._pinned_skills)
        self._pinned_skills = [s for s in self._pinned_skills
                               if s["title"].lower() != arg.lower()]
        if len(self._pinned_skills) < before:
            self.add_message(t("skill.unpinned", title=arg), "system")
        else:
            self.add_message(t("skill.not_pinned", title=arg), "error")

    def _list_pinned_skills(self):
        if not self._pinned_skills:
            self.add_message(t("skill.none_pinned"), "system")
            return
        lines = [t("skill.pinned_header", n=len(self._pinned_skills))]
        for s in self._pinned_skills:
            lines.append(f"  📌 {s['title']} ({len(s['content'])} chars)")
        self.add_message("\n".join(lines), "system")




    def clean_ai_response(self, text):
        if isinstance(text, list):
            import fed_agent
            text = fed_agent.flatten_parts({"content": text})["content"]
        if not text: return text
        cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
        while "<think>" in cleaned.lower() or "</think>" in cleaned.lower():
            cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
            cleaned = re.sub(r"<think>.*$", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
            cleaned = re.sub(r"^.*?</think>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"\n\n+", "\n\n", cleaned)
        return cleaned.strip()


    def add_user_fixed_context(self, text, auto=False):
        self.user_fixed_context.append({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content": text, "word_count": len(text.split()),
            "char_count": len(text), "is_user_fixed": not auto,
            "auto": bool(auto),
        })
        prev = " ".join(text.split())
        if len(prev) > 300:
            prev = prev[:300] + f"… (+{len(text) - 300:,} chars)"
        self.thread_msg(
            t("fixed.auto_added" if auto else "fixed.added",
              n=len(self.user_fixed_context), text=prev), "fixed")
        self.signals.update_rag_status.emit()

    def view_fixed_contexts(self):
        sys_count = len(self.fixed_context); usr_count = len(self.user_fixed_context)
        if sys_count == 0 and usr_count == 0: self.add_message(t("fixed.none"), "system"); return
        parts = ["=== FIXED CONTEXTS ===\n"]
        if sys_count:
            parts.append(f"\n📋 SYSTEM ({sys_count}):")
            for i, ctx in enumerate(self.fixed_context, 1): parts.append(f"\n{i}. [{ctx.get('type','').upper()}] {ctx.get('content','')[:200]}...")
        if usr_count:
            _auto_n = sum(1 for c in self.user_fixed_context if c.get("auto"))
            parts.append(f"\n📌 USER /fixed ({usr_count - _auto_n}"
                         + (f" + {_auto_n} 🤖" if _auto_n else "") + "):")
            for i, ctx in enumerate(self.user_fixed_context, 1):
                _who = "🤖 " if ctx.get("auto") else ""
                parts.append(f"\n{i}. {_who}({ctx['timestamp']}) "
                             f"{ctx.get('content','')[:200]}...")
        parts.append(f"\n=== TOTAL: {sys_count + usr_count} ===")
        parts.append("\n" + t("fixed.how_to_clear"))
        self.add_message("\n".join(parts), "fixed")

    def clear_user_fixed_context(self):
        n = len(self.user_fixed_context); self.user_fixed_context = []
        self.add_message(t("fixed.cleared", n=n), "system"); self.update_rag_status()

    def clear_all_fixed_context(self):
        n_sys = len(self.fixed_context)
        n_usr = len(self.user_fixed_context)
        if not n_sys and not n_usr:
            self.add_message(t("fixed.none"), "system")
            return
        self.fixed_context = []
        self.user_fixed_context = []
        self.add_message(t("fixed.all_cleared", sys=n_sys, usr=n_usr), "system")
        self.update_rag_status()

    _BRIEF_FIXED = "[FIXED CONTEXT]:"
    _BRIEF_STICKY = "[STICKY NOTE]:"

    def _persist_briefing_blocks(self, context_parts):
        hist = self.conversation_history
        sticky = [p_ for p_ in (context_parts or []) if "[STICKY NOTE" in p_[:40]]
        pins = [p_ for p_ in (context_parts or []) if "[STICKY NOTE" not in p_[:40]]
        blocks = []
        if sticky:
            blocks.append(self._BRIEF_STICKY + "\n" + "\n\n".join(sticky))
        if pins:
            blocks.append(self._BRIEF_FIXED + "\n\n" + "\n\n".join(pins))
        if not blocks:
            return
        import fed_agent as _fa_tag
        _tag = _fa_tag.HARNESS_TAG + "\n"
        _LEGACY_TAIL = "\n\nDo not use <think> tags."

        def _bare(content):
            c_ = str(content or "")
            c_ = c_[len(_tag):] if c_.startswith(_tag) else c_
            return c_[:-len(_LEGACY_TAIL)] if c_.endswith(_LEGACY_TAIL) else c_

        def _is_brief(content, markers=(self._BRIEF_FIXED, self._BRIEF_STICKY)):
            return _bare(content).startswith(markers)
        _at = next((i for i in range(len(hist) - 1, -1, -1)
                    if hist[i].get("role") == "user"
                    and not _is_brief(hist[i].get("content", ""))), len(hist))
        for block in blocks:
            marker = (self._BRIEF_STICKY if block.startswith(self._BRIEF_STICKY)
                      else self._BRIEF_FIXED)
            last = next((m for m in reversed(hist[:_at])
                         if m.get("role") == "user"
                         and _is_brief(m.get("content", ""), (marker,))), None)
            if last is not None and _bare(last.get("content")) == block:
                continue
            hist.insert(_at, {"role": "user", "content": _tag + block})
            _at += 1

    def get_all_fixed_context_text(self):
        parts = []
        if self.fixed_context:
            parts.append("=== SYSTEM FIXED CONTEXT ===\n")
            for ctx in self.fixed_context:
                if ctx.get("is_fixed"):
                    h = f"\n[{ctx['type'].upper()}]"
                    if ctx.get("focus") and ctx["focus"] != "general": h += f" - {ctx['focus']}"
                    elif ctx.get("query"): h += f" - {ctx['query']}"
                    parts.append(h + "\n" + ctx["content"])
        if self.user_fixed_context:
            parts.append("\n\n=== USER FIXED CONTEXT (@fixed) ===\n")
            for i, ctx in enumerate(self.user_fixed_context, 1):
                parts.append(f"\n[@fixed {i}] ({ctx['timestamp']})\n{ctx['content']}")
        if parts: parts.append("\n\n=== END FIXED CONTEXTS ===\n")
        return "\n".join(parts) if parts else ""


    def show_rag_stats(self):
        stats = self.rag_manager.get_stats()
        api_url, _, api_model = self._resolve_endpoint("API")
        chat_mm = t("rag.stats_multimodal") if self._chat_can_see_images() else t("rag.stats_textonly")
        msg = t("rag.stats",
                platform=platform.system(),
                chunks=stats['total_chunks'],
                sources=stats['sources'],
                used=stats['used_chunks'],
                available=stats['available_chunks'],
                raw_sources=stats['raw_texts']['total_sources'],
                raw_words=stats['raw_texts']['total_raw_words'],
                fixed_sys=len(self.fixed_context),
                fixed_usr=len(self.user_fixed_context),
                api_model=api_model, api_url=api_url, chat_mm=chat_mm,
                chunk=self.config.getint('RAG','chunk_size',fallback=200),
                k=self.config.getint('RAG','search_k',fallback=5),
                headless=self.config.getboolean('BROWSER','headless',fallback=True))
        self.add_message(msg, "rag")

    def reset_used_chunks(self):
        before = len(self.rag_manager.used_chunk_ids); self.rag_manager.reset_used_chunks()
        self.add_message(t("rag.chunks_reset", before=before), "system"); self.update_rag_status()

    def clear_rag_index(self):
        self.rag_manager.clear_index()
        self.add_message(t("rag.cleared"), "rag"); self.update_rag_status()

    def clear_chat(self, confirmed=False):
        if not confirmed and getattr(self, "chat_display", None) is not None:
            _n = len(getattr(self, "conversation_history", []) or [])
            if _n and QMessageBox.question(
                    self, t("chat.clear_confirm_title"),
                    t("chat.clear_confirm_body", n=_n),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        self._extract_pending(reason="clear")
        try:
            _c = self._view_ctx()
            _ids = [i for i in (getattr(_c, "session_uuid", "") or "",
                                getattr(_c, "sync_id", "") or "",
                                getattr(_c, "tab_id", "") or "") if i]
            if _ids:
                self._bg(lambda: self._mcp_call("working_memory_forget",
                                                {"sessions": _ids}))
        except Exception:
            pass
        n_sys = len(self.fixed_context)
        had_proj = self._code_close_project(silent=True) \
            if hasattr(self, "_code_project") else False
        self.chat_display.clear(); self.conversation_history = []
        self.fixed_context = []
        self.clear_user_fixed_context()
        self._last_extracted_idx = 0
        _ctx = self._turn_ctx()
        if _ctx is not None:
            _ctx.agents = []
            _ctx.intent = []
            if getattr(self, "agents_panel", None) is not None:
                self.agents_panel.update_state(self._agents_cards_for_active())
            self.signals.intent_changed.emit(self._active_tab_id or "")
            _ctx.council_html = ""
            _ctx.council_events = []
            if getattr(self, "council_web", None) is not None:
                self.council_web.load([])
            _ctx.council_ack = None
            _ctx.sticky = ""
            self._sticky_paint(self._active_tab_id)
            if self._active_tab_id == getattr(_ctx, "tab_id", None) \
                    or _ctx is self._view_ctx():
                if getattr(self, "council_view", None) is not None:
                    self._council_welcome()
                if getattr(self, "_council_gate_bar", None) is not None:
                    self._council_show_gate(None)
                self._render_chat_tabs()
        if getattr(self, "_chat_stack", None) is not None:
            self._chat_stack.setCurrentIndex(0)
            if hasattr(self, "_sync_input_placeholder"):
                self._sync_input_placeholder()
        self.add_message(t("msg.chat_cleared"), "system")
        if n_sys:
            self.add_message(t("fixed.sys_cleared", n=n_sys), "system")
        if had_proj:
            self.add_message(t("code.closed_msg"), "system")
            if getattr(self, "_chat_stack", None) is not None:
                self._code_view_placeholder(False)
        self.update_rag_status()

    def _on_turn_complete(self):
        nuevos = len(self.conversation_history) - self._last_extracted_idx
        if nuevos >= self.EXTRACT_EVERY_N:
            self._extract_pending(reason="incremental")

    def _extract_pending(self, reason="incremental"):
        if getattr(self._turn_ctx(), "no_log", False):
            self._last_extracted_idx = len(self.conversation_history)
            return
        segment = self.conversation_history[self._last_extracted_idx:]
        if not any(m.get("role") == "user" for m in segment):
            return
        self._last_extracted_idx = len(self.conversation_history)
        self._auto_extract_facts_async(segment)
        self._auto_extract_lessons_async(segment)

    _LOOP_USER_PREFIXES = ("[QUALITY REVIEW FAILED]", "[DEFINITION OF DONE",
                           "[NOT AN ANSWER]", "[INSTRUCTION]", "[PRE-FLIGHT",
                           "[PAUSE", "[WHILE YOU WORK", "[BEFORE YOU ANSWER",
                           "[YOUR OWN LIST", "[FIXED CONTEXT]", "[STICKY NOTE]")

    def _history_transcript(self, ctx):
        if not self.config.getboolean("CHAT", "history_transcript",
                                      fallback=True):
            return []
        return [m for m in (getattr(ctx, "turn_transcript", None) or [])
                if isinstance(m, dict) and m.get("role")]

    @staticmethod
    def _flatten_pictures(history, every_list=False):
        out = []
        for m in (history or []):
            c = m.get("content") if isinstance(m, dict) else None
            if isinstance(c, list):
                n = sum(1 for p_ in c if isinstance(p_, dict) and "image" in str(p_.get("type", "")))
                if n or every_list:
                    txt = " ".join(str(p_.get("text", "")) for p_ in c
                                   if isinstance(p_, dict) and p_.get("type") == "text").strip()
                    m = {**m, "content": (txt + (f"\n[{n} picture(s) shown to the model]" if n else "")).strip()}
            out.append(m)
        return out

    def _store_question_as_sent(self, ctx):
        sent = getattr(ctx, "turn_last_user", None)
        if sent is None:
            return
        hist = self.conversation_history
        for i in range(len(hist) - 1, -1, -1):
            if hist[i].get("role") == "user":
                if hist[i].get("content") != sent:
                    hist[i] = {**hist[i], "content": sent}
                break
        ctx.turn_last_user = None

    def _history_for_memory(self, history):
        keep = []
        for m in (history or []):
            role = m.get("role")
            c = m.get("content")
            if role == "user" and isinstance(c, str) \
                    and not c.lstrip().startswith(self._LOOP_USER_PREFIXES):
                import fed_agent as _fa_said
                said = _fa_said.user_said(m)
                if not said:
                    continue
                if said != c.strip():
                    m = {**m, "content": said}
                keep.append(m)
            elif role == "assistant" and isinstance(c, str) and c.strip() \
                    and not m.get("tool_calls"):
                keep.append(m)
        return keep

    def _auto_extract_lessons_async(self, history):
        history = self._history_for_memory(history)
        user_turns = [m for m in history if m.get("role") == "user"]
        if len(user_turns) < 1:
            return
        tab_id = getattr(self._tls, "tab_id", None) or self._active_tab_id
        self._run_in_tab(tab_id, self._extract_lessons_worker, history)

    def _extract_lessons_worker(self, history):
        self._distill_memory(history, ["lessons"])

    def _auto_extract_facts_async(self, history):
        history = self._history_for_memory(history)
        user_turns = [m for m in history if m.get("role") == "user"]
        if len(user_turns) < 1:
            return
        tab_id = getattr(self._tls, "tab_id", None) or self._active_tab_id
        self._run_in_tab(tab_id, self._extract_facts_worker, history)

    def _extract_facts_worker(self, history):
        self._distill_memory(history, ["facts"])

    def _distill_memory(self, history, kinds):
        try:
            r = requests.post(
                f"{self._mcp_url}/memory/distill",
                json={"history": [m for m in history
                                  if m.get("role") in ("user", "assistant")],
                      "kinds": kinds},
                timeout=self.config.getint("CHAT", "timeout", fallback=120) + 120)
            if r.status_code != 200:
                return
            got = r.json()
            n = sum(int(got.get(k, 0) or 0) for k in kinds)
            if n:
                self.thread_msg(t("msg.memory_distilled", n=n), "system")
        except Exception as e:
            print(f"[DISTILL] {e}", file=sys.stderr)

    def _write_config(self, new_content):
        config_path = self._get_config_path()
        import hoplita_llmcfg
        _bad = hoplita_llmcfg.ini_problem(new_content)
        if _bad:
            QMessageBox.critical(self, t("cfg.unreadable_title"), t("cfg.unreadable_body", why=_bad))
            return False
        try:
            import shutil
            shutil.copy(config_path, config_path + ".bak")
        except Exception:
            pass
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(new_content)
        except Exception as e:
            self.add_message(t("err.saving", e=str(e)), "error")
            return False
        self.reload_config(push_to_mcp=True)
        return True

    def _open_usage_dialog(self):
        try:
            from config_editor import UsageWidget
            dlg = QDialog(self)
            dlg.setWindowTitle(t("cfg.tab_usage"))
            dlg.resize(760, 460)
            lay = QVBoxLayout(dlg)
            lay.addWidget(UsageWidget(t, self._mcp_url))
            dlg.exec()
        except Exception as e:
            QMessageBox.warning(self, t("dlg.error"), str(e))

    def open_config_editor(self):
        try:
            from config_editor import ConfigEditorDialog
            ConfigEditorDialog(self, self._get_config_path(), t,
                               self._write_config).exec()
            return
        except Exception as e:
            print(f"[CONFIG] form editor unavailable ({e}) → raw editor",
                  file=sys.stderr)

        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPlainTextEdit,
                                      QPushButton, QLabel)
        from PyQt6.QtGui import QFont, QShortcut, QKeySequence
        from PyQt6.QtCore import Qt

        config_path = self._get_config_path()
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            self.add_message(t("err.reading_config", path=config_path, e=str(e)), "error")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(t("dlg.edit_config"))
        dlg.resize(900, 650)
        dlg.setModal(True)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(12, 12, 12, 12)

        info = QLabel(
            f"📁 <b>{config_path}</b><br>"
            "Saving (Ctrl+S) applies changes in Qt and sends a reload to the MCP server. "
            "<b>[MCP_SERVERS]</b> requires restarting the MCP."
        )
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setStyleSheet("color: #e6edf7; font-size: 12px; padding: 4px;")
        info.setWordWrap(True)
        layout.addWidget(info)

        edit = QPlainTextEdit()
        edit.setPlainText(content)
        mono_family = "Consolas" if platform.system() == "Windows" else "DejaVu Sans Mono"
        font = QFont(mono_family, 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        edit.setFont(font)
        edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        edit.setTabStopDistance(32)
        layout.addWidget(edit, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel_btn = QPushButton(t("btn.cancel"))
        save_btn = QPushButton(t("ui.save_reload"))
        save_btn.setDefault(True)
        btns.addWidget(cancel_btn)
        btns.addWidget(save_btn)
        layout.addLayout(btns)

        def do_save():
            if self._write_config(edit.toPlainText()):
                dlg.accept()

        save_btn.clicked.connect(do_save)
        cancel_btn.clicked.connect(dlg.reject)

        QShortcut(QKeySequence("Ctrl+S"), dlg).activated.connect(do_save)
        QShortcut(QKeySequence("Escape"), dlg).activated.connect(dlg.reject)

        dlg.exec()

    def _inject_into_turn(self, turn_id, msg, kind="user"):
        try:
            r = requests.post(f"{self._tab_url()}/agent/inject", timeout=15,
                              json={"turn_id": turn_id, "message": msg,
                                    "kind": kind})
            return bool(r.ok and r.json().get("injected"))
        except Exception as e:
            print(f"[INJECT] {e}", file=sys.stderr)
            return False

    def _withdraw_from_turn(self, turn_id, msg):
        if not turn_id:
            return False
        try:
            r = requests.post(f"{self._tab_url()}/agent/inject", timeout=5,
                              json={"turn_id": turn_id, "message": msg,
                                    "withdraw": True})
            return bool(r.ok and r.json().get("withdrawn"))
        except Exception as e:
            print(f"[INJECT] {e}", file=sys.stderr)
            return False

    def _watch_config(self):
        try:
            from PyQt6.QtCore import QFileSystemWatcher
            path = self._get_config_path()
            self._cfg_watcher = QFileSystemWatcher([path], self)
            self._cfg_watcher.fileChanged.connect(self._on_config_file_changed)
        except Exception as e:
            print(f"[CONFIG] not watching config.ini: {e}", file=sys.stderr)

    def _on_config_file_changed(self, path):
        from PyQt6.QtCore import QTimer

        def _reload():
            try:
                if os.path.exists(path) and path not in self._cfg_watcher.files():
                    self._cfg_watcher.addPath(path)
                import hashlib as _hl
                try:
                    _sig = _hl.sha1(open(path, "rb").read()).hexdigest()
                except OSError:
                    _sig = ""
                if _sig and _sig == getattr(self, "_cfg_sig", None):
                    return
                self._cfg_sig = _sig
                self.reload_config(push_to_mcp=True)
                self.add_message(t("cfg.reloaded_from_disk"), "system")
            except Exception as e:
                print(f"[CONFIG] reload failed: {e}", file=sys.stderr)
        QTimer.singleShot(400, _reload)

    def reload_config(self, push_to_mcp=False):
        old_mcp_url = getattr(self, "_mcp_url", None)
        self.config = self.load_config()
        self.rag_manager.config = self.config
        self._ctx_limit_cache = None

        new_mcp_url = self.config.get("MCP", "url",
                                       fallback="http://127.0.0.1:9077").rstrip("/")
        if new_mcp_url != old_mcp_url:
            self._mcp_url = new_mcp_url
            self.todo_manager = MCPManagerProxy(new_mcp_url, "todo")
            self.notes_manager = MCPManagerProxy(new_mcp_url, "notes")
            self.cron_manager = MCPManagerProxy(new_mcp_url, "cron")
            self.cred_manager = MCPManagerProxy(new_mcp_url, "cred")
            self.add_message(t("msg.mcp_url_changed", old=old_mcp_url, new=new_mcp_url), "system")

        self.browser_headless = self.config.getboolean("BROWSER", "headless", fallback=False)
        self._update_headless_button()
        model = self._base_model_name()
        self.api_button.setToolTip(t("ui.api_button_tip", model=model))
        max_iter = self.config.getint("CHAT", "agentic_max_iterations", fallback=5)
        self.add_message(
            t("msg.config_reloaded", model=model, max_iter=max_iter), "system")

        if push_to_mcp:
            threading.Thread(target=self._push_reload_to_mcp, daemon=True).start()

    def _push_reload_to_mcp(self):
        mcp_url = self.config.get("MCP", "url",
                                   fallback="http://127.0.0.1:9077").rstrip("/")
        try:
            r = requests.post(f"{mcp_url}/reload-config", timeout=5)
            if r.status_code != 200:
                self.thread_msg(
                    t("msg.reload_http", code=r.status_code), "system")
                return
            data = r.json()
            if "error" in data:
                self.thread_msg(t("msg.mcp_reload_fail", e=data["error"]), "system")
                return
            api_model = data.get("api_model", "?")
            self.thread_msg(t("msg.mcp_reloaded", model=api_model), "system")
            for w in (data.get("warnings") or []):
                self.thread_msg(f"⚠ {w}", "system")
        except requests.exceptions.Timeout:
            self.thread_msg(t("msg.reload_timeout"), "system")
        except Exception as e:
            self.thread_msg(t("msg.mcp_no_reload", e=e), "system")


    def _scrape_url(self, url):
        res = self._mcp_call_json("scrape_url",
                                 {"url": url, "max_chars": 400000,
                                  "escalate": False}, timeout=240)
        if not isinstance(res, dict) or not res.get("text"):
            why = "; ".join((res or {}).get("tried", [])) or (res or {}).get("error", "?")
            raise Exception(f"{url}: {why}")
        return f"URL: {url}\nCONTENT:\n{res['text']}"

    def _handle_browse_command(self, instruction: str):
        try:
            self.thread_msg(t("msg.nav_starting"), "browser")
            headless = self.config.getboolean("BROWSER", "headless", fallback=False)
            _b, _k, _m = self._chat_base_endpoint()
            raw = self._mcp_call("browse_web", {
                "task": instruction, "url": "", "headless": headless,
                "endpoint": ({"base_url": _b, "api_key": _k, "model": _m}
                             if _m else None)})
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
                result = data.get("result", raw) if isinstance(data, dict) else raw
            except Exception:
                result = raw
            if not isinstance(result, str):
                result = json.dumps(result, ensure_ascii=False, default=str)
            if result:
                self.thread_msg(t("browser.nav_done", result=result), "browser")
            else:
                self.thread_msg(t("msg.nav_no_result"), "error")
            self.conversation_history.append({
                "role": "user",
                "content": t("msg.nav_result", task=instruction[:100], result=result[:3000])
            })
            self.fixed_context.append({
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "type": "browser_navigation", "focus": instruction[:100],
                "content": result[:5000], "is_fixed": True, "priority": "high",
            })
            self.signals.update_rag_status.emit()
        except Exception as e:
            self.thread_msg(t("msg.error", e=str(e)), "error")

    def _handle_skill(self, search_term: str, category: str = "skills"):
        self.thread_msg(t("msg.searching_skill", term=search_term, cat=category), "system")

        results, stype = self._search_notes_hybrid(search_term, category)
        if not results:
            self.thread_msg(t("msg.skill_not_in_cat", cat=category), "system")
            results, stype = self._search_notes_hybrid(search_term, None)
        if not results:
            cats = self.notes_manager.get_categories()
            self.thread_msg(t("msg.skill_not_found2", term=search_term, cats=", ".join(cats)), "error")
            return

        if isinstance(results[0], dict) and 'note' in results[0]:
            note = results[0]['note']
            note_cat = results[0].get('category', category)
            score = results[0].get('score', 0)
        else:
            self.thread_msg(t("msg.bad_format_results"), "error")
            return

        skill_text = note.get('content', '').strip()
        skill_title = note.get('title', '')

        if not skill_text:
            self.thread_msg(t("msg.note_empty", title=skill_title), "error")
            return

        self.thread_msg(t("msg.skill_found", cat=note_cat, title=skill_title, score=score), "system")
        self.thread_msg(t("msg.executing_skill", text=skill_text[:500] + ("..." if len(skill_text) > 500 else "")), "system")

        self.conversation_history.append({"role": "user", "content": skill_text})
        enhanced = self.conversation_history.copy()
        response = self.call_chat_with_tools(enhanced)
        if response:
            clean = self.clean_ai_response(response)
            self.conversation_history.append({"role": "assistant", "content": clean})
            self.thread_msg(clean, "assistant")
            self.fixed_context.append({
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "type": "prompt_execution", "focus": skill_title,
                "content": f"Skill: {skill_title}\nResult:\n{clean[:5000]}",
                "is_fixed": True, "priority": "high",
            })



    def _post_chat_completions(self, base_url, headers, payload, timeout, stream=False):
        try:
            import hoplita_version as _hv
            headers.setdefault("User-Agent",
                               f"HOPLITA/{_hv.GENERATION} (build {_hv.BUILD})")
        except Exception:
            pass
        if isinstance(payload.get("messages"), list):
            payload["messages"] = [{k: v for k, v in m.items() if k != "ts"}
                                   if isinstance(m, dict) else m for m in payload["messages"]]
        model = payload.get("model", "?")
        n_tools = len(payload.get("tools") or [])
        n_msgs = len(payload.get("messages") or [])
        try:
            payload_kb = len(json.dumps(payload)) // 1024
        except Exception:
            payload_kb = -1
        self.thread_msg(
            f"⏳ POST {base_url}/chat/completions  "
            f"model={model} msgs={n_msgs} tools={n_tools} payload≈{payload_kb}KB",
            "system")
        t0 = time.time()
        _alive = [True]
        _wtab = getattr(self._tls, "tab_id", None)
        def _heartbeat():
            self._tls.tab_id = _wtab
            n = 0
            while _alive[0]:
                time.sleep(30)
                if _alive[0]:
                    n += 1
                    self.thread_msg(
                        t("msg.still_waiting", s=n*30), "system")
        threading.Thread(target=_heartbeat, daemon=True).start()
        try:
            response = requests.post(f"{base_url}/chat/completions",
                                      json=payload, headers=headers,
                                      timeout=(5, timeout), stream=stream)
        except requests.exceptions.ConnectTimeout:
            _alive[0] = False
            self.thread_msg(
                t("msg.llm_no_conn", url=base_url),
                "error")
            raise Exception(f"LLM connect timeout")
        except requests.exceptions.ReadTimeout:
            _alive[0] = False
            self.thread_msg(
                t("msg.llm_no_resp", timeout=timeout),
                "error")
            raise Exception(f"LLM read timeout {timeout}s")
        except requests.exceptions.ConnectionError as e:
            _alive[0] = False
            self.thread_msg(
                t("msg.llm_no_conn2", url=base_url, e=e),
                "error")
            raise Exception(f"LLM connection error: {e}")
        except requests.exceptions.RequestException as e:
            _alive[0] = False
            self.thread_msg(t("msg.llm_net_err", e=e), "error")
            raise
        _alive[0] = False
        dt = time.time() - t0
        if dt > 10:
            self.thread_msg(t("msg.llm_slow", dt=f"{dt:.1f}"), "system")
        else:
            self.thread_msg(t("msg.llm_responded", dt=f"{dt:.1f}"), "system")
        if response.status_code != 200:
            err_body = response.text[:500]
            self.thread_msg(f"❌ LLM HTTP {response.status_code}: {err_body}", "error")
            raise Exception(f"HTTP {response.status_code}: {err_body}")
        if not stream:
            try:
                _body = response.json()
                _usage = _body.get("usage") or {}
                if _usage.get("prompt_tokens") or _usage.get("completion_tokens"):
                    _mdl = _body.get("model") or payload.get("model", "?")
                    threading.Thread(
                        target=lambda: requests.post(
                            f"{self._mcp_url}/stats/tokens/add",
                            json={"model": _mdl,
                                  "prompt_tokens": int(_usage.get("prompt_tokens") or 0),
                                  "completion_tokens": int(_usage.get("completion_tokens") or 0)},
                            timeout=5),
                        daemon=True).start()
            except Exception:
                pass
        return response

    def call_chat(self, messages, stream=True):
        base_url, api_key, model = self._chat_base_endpoint()
        timeout = self.config.getint("CHAT", "timeout", fallback=120)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {"model": model, "messages": messages,
                   "temperature": 0.7, "max_tokens": self._max_tokens(), "stream": stream}
        self._merge_extra_body(payload, base_url, model)
        response = self._post_chat_completions(base_url, headers, payload, timeout, stream=stream)
        if not stream:
            return self.clean_ai_response(response.json()["choices"][0]["message"]["content"])
        _tab = getattr(self._tls, "tab_id", "") or ""
        self._stream_start(_tab)
        full = []
        for line in response.iter_lines():
            if not line:
                continue
            line = line.decode("utf-8") if isinstance(line, bytes) else line
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data.strip() == "[DONE]":
                break
            try:
                chunk = json.loads(data)
                tok = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                if isinstance(tok, list):
                    import fed_agent
                    tok = fed_agent.flatten_parts({"content": tok})["content"]
                if tok:
                    full.append(tok)
                    self.signals.stream_token.emit(tok, _tab)
            except Exception:
                continue
        self.signals.stream_done.emit(_tab)
        return self.clean_ai_response("".join(full))

    def call_chat_summary(self, prompt):
        base_url, api_key, model = self._chat_base_endpoint()
        timeout = self.config.getint("CHAT", "timeout", fallback=120)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        messages = [
            {"role": "system", "content": t("prompts.synthesis_expert")},
            {"role": "user", "content": prompt},
        ]
        payload = {"model": model, "messages": messages,
                   "temperature": 0.3, "max_tokens": self._max_tokens()}
        response = self._post_chat_completions(base_url, headers, payload, timeout)
        return self.clean_ai_response(response.json()["choices"][0]["message"]["content"])

    def _parse_tool_arguments(self, raw):
        if isinstance(raw, dict):
            return raw
        if not isinstance(raw, str):
            return {}
        raw = raw.strip()
        if not raw:
            return {}
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else {}
        except (json.JSONDecodeError, TypeError):
            pass
        try:
            v = json.loads(raw.replace("\r\n", "\\n").replace("\n", "\\n").replace("\t", "\\t"))
            return v if isinstance(v, dict) else {}
        except (json.JSONDecodeError, TypeError):
            pass
        try:
            import re as _re
            m = _re.search(r'"(\w+)"\s*:\s*"(.*)"\s*}?\s*$', raw, _re.DOTALL)
            if m:
                key, val = m.group(1), m.group(2)
                if "\\" in val:
                    try:
                        val = val.encode().decode("unicode_escape", errors="ignore")
                    except Exception:
                        pass
                return {key: val}
        except Exception:
            pass
        return {}

    def execute_tool(self, tool_name, arguments):
        if not isinstance(arguments, dict):
            arguments = self._parse_tool_arguments(arguments)
        try:
            _tab_fw = getattr(self._turn_ctx(), "blocked_tools", None) or ()
        except Exception:
            _tab_fw = ()
        if (tool_name in getattr(self, "blocked_tools", ())
                or tool_name in _tab_fw):
            self.thread_msg(t("tools.blocked_run", tool=tool_name), "error")
            return {"error": f"🛡 FIREWALL: '{tool_name}' is blocked"}
        if tool_name == "sticky_note":
            _txt = str((arguments or {}).get("text", "") or "").strip()
            _lines = [l for l in _txt.split("\n")]
            if len(_lines) > self._STICKY_MAX_LINES:
                return {"error": f"{len(_lines)} lines: a sticky note holds "
                                 f"{self._STICKY_MAX_LINES}. What does not fit "
                                 f"on it has to be distilled — that is what it "
                                 f"is for. Rewrite it shorter.",
                        "lines": len(_lines)}
            self.sticky = _txt
            self.signals.sticky_changed.emit(
                getattr(self._tls, "tab_id", None) or self._active_tab_id)
            return {"ok": True, "lines": len(_lines) if _txt else 0,
                    "note": _txt or "(cleared)"}
        if tool_name == "browse_web":
            original = (getattr(self, "_current_instruction", "") or "").strip()
            if not original:
                for msg in reversed(self.conversation_history):
                    if msg.get("role") == "user":
                        c = msg.get("content", "")
                        if isinstance(c, str):
                            original = c.strip()
                        break
            tarea = (arguments.get("task") or "").strip()
            if original and original not in tarea:
                arguments["task"] = (
                    (tarea or original)
                    + "\n\n" + t("tools.browse_verbatim", text=original))
        if tool_name == "run_command" and self.terminal is not None:
            cmd = arguments.get("command", "")
            timeout = arguments.get("timeout") or self.config.getint(
                "CHAT", "terminal_timeout", fallback=300)
            args_preview = cmd[:120]
            self.thread_msg(t("msg.terminal_call", args=args_preview), "system")
            t0 = time.time()
            result = self.terminal.execute_for_llm(cmd, timeout=timeout)
            dur = round(time.time() - t0, 1)
            result_str = json.dumps(result, ensure_ascii=False)
            preview = result_str[:200]
            marker = "❌" if result.get("exit_code", 0) != 0 else "✅"
            self.thread_msg(t("msg.terminal_result", marker=marker, dur=dur, preview=preview), "system")
            return result_str
        if tool_name in ("render_html", "dashboard_render") and self.dashboard is not None:
            html = arguments.get("html", "")
            if not html.strip():
                return json.dumps({"error": t("err.html_empty")}, ensure_ascii=False)
            _tab = self._emitting_tab()
            _rec = self._canvas(_tab)
            if arguments.get("append") and _rec["html"]:
                html = _rec["html"] + html
            self._render_canvas_html(html)
            self.thread_msg(t("msg.canvas_rendered", n=len(html)), "system")
            _out = {"rendered": True, "chars": len(html)}
            _back = _rec["prev"]
            if _back:
                _out["previous_kept"] = len(_back)
                _out["undo"] = ("The page this replaced is kept: "
                                "canvas_undo() brings it back. If what you "
                                "just rendered is worse than what was there, "
                                "go back instead of rebuilding it.")
            try:
                _msgs = self.dashboard.console_report(key=_tab)
                _errs = [m for m in _msgs if m.get("level") == "error"]
                if _errs:
                    _out["js_errors"] = _errs[:8]
                    _out["hint"] = ("The page rendered but its JavaScript "
                                    "FAILED. Fix these and render again; "
                                    "canvas_eval inspects the live page "
                                    "without re-rendering, and canvas_see "
                                    "shows you what it looks like.")
                _warn = [m for m in _msgs if m.get("level") == "warning"]
                if _warn:
                    _out["js_warnings"] = _warn[:4]
            except Exception as _e:
                _out["console_unavailable"] = str(_e)[:120]
            return json.dumps(_out, ensure_ascii=False)
        if tool_name == "canvas_undo" and self.dashboard is not None:
            _rec = self._canvas(self._emitting_tab())
            _back = _rec["prev"]
            if not _back:
                return json.dumps({"error": "nothing to go back to: only one "
                                            "page has been rendered"},
                                  ensure_ascii=False)
            _now = _rec["html"]
            self._render_canvas_html(_back)
            _rec["prev"] = _now
            self.thread_msg(t("msg.canvas_undone", n=len(_back)), "system")
            return json.dumps({"restored": True, "chars": len(_back)},
                              ensure_ascii=False)
        if tool_name == "canvas_see" and self.dashboard is not None:
            _out = {}
            _tab = self._emitting_tab()
            try:
                _msgs2 = self.dashboard.console_report(300, key=_tab)
                _errs2 = [m for m in _msgs2 if m.get("level") == "error"]
                if _errs2:
                    _out["js_errors"] = _errs2[:8]
            except Exception:
                pass
            if not self._chat_can_see_images():
                _out["error"] = t("err.canvas_no_vision")
                return json.dumps(_out, ensure_ascii=False)
            _png = self.dashboard.screenshot_png(
                max_w=int(arguments.get("max_width") or 1024), key=_tab)
            if not _png:
                _out["error"] = t("err.canvas_shot_failed")
                return json.dumps(_out, ensure_ascii=False)
            import base64 as _b64mod
            _out["screenshot_b64"] = _b64mod.b64encode(_png).decode()
            _out["screenshot_mime"] = "image/png"
            self.thread_msg(t("msg.canvas_seen", kb=max(1, len(_png) // 1024)),
                            "system")
            return json.dumps(_out, ensure_ascii=False)
        if tool_name == "canvas_eval" and self.dashboard is not None:
            _script = arguments.get("js", "")
            if not str(_script).strip():
                return json.dumps({"error": t("err.js_empty")},
                                  ensure_ascii=False)
            _tab = self._emitting_tab()
            _r = self.dashboard.eval_js(str(_script), key=_tab)
            _res = {"value": _r.get("value")}
            if _r.get("rebuilt"):
                _res["rebuilt"] = True
                _res["note"] = ("the page was built again from its last "
                                "render: what earlier canvas_eval calls had "
                                "added since is gone. Add it again if you "
                                "need it.")
            if not _r.get("done"):
                _res["timeout"] = True
                _res["hint"] = ("no answer in time. If your JS is async, set a "
                                "global and read it in a second call.")
            try:
                _e2 = [m for m in self.dashboard.console_report(120, key=_tab)
                       if m.get("level") == "error"]
                if _e2:
                    _res["js_errors"] = _e2[:6]
            except Exception:
                pass
            self.thread_msg(t("msg.canvas_eval", n=len(str(_script))), "system")
            return json.dumps(_res, ensure_ascii=False, default=str)
        if tool_name == "browse_web" and not arguments.get("endpoint"):
            _b, _k, _m = self._chat_base_endpoint()
            if _m:
                arguments["endpoint"] = {"base_url": _b, "api_key": _k,
                                         "model": _m}
        import fed_agent as _fa_prev
        args_preview = _fa_prev.args_preview(arguments)
        self.thread_msg(f"🔗 MCP → {tool_name}({args_preview})", "system")
        t0 = time.time()
        result = self._mcp_call(tool_name, arguments)
        dur = round(time.time() - t0, 1)
        result_preview = str(result)[:200] if result else t("err.empty")
        if result and (str(result).lstrip().startswith("❌")
                       or "error" in str(result)[:50].lower()):
            self.thread_msg(f"❌ {tool_name} [{dur}s]: {result_preview}", "system")
        else:
            self.thread_msg(f"✅ {tool_name} [{dur}s]: {result_preview}", "system")
        _no_log = {
            "add_note", "search_notes", "delete_note", "list_categories",
            "set_cron_context", "list_cron_jobs",
            "alert",
            "search_web", "search_rag", "search_experience",
            "read_file", "list_dir", "get_credential", "list_credentials", "get_otp",
            "get_agentic_config", "get_todo_context",
            "code_index", "code_read_smart", "code_read_range", "code_search",
            "code_deps", "code_references",
        }
        if tool_name in ("code_read_smart", "code_read_range", "read_file"):
            try:
                _note = self._reread_note(arguments)
                if _note:
                    try:
                        _obj = json.loads(result)
                    except Exception:
                        _obj = None
                    if isinstance(_obj, dict):
                        result = json.dumps({"note": _note, **_obj},
                                            ensure_ascii=False)
                    else:
                        result = _note + "\n" + (result or "")
            except Exception as _e:
                print(f"[CODE] re-read note: {_e}", file=sys.stderr)
        _tab_efim = bool(getattr(self._chat_ctxs.get(
            getattr(self._tls, "tab_id", None) or self._active_tab_id),
            "no_log", False))
        if tool_name not in _no_log and not _tab_efim \
                and self.config.getboolean(
                "APP", "log_tools_to_notes", fallback=True):
            try:
                log_title = f"{tool_name} [{dur}s]"
                log_content = (
                    f"Tool: {tool_name}\n"
                    f"Args: {json.dumps(arguments, ensure_ascii=False, indent=2)[:500]}\n"
                    f"Duration: {dur}s\n"
                    f"Result:\n{str(result)[:2000]}"
                )
                self._mcp_call("add_note", {
                    "category": "cmd_log",
                    "title": log_title,
                    "content": log_content
                })
            except:
                pass
        if tool_name == "alert":
            try:
                data = json.loads(result) if isinstance(result, str) else result
                if isinstance(data, dict) and data.get("alerted"):
                    self.signals.show_alert.emit(data.get("title","🔔"), data.get("message",""))
            except: pass
        if tool_name == "search_rag":
            try:
                query = arguments.get("query", "")
                top_k = arguments.get("top_k", 5)
                local_chunks = self.rag_manager.search_similar_chunks(query, k=top_k, exclude_used=False)
                local_raw = self.rag_manager.search_in_raw_text(query)
                if local_chunks or local_raw:
                    data = json.loads(result) if isinstance(result, str) else result
                    if isinstance(data, dict):
                        existing = data.get("results", [])
                        for c in local_chunks:
                            existing.append({"source": c.get("metadata", {}).get("source", "local"), "text": c["text"][:300], "score": c.get("score", 0)})
                        for r in local_raw[:3]:
                            existing.append({"source": r.get("source", "raw"), "text": r.get("context", "")[:300]})
                        data["results"] = existing[:top_k * 2]
                        result = json.dumps(data, ensure_ascii=False)
            except Exception as e:
                print(f"[RAG] Merge error: {e}")
        if tool_name in ("delete_current_cron_job", "disable_current_cron_job", "delete_cron", "toggle_cron", "add_cron", "update_cron"):
            self.signals.refresh_cron.emit()
        if tool_name == "add_note":
            self.signals.refresh_notes.emit()
        if tool_name == "delete_note":
            self.signals.refresh_notes.emit()
        if tool_name in ("add_appointment", "add_task", "update_task_progress"):
            self.signals.refresh_todo.emit()
        if tool_name in ("generate_image", "edit_image"):
            self._picture_result_to_canvas(result)
        return result

    def _picture_sources_label(self, name, args):
        if name not in ("edit_image", "generate_image"):
            return ""
        refs = args.get("refs") if isinstance(args, dict) else None
        refs = [refs] if isinstance(refs, str) else list(refs or [])
        if not refs:
            return ""
        out = []
        for r in refs[:4]:
            r = str(r or "").strip()
            out.append(t("chat.img_from_pasted")
                       if r.lower() in ("attached", "attached images", "adjuntas", "adjuntadas")
                       else os.path.basename(r) or r)
        return " ← " + ", ".join(out) + " "

    def _has_inline_image(self, messages):
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        return True
            return False
        return False

    def _chat_can_see_images(self):
        import hoplita_llmcfg
        url, _k, model = self._chat_base_endpoint()
        return hoplita_llmcfg.model_sees(self.config, url, model)

    def _paint_orch_event(self, ev):
        et = ev.get("type")
        if et == "tick":
            return
        if et == "intent":
            self._set_subtask_intent(ev.get("id"), ev.get("items"))
            return
        if et in ("step", "step_done"):
            self._set_subtask_step(ev)
            return
        if et == "note":
            key = ev.get("key", "")
            self.thread_msg(t(key, **(ev.get("args") or {})), "system")
            if key == "plan.ack_timeout":
                self.signals.cancel_ack_tab.emit(
                    getattr(self._tls, "tab_id", None) or self._active_tab_id)
            return
        if et in ("snapshot", "plan"):
            subs = ev.get("subtasks", [])
            if subs and getattr(self, "agents_panel", None):
                self._set_tab_agents(subs)
        if et == "plan":
            self.thread_msg(t("msg.plan_subtasks", n=len(ev.get("subtasks", []))),
                            "system")
            for st in ev.get("subtasks", []):
                deps = f" \u2190{','.join(st.get('deps') or [])}" if st.get("deps") else ""
                mdl = f" \U0001f9e0{st['model']}" if st.get("model") else ""
                self.thread_msg(f"   \u2022 [{st.get('id')}] {str(st.get('goal',''))[:50]} "
                                f"({st.get('role','')}){mdl}{deps}", "system")
            if getattr(self, "agents_panel", None):
                self.signals.switch_to_agents.emit(self._emitting_tab())
        elif et == "subtask_start":
            mdl = f" \U0001f9e0{ev['model']}" if ev.get("model") else " \U0001f9e0base"
            self.thread_msg(f"\u25b6 [{ev.get('id')}] {str(ev.get('goal',''))[:50]} "
                            f"({ev.get('role','')}){mdl}\u2026", "system")
        elif et == "subtask_done":
            self.thread_msg(t("msg.subtask_done", id=ev.get("id"),
                              n=ev.get("attempts", 1)), "system")
        elif et == "subtask_acted":
            self._set_subtask_field(ev.get("id"), "tools", int(ev.get("tools", 0) or 0))
            if int(ev.get("tools", 0) or 0) == 0 and not ev.get("seat"):
                self.thread_msg(t("msg.subtask_no_tools", id=ev.get("id")),
                                "system")
        elif et == "subtask_retry":
            self.thread_msg(t("msg.subtask_retry_err", id=ev.get("id"),
                              n=ev.get("attempt", 1),
                              e=str(ev.get("error", ""))[:40]), "system")
        elif et == "subtask_failed":
            self.thread_msg(t("msg.subtask_failed_err", id=ev.get("id"),
                              e=str(ev.get("error", ""))), "error")
        elif et == "replanning":
            self.thread_msg(t("plan.ack_replanning"), "system")
        elif et == "synthesis_done":
            self.thread_msg(t("msg.synthesizing_final"), "system")
        elif et in ("complete", "cancelled"):
            self.signals.switch_to_chat.emit(self._emitting_tab())

    def _remote_target_names(self):
        cache = getattr(self, "_fed_cache", {}) or {}
        names = []
        for ch in (cache.get("children") or []):
            h = ch.get("handle") if isinstance(ch, dict) else str(ch)
            if h:
                names.append(f"child:{h}")
        for c in (cache.get("contacts") or []):
            if not isinstance(c, dict):
                c = {"handle": str(c)}
            h = c.get("handle")
            if not h:
                continue
            pref = "child" if c.get("local") else "friend"
            if f"child:{h}" not in names and f"friend:{h}" not in names:
                names.append(f"{pref}:{h}")
        return names

    def _answer_plan_ack(self, box, decision, feedback=None, subtasks=None,
                         allow_expand=False, goal=""):
        ack_id = box.get("ack_id")
        if not ack_id or box.get("answered"):
            return
        box["answered"] = True
        payload = {"ack_id": ack_id, "decision": decision,
                   "feedback": feedback or "",
                   "goal": goal or "",
                   "allow_expand": bool(allow_expand)}
        if subtasks is not None:
            payload["subtasks"] = subtasks
        try:
            requests.post(f"{self._tab_url()}/agent/plan_ack", json=payload,
                          timeout=10)
        except Exception as e:
            print(f"[ACK] could not answer the plan: {e}", file=sys.stderr)

    def _on_plan_ack(self, box):
        tab = box.get("tab") or self._active_tab_id
        ctx = self._chat_ctxs.get(tab)
        if ctx is None or getattr(self, "agents_panel", None) is None:
            self._plan_ack_dialog(box)
            return
        ctx.pending_ack = box
        if tab == self._active_tab_id:
            self._show_ack_in_panel(tab)
        else:
            self.signals.message_tab.emit(t("plan.ack_waiting_tab"), "system", tab)
            self._render_chat_tabs()

    def _agents_labels(self):
        return {
            "title": t("agents.board_title"), "empty": t("agents.board_empty"),
            "origin": t("agents.board_origin"), "o_local": t("agents.board_local"),
            "o_out": t("agents.board_sent"), "o_in": t("agents.board_received"),
            "multi": t("agents.board_multi"), "multi_sub": t("agents.board_multi_sub"),
            "single": t("agents.board_single"), "attempt": t("agents.board_attempt"),
            "intent_title": t("agents.intent_title"),
            "graph_hint": t("agents.board_graph_hint"), "stage": t("agents.board_stage"),
            "ack_title": t("plan.ack_title"), "ack_sub": t("plan.ack_panel_sub"),
            "ack_run": "▶ " + t("plan.ack_run"), "ack_replan": "✏ " + t("plan.ack_replan"),
            "ack_no": "✕ " + t("plan.ack_cancel"),
            "ack_fb_hint": t("plan.ack_feedback_hint"),
            "ack_goal_hint": t("plan.ack_goal_hint"),
            "ack_fb_tip": t("plan.ack_fb_tip"),
            "ack_run_ignores_note": t("plan.ack_run_ignores_note"),
            "ack_fb_required": t("plan.ack_feedback_required"),
            "ack_remove": t("plan.ack_remove"), "ack_count": t("plan.ack_count"),
            "ack_launching": t("plan.ack_launching"),
            "ack_replanning": t("plan.ack_replanning"),
            "ack_cancelling": t("plan.ack_cancelling"),
            "ack_model_tip": t("plan.ack_model_tip"),
            "ack_node_tip": t("plan.ack_node_tip"),
            "ack_goal_tip": t("plan.ack_goal_tip"),
            "ack_grow": t("plan.ack_grow"), "ack_grow_tip": t("plan.ack_grow_tip"),
        }

    def _show_ack_in_panel(self, tab):
        panel = getattr(self, "agents_panel", None)
        ctx = self._chat_ctxs.get(tab)
        box = getattr(ctx, "pending_ack", None) if ctx is not None else None
        if panel is None or box is None:
            return
        self._ack_showing_tab = tab
        panel.set_labels(self._agents_labels())
        panel.ask_plan(box["subs"], ["local"] + self._remote_target_names(),
                       ["base"] + list(self._model_pool().keys()))
        if getattr(self, "_chat_stack", None) is not None:
            self._chat_stack.setCurrentIndex(1)
        self._render_chat_tabs()

    def _refresh_agents_view(self):
        panel = getattr(self, "agents_panel", None)
        if panel is None:
            return
        ctx = self._chat_ctxs.get(self._active_tab_id)
        if getattr(ctx, "pending_ack", None) is not None:
            self._show_ack_in_panel(self._active_tab_id)
            return
        if getattr(self, "_ack_showing_tab", None) is not None:
            panel.close_ack()
            self._ack_showing_tab = None
        panel.update_state(self._agents_cards_for_active())
        panel.set_intent(getattr(ctx, "intent", []) or [])

    def _on_ack_decided(self, data):
        tab = getattr(self, "_ack_showing_tab", None)
        ctx = self._chat_ctxs.get(tab)
        box = getattr(ctx, "pending_ack", None) if ctx is not None else None
        if box is None:
            return
        dec = str(data.get("decision") or "cancel")
        if dec == "run":
            subs = data.get("subtasks")
            subs = ([dict(x) for x in subs if isinstance(x, dict)]
                    if isinstance(subs, list) else box.get("subs"))
            _goal = str(data.get("goal") or "").strip()
            if _goal and ctx is not None:
                ctx.goal = _goal
                self.add_message(t("goal.set", goal=_goal), "system")
            self._answer_plan_ack(box, "run", subtasks=subs,
                                  allow_expand=bool(data.get("allow_expand")),
                                  goal=_goal)
        elif dec == "edit":
            self._answer_plan_ack(box, "edit",
                                  feedback=str(data.get("feedback") or "").strip())
        else:
            self._answer_plan_ack(box, "cancel")
        self._resolve_ack(ctx, box)

    def _resolve_ack(self, ctx, box):
        if ctx is not None:
            ctx.pending_ack = None
        panel = getattr(self, "agents_panel", None)
        if panel is not None and getattr(self, "_ack_showing_tab", None) is not None:
            panel.close_ack()
            self._ack_showing_tab = None
        self._render_chat_tabs()

    def _on_plan_ack_close(self, box):
        ctx = self._chat_ctxs.get(box.get("tab"))
        if ctx is not None and getattr(ctx, "pending_ack", None) is box:
            self._answer_plan_ack(box, "cancel")
            self._resolve_ack(ctx, box)

    def _cancel_pending_ack(self, tab_id):
        self._cancel_council_ack(tab_id)
        ctx = self._chat_ctxs.get(tab_id)
        box = getattr(ctx, "pending_ack", None) if ctx is not None else None
        if box is None:
            return False
        self._answer_plan_ack(box, "cancel")
        self._resolve_ack(ctx, box)
        return True

    def _plan_ack_dialog(self, box):
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                                     QPushButton, QScrollArea, QWidget, QComboBox,
                                     QLineEdit)
        subs = box["subs"]
        dlg = QDialog(self)
        dlg.setWindowTitle(t("plan.ack_title"))
        dlg.resize(920, 620)
        dlg.setModal(True)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(14, 14, 14, 12)
        v.setSpacing(10)
        head = QLabel(t("plan.ack_intro", n=len(subs)))
        head.setWordWrap(True)
        head.setMinimumHeight(38)
        head.setStyleSheet("color:#e6edf7; font-weight:600; padding:2px 2px 6px 2px;")
        v.addWidget(head)
        dlg.setStyleSheet(
            "QDialog { background:#0b1220; }"
            "QLabel { color:#e6edf7; }"
            "QLineEdit, QComboBox { background:#131c2e; color:#e6edf7;"
            " border:1px solid #2a3547; border-radius:7px; padding:5px 8px; }"
            "QPushButton { background:#1e293b; color:#e6edf7; border:1px solid #334155;"
            " border-radius:8px; padding:7px 15px; font-weight:600; }"
            "QPushButton:hover { background:#334155; }"
            "QPushButton:default { background:#2563eb; border-color:#2563eb; }"
            "QScrollArea { border:none; background:transparent; }")
        sc = QScrollArea(); sc.setWidgetResizable(True)
        inner = QWidget(); il = QVBoxLayout(inner); il.setSpacing(8)
        targets = ["local"] + self._remote_target_names()
        models = ["base"] + list(self._model_pool().keys())
        rows = []
        for s in subs:
            card = QWidget()
            card.setStyleSheet("QWidget { background:#0f172a; border:1px solid #1e293b;"
                               " border-radius:9px; }")
            cv = QVBoxLayout(card); cv.setContentsMargins(12, 8, 12, 10)
            top = QHBoxLayout()
            sid = QLabel(f"[{s.get('id','?')}]  {s.get('role','')}")
            sid.setStyleSheet("color:#38bdf8; font-weight:700;")
            top.addWidget(sid)
            top.addStretch()
            dep = s.get("deps") or []
            if dep:
                d = QLabel("←" + ",".join(dep))
                d.setStyleSheet("color:#94a3b8;")
                top.addWidget(d)
            cv.addLayout(top)
            goal = QLabel(str(s.get("goal", ""))[:400])
            goal.setWordWrap(True)
            goal.setStyleSheet("color:#cbd5e1; font-size:12px;")
            cv.addWidget(goal)
            pick = QHBoxLayout()
            pick.addWidget(QLabel("🧠"))
            cm = QComboBox(); cm.addItems(models)
            cm.setCurrentText(s.get("model") or "base")
            pick.addWidget(cm)
            pick.addSpacing(14)
            pick.addWidget(QLabel("🌐"))
            ct = QComboBox(); ct.addItems(targets)
            cur_t = str(s.get("target") or "local")
            if cur_t not in targets:
                ct.addItem(cur_t)
            ct.setCurrentText(cur_t)
            pick.addWidget(ct)
            pick.addStretch()
            cv.addLayout(pick)
            il.addWidget(card)
            rows.append((s, cm, ct))
        il.addStretch()
        sc.setWidget(inner)
        v.addWidget(sc, 1)
        fb = QLineEdit()
        fb.setPlaceholderText(t("plan.ack_feedback_hint"))
        v.addWidget(fb)
        br = QHBoxLayout(); br.addStretch()
        from PyQt6.QtWidgets import QCheckBox
        grow = QCheckBox(t("plan.ack_grow"))
        grow.setToolTip(t("plan.ack_grow_tip"))
        grow.setStyleSheet("QCheckBox { color:#94a3b8; } QCheckBox:hover { color:#e6edf7; }")
        v.addWidget(grow)
        b_cancel = QPushButton("✕ " + t("plan.ack_cancel"))
        b_edit = QPushButton("✏ " + t("plan.ack_replan"))
        b_run = QPushButton("▶ " + t("plan.ack_run"))
        b_run.setDefault(True)
        for b in (b_cancel, b_edit, b_run):
            br.addWidget(b)
        v.addLayout(br)
        out = {}

        def _apply_overrides():
            for s, cm, ct in rows:
                m = cm.currentText().strip()
                s["model"] = "" if m in ("base", "") else m
                s["target"] = ct.currentText().strip() or "local"

        def _run():
            _apply_overrides()
            out["d"] = ("run", grow.isChecked())
            dlg.accept()

        def _edit():
            txt = fb.text().strip()
            if not txt:
                fb.setPlaceholderText(t("plan.ack_feedback_required")); return
            out["d"] = ("edit", txt); dlg.accept()

        def _cancel():
            out["d"] = ("cancel", None); dlg.reject()
        b_run.clicked.connect(_run); b_edit.clicked.connect(_edit)
        b_cancel.clicked.connect(_cancel)
        dlg.exec()
        decision, extra = out.get("d") or ("cancel", None)
        if decision == "run":
            self._answer_plan_ack(box, "run", subtasks=subs,
                                  allow_expand=bool(extra))
        elif decision == "edit":
            self._answer_plan_ack(box, "edit", feedback=extra)
        else:
            self._answer_plan_ack(box, "cancel")

    def _handle_verify_command(self, arg):
        ctx = self._view_ctx()
        arg = (arg or "").strip()
        if not arg:
            cur = getattr(ctx, "verify_cmd", "")
            self.add_message(t("verify.current", cmd=cur) if cur
                             else t("verify.none"), "system")
            return
        if arg.lower() in ("off", "no", "clear", "none"):
            ctx.verify_cmd = ""
            self._code_verify_mirror("")
            self.add_message(t("verify.cleared"), "system")
            return
        ctx.verify_cmd = arg
        if "run_command" in (getattr(ctx, "blocked_tools", ()) or ()) \
                or "run_command" in (getattr(self, "blocked_tools", ()) or ()):
            self.add_message(t("fwtab.verify_warn", cmd=arg), "error")
        self._code_verify_mirror(arg)
        self.add_message(t("verify.set", cmd=arg), "system")

    def _code_verify_mirror(self, cmd):
        w = getattr(self, "code_verify", None)
        if w is None:
            return
        w.setText(cmd)
        getattr(self, "_code_verify_paint", lambda: None)()

    def _fed_handle(self):
        h = getattr(self, "_fed_handle_cache", None)
        if h:
            return h
        ok, st, _ = self._mcp_try("fed_status", {}, timeout=15)
        h = (st or {}).get("handle", "") if ok else ""
        self._fed_handle_cache = h
        return h

    def _handle_context_command(self):
        ctx = self._turn_ctx()
        hist = [m for m in (self.conversation_history or [])
                if isinstance(m, dict)]
        payload = {"messages": hist,
                   "code_project": getattr(ctx, "code_project", "") or ""}
        try:
            r = requests.post(f"{self._tab_url()}/agent/context_map", json=payload,
                              timeout=60)
            data = r.json() if r.status_code == 200 else {"error": f"HTTP {r.status_code}"}
        except Exception as e:
            data = {"error": str(e)}
        if not isinstance(data, dict) or data.get("error"):
            self.thread_msg(t("ctx.map_fail", e=(data or {}).get("error") or "?"),
                            "error")
            return
        f = lambda n: f"{n/1000:.1f}k" if n >= 1000 else str(n)
        measured = getattr(ctx, "last_prompt_tokens", 0) or 0
        h = data.get("history") or {}
        lines = [t("ctx.map_head", total=f(data.get("total_tokens", 0)),
                   measured=(f(measured) if measured else "—"),
                   n=h.get("messages", len(hist)),
                   tools=f((data.get("tools") or {}).get("tokens", 0)),
                   carried=(data.get("tools") or {}).get("carried", 0))]
        by_role = h.get("by_role") or {}
        if by_role:
            lines.append(t("ctx.map_roles", roles=" · ".join(
                f"{r}: {f(n)}" for r, n in sorted(by_role.items(),
                                                   key=lambda kv: -kv[1]))))
        lines.append(t("ctx.map_blocks"))
        for b in (data.get("by_block") or [])[:15]:
            lines.append(f"  {f(b.get('tokens', 0)):>7}  {b.get('role', ''):9.9} "
                         f"{(b.get('head') or '').strip()[:70]}")
        lines.append(t("ctx.map_hint"))
        self.thread_msg("\n".join(lines), "system")

    def _compact_now(self):
        tab_id = self._active_tab_id
        ctx = self._view_ctx()
        if getattr(ctx, "compacting", False):
            self.add_message(t("ctx.compact_busy"), "system")
            return

        def _toggle_queued():
            if getattr(ctx, "compact_pending", False):
                ctx.compact_pending = False
                self.add_message(t("ctx.compact_unqueued"), "system")
            else:
                ctx.compact_pending = True
                self.add_message(t("ctx.compact_queued"), "system")
            self._update_queue_label()

        if getattr(ctx, "turn_live", False) or getattr(ctx, "task_running", False):
            _toggle_queued()
            return
        if not self._spawn_turn(tab_id, self._compact_run):
            _toggle_queued()

    def _compact_run(self):
        self._compacting = True
        self._task_begin()
        try:
            self._auto_compact()
        finally:
            self._task_end()

    def _record_injections(self, tab, note, fresh):
        full = note.get("full")
        if not full:
            return False
        ctx = getattr(self, "_chat_ctxs", {}).get(tab)
        if ctx is None:
            return False
        if fresh:
            ctx.last_injections = []
        ctx.last_injections.append((note.get("key", ""), list(full)))
        return True

    def _handle_why_command(self, arg=""):
        ctx = self._view_ctx()
        inj = getattr(ctx, "last_injections", None)
        if not inj:
            self.add_message(t("why.nothing"), "system")
            return
        out = [t("why.header")]
        for key, items in inj:
            out.append("")
            out.append(self._why_title(key))
            for it in items:
                out.append("  • " + " ".join(str(it).split()))
        self.add_message("\n".join(out), "system")

    @staticmethod
    def _why_title(key):
        return {"msg.rules_injected": "📏 REGLAS TUYAS",
                "msg.lessons_injected": "🧠 LECCIONES",
                "msg.facts_applied": "📌 HECHOS",
                "msg.facts_all": "📌 HECHOS",
                "msg.facts_all_cut": "📌 HECHOS",
                "msg.skills_injected": "📋 SKILLS"}.get(key, key)

    def _handle_pin_command(self, arg):
        ctx = self._view_ctx()
        arg = (arg or "").strip().lower()
        cur = getattr(ctx, "pinned_device", None)
        if arg in ("?", "status", "ver"):
            self.add_message(
                t("pin.current", name=cur.get("name", "?"), id=cur.get("id", "?")[:8])
                if cur else t("pin.none"), "system")
            return
        if arg in ("off", "no", "clear", "none", "unpin"):
            ctx.pinned_device = None
            self.add_message(t("pin.cleared"), "system")
            self._render_chat_tabs()
            if ctx.session_uuid:
                self._persist_tab(self._active_tab_id, quiet=True)
            return
        okd, d, err = self._mcp_try("device_info", {}, timeout=15)
        if not okd or not isinstance(d, dict) or not d.get("id"):
            self.add_message(t("pin.no_device", e=err or "?"), "error")
            return
        if not getattr(ctx, "sync_id", ""):
            import uuid as _u
            ctx.sync_id = _u.uuid4().hex[:12]
        ctx.pinned_device = {"id": d["id"], "name": d.get("name", "?")}
        self.add_message(t("pin.set", name=ctx.pinned_device["name"],
                           id=d["id"][:8]), "system")
        self._render_chat_tabs()
        self._persist_tab(self._active_tab_id, quiet=True)

    def _tab_url(self, ctx=None):
        try:
            ctx = ctx if ctx is not None else self._turn_ctx()
            pin, remote = self._tab_runs_remotely(ctx)
        except Exception:
            return self._mcp_url
        return f"{self._mcp_url}/tied/{pin['id']}" if remote else self._mcp_url

    def _tab_headers(self, ctx=None):
        try:
            ctx = ctx if ctx is not None else self._turn_ctx()
            sid = getattr(ctx, "sync_id", "") or ""
        except Exception:
            sid = ""
        return {"X-Hoplita-Tab": sid} if sid else {}

    @staticmethod
    def _http_error(r):
        try:
            d = r.json() or {}
            why = d.get("error") or d.get("detail")
            if why:
                return f"HTTP {r.status_code}: {why}"
        except Exception:
            pass
        return f"HTTP {r.status_code}"

    def _tab_runs_remotely(self, ctx):
        pin = getattr(ctx, "pinned_device", None)
        if not pin or not pin.get("id"):
            return None, False
        mine = getattr(self, "_device_id", None)
        if mine is None:
            ok, d, _ = self._mcp_try("device_info", {}, timeout=10, url=self._mcp_url)
            mine = (d or {}).get("id", "") if ok else ""
            self._device_id = mine
        return pin, bool(mine) and pin["id"] != mine

    def _handle_goal_command(self, arg):
        ctx = self._view_ctx()
        arg = (arg or "").strip()
        if not arg:
            cur = getattr(ctx, "goal", "")
            self.add_message(t("goal.current", goal=cur) if cur
                             else t("goal.none"), "system")
            return
        if arg.lower() in ("off", "no", "clear", "none"):
            ctx.goal = ""
            self._code_goal_paint()
            self.add_message(t("goal.cleared"), "system")
            return
        ctx.goal = arg
        self._code_goal_paint()
        self.add_message(t("goal.set", goal=arg), "system")

    def _handle_qa_command(self, arg):
        arg = (arg or "").strip().lower()
        pool = self._model_pool()
        ctx = self._turn_ctx()
        if not arg:
            cur = getattr(ctx, "qa_override", "")
            cfg_alias = self.config.get("CHAT", "qa_model", fallback="")
            lines = [t("qa.active",
                       name=(cur or t("qa.config_default"))),
                     t("qa.base_is",
                       model=cfg_alias or t("qa.same_model"))]
            if pool:
                lines.append(t("qa.pool_list"))
                for name, m in pool.items():
                    lines.append(f"  • {name} → {m['model']}")
            lines.append(t("qa.usage"))
            self.add_message("\n".join(lines), "system")
            return
        if arg in ("auto", "default", "config"):
            ctx.qa_override = ""
            self.add_message(t("qa.cleared"), "system")
            return
        if arg == "base":
            ctx.qa_override = "base"
            self.add_message(t("qa.set_base", model=self.config.get(
                "API", "model", fallback="?")), "system")
            return
        if arg in ("off", "no", "none"):
            ctx.qa_override = "off"
            self.add_message(t("qa.off"), "system")
            return
        if arg in pool:
            ctx.qa_override = arg
            self.add_message(t("qa.set", name=arg,
                               model=pool[arg]["model"]), "system")
            return
        self.add_message(t("qa.unknown", name=arg,
                           pool=", ".join(pool) or "—"), "system")

    def _handle_model_command(self, arg):
        arg = arg.strip()
        _parts = arg.split(None, 1)
        if _parts and _parts[0].lower() == "qa":
            self._handle_qa_command(_parts[1] if len(_parts) > 1 else "")
            return
        if (len(_parts) > 1 and _parts[0].lower() == "base"):
            arg = _parts[1]
        pool = self._model_pool()
        if not arg:
            active = self._chat_model_override or "base"
            lines = [t("model.active", name=active)]
            base_model = self._base_model_name()
            lines.append(t("model.base_is", model=base_model))
            if pool:
                lines.append(t("model.pool_list"))
                for name, m in pool.items():
                    lines.append(f"  • {name} → {m['model']}" + self._lent_tag(m))
            else:
                lines.append(t("model.no_pool"))
            self.add_message("\n".join(lines), "system")
            return
        if arg in ("auto", "router"):
            self._chat_model_override = ""
            self.add_message(t("model.router_back"), "system")
            return
        if arg in ("base", "default"):
            self._chat_model_override = "base"
            self.add_message(t("model.switched_base",
                               model=self._base_model_name()),
                             "system")
            return
        if arg in pool:
            _via = pool[arg].get("via") or ""
            if _via and not self._contact_online_cached(_via):
                self.add_message(t("model.owner_offline", name=arg, handle=_via), "error")
                return
            self._chat_model_override = arg
            self.add_message(t("model.switched", name=arg, model=pool[arg]["model"])
                             + self._lent_tag(pool[arg]), "system")
        else:
            avail = ", ".join(pool.keys()) if pool else "—"
            self.add_message(t("model.unknown", name=arg, avail=avail), "error")

    def _handle_multi_command(self, task, already_in_history=False):
        self.thread_msg(t("msg.multi_planning", task=task[:60]), "system")
        self._task_begin()
        try:
            base_url, api_key, model = self._chat_base_endpoint()
            _hist = list(self.conversation_history)
            if not already_in_history:
                _hist.append({"role": "user", "content": task})
            final = self._stream_turn(
                {"orchestrate": task,
                 "messages": _hist,
                 "endpoint": {"base_url": base_url, "api_key": api_key,
                              "model": model}}, model=model)
            if not already_in_history:
                self.conversation_history.append({"role": "user", "content": task})
            _trace = (getattr(self._turn_ctx(), "turn_record", "")
                       or self._turn_trace_note())
            _hist = (final or "") + (("\n\n" + _trace) if _trace else "")
            if self._stopped():
                note = ("[The user STOPPED this task before it completed. "
                        "Partial result:]\n" + _hist)
                self.conversation_history.append({"role": "assistant", "content": note})
                self.thread_msg(t("msg.task_stopped_partial2", final=final), "assistant")
            else:
                self.conversation_history.append(
                    {"role": "assistant", "content": _hist or final})
                self.thread_msg(final, "assistant")
            self.signals.switch_to_chat.emit(self._emitting_tab())
        except Exception as e:
            self.thread_msg(t("msg.multi_error", e=e), "error")
            print(f"[MULTI] error: {e}", file=sys.stderr)
        finally:
            self._task_end()

    def call_chat_with_tools(self, messages, max_iterations=None,
                             model_name=None, compact=False):
        if max_iterations is None:
            max_iterations = self.config.getint("CHAT", "agentic_max_iterations", fallback=5)
        endpoint = {}
        _pin = model_name or self._chat_model_override
        if _pin and _pin != "base":
            endpoint = dict(zip(("base_url", "api_key", "model"),
                                self._resolve_model_by_name(_pin)))
        elif _pin == "base":
            endpoint = dict(zip(("base_url", "api_key", "model"), self._resolve_endpoint("API")))
        model = endpoint.get("model") or self._base_model_name()
        if self._has_inline_image(messages) and not self._chat_can_see_images():
            self.thread_msg(t("dlg.images_pasted_warn"), "system")

        return self._stream_turn({
            "messages": messages,
            "max_iterations": max_iterations,
            "compact": bool(compact),
            "verify": (None if getattr(self._tls, "unattended", False)
                       else getattr(self._turn_ctx(), "verify_cmd", "") or None),
            "goal": (None if getattr(self._tls, "unattended", False)
                     else getattr(self._turn_ctx(), "goal", "") or None),
            "verify_goal": (None if getattr(self._tls, "unattended", False)
                            else self._turn_check_goal() or None),
            "qa": self.config.get("CHAT", "qa_review", fallback="off"),
            "inject": True,
            "pinned_skills": [dict(sk) for sk in self._pinned_skills],
            "code_project": getattr(self._turn_ctx(), "code_project", "") or "",
            "job_id": getattr(self._turn_ctx(), "job_id", "") or "",
            "rag_docs": self._rag_docs_count(),
            "endpoint": endpoint,
        }, max_iterations=max_iterations, model=model)

    def _rag_docs_count(self):
        try:
            st = self.rag_manager.get_stats()
            return int(st.get("total_chunks", 0) or 0) + int(
                (st.get("raw_texts") or {}).get("total_sources", 0) or 0)
        except Exception:
            return 0

    def _stream_turn(self, payload, max_iterations=None, model=""):
        timeout = self.config.getint("CHAT", "timeout", fallback=120)
        max_iterations = max_iterations or 1
        payload = dict(payload)
        _tpin, _tremote = self._tab_runs_remotely(self._turn_ctx())
        if _tremote:
            payload.setdefault("delegate", list(_SCREEN_TOOLS))
        payload.setdefault("delegate", ["*"])
        payload.setdefault("client_tool_timeout", self.config.getint(
            "CHAT", "client_tool_timeout", fallback=3600))
        payload.setdefault("ack_mode", "stream")
        payload.setdefault("unattended", bool(getattr(self._tls, "unattended", False)))
        _c = self._turn_ctx()
        _qa_ov = getattr(_c, "qa_override", "")
        if _qa_ov == "off":
            payload["qa"] = "off"
        elif _qa_ov:
            if _qa_ov == "base":
                _qm = dict(zip(("base_url", "api_key", "model"),
                               self._resolve_endpoint("API")))
            else:
                _qm = self._model_pool().get(_qa_ov)
            if _qm:
                payload["qa_endpoint"] = {"base_url": _qm.get("base_url"),
                                          "api_key": _qm.get("api_key"),
                                          "model": _qm.get("model")}
                if payload.get("qa") == "off":
                    payload["qa"] = "auto"
        payload.setdefault("blocked_tools",
                           sorted(getattr(_c, "blocked_tools", ()) or ()))
        payload.setdefault("local_only", bool(getattr(_c, "local_only", False)))
        payload.setdefault("session", getattr(_c, "sync_id", "")
                           or getattr(_c, "tab_id", "") or "")
        payload.setdefault("session_uuid", getattr(_c, "session_uuid", "") or "")
        payload.setdefault("log", not getattr(_c, "no_log", False))
        turn_id = None
        stop_sent = False
        tool_idx = 0
        _prep_seen = False
        tab = getattr(self._tls, "tab_id", None) or self._active_tab_id
        if payload.get("council"):
            self._tls.council_tab = tab
        _c0 = getattr(self, "_chat_ctxs", {}).get(tab)
        if _c0 is not None:
            _c0.turn_steps = []
            _c0.turn_qa = []
            _c0.turn_record = ""
            _c0.turn_transcript = []
            _c0.turn_last_user = None

        def _send_stop():
            nonlocal stop_sent
            _was_stopped["v"] = True
            if stop_sent or not turn_id:
                return
            try:
                rs = requests.post(f"{self._tab_url(_c)}/agent/stop",
                                   json={"turn_id": turn_id}, timeout=15)
                if rs.status_code == 200:
                    stop_sent = True
            except Exception:
                pass

        _turn_stop = (getattr(self._tls, "stop_event", None)
                      or getattr(self, "_stop_requested", None) or threading.Event())
        _was_stopped = {"v": False}

        def _run_background_delegated(ev):
            self._tls.tab_id = tab
            self._tls.turn_id = turn_id
            self._tls.bg_job = ev.get("background") or ""
            name = ev.get("tool", "")
            err = ""
            try:
                result = self.execute_tool(name, ev.get("args") or {})
            except Exception as e:
                result, err = "", str(e)
            finally:
                self._tls.bg_job = ""
            try:
                _r = requests.post(f"{self._tab_url(_c)}/agent/background/done",
                                   json={"job_id": ev.get("background") or "",
                                         "result": (result if isinstance(result, str)
                                                    else json.dumps(result, ensure_ascii=False)),
                                         "error": err}, timeout=30)
                _j = _r.json() if _r.ok else {}
                if isinstance(_j, dict) and _j.get("accepted") is False:
                    self.thread_msg(t("bg.result_dropped", tool=name,
                                      why=str(_j.get("error") or "")[:120]),
                                    "system")
            except Exception as e:
                print(f"[BACKGROUND] {name}: {e}", file=sys.stderr)

        _tu_rid = getattr(self._tls, "tu_rid", 0)

        def _run_delegated(ev):
            self._tls.tab_id = tab
            self._tls.tu_rid = _tu_rid
            self._tls.turn_id = turn_id
            name = ev.get("tool", "")
            if _turn_stop.is_set() or _was_stopped["v"]:
                result = json.dumps({"error": "the user stopped the task; this was not run"},
                                    ensure_ascii=False)
            else:
                try:
                    result = self.execute_tool(name, ev.get("args") or {})
                except Exception as e:
                    result = json.dumps({"error": str(e)}, ensure_ascii=False)
            try:
                _sc = getattr(self, "_chat_ctxs", {}).get(tab)
                for _p in reversed(getattr(_sc, "turn_steps", []) or []):
                    if _p.get("action") == name and "result" not in _p:
                        _text = (result if isinstance(result, str)
                                  else json.dumps(result, ensure_ascii=False))
                        _p["result"] = _text[:400]
                        _p["result_len"] = len(_text)
                        _p["done"] = True
                        _p["duration"] = time.time() - float(_p.get("t0") or time.time())
                        _p["ok"] = not _text.lstrip().startswith(('{"error"', "❌"))
                        self._machine_emit("result", tool=name, ok=_p["ok"],
                                           duration=_p["duration"])
                        break
            except Exception:
                pass
            try:
                _r = requests.post(f"{self._tab_url(_c)}/agent/tool_result",
                              json={"turn_id": turn_id,
                                    "call_id": ev.get("call_id"),
                                    "result": result if isinstance(result, str)
                                    else json.dumps(result, ensure_ascii=False)},
                              timeout=30)
                try:
                    _j = _r.json()
                except Exception:
                    _j = {}
                if isinstance(_j, dict) and _j.get("accepted") is False \
                        and not (_turn_stop.is_set() or _was_stopped["v"]):
                    self.thread_msg(t("msg.tool_result_lost", tool=name,
                                      why=str(_j.get("error") or "")[:160]),
                                    "error")
            except Exception as e:
                self.thread_msg(f"❌ tool_result → MCP: {e}", "error")

        try:
            try:
                _pin, _remote = self._tab_runs_remotely(_c)
                if _pin and not _remote and getattr(_c, "sync_id", ""):
                    payload["mirror_tab"] = _c.sync_id
            except Exception:
                pass
            with requests.post(f"{self._tab_url(_c)}/agent/turn", json=payload,
                               headers=self._tab_headers(_c),
                               stream=True, timeout=(10, timeout + 60)) as r:
                if r.status_code != 200:
                    _why = self._http_error(r)
                    self.thread_msg(t("msg.llm_net_err", e=f"/agent/turn {_why}"), "error")
                    raise Exception(f"/agent/turn {_why}")
                for line in r.iter_lines():
                    if not line:
                        continue
                    if self._stopped():
                        _send_stop()
                    ev = json.loads(line)
                    et = ev.get("event")
                    if et == "turn_start":
                        _sctx = getattr(self, "_chat_ctxs", {}).get(tab)
                        if _sctx is not None:
                            _sctx.turn_steps = []
                            _sctx.turn_qa = []
                        turn_id = ev.get("turn_id")
                        self._turn_ctx().turn_id = turn_id
                        self._set_tab_intent([])
                        if not (payload.get("orchestrate")
                                or payload.get("council")):
                            _shelf = ev.get("shelf", 0)
                            self.thread_msg(
                                t("chat.agentic_tools_shelf",
                                  n=ev.get("tools", 0), shelf=_shelf,
                                  max=max_iterations) if _shelf else
                                t("chat.agentic_tools",
                                  n=ev.get("tools", 0), max=max_iterations))
                            _carried = ev.get("carried")
                            if isinstance(_carried, list):
                                _miss = [n_ for n_ in ("plan_and_orchestrate", "intent")
                                         if n_ not in _carried]
                                if _miss:
                                    self.thread_msg(t("chat.agentic_not_offered",
                                                      tools=", ".join(_miss)), "error")
                    elif et == "iteration":
                        tool_idx = 0
                        try:
                            self._turn_ctx().turn_iter = (int(ev.get("n") or 0),
                                                          int(ev.get("max") or 0))
                        except Exception:
                            pass
                        self._machine_emit("round", n=ev.get("n"), max=ev.get("max"))
                        self.thread_msg(t("chat.iteration", n=ev.get("n"),
                                          max=ev.get("max")))
                    elif et == "injected":
                        _tc, _im = self._turn_ctx(), ev.get("message", "")
                        _p = getattr(_tc, "injected_pending", None)
                        if _p and _im in _p:
                            _p.remove(_im)
                            self.signals.queue_changed.emit(
                                getattr(self._tls, "tab_id", "") or "")
                        if ev.get("kind") == "background":
                            self.thread_msg(t("chat.injected_bg", n=ev.get("n"),
                                              msg=_im.split("\n", 1)[0][:200]))
                        else:
                            self.thread_msg(t("chat.injected", n=ev.get("n"),
                                              msg=_im))
                    elif et == "background_live":
                        self.thread_msg(t("bg.live_line", n=ev.get("n", 0),
                                          names=", ".join(
                                              str(j.get("label") or j.get("id"))
                                              for j in (ev.get("jobs") or []))),
                                        "system")
                    elif et == "max_iterations":
                        max_iterations = ev.get("max", max_iterations)
                        self.thread_msg(t("chat.iteration_budget",
                                          was=ev.get("was"), max=ev.get("max")))
                    elif et == "prep":
                        n = ev.get("note") or {}
                        self.thread_msg(t(n.get("key", ""), **(n.get("args") or {})),
                                        "system")
                        if self._record_injections(tab, n, fresh=not _prep_seen):
                            _prep_seen = True
                    elif et == "orch":
                        self._paint_orch_event(ev.get("orch") or {})
                    elif et == "council":
                        self._paint_council_event(ev.get("council") or {})
                    elif et == "council_ack":
                        self.signals.council_ack.emit(
                            {"ack_id": ev.get("ack_id"),
                             "round": ev.get("round", 1),
                             "dissent": ev.get("dissent") or [], "tab": tab})
                    elif et == "plan_ack":
                        self.signals.plan_ack.emit(
                            {"subs": ev.get("subtasks") or [],
                             "ack_id": ev.get("ack_id"), "tab": tab})
                    elif et == "verify_run":
                        self.thread_msg(t("verify.running", cmd=ev.get("command", ""),
                                          n=ev.get("attempt", 1)))
                    elif et == "verify_result":
                        if ev.get("blocked"):
                            self.thread_msg(
                                t("verify.blocked",
                                  why=(ev.get("output_preview") or "")[:160]),
                                "error")
                        else:
                            self.thread_msg(t("verify.green") if ev.get("ok")
                                            else t("verify.red", out=(ev.get("output_preview") or "")[:160]),
                                            "system" if ev.get("ok") else "error")
                    elif et == "qa_run":
                        self.thread_msg(t("qa.running", n=ev.get("round", 1)))
                    elif et == "qa_result":
                        if ev.get("mode") == "output":
                            self.thread_msg(t("qa.output_only"), "system")
                        elif ev.get("mode") == "contract":
                            self.thread_msg(t("qa.steps_only"), "system")
                        if ev.get("reviewed") or ev.get("said"):
                            self.thread_msg(str(self._keep_qa_review(ev)), "qa_review")
                        elif ev.get("ok") is True:
                            self.thread_msg(t("qa.pass"))
                        elif ev.get("ok") is None:
                            self.thread_msg(t("qa.unjudged", why=ev.get("why", "")), "system")
                        else:
                            self.thread_msg(t("qa.fail", why=ev.get("why", "")), "error")
                        _sc = getattr(self, "_chat_ctxs", {}).get(tab)
                        if _sc is not None:
                            if not hasattr(_sc, "turn_qa"):
                                _sc.turn_qa = []
                            _sc.turn_qa.append({"ok": bool(ev.get("ok")),
                                                "why": str(ev.get("why", ""))[:300],
                                                "round": ev.get("round", 1)})
                    elif et == "tool_progress":
                        self.thread_msg(t(ev.get("key", ""),
                                          **(ev.get("args") or {})), "system")
                        if ev.get("key") == "wb.open":
                            self.signals.wb_open.emit(
                                str((ev.get("args") or {}).get("id") or ""), tab)
                        if ev.get("key") == "office.view":
                            self._office_view_to_canvas(ev.get("args") or {})
                        if ev.get("key") == "image.generated":
                            self._canvas_show_image(ev.get("args") or {})
                    elif et in ("config_consent", "config_changed", "share_consent"):
                        self.signals.config_event.emit(tab, ev)
                        if et == "config_changed":
                            self.thread_msg(t("selfcfg.changed", what=", ".join(
                                f"[{c.get('section', '')}] {c.get('key', '')}"
                                for c in (ev.get("changes") or []))), "system")
                    elif et in ("hands_begin", "hands_action", "hands_end",
                                "hands_consent"):
                        self.signals.hands_event.emit(tab, ev)
                        if et == "hands_begin":
                            self.thread_msg(t("hands.begin"), "system")
                        elif et == "hands_action":
                            _v = ev.get("verified")
                            self.thread_msg(t("hands.action_line",
                                              tool=str(ev.get("tool", "")).replace("hands_", ""),
                                              target=ev.get("target") or "",
                                              mark=("✔" if _v else "✖" if _v is False
                                                    else "·")),
                                            "system")
                        elif et == "hands_end":
                            self.thread_msg(t("hands.end", n=ev.get("actions", 0)),
                                            "system")
                    elif et == "intent":
                        self._set_tab_intent(ev.get("items") or [])
                    elif et == "intent_left":
                        self.thread_msg(t("chat.intent_left", n=ev.get("n", 0),
                                          goals=" · ".join(ev.get("goals") or [])),
                                        "system")
                    elif et == "metacog_nudge":
                        _why = ev.get("reason")
                        _cou = t("chat.metacog_council_offered") if ev.get("council") else ""
                        if _why == "loop":
                            self.thread_msg(t("chat.metacog_loop", tool=ev.get("tool", "?")) + _cou, "system")
                        elif _why == "last":
                            self.thread_msg(t("chat.metacog_last"), "system")
                        else:
                            self.thread_msg(t("chat.metacog_nudge",
                                              tools=ev.get("sterile_tools", 0)) + _cou,
                                            "system")
                    elif et == "model_escalated":
                        self.thread_msg(t("chat.escalated", model=ev.get("to", "?")), "system")
                    elif et == "model_unavailable":
                        _fb = ev.get("fallback") or ""
                        self.thread_msg(t("chat.model_unavailable_" + str(ev.get("why") or "offline"),
                                          model=ev.get("model", "?"), handle=ev.get("handle", "?"),
                                          fallback=_fb) if _fb else
                                        t("chat.model_unavailable_none",
                                          model=ev.get("model", "?"), handle=ev.get("handle", "?")),
                                        "system")
                    elif et == "escalate_failed":
                        self.thread_msg(t("chat.escalate_failed", why=ev.get("why", "")), "system")
                    elif et == "metacog_close":
                        self.thread_msg(t("chat.metacog_close"), "system")
                    elif et == "qa_skipped":
                        self.thread_msg(t("chat.qa_skipped_self"), "system")
                    elif et == "image_described":
                        self.thread_msg(t("chat.image_described", by=ev.get("by", "?"),
                                          to=ev.get("for", "?"), n=ev.get("n", 1)), "system")
                    elif et == "image_not_seen":
                        self.thread_msg(t("chat.image_not_seen_server" if ev.get("server")
                                          else "chat.image_not_seen",
                                          model=ev.get("model", "?"), path=ev.get("path", "")),
                                        "system")
                    elif et == "tokens":
                        pt = ev.get("prompt_tokens")
                        if pt:
                            self._last_prompt_tokens = pt
                            try:
                                _tcx = self._turn_ctx()
                                _tcx.ctx_estimated = bool(ev.get("estimated"))
                                if int(ev.get("context_limit") or 0) > 0:
                                    _tcx.context_limit = int(ev["context_limit"])
                            except Exception:
                                pass
                            try:
                                _tc = self._turn_ctx()
                                _tc.last_tokens_iter = int(ev.get("iteration", 1) or 1)
                            except Exception:
                                pass
                            self.signals.token_usage.emit(
                                getattr(self._tls, "tab_id", "") or "", pt)
                            self.signals.token_first.emit(
                                getattr(self._tls, "tab_id", "") or "",
                                pt, int(ev.get("iteration", 1) or 1))
                    elif et == "llm_call":
                        try:
                            self._turn_ctx().last_llm_call = dict(ev)
                        except Exception:
                            pass
                        self._machine_emit("model", model=ev.get("model") or model)
                        self.thread_msg(
                            f"⏳ LLM (MCP) → {ev.get('model') or model} "
                            f"msgs={ev.get('n_messages')} "
                            f"tools={ev.get('n_tools')} payload≈{ev.get('payload_kb', 0)}KB",
                            "system")
                    elif et == "llm_done":
                        dt = ev.get("duration_s", 0)
                        self._machine_emit("model_done", duration=dt)
                        if dt > 10:
                            self.thread_msg(t("msg.llm_slow", dt=f"{dt:.1f}"), "system")
                        else:
                            self.thread_msg(t("msg.llm_responded", dt=f"{dt:.1f}"), "system")
                    elif et == "client_tool_slow":
                        self.thread_msg(t("msg.client_tool_slow",
                                          tool=ev.get("tool", "?"),
                                          waited=ev.get("waited", 0),
                                          ceiling=ev.get("ceiling", 0)),
                                        "system")
                    elif et == "client_tool_call" and ev.get("background"):
                        name = ev.get("tool", "")
                        self.thread_msg(t("bg.started", tool=name,
                                          id=ev.get("background"),
                                          args=str(ev.get("args") or {})[:80]),
                                        "system")
                        threading.Thread(target=_run_background_delegated,
                                         args=(ev,), daemon=True,
                                         name=f"bg-{name}").start()
                    elif et == "client_tool_call":
                        tool_idx += 1
                        name = ev.get("tool", "")
                        _src = self._picture_sources_label(name, ev.get("args") or {})
                        self.thread_msg(f"  {tool_idx}. {name}{_src}({str(ev.get('args') or {})[:80]}...)")
                        self._machine_emit("call", tool=name, args=str(ev.get("args") or {})[:200])
                        self._tu_tools(tool_idx)
                        _sctx = getattr(self, "_chat_ctxs", {}).get(tab)
                        if _sctx is not None:
                            if not hasattr(_sctx, "turn_steps"):
                                _sctx.turn_steps = []
                            _sctx.turn_steps.append(
                                {"step": tool_idx, "action": name,
                                 "detail": str(ev.get("args") or {})[:200],
                                 "t0": time.time()})
                        threading.Thread(target=_run_delegated, args=(ev,),
                                         daemon=True,
                                         name=f"tool-{name}").start()
                    elif et == "tool_call" and ev.get("tool") not in (
                            "intent", "plan_and_orchestrate", "call_council"):
                        tool_idx += 1
                        name = ev.get("tool", "")
                        self.thread_msg(f"  {tool_idx}. {name}({ev.get('args_preview') or ''})")
                        self.thread_msg(f"🔗 MCP ⟲ {name}", "system")
                        self._machine_emit("call", tool=name, args=str(ev.get("args_preview") or "")[:200])
                        self._tu_tools(tool_idx)
                        _sctx = getattr(self, "_chat_ctxs", {}).get(tab)
                        if _sctx is not None:
                            if not hasattr(_sctx, "turn_steps"):
                                _sctx.turn_steps = []
                            _sctx.turn_steps.append(
                                {"step": tool_idx, "action": name,
                                 "detail": str(ev.get("args_preview") or "")[:200],
                                 "t0": time.time()})
                    elif et == "thought":
                        self.thread_msg(str(ev.get("text") or ""), "thought")
                        self._machine_emit("thought", chars=len(str(ev.get("text") or "")))
                    elif et == "tools_withheld":
                        _fw = [str(n) for n in (ev.get("firewall") or [])]
                        _rl = [str(n) for n in (ev.get("tools") or [])
                               if str(n) not in _fw]
                        if _fw:
                            self.thread_msg(t("msg.tools_withheld_fw",
                                              names=", ".join(_fw)), "system")
                        if _rl:
                            self.thread_msg(t("msg.tools_withheld_role",
                                              names=", ".join(_rl)), "system")
                    elif et == "tool_result":
                        if ev.get("delegated") is False:
                            try:
                                _sc2 = getattr(self, "_chat_ctxs", {}).get(tab)
                                for _p in reversed(getattr(_sc2, "turn_steps", []) or []):
                                    if _p.get("action") == ev.get("tool") and not _p.get("done"):
                                        _p["done"] = True
                                        _p["duration"] = float(ev.get("duration_s") or 0)
                                        _p["ok"] = not str(ev.get("result_preview", "")) \
                                            .lstrip().startswith(('{"error"', "❌"))
                                        break
                            except Exception:
                                pass
                            self._machine_emit(
                                "result", tool=ev.get("tool", ""),
                                ok=not str(ev.get("result_preview", "")).lstrip()
                                .startswith(('{"error"', "❌")),
                                duration=float(ev.get("duration_s") or 0))
                        if ev.get("tool") not in ("intent", "load_tools"):
                            _tcx = self._turn_ctx()
                            _tcx.turn_tools_used = getattr(_tcx, "turn_tools_used", 0) + 1
                        _prev = str(ev.get("result_preview", ""))
                        if "🛡 FIREWALL" in _prev:
                            _fw = re.search(r"FIREWALL: '([^']+)'", _prev)
                            self.thread_msg(t("tools.blocked_run",
                                              tool=(_fw.group(1) if _fw
                                                    else ev.get("tool", "?"))),
                                            "error")
                        elif ev.get("delegated") is False and ev.get("tool") not in (
                                "intent", "plan_and_orchestrate", "call_council"):
                            _failed = _prev.lstrip().startswith(('{"error"', "❌"))
                            self.thread_msg(
                                f"{'❌' if _failed else '✅'} {ev.get('tool', '?')} "
                                f"[{float(ev.get('duration_s') or 0):.1f}s]: {_prev[:200]}",
                                "error" if _failed else "system")
                    elif et == "reply":
                        if not (payload.get("orchestrate")
                                or payload.get("council")):
                            self.thread_msg(t("chat.completed", n=ev.get("iterations", 0)))
                        _sc = getattr(self, "_chat_ctxs", {}).get(tab)
                        if _sc is not None:
                            _sc.turn_record = ev.get("turn_record", "") or ""
                            _sc.turn_transcript = ev.get("transcript") or []
                            _sc.turn_last_user = ev.get("last_user_sent")
                        return ev.get("reply", "")
                    elif et == "cancelled":
                        _was_stopped["v"] = True
                        _sc = getattr(self, "_chat_ctxs", {}).get(tab)
                        if _sc is not None:
                            _sc.turn_record = ev.get("turn_record", "") or ""
                            _sc.turn_last_user = ev.get("last_user_sent")
                        self.thread_msg(t("msg.task_stopped_user"), "system", keep=True)
                        return "[The user stopped the task before it was completed.]"
                    elif et == "iteration_limit":
                        _sc = getattr(self, "_chat_ctxs", {}).get(tab)
                        if _sc is not None:
                            _sc.turn_record = ev.get("turn_record", "") or ""
                            _sc.turn_last_user = ev.get("last_user_sent")
                        return t("chat.iteration_limit",
                                 n=ev.get("iterations", max_iterations))
                    elif et == "context_overflow":
                        _oc = getattr(self, "_chat_ctxs", {}).get(tab)
                        if _oc is not None:
                            _oc.compact_pending = True
                            if not getattr(_oc, "overflow_replayed", False):
                                _oc.overflow_replay = True
                            self.signals.queue_changed.emit(tab or "")
                        self.thread_msg(t("ctx.overflow_compact", prompt=ev.get("prompt", "?"),
                                          reply=ev.get("reply", "?"), window=ev.get("window", "?")),
                                        "system", keep=True)
                    elif et == "error":
                        err = ev.get("error", "?")
                        _sc = getattr(self, "_chat_ctxs", {}).get(tab)
                        if _sc is not None:
                            _sc.turn_last_user = ev.get("last_user_sent")
                            _sc.turn_transcript = ev.get("transcript") or []
                        self.thread_msg(t("msg.llm_net_err", e=err), "error")
                        raise Exception(f"MCP loop: {err}")
        except requests.exceptions.RequestException as e:
            self.thread_msg(t("msg.llm_net_err", e=e), "error")
            raise
        finally:
            self._tls.council_tab = None
            self.signals.cancel_ack_tab.emit(tab)
            try:
                self._turn_ctx().turn_id = None
            except Exception:
                pass
        raise Exception("The /agent/turn stream closed without a terminal event")


    def _sweep_stored_records(self):
        import fed_agent as _fa_rec
        n = 0
        for i, m in enumerate(self.conversation_history):
            c = m.get("content") if isinstance(m, dict) else None
            if m.get("role") != "assistant" or not isinstance(c, str):
                continue
            for s_ in _fa_rec.RECORD_SENTINELS:
                j = c.find(s_)
                if j >= 0:
                    self.conversation_history[i] = {**m, "content": c[:j].rstrip() or "…"}
                    n += 1
                    break
        if n:
            print(f"[HISTORY] {n} stored answer(s) carried a turn record: cut once", file=sys.stderr)
        return n

    @staticmethod
    def _strip_old_records(history):
        import fed_agent as _fedagent_mod
        out = [dict(m) for m in history]
        last_a = next((i for i in range(len(out) - 1, -1, -1)
                       if out[i].get("role") == "assistant"), -1)
        for i, m in enumerate(out):
            if i == last_a or m.get("role") != "assistant":
                continue
            c = m.get("content", "")
            if not isinstance(c, str):
                continue
            for s_ in _fedagent_mod.RECORD_SENTINELS:
                j = c.find(s_)
                if j >= 0:
                    m["content"] = c[:j].rstrip()
                    break
        return out

    def call_normal_ai(self, user_message):
        self._current_instruction = user_message
        _ctx = self._turn_ctx()
        _pin, _remote = self._tab_runs_remotely(_ctx)
        if _remote:
            self.thread_msg(t("pin.running_on", name=_pin.get("name", "?")), "system")
        try:
            context_parts = []
            _sticky = (self.sticky or "").strip()
            if _sticky:
                context_parts.append(
                    "\n[STICKY NOTE — the working note of this conversation. "
                    "The user reads and EDITS it, so what is in here is agreed "
                    "and beats your memory of it. Keep it current with "
                    "sticky_note when something gets settled.]\n" + _sticky)
            for uctx in self.user_fixed_context:
                context_parts.append("\n[USER FIXED]\n" + uctx["content"])
            for ctx in self.fixed_context:
                if ctx.get("is_fixed"):
                    h = f"\n[{ctx['type'].upper()}]"
                    if ctx.get("focus") and ctx["focus"] != "general": h += f" - {ctx['focus']}"
                    elif ctx.get("query"): h += f" - {ctx['query']}"
                    context_parts.append(h + "\n" + ctx["content"])
            self._persist_briefing_blocks(context_parts)
            if self.config.getboolean("CHAT", "history_transcript", fallback=True):
                self._sweep_stored_records()
                enhanced = list(self.conversation_history)
            else:
                enhanced = self._strip_old_records(self.conversation_history)
            self._task_begin()
            try:
                response = self.call_chat_with_tools(enhanced)
                if response:
                    clean = self.clean_ai_response(response)
                    _trace = (getattr(_ctx, "turn_record", "")
                               or self._turn_trace_note())
                    _hist = clean + (("\n\n" + _trace) if _trace else "")
                    _tail = self._history_transcript(_ctx)
                    if self.config.getboolean("CHAT", "history_transcript", fallback=True):
                        _hist = clean
                    self._store_question_as_sent(_ctx)
                    self.conversation_history.extend(_tail)
                    self.conversation_history.append(
                        {"role": "assistant", "content": _hist})
                    self.thread_msg(clean, "assistant")
            finally:
                if not getattr(_ctx, "overflow_replay", False):
                    _ctx.overflow_replayed = False
                if getattr(_ctx, "compact_pending", False):
                    _ctx.compact_pending = False
                    _ctx.compacting = True
                    self.signals.queue_changed.emit(
                        getattr(self._tls, "tab_id", "") or "")
                    _replay = getattr(_ctx, "overflow_replay", False)
                    _q = None
                    if _replay:
                        _hi = next((i for i in range(len(self.conversation_history) - 1, -1, -1)
                                    if self.conversation_history[i].get("role") == "user"), -1)
                        if _hi >= 0:
                            _q = self.conversation_history.pop(_hi)
                    _ctx.compacted_ok = False
                    try:
                        self._auto_compact()
                    except Exception:
                        _ctx.compacting = False
                    if _replay:
                        _ctx.overflow_replay = False
                        _ctx.overflow_handled = True
                        if _q is not None and getattr(_ctx, "compacted_ok", False):
                            _ctx.overflow_replayed = True
                            _ctx.msg_queue.insert(0, self._question_to_queue(_q))
                            self.thread_msg(t("ctx.overflow_replay"), "system")
                        elif _q is not None:
                            self.conversation_history.append(_q)
                self._task_end()
        except Exception as e:
            if getattr(_ctx, "overflow_handled", False):
                _ctx.overflow_handled = False
                return
            self.thread_msg(t("msg.error", e=str(e)), "error")
            try:
                self._store_question_as_sent(_ctx)
            except Exception:
                pass
            try:
                if self.config.getboolean("CHAT", "history_transcript", fallback=True):
                    self.conversation_history.extend(self._history_transcript(_ctx))
                    self.conversation_history.append(
                        {"role": "assistant", "content": f"[Turn FAILED: {str(e)[:200]}]"})
                else:
                    _trace = (getattr(_ctx, "turn_record", "")
                               or self._turn_trace_note(failed=str(e)))
                    if _trace:
                        self.conversation_history.append(
                            {"role": "assistant",
                             "content": f"[Turn FAILED: {str(e)[:200]}]\n\n"
                                        + _trace})
            except Exception:
                pass
        finally:
            self._on_turn_complete()

    def search_with_rag(self, query):
        try:
            self.thread_msg(t("msg.searching_rag", query=query))
            raw_results = self.rag_manager.search_in_raw_text(query)
            try:
                semantic = self.rag_manager.search_similar_chunks(query, k=self.config.getint("RAG", "search_k", fallback=5), exclude_used=True)
            except Exception:
                semantic = []
            noise = self.config.getfloat("RAG", "min_similarity_score",
                                         fallback=0.18)
            _best = max([c["score"] for c in semantic] or [0.0])
            _floor = max(noise, _best * 0.5) if _best >= noise else float("inf")
            filtered = [c for c in semantic if c["score"] >= _floor]
            if filtered: self.rag_manager.mark_chunks_as_used([c["chunk_index"] for c in filtered]); self.signals.update_rag_status.emit()
            context = self.get_enhanced_context_for_query(query, raw_results, filtered)
            if raw_results or filtered or context:
                enhanced = [{"role": "system", "content": f"INFORMATION:\n{context}"}, {"role": "user", "content": query}]
                response = self.call_chat(enhanced, stream=False)
                if response:
                    clean = self.clean_ai_response(response)
                    self.fixed_context.append({"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "type": "rag_search", "query": query, "content": clean, "is_fixed": True, "priority": "high"})
                    self.thread_msg(clean, "rag")
            else: self.thread_msg(t("msg.no_rag_info"), "error")
        except Exception as e: self.thread_msg(t("msg.error_rag", e=str(e)), "error")

    def get_enhanced_context_for_query(self, query, raw_results=None, semantic_chunks=None, max_len=4000):
        parts = []
        if raw_results:
            parts.append("=== RAW ===")
            for r in raw_results[:3]: parts.append(f"[{r['source']}] '{r['term']}'\n{r['context']}")
        if semantic_chunks:
            parts.append("=== SEMANTIC ===")
            for c in semantic_chunks[:3]: parts.append(f"[{c['metadata'].get('source','')}] {c['score']:.3f}\n{c['text']}")
        hits = "\n".join(parts)
        if len(hits) > max_len:
            return hits[:max_len]
        fixed = self.get_all_fixed_context_text()
        if fixed:
            room = max_len - len(hits)
            return (fixed[:room] + "\n" + hits) if hits else fixed[:max_len]
        return hits

    def search_with_perplexity(self, query):
        try:
            raw = self._mcp_call("search_web", {"query": query})
            if not raw:
                self.thread_msg("❌ " + t("msg.no_search_result"), "error")
                return
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                data = {"error": str(raw)[:300]}
            if isinstance(data, dict) and "error" in data:
                self.thread_msg(f"❌ {data['error']}", "error")
                return
            response = data.get("result", "") if isinstance(data, dict) else str(data)
            citations = data.get("citations", []) if isinstance(data, dict) else []
            if response:
                clean = self.clean_ai_response(response)
                if citations: clean += "\n\n📚 Fuentes:\n" + "\n".join(f"[{i}] {u}" for i, u in enumerate(citations, 1))
                try:
                    self.rag_manager.add_raw_text(clean, f"Perplexity: {query}", {"search_query": query, "citations": citations})
                    chunks = self.rag_manager.create_chunks(clean, source=f"Perplexity: {query}",
                        chunk_size=self.config.getint("RAG", "chunk_size", fallback=200), overlap=self.config.getint("RAG", "chunk_overlap", fallback=50))
                    if chunks: self.rag_manager.add_chunks_to_index(chunks, f"Perplexity: {query}"); self.signals.update_rag_status.emit()
                except: pass
                self.thread_msg(clean, "search")
        except Exception as e: self.thread_msg(t("msg.error", e=str(e)), "error")

    def chat_with_todo(self, user_query):
        try:
            dt = datetime.now(); self.thread_msg(f"⏰ {dt.strftime('%Y-%m-%d %H:%M:%S')}")
            rag_urls = self.todo_manager.get_all_rag_urls()
            if rag_urls:
                self.thread_msg(t("msg.todo_urls", n=len(rag_urls)))
                for i, url in enumerate(rag_urls, 1):
                    try:
                        self.thread_msg(f"Scraping ({i}/{len(rag_urls)}): {url}")
                        content = self._scrape_url(url)
                        if content:
                            self.rag_manager.add_raw_text(content, f"TODO: {url}", {"todo_source": True})
                            chunks = self.rag_manager.create_chunks(content, source=f"TODO: {url}",
                                chunk_size=self.config.getint("RAG", "chunk_size", fallback=200), overlap=self.config.getint("RAG", "chunk_overlap", fallback=50))
                            if chunks: self.rag_manager.add_chunks_to_index(chunks, f"TODO: {url}")
                    except Exception as e: self.thread_msg(t("err.url_n", i=i, e=str(e)), "error")
                self.signals.update_rag_status.emit()
            todo_ctx = self.todo_manager.get_context_text(); fixed = self.get_all_fixed_context_text()
            full_q = user_query if user_query else "Summarize my appointments and tasks"
            enhanced = [{"role": "system", "content": f"TODO CONTEXT ({dt.strftime('%Y-%m-%d %H:%M:%S')}):\n{todo_ctx}\n{fixed}"},
                        {"role": "user", "content": full_q}]
            response = self.call_chat(enhanced, stream=False)
            if response:
                clean = self.clean_ai_response(response)
                self.fixed_context.append({"timestamp": dt.strftime("%Y-%m-%d %H:%M:%S"), "type": "todo_query",
                    "query": full_q, "content": clean, "is_fixed": True, "priority": "high"})
                self.thread_msg(clean, "todo")
        except Exception as e: self.thread_msg(t("msg.error_todo", e=str(e)), "error")


    def generate_hierarchical_summary(self, focus=None):
        try:
            raw_texts = self.rag_manager.get_raw_texts_for_summary()
            if not raw_texts: self.thread_msg(t("msg.no_raw_texts"), "error"); return
            self.thread_msg(t("rag.generating_summary", n=len(raw_texts), focus=("  - "+focus if focus else "")))
            all_summaries = []
            for i, raw in enumerate(raw_texts, 1):
                source = raw.get("source", f"Source {i}"); content = raw.get("content", "")
                self.thread_msg(f"Summarizing {i}/{len(raw_texts)}: {source}")
                blocks = self._fragment_text(content); block_sums = []
                for j, block in enumerate(blocks, 1):
                    focus_str = (": " + focus) if focus else ""
                    prompt = t("prompts.summarizer", focus=focus_str, block=block)
                    s = self.call_chat_summary(prompt)
                    if s: block_sums.append(s)
                if block_sums:
                    if len(block_sums) == 1: src_sum = block_sums[0]
                    else:
                        combined = "\n\n".join(f"BLOCK {k+1}:\n{s}" for k, s in enumerate(block_sums))
                        prompt = t("prompts.summarizer_combine", source=source, combined=combined)
                        src_sum = self.call_chat_summary(prompt)
                    if src_sum: all_summaries.append({"source": source, "summary": src_sum})
            if all_summaries:
                if len(all_summaries) == 1: final = all_summaries[0]["summary"]
                else:
                    combined = "\n\n".join(f"SOURCE: {s['source']}\n{s['summary']}" for s in all_summaries)
                    focus_str = (" " + focus) if focus else ""
                    prompt = t("prompts.summarizer_final", focus=focus_str, combined=combined)
                    final = self.call_chat_summary(prompt)
                if final:
                    clean = self.clean_ai_response(final); ctx_type = "resumen_enfocado" if focus else "resumen_general"
                    self.fixed_context = [c for c in self.fixed_context if not (c.get("type") == ctx_type and c.get("focus") == (focus or "general"))]
                    self.fixed_context.append({"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "type": ctx_type, "focus": focus or "general", "content": clean,
                        "sources_processed": len(raw_texts), "is_fixed": True, "priority": "high"})
                    self.thread_msg(t("rag.summary_result", focus=(": "+focus.upper() if focus else ""), n=len(raw_texts), text=clean), "summary")
        except Exception as e: self.thread_msg(t("msg.error_summary", e=str(e)), "error")

    def _fragment_text(self, text, max_words=10000):
        words = text.split()
        return [" ".join(words[i:i+max_words]) for i in range(0, len(words), max_words)]


    def _reread_note(self, arguments):
        return None
        path = (arguments or {}).get("path") or ""
        p = os.path.expanduser(str(path).strip())
        if not p or not os.path.isfile(p):
            return None
        ctx = self._turn_ctx()
        seen = getattr(ctx, "reread", None)
        if seen is None:
            seen = ctx.reread = {}
        seen[p] = seen.get(p, 0) + 1
        if seen[p] < 2:
            return None
        try:
            n = os.path.getsize(p)
        except OSError:
            n = 0
        if seen[p] == 2:
            self.thread_msg(t("code.reread_note", path=os.path.basename(p),
                              n=n), "system")
        return (f"[NOTE] {os.path.basename(p)} was already read in this turn "
                f"(read #{seen[p]}). The whole file ({n} chars) is NOT re-sent: "
                "the previous result stands above. For other lines use "
                "code_read_range(path, start_line, end_line); for a symbol use "
                "code_search. Re-reading the same file again yields nothing new.")

    def pin_file_to_context(self, file_path):
        p = os.path.expanduser((file_path or "").strip())
        if not p:
            self.thread_msg(t("inc.need_path"), "error"); return
        if not os.path.isfile(p):
            self.thread_msg(t("err.not_found_path", path=p), "error"); return
        try:
            raw = open(p, "rb").read()
        except Exception as e:
            self.thread_msg(t("msg.error", e=str(e)), "error"); return
        if b"\0" in raw[:8000]:
            self.thread_msg(t("inc.binary", path=os.path.basename(p)), "error"); return
        text = raw.decode("utf-8", errors="replace")
        cap = 200_000
        cut = len(text) > cap
        if cut:
            text = text[:cap]
        self.add_user_fixed_context(
            f"===== {os.path.basename(p)} ({p}) =====\n{text}")
        self.thread_msg(
            t("inc.pinned_cut", path=os.path.basename(p), n=cap) if cut
            else t("inc.pinned", path=os.path.basename(p), n=len(text)), "fixed")

    def process_include_file(self, file_path):
        try:
            if not os.path.exists(file_path): self.thread_msg(t("err.not_found_path", path=file_path), "error"); return
            with open(file_path, "r", encoding="utf-8") as f: content = f.read()
            urls = re.findall(r'https?://[^\s<>"\']+[^\s<>"\',.]', content)
            if not urls: self.thread_msg(t("msg.no_urls"), "error"); return
            _cap = self.config.getint("CRAWL4AI", "max_urls", fallback=10)
            if _cap > 0 and len(urls) > _cap:
                self.thread_msg(t("rag.urls_capped", n=len(urls), cap=_cap), "system")
                urls = urls[:_cap]
            self.thread_msg(t("rag.urls_found", n=len(urls))); all_chunks = []
            for i, url in enumerate(urls, 1):
                try:
                    self.thread_msg(f"({i}/{len(urls)}): {url}"); scraped = self._scrape_url(url)
                    if scraped:
                        self.rag_manager.add_raw_text(scraped, url, {"file_source": file_path})
                        chunks = self.rag_manager.create_chunks(scraped, source=url,
                            chunk_size=self.config.getint("RAG", "chunk_size", fallback=200), overlap=self.config.getint("RAG", "chunk_overlap", fallback=50))
                        all_chunks.extend(chunks)
                except Exception as e: self.thread_msg(t("err.url_generic", url=url, e=str(e)), "error")
            if all_chunks:
                self.rag_manager.add_chunks_to_index(all_chunks, f"File: {file_path}")
                self.thread_msg(f"✓ {len(all_chunks)} chunks", "rag"); self.signals.update_rag_status.emit()
        except Exception as e: self.thread_msg(t("msg.error", e=str(e)), "error")

    def process_single_url(self, url):
        try:
            self.thread_msg(f"Scraping: {url}"); content = self._scrape_url(url)
            if content:
                self.rag_manager.add_raw_text(content, url, {"scraped_at": datetime.now().isoformat()})
                chunks = self.rag_manager.create_chunks(content, source=url,
                    chunk_size=self.config.getint("RAG", "chunk_size", fallback=200), overlap=self.config.getint("RAG", "chunk_overlap", fallback=50))
                if chunks:
                    self.rag_manager.add_chunks_to_index(chunks, url)
                    self.thread_msg(f"✓ {len(chunks)} chunks + RAW", "rag"); self.signals.update_rag_status.emit()
            else:
                self.thread_msg(t("inc.url_empty", url=url), "error")
        except Exception as e: self.thread_msg(t("msg.error", e=str(e)), "error")

    def process_text_file(self, file_path):
        try:
            if not os.path.exists(file_path):
                self.thread_msg(t("err.not_found_path", path=file_path), "error"); return
            try:
                with open(file_path, "r", encoding="utf-8") as f: content = f.read().strip()
            except UnicodeDecodeError:
                with open(file_path, "r", encoding="latin-1") as f: content = f.read().strip()
            if not content:
                self.thread_msg(t("msg.empty_file"), "error"); return
            name = os.path.basename(file_path)
            self.rag_manager.add_raw_text(content, f"File: {name}", {"file_path": file_path})
            chunks = self.rag_manager.create_chunks(content, source=f"File: {name}",
                chunk_size=self.config.getint("RAG", "chunk_size", fallback=200), overlap=self.config.getint("RAG", "chunk_overlap", fallback=50))
            if chunks:
                self.rag_manager.add_chunks_to_index(chunks, f"File: {file_path}")
                self.thread_msg(t("rag.chunks_added", name=name, chunks=len(chunks), words=len(content.split())), "rag")
                self.signals.update_rag_status.emit()
            else:
                self.thread_msg(t("rag.raw_only", name=name,
                                  words=len(content.split())), "error")
        except Exception as e:
            self.thread_msg(t("msg.error", e=str(e)), "error")

    def load_notes_to_rag(self, category_name):
        try:
            self.thread_msg(t("msg.loading_notes", cat=category_name))
            if category_name not in self.notes_manager.get_categories(): self.thread_msg(t("msg.cat_not_found", cat=category_name), "error"); return
            text = self.notes_manager.get_all_notes_from_category(category_name)
            if not text: self.thread_msg(t("msg.no_notes_in", cat=category_name), "error"); return
            self.rag_manager.add_raw_text(text, f"Notes: {category_name}", {"category": category_name, "source_type": "notes"})
            chunks = self.rag_manager.create_chunks(text, source=f"Notas/{category_name}",
                chunk_size=self.config.getint("RAG", "chunk_size", fallback=200), overlap=self.config.getint("RAG", "chunk_overlap", fallback=50))
            if chunks:
                self.rag_manager.add_chunks_to_index(chunks, f"Notes: {category_name}")
                self.thread_msg(t("rag.chunks_from_cat", chunks=len(chunks), cat=category_name), "rag"); self.signals.update_rag_status.emit()
        except Exception as e: self.thread_msg(t("msg.error", e=str(e)), "error")


    def _extract_keywords(self, term):
        clean = re.sub(r'[^\w\s]', ' ', term.lower())
        stops = {'de','la','el','en','y','a','que','es','se','no','un','una','con','por','para','como','del','las','los'}
        return [w for w in clean.split() if len(w) > 2 and w not in stops]

    def _score_note_by_keywords(self, note, keywords):
        title = note.get('title', '').lower(); content = note.get('content', '').lower()
        return sum(title.count(k)*3 + content.count(k) for k in keywords)

    def _search_notes_hybrid(self, search_term, specific_category=None):
        keywords = self._extract_keywords(search_term)
        if not keywords: return [], "none"
        results = []
        cats = [specific_category] if specific_category else self.notes_manager.get_categories()
        for cat in cats:
            for note in self.notes_manager.get_notes(cat):
                score = self._score_note_by_keywords(note, keywords)
                if score > 0: results.append({'category': cat, 'note': note, 'score': score})
        results.sort(key=lambda x: x['score'], reverse=True)
        if results: return results[:5], "keywords"
        rag_results = self.rag_manager.search_similar_chunks(search_term, k=5, exclude_used=False)
        note_chunks = [r for r in rag_results if 'Notas/' in r['metadata'].get('source', '')]
        if note_chunks: return note_chunks, "semantic"
        return [], "none"


    def on_upload_clicked(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select file", "", t("dlg.pdf_filter"))
        if not path: return
        prompt, ok = QInputDialog.getText(self, t("dlg.ai_instructions"), t("dlg.ai_instructions_label"))
        custom = prompt.strip() if ok and prompt.strip() else None
        threading.Thread(target=self.process_uploaded_file, args=(path, custom), daemon=True).start()

    def process_uploaded_file(self, filepath, custom_prompt=None):
        try:
            name = os.path.basename(filepath); ext = os.path.splitext(name)[1].lower()
            self.thread_msg(t("rag.processing", name=name))
            if custom_prompt: self.thread_msg(t("msg.instruction", p=custom_prompt))
            extracted_text = None
            if ext == '.pdf': extracted_text = self.process_pdf_with_ai(filepath, custom_prompt)
            elif ext in ['.jpg', '.jpeg', '.png']: extracted_text = self.process_image_with_ai(filepath, custom_prompt)
            else: self.thread_msg(t("msg.format_unsupported", ext=ext), "error"); return
            if extracted_text:
                self.signals.update_rag_status.emit()
                _cap = 120000
                _body = extracted_text
                if len(_body) > _cap:
                    _body = _body[:_cap] + "\n\n" + t("file.truncated",
                                                         n=len(extracted_text) - _cap, name=name)
                _instr = custom_prompt or t("file.default_instruction")
                self.thread_msg(t("rag.added_context", name=name))
                self.signals.send_text.emit(t("file.turn_message", instr=_instr,
                                              name=name, text=_body))
            else: self.thread_msg(t("msg.cant_extract", name=name), "error")
        except Exception as e: self.thread_msg(t("msg.error", e=str(e)), "error")

    def process_image_with_ai(self, filepath, custom_prompt=None):
        try:
            with open(filepath, 'rb') as f: image_data = base64.b64encode(f.read()).decode('utf-8')
            ext = os.path.splitext(filepath)[1].lower()
            media = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png'}.get(ext, 'image/jpeg')
            text = self.extract_text_from_image(image_data, media, custom_prompt)
            if text and text.strip():
                name = os.path.basename(filepath)
                self.rag_manager.add_raw_text(text, name, {"type": "image_ai", "custom_prompt": custom_prompt or "auto"})
                chunks = self.rag_manager.create_chunks(text, source=name,
                    chunk_size=self.config.getint("RAG", "chunk_size", fallback=200), overlap=self.config.getint("RAG", "chunk_overlap", fallback=50))
                if chunks: self.rag_manager.add_chunks_to_index(chunks, name); self.thread_msg(t("rag.words_chunks", words=len(text.split()), chunks=len(chunks)))
                return text
            return None
        except Exception as e: self.thread_msg(t("msg.error_image", e=str(e)), "error"); return None

    def process_pdf_with_ai(self, filepath, custom_prompt=None):
        try:
            import PyPDF2; from io import BytesIO
            name = os.path.basename(filepath); text_content = []; images_found = []
            with open(filepath, 'rb') as file:
                pdf = PyPDF2.PdfReader(file)
                for i, page in enumerate(pdf.pages, 1):
                    text = page.extract_text()
                    if text and text.strip(): text_content.append(f"[{t('lbl.page')} {i}]\n{text}")
                    try:
                        if '/XObject' in page['/Resources']:
                            xobj = page['/Resources']['/XObject'].get_object()
                            for obj_name in xobj:
                                obj = xobj[obj_name]
                                if obj['/Subtype'] == '/Image':
                                    try:
                                        from PIL import Image
                                        size = (obj['/Width'], obj['/Height']); data = obj.get_data()
                                        mode = "RGB" if obj['/ColorSpace'] == '/DeviceRGB' else "P"
                                        img = Image.frombytes(mode, size, data); buf = BytesIO()
                                        img.convert('RGB').save(buf, format='JPEG', quality=85)
                                        images_found.append({'page': i, 'data': base64.b64encode(buf.getvalue()).decode('utf-8')})
                                    except: pass
                    except: pass
            full_text = "\n\n".join(text_content)
            if images_found:
                self.thread_msg(t("msg.images_found", n=len(images_found))); img_texts = []
                for idx, img in enumerate(images_found, 1):
                    txt = self.extract_text_from_image(img['data'], 'image/jpeg', custom_prompt)
                    if txt: img_texts.append(f"[{t('lbl.image')} {idx} - {t('lbl.pg')} {img['page']}]\n{txt}")
                if img_texts: full_text += "\n\n=== IMAGES ===\n" + "\n\n".join(img_texts)
            if not full_text.strip(): return None
            self.rag_manager.add_raw_text(full_text, name, {"type": "pdf_hybrid", "images": len(images_found)})
            chunks = self.rag_manager.create_chunks(full_text, source=name,
                chunk_size=self.config.getint("RAG", "chunk_size", fallback=200), overlap=self.config.getint("RAG", "chunk_overlap", fallback=50))
            if chunks: self.rag_manager.add_chunks_to_index(chunks, name); self.thread_msg(t("rag.words_chunks_imgs", words=len(full_text.split()), chunks=len(chunks), imgs=len(images_found)))
            return full_text
        except ImportError as e: self.thread_msg(f"Falta: {str(e)}\npip install PyPDF2 Pillow --break-system-packages", "error"); return None
        except Exception as e: self.thread_msg(t("msg.error_pdf", e=str(e)), "error"); return None

    def extract_text_from_image(self, image_b64, media_type, custom_prompt=None):
        import hoplita_llmcfg
        _bu, _bk, _bm = self._resolve_endpoint("API")
        if not hoplita_llmcfg.model_sees(self.config, _bu, _bm):
            api_model = self._base_model_name()
            raise Exception(t("warn.vision_off", model=api_model))

        prompt = custom_prompt or t("prompts.analyze_image")
        base_url, api_key, model = self._resolve_endpoint("API")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        messages = [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": f"data:{media_type};base64,{image_b64}"}},
        ]}]
        payload = {"model": model, "messages": messages, "max_tokens": self._max_tokens()}
        response = self._post_chat_completions(base_url, headers, payload, 120)
        return self.clean_ai_response(response.json()["choices"][0]["message"]["content"])


    def _setup_cron_page(self, parent_layout):
        header = QHBoxLayout()
        header.setContentsMargins(16, 12, 16, 12)
        header.addWidget(QLabel(f"<b>{t('cron.title')}</b>"))
        header.addStretch()
        ob = QPushButton(t("cron.open_tab"))
        ob.setToolTip(t("cron.open_tab_tip"))
        ob.clicked.connect(lambda: (self._ensure_cron_tab(),
                                    self._switch_chat_tab(CRON_TAB_ID)))
        header.addWidget(ob)
        pb = QPushButton(t("cron.purge_btn"))
        pb.setToolTip(t("cron.purge_tip"))
        pb.clicked.connect(lambda: self._purge_cron_history(confirm=True))
        header.addWidget(pb)
        rb = QPushButton(t("cron.update")); rb.clicked.connect(self.refresh_cron_list)
        header.addWidget(rb); parent_layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        parent_layout.addWidget(splitter, 1)

        fw = QWidget(); fw.setMaximumWidth(450); fl = QVBoxLayout(fw)
        self.cron_form_label = QLabel(f"<b>{t('cron.new_job')}</b>")
        fl.addWidget(self.cron_form_label)
        self.cron_name_entry = QLineEdit(); self.cron_name_entry.setPlaceholderText(t("gui.cron_job_name"))
        fl.addWidget(self.cron_name_entry)
        self.cron_instruction_text = QTextEdit()
        self.cron_instruction_text.setAcceptRichText(False)
        self.cron_instruction_text.setPlaceholderText(t("ui.cron_instruction_ph"))
        self.cron_instruction_text.setMaximumHeight(100); fl.addWidget(self.cron_instruction_text)

        tg = QGroupBox(t("grp.scheduling")); tl = QVBoxLayout(tg)
        self.cron_type_combo = QComboBox()
        for _code, _key in (("interval", "cron.type_interval"), ("daily", "cron.type_daily"),
                            ("weekly", "cron.type_weekly"), ("fixed", "cron.type_fixed"),
                            ("startup", "cron.type_startup")):
            self.cron_type_combo.addItem(t(_key), _code)
        self.cron_type_combo.currentIndexChanged.connect(
            lambda _i: self._on_cron_type_changed(self.cron_type_combo.currentData()))
        tl.addWidget(self.cron_type_combo)

        self.cron_interval_widget = QWidget()
        il = QHBoxLayout(self.cron_interval_widget); il.setContentsMargins(0,0,0,0)
        il.addWidget(QLabel(t("cron.every"))); self.cron_interval_spin = QSpinBox()
        self.cron_interval_spin.setRange(1, 10080); self.cron_interval_spin.setValue(30)
        il.addWidget(self.cron_interval_spin); il.addWidget(QLabel("min"))
        tl.addWidget(self.cron_interval_widget)

        self.cron_fixed_widget = QWidget()
        fxl = QHBoxLayout(self.cron_fixed_widget); fxl.setContentsMargins(0,0,0,0)
        self.cron_fixed_entry = QLineEdit(); self.cron_fixed_entry.setPlaceholderText("YYYY-MM-DD HH:MM")
        self.cron_fixed_entry.setReadOnly(True); fxl.addWidget(self.cron_fixed_entry, 1)
        fcb = QPushButton("📅"); fcb.clicked.connect(self._show_cron_calendar); fxl.addWidget(fcb)
        self.cron_fixed_widget.hide(); tl.addWidget(self.cron_fixed_widget)

        self.cron_daily_widget = QWidget()
        dl = QVBoxLayout(self.cron_daily_widget); dl.setContentsMargins(0,0,0,0)
        dl.addWidget(QLabel("Times (HH:MM, one per line):"))
        self.cron_daily_times_text = QTextEdit()
        self.cron_daily_times_text.setAcceptRichText(False)
        self.cron_daily_times_text.setPlaceholderText("09:00\n13:00\n18:00")
        self.cron_daily_times_text.setMaximumHeight(80); dl.addWidget(self.cron_daily_times_text)
        self.cron_daily_widget.hide(); tl.addWidget(self.cron_daily_widget)

        self.cron_weekly_widget = QWidget()
        wl = QVBoxLayout(self.cron_weekly_widget); wl.setContentsMargins(0,0,0,0)
        wl.addWidget(QLabel(t("cron.weekdays_label")))
        days_row = QHBoxLayout()
        self.cron_weekday_checks = []
        day_keys = ["cron.mon", "cron.tue", "cron.wed", "cron.thu",
                    "cron.fri", "cron.sat", "cron.sun"]
        for idx, dk in enumerate(day_keys):
            cb = QCheckBox(t(dk))
            if idx == 0:
                cb.setChecked(True)
            self.cron_weekday_checks.append(cb)
            days_row.addWidget(cb)
        wl.addLayout(days_row)
        hr = QHBoxLayout()
        hr.addWidget(QLabel(t("cron.at_time")))
        self.cron_weekly_time = QLineEdit("09:00")
        self.cron_weekly_time.setPlaceholderText("HH:MM")
        self.cron_weekly_time.setMaximumWidth(80)
        hr.addWidget(self.cron_weekly_time); hr.addStretch()
        wl.addLayout(hr)
        self.cron_weekly_widget.hide(); tl.addWidget(self.cron_weekly_widget)
        fl.addWidget(tg)

        self.cron_alert_check = QCheckBox(t("cron.alert_done")); self.cron_alert_check.setChecked(True)
        fl.addWidget(self.cron_alert_check)
        self.cron_own_tab_check = QCheckBox(t("cron.own_tab"))
        self.cron_own_tab_check.setToolTip(t("cron.own_tab_tip"))
        fl.addWidget(self.cron_own_tab_check)

        tgt_row = QHBoxLayout()
        tgt_row.addWidget(QLabel(t("cron.target")))
        self.cron_target_combo = QComboBox()
        tgt_row.addWidget(self.cron_target_combo, 1)
        fl.addLayout(tgt_row)
        self._populate_cron_targets()

        mdl_row = QHBoxLayout()
        mdl_row.addWidget(QLabel(t("cron.model")))
        self.cron_model_combo = QComboBox()
        self.cron_model_combo.setToolTip(t("cron.model_tip"))
        mdl_row.addWidget(self.cron_model_combo, 1)
        fl.addLayout(mdl_row)
        self._populate_cron_models()

        ctx_row = QHBoxLayout()
        ctx_row.addWidget(QLabel(t("cron.ctx_attach")))
        self.cron_ctx_combo = QComboBox()
        self.cron_ctx_combo.setToolTip(t("cron.ctx_tip"))
        ctx_row.addWidget(self.cron_ctx_combo, 1)
        fl.addLayout(ctx_row)
        _ctxlbl = QLabel(t("cron.ctx_label"))
        _ctxlbl.setWordWrap(True)
        _ctxlbl.setStyleSheet("QLabel { color:#e6edf7; font-size:12px; }")
        fl.addWidget(_ctxlbl)
        self._populate_cron_sessions()

        btn_row = QHBoxLayout()
        self.cron_save_btn = QPushButton(t("cron.add_job")); self.cron_save_btn.clicked.connect(self._save_cron_job)
        btn_row.addWidget(self.cron_save_btn)
        cron_clear_btn = QPushButton(t("cron.new")); cron_clear_btn.clicked.connect(self._clear_cron_form)
        btn_row.addWidget(cron_clear_btn)
        fl.addLayout(btn_row)

        self._editing_cron_job_id = None
        fl.addStretch(); splitter.addWidget(fw)

        rw = QWidget(); rl = QVBoxLayout(rw)
        rl.addWidget(QLabel(f"<b>{t('cron.jobs')}</b>"))
        self.cron_jobs_list = QListWidget()
        self.cron_jobs_list.currentRowChanged.connect(self._on_cron_job_selected)
        rl.addWidget(self.cron_jobs_list, 1)
        rl.addWidget(QLabel(f"<b>{t('cron.log')}</b>"))
        self.cron_log_display = QTextEdit(); self.cron_log_display.setReadOnly(True)
        self.cron_log_display.setFont(QFont("Consolas" if IS_WINDOWS else "Monospace", 10))
        self.cron_log_display.setMaximumHeight(250); rl.addWidget(self.cron_log_display)
        splitter.addWidget(rw)
        self.refresh_cron_list()

    def _on_cron_type_changed(self, stype):
        self.cron_interval_widget.setVisible(stype == "interval")
        self.cron_fixed_widget.setVisible(stype == "fixed")
        self.cron_daily_widget.setVisible(stype == "daily")
        self.cron_weekly_widget.setVisible(stype == "weekly")

    def _show_cron_calendar(self):
        dlg = QDialog(self); dlg.setWindowTitle(t("dlg.datetime")); dlg.setMinimumSize(350, 350)
        lay = QVBoxLayout(dlg); cal = QCalendarWidget(); lay.addWidget(cal)
        tr = QHBoxLayout(); tr.addWidget(QLabel(t("cron.time")))
        hs = QSpinBox(); hs.setRange(0,23); hs.setValue(datetime.now().hour); tr.addWidget(hs)
        tr.addWidget(QLabel(":")); ms = QSpinBox(); ms.setRange(0,59); ms.setSingleStep(5); tr.addWidget(ms)
        lay.addLayout(tr)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject); lay.addWidget(bb)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            d = cal.selectedDate()
            self.cron_fixed_entry.setText(f"{d.year():04d}-{d.month():02d}-{d.day():02d} {hs.value():02d}:{ms.value():02d}")

    def _populate_cron_models(self):
        combo = getattr(self, "cron_model_combo", None)
        if combo is None:
            return
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(t("cron.model_auto"), "")
        combo.addItem("base", "base")
        try:
            for alias in (self._model_pool() or {}):
                combo.addItem(f"🧠 {alias}", alias)
        except Exception:
            pass
        combo.blockSignals(False)

    def _populate_cron_targets(self):
        combo = getattr(self, "cron_target_combo", None)
        if combo is None:
            return
        prev = combo.currentData() if combo.count() else "local"
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(t("cron.target_local"), "local")
        cache = getattr(self, "_fed_cache", {}) or {}
        _kids = {(ch.get("handle") or "") for ch in (cache.get("children") or [])
                 if isinstance(ch, dict)}
        for c in (cache.get("contacts") or []):
            h = c.get("handle", "")
            if not h or (h in _kids and not c.get("local")):
                continue
            if c.get("local"):
                combo.addItem(t("cron.target_local_child", name=h), f"child:{h}")
                continue
            if c.get("status") in ("pending", "pending_incoming"):
                continue
            combo.addItem(f"🌐 {h}", f"friend:{h}")
        for ch in (cache.get("children") or []):
            h = ch.get("handle", "")
            if h:
                combo.addItem(f"🖥 {h}", f"child:{h}")
        idx = combo.findData(prev)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _populate_cron_sessions(self):
        combo = getattr(self, "cron_ctx_combo", None)
        if combo is None:
            return
        prev = combo.currentData() if combo.count() else ""
        ok, r, _ = self._mcp_try("chat_session_list", {}, timeout=15)
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(t("cron.ctx_none"), "")
        for sess in ((r or {}).get("sessions") or []) if ok else []:
            title = (sess.get("title") or "").strip() or sess.get("uuid", "")[:8]
            when = (sess.get("updated_at") or "")[:10]
            combo.addItem(f"{title}   ({when}, {sess.get('n', 0)})",
                          sess.get("uuid", ""))
        i = combo.findData(prev)
        combo.setCurrentIndex(i if i >= 0 else 0)
        combo.blockSignals(False)

    def _save_cron_job(self):
        name = self.cron_name_entry.text().strip()
        instr = self.cron_instruction_text.toPlainText().strip()
        if not name or not instr:
            QMessageBox.warning(self, t("dlg.error"), t("dlg.name_instr_required")); return
        stype = self.cron_type_combo.currentData() or "interval"
        interval = self.cron_interval_spin.value() if stype == "interval" else 0
        fixed_dt = self.cron_fixed_entry.text().strip() if stype == "fixed" else ""
        daily_times = []
        if stype == "daily":
            for line in self.cron_daily_times_text.toPlainText().strip().split("\n"):
                tm = line.strip()
                if re.match(r'^\d{1,2}:\d{2}$', tm): daily_times.append(tm)
        weekdays = []
        if stype == "weekly":
            for idx, cb in enumerate(self.cron_weekday_checks):
                if cb.isChecked():
                    weekdays.append(idx)
            wt = self.cron_weekly_time.text().strip()
            daily_times = [wt] if re.match(r'^\d{1,2}:\d{2}$', wt) else ["09:00"]
        if self.cron_alert_check.isChecked():
            if not instr.startswith("__ALERT_ON_COMPLETE__\n"):
                instr = "__ALERT_ON_COMPLETE__\n" + instr

        tgt = self.cron_target_combo.currentData() or "local"
        _mc = getattr(self, "cron_model_combo", None)
        model = (_mc.currentData() if _mc is not None else "") or ""
        fields = dict(name=name, instr=instr, stype=stype, interval=interval,
                      fixed_dt=fixed_dt, daily_times=daily_times,
                      weekdays=weekdays, tgt=tgt, model=model,
                      own_tab=(tgt == "local"
                               and self.cron_own_tab_check.isChecked()),
                      editing=self._editing_cron_job_id)

        combo = getattr(self, "cron_ctx_combo", None)
        sid = (combo.currentData() if combo is not None else "") or ""
        if not sid:
            self._cron_write_job(None, fields)
            return
        btn = self.cron_save_btn
        _was = btn.text()
        btn.setEnabled(False)

        def work():
            okl, lst, errl = self._mcp_try("chat_session_list", {}, timeout=15)
            if not okl:
                return (False, {}, errl)
            for sess in (lst or {}).get("sessions") or []:
                if sess.get("uuid") == sid:
                    return (True, {"uuid": sid, "title": sess.get("title", ""),
                                   "dated": sess.get("updated_at", ""),
                                   "source": "live", "text": ""}, "")
            return (False, {"error": "session not found"}, "")

        def done(res):
            btn.setEnabled(True)
            btn.setText(_was)
            okb, b, err = res if isinstance(res, tuple) else (False, {}, "")
            if not okb or not isinstance(b, dict) or b.get("error"):
                QMessageBox.warning(
                    self, t("dlg.error"),
                    t("cron.ctx_failed",
                      e=(b or {}).get("error") or err or "?"))
                return
            self._cron_write_job({"session": b.get("uuid", sid),
                                  "title": b.get("title", ""),
                                  "dated": b.get("dated", ""),
                                  "source": b.get("source", "?"),
                                  "text": b.get("text", ""),
                                  "live": True}, fields)

        self._bg(work, done)

    def _cron_write_job(self, cctx, f):
        name, instr, stype = f["name"], f["instr"], f["stype"]
        interval, fixed_dt = f["interval"], f["fixed_dt"]
        daily_times, weekdays, tgt = f["daily_times"], f["weekdays"], f["tgt"]
        editing = f.get("editing")
        if editing:
            self.cron_manager.update_job(editing,
                name=name, instruction=instr, schedule_type=stype,
                interval_minutes=interval, fixed_datetime=fixed_dt,
                daily_times=daily_times, weekdays=weekdays, target=tgt,
                model=f.get("model", ""), context=cctx,
                own_tab=bool(f.get("own_tab")))
            self.add_message(t("cron.job_updated", name=name), "system")
        else:
            self.cron_manager.add_job(name, instr, stype, interval, fixed_dt,
                                      daily_times, weekdays, target=tgt,
                                      model=f.get("model", ""), context=cctx,
                                      own_tab=bool(f.get("own_tab")))
            self.add_message(t("cron.job_created", name=name, type=stype), "system")
        if cctx:
            self.add_message(t("cron.ctx_attached", name=name,
                               title=cctx["title"],
                               date=(cctx.get("dated") or "?")[:10]), "system")

        self._clear_cron_form()
        self.refresh_cron_list()

    def _clear_cron_form(self):
        self._editing_cron_job_id = None
        self.cron_form_label.setText("<b>➕ NEW JOB</b>")
        self.cron_save_btn.setText(t("ui.add_job"))
        self.cron_name_entry.clear()
        self.cron_instruction_text.clear()
        self.cron_fixed_entry.clear()
        self.cron_daily_times_text.clear()
        self.cron_interval_spin.setValue(30)
        self.cron_type_combo.setCurrentIndex(max(0, self.cron_type_combo.findData("interval")))
        self.cron_alert_check.setChecked(True)
        self.cron_own_tab_check.setChecked(False)
        self._populate_cron_targets()
        if getattr(self, "cron_target_combo", None):
            self.cron_target_combo.setCurrentIndex(0)
        if getattr(self, "cron_ctx_combo", None) is not None:
            self._populate_cron_sessions()
            self.cron_ctx_combo.setCurrentIndex(0)
        if getattr(self, "cron_model_combo", None) is not None:
            self._populate_cron_models()
            self.cron_model_combo.setCurrentIndex(0)

    def _load_cron_job_to_form(self, job):
        self._editing_cron_job_id = job["id"]
        self.cron_form_label.setText(f"<b>✏️ EDITING: {job['name']}</b>")
        self.cron_save_btn.setText(t("btn.save_changes"))
        self.cron_name_entry.setText(job["name"])
        instr = job.get("instruction", "")
        has_alert = instr.startswith("__ALERT_ON_COMPLETE__\n")
        if has_alert:
            instr = instr[len("__ALERT_ON_COMPLETE__\n"):]
        self.cron_alert_check.setChecked(has_alert)
        self.cron_own_tab_check.setChecked(bool(job.get("own_tab")))
        self.cron_instruction_text.setPlainText(instr)
        self.cron_type_combo.setCurrentIndex(
            max(0, self.cron_type_combo.findData(job.get("schedule_type", "interval"))))
        self.cron_interval_spin.setValue(job.get("interval_minutes", 30))
        self.cron_fixed_entry.setText(job.get("fixed_datetime", ""))
        self.cron_daily_times_text.setPlainText("\n".join(job.get("daily_times", [])))
        wds = job.get("weekdays", [])
        for idx, cb in enumerate(self.cron_weekday_checks):
            cb.setChecked(idx in wds)
        if job.get("schedule_type") == "weekly":
            self.cron_weekly_time.setText((job.get("daily_times") or ["09:00"])[0])
        self._populate_cron_targets()
        tgt = job.get("target", "local")
        _cc = (job.get("context") or {}).get("session", "")
        if getattr(self, "cron_ctx_combo", None) is not None:
            self._populate_cron_sessions()
            _i = self.cron_ctx_combo.findData(_cc)
            if _i < 0 and _cc:
                self.cron_ctx_combo.addItem(
                    ((job.get("context") or {}).get("title") or _cc[:8])
                    + "   " + t("cron.ctx_gone"), _cc)
                _i = self.cron_ctx_combo.findData(_cc)
            self.cron_ctx_combo.setCurrentIndex(max(0, _i))
        if getattr(self, "cron_target_combo", None):
            i = self.cron_target_combo.findData(tgt)
            if i < 0:
                self.cron_target_combo.addItem(f"🌐 {tgt.split(':', 1)[-1]}", tgt)
                i = self.cron_target_combo.findData(tgt)
            self.cron_target_combo.setCurrentIndex(max(0, i))
        if getattr(self, "cron_model_combo", None) is not None:
            self._populate_cron_models()
            _m = job.get("model", "") or ""
            _mi = self.cron_model_combo.findData(_m)
            if _mi < 0 and _m:
                self.cron_model_combo.addItem(f"🧠 {_m}", _m)
                _mi = self.cron_model_combo.findData(_m)
            self.cron_model_combo.setCurrentIndex(max(0, _mi))

    def refresh_cron_list(self):
        self._populate_cron_targets()
        if getattr(self, "cron_ctx_combo", None) is not None:
            self._populate_cron_sessions()
        self.cron_jobs_list.clear()
        for job in self.cron_manager.get_jobs():
            en = "✅" if job.get("enabled") else "⏸️"
            st = {"interval":"🔄","fixed":"📅","daily":"🌅","weekly":"📆",
                  "startup":"⏻"}.get(job["schedule_type"],"❓")
            info = ""
            if job["schedule_type"] == "interval":
                m = job.get("interval_minutes", 0)
                info = (t("cron.every_hm", h=m // 60, m=m % 60) if m >= 60
                        else t("cron.every_min", m=m))
            elif job["schedule_type"] == "fixed": info = job.get("fixed_datetime", "")
            elif job["schedule_type"] == "startup": info = t("cron.type_startup")
            elif job["schedule_type"] == "daily":
                ts = job.get("daily_times", []); info = ", ".join(ts[:3])
            elif job["schedule_type"] == "weekly":
                day_abbr = [t("cron.mon"), t("cron.tue"), t("cron.wed"), t("cron.thu"),
                            t("cron.fri"), t("cron.sat"), t("cron.sun")]
                wds = job.get("weekdays", [])
                days_str = ", ".join(day_abbr[int(d)] for d in wds if 0 <= int(d) <= 6)
                hr = (job.get("daily_times") or ["09:00"])[0]
                info = f"{days_str} {hr}"
            nr = ""
            if job.get("next_run"):
                try: nr = f"-> {datetime.fromisoformat(job['next_run']).strftime('%m-%d %H:%M')}"
                except: pass
            tgt = job.get("target", "local")
            tmark = "" if tgt == "local" else f" 🌐{tgt.split(':', 1)[-1]}"
            _cx = (job.get("context") or {}).get("title") or ""
            cmark = f" 🧠{_cx}" if _cx else ""
            if job.get("own_tab"):
                cmark = " " + t("cron.own_tab_mark") + cmark
            item = QListWidgetItem(f"{en} {st} {job['name']}{tmark}{cmark} [{info}] {nr} (x{job.get('run_count',0)})")
            item.setData(Qt.ItemDataRole.UserRole, job)
            self.cron_jobs_list.addItem(item)
        self.cron_jobs_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        try: self.cron_jobs_list.customContextMenuRequested.disconnect()
        except: pass
        self.cron_jobs_list.customContextMenuRequested.connect(self._cron_context_menu)

    def _on_cron_job_selected(self, row):
        if row < 0: self.cron_log_display.clear(); return
        item = self.cron_jobs_list.item(row)
        if not item: return
        job = item.data(Qt.ItemDataRole.UserRole)
        if not job: return
        self._load_cron_job_to_form(job)
        log = self.cron_manager.get_job_log(job["id"])
        lines = []
        for e in reversed(log[-20:]):
            s = "✅" if e["status"] == "ok" else "❌"
            lines.append(f"[{e.get('timestamp','')[:19]}] {s} {e.get('result','')[:200]}")
        self.cron_log_display.setPlainText("\n".join(lines) if lines else "No executions")

    def _cron_context_menu(self, pos):
        item = self.cron_jobs_list.itemAt(pos)
        if not item: return
        job = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        toggle = menu.addAction("⏸️ Pause" if job.get("enabled") else "▶️ Activate")
        run_now = menu.addAction("▶️ Run now")
        clear_log = menu.addAction(t("btn.clear_log"))
        delete = menu.addAction(t("menu.delete"))
        action = menu.exec(self.cron_jobs_list.mapToGlobal(pos))
        if action == toggle:
            self.cron_manager.update_job(job["id"], enabled=not job.get("enabled", True))
            self.refresh_cron_list()
        elif action == run_now:
            if job.get("own_tab") and job.get("target", "local") == "local":
                self._run_job_in_tab(dict(job))
                self.cron_manager.log_execution(job["id"], "ok", "started in its tab")
                self.refresh_cron_list()
                return
            threading.Thread(target=self._run_cron_job_now, args=(job,), daemon=True).start()
        elif action == clear_log:
            self.cron_manager.clear_job_log(job["id"]); self.refresh_cron_list()
            self._on_cron_job_selected(self.cron_jobs_list.currentRow())
        elif action == delete:
            if QMessageBox.question(self, t("dlg.confirm"), t("cron.confirm_delete", name=job["name"])) == QMessageBox.StandardButton.Yes:
                self.cron_manager.delete_job(job["id"]); self.refresh_cron_list()
                self._clear_cron_form()

    def _run_cron_job_now(self, job):
        self.signals.ensure_cron_tab.emit()
        self.signals.message_tab.emit(t("cron.executing", name=job["name"]),
                                      "system", "cron")
        try:
            result = self._execute_cron_instruction(job)
            self.cron_manager.log_execution(job["id"], "ok", result or "no result")
            self.signals.message_tab.emit(f"✅ Job '{job['name']}' OK",
                                          "system", "cron")
        except Exception as e:
            self.cron_manager.log_execution(job["id"], "error", str(e))
            self.signals.message_tab.emit(
                t("msg.job_error", name=job["name"], e=e), "error", "cron")
        self.signals.refresh_cron.emit()

    @staticmethod
    def _cron_age(iso):
        try:
            from datetime import datetime as _dt
            d = (_dt.now() - _dt.fromisoformat(str(iso))).days
        except Exception:
            return ""
        if d < 0:
            return ""
        if d < 2:
            return t("cron.age_today")
        if d < 60:
            return t("cron.age_days", n=d)
        return t("cron.age_months", n=d // 30)

    def _cron_context_block(self, job):
        ctx = (job or {}).get("context") or {}
        text = (ctx.get("text") or "").strip()
        if not text:
            return ""
        title = ctx.get("title") or "?"
        dated = (ctx.get("dated") or "")[:10]
        age = self._cron_age(ctx.get("dated"))
        head = t("cron.ctx_header", title=title, date=dated or "?",
                 age=(f" ({age})" if age else ""))
        tail = t("cron.ctx_recall", title=title)
        if ctx.get("source") and ctx["source"] != "llm":
            head += "\n" + t("cron.ctx_excerpt")
        return f"{head}\n---\n{text}\n---\n{tail}"

    def _purge_cron_history(self, confirm=True):
        ctx = self._chat_ctxs.get(CRON_TAB_ID)
        if ctx is None:
            self.add_message(t("cron.purge_nothing"), "system")
            return
        if confirm:
            box = QMessageBox(self)
            box.setWindowTitle(t("cron.purge_title"))
            box.setText(t("cron.purge_confirm"))
            box.setIcon(QMessageBox.Icon.Warning)
            yes = box.addButton(t("btn.yes"), QMessageBox.ButtonRole.AcceptRole)
            box.addButton(t("btn.no"), QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() != yes:
                return
        ctx.history = []
        ctx.turn_steps = []
        self._chat_view(CRON_TAB_ID).clear()
        if getattr(ctx, "session_uuid", None):
            self._persist_tab(CRON_TAB_ID, ctx.title, quiet=True)
        self.add_message(t("cron.purge_done"), "system")

    def _cron_supervisor_turn(self, msg, prefix=""):
        try:
            jobs = self._mcp_call_json("list_cron", {}) or {}
        except Exception:
            jobs = {}
        _list = jobs.get("jobs", jobs) if isinstance(jobs, dict) else jobs
        sys_msg = t("cron.supervisor_sys", jobs=json.dumps(_list, ensure_ascii=False)[:4000])
        msgs = [{"role": "system", "content": sys_msg},
                {"role": "user", "content": (prefix + msg) if prefix else msg}]
        result = self.call_chat_with_tools(msgs)
        return result

    def _execute_cron_instruction(self, job):
        self._tls.unattended = True
        self._tu_begin(CRON_TAB_ID)
        try:
            return self._execute_cron_job(job)
        finally:
            self._tu_end()
            self._tls.unattended = False

    def _execute_cron_job(self, job):
        self._ensure_cron_ctx()
        self.signals.ensure_cron_tab.emit()
        self._tls.tab_id = CRON_TAB_ID
        _c_cron = self._chat_ctxs.get(CRON_TAB_ID)
        _waited = 0.0
        while (_c_cron is not None and getattr(_c_cron, "task_running",
                                               False) and _waited < 900):
            time.sleep(2.0)
            _waited += 2.0
        if _c_cron is not None and getattr(_c_cron, "task_running", False):
            self.thread_msg(t("cron.busy_skipped",
                              name=job.get("name", "?")), "system")
            return
        self._task_begin()
        try:
            return self._execute_cron_job_inner(job)
        finally:
            self._task_end()

    def _execute_cron_job_inner(self, job):
        instruction = job["instruction"]
        job_id = job.get("id")
        target = job.get("target", "local")
        alert = False
        self._current_cron_job_id = job_id
        if job_id:
            self._mcp_call("set_cron_context", {"job_id": job_id})
        if instruction.startswith("__ALERT_ON_COMPLETE__\n"):
            alert = True
            instruction = instruction[len("__ALERT_ON_COMPLETE__\n"):]

        if target and target != "local" and ":" in target:
            kind, _, handle = target.partition(":")
            try:
                resp = self._mcp_call_json("fed_ask_agent",
                                           {"handle": handle, "message": instruction},
                                           timeout=900)
                if isinstance(resp, dict) and resp.get("error"):
                    result = f"ERROR (cron {target}): {resp['error']}"
                else:
                    result = (resp or {}).get("reply", "") if isinstance(resp, dict) else str(resp)
            except Exception as e:
                result = f"ERROR delegating to {target}: {e}"; raise
            if alert and result:
                self.signals.show_alert.emit(f"⏰ CRON {target}", result[:500])
            return result or "no result"

        result = ""
        try:
            sys_msg = t("cron.exec_task")
            msgs = [{"role": "system", "content": sys_msg}]
            _live_uuid, _live_hist = self._cron_live_session(job)
            if _live_uuid:
                msgs[0] = {"role": "system", "content": t("cron.exec_task_live")}
                msgs.extend(_live_hist)
            else:
                _cblock = self._cron_context_block(job)
                if _cblock:
                    msgs.append({"role": "system", "content": _cblock})
            msgs.append({"role": "user", "content": instruction})
            self._current_instruction = instruction
            _ctx_run = self._turn_ctx()
            _ctx_run.turn_tools_used = 0
            result = self.call_chat_with_tools(
                msgs, model_name=(job.get("model") or "").strip() or None)
            if _live_uuid and getattr(_ctx_run, "turn_tools_used", 0) == 0 \
                    and result and not str(result).startswith("ERROR"):
                self.thread_msg(t("cron.live_no_consult"), "system")
                msgs.append({"role": "assistant", "content": str(result)})
                msgs.append({"role": "user", "content": t("cron.live_consult_nudge")})
                result = self.call_chat_with_tools(
                    msgs, model_name=(job.get("model") or "").strip() or None)
        except Exception as e:
            result = f"ERROR: {e}"; raise
        if alert and result:
            self.signals.show_alert.emit("⏰ CRON Completed", result[:500])
        if _live_uuid and result:
            self._cron_live_append(_live_uuid, instruction, result)
        return result or "no result"

    def _cron_live_session(self, job):
        ctx = job.get("context") or {}
        if not isinstance(ctx, dict) or ctx.get("frozen"):
            return "", []
        uuid = (ctx.get("session") or "").strip()
        if not uuid:
            return "", []
        try:
            r = self._mcp_call_json("chat_session_load", {"uuid": uuid},
                                    timeout=30)
        except Exception as e:
            self.thread_msg(t("cron.live_load_failed",
                              title=ctx.get("title", uuid[:8]),
                              err=str(e)[:120]), "error")
            return "", []
        if not isinstance(r, dict) or r.get("error"):
            self.thread_msg(t("cron.live_load_failed",
                              title=ctx.get("title", uuid[:8]),
                              err=str((r or {}).get("error", "?"))[:120]),
                            "error")
            return "", []
        _sess0 = (r.get("session") or {})
        hist = [m for m in (_sess0.get("history") or _sess0.get("messages") or [])
                if isinstance(m, dict) and m.get("role") in ("user", "assistant")
                and isinstance(m.get("content"), str) and m["content"].strip()]
        _keep = self.config.getint("CHAT", "cron_live_turns", fallback=0)
        if _keep > 0 and len(hist) > _keep:
            hist = hist[-_keep:]
        try:
            _lim = self._context_limit() or 0
        except Exception:
            _lim = 0
        _thr = self.config.getfloat("CHAT", "compact_threshold", fallback=0.85)
        _est = sum(len(m.get("content", "")) for m in hist) // 4
        if _lim and len(hist) > 8 and _est >= _thr * _lim:
            _kr = self.config.getint("CHAT", "compact_keep_recent", fallback=6)
            try:
                rr = requests.post(f"{self._mcp_url}/memory/compact",
                                   json={"messages": hist, "keep_recent": _kr,
                                         "conv": [uuid]}, timeout=900).json()
                if rr.get("compacted"):
                    hist = [m for m in rr.get("messages") or [] if isinstance(m, dict)]
                    _sess = dict((r.get("session") or {}))
                    _sess["history"] = hist
                    _sess.pop("messages", None)
                    self._mcp_call_json("chat_session_save", {"session": _sess},
                                        timeout=60)
                    self.thread_msg(t("cron.live_compacted",
                                      n=rr.get("replaced", 0)), "system")
                else:
                    self.thread_msg(t("cron.live_compact_failed",
                                      why=rr.get("why") or "?"), "system")
            except Exception as e:
                self.thread_msg(t("cron.live_compact_failed", why=str(e)[:120]),
                                "system")
        return uuid, hist

    def _cron_live_append(self, uuid, instruction, result):
        try:
            r = self._mcp_call_json("chat_session_load", {"uuid": uuid},
                                    timeout=30)
            sess = (r or {}).get("session") or {}
            if not sess:
                return
            msgs = list(sess.get("history") or sess.get("messages") or [])
            msgs.append({"role": "user", "content": instruction})
            msgs.append({"role": "assistant", "content": result})
            sess["history"] = msgs
            sess.pop("messages", None)
            _fu = self._chat_frag(str(instruction), "#0ea5e9",
                                  "👤 " + t("label.user"))
            _fa = self._chat_frag(str(result), "#e2e8f0", t("label.assistant"),
                                  rich=True)
            sess["html"] = (sess.get("html") or "") + _fu + _fa
            self._mcp_call_json("chat_session_save", {"session": sess},
                               timeout=30)
            for _tid, _c in (getattr(self, "_chat_ctxs", {}) or {}).items():
                if getattr(_c, "session_uuid", "") == uuid:
                    _c.history = msgs
                    self.signals.message_tab.emit(str(instruction), "user", _tid)
                    self.signals.message_tab.emit(str(result), "assistant", _tid)
                    self.signals.message_tab.emit(
                        t("cron.live_wrote", n=2), "system", _tid)
                    break
        except Exception as e:
            self.thread_msg(t("cron.live_save_failed", err=str(e)[:120]),
                            "error")


    def _show_credentials_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("🔐 " + t("gui.cred_manager_title"))
        dlg.setMinimumSize(640, 560)
        lay = QVBoxLayout(dlg)

        from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView
        table = QTableWidget()
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["Site", "User", "URL", "OTP", "Notes"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(table, 1)

        def refresh_table():
            creds = self.cred_manager.get_all()
            table.setRowCount(len(creds))
            for i, c in enumerate(creds):
                table.setItem(i, 0, QTableWidgetItem(c["site"]))
                table.setItem(i, 1, QTableWidgetItem(c["username"]))
                table.setItem(i, 2, QTableWidgetItem(c.get("url", "")))
                otp_text = "✅" if c.get("totp_secret") else ""
                table.setItem(i, 3, QTableWidgetItem(otp_text))
                table.setItem(i, 4, QTableWidgetItem(c.get("notes", "")))

        refresh_table()

        form = QGroupBox(t("grp.add_edit"))
        from config_editor import left_form
        fl = left_form(QFormLayout(form))
        kind_c = QComboBox()
        for _k, _lbl in (("web", t("cred.kind_web")),
                         ("apikey", t("cred.kind_apikey")),
                         ("card", t("cred.kind_card")),
                         ("mail", t("cred.kind_mail"))):
            kind_c.addItem(_lbl, _k)
        site_e = QLineEdit()
        site_e.setPlaceholderText(t("cred.site_ph"))
        user_e = QLineEdit(); user_e.setPlaceholderText(t("ph.user_email"))
        pass_e = QLineEdit(); pass_e.setPlaceholderText(t("ui.password"))
        pass_e.setEchoMode(QLineEdit.EchoMode.Password)
        url_e = QLineEdit()
        url_e.setPlaceholderText(t("cred.url_ph"))
        otp_e = QLineEdit(); otp_e.setPlaceholderText(t("gui.totp_placeholder"))
        otp_e.setEchoMode(QLineEdit.EchoMode.Password)
        notes_e = QLineEdit(); notes_e.setPlaceholderText(t("gui.notes_placeholder"))
        otp_display = QLabel(""); otp_display.setStyleSheet("font-size: 18px; font-weight: bold; color: #00aa00;")
        key_e = QLineEdit(); key_e.setPlaceholderText(t("cred.key_ph"))
        key_e.setEchoMode(QLineEdit.EchoMode.Password)
        num_e = QLineEdit(); num_e.setPlaceholderText("4111 1111 1111 1111")
        num_e.setEchoMode(QLineEdit.EchoMode.Password)
        exp_e = QLineEdit(); exp_e.setPlaceholderText("MM/AA")
        cvv_e = QLineEdit(); cvv_e.setPlaceholderText("CVV")
        cvv_e.setEchoMode(QLineEdit.EchoMode.Password)
        hold_e = QLineEdit(); hold_e.setPlaceholderText(t("cred.holder_ph"))
        mail_user_e = QLineEdit(); mail_user_e.setPlaceholderText("name@example.com")
        mail_pass_e = QLineEdit(); mail_pass_e.setPlaceholderText(t("cred.mail_password_ph"))
        mail_pass_e.setEchoMode(QLineEdit.EchoMode.Password)
        mail_smtp_e = QLineEdit(); mail_smtp_e.setPlaceholderText(t("cred.mail_smtp_ph"))
        mail_hint = QLabel(t("cred.mail_hint")); mail_hint.setWordWrap(True)
        from PyQt6.QtWidgets import QSizePolicy as _QSP
        mail_hint.setSizePolicy(_QSP.Policy.Preferred, _QSP.Policy.Minimum)
        fl.addRow(t("cred.kind"), kind_c)
        fl.addRow(t("cred.site"), site_e)
        fl.addRow(t("cred.url"), url_e)
        _rows = {
            "web": [("User:", user_e), ("Password:", pass_e),
                    ("2FA Seed:", otp_e), ("Current OTP:", otp_display)],
            "apikey": [(t("cred.key"), key_e)],
            "card": [(t("cred.number"), num_e), (t("cred.expiry"), exp_e),
                     (t("cred.cvv"), cvv_e), (t("cred.holder"), hold_e)],
            "mail": [(t("cred.mail_address"), mail_user_e),
                     (t("cred.mail_password"), mail_pass_e),
                     (t("cred.mail_smtp"), mail_smtp_e), ("", mail_hint)],
        }
        for _pares in _rows.values():
            for _lbl, _w in _pares:
                fl.addRow(_lbl, _w)
        fl.addRow("Notes:", notes_e)
        lay.addWidget(form)

        def _apply_kind():
            k = kind_c.currentData() or "web"
            for _kk, _pares in _rows.items():
                for _lbl, _w in _pares:
                    vis = (_kk == k)
                    _w.setVisible(vis)
                    _l = fl.labelForField(_w)
                    if _l is not None:
                        _l.setVisible(vis)
            url_e.setPlaceholderText(t("cred.mail_server_ph") if k == "mail"
                                     else t("cred.url_ph"))
            _ul = fl.labelForField(url_e)
            if _ul is not None:
                _ul.setText(t("cred.mail_server") if k == "mail" else t("cred.url"))

        kind_c.currentIndexChanged.connect(lambda _i: _apply_kind())
        _apply_kind()

        def update_otp():
            secret = otp_e.text().strip()
            if secret:
                try:
                    import hmac as _hmac, hashlib as _hashlib, struct as _struct, time as _time
                    s = secret.replace(" ", "").upper()
                    padding = 8 - len(s) % 8
                    if padding != 8: s += "=" * padding
                    key = base64.b32decode(s)
                    counter = int(_time.time()) // 30
                    msg = _struct.pack(">Q", counter)
                    h = _hmac.new(key, msg, _hashlib.sha1).digest()
                    offset = h[-1] & 0x0F
                    code = (_struct.unpack(">I", h[offset:offset+4])[0] & 0x7FFFFFFF) % 1000000
                    otp_display.setText(f"🔑 {code:06d}")
                except:
                    otp_display.setText(t("ui.invalid_seed"))
            else:
                otp_display.setText("")

        otp_timer = QTimer(dlg)
        otp_timer.timeout.connect(update_otp)
        otp_timer.start(1000)

        btn_row = QHBoxLayout()
        save_btn = QPushButton(t("cron.save"))
        del_btn = QPushButton(t("cron.delete"))
        show_btn = QPushButton(t("creds.view_password"))
        close_btn = QPushButton(t("creds.close"))

        def on_select():
            rows = table.selectedItems()
            if rows:
                r = rows[0].row()
                creds = self.cred_manager.get_all()
                if r < len(creds):
                    c = creds[r]
                    site_e.setText(c["site"])
                    _k = c.get("kind") or "web"
                    _i = kind_c.findData(_k)
                    kind_c.setCurrentIndex(_i if _i >= 0 else 0)
                    _apply_kind()
                    _mail = _k == "mail"
                    user_e.setText("" if _mail else c.get("username", ""))
                    pass_e.setText("" if _mail else c.get("password", ""))
                    mail_user_e.setText(c.get("username", "") if _mail else "")
                    mail_pass_e.setText(c.get("password", "") if _mail else "")
                    mail_smtp_e.setText(c.get("smtp", "") if _mail else "")
                    url_e.setText(c.get("url", ""))
                    otp_e.setText(c.get("totp_secret", ""))
                    key_e.setText(c.get("secret", ""))
                    num_e.setText(c.get("number", ""))
                    exp_e.setText(c.get("expiry", ""))
                    cvv_e.setText(c.get("cvv", ""))
                    hold_e.setText(c.get("holder", ""))
                    notes_e.setText(c.get("notes", ""))
                    update_otp()

        def on_save():
            site = site_e.text().strip()
            kind = kind_c.currentData() or "web"
            if not site:
                QMessageBox.warning(dlg, t("dlg.error"), t("err.site_user_required")); return
            if kind == "web" and not user_e.text().strip():
                QMessageBox.warning(dlg, t("dlg.error"), t("err.site_user_required")); return
            if kind == "apikey" and not key_e.text().strip():
                QMessageBox.warning(dlg, t("dlg.error"), t("cred.key_required")); return
            if kind == "card" and not num_e.text().strip():
                QMessageBox.warning(dlg, t("dlg.error"), t("cred.number_required")); return
            if kind == "mail" and not (mail_user_e.text().strip() and mail_pass_e.text()):
                QMessageBox.warning(dlg, t("dlg.error"), t("cred.mail_required")); return
            _user, _pw = ((mail_user_e.text().strip(), mail_pass_e.text())
                          if kind == "mail" else (user_e.text().strip(), pass_e.text()))
            self.cred_manager.add_credential(
                site, _user, _pw,
                url_e.text().strip(), notes_e.text().strip(),
                otp_e.text().strip(), kind=kind, secret=key_e.text().strip(),
                number=num_e.text().strip(), expiry=exp_e.text().strip(),
                cvv=cvv_e.text().strip(), holder=hold_e.text().strip(),
                smtp=mail_smtp_e.text().strip() if kind == "mail" else "")
            refresh_table()
            for _w in (site_e, user_e, pass_e, url_e, otp_e, notes_e, key_e,
                       num_e, exp_e, cvv_e, hold_e, mail_user_e, mail_pass_e,
                       mail_smtp_e):
                _w.clear()
            otp_display.setText("")
            self.add_message(t("cred.saved", site=site), "system")

        def on_delete():
            rows = table.selectedItems()
            if not rows: return
            site = table.item(rows[0].row(), 0).text()
            if QMessageBox.question(dlg, t("dlg.confirm"), t("cred.confirm_delete", site=site)) == QMessageBox.StandardButton.Yes:
                self.cred_manager.delete_credential(site)
                refresh_table()
                site_e.clear(); user_e.clear(); pass_e.clear(); url_e.clear(); otp_e.clear(); notes_e.clear()
                otp_display.setText("")

        def on_show_pwd():
            if pass_e.echoMode() == QLineEdit.EchoMode.Password:
                pass_e.setEchoMode(QLineEdit.EchoMode.Normal)
                mail_pass_e.setEchoMode(QLineEdit.EchoMode.Normal)
                show_btn.setText("🔒 Hide")
            else:
                pass_e.setEchoMode(QLineEdit.EchoMode.Password)
                mail_pass_e.setEchoMode(QLineEdit.EchoMode.Password)
                show_btn.setText("👁 View Password")

        table.itemSelectionChanged.connect(on_select)
        save_btn.clicked.connect(on_save)
        del_btn.clicked.connect(on_delete)
        show_btn.clicked.connect(on_show_pwd)
        close_btn.clicked.connect(dlg.close)

        btn_row.addWidget(save_btn)
        btn_row.addWidget(del_btn)
        btn_row.addWidget(show_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        dlg.exec()
        otp_timer.stop()
        dlg.deleteLater()



def main():
    import argparse
    parser = argparse.ArgumentParser(description="Chat Assistant Qt + Browser-Use")
    parser.add_argument("-s", "--solo", action="store_true", help="No system tray")
    args = parser.parse_args()
    print(f"🖥 Platform: {platform.system()}")
    os.environ.setdefault("QT_LOGGING_RULES", "qt.text.font.db=false")

    _extra = []
    try:
        _cf_now = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
        if hasattr(os, "geteuid") and os.geteuid() == 0 and "no-sandbox" not in _cf_now:
            _extra.append("--no-sandbox")
    except Exception:
        pass
    if "process-per-site" not in os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", ""):
        _extra.append("--process-per-site")
    if os.environ.get("HOPLITA_FORCE_SOFTWARE") == "1":
        print("🎨 HOPLITA_FORCE_SOFTWARE=1 → WebEngine software rendering")
        _cf_now = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
        for _flag, _key in [("--disable-gpu", "disable-gpu"),
                            ("--disable-gpu-compositing", "disable-gpu-compositing"),
                            ("--disable-features=VizDisplayCompositor", "VizDisplayCompositor")]:
            if _key not in _cf_now:
                _extra.append(_flag)
        os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
    else:
        if "QTWEBENGINE_CHROMIUM_FLAGS" not in os.environ:
            _extra.append("--log-level=3")
    if _extra:
        _cf = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (_cf + " " + " ".join(_extra)).strip()
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    try:
        from PyQt6 import QtWebEngineCore
    except ImportError:
        pass
    if sys.platform == "darwin":
        _mac_menu_name("HOPLITA")
    app = QApplication(sys.argv)
    app.setApplicationName("HOPLITA Chat Assistant")
    app.setDesktopFileName("HOPLITA")
    from PyQt6.QtGui import QPalette as _QPal, QColor as _QCol
    _pal = app.palette()
    _pal.setColor(_QPal.ColorRole.PlaceholderText, _QCol("#a9bdd6"))
    app.setPalette(_pal)
    app.setQuitOnLastWindowClosed(False)
    icon_path_win = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hoplita.png")
    try:
        import hoplita_paths
        icon_path_lin = hoplita_paths.icon_path()
    except Exception:
        icon_path_lin = "/opt/hoplita/hoplita.png"
    icon_path = icon_path_win if IS_WINDOWS else icon_path_lin
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    _single = None
    if not args.solo:
        from PyQt6.QtNetwork import QLocalServer, QLocalSocket
        import getpass as _gp
        import hashlib as _hl
        from hoplita_paths import hoplita_base as _hb
        _key = (f"hoplita-gui-{_gp.getuser()}-"
                f"{_hl.sha1(os.path.realpath(_hb()).encode()).hexdigest()[:10]}")
        _probe = QLocalSocket()
        _probe.connectToServer(_key)
        if _probe.waitForConnected(400):
            _probe.write(b"raise")
            _probe.flush()
            _probe.waitForBytesWritten(400)
            _probe.disconnectFromServer()
            print("[GUI] " + t("app.already_running"))
            sys.exit(0)
        QLocalServer.removeServer(_key)
        _single = QLocalServer()
        _single.listen(_key)

    window = ChatAssistantWindow()
    window.resize(1450, 860)
    window._normal_geo = window.geometry()
    window._is_max = False
    window.show()
    window.raise_()
    window.activateWindow()
    if sys.platform == "darwin":
        import mac_dialogs
        mac_dialogs.activate_app()

    def _raise_existing():
        _c = _single.nextPendingConnection()
        if _c is not None:
            _c.disconnectFromServer()
        window.showNormal()
        window.raise_()
        window.activateWindow()

    if _single is not None:
        _single.newConnection.connect(_raise_existing)
    if args.solo:
        app.setQuitOnLastWindowClosed(True)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
