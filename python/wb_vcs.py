#!/usr/bin/env python3
import json
import os
import re
import shutil
import subprocess
import threading

SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
WORKTREE = "worktree"
_LOCK = threading.Lock()
_BLOB_CACHE = {}
_BLOB_CACHE_MAX = 512


class VcsError(Exception):

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _git(root, args, raw=False, check=True, input_bytes=None, timeout=120):
    p = subprocess.run(["git", "-C", root] + list(args), capture_output=True,
                       input=input_bytes, timeout=timeout)
    if check and p.returncode != 0:
        raise VcsError((p.stderr or b"").decode("utf-8", "replace").strip()
                       or f"git {' '.join(args[:2])} failed")
    return p.stdout if raw else p.stdout.decode("utf-8", "replace")


def repo_root(path):
    p = os.path.realpath(os.path.expanduser(str(path or "")))
    if not os.path.exists(p):
        raise VcsError(f"No such path: {p}", 404)
    start = p if os.path.isdir(p) else os.path.dirname(p)
    try:
        top = _git(start, ["rev-parse", "--show-toplevel"]).strip()
    except VcsError:
        raise VcsError(f"Not a git repository: {p}", 400)
    return os.path.realpath(top)


def resolve(root, rev):
    rev = str(rev or "").strip()
    if not rev:
        raise VcsError("A revision is required.")
    try:
        return _git(root, ["rev-parse", "--verify", "--quiet",
                           rev + "^{commit}"]).strip()
    except VcsError:
        raise VcsError(f"Base or head revision does not exist in the local "
                       f"checkout ({rev}). Fetch the requested commits "
                       f"before authoring.", 404)


def merge_base(root, a, b):
    try:
        return _git(root, ["merge-base", a, b]).strip()
    except VcsError:
        return ""


def head_commit(root):
    try:
        return _git(root, ["rev-parse", "--verify", "--quiet", "HEAD"]).strip()
    except VcsError:
        return ""


def ahead_count(root, old, new):
    if not old or not new:
        return -1
    if old == new:
        return 0
    try:
        _git(root, ["merge-base", "--is-ancestor", old, new])
        return int(_git(root, ["rev-list", "--count", f"{old}..{new}"]).strip() or 0)
    except (VcsError, ValueError):
        return -1


def worktree_fingerprint(root):
    import hashlib
    h = hashlib.sha1(_git(root, ["diff", "HEAD", "--binary"], raw=True, check=False))
    for rel in sorted(_git(root, ["ls-files", "--others", "--exclude-standard", "-z"],
                           check=False).split("\0")):
        if not rel:
            continue
        h.update(rel.encode("utf-8", "replace"))
        try:
            with open(os.path.join(root, rel), "rb") as f:
                h.update(f.read(4 * 1024 * 1024))
        except OSError:
            pass
    return h.hexdigest()


def _rel_ok(path):
    path = str(path or "")
    if (not path or path.startswith("/") or "\\" in path
            or any(seg in (".", "..") for seg in path.split("/"))
            or any(ord(c) < 32 for c in path)):
        raise VcsError("Source file must be a repository-relative path.")
    return path


def read_file(root, commit, path):
    path = _rel_ok(path)
    if commit == WORKTREE:
        full = os.path.realpath(os.path.join(root, path))
        if not full.startswith(os.path.realpath(root) + os.sep):
            raise VcsError("Source file must be a repository-relative path.")
        if not os.path.isfile(full):
            return None
        with open(full, "rb") as f:
            return f.read()
    key = (root, commit, path)
    with _LOCK:
        if key in _BLOB_CACHE:
            return _BLOB_CACHE[key]
    p = subprocess.run(["git", "-C", root, "cat-file", "blob",
                        f"{commit}:{path}"], capture_output=True, timeout=60)
    data = p.stdout if p.returncode == 0 else None
    with _LOCK:
        if len(_BLOB_CACHE) >= _BLOB_CACHE_MAX:
            _BLOB_CACHE.pop(next(iter(_BLOB_CACHE)))
        _BLOB_CACHE[key] = data
    return data


def read_text(root, commit, path):
    data = read_file(root, commit, path)
    if data is None:
        raise VcsError("File is unavailable at the pinned commit.", 404)
    if b"\0" in data:
        raise VcsError("Binary files cannot be used as code references.")
    return data.decode("utf-8", "replace")


def file_lines(text):
    if text == "":
        return []
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def tree(root, commit, directory=""):
    d = str(directory or "").strip().strip("/")
    if d:
        _rel_ok(d)
    if commit == WORKTREE:
        files = _git(root, ["ls-files", "-z", "--cached", "--others",
                            "--exclude-standard"]).split("\0")
    else:
        files = _git(root, ["ls-tree", "-r", "-z", "--name-only",
                            commit]).split("\0")
    prefix = (d + "/") if d else ""
    seen, out = set(), []
    for f in files:
        if not f or not f.startswith(prefix):
            continue
        rest = f[len(prefix):]
        head, sep, _tail = rest.partition("/")
        entry = prefix + head
        if entry in seen:
            continue
        seen.add(entry)
        out.append({"path": entry, "kind": "directory" if sep else "file"})
    out.sort(key=lambda e: (e["kind"] != "directory", e["path"]))
    return out



