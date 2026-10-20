
import base64
import os
import re
import sys
import time
import uuid
import threading
import shutil
import urllib.request
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, QUrl
from PyQt6.QtWebChannel import QWebChannel

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEnginePage
    QWEBENGINE_AVAILABLE = True
    _QWEBENGINE_ERROR = None
except Exception as _e:
    QWebEngineView = None
    QWebEnginePage = None
    QWEBENGINE_AVAILABLE = False
    _QWEBENGINE_ERROR = f"{type(_e).__name__}: {_e}"

IS_WINDOWS = sys.platform == "win32"
IS_LINUX_OR_MAC = sys.platform.startswith("linux") or sys.platform == "darwin"

ANSI_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
OSC_RE = re.compile(r'\x1B\][^\x07\x1B]*(?:\x07|\x1B\\)')



def _default_shell():
    override = os.environ.get("HOPLITA_SHELL")
    if override:
        return override
    if IS_WINDOWS:
        for c in ("cmd.exe", "pwsh.exe", "powershell.exe"):
            if shutil.which(c):
                return c
        return "cmd.exe"
    for c in ("/bin/bash", "/usr/bin/bash", "/bin/sh"):
        if os.path.exists(c):
            return c
    return os.environ.get("SHELL", "/bin/sh")


def _shell_args(shell_path):
    override = os.environ.get("HOPLITA_SHELL_ARGS")
    if override is not None:
        return override.split()
    base = os.path.basename(shell_path).lower()
    if "bash" in base:
        return ["--norc", "-i"]
    if "zsh" in base:
        return ["-f", "-i"]
    if "pwsh" in base or "powershell" in base:
        return ["-NoLogo", "-NoProfile"]
    return ["-i"] if "sh" in base else []


def _shell_kind(shell_path):
    base = os.path.basename(shell_path).lower()
    if base in ("cmd.exe", "cmd"):
        return "cmd"
    if base in ("powershell.exe", "powershell", "pwsh.exe", "pwsh"):
        return "powershell"
    return "posix"



class PTYBackend:

    def __init__(self, shell_cmd, cols=100, rows=30):
        self.shell_cmd = shell_cmd
        self.cols = cols
        self.rows = rows
        self.alive = False
        self._impl = None

    def start(self, on_output):
        if IS_LINUX_OR_MAC:
            self._start_unix(on_output)
        else:
            self._start_windows(on_output)
        self.alive = True

    def _start_unix(self, on_output):
        import pty
        import select as _select
        if isinstance(self.shell_cmd, list):
            shell = self.shell_cmd
        else:
            shell = [self.shell_cmd] + _shell_args(self.shell_cmd)
        try:
            pid, fd = pty.fork()
        except Exception as e:
            raise RuntimeError(f"pty.fork() failed: {e}")
        if pid == 0:
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["COLORTERM"] = "truecolor"
            env["LANG"] = env.get("LANG", "en_US.UTF-8")
            env["PS1"] = r"\u@\h:\w\$ "
            env["BASH_SILENCE_DEPRECATION_WARNING"] = "1"
            for k in list(env.keys()):
                if k.startswith("HOPLITA_VAULT_PASSWORD"):
                    del env[k]
            try:
                os.execvpe(shell[0], shell, env)
            except Exception:
                os._exit(127)
        self._impl = ("unix", fd, pid)
        self._set_size_unix(fd, self.cols, self.rows)

        def loop():
            time.sleep(0.3)
            try:
                wpid, status = os.waitpid(pid, os.WNOHANG)
                if wpid != 0:
                    code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1
                    print(f"[PTY] ❌ Shell died on startup. "
                          f"pid={pid}, exit_code={code} "
                          f"(127 = exec failed; check that {shell[0]} exists)",
                          file=sys.stderr)
                    self.alive = False
                    return
            except ChildProcessError:
                pass
            while self.alive:
                try:
                    ready, _, _ = _select.select([fd], [], [], 0.1)
                    if not ready:
                        continue
                    data = os.read(fd, 8192)
                    if not data:
                        break
                except OSError:
                    break
                try:
                    on_output(data)
                except Exception as e:
                    print(f"[TERMINAL] on_output: {e}", file=sys.stderr)
            self.alive = False
        threading.Thread(target=loop, daemon=True, name="pty_read_unix").start()

    def _set_size_unix(self, fd, cols, rows):
        import fcntl, struct, termios
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
        except Exception:
            pass

    def _start_windows(self, on_output):
        try:
            import winpty
        except ImportError:
            raise RuntimeError(
                "The embedded terminal on Windows requires pywinpty.\n"
                "Install: pip install pywinpty\n"
                "Uses ConPTY (Win10+), supports real TUIs.")
        shell = self.shell_cmd if isinstance(self.shell_cmd, str) else " ".join(self.shell_cmd)
        try:
            proc = winpty.PtyProcess.spawn(shell, dimensions=(self.rows, self.cols))
        except Exception as e:
            raise RuntimeError(f"pywinpty could not launch '{shell}': {e}")
        self._impl = ("windows", proc)

        def loop():
            while self.alive and proc.isalive():
                try:
                    data = proc.read(blocking=True)
                except Exception:
                    break
                if not data:
                    break
                if isinstance(data, str):
                    data = data.encode("utf-8", errors="replace")
                try:
                    on_output(data)
                except Exception as e:
                    print(f"[TERMINAL] on_output: {e}", file=sys.stderr)
            self.alive = False
        threading.Thread(target=loop, daemon=True, name="pty_read_win").start()

    def write(self, data):
        if not self.alive or self._impl is None:
            return
        if isinstance(data, str):
            data = data.encode("utf-8", errors="replace")
        try:
            if self._impl[0] == "unix":
                os.write(self._impl[1], data)
            else:
                self._impl[1].write(data.decode("utf-8", errors="replace"))
        except Exception:
            pass

    def resize(self, cols, rows):
        self.cols, self.rows = cols, rows
        if self._impl is None:
            return
        try:
            if self._impl[0] == "unix":
                self._set_size_unix(self._impl[1], cols, rows)
            else:
                self._impl[1].setwinsize(rows, cols)
        except Exception:
            pass

    def kill(self):
        self.alive = False
        if self._impl is None:
            return
        try:
            if self._impl[0] == "unix":
                os.close(self._impl[1])
            else:
                self._impl[1].terminate(force=True)
        except Exception:
            pass



