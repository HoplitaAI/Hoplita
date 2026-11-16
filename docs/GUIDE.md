# HOPLITA — User guide


**v1.0**

Personal AI agent system with an MCP server, desktop GUI, CLI, autonomous web browsing, encrypted vault, RAG, CRON, coding tools, a tool firewall, an external-MCP router, **multi-model with automatic routing** and a **bilingual interface (ES/EN)**.

**New here?** [Install it on Linux, macOS or Windows](INSTALL.md) · [First steps: the model and the search key it needs](FIRST-STEPS.md) · [Every command](COMMANDS.md)

## Architecture

```
┌──────────────────┐     ┌──────────────┐
│  hoplita-gui.py  │     │ cli/main.go  │
│   (GUI, PyQt6)   │     │   (Go CLI)   │
└────────┬─────────┘     └──────┬───────┘
         │       HTTP REST      │
         └──────────┬───────────┘
                    │
          ┌─────────▼────────┐        ┌──────────────────┐
          │  hoplita-mcp.py  │───────►│  External MCPs   │
          │    (FastAPI)     │ stdio  │ (any MCP server) │
          │    port 9077     │ / SSE  └──────────────────┘
          └─────────┬────────┘
                    │
      ┌─────────────┼─────────────┐
      │             │             │
  ┌───▼────┐   ┌────▼────┐   ┌────▼───┐
  │ Word   │   │ Vault   │   │ CRON   │
  │ search │   │SQLCipher│   │ jobs   │
  └────────┘   └─────────┘   └────────┘
```

Every LLM client speaks **OpenAI-compatible** (`/v1/chat/completions`). There is no embedding server: memory and documents are searched by words. It works the same against OpenAI, Ollama (`http://localhost:11434/v1`), llama-swap, vLLM, LM Studio, etc.

## Files

The source tree. `install.py` copies the contents of `python/` flat into the install folder, next to `lang/`, `icons/` and `cli/`.

| Path | What it is |
|------|------------|
| `python/hoplita-mcp.py` | The MCP server (FastAPI): tools, the agentic loop, vault, RAG, CRON, firewall, external-MCP router, REST API |
| `python/hoplita-gui.py` | The desktop GUI (PyQt6): chat tabs, notes, TODO, CRON, memory, credentials, firewall, canvas, code view |
| `python/fed_agent.py` | The agentic loop that runs inside the MCP. The GUI and the CLI only draw what it streams |
| `python/hoplita_council.py` | The COUNCIL: several models deliberating by turns on one matter — answer → examine on two criteria → reformulate → vote. Driven by the MCP; the clients draw it and answer its gate |
| `python/hoplita_orchestrator.py` | Multi-agent orchestrator (planner → parallel sub-agents → synthesis), with per-sub-task model assignment. Driven by the MCP; the clients draw the plan and answer its approval |
| `python/hoplita_llmcfg.py` | The one rule for which model runs what (base, roles, vision, `extra_body`) and the INI reader/writer |
| `python/hoplita_prompts.py` | Every text the program sends to a model, in one file |
| `python/hoplita_desktop.py`, `python/hands_hud.py` | Desktop hands and their on-screen HUD |
| `python/wb_*.py`, `python/wb_canvas/`, `python/wb_instructions/` | The whiteboard: store, tools, background drawing, sync and its web page |
| `python/hoplita_federation.py`, `python/fed_*.py`, `python/hoplita_crypto.py` | Federation: identity, end-to-end encryption, sync, shared sessions |
| `python/hoplita_storage.py` | The single SQLCipher database |
| `python/hoplita_launch.py` | One-click launcher (MCP, then GUI) used by the Windows shortcut |
| `python/hoplita_i18n.py` | i18n module for Python |
| `python/hoplita_paths.py` | Where HOPLITA lives (`HOPLITA_HOME`, or the folder that has `lang/`) |
| `cli/main.go` | Go CLI: interactive and one-shot. It does not reason — the loop is the MCP's |
| `cli/turn.go` | Consumes the MCP's `/agent/turn` stream (the same one the GUI consumes) |
| `cli/memory.go` | Asks the MCP to distill memory and compact context (`/memory/*`) |
| `cli/plan_ack.go` | The CLI side of an orchestration: shows the plan and approves it |
| `cli/oneshot.go` | One-shot mode (`--json`, `--quiet`, `--verify`, piped stdin) |
| `cli/i18n.go` | i18n module for Go (reads the same `lang/*.json`) |
| `cli/compile.sh` | Builds the CLI for Linux and Windows into `cli/dist/` |
| `lang/en.json`, `lang/es.json` | English and Spanish interface texts (same key list) |
| `install.py` | Cross-platform installer, updater and uninstaller (Linux/macOS/Windows) |
| `install.sh`, `install.ps1` | One-line installers (Linux/macOS, Windows): fetch the source and, when needed, a standalone Python, then run `install.py` |
| `requirements.txt` | Core Python dependencies |
| `requirements-browser.txt` | Web browsing stack (Playwright, browser-use, crawl4ai) |
| `requirements-desktop.txt` | Desktop hands' pixel layer (pyautogui, mss; pywinauto on Windows) |
| `config.ini.example` | Annotated example config; `install.py` copies it to `config.ini` when there is none |
| `docs/` | `INSTALL.md` (per system), `FIRST-STEPS.md` (what it needs to start), `COMMANDS.md` (every command), `CONFIG.md` (every key), `TOOLS.md` (every tool), `API.md` (REST API), `DEFINITION-OF-DONE.md` |

## Installation

What each system needs before this (Python, git), step by step: **[docs/INSTALL.md](INSTALL.md)**. What to set up after it (a model, a search key): **[docs/FIRST-STEPS.md](FIRST-STEPS.md)**.

### In one line

```bash
curl -fsSL https://raw.githubusercontent.com/HoplitaAI/Hoplita/main/install.sh | sh                  # Linux, macOS
irm https://raw.githubusercontent.com/HoplitaAI/Hoplita/main/install.ps1 | iex   # Windows (PowerShell)
```

`install.sh` and `install.ps1` download the source and run `install.py` with a Python 3.11+: the machine's own when it can make a venv, otherwise (and always on Windows) a standalone Python 3.12 fetched with uv into `<install folder>/python`, with no root, no `PATH` change and nothing outside the install folder. Options go after `sh -s --` (or through a script block on Windows, see [docs/INSTALL.md](INSTALL.md)). Running the line again is a full install over the existing one, so new libraries in `requirements.txt` come in too; `--uninstall` removes the fetched Python with the rest.

### With the installer (Linux, macOS, Windows)

One Python script for all three platforms. It needs Python 3.11+ (the core may work on older versions; browser-use needs 3.11).

```bash
python install.py                    # core: venv, requirements.txt, files, config.ini, shortcuts
python install.py --browser          # + web browsing stack (requirements-browser.txt + Playwright's Chromium)
python install.py --cli              # + build the Go CLI (needs Go)
python install.py --lsp              # + Python language server (diagnostics and navigation in the code editor)
python install.py --desktop          # + desktop hands (requirements-desktop.txt) and a report of what they have
python install.py --diffr            # + diffr, the structural diff behind the whiteboard's Diff view
python install.py --system-libs      # + system libraries (QtWebEngine/tray, the hands' packages) — asks for sudo
python install.py --all              # core + browser + cli + desktop + diffr + lsp + system-libs
python install.py --prefix /path     # install somewhere else
python install.py --no-shortcuts     # skip the desktop shortcuts
python install.py --update           # refresh code, languages, assets and CLI of an existing install
```

The default install folder is `~/hoplita` on Linux/macOS and `%APPDATA%\hoplita` on Windows: a folder of your own, so installing needs no root. The installer creates a venv inside it, installs `requirements.txt`, copies `python/*.py` (flat), `lang/`, `icons/` and `cli/`, and creates `config.ini` from `config.ini.example` only when there is none (an existing `config.ini` is never overwritten). With `--prefix`, HOPLITA finds its own folder (the one with `lang/` next to the code), or you can set `HOPLITA_HOME`.

`--update` does not touch the venv, the dependencies, `config.ini` or the shortcuts; it rebuilds the CLI when the install already has one, and also runs `--lsp` (or `--all`), `--desktop` or `--diffr` when you add them.

When a new version adds a library to `requirements.txt` (the Word, Excel and PowerPoint tools need `python-docx`, `openpyxl` and `python-pptx`), install it into the existing venv after the update:

```bash
python install.py --update
<install folder>/venv/bin/python -m pip install -r requirements.txt          # Linux, macOS
<install folder>\venv\Scripts\python.exe -m pip install -r requirements.txt   # Windows
```

Already-installed packages are left as they are. If you skip it, everything else works and a tool whose library is missing says which one and gives that same command.

### By hand (from the source tree)

```bash
python -m venv venv && . venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-browser.txt   # optional: web browsing, /includeurl
python -m playwright install chromium     # with the browsing stack: browse_web and screenshot_web
pip install -r requirements-desktop.txt   # optional: desktop hands' pixel layer
pip install uv                            # optional: external MCPs launched with uvx
pip install pyright ruff                  # optional: richer diagnostics for code_lint
cp config.ini.example config.ini
```

The code finds `lang/` and `config.ini` in the parent of `python/`, so it runs in place.

### Uninstall

```bash
python install.py --uninstall           # program, venv and shortcuts; your data stays
python install.py --uninstall --purge   # your data too (type DELETE to confirm)
```

Your data is `config.ini`, the vault (`hoplita.db`), memory, notes and logs in
the install folder, the whiteboards in `~/.hoplita` (or `HOPLITA_HOME`), and the GUI's own
settings. `--uninstall` lists all of it before touching anything, and refuses a folder
that is not a HOPLITA install. Add `--prefix` when you installed somewhere else.

### Building the CLI (Go) by hand

```bash
cd cli
./compile.sh            # Linux and Windows binaries: dist/hoplita-cli and dist/hoplita.exe
./compile.sh linux      # only one of them
# or plainly:
go build -o dist/hoplita-cli .
```

`install.py --cli` does the same for the current OS and leaves the binary in `<install>/cli/dist/`.

## Starting it up

### Shortcuts

The installer creates one desktop shortcut, **HOPLITA** (`HOPLITA.desktop` on
Linux, `HOPLITA.app` on macOS, `HOPLITA.lnk` on Windows). It runs
`hoplita_launch.py` with no terminal, which starts the MCP (the vault password is
asked in a dialog) and then the GUI; an MCP that already answers is used as it is,
and only the GUI opens. On Linux the MCP's log is `mcp.log` in the install folder,
and an update replaces the two shortcuts an earlier install made (MCP and GUI). On macOS the app goes to
`~/Applications` — Spotlight (⌘ Space → HOPLITA) and Launchpad find it — with a link on
the desktop; drag it to the Dock to keep it there. An older install's `.command` files
are replaced by it.

### From a terminal

```bash
# Terminal 1: the MCP server (it asks for the vault password)
python hoplita-mcp.py --port 9077

# Terminal 2: the GUI
python hoplita-gui.py

# Or the CLI: interactive, or one-shot with the request as arguments
./cli/dist/hoplita-cli "find information about X"
```

Run them from the install folder (or as `python/hoplita-mcp.py` from the source tree).
The MCP listens on port 9077 by default; the faces find it through `[MCP] url`.

---

## Configuration — the basic path and the fine one

**Basic: fill in one model and live on it.** Open the config (the «Configuration» button in the header). It opens on **Models**. Press **＋ Add model**, give it a name, the model id, the server URL and the key. That model is your **base**, with no other click: the chat, the browser, the memory work, the planner, the reviewer and the desktop hands all run on it. Every other setting has a working default. That is the whole setup for most people, for good.

**Fine: when you want more, or something does not do X.** Still on **Models**:
- add more models;
- in **Who uses which model**, give a job its own model (browser and backup browser, background work such as memory and curation, planner, answer reviewer, the one that takes over when stuck, vision for describing pictures) and turn the router («Pick by itself») on or off;
- set up the picture server in **Pictures**.

The other tabs are subjects: Conversation (tool rounds per turn, tool timeout, tokens, compaction), Plans, Web, Search, Hands, Memory and App, plus **MCPs** (external MCP servers, `[MCP_SERVERS]`), **Other** (sections no tab covers, when there are any) and the raw **INI**. Token usage is not a setting: it has its own **Usage** button on the window's header.

**What to rely on:**
- **There is always a base while any model is on.** A base you switch off moves to the first active model, and its line wears the blue BASE badge. With no model at all, saving says so.
- **Each model's line holds its whole connection.** The `[API]` section is not shown: when the dialog opens, what a model inherited from it is written on that model's line, so no model changes server.
- **Keys no code reads are not shown.** They stay in the file.
- **One rule for which model runs what**, in `hoplita_llmcfg`, used by the MCP and the GUI (the Go CLI mirrors it):
  - the base is `[MODELS] base`;
  - a role naming a pool alias runs on that model;
  - «does it see images?» is that model's own declaration, then `[CHAT] vision`.

The full reference, every key with its real default, is **[docs/CONFIG.md](CONFIG.md)**.

## config.ini — reference

**Key convention:** the base is the pool model elected with `[MODELS] base`. A role section (`[BROWSER] model`, `[MEMORY] model`, `[CHAT] planner_model`…) names a pool alias and runs on that model. Any section's empty `base_url`/`api_key`/`model` fill from the base.

### [APP]

| Key | Description | Default |
|-----|-------------|---------|
| `language` | Interface language: `en`, `es` (needs a GUI restart) | `en` |

### [API]

The default connection. Once a pool base is elected (`[MODELS] base`) it chooses **no model**: its fields fill whatever the base leaves empty, and it is the base only while no pool model is elected. The config dialog does not show it; it writes what each model inherited from it onto that model's own line.

| Key | Description | Default |
|-----|-------------|---------|
| `base_url` | LLM server URL (must end in `/v1`) | `https://api.openai.com/v1` |
| `api_key` | Bearer token. For local servers that do not validate it, any dummy value (`ollama`, `xx`, …) | — |
| `model` | Model name | — |

**Examples:**
- OpenAI: `base_url=https://api.openai.com/v1`, `api_key=sk-...`, `model=gpt-4o`
- Local Ollama: `base_url=http://localhost:11434/v1`, `api_key=ollama`, `model=my-model`
- Your own llama-swap or llama-server: `base_url=http://localhost:8080/v1`, `api_key=xx`, `model=my-model`

