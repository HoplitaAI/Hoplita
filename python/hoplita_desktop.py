import base64
import glob
import io
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time

_SYSTEM = platform.system()
IS_LINUX = _SYSTEM == "Linux"
IS_WINDOWS = _SYSTEM == "Windows"
IS_MAC = _SYSTEM == "Darwin"

OWN_MARKERS = ("hoplita-gui", "hoplita-mcp", "hoplita_launch", "hoplita_hud")
_EXTRA_OWN_PIDS = set()

_KEYSYMS = {
    "return": 0xff0d, "enter": 0xff0d, "tab": 0xff09, "escape": 0xff1b,
    "esc": 0xff1b, "backspace": 0xff08, "delete": 0xffff, "del": 0xffff,
    "insert": 0xff63, "home": 0xff50, "end": 0xff57, "pageup": 0xff55,
    "pagedown": 0xff56, "up": 0xff52, "down": 0xff54, "left": 0xff51,
    "right": 0xff53, "space": 0x0020,
    "ctrl": 0xffe3, "control": 0xffe3, "shift": 0xffe1, "alt": 0xffe9,
    "super": 0xffeb, "meta": 0xffeb, "win": 0xffeb, "cmd": 0xffeb,
}
for _i in range(1, 13):
    _KEYSYMS[f"f{_i}"] = 0xffbe + _i - 1
_MODIFIERS = ("ctrl", "control", "shift", "alt", "super", "meta", "win", "cmd")



def discover_session():
    if not IS_LINUX:
        return
    try:
        uid = os.getuid()
    except Exception:
        return
    rt = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
    if os.path.isdir(rt):
        os.environ.setdefault("XDG_RUNTIME_DIR", rt)
        if not os.environ.get("WAYLAND_DISPLAY"):
            for cand in sorted(os.listdir(rt)):
                if cand.startswith("wayland-") and not cand.endswith(".lock"):
                    os.environ["WAYLAND_DISPLAY"] = cand
                    break
        if not os.environ.get("DBUS_SESSION_BUS_ADDRESS") \
                and os.path.exists(os.path.join(rt, "bus")):
            os.environ["DBUS_SESSION_BUS_ADDRESS"] = \
                "unix:path=" + os.path.join(rt, "bus")
        if not os.environ.get("XAUTHORITY"):
            xa = sorted(glob.glob(os.path.join(rt, "xauth_*")))
            if xa:
                os.environ["XAUTHORITY"] = xa[0]
    if not os.environ.get("DISPLAY"):
        for sock in sorted(glob.glob("/tmp/.X11-unix/X*")):
            if _owned_by_me(sock):
                os.environ["DISPLAY"] = ":" + sock.rsplit("X", 1)[1]
                break


def _owned_by_me(path):
    try:
        return os.stat(path).st_uid == os.getuid()
    except Exception:
        return False


def session_type():
    if IS_WINDOWS:
        return "windows"
    if IS_MAC:
        return "macos"
    discover_session()
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "none"



def register_own_pid(pid):
    try:
        _EXTRA_OWN_PIDS.add(int(pid))
    except Exception:
        pass


def _cmdline_is_own(cmdline):
    low = (cmdline or "").lower()
    return any(m in low for m in OWN_MARKERS)


def own_pids():
    pids = {os.getpid()} | set(_EXTRA_OWN_PIDS)
    try:
        pids.add(os.getppid())
    except Exception:
        pass
    if IS_LINUX:
        for p in glob.glob("/proc/[0-9]*/cmdline"):
            try:
                with open(p, "rb") as f:
                    cl = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
                if _cmdline_is_own(cl):
                    pids.add(int(p.split("/")[2]))
            except Exception:
                continue
    else:
        try:
            import psutil
            for pr in psutil.process_iter(["pid", "cmdline"]):
                try:
                    if _cmdline_is_own(" ".join(pr.info.get("cmdline") or [])):
                        pids.add(pr.info["pid"])
                except Exception:
                    continue
        except Exception:
            pass
    return pids



class Session:

    def __init__(self, turn_id, label=""):
        self.turn_id = turn_id
        self.label = label
        self.started = time.time()
        self.actions = 0
        self.paused = threading.Event()
        self.stopped = False
        self.consented = False
        self.announced = False
        self.elements = {}
        self.windows = {}
        self._seq = 0
        self.last_capture = None
        self.view = None
        self.act_lock = threading.Lock()

    def new_id(self, prefix):
        self._seq += 1
        return f"{prefix}{self._seq}"

    def summary(self):
        return {"turn_id": self.turn_id, "label": self.label,
                "since": self.started, "actions": self.actions,
                "paused": self.paused.is_set(), "stopped": self.stopped}


_STATE = {"session": None}
_STATE_LOCK = threading.Lock()


def current():
    return _STATE["session"]


def acquire(turn_id, label=""):
    turn_id = (turn_id or "").strip() or "call"
    with _STATE_LOCK:
        s = _STATE["session"]
        if s is not None and s.turn_id != turn_id:
            return None, {
                "error": "the desktop hands are held by another conversation",
                "held_by_turn": s.turn_id, "held_since": s.started,
                "actions_so_far": s.actions,
                "hint": "one turn drives the desktop at a time on this "
                        "machine; wait for it to finish, or do this without "
                        "the desktop"}
        if s is None:
            s = Session(turn_id, label)
            _STATE["session"] = s
        return s, None


def release(turn_id):
    turn_id = (turn_id or "").strip() or "call"
    with _STATE_LOCK:
        s = _STATE["session"]
        if s is not None and s.turn_id == turn_id:
            _STATE["session"] = None
            return s
    return None