XTERM_VERSION = "5.3.0"
XTERM_FIT_VERSION = "0.10.0"
XTERM_WEBLINKS_VERSION = "0.11.0"

XTERM_ASSETS = {
    "xterm.css": f"https://cdn.jsdelivr.net/npm/xterm@{XTERM_VERSION}/css/xterm.min.css",
    "xterm.js": f"https://cdn.jsdelivr.net/npm/xterm@{XTERM_VERSION}/lib/xterm.min.js",
    "xterm-addon-fit.js": f"https://cdn.jsdelivr.net/npm/@xterm/addon-fit@{XTERM_FIT_VERSION}/lib/addon-fit.min.js",
    "xterm-addon-web-links.js": f"https://cdn.jsdelivr.net/npm/@xterm/addon-web-links@{XTERM_WEBLINKS_VERSION}/lib/addon-web-links.min.js",
}


def _cache_dir():
    if IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    cache = base / "hoplita" / "xterm"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _ensure_assets():
    cache = _cache_dir()
    qwc = cache / "qwebchannel.js"
    if not qwc.exists() or qwc.stat().st_size == 0:
        from PyQt6.QtCore import QFile, QIODevice
        f = QFile(":/qtwebchannel/qwebchannel.js")
        if f.open(QIODevice.OpenModeFlag.ReadOnly):
            qwc.write_bytes(bytes(f.readAll()))
            f.close()
        else:
            raise RuntimeError(
                "qwebchannel.js not in Qt resources. Is PyQt6-WebEngine missing?")
    missing = [(fn, url, cache / fn) for fn, url in XTERM_ASSETS.items()
               if not (cache / fn).exists() or (cache / fn).stat().st_size == 0]
    if missing:
        print(f"[TERMINAL] Downloading xterm.js assets to {cache} "
              f"(first run)...", file=sys.stderr)
        for fname, url, target in missing:
            try:
                urllib.request.urlretrieve(url, target)
                print(f"[TERMINAL]   ✓ {fname} ({target.stat().st_size} bytes)",
                      file=sys.stderr)
            except Exception as e:
                raise RuntimeError(
                    f"Could not download {fname} from {url}: {e}\n"
                    f"Without internet, download manually to {cache} and restart.")
    return cache



