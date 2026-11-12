import configparser
import json

_STRUCTURAL = ("messages", "model", "tools", "tool_choice", "stream")


def _sections_for(cfg, base_url, model):
    if not cfg:
        return []
    want_url = (base_url or "").rstrip("/").lower()
    want_model = (model or "").strip()
    fb_url = cfg.get("API", "base_url", fallback="").rstrip("/").lower()
    fb_key_model = cfg.get("API", "model", fallback="").strip()
    out, api_hit = [], False
    for sec in cfg.sections():
        if sec.upper() != "API" and not sec.upper().startswith("MODEL:"):
            continue
        url = (cfg.get(sec, "base_url", fallback="").rstrip("/").lower()
               or fb_url)
        mdl = cfg.get(sec, "model", fallback="").strip() or fb_key_model
        if url == want_url and mdl == want_model:
            if sec.upper() == "API":
                api_hit = True
            else:
                out.append(sec)
    if api_hit:
        out.append("API")
    return out


def extra_body_for(cfg, base_url, model, warn=None):
    for sec in _sections_for(cfg, base_url, model):
        raw = (cfg.get(sec, "extra_body", fallback="") or "").strip()
        if not raw:
            continue
        try:
            val = json.loads(raw)
        except Exception as e:
            if warn:
                warn("[%s] extra_body is not valid JSON (%s): %s"
                     % (sec, e, raw[:120]))
            continue
        if not isinstance(val, dict):
            if warn:
                warn("[%s] extra_body must be a JSON OBJECT, got %s"
                     % (sec, type(val).__name__))
            continue
        return val
    return {}


def apply_extra_body(payload, extra, warn=None):
    for k, v in (extra or {}).items():
        if k in _STRUCTURAL:
            if warn:
                warn("extra_body cannot override '%s' — ignored" % k)
            continue
        payload[k] = v
    return payload


def merge_extra_body(cfg, payload, base_url, model, warn=None):
    xb = extra_body_for(cfg, base_url, model, warn=warn)
    return apply_extra_body(payload, xb, warn=warn) if xb else payload



_TRUE = ("1", "true", "yes", "on")


def model_section(cfg, name):
    if not cfg or not name:
        return None
    want = f"MODEL:{name}"
    if cfg.has_section(want):
        return want
    low = want.lower()
    for sec in cfg.sections():
        if sec.lower() == low:
            return sec
    return None


def base_alias(cfg):
    if cfg and cfg.has_section("MODELS"):
        return (cfg.get("MODELS", "base", fallback="") or "").strip()
    return ""



LEND_PATH = "/lend"


def mcp_url(cfg):
    url = ""
    if cfg and cfg.has_section("MCP"):
        url = (cfg.get("MCP", "url", fallback="") or "").strip()
    return (url or "http://127.0.0.1:9077").rstrip("/")


def lent_from(cfg, section):
    if not cfg or not section or not cfg.has_section(section):
        return ""
    return (cfg.get(section, "via", fallback="") or "").strip()


def lends(cfg, section):
    if not cfg or not section or not cfg.has_section(section):
        return False
    if lent_from(cfg, section):
        return False
    return (cfg.get(section, "lend", fallback="") or "").strip().lower() in _TRUE


def lend_url(cfg, handle, alias):
    return f"{mcp_url(cfg)}{LEND_PATH}/{handle}/{alias}/v1"


def lend_of_url(url):
    u = (url or "").strip().rstrip("/")
    marker = LEND_PATH + "/"
    i = u.find(marker)
    if i < 0:
        return None
    rest = u[i + len(marker):]
    if rest.endswith("/v1"):
        rest = rest[:-3]
    parts = rest.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def _lent_endpoint(cfg, sec):
    handle = lent_from(cfg, sec)
    alias = (cfg.get(sec, "model", fallback="") or "").strip() or sec.split(":", 1)[-1]
    return lend_url(cfg, handle, alias), "", alias


