#!/usr/bin/env python3
import concurrent.futures as _cf
import datetime as _dt
import hashlib
import json

import wb_store as S
import wb_vcs as V
from wb_pipeline import Board, PipelineError, _drop, _safe_insert, create_board

PERIOD_BUDGET = 24000
MAX_KEY = 4
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _git(root, args):
    return V._git(root, args)


def _month_end(ym):
    y, m = (int(x) for x in ym.split("-"))
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return f"{y:04d}-{m:02d}-01"


def _valid_month(s):
    try:
        _dt.date.fromisoformat(f"{s}-01")
        return True
    except ValueError:
        return False


def commits(root, since="", until=""):
    for v in (since, until):
        if v and not _valid_month(v):
            raise PipelineError(f"bad_month: {v} (YYYY-MM)")
    args = ["log", "--reverse", "--no-merges", "--date=iso-strict",
            "--format=%x1e%H%x00%aI%x00%s%x00%b%x00", "--name-only"]
    if since:
        args.append(f"--since={since}-01T00:00:00")
    if until:
        args.append(f"--until={_month_end(until)}T00:00:00")
    raw = _git(root, args + ["HEAD"])
    out = []
    for rec in raw.split("\x1e"):
        if not rec.strip():
            continue
        f = rec.split("\x00")
        if len(f) < 5:
            continue
        sha, when, subj, body, names = f[0].strip(), f[1], f[2], f[3], f[4]
        areas = []
        for n in names.split("\n"):
            n = n.strip()
            if n:
                a = n.split("/", 1)[0] if "/" in n else n
                if a not in areas:
                    areas.append(a)
        out.append({"sha": sha, "date": when[:10], "subject": subj.strip(),
                    "body": " ".join(body.split())[:240], "areas": areas[:5]})
    return out


def grain(first, last):
    d0, d1 = _dt.date.fromisoformat(first), _dt.date.fromisoformat(last)
    days = (d1 - d0).days
    return "week" if days <= 45 else "month" if days <= 548 else "quarter"


def period_of(date, g):
    d = _dt.date.fromisoformat(date)
    if g == "week":
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"
    if g == "month":
        return f"{d.year}-{d.month:02d}"
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def label_of(key, g):
    if g == "week":
        y, w = key.split("-W")
        mon = _dt.date.fromisocalendar(int(y), int(w), 1)
        sun = mon + _dt.timedelta(days=6)
        return (f"{mon.day}–{sun.day} {_MONTHS[sun.month - 1]} {sun.year}" if mon.month == sun.month
                else f"{mon.day} {_MONTHS[mon.month - 1]} – {sun.day} {_MONTHS[sun.month - 1]} {sun.year}")
    if g == "month":
        y, m = key.split("-")
        return f"{_MONTHS[int(m) - 1]} {y}"
    y, q = key.split("-Q")
    return f"Q{q} {y}"


def _lines(cs):
    out = []
    for c in cs:
        ln = f"- {c['subject'][:300]} [{c['sha'][:8]}]"
        if c["areas"]:
            ln += f" ({', '.join(c['areas'])})"
        if c["body"] and c["body"] != c["subject"]:
            ln += f"\n  {c['body']}"
        out.append(ln)
    return out


def _chunks(cs):
    pieces, cur, size = [], [], 0
    for c, ln in zip(cs, _lines(cs)):
        if cur and size + len(ln) > PERIOD_BUDGET:
            pieces.append(cur)
            cur, size = [], 0
        cur.append(c)
        size += len(ln) + 1
    if cur:
        pieces.append(cur)
    return pieces


_TELL = """Commits of the project «{name}» in {label}, oldest first. Each line: the commit's
subject [short sha] (top-level areas it touched), and under it the start of its message.
{note}
{commits}

Write in {lang}. Answer ONLY with JSON:
{{"headline": "this period in 2 to 6 words: what moved most, without naming the period",
  "story": "3 to 5 sentences: what was built, changed or fixed and why, the big things first. Not a list of commits",
  "key": ["the short shas of up to {k} commits that best show it"]}}"""

_JOIN = """These are tellings of consecutive parts of {label} in the project «{name}».
{parts}

Tell the whole of {label} as one, in {lang}. Answer ONLY with JSON:
{{"headline": "the period in 2 to 6 words", "story": "3 to 5 sentences, the big things first",
  "key": ["up to {k} short shas, taken from the parts' key commits"]}}"""

_ARC = """The history of the project «{name}», period by period:
{periods}

Write in {lang}. Answer ONLY with JSON:
{{"title": "a title for this history, at most 8 words",
  "story": "4 to 6 sentences: the arc of the project over these periods, where it started, what it became"}}"""

_SYSTEM = "You tell the history of software projects from their commits. Terse, factual, JSON only."


