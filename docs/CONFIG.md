# HOPLITA configuration (`config.ini`)

**Complete reference, derived from the code.** Every key is listed with its *real default*: the value the program uses when the key is absent from the file (its `fallback=`). Where `config.ini.example` ships a different value, both are given. Anything not listed here is not read by the code.

The file lives in the install's home (see `HOPLITA_HOME` below). Start from `config.ini.example`.

- **Hot reload**: saving in the editor (or `POST /reload-config`) re-reads the **whole** file. Exceptions that need an MCP restart: **`[MCP_SERVERS]`** (unless the assistant changed it, see below) and the vault password.
- **The assistant can read and change it too**: `see_my_config` shows the file with every secret covered, `read_my_docs` reads this manual by section, and `edit_my_config` writes one or several keys after you approve the exact change in a dialog (in the CLI, a y/N question). «I installed the X MCP server, set it up» or «add this model, keep it off councils» is enough. And **`[APP] language`** needs a GUI restart: text already painted is not re-translated live.
- **One rule for every endpoint** (`hoplita_llmcfg.resolve_endpoint`, read alike by the MCP, the GUI, the config editor and — for the base — the Go CLI):
  - **The base** is the pool model elected with `[MODELS] base = <alias>`; its empty fields fill from the literal `[API]`. With nothing elected, `[API]` alone is the base.
  - **A role** (`[BROWSER] model`, `[MEMORY] model`, `[CHAT] planner_model`…) that names a pool **alias** runs on that whole pool model (its own `base_url`/`api_key` are then ignored). A raw model id still works: that id, on the base's server.
  - **Any other section's** empty `base_url`/`api_key`/`model` fill from the base.
- **Visual editor**: the «Configuration» button in the header. See «The dialog» right below.

## The dialog: the basic path and the fine one

**Basic — what anyone who has used an agent fills in, and can live on for good.** The dialog opens on **Models**:

