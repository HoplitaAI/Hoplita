#!/usr/bin/env python3

import argparse
import json
import os
import re
import platform
import shutil
import subprocess
import sys
import time

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

SRC_DIR = os.path.dirname(os.path.abspath(__file__))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

_TTY = sys.stdout.isatty()
def _c(code, s):
    return f"\033[{code}m{s}\033[0m" if _TTY else s
def ok(s):   print(_c("32", "✓ ") + s)
def warn(s): print(_c("33", "⚠ ") + s)
def err(s):  print(_c("31", "✗ ") + s, file=sys.stderr)
def step(s): print("\n" + _c("36;1", "▶ " + s))


def again(flags=""):
    cmd = os.environ.get("HOPLITA_RERUN") or "python install.py"
    return f"{cmd} {flags}".rstrip()


def default_prefix():
    if IS_WINDOWS:
        appdata = os.getenv("APPDATA", "")
        return os.path.join(appdata, "hoplita") if appdata else \
            os.path.join(os.path.expanduser("~"), "hoplita")
    return os.path.join(os.path.expanduser("~"), "hoplita")


LEGACY_PREFIX = None if IS_WINDOWS else "/opt/hoplita"


def _is_install(path):
    return os.path.exists(os.path.join(path, MANIFEST)) or \
        os.path.exists(os.path.join(path, "hoplita-mcp.py"))


def resolve_prefix(arg):
    if arg:
        return os.path.abspath(os.path.expanduser(arg))
    new = default_prefix()
    if LEGACY_PREFIX and not _is_install(new) and _is_install(LEGACY_PREFIX):
        return LEGACY_PREFIX
    return new


def venv_python(venv_dir):
    if IS_WINDOWS:
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def venv_pythonw(venv_dir):
    if IS_WINDOWS:
        pw = os.path.join(venv_dir, "Scripts", "pythonw.exe")
        return pw if os.path.exists(pw) else venv_python(venv_dir)
    return None


def desktop_dir():
    if IS_LINUX:
        try:
            r = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True,
                               text=True, timeout=5)
            d = r.stdout.strip()
            if d and os.path.isdir(d):
                return d
        except Exception:
            pass
    for name in ("Desktop", "Escritorio"):
        d = os.path.join(os.path.expanduser("~"), name)
        if os.path.isdir(d):
            return d
    return os.path.expanduser("~")



def make_shortcuts(prefix, venv_dir):
    desk = desktop_dir()
    icon_png = os.path.join(prefix, "hoplita.png")
    py = venv_python(venv_dir)
    pyw = venv_pythonw(venv_dir) or py
    mcp_script = os.path.join(prefix, "hoplita-mcp.py")
    gui_script = os.path.join(prefix, "hoplita-gui.py")

    if IS_LINUX:
        return _make_linux_desktop(desk, prefix, py, pyw, mcp_script, gui_script, icon_png)
    if IS_MAC:
        return _make_mac_app(desk, prefix, py, icon_png)
    if IS_WINDOWS:
        return _make_windows_lnk(desk, prefix, py, pyw, mcp_script, gui_script, icon_png)
    return []


_LINUX_DESKTOP = "HOPLITA.desktop"
_LINUX_OLD = ("HOPLITA-MCP.desktop", "HOPLITA-GUI.desktop")


def _make_linux_desktop(desk, prefix, py, pyw, mcp, gui, icon):
    created = []
    path = os.path.join(desk, _LINUX_DESKTOP)
    launcher = os.path.join(prefix, "hoplita_launch.py")
    content = (
        "[Desktop Entry]\n"
        "Version=1.0\nType=Application\n"
        "Name=HOPLITA\n"
        f'Exec="{py}" "{launcher}"\n'
        f"Icon={icon if os.path.exists(icon) else 'applications-chat'}\n"
        f"Path={prefix}\n"
        "Terminal=false\nCategories=Utility;\n")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(path, 0o755)
        try:
            subprocess.run(["gio", "set", path, "metadata::trusted", "true"],
                           capture_output=True, timeout=5)
        except Exception:
            pass
        created.append(path)
    except Exception as e:
        warn(f"could not create {_LINUX_DESKTOP}: {e}")
        return created
    for old in _LINUX_OLD:
        p = os.path.join(desk, old)
        if os.path.exists(p) and _shortcut_is_ours(p, prefix):
            try:
                os.remove(p)
            except Exception:
                pass
    return created


_MAC_APP = "HOPLITA.app"
_MAC_OLD = ("HOPLITA-MCP.command", "HOPLITA-GUI.command")


def _make_mac_app(desk, prefix, py, icon_png):
    created = []
    py = _mac_named_interpreter(os.path.dirname(os.path.dirname(py))) or py
    apps = os.path.expanduser("~/Applications")
    os.makedirs(apps, exist_ok=True)
    app = os.path.join(apps, _MAC_APP)
    macos = os.path.join(app, "Contents", "MacOS")
    res = os.path.join(app, "Contents", "Resources")
    try:
        os.makedirs(macos, exist_ok=True)
        os.makedirs(res, exist_ok=True)
        exe = os.path.join(macos, "HOPLITA")
        with open(exe, "w", encoding="utf-8") as f:
            f.write("#!/bin/bash\n"
                    f'cd "{prefix}"\n'
                    f'"{py}" "{os.path.join(prefix, "hoplita_launch.py")}"\n')
        os.chmod(exe, 0o755)
        icon_key = ""
        if os.path.exists(icon_png) and _mac_icns(icon_png, os.path.join(res, "hoplita.icns")):
            icon_key = "  <key>CFBundleIconFile</key><string>hoplita</string>\n"
        with open(os.path.join(app, "Contents", "Info.plist"), "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict>\n'
                    "  <key>CFBundleName</key><string>HOPLITA</string>\n"
                    "  <key>CFBundleDisplayName</key><string>HOPLITA</string>\n"
                    "  <key>CFBundleIdentifier</key><string>net.hoplita.launcher</string>\n"
                    "  <key>CFBundleExecutable</key><string>HOPLITA</string>\n"
                    "  <key>CFBundlePackageType</key><string>APPL</string>\n"
                    "  <key>CFBundleShortVersionString</key><string>1.0</string>\n"
                    + icon_key +
                    "</dict></plist>\n")
        created.append(app)
    except Exception as e:
        warn(f"could not create {_MAC_APP}: {e}")
        return created
    link = os.path.join(desk, _MAC_APP)
    try:
        if os.path.islink(link):
            os.remove(link)
        elif os.path.isdir(link) and _shortcut_is_ours(link, prefix):
            shutil.rmtree(link)
        if not os.path.exists(link):
            os.symlink(app, link)
            created.append(link)
    except Exception as e:
        warn(f"could not put {_MAC_APP} on the desktop: {e}")
    for old in _MAC_OLD:
        p = os.path.join(desk, old)
        if os.path.exists(p) and _shortcut_is_ours(p, prefix):
            try:
                os.remove(p)
            except Exception:
                pass
    return created


def _mac_named_interpreter(venv_dir):
    import filecmp
    real = os.path.realpath(os.path.join(venv_dir, "bin", "python"))
    dst = os.path.join(venv_dir, "bin", "HOPLITA")
    cfg = os.path.join(venv_dir, "pyvenv.cfg")
    try:
        lines = open(cfg, encoding="utf-8").read().splitlines()
        want = os.path.dirname(real)
        fixed = [f"home = {want}" if l.startswith("home =") else l for l in lines]
        if fixed != lines:
            with open(cfg, "w", encoding="utf-8") as f:
                f.write("\n".join(fixed) + "\n")
        if not (os.path.exists(dst) and filecmp.cmp(real, dst, shallow=False)):
            shutil.copy2(real, dst)
            subprocess.run(["codesign", "-s", "-", "--force", "--identifier",
                            "net.hoplita.HOPLITA", dst], capture_output=True, timeout=60)
        r = subprocess.run([dst, "-c", "import sys, encodings"], capture_output=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or b"").decode(errors="replace")[-200:])
        return dst
    except Exception as e:
        warn(f"HOPLITA runs as python on this Mac (permissions are asked for "
             f"python3.12): {e}")
        try:
            os.remove(dst)
        except OSError:
            pass
        return None