### [MODELS] + [MODEL:name] — the model pool

Every model lives here, one of them elected **base** with `base = <alias>`. Each sub-task (or chat turn) can run on the model that fits best. **The base has priority**; work is only delegated when a specialist is a better fit. In the dialog: the Models tab, one line per model.

`[MODELS]` lists the active models (one per line, `name = true`). Each is configured in its own `[MODEL:name]` section:

| Key | Description |
|-----|-------------|
| `base_url` | That model's endpoint URL (it may live on another machine) |
| `api_key` | Token (a dummy one works for local servers) |
| `model` | The model's exact name on that endpoint |
| `profile` | A plain-language description of what it is good at and what it prefers. The router and the planner read this to decide when to use it |
| `context_length` | Its context window in tokens; empty = asked of the server |
| `vision` | Whether it sees images (`true`/`false`); empty = `[CHAT] vision` |
| `extra_body` | **Passthrough**: a JSON object that rides as-is in the body of every request to that endpoint, for what is not standardised and is not HOPLITA's to translate. Thinking is the case: qwen wants `{"chat_template_kwargs": {"enable_thinking": true}}`, deepseek wants `{"reasoning_effort": "high"}`, and both answer in `reasoning_content`. It is **not inherited** (sent to a model that does not know it, it is a 400) and it cannot override `messages`/`model`/`tools`/`stream`. Editable in the GUI as «Extra options (JSON)» on each model's line (✎); broken JSON is refused on save, never saved silently |

```ini
[MODELS]
base = general
general = true
coder = true

[MODEL:general]
base_url = http://localhost:8080/v1
api_key = dummy
model = my-model

[MODEL:coder]
base_url = http://gpu-box.lan:8080/v1
api_key = dummy
model = my-coder-model
profile = Intensive coding, refactors, advanced tooling, concurrency
  debugging, long and precise code generation. 120K context.
  Prefers complex programming tasks over chat or browsing.
```

Adding a model = adding its section. **No code changes.** Without `[MODELS]`, everything runs on `[API]` (a hand-written config with no pool).

**Three ways to use the pool:**
1. **Automatic router** (`[CHAT] auto_route_models = true`): before each turn, a short call decides whether the request belongs to a specialist and routes it there. Deterministic in code — it does not depend on the LLM calling a tool.
2. **Manual** with the `/model <name>` command: pins the chat's model on the fly. Takes priority over the router. `/model base` pins the base model with the router bypassed; `/model auto` hands the choice back to the router. Three different answers, each with its own word.
3. **Multi-agent** (`/multi` or the `plan_and_orchestrate` tool): the planner splits sub-tasks across pool models and runs them in parallel.

### [CHAT]

| Key | Description | Default |
|-----|-------------|---------|
| `timeout` | Seconds to wait on the LLM | `120` |
| `context_length` | Context window in tokens. 0 or absent = detected from the server (llama-server `/props`, vLLM `/v1/models`); each `[MODEL:x]` may carry its own | `0` |
| `vision` | Whether the model sees images, for a model that does not declare it | `false` |
| `font_size` | Base font size of the chat transcript (see *Legibility*) | `13` |
| `agentic_max_iterations` | Maximum tool rounds per chat turn (sub-agents of a plan use 5 when unset) | `8` |
| `max_tokens` | Cap on the LLM's response tokens | `8000` |
| `auto_route_models` | Automatic model router: a pre-decision that sends the turn to a pool specialist when it fits. `false` = manual `/model` only | `true` |
| `multi_max_parallel` | Sub-agents running in parallel in the multi-agent | `4` |
| `multi_max_retries` | Retries per failing sub-task | `2` |
| `council_max_rounds` | Ceiling on a council's rounds. Not the policy: a split vote asks you whether to spend another one and never repeats on its own. This only stops a stuck loop with nobody watching | `4` |
| `council_gate_timeout` | Seconds a council waits for that decision. On timeout it stops and hands back the answer with the disagreement signed — never another round nobody asked for | `900` |
| `compact_threshold` | Context fraction that triggers auto-compaction | `0.85` |
| `compact_timeout` | Seconds one compaction slice may take; 0 = max(`timeout`, 900) | `0` |
| `compact_keep_recent` | Recent messages kept intact when compacting | `6` |
| `metacog` | Metacognition: the stall nudge (it asks, never dictates; max 2 per turn, re-armed only by real progress) | `true` |
| `metacog_stall_seconds` | Seconds without real progress before the tap (checked between tools, never inside one) | `120` |
| `metacog_stall_tools` | Consecutive sterile tools (repeats/errors/empties) before the tap | `4` |
| `heavy_sterile_cut` | Sterile streak that turns an expensive tool (the browser) off for the rest of the turn, told to the model. Never a count of uses | `3` |

### [BROWSER]

Browser-use. An empty connection is filled from the base model. Applies to the MCP server (where browser-use runs).

| Key | Description | Default |
|-----|-------------|---------|
| `model` | **Role** («Browser» on the Models tab): the pool alias that drives the browser. Empty = the conversation's model (the tab's pin, else the base). **Recommended:** a strong model that sees | — |
| `headless` | Browser with no window | `false` |
| `viewport_width` | Viewport width | `1366` |
| `viewport_height` | Viewport height | `900` |
| `locale` | Browser locale (e.g. `en-US`); empty = the app's language | — |
| `use_vision` | `auto` / `true` / `false`. `auto` = the driving model's own vision declaration, else `[CHAT] vision` (the one vision rule) | `auto` |
| `wait_between_actions` | Delay (seconds) between the agent's actions | `1.2` |
| `wait_network_idle` | Wait for the network to go idle after loading a page | `3.0` |
| `max_steps_default` | Default maximum iterations when `browse_web` does not pass `max_steps` | `30` |
| `max_actions_per_step` | Actions per agent turn | `3` |
| `default_skill_category` | Note category `/skill` searches by default | `skills` |

### [HANDS]
The desktop hands (`hands_*`). Applies to the MCP server, which is the one that has the screen.

| Key | Description | Default |
|-----|-------------|---------|
| `remote_model` | `ask` · `allow` · `deny` — what happens when the turn's model is neither on this machine nor on your local network | `ask` |
| `local_hosts` | Extra hosts, IPs or CIDRs that count as your local network | — |
| `verify` | Capture before and after each action and say whether anything changed | `true` |
| `action_delay` | Seconds to let the interface settle before verifying | `0.4` |
| `screenshot_max_width` | Width the captures sent to the model are scaled down to | `1280` |
| `consent_timeout` | Seconds the consent dialog waits for an answer | `300` |
| `pixel_backend` | Pixel layer picked from the session; `x11` or `wayland` to force one | `auto` |

### [RAG]

| Key | Description | Default |
|-----|-------------|---------|
| `chunk_size` | Words per chunk | `200` |
| `chunk_overlap` | Overlap between chunks | `50` |
| `search_k` | Results per RAG search | `5` |
| `min_similarity_score` | Anti-noise floor for document search, relative to the best hit | `0.18` |

### [PERPLEXITY]

| Key | Description | Default |
|-----|-------------|---------|
| `api_key` | Perplexity API key for `search_web` | — |
| `model` | Perplexity model | `sonar-pro` |
| `url` | Base URL | `https://api.perplexity.ai` |

`[SEARCH] provider` picks the engine behind `search_web` (`perplexity`, `tavily`, `exa` or `firecrawl`, default `perplexity`); each has its own section (`docs/CONFIG.md`).

### [MCP]

| Key | Description | Default |
|-----|-------------|---------|
| `url` | Where the GUI and the CLI reach the MCP server | `http://127.0.0.1:9077` |
| `enabled` | Not read by the code; shown in the dialog only | `true` |

### [CRAWL4AI]

The first engine `scrape_url` tries (also behind `/includeurl`, `/includeurls` and `/todo`). Read by the MCP, except `max_urls`.

| Key | Description | Default |
|-----|-------------|---------|
| `timeout` | Seconds per page | `60` |
| `max_depth` | Link depth to follow from the page (0 = that page only) | `0` |
| `max_pages` | Page cap when `max_depth` > 0 | `10` |
| `max_urls` | URLs of one file that `/includeurls` scrapes (GUI) | `10` |

### [MCP_SERVERS] — external MCPs

This is how you add an external MCP server: one entry `name = transport:command_or_url`
(also editable in the config dialog's **MCPs** tab). Changes made by hand or in the dialog need an MCP restart; one the assistant makes for you (`edit_my_config`, after you approve it) connects the server on the spot.
Extra parameters: `name_key` and `name_whatever`, passed to a stdio server as environment variables.

```ini
[MCP_SERVERS]
# stdio: launches a local subprocess
tool = stdio:<command> <args>
tool_key = <key>              # given to the process as TOOL_KEY

# SSE: connects to a remote server
other = sse:https://mcp.example.com/sse
other_key = key-xxxxx

# Any name_xxx becomes NAME_XXX as an environment variable
github = stdio:npx -y @modelcontextprotocol/server-github
github_key = ghp-xxxxx
```

**Supported transports:**
- `stdio:command` — spawns a subprocess, JSON-RPC over stdin/stdout
- `sse:url` — connects over HTTP POST/SSE; `name_key` is sent as its API key

**Environment variables (stdio):**
- `name_key` → `NAME_API_KEY` (special case)
- `name_xxx` → `NAME_XXX` (generic)

---

## Tools — the full reference lives in `docs/TOOLS.md` (extracted from the real registry)

The main families, in short. `docs/TOOLS.md` lists every tool, including canvas, pictures, whiteboard, desktop hands, chat sessions, orchestration, git and federation.

### 🌐 Web

| Tool | Description |
|------|-------------|
| `browse_web` | Autonomous web browsing (browser-use + Playwright). Returns `{result, success, steps_used, max_steps}` |
| `scrape_url` | Read a URL with a cascade: crawl4ai (renders JavaScript) → plain HTTP → browser-use if needed; `depth` follows same-domain links |
| `screenshot_web` | Capture a web page as a PDF on disk |
| `search_web` | Web search through the engine set in `[SEARCH] provider` (Perplexity, Tavily, Exa or Firecrawl) |
| `call_api` | Call any REST API. Auth: bearer, api_key (header/query), basic, none |

### 📝 Notes

| Tool | Description |
|------|-------------|
| `add_note` | Add a note to a category (encrypts the content) |
| `search_notes` | Search notes by words (rarity + an explanation of why each one matched) |
| `delete_note` | Delete a note |
| `list_categories` | List note categories |

### 🔐 Credentials

| Tool | Description |
|------|-------------|
| `get_credential` | Retrieve a credential (no password in the answer) |
| `list_credentials` | List saved sites |
| `get_otp` | Generate an OTP code (TOTP) |
| `fill_credentials` | Auto-fill in the live browser (runs inside `browse_web`, which handles it with `sensitive_data`) |

Adding and deleting credentials is manual, from the GUI's credentials dialog (through the MCP's `/data` endpoint), not an agentic tool.

### ✉ Mail

| Tool | Description |
|------|-------------|
| `mail_folders` | A mailbox's folders with their role (inbox, junk, trash…) and counts |
| `mail_list` | The newest messages of a folder, searched by date, sender, subject or text, with a snippet each |
| `mail_read` | One message whole: its text and its attachments |
| `mail_move` | Messages to another folder or to junk / trash; there is no permanent delete, and a move is undone by moving them back |
| `mail_send` | Send from the mailbox: to/cc/bcc, files attached, or a threaded reply to a message; a copy kept in Sent |

A mailbox is a credential of kind **Mailbox (IMAP)**: address, password, IMAP and SMTP server (left empty, they are taken from the address). Gmail needs an app password (2-Step Verification, then myaccount.google.com/apppasswords); Outlook and Hotmail require OAuth over IMAP and are not supported. With a CRON job in its own tab ("every 5 minutes, move the spam out of my inbox") the mailbox gets a worker you can talk to: tell it in its tab that a sender is not spam, and it changes its own job and puts the messages back; tell it to answer one, and it sends the reply. Sending asks no confirmation: whether a tab may send is decided in the tool firewall, where `mail_send` can be turned off globally or per tab.

### ⏰ CRON

| Tool | Description |
|------|-------------|
| `add_cron` | Create a scheduled task (`fixed`, `interval`, `daily`, `weekly`, `startup`) |
| `list_cron` | List scheduled tasks |
| `delete_cron` | Delete a task |
| `toggle_cron` | Enable/disable a task |
| `update_task_progress` | Update a TODO task's progress |
| `delete_current_cron_job` | Delete the CRON job that is running now |
| `disable_current_cron_job` | Pause the CRON job that is running now |
| `set_cron_context` | Set the active CRON context (client-internal use) |

### 📂 System

| Tool | Description |
|------|-------------|
| `run_command` | Run a system command (with a minimal blocklist of destructive commands) |
| `read_file` | Read a file |
| `list_dir` | List a directory |
| `generate_pdf` | Generate a PDF from text/markdown (fpdf2) |
| `pdf_info` | X-ray of a PDF: pages, outline, form fields, detection of two-pages-per-sheet scans and of unnumbered front matter, text by band, render to PNG |
| `pdf` | A pipeline of PDF steps: split two-page spreads, extract by printed page number (auto-mapped), merge, rotate, crop, stamp, number, fill forms, encrypt |
| `office_info` | Read Word, Excel and PowerPoint: blocks with their style, cells with values and formulas, slides with shapes and notes; `find`; a view of the whole file on the canvas |
| `office` | A pipeline of office edits: replace keeping the format, paragraphs, tables, cells, formulas, rows, sheets, slides, notes; refuses to overwrite a file it would damage |
| `read_folder` | Read every file in a folder (batch, multimedia + text) |

To edit files the model uses `code_apply_patch`, which is atomic (all-or-nothing with rollback) and creates a new file when the path does not exist and a single change with `old=""` is passed.

### 🧠 RAG / Memory

| Tool | Description |
|------|-------------|
| `search_rag` | Search the local knowledge base (chunked documents, by words) |
| `search_experience` | Search past experiences |
| `use_skill` | Find and return a skill's content from notes |
| `search_lessons` | Search learned lessons (reusable statements distilled from feedback) |
| `list_lessons` | List ALL lessons (for consolidation) |
| `add_lesson` | Add a lesson (e.g. a consolidated rule) |
| `deactivate_lesson` | Deactivate a lesson (marked stale, recoverable) |
| `delete_lesson` | Delete a lesson (permanent; for junk) |
| `search_facts` / `add_fact` / `delete_fact` | User fact management |
| `list_facts` | List ALL facts (for consolidation) |

