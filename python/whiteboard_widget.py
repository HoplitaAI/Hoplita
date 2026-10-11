#!/usr/bin/env python3
from urllib.parse import parse_qs, urlparse

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QVBoxLayout, QWidget


class _Page(QWebEnginePage):
    def __init__(self, owner):
        super().__init__(owner)
        self._owner = owner

    def javaScriptConsoleMessage(self, level, message, line, source):
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            import sys
            print(f"[WB-PAGE] {message} ({source.rsplit('/', 1)[-1]}:{line})", file=sys.stderr)

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        u = urlparse(url.toString())
        if is_main_frame and not u.path.startswith(("/wb/app", "/wb/host/")):
            if u.scheme in ("http", "https", "mailto") and not url.toString().startswith(self._owner.mcp_url):
                QTimer.singleShot(0, lambda s=url.toString(): self._owner.host_request.emit("external", {"url": s}))
            return False
        if u.path.startswith("/wb/host/"):
            action = u.path[len("/wb/host/"):]
            q = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
            QTimer.singleShot(0, lambda a=action, p=q: self._owner.host_request.emit(a, p))
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class WhiteboardWidget(QWidget):
    host_request = pyqtSignal(str, dict)
    shown = pyqtSignal(str)

    def __init__(self, mcp_url, parent=None):
        super().__init__(parent)
        self.mcp_url = (mcp_url or "").rstrip("/")
        self.current = None
        self.root = ""
        self.loaded = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = QWebEngineView(self)
        self.view.setPage(_Page(self))
        try:
            from PyQt6.QtGui import QColor
            self.view.page().setBackgroundColor(QColor("#0b1020"))
        except Exception:
            pass
        lay.addWidget(self.view)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._menu)
        self.view.urlChanged.connect(self._url_changed)

    def show_whiteboard(self, wid=None, root=""):
        from urllib.parse import urlencode
        try:
            import hoplita_i18n
            lang = getattr(hoplita_i18n, "_lang", "") or "en"
        except Exception:
            lang = "en"
        self.current = wid
        self.root = root or ""
        self.loaded = True
        q = {"lang": lang}
        if wid:
            q["id"] = wid
        elif root:
            q["root"] = root
        self.view.load(QUrl(f"{self.mcp_url}/wb/app?{urlencode(q)}"))

    def _url_changed(self, url):
        from urllib.parse import parse_qs as _pq
        wid = (_pq(urlparse(url.toString()).query).get("id") or [""])[0]
        if wid != (self.current or ""):
            self.current = wid or None
        self.shown.emit(wid)

    def _menu(self, pos):
        from PyQt6.QtWidgets import QMenu
        page = self.view.page()
        if not page.hasSelection():
            return
        m = QMenu(self)
        try:
            import hoplita_i18n
            word = hoplita_i18n.t
        except Exception:
            def word(key, **_kw):
                return key
        m.addAction(word("wb.menu_copy"), lambda: page.triggerAction(QWebEnginePage.WebAction.Copy))
        m.addAction(word("wb.menu_copy_agent"),
                    lambda: page.runJavaScript(
                        "(() => { if (typeof doCopy !== 'function') return;"
                        " if (!pendingCopy) { const t = getSelection().toString();"
                        " if (t.trim()) pendingCopy = {kind: 'text', quote: t}; } doCopy(); })()"))
        m.exec(self.view.mapToGlobal(pos))

    def reload(self):
        self.view.reload()
