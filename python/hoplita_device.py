
import json
import os
import platform
import uuid

from hoplita_paths import hoplita_base

_FILE = "device.json"
_cache = None


def _path():
    return os.path.join(hoplita_base(), _FILE)


def _default_name():
    try:
        n = platform.node().split(".")[0].strip()
    except Exception:
        n = ""
    return n or "hoplita"


def device():
    global _cache
    if _cache is not None:
        return dict(_cache)
    data = {}
    try:
        with open(_path(), encoding="utf-8") as fh:
            data = json.load(fh) or {}
    except Exception:
        data = {}
    if not data.get("id"):
        data = {"id": uuid.uuid4().hex[:12], "name": _default_name()}
        _save(data)
    data.setdefault("name", _default_name())
    _cache = data
    return dict(data)


def device_id():
    return device()["id"]


def device_name():
    return device()["name"]


def rename(name):
    name = " ".join(str(name or "").split())[:40]
    if not name:
        return device()
    d = device()
    d["name"] = name
    _save(d)
    global _cache
    _cache = d
    return dict(d)


def regenerate():
    d = {"id": uuid.uuid4().hex[:12], "name": _default_name()}
    _save(d)
    global _cache
    _cache = d
    return dict(d)


def same_device_as(other_id):
    return bool(other_id) and other_id == device_id()


def _save(data):
    try:
        os.makedirs(os.path.dirname(_path()), exist_ok=True)
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, _path())
    except Exception:
        pass
