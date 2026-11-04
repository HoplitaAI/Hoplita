#!/usr/bin/env python3

import configparser
import json
import html as _h
import platform
import re

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QMessageBox, QCheckBox, QComboBox, QDialog,
                             QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPlainTextEdit, QPushButton, QRadioButton,
                             QScrollArea, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

CHAT_TAB = [
    ("CHAT", "agentic_max_iterations", "int", (1, 200)),
    ("CHAT", "client_tool_timeout", "int", (60, 21600)),
    ("CHAT", "max_threads", "int", (1, 64)),
    ("CHAT", "timeout", "int", (5, 36000)),
    ("CHAT", "max_tokens", "int", (256, 400000)),
    ("CHAT", "context_length", "int", (0, 4000000)),
    ("CHAT", "history_transcript", "bool", None),
    ("CHAT", "session_inject_context", "bool", None),
    ("CHAT", "compact_threshold", "float", (0.1, 0.99)),
    ("CHAT", "compact_keep_recent", "int", (1, 100)),
    ("CHAT", "compact_timeout", "int", (0, 86400)),
    ("AGENTIC", "tool_result_max_chars", "int", (1000, 400000)),
    ("CHAT", "qa_review", "choice", ["auto", "always", "off"]),
    ("CHAT", "qa_max_rounds", "int", (1, 5)),
    ("CHAT", "qa_budget_chars", "int", (2000, 400000)),
]

PLANS_TAB = [
    ("CHAT", "plan_approval", "choice", ["always", "auto", "off"]),
    ("CHAT", "plan_auto_limit", "int", (0, 50)),
    ("CHAT", "plan_ack_timeout", "int", (30, 86400)),
    ("CHAT", "plan_max_expansions", "int", (0, 10)),
    ("CHAT", "plan_max_subtasks", "int", (1, 60)),
    ("CHAT", "subtask_timeout", "int", (0, 86400)),
    ("CHAT", "multi_max_parallel", "int", (1, 32)),
    ("CHAT", "multi_max_retries", "int", (0, 10)),
    ("CHAT", "escalate_on_stall", "bool", None),
    ("CHAT", "council_max_rounds", "int", (1, 10)),
    ("CHAT", "council_gate_timeout", "int", (30, 86400)),
]

WEB_TAB = [
    ("BROWSER", "headless", "bool", None),
    ("BROWSER", "use_vision", "str", None),
    ("BROWSER", "structured_output", "choice", ["auto", "force", "off"]),
    ("BROWSER", "flash_mode", "choice", ["auto", "true", "false"]),
    ("BROWSER", "max_steps_default", "int", (1, 200)),
    ("BROWSER", "use_thinking", "bool", None),
    ("BROWSER", "user_agent", "str", None),
    ("CRAWL4AI", "max_depth", "int", (0, 5)),
    ("CRAWL4AI", "max_pages", "int", (1, 500)),
    ("CRAWL4AI", "max_urls", "int", (0, 500)),
    ("CRAWL4AI", "timeout", "int", (5, 600)),
    ("RAG", "search_k", "int", (1, 50)),
    ("RAG", "min_similarity_score", "float", (0.0, 1.0)),
    ("RAG", "chunk_size", "int", (5, 2000)),
    ("RAG", "chunk_overlap", "int", (0, 500)),
    ("RAG", "max_chunk_chars", "int", (200, 32000)),
]

HANDS_TAB = [
    ("HANDS", "remote_model", "choice", ["ask", "allow", "deny"]),
    ("HANDS", "local_hosts", "str", None),
    ("HANDS", "verify", "bool", None),
    ("HANDS", "action_delay", "float", (0.0, 10.0)),
    ("HANDS", "screenshot_max_width", "int", (320, 4096)),
    ("HANDS", "consent_timeout", "int", (10, 3600)),
    ("HANDS", "op_timeout", "int", (5, 600)),
    ("HANDS", "pixel_backend", "choice", ["auto", "x11", "wayland"]),
]

MEMORY_TAB = [
    ("LESSONS", "enabled", "bool", None),
    ("LESSONS", "extract_on_feedback", "bool", None),
    ("LESSONS", "inject_on_chat", "bool", None),
    ("SKILLS", "ask_which", "bool", None),
]

APP_TAB = [
    ("APP", "language", "choice", ["en", "es"]),
    ("APP", "log_tools_to_notes", "bool", None),
    ("MCP", "url", "str", None),
    ("MCP", "enabled", "bool", None),
    ("MCP", "sync_on_boot", "bool", None),
    ("MCP", "sync_every_min", "int", (0, 1440)),
]

IMAGE_SPEC = [
    ("IMAGE", "base_url", "str", None),
    ("IMAGE", "model", "str", None),
    ("IMAGE", "api_key", "pwd", None),
    ("IMAGE", "size", "str", None),
    ("IMAGE", "timeout", "int", (30, 3600)),
]

SEARCH = [
    ("SEARCH", "provider", "choice",
     ["perplexity", "firecrawl", "exa", "tavily"]),
    ("PERPLEXITY", "api_key", "pwd", None),
    ("PERPLEXITY", "model", "str", None),
    ("PERPLEXITY", "url", "str", None),
    ("FIRECRAWL", "api_key", "pwd", None),
    ("FIRECRAWL", "url", "str", None),
    ("FIRECRAWL", "limit", "int", (1, 100)),
    ("FIRECRAWL", "max_chars", "int", (0, 200000)),
    ("EXA", "api_key", "pwd", None),
    ("EXA", "url", "str", None),
    ("EXA", "mode", "choice", ["search", "answer"]),
    ("EXA", "type", "choice", ["auto", "neural", "keyword", "fast"]),
    ("EXA", "limit", "int", (1, 100)),
    ("EXA", "max_chars", "int", (0, 200000)),
    ("TAVILY", "api_key", "pwd", None),
    ("TAVILY", "url", "str", None),
    ("TAVILY", "depth", "choice", ["basic", "advanced"]),
    ("TAVILY", "limit", "int", (1, 100)),
    ("TAVILY", "include_answer", "bool", None),
]

ALL_SPECS = CHAT_TAB + PLANS_TAB + WEB_TAB + SEARCH + HANDS_TAB + MEMORY_TAB + APP_TAB + IMAGE_SPEC

HIDDEN_KEYS = {("RAG", "base_url"), ("RAG", "api_key"), ("RAG", "model")}
HIDDEN_SECTIONS = ("API",)

TEMPLATE_PLACEHOLDER_MODELS = ("tu-modelo", "your-model")

ROLES = [
    ("cfg.role_browser", "BROWSER", "model", "turn"),
    ("cfg.role_browser_fallback", "BROWSER", "fallback_model", "none"),
    ("cfg.role_memory", "MEMORY", "model", "base"),
    ("cfg.role_planner", "CHAT", "planner_model", "turn"),
    ("cfg.role_qa", "CHAT", "qa_model", "turn"),
    ("cfg.role_escalate", "CHAT", "escalate_model", "auto"),
    ("cfg.role_vision", "CHAT", "vision_model", "seeing"),
]
ROLE_ENDPOINT_SECTIONS = ("BROWSER", "MEMORY")
ROLE_KEYS = ({(sec, opt) for _l, sec, opt, _d in ROLES}
             | {(sec, k) for sec in ROLE_ENDPOINT_SECTIONS for k in ("base_url", "api_key")}
             | {("CHAT", "auto_route_models"), ("CHAT", "vision")} | HIDDEN_KEYS)

OWN_CHAT = ("CHAT",)
OWN_WEB = ("BROWSER", "CRAWL4AI", "RAG")
OWN_SEARCH = ("SEARCH", "PERPLEXITY", "FIRECRAWL", "EXA", "TAVILY")
OWN_HANDS = ("HANDS",)
OWN_MEMORY = ("LESSONS", "SKILLS", "MEMORY")
OWN_APP = ("APP", "MCP")
OWN_ALL = (set(OWN_CHAT) | set(OWN_WEB) | set(OWN_SEARCH) | set(OWN_HANDS)
           | set(OWN_MEMORY) | set(OWN_APP) | {"IMAGE"} | set(HIDDEN_SECTIONS))


