#!/usr/bin/env python3


_EXACT = {
    "plan_and_orchestrate": "agents", "call_council": "agents",
    "fed_ask_agent": "agents", "fed_spawn_child": "agents",
    "fed_list_children": "agents", "fed_revoke_child": "agents",
    "search_web": "web", "browse_web": "web", "scrape_url": "web",
    "screenshot_web": "web", "call_api": "web",
    "run_command": "system", "read_file": "system", "read_folder": "system",
    "see_image": "system",
    "list_dir": "system", "edit_file": "system", "generate_pdf": "system",
    "load_tools": "system", "get_agentic_config": "system", "alert": "system",
    "see_my_config": "system", "edit_my_config": "system",
    "read_my_docs": "system",
    "add_note": "notes", "read_note": "notes", "update_note": "notes",
    "delete_note": "notes", "search_notes": "notes",
    "list_categories": "notes", "use_skill": "notes", "skill_folder": "notes",
    "recall": "memory", "search_rag": "memory", "intent": "memory",
    "propose_memory_change": "memory", "working_memory_forget": "memory",
    "search_working_memory": "memory",
    "add_task": "tasks", "update_task_progress": "tasks",
    "task_depends": "tasks",
    "get_todo_context": "tasks", "project_journal": "tasks",
    "project_write": "tasks",
    "sticky_note": "chat",
    "render_html": "canvas", "dashboard_render": "canvas",
    "canvas_undo": "canvas",
    "generate_image": "canvas", "edit_image": "canvas",
    "fill_credentials": "creds",
}

_PREFIX = (
    ("fed_code_", "fedcode"),
    ("fed_sync_", "fedsync"),
    ("chat_session_", "chat"),
    ("code_", "code"), ("git_", "code"),
    ("canvas_", "canvas"),
    ("wb_", "whiteboard"),
    ("hands_", "desktop"),
    ("session_", "chat"),
    ("device_", "device"),
    ("child_", "agents"),
    ("pdf", "system"),
    ("office", "system"),
)

_FED_ACCOUNT = {"fed_register", "fed_verify", "fed_login", "fed_status",
                "fed_quota", "fed_motd", "fed_leave"}
_FED_CONTACTS = {"fed_add_contact", "fed_remove_contact", "fed_list_contacts",
                 "fed_accept_contact", "fed_set_permissions"}
_FED_INBOX = {"fed_inbox", "fed_notices", "fed_activity", "fed_activity_clear",
              "fed_debug_notes"}

_SUBJECT = (
    ("fact", "memory"), ("lesson", "memory"), ("experience", "memory"),
    ("todo_project", "tasks"),
    ("appointment", "calendar"),
    ("cron", "cron"),
    ("credential", "creds"), ("api_key", "creds"), ("card", "creds"),
    ("otp", "creds"),
)

OTHER = "other"


def tool_category(name):
    n = (name or "").strip()
    if not n:
        return OTHER
    if "." in n:
        return "mcp:" + n.split(".", 1)[0]
    if n in _EXACT:
        return _EXACT[n]
    for p, c in _PREFIX:
        if n.startswith(p):
            return c
    if n.startswith("fed_"):
        if n in _FED_ACCOUNT:
            return "fedaccount"
        if n in _FED_CONTACTS:
            return "fedcontacts"
        if n in _FED_INBOX or n.endswith("_guarded"):
            return "fedsync"
        return "fedshare"
    for k, c in _SUBJECT:
        if k in n:
            return c
    return OTHER


_ORDER = ("system", "code", "web", "desktop", "notes", "memory", "tasks", "calendar",
          "cron", "chat", "canvas", "whiteboard", "creds", "agents", "device",
          "fedaccount", "fedcontacts", "fedshare", "fedcode", "fedsync")

def group_tools(names):
    buckets = {}
    for n in names:
        buckets.setdefault(tool_category(n), []).append(n)
    known = [c for c in _ORDER if c in buckets]
    extra = sorted(c for c in buckets if c not in _ORDER and c != OTHER)
    tail = [OTHER] if OTHER in buckets else []
    return [(c, sorted(buckets[c])) for c in known + extra + tail]