1. **Your models** — one line per model: its name, the model id, the server, whether it sees images, whether it is on, and a blue **BASE** badge on the base. ✎ opens its details with plain labels (Model, Server (URL), Key, Context, What it is good at, Does it see images?, Extra options), 🔌 checks it answers, 🗑 removes it. **＋ Add model**, fill model + URL + key: done. **There is always a base while any model is on**: the first model you add IS the base with no other click, and a base you switch off moves to the first active model, in sight. With no model at all, saving says so in one sentence.
2. **Who uses which model** — every decision about models in one place: the base; «Pick by itself» (the router); Browser and Backup browser; Background work; Planner; Answer reviewer; Takes over when stuck; Vision (describes pictures). Each is a picker over your models whose first entry names its default in words («= the base model (my-model)», «= the conversation's»). The desktop hands run on the conversation's model, as the dialog states.
3. **Pictures** — the picture model on its own server (Server, Model, Key, Size, Max wait).

Everything else follows the base on its own; the other tabs carry sane defaults.

**Fine — for whoever wants to tune, or to find why something does not do X.** The other tabs are **subjects, not INI sections**: Conversation (tool rounds per turn, tool timeout, tokens, context, compaction…), Plans (approval, sub-tasks, reviewer, council), Web (browser behaviour, scraping, retrieval), Search, Hands, Memory, App (language, the MCP), plus MCPs and the raw INI. Token usage is not a setting: it has its own button on the window's header, «📊 Usage». Field labels are the INI keys, each with its help on hover.

**What the dialog never shows:**
- **`[API]`.** On open, what each pool model inherited from it (URL, key, and `extra_body` on the line that carries its endpoint and model) is written onto that model's line: exactly what the resolver gave it, so nothing runs elsewhere after a save. A connection someone really typed into `[API]` is brought in as a pool model (the base if none was elected, inactive if one was); the template's placeholder (`your-model`) is not.
- **A key no code reads.** It stays in the file, untouched, and on no tab — showing options nothing uses only confuses.

**What it brings in, so nothing you had stops working:** an endpoint typed by hand into `[BROWSER]` or `[MEMORY]` (a model id, maybe a URL and key) becomes a pool model — inactive, so it joins no routing it was not in — and the role points at it.

## `[APP]` — Application

| Key | Type | Default | What it does |
|---|---|---|---|
| `language` | text | `en` | UI language: `es` · `en` |
| `motd_seen` | text | — | Internal: last server notice already shown |
| `log_tools_to_notes` | bool | `true` | Auto-log every action tool into the `cmd_log` note category (audit trail, ring-buffered at 300, never semantically indexed) |
| `quit_stops_mcp` | bool | `true` | Quitting from the tray also shuts the MCP down, cleanly (it closes the vault and exits — never killed mid-write). On Windows the shortcut starts it with no console, so without this there is no way to stop it short of the task manager. Set to `false` only if you share one MCP between several faces at once |

## `[API]` — Default connection (not shown in the dialog)

`[API]` chooses **no model** once a pool base is elected: it is the default connection whose fields fill whatever the elected base leaves empty, and the base only while no pool model is elected (a hand-written config with no pool). The dialog writes what it gave the models onto their own lines and does not show it.

| Key | Type | Default | What it does |
|---|---|---|---|
| `api_key` | text | — | Bearer token (a dummy value works for local servers) |
| `base_url` | text | `https://api.openai.com/v1` | Endpoint (ends in /v1) the elected base falls back to for an empty URL |
| `model` | text | — | The base's model while no pool model is elected |
| `extra_body` | text | — | **Passthrough**: a JSON object that rides AS-IS in the body of every request to this endpoint. For what is not standardised and is not HOPLITA's to translate — qwen wants `{"chat_template_kwargs": {"enable_thinking": true}}`, deepseek wants `{"reasoning_effort": "high"}`, and both answer in `reasoning_content`. It does **not** inherit (sending `enable_thinking` to a model that never heard of it is a 400) and it cannot override `messages`/`model`/`tools`/`stream`; anything else (`temperature`, `max_tokens`, `top_p`…) it can, and it wins. Bad JSON is refused out loud — in the editor on save, in the log at call time — never applied silently. Editable in the GUI as «Extra options (JSON)» on each model's line (✎). |

## `[CHAT]` — Chat, agent and multi-agent

> Almost all of these are read by the **MCP**, which is where the loop, the
> context assembly, the router, the orchestrator and the memory work run.
> The GUI and the CLI consume the same endpoints, so a setting here changes
> both faces at once. A few are read by the desktop face itself because they
> govern something it owns: `font_size`, `terminal_timeout`, `cron_live_turns`,
> `client_tool_timeout`, `history_transcript`, `compact_threshold` and
> `compact_keep_recent` (the last one also by the CLI).

| Key | Type | Default | What it does |
|---|---|---|---|
| `agentic_max_iterations` | int | `8` (`20` in the example) | Maximum tool rounds per turn — the per-turn tool limit (Conversation tab, first line). With the key absent, a turn sent by the GUI and a plan's sub-agents use `5` |
| `auto_route_models` | bool | `True` | Router: sends each turn to the pool specialist that fits — «Pick by itself» on the Models tab |
| `client_tool_timeout` | int | `3600` | Seconds the MCP waits for a tool the GUI executes (the GUI sends it with each turn). Long on purpose: an orchestration's sub-agents delegate their tools here too, and a browse or a build takes minutes |
| `max_threads` | int | `8` | How many background jobs may be alive at once, over every conversation: tools started with `background_run`, watches (`background_watch`, `/watch`) and the shell runs they hold. One more is refused and told why (Conversation tab) |
| `terminal_timeout` | int | `300` | Seconds the embedded terminal waits for ONE command — the inner limit, where the one above is how long the MCP waits for the client. A cut comes back as exit `-1` with the partial output, which reads like a failure and is not one, so give long scripts (builds, acceptance runs) room here |
| `compact_keep_recent` | int | `6` | Recent messages that are NOT compacted |
| `compact_timeout` | int | `0` | Seconds one compaction slice may take. 0 = never less than `timeout`, nor than 900. A slice that runs out compacts nothing |
| `compact_threshold` | float | `0.85` | Context fraction that triggers auto-compaction. The bar is measured on the turn's FIRST model call (later calls carry the turn's own tool results, a spike that is not the conversation); the compaction itself runs when the turn has ended, with the tab paused — never while a loop is reading the history. |
| `context_length` | int | `0` (`30000` in the example) | Context window in tokens. 0/absent = autodetect from the server, which is right far more often than a number written here. In the editor the box shows `auto` for 0: empty it or type `auto` to go back to detection (per model under `[MODEL:<name>]`, or here as the last resort). **A value under 1024 is refused out loud and ignored**: it cannot be a window, and a typo such as `= 20` would make the conversation compact itself on every reply. |
| `font_size` | int | `13` | Base font size of the chat transcript (PIXELS, like the app stylesheet — a stylesheet beats setFont in Qt, which is why the zoom goes through one). Live zoom: Ctrl+= / Ctrl+- / Ctrl+0 / Ctrl+mouse-wheel; the zoomed value persists per machine and wins over this base |
| `subtask_timeout` | int | `1800` | Seconds the PLAN waits for ONE sub-agent before going on without it. A clock on the WAITING, not on the work — the only thing that can hang for ever. The sub-task is marked failed and reported, its dependents fail honestly, and the synthesis says what never arrived. 0 = wait for ever, so one stuck call can freeze the whole plan |
| `cron_live_turns` | int | `0` | Optional hard tail of turns handed to a cron that continues a conversation. `0` = the WHOLE conversation, every run — what attaching a chat means: the run reads it all, follows the instruction as its next turn and writes back. When it outgrows the window it is compacted like a tab's context (old part folded into a summary, recent turns kept) and the compacted conversation is saved back. Set a number only for a job that must stay cheap whatever the history holds. |
| `escalate_on_stall` | bool | `True` | When a turn stalls —two sterile nudges, or the same call repeated until the loop breaks— hand it ONCE to a stronger pool model instead of stopping. Said out loud (`model_escalated`), never a silent swap, and only if there is somebody to hand it to: with no pool it does nothing rather than pretend |
| `escalate_model` | text | — (`auto` in the example) | **Role** («Takes over when stuck» on the Models tab): WHO takes over. A pool alias, or `auto` to let the router pick reading the profiles — «bigger» is not in the data, so it is a decision, not a guess. Empty = auto |
| `council_gate_timeout` | int | `900` | Seconds a council waits for your decision when its vote came back split. On timeout it STOPS and hands back the answer with the disagreement signed — never another round nobody asked for |
| `council_max_rounds` | int | `4` | Ceiling on a council's rounds. NOT the policy: a split vote ASKS you whether to spend another one and never repeats on its own. This only stops a stuck loop with nobody watching |
| `heavy_sterile_cut` | int | `3` | Sterile results IN A ROW (empty/error/repeat) that turn an expensive tool (the browser) off for the rest of the turn, told to the model in the result itself. Never a count of uses: a navigation that keeps paying may run a hundred times |
| `max_tokens` | int | `8000` | Cap on the LLM's RESPONSE tokens |
| `metacog` | bool | `True` | Metacognition: when a turn stalls, the model is asked to stop and rethink the path. It asks, never dictates; at most twice per turn, re-armed only by real progress |
| `metacog_stall_seconds` | int | `120` | Seconds without progress (a new tool result, an intent item crossed out) before the tap. Checked between tools, never inside one: a long browse that brings data home never triggers it |
| `metacog_stall_tools` | int | `4` | Consecutive tools with nothing new (repeats, errors, empties) before the tap, however fast they run |
| `multi_max_parallel` | int | `4` | Sub-agents in parallel (raise it for sweeps across contacts) |
| `multi_max_retries` | int | `2` | Retries per failed sub-task |
| ~~`num_ctx`~~ | — | — | **DEAD: not read.** The window has one name, `context_length`. If `[CHAT] num_ctx` is in your file you get a warning on the first turn: rename it to `context_length`, or delete it and the window is asked of the server. |
| `plan_approval` | text | `always` | Permission before launching sub-agents: `always` (every plan asks) · `auto` (only remote or above the limit) · `off` |
| `plan_ack_timeout` | int | `900` | Seconds a plan waits for your approval. On timeout it CANCELS: better to lose the plan than to fan out unattended |
| `plan_auto_limit` | int | `3` | **With `auto` only**: local plans of ≤N sub-tasks do not ask |
| `plan_max_expansions` | int | `2` | Ceiling on RE-SCALING rounds: how many times a running plan may grow because a sub-agent found the job bigger than assumed. `0` freezes it even with the box ticked |
| `plan_max_subtasks` | int | `12` | Hard ceiling of sub-tasks in one plan, re-scaling included. Reached, sub-agents stop being asked for more |
| `planner_model` | text | — | **Role** («Planner» on the Models tab): pool alias for the planner and synthesizer (one pass each: quality over speed). Empty = follows the chat's base |
| `vision_model` | text | — (automatic) | **Role** («Vision (describes pictures)» on the Models tab): the pool alias that DESCRIBES a picture for a model that cannot see it; the description takes the picture's place, once per picture (kept by its hash in `vision_descriptions.json`, so a history sent again gets the same text). `none` = nobody describes, the blind model gets a note. Empty = the base if it sees, else the first active pool model that sees |
| `qa_model` | text | — | **Role** («Answer reviewer» on the Models tab): pool alias for the quality reviewer. Empty = the reviewer would be the turn's own model, so the turn is not reviewed (see `qa_review`) |
| `qa_budget_chars` | int | `40000` | Evidence the reviewer may be shown. Past it, grounding is no longer graded and only the deliverable is judged — a verdict on a fraction of the evidence calls real findings inventions |
| `qa_max_rounds` | int | `2` | Times the reviewer may send an answer back before the turn ends anyway |
| `qa_review` | text | `off` | Review before accepting an answer: `off` (default) · `auto` · `always`. The reviewer sees the WORK: every step whole — the tool, its full arguments (the patch, the file written, the query) and everything it returned — and judges whether the answer is supported by it, and whether code written has visible bugs or leftovers. All or nothing: when the whole does not fit `qa_budget_chars` it reviews the steps only (tool, arguments on one line, ok / failed / empty) and the chat says so. One extra LLM call, max 2 rounds. **Never a model reviewing itself**: when the reviewer would be the model that did the work (no `qa_model`, or the same one) the turn is not reviewed and the chat says so. Naming a judge with `/qa <model>` turns it on for that tab; the event `qa_skipped` (why `self`) says when a review was skipped for this reason. |
| `history_transcript` | bool | `True` | The history keeps the turn's TRANSCRIPT as the model saw it (tool calls, results, reviewer notes, rejected drafts), not just question and answer. The context grows downward and the server's prefix cache serves from one turn to the next. Turn off to keep only question and answer: shorter, but every turn re-reads the previous one without the cache. |
| `session_inject_context` | bool | `True` | In a session with **yourself** from another machine (same identity), the agent that runs it works with your facts and lessons; `false` turns that off too. In a session with a **contact** or a **child** they never go in, whatever this says: the answer goes back to the other side. What they need to know is shared on purpose, as a skill or a pack |
| `timeout` | int | `120` (`1000` in the example) | Seconds to wait on the LLM |
| `vision` | bool | `False` | The general answer to «does it see images?» for a model that declares nothing on its own line (see `[MODEL:<alias>] vision`) |

