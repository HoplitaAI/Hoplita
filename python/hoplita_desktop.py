import base64
import glob
import io
import json
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
    mac_ask_permissions()
    if IS_LINUX:
        return AtspiBackend()
    if IS_WINDOWS:
        return UiaBackend()
    if IS_MAC:
        return MissingElementBackend(
            "ax", "the macOS accessibility layer is not driven by this version; "
                  + mac_permissions_note())
    return MissingElementBackend("none", "no element layer on this platform")


_MAC_ASKED = False


def mac_ask_permissions():
    global _MAC_ASKED
    if not IS_MAC or _MAC_ASKED:
        return []
    _MAC_ASKED = True
    import ctypes
    asked = []
    try:
        appsvc = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        appsvc.AXIsProcessTrusted.restype = ctypes.c_bool
        if not appsvc.AXIsProcessTrusted():
            cf = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            key = ctypes.c_void_p.in_dll(appsvc, "kAXTrustedCheckOptionPrompt")
            true = ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue")
            cf.CFDictionaryCreate.restype = ctypes.c_void_p
            cf.CFDictionaryCreate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                              ctypes.POINTER(ctypes.c_void_p), ctypes.c_long,
                                              ctypes.c_void_p, ctypes.c_void_p]
            keys = (ctypes.c_void_p * 1)(key.value)
            vals = (ctypes.c_void_p * 1)(true.value)
            opts = cf.CFDictionaryCreate(None, keys, vals, 1, None, None)
            appsvc.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
            appsvc.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
            appsvc.AXIsProcessTrustedWithOptions(opts)
            asked.append("accessibility")
    except Exception as e:
        print(f"[HANDS] could not ask for accessibility: {e}", file=sys.stderr)
    try:
        cg = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        if not cg.CGPreflightScreenCaptureAccess():
            import subprocess
            subprocess.run(
                [sys.executable, "-c",
                 "import ctypes; cg = ctypes.cdll.LoadLibrary('/System/Library/"
                 "Frameworks/CoreGraphics.framework/CoreGraphics'); "
                 "cg.CGRequestScreenCaptureAccess()"],
                timeout=30, capture_output=True)
            asked.append("screen recording")
    except Exception as e:
        print(f"[HANDS] could not ask for screen recording: {e}", file=sys.stderr)
    return asked


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
                     "NOT granted to the app that launched this server — "
                     "System Settings → Privacy & Security → Accessibility, "
                     "turn that app on (HOPLITA when started from HOPLITA.app; "
                     "Terminal when started from a terminal)"))
    except Exception as e:
        notes.append(f"accessibility permission: unknown ({e})")
    try:
        cg = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        ok = bool(cg.CGPreflightScreenCaptureAccess())
        notes.append("screen recording permission: " + ("granted" if ok else
                     "NOT granted to the app that launched this server — "
                     "System Settings → Privacy & Security → Screen Recording; "
                     "if it is not in the list, add it with + (the install's "
                     "venv/bin/HOPLITA when started from HOPLITA.app; "
                     "Applications → Utilities → Terminal when started from a "
                     "terminal), turn it on, then quit HOPLITA and start it "
                     "again"))
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
            img = Image.frombytes("RGB", im.size, im.rgb)
        self._learn_scale(img.size[0])
        return img

    _scale = None

    def _learn_scale(self, captured_width):
        sz = self.size()
        if sz and sz[0] and captured_width:
            self._scale = captured_width / float(sz[0])

    def _pt(self, v):
        if self._scale is None:
            try:
                self.capture()
            except Exception:
                self._scale = 1.0
        return int(round(float(v) / (self._scale or 1.0)))

    def move(self, x, y):
        self.pg.moveTo(self._pt(x), self._pt(y))
        return True

    def click(self, x, y, button="left", double=False):
        x, y = self._pt(x), self._pt(y)
        if double:
            self.pg.doubleClick(x, y, button=button)
        else:
            self.pg.click(x, y, button=button)
        return True

    def type_text(self, text):
        self.pg.write(text, interval=0.02)
        return True

    def key(self, combo):
        keys = [k.strip().lower() for k in combo.split("+") if k.strip()]
        self.pg.hotkey(*keys)
        return True