def control(turn_id, action):
    with _STATE_LOCK:
        s = _STATE["session"]
    if s is None:
        return {"ok": False, "error": "no hands session"}
    if turn_id and s.turn_id != turn_id:
        return {"ok": False, "error": "that turn does not hold the hands",
                "held_by_turn": s.turn_id}
    if action == "pause":
        s.paused.set()
    elif action == "resume":
        s.paused.clear()
    elif action == "stop":
        s.stopped = True
        s.paused.clear()
    elif action != "status":
        return {"ok": False, "error": f"unknown action '{action}'"}
    return {"ok": True, **s.summary()}


def wait_if_paused(session, cancel=None, poll=0.2):
    while True:
        if session.stopped:
            return {"error": "the desktop hands were stopped by the user",
                    "hint": "do not retry desktop actions this turn; report "
                            "what was done and what was not"}
        if callable(cancel) and cancel():
            return {"error": "the turn was cancelled"}
        if not session.paused.is_set():
            return None
        time.sleep(poll)



def _import_pyatspi():
    try:
        import pyatspi
        return pyatspi, ""
    except Exception as first:
        v = f"python{sys.version_info.major}.{sys.version_info.minor}"
        added = False
        for base in (f"/usr/lib64/{v}/site-packages", f"/usr/lib/{v}/site-packages",
                     f"/usr/lib/{v}/dist-packages",
                     f"/usr/lib/python3/dist-packages"):
            if os.path.isdir(base) and base not in sys.path:
                sys.path.append(base)
                added = True
        if added:
            try:
                import pyatspi
                return pyatspi, ""
            except Exception as second:
                return None, f"{second}"
        return None, f"{first}"


_WINDOW_ROLES = ("frame", "window", "dialog", "file chooser", "alert",
                 "color chooser", "font chooser", "filler")
_SKIP_ROLES = ("filler", "panel", "section", "viewport", "scroll pane",
               "separator", "unknown", "layered pane", "split pane")
_PRESS_NAMES = ("press", "click", "activate", "toggle", "jump", "open",
                "select", "expand or contract", "do default")


class AtspiBackend:

    name = "at-spi"

    def __init__(self):
        discover_session()
        self.mod, self.why = _import_pyatspi()
        self.desktop = None
        if self.mod:
            try:
                self.desktop = self.mod.Registry.getDesktop(0)
            except Exception as e:
                self.why = f"accessibility bus unreachable: {e}"
                self.mod = None

    @property
    def available(self):
        return self.mod is not None and self.desktop is not None

    def _states(self, obj):
        out = []
        try:
            st = obj.getState()
            for name in ("FOCUSED", "PRESSED", "CHECKED", "SELECTED",
                         "EXPANDED", "ACTIVE", "SENSITIVE", "SHOWING",
                         "VISIBLE", "EDITABLE", "FOCUSABLE", "ICONIFIED"):
                const = getattr(self.mod, "STATE_" + name, None)
                if const is not None and st.contains(const):
                    out.append(name.lower())
        except Exception:
            pass
        return out

    def _extents(self, obj):
        try:
            e = obj.queryComponent().getExtents(self.mod.DESKTOP_COORDS)
            return [int(e.x), int(e.y), int(e.width), int(e.height)]
        except Exception:
            return None

    def _text(self, obj, cap=200):
        try:
            t = obj.queryText()
            s = t.getText(0, -1) or ""
            return s[:cap]
        except Exception:
            return ""

    def _actions(self, obj):
        try:
            a = obj.queryAction()
            return [a.getName(i) for i in range(a.nActions)]
        except Exception:
            return []

    def windows(self, session, exclude):
        out = []
        for app in list(self.desktop):
            try:
                pid = int(app.get_process_id())
            except Exception:
                pid = -1
            if pid in exclude:
                continue
            try:
                app_name = app.name or ""
                children = list(app)
            except Exception:
                continue
            for w in children:
                try:
                    role = w.getRoleName()
                except Exception:
                    continue
                if role not in _WINDOW_ROLES:
                    continue
                title = ""
                try:
                    title = w.name or ""
                except Exception:
                    pass
                states = self._states(w)
                wid = session.new_id("w")
                session.windows[wid] = w
                session.elements[wid] = w
                out.append({"id": wid, "app": app_name, "pid": pid,
                            "title": title, "role": role,
                            "active": "active" in states,
                            "minimized": "iconified" in states,
                            "extents": self._extents(w)})
        return out

    def tree(self, session, root, depth=6, max_items=200, query=""):
        q = (query or "").strip().lower()
        out = []
        budget = [max_items]

        def walk(o, d, path):
            if budget[0] <= 0 or d > depth:
                return
            try:
                kids = list(o)
            except Exception:
                return
            for c in kids:
                if budget[0] <= 0:
                    return
                try:
                    role = c.getRoleName()
                    name = c.name or ""
                except Exception:
                    continue
                text = self._text(c) if role in ("text", "entry", "password text",
                                                 "label", "document text",
                                                 "paragraph", "heading") else ""
                listed = not (role in _SKIP_ROLES and not name)
                if listed and q and q not in name.lower() and q not in role \
                        and q not in text.lower():
                    listed = False
                if listed:
                    eid = session.new_id("e")
                    session.elements[eid] = c
                    entry = {"id": eid, "role": role, "name": name,
                             "depth": d}
                    if text and text != name:
                        entry["text"] = text
                    st = self._states(c)
                    if st:
                        entry["states"] = [s for s in st
                                           if s not in ("showing", "visible",
                                                        "sensitive")]
                    acts = self._actions(c)
                    if acts:
                        entry["actions"] = acts
                    ext = self._extents(c)
                    if ext:
                        entry["extents"] = ext
                    out.append(entry)
                    budget[0] -= 1
                walk(c, d + 1, path + [role])

        walk(root, 1, [])
        return out

    def label(self, obj):
        try:
            role = obj.getRoleName()
        except Exception:
            role = ""
        try:
            name = (obj.name or "").strip()
        except Exception:
            name = ""
        if not name:
            name = (self._text(obj, cap=40) or "").strip()
        if role and name:
            return f'{role} «{name}»'
        return name or role or "element"

    def read(self, obj):
        t = self._text(obj, cap=4000)
        if t:
            return t
        try:
            n = obj.name or ""
            d = obj.description or ""
            return (n + ((" — " + d) if d else "")).strip()
        except Exception:
            return ""

    def subtree_text(self, root, cap=6000):
        parts = []

        def walk(o, d):
            if d > 12 or sum(len(p) for p in parts) > cap:
                return
            try:
                kids = list(o)
            except Exception:
                return
            for c in kids:
                try:
                    n = c.name or ""
                except Exception:
                    n = ""
                t = self._text(c)
                for s in (n, t):
                    if s and s not in parts:
                        parts.append(s)
                walk(c, d + 1)

        walk(root, 0)
        return "\n".join(parts)

    def top_window(self, obj):
        cur = obj
        for _ in range(40):
            try:
                role = cur.getRoleName()
                parent = cur.parent
            except Exception:
                break
            if role in _WINDOW_ROLES and parent is not None:
                try:
                    if parent.getRoleName() == "application":
                        return cur
                except Exception:
                    return cur
            if parent is None:
                break
            cur = parent
        return obj

    def all_text(self, exclude, cap=20000):
        parts = []
        for app in list(self.desktop):
            try:
                if int(app.get_process_id()) in exclude:
                    continue
                parts.append(self.subtree_text(app, cap=cap))
            except Exception:
                continue
            if sum(len(x) for x in parts) > cap:
                break
        return "\n".join(parts)

    def press(self, obj):
        acts = self._actions(obj)
        if not acts:
            raise RuntimeError("the element offers no accessible action")
        idx = 0
        for i, a in enumerate(acts):
            if a.lower() in _PRESS_NAMES:
                idx = i
                break
        obj.queryAction().doAction(idx)
        return acts[idx]

    def focus(self, obj):
        try:
            return bool(obj.queryComponent().grabFocus())
        except Exception:
            return False

    def set_text(self, obj, text):
        et = obj.queryEditableText()
        et.setTextContents(text)
        return True

    def key(self, combo):
        keys = [k.strip().lower() for k in combo.replace(" ", "").split("+") if k.strip()]
        if not keys:
            raise ValueError("empty key combination")
        syms = []
        for k in keys:
            if k in _KEYSYMS:
                syms.append(_KEYSYMS[k])
            elif len(k) == 1 and 0x20 <= ord(k) < 0x7f:
                syms.append(ord(k))
            else:
                raise ValueError(f"unknown key '{k}'")
        R = self.mod.Registry
        mods = [s for k, s in zip(keys, syms) if k in _MODIFIERS]
        main = [s for k, s in zip(keys, syms) if k not in _MODIFIERS]
        for s in mods:
            R.generateKeyboardEvent(s, None, self.mod.KEY_PRESS)
        for s in main:
            R.generateKeyboardEvent(s, None, self.mod.KEY_PRESSRELEASE)
        for s in reversed(mods):
            R.generateKeyboardEvent(s, None, self.mod.KEY_RELEASE)
        return True

    def click_xy(self, x, y, button="left", double=False):
        b = {"left": "b1", "middle": "b2", "right": "b3"}.get(button, "b1")
        self.mod.Registry.generateMouseEvent(int(x), int(y), b + ("d" if double else "c"))
        return True