DEFAULTS = {
    ("SKILLS", "ask_which"): "true",
    ("IMAGE", "size"): "1024x1024",
    ("IMAGE", "timeout"): "300",
    ("HANDS", "remote_model"): "ask",
    ("HANDS", "verify"): "true",
    ("HANDS", "action_delay"): "0.4",
    ("HANDS", "screenshot_max_width"): "1280",
    ("HANDS", "consent_timeout"): "300",
    ("HANDS", "op_timeout"): "45",
    ("HANDS", "local_hosts"): "",
    ("HANDS", "pixel_backend"): "auto",
    ("CHAT", "auto_route_models"): "true",
    ("CHAT", "session_inject_context"): "true",
    ("CHAT", "vision"): "false",
    ("CHAT", "max_tokens"): "8000",
    ("CHAT", "client_tool_timeout"): "3600",
    ("CHAT", "max_threads"): "8",
    ("CHAT", "qa_review"): "off",
    ("CHAT", "history_transcript"): "true",
    ("CHAT", "plan_approval"): "always",
    ("CHAT", "plan_auto_limit"): "3",
    ("CHAT", "qa_max_rounds"): "2",
    ("CHAT", "qa_budget_chars"): "40000",
    ("AGENTIC", "tool_result_max_chars"): "8000",
    ("CHAT", "plan_ack_timeout"): "900",
    ("CHAT", "plan_max_expansions"): "2",
    ("CHAT", "plan_max_subtasks"): "12",
    ("CHAT", "agentic_max_iterations"): "8",
    ("CHAT", "compact_threshold"): "0.85",
    ("CHAT", "compact_keep_recent"): "6",
    ("CHAT", "compact_timeout"): "0",
    ("CHAT", "multi_max_parallel"): "4",
    ("CHAT", "multi_max_retries"): "2",
    ("CHAT", "council_max_rounds"): "4",
    ("CHAT", "council_gate_timeout"): "900",
    ("CHAT", "timeout"): "120",
    ("LESSONS", "enabled"): "true",
    ("LESSONS", "extract_on_feedback"): "true",
    ("LESSONS", "inject_on_chat"): "true",
    ("RAG", "search_k"): "5",
    ("RAG", "min_similarity_score"): "0.18",
    ("RAG", "chunk_size"): "200",
    ("RAG", "chunk_overlap"): "50",
    ("BROWSER", "headless"): "false",
    ("BROWSER", "flash_mode"): "auto",
    ("BROWSER", "structured_output"): "auto",
    ("BROWSER", "max_steps_default"): "30",
    ("BROWSER", "use_vision"): "auto",
    ("FIRECRAWL", "limit"): "5",
    ("FIRECRAWL", "max_chars"): "0",
    ("EXA", "url"): "https://api.exa.ai",
    ("EXA", "mode"): "search",
    ("EXA", "type"): "auto",
    ("EXA", "limit"): "5",
    ("EXA", "max_chars"): "0",
    ("TAVILY", "url"): "https://api.tavily.com",
    ("TAVILY", "depth"): "basic",
    ("TAVILY", "limit"): "5",
    ("TAVILY", "include_answer"): "true",
    ("CRAWL4AI", "max_depth"): "0",
    ("CRAWL4AI", "max_pages"): "10",
    ("CRAWL4AI", "max_urls"): "10",
    ("CRAWL4AI", "timeout"): "60",
}

def left_form(form):
    form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
    return form


class AutoSpinBox(QSpinBox):

    def __init__(self, auto_text="auto", parent=None):
        super().__init__(parent)
        self._auto_words = {"", "0", "auto", str(auto_text or "auto").strip().lower()}
        self.setSpecialValueText(str(auto_text or "auto"))
        self.setToolTip(f"0 = {auto_text}. Empty the field or type "
                        f"«{auto_text}» to go back to it.")

    def validate(self, text, pos):
        from PyQt6.QtGui import QValidator
        t = (text or "").strip().lower()
        if t in self._auto_words or any(w.startswith(t) for w in self._auto_words if w):
            return (QValidator.State.Acceptable if t in self._auto_words
                    else QValidator.State.Intermediate, text, pos)
        return super().validate(text, pos)

    def valueFromText(self, text):
        t = (text or "").strip().lower()
        if t in self._auto_words:
            return self.minimum()
        return super().valueFromText(text)

    def fixup(self, text):
        t = (text or "").strip().lower()
        if t in self._auto_words or any(w.startswith(t) for w in self._auto_words if w):
            return self.specialValueText()
        return super().fixup(text)


MODEL_FIELDS = [("model", "str"), ("base_url", "str"), ("api_key", "pwd"),
                ("context_length", "int"), ("profile", "str"),
                ("vision", "tri"), ("council", "bool"), ("extra_body", "str")]


def _keep_inline_comment(old_line, new_line):
    m = re.search(r"\s+([;#].*)$", old_line)
    return f"{new_line}   {m.group(1)}" if m else new_line


from hoplita_llmcfg import (set_value, del_key, del_section,
                            _scan, _is_header, repair_ini)


def ini_problem(text):
    import hoplita_llmcfg
    return hoplita_llmcfg.ini_problem(text)


_USAGE_COLORS = ("#38bdf8", "#34d399", "#fbbf24", "#a78bfa",
                 "#f87171", "#22d3ee", "#fb923c", "#e879f9")


def _fmt(n):
    return f"{int(n):,}".replace(",", ".")


def _usage_name(r):
    name = (r.get("name") or "").strip()
    sent = (r.get("sent") or "").strip()
    if not sent and "/" not in (r.get("model") or "") and "\\" not in (r.get("model") or ""):
        sent = (r.get("model") or "").strip()
    if name and sent and sent.lower() != name.lower():
        return f"{name} · {sent}"
    if name or sent:
        return name or sent
    m = (r.get("model") or "?").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"\.(gguf|bin|safetensors)$", "", m, flags=re.I) or "?"