_MAC_KEYS = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8,
    "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "equal": 24,
    "9": 25, "7": 26, "-": 27, "minus": 27, "8": 28, "0": 29, "]": 30, "o": 31,
    "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38, "'": 39, "k": 40,
    ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50,
    "return": 36, "enter": 36, "tab": 48, "space": 49, " ": 49,
    "backspace": 51, "delete": 117, "del": 117, "escape": 53, "esc": 53,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98,
    "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}
_MAC_FLAGS = {"cmd": 1 << 20, "command": 1 << 20, "meta": 1 << 20, "super": 1 << 20,
              "win": 1 << 20, "shift": 1 << 17, "ctrl": 1 << 18, "control": 1 << 18,
              "alt": 1 << 19, "option": 1 << 19, "opt": 1 << 19}


class MacBackend(PyautoguiBackend):

    name = "coregraphics+mss"

    def __init__(self):
        PixelBackend.__init__(self)
        self.pg = None
        self.mss = None
        self.capture_why = ""
        self.input_why = ""
        try:
            import mss
            self.mss = mss
        except Exception as e:
            self.capture_why = f"capture unavailable: {e}"
        try:
            import ctypes
            cg = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")

            class _P(ctypes.Structure):
                _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

            class _R(ctypes.Structure):
                _fields_ = [("origin", _P), ("size", _P)]
            cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p
            cg.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, _P, ctypes.c_uint32]
            cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
            cg.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
            cg.CGEventKeyboardSetUnicodeString.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p]
            cg.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
            cg.CGEventSetIntegerValueField.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int64]
            cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
            cg.CGMainDisplayID.restype = ctypes.c_uint32
            cg.CGDisplayBounds.restype = _R
            cg.CGDisplayBounds.argtypes = [ctypes.c_uint32]
            cf = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            cf.CFRelease.argtypes = [ctypes.c_void_p]
            self._cg, self._cf, self._P = cg, cf, _P
        except Exception as e:
            self.input_why = f"raw input unavailable: {e}"

    def size(self):
        try:
            b = self._cg.CGDisplayBounds(self._cg.CGMainDisplayID())
            return [int(b.size.x), int(b.size.y)]
        except Exception:
            return None

    def _post(self, ev):
        if ev:
            self._cg.CGEventPost(0, ev)
            self._cf.CFRelease(ev)
        time.sleep(0.02)

    def _mouse(self, kind, x, y, button=0, clicks=1):
        ev = self._cg.CGEventCreateMouseEvent(None, kind, self._P(float(x), float(y)), button)
        if ev and clicks > 1:
            self._cg.CGEventSetIntegerValueField(ev, 1, clicks)
        self._post(ev)

    def move(self, x, y):
        self._mouse(5, self._pt(x), self._pt(y))
        return True

    def _window_owner_at(self, x, y):
        import ctypes
        cg, cf = self._cg, self._cf
        cg.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
        cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        cf.CFArrayGetCount.restype = ctypes.c_long
        cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
        cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
        cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
        cf.CFDictionaryGetValue.restype = ctypes.c_void_p
        cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]

        class _R(ctypes.Structure):
            _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double),
                        ("w", ctypes.c_double), ("h", ctypes.c_double)]
        cg.CGRectMakeWithDictionaryRepresentation.restype = ctypes.c_bool
        cg.CGRectMakeWithDictionaryRepresentation.argtypes = [ctypes.c_void_p, ctypes.POINTER(_R)]

        def key(name):
            return cf.CFStringCreateWithCString(None, name, 0x08000100)

        def number(d, k):
            v = cf.CFDictionaryGetValue(d, k)
            n = ctypes.c_int64(0)
            if v:
                cf.CFNumberGetValue(v, 4, ctypes.byref(n))
            return n.value
        k_pid, k_layer, k_bounds = key(b"kCGWindowOwnerPID"), key(b"kCGWindowLayer"), key(b"kCGWindowBounds")
        arr = cg.CGWindowListCopyWindowInfo(1 | 16, 0)
        try:
            for i in range(cf.CFArrayGetCount(arr) if arr else 0):
                d = cf.CFArrayGetValueAtIndex(arr, i)
                if number(d, k_layer) != 0:
                    continue
                r = _R()
                b = cf.CFDictionaryGetValue(d, k_bounds)
                if b and cg.CGRectMakeWithDictionaryRepresentation(b, ctypes.byref(r)) \
                        and r.x <= x < r.x + r.w and r.y <= y < r.y + r.h:
                    return number(d, k_pid) or None
        finally:
            for o in (arr, k_pid, k_layer, k_bounds):
                if o:
                    cf.CFRelease(o)
        return None

    def _activate_at(self, x, y):
        try:
            pid = self._window_owner_at(x, y)
            if not pid or pid in own_pids():
                return
            if self._ax_frontmost(pid):
                time.sleep(0.15)
                return
            import ctypes
            import ctypes.util
            objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
            ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
            objc.objc_getClass.restype = ctypes.c_void_p
            objc.sel_registerName.restype = ctypes.c_void_p
            by_pid = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_int)(("objc_msgSend", objc))
            activate = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_ulong)(("objc_msgSend", objc))
            app = by_pid(objc.objc_getClass(b"NSRunningApplication"),
                         objc.sel_registerName(b"runningApplicationWithProcessIdentifier:"), int(pid))
            if app and not activate(app, objc.sel_registerName(b"activateWithOptions:"), 2):
                print(f"[HANDS] macOS did not make pid {pid} active", file=sys.stderr)
            time.sleep(0.15)
        except Exception as e:
            print(f"[HANDS] could not activate the app under the pointer: {e}", file=sys.stderr)

    def _ax_frontmost(self, pid):
        try:
            import ctypes
            ax = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
            cf = self._cf
            ax.AXUIElementCreateApplication.restype = ctypes.c_void_p
            ax.AXUIElementCreateApplication.argtypes = [ctypes.c_int]
            ax.AXUIElementSetAttributeValue.restype = ctypes.c_int32
            ax.AXUIElementSetAttributeValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
            cf.CFStringCreateWithCString.restype = ctypes.c_void_p
            cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
            true = ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue")
            el = ax.AXUIElementCreateApplication(int(pid))
            attr = cf.CFStringCreateWithCString(None, b"AXFrontmost", 0x08000100)
            try:
                err = ax.AXUIElementSetAttributeValue(el, attr, true)
            finally:
                for o in (el, attr):
                    if o:
                        cf.CFRelease(o)
            if err:
                print(f"[HANDS] Accessibility did not bring pid {pid} to the front (error {err})",
                      file=sys.stderr)
            return err == 0
        except Exception as e:
            print(f"[HANDS] Accessibility could not bring pid {pid} to the front: {e}", file=sys.stderr)
            return False

    def click(self, x, y, button="left", double=False):
        x, y = self._pt(x), self._pt(y)
        down, up, btn = {"right": (3, 4, 1), "middle": (25, 26, 2)}.get(button, (1, 2, 0))
        self._activate_at(x, y)
        self._mouse(5, x, y)
        time.sleep(0.05)
        for n in ((1, 2) if double else (1,)):
            self._mouse(down, x, y, btn, n)
            self._mouse(up, x, y, btn, n)
        return True

    def type_text(self, text):
        for ch in str(text):
            for down in (True, False):
                ev = self._cg.CGEventCreateKeyboardEvent(None, 0, down)
                self._cg.CGEventKeyboardSetUnicodeString(ev, 1, ch)
                self._cg.CGEventSetFlags(ev, 0)
                self._post(ev)
        return True

    _LAYOUT_PROBE = r"""
import ctypes, json
carbon = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Carbon.framework/Carbon")
cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
carbon.TISCopyCurrentKeyboardLayoutInputSource.restype = ctypes.c_void_p
carbon.TISGetInputSourceProperty.restype = ctypes.c_void_p
carbon.TISGetInputSourceProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
carbon.LMGetKbdType.restype = ctypes.c_uint8
carbon.UCKeyTranslate.restype = ctypes.c_int32
carbon.UCKeyTranslate.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16, ctypes.c_uint32,
    ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.c_ulong,
    ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
cf.CFDataGetBytePtr.restype = ctypes.c_void_p
cf.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
src = carbon.TISCopyCurrentKeyboardLayoutInputSource()
data = carbon.TISGetInputSourceProperty(src, ctypes.c_void_p.in_dll(carbon, "kTISPropertyUnicodeKeyLayoutData"))
layout = cf.CFDataGetBytePtr(data) if data else None
out = {}
if layout:
    kbd, buf = carbon.LMGetKbdType(), (ctypes.c_uint16 * 4)()
    for code in range(0, 64):
        dead, n = ctypes.c_uint32(0), ctypes.c_ulong(0)
        if carbon.UCKeyTranslate(layout, code, 3, 0, kbd, 1, ctypes.byref(dead), 4,
                                 ctypes.byref(n), buf) == 0 and n.value == 1:
            ch = chr(buf[0]).lower()
            if "a" <= ch <= "z" and ch not in out:
                out[ch] = code
print(json.dumps(out))
"""
    _layout_cache = (0.0, {})

    def _layout_letters(self):
        at, letters = MacBackend._layout_cache
        if time.time() - at < 10:
            return letters
        letters = {}
        try:
            r = subprocess.run([sys.executable, "-c", self._LAYOUT_PROBE],
                               capture_output=True, text=True, timeout=10)
            got = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
            letters = {k: int(v) for k, v in got.items()
                       if len(k) == 1 and "a" <= k <= "z" and 0 <= int(v) < 64}
            if r.returncode != 0:
                print(f"[HANDS] keyboard layout unreadable, US key positions used: "
                      f"{(r.stderr or '').strip()[-200:]}", file=sys.stderr)
        except Exception as e:
            print(f"[HANDS] keyboard layout unreadable, US key positions used: {e}", file=sys.stderr)
        MacBackend._layout_cache = (time.time(), letters)
        return letters

    def key(self, combo):
        keys = [k.strip().lower() for k in combo.split("+") if k.strip()]
        flags = 0
        main = []
        letters = self._layout_letters() if any(len(k) == 1 and "a" <= k <= "z" for k in keys) else {}
        for k in keys:
            if k in _MAC_FLAGS:
                flags |= _MAC_FLAGS[k]
            elif k in letters:
                main.append(letters[k])
            elif k in _MAC_KEYS:
                main.append(_MAC_KEYS[k])
            else:
                raise ValueError(f"unknown key '{k}'")
        for code in main or []:
            for down in (True, False):
                ev = self._cg.CGEventCreateKeyboardEvent(None, code, down)
                self._cg.CGEventSetFlags(ev, flags)
                self._post(ev)
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
    if not force and st == "macos":
        return MacBackend()
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