_UIA_ROLES = {
    "button": "button", "splitbutton": "button", "edit": "text", "text": "label",
    "checkbox": "check box", "radiobutton": "radio button", "menuitem": "menu item",
    "menu": "menu", "menubar": "menu bar", "combobox": "combo box",
    "listitem": "list item", "list": "list", "tabitem": "page tab",
    "tab": "page tab list", "window": "frame", "pane": "panel", "group": "panel",
    "document": "document text", "hyperlink": "link", "image": "image",
    "toolbar": "tool bar", "statusbar": "status bar", "treeitem": "tree item",
    "tree": "tree", "titlebar": "title bar", "scrollbar": "scroll bar",
    "slider": "slider", "spinner": "spin button", "progressbar": "progress bar",
    "table": "table", "dataitem": "table cell", "header": "header",
    "headeritem": "header item", "tooltip": "tool tip", "separator": "separator",
    "appbar": "tool bar", "calendar": "calendar", "custom": "unknown",
}


class UiaBackend:

    name = "uia"

    def __init__(self):
        self.desktop = None
        self.available = False
        try:
            from pywinauto import Desktop
            self.desktop = Desktop(backend="uia")
            self.available = True
            self.why = ""
        except Exception as e:
            self.why = (f"UI Automation binding unavailable: {e}; install it into "
                        f"the venv (pip install pywinauto) or run install.py --desktop")

    def _ctype(self, w):
        try:
            return (w.element_info.control_type or "").lower()
        except Exception:
            return ""

    def _role(self, w):
        ct = self._ctype(w)
        return _UIA_ROLES.get(ct, ct or "unknown")

    def _name(self, w):
        try:
            n = w.window_text() or ""
        except Exception:
            n = ""
        if not n:
            try:
                n = w.element_info.name or ""
            except Exception:
                n = ""
        return n

    def _pid(self, w):
        try:
            return int(w.process_id())
        except Exception:
            return -1

    def _states(self, w):
        out = []
        for probe, name in (("is_enabled", "sensitive"), ("is_visible", "visible"),
                            ("has_keyboard_focus", "focused"), ("is_active", "active"),
                            ("is_minimized", "iconified"), ("is_selected", "selected")):
            try:
                if getattr(w, probe)():
                    out.append(name)
            except Exception:
                pass
        if "visible" in out:
            out.append("showing")
        try:
            if w.get_toggle_state() == 1:
                out.append("checked")
        except Exception:
            pass
        if self._ctype(w) in ("edit", "document"):
            out.append("editable")
        try:
            if w.element_info.element.CurrentIsKeyboardFocusable:
                out.append("focusable")
        except Exception:
            if self._ctype(w) in ("button", "edit", "checkbox", "radiobutton",
                                  "combobox", "listitem", "tabitem", "menuitem",
                                  "hyperlink", "document"):
                out.append("focusable")
        return out

    def _extents(self, w):
        try:
            r = w.rectangle()
            return [int(r.left), int(r.top), int(r.width()), int(r.height())]
        except Exception:
            return None

    def _text(self, w, cap=200):
        try:
            v = w.get_value()
            if v:
                return str(v)[:cap]
        except Exception:
            pass
        if self._ctype(w) in ("edit", "document", "text"):
            try:
                t = " ".join(str(x) for x in (w.texts() or []) if x).strip()
                if t:
                    return t[:cap]
            except Exception:
                pass
        return ""

    def _actions(self, w):
        acts = []
        for attr, label in (("iface_invoke", "Invoke"), ("iface_toggle", "Toggle"),
                            ("iface_selection_item", "Select"),
                            ("iface_expand_collapse", "Expand"), ("iface_value", "SetValue")):
            try:
                getattr(w, attr)
                acts.append(label)
            except Exception:
                pass
        return acts

    def windows(self, session, exclude):
        out = []
        tops = list(self.desktop.windows())
        for w in tops:
            pid = self._pid(w)
            if pid in exclude:
                continue
            states = self._states(w)
            wid = session.new_id("w")
            session.windows[wid] = w
            session.elements[wid] = w
            out.append({"id": wid, "app": self._process_name(pid), "pid": pid,
                        "title": self._name(w), "role": self._role(w),
                        "active": "active" in states,
                        "minimized": "iconified" in states,
                        "extents": self._extents(w)})
        return out

    def _process_name(self, pid):
        try:
            import psutil
            return psutil.Process(pid).name()
        except Exception:
            return ""

    def tree(self, session, root, depth=6, max_items=200, query=""):
        q = (query or "").strip().lower()
        out = []
        budget = [max_items]

        def walk(o, d):
            if budget[0] <= 0 or d > depth:
                return
            try:
                kids = list(o.children())
            except Exception:
                return
            for c in kids:
                if budget[0] <= 0:
                    return
                role = self._role(c)
                name = self._name(c)
                text = self._text(c) if role in ("text", "label", "document text") else ""
                listed = not (role in _SKIP_ROLES and not name)
                if listed and q and q not in name.lower() and q not in role \
                        and q not in text.lower():
                    listed = False
                if listed:
                    eid = session.new_id("e")
                    session.elements[eid] = c
                    entry = {"id": eid, "role": role, "name": name, "depth": d}
                    if text and text != name:
                        entry["text"] = text
                    st = [x for x in self._states(c)
                          if x not in ("showing", "visible", "sensitive")]
                    if st:
                        entry["states"] = st
                    acts = self._actions(c)
                    if acts:
                        entry["actions"] = acts
                    ext = self._extents(c)
                    if ext:
                        entry["extents"] = ext
                    out.append(entry)
                    budget[0] -= 1
                walk(c, d + 1)

        walk(root, 1)
        return out

    def label(self, w):
        role = self._role(w)
        name = (self._name(w) or "").strip() or (self._text(w, cap=40) or "").strip()
        if role and name:
            return f'{role} «{name}»'
        return name or role or "element"

    def read(self, w):
        t = self._text(w, cap=4000)
        return t if t else self._name(w)

    def subtree_text(self, root, cap=6000):
        parts = []

        def walk(o, d):
            if d > 12 or sum(len(p) for p in parts) > cap:
                return
            try:
                kids = list(o.children())
            except Exception:
                return
            for c in kids:
                for x in (self._name(c), self._text(c)):
                    if x and x not in parts:
                        parts.append(x)
                walk(c, d + 1)

        n = self._name(root)
        if n:
            parts.append(n)
        walk(root, 0)
        return "\n".join(parts)

    def all_text(self, exclude, cap=20000):
        parts = []
        tops = list(self.desktop.windows())
        for w in tops:
            if self._pid(w) in exclude:
                continue
            parts.append(self.subtree_text(w, cap=cap))
            if sum(len(x) for x in parts) > cap:
                break
        return "\n".join(parts)

    def top_window(self, w):
        try:
            return w.top_level_parent()
        except Exception:
            return w

    def press(self, w):
        acts = self._actions(w)
        if "Invoke" in acts:
            w.invoke()
            return "Invoke"
        if "Toggle" in acts:
            w.toggle()
            return "Toggle"
        if "Select" in acts:
            w.select()
            return "Select"
        if "Expand" in acts:
            w.expand()
            return "Expand"
        raise RuntimeError("the element offers no automation pattern to press; "
                           "click its coordinates")

    def focus(self, w):
        try:
            w.set_focus()
            return True
        except Exception:
            return False

    def set_text(self, w, text):
        try:
            w.set_edit_text(text)
        except Exception:
            w.iface_value.SetValue(text)
        return True