def _mac_icns(png, icns):
    import tempfile
    tmp = tempfile.mkdtemp(prefix="hoplita-icon-")
    iconset = os.path.join(tmp, "hoplita.iconset")
    try:
        os.makedirs(iconset)
        for size in (16, 32, 128, 256, 512):
            for scale, name in ((1, f"icon_{size}x{size}.png"), (2, f"icon_{size}x{size}@2x.png")):
                subprocess.run(["sips", "-z", str(size * scale), str(size * scale), png,
                                "--out", os.path.join(iconset, name)],
                               capture_output=True, timeout=30, check=True)
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns],
                       capture_output=True, timeout=30, check=True)
        return os.path.exists(icns)
    except Exception:
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _make_windows_lnk(desk, prefix, py, pyw, mcp, gui, icon):
    created = []
    ico_file = os.path.join(prefix, "hoplita.ico")
    ico = ico_file if os.path.exists(ico_file) else py
    launcher = os.path.join(prefix, "hoplita_launch.py")

    specs = [
        ("HOPLITA.lnk", pyw, f'"{launcher}"', prefix, ico, 7),
    ]

    for fname, target, args, workdir, iconloc, style in specs:
        lnk = os.path.join(desk, fname)
        ps = (
            "$s=(New-Object -COM WScript.Shell).CreateShortcut('%s');"
            "$s.TargetPath='%s';$s.Arguments='%s';"
            "$s.WorkingDirectory='%s';$s.IconLocation='%s';"
            "$s.WindowStyle=%d;$s.Save()"
        ) % (lnk.replace("'", "''"), target.replace("'", "''"),
             args.replace("'", "''"), workdir.replace("'", "''"),
             iconloc.replace("'", "''"), style)
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=20, check=True)
            created.append(lnk)
        except Exception as e:
            warn(f"could not create {fname}: {e}")
    return created


def compile_cli(prefix):
    step("Building the Go CLI")
    if not shutil.which("go"):
        warn("Go is not installed: skipping the CLI.")
        return
    cli_dir = os.path.join(prefix, "cli")
    goos = {"Windows": "windows", "Darwin": "darwin"}.get(platform.system(), "linux")
    env = dict(os.environ, GOOS=goos)
    binname = "hoplita-cli.exe" if IS_WINDOWS else "hoplita-cli"
    out = os.path.join(cli_dir, "dist", binname)
    os.makedirs(os.path.join(cli_dir, "dist"), exist_ok=True)
    r = run(["go", "build", "-o", out, "."], cwd=cli_dir, env=env)
    if r.returncode == 0:
        ok(f"CLI built: {out}")
        _link_cli(out)
        return
    warn("CLI build failed.")
    if os.path.exists(out):
        warn(f"the OLD binary is still at {out} — it was built from sources "
             f"this update replaced. Do not use it until this compiles.")
    src_cli = os.path.join(SRC_DIR, "cli")
    strays = sorted(f for f in os.listdir(src_cli) if f.endswith(".go")) \
        if os.path.isdir(src_cli) else []
    if strays:
        print(f"   · .go in the SOURCE ({src_cli}): {' '.join(strays)}")
        print("   · if the errors above are 'undefined:', one of those is an "
              "orphan from an older version — it gets shipped and compiled "
              "even though nobody imports it. `scp -r` MERGES into an existing "
              "dir and leaves such files behind; `rsync -a --delete` mirrors.")


def _cli_link_path():
    return os.path.join(os.path.expanduser("~"), ".local", "bin", "hoplita-cli")


def _link_cli(out):
    if IS_WINDOWS:
        print(f"   · run it as: {out}")
        return
    link = _cli_link_path()
    try:
        os.makedirs(os.path.dirname(link), exist_ok=True)
        if os.path.islink(link) or os.path.exists(link):
            os.remove(link)
        os.symlink(out, link)
        ok(f"`hoplita-cli` → {link}")
    except Exception as e:
        warn(f"could not link {link}: {e}")
        print(f"   · run it as: {out}")
        return
    bindir = os.path.dirname(link)
    if bindir not in os.environ.get("PATH", "").split(os.pathsep):
        shell_rc = "~/.zshrc" if IS_MAC else "~/.bashrc"
        warn(f"{bindir} is not in your PATH: add it, then open a new terminal:")
        print(f"   echo 'export PATH=\"$HOME/.local/bin:$PATH\"' >> {shell_rc}")


MANIFEST = ".hoplita-installed.json"


def _prune_stale(prefix, shipped):
    import json as _json
    mpath = os.path.join(prefix, MANIFEST)
    previous = []
    try:
        with open(mpath, encoding="utf-8") as fh:
            previous = _json.load(fh).get("files", [])
    except Exception:
        previous = []
    cli_dir = os.path.join(prefix, "cli")
    if os.path.isdir(cli_dir):
        for f in os.listdir(cli_dir):
            if f.endswith(".go") and os.path.join("cli", f) not in shipped:
                previous.append(os.path.join("cli", f))
    removed = 0
    for rel in set(previous) - set(shipped):
        victim = os.path.join(prefix, rel)
        if os.path.isfile(victim):
            try:
                os.remove(victim)
                removed += 1
            except Exception as e:
                warn(f"could not remove the stale {rel}: {e}")
    if removed:
        ok(f"{removed} stale file(s) removed")
    try:
        with open(mpath, "w", encoding="utf-8") as fh:
            _json.dump({"files": sorted(shipped)}, fh, indent=1)
    except Exception as e:
        warn(f"could not write {MANIFEST}: {e}")



CHILD_MARK = ".hoplita-child.json"
SPAWN_VARS = ("HOPLITA_SPAWN_SERVER", "HOPLITA_SPAWN_HANDLE", "HOPLITA_SPAWN_TOKEN")
SPAWN_FILE = "spawn.env"


def take_spawn_env():
    return {k: os.environ.pop(k) for k in SPAWN_VARS if os.environ.get(k)}


