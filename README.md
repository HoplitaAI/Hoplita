# HOPLITA

**A personal AI agent that lives on your machine, works with your models, keeps
your data encrypted, and federates with the people you choose.**

HOPLITA runs against any OpenAI-compatible server — llama.cpp, Ollama, vLLM,
LM Studio, llama-swap, or a cloud API — and turns it into an assistant with
more than two hundred tools, a memory that curates itself, a real coding
environment, a council of models that deliberate, and a federation in which
your contacts share notes, projects and code without the server ever being able
to read them.

Everything that thinks lives in one place, **the MCP server**
(`python/hoplita-mcp.py`): the agent loop, the planner, the reviewer, the model
router, the memory and the tools. Two faces talk to it over HTTP — a desktop
**GUI** (PyQt6) and a Unix-style **CLI** (Go) — and any third face can use the
same API.

---

## Highlights

### Your data, encrypted and yours
- **Everything in one SQLCipher database** — notes, tasks and appointments,
  credentials, cron jobs, facts, lessons, sessions, the firewall — encrypted
  with a key derived from your master password, asked for when the MCP starts.
- **A credential vault**: sites, users, passwords and TOTP secrets. The agent
  gets a credential without the password in its answer, generates OTP codes,
  and the browser agent logs in with the secrets kept out of the prompt.
- **Private notes**: mark a notes folder **local-only** 🔒 and it is never
  shared, never published and never leaves the machine — and in that project's
  turns the agent does not even get the federation tools.
- **A tool firewall**, global and per tab, enforced at every door: the tool
  list, the call endpoint, the loop, delegation and the GUI.

### Chat that does the work
- **Tabs, each its own world**: its conversation, its model, its reviewer, its
  firewall, its project and its queue of pending messages.
- **Agentic by default**: web search (Perplexity, Tavily, Exa, Firecrawl),
  scraping, autonomous browsing with browser-use and Playwright, files, shell,
  PDF and image input, notes, tasks and calendar, generated pictures and charts
  on a live canvas.
- **A definition of done**: `/verify <command>` keeps the turn going until the
  command passes; `/goal <text>` states the bar in words; a **reviewer model**
  checks the answer before you see it, and you can open its review — the
  verdict, what it asked for, and the answer it judged.

### ⏰ Cron jobs that think
Scheduled tasks — fixed time, interval, daily, weekly, or at every start — run
as full agentic turns with their own log. Their output lands in a chat tab of
its own, with a supervisor scoped to your jobs: dislike a result and it
rewrites the job.

### Several models, one assistant
- **A pool of models** with one elected as the base. Every job — browsing,
  background memory work, planning, reviewing, vision, taking over when stuck —
  can run on its own model, and a router can pick per question.
- **The 🏛 Council**: several models deliberate on any matter by turns —
  answer, examine, reformulate — and vote. A stuck turn can convene one itself.
- **Multi-agent orchestration**: a planner splits a task into sub-agents, each
  on its own model, shown as cards with a dependency graph, and runs them after
  you approve the plan.
- **Metacognition while it works**: the model keeps a live intent list you can
  watch; a stall is detected by progress and time, not by a step count, and
  answered with a question, then a council, then another model, then an honest
  close that says what blocked it.

### A coding environment, not a chat about files
- The **💻 Code tab** opens a real repository: changes with per-hunk reject,
  commits that branch first on `main`, history and interdiffs, symbols from the
  LSP, project-wide search, and git checkpoints.
- **RUN / Verify**: a command, what you expect to see, and a model that judges
  the output against your words.
- **The 🧭 Whiteboard**: the agent explains code on a live page — what and why,
  the design as a diagram, the call path old against new — where every phrase is
  anchored to a file and line at a pinned commit and verified against those
  bytes. It follows the code and offers to bring itself up to date.
- **A project's memory**: its `rules`, `spec`, `future` and `journal` notes and
  its task board, read by the agent before it touches anything.

### Memory that keeps itself
- **Facts, lessons and a journal**, searched by words — no embedding server.
- A **write gate** meets each new fact with the ones it overlaps: new, the
  same, an update, or a newer truth that archives the old one. A periodic pass
  curates the rest, and every change it makes can be undone.
