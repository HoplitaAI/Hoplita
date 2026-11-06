# Commands

Everything here can also be asked in plain words: the model has the tools
behind each command. A command is the short way, and some of them (the tab's
model, its judge, its definition of done) set something the model does not
decide by itself.

- [In the window's chat](#in-the-windows-chat)
- [In the CLI's interactive mode](#in-the-clis-interactive-mode)
- [CLI flags](#cli-flags)

## In the window's chat

Typed with `/` at the start of the message (`@` works too, as an alias). The
input box remembers what you typed with ↑/↓, and an unknown command is not sent
to the model as if it were chat. `/help` shows the same list grouped by
purpose.

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
| `/watch <seconds> <path>` · `/watch <seconds> cmd: <command>` | Watch a file or a command from this tab while it goes on with its work: when it changes, the report comes back to THIS conversation — into its running turn, or as its next turn. `/watch list` shows this tab's live jobs (watches and background runs), `/watch stop <id|all>` ends them, even while the tab is mid-turn and without going through the model; closing the tab ends them too, and the ✕ on a job in the ▦ overview is the same door. Stopping the turn does not end its jobs. The model has the same from its side (`background_watch`, `background_run`, `background_stop`), so asking in words works as well |
| `/queue` | What is waiting in this tab's queue. `/queue <n>` takes message n back into the box to edit it, `/queue del <n>` drops it, `/queue clear` empties the queue (and a pending compaction) |
| `/alert <message>` | Popup with a sound |
| `/creds` | Credentials manager (dialog) |
| `/help` | Help, grouped by purpose (the three memories: what is pinned and travels whole, the searchable RAG, and the code the model reads by itself) |

## In the CLI's interactive mode

`hoplita-cli` with no arguments opens a conversation in the terminal. These
commands act on that session; anything else goes to the model.

| Command | Description |
|---------|-------------|
| `/help` | The list of commands (`/?` too) |
| `/info` | The model, the MCP and its number of tools, the language, the limits |
| `/tools` | Every tool the MCP offers, with its description |
| `/facts` | The facts HOPLITA keeps about you |
| `/lessons` | The lessons it has learned |
| `/goal <text>` | This session's acceptance bar: the model reads it before starting and the reviewer judges against it. `/goal off` clears it |
| `/project <folder>` | Open a project for this session, as the Code page does: the model works on its files and gets the project's memory. `/project off` closes it |
| `/qa <alias\|off\|auto>` | This session's reviewer: a pool model, none, or the one in the config |
| `/model <name>` | Pin a pool model for this session. `/model base` returns to the base; `/model` alone shows the pool |
| `/skill …` | `add <path.md>`, `add <name>: <text>`, `pin`, `unpin`, `pinned` |
| `/tokens` | What each model has consumed. `/tokens reset` sets it to zero |
| `/context` | What the next prompt weighs, block by block, heaviest first |
| `/compact` | Fold the old part of the session's context into a summary now |
| `/memory` | The memory's weekly pass: pending proposals and what it applied. `/memory run`, `/memory ok <id>`, `/memory no <id>`, `/memory undo <id>` |
| `/clear` | Clear the screen |
| `/quit` | Exit (`/q` and `/exit` too) |

The CLI is one thread on purpose. A job the model starts in the background (`background_run`, `background_watch`) runs on the MCP; a report that lands while a turn runs enters that turn. One that lands between turns is announced on the prompt as it arrives, and handed to the model on the next Enter — or before whatever you type next, as the conversation's own message. The CLI has no `/watch`: to end a job from there, say so in words and the model ends it with `background_stop`; stopping the turn does not end its jobs.

## CLI flags

```bash
hoplita-cli                            # interactive conversation
hoplita-cli "what is my quota?"        # one question: the answer on stdout
journalctl -n 100 | hoplita-cli "what is failing?"
hoplita-cli --verify "pytest -q" "fix the tests" && git push
hoplita-cli --json "list my tasks" | jq -r .reply
```

| Flag | What it does |
|---|---|
| `--verify "<cmd>"` | Definition of done: the turn is not finished until `<cmd>` exits 0 |
| `--json` | Machine-readable result (`ok`, `reply`, `verified`) |
| `--quiet`, `-q` | No progress on stderr |
| `--yes`, `-y` | Approve multi-agent plans without asking |
| `--spawn --server <url> --token <t> [--handle <h>]` | Enroll this machine as a child and exit |
| `--help`, `-h` | Usage |

Exit codes: `0` fine · `1` the turn failed · `2` `--verify` never went green.
Piped input is treated as data, never as instructions.