def child_mark(prefix):
    try:
        with open(os.path.join(prefix, CHILD_MARK), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def child_prefix(handle):
    base = os.getenv("APPDATA", "") if IS_WINDOWS else ""
    return os.path.join(base or os.path.expanduser("~"), f"hoplita-{handle}")


def _child_port(prefix):
    import glob
    import socket
    had = (child_mark(prefix) or {}).get("port")
    if had:
        return had
    taken = set()
    for d in {os.path.dirname(os.path.abspath(prefix)), os.path.expanduser("~")}:
        for m in glob.glob(os.path.join(d, "*", CHILD_MARK)):
            try:
                with open(m, encoding="utf-8") as fh:
                    taken.add(int(json.load(fh).get("port") or 0))
            except Exception:
                pass
    for port in range(9177, 9277):
        if port in taken:
            continue
        with socket.socket() as sk:
            try:
                sk.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit("No free port between 9177 and 9276 for the child.")


def child_requirements(prefix):
    out = os.path.join(prefix, ".requirements-child.txt")
    keep = [ln for ln in open(os.path.join(SRC_DIR, "requirements.txt"), encoding="utf-8")
            if not ln.split("#", 1)[0].strip().lower().startswith("pyqt6")]
    with open(out, "w", encoding="utf-8") as fh:
        fh.writelines(keep)
    return out


def _service_name(name):
    return f"hoplita-{name}"


def _child_argv(prefix, py, port):
    if IS_WINDOWS:
        pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
        py = pyw if os.path.exists(pyw) else py
    return [py, os.path.join(prefix, "hoplita-mcp.py"), "--host", "127.0.0.1",
            "--port", str(port)]


def _systemd_user():
    return bool(shutil.which("systemctl")) and subprocess.run(
        ["systemctl", "--user", "show-environment"], capture_output=True).returncode == 0


def install_child_service(prefix, py, name, port):
    argv = _child_argv(prefix, py, port)
    log = os.path.join(prefix, "hoplita-child.log")
    svc = _service_name(name)
    if IS_LINUX and _systemd_user():
        unit = os.path.join(prefix, f"{svc}.service")
        q = " ".join(f'"{a}"' for a in argv)
        with open(unit, "w", encoding="utf-8") as fh:
            fh.write(f"""[Unit]
Description=HOPLITA child {name}
After=network-online.target
Wants=network-online.target

[Service]
Environment="HOPLITA_HOME={prefix}" "PYTHONUNBUFFERED=1"
ExecStart={q}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
""")
        subprocess.run(["systemctl", "--user", "disable", "--now", f"{svc}.service"],
                       capture_output=True)
        r = subprocess.run(["systemctl", "--user", "enable", "--now", unit],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"systemd would not start {svc}: {r.stderr.strip()}")
        user = os.environ.get("USER") or os.path.basename(os.path.expanduser("~"))
        lg = subprocess.run(["loginctl", "show-user", user, "-p", "Linger"],
                            capture_output=True, text=True).stdout.strip()
        if lg != "Linger=yes":
            if subprocess.run(["loginctl", "enable-linger", user],
                              capture_output=True).returncode == 0:
                ok("Lingering on: the child keeps running after you log out")
            else:
                warn("The child stops when you log out of this machine. To keep it "
                     f"running: sudo loginctl enable-linger {user}")
        return f"systemd user unit {svc}", f"journalctl --user -u {svc} -f"
    if IS_MAC:
        label = f"net.hoplita.{svc}"
        plist = os.path.expanduser(f"~/Library/LaunchAgents/{label}.plist")
        os.makedirs(os.path.dirname(plist), exist_ok=True)
        import plistlib
        with open(plist, "wb") as fh:
            plistlib.dump({"Label": label, "ProgramArguments": argv,
                           "EnvironmentVariables": {"HOPLITA_HOME": prefix,
                                                    "PYTHONUNBUFFERED": "1"},
                           "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False},
                           "StandardOutPath": log, "StandardErrorPath": log}, fh)
        dom = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", dom, plist], capture_output=True)
        r = subprocess.run(["launchctl", "bootstrap", dom, plist], capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"launchd would not start {label}: {r.stderr.strip()}")
        return f"LaunchAgent {label}", f"tail -f {log}"
    if IS_WINDOWS:
        task = f"HOPLITA {svc}"
        tr = " ".join(f'"{a}"' if " " in a else a for a in argv)
        subprocess.run(["schtasks", "/End", "/TN", task], capture_output=True)
        r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "ONLOGON", "/RL", "LIMITED",
                            "/TN", task, "/TR", tr], capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"Task Scheduler would not take {task}: "
                             f"{(r.stderr or r.stdout).strip()}")
        subprocess.run(["schtasks", "/Run", "/TN", task], capture_output=True)
        return f"logon task «{task}»", os.path.join(prefix, "hoplita-mcp.log")
    _stop_detached_child(prefix)
    env = dict(os.environ, HOPLITA_HOME=prefix, PYTHONUNBUFFERED="1")
    with open(log, "ab") as fh:
        p = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=fh,
                             stderr=fh, start_new_session=True)
    with open(os.path.join(prefix, "hoplita-child.pid"), "w") as fh:
        fh.write(str(p.pid))
    warn("No service manager for this user: the child runs as a detached "
         "process and will not start again after a reboot.")
    return f"process {p.pid}", f"tail -f {log}"


def _stop_detached_child(prefix, wait=15):
    import signal
    pidf = os.path.join(prefix, "hoplita-child.pid")
    try:
        pid = int(open(pidf).read().strip())
    except Exception:
        return
    cmdline = f"/proc/{pid}/cmdline"
    if os.path.exists("/proc"):
        try:
            if prefix not in open(cmdline, "rb").read().decode(errors="replace"):
                return
        except OSError:
            return
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        return
    end = time.time() + wait
    while time.time() < end:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.3)
    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def remove_child_service(prefix, name):
    svc = _service_name(name)
    if IS_LINUX and _systemd_user():
        subprocess.run(["systemctl", "--user", "disable", "--now", f"{svc}.service"],
                       capture_output=True)
    elif IS_MAC:
        label = f"net.hoplita.{svc}"
        plist = os.path.expanduser(f"~/Library/LaunchAgents/{label}.plist")
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", plist],
                       capture_output=True)
        if os.path.exists(plist):
            os.remove(plist)
    elif IS_WINDOWS:
        task = f"HOPLITA {svc}"
        subprocess.run(["schtasks", "/End", "/TN", task], capture_output=True)
        subprocess.run(["schtasks", "/Delete", "/F", "/TN", task], capture_output=True)
    _stop_detached_child(prefix)
    ok(f"Child service {svc} stopped and removed")


def install_child(prefix, py, spawn):
    mark = child_mark(prefix) or {}
    name = spawn.get("HOPLITA_SPAWN_HANDLE") or mark.get("name") \
        or os.path.basename(prefix).removeprefix("hoplita-")
    port = _child_port(prefix)
    if spawn:
        fd = os.open(os.path.join(prefix, SPAWN_FILE),
                     os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.writelines(f"{k}={v}\n" for k, v in spawn.items())
    with open(os.path.join(prefix, CHILD_MARK), "w", encoding="utf-8") as fh:
        json.dump({"name": name, "port": port}, fh)
    step(f"Starting the child {name}")
    how, logs = install_child_service(prefix, py, name, port)
    ok(f"Running as {how}, on 127.0.0.1:{port}")
    return name, port, how, logs



_OURS_DIRS = ("venv", "lang", "icons", "cli", "docs", "bin", "wb_canvas",
              "wb_instructions", "council_view", "calendar_view", "__pycache__",
              "python")
_OURS_FILES = ("config.ini.example", "README.md", "requirements.txt", MANIFEST,
               CHILD_MARK, ".requirements-child.txt", "hoplita-child.pid", SPAWN_FILE)


def _shortcut_paths():
    desk = desktop_dir()
    names = {"Linux": (_LINUX_DESKTOP,) + _LINUX_OLD,
             "Darwin": (_MAC_APP,) + _MAC_OLD,
             "Windows": ("HOPLITA.lnk",)}.get(platform.system(), ())
    out = [os.path.join(desk, n) for n in names]
    if IS_MAC:
        out.append(os.path.join(os.path.expanduser("~/Applications"), _MAC_APP))
    return out


def _shortcut_is_ours(path, prefix):
    if path.endswith(".app"):
        path = os.path.join(path, "Contents", "MacOS", "HOPLITA")
    if path.endswith(".lnk"):
        return os.path.normcase(prefix) == os.path.normcase(default_prefix())
    try:
        return prefix in open(path, encoding="utf-8", errors="replace").read()
    except Exception:
        return False


def _cache_paths():
    home = os.path.expanduser("~")
    app = "HOPLITA Chat Assistant"
    out = [os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.join(home, ".cache"),
                        "hoplita")]
    if IS_MAC:
        out += [os.path.join(home, "Library", "Caches", app),
                os.path.join(home, "Library", "Application Support", app)]
    elif IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        out += [os.path.join(local, app)]
    else:
        out += [os.path.join(home, ".cache", app),
                os.path.join(home, ".local", "share", app)]
    return [p for p in out if os.path.exists(p)]


def _settings_paths():
    home = os.path.expanduser("~")
    if IS_WINDOWS:
        return []
    if IS_MAC:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        if xdg:
            return [os.path.join(xdg, "hoplita")]
        return [os.path.join(home, "Library", "Preferences", "com.hoplita.hoplita.plist")]
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    return [os.path.join(base, "hoplita")]


