
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import urllib.parse
import urllib.request


def path_to_uri(path):
    return "file://" + urllib.request.pathname2url(os.path.abspath(path))


def uri_to_path(uri):
    if not uri.startswith("file://"):
        return uri
    return urllib.request.url2pathname(urllib.parse.urlparse(uri).path)


SYMBOL_KIND = {
    1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class",
    6: "method", 7: "property", 8: "field", 9: "constructor", 10: "enum",
    11: "interface", 12: "function", 13: "variable", 14: "constant",
    15: "string", 16: "number", 17: "boolean", 18: "array", 19: "object",
    20: "key", 21: "null", 22: "enum-member", 23: "struct", 24: "event",
    25: "operator", 26: "type-parameter",
}

SERVERS = {
    "python": [["pylsp"], ["pyright-langserver", "--stdio"],
               ["basedpyright-langserver", "--stdio"]],
    "go": [["gopls"]],
    "c": [["clangd"]],
    "cpp": [["clangd"]],
    "typescript": [["typescript-language-server", "--stdio"]],
    "javascript": [["typescript-language-server", "--stdio"]],
    "rust": [["rust-analyzer"]],
}

EXT_LANG = {
    ".py": "python", ".go": "go", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp", ".cxx": "cpp",
    ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".rs": "rust",
}


def language_of(path):
    return EXT_LANG.get(os.path.splitext(path)[1].lower())


def _which(name):
    here = os.path.dirname(os.path.abspath(sys.executable))
    for cand in (os.path.join(here, name), os.path.join(here, name + ".exe")):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return shutil.which(name)


def server_command(language):
    for cmd in SERVERS.get(language, []):
        found = _which(cmd[0])
        if found:
            return [found] + cmd[1:]
    return None