The `list_*` / `add_lesson` / `deactivate_lesson` tools serve the **weekly memory pass**, which runs by itself (`[MEMORY] auto_review`): it merges redundant rules and look-alike facts, archives what ended or was never a fact, and every change can be undone — so it applies rather than asking. See **Memory: it keeps itself** below.

Experiences are saved through the MCP's `/feedback` endpoint (👍/👎 on an answer), not by an agentic tool.

### 💻 Code

| Tool | Description |
|------|-------------|
| `code_index` | Index a project: tree, languages, sizes, detected project type |
| `code_read_smart` | Smart read: a small file comes whole, a large one as structure + chunks |
| `code_read_range` | Read a range of lines; past the turn's result cap it is cut on a whole line and says which lines fit |
| `code_search` | Literal search across the whole project, whatever its size: exact totals and where the hits are, then pages of hits grouped by function, each with its count, its span and the read that fetches it whole; `next` fetches the following page |
| `code_apply_patch` | Atomic edits (all-or-nothing with rollback). Creates files when missing |
| `code_lint` | Diagnostics: pyright (types) + ruff (style) + go vet + tsc + cargo + fallbacks |
| `code_deps` | Extract a file's imports/exports |
| `code_references` | Find every reference to a symbol in the project |

### 🔔 Other

| Tool | Description |
|------|-------------|
| `alert` | Popup with sound (handled by the GUI) |
| `intent` | The turn's intent list: the model plans out loud and you watch it cross items out live (🧭) |
| `get_agentic_config` | Returns the current `base_url`, `model` and `platform` |
| `get_todo_context` | Every project's tasks with progress and dependencies, plus the appointments |
| `add_appointment` | Create an appointment in TODO |
| `add_task` | Create a task in TODO (optional `depends_on`: the tasks it depends on) |
| `task_depends` | Set a task's dependencies (by title; stored by `uuid`, the one identity that survives federation) |

### 🔌 External (dynamic)

External MCP tools are registered with a prefix: `<server>.<tool>`.
They show up in the firewall automatically and are available to the LLM.

Bundled media post-processor: when an external MCP returns `http(s)://` URLs with audio/image/video, `data:audio/...;base64,...` data URIs, or large base64 blobs (>10KB), the MCP downloads and saves the file to `/tmp/{server}_{tool}_{ts}.{ext}` and replaces the value in the response with the local path. Detection by extension, content-type and magic bytes.

---

## Whiteboard — the agent explains code on a live, code-anchored page

The whiteboard is a page of the **`</>` code view**: the 🧭 icon in its rail, beside Verify and Co-work. It works like a document program: one row of buttons on top, one for each thing, and below it the surface that shows the thing pressed.

- **How it works**: the project explained part by part, with its code.
- **History**: the project's past told period by period, with a row to choose its span.
- **Commits**: the project's commits, newest first. Each one has **Explanation** and **Diff**: the diff is always there, read from git with no model; the explanation is drawn only when you press **Explain this commit**.
- **Changes**: your uncommitted work, with its **Explanation** and **Diff** the same way.
- **From contacts**: what a contact sent, when there is something.

Nothing draws on its own. A thing not drawn yet says what drawing it costs and offers it; a drawing runs **in the background**, inside the MCP, while you go on working with the agent of your chat, and the surface shows it the moment it exists. The status beside the buttons says whether the whiteboard on screen is up to date, behind by some commits, or being drawn.

Every place is a step you can go back from: a section, a commit, a period of a history, a step of a tour, a code peek. **Back** (the ← button, Alt+← or the mouse's back button) returns to the previous one, scrolled where you were. A **tour** is a mode of the page with its own bar: step *n* of *m*, previous and next (or the arrow keys), and **Exit tour** (Esc), which returns to exactly where you were.

It is drawn the way an engineer reads an unknown repository, not by one agent reading until its context overflows: code first maps the project (README, manifests, the modules the rest imports, entry points, what changes), one call plans the story, isolated explorers read one part each and hand back claims with the line of code that proves each, code turns those lines into exact anchors, and one call writes the prose. What explorers learned is remembered by the files' contents, so bringing a whiteboard up to date re-reads only what changed.

The page follows the whiteboard the moment it exists, so you watch it being drawn. **Which model draws** is a box on the same page: with one model it just says which; with a pool you pick one the first time (the drawing waits for you) and the page remembers it. **A whiteboard follows its code**: when the project has moved on from «How it works» or from the uncommitted work it was drawn from, the page says so and offers **Bring it up to date** — the same kind of background agent re-pins it, fixes the anchors that no longer hold and revises what changed, as a new version (the old one stays in the history). Nothing is brought up to date until you press it. A whiteboard of one commit is that commit's record and never changes.

**History** draws the project's past from its commits: pick the months (from the beginning to now, or any span), and the whiteboard shows one box per period joined in time («Aug 2026 · Card payments → Sep 2026 · Product search»), the arc of the whole span on top, and under each box what that period did with the commits that show it best. It reads what each commit says of itself and which areas it touched, never the diffs, so a history of thousands of commits costs a call per period. The cut follows the span: weeks for up to six weeks, months for up to a year and a half, quarters beyond. A period is remembered by its last commit: the periods that are over are never told again, and drawing the history next week tells only what is new. A period too long for one call is told in pieces and then as one; nothing is cut to fit. Same months, same last commit: it opens the whiteboard instead of drawing it again. A history that reaches today says when commits have landed since, and **Bring it up to date** tells only the new periods over the same whiteboard. On the page the periods stay in a strip above the history: pressing one shows that period alone, its story and every one of its commits, with previous and next; pressing a commit opens it under **Commits**.

The **⋯** menu beside the whiteboard's status holds **Redraw from scratch**, its earlier **versions**, and **Delete**. Redraw draws it again with the model picked in the box, reusing nothing the previous model read, as a new version (the old ones stay in its versions); the menu says what it costs, and nothing else asks. Delete takes it out of sight at once with **Undo** for 8 seconds, then removes it here and wherever it travelled. The page itself is not a browser: no browser menu, no navigating away — its right-click offers copy and copy for agent, and web links open in the system browser. You can also ask in the chat ("review the last commit on a whiteboard", "show me how this code works") and the page comes up on what that agent writes.

Either way the whiteboard carries: what and why, the requirements in your own words (quoted from the conversation, and checked to be really there), the design as one diagram — a sequence, a flow or a database lens — and the implementation as the call path old against new, with code peeks where the mechanism lives. Every phrase, step, node and operation that describes code carries a file and line range at pinned commits, verified against those bytes before the edit is accepted; clicking it opens the code beside the page or in a guided tour, and **Open file** takes you to the line in the code tab. The Diff view splits the change into lenses and tracks what you have read. With `python install.py --diffr` the diff is structural (diffr, a difftastic fork): unchanged regions, tests and generated files fold away.

A whiteboard belongs to its repository's project and is its way of **seeing and weighing a change**: it reads the project's `rules`, `spec` and `future`, holds no specs or tasks of its own, and when it is finished the project's `journal` gets a line pointing at it (see *A project, one job per part*). The changes page's **Whiteboard of these changes** starts the uncommitted-changes drawing and takes you to the page. Tools: `wb_*` in `docs/TOOLS.md`, section *Whiteboard*.

**It travels like the rest of the project.** Your other machines get every whiteboard in the sealed device snapshot, and the people a project is shared with get its whiteboards next to its tasks, over the same link and grants. What travels is the latest version, with its images, traces and maps; older versions stay in the history of the machine that wrote them. A whiteboard arrives already in the journal, so the machine it lands on writes no second line for it. Repository ids belong to one machine, so the machine a whiteboard lands on finds its own checkout by the pinned commits: once the project is open there, even at a different path, the code reads. Until then, reading code says the repository is not on that machine yet. Edits come back as the next version. If two people edit the same version at once, the losing side's edit is kept as a `-local` whiteboard. If one side had already settled the clash, the overtaken edit stays in its history and `wb_restore` brings it back. Deleting a whiteboard removes it everywhere. A whiteboard of uncommitted work shows the working files of the machine it is opened on.

## Desktop hands — the model on your screen, and a hoplite to stop it

`hands_*` tools let a turn work on your desktop: list windows, read a window's elements, press buttons and fill fields through the accessibility layer (AT-SPI on Linux, UI Automation on Windows, the AX API on macOS), click and type by raw input where the session offers it, capture the screen and **verify** that each action changed something (a capture before and after, an element's state or text read back — `verified: true/false`, or `null` when nothing could measure it). Full list: `docs/TOOLS.md`, section *Desktop hands*.

**Coordinates are read in what the model saw.** The capture a model is shown is shrunk to `[HANDS] screenshot_max_width` (with the default 1280, a 2560×1440 screen arrives as 1280×720) and may be cropped to a window or region; `hands_click(x, y)` and `hands_screenshot(region=…)` take the pixel in the last image shown, and the hands undo the scale and the crop. The model never does the arithmetic.

What happens on screen while the hands work:

- **HOPLITA steps aside.** On the first hands tool of a turn the window retires to the tray and never raises itself, activates itself or opens a modal dialog until the turn ends (alerts wait). The model never sees the app's own windows or the HUD: their processes are excluded from every list and tree.
- **The HUD is the hoplite.** One small helmeted figure, always on top, in a corner, draggable. Its halo breathes while the hands work, goes still when paused, turns red when the last action failed. Hover reads what it is doing. **One click = stop**, immediately, no confirmation. Right button: pause, resume, bring HOPLITA back.
- **One turn holds the hands** per machine; a second turn asking for them is told who has them.
- **Your screen does not leave silently.** Everything these tools return is your screen, and it goes to the turn's model. A model on this machine or your local network gets it; one out on the internet only if you allow it — a dialog once per turn (`[HANDS] remote_model = ask`), always (`allow`) or never (`deny`). Unattended turns are refused.