def _diff_args(base, head):
    return [base] if head == WORKTREE else [base, head]


def changed_files(root, base, head, paths=None):
    if base == head:
        return []
    args = ["diff", "--no-ext-diff", "--no-color", "--no-textconv", "-M",
            "--numstat", "-z"] + _diff_args(base, head) + ["--"] + list(paths or [])
    raw = _git(root, args)
    status = {}
    st = _git(root, ["diff", "--no-ext-diff", "--no-color", "-M",
                     "--name-status", "-z"] + _diff_args(base, head) + ["--"]
              + list(paths or []))
    parts = st.split("\0")
    i = 0
    while i < len(parts):
        code = parts[i]
        if not code:
            i += 1
            continue
        if code[0] in "RC":
            status[parts[i + 2]] = ("renamed" if code[0] == "R" else "copied",
                                    parts[i + 1])
            i += 3
        else:
            status[parts[i + 1]] = ({"A": "added", "D": "deleted",
                                     "M": "modified", "T": "modified"}
                                    .get(code[0], "modified"), None)
            i += 2
    out = []
    toks = raw.split("\0")
    i = 0
    while i < len(toks):
        t = toks[i]
        if not t:
            i += 1
            continue
        fields = t.split("\t")
        add, dele = fields[0], fields[1]
        if len(fields) >= 3 and fields[2]:
            path, prev = fields[2], None
            i += 1
        else:
            prev, path = toks[i + 1], toks[i + 2]
            i += 3
        stt, prev2 = status.get(path, ("modified", None))
        row = {"path": path, "status": stt,
               "additions": int(add) if add.isdigit() else 0,
               "deletions": int(dele) if dele.isdigit() else 0}
        if prev or prev2:
            row["previousPath"] = prev or prev2
        if add == "-":
            row["binary"] = True
        out.append(row)
    return out


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def numbered_patch(root, base, head, paths=None, context=3, max_bytes=40000):
    if base == head:
        return ""
    ctx = max(0, min(50, int(context if context is not None else 3)))
    raw = _git(root, ["diff", "--no-ext-diff", "--no-color", "--no-textconv",
                      "-M", f"--unified={ctx}"] + _diff_args(base, head)
               + ["--"] + list(paths or []))
    files, cur = [], None
    for line in raw.split("\n"):
        if line.startswith("diff --git "):
            cur = {"head": line, "lines": [], "path": line.split(" b/", 1)[-1]}
            files.append(cur)
            continue
        if cur is None:
            continue
        cur["lines"].append(line)
    out_files = []
    for f in files:
        rows, b, h = [f["head"]], 0, 0
        in_hunk = False
        numbered = []
        for line in f["lines"]:
            if line.startswith(("index ", "--- ", "+++ ")) and not in_hunk:
                continue
            m = _HUNK.match(line)
            if m:
                in_hunk = True
                b, h = int(m.group(1)), int(m.group(3))
                numbered.append(line)
                continue
            if not in_hunk:
                numbered.append(line)
                continue
            if line.startswith("+"):
                numbered.append((None, h, line)); h += 1
            elif line.startswith("-"):
                numbered.append((b, None, line)); b += 1
            elif line.startswith(" ") or line == "":
                if line == "":
                    continue
                numbered.append((b, h, line)); b += 1; h += 1
            else:
                numbered.append(line)
        width = max([len(str(x[0] or "")) for x in numbered if isinstance(x, tuple)]
                    + [len(str(x[1] or "")) for x in numbered if isinstance(x, tuple)]
                    + [1])
        for x in numbered:
            if isinstance(x, tuple):
                bn = str(x[0]) if x[0] is not None else ""
                hn = str(x[1]) if x[1] is not None else ""
                rows.append(f"{bn:>{width}} {hn:>{width}} {x[2]}")
            else:
                rows.append(x)
        out_files.append((f["path"], "\n".join(rows) + "\n"))
    budget, taken, rest = int(max_bytes or 40000), [], []
    used = 0
    for i, (path, text) in enumerate(out_files):
        size = len(text.encode("utf-8"))
        if used + size <= budget:
            taken.append(text); used += size
        elif not taken and i == 0:
            cut = text.encode("utf-8")[:budget].decode("utf-8", "ignore")
            taken.append(cut[:cut.rfind("\n") + 1] + "[… truncated at the byte budget]\n")
            used = budget
        else:
            rest.append(path)
    text = "".join(taken)
    if rest:
        text += (f"[{len(rest)} more files over the {budget}-byte budget: "
                 f"{', '.join(rest)}. Fetch them with paths:[…], format:\"patch\".]\n")
    return text