class LspServer:

    def __init__(self, cmd, root, on_diagnostics=None):
        self.cmd = cmd
        self.root = os.path.abspath(root)
        self.proc = None
        self._id = 0
        self._pending = {}
        self._lock = threading.Lock()
        self._wlock = threading.Lock()
        self._opened = {}
        self.diagnostics = {}
        self._on_diagnostics = on_diagnostics
        self.alive = False
        self.error = ""

    def _send(self, payload):
        body = json.dumps(payload).encode("utf-8")
        head = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        try:
            with self._wlock:
                self.proc.stdin.write(head + body)
                self.proc.stdin.flush()
        except Exception as e:
            self.alive = False
            self.error = f"write failed: {e}"

    def _reader(self):
        f = self.proc.stdout
        while True:
            try:
                length = None
                while True:
                    line = f.readline()
                    if not line:
                        raise EOFError
                    line = line.decode("utf-8", "replace").strip()
                    if not line:
                        break
                    if line.lower().startswith("content-length:"):
                        length = int(line.split(":", 1)[1].strip())
                if length is None:
                    continue
                raw = f.read(length)
                msg = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                self.alive = False
                for q in list(self._pending.values()):
                    q.put({"error": {"message": "server closed"}})
                return
            if "id" in msg and ("result" in msg or "error" in msg):
                q = self._pending.pop(msg["id"], None)
                if q is not None:
                    q.put(msg)
            elif msg.get("method") == "textDocument/publishDiagnostics":
                p = msg.get("params") or {}
                self.diagnostics[p.get("uri", "")] = p.get("diagnostics", [])
                if self._on_diagnostics:
                    try:
                        self._on_diagnostics(p)
                    except Exception:
                        pass
            elif "id" in msg:
                self._send({"jsonrpc": "2.0", "id": msg["id"], "result": None})

    def request(self, method, params, timeout=20):
        if not self.alive:
            return None
        with self._lock:
            self._id += 1
            rid = self._id
            q = queue.Queue(maxsize=1)
            self._pending[rid] = q
        self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                    "params": params})
        try:
            msg = q.get(timeout=timeout)
        except queue.Empty:
            self._pending.pop(rid, None)
            self.error = f"{method} timed out after {timeout}s"
            return None
        if msg.get("error"):
            self.error = str(msg["error"].get("message", msg["error"]))[:200]
            return None
        return msg.get("result")

    def notify(self, method, params):
        if self.alive:
            self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def start(self, timeout=30):
        try:
            import hoplita_paths as _hp
            self.proc = subprocess.Popen(
                self.cmd, cwd=self.root, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                **_hp.no_console())
        except Exception as e:
            self.error = f"could not launch {self.cmd[0]}: {e}"
            return False
        self.alive = True
        threading.Thread(target=self._reader, daemon=True,
                         name=f"lsp-{self.cmd[0]}").start()
        res = self.request("initialize", {
            "processId": os.getpid(),
            "rootUri": path_to_uri(self.root),
            "workspaceFolders": [{"uri": path_to_uri(self.root),
                                  "name": os.path.basename(self.root)}],
            "capabilities": {
                "textDocument": {
                    "references": {"dynamicRegistration": False},
                    "definition": {"dynamicRegistration": False},
                    "rename": {"dynamicRegistration": False},
                    "publishDiagnostics": {"relatedInformation": False},
                },
                "workspace": {"workspaceFolders": True, "symbol": {}},
            },
        }, timeout=timeout)
        if res is None:
            self.stop()
            self.error = self.error or "initialize got no answer"
            return False
        self.capabilities = (res or {}).get("capabilities", {})
        self.notify("initialized", {})
        return True

    def stop(self):
        self.alive = False
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=3)
                except Exception:
                    self.proc.kill()
        except Exception:
            pass

    def open_file(self, path, text=None):
        uri = path_to_uri(path)
        if text is None:
            try:
                text = open(path, encoding="utf-8", errors="replace").read()
            except Exception:
                return False
        version = self._opened.get(uri, 0) + 1
        self._opened[uri] = version
        if version == 1:
            self.notify("textDocument/didOpen", {"textDocument": {
                "uri": uri, "languageId": language_of(path) or "plaintext",
                "version": version, "text": text}})
        else:
            self.notify("textDocument/didChange", {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{"text": text}]})
        return True

    def references(self, path, line, character, include_decl=True):
        return self.request("textDocument/references", {
            "textDocument": {"uri": path_to_uri(path)},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": include_decl}})

    def document_symbols(self, path):
        res = self.request("textDocument/documentSymbol", {
            "textDocument": {"uri": path_to_uri(path)}})
        if not res:
            return []
        out = []

        def walk(items, depth=0):
            for it in items:
                rng = it.get("selectionRange") or it.get("range") or \
                    (it.get("location") or {}).get("range") or {}
                start = (rng.get("start") or {})
                out.append({"name": it.get("name", "?"),
                            "kind": it.get("kind", 0),
                            "depth": depth,
                            "line": start.get("line", 0) + 1,
                            "detail": (it.get("detail") or "")[:60]})
                if it.get("children"):
                    walk(it["children"], depth + 1)

        walk(res)
        return out

    def definition(self, path, line, character):
        return self.request("textDocument/definition", {
            "textDocument": {"uri": path_to_uri(path)},
            "position": {"line": line, "character": character}})


class LspPool:

    def __init__(self):
        self._servers = {}
        self._lock = threading.Lock()

    def get(self, root, language, start_timeout=30):
        if not language:
            return None, "unsupported file type"
        key = (os.path.abspath(root), language)
        with self._lock:
            srv = self._servers.get(key)
            if srv is not None and srv.alive:
                return srv, ""
            cmd = server_command(language)
            if cmd is None:
                names = ", ".join(c[0] for c in SERVERS.get(language, []))
                return None, f"no language server installed for {language}" + (
                    f" (tried: {names})" if names else "")
            srv = LspServer(cmd, root)
            if not srv.start(timeout=start_timeout):
                return None, f"{cmd[0]} did not start: {srv.error}"
            self._servers[key] = srv
            return srv, ""

    def stop_all(self):
        with self._lock:
            for srv in self._servers.values():
                srv.stop()
            self._servers.clear()

    def running(self):
        with self._lock:
            return [{"root": k[0], "language": k[1], "cmd": s.cmd[0],
                     "alive": s.alive}
                    for k, s in self._servers.items()]


POOL = LspPool()
