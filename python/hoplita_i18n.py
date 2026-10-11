
import json, os

_strings = {}
_lang = "en"
_lang_dir = None

def _find_lang_dir():
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "lang"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lang"),
    ]
    try:
        import hoplita_paths
        candidates.append(hoplita_paths.lang_dir())
    except Exception:
        candidates.append("/opt/hoplita/lang")
    if os.name == "nt":
        appdata = os.getenv("APPDATA", "")
        if appdata:
            candidates.append(os.path.join(appdata, "hoplita", "lang"))
    for d in candidates:
        if os.path.isdir(d):
            return d
    return candidates[0]

def load_language(lang_code=None, lang_dir=None):
    global _strings, _lang, _lang_dir
    _lang_dir = lang_dir or _find_lang_dir()
    _lang = lang_code or "en"
    
    path = os.path.join(_lang_dir, f"{_lang}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            _strings = json.load(f)
        print(f"[i18n] {_lang}: {path} ({len(_strings)} keys)")
        return True
    
    fallback = os.path.join(_lang_dir, "en.json")
    if os.path.exists(fallback):
        with open(fallback, "r", encoding="utf-8") as f:
            _strings = json.load(f)
        print(f"[i18n] Fallback en: {fallback}")
        return True
    
    print(f"[i18n] ⚠ No lang files found in {_lang_dir}")
    return False

def t(key, **kwargs):
    text = _strings.get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text