def usage_html(d, t):
    rows = d.get("models") or []
    total = int(d.get("total") or 0)
    since = (d.get("since") or "")[:16].replace("T", " ")
    css = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
  html,body{margin:0;padding:14px;background:#0f172a;color:#e2e8f0;
    font-family:-apple-system,'Segoe UI',Roboto,sans-serif;}
  *{box-sizing:border-box;}
  .tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:12px;}
  .tile{background:#1e293b;border:1px solid #334155;border-radius:12px;
    padding:10px 14px;}
  .tile .v{font-size:24px;font-weight:700;color:#f1f5f9;}
  .tile .k{font-size:12px;color:#e6edf7;margin-top:2px;}
  .grid{display:grid;grid-template-columns:280px 1fr;gap:10px;}
  .card{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:14px;}
  .card h3{margin:0 0 10px;font-size:12px;color:#e6edf7;font-weight:600;
    text-transform:uppercase;letter-spacing:.5px;}
  .donutwrap{display:flex;align-items:center;gap:14px;flex-direction:column;}
  .donut{width:170px;height:170px;border-radius:50%;position:relative;}
  .donut::after{content:attr(data-total);position:absolute;inset:26px;
    background:#1e293b;border-radius:50%;display:flex;align-items:center;
    justify-content:center;font-size:17px;font-weight:700;color:#f1f5f9;}
  .leg{width:100%;font-size:12px;}
  .leg div{display:flex;align-items:center;gap:7px;margin:3px 0;}
  .dot{width:10px;height:10px;border-radius:3px;flex:none;}
  .leg .pc{margin-left:auto;color:#94a3b8;}
  .bar-row{display:grid;grid-template-columns:150px 1fr 90px;gap:8px;
    align-items:center;margin:7px 0;font-size:12px;}
  .bar-name{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#cbd5e1;}
  .bar{height:16px;border-radius:5px;background:#0f172a;overflow:hidden;
    display:flex;}
  .bar .in{height:100%;}
  .bar .out{height:100%;opacity:.45;}
  .bar-tot{text-align:right;color:#94a3b8;font-variant-numeric:tabular-nums;}
  table{width:100%;border-collapse:collapse;margin-top:2px;font-size:12px;}
  th,td{padding:6px 10px;text-align:right;border-bottom:1px solid #334155;
    font-variant-numeric:tabular-nums;}
  th:first-child,td:first-child{text-align:left;}
  th{color:#94a3b8;font-weight:600;}
  .tl-head{display:flex;align-items:center;gap:6px;margin-bottom:10px;}
  .tl-head h3{margin:0;flex:1;}
  .tl-btn{background:#0f172a;color:#94a3b8;border:1px solid #334155;
    border-radius:7px;padding:3px 12px;font-size:12px;cursor:pointer;}
  .tl-btn.on{background:#1d3a5f;color:#e2e8f0;border-color:#3b82f6;}
  .tl{display:flex;align-items:flex-end;gap:4px;height:170px;
    padding-top:16px;}
  .tl .col{flex:1;display:flex;flex-direction:column-reverse;min-width:6px;
    height:100%;justify-content:flex-start;position:relative;}
  .tl .seg{width:100%;}
  .tl .col:hover{filter:brightness(1.25);}
  .tl-x{display:flex;gap:4px;font-size:12px;color:#e6edf7;margin-top:4px;}
  .tl-x div{flex:1;text-align:center;overflow:hidden;white-space:nowrap;}
  .tl-top{position:absolute;top:-15px;width:100%;text-align:center;
    font-size:12px;color:#e6edf7;}
  .empty{display:flex;flex-direction:column;align-items:center;
    justify-content:center;height:60vh;color:#64748b;text-align:center;}
  .empty .big{font-size:44px;margin-bottom:10px;}
  .note{font-size:12px;color:#e6edf7;margin-top:10px;}
  .inkey{display:inline-block;width:9px;height:9px;border-radius:2px;
    background:#38bdf8;margin-right:4px;}
  .outkey{display:inline-block;width:9px;height:9px;border-radius:2px;
    background:#38bdf8;opacity:.45;margin-right:4px;}
</style></head><body>"""
    if not rows:
        return (css + f"""<div class="empty"><div class="big">📊</div>
<h3>{t('cfg.usage_empty')}</h3></div></body></html>""")

    calls = sum(int(r.get("calls") or 0) for r in rows)
    tiles = f"""<div class="tiles">
  <div class="tile"><div class="v">{_fmt(total)}</div><div class="k">{t('cfg.usage_total')} (tokens)</div></div>
  <div class="tile"><div class="v">{_fmt(calls)}</div><div class="k">{t('cfg.usage_calls')}</div></div>
  <div class="tile"><div class="v">{len(rows)}</div><div class="k">{t('cfg.usage_models')}</div></div>
  <div class="tile"><div class="v" style="font-size:15px;">{since or "—"}</div><div class="k">{t('cfg.usage_since_k')}</div></div>
</div>"""

    stops, legend, acc = [], [], 0.0
    for i, r in enumerate(rows):
        c = _USAGE_COLORS[i % len(_USAGE_COLORS)]
        share = (100.0 * int(r.get("total") or 0) / total) if total else 0.0
        stops.append(f"{c} {acc:.2f}% {acc + share:.2f}%")
        acc += share
        legend.append(
            f'<div><span class="dot" style="background:{c}"></span>'
            f'<span title="{_h.escape(r.get("model") or "")}">{_h.escape(_usage_name(r))}</span>'
            f'<span class="pc">{share:.0f}% · {_fmt(r.get("total") or 0)}</span></div>')
    _short = _fmt(total) if total < 1_000_000 else f"{total/1e6:.2f}M"
    donut = f"""<div class="card"><h3>{t('cfg.usage_share')}</h3>
<div class="donutwrap">
  <div class="donut" data-total="{_short}"
       style="background:conic-gradient({', '.join(stops)});"></div>
  <div class="leg">{''.join(legend)}</div>
</div></div>"""

    top = max(int(r.get("total") or 1) for r in rows)
    bars = []
    for i, r in enumerate(rows):
        c = _USAGE_COLORS[i % len(_USAGE_COLORS)]
        w_in = 100.0 * int(r.get("prompt") or 0) / top
        w_out = 100.0 * int(r.get("completion") or 0) / top
        bars.append(
            f'<div class="bar-row"><div class="bar-name" title="{_h.escape(r.get("model") or "")}">{_h.escape(_usage_name(r))}</div>'
            f'<div class="bar"><div class="in" style="width:{w_in:.1f}%;background:{c}"></div>'
            f'<div class="out" style="width:{w_out:.1f}%;background:{c}"></div></div>'
            f'<div class="bar-tot">{_fmt(r.get("total") or 0)}</div></div>')
    barcard = f"""<div class="card"><h3>{t('cfg.usage_inout')}</h3>
{''.join(bars)}
<div class="note"><span class="inkey"></span>{t('cfg.usage_in')} &nbsp;
<span class="outkey"></span>{t('cfg.usage_out')}</div></div>"""

    days = d.get("days") or []
    model_color = {r.get("model"): _USAGE_COLORS[i % len(_USAGE_COLORS)]
                   for i, r in enumerate(rows)}
    timeline = ""
    if days:
        payload = json.dumps({"days": days, "colors": model_color,
                              "names": {r.get("model"): _usage_name(r) for r in rows}},
                             ensure_ascii=False)
        timeline = f"""<div class="card" style="margin-top:10px;">
<div class="tl-head"><h3>{t('cfg.usage_timeline')}</h3>
  <button class="tl-btn on" data-g="day">{t('cfg.usage_by_day')}</button>
  <button class="tl-btn" data-g="week">{t('cfg.usage_by_week')}</button>
  <button class="tl-btn" data-g="month">{t('cfg.usage_by_month')}</button>
</div>
<div class="tl" id="tl"></div><div class="tl-x" id="tlx"></div>
<script>
const DATA = {payload};
const NSHOW = {{day: 31, week: 26, month: 12}};
function keyOf(day, g) {{
  if (g === "day") return day;
  if (g === "month") return day.slice(0, 7);
  const dt = new Date(day + "T00:00:00");
  dt.setDate(dt.getDate() - ((dt.getDay() + 6) % 7));
  return dt.toISOString().slice(0, 10);
}}
function label(k, g) {{
  if (g === "month") return k;
  return k.slice(5);
}}
function fmt(n) {{
  return n.toLocaleString("de-DE");
}}
function draw(g) {{
  const buckets = {{}};
  for (const r of DATA.days) {{
    const k = keyOf(r.day, g);
    (buckets[k] = buckets[k] || {{}})[r.model] =
        (buckets[k][r.model] || 0) + r.total;
  }}
  const keys = Object.keys(buckets).sort().slice(-NSHOW[g]);
  const top = Math.max(1, ...keys.map(
      k => Object.values(buckets[k]).reduce((a, b) => a + b, 0)));
  const tl = document.getElementById("tl"), tlx = document.getElementById("tlx");
  tl.innerHTML = ""; tlx.innerHTML = "";
  for (const k of keys) {{
    const col = document.createElement("div"); col.className = "col";
    let tot = 0;
    for (const [m, v] of Object.entries(buckets[k])) {{
      const seg = document.createElement("div"); seg.className = "seg";
      seg.style.height = (100 * v / top) + "%";
      seg.style.background = DATA.colors[m] || "#64748b";
      seg.title = (DATA.names[m] || m) + ": " + fmt(v);
      col.appendChild(seg); tot += v;
    }}
    col.title = k + " — " + fmt(tot);
    if (keys.length <= 16) {{
      const cap = document.createElement("div"); cap.className = "tl-top";
      cap.textContent = tot >= 1e6 ? (tot / 1e6).toFixed(1) + "M"
                       : tot >= 1e3 ? Math.round(tot / 1e3) + "k" : tot;
      col.appendChild(cap);
    }}
    tl.appendChild(col);
    const x = document.createElement("div");
    x.textContent = keys.length <= 20 ? label(k, g) : "";
    tlx.appendChild(x);
  }}
}}
for (const b of document.querySelectorAll(".tl-btn")) {{
  b.onclick = () => {{
    document.querySelectorAll(".tl-btn").forEach(x => x.classList.remove("on"));
    b.classList.add("on"); draw(b.dataset.g);
  }};
}}
draw("day");
</script></div>"""

    trs = "".join(
        f"<tr><td title=\"{_h.escape(r.get('model') or '')}\">{_h.escape(_usage_name(r))}</td><td>{_fmt(r.get('calls') or 0)}</td>"
        f"<td>{_fmt(r.get('prompt') or 0)}</td><td>{_fmt(r.get('completion') or 0)}</td>"
        f"<td>{_fmt(r.get('total') or 0)}</td></tr>" for r in rows)
    table = f"""<div class="card" style="margin-top:10px;"><h3>{t('cfg.usage_exact')}</h3>
<table><tr><th>{t('cfg.usage_model')}</th><th>{t('cfg.usage_calls')}</th>
<th>{t('cfg.usage_in')}</th><th>{t('cfg.usage_out')}</th><th>{t('cfg.usage_total')}</th></tr>
{trs}</table>
<div class="note">{t('cfg.usage_since', when=since) if since else t('cfg.usage_never')}</div></div>"""

    return (css + tiles + f'<div class="grid">{donut}{barcard}</div>'
            + timeline + table + "</body></html>")


class UsageWidget(QWidget):

    def __init__(self, t, mcp_url, parent=None):
        super().__init__(parent)
        self._t = t
        self._mcp = (mcp_url or "").rstrip("/")
        self._view = None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            self._view = QWebEngineView()
            self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            v.addWidget(self._view, 1)
        except Exception:
            self._box = QPlainTextEdit()
            self._box.setReadOnly(True)
            self._box.setFont(QFont("Monospace", 11))
            v.addWidget(self._box, 1)
        row = QHBoxLayout()
        row.setContentsMargins(8, 4, 8, 6)
        b = QPushButton(t("btn.reload"))
        b.clicked.connect(self.load)
        row.addWidget(b)
        rst = QPushButton(t("cfg.usage_reset"))
        rst.setStyleSheet("QPushButton { background:#3f2d1d; color:#fed7aa; "
                          "border:1px solid #b45309; border-radius:8px; "
                          "padding:4px 12px; }")
        rst.clicked.connect(self._reset)
        row.addWidget(rst)
        row.addStretch(1)
        v.addLayout(row)
        self.load()

    def _show(self, data=None, error=""):
        t = self._t
        if self._view is not None:
            if error:
                self._view.setHtml(usage_html({"models": []}, t)
                                   .replace("📊", "❌")
                                   .replace(t("cfg.usage_empty"), error))
            else:
                self._view.setHtml(usage_html(data or {}, t))
            return
        if error:
            self._box.setPlainText(error)
            return
        rows = (data or {}).get("models") or []
        if not rows:
            self._box.setPlainText(t("cfg.usage_empty"))
            return
        head = f"{t('cfg.usage_model'):36} {t('cfg.usage_calls'):>8} " \
               f"{t('cfg.usage_in'):>12} {t('cfg.usage_out'):>12} " \
               f"{t('cfg.usage_total'):>12}"
        lines = [head, "─" * len(head)]
        for r in rows:
            lines.append(f"{_usage_name(r)[:36]:36} "
                         f"{_fmt(r.get('calls') or 0):>8} {_fmt(r.get('prompt') or 0):>12} "
                         f"{_fmt(r.get('completion') or 0):>12} {_fmt(r.get('total') or 0):>12}")
        lines.append(f"{t('cfg.usage_sum'):36} {'':>8} {'':>12} {'':>12} "
                     f"{_fmt((data or {}).get('total') or 0):>12}")
        self._box.setPlainText("\n".join(lines))

    def load(self):
        t = self._t
        if not self._mcp:
            self._show(error=t("tools.mcp_unavailable"))
            return
        try:
            import requests as _rq
            d = _rq.get(f"{self._mcp}/stats/tokens", timeout=5).json()
        except Exception as e:
            self._show(error=t("tools.mcp_unavailable") + f" — {e}")
            return
        self._show(data=d)

    def _reset(self):
        t = self._t
        from PyQt6.QtWidgets import QMessageBox
        if QMessageBox.question(
                self, t("cfg.tab_usage"), t("cfg.usage_reset_confirm"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        if self._mcp:
            try:
                import requests as _rq
                _rq.post(f"{self._mcp}/stats/tokens/reset", timeout=5)
            except Exception:
                pass
        self.load()


class ConfigEditorDialog(QDialog):

    def __init__(self, parent, config_path, t, on_saved):
        super().__init__(parent)
        self._t = t
        self._on_saved = on_saved
        self._path = config_path
        self._widgets = {}
        self._model_rows = []
        self._removed_models = []
        self._base_alias = ""
        self._kv_sections = {}
        self._initial = {}

        with open(config_path, "r", encoding="utf-8") as f:
            self._raw = f.read()
        self._repaired, self._repair_notes = False, []
        _fixed, _notes = repair_ini(self._raw)
        if _notes:
            self._raw, self._repaired, self._repair_notes = _fixed, True, _notes
        self._cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"),
                                             interpolation=None)
        self._parse_ok, self._parse_err = True, ""
        try:
            self._cp.read_string(self._raw)
        except Exception as e:
            self._parse_ok, self._parse_err = False, str(e)

        self.setWindowTitle(t("dlg.edit_config"))
        self.resize(1060, 760)
        self.setModal(True)
        self.setStyleSheet("""
            QDialog { background:#0b1220; }
            QLabel { color:#e6edf7; font-size:12px; }
            QLabel#cfgsection { color:#38bdf8; font-weight:800; font-size:12px;
                                letter-spacing:1px; }
            QLabel#cfgkey { color:#e6edf7; font-family:monospace; font-size:12px; }
            QWidget#cfgcard { background:#0f172a; border:1px solid #1e293b;
                              border-radius:10px; }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {
                background:#131c2e; color:#e6edf7; border:1px solid #2a3547;
                border-radius:7px; padding:6px 9px; selection-background-color:#1d4ed8; }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
                border-color:#38bdf8; }
            QCheckBox { color:#e6edf7; }
            QPushButton { background:#1e293b; color:#e6edf7; border:1px solid #334155;
                          border-radius:8px; padding:7px 16px; font-weight:600; }
            QPushButton:hover { background:#334155; }
            QPushButton:default { background:#2563eb; border-color:#2563eb; }
            QPushButton:default:hover { background:#1d4ed8; }
            QTabWidget::pane { border:1px solid #1e293b; border-radius:8px; top:-1px; }
            QTabBar::tab { background:#0f172a; color:#94a3b8; border:1px solid #1e293b;
                           padding:7px 14px; margin-right:2px;
                           border-top-left-radius:8px; border-top-right-radius:8px; }
            QTabBar::tab:selected { background:#131c2e; color:#e6edf7;
                                    border-bottom:2px solid #38bdf8; }
            QScrollArea { border:none; background:transparent; }
            QWidget#cfgpage { background:#0b1220; }
        """)
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)

        head = QLabel(f"📁 <b>{config_path}</b><br><span style='color:#94a3b8'>"
                      f"{t('cfg.header_note')}</span>")
        head.setTextFormat(Qt.TextFormat.RichText)
        head.setWordWrap(True)
        v.addWidget(head)
        if self._repaired:
            _rn = QLabel("⚠ " + t("cfg.repaired", what="; ".join(self._repair_notes)[:300]))
            _rn.setWordWrap(True)
            _rn.setStyleSheet("color:#fbbf24; font-weight:600;")
            v.addWidget(_rn)

        self.tabs = QTabWidget()
        if not self._parse_ok:
            warn = QLabel("⚠ " + t("cfg.parse_error", err=self._parse_err))
            warn.setWordWrap(True)
            warn.setStyleSheet("color:#f87171; font-weight:600;")
            v.addWidget(warn)
            self.tabs.addTab(self._raw_tab(), t("cfg.tab_raw"))
            v.addWidget(self.tabs, 1)
            self.status = QLabel("")
            v.addWidget(self.status)
            row = QHBoxLayout(); row.addStretch(1)
            c = QPushButton(t("btn.cancel")); c.clicked.connect(self.reject)
            s = QPushButton(t("ui.save_reload")); s.clicked.connect(self._save)
            row.addWidget(c); row.addWidget(s); v.addLayout(row)
            QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._save)
            return
        self.tabs.addTab(self._models_tab(), t("cfg.tab_models"))
        self.tabs.addTab(self._form_tab(CHAT_TAB, own_sections=OWN_CHAT), t("cfg.tab_chat"))
        self.tabs.addTab(self._form_tab(PLANS_TAB), t("cfg.tab_plans"))
        self.tabs.addTab(self._form_tab(WEB_TAB, own_sections=OWN_WEB), t("cfg.tab_web"))
        self.tabs.addTab(self._form_tab(SEARCH, own_sections=OWN_SEARCH),
                         t("cfg.tab_search"))
        self.tabs.addTab(self._form_tab(HANDS_TAB, own_sections=OWN_HANDS), t("cfg.tab_hands"))
        self.tabs.addTab(self._form_tab(MEMORY_TAB, own_sections=OWN_MEMORY), t("cfg.tab_memory"))
        self.tabs.addTab(self._form_tab(APP_TAB, own_sections=OWN_APP), t("cfg.tab_app"))
        self.tabs.addTab(self._kv_tab("MCP_SERVERS", "cfg.mcpservers_note"),
                         t("cfg.tab_mcpservers"))
        _others, _n_others = self._others_tab()
        if _n_others:
            self.tabs.addTab(_others, t("cfg.tab_others"))
        self.tabs.addTab(self._raw_tab(), t("cfg.tab_raw"))
        v.addWidget(self.tabs, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#e6edf7; font-size:12px;")
        v.addWidget(self.status)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton(t("btn.cancel"))
        cancel.clicked.connect(self.reject)
        save = QPushButton(t("ui.save_reload"))
        save.setDefault(True)
        save.clicked.connect(self._save)
        row.addWidget(cancel)
        row.addWidget(save)
        v.addLayout(row)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._save)

    def _scroll(self, inner):
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        inner.setObjectName("cfgpage")
        sc.setWidget(inner)
        return sc

    def _make_widget(self, section, key, kind, extra):
        present = (self._cp.has_section(section)
                   and self._cp.has_option(section, key))
        cur = (self._cp.get(section, key, fallback="") if present
               else DEFAULTS.get((section, key), ""))
        if kind == "bool":
            w = QCheckBox()
            w.setChecked(str(cur).strip().lower() in ("1", "true", "yes", "on"))
        elif kind == "int":
            lo, hi = extra or (0, 1000000)
            w = AutoSpinBox(self._t("cfg.auto")) if lo == 0 else QSpinBox()
            w.setRange(lo, hi)
            try:
                w.setValue(int(float(str(cur).strip() or 0)))
            except Exception:
                pass
        elif kind == "float":
            w = QDoubleSpinBox()
            lo, hi = extra or (0.0, 1.0)
            w.setRange(lo, hi)
            w.setSingleStep(0.05)
            w.setDecimals(2)
            try:
                w.setValue(float(str(cur).strip() or lo))
            except Exception:
                pass
        elif kind == "choice":
            w = QComboBox()
            w.addItems(extra or [])
            w.setEditable(True)
            w.setCurrentText(str(cur).strip())
        elif kind == "pwd":
            w = QLineEdit(str(cur).strip())
            w.setEchoMode(QLineEdit.EchoMode.Password)
        else:
            w = QLineEdit(str(cur).strip())
        help_txt = self._t(f"cfg.h.{section}.{key}")
        if help_txt and not help_txt.startswith("cfg.h."):
            w.setToolTip(help_txt)
        return w

    def _section_card(self, section):
        card = QWidget()
        card.setObjectName("cfgcard")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(14, 10, 14, 12)
        cv.setSpacing(6)
        title = QLabel(f"[{section}]")
        title.setObjectName("cfgsection")
        cv.addWidget(title)
        form = left_form(QFormLayout())
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight |
                               Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(9)
        cv.addLayout(form)
        return card, form

    def _add_row(self, form, section, key, kind, extra, label=None):
        w = self._make_widget(section, key, kind, extra)
        self._widgets[(section, key)] = (w, kind)
        present = (self._cp.has_section(section)
                   and self._cp.has_option(section, key))
        self._initial[(section, key)] = (present, self._value_of(w, kind))
        if isinstance(w, (QLineEdit, QComboBox)):
            w.setMinimumWidth(340)
        lbl = QLabel(label or key)
        lbl.setObjectName("cfgkey" if not label else "cfglabel")
        lbl.setMinimumWidth(190)
        present = (self._cp.has_section(section)
                   and self._cp.has_option(section, key))
        help_txt = self._t(f"cfg.h.{section}.{key}")
        tip = help_txt if help_txt and not help_txt.startswith("cfg.h.") else ""
        if not present:
            tip = ((tip + "\n\n") if tip else "") + self._t("cfg.is_default")
            lbl.setText(f"{label or key}  ·")
        elif tip:
            lbl.setText(f"{label or key}  ⓘ")
        if tip:
            lbl.setToolTip(tip)
            w.setToolTip(tip)
        form.addRow(lbl, w)

    def _form_tab(self, spec, endpoint=None, own_sections=()):
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setSpacing(12)
        curated = {(s, k) for s, k, _, _ in ALL_SPECS} | ROLE_KEYS
        order, by_sec = [], {}
        for section, key, kind, extra in spec:
            if section not in by_sec:
                by_sec[section] = []
                order.append(section)
            by_sec[section].append((key, kind, extra))
        for section in list(own_sections):
            if section not in by_sec and self._cp.has_section(section):
                by_sec[section] = []
                order.append(section)
        for section in order:
            card, form = self._section_card(section)
            for key, kind, extra in by_sec[section]:
                self._add_row(form, section, key, kind, extra)
            if section in own_sections and self._cp.has_section(section):
                for k in self._cp.options(section):
                    if (section, k) in curated:
                        continue
                    cur = str(self._cp.get(section, k, fallback="")).strip()
                    kind = ("bool" if cur.lower() in ("true", "false") else
                            "int" if re.fullmatch(r"\d+", cur) else "str")
                    self._add_row(form, section, k, kind,
                                  (0, 4000000) if kind == "int" else None)
            lay.addWidget(card)
        if endpoint:
            sec, ku, kk, km = endpoint
            btn = QPushButton("🔌 " + self._t("cfg.test_conn"))
            btn.clicked.connect(lambda _=False, s=sec, a=ku, b=kk, c=km:
                                self._test_conn(s, a, b, c))
            lay.addWidget(btn)
        lay.addStretch(1)
        return self._scroll(inner)

    def _card(self, title_key, note_key=None):
        card = QWidget()
        card.setObjectName("cfgcard")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(14, 10, 14, 12)
        cv.setSpacing(8)
        _title = QLabel(self._t(title_key))
        _title.setObjectName("cfgsection")
        cv.addWidget(_title)
        if note_key:
            _n = QLabel(self._t(note_key))
            _n.setWordWrap(True)
            _n.setStyleSheet("color:#e6edf7; font-size:12px;")
            cv.addWidget(_n)
        return card, cv

    def _models_tab(self):
        self._base_alias = ((self._cp.get("MODELS", "base", fallback="") or "").strip()
                            if self._cp.has_section("MODELS") else "")
        inner = QWidget()
        self._pool_layout = QVBoxLayout(inner)
        self._pool_layout.setSpacing(12)
        mcard, mv = self._card("cfg.models_title", "cfg.models_note")
        self._pool_notes = mv
        self._pool_box = QVBoxLayout()
        self._pool_box.setSpacing(6)
        mv.addLayout(self._pool_box)
        add = QPushButton("＋ " + self._t("cfg.add_model"))
        add.clicked.connect(lambda: (self._add_model_row("", True, {}, expanded=True),
                                     self._refresh_base_combo()))
        self._pool_add_btn = add
        _ar = QHBoxLayout(); _ar.addWidget(add); _ar.addStretch(1)
        mv.addLayout(_ar)
        _vf = left_form(QFormLayout())
        _vf.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._add_row(_vf, "CHAT", "vision", "bool", None, label=self._t("cfg.vision_default"))
        mv.addLayout(_vf)
        self._pool_layout.addWidget(mcard)
        card, cv = self._card("cfg.roles_title", "cfg.roles_note")
        form = left_form(QFormLayout())
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(9)
        cv.addLayout(form)
        _blbl = QLabel(self._t("cfg.base_model_label"))
        _blbl.setStyleSheet("font-weight:700; color:#e6edf7;")
        self._base_combo = QComboBox()
        self._base_combo.setMinimumWidth(320)
        self._base_combo.setToolTip(self._t("cfg.model_is_base_hint"))
        self._base_combo.currentIndexChanged.connect(lambda _i: self._refresh_role_combos())
        form.addRow(_blbl, self._base_combo)
        _rv = (self._cp.get("CHAT", "auto_route_models", fallback="true")
               if self._cp.has_section("CHAT") else "true")
        self._router_chk = QCheckBox(self._t("cfg.role_router_on"))
        self._router_chk.setChecked(str(_rv).strip().lower() in ("1", "true", "yes", "on"))
        self._router_initial = self._router_chk.isChecked()
        self._router_chk.setToolTip(self._t("cfg.h.CHAT.auto_route_models"))
        form.addRow(QLabel(self._t("cfg.role_router")), self._router_chk)
        self._role_combos = {}
        for _key, _sec, _opt, _dflt in ROLES:
            _c = QComboBox()
            _c.setMinimumWidth(320)
            _tip = self._t(f"cfg.h.{_sec}.{_opt}")
            _l = QLabel(self._t(_key))
            if _tip and not _tip.startswith("cfg.h."):
                _c.setToolTip(_tip)
                _l.setToolTip(_tip)
            self._role_combos[(_sec, _opt)] = (_c, _dflt)
            form.addRow(_l, _c)
        _hands = QLabel(self._t("cfg.role_hands_value"))
        _hands.setWordWrap(True)
        _hands.setStyleSheet("color:#94a3b8;")
        form.addRow(QLabel(self._t("cfg.role_hands")), _hands)
        _imgs = QLabel(self._t("cfg.role_images_value"))
        _imgs.setWordWrap(True)
        _imgs.setStyleSheet("color:#94a3b8;")
        form.addRow(QLabel(self._t("cfg.role_images")), _imgs)
        self._pool_layout.addWidget(card)
        icard, iv = self._card("cfg.images_title", "cfg.images_note")
        iform = left_form(QFormLayout())
        iform.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        iform.setHorizontalSpacing(18)
        iform.setVerticalSpacing(9)
        iv.addLayout(iform)
        for _s, _k, _kind, _x in IMAGE_SPEC:
            self._add_row(iform, _s, _k, _kind, _x, label=self._t(f"cfg.img.{_k}"))
        self._pool_layout.addWidget(icard)
        self._role_initial = {}
        self._pool_layout.addStretch(1)
        if self._cp.has_section("MODELS"):
            for name, val in self._cp.items("MODELS"):
                if name.strip().lower() == "base":
                    continue
                sec = self._model_section(name)
                data = dict(self._cp.items(sec)) if sec else {}
                self._add_model_row(name, str(val).strip().lower() in
                                    ("1", "true", "yes", "on"), data,
                                    section=sec)
        self._fill_inherited()
        self._seed_pool_from_api()
        self._seed_roles()
        self._refresh_base_combo()
        return self._scroll(inner)

    def _fill_inherited(self):
        import hoplita_llmcfg
        lit = (self._role_value("API", "base_url"), self._role_value("API", "api_key"))
        try:
            b_url, b_key, _m = hoplita_llmcfg.resolve_endpoint(self._cp, "API")
        except Exception:
            b_url, b_key = lit
        b_url = b_url if self._role_value("API", "base_url") or self._base_alias else ""
        for row in self._model_rows:
            is_base = row["name"].text().strip().lower() == (self._base_alias or "").lower()
            url, key = (lit if is_base else (b_url, b_key))
            f = row["fields"]
            if not f["base_url"][0].text().strip() and url:
                f["base_url"][0].setText(url)
            if not f["api_key"][0].text().strip() and key:
                f["api_key"][0].setText(key)
            _eb = self._role_value("API", "extra_body")
            if _eb and not f["extra_body"][0].text().strip() \
                    and f["model"][0].text().strip() == self._role_value("API", "model") \
                    and f["base_url"][0].text().strip().rstrip("/") == lit[0].rstrip("/"):
                f["extra_body"][0].setText(_eb)

    def _role_value(self, sec, opt):
        return ((self._cp.get(sec, opt, fallback="") or "").strip()
                if self._cp.has_section(sec) else "")

    def _row_alias_for(self, model, url):
        for row in self._model_rows:
            r_url = row["fields"]["base_url"][0].text().strip()
            r_model = row["fields"]["model"][0].text().strip()
            if r_model == model and (not r_url or not url
                                     or r_url.rstrip("/") == url.rstrip("/")):
                return row["name"].text().strip()
        return None

    def _seed_roles(self):
        aliases = {r["name"].text().strip().lower(): r["name"].text().strip()
                   for r in self._model_rows if r["name"].text().strip()}
        for _key, sec, opt, _dflt in ROLES:
            val = self._role_value(sec, opt)
            state = val
            if sec in ROLE_ENDPOINT_SECTIONS:
                url = self._role_value(sec, "base_url")
                key = self._role_value(sec, "api_key")
                if val and val.lower() in aliases:
                    state = aliases[val.lower()]
                elif val:
                    import hoplita_llmcfg
                    _bu, _bk, _bm = hoplita_llmcfg.resolve_endpoint(self._cp, "API")
                    _api_url = _bu
                    alias = self._row_alias_for(val, url or _api_url)
                    if not alias:
                        alias = self._new_alias(val)
                        data = {"model": val, "base_url": url or _api_url,
                                "api_key": key or _bk}
                        self._add_model_row(alias, False, data)
                    state = "__seeded__:" + alias
                elif url or key:
                    state = "__keep__"
            elif val.lower() == "none":
                state = "none"
            elif val and val.lower() != "auto" and val.lower() in aliases:
                state = aliases[val.lower()]
            elif val and val.lower() != "auto":
                state = "__missing__:" + val
            self._role_initial[(sec, opt)] = state

    def _new_alias(self, model):
        _base_id = model.rsplit("/", 1)[-1].rsplit(".gguf", 1)[0]
        _fam = re.match(r"[A-Za-z]+", _base_id)
        taken = {r["name"].text().strip().lower() for r in self._model_rows}
        alias = (_fam.group(0).lower() if _fam and _fam.group(0).lower() not in taken
                 else re.sub(r"[^A-Za-z0-9._-]+", "-", _base_id).strip("-")) or "model"
        base_alias, n = alias, 2
        while alias.lower() in taken or alias.lower() == "base":
            alias = f"{base_alias}-{n}"; n += 1
        return alias

    def _refresh_role_combos(self):
        combos = getattr(self, "_role_combos", None)
        if not combos:
            return
        base = (self._base_combo.currentData() or "").strip() if self._base_combo.count() else ""
        self._refresh_badges()
        base_txt = base or self._t("cfg.role_api_fallback")
        rows = [r["name"].text().strip() for r in self._model_rows if r["name"].text().strip()]
        for (sec, opt), (c, dflt) in combos.items():
            init = self._role_initial.get((sec, opt), "")
            prev = c.currentData() if c.count() else (
                init.split(":", 1)[1] if init.startswith("__seeded__:") else init)
            c.blockSignals(True)
            c.clear()
            if dflt == "auto":
                c.addItem(self._t("cfg.role_auto"), "")
            elif dflt == "none":
                c.addItem(self._t("cfg.role_none"), "")
            elif dflt == "seeing":
                c.addItem(self._t("cfg.role_vision_auto"), "")
            elif dflt == "turn":
                c.addItem(self._t("cfg.role_follow_turn", base=base_txt), "")
            else:
                c.addItem(self._t("cfg.role_follow_base", base=base_txt), "")
            for a in rows:
                c.addItem(a, a)
            if dflt == "seeing":
                c.addItem(self._t("cfg.role_vision_none"), "none")
            if init == "__keep__":
                c.addItem(self._t("cfg.role_custom"), "__keep__")
            elif init.startswith("__missing__:"):
                c.addItem(self._t("cfg.role_missing", v=init.split(":", 1)[1]), init)
            i = c.findData(prev)
            c.setCurrentIndex(i if i >= 0 else 0)
            c.blockSignals(False)

    def _save_roles(self, base):
        _rc = getattr(self, "_router_chk", None)
        if _rc is not None and _rc.isChecked() != self._router_initial:
            base = set_value(base, "CHAT", "auto_route_models",
                             "true" if _rc.isChecked() else "false")
        for (sec, opt), (c, _dflt) in (getattr(self, "_role_combos", None) or {}).items():
            choice = c.currentData() or ""
            init = self._role_initial.get((sec, opt), "")
            if choice == init and not init.startswith("__seeded__:"):
                continue
            if choice in ("__keep__",) or choice.startswith("__missing__:"):
                continue
            if choice:
                base = set_value(base, sec, opt, choice)
            else:
                base = del_key(base, sec, opt)
            if sec in ROLE_ENDPOINT_SECTIONS:
                base = del_key(base, sec, "base_url")
                base = del_key(base, sec, "api_key")
        return base

    def _seed_pool_from_api(self):
        if not self._cp.has_section("API"):
            return
        url = (self._cp.get("API", "base_url", fallback="") or "").strip()
        model = (self._cp.get("API", "model", fallback="") or "").strip()
        if not url or not model:
            return
        if model.lower() in TEMPLATE_PLACEHOLDER_MODELS:
            return
        for row in self._model_rows:
            r_url = row["fields"]["base_url"][0].text().strip()
            r_model = row["fields"]["model"][0].text().strip()
            if r_model == model and (not r_url or r_url.rstrip("/") == url.rstrip("/")):
                return
        alias = self._new_alias(model)
        data = {"model": model, "base_url": url,
                "api_key": (self._cp.get("API", "api_key", fallback="") or "").strip()}
        for k in ("context_length", "extra_body"):
            v = (self._cp.get("API", k, fallback="") or "").strip()
            if v:
                data[k] = v
        _had_base = bool(self._base_alias)
        self._add_model_row(alias, not _had_base, data)
        if not _had_base:
            self._base_alias = alias
        self._seeded_alias = alias
        note = QLabel(self._t("cfg.pool_seeded_other", alias=alias, base=self._base_alias)
                      if _had_base else self._t("cfg.pool_seeded", alias=alias))
        note.setWordWrap(True)
        note.setStyleSheet("color:#fbbf24; font-size:12px;")
        self._pool_notes.insertWidget(2, note)

    def _model_section(self, name):
        if not name:
            return None
        want = f"MODEL:{name}"
        if self._cp.has_section(want):
            return want
        low = want.lower()
        for sec in self._cp.sections():
            if sec.lower() == low:
                return sec
        return None

    def _refresh_base_combo(self):
        combo = getattr(self, "_base_combo", None)
        if combo is None:
            return
        prev = (combo.currentData() or "") if combo.count() else self._base_alias
        combo.blockSignals(True)
        combo.clear()
        active = [r["name"].text().strip() for r in self._model_rows
                  if r["name"].text().strip() and r["enabled"].isChecked()]
        if not active:
            combo.addItem(self._t("cfg.base_model_none"), "")
        for alias in active:
            combo.addItem(alias, alias)
        i = combo.findData(prev)
        combo.setCurrentIndex(i if i >= 0 else 0)
        combo.blockSignals(False)
        self._refresh_role_combos()

    def _refresh_badges(self):
        base = (self._base_combo.currentData() or "") if getattr(self, "_base_combo", None) else ""
        for row in self._model_rows:
            b = row.get("badge")
            if b is not None:
                b.setVisible(bool(base) and row["name"].text().strip() == base)

    @staticmethod
    def _host_of(url):
        from urllib.parse import urlparse
        u = (url or "").strip()
        if not u:
            return "?"
        p = urlparse(u if "://" in u else "http://" + u)
        return f"{p.hostname}:{p.port}" if p.port else (p.hostname or u)

    def _row_summary(self, fields):
        model = fields["model"][0].text().strip()
        url = fields["base_url"][0].text().strip()
        vis = fields["vision"][0].currentIndex()
        bits = [model or self._t("cfg.row_no_model"),
                self._host_of(url) if url else self._t("cfg.row_default_server")]
        if vis == 1:
            bits.append("👁 " + self._t("cfg.row_sees"))
        elif vis == 2:
            bits.append(self._t("cfg.row_blind"))
        if not fields["council"][0].isChecked():
            bits.append(self._t("cfg.row_no_council"))
        return "  ·  ".join(bits)

    def _add_model_row(self, name, enabled, data, section=None, expanded=False):
        box = QWidget()
        box.setObjectName("poolrow")
        box.setStyleSheet("QWidget#poolrow { background:#131c2e; border:1px solid #2a3547; "
                          "border-radius:8px; }")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(10, 6, 10, 6)
        top = QHBoxLayout()
        nm = QLineEdit(name)
        nm.setPlaceholderText(self._t("cfg.model_alias"))
        nm.setMaximumWidth(150)
        nm.setStyleSheet("font-weight:700;")
        badge = QLabel(self._t("cfg.row_base_badge"))
        badge.setStyleSheet("background:#1d4ed8; color:#ffffff; border-radius:6px; "
                            "padding:1px 7px; font-size:12px; font-weight:800;")
        badge.setVisible(False)
        summary = QLabel("")
        summary.setStyleSheet("color:#cbd5e1;")
        chk = QCheckBox(self._t("cfg.enabled"))
        chk.setChecked(enabled)
        chk.setToolTip(self._t("cfg.row_enabled_tip"))
        chk.toggled.connect(lambda _=None: self._refresh_base_combo())
        nm.textChanged.connect(lambda _=None: self._refresh_base_combo())
        _icon_css = ("QPushButton { padding:0; font-size:14px; } "
                     "QPushButton:checked { background:#1d4ed8; border-color:#3b82f6; }")
        ed = QPushButton("✎")
        ed.setFixedSize(34, 30)
        ed.setStyleSheet(_icon_css)
        ed.setCheckable(True)
        ed.setToolTip(self._t("cfg.row_edit"))
        tst = QPushButton("🔌")
        tst.setFixedSize(34, 30)
        tst.setStyleSheet(_icon_css)
        tst.setToolTip(self._t("cfg.test_conn"))
        rm = QPushButton("🗑")
        rm.setFixedSize(34, 30)
        rm.setStyleSheet(_icon_css)
        rm.setToolTip(self._t("cfg.row_remove"))
        top.addWidget(nm)
        top.addWidget(badge)
        top.addWidget(summary, 1)
        top.addWidget(chk)
        top.addWidget(ed)
        top.addWidget(tst)
        top.addWidget(rm)
        bl.addLayout(top)
        details = QWidget()
        form = left_form(QFormLayout(details))
        form.setContentsMargins(0, 6, 0, 2)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        fields = {}
        for key, kind in MODEL_FIELDS:
            cur = str(data.get(key, "")).strip()
            if kind == "pwd":
                w = QLineEdit(cur)
                w.setEchoMode(QLineEdit.EchoMode.Password)
            elif kind == "int":
                w = AutoSpinBox(self._t("cfg.auto"))
                w.setRange(0, 4000000)
                try:
                    w.setValue(int(float(cur or 0)))
                except Exception:
                    pass
            elif kind == "tri":
                w = QComboBox()
                w.addItems([self._t("cfg.tri_unset"), self._t("cfg.tri_yes"),
                            self._t("cfg.tri_no")])
                w.setCurrentIndex(0 if cur == "" else
                                  1 if cur.lower() in ("true", "1", "yes", "on")
                                  else 2)
            elif kind == "bool":
                w = QCheckBox()
                w.setChecked(cur.lower() not in ("false", "0", "no", "off"))
                w.setToolTip(self._t(f"cfg.h.MODEL.{key}"))
            else:
                w = QLineEdit(cur)
            if key == "profile":
                w.setPlaceholderText(self._t("cfg.profile_hint"))
            elif key == "extra_body":
                w.setPlaceholderText(self._t("cfg.extra_body_hint"))
                w.setToolTip(self._t("cfg.h.API.extra_body"))
            _lbl = QLabel(self._t(f"cfg.mf.{key}"))
            _lbl.setToolTip(key)
            form.addRow(_lbl, w)
            fields[key] = (w, kind)
        bl.addWidget(details)

        def _resum(*_a):
            summary.setText(self._row_summary(fields))
        for key in ("model", "base_url"):
            fields[key][0].textChanged.connect(_resum)
        fields["vision"][0].currentIndexChanged.connect(_resum)
        fields["council"][0].toggled.connect(_resum)
        _resum()
        open_ = expanded or not fields["model"][0].text().strip()
        details.setVisible(open_)
        ed.setChecked(open_)
        ed.toggled.connect(details.setVisible)
        self._pool_box.addWidget(box)
        row = {"widget": box, "name": nm, "enabled": chk, "fields": fields,
               "orig": name, "orig_section": section, "test": tst,
               "badge": badge, "summary": summary, "details": details, "edit": ed}
        tst.clicked.connect(lambda _=False, r=row: self._test_model_row(r))

        def _remove():
            if row["orig"]:
                self._removed_models.append(row["orig"])
            box.setParent(None)
            self._model_rows.remove(row)
            self._refresh_base_combo()
        rm.clicked.connect(_remove)
        self._model_rows.append(row)

    def _kv_tab(self, section, note_key):
        inner = QWidget()
        lay = QVBoxLayout(inner)
        note = QLabel(self._t(note_key))
        note.setWordWrap(True)
        note.setStyleSheet("color:#e6edf7; font-size:12px;")
        lay.addWidget(note)
        rows_box = QVBoxLayout()
        lay.addLayout(rows_box)
        rows = []
        self._kv_sections[section] = {"rows": rows, "removed": []}

        def add_row(k="", val=""):
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            ke = QLineEdit(k)
            ke.setPlaceholderText(self._t("cfg.kv_key"))
            ke.setMaximumWidth(220)
            ve = QLineEdit(val)
            ve.setPlaceholderText(self._t("cfg.kv_value"))
            rm = QPushButton("🗑")
            rm.setFixedWidth(36)
            h.addWidget(ke)
            h.addWidget(ve, 1)
            h.addWidget(rm)
            rows_box.addWidget(w)
            row = {"w": w, "k": ke, "v": ve, "orig": k}
            rows.append(row)

            def _rm():
                if row["orig"]:
                    self._kv_sections[section]["removed"].append(row["orig"])
                w.setParent(None)
                rows.remove(row)
            rm.clicked.connect(_rm)

        if self._cp.has_section(section):
            for k, val in self._cp.items(section):
                add_row(k, str(val))
        btn = QPushButton("＋ " + self._t("cfg.add_entry"))
        btn.clicked.connect(lambda: add_row())
        lay.addWidget(btn)
        lay.addStretch(1)
        return self._scroll(inner)

    def _others_tab(self):
        covered_keys = {(s, k) for s, k, _, _ in ALL_SPECS} | ROLE_KEYS
        skip_sections = {"MODELS", "MCP_SERVERS"} | OWN_ALL
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setSpacing(12)
        note = QLabel(self._t("cfg.others_note"))
        note.setWordWrap(True)
        note.setStyleSheet("color:#e6edf7; font-size:12px;")
        lay.addWidget(note)
        n = 0
        for section in self._cp.sections():
            if section in skip_sections or section.upper().startswith("MODEL:"):
                continue
            pend = [k for k in self._cp.options(section)
                    if (section, k) not in covered_keys]
            if not pend:
                continue
            card, form = self._section_card(section)
            for k in pend:
                cur = str(self._cp.get(section, k, fallback="")).strip()
                kind = ("bool" if cur.lower() in ("true", "false") else
                        "int" if re.fullmatch(r"\d+", cur) else "str")
                self._add_row(form, section, k, kind,
                              (0, 4000000) if kind == "int" else None)
                n += 1
            lay.addWidget(card)
        lay.addStretch(1)
        return self._scroll(inner), n


    def _raw_tab(self):
        self.raw_edit = QPlainTextEdit()
        self.raw_edit.setPlainText(self._raw)
        mono = "Consolas" if platform.system() == "Windows" else "DejaVu Sans Mono"
        f = QFont(mono, 10)
        f.setStyleHint(QFont.StyleHint.Monospace)
        self.raw_edit.setFont(f)
        self.raw_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        return self.raw_edit

    def _test_conn(self, section, k_url, k_key, k_model):
        w_url = self._widgets[(section, k_url)][0]
        w_key = self._widgets[(section, k_key)][0]
        w_mod = self._widgets[(section, k_model)][0]
        url = w_url.text().strip() or (self._cp.get("API", "base_url", fallback="").strip())
        key = w_key.text().strip() or (self._cp.get("API", "api_key", fallback="").strip())
        model = (w_mod.currentText() if isinstance(w_mod, QComboBox) else w_mod.text()).strip()
        self._test_endpoint(url, key, model)

    def _test_model_row(self, row):
        url = row["fields"]["base_url"][0].text().strip() \
            or (self._cp.get("API", "base_url", fallback="").strip())
        key = row["fields"]["api_key"][0].text().strip() \
            or (self._cp.get("API", "api_key", fallback="").strip())
        self._test_endpoint(url, key, row["fields"]["model"][0].text().strip())

    def _test_endpoint(self, url, key, model):
        import requests
        if not url:
            self.status.setText("⚠ " + self._t("cfg.test_no_url"))
            return
        self.status.setText("⏳ " + self._t("cfg.testing", url=url))
        headers = {"Authorization": f"Bearer {key}"} if key else {}

        def work():
            try:
                r = requests.get(url.rstrip("/") + "/models", headers=headers, timeout=8)
                if r.status_code != 200:
                    return {"ok": False, "msg": f"HTTP {r.status_code}"}
                names = [m.get("id", "") for m in (r.json().get("data") or [])]
                return {"ok": True, "n": len(names), "has": (model in names) if model else None,
                        "sample": ", ".join(names[:6])}
            except Exception as e:
                return {"ok": False, "msg": str(e)[:120]}

        def done(res):
            if not isinstance(res, dict) or not res.get("ok"):
                self.status.setText("❌ " + self._t("cfg.test_fail",
                                                   err=(res or {}).get("msg", "?")))
                return
            txt = "✅ " + self._t("cfg.test_ok", n=res["n"])
            if res.get("has") is True:
                txt += " · " + self._t("cfg.test_model_ok", model=model)
            elif res.get("has") is False:
                txt += " · ⚠ " + self._t("cfg.test_model_missing", model=model)
            if res.get("sample"):
                txt += f"\n{res['sample']}"
            self.status.setText(txt)

        par = self.parent()
        if hasattr(par, "_bg"):
            par._bg(work, done)
        else:
            done(work())

    def _value_of(self, w, kind):
        if kind == "bool":
            return "true" if w.isChecked() else "false"
        if kind == "int":
            return str(w.value())
        if kind == "float":
            return str(round(w.value(), 4))
        if kind == "choice":
            return w.currentText().strip()
        if kind == "tri":
            return {0: "", 1: "true", 2: "false"}.get(w.currentIndex(), "")
        return w.text().strip()

    def _bad_extra_body(self):
        import json as _json
        bad = []

        def _check(where, raw):
            raw = (raw or "").strip()
            if not raw:
                return
            try:
                val = _json.loads(raw)
            except Exception as e:
                bad.append((where, str(e)))
                return
            if not isinstance(val, dict):
                bad.append((where, self._t("cfg.extra_body_not_object")))

        for (section, key), (w, kind) in self._widgets.items():
            if key == "extra_body":
                _check("[%s]" % section, self._value_of(w, kind))
        for row in self._model_rows:
            _w = row["fields"].get("extra_body")
            if _w:
                _check("[MODEL:%s]" % (row["name"].text().strip() or "?"),
                       self._value_of(_w[0], _w[1]))
        return bad

    def _warn_bad_extra_body(self, bad):
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(
            self, self._t("cfg.extra_body_bad_title"),
            self._t("cfg.extra_body_bad_body") + "\n\n"
            + "\n".join("%s → %s" % (w, why) for w, why in bad))

    def _save(self):
        text = self.raw_edit.toPlainText()
        if text.strip() != self._raw.strip():
            base = text
        else:
            base = self._raw
        for (section, key), (w, kind) in self._widgets.items():
            val = self._value_of(w, kind)
            present, initial = self._initial.get((section, key), (True, None))
            if not present and val == initial:
                continue
            base = set_value(base, section, key, val)
        for section, st in self._kv_sections.items():
            for k in st["removed"]:
                base = del_key(base, section, k)
            for row in st["rows"]:
                k = row["k"].text().strip()
                if not k:
                    continue
                if row["orig"] and row["orig"] != k:
                    base = del_key(base, section, row["orig"])
                base = set_value(base, section, k, row["v"].text().strip())
        _bad = self._bad_extra_body()
        if _bad:
            self._warn_bad_extra_body(_bad)
            return
        for name in self._removed_models:
            base = del_section(base, self._model_section(name) or f"MODEL:{name}")
            base = del_key(base, "MODELS", name)
        _chosen_base = (self._base_combo.currentData() or "").strip() \
            if getattr(self, "_base_combo", None) is not None else ""
        if getattr(self, "_base_combo", None) is not None and not _chosen_base:
            QMessageBox.warning(self, self._t("cfg.no_models_title"), self._t("cfg.no_models_body"))
        for row in self._model_rows:
            name = row["name"].text().strip()
            if not name:
                continue
            renamed = bool(row["orig"]) and row["orig"] != name
            if renamed:
                base = del_section(base, row.get("orig_section")
                                   or f"MODEL:{row['orig']}")
                base = del_key(base, "MODELS", row["orig"])
            base = set_value(base, "MODELS", name,
                             "true" if (row["enabled"].isChecked() or name == _chosen_base)
                             else "false")
            target = (f"MODEL:{name}" if renamed
                      else (row.get("orig_section") or f"MODEL:{name}"))
            for key, (w, kind) in row["fields"].items():
                base = set_value(base, target, key, self._value_of(w, kind))
        if _chosen_base:
            base = set_value(base, "MODELS", "base", _chosen_base)
        else:
            base = del_key(base, "MODELS", "base")
        base = self._save_roles(base)
        _bad = ini_problem(base)
        if _bad:
            QMessageBox.critical(self, self._t("cfg.unreadable_title"),
                                 self._t("cfg.unreadable_body", why=_bad))
            return
        self._on_saved(base)
        self.accept()
