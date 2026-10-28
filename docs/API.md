# HOPLITA — the MCP's HTTP surface

This is the contract between the brain and its faces. The MCP does the
thinking; the GUI and the CLI consume **these endpoints and nothing else** —
neither of them has an agentic loop, an orchestrator, a context assembler, a
model router or a memory distiller of its own. If you are writing a third
face, this page is the whole API you need.

Base URL is `[MCP] url` (default `http://127.0.0.1:9077`). Every endpoint
takes and returns JSON unless it says otherwise. A complete index is at the
end: [All endpoints](#all-endpoints).

---

## `POST /agent/turn` — one turn, streamed

The centre of everything. Returns **NDJSON**: one JSON object per line, ending
in exactly one terminal event. Do not buffer it; the whole point is that you
paint as it happens.

### Request

| Field | Default | What it does |
|---|---|---|
| `messages` | — | The complete message list. Wins over `message`/`history`/`system`. |
| `message` + `history` + `system` | — | The loop assembles the messages itself. For a simple caller. |
| `max_iterations` | `[CHAT] agentic_max_iterations` | Tool rounds before it gives up. |
| `verify` | — | **Definition of done**: a shell command that must exit 0. While it fails, the loop refuses to accept "I'm finished" and feeds the failure back. Bounded by the SAME iteration budget — it adds no loop of its own. |
| `verify_goal` | — | The bar of the CHECK itself, for what its exit code cannot answer, **in words**: «pp512 above 2000», «no errors in the log». Judged against the verify command's own output by a model (`fed_agent.judge_output` — the same judge `POST /code/judge` asks, so the button and the loop get the same reader). Green means exit 0 **and** the judge calling the bar met; the judge's one-line why is fed back, so a model that came up short is told why. A bar nobody could judge is handed on saying exactly that and never changes the verdict. |
| `goal` | — | Definition of done **in words**, for what no command can check — "at least 3 breeds backed by veterinary sources". `verify` is an oracle for code; this is the acceptance criterion for everything else. It goes to the model up front (finding out the bar AFTER failing costs a whole loop) and to the quality reviewer as the bar to clear. Without one the reviewer can only judge coherence, so a tidy useless answer passes. |
| `qa` | `off` | Quality review: `auto` (turns whose work is worth reviewing: tools used, or a long answer) · `always` · `off`. |
| `qa_endpoint` | resolved here | Who reviews. Omit it and the MCP expands `[CHAT] qa_model` itself — do not expand the alias on the client, that is how two faces end up judging by different standards. |
| `endpoint` | the router decides | `{base_url, api_key, model}`. Send it **only when the user pinned a model** (`/model X`, `/model`). Without it `route_turn_model` picks a pool specialist, once, here. |
| `compact` | `false` | A compaction run as a turn of the conversation: the same preparation, tools and model (the one that served the conversation's last turn when no `endpoint` is sent), thinking off, no log, no review, one iteration, every tool refused. The reply is the summary; the face hands it to `/memory/compact` as `summary`. |
| `delegate` | `[]` | Tool names the CLIENT executes, or `["*"]` for all. See below. |
| `client_tool_timeout` | `900` | How long to wait for a delegated tool. |
| `inject` | `false` | Build the context here: lessons, skills, facts, language, identity, model pool. Both faces send `true`. |
| `pinned_skills` | `[]` | `[{title, content}]` — skills pinned in YOUR session. The one piece of context the MCP cannot know on its own. A pinned skill may also carry its own definition of done: a line `hoplita-verify: <command>` in its text sets `verify` for the turn, so a skill that states how it is judged does not need the user to retype it. Only PINNED skills may — pinning is the explicit act; a skill matched by relevance arming the verifier would mean any note could get a command run. An explicit `verify` in the request wins, and the choice is announced as a `prep` note. |
| `code_project` | `""` | Path of the project the caller has open in its code view. Injected as a `[CODE MODE]` block so the model knows WHERE it is working and uses it as the default path for the `code_*` and `git_*` tools, instead of asking or being told the path on every message. It also states the two habits that keep the work reviewable: `code_apply_patch` over shell redirection (it validates before writing and leaves an undo point), and a branch before a substantial change. In the GUI it is per chat TAB, so two tabs on two projects do not cross. Announced as a `prep` note. |
| `rag_docs` | `0` | How much the caller's knowledge base holds for this conversation (attached documents, URLs, dumped notes). With something loaded, `search_rag` rides among the hot tools — the tool the cut of a long document points at must not sit on the shelf. |
| `blocked_tools` | `[]` | Tool names THIS turn may not see or run, on top of the global firewall. The GUI sends the calling tab's own policy (right-click on the tab → firewall): the model is not offered them, a delegated call to one is refused before the `client_tool_call` event leaves, and every sub-agent the turn spawns inherits the same set. Empty keeps the turn on the global firewall alone. |
| `local_only` | `false` | The calling tab's lock (🔒 on the conversation). A locked turn is offered no `fed_*` tool, its sub-agents neither, and the model is told why; the project folder's own local-only mark has the same effect, and either one suffices. |
| `ack_mode` | `""` | `"stream"` = you read this stream and will answer a `plan_ack`. Anything else means nobody can approve, and a plan resolves without blocking (local runs, remote is cancelled). |
| `unattended` | `false` | Nobody in front of the screen (a cron). Never blocks on approval. |
| `orchestrate` | — | Run the multi-agent orchestrator DIRECTLY as this turn, instead of letting the model decide. This is `/multi`. |
| `council` | — | Run a COUNCIL directly as this turn: `{topic, members[], first, context}`. Same idea as `orchestrate` and the same reason — on the council page the seats are already chosen, so no LLM round trip is spent deciding any of it and the router gets no vote on who sits. `members` are pool aliases (`base` = the endpoint this turn is pinned to), `first` is who answers, `context` is a briefing that travels with the matter. |
| `turn_id` | generated | So you can cancel it. |
| `log` | `true` | Whether to record this turn in the searchable message log. `false` = an EPHEMERAL conversation: nothing is written, nothing is saved, nothing leaves the machine. |
| `session` | `""` | Which conversation this turn belongs to, so the searchable message log can group it. Every turn is logged verbatim here in the MCP — not in the face — because the GUI, the CLI, cron and turns arriving from another machine all come through this door. Empty still logs, just ungrouped. |
| `session_uuid` | `""` | The SAVED conversation this turn belongs to (a `chat_sessions` uuid). NOT the same as `session` above: that one only groups the log, this one is the row. It is what lets a tool point at «THIS conversation» — `add_cron(context_session="this")` — without the model having to know the tab's title, which a fresh tab does not have. Empty when the tab has never been saved, and then attaching it says so instead of guessing. |
| `job_id` | `""` | The cron job whose own tab this turn runs in, in a run and in what the person types there between runs. `update_cron` and the `*_current_cron_job` tools act on it when no job is named, so a worker told to change its task changes its own job. |

### Events

Non-terminal, in no guaranteed order except that `turn_start` is first:

| Event | Payload | Meaning |
|---|---|---|
| `turn_start` | `turn_id`, `tools`, `max_iterations`, `shelf`, `carried` | Keep the `turn_id`: you need it to stop or to answer tools. `carried` names the tools that travel with their full schema this turn; `shelf` counts the rest, available by name through `load_tools`. |
| `tab_saved` | `uuid`, `sync_id` | Only to the window of the machine a tied tab lives on: another of your machines saved that tab here through `/tied/…`; an open window takes the saved copy. |
| `proxy_response` / `proxy_end` | `status`, `ctype`, `body_b64` / `status` | Internal to `/tied/…`: the answer of a request that does not stream, and the end of one that does. A client of `/tied/…` receives the answer itself, never these. |
| `prep` | `note:{key,args}` | What the MCP put in front of the model. **An i18n key, not text** — it does not know your language. |
| `iteration` | `n`, `max` | |
| `injected` | `message`, `n` | Something arrived on `/agent/inject` while this turn was running and was appended as a user message on iteration `n`. |
| `tools_expanded` | `tools`, `total` | The model asked for schemas with `load_tools`. The manual travels in the tool RESULT (a message at the bottom); the tools block up top is NOT rebuilt — it is the prompt's prefix and it is built once per turn. The model calls a catalogue tool through the static bridge `use_tool(name, arguments)`, which the loop unwraps before every gate (firewall, profile, sterile cut, delegation, record). `total` is the size of the unchanged block. |
| `max_iterations` | `max`, `was` | The budget changed **mid-turn**: `[CHAT] agentic_max_iterations` was edited and reloaded while this turn was running. The loop re-reads it every round, so a raise reaches the turn already spinning. |
| `llm_call` / `llm_done` | `model`, `n_messages`, `n_tools`, `payload_kb` / `duration_s` | **Label the call with `model`, never with your own config.** Only the MCP knows where the turn went: when its router picks a pool specialist the client has no endpoint at all. |
| `tokens` | `prompt_tokens`, `estimated` | For the context meter. |
| `tool_call` / `tool_result` | `tool`, `args_preview` / `result_preview`, `delegated` | Ran **here**. |
| `client_tool_call` | `call_id`, `tool`, `args` | Ran **by you**. Answer on `/agent/tool_result` with **exactly this `call_id`** — a sub-agent's is prefixed (`<sid>~<id>`) because parallel sub-agents would collide, and echoing anything else means the loop waits for an answer that reached nobody. |
| `client_tool_slow` | `tool`, `call_id`, `waited`, `ceiling` | **The MCP is waiting for you.** `waited` seconds so far, `ceiling` before the wait gives up (`client_tool_timeout`). Sent once, ~20s in. Nothing is cut — a delegated tool may legitimately take an hour — but a deaf or crashed client is otherwise invisible: the stall watchdog checks BETWEEN iterations and a turn blocked on a delegated tool never reaches one, so this is the only clock that can see it. Show it. |
| `loop_broken` | `tool`, `times` | The same call returned a byte-identical result `times` in a row, so the turn was cut short instead of burning its iteration budget. The reply says which tool it got stuck on. Warned from the second repeat inside the tool result itself. |
| `tool_progress` | `key`, `args` | Mid-tool truth: a running tool narrates what it is REALLY doing when that differs from its name — e.g. a scrape escalating to the real browser (`scrape.escalating`, minutes ahead). `key` is a lang key the face renders localized. |
| `intent` | `items` | The turn's own intent list — the model planning itself out loud with the `intent` tool. Full state on every change: `[{id, goal, status: pending\|doing\|done\|failed}]`. The GUI paints it live (🧭 strip over the chat, band on the agents board); the CLI prints it as one line. |
| `metacog_nudge` | `n`, `sterile_tools`, `stalled_s`, `reason?`, `tool?`, `council?` | `council: true`: the question also offered the model a council (`call_council`) — there are two or more models and the tool is not blocked on this turn. `reason: "loop"` (with `tool`) when it was the same call returning the same result four times: the model is asked what it is doing wrong instead of the turn being cut. `reason: "last"`: there was nobody to hand the turn to, so after the first question came a second, firmer one. Otherwise: the stall detector asked the model to stop and rethink on iteration `n`: `sterile_tools` consecutive tools brought nothing new, or `stalled_s` seconds passed without progress. The question itself already reached the model as a user message — this event only lets the face say so. Note it needs **at least one sterile tool**: `stalled_s` alone never fires it, because a model thinking or working long is not stalled. |
| `plan_gate` | `iteration` | Code mode only. The model wanted to finish a turn that wrote nothing — neither into the project's memory nor into the code — with a reply shaped like a list of steps. The loop asked it once, as a user message, to WRITE the plan (the project's `future` note, one line per pending item, and at most a couple of `add_task` for what starts now) or to say it is not a plan, in which case the original reply stands. Once per turn, never for a turn that acted. |
| `reasoning_only` | `iteration`, `chars` | The model answered with reasoning only — `chars` of it — and stopped without a visible reply (not a cut by length: that one carries its own warning). The loop asked it once, as a user message, to answer in words; a second silence is reported as such. |
| `canvas_shot_attached` | `n` | canvas_see answered with a screenshot and the loop turned it into a real image message (`n` captures this round), appended AFTER the round's tool results — the base64 never reaches the digest and the model actually SEES the canvas. |
| `hands_begin` | `turn_id`, `session`, `own_pids` | The turn took the DESKTOP HANDS (first `hands_*` tool of the turn). A face with a window retires it (to the tray) and puts up its control (the HUD): from here on the model is clicking on the person's screen and the face must not raise, activate or open modal dialogs. `own_pids` are the processes whose windows the model never sees. |
| `hands_action` | `tool`, `target`, `ok`, `verified` | One desktop action happened. `verified` is `true`/`false` when something could measure it (pixels changed, an element's state or text), `null` when nothing could. Paint it on the control's tooltip and one line in the transcript. |
| `hands_end` | `turn_id`, `actions`, `stopped` | The turn released the hands (it ended, or was stopped): the face comes back. Emitted BEFORE the terminal event, because a face stops reading at `reply`. |
| `hands_consent` | `ack_id`, `turn_id`, `model`, `host` | The turn's model is NOT on this machine or the local network and a hands tool wants to send it the screen. Ask the person and answer on `POST /agent/plan_ack` with `decision: run` (allow, for this turn) or `cancel`. Only sent when `ack_mode` is `stream`; otherwise the tool is refused and told why. |
| `config_consent` | `ack_id`, `turn_id`, `changes`, `runs_program` | `edit_my_config` wants to write `config.ini`. `changes` is a list of `{section, key, old, new, delete}` with secrets already covered (`old: null` = a new key). `runs_program: true` = one of them is a `stdio:` line in `[MCP_SERVERS]`, a program that will start on this machine. Ask the person and answer on `POST /agent/plan_ack` with `decision: run` or `cancel`; nothing is written before the answer. Only sent when `ack_mode` is `stream`; otherwise the tool refuses. |
| `config_changed` | `changes` (`[{section, key}]`) | `edit_my_config` wrote `config.ini` and the MCP reloaded it. A face that keeps its own copy of the config re-reads it. |
| `tools_withheld` | `tools` | `load_tools` handed over a schema the payload will NOT carry: the tool exists globally but not in THIS turn's allowed set (a sub-agent's profile, or a tool reserved for a terminal sub-task because it owns a shared surface). The model is told so in the tool result itself, with what to do instead — a manual shown while the tool is hidden is how a turn grinds to `loop_broken`. |
| `intent_open` | `n`, `iterations` | The model was about to answer with `n` items of ITS OWN intent list neither done nor dropped, so it was asked once to close them — finish them, or mark what failed and what no longer applies. Once per turn, never a loop. A plan nobody closes is not a plan. |
| `intent_chase_ignored` | `iterations` | The reply to the open-list chase did no work — no tool ran, the list did not move — so it was a comment on the notice, not an answer: the answer the chase interrupted stands and the chase pair leaves the transcript. A chase fires only over real work (a list decorating a plain answer is not chased). |
| `resume_chase_ignored` | `iterations` | The reply to the question put after a mid-turn aside («is the request that started this turn done?») did no work — no tool ran — so it was a comment on the question, not an answer: the answer written before it stands, and the question with its reply leaves the transcript. |
| `look_check` | `n`, `iterations` | The turn made pictures (a result's `must_look`) that no `see_image` looked at, and it was about to answer: it is asked, once per unseen picture and three times a turn at most, to look before it describes them. |
| `context_overflow` | `prompt`, `reply`, `window` | The server refused the prompt for its window (prompt + reply reserve). The loop does not retry the same prompt; the face compacts the conversation and asks the question again, once. |
| `plan_gate_ignored` | `iteration` | The reply to the plan gate neither carried the keep-token nor wrote a change note (a journal line, a remark about the gate): it was an answer to the notice, not to the person, so the answer the gate interrupted stands and the pair leaves the transcript. |
| `intent_left` | `n`, `goals` | The turn is ending with `n` items of its own intent list still open, after the one chase. They are marked `unfinished` in a final `intent` event (sent just before this one) and their goals are listed here: a plan nobody closed is shown as such, never as work still coming. Arrives before `reply`. |
| `resume_check` | `iterations` | The user said something mid-turn (`/agent/inject`), the model handled it and was about to answer: it is asked once whether the request that STARTED the turn is complete, and told to carry on if not. Injected text itself arrives framed as an aside (`[WHILE YOU WORK …]`), never as a bare new request. Once per turn, never a loop. |
| `tick` | — | Heartbeat, every ~30s while the turn lives (emitted by the MCP's stream, not the loop). Paint nothing; it exists so a face's between-events read-timeout never mistakes a LONG SILENT ACTION for a dead turn. A stream with no tick for minutes means the MCP itself is gone. |
| `model_escalated` | `to`, `n`, `after`, `sterile_tools` \| `tool`+`times` | The turn was handed to another pool model, once. `after: "asked"`: the model was asked to rethink (a nudge, or the same call stuck on the same result) and stayed sterile after the question. `after: "loop_broken"` (with `tool`, `times`): the same call stuck again after it had been asked. Never a silent swap: show it. |
| `reply` | `reply`, `iterations`, `turn_record`, `transcript`, `last_user_sent`?, `verified`? | The terminal event. `last_user_sent` is the newest question AS IT WAS SENT to the model when `inject` glued this turn's blocks in front of it (the skill chosen, the working-memory inventory): **store it in place of the bare question**, or the next prompt diverges right there. Also on `cancelled` and `iteration_limit`. `transcript` is every message the loop appended this turn — assistant tool calls, tool results, the reviewer's notes, rejected drafts, framed asides — exactly as the model saw them. **Keep it in your history, before the final answer, verbatim**: the next prompt is then the last one plus one message, and the backend's prefix cache serves. Assistant messages in it are **as the model generated them**: content untouched, `reasoning_content` (or `reasoning`, whichever key the server used) and `tool_calls` as returned — a re-entered turn rendered into different tokens is a prefix cut exactly there. Keep the fields; never strip the reasoning. When no `transcript` comes, fuse `turn_record` into the answer instead. `verified` reports the definition of done (`verify`) when the turn had one. |
| `context_full` | `prompt_tokens`, `completion_tokens`, `iterations` | The completion was cut by length with (almost) nothing written: the context is full. An `error` follows at once — nothing is reviewed or redone, because a redo cannot buy tokens. |
| `metacog_close` | `n`, `sterile_tools` | Nobody to hand the turn to, the model was asked twice (the question, then a firmer `metacog_nudge` with `reason: "last"`) and stayed sterile: one tool-free pass follows whose words — what it tried, what blocks it, what it would need — are the `reply`. |
| `image_described` | `by`, `for`, `n` | `n` pictures were sent to a model that cannot see (`for`): `by` described them and the descriptions stand in their place. Said only when a picture is described the first time — the description is kept by the picture's hash and reused. |
| `image_not_seen` | `model`, `server`, `path`, `n` | A picture could not be shown to `model` and nobody described it: the model is declared without vision, or (`server` true) its server says it takes no images. The picture is kept as a file at `path`, the model is given that path, and the person is told once per turn. |
| `qa_skipped` | `why`, `iterations` | `why: "self"`: the reviewer would have been the very model that did the work (no `[CHAT] qa_model`, or the same one), and a model grading itself approves itself — so the turn was not reviewed, and the face says so. |
| `escalate_failed` | `why` | There was somebody to hand it to and the hand-over itself failed. Said, not swallowed; the turn carries on with the model it has. |
| `verify_run` / `verify_result` | `command`, `attempt` / `ok`, `output_preview` | The definition of done. |
| `qa_run` / `qa_result` | `round` / `ok`, `why`, `next`, `mode`, `reviewed`, `said` | The reviewer. `ok` is `true` (passed), `false` (failed, a redo follows) or `null` (NO verdict: the reviewer answered without an object or failed — the answer stands unreviewed, never approved). `why`/`next` are the reviewer's reason and what it asks for, on a pass too. `reviewed` is the answer it judged, whole; `said` is the reviewer's own text as it wrote it. `mode` is `grounded` (it read every step whole) or `contract` (the work did not fit `qa_budget_chars`: it read the steps only). |
| `orch` | `orch:{type,…}` | The orchestrator. Types: `start`, `plan`, `snapshot`, `subtask_start/done/retry/failed`, `subtask_acted`, `expansion_requested/approved/rejected` (a running plan asking to grow, see `allow_expand`), `replanning`, `synthesis_done`, `complete`, `cancelled`, `tick` (heartbeat, paint nothing), `note` (an i18n key again). |
| `plan_ack` | `ack_id`, `turn_id`, `subtasks` | **A plan is blocked waiting for you.** Answer on `/agent/plan_ack`. |
| `canvas` | `html`, `tool` | **The shared canvas.** A `render_html`/`dashboard_render` from inside a federated session does not go to the MCP: it becomes this, and it reaches the executor's GUI AND the originator's, so BOTH paint the same surface. A local child (a headless agent on this machine) emits it too. |
| `alert` | `title`, `message` | Same shape, same reason: the popup fires on both screens instead of running in the MCP with no visible effect. |
| `council` | `council:{type,…}` | The council deliberating (`members`, `first`, and `round`, `member` on the per-seat types). Types: `convene`, `speak_start`, `speak_done` (carries the member's whole text and `produced`, which is false when it spoke and produced NOTHING), `speak_failed`, `vote` (`verdict` is `placet`, `no` or `""` — and `""` is never counted as agreement), `round_done`, `note` (an i18n key), `done`, `cancelled`. |
| `council_ack` | `ack_id`, `round`, `answer`, `dissent[]` | **A council's vote came back split and it is blocked at its gate.** Answer on `/agent/council_ack`. It is never an approval to START — a council is convened deliberately — only whether to spend ANOTHER round. |

A council's terminal `reply` also carries `turn_record`: the
DELIBERATION — who examined the answer, what they argued, who voted and
why. It goes to the conversation's CONTEXT, not to the screen, which is the
same split `/multi` uses, so the model can answer «why did that member not
accept it?» later on. The answers are deliberately NOT in it — the final one
is the reply it hangs off, and a superseded draft was replaced on purpose.

Terminal, exactly one: `reply` (`reply`, `iterations`) · `cancelled` ·
`iteration_limit` · `error` (`error`, and `code` / `retryable` when the
server's failure has one). All four carry `turn_record` when there is one.

### Delegation, which is not about remote machines

When you list a tool in `delegate`, the MCP decides to call it and then hands
it **back to you** to run, so your interceptions survive — the GUI's embedded
terminal and its canvas. It happens on your own machine, every turn. The GUI
delegates `["*"]`; the CLI delegates nothing, because a terminal has no
terminal to intercept.

Three rules that are not optional:

- **`plan_and_orchestrate` is never delegated**, whatever you ask for
  (`fed_agent.NEVER_DELEGATE`): the orchestrator lives in the MCP only.
- **`hands_*` tools are never delegated** either: they keep turn state on the
  server (the machine's one hands session, the element ids they hand out, the
  events they emit), and through a client's bare `/call` every call would be a
  fresh session with no turn. A face asks for them like any other tool and the
  server runs them.
- **Execute delegated tools off the reader thread.** The orchestrator's
  sub-agents run in parallel and each may be waiting on one; doing it inline
  serialises them onto one pair of hands. Their `call_id` is namespaced per
  sub-task (`<sid>~<id>`) precisely so they cannot collide.

---

## `POST /memory/curate`

The facts half of the memory pass (`/memory/review` runs it too). Goes **fact by fact**: takes the fact's own words,
searches them in what was said *after* it was recorded, and only when something
turns up does it spend one model call asking "did this happen?". Minimum
context and a concrete question, instead of handing months of chat to a
model and asking what changed.

The quote is not written by the model. It is shown numbered lines and returns
**numbers**; the quote is assembled here, from the log. Making up evidence
stops being possible rather than being something the prompt asks it not to do.

`{"limit": 0, "kinds": [], "recheck_days": 21}` — `limit` 0 = self-sized (a
third of the pending pool), -1 = all, a number = that many; `kinds` empty = what
expires. Oldest expirable facts first (intentions and states, then durable ones
over 90 days).
A fact that has been asked about is not asked again for `recheck_days`.
A fact said **once**, never returned by a search, and not spoken of since for
`[MEMORY] fade_after_days` (60) fades with no model call: verdict `faded`,
action `fade`, archived like the rest (undoable, still found by a search).
Repeated or recalled facts never fade this way.
Every model call here waits as long as the chat does (`[CHAT] timeout`), never
less than 60-90 s.
Returns `{asked, checked, faded, proposals[], reviewed[], no_trace[], pending}` —
including what it looked at and got nothing from, because a review that only
shows its hits cannot be judged. It writes nothing to memory: everything it
finds lands in the proposals below.

## `POST /memory/review`

**The whole weekly pass, on demand.** The same function the scheduler runs, so
a manual run and the scheduled one cannot drift apart. Split by
REVERSIBILITY, not by confidence:

- **Lessons are merged, and the merge is APPLIED.** Every move is recoverable:
  the superseded rules are deactivated (`stale`, recoverable) and never
  deleted, the consolidated one is a new row, and `/memory/autolog/{id}/undo`
  puts the group back whole. Waiting for approval on something one click can
  take back is friction that buys nothing.
- **Facts are proposed and then applied by the pass itself**: an expired or
  vacuous fact is ARCHIVED, never deleted, logged in `/memory/autolog` and
  undoable from there. What the model files with `propose_memory_change` during
  a conversation is different: it waits in `/memory/proposals` for your answer.
- **Duplicated facts the same way**: the model GROUPS facts that say the same
  thing twice, the proposal carries every involved fact verbatim as its
  evidence, and applying it merges — the freshest row is rewritten with the
  merged text and absorbs the rest, undoable like any other change. This keeps
  the always-injected facts index from swelling.

Nothing in this pass can delete anything — not as a policy a prompt is asked to
honour, but by which functions it calls.

It also runs by itself on a thread, set by `[MEMORY] auto_review /
review_every_hours / review_facts` in config.ini (see CONFIG.md).

`{"limit": 0}` (facts per pass, same meaning as in `/memory/curate`) →
`{at, lessons:{looked_at, merged[], deactivated}, facts:{…as /memory/curate},
duplicates:{looked_at, proposed[]}}`. The «🔎 Curate memory» button runs THIS
endpoint.

## `GET /memory/review/progress`

Where a running pass is, for whoever watches it (the window's pilot light):
`{running, phase: lessons|facts|duplicates|apply, done, total, current,
elapsed, still, limit}`. `done`/`total` and `current` (the fact being judged)
move in the facts half; `still` is seconds since the pass last advanced and
`limit` how long one model call may take, so a stuck pass can be told from a
slow one. The scheduled pass reports here too.

## `GET /stats/tokens` · `POST /stats/tokens/reset` · `POST /stats/tokens/add`

The token meter: what each model has eaten, accumulated across restarts and
resettable. `GET` → `{since, total, models: [{model, name, sent, calls, prompt,
completion, total}], days: [{day, model, name, sent, calls, prompt, completion, total}]}`
— per-model totals most expensive first, PLUS the daily series: the store
keeps days (400 days of retention, oldest fall off), and weeks/months are
grouping, done in the page over this series. `reset` zeroes every row and
stamps `since`, so «how much since I started measuring X» always has an
answer. `add` takes `{model, prompt_tokens, completion_tokens}` — the GUI's
few direct model calls (session titles, the cron supervisor) report in
through it, so there is one meter. Counted at the funnels every call goes through
(`_llm_chat_with_tools`, `_llm_call_json`, Perplexity), keyed by the model id
the server says answered. A local server answers with the path of the weights
file, so the id it was SENT is kept beside it (`sent`, persisted), and `name`
is the alias of the config's `[MODEL:x]` whose model is that sent id (empty
when no section names it). Shown by the **📊 Usage** header button.

## `GET /memory/autolog` · `POST /memory/autolog/{entry_id}/undo`

What the pass did **by itself**, newest first, each with what it takes to put
it back: `{entries[], pending_undo, last_run}`.

`POST …/undo` reverses one entry whole — the merged rule is removed and the
rules it superseded go active again → `{ok, reactivated, removed}`. An entry
already undone is refused rather than applied twice.

## `GET /memory/proposals` · `POST /memory/proposals/{pid}`

What the weekly pass wants to change about your memory, and your answer. It
**proposes and never decides**: the pass reads the conversation log, and when a
stated intention looks resolved it files a proposal — with the LITERAL quotes
from the log that support it. Without quotes the proposal is refused at the
door: an old intention is not evidence that it happened.

`POST` takes `{"accept": true|false}`. Accepting is the only thing that touches
the memory (retire the fact, or replace it — "wants to visit Lisbon" becomes
"has been to Lisbon", and its kind stops being an intention). Rejecting is
remembered: the same proposal is never filed again.

## `POST /agent/tool_result`
`{turn_id, call_id, result}` → `{accepted}`. The result of a `client_tool_call`.
The loop is blocked on it.

**Read the answer.** `{"accepted": false, "error": "nobody is waiting (…)"}` means
this result went to an address with no reader — a `call_id` that is not the one the
loop filed, or a turn already cancelled or timed out. It is not a network hiccup:
the work was done and thrown away, and the turn behind it is either dead or about
to hang for the whole `client_tool_timeout`. Check the response and report it.

## `POST /agent/plan_ack`
`{ack_id, decision, feedback, goal, subtasks, allow_expand}` → `{accepted}`.

`decision` is `run` | `edit` | `cancel`. `feedback` is what to change, for
`edit`. `goal` is a definition of done for the whole plan, written in the
approval dialog; it reaches the turn that is already waiting. `subtasks` is **what you left in the
cards**: goals you rewrote, models and nodes you reassigned, minus the ones you
removed. An empty list is a legitimate answer — you emptied the plan, and the
orchestrator reads that as a no. `allow_expand` is the re-scaling tick, and it
is answered once, here: a plan can only grow by itself if you saw the box.

No answer within `[CHAT] plan_ack_timeout` (900s) cancels: losing a plan beats
fanning out to other machines with nobody watching. An unknown or expired
`ack_id` answers `{accepted: false, error}`.

## `POST /agent/council_ack`
`{ack_id, decision}` → `{accepted}`.

`decision` is `again` | `stop`. Asked ONLY when a council's vote came back
split, and never to start one: convening is deliberate, spending more
models on a matter that already has an answer is not.

No answer within `[CHAT] council_gate_timeout` (900s) stops the council and
hands back the answer with the disagreement signed by whoever holds it.
With no transport to ask on (`ack_mode` is not `stream`) or with nobody
watching (`unattended`), it is not asked at all and the same thing happens:
one round, and the dissent in writing.

## `POST /agent/stop`
`{turn_id}` → `{stopped, turn_id}` (or `{stopped: false, error}`). Stops
cleanly at the next checkpoint.

## `POST /agent/hands`
`{turn_id?, action}` — the person's control over the desktop hands from outside the turn: `pause` (every `hands_*` call waits), `resume`, `stop` (every further call refuses; the turn itself is stopped with `/agent/stop`, as always) or `status`. Empty `turn_id` addresses whoever holds the hands. Answers `{ok, turn_id, actions, paused, stopped}` or `{ok: false, error}` when nobody holds them.

## `POST /agent/inject`
`{turn_id, message}` → `{injected}`. Says something to a turn that is **already
running**: the text is appended as a user message at the top of the next
iteration and the stream emits `injected`. `{injected: false}` means that turn
is not running — queue the message instead.

## `POST /agent/memory_snapshot`
`{session, action: "reset"}` → `{reset, held}`. Drops a conversation's MEMORY
PICTURE. The first turn of a conversation (identified by `session`) freezes the
facts catalogue and the project memory block in the system prefix; every later
turn reuses the very same text, so the top of the prompt never moves and the
backend's prefix cache serves. What memory extraction writes during the
conversation shows up in the NEXT conversation — or after this reset, which the
GUI sends when a tab is reset or closed. A turn with no `session` computes the
blocks each time and places them at the end.

## `POST /agent/qa`
`{request, reply, tool_trace, endpoint}` → `{ok, why, next, mode}`. Reviews an
answer produced **outside** the loop. A broken reviewer never blocks a good
answer: any failure is a pass.

## `GET /agent/context_limit`
`?base_url=&api_key=&model=` → `{limit, source}`. In order: `[MODEL:x]
context_length` for the pinned alias, then the server itself (llama-server
`/props`, then vLLM `/v1/models`), then `[CHAT] context_length` as a last
resort. That order matters with a pool: one global number cannot describe
several windows. Cached per endpoint+model.
`limit: 0` means it could not tell — fall back, do not invent.

## `GET /agent/prompt_budget`

`?text=hello` → `{total_tokens, deferred_schemas, messages:{tokens,chars,blocks},
tools:{carried,of,tokens,heaviest[]}, by_block[]}`. **Where the tokens of a
«hello» go**, before blaming any part of it. Runs the real `prepare_turn_messages`
and assembles the tool payload the turn would actually carry — with deferred
schemas on that is a dozen manuals plus the shelf, not the whole registry.

`by_block` names each block by its own first line, heaviest first, so the facts
catalogue, the shelf and the identity prompt are separate numbers instead of one
total nobody can act on. Read-only: it prepares a turn and throws it away.

## `POST /memory/distill`
`{history, kinds:["facts","lessons"]}` → `{facts:N, lessons:N}`. Mines a stretch
of conversation for durable facts about the user and behavioural lessons, and
stores them. Chunked at ~6000 chars on message boundaries, each chunk told what
the previous ones already found.

## `POST /memory/compact`
`{messages, keep_recent, summary, endpoint, conv}` → `{compacted, messages, replaced,
summarised_total, since, tokens_before, tokens_after, recent_tokens,
recent_heaviest, why}`. Replaces the old part of a conversation with a
summary; the originals stay where they were, as rows of the conversation log.
The old part is summarised in slices of whole messages, the running summary
carried into each next slice, so a heavy head (tool results included) is
covered whole — nothing of it sits behind a fixed cut. `summary` is a summary
the face already has (the reply of a `compact: true` turn); without it the MCP
writes one. `conv` lists every id the conversation answers to, so what the
compaction archives belongs to that conversation and goes with it. Call this BETWEEN turns only, never while a loop is reading the
history: the GUI decides on the turn's first token report and compacts when the
turn has ended, with that tab's input blocked and its queue held (a `/compact`
typed during a turn waits in the tab's queue).
If the summary fails it returns your history untouched and `compacted:false`:
a half-summarised context is worse than a long one. **Say the sizes**:
`tokens_before` → `tokens_after` (estimates), and what the recent window that
stays untouched still weighs (`recent_tokens`) with its three heaviest blocks
(`recent_heaviest`: `{role, head, tokens}`), so a compaction that left the
recent window heavy can be told apart from one that did nothing.

## `POST /agent/context_map`
`{messages, code_project?}` → `{total_tokens, history: {messages, tokens,
by_role}, messages: {tokens, chars, blocks}, tools: {carried, of, tokens,
heaviest}, by_block: [{role, head, chars, tokens}], deferred_schemas}`.
What the face's NEXT prompt weighs, block by block, heaviest first: the
history is prepared exactly like a turn (fixed blocks, memory, skills, code
note) and thrown away. Estimates at ~4 chars per token; the meter has the
measured figure. Read-only. The faces expose it as `/context`; `GET
/agent/prompt_budget?text=` is the same weighing for a bare question.

## `POST /code/judge`

The Verify page's judge. Body: `{command, expect, output}` — `expect` is an
expectation **in words** («no errors», «go from 50 to 60 tok/s»); a model
reads the output and says whether it holds. Returns `{judged, ok, why}`:
`judged:false` (with `ok:null`) means nobody could judge — no endpoint, or an
unreadable answer — and the caller must say so instead of inventing a verdict.
Same judge the agentic loop's `verify_goal` uses.

## The whiteboard canvas: `GET /wb/app` · `/wb/api/…`
The page the CODE tab's 🧭 whiteboard page loads, and its reads, all at a whiteboard's pinned commits.
- `GET /wb/app?id=<id>&lang=<xx>` the canvas (no id: the list, the project at `root=` first); `GET /wb/static/…` its assets; `GET /wb/api/strings?lang=` the page's words (keys `wbc.*` of `lang/*.json`).
- `GET /wb/api/list[?project=|?root=]` · `GET /wb/api/{id}?version=` · `GET /wb/api/{id}/history`.
- `GET /wb/api/find?root=&what=` the whiteboard of that kind for that project (`overview`, `commit` at HEAD, `changes`), if one with content exists, and the job drawing that kind right now — what the page's buttons open before drawing anything.
- `GET /wb/api/{id}/staleness` has the code moved on: `{kind: overview|changes|commit|scratchpad, updatable, stale, pinned, current, ahead, worktreeChanged}` — a review of one commit is never `updatable`.
- `POST /wb/api/draw {root, what, model?, whiteboardId?}` an agent draws one in the background with `model` (a pool alias; `base` or empty = the base) (`what`: `changes` · `commit` · `overview` · `update` with `whiteboardId`, which brings that whiteboard up to date and is done only when it is no longer behind · `redraw` with `whiteboardId`, which draws it again from scratch reusing nothing read before, as a new version; one job per project and kind at a time) · `GET /wb/api/draw?root=` the jobs (`state` running/done/failed/cancelled, `whiteboardId` as soon as it exists, `alias` and `model` it draws with, `error`: `no_changes` / `no_parent` / `model_unknown` / `up_to_date` / `not_updatable` / `still_behind` / `empty` (it read and wrote nothing; the empty whiteboard is removed) or the reason) · `POST /wb/api/draw/{job}/cancel`.
- `GET /wb/api/{id}/watch` NDJSON: the snapshot (with `activity`) at once, then one line per change; a slow reader gets only the newest.
- `GET /wb/api/{id}/peek?file&startSide&startLine&endSide&endLine[&repositoryId&head&base]` the aligned rows of one selection ±3 (structural with diffr, else textual).
- `GET /wb/api/{id}/files` · `GET /wb/api/{id}/filediff?path=` every row of a file with diffr's collapsed bands · `GET /wb/api/{id}/file?file&side` · `GET /wb/api/{id}/commits`.
- `GET|POST /wb/api/{id}/progress` changed lines per file and lens, viewed marks (`{path, viewed, lensId?, ranges?}`).
- `GET /wb/api/{id}/resource/{rid}` a retained trace, image or map · `POST /wb/api/{id}/copy-context` the text "Copy for agent" puts on the clipboard.
- `GET /wb/host/{action}` answers 204: the page asks the application for `open` / `copy` / `external` by navigating there, and the host intercepts it.

A turn that creates or opens a whiteboard emits `tool_progress` with key `wb.open` and `args.id`; the GUI shows the tab.

## Messages the loop writes to the model, and how they are marked
Everything the harness itself tells the model — a nudge, a gate, the final-answer pass, the date block — is a `user`-role message (chat templates such as llama.cpp's and MLX's accept one system message only), so it opens with a tag that says who is speaking: `<INTERNAL HARNESS MESSAGE — no human wrote this; …>`, and the QA reviewer's verdicts with `<AUTONOMOUS QA REVIEWER, NOT THE USER>`. `fed_agent.harness_msg(text)` builds one, `is_harness(message)` recognises one, and `user_said(message)` is the single reader that returns only the words a person typed in a message (no memory catalogue, no date block, no attachments' text). A third face that injects its own blocks should mark them the same way; the date rides as a bracketed `[TODAY]` block in front of the question.

## `POST /call`
`{tool, arguments, code_project?, turn_id?}` → `{tool, result, duration_s}`. One tool, no
loop. If the tool turns out to be an orchestration it gets the safe context:
nobody to ask, so a local plan runs and one reaching other machines is
cancelled. `code_project` is the project open in the calling tab: with it, the
code/git tools and the shell-family file tools read an empty, `.` or relative
`path` inside that project (absolute paths are left alone). A delegated tool
reaches `/call` in its own request, so the turn's project has to travel with
it — the GUI sends it on every call. `turn_id` ties a delegated call to the
turn it belongs to, so tools that need the turn's state (attached images, the
working memory) find it.

## `GET /tools` · `GET /health`
`/tools`: the firewalled tool list in OpenAI format. `/health`: liveness and
which build is answering — `{status, version, label, started, pid, source,
platform, tools, tool_count, experience_count, notes_cats, cred_sites}`.

## `GET /firewall` · `POST /firewall`
`{blocked: [...], all_tools: {name: description}}` and, to set it,
`{blocked: [...]}`. The whole catalogue travels WITH its descriptions because
the screen that shows it groups by FAMILY — one tri-state tick per family, so
a policy is a handful of clicks — and searches by what a tool DOES on top of
that. The taxonomy is client-side (`tool_families.tool_category`): rules over
the registry, not a table of names. Persisted encrypted (store
`firewall`) and enforced on every route — `/tools`, `/call`, the loop,
delegation, a child's pack.

## `POST /child/tools_for`
`{capabilities: [...]}` → `{tools: [...]}`. What the capability words
(`web`, `research`, `files`, `terminal`, `code`, `write`, `none`) resolve to,
answered in ONE place. A face offering to build a child (a headless agent of
yours) shows the starting point those words mean without keeping its own copy
of the mapping.

## Federated sessions
`/agent/session-send`, `/agent/session-inject`, `/agent/session-stop`,
`/agent/session-close-inbound`, `/agent/session-events`,
`/agent/pending-approvals`, `/agent/resolve-approval`. Piloting another node's
agent, or answering one that pilots yours; the work runs in the MCP because the
vault (identity and keys) lives there.

- `POST /agent/session-send {peer, message}` opens or continues an OUTBOUND
  session with a contact.
- `POST /agent/session-inject {peer, message}` writes into an INBOUND session
  (one a contact opened with you): your own agent runs the turn, and its events
  go to your tab and back to the originator.
- `POST /agent/session-stop {peer}` cuts an outbound session;
  `POST /agent/session-close-inbound {peer}` closes an inbound one (the
  originator gets `session_closed`, the running turn is cancelled).
- `GET /agent/session-events` → `{events, shares, wake}`: everything buffered
  since the last call, in both directions, delivered once. Poll it.
- `GET /agent/pending-approvals` → `{pending: [{id, peer, message, analyze}]}`:
  incoming requests waiting for your yes; `POST /agent/resolve-approval
  {peer, approved}` answers one.

Besides the loop's own events, session events include `session_open`
(`peer`, `session_id`), `user_message` (`from`, `text`: what the other side
typed), `queued` (`peer`: the peer's turn waits behind another one),
`session_closed` (`by`) and `status` (`text`: e.g. the server asking to slow
down).

A turn a peer asks for can orchestrate too, but strictly: `unattended`, no ack
channel, so a local plan runs and one that would reach a **third** machine is
cancelled.

---

### Session events (`agent.event` envelopes)
The executor of a remote session forwards the loop's events to the originator
one envelope each, **from an outbox thread, in order** — never from the
model's thread. A send that fails is retried (a 429 for what `Retry-After`
asks, anything else briefly) and, if it still fails, **counted and logged**,
never dropped in silence; the terminal event always drains before the turn is
declared over. Every event carries `t_exec` (the executor's clock when the
event was born); the originator adds `t_recv` (when it landed) and `t_server`
(the envelope's `created_at`). `t_recv - t_exec` is the transport's share,
the gap between two events' `t_exec` is the model's: the GUI prints
« · +Ns transport» on the session lines when the lag is three seconds or
more, so a slow remote turn can be told apart from a slow model.

## Writing a third face

The short version: prepare nothing, reason about nothing, and stream.

1. `POST /agent/turn` with your `messages`, `inject: true`, your pinned skills,
   `delegate` set to whatever you can actually execute, and `ack_mode` set
   honestly — `"stream"` only if you really will answer.
2. Read the NDJSON. Paint what you can. Translate `prep`/`orch` **notes** from
   their keys, in your own language.
3. Answer `client_tool_call` on `/agent/tool_result`, off the reader thread.
4. Answer `plan_ack` on `/agent/plan_ack`.
5. Stop at the terminal event.

What stays yours: how it looks, what you intercept, and your session state.

### The prefix contract (the cache is the prefix)

The backend reuses the work done on a prompt's prefix for as long as that prefix
does not change. Everything above is arranged so that nothing above the newest
message moves; a face must not undo it:

1. **Never rewrite an earlier message.** No trimming, no re-summarising, no
   stripping of records from old replies. Append only.
2. **Keep the `transcript`** the `reply` event hands you, before the final
   answer. It is what the model already prefilled. Do not fuse `turn_record`
   into the answer when a transcript came (the transcript is the record).
3. **Send a stable `session`** (one id per conversation). The first turn with an
   id freezes the facts catalogue and the project memory in the system prefix
   (the *memory picture*); later turns reuse the very same bytes. When your
   conversation ends or resets, `POST /agent/memory_snapshot {session}` so the
   next one takes a fresh picture.
4. **Compact rarely, and on a message the person typed**, never inside a tool
   exchange (`/memory/compact` already walks the cut back to one).
5. **Paint the honest terminals**: `context_full` (nothing to redo), a
   `verify_result` with `blocked: true` (the check could not RUN — not red), and
   `resume_check` (an aside was handled, the original request is being
   finished).
6. Per-conversation policy rides in `blocked_tools`; the tools block the MCP
   builds is static for the turn whatever the model loads.

---

## All endpoints

Every route the MCP serves. The ones not described above are the plumbing the
GUI's memory, notes and data screens use; a third face needs them only to
offer the same screens.

**The turn and its controls** — `POST /agent/turn` · `POST /agent/tool_result` ·
`POST /agent/plan_ack` · `POST /agent/council_ack` · `POST /agent/stop` ·
`POST /agent/inject` · `POST /agent/hands` · `POST /agent/memory_snapshot` ·
`POST /agent/qa` · `GET /agent/context_limit` · `GET /agent/prompt_budget` ·
`POST /agent/context_map` · `POST /code/judge` · `POST /call`.

**Another of your machines** — `/tied/<device>/<path>`, any method: the same
request, asked of the server of one of your own machines (`device` is its
`device_info` id). It reaches that machine's own server through the
federation, sealed, and comes back as that server answered it — the same
status and bytes, or the same NDJSON stream when it streams, as
`/agent/turn` does. A tab tied to another machine uses it for everything
about the tab: `/tied/<device>/agent/turn` runs the turn there with its whole
conversation, memory, skills, project and tools, and its `turn_id` is that
machine's, so `/tied/<device>/agent/stop`, `…/agent/plan_ack`,
`…/agent/council_ack` and `…/agent/inject` reach it. Compaction
(`…/memory/compact`), the saved conversation (`…/call` with
`chat_session_save` / `chat_session_load`), the project's files and git
(`…/call` with `code_*` / `git_*`) all run there. The optional header
`X-Hoplita-Tab: <sync id>` names the tab, so that machine's window paints the
turn in it and takes a saved copy. Only between machines of one federated
identity; `/shutdown` and a second hop are refused (403), and a request over
~2.8 MB is refused (413).

**Tools and configuration**

| Endpoint | Body → answer |
|---|---|
| `GET /tools` · `GET /health` | See above. |
| `GET /firewall` · `POST /firewall` | See above. |
| `POST /reload-config` | `{}` → `{reloaded, api_model, api_url, warnings[]}`. Re-reads `config.ini` (see CONFIG.md); `warnings` says when a change needs a restart (`[MCP_SERVERS]`). |
| `POST /shutdown` | Closes the vault and exits cleanly. From localhost only (403 otherwise). |
| `POST /child/tools_for` | See above. |
| `POST /spawn/enroll` | `{server_url, token, handle}`: enrolls THIS instance as a child (a headless agent of its owner) with a spawn token generated in the owner's GUI. The CLI calls it at start with `--spawn`. |

**Memory: facts**

| Endpoint | Body → answer |
|---|---|
| `GET /facts/list` | → `{facts[], total, live, archived, archived_bytes, injected, outside, generic}`: every fact, and how many still reach the prompt (`injected`) or no longer fit its catalogue (`outside`). |
| `POST /facts/add` | `{text, tags[]}` → `{added, fact}`. Idempotent: the same text is not stored twice. |
| `POST /facts/search` | `{query, n_results: 3, min_score: 0.5}` → `{facts[]}`. |
| `POST /facts/inject` | `{query, n_results}` → `{facts[], count}`: what would be injected for that query (pinned facts first, then by relevance, within a budget). |
| `PATCH /facts/{id}` | `{text?, tags?, hits?}` → `{updated, fact}`. |
| `DELETE /facts/{id}` | → `{deleted}`. |
| `POST /facts/purge` | `{ids[]}` · `{archived: true}` (the whole archive) · `{all: true, confirm: "DELETE ALL"}` → `{deleted, left, archived_left}`. Deleting everything requires the typed word (`DELETE ALL` or `BORRAR TODO`). Deletions travel to your other machines on the next sync. |

**Memory: lessons, experience, curation**

| Endpoint | Body → answer |
|---|---|
| `GET /lessons/list` | → `{total, active, lessons[]}` (stale ones included). |
| `POST /lessons/search` | `{query, n_results: 3, min_confidence: 0}`. |
| `POST /lessons/add` | `{text, tags[], confidence: 0.7, source}` → `{added, lesson}`. An already-distilled lesson; a similar existing one is reinforced instead of duplicated. |
| `DELETE /lessons/{id}` | → `{deleted, remaining}`. |
| `POST /feedback` | `{goal, steps[], result, feedback, tags[], duration_s, partial_good_steps[], note}`. `feedback` is `ok` · `partial` · `fail` (`good`/`up`/`yes`, `bad`/`down`/`no` accepted). Stores the experience and queues lesson extraction (`[LESSONS]`); `note` is what the person said, kept apart. |
| `POST /experience/search` | `{query, n_results: 3}`. |
| `DELETE /experience/{id}` | → `{deleted, left}`. |
| `POST /memory/distill` · `POST /memory/compact` | See above. |
| `POST /memory/curate` · `POST /memory/review` | See above. |
| `GET /memory/proposals` · `POST /memory/proposals/{pid}` | See above. `GET` → `{proposals[], pending}`. |
| `GET /memory/autolog` · `POST /memory/autolog/{id}/undo` | See above. |

**Conversation log and notes**

| Endpoint | Body → answer |
|---|---|
| `GET /log/sessions` | → `{sessions[], total}`: the conversations in the searchable message log. |
| `POST /log/purge` | `{session}` forgets one conversation · `{all: true, confirm: "DELETE ALL"}` forgets everything → `{deleted, left}`. |
| `POST /notes/search` | `{query, category?, n_results: 5}` → `{results: [{category, title, id, score, content}]}`. Skills are notes too. |
| `POST /data` | `{manager, method, args[], kwargs{}}` → `{result}` or `{error}`. Direct access to the stores behind the GUI's screens: `manager` is `todo`, `notes`, `cred` (the credential vault) or `cron`, and `method` one of that store's methods. A change to a shared task or note is pushed to the contact it is shared with. |

**Token meter** — `GET /stats/tokens` · `POST /stats/tokens/reset` · `POST /stats/tokens/add` (see above).

**Federated sessions** — `POST /agent/session-send` · `POST /agent/session-inject` ·
`POST /agent/session-stop` · `POST /agent/session-close-inbound` ·
`GET /agent/session-events` · `GET /agent/pending-approvals` ·
`POST /agent/resolve-approval` (see above).

**Whiteboard** — `GET /wb/app` · `GET /wb/host/{action}` · `GET /wb/static/{name}` ·
`GET /wb/api/strings` · `GET /wb/api/list` · `GET /wb/api/find` ·
`POST|GET /wb/api/draw` · `POST /wb/api/draw/{job}/cancel` · `GET /wb/api/{id}` ·
`GET /wb/api/{id}/staleness|history|watch|peek|files|filediff|file|commits|resource/{rid}` ·
`GET|POST /wb/api/{id}/progress` · `POST /wb/api/{id}/copy-context` (see above).