def _program_entries(prefix):
    import json as _json
    names = set()
    try:
        with open(os.path.join(prefix, MANIFEST), encoding="utf-8") as fh:
            names.update(_json.load(fh).get("files", []))
    except Exception:
        pass
    src_py = os.path.join(SRC_DIR, "python")
    if os.path.isdir(src_py):
        names.update(f for f in os.listdir(src_py) if f.endswith(".py"))
    out = []
    for d in _OURS_DIRS:
        p = os.path.join(prefix, d)
        if os.path.isdir(p):
            out.append((p, True))
    top = {n for n in names if os.sep not in n and "/" not in n}
    for f in sorted(top | set(_OURS_FILES)):
        p = os.path.join(prefix, f)
        if os.path.isfile(p):
            out.append((p, False))
    for f in os.listdir(prefix):
        if f.startswith("hoplita") and f.endswith((".png", ".ico", ".service")):
            out.append((os.path.join(prefix, f), False))
    return out


def uninstall(prefix, purge=False, yes=False):
    step(f"Uninstalling HOPLITA from {prefix}")
    home = os.path.abspath(os.path.expanduser("~"))
    if os.path.abspath(prefix) in ("/", home, os.path.dirname(home)) \
            or not os.path.isdir(prefix):
        err(f"{prefix} is not a HOPLITA install: nothing removed.")
        sys.exit(1)
    if not (os.path.exists(os.path.join(prefix, MANIFEST))
            or os.path.exists(os.path.join(prefix, "hoplita-mcp.py"))):
        err(f"{prefix} has neither {MANIFEST} nor hoplita-mcp.py: "
            "it does not look like a HOPLITA install. Nothing removed.")
        sys.exit(1)
    program = _program_entries(prefix)
    mark = child_mark(prefix)
    shortcuts = [p for p in _shortcut_paths()
                 if os.path.exists(p) and _shortcut_is_ours(p, prefix)]
    caches = _cache_paths()
    _cl = _cli_link_path()
    if os.path.islink(_cl) and os.path.realpath(_cl).startswith(os.path.realpath(prefix) + os.sep):
        caches.append(_cl)
    ours = {p for p, _d in program}
    data = sorted(os.path.join(prefix, f) for f in os.listdir(prefix)
                  if os.path.join(prefix, f) not in ours)
    boards = os.path.expanduser("~/.hoplita")
    extra_data = [p for p in [boards] + _settings_paths() if os.path.exists(p)]

    print("\nProgram (removed):")
    for p, is_dir in program:
        print(f"  - {p}{os.sep if is_dir else ''}")
    for p in shortcuts + caches:
        print(f"  - {p}")
    print("\nYour data (" + ("REMOVED with --purge" if purge else "KEPT") + "):")
    for p in data + extra_data:
        print(f"  · {p}")
    if IS_WINDOWS:
        print("  · registry HKCU\\Software\\hoplita (the GUI's settings)")
    if not (data or extra_data):
        print("  (none)")
    print("\nClose HOPLITA (the GUI and the MCP) before going on.")
    if purge:
        ans = input(_c("31;1", "This deletes your vault, memory and notes for good. "
                                "Type DELETE to confirm: ")).strip()
        if ans != "DELETE":
            err("Not confirmed: nothing removed.")
            sys.exit(1)
    elif not yes:
        if input("Uninstall? [y/N] ").strip().lower() not in ("y", "yes", "s", "si", "sí"):
            err("Cancelled: nothing removed.")
            sys.exit(1)

    failed = []

    def _rm(p):
        try:
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p)
            else:
                os.remove(p)
        except Exception as e:
            failed.append(f"{p}: {e}")

    if mark:
        remove_child_service(prefix, mark.get("name", ""))
    for p, _d in program:
        _rm(p)
    for p in shortcuts + caches:
        _rm(p)
    if purge:
        for p in data + extra_data:
            _rm(p)
        if IS_WINDOWS:
            try:
                import winreg
                for key in (r"Software\hoplita\hoplita", r"Software\hoplita"):
                    try:
                        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
                    except FileNotFoundError:
                        pass
            except Exception as e:
                failed.append(f"registry: {e}")
    try:
        if not os.listdir(prefix):
            os.rmdir(prefix)
    except Exception:
        pass

    if failed:
        warn(f"{len(failed)} item(s) could not be removed (in use, or permissions):")
        for f in failed:
            print(f"  ! {f}")
    ok(_c("32;1", "HOPLITA uninstalled" + (" with its data." if purge else ".")))
    if not purge and (data or extra_data):
        print("Your data is still there (listed above). To remove it too:")
        print("  " + again("--uninstall --purge"
                           + (f" --prefix {prefix}" if prefix != default_prefix() else "")))
    return not failed


def _report_new_settings(prefix):
    import configparser as _cp
    cfg_path = os.path.join(prefix, "config.ini")
    ex_path = os.path.join(prefix, "config.ini.example")
    if not (os.path.exists(cfg_path) and os.path.exists(ex_path)):
        return
    yours, theirs = _cp.ConfigParser(inline_comment_prefixes=(";", "#")), \
        _cp.ConfigParser(inline_comment_prefixes=(";", "#"))
    try:
        yours.read(cfg_path, encoding="utf-8")
        theirs.read(ex_path, encoding="utf-8")
    except Exception:
        return
    new = [f"[{sec}] {k}" for sec in theirs.sections()
           for k in theirs.options(sec)
           if not (yours.has_section(sec) and yours.has_option(sec, k))]
    if not new:
        return
    step(f"{len(new)} new setting(s) your config.ini does not mention")
    for n in new:
        print(f"   · {n}")
    print("   (they use their defaults; edit them in the GUI → Settings)")


def install_lsp(prefix):
    step("Installing the language server (Python)")
    py = os.path.join(prefix, "venv", "bin", "python")
    if not os.path.exists(py):
        py = os.path.join(prefix, "venv", "Scripts", "python.exe")
    if not os.path.exists(py):
        err(f"{prefix} has no venv ({os.path.join(prefix, 'venv')}).")
        print("   · This install is incomplete: the MCP cannot start there either.")
        print(f"   · Complete it:  {again(f'--prefix {prefix}')}")
        return
    r = pip_install(py, ["python-lsp-server[all]"])
    if r.returncode != 0:
        warn("python-lsp-server[all] could not be installed. Without it the code "
             "view works the same but with no diagnostics or semantic "
             "navigation — and it says so on screen.")
        return
    ok("pylsp installed. The editor will underline errors and the function bar "
       "will come from the server.")
    print("   · For Go:    go install golang.org/x/tools/gopls@latest")
    print("   · For C/C++: clangd, from your system's package manager")


def _desktop_system_cmd():
    if shutil.which("apt-get"):
        return "sudo apt-get install -y python3-pyatspi python3-gi ydotool"
    if shutil.which("dnf") or shutil.which("yum"):
        return "sudo dnf install -y python3-pyatspi python3-gobject ydotool"
    if shutil.which("pacman"):
        return "sudo pacman -S --needed python-atspi python-gobject ydotool"
    return "(your package manager) the Python accessibility binding and ydotool"


def _sudo_ok(sudo):
    if not sudo:
        return True
    print("   · the system packages need root: sudo will ask for your password")
    return run(sudo + ["-v"], check=False).returncode == 0


_DIFFR_VERSION = "0.1.16"
_DIFFR_PINS = {
    ("Linux", "x86_64"): ("x86_64-unknown-linux-gnu",
                          "2d6a99e2fe2eea587d68d4929710b8a136beda71c0a0f05ae29b174aef4d9389"),
    ("Darwin", "arm64"): ("aarch64-apple-darwin",
                          "151188635ca8b72baddd78ac27bcd2e2e3d46fda79746a4748a468f7b2e9da04"),
    ("Windows", "AMD64"): ("x86_64-pc-windows-msvc",
                           "460dcde613fda17c9d5a10f523409946f771e9243edd546595fa13f8524c0f6d"),
}