class MissingElementBackend:

    def __init__(self, name, why):
        self.name = name
        self.why = why
        self.available = False


def _element_backend():
    if IS_LINUX:
        return AtspiBackend()
    if IS_WINDOWS:
        return UiaBackend()
    if IS_MAC:
        return MissingElementBackend(
            "ax", "the macOS accessibility layer is not driven by this version; "
                  + mac_permissions_note())
    return MissingElementBackend("none", "no element layer on this platform")


def mac_permissions_note():
    if not IS_MAC:
        return ""
    import ctypes
    notes = []
    try:
        appsvc = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        appsvc.AXIsProcessTrusted.restype = ctypes.c_bool
        trusted = bool(appsvc.AXIsProcessTrusted())
        notes.append("accessibility permission: " + ("granted" if trusted else
                     "NOT granted to the process that launched this server"))
    except Exception as e:
        notes.append(f"accessibility permission: unknown ({e})")
    try:
        cg = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        ok = bool(cg.CGPreflightScreenCaptureAccess())
        notes.append("screen recording permission: " + ("granted" if ok else
                     "NOT granted to the process that launched this server"))
    except Exception as e:
        notes.append(f"screen recording permission: unknown ({e})")
    return "; ".join(notes)



class PixelBackend:

    name = "none"
    capture_why = "no capture on this session"
    input_why = "no raw input on this session"
    note = ""

    def can_capture(self):
        return not self.capture_why

    def can_input(self):
        return not self.input_why

    def capture(self):
        raise RuntimeError(self.capture_why)

    def size(self):
        return None

    def move(self, x, y):
        raise RuntimeError(self.input_why)

    def click(self, x, y, button="left", double=False):
        raise RuntimeError(self.input_why)

    def type_text(self, text):
        raise RuntimeError(self.input_why)

    def key(self, combo):
        raise RuntimeError(self.input_why)


