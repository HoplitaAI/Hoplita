
import math
import re
import unicodedata

_SEARCH_STOPS = {'de', 'la', 'el', 'en', 'y', 'a', 'que', 'es', 'se', 'no', 'un',
                 'una', 'con', 'por', 'para', 'como', 'del', 'las', 'los', 'me',
                 'te', 'lo', 'le', 'mi', 'su', 'al', 'hay', 'tengo', 'tiene',
                 'qué', 'the', 'of', 'to', 'is', 'it', 'in', 'and', 'for'}


def relevance_floor(best, min_abs, rel=0.5):
    return max(min_abs, (best or 0.0) * rel)


_URL_NOISE = {"https", "http", "www", "com", "org", "net", "html", "php", "index"}


def _norm_words(text):
    t = unicodedata.normalize("NFD", (text or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    out = []
    for w in re.findall(r"[\w./-]{2,}", t):
        if w not in _SEARCH_STOPS and w not in _URL_NOISE:
            out.append(w)
        if any(c in w for c in "/.-_"):
            for parte in re.split(r"[/.\-_]+", w):
                if len(parte) >= 2 and parte not in _SEARCH_STOPS \
                        and parte not in _URL_NOISE:
                    out.append(parte)
    return out


_ID_SEPS = "/.-_"


def _same_word(a, b):
    if a == b:
        return True
    if len(a) >= 3 and b.startswith(a):
        return True
    if len(b) >= 3 and a.startswith(b):
        return True
    if len(a) >= 4 and len(b) >= 4 and (a.startswith(b[:4]) or b.startswith(a[:4])):
        return True
    return (len(a) >= 3 and a in b and any(c in b for c in _ID_SEPS))


def group_and_score(query, hits, textof, posof, df_of, total, gap=2, k=5):
    pal = list(dict.fromkeys(_norm_words(query)))
    if not pal or not hits:
        return []
    hay_estadistica = total >= 20
    df = {w: df_of(w) for w in pal}
    present = [w for w in pal if df[w] > 0]
    informative = [w for w in present
                    if not hay_estadistica
                    or math.log(total / max(df[w], 1)) >= 1.0]
    pal = informative or present
    if not pal:
        return []
    idf = {w: math.log((total + 1) / (df[w] + 1)) + 0.1 for w in pal}
    total_idf = sum(idf.values()) or 1.0

    _pal_de = {}

    def _covers(h):
        _id = id(h)
        if _id not in _pal_de:
            _pal_de[_id] = set(_norm_words(textof(h) or ""))
        ws = _pal_de[_id]
        return [w for w in pal if any(_same_word(w, x) for x in ws)]

    matching = [h for h in hits if _covers(h)]
    if not matching:
        return []
    groups, cur = [], None
    for h in sorted(matching, key=posof):
        if cur is not None and posof(h) - posof(cur[-1]) <= gap:
            cur.append(h)
        else:
            cur = [h]
            groups.append(cur)
    out = []
    for g in groups:
        covers = sorted({w for x in g for w in _covers(x)})
        if not covers:
            continue
        score = (sum(idf[w] for w in covers) / total_idf) \
            * (len(covers) / len(pal)) ** 0.5
        out.append({"items": g, "score": round(score, 3),
                    "covers": sorted(covers, key=lambda w: -idf[w]),
                    "from": posof(g[0]), "to": posof(g[-1])})
    out.sort(key=lambda x: (-len(x["covers"]), -x["score"], -x["to"]))
    return out[:k]


def rank_by_words(query, items, textof, k=5):
    items = [i for i in (items or []) if (textof(i) or "").strip()]
    q = _norm_words(query)
    if not q or not items:
        return []
    docs = [_norm_words(textof(i)) for i in items]
    n = len(docs)
    idf = {}
    for term in set(q):
        times = sum(1 for d in docs if any(_same_word(term, w) for w in d))
        idf[term] = math.log((n + 1) / (times + 1)) + 0.1
    out = []
    for it, d in zip(items, docs):
        matching = [t for t in q if any(_same_word(t, w) for w in d)]
        if not matching:
            continue
        score = sum(idf[t] for t in matching) / (sum(idf.values()) or 1.0)
        out.append({**it, "score": round(score, 3), "by": "words",
                    "why": matching})
    if not out:
        return []
    best = max(h["score"] for h in out)
    suelo = relevance_floor(best, 0.05, 0.5)
    out = [h for h in out if h["score"] >= suelo]
    return sorted(out, key=lambda x: -x["score"])[:k]



BLOCK_CHARS = 700
PASSAGE_PIECES = 3


def text_blocks(content, size=BLOCK_CHARS):
    out, cur, start = [], [], None

    def _flush():
        nonlocal cur, start
        if not cur:
            return
        buf, b0 = [], start
        for k, ln in enumerate(cur):
            no = start + k
            while len(ln) > size:
                if buf:
                    out.append((b0, no - 1, "\n".join(buf)))
                    buf = []
                out.append((no, no, ln[:size]))
                ln = ln[size:]
                b0 = no
            if buf and sum(len(x) + 1 for x in buf) + len(ln) > size:
                out.append((b0, no - 1, "\n".join(buf)))
                buf = []
            if not buf:
                b0 = no
            if ln:
                buf.append(ln)
        if buf:
            out.append((b0, start + len(cur) - 1, "\n".join(buf)))
        cur, start = [], None

    for no, ln in enumerate((content or "").split("\n"), 1):
        if ln.strip():
            if start is None:
                start = no
            cur.append(ln)
        else:
            _flush()
    _flush()
    return out


def find_passages(query, docs, regex=False, context=1, k=5, budget=None,
                  pieces=PASSAGE_PIECES):
    query = (query or "").strip()
    if not query or not docs:
        return {"passages": [], "total": 0}
    if not regex:
        _stems = [w[:4] for w in dict.fromkeys(_norm_words(query))]
        if not _stems:
            return {"passages": [], "total": 0}

        def _plain(t):
            t = unicodedata.normalize("NFD", (t or "").lower())
            return "".join(c for c in t if unicodedata.category(c) != "Mn")
        docs = [d for d in docs
                if any(st in _plain(d["text"] if d.get("blocks") is None else
                                    " ".join(x[2] for x in d["blocks"]))
                       for st in _stems)]
    items = []
    for d in docs:
        blocks = d.get("blocks")
        if blocks is None:
            blocks = text_blocks(d.get("text", ""))
        for (a, b, txt) in blocks:
            if txt and txt.strip():
                items.append({"i": len(items), "doc": d, "from": a, "to": b,
                              "text": txt})
    groups = []
    if regex:
        try:
            rx = re.compile(query, re.I)
        except re.error:
            rx = re.compile(re.escape(query), re.I)
        for it in items:
            if not rx.search(it["text"]):
                continue
            last = groups[-1]["items"][-1] if groups else None
            if (last is not None and last["i"] == it["i"] - 1
                    and last["doc"] is it["doc"]
                    and len(groups[-1]["items"]) < pieces):
                groups[-1]["items"].append(it)
            else:
                groups.append({"items": [it], "covers": [query], "score": 1.0})
    else:
        q = list(dict.fromkeys(_norm_words(query)))
        if not q:
            return {"passages": [], "total": 0}
        words = {}

        def _ws(it):
            if it["i"] not in words:
                words[it["i"]] = set(_norm_words(it["text"]))
            return words[it["i"]]

        df_cache = {}

        def _df(w):
            if w not in df_cache:
                df_cache[w] = sum(1 for it in items
                                  if any(_same_word(w, x) for x in _ws(it)))
            return df_cache[w]
        by_doc = {}
        for it in items:
            by_doc.setdefault(id(it["doc"]), []).append(it)
        for mine in by_doc.values():
            for g in group_and_score(query, mine, lambda x: x["text"],
                                     lambda x: x["i"], _df, len(items),
                                     gap=1, k=len(mine)):
                for j in range(0, len(g["items"]), pieces):
                    groups.append({**g, "items": g["items"][j:j + pieces]})
        groups.sort(key=lambda g: (-len(g["covers"]), -g["score"]))
    total = len(groups)
    out, used = [], 0
    for g in groups[:k]:
        first, last = g["items"][0]["i"], g["items"][-1]["i"]
        doc = g["items"][0]["doc"]
        span = [it for it in items[max(0, first - context):last + context + 1]
                if it["doc"] is doc]
        text = "\n…\n".join(x["text"] for x in span)
        if budget is not None:
            if out and used + len(text) > budget:
                break
            if len(text) > budget:
                text = text[:budget] + " …"
        used += len(text)
        out.append({"doc": doc, "from": span[0]["from"], "to": span[-1]["to"],
                    "matched": list(g.get("covers", [])),
                    "score": g.get("score", 0), "text": text,
                    "pieces": [x["from"] for x in g["items"]]})
    return {"passages": out, "total": total}
