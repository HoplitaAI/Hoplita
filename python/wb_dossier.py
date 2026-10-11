#!/usr/bin/env python3
import ast
import os
import re
import subprocess
from collections import Counter

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
             "target", ".tox", ".mypy_cache", ".pytest_cache", "vendor", "third_party",
             ".idea", ".vscode", "site-packages", "coverage", ".next", ".cache"}
CODE_EXT = {".py": "python", ".js": "javascript", ".mjs": "javascript", ".jsx": "javascript",
            ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".rs": "rust",
            ".java": "java", ".kt": "kotlin", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp",
            ".hpp": "cpp", ".cs": "csharp", ".rb": "ruby", ".php": "php", ".swift": "swift",
            ".scala": "scala", ".sh": "shell", ".lua": "lua", ".ex": "elixir", ".exs": "elixir"}
MANIFESTS = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "package.json",
             "go.mod", "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts",
             "Gemfile", "composer.json", "Makefile", "CMakeLists.txt", "Dockerfile",
             "docker-compose.yml", "manage.py", "mix.exs")
ENTRY_NAMES = {"main.py", "__main__.py", "app.py", "server.py", "cli.py", "manage.py", "wsgi.py",
               "asgi.py", "main.go", "main.rs", "index.js", "index.ts", "server.js", "app.js",
               "main.c", "main.cpp", "Program.cs"}
MAX_FILES = 20000
MAX_PARSE = 1500
MAX_BYTES = 400_000


def _git(root, args, timeout=30):
    try:
        p = subprocess.run(["git", "-C", root] + args, capture_output=True, text=True,
                           timeout=timeout, errors="replace")
        return p.stdout if p.returncode == 0 else ""
    except Exception:
        return ""