def install_diffr(prefix):
    import hashlib
    import tarfile
    import tempfile
    import urllib.request
    step(f"Installing diffr {_DIFFR_VERSION} (structural diff for the whiteboard)")
    key = (platform.system(), platform.machine())
    pin = _DIFFR_PINS.get(key)
    if pin is None:
        err(f"diffr publishes no binary for {key[0]} {key[1]}.")
        print("   · The whiteboard keeps working with a textual diff.")
        print("   · A diffr on PATH, or HOPLITA_DIFFR=/path/to/diffr, is picked up.")
        return
    target, sha = pin
    url = (f"https://github.com/devdotfast/diffr/releases/download/"
           f"{_DIFFR_VERSION}/diffr-{_DIFFR_VERSION}-{target}.tar.gz")
    tmp = tempfile.mkdtemp(prefix="hoplita-diffr-")
    tgz = os.path.join(tmp, "diffr.tgz")
    try:
        urllib.request.urlretrieve(url, tgz)
        got = hashlib.sha256(open(tgz, "rb").read()).hexdigest()
        if got != sha:
            err(f"diffr checksum mismatch ({got[:12]}… ≠ {sha[:12]}…): not installed.")
            return
        exe = "diffr.exe" if platform.system() == "Windows" else "diffr"
        with tarfile.open(tgz) as tf:
            member = next((m for m in tf.getmembers()
                           if os.path.basename(m.name) == exe and m.isfile()), None)
            if member is None:
                err("The diffr archive has no diffr binary: not installed.")
                return
            member.name = exe
            try:
                tf.extract(member, tmp, filter="data")
            except TypeError:
                tf.extract(member, tmp)
        bindir = os.path.join(prefix, "bin")
        os.makedirs(bindir, exist_ok=True)
        shutil.copy2(os.path.join(tmp, exe), os.path.join(bindir, exe))
        os.chmod(os.path.join(bindir, exe), 0o755)
        ok(f"diffr → {os.path.join(bindir, exe)}")
    except Exception as e:
        err(f"diffr could not be installed: {e}")
        print("   · The whiteboard keeps working with a textual diff.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def install_desktop(prefix, system_libs=False):
    step("Desktop hands (hands_*)")
    py = venv_python(os.path.join(prefix, "venv"))
    if not os.path.exists(py):
        err(f"{prefix} has no venv: complete the install first.")
        return
    req = os.path.join(SRC_DIR, "requirements-desktop.txt")
    if os.path.exists(req):
        if pip_install(py, ["-r", req], quiet=True).returncode == 0:
            ok("Pixel layer (X11/Windows/macOS) installed in the venv")
        else:
            warn("The pixel layer was not installed; on Wayland it is not needed.")
    if IS_LINUX and not system_libs:
        cmd = _desktop_system_cmd()
        print("   · System packages NOT touched (add --system-libs to let the installer "
              "do it; that asks for sudo). By hand:")
        print(f"       {cmd}")
        print("       and on Wayland start the daemon: sudo systemctl enable --now ydotool.service")
    elif IS_LINUX:
        sudo = [] if os.geteuid() == 0 else (["sudo"] if shutil.which("sudo") else None)
        if sudo is None:
            warn("Not root and no sudo: cannot install the system packages "
                 "(Python accessibility binding, input helper).")
        elif not _sudo_ok(sudo):
            warn("sudo did not get a password: system packages NOT installed. By hand:")
            print(f"       {_desktop_system_cmd()}")
        else:
            if shutil.which("apt-get"):
                pkgs = ["python3-pyatspi", "python3-gi", "ydotool"]
                r = run(sudo + ["apt-get", "install", "-y", "-qq"] + pkgs, check=False)
            elif shutil.which("dnf") or shutil.which("yum"):
                pkg = "dnf" if shutil.which("dnf") else "yum"
                r = run(sudo + [pkg, "install", "-y", "-q",
                                "python3-pyatspi", "python3-gobject"], check=False)
                r2 = run(sudo + [pkg, "install", "-y", "-q", "ydotool"], check=False)
                if r2.returncode != 0:
                    warn("This distribution does not package the input helper "
                         "(ydotool): on Wayland there will be no clicks by "
                         "coordinates; pressing and typing through the element "
                         "layer still work. An X11 session has the whole pixel layer.")
            elif shutil.which("pacman"):
                r = run(sudo + ["pacman", "-S", "--noconfirm", "--needed",
                                "python-atspi", "python-gobject", "ydotool"], check=False)
            else:
                r = None
                warn("Unrecognised package manager: install the Python accessibility "
                     "binding by hand and, on Wayland, ydotool.")
            if r is not None and r.returncode == 0:
                ok("The hands' system packages are installed")
            if shutil.which("ydotool") and shutil.which("systemctl"):
                units = run(["systemctl", "list-unit-files", "ydotool.service",
                             "--no-legend"], capture_output=True, text=True)
                if "ydotool.service" in (units.stdout or ""):
                    if run(sudo + ["systemctl", "enable", "--now", "ydotool.service"],
                           check=False).returncode == 0:
                        ok("Input helper daemon enabled")
                    else:
                        warn("could not enable ydotool.service; start it yourself and "
                             "check the socket with hands_status.")
                else:
                    warn("ydotool is there but its package ships no systemd unit: "
                         "start ydotoold yourself (it needs access to /dev/uinput).")
    probe = ("import json, sys; sys.path.insert(0, %r); import hoplita_desktop as d; "
             "s = d.Hands().status(); print(json.dumps({k: s[k] for k in "
             "('session', 'elements', 'pixels')}))" % prefix)
    r = run([py, "-c", probe], capture_output=True, text=True,
            env={**os.environ, "HOPLITA_HANDS_QUIET": "1"})
    if r.returncode == 0 and r.stdout.strip():
        try:
            st = json.loads(r.stdout.strip().splitlines()[-1])
            el, px = st["elements"], st["pixels"]
            print(f"   · session: {st['session']}")
            print("   · elements (windows, pressing, typing): "
                  + ("OK" if el.get("available") else "NO — " + str(el.get("why", ""))))
            print("   · screen capture: "
                  + ("OK" if px.get("capture") else "NO — " + str(px.get("capture_why", ""))))
            print("   · raw input (clicks by coordinates, keys): "
                  + ("OK" if px.get("input") else "NO — " + str(px.get("input_why", ""))))
        except Exception:
            print(r.stdout[-400:])
    else:
        warn("could not ask the module what the machine has: "
             + (r.stderr or "")[-300:])


def copy_payload(prefix):
    step(f"Copying files to {prefix}")
    shipped = []
    for f in os.listdir(os.path.join(SRC_DIR, "python")):
        if f.endswith(".py"):
            shutil.copy(os.path.join(SRC_DIR, "python", f), prefix)
            shipped.append(f)
    for d in ("wb_canvas", "wb_instructions", "council_view", "calendar_view"):
        src = os.path.join(SRC_DIR, "python", d)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(prefix, d), dirs_exist_ok=True)
            for root, _dirs, files in os.walk(src):
                for f in files:
                    shipped.append(os.path.relpath(os.path.join(root, f),
                                                   os.path.join(SRC_DIR, "python")))
    os.makedirs(os.path.join(prefix, "lang"), exist_ok=True)
    for f in os.listdir(os.path.join(SRC_DIR, "lang")):
        if f.endswith(".json"):
            shutil.copy(os.path.join(SRC_DIR, "lang", f),
                        os.path.join(prefix, "lang"))
            shipped.append(os.path.join("lang", f))
    for extra in ("icons",):
        s = os.path.join(SRC_DIR, extra)
        if os.path.isdir(s):
            shutil.copytree(s, os.path.join(prefix, extra), dirs_exist_ok=True)
    import glob as _glob
    for icon in [os.path.basename(p) for p in
                 _glob.glob(os.path.join(SRC_DIR, "hoplita*.png")) +
                 _glob.glob(os.path.join(SRC_DIR, "hoplita*.ico"))]:
        s = os.path.join(SRC_DIR, icon)
        if os.path.exists(s):
            shutil.copy(s, prefix)
    cli_src = os.path.join(SRC_DIR, "cli")
    if os.path.isdir(cli_src):
        shutil.copytree(cli_src, os.path.join(prefix, "cli"), dirs_exist_ok=True)
        for f in os.listdir(cli_src):
            if os.path.isfile(os.path.join(cli_src, f)):
                shipped.append(os.path.join("cli", f))
    example = os.path.join(SRC_DIR, "config.ini.example")
    if os.path.exists(example):
        shutil.copy(example, os.path.join(prefix, "config.ini.example"))
    for doc in ("README.md", "requirements.txt"):
        d = os.path.join(SRC_DIR, doc)
        if os.path.exists(d):
            shutil.copy(d, prefix)
    docs_src = os.path.join(SRC_DIR, "docs")
    if os.path.isdir(docs_src):
        shutil.copytree(docs_src, os.path.join(prefix, "docs"), dirs_exist_ok=True)
        for f in os.listdir(docs_src):
            if os.path.isfile(os.path.join(docs_src, f)):
                shipped.append(os.path.join("docs", f))
    _prune_stale(prefix, shipped)
    ok("Files copied")



