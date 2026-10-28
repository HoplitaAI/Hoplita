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
        _wb = u.path.find("/wb/")
        _rel = u.path[_wb:] if _wb >= 0 else u.path
        if is_main_frame and not _rel.startswith(("/wb/app", "/wb/host/")):
            if u.scheme in ("http", "https", "mailto") and not url.toString().startswith(self._owner.mcp_url):
                QTimer.singleShot(0, lambda s=url.toString(): self._owner.host_request.emit("external", {"url": s}))
            return False
        if _rel.startswith("/wb/host/"):
            action = _rel[len("/wb/host/"):]
            q = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
            QTimer.singleShot(0, lambda a=action, p=q: self._owner.host_request.emit(a, p))
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class WhiteboardWidget(QWidget):
    host_request = pyqtSignal(str, dict)
    shown = pyqtSignal(str)

    def __init__(self, mcp_url, parent=None):
        super().__init__(parent)
        self._url = mcp_url
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
        self.view.loadFinished.connect(self._loaded)
        self._base = None
        self._fresh = False
        self._filtered = None

    @property
    def mcp_url(self):
        u = self._url() if callable(self._url) else self._url
        return (u or "").rstrip("/")

    @mcp_url.setter
    def mcp_url(self, value):
        self._url = value

    def show_whiteboard(self, wid=None, root=""):
        from urllib.parse import urlencode
        try:
            import hoplita_i18n
            lang = getattr(hoplita_i18n, "_lang", "") or "en"
        except Exception:
            lang = "en"
        root = (root or "").rstrip("/\\")
        base = (self.mcp_url, lang, root or self.root)
        self.current = wid
        if self.loaded and self._base == base and root in ("", self.root):
            import json as _json
            self.view.page().runJavaScript(
                f"window.wbOpen && window.wbOpen({_json.dumps(wid or None)}, {_json.dumps(root)})")
            return
        self.root = root or self.root
        self.loaded = True
        self._base = (self.mcp_url, lang, self.root)
        q = {"lang": lang}
        if wid:
            q["id"] = wid
        if self.root:
            q["root"] = self.root
        self._fresh = True
        self.view.load(QUrl(f"{self.mcp_url}/wb/app?{urlencode(q)}"))

    def refresh(self):
        self.view.page().runJavaScript("window.wbRefresh && window.wbRefresh()")

    def _loaded(self, ok):
        if ok and self._fresh:
            self._fresh = False
            self.view.history().clear()
        fp = self.view.focusProxy()
        if fp is not None and fp is not self._filtered:
            fp.installEventFilter(self)
            self._filtered = fp

    def eventFilter(self, obj, ev):
        from PyQt6.QtCore import QEvent
        if ev.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                         QEvent.Type.MouseButtonDblClick) \
                and ev.button() in (Qt.MouseButton.BackButton, Qt.MouseButton.ForwardButton):
            if ev.type() == QEvent.Type.MouseButtonPress:
                self.go_back() if ev.button() == Qt.MouseButton.BackButton else self.go_forward()
            return True
        return super().eventFilter(obj, ev)

    def go_back(self):
        self.view.page().runJavaScript("window.wbBack && window.wbBack()")

    def go_forward(self):
        self.view.page().runJavaScript("window.wbForward && window.wbForward()")

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