def _fit(text, n):
    text = " ".join(str(text or "").split())
    if len(text) <= n:
        return text
    cut = text[:n - 1].rsplit(" ", 1)[0].rstrip(" ,;:·—-")
    return (cut or text[:n - 1]) + "…"


def _unlabel(headline, label):
    h = headline.strip()
    if label and h.lower().startswith(label.lower()):
        h = h[len(label):].lstrip(" :·—–-,")
    return h


def _clean(r, cs):
    r = r if isinstance(r, dict) else {}
    known = {c["sha"][:8]: c for c in cs}
    key = []
    for k in r.get("key") or []:
        k = str(k).strip()[:8]
        if k in known and k not in key:
            key.append(k)
    return {"headline": " ".join(str(r.get("headline") or "").split())[:60],
            "story": str(r.get("story") or "").strip(),
            "key": key[:MAX_KEY]}


def tell(ctx, name, label, cs):
    pieces = _chunks(cs)
    if len(pieces) == 1:
        r = _clean(ctx.json_call(_TELL.format(name=name, label=label, note="", lang=ctx.lang, k=MAX_KEY,
                                              commits="\n".join(_lines(cs))), _SYSTEM), cs)
        r["headline"] = _unlabel(r["headline"], label)
        return r
    parts = []
    for i, p in enumerate(pieces):
        if ctx.cancelled():
            raise PipelineError("cancelled")
        note = f"(Part {i + 1} of {len(pieces)} of this period.)"
        parts.append(_clean(ctx.json_call(_TELL.format(name=name, label=label, note=note, lang=ctx.lang,
                                                       k=MAX_KEY, commits="\n".join(_lines(p))), _SYSTEM), p))
    text = "\n".join(f"Part {i + 1}: {p['headline']}. {p['story']} Key: {', '.join(p['key'])}"
                     for i, p in enumerate(parts))
    joined = _clean(ctx.json_call(_JOIN.format(name=name, label=label, parts=text, lang=ctx.lang, k=MAX_KEY),
                                  _SYSTEM), cs)
    if not joined["key"]:
        joined["key"] = [k for p in parts for k in p["key"]][:MAX_KEY]
    joined["headline"] = _unlabel(joined["headline"], label)
    return joined


