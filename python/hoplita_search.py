
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