def install_system_libs():
    if not IS_LINUX:
        return
    step("System libraries (WebEngine canvas, tray)")
    sudo = []
    if os.geteuid() != 0:
        if shutil.which("sudo"):
            sudo = ["sudo"]
        else:
            warn("Not root and no sudo: cannot install system libraries.")
            warn("Install the QtWebEngine/tray libraries by hand — see README.md.")
            warn("(The password dialog does NOT need them: it uses Qt.)")
            return
    if shutil.which("apt-get"):
        pkgs = ["libnss3", "libnspr4", "libxcomposite1",
                "libxdamage1", "libxrandr2", "libxtst6", "libxkbcommon0",
                "libgbm1", "libasound2", "libxshmfence1", "libegl1", "libgl1",
                "libxcb-cursor0", "libxkbfile1", "libxcb-icccm4", "libxcb-keysyms1",
                "libxcb-shape0", "libxcb-xkb1", "libxkbcommon-x11-0", "fonts-liberation", "libayatana-appindicator3-1"]
        run(sudo + ["apt-get", "update", "-qq"], check=False)
        if subprocess.run(["apt-cache", "show", "libasound2t64"], capture_output=True).returncode == 0:
            pkgs[pkgs.index("libasound2")] = "libasound2t64"
        r = run(sudo + ["apt-get", "install", "-y", "-qq"] + pkgs, check=False)
    elif shutil.which("dnf") or shutil.which("yum"):
        pkg = "dnf" if shutil.which("dnf") else "yum"
        pkgs = ["nss", "nspr", "libXcomposite", "libXdamage",
                "libXrandr", "libXtst", "libxkbcommon", "mesa-libgbm", "alsa-lib",
                "libxshmfence", "libglvnd-egl", "libglvnd-opengl",
                "xcb-util-cursor", "libxkbfile", "xcb-util-wm", "xcb-util-keysyms",
                "libxkbcommon-x11", "libappindicator-gtk3"]
        r = run(sudo + [pkg, "install", "-y", "-q"] + pkgs, check=False)
    elif shutil.which("pacman"):
        pkgs = ["nss", "nspr", "libxcomposite", "libxdamage", "libxrandr",
                "libxtst", "libxkbcommon", "mesa", "alsa-lib", "libxshmfence",
                "libxkbfile", "xcb-util-wm", "xcb-util-keysyms", "libxkbcommon-x11"]
        r = run(sudo + ["pacman", "-S", "--noconfirm", "--needed"] + pkgs, check=False)
    else:
        warn("Unrecognised package manager. Install the libraries by hand;")
        warn("QtWebEngine/tray (see README.md).")
        return
    if r.returncode == 0:
        ok("System libraries installed (WebEngine + tray)")
    else:
        warn("Some system libraries were not installed; the canvas or the tray")
        warn("may fail. Check your package manager.")


UV_VERSION = "0.13.6"
PY_WANT = "3.12"


def _is_framework_python():
    import sysconfig
    return bool(sysconfig.get_config_var("PYTHONFRAMEWORK"))