`python install.py --desktop` (also inside `--all`, and on an existing install with `--update --desktop`) installs the pixel-layer libraries in the venv and ends by printing what the hands actually have (on macOS the plain install already brings the one library the pixel layer needs there, so no flag is needed); add `--system-libs` (the installer's one flag that asks for sudo) and it also installs the distribution's accessibility binding for Python and, where packaged, the Wayland input helper with its daemon, otherwise it prints the exact command for your distribution. `hands_status` reports the same at any time. Platforms: Linux is the primary one (AT-SPI for elements; on an X display a pointer library and a screen grabber; on Wayland the compositor's screenshot tool and, when installed, an input-injection helper for raw input — the element layer needs neither). Windows gets its element layer through UI Automation (the `pywinauto` binding, installed by `--desktop`); it is written against the binding's documented surface and tested only through a stand-in, so treat it as experimental — `hands_status` says what is missing. macOS reports what it has and which permission is missing rather than pretend; it grants Accessibility and Screen Recording per binary, to the process that launched the app.

## Chat commands

All of them, plus the CLI's own commands and flags, on one page: **[docs/COMMANDS.md](COMMANDS.md)**.

| Command | Description |
|---------|-------------|
| `/searchrag <text>` | Search the documents loaded into the local RAG |
| `/search <text>` | Search online with `search_web` (the engine in `[SEARCH] provider`) |
| `/include <file>` | Pin the whole file into this tab's context (rides every turn) |
| `/includeurl <url>` | Scrape a URL and load it into the RAG |
| `/includeurls <file>` | Scrape every URL found in a file into the RAG (up to `[CRAWL4AI] max_urls`) |
| `/addtext <file>` | Load a plain-text file into the RAG |
| `/summary [topic]` | Build a hierarchical summary of the accumulated context |
| `/todo <text>` | Chat with the task/appointment manager |
| `/fixed <text>` | Pin permanent context for the chat |
| `/notes <category>` | Load a category's notes into the RAG |
| `/browse <instruction>` | Autonomous browser (`browse_web`) |
| `/skill <search>` | Find a skill and run it as a prompt (`/skill add`, `/skill pin`, `/skill unpin`, `/skill pinned` manage them) |
| `/multi <task>` | Force the multi-agent orchestrator (planner → parallel sub-agents → synthesis) |
| `/model` | Show the chat's active model and the available pool |
| `/model <name>` | Pin a pool model for the chat (on the fly). Takes priority over the router |
| `/model base` | Pin the base model; the router is bypassed |
| `/model auto` | Hand the choice back to the router |
| `/verify <cmd>` | This tab's definition of done in commands: while it exits ≠ 0, the loop does not accept «finished». `off` clears it. The same command as on the VERIFY page |
| `/goal <text>` | The definition of done in words, for what no command checks. The model reads it before starting and the reviewer uses it as the acceptance criterion. `/goal off` clears it, bare `/goal` shows it |
| `/qa <alias\|off\|auto\|base>` | Who reviews this tab's answers before they are accepted. `<alias>` names a pool model, `base` pins the base model, `off` removes the reviewer here, `auto` returns to the config's judge. Naming a judge does not make that model answer the turn — that is `/model` |
| `/pin` | Tie this tab to this machine: the conversation stays visible everywhere, and its turns run here, where its files, services and networks are. `/pin off` releases it, `/pin ?` says which machine it is tied to |
| `/why` | What was put in front of the model on this tab's last turn: the trail behind its answer |
| `/context` | What the next prompt weighs, block by block, heaviest first: history by role, the fifteen heaviest blocks, the tools carried. The meter gives one figure; this says what the figure is made of |
| `/compact` | Fold the old part of this tab's context into a summary now, without waiting for the meter. Same routine as the automatic one; it says the size before and after and what the untouched recent window still weighs |
| `/queue` | What is waiting in this tab's queue. `/queue <n>` takes message n back into the box to edit it, `/queue del <n>` drops it, `/queue clear` empties the queue (and a pending compaction) |
| `/alert <message>` | Popup with a sound |
| `/creds` | Credentials manager (dialog) |
| `/help` | Help, grouped by purpose (the three memories: what is pinned and travels whole, the searchable RAG, and the code the model reads by itself) |

They are typed with `/` (`@` still works as a live alias), the input remembers
with ↑/↓, and an unknown command is not sent to the LLM as if it were chat.

## GUI tabs

The main window's tabs:

| Tab | What it holds |
|-----|---------------|
| **CHAT** | LLM chat (streaming), in tabs of its own (below). Image/PDF paste. Token counter with auto-compaction |
| **TODO** | Two sides, chosen in the sidebar. **Appointments**: a calendar of their own, month, week and the agenda (every appointment in order, past and future), the urgency as a colour (translatable, stable internal keys), the federated ones waiting under **Pending**; a day or an hour clicked is a new appointment at that moment. **Projects**: tasks with status/progress and per-project sub-tabs: 📋 list, 🧩 **boxes** (tasks colored by priority, the % edited in the box — stepper, bar click or typed, no dialog), 🕸 **dependency tree** (deps stored by `uuid` so they survive federation; cycles shown in red, never auto-broken; a dependency whose task was deleted elsewhere is shown as such, not hidden) and 🕒 **timeline** (append-only `project_events`: who, what, from which machine — it syncs across devices and logs federated edits under their author's handle) |
| **NOTES** | Notes by category, edited as rich text and stored as plain markdown |
| **CRON** | Scheduled tasks (`fixed`/`interval`/`daily`/`weekly`/`startup`). Agentic execution with its own log. `startup` («When HOPLITA starts») runs once each time the app opens — for a machine that is not on all day |
| **Tools** | The firewall: enable/disable tools globally |
| **Memory** | Sub-tabs **Facts**, **Lessons**, **Experience**, **Conversations** and **Proposals**. Facts shows **Live** and **Archive** apart, each with its count; the archive with its size and **Empty archive…**. Proposals lists what the memory pass did on its own, each with **↩ Undo**, and **🔎 Curate memory** runs the pass over the whole store. It refreshes when you click the tab |
| **Canvas** | The agent's free visual surface: charts, pages, generated pictures (as figures; **Save as…** writes them to files); `canvas_eval`/`canvas_see` let the model check what it drew |
| **Terminal** | An embedded terminal (Linux/macOS) |
| **Contacts** | Federation: identity, contacts, presence, permissions |

Inside the chat, beside the chat tabs, three buttons switch the chat area to a page of
the active tab, and pressing one again returns to the conversation:

| Page | What it holds |
|------|---------------|
| **🤖 Agents** | The multi-agent board: sub-task cards + dependency graph, with the model assigned to each. On top, the 🧭 band with the turn's intent list (metacognition), crossed out live |
| **`</>` Code** | The code view of the active chat tab: project, changes, editor, symbols, history, search, the VERIFY and CO-WORK pages, and the 🧭 whiteboard page. Its own section below |
| **🏛 Council** | Your models deliberating by turns on whatever you put to them. Its own section below |

And one special chat tab, **⏰ CRON**: where the crons land — their output, and a supervisor scoped to your jobs. It is born when something happens, not at startup.

**A project, one job per part.** A code project (the directory open in the CODE
tab; its name is also its notes folder and its task project) is made of these
parts, and each one does one thing:

| Part | Its one job | Written by | Seen in |
|---|---|---|---|
| `rules` | your standing orders for this project | you (the agent only reads them) | Notes, the project's folder |
| `spec` | what the project IS and how it must behave, in short annotations | the agent (`project_write note=spec`) and you | Notes |
| `future` | what is pending and not being done now — a plan lives here | the agent and you | Notes |
| board | at most **seven** open tasks: only what is being done now and you asked for or approved | the agent and you | CODE tab; the 🧩/🕸/🕒 pages of the TODO tab |
| `journal` | what happened, dated — decisions, traps, what changed, and every finished whiteboard | the agent; a whiteboard writes its own line | Notes; its tail rides every code turn |
| whiteboard | **seeing and weighing a change**: what, why, the design as one diagram, the old and new call path, each claim anchored to the code at pinned commits | the agent (`wb_*`), or one in the background from the page | CODE → 🧭; «Whiteboard of these changes» in CODE |
| scratchpad | sketching how existing code works, when there is no change | the agent | CODE → 🧭 |
| canvas | free visuals that are not about code: charts, pictures, pages | the agent | Canvas tab |

`add_note` refuses any other note in a project folder and `add_task` any eighth
open task. The parts talk to each other one way only: a whiteboard **reads**
`rules`, `spec` and `future` and quotes your own words as its requirements; a
truth the change alters is fixed **in `spec`**, something the review uncovers
goes **to `future`**, never to the board unless you ask; and when the agent
finishes a whiteboard, the project's `journal` gets one line pointing at it.
So the life of a change is: you ask → the agent reads the project's notes →
works on the board → corrects `spec`, notes what is left in `future` → the
`journal` says what happened → the whiteboard lets you see and judge it,
anchored to the lines that did it.

**The chat has tabs.** Each one is a conversation with its own context, its model
(`/model`), its judge (`/qa`), its code project, its `/verify` and its `/goal`.
Turns are serialized per tab — talking in one does not interrupt another's work;
what you type while a turn runs waits in the tab's queue, shown above the box,
where a click on a line takes it back to edit and ✕ drops it —
and the session is stored encrypted and synced. The ⏰ CRON tab is a special case:
a singleton, and it only exists from the moment a cron fires or you open it.

## The 🏛 COUNCIL — several models, one matter, and a vote

It is not a tab. It is a **page of the chat**, reached with 🏛 in the tab strip,
next to 🤖 and `</>`, so it shares the chat's input, `/commands`, history and
loop. What you type with the page open does not go to the chat model: it goes
to the bench.

**It is not about code, nor about anything else in particular.** A decision,
a text, a plan, a diagnosis, a doubt, a purchase, an argument. Nothing in the
prompts names a subject, so nothing slants the deliberation toward one.

**You pick who sits and who starts.** The bench is your model pool, plus `base`
— the model this chat is already talking to, which is as entitled to a seat as
any other. It is stored per TAB, like the project and the pin: one conversation
can keep a bench of two for one kind of question while another keeps a
different one. Marking somebody as «starts» seats them; choosing an opener who
is not sitting is a contradiction the page resolves instead of reporting.

**The round**:

1. the first member **answers** in full;
2. the others read question + answer and **examine** it on two criteria — are
   there errors in it? can it be done better? They judge **independently**: none
   reads another's critique first, because a panel whose second voice is
   anchored to the first is a panel agreeing with itself. They run at the same
   time, which is a consequence, not the reason;
3. the first member reads the feedback and **reformulates** — the whole answer
   again, not a patch, holding its ground where a critique was wrong;
4. the others read it and **vote**: *placet* or not.

Every member is a full sub-agent with its tools: one that cannot check a thing
is one giving an opinion.

**The reviewer stays on inside every member.** The council and the quality reviewer do different jobs and neither replaces the other: the members judge the content against two criteria, while the reviewer refuses an answer that is not an answer — a turn reporting on itself, a pasted record — so a status report never reaches the bench as the first speaker's answer.

**Nobody sitting has read your conversation.** Each member receives the matter framed explicitly as somebody else's request, so no member mistakes an earlier turn for its own history and replies that it already answered. With the page's context tick on, the tail of the conversation goes in as a briefing, shown on the page before anyone speaks, and the whole of it sits behind `search_conversation` for any seat that wants to look something up; untick it and the seats come in clean, with the matter alone.

**A split vote does not silently repeat.** The council stops and asks: another
round, or leave it here? Spending more models is the one thing here that is
nobody's to decide alone. With nobody watching — a cron at four in the morning —
it is not asked at all: one round, and **the disagreement comes back in writing,
signed by whoever holds it**. A council that hides its dissent is a council that
agreed with itself.

**What comes back is not only the verdict.** The answer goes on screen; the
deliberation — who examined it, what they argued, who voted and why — goes
into the conversation's context, the same split `/multi` uses. Ask «and why
did that one not accept it?» a message later and the chat knows, instead of
the arguments having lived on a page it never sees. The answers themselves
are not duplicated into it: the final one is right there, and a superseded
draft was replaced on purpose. The page keeps everything, whole.

**The model can convene it too.** `call_council` is always in the model's tool
list from the first turn, never left for the model to discover, because it is the
tool you reach for precisely when you are stuck. The model stands where you
stand: it states the matter and names who sits. It cannot convene a council from
inside a council (nor from a sub-agent), which would leave a round waiting on the
round it started.

**What it costs.** One call per member per phase; a full round of three seats on
local models takes minutes. What rides into the conversation's context is the
deliberation without duplicated drafts, not the whole transcript. Two knobs,
both in `[CHAT]`: `council_max_rounds` (4) is a ceiling on the gate, not a
policy, and `council_gate_timeout` (900s) is how long it waits for your decision
before stopping and handing you the disagreement.

**When a turn is stuck, it is offered.** The model can convene a council on its own with `call_council`. With two or more models and the tool not blocked, the metacognition's first question in a stall offers it explicitly: tell them what you are doing, what keeps happening and what you have, and ask whether to stop or what else to try. It is offered, not forced, because the question alone often unblocks the turn. One model talking to itself is no council, so with a single model it is not offered.

Conversations save themselves. 💬 in the tab strip lists them, newest
first, each with its size and date: Restore opens one in a new tab, Delete
removes it from every machine, and the bar above deletes by date. The star on
a card keeps that conversation at the top of the list whatever its date, on
every machine of yours, and the prune leaves it alone: the one with the rules
you keep coming back to never sinks under a thousand passing chats.

## ◎ The machine beside the transcript

◎ in the tab strip opens an instrument beside the transcript that shows what
the active tab is doing as it does it. A dial: every tool the tab has used
is a slot around the arc with its count, the model's own rounds the first
slot, and the needle swings to whatever is running now, with its arguments
and its clock under the dial. A belt: every call is a box that arrives from
the right and pushes the older ones left, grows while it runs, settles into
the lower lane when it ends, turns red when it fails, and wears the
sub-agent's name when a sub-agent made it. A call lands once, repeats fold
into their slot's count, and when nothing runs nothing moves and no frame is
painted. Its width and whether it is shown are remembered.

## The CODE tab — a real repo, not a chat about files

Opening a project is not «picking a folder»: it is entering code mode. The path
travels in every turn of that tab (`[CODE MODE]` with the path, to the model), and
**opening IS creating** — opening knocks on `code_index`, which gives birth to the
project's memory — a Notes folder named after the directory, bound to the
checkout, with four notes: `rules` (your standing orders, which the model reads
and does not write), `spec` (settled truths in short lines), `future` (what is
pending) and `journal` (what happened, dated) — plus its `skill: …` notes and a
same-named board of at most seven open tasks. While `spec` is still the unfilled
template, the model is told to build it by looking at the repo, the README and
the distilled `AGENTS.md`. The four ride a conversation from its first turn, and
whichever changes during it — written by the model or edited by you — reaches
the next turn as it is now. Notes titled `project`, `spec: …` or `change: …`
are folded into `spec` and `future` when the project opens, without losing
content. The model cannot create a note with any other title there; you can,
from Notes, and such a note is yours: it rides with the four, under its title
and id, read as coming from you (a request left there gets done), and the model
may edit or delete it but not rename it into a new title.

**The left bar: one view at a time**, and pressing the active icon again folds the
side panel away and gives you the code full screen.

| View | What it does |
|---|---|
| **± Changes** | What has been touched. Diff per file, **per-hunk reject** (keeping part of a change), undo, and the commit — with its message, and on `main`/`master` it **branches first**. The «only mine» checkbox separates what HOPLITA did from what was already dirty in your tree; unchecked, the commit ALSO takes what you edited by hand |
| **📁 Files** | The project tree (lazy: walked the first time you look at it). Ctrl+P opens by name |
| **ƒ Symbols** | The open file's outline: the LSP's real hierarchy when there is one, and groups with headers when there isn't; glyphs and colour per kind. Cached in the MCP by `(path, hash)` |
| **✓ Verify** | **The VERIFY page**, below |
| **🕘 History** | The last commits. Two questions per commit and a checkbox picks which: what that commit changed, or what has changed between it and now (the interdiff you read before saying «this is where it broke») |
| **🔍 Search** | Search the whole project and jump to the line (Ctrl+Shift+F) |
| **🤝 Co-work** | Share this project with a contact and see what arrives from them. **Its own page**, below |

**The diff is a document, not a twin panel**: it shares space with the open files
and is picked with a tab (draggable, with a ● when dirty). The editor has
find/replace, live diagnostics (pyright + ruff + go vet + tsc + cargo; when a
tool is missing it says so, with the command to install it), Ctrl+S to save,
and per-file undo that survives switching tabs.

**Checkpoints**: every edit the model applies is reversible. The diff you see by
default is *since the last checkpoint*, and if the repo no longer has them (you
recreated it) it tells you instead of showing you something else.

**The model knows its hooks.** The code-mode block tells it the contract: what
is being done now → the board (`add_task`, moved with
`update_task_progress`); a settled truth → `spec`; what is pending → `future`;
what happened → `journal`. And the twin task project carries a 💻 badge so you
can tell which one belongs to the repo.

### The VERIFY page — a command, what you expect to see, one button

`✓ Verify` is not a column in the side panel: **it takes the whole tab**. The diff
and the editor live on another page, so nothing that repaints them can evict you
from a run in progress.

- **Command**: the check that says whether the work still holds — `pytest -q`,
  `make -s`, `go test ./...`. It is the same value `/verify` sets, and the one
  the agentic loop uses as its definition of done. Enter runs it.
- **What you expect to see in the output** (optional): what the exit code cannot
  answer, in words — «no errors», «throughput above 2000». A model judges the run's
  output against it, so green means exit 0 AND the bar met, with the why said.
  Nothing pretends to measure; nothing is parsed. Full guide:
  [`docs/DEFINITION-OF-DONE.md`](DEFINITION-OF-DONE.md).
- **▶ Run**, and under it the verdict (a banner that does not scroll away) and
  the whole output.
- **Before**: one list of what ran in this project, newest first. A click fills
  both boxes and brings that output back **without re-running anything**.
  Running a command is what remembers it; there is nothing to press.
- **→ Send it to the chat**: fills the chat box with the command, its code and its
  output. It fills, it does not send: asking for the fix is your call.
- The work goal in words — what «done» means when no command can check it — is
  `/goal` in the chat, not a box on this page.
- A check the **firewall** refuses to run is «not checked», never «still failing»:
  said once, no retries, the turn ends on the model's answer.

### The CO-WORK page — sharing a repo, and leaving

Co-work is a view of the code tab, because sharing a repo is part of working on
it. Inviting sends the current branch as an **end-to-end sealed git bundle**
(the server relays opaque chunks it cannot read) and shares the memory folder
and task project alongside, and with the task project its whiteboards. **Whoever accepts chooses where their copy lives**,
which is what makes an invite an invite. From then on commits flow both ways
into `refs/hoplita/incoming/<peer>/<branch>`: **nobody's trunk moves without
their own hand.**

The page shows the three things you need at once: who it is **already shared
with**, who you can **invite**, and what is **coming in** — with ✓ *fully
arrived* apart from ⏳ *still in parts*, because «nothing arrived» and «it is on
its way» are different answers. Select what came in and **🧭 Whiteboard** explains
it before you take it: the whole branch, from where it left yours to its tip, on
a whiteboard named for who sent it (pressing again opens it; a new push from
them is a new whiteboard). Plus the two manual verbs (bring in / send
mine) for when you do not trust the background engine yet.

**And a way out**: `✕ Stop sharing` ends the co-work with one peer or with all
of them — they stop receiving your commits and their packs stop being applied
here. What it does not do is stated in the confirmation itself: **their clone
stays theirs.** Code that was shared cannot be taken back.

## The ⏰ CRON tab — watching the crons work

A cron does not write into your conversation. It lands in its own chat tab, which
**is born when needed** (on the first fire, or when you open it) and rehydrates
with the historical output below the live one.

- **Live board** of the execution, with a filter by kind.
- **Its own model per job** (`auto` / `base` / a pool alias): the pin belongs to
  that call and never writes the persistent context.
- **A scoped supervisor**: typing there does not launch a loose task — it offers
  you the gate (create it as a cron / open it in a normal tab).
- **👎 with a comment regenerates the job**, it does not just complain.
- **Self-protection with a voice**: if a job disables or deletes itself you get a
  🚨 warning instead of a silent disappearance.
- A cron does **not** inherit the `/verify` or `/goal` of your chat tabs: the job's
  instruction is its own contract.

## Workers — a job with a tab of its own

Tick **Its own tab** on a cron job (or ask for it: «watch my inbox every ten
minutes, in its own tab») and the job becomes a worker:

- every run is a turn of **its** tab, named after the job and marked ⏰, so its
  work reads as one conversation instead of lines in an execution log;
- it runs **in parallel** with your other tabs and with other jobs, and it
  never takes the view from the tab you are in;
- you can **open it and talk to it** between runs, with everything it has
  done in view; a run that comes while you are talking to it waits its turn,
  and a run that falls due while another still waits is not queued twice;
- every run gives it its task again, as it stands, and tells it to go on
  from the conversation, so what you told it in between counts («you missed
  the invoices, keep going»);
- tell it in its tab to change what it does or how often («from now on also
  file the receipts, every 7 minutes») and it changes its own job
  (`update_cron`); the next run follows the new task;
- each run is an ordinary turn, so it has what any tab has: tools, delegating
  to other models, the council, children;
- the tab is saved as it works; close it and the next run reopens the same
  conversation. With **On start** it opens and gets to work when HOPLITA
  starts.

Jobs run while HOPLITA's window is open: the scheduler lives in it.

## Tabs that show they are working, and ▦ the overview

A tab with a turn running wears a turning ◐ before its title and its
background breathes, so a tab you are not looking at still tells you it is
busy; ⏸ in amber is a tab waiting for your OK.

**▦** at the right of the strip (it reads `▦ 2/5` when two of five tabs are
working) opens every tab on one page: its state and for how long, the job it
works for and its next run, its goal, the last thing it said, and a tree of
its work the way a process tree reads: the turn with its tool calls and their
arguments (each ticked with its clock when it ends, the running one turning),
the plan it keeps and the item it is on, its sub-agents with where they run,
their model, their own calls and their own list, and the background jobs
alive for it with their last probe. A ✕ on the turn stops it, a ✕ on a job
ends it. A card opens its tab. The tree moves by three rules: a call lands
once (its line enters with a band of its colour that fades), a finished
call's mark settles once, and the running mark is the only thing that keeps
turning; when nothing works, nothing moves and no frame is painted.

A tab can also leave work running and go on, or end its turn and be woken.
The model starts any tool in the background (`background_run`: a test suite,
a build, a slow fetch), watches a file, a command or a tool for a change
(`background_watch`), and runs shell commands outside the shared terminal,
several at once (`exec_command`); you do the same from the keyboard with
`/watch`. Their reports come back to the conversation that started them —
into its running turn, or as its next turn, painted as the job speaking —
never to another tab. An idle tab with jobs alive wears 👁; `/watch list`
shows them, `/watch stop <id|all>` ends them, as does closing the tab, and the
model has `background_stop` for the same, so saying so in words works too,
mid-turn or not. Stopping the turn (■) does not end its jobs: they belong to
the conversation, not to the turn, and report when they land. `[CHAT]
max_threads` caps how many may be alive at once.

Work in one tab never takes the screen from you. The canvas comes to the
front only for a canvas you open, or for what the tab you are in draws while
nothing is half-typed in its box; anything else (another tab drawing, a
remote session's canvas) leaves you where you are and makes the Canvas tab's
icon glow amber, its tooltip naming the tab that drew, until you open it.
A task finishing in another tab does not move your view either.

## Timeout doctrine — no total clocks

A timeout measures **one action**, never a job. If an action overruns its time,
that action is retried or dropped; the turn goes on.

- An expired delegated tool returns an error result and **the turn continues**.
- An LLM call is tried up to 3 times with backoff, so one network hiccup does not
  end a long run.
- The MCP emits a heartbeat every 30 s inside the stream, so the client's read
  timeout does not bite long, silent actions.
- The Go CLI has no total timeout on the turn stream; it watches the time
  between events instead.

A turn only stops for: its answer (with a green check when one is set), its
iteration budget, or your stop.

## Legibility — the zoom

`Ctrl+wheel`, `Ctrl+±` and `Ctrl+0` govern the size **live**, and `[CHAT]
font_size` sets the base. The adjustment is stored per machine (it does not travel
in the config). It reaches the chat, the diff, the run console, the history and
the code blocks: the zoom is applied as a stylesheet, and reading surfaces carry
no font size of their own, so nothing overrides it.

## Federation (optional)

Step-by-step procedures (server, identity, sync, contacts, sharing, children): [FEDERATION.md](FEDERATION.md).

A portable identity (`fed_identity.json.encrypted`), contacts, and sharing by
folder: notes, code projects, appointments, skills — with explicit acceptance on
the other side. What is public can be **clicked**: «🌍 Make PUBLIC» in the sharing
submenu and «🌍 Public folders…» in the contact's menu, with one-click
subscription. Presence 🟢 is a flag **and** a heartbeat (TTL 180 s): a contact
that is off stops showing green.

**A contact's model in your pool.** A model marked as lendable (`lend = true`
on its `[MODEL:…]` line) can be added by a contact you gave the «Models»
permission: a pool line of theirs with `via = your handle`, no URL and no key.
Their requests reach your machine sealed inside the federation, your HOPLITA
asks the model, the answer goes back the same way. Nobody opens a port,
nothing is handed to a VPN or over SSH, your key never leaves. You see every
use (the federation line, a notice in the chat, a row on the meter) and take
it back in one click; their side leaves the model out while you are offline
and never sends a local-only conversation to it. On their side it is a model
like any other: `/model` pins it, the router may pick it, a council may seat
it ([FEDERATION.md](FEDERATION.md), *Use a contact's model*).

**How shared things stay in sync, and what it costs the server.** Your edits
to a shared note or task leave on their own two seconds after you make them,
and each member receives only what changed since the last time it received
anything. Arrivals come the other way by push: the server rings your client
through its WebSocket the moment something lands, and the client collects it
at once — typically two to three seconds end to end. A full sweep still runs,
as a safety net, every five minutes. When the socket is down the client falls
back to asking the server itself: one look at the mailbox's headers every few
seconds, each step only if its kind of mail is there, bodies downloaded once.
An idle client costs a server about one request a minute with the socket up
(about a dozen with it down).

**Your own machines.** Everything that is yours (notes, tasks, appointments, crons,
credentials, facts, lessons, experience, sessions, canvases, whiteboards) travels between your
machines as one encrypted snapshot of the whole state, sealed to your own key:
the server stores it and cannot read it. On a pull, each row keeps the newest
version, by its date of change. A deletion leaves a dated **tombstone** that
travels too, so the other machines delete their copy instead of sending it back.
Tombstones are dropped after 60 days, and a laptop can sleep in a drawer for
longer — so each machine also remembers which rows the server held at its last
sync, and how each looked. Back from the drawer, a row the server had and no
longer has, untouched here since, was deleted elsewhere: it goes, with no
tombstone needed. What the laptop wrote while away is kept and pushed, and an
edit made while away beats the deletion. Nothing is dropped on the word of a
snapshot from an older client that does not sync that kind of row, of a folder
locked on the other machine, or of a server restored to an older snapshot.

**What the server cannot do.** It holds everybody's public keys, so nothing it
says about a sender is taken on its word. Who your contacts are, their keys and
what each may do (trust level and permissions) are signed by you into each
contact's attestation; a contact planted in the server's database, or one
raised there to «auto», is a stranger. Everything that travels — folder and
project content, a folder's dissolution, code, notes, skills, a child's pack,
appointments, session turns, your own snapshot — is sealed by its sender,
says inside who wrote it, to whom, about what, when and with a one-time
number, and comes in by one door: opened only with the key you signed for
that sender (your own, for your machines), taken only if its sender has the
right to that very folder, project or session, and never twice. A snapshot
you did not seal, or an older one than you already have, is not merged. A
HOPLITA from before this scheme (federation protocol 4) cannot share with
this one, and a server set to require it tells such a client in words to
update ([FEDERATION.md](FEDERATION.md)).

## Children: local packs and remote nodes

There are two kinds of child. An **emancipated** child is a headless node on
**another** machine (or in a container on this one), with its own identity on
the server. It boots from a single-use token that carries its model, sealed so
the server cannot read it: one command, nothing typed.

An **unemancipated** child is a personalisation **pack** that runs *here*, as a
sub-agent. No identity, no keys, no quota, no envelopes, nothing to enroll — it
works with the server down and with no federation at all. The pack is its name,
its role, its model, its capabilities, the note folders it may look at, its
skills folder and its avatar.

- **It shows up as a contact.** The contacts list is a fusion — the server's
  contacts plus your local children, marked 🧒, with no presence dot and no
  verified badge, because there is no server for them to be on. With no network
  the Contacts tab still lists them.
- **You teach it by talking.** «give my child MarketingManager the sales-notes
  folder to search, sales-skill as its skills folder, and reinforce its role as
  an expert» → the model calls `child_teach` with named fields and **answers
  with the diff**. `role_append` adds to its role (the normal way to reinforce
  it); `role` replaces it, and only when you ask for a rewrite. Lists come in
  two flavours: `add_notes_folders` / `add_projects` / `add_capabilities` add,
  the plain names replace. There is no free-text rewrite of the whole pack.
- **You give it work like any other node**: `fed_ask_agent`, a plan's
  `target: "child:<name>"`, or a CRON job. All three roads resolve in the SAME
  place, and a local child has no presence gate — a cron aimed at a remote peer
  fires only when that peer is online; one aimed at a local child always fires.
- **It can think and it can split work — with one line between the two cases.**
  The intent list is in EVERY profile (thinking is not a capability: it calls
  nothing and it is what you watch live). Orchestrating is not: a LOCAL child
  never does it, because its plan would fan out inside yours, which is
  recursion. A NODE wearing the pack does, on any profile — its plan runs on
  its own machine, so specialising a node does not take that away. And a pack that declares `projects` gets the task
  tools even on a narrow profile: declaring the scope IS asking for the
  capability. Creating or deleting a project never comes with it.
- **Its scope is a real boundary**, not a line in its prompt. Two lists: the
  note folders (the note tools refuse outside them, reads *and* writes) and the
  **task projects** — it sees the pending tasks of those, does them and updates
  them, and the plate it reads is filtered to them. Every refusal names what it
  CAN reach, because a sub-agent told «not allowed» with no list retries until
  its iterations run out. Its memory, though, is the **global** one: facts and
  lessons are not sliced per child, so every child draws on everything you have taught HOPLITA.
- **The planner knows what each one is FOR.** Children are offered to it in a
  block of their own, each with its role and its scope — a pool model carries
  its `profile` for the same reason. Given a task with a security half and a
  poetry half, it aims the first at a security child and keeps the other local.
- **Its whole pack is editable on screen** — right-click it in Contacts → «Edit
  its pack…»: the role as a paragraph, the capabilities as checkboxes, and the
  scope as checkboxes over the folders that really exist (typed free-hand, a
  scope is one typo away from silently matching nothing). Talking to it still
  works and still APPENDS; the screen shows you the whole thing, so what you
  save is what it becomes.
- **Your family comes with you.** A pack rides in the device snapshot, so it
  appears on your other machines and works there with no extra setup.
- **Emancipating one is ONE FIELD**: «where it runs». Empty = here, as a
  sub-agent. A federated child's handle = there, on that node — the pack is
  pushed to it, sealed, and the node wears it: its role and its tool
  profile; it answers with the node's own model. `--spawn` still creates a **generic** node (a box is not born
  knowing); specialising it is writing its role. Saving again re-pushes, which
  is what overwriting means, and picking «here» again brings it home. There is
  only ever one copy of the pack.
  A node accepts a pack from **its owner and nobody else** — that envelope
  rewrites who the agent is, and from a contact it would be prompt injection.
- **Careful with what changes when it moves out**: a local child reads your
  vault directly and its scope is a *filter* — nothing is copied, nothing
  leaves. A node is another machine: giving it your notes means **sharing** them
  (`fed_share_folder`), so a copy travels and unsharing does not un-copy. And a
  folder you have marked as skills **arrives as skills**: your own child
  applies the mark by itself, a friend is only offered it (their context budget
  is theirs).

Worth knowing before you build three: turns to a remote peer serialise per peer,
but local children are sub-agents — three tasks to three children are three
turns at once, competing for the same GPU.

## Skills are a SET of folders, and the index has a ceiling

Skills are not one fixed folder. The default one (`[BROWSER]
default_skill_category`, `skills`) is always in, and `skill_folder` marks any
other folder in or out — a folder a contact shared, a public one you
subscribed to, a project's own — so know-how that arrives is visible to the
skill picker. The marks live on the notes blob, so they travel to your other
machines.

**The index rides in the context of every turn.** At ~91 chars per skill, 500
skills are ~12,600 tokens on every single message. Past `[BROWSER]
skills_index_budget` (6000 chars; 0 = no ceiling) the index stops being LISTED
and starts being SEARCHED — by words, weighting the rare ones, no model and no
server — and it **says** that what travelled is a selection. An index silently
truncated would teach the model that the right skill does not exist.

A folder shared with you **arrives as skills** if its owner had it marked.
Your own child applies the mark by itself; a contact only gets it OFFERED,
because that index is their context budget to spend.

## The canvas is not a one-way mirror

`render_html` hands back what the page said: its JavaScript errors come back in
the tool result (`js_errors`), in the same round trip. A model that cannot see
whether its page threw on line one can only write effects that cannot be wrong.
Two more tools reach into the LIVE page:
`canvas_eval` runs JS in it (build the base once, then grow it, fix it, and ASK
it where things are) and `canvas_see` takes a screenshot the model can actually
look at, so it can judge what numbers cannot — composition, overlap, whether
the scene reads as intended (it needs a vision-capable chat model).

## Pictures — made by a picture server, shown on the canvas

Ask for a picture ("make me a picture of…", or attach one and ask to change it) and the agent calls `generate_image` / `edit_image` against the server in `[IMAGE]`: any endpoint that speaks the OpenAI Images API — OpenAI, xAI, LocalAI, or stable-diffusion.cpp's own server with a local model such as Qwen-Image. `base_url` is the server **up to `/v1`** (`http://host:port/v1`); the tools add `/images/generations` and `/images/edits` themselves.

- The picture is saved under `<HOPLITA_HOME>/media` and lands **on the canvas** as a figure; several pictures of one turn stand side by side. The chat gets one line naming the file, and a picture the model writes into its answer becomes a link that paints it on the canvas — the transcript never carries bitmaps.
- The canvas loads the file from disk by URL (no size limit, nothing embedded), and the kept canvas refers to the file.
- **Save as…** on the canvas bar copies the pictures to a file (one) or a folder (several), or saves the page as HTML when there are none. **💾 Save** keeps the canvas as a snapshot inside HOPLITA.
- The model gets the path, never the bytes; `see_image(path)` lets it look at a picture when it must judge it, and `render_html` composes around it.
- Video generation is out of scope.

## Word, Excel and PowerPoint — read, edit, shown on the canvas

`office_info` reads `.docx`, `.xlsx` and `.pptx` (and their macro twins `.docm`, `.xlsm`, `.pptm`): a document's paragraphs with their style and its tables, a workbook's sheets with values **and** formulas, a deck's slides with every shape and the speaker notes. `find` says where a text lives. With `view=true` the whole file is painted on the canvas as one page: the content is faithful, the layout approximate.

`office` edits with a **pipeline of steps** applied in order (replace keeping the format, paragraphs, tables, cells, formulas, rows and columns, sheets, slides, notes) and writes the result to `output`, or over the source to edit in place.

- **Nothing half-written.** The result is built in a temporary file next to the output; a step that fails writes nothing.
- **Nothing silently lost.** Every write measures what the library cannot carry (the source opened and saved with no step, its parts counted by kind). If something would be dropped it is named, and the source is not overwritten without `accept_loss`.
- **A backup.** Overwriting a file keeps the previous one in `<HOPLITA_HOME>/office-backups`.
- **Formulas.** Moving rows or columns, or renaming a sheet, does not rewrite the formulas that point at them, and the result says so. A formula written here gets its value when the file is opened in Excel or LibreOffice.
- **Formats refused by name.** Legacy `.doc`, `.xls`, `.ppt` and OpenDocument files are refused with a hint to convert them; a password-protected or damaged file is reported as such.
- **Attachments and folders.** An office file attached in the chat, or found by `read_folder`, is read as its text.

The three libraries are plain `pip` packages with prebuilt wheels for Linux, Windows and macOS (Apple Silicon and Intel): no compiler and no LibreOffice. On Windows, a file open in Word or Excel is locked: `office` reports that it could not write and the original stays untouched.

## What your contacts DID to what you share

A per-contact timeline: «alice · spreadsheet · 4 commits», «added 4 notes».
Atomic events on write, grouped on read, painted in the contacts tab
(`fed_activity`, `fed_activity_clear`).

It costs the federation nothing new: every fact it needs already crosses the
wire, and the pulls record WHAT they applied as they apply it. No new envelope
type, no new privacy surface. It rides in
the device snapshot because an event is born on whichever machine collected the
envelope, so without that each machine would hold a different HALF of it.

## Sharing code ASKS before it builds

The order is **offer → weight → my quota → accept → then the bundle**
(`fed_code_state`). Nothing is built into the recipient's mailbox until they
accept, so no quota is spent on a parcel nobody agreed to, and no half-sent
transfer leaves orphan parts behind. Accepted offers are served and arrivals
materialised in the background, every 90 seconds.

## Working memory: what a conversation digested, kept whole

When a tool result is too big for the context it is **not amputated**. The
model reads it whole in a side-call and the conversation receives a dense,
data-first extract; the FULL text is kept as a note in that conversation's
**working memory**, and the extract points at it. The inventory of it rides in
every turn, so the model knows what it is holding and does not re-fetch the web
for something it already has.

An extract is a selection, and nobody reads a whole text they only have the
extract of. So the store is **searched**, not reread:
`search_working_memory(query)` takes any words, a name or a figure and returns
the passages that mention them, with the paragraph before and after and their
line numbers, ranked by how many of the rarer words each covers. It is the
one engine every text search uses (`find_passages`): notes, attached
documents and the working memory answer with the passage where the words are,
never with a text's first lines, and the conversation log's moments are the
same grouping with time as the axis. `regex=true` makes it grep, `title=` keeps
it to one text, and `read_note('working-memory', <title>, start_line,
end_line)` reads around a passage. The tool always travels with the model,
and a JSON result is stored indented so it has lines to find.

A sub-agent of a plan has the same door, and one more. It is born from its
brief, the planner's summary of a conversation it never saw, and a summary
loses: a figure said three turns earlier, a condition the person put once.
Carrying the whole conversation into every sub-agent keeps all of that at the
price of the whole prefix per sub-agent, a full prefill each on a local
server. So the sub-agent searches instead: `search_conversation(query)` looks
inside the messages the turn put in front of its model, and
`search_working_memory` inside the texts that conversation digested; its
prompt says the brief is a summary, to search before assuming, and that the
conversation wins over the brief. A plan typed as `@multi` sends the
conversation with it, as any turn does, and so does a council convened from
its page with the context tick on; where nothing is filed behind a turn, no
door is promised.

Three rules it lives by:

- **Nothing is cut on the way in.** If the tool returned 100k, that is the
  output: `[EXA]`/`[FIRECRAWL] max_chars = 0` by default. A cut at the provider
  destroys the text before the store, the RAG or a later `read_note` can hold
  any of it.
- **Everything is read.** A result longer than one pass is split on paragraph
  boundaries and EVERY part is digested; the extracts are joined and the reply
  opens with «digested in N parts — ALL of the result was read».
- **It belongs to its conversation and dies with it.** Reset the chat or delete
  the session and the working memory goes too. It is not a global bucket. A
  note carries every id its conversation answers to (the tab's id and, once
  saved, the session's uuid), and a reset forgets all of them.

  **A note with NO conversation tag belongs to nobody.** It is never injected
  into any conversation, and orphans are deleted
  (`working_memory_forget` with `orphans=true`), not adopted.

  The folders the app fills for itself — `working-memory` and `cmd_log` —
  sit on their own shelf in the Notes tab (`⚙️ FILLED BY HOPLITA`), each one
  saying in its tooltip what it is and when it goes. Nothing is guessed onto
  that shelf: a folder one of your crons writes is your folder, and you can
  move it there yourself with a right-click.

  A compaction summary is not a note: it is a row in the conversation log,
  under its own role, next to the verbatim messages it summarises.

The knob that decides whether the distiller runs at all is
`[AGENTIC] tool_result_max_chars` (8000). Raising it trades context for latency
— on a 262k model that is often the better trade, because the distilling pass
is what costs the minutes, not the search.

## Memory: it keeps itself

A memory that only grows stops being a memory: facts said twice, a city you
left still riding next to the one you moved to, an errand from one afternoon
kept as a fact about your life, and past a size the newest ones no longer fit.
It is kept the way a person keeps what they know — by updating it as it
arrives, not by pruning it once a week.

- **The write gate.** Every new fact — from the agent's `add_fact`, from the
  conversation distiller, from the page — meets the stored facts that overlap
  it before it is written. Code finds them by words; one call judges: **new**,
  the **same** (it reinforces the one there), an **update** (the stored fact is
  rewritten with both, its old words kept in its history) or a **newer truth**
  (a move, a new job) that **archives** the old one. A fact
  about something new costs no model call; a model that is down never loses a
  fact. The distiller is shown the facts related to what it is reading, not the
  fifty oldest, so it stops extracting again what is already known.
- **Live and archived.** An archived fact stays in the store — it travels to
  your other machines, it is never injected, it comes back with one undo.
  Nothing in this system deletes a fact on its own.
- **Emptying the archive is one button.** Memory → Facts has two views, **Live**
  and **Archive**, each with its count; the archive says how many facts it
  holds and what they weigh, and **Empty archive…** deletes them after one
  question — live facts untouched. The deletion reaches your other machines on
  their next sync (every deletion leaves a dated tombstone), and a curation
  change that touched a deleted fact says it can no longer be undone instead of
  bringing it back.
- **Every fact has a life.** *Permanent* (who you are: name, family, IDs,
  your machines' addresses), *relevant* (something ongoing: a project, a trip,
  a purchase) or *volatile* (one moment: a search, a troubleshooting step).
  The extractor gives it when it writes the fact; an hourly clock gives one to
  the rest. Memory → Facts shows it beside each fact, with the days it has
  left before it is forgotten or asked about.
- **A fact is printer ink.** Every word said is kept in the conversation log
  and recall finds it, so the extractor writes a fact only for who you are or
  something ongoing; a moment of a conversation (a search, a troubleshooting
  step, the parts of the device being fixed) stays in the log. Lessons ride
  every turn and are written as sparingly. A fact is a note: when the detail
  matters, the agent recalls what was actually said.
- **What reaches a turn.** Only the permanent facts ride the stable top of the
  prompt. The rest come in when the words of your message touch them, in a
  small block at the end, and the agent is told to `recall` before answering
  when you bring up something of your life that neither covers. Every log
  search result carries a marker (`at`) and `session_read` reads the
  conversation around it, the way a file is read from a line.
- **What passes, passes.** A volatile fact that has not come up for
  `[MEMORY] volatile_days` (3) is archived by the clock; a relevant one silent
  for `ask_after_days` (30) becomes a question in Memory → Proposals, «still
  on with this?»: *No longer* archives it, *Yes* keeps it and restarts its
  clock. «Come up» means stored again, returned by a search, or spoken of in
  the log. Who you are never expires this way.
- **The review applies, and can be undone.** Merging look-alikes (read in
  groups of related facts once the list outgrows one prompt), retiring what
  the conversation log shows ended, and archiving a session's errand kept as a
  fact are applied by the pass itself, each one logged in `Memory →
  Proposals` with the button that puts it back. Rules are merged and
  deactivated the same way. What the agent proposes with
  `propose_memory_change` still waits for you.
- **Pressure, not only a calendar.** The pass still runs weekly, and also as
  soon as the live facts outgrow the catalogue — at most once an hour.

**Cleaning up a store that grew without the gate** (facts imported or written
by hand, duplicates, noise). Nothing has to be done by hand, but this is the
order that works:

1. **Run the same HOPLITA version on every machine that syncs.** A client that
   does not know what an archived fact is shows it, injects it and syncs it as
   live.
2. **Run the pass**: `Memory → Proposals → 🔎 Curate memory` reviews the whole
   live store at once — facts an earlier pass judged recently included,
   archived ones never (the automatic runs size themselves to a third of it
   and leave a fact alone for 21 days after judging it). Pending proposals
   stay in that page for you to accept or reject. With many rules, press it
   two or three times: a pass merges at most four groups of rules, so a review
   stays reviewable.
3. **Read what it did** in the same page: every change, with its undo button.
   An undone change is never applied again.
4. **Look at `Memory → Facts → Archive`**: what left the live memory and why
   (superseded, merged, stale, said nothing).
5. **Empty the archive when you agree** — not before: a change that touched a
   deleted fact can no longer be undone.

It runs on `[MEMORY]`'s model («Background work» on the Models tab; empty = the
base model). Naming a different server there keeps the chat's prompt cache warm
while the memory works.

**Nothing on this path can delete.** Not a policy a prompt is asked to honour:
there is no destructive call on it, and the tests booby-trap those functions
and run the whole pass over them. A move or a new job archives the old
city or the old job; look-alikes are merged; and one undo puts a merge back.

**What counts as chaff.** Each fact is asked one question: a month from now,
will this still be worth knowing about you? Three kinds of sentence are
archived — the trace of one moment of one conversation (something you asked
for, looked up, tested or said about today, even when it names a place), a
remark about you with no content («shows interest in X»), and how you use the
assistant itself. Everything about your life stays, including plans such as
looking for a flat or a job and what you want to be called; in doubt, it
stays.

```ini
[MEMORY]
base_url =                 ; the model the distiller, the write gate and the
api_key =                  ; pass talk to; empty = the base model. A pool
model =                    ; alias in `model` takes that whole pool model
auto_review = on
review_every_hours = 168   ; weekly. It checks the clock every 15 min, so a
                           ; machine that was off on Monday gets its pass on
                           ; Tuesday instead of skipping the week
review_facts = 0           ; facts per automatic pass; 0 = a third of the
                           ; pending pool (6 to 60), so the whole store is
                           ; seen each window
volatile_days = 3          ; a passing moment is archived after this long
                           ; without coming up again (undoable); 0 = never
ask_after_days = 30        ; something ongoing becomes a «still on with
                           ; this?» question after this long; 0 = never
```

The schedule is configuration, not a CRON row, on purpose: a row can be edited
into something else, and the review must not be lost that way.

## Web search — four engines

`search_web` has a provider layer: one function per engine, one entry in a
registry. `[SEARCH] provider` picks which.

| Engine | What it gives back |
|---|---|
| `perplexity` | A written answer with citations (`kind:answer`) |
| `firecrawl` | Raw sources in markdown, the agent synthesises (`kind:sources`) |
| `exa` | Retrieval-first: the passage that answers, not the page that ranks. Sources by default; `mode = answer` has it write the answer instead |
| `tavily` | Both halves — the sources, and a written answer on top if you want it |

`kind` is not decoration: it tells the agent whether it is holding somebody
else's summary (judge it) or raw material (synthesise it, and be checkable on
the quotes).

**The trade-off.** Tavily answers in ~2.5s because it hands
back a written answer plus short extracts — less material, and the model
usually has to `scrape_url` afterwards for the actual page. Exa brings the page
text: more to work with, and big enough to cross the distiller's threshold,
which is where the minutes go. Neither is throwing anything away; they return
different things.

## What DELETE means, and what CLEAR means

Two different words for two different things.

| gesture | reaches |
|---|---|
| **Clear** (the chat's ↺) | this tab's transcript, its pinned context, its open project, its agent cards, its intent list **and its council board**. NOT the conversation log: clearing empties a context, it does not say the conversation never happened |
| **Delete** (a saved session) | the session row, its working memory, **its payload on disk** and **its messages in the searchable log**. It says it never happened |
| **Notes tab → Conversations → Forget** | the log of one conversation, or all of it. Still there for when you want only that |

How a deletion behaves:

- **A deleted session keeps no body.** Its tombstone — which exists so the
  deletion PROPAGATES on sync instead of the row coming back from another
  machine — carries the row's identity and date, never the conversation text.
- **Delete purges the log too**, so nothing of the conversation stays
  searchable after it has left the list.
- **Every deletion travels.** The storage layer, which every deletion passes
  through, records a dated tombstone for each removed row of a synced store (notes,
  tasks, credentials, sessions, …), so your other machines delete their copy
  instead of sending it back.

## The notes editor: rich on screen, plain in the store — and honest about it

One editor, formatted in the hand, markdown in the store. The invariant lives
at the door: notes are loaded with `setMarkdown` and saved through
`_note_editor_markdown()`, so the store never holds rich HTML.

**A line break you typed is a line break.** In plain markdown a single newline
is a SPACE, which would turn a note of commands, one per line, into one
run-on line. The pane renders the way GitHub renders a comment: every newline inside a paragraph is a hard break
(fenced blocks, list items, headings, quotes and table rows are left to their
own syntax). A save from the pane gives you back the lines you had adjacent,
without the blank lines Qt writes between hard breaks; a blank line you typed
stays.

**And the formatted view is only offered when it is TRUE.** What the pane
still cannot carry — indentation, backslashes, globs (`*` is emphasis to
markdown) — opens in RAW, with its exact bytes, editable, and a line saying
why; the Raw button's flip back to the pane goes through the same door. The
check is not byte equality (Qt re-wraps long prose and writes `1.  ` for
`1. `, which loses nothing) — it is whether LINES or CHARACTERS are lost.

Everything that follows from the same rule:

- **`<>` is a switch.** Press it with nothing selected and a code block opens
  right there, with the caret inside; type or paste and every line stays code;
  press it again and it closes. Enter and multi-line pastes stay inside the
  block.
- **A paste is judged before markdown gets it.** Prose is pasted as prose; text
  markdown cannot carry (indent, backslashes, one command per line) goes into a
  fenced block, which carries every byte. Same verdict that chooses the pane.
- **`Ctrl+Y` redoes**, on every platform, as well as `Ctrl+Shift+Z`, in the
  three editable boxes (notes, chat input, code editor).

## Multi-model system

HOPLITA can use **several models at once**, each on its own endpoint, assigning the most suitable one to each task. The **base model** — elected in the pool (`[MODELS] base`), or `[API]` while none is — is the default brain and has priority.

**Which models, and who uses which.** The config dialog opens on «Models»: first **Your models**, one line per model (name, model, server, whether it sees images, active, the BASE badge; ✎ opens its details, 🔌 tests it), then **Who uses which model**: the base model, whether the chat picks by itself (router), and each job that can use another model — browser and backup browser, background work (memory, curation, lessons, digesting large tool results), planner, answer reviewer, the one that takes over when stuck, the one that describes pictures for a model that cannot see them — plus the hands, said in words; then **Pictures**, the picture model on its own server. Each follows the base (or the conversation's model) until you pick another; the desktop hands always run on the conversation's model. In the INI a role names a pool alias: `[BROWSER] model = coder`. `[API]` is not shown: when the dialog opens, what a pool model inherited from it is written on that model's own line, and its model becomes a pool model. The other tabs are subjects — Conversation, Plans, Web, Search, Hands, Memory, App — not INI sections. GUI, MCP and CLI resolve all of this through one rule (`hoplita_llmcfg.resolve_endpoint`).

**Configuration:** the pool in `[MODELS]` + `[MODEL:name]` sections with a plain-language `profile` (see the config section). Adding a model = adding its section, no code changes.

**Four ways to use it:**

1. **Automatic router** (`[CHAT] auto_route_models = true`): before each chat turn, a short call (no tools, temperature 0) decides which model gets the request. The router answers a name and the code routes. You will see `🧭 Routed to «X» (auto router)` in the chat. It does not depend on the LLM calling any tool.

2. **`/model <name>`** (manual): pins the chat's model on the fly. Takes priority over the router. `/model base` pins the base, `/model auto` hands the choice back to the router, `/model` alone just shows the state.

3. **Multi-agent** (`/multi <task>` or the `plan_and_orchestrate` tool): the planner breaks the task into sub-agents, assigns each sub-task the pool model that fits best (the `model` field in the plan) and, optionally, a machine (`target`: local, `child:<handle>` or `friend:<handle>`), shows you the plan for approval, runs the sub-tasks in parallel with a dependency graph and retries, and synthesizes. Each card shows which model its sub-task uses (🧠).

4. **The council** (the 🏛 page of the chat, or the `call_council` tool): the same pool, but nothing is routed and nothing is split. YOU pick who sits and who starts, and they deliberate on ONE matter — the first answers, the others examine that answer on two criteria (are there errors? can it be done better?), the first reformulates, the others vote *placet*. Where the orchestrator asks «who is best for each piece», this asks «what do several of you make of this one thing». Its own section above.

The planner and the router READ each pool model's `profile` to decide. The clearer the profile (e.g. "for heavy CODE work"), the better the routing.

## Metacognition — thinking WHILE doing

Three layers: the **plan** (before, the orchestrator), the **verification** (after, `/verify` and the optional review) and the one in between, metacognition: the turn thinking about itself while it works.

- **Intent list (tool `intent`)**: on multi-step tasks the model sets its plan (`set=[3-6 goals]`), crosses off what it achieved (`done`) and what failed (`failed`), marks what it is on (`doing`) and REWRITES the list when the plan changes. It shows **live**: a 🧭 strip over the transcript (click = open the board), its own band in the Agents panel (the same cards with LED and strike-through, an indigo stripe: not an agent, the turn thinking) and a line in the CLI. Always available, never delegated, and above the restrictive sub-agent profiles — planning out loud grants no capability.
- **Stall watch**: never by number of iterations (three normal rounds are nothing; one browser round is an hour). By **progress and time**, evaluated between tools — it never interrupts one in flight: `metacog_stall_seconds` with no new data OR `metacog_stall_tools` sterile tools in a row → a generic nudge that **asks** (is this path producing data? another source, another angle, another of your tools?) and does not dictate. **A productive turn is never interrupted.**
- **A ladder, not a cut**: a stuck model is first **asked**. The stall watch above can ask, or the model can repeat **the same call with the same result four times**; then it is asked which assumption is failing and to try another way, and the count starts again. **With two or more models, that first question also offers a council** (`call_council`), spelled out in the question: tell them what you are doing, what keeps happening and what you have, and ask whether to stop or what else to try. The model decides; the question alone often unblocks it, and a council costs several calls. If it is still sterile **after the question** for `metacog_stall_tools` tools, the turn is **handed to another pool model** (`[CHAT] escalate_model`, «Takes over when stuck» on the Models tab): once per turn, said in the chat. **With a single model, or nobody else to hand it to,** that step is a **second, firmer question**: a truly different approach, or stop and say what blocks it. If it stays sterile after that, the turn **closes honestly**: one tool-free pass in which it tells you what it tried, what blocks it and what it would need, instead of grinding the rest of the budget. If the same call sticks four more times, the turn is cut. Any real progress clears the ladder.
- **Sterile-streak cut** (`heavy_sterile_cut`, default 3): an expensive tool (the browser) is switched off only when it comes back empty/error/repeated N times **in a row** — that source is not yielding the data that way — and the model is told in the result itself (and you in the chat: ✂). A result that pays resets the streak: if every browse pays, it can do a hundred. There is no fixed numeric cap on how often a tool may be used.

Settings in `[CHAT]`, all hot-reloaded (the GUI editor or the ini): `metacog`, `metacog_stall_seconds`, `metacog_stall_tools`, `heavy_sterile_cut`. Keys: `docs/CONFIG.md` · stream events (`intent`, `metacog_nudge`, `model_escalated`, `metacog_close`): `docs/API.md`.

## Tool firewall

- Visual panel in the 🛡 Tools tab: the general policy, by families (one tick
  takes a whole family), with search on top
- State persisted in the encrypted SQLCipher store (`firewall`)
- Blocked tools are neither exposed to the LLM nor executed
- A tool that opens the same door as another under a different name is blocked
  with it: blocking `run_command` also blocks `exec_command` (the shell without
  the terminal) and the shell probe of the watchers
- It applies everywhere: chat, CRON, CLI
- **Per tab**: right-click a chat tab → «This tab's firewall…». The same picker,
  but what you untick is blocked in that conversation only, sub-agents included,
  on top of the general list. The tab wears 🛡, the policy travels with the saved
  session. Mind the shell: blocking the web family does not stop `run_command`
  from reaching the internet with `curl` — block «System» too when the web must
  really be off
- Tools from external MCPs appear automatically, in a family of their own per
  server
- Two badges, nothing else painted: «system» for what can run commands or write
  files, «external» for what comes from an MCP server you configured

## Context and cache — the prefix contract

The backend reuses the work done on a prompt's prefix as long as it does not
change, so the prompt is arranged for that: everything static up top, everything
that moves at the bottom, and nothing above the newest message ever rewritten.

- **Tools block built once per turn, never rebuilt.** Hot schemas + the catalogue
  by name; what `load_tools` brings arrives in its result, at the bottom; a
  catalogue tool is called through the static bridge `use_tool(name, arguments)`.
  The hot set is frozen per MCP process. A tool cut for a sterile streak stays
  listed and its call is refused with a result, never dropped in silence.
- **The history is the transcript.** Tool calls, tool results, the reviewer's
  notes, rejected drafts and your mid-turn asides stay in the history exactly as
  the model saw them (`[CHAT] history_transcript`, default on). The next prompt is
  the last one plus a message.
- **The memory picture.** The first turn of a tab freezes the facts catalogue and
  the project memory in the system prefix; later turns reuse the same bytes. What
  is written during a conversation shows up in the next one, or after the tab is
  reset or closed.
- **What varies goes last**: the working-memory inventory, the per-question
  skill, and — with no conversation id — the facts and the project memory.
- **An aside typed mid-turn** (a git request in the middle of a job list, say) is framed as one:
  handled, then the original request is finished. It never replaces the task.
- **Compaction** is rare, cuts on a message you typed, and keeps a contract:
  what you asked and decided, done/pending/failed, literal identifiers, what the
  tools returned that still matters and its `read_note` locators.

## Streaming

- Non-agentic chat: token-by-token SSE streaming (`/v1/chat/completions` with `stream=true`)
- Agentic chat: non-streaming answer (it needs complete `tool_calls` to iterate)
- RAG and summaries: non-streaming answer

## Pasting files into the chat

- **Ctrl+V screenshot** → image to the vision LLM
- **Multiple Ctrl+V** → a queue of files, all sent when you press Enter
- **Drag & drop** → same effect
- Supported: PNG, JPG, GIF, WEBP, PDF, TXT, MD, CSV
- Images → `image_url` data URI to the chat model. A model declared blind never
  receives an image: when a model that sees is at hand (`[CHAT] vision_model`,
  «Vision» on the Models tab), it describes each picture first and the
  description stands in its place
- PDFs → text extracted with `pdftotext` (first 8,000 characters)
- Text → inline in the context (first 8,000 characters)

## Logging

Every action tool the GUI executes is stored as a note in the `cmd_log` category
(`[APP] log_tools_to_notes`, default `true`):
- Tool, arguments (first 500 chars), duration, result (first 2,000 chars)
- Queryable with `search_notes(category="cmd_log")`; the folder keeps the
  newest 300 entries
- Excluded from the log: `add_note`, `search_notes`, `delete_note`, `list_categories`, `set_cron_context`, `list_cron_jobs`, `alert`, `search_web`, `search_rag`, `search_experience`, `read_file`, `list_dir`, `get_credential`, `list_credentials`, `get_otp`, `get_agentic_config`, `get_todo_context`, and the code reads (`code_index`, `code_read_smart`, `code_read_range`, `code_search`, `code_deps`, `code_references`)
- A tool run from an ephemeral tab leaves no `cmd_log` note

### What the MCP's console is telling you

The server prints a running commentary on its own health. Every line below is
deliberate, and the reason it exists is that **quiet and stuck must never look
alike** — a console that says nothing while a socket is dead hides a failure.

| Line | Every | What it means |
|---|---|---|
| `[SESSION-LOOP] tick · ws=up · last catch-up Ns ago` | ~15 min, and whenever the transport changes | The federated-session loop is ALIVE. `ws=up/down` says which transport an incoming turn would arrive by; the catch-up age says how long since REST last swept for envelopes the socket may have missed. Both numbers, so «nothing is happening» and «I stopped listening» are distinguishable at a glance |
| `[MCP] fed_code_pull(…)` | 90s | Polling for code bundles a contact shared, and serving the offers you accepted. Cheap; it is a poll because there is no reverse MCP→GUI push channel |
| `[MCP] fed_notices(…)` · `fed_shared_overview(…)` | 5 min | Catch-up for server notices and shared folders that arrived while disconnected. The server already pushes these over the socket — this only picks up what was missed |
| `[MCP] fed_sync_pull(…)` + `fed_sync_push(…)` | `[MCP] sync_every_min` (15) | The DEVICE snapshot: your chats, notes, facts, lessons, cron and whiteboards mirrored between your own machines. Pull first, push after, so bringing down never steps on what the other machine did |
| `[OK] … loaded from SQLCipher` ×8 | only on a pull that MERGED | The managers re-reading the stores because something actually arrived. **If you see this on a cycle with nothing new, that is a bug** |

None of it is an error. `[MCP] tool(...) → Ns` is simply how every tool call is
timed, whoever asked for it — the model, the GUI or a timer.

## Skills

Skills = notes in the `skills` folder (or any folder marked with
`skill_folder`) that teach the LLM a workflow — recipes for tasks where the
generic answer is not good enough. HOPLITA ships none; you write your own.

- Add one: `add_note(category="skills", title="name", content="the recipe")`,
  or create the note in the Notes tab.
- Before each turn the model is shown the skills index and asked ONE short
  question: which of these is for this task? If none, none is loaded
  (`[SKILLS] ask_which = true`; `false` = only the ones you pin with
  `/skill pin`).
- The model can also fetch one by title with `use_skill`, and you can run one
  with `/skill <search>`.

## Encrypted vault

- **SQLCipher**: one encrypted database, `hoplita.db` in the HOPLITA data folder,
  with row-level writes and a key derived from the master password
  (PBKDF2-SHA256, 600,000 iterations, derived once per start)
- It stores: TODOs (appointments + tasks), notes, credentials (site + user +
  password + URL + OTP secret), CRON jobs, facts/lessons/experience, the
  firewall, saved sessions and canvases
- The only separately encrypted file is `fed_identity.json.encrypted`: the
  federated identity, portable on purpose
- The password is requested when the MCP starts (every manager shares the same one)
- It can be changed with the server running, from **Configuration → App →
  Vault** or `python/hoplita_passwd.py` (`POST /vault/password`, this machine
  only, never a tool), all or nothing: the identity file sealed under the
  new password is written beside the old one and read back, the database is
  re-keyed in one transaction with its note bodies, and the file takes its
  new seal in one rename; a copy of both that opens with the old password
  goes to `backups/` first. It is this machine's password: another machine
  carrying the same identity keeps its own
- One implementation for every store: the `EncryptedStorageManager` base class
- The database keeps rolling backups (the newest 30)
- Note bodies carry a second, content-level encryption layer inside the
  database

## Web browsing (browser-use)

- Library: `browser-use>=0.13.4` (detects LLM truncation and allows the rescue with `fallback_llm`)
- Client: `browser_use.llm.ChatOpenAI` (any OpenAI-compatible endpoint)
- `[BROWSER] structured_output = auto` FORCES the schema on the server (local or cloud): small models are exactly the ones that ramble out of a schema. `off` is the escape hatch for a server that does not support it
- `[BROWSER] use_vision = auto` follows what the model declares: it gets screenshots only if its pool line (or `[CHAT] vision`) says it sees. `true`/`false` force it
- `BrowserSession` with `viewport`, `user_agent` (Mac UA by default), `locale`, `wait_between_actions=1.2`, `wait_network_idle=3.0`
- `extend_system_message` with 6 injected rules:
  1. Prefer `click_element_by_index` over `evaluate_javascript` for UI (it pierces shadow DOM via Playwright locators)
  2. `select_dropdown_option` for `<select>`; click + wait + click for custom dropdowns
  3. `evaluate_javascript` only to READ the DOM, never to mutate the UI
  4. If an action seems to fail, wait 1-2s and re-check (SPAs re-render)
  5. Verify the final state by extracting the text/attribute before declaring done
  6. If the same action repeats 3+ times, stop and change approach
- Vault credentials injected via `sensitive_data`: the LLM sees `{site_username}`/`{site_password}`/`{site_otp}` placeholders and browser-use substitutes the real values at runtime
- Real success detection with `history.is_successful()` (not the false positive from `final_result()`)
- Returns `{result, success, steps_used, max_steps}` so the caller can act on the success flag

## External MCPs

Add one line per server to `[MCP_SERVERS]` in `config.ini` and restart the MCP;
its tools are registered next to HOPLITA's own and show up in the firewall.

Or ask: «I installed this MCP server, it starts with `<command>`; set it up». The
assistant reads its own manual (`read_my_docs`), shows you the exact line it wants to
add and, once you allow it, writes it (`edit_my_config`) and connects the server right
away — its tools are usable in that same turn, no restart.

```ini
[MCP_SERVERS]
; name = stdio:<command line>   or   name = sse:<url>
files = stdio:uvx my-mcp-server --some-flag
files_key = sk-...               ; passed as FILES_API_KEY (stdio) or as the auth key (sse)
files_base_path = /tmp           ; any other name_xxx is passed as env var NAME_XXX
remote = sse:http://localhost:9100/sse
```

- Supports stdio (subprocess) and SSE (HTTP)
- Generic env vars: `name_xxx` → `NAME_XXX`; `name_key` → `NAME_API_KEY`
- Stdio processes are restarted automatically if they die
- Process cleanup on exit (atexit)
- Media post-processor: audio/image/video URLs, data-URIs and b64 → automatic download to `/tmp/`
- Detection by content-type and magic bytes

## MCP REST API

The main endpoints (the full list, with the stream events, is in `docs/API.md`):

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Server status + tool list + counts |
| `/tools` | GET | Available tools (filtered by the firewall) |
| `/call` | POST | Run a tool: `{"tool": "name", "arguments": {...}}` |
| `/firewall` | GET | Firewall state: tools + blocked ones |
| `/firewall` | POST | Update the firewall: `{"blocked": ["tool1", "tool2"]}` |
| `/feedback` | POST | Save a post-plan experience: `{goal, steps, result, feedback, tags, duration_s, partial_good_steps, note}`; `feedback` is `ok`, `partial` or `fail` |
| `/experience/search` | POST | Search experiences: `{query, n_results}` |
| `/agent/council_ack` | POST | Release a COUNCIL blocked at its gate: `{ack_id, decision}` with `again` \| `stop`. Only ever asked when the vote came back split — never to start one |
| `/data` | POST | Direct proxy to manager methods: `{manager, method, args, kwargs}` (used by the GUI to manage data without going through the LLM) |
| `/facts/list` | GET | Every fact with its `status`, plus `total`, `live`, `archived`, `archived_bytes` |
| `/facts/add` | POST | `{text, tags}` — through the write gate |
| `/facts/purge` | POST | `{ids}` deletes those; `{archived: true}` empties the archive; `{all: true, confirm: "DELETE ALL"}` everything. Deletions leave tombstones |
| `/memory/review` | POST | Run the memory pass now: `{limit: -1}` reviews the whole store. Returns what it `applied` |
| `/memory/autolog` | GET | What the pass did on its own, newest first |
| `/memory/autolog/{id}/undo` | POST | Put one of those changes back |

## Complete config.ini example

```ini
[APP]
language = en

; The literal base: used while no pool model is elected with [MODELS] base
[API]
base_url = http://localhost:8080/v1
api_key =
model = my-model

; Multi-model pool: one line per model, `alias = true|false`
[MODELS]
base = general
general = true
coder = true

[MODEL:general]
base_url = http://localhost:8080/v1
api_key =
model = my-model
vision = true
profile = Generalist with vision: chat, image analysis, web browsing.

[MODEL:coder]
base_url = http://localhost:8081/v1
api_key =
model = my-coder-model
profile = Heavy coding, refactoring, advanced tooling, long and precise
  code generation. Prefers technical programming work over chat or browsing.

[CHAT]
context_length = 30000
timeout = 1000
agentic_max_iterations = 20
auto_route_models = true
planner_model =          ; pool alias; empty = the conversation's model
escalate_model = auto    ; who takes over when a turn is stuck

[BROWSER]
model =                  ; pool alias; empty = the conversation's model
headless = false
viewport_width = 1024
viewport_height = 768
locale = en-US
use_vision = auto
wait_between_actions = 1.2
wait_network_idle = 3.0
max_steps_default = 30
max_actions_per_step = 3

[IMAGE]
base_url = http://localhost:8090/v1
api_key =
model = my-image-model

[MEMORY]
model =                  ; empty = the base model

[MCP]
url = http://127.0.0.1:9077
enabled = true

[RAG]
chunk_size = 25
chunk_overlap = 5
search_k = 5

[SEARCH]
provider = perplexity    ; perplexity | firecrawl | exa | tavily

[PERPLEXITY]
api_key = your-perplexity-key
model = sonar-pro
url = https://api.perplexity.ai

[CRAWL4AI]
timeout = 60
max_pages = 10

[MCP_SERVERS]
files = stdio:uvx my-mcp-server
files_key = your-key
```

`config.ini.example` carries every key with its comment; `docs/CONFIG.md`
documents them one by one.

---

## Bilingual interface (i18n)

Every visible surface (GUI, system messages, dialogs, tray, menus, errors, help) is translated through `hoplita_i18n.py` + `lang/es.json` / `lang/en.json`. The Go CLI uses the **same** JSON files via `i18n.go` (a single source of truth). Switch it with `[APP] language = es | en`.

- The LLM's internal prompts (planner, synthesizer, extractors, …) also live in the JSON files, in both languages.
- The **interface** language is independent from the **LLM's answer** language: an injected directive makes the LLM reply in the user's language even when the internal prompts are in another one.
- The MCP server's console logs are always in English (they are not UI).
- `es.json` and `en.json` keep the **same key set** (paired).

## CLI: the Unix face (no parity with the GUI)

The Go CLI is **not** a poorer GUI and does not chase feature parity with it.
Canvas, editable approval cards and the agents graph are *drawings*, and a
terminal is not the place for them. What the CLI has is what only a terminal
can do: stdin, stdout and an exit code.

```bash
hoplita-cli                            # interactive chat (REPL)
hoplita-cli "what is my quota?"        # one shot: answer on stdout
journalctl -n 100 | hoplita-cli "what is failing?"
hoplita-cli --verify "pytest -q" "fix the tests" && git push
hoplita-cli --json "list my tasks" | jq -r .reply
```

| Flag | What it does |
|---|---|
| `--verify "<cmd>"` | Definition of done: the turn is not finished until `<cmd>` exits 0 |
| `--json` | Machine-readable result (`ok`, `reply`, `verified`) — stable field names |
| `--quiet`, `-q` | No progress on stderr |
| `--yes`, `-y` | Approve multi-agent plans without asking (remote nodes included). Without it: asked on a terminal; on a pipe a local plan runs and one reaching other machines does not |
| `--spawn --server <url> --token <t> [--handle <h>]` | Enroll this machine as a child and exit |
| `--help`, `-h` | Usage |

**Exit codes**: `0` fine · `1` the turn failed · `2` `--verify` never went green.
That is what lets HOPLITA sit inside `&&`: success is not the model saying it
finished, it is the command exiting 0.

Piped input is treated as DATA, never as instructions — a log line that says
"ignore previous instructions" is just a log line.

## Troubleshooting

### System libraries for the canvas (QtWebEngine)

The canvas/dashboards use QtWebEngine (embedded Chromium). The `PyQt6-WebEngine` pip package
ships the Python binding, but Chromium also needs SYSTEM libraries that pip does not provide.
`install.py --system-libs` installs them for your distro; by hand:

```bash
# Debian/Ubuntu
sudo apt install libnss3 libnspr4 libxcomposite1 libxdamage1 \
  libxrandr2 libxtst6 libxkbcommon0 libgbm1 libasound2 libxshmfence1 \
  libegl1 libgl1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
  libxcb-keysyms1 libxcb-render-util0 libxcb-shape0 fonts-liberation

# RHEL/Rocky/Alma/Fedora
sudo dnf install nss nspr libXcomposite libXdamage libXrandr libXtst \
  libxkbcommon mesa-libgbm alsa-lib libxshmfence libglvnd-egl \
  libglvnd-opengl xcb-util-cursor xcb-util-image xcb-util-keysyms \
  xcb-util-renderutil xcb-util-wm
```

Quick check (with the venv active):

```bash
python -c "from PyQt6.QtWebEngineWidgets import QWebEngineView; print('OK')"
```
- `No module named ...` → the pip package is missing: `pip install PyQt6-WebEngine`
- `libXXX.so: cannot open ...` → the system libraries above are missing

### System tray icon does not appear

Missing StatusNotifierItem support. `install.py --system-libs` installs libappindicator, but
some desktops need more:

- **KDE Plasma**: usually works out of the box. If not, install `libappindicator-gtk3` (RHEL)
  or `libayatana-appindicator3-1` (Debian).
- **GNOME**: it has no tray of its own and needs the AppIndicator extension
  (Ubuntu ships it enabled) — `sudo apt install gnome-shell-extension-appindicator`
  or `sudo dnf install gnome-shell-extension-appindicator`, then enable it in
  *Extensions* and log in again.

Without a tray HOPLITA still runs: there is simply no icon, alerts come as a
dialog with their sound, and the window's ✕ quits instead of hiding to the tray.
Quitting stops the scheduled jobs too, since the scheduler lives in the window.

```bash
python -c "from PyQt6.QtWidgets import QApplication,QSystemTrayIcon; \
  a=QApplication([]); print('tray:', QSystemTrayIcon.isSystemTrayAvailable())"
```
`False` means the desktop has no tray support.

### Blank canvas / "Failed to create GBM buffer"

Chromium uses the GPU by default. On a VM with a virtual GPU (vmwgfx, virtio…) it logs
"Failed to create GBM buffer" and falls back to EGL and then to software on its own — a
recoverable fallback, not a failure. HOPLITA does not guess which mode will work: it lets
Chromium adapt and only lowers its log level (`--log-level=3`) so those warnings do not flood
the console. If the canvas still shows blank, force a mode yourself:

| Variable | Effect |
|---|---|
| `HOPLITA_FORCE_SOFTWARE=1` | force software rendering (`--disable-gpu`, `--disable-gpu-compositing`, `LIBGL_ALWAYS_SOFTWARE=1`) |
| `QTWEBENGINE_CHROMIUM_FLAGS="..."` | full control, passed through as-is |

Running as root adds `--no-sandbox` automatically (Chromium cannot create its sandbox as root).

```bash
HOPLITA_FORCE_SOFTWARE=1 python hoplita-gui.py
```

For a real GPU under VMware, enable "Accelerate 3D graphics" in the VM settings.