def _cache_key(root, g, period, lang):
    raw = json.dumps(["history", root, g, period, lang], ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()


def _fingerprint(cs):
    return hashlib.sha1("".join(c["sha"] for c in cs).encode()).hexdigest()


def range_head(root, since="", until=""):
    cs = commits(root, since, until)
    return cs[-1]["sha"] if cs else ""


def draw(job, ctx, project_of=None, wid=None, remember=None):
    root = job["root"]
    since, until = job.get("since") or "", job.get("until") or ""
    ctx.progress("mapping")
    cs = commits(root, since, until)
    if not cs:
        raise PipelineError("no_history")
    g = grain(cs[0]["date"], cs[-1]["date"])
    periods = {}
    for c in cs:
        periods.setdefault(period_of(c["date"], g), []).append(c)
    keys = list(periods)
    name = root.rstrip("/\\").rsplit("/", 1)[-1]
    first, last = cs[0]["sha"], cs[-1]["sha"]
    try:
        base = V.resolve(root, first + "~1")
    except Exception:
        base = first
    redraw = bool(wid)
    remember = (not redraw) if remember is None else remember
    if not redraw:
        wid = create_board(root, {"kind": "commits", "head": last, "base": base}, name, project_of)
        S.store().plan_put(wid, {"kind": "history", "since": since, "until": until, "parts": []})
        job["whiteboardId"] = wid
    board = Board(wid)
    board.begin(ctx.focus("planning"))
    st = S.store()
    told = {}
    try:
        cache = {k: (_cache_key(root, g, k, ctx.lang), _fingerprint(periods[k])) for k in keys}
        remembered = []
        for k in keys:
            hit = st.brief_get(*cache[k]) if remember else None
            if hit:
                told[k] = hit
                remembered.append(k)

        def one(k):
            if ctx.cancelled():
                return k, None
            return k, tell(ctx, name, label_of(k, g), periods[k])

        ctx.progress("history", n=len(told), of=len(keys))
        with _cf.ThreadPoolExecutor(max_workers=ctx.parallel) as ex:
            for k, r in ex.map(one, [k for k in keys if k not in told]):
                told[k] = r or {"headline": "", "story": "", "key": []}
                if told[k]["headline"] or told[k]["story"]:
                    st.brief_put(*cache[k], told[k])
                ctx.progress("history", n=len(told), of=len(keys))
        untold = [k for k in keys if not (told[k]["headline"] or told[k]["story"])]
        job["explored"] = {"periods": len(keys), "cached": len(remembered), "remembered": remembered,
                           "untold": untold, "commits": len(cs), "grain": g}
        if ctx.cancelled():
            raise PipelineError("cancelled")
        if not any(t["story"] for t in told.values()):
            raise PipelineError("no_answers")
        ctx.progress("writing")
        board.focus(ctx.focus("writing"))
        if redraw:
            for b in board.snap().get("document") or []:
                board.remove(b["id"])
            board.cmd({"type": "set_target", "target": {"kind": "commits", "head": last, "base": base,
                                                        "repositoryId": board.snap()["pins"]["repositoryId"]}})
        arc = ctx.json_call(_ARC.format(name=name, lang=ctx.lang, periods="\n".join(
            f"- {label_of(k, g)}: {told[k]['headline']}. {told[k]['story']}" for k in keys)), _SYSTEM)
        arc = arc if isinstance(arc, dict) else {}
        span = f"{label_of(keys[0], g)} – {label_of(keys[-1], g)}" if len(keys) > 1 else label_of(keys[0], g)
        title = " ".join(str(arc.get("title") or "").split())[:90] or name
        board.cmd({"type": "rename", "title": f"{title} · {span}"[:120]})
        intro = str(arc.get("story") or "").strip()
        _safe_insert(board, {"type": "markdown", "markdown":
                             (intro + "\n\n" if intro else "") + f"*{span} · {len(cs)} commits*"})
        nodes, edges = [], []
        for i, k in enumerate(keys):
            head = told[k]["headline"] or f"{len(periods[k])} commits"
            nodes.append({"key": f"p{i}", "label": _fit(f"{label_of(k, g)} · {head}", 56),
                          "kind": "terminal" if i in (0, len(keys) - 1) else "process"})
            if i:
                edges.append({"from": f"p{i - 1}", "to": f"p{i}"})
        if len(nodes) > 1:
            _safe_insert(board, {"type": "flow_diagram", "title": span[:80], "nodes": nodes, "edges": edges})
        by = {c["sha"][:8]: c for c in cs}
        for k in keys:
            t_ = told[k]
            sec = board.insert({"type": "section", "children": [],
                                "title": _fit(f"{label_of(k, g)} — {t_['headline'] or str(len(periods[k])) + ' commits'}", 120)}
                               )["targetId"]
            md = t_["story"] or "—"
            md += f"\n\n*{len(periods[k])} commit{'' if len(periods[k]) == 1 else 's'}.*"
            if t_["key"]:
                md += "\n\n" + "\n".join(f"- `{s}` {by[s]['subject'][:140]}" for s in t_["key"] if s in by)
            _safe_insert(board, {"type": "markdown", "markdown": md}, parent=sec)
        st.plan_put(wid, {"kind": "history", "since": since, "until": until, "grain": g,
                          "head": last, "parts": []})
    except PipelineError:
        if not redraw:
            _drop(board, job)
        raise
    finally:
        try:
            board.end()
        except Exception:
            pass
    return wid


def redraw(job, ctx):
    plan = S.store().plan_get(job["whiteboardId"]) or {}
    job["since"], job["until"] = plan.get("since") or "", plan.get("until") or ""
    return draw(job, ctx, wid=job["whiteboardId"])


def update(job, ctx):
    plan = S.store().plan_get(job["whiteboardId"]) or {}
    job["since"], job["until"] = plan.get("since") or "", plan.get("until") or ""
    return draw(job, ctx, wid=job["whiteboardId"], remember=True)


def staleness(root, plan):
    try:
        now = range_head(root, plan.get("since") or "", plan.get("until") or "")
    except Exception:
        now = ""
    told = plan.get("head") or ""
    stale = bool(now and told and now != told)
    return {"kind": "history", "updatable": True, "stale": stale, "pinned": told, "current": now,
            "ahead": V.ahead_count(root, told, now) if stale else 0}


def periods(root, plan):
    cs = commits(root, plan.get("since") or "", plan.get("until") or "")
    head = plan.get("head") or ""
    if head:
        i = next((n for n, c in enumerate(cs) if c["sha"] == head), None)
        cs = cs[:i + 1] if i is not None else cs
    if not cs:
        return []
    g = plan.get("grain") or grain(cs[0]["date"], cs[-1]["date"])
    out = {}
    for c in cs:
        k = period_of(c["date"], g)
        out.setdefault(k, {"key": k, "label": label_of(k, g), "commits": []})["commits"].append(
            {"sha": c["sha"], "date": c["date"][:10], "subject": c["subject"]})
    return list(out.values())


def months(root):
    r = _git(root, ["log", "--format=%ad", "--date=format:%Y-%m", "HEAD"])
    return sorted(set(r.split()))
