import sys

from PyQt6.QtCore import QObject, QEvent
from PyQt6.QtWidgets import QApplication, QMessageBox

_TITLE = "hoplita_title"
_installed = False


def _apply(box):
    title = box.property(_TITLE)
    if not title or box.property("hoplita_title_done"):
        return
    box.setProperty("hoplita_title_done", True)
    body = box.text()
    if body and body != title:
        info = box.informativeText()
        box.setInformativeText(body + ("\n\n" + info if info else ""))
    box.setText(title)


class _OnShow(QObject):
    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Show and isinstance(obj, QMessageBox):
            _apply(obj)
        return False


def _static(icon):
    def helper(parent, title, text,
               buttons=QMessageBox.StandardButton.Ok,
               defaultButton=QMessageBox.StandardButton.NoButton):
        box = QMessageBox(parent)
        box.setIcon(icon)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(buttons)
        if defaultButton != QMessageBox.StandardButton.NoButton:
            box.setDefaultButton(defaultButton)
        box.exec()
        clicked = box.clickedButton()
        if clicked is None:
            return QMessageBox.StandardButton.NoButton
        return box.standardButton(clicked)
    return staticmethod(helper)


def install(force=False):
    global _installed
    if _installed or not (force or sys.platform == "darwin"):
        return False
    _installed = True
    _set_title = QMessageBox.setWindowTitle

    def set_window_title(self, title):
        self.setProperty(_TITLE, title)
        _set_title(self, title)

    QMessageBox.setWindowTitle = set_window_title

    def opener(orig):
        def run(self, *a, **kw):
            _apply(self)
            return orig(self, *a, **kw)
        return run

    for _name in ("exec", "show", "open"):
        setattr(QMessageBox, _name, opener(getattr(QMessageBox, _name)))
    QMessageBox.question = _static(QMessageBox.Icon.Question)
    QMessageBox.warning = _static(QMessageBox.Icon.Warning)
    QMessageBox.information = _static(QMessageBox.Icon.Information)
    QMessageBox.critical = _static(QMessageBox.Icon.Critical)
    app = QApplication.instance()
    if app is not None:
        app._mac_dialogs_filter = _OnShow(app)
        app.installEventFilter(app._mac_dialogs_filter)
    return True


def deactivate_app():
    try:
        import ctypes
        import ctypes.util
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.sel_registerName.restype = ctypes.c_void_p
        send = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
            ("objc_msgSend", objc))
        app = send(objc.objc_getClass(b"NSApplication"),
                   objc.sel_registerName(b"sharedApplication"))
        send(app, objc.sel_registerName(b"deactivate"))
    except Exception as e:
        print(f"[MAC] could not stop being the active app: {e}", file=sys.stderr)


def activate_app():
    try:
        import ctypes
        import ctypes.util
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.sel_registerName.restype = ctypes.c_void_p
        send = objc.objc_msgSend
        send.restype = ctypes.c_void_p
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        app = send(objc.objc_getClass(b"NSApplication"),
                   objc.sel_registerName(b"sharedApplication"))
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
        send(app, objc.sel_registerName(b"activateIgnoringOtherApps:"), True)
    except Exception as e:
        print(f"[MAC] could not become the active app: {e}", file=sys.stderr)
