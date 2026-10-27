#!/usr/bin/env python3

import os, sys, json, time, platform, subprocess, base64, re, hashlib, getpass, uuid
from collections import OrderedDict
import asyncio
import threading
import configparser
import shutil
import mimetypes
from datetime import datetime, timedelta

import requests
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
import fed_tools
import hoplita_children
from hoplita_search import (rank_by_words, _norm_words,
                            _SEARCH_STOPS)
from hoplita_prompts import (_BROWSE_SYSTEM_HINTS, _CURATE_PROMPT,
                             _FACT_DEDUP_PROMPT, _LESSON_MERGE_PROMPT,
                             _PICK_SKILL_PROMPT, _PRUNE_PROMPT,
                             _FACT_GATE_PROMPT)

IS_WINDOWS = platform.system() == "Windows"


def hoplita_base():
    import hoplita_paths
    return hoplita_paths.hoplita_base()


def config_path():
    import hoplita_paths
    return hoplita_paths.config_path()


def load_config():
    import hoplita_llmcfg
    p = config_path()
    cfg, problem = hoplita_llmcfg.read_config(p, restore=False)
    if os.path.exists(p):
        print(f"[MCP] Config: {p}")
        if problem:
            print(f"[MCP] ⚠ config.ini is not well formed ({problem})", file=sys.stderr)
    else:
        print(f"[MCP] ⚠ Config not found: {p}")
    return _tolerant(cfg)


def _tolerant(cfg):
    _raw = cfg.getboolean

    def _getboolean(section, option, *, raw=False, vars=None, fallback=configparser._UNSET):
        try:
            return _raw(section, option, raw=raw, vars=vars, fallback=fallback)
        except ValueError:
            val = cfg.get(section, option, fallback="")
            print(f"[CONFIG] [{section}] {option} = {val!r} is not a yes/no value; "
                  f"using {fallback if fallback is not configparser._UNSET else False}",
                  file=sys.stderr)
            return False if fallback is configparser._UNSET else fallback
    cfg.getboolean = _getboolean
    return cfg


_HTTP_LOCAL = threading.local()


def _user_agent():
    try:
        import hoplita_version as _hv
        return f"HOPLITA/{_hv.GENERATION} (build {_hv.BUILD})"
    except Exception:
        return "HOPLITA"


def _http():
    s = getattr(_HTTP_LOCAL, "session", None)
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = _user_agent()
        adapter = requests.adapters.HTTPAdapter(pool_connections=4,
                                                pool_maxsize=8, max_retries=0)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        _HTTP_LOCAL.session = s
    return s


def _council_available(is_blocked=None, allowed=None):
    if "call_council" not in TOOLS or len(_council_pool()) < 2:
        return False
    if is_blocked is not None and is_blocked("call_council"):
        return False
    return allowed is None or "call_council" in allowed


def _build_escalator(current_ep):
    if not CONFIG or not CONFIG.getboolean("CHAT", "escalate_on_stall",
                                           fallback=True):
        return None
    pool = _model_pool()
    if not pool:
        return None
    want = (CONFIG.get("CHAT", "escalate_model", fallback="") or "").strip()
    cur_model = (current_ep or {}).get("model", "")

    def _pick():
        if want and want.lower() not in ("auto", "router"):
            return pool.get(want)
        try:
            names = [n for n, m in pool.items() if m.get("model") != cur_model]
            if not names:
                return None
            if len(names) == 1:
                return pool[names[0]]
            desc = "\n".join(f"- {n}: {pool[n].get('profile', '')[:200]}"
                              for n in names)
            ans = _llm_call_json(
                "A task has DEFEATED the current model: it kept trying the "
                "same paths with no new data. Pick the ONE model from this "
                "list most likely to succeed where it failed. Answer only "
                f"{{\"model\": \"<name>\"}}.\n\n{desc}",
                max_tokens=40)
            pick = str((ans or {}).get("model", "")).strip()
            return pool.get(pick) or pool[names[0]]
        except Exception:
            return None

    def _escalate():
        m = _pick()
        if not m or m.get("model") == cur_model:
            return None
        label = m.get("model") or "?"

        def _chat(messages, tools):
            return _llm_chat_with_tools(
                messages, tools, base_url=m.get("base_url"),
                api_key=m.get("api_key"), model=m.get("model"))

        return _chat, label

    return _escalate


def _merge_extra_body(payload, base_url, model):
    try:
        import hoplita_llmcfg
        return hoplita_llmcfg.merge_extra_body(
            CONFIG, payload, base_url, model,
            warn=lambda m: print(f"[CONFIG] {m}", flush=True))
    except Exception as e:
        print(f"[CONFIG] extra_body ignored: {e}", flush=True)
        return payload


def _base_alias():
    if CONFIG and CONFIG.has_section("MODELS"):
        return (CONFIG.get("MODELS", "base", fallback="") or "").strip()
    return ""


def _machine_facts():
    sysname = platform.system() or "?"
    if sysname == "Windows":
        rel = platform.release()
        os_name, shell = f"Windows {rel}".strip(), "PowerShell / cmd.exe"
    elif sysname == "Darwin":
        os_name, shell = f"macOS {platform.mac_ver()[0]}".strip(), os.environ.get("SHELL", "/bin/zsh")
    else:
        os_name = sysname
        try:
            with open("/etc/os-release", encoding="utf-8") as fh:
                _kv = dict(l.rstrip("\n").split("=", 1) for l in fh if "=" in l)
            os_name = (_kv.get("PRETTY_NAME", "") or sysname).strip('"')
        except Exception:
            pass
        shell = os.environ.get("SHELL", "/bin/sh")
    return {"os": os_name, "arch": platform.machine() or "?", "shell": shell,
            "home": os.path.expanduser("~"), "sep": os.sep}


def _resolve_endpoint(section):
    import hoplita_llmcfg
    return hoplita_llmcfg.resolve_endpoint(CONFIG, section)



class ContextOverflow(Exception):
    overflow = True

    def __init__(self, prompt, reply, window):
        super().__init__(f"the prompt ({prompt} tokens) and the reply ({reply}) do not fit "
                         f"this model's window ({window})")
        self.prompt, self.reply, self.window = int(prompt), int(reply), int(window)


_OVERFLOW_PATTERNS = (
    (re.compile(r"prompt has (\d+) tokens and requests (\d+) reply tokens.*?context window is (\d+)", re.S), (0, 1, 2)),
    (re.compile(r"maximum context length is (\d+) tokens.*?\((\d+) in the messages, (\d+) in the completion\)", re.S), (1, 2, 0)),
)


def _context_overflow_of(text):
    for rx, order in _OVERFLOW_PATTERNS:
        m = rx.search(text or "")
        if m:
            g = [int(x) for x in m.groups()]
            return g[order[0]], g[order[1]], g[order[2]]
    return None


class VaultPasswordError(Exception):
    pass


try:
    import hoplita_storage
except Exception:
    hoplita_storage = None

_MCP_STARTED = datetime.now().isoformat(timespec="seconds")

_SQL = None
_SQL_FAILED = False


def _get_sql(password=None):
    global _SQL, _SQL_FAILED
    if _SQL is not None or _SQL_FAILED:
        return _SQL
    if password is None:
        return None
    if hoplita_storage is None or not hoplita_storage.backend_available():
        _SQL_FAILED = True
        print("[SQL] ✗ sqlcipher3 not installed (REQUIRED). "
              "Install with: pip install sqlcipher3-wheels")
        return None
    try:
        _SQL = hoplita_storage.SqlStore(
            os.path.join(hoplita_base(), "hoplita.db"), password)
        hoplita_storage.set_tombstone_stores(fed_tools._SYNC_STORES)
        print(f"[SQL] ✓ SQLCipher store open: {_SQL.db_path}")
    except hoplita_storage.BadKeyError as e:
        raise VaultPasswordError("SQL") from e
    except Exception as e:
        _SQL_FAILED = True
        print(f"[SQL] ✗ Could not open SQLCipher store: {e}")
    return _SQL


class EncryptedStorageManager:
    SALT_SIZE = 32
    NONCE_SIZE = 12
    KEY_SIZE = 32
    VERSION = 1
    PBKDF2_ITERATIONS = 600_000

    DEFAULT_FILENAME = ""
    DEFAULT_DATA: dict = {}
    LOG_LABEL = ""
    SQL_LABEL = ""

    def __init__(self, json_path=None):
        if json_path is None:
            base = hoplita_base()
            os.makedirs(base, exist_ok=True)
            json_path = os.path.join(base, self.DEFAULT_FILENAME)
        self.json_path = json_path
        self.data = json.loads(json.dumps(self.DEFAULT_DATA))
        self._data_loaded = False
        self._password = None
        self._sql_shadow = None

    def _derive_key(self, salt):
        if not self._password:
            raise Exception("Password no inicializada")
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=self.KEY_SIZE,
                         salt=salt, iterations=self.PBKDF2_ITERATIONS,
                         backend=default_backend())
        return kdf.derive(self._password.encode("utf-8"))

    def set_password(self, password):
        self._password = password
        if not self._data_loaded:
            self.load_data()
            self._data_loaded = True

    def _decrypt_blob(self, encrypted_json):
        if encrypted_json.get("version") != self.VERSION:
            raise Exception(f"Unsupported version: {encrypted_json.get('version')}")
        salt = base64.b64decode(encrypted_json["salt"])
        key = self._derive_key(salt)
        blob = base64.b64decode(encrypted_json["encrypted_data"])
        nonce, ciphertext = blob[:self.NONCE_SIZE], blob[self.NONCE_SIZE:]
        return json.loads(AESGCM(key).decrypt(nonce, ciphertext, None).decode("utf-8"))

    def _encrypt_blob(self, data_obj):
        salt = os.urandom(self.SALT_SIZE)
        key = self._derive_key(salt)
        plaintext = json.dumps(data_obj, ensure_ascii=False, indent=2).encode("utf-8")
        nonce = os.urandom(self.NONCE_SIZE)
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
        return {
            "version": self.VERSION,
            "salt": base64.b64encode(salt).decode("utf-8"),
            "encrypted_data": base64.b64encode(nonce + ciphertext).decode("utf-8"),
        }


    def _sql_blob_keys(self):
        consumed = getattr(self, "SQL_ROW_KEYS", None) or self.SQL_STORES
        return {k: v for k, v in self.data.items() if k not in consumed}

    @staticmethod
    def _sql_sort_key(item):
        return str(item.get("created_at") or item.get("id")
                   or item.get("date") or item.get("site") or "")

    def _sql_rows(self):
        return {s: {self._sql_key(it): it for it in self.data.get(s, [])}
                for s in self.SQL_STORES}

    def _sql_rebuild(self, rows_by_store, blob):
        data = json.loads(json.dumps(self.DEFAULT_DATA))
        data.update(blob)
        for s in self.SQL_STORES:
            data[s] = sorted(rows_by_store.get(s, {}).values(),
                             key=self._sql_sort_key)
        return data

    def load_data(self):
        if not self.SQL_LABEL:
            self._legacy_load_json()
            return
        sql = _get_sql(self._password)
        if sql is None:
            raise RuntimeError(
                f"{self.LOG_LABEL}: SQLCipher backend not available — it is "
                f"required. Install it with: pip install sqlcipher3-wheels")
        if sql.meta_get("init:" + self.SQL_LABEL):
            rows_by_store = {s: {} for s in self.SQL_STORES}
            for s in self.SQL_STORES:
                for p in sql.load(s):
                    rows_by_store[s][self._sql_key(p)] = p
            blob_rows = sql.load(self.SQL_LABEL + "__blob")
            blob = blob_rows[0] if blob_rows else {}
            self.data = self._sql_rebuild(rows_by_store, blob)
            self._sql_shadow = self._sql_serialize()
            n = sum(len(v) for v in rows_by_store.values())
            print(f"[OK] {self.LOG_LABEL} loaded from SQLCipher ({n} rows)")
            return
        self._sql_shadow = {}
        self.save_data()
        sql.meta_set("init:" + self.SQL_LABEL, datetime.now().isoformat())
        print(f"[OK] {self.LOG_LABEL}: fresh SQLCipher store initialized")

    def _sql_serialize(self):
        out = {}
        for store, keyed in self._sql_rows().items():
            out[store] = {k: json.dumps(p, ensure_ascii=False, sort_keys=True)
                          for k, p in keyed.items()}
        blob = self._sql_blob_keys()
        out[self.SQL_LABEL + "__blob"] = {
            "blob": json.dumps(blob, ensure_ascii=False, sort_keys=True)}
        return out

    def _sql_key(self, payload):
        return (payload.get("uuid") or payload.get("id")
                or str(payload.get("site", "")).lower())

    def _legacy_load_json(self):
        enc_path = self.json_path + ".encrypted"
        if os.path.exists(enc_path):
            try:
                with open(enc_path, "r", encoding="utf-8") as f:
                    self.data = self._decrypt_blob(json.load(f))
                print(f"[OK] {self.LOG_LABEL} decrypted")
            except Exception as e:
                raise VaultPasswordError(self.LOG_LABEL) from e
            return
        try:
            if os.path.exists(self.json_path):
                print(f"[WARN] {self.LOG_LABEL}: using plain JSON {self.json_path}")
                with open(self.json_path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            else:
                self.save_data()
                print(f"[OK] {self.LOG_LABEL}: created {self.json_path}.encrypted")
        except Exception as e:
            print(f"[ERR] Loading {self.LOG_LABEL}: {e}")
            self.data = json.loads(json.dumps(self.DEFAULT_DATA))

    BACKUP_KEEP = 30

    def _backup_before_save(self, path):
        try:
            if not os.path.exists(path):
                return
            bdir = os.path.join(os.path.dirname(path), "backups")
            os.makedirs(bdir, exist_ok=True)
            base = os.path.basename(path)
            mine = sorted(f for f in os.listdir(bdir) if f.startswith(base + "."))
            if mine:
                newest = os.path.getmtime(os.path.join(bdir, mine[-1]))
                import time as _t
                if _t.time() - newest < 600:
                    return
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            import shutil
            shutil.copy2(path, os.path.join(bdir, f"{base}.{stamp}"))
            mine = sorted(f for f in os.listdir(bdir) if f.startswith(base + "."))
            for old_f in mine[:-self.BACKUP_KEEP]:
                os.remove(os.path.join(bdir, old_f))
        except Exception as e:
            print(f"[WARN] backup of {self.LOG_LABEL} failed: {e}")

    def save_data(self):
        sql = _get_sql() if self.SQL_LABEL else None
        if sql is not None:
            try:
                current = self._sql_serialize()
                shadow = self._sql_shadow or {}
                new_shadow = {}
                for store, cur in current.items():
                    new_shadow[store] = sql.sync_store(
                        store, cur, shadow.get(store, {}))
                self._sql_shadow = new_shadow
                sql.maybe_backup()
            except Exception as e:
                print(f"[ERR] SQL save {self.LOG_LABEL}: {e}")
            return
        try:
            os.makedirs(os.path.dirname(self.json_path), exist_ok=True)
            target = self.json_path + ".encrypted"
            self._backup_before_save(target)
            with open(target, "w", encoding="utf-8") as f:
                json.dump(self._encrypt_blob(self.data), f, indent=2)
        except Exception as e:
            print(f"[ERR] Saving {self.LOG_LABEL}: {e}")



class TODOManager(EncryptedStorageManager):
    DEFAULT_FILENAME = "todo.json"
    DEFAULT_DATA = {"projects": ["general"], "appointments": [], "tasks": [],
                    "deleted_tasks": {}, "deleted_appointments": {}}
    SQL_LABEL = "todo"
    SQL_STORES = ("tasks", "appointments")
    EVENTS_STORE = "project_events"
    LOG_LABEL = "TODO"
    DEFAULT_PROJECT = "general"

    def _migrate(self):
        if "categories" in self.data and "projects" not in self.data:
            self.data["projects"] = self.data.pop("categories")
            print(f"[TODO] Migration: 'categories' → 'projects'")
        if "projects" not in self.data or not self.data["projects"]:
            self.data["projects"] = [self.DEFAULT_PROJECT]
        elif self.DEFAULT_PROJECT not in self.data["projects"]:
            self.data["projects"].insert(0, self.DEFAULT_PROJECT)
        migrated_items = 0
        for item in self.data.get("appointments", []) + self.data.get("tasks", []):
            if "category" in item and "project" not in item:
                item["project"] = item.pop("category")
            if "project" not in item:
                item["project"] = self.DEFAULT_PROJECT
                migrated_items += 1
        if migrated_items:
            print(f"[TODO] Migration: {migrated_items} items assigned to default project")
            self.save_data()

    def load_data(self):
        super().load_data()
        self._migrate()


    def get_projects(self):
        return list(self.data.get("projects", [self.DEFAULT_PROJECT]))

    def add_project(self, name):
        name = (name or "").strip()
        if not name:
            return {"error": "Empty name"}
        if name in self.data["projects"]:
            return {"error": f"Project '{name}' already exists", "project": name}
        self.data["projects"].append(name)
        self.save_data()
        return {"added": True, "project": name}

    def rename_project(self, old, new):
        old = (old or "").strip()
        new = (new or "").strip()
        if not old or not new:
            return {"error": "Empty names"}
        if old == self.DEFAULT_PROJECT:
            return {"error": f"You cannot rename '{self.DEFAULT_PROJECT}'"}
        if old not in self.data["projects"]:
            return {"error": f"'{old}' does not exist"}
        if new in self.data["projects"]:
            return {"error": f"'{new}' already exists"}
        idx = self.data["projects"].index(old)
        self.data["projects"][idx] = new
        moved = 0
        for item in self.data["appointments"] + self.data["tasks"]:
            if item.get("project") == old:
                item["project"] = new
                moved += 1
        self.save_data()
        self._migrate_project_events(old, new)
        return {"renamed": True, "old": old, "new": new, "items_moved": moved}

    def delete_project(self, name):
        name = (name or "").strip()
        if name == self.DEFAULT_PROJECT:
            return {"error": f"You cannot delete '{self.DEFAULT_PROJECT}'"}
        if name not in self.data["projects"]:
            return {"error": f"'{name}' does not exist"}
        self.data["projects"].remove(name)
        moved = 0
        for item in self.data["appointments"] + self.data["tasks"]:
            if item.get("project") == name:
                item["project"] = self.DEFAULT_PROJECT
                moved += 1
        self.save_data()
        self._migrate_project_events(name, self.DEFAULT_PROJECT)
        return {"deleted": True, "project": name,
                "items_moved_to_default": moved}


    def add_appointment(self, date, title, description, rag_urls,
                        urgency="Normal", project=None):
        proj = project if project in self.data.get("projects", []) else self.DEFAULT_PROJECT
        apt = {
            "id": datetime.now().isoformat(), "date": date, "title": title,
            "description": description, "rag_urls": rag_urls, "urgency": urgency,
            "project": proj,
            "created_at": datetime.now().isoformat(),
        }
        self.data["appointments"].append(apt)
        self.save_data()
        return apt

    @staticmethod
    def _clean_deps(deps, own_uuid=None):
        out = []
        for d in (deps or []):
            d = str(d or "").strip()
            if d and d != own_uuid and d not in out:
                out.append(d)
        return out

    def _project_event(self, project, action, task, detail="", editor=None,
                       fed_rev=None):
        try:
            sql = _get_sql()
            if sql is None:
                return
            ts = datetime.now().isoformat()
            if fed_rev is not None:
                key = f"fed|{task.get('uuid', '')}|{int(fed_rev)}|{action}"
            else:
                key = f"{ts}|{uuid.uuid4().hex[:8]}"
            sql.upsert(self.EVENTS_STORE, key, {
                "key": key, "ts": ts, "project": str(project or ""),
                "action": str(action), "task": task.get("uuid", "") or "",
                "title": str(task.get("title", "") or ""),
                "detail": str(detail or ""),
                "editor": str(editor or ""),
                "host": "" if editor else platform.node()})
        except Exception:
            pass

    def _migrate_project_events(self, old, new):
        try:
            sql = _get_sql()
            if sql is None:
                return
            for ev in (sql.load(self.EVENTS_STORE) or []):
                if isinstance(ev, dict) and ev.get("project") == old \
                        and ev.get("key"):
                    ev["project"] = new
                    sql.upsert(self.EVENTS_STORE, ev["key"], ev)
        except Exception:
            pass

    def add_task(self, priority, title, description, rag_urls, progress=0,
                 project=None, deps=None):
        proj = project if project in self.data.get("projects", []) else self.DEFAULT_PROJECT
        now = datetime.now().isoformat()
        task = {
            "id": now, "priority": priority, "title": title,
            "description": description, "rag_urls": rag_urls, "progress": progress,
            "project": proj,
            "uuid": uuid.uuid4().hex, "rev": 1, "editor": "",
            "deps": self._clean_deps(deps),
            "created_at": now,
            "updated_at": now,
        }
        self.data["tasks"].append(task)
        self.save_data()
        self._project_event(proj, "task_created", task)
        return task

    def update_appointment(self, apt_id, date=None, title=None, description=None,
                           rag_urls=None, urgency=None, project=None):
        for apt in self.data["appointments"]:
            if apt["id"] == apt_id:
                for k, v in (("date", date), ("title", title), ("description", description),
                             ("rag_urls", rag_urls), ("urgency", urgency)):
                    if v is not None: apt[k] = v
                if project is not None and project in self.data.get("projects", []):
                    apt["project"] = project
                apt["updated_at"] = datetime.now().isoformat()
                self.save_data()
                return apt
        return None

    def update_task(self, task_id, priority=None, title=None, description=None,
                    rag_urls=None, progress=None, project=None, deps=None):
        for task in self.data["tasks"]:
            if task["id"] == task_id:
                before = dict(task)
                for k, v in (("priority", priority), ("title", title), ("description", description),
                             ("rag_urls", rag_urls)):
                    if v is not None: task[k] = v
                if progress is not None:
                    task["progress"] = max(0, min(100, progress))
                if deps is not None:
                    task["deps"] = self._clean_deps(deps, own_uuid=task.get("uuid"))
                if project is not None and project in self.data.get("projects", []):
                    task["project"] = project
                task["rev"] = int(task.get("rev", 1)) + 1
                task["updated_at"] = datetime.now().isoformat()
                self.save_data()
                parts = []
                if task.get("progress") != before.get("progress"):
                    parts.append(f"{before.get('progress', 0)}% → {task['progress']}%")
                if title is not None and title != before.get("title"):
                    parts.append(f"«{before.get('title', '')}» → «{title}»")
                if priority is not None and priority != before.get("priority"):
                    parts.append(f"{before.get('priority', '')} → {priority}")
                if deps is not None and task.get("deps") != (before.get("deps") or []):
                    parts.append(f"🔗 {len(task['deps'])}")
                if project is not None and task.get("project") != before.get("project"):
                    parts.append(f"{before.get('project', '')} → {task['project']}")
                if parts:
                    self._project_event(task.get("project"), "task_updated",
                                        task, detail=" · ".join(parts))
                return task
        return None

    def fed_items(self, project):
        out = []
        for tk in self.data.get("tasks", []):
            if tk.get("project") != project:
                continue
            out.append({"uuid": tk.get("uuid"), "rev": int(tk.get("rev", 1)),
                        "editor": tk.get("editor", ""),
                        "deleted": bool(tk.get("deleted", False)),
                        "local_only": bool(tk.get("local_only", False)),
                        "payload": {"title": tk.get("title", ""),
                                    "description": tk.get("description", ""),
                                    "progress": tk.get("progress", 0),
                                    "priority": tk.get("priority", "media"),
                                    "deps": list(tk.get("deps") or []),
                                    "rag_urls": tk.get("rag_urls", [])}})
        return out

    def fed_stamp_editor(self, project, uuid_, editor):
        for tk in self.data.get("tasks", []):
            if tk.get("uuid") == uuid_ and not tk.get("editor"):
                tk["editor"] = editor
                self.save_data()
                return True
        return False

    def fed_upsert(self, project, item):
        p = item.get("payload", {})
        now = datetime.now().isoformat()
        if project:
            projs = self.data.setdefault("projects", [self.DEFAULT_PROJECT])
            if project not in projs:
                projs.append(project)
        for tk in self.data.get("tasks", []):
            if tk.get("uuid") == item["uuid"]:
                before = dict(tk)
                tk["title"] = p.get("title", tk.get("title", ""))
                tk["description"] = p.get("description", tk.get("description", ""))
                tk["progress"] = p.get("progress", tk.get("progress", 0))
                tk["priority"] = p.get("priority", tk.get("priority", "media"))
                tk["deps"] = self._clean_deps(p.get("deps", tk.get("deps") or []),
                                              own_uuid=tk.get("uuid"))
                tk["rag_urls"] = p.get("rag_urls", tk.get("rag_urls", []))
                tk["project"] = project
                tk["rev"] = int(item["rev"]); tk["editor"] = item.get("editor", "")
                tk["deleted"] = bool(item.get("deleted", False))
                tk["local_only"] = bool(item.get("local_only", False))
                tk["updated_at"] = now
                self.save_data()
                _watched = ("title", "description", "progress", "priority",
                            "deps", "rag_urls", "deleted", "rev", "editor")
                changed = any(tk.get(k) != before.get(k) for k in _watched)
                if not changed:
                    action = None
                elif tk.get("deleted") and not before.get("deleted"):
                    action = "task_deleted"
                elif tk.get("deleted"):
                    action = None
                else:
                    action = "task_updated"
                detail = ""
                if action == "task_updated" \
                        and tk.get("progress") != before.get("progress"):
                    detail = f"{before.get('progress', 0)}% → {tk['progress']}%"
                if action:
                    self._project_event(project, action, tk, detail=detail,
                                        editor=item.get("editor", ""),
                                        fed_rev=item.get("rev"))
                return tk
        task = {"id": now, "uuid": item["uuid"], "rev": int(item["rev"]),
                "editor": item.get("editor", ""), "priority": p.get("priority", "media"),
                "title": p.get("title", ""), "description": p.get("description", ""),
                "deps": self._clean_deps(p.get("deps"), own_uuid=item["uuid"]),
                "rag_urls": p.get("rag_urls", []), "progress": p.get("progress", 0),
                "project": project, "created_at": now, "updated_at": now,
                "deleted": bool(item.get("deleted", False)),
                "local_only": bool(item.get("local_only", False))}
        self.data.setdefault("tasks", []).append(task)
        self.save_data()
        if not task.get("deleted"):
            self._project_event(project, "task_created", task,
                                editor=item.get("editor", ""),
                                fed_rev=item.get("rev"))
        return task

    def delete_appointment(self, appointment_id):
        gone = next((a for a in self.data["appointments"]
                     if a["id"] == appointment_id), None)
        self.data["appointments"] = [a for a in self.data["appointments"]
                                     if a["id"] != appointment_id]
        if gone:
            key = f"{gone.get('date','')}|{gone.get('title','').strip().lower()}"
            self.data.setdefault("deleted_appointments", {})[key] = datetime.now().isoformat()
        self.save_data()

    def get_deleted_appointments(self):
        return dict(self.data.get("deleted_appointments", {}))

    def delete_task(self, task_id):
        gone = next((t for t in self.data["tasks"] if t["id"] == task_id), None)
        self.data["tasks"] = [t for t in self.data["tasks"] if t["id"] != task_id]
        if gone:
            key = f"{gone.get('project','general')}|{gone.get('title','').strip().lower()}"
            self.data.setdefault("deleted_tasks", {})[key] = datetime.now().isoformat()
        self.save_data()
        if gone:
            self._project_event(gone.get("project"), "task_deleted", gone)

    def get_appointments(self, project=None):
        items = self.data.get("appointments", [])
        if project:
            items = [a for a in items if a.get("project", self.DEFAULT_PROJECT) == project]
        return sorted(items, key=lambda x: x["date"])

    def get_tasks(self, project=None):
        order = {"Alta": 1, "Media": 2, "Baja": 3}
        items = [t for t in self.data.get("tasks", []) if not t.get("deleted")]
        if project:
            items = [t for t in items if t.get("project", self.DEFAULT_PROJECT) == project]
        return sorted(items, key=lambda x: order.get(x.get("priority", "Media"), 4))

    def get_project_board(self, project=None, events_limit=250):
        proj = project or self.DEFAULT_PROJECT
        tasks = self.get_tasks(proj)
        live = {t.get("uuid") for t in tasks if t.get("uuid")}
        by_uuid = {t.get("uuid"): t for t in self.data.get("tasks", [])
                   if t.get("uuid")}
        missing = {}
        for t in tasks:
            for d in (t.get("deps") or []):
                if d in live or d in missing:
                    continue
                s = by_uuid.get(d)
                if s is None:
                    missing[d] = {"title": "", "state": "unknown"}
                elif s.get("deleted"):
                    missing[d] = {"title": s.get("title", ""),
                                  "state": "deleted",
                                  "editor": s.get("editor", "")}
                else:
                    missing[d] = {"title": s.get("title", ""),
                                  "state": "elsewhere",
                                  "project": s.get("project", "")}
        return {"project": proj, "tasks": tasks, "missing": missing,
                "events": self.get_project_events(proj, events_limit)}

    def get_project_events(self, project=None, limit=250):
        try:
            sql = _get_sql()
            if sql is None:
                return []
            rows = [p for p in (sql.load(self.EVENTS_STORE) or [])
                    if isinstance(p, dict)
                    and (not project or p.get("project") == project)]
            rows.sort(key=lambda r: (str(r.get("ts", "")), str(r.get("key", ""))),
                      reverse=True)
            return rows[:max(1, int(limit or 250))]
        except Exception:
            return []

    def get_all_rag_urls(self):
        urls = []
        for apt in self.data.get("appointments", []):
            if apt.get("rag_urls"):
                urls.extend([u.strip() for u in apt["rag_urls"].split(",") if u.strip()])
        for task in self.data.get("tasks", []):
            if task.get("rag_urls"):
                urls.extend([u.strip() for u in task["rag_urls"].split(",") if u.strip()])
        seen, unique = set(), []
        for url in urls:
            if url not in seen:
                seen.add(url); unique.append(url)
        return unique

    def get_context_text(self, current_datetime=None, projects=None):
        parts = []
        now = current_datetime or datetime.now()
        parts.append(f"=== CURRENT DATE/TIME: {now.strftime('%Y-%m-%d %H:%M:%S (%A)')} ===")
        _keep = (None if projects is None
                 else {str(p) for p in projects})

        def _mine(item):
            return _keep is None or (item.get("project") or "general") in _keep

        apts = [a for a in self.get_appointments() if _mine(a)]
        if apts:
            parts.append("\n=== APPOINTMENTS ===")
            for apt in apts:
                emoji = {"Baja": "🟢", "Normal": "🔵", "Alta": "🟡", "Crítica": "🔴"}.get(apt.get("urgency", "Normal"), "⚪")
                parts.append(f"{emoji} [{apt.get('urgency', 'Normal')}] 📅 {apt['date']} - {apt['title']}\n"
                             f"{apt['description']}\nURLs RAG: {apt['rag_urls']}")
        tasks = [t for t in self.get_tasks() if _mine(t)]
        if tasks:
            all_live = {x.get("uuid"): x for x in self.get_tasks()
                        if x.get("uuid")}
            all_any = {x.get("uuid"): x for x in self.data.get("tasks", [])
                       if x.get("uuid")}
            parts.append("\n=== TASKS ===")
            for t in tasks:
                emoji = {"Alta": "🔴", "Media": "🟡", "Baja": "🟢"}.get(t["priority"], "⚪")
                progress = t.get("progress", 0)
                bar = "█" * (progress // 10) + "░" * (10 - progress // 10)
                parts.append(f"{emoji} [{t['priority']}] {t['title']} - {progress}% {bar}\n"
                             f"{t['description']}\nURLs RAG: {t['rag_urls']}")
                deps = t.get("deps") or []
                if deps:
                    names, ok = [], 0
                    for d in deps:
                        x = all_live.get(d)
                        if x is not None:
                            done = int(x.get("progress") or 0) >= 100
                            ok += 1 if done else 0
                            names.append(x.get("title", "")
                                         + (" ✔" if done else
                                            f" ({int(x.get('progress') or 0)}%)"))
                        else:
                            gone = all_any.get(d)
                            names.append("⚠ "
                                         + ((gone.get("title", "") + " (deleted)")
                                            if gone else "a task that no longer exists here"))
                    gate = ("READY — every dependency done" if ok == len(deps)
                            else f"BLOCKED — {ok}/{len(deps)} dependencies done")
                    parts.append(f"⛓ depends on: {', '.join(names)} → {gate}")
        return "\n".join(parts) if parts else "No appointments or tasks."



class NotesManager(EncryptedStorageManager):
    DEFAULT_FILENAME = "notes.json"
    DEFAULT_DATA = {"categories": {}, "deleted_notes": {}, "realms": {},
                    "local_only": {}}
    LOG_LABEL = "NOTES"
    SQL_LABEL = "notes"
    SQL_STORES = ("notes",)
    SQL_ROW_KEYS = ("categories",)

    SYSTEM_FOLDERS = ("working-memory", "compaction", "cmd_log")

    def get_realms(self):
        _have = self.data.get("categories", {}) or {}
        out = {c: "system" for c in self.SYSTEM_FOLDERS if c in _have}
        for c, r in (self.data.get("realms", {}) or {}).items():
            if c not in out:
                out[c] = r
        return out

    def get_realm(self, category):
        if category in self.SYSTEM_FOLDERS:
            return "system"
        return self.data.get("realms", {}).get(category, "personal")

    def set_realm(self, category, realm):
        if category in self.SYSTEM_FOLDERS:
            return False
        if realm not in ("dev", "personal"):
            return False
        if self.data.setdefault("realms", {}).get(category) == realm:
            return True
        self.data["realms"][category] = realm
        self.save_data()
        return True

    def get_local_only(self):
        return dict(self.data.get("local_only", {}))

    def set_local_only(self, category, flag):
        m = self.data.setdefault("local_only", {})
        if bool(m.get(category)) == bool(flag):
            return True
        if flag:
            m[category] = True
        else:
            m.pop(category, None)
        self.save_data()
        return True

    def get_skill_cats(self):
        return dict(self.data.get("skill_cats", {}))

    def set_skill_cat(self, category, flag):
        m = self.data.setdefault("skill_cats", {})
        if bool(m.get(category)) == bool(flag):
            return True
        if flag:
            m[category] = True
        else:
            m.pop(category, None)
        self.save_data()
        return True

    def _sql_rows(self):
        rows = {}
        for cat, notes in self.data.get("categories", {}).items():
            for n in notes:
                rows[self._sql_key(n)] = {**n, "category": cat}
        return {"notes": rows}

    def _sql_blob_keys(self):
        blob = {k: v for k, v in self.data.items() if k != "categories"}
        blob["__empty_categories"] = [
            c for c, ns in self.data.get("categories", {}).items() if not ns]
        return blob

    def _sql_rebuild(self, rows_by_store, blob):
        data = {"categories": {}, "deleted_notes": {}}
        empties = blob.pop("__empty_categories", [])
        data.update(blob)
        cats = data["categories"] = {}
        for p in sorted(rows_by_store.get("notes", {}).values(),
                        key=self._sql_sort_key):
            note = dict(p)
            cat = note.pop("category", "general")
            cats.setdefault(cat, []).append(note)
        for c in empties:
            cats.setdefault(c, [])
        return data

    _NOTE_FIXED_SALT = b"apita_notes_salt_v1"
    _NOTE_KDF_ITERATIONS = 100_000

    def _note_content_key(self):
        if not self._password:
            raise Exception("Password no inicializada")
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                         salt=self._NOTE_FIXED_SALT,
                         iterations=self._NOTE_KDF_ITERATIONS,
                         backend=default_backend())
        return kdf.derive(self._password.encode("utf-8"))

    def encrypt_note_content(self, content):
        try:
            key = self._note_content_key()
            nonce = os.urandom(12)
            cipher = Cipher(algorithms.AES(key), modes.GCM(nonce), backend=default_backend())
            encryptor = cipher.encryptor()
            ciphertext = encryptor.update(content.encode("utf-8")) + encryptor.finalize()
            return base64.b64encode(nonce + encryptor.tag + ciphertext).decode("utf-8")
        except Exception as e:
            print(f"[ERR] Encrypting note: {e}")
            return None

    def decrypt_note_content(self, encrypted_package):
        try:
            key = self._note_content_key()
            raw = base64.b64decode(encrypted_package)
            nonce, tag, ciphertext = raw[:12], raw[12:28], raw[28:]
            cipher = Cipher(algorithms.AES(key), modes.GCM(nonce, tag), backend=default_backend())
            decryptor = cipher.decryptor()
            return (decryptor.update(ciphertext) + decryptor.finalize()).decode("utf-8")
        except Exception as e:
            print(f"[ERR] Decrypting note: {e}")
            return None

    def add_category(self, name):
        if name.strip() and name not in self.data["categories"]:
            self.data["categories"][name] = []
            self.save_data()
            return True
        return False

    def delete_category(self, name):
        if name in self.data["categories"]:
            del self.data["categories"][name]
            self.save_data()
            return True
        return False

    def get_categories(self):
        return sorted(self.data["categories"].keys())

    RING_CAPS = {"cmd_log": 300, "working-memory": 40}
    WM_GLOBAL_CAP = 240

    def _ring_key(self, category, content):
        if category != WORKING_MEMORY:
            return ""
        c = content or ""
        if not c.startswith(_WM_TAG):
            return ""
        end = c.find("\n")
        raw = c[len(_WM_TAG):(end if end > 0 else len(c))].rstrip("]").strip()
        return raw.split(" ")[0] if raw else ""

    def _prune_ring(self, bucket, cap, per_conversation):
        if not per_conversation:
            extra = len(bucket) - cap
            if extra > 0:
                del bucket[:extra]
            return max(0, extra)
        buckets = {}
        for n in bucket:
            buckets.setdefault(n.get("conv", ""), []).append(n)
        doomed = set()
        for _k, notes in buckets.items():
            if len(notes) > cap:
                for n in notes[:len(notes) - cap]:
                    doomed.add(n["uuid"])
        if doomed:
            bucket[:] = [n for n in bucket if n["uuid"] not in doomed]
        gone = len(doomed)
        writer = (bucket[-1].get("conv", "") if bucket else "")
        while len(bucket) > self.WM_GLOBAL_CAP:
            _stale = {}
            for n in bucket:
                k = n.get("conv", "")
                if k != writer:
                    _stale[k] = max(_stale.get(k, ""), n.get("created_at", ""))
            if not _stale:
                break
            victim = min(_stale, key=_stale.get)
            oldest = next(n for n in bucket if n.get("conv", "") == victim)
            bucket.remove(oldest)
            gone += 1
        return gone

    def add_note(self, category, title, content, attachment=None):
        encrypted = self.encrypt_note_content(content)
        if encrypted is None:
            return None
        note = {
            "id": datetime.now().isoformat(), "title": title,
            "content": encrypted, "encrypted": True,
            "uuid": uuid.uuid4().hex, "rev": 1, "editor": "",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "conv": self._ring_key(category, content),
            "attachment": attachment,
        }
        bucket = self.data["categories"].setdefault(category, [])
        bucket.append(note)
        _cap = self.RING_CAPS.get(category)
        if _cap:
            self._prune_ring(bucket, _cap,
                             per_conversation=(category == WORKING_MEMORY))
        self.save_data()
        return note

    def clear_category(self, category):
        bucket = self.data.get("categories", {}).get(category, [])
        n = len(bucket)
        if n:
            self.data["categories"][category] = []
            self.save_data()
        return n

    def update_note(self, category, note_id, title=None, content=None):
        if category not in self.data["categories"]:
            return False
        for note in self.data["categories"][category]:
            if note["id"] == note_id:
                if title is not None:
                    note["title"] = title
                if content is not None:
                    encrypted = self.encrypt_note_content(content)
                    note["content"] = encrypted if encrypted else content
                    note["encrypted"] = bool(encrypted)
                note["rev"] = int(note.get("rev", 1)) + 1
                note["updated_at"] = datetime.now().isoformat()
                self.save_data()
                return True
        return False

    def delete_note(self, category, note_id):
        if category not in self.data["categories"]:
            return False
        gone = next((n for n in self.data["categories"][category]
                     if n["id"] == note_id), None)
        before = len(self.data["categories"][category])
        self.data["categories"][category] = [n for n in self.data["categories"][category] if n["id"] != note_id]
        if len(self.data["categories"][category]) < before:
            if gone:
                key = f"{category}|{gone.get('title','').strip().lower()}"
                self.data.setdefault("deleted_notes", {})[key] = datetime.now().isoformat()
            self.save_data()
            return True
        return False

    def get_category_counts(self):
        return {cat: sum(1 for n in notes if not n.get("deleted"))
                for cat, notes in self.data.get("categories", {}).items()}

    def get_notes(self, category):
        notes = self.data["categories"].get(category, [])
        out = []
        for note in notes:
            if note.get("deleted"):
                continue
            if note.get("encrypted"):
                d = self.decrypt_note_content(note["content"])
                if d is not None:
                    copy = note.copy()
                    copy["content"] = d
                    out.append(copy)
                else:
                    out.append(note)
            else:
                out.append(note)
        return out

    def get_all_notes_from_category(self, category):
        notes = self.get_notes(category)
        if not notes:
            return ""
        parts = [f"=== CATEGORY: {category.upper()} ===\n"]
        for note in notes:
            parts.append(f"\n--- {note['title']} ---")
            parts.append(note["content"])
        return "\n".join(parts)

    def fed_items(self, category):
        out = []
        for n in self.data.get("categories", {}).get(category, []):
            content = n.get("content", "")
            if n.get("encrypted"):
                d = self.decrypt_note_content(content)
                content = d if d is not None else ""
            out.append({"uuid": n.get("uuid"), "rev": int(n.get("rev", 1)),
                        "editor": n.get("editor", ""),
                        "deleted": bool(n.get("deleted", False)),
                        "local_only": bool(n.get("local_only", False)),
                        "payload": {"title": n.get("title", ""), "content": content,
                                    "attachment": n.get("attachment")}})
        return out

    def fed_stamp_editor(self, category, uuid_, editor):
        for n in self.data.get("categories", {}).get(category, []):
            if n.get("uuid") == uuid_ and not n.get("editor"):
                n["editor"] = editor
                self.save_data()
                return True
        return False

    def fed_upsert(self, category, item):
        self.data.setdefault("categories", {}).setdefault(category, [])
        bucket = self.data["categories"][category]
        p = item.get("payload", {})
        enc = self.encrypt_note_content(p.get("content", ""))
        now = datetime.now().isoformat()
        for n in bucket:
            if n.get("uuid") == item["uuid"]:
                n["title"] = p.get("title", n.get("title", ""))
                n["content"] = enc if enc is not None else p.get("content", "")
                n["encrypted"] = enc is not None
                n["attachment"] = p.get("attachment", n.get("attachment"))
                n["rev"] = int(item["rev"]); n["editor"] = item.get("editor", "")
                n["deleted"] = bool(item.get("deleted", False))
                n["local_only"] = bool(item.get("local_only", False))
                n["updated_at"] = now
                self.save_data(); return n
        note = {"id": now, "uuid": item["uuid"], "rev": int(item["rev"]),
                "editor": item.get("editor", ""), "title": p.get("title", ""),
                "content": enc if enc is not None else p.get("content", ""),
                "encrypted": enc is not None, "attachment": p.get("attachment"),
                "created_at": now, "updated_at": now,
                "deleted": bool(item.get("deleted", False)),
                "local_only": bool(item.get("local_only", False))}
        bucket.append(note); self.save_data(); return note



class CredentialManager(EncryptedStorageManager):
    DEFAULT_FILENAME = "credentials.json"
    DEFAULT_DATA = {"credentials": []}
    LOG_LABEL = "CREDS"
    SQL_LABEL = "cred"
    SQL_STORES = ("credentials",)

    KINDS = ("web", "apikey", "card", "mail")

    def add_credential(self, site, username="", password="", url="", notes="",
                       totp_secret="", kind="web", secret="", number="",
                       expiry="", cvv="", holder="", smtp=""):
        kind = kind if kind in self.KINDS else "web"
        campos = {"username": username, "password": password, "url": url,
                  "notes": notes, "totp_secret": totp_secret, "kind": kind,
                  "secret": secret, "number": number, "expiry": expiry,
                  "cvv": cvv, "holder": holder, "smtp": smtp,
                  "updated_at": datetime.now().isoformat()}
        for cred in self.data["credentials"]:
            if cred["site"].lower() == site.lower():
                cred.update(campos)
                self.save_data()
                return cred
        cred = dict(campos, site=site,
                    created_at=datetime.now().isoformat())
        self.data["credentials"].append(cred)
        self.save_data()
        return cred

    def get_credential(self, site):
        site_l = site.lower()
        for cred in self.data["credentials"]:
            if cred["site"].lower() == site_l:
                return cred
        for cred in self.data["credentials"]:
            if site_l in cred["site"].lower() or site_l in cred.get("url", "").lower():
                return cred
        return None

    def get_otp(self, site):
        cred = self.get_credential(site)
        if not cred or not cred.get("totp_secret"):
            return None
        try:
            import hmac, struct
            secret = cred["totp_secret"].replace(" ", "").upper()
            padding = (-len(secret)) % 8
            if padding: secret += "=" * padding
            key = base64.b32decode(secret)
            counter = int(time.time()) // 30
            h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
            offset = h[-1] & 0x0F
            code = (struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
            return f"{code:06d}"
        except Exception as e:
            print(f"[OTP] Error for {site}: {e}")
            return None

    def get_credential_safe(self, site):
        cred = self.get_credential(site)
        if not cred:
            return None
        kind = cred.get("kind") or "web"
        out = {"site": cred["site"], "kind": kind,
               "url": cred.get("url", ""), "notes": cred.get("notes", "")}
        if kind == "apikey":
            out["has_key"] = bool(cred.get("secret"))
            out["hint"] = "get_api_key to use it"
        elif kind == "mail":
            out["username"] = cred.get("username", "")
            out["has_password"] = bool(cred.get("password"))
            out["smtp"] = cred.get("smtp", "")
            out["hint"] = (f"mail_list / mail_read / mail_move / mail_send with "
                           f"account='{cred['site']}'")
        elif kind == "card":
            num = (cred.get("number") or "").replace(" ", "")
            out["last4"] = num[-4:] if len(num) >= 4 else ""
            out["holder"] = cred.get("holder", "")
            out["expiry"] = cred.get("expiry", "")
            out["has_card"] = bool(num)
            out["hint"] = "get_card for the full details (number, expiry, CVV)"
        else:
            out["username"] = cred.get("username", "")
            out["has_password"] = bool(cred.get("password"))
            out["has_otp"] = bool(cred.get("totp_secret"))
        return out

    def get_api_key(self, site):
        cred = self.get_credential(site)
        if not cred or (cred.get("kind") or "web") != "apikey":
            return None
        return cred.get("secret") or None

    def get_card(self, site):
        cred = self.get_credential(site)
        if not cred or (cred.get("kind") or "web") != "card":
            return None
        return {"number": cred.get("number", ""), "expiry": cred.get("expiry", ""),
                "cvv": cred.get("cvv", ""), "holder": cred.get("holder", "")}

    def list_sites(self):
        return [{"site": c["site"], "kind": c.get("kind") or "web",
                 "username": c.get("username", ""), "url": c.get("url", "")}
                for c in self.data["credentials"]]

    def delete_credential(self, site):
        site_l = site.lower()
        self.data["credentials"] = [c for c in self.data["credentials"] if c["site"].lower() != site_l]
        self.save_data()

    def get_all(self):
        return self.data.get("credentials", [])



class CronManager(EncryptedStorageManager):
    DEFAULT_FILENAME = "cron.json"
    DEFAULT_DATA = {"jobs": []}
    LOG_LABEL = "CRON"
    SQL_LABEL = "cron"
    SQL_STORES = ("jobs",)

    def add_job(self, name, instruction, schedule_type,
                interval_minutes=0, fixed_datetime="", daily_times=None,
                weekdays=None, enabled=True, target="local", context=None,
                model="", own_tab=False):
        job = {
            "id": datetime.now().isoformat(), "name": name,
            "instruction": instruction, "schedule_type": schedule_type,
            "interval_minutes": interval_minutes,
            "fixed_datetime": fixed_datetime,
            "daily_times": daily_times or [],
            "weekdays": weekdays or [],
            "enabled": enabled,
            "model": (model or "").strip(),
            "target": target or "local",
            "context": context or None,
            "own_tab": bool(own_tab),
            "created_at": datetime.now().isoformat(),
            "last_run": None,
            "next_run": self._calc_next(schedule_type, interval_minutes, fixed_datetime, daily_times, None, weekdays),
            "run_count": 0, "log": [],
        }
        self.data["jobs"].append(job)
        self.save_data()
        return job

    def _calc_next(self, stype, interval=0, fixed_dt="", daily_times=None, last_run=None, weekdays=None):
        now = datetime.now()
        if stype == "fixed":
            try:
                dt = datetime.strptime(fixed_dt, "%Y-%m-%d %H:%M")
                return dt.isoformat() if dt > now else None
            except Exception:
                return None
        if stype == "interval":
            if interval <= 0: return None
            base = datetime.fromisoformat(last_run) if last_run else now
            nxt = base + timedelta(minutes=interval)
            if nxt <= now: nxt = now + timedelta(minutes=interval)
            return nxt.isoformat()
        if stype == "daily":
            if not daily_times: return None
            candidates = []
            for t_str in daily_times:
                try:
                    h, m = map(int, t_str.split(":"))
                    c = now.replace(hour=h, minute=m, second=0, microsecond=0)
                    if c <= now: c += timedelta(days=1)
                    candidates.append(c)
                except Exception:
                    pass
            return min(candidates).isoformat() if candidates else None
        if stype == "weekly":
            if not weekdays: return None
            try:
                h, m = map(int, (daily_times[0] if daily_times else "09:00").split(":"))
            except Exception:
                h, m = 9, 0
            candidates = []
            for wd in weekdays:
                try:
                    wd = int(wd)
                except Exception:
                    continue
                days_ahead = (wd - now.weekday()) % 7
                c = now.replace(hour=h, minute=m, second=0, microsecond=0) \
                    + timedelta(days=days_ahead)
                if c <= now:
                    c += timedelta(days=7)
                candidates.append(c)
            return min(candidates).isoformat() if candidates else None
        return None

    def update_job(self, job_id, **kwargs):
        for job in self.data["jobs"]:
            if job["id"] == job_id:
                for k, v in kwargs.items():
                    if (k in job or k in ("context", "own_tab", "model", "weekdays")) \
                            and k not in ("id", "created_at", "log"):
                        job[k] = v
                if any(k in kwargs for k in ("schedule_type", "interval_minutes",
                                              "fixed_datetime", "daily_times", "weekdays")):
                    job["next_run"] = self._calc_next(
                        job["schedule_type"], job["interval_minutes"],
                        job["fixed_datetime"], job["daily_times"], job["last_run"],
                        job.get("weekdays"))
                self.save_data()
                return job
        return None

    def delete_job(self, job_id):
        self.data["jobs"] = [j for j in self.data["jobs"] if j["id"] != job_id]
        self.save_data()

    def get_jobs(self):
        return self.data.get("jobs", [])

    def arm_startup_jobs(self):
        now = datetime.now().isoformat()
        n = 0
        for job in self.data.get("jobs", []):
            if job.get("schedule_type") == "startup" and job.get("enabled", True):
                job["next_run"] = now
                n += 1
        if n:
            self.save_data()
        return n

    def get_due_jobs(self):
        now = datetime.now()
        due = []
        for job in self.data["jobs"]:
            if not job.get("enabled", True): continue
            nr = job.get("next_run")
            if not nr: continue
            try:
                if datetime.fromisoformat(nr) <= now:
                    due.append(job)
            except Exception:
                pass
        return due

    def log_execution(self, job_id, status, result):
        now = datetime.now()
        for job in self.data["jobs"]:
            if job["id"] == job_id:
                job["log"].append({"timestamp": now.isoformat(), "status": status, "result": result[:5000]})
                if len(job["log"]) > 50:
                    job["log"] = job["log"][-50:]
                job["last_run"] = now.isoformat()
                job["run_count"] = job.get("run_count", 0) + 1
                if job["schedule_type"] == "fixed":
                    job["next_run"] = None
                    job["enabled"] = False
                else:
                    job["next_run"] = self._calc_next(
                        job["schedule_type"], job["interval_minutes"],
                        job["fixed_datetime"], job["daily_times"], job["last_run"],
                        job.get("weekdays"))
                self.save_data()
                return

    def get_job_log(self, job_id):
        for job in self.data["jobs"]:
            if job["id"] == job_id:
                return job.get("log", [])
        return []

    def clear_job_log(self, job_id):
        for job in self.data["jobs"]:
            if job["id"] == job_id:
                job["log"] = []
                self.save_data()
                return True
        return False



EXP_META_PATH = os.path.join(hoplita_base(), "experience_meta.json")

_exp_docs: list = []
_exp_meta: list = []


LESSONS_META_PATH = os.path.join(hoplita_base(), "lessons_meta.json")
_lessons_data: list = []
_lessons_lock = threading.Lock()
FACTS_META_PATH = os.path.join(hoplita_base(), "facts_meta.json")
FACTS_PIN_THRESHOLD = 3
FACTS_INJECT_BUDGET = 20
FACTS_HITS_COOLDOWN_MIN = 30
_facts_data: list = []
_facts_lock = threading.Lock()


def _save_exp_to_disk():
    try:
        sql = _get_sql()
        if sql is not None:
            sql.replace_all("experience",
                            [(str(i), {"doc": d, "meta": m, "ord": i})
                             for i, (d, m) in enumerate(zip(_exp_docs, _exp_meta))])
            sql.meta_set("init:EXP", datetime.now().isoformat())
        else:
            os.makedirs(hoplita_base(), exist_ok=True)
            with open(EXP_META_PATH, "w", encoding="utf-8") as f:
                json.dump({"docs": _exp_docs, "meta": _exp_meta}, f, ensure_ascii=False)
    except Exception as e:
        print(f"[EXP] ERROR writing to disk ({EXP_META_PATH}): {e}", file=sys.stderr)


def init_experience():
    global _exp_docs, _exp_meta
    try:
        sql = _get_sql()
        if sql is None:
            _exp_docs, _exp_meta = [], []
            print("[MCP] Experience: new (empty)")
            return
        rows = sorted(sql.load("experience"), key=lambda r: r.get("ord", 0))
        _exp_docs = [r.get("doc", "") for r in rows]
        _exp_meta = [r.get("meta", {}) for r in rows]
        print(f"[MCP] Experience (SQLCipher): {len(_exp_docs)} exp")
    except Exception as e:
        print(f"[MCP] ⚠ Experience load: {e}", file=sys.stderr)
        _exp_docs, _exp_meta = [], []

def save_experience(goal, steps, result, feedback, tags=None, duration_s=0):
    doc_id = hashlib.md5(f"{goal}_{datetime.now().isoformat()}".encode()).hexdigest()[:16]
    steps_text = "\n".join(
        f"  [{s.get('step', i + 1)}] {s.get('action', '?')}: {s.get('detail', '')}" +
        (f"\n      $ {s['command']}" if s.get('command') else "")
        for i, s in enumerate(steps))
    doc = (f"GOAL: {goal}\nFEEDBACK: {feedback}\nTAGS: {','.join(tags or [])}\n"
           f"DATE: {datetime.now().isoformat()}\nSTEPS:\n{steps_text}\n"
           f"RESULT: {result[:500]}")
    meta = {"goal": goal[:200], "feedback": feedback,
            "date": datetime.now().isoformat(),
            "tags": ",".join(tags or []), "id": doc_id}
    _exp_docs.append(doc)
    _exp_meta.append(meta)
    _save_exp_to_disk()
    return {"saved": True, "id": doc_id, "total": len(_exp_docs)}

def list_experience(limit=100):
    out = []
    for i in range(len(_exp_meta) - 1, -1, -1):
        m = _exp_meta[i] or {}
        out.append({"id": m.get("id", str(i)), "goal": m.get("goal", ""),
                    "feedback": m.get("feedback", ""),
                    "date": m.get("date", ""),
                    "tags": [t for t in (m.get("tags", "") or "").split(",") if t],
                    "doc": (_exp_docs[i] if i < len(_exp_docs) else "")[:4000]})
        if len(out) >= max(1, int(limit or 100)):
            break
    return {"experiences": out, "total": len(_exp_docs)}


def search_experience(query, n_results=3):
    _items = [{"i": i_, "doc": d} for i_, d in enumerate(_exp_docs)]
    return {"results": [
        {"document": h["doc"],
         "metadata": _exp_meta[h["i"]] if h["i"] < len(_exp_meta) else {},
         "similarity": h.get("score", 0), "by": "words", "why": h.get("why", [])}
        for h in rank_by_words(query, _items, lambda x: x["doc"], k=n_results)]}

def _save_facts_to_disk():
    try:
        sql = _get_sql()
        if sql is not None:
            sql.replace_all("facts", [(f.get("id", str(i)), f)
                                      for i, f in enumerate(_facts_data)])
            sql.meta_set("init:FACTS", datetime.now().isoformat())
        else:
            os.makedirs(hoplita_base(), exist_ok=True)
            with open(FACTS_META_PATH, "w", encoding="utf-8") as f:
                json.dump({"facts": _facts_data}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[FACTS] ERROR writing to disk: {e}", file=sys.stderr)


def init_facts():
    global _facts_data
    sql = _get_sql()
    if sql is None:
        _facts_data = []
        print("[FACTS] 0 fact(s) (no SQL store)")
        return
    _facts_data = sorted(sql.load("facts"), key=lambda f: f.get("ts", ""))
    print(f"[FACTS] {len(_facts_data)} fact(s) loaded (SQLCipher)")


_INVISIBLE = re.compile(
    r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u206a-\u206f\ufeff"
    r"\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]")


def sanitize_memory_text(text):
    t = str(text or "")
    outside_n = len(_INVISIBLE.findall(t))
    t = _INVISIBLE.sub("", t)
    return t.strip(), outside_n


def _fact_live(f):
    return (f or {}).get("status", "live") != "archived"


def _live_facts():
    with _facts_lock:
        return [f for f in _facts_data if _fact_live(f)]


def _live_bytes():
    return sum(len((f.get("text") or "").strip()) + 40 for f in _live_facts())


def _fact_touch(f):
    f["updated"] = datetime.now().isoformat()


def _archive_fact(fact_id, reason, by=None):
    with _facts_lock:
        f = next((x for x in _facts_data if x.get("id") == fact_id), None)
        if f is None or not _fact_live(f):
            return None
        before = dict(f)
        f["status"] = "archived"
        f["archived_at"] = datetime.now().isoformat()
        f["archived_reason"] = str(reason or "")[:60]
        if by:
            f["superseded_by"] = by
        _fact_touch(f)
        _save_facts_to_disk()
        return before


def _restore_fact_image(image):
    if not isinstance(image, dict) or not image.get("id"):
        return False
    with _facts_lock:
        f = next((x for x in _facts_data if x.get("id") == image["id"]), None)
        if f is None:
            f = dict(image)
            _facts_data.append(f)
        else:
            f.clear()
            f.update(image)
        _fact_touch(f)
        _save_facts_to_disk()
    return True


FACT_GATE_MIN_SCORE = 0.25


def _fact_gate(text, kind, live):
    cands = rank_by_words(text, live, lambda f: f.get("text", ""), k=5)
    cands = [c for c in cands if c.get("score", 0) >= FACT_GATE_MIN_SCORE
             or len(c.get("why") or []) >= 2]
    if not cands:
        return None
    stored = "\n".join(f"[{c.get('id')}] {c.get('text', '')}" for c in cands)
    try:
        r = _llm_call_json(_FACT_GATE_PROMPT.format(kind=kind or "durable", new=text,
                                                    stored=stored),
                           breaker=False, quiet=True, timeout=120)
    except Exception:
        return None
    if not isinstance(r, dict):
        return None
    action = str(r.get("action") or "").strip().lower()
    target = str(r.get("target") or "").strip().strip("[]")
    if action == "new":
        return {"action": "new", "why": str(r.get("why") or "")[:160]}
    if action not in ("same", "update", "supersede") or target not in {c.get("id") for c in cands}:
        return None
    out = {"action": action, "target": target, "why": str(r.get("why") or "")[:160]}
    if action == "update":
        merged, _h = sanitize_memory_text(r.get("text"))
        if len(merged) < 5:
            return None
        out["text"] = merged
    return out


def _add_fact(text, tags=None, kind="durable", report=None, gate=True):
    report = report if isinstance(report, dict) else {}
    text, _hidden = sanitize_memory_text(text)
    if _hidden:
        print(f"[FACTS] {_hidden} invisible character(s) stripped from a "
              f"fact before storing it", file=sys.stderr)
    if not text:
        return None
    now = datetime.now().isoformat()
    text_low = text.lower()

    def _should_count_hit(existing):
        try:
            last = datetime.fromisoformat(existing.get("last_seen", existing.get("ts", "")))
            mins = (datetime.now() - last).total_seconds() / 60.0
            return mins >= FACTS_HITS_COOLDOWN_MIN
        except Exception:
            return True

    with _facts_lock:
        for existing in _facts_data:
            if _fact_live(existing) and existing.get("text", "").lower() == text_low:
                report["action"] = "same"
                if _should_count_hit(existing):
                    existing["hits"] = existing.get("hits", 1) + 1
                    existing["last_seen"] = now
                    _save_facts_to_disk()
                    print(f"[FACTS] reinforce (exact) hits={existing['hits']}: {text[:60]}")
                else:
                    print(f"[FACTS] re-save ignored (cooldown, exact): {text[:60]}")
                return existing

    with _facts_lock:
        similar = rank_by_words(text, [f for f in _facts_data if _fact_live(f)],
                                lambda f: f.get("text", ""), k=1)
        if similar and similar[0].get("score", 0) >= 0.9:
            existing = next((f for f in _facts_data
                             if f.get("id") == similar[0].get("id")), None)
            if existing is not None:
                report["action"] = "same"
                if _should_count_hit(existing):
                    existing["hits"] = existing.get("hits", 1) + 1
                    existing["last_seen"] = now
                    _save_facts_to_disk()
                    print(f"[FACTS] reinforce (words) hits={existing['hits']}: "
                          f"{existing['text'][:60]}")
                return existing

    decision = _fact_gate(text, kind, _live_facts()) if gate else None
    if decision:
        report["gated"] = True
        with _facts_lock:
            tgt = next((f for f in _facts_data if f.get("id") == decision.get("target")), None) \
                if decision.get("target") else None
        if tgt is not None and decision["action"] == "same":
            report.update(decision)
            with _facts_lock:
                if _should_count_hit(tgt):
                    tgt["hits"] = tgt.get("hits", 1) + 1
                tgt["last_seen"] = now
                _save_facts_to_disk()
            print(f"[FACTS] gate: same as [{tgt['id']}]: {text[:60]}")
            return tgt
        if tgt is not None and decision["action"] == "update":
            report.update(decision, before=dict(tgt))
            with _facts_lock:
                hist = list(tgt.get("history") or [])[-4:]
                hist.append({"text": tgt.get("text", ""), "ts": now})
                tgt["history"] = hist
                tgt["text"] = decision["text"]
                tgt["hits"] = tgt.get("hits", 1) + 1
                tgt["last_seen"] = now
                _fact_touch(tgt)
                _save_facts_to_disk()
            print(f"[FACTS] gate: updated [{tgt['id']}]: {decision['text'][:60]}")
            return tgt

    fact = {
        "id": str(uuid.uuid4())[:8],
        "text": text,
        "kind": kind if kind in ("durable", "intencion", "estado") else "durable",
        "tags": [t for t in (tags or []) if isinstance(t, str)][:10],
        "ts": now,
        "hits": 1,
        "last_seen": now,
    }
    with _facts_lock:
        _facts_data.append(fact)
        _save_facts_to_disk()
    if decision and decision.get("action") == "supersede":
        before = _archive_fact(decision["target"], "superseded", by=fact["id"])
        report.update(decision, before=before)
        print(f"[FACTS] gate: [{fact['id']}] supersedes [{decision['target']}]: {text[:60]}")
    else:
        report.setdefault("action", "new")
        print(f"[FACTS] +new: {text[:80]}")
    _memory_pressure()
    return fact


def tool_add_fact_checked(text, tags=None, kind=None):
    report = {}
    fact = _add_fact(text, tags, kind or "durable", report=report)
    if fact is None:
        return {"added": False, "error": "empty fact"}
    out = {"added": report.get("action") in ("new", "supersede", None), "fact": fact,
           "memory": report.get("action", "new")}
    if report.get("action") in ("same", "update", "supersede"):
        out["target"] = report.get("target")
        if report.get("why"):
            out["why"] = report["why"]
        return out
    try:
        with _facts_lock:
            others = [f for f in _facts_data if f.get("id") != fact.get("id") and _fact_live(f)]
        near = rank_by_words(fact.get("text", ""), others,
                              lambda f: f.get("text", ""), k=3)
        _best = max((c.get("score", 0) for c in near), default=0)
        related = [{"id": c.get("id"), "text": c.get("text", "")[:160]}
                   for c in near
                   if c.get("score", 0) > 0
                   and (c.get("score", 0) >= 0.5 * _best
                        or len(c.get("why") or []) >= 2)]
        if related and report.get("gated"):
            related = []
        if related:
            out["related_existing"] = related
            out["warning"] = ("These stored facts overlap the new one. "
                              "BEFORE you answer the user: if the new fact "
                              "SUPERSEDES one of them, delete_fact/edit_fact "
                              "the stale one NOW — two contradictory facts "
                              "ride every future turn and poison it. If "
                              "they genuinely coexist, say nothing.")
    except Exception:
        pass
    return out


def _remove_fact(fact_id):
    with _facts_lock:
        idx = next((i for i, f in enumerate(_facts_data)
                    if f.get("id") == fact_id), -1)
        if idx < 0:
            return False
        del _facts_data[idx]
        _save_facts_to_disk()
    return True


def _update_fact(fact_id, text=None, tags=None, hits=None):
    with _facts_lock:
        idx = next((i for i, f in enumerate(_facts_data) if f.get("id") == fact_id), -1)
        if idx < 0:
            return None
        fact = _facts_data[idx]
        if text is not None and text.strip() and text.strip() != fact.get("text"):
            fact["text"] = text.strip()
            _fact_touch(fact)
        if tags is not None:
            _tags = [t for t in tags if isinstance(t, str)][:10]
            if _tags != fact.get("tags"):
                fact["tags"] = _tags
                _fact_touch(fact)
        if hits is not None:
            try:
                fact["hits"] = max(1, int(hits))
            except (ValueError, TypeError):
                pass
        fact["last_seen"] = datetime.now().isoformat()
        _save_facts_to_disk()
        return fact


def rank_relevant(query, items, textof, k=5):
    return rank_by_words(query, items, textof, k)

def search_facts(query, n_results=3, min_score=None):
    if not query:
        return []
    all_facts = _live_facts()
    out = rank_by_words(query, all_facts, lambda f: f.get("text", ""), k=n_results)
    _mark_facts_recalled([h.get("id") for h in out])
    return out


def _mark_facts_recalled(ids):
    if not ids:
        return
    now = datetime.now()
    changed = False
    with _facts_lock:
        for f in _facts_data:
            if f.get("id") not in ids:
                continue
            try:
                last = datetime.fromisoformat(f.get("last_recalled", ""))
                if (now - last).total_seconds() / 60 < FACTS_HITS_COOLDOWN_MIN:
                    continue
            except Exception:
                pass
            f["recalls"] = int(f.get("recalls", 0)) + 1
            f["last_recalled"] = now.isoformat()
            changed = True
    if changed:
        _save_facts_to_disk()


def _fact_recency_weight(last_seen):
    try:
        seen = datetime.fromisoformat(last_seen)
        days = max(0.0, (datetime.now() - seen).total_seconds() / 86400.0)
        return 1.0 / (1.0 + days / 30.0)
    except Exception:
        return 0.5


FACTS_CATALOGUE_BYTES = 12000


def facts_catalogue(budget=None):
    budget = budget or FACTS_CATALOGUE_BYTES
    try:
        sql = _get_sql()
        if sql is not None:
            _en_base = sql.count("facts")
            if _en_base != len(_facts_data):
                print(f"[FACTS] memoria {len(_facts_data)} ≠ base {_en_base} "
                      f"→ recargando", file=sys.stderr)
                init_facts()
    except Exception as _e:
        print(f"[FACTS] could not check the store: {_e}", file=sys.stderr)
    all_facts = _live_facts()
    if not all_facts:
        return "", 0, 0
    _weight = lambda f: ((f.get("hits", 1) + f.get("recalls", 0))
                         * _fact_recency_weight(
                             f.get("last_seen", f.get("ts", ""))))
    _stable = sorted(all_facts, key=lambda f: (f.get("ts", ""),
                                               f.get("id", "")))
    if sum(len((f.get("text") or "").strip()) + 40 for f in _stable) <= budget:
        all_facts = _stable
    else:
        all_facts.sort(key=_weight, reverse=True)
    def _age(f):
        try:
            return (datetime.now()
                    - datetime.fromisoformat(f.get("ts", ""))).days
        except Exception:
            return None

    lines, used, inside = [], 0, 0
    for f in all_facts:
        t_ = (f.get("text", "") or "").strip().replace("\n", " ")
        if not t_:
            continue
        _k = f.get("kind", "durable")
        _days = _age(f) if _k in ("intencion", "estado") else None
        _mark = ""
        if _days is not None and _days >= 30:
            _mark = (f"   [{_k}, {_days // 30} month(s) ago — ask before "
                     f"taking it as current]")
        elif _k == "intencion":
            _mark = "   [intent]"
        line_ = f"  • [{f.get('id', '?')}] {t_}{_mark}"
        if used + len(line_) > budget and inside:
            break
        lines.append(line_)
        used += len(line_) + 1
        inside += 1
    return "\n".join(lines), inside, len(all_facts) - inside


FACTS_RELEVANT_BYTES = 2500


def facts_relevant(query, shown_ids, since=""):
    rest = [f for f in _live_facts() if f.get("id") not in shown_ids]
    if not rest:
        return []
    fresh = [f for f in rest if since and (f.get("ts") or "") > since]
    picked = {f["id"]: f for f in fresh}
    if query:
        for h in rank_by_words(str(query)[:600], [f for f in rest if f["id"] not in picked],
                               lambda f: f.get("text", ""), k=12):
            if h.get("score", 0) > 0:
                picked.setdefault(h["id"], next(f for f in rest if f["id"] == h["id"]))
    out, used = [], 0
    for f in picked.values():
        line = f"  • [{f.get('id')}] {(f.get('text') or '').strip()}"
        if used + len(line) > FACTS_RELEVANT_BYTES and out:
            break
        out.append(line)
        used += len(line) + 1
    return out


def select_facts_for_injection(query, budget=None):
    budget = budget or FACTS_INJECT_BUDGET
    all_facts = _live_facts()
    if not all_facts:
        return []
    pinned = [f for f in all_facts if f.get("hits", 1) >= FACTS_PIN_THRESHOLD]
    rest = [f for f in all_facts if f.get("hits", 1) < FACTS_PIN_THRESHOLD]
    _peso = lambda f: (f.get("hits", 1)
                       * _fact_recency_weight(f.get("last_seen", f.get("ts", ""))))
    pinned.sort(key=_peso, reverse=True)
    sel = [{**f, "_pinned": True} for f in pinned[:budget]]
    gap = budget - len(sel)
    if gap > 0 and rest and query:
        sel += rank_by_words(query[:500], rest, lambda f: f.get("text", ""),
                             k=gap)
    return sel

def _save_lessons_to_disk():
    try:
        sql = _get_sql()
        if sql is not None:
            sql.replace_all("lessons", [(l.get("id", str(i)), l)
                                        for i, l in enumerate(_lessons_data)])
            sql.meta_set("init:LESSONS", datetime.now().isoformat())
        else:
            os.makedirs(hoplita_base(), exist_ok=True)
            with open(LESSONS_META_PATH, "w", encoding="utf-8") as f:
                json.dump({"lessons": _lessons_data}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[LESSONS] ERROR writing to disk ({LESSONS_META_PATH}): {e}", file=sys.stderr)


def init_lessons():
    global _lessons_data
    try:
        sql = _get_sql()
        if sql is None:
            _lessons_data = []
            print("[MCP] Lessons: new (empty)")
            return
        _lessons_data = sorted(sql.load("lessons"),
                               key=lambda l: l.get("created", ""))
        active = sum(1 for l in _lessons_data if not l.get("stale"))
        print(f"[MCP] Lessons (SQLCipher): {len(_lessons_data)} total "
              f"({active} active)")
    except Exception as e:
        print(f"[MCP] ⚠ Lessons load: {e}")

def _llm_max_tokens():
    return CONFIG.getint("CHAT", "max_tokens", fallback=8000)


_LLM_CALL_FAILS = {"count": 0, "limit": 3, "disabled": False,
                   "last_error": "", "since": ""}


_TOKENS_LOCK = threading.Lock()
_TOKENS_STORE = "token_usage"
_TOKENS_KEEP_DAYS = 400
_tokens_cache = None


def _tokens_load():
    global _tokens_cache
    if _tokens_cache is not None:
        return _tokens_cache
    _tokens_cache = {}
    try:
        sql = _get_sql()
        if sql is not None:
            legacy = []
            for r in sql.load(_TOKENS_STORE):
                if not (isinstance(r, dict) and r.get("model")):
                    continue
                if r.get("day"):
                    _tokens_cache[(r["model"], r["day"])] = {
                        "calls": int(r.get("calls", 0)),
                        "prompt": int(r.get("prompt", 0)),
                        "completion": int(r.get("completion", 0))}
                else:
                    legacy.append(r)
            if legacy:
                day0 = ((sql.meta_get("tokens:since") or "")[:10]
                        or datetime.now().strftime("%Y-%m-%d"))
                for r in legacy:
                    k = (r["model"], day0)
                    row = _tokens_cache.setdefault(
                        k, {"calls": 0, "prompt": 0, "completion": 0})
                    row["calls"] += int(r.get("calls", 0))
                    row["prompt"] += int(r.get("prompt", 0))
                    row["completion"] += int(r.get("completion", 0))
                    sql.delete(_TOKENS_STORE, r["model"])
                    sql.upsert(_TOKENS_STORE, f"{r['model']}|{day0}",
                               dict(row, model=r["model"], day=day0))
    except Exception:
        pass
    return _tokens_cache


_tokens_ids = None


def _tokens_ids_load():
    global _tokens_ids
    if _tokens_ids is None:
        _tokens_ids = {}
        try:
            sql = _get_sql()
            raw = (sql.meta_get("tokens:ids") if sql is not None else "") or ""
            _tokens_ids = dict(json.loads(raw)) if raw else {}
        except Exception:
            _tokens_ids = {}
    return _tokens_ids


def _count_tokens(model, usage, sent=""):
    try:
        model = (model or "").strip() or "?"
        sent = (sent or "").strip()
        if sent and sent != model:
            with _TOKENS_LOCK:
                ids = _tokens_ids_load()
                if ids.get(model) != sent:
                    ids[model] = sent
                    sql = _get_sql()
                    if sql is not None:
                        sql.meta_set("tokens:ids", json.dumps(ids, ensure_ascii=False))
        u = usage or {}
        p = int(u.get("prompt_tokens") or 0)
        c = int(u.get("completion_tokens") or 0)
        if p <= 0 and c <= 0:
            return
        day = datetime.now().strftime("%Y-%m-%d")
        with _TOKENS_LOCK:
            cache = _tokens_load()
            row = cache.setdefault((model, day),
                                   {"calls": 0, "prompt": 0, "completion": 0})
            row["calls"] += 1
            row["prompt"] += p
            row["completion"] += c
            sql = _get_sql()
            if sql is not None:
                sql.upsert(_TOKENS_STORE, f"{model}|{day}",
                           dict(row, model=model, day=day))
                if not (sql.meta_get("tokens:since") or ""):
                    sql.meta_set("tokens:since", _chat_now_iso())
                _floor = (datetime.now()
                          - timedelta(days=_TOKENS_KEEP_DAYS)
                          ).strftime("%Y-%m-%d")
                for (m_, d_) in [k for k in cache if k[1] < _floor]:
                    cache.pop((m_, d_), None)
                    sql.delete(_TOKENS_STORE, f"{m_}|{d_}")
    except Exception:
        pass


def _tokens_model_names():
    names = {}
    try:
        for sec in (CONFIG.sections() if CONFIG else []):
            if not sec.lower().startswith("model:"):
                continue
            mid = (_resolve_endpoint(sec)[2] or "").strip()
            if mid:
                names.setdefault(mid, sec.split(":", 1)[1].strip())
        with _TOKENS_LOCK:
            ids = dict(_tokens_ids_load())
        for answered, sent in ids.items():
            if answered not in names and sent in names:
                names[answered] = names[sent]
    except Exception:
        pass
    return names


def tokens_stats():
    with _TOKENS_LOCK:
        data = {k: dict(v) for k, v in _tokens_load().items()}
    per_model = {}
    days = []
    for (m, d), v in data.items():
        agg = per_model.setdefault(
            m, {"calls": 0, "prompt": 0, "completion": 0})
        for k in ("calls", "prompt", "completion"):
            agg[k] += v[k]
        days.append(dict(v, model=m, day=d,
                         total=v["prompt"] + v["completion"]))
    names = _tokens_model_names()
    with _TOKENS_LOCK:
        ids = dict(_tokens_ids_load())
    for r in days:
        r["name"] = names.get(r["model"], "")
        r["sent"] = ids.get(r["model"], "")
    rows = [dict(v, model=m, name=names.get(m, ""), sent=ids.get(m, ""),
                 total=v["prompt"] + v["completion"])
            for m, v in per_model.items()]
    rows.sort(key=lambda r: r["total"], reverse=True)
    days.sort(key=lambda r: (r["day"], r["model"]))
    since = ""
    try:
        sql = _get_sql()
        if sql is not None:
            since = sql.meta_get("tokens:since") or ""
    except Exception:
        pass
    return {"since": since, "models": rows, "days": days,
            "total": sum(r["total"] for r in rows)}


def tokens_reset():
    global _tokens_cache
    with _TOKENS_LOCK:
        n = len({m for (m, _d) in _tokens_load()})
        _tokens_cache = {}
        try:
            sql = _get_sql()
            if sql is not None:
                for r in sql.load(_TOKENS_STORE):
                    if isinstance(r, dict) and r.get("model"):
                        sql.delete(
                            _TOKENS_STORE,
                            f"{r['model']}|{r['day']}" if r.get("day")
                            else r["model"])
                sql.meta_set("tokens:since", _chat_now_iso())
        except Exception:
            pass
    return {"reset": True, "models_cleared": n}


def _llm_breaker_rearm(why=""):
    was = bool(_LLM_CALL_FAILS.get("disabled"))
    prev = _LLM_CALL_FAILS.get("last_error", "")
    _LLM_CALL_FAILS["count"] = 0
    _LLM_CALL_FAILS["disabled"] = False
    if was:
        print(f"[LESSONS] breaker rearmed ({why or 'explicit pass'}); "
              f"it had tripped on: {prev or 'unknown'}", file=sys.stderr)
    return {"was_tripped": was, "previous_error": prev} if was else None


def _extract_json_block(text, last=False):
    hits = []
    starts = sorted(
        (i0, op, cl) for op, cl in (("{", "}"), ("[", "]"))
        for i0 in [text.find(op)] if i0 != -1)
    if last:
        starts = sorted(
            (i, op, cl) for op, cl in (("{", "}"), ("[", "]"))
            for i, ch in enumerate(text) if ch == op)
    _skip_until = -1
    for i0, opener, closer in starts:
        if i0 <= _skip_until:
            continue
        depth = 0
        for i in range(i0, len(text)):
            if text[i] == opener: depth += 1
            elif text[i] == closer:
                depth -= 1
                if depth == 0:
                    try:
                        hits.append(json.loads(text[i0:i + 1]))
                        _skip_until = i
                    except Exception:
                        pass
                    break
        if hits and not last:
            return hits[0]
    return hits[-1] if hits else None


_JSON_CALLERS = {"pick_skill_for": "SKILL-PICK", "_pick": "ESCALATE",
                 "_extract_lessons_from_experience": "LESSONS",
                 "_decide_lesson_action": "LESSONS", "curate_memory": "MEMORY-CURATE",
                 "curate_lessons": "LESSONS-MERGE", "curate_fact_dups": "FACTS-DEDUP"}


_NO_THINK_EXTRA = {"response_format": {"type": "json_object"},
                   "chat_template_kwargs": {"enable_thinking": False}}
_EXTRA_REJECTED = {}


def _norm_base(base_url):
    return (base_url or "").rstrip("/").lower()


def _llm_call_json(prompt, max_tokens=None, timeout=60, breaker=True,
                   _grew=False, _who=None, quiet=False, _follow=None,
                   system=None, endpoint=None, usage_sink=None):
    if breaker and _LLM_CALL_FAILS["disabled"]:
        return None
    if _who is None:
        try:
            _caller = sys._getframe(1).f_code.co_name
        except Exception:
            _caller = ""
        _who = _JSON_CALLERS.get(_caller, (_caller or "side-call").upper().strip("_"))
    try:
        base_url, api_key, model = endpoint or _resolve_endpoint("MEMORY")
    except Exception as _e:
        print(f"[{_who}] no LLM endpoint: {_e}")
        return None
    if not model:
        return None
    if max_tokens is None:
        max_tokens = _llm_max_tokens()
    _json_rule = ("Respond DIRECTLY with valid pure JSON. "
                  "Do NOT include intermediate reasoning. "
                  "Do NOT use <think> tags, markdown, or prose before or after. "
                  "Only the JSON, ready for json.loads().")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content":
                (f"{system.rstrip()}\n\n{_json_rule}" if system else _json_rule)},
            {"role": "user", "content": prompt},
        ] + list(_follow or []),
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }

    def _ask_again(reasoning_text, what):
        if not quiet:
            print(f"[{_who}] {what} (finish_reason={finish}, "
                  f"{len(reasoning_text)} chars of reasoning) — asking once "
                  f"more for the object itself.")
        return _llm_call_json(
            prompt, max_tokens=max(max_tokens, 2000), timeout=timeout,
            breaker=breaker, _grew=True, _who=_who, quiet=quiet, system=system,
            endpoint=endpoint, usage_sink=usage_sink,
            _follow=[{"role": "assistant", "content": reasoning_text[:6000]},
                     {"role": "user", "content":
                      "That was your reasoning, not an answer. Write ONLY the "
                      "JSON object now — no reasoning, no prose, nothing "
                      "before or after it."},
                     {"role": "assistant", "content": "{"}])

    def _trip(why=""):
        if why:
            _LLM_CALL_FAILS["last_error"] = str(why)[:300]
        if not breaker:
            return
        _LLM_CALL_FAILS["count"] += 1
        if _LLM_CALL_FAILS["count"] >= _LLM_CALL_FAILS["limit"]:
            _LLM_CALL_FAILS["disabled"] = True
            _LLM_CALL_FAILS["since"] = _chat_now_iso()
            print(f"[{_who}] Circuit breaker: {_LLM_CALL_FAILS['count']} failures "
                  f"in a row → helper disabled for the REST OF THIS PASS. "
                  f"The next explicit pass rearms it. "
                  f"Likely cause: model stuck in thinking, max_tokens too short, "
                  f"or a buggy LLM server. Check the messages above.")

    try:
        raw = _llm_chat_with_tools(payload["messages"], None, timeout=timeout,
                                   base_url=base_url, api_key=api_key,
                                   model=model, temperature=0.2,
                                   max_tokens=max_tokens,
                                   extra=_NO_THINK_EXTRA)
        raw_json = {"usage": (raw or {}).get("usage") or {}}
        if usage_sink is not None:
            try:
                usage_sink(raw_json["usage"])
            except Exception:
                pass
        msg = (raw or {}).get("message") or {}
        finish = (raw or {}).get("finish_reason") or "?"
        if _follow and _follow[-1].get("role") == "assistant" \
                and isinstance(msg.get("content"), str) and msg.get("content"):
            _pre = str(_follow[-1].get("content") or "")
            if _pre and not msg["content"].lstrip().startswith(_pre):
                msg = {**msg, "content": _pre + msg["content"]}
        content_raw = msg.get("content", "") or ""
        reasoning = msg.get("reasoning_content", "") or msg.get("reasoning", "") or ""

        def _cut_retry(what):
            _mt = max(max_tokens * 2,
                      max_tokens + len(reasoning or content_raw) // 3)
            if not quiet:
                print(f"[{_who}] {what} was CUT at max_tokens={max_tokens} "
                  f"(finish_reason=length) — retrying once at {_mt}.")
            return _llm_call_json(prompt, max_tokens=_mt,
                                  timeout=timeout, breaker=breaker, _grew=True,
                                  _who=_who, quiet=quiet, system=system,
                                  endpoint=endpoint, usage_sink=usage_sink)

        if not content_raw and not reasoning:
            usage = raw_json.get("usage", {})
            other_keys = [k for k in msg.keys() if k not in ("role", "content", "tool_calls")]
            if not quiet:
                print(f"[{_who}] LLM content empty. finish_reason={finish} "
                  f"usage={usage} other_keys={other_keys}")
            if finish == "length" and not _grew:
                return _cut_retry("the whole budget went to reasoning: it")
            _trip(f"empty answer (finish_reason={finish})")
            return None
        if not content_raw and reasoning:
            _rescued = _extract_json_block(reasoning, last=True)
            if _rescued is not None:
                print(f"[{_who}] answer rescued from reasoning_content "
                      f"({len(reasoning)} chars, no visible response).")
                _LLM_CALL_FAILS["count"] = 0
                return _rescued
            if not quiet:
                print(f"[{_who}] LLM only reasoning ({len(reasoning)} chars in "
                  f"reasoning_content), no visible response.")
            if finish == "length" and not _grew:
                return _cut_retry("the answer")
            if not _grew:
                return _ask_again(reasoning, "the model only reasoned")
            _trip("the model only produced reasoning, no answer")
            return None

        content = re.sub(r"<think>.*?</think>", "", content_raw, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r"<think>.*$", "", content, flags=re.DOTALL | re.IGNORECASE)
        content = content.strip()

        if not content:
            _rescued = _extract_json_block(content_raw, last=True)
            if _rescued is not None:
                print(f"[{_who}] answer rescued from inside <think> "
                      f"({len(content_raw)} chars, nothing after).")
                _LLM_CALL_FAILS["count"] = 0
                return _rescued
            raw_len = len(content_raw)
            if not quiet:
                print(f"[{_who}] LLM generated {raw_len} chars but ALL was internal reasoning "
                  f"(<think> tags) with no JSON after. The model didn't complete the "
                  f"response — raise max_tokens or use a less verbose model for lessons.")
            if finish == "length" and not _grew:
                return _cut_retry("the answer")
            if not _grew:
                return _ask_again(content_raw, "all of it was <think>")
            _trip("all of it was <think>: raise max_tokens or use a less "
                  "verbose model")
            return None

        parsed = _extract_json_block(content)
        if parsed is not None:
            _LLM_CALL_FAILS["count"] = 0
            return parsed
        if finish == "length" and not _grew:
            return _cut_retry("the JSON")
        if not quiet:
            print(f"[{_who}] LLM didn't return parseable JSON "
              f"(finish_reason={finish}): {content[:200]!r}")
        _trip(f"unparseable JSON (finish_reason={finish}): {content[:120]!r}")
        return None
    except requests.exceptions.Timeout:
        if not quiet:
            print(f"[{_who}] LLM timeout {timeout}s")
        _trip(f"timeout after {timeout}s")
        return None
    except Exception as e:
        if not quiet:
            print(f"[{_who}] LLM error: {e}")
        _trip(str(e))
        return None


_LAST_WIRE = {}
_LAST_WIRE_MAX = 32


def _compact_timeout():
    chat_t = CONFIG.getint("CHAT", "timeout", fallback=120) if CONFIG else 120
    own = CONFIG.getint("CHAT", "compact_timeout", fallback=0) if CONFIG else 0
    return own if own > 0 else max(900, chat_t)


_VISION_DESC = None
_VISION_LOCK = threading.Lock()
_VISION_PROMPT = (
    "Describe this image for a model that cannot see it and must work from "
    "your words alone. Everything that matters: all visible text verbatim, "
    "numbers, the elements of an interface and where they are, people, "
    "objects, colours that carry meaning, the layout. Complete and precise; "
    "no preamble, no opinion.")


def _background_model_name():
    import hoplita_llmcfg as _lc
    named = ((CONFIG.get("MEMORY", "model", fallback="") or "").strip()
             if CONFIG is not None and CONFIG.has_section("MEMORY") else "")
    if named:
        return named
    return _lc.base_alias(CONFIG) or _resolve_endpoint("API")[2] or "?"


def _vision_describer(blind_model=""):
    import hoplita_llmcfg as _lc
    if CONFIG is None:
        return None
    want = (CONFIG.get("CHAT", "vision_model", fallback="") or "").strip()
    if want.lower() == "none":
        return None
    cands = []
    if want:
        sec = _lc.model_section(CONFIG, want)
        if sec:
            cands.append(_resolve_endpoint(sec))
    else:
        cands.append(_resolve_endpoint("API"))
        for _n, m in _model_pool().items():
            cands.append((m["base_url"], m["api_key"], m["model"]))
    for u, k, m in cands:
        if m and m.strip() != (blind_model or "").strip() and _lc.model_sees(CONFIG, u, m):
            return u, k, m
    return None


def _vision_cache():
    global _VISION_DESC
    if _VISION_DESC is None:
        try:
            p = os.path.join(hoplita_base(), "vision_descriptions.json")
            with open(p, encoding="utf-8") as fh:
                _VISION_DESC = json.load(fh)
        except Exception:
            _VISION_DESC = {}
    return _VISION_DESC


def _vision_cache_save():
    try:
        d = _vision_cache()
        if len(d) > 500:
            for k_ in list(d)[:len(d) - 500]:
                d.pop(k_, None)
        p = os.path.join(hoplita_base(), "vision_descriptions.json")
        with open(p + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False)
        os.replace(p + ".tmp", p)
    except Exception as e:
        print(f"[VISION] could not keep the descriptions: {e}", file=sys.stderr)


def _describe_images_for(messages, blind_model):
    desc = _vision_describer(blind_model)
    if not desc:
        return messages, 0
    d_url, d_key, d_model = desc
    out, n_new, n = [], 0, 0
    for m in messages or []:
        c = m.get("content") if isinstance(m, dict) else None
        if not (isinstance(c, list) and any(isinstance(p, dict) and "image" in str(p.get("type", ""))
                                            for p in c)):
            out.append(m)
            continue
        parts = []
        for p in c:
            if not (isinstance(p, dict) and "image" in str(p.get("type", ""))):
                parts.append(p)
                continue
            key = hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()
            with _VISION_LOCK:
                text = _vision_cache().get(key)
            if text is None:
                try:
                    r = _llm_chat_with_tools(
                        [{"role": "user", "content": [{"type": "text", "text": _VISION_PROMPT}, p]}],
                        None, base_url=d_url, api_key=d_key, model=d_model)
                    text = ((r or {}).get("message") or {}).get("content") or ""
                    text = text.strip()
                except Exception as e:
                    print(f"[VISION] {d_model} could not describe a picture: {e}", file=sys.stderr)
                    text = ""
                if text:
                    with _VISION_LOCK:
                        _vision_cache()[key] = text
                        _vision_cache_save()
                    n_new += 1
            if text:
                parts.append({"type": "text", "text":
                              f"[image described by {d_model} for {blind_model}, which "
                              f"cannot see images]\n{text}"})
                n += 1
            else:
                parts.append(p)
        out.append(dict(m, content=parts))
    if n_new:
        try:
            _ctx = getattr(_ORCH, "ctx", None)
            if _ctx is not None:
                _ctx.emit({"event": "image_described", "by": d_model,
                           "for": blind_model, "n": n_new})
        except Exception:
            pass
    return out, n


_SERVER_VISION = {}


def _server_says_blind(base_url, api_key, model):
    key = f"{base_url}|{model}"
    now = time.time()
    hit = _SERVER_VISION.get(key)
    if hit and now - hit[0] < 120:
        return hit[1] is False
    sees = None
    try:
        root = base_url[:-3] if base_url.rstrip("/").endswith("/v1") else base_url
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        r = _http().get(root.rstrip("/") + "/props", headers=headers, timeout=4)
        if r.status_code == 200:
            mod = (r.json() or {}).get("modalities")
            if isinstance(mod, dict) and "vision" in mod:
                sees = bool(mod.get("vision"))
    except Exception:
        sees = None
    _SERVER_VISION[key] = (now, sees)
    return sees is False


_OLLAMA_WINDOW = {}
_NOT_OLLAMA = {}


def _ollama_window(base_url, api_key, model):
    key = f"{base_url}|{model}"
    now = time.time()
    hit = _OLLAMA_WINDOW.get(key)
    if hit and now - hit[0] < 60:
        return hit[1]
    root = base_url[:-3] if base_url.rstrip("/").endswith("/v1") else base_url
    root = root.rstrip("/")
    if _NOT_OLLAMA.get(root):
        return 0
    window = 0
    try:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        _v = _http().get(root + "/api/version", headers=headers, timeout=4)
        try:
            _is_ollama = _v.status_code == 200 and "version" in (_v.json() or {})
        except Exception:
            _is_ollama = False
        if not _is_ollama:
            _NOT_OLLAMA[root] = True
        if _is_ollama:
            for attempt in (0, 1):
                ps = _http().get(root + "/api/ps", headers=headers, timeout=4).json() or {}
                own = next((m for m in ps.get("models") or []
                            if model in (m.get("name"), m.get("model"))), None)
                if own is not None:
                    window = int(own.get("context_length") or 0)
                    break
                if attempt == 0:
                    _http().post(root + "/api/generate", json={"model": model},
                                 headers=headers, timeout=300)
    except Exception:
        window = 0
    _OLLAMA_WINDOW[key] = (now, window)
    return window


_SERVER_TOOLS = {}


def _server_says_no_tools(base_url, api_key, model):
    key = f"{base_url}|{model}"
    now = time.time()
    hit = _SERVER_TOOLS.get(key)
    if hit and now - hit[0] < 120:
        return hit[1] is False
    takes = None
    try:
        root = base_url[:-3] if base_url.rstrip("/").endswith("/v1") else base_url
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        r = _http().post(root.rstrip("/") + "/api/show", json={"model": model},
                         headers=headers, timeout=4)
        if r.status_code == 200:
            caps = (r.json() or {}).get("capabilities")
            if isinstance(caps, list):
                takes = "tools" in caps
    except Exception:
        takes = None
    _SERVER_TOOLS[key] = (now, takes)
    return takes is False


class ModelTakesNoTools(Exception):
    no_retry = True


class PromptCannotFit(Exception):
    no_retry = True


def _check_fits_ollama(base_url, api_key, model, messages, tools, max_tokens):
    window = _ollama_window(base_url, api_key, model)
    if window <= 0:
        return
    prompt = _estimate_prompt_tokens(messages, tools)
    reply = min(int(max_tokens or 0), window // 2)
    if prompt + reply <= window:
        return
    fixed = _estimate_prompt_tokens([m for m in messages if m.get("role") == "system"], tools)
    if fixed + reply > window:
        raise PromptCannotFit(
            f"Ollama runs {model} with a {window}-token window, and HOPLITA's "
            f"instructions and tools alone are about {fixed} tokens: Ollama would "
            f"cut them and the model would answer without them. Give it a bigger "
            f"window — 32768 or more — in the Ollama app (Settings → Context "
            f"length) or with OLLAMA_CONTEXT_LENGTH=32768 where Ollama starts, "
            f"then restart Ollama.")
    raise ContextOverflow(prompt, reply, window)


def _park_images(messages, model, why):
    out, paths = [], []
    for m in messages or []:
        c = m.get("content") if isinstance(m, dict) else None
        if not (isinstance(c, list) and any(isinstance(p, dict) and "image" in str(p.get("type", ""))
                                            for p in c)):
            out.append(m)
            continue
        parts = []
        for p in c:
            if not (isinstance(p, dict) and "image" in str(p.get("type", ""))):
                parts.append(p)
                continue
            path = ""
            try:
                url = str(((p.get("image_url") or {}).get("url")) or "")
                head, _, b64 = url.partition(",")
                if head.startswith("data:") and b64:
                    ext = {"image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}.get(
                        head[5:].split(";")[0], ".png")
                    raw = base64.b64decode(b64)
                    d = os.path.join(hoplita_base(), "attachments")
                    os.makedirs(d, exist_ok=True)
                    path = os.path.join(d, hashlib.sha256(raw).hexdigest()[:24] + ext)
                    if not os.path.exists(path):
                        with open(path, "wb") as fh:
                            fh.write(raw)
            except Exception as e:
                print(f"[VISION] could not keep a picture as a file: {e}", file=sys.stderr)
                path = ""
            if path:
                paths.append(path)
                parts.append({"type": "text", "text":
                              f"[picture not shown to you: {model} {why}. It is saved at "
                              f"{path} — do not look for it anywhere else. Tell the person "
                              f"you cannot see pictures; when a tool can do what they asked "
                              f"from the file (OCR, for example), use it on that path.]"})
            else:
                parts.append(p)
        out.append(dict(m, content=parts))
    return out, paths


def _llm_chat_with_tools(messages, tools, timeout=None,
                         base_url=None, api_key=None, model=None,
                         temperature=None, max_tokens=None, extra=None):
    if not (base_url and model):
        base_url, api_key, model = _resolve_endpoint("API")
    if not model:
        raise Exception("No model configured in [API]")
    if timeout is None:
        timeout = CONFIG.getint("CHAT", "timeout", fallback=120) if CONFIG else 120
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    import fed_agent as _fa
    messages = _fa.single_system(messages)
    try:
        import hoplita_llmcfg as _lc
        _has_img = any(isinstance(m, dict) and isinstance(m.get("content"), list)
                       and any(isinstance(p, dict) and "image" in str(p.get("type", ""))
                               for p in m["content"]) for m in messages)
        _why = ""
        if _has_img and CONFIG is not None:
            if not _lc.model_sees(CONFIG, base_url, model):
                _why = "is declared without vision"
            elif _server_says_blind(base_url, api_key, model):
                _why = "is served without vision (its server says so)"
        if _why:
            messages, _n_desc = _describe_images_for(messages, model)
            messages, _paths = _park_images(messages, model, _why)
            messages, _n_img = _fa.strip_images(messages, model)
            print(f"[VISION] {model} {_why}: {_n_desc} image(s) described, "
                  f"{len(_paths)} kept as a file, {_n_img} replaced by a note")
            _ctx = getattr(_ORCH, "ctx", None)
            if _paths and _ctx is not None and not getattr(_ctx, "_told_not_seen", False):
                _ctx._told_not_seen = True
                try:
                    _ctx.emit({"event": "image_not_seen", "model": model,
                               "server": _why.startswith("is served"),
                               "path": _paths[-1], "n": len(_paths)})
                except Exception:
                    pass
    except Exception as _e:
        print(f"[VISION] could not check images for {model}: {_e}", file=sys.stderr)
    global _LAST_WIRE
    if tools:
        try:
            _conv = _turn_conv() or "-"
            _prev = _LAST_WIRE.get(_conv) or {}
            _div = _fa.prefix_divergence(_prev.get("messages"), _prev.get("tools"),
                                         messages, tools)
            if _prev.get("messages") is not None:
                if _div:
                    print(f"[PREFIX] broke ({model}): {_div}")
                else:
                    print(f"[PREFIX] kept ({model}): {len(_prev['messages'])} messages shared, "
                          f"+{len(messages) - len(_prev['messages'])} new")
            _LAST_WIRE.pop(_conv, None)
            _LAST_WIRE[_conv] = {"messages": [dict(m) for m in messages], "tools": tools}
            while len(_LAST_WIRE) > _LAST_WIRE_MAX:
                _LAST_WIRE.pop(next(iter(_LAST_WIRE)))
        except Exception as _e:
            print(f"[PREFIX] could not compare: {_e}", file=sys.stderr)
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.7 if temperature is None else temperature,
        "max_tokens": int(max_tokens) if max_tokens else _llm_max_tokens(),
    }
    if tools:
        payload["tools"] = tools
    _merge_extra_body(payload, base_url, model)
    _extra_used = False
    if extra and not _EXTRA_REJECTED.get(_norm_base(base_url)):
        for _k, _v in dict(extra).items():
            if isinstance(_v, dict) and isinstance(payload.get(_k), dict):
                payload[_k] = {**payload[_k], **_v}
            else:
                payload[_k] = _v
        _extra_used = True
    _check_fits_ollama(base_url, api_key, model, messages, tools, payload["max_tokens"])
    r = _http().post(f"{base_url}/chat/completions",
                      json=payload, headers=headers, timeout=timeout)
    if _extra_used and r.status_code == 400 and not _context_overflow_of(r.text or ""):
        _EXTRA_REJECTED[_norm_base(base_url)] = (r.text or "")[:200]
        print(f"[LLM] {base_url} rejected the per-call extra fields "
              f"({list(extra)}): {(r.text or '')[:120]!r} — repeating without "
              f"them and not sending them to this endpoint again.",
              file=sys.stderr)
        for _k in dict(extra):
            payload.pop(_k, None)
        _merge_extra_body(payload, base_url, model)
        r = _http().post(f"{base_url}/chat/completions",
                          json=payload, headers=headers, timeout=timeout)
    if r.status_code != 200:
        _ov = _context_overflow_of(r.text or "")
        if _ov:
            raise ContextOverflow(*_ov)
    if r.status_code != 200:
        body = r.text[:600]
        if r.status_code == 400 and "does not support tools" in body:
            raise ModelTakesNoTools(
                f"{model} takes no tools (its server says so), and this turn needs "
                f"them. Pick a model that takes tools for it; this one can still "
                f"describe pictures or answer without tools. Server: {body[:160]}")
        if ("parse tool call arguments" in body or "missing closing quote" in body
                or "parse_error" in body):
            raise Exception(
                f"LLM output TRUNCATED mid tool-call (hit [CHAT] max_tokens="
                f"{_llm_max_tokens()}). Send big content in chunks — render_html "
                f"accepts append=true — or raise max_tokens. Server: {body[:160]}")
        raise Exception(f"LLM HTTP {r.status_code}: {body[:200]}")
    data = r.json()
    choice = data.get("choices", [{}])[0]
    usage = data.get("usage", {}) or {}
    if not usage.get("prompt_tokens"):
        usage = dict(usage)
        usage["prompt_tokens"] = _estimate_prompt_tokens(messages, tools)
        usage["estimated"] = True
    _count_tokens(data.get("model") or model, usage, sent=model)
    return {"message": choice.get("message", {}) or {},
            "usage": usage,
            "finish_reason": choice.get("finish_reason", "") or ""}


def _estimate_prompt_tokens(messages, tools=None):
    chars = 0
    for m in (messages or []):
        c = m.get("content")
        if isinstance(c, str):
            chars += len(c)
        elif isinstance(c, list):
            for p in c:
                if isinstance(p, dict) and p.get("type") == "text":
                    chars += len(p.get("text", ""))
        chars += 8
    if tools:
        try:
            chars += len(json.dumps(tools))
        except Exception:
            pass
    return max(1, chars // 4)


def _extract_lessons_from_experience(goal, steps, result, feedback, tags):
    steps_text = "\n".join(
        f"  [{i + 1}] {s.get('action', '?')}: {s.get('detail', '')}"
        for i, s in enumerate(steps[:10]))
    prompt = _t("prompts.lesson_extractor", goal=goal, feedback=feedback,
                tags=','.join(tags or []), steps=steps_text, result=result[:400])
    result_data = _llm_call_json(prompt, timeout=60)
    if isinstance(result_data, dict):
        if "lessons" in result_data and isinstance(result_data["lessons"], list):
            result_data = result_data["lessons"]
        elif "text" in result_data:
            result_data = [result_data]
        else:
            print(f"[LESSONS] LLM returned dict with unrecognized structure")
            return []
    if not isinstance(result_data, list):
        print(f"[LESSONS] LLM returned unexpected type: {type(result_data).__name__}")
        return []
    if not result_data:
        print(f"[LESSONS] LLM judged there were no generalizable lessons")
        return []
    valid = []
    for item in result_data[:3]:
        if not isinstance(item, dict):
            continue
        text = (item.get("text") or "").strip()[:200]
        if not text:
            continue
        valid.append({
            "text": text,
            "tags": [t for t in (item.get("tags") or []) if isinstance(t, str)][:5],
            "confidence": max(0.0, min(1.0, float(item.get("confidence", 0.5)))),
        })
    if valid:
        print(f"[LESSONS] Extracted {len(valid)} from '{goal[:50]}'")
    else:
        print(f"[LESSONS] LLM responded but no lesson was valid")
    return valid


def _decide_lesson_action(new_lesson, existing_lesson):
    prompt = f"""Compare these two lessons learned by an AI agent:

EXISTING: "{existing_lesson['text']}"  (confidence={existing_lesson.get('confidence', 0.5):.2f})
NEW:      "{new_lesson['text']}"  (confidence={new_lesson.get('confidence', 0.5):.2f})

What is the relationship between them?
- "reaffirm" = the new one says the same as the existing one (reinforces, adds no new info)
- "refine"   = the new one adds nuance/detail compatible with the existing one (enriches)
- "refute"   = the new one contradicts the existing one (the old one was incorrect)

Respond ONLY with JSON: {{"action": "reaffirm|refine|refute", "merged_text": "..."}}
Only include merged_text if action="refine"."""
    result = _llm_call_json(prompt, timeout=30)
    if not isinstance(result, dict):
        return None, None
    action = result.get("action", "").lower()
    if action not in ("reaffirm", "refine", "refute"):
        return None, None
    return action, result.get("merged_text", "").strip()[:200]


USER_STATED_CONFIDENCE = 0.9


def _add_lesson_with_consolidation(new_lesson, source_id):
    with _lessons_lock:
        vivas = [l for l in _lessons_data if not l.get("stale")]
        near = rank_by_words(new_lesson["text"], vivas,
                              lambda l: l.get("text", ""), k=1)
        if near and near[0].get("score", 0) >= 0.9:
            ex = next((l for l in vivas if l["id"] == near[0]["id"]), None)
            if ex is not None:
                action, merged = _decide_lesson_action(new_lesson, ex)
                if action == "reaffirm":
                    ex["confidence"] = min(1.0, ex.get("confidence", 0.5) + 0.05)
                    ex["sources"] = list(set(ex.get("sources", []) + [source_id]))
                    ex["updated"] = datetime.now().isoformat()
                    _save_lessons_to_disk()
                    return
                if action == "refine" and merged:
                    ex["text"] = merged
                    ex["sources"] = list(set(ex.get("sources", []) + [source_id]))
                    ex["updated"] = datetime.now().isoformat()
                    _save_lessons_to_disk()
                    return
                if action == "refute":
                    ex["stale"] = True
                    ex["updated"] = datetime.now().isoformat()
        _add_lesson_raw(new_lesson["text"], new_lesson.get("tags", []),
                        float(new_lesson.get("confidence", 0.5)), [source_id])
        _save_lessons_to_disk()

def _add_lesson_raw(text, tags, confidence, sources):
    lesson_id = hashlib.md5(
        f"{text}_{datetime.now().isoformat()}".encode()).hexdigest()[:12]
    now = datetime.now().isoformat()
    _lessons_data.append({
        "id": lesson_id, "text": text, "tags": tags, "confidence": confidence,
        "sources": sources, "stale": False, "created": now, "updated": now,
    })
    print(f"[LESSONS] +new conf={confidence:.2f}: {text[:60]}")

def _extract_and_store_lessons_async(goal, steps, result, feedback, tags,
                                     source_id, user_note=""):
    try:
        if not CONFIG.getboolean("LESSONS", "extract_on_feedback", fallback=True):
            return
        lessons = _extract_lessons_from_experience(goal, steps, result, feedback, tags)
        if not lessons:
            return
        for lesson in lessons:
            if user_note:
                lesson["confidence"] = max(float(lesson.get("confidence", 0.5)),
                                           USER_STATED_CONFIDENCE)
                lesson["tags"] = list(set(lesson.get("tags", []) + ["user_stated"]))
                print(f"[LESSONS] user-stated → conf {lesson['confidence']:.2f}: "
                      f"{lesson.get('text','')[:70]}")
            _add_lesson_with_consolidation(lesson, source_id)
    except Exception as e:
        print(f"[LESSONS] Async extraction failed: {e}", file=sys.stderr)


import queue as _queue_mod
_LESSON_EXTRACTION_QUEUE = _queue_mod.Queue()
_LAST_USER_ACTIVITY = time.time()
_LESSON_WORKER_STARTED = False
_LESSON_WORKER_IDLE_S = 8


def _lesson_worker_loop():
    while True:
        try:
            job = _LESSON_EXTRACTION_QUEUE.get()
        except Exception:
            time.sleep(1)
            continue
        _urgent = len(job) > 6 and bool(job[6])
        while not _urgent:
            idle = time.time() - _LAST_USER_ACTIVITY
            if idle >= _LESSON_WORKER_IDLE_S:
                break
            time.sleep(min(2.0, _LESSON_WORKER_IDLE_S - idle))
        try:
            _extract_and_store_lessons_async(*job)
        except Exception as e:
            print(f"[LESSONS] Worker exception: {e}", file=sys.stderr)
        finally:
            _LESSON_EXTRACTION_QUEUE.task_done()


def _ensure_lesson_worker():
    global _LESSON_WORKER_STARTED
    if _LESSON_WORKER_STARTED:
        return
    _LESSON_WORKER_STARTED = True
    threading.Thread(target=_lesson_worker_loop, daemon=True,
                     name="lesson_worker").start()
    print("[LESSONS] Worker started (single-threaded, idle-wait %ds)" %
          _LESSON_WORKER_IDLE_S, file=sys.stderr)


RULES_BUDGET_BYTES = 4000


def search_lessons(query, n_results=3, min_confidence=0.0,
                   exclude_stale=True):
    if not _lessons_data:
        return {"lessons": []}
    vivas = [l for l in _lessons_data
             if not (exclude_stale and l.get("stale"))
             and l.get("confidence", 0) >= min_confidence]
    return {"lessons": [
        {"id": l["id"], "text": l["text"], "tags": l.get("tags", []),
         "confidence": l.get("confidence", 0.5), "by": "words",
         "why": l.get("why", []), "similarity": l.get("score", 0),
         "sources_count": len(l.get("sources", []))}
        for l in rank_by_words(query, vivas, lambda l: l.get("text", ""),
                               k=n_results)]}


def list_lessons(include_stale=False):
    out = []
    for l in _lessons_data:
        if not include_stale and l.get("stale"):
            continue
        out.append({
            "id": l["id"], "text": l["text"], "tags": l.get("tags", []),
            "confidence": l.get("confidence", 0.5),
            "stale": l.get("stale", False),
            "sources_count": len(l.get("sources", [])),
            "updated": l.get("updated", l.get("created", "")),
        })
    return {"lessons": out, "total": len(out)}


def deactivate_lesson(lesson_id):
    with _lessons_lock:
        for l in _lessons_data:
            if l["id"] == lesson_id:
                l["stale"] = True
                l["updated"] = datetime.now().isoformat()
                _save_lessons_to_disk()
                return {"ok": True, "id": lesson_id, "action": "deactivated"}
    return {"ok": False, "error": f"lesson {lesson_id} not found"}


def delete_lesson(lesson_id):
    global _lessons_data
    with _lessons_lock:
        before = len(_lessons_data)
        _lessons_data = [l for l in _lessons_data if l["id"] != lesson_id]
        if len(_lessons_data) == before:
            return {"ok": False, "error": f"lesson {lesson_id} not found"}
        _save_lessons_to_disk()
        return {"ok": True, "id": lesson_id, "action": "deleted"}


def add_lesson(text, tags=None, confidence=0.7):
    if not text or not text.strip():
        return {"ok": False, "error": "empty text"}
    with _lessons_lock:
        _add_lesson_raw(text.strip(), tags or [], float(confidence),
                        ["consolidation"])
        _save_lessons_to_disk()
    return {"ok": True, "text": text.strip(), "action": "added"}


def list_facts():
    out = []
    for f in _facts_data:
        out.append({
            "id": f["id"], "text": f["text"], "tags": f.get("tags", []),
            "status": f.get("status", "live"),
            "pinned": f.get("pinned", False),
            "hits": f.get("hits", 1),
            "recalls": f.get("recalls", 0),
            "last_recalled": f.get("last_recalled", ""),
        })
    return {"facts": out, "total": len(out)}





_notes_docs: list = []
_notes_meta: list = []


def _load_notes_index():
    global _notes_docs, _notes_meta
    try:
        sql = _get_sql()
        if sql is None:
            return False
        rows = sorted(sql.load("notes_index"), key=lambda r: r.get("ord", 0))
        if not rows:
            return False
        _notes_docs = [r.get("doc", "") for r in rows]
        _notes_meta = [r.get("meta", {}) for r in rows]
        print(f"[MCP] Notes: {len(_notes_docs)} chunks (SQLCipher)")
        return True
    except Exception as e:
        print(f"[MCP] ⚠ Notes load: {e}")
    return False

def _index_notes_on_boot():
    if not notes_mgr:
        return
    indexed = {m.get("title", "") for m in _notes_meta if m.get("type") == "note"}
    all_titles = set()
    pending = []
    for cat in notes_mgr.get_categories():
        if cat in _NO_INDEX_CATEGORIES:
            continue
        for note in notes_mgr.get_notes(cat):
            t = note.get("title", "")
            all_titles.add(t)
            if t not in indexed:
                pending.append((cat, t, note.get("content", "")))


HOPLITA_VERSION = "1.0"
CONFIG = None
todo_mgr = None
notes_mgr = None
cred_mgr = None
cron_mgr = None
fed_store = None


def _fed_migrate(manager):
    changed = False
    data = getattr(manager, "data", {})
    for _cat, notes in data.get("categories", {}).items():
        for n in notes:
            if not n.get("uuid"):
                n["uuid"] = uuid.uuid4().hex; n.setdefault("rev", 1)
                n.setdefault("editor", ""); n.setdefault("deleted", False)
                n.setdefault("local_only", False); changed = True
    for tk in data.get("tasks", []):
        if not tk.get("uuid"):
            tk["uuid"] = uuid.uuid4().hex; tk.setdefault("rev", 1)
            tk.setdefault("editor", ""); tk.setdefault("deleted", False)
            tk.setdefault("local_only", False); changed = True
    if changed and hasattr(manager, "save_data"):
        manager.save_data()
    return changed


def _session_pack_tools():
    try:
        pack = fed_tools.my_pack()
    except Exception:
        pack = None
    if not pack:
        return None
    return _pack_tool_names(pack, terminal=True, may_orchestrate=True)


def _build_session_system(message, own=False):
    try:
        parts = [_t("prompts.session_agent"),
                 _t("prompts.today", date=datetime.now().strftime("%Y-%m-%d"))]
        try:
            _pack = fed_tools.my_pack()
        except Exception:
            _pack = None
        if _pack:
            parts.insert(0, hoplita_children.system_block(_pack))
        fact_lines, les_lines = [], []
        inject_ctx = True
        try:
            if CONFIG is not None:
                inject_ctx = CONFIG.getboolean("CHAT", "session_inject_context",
                                               fallback=True)
        except Exception:
            inject_ctx = True
        inject_ctx = inject_ctx and own
        if inject_ctx and message:
            try:
                facts = select_facts_for_injection(message, budget=20) or []
            except Exception:
                facts = []
            fact_lines = [f"- {f.get('text', '')}" for f in facts if f.get("text")]
            if fact_lines:
                parts.append(_t("prompts.session_facts_header") + "\n" +
                             "\n".join(fact_lines))
            try:
                les = (search_lessons(message, n_results=2, min_confidence=0.85)
                       or {}).get("lessons", [])
            except Exception:
                les = []
            les_lines = [f"- {l.get('text', '')}" for l in les if l.get("text")]
            if les_lines:
                parts.append(_t("prompts.session_lessons_header") + "\n" +
                             "\n".join(les_lines))
        lang = _t("prompts.language_directive")
        if lang:
            parts.append(lang)
        _sys = "\n\n".join(p for p in parts if p) or None
        try:
            print(f"[SESSION] enrich → system={len(_sys or '')} chars · "
                  f"facts={len(fact_lines) if inject_ctx else 0} · "
                  f"lessons={len(les_lines) if inject_ctx else 0}",
                  file=sys.stderr)
        except Exception:
            pass
        return _sys
    except Exception as e:
        try:
            print(f"[SESSION] enrich FAILED: {e}", file=sys.stderr)
        except Exception:
            pass
        return None


def reload_all_data():
    for m in (todo_mgr, notes_mgr, cred_mgr, cron_mgr):
        if m is not None:
            try:
                m._data_loaded = False
                m._sql_shadow = None
                m.load_data()
                m._data_loaded = True
            except Exception as e:
                print(f"[SYNC] reload {getattr(m,'LOG_LABEL','?')}: {e}", file=sys.stderr)
    try:
        init_facts(); init_lessons(); init_experience(); _load_notes_index()
    except Exception as e:
        print(f"[SYNC] reload modules: {e}", file=sys.stderr)
    try:
        import wb_sync as _wbs
        _wbs.reconcile()
    except Exception as e:
        print(f"[SYNC] reload whiteboards: {e}", file=sys.stderr)


def init_managers(password):
    global CONFIG, todo_mgr, notes_mgr, cred_mgr, cron_mgr, fed_store
    CONFIG = load_config()
    todo_mgr = TODOManager(); todo_mgr.set_password(password)
    notes_mgr = NotesManager(); notes_mgr.set_password(password)
    try:
        _fed_migrate(todo_mgr); _fed_migrate(notes_mgr)
    except Exception as e:
        print(f"[MCP] ⚠ fed-fields migration: {e}")
    cred_mgr = CredentialManager(); cred_mgr.set_password(password)
    cron_mgr = CronManager(); cron_mgr.set_password(password)

    try:
        from hoplita_federation import build_identity_store
        import fed_tools
        FedStore = build_identity_store(EncryptedStorageManager)
        fed_store = FedStore(); fed_store.set_password(password)
        fed_tools.set_store_getter(lambda: fed_store)
        fed_tools.set_notes_getter(lambda: notes_mgr)
        fed_tools.set_project_journal(
            lambda path, text: tool_project_journal(path, text))

        import fed_agent

        def _spill_tool_result(tool_name, text):
            _title = (f"{WORKING_MEMORY}: {tool_name} "
                      f"{datetime.now().strftime('%H:%M:%S')}")
            try:
                _taken = {(r_.get("title") or "") for r_ in
                          (notes_mgr.data.get("categories", {}) or {})
                          .get(WORKING_MEMORY, []) or []
                          if not r_.get("deleted")}
            except Exception:
                _taken = set()
            _base, _k = _title, 2
            while _title in _taken:
                _title = f"{_base} #{_k}"
                _k += 1
            try:
                _obj = json.loads(text)
                if isinstance(_obj, (dict, list)):
                    text = json.dumps(_obj, ensure_ascii=False, indent=1)
            except Exception:
                pass
            tool_add_note(WORKING_MEMORY,
                          _title, f"{_wm_tag_line()}\n{text}",
                          _system=True)
            _tq = _title.replace("'", "\\'")
            return (f"search_working_memory(query='<words, a name, a "
                    f"figure>', title='{_tq}') for the passages that mention "
                    f"it; read_note(category='{WORKING_MEMORY}', "
                    f"title='{_tq}') reads it in order")
        fed_agent.configure_spill(_spill_tool_result)
        fed_tools.set_todo_getter(lambda: todo_mgr)
        fed_tools.set_sql_getter(_get_sql)
        fed_tools.set_reload_getter(reload_all_data)
        hoplita_children.set_runner(_child_run_local)
        hoplita_children.set_models_getter(lambda: list(_model_pool()))
        print("[MCP] ✓ Federation ready")
    except VaultPasswordError:
        raise
    except Exception as e:
        print(f"[MCP] ⚠ Federation unavailable: {e}")
    init_experience()
    init_lessons()
    init_facts()
    _load_notes_index()
    _index_notes_on_boot()
    try:
        _wm_gone = wm_sweep_window_ids()
        if _wm_gone:
            print(f"[MCP] working memory: {_wm_gone} note(s) owned only by a "
                  f"window's tab number, dropped")
    except Exception as e:
        print(f"[MCP] ⚠ working-memory sweep: {e}")
    try:
        _ensure_curate_worker()
    except Exception as e:
        print(f"[MCP] ⚠ automatic memory review: {e}")
    print("[MCP] ✓ Managers OK")



_LINUX_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_MAC_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
           "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15")


def _search_perplexity(query):
    api_key = CONFIG.get("PERPLEXITY", "api_key", fallback="")
    if not api_key or api_key == "tu_perplexity_key":
        return {"error": "Perplexity API key no configurada"}
    model = CONFIG.get("PERPLEXITY", "model", fallback="sonar-pro")
    url = CONFIG.get("PERPLEXITY", "url", fallback="https://api.perplexity.ai")
    if not url.endswith("/chat/completions"):
        url = url.rstrip("/") + "/chat/completions"
    r = requests.post(url,
        json={"model": model, "messages": [{"role": "user", "content": query}],
              "return_citations": True},
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json",
                 "User-Agent": _user_agent()},
        timeout=CONFIG.getint("CHAT", "timeout", fallback=120))
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}: {r.text[:300]}"}
    d = r.json()
    _count_tokens(d.get("model") or model, d.get("usage") or {}, sent=model)
    return {
        "provider": "perplexity",
        "kind": "answer",
        "result": d.get("choices", [{}])[0].get("message", {}).get("content", ""),
        "citations": d.get("citations", []),
    }


def _search_firecrawl(query):
    api_key = CONFIG.get("FIRECRAWL", "api_key", fallback="")
    if not api_key or api_key.startswith("tu_"):
        return {"error": "Firecrawl API key no configurada"}
    url = CONFIG.get("FIRECRAWL", "url", fallback="https://api.firecrawl.dev")
    endpoint = url.rstrip("/") + "/v1/search"
    limit = CONFIG.getint("FIRECRAWL", "limit", fallback=5)
    chars = CONFIG.getint("FIRECRAWL", "max_chars", fallback=0)
    payload = {
        "query": query,
        "limit": limit,
        "scrapeOptions": {"formats": ["markdown"]},
    }
    r = requests.post(endpoint,
        json=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=CONFIG.getint("CHAT", "timeout", fallback=120))
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}: {r.text[:300]}"}
    d = r.json()
    results = []
    citations = []
    for item in d.get("data", []) or []:
        src_url = item.get("url", "")
        results.append({
            "title": item.get("title", ""),
            "url": src_url,
            "content": (lambda _t: _t[:chars] if chars > 0 else _t)(
                item.get("markdown") or item.get("description") or ""),
        })
        if src_url:
            citations.append(src_url)
    return {
        "provider": "firecrawl",
        "kind": "sources",
        "results": results,
        "citations": citations,
    }


def _search_exa(query):
    api_key = CONFIG.get("EXA", "api_key", fallback="").strip()
    if not api_key or api_key.startswith("tu_"):
        return {"error": "Exa API key not configured ([EXA] api_key)."}
    base = CONFIG.get("EXA", "url", fallback="https://api.exa.ai").rstrip("/")
    mode = CONFIG.get("EXA", "mode", fallback="search").strip().lower()
    limit = CONFIG.getint("EXA", "limit", fallback=5)
    chars = CONFIG.getint("EXA", "max_chars", fallback=0)
    timeout = CONFIG.getint("CHAT", "timeout", fallback=120)
    headers = {"x-api-key": api_key, "Content-Type": "application/json",
               "User-Agent": _user_agent()}

    if mode == "answer":
        r = requests.post(f"{base}/answer",
                          json={"query": query, "text": True},
                          headers=headers, timeout=timeout)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}: {r.text[:300]}"}
        d = r.json() or {}
        cites = d.get("citations") or []
        return {"provider": "exa", "kind": "answer",
                "result": d.get("answer", ""),
                "citations": [c.get("url", "") for c in cites if isinstance(c, dict)]}

    payload = {"query": query, "numResults": limit,
               "contents": {"text": ({"maxCharacters": chars} if chars > 0
                                     else True)}}
    _type = CONFIG.get("EXA", "type", fallback="auto").strip().lower()
    if _type and _type != "auto":
        payload["type"] = _type
    r = requests.post(f"{base}/search", json=payload, headers=headers,
                      timeout=timeout)
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}: {r.text[:300]}"}
    d = r.json() or {}
    results, citations = [], []
    for item in (d.get("results") or []):
        if not isinstance(item, dict):
            continue
        u = item.get("url", "")
        body = item.get("text") or item.get("summary") or ""
        if not body and isinstance(item.get("highlights"), list):
            body = " … ".join(str(h) for h in item["highlights"])
        results.append({"title": item.get("title", ""), "url": u,
                        "content": (str(body)[:chars] if chars > 0
                                    else str(body)),
                        "published": item.get("publishedDate", "")})
        if u:
            citations.append(u)
    return {"provider": "exa", "kind": "sources", "results": results,
            "citations": citations, "count": len(results)}


def _search_tavily(query):
    api_key = CONFIG.get("TAVILY", "api_key", fallback="").strip()
    if not api_key or api_key.startswith("tu_"):
        return {"error": "Tavily API key not configured ([TAVILY] api_key)."}
    base = CONFIG.get("TAVILY", "url",
                      fallback="https://api.tavily.com").rstrip("/")
    depth = CONFIG.get("TAVILY", "depth", fallback="basic").strip().lower()
    limit = CONFIG.getint("TAVILY", "limit", fallback=5)
    want_answer = CONFIG.getboolean("TAVILY", "include_answer", fallback=True)
    payload = {"query": query, "max_results": limit,
               "search_depth": ("advanced" if depth == "advanced" else "basic"),
               "include_answer": bool(want_answer)}
    r = requests.post(f"{base}/search", json=payload,
                      headers={"Authorization": f"Bearer {api_key}",
                               "Content-Type": "application/json"},
                      timeout=CONFIG.getint("CHAT", "timeout", fallback=120))
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}: {r.text[:300]}"}
    d = r.json() or {}
    results, citations = [], []
    for item in (d.get("results") or []):
        if not isinstance(item, dict):
            continue
        u = item.get("url", "")
        results.append({"title": item.get("title", ""), "url": u,
                        "content": str(item.get("content") or ""),
                        "score": item.get("score")})
        if u:
            citations.append(u)
    answer = str(d.get("answer") or "").strip()
    out = {"provider": "tavily", "kind": "answer" if answer else "sources",
           "results": results, "citations": citations, "count": len(results)}
    if answer:
        out["result"] = answer
    return out


SEARCH_PROVIDERS = {
    "perplexity": _search_perplexity,
    "firecrawl": _search_firecrawl,
    "exa": _search_exa,
    "tavily": _search_tavily,
}


def tool_search_web(query):
    provider = (CONFIG.get("SEARCH", "provider", fallback="perplexity")
                if CONFIG else "perplexity").strip().lower()
    fn = SEARCH_PROVIDERS.get(provider)
    if not fn:
        return {"error": f"Unknown search provider '{provider}'. "
                         f"Available: {', '.join(SEARCH_PROVIDERS)}"}
    try:
        return fn(query)
    except Exception as e:
        return {"error": f"[{provider}] {e}"}


def tool_run_command(command, cwd=None, timeout=60):
    for danger in ('rm -rf /', 'format c:', 'mkfs', 'dd if=/dev/zero'):
        if danger in command.lower():
            return {"error": "Blocked"}
    try:
        r = subprocess.run(command, shell=True, capture_output=True, text=True,
                           **__import__("hoplita_paths").no_console(),
                           timeout=timeout, cwd=cwd or os.getcwd())
        return {"exit_code": r.returncode,
                "stdout": r.stdout[-5000:], "stderr": r.stderr[-2000:]}
    except subprocess.TimeoutExpired:
        return {"error": f"Timeout ({timeout}s)"}
    except Exception as e:
        return {"error": str(e)}


def tool_canvas_see(max_width=None):
    return {"error": "no canvas here: canvas_see captures the GUI's live "
                     "canvas, and this caller (CLI / direct MCP) has none."}


def tool_canvas_eval(js):
    return {"error": "no canvas here: canvas_eval runs in the GUI's live "
                     "canvas, and this caller (CLI / direct MCP) has none. "
                     "render_html returns the HTML for the client to save "
                     "or open instead."}


def tool_dashboard_render(html):
    if not html or not html.strip():
        return {"error": "empty html"}
    return {"rendered": False,
            "reason": "dashboard_render requires the GUI (Dashboard tab). "
                      "Without a GUI there is no canvas. The HTML is returned so the "
                      "client can save it or open it in a browser if desired.",
            "html": html, "chars": len(html)}


def _note_open_call(category, title):
    _cat_s = str(category or "").replace("'", "\\'")
    _title_s = str(title or "").replace("'", "\\'")
    return f"read_note(category='{_cat_s}', title='{_title_s}')"


def _path_is_a_note(path):
    p = str(path or "").strip().strip("/")
    if "/" not in p or os.path.exists(os.path.expanduser(p)):
        return None
    cat, title = p.split("/", 1)
    try:
        notes = notes_mgr.get_notes(cat) if notes_mgr else []
    except Exception:
        return None
    if not notes:
        return None
    for n_ in notes:
        t_ = str(n_.get("title", ""))
        if t_ == title or t_.endswith(title) or title in t_:
            return cat, t_
    return cat, title



def _media_dir():
    d = os.path.join(hoplita_base(), "media")
    os.makedirs(d, exist_ok=True)
    return d


def _image_cfg():
    if not CONFIG or not CONFIG.has_section("IMAGE"):
        return "", "", "", "1024x1024", 300
    return ((CONFIG.get("IMAGE", "base_url", fallback="") or "").strip().rstrip("/"),
            (CONFIG.get("IMAGE", "api_key", fallback="") or "").strip(),
            (CONFIG.get("IMAGE", "model", fallback="") or "").strip(),
            (CONFIG.get("IMAGE", "size", fallback="1024x1024") or "1024x1024").strip(),
            CONFIG.getint("IMAGE", "timeout", fallback=300))


_IMG_SIGS = ((b"\x89PNG", "png"), (b"\xff\xd8", "jpg"), (b"GIF8", "gif"))


def _image_ext(raw):
    for sig, ext in _IMG_SIGS:
        if raw.startswith(sig):
            return ext
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    return "png"


def _image_save(raw, stem="img"):
    ext = _image_ext(raw)
    name = f"{stem}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:6]}.{ext}"
    path = os.path.join(_media_dir(), name)
    with open(path, "wb") as fh:
        fh.write(raw)
    out = {"path": path, "src": "file://" + path, "bytes": len(raw)}
    try:
        from PIL import Image
        with Image.open(path) as im:
            out["width"], out["height"] = im.size
    except Exception:
        pass
    return out


def _image_ref_bytes(ref):
    import hoplita_hands as _hands
    ref = str(ref or "").strip()
    if ref.lower() in ("attached", "attached images", "adjuntas", "adjuntadas"):
        got = _turn_attached_images()
        if not got:
            return None, ("refs=['attached'] but no picture was pasted in this "
                          "conversation. Ask the user to paste it, or name a file.")
        return [(f"attached_{i}.{(m.split('/')[-1] or 'png')}", base64.b64decode(b64), m)
                for i, (m, b64) in enumerate(got)], ""
    pth = os.path.expanduser(ref[7:] if ref.startswith("file://") else ref)
    if not _hands.is_image_path(pth) or not os.path.isfile(pth):
        return None, f"'{ref}' is not an image file here."
    with open(pth, "rb") as fh:
        raw = fh.read()
    return [(os.path.basename(pth), raw, mimetypes.guess_type(pth)[0] or "image/png")], ""


def _image_request(prompt, refs=None, size=None, n=1):
    base_url, api_key, model, dsize, timeout = _image_cfg()
    if not base_url:
        return {"error": "no picture server configured: set [IMAGE] base_url "
                         "(and model) to a server that speaks the OpenAI "
                         "Images API — OpenAI, xAI, LocalAI, stable-diffusion.cpp."}
    prompt = (prompt or "").strip()
    if not prompt:
        return {"error": "generate_image needs a prompt."}
    size = (size or dsize or "").strip()
    n = max(1, min(int(n or 1), 4))
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    files, refs = [], [r for r in (refs or []) if str(r or "").strip()]
    for r in refs:
        got, err = _image_ref_bytes(r)
        if err:
            return {"error": err}
        files.extend(got)
    try:
        if files:
            data = {"prompt": prompt, "n": str(n)}
            if model:
                data["model"] = model
            if size:
                data["size"] = size
            field = "image" if len(files) == 1 else "image[]"
            r = _http().post(f"{base_url}/images/edits", data=data,
                             files=[(field, (nm, raw, mime)) for nm, raw, mime in files],
                             headers=headers, timeout=timeout)
        else:
            body = {"prompt": prompt, "n": n, "response_format": "b64_json"}
            if model:
                body["model"] = model
            if size:
                body["size"] = size
            r = _http().post(f"{base_url}/images/generations", json=body,
                             headers={**headers, "Content-Type": "application/json"},
                             timeout=timeout)
            if r.status_code == 400 and "response_format" in (r.text or ""):
                body.pop("response_format", None)
                r = _http().post(f"{base_url}/images/generations", json=body,
                                 headers={**headers, "Content-Type": "application/json"},
                                 timeout=timeout)
    except Exception as e:
        return {"error": f"picture server unreachable: {str(e)[:200]}"}
    if r.status_code != 200:
        return {"error": f"picture server HTTP {r.status_code}: {(r.text or '')[:300]}"}
    try:
        payload = r.json()
    except Exception:
        return {"error": "picture server answered no JSON."}
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        return {"error": f"picture server answered without images: {str(payload)[:200]}"}
    out_imgs = []
    for it in items:
        raw = None
        if isinstance(it, dict) and it.get("b64_json"):
            try:
                raw = base64.b64decode(it["b64_json"])
            except Exception:
                raw = None
        elif isinstance(it, dict) and it.get("url"):
            try:
                rr = _http().get(it["url"], timeout=timeout)
                raw = rr.content if rr.status_code == 200 else None
            except Exception:
                raw = None
        if not raw:
            continue
        saved = _image_save(raw, "edit" if files else "img")
        if isinstance(it, dict) and it.get("revised_prompt"):
            saved["revised_prompt"] = str(it["revised_prompt"])[:500]
        out_imgs.append(saved)
        try:
            import fed_agent as _fa_img
            _fa_img.tool_progress("image.generated", path=saved["path"],
                                  src=saved["src"], prompt=prompt[:160],
                                  model=model or "?")
        except Exception:
            pass
    if not out_imgs:
        return {"error": "picture server answered, but no image could be read."}
    sources = ["attached" if str(r).strip().lower() in ("attached", "attached images", "adjuntas", "adjuntadas")
               else os.path.expanduser(str(r)) for r in refs]
    return {"images": out_imgs, "model": model, "prompt": prompt, "size": size,
            "edited_from": len(files) or None, "sources": sources or None,
            "must_look": [i["path"] for i in out_imgs],
            "note": "Shown on the canvas already, and NOT SEEN by you yet: before you "
                    "say anything about what it shows, see_image(path). To keep editing "
                    "THIS result (\"now also…\", \"keep going\", \"on this one…\"), "
                    "refs=[\"" + out_imgs[0]["path"] + "\"]; to start over from the picture "
                    "the person pasted (\"that was not it\", \"do it again\"), "
                    "refs=[\"attached\"]. To compose (a frame, a caption, other pictures), "
                    "render_html with <img src=\"" + out_imgs[0]["src"] + "\">."}


def tool_generate_image(prompt, size=None, n=1, refs=None):
    return _image_request(prompt, refs=refs, size=size, n=n)


def tool_edit_image(prompt, refs, size=None, n=1):
    refs = [refs] if isinstance(refs, str) else list(refs or [])
    if not refs:
        return {"error": "edit_image needs refs: image file paths, or 'attached' "
                         "for the pictures in the user's message."}
    return _image_request(prompt, refs=refs, size=size, n=n)


def tool_see_image(path, max_width=1024):
    import hoplita_hands as _hands
    p = os.path.expanduser(str(path or "").strip())
    if not _hands.is_image_path(p):
        return {"error": f"'{path}' is not an image file (png/jpg/gif/webp/bmp/svg)."}
    if not os.path.isfile(p):
        return {"error": f"'{path}' does not exist."}
    try:
        from PIL import Image
        import io as _io
        img = Image.open(p)
        w, h = img.size
        mw = max(64, int(max_width or 1024))
        if w > mw:
            img = img.convert("RGB") if img.mode not in ("RGB", "RGBA") else img
            img = img.resize((mw, max(1, int(h * mw / w))))
        buf = _io.BytesIO()
        (img.convert("RGB") if img.mode == "P" else img).save(buf, format="PNG")
        raw = buf.getvalue()
        return {"path": p, "width": w, "height": h, "shown_width": img.size[0],
                "bytes": os.path.getsize(p),
                "screenshot_b64": base64.b64encode(raw).decode("ascii"),
                "screenshot_mime": "image/png",
                "note": "The image is attached right after this round's tool "
                        "results: look at it. Two images per round at most."}
    except Exception as e:
        return {"error": f"could not open the image: {e}"}


def tool_read_file(path):
    import hoplita_hands as _hands
    if _hands.is_image_path(path):
        return {"error": f"'{path}' is an image, not text",
                "message": f"Look at it with see_image(path='{path}') — it is "
                           "attached as a picture for you to read."}
    _ext = os.path.splitext(str(path or ""))[1].lower()
    if _ext == ".pdf":
        return {"error": f"'{path}' is a PDF, not text",
                "message": f"Read it with pdf_info(source='{path}', text='full')."}
    if _ext in _hoplita_office().OFFICE_EXTS:
        return {"error": f"'{path}' is an office document, not text",
                "message": f"Read it with office_info(source='{path}'); "
                           "edit it with office."}
    try:
        p = os.path.expanduser(path)
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            c = f.read()
        return {"result": c[:100000], "size": os.path.getsize(p)}
    except Exception as e:
        _hit = _path_is_a_note(path)
        if _hit:
            return {"error": f"'{path}' is a NOTE, not a file",
                    "message": "Notes are not on disk. Read it with "
                               + _note_open_call(*_hit) + "."}
        return {"error": str(e)}


def tool_list_dir(path=".", recursive=False):
    try:
        p = os.path.expanduser(path or ".")
        if recursive:
            lines = []
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs
                           if not d.startswith('.') and d not in ('node_modules', '__pycache__', '.git')]
                for f in files:
                    lines.append(os.path.relpath(os.path.join(root, f), p))
                if len(lines) > 300: break
            return {"result": "\n".join(lines)}
        entries = []
        for e in sorted(os.scandir(p), key=lambda x: x.name):
            try:
                entries.append({"name": e.name, "is_dir": e.is_dir(),
                                "size": e.stat().st_size if not e.is_dir() else 0})
            except OSError:
                entries.append({"name": e.name, "is_dir": False, "size": 0,
                                "broken_link": True})
        return {"entries": entries}
    except Exception as e:
        return {"error": str(e)}




_MEM_STORES = ("facts", "lessons", "notes", "tasks", "appointments",
               "conversation")


def _recall_todo(query, k):
    def _txt(it):
        return f"{it.get('title', '')} — {it.get('description', '')}".strip(" —")

    out = {}
    for kind, items in (("tasks", todo_mgr.get_tasks()),
                        ("appointments", todo_mgr.get_appointments())):
        out[kind] = [{"kind": kind, "title": it.get("title", ""),
                      "text": _txt(it), "id": it.get("id", ""),
                      "project": it.get("project", ""),
                      "score": it.get("score", 0),
                      "matched": it.get("why") or it.get("by", "")}
                     for it in rank_relevant(query, items, _txt, k=k)]
    return out


def log_turn_message(session, role, text, tab=""):
    try:
        sql = _get_sql()
        if sql is None:
            return False
        return sql.log_message(session, role, (text or "")[:20000],
                               ts=_chat_now_iso(), tab=tab)
    except Exception as e:
        print(f"[LOG] {e}", file=sys.stderr)
        return False


def backfill_message_log():
    try:
        sql = _get_sql()
        if sql is None or not getattr(sql, "fts", False):
            return 0
        if sql.meta_get("msglog_backfilled"):
            return 0
        n = 0
        for row in _chat_sessions_rows():
            if row.get("deleted"):
                continue
            sid = row.get("uuid", "")
            for m in (row.get("history") or []):
                c = m.get("content")
                if isinstance(c, str) and c.strip():
                    if sql.log_message(sid, m.get("role", ""), c[:20000],
                                       ts=row.get("updated_at", "")):
                        n += 1
        sql.meta_set("msglog_backfilled", "1")
        if n:
            print(f"[LOG] {n} message(s) from saved sessions put into the "
                  f"searchable log", file=sys.stderr)
        return n
    except Exception as e:
        print(f"[LOG] backfill: {e}", file=sys.stderr)
        return 0


def tool_session_moments(query, limit=4, window_min=5):
    try:
        sql = _get_sql()
        if sql is None:
            return {"error": "store unavailable"}
        if not getattr(sql, "fts", False):
            return {"error": "this build has no FTS5", "moments": []}
        out = []
        for m in sql.search_moments(query, limit=limit, window_min=window_min):
            out.append({
                "when": m.get("ts", ""), "session": m.get("session", ""),
                "matched_terms": m.get("covers", []), "score": m.get("score", 0),
                "messages": [{"role": x["role"], "text": x["text"][:900]}
                             for x in sql.moment_messages(m["from_rowid"],
                                                          m["to_rowid"])]})
        return {"query": query, "moments": out,
                "total_logged": sql.messages_count()}
    except Exception as e:
        return {"error": str(e)}


def tool_session_search(query, limit=6, session=None, context=1,
                        include_machinery=False):
    try:
        sql = _get_sql()
        if sql is None:
            return {"error": "store unavailable"}
        if not getattr(sql, "fts", False):
            return {"error": "this build has no FTS5: the log is not searchable",
                    "hits": []}
        hits = sql.search_messages(query, limit=max(1, int(limit)),
                                   session=session,
                                   roles=("*",) if include_machinery else None)
        out = []
        for h in hits:
            item = {"session": h["session"], "role": h["role"],
                    "ts": h["ts"], "text": h["text"][:1200]}
            if context:
                item["around"] = [
                    {"role": c["role"], "text": c["text"][:400]}
                    for c in sql.message_context(h["rowid"], context, context)
                    if c["rowid"] != h["rowid"]]
            out.append(item)
        return {"query": query, "hits": out, "total_logged": sql.messages_count()}
    except Exception as e:
        return {"error": str(e)}


def _proposals_rows():
    sql = _get_sql()
    return (sql.load("memory_proposals") if sql is not None else [])


def tool_propose_memory_change(target_id, action, why, quotes,
                               new_text="", target_kind="fact",
                               absorbed=None):
    sql = _get_sql()
    if sql is None:
        return {"error": "store unavailable"}
    if action not in ("retire", "replace", "merge", "prune"):
        return {"error": "action must be retire, replace, merge or prune"}
    quotes = [q for q in (quotes or []) if isinstance(q, dict) and q.get("text")]
    if action == "prune" and not quotes:
        _f = next((x for x in _facts_data if x.get("id") == target_id), None)
        if _f is None:
            return {"error": "no such fact"}
        quotes = [{"ts": _f.get("ts", ""), "role": "fact",
                   "text": _f.get("text", "")}]
    if not quotes:
        return {"error": "NO QUOTE, NO PROPOSAL. Something being old is not "
                         "evidence that it happened: bring what was said, "
                         "verbatim, with session_moments. If you cannot find "
                         "it, propose nothing — «I do not know» is a valid "
                         "answer."}
    if action in ("replace", "merge") and not (new_text or "").strip():
        return {"error": f"{action} needs new_text"}
    if action == "merge" and not absorbed:
        return {"error": "merge needs the ids it absorbs"}
    for r in _proposals_rows():
        if r.get("target_id") == target_id and r.get("action") == action \
                and r.get("status") == "rejected":
            return {"skipped": "rejected_before",
                    "note": "This was proposed and declined. Do not push."}
        if r.get("target_id") == target_id and r.get("status") == "pending":
            return {"skipped": "already_pending"}
    pid = uuid.uuid4().hex[:10]
    _obj = next((f for f in _facts_data if f.get("id") == target_id), None)
    row = {"id": pid, "target_id": target_id, "target_kind": target_kind,
            "target_text": (_obj or {}).get("text", ""),
            "action": action, "new_text": (new_text or "").strip(),
            "absorbed": list(absorbed or []),
            "why": (why or "").strip(), "quotes": quotes[:5],
            "status": "pending", "created": _chat_now_iso()}
    sql.upsert("memory_proposals", pid, row)
    return {"proposed": True, "id": pid}


_CURATE_RECHECK_DAYS = 21
_CURATE_EXPIRABLE = ("intencion", "estado")
_CURATE_DURABLE_AFTER = 90


def _curate_candidates(kinds=None, recheck_days=_CURATE_RECHECK_DAYS,
                       with_stats=False):
    kinds = tuple(kinds) if kinds else _CURATE_EXPIRABLE
    now_ = datetime.now()

    def _days(value):
        try:
            return (now_ - datetime.fromisoformat(value)).days
        except Exception:
            return None

    _rows = _proposals_rows()
    _pend = {r.get("target_id") for r in _rows if r.get("status") == "pending"}
    _rej = {r.get("target_id") for r in _rows if r.get("status") == "rejected"}
    ya = _pend | _rej
    with _facts_lock:
        all_facts = [f for f in _facts_data if _fact_live(f)]
    out = []
    stats = {"total": len(all_facts), "awaiting_decision": 0, "rejected": 0,
             "recently_reviewed": 0, "next_due_days": None}
    for f in all_facts:
        if f.get("id") in ya:
            stats["awaiting_decision" if f.get("id") in _pend
                  else "rejected"] += 1
            continue
        seen = _days(f.get("curated", ""))
        if seen is not None and seen < recheck_days:
            stats["recently_reviewed"] += 1
            _due = recheck_days - seen
            if stats["next_due_days"] is None or _due < stats["next_due_days"]:
                stats["next_due_days"] = _due
            continue
        age = _days(f.get("ts", ""))
        k = f.get("kind", "durable")
        if k in kinds:
            prio = 0
        elif "durable" in kinds or (age is not None
                                    and age >= _CURATE_DURABLE_AFTER):
            prio = 1
        else:
            prio = 2
        out.append((prio, -(age or 0), f))
    out.sort(key=lambda x: (x[0], x[1]))
    if with_stats:
        return [f for _p, _e, f in out], stats
    return [f for _p, _e, f in out]


def _curate_evidence(fact, window_min=5, max_lines=24):
    sql = _get_sql()
    text_ = (fact.get("text") or "").strip()
    if sql is None or not text_:
        return []
    since = fact.get("ts") or ""
    try:
        momentos = sql.search_moments(text_, limit=3, window_min=window_min)
    except Exception as e:
        print(f"[CURATE] search failed: {e}", file=sys.stderr)
        return []
    def _min(v):
        try:
            return datetime.fromisoformat(v).timestamp() / 60.0
        except Exception:
            return None

    seen_ids, lines = set(), []
    for m in momentos:
        if len(m.get("covers") or []) < 2:
            continue
        m_min = _min(m.get("ts") or "")
        for r in sql.moment_messages(m["from_rowid"], m["to_rowid"],
                                     pad=1, cap=12):
            if (r.get("ts") or "") <= since or r["rowid"] in seen_ids:
                continue
            r_min = _min(r.get("ts") or "")
            if (m_min is not None and r_min is not None
                    and abs(r_min - m_min) > window_min * 3):
                continue
            seen_ids.add(r["rowid"])
            lines.append(r)
    lines.sort(key=lambda r: r.get("rowid", 0))
    return lines[:max_lines]


def curate_memory(limit=None, kinds=None, recheck_days=_CURATE_RECHECK_DAYS,
                  window_min=5):
    sql = _get_sql()
    if sql is None:
        return {"error": "store unavailable"}
    if not getattr(sql, "fts", None):
        return {"error": "no conversation log to check against",
                "note": "The message log is off or empty: without what was "
                        "said there is no evidence, and without evidence this "
                        "proposes nothing."}
    _rearmed = _llm_breaker_rearm("curate_memory")
    reviewed, proposals, no_trace, ok = [], [], [], 0
    if limit is not None and int(limit) < 0:
        recheck_days = 0
    _cr = _curate_candidates(kinds, recheck_days, with_stats=True)
    _cands, _pool_stats = _cr if isinstance(_cr, tuple) else (_cr, {})
    if limit is None:
        limit = min(60, max(6, -(-len(_cands) // 3)))
    elif int(limit) < 0:
        limit = max(1, len(_cands))
    for f in _cands[:max(1, int(limit))]:
        lines = _curate_evidence(f, window_min=window_min)
        if not lines:
            ok += 1
            v_ = _llm_call_json(_PRUNE_PROMPT.format(text=f.get("text", "")),
                                max_tokens=200, timeout=60)
            if not isinstance(v_, dict):
                no_trace.append(f.get("text", ""))
                _why = ((f"the model answered with "
                         f"{type(v_).__name__}, not an object")
                        if v_ is not None else
                        (_LLM_CALL_FAILS.get("last_error", "")
                         or "the model did not answer"))
                if _LLM_CALL_FAILS.get("disabled"):
                    _why = (f"breaker open since "
                            f"{_LLM_CALL_FAILS.get('since', '?')} — {_why}")
                reviewed.append({"fact": f.get("text", ""), "verdict": "error",
                                 "why": _why})
                continue
            _mark_curated(f["id"])
            if not bool(v_.get("vacuous")):
                no_trace.append(f.get("text", ""))
                reviewed.append({"fact": f.get("text", ""), "verdict": "stands",
                                 "why": str(v_.get("why", ""))[:200]})
                continue
            reviewed.append({"fact": f.get("text", ""), "verdict": "vacuous",
                             "why": str(v_.get("why", ""))[:200]})
            p_ = tool_propose_memory_change(
                f["id"], "prune", str(v_.get("why", ""))[:300], [])
            if p_.get("proposed"):
                proposals.append({"id": p_["id"], "fact": f.get("text", ""),
                                  "action": "prune"})
            continue
        try:
            age = (datetime.now()
                    - datetime.fromisoformat(f.get("ts", ""))).days
            age = f"{age} days ago"
        except Exception:
            age = "an unknown time ago"
        numbered = "\n".join(
            f"[{i}] {(r.get('ts') or '')[:16]} {r.get('role', '?')}: "
            f"{(r.get('text') or '')[:400]}" for i, r in enumerate(lines))
        ok += 1
        r_ = _llm_call_json(_CURATE_PROMPT.format(
            age=age, text=f.get("text", ""), lines=numbered),
            max_tokens=500, timeout=90)
        if not isinstance(r_, dict):
            _why = ((f"the model answered with "
                     f"{type(r_).__name__}, not an object")
                    if r_ is not None else
                    (_LLM_CALL_FAILS.get("last_error", "")
                     or "the model did not answer"))
            if _LLM_CALL_FAILS.get("disabled"):
                _why = (f"breaker open since "
                        f"{_LLM_CALL_FAILS.get('since', '?')} — {_why}")
            reviewed.append({"fact": f.get("text", ""), "verdict": "error",
                             "why": _why})
            continue
        _mark_curated(f["id"])
        v = str(r_.get("verdict", "unknown")).lower().strip()
        reviewed.append({"fact": f.get("text", ""), "verdict": v,
                          "why": str(r_.get("why", ""))[:200]})
        if v not in ("happened", "dropped"):
            continue
        quotes_ = []
        for n in (r_.get("lines") or [])[:3]:
            try:
                row = lines[int(n)]
            except (ValueError, TypeError, IndexError):
                continue
            quotes_.append({"ts": row.get("ts", ""), "role": row.get("role", ""),
                          "text": (row.get("text") or "")[:400]})
        if not quotes_:
            reviewed[-1]["verdict"] = "unknown"
            reviewed[-1]["why"] = ("it said yes but pointed at no line — no "
                                   "quote, no proposal")
            continue
        p = tool_propose_memory_change(
            f["id"], "replace" if v == "happened" else "retire",
            str(r_.get("why", ""))[:300], quotes_,
            new_text=str(r_.get("new_text", ""))[:300])
        if p.get("proposed"):
            proposals.append({"id": p["id"], "fact": f.get("text", ""),
                               "action": "replace" if v == "happened"
                                         else "retire"})
    out = {"asked": ok, "checked": ok + len(no_trace),
           "proposals": proposals, "reviewed": reviewed,
           "no_trace": no_trace[:20],
           "pool": len(_cands), "pool_stats": _pool_stats,
           "pending": len([r for r in _proposals_rows()
                           if r.get("status") == "pending"])}
    _errs = [r for r in reviewed if r.get("verdict") == "error"]
    if _errs:
        out["errors"] = len(_errs)
        out["error_last"] = _errs[-1].get("why", "")
    if _rearmed:
        out["llm_breaker_was_tripped"] = dict(
            _rearmed, note="the LLM helper was disabled before this pass, so "
                           "every earlier pass reported nothing having asked "
                           "anything. Rearmed for this run.")
    if _LLM_CALL_FAILS.get("disabled"):
        out["llm_breaker"] = {
            "tripped": True,
            "since": _LLM_CALL_FAILS.get("since", ""),
            "last_error": _LLM_CALL_FAILS.get("last_error", ""),
            "note": "the LLM helper is disabled after "
                    f"{_LLM_CALL_FAILS['limit']} failures in a row: every "
                    "verdict after it tripped is 'error' without anything "
                    "having been asked. It rearms by itself on the next "
                    "explicit pass, or on an MCP restart."}
    return out



_AUTOLOG_STORE = "memory_autolog"
_MERGE_MAX_GROUPS = 4
_MERGE_MIN_LESSONS = 4


def _autolog_rows():
    sql = _get_sql()
    return (sql.load(_AUTOLOG_STORE) if sql is not None else [])


_AUTOLOG_KEEP = 120


def _autolog(kind, summary, undo):
    sql = _get_sql()
    if sql is None:
        return None
    row = {"id": str(uuid.uuid4())[:8], "ts": _chat_now_iso(), "kind": kind,
           "summary": summary, "undo": undo, "undone": False}
    sql.upsert(_AUTOLOG_STORE, row["id"], row)
    try:
        rows = sorted(_autolog_rows(), key=lambda r: r.get("ts", ""))
        for r_ in rows[:max(0, len(rows) - _AUTOLOG_KEEP)]:
            sql.delete(_AUTOLOG_STORE, r_["id"])
    except Exception:
        pass
    return row


def memory_undo(entry_id):
    sql = _get_sql()
    if sql is None:
        return {"error": "store unavailable"}
    row = next((r for r in _autolog_rows() if r.get("id") == entry_id), None)
    if row is None:
        return {"error": "not found"}
    if row.get("undone"):
        return {"error": "already undone"}
    if row.get("undo_gone"):
        return {"error": "its facts were deleted"}
    u = row.get("undo") or {}
    back = 0
    for img in (u.get("facts_before") or []):
        if _restore_fact_image(img):
            back += 1
    if u.get("proposal"):
        for r_ in _proposals_rows():
            if r_.get("id") == u["proposal"]:
                r_["status"] = "rejected"
                r_["decided"] = _chat_now_iso()
                sql.upsert("memory_proposals", r_["id"], r_)
    with _lessons_lock:
        for lid in (u.get("deactivated") or []):
            for l in _lessons_data:
                if l["id"] == lid and l.get("stale"):
                    l["stale"] = False
                    l["updated"] = datetime.now().isoformat()
                    back += 1
        _lessons_data[:] = [l for l in _lessons_data
                            if l["id"] not in set(u.get("added") or [])]
        _save_lessons_to_disk()
    row["undone"] = True
    row["undone_at"] = _chat_now_iso()
    sql.upsert(_AUTOLOG_STORE, entry_id, row)
    return {"ok": True, "reactivated": back,
            "removed": len(u.get("added") or [])}


def curate_lessons(max_groups=_MERGE_MAX_GROUPS):
    active = [l for l in _lessons_data if not l.get("stale")]
    if len(active) < _MERGE_MIN_LESSONS:
        return {"looked_at": len(active), "merged": [], "deactivated": 0,
                "why": "too few to consolidate"}
    numbered = "\n".join(f"{i}. {l.get('text','')}"
                          for i, l in enumerate(active))
    try:
        r_ = _llm_call_json(_LESSON_MERGE_PROMPT.format(lessons=numbered[:12000]),
                            timeout=300, breaker=False)
    except Exception as e:
        return {"looked_at": len(active), "merged": [], "deactivated": 0,
                "error": str(e)[:200]}
    if not isinstance(r_, dict):
        _why = _LLM_CALL_FAILS.get("last_error", "")
        return {"looked_at": len(active), "merged": [], "deactivated": 0,
                "error": "the model did not answer with an object"
                         + (f" — {_why}" if _why else "")}

    def _pick(idx):
        try:
            return active[int(idx)]
        except (ValueError, TypeError, IndexError):
            return None

    done, gone = [], 0
    for g in (r_.get("groups") or [])[:max(1, int(max_groups))]:
        merged_text = str(g.get("merged", "") or "").strip()
        group = [x for x in (_pick(i) for i in (g.get("ids") or [])) if x]
        _ids = {l["id"] for l in group}
        if len(_ids) < 2 or len(merged_text) < 10:
            continue
        _new = add_lesson(merged_text, ["consolidation"], 0.85)
        if not _new.get("ok"):
            continue
        _added = next((l["id"] for l in reversed(_lessons_data)
                       if l.get("text") == merged_text), None)
        _off = []
        for l in group:
            if deactivate_lesson(l["id"]).get("ok"):
                _off.append(l["id"])
                gone += 1
        entry = _autolog(
            "lessons_merged",
            {"merged": merged_text, "why": str(g.get("why", ""))[:200],
             "replaced": [l.get("text", "")[:160] for l in group]},
            {"added": [_added] if _added else [], "deactivated": _off})
        done.append({"entry": (entry or {}).get("id"), "merged": merged_text,
                     "replaced": len(_off)})
    noise = []
    for i in (r_.get("noise") or [])[:max_groups]:
        l = _pick(i)
        if l and deactivate_lesson(l["id"]).get("ok"):
            noise.append(l["id"])
    if noise:
        entry = _autolog("lessons_retired",
                         {"count": len(noise),
                          "texts": [l.get("text", "")[:160] for l in
                                    (_pick(i) for i in (r_.get("noise") or []))
                                    if l][:max_groups]},
                         {"added": [], "deactivated": noise})
        done.append({"entry": (entry or {}).get("id"), "retired": len(noise)})
        gone += len(noise)
    return {"looked_at": len(active), "merged": done, "deactivated": gone}


_DEDUP_MAX_GROUPS = 4


def curate_fact_dups(max_groups=_DEDUP_MAX_GROUPS):
    all_facts = [dict(f) for f in _live_facts()]
    if len(all_facts) < 4:
        return {"looked_at": len(all_facts), "proposed": [],
                "why": "too few to repeat"}
    numbered = "\n".join(
        f"{i}. [{(f.get('last_seen') or f.get('ts') or '')[:10]}] "
        f"{f.get('text', '')}" for i, f in enumerate(all_facts))
    if len(numbered) > 14000:
        return _curate_fact_dup_groups(all_facts, max_groups)
    try:
        r_ = _llm_call_json(_FACT_DEDUP_PROMPT.format(facts=numbered[:14000]),
                            timeout=300, breaker=False)
    except Exception as e:
        return {"looked_at": len(all_facts), "proposed": [],
                "error": str(e)[:200]}
    if isinstance(r_, list):
        if any(isinstance(x, dict) and "id" in x and "ids" not in x for x in r_):
            r_ = {"groups": [], "noise": r_}
        else:
            r_ = {"groups": r_, "noise": []}
    if not isinstance(r_, dict):
        _why = _LLM_CALL_FAILS.get("last_error", "")
        return {"looked_at": len(all_facts), "proposed": [],
                "error": "the model did not answer with an object"
                         + (f" — {_why}" if _why else "")}

    def _pick(idx):
        try:
            return all_facts[int(idx)]
        except (ValueError, TypeError, IndexError):
            return None

    proposed = []
    _groups = [g for g in (r_.get("groups") or []) if isinstance(g, dict)]
    for g in _groups[:max(1, int(max_groups))]:
        merged = str(g.get("merged", "") or "").strip()
        group = [x for x in (_pick(i) for i in (g.get("ids") or [])) if x]
        _ids = sorted({f["id"] for f in group})
        if len(_ids) < 2 or len(merged) < 5:
            continue
        group.sort(key=lambda f: f.get("last_seen") or f.get("ts") or "",
                   reverse=True)
        target = group[0]
        ev = [{"ts": (f.get("last_seen") or f.get("ts") or "")[:10],
               "role": "memoria", "text": f.get("text", "")} for f in group]
        p = tool_propose_memory_change(
            target["id"], "merge", str(g.get("why", ""))[:300], ev,
            new_text=merged,
            absorbed=[f["id"] for f in group[1:]])
        if p.get("proposed"):
            proposed.append({"id": p["id"], "merged": merged,
                             "absorbs": len(group) - 1})
    for n_ in (r_.get("noise") or [])[:max(1, int(max_groups))]:
        if not isinstance(n_, dict):
            continue
        f = _pick(n_.get("id"))
        if f is None:
            continue
        p = tool_propose_memory_change(
            f["id"], "retire", str(n_.get("why", ""))[:300],
            [{"ts": (f.get("ts") or "")[:10], "role": "memoria",
              "text": f.get("text", "")}])
        if p.get("proposed"):
            proposed.append({"id": p["id"], "noise": f.get("text", "")[:120]})
    return {"looked_at": len(all_facts), "proposed": proposed}


def _curate_fact_dup_groups(all_facts, max_groups):
    parent = {f["id"]: f["id"] for f in all_facts}

    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for f in all_facts:
        others = [o for o in all_facts if o["id"] != f["id"]]
        for h in rank_by_words(f.get("text", ""), others, lambda o: o.get("text", ""), k=3):
            if h.get("score", 0) >= 0.5:
                parent[root(f["id"])] = root(h["id"])
    groups = {}
    for f in all_facts:
        groups.setdefault(root(f["id"]), []).append(f)
    clusters = sorted((g for g in groups.values() if 2 <= len(g) <= 12), key=len, reverse=True)
    proposed, looked = [], 0
    for cl in clusters[:12]:
        numbered = "\n".join(f"{i}. [{(f.get('last_seen') or f.get('ts') or '')[:10]}] "
                              f"{f.get('text', '')}" for i, f in enumerate(cl))
        looked += len(cl)
        try:
            r_ = _llm_call_json(_FACT_DEDUP_PROMPT.format(facts=numbered), timeout=300, breaker=False)
        except Exception:
            continue
        if isinstance(r_, list):
            r_ = {"groups": r_, "noise": []}
        if not isinstance(r_, dict):
            continue
        for g in [g for g in (r_.get("groups") or []) if isinstance(g, dict)][:max(1, int(max_groups))]:
            merged = str(g.get("merged", "") or "").strip()
            try:
                grp = [cl[int(i)] for i in (g.get("ids") or []) if 0 <= int(i) < len(cl)]
            except (ValueError, TypeError):
                continue
            if len({f["id"] for f in grp}) < 2 or len(merged) < 5:
                continue
            grp.sort(key=lambda f: f.get("last_seen") or f.get("ts") or "", reverse=True)
            ev = [{"ts": (f.get("last_seen") or f.get("ts") or "")[:10], "role": "memoria",
                   "text": f.get("text", "")} for f in grp]
            p = tool_propose_memory_change(grp[0]["id"], "merge", str(g.get("why", ""))[:300], ev,
                                           new_text=merged, absorbed=[f["id"] for f in grp[1:]])
            if p.get("proposed"):
                proposed.append({"id": p["id"], "merged": merged, "absorbs": len(grp) - 1})
    return {"looked_at": len(all_facts), "grouped": looked, "groups": len(clusters),
            "proposed": proposed}


def memory_review(facts=None):
    out = {"at": _chat_now_iso()}
    _r = _llm_breaker_rearm("memory_review")
    if _r:
        out["llm_breaker_was_tripped"] = _r
    try:
        out["lessons"] = curate_lessons()
    except Exception as e:
        out["lessons"] = {"error": str(e)[:200]}
    try:
        out["facts"] = curate_memory(limit=facts)
    except Exception as e:
        out["facts"] = {"error": str(e)[:200]}
    try:
        out["duplicates"] = curate_fact_dups()
    except Exception as e:
        out["duplicates"] = {"error": str(e)[:200]}
    try:
        _pids = [p.get("id") for p in ((out.get("facts") or {}).get("proposals") or [])] + \
                [p.get("id") for p in ((out.get("duplicates") or {}).get("proposed") or [])]
        out["applied"] = _apply_curator_proposals([p for p in _pids if p])
    except Exception as e:
        out["applied"] = {"error": str(e)[:200]}
    try:
        sql = _get_sql()
        if sql is not None:
            sql.meta_set("curate:last", out["at"])
    except Exception:
        pass
    return out


_CURATE_WAKE = threading.Event()
_PRESSURE_EVERY_S = 3600
_pressure_last = [0.0]


def _memory_pressure():
    try:
        if _live_bytes() > FACTS_CATALOGUE_BYTES and \
                time.time() - _pressure_last[0] > _PRESSURE_EVERY_S:
            _CURATE_WAKE.set()
    except Exception:
        pass


_CURATE_TICK_S = 900
_curate_started = False


def _curate_cfg():
    if CONFIG is None:
        return True, 168, None
    def _g(k, d):
        try:
            return CONFIG.get("MEMORY", k, fallback=str(d))
        except Exception:
            return str(d)
    on = str(_g("auto_review", "on")).strip().lower() not in (
        "0", "off", "no", "false")
    try:
        hours = max(1, int(float(_g("review_every_hours", 168))))
    except Exception:
        hours = 168
    try:
        _rf = str(_g("review_facts", "0")).strip().lower()
        facts = None if _rf in ("0", "auto", "") else max(1, int(float(_rf)))
    except Exception:
        facts = None
    return on, hours, facts


def _curate_due(hours):
    sql = _get_sql()
    if sql is None:
        return False
    last = ""
    try:
        last = sql.meta_get("curate:last") or ""
    except Exception:
        pass
    if not last:
        return False
    try:
        return (datetime.now()
                - datetime.fromisoformat(last)).total_seconds() >= hours * 3600
    except Exception:
        return True


def _curate_loop():
    while True:
        try:
            woken = _CURATE_WAKE.wait(_CURATE_TICK_S)
            _CURATE_WAKE.clear()
            on, hours, facts = _curate_cfg()
            if woken and on and time.time() - _pressure_last[0] > _PRESSURE_EVERY_S:
                _pressure_last[0] = time.time()
                r = memory_review(facts=facts)
                print(f"[MEMORY] pressure pass: {len(r.get('applied') or [])} change(s) "
                      f"applied, live memory {_live_bytes()} bytes", file=sys.stderr)
                continue
            if not on or not _curate_due(hours):
                continue
            try:
                _get_sql().meta_set("curate:last", _chat_now_iso())
            except Exception:
                pass
            r = memory_review(facts=facts)
            _les = (r.get("lessons") or {})
            _fac = (r.get("facts") or {})
            print(f"[MEMORY] weekly pass: {len(_les.get('merged') or [])} "
                  f"merge(s), {_les.get('deactivated', 0)} rule(s) retired, "
                  f"{len(_fac.get('proposals') or [])} proposal(s)",
                  file=sys.stderr)
        except Exception as e:
            print(f"[MEMORY] pass failed: {e}", file=sys.stderr)


def _ensure_curate_worker():
    global _curate_started
    if _curate_started:
        return
    _curate_started = True
    try:
        sql = _get_sql()
        if sql is not None and not (sql.meta_get("curate:last") or ""):
            sql.meta_set("curate:last", _chat_now_iso())
    except Exception:
        pass
    threading.Thread(target=_curate_loop, daemon=True,
                     name="memory_review").start()
    _on, _h, _ = _curate_cfg()
    print(f"[MEMORY] automatic review: {'on' if _on else 'off'} "
          f"(every {_h}h)", file=sys.stderr)


def _mark_curated(fact_id):
    with _facts_lock:
        for f in _facts_data:
            if f.get("id") == fact_id:
                f["curated"] = datetime.now().isoformat()
                _save_facts_to_disk()
                return True
    return False


def tool_recall(query, terms=None, n_results=4):
    q = (query or "").strip()
    if isinstance(terms, str):
        terms = [terms]
    extra = [str(t_).strip() for t_ in (terms or []) if str(t_).strip()]
    if not q and not extra:
        return {"error": "recall needs a query"}
    q = " ".join([q] + extra).strip()
    found, failed = {}, []
    try:
        found["facts"] = [{"text": f.get("text", ""), "id": f.get("id", ""),
                           "score": f.get("score", 0)}
                          for f in search_facts(q, n_results)]
    except Exception as e:
        failed.append(f"facts: {e}")
    try:
        found["lessons"] = [{"text": l.get("text", ""), "id": l.get("id", ""),
                             "confidence": l.get("confidence", 0)}
                            for l in (search_lessons(q, n_results, 0.0)
                                      or {}).get("lessons", [])]
    except Exception as e:
        failed.append(f"lessons: {e}")
    try:
        found["notes"] = [{"title": r.get("title", ""),
                           "category": r.get("category", ""),
                           "id": r.get("id", ""),
                           "open": _note_open_call(r.get("category", ""),
                                                   r.get("title", "")),
                           "excerpt": (r.get("content", "") or "")[:300]}
                          for r in (tool_search_notes(q) or {}).get("results",
                                                                    [])[:n_results]]
    except Exception as e:
        failed.append(f"notes: {e}")
    try:
        found.update(_recall_todo(q, n_results))
    except Exception as e:
        failed.append(f"todo: {e}")
    try:
        _log = tool_session_moments(q, limit=max(2, n_results // 2))
        found["conversation"] = [
            {"when": m.get("when", ""), "matched": m.get("matched_terms", []),
             "text": " | ".join(f"{x['role']}: {x['text'][:180]}"
                                for x in m.get("messages", [])[:6])}
            for m in (_log.get("moments") or [])]
        if _log.get("error"):
            failed.append(f"conversation: {_log['error']}")
    except Exception as e:
        failed.append(f"conversation: {e}")
    total = sum(len(v) for v in found.values())
    out = {"query": (query or "").strip(), "searched_terms": extra or None,
           "found": {k: v for k, v in found.items() if v},
           "total": total,
           "looked_in": [k for k in _MEM_STORES if k not in
                         {f.split(":")[0] for f in failed}]}
    if failed:
        out["could_not_search"] = failed
    if not total:
        out["note"] = ("Nothing recorded about this in any store. This is the "
                       "whole memory, not one drawer of it.")
    if not extra:
        out["hint"] = ("Searched by WORDS, with no `terms` given. If this is "
                       "not what you meant, retry with `terms` listing the "
                       "words the record itself would likely contain "
                       "(synonyms, the category word, brands, proper names) "
                       "before telling the user there is no record.")
    return out


def _with_scope(result, key, kind):
    if isinstance(result, dict) and not (result.get(key) or []):
        result["note"] = _scope_note(kind)
    return result


def _scope_note(kind):
    return (f"0 results — and this searched {kind} ONLY. "
            f"Your memory is split across {', '.join(_MEM_STORES)}; "
            f"call recall(query) to search all of them before concluding "
            f"there is no record of something.")


_NO_INDEX_CATEGORIES = {"cmd_log"}


def tool_search_notes(search_term, category=None):
    if category:
        blocked = _outside_child_scope(category)
        if blocked:
            return blocked
    cats = [category] if category else notes_mgr.get_categories()
    _scope = hoplita_children.notes_scope()
    if _scope is not None:
        cats = [c for c in cats if c in _scope]
    sl = (search_term or "").lower().strip()
    if not sl:
        results = []
        for cat in cats:
            for note in notes_mgr.get_notes(cat):
                results.append({"category": cat, "title": note["title"],
                                "open": _note_open_call(cat, note["title"]),
                                "content": note.get("content", "")[:500],
                                "id": note["id"], "score": 1})
        return {"results": results[:20]}

    from hoplita_search import find_passages, _norm_words as _nw, _same_word
    ids_now = set(_turn_conv_ids())
    docs = []
    for cat in cats:
        if not category and cat in _NO_INDEX_CATEGORIES:
            continue
        for note in notes_mgr.get_notes(cat):
            text = note.get("content", "") or ""
            if cat == WORKING_MEMORY:
                if not (ids_now and (_wm_sessions_of(note) or set()) & ids_now):
                    continue
                text = _wm_text(note)
            docs.append({"category": cat, "title": note.get("title", ""),
                         "id": note.get("id", ""), "text": text})
    found = find_passages(sl, docs, context=1, k=60)
    rows = {}
    for p_ in found["passages"]:
        d = p_["doc"]
        r = rows.get(id(d))
        if r is None:
            r = rows[id(d)] = {"doc": d, "covers": set(), "best": 0.0, "passages": []}
        r["covers"] |= set(p_["matched"])
        r["best"] = max(r["best"], p_["score"])
        if len(r["passages"]) < 2:
            r["passages"].append({"lines": f"{p_['from']}-{p_['to']}",
                                  "matched": p_["matched"], "text": p_["text"][:900]})
    _q = list(dict.fromkeys(_nw(sl)))
    for d in docs:
        _tw = _nw(d["title"])
        _title_hits = {w for w in _q if any(_same_word(w, x) for x in _tw)}
        if not _title_hits:
            continue
        r = rows.get(id(d))
        if r is None:
            r = rows[id(d)] = {"doc": d, "covers": set(), "best": 0.0, "passages": []}
        r["covers"] |= _title_hits
    out = []
    for r in rows.values():
        d = r["doc"]
        out.append({"category": d["category"], "title": d["title"],
                    "open": _note_open_call(d["category"], d["title"]),
                    "content": (r["passages"][0]["text"] if r["passages"]
                                else d["text"][:500]),
                    "passages": r["passages"],
                    "id": d["id"], "matched": sorted(r["covers"]),
                    "score": round(len(r["covers"]) + r["best"], 3),
                    "source": "words"})
    out.sort(key=lambda x: x["score"], reverse=True)
    return {"results": out[:10]}


def _skill_payload(note, cat):
    out = {"skill": note.get("title", ""), "category": cat,
           "content": note.get("content", "") or ""}
    if cat == "shared-skills":
        out["shared"] = True
        out["note"] = ("Skill shared by a contact (see @origin in the title). "
                       "Use it with judgment.")
    return out


def tool_skill_folder(category=None, on=True):
    _default = _skills_category()
    try:
        reales = list(notes_mgr.get_categories() or [])
    except Exception:
        reales = []
    cat = (category or "").strip()
    if cat:
        if cat not in reales:
            return {"error": f"'{cat}' is not a note folder.",
                    "folders": reales}
        if cat == _default:
            return {"error": f"'{cat}' is the default skills folder "
                             f"([BROWSER] default_skill_category): it is always "
                             f"in, and it is changed there, not here."}
        notes_mgr.set_skill_cat(cat, bool(on))
    return {"ok": True, "default": _default,
            "skills_folders": _skills_categories(),
            "added": sorted(k for k, v in
                            (notes_mgr.get_skill_cats() or {}).items() if v)}


def tool_use_skill(search_term, category=None):
    own_cat = _skills_category()
    try:
        reales = list(notes_mgr.get_categories() or [])
    except Exception:
        reales = []
    asked_cat = (category or "").strip()
    cats = []
    if asked_cat and asked_cat in reales:
        cats.append(asked_cat)
    elif asked_cat:
        search_term = f"{search_term} {asked_cat}".strip()[:300]
    for c in list(_skills_categories()) + ["shared-skills"]:
        if c in reales and c not in cats:
            cats.append(c)
    if hoplita_children.notes_scope() is not None:
        cats = [c for c in cats if hoplita_children.notes_allowed(c)]
    elif not cats:
        cats = [own_cat]

    def _key(s):
        return " ".join(_norm_words(s or ""))

    target = _key(search_term)
    for cat in cats:
        try:
            notas = list(notes_mgr.get_notes(cat) or [])
        except Exception:
            continue
        for n in notas:
            tit = _key(n.get("title", ""))
            if tit and (tit == target or target.startswith(tit + " ")
                        or tit.startswith(target + " ")):
                return _skill_payload(n, cat)

    results = []
    for cat in cats:
        results.extend(tool_search_notes(search_term, cat).get("results", []))
    if results:
        results.sort(key=lambda r: r.get("score", 0), reverse=True)
        best = results[0]
        for n in notes_mgr.get_notes(best["category"]):
            if n["id"] == best["id"]:
                return _skill_payload(n, best["category"])
    _idx = skills_index()
    return {"error": f"No saved skill matches '{search_term}'.",
            "available": _idx[:1500] if _idx else "(none saved)",
            "note": "Call use_skill again with one of the TITLES above as "
                    "search_term (the part before the dash). `category` is a "
                    "FOLDER name, never the description — and a skill that "
                    "lives in a project's own folder is not in this list: "
                    "for those, pass category='<that folder>' or read_note "
                    "it."}


def _outside_child_scope(category):
    if hoplita_children.notes_allowed(category):
        return None
    return {"error": hoplita_children.scope_refusal(category)}


def _system_folder_refusal(category):
    if category not in NotesManager.SYSTEM_FOLDERS:
        return None
    return {"error": f"'{category}' is a SYSTEM folder the machine writes "
                     f"(digested tool results / archives / command log) — "
                     f"notes put here are untagged orphans that die with "
                     f"the ring.",
            "instead": ("for the pinned working note the user reads and "
                        "edits (the 📌 post-it): sticky_note(text=...). "
                        "For documentation that must LAST: add_note into a "
                        "user folder — get_note_categories() lists them.")}


def tool_add_note(category, title, content, _system=False):
    blocked = _outside_child_scope(category)
    if blocked:
        return blocked
    if not _system:
        refusal = _system_folder_refusal(category)
        if refusal:
            return refusal
    _wrong = _project_title_refusal(category, title, _system)
    if _wrong:
        return {"added": False, **_wrong}
    if category not in notes_mgr.get_categories():
        notes_mgr.add_category(category)
    n = notes_mgr.add_note(category, title, content)
    fed_tools.after_local_mutation("notes", "add_note", (category,), {}, n)
    return {"added": True, "id": n["id"] if n else None, "title": title}


def tool_delete_note(category, note_id):
    blocked = _outside_child_scope(category)
    if blocked:
        return blocked
    notes_mgr.delete_note(category, note_id)
    fed_tools.after_local_mutation("notes", "delete_note", (category,), {}, None)
    return {"deleted": True}


def tool_update_note(category, note_id, title=None, content=None, changes=None):
    blocked = _outside_child_scope(category)
    if blocked:
        return blocked
    refusal = _system_folder_refusal(category)
    if refusal:
        return refusal
    if changes:
        cur = next((n_ for n_ in (notes_mgr.get_notes(category) or [])
                    if n_.get("id") == note_id), None)
        if cur is None:
            return {"updated": False, "error": "note not found"}
        body = cur.get("content", "") or ""
        for i, ch in enumerate(changes if isinstance(changes, list) else []):
            old_ = (ch or {}).get("old", "")
            if not old_:
                return {"updated": False, "error": f"changes[{i}]: 'old' is empty"}
            n_hits = body.count(old_)
            if n_hits != 1:
                return {"updated": False,
                        "error": f"changes[{i}]: 'old' matches {n_hits} times, "
                                 f"must match exactly once — widen it",
                        "old_preview": old_[:120]}
            body = body.replace(old_, (ch or {}).get("new", ""), 1)
        content = body
    if title is not None:
        _row = next((n_ for n_ in (notes_mgr.data.get("categories", {}) or {}).get(category, []) or []
                     if n_.get("id") == note_id), None)
        _same = _row is not None and (_row.get("title", "") or "").strip().lower() == title.strip().lower()
        _wrong = None if _same else _project_title_refusal(category, title, False)
        if _wrong:
            return {"updated": False, **_wrong}
    ok = notes_mgr.update_note(category, note_id, title=title, content=content)
    if not ok:
        return {"updated": False, "error": "note not found"}
    fed_tools.after_local_mutation("notes", "update_note", (category,), {}, None)
    return {"updated": True, "note_id": note_id}


def tool_get_credential_safe(site):
    c = cred_mgr.get_credential_safe(site)
    return c if c else {"error": f"No creds for '{site}'"}


def tool_get_card(site):
    card = cred_mgr.get_card(site)
    if card:
        return dict(card, site=site)
    c = cred_mgr.get_credential_safe(site)
    if c:
        return {"error": f"'{site}' is stored as kind '{c.get('kind')}', "
                         f"not 'card'.", "kind": c.get("kind")}
    return {"error": f"No credential stored for '{site}'."}


def tool_get_api_key(site):
    key = cred_mgr.get_api_key(site)
    if key:
        return {"site": site, "api_key": key}
    c = cred_mgr.get_credential_safe(site)
    if c:
        return {"error": f"'{site}' is stored as kind '{c.get('kind')}', not "
                         f"'apikey'. There is no API key here.",
                "kind": c.get("kind")}
    return {"error": f"No credential stored for '{site}'."}


def _mail_cred(account):
    boxes = [c for c in cred_mgr.get_all() if (c.get("kind") or "web") == "mail"]
    if not account:
        if len(boxes) == 1:
            return boxes[0], None
        if not boxes:
            return None, {"error": "No mailbox stored. The user adds one in the "
                                   "credential manager (kind 'mail': address, "
                                   "password, IMAP server)."}
        return None, {"error": "Several mailboxes are stored: name one with "
                               "'account'.", "accounts": [c["site"] for c in boxes]}
    c = cred_mgr.get_credential(account)
    if not c:
        return None, {"error": f"No credential stored for '{account}'.",
                      "accounts": [b["site"] for b in boxes]}
    if (c.get("kind") or "web") != "mail":
        return None, {"error": f"'{c['site']}' is stored as kind '{c.get('kind') or 'web'}', "
                               f"not 'mail'.", "accounts": [b["site"] for b in boxes]}
    return c, None


def tool_mail(op, a):
    import hoplita_mail as _hm
    cred, err = _mail_cred(a.get("account"))
    if err:
        return err
    try:
        if op == "folders":
            return _hm.folders(cred)
        if op == "list":
            return _hm.list_messages(cred, a.get("folder") or "INBOX", a.get("limit", 25),
                                     bool(a.get("unseen")), a.get("since"), a.get("before"),
                                     a.get("from"), a.get("subject"), a.get("text"),
                                     a.get("to"))
        if op == "read":
            if a.get("uid") is None:
                return {"error": "Missing uid (mail_list gives it)."}
            return _hm.read_message(cred, a["uid"], a.get("folder") or "INBOX",
                                    a.get("max_chars", 20000))
        if op == "move":
            return _hm.move_messages(cred, a.get("uids"), a.get("to"),
                                     a.get("folder") or "INBOX")
        if op == "send":
            return _hm.send_message(cred, a.get("to"), a.get("subject") or "",
                                    a.get("body") or "", a.get("cc"), a.get("bcc"),
                                    a.get("html"), a.get("attachments"),
                                    a.get("reply_to_uid"), a.get("folder") or "INBOX",
                                    a.get("from_name"), a.get("save_sent", True) is not False)
    except _hm.MailError as e:
        return {"error": str(e), "account": cred.get("site")}
    return {"error": f"unknown mail operation '{op}'"}


def tool_get_otp(site):
    code = cred_mgr.get_otp(site)
    return {"otp": code, "site": site} if code else {"error": f"No OTP for '{site}'"}


def _outside_child_projects(project):
    if hoplita_children.projects_allowed(project):
        return None
    return {"error": hoplita_children.project_refusal(project)}


def tool_get_todo_context():
    _scope = hoplita_children.projects_scope()
    if _scope is None:
        return {"context": todo_mgr.get_context_text()}
    return {"context": todo_mgr.get_context_text(projects=_scope),
            "scope": _scope}


_APT_DATE_FMTS = ("%Y-%m-%d %H:%M", "%Y-%m-%d")
_APT_DATE_RULE = "'YYYY-MM-DD HH:MM', or 'YYYY-MM-DD' for a whole day"


def _apt_date_ok(date):
    for fmt in _APT_DATE_FMTS:
        try:
            if datetime.strptime(date, fmt).strftime(fmt) == date:
                return True
        except (TypeError, ValueError):
            pass
    return False


def _apt_date_match(stored, date):
    stored = str(stored or "")
    return stored == date if len(date) > 10 else stored[:10] == date


def tool_add_appointment(date, title, desc, urgency="Normal", project=None):
    date = str(date or "").strip()
    if not _apt_date_ok(date):
        return {"error": f"date must be {_APT_DATE_RULE}; got '{date}'"}
    a = todo_mgr.add_appointment(date, title, desc, "", urgency, project)
    return {"added": True, "id": a["id"], "project": a["project"]}


def _resolve_task_deps(names, project, exclude_uuid=None):
    pool = [t for t in todo_mgr.get_tasks(project)
            if t.get("uuid") and t.get("uuid") != exclude_uuid]
    uuids, misses = [], []
    for name in (names or []):
        name = str(name or "").strip()
        if not name:
            continue
        hit = next((t for t in pool if t["uuid"] == name), None)
        if hit is None:
            hit = next((t for t in pool
                        if name.lower() in t.get("title", "").lower()), None)
        if hit is None:
            misses.append(name)
        elif hit["uuid"] not in uuids:
            uuids.append(hit["uuid"])
    return uuids, misses


def tool_add_task(title, desc, priority="Media", progress=0, project=None,
                  depends_on=None):
    blocked = _outside_child_projects(project)
    if blocked:
        return blocked
    if project and int(progress or 0) < 100 and _is_project_folder(project):
        _open = [tk for tk in todo_mgr.get_tasks(project)
                 if int(tk.get("progress") or 0) < 100]
        if len(_open) >= _OPEN_TASKS_CAP:
            return {"error": "board_full",
                    "message": f"'{project}' already has {len(_open)} open tasks — "
                               f"the board is for what is being done NOW "
                               f"(max {_OPEN_TASKS_CAP}). Finish or delete one, or "
                               f"write this into 'future' (project_write).",
                    "open": [(tk.get("title") or "")[:80] for tk in _open]}
    t = todo_mgr.add_task(priority, title, desc, "", progress, project)
    fed_tools.after_local_mutation("todo", "add_task", (), {}, t)
    out = {"added": True, "id": t["id"], "project": t["project"]}
    if depends_on:
        uuids, misses = _resolve_task_deps(depends_on, t["project"],
                                           exclude_uuid=t.get("uuid"))
        todo_mgr.update_task(t["id"], deps=uuids)
        fed_tools.after_local_mutation(
            "todo", "update_task", (t["id"],), {"project": t["project"]}, t)
        out["deps"] = len(uuids)
        if misses:
            out["deps_not_found"] = misses
    return out


def tool_task_depends(task_title, depends_on, project=None):
    _scope = hoplita_children.projects_scope()
    for t in todo_mgr.get_tasks(project):
        if _scope is not None and (t.get("project") or "general") not in _scope:
            continue
        if task_title.lower() in t["title"].lower():
            uuids, misses = _resolve_task_deps(
                depends_on, t.get("project"), exclude_uuid=t.get("uuid"))
            todo_mgr.update_task(t["id"], deps=uuids)
            fed_tools.after_local_mutation(
                "todo", "update_task", (t["id"],),
                {"project": t.get("project")}, t)
            out = {"updated": True, "task": t["title"], "deps": len(uuids)}
            if misses:
                out["deps_not_found"] = misses
            return out
    return {"error": f"Task '{task_title}' not found"}


def tool_update_task_progress(task_title, new_progress):
    _scope = hoplita_children.projects_scope()
    for t in todo_mgr.get_tasks():
        if _scope is not None and (t.get("project") or "general") not in _scope:
            continue
        if task_title.lower() in t["title"].lower():
            todo_mgr.update_task(t["id"], progress=new_progress)
            fed_tools.after_local_mutation(
                "todo", "update_task", (), {"project": t.get("project")}, t)
            return {"updated": True, "task": t["title"], "progress": new_progress}
    return {"error": f"Task '{task_title}' not found"}


def tool_list_todo_projects():
    _scope = hoplita_children.projects_scope()
    if _scope is not None:
        return {"projects": [p for p in todo_mgr.get_projects() if p in _scope],
                "scope": _scope}
    out = {"projects": todo_mgr.get_projects()}
    try:
        sql = _get_sql()
        if sql is not None:
            linked = {}
            for name in out["projects"]:
                p = sql.meta_get(f"code_path:{name}")
                if p:
                    linked[name] = p
            if linked:
                out["code_linked"] = linked
    except Exception:
        pass
    return out


def tool_list_appointments(project=None):
    apts = todo_mgr.get_appointments(project)
    LIMIT = 40
    if len(apts) <= LIMIT:
        return {"count": len(apts),
                "appointments": [{"id": a["id"], "date": a["date"],
                                  "title": a["title"],
                                  "description": a.get("description", ""),
                                  "urgency": a.get("urgency", "Normal"),
                                  "project": a.get("project", "general")}
                                 for a in apts]}
    from collections import Counter
    by_title = Counter(a["title"] for a in apts)
    return {"count": len(apts), "truncated": True,
            "summary_by_title": [{"title": t, "copies": n}
                                 for t, n in by_title.most_common()],
            "hint": "There are many appointments (probably duplicates). Use "
                    "delete_all_appointments with 'contains' to clean up by "
                    "title, e.g. contains='dentist'."}


def tool_delete_appointment(title=None, date=None, apt_id=None):
    if apt_id:
        hit = next((a for a in todo_mgr.get_appointments()
                    if a["id"] == str(apt_id).strip()), None)
        if hit is None:
            return {"error": f"No appointment with id '{apt_id}'"}
        todo_mgr.delete_appointment(hit["id"])
        return {"deleted": 1, "title": hit["title"], "dates": [hit["date"]]}
    tl = (title or "").strip().lower()
    if not tl:
        return {"error": "Missing title (or the id list_appointments gives)"}
    if date is not None:
        date = str(date).strip()
        if not _apt_date_ok(date):
            return {"error": f"date must be {_APT_DATE_RULE}; got '{date}'"}
    matches = [a for a in todo_mgr.get_appointments()
               if a["title"].strip().lower() == tl
               and (date is None or _apt_date_match(a["date"], date))]
    if not matches:
        return {"error": f"No appointments with title '{title}'"
                         + (f" and date {date}" if date else "")}
    for a in matches:
        todo_mgr.delete_appointment(a["id"])
    return {"deleted": len(matches), "title": title,
            "dates": [a["date"] for a in matches]}


def tool_delete_all_appointments(contains=None, date=None, project=None,
                                 confirm=False):
    if date is not None:
        date = str(date).strip()
        if not _apt_date_ok(date):
            return {"error": f"date must be {_APT_DATE_RULE}; got '{date}'"}
    apts = todo_mgr.get_appointments(project)
    sub = (contains or "").strip().lower()
    victims = [a for a in apts
               if (not sub or sub in a["title"].strip().lower())
               and (date is None or _apt_date_match(a["date"], date))]
    if not sub and date is None and project is None:
        return {"error": "Total deletion of the calendar disabled via "
                         "tool. Add a filter: contains (title substring), "
                         "date, or project.",
                "would_delete": len(victims)}
    for a in victims:
        todo_mgr.delete_appointment(a["id"])
    return {"deleted": len(victims),
            "filter": {"contains": contains, "date": date, "project": project}}


def tool_add_todo_project(name):
    if hoplita_children.projects_scope() is not None:
        return {"error": hoplita_children.project_refusal(name)}
    return todo_mgr.add_project(name)


def tool_delete_todo_project(name):
    if hoplita_children.projects_scope() is not None:
        return {"error": hoplita_children.project_refusal(name)}
    return todo_mgr.delete_project(name)


def tool_list_cron():
    return {"jobs": [
        {"id": j["id"], "name": j["name"], "type": j["schedule_type"],
         "enabled": j.get("enabled", True), "next": j.get("next_run"),
         "runs": j.get("run_count", 0),
         "context": ((j.get("context") or {}).get("title") or None),
         "own_tab": bool(j.get("own_tab")),
         "this_tab": bool(_turn_job()) and j["id"] == _turn_job()}
        for j in cron_mgr.get_jobs()]}


def _turn_job():
    return getattr(_TURN_SESSION, "job", "") or ""


import threading as _thr_sess
_TURN_SESSION = _thr_sess.local()
_THIS_SESSION = ("this", "this one", "current", "esta", "esta conversacion",
                 "esta conversación", "esta conver", "la actual", "actual",
                 "self", "@this")


def _set_turn_session(uuid_, code_project="", conv="", rag_docs=0, images=None,
                      job=""):
    _TURN_SESSION.uuid = (uuid_ or "").strip()
    _TURN_SESSION.job = (job or "").strip()
    _TURN_SESSION.project = (code_project or "").strip()
    _TURN_SESSION.rag_docs = int(rag_docs or 0)
    _TURN_SESSION.images = list(images or [])
    _TURN_SESSION.conv = ((uuid_ or "").strip() or (conv or "").strip())
    _TURN_SESSION.tab = (conv or "").strip()


_TURN_SNAPSHOTS = {}
_TURN_SNAPSHOT_FIELDS = ("uuid", "project", "rag_docs", "images", "conv", "tab",
                         "job")


def _turn_snapshot_file(turn_id):
    if turn_id:
        _TURN_SNAPSHOTS[turn_id] = {k: getattr(_TURN_SESSION, k, None)
                                    for k in _TURN_SNAPSHOT_FIELDS}


def _turn_snapshot_drop(turn_id):
    _TURN_SNAPSHOTS.pop(turn_id, None)


def _turn_session():
    return getattr(_TURN_SESSION, "uuid", "") or ""


def _turn_conv():
    return (getattr(_TURN_SESSION, "conv", "") or "").strip()


def _turn_conv_ids():
    out = []
    for i in (getattr(_TURN_SESSION, "tab", "") or "",
              getattr(_TURN_SESSION, "uuid", "") or "",
              _turn_conv()):
        if i and i not in out:
            out.append(i)
    return out


def _turn_project():
    return getattr(_TURN_SESSION, "project", "") or ""


def _turn_attached_images():
    return list(getattr(_TURN_SESSION, "images", None) or [])


def _attached_images_of(messages):
    import fed_agent as _fa_att
    for m in reversed(messages or []):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        out = []
        c = m.get("content")
        _shot_head = _fa_att.HARNESS_TAG + "\n" + _fa_att._t("canvas.shot")
        if isinstance(c, list) and any(isinstance(p_, dict) and p_.get("type") == "text"
                                       and str(p_.get("text", "")).startswith(_shot_head)
                                       for p_ in c):
            continue
        if isinstance(c, list):
            for part in c:
                if not isinstance(part, dict) or part.get("type") != "image_url":
                    continue
                url = str(((part.get("image_url") or {}).get("url")) or "")
                mm = re.match(r"^data:([\w/.+-]+);base64,(.+)$", url, re.S)
                if mm:
                    out.append((mm.group(1), mm.group(2)))
        if out:
            return out
    return []


def _turn_rag():
    return int(getattr(_TURN_SESSION, "rag_docs", 0) or 0)


def _project_path(path):
    root = _turn_project()
    if not root or not isinstance(path, (str, type(None))):
        return path
    p = (path or "").strip()
    if not p or p == ".":
        return root
    p = os.path.expanduser(p)
    if os.path.isabs(p):
        return p
    return os.path.normpath(os.path.join(root, p))


def _spill_path_intercept(name, args):
    if not isinstance(args, dict) or notes_mgr is None:
        return None
    p = args.get("path")
    if not isinstance(p, str) or not p.strip():
        return None
    p = p.strip()
    if not (p.startswith(f"{WORKING_MEMORY}:") or p.startswith(f"{WORKING_MEMORY}/")):
        try:
            rows = (notes_mgr.data.get("categories", {}) or {}).get(WORKING_MEMORY, []) or []
            if not any((r_.get("title") or "").strip() == p and not r_.get("deleted")
                       for r_ in rows):
                return None
        except Exception:
            return None
    title = p.split("/", 1)[1] if p.startswith(f"{WORKING_MEMORY}/") else p
    res = tool_read_note(WORKING_MEMORY, title=title,
                         start_line=args.get("start_line"),
                         end_line=args.get("end_line"))
    if isinstance(res, dict):
        res = dict(res)
        res["note"] = (f"'{p}' is a working-memory NOTE, not a file: served "
                       f"here; its call is {_note_open_call(WORKING_MEMORY, title)}.")
    return json.dumps(res, ensure_ascii=False, default=str)


def _spill_aware(name, fn):
    def run(a):
        hit = _spill_path_intercept(name, a)
        return hit if hit is not None else fn(a)
    return run


def _project_scoped(fn, keys=("path", "project_path")):
    def run(a):
        if isinstance(a, dict) and _turn_project():
            a = dict(a)
            for i, k in enumerate(keys):
                if k in a or i == 0:
                    a[k] = _project_path(a.get(k))
        return fn(a)
    return run


def _cron_context_from_session(ref, live=False):
    if (ref or "").strip().lower() in _THIS_SESSION:
        _cur = _turn_session()
        if not _cur:
            return None, ("this conversation is not saved, so there is "
                          "nothing to attach to. In the GUI: save it (it gets "
                          "a name) and attach it by that name. From the CLI "
                          "there are no saved conversations at all, so name an "
                          "already saved one or create the job without "
                          "context.")
        ref = _cur
    if live:
        row, err = _session_resolve(ref)
        if err:
            return None, err
        return {"session": row.get("uuid"), "title": row.get("title", ""),
                "dated": row.get("updated_at", ""), "source": "live",
                "text": "", "live": True}, ""
    b = tool_session_brief({"session": ref})
    if b.get("error"):
        return None, b["error"]
    return {"session": b["uuid"], "title": b["title"], "dated": b["dated"],
            "source": b["source"], "text": b["text"],
            "live": False, "frozen": True}, ""


def tool_add_cron(name, instruction, stype, interval=0, fixed_dt="", daily_times=None,
                  target="local", context_session="", model="",
                  context_live=None, context_current=None, own_tab=False):
    ctx = None
    if context_current is True and not (context_session or "").strip() \
            and not _turn_session():
        return {"error": ("you asked for THIS conversation's context and there "
                          "is none: this turn does not belong to a saved "
                          "conversation. Save it first, name another one, or "
                          "create the job without context.")}
    if (context_current is None or context_current) \
            and not (context_session or "").strip() and _turn_session():
        context_session = "this"
        if context_live is None:
            context_live = True
    if (context_session or "").strip():
        if context_live is None:
            context_live = True
        ctx, err = _cron_context_from_session(context_session, context_live)
        if err:
            return {"error": f"context session: {err}"}
    if own_tab and (target or "local") != "local":
        return {"error": "own_tab is for a job that runs here: a job for a "
                         "child or a friend runs on their machine, in their tabs"}
    j = cron_mgr.add_job(name, instruction, stype, interval, fixed_dt, daily_times,
                         target=target, context=ctx, model=model,
                         own_tab=bool(own_tab))
    out = {"added": True, "id": j["id"], "target": j.get("target", "local"),
           "own_tab": j.get("own_tab", False)}
    if ctx:
        out["context"] = {"title": ctx["title"], "dated": ctx["dated"],
                          "source": ctx["source"],
                          "live": ctx["live"]}
    return out


def tool_cron_set_context(job_id, session="", live=True):
    if not (session or "").strip():
        j = cron_mgr.update_job(job_id, context=None)
        return {"updated": True, "context": None} if j else {"error": "No encontrado"}
    ctx, err = _cron_context_from_session(session, live)
    if err:
        return {"error": err}
    j = cron_mgr.update_job(job_id, context=ctx)
    if not j:
        return {"error": "No encontrado"}
    return {"updated": True, "context": {"title": ctx["title"],
                                         "dated": ctx["dated"],
                                         "source": ctx["source"],
                                         "live": ctx["live"]}}


_CRON_EDITABLE = ("name", "instruction", "schedule_type", "interval_minutes",
                  "fixed_datetime", "daily_times", "weekdays", "model", "enabled")


def tool_update_cron(a):
    jid = (a.get("job_id") or "").strip() or _turn_job()
    if not jid:
        return {"error": "no job_id, and this tab does not work for a job: "
                         "name the job (list_cron gives the ids)"}
    old = next((j for j in cron_mgr.get_jobs() if j["id"] == jid), None)
    if old is None:
        return {"error": f"no job with id {jid}"}
    ch = {k: a[k] for k in _CRON_EDITABLE if k in a and a[k] is not None}
    if not ch:
        return {"error": "nothing to change: give instruction, name, "
                         "schedule_type, interval_minutes, daily_times, "
                         "weekdays, fixed_datetime, model or enabled"}
    st = ch.get("schedule_type", old.get("schedule_type"))
    if st not in ("interval", "daily", "weekly", "fixed", "startup"):
        return {"error": f"schedule_type {st!r}: interval, daily, weekly, "
                         "fixed or startup"}
    if "interval_minutes" in ch:
        try:
            ch["interval_minutes"] = int(ch["interval_minutes"])
        except (TypeError, ValueError):
            return {"error": "interval_minutes must be a whole number"}
    if st == "interval" and int(ch.get("interval_minutes",
                                       old.get("interval_minutes") or 0)) < 1:
        return {"error": "an interval job needs interval_minutes of 1 or more"}
    if "instruction" in ch and (old.get("instruction") or "").startswith(
            "__ALERT_ON_COMPLETE__\n") \
            and not ch["instruction"].startswith("__ALERT_ON_COMPLETE__\n"):
        ch["instruction"] = "__ALERT_ON_COMPLETE__\n" + ch["instruction"]
    j = cron_mgr.update_job(jid, **ch)
    if not j:
        return {"error": f"no job with id {jid}"}
    try:
        import fed_agent
        fed_agent.tool_progress("cron.updated", job=jid,
                                fields=",".join(sorted(ch)))
    except Exception:
        pass
    return {"updated": True, "id": jid, "changed": sorted(ch),
            "name": j.get("name"), "schedule_type": j.get("schedule_type"),
            "interval_minutes": j.get("interval_minutes"),
            "daily_times": j.get("daily_times"), "enabled": j.get("enabled", True),
            "next": j.get("next_run")}


def tool_delete_cron(job_id):
    cron_mgr.delete_job(job_id)
    return {"deleted": True}


def tool_toggle_cron(job_id, enabled):
    j = cron_mgr.update_job(job_id, enabled=enabled)
    return {"updated": True} if j else {"error": "No encontrado"}


_current_cron_job_id = None


def tool_set_cron_context(job_id):
    global _current_cron_job_id
    _current_cron_job_id = job_id
    return {"set": True, "job_id": job_id}


def tool_delete_current_cron():
    global _current_cron_job_id
    jid = _turn_job() or _current_cron_job_id
    if not jid:
        return {"error": "No active CRON"}
    cron_mgr.delete_job(jid)
    if jid == _current_cron_job_id:
        _current_cron_job_id = None
    try:
        import fed_agent
        fed_agent.tool_progress("cron.self_deleted", job=jid)
    except Exception:
        pass
    return {"deleted": True, "job_id": jid}


def tool_disable_current_cron():
    jid = _turn_job() or _current_cron_job_id
    if not jid:
        return {"error": "No active CRON"}
    cron_mgr.update_job(jid, enabled=False)
    try:
        import fed_agent
        fed_agent.tool_progress("cron.self_disabled", job=jid)
    except Exception:
        pass
    return {"disabled": True, "job_id": jid}


def tool_alert(title, message):
    print(f"[MCP] 🔔 ALERT: {title} — {message}")
    return {"alerted": True, "title": title, "message": message}


def tool_get_agentic_config():
    base_url, _, model = _resolve_endpoint("API")
    return {"base_url": base_url, "model": model,
            "platform": f"{platform.system()}/{platform.machine()}"}


def tool_search_rag(query, top_k=5):
    results = []
    for n in tool_search_notes(query).get("results", []):
        results.append({"source": f"notes/{n['category']}",
                        "text": n.get("content", "")[:300], "title": n["title"]})
    for e in search_experience(query, top_k).get("results", []):
        results.append({"source": "experience", "text": e.get("document", "")[:300],
                        "similarity": e.get("similarity", 0)})
    if not results:
        return {"results": [], "count": 0,
                "note": "Nothing about this in the knowledge base (notes and "
                        "experience). Do NOT infer an answer from unrelated "
                        "material: say so, or get the facts with another tool."}
    return {"results": results[:top_k], "count": len(results)}


_SCRAPE_MIN_USEFUL = 200


def _looks_like_chrome(text):
    t = (text or "").strip()
    if not t:
        return True, "empty"
    enlaces = sum(len(m.group(0)) for m in re.finditer(r"\[[^\]]*\]\([^)]*\)", t))
    ratio = enlaces / max(len(t), 1)
    clean = re.sub(r"\[[^\]]*\]\([^)]*\)", " ", t)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", clean)]
    prose = max((len(p) for p in paragraphs), default=0)
    if prose >= 400:
        return False, ""
    if ratio > 0.35:
        return True, f"mostly links ({ratio:.0%} of the text is link markup)"
    if prose < 200:
        return True, f"no prose paragraph (longest is {prose} chars)"
    return False, ""

def _scrape_crawl4ai(url, timeout_s, max_depth=0, max_pages=10):
    from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
    kwargs = dict(cache_mode=CacheMode.BYPASS, page_timeout=timeout_s * 1000)
    def _c4b(key, default):
        return CONFIG.getboolean("CRAWL4AI", key, fallback=default) if CONFIG else default
    if _c4b("override_navigator", True):
        kwargs["override_navigator"] = True
    if _c4b("magic", False):
        kwargs["magic"] = True
    if _c4b("simulate_user", False):
        kwargs["simulate_user"] = True
    if max_depth > 0:
        from crawl4ai.deep_crawling import BFSDeepCrawlStrategy
        kwargs["deep_crawl_strategy"] = BFSDeepCrawlStrategy(
            max_depth=max_depth, max_pages=max_pages, include_external=False)
    run_cfg = CrawlerRunConfig(**kwargs)
    browser_cfg = None
    if _c4b("stealth", True):
        try:
            from crawl4ai import BrowserConfig
            browser_cfg = BrowserConfig(enable_stealth=True, verbose=False)
        except Exception:
            browser_cfg = None

    async def _run():
        _cw = (AsyncWebCrawler(config=browser_cfg, verbose=False)
               if browser_cfg is not None else AsyncWebCrawler(verbose=False))
        async with _cw as c:
            res = await c.arun(url=url, config=run_cfg)
            pages = res if isinstance(res, list) else [res]
            parts = []
            for p in pages:
                if not getattr(p, "success", False):
                    continue
                md = getattr(p, "markdown", "") or ""
                md = getattr(md, "raw_markdown", md)
                if str(md).strip():
                    u = getattr(p, "url", url)
                    parts.append(f"# {u}\n{md}" if len(pages) > 1 else str(md))
            if not parts:
                err = getattr(pages[0], "error_message", "no content") if pages else "no result"
                raise Exception(err)
            return "\n\n---\n\n".join(parts)
    try:
        return asyncio.run(_run())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()


def _scrape_requests(url, timeout_s=30):
    r = requests.get(url, headers={"User-Agent": _LINUX_UA},
                     timeout=timeout_s, allow_redirects=True)
    r.raise_for_status()
    if "html" not in r.headers.get("content-type", ""):
        return r.text
    text = re.sub(r'<script[^>]*>.*?</script>', '', r.text,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    import html as html_mod
    return html_mod.unescape(text)


def tool_scrape_url(url, max_chars=10000, escalate=True, depth=None):
    if not url.startswith("http"):
        url = "https://" + url
    tried = []
    _best = None

    def _ok(text, engine):
        return {"engine": engine, "url": url, "text": text[:max_chars],
                "chars": len(text), "tried": tried}

    try:
        _to = CONFIG.getint("CRAWL4AI", "timeout", fallback=60) if CONFIG else 60
        _depth = (int(depth) if depth is not None else
                  (CONFIG.getint("CRAWL4AI", "max_depth", fallback=0) if CONFIG else 0))
        _mp = CONFIG.getint("CRAWL4AI", "max_pages", fallback=10) if CONFIG else 10
        txt = _scrape_crawl4ai(url, _to, max_depth=_depth, max_pages=_mp)
        if txt and len(txt.strip()) >= _SCRAPE_MIN_USEFUL:
            _chrome, _pq = _looks_like_chrome(txt)
            if not _chrome:
                return _ok(txt, "crawl4ai")
            tried.append(f"crawl4ai: {_pq}")
            _best = _best or (txt, "crawl4ai", _pq)
        else:
            tried.append("crawl4ai: too little content")
    except ImportError:
        tried.append("crawl4ai: not installed")
    except Exception as e:
        tried.append(f"crawl4ai: {e}"[:120])

    try:
        txt = _scrape_requests(url)
        if txt and len(txt.strip()) >= _SCRAPE_MIN_USEFUL:
            _chrome, _pq = _looks_like_chrome(txt)
            if not _chrome:
                return _ok(txt, "requests")
            tried.append(f"requests: {_pq}")
            _best = _best or (txt, "requests", _pq)
        else:
            tried.append("requests: too little content (JavaScript page?)")
    except requests.exceptions.HTTPError as e:
        _code = getattr(getattr(e, "response", None), "status_code", 0)
        if _code in (404, 410, 401):
            return {"engine": "requests", "url": url, "text": "",
                    "chars": 0, "http_status": _code,
                    "tried": tried + [f"requests: HTTP {_code}"],
                    "error": f"HTTP {_code}",
                    "hint": ("The server answered: this URL is not there "
                             "(404/410) or needs credentials (401). It was NOT "
                             "a fetch that failed, so no browser was started — "
                             "one would take minutes to be told the same. "
                             "Check the path: a branch, a tag or a file name "
                             "that does not exist looks exactly like this.")}
        tried.append(f"requests: {e}"[:120])
    except Exception as e:
        tried.append(f"requests: {e}"[:120])

    if escalate:
        try:
            import socket as _sck
            from urllib.parse import urlparse as _urp
            _host = _urp(url).hostname or ""
            if _host:
                _sck.getaddrinfo(_host, None)
        except OSError:
            return {"error": "dns_unresolvable", "url": url,
                    "message": f"'{_host}' does not resolve: the URL is "
                               f"dead or invented. Do NOT retry it — find "
                               f"the information through another source.",
                    "tried": tried}
        except Exception:
            pass
        print(f"[SCRAPE] {url}: both scrapers failed → escalating to browser-use",
              file=sys.stderr)
        try:
            import fed_agent as _fa_mod
            _fa_mod.tool_progress("scrape.escalating", url=url[:120])
        except Exception:
            pass
        res = tool_browse_web(
            task=("Read this page and return ALL its useful readable content "
                  "verbatim: prices, plans, tables, terms, code blocks. "
                  "Do not summarise, do not shorten, do not comment.\n\n"
                  "HOW TO READ IT: the page is probably longer than the "
                  "window, so one scroll is not reading it. Keep scrolling "
                  "while what appears is NEW AND PART OF WHAT WAS ASKED "
                  "FOR — content that loads as you scroll counts.\n\n"
                  "STOP as soon as any of these is true, and answer with "
                  "what you have:\n"
                  "  · scrolling stops revealing anything new (a document "
                  "that ends: a README, an article, a docs page);\n"
                  "  · what keeps appearing is MORE OF THE SAME LIST — "
                  "headline after headline, product after product. That is "
                  "an endless feed and it has no bottom: scrolling it to the "
                  "end never happens, and everything past the first screens "
                  "is unrelated to what was asked. Stop and SAY it is an "
                  "endless feed, so whoever asked knows the page was not "
                  "exhausted;\n"
                  "  · you already have what was asked for.\n"
                  "Reaching the bottom is not the goal. Coming back with the "
                  "content that answers the request is.\n\n"
                  "HOW TO ANSWER: the text itself IS your answer. Do not "
                  "write it to a file, do not save it anywhere, do not answer "
                  "with a description of what you did — a report that a file "
                  "was written is not the content and is worth nothing to "
                  "whoever asked."),
            url=url)
        if isinstance(res, dict) and not res.get("error"):
            body = str(res.get("result") or res.get("final_result") or "")
            if len(body.strip()) >= _SCRAPE_MIN_USEFUL:
                return _ok(body, "browser-use")
            if body.strip():
                tried.append(f"browser-use: {len(body.strip())} chars — a "
                             f"report, not the page")
            else:
                tried.append("browser-use: empty result")
        else:
            tried.append(f"browser-use: {(res or {}).get('error')}"[:120])

    if _best:
        txt, eng, pq = _best
        return {"engine": eng, "url": url, "text": txt[:max_chars],
                "chars": len(txt), "tried": tried,
                "warning": (f"This looks like page chrome, not the article "
                            f"({pq}). The real content was not extracted. "
                            f"Do NOT call scrape_url on this URL again — it "
                            f"will return the same. Use browse_web with a "
                            f"specific task, or work from another source.")}
    return {"error": f"Could not read {url} with any engine", "tried": tried,
            "hint": "Try browse_web with a specific task, or screenshot_web."}



def tool_screenshot_web(url, headless=None, output_path=None):
    if headless is None:
        headless = CONFIG.getboolean("BROWSER", "headless", fallback=False)
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"error": "playwright no instalado. pip install playwright && playwright install chromium"}

    async def _do():
        pw = await async_playwright().start()
        try:
            br = await pw.chromium.launch(headless=headless)
            try:
                page = await br.new_page(viewport={"width": 1280, "height": 900})
                full_url = url if url.startswith("http") else "https://" + url
                await page.goto(full_url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(2000)
                pdf_bytes = await page.pdf(format="A4", print_background=True)
                title = await page.title()
                text = await page.evaluate("document.body ? document.body.innerText.substring(0, 5000) : ''")
                return title, text, pdf_bytes
            finally:
                await br.close()
        finally:
            await pw.stop()

    try:
        title, text, pdf_bytes = asyncio.run(_do())
    except Exception as e:
        return {"error": str(e)}

    if output_path:
        out = os.path.expanduser(output_path)
    else:
        out = os.path.join("/tmp", f"screenshot_{int(time.time())}.pdf")
    try:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "wb") as f:
            f.write(pdf_bytes)
    except Exception as e:
        return {"error": f"Could not save PDF: {e}"}

    return {"title": title, "text": text, "pdf_path": out, "pdf_size": len(pdf_bytes)}



def _is_small_model(model_name):
    m = (model_name or "").lower()
    return any(s in m for s in ("3b", "7b", "8b", "ministral", "phi-3", "phi-4", "mini",
                                "gemma-2b", "qwen2.5-3b", "qwen3-4b", "tiny"))


def _browse_force_schema():
    mode = (CONFIG.get("BROWSER", "structured_output", fallback="auto").strip().lower()
            if CONFIG else "auto")
    if mode in ("force", "true", "1", "yes", "on"):
        return True
    if mode in ("off", "false", "0", "no"):
        return False
    return True


def _browse_endpoint(endpoint=None):
    raw = CONFIG.get("BROWSER", "model", fallback="").strip() if CONFIG else ""
    if raw:
        return _resolve_endpoint("BROWSER")
    if isinstance(endpoint, dict) and (endpoint.get("model") or "").strip():
        return ((endpoint.get("base_url") or "").rstrip("/"),
                endpoint.get("api_key") or "", endpoint["model"].strip())
    ctx = getattr(_ORCH, "ctx", None)
    ep = getattr(ctx, "endpoint", None) if ctx else None
    if isinstance(ep, dict) and (ep.get("model") or "").strip():
        return ((ep.get("base_url") or "").rstrip("/"),
                ep.get("api_key") or "", ep["model"].strip())
    return _resolve_endpoint("BROWSER")


async def _browse_async(task, start_url, headless, max_steps, endpoint=None):
    try:
        from browser_use.agent.service import Agent
        from browser_use.llm import ChatOpenAI as BUChatOpenAI
        from browser_use.browser.session import BrowserSession
    except ImportError as e:
        return {"error": f"browser-use no disponible: {e}", "success": False}

    full_task = task
    _initial_actions = None
    if start_url:
        u = start_url if start_url.startswith("http") else "https://" + start_url
        _initial_actions = [{"navigate": {"url": u, "new_tab": False}}]
        full_task = (f"You are already on {u} — it was opened for you; do not "
                     f"retype or shorten that URL. Then: {task}")
    try:
        import fed_agent as _fa_mod2
        _tail_ctx = _fa_mod2.conversation_tail_for_tool()
    except Exception:
        _tail_ctx = []
    if _tail_ctx:
        full_task += (
            "\n\nCONVERSATION CONTEXT (oldest first). Whatever it already "
            "settled — the thing, its place, its colour, its size, its "
            "date, its version — RULES: if this task contradicts it, "
            "trust the conversation:\n  "
            + "\n  ".join(_tail_ctx))

    base_url, api_key, model = _browse_endpoint(endpoint)
    if not model:
        return {"error": "BROWSER model no configurado", "success": False}

    is_small = _is_small_model(model)

    _llm_kwargs = dict(
        model=model,
        api_key=api_key or "dummy",
        base_url=base_url,
        dont_force_structured_output=_browse_force_schema() is False,
        add_schema_to_system_prompt=_browse_force_schema() is False,
        temperature=0.1,
        frequency_penalty=None,
        max_completion_tokens=_llm_max_tokens(),
    )
    try:
        llm = BUChatOpenAI(**_llm_kwargs)
    except TypeError as e:
        print(f"[BROWSE] ⚠ ChatOpenAI kwargs unsupported ({e}) → retrying lean",
              file=sys.stderr)
        for k in ("dont_force_structured_output", "add_schema_to_system_prompt",
                  "frequency_penalty", "max_completion_tokens"):
            _llm_kwargs.pop(k, None)
        llm = BUChatOpenAI(**_llm_kwargs)

    vp_w = CONFIG.getint("BROWSER", "viewport_width", fallback=1366)
    vp_h = CONFIG.getint("BROWSER", "viewport_height", fallback=900)
    cookies_path = os.path.join(hoplita_base(), "browser_cookies.json")

    session_kwargs = {
        "headless": headless,
        "viewport": {"width": vp_w, "height": vp_h},
        "user_agent": CONFIG.get("BROWSER", "user_agent", fallback=_MAC_UA),
        "locale": CONFIG.get("BROWSER", "locale", fallback="es-ES"),
        "wait_between_actions": CONFIG.getfloat("BROWSER", "wait_between_actions", fallback=1.2),
        "wait_for_network_idle_page_load_time": CONFIG.getfloat("BROWSER", "wait_network_idle", fallback=3.0),
        "cookies_file": cookies_path,
        "args": ["--password-store=basic"],
    }
    browser_session = None
    while browser_session is None:
        try:
            browser_session = BrowserSession(**session_kwargs)
        except TypeError as e:
            _bad = re.search(r"unexpected keyword argument '(\w+)'", str(e))
            if not _bad or _bad.group(1) not in session_kwargs:
                raise
            print(f"[BROWSE] ⚠ this browser-use does not take "
                  f"'{_bad.group(1)}': launched without it", file=sys.stderr)
            session_kwargs.pop(_bad.group(1))

    sensitive_data = {}
    _mention_only = []
    _task_low = (full_task or "").lower()
    if cred_mgr:
        for entry in cred_mgr.list_sites():
            site = entry["site"] if isinstance(entry, dict) else str(entry)
            if not _cred_boards(site, _task_low):
                _mention_only.append(site)
                continue
            cred = cred_mgr.get_credential(site)
            if cred:
                sensitive_data[f"{site}_username"] = cred.get("username", "")
                sensitive_data[f"{site}_password"] = cred.get("password", "")
                otp = cred_mgr.get_otp(site)
                if otp:
                    sensitive_data[f"{site}_otp"] = otp

    cred_hint = ""
    if sensitive_data:
        sites = sorted({k.rsplit("_", 1)[0] for k in sensitive_data})
        cred_hint = (
            f"\n\nAVAILABLE CREDENTIALS: {', '.join(sites)}. "
            f"Use placeholders {{{sites[0]}_username}} / {{{sites[0]}_password}} — "
            "the system substitutes the real values at runtime."
        )
    if _mention_only:
        cred_hint += (
            f"\n\nOTHER STORED CREDENTIALS (names only, their secrets are "
            f"NOT on board this run): {', '.join(sorted(_mention_only))}. "
            "If this task turns out to need one of them, do NOT improvise a "
            "login: finish with a report saying WHICH site is needed, and "
            "the task will be rerun with that site named so its credential "
            "boards.")

    vision_cfg = CONFIG.get("BROWSER", "use_vision", fallback="auto").lower()
    if vision_cfg in ("", "auto"):
        import hoplita_llmcfg
        use_vision = hoplita_llmcfg.model_sees(CONFIG, base_url, model)
        _vsrc = "model_sees"
    else:
        use_vision = vision_cfg in ("true", "1", "yes", "on")
        _vsrc = "[BROWSER] use_vision"
    print(f"[BROWSE] vision={use_vision} decided by {_vsrc} "
          f"(model={model})")

    agent_kwargs = {
        "task": full_task,
        **({"initial_actions": _initial_actions} if _initial_actions else {}),
        "llm": llm,
        "browser_session": browser_session,
        "sensitive_data": sensitive_data or None,
        "use_vision": use_vision,
        "extend_system_message": _BROWSE_SYSTEM_HINTS
                                 + (("\n\n" + cred_hint.strip()) if cred_hint.strip() else ""),
        "max_actions_per_step": CONFIG.getint("BROWSER", "max_actions_per_step", fallback=3),
    }
    use_thinking_cfg = CONFIG.get("BROWSER", "use_thinking", fallback="").strip().lower()
    if use_thinking_cfg in ("false", "0", "no", "off"):
        agent_kwargs["use_thinking"] = False
    elif use_thinking_cfg in ("true", "1", "yes", "on"):
        agent_kwargs["use_thinking"] = True

    flash_cfg = CONFIG.get("BROWSER", "flash_mode", fallback="auto").strip().lower()
    if flash_cfg in ("false", "0", "no", "off"):
        flash = False
    else:
        flash = True
    agent_kwargs["flash_mode"] = flash
    if flash:
        agent_kwargs.setdefault("use_thinking", False)

    fb_alias = CONFIG.get("BROWSER", "fallback_model", fallback="").strip()
    if fb_alias and fb_alias.lower() not in {k.lower() for k in _model_pool()}:
        print(f"[BROWSE] fallback_model '{fb_alias}' is disabled in [MODELS]: "
              f"no fallback this run", file=sys.stderr)
        fb_alias = ""
    if fb_alias:
        fb_sec = model_section(CONFIG, fb_alias)
        if fb_sec:
            fb_url, fb_key, fb_model = _resolve_endpoint(fb_sec)
            try:
                agent_kwargs["fallback_llm"] = BUChatOpenAI(
                    model=fb_model, base_url=fb_url, api_key=fb_key or "dummy",
                    temperature=0.1)
                print(f"[BROWSE] fallback_llm = {fb_alias} ({fb_model})")
            except Exception as e:
                print(f"[BROWSE] ⚠ fallback_llm {fb_alias}: {e}", file=sys.stderr)
        else:
            print(f"[BROWSE] ⚠ fallback_model '{fb_alias}' has no [{fb_sec}] section",
                  file=sys.stderr)

    try:
        agent = Agent(**agent_kwargs)
    except TypeError:
        for k in ("extend_system_message", "max_actions_per_step", "use_thinking",
                  "flash_mode", "fallback_llm", "initial_actions"):
            agent_kwargs.pop(k, None)
        agent = Agent(**agent_kwargs)

    print(f"[BROWSE] model={model} vision={use_vision} flash={flash} "
          f"forced_schema={_browse_force_schema()} max_steps={max_steps} "
          f"→ {full_task[:80]}")
    print(f"[BROWSE] boards → task {len(full_task)} chars · conversation lines "
          f"{len(_tail_ctx)} · credentials boarded {len(sensitive_data) // 2} "
          f"(values masked) · names-only {len(_mention_only)} · "
          f"memory/lessons/rules/code project: none", file=sys.stderr)

    try:
        history = await agent.run(max_steps=max_steps)
    except Exception as e:
        import traceback; traceback.print_exc()
        try: await browser_session.close()
        except Exception: pass
        return {"error": f"Agent crashed: {e}", "success": False}

    try: await browser_session.close()
    except Exception: pass

    if not history:
        return {"result": "No history", "success": False, "steps_used": 0}

    is_success = None
    try: is_success = history.is_successful()
    except Exception: pass

    final = ""
    try: final = history.final_result() or ""
    except Exception: pass

    if not final:
        try:
            for h in reversed(history.history):
                r = h.result
                items = r if isinstance(r, list) else ([r] if r else [])
                for item in items:
                    ec = getattr(item, "extracted_content", None)
                    if ec:
                        final = str(ec)
                        break
                if final:
                    break
        except Exception as e:
            print(f"[BROWSE] history scan: {e}")

    n_steps = len(history.history) if history.history else 0
    emoji = "✅" if is_success else ("⚠" if final else "❌")
    print(f"[BROWSE] {emoji} success={is_success} steps={n_steps}/{max_steps}: {final[:120]}")

    out = {
        "result": final or "The browser run ended without a result",
        "success": bool(is_success),
        "steps_used": n_steps,
        "max_steps": max_steps,
    }
    if not is_success:
        errs = []
        try:
            for e in history.errors():
                e = " ".join(str(e or "").split())[:200]
                if e and e not in errs:
                    errs.append(e)
        except Exception:
            pass
        try:
            last_url = next((u for u in reversed(history.urls()) if u), "")
        except Exception:
            last_url = ""
        if errs:
            out["errors"] = errs[-4:]
        if last_url:
            out["last_url"] = last_url
    return out


def _cred_boards(site, task_low):
    _s = (site or "").lower().strip()
    if not _s or not task_low:
        return False
    if _s in task_low:
        return True
    _parts = _s.split(".")
    _core = _parts[-2] if len(_parts) >= 2 else _s
    return bool(_core and len(_core) > 3 and _core in task_low)


def tool_browse_web(task, url="", headless=None, max_steps=None, endpoint=None):
    if headless is None:
        headless = CONFIG.getboolean("BROWSER", "headless", fallback=False)
    if max_steps is None:
        max_steps = CONFIG.getint("BROWSER", "max_steps_default", fallback=30)
    try:
        return asyncio.run(_browse_async(task, url, headless, max_steps,
                                         endpoint=endpoint))
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"error": str(e), "success": False}



import fnmatch as _fnmatch

_CODE_SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'dist', 'build',
                   'target', '.mypy_cache', '.next', 'coverage'}
_CODE_SKIP_EXTS = {'.pyc', '.pyo', '.o', '.so', '.exe', '.dll', '.jpg', '.png', '.gif',
                   '.ico', '.woff', '.pdf', '.zip', '.tar', '.gz', '.lock'}
_CODE_EXT_LANG = {
    '.py': 'python', '.go': 'go', '.js': 'javascript', '.ts': 'typescript',
    '.jsx': 'react', '.tsx': 'react-ts', '.rs': 'rust', '.java': 'java',
    '.kt': 'kotlin', '.c': 'c', '.cpp': 'cpp', '.rb': 'ruby', '.php': 'php',
    '.sh': 'bash', '.html': 'html', '.css': 'css', '.sql': 'sql',
    '.yaml': 'yaml', '.yml': 'yaml', '.toml': 'toml', '.json': 'json',
    '.md': 'markdown', '.ini': 'ini', '.vue': 'vue', '.svelte': 'svelte',
}


def tool_code_projects():
    out, seen = [], set()
    sql = _get_sql()
    names = []
    try:
        if sql is not None:
            raw = sql.meta_get("code_projects") or "[]"
            names = [n for n in json.loads(raw) if isinstance(n, str)]
    except Exception:
        names = []
    try:
        if notes_mgr is not None:
            for cat in (notes_mgr.get_categories() or []):
                if sql is not None and sql.meta_get(f"code_path:{cat}"):
                    names.append(cat)
    except Exception:
        pass
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        path = ""
        try:
            path = (sql.meta_get(f"code_path:{name}") or "") if sql else ""
        except Exception:
            path = ""
        notes = 0
        try:
            notes = len(notes_mgr.get_notes(name) or []) if notes_mgr else 0
        except Exception:
            notes = 0
        peers = []
        try:
            peers = [p.get("handle") for p in (fed_tools.tool_fed_code_peers(
                project=name) or {}).get("peers", [])]
        except Exception:
            peers = []
        out.append({"name": name, "path": path,
                    "exists": bool(path) and os.path.isdir(path),
                    "notes": notes, "peers": peers})
    out.sort(key=lambda p: (not p["exists"], p["name"].lower()))
    return {"projects": out, "count": len(out)}


def tool_code_name(path="."):
    p = os.path.abspath(os.path.expanduser(path or "."))
    name = os.path.basename(p.rstrip("/\\")) or p
    cat, notes = _project_notes(p)
    return {"name": name, "path": p, "exists": bool(notes),
            "category": cat or name}


def tool_code_index(path=".", name=None):
    p = os.path.expanduser(path or ".")
    if not os.path.isdir(p):
        return {"error": f"Not a directory: {p}"}
    if (name or "").strip():
        try:
            sql = _get_sql()
            if sql is not None:
                sql.meta_set(f"code_name_for:{os.path.abspath(p)}",
                             str(name).strip()[:60])
        except Exception:
            pass
    _memory_born = False
    try:
        _memory_born = bool(_project_memory_ensure(p))
    except Exception:
        pass
    files = []
    lang_count = {}
    total_size = 0
    for root, dirs, fnames in os.walk(p):
        dirs[:] = [d for d in dirs if d not in _CODE_SKIP_DIRS and not d.startswith('.')]
        for fn in fnames:
            ext = os.path.splitext(fn)[1].lower()
            if ext in _CODE_SKIP_EXTS:
                continue
            fp = os.path.join(root, fn)
            try:
                size = os.path.getsize(fp)
            except OSError:
                continue
            lang = _CODE_EXT_LANG.get(ext, 'other')
            lang_count[lang] = lang_count.get(lang, 0) + 1
            total_size += size
            files.append({"path": os.path.relpath(fp, p), "size": size, "lang": lang})
            if len(files) > 500:
                break
        if len(files) > 500:
            break
    root_files = set(os.listdir(p))
    _MANIFESTS = {
        'go.mod': 'go', 'go.work': 'go',
        'Cargo.toml': 'rust',
        'package.json': 'node',
        'requirements.txt': 'python', 'setup.py': 'python',
        'pyproject.toml': 'python', 'Pipfile': 'python',
        'pom.xml': 'java-maven',
        'build.gradle': 'jvm-gradle', 'build.gradle.kts': 'jvm-gradle',
        'settings.gradle': 'jvm-gradle',
        'composer.json': 'php',
        'Gemfile': 'ruby',
        'CMakeLists.txt': 'cmake',
        'Makefile': 'make', 'makefile': 'make', 'GNUmakefile': 'make',
        'meson.build': 'meson', 'configure.ac': 'autotools',
        'mix.exs': 'elixir', 'build.sbt': 'scala',
        'Package.swift': 'swift', 'pubspec.yaml': 'dart',
        'stack.yaml': 'haskell', 'cpanfile': 'perl',
        'Dockerfile': 'docker',
        'docker-compose.yml': 'docker', 'compose.yaml': 'docker',
    }
    found_manifests = [f for f in _MANIFESTS if f in root_files]
    for f in root_files:
        if f.endswith(('.csproj', '.sln', '.fsproj')):
            found_manifests.append(f); _MANIFESTS[f] = 'dotnet'
        elif f.endswith('.cabal'):
            found_manifests.append(f); _MANIFESTS[f] = 'haskell'
    found_manifests.sort()
    project_type = sorted({_MANIFESTS[f] for f in found_manifests})
    files.sort(key=lambda x: x["size"], reverse=True)
    _docs = [f["path"] for f in files
             if os.path.basename(f["path"]).lower().startswith(
                 ("readme", "architecture", "design", "contributing"))
             or f["path"].lower().endswith(".md")][:5]
    out = {"project_dir": os.path.abspath(p),
           "project_type": project_type or ["unknown"],
           "total_files": len(files), "total_size": total_size,
           "languages": lang_count, "files": files[:200],
           "survey": ("To UNDERSTAND this project (asked what it is, what it "
                      "does, how it hangs together): 1) read the docs first"
                      + (f" — it has {', '.join(_docs)}" if _docs else
                         " (none found here)")
                      + "; 2) read the dependency/build manifest"
                      + (f" — it has {', '.join(found_manifests[:5])}"
                         if found_manifests else
                         " (none at the root: plain sources, the listing "
                         "above still says the languages)")
                      + ": what it is built ON says what it IS; 3) find "
                        "the entry point (main/init/index/config) and "
                        "code_outline it; 4) code_outline the biggest "
                        "sources above; 5) answer from what YOU read — "
                        "never ask the user which file, the listing above "
                        "is the whole choice.")}
    if _memory_born:
        out["memory_born"] = True
        out["memory_note"] = ("This project's memory folder was just created "
                              "in Notes (rules + project seeded) and its "
                              "same-named task project ensured.")
    if len(files) > 500:
        out["truncated"] = True
        out["note"] = ("Indexing stopped after 500 files: totals and language "
                       "counts cover only what was walked. Use code_search "
                       "for anything specific.")
    return out


_STRUCTURE_PATTERNS = {
    '.py': r'^\s*(class |def |async def )',
    '.go': r'^(func |type )',
    '.js': r'^\s*(function |class |const \w+ = |export )',
    '.ts': r'^\s*(function |class |interface |type |export )',
    '.rs': r'^\s*(fn |struct |enum |impl |trait |pub )',
    '.java': r'^\s*(public |private |protected |class )',
    '.kt': r'^\s*(fun |class |object |data class )',
    '.rb': r'^\s*(def |class |module )',
    '.php': r'^\s*(function |class |interface |trait )',
}


def _code_extract_structure(lines, path):
    pat = _STRUCTURE_PATTERNS.get(os.path.splitext(path)[1].lower())
    if not pat:
        return []
    return [{"line": i + 1, "text": line.strip()[:120]}
            for i, line in enumerate(lines) if re.match(pat, line)][:100]


_CODE_WHOLE_MAX = 24000


_CODE_SYM_CACHE = {}

_SYM_NAME = re.compile(
    r'^\s*(?:pub\s+|public\s+|private\s+|protected\s+|export\s+|async\s+)*'
    r'(?:class|def|func|function|type|struct|enum|impl|trait|interface|'
    r'module|object|data\s+class|fun|fn|const|var|let)\s+'
    r'([A-Za-z_][A-Za-z0-9_]*)')


def _code_symbol_index(root):
    root = os.path.abspath(os.path.expanduser(root or "."))
    idx, newest, nfiles, walked = {}, 0.0, 0, 0
    for dirpath, dirs, fnames in os.walk(root):
        dirs[:] = [d for d in dirs
                   if d not in _CODE_SKIP_DIRS and not d.startswith('.')]
        walked += len(fnames)
        for fn in fnames:
            ext = os.path.splitext(fn)[1].lower()
            pat = _STRUCTURE_PATTERNS.get(ext)
            if not pat or ext in _CODE_SKIP_EXTS:
                continue
            fp = os.path.join(dirpath, fn)
            try:
                st = os.stat(fp)
                if st.st_size > 4_000_000:
                    continue
                newest = max(newest, st.st_mtime)
                with open(fp, encoding="utf-8", errors="replace") as fh:
                    lines = fh.read().splitlines()
            except Exception:
                continue
            nfiles += 1
            rel = os.path.relpath(fp, root)
            for i, line in enumerate(lines):
                if not re.match(pat, line):
                    continue
                m = _SYM_NAME.match(line)
                if not m:
                    continue
                idx.setdefault(m.group(1), []).append(
                    (rel, i + 1, line.strip()[:160]))
    stamp = (newest, nfiles, walked)
    cached = _CODE_SYM_CACHE.get(root)
    if cached and cached[0] == stamp:
        return cached[1], nfiles, False
    _CODE_SYM_CACHE[root] = (stamp, idx)
    return idx, nfiles, True


def tool_code_symbols(query="", path="", limit=40):
    if not (path or "").strip():
        return {"error": "code_symbols needs 'path': the project directory, "
                         "or one file in it. In code mode that is the open "
                         "project's path (shown in the [CODE MODE] note)."}
    root = os.path.abspath(os.path.expanduser(path))
    only_file = None
    if os.path.isfile(root):
        only_file = root
        root = _git_root(root) or os.path.dirname(root)
    if not os.path.isdir(root):
        return {"error": f"Not a directory or file: {root}"}
    try:
        idx, nfiles, built = _code_symbol_index(root)
    except Exception as e:
        return {"error": str(e)}
    if only_file:
        _rel = os.path.relpath(only_file, root)
        idx = {n: [(rf, l, d) for (rf, l, d) in v if rf == _rel] for n, v in idx.items()}
        idx = {n: v for n, v in idx.items() if v}
        nfiles = 1
    total = sum(len(v) for v in idx.values())
    q = (query or "").strip()
    if not q:
        crowded = sorted(
            ((f, sum(1 for v in idx.values() for (rf, _l, _d) in v if rf == f))
             for f in {rf for v in idx.values() for (rf, _l, _d) in v}),
            key=lambda x: -x[1])[:12]
        return {"symbols": total, "files": nfiles, "cached": not built,
                "busiest_files": [{"file": f, "symbols": n} for f, n in crowded],
                "hint": "Call it again with a name to get file:line without "
                        "reading anything. Then code_read_range(path, line-2, "
                        "line+40) is the function itself."}
    ql = q.lower()
    exact = [(n, v) for n, v in idx.items() if n == q]
    ci = [(n, v) for n, v in idx.items() if n != q and n.lower() == ql]
    part = [(n, v) for n, v in idx.items()
            if n != q and n.lower() != ql and ql in n.lower()]
    out, seen = [], 0
    for name, places in exact + ci + sorted(part, key=lambda x: len(x[0])):
        for rel, line, decl in places:
            out.append({"symbol": name, "file": rel, "line": line,
                        "declaration": decl})
            seen += 1
            if seen >= int(limit):
                break
        if seen >= int(limit):
            break
    return {"query": q, "found": len(out), "total_symbols": total,
            "files": nfiles, "cached": not built, "matches": out,
            "hint": ("Read one with code_read_range(file, line-2, line+40) — "
                     "that is the declaration and its body, and it costs one "
                     "call instead of reading the file."
                     if out else
                     "No declaration by that name. It may be a local, an "
                     "attribute or defined in a language this index does not "
                     "parse; code_search finds it as text.")}


def tool_code_read_smart(path, search=None):
    max_chars = _CODE_WHOLE_MAX
    p = os.path.expanduser(path)
    if os.path.isdir(p):
        return {"error": f"'{p}' is a DIRECTORY and this reads one file — "
                         f"code_index lists it, code_search searches it."}
    if not os.path.isfile(p):
        return {"error": f"Not a file: {p}"}
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return {"error": str(e)}
    lines = content.split("\n")
    size = len(content)
    if size <= max_chars:
        out = {"content": content, "lines": len(lines), "size": size,
               "complete": True}
        if search and search.lower() not in content.lower():
            out["search_note"] = (f"'{search}' does NOT appear in this "
                                  f"file; the whole file follows because "
                                  f"it fits.")
        return out
    if search:
        sl = search.lower()
        matching = []
        for i, line in enumerate(lines):
            if sl in line.lower():
                s, e = max(0, i - 5), min(len(lines), i + 10)
                matching.append({"line": i + 1,
                                 "chunk": "\n".join(f"{j + 1}: {lines[j]}" for j in range(s, e))})
                if len(matching) >= 20:
                    break
        return {"matches": matching,
                "total_matches": sum(1 for l in lines if sl in l.lower()),
                "lines": len(lines), "size": size, "complete": False}
    structure = _code_extract_structure(lines, p)
    head = "\n".join(f"{i + 1}: {lines[i]}" for i in range(min(50, len(lines))))
    ts = max(0, len(lines) - 30)
    tail = "\n".join(f"{i + 1}: {lines[i]}" for i in range(ts, len(lines)))
    return {"structure": structure, "head": head, "tail": tail,
            "lines": len(lines), "size": size, "complete": False,
            "hint": f"File of {len(lines)} lines / {size} chars: too big to "
                    f"read whole (cap {_CODE_WHOLE_MAX}). Above are its "
                    f"structure, the first 50 lines and the last 30. To get "
                    f"the rest call code_read_smart(search='term') for the "
                    f"places a term appears, or code_read_range(start_line, "
                    f"end_line) for exact lines — the structure lists the "
                    f"line numbers. Never fill a gap from memory."}


def tool_code_read_range(path, start_line, end_line):
    p = os.path.expanduser(path)
    if os.path.isdir(p):
        return {"error": f"'{p}' is a DIRECTORY and this reads one file — "
                         f"code_index lists it, code_search searches it."}
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        s = max(0, start_line - 1)
        e = min(len(lines), end_line)
        return {"content": "".join(f"{i + 1}: {lines[i]}" for i in range(s, e)),
                "range": f"{s + 1}-{e}", "total_lines": len(lines)}
    except Exception as e:
        return {"error": str(e)}


def tool_code_search(path, query, file_pattern=None):
    if not (path or "").strip():
        return {"error": "code_search needs 'path': the directory to search. "
                         "In code mode that is the open project's path "
                         "(shown in the [CODE MODE] note).",
                "searched": None}
    p = os.path.expanduser(path)
    skip = _CODE_SKIP_DIRS
    skip_ext = _CODE_SKIP_EXTS
    results = []
    ql = query.lower()

    if os.path.isfile(p):
        candidates = [(os.path.basename(p), p)]
        rel_base = os.path.dirname(p) or "."
    else:
        candidates = None
        rel_base = p

    def _scan(fp, relname):
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f, 1):
                    if ql in line.lower():
                        results.append({"file": relname, "line": i,
                                        "text": line.strip()[:200]})
                        if len(results) >= 50:
                            return True
        except Exception:
            pass
        return False

    if candidates is not None:
        for fn, fp in candidates:
            if _scan(fp, fn):
                return {"results": results, "truncated": True,
                        "searched": os.path.abspath(p),
                        "mode": "literal substring"}
    else:
        for root, dirs, fnames in os.walk(p):
            dirs[:] = [d for d in dirs
                       if d not in skip and not d.startswith('.')]
            for fn in fnames:
                if os.path.splitext(fn)[1].lower() in skip_ext:
                    continue
                if file_pattern and not _fnmatch.fnmatch(fn, file_pattern):
                    continue
                fp = os.path.join(root, fn)
                if _scan(fp, os.path.relpath(fp, rel_base)):
                    return {"results": results, "truncated": True,
                            "searched": os.path.abspath(p),
                            "mode": "literal substring"}
    out = {"results": results, "truncated": False,
           "searched": os.path.abspath(p), "mode": "literal substring"}
    if not results and any(c in query for c in "\\^$|()[]{}+*?"):
        out["note"] = ("your query contains regex metacharacters, but this "
                       "search is LITERAL: they were matched as plain "
                       "characters. Write the text exactly as it appears in "
                       "the code, or use code_outline/code_symbols for "
                       "structural questions.")
    return out


_CODE_CHECKPOINTS = {}
_CODE_CHECKPOINT_MAX = 50


_GIT_VERBATIM = ["-c", "core.autocrlf=false", "-c", "core.safecrlf=false"]


def _git(args, cwd, env=None, raw=False):
    import hoplita_paths
    try:
        r = subprocess.run(["git"] + args, capture_output=True, text=True,
                           timeout=30, cwd=cwd,
                           env=dict(os.environ, **(env or {})),
                           **hoplita_paths.no_console())
        out = r.stdout or ""
        return r.returncode == 0, (out if raw else out.strip())
    except Exception:
        return False, ""


def _git_root(path):
    p = os.path.abspath(os.path.expanduser(path))
    d = p if os.path.isdir(p) else (os.path.dirname(p) or ".")
    while not os.path.isdir(d) and d != os.path.dirname(d):
        d = os.path.dirname(d)
    ok, out = _git(["rev-parse", "--show-toplevel"], d)
    if not (ok and out):
        return None
    a = d
    while True:
        try:
            if os.path.samefile(a, out):
                return a
        except OSError:
            pass
        if a == os.path.dirname(a):
            return out
        a = os.path.dirname(a)


def _git_checkpoint(root, label=""):
    if not root:
        return None
    ok, head = _git(["rev-parse", "HEAD"], root)
    if not ok or not head:
        return None
    tmp = None
    try:
        import tempfile
        fd, tmp = tempfile.mkstemp(prefix="hoplita-idx-")
        os.close(fd)
        os.unlink(tmp)
        env = {"GIT_INDEX_FILE": tmp}
        if not _git(_GIT_VERBATIM + ["read-tree", "HEAD"], root, env)[0]:
            return None
        if not _git(_GIT_VERBATIM + ["add", "-A"], root, env)[0]:
            return None
        ok, tree = _git(_GIT_VERBATIM + ["write-tree"], root, env)
        if not ok or not tree:
            return None
        ok, sha = _git(["commit-tree", tree, "-p", head,
                        "-m", f"hoplita checkpoint {label}".strip()], root, env)
        return sha if ok and sha else None
    except Exception:
        return None
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except Exception:
                pass


_CODE_DROPPED = {}
_CODE_EDITED = {}
_CODE_PATCH_FAILED = {}


def _checkpoint_push(root, sha, files, created):
    _CODE_PATCH_FAILED.pop(root, None)
    st = _CODE_CHECKPOINTS.setdefault(root, [])
    st.append({"sha": sha, "ts": datetime.now().isoformat(),
               "files": list(files), "created": list(created)})
    extra = len(st) - _CODE_CHECKPOINT_MAX
    if extra > 0:
        del st[:extra]
        _CODE_DROPPED[root] = _CODE_DROPPED.get(root, 0) + extra


def tool_code_write_file(path, content):
    p = os.path.expanduser(path)
    root = _git_root(p)
    existed = os.path.exists(p)
    cp = _git_checkpoint(root, os.path.basename(p))
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        return {"error": f"Cannot write: {e}"}
    if root:
        rel = os.path.relpath(p, root)
        _CODE_EDITED.setdefault(root, set()).add(rel)
        if cp:
            _checkpoint_push(root, cp, [rel], [] if existed else [rel])
    return {"ok": True, "path": p, "bytes": len(content.encode("utf-8")),
            "created": not existed, "undo": bool(cp),
            "checkpoint": (cp or "")[:8]}


def tool_code_undo(path=None, steps=1):
    root = _git_root(path or os.getcwd())
    if not root:
        return {"error": "Not inside a git repository: there is no checkpoint "
                         "to go back to. Run 'git init' in the project."}
    stack = _CODE_CHECKPOINTS.get(root) or []
    if _CODE_PATCH_FAILED.pop(root, None):
        return {"error": (
            "Your last code_apply_patch FAILED and changed nothing: there is "
            "nothing of it to undo. code_undo would revert the edit BEFORE it, "
            + (f"to {', '.join(stack[-1]['files'])} at {stack[-1]['ts'][11:19]}, "
               if stack else "")
            + "which did apply. To fix the failed patch, read the exact lines "
            "with code_read_range and apply it again. If you really mean to "
            "undo that earlier edit, call code_undo again.")}
    if not stack:
        return {"error": f"No checkpoints recorded for {root} in this session."}
    steps = max(1, min(int(steps or 1), len(stack)))
    entry = stack[-steps]
    created = {f for e in stack[-steps:] for f in e["created"]}
    to_restore = sorted({f for e in stack[-steps:] for f in e["files"]}
                        - created)
    restored, failed = [], []
    for rel in to_restore:
        if _git(_GIT_VERBATIM + ["restore", "--source", entry["sha"],
                                 "--worktree", "--", rel], root)[0]:
            restored.append(rel)
        else:
            failed.append(rel)
    if to_restore and not restored:
        return {"error": f"Could not restore from {entry['sha'][:8]}."}
    removed = []
    for rel in created:
        victim = os.path.join(root, rel)
        if os.path.isfile(victim):
            try:
                os.unlink(victim)
                removed.append(rel)
            except Exception:
                pass
    undone = stack[-steps:]
    del stack[-steps:]
    out = {"ok": True, "undone": len(undone), "checkpoint": entry["sha"][:8],
           "files_restored": restored, "files_removed": sorted(removed),
           "remaining_checkpoints": len(stack)}
    if failed:
        out["files_failed"] = failed
    return out


def tool_code_checkpoints(path=None):
    root = _git_root(path or os.getcwd())
    if not root:
        return {"error": "Not inside a git repository."}
    dropped = _CODE_DROPPED.get(root, 0)
    out = {"root": root, "kept_at_most": _CODE_CHECKPOINT_MAX,
           "checkpoints": [{"sha": e["sha"][:8], "ts": e["ts"],
                            "files": e["files"], "created": e["created"]}
                           for e in reversed(_CODE_CHECKPOINTS.get(root) or [])]}
    if dropped:
        out["dropped_older"] = dropped
        out["warning"] = (f"{dropped} older undo point(s) have aged out "
                          f"(only the last {_CODE_CHECKPOINT_MAX} are kept). "
                          f"Anything before them can no longer be undone here "
                          f"— git history is the way back.")
    return out


_GIT_OUT_CAP = 12000


def _git_or_error(path):
    root = _git_root(path or os.getcwd())
    if not root:
        return None, {"error": "Not a git repository. Run 'git init' in the "
                               "project if you want history and undo."}
    return root, None


def _cap(text, cap=_GIT_OUT_CAP):
    if len(text) <= cap:
        return text, False
    return text[:cap], True


def tool_git_status(path=None):
    root, err = _git_or_error(path)
    if err:
        return err
    _, branch = _git(["branch", "--show-current"], root)
    _, porcelain = _git(["status", "--porcelain"], root, raw=True)
    changed, untracked, staged = [], [], []
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        x, y, name = line[0], line[1], line[3:]
        if x == "?" and y == "?":
            untracked.append(name)
            continue
        if x != " ":
            staged.append(name)
        if y != " ":
            changed.append(name)
    _, ahead = _git(["rev-list", "--count", "@{u}..HEAD"], root)
    return {"root": root, "branch": branch or "(detached)",
            "staged": staged, "modified": changed, "untracked": untracked,
            "clean": not porcelain,
            "unpushed_commits": int(ahead) if ahead.isdigit() else None}


def tool_git_diff(path=None, files=None, staged=False, since_checkpoint=False,
                  stat_only=False, commit=""):
    root, err = _git_or_error(path)
    if err:
        return err
    args = ["diff"]
    if (commit or "").strip():
        sha = commit.strip()
        if not _git(["rev-parse", "--verify", "--quiet",
                     sha + "^{commit}"], root)[0]:
            return {"error": "unknown_commit",
                    "message": f"'{sha}' is not a commit in this repository."}
        args.append(sha)
    elif since_checkpoint:
        stack = _CODE_CHECKPOINTS.get(root) or []
        if not stack:
            return {"error": "no_checkpoints",
                    "message": "HOPLITA has not edited anything here yet."}
        if not _git(["cat-file", "-e", stack[0]["sha"] + "^{commit}"], root)[0]:
            _CODE_CHECKPOINTS.pop(root, None)
            return {"error": "checkpoint_lost",
                    "message": "The session's checkpoints no longer exist in "
                               "this repository (was it re-created?). Compare "
                               "without since_checkpoint."}
        now = _git_checkpoint(root, "now")
        if not now:
            return {"error": "Could not snapshot the current tree to compare."}
        _then = stack[0]["sha"] + "^"
        _ok, _names = _git(["diff", "--name-only", "-z", _then, "HEAD", "--"]
                           + (files or ["."]), root)
        committed = [n for n in (_names.split("\0") if _ok else []) if n]
        if committed:
            _st = ["--stat"] if stat_only else []
            ok1, out1 = _git(["diff"] + _st + [stack[0]["sha"], now, "--"]
                             + (files or ["."])
                             + [":(exclude,literal)" + n for n in committed], root)
            ok2, out2 = _git(["diff"] + _st + ["HEAD", now, "--"]
                             + [":(literal)" + n for n in committed], root)
            if not (ok1 and ok2):
                return {"error": "git diff failed"}
            out = "\n".join(x for x in (out1, out2) if x.strip())
            body, truncated = _cap(out)
            return {"root": root, "diff": body, "truncated": truncated,
                    "empty": not out.strip()}
        args += [stack[0]["sha"], now]
    elif staged:
        args.append("--cached")
    if stat_only:
        args.append("--stat")
    args.append("--")
    args += (files or ["."])
    ok, out = _git(args, root)
    if not ok:
        return {"error": "git diff failed"}
    body, truncated = _cap(out)
    return {"root": root, "diff": body, "truncated": truncated,
            "empty": not out.strip()}


def _split_hunks(diff_text):
    head, hunks, cur = [], [], None
    for line in (diff_text or "").splitlines(True):
        if line.startswith("@@"):
            cur = [line]
            hunks.append(cur)
        elif cur is None:
            head.append(line)
        else:
            cur.append(line)
    return head, hunks


def tool_code_reject_hunk(path, file, hunk, header="", patch=""):
    root, err = _git_or_error(path)
    if err:
        return err
    if (patch or "").strip():
        return _reverse_patch(root, file, patch)
    ok, out = _git(["diff", "--", file], root)
    if not ok:
        return {"error": "git diff failed"}
    head, hunks = _split_hunks(out)
    try:
        idx = int(hunk)
    except (TypeError, ValueError):
        return {"error": "hunk must be a number"}
    if not (0 <= idx < len(hunks)):
        return {"error": f"There is no hunk {idx} in {file} "
                         f"(it has {len(hunks)}). Refresh the diff."}
    shown = (header or "").strip()
    if shown and hunks[idx][0].strip() != shown:
        return {"error": "the diff moved since it was shown; refresh and retry",
                "expected": shown, "found": hunks[idx][0].strip()}
    return _reverse_patch(root, file, "".join(head + hunks[idx]), idx,
                          len(hunks) - 1)


def _reverse_patch(root, file, patch, idx=None, remaining=None):
    if not patch.lstrip().startswith("diff --git"):
        patch = (f"diff --git a/{file} b/{file}\n"
                 f"--- a/{file}\n+++ b/{file}\n") + patch.lstrip("\n")
    cp = _git_checkpoint(root, f"reject {file}")
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix="hoplita-hunk-", suffix=".patch")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(patch if patch.endswith("\n") else patch + "\n")
        ok, msg = _git(["apply", "--reverse", "--recount", tmp], root)
        if not ok:
            return {"error": f"could not reverse that hunk: {msg[:200]}",
                    "hint": "the file changed since the diff was shown; refresh"}
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass
    if cp:
        _checkpoint_push(root, cp, [file], [])
    out = {"ok": True, "file": file, "undo": bool(cp),
           "checkpoint": (cp or "")[:8]}
    if idx is not None:
        out["hunk"] = idx
    if remaining is not None:
        out["remaining_hunks"] = remaining
    return out


def tool_git_log(path=None, limit=15, files=None):
    root, err = _git_or_error(path)
    if err:
        return err
    args = ["log", f"-{max(1, min(int(limit or 15), 100))}",
            "--pretty=format:%h\x1f%an\x1f%ar\x1f%s"]
    if files:
        args += ["--"] + files
    ok, out = _git(args, root)
    if not ok:
        return {"root": root, "commits": []}
    commits = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            commits.append({"sha": parts[0], "author": parts[1],
                            "when": parts[2], "subject": parts[3]})
    return {"root": root, "commits": commits}


def tool_git_show(path=None, sha=""):
    root, err = _git_or_error(path)
    if err:
        return err
    sha = (sha or "").strip()
    if not sha:
        return {"error": "git_show needs a commit sha (see git_log)."}
    if not _git(["rev-parse", "--verify", "--quiet",
                 sha + "^{commit}"], root)[0]:
        return {"error": "unknown_commit",
                "message": f"'{sha}' is not a commit in this repository."}
    ok, meta = _git(["show", "--no-patch",
                     "--format=%h\x1f%an\x1f%ar\x1f%s", sha], root)
    parts = meta.split("\x1f") if ok else []
    ok, patch = _git(["show", "--format=", "--patch", sha], root)
    if not ok:
        return {"error": "git show failed"}
    body, truncated = _cap(patch)
    out = {"root": root, "diff": body, "truncated": truncated}
    if len(parts) == 4:
        out.update({"sha": parts[0], "author": parts[1],
                    "when": parts[2], "subject": parts[3]})
    return out


def tool_git_branch(path=None, name=None, base=None):
    root, err = _git_or_error(path)
    if err:
        return err
    if not name:
        _, out = _git(["branch", "--format=%(refname:short)"], root)
        _, cur = _git(["branch", "--show-current"], root)
        return {"root": root, "current": cur,
                "branches": [b for b in out.splitlines() if b]}
    ok, out = _git(["switch", "-c", name] + ([base] if base else []), root)
    if not ok:
        ok, out = _git(["checkout", "-b", name] + ([base] if base else []), root)
    if not ok:
        return {"error": f"Could not create branch '{name}'."}
    return {"ok": True, "root": root, "branch": name}


def tool_git_switch(path=None, name=""):
    root, err = _git_or_error(path)
    if err:
        return err
    name = (name or "").strip()
    if not name:
        return {"error": "git_switch needs the branch name."}
    ok, cur = _git(["branch", "--show-current"], root)
    if cur == name:
        return {"ok": True, "root": root, "branch": name, "already": True}
    ok, out = _git(["switch", name], root)
    if not ok:
        ok, out = _git(["checkout", name], root)
    if not ok:
        _, dirty = _git(["status", "--porcelain"], root, raw=True)
        return {"error": f"Could not switch to '{name}': "
                         f"{(out or '').strip()[:300]}",
                "dirty": [l[3:] for l in (dirty or "").splitlines()
                          if l.strip() and not l.startswith("??")][:20],
                "hint": "commit your edits on the current branch (git_commit) "
                        "or undo them (code_undo) and switch again."}
    return {"ok": True, "root": root, "branch": name, "from": cur}


def tool_git_merge(path=None, branch="", into="main", delete_branch=False):
    root, err = _git_or_error(path)
    if err:
        return err
    branch = (branch or "").strip()
    into = (into or "main").strip()
    if not branch:
        return {"error": "git_merge needs the work branch to merge."}
    if branch == into:
        return {"error": f"'{branch}' is the target itself; nothing to merge."}
    _, dirty = _git(["status", "--porcelain"], root, raw=True)
    dirty_files = [l[3:] for l in (dirty or "").splitlines()
                   if l.strip() and not l.startswith("??")]
    if dirty_files:
        return {"error": "the working tree has uncommitted changes; commit "
                         "them (git_commit) or undo them (code_undo) before "
                         "merging.", "dirty": dirty_files[:20]}
    for b_ in (branch, into):
        ok, _ = _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{b_}"], root)
        if not ok:
            _, names = _git(["branch", "--format=%(refname:short)"], root)
            return {"error": f"no branch '{b_}' here.",
                    "branches": (names or "").splitlines()[:30]}
    _, cur = _git(["branch", "--show-current"], root)
    if cur != into:
        ok, out = _git(["switch", into], root)
        if not ok:
            ok, out = _git(["checkout", into], root)
        if not ok:
            return {"error": f"could not switch to '{into}': {(out or '').strip()[:300]}"}
    _, before = _git(["rev-parse", "HEAD"], root)
    ok, out = _git(["merge", "--no-edit", branch], root)
    if not ok:
        _, conflicted = _git(["diff", "--name-only", "--diff-filter=U"], root)
        _git(["merge", "--abort"], root)
        if cur and cur != into:
            _git(["switch", cur], root) or _git(["checkout", cur], root)
        return {"error": f"merging '{branch}' into '{into}' conflicts; the "
                         f"merge was aborted and the tree is as it was.",
                "conflicts": (conflicted or "").splitlines()[:30],
                "hint": "resolve on the work branch (merge the trunk into it, "
                        "fix, commit) and merge again."}
    _, after = _git(["rev-parse", "HEAD"], root)
    _, log = _git(["log", "--oneline", f"{before}..{after}"], root)
    res = {"ok": True, "root": root, "into": into, "branch": branch,
           "fast_forward": "Fast-forward" in (out or ""),
           "commits": (log or "").splitlines()[:20],
           "unchanged": before == after}
    if delete_branch and before != after:
        okd, _ = _git(["branch", "-d", branch], root)
        res["deleted_branch"] = bool(okd)
    return res


def tool_git_commit(path=None, message="", files=None, allow_main=False):
    root, err = _git_or_error(path)
    if err:
        return err
    if not (message or "").strip():
        return {"error": "A commit needs a message."}
    _, branch = _git(["branch", "--show-current"], root)
    if branch in ("main", "master") and not allow_main:
        return {"error": f"Refusing to commit on '{branch}'. The road: "
                         f"git_branch(name) → work → git_commit on that branch "
                         f"→ git_merge(branch, into='{branch}') brings it home. "
                         f"allow_main=true only if the user asked for a commit "
                         f"on '{branch}' explicitly."}
    if files is not None and not (isinstance(files, list)
                                  and all(isinstance(f, str) for f in files)):
        return {"error": "'files' is a list of paths, e.g. [\"calc.py\"] — "
                         f"got {type(files).__name__}: {json.dumps(files, ensure_ascii=False)[:120]}"}
    touched = sorted({f for e in (_CODE_CHECKPOINTS.get(root) or [])
                      for f in e["files"]} | _CODE_EDITED.get(root, set()))
    targets = files or touched
    if not targets:
        return {"error": "HOPLITA has not edited anything here this session. "
                         "If the USER edited by hand and wants it committed, "
                         "look at git_status and pass 'files' with exactly "
                         "what they want in — their word is the selection."}
    if not _git(["add", "--"] + targets, root)[0]:
        return {"error": "git add failed"}
    _, staged = _git(["diff", "--cached", "--name-only"], root)
    if not staged.strip():
        return {"error": "Nothing staged: there is nothing to commit."}
    ok, out = _git(["commit", "-m", message], root)
    if not ok:
        return {"error": f"git commit failed: {out[:300]}"}
    _, sha = _git(["rev-parse", "--short", "HEAD"], root)
    try:
        fed_tools.code_autopush(root)
    except Exception:
        pass
    return {"ok": True, "root": root, "branch": branch, "sha": sha,
            "files": staged.splitlines(),
            "scope": "explicit" if files else "edits made this session"}


def _is_project_folder(cat):
    try:
        _sql = _get_sql()
        if _sql is not None and _sql.meta_get(f"code_path:{cat}"):
            return True
    except Exception:
        pass
    try:
        rows = (notes_mgr.data.get("categories", {}) or {}).get(cat, []) or []
        titles = {(n_.get("title", "") or "").strip().lower()
                  for n_ in rows if not n_.get("deleted")}
    except Exception:
        try:
            titles = {(n_.get("title", "") or "").strip().lower()
                      for n_ in (notes_mgr.get_notes(cat) or [])}
        except Exception:
            return False
    return bool(titles & {"rules", "journal"})


def _note_window_chars():
    try:
        cap = int(CONFIG.getint("AGENTIC", "tool_result_max_chars", fallback=8000)) if CONFIG else 8000
    except Exception:
        cap = 8000
    return max(1000, cap - 600)


def _note_window(n_, category, start_line=None, end_line=None):
    body = n_.get("content", "") or ""
    lines = body.split("\n")
    total = len(lines)
    budget = _note_window_chars()
    out = {"category": category, "title": n_.get("title", ""),
           "id": n_.get("id"), "updated_at": n_.get("updated_at"),
           "total_lines": total, "total_chars": len(body)}
    s = 1
    if start_line is not None:
        try:
            s = max(1, int(start_line))
        except Exception:
            s = 1
    e = total
    if end_line is not None:
        try:
            e = min(total, max(s, int(end_line)))
        except Exception:
            e = total
    chosen = lines[s - 1:e]
    kept, size = [], 0
    for ln in chosen:
        if kept and size + len(ln) + 1 > budget and len(ln) <= budget:
            break
        kept.append(ln)
        size += len(ln) + 1
    last = s + len(kept) - 1
    out["content"] = "\n".join(kept)
    out["range"] = f"{s}-{last}"
    if last < e:
        out["cut"] = True
    if last < total:
        _nxt_end = min(total, last + max(1, len(kept)))
        _who = (f"note_id='{n_.get('id')}'" if n_.get("id") else
                f"title='{(n_.get('title', '') or '')}'")
        out["next"] = (f"read_note(category='{category}', {_who}, "
                       f"start_line={last + 1}, end_line={_nxt_end})")
        out["hint"] = (f"Lines {s}-{last} of {total}. The rest EXISTS and is "
                       f"not here: read it with the call in 'next' (and so on "
                       f"until the last line). Never fill it in from memory.")
    return out



PROJECT_NOTES = ("rules", "spec", "journal", "future")
_SPEC_ENTRY_CAP = 600
_SPEC_CAP = 12000
_FUTURE_CAP = 8000
_OPEN_TASKS_CAP = 7


def _project_title_refusal(category, title, system=False):
    if system or not category or not _is_project_folder(category):
        return None
    t_ = (title or "").strip().lower()
    if t_ in PROJECT_NOTES or t_.startswith("skill:"):
        return None
    return {"error": "project_notes",
            "message": f"'{title}' is not one of this project's notes. A project "
                       f"folder holds exactly {', '.join(PROJECT_NOTES)} (plus your "
                       f"'skill: …' notes). A settled truth → project_write(note="
                       f"'spec'); something pending → project_write(note='future'); "
                       f"what happened → project_journal. Nothing else is created."}


def _project_note(cat, title):
    for n_ in notes_mgr.get_notes(cat) or []:
        if (n_.get("title", "") or "").strip().lower() == title:
            return n_
    return None


def _fold_legacy_notes(cat):
    notes = list(notes_mgr.get_notes(cat) or [])
    by = {(n_.get("title", "") or "").strip(): n_ for n_ in notes}
    low = {k.lower(): v for k, v in by.items()}
    n_spec = n_change = 0
    if "spec" not in low and "project" in low:
        card = (low["project"].get("content") or "")
        tool_add_note(cat, "spec", (_tr("code.memory_tpl_spec") if _project_note_is_virgin(card)
                                    else card.strip() + "\n"), _system=True)
        notes_mgr.delete_note(cat, low["project"]["id"])
    pending = {"spec": [], "future": []}
    for k, n_ in sorted(by.items()):
        kl = k.lower()
        if kl.startswith("spec:"):
            pending["spec"].append((k.split(":", 1)[1].strip(), n_)); n_spec += 1
        elif kl.startswith("change:"):
            pending["future"].append((k.split(":", 1)[1].strip(), n_)); n_change += 1
    for target, items in pending.items():
        if not items:
            continue
        cur = _project_note(cat, target)
        prev = (cur.get("content") or "").rstrip() if cur else ""
        marker = "## Folded from the old design"
        chunk = "" if marker in prev else f"\n\n{marker}\n"
        chunk += "".join(f"\n### {ttl}\n{(n_.get('content') or '').strip()}\n" for ttl, n_ in items)
        body = (prev + chunk) if cur else (_tr("code.memory_tpl_spec" if target == "spec"
                                               else "code.memory_tpl_future").rstrip() + chunk)
        if cur is None:
            tool_add_note(cat, target, body, _system=True)
        else:
            notes_mgr.update_note(cat, cur["id"], content=body)
        for _ttl, n_ in items:
            notes_mgr.delete_note(cat, n_["id"])
    return n_spec, n_change


def _board_fold(cat):
    if todo_mgr is None:
        return 0
    open_ = [tk for tk in (todo_mgr.get_tasks(cat) or [])
             if int(tk.get("progress") or 0) < 100]
    if len(open_) <= _OPEN_TASKS_CAP:
        return 0
    open_.sort(key=lambda tk: (int(tk.get("progress") or 0),
                               str(tk.get("created_at") or tk.get("ts") or "")),
               reverse=True)
    excess = open_[_OPEN_TASKS_CAP:]
    lines = []
    for tk in excess:
        title = (tk.get("title") or "").strip()
        desc = (str(tk.get("description") or "").strip().splitlines() or [""])[0]
        lines.append(f"- {title}" + (f" — {desc[:120]}" if desc and desc != title else "")
                     + " (was a task)")
    cur = _project_note(cat, "future")
    prev = (cur.get("content") or "").rstrip() if cur else _tr("code.memory_tpl_future").rstrip()
    body = prev + "\n\n## Moved from the board\n" + "\n".join(lines) + "\n"
    if cur is None:
        tool_add_note(cat, "future", body, _system=True)
    else:
        notes_mgr.update_note(cat, cur["id"], content=body)
    for tk in excess:
        todo_mgr.delete_task(tk["id"])
    return len(excess)


def tool_project_write(path, note, text, replace=False):
    cat = _project_resolve(path)
    if not cat:
        return {"error": f"'{path}' is neither an open project directory "
                         f"nor a known project folder."}
    note = (note or "").strip().lower()
    if note not in ("spec", "future", "journal"):
        return {"error": f"note must be one of spec, future, journal (got '{note}'). "
                         f"'rules' are the user's own."}
    text = (text or "").strip()
    if not text:
        return {"error": "project_write needs the text."}
    if note == "journal" and not replace:
        return tool_project_journal(path, text)
    if note == "spec" and not replace and len(text) > _SPEC_ENTRY_CAP:
        return {"error": f"a spec annotation is a line or a short paragraph "
                         f"(≤{_SPEC_ENTRY_CAP} chars; this one is {len(text)}). "
                         f"Say the truth in one breath; the story of how you got "
                         f"there goes to the journal."}
    cur = _project_note(cat, note)
    prev = (cur.get("content") or "").rstrip() if cur else ""
    if replace:
        body = text + "\n"
    else:
        bullet = text if text.startswith(("-", "*", "#")) else f"- {text}"
        body = (prev + "\n" if prev else "") + bullet + "\n"
    cap = _SPEC_CAP if note == "spec" else _FUTURE_CAP
    if replace and len(body) > cap:
        return {"error": f"a rewritten '{note}' must stay under {cap} chars "
                         f"(this one is {len(body)}): keep the truths, drop the "
                         f"story, and leave the folded legacy out or trim it."}
    if cur is None:
        tool_add_note(cat, note, body, _system=True)
    else:
        notes_mgr.update_note(cat, cur["id"], content=body)
        fed_tools.after_local_mutation("notes", "update_note", (cat,), {}, None)
    return {"ok": True, "category": cat, "note": note,
            "chars": len(body), "replaced": bool(replace)}


def tool_project_journal(path, entry, title=""):
    cat = _project_resolve(path)
    if not cat:
        return {"error": f"'{path}' is neither an open project directory "
                         f"nor a known project folder."}
    entry = (entry or "").strip()
    if not entry:
        return {"error": "project_journal needs the entry text."}
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = entry.splitlines()
    title = (title or "").strip()
    if not title and len(lines) == 1 and len(entry) <= 100:
        block = f"## {stamp} — {entry}\n"
    else:
        block = f"## {stamp} — {title or lines[0][:60]}\n{entry}\n"
    prev = ""
    journal = None
    try:
        for n_ in notes_mgr.get_notes(cat) or []:
            if (n_.get("title", "") or "").strip().lower() == "journal":
                journal = n_
                prev = n_.get("content") or ""
                break
    except Exception:
        pass
    text = (prev.rstrip() + "\n\n" if prev.strip() else "") + block
    if journal is not None:
        r = tool_update_note(cat, journal["id"], content=text)
        if not r.get("updated"):
            return {"error": "could not update the journal note"}
    else:
        tool_add_note(cat, "journal", text)
    return {"ok": True, "category": cat,
            "entries": len(re.findall(r"^## ", text, re.M))}


    kept, size = [], 0
    for ln in chosen:
        if kept and size + len(ln) + 1 > budget and len(ln) <= budget:
            break
        kept.append(ln)
        size += len(ln) + 1
    last = s + len(kept) - 1
    out["content"] = "\n".join(kept)
    out["range"] = f"{s}-{last}"
    if last < e:
        out["cut"] = True
    if last < total:
        _nxt_end = min(total, last + max(1, len(kept)))
        _who = (f"note_id='{n_.get('id')}'" if n_.get("id") else
                f"title='{(n_.get('title', '') or '')}'")
        out["next"] = (f"read_note(category='{category}', {_who}, "
                       f"start_line={last + 1}, end_line={_nxt_end})")
        out["hint"] = (f"Lines {s}-{last} of {total}. The rest EXISTS and is "
                       f"not here: read it with the call in 'next' (and so on "
                       f"until the last line). Never fill it in from memory.")
    return out


def tool_read_note(category, title="", note_id="", start_line=None, end_line=None):
    blocked = _outside_child_scope(category)
    if blocked:
        return blocked
    try:
        notes = list(notes_mgr.get_notes(category) or [])
    except Exception:
        notes = []
    if not notes:
        try:
            cats = list(notes_mgr.get_categories() or [])
        except Exception:
            cats = []
        return {"error": f"no notes in '{category}'",
                "categories": cats[:30]}

    def _payload(n_):
        return _note_window(n_, category, start_line, end_line)

    if note_id:
        for n_ in notes:
            if n_.get("id") == note_id:
                return _payload(n_)
        return {"error": f"no note with id '{note_id}' in '{category}'",
                "titles": [n_.get("title", "") for n_ in notes][:30]}
    tl = (title or "").strip().lower()
    if not tl:
        return {"error": "read_note needs a title or a note_id",
                "titles": [n_.get("title", "") for n_ in notes][:30]}
    exact = [n_ for n_ in notes
             if (n_.get("title", "") or "").strip().lower() == tl]
    if exact:
        return _payload(exact[0])
    subs = [n_ for n_ in notes
            if tl in (n_.get("title", "") or "").lower()]
    if len(subs) == 1:
        return _payload(subs[0])
    if subs:
        return {"error": f"'{title}' matches several notes — pick one",
                "titles": [n_.get("title", "") for n_ in subs][:30]}
    return {"error": f"no note titled '{title}' in '{category}'",
            "titles": [n_.get("title", "") for n_ in notes][:30]}


def tool_code_apply_patch(path, changes):
    out = _code_apply_patch(path, changes)
    if isinstance(out, dict) and out.get("error"):
        root = _git_root(os.path.expanduser(path))
        if root:
            _CODE_PATCH_FAILED[root] = True
    return out


def _code_apply_patch(path, changes):
    p = os.path.expanduser(path)
    root = _git_root(p)
    if not os.path.exists(p) and len(changes) == 1 and not changes[0].get("old"):
        try:
            cp = _git_checkpoint(root, os.path.basename(p))
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(changes[0].get("new", ""))
            rel = os.path.relpath(p, root) if root else p
            if root:
                _CODE_EDITED.setdefault(root, set()).add(rel)
            if cp:
                _checkpoint_push(root, cp, [rel], [rel])
            return {"applied": 1, "path": p, "created": True,
                    "checkpoint": (cp or "")[:8], "undo": bool(cp)}
        except Exception as e:
            return {"error": f"Cannot create: {e}"}
    try:
        with open(p, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return {"error": f"Cannot read: {e}"}
    for i, ch in enumerate(changes):
        old = ch.get("old", "")
        if not old:
            return {"error": f"Change {i}: empty 'old'"}
        c = content.count(old)
        if c == 0:
            return {"error": f"Change {i}: not found: '{old[:60]}'. 'old' must "
                             "match the file byte for byte (indentation, quotes "
                             "and blank lines included). Read the exact lines "
                             "with code_read_range first and copy them. Nothing "
                             "was applied."}
        if c > 1:
            return {"error": f"Change {i}: ambiguous ({c} matches): 'old' "
                             f"appears {c} times in the file. Add the lines "
                             "before or after it so it matches exactly once. "
                             "Nothing was applied."}
    original = content
    for ch in changes:
        content = content.replace(ch["old"], ch.get("new", ""), 1)
    cp = _git_checkpoint(root, os.path.basename(p))
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        with open(p, "w", encoding="utf-8") as f:
            f.write(original)
        return {"error": f"Write failed, rolled back: {e}"}
    if root:
        _CODE_EDITED.setdefault(root, set()).add(os.path.relpath(p, root))
    if cp:
        _checkpoint_push(root, cp, [os.path.relpath(p, root)], [])
    return {"applied": len(changes), "path": p,
            "checkpoint": (cp or "")[:8], "undo": bool(cp)}


def _run_tool(cmd, parse_fn=None, label="", cwd="."):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                           cwd=cwd, **__import__("hoplita_paths").no_console())
        if parse_fn:
            return parse_fn(r)
        if r.returncode == 0:
            return []
        return [{"level": "error", "tool": label,
                 "message": (r.stderr + r.stdout).strip()[:2000]}]
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return [{"level": "warn", "tool": label, "message": "timeout"}]
    except Exception:
        return None


_TOOLCHAIN = {
    ".py": {"lang": "Python",
            "lint": [("ruff", "ruff"), ("pyright", "pyright")],
            "lsp": [("pylsp", "'python-lsp-server[all]'")],
            "how": "{py} -m pip install {pkgs}",
            "builtin": "py_compile (ships with Python)"},
    ".go": {"lang": "Go",
            "lint": [("staticcheck", "honnef.co/go/tools/cmd/staticcheck@latest")],
            "lsp": [("gopls", "golang.org/x/tools/gopls@latest")],
            "how": "go install {pkgs}",
            "builtin": "go vet (ships with Go)"},
    ".ts": {"lang": "TypeScript", "lint": [("eslint", "eslint")],
            "lsp": [("typescript-language-server", "typescript-language-server typescript")],
            "how": "npm install -g {pkgs}", "builtin": ""},
    ".js": {"lang": "JavaScript", "lint": [("eslint", "eslint")],
            "lsp": [("typescript-language-server", "typescript-language-server typescript")],
            "how": "npm install -g {pkgs}", "builtin": "node --check"},
    ".php": {"lang": "PHP", "lint": [("php", "php-cli")],
             "lsp": [("intelephense", "intelephense")],
             "how": "npm install -g {pkgs}", "builtin": ""},
    ".rs": {"lang": "Rust", "lint": [("clippy-driver", "clippy")],
            "lsp": [("rust-analyzer", "rust-analyzer")],
            "how": "rustup component add {pkgs}", "builtin": ""},
    ".c": {"lang": "C", "lint": [("gcc", "gcc")], "lsp": [("clangd", "clangd")],
           "how": "your package manager: {pkgs}",
           "builtin": "gcc -fsyntax-only"},
    ".h": {"lang": "C/C++ headers", "lint": [("gcc", "gcc")],
           "lsp": [("clangd", "clangd")],
           "how": "your package manager: {pkgs}",
           "builtin": "gcc -fsyntax-only"},
    ".cpp": {"lang": "C++", "lint": [("g++", "gcc-c++")], "lsp": [("clangd", "clangd")],
             "how": "your package manager: {pkgs}",
             "builtin": "g++ -fsyntax-only"},
    ".cc": {"lang": "C++", "lint": [("g++", "gcc-c++")], "lsp": [("clangd", "clangd")],
            "how": "your package manager: {pkgs}",
            "builtin": "g++ -fsyntax-only"},
    ".hpp": {"lang": "C++ headers", "lint": [("g++", "gcc-c++")],
             "lsp": [("clangd", "clangd")],
             "how": "your package manager: {pkgs}",
             "builtin": "g++ -fsyntax-only"},
    ".java": {"lang": "Java", "lint": [("javac", "java-devel")],
              "lsp": [("jdtls", "eclipse.jdt.ls")],
              "how": "your package manager: {pkgs}", "builtin": ""},
    ".rb": {"lang": "Ruby", "lint": [("rubocop", "rubocop")],
            "lsp": [("solargraph", "solargraph")],
            "how": "gem install {pkgs}", "builtin": ""},
    ".sh": {"lang": "Shell", "lint": [("shellcheck", "ShellCheck")], "lsp": [],
            "how": "your package manager: {pkgs}", "builtin": "bash -n"},
}
_SOURCEY = {".py", ".go", ".ts", ".tsx", ".js", ".jsx", ".php", ".rs", ".c",
            ".h", ".cpp", ".cc", ".cxx", ".hpp", ".java", ".rb", ".sh", ".kt",
            ".swift", ".cs", ".scala", ".ex", ".erl", ".lua", ".pl", ".m",
            ".zig", ".dart", ".sql", ".vue", ".svelte"}


def tool_code_toolchain(path=None):
    root = _git_root(path or os.getcwd()) or os.path.abspath(
        os.path.expanduser(path or os.getcwd()))
    if not os.path.isdir(root):
        return {"error": f"Not a folder: {root}"}
    skip = {'.git', 'node_modules', '__pycache__', 'venv', '.venv', 'dist', 'build'}
    counts, unknown = {}, {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith('.')]
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in _TOOLCHAIN:
                if ext in _SOURCEY:
                    unknown[ext] = unknown.get(ext, 0) + 1
                continue
            c = counts.setdefault(ext, {"files": 0, "lines": 0})
            c["files"] += 1
            try:
                with open(os.path.join(base, fn), "rb") as fh:
                    c["lines"] += fh.read().count(b"\n")
            except Exception:
                pass
    unknown_note = ("" if not unknown else
                    " Source this does not know how to check: " +
                    ", ".join(f"{n}x {e}" for e, n in
                              sorted(unknown.items(), key=lambda kv: -kv[1])) +
                    " — no linter and no language server will run on those.")
    if not counts:
        return {"root": root, "languages": [], "ready": not unknown,
                "unknown": unknown,
                "message": ("No source in a language this knows about."
                            + unknown_note)}

    def have(binary):
        if shutil.which(binary):
            return True
        try:
            import importlib.util as _iu
            return _iu.find_spec(binary.replace("-", "_")) is not None
        except Exception:
            return False

    out, degraded = [], []
    for ext, c in sorted(counts.items(), key=lambda kv: -kv[1]["lines"]):
        spec = _TOOLCHAIN[ext]
        missing_lint = [pkg for binary, pkg in spec["lint"] if not have(binary)]
        missing_lsp = [pkg for binary, pkg in spec["lsp"] if not have(binary)]
        present = [binary for binary, _ in spec["lint"] + spec["lsp"]
                   if have(binary)]
        entry = {"language": spec["lang"], "ext": ext, "files": c["files"],
                 "lines": c["lines"], "have": present,
                 "missing": missing_lint,
                 "missing_lsp": missing_lsp,
                 "lsp_note": ("A language server is not used yet: there is no "
                              "LSP client, so code_references is a TEXT search "
                              "whatever you install." if missing_lsp else ""),
                 "builtin_fallback": spec["builtin"]}
        if missing_lint:
            entry["install"] = spec["how"].format(
                pkgs=" ".join(missing_lint), py=sys.executable)
            degraded.append(spec["lang"])
        out.append(entry)
    return {"root": root, "languages": out,
            "ready": not degraded and not unknown,
            "degraded": degraded, "unknown": unknown, "unknown_note": unknown_note,
            "message": (("Every linter this project can use is installed."
                         if not degraded else
                         "code_lint is running on the language's built-ins for "
                         + ", ".join(degraded) + ". The commands above install "
                         "real linters and take effect immediately. Nothing is "
                         "installed automatically — they are for the user to "
                         "run or approve.") +
                        " Separately, and whatever you install: "
                        "code_references is a TEXT search, not a semantic one. "
                        "There is no LSP client yet, so it can miss (an "
                        "aliased import, a dynamic call) and over-match (the "
                        "same name in another scope). Do not treat it as "
                        "authoritative before a rename."
                        ) + unknown_note}


def tool_code_propose_verify(path=None):
    root = _git_root(path or os.getcwd()) or os.path.abspath(
        os.path.expanduser(path or os.getcwd()))
    if not os.path.isdir(root):
        return {"error": f"Not a folder: {root}"}
    marks, skip = [], {'.git', 'node_modules', '__pycache__', 'venv', '.venv'}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith('.')]
        for fn in files:
            if fn in ("Makefile", "makefile", "CMakeLists.txt", "go.mod",
                      "pyproject.toml", "setup.py", "tox.ini", "pytest.ini",
                      "package.json", "Cargo.toml", "meson.build", "justfile"):
                marks.append(os.path.relpath(os.path.join(base, fn), root))
        if len(marks) > 40:
            break
    tc = tool_code_toolchain(root)
    langs = [L["language"] for L in tc.get("languages", [])]
    tests, runners = [], []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith('.')]
        in_testdir = os.path.basename(base) in ("test", "tests", "spec", "t")
        for fn in files:
            rel = os.path.relpath(os.path.join(base, fn), root)
            if fn in ("run_all.sh", "runtests.sh", "run_tests.sh", "check.sh",
                      "Makefile", "makefile", "justfile", "tox.ini"):
                runners.append(rel)
            elif (fn.startswith(("test", "t_")) or in_testdir
                  or fn.endswith(("_test.go", "_test.py", "_spec.rb",
                                  ".test.ts", ".test.js"))):
                tests.append(rel)
        if len(tests) > 40:
            break
    return {
        "root": root, "languages": langs,
        "build_files": marks[:40], "test_files": sorted(tests)[:25],
        "existing_runners": sorted(set(runners))[:10],
        "instruction":
            "Propose ONE shell command that exits 0 while the project still "
            "works, to be used as the turn's definition of done. Rules: it must "
            "be a REGRESSION gate, not a quality judge — 'did you break it', "
            "not 'is it better'. Prefer what the project already has (a "
            "Makefile target, its test runner) over inventing one. It must be "
            "fast enough to run on every turn, and must not need the network, "
            "sudo or a running server. If the project has nothing that can "
            "answer the question, say so plainly and propose NOTHING: an empty "
            "definition of done is honest, a command that always passes is a "
            "green light nobody checked. Answer with the command alone, or "
            "with the single word NONE.",
        "note": "This is a SUGGESTION. Only the user puts it in the field.",
    }


def tool_code_lint(path):
    p = os.path.expanduser(path)
    ext = os.path.splitext(p)[1].lower()
    cwd = os.path.dirname(p) or "."
    diagnostics = []
    ran = []

    def _ran_tool(cmd, parse_fn=None, label="", cwd="."):
        out = _run_tool(cmd, parse_fn, label, cwd)
        if out is not None:
            ran.append(label or (cmd[0] if cmd else "?"))
        return out

    if ext == '.py':
        def _parse_pyright(r):
            diags = []
            try:
                data = json.loads(r.stdout)
                for d in data.get("generalDiagnostics", []):
                    diags.append({
                        "level": d.get("severity", "error"),
                        "line": d.get("range", {}).get("start", {}).get("line", 0),
                        "message": d.get("message", ""),
                        "rule": d.get("rule", ""),
                        "tool": "pyright"})
            except Exception:
                for line in (r.stdout + r.stderr).split("\n"):
                    m = re.match(r'.*:(\d+):\d+\s*-\s*(\w+):\s*(.*)', line)
                    if m:
                        diags.append({"level": m.group(2), "line": int(m.group(1)),
                                      "message": m.group(3), "tool": "pyright"})
            return diags

        result = _ran_tool(['pyright', '--outputjson', p], _parse_pyright, "pyright", cwd)
        if result is None:
            result = _ran_tool(['basedpyright', '--outputjson', p], _parse_pyright, "basedpyright", cwd)
        if result is not None:
            diagnostics.extend(result)

        def _parse_ruff(r):
            diags = []
            try:
                for d in json.loads(r.stdout):
                    diags.append({
                        "level": "warning",
                        "line": d.get("location", {}).get("row", 0),
                        "message": d.get("message", ""),
                        "rule": d.get("code", ""),
                        "tool": "ruff",
                        "fix": d.get("fix", {}).get("message", "")})
            except Exception:
                pass
            return diags

        ruff = _ran_tool(['ruff', 'check', '--output-format', 'json', p], _parse_ruff, "ruff", cwd)
        if ruff is not None:
            diagnostics.extend(ruff)

        if not diagnostics:
            r = _ran_tool([sys.executable, '-m', 'py_compile', p],
                          label="py_compile", cwd=cwd)
            if r is not None:
                diagnostics.extend(r)

    elif ext in ('.c', '.h', '.cpp', '.cc', '.cxx', '.hpp'):
        cc = 'g++' if ext in ('.cpp', '.cc', '.cxx', '.hpp') else 'gcc'

        def _parse_cc(r):
            diags = []
            for ln in (r.stderr or "").splitlines():
                m = re.match(r'^.*?:(\d+):(\d+):\s*(error|warning):\s*(.*)$', ln)
                if m:
                    diags.append({"level": m.group(3), "line": int(m.group(1)),
                                  "col": int(m.group(2)), "message": m.group(4),
                                  "tool": cc})
            return diags

        r = _ran_tool([cc, '-fsyntax-only', '-Wall', '-Wextra', p],
                      _parse_cc, cc, cwd)
        if r is not None:
            diagnostics.extend(r)

    elif ext == '.go':
        for cmd, label in ((['go', 'vet', './...'], "go vet"),
                           (['staticcheck', './...'], "staticcheck")):
            r = _ran_tool(cmd, label=label, cwd=cwd)
            if r is not None:
                diagnostics.extend(r)

    elif ext in ('.js', '.jsx'):
        for cmd, label in ((['node', '--check', p], "node"),
                           (['npx', 'eslint', '--format', 'json', p], "eslint")):
            r = _ran_tool(cmd, label=label, cwd=cwd)
            if r is not None:
                diagnostics.extend(r)

    elif ext in ('.ts', '.tsx'):
        r = _ran_tool(['npx', 'tsc', '--noEmit', '--pretty', 'false', p], label="tsc", cwd=cwd)
        if r is not None:
            diagnostics.extend(r)

    elif ext == '.rs':
        r = _ran_tool(['cargo', 'check', '--message-format', 'json'], label="cargo", cwd=cwd)
        if r is not None:
            diagnostics.extend(r)

    elif ext == '.json':
        r = _ran_tool([sys.executable, '-c',
                       'import json,sys;json.load(open(sys.argv[1]))', p],
                      label="json", cwd=cwd)
        if r is not None:
            diagnostics.extend(r)

    elif ext in ('.yaml', '.yml'):
        r = _ran_tool([sys.executable, '-c',
                       'import yaml,sys;yaml.safe_load(open(sys.argv[1]))', p],
                      label="yaml", cwd=cwd)
        if r is not None:
            diagnostics.extend(r)

    else:
        return {"info": f"No diagnostics for {ext}"}

    if not ran:
        return {"ok": None, "error": "no_linter",
                "message": f"No linter available for {ext} on this machine, so "
                           f"NOTHING was checked. This is not a clean result.",
                "hint": {".py": "pip install ruff pyright",
                         ".go": "go vet ships with Go; staticcheck: "
                                "go install honnef.co/go/tools/cmd/staticcheck@latest"
                         }.get(ext, ""),
                "path": p, "tools_used": []}
    ok = not any(d.get("level") in ("error", "Error") for d in diagnostics)
    return {"ok": ok, "diagnostics": diagnostics[:50], "path": p,
            "tools_used": sorted(set(ran))}


_REF_FILE_PATTERNS = {
    '.py': '*.py', '.go': '*.go', '.js': '*.js', '.ts': '*.ts',
    '.jsx': '*.jsx', '.tsx': '*.tsx', '.rs': '*.rs', '.java': '*.java',
    '.rb': '*.rb', '.php': '*.php', '.c': '*.[ch]', '.cpp': '*.[ch]pp',
}


def _lsp_positions(text, symbol):
    out, decl = [], []
    pat = re.compile(rf"\b{re.escape(symbol)}\b")
    for ln, line in enumerate(text.splitlines()):
        for m in pat.finditer(line):
            spot = (ln, m.start())
            head = line[:m.start()].strip()
            if head.endswith(("def", "class", "func", "type", "struct",
                              "fn", "const", "var", "let")):
                decl.append(spot)
            else:
                out.append(spot)
    return decl + out


_OUTLINE_PAT = {
    ".py": r"^\s*(?:async\s+)?(def|class)\s+([A-Za-z_]\w*)",
    ".go": r"^\s*(func|type)\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)",
    ".c": r"^\s*[A-Za-z_][\w \*]*?\b(\w+)\s*\([^;]*\)\s*\{",
    ".h": r"^\s*[A-Za-z_][\w \*]*?\b(\w+)\s*\([^;]*\)\s*[;{]",
    ".js": r"^\s*(?:export\s+)?(function|class)\s+([A-Za-z_$][\w$]*)",
    ".ts": r"^\s*(?:export\s+)?(function|class|interface|type)\s+([A-Za-z_$][\w$]*)",
    ".rs": r"^\s*(?:pub\s+)?(fn|struct|enum|trait|impl)\s+([A-Za-z_]\w*)",
    ".sh": r"^\s*(?:function\s+)?([A-Za-z_]\w*)\s*\(\)",
}
_OUTLINE_PAT[".cpp"] = _OUTLINE_PAT[".cc"] = _OUTLINE_PAT[".c"]
_OUTLINE_PAT[".hpp"] = _OUTLINE_PAT[".h"]
_OUTLINE_PAT[".jsx"] = _OUTLINE_PAT[".js"]
_OUTLINE_PAT[".tsx"] = _OUTLINE_PAT[".ts"]


_OUTLINE_CACHE = OrderedDict()


def _outline_store(key, result):
    _OUTLINE_CACHE[key] = dict(result)
    _OUTLINE_CACHE.move_to_end(key)
    while len(_OUTLINE_CACHE) > 64:
        _OUTLINE_CACHE.popitem(last=False)
    return result


def tool_code_outline(path, content=None):
    p = os.path.expanduser(path)
    if os.path.isdir(p):
        return {"error": f"'{p}' is a DIRECTORY and this outlines one "
                         f"file — code_index lists it, code_symbols "
                         f"maps its declarations."}
    if content is None:
        try:
            content = open(p, encoding="utf-8", errors="replace").read()
        except Exception as e:
            return {"error": str(e)}
    _ck = (p, hashlib.sha1(content.encode("utf-8",
                                          "replace")).hexdigest())
    _hit = _OUTLINE_CACHE.get(_ck)
    if _hit is not None and _hit.get("source") == "lsp":
        _OUTLINE_CACHE.move_to_end(_ck)
        return dict(_hit)
    try:
        import hoplita_lsp as _l
        lang = _l.language_of(p)
        root = _git_root(p) or os.path.dirname(p) or "."
        srv, why = _l.POOL.get(root, lang, start_timeout=25) if lang \
            else (None, "unsupported file type")
    except Exception as e:
        srv, why = None, str(e)
    if srv is not None:
        try:
            srv.open_file(p, content)
            syms = srv.document_symbols(p)
            if syms:
                for x in syms:
                    x["kind"] = _l.SYMBOL_KIND.get(x["kind"], str(x["kind"]))
                return _outline_store(_ck,
                                      {"source": "lsp", "server": srv.cmd[0],
                                       "symbols": syms, "total": len(syms)})
        except Exception:
            pass
    ext = os.path.splitext(p)[1].lower()
    pat = _OUTLINE_PAT.get(ext)
    if not pat:
        return {"source": "none", "symbols": [], "why": why,
                "note": f"no outline for {ext} without a language server"}
    rx = re.compile(pat)
    syms = []
    for i, line in enumerate(content.splitlines()):
        m = rx.match(line)
        if m:
            g = m.groups()
            kind, name = (g[0], g[1]) if len(g) > 1 else ("function", g[0])
            syms.append({"name": name, "kind": kind, "depth": 0,
                         "line": i + 1, "detail": ""})
    return {"source": "text scan", "symbols": syms, "total": len(syms),
            "why": why,
            "caveat": "A regex scan: no nesting, and it cannot see anything "
                      "declared at runtime. Install a language server for "
                      "the real outline."}


_DIAG_LINE = re.compile(
    r"^(?P<file>[^:\n]+):(?P<line>\d+)(?::(?P<col>\d+))?:\s*"
    r"(?:(?P<sev>error|warning|note|fatal error)[:,]?\s*)?(?P<msg>.+)$")


def _parse_tool_lines(text, source, only_file=None):
    out = []
    for ln in (text or "").splitlines():
        m = _DIAG_LINE.match(ln.strip())
        if not m:
            continue
        f = m.group("file")
        if only_file and os.path.basename(f) != only_file \
                and f != "<standard input>":
            continue
        line = int(m.group("line"))
        col = int(m.group("col") or 1)
        sev = (m.group("sev") or "error").split()[0]
        out.append({"line": line, "col": col, "end_line": line, "end_col": col,
                    "severity": "warning" if sev in ("warning", "note")
                                else "error",
                    "message": m.group("msg").strip(), "source": source})
    return out


def _diagnostics_fallback(path, lang, content, why):
    if content is None:
        try:
            content = open(path, encoding="utf-8",
                           errors="replace").read()
        except Exception as e:
            return {"available": False, "why": str(e), "diagnostics": []}
    try:
        disk = open(path, encoding="utf-8", errors="replace").read()
    except Exception:
        disk = None
    same = (disk is not None and content == disk)
    cwd = os.path.dirname(path) or "."
    base = os.path.basename(path)
    diags, used = [], []

    def _run(cmd, source, stdin=None, only_file=None):
        try:
            r = subprocess.run(cmd, input=stdin, capture_output=True,
                               text=True, timeout=30, cwd=cwd,
                               **__import__("hoplita_paths").no_console())
        except FileNotFoundError:
            return False
        except Exception:
            return False
        used.append(source)
        diags.extend(_parse_tool_lines(r.stderr + "\n" + r.stdout, source,
                                       only_file=only_file))
        return True

    if lang == "go":
        _run(["gofmt", "-e"], "gofmt", stdin=content)
        if same:
            _run(["go", "vet", "./..."], "go vet", only_file=base)
    elif lang in ("c", "cpp"):
        cc = "g++" if lang == "cpp" else "gcc"
        if same:
            _run([cc, "-fsyntax-only", "-Wall", "-Wextra", path], cc,
                 only_file=base)
    if not used:
        return {"available": False, "why": why, "diagnostics": []}
    seen, unique = set(), []
    for d_ in diags:
        k = (d_["line"], d_["message"])
        if k not in seen:
            seen.add(k)
            unique.append(d_)
    diags = unique
    note = None if same else \
        "buffer differs from disk: only syntax checks ran"
    out = {"available": True, "server": "+".join(used), "fallback": True,
           "why": why, "diagnostics": diags[:200]}
    if note:
        out["note"] = note
    return out


def tool_code_diagnostics(path, content=None):
    try:
        import hoplita_lsp as _l
    except Exception as e:
        return {"error": f"LSP client unavailable: {e}"}
    p = os.path.expanduser(path)
    root = _git_root(p) or os.path.dirname(p) or "."
    lang = _l.language_of(p)
    srv, why = _l.POOL.get(root, lang, start_timeout=25) if lang \
        else (None, "unsupported file type")
    if srv is None:
        return _diagnostics_fallback(p, lang, content, why)
    if content is None:
        try:
            content = open(p, encoding="utf-8", errors="replace").read()
        except Exception as e:
            return {"error": str(e)}
    uri = _l.path_to_uri(p)
    before = srv.diagnostics.get(uri)
    srv.open_file(p, content)
    deadline = time.time() + 2.0
    while time.time() < deadline:
        got = srv.diagnostics.get(uri)
        if got is not None and got is not before:
            break
        time.sleep(0.05)
    got = srv.diagnostics.get(uri)
    if got is None:
        return {"available": True, "server": srv.cmd[0], "diagnostics": [],
                "note": "the server has not published diagnostics yet"}
    return {"available": True, "server": srv.cmd[0], "diagnostics": [
        {"line": d["range"]["start"]["line"] + 1,
         "col": d["range"]["start"]["character"] + 1,
         "end_line": d["range"]["end"]["line"] + 1,
         "end_col": d["range"]["end"]["character"] + 1,
         "severity": {1: "error", 2: "warning", 3: "info",
                      4: "hint"}.get(d.get("severity", 2), "warning"),
         "message": d.get("message", ""), "source": d.get("source", "")}
        for d in got]}


def tool_code_lsp_status(path=None):
    try:
        import hoplita_lsp as _l
    except Exception as e:
        return {"error": f"LSP client unavailable: {e}"}
    root = _git_root(path or os.getcwd()) or os.path.abspath(
        os.path.expanduser(path or os.getcwd()))
    langs = {}
    for ext, lang in _l.EXT_LANG.items():
        cmd = _l.server_command(lang)
        langs.setdefault(lang, cmd[0] if cmd else None)
    return {"root": root, "running": _l.POOL.running(),
            "available": {k: v for k, v in langs.items() if v},
            "missing": sorted(k for k, v in langs.items() if not v)}


def tool_code_definition(path, symbol, project_path=None):
    try:
        import hoplita_lsp as _l
    except Exception as e:
        return {"error": f"LSP client unavailable: {e}"}
    p = os.path.expanduser(path)
    root = os.path.expanduser(project_path or "") or _git_root(p) or \
        os.path.dirname(p) or "."
    lang = _l.language_of(p)
    srv, why = _l.POOL.get(root, lang, start_timeout=25)
    if srv is None:
        return {"error": why, "hint": "code_references does a text search "
                                      "without a language server."}
    try:
        text = open(p, encoding="utf-8", errors="replace").read()
    except Exception as e:
        return {"error": str(e)}
    srv.open_file(p, text)
    for ln, col in _lsp_positions(text, symbol):
        res = srv.definition(p, ln, col)
        if res:
            items = res if isinstance(res, list) else [res]
            return {"symbol": symbol, "source": "lsp", "server": srv.cmd[0],
                    "definitions": [
                        {"file": os.path.relpath(_l.uri_to_path(r["uri"]), root),
                         "line": r["range"]["start"]["line"] + 1}
                        for r in items if r.get("uri")]}
    return {"symbol": symbol, "source": "lsp", "definitions": [],
            "note": "the server found no definition for that name here"}


def tool_code_references(path, symbol, project_path=None):
    p = os.path.expanduser(path)
    proj = os.path.expanduser(project_path or "") or _git_root(p) or \
        os.path.dirname(p) or "."
    try:
        import hoplita_lsp as _l
        lang = _l.language_of(p)
        srv, why = (_l.POOL.get(proj, lang, start_timeout=25) if lang
                    else (None, "unsupported file type"))
    except Exception as e:
        srv, why = None, f"LSP client unavailable: {e}"
    if srv is not None:
        try:
            text = open(p, encoding="utf-8", errors="replace").read()
            srv.open_file(p, text)
            for ln, col in _lsp_positions(text, symbol):
                res = srv.references(p, ln, col)
                if res:
                    return {"symbol": symbol, "source": "lsp",
                            "server": srv.cmd[0], "authoritative": True,
                            "references": [
                                {"file": os.path.relpath(
                                    _l.uri_to_path(r["uri"]), proj),
                                 "line": r["range"]["start"]["line"] + 1}
                                for r in res if r.get("uri")],
                            "total": len(res)}
        except Exception:
            pass
    ext = os.path.splitext(p)[1].lower()
    file_pat = _REF_FILE_PATTERNS.get(ext, '*')
    refs = tool_code_search(proj, symbol, file_pattern=file_pat).get("results", [])

    def_patterns = {
        '.py': rf'^\s*(def|class|async def)\s+{re.escape(symbol)}\b',
        '.go': rf'^(func|type|var|const)\s+.*{re.escape(symbol)}\b',
        '.js': rf'(function|class|const|let|var)\s+{re.escape(symbol)}\b',
        '.ts': rf'(function|class|const|let|var|interface|type)\s+{re.escape(symbol)}\b',
        '.rs': rf'(fn|struct|enum|trait|impl|pub fn)\s+{re.escape(symbol)}\b',
    }
    def_pat = def_patterns.get(ext)
    definitions, usages = [], []
    for r in refs:
        if def_pat and re.search(def_pat, r.get("text", "")):
            definitions.append(r)
        else:
            usages.append(r)
    return {"symbol": symbol, "source": "text search", "authoritative": False,
            "why_not_lsp": why,
            "caveat": "A text search MISSES aliased imports and dynamic calls, "
                      "and MATCHES the same name in unrelated scopes. Read the "
                      "hits before acting on the count.",
            "definitions": definitions, "usages": usages,
            "total": len(refs),
            "files_affected": sorted({r.get("file", "") for r in refs})}


def tool_code_deps(path):
    p = os.path.expanduser(path)
    if os.path.isdir(p):
        return {"error": f"'{p}' is a DIRECTORY and this reads one file — "
                         f"code_index lists it, code_search searches it."}
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return {"error": str(e)}
    ext = os.path.splitext(p)[1].lower()
    imports, exports = [], []
    if ext == '.py':
        for m in re.finditer(r'^(?:from\s+(\S+)\s+)?import\s+(.+)', content, re.MULTILINE):
            imports.append(m.group(1) or m.group(2).split(',')[0].strip().split(' as ')[0])
        for m in re.finditer(r'^(?:def|class|async def)\s+(\w+)', content, re.MULTILINE):
            exports.append(m.group(1))
    elif ext == '.go':
        for block in re.finditer(r'^import\s*\(([^)]*)\)', content,
                                 re.MULTILINE | re.DOTALL):
            for m in re.finditer(r'"([^"]+)"', block.group(1)):
                imports.append(m.group(1))
        for m in re.finditer(r'^import\s+(?:\w+\s+)?"([^"]+)"', content,
                             re.MULTILINE):
            imports.append(m.group(1))
        for m in re.finditer(r'^func\s+(\w+)', content, re.MULTILINE):
            if m.group(1)[0].isupper():
                exports.append(m.group(1))
    elif ext in ('.js', '.ts', '.jsx', '.tsx'):
        for m in re.finditer(r'(?:import|require)\s*\(?["\']([^"\']+)["\']', content):
            imports.append(m.group(1))
        for m in re.finditer(r'export\s+(?:default\s+)?(?:function|class|const|let|var)\s+(\w+)', content):
            exports.append(m.group(1))
    elif ext == '.rs':
        for m in re.finditer(r'use\s+([\w:]+)', content):
            imports.append(m.group(1))
        for m in re.finditer(r'pub\s+(?:fn|struct|enum|trait)\s+(\w+)', content):
            exports.append(m.group(1))
    return {"file": p, "imports": imports[:50], "exports": exports[:50]}



def tool_call_api(url, method="GET", headers=None, body=None, auth_type=None,
                  auth_token=None, auth_key_name=None, auth_key_value=None,
                  query_params=None, timeout=30, save_response=None,
                  auth_from=None):
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        h.update(headers)

    if auth_from:
        _k = cred_mgr.get_api_key(auth_from) if cred_mgr else None
        if not _k:
            _c = cred_mgr.get_credential_safe(auth_from) if cred_mgr else None
            return {"error": (f"'{auth_from}' is stored as kind "
                              f"'{_c.get('kind')}', not 'apikey'"
                              if _c else
                              f"no API key stored for '{auth_from}'. Save it in "
                              f"the credential manager as kind 'apikey', or "
                              f"pass auth_key_value directly."),
                    "auth_from": auth_from}
        auth_key_value = _k
        auth_token = _k
        auth_type = auth_type or "api_key_query"

    if auth_type == "bearer" and auth_token:
        h["Authorization"] = f"Bearer {auth_token}"
    elif auth_type == "basic" and auth_token:
        h["Authorization"] = f"Basic {base64.b64encode(auth_token.encode()).decode()}"
    elif auth_type == "api_key_header" and auth_key_name and auth_key_value:
        h[auth_key_name] = auth_key_value
    elif auth_type == "api_key_query" and auth_key_name and auth_key_value:
        query_params = query_params or {}
        query_params[auth_key_name] = auth_key_value

    if query_params:
        from urllib.parse import urlencode, urlparse, parse_qs, urlunparse
        parsed = urlparse(url)
        existing = parse_qs(parsed.query)
        existing.update({k: [v] for k, v in query_params.items()})
        new_query = urlencode({k: (v[0] if isinstance(v, list) else v) for k, v in existing.items()})
        url = urlunparse(parsed._replace(query=new_query))

    data = None
    json_body = None
    if body:
        if isinstance(body, dict):
            json_body = body
        elif isinstance(body, str):
            try:
                json_body = json.loads(body)
            except json.JSONDecodeError:
                data = body
                if "json" in h.get("Content-Type", ""):
                    h["Content-Type"] = "text/plain"

    try:
        r = requests.request(method.upper(), url, headers=h, json=json_body,
                             data=data, timeout=timeout, stream=bool(save_response))

        if save_response:
            ct = r.headers.get("content-type", "").lower()
            is_binary = any(ct.startswith(t) for t in
                            ("image/", "audio/", "video/", "application/octet",
                             "application/pdf", "application/zip"))
            if is_binary or r.headers.get("content-length", "0") != "0":
                out_path = os.path.expanduser(save_response)
                with open(out_path, "wb") as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
                return {"status": r.status_code, "saved": out_path,
                        "size": os.path.getsize(out_path), "content_type": ct}

        ct = r.headers.get("content-type", "")
        if "json" in ct:
            try:
                resp_data = r.json()
                resp_str = json.dumps(resp_data, ensure_ascii=False)
                if len(resp_str) > 10_000:
                    return {"status": r.status_code, "data": resp_data,
                            "truncated": True, "note": "Response > 10KB"}
                return {"status": r.status_code, "data": resp_data}
            except ValueError:
                pass
        return {"status": r.status_code, "body": r.text[:5000], "content_type": ct}

    except requests.exceptions.Timeout:
        return {"error": f"Timeout ({timeout}s)", "url": url}
    except Exception as e:
        return {"error": str(e), "url": url}



_PDF_FONT_CANDIDATES = {
    "Linux": [
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
        ("/usr/share/fonts/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/dejavu/DejaVuSansMono.ttf"),
        ("/usr/share/fonts/TTF/DejaVuSans.ttf",
         "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/TTF/DejaVuSansMono.ttf"),
        ("/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf",
         "/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf",
         "/usr/share/fonts/liberation-mono/LiberationMono-Regular.ttf"),
        ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"),
        ("/usr/share/fonts/noto/NotoSans-Regular.ttf",
         "/usr/share/fonts/noto/NotoSans-Bold.ttf",
         "/usr/share/fonts/noto/NotoSansMono-Regular.ttf"),
    ],
    "Darwin": [
        ("/Library/Fonts/Arial Unicode.ttf",
         "/Library/Fonts/Arial Unicode.ttf",
         "/System/Library/Fonts/Menlo.ttc"),
        ("/System/Library/Fonts/Supplemental/Arial.ttf",
         "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
         "/System/Library/Fonts/Menlo.ttc"),
    ],
    "Windows": [
        ("C:\\Windows\\Fonts\\arial.ttf",
         "C:\\Windows\\Fonts\\arialbd.ttf",
         "C:\\Windows\\Fonts\\consola.ttf"),
        ("C:\\Windows\\Fonts\\tahoma.ttf",
         "C:\\Windows\\Fonts\\tahomabd.ttf",
         "C:\\Windows\\Fonts\\consola.ttf"),
        ("C:\\Windows\\Fonts\\calibri.ttf",
         "C:\\Windows\\Fonts\\calibrib.ttf",
         "C:\\Windows\\Fonts\\consola.ttf"),
    ],
}

_PDF_EMOJI_REPLACEMENTS = {
    "✅": "[OK]", "❌": "[X]", "✗": "[X]",
    "⚠️": "[!]", "🔔": "[!]", "🚨": "[!!]",
    "🔴": "[H]", "🟡": "[M]", "🟢": "[L]", "🔵": "[N]",
    "🟠": "[M]", "⚪": "[ ]", "🟣": "[*]",
    "📋": "[#]", "📝": "[N]", "📅": "[D]", "📂": "[F]",
    "🌐": "[W]", "🔐": "[S]", "⏰": "[T]", "🛡": "[*]", "🛡️": "[*]",
    "🎯": "[*]", "💡": "[i]", "📊": "[#]", "📈": "[^]", "📉": "[v]",
    "🚀": "[!]", "⭐": "[*]", "🔥": "[!]", "💪": "[!]", "👍": "[+]", "👎": "[-]",
    "🤖": "[AI]", "💻": "[PC]", "📱": "[m]", "🖥": "[PC]",
}

_PDF_LATIN1_REPLACEMENTS = {
    "•": "-", "·": "-", "–": "-", "—": "-", "…": "...",
    "“": '"', "”": '"', "‘": "'", "’": "'",
    "✓": "[OK]", "⚠": "[!]",
    "→": "->", "←": "<-", "↑": "^", "↓": "v",
}


def _find_pdf_font():
    for paths in _PDF_FONT_CANDIDATES.get(platform.system(), []):
        if all(os.path.exists(p) for p in paths):
            return paths
    return None


def _strip_unrenderable(text, allow_unicode):
    out = text
    for src, dst in _PDF_EMOJI_REPLACEMENTS.items():
        out = out.replace(src, dst)
    if not allow_unicode:
        for src, dst in _PDF_LATIN1_REPLACEMENTS.items():
            out = out.replace(src, dst)
        out = "".join(c if ord(c) < 256 else "?" for c in out)
    else:
        out = "".join(c if ord(c) < 0x2600 or 0x2600 <= ord(c) < 0x2700 else "?" for c in out)
    return out


_PDF_CSS = """
:root { color: #1a1f26; }
* { box-sizing: border-box; }
body { font: 10.5pt/1.5 -apple-system, 'Segoe UI', Roboto, 'Noto Sans',
       'DejaVu Sans', sans-serif; margin: 0; }
main { max-width: 100%; }
h1 { font-size: 19pt; line-height: 1.25; margin: 0 0 10pt;
     padding-bottom: 6pt; border-bottom: 2px solid #1a1f26; }
h2 { font-size: 14pt; margin: 16pt 0 6pt; padding-bottom: 3pt;
     border-bottom: 1px solid #cfd6dd; page-break-after: avoid; }
h3 { font-size: 11.5pt; margin: 12pt 0 4pt; page-break-after: avoid; }
p { margin: 5pt 0; }
ul, ol { margin: 5pt 0; padding-left: 18pt; }
li { margin: 2.5pt 0; }
strong { color: #10141a; }
a { color: #1652a0; text-decoration: none; word-break: break-all; }
blockquote { margin: 6pt 0; padding: 4pt 10pt; border-left: 3px solid
             #9aa5b1; color: #3d4650; background: #f4f6f8; }
code { font: 8.5pt/1.4 'DejaVu Sans Mono', Menlo, Consolas, monospace;
       background: #f0f2f5; border: 1px solid #dde2e8; border-radius: 3px;
       padding: 0 3px; }
pre { background: #f6f8fa; border: 1px solid #dde2e8; border-radius: 4px;
      padding: 8pt; overflow-x: hidden; white-space: pre-wrap;
      word-wrap: break-word; page-break-inside: avoid; }
pre code { background: none; border: none; padding: 0; }
hr { border: none; border-top: 1px solid #cfd6dd; margin: 12pt 0; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0;
        font-size: 9pt; page-break-inside: auto; }
th { background: #eef1f5; text-align: left; font-weight: 600; }
th, td { border: 1px solid #c7cfd8; padding: 4.5pt 6pt; vertical-align: top;
         word-wrap: break-word; overflow-wrap: anywhere; }
tbody tr:nth-child(even) { background: #f7f9fb; }
tr { page-break-inside: avoid; }
img { max-width: 100%; }
"""


def _generate_pdf_chromium(content, title, output_path):
    import html as _html
    body = content
    if not re.search(r"<\w+[^>]*>", content[:400]):
        import markdown as _md
        body = _md.markdown(content, extensions=["tables", "fenced_code",
                                                 "sane_lists"])
        if not content.lstrip().startswith("#") and title:
            body = f"<h1>{_html.escape(title)}</h1>\n" + body
    import hoplita_hands as _hands
    body = _hands.inline_local_images(body)
    page_html = (f"<!doctype html><html><head><meta charset='utf-8'>"
                 f"<title>{_html.escape(title or 'Document')}</title>"
                 f"<style>{_PDF_CSS}</style></head><body><main>{body}"
                 f"</main></body></html>")

    async def _run():
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            br = await pw.chromium.launch(headless=True)
            try:
                page = await br.new_page()
                await page.set_content(page_html, wait_until="load")
                await page.pdf(path=output_path, format="A4",
                               print_background=True,
                               margin={"top": "16mm", "bottom": "16mm",
                                       "left": "14mm", "right": "14mm"})
            finally:
                await br.close()
    try:
        asyncio.run(_run())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_run())
        finally:
            loop.close()
    pages = None
    try:
        r = subprocess.run(["pdfinfo", output_path], capture_output=True,
                           **__import__("hoplita_paths").no_console(),
                           text=True, timeout=10)
        m = re.search(r"Pages:\s+(\d+)", r.stdout)
        pages = int(m.group(1)) if m else None
    except Exception:
        pass
    return {"path": output_path, "pages": pages,
            "size": os.path.getsize(output_path), "engine": "chromium"}


def tool_generate_pdf(content, title="Document", output_path=None):
    if not output_path:
        safe = re.sub(r"[^\w\-]+", "_", title).strip("_") or "Document"
        output_path = os.path.join(os.path.expanduser("~"), f"{safe}.pdf")
    output_path = os.path.expanduser(output_path)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    try:
        return _generate_pdf_chromium(content, title, output_path)
    except Exception as e:
        print(f"[PDF] chromium engine failed ({e}) → fpdf2 fallback")
        return _generate_pdf_legacy(content, title, output_path)


def _generate_pdf_legacy(content, title="Document", output_path=None):
    try:
        from fpdf import FPDF
    except ImportError:
        return {"error": "fpdf2 no instalado. pip install fpdf2"}

    if not output_path:
        safe = re.sub(r"[^\w\-]+", "_", title).strip("_") or "Document"
        output_path = os.path.join(os.path.expanduser("~"), f"{safe}.pdf")
    output_path = os.path.expanduser(output_path)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    fonts = _find_pdf_font()
    unicode_ok = False
    if fonts:
        try:
            regular, bold, mono = fonts
            pdf.add_font("Body", "", regular)
            pdf.add_font("Body", "B", bold)
            pdf.add_font("Mono", "", mono)
            body_font, mono_font = "Body", "Mono"
            unicode_ok = True
        except Exception as e:
            print(f"[PDF] add_font failed ({e}); fallback to Helvetica with sanitizing")

    if not unicode_ok:
        if not fonts:
            print("[PDF] No Unicode font found; using Helvetica (sanitizing content)")
        body_font, mono_font = "Helvetica", "Courier"

    content = _strip_unrenderable(content, allow_unicode=unicode_ok)
    title = _strip_unrenderable(title, allow_unicode=unicode_ok)
    bullet = "• " if unicode_ok else "- "

    pdf.set_font(body_font, "B", 18)
    pdf.multi_cell(0, 12, title, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font(body_font, "", 11)

    in_code = False
    for line in content.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
            if in_code:
                pdf.set_font(mono_font, "", 9)
                pdf.set_fill_color(240, 240, 240)
            else:
                pdf.set_font(body_font, "", 11)
            continue
        if in_code:
            pdf.multi_cell(0, 5, "  " + line, fill=True,
                           new_x="LMARGIN", new_y="NEXT")
            continue
        s = line.strip()
        if s.startswith("# "):
            pdf.ln(4); pdf.set_font(body_font, "B", 16)
            pdf.multi_cell(0, 10, s[2:], new_x="LMARGIN", new_y="NEXT")
            pdf.set_font(body_font, "", 11)
        elif s.startswith("## "):
            pdf.ln(3); pdf.set_font(body_font, "B", 14)
            pdf.multi_cell(0, 9, s[3:], new_x="LMARGIN", new_y="NEXT")
            pdf.set_font(body_font, "", 11)
        elif s.startswith("### "):
            pdf.ln(2); pdf.set_font(body_font, "B", 12)
            pdf.multi_cell(0, 8, s[4:], new_x="LMARGIN", new_y="NEXT")
            pdf.set_font(body_font, "", 11)
        elif s.startswith("- ") or s.startswith("* "):
            pdf.multi_cell(0, 6, "    " + bullet + s[2:],
                           new_x="LMARGIN", new_y="NEXT")
        elif s and s[0].isdigit() and len(s) > 1 and s[1] == ".":
            pdf.multi_cell(0, 6, "    " + s, new_x="LMARGIN", new_y="NEXT")
        elif s in ("---", "***"):
            pdf.ln(2)
            pdf.line(pdf.get_x(), pdf.get_y(), pdf.get_x() + 170, pdf.get_y())
            pdf.ln(4)
        elif not s:
            pdf.ln(4)
        else:
            pdf.multi_cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")

    try:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        pdf.output(output_path)
    except Exception as e:
        return {"error": f"Could not write PDF: {e}"}

    return {"path": output_path, "pages": pdf.page_no(),
            "size": os.path.getsize(output_path),
            "font": "unicode" if unicode_ok else "latin1-fallback"}


_FOLDER_DEFAULT_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.pdf', '.txt', '.md', '.csv',
                        '.docx', '.xlsx', '.pptx'}
_FOLDER_IMG_MIMES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                     '.gif': 'image/gif', '.webp': 'image/webp'}


def tool_read_folder(path, extensions=None):
    p = os.path.expanduser(path)
    if not os.path.isdir(p):
        return {"error": f"Not a directory: {p}"}
    allowed = ({e if e.startswith('.') else f'.{e}' for e in extensions}
               if extensions else _FOLDER_DEFAULT_EXTS)
    files = []
    for fn in sorted(os.listdir(p)):
        ext = os.path.splitext(fn)[1].lower()
        if ext not in allowed:
            continue
        fp = os.path.join(p, fn)
        if not os.path.isfile(fp):
            continue
        size = os.path.getsize(fp)
        if size > 20 * 1024 * 1024:
            files.append({"name": fn, "type": "skipped", "reason": "too large"})
            continue
        entry = {"name": fn, "size": size}
        if ext in _FOLDER_IMG_MIMES:
            entry["type"] = "image"
            entry["mime"] = _FOLDER_IMG_MIMES[ext]
            entry["path"] = fp
        elif ext == '.pdf':
            import hoplita_pdf
            _txt, _err = hoplita_pdf.plain_text(fp, 10_000)
            entry["content"] = _txt if not _err else f"(could not read the text: {_err})"
            entry["type"] = "pdf"
        elif ext in _hoplita_office().OFFICE_EXTS:
            entry["content"] = _hoplita_office().plain_text(fp, 10_000)
            entry["type"] = "office"
        else:
            try:
                with open(fp, 'r', encoding='utf-8', errors='replace') as f:
                    entry["content"] = f.read()[:10_000]
            except Exception:
                entry["content"] = "(error leyendo)"
            entry["type"] = "text"
        files.append(entry)
        if len(files) >= 50:
            break
    out = {"folder": p, "total": len(files), "files": files}
    if any(f.get("type") == "image" for f in files):
        out["images"] = ("Pictures are listed, not attached: look at the ones "
                         "you need with see_image(path).")
    return out



TOOLS: dict = {}
TOOL_PARAMS: dict = {}


def _chat_now_iso():
    import datetime as _d
    return _d.datetime.now().isoformat()


def _chat_sessions_rows():
    sql = _get_sql()
    return (sql.load("chat_sessions") if sql is not None else [])


def tool_chat_session_save(args):
    sql = _get_sql()
    if sql is None:
        return {"error": "SQL store unavailable"}
    sess = dict(args.get("session") or {})
    import uuid as _u
    sid = sess.get("uuid") or sess.get("id") or _u.uuid4().hex
    prev = next((r for r in _chat_sessions_rows() if r.get("uuid") == sid), None)
    sess["uuid"] = sid
    sess.pop("deleted", None)
    try:
        import hoplita_device as _hd
        sess["writer_device"] = _hd.device_id()
    except Exception:
        pass
    sess["rev"] = int((prev or {}).get("rev", 0)) + 1
    sess.setdefault("created_at", (prev or {}).get("created_at") or _chat_now_iso())
    sess["updated_at"] = _chat_now_iso()
    sql.upsert("chat_sessions", sid, sess)
    try:
        if getattr(sql, "fts", False):
            _ya = sql.messages_in(sid)
            for _m in (sess.get("history") or [])[_ya:]:
                _c = _m.get("content")
                if isinstance(_c, str) and _c.strip():
                    sql.log_message(sid, _m.get("role", ""), _c[:20000],
                                    ts=sess.get("updated_at", ""))
    except Exception as _e:
        print(f"[LOG] session save: {_e}", file=sys.stderr)
    return {"ok": True, "uuid": sid, "rev": sess["rev"]}


def tool_chat_session_list(args):
    out = []
    for r in _chat_sessions_rows():
        if r.get("deleted"):
            continue
        out.append({"uuid": r.get("uuid"), "title": r.get("title", ""),
                    "model": r.get("model", ""),
                    "created_at": r.get("created_at", ""),
                    "updated_at": r.get("updated_at", ""),
                    "pinned_device": r.get("pinned_device") or None,
                    "sync_id": r.get("sync_id") or "",
                    "n": len(r.get("history") or [])})
    out.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return {"sessions": out}


def tool_chat_session_load(args):
    sid = args.get("uuid")
    for r in _chat_sessions_rows():
        if r.get("uuid") == sid and not r.get("deleted"):
            out = dict(r)
            hist = out.get("history") or []
            _head = next((str(m.get("content", "")) for m in hist[:1]
                          if m.get("role") == "system"
                          and str(m.get("content", "")).lstrip()
                          .startswith(_SUMMARY_MARK)), "")
            if _head:
                _since, _n = _summary_head_parse(_head)
                out["context_since"] = _since
                out["compacted_n"] = _n
                out["fresh_n"] = max(0, len(hist) - 1)
            return {"session": out}
    return {"error": "session not found"}


def tool_chat_session_delete(args):
    sql = _get_sql()
    if sql is None:
        return {"error": "SQL store unavailable"}
    sid = args.get("uuid")
    _ids = [i for i in ([sid] + list(args.get("conv_ids") or [])) if i]
    prev = next((r for r in _chat_sessions_rows() if r.get("uuid") == sid), None)
    if prev is not None:
        prev = {"uuid": sid,
                "deleted": True,
                "rev": int((prev or {}).get("rev", 0)) + 1,
                "editor": (prev or {}).get("editor", ""),
                "local_only": bool((prev or {}).get("local_only", False)),
                "updated_at": _chat_now_iso()}
        sql.upsert("chat_sessions", sid, prev)
    _wm = wm_forget(_ids)
    _msgs = 0
    for _sid in _ids:
        try:
            _msgs += sql.forget_messages(session=_sid)
        except Exception as e:
            print(f"[SESSION] could not purge the log of {_sid}: {e}")
    return {"ok": True, "working_memory_dropped": _wm,
            "messages_purged": _msgs}



def _session_resolve(ref):
    ref = (ref or "").strip()
    if not ref:
        return None, "empty session reference"
    rows = [r for r in _chat_sessions_rows() if not r.get("deleted")]
    if not rows:
        return None, "no saved sessions"
    for r in rows:
        if r.get("uuid") == ref:
            return r, ""
    low = ref.lower()
    exact = [r for r in rows if (r.get("title") or "").strip().lower() == low]
    if len(exact) == 1:
        return exact[0], ""
    cands = exact or [r for r in rows if low in (r.get("title") or "").lower()]
    if not cands:
        return None, (f"no session named {ref!r}. Saved: "
                      + ", ".join(repr(r.get("title", "")) for r in rows[:12]))
    if len(cands) > 1:
        return None, (f"{ref!r} matches {len(cands)} sessions: "
                      + ", ".join(f"{r.get('title','')!r} ({r.get('uuid','')[:8]})"
                                  for r in cands[:8])
                      + ". Use the uuid.")
    return cands[0], ""


_SESSION_BRIEF_SYS = (
    "You are summarising a saved conversation so a scheduled task can inherit "
    "its context later. Reply ONLY with JSON:\n"
    '{"topic": "...", "situation": "...", "positions": ["..."], '
    '"watch_for": ["..."], "open_questions": ["..."]}\n'
    "situation: what was going on and WHY it mattered. positions: what the "
    "user concluded, decided or believed — their view, not yours. watch_for: "
    "what would change that view. Be concrete and short: names, figures, "
    "dates. Do NOT invent anything that is not in the conversation; leave a "
    "list empty rather than filling it."
)


def _session_brief_text(brief):
    if not isinstance(brief, dict):
        return str(brief or "")
    out = []
    if brief.get("topic"):
        out.append(f"Topic: {brief['topic']}")
    if brief.get("situation"):
        out.append(f"Situation: {brief['situation']}")
    for key, label in (("positions", "What the user concluded"),
                       ("watch_for", "What would change that"),
                       ("open_questions", "Left open")):
        vals = [v for v in (brief.get(key) or []) if str(v).strip()]
        if vals:
            out.append(label + ":\n" + "\n".join(f"  - {v}" for v in vals))
    return "\n".join(out)


def _session_extractive_brief(history, keep=12):
    turns = [m for m in (history or [])
             if m.get("role") in ("user", "assistant") and m.get("content")]
    if not turns:
        return ""
    first_user = next((m for m in turns if m["role"] == "user"), None)
    tail = turns[-keep:]
    chunk = ([first_user] if first_user is not None and first_user not in tail
             else []) + tail
    lines = []
    for m in chunk:
        who = "User" if m["role"] == "user" else "HOPLITA"
        txt = re.sub(r"\s+", " ", str(m.get("content", "")))[:400]
        lines.append(f"{who}: {txt}")
    return "\n".join(lines)


def tool_session_brief(args):
    row, err = _session_resolve(args.get("session"))
    if err:
        return {"error": err}
    refresh = bool(args.get("refresh"))
    rev = int(row.get("rev", 0))
    cached = row.get("brief")
    if cached and not refresh and int(row.get("brief_rev", -1)) == rev:
        return {"uuid": row.get("uuid"), "title": row.get("title", ""),
                "dated": row.get("updated_at", ""), "source": row.get("brief_source", "?"),
                "brief": cached, "text": _session_brief_text(cached),
                "cached": True}
    history = row.get("history") or []
    if not history:
        return {"error": f"session {row.get('title','')!r} has no history"}
    convo = "\n".join(
        f"{'User' if m.get('role') == 'user' else 'HOPLITA'}: "
        + re.sub(r"\s+", " ", str(m.get("content", "")))[:1500]
        for m in history if m.get("role") in ("user", "assistant"))
    brief = _distill_json(_SESSION_BRIEF_SYS, convo[:24000])
    source = "llm"
    if not isinstance(brief, dict) or not any(brief.get(k) for k in
                                              ("topic", "situation", "positions")):
        brief = {"topic": row.get("title", ""),
                 "situation": _session_extractive_brief(history)}
        source = "excerpt"
    sql = _get_sql()
    if sql is not None:
        upd = dict(row)
        upd["brief"] = brief
        upd["brief_rev"] = rev
        upd["brief_source"] = source
        sql.upsert("chat_sessions", row.get("uuid"), upd)
    return {"uuid": row.get("uuid"), "title": row.get("title", ""),
            "dated": row.get("updated_at", ""), "source": source,
            "brief": brief, "text": _session_brief_text(brief),
            "cached": False, "turns": len(history)}


def tool_session_recall(args):
    row, err = _session_resolve(args.get("session"))
    if err:
        return {"error": err}
    q = (args.get("query") or "").strip().lower()
    k = max(1, min(int(args.get("k") or 5), 20))
    history = [m for m in (row.get("history") or [])
               if m.get("role") in ("user", "assistant")]
    if not q:
        hits = list(enumerate(history))[-k:]
    else:
        terms = [w for w in re.split(r"\W+", q) if len(w) > 2] or [q]
        scored = []
        for i, m in enumerate(history):
            txt = str(m.get("content", "")).lower()
            sc = sum(txt.count(w) for w in terms)
            if sc:
                scored.append((sc, i, m))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        hits = [(i, m) for _sc, i, m in scored[:k]]
        hits.sort()
    return {"title": row.get("title", ""), "dated": row.get("updated_at", ""),
            "scanned": len(history),
            "turns": [{"i": i, "role": m.get("role"),
                       "text": re.sub(r"\s+", " ", str(m.get("content", "")))[:1200]}
                      for i, m in hits]}


def _canvas_rows():
    sql = _get_sql()
    return (sql.load("canvases") if sql is not None else [])


def tool_canvas_save(args):
    sql = _get_sql()
    if sql is None:
        return {"error": "SQL store unavailable"}
    c = dict(args.get("canvas") or {})
    import uuid as _u
    cid = c.get("uuid") or c.get("id") or _u.uuid4().hex
    prev = next((r for r in _canvas_rows() if r.get("uuid") == cid), None)
    c["uuid"] = cid
    c.pop("deleted", None)
    c["rev"] = int((prev or {}).get("rev", 0)) + 1
    c.setdefault("created_at", (prev or {}).get("created_at") or _chat_now_iso())
    c["updated_at"] = _chat_now_iso()
    sql.upsert("canvases", cid, c)
    return {"ok": True, "uuid": cid, "rev": c["rev"]}


def tool_canvas_list(args):
    out = []
    for r in _canvas_rows():
        if r.get("deleted"):
            continue
        out.append({"uuid": r.get("uuid"), "title": r.get("title", ""),
                    "created_at": r.get("created_at", ""),
                    "updated_at": r.get("updated_at", ""),
                    "size": len(r.get("html") or "")})
    out.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return {"canvases": out}


def tool_canvas_load(args):
    cid = args.get("uuid")
    for r in _canvas_rows():
        if r.get("uuid") == cid and not r.get("deleted"):
            return {"canvas": r}
    return {"error": "canvas not found"}


def tool_canvas_delete(args):
    sql = _get_sql()
    if sql is None:
        return {"error": "SQL store unavailable"}
    cid = args.get("uuid")
    prev = next((r for r in _canvas_rows() if r.get("uuid") == cid), None)
    if prev is not None:
        prev = dict(prev)
        prev["deleted"] = True
        prev["rev"] = int(prev.get("rev", 0)) + 1
        prev["updated_at"] = _chat_now_iso()
        sql.upsert("canvases", cid, prev)
    return {"ok": True}


def _hoplita_pdf():
    import hoplita_pdf
    return hoplita_pdf


def _hoplita_office():
    import hoplita_office
    return hoplita_office


def register_tools():
    global TOOLS
    TOOLS = {
        "plan_and_orchestrate":   {"desc": "Breaks a task into sub-agents and runs them: you pass the whole task in 'task', it plans the sub-tasks (each with its role, its goal, its capabilities and its dependencies), runs the independent ones in parallel, and synthesises one answer. EACH SUB-TASK CAN NAME ITS OWN MODEL from the pool, and its own machine ('target': local, child:<handle> or friend:<handle>), which is what this can do that a single pass cannot. The plan is shown to the user for approval before it runs. 'task' must be the full description of what has to be achieved, self-contained: the planner sees it and not your conversation.",
                                   "fn": lambda a: tool_plan_and_orchestrate(a.get("task", ""), solo_single=True)},
        "call_council":           {"desc": "Puts one matter to several of the person's models and returns their DELIBERATED answer. The round: the first member answers, the others examine that answer on two criteria (are there errors in it? can it be done better?), the first reformulates with their feedback, and the others vote placet or not. You get back the agreed answer plus, signed, whoever did not agree. 'topic' must be self-contained: whoever sits down has not read your conversation, so state the matter, what you have tried and what you need decided. 'members' are pool aliases (empty = every pool model that sits on councils; a model marked as not sitting is refused), 'first' is who answers first (empty = the first of the list), and 'context' carries what they need that does not belong in the statement of the matter. Costs one call per member per phase.",
                                   "fn": lambda a: tool_call_council(
                                       a.get("topic", ""), a.get("members"),
                                       a.get("first", ""), a.get("context", ""))},
        "search_web":             {"desc": "For anything you need to know AND DO NOT ALREADY HAVE. If one of the user's saved SKILLS covers this, use that instead — a skill exists precisely because the generic web answer was not good enough, and a saved skill beats a search every time. Check the user's saved memory first — it is in front of you and it is authoritative about their world: who administers their machines, what they decided, what they own. No web search can confirm those, and going to the web for them makes you answer 'I could not verify it' about something you were told. For everything else — who, what, when, how much, does it exist — this is the default. One request, and it usually comes back already written with citations. Returns 'kind':'answer' (an answer with sources) or 'kind':'sources' (raw sources you must synthesize yourself) — check the 'provider'/'kind' field. Try this BEFORE browse_web, which drives a real browser and costs minutes.",
                                   "fn": lambda a: tool_search_web(a["query"])},
        "run_command":            {"desc": f"Run a command ({platform.system()}). Pass `timeout` in SECONDS for anything that is not instant — an install, a build, a migration, an acceptance script. When it is cut short you get exit_code -1 with only part of the output: that is the clock running out, NOT the command failing, so re-run it with a bigger timeout instead of concluding anything from the fragment.",
                                   "fn": lambda a: tool_run_command(a["command"], a.get("cwd"), a.get("timeout", 60))},
        "render_html":            {"desc": "Render HTML/CSS/JS/SVG in the GUI's visual CANVAS (a Chromium webview). To explain CODE — a flow, a call path, a data shape, a change — use the whiteboard (wb_*: diagrams anchored to the code) instead. It is a FREE canvas, not just for dashboards: use it for ANY other visual content — charts (Chart.js 4 already available, or D3/Plotly via CDN), SVG (diagrams, illustrations, icons, schematics), CSS/JS animations, canvas/WebGL (three.js via CDN), maps, UI mockups, tables, timelines, trees, graphs, or complete HTML pages. You can send just the <body> (it gets wrapped in a dark theme) or a complete HTML document (rendered as-is). If you need data, fetch it first (run_command, search_web...) and build the HTML with it. For a LONG document, split it across 2-4 calls with append=true — one giant call gets cut off by the output token limit.",
                                   "fn": lambda a: tool_dashboard_render(a.get("html", ""))},
        "dashboard_render":       {"desc": "[ALIAS of render_html] Render a dashboard/visualization in the GUI canvas. Same as render_html: accepts HTML/CSS/JS/SVG. Kept for compatibility; prefer render_html.",
                                   "fn": lambda a: tool_dashboard_render(a.get("html", ""))},
        "see_image":              {"desc": "LOOK at an image FILE on this machine (png/jpg/gif/webp): it is attached to the conversation as a picture you can actually see — screenshots, scans, evidence, diagrams. Use it instead of OCR through the shell. Needs a vision-capable chat model; two images per round at most.",
                                   "fn": lambda a: tool_see_image(a["path"], a.get("max_width", 1024))},
        "canvas_see":             {"desc": "LOOK at the canvas: a screenshot of what it shows RIGHT NOW, attached to the conversation as an image you can actually see. Use it after render_html or a batch of canvas_eval changes to judge what numbers cannot: composition, colour, overlap, whether the scene reads as intended. Console errors ride along. Needs a vision-capable chat model ([CHAT] vision); without one it says so and canvas_eval remains your instrument.",
                                   "fn": lambda a: tool_canvas_see(a.get("max_width"))},
        "canvas_eval":            {"desc": "Run JavaScript in the LIVE canvas page and get the value back (the last expression, JSON-serializable). The page KEEPS RUNNING: nothing re-renders, animations keep their state. This is how you BUILD something big — render the base once, then grow and fix it in increments — and how you CHECK your work: ask the page where a sprite actually is, whether a timer fired, what an element's position is, instead of guessing. Errors from the page's console ride back in the result. If your JS is async, set a global (window.x = ...) and read it in a second call.",
                                   "fn": lambda a: tool_canvas_eval(a.get("js", ""))},
        "read_file":              {"desc": "Read a file.",
                                   "fn": lambda a: tool_read_file(a["path"])},
        "list_dir":               {"desc": "List a directory.",
                                   "fn": lambda a: tool_list_dir(a.get("path", "."), a.get("recursive", False))},
        "list_experience":        {"desc": "List stored experiences (goal, verdict, tags, date), newest first. The third memory store next to facts and lessons: what was ATTEMPTED and how it went. Use it to review what has been tried, when a query for search_experience is not what you want.",
                                   "fn": lambda a: list_experience(a.get("limit", 100))},
        "search_experience":      {"desc": "Search past experiences.",
                                   "fn": lambda a: search_experience(a["query"], a.get("n_results", 3))},
        "search_lessons":         {"desc": "Search learned lessons ONLY (reusable statements distilled from past feedback). Useful for remembering WHAT WORKED and WHAT TO AVOID before planning. For anything the user RECORDED — facts, tasks, notes — use recall instead.",
                                   "fn": lambda a: _with_scope(
                                       search_lessons(a["query"], a.get("n_results", 3),
                                                      a.get("min_confidence", 0.0),
                                                      a.get("exclude_stale", True)),
                                       "lessons", "lessons")},
        "list_lessons":           {"desc": "List ALL lessons (not by query). For periodic consolidation: see the full set, detect redundancies and superseded rules.",
                                   "fn": lambda a: list_lessons(a.get("include_stale", False))},
        "add_lesson":             {"desc": "Add a new lesson (e.g. a CONSOLIDATED rule that merges several). For memory consolidation.",
                                   "fn": lambda a: add_lesson(a["text"], a.get("tags"), a.get("confidence", 0.7))},
        "deactivate_lesson":      {"desc": "Deactivate a lesson (marks it stale, does NOT delete it — recoverable). Use it when a rule is superseded by a more general one during consolidation.",
                                   "fn": lambda a: deactivate_lesson(a["id"])},
        "delete_lesson":          {"desc": "DELETE a lesson by id (permanent). For consolidation, prefer deactivate_lesson (recoverable). Use it only for clear junk (meta-comments, worthless lessons).",
                                   "fn": lambda a: delete_lesson(a["id"])},
        "list_facts":             {"desc": "List ALL the user's facts (not by query). For periodic consolidation: see the full set and detect redundancies or obsolete facts.",
                                   "fn": lambda a: list_facts()},
        "add_fact":               {"desc": "Save a FACT confirmed by the user about themselves or their world (name, mapping, personal detail, preference, equivalence). Use it WHEN the user explicitly says 'learn that X', 'remember that Y', 'don't forget that Z'. These facts are automatically injected into the context in future conversations. Do NOT use this for technical documentation or notes — for that use add_note. Pass `kind` when it is not a permanent truth: an intention ('wants to', 'is going to') or a state ('right now X'), so it can be retired when it stops applying instead of being injected for ever. If the result lists RELATED existing facts, READ them: when the new fact supersedes one (moved cities, changed jobs), delete_fact or edit_fact the stale one NOW — two contradictory facts injected together poison every future turn.",
                                   "fn": lambda a: tool_add_fact_checked(a["text"], a.get("tags"), a.get("kind"))},
        "session_moments":        {"desc": "Find the MOMENTS you talked about something, not the lines that match. A conversation is a burst of messages about one thing: two common words may each appear everywhere alone, but the few minutes where BOTH appear is the conversation you want — and the answer is usually two messages later. Ranked by how many DISTINCT query terms a block covers, weighted by how rare each is in the whole log. PREFER THIS over session_search when you are looking for a past conversation rather than a single quote.",
                                   "fn": lambda a: tool_session_moments(a["query"], a.get("limit", 4), a.get("window_min", 5))},
        "session_search":         {"desc": "Search everything ever SAID, verbatim, across all past conversations. No summary, no distiller, no model: the raw log, in milliseconds. Use it for 'what did we decide about X', 'what was that command', 'which one was I going to pick' — and ALWAYS before telling the user there is no record of something. Each hit comes with the messages around it.",
                                   "fn": lambda a: tool_session_search(a["query"], a.get("limit", 6), a.get("session"), a.get("context", 1), a.get("include_machinery", False))},
        "propose_memory_change":  {"desc": "Propose retiring or rewriting one of the user's facts — you do NOT change it, you propose and they decide. For the weekly memory pass: an INTENTION they never came back to may have happened, been dropped, or still stand. REQUIRED: 'quotes', literal lines from the log (get them with session_moments) that show it. If you cannot find any, propose NOTHING — an old intention is not evidence that it happened, and 'I do not know' is the correct answer. action=retire when it no longer applies; action=replace with new_text when it became something else — an intention that was carried out is rewritten as the thing that happened, and stops being an intention.",
                                   "fn": lambda a: tool_propose_memory_change(
                                       a["target_id"], a["action"], a.get("why", ""),
                                       a.get("quotes") or [], a.get("new_text", ""))},
        "recall":                 {"desc": "Search EVERYTHING the user has recorded OR EVER SAID — facts, lessons, notes, tasks, appointments AND THE VERBATIM LOG OF EVERY PAST CONVERSATION — in one call. YES, IT SEARCHES PAST CHATS: the conversation log is one of its drawers, searched by MOMENTS (the burst of messages where your terms co-occur, which is the conversation you are looking for) and not by loose matching lines. So for \"what did we decide about X\", \"what did I say about Y\", \"that command from the other day\" this IS the tool, and you do not need session_search or session_moments first — reach for those only to dig further into a conversation this one surfaced. Use it for any 'what do I have written down / what did I say / what am I supposed to do about X'. Prefer it over the per-store searches: those look in ONE drawer, and a wrong guess reads as 'you have no record of that' when the record exists. It searches BY WORDS, so ALWAYS pass 'terms' with the words the record itself would likely contain — synonyms, the category word, brands, proper names. For 'what car should I buy': car, vehicle, brand, model, hybrid, petrol, electric. That expansion is your job and it costs nothing.",
                                   "fn": lambda a: tool_recall(a["query"], a.get("terms"), a.get("n_results", 4))},
        "search_facts":           {"desc": "Search the user's saved facts ONLY (tasks, notes, appointments and lessons are OTHER stores — use recall to cover all of them). Normally you do not need this: the relevant facts are injected automatically at the start of the chat.",
                                   "fn": lambda a: _with_scope(
                                       {"facts": search_facts(a["query"], a.get("n_results", 5))},
                                       "facts", "facts")},
        "delete_fact":            {"desc": "DELETE a saved user fact, by its id. Use it when the user asks to forget/delete/remove a fact. If you don't know the id, first call search_facts to find it and get its 'id', then delete with that id.",
                                   "fn": lambda a: ({"deleted": True} if _remove_fact(a["id"])
                                                    else {"deleted": False,
                                                          "why": "no fact with that id — already "
                                                                 "deleted or wrong id. Do NOT retry: "
                                                                 "re-check with search_facts if you "
                                                                 "must, or move on."})},
        "edit_fact":              {"desc": "EDIT/correct a saved fact, by its id. Use it when the user wants to correct a badly saved fact. If you don't know the id, first search_facts to find it. You can change 'text' and/or 'tags'.",
                                   "fn": lambda a: {"updated": _update_fact(a["id"], a.get("text"), a.get("tags")) is not None}},
        "search_notes":           {"desc": "Search the user's technical notes and documentation (config, commands, code) ONLY. Each hit is the PASSAGE where the words are, with a paragraph either side and its lines for read_note — not the note's top. Does NOT contain facts, tasks or appointments — to cover every store at once use recall.",
                                   "fn": lambda a: _with_scope(
                                       tool_search_notes(a["search_term"], a.get("category")),
                                       "results", "notes")},
        "use_skill":              {"desc": "Get a saved skill BY ITS TITLE. The titles you can ask for are listed in your context — pass the title, the part before the dash, as 'search_term'. Do NOT pass the description. 'category' is an optional FOLDER name; if you do not know one, leave it out.",
                                   "fn": lambda a: tool_use_skill(a["search_term"], a.get("category"))},
        "skill_folder":           {"desc": "Mark a note folder as SKILLS, or unmark it. Skills are not one fixed folder: the default one is always in, and the user adds any other — a folder a contact shared, one they subscribed to, a project's own. Marked folders join the skills index and use_skill searches them. Pass on=false to take one out. With no 'category', it just answers which folders are skills right now.",
                                   "fn": lambda a: tool_skill_folder(a.get("category"), a.get("on", True))},
        "list_categories":        {"desc": "List note categories.",
                                   "fn": lambda a: {"categories": notes_mgr.get_categories()}},
        "add_note":               {"desc": "Add a technical note/documentation to an existing user category. Do NOT use for personal facts or learnings — for that use add_fact.",
                                   "fn": lambda a: tool_add_note(a["category"], a["title"], a["content"])},
        "delete_note":            {"desc": "Delete a note by category and ID.",
                                   "fn": lambda a: tool_delete_note(a["category"], a["note_id"])},
        "update_note":            {"desc": "Edit a note IN PLACE (atomic, keeps the ID, doesn't delete-then-add). Pass 'content' to rewrite it, or 'changes': [{old, new}] to edit a LONG note without rewriting it — each 'old' must match exactly once.",
                                   "fn": lambda a: tool_update_note(a["category"], a["note_id"], a.get("title"), a.get("content"), a.get("changes"))},
        "get_credential":         {"desc": "What is stored for a site, WITHOUT the secret. "
                                    "Returns 'kind': web (username + whether there is a "
                                    "password/OTP), apikey (whether there is a key — ask for "
                                    "it with get_api_key) or card (brand tail and expiry, "
                                    "never the number). Check this first: it tells you "
                                    "whether you can do the job or have to ask the user.",
                                   "fn": lambda a: tool_get_credential_safe(a["site"])},
        "get_card":               {"desc": "FULL card details (number, expiry, CVV, holder) "
                                    "for a site saved as kind 'card'. The owner chose this "
                                    "deliberately: half a card completes no purchase. Use it "
                                    "only to complete a payment the user asked for, never "
                                    "print it in your answer and never write it into a note "
                                    "or a skill.",
                                   "fn": lambda a: tool_get_card(a["site"])},
        "get_api_key":            {"desc": "The API key IN CLEAR for a site stored as kind "
                                    "'apikey'. This is the one secret you are meant to "
                                    "handle: it goes in a header YOU write, there is no "
                                    "browser to type it for you. Do not print it in your "
                                    "answer and do not paste it into a note or a skill — "
                                    "read it from here each time.",
                                   "fn": lambda a: tool_get_api_key(a["site"])},
        "get_otp":                {"desc": "Current OTP/2FA code.",
                                   "fn": lambda a: tool_get_otp(a["site"])},
        "list_credentials":       {"desc": "List saved sites with their kind (web / apikey / "
                                    "card / mail), so you can tell what is available before asking "
                                    "the user for something they already gave you.",
                                   "fn": lambda a: {"sites": cred_mgr.list_sites()}},
        "fill_credentials":       {"desc": "Autofill creds in the active browser (use inside browse_web).",
                                   "fn": lambda a: {"info": "fill_credentials runs inside browse_web"}},
        "get_todo_context":       {"desc": "Every project's tasks with progress and dependencies (each marked READY or BLOCKED), plus the appointments. Read it before adding or planning: what exists, what is done, what waits on what.",
                                   "fn": lambda a: tool_get_todo_context()},
        "add_appointment":        {"desc": "Create an appointment. Optional 'project' param (default 'general'). Use list_todo_projects first to see the available ones.",
                                   "fn": lambda a: tool_add_appointment(a["date"], a["title"], a["description"], a.get("urgency", "Normal"), a.get("project"))},
        "add_task":               {"desc": "Add a task to a project. The PROJECTS tab draws it as a box with its % and its arrows. Optional 'project' (default 'general'; list_todo_projects shows them) and 'depends_on': titles of tasks it waits for.",
                                   "fn": lambda a: tool_add_task(a["title"], a["description"], a.get("priority", "Media"), a.get("progress", 0), a.get("project"), a.get("depends_on"))},
        "update_task_progress":   {"desc": "Set a task's progress, 0-100.",
                                   "fn": lambda a: tool_update_task_progress(a["task_title"], a["new_progress"])},
        "task_depends":           {"desc": "Set which tasks a task waits for (replaces the whole list; [] clears it). Titles are matched within the task's project. The board reads it as READY vs BLOCKED.",
                                   "fn": lambda a: tool_task_depends(a["task_title"], a.get("depends_on", []), a.get("project"))},
        "list_todo_projects":     {"desc": "List the projects tasks and appointments are grouped in. 'general' always exists.",
                                   "fn": lambda a: tool_list_todo_projects()},
        "list_appointments":      {"desc": "List the calendar appointments (all, or filtered by 'project').",
                                   "fn": lambda a: tool_list_appointments(a.get("project"))},
        "delete_appointment":     {"desc": "Delete one appointment by its 'id' (as list_appointments gives it), or calendar appointments by 'title' (exact) and optionally 'date': 'YYYY-MM-DD HH:MM' for that moment, 'YYYY-MM-DD' for any time that day. Deletes ALL matching ones (useful for cleaning duplicates) and returns how many.",
                                   "fn": lambda a: tool_delete_appointment(a.get("title"), a.get("date"), a.get("id"))},
        "delete_all_appointments": {"desc": "Delete MULTIPLE appointments at once (clean up duplicates without enumerating titles). Optional combinable filters: 'contains' (title substring), 'date', 'project'. With NO filters it deletes the WHOLE calendar and requires 'confirm':true. Returns how many it deleted.",
                                   "fn": lambda a: tool_delete_all_appointments(a.get("contains"), a.get("date"), a.get("project"), a.get("confirm", False))},
        "add_todo_project":       {"desc": "Create a project to group tasks and appointments. Use it when the user wants a new group.",
                                   "fn": lambda a: tool_add_todo_project(a["name"])},
        "delete_todo_project":    {"desc": "Delete a project. The items that were in it are automatically moved to 'general' (they are not lost).",
                                   "fn": lambda a: tool_delete_todo_project(a["name"])},
        "list_cron":              {"desc": "List CRON jobs.",
                                   "fn": lambda a: tool_list_cron()},
        "add_cron":               {"desc": "Add a CRON job (agentic scheduled task). "
                                    "schedule_type: 'interval' (every interval_minutes), "
                                    "'daily' (daily_times, HH:MM), 'weekly', 'fixed' (once, "
                                    "fixed_datetime 'YYYY-MM-DD HH:MM') or 'startup' (every "
                                    "time HOPLITA starts — for a machine that is not on all "
                                    "day; a daily or weekly job whose hour passed while it "
                                    "was off also runs once at the next start). "
                                    "Optional 'target': 'local' (my agent, by default), "
                                    "'child:<handle>' (a child of mine) or 'friend:<handle>' (a "
                                    "friend). With a remote target, the scheduled task runs "
                                    "on the child's/friend's machine — local cron morphs into "
                                    "children's/friends' cron. own_tab=true gives a local job "
                                    "a tab of its own (a worker: a mail watcher, a review "
                                    "bot): every run is a turn in that tab, the person can "
                                    "open it and talk to it between runs, and it runs in "
                                    "parallel with other tabs. With 'startup' it opens and "
                                    "starts working when HOPLITA starts.",
                                   "fn": lambda a: tool_add_cron(a["name"], a["instruction"], a["schedule_type"],
                                                                  a.get("interval_minutes", 0), a.get("fixed_datetime", ""),
                                                                  a.get("daily_times"), a.get("target", "local"),
                                                                  a.get("context_session", ""),
                                                                  a.get("model", ""),
                                                                  a.get("context_live", True),
                                                                  a.get("context_current", False),
                                                                  a.get("own_tab", False))},
        "load_tools":             {"desc": "Get the full schema (parameters) of tools you can see "
                                    "listed by name but not yet expanded. Pass the exact names. "
                                    "Call this the moment you know which tool you need — the list "
                                    "is complete, so if a name is there the tool exists and works. "
                                    "ASK FOR EVERY TOOL YOU WILL NEED IN ONE CALL: expanding a "
                                    "schema changes the tool set, which sits at the top of the "
                                    "prompt — so the server re-processes the whole context instead "
                                    "of reusing its cache. That price is paid once per expansion — two separate calls pay it twice.",
                                   "fn": lambda a: tool_load_tools(a.get("names") or [])},
        "pdf_info":               {"desc": "X-ray a PDF before shaping it: page count and sizes, "
                                    "outline, metadata, form fields, printed-folio LAYOUT detection "
                                    "(two-up scans, unnumbered preliminaries → the offset between "
                                    "printed and physical pages), text by page or by edge band, and "
                                    "page→PNG render to LOOK at a scan. Read-only.",
                                   "fn": lambda a: _hoplita_pdf().tool_pdf_info(a)},
        "pdf":                    {"desc": "The PDF clay: reshape any PDF with a PIPELINE of steps "
                                    "applied in order, then write to output. Verbs: split_two_up, "
                                    "select (physical or printed numbering with auto mapping), "
                                    "merge, rotate, crop, scale, n_up, stamp, watermark, "
                                    "page_numbers, overlay_pdf, fill_form, encrypt. One call is one "
                                    "whole recipe; the result narrates every step and every auto "
                                    "decision. Use pdf_info first when unsure of the layout.",
                                   "fn": lambda a: _hoplita_pdf().tool_pdf(a)},
        "mail_folders":           {"desc": "A mailbox's folders, each with its role (inbox, junk, trash, sent, "
                                    "archive, all, drafts) and its message and unread counts. 'account' is "
                                    "the name of a credential of kind 'mail' (list_credentials); with one "
                                    "mailbox stored it can be left out.",
                                   "fn": lambda a: tool_mail("folders", a)},
        "mail_list":              {"desc": "The newest messages of a mailbox folder (default INBOX), each with "
                                    "uid, from, subject, date, seen and a snippet of its text; searched by "
                                    "unseen, since/before (YYYY-MM-DD), from, to, subject, text. Reading "
                                    "never marks a message read. The uid belongs to that folder.",
                                   "fn": lambda a: tool_mail("list", a)},
        "mail_read":              {"desc": "One message whole: headers, its text (HTML turned to text), its "
                                    "attachments by name and size. Does not mark it read.",
                                   "fn": lambda a: tool_mail("read", a)},
        "mail_move":              {"desc": "Move messages (uids from mail_list, of 'folder', default INBOX) to "
                                    "another folder or to a role: 'junk', 'trash', 'inbox', 'archive'. "
                                    "This is how mail is taken out: there is no permanent delete, and a "
                                    "move is undone by moving the new_uids it returns back to 'inbox'. "
                                    "Report what you moved, sender and subject, so the user can ask for "
                                    "it back.",
                                   "fn": lambda a: tool_mail("move", a)},
        "mail_send":              {"desc": "Send an email from the mailbox's address, now: to/cc/bcc, a subject and a "
                                    "plain-text body (html optional), files on disk attached by path. To answer a "
                                    "message give reply_to_uid (and its folder): it is threaded, 'Re:' and the "
                                    "recipient are taken from it unless given. A copy is kept in Sent. There is no "
                                    "draft and no undo: send what the user or your task asks you to send.",
                                   "fn": lambda a: tool_mail("send", a)},
        "office_info":            {"desc": "Read a Word, Excel or PowerPoint file (.docx/.xlsx/.pptx and their macro "
                                    "twins): a document's blocks by index (paragraphs with their style, tables), a "
                                    "workbook's sheets and a cell range with values AND formulas, a deck's slides with "
                                    "each shape's id, name and text plus the notes. find='text' says where a text lives. "
                                    "view=true paints the whole file on the canvas as HTML (content faithful, layout "
                                    "approximate). Read-only. Legacy .doc/.xls/.ppt and OpenDocument are refused.",
                                   "fn": lambda a: _hoplita_office().tool_office_info(a)},
        "office":                 {"desc": "Edit a Word, Excel or PowerPoint file with a PIPELINE of steps applied in "
                                    "order, then written to output (which may be the source). A replace keeps the "
                                    "formatting, even across Word's split runs. Indexes come from office_info. A step "
                                    "that fails writes nothing; overwriting keeps a backup; and a part the library "
                                    "cannot carry (some charts, shapes…) is named, and the source is not overwritten "
                                    "unless accept_loss=true.",
                                   "fn": lambda a: _hoplita_office().tool_office(a)},
        "intent":                 {"desc": "Your working intent list for THIS turn — your thinking, "
                                    "visible to the user live. Open any multi-step task by setting "
                                    "it (set=[3-6 short goals]) and keep it honest while you work: "
                                    "mark ids done/failed as reality answers, point doing at what "
                                    "you attack next, and REWRITE the list the moment the plan "
                                    "changes. Costs nothing and takes no extra step. "
                                    "IT IS NOT YOUR ANSWER AND IT IS NEVER PART OF IT: the user "
                                    "watches this list live, so closing it, summarising it or "
                                    "reporting that its points are done tells them nothing they "
                                    "cannot already see. When the work is finished, answer the "
                                    "QUESTION — the list is scaffolding and scaffolding does not "
                                    "get delivered.",
                                   "fn": lambda a: json.dumps({"error": "intent only lives inside "
                                                               "an agentic turn"})},
        "cron_set_context":       {"desc": "Attach a saved conversation to an EXISTING cron job as "
                                    "inherited context (or pass no session to detach it).",
                                   "fn": lambda a: tool_cron_set_context(a["job_id"], a.get("session", ""))},
        "session_brief":          {"desc": "Summarise a saved conversation (by title or uuid) so a "
                                    "scheduled task can inherit its context. Cached per revision.",
                                   "fn": lambda a: tool_session_brief(a)},
        "session_recall":         {"desc": "Search INSIDE a saved conversation. Use when the inherited "
                                    "brief is not enough and you need the detail — a figure, who said "
                                    "what — without loading the whole thing.",
                                   "fn": lambda a: tool_session_recall(a)},
        "delete_cron":            {"desc": "Delete a CRON job.",
                                   "fn": lambda a: tool_delete_cron(a["job_id"])},
        "update_cron":            {"desc": "Change an existing CRON job: its task (instruction), name, "
                                    "schedule or model, or pause/resume it. Only the fields given "
                                    "change. In a job's own tab, job_id may be left out: it is that "
                                    "tab's job — when the person tells the worker to change what it "
                                    "does or how often, this is how.",
                                   "fn": lambda a: tool_update_cron(a)},
        "toggle_cron":            {"desc": "Enable/pause a CRON job.",
                                   "fn": lambda a: tool_toggle_cron(a["job_id"], a["enabled"])},
        "delete_current_cron_job":{"desc": "Delete the CRON job that is running now — ONLY when the "
                                    "task itself says it is one-shot and must never run again, or "
                                    "the user asked for its removal. NEVER as a routine closing "
                                    "step of a periodic job. When unsure, prefer "
                                    "disable_current_cron_job or do nothing.",
                                   "fn": lambda a: tool_delete_current_cron()},
        "set_cron_context":       {"desc": "Set the active CRON job (internal use).",
                                   "fn": lambda a: tool_set_cron_context(a["job_id"])},
        "disable_current_cron_job":{"desc": "Pause the CRON job that is running now — ONLY when the "
                                    "task itself says it must not run again (a one-shot that is now "
                                    "done, or the user asked to stop it). NEVER as a routine closing "
                                    "step: pausing a periodic job kills its schedule, and finishing "
                                    "one run is not a reason. If the task says nothing about "
                                    "stopping, do not touch this.",
                                    "fn": lambda a: tool_disable_current_cron()},
        "alert":                  {"desc": "Send an alert to the user.",
                                   "fn": lambda a: tool_alert(a.get("title", "🔔"), a.get("message", ""))},
        "get_agentic_config":     {"desc": "View the agent configuration.",
                                   "fn": lambda a: tool_get_agentic_config()},
        "see_my_config":          {"desc": "Your own configuration: where config.ini is, its sections and keys, and with `section` that section's values — secrets (keys, passwords, tokens) are covered, you see that they are set and how long they are, never what they are. For [MCP_SERVERS] it also says which external servers are connected and how many tools each brought. Look here BEFORE changing anything, and before telling the user where a setting lives.",
                                   "fn": lambda a: tool_see_my_config(a.get("section"))},
        "edit_my_config":         {"desc": "Change your own config.ini — one key (section+key+value), or several under `changes` when they belong together (a pool model is [MODELS] <alias> = true plus its [MODEL:<alias>] section: send them in ONE call) — and apply it at once (same hot reload as the Configuration dialog). The person is asked to approve the exact changes first, once per call; on a turn nobody can answer, or one that came from another machine, it refuses. Comments and every other key are kept, a .bak is left beside the file, and a change that would leave the file unreadable is not written. In [MCP_SERVERS] the server you add, change or remove is connected or dropped on the spot, and the answer lists its tools — usable in this same turn. Never write back a covered secret (••••): ask the person for the value. Read the manual's section for a key (read_my_docs) before writing it: do not invent keys.",
                                   "fn": lambda a: tool_edit_my_config(a.get("section"), a.get("key"), a.get("value"), a.get("delete", False), a.get("changes"))},
        "read_my_docs":           {"desc": "Your own manual — the one this install shipped with, so it describes the version that is running. No arguments: the outline (files and section titles). `query`: the sections where those words appear, with the matching lines. `section` (+ optional `file`): that one section's full text. Use it to know how a feature works or what a config key does instead of guessing; read one section, not the whole manual.",
                                   "fn": lambda a: tool_read_my_docs(a.get("query"), a.get("section"), a.get("file"))},
        "screenshot_web":         {"desc": "Capture a webpage as a PDF on disk.",
                                   "fn": lambda a: tool_screenshot_web(a["url"], a.get("headless"), a.get("output_path"))},
        "browse_web":             {"desc": ("EXPENSIVE — drives a real browser, step by step, with a model "
                                            "deciding every click. That is one page load and one model call PER "
                                            "STEP, so it costs orders of magnitude more than a search, on any "
                                            "machine. Use it ONLY for "
                                            "what a search cannot do: logging in, filling forms, clicking through "
                                            "an interface, reading something that only exists behind interaction. "
                                            "If the user just wants to KNOW something — who sang it, what it costs, "
                                            "when it happened — use search_web; if you need the text of a page you "
                                            "already know the URL of, use scrape_url. Renting a yacht to cross a "
                                            "puddle is not thoroughness, it is ten minutes of someone waiting. "
                                            "THE FLIP SIDE IS JUST AS BINDING: when the user hands you a SPECIFIC "
                                            "URL or asks for what only THAT page shows (a fare grid, a cart, live "
                                            "listings) and the cheap readers came back blocked or empty, the "
                                            "browser is not a yacht — it is the only tool left, so USE it instead "
                                            "of downgrading to a generic search. And never claim you used or "
                                            "tried it unless a browse_web call actually ran this turn. "
                                            "The 'task' field: say what to DO on the page; the user's own wording "
                                            "is appended as context, you do not need to copy it. CREDENTIALS: a "
                                            "secret boards the browser only for sites the task NAMES — when a "
                                            "login is expected, check get_credential first and name the site in "
                                            "the task; the rest travel as names only, never as secrets."),
                                   "fn": lambda a: tool_browse_web(a["task"], a.get("url", ""),
                                                                    a.get("headless"), a.get("max_steps"),
                                                                    endpoint=a.get("endpoint"))},
        "scrape_url":             {"desc": "Read a URL with a cascade: crawl4ai (renders JavaScript) → plain HTTP → browser-use driving the page if needed. Use it for any page; it does not give up on JS-heavy sites. Pass depth=1..3 to also follow links on the same domain (prospective crawl).",
                                   "fn": lambda a: tool_scrape_url(a["url"], a.get("max_chars", 10000),
                                                                   a.get("escalate", True), a.get("depth"))},
        "search_rag":             {"desc": "Search what is LOADED in this conversation's knowledge base: the attached documents (PDFs, files, URLs) and the notes and experience. When an attached document was cut («… do not fit here»), the rest of it is HERE — search it before saying something is not in the document. Returns the matching passages with their source.",
                                   "fn": lambda a: tool_search_rag(a["query"], a.get("top_k", 5))},
        "code_projects":          {"desc": "EVERY code project this machine knows: name, path, whether the folder still exists, how many notes its memory holds and who it is co-worked with. Read-only — the answer to «what do I even have?» without opening folders one by one.",
                                   "fn": lambda a: tool_code_projects()},
        "code_project_name":      {"desc": "What a code project is called here and whether it already HAS an identity (memory folder + task project). Read-only: the name is chosen by the user at birth, never by you.",
                                   "fn": lambda a: tool_code_name(a.get("path", "."))},
        "code_index":             {"desc": "Index a project: tree, languages, sizes. Use FIRST. `name` is the project's name when the USER chose one — never invent it: the folder's basename is the default and only a human renames it.",
                                   "fn": lambda a: tool_code_index(a.get("path", "."), a.get("name"))},
        "sticky_note":            {"desc": "REWRITE the conversation's sticky note — the working note the PERSON reads and edits, pinned above the chat. Not your progress (that is `intent`) and not memory: what is settled and what they still have to run. Typical contents: «we left it at this point», the exact command that turned out to be right, the four steps left to install something, a path or a version that cost work to find out. You do not need a tool to READ it: it rides at the top of every turn, with their edits already in it, and what is in there beats your memory of it. Max 15 lines — it is a sticky note, and what does not fit has to be distilled. You pass the WHOLE note every time: it is rewritten, never appended to, so drop what stopped mattering. Empty text clears it. Write it when something gets SETTLED, not while you are still finding out.",
                                   "fn": lambda a: {"error": "sticky_note is written by the client that owns the tab"}},
        "canvas_undo":            {"desc": "Put back the page the canvas held before the last render. One step. Use it when what you just rendered turned out worse than what it replaced — going back is cheaper and safer than rebuilding from memory, and the page you are trying to recover may be one you can no longer reproduce.",
                                   "fn": lambda a: {"error": "canvas_undo is executed by the client that owns the canvas"}},
        "code_symbols":           {"desc": "WHERE A NAME IS DECLARED, across the whole project, WITHOUT READING A FILE. One regex pass over the tree, cached until something changes: milliseconds, no language server, no model. THIS IS THE FIRST THING TO CALL when you need a function, class, type or method and you do not already know its line — not code_read_smart, which on a big file returns a structure so large it gets cut, taking the very map you were after with it. Empty query = the project's shape (how many symbols, in how many files, which files hold most). With a name = exact matches first, each with file:line and the declaration line itself. Then code_read_range(file, line-2, line+40) IS the function, in one call. Reading a whole file to find one function is the slow road and it does not scale: on a hundred files it is a hundred reads that hold nothing.",
                                   "fn": lambda a: tool_code_symbols(a.get("query", ""), a.get("path", ""), a.get("limit", 40))},
        "code_read_smart":        {"desc": "Smart file read: if it fits in context → whole, otherwise → structure + chunks. 'search' is LITERAL text (case-insensitive), not a regex.",
                                   "fn": lambda a: tool_code_read_smart(a["path"], a.get("search"))},
        "code_read_range":        {"desc": "Read a range of lines from a file.",
                                   "fn": lambda a: tool_code_read_range(a["path"], a["start_line"], a["end_line"])},
        "code_search":            {"desc": "Search a LITERAL text (case-insensitive substring) across the project. NOT a regex — write the text exactly as it appears in the code.",
                                   "fn": lambda a: tool_code_search(a.get("path", ""), a["query"], a.get("file_pattern"))},
        "code_apply_patch":       {"desc": "Apply multiple edits atomically. If one fails, none are applied. Also creates new files if 'old' is empty. In a git repo it takes a checkpoint first, so code_undo can put it back.",
                                   "fn": lambda a: tool_code_apply_patch(a["path"], a["changes"])},
        "code_write_file":        {"desc": "Write a whole file at once, taking a checkpoint first so it can be undone. Use code_apply_patch for surgical edits — it validates what it replaces; this one is for creating a file or replacing it entirely.",
                                   "fn": lambda a: tool_code_write_file(a["path"], a["content"])},
        "code_undo":              {"desc": "Undo the last edit(s) you applied with code_apply_patch, from the checkpoint taken before each one. Restores what the snapshot held and removes the files the patch created; anything the USER changed meanwhile is left alone. Use it when an edit turned out wrong.",
                                   "fn": lambda a: tool_code_undo(a.get("path"), a.get("steps", 1))},
        "code_propose_verify":    {"desc": "Gather the evidence to PROPOSE a definition of done for a project (build files, test files, languages) plus the rules a good one follows. Use it when the user asks for a verification command, or before a substantial change with no gate set. It only proposes: you cannot set the definition of done yourself, and you should not want to — a model that picks its own exam passes every time.",
                                   "fn": lambda a: tool_code_propose_verify(a.get("path"))},
        "code_definition":        {"desc": "Where a symbol is DEFINED, semantically, via the language server. Resolves an aliased import — which a text search cannot do at all. Says so if no server is available.",
                                   "fn": lambda a: tool_code_definition(a["path"], a["symbol"], a.get("project_path"))},
        "code_outline":           {"desc": "What a file declares and on which line — functions, classes, methods — to navigate a long file. From the language server when there is one (with nesting and real kinds), from a per-language regex when not, saying which.",
                                   "fn": lambda a: tool_code_outline(a["path"], a.get("content"))},
        "code_diagnostics":       {"desc": "What the language server reports as wrong in a file RIGHT NOW, including unsaved edits if you pass 'content'. Different from code_lint, which reads the file from disk: this is what an editor underlines as you type. Says available=false with a reason when there is no server.",
                                   "fn": lambda a: tool_code_diagnostics(a["path"], a.get("content"))},
        "code_lsp_status":        {"desc": "Which language servers are running for this project and which are missing. 'Not installed' and 'installed but it died' need different answers and both look like an empty result from outside.",
                                   "fn": lambda a: tool_code_lsp_status(a.get("path"))},
        "code_toolchain":         {"desc": "Which languages a project is written in and which linters / language servers are MISSING for them, with the exact install command. Call it when you open a project: it tells you whether code_references is doing a real semantic search or falling back to text (which can miss and over-match), so you know how much to trust it. It NEVER installs anything — the user decides.",
                                   "fn": lambda a: tool_code_toolchain(a.get("path"))},
        "code_checkpoints":       {"desc": "List the undo points recorded in this session for a project, newest first. Use it to see what code_undo would revert.",
                                   "fn": lambda a: tool_code_checkpoints(a.get("path"))},
        "code_reject_hunk":       {"desc": "Throw away ONE hunk of a change and keep the rest. Reviewing is half a decision if it has to be all or nothing: of three fixes the user may want two. Pass the file, the hunk's index in ITS diff, and the '@@ ...' header exactly as it was shown — if the diff moved meanwhile this refuses instead of mangling the file. Takes a checkpoint, so code_undo brings it back.",
                                   "fn": lambda a: tool_code_reject_hunk(a.get("path"), a["file"], a["hunk"],
                                                                         a.get("header", ""), a.get("patch", ""))},
        "git_status":             {"desc": "What is modified, staged and untracked in the project right now, and on which branch. Use it BEFORE editing, to know what was already dirty and not blame yourself for it.",
                                   "fn": lambda a: tool_git_status(a.get("path"))},
        "git_diff":               {"desc": "The actual change as a patch. since_checkpoint=true compares against the snapshot taken before YOUR first edit, which is the honest answer to 'what have you changed' — as opposed to everything dirty in the tree. commit=<sha> compares the tree as it is NOW against that commit (the time-travel question). stat_only=true for a summary when the patch would be huge.",
                                   "fn": lambda a: tool_git_diff(a.get("path"), a.get("files"), a.get("staged", False),
                                                                 a.get("since_checkpoint", False), a.get("stat_only", False),
                                                                 a.get("commit", ""))},
        "git_log":                {"desc": "Recent commits, newest first, with author and age. Use it to answer what changed in a project and who did it.",
                                   "fn": lambda a: tool_git_log(a.get("path"), a.get("limit", 15), a.get("files"))},
        "git_show":               {"desc": "ONE commit, whole: its metadata and the patch it introduced. The half git_log cannot answer — the log says a commit exists, this says what it DID. For 'what has changed since then', use git_diff with commit=<sha>.",
                                   "fn": lambda a: tool_git_show(a.get("path"), a.get("sha", ""))},
        "git_branch":             {"desc": "List branches, or create one and switch to it when 'name' is given. Make a branch before a substantial change: it is what lets the work be reviewed and dropped as a unit.",
                                   "fn": lambda a: tool_git_branch(a.get("path"), a.get("name"), a.get("base"))},
        "git_switch":             {"desc": "Go to a branch that already exists — main included. git_branch creates and switches; this is the way back. Refuses with uncommitted edits in the way and names them.",
                                   "fn": lambda a: tool_git_switch(a.get("path"), a.get("name", ""))},
        "git_merge":              {"desc": "Merge your work branch INTO the trunk (main by default): how a branch opened with git_branch comes home once its work is committed and verified. Refuses with uncommitted changes or conflicts, saying which files; a conflicting merge is aborted clean. Ends on the trunk.",
                                   "fn": lambda a: tool_git_merge(a.get("path"), a.get("branch", ""), a.get("into", "main"), bool(a.get("delete_branch", False)))},
        "git_commit":             {"desc": "Record a commit. By default it commits ONLY the files you edited this session, not everything dirty in the tree — the user's work-in-progress is not yours to sign. NEVER pushes. Refuses to commit on main/master unless the user explicitly asked (allow_main), because the trunk is the user's decision, not the loop's.",
                                   "fn": lambda a: tool_git_commit(a.get("path"), a.get("message", ""),
                                                                   a.get("files"), a.get("allow_main", False))},
        "project_journal":        {"desc": "Append a dated entry to the open project's journal — a note in the project's own folder, which holds its memory in four notes ('rules', 'spec', 'future', 'journal', plus the project's 'skill: …' notes) read at the start of the conversation, and again whenever one of them changes. Write down decisions taken, traps found and how things really build, and the next session starts knowing it — on every machine and every face, because it is a notes folder like any other.",
                                   "fn": lambda a: tool_project_journal(a["path"], a["entry"], a.get("title", ""))},
        "project_write":          {"desc": "Write one of the open project's notes: 'spec' (a settled truth, SHORT — one annotation, one breath), 'future' (something pending, one line per item) or 'journal' (what happened, dated). Appends; replace=true rewrites the note whole, which is how a grown spec is kept short. These three plus the user's 'rules' are the project's whole memory: add_note refuses any other title in a project folder.",
                                   "fn": lambda a: tool_project_write(a.get("path", ""), a.get("note", ""), a.get("text", ""), bool(a.get("replace", False)))},
        "search_working_memory":  {"desc": "Search INSIDE the full texts of the large results digested in this conversation (its working memory): the digest you got is an extract, the original is here whole. Words, a name, a figure — whatever you think of — and it returns the passages that mention it with the paragraphs around them and their lines; regex=true is grep. Use it before re-calling a tool for something an earlier result may already hold.",
                                   "fn": lambda a: tool_search_working_memory(a.get("query", ""), a.get("title", ""), bool(a.get("regex")), a.get("context", 1), a.get("k", 5))},
        "read_note":              {"desc": "One note, by title (forgiving: exact, then unique substring) or id — whole when it fits, else BY LINES like code_read_range: the first window plus 'next', the exact call for the following lines. search_notes previews 500 chars; this is the text itself. A miss answers with the titles that DO exist.",
                                   "fn": lambda a: tool_read_note(a["category"], a.get("title", ""), a.get("note_id", ""), a.get("start_line"), a.get("end_line"))},
        "code_lint":              {"desc": "Real diagnostics: type errors (pyright), unused imports (ruff), static analysis.",
                                   "fn": lambda a: tool_code_lint(a["path"])},
        "code_deps":              {"desc": "Extract imports/exports from a file (dependencies).",
                                   "fn": lambda a: tool_code_deps(a["path"])},
        "code_references":        {"desc": "Find where a symbol is used. Uses the language server when one is available (semantic, authoritative: source=\"lsp\") and falls back to a TEXT search when not, SAYING which you got. Check the 'source' field before trusting a count. The text fallback is not a semantic one: it can MISS a reference (an aliased import, a dynamic call, a name built at runtime) and can MATCH the same name in an unrelated scope. Useful to find candidates; NOT proof that you have them all. Before a rename, read what it returns instead of trusting the count, and say so to the user rather than claiming the rename is complete.",
                                   "fn": lambda a: tool_code_references(a["path"], a["symbol"], a.get("project_path"))},
        "generate_pdf":           {"desc": "Generate a PDF from markdown or HTML, rendered by a real browser: headings, code and REAL formatted tables. Pass markdown and it gets document styling; pass full HTML (e.g. exactly what you painted on the canvas with render_html) and it prints as-is — canvas → PDF.",
                                   "fn": lambda a: tool_generate_pdf(a["content"], a.get("title", "Document"), a.get("output_path"))},
        "call_api":               {"desc": "Call any REST API. Auth: bearer, api_key_header, "
                                    "api_key_query, basic. Pass 'auth_from' with a site saved "
                                    "as kind 'apikey' and the key is taken FROM THE VAULT — a "
                                    "skill then says WHICH key instead of carrying it in plain "
                                    "text inside its own body.",
                                   "fn": lambda a: tool_call_api(a["url"], a.get("method", "GET"), a.get("headers"), a.get("body"),
                                       a.get("auth_type"), a.get("auth_token"), a.get("auth_key_name"), a.get("auth_key_value"),
                                       a.get("query_params"), a.get("timeout", 30), a.get("save_response"),
                                       a.get("auth_from"))},
        "read_folder":            {"desc": "Read all files in a folder: text, PDFs and office files come with their text; pictures are LISTED with their path (look at one with see_image). For batch processing.",
                                   "fn": lambda a: tool_read_folder(a["path"], a.get("extensions"))},
    }
    for _n, _tool in TOOLS.items():
        if _n.startswith(("code_", "git_")):
            _tool["fn"] = _project_scoped(_tool["fn"])
    for _n in ("read_file", "code_read_smart", "code_read_range", "code_read_file"):
        if _n in TOOLS:
            TOOLS[_n]["fn"] = _spill_aware(_n, TOOLS[_n]["fn"])
    import wb_tools as _wbt
    TOOLS.update(_wbt.tools())
    def _wb_journal(path, text, title=""):
        try:
            known = _project_category(path) in (notes_mgr.get_categories() or [])
        except Exception:
            known = False
        if not known:
            return {"skipped": "not a project"}
        return tool_project_journal(path, text, title)
    _wbt.set_project_hooks(_project_category, _wb_journal)
    try:
        import wb_sync as _wbs
        import wb_store as _wbst

        def _wb_handle():
            try:
                return (fed_tools._store().data.get("handle") or "")
            except Exception:
                return ""

        def _wb_checkout(project):
            sql = _get_sql()
            return (sql.meta_get(f"code_path:{project}") or "") if sql else ""
        _wbst.store().checkout_for_project = _wb_checkout
        _wbs.setup(_get_sql, _wb_handle,
                   lambda proj: fed_tools._autopush_schedule("whiteboard", proj))
        _wbs.reconcile()
    except Exception as e:
        print(f"[WB-SYNC] not travelling: {e}", file=sys.stderr)
    try:
        import wb_jobs as _wbj
        _wbj.set_runner(_wb_draw_run, _wbt._project_of)
    except Exception as e:
        print(f"[WB-DRAW] unavailable: {e}", file=sys.stderr)


def _wb_draw_run(job):
    import fed_agent
    import wb_jobs as _wbj
    import wb_tools as _wbt
    import wb_vcs as _wbv
    root = job["root"]
    if job["what"] == "changes":
        r = subprocess.run(["git", "-C", root, "status", "--porcelain"],
                           capture_output=True, text=True, timeout=30,
                           **__import__("hoplita_paths").no_console())
        if r.returncode != 0:
            raise _wbj.JobError(f"{root} is not a git repository.")
        if not r.stdout.strip():
            raise _wbj.JobError("no_changes")
    elif job["what"] == "commit":
        try:
            _wbv.resolve(root, "HEAD~1")
        except Exception:
            raise _wbj.JobError("no_parent")
    info = None
    if job["what"] == "redraw":
        import wb_store as _wbst
        if not _wbst.store().exists(job["whiteboardId"]):
            raise _wbj.JobError(f"Whiteboard {job['whiteboardId']} does not exist.")
    if job["what"] == "update":
        import wb_store as _wbst
        try:
            info = _wbst.store().staleness(job["whiteboardId"])
            info["title"] = _wbst.store().snapshot(job["whiteboardId"]).get("title") or ""
        except Exception as e:
            raise _wbj.JobError(str(e))
        if not info.get("updatable"):
            raise _wbj.JobError("not_updatable")
        if not info.get("stale"):
            raise _wbj.JobError("up_to_date")
    alias = job.get("alias") or "base"
    if alias not in ("base", "default") and alias not in _model_pool():
        raise _wbj.JobError("model_unknown")
    _b, _k, _m = _orch_model_endpoint(alias, OrchestrationCtx(endpoint={}))
    if not _m:
        raise _wbj.JobError("No model configured to draw with.")
    job["model"] = _m

    import wb_pipeline as _wbp
    job["usage"] = {"calls": 0, "prompt": 0, "completion": 0, "largest": 0, "tools": {}}
    _acc_lock = threading.Lock()

    def _count(u):
        u = u or {}
        with _acc_lock:
            acc = job["usage"]
            acc["calls"] += 1
            acc["prompt"] += int(u.get("prompt_tokens") or 0)
            acc["completion"] += int(u.get("completion_tokens") or 0)
            acc["largest"] = max(acc["largest"], int(u.get("prompt_tokens") or 0))

    def _chat(msgs, tools, max_tokens=None):
        raw = _llm_chat_with_tools(msgs, tools, base_url=_b, api_key=_k, model=_m,
                                   max_tokens=max_tokens)
        _count((raw.get("usage") if isinstance(raw, dict) else None))
        return raw
    lang = {"es": "Spanish", "en": "English"}.get(
        CONFIG.get("APP", "language", fallback="en"), "English")
    try:
        local = bool(fed_tools._is_local_only(job.get("project") or ""))
    except Exception:
        local = False
    if local and not job.get("remote_ok"):
        import hoplita_desktop as _hd
        from urllib.parse import urlparse as _up
        _extra = []
        if CONFIG and CONFIG.has_option("HANDS", "local_hosts"):
            _extra = [h for h in re.split(r"[,\s]+", CONFIG.get("HANDS", "local_hosts", fallback="")) if h]
        if not _hd.endpoint_is_local(_b, extra_hosts=_extra):
            job["gate"] = {"model": _m, "host": _up(_b if "://" in _b else "http://" + _b).hostname or _b}
            raise _wbj.JobError("remote_model")
    is_blocked, allowed = _turn_tool_policy(tab_blocked=job.get("blocked") or (),
                                            local_only=local)
    allowed = set(allowed if allowed is not None else _allowed_tools())

    _jail = os.path.realpath(root)
    job["trace"] = []

    def _inside(p_):
        p_ = os.path.expanduser(str(p_ or "").strip()) or _jail
        if not os.path.isabs(p_):
            p_ = os.path.join(_jail, p_)
        rp = os.path.realpath(p_)
        return rp if rp == _jail or rp.startswith(_jail.rstrip(os.sep) + os.sep) else None

    def _exec(call_id, name, args):
        args = dict(args or {})
        props = (TOOL_PARAMS.get(name) or {}).get("properties") or {}
        where, outside = "", None
        for key in ("path", "project_path"):
            if key in props:
                j_ = _inside(args.get(key))
                if j_ is None:
                    outside = str(args.get(key))
                    break
                args[key] = j_
                where = where or j_
        if outside is None and isinstance(args.get("files"), list):
            outside = next((str(f) for f in args["files"] if _inside(f) is None), None)
        with _acc_lock:
            if len(job["trace"]) < 1000:
                job["trace"].append({"tool": name, "path": outside or where,
                                     "refused": outside is not None})
        if outside is not None:
            return json.dumps({"error": f"outside this project: {outside}. "
                                        f"A whiteboard reads only {root}."}, ensure_ascii=False)
        return fed_agent._execute_tool(name, args)

    def _on_event(ev):
        if ev.get("event") == "tool_result":
            refused = "is not available to this agent" in str(ev.get("result_preview") or "")
            with _acc_lock:
                _tally = job["usage"].setdefault("refused" if refused else "tools", {})
                _tally[ev.get("tool") or "?"] = _tally.get(ev.get("tool") or "?", 0) + 1

    def _agent(prompt, tools, max_iter, label):
        res = fed_agent.run_agentic_turn(
            user_message=prompt, on_event=_on_event, llm_chat=_chat,
            cancel=job["cancel"].is_set, turn_id=f"wb-draw-{job['id']}-{label}",
            max_iterations=max_iter, is_blocked=is_blocked,
            allowed_tools=set(tools) & allowed, qa=False,
            delegate=_exec, delegate_names=("*",))
        return res.get("reply") or ""

    def _json(prompt, system=None):
        return _llm_call_json(prompt, system=system, endpoint=(_b, _k, _m),
                              usage_sink=_count, breaker=False, quiet=True,
                              timeout=CONFIG.getint("CHAT", "timeout", fallback=300))

    def _progress(stage, **args):
        job["stage"] = dict(args, key=stage)

    _focus_keys = {"planning": "wb.focus_planning", "exploring": "wb.focus_exploring",
                   "writing": "wb.focus_writing", "checking": "wb.focus_checking"}

    def _focus(stage, **args):
        from hoplita_i18n import t as _t
        return _t(_focus_keys.get(stage, "wb.focus_writing"), **args)

    ctx = _wbp.Ctx(_json, _agent, cancelled=job["cancel"].is_set, progress=_progress,
                   lang=lang, focus=_focus,
                   parallel=max(1, min(3, CONFIG.getint("CHAT", "multi_max_parallel", fallback=2))))
    try:
        if job["what"] == "update":
            _wbp.update(job, ctx, info)
        elif job["what"] == "redraw":
            _wbp.redraw(job, ctx)
        else:
            _wbp.draw(job, ctx, project_of=_wbt._project_of)
    except _wbp.PipelineError as e:
        raise _wbj.JobError(str(e))
    res = {"reply": ""}
    if job["what"] in ("changes", "commit", "overview") and job.get("whiteboardId") \
            and not job["cancel"].is_set():
        try:
            import wb_store as _wbst
            if not (_wbst.store().snapshot(job["whiteboardId"]).get("document") or []):
                job["failed"] = True
                job["error"] = "empty"
                _wbst.store().command({"type": "delete", "reviewId": job["whiteboardId"]})
        except Exception:
            pass
    if job["what"] == "update" and not job["cancel"].is_set():
        try:
            import wb_store as _wbst
            if _wbst.store().staleness(job["whiteboardId"]).get("stale"):
                job["failed"] = True
                job["error"] = job.get("error") or "still_behind"
        except Exception:
            pass
    u = job["usage"]
    return f"{u['calls']} calls, {job.get('explored', 0)} parts explored" + \
        (" — followed the code without a model" if job.get("modelFree") else "")


def register_tool_params():
    global TOOL_PARAMS, TOOLS
    TOOL_PARAMS = {
        "plan_and_orchestrate":   {"properties": {"task": {"type": "string"}}, "required": ["task"]},
        "call_council":           {"properties": {"topic": {"type": "string", "description": "The matter, stated so it stands on its own: nobody sitting down has read your conversation."}, "members": {"type": "array", "items": {"type": "string"}, "description": "Pool aliases that take a seat. Empty = every pool model that sits on councils. 'base' is the model you are running on."}, "first": {"type": "string", "description": "Who answers first — the one the others examine. Empty = the first of the list."}, "context": {"type": "string", "description": "What they need that does not belong in the statement of the matter: what you already tried, what came back, the constraints."}}, "required": ["topic"]},
        "search_web":             {"properties": {"query": {"type": "string"}}, "required": ["query"]},
        "run_command":            {"properties": {"command": {"type": "string"}, "cwd": {"type": "string"}, "timeout": {"type": "integer", "description": "Seconds to wait. Raise it well above the default for installs, builds, migrations and acceptance scripts — 300 or more."}}, "required": ["command"]},
        "render_html":            {"properties": {"html": {"type": "string", "description": "HTML/CSS/JS/SVG to render. Chart.js 4 available; D3/Plotly/three.js via CDN. <body> fragment or complete document."}, "append": {"type": "boolean", "description": "true = ADD this chunk to what is already on the canvas. Use it to build a LARGE document in 2-4 calls: a single huge call gets truncated by the output token cap and the server rejects it."}}, "required": ["html"]},
        "see_image":              {"properties": {"path": {"type": "string", "description": "the image file"}, "max_width": {"type": "integer", "description": "downscale width in px (default 1024)"}}, "required": ["path"]},
        "canvas_see":             {"properties": {"max_width": {"type": "integer", "description": "Screenshot width in px (default 1024). Smaller = fewer tokens."}}, "required": []},
        "canvas_eval":            {"properties": {"js": {"type": "string", "description": "JavaScript to run in the live page. The value of the last expression comes back (must be JSON-serializable)."}}, "required": ["js"]},
        "dashboard_render":       {"properties": {"html": {"type": "string", "description": "HTML+JS/SVG. Chart.js 4 already available. Alias of render_html."}, "append": {"type": "boolean", "description": "true = ADD this chunk to what is already on the canvas, to build a LARGE document across 2-4 calls."}}, "required": ["html"]},
        "read_file":              {"properties": {"path": {"type": "string"}}, "required": ["path"]},
        "list_dir":               {"properties": {"path": {"type": "string"}, "recursive": {"type": "boolean"}}},
        "list_experience":        {"properties": {"limit": {"type": "integer", "description": "How many, newest first (default 100)."}}},
        "search_experience":      {"properties": {"query": {"type": "string"}, "n_results": {"type": "integer"}}, "required": ["query"]},
        "search_lessons":         {"properties": {"query": {"type": "string"}, "n_results": {"type": "integer"},
                                                  "min_confidence": {"type": "number"},
                                                  "exclude_stale": {"type": "boolean"}}, "required": ["query"]},
        "list_lessons":           {"properties": {"include_stale": {"type": "boolean"}}, "required": []},
        "add_lesson":             {"properties": {"text": {"type": "string"},
                                                  "tags": {"type": "array", "items": {"type": "string"}},
                                                  "confidence": {"type": "number"}}, "required": ["text"]},
        "deactivate_lesson":      {"properties": {"id": {"type": "string"}}, "required": ["id"]},
        "delete_lesson":          {"properties": {"id": {"type": "string"}}, "required": ["id"]},
        "list_facts":             {"properties": {}, "required": []},
        "add_fact":               {"properties": {"text": {"type": "string", "description": "The fact in a clear sentence, e.g. 'The cat is called Pixel'"},
                                                  "kind": {"type": "string", "enum": ["durable", "intencion", "estado"], "description": "durable = true from now on (a name, a relationship, a machine). intencion = something they mean to do, which stops being true once done or dropped. estado = how things are right now, which changes. Default durable."},
                                                  "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["text"]},
        "session_moments":        {"properties": {"query": {"type": "string"},
                                                  "limit": {"type": "integer"},
                                                  "window_min": {"type": "integer", "description": "Minutes of silence that end a moment. 5 by default."}},
                                   "required": ["query"]},
        "session_search":         {"properties": {"query": {"type": "string"},
                                                  "limit": {"type": "integer"},
                                                  "session": {"type": "string"},
                                                  "context": {"type": "integer", "description": "How many messages before/after each hit. 0 = just the hit."},
                                                  "include_machinery": {"type": "boolean", "description": "Also search what the TOOLS did (each turn's record: tool, args and the head of its result) and the summaries compactions produced. Off by default so a search for a word does not come back with four hundred lines of a file dump. Turn it on when you are looking for exactly that: 'what command did I run', 'what did that scrape return', 'what got compacted away'."}},
                                   "required": ["query"]},
        "propose_memory_change":  {"properties": {"target_id": {"type": "string"},
                                                  "action": {"type": "string", "enum": ["retire", "replace", "prune"]},
                                                  "new_text": {"type": "string"},
                                                  "why": {"type": "string"},
                                                  "quotes": {"type": "array", "items": {"type": "object"},
                                                             "description": "Literal lines from the log that support this. Without them the proposal is refused."}},
                                   "required": ["target_id", "action", "quotes"]},
        "recall":                 {"properties": {"query": {"type": "string"},
                                                  "terms": {"type": "array", "items": {"type": "string"},
                                                            "description": "Words the stored record would likely contain: synonyms, the category word, brands, proper names. Widens a word search that the question's own phrasing would miss."},
                                                  "n_results": {"type": "integer"}},
                                   "required": ["query"]},
        "search_facts":           {"properties": {"query": {"type": "string"},
                                                  "n_results": {"type": "integer"}},
                                   "required": ["query"]},
        "delete_fact":            {"properties": {"id": {"type": "string", "description": "The id of the fact to delete (obtained from search_facts)"}}, "required": ["id"]},
        "edit_fact":              {"properties": {"id": {"type": "string", "description": "The id of the fact to edit (obtained from search_facts)"},
                                                  "text": {"type": "string", "description": "New text of the fact"},
                                                  "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["id"]},
        "search_notes":           {"properties": {"search_term": {"type": "string"}, "category": {"type": "string"}}, "required": ["search_term"]},
        "use_skill":              {"properties": {"search_term": {"type": "string"}, "category": {"type": "string"}}, "required": ["search_term"]},
        "skill_folder":           {"properties": {"category": {"type": "string", "description": "Folder to mark or unmark. Omit to only ask which ones are skills."}, "on": {"type": "boolean", "description": "true = it is skills (default); false = take it out."}}, "required": []},
        "list_categories":        {"properties": {}},
        "add_note":               {"properties": {"category": {"type": "string"}, "title": {"type": "string"}, "content": {"type": "string"}}, "required": ["category", "title", "content"]},
        "delete_note":            {"properties": {"category": {"type": "string"}, "note_id": {"type": "string"}}, "required": ["category", "note_id"]},
        "update_note":            {"properties": {"category": {"type": "string"}, "note_id": {"type": "string"}, "title": {"type": "string"}, "content": {"type": "string"}, "changes": {"type": "array", "items": {"type": "object", "properties": {"old": {"type": "string"}, "new": {"type": "string"}}, "required": ["old", "new"]}, "description": "Exact-match edits applied in order; each 'old' must occur exactly once."}}, "required": ["category", "note_id"]},
        "get_credential":         {"properties": {"site": {"type": "string"}}, "required": ["site"]},
        "get_card":               {"properties": {"site": {"type": "string"}}, "required": ["site"]},
        "get_api_key":            {"properties": {"site": {"type": "string"}}, "required": ["site"]},
        "mail_folders":           {"properties": {"account": {"type": "string", "description": "Credential name of kind 'mail'; optional with one mailbox."}},
                                   "required": []},
        "mail_list":              {"properties": {"account": {"type": "string", "description": "Credential name of kind 'mail'; optional with one mailbox."},
                                                  "folder": {"type": "string", "description": "Folder name or role (inbox, junk, trash, sent, archive). Default INBOX."},
                                                  "limit": {"type": "integer", "description": "How many, newest first (max 100). Default 25."},
                                                  "unseen": {"type": "boolean", "description": "Only unread messages."},
                                                  "since": {"type": "string", "description": "YYYY-MM-DD, on or after."},
                                                  "before": {"type": "string", "description": "YYYY-MM-DD, before."},
                                                  "from": {"type": "string", "description": "Sender contains."},
                                                  "to": {"type": "string", "description": "Recipient contains."},
                                                  "subject": {"type": "string", "description": "Subject contains."},
                                                  "text": {"type": "string", "description": "Headers or body contain."}},
                                   "required": []},
        "mail_read":              {"properties": {"account": {"type": "string", "description": "Credential name of kind 'mail'; optional with one mailbox."},
                                                  "uid": {"type": "integer", "description": "The uid mail_list gives."},
                                                  "folder": {"type": "string", "description": "The folder the uid belongs to. Default INBOX."},
                                                  "max_chars": {"type": "integer", "description": "Text cap. Default 20000."}},
                                   "required": ["uid"]},
        "mail_move":              {"properties": {"account": {"type": "string", "description": "Credential name of kind 'mail'; optional with one mailbox."},
                                                  "uids": {"type": "array", "items": {"type": "integer"}, "description": "uids from mail_list, of 'folder'."},
                                                  "to": {"type": "string", "description": "Destination: a folder name or a role (junk, trash, inbox, archive)."},
                                                  "folder": {"type": "string", "description": "Where the uids are now. Default INBOX."}},
                                   "required": ["uids", "to"]},
        "mail_send":              {"properties": {"account": {"type": "string", "description": "Credential name of kind 'mail'; optional with one mailbox."},
                                                  "to": {"type": "array", "items": {"type": "string"}, "description": "Recipients ('a@b.c' or 'Name <a@b.c>'). Optional with reply_to_uid."},
                                                  "subject": {"type": "string", "description": "One line. Optional with reply_to_uid ('Re: ' + the original)."},
                                                  "body": {"type": "string", "description": "The message, plain text."},
                                                  "cc": {"type": "array", "items": {"type": "string"}},
                                                  "bcc": {"type": "array", "items": {"type": "string"}},
                                                  "html": {"type": "string", "description": "An HTML version of the body, optional."},
                                                  "attachments": {"type": "array", "items": {"type": "string"}, "description": "Paths of files to attach."},
                                                  "reply_to_uid": {"type": "integer", "description": "uid (from mail_list) of the message this answers."},
                                                  "folder": {"type": "string", "description": "Folder of reply_to_uid. Default INBOX."},
                                                  "from_name": {"type": "string", "description": "Display name for the sender."},
                                                  "save_sent": {"type": "boolean", "description": "Keep a copy in Sent. Default true."}},
                                   "required": ["body"]},
        "get_otp":                {"properties": {"site": {"type": "string"}}, "required": ["site"]},
        "list_credentials":       {"properties": {}},
        "fill_credentials":       {"properties": {"site": {"type": "string"}}, "required": ["site"]},
        "get_todo_context":       {"properties": {}},
        "add_appointment":        {"properties": {"date": {"type": "string", "description": "'YYYY-MM-DD HH:MM', or 'YYYY-MM-DD' for a whole day. Nothing else is accepted."}, "title": {"type": "string"}, "description": {"type": "string"}, "urgency": {"type": "string"}, "project": {"type": "string", "description": "Optional TODO project. 'general' if not specified."}}, "required": ["date", "title", "description"]},
        "add_task":               {"properties": {"title": {"type": "string"}, "description": {"type": "string"}, "priority": {"type": "string"}, "progress": {"type": "integer"}, "project": {"type": "string", "description": "Optional TODO project. 'general' if not specified."}, "depends_on": {"type": "array", "items": {"type": "string"}, "description": "Titles of tasks in the same project this task depends on."}}, "required": ["title", "description"]},
        "update_task_progress":   {"properties": {"task_title": {"type": "string"}, "new_progress": {"type": "integer"}}, "required": ["task_title", "new_progress"]},
        "task_depends":           {"properties": {"task_title": {"type": "string"}, "depends_on": {"type": "array", "items": {"type": "string"}}, "project": {"type": "string"}}, "required": ["task_title", "depends_on"]},
        "list_appointments":      {"properties": {"project": {"type": "string", "description": "Optional: filter by project."}}, "required": []},
        "delete_appointment":     {"properties": {"id": {"type": "string", "description": "The appointment's id from list_appointments: deletes that one."}, "title": {"type": "string", "description": "Exact title, when no id is given."}, "date": {"type": "string", "description": "Optional, with title: 'YYYY-MM-DD HH:MM' (that moment) or 'YYYY-MM-DD' (that day)."}}, "required": []},
        "delete_all_appointments": {"properties": {"contains": {"type": "string", "description": "Title substring (optional)."}, "date": {"type": "string", "description": "Optional: 'YYYY-MM-DD HH:MM' (that moment) or 'YYYY-MM-DD' (that day)."}, "project": {"type": "string", "description": "Project (optional)."}, "confirm": {"type": "boolean", "description": "Mandatory true if you provide no filter (deletes everything)."}}, "required": []},
        "list_todo_projects":     {"properties": {}},
        "add_todo_project":       {"properties": {"name": {"type": "string"}}, "required": ["name"]},
        "delete_todo_project":    {"properties": {"name": {"type": "string"}}, "required": ["name"]},
        "list_cron":              {"properties": {}},
        "add_cron":               {"properties": {"name": {"type": "string"}, "instruction": {"type": "string"}, "schedule_type": {"type": "string"}, "interval_minutes": {"type": "integer"}, "fixed_datetime": {"type": "string"}, "daily_times": {"type": "array", "items": {"type": "string"}}, "target": {"type": "string", "description": "local | child:<handle> | friend:<handle>. A LOCAL child (child_list) is written the same way and is the most reliable target there is: a remote peer only runs the job when it happens to be online, a local child always does."}, "context_current": {"type": "boolean", "description": "true = attach THE CONVERSATION YOU ARE IN RIGHT NOW. This is the normal case: the user says «set up a cron with this conversation and follow it up yourself» and means the one you are both in. You do not need its title — you do not have it. Pair it with context_live=true when they ask you to keep working on it."}, "context_session": {"type": "string", "description": "ANOTHER conversation, by title or uuid, when they mean a different one than this. Leave empty and use context_current for the current one. Title or uuid of a saved conversation this job CONTINUES: each run loads the whole chat, follows the instruction as its next turn and writes its findings back into it."}, "context_live": {"type": "boolean", "description": "Default true: the job CONTINUES the attached conversation — every run reads the whole chat (compacted when it outgrows the window, like a tab), works, and appends its findings to it, so reopening the tab shows the whole watch. false = remember only a frozen summary taken at attach time, for a job that must never touch the conversation."}, "model": {"type": "string", "description": "The job's own model: empty = the router decides per run, 'base' = the [API] model pinned, or a pool alias."}, "own_tab": {"type": "boolean", "description": "true = the job gets a tab of its own: each run is a turn there, the person can talk to it between runs, and it runs in parallel. For standing workers (watch my mail, review PRs)."}}, "required": ["name", "instruction", "schedule_type"]},
        "load_tools":             {"properties": {"names": {"type": "array", "items": {"type": "string"}, "description": "Exact tool names from the catalogue."}}, "required": ["names"]},
        "pdf_info":               {"properties": {"source": {"type": "string", "description": "Path to the PDF."},
                                                  "password": {"type": "string", "description": "To open an encrypted source."},
                                                  "pages": {"type": "string", "description": "Which pages for text extraction, e.g. '1,3-5'. Default all."},
                                                  "text": {"type": "string", "description": "'full' = text per page · 'bottom'/'top' = only the edge band (where printed folios live). Omit for structure only."},
                                                  "render": {"type": "integer", "description": "Render THIS page (1-based) to a PNG file next to the source — to look at a scan, or paste into the chat."},
                                                  "dpi": {"type": "integer", "description": "Render resolution (default 120)."}},
                                   "required": ["source"]},
        "pdf":                    {"properties": {"source": {"type": "string", "description": "Path to the source PDF."},
                                                  "output": {"type": "string", "description": "Path for the resulting PDF."},
                                                  "password": {"type": "string", "description": "To open an encrypted source."},
                                                  "metadata": {"type": "object", "description": "Optional {Title, Author, Subject...} written into the result."},
                                                  "steps": {"type": "array", "items": {"type": "object"},
                                                            "description": "Pipeline, applied in order; each step is ONE {verb: {params}}. "
                                                            "split_two_up:{order:'left-first'|'right-first'|'auto'} splits scanned double pages into halves · "
                                                            "select:{pages:'10,13', numbering:'physical'|'printed', offset:int, auto:bool} keeps pages (printed+auto reads the inked folios to find the offset) · "
                                                            "merge:{source, position:'end'|'start'|index} · rotate:{pages, degrees} · "
                                                            "crop:{pages, box:[x0,y0,x1,y1]} or {pages, half:'left'|'right'|'top'|'bottom'} · "
                                                            "scale:{pages, factor} · n_up:{per_page:2} composes 2 pages per sheet · "
                                                            "stamp:{text, pages, size, rotate, color, opacity} · watermark:{text,...} (diagonal translucent defaults) · "
                                                            "page_numbers:{start, format:'{n}' or '{n}/{total}', position} · overlay_pdf:{source, pages} · "
                                                            "fill_form:{fields:{name:value}} · encrypt:{password, owner_password}"}},
                                   "required": ["source", "output", "steps"]},
        "office_info":            {"properties": {"source": {"type": "string", "description": "Path to the .docx/.xlsx/.pptx."},
                                                  "range": {"type": "string", "description": "docx: block indexes '10-40' · pptx: slides '2-5' · xlsx: cells 'A1:F30'. Default: the start."},
                                                  "sheet": {"type": "string", "description": "xlsx: which sheet (default the active one)."},
                                                  "find": {"type": "string", "description": "Where this text occurs: blocks, cells or slides+shapes."},
                                                  "match_case": {"type": "boolean", "description": "For find; default true."},
                                                  "view": {"type": "boolean", "description": "true = also render the file as HTML on the canvas."}},
                                   "required": ["source"]},
        "office":                 {"properties": {"source": {"type": "string", "description": "Path to the .docx/.xlsx/.pptx."},
                                                  "output": {"type": "string", "description": "Where to write the result; the source path itself to edit in place."},
                                                  "accept_loss": {"type": "boolean", "description": "Overwrite the source even if parts the library cannot carry would be dropped (they are named first)."},
                                                  "steps": {"type": "array", "items": {"type": "object"},
                                                            "description": "Applied in order; each step is ONE {verb: {params}}. "
                                                            "DOCX — replace:{find, replace, match_case} (body, tables, headers, footers) · set_paragraph:{index, text, style} · "
                                                            "insert_before / insert_after:{index, text, style} · append:{text, style} · delete:{index} (a paragraph or a table) · "
                                                            "set_cell:{table, row, col, text} · add_row:{table, values} · add_table:{rows:[[...]], after, style}. "
                                                            "Indexes are block numbers from office_info and refer to the document AS IT IS at that step. "
                                                            "XLSX — set:{sheet, cell, value} (a value starting with '=' is a formula) · set_range:{sheet, start, rows:[[...]]} · "
                                                            "insert_rows / delete_rows:{sheet, at, count} · insert_cols / delete_cols:{sheet, at:'C', count} · "
                                                            "add_sheet:{name, index} · rename_sheet:{sheet, name} · delete_sheet:{sheet} · replace:{find, replace, sheet} (text cells only). "
                                                            "Moving rows/columns does not rewrite formulas. "
                                                            "PPTX — replace:{find, replace, notes} · set_text:{slide, shape (id or name), text} (one paragraph per line) · "
                                                            "add_slide:{layout, title, body (text or list), position} · delete_slide:{slide} · move_slide:{slide, to} · notes:{slide, text}. "
                                                            "Slides are 1-based."}},
                                   "required": ["source", "output", "steps"]},
        "intent":                 {"properties": {"set": {"type": "array", "items": {"type": "string"}, "description": "Replace the whole list with these goals (short, 3-8 words each), in order."},
                                                  "add": {"type": "array", "items": {"type": "string"}, "description": "Append goals without touching the rest."},
                                                  "done": {"type": "array", "items": {"type": "integer"}, "description": "Ids achieved — cross them out."},
                                                  "failed": {"type": "array", "items": {"type": "integer"}, "description": "Ids that did NOT work — mark them and move on."},
                                                  "doing": {"type": "integer", "description": "The single id you are attacking right now."}},
                                   "required": []},
        "cron_set_context":       {"properties": {"job_id": {"type": "string"}, "session": {"type": "string", "description": "Title or uuid; empty detaches."}}, "required": ["job_id"]},
        "session_brief":          {"properties": {"session": {"type": "string", "description": "Title or uuid."}, "refresh": {"type": "boolean"}}, "required": ["session"]},
        "session_recall":         {"properties": {"session": {"type": "string"}, "query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["session"]},
        "delete_cron":            {"properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
        "toggle_cron":            {"properties": {"job_id": {"type": "string"}, "enabled": {"type": "boolean"}}, "required": ["job_id", "enabled"]},
        "update_cron":            {"properties": {"job_id": {"type": "string", "description": "Empty in a job's own tab = that tab's job."}, "instruction": {"type": "string", "description": "The new task, whole: it replaces the old one."}, "name": {"type": "string"}, "schedule_type": {"type": "string", "description": "interval | daily | weekly | fixed | startup"}, "interval_minutes": {"type": "integer"}, "daily_times": {"type": "array", "items": {"type": "string"}}, "weekdays": {"type": "array", "items": {"type": "integer"}}, "fixed_datetime": {"type": "string"}, "model": {"type": "string"}, "enabled": {"type": "boolean"}}},
        "delete_current_cron_job":{"properties": {}},
        "set_cron_context":       {"properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
        "disable_current_cron_job":{"properties": {}},
        "alert":                  {"properties": {"title": {"type": "string"}, "message": {"type": "string"}}, "required": ["title", "message"]},
        "get_agentic_config":     {"properties": {}},
        "see_my_config":          {"properties": {"section": {"type": "string", "description": "One section, e.g. 'MCP_SERVERS'. Omit for the map of every section and its keys."}}, "required": []},
        "edit_my_config":         {"properties": {"section": {"type": "string", "description": "Section name without brackets, e.g. 'MCP_SERVERS' or 'MODEL:fast'. Created if missing."}, "key": {"type": "string", "description": "The key to set or remove."}, "value": {"type": "string", "description": "The new value (omit with delete=true)."}, "delete": {"type": "boolean", "description": "true removes the key."}, "changes": {"type": "array", "items": {"type": "object", "properties": {"section": {"type": "string"}, "key": {"type": "string"}, "value": {"type": "string"}, "delete": {"type": "boolean"}}, "required": ["section", "key"]}, "description": "Several changes applied together under one approval, in order. Use instead of section/key/value."}}, "required": []},
        "read_my_docs":           {"properties": {"query": {"type": "string", "description": "Words to look for, e.g. 'MCP_SERVERS stdio'."}, "section": {"type": "string", "description": "A section title to read whole, as the outline or a search gave it."}, "file": {"type": "string", "description": "Narrow to one file, e.g. 'CONFIG.md'."}}, "required": []},
        "screenshot_web":         {"properties": {"url": {"type": "string"}, "headless": {"type": "boolean"}, "output_path": {"type": "string"}}, "required": ["url"]},
        "browse_web":             {"properties": {"task": {"type": "string"}, "url": {"type": "string"}, "headless": {"type": "boolean"}, "max_steps": {"type": "integer"}, "endpoint": {"type": "object", "description": "Which LLM drives the browser when [BROWSER] has no model: {base_url, api_key, model}. Faces attach their tab's pin; sub-agents inherit their own."}}, "required": ["task"]},
        "scrape_url":             {"properties": {"url": {"type": "string"}, "max_chars": {"type": "integer"}, "escalate": {"type": "boolean", "description": "Let it drive a real browser if scraping fails (default true)"}, "depth": {"type": "integer", "description": "0=this page only (default). 1-3=follow links on the SAME domain up to N levels, capped by max_pages"}}, "required": ["url"]},
        "search_rag":             {"properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}}, "required": ["query"]},
        "code_projects":          {"properties": {}},
        "code_project_name":      {"properties": {"path": {"type": "string"}}},
        "code_index":             {"properties": {"path": {"type": "string"}, "name": {"type": "string", "description": "The project's name, ONLY when the user gave one. Names its memory folder, its task project and what a peer sees. Default: the folder's basename."}}},
        "sticky_note":            {"properties": {"text": {"type": "string", "description": "The WHOLE note as it must now read, at most 15 lines. Empty clears it."}}, "required": ["text"]},
        "canvas_undo":            {"properties": {}, "required": []},
        "code_symbols":           {"properties": {"query": {"type": "string", "description": "The name to locate. Empty = the project's shape."}, "path": {"type": "string", "description": "Project root. Defaults to the open project."}, "limit": {"type": "integer"}}, "required": []},
        "code_read_smart":        {"properties": {"path": {"type": "string"}, "search": {"type": "string"}}, "required": ["path"]},
        "code_read_range":        {"properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, "required": ["path", "start_line", "end_line"]},
        "code_search":            {"properties": {"path": {"type": "string", "description": "Directory to search — there is no sensible default (the daemon's cwd is HOPLITA itself). In code mode: the open project's path."}, "query": {"type": "string", "description": "LITERAL text, matched case-insensitively as a substring. Not a regex: metacharacters are searched as plain characters."}, "file_pattern": {"type": "string"}}, "required": ["query", "path"]},
        "code_apply_patch":       {"properties": {"path": {"type": "string"}, "changes": {"type": "array", "items": {"type": "object"}}}, "required": ["path", "changes"]},
        "code_write_file":        {"properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
        "code_undo":              {"properties": {"path": {"type": "string", "description": "Any path inside the project. Defaults to the current directory."}, "steps": {"type": "integer", "description": "How many edits to undo, newest first. Default 1."}}},
        "code_propose_verify":    {"properties": {"path": {"type": "string", "description": "Any path inside the project."}}},
        "code_definition":        {"properties": {"path": {"type": "string"}, "symbol": {"type": "string"}, "project_path": {"type": "string"}}, "required": ["path", "symbol"]},
        "code_outline":           {"properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path"]},
        "code_diagnostics":       {"properties": {"path": {"type": "string"}, "content": {"type": "string", "description": "The buffer as it is now, if it differs from disk."}}, "required": ["path"]},
        "code_lsp_status":        {"properties": {"path": {"type": "string"}}},
        "code_toolchain":         {"properties": {"path": {"type": "string", "description": "Any path inside the project. Defaults to the current directory."}}},
        "code_checkpoints":       {"properties": {"path": {"type": "string", "description": "Any path inside the project. Defaults to the current directory."}}},
        "code_reject_hunk":       {"properties": {"path": {"type": "string"}, "file": {"type": "string", "description": "Path relative to the repo root."}, "hunk": {"type": "integer", "description": "Index of the hunk within that file's diff, from 0."}, "header": {"type": "string", "description": "The '@@ ...' line as shown, to detect that the diff moved."}, "patch": {"type": "string", "description": "The hunk EXACTLY as it was displayed. Preferred: the index alone means different hunks in different views, and it cannot express a hunk that git merged with the user's own edit."}}, "required": ["file", "hunk"]},
        "git_status":             {"properties": {"path": {"type": "string", "description": "Any path inside the project."}}},
        "git_diff":               {"properties": {"path": {"type": "string"},
                                                  "files": {"type": "array", "items": {"type": "string"}, "description": "Limit the diff to these paths."},
                                                  "staged": {"type": "boolean", "description": "Diff the index instead of the working tree."},
                                                  "since_checkpoint": {"type": "boolean", "description": "Compare against the snapshot before YOUR first edit this session."},
                                                  "stat_only": {"type": "boolean", "description": "Summary of files and line counts instead of the full patch."},
                                                  "commit": {"type": "string", "description": "Compare the tree as it is NOW against this commit."}}},
        "git_log":                {"properties": {"path": {"type": "string"},
                                                  "limit": {"type": "integer", "description": "How many commits (default 15, max 100)."},
                                                  "files": {"type": "array", "items": {"type": "string"}, "description": "Only commits touching these paths."}}},
        "git_show":               {"properties": {"path": {"type": "string"},
                                                  "sha": {"type": "string", "description": "The commit to show (from git_log)."}},
                                   "required": ["sha"]},
        "git_switch":             {"properties": {"path": {"type": "string"}, "name": {"type": "string", "description": "An existing branch to go to (e.g. main)."}}, "required": ["name"]},
        "git_merge":              {"properties": {"path": {"type": "string"}, "branch": {"type": "string", "description": "The work branch to merge."}, "into": {"type": "string", "description": "The target branch. Default main."}, "delete_branch": {"type": "boolean", "description": "Delete the work branch after a successful merge. Default false."}}, "required": ["branch"]},
        "git_branch":             {"properties": {"path": {"type": "string"},
                                                  "name": {"type": "string", "description": "Create this branch and switch to it. Omit to just list."},
                                                  "base": {"type": "string", "description": "Start point for the new branch."}}},
        "git_commit":             {"properties": {"path": {"type": "string"},
                                                  "message": {"type": "string"},
                                                  "files": {"type": "array", "items": {"type": "string"}, "description": "Only these paths. Default: everything changed."},
                                                  "allow_main": {"type": "boolean", "description": "Permit committing on main/master. Only when the user asked."}}, "required": ["message"]},
        "project_journal":        {"properties": {"path": {"type": "string", "description": "The open project's root path (its folder name is the project's notes folder)."},
                                                  "entry": {"type": "string", "description": "What to record: a decision, a trap, state worth keeping across sessions."},
                                                  "title": {"type": "string", "description": "Short header for the entry; default is its first line."}},
                                   "required": ["path", "entry"]},
        "project_write":          {"properties": {"path": {"type": "string", "description": "The open project's directory (or its folder name)."}, "note": {"type": "string", "enum": ["spec", "future", "journal"]}, "text": {"type": "string", "description": "spec: one short truth (≤600 chars). future: one line per pending item. journal: the entry."}, "replace": {"type": "boolean", "description": "Rewrite the whole note instead of appending (default false)."}}, "required": ["path", "note", "text"]},
        "search_working_memory":  {"properties": {"query": {"type": "string", "description": "Words to look for (ranked by how many of the rarer ones a passage covers), or a pattern with regex=true."}, "title": {"type": "string", "description": "One stored text (its title, or part of it); empty searches all of this conversation's."}, "regex": {"type": "boolean"}, "context": {"type": "integer", "description": "Paragraphs before and after each hit (0-5, default 1)."}, "k": {"type": "integer", "description": "How many passages (default 5)."}}, "required": ["query"]},
        "read_note":              {"properties": {"category": {"type": "string", "description": "The notes folder."},
                                                  "title": {"type": "string", "description": "The note's title (exact or unique substring)."},
                                                  "note_id": {"type": "string", "description": "Exact id, when you have it (wins over title)."},
                                                  "start_line": {"type": "integer", "description": "First line to return (1-based). Omit for the beginning."},
                                                  "end_line": {"type": "integer", "description": "Last line to return. Omit for as much as fits; the answer says the next call."}},
                                   "required": ["category"]},
        "code_lint":              {"properties": {"path": {"type": "string"}}, "required": ["path"]},
        "code_deps":              {"properties": {"path": {"type": "string"}}, "required": ["path"]},
        "code_references":        {"properties": {"path": {"type": "string"}, "symbol": {"type": "string"}, "project_path": {"type": "string"}}, "required": ["path", "symbol"]},
        "generate_pdf":           {"properties": {"content": {"type": "string"}, "title": {"type": "string"}, "output_path": {"type": "string"}}, "required": ["content"]},
        "call_api":               {"properties": {"auth_from": {"type": "string", "description": "Site saved as kind 'apikey'; the key is read from the vault instead of being passed here."}, "url": {"type": "string"}, "method": {"type": "string"}, "headers": {"type": "object"},
                                                  "body": {}, "auth_type": {"type": "string"}, "auth_token": {"type": "string"},
                                                  "auth_key_name": {"type": "string"}, "auth_key_value": {"type": "string"},
                                                  "query_params": {"type": "object"}, "timeout": {"type": "integer"},
                                                  "save_response": {"type": "string"}}, "required": ["url"]},
        "read_folder":            {"properties": {"path": {"type": "string"}, "extensions": {"type": "array", "items": {"type": "string"}}}, "required": ["path"]},
    }

    try:
        import fed_tools
        TOOLS.update(fed_tools.FED_TOOLS)
        TOOL_PARAMS.update(fed_tools.FED_TOOL_PARAMS)
    except Exception as _e:
        print(f"[MCP] ⚠ Federation tools not loaded: {_e}")

    TOOLS.update({
        "chat_session_save":   {"desc": "Save/overwrite a chat session (local tab) to the encrypted store.",
                                "fn": lambda a: tool_chat_session_save(a)},
        "chat_session_list":   {"desc": "List saved chat sessions (metadata, newest first).",
                                "fn": lambda a: tool_chat_session_list(a)},
        "chat_session_load":   {"desc": "Load a full chat session (with history) by uuid.",
                                "fn": lambda a: tool_chat_session_load(a)},
        "chat_session_delete": {"desc": "Delete a chat session (tombstone, syncs).",
                                "fn": lambda a: tool_chat_session_delete(a)},
        "working_memory_forget": {"desc": "Drop what a conversation left in a "
                                  "folder: its WORKING MEMORY (the full texts "
                                  "of big tool results it digested). "
                                  "Called when a chat is reset or a session "
                                  "deleted — that material is no more use than "
                                  "the conversation it belonged to. Pass "
                                  "orphans=true to also take the ones that "
                                  "belong to no conversation at all, which no "
                                  "reset can ever name.",
                                  "fn": lambda a: {"ok": True, "dropped":
                                                   wm_forget(
                                                       a.get("sessions")
                                                       or a.get("session", ""),
                                                       bool(a.get("orphans")))}},
        "generate_image": {"desc": "MAKE a picture from a description with the configured picture server ([IMAGE]: any server speaking the OpenAI Images API). The picture is saved to the media folder and SHOWN on the canvas; you get its path, never its bytes. Compose with render_html (<img src=file://…> plus SVG/CSS around it) and save the canvas when it is a piece worth keeping; see_image(path) if you need to look at it yourself.",
                          "fn": lambda a: tool_generate_image(a.get("prompt", ""), a.get("size"), a.get("n", 1), a.get("refs"))},
        "edit_image":     {"desc": "CHANGE a picture following instructions: refs are the source pictures — local image files, or 'attached' for the pictures the user pasted last in this conversation — and the prompt says what to do (restyle, remove, add, combine several into one). WHICH picture: when the person rejects a result (\"that was not it\", \"do it again\"), start over from 'attached'; when they build on it (\"now also…\", \"keep going\"), pass the path of the LAST result. Same server, same result shape as generate_image: saved to the media folder, shown on the canvas, path back. You have not seen the result until you see_image it: do not describe it before.",
                          "fn": lambda a: tool_edit_image(a.get("prompt", ""), a.get("refs"), a.get("size"), a.get("n", 1))},
        "canvas_save":   {"desc": "Save/overwrite a canvas snapshot (render_html output) to the encrypted store.",
                          "fn": lambda a: tool_canvas_save(a)},
        "canvas_list":   {"desc": "List saved canvases (metadata, newest first).",
                          "fn": lambda a: tool_canvas_list(a)},
        "canvas_load":   {"desc": "Load a full canvas (with html) by uuid.",
                          "fn": lambda a: tool_canvas_load(a)},
        "canvas_delete": {"desc": "Delete a canvas (tombstone, syncs).",
                          "fn": lambda a: tool_canvas_delete(a)},
    })
    TOOL_PARAMS.update({
        "chat_session_save":   {"properties": {"session": {"type": "object"}}, "required": ["session"]},
        "chat_session_list":   {"properties": {}},
        "chat_session_load":   {"properties": {"uuid": {"type": "string"}}, "required": ["uuid"]},
        "chat_session_delete": {"properties": {"uuid": {"type": "string"}}, "required": ["uuid"]},
        "working_memory_forget": {"properties": {
            "session": {"type": "string",
                        "description": "One id the conversation answers to."},
            "sessions": {"type": "array", "items": {"type": "string"},
                         "description": "EVERY id it answers to — its saved "
                         "uuid and its tab id. A tab saved half way through "
                         "wrote notes under both, and asking for one strands "
                         "the other half."},
            "orphans": {"type": "boolean",
                        "description": "Also drop what belongs to no "
                        "conversation: the untagged notes the old tool_spill "
                        "bucket left behind, which nothing else can reach."}}},
        "generate_image": {"properties": {"prompt": {"type": "string", "description": "What the picture shows, in words the picture model understands (subject, style, light, composition)."}, "size": {"type": "string", "description": "WIDTHxHEIGHT, e.g. 1024x1024 (default from [IMAGE] size)."}, "n": {"type": "integer", "description": "How many variants, 1-4."}, "refs": {"type": "array", "items": {"type": "string"}, "description": "Optional reference pictures (file paths, or 'attached'): with refs this is an edit."}}, "required": ["prompt"]},
        "edit_image":     {"properties": {"prompt": {"type": "string", "description": "The change to make."}, "refs": {"type": "array", "items": {"type": "string"}, "description": "Source pictures: local image file paths, or 'attached' for the ones the user pasted last in this conversation (up to 16)."}, "size": {"type": "string"}, "n": {"type": "integer"}}, "required": ["prompt", "refs"]},
        "canvas_save":   {"properties": {"canvas": {"type": "object"}}, "required": ["canvas"]},
        "canvas_list":   {"properties": {}},
        "canvas_load":   {"properties": {"uuid": {"type": "string"}}, "required": ["uuid"]},
        "canvas_delete": {"properties": {"uuid": {"type": "string"}}, "required": ["uuid"]},
    })
    try:
        from tool_families import tool_category as _tc
        for _n, _tool in TOOLS.items():
            if _n.startswith(("code_", "git_")):
                continue
            _props = (TOOL_PARAMS.get(_n) or {}).get("properties") or {}
            if _tc(_n) != "system":
                continue
            if _n == "run_command":
                _tool["fn"] = _project_scoped(_tool["fn"], keys=("cwd",))
            elif "path" in _props:
                _tool["fn"] = _project_scoped(_tool["fn"], keys=("path",))
    except Exception as _e:
        print(f"[TOOLS] project scoping of file tools skipped: {_e}")



_ext_mcp_procs: dict = {}
_ext_mcp_urls: dict = {}
_ext_mcp_keys: dict = {}
_ext_mcp_lock = threading.Lock()
_ext_mcp_id = 100


def _jsonrpc_request(method, params=None):
    global _ext_mcp_id
    _ext_mcp_id += 1
    msg = {"jsonrpc": "2.0", "id": _ext_mcp_id, "method": method}
    if params:
        msg["params"] = params
    return msg


def _stdio_send_recv(proc, msg, timeout=30):
    try:
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
    except (BrokenPipeError, OSError) as e:
        print(f"[MCP-EXT] stdio write failed (proc died?): {e}")
        return None

    if not hasattr(proc, "_stdout_queue"):
        import queue as _queue
        proc._stdout_queue = _queue.Queue()

        def _reader(p, q):
            try:
                for line in iter(p.stdout.readline, ''):
                    q.put(line)
            except Exception:
                pass
            q.put(None)

        t = threading.Thread(target=_reader, args=(proc, proc._stdout_queue), daemon=True)
        t.start()

    import queue as _queue
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            line = proc._stdout_queue.get(timeout=min(deadline - time.time(), 1.0))
        except _queue.Empty:
            continue
        if line is None:
            print(f"[MCP-EXT] stdio EOF (subprocess ended)")
            return None
        line = line.strip()
        if not line:
            continue
        if not line.startswith("{"):
            print(f"[MCP-EXT] stdio skip: {line[:100]}")
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            print(f"[MCP-EXT] stdio bad JSON: {line[:100]}")
            continue
    print(f"[MCP-EXT] stdio timeout ({timeout}s)")
    return None


def _sse_call(url, api_key, method, params=None, timeout=30):
    msg = _jsonrpc_request(method, params)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        r = requests.post(url, json=msg, headers=headers, timeout=timeout)
        if r.status_code == 200:
            return r.json()
        print(f"[MCP-EXT] SSE HTTP {r.status_code}: {r.text[:200]}")
        return None
    except Exception as e:
        print(f"[MCP-EXT] SSE error: {e}")
        return None


def _ensure_venv_in_path():
    in_venv = (hasattr(sys, "real_prefix") or
               sys.base_prefix != sys.prefix or
               os.environ.get("VIRTUAL_ENV"))
    if not in_venv:
        return
    if platform.system() == "Windows":
        scripts_dir = os.path.join(sys.prefix, "Scripts")
    else:
        scripts_dir = os.path.join(sys.prefix, "bin")
    if os.path.isdir(scripts_dir):
        current_path = os.environ.get("PATH", "")
        if scripts_dir not in current_path.split(os.pathsep):
            os.environ["PATH"] = scripts_dir + os.pathsep + current_path
            print(f"[MCP] venv found → PATH += {scripts_dir}")
            uvx_path = os.path.join(scripts_dir,
                                     "uvx.exe" if platform.system() == "Windows" else "uvx")
            if os.path.isfile(uvx_path):
                print(f"[MCP] uvx found: {uvx_path}")
            else:
                print(f"[MCP] ⚠ uvx NOT found in {scripts_dir}. "
                      f"MCP_SERVERS that use 'uvx ...' will NOT work. "
                      f"Install with: {sys.executable} -m pip install uv")


_HANDS = None
_HANDS_CONSENT_TIMEOUT_S = 300


def _hands():
    global _HANDS
    if _HANDS is None:
        import hoplita_desktop
        _HANDS = hoplita_desktop.Hands(CONFIG)
    return _HANDS


def _hands_remote_gate(session, ctx):
    import hoplita_desktop as hd
    from urllib.parse import urlparse
    if session.consented:
        return None
    ep = getattr(ctx, "endpoint", None) or {}
    _b = _resolve_endpoint("API") if CONFIG else ("", "", "")
    base = ep.get("base_url") or _b[0] or ""
    model = ep.get("model") or _b[2] or ""
    _extra = []
    if CONFIG and CONFIG.has_option("HANDS", "local_hosts"):
        _extra = [h for h in re.split(r"[,\s]+",
                  CONFIG.get("HANDS", "local_hosts", fallback="")) if h]
    if hd.endpoint_is_local(base, extra_hosts=_extra):
        session.consented = True
        return None
    host = urlparse(base if "://" in base else "http://" + base).hostname or base
    policy = ((CONFIG.get("HANDS", "remote_model", fallback="ask") if CONFIG else "ask")
              or "ask").strip().lower()
    if policy == "allow":
        session.consented = True
        return None
    refusal = {"error": "the desktop hands would send the person's screen to a "
                        "model outside this machine",
               "model": model, "host": host,
               "hint": "work without the desktop, or tell the user: a model "
                       "on this machine or local network gets the screen; "
                       "for this one they must allow it ([HANDS] remote_model "
                       "= allow, or answer yes when asked)"}
    if policy == "deny":
        refusal["why"] = "[HANDS] remote_model = deny"
        return refusal
    if ctx is None or getattr(ctx, "unattended", False) \
            or getattr(ctx, "ack_mode", "") != "stream":
        refusal["why"] = "nobody can answer the consent question on this turn"
        return refusal
    ack_id = uuid.uuid4().hex[:12]
    waiter = _queue.Queue(maxsize=1)
    with _PLAN_ACKS_LOCK:
        _PLAN_ACKS[ack_id] = {"q": waiter, "subtasks": [], "mode": ctx.ack_mode,
                              "kind": "hands"}
    try:
        ctx.emit({"event": "hands_consent", "ack_id": ack_id,
                  "turn_id": ctx.turn_id, "model": model, "host": host})
        wait_s = (CONFIG.getint("HANDS", "consent_timeout",
                                fallback=_HANDS_CONSENT_TIMEOUT_S)
                  if CONFIG else _HANDS_CONSENT_TIMEOUT_S)
        deadline = time.time() + wait_s
        answer = None
        while time.time() < deadline:
            if ctx.cancel():
                refusal["why"] = "the turn was cancelled while asking"
                return refusal
            try:
                answer = waiter.get(timeout=1.0)
                break
            except _queue.Empty:
                continue
    finally:
        with _PLAN_ACKS_LOCK:
            _PLAN_ACKS.pop(ack_id, None)
    if answer is None:
        refusal["why"] = "the consent question timed out"
        return refusal
    if (answer.get("decision") or "") == "run":
        session.consented = True
        return None
    refusal["why"] = "declined by the user"
    return refusal


def _turn_model_sees(ctx):
    if CONFIG is None:
        return False
    import hoplita_llmcfg
    url, model = _turn_model(ctx)
    return hoplita_llmcfg.model_sees(CONFIG, url, model)


def _turn_model(ctx):
    ep = getattr(ctx, "endpoint", None) or {}
    if (ep.get("model") or "").strip():
        return (ep.get("base_url") or "").rstrip("/"), ep["model"].strip()
    b = _resolve_endpoint("API")
    return b[0], b[2]


def _hands_target(tool, a, session=None, elements=None):
    a = a or {}
    for k in ("element", "window"):
        if a.get(k):
            obj = (session.elements.get(a[k]) if session else None)
            if obj is not None and elements is not None and hasattr(elements, "label"):
                try:
                    lab = elements.label(obj)
                    if lab:
                        return lab
                except Exception:
                    pass
            return f"{k}={a[k]}"
    if a.get("keys"):
        return f"keys={a['keys']}"
    if a.get("x") is not None and a.get("y") is not None:
        return f"xy=({a['x']},{a['y']})"
    if tool == "hands_type":
        return f"{len(str(a.get('text') or ''))} chars"
    if tool == "hands_screenshot":
        return f"region={a['region']}" if a.get("region") else "screen"
    if tool == "hands_verify" and a.get("expect_text"):
        return f"expect={str(a['expect_text'])[:40]!r}"
    return ""


def _hands_op_timeout():
    try:
        return CONFIG.getint("HANDS", "op_timeout", fallback=45) if CONFIG else 45
    except Exception:
        return 45


def _run_hands_op(op, tool):
    box = {}
    done = threading.Event()

    def _work():
        try:
            box["r"] = op()
        except BaseException as e:
            box["e"] = e
        finally:
            done.set()

    th = threading.Thread(target=_work, name=f"hands-op-{tool}", daemon=True)
    th.start()
    if not done.wait(_hands_op_timeout()):
        raise TimeoutError(
            f"{tool} did not finish in {_hands_op_timeout()}s — the desktop's "
            "accessibility service is not answering (a window may be "
            "unresponsive). Try again, or name a specific window instead of "
            "scanning them all.")
    if "e" in box:
        raise box["e"]
    return box.get("r")


def _hands_call(tool, fn, a):
    import hoplita_desktop as hd
    H = _hands()
    if tool == "hands_status":
        return H.status()
    ctx = getattr(_ORCH, "ctx", None)
    turn_id = (getattr(ctx, "turn_id", "") or "").strip() or "call"
    emit = ctx.emit if ctx is not None else (lambda ev: None)
    session, err = hd.acquire(turn_id, label=_turn_conv() or "")
    if err:
        return err
    try:
        gate = _hands_remote_gate(session, ctx)
        if gate:
            return gate
        if not session.announced:
            session.announced = True
            emit({"event": "hands_begin", "turn_id": turn_id,
                  "session": hd.session_type(),
                  "own_pids": sorted(hd.own_pids())})
            print(f"[HANDS] turn {turn_id} takes the desktop "
                  f"(session={hd.session_type()})")
        waited = hd.wait_if_paused(session, cancel=getattr(ctx, "cancel", None))
        if waited:
            return waited
        sees = ctx is None or _turn_model_sees(ctx)
        if tool == "hands_screenshot" and not sees:
            _jm = _turn_model(ctx)[1]
            return {"error": f"this turn's model ({_jm}) is not declared vision-capable: "
                             "a capture would reach it as text it cannot look at",
                    "model": _jm,
                    "hint": "read the window instead: hands_tree lists its "
                            "elements and texts, hands_verify(expect_text=...) "
                            "checks what it shows; or run this turn on a model "
                            "declared with vision"}
        if tool == "hands_verify" and not sees:
            a = dict(a or {}, with_screenshot=False)
        with session.act_lock:
            try:
                res = _run_hands_op(lambda: fn(H, session, a or {}), tool)
            except KeyError as e:
                res = {"error": str(e.args[0] if e.args else e)}
            except TimeoutError as e:
                res = {"error": str(e), "hint": "the desktop did not answer in "
                       "time; [HANDS] op_timeout raises the limit"}
            except Exception as e:
                res = {"error": f"{tool} failed: {e}"}
        if tool == "hands_verify" and not sees and isinstance(res, dict):
            res["screenshot"] = "not attached: this turn's model is not declared vision-capable"
        target = _hands_target(tool, a, session=session, elements=H.elements)
        ok = not (isinstance(res, dict) and res.get("error"))
        verified = res.get("verified") if isinstance(res, dict) else None
        if tool not in ("hands_windows", "hands_tree"):
            emit({"event": "hands_action", "tool": tool, "target": target,
                  "ok": ok, "verified": verified})
        print(f"[HANDS] {tool} {target} ok={ok} verified={verified}")
        return res
    finally:
        if turn_id == "call":
            hd.release(turn_id)


def _hands_turn_end(turn_id, emit):
    try:
        import hoplita_desktop as hd
        s = hd.release(turn_id)
        if s is not None and s.announced:
            emit({"event": "hands_end", "turn_id": turn_id,
                  "actions": s.actions, "stopped": s.stopped})
            print(f"[HANDS] turn {turn_id} releases the desktop "
                  f"({s.actions} actions)")
    except Exception as e:
        print(f"[HANDS] release failed: {e}", file=sys.stderr)


def register_hands_tools():
    TOOLS.update({
        "hands_status":     {"desc": "The DESKTOP HANDS: what this machine offers before you touch anything — whether windows can be read as elements, whether the screen can be captured, whether raw pointer/keyboard input exists, which session this is and who holds the hands right now. Call it first; the rest of the hands_* tools follow the recipe it returns.",
                             "fn": lambda a: _hands_call("hands_status", lambda H, s, a: H.status(), a)},
        "hands_windows":    {"desc": "Lists the windows the desktop knows — application, title, whether it is the ACTIVE one, whether it is minimized — with ids for hands_tree. The list spans every virtual desktop, so a window listed is not necessarily on the visible screen: only the active one is for sure, and a capture decides the rest. This app's own windows are never listed: you cannot drive yourself.",
                             "fn": lambda a: _hands_call("hands_windows", lambda H, s, a: H.windows(s), a)},
        "hands_tree":       {"desc": "The elements inside a window: buttons, fields, menus, labels, tabs — each with an id, its states (focused, checked, editable…) and the actions it offers. `query` narrows to names, roles or text containing it. Ids are valid for THIS turn.",
                             "fn": lambda a: _hands_call("hands_tree", lambda H, s, a: H.tree(
                                 s, a["window"], a.get("depth", 6), a.get("max_items", 200), a.get("query", "")), a)},
        "hands_click":      {"desc": "Presses an element by id (its own action — the reliable way) or clicks at x, y when raw input exists — x, y read IN THE LAST IMAGE you were shown (hands_screenshot or hands_verify): its shrinking and cropping are undone for you, so give the pixel you see. The result says how it was done and whether anything changed: `verified` true/false, or null when nothing could measure it. A false is a press that did nothing: look (hands_screenshot) before pressing again.",
                             "fn": lambda a: _hands_call("hands_click", lambda H, s, a: H.click(
                                 s, element=a.get("element"), x=a.get("x"), y=a.get("y"),
                                 button=a.get("button", "left"), double=bool(a.get("double", False))), a)},
        "hands_type":       {"desc": "Writes text: into an element by id (set through its editable interface and read back to confirm) or into whatever has the focus when raw input exists.",
                             "fn": lambda a: _hands_call("hands_type", lambda H, s, a: H.type_text(
                                 s, a["text"], element=a.get("element")), a)},
        "hands_key":        {"desc": "Sends a key combination ('ctrl+s', 'Return', 'alt+F4'). Prefer pressing the real menu item or button with hands_click when it exists: a key can only be verified by the pixels it changed.",
                             "fn": lambda a: _hands_call("hands_key", lambda H, s, a: H.key(s, a["keys"]), a)},
        "hands_screenshot": {"desc": "Looks at the screen — or one window, or a region — attached to the conversation as an image you can actually see. Needs a vision-capable model. The small helmeted figure in a corner is the user's control: never click it.",
                             "fn": lambda a: _hands_call("hands_screenshot", lambda H, s, a: H.screenshot(
                                 s, window=a.get("window"), region=a.get("region"), max_width=a.get("max_width")), a)},
        "hands_verify":     {"desc": "Checks the outcome of what you did: `expect_text` is searched in the element or window you name (or in every window), the pixel change since the last capture is measured, and a fresh capture is attached so you can look. Use it after every step that matters, before saying it is done.",
                             "fn": lambda a: _hands_call("hands_verify", lambda H, s, a: H.verify(
                                 s, expect_text=a.get("expect_text", ""), element=a.get("element"),
                                 window=a.get("window"), with_screenshot=a.get("with_screenshot", True)), a)},
    })
    TOOL_PARAMS.update({
        "hands_status":     {"properties": {}},
        "hands_windows":    {"properties": {}},
        "hands_tree":       {"properties": {"window": {"type": "string", "description": "A window id from hands_windows."},
                                            "depth": {"type": "integer"}, "max_items": {"type": "integer"},
                                            "query": {"type": "string", "description": "Only elements whose name, role or text contains this."}},
                             "required": ["window"]},
        "hands_click":      {"properties": {"element": {"type": "string", "description": "An element id from hands_tree."},
                                            "x": {"type": "integer", "description": "Pixel x in the last image you were shown."},
                                            "y": {"type": "integer", "description": "Pixel y in the last image you were shown."},
                                            "button": {"type": "string", "enum": ["left", "right", "middle"]},
                                            "double": {"type": "boolean"}}},
        "hands_type":       {"properties": {"text": {"type": "string"},
                                            "element": {"type": "string", "description": "An editable element id from hands_tree; empty = whatever has focus."}},
                             "required": ["text"]},
        "hands_key":        {"properties": {"keys": {"type": "string", "description": "Keys joined with '+', e.g. ctrl+s, alt+F4, Return."}},
                             "required": ["keys"]},
        "hands_screenshot": {"properties": {"window": {"type": "string"},
                                            "region": {"type": "array", "items": {"type": "integer"}, "description": "[x, y, width, height] read in the last image you were shown (its scale and crop are undone for you)."},
                                            "max_width": {"type": "integer"}}},
        "hands_verify":     {"properties": {"expect_text": {"type": "string"},
                                            "element": {"type": "string"}, "window": {"type": "string"},
                                            "with_screenshot": {"type": "boolean"}}},
    })
    import wb_tools as _wbt
    TOOL_PARAMS.update(_wbt.tool_params())



def _load_external_mcps():
    if not CONFIG.has_section("MCP_SERVERS"):
        return
    for key in CONFIG.options("MCP_SERVERS"):
        tools = _connect_external_mcp(key)
        if tools:
            _register_external_tools(key, tools)


def _connect_external_mcp(key):
    value = CONFIG.get("MCP_SERVERS", key, fallback="") or ""
    if not (value.startswith("stdio:") or value.startswith("sse:")):
        return None
    api_key = CONFIG.get("MCP_SERVERS", f"{key}_key", fallback="")
    print(f"[MCP-EXT] Connecting '{key}': {value[:60]}...")
    tools = None
    if value.startswith("stdio:"):
        tools = _connect_stdio_mcp(key, value[6:])
    elif value.startswith("sse:"):
        url = value[4:]
        _ext_mcp_urls[key] = url
        _ext_mcp_keys[key] = api_key
        tools = _connect_sse_mcp(key, url, api_key)
    if tools:
        print(f"[MCP-EXT] ✅ '{key}': {len(tools)} tools")
    else:
        print(f"[MCP-EXT] ❌ '{key}': no tools or connection failed")
    return tools


def _connect_stdio_mcp(name, cmd):
    try:
        import shlex
        args = shlex.split(cmd)
        env = os.environ.copy()
        api_key = CONFIG.get("MCP_SERVERS", f"{name}_key", fallback="")
        if api_key:
            env[f"{name.upper()}_API_KEY"] = api_key
            print(f"[MCP-EXT] ENV: {name.upper()}_API_KEY = {api_key[:8]}...")
        for cfgkey in CONFIG.options("MCP_SERVERS"):
            if cfgkey.startswith(f"{name}_") and cfgkey != f"{name}_key":
                suffix = cfgkey[len(name) + 1:]
                env_name = f"{name.upper()}_{suffix.upper()}"
                env[env_name] = CONFIG.get("MCP_SERVERS", cfgkey)
                print(f"[MCP-EXT] ENV: {env_name} = {CONFIG.get('MCP_SERVERS', cfgkey)[:40]}")
        try:
            proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, env=env, bufsize=1,
                                    **__import__("hoplita_paths").no_console())
        except FileNotFoundError:
            cmd_name = args[0] if args else "?"
            print(f"[MCP-EXT] ❌ '{name}' command not found: {cmd_name}")
            if cmd_name in ("uvx", "uvx.exe", "uv", "uv.exe"):
                print(f"[MCP-EXT]   Cause: uv/uvx not in PATH nor venv.")
                print(f"[MCP-EXT]   Fix: {sys.executable} -m pip install uv")
                print(f"[MCP-EXT]   Workaround: comment out/delete the '{name} = ...' line "
                      f"from [MCP_SERVERS] in config.ini if you do not need this MCP.")
            else:
                print(f"[MCP-EXT]   Cause: '{cmd_name}' not found in PATH.")
            return None
        _ext_mcp_procs[name] = proc

        def _drain_stderr(p, sname):
            try:
                for line in p.stderr:
                    line = line.strip()
                    if line:
                        print(f"[MCP-EXT:{sname}] {line[:120]}")
            except Exception:
                pass
        threading.Thread(target=_drain_stderr, args=(proc, name), daemon=True).start()

        time.sleep(1)
        if proc.poll() is not None:
            print(f"[MCP-EXT] '{name}' exited immediately (code {proc.returncode})")
            return None

        resp = _stdio_send_recv(proc, _jsonrpc_request("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "HOPLITA", "version": "2.0"}}), timeout=120)
        if not resp or "result" not in resp:
            print(f"[MCP-EXT] '{name}' init failed: {resp}")
            proc.kill()
            return None
        proc.stdin.write(json.dumps({"jsonrpc": "2.0",
                                     "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()
        resp = _stdio_send_recv(proc, _jsonrpc_request("tools/list"))
        if resp and "result" in resp:
            return resp["result"].get("tools", [])
        return None
    except Exception as e:
        print(f"[MCP-EXT] stdio '{name}' error: {e}")
        return None


def _connect_sse_mcp(name, url, api_key):
    post_url = url.replace("/sse", "") if url.endswith("/sse") else url
    resp = _sse_call(post_url, api_key, "initialize", {
        "protocolVersion": "2024-11-05", "capabilities": {},
        "clientInfo": {"name": "HOPLITA", "version": "2.0"}})
    if not resp or "result" not in resp:
        print(f"[MCP-EXT] '{name}' init failed: {resp}")
        return None
    _sse_call(post_url, api_key, "notifications/initialized")
    resp = _sse_call(post_url, api_key, "tools/list")
    if resp and "result" in resp:
        return resp["result"].get("tools", [])
    return None


def _register_external_tools(name, tools, into=None, params_into=None):
    into = TOOLS if into is None else into
    params_into = TOOL_PARAMS if params_into is None else params_into
    for tool in tools:
        tool_name = f"{name}.{tool['name']}"
        desc = tool.get("description", tool["name"])
        schema = tool.get("inputSchema", {})

        def make_caller(sn, tn):
            def caller(args):
                return _call_external_tool(sn, tn, args)
            return caller

        into[tool_name] = {"desc": f"[{name}] {desc}", "fn": make_caller(name, tool["name"])}
        params_into[tool_name] = {"properties": schema.get("properties", {}),
                                  "required": schema.get("required", [])}


def _call_external_tool(server_name, tool_name, arguments):
    params = {"name": tool_name, "arguments": arguments or {}}
    if server_name in _ext_mcp_procs:
        proc = _ext_mcp_procs[server_name]
        if proc.poll() is not None:
            print(f"[MCP-EXT] '{server_name}' died, restarting...")
            cmd = CONFIG.get("MCP_SERVERS", server_name)[6:]
            _connect_stdio_mcp(server_name, cmd)
            proc = _ext_mcp_procs.get(server_name)
            if not proc:
                return {"error": f"Could not restart '{server_name}'"}
        with _ext_mcp_lock:
            resp = _stdio_send_recv(proc, _jsonrpc_request("tools/call", params), timeout=120)
    elif server_name in _ext_mcp_urls:
        url = _ext_mcp_urls[server_name]
        post_url = url.replace("/sse", "") if url.endswith("/sse") else url
        api_key = _ext_mcp_keys.get(server_name, "")
        resp = _sse_call(post_url, api_key, "tools/call", params, timeout=120)
    else:
        return {"error": f"Server '{server_name}' not connected"}

    if not resp:
        return {"error": f"No response from '{server_name}'"}
    if "error" in resp:
        return {"error": resp["error"].get("message", str(resp["error"]))}
    result = resp.get("result", {})
    content = result.get("content", [])
    if content:
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        if texts:
            result = {"result": "\n".join(texts)}
    return _postprocess_media(result, server_name, tool_name)



_MEDIA_EXTS = {
    '.mp3': 'audio', '.wav': 'audio', '.ogg': 'audio', '.flac': 'audio',
    '.aac': 'audio', '.m4a': 'audio',
    '.mp4': 'video', '.webm': 'video', '.avi': 'video', '.mov': 'video', '.mkv': 'video',
    '.png': 'image', '.jpg': 'image', '.jpeg': 'image', '.gif': 'image',
    '.webp': 'image', '.svg': 'image',
    '.pdf': 'document',
}
_MEDIA_MIMES = {
    'audio/': 'audio', 'video/': 'video', 'image/': 'image',
    'application/pdf': 'document', 'application/octet-stream': 'binary',
}
_MIME_TO_EXT = {
    'audio/mpeg': '.mp3', 'audio/wav': '.wav', 'audio/ogg': '.ogg', 'audio/mp4': '.m4a',
    'video/mp4': '.mp4', 'video/webm': '.webm',
    'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif', 'image/webp': '.webp',
    'application/pdf': '.pdf',
}


def _postprocess_media(result, server_name, tool_name):
    saved = []

    def _scan(obj):
        if isinstance(obj, str):
            if obj.startswith(("http://", "https://")):
                local = _try_download_media(obj, server_name, tool_name)
                if local:
                    saved.append(local)
                    return local["local_path"]
            if obj.startswith("data:") and ";base64," in obj:
                local = _save_data_uri(obj, server_name, tool_name)
                if local:
                    saved.append(local)
                    return local["local_path"]
            if len(obj) > 10_000 and re.match(r'^[A-Za-z0-9+/=]+$', obj[:100]):
                local = _save_raw_b64(obj, server_name, tool_name)
                if local:
                    saved.append(local)
                    return local["local_path"]
        elif isinstance(obj, dict):
            for k, v in list(obj.items()):
                new = _scan(v)
                if new is not v:
                    obj[k] = new
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                new = _scan(v)
                if new is not v:
                    obj[i] = new
        return obj

    result = _scan(result)
    if saved:
        if isinstance(result, dict):
            result["_saved_files"] = saved
        print(f"[MCP-EXT] 📥 {len(saved)} media file(s) saved")
    return result


def _try_download_media(url, server_name, tool_name):
    from urllib.parse import urlparse
    parsed = urlparse(url)
    path_ext = os.path.splitext(parsed.path)[1].lower()
    is_media = path_ext in _MEDIA_EXTS
    try:
        if is_media:
            r = requests.get(url, timeout=60, stream=True)
        else:
            r = requests.head(url, timeout=10, allow_redirects=True)
            ct = r.headers.get("content-type", "").lower()
            is_media = any(ct.startswith(m) for m in _MEDIA_MIMES)
            if not is_media:
                return None
            r = requests.get(url, timeout=60, stream=True)
        if r.status_code != 200:
            return None
        ct = r.headers.get("content-type", "").lower().split(";")[0]
        if not path_ext or path_ext not in _MEDIA_EXTS:
            path_ext = _MIME_TO_EXT.get(ct, '.bin')
        fname = f"{server_name}_{tool_name}_{int(time.time())}{path_ext}"
        local_path = os.path.join("/tmp", fname)
        with open(local_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        size = os.path.getsize(local_path)
        media_type = _MEDIA_EXTS.get(path_ext, "binary")
        print(f"[MCP-EXT] 📥 {media_type}: {url[:60]}... → {local_path} ({size / 1024:.0f}KB)")
        return {"local_path": local_path, "type": media_type, "size": size, "source_url": url}
    except Exception as e:
        print(f"[MCP-EXT] Download failed {url[:60]}: {e}")
        return None


def _save_data_uri(data_uri, server_name, tool_name):
    try:
        header, b64_data = data_uri.split(";base64,", 1)
        mime = header.split("data:", 1)[1]
        ext = _MIME_TO_EXT.get(mime, '.bin')
        fname = f"{server_name}_{tool_name}_{int(time.time())}{ext}"
        local_path = os.path.join("/tmp", fname)
        with open(local_path, "wb") as f:
            f.write(base64.b64decode(b64_data))
        size = os.path.getsize(local_path)
        media_type = _MEDIA_EXTS.get(ext, "binary")
        print(f"[MCP-EXT] 📥 {media_type} (data URI) → {local_path} ({size / 1024:.0f}KB)")
        return {"local_path": local_path, "type": media_type, "size": size}
    except Exception:
        return None


_MAGIC_BYTES = [
    (b'ID3', '.mp3'), (b'\xff\xfb', '.mp3'),
    (b'RIFF', '.wav'),
    (b'\x89PNG', '.png'),
    (b'\xff\xd8', '.jpg'),
    (b'%PDF', '.pdf'),
    (b'\x00\x00\x00\x1c', '.mp4'), (b'\x00\x00\x00\x18', '.mp4'),
]


def _save_raw_b64(b64_str, server_name, tool_name):
    try:
        raw = base64.b64decode(b64_str)
        ext = '.bin'
        for magic, e in _MAGIC_BYTES:
            if raw[:len(magic)] == magic:
                ext = e
                break
        fname = f"{server_name}_{tool_name}_{int(time.time())}{ext}"
        local_path = os.path.join("/tmp", fname)
        with open(local_path, "wb") as f:
            f.write(raw)
        media_type = _MEDIA_EXTS.get(ext, "binary")
        print(f"[MCP-EXT] 📥 {media_type} (raw b64, {len(raw) / 1024:.0f}KB) → {local_path}")
        return {"local_path": local_path, "type": media_type, "size": len(raw)}
    except Exception:
        return None


def _shutdown_external_mcps():
    for name, proc in _ext_mcp_procs.items():
        try:
            proc.stdin.close()
            proc.wait(timeout=5)
        except Exception:
            try: proc.kill()
            except Exception: pass
        print(f"[MCP-EXT] Shut down '{name}'")
    _ext_mcp_procs.clear()


import atexit
atexit.register(_shutdown_external_mcps)


import hoplita_selfcfg

_SELFCFG_LOCK = threading.Lock()
_SELFCFG_REMOTE = {"error": "this turn came from another machine: only the "
                            "person at this machine may read or change its "
                            "configuration"}


def _selfcfg_ctx():
    return getattr(_ORCH, "ctx", None)


def _selfcfg_docs_hint(section):
    base = hoplita_base()
    hit, _o = hoplita_selfcfg.read_section(base, f"[{section}]", "CONFIG.md")
    if hit:
        return {"file": hit["file"], "section": hit["section"]}
    return None


def _ext_mcp_status():
    out = {}
    if CONFIG is None or not CONFIG.has_section("MCP_SERVERS"):
        return out
    for k in CONFIG.options("MCP_SERVERS"):
        v = CONFIG.get("MCP_SERVERS", k, fallback="") or ""
        if not v.startswith(("stdio:", "sse:")):
            continue
        n = sum(1 for t_ in TOOLS if t_.startswith(k + "."))
        proc = _ext_mcp_procs.get(k)
        alive = (proc is not None and proc.poll() is None) or k in _ext_mcp_urls
        out[k] = {"connected": bool(alive and n), "tools": n}
    return out


def tool_see_my_config(section=None):
    if getattr(_selfcfg_ctx(), "remote", False):
        return dict(_SELFCFG_REMOTE)
    import hoplita_llmcfg
    path = config_path()
    cfg, problem = hoplita_llmcfg.read_config(path)
    if section:
        real, vals = hoplita_selfcfg.section_values(cfg, section)
        if real is None:
            return {"error": f"there is no section [{section}] in {path}",
                    "sections": cfg.sections(),
                    "hint": "edit_my_config creates a section when you write "
                            "its first key"}
        out = {"path": path, "section": real, "values": vals}
        if real.upper() == "MCP_SERVERS":
            out["servers"] = _ext_mcp_status()
        hint = _selfcfg_docs_hint(real)
        if hint:
            out["manual"] = hint
        if problem:
            out["problem"] = problem
        return out
    base = hoplita_base()
    out = {"path": path, "install": base,
           "manual": os.path.join(base, "docs"),
           "sections": hoplita_selfcfg.overview(cfg),
           "how": "see_my_config(section=...) for one section's values "
                  "(secrets covered); read_my_docs to find what a key does; "
                  "edit_my_config to change one key, with the person's yes."}
    if problem:
        out["problem"] = problem
    return out


def tool_read_my_docs(query=None, section=None, file=None):
    base = hoplita_base()
    if not hoplita_selfcfg.doc_files(base):
        return {"error": f"no manual found under {base} (docs/*.md)"}
    if section:
        hit, others = hoplita_selfcfg.read_section(base, section, file)
        if hit is None:
            near = hoplita_selfcfg.search(base, section, limit=5)
            return {"error": f"no section titled '{section}'"
                             + (f" in {file}" if file else ""),
                    "near": near}
        if others:
            hit["also_titled_like_this"] = others
        return hit
    if query:
        hits = hoplita_selfcfg.search(base, query)
        if not hits:
            return {"results": [], "note": "nothing in the manual matches; "
                                           "try other words, or the outline "
                                           "(no arguments)"}
        return {"results": hits,
                "how": "read one with read_my_docs(section=<its title>, "
                       "file=<its file>)"}
    return {"outline": hoplita_selfcfg.outline(base, file),
            "how": "read_my_docs(query=...) to search, or "
                   "read_my_docs(section=<title>, file=<file>) to read one"}


def _selfcfg_gate(ctx, change):
    refusal = {"error": "changing the configuration needs the person's yes",
               "change": change,
               "hint": "tell the user the exact change so they can make it, "
                       "or ask again from an interactive chat"}
    if ctx is None or getattr(ctx, "unattended", False) \
            or getattr(ctx, "ack_mode", "") != "stream":
        refusal["why"] = "nobody can answer on this turn"
        return refusal
    ack_id = uuid.uuid4().hex[:12]
    waiter = _queue.Queue(maxsize=1)
    with _PLAN_ACKS_LOCK:
        _PLAN_ACKS[ack_id] = {"q": waiter, "subtasks": [], "mode": ctx.ack_mode,
                              "kind": "config"}
    try:
        ctx.emit(dict({"event": "config_consent", "ack_id": ack_id,
                       "turn_id": ctx.turn_id}, **change))
        wait_s = (CONFIG.getint("CHAT", "plan_ack_timeout",
                                fallback=_PLAN_ACK_TIMEOUT_S)
                  if CONFIG else _PLAN_ACK_TIMEOUT_S)
        deadline = time.time() + wait_s
        answer = None
        while time.time() < deadline:
            if ctx.cancel():
                refusal["why"] = "the turn was cancelled while asking"
                return refusal
            try:
                answer = waiter.get(timeout=1.0)
                break
            except _queue.Empty:
                continue
    finally:
        with _PLAN_ACKS_LOCK:
            _PLAN_ACKS.pop(ack_id, None)
    if answer is None:
        refusal["why"] = "the question timed out"
        return refusal
    if (answer.get("decision") or "") == "run":
        return None
    refusal["why"] = "the person said no"
    return refusal


def _detach_external_mcp(name):
    global TOOLS, TOOL_PARAMS
    proc = _ext_mcp_procs.pop(name, None)
    if proc is not None:
        try:
            proc.stdin.close()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    _ext_mcp_urls.pop(name, None)
    _ext_mcp_keys.pop(name, None)
    pre = name + "."
    gone = [k for k in TOOLS if k.startswith(pre)]
    if gone:
        TOOLS = {k: v for k, v in TOOLS.items() if not k.startswith(pre)}
        TOOL_PARAMS = {k: v for k, v in TOOL_PARAMS.items() if not k.startswith(pre)}
    return gone


def _reattach_external_mcp(name):
    global TOOLS, TOOL_PARAMS
    gone = _detach_external_mcp(name)
    line = (CONFIG.get("MCP_SERVERS", name, fallback="")
            if CONFIG and CONFIG.has_section("MCP_SERVERS") else "") or ""
    if not line.startswith(("stdio:", "sse:")):
        return {"server": name, "connected": False, "removed_tools": len(gone)}
    tools = _connect_external_mcp(name)
    if not tools:
        return {"server": name, "connected": False,
                "error": "the server did not answer with its tools — the "
                         "line is saved; check the command or URL (the MCP "
                         "log says why)"}
    new_t, new_p = dict(TOOLS), dict(TOOL_PARAMS)
    _register_external_tools(name, tools, into=new_t, params_into=new_p)
    TOOL_PARAMS = new_p
    TOOLS = new_t
    names = [f"{name}.{t_['name']}" for t_ in tools]
    return {"server": name, "connected": True,
            "tools": [{"name": n, "description": (new_t[n]["desc"] or "")[:160]}
                      for n in names],
            "how": "they are in the catalogue NOW, in this same turn: "
                   "load_tools(names=[...]) for their parameters, then "
                   "use_tool(name=..., arguments={...})"}


def tool_edit_my_config(section=None, key=None, value=None, delete=False,
                        changes=None):
    import hoplita_llmcfg
    ctx = _selfcfg_ctx()
    if getattr(ctx, "remote", False):
        return dict(_SELFCFG_REMOTE)
    if isinstance(changes, str):
        try:
            changes = json.loads(changes)
        except Exception:
            return {"error": "`changes` must be a list of {section, key, value} "
                             "or {section, key, delete: true}"}
    if changes is None:
        changes = [{"section": section, "key": key, "value": value,
                    "delete": delete}]
    if not isinstance(changes, list) or not changes:
        return {"error": "nothing to change: pass section+key+value, or `changes`"}
    wanted = []
    for c in changes:
        if not isinstance(c, dict):
            return {"error": "each change is an object {section, key, value}"}
        sect = str(c.get("section") or "").strip().strip("[]").strip()
        k = str(c.get("key") or "").strip()
        why = hoplita_selfcfg.bad_name(sect, k)
        if why:
            return {"error": f"[{sect}] {k}: {why}"}
        d = c.get("delete", False)
        d = bool(d) and str(d).lower() not in ("false", "0", "no")
        v = c.get("value")
        if not d:
            if v is None:
                return {"error": f"[{sect}] {k}: pass `value`, or delete=true "
                                 "to remove the key"}
            v = str(v)
            if hoplita_selfcfg.looks_masked(v):
                return {"error": f"[{sect}] {k}: that is the mask you were "
                                 "shown, not a value: writing it would replace "
                                 "the real secret. Ask the person for the "
                                 "value, or leave the key alone."}
        wanted.append((sect, k, v, d))
    path = config_path()

    def _plan(text):
        done = []
        for sect, k, v, d in wanted:
            cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"),
                                            strict=False)
            try:
                cfg.read_string(text)
            except configparser.Error as e:
                return {"error": f"config.ini cannot be read "
                                 f"({str(e).splitlines()[0]}); fix it in the "
                                 "Configuration dialog first"}
            real = next((s for s in cfg.sections() if s.lower() == sect.lower()),
                        sect)
            old = (cfg.get(real, k, raw=True)
                   if cfg.has_section(real) and cfg.has_option(real, k) else None)
            if d:
                if old is None:
                    return {"error": f"[{real}] has no key '{k}' to remove"}
                text = hoplita_llmcfg.del_key(text, real, k)
            else:
                if old == v:
                    continue
                text = hoplita_llmcfg.set_value(text, real, k, v)
            done.append((real, k, old, v, d))
        bad = hoplita_llmcfg.ini_problem(text)
        if bad:
            return {"error": f"the change would leave config.ini unreadable "
                             f"({bad}); nothing was written"}
        return done, text

    def _read():
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        except FileNotFoundError:
            return ""

    planned = _plan(_read())
    if isinstance(planned, dict):
        return planned
    done, _new = planned
    if not done:
        return {"ok": True, "changed": False,
                "note": "config.ini already has every one of those values"}

    def _shown(items):
        return [{"section": s, "key": k,
                 "old": None if o is None else hoplita_selfcfg.mask_value(k, o),
                 "new": None if d else hoplita_selfcfg.mask_value(k, v),
                 "delete": d}
                for s, k, o, v, d in items]

    shown = _shown(done)
    runs_program = any(s.upper() == "MCP_SERVERS" and not d
                       and str(v).startswith("stdio:")
                       for s, k, o, v, d in done)
    gate = _selfcfg_gate(ctx, {"changes": shown, "runs_program": runs_program})
    if gate:
        return gate

    with _SELFCFG_LOCK:
        planned = _plan(_read())
        if isinstance(planned, dict):
            return planned
        done, new_text = planned
        old_cfg = CONFIG
        try:
            if os.path.exists(path):
                shutil.copy(path, path + ".bak")
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(new_text)
            if os.path.exists(path):
                shutil.copymode(path, tmp)
            os.replace(tmp, path)
        except Exception as e:
            return {"error": f"could not write {path}: {e}"}
        reloaded = reload_config_endpoint()

    out = {"ok": True, "changed": True, "path": path,
           "changes": _shown(done), "backup": path + ".bak"}
    warnings = [w for w in (reloaded.get("warnings") or [])
                if "MCP_SERVERS" not in w]
    if reloaded.get("error"):
        warnings.append(f"the reload failed: {reloaded['error']}")
    servers = []
    for s, k, o, v, d in done:
        if s.upper() == "MCP_SERVERS":
            srv = (hoplita_selfcfg.mcp_server_of(CONFIG, k)
                   or (old_cfg is not None
                       and hoplita_selfcfg.mcp_server_of(old_cfg, k)) or None)
            if srv and srv not in servers:
                servers.append(srv)
        elif s.upper() == "APP" and k.lower() == "language":
            warnings.append("the window keeps the language it painted with "
                            "until the app is restarted")
    if servers:
        out["mcp"] = [_reattach_external_mcp(srv) for srv in servers]
    if warnings:
        out["warnings"] = warnings
    try:
        if ctx is not None:
            ctx.emit({"event": "config_changed",
                      "changes": [{"section": s, "key": k} for s, k, *_ in done]})
    except Exception:
        pass
    print(f"[SELFCFG] {len(done)} change(s) written with the person's yes: "
          + ", ".join(f"[{s}] {k}" for s, k, *_ in done))
    return out



from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="HOPLITA MCP Server", version="2.0")
import wb_http as _wb_http
app.include_router(_wb_http.router)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class ToolCallReq(BaseModel):
    tool: str
    arguments: dict = {}
    code_project: str = ""
    turn_id: str = ""


class FeedbackReq(BaseModel):
    goal: str
    steps: list
    result: str
    feedback: str
    tags: list = []
    duration_s: float = 0
    partial_good_steps: list = []
    note: str = ""


class DataReq(BaseModel):
    manager: str
    method: str
    args: list = []
    kwargs: dict = {}


class FirewallReq(BaseModel):
    blocked: list = []


class ExpSearchReq(BaseModel):
    query: str
    n_results: int = 3


MANAGERS: dict = {}


def register_manager_map():
    global MANAGERS
    MANAGERS = {"todo": todo_mgr, "notes": notes_mgr, "cred": cred_mgr, "cron": cron_mgr}


@app.post("/data")
def data_proxy(req: DataReq):
    if req.manager not in MANAGERS:
        raise HTTPException(404, f"Manager '{req.manager}' does not exist")
    mgr = MANAGERS[req.manager]
    if not hasattr(mgr, req.method):
        raise HTTPException(404, f"Method '{req.method}' does not exist in {req.manager}")
    try:
        result = getattr(mgr, req.method)(*req.args, **req.kwargs)
        try:
            import fed_tools as _ft
            _ft.after_local_mutation(req.manager, req.method,
                                     req.args, req.kwargs, result)
        except Exception:
            pass
        if result is None:
            return {"result": None}
        return {"result": json.loads(json.dumps(result, default=str))}
    except Exception as e:
        return {"error": str(e)}


@app.get("/health")
def health():
    try:
        import hoplita_version as _hv
        _vinfo = _hv.info()
    except Exception:
        _vinfo = {}
    return {
        "status": "ok",
        "version": _vinfo.get("date", ""),
        "label": _vinfo.get("long", ""),
        "started": _MCP_STARTED,
        "pid": os.getpid(),
        "source": os.path.abspath(__file__),
        "platform": f"{platform.system()}/{platform.machine()}",
        "tools": list(TOOLS.keys()),
        "tool_count": len(TOOLS),
        "experience_count": len(_exp_docs),
        "notes_cats": notes_mgr.get_categories() if notes_mgr else [],
        "cred_sites": len(cred_mgr.list_sites()) if cred_mgr else 0,
    }


class SpawnEnrollReq(BaseModel):
    server_url: str
    token: str
    handle: str


@app.post("/spawn/enroll")
def spawn_enroll(req: SpawnEnrollReq):
    import fed_tools
    try:
        result = fed_tools.tool_fed_enroll_child(
            req.server_url, req.token, req.handle)
    except Exception as e:
        return {"error": f"Enrollment failed: {e}"}
    if result.get("kit_applied"):
        reload_config_endpoint()
    return result


def _enroll_from_env():
    tok = os.environ.get("HOPLITA_SPAWN_TOKEN") or ""
    if tok:
        _scrub_startup_env("HOPLITA_SPAWN_TOKEN")
    os.environ.pop("HOPLITA_SPAWN_TOKEN", None)
    server = os.environ.pop("HOPLITA_SPAWN_SERVER", "")
    handle = os.environ.pop("HOPLITA_SPAWN_HANDLE", "")
    if not tok:
        return
    import fed_tools
    if fed_tools._store().has_identity():
        print("[SPAWN] already enrolled; the token is not needed again")
        return
    if not (server and handle):
        print("[SPAWN] ⚠ HOPLITA_SPAWN_TOKEN needs HOPLITA_SPAWN_SERVER and "
              "HOPLITA_SPAWN_HANDLE", file=sys.stderr)
        return

    def _go():
        for n in range(12):
            r = fed_tools.tool_fed_enroll_child(server, tok, handle) or {}
            if r.get("ok"):
                print(f"[SPAWN] ✓ {r.get('message')}")
                if r.get("kit_applied"):
                    reload_config_endpoint()
                return
            err = str(r.get("error") or "")
            print(f"[SPAWN] ⚠ {err}", file=sys.stderr)
            if "offline" not in err.lower() and "connect" not in err.lower():
                return
            time.sleep(10)
    threading.Thread(target=_go, daemon=True, name="spawn_enroll").start()


@app.get("/agent/pending-approvals")
def agent_pending_approvals():
    import fed_tools
    return {"pending": fed_tools.list_pending_approvals(),
            "expired": sorted(fed_tools._EXPIRED_APPROVALS)}


@app.get("/agent/session-events")
def agent_session_events():
    import fed_tools
    return {"events": fed_tools.drain_session_events(),
            "shares": fed_tools.drain_share_events(),
            "wake": fed_tools.drain_wake_events()}


class SessionSendReq(BaseModel):
    peer: str
    message: str


@app.post("/agent/session-send")
def agent_session_send(req: SessionSendReq):
    import fed_tools
    return fed_tools.start_outbound_session(req.peer, req.message)


class SessionStopReq(BaseModel):
    peer: str


@app.post("/agent/session-stop")
def agent_session_stop(req: SessionStopReq):
    import fed_tools
    return fed_tools.stop_outbound_session(req.peer)


@app.post("/agent/session-inject")
def agent_session_inject(req: SessionSendReq):
    import fed_tools
    return fed_tools.inject_local_session_turn(req.peer, req.message)


@app.post("/agent/session-close-inbound")
def agent_session_close_inbound(req: SessionStopReq):
    import fed_tools
    return fed_tools.close_inbound_session(req.peer)


class ApprovalDecisionReq(BaseModel):
    peer: str
    approved: bool
    id: str = ""


@app.post("/agent/resolve-approval")
def agent_resolve_approval(req: ApprovalDecisionReq):
    import fed_tools
    return fed_tools.resolve_approval(req.peer, req.approved, approval_id=req.id)


@app.post("/reload-config")
def reload_config_endpoint():
    global CONFIG
    try:
        def _section_dict(cfg, sec):
            if not cfg or not cfg.has_section(sec):
                return {}
            return {k: cfg.get(sec, k) for k in cfg.options(sec)}

        old_mcp_servers = _section_dict(CONFIG, "MCP_SERVERS")
        new_cfg = load_config()
        new_mcp_servers = _section_dict(new_cfg, "MCP_SERVERS")

        CONFIG = new_cfg

        try:
            import fed_agent
            fed_agent.set_max_iter(CONFIG.getint("CHAT", "agentic_max_iterations",
                                                 fallback=8))
            fed_agent.set_result_cap(CONFIG.getint(
                "AGENTIC", "tool_result_max_chars", fallback=8000))
        except Exception as _e:
            print(f"[RELOAD] ⚠ max_iter: {_e}", file=sys.stderr)
        try:
            import fed_agent
            fed_agent.set_metacog(
                enabled=CONFIG.getboolean("CHAT", "metacog", fallback=True),
                stall_seconds=CONFIG.getint("CHAT", "metacog_stall_seconds",
                                            fallback=120),
                stall_tools=CONFIG.getint("CHAT", "metacog_stall_tools",
                                          fallback=4),
                heavy_cut=CONFIG.getint("CHAT", "heavy_sterile_cut",
                                        fallback=3))
        except Exception as _e:
            print(f"[RELOAD] ⚠ metacog knobs: {_e}", file=sys.stderr)

        try:
            import fed_agent
            if CONFIG.getboolean("AGENTIC", "defer_tool_schemas", fallback=True):
                _load_tool_uses()
                fed_agent.configure_deferred(hot=hot_tools,
                                             catalogue=tool_catalogue,
                                             on_tool_used=_tool_used)
                print(f"[RELOAD] schemas on demand ON "
                      f"(hot: {len(hot_tools())})")
            else:
                fed_agent.configure_deferred(hot=None, catalogue=None,
                                             on_tool_used=None)
                print("[RELOAD] schemas on demand OFF")
        except Exception as _e:
            print(f"[RELOAD] ⚠ defer_tool_schemas: {_e}", file=sys.stderr)

        warnings = []
        if old_mcp_servers != new_mcp_servers:
            warnings.append(
                "Changes detected in [MCP_SERVERS] — restart the MCP server "
                "(running stdio/SSE processes are not hot-reloaded)."
            )

        api_url, _k, api_model = _resolve_endpoint("API")
        print(f"[MCP] Config hot-reloaded. API: {api_model} @ {api_url}")
        return {
            "reloaded": True,
            "api_model": api_model,
            "api_url": api_url,
            "warnings": warnings,
        }
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"error": str(e)}



_blocked_tools: set = set()


def _load_firewall():
    global _blocked_tools
    try:
        sql = _get_sql()
        if sql is None:
            _blocked_tools = set()
            return
        row = next(iter(sql.load("firewall")), {})
        _blocked_tools = set(row.get("blocked", []))
        print(f"[FIREWALL] Blocked: {sorted(_blocked_tools) or 'none'}")
    except Exception as e:
        print(f"[FIREWALL] Error loading: {e}")
        _blocked_tools = set()


_TOOL_USES = {}
_TOOL_USES_DIRTY = False
_TOOL_USES_KEY = "uses_model_v2"


_TOOL_USES_SAVE_EVERY = 25
_tool_uses_since_save = 0


def _tool_used(name):
    global _TOOL_USES_DIRTY, _tool_uses_since_save
    if not name:
        return
    _TOOL_USES[name] = _TOOL_USES.get(name, 0) + 1
    _TOOL_USES_DIRTY = True
    _tool_uses_since_save += 1
    if _tool_uses_since_save >= _TOOL_USES_SAVE_EVERY:
        _tool_uses_since_save = 0
        try:
            _save_tool_uses()
        except Exception:
            pass


def _load_tool_uses():
    global _TOOL_USES
    try:
        sql = _get_sql()
        if sql is None:
            return
        row = next((r for r in sql.load("tool_uses")
                    if r.get("uuid") == _TOOL_USES_KEY), {})
        _TOOL_USES = {k: int(v) for k, v in (row.get("uses") or {}).items()}
        if _TOOL_USES:
            print(f"[TOOLS] previous use of {len(_TOOL_USES)} tool(s)")
    except Exception as e:
        print(f"[TOOLS] could not read the previous use: {e}", file=sys.stderr)


def _save_tool_uses():
    global _TOOL_USES_DIRTY
    if not _TOOL_USES_DIRTY:
        return
    try:
        sql = _get_sql()
        if sql is None:
            return
        sql.upsert("tool_uses", _TOOL_USES_KEY,
                   {"uuid": _TOOL_USES_KEY, "uses": _TOOL_USES,
                    "updated_at": datetime.now().isoformat()})
        _TOOL_USES_DIRTY = False
    except Exception:
        pass


_ALWAYS_HOT = ("add_fact", "edit_fact", "delete_fact",
               "plan_and_orchestrate", "call_council",
               "search_working_memory")


_STARTER_HOT = ("run_command", "read_file", "list_dir", "search_web",
                "scrape_url", "see_image")

_HOT_FROZEN = {}


def hot_tools(n=None):
    _key = (n, bool(_turn_project()), bool(_turn_rag()), frozenset(_blocked_tools))
    if _key in _HOT_FROZEN:
        return list(_HOT_FROZEN[_key])
    _out = _hot_tools_compute(n)
    _HOT_FROZEN[_key] = list(_out)
    return _out


def _hot_tools_compute(n=None):
    if n is None:
        n = CONFIG.getint("AGENTIC", "hot_tools", fallback=12) if CONFIG else 12
    if n <= 0:
        return []
    allowed = set(_allowed_tools())
    base = [k for k in _ALWAYS_HOT if k in allowed]
    if _turn_rag() and "search_rag" in allowed and "search_rag" not in base:
        base.append("search_rag")
    _proj = []
    if _turn_project():
        _proj = sorted(k for k in allowed
                       if (k.startswith(("code_", "git_"))
                           or k in ("fed_code_incoming", "fed_code_take",
                                    "add_task", "task_depends",
                                    "update_task_progress",
                                    "add_note", "update_note", "read_note",
                                    "project_journal", "project_write"))
                       and k not in _ALWAYS_HOT)
    ranked = sorted(((v, k) for k, v in _TOOL_USES.items()
                     if k in allowed and k not in _ALWAYS_HOT
                     and k not in set(_proj)),
                    reverse=True)
    used = [k for _v, k in ranked[:max(0, n - len(base))]]
    starter = [k for k in _STARTER_HOT
               if k in allowed and k not in base and k not in used and k not in set(_proj)]
    used += starter[:max(0, n - len(base) - len(used))]
    return base + _proj + used


_FAMILY_BLURB = {
    "system": "shell, files on disk, PDFs and alerts — and yourself: your own config.ini (read, and change with the person's yes) and your own manual",
    "code": "read/edit/search code in the open project, and git",
    "web": "search and browse the internet",
    "mail": "the user's mailboxes: folders, list, read, move to junk/trash and back (no permanent delete), send",
    "desktop": "the person's screen: windows, elements, clicks, keys, captures",
    "notes": "the user's saved notes and skills",
    "memory": "the user's facts, lessons, experience and recall",
    "tasks": "todo tasks and task projects",
    "calendar": "appointments",
    "cron": "scheduled jobs",
    "chat": "saved chat sessions and the conversation log",
    "canvas": "the HTML canvas and dashboards",
    "whiteboard": "a live whiteboard anchored to code: explain a change or how code works with diagrams and code peeks that open the code (start with wb_get_instructions)",
    "creds": "stored credentials, API keys, cards and OTP",
    "agents": "delegate to other models, council, children",
    "device": "this device's federated identity",
    "fedaccount": "your federation account",
    "fedcontacts": "your federated contacts",
    "fedshare": "share notes/folders/projects/appointments with contacts",
    "fedcode": "share code with contacts",
    "fedsync": "sync between your devices, inbox and notices",
    "other": "everything else",
}

_SHELF_KEEP_DESC = {
    "recall", "intent", "use_skill", "skill_folder", "search_rag",
    "session_moments", "session_brief", "session_recall", "session_search",
    "browse_web", "scrape_url", "call_api", "screenshot_web",
    "render_html", "canvas_eval", "canvas_see", "dashboard_render",
    "pdf", "pdf_info", "generate_pdf", "alert", "office", "office_info",
    "propose_memory_change", "working_memory_forget", "search_working_memory",
    "project_journal", "project_write", "get_agentic_config",
    "cron_set_context", "set_cron_context", "fed_ask_agent", "fed_share",
    "fill_credentials", "get_otp",
}


def _shelf_first_sentence(name, cap=90):
    d = (TOOLS.get(name, {}).get("desc") or "").strip()
    first = d.split(". ")[0].strip().rstrip(".")
    return first[:cap]


def tool_catalogue(expanded=()):
    from tool_families import tool_category, group_tools
    skip = set(expanded) | {"load_tools"}
    names = [n for n in _allowed_tools() if n not in skip]
    out = []
    for cat, ns in group_tools(names):
        blurb = _FAMILY_BLURB.get(cat) or (
            f"tools from the external server '{cat[4:]}'"
            if cat.startswith("mcp:") else cat)
        inner = []
        for n in ns:
            if n in _SHELF_KEEP_DESC or "." in n:
                d = _shelf_first_sentence(n)
                inner.append(f"{n} ({d})" if d else n)
            else:
                inner.append(n)
        out.append(f"[{cat} — {blurb}]: " + ", ".join(inner))
    return out


def tool_load_tools(names):
    if isinstance(names, str):
        names = [names]
    allowed = _allowed_tools()
    found, missing = [], []
    for n in (names or []):
        n = (n or "").strip()
        if n in allowed:
            p = TOOL_PARAMS.get(n, {"properties": {}, "required": []})
            found.append({"name": n, "description": allowed[n].get("desc", ""),
                          "parameters": {"type": "object",
                                         "properties": p.get("properties", {}),
                                         "required": p.get("required", [])}})
        elif n:
            missing.append(n)
    out = {"tools": found, "loaded": [f["name"] for f in found]}
    if missing:
        pool = list(allowed)
        import difflib
        out["not_found"] = {m: difflib.get_close_matches(m, pool, 3, 0.5)
                            for m in missing}
        try:
            titles = _skill_titles()
        except Exception:
            titles = []
        son, served = {}, []
        for m in missing:
            cand = difflib.get_close_matches(m, titles, 1, 0.6) or \
                difflib.get_close_matches(m.split(" — ")[0].strip(), titles, 1, 0.6)
            if not cand:
                continue
            son[m] = cand[0]
            sk = tool_use_skill(cand[0])
            if sk.get("content"):
                served.append({"title": sk.get("skill", cand[0]),
                                 "content": sk["content"]})
        if son:
            out["these_are_skills"] = son
            if served:
                out["skills"] = served
                out["note"] = ("Not tools — SKILLS, and here they are, in "
                               "full. FOLLOW what they say: a saved skill "
                               "exists because the generic answer was not good "
                               "enough. Do not search for what it already "
                               "tells you. To pull another one, use_skill is "
                               "now loaded.")
            else:
                out["note"] = ("Not tools — SKILLS. Fetch one with "
                               "use_skill(search_term=\"<title>\").")
            if "use_skill" in allowed and not any(f["name"] == "use_skill"
                                                  for f in found):
                p = TOOL_PARAMS.get("use_skill", {"properties": {}, "required": []})
                found.append({"name": "use_skill",
                              "description": allowed["use_skill"].get("desc", ""),
                              "parameters": {"type": "object",
                                             "properties": p.get("properties", {}),
                                             "required": p.get("required", [])}})
                out["loaded"] = [f["name"] for f in found]
    return out


_PICTURE_TOOLS = ("generate_image", "edit_image")


def _unavailable_tools():
    return set(_PICTURE_TOOLS) if not _image_cfg()[0] else set()


def _allowed_tools():
    _off = _blocked_tools | _unavailable_tools()
    if not _off:
        return TOOLS
    return {k: v for k, v in TOOLS.items() if k not in _off}


def _turn_tool_policy(tab_blocked=(), local_only=False):
    tab = frozenset(str(n) for n in (tab_blocked or ()) if n)

    def is_blocked(name):
        return name in _blocked_tools or name in tab

    if local_only:
        allowed = _local_only_toolset() - tab
    elif tab:
        allowed = set(_allowed_tools()) - tab
    else:
        allowed = None
    return is_blocked, allowed


def _save_firewall():
    try:
        sql = _get_sql()
        if sql is None:
            print("[FIREWALL] ⚠ no SQLCipher store — policy NOT saved")
            return
        prev = next(iter(sql.load("firewall")), {})
        sql.upsert("firewall", "policy", {
            "uuid": "policy",
            "blocked": sorted(_blocked_tools),
            "rev": int(prev.get("rev", 0)) + 1,
            "updated_at": datetime.now().isoformat()})
    except Exception as e:
        print(f"[FIREWALL] Error saving: {e}")


@app.get("/firewall")
def get_firewall():
    return {"blocked": sorted(_blocked_tools),
            "all_tools": {name: t.get("desc", "") for name, t in TOOLS.items()}}


@app.post("/firewall")
def set_firewall(req: FirewallReq):
    global _blocked_tools
    _blocked_tools = set(req.blocked)
    _save_firewall()
    print(f"[FIREWALL] Updated: {_blocked_tools or 'none blocked'}")
    return {"blocked": sorted(_blocked_tools)}


@app.get("/tools")
def get_tools():
    defs = []
    for name, t in TOOLS.items():
        if name in _blocked_tools:
            continue
        params = TOOL_PARAMS.get(name, {"properties": {}})
        defs.append({
            "type": "function",
            "function": {
                "name": name,
                "description": t["desc"],
                "parameters": {
                    "type": "object",
                    "properties": params.get("properties", {}),
                    "required": params.get("required", []),
                },
            },
        })
    return {"tools": defs}


class CodeJudgeReq(BaseModel):
    command: str = ""
    expect: str
    output: str = ""


_TREE_SKIP = {".git", "node_modules", "__pycache__", "venv", ".venv",
              "dist", "build", ".mypy_cache", ".pytest_cache", "target"}


@app.get("/code/tree")
def code_tree_ep(root: str, limit: int = 4000):
    root = os.path.expanduser(root or "")
    if not root or not os.path.isdir(root):
        return {"root": root, "exists": False, "dirs": [], "files": [], "truncated": False}
    dirs, files, cut = [], [], False
    for base, ds, fs in os.walk(root):
        ds[:] = sorted(d for d in ds if d not in _TREE_SKIP and not d.startswith("."))
        rel = os.path.relpath(base, root)
        for d in ds:
            dirs.append(os.path.normpath(os.path.join(rel, d)))
        for f in sorted(fs):
            if f.startswith(".") or f.endswith((".pyc", ".so", ".o")):
                continue
            files.append(os.path.normpath(os.path.join(rel, f)))
            if len(files) >= limit:
                cut = True
                break
        if cut:
            break
    return {"root": root, "exists": True, "dirs": dirs, "files": files, "truncated": cut}


@app.get("/code/file")
def code_file_ep(path: str):
    import base64 as _b64
    p = os.path.expanduser(path or "")
    try:
        raw = open(p, "rb").read()
        return {"path": p, "b64": _b64.b64encode(raw).decode(),
                "mtime": os.path.getmtime(p)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=404)


class CodeStatReq(BaseModel):
    paths: list = []


@app.post("/code/stat")
def code_stat_ep(req: CodeStatReq):
    out = {}
    for p in req.paths[:500]:
        q = os.path.expanduser(p or "")
        try:
            out[p] = {"exists": True, "isdir": os.path.isdir(q), "mtime": os.path.getmtime(q),
                      "empty": os.path.isdir(q) and not any(True for _ in os.scandir(q))}
        except OSError:
            out[p] = {"exists": False, "isdir": False, "mtime": None, "empty": False}
    return {"stat": out}


class CodePathReq(BaseModel):
    path: str
    action: str = ""


@app.post("/code/mkdir")
def code_mkdir_ep(req: CodePathReq):
    p = os.path.expanduser(req.path or "")
    try:
        os.makedirs(p, exist_ok=True)
        return {"ok": True, "path": p}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/code/git")
def code_git_ep(req: CodePathReq):
    import subprocess as _sp
    p = os.path.expanduser(req.path or "")
    if not os.path.isdir(p):
        return JSONResponse({"error": f"no such folder: {p}"}, status_code=404)
    nc = __import__("hoplita_paths").no_console()
    try:
        if req.action == "init":
            r = _sp.run(["git", "-C", p, "init"], capture_output=True, text=True, timeout=30, **nc)
            return {"ok": r.returncode == 0, "out": (r.stdout + r.stderr).strip()}
        r = _sp.run(["git", "-C", p, "rev-parse", "--git-dir"], capture_output=True,
                    text=True, timeout=10, **nc)
        return {"is_git": r.returncode == 0}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/code/judge")
def code_judge(req: CodeJudgeReq):
    import fed_agent
    v = fed_agent.judge_output(
        lambda m, tl: _llm_chat_with_tools(m, tl),
        req.expect, req.output or "", command=req.command)
    if v is None:
        return {"judged": False, "ok": None, "why": ""}
    return {"judged": True, "ok": bool(v[0]), "why": v[1]}


@app.post("/call")
def call_tool(req: ToolCallReq):
    if req.tool == "use_tool":
        _a = req.arguments if isinstance(req.arguments, dict) else {}
        _inner = _a.get("arguments", {})
        if isinstance(_inner, str):
            try:
                _inner = json.loads(_inner)
            except Exception:
                _inner = {}
        _real = str(_a.get("name", "") or "").strip()
        if not _real or _real == "use_tool":
            return {"tool": req.tool, "duration_s": 0,
                    "result": {"error": "use_tool needs `name` (a tool from the catalogue) "
                                        "and `arguments` (an object)"}}
        req.tool = _real
        req.arguments = _inner if isinstance(_inner, dict) else {}
    if req.tool in _blocked_tools:
        print(f"[FIREWALL] 🛡 Blocked: {req.tool}")
        return {"tool": req.tool,
                "result": {"error": f"🛡 FIREWALL: '{req.tool}' blocked"},
                "duration_s": 0}
    if req.tool not in TOOLS:
        raise HTTPException(404, f"Tool '{req.tool}' does not exist. Do not guess "
                                 "names: use one from the catalogue.")
    t0 = time.time()
    try:
        _prev = {k: getattr(_TURN_SESSION, k, None) for k in _TURN_SNAPSHOT_FIELDS}
        _snap = _TURN_SNAPSHOTS.get((getattr(req, "turn_id", "") or "").strip())
        if _snap:
            for k, v in _snap.items():
                setattr(_TURN_SESSION, k, v)
        _req_proj = (getattr(req, "code_project", "") or "").strip()
        if _req_proj:
            _TURN_SESSION.project = _req_proj
        with _AGENT_TURNS_LOCK:
            _turn_cancel = _AGENT_TURNS.get((getattr(req, "turn_id", "") or "").strip())
        try:
            with _orchestration_ctx(OrchestrationCtx(
                    cancel=_turn_cancel.is_set if _turn_cancel is not None else None)):
                result = TOOLS[req.tool]["fn"](req.arguments)
        finally:
            for k, v in _prev.items():
                setattr(_TURN_SESSION, k, v)
    except KeyError as e:
        import fed_agent as _fa
        _k = e.args[0] if e.args else None
        _spec = TOOL_PARAMS.get(req.tool) or {}
        if isinstance(_k, str) and _k in (_spec.get("properties") or {}):
            result = _fa.args_complaint(req.tool, req.arguments, e, _spec)
        else:
            import traceback; traceback.print_exc()
            result = {"error": str(e)}
    except Exception as e:
        import traceback; traceback.print_exc()
        result = {"error": str(e)}
    dur = time.time() - t0
    print(f"[MCP] {req.tool}({json.dumps(req.arguments, ensure_ascii=False)[:80]}) → {dur:.1f}s")
    try:
        json.dumps(result)
    except (TypeError, ValueError):
        result = {"error": f"Resultado no serializable: {str(result)[:200]}"}
    return {"tool": req.tool, "result": result, "duration_s": round(dur, 2)}



import queue as _queue
import contextlib as _contextlib


def _tr(key, **kwargs):
    try:
        from hoplita_i18n import t as _t
        return _t(key, **kwargs)
    except Exception:
        return key


_MEM_SNAPSHOTS = {}


def _snapshot_key():
    ids = _turn_conv_ids()
    return ids[0] if ids else ""


def memory_snapshot_reset(key):
    n = 0
    for k in {key, *_turn_conv_ids()}:
        if k and _MEM_SNAPSHOTS.pop(k, None) is not None:
            n += 1
    return n


class MemorySnapshotReq(BaseModel):
    session: str = ""
    action: str = "reset"


@app.post("/agent/memory_snapshot")
def agent_memory_snapshot(req: MemorySnapshotReq):
    return {"reset": memory_snapshot_reset((req.session or "").strip()),
            "held": len(_MEM_SNAPSHOTS)}


def _inject_block(messages, block, stable=True):
    out = list(messages)
    import fed_agent as _fa_tag
    glued = _fa_tag.HARNESS_TAG + "\n" + block
    if not stable:
        u = next((i for i in range(len(out) - 1, -1, -1)
                  if out[i].get("role") == "user"), -1)
        if u < 0:
            return _inject_block(messages, block, stable=True)
        prev = out[u].get("content", "")
        if isinstance(prev, str):
            out[u] = {**out[u], "content": glued + "\n\n---\n\n" + prev}
            return out
        if isinstance(prev, list):
            out[u] = {**out[u], "content": [{"type": "text",
                                             "text": glued + "\n\n---\n\n"}]
                      + list(prev)}
            return out
        return messages
    sys_i = next((i for i, m in enumerate(out) if m.get("role") == "system"), -1)
    idx, sep_before = (sys_i, True) if sys_i >= 0 else (
        next((i for i, m in enumerate(out) if m.get("role") == "user"), -1), False)
    if idx < 0:
        return messages
    existing = out[idx].get("content", "")
    if isinstance(existing, str):
        out[idx] = {**out[idx],
                    "content": (existing + "\n\n" + block) if sep_before
                    else (glued + "\n\n---\n\n" + existing)}
    elif isinstance(existing, list):
        part = {"type": "text",
                "text": ("\n\n" + block) if sep_before else (glued + "\n\n---\n\n")}
        out[idx] = {**out[idx],
                    "content": (list(existing) + [part]) if sep_before
                    else ([part] + list(existing))}
    else:
        return messages
    return out


def _prepend_system(messages, directive):
    out = list(messages)
    if out and out[0].get("role") == "system":
        existing = out[0].get("content", "")
        if isinstance(existing, str):
            out[0] = {**out[0], "content": directive + "\n\n" + existing}
            return out
    out.insert(0, {"role": "system", "content": directive})
    return out


_SKILL_WHEN = re.compile(r"^\s*hoplita-when:\s*(\S.*?)\s*$", re.M | re.I)


def _skill_declaration(note):
    title_ = (note.get("title", "") or "").strip()
    cuerpo = note.get("content", "") or ""
    m = _SKILL_WHEN.search(cuerpo)
    if m:
        return f"{title_} {m.group(1)}"[:400]
    first_ = ""
    for line_ in cuerpo.splitlines():
        l = line_.strip().lstrip("#").strip()
        if len(l) > 15:
            first_ = l
            break
    return f"{title_} {first_}"[:400]


WORKING_MEMORY = "working-memory"
_CONV_SCOPED = (WORKING_MEMORY,)
_WM_TAG = "[conversation: "


def _wm_tag_line(ids=None):
    return _WM_TAG + " ".join(ids if ids is not None else _turn_conv_ids()) + "]"


def _wm_sessions_of(note):
    c = (note.get("content") or "")
    if not c.startswith(_WM_TAG):
        return None
    end = c.find("\n")
    raw = c[len(_WM_TAG):(end if end > 0 else len(c))].rstrip("]").strip()
    return {i for i in raw.split() if i}


def _wm_body(note):
    c = (note.get("content") or "")
    return c.split("\n", 1)[1] if c.startswith(_WM_TAG) and "\n" in c else c


def _wm_text(note):
    c = note.get("content") or ""
    return ("\n" + c.split("\n", 1)[1]) if c.startswith(_WM_TAG) and "\n" in c else c


def _passage_rows(found, category_of):
    return [{"category": category_of(p_["doc"]), "title": p_["doc"]["title"],
             "lines": f"{p_['from']}-{p_['to']}", "matched": p_["matched"],
             "text": p_["text"]} for p_ in found["passages"]]


def tool_search_working_memory(query, title="", regex=False, context=1, k=5):
    if notes_mgr is None:
        return {"error": "notes store not ready"}
    if not (query or "").strip():
        return {"error": "query is empty: say what to look for (words, a "
                         "name, a figure; or a pattern with regex=true)"}
    try:
        context = max(0, min(5, int(context)))
    except Exception:
        context = 1
    try:
        k = max(1, min(20, int(k)))
    except Exception:
        k = 5
    ids_now = set(_turn_conv_ids())
    notes = [n_ for n_ in (notes_mgr.get_notes(WORKING_MEMORY) or [])
             if ids_now and (_wm_sessions_of(n_) or set()) & ids_now]
    if title:
        _want = title.strip().lower()
        exact = [n_ for n_ in notes if (n_.get("title") or "").strip().lower() == _want]
        notes = exact or [n_ for n_ in notes
                          if _want in (n_.get("title") or "").strip().lower()]
    if not notes:
        return {"results": [], "count": 0,
                "note": ("no full text by that title in this conversation's "
                         "working memory" if title else
                         "this conversation's working memory is empty: no "
                         "large result was digested here")}
    from hoplita_search import find_passages
    found = find_passages(query, [{"title": n_.get("title", ""), "text": _wm_text(n_)}
                                  for n_ in notes],
                          regex=bool(regex), context=context, k=k,
                          budget=_note_window_chars() - 400)
    out = [{k_: v for k_, v in r_.items() if k_ != "category"}
           for r_ in _passage_rows(found, lambda d: WORKING_MEMORY)]
    if not out:
        return {"results": [], "count": 0,
                "note": "nothing matches in the stored text"
                        + (" (try other words, or regex=true for a pattern)"
                           if not regex else " (try words without regex)")}
    return {"results": out, "count": len(out),
            **({"more": f"{found['total'] - len(out)} more passage(s): narrow "
                        f"the query, context=0, or title= one text"}
               if found["total"] > len(out) else {}),
            "read_more": "read_note(category='working-memory', title=<title>, "
                         "start_line, end_line) around a passage's lines"}


def wm_forget(session_uuid, orphans=False):
    if notes_mgr is None:
        return 0
    want = ({session_uuid} if isinstance(session_uuid, str)
            else set(session_uuid or ()))
    want = {(i or "").strip() for i in want}
    gone = 0
    for cat in _CONV_SCOPED:
        try:
            for n_ in list(notes_mgr.get_notes(cat) or []):
                ids = _wm_sessions_of(n_)
                hit = (ids & want) if ids else (
                    orphans if not ids else False)
                if hit:
                    notes_mgr.delete_note(cat, n_["id"])
                    gone += 1
        except Exception:
            pass
    return gone


_WINDOW_TAB_ID = re.compile(r"^local(:\d+)?$")


def wm_sweep_window_ids():
    if notes_mgr is None:
        return 0
    gone = 0
    for cat in _CONV_SCOPED:
        try:
            for n_ in list(notes_mgr.get_notes(cat) or []):
                ids = _wm_sessions_of(n_)
                if ids and all(_WINDOW_TAB_ID.match(i) for i in ids):
                    notes_mgr.delete_note(cat, n_["id"])
                    gone += 1
        except Exception:
            pass
    return gone


def _skills_category():
    return (CONFIG.get("BROWSER", "default_skill_category", fallback="skills")
            if CONFIG else "skills")


def _skills_categories():
    _child = hoplita_children.skills_scope()
    if _child:
        return list(_child)
    out = [_skills_category()]
    try:
        for cat, on in (notes_mgr.get_skill_cats() or {}).items():
            if on and cat and cat not in out:
                out.append(cat)
    except Exception:
        pass
    return out


def pick_skill_for(query, tail=None, extra=None):
    idx = skills_index(extra, query=query)
    if not idx or not (query or "").strip():
        return None
    _lines = []
    for m in (tail or [])[-6:]:
        _c = m.get("content", "")
        if isinstance(_c, list):
            _c = " ".join(p_.get("text", "") for p_ in _c
                          if isinstance(p_, dict) and p_.get("type") == "text")
        _c = str(_c or "").strip().replace("\n", " ")
        if _c and m.get("role") in ("user", "assistant"):
            _lines.append(f"  {m['role']}: {_c[:240]}")
    r = _llm_call_json(_PICK_SKILL_PROMPT.format(
        query=query[:600], index=idx,
        tail="\n".join(_lines) or "  (nothing yet: this is the first message)"),
        max_tokens=400, timeout=30, breaker=False, quiet=True)
    title_ = str((r or {}).get("skill", "") or "").strip()
    if not title_ or title_.upper() == "NONE":
        return None
    for s_ in (extra or []):
        if (s_.get("title", "").strip().lower() == title_.lower()
                and s_.get("content")):
            return {"title": s_["title"], "content": s_["content"]}
    try:
        sk = tool_use_skill(title_)
    except Exception:
        return None
    if not sk.get("content"):
        return None
    return {"title": sk.get("skill", title_), "content": sk["content"]}


def _skill_titles():
    try:
        return [(n.get("title", "") or "").strip()
                for n in (notes_mgr.get_notes(_skills_category()) or [])
                if (n.get("title", "") or "").strip()]
    except Exception:
        return []


def skills_index(extra=None, query=None):
    notas = []
    for _cat in _skills_categories():
        try:
            notas.extend(list(notes_mgr.get_notes(_cat) or []))
        except Exception:
            pass
    rows, _seen = [], set()
    for n_ in list(notas) + list(extra or []):
        t_ = (n_.get("title", "") or "").strip()
        if not t_ or t_ in _seen:
            continue
        _seen.add(t_)
        d = _skill_declaration(n_)[len(t_):].strip(" —-:")
        rows.append({"title": t_,
                      "line": f"{t_}" + (f" — {d[:110]}" if d else "")})
    rows.sort(key=lambda f: f["line"])
    whole = "; ".join(f["line"] for f in rows)

    _tope = (CONFIG.getint("BROWSER", "skills_index_budget", fallback=6000)
             if CONFIG else 6000)
    if _tope <= 0 or len(whole) <= _tope:
        return whole
    _k = max(1, _tope // 140)
    _sel = []
    if (query or "").strip():
        import hoplita_search as _hs
        _sel = [h["line"] for h in
                _hs.rank_by_words(query, rows, lambda f: f["line"], k=_k)]
    if not _sel:
        _sel = [f["line"] for f in rows[:_k]]
    return ("; ".join(_sel) + " "
            + _tr("inject.skills_index_partial",
                  shown=len(_sel), total=len(rows)))


_SKILL_VERIFY = re.compile(r"^\s*hoplita-verify:\s*(\S.*?)\s*$", re.M)


def _skill_verify(pinned_skills):
    for sk in (pinned_skills or []):
        m = _SKILL_VERIFY.search(sk.get("content", "") or "")
        if m:
            return m.group(1)
    return ""


def _injected_names(items, limit=6, width=64):
    out = []
    for it in items:
        one = " ".join(str(it).split())
        out.append(one[:width] + "…" if len(one) > width else one)
        if len(out) >= limit:
            out.append("…")
            break
    return " · ".join(out)


_PROJECT_MD_CAP = 3000
_JOURNAL_TAIL_CAP = 2500
_RULES_CAP = 4000


def _project_category(root):
    root = (root or "").strip().rstrip("/\\")
    if not root:
        return ""
    try:
        sql = _get_sql()
        if sql is not None:
            alias = sql.meta_get(f"code_name_for:{os.path.abspath(root)}")
            if alias:
                return str(alias)
    except Exception:
        pass
    return os.path.basename(root)


def _project_notes(root):
    cat = _project_category(root)
    if not cat or notes_mgr is None:
        return "", []
    try:
        return cat, list(notes_mgr.get_notes(cat) or [])
    except Exception:
        return cat, []


def _project_resolve(path):
    raw = (path or "").strip()
    if not raw:
        return ""
    if os.path.isdir(raw):
        return _project_category(os.path.abspath(raw))
    cat = _project_category(raw)
    try:
        if notes_mgr is not None and cat in (notes_mgr.get_categories() or []):
            return cat
    except Exception:
        pass
    return ""


def _project_memory_ensure(root):
    cat, notes = _project_notes(root)
    if not cat or notes_mgr is None:
        return False
    born = False
    if not notes:
        tool_add_note(cat, "rules", _tr("code.memory_tpl_rules"), _system=True)
        tool_add_note(cat, "spec", _tr("code.memory_tpl_spec"), _system=True)
        tool_add_note(cat, "future", _tr("code.memory_tpl_future"), _system=True)
        born = True
    else:
        _ns = _nc = _nt = 0
        try:
            _ns, _nc = _fold_legacy_notes(cat)
        except Exception as _e:
            print(f"[PROJECT] fold of legacy notes: {_e}", file=sys.stderr)
        for _ttl, _tpl in (("spec", "code.memory_tpl_spec"), ("future", "code.memory_tpl_future")):
            if _project_note(cat, _ttl) is None:
                tool_add_note(cat, _ttl, _tr(_tpl), _system=True)
        try:
            _nt = _board_fold(cat)
        except Exception as _e:
            print(f"[PROJECT] board fold: {_e}", file=sys.stderr)
        if _ns or _nc or _nt:
            try:
                tool_project_journal(root, f"Memory brought to the four notes: "
                                           f"{_ns} spec note(s) folded into 'spec', "
                                           f"{_nc} change note(s) into 'future', "
                                           f"{_nt} task(s) moved from the board to 'future'.")
            except Exception:
                pass
    try:
        notes_mgr.set_realm(cat, "dev")
    except Exception:
        pass
    try:
        sql = _get_sql()
        if sql is not None and (root or "").strip():
            sql.meta_set(f"code_path:{cat}",
                         os.path.abspath(root.strip()))
    except Exception:
        pass
    if todo_mgr is not None:
        try:
            todo_mgr.add_project(cat)
        except Exception:
            pass
    return born


def _project_local_only(root):
    cat = _project_category(root)
    try:
        return bool(cat and notes_mgr is not None
                    and (notes_mgr.get_local_only() or {}).get(cat))
    except Exception:
        return False


def _turn_local_only(tab_local_only, code_project):
    return bool(tab_local_only) or _project_local_only(code_project)


def _sub_agent_wall(ctx, names):
    tab = ctx.blocked
    lock = bool(getattr(ctx, "local_only", False))

    def is_blocked(n):
        return (n in _blocked_tools or n in tab
                or (lock and n.startswith("fed_")))
    allowed = {n for n in names if n not in tab
               and not (lock and n.startswith("fed_"))}
    return is_blocked, allowed


_MEMORY_WRITE_TOOLS = frozenset(("add_note", "update_note", "add_task",
                                 "task_depends", "update_task_progress",
                                 "project_journal", "project_write"))
_CODE_WRITE_TOOLS = frozenset(("code_apply_patch", "code_write_file",
                               "code_reject_hunk", "git_commit", "git_branch",
                               "run_command"))
_CODE_EDIT_TOOLS = frozenset(("code_apply_patch", "code_write_file",
                              "git_commit"))
_LIST_LINE = re.compile(r"^\s*(?:[-*•]|\d+[.)]|#{1,6}\s)\s*\S")


def _looks_like_steps(text):
    return sum(1 for l in (text or "").splitlines() if _LIST_LINE.match(l)) >= 3


def _fenced_share(text):
    text = text or ""
    if not text.strip() or "```" not in text:
        return 0.0
    inside, total, i = 0, len(text), 0
    parts = text.split("```")
    for k, part in enumerate(parts):
        if k % 2 == 1:
            inside += len(part) + 6
    return min(1.0, inside / max(1, total))


def _plan_gate_for(cat):
    if not cat:
        return None
    import fed_agent as _fa

    def gate(reply, trace):
        names = {str(s.get("tool")) for s in (trace or []) if isinstance(s, dict)}
        if names & _MEMORY_WRITE_TOOLS:
            return None
        if names & _CODE_EDIT_TOOLS:
            rows = _project_open_task_rows(cat, cap=8)
            if not rows:
                return None
            return _tr("inject.progress_gate", cat=cat,
                       token=_fa.PROGRESS_GATE_KEEP, tasks="\n".join(rows))
        if names & _CODE_WRITE_TOOLS:
            return None
        if not _looks_like_steps(reply):
            return None
        if "\n@@ " in reply or "diff --git" in reply:
            return None
        if _fenced_share(reply) > 0.5:
            return None
        return _tr("inject.plan_gate", cat=cat, token=_fa.PLAN_GATE_KEEP)
    return gate


def _local_only_toolset():
    return {k for k in _allowed_tools() if not k.startswith("fed_")}


def _project_skills(root):
    _, notes = _project_notes(root)
    out = []
    for n_ in notes:
        t_ = (n_.get("title", "") or "").strip()
        if not t_.lower().startswith("skill:"):
            continue
        body = (n_.get("content") or "").strip()
        if not body:
            continue
        out.append({"title": t_.split(":", 1)[1].strip() or t_,
                    "content": body[:8000]})
        if len(out) >= 20:
            break
    return out


def _project_note_is_virgin(text):
    t_ = (text or "").strip()
    if not t_:
        return True
    fields = []
    for line in t_.splitlines():
        line = line.strip()
        if line.startswith("-") and ":" in line:
            fields.append(line.partition(":")[2].strip())
    if len(fields) < 2:
        return False
    return not any(fields)


def _project_memory_block(root):
    cat, notes = _project_notes(root)
    stats = {"blocks": 0, "pending": 0, "sections": {}}
    if not cat or not notes:
        return "", stats

    def _one(title):
        for n_ in notes:
            if (n_.get("title", "") or "").strip().lower() == title:
                return (n_.get("content") or "").strip()
        return ""

    parts = []
    rules = _one("rules")
    if rules:
        if len(rules) > _RULES_CAP:
            rules = (rules[:_RULES_CAP]
                     + "\n…(cut)\nWARNING: the 'rules' note exceeds the "
                       "budget and arrived cut. Ask the user to shorten it: "
                       "a rule not shown is not held.")
        parts.append("── 'rules' — STANDING ORDERS from the user for this "
                     "project. They override your defaults; when one applies, "
                     "obey it without being reminded ──\n" + rules)
        stats["sections"]["rules"] = parts[-1]
    spec = _one("spec")
    if spec:
        if _project_note_is_virgin(spec):
            parts.append(
                "── 'spec' — STILL THE EMPTY TEMPLATE ──\n"
                "Nobody has written what this project is. BEFORE (or alongside) "
                "whatever this turn asks, look at the repo (code_index, git_log, "
                "the README; distill an AGENTS.md / CLAUDE.md if there is one) and "
                f"write the truth in short lines: project_write('{root}', "
                "note='spec', text=<what it is / how it builds / how it is "
                "tested / status>, replace=true). Tell the user you are doing it.")
            stats["virgin"] = True
            stats["sections"]["spec"] = parts[-1]
        else:
            _legacy = ""
            if "## Folded from the old design" in spec:
                live, _, rest = spec.partition("## Folded from the old design")
                spec = live.rstrip()
                _legacy = (f"\n…plus {rest.count(chr(10) + '### ')} section(s) folded "
                           f"from the old design (read_note(category='{cat}', "
                           f"title='spec') for them; search_notes finds inside).")
            if len(spec) > _PROJECT_MD_CAP:
                spec = (spec[:_PROJECT_MD_CAP]
                        + "\n…(truncated — read_note for the rest; and the spec "
                          "is too long: rewrite it shorter with project_write "
                          "replace=true)")
            spec += _legacy
            parts.append("── 'spec' — the CURRENT TRUTH: what this is and how "
                         "it must behave, in short annotations. Read before "
                         "changing behaviour; when a change alters a truth, "
                         "fix the line ──\n" + spec)
            stats["sections"]["spec"] = parts[-1]
    fut = _one("future")
    if fut and not _project_note_is_virgin(fut):
        stats["pending"] = len(re.findall(r"^\s*(?:[-*]|\d+[.)]|##)\s*\S", fut, re.M))
        if len(fut) > _FUTURE_CAP:
            fut = fut[:_FUTURE_CAP] + "\n…(truncated — read_note for the rest)"
        parts.append("── 'future' — what is PENDING (not on the board: the board "
                     "is only for what you are doing now). Take from here when "
                     "you start something; strike out what is done or moot ──\n"
                     + fut)
        stats["sections"]["future"] = parts[-1]
    jr = _one("journal")
    if jr:
        if len(jr) > _JOURNAL_TAIL_CAP:
            cut = jr[-_JOURNAL_TAIL_CAP:]
            j = cut.find("\n## ")
            jr = ("…(older entries trimmed — read_note for the past)\n"
                  + (cut[j + 1:] if j >= 0 else cut))
        parts.append("── 'journal' (latest at the end) ──\n" + jr)
        stats["sections"]["journal"] = parts[-1]
    if not parts:
        return "", stats
    stats["blocks"] = len(parts)
    head = (
        f"[PROJECT MEMORY] Folder '{cat}' is this project's memory across "
        "sessions and faces (a shared folder shares it). FOUR notes and "
        "nothing else: rules (the user's, read only), spec (settled truths, "
        "short lines — project_write note='spec'), future (what is pending — "
        "project_write note='future'), journal (what happened — "
        "project_journal). The board holds only what is being done NOW.")
    return head + "\n\n" + "\n\n".join(parts), stats


_OPEN_TASK_ROWS_CAP = 15


def _project_open_task_rows(cat, cap=_OPEN_TASK_ROWS_CAP):
    if not cat or todo_mgr is None:
        return []
    try:
        tasks = list(todo_mgr.get_tasks(cat) or [])
    except Exception:
        return []
    by_uuid = {tk.get("uuid"): tk for tk in tasks if tk.get("uuid")}
    rows = []
    for tk in tasks:
        title = (tk.get("title") or "").strip()
        if not title or title.lower().startswith("change:"):
            continue
        prog = int(tk.get("progress") or 0)
        if prog >= 100:
            continue
        deps = [d for d in (tk.get("deps") or []) if d in by_uuid]
        blocked = [by_uuid[d] for d in deps
                   if int(by_uuid[d].get("progress") or 0) < 100]
        state = (f"BLOCKED by {', '.join((b.get('title') or '')[:40] for b in blocked[:3])}"
                 if blocked else "READY")
        rows.append(f"- {title[:90]} — {prog}% · {state}")
    if len(rows) > cap:
        more = len(rows) - cap
        rows = rows[:cap] + [f"…and {more} more (get_todo_context lists them all)"]
    return rows


def _code_project_note(path):
    if not (path or "").strip():
        return ""
    cat = ""
    try:
        cat = _project_category(path) or ""
    except Exception:
        pass
    memo = ""
    if cat:
        memo = (
            f"\nITS MEMORY IS YOURS TO WRITE — folder and task project are both "
            f"'{cat}'. FOUR notes, no others (add_note refuses anything else):\n"
            f"- 'rules': the user's standing orders. Read them; never write them.\n"
            f"- 'spec': what this project IS and how it must behave, in short "
            f"annotations — project_write(note='spec', text=<one truth, one "
            f"breath>). When a change alters a truth, fix that line (replace=true "
            f"rewrites the whole spec shorter). Never an essay: the how and why "
            f"go to the journal.\n"
            f"- 'future': what is pending and NOT being done now — "
            f"project_write(note='future', text=<one line per item>). A plan "
            f"lives here, not as tasks.\n"
            f"- 'journal': what happened — project_journal, only significant "
            f"events (a decision, a trap, what changed).\n"
            f"THE BOARD IS SMALL AND IMMEDIATE: at most {_OPEN_TASKS_CAP} open "
            f"tasks per project (add_task refuses beyond), only what is being "
            f"done in this session AND that the user asked for or approved in "
            f"this conversation — an idea of yours is a line in 'future', never a "
            f"task; order between them → task_depends (the board shows "
            f"BLOCKED or READY). At the end of EVERY turn look at the open "
            f"tasks in front of you: done → update_task_progress 100; no longer "
            f"relevant → delete_task or move it to 'future'. Nothing sits there "
            f"for weeks.\n"
            f"A fact about THE PROJECT (machines, versions, paths) goes to 'spec' "
            f"or the journal — NEVER add_fact: that is the person's memory, in "
            f"every conversation.\n"
            f"To SHOW the user a change (a review, a design walk-through) write a "
            f"whiteboard (wb_get_instructions): it reads these notes, holds no "
            f"specs or tasks of its own, and journals itself when finished.\n"
            f"ENGINEERING JUDGMENT, not bookkeeping: few words, few tasks, and "
            f"read the project's 'rules' first — if they ask for confirmation "
            f"before creating, ask and wait.\n"
            f"PLAN vs DO: an order («do X», «fix Y») is done now, in one go, no "
            f"tasks — Never turn an order into a plan. Asked to PLAN («let us "
            f"think about…», an architecture, a change of scale): write it into "
            f"'future' BEFORE you answer, then a short reply that says so. The "
            f"board draws the graph — no canvas, no HTML, no ASCII boxes.")
    return (f"[CODE MODE] Project open: {path} — the default path for the "
            f"code_* and git_* tools; ask only if the user names another.\n"
            f"- Edit with code_apply_patch (validated, undoable with code_undo), "
            f"not shell redirection. Before a substantial change, git_branch; "
            f"commit there; when the work is done and verified, git_merge it "
            f"into main — the trunk is the user's, and that is how work "
            f"reaches it.\n"
            f"- Shared project: a peer's commits wait in QUARANTINE, never on "
            f"the user's branch. fed_code_incoming lists them, fed_code_take "
            f"integrates when the user says so, fed_code_push sends theirs, "
            f"fed_code_state reports. Never raw git over refs/hoplita.\n"
            f"- What the project IS: its 'spec' above IS the GPS — "
            f"answer from it first. Only what it lacks, or looks stale against "
            f"the code, gets the SURVEY: README/*.md, the dependency manifest, "
            f"the entry point, outlines of the biggest sources (code_index "
            f"lists by size). Never from memory of OTHER projects, never ask "
            f"which file.\n"
            f"- Before adding: read the code that CONSUMES it — its accepted "
            f"values are the constraints. Re-read LIQUID data (versions, sizes, "
            f"counts) before stating or editing them; when the spec lies, "
            f"fix the line (project_write note='spec')."
            + memo)

def prepare_turn_messages(messages, pinned_skills=None, emit=None,
                          code_project="", local_only=False):
    _given = []
    emit = emit or (lambda ev: None)

    def note(key, **args):
        full = args.pop("_full", None)
        ev = {"event": "prep", "note": {"key": key, "args": args}}
        if full:
            ev["note"]["full"] = list(full)
        emit(ev)

    query = ""
    for m in reversed(messages or []):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                query = c
            elif isinstance(c, list):
                query = " ".join(p.get("text", "") for p in c
                                 if isinstance(p, dict) and p.get("type") == "text")
            break

    out = list(messages or [])

    out = _inject_block(out, _tr("inject.order"), stable=True)
    if _unavailable_tools() & set(_PICTURE_TOOLS):
        out = _inject_block(out, _tr("inject.no_pictures"), stable=True)

    _skey = _snapshot_key()
    _snap = _MEM_SNAPSHOTS.get(_skey) if _skey else None
    _new_snap = {} if (_skey and _snap is None) else None

    if not CONFIG or CONFIG.getboolean("LESSONS", "inject_on_chat", fallback=True):
        lessons = []
        try:
            with _lessons_lock:
                _vivas = [l for l in _lessons_data if not l.get("stale")
                          and "user_stated" not in (l.get("tags") or [])]
            _vivas.sort(key=lambda l: float(l.get("confidence", 0.5)),
                        reverse=True)
            _budget, _spent = 1400, 0
            for l in _vivas:
                if float(l.get("confidence", 0.5)) < 0.6:
                    break
                _spent += len(l.get("text", "")) + 12
                if _spent > _budget and lessons:
                    lessons.append({"text": _tr("inject.lessons_truncated",
                                                n=len(_vivas) - len(lessons)),
                                    "confidence": 1.0})
                    break
                lessons.append(l)
        except Exception:
            lessons = []
        dictated = []
        try:
            _ya = {l.get("id") for l in lessons}
            _dic = [l for l in _lessons_data
                    if "user_stated" in (l.get("tags") or [])
                    and not l.get("stale") and l.get("id") not in _ya
                    and (l.get("text") or "").strip()]
            _dic.sort(key=lambda l: l.get("updated") or l.get("created") or "", reverse=True)
            _used = 0
            for l in _dic:
                _txt_regla = l.get("text", "")
                if _used + len(_txt_regla) > RULES_BUDGET_BYTES and dictated:
                    break
                dictated.append(l)
                _used += len(_txt_regla) + 4
            _off_rules = len(_dic) - len(dictated)
        except Exception:
            dictated, _off_rules = [], 0
        _rules_block = ("\n".join([_tr("inject.rules_header")] +
                                   [f"  • {l.get('text','')}" for l in dictated]
                                   + ([_tr("inject.rules_truncated", n=_off_rules)]
                                      if _off_rules else []))
                        if dictated else "")
        _less_block = ("\n".join([_tr("inject.lessons_header")] +
                                  [f"  • ({float(l.get('confidence', 0.5)):.0%}) {l.get('text','')}"
                                   for l in lessons])
                       if lessons else "")
        _now_ids = [l.get("id") for l in dictated + lessons if l.get("id")]
        if _snap is not None and "lessons" in _snap:
            _rules_block, _less_block, _then_ids = _snap["lessons"]
            _fresh = [l for l in dictated + lessons
                      if l.get("id") and l.get("id") not in set(_then_ids)]
        else:
            _fresh = []
            if _new_snap is not None:
                _new_snap["lessons"] = (_rules_block, _less_block, _now_ids)
        if _rules_block:
            out = _inject_block(out, _rules_block, stable=True)
            _given.append(_rules_block)
            note("msg.rules_injected", n=len(dictated),
                 what=_injected_names(l.get("text", "") for l in dictated),
                 _full=[l.get("text", "") for l in dictated])
        if _less_block:
            out = _inject_block(out, _less_block, stable=True)
            note("msg.lessons_injected", n=len(lessons),
                 _full=[f"({float(l.get('confidence', 0.5)):.0%}) {l.get('text','')}"
                        for l in lessons])
        if _fresh:
            _fb = "\n".join([_tr("inject.lessons_since")] +
                             [f"  • {l.get('text','')}" for l in _fresh])
            out = _inject_block(out, _fb, stable=False)
            _given.append(_fb)

    skills = [dict(s) for s in (pinned_skills or []) if s.get("content")]
    pinned_titles = {s.get("title", "") for s in skills}
    _pskills = _project_skills(code_project)
    if CONFIG is None or CONFIG.getboolean("SKILLS", "ask_which", fallback=True):
        try:
            _chosen = pick_skill_for(query, messages, extra=_pskills)
        except Exception as _e:
            print(f"[SKILLS] could not pick one: {_e}", file=sys.stderr)
            _chosen = None
        if _chosen and _chosen["title"] not in pinned_titles:
            skills.append(_chosen)
    _idx = skills_index(_pskills, query=query)
    if _idx:
        out = _inject_block(out, _tr("inject.skills_index", titles=_idx))
    if skills:
        block = "\n".join([_tr("inject.skills_header")] +
                          [f"\n### {s.get('title','')}"
                           f"{' 📌' if s.get('title') in pinned_titles else ''}\n"
                           f"{s.get('content','')}" for s in skills])
        out = _inject_block(out, block, stable=not any(
            s.get("title") not in pinned_titles for s in skills))
        note("msg.skills_injected",
             what=_injected_names(
                 s.get("title", "?") + (" 📌" if s.get("title") in pinned_titles
                                        else " " + _tr("msg.skill_chosen_mark"))
                 for s in skills),
             _full=[s.get("title", "?") for s in skills],
             pinned=len(pinned_titles),
             auto=len(skills) - len(pinned_titles))

    if _snap is not None and "facts" in _snap:
        cat, inside, outside_n = _snap["facts"]
    else:
        try:
            cat, inside, outside_n = facts_catalogue()
        except Exception:
            cat, inside, outside_n = "", 0, 0
        if _new_snap is not None:
            _new_snap["facts"] = (cat, inside, outside_n)
            _new_snap["facts_ts"] = datetime.now().isoformat()
    if cat:
        head = _tr("inject.facts_header")
        if outside_n:
            head += "\n" + _tr("inject.facts_truncated", n=outside_n)
        out = _inject_block(out, head + "\n" + cat, stable=bool(_skey))
        _given.append(head + "\n" + cat)
        note("msg.facts_all" if not outside_n else "msg.facts_all_cut",
             n=inside, outside_n=outside_n,
             _full=[l.strip(" •") for l in cat.split("\n")])
    try:
        _shown = set(re.findall(r"• \[([^\]]+)\]", cat or ""))
        _since = ((_snap or {}).get("facts_ts") or (_new_snap or {}).get("facts_ts") or "") if _skey else ""
        _rel = facts_relevant(query if (outside_n or not _skey) else "", _shown, since=_since) \
            if (outside_n or _since) else []
        if _rel:
            _rb = _tr("inject.facts_relevant") + "\n" + "\n".join(_rel)
            out = _inject_block(out, _rb, stable=False)
            _given.append(_rb)
    except Exception as _e:
        print(f"[FACTS] relevant block: {_e}", file=sys.stderr)

    try:
        _ids_now = set(_turn_conv_ids())
        _spills = [n_ for n_ in (notes_mgr.get_notes(WORKING_MEMORY) or [])
                   if _ids_now and (_wm_sessions_of(n_) or set()) & _ids_now]
    except Exception:
        _spills = []
    if _spills:
        _sp_lines = [f"  • '{n_.get('title', '?')}' — {len(_wm_body(n_))} chars"
                     for n_ in _spills[-8:][::-1]]
        _sp_block = _tr("inject.spill_index") + "\n" + "\n".join(_sp_lines)
        out = _inject_block(out, _sp_block, stable=False)
        _given.append(_sp_block)
        note("msg.spill_index", n=len(_spills))

    out = _prepend_system(out, _tr("prompts.language_directive"))

    out = _prepend_system(out, _tr("prompts.no_false_claims"))

    out = _prepend_system(out, _tr("prompts.identity"))
    out = _inject_block(out, _tr("prompts.today",
                                 date=datetime.now().strftime("%Y-%m-%d")),
                        stable=False)
    out = _inject_block(out, _tr("prompts.machine", **_machine_facts()), stable=False)

    pool = _model_pool()
    if pool:
        lines = [_tr("chat.pool_note_header")]
        for name, m in pool.items():
            prof = m.get("profile") or _tr("planner.pool_no_profile")
            mid = f" [{m.get('model')}]" if m.get("model") else ""
            off = "" if m.get("council", True) else " " + _tr("chat.pool_no_council")
            lines.append(f'  - "{name}"{mid}: {prof}{off}')
        lines.append(_tr("chat.pool_note_rule"))
        out = _inject_block(out, "\n".join(lines))

    _cp = _code_project_note(code_project)
    if _cp:
        out = _inject_block(out, _cp)
        note("msg.code_project_injected", path=code_project)
        if _project_memory_ensure(code_project):
            note("msg.project_born", name=_project_category(code_project))
        _pm_changed = ""
        if _snap is not None and "pm" in _snap:
            _pm, _pmstats = _snap["pm"]
            try:
                _now_pm, _now_stats = _project_memory_block(code_project)
                _then = (_pmstats or {}).get("sections") or {}
                _now = (_now_stats or {}).get("sections") or {}
                _diff = [_now[k] for k in ("rules", "spec", "future", "journal")
                         if _now.get(k) and _now.get(k) != _then.get(k)]
                _gone = [k for k in _then if k not in _now]
                if _diff or _gone:
                    _pm_changed = "\n\n".join(
                        [_tr("inject.project_changed")] + _diff
                        + [f"── '{k}' — now empty or removed ──" for k in _gone])
            except Exception as _e:
                print(f"[PROJECT] could not compare the project memory: {_e}", file=sys.stderr)
        else:
            _pm, _pmstats = _project_memory_block(code_project)
            if _new_snap is not None:
                _new_snap["pm"] = (_pm, _pmstats)
        if _pm:
            out = _inject_block(out, _pm, stable=bool(_skey))
            _given.append(_pm)
            note("msg.project_memory",
                 blocks=_pmstats.get("blocks", 0),
                 changes=_pmstats.get("changes", 0), skills=len(_pskills))
        if _pm_changed:
            out = _inject_block(out, _pm_changed, stable=False)
            _given.append(_pm_changed)
        _board_rows = _project_open_task_rows(_project_category(code_project))
        if _board_rows:
            out = _inject_block(
                out, "[PROJECT BOARD] open tasks of this project — move each "
                     "one THE MOMENT it lands: update_task_progress('<title>', "
                     "100); partial work is not done\n" + "\n".join(_board_rows),
                stable=False)
        if _turn_local_only(local_only, code_project):
            _by_project = _project_local_only(code_project)
            out = _inject_block(out, (
                ("[LOCAL-ONLY PROJECT] This project is marked local-only: "
                 if _by_project else
                 "[LOCAL-ONLY CONVERSATION] This conversation is locked to "
                 "this machine: ")
                + "federation tools are not available this turn, and nothing "
                "of this work (its folder, its sessions) rides the device "
                "snapshot. Do not try to send, share or publish any of it; "
                "everything local — memory, search, journal, changes — works "
                "as always."))
            note("msg.project_local_only" if _by_project
                 else "msg.tab_local_only")

    meta = {"given": "\n\n".join(_given)}
    verify = _skill_verify(pinned_skills)
    if verify:
        meta["verify"] = verify
        note("verify.from_skill", cmd=verify)
    if _new_snap is not None:
        _MEM_SNAPSHOTS[_skey] = _new_snap
    _lu = next((i for i in range(len(out) - 1, -1, -1)
                if out[i].get("role") == "user"), -1)
    _lu0 = next((i for i in range(len(messages) - 1, -1, -1)
                 if messages[i].get("role") == "user"), -1)
    if _lu >= 0 and _lu0 >= 0 and out[_lu].get("content") != messages[_lu0].get("content"):
        meta["last_user_sent"] = out[_lu].get("content")
    return out, meta


def _last_user_said(messages):
    import fed_agent
    users = [m.get("content") for m in (messages or [])
             if m.get("role") == "user" and isinstance(m.get("content"), str)]
    for c in reversed(users):
        said = fed_agent.user_said({"role": "user", "content": c})
        if said:
            return said
    return users[-1] if users else ""


def route_turn_model(user_query, emit=None):
    emit = emit or (lambda ev: None)
    pool = _model_pool()
    if not pool or not user_query:
        return None
    if CONFIG and not CONFIG.getboolean("CHAT", "auto_route_models", fallback=True):
        return None

    def note(key, **args):
        emit({"event": "prep", "note": {"key": key, "args": args}})

    for _n, _m in list(pool.items()):
        if _server_says_no_tools(_m.get("base_url") or "", _m.get("api_key") or "",
                                 _m.get("model") or ""):
            note("router.no_tools", name=_n)
            pool.pop(_n)
    if not pool:
        return None
    note("router.evaluating", models=", ".join(pool))
    options = "\n".join(
        f'  - "{n}"{" [" + m.get("model") + "]" if m.get("model") else ""}: '
        f'{m.get("profile") or _tr("planner.pool_no_profile")}'
        for n, m in pool.items())
    try:
        raw = _llm_chat_with_tools(
            [{"role": "system", "content": _tr("router.system", options=options)},
             {"role": "user", "content": user_query[:500]}], None,
            temperature=0.0)
        answer = _strip_think((raw.get("message") or {}).get("content", "") or "").lower()
    except Exception as e:
        note("router.error", e=str(e))
        return None
    note("router.decided", ans=answer[:40])
    for name in pool:
        if name.lower() in answer:
            note("router.routed", name=name)
            m = pool[name]
            return m["base_url"], m["api_key"], m["model"]
    return None



TOOL_PROFILES = {
    "full":    None,
    "none":    set(),
    "browse":  {"browse_web", "scrape_url", "screenshot_web", "search_web",
                "alert", "delete_current_cron_job", "disable_current_cron_job",
                "get_credential", "search_notes", "search_rag"},
    "read":    {"search_web", "scrape_url", "search_notes", "search_rag",
                "search_experience", "search_facts", "list_dir", "read_file",
                "alert", "code_index", "code_read_smart", "code_read_range",
                "code_search", "code_deps", "code_references", "code_lint"},
    "monitor": {"browse_web", "scrape_url", "search_web", "alert",
                "delete_current_cron_job", "disable_current_cron_job"},
}
TOOL_PROFILES["browse_read"] = TOOL_PROFILES["browse"] | TOOL_PROFILES["read"]

_SUBAGENT_BANNED_TOOLS = {"plan_and_orchestrate", "call_council"}

_ALWAYS_IN_PROFILE = {"intent"}

_PROJECT_TOOLS = {"get_todo_context", "list_todo_projects", "add_task",
                  "update_task_progress", "task_depends"}

_SUBAGENT_SHARED_SURFACE_TOOLS = {"render_html", "dashboard_render",
                                  "canvas_eval"}


def _profile_tool_names(profile, terminal=False, may_orchestrate=False):
    allowed = TOOL_PROFILES.get(profile, None)
    _all = set(_allowed_tools())
    names = _all if allowed is None else set(allowed)
    if may_orchestrate:
        names |= (_SUBAGENT_BANNED_TOOLS & _all)
    else:
        names -= _SUBAGENT_BANNED_TOOLS
    if not terminal:
        names -= _SUBAGENT_SHARED_SURFACE_TOOLS
    names |= (_ALWAYS_IN_PROFILE & _all)
    return names


def _pack_tool_names(pack, terminal=True, may_orchestrate=False):
    from hoplita_orchestrator import capabilities_to_profile
    chosen = [x for x in (pack.get("tools") or []) if x]
    if chosen:
        names = set(chosen) & set(_allowed_tools())
        names -= _SUBAGENT_BANNED_TOOLS
        if not terminal:
            names -= _SUBAGENT_SHARED_SURFACE_TOOLS
        names |= (_ALWAYS_IN_PROFILE & set(_allowed_tools()))
        return names
    names = _profile_tool_names(
        capabilities_to_profile(pack.get("capabilities") or []),
        terminal=terminal, may_orchestrate=may_orchestrate)
    if pack.get("projects"):
        names |= (_PROJECT_TOOLS & set(_allowed_tools()))
    return names


def model_section(cfg, name):
    import hoplita_llmcfg
    return hoplita_llmcfg.model_section(cfg, name)


def _model_pool():
    pool = {}
    if not CONFIG or not CONFIG.has_section("MODELS"):
        return pool
    for name, active in CONFIG.items("MODELS"):
        if str(active).strip().lower() not in ("1", "true", "yes", "on"):
            continue
        sec = model_section(CONFIG, name)
        if not sec:
            continue
        base_url, api_key, model = _resolve_endpoint(sec)
        pool[name] = {"base_url": base_url, "api_key": api_key, "model": model,
                      "profile": CONFIG.get(sec, "profile", fallback="").strip(),
                      "council": _sits_on_council(CONFIG, sec)}
    return pool


def _sits_on_council(cfg, sec):
    return (cfg.get(sec, "council", fallback="") or "").strip().lower() \
        not in ("false", "0", "no", "off")


def _council_pool():
    return {n: m for n, m in _model_pool().items() if m.get("council", True)}


class OrchestrationCtx:

    def __init__(self, emit=None, cancel=None, delegate=None, delegate_names=(),
                 endpoint=None, ack_mode="", unattended=False, turn_id="",
                 code_project="", blocked=(), local_only=False, remote=False):
        self.emit = emit or (lambda ev: None)
        self.remote = bool(remote)
        self.local_only = _turn_local_only(local_only, code_project)
        self.blocked = frozenset(str(n) for n in (blocked or ()) if n)
        self.cancel = cancel or (lambda: False)
        self.delegate = delegate
        self.delegate_names = tuple(delegate_names or ())
        self.endpoint = endpoint or {}
        self.code_project = (code_project or "").strip()
        self.ack_mode = (ack_mode or "").strip().lower()
        self.unattended = bool(unattended)
        self.turn_id = turn_id or ""
        self.plan_denied = False
        self.plan_solo = False

    @property
    def can_paint(self):
        return self.delegate is not None


_ORCH = threading.local()


def _current_turn_cancel():
    ctx = getattr(_ORCH, "ctx", None)
    return getattr(ctx, "cancel", None)


fed_tools.TURN_STOP_PROBE = _current_turn_cancel


def _current_turn_emitter():
    ctx = getattr(_ORCH, "ctx", None)
    return getattr(ctx, "emit", None)


fed_tools.TURN_EMITTER = _current_turn_emitter


@_contextlib.contextmanager
def _orchestration_ctx(ctx):
    prev = getattr(_ORCH, "ctx", None)
    _ORCH.ctx = ctx
    try:
        yield ctx
    finally:
        _ORCH.ctx = prev


@_contextlib.contextmanager
def _plan_subtask(ctx):
    prev = getattr(_ORCH, "in_plan", False)
    _ORCH.in_plan = True
    try:
        with _orchestration_ctx(ctx):
            yield ctx
    finally:
        _ORCH.in_plan = prev


def _orch_base_endpoint(ctx):
    ep = ctx.endpoint or {}
    if ep.get("base_url") and ep.get("model"):
        return ep["base_url"], ep.get("api_key", ""), ep["model"]
    return _resolve_endpoint("API")


def _pool_alias(name):
    n = str(name or "").strip()
    if n.lower() in ("", "base", "default"):
        return ""
    pool = _model_pool()
    if n in pool:
        return n
    low = n.lower()
    for alias, m in pool.items():
        if alias.lower() == low or str(m.get("model") or "").lower() == low:
            return alias
    return None


def _orch_model_endpoint(alias, ctx):
    alias = _pool_alias(alias)
    if alias:
        m = _model_pool()[alias]
        return m["base_url"], m["api_key"], m["model"]
    return _orch_base_endpoint(ctx)


def _orch_role_endpoint(role, ctx):
    alias = (CONFIG.get("CHAT", f"{role}_model", fallback="").strip()
             if CONFIG else "")
    if alias and alias not in ("base", "default") and alias not in _model_pool():
        print(f"[ORCH] '{alias}' is not in the pool; {role} uses the base")
    return _orch_model_endpoint(alias, ctx)


def _orch_local_children():
    out = []
    for p in hoplita_children.load_all():
        role = " ".join((p.get("role") or "").split())[:220]
        scope = []
        if p.get("notes_folders"):
            scope.append("notes: " + ", ".join(p["notes_folders"][:4]))
        if p.get("projects"):
            scope.append("projects: " + ", ".join(p["projects"][:4]))
        tail = f" [{'; '.join(scope)}]" if scope else ""
        out.append(f"child:{p['name']}" + (f" — {role}" if role else "") + tail)
    return out


def _orch_remote_targets():
    names = []
    try:
        for ch in (fed_tools.tool_fed_list_children() or {}).get("children", []):
            h = ch.get("handle") if isinstance(ch, dict) else str(ch)
            if h and f"child:{h}" not in names:
                names.append(f"child:{h}")
    except Exception:
        pass
    try:
        for c in (fed_tools.tool_fed_list_contacts() or {}).get("contacts", []):
            if not isinstance(c, dict):
                c = {"handle": str(c)}
            if c.get("local"):
                continue
            h = c.get("handle")
            if h and f"child:{h}" not in names:
                names.append(f"friend:{h}")
    except Exception:
        pass
    return names


def _orch_planner_block():
    block = ""
    pool = _model_pool()
    if pool:
        lines = [_tr("planner.pool_header")]
        for name, m in pool.items():
            prof = m.get("profile") or _tr("planner.pool_no_profile")
            mid = f" [{m.get('model')}]" if m.get("model") else ""
            lines.append(f'  - "{name}"{mid}: {prof}')
        lines.append(_tr("planner.pool_rules"))
        block += "\n\n" + "\n".join(lines)
    kids = _orch_local_children()
    if kids:
        block += "\n\n" + "\n".join([_tr("planner.children_header")]
                                    + [f"  - {n}" for n in kids]
                                    + [_tr("planner.children_rules")])
    targets = _orch_remote_targets()
    if targets:
        block += "\n\n" + "\n".join([_tr("planner.targets_header")]
                                    + [f"  - {n}" for n in targets]
                                    + [_tr("planner.targets_rules")])
    return block


_PLAN_ACKS = {}
_PLAN_ACKS_LOCK = threading.Lock()
_PLAN_ACK_TIMEOUT_S = 900

_COUNCIL_ACKS = {}
_COUNCIL_ACKS_LOCK = threading.Lock()


def _plan_ack_policy(subtasks, unattended=False):
    def _is_remote(target):
        t_ = str(target or "local").strip() or "local"
        if t_ == "local":
            return False
        if t_.startswith("child:"):
            try:
                return hoplita_children.find(t_) is None
            except Exception:
                return True
        return True

    has_remote = any(_is_remote(s.get("target")) for s in subtasks)
    if unattended:
        return ("cancel" if has_remote else "run"), {}
    mode = (CONFIG.get("CHAT", "plan_approval", fallback="always").strip().lower()
            if CONFIG else "always")
    if mode in ("off", "false", "no"):
        return "run", {}
    limit = CONFIG.getint("CHAT", "plan_auto_limit", fallback=3) if CONFIG else 3
    if mode == "auto" and not has_remote and len(subtasks) <= limit:
        return "run", {}
    return "ask", None


def _request_plan_ack(subtasks, ctx):
    if ctx.ack_mode != "stream":
        decision, opts = _plan_ack_policy(subtasks, unattended=True)
        ctx.emit({"event": "orch", "orch": {
            "type": "note",
            "key": ("plan.ack_unattended" if decision == "run"
                    else "plan.ack_unattended_remote")}})
        return decision, None, opts

    ack_id = uuid.uuid4().hex[:12]
    waiter = _queue.Queue(maxsize=1)
    with _PLAN_ACKS_LOCK:
        _PLAN_ACKS[ack_id] = {"q": waiter, "subtasks": subtasks,
                              "mode": ctx.ack_mode}
    try:
        ctx.emit({"event": "plan_ack", "ack_id": ack_id, "turn_id": ctx.turn_id,
                  "subtasks": subtasks})
        answer = None
        wait_s = (CONFIG.getint("CHAT", "plan_ack_timeout",
                                fallback=_PLAN_ACK_TIMEOUT_S)
                  if CONFIG else _PLAN_ACK_TIMEOUT_S)
        deadline = time.time() + wait_s
        while time.time() < deadline:
            if ctx.cancel():
                return "cancel", None, {}
            try:
                answer = waiter.get(timeout=1.0)
                break
            except _queue.Empty:
                continue
        if answer is None:
            ctx.emit({"event": "orch", "orch": {"type": "note",
                                                "key": "plan.ack_timeout"}})
            return "cancel", None, {}
    finally:
        with _PLAN_ACKS_LOCK:
            _PLAN_ACKS.pop(ack_id, None)

    edited = answer.get("subtasks")
    if isinstance(edited, list):
        subtasks[:] = [dict(x) for x in edited if isinstance(x, dict)]
    return (str(answer.get("decision") or "cancel"),
            (str(answer.get("feedback") or "").strip() or None),
            {"allow_expand": bool(answer.get("allow_expand")),
             "goal": str(answer.get("goal") or "").strip()})


def _subagent_remote(target, prompt, terminal=False, ctx=None):
    handle = target.split(":", 1)[1].strip() if ":" in target else target.strip()
    if not handle:
        return f"[invalid target '{target}']"
    with (_plan_subtask(ctx) if ctx is not None else _contextlib.nullcontext()):
        try:
            res = fed_tools.tool_fed_ask_agent(handle, prompt, terminal=terminal)
        except Exception as e:
            return f"[remote {target} failed: {e}]"
    if not isinstance(res, dict) or res.get("error"):
        return f"[remote {target} failed: {(res or {}).get('error', '?')}]"
    return ((res.get("reply") or res.get("result") or "").strip()
            or f"[remote {target}: empty]")


def _subagent_local(prompt, profile, model_name, ctx, sid, terminal=False,
                    messages=None, given="", event_sink=None, tool_names=None):
    import fed_agent
    base_url, api_key, model = _orch_model_endpoint(model_name, ctx)
    qa_url, qa_key, qa_model = _orch_role_endpoint("qa", ctx)
    qa_mode = (CONFIG.get("CHAT", "qa_review", fallback="off").strip().lower()
               if CONFIG else "off")
    max_iter = (CONFIG.getint("CHAT", "agentic_max_iterations", fallback=5)
                if CONFIG else 5)

    def chat(msgs, tools, max_tokens=None):
        return _llm_chat_with_tools(msgs, tools, base_url=base_url,
                                    api_key=api_key, model=model,
                                    max_tokens=max_tokens)

    def qa_chat(msgs, tools):
        return _llm_chat_with_tools(msgs, tools, base_url=qa_url,
                                    api_key=qa_key, model=qa_model)

    delegate = None
    if ctx.delegate is not None:
        def delegate(call_id, name, args, _sid=sid):
            return ctx.delegate(f"{_sid}~{call_id}", name, args)

    try:
        from hoplita_orchestrator import current_subtask_id
        _card = current_subtask_id()
    except Exception:
        _card = ""

    def on_event(ev):
        et = ev.get("event")
        if event_sink is not None:
            try:
                event_sink(ev)
            except Exception:
                pass
        if et == "client_tool_call":
            ev = dict(ev)
            ev["call_id"] = f"{sid}~{ev.get('call_id')}"
            ctx.emit(ev)
        elif et in ("llm_call", "llm_done"):
            ctx.emit({"event": "orch", "orch": {"type": "tick", "id": sid}})
        elif et == "intent" and _card:
            ctx.emit({"event": "orch", "orch": {
                "type": "intent", "id": _card,
                "items": [dict(it) for it in (ev.get("items") or [])
                          if isinstance(it, dict)]}})

    try:
        _sub_is_blocked, _sub_allowed = _sub_agent_wall(
            ctx, tool_names if tool_names is not None
            else _profile_tool_names(profile, terminal))
        res = fed_agent.run_agentic_turn(
            user_message=None if messages else prompt, messages=messages,
            given=given, max_iterations=max_iter,
            on_event=on_event, cancel=ctx.cancel,
            turn_id=f"{ctx.turn_id}~{sid}", llm_chat=chat,
            delegate=delegate, delegate_names=ctx.delegate_names,
            is_blocked=_sub_is_blocked,
            qa=qa_mode, qa_chat=qa_chat, qa_max_rounds=1,
            qa_self=(qa_model or "").strip() == (model or "").strip(),
            allowed_tools=_sub_allowed)
    except Exception as e:
        return f"[sub-agent error: {e}]"
    try:
        _n_tools = len([t for t in (res.get("tool_trace") or [])
                        if (t.get("tool") or "") != "intent"])
        ctx.emit({"event": "orch", "orch": {"type": "subtask_acted",
                                            "id": sid, "tools": _n_tools}})
    except Exception:
        pass
    if res.get("cancelled"):
        return "[stopped by the user]"
    if res.get("error"):
        return f"[sub-agent error: {res['error']}]"
    return res.get("reply") or ""


def _child_run_local(pack, message, terminal=True, event_sink=None):
    ctx = getattr(_ORCH, "ctx", None) or OrchestrationCtx()
    if ctx.delegate is None and event_sink is not None:
        _CANVAS_TOOLS = ("render_html", "dashboard_render")

        def _child_delegate(call_id, name, args, _sink=event_sink):
            if name in _CANVAS_TOOLS:
                a_ = args or {}
                html = a_.get("html", "")
                _sink({"event": "canvas", "html": html, "tool": name,
                       "append": bool(a_.get("append"))})
                return json.dumps({"rendered": True, "chars": len(html),
                                   "appended": bool(a_.get("append"))},
                                  ensure_ascii=False)
            if name == "alert":
                a_ = args or {}
                _sink({"event": "alert", "title": a_.get("title", "🔔"),
                       "message": a_.get("message", "")})
                return json.dumps({"ok": True}, ensure_ascii=False)
            return json.dumps(
                {"error": f"'{name}' needs a client and this child has none"},
                ensure_ascii=False)

        ctx = OrchestrationCtx(
            emit=ctx.emit, cancel=ctx.cancel, delegate=_child_delegate,
            delegate_names=_CANVAS_TOOLS + ("alert",),
            endpoint=ctx.endpoint, ack_mode=ctx.ack_mode,
            unattended=ctx.unattended, turn_id=ctx.turn_id,
            code_project=ctx.code_project, blocked=ctx.blocked,
            local_only=ctx.local_only, remote=getattr(ctx, "remote", False))
    msgs = [{"role": "system", "content": hoplita_children.system_block(pack)},
            {"role": "user", "content": message}]
    given = ""
    try:
        msgs, _meta = prepare_turn_messages(msgs, emit=ctx.emit)
        given = _meta.get("given", "")
    except Exception as e:
        print(f"[CHILD] memory prep failed for '{pack['name']}': {e}",
              file=sys.stderr)
    sid = f"child-{uuid.uuid4().hex[:4]}"
    from hoplita_orchestrator import capabilities_to_profile
    card = {"id": sid, "goal": message[:200], "role": pack["name"],
            "capabilities": list(pack.get("capabilities") or []),
            "profile": capabilities_to_profile(pack.get("capabilities") or []),
            "model": pack.get("model", ""),
            "target": f"child:{pack['name']}", "deps": [],
            "status": "running", "result": None, "error": None, "attempts": 1}

    def _board(**over):
        ctx.emit({"event": "orch", "orch": {"type": "snapshot",
                                            "subtasks": [{**card, **over}]}})

    ctx.emit({"event": "orch", "orch": {
        "type": "note", "key": "child.local_turn",
        "args": {"name": pack["name"], "model": pack.get("model") or "base"}}})
    _board()
    ctx.emit({"event": "orch", "orch": {
        "type": "subtask_start", "id": sid, "goal": message[:200],
        "role": pack["name"], "model": pack.get("model", "")}})
    out = ""
    try:
        out = _subagent_local(message, None, pack.get("model", ""), ctx,
                              sid, terminal=terminal, messages=msgs,
                              given=given, event_sink=event_sink,
                              tool_names=_pack_tool_names(pack, terminal))
        return out
    finally:
        _failed = not out or out.startswith("[sub-agent error")
        _board(status="failed" if _failed else "done", result=out[:400] or None)
        if _failed:
            ctx.emit({"event": "orch", "orch": {
                "type": "subtask_failed", "id": sid,
                "error": (out or "empty")[:200]}})
        else:
            ctx.emit({"event": "orch", "orch": {"type": "subtask_done",
                                                "id": sid, "attempts": 1}})
        if not getattr(_ORCH, "in_plan", False):
            ctx.emit({"event": "orch", "orch": {"type": "complete"}})


def tool_plan_and_orchestrate(task, solo_single=False):
    task = (task or "").strip()
    if not task:
        return {"error": _tr("err.task_empty")}
    try:
        from hoplita_orchestrator import Orchestrator
    except Exception as e:
        return {"error": f"orchestrator unavailable (hoplita_orchestrator.py): {e}"}

    ctx = getattr(_ORCH, "ctx", None) or OrchestrationCtx()
    if ctx.plan_denied:
        return {"orchestrated": False, "result": _tr("msg.plan_cancelled")}
    if solo_single and ctx.plan_solo:
        return {"orchestrated": False, "result": _tr("msg.plan_single_solo")}

    planner_block = _orch_planner_block()

    def llm_fn(system, user):
        base_url, api_key, model = _orch_role_endpoint("planner", ctx)
        try:
            raw = _llm_chat_with_tools(
                [{"role": "system", "content": system + planner_block},
                 {"role": "user", "content": user}], None,
                base_url=base_url, api_key=api_key, model=model,
                temperature=0.3)
        except Exception as e:
            print(f"[ORCH] llm_fn error: {e}", file=sys.stderr)
            return ""
        return _strip_think((raw.get("message") or {}).get("content", "") or "")

    def agent_fn(goal, profile, deps_ctx, model_name="", target="local",
                 terminal=False):
        prompt = goal
        if deps_ctx:
            prompt = (f"{goal}\n\n[Context from previous tasks you must use]:"
                      f"\n{deps_ctx}")
        if target and target != "local":
            _local_child = hoplita_children.find(target) is not None
            ctx.emit({"event": "orch", "orch": {
                "type": "note",
                "key": ("multi.local_child_subtask" if _local_child
                        else "multi.remote_subtask"),
                "args": {"target": target}}})
            out = _subagent_remote(target, prompt, terminal, ctx)
        else:
            out = _subagent_local(prompt, profile, model_name, ctx,
                                  uuid.uuid4().hex[:6], terminal)
        if not out or out.startswith("[sub-agent error") \
                or out.startswith("[stopped by the user]"):
            raise RuntimeError(out or "empty result")
        return out

    def progress_fn(event):
        ctx.emit({"event": "orch", "orch": event})

    def approve_fn(plan_dicts):
        decision, opts = _plan_ack_policy(plan_dicts, ctx.unattended)
        if decision != "ask":
            if ctx.unattended:
                ctx.emit({"event": "orch", "orch": {
                    "type": "note",
                    "key": ("plan.ack_unattended" if decision == "run"
                            else "plan.ack_unattended_remote")}})
            return (decision, None, opts)
        decision, feedback, opts = _request_plan_ack(plan_dicts, ctx)
        if opts.get("goal") and getattr(ctx, "turn_id", None):
            import fed_agent as _fedagent
            _fedagent.set_turn_goal(ctx.turn_id, opts["goal"])
        return decision, feedback, opts

    def on_expand(proposals):
        for p in proposals:
            ctx.emit({"event": "orch", "orch": {
                "type": "note", "key": "plan.grew",
                "args": {"id": p.get("id", "?"),
                         "goal": str(p.get("goal", ""))[:70]}}})
        return proposals

    _int = ((lambda k, d: CONFIG.getint("CHAT", k, fallback=d)) if CONFIG
            else (lambda k, d: d))
    _par = _int("multi_max_parallel", 4)
    _ctx_proj = getattr(ctx, "code_project", "")
    if _ctx_proj and _par > 1:
        progress_fn({"type": "note", "key": "orch.serial_code", "n": _par})
        _par = 1
    orch = Orchestrator(
        llm_fn, agent_fn, progress_fn,
        max_parallel=_par,
        max_retries=_int("multi_max_retries", 2),
        max_expansions=_int("plan_max_expansions", 2),
        max_subtasks=_int("plan_max_subtasks", 12),
        solo_single=solo_single,
        model_alias=_pool_alias,
        subtask_timeout=_int("subtask_timeout", 1800),
        should_stop=ctx.cancel,
        base_model_label=((ctx.endpoint or {}).get("model")
                          or _resolve_endpoint("API")[2]))
    try:
        final, _subs = orch.execute(task, approve_fn=approve_fn,
                                    on_expand=on_expand)
    except Exception as e:
        print(f"[ORCH] error: {e}", file=sys.stderr)
        return {"error": _tr("msg.orch_error", e=e)}

    if final == "__SOLO__":
        ctx.plan_solo = True
        ctx.emit({"event": "orch", "orch": {"type": "note", "key": "plan.solo"}})
        return {"orchestrated": False, "result": _tr("msg.plan_single_solo")}
    if final == "__CANCELLED__":
        ctx.plan_denied = True
        return {"orchestrated": False, "result": _tr("msg.plan_cancelled")}
    if final is None:
        ctx.plan_denied = True
        return {"orchestrated": False, "result": _tr("msg.plan_failed_solo")}
    out = {"orchestrated": True, "result": final or "[no result]"}
    if ctx.can_paint:
        out["canvas"] = ("The sub-agents did not draw anything. If this result "
                         "is worth showing visually (an itinerary, a comparison, "
                         "a table, a chart), render it NOW with render_html "
                         "using the complete result. If it is not, just answer.")
    return out


_COUNCIL_MAX_ROUNDS = 4
_COUNCIL_GATE_TIMEOUT_S = 900


def _council_members(names, first=""):
    pool = _model_pool()
    members, unknown, benched = [], [], []
    for raw in (names or []):
        n = (raw or "").strip()
        if not n:
            continue
        if n.lower() in ("base", "default"):
            n = "base"
        elif n not in pool:
            n = next((k for k in pool if k.lower() == n.lower()), None) or n
        if n != "base" and n not in pool:
            unknown.append(raw)
            continue
        if n != "base" and not pool[n].get("council", True):
            benched.append(n)
            continue
        if n not in members:
            members.append(n)
    f = (first or "").strip()
    if f:
        f = "base" if f.lower() in ("base", "default") else \
            next((m for m in members if m.lower() == f.lower()), f)
    return (members, (f if f in members else (members[0] if members else "")),
            unknown, benched)


def _council_gate(state, ctx):
    if ctx.ack_mode != "stream" or ctx.unattended:
        ctx.emit({"event": "council", "council": {
            "type": "note", "key": "council.gate_unattended"}})
        return "stop"
    ack_id = uuid.uuid4().hex[:12]
    waiter = _queue.Queue(maxsize=1)
    with _COUNCIL_ACKS_LOCK:
        _COUNCIL_ACKS[ack_id] = {"q": waiter}
    try:
        ctx.emit({"event": "council_ack", "ack_id": ack_id,
                  "turn_id": ctx.turn_id, "round": state.get("round", 1),
                  "answer": str(state.get("answer") or "")[:4000],
                  "dissent": [{"member": d.get("member"),
                               "verdict": d.get("verdict"),
                               "why": str(d.get("why") or "")[:600]}
                              for d in (state.get("dissent") or [])]})
        wait_s = (CONFIG.getint("CHAT", "council_gate_timeout",
                                fallback=_COUNCIL_GATE_TIMEOUT_S)
                  if CONFIG else _COUNCIL_GATE_TIMEOUT_S)
        deadline = time.time() + wait_s
        while time.time() < deadline:
            if ctx.cancel():
                return "stop"
            try:
                answer = waiter.get(timeout=1.0)
                break
            except _queue.Empty:
                continue
        else:
            ctx.emit({"event": "council", "council": {
                "type": "note", "key": "council.gate_timeout"}})
            return "stop"
    finally:
        with _COUNCIL_ACKS_LOCK:
            _COUNCIL_ACKS.pop(ack_id, None)
    return "again" if str(answer.get("decision") or "") == "again" else "stop"


def _run_council(topic, members, first="", context="", ctx=None):
    ctx = ctx or getattr(_ORCH, "ctx", None) or OrchestrationCtx()
    try:
        from hoplita_council import Council
    except Exception as e:
        return {"error": f"council unavailable (hoplita_council.py): {e}"}
    seats, first, unknown, benched = _council_members(members, first)
    if unknown:
        return {"error": _tr("council.err_unknown", names=", ".join(unknown),
                             avail=", ".join(sorted(_council_pool())) or "—")}
    if benched:
        return {"error": _tr("council.err_benched", names=", ".join(benched),
                             avail=", ".join(sorted(_council_pool())) or "—")}
    if not seats:
        return {"error": _tr("council.err_no_members")}

    where, clash = {}, {}
    for m in seats:
        _url, _key, _mdl = _orch_model_endpoint(m, ctx)
        where[m] = {"base_url": _url, "model": _mdl}
        clash.setdefault((_url, _mdl), []).append(m)
    _dupes = [ms for ms in clash.values() if len(ms) > 1]
    if _dupes:
        return {"error": _tr(
            "council.err_same_endpoint",
            pairs="; ".join(
                _tr("council.err_same_endpoint_item", who=" + ".join(ms),
                    url=where[ms[0]]["base_url"] or "?",
                    model=where[ms[0]]["model"] or "?") for ms in _dupes))}

    def speak(member, phase, prompt, rnd):
        import fed_agent as _fa
        sid = f"{member}·{phase}{rnd}·{uuid.uuid4().hex[:4]}"
        with _plan_subtask(ctx):
            text = _subagent_local(prompt, "full", member, ctx, sid,
                                   terminal=False)
        return {"text": text, "ok": not _fa.produced_nothing(text)}

    def progress(ev):
        ctx.emit({"event": "council", "council": ev})

    council = Council(
        speak_fn=speak, progress_fn=progress,
        gate_fn=(lambda st: _council_gate(st, ctx)),
        cancel_fn=ctx.cancel,
        max_parallel=(CONFIG.getint("CHAT", "multi_max_parallel", fallback=4)
                      if CONFIG else 4),
        max_rounds=(CONFIG.getint("CHAT", "council_max_rounds",
                                  fallback=_COUNCIL_MAX_ROUNDS)
                    if CONFIG else _COUNCIL_MAX_ROUNDS))
    return council.deliberate(topic, seats, first, context=context, where=where)


def tool_call_council(topic, members=None, first="", context=""):
    import hoplita_council
    topic = (topic or "").strip()
    if not topic:
        return {"error": _tr("council.err_no_topic")}
    if not members:
        members = sorted(_council_pool())
    res = _run_council(topic, members, first, context)
    if res.get("error"):
        return res
    return {"_verbatim": _council_verdict_line(res),
            "council": True, "placet": bool(res.get("placet")),
            "members": res.get("members") or [], "first": res.get("first", ""),
            "rounds": len(res.get("rounds") or []),
            "dissent": res.get("dissent") or [],
            "deliberation": hoplita_council.render_record(res),
            "result": hoplita_council.format_result(res)}


def _council_verdict_line(res):
    members = list(res.get("members") or [])
    first = res.get("first", "")
    rounds = res.get("rounds") or []
    others = [m for m in members if m != first]
    parts = [f"COUNCIL FACTS (exact; report these, do not recount): "
             f"{len(members)} seats: {', '.join(members)}. "
             f"{first} answered and reformulated; "
             f"{', '.join(others) or 'nobody'} examined and voted."]
    if rounds:
        last = rounds[-1]
        votes = ", ".join(f"{v.get('member', '?')} "
                          f"{v.get('verdict') or 'unreadable'}"
                          for v in last.get("votes") or [])
        parts.append(f"Round {last.get('n', len(rounds))} of {len(rounds)}, "
                     f"votes: {votes or 'none'}.")
    if res.get("cancelled"):
        parts.append("The council was stopped before it finished.")
    elif rounds:
        parts.append("Unanimous placet." if res.get("placet") else
                     "No unanimous placet: " + ", ".join(
                         d.get("member", "?") for d in res.get("dissent") or [])
                     + " did not agree.")
    return " ".join(parts)


def _strip_think(text):
    if not text:
        return ""
    cleaned = re.sub(r"<think>.*?</think>", "", text,
                     flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"^.*?</think>", "", cleaned,
                     flags=re.DOTALL | re.IGNORECASE)
    return cleaned.strip()



from fastapi.responses import JSONResponse, StreamingResponse
from typing import Optional, List, Dict, Any

_AGENT_TURNS = {}
_AGENT_TURNS_LOCK = threading.Lock()
_AGENT_TOOL_WAITS = {}
_AGENT_TOOL_WAITS_LOCK = threading.Lock()


class AgentTurnReq(BaseModel):
    messages: Optional[List[Dict[str, Any]]] = None
    message: Optional[str] = None
    history: List[Dict[str, Any]] = []
    system: Optional[str] = None
    max_iterations: Optional[int] = None
    verify: Optional[str] = None
    goal: Optional[str] = None
    verify_goal: Optional[str] = None
    qa: Optional[str] = None
    qa_endpoint: Optional[Dict[str, Any]] = None
    turn_id: Optional[str] = None
    session: Optional[str] = ""
    session_uuid: Optional[str] = ""
    job_id: Optional[str] = ""
    log: bool = True
    endpoint: Optional[Dict[str, Any]] = None
    compact: bool = False
    delegate: List[str] = []
    client_tool_timeout: int = 900
    ack_mode: str = ""
    unattended: bool = False
    orchestrate: Optional[str] = None
    council: Optional[Dict[str, Any]] = None
    inject: bool = False
    pinned_skills: List[Dict[str, Any]] = []
    code_project: str = ""
    blocked_tools: List[str] = []
    local_only: bool = False
    rag_docs: int = 0


class AgentQAReq(BaseModel):
    request: str = ""
    reply: str = ""
    tool_trace: List[Dict[str, Any]] = []
    endpoint: Optional[Dict[str, Any]] = None
    prior: List[Dict[str, Any]] = []
    given: str = ""


@app.post("/agent/qa")
def agent_qa(req: AgentQAReq):
    import fed_agent
    ep = req.endpoint or {}
    if ep.get("base_url") and ep.get("model"):
        chat = (lambda m, tl: _llm_chat_with_tools(
            m, tl, base_url=ep["base_url"], api_key=ep.get("api_key"),
            model=ep["model"]))
    else:
        chat = fed_agent._llm_chat
    if chat is None:
        return {"ok": True, "why": "", "next": ""}
    ok, why, nxt, mode = fed_agent._run_qa(
        req.request, req.tool_trace, req.reply, chat,
        given=req.given, prior=req.prior)
    return {"ok": ok, "why": why, "next": nxt, "mode": mode}


@app.post("/agent/turn")
def agent_turn(req: AgentTurnReq):
    return StreamingResponse(_agent_turn_events(req),
                             media_type="application/x-ndjson")


def _agent_turn_events(req):
    import fed_agent

    turn_id = req.turn_id or uuid.uuid4().hex[:12]
    if req.compact:
        req.log = False
        req.qa = "off"
        req.max_iterations = 1
    _sess = (req.session or "").strip()
    _said = req.message or ""
    if not _said and req.messages:
        _said = _last_user_said(req.messages)
    if not _said:
        _said = (req.orchestrate or ""
                 or str((req.council or {}).get("topic") or ""))
    if _said and req.log:
        log_turn_message(_sess, "user", _said)
    cancel_ev = threading.Event()
    with _AGENT_TURNS_LOCK:
        _AGENT_TURNS[turn_id] = cancel_ev

    events = _queue.Queue()

    class _LoggedQueue:
        def __init__(self, q):
            self._q = q

        def put(self, ev):
            try:
                if req.log and isinstance(ev, dict) \
                        and ev.get("event") == "reply":
                    log_turn_message(_sess, "assistant", ev.get("reply", ""))
                    _rec = ev.get("turn_record") or ""
                    if _rec:
                        log_turn_message(_sess, "tool", _rec)
            except Exception:
                pass
            return self._q.put(ev)

        def __getattr__(self, n):
            return getattr(self._q, n)

    events = _LoggedQueue(events)

    ep = req.endpoint or {}
    _conv_key = (req.session or req.session_uuid or "").strip()
    if req.compact and not (ep.get("base_url") and ep.get("model")):
        ep = dict(_LAST_TURN_EP.get(_conv_key) or {})
    elif not (ep.get("base_url") and ep.get("model")):
        route_q = req.message or ""
        if not route_q and req.messages:
            route_q = _last_user_said(req.messages)
        routed = route_turn_model(route_q, events.put) if route_q else None
        if routed:
            ep = {"base_url": routed[0], "api_key": routed[1], "model": routed[2]}
    if _conv_key and not req.compact:
        _LAST_TURN_EP[_conv_key] = dict(ep)

    llm_override = None
    if req.compact:
        _cb, _ck, _cm = ((ep["base_url"], ep.get("api_key"), ep["model"])
                         if ep.get("base_url") and ep.get("model") else _resolve_endpoint("API"))
        llm_override = (lambda m, tl, max_tokens=None: _llm_chat_with_tools(
            m, tl, base_url=_cb, api_key=_ck, model=_cm, max_tokens=max_tokens,
            extra={"chat_template_kwargs": {"enable_thinking": False}}))
    elif ep.get("base_url") and ep.get("model"):
        llm_override = (lambda m, tl, max_tokens=None: _llm_chat_with_tools(
            m, tl, base_url=ep["base_url"], api_key=ep.get("api_key"),
            model=ep["model"], max_tokens=max_tokens))

    _qe = req.qa_endpoint or {}
    if not (_qe.get("base_url") and _qe.get("model")):
        _b, _k, _m = _orch_role_endpoint("qa", OrchestrationCtx(endpoint=ep))
        _qe = {"base_url": _b, "api_key": _k, "model": _m}
    qa_override = (lambda m, tl: _llm_chat_with_tools(
        m, tl, base_url=_qe["base_url"], api_key=_qe.get("api_key"),
        model=_qe["model"])) if _qe.get("base_url") and _qe.get("model") else None
    _qa_self = ((_qe.get("model") or "").strip()
                == ((ep.get("model") or _resolve_endpoint("API")[2]) or "").strip())

    _turn_is_blocked, _turn_allowed = _turn_tool_policy(
        req.blocked_tools,
        local_only=_turn_local_only(req.local_only, req.code_project))
    if req.compact:
        _turn_is_blocked = lambda _n: True

    def _delegate(call_id, name, args):
        _hit = _spill_path_intercept(name, args)
        if _hit is not None:
            return _hit
        if _turn_is_blocked(name):
            print(f"[FIREWALL] 🛡 Blocked (delegated): {name}")
            return json.dumps({"error": f"🛡 FIREWALL: '{name}' is blocked"},
                              ensure_ascii=False)
        waiter = _queue.Queue(maxsize=1)
        with _AGENT_TOOL_WAITS_LOCK:
            _AGENT_TOOL_WAITS[(turn_id, call_id)] = waiter
        try:
            deadline = time.time() + max(req.client_tool_timeout, 30)
            _t0 = time.time()
            _said = False
            while time.time() < deadline:
                if cancel_ev.is_set():
                    return None
                try:
                    return waiter.get(timeout=1.0)
                except _queue.Empty:
                    if not _said and time.time() - _t0 >= 20.0:
                        _said = True
                        events.put({"event": "client_tool_slow", "tool": name,
                                    "call_id": call_id,
                                    "waited": int(time.time() - _t0),
                                    "ceiling": int(req.client_tool_timeout)})
                    continue
            raise TimeoutError(f"{req.client_tool_timeout}s with no response from the client")
        finally:
            with _AGENT_TOOL_WAITS_LOCK:
                _AGENT_TOOL_WAITS.pop((turn_id, call_id), None)

    orch_ctx = OrchestrationCtx(
        emit=events.put, cancel=cancel_ev.is_set,
        delegate=_delegate if req.delegate else None,
        delegate_names=tuple(req.delegate),
        endpoint=ep, ack_mode=req.ack_mode,
        unattended=req.unattended, turn_id=turn_id,
        code_project=req.code_project, blocked=req.blocked_tools,
        local_only=req.local_only)

    def _worker():
        _set_turn_session(req.session_uuid or "", req.code_project or "",
                          conv=req.session or "", rag_docs=req.rag_docs,
                          images=_attached_images_of(req.messages),
                          job=req.job_id or "")
        _turn_snapshot_file(turn_id)
        try:
            with _orchestration_ctx(orch_ctx):
                if req.orchestrate:
                    events.put({"event": "turn_start", "turn_id": turn_id,
                                "tools": 0, "max_iterations": 1})
                    out = tool_plan_and_orchestrate(req.orchestrate)
                    _hands_turn_end(turn_id, events.put)
                    if cancel_ev.is_set():
                        events.put({"event": "cancelled", "iterations": 1})
                    elif out.get("error"):
                        events.put({"event": "error", "error": out["error"],
                                    "iterations": 1})
                    else:
                        _synth = out.get("result") or ""
                        if any(s in _synth
                               for s in fed_agent.RECORD_SENTINELS):
                            _synth = fed_agent._rtr("chat.turn_echo_dead")
                        events.put({"event": "reply", "iterations": 1,
                                    "reply": _synth,
                                    "orchestrated": out.get("orchestrated", False)})
                    return
                if req.council:
                    import hoplita_council
                    _c = req.council or {}
                    events.put({"event": "turn_start", "turn_id": turn_id,
                                "tools": 0, "max_iterations": 1})
                    res = _run_council(str(_c.get("topic") or ""),
                                       _c.get("members") or [],
                                       str(_c.get("first") or ""),
                                       str(_c.get("context") or ""),
                                       ctx=orch_ctx)
                    _hands_turn_end(turn_id, events.put)
                    if cancel_ev.is_set():
                        events.put({"event": "cancelled", "iterations": 1})
                    elif res.get("error"):
                        events.put({"event": "error", "error": res["error"],
                                    "iterations": 1})
                    else:
                        _txt = hoplita_council.format_result(res)
                        if any(s in _txt for s in fed_agent.RECORD_SENTINELS):
                            _txt = fed_agent._rtr("chat.turn_echo_dead")
                        events.put({"event": "reply", "iterations": 1,
                                    "reply": _txt,
                                    "deliberated": True,
                                    "placet": bool(res.get("placet")),
                                    "turn_record":
                                        hoplita_council.render_record(res)})
                    return
                _messages, _verify = req.messages, req.verify
                _given = ""
                _last_user_sent = None
                if req.inject and _messages is not None:
                    _messages, _meta = prepare_turn_messages(
                        _messages, req.pinned_skills, events.put,
                        code_project=req.code_project,
                        local_only=req.local_only)
                    _verify = _verify or _meta.get("verify")
                    _given = _meta.get("given", "")
                    _last_user_sent = _meta.get("last_user_sent")

                _lim_box = {}

                def _put_with_sent(ev, _p=events.put, _s=_last_user_sent):
                    if ev.get("event") == "tokens":
                        if "limit" not in _lim_box:
                            try:
                                _eb, _ek, _em = ((ep.get("base_url"), ep.get("api_key", ""), ep.get("model"))
                                                 if ep.get("base_url") and ep.get("model")
                                                 else _resolve_endpoint("API"))
                                _lim_box["limit"] = int(agent_context_limit(_eb, _ek or "", _em or "")
                                                        .get("limit", 0) or 0)
                                _lim_box["model"] = _em or ""
                            except Exception:
                                _lim_box["limit"], _lim_box["model"] = 0, ""
                        ev = dict(ev, context_limit=_lim_box["limit"], model=_lim_box["model"])
                    if ev.get("event") in fed_agent.TERMINAL_EVENTS:
                        _hands_turn_end(turn_id, _p)
                    if _s is not None and ev.get("event") in (
                            "reply", "cancelled", "iteration_limit", "error"):
                        ev = dict(ev, last_user_sent=_s)
                    _p(ev)
                fed_agent.run_agentic_turn(
                    user_message=req.message, history=req.history,
                    system=req.system, max_iterations=req.max_iterations,
                    on_event=_put_with_sent, cancel=cancel_ev.is_set,
                    turn_id=turn_id, messages=_messages,
                    llm_chat=llm_override, given=_given,
                    is_blocked=_turn_is_blocked,
                    allowed_tools=_turn_allowed,
                    model_label=(ep.get("model")
                                 or _resolve_endpoint("API")[2]),
                    delegate=_delegate if req.delegate else None,
                    delegate_names=tuple(req.delegate),
                    verify=_verify, qa=req.qa, qa_chat=qa_override,
                    plan_gate=(_plan_gate_for(_project_category(req.code_project))
                               if req.code_project else None),
                    goal=req.goal, verify_goal=req.verify_goal,
                    escalate=_build_escalator(ep),
                    qa_self=_qa_self,
                    council=_council_available(_turn_is_blocked, _turn_allowed),
                    qa_max_rounds=(CONFIG.getint("CHAT", "qa_max_rounds",
                                                 fallback=2) if CONFIG else 2))
        except Exception as e:
            events.put({"event": "error", "error": f"loop crash: {e}",
                        "iterations": 0})
        finally:
            _hands_turn_end(turn_id, events.put)
            _turn_snapshot_drop(turn_id)
            _set_turn_session("", "")

    threading.Thread(target=_worker, daemon=True,
                     name=f"agent-turn-{turn_id}").start()

    _hb_stop = threading.Event()

    def _heartbeat():
        while not _hb_stop.wait(30):
            events.put({"event": "tick"})
    threading.Thread(target=_heartbeat, daemon=True,
                     name=f"hb-{turn_id}").start()

    def _stream():
        try:
            while True:
                ev = events.get()
                yield json.dumps(ev, ensure_ascii=False) + "\n"
                if ev.get("event") in fed_agent.TERMINAL_EVENTS:
                    break
        finally:
            _hb_stop.set()
            cancel_ev.set()
            with _AGENT_TURNS_LOCK:
                _AGENT_TURNS.pop(turn_id, None)
            fed_agent.forget_turn(turn_id)

    return _stream()


class AgentToolResultReq(BaseModel):
    turn_id: str
    call_id: str
    result: str

@app.post("/agent/tool_result")
def agent_tool_result(req: AgentToolResultReq):
    with _AGENT_TOOL_WAITS_LOCK:
        waiter = _AGENT_TOOL_WAITS.get((req.turn_id, req.call_id))
    if waiter is None:
        return {"accepted": False,
                "error": f"nobody is waiting ({req.turn_id}, {req.call_id}) — "
                         "turn cancelled or timeout?"}
    try:
        waiter.put_nowait(req.result)
        return {"accepted": True}
    except Exception as e:
        return {"accepted": False, "error": str(e)}



def _conversation_text(history, per_message=500, keep_last=None):
    lines = []
    for m in history or []:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content
                               if isinstance(p, dict) and p.get("type") == "text")
        if isinstance(content, str) and content.strip():
            lines.append(f"{role}: {content.strip()[:per_message]}")
    return lines[-keep_last:] if keep_last else lines


def _distill_json(system, user, max_tokens=None):
    try:
        r_ = _llm_call_json(user, max_tokens=max_tokens, timeout=90,
                            breaker=False, _who="DISTILL", system=system)
        return r_ if isinstance(r_, dict) else None
    except Exception as e:
        print(f"[DISTILL] {e}")
        return None

def _distill_facts(history):
    lines = _conversation_text(history)
    if not lines:
        return 0
    CHUNK_CHARS = 6000
    chunks, cur, cur_len = [], [], 0
    for line in lines:
        if cur_len + len(line) > CHUNK_CHARS and cur:
            chunks.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(line)
        cur_len += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))

    saved = 0
    _live = _live_facts()
    for chunk in chunks:
        system = _tr("prompts.fact_extractor")
        _rel = rank_by_words(chunk[:4000], _live, lambda f: f.get("text", ""), k=40)
        _rel = [h for h in _rel if h.get("score", 0) > 0] or sorted(
            _live, key=lambda f: f.get("ts", ""), reverse=True)[:40]
        known = "\n".join(f"- {f.get('text', '')}" for f in _rel)
        if known:
            system += _tr("prompts.fact_extractor_existing", existing=known[:3000])
        data = _distill_json(system, _tr("prompts.fact_extractor_user", convo=chunk))
        for f in ((data or {}).get("facts") or []):
            text = str(f.get("text", "")).strip()
            if len(text) < 5:
                continue
            try:
                _rep = {}
                if _add_fact(text, f.get("tags") or ["auto_extract"],
                             kind=f.get("kind", "durable"), report=_rep) is not None \
                        and _rep.get("action") != "same":
                    saved += 1
            except Exception as e:
                print(f"[DISTILL] fact: {e}")
    return saved


def _distill_lessons(history):
    lines = _conversation_text(history, keep_last=50)
    if not lines:
        return 0
    data = _distill_json(_tr("prompts.tone_lessons"),
                         _tr("rag.conv_label", convo="\n".join(lines)))
    saved = 0
    for l in ((data or {}).get("lessons") or []):
        text = str(l.get("text", "")).strip()
        if len(text) < 5:
            continue
        try:
            _add_lesson_with_consolidation(
                {"text": text, "tags": l.get("tags") or ["tone"],
                 "confidence": float(l.get("confidence", 0.7) or 0.7)}, "tone")
            saved += 1
        except Exception as e:
            print(f"[DISTILL] lesson: {e}")
    return saved


@app.get("/agent/context_limit")
def agent_context_limit(base_url: str = "", api_key: str = "", model: str = ""):
    if not base_url:
        base_url, api_key, model = _resolve_endpoint("API")
    for name, m in _model_pool().items():
        if model and m.get("model") == model and CONFIG:
            _ms = model_section(CONFIG, name)
            n = CONFIG.getint(_ms, "context_length", fallback=0) if _ms else 0
            if n > 0:
                return {"limit": n, "source": f"[MODEL:{name}] context_length"}
    ck = f"{base_url}|{model}"
    if ck in _CTX_LIMIT_CACHE:
        return {"limit": _CTX_LIMIT_CACHE[ck], "source": "cache"}

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    root = base_url[:-3] if base_url.endswith("/v1") else base_url
    limit, source = 0, "unknown"
    try:
        r = _http().get(root.rstrip("/") + "/props", headers=headers, timeout=5)
        if r.status_code == 200:
            d = r.json()
            limit = int((d.get("default_generation_settings") or {}).get("n_ctx")
                        or d.get("n_ctx") or 0)
            source = "/props"
    except Exception:
        pass
    if limit <= 0:
        _ow = _ollama_window(base_url, api_key, model)
        if _ow > 0:
            return {"limit": _ow, "source": "/api/ps"}
    if limit <= 0:
        try:
            r = _http().get(base_url.rstrip("/") + "/models", headers=headers,
                            timeout=5)
            if r.status_code == 200:
                _j = r.json() or {}
                data = [d for d in (_j.get("data") or _j.get("models") or []) if isinstance(d, dict)]
                _own = next((d for d in data if model and model in (d.get("id"), d.get("name"), d.get("model"))),
                            data[0] if data else {})
                limit = _window_of(_own)
                source = "/models"
        except Exception:
            pass
    if limit <= 0:
        try:
            r = _http().get(root.rstrip("/") + "/info", headers=headers, timeout=5)
            if r.status_code == 200:
                _i = r.json() or {}
                limit = int(_i.get("max_total_tokens") or _i.get("max_input_length") or 0)
                source = "/info"
        except Exception:
            pass
    if limit > 0:
        _CTX_LIMIT_CACHE[ck] = limit
        return {"limit": limit, "source": source}
    if CONFIG:
        n = CONFIG.getint("CHAT", "context_length", fallback=0)
        if n > 0:
            return {"limit": n, "source": "[CHAT] context_length"}
        if CONFIG.has_option("CHAT", "num_ctx"):
            return {"limit": 0, "source": "[CHAT] num_ctx is dead — "
                                          "rename it to context_length"}
    return {"limit": 0, "source": "unknown"}


_CTX_LIMIT_CACHE = {}
_LAST_TURN_EP = {}

_WINDOW_KEYS = ("max_model_len", "context_length", "context_window",
                "max_context_length", "n_ctx", "max_seq_len", "max_tokens_context")


def _window_of(entry):
    for src in (entry, entry.get("meta") if isinstance(entry.get("meta"), dict) else {},
                entry.get("top_provider") if isinstance(entry.get("top_provider"), dict) else {}):
        for k in _WINDOW_KEYS:
            try:
                n = int((src or {}).get(k) or 0)
            except (TypeError, ValueError):
                n = 0
            if n >= 1024:
                return n
    return 0


class MemoryDistillReq(BaseModel):
    history: List[Dict[str, Any]] = []
    kinds: List[str] = ["facts", "lessons"]


@app.post("/memory/distill")
def memory_distill(req: MemoryDistillReq):
    out = {"facts": 0, "lessons": 0}
    if "facts" in req.kinds:
        out["facts"] = _distill_facts(req.history)
    if "lessons" in req.kinds:
        out["lessons"] = _distill_lessons(req.history)
    return out


_COMPACT_SLICE_CHARS = 20000


class MemoryCompactReq(BaseModel):
    messages: List[Dict[str, Any]] = []
    keep_recent: int = 6
    summary: str = ""
    endpoint: dict = {}
    conv: List[str] = []


@app.get("/memory/proposals")
def memory_proposals():
    rows = [r for r in _proposals_rows() if r.get("status") == "pending"]
    rows.sort(key=lambda r: r.get("created", ""), reverse=True)
    return {"proposals": rows, "pending": len(rows)}


class CurateReq(BaseModel):
    limit: int = 0
    kinds: List[str] = []
    recheck_days: int = _CURATE_RECHECK_DAYS


@app.post("/memory/curate")
def memory_curate(req: CurateReq):
    return curate_memory(limit=(req.limit or None) if req.limit >= 0 else -1,
                         kinds=req.kinds or None,
                         recheck_days=req.recheck_days)


@app.post("/memory/review")
def memory_review_now(req: CurateReq):
    return memory_review(facts=(req.limit or None) if req.limit >= 0 else -1)


class TokensAdd(BaseModel):
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


@app.get("/stats/tokens")
def stats_tokens():
    return tokens_stats()


@app.post("/stats/tokens/reset")
def stats_tokens_reset():
    return tokens_reset()


@app.post("/stats/tokens/add")
def stats_tokens_add(req: TokensAdd):
    _count_tokens(req.model, {"prompt_tokens": req.prompt_tokens,
                              "completion_tokens": req.completion_tokens})
    return {"ok": True}


def _weigh_prompt(msgs):
    blocks = []
    for m in (msgs or []):
        c = m.get("content")
        if not isinstance(c, str) or not c.strip():
            continue
        pieces = (c.split("\n\n---\n\n") if m.get("role") == "user"
                  else [c])
        for piece in pieces:
            if not piece.strip():
                continue
            import fed_agent
            _lines = [l_ for l_ in piece.strip().splitlines() if l_.strip()]
            if _lines and _lines[0].strip().startswith(fed_agent.HARNESS_TAGS) \
                    and len(_lines) > 1:
                _lines = _lines[1:]
            head = (_lines[0] if _lines else "").strip()
            blocks.append({"role": m.get("role", ""), "head": head[:80],
                           "chars": len(piece),
                           "tokens": _estimate_prompt_tokens(
                               [{"role": m.get("role", ""), "content": piece}])})
    _deferred = bool(CONFIG.getboolean("AGENTIC", "defer_tool_schemas",
                                       fallback=True)) if CONFIG else True
    _all = (get_tools() or {}).get("tools") or []
    if _deferred:
        _hot = set(hot_tools()) | {"load_tools"}
        _carried = [t for t in _all
                    if ((t.get("function") or {}).get("name") in _hot)]
        try:
            _shelf = tool_catalogue(hot_tools())
            _shelf = _shelf if isinstance(_shelf, str) else json.dumps(_shelf)
        except Exception:
            _shelf = ""
        if _shelf:
            blocks.append({"role": "system", "head": "🧰 tool catalogue (shelf)",
                           "chars": len(_shelf),
                           "tokens": _estimate_prompt_tokens(
                               [{"role": "system", "content": _shelf}])})
    else:
        _carried, _shelf = _all, ""
    _heavy = []
    for _spec in _carried:
        _f = _spec.get("function") or {}
        try:
            _heavy.append({"name": _f.get("name", "?"),
                           "chars": len(json.dumps(_spec))})
        except Exception:
            pass
    _heavy.sort(key=lambda x: -x["chars"])
    blocks.sort(key=lambda b: -b["chars"])
    _msg_tokens = sum(b["tokens"] for b in blocks)
    _tool_tokens = max(0, _estimate_prompt_tokens([], _carried) - 1)
    return {"total_tokens": _msg_tokens + _tool_tokens,
            "deferred_schemas": _deferred,
            "messages": {"tokens": _msg_tokens,
                         "chars": sum(b["chars"] for b in blocks),
                         "blocks": len(blocks)},
            "tools": {"carried": len(_carried), "of": len(_all),
                      "tokens": _tool_tokens, "heaviest": _heavy[:15]},
            "by_block": blocks}


@app.get("/agent/prompt_budget")
def agent_prompt_budget(text: str = "hola"):
    try:
        msgs, _meta = prepare_turn_messages(
            [{"role": "user", "content": text or "hola"}])
    except Exception as e:
        return {"error": f"could not prepare the turn: {e}"}
    return {"question": text or "hola", **_weigh_prompt(msgs)}


class ContextMapReq(BaseModel):
    messages: List[Dict[str, Any]] = []
    code_project: str = ""


@app.post("/agent/context_map")
def agent_context_map(req: ContextMapReq):
    msgs = list(req.messages or [])
    if not msgs or msgs[-1].get("role") != "user":
        msgs.append({"role": "user", "content": "…"})
    try:
        prepared, _meta = prepare_turn_messages(msgs, code_project=req.code_project)
    except Exception as e:
        return {"error": f"could not prepare the turn: {e}"}
    out = _weigh_prompt(prepared)
    out["history"] = {"messages": len(req.messages or []),
                      "tokens": _estimate_prompt_tokens(req.messages or [])}
    _by_role = {}
    for m in (req.messages or []):
        r = m.get("role", "?")
        _by_role[r] = _by_role.get(r, 0) + _estimate_prompt_tokens([m])
    out["history"]["by_role"] = _by_role
    return out



@app.get("/memory/autolog")
def memory_autolog(limit: int = 20):
    rows = sorted(_autolog_rows(), key=lambda r: r.get("ts", ""), reverse=True)
    return {"entries": rows[:max(1, int(limit))],
            "pending_undo": sum(1 for r in rows if not r.get("undone")),
            "last_run": (_get_sql().meta_get("curate:last")
                         if _get_sql() is not None else "") or ""}


@app.post("/memory/autolog/{entry_id}/undo")
def memory_autolog_undo(entry_id: str):
    return memory_undo(entry_id)


class ProposalDecision(BaseModel):
    accept: bool = False


@app.post("/memory/proposals/{pid}")
def memory_proposal_decide(pid: str, req: ProposalDecision):
    sql = _get_sql()
    if sql is None:
        return {"error": "store unavailable"}
    row = next((r for r in _proposals_rows() if r.get("id") == pid), None)
    if row is None:
        return {"error": "not found"}
    if not req.accept:
        row["status"] = "rejected"
        row["decided"] = _chat_now_iso()
        sql.upsert("memory_proposals", pid, row)
        return {"rejected": True}
    fact_ = next((f for f in _facts_data
                  if f.get("id") == row.get("target_id")), None)
    if fact_ is None:
        row["status"] = "gone"
        sql.upsert("memory_proposals", pid, row)
        return {"error": "that fact no longer exists"}
    images = _apply_fact_change(row, fact_)
    row["status"] = "applied"
    row["decided"] = _chat_now_iso()
    sql.upsert("memory_proposals", pid, row)
    return {"applied": True, "action": row["action"], "touched": len(images)}


def _apply_fact_change(row, fact_):
    images = []
    if row["action"] in ("retire", "prune"):
        b = _archive_fact(fact_["id"], row["action"])
        if b:
            images.append(b)
    elif row["action"] == "merge":
        images.append(dict(fact_))
        _update_fact(fact_["id"], text=row["new_text"])
        with _facts_lock:
            _abs = [f for f in _facts_data
                    if f.get("id") in set(row.get("absorbed") or [])]
            _h = next((f for f in _facts_data if f["id"] == fact_["id"]), None)
            if _h is not None and _abs:
                _h["hits"] = max([_h.get("hits", 1)]
                                 + [a.get("hits", 1) for a in _abs])
        for a in list(row.get("absorbed") or []):
            if a != fact_["id"]:
                b = _archive_fact(a, "merged", by=fact_["id"])
                if b:
                    images.append(b)
        _save_facts_to_disk()
    else:
        images.append(dict(fact_))
        _update_fact(fact_["id"], text=row["new_text"])
        with _facts_lock:
            _h = next((f for f in _facts_data if f["id"] == fact_["id"]), None)
            if _h is not None:
                _h["kind"] = "durable"
        _save_facts_to_disk()
    return images


def _apply_curator_proposals(pids):
    sql = _get_sql()
    if sql is None or not pids:
        return []
    done = []
    rows = {r.get("id"): r for r in _proposals_rows()}
    for pid in pids:
        row = rows.get(pid)
        if not row or row.get("status") != "pending":
            continue
        fact_ = next((f for f in _facts_data if f.get("id") == row.get("target_id")), None)
        if fact_ is None or not _fact_live(fact_):
            continue
        images = _apply_fact_change(row, fact_)
        row["status"] = "applied"
        row["decided"] = _chat_now_iso()
        row["by"] = "curator"
        sql.upsert("memory_proposals", pid, row)
        entry = _autolog("facts_" + row["action"],
                         {"action": row["action"], "fact": row.get("target_text", "")[:160],
                          "new_text": (row.get("new_text") or "")[:160],
                          "why": (row.get("why") or "")[:200]},
                         {"facts_before": images, "proposal": pid})
        done.append({"proposal": pid, "action": row["action"], "entry": (entry or {}).get("id")})
    return done


_SUMMARY_MARK = "[SUMMARY"


def _summary_head_line(n, since=""):
    return ("%s — %d earlier message(s), compacted%s. The originals are in the "
            "conversation log, verbatim: search them by words (recall) — they "
            "were not copied anywhere else.]"
            % (_SUMMARY_MARK, int(n), (" since " + since) if since else ""))


def _summary_head_parse(head):
    import re as _re
    txt = str(head or "")
    m = _re.search(r"—\s*(\d+)\s+earlier message", txt)
    n = int(m.group(1)) if m else 0
    m2 = _re.search(r"compacted since ([^.\]]+)", txt)
    return ((m2.group(1).strip() if m2 else ""), n)


def _log_first_ts(session_ids):
    try:
        sql = _get_sql()
        if sql is None or not getattr(sql, "fts", False):
            return ""
        for sid in [i for i in (session_ids or []) if i]:
            with sql._lock:
                r = sql._conn.execute(
                    "SELECT min(ts) FROM msgs_fts WHERE session = ? AND ts != ''",
                    (str(sid),)).fetchone()
            if r and r[0]:
                return str(r[0])[:16].replace("T", " ")
    except Exception:
        pass
    return ""


@app.post("/memory/compact")
def memory_compact(req: MemoryCompactReq):
    keep = max(1, req.keep_recent)
    hist = req.messages or []
    if len(hist) <= keep + 2:
        return {"compacted": False, "messages": hist, "why": "little to compact"}
    import fed_agent as _fa
    cut = max(1, len(hist) - keep)
    _floor = max(1, len(hist) - keep * 4)
    while cut > _floor and not (hist[cut].get("role") == "user"
                                and _fa.user_said(hist[cut])):
        cut -= 1
    old, recent = hist[:cut], hist[cut:]

    prior, first_ts, n_prior = "", "", 0
    if old and old[0].get("role") == "system" \
            and str(old[0].get("content", "")).lstrip().startswith(_SUMMARY_MARK):
        _h = str(old.pop(0).get("content", ""))
        prior = _h.split("\n", 1)[-1].strip()
        first_ts, n_prior = _summary_head_parse(_h)
        if not old:
            return {"compacted": False, "messages": hist,
                    "why": "only the previous summary left to compact"}

    _lines = [f"[{m.get('role','?')}]: {m.get('content','')}"
              for m in old if isinstance(m.get("content"), str)]
    dump = "\n\n".join(_lines)
    if not dump.strip():
        return {"compacted": False, "messages": hist, "why": "nothing readable"}
    _slices, _cur, _size = [], [], 0
    for _ln in _lines:
        if _cur and _size + len(_ln) + 2 > _COMPACT_SLICE_CHARS:
            _slices.append(_cur)
            _cur, _size = [], 0
        _cur.append(_ln)
        _size += len(_ln) + 2
    if _cur:
        _slices.append(_cur)

    _ids = [i for i in (req.conv or []) if (i or "").strip()]
    if not first_ts:
        first_ts = _log_first_ts(_ids)

    ep = req.endpoint or {}
    summary = (prior or "").strip()
    if (getattr(req, "summary", "") or "").strip():
        summary = req.summary.strip()
        _slices = []
    for _k, _sl in enumerate(_slices):
        _msgs = [{"role": "system", "content": _tr("prompts.compactor")}]
        if summary:
            _msgs.append({"role": "user",
                          "content": "[ALREADY SUMMARISED — carry it forward, do "
                                     "not re-summarise it]:\n" + summary})
        _msgs.append({"role": "user", "content": "\n\n".join(_sl)})
        _part, _why_slice = "", ""
        for _attempt in range(2):
            try:
                raw = _llm_chat_with_tools(
                    _msgs, None, temperature=0.3, timeout=_compact_timeout(),
                    base_url=ep.get("base_url"), api_key=ep.get("api_key"),
                    model=ep.get("model"))
                _m = raw.get("message") or {}
                _part = _strip_think(_m.get("content", "") or "")
                _reason = _m.get("reasoning_content", "") or _m.get("reasoning", "") or ""
                if _part.strip():
                    break
                _why_slice = (f"the model returned only reasoning "
                              f"({len(_reason)} chars), no summary"
                              if _reason else "the model returned an empty summary")
                if _attempt == 0:
                    _msgs.append({"role": "assistant", "content": _reason or ""})
                    _msgs.append({"role": "user",
                                  "content": _tr("inject.answer_after_thinking")})
            except Exception as e:
                _why_slice = str(e)[:160]
                break
        if not _part.strip():
            _fail_why = f"slice {_k + 1}/{len(_slices)}: {_why_slice or 'no summary'}"
            print(f"[COMPACT] summary failed ({_fail_why})")
            summary = ""
            break
        summary = _part
    if not summary.strip():
        return {"compacted": False, "messages": hist,
                "why": locals().get("_fail_why") or "summary failed"}

    n_total = n_prior + len(old)
    head_line = _summary_head_line(n_total, first_ts)
    head = {"role": "system", "content": head_line + "\n" + summary}

    try:
        _sql = _get_sql()
        if _sql is not None and getattr(_sql, "fts", False):
            _sql.log_message(_ids[0] if _ids else "", _sql.ROLE_SUMMARY,
                             head["content"],
                             ts=datetime.now().isoformat())
    except Exception as e:
        print(f"[COMPACT] could not log the summary: {e}")

    _recent_blocks = sorted(
        ({"role": m.get("role", ""),
          "head": str(m.get("content", "")).strip().split("\n", 1)[0][:60],
          "tokens": _estimate_prompt_tokens([m])}
         for m in recent if isinstance(m.get("content"), str)),
        key=lambda b: -b["tokens"])
    return {"compacted": True, "messages": [head] + recent,
            "replaced": len(old), "summarised_total": n_total,
            "since": first_ts,
            "tokens_before": _estimate_prompt_tokens(hist),
            "tokens_after": _estimate_prompt_tokens([head] + recent),
            "recent_tokens": _estimate_prompt_tokens(recent),
            "recent_heaviest": _recent_blocks[:3]}


class PlanAckReq(BaseModel):
    ack_id: str
    decision: str = "cancel"
    feedback: str = ""
    goal: str = ""
    subtasks: Optional[List[Dict[str, Any]]] = None
    allow_expand: bool = False


@app.post("/agent/plan_ack")
def agent_plan_ack(req: PlanAckReq):
    with _PLAN_ACKS_LOCK:
        entry = _PLAN_ACKS.get(req.ack_id)
    if entry is None:
        return {"accepted": False,
                "error": f"no plan is waiting on '{req.ack_id}' — "
                         "cancelled, or it timed out"}
    try:
        entry["q"].put_nowait({"decision": req.decision,
                               "feedback": req.feedback,
                               "subtasks": req.subtasks,
                               "goal": req.goal,
                               "allow_expand": req.allow_expand})
        return {"accepted": True}
    except Exception as e:
        return {"accepted": False, "error": str(e)}


class CouncilAckReq(BaseModel):
    ack_id: str
    decision: str = "stop"


@app.post("/agent/council_ack")
def agent_council_ack(req: CouncilAckReq):
    with _COUNCIL_ACKS_LOCK:
        entry = _COUNCIL_ACKS.get(req.ack_id)
    if entry is None:
        return {"accepted": False,
                "error": f"no council is waiting on '{req.ack_id}' — "
                         "it stopped, or it timed out"}
    try:
        entry["q"].put_nowait({"decision": req.decision})
        return {"accepted": True}
    except Exception as e:
        return {"accepted": False, "error": str(e)}


class ChildToolsForReq(BaseModel):
    capabilities: List[str] = []


@app.post("/child/tools_for")
def child_tools_for(req: ChildToolsForReq):
    try:
        return {"tools": sorted(_pack_tool_names(
            {"capabilities": list(req.capabilities or [])}, terminal=True))}
    except Exception as e:
        return {"tools": [], "error": str(e)}


class ChildDesignReq(BaseModel):
    description: str
    name: str = ""


_CHILD_DESIGN_SYSTEM = (
    "You design a LOCAL child of this user: a specialised sub-agent that runs "
    "on their machine, defined by a pack. From the user's description, write "
    "the pack as ONE JSON object with these keys:\n"
    "  name: short, letters/digits/space/._- only, max 48, no '@' (keep the "
    "user's name if they gave one);\n"
    "  avatar: one emoji;\n"
    "  role: the prompt it lives by, in the second person, in the language of "
    "the description: what it is, what it knows, how it works, what it "
    "delivers and what it refuses. Concrete, a few paragraphs, no filler;\n"
    "  capabilities: the fewest of the given capabilities its work needs;\n"
    "  notes_folders: only folders from the given list that its work touches, "
    "or [] (an empty list means it sees every folder);\n"
    "  projects: only projects from the given list it should take care of, or [];\n"
    "  model: a pool alias from the given list only if the description asks "
    "for one, else \"\".\n"
    "Never invent a folder, project, capability or model that is not listed. "
    "Answer with the JSON object only.")


@app.post("/child/design")
def child_design(req: ChildDesignReq):
    desc = (req.description or "").strip()
    if not desc:
        return {"error": "Say what the child is for."}
    if req.name.strip() and hoplita_children.find(req.name.strip()) is not None:
        return {"error": f"You already have a child called '{req.name.strip()}'."}

    def _listed(tool, key):
        try:
            r = TOOLS[tool]["fn"]({}) or {}
            return [str(x) for x in (r.get(key) or []) if x]
        except Exception:
            return []
    folders = _listed("list_categories", "categories")
    projects = _listed("list_todo_projects", "projects")
    models = sorted(_model_pool())
    caps = sorted(hoplita_children.CAPABILITY_TOOLS)
    prompt = (f"Description: {desc}\n"
              + (f"Name the user chose: {req.name.strip()}\n" if req.name.strip() else "")
              + f"Capabilities: {', '.join(caps)}\n"
              f"Note folders: {', '.join(folders) or '(none)'}\n"
              f"Projects: {', '.join(projects) or '(none)'}\n"
              f"Pool models: {', '.join(models) or '(none)'}")
    pack = _llm_call_json(prompt, system=_CHILD_DESIGN_SYSTEM, timeout=240,
                          breaker=False, _who="CHILD-DESIGN")
    if not isinstance(pack, dict) or not str(pack.get("role") or "").strip():
        return {"error": "The model did not write a pack for that description; "
                         "try again, or describe it differently."}

    def _keep(v, allowed):
        v = v if isinstance(v, list) else []
        return [x for x in (str(i).strip() for i in v) if x in allowed]
    name = req.name.strip() or str(pack.get("name") or "").strip()
    if not req.name.strip():
        base, n = name, 2
        while name and hoplita_children.find(name) is not None:
            name, n = f"{base[:44]} {n}", n + 1
    model = str(pack.get("model") or "").strip()
    return hoplita_children.tool_child_create({
        "name": name,
        "role": str(pack.get("role")).strip(),
        "avatar": str(pack.get("avatar") or "").strip()[:4],
        "capabilities": _keep(pack.get("capabilities"), caps),
        "notes_folders": _keep(pack.get("notes_folders"), folders),
        "projects": _keep(pack.get("projects"), projects),
        "model": model if model in models else ""})


class AgentStopReq(BaseModel):
    turn_id: str


@app.post("/agent/stop")
def agent_stop(req: AgentStopReq):
    with _AGENT_TURNS_LOCK:
        ev = _AGENT_TURNS.get(req.turn_id)
    if ev is None:
        return {"stopped": False, "error": f"turn '{req.turn_id}' does not exist or already finished"}
    ev.set()
    return {"stopped": True, "turn_id": req.turn_id}



_TIED_MAX_BYTES = 2_800_000
_TIED_NEVER = ("/shutdown", "/tied/")
_SERVE = {"host": "127.0.0.1", "port": 9077}


def _run_tied_request(r, emit, cancel_flag):
    import base64 as _b64
    method = str(r.get("method") or "GET").upper()
    path = "/" + str(r.get("path") or "").lstrip("/")
    if any(path == n or path.startswith(n) for n in _TIED_NEVER):
        emit({"event": "proxy_response", "status": 403, "ctype": "application/json",
              "body_b64": _b64.b64encode(json.dumps(
                  {"error": f"{path} is not asked across machines"}).encode()).decode()})
        return None
    url = f"http://{_SERVE['host']}:{_SERVE['port']}{path}"
    if r.get("query"):
        url += "?" + str(r["query"])
    headers = {"content-type": r["ctype"]} if r.get("ctype") else {}
    body = _b64.b64decode(r.get("body_b64") or "")
    turn = {"id": None}
    done = threading.Event()

    def _watch():
        while not done.is_set():
            if cancel_flag.wait(0.5):
                if turn["id"]:
                    agent_stop(AgentStopReq(turn_id=turn["id"]))
                return
    threading.Thread(target=_watch, daemon=True, name="tied-stop").start()
    try:
        with requests.request(method, url, data=body or None, headers=headers,
                              stream=True, timeout=(10, None)) as resp:
            ctype = resp.headers.get("content-type", "")
            if "ndjson" in ctype:
                last = None
                for line in resp.iter_lines():
                    if cancel_flag.is_set():
                        break
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if ev.get("event") == "turn_start":
                        turn["id"] = ev.get("turn_id")
                    emit(ev)
                    last = ev
                emit({"event": "proxy_end", "status": resp.status_code})
                return last
            content = resp.content
            if (path == "/call" and resp.status_code == 200
                    and b'"chat_session_save"' in body):
                try:
                    _sess = (json.loads(body).get("arguments") or {}).get("session") or {}
                    emit({"event": "tab_saved", "uuid": _sess.get("uuid") or "",
                          "sync_id": _sess.get("sync_id") or ""})
                except Exception:
                    pass
            emit({"event": "proxy_response", "status": resp.status_code, "ctype": ctype,
                  "body_b64": _b64.b64encode(content).decode()})
            return None
    except Exception as e:
        emit({"event": "proxy_response", "status": 502, "ctype": "application/json",
              "body_b64": _b64.b64encode(json.dumps(
                  {"error": f"this machine's server did not answer: {e}"}).encode()).decode()})
        return None
    finally:
        done.set()


try:
    import fed_session as _fed_session_mod
    _fed_session_mod.TIED_REQUEST = _run_tied_request
except Exception as _e:
    print(f"[TIED] not available: {_e}", file=sys.stderr)


@app.api_route("/tied/{device}/{path:path}",
               methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def tied_proxy(device: str, path: str, request: Request):
    import base64 as _b64
    import fed_session
    from starlette.concurrency import run_in_threadpool
    from fastapi.responses import Response
    body = await request.body()
    r = {"method": request.method, "path": "/" + path.lstrip("/"),
         "query": request.url.query or "", "ctype": request.headers.get("content-type", ""),
         "body_b64": _b64.b64encode(body).decode()}
    tab = request.headers.get("x-hoplita-tab", "")
    said = r["path"]
    if r["path"] == "/agent/turn":
        try:
            said = _last_user_said(json.loads(body).get("messages") or []) or said
        except Exception:
            pass

    def _json(status, msg):
        return Response(json.dumps({"error": msg}, ensure_ascii=False), status_code=status,
                        media_type="application/json")
    try:
        me = fed_tools._store().data.get("handle", "")
    except Exception:
        me = ""
    if not me:
        return _json(409, "no federated identity here: a tab can only be tied "
                          "between machines of one identity")
    if len(body) > _TIED_MAX_BYTES:
        return _json(413, f"this request is {len(body) // 1_000_000} MB, more than one "
                          f"envelope carries to the tied machine")
    q = _queue.Queue()
    cancel_ev = threading.Event()

    def _run():
        try:
            orig = fed_session.SessionOriginator(
                me, q.put, device=device, tab=tab, extra={"tied_request": r})
            term = orig.start(str(said)[:4000], None, should_stop=cancel_ev.is_set)
        except Exception as e:
            term = {"event": "error", "error": f"the tied machine could not be reached: {e}"}
        q.put({"__end__": term})
    threading.Thread(target=_run, daemon=True, name="tied-proxy").start()

    def _next():
        while True:
            try:
                ev = q.get(timeout=30)
            except _queue.Empty:
                return {"event": "tick"}
            if ev.get("event") in ("session_open", "session_ack", "tab_saved", "queued"):
                continue
            return ev

    first = await run_in_threadpool(_next)
    while first.get("event") == "tick":
        if await request.is_disconnected():
            cancel_ev.set()
            return _json(499, "closed")
        first = await run_in_threadpool(_next)
    if "__end__" in first:
        term = first["__end__"] or {}
        return _json(502, term.get("error") or "the tied machine ended without an answer")
    if first.get("event") == "proxy_response":
        return Response(_b64.b64decode(first.get("body_b64") or ""),
                        status_code=int(first.get("status") or 200),
                        media_type=first.get("ctype") or None)

    def _stream():
        ev = first
        try:
            while True:
                if "__end__" in ev:
                    term = ev["__end__"] or {}
                    if term.get("event") == "error":
                        yield json.dumps(term, ensure_ascii=False) + "\n"
                    return
                if ev.get("event") == "proxy_end":
                    return
                if ev.get("event") == "tick":
                    yield "\n"
                else:
                    yield json.dumps(ev, ensure_ascii=False) + "\n"
                ev = _next()
        finally:
            cancel_ev.set()
    return StreamingResponse(_stream(), media_type="application/x-ndjson")


class HandsControlReq(BaseModel):
    turn_id: str = ""
    action: str = "status"


@app.post("/agent/hands")
def agent_hands(req: HandsControlReq):
    import hoplita_desktop as hd
    return hd.control(req.turn_id, req.action)


class AgentInjectReq(BaseModel):
    turn_id: str
    message: str


@app.post("/shutdown")
def shutdown_mcp(request: Request):
    host = (request.client.host if request.client else "") or ""
    if host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse({"error": "shutdown is local-only"}, status_code=403)
    print("[MCP] shutdown requested — closing the vault and exiting",
          file=sys.stderr)

    def _bye():
        time.sleep(0.4)
        try:
            _save_tool_uses()
        except Exception:
            pass
        try:
            fed_tools.stop_session_engine()
            fed_tools.stop_sync_engine()
        except Exception:
            pass
        try:
            import hoplita_federation as _fed
            _fed.ws_stop()
        except Exception:
            pass
        try:
            sql = _get_sql()
            if sql is not None and hasattr(sql, "close"):
                sql.close()
        except Exception:
            pass
        os._exit(0)

    threading.Thread(target=_bye, daemon=True, name="mcp-shutdown").start()
    return {"ok": True, "shutting_down": True}


@app.post("/agent/inject")
def agent_inject(req: AgentInjectReq):
    import fed_agent
    if fed_agent.inject(req.turn_id, req.message):
        return {"injected": True, "turn_id": req.turn_id}
    return {"injected": False,
            "error": f"turn '{req.turn_id}' does not exist or already finished"}


@app.post("/feedback")
def feedback(req: FeedbackReq):
    verdict = {"bad": "fail", "ko": "fail", "down": "fail", "no": "fail",
               "fail": "fail", "partial": "partial",
               "ok": "ok", "good": "ok", "up": "ok", "yes": "ok",
               }.get((req.feedback or "").strip().lower())
    if verdict is None:
        return {"saved": False,
                "error": f"unknown verdict {req.feedback!r}; use ok, partial "
                         f"or fail"}
    result_text = req.result
    if (req.note or "").strip():
        result_text = f"{req.result}\n\nUSER SAID: {req.note.strip()}"
    if verdict == "fail":
        result = save_experience(req.goal, req.steps, f"FAIL: {result_text}",
                                  "fail", req.tags + ["failed"], req.duration_s)
        steps_for_lessons, fb_for_lessons = req.steps, "fail"
    elif verdict == "partial":
        good = [s for i, s in enumerate(req.steps) if i in req.partial_good_steps]
        if not good:
            return {"saved": False}
        result = save_experience(req.goal, good, result_text, "partial",
                                  req.tags, req.duration_s)
        steps_for_lessons, fb_for_lessons = good, "partial"
    else:
        result = save_experience(req.goal, req.steps, result_text, "ok",
                                  req.tags, req.duration_s)
        steps_for_lessons, fb_for_lessons = req.steps, "ok"

    global _LAST_USER_ACTIVITY
    _LAST_USER_ACTIVITY = time.time()

    if result.get("saved") and CONFIG.getboolean("LESSONS", "enabled", fallback=True):
        _ensure_lesson_worker()
        _LESSON_EXTRACTION_QUEUE.put(
            (req.goal, steps_for_lessons, result_text, fb_for_lessons,
             req.tags, result.get("id", "unknown"),
             (req.note or "").strip()))
        result["lessons_extraction"] = f"queued (size={_LESSON_EXTRACTION_QUEUE.qsize()})"
    return result


@app.delete("/experience/{exp_id}")
def experience_delete(exp_id: str):
    global _exp_docs, _exp_meta
    idx = next((i for i, m in enumerate(_exp_meta)
                if str(m.get("id", "")) == str(exp_id)), -1)
    if idx < 0:
        return {"deleted": False, "error": "not found"}
    _exp_docs.pop(idx)
    _exp_meta.pop(idx)
    _save_exp_to_disk()
    return {"deleted": True, "left": len(_exp_docs)}


@app.post("/experience/search")
def exp_search(req: ExpSearchReq):
    return search_experience(req.query, req.n_results)



class LessonsSearchReq(BaseModel):
    query: str
    n_results: int = 3
    min_confidence: float = 0.0


@app.post("/lessons/search")
def lessons_search(req: LessonsSearchReq):
    return search_lessons(req.query, req.n_results, req.min_confidence)


class LessonAddReq(BaseModel):
    text: str
    tags: list = []
    confidence: float = 0.7
    source: str = "conversation"


@app.post("/lessons/add")
def lessons_add(req: LessonAddReq):
    text = (req.text or "").strip()
    if not text:
        return {"added": False, "error": "empty text"}
    lesson = {
        "text": text[:200],
        "tags": [t for t in (req.tags or []) if isinstance(t, str)][:5],
        "confidence": max(0.0, min(1.0, float(req.confidence))),
    }
    source_id = f"{req.source}_{uuid.uuid4().hex[:8]}"
    _add_lesson_with_consolidation(lesson, source_id)
    return {"added": True, "lesson": lesson}



class FactAddReq(BaseModel):
    text: str
    tags: list = []


class FactsSearchReq(BaseModel):
    query: str
    n_results: int = 3
    min_score: float = 0.5


@app.post("/facts/add")
def facts_add(req: FactAddReq):
    f = _add_fact(req.text, req.tags)
    return {"added": f is not None, "fact": f}


@app.post("/facts/search")
def facts_search(req: FactsSearchReq):
    return {"facts": search_facts(req.query, req.n_results, req.min_score)}


class NotesSearchReq(BaseModel):
    query: str
    category: str = None
    n_results: int = 5


@app.post("/notes/search")
def notes_search(req: NotesSearchReq):
    res = tool_search_notes(req.query, req.category).get("results", [])
    out = []
    for r in res[:req.n_results]:
        content = ""
        for n in notes_mgr.get_notes(r.get("category", "")):
            if n.get("id") == r.get("id"):
                content = n.get("content", "")
                break
        out.append({"category": r.get("category"), "title": r.get("title"),
                    "id": r.get("id"), "score": r.get("score", 0.0),
                    "content": content})
    return {"results": out}


@app.post("/facts/inject")
def facts_inject(req: FactsSearchReq):
    facts = select_facts_for_injection(req.query, budget=req.n_results or None)
    return {"facts": facts, "count": len(facts)}


def _fact_looks_generic(text):
    t = (text or "").strip()
    if not t:
        return True
    if any(c.isdigit() for c in t):
        return False
    for w in t.split()[1:]:
        w = w.strip("«»\"'(),.;:")
        if w[:1].isupper() or "/" in w or "." in w[1:]:
            return False
    return True


@app.get("/facts/list")
def facts_list():
    try:
        _, _inside, _outside = facts_catalogue()
    except Exception:
        _inside = _outside = None
    _arch = [f for f in _facts_data if not _fact_live(f)]
    return {"facts": [dict(f, generic=_fact_looks_generic(f.get("text", "")))
                      for f in _facts_data],
            "total": len(_facts_data),
            "live": sum(1 for f in _facts_data if _fact_live(f)),
            "archived": len(_arch),
            "archived_bytes": len(json.dumps(_arch, ensure_ascii=False).encode("utf-8")),
            "injected": _inside, "outside": _outside,
            "generic": sum(1 for f in _facts_data
                           if _fact_looks_generic(f.get("text", "")))}


_PURGE_WORDS = ("BORRAR TODO", "DELETE ALL")


class LogPurgeReq(BaseModel):
    session: Optional[str] = None
    all: bool = False
    confirm: str = ""


@app.get("/log/sessions")
def log_sessions():
    sql = _get_sql()
    if sql is None or not getattr(sql, "fts", False):
        return {"sessions": [], "total": 0}
    return {"sessions": sql.sessions_logged(), "total": sql.messages_count()}


@app.post("/log/purge")
def log_purge(req: LogPurgeReq):
    sql = _get_sql()
    if sql is None or not getattr(sql, "fts", False):
        return {"error": "log unavailable"}
    if req.all:
        if (req.confirm or "").strip().upper() not in _PURGE_WORDS:
            return {"error": f"confirm must be exactly one of {_PURGE_WORDS}",
                    "would_delete": sql.messages_count()}
        n = sql.forget_messages()
    elif req.session is not None:
        n = sql.forget_messages(session=req.session)
    else:
        return {"error": "give a session, or all=true"}
    return {"deleted": n, "left": sql.messages_count()}


class FactsPurgeReq(BaseModel):
    ids: list = []
    all: bool = False
    archived: bool = False
    confirm: str = ""


def _autolog_forget_facts(ids):
    sql = _get_sql()
    ids = set(ids or [])
    if sql is None or not ids:
        return 0
    n = 0
    for r in _autolog_rows():
        u = r.get("undo") or {}
        if any(isinstance(i, dict) and i.get("id") in ids
               for i in (u.get("facts_before") or [])):
            r["undo"] = {}
            r["undo_gone"] = "purged"
            sql.upsert(_AUTOLOG_STORE, r["id"], r)
            n += 1
    return n


@app.post("/facts/purge")
def facts_purge(req: FactsPurgeReq):
    if req.all:
        if (req.confirm or "").strip().upper() not in _PURGE_WORDS:
            return {"error": f"confirm must be exactly one of {_PURGE_WORDS}",
                    "would_delete": len(_facts_data)}
        ids = [f.get("id") for f in list(_facts_data)]
    elif req.archived:
        ids = [f.get("id") for f in list(_facts_data) if not _fact_live(f)]
    else:
        ids = [i for i in (req.ids or []) if i]
    gone = [i for i in ids if _remove_fact(i)]
    _autolog_forget_facts(gone)
    return {"deleted": len(gone), "left": len(_facts_data),
            "archived_left": sum(1 for f in _facts_data if not _fact_live(f))}


@app.delete("/facts/{fact_id}")
def facts_delete(fact_id: str):
    ok = _remove_fact(fact_id)
    if ok:
        _autolog_forget_facts([fact_id])
    return {"deleted": ok}


class FactUpdateReq(BaseModel):
    text: str = None
    tags: list = None
    hits: int = None


@app.patch("/facts/{fact_id}")
def facts_update(fact_id: str, req: FactUpdateReq):
    fact = _update_fact(fact_id, text=req.text, tags=req.tags, hits=req.hits)
    if fact is None:
        return {"updated": False, "error": "fact no encontrado"}
    return {"updated": True, "fact": fact}


@app.get("/lessons/list")
def lessons_list():
    return {
        "total": len(_lessons_data),
        "active": sum(1 for l in _lessons_data if not l.get("stale")),
        "lessons": _lessons_data,
    }


@app.delete("/lessons/{lesson_id}")
def lessons_delete(lesson_id: str):
    global _lessons_data
    with _lessons_lock:
        before = len(_lessons_data)
        _lessons_data = [l for l in _lessons_data if l.get("id") != lesson_id]
        if len(_lessons_data) == before:
            return {"deleted": False, "reason": "not_found"}
        _save_lessons_to_disk()
    return {"deleted": True, "remaining": len(_lessons_data)}



def _scrub_startup_env(name):
    system = platform.system()
    if system not in ("Linux", "Darwin"):
        return
    try:
        import ctypes
        libc = ctypes.CDLL(None)
        if system == "Darwin":
            get_env = libc._NSGetEnviron
            get_env.restype = ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
            env = get_env().contents
        else:
            env = ctypes.POINTER(ctypes.c_void_p).in_dll(libc, "environ")
        key = (name + "=").encode()
        i = 0
        while env[i]:
            entry = ctypes.string_at(env[i])
            if entry.startswith(key):
                ctypes.memset(env[i] + len(key), 0, len(entry) - len(key))
            i += 1
    except Exception as e:
        print(f"[VAULT] could not scrub the start-up environment: {e}", file=sys.stderr)


def _gave_up(why):
    try:
        from hoplita_paths import hoplita_base
        with open(os.path.join(hoplita_base(), "hoplita-mcp.gaveup"), "w", encoding="utf-8") as f:
            json.dump({"at": time.time(), "why": why}, f)
    except Exception:
        pass
    if os.environ.get("HOPLITA_GUI_PROMPT") == "1":
        try:
            from PyQt6.QtWidgets import QApplication, QMessageBox
            QApplication.instance() or QApplication(sys.argv)
            QMessageBox.critical(None, "HOPLITA MCP", why)
        except Exception:
            pass
    sys.exit(1)


def _get_vault_password(note=""):
    from hoplita_i18n import t as _t
    env_pwd = os.environ.get("HOPLITA_VAULT_PASSWORD")
    if env_pwd:
        _scrub_startup_env("HOPLITA_VAULT_PASSWORD")
    os.environ.pop("HOPLITA_VAULT_PASSWORD", None)
    if env_pwd:
        print("🔐 " + _t("mcp.password_from_env"))
        return env_pwd
    kp = os.path.join(hoplita_base(), "vault.key")
    if (os.environ.get("HOPLITA_VAULT") == "open" or os.path.exists(kp)
            or (os.environ.get("HOPLITA_SPAWN_TOKEN")
                and not os.path.exists(os.path.join(hoplita_base(), "hoplita.db")))):
        if not os.path.exists(kp):
            import secrets as _secrets
            fd = os.open(kp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(_secrets.token_urlsafe(32))
        print("🔓 " + _t("mcp.vault_open", path=kp))
        with open(kp, encoding="utf-8") as fh:
            return fh.read().strip()
    force_gui = os.environ.get("HOPLITA_GUI_PROMPT") == "1"
    _new = not os.path.exists(os.path.join(hoplita_base(), "hoplita.db"))
    _joins = _new and os.path.exists(os.path.join(hoplita_base(), "fed_identity.json.encrypted"))
    if not force_gui and sys.stdin and sys.stdin.isatty():
        if not _new:
            return getpass.getpass("🔐 " + _t("mcp.vault_prompt_known") + " ")
        if _joins:
            print(_t("mcp.vault_join"))
            return getpass.getpass("🔐 " + _t("mcp.vault_join_prompt") + " ")
        while True:
            print("⚠ " + _t("mcp.vault_new_head"))
            print(_t("mcp.vault_new"))
            p1 = getpass.getpass("🔐 " + _t("mcp.vault_new_prompt") + " ")
            if p1 and p1 == getpass.getpass("🔐 " + _t("mcp.vault_again") + " "):
                return p1
            print(_t("mcp.vault_mismatch"))
    print("🔐 " + _t("mcp.vault_qt_dialog"), file=sys.stderr)
    try:
        from PyQt6.QtWidgets import QApplication, QInputDialog, QLineEdit
        app = QApplication.instance() or QApplication(sys.argv)
        app.setStyleSheet(
            "QInputDialog, QDialog { background:#0f172a; }"
            "QLabel { color:#e2e8f0; font-size:13px; }"
            "QLineEdit { background:#1e293b; color:#e2e8f0; border:1px solid "
            "#334155; border-radius:6px; padding:6px 10px; }"
            "QPushButton { background:#3b82f6; color:white; border:none; "
            "border-radius:6px; padding:6px 16px; font-weight:bold; }"
            "QPushButton:hover { background:#2563eb; }")
        from PyQt6.QtCore import Qt as _Qt, QTimer as _QT
        dlg = QInputDialog()
        dlg.setWindowTitle("HOPLITA MCP")
        dlg.setTextEchoMode(QLineEdit.EchoMode.Password)
        dlg.setWindowFlag(_Qt.WindowType.WindowStaysOnTopHint, True)

        def _front():
            dlg.raise_()
            dlg.activateWindow()
            if sys.platform == "darwin":
                import mac_dialogs
                mac_dialogs.activate_app()
        _QT.singleShot(0, _front)
        _keep = _QT(dlg)
        _keep.timeout.connect(lambda: dlg.isActiveWindow() or _front())
        _keep.start(1500)
        _QT.singleShot(15000, _keep.stop)

        def _ask(label):
            dlg.setTextValue("")
            dlg.setLabelText(label)
            from PyQt6.QtWidgets import QLabel as _QL
            for _lb in dlg.findChildren(_QL):
                _lb.setWordWrap(True)
            dlg.setFixedWidth(460)
            ok_pressed = dlg.exec() == 1
            if not ok_pressed:
                print("❌ " + _t("mcp.vault_cancelled"), file=sys.stderr)
                _gave_up(_t("mcp.vault_cancelled_gui"))
            return dlg.textValue()

        if not _new:
            return _ask((note + "\n\n" if note else "") + "🔐 " + _t("mcp.vault_prompt_known"))
        if _joins:
            import html as _html_mod
            dlg.setWindowTitle(_t("mcp.vault_join_title"))
            return _ask('<p style="color:#93c5fd;font-weight:bold;font-size:15px;">'
                        + _html_mod.escape(_t("mcp.vault_join_head")) + "</p><p>"
                        + _html_mod.escape(_t("mcp.vault_join")) + "</p>"
                        + ('<p style="color:#f87171;">' + _html_mod.escape(note) + "</p>" if note else "")
                        + "<p>🔐 " + _html_mod.escape(_t("mcp.vault_join_prompt")) + "</p>")
        import html as _html_mod
        dlg.setWindowTitle(_t("mcp.vault_new_title"))
        _warn = ('<p style="color:#fbbf24;font-weight:bold;font-size:15px;">'
                 + _html_mod.escape(_t("mcp.vault_new_head")) + "</p><p>"
                 + _html_mod.escape(_t("mcp.vault_new")) + "</p>")
        _note = note
        while True:
            p1 = _ask(_warn + ('<p style="color:#f87171;">' + _html_mod.escape(_note) + "</p>" if _note else "")
                      + "<p>🔐 " + _html_mod.escape(_t("mcp.vault_new_prompt")) + "</p>")
            if p1 and p1 == _ask(_warn + "<p>🔐 " + _html_mod.escape(_t("mcp.vault_again")) + "</p>"):
                return p1
            _note = _t("mcp.vault_mismatch")
    except ImportError:
        print("❌ " + _t("mcp.vault_no_qt"), file=sys.stderr)
        print("   " + _t("mcp.vault_no_qt_hint1"), file=sys.stderr)
        print("   " + _t("mcp.vault_no_qt_hint2"),
              file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error opening Qt dialog: {e}", file=sys.stderr)
        print("   As a workaround, set the HOPLITA_VAULT_PASSWORD env var.",
              file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    import argparse, uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9077)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--playwright-install", action="store_true",
                        help="Install the Playwright Chromium browser and exit "
                             "(used by the Inno installer after deployment).")
    args = parser.parse_args()

    if args.playwright_install:
        try:
            import subprocess as _sp
            print("Installing Chromium browser for browser-use...")
            _sp.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)
            print("Browser installed (or already present).")
        except Exception as _e:
            print(f"Could not install the browser: {_e}")
        sys.exit(0)

    def _t0(key, **kw):
        try:
            from hoplita_i18n import t as _tt
            return _tt(key, **kw)
        except Exception:
            return key
    try:
        from hoplita_i18n import load_language as _load_lang
        _load_lang(load_config().get("APP", "language", fallback="en"))
    except Exception as _le:
        print(f"[MCP] i18n: {_le}")
    from hoplita_i18n import t as _t

    log_path = os.environ.get("HOPLITA_LOGFILE")
    if sys.stdout is None or sys.stderr is None:
        if not log_path:
            log_path = os.path.join(hoplita_base(), "hoplita-mcp.log")
        class _Null:
            def write(self, *a): pass
            def flush(self): pass
            def isatty(self): return False
            def fileno(self): raise OSError("no fileno")
            @property
            def encoding(self): return "utf-8"
        if sys.stdout is None: sys.stdout = _Null()
        if sys.stderr is None: sys.stderr = _Null()
    if log_path:
        try:
            log_file = open(log_path, "a", encoding="utf-8", buffering=1)
            log_file.write(f"\n\n=== HOPLITA MCP start {datetime.now().isoformat()} ===\n")

            class _Tee:
                def __init__(self, *streams): self.streams = streams
                def write(self, data):
                    for s in self.streams:
                        try: s.write(data)
                        except Exception: pass
                def flush(self):
                    for s in self.streams:
                        try: s.flush()
                        except Exception: pass
                def isatty(self):
                    for s in self.streams:
                        try:
                            if hasattr(s, "isatty"):
                                return s.isatty()
                        except Exception: pass
                    return False
                def fileno(self):
                    for s in self.streams:
                        try:
                            if hasattr(s, "fileno"):
                                return s.fileno()
                        except Exception: pass
                    raise OSError("no fileno")
                @property
                def encoding(self):
                    for s in self.streams:
                        enc = getattr(s, "encoding", None)
                        if enc: return enc
                    return "utf-8"

            sys.stdout = _Tee(sys.stdout, log_file)
            sys.stderr = _Tee(sys.stderr, log_file)
            print("[MCP] " + _t0("mcp.log_dual", path=log_path))
        except Exception as e:
            print("[MCP] ⚠ " + _t0("mcp.log_fail", path=log_path, err=e))

    import hoplita_version as _ver
    _lab = _ver.label()
    _C, _R = "\033[38;5;39m", "\033[0m"
    _w = 39
    print("\n╔" + "═" * _w + "╗")
    print("║" + "HOPLITA MCP".center(_w) + "║")
    print("║" + (" " * ((_w - len(_lab)) // 2)) + _C + _lab + _R
          + (" " * (_w - len(_lab) - (_w - len(_lab)) // 2)) + "║")
    print("╚" + "═" * _w + "╝\n")
    import socket as _socket
    test_sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    test_sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    try:
        test_sock.bind((args.host, args.port))
        test_sock.close()
    except OSError as e:
        print("\n❌ " + _t("mcp.port_busy", port=args.port, host=args.host,
                            err=e))
        print("   " + _t("mcp.port_busy_hint"))
        if platform.system() == "Windows":
            print(f"     PowerShell: Get-NetTCPConnection -LocalPort {args.port} | "
                  f"ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force }}")
            print(f"     CMD:        for /f \"tokens=5\" %a in "
                  f"('netstat -ano ^| findstr :{args.port}') do taskkill /F /PID %a")
        else:
            print(f"     Linux/Mac:  fuser -k {args.port}/tcp")
        sys.exit(1)

    from hoplita_paths import hoplita_base as _hb
    os.makedirs(_hb(), exist_ok=True)
    try:
        os.remove(os.path.join(_hb(), "hoplita-mcp.gaveup"))
    except OSError:
        pass
    password = _get_vault_password()

    try:
        _attempt = 0
        while True:
            try:
                if not password:
                    raise VaultPasswordError("empty")
                init_managers(password)
                break
            except VaultPasswordError:
                _attempt += 1
                if _attempt >= 3:
                    print(_t("mcp.wrong_password_final"))
                    _gave_up(_t("mcp.wrong_password_final_gui"))
                print(_t("mcp.wrong_password"))
                password = _get_vault_password(
                    note=_t("mcp.wrong_password_try", n=_attempt + 1, of=3))
        register_tools()
        register_tool_params()
        register_hands_tools()
        _load_firewall()
        _ensure_venv_in_path()
        _load_external_mcps()
        register_manager_map()
        _enroll_from_env()
        threading.Thread(target=backfill_message_log, daemon=True).start()

        try:
            import fed_tools
            fed_tools.start_sync_engine()
            print("[MCP] ✓ Shared-folder sync engine active")
        except Exception as _e:
            print("[MCP] ⚠ " + _t0("mcp.sync_engine_fail", err=_e))

        if CONFIG and CONFIG.getboolean("MCP", "sync_on_boot", fallback=False):
            def _boot_pull():
                try:
                    import fed_tools as _ft
                    r = _ft.tool_fed_sync_pull() or {}
                    if r.get("skipped"):
                        print("[MCP] – " + _t0("mcp.boot_sync_skipped",
                                               why=r["skipped"]))
                    elif r.get("error"):
                        print("[MCP] ⚠ " + _t0("mcp.boot_sync_fail",
                                               err=r["error"]), file=sys.stderr)
                    elif r.get("version") is None:
                        print("[MCP] – " + _t0("mcp.boot_sync_empty"))
                    else:
                        print("[MCP] ✓ " + _t0("mcp.boot_sync_ok",
                                               n=r.get("merged", 0),
                                               v=r["version"]))
                except Exception as _e:
                    print("[MCP] ⚠ " + _t0("mcp.boot_sync_fail", err=_e),
                          file=sys.stderr)
            threading.Thread(target=_boot_pull, daemon=True,
                             name="fed_boot_pull").start()
        try:
            import fed_agent
            fed_agent.set_result_cap(CONFIG.getint(
                "AGENTIC", "tool_result_max_chars", fallback=8000))
            fed_agent.configure(
                llm_chat=_llm_chat_with_tools,
                get_tools=_allowed_tools,
                get_tool_params=lambda: TOOL_PARAMS,
                max_iter=CONFIG.getint("CHAT", "agentic_max_iterations", fallback=8),
                translate=_t,
                get_max_tokens=_llm_max_tokens,
                qa_budget_chars=CONFIG.getint("CHAT", "qa_budget_chars",
                                              fallback=40000))

            def _side_chat(m, tl, max_tokens=None):
                _b, _k, _mo = _resolve_endpoint("MEMORY")
                return _llm_chat_with_tools(m, tl, base_url=_b, api_key=_k,
                                            model=_mo, max_tokens=max_tokens)
            _side_chat.label = _background_model_name
            fed_agent.configure_side_chat(_side_chat)
            if CONFIG.getboolean("AGENTIC", "defer_tool_schemas", fallback=True):
                _load_tool_uses()
                fed_agent.configure_deferred(hot=hot_tools,
                                             catalogue=tool_catalogue,
                                             on_tool_used=_tool_used)
                print(f"[MCP] ✓ schemas on demand "
                      f"(hot: {len(hot_tools())}, "
                      f"shelf: {len(tool_catalogue(hot_tools()))})")
            print("[MCP] ✓ MCP agentic loop configured")
        except Exception as _e:
            print(f"[MCP] ⚠ MCP agentic loop failed to configure: {_e}")
        try:
            import fed_agent
            fed_agent.set_metacog(
                enabled=CONFIG.getboolean("CHAT", "metacog", fallback=True),
                stall_seconds=CONFIG.getint("CHAT", "metacog_stall_seconds",
                                            fallback=120),
                stall_tools=CONFIG.getint("CHAT", "metacog_stall_tools",
                                          fallback=4),
                heavy_cut=CONFIG.getint("CHAT", "heavy_sterile_cut",
                                        fallback=3))
        except Exception as _e:
            print(f"[MCP] ⚠ metacog knobs not applied (loop still up): {_e}",
                  file=sys.stderr)
        try:
            import fed_session, fed_agent, fed_tools
            fed_session.configure(
                store_getter=lambda: fed_store,
                run_turn=fed_agent.run_agentic_turn,
                execute_local_tool=lambda name, args: (
                    json.dumps(TOOLS[name]["fn"](args), ensure_ascii=False)
                    if name in TOOLS else json.dumps({"error": f"tool '{name}' does not exist"})),
                session_system_getter=_build_session_system,
                session_tools_getter=_session_pack_tools,
                orchestration_ctx=lambda **kw: _orchestration_ctx(
                    OrchestrationCtx(remote=True, **kw)))
            fed_tools.start_session_engine()
            print("[MCP] ✓ Federated sessions active (ask remote agent)")
        except Exception as _e:
            print(f"[MCP] ⚠ Federated sessions failed to start: {_e}")
    except Exception as e:
        import traceback as _tb
        print(f"❌ MCP startup failed: {e}")
        _tb.print_exc()
        try:
            _crash = os.path.join(hoplita_base(), "hoplita-mcp.log")
            with open(_crash, "a", encoding="utf-8") as _lf:
                _lf.write(f"\n=== STARTUP CRASH {datetime.now().isoformat()} ===\n")
                _tb.print_exc(file=_lf)
            print(f"   (traceback saved to {_crash})")
        except Exception:
            pass
        sys.exit(1)
    print(f"\n  ✓ {len(TOOLS)} tools\n  ✓ http://{args.host}:{args.port}\n")
    print(f"[HOPLITA MCP] {_ver.long_label()} starting on "
          f"{args.host}:{args.port}")
    _SERVE["host"] = "127.0.0.1" if args.host in ("0.0.0.0", "::", "") else args.host
    _SERVE["port"] = args.port
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
