#!/usr/bin/env python3
import os
import re


_SECRET_KEY = re.compile(
    r"(^|_)(api_?key|key|password|passwd|pass|secret|token|cookie|"
    r"credentials?|auth|bearer)$", re.I)

_SECRET_IN_TEXT = re.compile(
    r"\b(sk-[A-Za-z0-9_\-]{8,}|sk_[A-Za-z0-9_\-]{8,}|pplx-[A-Za-z0-9]{8,}|"
    r"ghp_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,}|xox[abprs]-[A-Za-z0-9\-]{8,}|"
    r"AIza[A-Za-z0-9_\-]{20,}|fc-[A-Za-z0-9]{16,}|hf_[A-Za-z0-9]{16,})")
_SECRET_QUERY = re.compile(
    r"([?&](?:api_?key|key|token|access_token|secret|password|auth)=)([^&\s]+)",
    re.I)

MASK = "••••"


def is_secret_key(key):
    return bool(_SECRET_KEY.search((key or "").strip()))


def mask_text(text):
    s = str(text or "")
    s = _SECRET_QUERY.sub(lambda m: m.group(1) + MASK, s)
    return _SECRET_IN_TEXT.sub(lambda m: m.group(0)[:3] + MASK, s)


def mask_value(key, value):
    v = str(value if value is not None else "")
    if is_secret_key(key):
        if not v.strip():
            return "(empty)"
        return f"{MASK} (set, {len(v.strip())} characters)"
    return mask_text(v)


def looks_masked(value):
    return MASK in str(value or "")



def overview(cfg):
    out = []
    for sect in cfg.sections():
        out.append({"section": sect, "keys": list(cfg.options(sect))})
    return out


def section_values(cfg, section):
    real = next((s for s in cfg.sections() if s == section), None) or \
        next((s for s in cfg.sections() if s.lower() == (section or "").lower()), None)
    if real is None:
        return None, None
    return real, {k: mask_value(k, cfg.get(real, k, raw=True, fallback=""))
                  for k in cfg.options(real)}


_NAME_OK = re.compile(r"^[A-Za-z0-9_.\-]+$")
_SECTION_OK = re.compile(r"^[A-Za-z0-9_.\-]+(:[A-Za-z0-9_.\-]+)?$")


def bad_name(section, key):
    if not section or not _SECTION_OK.match(section):
        return ("the section name must be letters, digits, '_', '.' or '-' "
                "(and one ':' as in MODEL:<alias>)")
    if not key or not _NAME_OK.match(key):
        return "the key must be letters, digits, '_', '.' or '-'"
    return None


def mcp_server_of(cfg, key):
    key = (key or "").lower()
    if not cfg.has_section("MCP_SERVERS"):
        return None
    servers = [k for k in cfg.options("MCP_SERVERS")
               if (cfg.get("MCP_SERVERS", k, raw=True, fallback="") or "")
               .startswith(("stdio:", "sse:"))]
    if key in servers:
        return key
    owners = [s for s in servers if key.startswith(s + "_")]
    return max(owners, key=len) if owners else None



_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
SECTION_CAP = 12000


def doc_files(base):
    out = []
    d = os.path.join(base, "docs")
    if os.path.isdir(d):
        out += sorted(os.path.join(d, f) for f in os.listdir(d)
                      if f.lower().endswith(".md"))
    readme = os.path.join(base, "README.md")
    if os.path.isfile(readme):
        out.append(readme)
    return out


def _sections(path):
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    except Exception:
        return [], []
    heads, fence = [], False
    for n, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        m = _HEADING.match(line)
        if m:
            heads.append((len(m.group(1)), m.group(2).strip(), n))
    out = []
    for i, (lvl, title, start) in enumerate(heads):
        end = len(lines)
        for lvl2, _t, s2 in heads[i + 1:]:
            if lvl2 <= lvl:
                end = s2
                break
        out.append((lvl, title, start, end))
    return out, lines


def outline(base, file=None, depth=2):
    files = doc_files(base)
    if file:
        files = [f for f in files if os.path.basename(f).lower() == file.lower()]
        depth = max(depth, 3)
    out = []
    for f in files:
        secs, _lines = _sections(f)
        out.append({"file": os.path.basename(f),
                    "sections": ["  " * (lvl - 1) + title
                                 for lvl, title, _s, _e in secs if lvl <= depth]})
    return out


def _words(q):
    return [w for w in re.findall(r"[\w\[\]\-\.]+", (q or "").lower()) if len(w) > 1]


def search(base, query, limit=6):
    words = _words(query)
    if not words:
        return []
    hits = []
    for f in doc_files(base):
        secs, lines = _sections(f)
        starts = [s for _l, _t, s, _e in secs]
        for i, (lvl, title, start, _end) in enumerate(secs):
            leaf_end = starts[i + 1] if i + 1 < len(starts) else len(lines)
            body = lines[start + 1:leaf_end]
            low_t = title.lower()
            low_b = "\n".join(body).lower()
            in_title = sum(1 for w in words if w in low_t)
            in_body = sum(1 for w in words if w in low_b)
            if not in_title and not in_body:
                continue
            covered = sum(1 for w in words if w in low_t or w in low_b)
            score = covered * 10 + in_title * 5 + min(in_body, 5)
            match = [ln.strip() for ln in body
                     if any(w in ln.lower() for w in words) and ln.strip()][:3]
            hits.append({"file": os.path.basename(f), "section": title,
                         "score": score, "lines": [m[:200] for m in match]})
    hits.sort(key=lambda h: -h["score"])
    for h in hits:
        h.pop("score", None)
    return hits[:limit]


def read_section(base, section, file=None):
    want = (section or "").strip().lower().lstrip("#").strip()
    if not want:
        return None, []
    found = []
    for f in doc_files(base):
        if file and os.path.basename(f).lower() != file.lower():
            continue
        secs, lines = _sections(f)
        for lvl, title, start, end in secs:
            t_ = title.lower()
            rank = 0 if t_ == want else (1 if want in t_ else None)
            if rank is not None:
                found.append((rank, lvl, f, title, start, end, lines))
    if not found:
        return None, []
    found.sort(key=lambda x: (x[0], x[1]))
    rank, lvl, f, title, start, end, lines = found[0]
    text = "\n".join(lines[start:end]).strip()
    truncated = len(text) > SECTION_CAP
    hit = {"file": os.path.basename(f), "section": title,
           "text": text[:SECTION_CAP]}
    if truncated:
        hit["truncated"] = (f"the section is {len(text)} characters; this is "
                            f"the first {SECTION_CAP}. Read one of its "
                            "subsections for the rest.")
    others = [{"file": os.path.basename(x[2]), "section": x[3]}
              for x in found[1:8]]
    return hit, others