class PyautoguiBackend(PixelBackend):

    name = "pyautogui+mss"

    def __init__(self):
        self.pg = None
        self.mss = None
        try:
            import pyautogui
            pyautogui.FAILSAFE = True
            pyautogui.PAUSE = 0.05
            self.pg = pyautogui
            self.input_why = ""
        except Exception as e:
            self.input_why = f"raw input unavailable: {e}"
        try:
            import mss
            self.mss = mss
            self.capture_why = ""
        except Exception as e:
            self.capture_why = f"capture unavailable: {e}"

    def size(self):
        try:
            s = self.pg.size()
            return [int(s.width), int(s.height)]
        except Exception:
            return None

    def capture(self):
        from PIL import Image
        with (self.mss.MSS() if hasattr(self.mss, "MSS") else self.mss.mss()) as s:
            im = s.grab(s.monitors[0])
            return Image.frombytes("RGB", im.size, im.rgb)

    def move(self, x, y):
        self.pg.moveTo(int(x), int(y))
        return True

    def click(self, x, y, button="left", double=False):
        if double:
            self.pg.doubleClick(int(x), int(y), button=button)
        else:
            self.pg.click(int(x), int(y), button=button)
        return True

    def type_text(self, text):
        self.pg.write(text, interval=0.02)
        return True

    def key(self, combo):
        keys = [k.strip().lower() for k in combo.split("+") if k.strip()]
        self.pg.hotkey(*keys)
        return True


_EVCODES = {
    "esc": 1, "escape": 1, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8,
    "8": 9, "9": 10, "0": 11, "-": 12, "minus": 12, "=": 13, "equal": 13,
    "backspace": 14, "tab": 15, "q": 16, "w": 17, "e": 18, "r": 19, "t": 20,
    "y": 21, "u": 22, "i": 23, "o": 24, "p": 25, "[": 26, "]": 27, "return": 28,
    "enter": 28, "ctrl": 29, "control": 29, "a": 30, "s": 31, "d": 32, "f": 33,
    "g": 34, "h": 35, "j": 36, "k": 37, "l": 38, ";": 39, "'": 40, "`": 41,
    "shift": 42, "\\": 43, "z": 44, "x": 45, "c": 46, "v": 47, "b": 48, "n": 49,
    "m": 50, ",": 51, ".": 52, "/": 53, "alt": 56, "space": 57, " ": 57,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64, "f7": 65, "f8": 66,
    "f9": 67, "f10": 68, "f11": 87, "f12": 88, "home": 102, "up": 103,
    "pageup": 104, "left": 105, "right": 106, "end": 107, "down": 108,
    "pagedown": 109, "insert": 110, "delete": 111, "del": 111,
    "super": 125, "meta": 125, "win": 125, "cmd": 125,
}


def ydotool_key_args(combo):
    keys = [k.strip().lower() for k in combo.split("+") if k.strip()]
    if not keys:
        raise ValueError("empty key combination")
    codes = []
    for k in keys:
        if k not in _EVCODES:
            raise ValueError(f"unknown key '{k}' for the input helper")
        codes.append((k, _EVCODES[k]))
    mods = [c for k, c in codes if k in _MODIFIERS]
    main = [c for k, c in codes if k not in _MODIFIERS]
    seq = [f"{c}:1" for c in mods]
    for c in main:
        seq += [f"{c}:1", f"{c}:0"]
    seq += [f"{c}:0" for c in reversed(mods)]
    return seq


def ydotool_socket():
    cands = [os.environ.get("YDOTOOL_SOCKET", "")]
    rt = os.environ.get("XDG_RUNTIME_DIR", "")
    if rt:
        cands.append(os.path.join(rt, ".ydotool_socket"))
    cands.append("/tmp/.ydotool_socket")
    for c in cands:
        if c and os.path.exists(c):
            return c
    return ""


