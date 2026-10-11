import base64
import mimetypes
import os
import re

_IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
_SRC = re.compile(r"""(<img\b[^>]*?\bsrc\s*=\s*)(["'])([^"']+)\2""", re.I)


def is_image_path(path):
    return os.path.splitext(str(path or ""))[1].lower() in _IMG_EXT


def image_data_uri(path, max_bytes=8 * 1024 * 1024):
    p = os.path.expanduser(str(path or "").strip())
    if p.startswith("file://"):
        p = p[len("file://"):]
    if not is_image_path(p) or not os.path.isfile(p):
        return None
    if os.path.getsize(p) > max_bytes:
        return None
    mime = mimetypes.guess_type(p)[0] or "image/png"
    with open(p, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode("ascii")


def inline_local_images(html, max_bytes=8 * 1024 * 1024):
    if not html or "<img" not in html.lower():
        return html

    def _one(m):
        src = m.group(3).strip()
        low = src.lower()
        if low.startswith(("http://", "https://", "data:", "blob:", "//")):
            return m.group(0)
        uri = image_data_uri(src, max_bytes=max_bytes)
        if not uri:
            return m.group(0)
        return f"{m.group(1)}{m.group(2)}{uri}{m.group(2)}"
    return _SRC.sub(_one, html)