- **Working memory per conversation**, and `/why` shows exactly what was put
  in front of the model on the last turn.

### Federation: share without trusting the server
Optional, and built so that the server is a **relay that cannot read what it
carries**. The server is [Hoplita-server](https://github.com/HoplitaAI/Hoplita-server);
run your own or use a public one.
- **Signed trust**: every user has an Ed25519 identity. A contact or a share is
  valid because *you* signed it, not because the server says so.
- **Share what you choose** with each contact — a notes folder, a task project
  with its whiteboards, appointments, skills — encrypted to the recipient's key,
  with explicit acceptance on the other side and permissions per share. Edits
  travel within seconds, pushed over a WebSocket.
- **Public folders** anyone can subscribe to with one click.
- **Co-work on code**: share a repository with a contact, see what arrives from
  them, take it or discard it, and leave when you are done.
- **Ask a contact's agent**, and pilot a session on another of your nodes.
- **Your own machines in sync**: everything that is yours travels between your
  devices as one snapshot sealed to your own key, with tombstones for deletions,
  so a laptop that comes back after a long time away does not undo what
  happened meanwhile.
- **Children**: headless nodes on other machines with their own identity, or
  personalised packs — a role, a model, a few folders, an avatar — that run
  here as sub-agents and show up in your contacts.

### Hands on the desktop
The model can see the screen and act on it — windows, the accessibility tree,
clicks, keys and screenshots — with an on-screen hoplite that stops it on one
click, and a gate that asks before the screen goes to a model outside your
network.

### And more
- **External MCP servers** (stdio or SSE) add their tools to the catalogue.
- **Skills**: reusable instructions, pinned or found on demand.
- **Prefix-cache friendly**: the history is append-only and the tool block is
  frozen per turn, so a local server reuses its cache across the conversation.
- **English and Spanish** interface (`lang/*.json`).

---

## Install

Python 3.11 or newer.

```bash
python install.py
```

The installer creates the app folder and a virtual environment, installs the
core dependencies (`requirements.txt`), creates `config.ini` and the desktop
shortcuts. `--browser` adds the browsing stack and Playwright's Chromium.

To remove it, `python install.py --uninstall` takes away the program, its
virtual environment and its shortcuts and keeps your data (config, encrypted
store, memory, notes); add `--purge` to delete those too.

The default install folder is `~/hoplita` on Linux and macOS and
`%APPDATA%\hoplita` on Windows — your own folder, no root or sudo needed;
`--prefix` picks another. Optional parts are
flags: `--browser`, `--cli` (needs Go), `--desktop`, `--diffr`, `--lsp`,
`--system-libs`, or `--all`. `--update` refreshes an existing install.

## Run

The installer creates desktop shortcuts: start **HOPLITA MCP Server** first (it
asks for the vault password), then **HOPLITA**. On Windows a single shortcut
starts both. From a terminal, in the install folder:

```bash
venv/bin/python hoplita-mcp.py --port 9077   # the brain; asks for the vault password
venv/bin/python hoplita-gui.py               # the desktop GUI
./cli/dist/hoplita-cli "your question"     # or the CLI
```

## Configure

The configuration dialog opens on **Models**: add one model (name, model id,
server URL, key) and it becomes the base everything runs on. Everything else has
a working default; `config.ini.example` lists every key.

## Documentation

- [User guide](docs/GUIDE.md) — install, configuration, every tab and page,
  models and roles, external MCP servers, memory, federation, the CLI and
  troubleshooting.
- [Federation, step by step](docs/FEDERATION.md) — your account, your own
  machines, contacts, sharing, co-working on code, and children: a pack that
  runs here or a node of its own on another machine.
- [Configuration reference](docs/CONFIG.md) — every `config.ini` key with its
  real default.
- [Tools](docs/TOOLS.md) — every tool the model can call, with its arguments.
- [HTTP API](docs/API.md) — the MCP's endpoints and the turn's event stream,
  for writing another face.
- [Definition of done](docs/DEFINITION-OF-DONE.md) — how the Code tab decides
  that a job is finished.

## License

GNU General Public License v3.0 — see `LICENSE`.