class WaylandBackend(PixelBackend):

    name = "wayland"

    def __init__(self):
        discover_session()
        self.shooter = None
        for cand in ("spectacle", "grim", "gnome-screenshot"):
            if shutil.which(cand):
                self.shooter = cand
                break
        self.capture_why = "" if self.shooter else \
            "no screenshot tool found on this Wayland session"
        self.typer = shutil.which("ydotool")
        self.socket = ydotool_socket() if self.typer else ""
        if not self.typer:
            self.input_why = ("raw input is not available on this Wayland "
                              "session: no input-injection helper is installed; "
                              "the element layer (accessible actions and text) "
                              "still works")
        elif not self.socket:
            self.input_why = ("raw input is not available: the input helper is "
                              "installed but its daemon is not running (no "
                              "socket); the element layer still works")
        else:
            self.input_why = ""

    def capture(self):
        from PIL import Image
        fd, path = tempfile.mkstemp(suffix=".png", prefix="hands-")
        os.close(fd)
        try:
            if self.shooter == "spectacle":
                cmd = ["spectacle", "-b", "-n", "-o", path]
            elif self.shooter == "grim":
                cmd = ["grim", path]
            else:
                cmd = ["gnome-screenshot", "-f", path]
            env = dict(os.environ)
            env.pop("QT_QPA_PLATFORM", None)
            env.pop("QT_QPA_PLATFORMTHEME", None)
            res = subprocess.run(cmd, capture_output=True, timeout=20, env=env)
            if res.returncode != 0 or not os.path.exists(path) or os.path.getsize(path) < 100:
                err = (res.stderr or b"").decode("utf-8", "replace").strip()[-300:]
                raise RuntimeError(f"{self.shooter} produced no capture"
                                   + (f": {err}" if err else ""))
            with Image.open(path) as im:
                return im.convert("RGB")
        finally:
            try:
                os.unlink(path)
            except Exception:
                pass

    def size(self):
        return None

    def _run(self, *args):
        env = dict(os.environ)
        if self.socket:
            env["YDOTOOL_SOCKET"] = self.socket
        subprocess.run([self.typer, *args], check=True, capture_output=True,
                       timeout=20, env=env)
        return True

    def move(self, x, y):
        return self._run("mousemove", "--absolute", "-x", str(int(x)), "-y", str(int(y)))

    def click(self, x, y, button="left", double=False):
        self.move(x, y)
        code = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}.get(button, "0xC0")
        self._run("click", code)
        if double:
            self._run("click", code)
        return True

    def type_text(self, text):
        return self._run("type", "--", text)

    def key(self, combo):
        return self._run("key", *ydotool_key_args(combo))


def _pixel_backend(force=""):
    st = session_type()
    if force == "x11" or (not force and st in ("x11", "windows", "macos")):
        b = PyautoguiBackend()
        if st == "wayland":
            b.note = ("forced X path on a Wayland session: input reaches the "
                      "desktop only if the compositor forwards X input (it "
                      "may ask for permission once); check `verified` on the "
                      "first action")
            b.capture_why = ("the X bridge on a Wayland session captures a black "
                             "screen; the compositor's screenshot tool is used instead")
        return b
    if force == "wayland" or (not force and st == "wayland"):
        return WaylandBackend()
    return PixelBackend()



def _downscale(im, max_width):
    if max_width and im.width > max_width:
        h = max(1, int(im.height * max_width / im.width))
        im = im.resize((max_width, h))
    return im


def image_b64(im, max_width=1280):
    im = _downscale(im, max_width)
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii"), im.size


def changed_pct(before, after):
    from PIL import ImageChops
    a = _downscale(before, 320).convert("L")
    b = _downscale(after, 320).convert("L")
    if a.size != b.size:
        b = b.resize(a.size)
    diff = ImageChops.difference(a, b).point(lambda v: 255 if v > 24 else 0)
    hist = diff.histogram()
    changed = hist[255] if len(hist) > 255 else 0
    total = a.width * a.height or 1
    return round(100.0 * changed / total, 2)



