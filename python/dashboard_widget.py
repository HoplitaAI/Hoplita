#!/usr/bin/env python3

import os
import re
import sys

import threading
import time

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel
from PyQt6.QtCore import Qt, pyqtSignal, QEventLoop, QTimer, QThread, QUrl
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import (QWebEnginePage, QWebEngineProfile,
                                   QWebEngineUrlRequestJob, QWebEngineUrlScheme,
                                   QWebEngineUrlSchemeHandler)


MEDIA_SCHEME = "hoplita-media"
_MEDIA_FILES = {}
_MEDIA_HANDLER = None


def register_media_scheme():
    if getattr(register_media_scheme, "done", False):
        return
    sch = QWebEngineUrlScheme(MEDIA_SCHEME.encode())
    sch.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    sch.setFlags(QWebEngineUrlScheme.Flag.SecureScheme
                 | QWebEngineUrlScheme.Flag.CorsEnabled)
    QWebEngineUrlScheme.registerScheme(sch)
    register_media_scheme.done = True


register_media_scheme()


class _MediaHandler(QWebEngineUrlSchemeHandler):

    def requestStarted(self, job):
        from PyQt6.QtCore import QBuffer, QIODevice
        import mimetypes
        path = _MEDIA_FILES.get(job.requestUrl().host())
        if not path or not os.path.isfile(path):
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
            return
        buf = QBuffer(job)
        buf.setData(data)
        buf.open(QIODevice.OpenModeFlag.ReadOnly)
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        job.reply(mime.encode(), buf)


def _install_media_handler():
    global _MEDIA_HANDLER
    if _MEDIA_HANDLER is None:
        _MEDIA_HANDLER = _MediaHandler()
        QWebEngineProfile.defaultProfile().installUrlSchemeHandler(
            MEDIA_SCHEME.encode(), _MEDIA_HANDLER)


def media_url(path):
    import hashlib
    from urllib.parse import quote
    p = os.path.abspath(os.path.expanduser(str(path or "")))
    token = hashlib.sha1(p.encode("utf-8")).hexdigest()[:20]
    _MEDIA_FILES[token] = p
    return f"{MEDIA_SCHEME}://{token}/{quote(os.path.basename(p))}"


def media_path(url):
    from urllib.parse import urlparse
    try:
        u = urlparse(str(url or ""))
    except ValueError:
        return None
    if u.scheme != MEDIA_SCHEME:
        return None
    return _MEDIA_FILES.get(u.hostname or "")


_SRC_RE = re.compile(r"""(<img\b[^>]*?\bsrc\s*=\s*)(["'])(.*?)\2""", re.I | re.S)


def serve_local_images(html):
    if not html or "<img" not in html.lower():
        return html
    from urllib.parse import unquote
    import hoplita_hands as _hands

    def _one(m):
        src = m.group(3).strip()
        low = src.lower()
        if low.startswith(("http://", "https://", "data:", "blob:", "//",
                           MEDIA_SCHEME + "://")):
            return m.group(0)
        p = unquote(src[len("file://"):]) if low.startswith("file://") else src
        p = os.path.expanduser(p)
        if not (_hands.is_image_path(p) and os.path.isfile(p)):
            return m.group(0)
        return f"{m.group(1)}{m.group(2)}{media_url(p)}{m.group(2)}"
    return _SRC_RE.sub(_one, html)