def resolve_endpoint(cfg, section):
    fb_url = cfg.get("API", "base_url", fallback="https://api.openai.com/v1") or "https://api.openai.com/v1"
    fb_key = cfg.get("API", "api_key", fallback="") or ""
    fb_model = cfg.get("API", "model", fallback="") or ""
    if section == "API":
        sec = model_section(cfg, base_alias(cfg))
        if sec and lent_from(cfg, sec):
            return _lent_endpoint(cfg, sec)
        if sec:
            return ((cfg.get(sec, "base_url", fallback="") or fb_url).rstrip("/"),
                    cfg.get(sec, "api_key", fallback="") or fb_key,
                    cfg.get(sec, "model", fallback="") or fb_model)
        return fb_url.rstrip("/"), fb_key, fb_model
    b_url, b_key, b_model = resolve_endpoint(cfg, "API")
    if not section.upper().startswith("MODEL:"):
        named = (cfg.get(section, "model", fallback="") or "").strip() \
            if cfg.has_section(section) else ""
        target = model_section(cfg, named) if named else None
        if target:
            return resolve_endpoint(cfg, target)
    if not cfg.has_section(section):
        return b_url, b_key, b_model
    if lent_from(cfg, section):
        return _lent_endpoint(cfg, section)
    return ((cfg.get(section, "base_url", fallback="") or b_url).rstrip("/"),
            cfg.get(section, "api_key", fallback="") or b_key,
            cfg.get(section, "model", fallback="") or b_model)


def base_model(cfg):
    return resolve_endpoint(cfg, "API")[2]



def _declared(cfg, sec):
    v = (cfg.get(sec, "vision", fallback="") or "").strip().lower()
    if not v:
        return None
    return v in _TRUE


def model_sees(cfg, base_url, model):
    url = (base_url or "").rstrip("/").lower()
    mdl = (model or "").strip()
    same_server, same_model = [], []
    if cfg and mdl:
        for sec in cfg.sections():
            if not sec.upper().startswith("MODEL:"):
                continue
            d = _declared(cfg, sec)
            if d is None:
                continue
            r_url, _k, r_model = resolve_endpoint(cfg, sec)
            if r_model.strip() != mdl:
                continue
            (same_server if r_url.rstrip("/").lower() == url else same_model).append(d)
    for group in (same_server, same_model):
        if group:
            return any(group)
    if not cfg:
        return False
    return (cfg.get("CHAT", "vision", fallback="false") or "").strip().lower() in _TRUE



def ini_problem(text):
    cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    try:
        cp.read_string(text)
    except configparser.Error as e:
        return str(e).split("\n")[0]
    return None


def read_config(path, restore=False):
    import os, shutil, time as _time
    cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    if not os.path.exists(path):
        return cp, None
    try:
        cp.read(path, encoding="utf-8")
        return cp, None
    except configparser.Error as e:
        why = str(e).split("\n")[0]
    if restore:
        try:
            with open(path, encoding="utf-8") as fh:
                _text = fh.read()
            _fixed, _notes = repair_ini(_text)
            if _notes and ini_problem(_fixed) is None:
                shutil.copy(path, path + ".broken-" + _time.strftime("%Y%m%d-%H%M%S"))
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(_fixed)
                cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
                cp.read(path, encoding="utf-8")
                return cp, "repaired (" + "; ".join(_notes)[:200] + ") — " + why
        except Exception:
            pass
    bak = path + ".bak"
    if restore and os.path.exists(bak):
        try:
            with open(bak, encoding="utf-8") as fh:
                bak_text = fh.read()
        except Exception:
            bak_text = None
        if bak_text is not None and ini_problem(bak_text) is None:
            try:
                shutil.copy(path, path + ".broken-" + _time.strftime("%Y%m%d-%H%M%S"))
                shutil.copy(bak, path)
                cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
                cp.read(path, encoding="utf-8")
                return cp, "restored .bak — " + why
            except Exception:
                pass
    lenient = configparser.ConfigParser(inline_comment_prefixes=(";", "#"), strict=False)
    try:
        lenient.read(path, encoding="utf-8")
    except configparser.Error:
        lenient = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    return lenient, "read leniently — " + why



_SECTCRE = configparser.ConfigParser.SECTCRE
_OPTCRE = configparser.ConfigParser.OPTCRE
_INLINE = (";", "#")


def _is_header(line):
    return bool(_SECTCRE.match(line.strip()))


def _scan(lines):
    roles = []
    sect, opt, indent_level = None, None, 0
    for line in lines:
        cs = None
        for pfx in _INLINE:
            idx = -1
            while True:
                idx = line.find(pfx, idx + 1)
                if idx == -1:
                    break
                if idx == 0 or line[idx - 1].isspace():
                    cs = idx if cs is None else min(cs, idx)
                    break
        if line.strip().startswith(_INLINE):
            cs = 0
        value = (line[:cs] if cs is not None else line).strip()
        if not value:
            roles.append(("comment" if cs is not None else "blank", sect, opt))
            continue
        cur = len(line) - len(line.lstrip())
        if sect is not None and opt and cur > indent_level:
            roles.append(("cont", sect, opt))
            continue
        indent_level = cur
        mo = _SECTCRE.match(value)
        if mo:
            sect, opt = mo.group("header"), None
            roles.append(("header", sect, None))
            continue
        if sect is None:
            roles.append(("bad", None, None))
            continue
        mo = _OPTCRE.match(value)
        if mo and mo.group("option"):
            opt = mo.group("option").rstrip().lower()
            roles.append(("key", sect, opt))
        else:
            roles.append(("bad", sect, opt))
    return roles