class Hands:

    def __init__(self, config=None):
        self.cfg = config
        self._elements = None
        self._pixels = None

    def _get(self, key, default):
        try:
            if self.cfg is not None and self.cfg.has_option("HANDS", key):
                return self.cfg.get("HANDS", key)
        except Exception:
            pass
        return default

    def _bool(self, key, default):
        v = str(self._get(key, default)).strip().lower()
        return v in ("1", "true", "yes", "on")

    def _float(self, key, default):
        try:
            return float(self._get(key, default))
        except Exception:
            return float(default)

    def _int(self, key, default):
        try:
            return int(self._get(key, default))
        except Exception:
            return int(default)

    @property
    def elements(self):
        if self._elements is None:
            self._elements = _element_backend()
        return self._elements

    @property
    def pixels(self):
        if self._pixels is None:
            self._pixels = _pixel_backend(str(self._get("pixel_backend", "auto")).strip().lower()
                                          .replace("auto", ""))
            if self._pixels.note and session_type() == "wayland":
                shooter = WaylandBackend()
                if shooter.can_capture():
                    px = self._pixels
                    px.capture = shooter.capture
                    px.size = shooter.size
                    px.capture_why = ""
        return self._pixels

    def reset_backends(self):
        self._elements = None
        self._pixels = None

    def status(self):
        el, px = self.elements, self.pixels
        s = current()
        out = {
            "platform": _SYSTEM,
            "session": session_type(),
            "elements": {"backend": el.name, "available": bool(el.available),
                         **({"why": el.why} if not el.available else {})},
            "pixels": {"backend": px.name,
                       "capture": px.can_capture(),
                       "input": px.can_input(),
                       **({"capture_why": px.capture_why} if not px.can_capture() else {}),
                       **({"input_why": px.input_why} if not px.can_input() else {}),
                       **({"note": px.note} if px.note else {})},
            "screen": px.size() if px.can_capture() else None,
            "verify_after_actions": self._bool("verify", "true"),
            "own_pids": sorted(own_pids()),
            "held": s.summary() if s else None,
        }
        if IS_MAC:
            out["permissions"] = mac_permissions_note()
        out["how_to_work"] = (
            "hands_windows → hands_tree(window) → act on element ids with "
            "hands_click / hands_type, or by coordinates when there is no "
            "tree; hands_screenshot to look; hands_verify to confirm what "
            "changed. A small helmeted figure in a screen corner is the "
            "user's control: never click it.")
        return out

    def _snap(self):
        try:
            if self.pixels.can_capture():
                return self.pixels.capture()
        except Exception:
            return None
        return None

    def _verify(self, session, before, facts):
        out = dict(facts or {})
        if not self._bool("verify", "true"):
            out["verification"] = "off ([HANDS] verify = false)"
            return None, out
        time.sleep(self._float("action_delay", "0.4"))
        after = self._snap()
        if before is not None and after is not None:
            pct = changed_pct(before, after)
            out["pixels_changed_pct"] = pct
            session.last_capture = after
        elif not self.pixels.can_capture():
            out["pixels"] = "no capture on this session: " + self.pixels.capture_why
        evidence = []
        if "pixels_changed_pct" in out:
            evidence.append(out["pixels_changed_pct"] > 0.05)
        for k in ("text_readback_matches", "state_changed", "window_text_changed",
                  "focused"):
            if k in out and isinstance(out[k], bool):
                evidence.append(out[k])
        if not evidence:
            return None, out
        return any(evidence), out

    def windows(self, session):
        el = self.elements
        if not el.available:
            return {"error": f"element layer unavailable ({el.name}): {el.why}",
                    "hint": "hands_screenshot still works if capture does; "
                            "act by coordinates"}
        excl = own_pids()
        wins = el.windows(session, excl)
        return {"windows": wins, "count": len(wins),
                "excluded_own_pids": sorted(excl),
                "note": "the accessibility bus lists windows from EVERY virtual "
                        "desktop, minimized ones included, with the geometry "
                        "they would have; only `active` is on screen for sure. "
                        "What is actually visible right now, a capture decides.",
                "hint": "hands_tree(window=<id>) lists what a window contains"}

    def _obj(self, session, eid):
        o = session.elements.get(eid)
        if o is None:
            raise KeyError(f"unknown element id '{eid}' — ids come from "
                           f"hands_windows / hands_tree of THIS turn")
        return o

    def tree(self, session, window, depth=6, max_items=200, query=""):
        el = self.elements
        if not el.available:
            return {"error": f"element layer unavailable ({el.name}): {el.why}"}
        root = self._obj(session, window)
        items = el.tree(session, root, depth=int(depth or 6),
                        max_items=int(max_items or 200), query=query or "")
        return {"window": window, "items": items, "count": len(items),
                **({"query": query} if query else {}),
                "hint": "hands_click(element=<id>) presses; hands_type(text, "
                        "element=<id>) fills; hands_verify checks"}

    @staticmethod
    def set_view(session, ox, oy, shown_size, captured_size):
        try:
            sw, sh = shown_size
            cw, ch = captured_size
            session.view = (int(ox), int(oy), cw / max(1, sw), ch / max(1, sh))
        except Exception:
            session.view = None

    @staticmethod
    def to_screen(session, x, y):
        x, y = float(x), float(y)
        v = getattr(session, "view", None)
        if not v:
            return int(round(x)), int(round(y))
        ox, oy, sx, sy = v
        return int(round(ox + x * sx)), int(round(oy + y * sy))

    def click(self, session, element=None, x=None, y=None, button="left",
              double=False):
        before = self._snap()
        facts = {"button": button, "double": bool(double)}
        if element:
            obj = self._obj(session, element)
            el = self.elements
            states_before = el._states(obj)
            win = el.top_window(obj)
            text_before = el.subtree_text(win)
            try:
                facts["action"] = el.press(obj)
                facts["element"] = element
                facts["via"] = "element"
                time.sleep(self._float("action_delay", "0.4"))
                states_after = el._states(obj)
                facts["states_after"] = states_after
                facts["state_changed"] = sorted(states_after) != sorted(states_before)
                facts["window_text_changed"] = el.subtree_text(win) != text_before
            except Exception as e:
                ext = el._extents(obj)
                if ext and self.pixels.can_input() and session_type() != "wayland":
                    cx, cy = ext[0] + ext[2] // 2, ext[1] + ext[3] // 2
                    self.pixels.click(cx, cy, button=button, double=double)
                    facts.update({"via": "pixels", "at": [cx, cy],
                                  "element_action_failed": str(e)})
                else:
                    return {"error": f"could not press '{element}': {e}",
                            "hint": "try another element (a parent or the "
                                    "child), hands_key for a shortcut, or "
                                    "coordinates when raw input is available"}
        elif x is not None and y is not None:
            px = self.pixels
            seen = [int(round(float(x))), int(round(float(y)))]
            x, y = self.to_screen(session, x, y)
            if px.can_input():
                px.click(x, y, button=button, double=double)
                facts.update({"via": "pixels", "at": [x, y], "at_in_image": seen})
            elif self.elements.available and hasattr(self.elements, "click_xy"):
                try:
                    self.elements.click_xy(x, y, button=button, double=double)
                    facts.update({"via": "accessibility registry", "at": [x, y], "at_in_image": seen})
                except Exception as e:
                    return {"error": f"no raw input on this session: {px.input_why}; "
                                     f"the accessibility registry refused too: {e}",
                            "hint": "use element ids from hands_tree instead"}
            else:
                return {"error": f"no raw input on this session: {px.input_why}",
                        "hint": "use element ids from hands_tree instead"}
        else:
            return {"error": "hands_click needs `element` or both `x` and `y`"}
        verified, ev = self._verify(session, before, facts)
        session.actions += 1
        return {"ok": True, "verified": verified, "evidence": ev}

    def type_text(self, session, text, element=None):
        before = self._snap()
        facts = {"chars": len(text or "")}
        el = self.elements
        if element:
            obj = self._obj(session, element)
            try:
                el.set_text(obj, text)
                facts["via"] = "element"
                back = el.read(obj)
                facts["text_readback"] = back[:200]
                facts["text_readback_matches"] = (back == text)
            except Exception as e:
                if self.pixels.can_input():
                    el.focus(obj)
                    self.pixels.type_text(text)
                    facts.update({"via": "pixels", "element_set_failed": str(e)})
                else:
                    return {"error": f"could not set text on '{element}': {e}",
                            "hint": "the element is not editable through its "
                                    "accessible interface and there is no raw "
                                    "input on this session"}
        else:
            if not self.pixels.can_input():
                return {"error": f"no raw input on this session: {self.pixels.input_why}",
                        "hint": "pass `element` (an editable one from hands_tree)"}
            self.pixels.type_text(text)
            facts["via"] = "pixels"
        verified, ev = self._verify(session, before, facts)
        session.actions += 1
        return {"ok": True, "verified": verified, "evidence": ev}

    def key(self, session, keys):
        before = self._snap()
        facts = {"keys": keys}
        px = self.pixels
        sent = False
        if px.can_input():
            try:
                px.key(keys)
                facts["via"] = "pixels"
                sent = True
            except Exception as e:
                facts["pixels_failed"] = str(e)
        if not sent and self.elements.available and hasattr(self.elements, "key"):
            try:
                self.elements.key(keys)
                facts["via"] = "accessibility registry"
                sent = True
            except Exception as e:
                return {"error": f"could not send '{keys}': {e}",
                        "hint": "prefer an element action (hands_click on the "
                                "menu item or button) on this session"}
        if not sent:
            return {"error": f"no way to send keys on this session: {px.input_why}",
                    "hint": "prefer an element action (hands_click on the menu "
                            "item or button)"}
        verified, ev = self._verify(session, before, facts)
        session.actions += 1
        return {"ok": True, "verified": verified, "evidence": ev}

    def screenshot(self, session, window=None, region=None, max_width=None):
        px = self.pixels
        if not px.can_capture():
            return {"error": f"no capture on this session: {px.capture_why}",
                    "hint": "hands_tree still reads windows through the "
                            "element layer" if self.elements.available else ""}
        im = px.capture()
        crop = None
        if region and len(region) == 4:
            _x0, _y0 = self.to_screen(session, region[0], region[1])
            _x1, _y1 = self.to_screen(session, float(region[0]) + float(region[2]),
                                      float(region[1]) + float(region[3]))
            crop = [_x0, _y0, max(1, _x1 - _x0), max(1, _y1 - _y0)]
        elif window:
            obj = self._obj(session, window)
            ext = self.elements._extents(obj) if self.elements.available else None
            if ext and (ext[0] or ext[1]):
                crop = ext
        if crop:
            x, y, w, h = crop
            im = im.crop((max(0, x), max(0, y), min(im.width, x + w), min(im.height, y + h)))
        session.last_capture = im
        session.actions += 1
        b64, size = image_b64(im, int(max_width or self._int("screenshot_max_width", "1280")))
        self.set_view(session, crop[0] if crop else 0, crop[1] if crop else 0,
                      size, (im.width, im.height))
        return {"screenshot_b64": b64, "screenshot_mime": "image/png",
                "size": list(size), "screen": [im.width, im.height],
                **({"cropped_to": crop} if crop else {}),
                "note": "a small helmeted figure in a corner is the user's "
                        "control: never click it"}

    def verify(self, session, expect_text="", element=None, window=None,
               with_screenshot=True):
        out = {}
        el = self.elements
        root = None
        if element or window:
            root = self._obj(session, element or window)
        if expect_text:
            if el.available and root is not None:
                hay = el.subtree_text(root) if window and not element else el.read(root)
                found = expect_text.lower() in (hay or "").lower()
                out["text_found"] = found
                out["searched_in"] = "element" if element else "window"
                if not found:
                    out["read"] = (hay or "")[:600]
            elif el.available:
                hay = el.all_text(own_pids(), cap=20000)
                out["text_found"] = expect_text.lower() in (hay or "").lower()
                out["searched_in"] = "all windows"
            else:
                out["text"] = "cannot search: " + el.why
        if session.last_capture is not None:
            after = self._snap()
            if after is not None:
                out["pixels_changed_pct_since_last"] = changed_pct(session.last_capture, after)
                session.last_capture = after
        verified = out.get("text_found")
        if verified is None and "pixels_changed_pct_since_last" in out:
            verified = out["pixels_changed_pct_since_last"] > 0.05
        out["verified"] = verified
        session.actions += 1
        if with_screenshot and self.pixels.can_capture():
            try:
                im = session.last_capture or self.pixels.capture()
                b64, size = image_b64(im, self._int("screenshot_max_width", "1280"))
                self.set_view(session, 0, 0, size, (im.width, im.height))
                out["screenshot_b64"] = b64
                out["screenshot_mime"] = "image/png"
                out["size"] = list(size)
            except Exception as e:
                out["screenshot"] = f"capture failed: {e}"
        return out