def _files(root, subpath=""):
    base = os.path.join(root, subpath) if subpath else root
    out = []
    listed = _git(root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"]
                  + ([subpath] if subpath else []))
    if listed:
        for rel in listed.split("\0"):
            if rel and not any(seg in SKIP_DIRS for seg in rel.split("/")[:-1]):
                out.append(rel)
                if len(out) >= MAX_FILES:
                    break
        return out
    for d, dirs, fs in os.walk(base):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith("."))
        for f in sorted(fs):
            out.append(os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/"))
            if len(out) >= MAX_FILES:
                return out
    return out


def _read(root, rel, limit=MAX_BYTES):
    try:
        with open(os.path.join(root, rel), "rb") as f:
            return f.read(limit).decode("utf-8", "replace")
    except OSError:
        return ""


def _tail(root, rel, n=24000):
    try:
        with open(os.path.join(root, rel), "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - n))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _count_lines(root, rel):
    try:
        with open(os.path.join(root, rel), "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0



def _py_module_index(files):
    idx = {}
    for rel in files:
        if not rel.endswith(".py"):
            continue
        parts = rel[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        for i in range(len(parts)):
            idx.setdefault(".".join(parts[i:]), rel)
    return idx


def _py_imports(text):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return re.findall(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", text, re.M) and \
            [a or b for a, b in re.findall(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", text, re.M)]
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
            out += [f"{node.module}.{a.name}" for a in node.names]
    return out


_JS_IMPORT = re.compile(r"""(?:import\s[^'"]*?from\s*|import\s*\(|require\s*\()\s*['"](\.[^'"]+)['"]""")
_GO_IMPORT = re.compile(r'"([\w./-]+)"')


def _resolve_rel(rel, target, files_set):
    base = os.path.normpath(os.path.join(os.path.dirname(rel), target)).replace(os.sep, "/")
    for cand in (base, base + ".js", base + ".ts", base + ".tsx", base + ".jsx", base + ".mjs",
                 base + "/index.js", base + "/index.ts"):
        if cand in files_set:
            return cand
    return None


def import_graph(root, files):
    files_set = set(files)
    pyidx = _py_module_index(files)
    go_mod = ""
    gm = _read(root, "go.mod", 4000)
    m = re.search(r"^module\s+(\S+)", gm, re.M)
    if m:
        go_mod = m.group(1)
    go_dirs = {}
    for rel in files:
        if rel.endswith(".go"):
            go_dirs.setdefault(os.path.dirname(rel), []).append(rel)
    indeg = Counter()
    parsed = 0
    for rel in files:
        ext = os.path.splitext(rel)[1]
        if ext not in (".py", ".js", ".mjs", ".jsx", ".ts", ".tsx", ".go"):
            continue
        if parsed >= MAX_PARSE:
            break
        parsed += 1
        text = _read(root, rel)
        targets = set()
        if ext == ".py":
            for name in _py_imports(text) or []:
                t = pyidx.get(name) or pyidx.get(name.rsplit(".", 1)[0])
                if t and t != rel:
                    targets.add(t)
        elif ext == ".go":
            for imp in _GO_IMPORT.findall(text):
                if go_mod and imp.startswith(go_mod + "/"):
                    d = imp[len(go_mod) + 1:]
                    for t in go_dirs.get(d, [])[:1]:
                        targets.add(t)
        else:
            for imp in _JS_IMPORT.findall(text):
                t = _resolve_rel(rel, imp, files_set)
                if t and t != rel:
                    targets.add(t)
        for t in targets:
            indeg[t] += 1
    return indeg



_DEF_RE = {
    "go": re.compile(r"^func\s+(?:\([^)]*\)\s*)?(\w+)|^type\s+(\w+)", re.M),
    "javascript": re.compile(r"^(?:export\s+)?(?:async\s+)?(?:function\s+(\w+)|class\s+(\w+)|const\s+(\w+)\s*=\s*(?:async\s*)?\()", re.M),
    "typescript": re.compile(r"^(?:export\s+)?(?:async\s+)?(?:function\s+(\w+)|class\s+(\w+)|interface\s+(\w+)|const\s+(\w+)\s*=)", re.M),
    "rust": re.compile(r"^\s*(?:pub\s+)?(?:fn|struct|enum|trait)\s+(\w+)", re.M),
    "java": re.compile(r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:class|interface|[\w<>\[\]]+)\s+(\w+)\s*[({]", re.M),
}


def outline(root, rel, limit=40):
    text = _read(root, rel)
    lang = CODE_EXT.get(os.path.splitext(rel)[1], "")
    names = []
    if lang == "python":
        try:
            tree = ast.parse(text)
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.append(f"def {node.name}")
                elif isinstance(node, ast.ClassDef):
                    meths = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                             and not n.name.startswith("__")][:8]
                    names.append(f"class {node.name}" + (f" ({', '.join(meths)})" if meths else ""))
        except (SyntaxError, ValueError):
            names = [f"def {n}" for n in re.findall(r"^def\s+(\w+)", text, re.M)]
    elif lang in _DEF_RE:
        for m in _DEF_RE[lang].finditer(text):
            n = next((g for g in m.groups() if g), None)
            if n:
                names.append(n)
    return names[:limit]



def _manifest_digest(root, rel):
    text = _read(root, rel, 20000)
    name = os.path.basename(rel)
    if name == "package.json":
        import json
        try:
            j = json.loads(text)
            bits = [f"name={j.get('name')}", f"description={j.get('description')}"]
            if j.get("bin"):
                bits.append(f"bin={j['bin']}")
            if j.get("scripts"):
                bits.append("scripts=" + ", ".join(list(j["scripts"])[:10]))
            bits.append("deps=" + ", ".join(list((j.get("dependencies") or {}))[:15]))
            return "; ".join(bits)
        except Exception:
            pass
    if name == "requirements.txt":
        return "deps=" + ", ".join(re.split(r"[<>=~!\[; ]", ln)[0] for ln in text.splitlines()
                                  if ln.strip() and not ln.startswith("#"))[:400]
    if name == "Makefile":
        return "targets=" + ", ".join(re.findall(r"^([\w.-]+):", text, re.M)[:15])
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    return " | ".join(lines[:12])[:500]


def build(root, subpath=""):
    root = os.path.realpath(root)
    subpath = (subpath or "").strip("/")
    files = _files(root, subpath)
    code = [f for f in files if os.path.splitext(f)[1] in CODE_EXT]
    lang = Counter(CODE_EXT[os.path.splitext(f)[1]] for f in code)

    depth = len(subpath.split("/")) if subpath else 0
    dirs = Counter()
    for f in files:
        parts = f.split("/")
        dirs[parts[depth] if len(parts) > depth + 1 else "."] += 1

    sizes = {}
    for f in code[:4000]:
        try:
            sizes[f] = os.path.getsize(os.path.join(root, f))
        except OSError:
            pass

    indeg = import_graph(root, code)
    central = [f for f, _n in indeg.most_common(15)]
    entries = [f for f in code if os.path.basename(f) in ENTRY_NAMES]
    main_guard = re.compile(r"if\s+__name__\s*==\s*['\"]__main__['\"]")
    for f in code[:MAX_PARSE]:
        if f.endswith(".py") and f not in entries and main_guard.search(_tail(root, f)):
            entries.append(f)
    _is_test = re.compile(r"(^|/)(tests?|spec|__tests__)/|(^|/)test_|_test\.|\.spec\.|\.test\.")
    entries = [e for e in entries if not _is_test.search(e)][:12]
    churn = Counter()
    for ln in _git(root, ["log", "-n", "400", "--name-only", "--pretty=format:"] + ([subpath] if subpath else [])).splitlines():
        ln = ln.strip()
        if ln and ln in sizes:
            churn[ln] += 1
    active = [f for f, _n in churn.most_common(12)]
    tests = [f for f in files if re.search(r"(^|/)(tests?|spec|__tests__)/|(^|/)test_|_test\.|\.spec\.|\.test\.", f)]
    readme = next((f for f in files if re.match(r"(?i)readme(\.\w+)?$", os.path.basename(f))
                   and f.count("/") == depth), None)
    manifests = [f for f in files if os.path.basename(f) in MANIFESTS and f.count("/") <= depth + 1][:8]
    log = _git(root, ["log", "-n", "8", "--pretty=format:%h %s"])

    out = []
    title = os.path.basename(root) + (f"/{subpath}" if subpath else "")
    out.append(f"# Dossier of {title}")
    out.append(f"{len(files)} files ({len(code)} source); languages: "
               + ", ".join(f"{k} {v}" for k, v in lang.most_common(6)))
    if readme:
        head = _read(root, readme, 6000).strip()
        out.append(f"\n## {readme} (head)\n" + "\n".join(head.splitlines()[:40])[:3000])
    if manifests:
        out.append("\n## Manifests")
        out += [f"- {m}: {_manifest_digest(root, m)}" for m in manifests]
    out.append("\n## Layout (top level: files)")
    out.append(", ".join(f"{d}/ {n}" if d != "." else f"(root) {n}" for d, n in dirs.most_common(25)))
    if entries:
        out.append("\n## Entry points\n" + "\n".join(f"- {e}" for e in entries))
    if central:
        out.append("\n## Core: the modules the rest imports most (file: importers)")
        for f in central:
            out.append(f"- {f}: {indeg[f]} · {sizes.get(f, 0) // 1024} KB · "
                       + ", ".join(outline(root, f, 14)))
    if active:
        out.append("\n## Most changed recently\n" + ", ".join(active))
    big = sorted(sizes, key=lambda f: -sizes[f])[:8]
    if big:
        out.append("\n## Largest source files\n" + ", ".join(f"{f} ({sizes[f] // 1024} KB)" for f in big))
    if tests:
        out.append(f"\n## Tests ({len(tests)})\n" + ", ".join(tests[:25]))
    if log:
        out.append("\n## Recent commits\n" + log)
    text = "\n".join(out)
    return {"text": text[:14000], "files": files, "code": code, "central": central,
            "entries": entries, "active": active, "tests": tests, "root": root,
            "subpath": subpath, "size": len(code)}