_WRAPPER_HEAD = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  html, body {
    margin: 0; padding: 16px; box-sizing: border-box;
    background: #0f172a; color: #e2e8f0;
    font-family: -apple-system, 'Segoe UI', Roboto, sans-serif;
  }
  * { box-sizing: border-box; }
  h1, h2, h3 { color: #f1f5f9; }
  .grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }
  .card {
    background: #1e293b; border-radius: 12px; padding: 16px;
    border: 1px solid #334155;
  }
  table { width: 100%; border-collapse: collapse; }
  th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #334155; }
  th { color: #94a3b8; font-weight: 600; }
  canvas { max-width: 100%; }
</style>
</head><body>
"""

_WRAPPER_TAIL = """
</body></html>
"""

_PLACEHOLDER = _WRAPPER_HEAD + """
<div style="display:flex;align-items:center;justify-content:center;height:80vh;
            flex-direction:column;color:#64748b;text-align:center;">
  <div style="font-size:48px;margin-bottom:16px;">🎨</div>
  <h2 style="color:#64748b;">Empty canvas</h2>
  <p>Ask the assistant to draw or build something visual.<br>
     Charts, SVG, diagrams, animations, mockups, maps...<br>
     E.g.: <i>"dashboard of RAM per process"</i> · <i>"an SVG diagram of the login flow"</i></p>
</div>
""" + _WRAPPER_TAIL


CANVAS_BASE_URL = "http://canvas.localhost/"

LIVE_PAGES = 8


class _CanvasPage(QWebEnginePage):

    def __init__(self, key="", parent=None):
        super().__init__(parent)
        self.key = key
        self.console = []
        self.html = ""
        self.touched = time.time()
        self.rebuilt = False
        try:
            from PyQt6.QtGui import QColor as _QColor
            self.setBackgroundColor(_QColor("#0f172a"))
        except Exception:
            pass

    def javaScriptConsoleMessage(self, level, message, line, source):
        _name = getattr(level, "name", None) or str(level)
        _lv = ("error" if "Error" in _name
               else "warning" if "Warning" in _name
               else "info")
        self.console.append({"level": _lv, "line": int(line or 0),
                             "source": str(source or "")[-60:],
                             "text": str(message or "")[:300]})
        del self.console[:-60]


class DashboardWidget(QWidget):

    _render_requested = pyqtSignal(str, str)
    _bridge_requested = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QWidget()
        self._bar = QHBoxLayout(bar)
        self._bar.setContentsMargins(12, 6, 8, 6)
        self._bar.setSpacing(6)
        self.title_label = QLabel("Canvas")
        self.title_label.setStyleSheet(
            "color:#e6edf7; font-size:12px; font-weight:600; letter-spacing:0.5px;")
        self._bar.addWidget(self.title_label)
        self._bar.addStretch()
        layout.addWidget(bar)
        self.add_action("↻", "Reload this page", self._reload)

        self.view = QWebEngineView()
        _install_media_handler()
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.view.setStyleSheet("background:#0f172a;")
        layout.addWidget(self.view, 1)

        self._pages = {}
        self._frozen = {}
        self._shown = None
        self._req_gen = {}
        self._render_gen = {}
        self._loaded_gen = {}
        self._blank = _CanvasPage("", self.view)
        self._blank.setHtml(_PLACEHOLDER, QUrl(CANVAS_BASE_URL))
        self.view.setPage(self._blank)
        self._render_requested.connect(self._do_render)
        self._bridge_requested.connect(self._do_bridge)

    def add_action(self, label, tip, slot):
        b = QPushButton(label)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setToolTip(tip)
        b.setStyleSheet(
            "QPushButton { background:transparent; color:#cbd5e1; border:1px solid #334155; "
            "border-radius:6px; padding:3px 10px; font-size:12px; } "
            "QPushButton:hover { background:#1e293b; color:#e6edf7; }")
        b.clicked.connect(slot)
        self._bar.addWidget(b)
        return b

    def _key(self, key):
        if key is None:
            return self._shown if self._shown is not None else ""
        return str(key)

    def _page(self, key, thaw=True):
        pg = self._pages.get(key)
        if pg is not None:
            pg.touched = time.time()
            return pg
        pg = _CanvasPage(key, self.view)
        pg.loadFinished.connect(lambda _ok=True, k=key: self._on_load_finished(k))
        pg.renderProcessTerminated.connect(
            lambda *_a, k=key: QTimer.singleShot(0, lambda: self._rebuild(k)))
        self._pages[key] = pg
        html = self._frozen.pop(key, "")
        if thaw and html:
            pg.html = html
            pg.rebuilt = True
            pg.setHtml(html, QUrl(CANVAS_BASE_URL))
        self._enforce_cap()
        return pg

    def _enforce_cap(self):
        while len(self._pages) > LIVE_PAGES:
            idle = [p for k, p in self._pages.items() if k != self._shown]
            if not idle:
                return
            old = min(idle, key=lambda p: p.touched)
            self._freeze(old.key)

    def _freeze(self, key):
        pg = self._pages.pop(key, None)
        if pg is None:
            return
        if pg.html:
            self._frozen[key] = pg.html
        if self.view.page() is pg:
            self.view.setPage(self._blank)
        pg.deleteLater()

    def _rebuild(self, key):
        pg = self._pages.get(key)
        if pg is None or not pg.html:
            return
        pg.rebuilt = True
        pg.setHtml(pg.html, QUrl(CANVAS_BASE_URL))

    def show_tab(self, key):
        key = self._key(key)
        self._shown = key
        pg = self._pages.get(key)
        if pg is None and key in self._frozen:
            pg = self._page(key)
        if pg is None:
            if self.view.page() is not self._blank:
                self.view.setPage(self._blank)
            return
        pg.touched = time.time()
        if self.view.page() is not pg:
            self.view.setPage(pg)

    def shown(self):
        return self._shown

    def has(self, key):
        key = self._key(key)
        return key in self._pages or key in self._frozen

    def html(self, key=None):
        key = self._key(key)
        pg = self._pages.get(key)
        return pg.html if pg is not None else self._frozen.get(key, "")

    def drop(self, key):
        key = self._key(key)
        pg = self._pages.pop(key, None)
        if pg is not None:
            if self.view.page() is pg:
                self.view.setPage(self._blank)
            pg.deleteLater()
        self._frozen.pop(key, None)
        for d in (self._req_gen, self._render_gen, self._loaded_gen):
            d.pop(key, None)

    def live_pages(self):
        return len(self._pages)

    def render_html(self, html, wrap=True, key=None):
        if wrap:
            low = html.lower()
            if "<!doctype" in low or "<html" in low:
                full = html
            else:
                full = _WRAPPER_HEAD + html + _WRAPPER_TAIL
        else:
            full = html
        key = self._key(key)
        self._req_gen[key] = self._req_gen.get(key, 0) + 1
        self._render_requested.emit(key, full)

    _LOAD_CAP_MS = 10000

    def _on_load_finished(self, key):
        self._loaded_gen[key] = self._render_gen.get(key, 0)

    def _loaded(self, key):
        return self._loaded_gen.get(key, 0) >= self._req_gen.get(key, 0)

    def _wait_loaded(self, key):
        if self._loaded(key):
            return
        deadline = time.time() + self._LOAD_CAP_MS / 1000.0
        if QThread.currentThread() is self.thread():
            while not self._loaded(key) and time.time() < deadline:
                _loop = QEventLoop()
                QTimer.singleShot(50, _loop.quit)
                _loop.exec()
        else:
            while not self._loaded(key) and time.time() < deadline:
                time.sleep(0.05)

    def _do_render(self, key, full_html):
        pg = self._page(key, thaw=False)
        self._render_gen[key] = self._req_gen.get(key, 0)
        pg.html = full_html
        pg.rebuilt = False
        pg.console.clear()
        pg.setHtml(full_html, QUrl(CANVAS_BASE_URL))
        if self._shown is None:
            self._shown = key
        if key == self._shown and self.view.page() is not pg:
            self.view.setPage(pg)

    def _do_bridge(self, req):
        try:
            if req.get("op") == "eval":
                req["out"] = self._eval_js_gui(req["key"], req["js"], req["wait_ms"])
            elif req.get("op") == "shot":
                req["out"] = self._screenshot_gui(req["key"], req["max_w"])
        except Exception as e:
            req["out"] = {"error": str(e)[:200]}
        finally:
            req["event"].set()

    def _bridge(self, op, key, timeout_s=8.0, **kw):
        if QThread.currentThread() is self.thread():
            if op == "eval":
                return self._eval_js_gui(key, kw["js"], kw["wait_ms"])
            return self._screenshot_gui(key, kw["max_w"])
        req = dict(kw, op=op, key=key, event=threading.Event(), out=None)
        self._bridge_requested.emit(req)
        if not req["event"].wait(timeout_s):
            return {"error": "the GUI thread did not answer in time"}
        return req["out"]

    def console_report(self, wait_ms=900, key=None):
        key = self._key(key)
        self._wait_loaded(key)
        if QThread.currentThread() is self.thread():
            _loop = QEventLoop()
            QTimer.singleShot(max(0, int(wait_ms)), _loop.quit)
            _loop.exec()
        else:
            time.sleep(max(0, int(wait_ms)) / 1000.0)
        pg = self._pages.get(key)
        try:
            return list(pg.console) if pg is not None else []
        except Exception:
            return []

    def screenshot_png(self, max_w=1024, key=None):
        r = self._bridge("shot", self._key(key), max_w=max_w)
        return r if isinstance(r, (bytes, bytearray)) else b""

    def _screenshot_gui(self, key, max_w):
        pg = self._pages.get(key)
        if pg is None and key in self._frozen:
            pg = self._page(key)
        if pg is None:
            return b""
        pg.touched = time.time()
        try:
            _d = self._canvas_dataurl(key, max_w)
            if _d:
                return _d
        except Exception:
            pass
        if not self.view.isVisible():
            return b""
        from PyQt6.QtCore import QBuffer, QIODevice
        before = self.view.page()
        swapped = before is not pg
        if swapped:
            self.view.setPage(pg)
        _loop = QEventLoop()
        QTimer.singleShot(90 if swapped else 30, _loop.quit)
        _loop.exec()
        pix = self.view.grab()
        if swapped:
            self.view.setPage(before)
        if pix.isNull() or pix.width() == 0:
            return b""
        if pix.width() > max_w:
            from PyQt6.QtCore import Qt as _Qt
            pix = pix.scaledToWidth(max_w,
                                    _Qt.TransformationMode.SmoothTransformation)
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pix.save(buf, "PNG")
        data = bytes(buf.data())
        buf.close()
        return data

    def _canvas_dataurl(self, key, max_w):
        js = ("(function(){try{"
              "var cs=document.getElementsByTagName('canvas');"
              "if(!cs.length)return '';"
              "var b=cs[0];"
              "for(var i=1;i<cs.length;i++)"
              "  if(cs[i].width*cs[i].height>b.width*b.height)b=cs[i];"
              "if(!b.width||!b.height)return '';"
              "return b.toDataURL('image/png');"
              "}catch(e){return '';}})()")
        url = self._eval_js_gui(key, js, 2500).get("value")
        if not isinstance(url, str) or not url.startswith("data:image/png;base64,"):
            return b""
        import base64
        raw = base64.b64decode(url.split(",", 1)[1])
        if max_w:
            from PyQt6.QtGui import QImage
            from PyQt6.QtCore import QBuffer, QIODevice, Qt as _Qt
            img = QImage()
            if img.loadFromData(raw, "PNG") and img.width() > max_w:
                img = img.scaledToWidth(
                    max_w, _Qt.TransformationMode.SmoothTransformation)
                buf = QBuffer()
                buf.open(QIODevice.OpenModeFlag.WriteOnly)
                img.save(buf, "PNG")
                raw = bytes(buf.data())
                buf.close()
        return raw

    def eval_js(self, script, wait_ms=2500, key=None):
        return self._bridge("eval", self._key(key), js=script, wait_ms=wait_ms,
                            timeout_s=max(4.0, int(wait_ms) / 1000.0 + 2)
                            + self._LOAD_CAP_MS / 1000.0)

    def _eval_js_gui(self, key, script, wait_ms):
        out = {"done": False, "value": None}
        pg = self._page(key)
        if pg.rebuilt:
            out["rebuilt"] = True
            pg.rebuilt = False
        self._wait_loaded(key)
        _loop = QEventLoop()

        def _cb(v):
            out["value"] = v
            out["done"] = True
            _loop.quit()

        pg.runJavaScript(script, _cb)
        QTimer.singleShot(max(50, int(wait_ms)), _loop.quit)
        _loop.exec()
        return out

    def _reload(self):
        pg = self._pages.get(self._key(None))
        if pg is not None and pg.html:
            pg.console.clear()
            pg.setHtml(pg.html, QUrl(CANVAS_BASE_URL))

    def clear(self, key=None):
        self.drop(self._key(key))
        if self._key(None) == self._key(key):
            self.view.setPage(self._blank)


if __name__ == "__main__":
    from PyQt6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    w = DashboardWidget()
    w.resize(900, 600)
    w.show()
    demo = """
    <h1>Demo Dashboard</h1>
    <div class="grid">
      <div class="card">
        <h3>RAM usage per process</h3>
        <canvas id="bars"></canvas>
      </div>
      <div class="card">
        <h3>CPU per core</h3>
        <canvas id="pie"></canvas>
      </div>
    </div>
    <script>
      new Chart(document.getElementById('bars'), {
        type: 'bar',
        data: {
          labels: ['firefox', 'code', 'python', 'llama-server'],
          datasets: [{ label: 'MB', data: [1240, 890, 450, 8200],
                       backgroundColor: '#3b82f6' }]
        },
        options: { plugins: { legend: { labels: { color: '#e2e8f0' } } },
                   scales: { x: { ticks: { color: '#94a3b8' } },
                             y: { ticks: { color: '#94a3b8' } } } }
      });
      new Chart(document.getElementById('pie'), {
        type: 'doughnut',
        data: {
          labels: ['core0', 'core1', 'core2', 'core3'],
          datasets: [{ data: [45, 78, 23, 90],
                       backgroundColor: ['#3b82f6','#22c55e','#eab308','#ef4444'] }]
        },
        options: { plugins: { legend: { labels: { color: '#e2e8f0' } } } }
      });
    </script>
    """
    w.render_html(demo)
    sys.exit(app.exec())