_LOCAL_SUFFIXES = (".localhost", ".local", ".lan", ".home", ".home.arpa",
                   ".internal", ".intranet", ".ts.net")
_SHARED_CGNAT = None


def _host_of(base_url):
    from urllib.parse import urlparse
    u = (base_url or "").strip()
    if not u:
        return ""
    if "://" not in u:
        u = "http://" + u
    return (urlparse(u).hostname or "").strip().lower().strip("[]")


def endpoint_is_local(base_url, extra_hosts=()):
    import ipaddress
    global _SHARED_CGNAT
    if _SHARED_CGNAT is None:
        _SHARED_CGNAT = ipaddress.ip_network("100.64.0.0/10")
    host = _host_of(base_url)
    if not host:
        return True
    for entry in (extra_hosts or ()):
        e = str(entry).strip().lower()
        if not e:
            continue
        if e == host:
            return True
        try:
            net = ipaddress.ip_network(e, strict=False)
            ip = ipaddress.ip_address(host)
            if ip in net:
                return True
        except ValueError:
            pass
    if host == "localhost" or any(host.endswith(suf) for suf in _LOCAL_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return (ip.is_loopback or ip.is_private or ip.is_link_local
                or ip in _SHARED_CGNAT)
    except ValueError:
        pass
    return "." not in host
