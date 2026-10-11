#!/usr/bin/env python3
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

os.environ.setdefault("HOPLITA_GUI_PROMPT", "1")
os.environ.setdefault("HOPLITA_LOGFILE", os.path.join(HERE, "hoplita-mcp.log"))

_interp = sys.executable
if os.name == "nt":
    _pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if os.path.exists(_pyw):
        _interp = _pyw


def _spawn(script):
    return subprocess.Popen([_interp, os.path.join(HERE, script)], cwd=HERE)


if __name__ == "__main__":
    _spawn("hoplita-mcp.py")
    time.sleep(2)
    _spawn("hoplita-gui.py")