def _mac_standalone_python(prefix):
    if not _is_framework_python() and sys.version_info >= (3, 11) \
            and os.path.realpath(sys.executable).startswith(os.path.realpath(prefix) + os.sep):
        return None
    pydir = os.path.join(prefix, "python")
    import glob as _glob
    have = sorted(_glob.glob(os.path.join(pydir, f"cpython-{PY_WANT}*", "bin", f"python{PY_WANT}")))
    if have:
        return have[-1]
    step("Fetching HOPLITA's own Python for macOS")
    print(f"   this Python ({sys.version_info.major}.{sys.version_info.minor}"
          + (", a framework build" if _is_framework_python() else "")
          + f") only starts the installer; HOPLITA runs on a standalone {PY_WANT}")
    import tarfile
    import tempfile
    import urllib.request
    arch = "aarch64" if platform.machine() in ("arm64", "aarch64") else "x86_64"
    target = f"{arch}-apple-darwin"
    work = tempfile.mkdtemp(prefix="hoplita-uv-")
    try:
        tgz = os.path.join(work, "uv.tar.gz")
        urllib.request.urlretrieve(
            f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/uv-{target}.tar.gz", tgz)
        with tarfile.open(tgz) as t:
            t.extractall(work)
        uv = os.path.join(work, f"uv-{target}", "uv")
        os.makedirs(pydir, exist_ok=True)
        env = {**os.environ, "UV_PYTHON_INSTALL_DIR": pydir,
               "UV_CACHE_DIR": os.path.join(work, "uv-cache")}
        run([uv, "python", "install", "--quiet", "--no-bin", PY_WANT], env=env, check=True)
        r = run([uv, "python", "find", "--managed-python", PY_WANT], env=env,
                capture_output=True, text=True, check=True)
        py = r.stdout.strip()
        if not py or not os.access(py, os.X_OK):
            err(f"uv could not provide Python {PY_WANT}")
            sys.exit(1)
        ok(f"Python {PY_WANT} in {pydir}")
        return py
    except Exception as e:
        err(f"could not fetch the standalone Python: {e}")
        err("  Install Python 3.12 (python.org) and run: python3.12 install.py")
        sys.exit(1)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run(cmd, **kw):
    return subprocess.run(cmd, **kw)


def align_qt(py):
    probe = ("import importlib.metadata as m\n"
             "for n in ('PyQt6', 'PyQt6-WebEngine'):\n"
             "    try: print(n, m.version(n))\n"
             "    except Exception: print(n, '')\n")
    r = run([py, "-c", probe], capture_output=True, text=True)
    have = dict(ln.split(" ", 1) for ln in (r.stdout or "").splitlines() if " " in ln)
    qt, we = have.get("PyQt6", "").strip(), have.get("PyQt6-WebEngine", "").strip()
    if not qt or not we:
        return
    probe_import = "from PyQt6 import QtWebEngineWidgets\n"
    if run([py, "-c", probe_import], capture_output=True, text=True).returncode == 0:
        return
    minor = ".".join(we.split(".")[:2])
    qt_minor = ".".join(qt.split(".")[:2])
    warn(f"PyQt6 {qt} does not load with PyQt6-WebEngine {we}: aligning the pair")
    if pip_install(py, [f"PyQt6=={minor}.*", f"PyQt6-Qt6=={minor}.*"], quiet=True).returncode == 0:
        return
    if pip_install(py, [f"PyQt6-WebEngine=={qt_minor}.*",
                        f"PyQt6-WebEngine-Qt6=={qt_minor}.*"], quiet=True).returncode == 0:
        return
    warn("Could not align PyQt6 with PyQt6-WebEngine; the canvas and dashboards may not load")


def pip_install(py, pip_args, quiet=False):
    base = [py, "-m", "pip", "install", "--retries", "1", "--timeout", "20"]
    if quiet:
        base.append("-q")
    r = subprocess.run(base + list(pip_args), capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    dead = sorted(set(re.findall(r"host='([^']+)'", out)))
    if "NameResolutionError" in out or "Failed to resolve" in out or \
            "Could not find a version" in out and dead:
        warn("An index configured for pip does not resolve"
             + (f": {', '.join(dead)}" if dead else "")
             + " — see `pip config list` in that venv. Retrying with pip "
               "isolated from that configuration.")
        r = subprocess.run(base + ["--isolated"] + list(pip_args),
                           capture_output=True, text=True)
        out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        print(out[-1500:])
    elif not quiet:
        tail = [l for l in out.splitlines() if l.startswith(("Successfully", "Requirement already"))]
        for l in tail[-3:]:
            print("   " + l)
    return r



def _parts_report(prefix, py, args):
    def has(mod):
        return run([py, "-c", f"import {mod}"], capture_output=True).returncode == 0
    binname = "hoplita-cli.exe" if IS_WINDOWS else "hoplita-cli"
    parts = [
        ("browser", "web browsing (Playwright and its Chromium)",
         args.browser, lambda: has("playwright")),
        ("desktop", "desktop hands (pixel layer)",
         args.desktop, lambda: has("mss")),
        ("lsp", "Python language server for the code editor",
         args.lsp or args.all, lambda: has("pylsp")),
        ("diffr", "structural diff for the whiteboard",
         args.diffr, lambda: os.path.exists(os.path.join(
             prefix, "bin", "diffr.exe" if IS_WINDOWS else "diffr"))),
        ("cli", "command-line client (needs Go)",
         args.cli, lambda: os.path.exists(os.path.join(prefix, "cli", "dist", binname))),
    ]
    print("\nParts:")
    for flag, what, asked, present in parts:
        if present():
            print(f"  {_c('32', '✓')} {what}")
        elif asked:
            print(f"  {_c('31', '✗')} {what}: NOT installed (see the lines above); "
                  f"run the same line again with --{flag}")
        else:
            more = "" if flag == "cli" else ", or --full for every part but the CLI"
            print(f"  · {what}: not included; add --{flag} to the same line{more}")


def main():
    ap = argparse.ArgumentParser(add_help=True, description="HOPLITA 1.0 installer")
    ap.add_argument("--prefix", default=None, help="install path")
    ap.add_argument("--update", action="store_true",
                    help="update only: copy code/languages/assets/CLI into an "
                         "existing install. Does not touch venv, deps, config or shortcuts.")
    ap.add_argument("--browser", action="store_true", help="+ web browsing stack")
    ap.add_argument("--cli", action="store_true", help="+ build the Go CLI")
    ap.add_argument("--lsp", action="store_true",
                    help="+ Python language server (navigation and diagnostics "
                         "in the code editor)")
    ap.add_argument("--desktop", action="store_true",
                    help="+ desktop hands: pixel layer in the venv and a report of "
                         "what the machine has; with --system-libs also the Python "
                         "accessibility binding and the system's input helper "
                         "(that part asks for sudo)")
    ap.add_argument("--diffr", action="store_true",
                    help="+ diffr, the structural diff behind the whiteboard's "
                         "Diff view (a pinned release binary)")
    ap.add_argument("--full", action="store_true",
                    help="core + browser + desktop + diffr + lsp: every part that "
                         "needs neither Go nor root (the CLI stays apart)")
    ap.add_argument("--all", action="store_true", help="core + browser + cli + desktop + diffr + lsp")
    ap.add_argument("--system-libs", action="store_true",
                    help="install system libraries (WebEngine/tray, the hands' "
                         "packages) — asks for sudo")
    ap.add_argument("--child", action="store_true",
                    help="a child: no window, no shortcuts, running as a service "
                         "of this user; its first boot enrolls with the token in "
                         "HOPLITA_SPAWN_SERVER/_HANDLE/_TOKEN (the line its owner "
                         "is handed). Installs into ~/hoplita-<handle> by default")
    ap.add_argument("--no-shortcuts", action="store_true",
                    help="do not create desktop shortcuts")
    ap.add_argument("--uninstall", action="store_true",
                    help="remove the program, its venv and shortcuts; your data "
                         "(config, vault, memory, notes) stays")
    ap.add_argument("--purge", action="store_true",
                    help="with --uninstall: remove your data too (asks you to type DELETE)")
    ap.add_argument("--yes", action="store_true",
                    help="with --uninstall: do not ask (never skips the --purge question)")
    args = ap.parse_args()
    if args.all:
        args.browser = args.cli = args.system_libs = args.desktop = args.diffr = True
    if args.full:
        args.browser = args.desktop = args.diffr = args.lsp = True
    spawn = take_spawn_env()
    if args.child and not args.prefix:
        if not spawn.get("HOPLITA_SPAWN_HANDLE"):
            err("--child needs --prefix, or the handle in HOPLITA_SPAWN_HANDLE.")
            sys.exit(2)
        args.prefix = child_prefix(spawn["HOPLITA_SPAWN_HANDLE"])

    prefix = resolve_prefix(args.prefix)
    if IS_MAC and not args.uninstall and not os.environ.get("HOPLITA_STANDALONE_PY"):
        _std = _mac_standalone_python(prefix)
        if _std and os.path.realpath(_std) != os.path.realpath(sys.executable):
            os.environ["HOPLITA_STANDALONE_PY"] = _std
            os.execve(_std, [_std, os.path.abspath(__file__)] + sys.argv[1:], os.environ)
    if child_mark(prefix) and not args.uninstall:
        args.child = True
    if args.child:
        if args.update:
            err("A child updates with the same line it was installed with, "
                "not --update.")
            sys.exit(2)
        args.no_shortcuts = True
        args.system_libs = args.desktop = False
    if os.path.exists(prefix) and os.path.samefile(prefix, SRC_DIR):
        err(f"The install folder {prefix} is this source folder "
            f"({SRC_DIR}) — on macOS, folder names ignore letter case.")
        err("  Install somewhere else:  python3 install.py --prefix ~/hoplita-app")
        err("  or clone into another name:  git clone "
            "https://github.com/HoplitaAI/Hoplita.git hoplita-src")
        sys.exit(2)
    if not args.prefix and prefix == LEGACY_PREFIX:
        print(f"Existing install found in {LEGACY_PREFIX}: using it "
              f"(--prefix picks another folder).")

    if args.uninstall:
        print(_c("1", "HOPLITA 1.0 — Uninstaller"))
        sys.exit(0 if uninstall(prefix, purge=args.purge, yes=args.yes) else 1)
    if args.purge:
        err("--purge only goes with --uninstall.")
        sys.exit(2)

    print(_c("1", "HOPLITA 1.0 — " + ("Updater" if args.update else "Installer")))
    print(f"OS:      {platform.system()}")
    print(f"Source:  {SRC_DIR}")
    print(f"Target:  {prefix}")

    if args.update:
        if not os.path.isdir(prefix):
            err(f"{prefix} does not exist: for a fresh install, run without --update.")
            sys.exit(1)
        copy_payload(prefix)
        binname = "hoplita-cli.exe" if IS_WINDOWS else "hoplita-cli"
        has_cli = os.path.exists(os.path.join(prefix, "cli", "dist", binname))
        if args.cli or has_cli:
            compile_cli(prefix)
        else:
            step("Go CLI: nothing to rebuild")
            print(f"   · no binary at {os.path.join(prefix, 'cli', 'dist', binname)}")
            print("   · add --cli to build it")
        if args.lsp or args.all:
            install_lsp(prefix)
        if args.desktop:
            install_desktop(prefix, system_libs=args.system_libs)
        if args.diffr:
            install_diffr(prefix)
        _report_new_settings(prefix)
        if (IS_MAC or IS_LINUX) and not args.no_shortcuts:
            make_shortcuts(prefix, os.path.join(prefix, "venv"))
        step("Clearing bytecode caches")
        n = 0
        for root, dirs, _ in os.walk(prefix):
            for d in list(dirs):
                if d == "__pycache__":
                    shutil.rmtree(os.path.join(root, d), ignore_errors=True)
                    dirs.remove(d)
                    n += 1
        ok(f"{n} __pycache__ removed")
        print()
        ok(_c("32;1", f"Updated {prefix}. Restart the GUI and the MCP."))
        return

    step("Checking Python")
    v = sys.version_info
    print(f"Python {v.major}.{v.minor}")
    if (v.major, v.minor) < (3, 11):
        warn(f"Python {v.major}.{v.minor}: 3.11+ is recommended "
             "(browser-use requires it). The core may work anyway.")
    ok("Python OK")

    step(f"Preparing {prefix}")
    try:
        os.makedirs(prefix, exist_ok=True)
        if not os.access(prefix, os.W_OK):
            raise PermissionError
    except PermissionError:
        err(f"Cannot write to {prefix}.")
        err(f"  Pick a folder you own:  {again('--prefix ~/hoplita')}")
        sys.exit(1)
    ok(f"{prefix} is writable")

    if args.system_libs:
        install_system_libs()
    elif IS_LINUX:
        print(_c("34", "\n(System libraries NOT touched — no sudo asked. If the canvas "
                 "or the tray fail because of missing\n libraries, re-run with "
                 "--system-libs, or install them by hand; see README.md.)"))

    venv_dir = os.path.join(prefix, "venv")
    if IS_MAC and os.path.isdir(venv_dir):
        _home = ""
        try:
            for _l in open(os.path.join(venv_dir, "pyvenv.cfg"), encoding="utf-8"):
                if _l.startswith("home ="):
                    _home = _l.split("=", 1)[1].strip()
        except Exception:
            pass
        _own = os.path.realpath(os.path.join(prefix, "python")) + os.sep
        if _home and not os.path.realpath(_home).startswith(_own):
            warn("the venv stands on another Python; rebuilding it on HOPLITA's own")
            shutil.rmtree(venv_dir, ignore_errors=True)
    if not os.path.isdir(venv_dir):
        base = os.path.realpath(sys.executable) if IS_MAC else sys.executable
        run([base, "-m", "venv", venv_dir], check=True)
        ok("venv created")
    else:
        ok("venv already exists (reused)")
    py = venv_python(venv_dir)
    pip_install(py, ["--upgrade", "pip"], quiet=True)

    step("Installing core dependencies")
    req = os.path.join(SRC_DIR, "requirements.txt")
    if args.child and os.path.exists(req):
        req = child_requirements(prefix)
    if not os.path.exists(req):
        err(f"requirements.txt not found in {SRC_DIR}")
        sys.exit(1)
    r = pip_install(py, ["-r", req])
    if r.returncode != 0:
        err("Core dependency installation failed."); sys.exit(1)
    align_qt(py)
    ok("Core dependencies installed")

    copy_payload(prefix)

    step("Configuration")
    cfg = os.path.join(prefix, "config.ini")
    example = os.path.join(SRC_DIR, "config.ini.example")
    if os.path.exists(cfg):
        ok("config.ini already exists (left alone)")
    elif os.path.exists(example):
        shutil.copy(example, cfg)
        if args.child:
            ok("Created config.ini; the starter kit fills it in when the child enrolls")
        else:
            warn("Created config.ini from the example. Edit it ([API] and [MCP]).")
    if os.path.exists(example):
        shutil.copy(example, os.path.join(prefix, "config.ini.example"))

    if args.browser:
        step("Installing the browsing stack (heavy)")
        rb = os.path.join(SRC_DIR, "requirements-browser.txt")
        if pip_install(py, ["-r", rb]).returncode == 0:
            ok("Browsing stack installed")
            if run([py, "-m", "playwright", "install", "chromium"]).returncode == 0:
                ok("Playwright's Chromium installed")
            else:
                warn("Run by hand: <venv>/python -m playwright install chromium")
        else:
            warn("Browsing stack failed. The core works anyway.")

    if args.cli:
        compile_cli(prefix)

    if args.desktop:
        install_desktop(prefix, system_libs=args.system_libs)

    if args.diffr:
        install_diffr(prefix)

    if args.lsp or args.all:
        install_lsp(prefix)

    shortcuts = []
    if not args.no_shortcuts:
        step("Creating desktop shortcuts")
        shortcuts = make_shortcuts(prefix, venv_dir)
        for s in shortcuts:
            ok(f"Shortcut: {s}")
        if not shortcuts:
            warn("No shortcuts created (check desktop permissions).")

    step("Checking the install")
    check = (
        "import importlib,sys\n"
        "gui='child' not in sys.argv\n"
        "core=['fastapi','uvicorn','pydantic','requests',"
        "'cryptography','nacl','markdown','pygments','fpdf','PyPDF2',"
        "'PIL','yaml','sqlcipher3','websocket']+(['PyQt6'] if gui else [])\n"
        "miss=[]\n"
        "for m in core:\n"
        "  try: importlib.import_module(m)\n"
        "  except Exception as e: miss.append((m,str(e).splitlines()[0]))\n"
        "try:\n"
        "  gui and importlib.import_module('PyQt6.QtWebEngineWidgets')\n"
        "except ModuleNotFoundError:\n"
        "  miss.append(('PyQt6-WebEngine','pip install PyQt6-WebEngine'))\n"
        "except ImportError as e:\n"
        "  miss.append(('QtWebEngine system libs',str(e).splitlines()[0]))\n"
        "print('MISSING' if miss else 'OK')\n"
        "[print(f'  - {m}: {e}') for m,e in miss]\n")
    r = run([py, "-c", check] + (["child"] if args.child else []),
            capture_output=True, text=True)
    if "OK" in r.stdout and "MISSING" not in r.stdout:
        ok("Core check OK")
    else:
        warn("Some modules are missing/broken:")
        print(r.stdout)
        if IS_LINUX or IS_MAC:
            warn("If it is QtWebEngine (system libraries), install the ones in the README.")

    if args.child:
        name, port, how, logs = install_child(prefix, py, spawn)
        print("\n" + _c("32;1", f"Child {name} installed in {prefix}"))
        boots = "" if how.startswith("process ") else " starts again at every boot,"
        print(f"It runs as {how} on 127.0.0.1:{port},{boots} and")
        print("appears among your children once it enrolls.")
        print(f"Its log:   {logs}")
        print(f"Update:    {again(f'--child --prefix {prefix}')}")
        print(f"Uninstall: {again(f'--uninstall --prefix {prefix}')}")
        return

    print("\n" + _c("32;1", "═" * 44))
    print(_c("32;1", "  Install complete"))
    print(_c("32;1", "═" * 44))
    if shortcuts and IS_MAC:
        print("\nOpen HOPLITA: double-click HOPLITA on the desktop, or ⌘ Space → HOPLITA.")
        print("It asks for the vault password in a window (the first time you choose it),")
        print("then opens. No terminal is needed.")
    elif shortcuts:
        print("\nOpen HOPLITA with the HOPLITA shortcut on the desktop: it asks for the")
        print("vault password in a window (the first time you choose it), then opens.")
    else:
        homehint = ""
        if args.prefix and prefix != default_prefix():
            homehint = f"HOPLITA_HOME={prefix} "
        print("\nStart by hand (MCP first, then GUI):")
        act = (os.path.join(venv_dir, "Scripts", "activate")
               if IS_WINDOWS else f"source {os.path.join(venv_dir,'bin','activate')}")
        print(f"  cd {prefix}")
        print(f"  {act}")
        print(f"  {homehint}python hoplita-mcp.py")
        print(f"  {homehint}python hoplita-gui.py   (in another terminal)")
    _parts_report(prefix, py, args)
    guide = os.path.join(prefix, "docs", "GUIDE.md")
    print(f"\nDocs: {guide if os.path.exists(guide) else os.path.join(prefix, 'README.md')}")
    if os.environ.get("HOPLITA_RERUN"):
        print(f"Update: run the same line again. Uninstall: {again('--uninstall')}")


if __name__ == "__main__":
    main()
