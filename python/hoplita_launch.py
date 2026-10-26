#!/usr/bin/env python3
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

LOG = os.path.join(HERE, "mcp.log" if sys.platform.startswith("linux")
                   else "hoplita-mcp.log")

_interp = sys.executable
if os.name == "nt":
    _pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if os.path.exists(_pyw):
        _interp = _pyw


def mcp_url():
    import configparser
    sys.path.insert(0, HERE)
    try:
        import hoplita_paths
        cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        cfg.read(hoplita_paths.config_path(), encoding="utf-8")
        return cfg.get("MCP", "url", fallback="http://127.0.0.1:9077").rstrip("/")
    except Exception:
        return "http://127.0.0.1:9077"


def mcp_answers(url=None):
    import urllib.request
    try:
        with urllib.request.urlopen((url or mcp_url()) + "/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _spawn(script, *args):
    return subprocess.Popen([_interp, os.path.join(HERE, script), *args], cwd=HERE)


def mcp_args(url):
    from urllib.parse import urlparse
    u = urlparse(url)
    if u.hostname in ("127.0.0.1", "localhost") and u.port:
        return ["--port", str(u.port)]
    return []


def main():
    url = mcp_url()
    if not mcp_answers(url):
        os.environ.setdefault("HOPLITA_GUI_PROMPT", "1")
        os.environ.setdefault("HOPLITA_LOGFILE", LOG)
        _spawn("hoplita-mcp.py", *mcp_args(url))
        time.sleep(2)
    _spawn("hoplita-gui.py")


if __name__ == "__main__":
    main()