## `[RAG]` — Retrieval

RAG is word search: no model call reads a `[RAG]` endpoint, so it has none. `base_url`/`api_key`/`model` keys in this section (the example still carries them) are left in the file, read by nothing and shown nowhere.

| Key | Type | Default | What it does |
|---|---|---|---|
| `chunk_overlap` | int | `50` (`5` in the example) | Overlap between chunks, in words |
| `chunk_size` | int | `200` (`25` in the example) | Words per chunk when indexing |
| `min_similarity_score` | float | `0.18` | Anti-noise floor (0-1) for accepting a chunk. The real bar is relative: half the best hit's score, never below this floor |
| `search_k` | int | `5` | Chunks returned by a search |
| `max_chunk_chars` | int | `1600` | Character ceiling per chunk, on top of the word count (minimum 200). Automatically tightened to 700 on markup-heavy text (>12 chars/word), where a few «words» can be very long |

## `[BROWSER]` — Agentic browsing (browser-use)

| Key | Type | Default | What it does |
|---|---|---|---|
| `model` | text | — (the conversation's model) | **Role**: the pool alias that **drives** the browser — «Browser» on the Models tab. Empty = the tab's pinned model, else the base. A raw model id still works (on the base's server) |
| `default_skill_category` | text | `skills` | Category where skills learned while browsing are stored. It is the DEFAULT, not the only one: any folder can be marked as skills (`skill_folder`, or right-click in the notes list) and joins the index and `use_skill` — a folder a contact shared, one you subscribed to. That mark lives with the notes, so it follows you to your other devices |
| `skills_index_budget` | int | `6000` | Ceiling for the skills INDEX, in characters. That index rides in the context of EVERY turn, so its size is paid on every message: at ~91 chars per skill, 500 skills is ~12,600 tokens each time. Past the ceiling the index stops being shown whole and the matching ones are SEARCHED instead (by words, weighting the rare ones — no model, no server, no tokens), and it SAYS it is a selection: an index silently truncated makes the model believe the right skill does not exist, which is the exact failure the index exists to prevent. 0 = no ceiling |
| `fallback_model` | text | — | **Role** («Backup browser» on the Models tab): pool alias that rescues a step when the main model fails. Must be enabled in `[MODELS]`: a disabled alias is ignored and said in the MCP log — off is off on every road |
| `flash_mode` | text | `auto` | `auto` · `true` · `false` — strips the agent's thinking (less prose to dirty the JSON) |
| `headless` | bool | `False` | Browse without showing the window |
| `locale` | text | — | Browser locale (e.g. `en-US`); empty = the app's language |
| `max_actions_per_step` | int | `3` | Actions per step |
| `max_steps_default` | int | `30` | Maximum steps for the browsing agent |
| `structured_output` | text | `auto` | `force` · `auto` · `off` — force makes the SERVER constrain the JSON (the cure for "Invalid JSON") |
| `use_thinking` | text | — | `true` · `false` · empty — pass "thinking" mode to the agent (only useful on reasoning models) |
| `use_vision` | text | `auto` | Whether the browser sends SCREENSHOTS to the model. `true`/`false` force it and **overrule everything below**. `auto` asks the config through the one vision rule (see `[MODEL:<alias>] vision`), never the model's name. |
| `user_agent` | text | a desktop Chrome on macOS | Browser User-Agent. Empty in the example, which means the built-in one |
| `viewport_height` | int | `900` (`768` in the example) | Browser window height |
| `viewport_width` | int | `1366` (`1024` in the example) | Browser window width |
| `wait_between_actions` | float | `1.2` | Seconds between actions |
| `wait_network_idle` | float | `3.0` | Seconds to wait for network idle |

## `[IMAGE]` — Picture server (generate_image / edit_image)

Any server that speaks the OpenAI Images API (`/v1/images/generations`, `/v1/images/edits`): OpenAI, xAI, LocalAI, stable-diffusion.cpp's server. The chat endpoint does not make pictures, so this is its own section. Empty `base_url` = the two tools answer that no picture server is configured.

| Key | Type | Default | What it does |
|---|---|---|---|
| `base_url` | text | — | The picture server, up to `/v1` (e.g. `http://127.0.0.1:8090/v1`). |
| `api_key` | password | — | Bearer key, if the server wants one. |
| `model` | text | — | Model name the server expects. Empty sends none. |
| `size` | text | `1024x1024` | Default WIDTHxHEIGHT when the model gives none. Some diffusion models want multiples of 32. |
| `timeout` | int | `300` | Seconds to wait for a picture; local diffusion on a busy GPU takes minutes. |

## `[HANDS]` — Desktop hands (the model on your screen)

| Key | Type | Default | What it does |
|---|---|---|---|
| `remote_model` | text | `ask` | `ask` · `allow` · `deny`. What happens when the turn's model is **not** on this machine or your local network (what counts as local is listed under `local_hosts`): with `ask` a dialog asks once per turn, `allow` never asks, `deny` never sends. The screen never leaves silently: unattended turns (cron) and faces that cannot answer are refused and told why. |
| `verify` | bool | `true` | After each click, key or text the screen is captured and compared with the capture before, and the model is told whether anything changed (`verified` true/false, or null when nothing could measure it). |
| `action_delay` | float | `0.4` | Seconds to let the interface settle after an action before verifying. |
| `screenshot_max_width` | int | `1280` | Captures attached for the model are scaled down to this width. |
| `op_timeout` | int | `45` | Seconds ONE desktop operation (list windows, read a tree, press, capture) may take before it is abandoned. The element layer talks to the desktop's accessibility service, and enumerating a desktop that holds an unresponsive window can block for minutes; the watchdog returns a timeout error instead and the turn walks on. |
| `consent_timeout` | int | `300` | Seconds the «may this remote model see your screen?» question waits for an answer before the turn is refused as unanswered. |
| `local_hosts` | text | — | Extra hosts, IPs or CIDRs (comma/space separated) that count as YOUR network, so `remote_model` does NOT treat a model there as remote. Already local without listing: `localhost`, loopback, link-local, RFC 1918 private ranges, the shared/CGNAT space Tailscale uses (`100.64.0.0/10`), the suffixes `.localhost` `.local` `.lan` `.home` `.home.arpa` `.internal` `.intranet` `.ts.net`, and any bare hostname (no dot). No host name is hardcoded — this list is the only place a specific machine is named. |
| `pixel_backend` | text | `auto` | `auto` picks the pixel layer from the **session**, not the platform: an X display, Windows and macOS use a pointer/keyboard library and a screen grabber; a Wayland session uses the compositor's screenshot tool for capture and an input-injection helper (`ydotool`) for raw input when installed. The element layer (accessibility: windows, trees, pressing, typing into fields) needs neither. The helper needs its daemon running (its socket is looked for in `$YDOTOOL_SOCKET`, the runtime dir and `/tmp`); installed without the daemon reads as `input: false` with the reason. `install.py --desktop --system-libs` sets up what the distribution packages (without `--system-libs` it prints the command and asks for nothing). `wayland` forces the Wayland path. On a Wayland desktop whose distribution does not package the helper, `x11` forces the X path through the X bridge: it reaches the desktop only where the compositor forwards X input (recent KDE does, asking for permission once), capture still goes through the compositor's tool, and `verified` on the first action tells whether it worked. |

## `[MEMORY]` — The memory work (distilling after a turn, the weekly pass)

| Key | Type | Default | What it does |
|---|---|---|---|
| `model` | text | — (the base) | **Role** («Background work» on the Models tab): the pool alias that DISTILS facts and lessons right after a turn. It runs every few messages on this model, whichever model the conversation uses — also when it is that very model (the MCP log's `[PREFIX]` lines say what that costs the prompt cache). Every other side call speaks to this model too: the digester of large tool results, the skill picker that runs before each turn, the lesson extractor, the curation passes. The MCP log tags each of them by name (`[SKILL-PICK]`, `[LESSONS]`, `[MEMORY-CURATE]`…). |
| `base_url` / `api_key` | text | — | A hand-written endpoint for the role above, used with a raw model id in `model`. The dialog brings such an endpoint into the pool as an inactive model and points the role at it |
| `auto_review` | text | `on` | The memory curation pass: merges duplicate lessons, retires rules that no longer hold and proposes changes to facts. `off` / `false` / `no` / `0` turn it off. Read on every tick, so no restart is needed. The same pass also runs early, as a pressure pass, when live memory grows past its budget |
| `review_every_hours` | int | `168` | Hours between scheduled passes (168 = weekly). A fresh store gets its first pass one full period after it is created |
| `review_facts` | text | `0` | Facts examined per pass. `0` / `auto` = self-sized: a third of the pending pool per pass, so the whole store is reviewed once per period. A number is used as written |
| `volatile_days` | int | `3` | Every fact has a life: `permanent` (who the person is: always in the prompt, never expires by silence), `relevant` (ongoing: found when its topic comes up) or `volatile` (one moment). The extractor gives it when it writes the fact; the hourly clock gives one to the rest. A volatile fact is archived after this many days without coming up (stored again, returned by a search or spoken of in the log), however many times it was stored. Undoable; still found by a search. `0` = never |
| `ask_after_days` | int | `30` | A relevant fact silent this long becomes a question in Proposals, «still on with this?»: «No longer» archives it, «Yes» keeps it and starts its clock again. `0` = never |

## `[SEARCH]` — Web search

| Key | Type | Default | What it does |
|---|---|---|---|
| `provider` | text | `perplexity` | Web search provider: `perplexity` · `firecrawl` · `exa` · `tavily`. The others stay configured and idle |

## `[PERPLEXITY]` — Perplexity provider

| Key | Type | Default | What it does |
|---|---|---|---|
| `api_key` | text | — | Perplexity key |
| `model` | text | `sonar-pro` | Model (sonar-pro…) |
| `url` | text | `https://api.perplexity.ai` | Perplexity endpoint |

## `[FIRECRAWL]` — Firecrawl provider

| Key | Type | Default | What it does |
|---|---|---|---|
| `api_key` | text | — | Firecrawl key |
| `limit` | int | `5` | Results per search |
| `max_chars` | int | `0` | Text kept per source. **0 = whole**. Same as Exa's |
| `url` | text | `https://api.firecrawl.dev` | Firecrawl endpoint |

## `[EXA]` — Exa provider

Search built for retrieval rather than for a human reading a page: it tends to
bring back the passage that answers the question, not the page that ranks for it.

| Key | Type | Default | What it does |
|---|---|---|---|
| `api_key` | text | — | Exa key |
| `url` | text | `https://api.exa.ai` | Exa endpoint |
| `mode` | choice | `search` | `search` = raw sources with their text, the agent synthesises and can be checked on its quotes · `answer` = Exa writes it with citations |
| `type` | choice | `auto` | `auto` (Exa decides) · `neural` (by meaning) · `keyword` (by words) · `fast` (latency over quality) |
| `limit` | int | `5` | Sources per search |
| `max_chars` | int | `0` | Text kept per source. **0 = whole**, whatever the provider sent. A cut here destroys the text before the spill store, the RAG or a later `read_note` can hold it; size is handled downstream, where the original survives and every part of it is read |

## `[TAVILY]` — Tavily provider

The one provider that can return an answer AND the sources behind it, so it
returns both: one to read, the other to check it against.

| Key | Type | Default | What it does |
|---|---|---|---|
| `api_key` | text | — | Tavily key |
| `url` | text | `https://api.tavily.com` | Tavily endpoint |
| `depth` | choice | `basic` | `basic` is one fast pass · `advanced` digs more and costs more per search |
| `limit` | int | `5` | Sources per search |
| `include_answer` | bool | `true` | Ask for a written answer on top of the sources (they come either way) |

## `[CRAWL4AI]` — URL scraping (crawl4ai)

| Key | Type | Default | What it does |
|---|---|---|---|
| `max_depth` | int | `0` | Depth of the prospective crawl (0 = just the given URL; 1-3 follows links on the SAME domain) |
| `max_pages` | int | `10` | Page cap when max_depth > 0 |
| `max_urls` | int | `10` | Cap on URLs `/includeurls` scrapes per file (0 = no limit) |
| `timeout` | int | `60` | Seconds per page when scraping |
| `stealth` | bool | `True` | Run the crawler's browser in stealth mode (passive fingerprint fixes) |
| `override_navigator` | bool | `True` | Hide the automation markers a page can read (`navigator.webdriver` and the like). Passive |
| `magic` | bool | `False` | crawl4ai's «magic» mode: it interacts with the page to get past overlays. Can break some sites, so it is opt-in |
| `simulate_user` | bool | `False` | Simulate user activity (mouse, scroll) on the page. Interactive like `magic`, so opt-in |

## `[MCP]` — The local MCP server

| Key | Type | Default | What it does |
|---|---|---|---|
| `url` | text | `http://127.0.0.1:9077` | Where the faces (GUI and CLI) reach the MCP |
| `enabled` | bool | `true` | Shown in the dialog and shipped in the example; no code reads it. The faces always use the MCP |
| `sync_on_boot` | bool | `False` when absent (`true` in the example) | On MCP start, pull your encrypted snapshot — chats, canvases, notes, facts, lessons, cron — so **any** face, the CLI included, finds the machine up to date. It WRITES into your local database, so set it to `false` on any machine you do not own: a headless child spawned on someone else's server also starts an MCP. A `config.ini` without the key stays off |
| `sync_every_min` | int | `15` | While the GUI is open, pull and then push the snapshot every N minutes (0 = off), so two machines open at the same time see each other's changes. Pull first, push after, so one machine never overwrites what the other did. Silent unless something changed |

## `[SKILLS]` — Skills

| Key | Type | Default | What it does |
|---|---|---|---|
| `ask_which` | bool | `True` | Before each turn, show the model the INDEX of your skills and ask it one short question: which of these is FOR this? If it says none, none goes in. One short call on the «Background work» model. `false` = only the skills you pin by hand with `/skill pin` |

## `[LESSONS]` — Lessons

| Key | Type | Default | What it does |
|---|---|---|---|
| `enabled` | bool | `True` | Lessons system (behavioural memory): when on, saved feedback is queued for lesson extraction, one at a time, while you are idle |
| `extract_on_feedback` | bool | `True` | Distil a lesson when you give feedback. With this off, a thumbs-down stores the experience and nothing else — no lesson, weak or otherwise |
| `inject_on_chat` | bool | `True` | Inject relevant lessons into the chat |

## `[AGENTIC]` — The tool loop

| Key | Type | Default | What it does |
|---|---|---|---|
| `defer_tool_schemas` | bool | `True` | Ship the full schema only for the hot tools, and the rest as a **name + one line** catalogue. The tools block is built ONCE per turn and never rebuilt: what `load_tools` brings arrives in its result, at the bottom of the context, and the model calls a catalogue tool through the static bridge `use_tool(name, arguments)`. Measured: full schemas of ~200 tools are ~15-30k tokens at the top of every prompt; the catalogue plus the hot set is ~3k. With a prefix-caching backend the point is not the size but that the top never moves. |
| `hot_tools` | int | `12` | How many of the most-used tools travel already expanded. Decided by REAL usage, counted per install and persisted — and **frozen per MCP process**: the ranking is computed once per (project open, firewall) and kept until restart, so the tools block is identical turn after turn. Usage keeps counting and shapes the next boot. (The memory verbs ride hot by design, outside this count; an open project makes the code and git tools hot.) |
| `tool_result_max_chars` | int | `8000` | Threshold for ONE tool result entering the turn's context (what is READ at once, not what can be changed). Above it the result is DIGESTED, not cut, by the «Background work» model (`[MEMORY]`) — always that model, even when it is the one the conversation runs on: the LLM reads it whole in one side-call and the context receives the dense data-first extract, while the full text is kept in the conversation's WORKING MEMORY (a `working-memory` note, reachable via read_note) and dropped when that conversation is reset or deleted. Only if the digester fails does the announced cut run. **Line-oriented tools are exempt from the digest**: source code, a command's output or a file listing are LINES, not prose, and a paragraph describing a file is worse than the file's first N lines. Those enter whole up to 15% of the answering model's context window (about 3 chars per token, at most 100.000 chars; this value is the floor when the server does not say its window), and past that they are cut on a real line boundary — inside a JSON result too: the result says `cut: lines A-B of N` and `next`, the exact `code_read_range` call that continues. A reply given with part of a cut file unread is said in the chat («read 240 of the 480 lines of …»). Hot-reloadable. |

## Free-form sections

### `[MODELS]` + `[MODEL:<alias>]` — multi-model pool
`[MODELS]` lists the active aliases (`alias = true`). Each one is configured in its own section:

| Key | What it does |
|---|---|
| `base_url` / `api_key` / `model` | That specialist's endpoint and model |
| `context_length` | That model's real window (0 = autodetect). Useful when the endpoint lies |
| `vision` | Does THIS model see images? `true` / `false`, or empty = not declared. **One rule** (`hoplita_llmcfg.model_sees`) answers for the hands, the browser and the GUI's pasted-image check: the pool lines carrying that model id are asked — those on its own server first; any `true` among them means it sees, an explicit `false` with no `true` means it does not, empty lines do not count; with nobody declaring, `[CHAT] vision` decides. **And it is enforced at the wire:** every request to a model that does not see goes out with each image replaced by a note saying it was not sent — the history's old captures, a pasted picture, anything — so a server started without a vision encoder never answers 400. |
| `profile` | **In plain language**: what it is good at. The router and the planner READ this to decide |
| `council` | `false` keeps this model off every council: it stays in the pool for everything else, but no council seats it — not the whole-pool default, not the council page's bench, and a `call_council` that names it is refused. Empty or `true` = it sits. In the dialog: «Sits on councils?» on the model's line |
| `extra_body` | That model's own passthrough knobs (JSON object) — see `[API] extra_body`. Per model on purpose: thinking is spelled differently by every backend |
| `lend` | `true` lets a contact with the «Models» permission add this model to their pool and run it here, through the federation: their requests reach this machine sealed, this machine asks the model, the answer goes back the same way; your key never leaves. Absent = not lent. In the dialog: «Lend to contacts?» |
| `via` | The handle whose model this line is (`via = bob@hoplita.net`): a contact's model, with no `base_url` and no `api_key` of its own. Written by «Models they lend…» in Contacts, never by hand; `model` is its name on their side, `profile` / `context_length` / `vision` what they said of it. It resolves to this machine's own `/lend/…` route, so every role and the router use it like any other model; it answers only while they are online, never serves a local-only conversation, and is not lent on |

**`base = <alias>`** in `[MODELS]` elects one pool model as **the base** — the model that runs when nothing is pinned, what `/model base` (and a cron on «base», and `/qa base`) returns, what the hands and the «Configuration» button tooltip name, and what every section's empty fields fill from. In the dialog it is the «Base model» picker at the top of «Who uses which model», over the active models only.

It is used three ways: automatic router (`[CHAT] auto_route_models`), manual (`/model <alias>`), or per planner sub-task. `/model X` makes X **the chat's base**: planner, synthesizer, "base" sub-agents and compaction all switch to it.

### `[MCP_SERVERS]` — external MCPs (as many as you like)
One line per server: `name = stdio:<command args>` (a local process HOPLITA starts) or `name = sse:https://url` (a remote server). Lines whose value starts with neither are skipped. The server's tools join the catalogue under that name.

- `<name>_key` is the server's key: sent as the bearer for an `sse:` server, exported as `NAME_API_KEY` to a `stdio:` process.
- Any other `<name>_something` key is exported to the `stdio:` process as the environment variable `NAME_SOMETHING`.

```ini
[MCP_SERVERS]
files = stdio:npx -y @modelcontextprotocol/server-filesystem /path/to/folder
remote = sse:https://mcp.example.com/sse
remote_key = your-key
```

**Requires an MCP restart** (the subprocesses are already up) when it is edited by hand or in the dialog — the dialog's «MCPs» tab edits this section. A change the assistant makes with `edit_my_config` (after you approve it) connects or drops that server on the spot, with no restart.

## Environment variables

| Variable | What it does |
|---|---|
| `HOPLITA_HOME` | The install's home: `config.ini`, the encrypted store, `media/`, `whiteboards.db`, `bin/`. |
| `HOPLITA_VAULT_PASSWORD` | The vault password, for unattended starts. The MCP takes it out of its environment at start, so the commands it runs do not inherit it. Where to keep it: [FEDERATION.md 7b, *With a password*](FEDERATION.md#7b-a-federated-child-a-headless-node-on-another-machine). After a change of password (`python/hoplita_passwd.py`, or Configuration → App), write the new one here too. |
| `HOPLITA_VAULT` | `open`: the vault's key is the file `vault.key` beside it, and every start says so. Also chosen when a spawn token boots a machine with no vault yet, and kept once `vault.key` exists. A password given still wins. See [FEDERATION.md 7b](FEDERATION.md#7b-a-federated-child-a-headless-node-on-another-machine). |
| `HOPLITA_SPAWN_SERVER` / `HOPLITA_SPAWN_HANDLE` / `HOPLITA_SPAWN_TOKEN` | A child's boot: the MCP enrolls itself with the token at start, writes the starter kit into its `config.ini` and takes the token out of its environment. Ignored on a machine that already has an identity. A child installed with `install.sh --child` gets them from `spawn.env` in its folder instead, which the MCP deletes once the token is spent. See FEDERATION.md 7b and 7c. |
| `HOPLITA_CHILD_NETWORK` | The `--network` a container child is started with (`host` for a federation server on this same machine). Default: the runtime's own. |
| `HOPLITA_DIFFR` | Path to the `diffr` binary for the whiteboard's structural diff. Without it, `<HOPLITA_HOME>/bin/diffr` (what `install.py --diffr` installs), then `diffr` on `PATH`; with none, the whiteboard uses a textual diff. |
| `HOPLITA_CONFIG` | CLI only: the `config.ini` to read. Without it the CLI looks in the usual places for the platform. |
| `HOPLITA_LOGFILE` | The MCP also writes its log to this file. The desktop launcher sets it to `hoplita-mcp.log` next to the program. |
| `HOPLITA_GUI_PROMPT` | `1` makes the MCP ask for the vault password with a graphical dialog instead of the terminal. The desktop launcher sets it. |
| `HOPLITA_SHELL` / `HOPLITA_SHELL_ARGS` | The shell of the embedded terminal and its arguments. Default: `bash --norc -i` on Linux and macOS (not your login shell, whose plugins tend to crash in an embedded terminal), `cmd.exe` on Windows. |
| `HOPLITA_INCOMING_QA` | Quality review for turns this machine runs for a federated contact: `always` (default, since they run unattended) · `auto` · `off`. |
| `HOPLITA_FORCE_SOFTWARE` | `1` forces software rendering in the GUI's web views, for machines whose GPU driver misdraws them. |

The whiteboard has no `config.ini` section: its store is `<HOPLITA_HOME>/whiteboards.db`, its authoring lease lasts ten minutes without a write, and its page is served by the MCP at `/wb/app`.

## Keys the code ignores

These may appear in `config.ini.example` or in an older file and do nothing: `[BROWSER] num_ctx`, `[MCP] enabled`, `[RAG] base_url` / `api_key` / `model`, and `[CHAT] num_ctx` (which only triggers the warning described above). Any key not documented on this page belongs here too.

## Internal values (not configurable)

| What | Where |
|---|---|
| CRON scheduler interval (30 s) | `CronScheduler.CHECK_INTERVAL_S` |
| Per-note encryption salt | cryptographic constant, **never** changed |