def _find_section(lines, section, roles=None):
    roles = roles if roles is not None else _scan(lines)
    heads = [(n, r[1]) for n, r in enumerate(roles) if r[0] == "header"]
    for n, name in heads:
        if name == section:
            return n
    for n, name in heads:
        if name.lower() == section.lower():
            return n
    return None


def _entry(lines, roles, head, key):
    sect = roles[head][1]
    key = key.lower()
    for n in range(head + 1, len(roles)):
        role, s_, k_ = roles[n]
        if role == "header":
            return None
        if role == "key" and s_ == sect and k_ == key:
            end = n + 1
            for m in range(n + 1, len(roles)):
                r2 = roles[m]
                if r2[0] in ("header", "key", "bad"):
                    break
                if r2[0] == "cont":
                    end = m + 1
            return n, end
    return None


def _value_lines(key, value, indent=""):
    parts = str(value).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = [f"{indent}{key} = {parts[0].rstrip()}"]
    out += [indent + "    " + p.strip() for p in parts[1:] if p.strip()]
    return out


def set_value(text, section, key, value):
    lines = text.split("\n")
    roles = _scan(lines)
    head = _find_section(lines, section, roles)
    if head is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [f"[{section}]"] + _value_lines(key, value)
        return "\n".join(lines)
    found = _entry(lines, roles, head, key)
    if found:
        k, end = found
        indent = lines[k][:len(lines[k]) - len(lines[k].lstrip())]
        new = _value_lines(key, value, indent)
        new[0] = _keep_inline_comment(lines[k], new[0])
        lines[k:end] = new
        return "\n".join(lines)
    lines[head + 1:head + 1] = _value_lines(key, value)
    return "\n".join(lines)


def del_section(text, section):
    lines = text.split("\n")
    roles = _scan(lines)
    head = _find_section(lines, section, roles)
    if head is None:
        return text
    end = next((n for n in range(head + 1, len(roles)) if roles[n][0] == "header"), len(lines))
    return "\n".join(lines[:head] + lines[end:])


def del_key(text, section, key):
    lines = text.split("\n")
    roles = _scan(lines)
    head = _find_section(lines, section, roles)
    if head is None:
        return text
    found = _entry(lines, roles, head, key)
    if not found:
        return text
    del lines[found[0]:found[1]]
    return "\n".join(lines)



def _keep_inline_comment(old_line, new_line):
    import re as _re
    m = _re.search(r"\s+([;#].*)$", old_line)
    return f"{new_line}   {m.group(1)}" if m else new_line


def _dirty_header(line):
    v = line.strip()
    mo = _SECTCRE.match(v)
    if not mo:
        return False
    rest = v[mo.end():].strip()
    return bool(rest) and not rest.startswith(_INLINE)


def repair_ini(text):
    notes = []
    lines = text.split("\n")
    for _ in range(200):
        roles = _scan(lines)
        fix = None
        last_key = None
        for n, (role, _s, _k) in enumerate(roles):
            if role == "key":
                last_key = n
            elif role == "header":
                if last_key is not None and _dirty_header(lines[n]):
                    fix = (n, last_key)
                    break
                last_key = None
        if not fix:
            break
        n, k = fix
        indent = lines[k][:len(lines[k]) - len(lines[k].lstrip())]
        notes.append(lines[n].strip()[:60])
        lines[n] = indent + "    " + lines[n].strip()
    dups = 0
    for _ in range(500):
        roles = _scan(lines)
        seen = set()
        drop = None
        for n, (role, sect, key) in enumerate(roles):
            if role == "key":
                if (sect, key) in seen:
                    end = n + 1
                    for m in range(n + 1, len(roles)):
                        if roles[m][0] in ("header", "key", "bad"):
                            break
                        if roles[m][0] == "cont":
                            end = m + 1
                    drop = (n, end)
                    break
                seen.add((sect, key))
        if not drop:
            break
        del lines[drop[0]:drop[1]]
        dups += 1
    if dups:
        notes.append(f"{dups} repeated key(s) removed")
    return "\n".join(lines), notes