TERMINAL_HTML = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<link rel="stylesheet" href="xterm.css">
<style>
  html, body {
    margin:0; padding:0; height:100%; width:100%;
    background:#1e1e1e; overflow:hidden;
    font-family:Menlo,Consolas,"DejaVu Sans Mono",monospace;
    color: #d4d4d4;
  }
  #terminal { height:100vh; width:100vw; padding:6px; box-sizing:border-box; }
  .xterm { padding:0; }
  .xterm-viewport::-webkit-scrollbar { width:10px; }
  .xterm-viewport::-webkit-scrollbar-track { background:#1e1e1e; }
  .xterm-viewport::-webkit-scrollbar-thumb { background:#44475a; border-radius:5px; }
  .xterm-viewport::-webkit-scrollbar-thumb:hover { background:#6272a4; }
</style>
</head><body>
<div id="terminal"></div>
<script src="qwebchannel.js"></script>
<script src="xterm.js"></script>
<script src="xterm-addon-fit.js"></script>
<script src="xterm-addon-web-links.js"></script>
<script>
try {
  const term = new Terminal({
    cursorBlink: true, cursorStyle: 'block',
    fontFamily: 'Menlo,Consolas,"DejaVu Sans Mono",monospace',
    fontSize: 13, lineHeight: 1.1, scrollback: 5000,
    allowProposedApi: true, windowsMode: false,
    theme: {
      background:'#1e1e1e', foreground:'#d4d4d4',
      cursor:'#a6e22e', cursorAccent:'#1e1e1e',
      selectionBackground:'#44475a', selectionForeground:'#f8f8f2',
      black:'#000000', red:'#cd3131', green:'#0dbc79', yellow:'#e5e510',
      blue:'#2472c8', magenta:'#bc3fbc', cyan:'#11a8cd', white:'#e5e5e5',
      brightBlack:'#666666', brightRed:'#f14c4c',
      brightGreen:'#23d18b', brightYellow:'#f5f543',
      brightBlue:'#3b8eea', brightMagenta:'#d670d6',
      brightCyan:'#29b8db', brightWhite:'#ffffff'
    }
  });
  const fitAddon = new FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.loadAddon(new WebLinksAddon.WebLinksAddon());
  term.open(document.getElementById('terminal'));
  function realFit() {
    const d = fitAddon.proposeDimensions();
    if (!d || d.cols < 20 || d.rows < 4) return false;
    fitAddon.fit();
    return true;
  }
  try { realFit(); } catch(e) {}
  setTimeout(function() { try { realFit(); } catch(e) {} }, 500);

  new QWebChannel(qt.webChannelTransport, function(channel) {
    const bridge = channel.objects.bridge;
    bridge.output.connect(function(data) {
      try { term.write(data); } catch(e) { console.warn("term.write: " + e); }
    });
    term.onData(function(data) { bridge.input(data); });
    function reportSize() {
      try { if (realFit()) bridge.resize(term.cols, term.rows); } catch(e) {}
    }
    reportSize();
    new ResizeObserver(reportSize).observe(document.getElementById('terminal'));
    window.addEventListener('resize', reportSize);
    bridge.ready();
    term.focus();
  });
} catch(e) {
  console.error("Terminal init failed: " + e + " stack=" + (e.stack || 'no-stack'));
  document.body.innerHTML = '<div style="color:#cd3131;padding:20px;font-family:monospace;">' +
    '<h3>⚠ Terminal init failed</h3><pre>' + e + '</pre></div>';
}
</script>
</body></html>
"""



class TerminalBridge(QObject):
    output = pyqtSignal(str)

    def __init__(self, pty, parent=None):
        super().__init__(parent)
        self.pty = pty
        self._js_ready = False
        self._buffer = []
        self._buffer_lock = threading.Lock()

    @pyqtSlot(str)
    def input(self, data):
        self.pty.write(data)

    @pyqtSlot(int, int)
    def resize(self, cols, rows):
        if cols > 0 and rows > 0:
            self.pty.resize(cols, rows)

    @pyqtSlot()
    def ready(self):
        self._js_ready = True
        with self._buffer_lock:
            for chunk in self._buffer:
                self.output.emit(chunk)
            self._buffer.clear()

    def push_output(self, data):
        text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
        if self._js_ready:
            self.output.emit(text)
        else:
            with self._buffer_lock:
                self._buffer.append(text)



class TerminalWidget(QWebEngineView if QWEBENGINE_AVAILABLE else object):

    def __init__(self, parent=None, shell=None):
        if not QWEBENGINE_AVAILABLE:
            raise RuntimeError(
                f"PyQt6-WebEngine could not be imported.\n\n"
                f"Actual error: {_QWEBENGINE_ERROR}\n\n"
                f"Common causes:\n"
                f"  1. Mismatch between PyQt6 and PyQt6-WebEngine. "
                f"Verify that ALL versions match:\n"
                f"     pip list | grep -i pyqt6\n"
                f"     (They must all share the same major.minor version, e.g. 6.10.x)\n"
                f"  2. If there is a mismatch, align them with:\n"
                f"     pip install --upgrade --force-reinstall PyQt6 PyQt6-WebEngine\n"
                f"  3. PyQt6-WebEngine not installed in this venv:\n"
                f"     pip install PyQt6-WebEngine")
        super().__init__(parent)

        class DebugPage(QWebEnginePage):
            def javaScriptConsoleMessage(self, level, message, line, source):
                try:
                    if hasattr(level, "name"):
                        lvl = level.name.replace("MessageLevel", "").upper()
                    else:
                        lvl = str(level)
                    if lvl in ("INFO", "LOG"):
                        return
                    src = (source or "").split("/")[-1] or "?"
                    print(f"[TERMINAL JS {lvl}] {src}:{line}  {message}",
                          file=sys.stderr)
                except Exception:
                    pass
        self.setPage(DebugPage(self))

        try:
            from PyQt6.QtWebEngineCore import QWebEngineProfile
            profile = self.page().profile()
            profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
            profile.setPersistentCookiesPolicy(
                QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        except Exception as _e:
            print(f"[TERMINAL] Could not disable cache: {_e}", file=sys.stderr)

        self.shell_cmd = shell or _default_shell()
        self.shell_kind = _shell_kind(self.shell_cmd)
        self.pty = PTYBackend(self.shell_cmd, cols=100, rows=30)
        self.bridge = TerminalBridge(self.pty)

        self._capture = None
        self._capture_lock = threading.Lock()

        def on_output(data):
            text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
            with self._capture_lock:
                if self._capture is not None:
                    self._capture["buffer"] += text
            self.bridge.push_output(data)

        try:
            self.pty.start(on_output)
        except RuntimeError as e:
            self._show_error(str(e))
            return

        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)
        self.page().setWebChannel(self.channel)

        try:
            cache_dir = _ensure_assets()
            html_path = cache_dir / "terminal.html"
            html_path.write_text(TERMINAL_HTML, encoding="utf-8")
            self.load(QUrl.fromLocalFile(str(html_path)))
        except RuntimeError as e:
            print(f"[TERMINAL] ❌ _ensure_assets failed: {e}", file=sys.stderr)
            self._show_error(str(e))

    def _show_error(self, msg):
        esc = msg.replace("<", "&lt;").replace(">", "&gt;")
        self.setHtml(f"""<html><body style="background:#1e1e1e;color:#cd3131;
            font-family:monospace;padding:20px;font-size:14px;">
            <h3>⚠ Terminal unavailable</h3>
            <pre style="color:#d4d4d4;white-space:pre-wrap;">{esc}</pre>
            </body></html>""")

    @staticmethod
    def _as_one_line(command):
        if "\n" not in command.strip():
            return command
        blob = base64.b64encode(command.encode()).decode()
        return f'eval "$(printf %s {blob} | base64 -d)"'

    def execute_for_llm(self, command, timeout=60):
        if not self.pty.alive:
            return {"exit_code": -1, "output": "Terminal closed"}
        if not command or not command.strip():
            return {"exit_code": -1, "output": "Empty command"}

        marker = f"__HOPLITA_END_{uuid.uuid4().hex[:8]}__"
        start = f"__HOPLITA_BEG_{uuid.uuid4().hex[:8]}__"

        with self._capture_lock:
            self._capture = {"buffer": "", "marker": marker}

        sent = command
        if self.shell_kind == "powershell":
            self.pty.write(f'Write-Host "{start}"; {command}; '
                           f'Write-Host "{marker}_${{LASTEXITCODE}}_{marker}"\n')
        elif self.shell_kind == "cmd":
            self.pty.write(f"echo {start}& {command}& "
                           f"echo {marker}_%errorlevel%_{marker}\r\n")
        else:
            sent = self._as_one_line(command)
            self.pty.write(f"printf '%s\\n' '{start}'; {sent}; "
                           f"printf '%s_%s_%s\\n' '{marker}' \"$?\" '{marker}'\n")

        marker_pat = re.compile(rf"{re.escape(marker)}_(-?\d+)_{re.escape(marker)}")
        deadline = time.time() + timeout
        exit_code = -1
        while time.time() < deadline:
            time.sleep(0.1)
            with self._capture_lock:
                buf = self._capture["buffer"] if self._capture else ""
            m = marker_pat.search(buf)
            if m:
                exit_code = int(m.group(1))
                break

        with self._capture_lock:
            buf = self._capture["buffer"] if self._capture else ""
            self._capture = None

        clean = ANSI_RE.sub('', OSC_RE.sub('', buf))
        raw = clean.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        try:
            i = len(raw) - 1 - raw[::-1].index(start)
        except ValueError:
            i = -1
        lines = raw[i + 1:]
        out = []
        for ln in lines:
            if marker_pat.search(ln):
                break
            if marker in ln:
                continue
            out.append(ln)
        clean = "\n".join(out).strip()
        if len(clean) > 5000:
            clean = clean[:5000] + "\n...[truncated to 5000 chars]"
        return {"exit_code": exit_code, "output": clean}

    def shutdown(self):
        if self.pty:
            self.pty.kill()


if __name__ == "__main__":
    from PyQt6.QtWidgets import QApplication, QMainWindow
    app = QApplication(sys.argv)
    win = QMainWindow()
    win.setWindowTitle("TerminalWidget v2 self-test")
    win.resize(900, 600)
    term = TerminalWidget()
    win.setCentralWidget(term)
    win.show()
    print(f"Shell: {term.shell_cmd} (kind: {term.shell_kind})")
    sys.exit(app.exec())