_FINE_MIN_PIXELS = 200


def changed_pixels(before, after):
    from PIL import ImageChops
    a, b = before.convert("L"), after.convert("L")
    if a.size != b.size:
        b = b.resize(a.size)
    hist = ImageChops.difference(a, b).point(lambda v: 255 if v > 24 else 0).histogram()
    return hist[255] if len(hist) > 255 else 0


def screen_changed(before, after, pct):
    if pct > 0.05:
        return True, None
    n = changed_pixels(before, after)
    return n >= _FINE_MIN_PIXELS, n



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
            out["screen_changed"], fine = screen_changed(before, after, pct)
            if fine is not None:
                out["pixels_changed_full_size"] = fine
            session.last_capture = after
        elif not self.pixels.can_capture():
            out["pixels"] = "no capture on this session: " + self.pixels.capture_why
        evidence = []
        if "screen_changed" in out:
            evidence.append(out.pop("screen_changed"))
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
                pct = changed_pct(session.last_capture, after)
                out["pixels_changed_pct_since_last"] = pct
                changed, fine = screen_changed(session.last_capture, after, pct)
                if fine is not None:
                    out["pixels_changed_full_size"] = fine
                session.last_capture = after
        verified = out.get("text_found")
        if verified is None and "pixels_changed_pct_since_last" in out:
            verified = changed
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