def hunk_ranges(root, base, head, path, previous=None):
    out = {"base": [], "head": []}
    if base == head:
        return out
    paths = [path] + ([previous] if previous and previous != path else [])
    raw = _git(root, ["diff", "--no-ext-diff", "--no-color", "-M",
                      "--unified=0"] + _diff_args(base, head) + ["--"] + paths)
    for line in raw.split("\n"):
        m = _HUNK.match(line)
        if not m:
            continue
        bs, bc = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
        hs, hc = int(m.group(3)), int(m.group(4) if m.group(4) is not None else 1)
        if bc:
            out["base"].append((bs, bs + bc - 1))
        if hc:
            out["head"].append((hs, hs + hc - 1))
    return out


def textual_rows(root, base, head, path, previous=None):
    btext = "" if base is None else (read_file(root, base, previous or path) or b"").decode("utf-8", "replace")
    htext = (read_file(root, head, path) or b"").decode("utf-8", "replace")
    nb, nh = len(btext.split("\n")), len(htext.split("\n"))
    rows, b, h = [], 0, 0
    if base != head:
        paths = [path] + ([previous] if previous and previous != path else [])
        raw = _git(root, ["diff", "--no-ext-diff", "--no-color", "-M",
                          "--unified=0"] + _diff_args(base, head) + ["--"] + paths)
        for line in raw.split("\n"):
            m = _HUNK.match(line)
            if not m:
                continue
            bs, bc = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
            hs, hc = int(m.group(3)), int(m.group(4) if m.group(4) is not None else 1)
            b0 = bs - 1 if bc else bs
            h0 = hs - 1 if hc else hs
            while b < b0 and h < h0:
                rows.append((b, h)); b += 1; h += 1
            for k in range(bc):
                rows.append((b, None)); b += 1
            for k in range(hc):
                rows.append((None, h)); h += 1
    while b < nb or h < nh:
        rows.append((b if b < nb else None, h if h < nh else None))
        b += 1; h += 1
    return rows


def commits(root, base, head):
    if head == WORKTREE or base == head:
        return []
    raw = _git(root, ["log", "--reverse", "--topo-order",
                      "--format=%H%x00%P%x00%an%x00%aI%x00%s%x1e",
                      f"{base}..{head}"])
    out = []
    for rec in raw.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        c, parents, an, at, subj = (rec.split("\0") + [""] * 5)[:5]
        out.append({"commit": c, "parent": (parents.split() or [""])[0],
                    "subject": subj, "author": an, "authoredAt": at})
    return out



def diffr_binary():
    cand = [os.environ.get("HOPLITA_DIFFR", "")]
    home = os.environ.get("HOPLITA_HOME", "")
    if home:
        cand.append(os.path.join(home, "bin", "diffr.exe" if os.name == "nt" else "diffr"))
    cand.append(shutil.which("diffr") or "")
    for c in cand:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def structural_diff(root, base, head, paths=None, timeout=180):
    exe = diffr_binary()
    if not exe or head == WORKTREE or base == head:
        return None
    args = [exe, "--repo", root, "--format", "ndjson", base, head, "--"] + list(paths or [])
    try:
        p = subprocess.run(args, capture_output=True, cwd=root, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode not in (0, 2):
        return None
    events = []
    for line in p.stdout.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except ValueError:
                return None
    if not events or events[0].get("type") != "start":
        return None
    return events


def _leaf_span(region):
    s, e = region["start"], region["end"]
    return s["line"], (e["line"] if e.get("column", 0) == 0 else e["line"] + 1)


def regions_of(side):
    side = side or {}
    if side.get("regions") is not None:
        return side.get("regions") or []
    return (side.get("root") or {}).get("children") or []


def _leaves(regions, out=None):
    out = [] if out is None else out
    for r in regions or []:
        if r.get("kind") == "leaf":
            out.append(r)
        else:
            _leaves(r.get("children"), out)
    return out


def structural_rows(diff):
    lhs = (diff.get("lhs") or {})
    rhs = (diff.get("rhs") or {})
    lleaves, rleaves = _leaves(regions_of(lhs)), _leaves(regions_of(rhs))
    nl = len((lhs.get("text") or "").split("\n")) if "lhs" in diff else 0
    nr = len((rhs.get("text") or "").split("\n")) if "rhs" in diff else 0
    rpos = {r["alignment_id"]: i for i, r in enumerate(rleaves)}
    rows, ri = [], 0
    for leaf in lleaves:
        a0, a1 = _leaf_span(leaf)
        j = rpos.get(leaf["alignment_id"])
        if j is None or j < ri:
            rows.extend((k, None) for k in range(a0, a1))
            continue
        while ri < j:
            b0, b1 = _leaf_span(rleaves[ri])
            rows.extend((None, k) for k in range(b0, b1))
            ri += 1
        b0, _b1 = _leaf_span(rleaves[j])
        rows.extend((a0 + k, b0 + k) for k in range(a1 - a0))
        ri = j + 1
    while ri < len(rleaves):
        b0, b1 = _leaf_span(rleaves[ri])
        rows.extend((None, k) for k in range(b0, b1))
        ri += 1
    left = max([r[0] for r in rows if r[0] is not None] + [-1]) + 1
    right = max([r[1] for r in rows if r[1] is not None] + [-1]) + 1
    while left < nl or right < nr:
        rows.append((left if left < nl else None, right if right < nr else None))
        left += 1; right += 1
    return rows
