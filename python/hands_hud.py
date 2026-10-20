from PyQt6.QtCore import Qt, QTimer, QPoint, QPointF, QRect, QRectF, pyqtSignal
from PyQt6.QtGui import (QPainter, QPixmap, QColor, QRadialGradient, QAction,
                         QFont, QFontMetrics, QRegion, QGuiApplication)
from PyQt6.QtWidgets import QWidget, QMenu, QApplication
import math
import sys
import time

COLOR_WORKING = "#10b981"
COLOR_PAUSED = "#6b7280"
COLOR_ERROR = "#ef4444"
_DRAG_THRESHOLD_PX = 6
_CAPTION_FLASH_MS = 2600
_MARGIN = 24


def _is_wayland():
    try:
        return QGuiApplication.platformName().lower().startswith("wayland")
    except Exception:
        return False


class _Caption(QWidget):

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool
                            | Qt.WindowType.WindowDoesNotAcceptFocus
                            | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._text = ""
        self._font = QFont()
        self._font.setPointSize(10)
        self._pad_x, self._pad_y = 12, 7

    @property
    def text(self):
        return self._text

    def set_text(self, text):
        self._text = text or ""
        w, h = caption_size(self._text, self._font, self._pad_x, self._pad_y)
        self.setFixedSize(w, h)
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        paint_caption(p, QRect(0, 0, self.width(), self.height()),
                      self._text, self._font, self._pad_x)
        p.end()


def caption_size(text, font, pad_x=12, pad_y=7):
    fm = QFontMetrics(font)
    w = min(fm.horizontalAdvance(text) + pad_x * 2, 460)
    h = fm.height() + pad_y * 2
    return max(w, 40), h


def paint_caption(p, rect, text, font, pad_x):
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(15, 23, 42, 235))
    p.drawRoundedRect(QRectF(rect), 8, 8)
    p.setPen(QColor(226, 232, 240))
    p.setFont(font)
    p.drawText(QRectF(rect.x() + pad_x, rect.y(), rect.width() - pad_x * 2, rect.height()),
               int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)


class HoplitaHud(QWidget):
    stop_clicked = pyqtSignal()
    pause_requested = pyqtSignal(bool)
    back_requested = pyqtSignal()

    def __init__(self, icon_path, t=None, size=56, parent=None, overlay=None):
        super().__init__(parent)
        self._t = t or (lambda k, **kw: k)
        self._size = int(size)
        self._state = "working"
        self._paused = False
        self._action = ""
        self._phase = 0.0
        self._press_pos = None
        self._dragging = False
        self._hovering = False
        self._overlay = _is_wayland() if overlay is None else bool(overlay)
        self._pix = QPixmap(icon_path) if icon_path else QPixmap()
        if not self._pix.isNull():
            self._pix = self._pix.scaled(self._size, self._size,
                                         Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
        flags = (Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.Tool
                 | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cap_font = QFont()
        self._cap_font.setPointSize(10)
        self._cap_text = ""
        if self._overlay:
            self._fig = QRect(0, 0, self._size, self._size)
            self._caption = None
        else:
            pad = self._size // 2
            self.setFixedSize(self._size + pad, self._size + pad)
            self._fig = QRect(0, 0, self._size + pad, self._size + pad)
            self._caption = _Caption()
        self._flash = QTimer(self)
        self._flash.setSingleShot(True)
        self._flash.timeout.connect(self._maybe_hide_caption)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)
        self.set_action("")

    @property
    def state(self):
        return self._state

    @property
    def paused(self):
        return self._paused

    @property
    def overlay(self):
        return self._overlay

    def set_state(self, state):
        self._state = state if state in ("working", "paused", "error") else "working"
        self._paused = self._state == "paused"
        self.update()

    def set_action(self, text):
        self._action = text or ""
        line = self._action or self._t("hands.hud_tip_idle")
        self.setToolTip(line)
        self._cap_text = line
        if self._caption is not None:
            self._caption.set_text(line)
        if self.isVisible():
            self._show_caption()
            self._flash.start(_CAPTION_FLASH_MS)

    @property
    def action(self):
        return self._action

    def caption_text(self):
        return self._cap_text

    @property
    def caption_visible(self):
        if self._overlay:
            return self._caption_on
        return self._caption is not None and self._caption.isVisible()

    _caption_on = False

    def _screen_geo(self):
        scr = QApplication.primaryScreen()
        return scr.geometry() if scr else QRect(0, 0, 1280, 800)

    def _fig_center(self):
        return self._fig.center()

    def _caption_rect(self):
        w, h = caption_size(self._cap_text or " ", self._cap_font)
        x = self._fig.left() - w - 8
        y = self._fig.center().y() - h // 2
        bounds = self.rect()
        if x < bounds.left() + 2:
            x = self._fig.left()
            y = self._fig.top() - h - 6
        x = max(bounds.left() + 2, min(x, bounds.right() - w - 2))
        y = max(bounds.top() + 2, min(y, bounds.bottom() - h - 2))
        return QRect(int(x), int(y), w, h)

    def show_in_corner(self, margin=_MARGIN):
        g = self._screen_geo()
        if self._overlay:
            self.setGeometry(g)
            self._fig = QRect(g.width() - self._size - margin,
                              g.height() - self._size - margin,
                              self._size, self._size)
            self._apply_mask()
            self.showFullScreen()
        else:
            scr = QApplication.primaryScreen()
            g = scr.availableGeometry() if scr else g
            self.move(g.right() - self.width() - margin,
                      g.bottom() - self.height() - margin)
            self.show()

    def _apply_mask(self):
        if not self._overlay:
            return
        region = QRegion(self._fig.adjusted(-2, -2, 2, 2))
        if self._caption_on and self._cap_text:
            region = region.united(QRegion(self._caption_rect()))
        self._mask_region = region
        self.setMask(region)

    def mask_region(self):
        return getattr(self, "_mask_region", QRegion())

    def _show_caption(self):
        if not self._cap_text:
            return
        if self._overlay:
            self._caption_on = True
            self._apply_mask()
            self.update()
        else:
            self._position_caption()
            self._caption.show()
            if sys.platform != "darwin":
                self._caption.raise_()

    def _position_caption(self):
        c = self._caption
        c.set_text(self._cap_text)
        hud = self.frameGeometry()
        w, h = c.width(), c.height()
        x = hud.left() - w - 8
        y = hud.center().y() - h // 2
        g = self._screen_geo()
        if x < g.left() + 4:
            x = hud.left()
            y = hud.top() - h - 6
        x = max(g.left() + 4, min(x, g.right() - w - 4))
        y = max(g.top() + 4, min(y, g.bottom() - h - 4))
        c.move(int(x), int(y))

    def _maybe_hide_caption(self):
        if not self._hovering:
            self._hide_caption()

    def _hide_caption(self):
        if self._overlay:
            self._caption_on = False
            self._apply_mask()
            self.update()
        elif self._caption is not None:
            self._caption.hide()

    def enterEvent(self, ev):
        self._hovering = True
        self._show_caption()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._hovering = False
        if not self._flash.isActive():
            self._hide_caption()
        super().leaveEvent(ev)

    def _tick(self):
        if self._state == "working":
            self._phase = (time.monotonic() % 3.2) / 3.2
            self.update()

    def _halo_color(self):
        if self._state == "paused":
            return QColor(COLOR_PAUSED), 70
        if self._state == "error":
            return QColor(COLOR_ERROR), 150
        c = QColor(COLOR_WORKING)
        a = 60 + int(90 * (0.5 + 0.5 * math.sin(self._phase * 2 * math.pi)))
        return c, a

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        fr = self._fig
        color, alpha = self._halo_color()
        grad = QRadialGradient(QPointF(fr.center()), fr.width() / 2)
        inner = QColor(color)
        inner.setAlpha(alpha)
        outer = QColor(color)
        outer.setAlpha(0)
        grad.setColorAt(0.35, inner)
        grad.setColorAt(1.0, outer)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawEllipse(QRectF(fr))
        if not self._pix.isNull():
            if self._state == "paused":
                p.setOpacity(0.55)
            p.drawPixmap(int(fr.center().x() - self._pix.width() / 2),
                         int(fr.center().y() - self._pix.height() / 2), self._pix)
            p.setOpacity(1.0)
        if self._overlay and self._caption_on and self._cap_text:
            paint_caption(p, self._caption_rect(), self._cap_text, self._cap_font, 12)
        p.end()

    def _on_figure(self, pos):
        return (not self._overlay) or self._fig.contains(pos)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton and self._on_figure(ev.position().toPoint()):
            self._press_pos = ev.globalPosition().toPoint()
            self._press_offset = self._press_pos - (
                self._fig.topLeft() if self._overlay else self.frameGeometry().topLeft())
            self._dragging = False
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._press_pos is not None and (ev.buttons() & Qt.MouseButton.LeftButton):
            here = ev.globalPosition().toPoint()
            if self._dragging or (here - self._press_pos).manhattanLength() > _DRAG_THRESHOLD_PX:
                self._dragging = True
                if self._overlay:
                    self._fig.moveTopLeft(here - self._press_offset)
                    self._apply_mask()
                    self.update()
                else:
                    self.move(here - self._press_offset)
                    if self._caption is not None and self._caption.isVisible():
                        self._position_caption()
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton and self._press_pos is not None:
            was_drag = self._dragging
            self._press_pos = None
            self._dragging = False
            if not was_drag:
                self.stop_clicked.emit()
        super().mouseReleaseEvent(ev)

    def contextMenuEvent(self, ev):
        if not self._on_figure(ev.pos()):
            return
        menu = QMenu(self)
        if self._paused:
            a = QAction(self._t("hands.hud_menu_resume"), menu)
            a.triggered.connect(lambda: self.pause_requested.emit(False))
        else:
            a = QAction(self._t("hands.hud_menu_pause"), menu)
            a.triggered.connect(lambda: self.pause_requested.emit(True))
        menu.addAction(a)
        b = QAction(self._t("hands.hud_menu_back"), menu)
        b.triggered.connect(self.back_requested.emit)
        menu.addAction(b)
        menu.addSeparator()
        s = QAction(self._t("hands.hud_menu_stop"), menu)
        s.triggered.connect(self.stop_clicked.emit)
        menu.addAction(s)
        menu.exec(ev.globalPos())

    def menu_actions(self):
        return [
            (self._t("hands.hud_menu_resume") if self._paused else self._t("hands.hud_menu_pause"),
             lambda: self.pause_requested.emit(not self._paused)),
            (self._t("hands.hud_menu_back"), self.back_requested.emit),
            (self._t("hands.hud_menu_stop"), self.stop_clicked.emit),
        ]

    def hideEvent(self, ev):
        if self._caption is not None:
            self._caption.hide()
        self._caption_on = False
        super().hideEvent(ev)

    def closeEvent(self, ev):
        self._timer.stop()
        self._flash.stop()
        if self._caption is not None:
            self._caption.close()
        super().closeEvent(ev)
