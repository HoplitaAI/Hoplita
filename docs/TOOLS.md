# HOPLITA — Agent tools

Reference for the **237 built-in tools** the LLM can call. The list is extracted from the real registry (`hoplita-mcp.py`, which also loads `fed_tools.py` for the federation and `wb_tools.py` for the whiteboard); if a tool is not here, it does not exist. The sections follow the families the firewall groups its checkboxes by (`tool_families.py`).

- **Argument convention**: `arg` required · `arg?` optional · `—` takes none.
- **Who runs them**: all of them are served by the **MCP** (`GET /tools`, `POST /call`). A GUI turn asks the MCP to delegate tools back to it, and the GUI runs in its own process the ones whose state lives there: `run_command` (in the embedded terminal, when there is one), `render_html`, `dashboard_render`, `canvas_eval`, `canvas_see` and `canvas_undo` (the canvas), and `sticky_note` (the tab's note); everything else it hands back to the MCP. Never delegated, whatever the client asks: `plan_and_orchestrate`, `call_council`, `intent`, the `hands_*` and the `wb_*` tools. The GUI only draws a plan and answers its approval.
- **GUI side effects**: `search_rag` merges in the hits from local documents; `alert` opens the popup; the cron, notes and TODO tools refresh their panel; `generate_image` and `edit_image` paint their result on the canvas. `browse_web` leaves with the user's literal request appended to its `task`.
- **Pictures paint**: local `<img src>` paths in `render_html` and `generate_pdf` are embedded in the page as data URIs, so evidence images show on the canvas and print in the PDF; `see_image` puts a local image in front of the model's own eyes.
- **Firewall**: the blocked-tool list lives encrypted in SQLCipher (store `firewall`) and is enforced in `/tools`, in `/call`, in the agentic loop, in delegation and in the GUI. A blocked tool cannot be reached by any route. **Per tab too**: right-click a chat tab → firewall → the same picker, by family; what it blocks applies to that conversation only (sub-agents included), on top of the general list, and travels as `blocked_tools` on the turn. Mind the shell: blocking the web family does not stop `run_command` from reaching the internet with `curl`; block «System and files» as well when the web must really be off.
- **Arguments are checked at the door**: in the loop, in `/call` and in a background job, before the tool runs, a name the tool's schema does not declare is refused with the names it does declare, and an argument declared as an object or an array of objects that arrives as something else (a list sent as JSON text) is refused with the shape it must have. Arguments that are not JSON (an unescaped quote inside a long string, or a call the output limit cut before it closed) are refused saying so, where the text broke and how it ended, never read as no arguments. Nothing is renamed or converted, and nothing is done. A schema that admits other names (`additionalProperties`), or declares none, is not checked for names.
- **Deferred schemas**: with `[AGENTIC] defer_tool_schemas` on (the default), the model sees most tools by name and family only; `load_tools` expands the schemas it needs, and the loop's `use_tool` bridge (`name` + `arguments`) calls any catalogue tool without changing the tool block. The loop unwraps `use_tool` before every gate, so the firewall, the profile and the record see the real tool. `use_tool` is part of the loop, not of the registry, and is not counted above.
- **This is not the final list**: every server in `[MCP_SERVERS]` adds its own tools, named `server.tool`, with the description prefixed by `[server]`. Each external server is its own firewall family.
- **Picture tools need a server**: with `[IMAGE] base_url` empty, `generate_image` and `edit_image` are not offered to the model.


## 📂 System and files

| Tool | Args | What it does |
|---|---|---|
| `exec_command` | `command` `cwd?` `timeout?` | Runs a shell command ON ITS OWN, outside the shared terminal: nobody watches it, its whole output goes to a file (`output_path`; the tail comes back with `exit_code` and `duration_s`), and several can run at once. The terminal is one and serial; this is the second door, for checks, builds and suites the person does not need to see run. Through `background_run` it does not even hold the turn. Blocked whenever `run_command` is: it is the same door. |
| `run_command` | `command` `cwd?` `timeout?` | Runs a system command. Pass `timeout` in seconds for anything that is not instant (installs, builds, migrations); a command cut short returns exit code -1 with partial output. |
| `read_file` | `path` | Reads a text file. A PDF or an office document is refused with the tool that reads it (`pdf_info`, `office_info`): read as text they are mojibake. |
| `read_folder` | `path` `extensions?` | Reads every file in a folder (text, images, PDFs, Word/Excel/PowerPoint as their text) for batch processing. |
| `list_dir` | `path?` `recursive?` | Lists a directory, optionally recursively. |
| `see_image` | `path` `max_width?` | Looks at a local image file (png/jpg/gif/webp) with the chat model's own eyes: it is attached to the conversation as a picture. Use it instead of OCR through the shell. Needs a vision-capable model; two images per round at most. |
| `generate_pdf` | `content` `title?` `output_path?` | Generates a PDF from markdown (document styling) or full HTML (printed as-is, e.g. what `render_html` painted), rendered by a real browser with formatted tables and code. |
| `pdf_info` | `source` `password?` `pages?` `text?` `render?` `dpi?` | Inspects a PDF before shaping it: pages and sizes, outline, metadata, form fields, printed-folio **layout detection** (two-up scans, unnumbered preliminaries and the printed-to-physical offset), text by page or edge band, and page-to-PNG render. Read-only. |
| `pdf` | `source` `output` `steps` `password?` `metadata?` | Reshapes a PDF with a **pipeline** of steps applied in order: `split_two_up`, `select` (physical or printed numbering), `merge`, `rotate`, `crop`, `scale`, `n_up`, `stamp`, `watermark`, `page_numbers`, `overlay_pdf`, `fill_form`, `encrypt`. The result reports every step and every automatic decision. Cover-and-write-over editing is visual only: true text reflow and forensic redaction are not claimed. |
| `office_info` | `source` `range?` `sheet?` `find?` `match_case?` `view?` | Reads a Word, Excel or PowerPoint file (`.docx` `.xlsx` `.pptx` and their macro twins `.docm` `.xlsm` `.pptm`): a document's **blocks** by index (paragraphs with their style, tables), a workbook's sheets and a cell range with **values and formulas** (merged cells listed), a deck's slides with every shape's id, name and text plus the speaker notes. `find` says where a text lives. `view=true` writes the file as one HTML page and paints it on the canvas: content faithful, layout approximate. Read-only. Legacy `.doc` `.xls` `.ppt` and OpenDocument are refused by name. |
| `office` | `source` `output` `steps` `accept_loss?` | Edits an office file with a **pipeline** of steps applied in order, written to `output` (the source itself to edit in place). Word: `replace` (body, tables, headers and footers; the replacement keeps the format of where the match began, also across the runs Word splits text into), `set_paragraph`, `insert_before`, `insert_after`, `append`, `delete`, `set_cell`, `add_row`, `add_table`. Excel: `set` (a value starting with `=` is a formula), `set_range`, `insert_rows`, `delete_rows`, `insert_cols`, `delete_cols`, `add_sheet`, `rename_sheet`, `delete_sheet`, `replace` (text cells only). PowerPoint: `replace`, `set_text`, `add_slide`, `delete_slide`, `move_slide`, `notes`. A step that fails writes nothing; overwriting keeps the previous file in `office-backups`. What the library cannot carry is measured on every write (the source opened and saved with no step, its parts counted by kind): if something would be dropped it is named, and the source is not overwritten without `accept_loss`. Moving rows or columns, or renaming a sheet, does not rewrite the formulas that point at them, and the result says so. Formulas written here get their value when the file is opened in Excel or LibreOffice. |
| `alert` | `title` `message` | Raises a popup alert to the user. |
| `get_agentic_config` | — | Shows the agent configuration. |
| `see_my_config` | `section?` | HOPLITA's own `config.ini`: where it is, every section with its keys, and with `section` that section's values. Secrets (keys, passwords, tokens, and keys inside URLs or command lines) come back covered: set or empty, and how long — never the value. For `[MCP_SERVERS]` it also says which servers are connected and how many tools each brought. Refused on a turn from another machine. |
| `edit_my_config` | `section` `key` `value` · or `changes` | Changes `config.ini` — one key, or several under `changes` (a list of `{section, key, value}` or `{section, key, delete: true}`) applied together under **one** approval: adding a pool model is `[MODELS] <alias> = true` plus its `[MODEL:<alias>]` section in one call. The person approves the exact changes first (secrets shown covered; a `stdio:` line is flagged as a program that will start on the machine); with nobody to ask (a cron, a bare `/call`) or on a turn from another machine it refuses. Comments and every other key are kept, a `.bak` is left beside the file, a change that would leave it unreadable is not written, and the file is hot-reloaded. In `[MCP_SERVERS]` the server added, changed or removed is connected or dropped on the spot: its tools are usable **in the same turn**. Writing back a covered secret is refused. |
| `read_my_docs` | `query?` `section?` `file?` | The manual that shipped with this install (`docs/*.md` and the README): with no arguments the outline, with `query` the sections where the words appear (with the matching lines), with `section` that one section's text. It is the copy `--update` refreshes, so it always describes the version that is running. |
| `load_tools` | `names` | Returns the full schema of tools the model sees listed by name but not expanded (deferred schemas). Ask for every tool needed in one call: each expansion changes the tool block and costs a re-read of the prompt cache. |


## 💻 Code and git

| Tool | Args | What it does |
|---|---|---|
| `code_projects` | — | Every code project this install knows: name, path, whether the folder still exists, how many notes its memory holds and who it is co-worked with. Read-only. |
| `code_project_name` | `path?` | What a code project is called here and whether it already has an identity (memory folder + task project). Read-only: the name is chosen by the user, never by the model. |
| `code_index` | `path?` `name?` | Indexes a project: tree, languages, sizes. **Use it first.** Opening a project also creates its memory (notes folder + task project); `name` is passed only when the user chose one. |
| `code_toolchain` | `path?` | Which languages a project uses and which linters / language servers are missing for them, with the exact install command. |
| `code_lsp_status` | `path?` | Which language servers are running for the project and which are missing. |
| `code_symbols` | `query?` `path?` `limit?` | Where a name is **declared**, across the whole project, without reading a file: one cached regex pass over the tree, no language server and no model. Empty query = the project's shape; with a name, exact matches first with `file:line`, the declaration line, where its body **ends** and the exact `code_read_range` that reads it whole (split into the reads that fit when it is bigger than one result). The `ƒ` column of the CODE tab searches with it too. |
| `code_search` | `path` `query` `file_pattern?` `cursor?` `per_scope?` | Searches a literal text (case-insensitive substring, not a regex) across the project, whatever its size. The counts are complete: `hits_total`, `files_total`, `by_dir` (where the hits are, by first directory) and `by_file`; GNU grep does the counting when the host has it (the Linux kernel, 86k files, in 8 s), the Python scanner otherwise. The hits come in pages grouped by function: `functions` lists each function of the page with its count, its span and the exact `code_read_range` that reads it whole, `results` keeps five lines per function (`per_scope`, 0 for every line, as the window's search box asks). Inside a turn a page is sized to what one result may carry, and a function bigger than a page continues on the next; `next` is the call for the following page (its `cursor`), to be called until it is null, or the search is narrowed with `path`, `file_pattern` or a longer text. A cursor from another search is refused. When the function cannot be told the hit says so and that the line may still be inside one. The grep is a fixed command run without a shell: the model chooses the text and the path, never an option or a program; the text goes after `--` as one literal argument (`-F`), so a `;`, a `|`, a `$(` or a leading `-` in it is text; FIFOs, devices and binary files are skipped; a text outside ASCII and a host without grep use the Python scanner, which skips the same files, so the two engines answer alike. An empty text or one with a line break is refused. |
| `code_read_smart` | `path` `search?` | Reads a file whole only when it is small (up to 24,000 characters, about 600 lines); otherwise its structure, the first and last lines, and how to get the rest. `search` returns the places a literal term appears. |
| `code_read_range` | `path` `start_line` `end_line` | Reads a specific range of lines. A range bigger than one result may carry this turn comes back cut on a whole line, with `cut: lines A-B of N`, how many lines of this file fit in one result, and `next`, the exact call that continues. |
| `code_outline` | `path` `content?` | What a file declares and on which line (functions, classes, methods), from the language server when there is one, from a per-language regex otherwise, saying which. |
| `code_deps` | `path` | Extracts a file's imports and exports. |
| `code_references` | `path` `symbol` `project_path?` | Where a symbol is used. **Semantic** through the language server when one is installed (`source: "lsp"`); a **text search** otherwise (`source: "text search"`), which misses aliased imports and dynamic calls. Check `source` before trusting a count. |
| `code_definition` | `path` `symbol` `project_path?` | Where a symbol is **defined**, semantically; resolves aliased imports back to the original. Needs a language server and says so when there is none. |
| `code_diagnostics` | `path` `content?` | What the language server reports as wrong in a file right now, including unsaved edits passed as `content`. Answers `available: false` with a reason when there is no server. Unlike `code_lint`, which reads from disk. |
| `code_lint` | `path` | Real diagnostics from disk: type errors (pyright), unused imports (ruff), static analysis. |
| `code_apply_patch` | `path` `changes` | Applies several exact-match edits atomically: each `old` is replaced by `new`, and if one fails none is applied. An `old` must occur exactly once; for identical copies, `expected: N` replaces all N and refuses any other count. Creates a file when `old` is empty. In a git repository it takes a checkpoint first. |
| `code_write_file` | `path` `content` | Writes a whole file, taking a checkpoint first so it can be undone. For surgical edits use `code_apply_patch`. |
| `code_undo` | `path?` `steps?` | Undoes the last edit(s) made with `code_apply_patch` / `code_write_file` from the checkpoint taken before each; files the patch created are removed, changes the user made meanwhile are left alone. |
| `code_checkpoints` | `path?` | Lists the undo points recorded in this session for a project, newest first. |
| `code_reject_hunk` | `file` `hunk` `path?` `header?` `patch?` | Throws away one hunk of a change and keeps the rest. Pass the file, the hunk's index and its `@@` header (or the hunk itself as `patch`), so a moved diff is detected. |
| `code_propose_verify` | `path?` | Gathers the evidence (build files, test files, languages) to **propose** a definition of done for a project, plus the rules a good one follows. It only proposes; the user decides. |
| `git_status` | `path?` | What is modified, staged and untracked, and on which branch. |
| `git_diff` | `path?` `files?` `staged?` `since_checkpoint?` `stat_only?` `commit?` | The change as a patch. `since_checkpoint=true` compares against the snapshot taken before the model's first edit; `commit=<sha>` compares the current tree against that commit; `stat_only` gives the summary. |
| `git_show` | `sha` `path?` | One commit, whole: its metadata and the patch it introduced. |
| `git_log` | `path?` `limit?` `files?` | Recent commits, newest first, with author and age. |
| `git_branch` | `path?` `name?` `base?` | Lists branches, or creates one (from `base`) and switches to it when `name` is given. |
| `git_switch` | `name` `path?` | Switches to an existing branch (main included). Refuses when uncommitted edits are in the way, and names them. |
| `git_commit` | `message` `path?` `files?` `allow_main?` | Records a commit. By default only the files edited in this session, not everything dirty in the tree. Never pushes. Refuses to commit on main/master unless `allow_main` (only on an explicit request). |
| `git_merge` | `branch` `path?` `into?` `delete_branch?` | Merges a work branch into the trunk (`main` by default) once committed and verified. Refuses with uncommitted changes; a conflicting merge is aborted clean and the files are named. |


## 🌐 Web

| Tool | Args | What it does |
|---|---|---|
| `search_web` | `query` | Searches the web. Returns `kind:answer` (a written answer with citations: Perplexity, Exa in answer mode, Tavily) or `kind:sources` (raw sources to synthesize: Firecrawl, Exa). The engine is `[SEARCH] provider`. |
| `scrape_url` | `url` `max_chars?` `escalate?` `depth?` | Reads a URL through a cascade: crawl4ai (runs JavaScript), then plain HTTP, then browser-use if needed. With `depth` 1-3 it also follows links on the same domain. |
| `browse_web` | `task` `url?` `headless?` `max_steps?` `endpoint?` | Real agentic browsing (browser-use): logins, forms, clicks, content behind interaction. One page load and one model call per step, so it is slow and expensive: last resort after `search_web` and `scrape_url`. `endpoint` names the model that drives the browser when `[BROWSER]` has none. |
| `screenshot_web` | `url` `headless?` `output_path?` | Saves a web page as a PDF on disk. |
| `call_api` | `url` `auth_from?` `method?` `headers?` `body?` `auth_type?` `auth_token?` `auth_key_name?` `auth_key_value?` `query_params?` `timeout?` `save_response?` | Calls any REST API (bearer, api_key header or query, basic auth). With `auth_from` the key is taken from the vault entry of a site saved as kind `apikey`, so a skill names the key instead of carrying it. A binary body (an image, a PDF, bytes) is not returned as text: the answer gives its type and size, and `save_response` writes it to a file. |


## ✉ Mail

A mailbox: IMAP to look, read and move, SMTP to send. `account` is the name of a credential of kind `mail` (address, password, IMAP server, SMTP server; a server left empty is taken from the address), added in the credential manager; with one mailbox stored it can be left out. The model names the mailbox and never sees its password. TLS always: `imaps` on 993 and SMTP on 465 by default, or `imap://host` / `smtp://host:587` with STARTTLS required, and the certificate is verified. Gmail takes an app password (2-Step Verification on, then myaccount.google.com/apppasswords); Microsoft accounts require OAuth over IMAP and are not supported.

| Tool | Args | What it does |
|---|---|---|
| `mail_folders` | `account?` | The folders, each with its role (`inbox` `junk` `trash` `sent` `archive` `all` `drafts`, read from the server's SPECIAL-USE flags, so Gmail's localized names resolve) and its message and unread counts. |
| `mail_list` | `account?` `folder?` `limit?` `unseen?` `since?` `before?` `from?` `to?` `subject?` `text?` | The newest messages of a folder (default INBOX, at most 100), each with `uid`, sender, subject, date, `seen` and a snippet of its text, optionally searched. Dates are `YYYY-MM-DD`. A uid belongs to its folder. |
| `mail_read` | `uid` `account?` `folder?` `max_chars?` | One message whole: headers, its text (an HTML-only message turned to text), its attachments by name, type and size. |
| `mail_move` | `uids` `to` `account?` `folder?` | Moves messages to a folder or a role (`junk` `trash` `inbox` `archive`), and returns their `new_uids` there. It is the only way mail leaves a folder: there is no permanent delete, so taking spam out is a move, and moving the `new_uids` back to `inbox` undoes it. On Gmail a move to `trash` is a real delete (Gmail empties it after 30 days); removing a message from INBOX any other way only archives it. |

| `mail_send` | `body` `to?` `subject?` `cc?` `bcc?` `html?` `attachments?` `reply_to_uid?` `folder?` `from_name?` `save_sent?` | Sends from the mailbox's address, now: recipients, a plain-text body (an HTML version optional), files on disk attached by path (25 MB in all). With `reply_to_uid` it answers that message: threaded (In-Reply-To, References), `Re:` and the recipient taken from it unless given. A copy goes to the Sent folder (Gmail keeps its own). There is no confirmation step: whether a tab may send is the firewall's decision, and the tool carries the firewall's system badge. |

Reading never marks a message read: folders are opened read-only and bodies fetched with `BODY.PEEK`.

## 🖱 Desktop hands

The model working on the user's own screen. Two layers under one contract: the **element layer** (accessibility: windows, their widget trees, pressing a button by its own action, setting a field through its editable interface) and the **pixel layer** (capture and raw pointer/keyboard input), which is what *verifies*: after each action a capture is compared with the one before, so a press that did nothing is reported as one. On Linux the element layer is AT-SPI; the pixel layer is chosen from the session (an X display: pointer library and grabber; Wayland: the compositor's screenshot tool, and an input-injection helper when installed). On Windows the element layer is UI Automation (the `pywinauto` binding). On macOS it is the accessibility API (AX) through ctypes, nothing to install: windows and trees come from the applications themselves, a button is pressed by its own action with no pointer moved and no focus taken, a field is set through its value; roles are folded into the same vocabulary the other platforms use, one accessible name is computed by one precedence (title, description, a static text's value, the placeholder, the static-text children of a control labelled by them), and unnamed containers do not consume depth, so a web view wrapped in anonymous groups still fits the budget. Accessibility must be granted to the process that runs the server; the status says which permission is missing. One turn holds the hands per machine; a second is told who has them. The app's own windows are never listed. Every call is gated on where the turn's model runs (`[HANDS] remote_model`). The GUI retires to the tray on `hands_begin` and shows the HUD, a small hoplite: one click stops the turn.

| Tool | Args | What it does |
|---|---|---|
| `hands_status` | — | What this machine offers: element layer, capture, raw input, session, who holds the hands, and the recipe to follow. Call it first. |
| `hands_windows` | — | The desktop's windows (application, title, active, minimized) with ids for `hands_tree`, across every virtual desktop; never the app's own windows. |
| `hands_tree` | `window` `depth?` `max_items?` `query?` | The elements inside a window with ids, states and actions; `query` filters by name, role or text. Ids are valid for the current turn. |
| `hands_click` | `element?` `x?` `y?` `button?` `double?` | Presses an element by its own action, or clicks at `x, y` when raw input exists, read in the **last** image the model was shown (`hands_screenshot` / `hands_verify`): its scaling and cropping are undone. Returns `verified` (true, false, or null when nothing could measure it). |
| `hands_type` | `text` `element?` | Sets an element's text through its editable interface (and reads it back), or types into the focus with raw input. |
| `hands_key` | `keys` | Sends a key combination (`ctrl+s`, `Return`). Verified only by the pixels it changed. |
| `hands_screenshot` | `window?` `region?` `max_width?` | The screen, a window or a region, attached as an image the model sees. Only for a model declared vision-capable (`[MODEL:<alias>] vision`, else `[CHAT] vision`); otherwise it says so and points at `hands_tree`. |
| `hands_verify` | `expect_text?` `element?` `window?` `with_screenshot?` | Checks an outcome: searches `expect_text` in an element, a window or everywhere, measures the pixel change since the last capture, and attaches a fresh capture. |


## 📝 Notes and skills

| Tool | Args | What it does |
|---|---|---|
| `add_note` | `category` `title` `content` | Adds a technical note to an existing category. Not for personal facts (that is `add_fact`). |
| `update_note` | `category` `note_id` `title?` `content?` `changes?` | Edits a note in place, keeping its id: `content` rewrites it, `changes: [{old, new}]` edits a long note without rewriting it (each `old` must match exactly once). |
| `delete_note` | `category` `note_id` | Deletes a note by category and id. |
| `search_conversation` | `query` `regex?` `context?` `k?` | For a sub-agent of a plan: searches what was said in the conversation its task comes from (the messages the turn put in front of its model, filed while the turn lives): the passages that mention the words or match the pattern (`regex=true`), with `context` paragraphs around them. Its brief is a summary; this is the original. From the turn itself there is nothing behind to search. In every profile that calls anything, with `search_working_memory`, and its schema always travels with a sub-agent. |
| `search_working_memory` | `query` `title?` `regex?` `context?` `k?` | Searches inside the full texts of the large results this conversation digested: the passages that mention the words (ranked by how many of the rarer ones they cover) or match the pattern (`regex=true`), each with `context` paragraphs around it (default 1) and its lines, for `read_note`. Always carried with the model. |
| `read_note` | `category` `title?` `note_id?` `start_line?` `end_line?` | One note by title (exact, then unique substring) or id: whole when it fits, otherwise by lines (`start_line` / `end_line`, with the exact call for the next window). A miss answers with the titles that exist. |
| `search_notes` | `search_term` `category?` | Searches the technical notes only (configs, commands, code). Each hit is the passage where the words are, with a paragraph either side and its lines (`read_note` reads around them); a note is also found by its title. The action log only when named as `category`. For every store at once use `recall`. |
| `list_categories` | — | Lists the note categories. |
| `use_skill` | `search_term` `category?` | Retrieves a saved skill by its title (the titles are listed in the model's context); `category` optionally names the folder. |
| `skill_folder` | `category?` `on?` | Marks a note folder as **skills**, or unmarks it. The default skills folder (`[BROWSER] default_skill_category`) is always in; any other (a shared folder, a subscribed one, a project's) joins on demand, and `use_skill` searches all marked folders. The mark lives with the notes, so it syncs. No `category` = report which folders are skills. |


## 🧠 Memory (facts, lessons, experience)

| Tool | Args | What it does |
|---|---|---|
| `recall` | `query` `terms?` `n_results?` | Searches everything the user has recorded **or ever said** in one call: facts, lessons, notes, tasks, appointments **and the verbatim log of every past conversation**, the log searched by moments (the burst of messages where the terms co-occur). Pass `terms` with the words the record itself would contain. |
| `search_rag` | `query` `top_k?` | Searches the knowledge base loaded in the conversation: attached documents (PDFs, files, URLs), notes and experience; the rest of a document that was cut is here. |
| `add_fact` | `text` `kind?` `tags?` | Stores a fact the user confirmed about themselves or their world (a name, a mapping, a preference). `kind` marks an intention or a passing state so it can be retired later. Related existing facts come back so a superseded one can be fixed. |
| `edit_fact` | `id` `text?` `tags?` | Corrects a stored fact by id (`text` and/or `tags`). |
| `delete_fact` | `id` | Deletes a fact by id. |
| `list_facts` | — | Lists all facts, for consolidation. |
| `search_facts` | `query` `n_results?` | Searches the facts only. Rarely needed: the relevant ones are injected automatically every turn. |
| `propose_memory_change` | `target_id` `action` `quotes` `new_text?` `why?` | Proposes retiring or rewriting one of the user's facts; the user decides. Requires `quotes`, literal lines from the conversation log that support it. |
| `add_lesson` | `text` `tags?` `confidence?` | Adds a lesson (a rule distilled from past feedback, typically consolidating several). |
| `deactivate_lesson` | `id` | Marks a lesson stale without deleting it (recoverable). Prefer it over deleting. |
| `delete_lesson` | `id` | Deletes a lesson by id, permanently. |
| `list_lessons` | `include_stale?` | Lists all lessons, for consolidation. |
| `search_lessons` | `query` `n_results?` `min_confidence?` `exclude_stale?` | Searches learned lessons only: what worked and what to avoid. |
| `search_experience` | `query` `n_results?` | Searches past experience (distilled earlier turns). |
| `list_experience` | `limit?` | Lists stored experiences (goal, verdict, tags, date), newest first. |
| `working_memory_forget` | `session?` `sessions?` `orphans?` | Drops a conversation's working memory (the full texts of big tool results it digested). Called when a chat is reset or a session deleted; `orphans=true` also drops what belongs to no conversation. |
| `intent` | `set?` `add?` `done?` `failed?` `doing?` | The turn's own intent list: the model plans out loud and crosses items off live on screen (the strip over the chat, a band on the Agents board, one line in the CLI). Handled inline by the loop: always available, never delegated, present in every sub-agent profile. |


## ✅ Tasks and projects

| Tool | Args | What it does |
|---|---|---|
| `add_task` | `title` `description` `priority?` `progress?` `project?` `depends_on?` | Adds a task to a project (default `general`); the TODO tab's project view draws it as a box with its % and dependency arrows. |
| `update_task_progress` | `task_title` `new_progress` | Sets a task's progress, 0-100 (the task is matched by title). |
| `task_depends` | `task_title` `depends_on` `project?` | Sets which tasks a task waits for (replaces the list; `[]` clears it). The board reads it as READY or BLOCKED. |
| `get_todo_context` | — | Every project's tasks with progress, dependencies and READY/BLOCKED, plus the appointments. |
| `add_todo_project` | `name` | Creates a project to group tasks and appointments. |
| `delete_todo_project` | `name` | Deletes a project; whatever it held moves to `general`. |
| `list_todo_projects` | — | Lists the projects (`general` always exists). |
| `project_journal` | `path` `entry` `title?` | Appends a dated entry to the open code project's journal: what happened (a decision taken, a trap found, what changed and why). The journal is a note in the project's own folder, which holds the project memory (`rules`, `spec`, `future`, `journal`, plus `skill: …` notes), read at the start of a conversation and again when it changes; it syncs and federates like any notes folder. The journal is never rewritten, only added to. |
| `project_write` | `path` `note` `text` `replace?` `done?` | Writes one of the open project's notes: `spec` (a settled truth, short), `future` (what is still pending, one line per item, never what was done) or `journal` (a dated entry). Appends; `replace=true` rewrites `spec` or `future` whole, never the journal. `done=true` with `note=future` closes the pending line that says `text`: it leaves `future` and lands in the journal, dated. |

A code project's memory is a notes folder with exactly four notes, `rules` (the user's standing orders), `spec`, `future` and `journal`, plus any `skill: …` notes; `add_note` with one of those titles goes the way of that note (one note per title, never two) and refuses any other title in a project folder. A note with another title written by the user from Notes rides with the four as the user's own; the model may edit (`update_note`, same title) or delete it. Its task board is for immediate work: at most 7 open tasks per code project (`add_task` refuses beyond that), and what is pending but not being done goes to `future`. A turn that lands code with nothing open on the board and nothing written to the memory is asked once whether it holds a decision, a trap or a changed truth for the journal, or was a routine edit.


## 📅 Calendar

| Tool | Args | What it does |
|---|---|---|
| `add_appointment` | `date` `title` `description` `urgency?` `project?` | Creates an appointment, with optional urgency and project. `date` is `YYYY-MM-DD HH:MM`, or `YYYY-MM-DD` for a whole day; anything else is refused. |
| `list_appointments` | `project?` | Lists appointments, all or one project's. |
| `delete_appointment` | `id?` `title?` `date?` | Deletes one appointment by the `id` `list_appointments` gives, or appointments by exact title and, optionally, date (`YYYY-MM-DD HH:MM` for that moment, `YYYY-MM-DD` for any time that day); all matches. |
| `delete_all_appointments` | `contains?` `date?` `project?` `confirm?` | Deletes several appointments at once with combinable filters (`contains`, `date` as in `delete_appointment`, `project`). With no filter it deletes nothing: clearing the whole calendar is done by hand. |


## ⏰ CRON

| Tool | Args | What it does |
|---|---|---|
| `add_cron` | `name` `instruction` `schedule_type` `interval_minutes?` `fixed_datetime?` `daily_times?` `target?` `context_current?` `context_session?` `context_live?` `model?` `own_tab?` | Schedules an agentic task. `schedule_type`: `interval`, `daily`, `weekly`, `fixed` (once) or `startup` (each time HOPLITA starts). `target`: `local`, `child:<handle>` or `friend:<handle>` (runs on that machine). `context_current` / `context_session` attach a conversation the job inherits (`context_live` continues it on each run). `model`: empty = the router decides per run, `base` = the `[API]` model, or a pool alias. `own_tab`: the job gets a tab of its own, where each run is a turn you can follow and talk to (local jobs only). |
| `list_cron` | — | Lists the scheduled jobs. |
| `delete_cron` | `job_id` | Deletes a job by id. |
| `toggle_cron` | `job_id` `enabled` | Enables or pauses a job. |
| `update_cron` | `job_id?` `instruction?` `name?` `schedule_type?` `interval_minutes?` `daily_times?` `weekdays?` `fixed_datetime?` `model?` `enabled?` | Changes an existing job; only the fields given change, and a new `instruction` replaces the old one whole. In a job's own tab `job_id` may be left out: it is that tab's job, so a worker told «from now on do X, every 7 minutes» changes its own job. |
| `cron_set_context` | `job_id` `session?` | Attaches a saved conversation to an existing job as inherited context, or detaches it when no session is given. |
| `delete_current_cron_job` | — | The job running right now (or the job whose own tab this is) deletes itself; only for a one-shot task or on an explicit request. |
| `disable_current_cron_job` | — | The job running right now pauses itself; only when the task says it must not run again. |
| `set_cron_context` | `job_id` | Sets which job is running (internal: it is what makes the two above possible). |


## 👁 Background: work that goes on without the turn

A conversation can leave work running and go on, or end its turn and be woken. Every job belongs to the conversation that started it, and its report comes back THERE: into the turn running in it (framed as the job's aside, read before the model's next call) or, with no turn running, as that conversation's next turn — the window paints it as the job speaking and starts the turn; a saved conversation whose tab is closed is reopened for it. A job never wakes another tab. Stopping a turn does not end its jobs: they belong to the conversation, not to the turn; what ends them is `background_stop` (the model's, or asked for in words), `/watch stop`, the ✕ on the job in the ▦ overview, or closing the tab. A sub-agent of a plan can start jobs too: they belong to the plan's conversation, and the report goes to the sub-agent while it still works, to the plan's turn once the sub-agent is gone, and wakes the conversation when both are. Jobs survive a restart: watches are re-armed, a run that was alive is reported as lost. `[CHAT] max_threads` caps how many may be alive at once; the firewall judges the real tool, not the bridge.

| Tool | Args | What it does |
|---|---|---|
| `background_run` | `tool` `arguments?` `label?` | Runs ANY tool in the background: answers at once with a `job_id`, the tool runs on its own (on the window when it owns it, on the MCP otherwise), and its result reaches the conversation when it lands. For anything long: a test suite, a build, a slow fetch. Not for the tools that talk about the turn itself or carry a stream a face must answer (`intent`, `plan_and_orchestrate`, `call_council`, the bridges). |
| `background_watch` | `label?` `every_s?` `file?` `command?` `cwd?` `tool?` `arguments?` `wake?` `until_s?` `job_id?` | Watches ONE thing without the model, every `every_s` seconds (floor 5, default 30): a `file` (appears, disappears, changes), a shell `command` (exit code or output changes) or a `tool` with its `arguments` (its result changes, keys that are clocks ignored). `wake=change` (default) reports only a change, with before and now; `wake=always` reports every round. `until_s` is a life limit. With `job_id` an existing watch is re-paced or re-labelled. The reply carries the baseline (`now`). |
| `background_stop` | `job_id` | Ends a job: a run's process is ended and its late result dropped; a watch stops probing. For what is no longer needed, and for whatever is wedged on it. The person has the same door: `/watch stop <id|all>`, and closing the tab. |
| `background_list` | `all?` | This conversation's jobs (every conversation's with `all`): id, kind, label, tool, state, age, rounds, last probe; `live` and `ceiling`. |

## 💬 Conversations

| Tool | Args | What it does |
|---|---|---|
| `session_search` | `query` `limit?` `session?` `context?` `include_machinery?` | Searches everything ever said, verbatim, across all past conversations; each hit comes with the messages around it. `include_machinery=true` also searches what the tools did (each turn's record) and the summaries compactions produced. |
| `session_moments` | `query` `limit?` `window_min?` | Finds the **moments** a subject was discussed (the bursts of messages where all the terms co-occur) rather than single matching lines. |
| `session_read` | `at` `before?` `after?` | Reads the log **around a marker**: every message a log search returns carries its `at`, and a text cut short names the marker to read on from, the way a file is read from a line. |
| `session_recall` | `session` `query?` `k?` | Searches inside one saved conversation. |
| `session_brief` | `session` `refresh?` | Summarises a saved conversation (by title or uuid) so a scheduled task can inherit its context; cached per revision. |
| `sticky_note` | `text` | Rewrites the conversation's sticky note: the working note pinned above the chat that the user reads and edits (what is settled, what is left to run). The whole note every time, at most 15 lines; empty text clears it. It rides at the top of every turn, so no tool is needed to read it. Written by the GUI on the tab it belongs to. |
| `chat_session_save` | `session` | Saves or overwrites a chat tab as an encrypted session. |
| `chat_session_list` | — | Lists saved sessions (metadata, newest first). |
| `chat_session_load` | `uuid` | Loads a full session with its history. |
| `chat_session_delete` | `uuid` `conv_ids?` | Deletes a session (a tombstone that syncs); `conv_ids` are other ids the same conversation is kept under, dropped with it. |
| `chat_session_star` | `uuid` `starred?` | Stars or unstars a saved session: a starred one stands at the top of the list whatever its date, the prune leaves it alone, and the mark syncs to the user's other machines. |


## 🎨 Canvas

Every chat tab has a canvas of its own, a panel beside its transcript: a call paints the page of the tab whose turn runs it (a sub-agent's, the tab of the turn that spawned it), `canvas_eval` grows that page and `canvas_undo` steps that page back, and nothing of one tab's canvas reaches another's. The page keeps running while its tab is not on screen.

| Tool | Args | What it does |
|---|---|---|
| `render_html` | `html` `append?` | Paints HTML/CSS/JS/SVG on the GUI's Chromium canvas: charts, diagrams, mockups, whole pages. `append=true` builds a large document across several calls so the output limit does not truncate it. To explain code, the whiteboard is the place. |
| `dashboard_render` | `html` `append?` | Alias of `render_html`, kept for compatibility. |
| `canvas_eval` | `js` | Runs JavaScript in the **live** canvas page and returns the last expression's value (JSON-serializable). The page keeps running, so large things are built in increments and the page can be queried instead of guessed. Console errors come back in the result. GUI-only. |
| `canvas_see` | `max_width?` | A screenshot of what the canvas shows right now, attached as an image the model sees, to judge composition, colour and overlap. Needs a vision-capable model; otherwise it points at `canvas_eval`. GUI-only. |
| `canvas_undo` | — | Puts back the page the canvas held before the last render (one step). |
| `canvas_save` | `canvas` | Saves a canvas snapshot to the encrypted store (it syncs). |
| `canvas_list` | — | Lists saved canvases (metadata, newest first). |
| `canvas_load` | `uuid` | Loads a full canvas by uuid. |
| `canvas_delete` | `uuid` | Deletes a canvas (a tombstone that syncs). |
| `generate_image` | `prompt` `size?` `n?` `refs?` | Makes a picture with the configured picture server (`[IMAGE]`, OpenAI Images API). Saved to the media folder and shown on the canvas as a figure; the model gets the path, never the bytes. With `refs` it is an edit. Not offered when `[IMAGE] base_url` is empty. |
| `edit_image` | `prompt` `refs` `size?` `n?` | Changes source pictures (`refs`: local files, or `attached` for the pictures pasted last in the conversation) following the prompt. Same server and result shape as `generate_image`. |


## 🧭 Whiteboard

A live document the agent writes while you watch, where every claim about code is a reference to a file and a line range at pinned commits, checked against those bytes before it is saved, that opens the code when clicked. It shows on the CODE tab's whiteboard page (served by the MCP at `/wb/app`). The `wb_*` tools run in the MCP and are never delegated. A whiteboard belongs to its repository's project: it reads the project's `rules`/`spec`/`future`, never stores specs or tasks (a changed truth goes to `spec`, pending work to `future`, through `project_write`), and ending its lease writes one line in the project's `journal` (only for a project that exists). Canvas or whiteboard: explaining code goes to the whiteboard (the scratchpad when there is no change); other visuals go to the canvas. Guidance for the model comes from `wb_get_instructions`. Block shapes and rules follow Whiteboard (dev.fast, MIT).

**Drawn in the background.** The whiteboard page asks the MCP for a drawing (`POST /wb/api/draw`, `wb_jobs.py`), drawn the way an engineer reads an unknown repository (`wb_pipeline.py`):

1. a **dossier** computed by code with no model (README, manifests, layout, the core of the import graph, entry points, what changes, tests: `wb_dossier.py`);
2. a **plan** in one JSON call (the story, its parts and the files each rests on, the one flow worth a diagram); the sections land on the page at once;
3. **explorers**, one isolated agent turn per part, in parallel, with reading tools only (`code_*` reads, `list_dir`, `git_log`/`git_show`, `wb_diff` for a change), each answering with claims backed by one line of code copied verbatim;
4. **anchors** found by code at the pinned commit from those quotes;
5. the **writer**, one JSON call for the prose, while the diagram and the code peeks are assembled by code.

Explorers' briefs are remembered by the fingerprint of the files they read, so drawing again or bringing a whiteboard up to date re-explores only what changed, and a whiteboard none of whose cited files changed follows the code with no model call. Drawing runs under the global firewall and the local-only lock; a drawing nobody answered ends as `no_answers` and leaves nothing behind. The page says which model draws: the base model or a pool model, picked once on the page and resolved by code.

**What a contact sent.** `what: incoming` with `ref` (a branch under `refs/hoplita/incoming/<peer>/`) draws a shared repo's quarantined branch whole, pinned from its merge base with HEAD to its tip, before it is integrated; the title starts with the peer's handle. It is what Co-work's **🧭 Whiteboard** button asks for: an existing whiteboard of that tip is opened instead of drawn again, and a new push is a new tip and a new whiteboard. A ref outside the quarantine is refused, and one that brings nothing ends as `nothing_incoming`.

**A history.** `what: history` with `since` / `until` (months, `YYYY-MM`, either end open) tells the project's history from its commits: one JSON call per period, in parallel, reading each commit's subject, the start of its message and the top-level areas it touched (`git log --no-merges`), never a diff; then one call for the arc. Periods are weeks up to six weeks of span, months up to a year and a half, quarters beyond. Each told period is remembered (the briefs cache, keyed by the period and fingerprinted by its commits), so a later drawing tells only the periods whose commits changed. A period over the per-call budget is told in pieces and the pieces as one. The whiteboard has the arc, a flow diagram with a box per period, and a section per period with its story and key commits; it is a record pinned to the range's commits (`wb_history.py`). A range with no commits ends as `no_history` before any model is asked. **Redraw** tells it again without the remembered periods.

**Following the code.** A whiteboard of the source at a commit (**How it works**) or of the uncommitted work follows its project; a review of one commit, or of what a contact sent, is a record and never does (`wb_store.staleness`). When the code has moved on, the page says so and offers **Bring it up to date**: a background job (`what: update`) that re-pins it with `wb_set_target`, fixes the anchors that no longer hold, revises what changed and writes a new version (the old one stays in the history, and the project journal gets its line). Nothing asks for it on its own: the page offers it and you press it. A history (`what: history`) whose months reach the present is behind when its range has commits after the one it was told up to; its update draws over the same whiteboard, every period already told taken from what is remembered. `GET /wb/api/{id}/periods` gives a history's periods with their commits, `GET /wb/api/months?root=` the months a history may span, `GET /wb/api/repo/log`, `/wb/api/repo/files` and `/wb/api/repo/filediff` (with `root` and `commit`, a sha or `worktree`) a checkout's commits and the diff of any of them with no whiteboard, and `what: commit` with `ref` (a sha) draws that commit against its parent instead of the one at HEAD.

**Omitted arguments.** Any `wb_*` call without `whiteboardId` uses the whiteboard its lease holds, or else the one the conversation last named, and its answer says which. An omitted `leaseId` is the lease the same conversation began on that whiteboard. A checkout path given as `repositoryId` registers that repository. Arguments wrapped as `{"name": <the tool>, "arguments": {…}}` are unwrapped for every tool.

**Travel.** Each whiteboard is mirrored as one row of the synced store `whiteboards`, holding its latest version, the resources it uses and its repository's name and commits (`wb_sync.py`). Your other machines get it in the device snapshot (`fed_sync_push` / `fed_sync_pull`); a shared project's members get it with the project's tasks (`fed_project_push` / `fed_project_pull`, envelope `project_board`, under the project's grants). A whiteboard that arrives this way counts as already journaled. Its repository is found on the receiving machine by the pinned commits, among the registered repositories and the project's checkout. The row's revision is the whiteboard's version: an edit arrives as the next version, a concurrent edit keeps the losing side as `-local`, and a deletion is a tombstone row.

| Tool | Args | What it does |
|---|---|---|
| `wb_get_instructions` | `topic?` | The whiteboard guidance: the authoring workflow (default), `file-lenses` (Diff-view lenses) or `scratchpad` (explaining code that is not a change). |
| `wb_capabilities` | — | What the whiteboard offers here: scratchpad, structural diff (whether diffr is installed), block types. |
| `wb_register_repository` | `path` | Registers the local git repository containing `path`; returns its id and name. |
| `wb_resolve_pins` | `repositoryId` `base` `head` | Resolves base and head revisions (branch, `HEAD~1`, sha) to commit ids. |
| `wb_create` | `title?` `target?` `pins?` `kind?` `open?` | Creates a whiteboard of commits (`{kind:"commits", repositoryId, head, base?}`) or of the uncommitted work (`{kind:"worktree", repositoryId, base?}`) and opens it unless `open:false`. Returns its id, the pins and the repository path. |
| `wb_open` | `whiteboardId` | Shows a whiteboard on the CODE tab's 🧭 page; later edits appear live. |
| `wb_activity` | `whiteboardId` `action` `leaseId` `scope?` `focus?` | The exclusive authoring lease for one scope (`document` or `lenses`): begin with a fresh `leaseId`, pass it on every write, end when done. It expires after 10 minutes without a write; `focus` is what the reader sees the agent doing. |
| `wb_edit` | `whiteboardId` `edit` `commandId?` `leaseId?` | Insert / replace / update / move / remove of blocks (`section`, `callout`, `markdown`, `code`, `divider`, `code_peek`, `sequence`, `flow_diagram`, `call_stack_diff`, `database_lens`, `image`, `trace_quote`, `software_map`) and of diagram units. A source range past the end of the file, a peek of blank lines or a quote not in its trace rejects the edit; a retried `commandId` replays. An edit without `type` is read as insert, replace or update from its fields. |
| `wb_lens_edit` | `whiteboardId` `edit` `commandId?` `leaseId?` | Edits one Diff-view lens (a group of files or line ranges of the change). |
| `wb_lens_get` | `whiteboardId` | Reads the lenses and what is still uncategorized. |
| `wb_get` | `whiteboardId` `version?` `targetId?` `full?` `format?` | Reads a whiteboard as an outline with editable ids; `targetId` reads one component, `full` all content, `format:"json"` the raw data, `version` an older one. |
| `wb_list` | — | Lists saved whiteboards. |
| `wb_history` | `whiteboardId` | Lists a whiteboard's saved versions. |
| `wb_diff` | `whiteboardId` `paths?` `format?` `context?` `maxBytes?` `commit?` `repositoryId?` `head?` `base?` | The whiteboard's change: `format:"files"` lists files with counts, `format:"patch"` numbers every line on both sides. With `repositoryId` and `commit`, that commit against its parent. |
| `wb_file` | `whiteboardId` `file` `side?` `repositoryId?` `head?` `base?` | A whole source file at the whiteboard's pins (`side` base or head), or at explicit pins. |
| `wb_tree` | `whiteboardId` `path?` `side?` `repositoryId?` `head?` `base?` | Directory entries at the pins, or at explicit pins. |
| `wb_source` | `whiteboardId` `source` | An exact code range (`{side, file, fromLine, toLine}`) with line numbers, at the pins. |
| `wb_commits` | `whiteboardId` | The commits in the whiteboard's pinned comparison. |
| `wb_rename` | `whiteboardId` `title` `commandId?` `leaseId?` | Changes a whiteboard's title. |
| `wb_repin` | `whiteboardId` `pins` `commandId?` `leaseId?` | Moves the pins (`{repositoryId, base, head}`) keeping the document and its ids; warns about source ranges to fix. |
| `wb_set_target` | `whiteboardId` `target` `commandId?` `leaseId?` | Changes the whiteboard's target (commits or worktree), keeping the document and its ids. |
| `wb_restore` | `whiteboardId` `version` `commandId?` `leaseId?` | Restores title, pins and content from a saved version, as a new version. |
| `wb_delete` | `whiteboardId` `commandId?` | Deletes a whiteboard and its history permanently. |
| `wb_attention` | `whiteboardId` `action` | Marks a whiteboard viewed, dismissed or restored without changing its content. |
| `wb_upload` | `kind` `repositoryId?` `id?` `trace?` `base64?` `model?` `side?` `pins?` | Retains a trace, a PNG or a software map (C4-style systems, containers and components with file coverage) for use in a whiteboard. |
| `wb_trace_capture` | `label?` `repositoryId?` | Retains this conversation as a trace, so the whiteboard can quote the user's words (requirements) and the agent's (decisions). |

In the tab: a source link opens a side peek of the aligned code; a sequence step, a flow node or a database operation opens a guided tour, one code peek per stop; **Open file** opens the file in the CODE tab at the line (the real file when it still equals the pinned commit, otherwise a read-only view of the commit's bytes). **Copy for agent** (select prose or code, ⇧⌘C) copies the selection with the whiteboard's id and version. The Diff view groups the change by lenses and records what you marked viewed; with the `diffr` binary installed (`python install.py --diffr`, or `HOPLITA_DIFFR`) it is a structural diff that folds unchanged regions, tests and generated files, otherwise a textual one.


## 🔐 Credentials

| Tool | Args | What it does |
|---|---|---|
| `get_credential` | `site` | What is stored for a site, **without** the secret: its kind (`web`, `apikey`, `card`, `mail`), username and whether a password/OTP/key exists. |
| `list_credentials` | — | Lists the stored sites with their kind. |
| `fill_credentials` | `site` | Fills credentials into the live browser (used inside `browse_web`). |
| `get_otp` | `site` | A site's current OTP/2FA code. |
| `get_api_key` | `site` | The API key in clear for a site stored as kind `apikey`, for a header the model writes. Never to be printed or written into a note. |
| `get_card` | `site` | Full card details (number, expiry, CVV, holder) for a site stored as kind `card`, only to complete a payment that was explicitly requested. |


## 🤖 Agents and children

| Tool | Args | What it does |
|---|---|---|
| `plan_and_orchestrate` | `task` | Breaks a task into sub-agents (roles, goals, capabilities, dependencies), runs the independent ones in parallel and synthesizes one answer. Each sub-task can name its own pool model and machine (`local`, `child:<handle>`, `friend:<handle>`). **Requires approval** per `[CHAT] plan_approval`. |
| `call_council` | `topic` `members?` `first?` `context?` | Puts one matter to several pool models and returns their **deliberated** answer: the first member answers, the others examine it (errors? can it be done better?), the first reformulates, the others vote *placet*. Returns the answer, the signed dissent and the `deliberation`. `members` are pool aliases (empty = every pool model that sits on councils, except the model asking: it puts the matter and never sits, under its alias or as `base`), `first` opens. If the vote splits, it asks whether to spend another round. Costs one call per member per phase. |
| `fed_ask_agent` | `handle` `message` `max_iterations?` `tab?` `device?` | Asks a **remote** agent for help (a child of yours or a contact's agent): the task runs on their machine with their model and tools. `device` picks one machine of an identity; `tab` renders the turn in the calling conversation. |
| `fed_spawn_child` | `label?`, `handle?`, `model?` or `base_url`+`model_id`+`api_key?`, `search?`, `launch?` | Generates a single-use enrollment token for a federated **child**: a headless agent of yours on another machine. The token carries a sealed starter kit (model, optional web search) and the answer gives two commands that boot the child with nothing typed: one for a container, and one line that installs and starts the child as a service on any Linux, macOS or Windows machine with nothing installed first. `launch` builds the `hoplita-child` image if needed and starts it here. See FEDERATION.md 7b and 7c. |
| `fed_list_children` | — | Lists your federated children with their presence (online/offline). |
| `fed_revoke_child` | `handle` | Removes a federated child: invalidates its session instantly and deletes its identity and contact. Irreversible. |
| `child_create` | `name` `role?` `model?` `capabilities?` `notes_folders?` `projects?` `skills_folder?` `avatar?` `node?` | Creates a **local** child: a personalisation pack that runs here as a sub-agent. |
| `child_list` | — | Lists the local children with role, model, capabilities and folders. |
| `child_teach` | `name` `role_append?` `role?` `model?` `capabilities?` `add_capabilities?` `notes_folders?` `add_notes_folders?` `projects?` `add_projects?` `skills_folder?` `avatar?` `node?` | Changes named fields of a local child's pack and answers with the diff. `role_append` adds to the role, `role` replaces it; `notes_folders` / `projects` / `capabilities` replace, their `add_*` forms add; `node` runs the pack on a federated child. |
| `child_delete` | `name` | Deletes a local child's pack; whatever it wrote stays. A node that ran it keeps running until `fed_revoke_child`. |

### Two kinds of children

A **federated** child (`fed_spawn_child`) is a headless node on another machine, with its own identity on the server. A **local** child (`child_*`) is a personalisation pack that runs **here** as a sub-agent: no identity, no keys, no quota, nothing to enroll. It shows up as a contact you hand work to, and you teach it by talking.

You give a local child work exactly as you would a remote one: `fed_ask_agent`, a plan's `child:<name>` target, or a CRON job. That is one resolution point, not three, and a local child has no presence gate, so a cron aimed at one always fires.

A pack has no identity on any server, and it still reaches your other machines: it rides in the device snapshot sealed to your own key (role, model, capabilities, folders and avatar), and works there with no extra setup.

Its scope is two lists, and both are enforced by the tools, not by its prompt: `notes_folders` (the note tools refuse outside them, for reads and writes alike; its `skills_folder` is reachable even when it is not one of them) and `projects` (the TODO projects whose tasks it may see, do and update; creating or deleting a project stays the owner's). Appointments follow the projects.

Local children run in **parallel**: three tasks to three children are three turns at once on the same GPU.


## 🖥 Device

| Tool | Args | What it does |
|---|---|---|
| `device_info` | — | Which machine this install is (id and name). A tab tied to this id runs its turns here, whichever machine the user types from. |
| `device_rename` | `name` | Names this machine (`home`, `laptop`) so it is recognisable from the others. Local only. |
| `device_regenerate` | — | Gives this machine a new id, for a second computer set up by copying the folder. |


## 🪪 Federation: your account

| Tool | Args | What it does |
|---|---|---|
| `fed_register` | `handle` `email` `server_url` `invite?` | Creates a federated identity on a HOPLITA server: generates the keys (the private one stays encrypted in your vault, never leaves this machine) and sends only the public key. |
| `fed_verify` | `pin` | Verifies the email with the 6-digit PIN received after `fed_register`. |
| `fed_login` | — | Logs in with no password, by answering a challenge with your private key. |
| `fed_status` | — | Your identity: handle, fingerprint, server, and whether a session is active. |
| `fed_quota` | — | Your server plan: tier, limits and usage (sync bytes, mailbox, contacts, children, rate). |
| `fed_motd` | — | The operator's message of the day (maintenance or downtime notices). |
| `fed_leave` | `force_local?` | Leaves the federation: deregisters you from the server (identity, contacts, envelopes, children) and deletes the local identity. |


## 👥 Federation: contacts

| Tool | Args | What it does |
|---|---|---|
| `fed_add_contact` | `handle` `trust_level?` | Requests contact with another member by handle, with the trust level you grant (`open`, `accept`, `analyze`) for tasks they delegate to you. |
| `fed_accept_contact` | `handle` `trust_level?` | Accepts an incoming contact request. |
| `fed_list_contacts` | — | Lists your contacts: status, trust level and whether they are online. |
| `fed_remove_contact` | `handle` | Removes a contact on both sides; pending sends are dropped. Irreversible. |
| `fed_set_permissions` | `handle` `resource_type` `perms` | Granular permissions for a contact over a resource type (e.g. calendar: may add appointments, sees only busy/free). For `sesiones`, `tools` is the list of tools that contact's turns may use on this machine, or null for everything the firewall allows. |


## 📤 Federation: sharing

| Tool | Args | What it does |
|---|---|---|
| `fed_share` | `handle` `resource_type` `payload` | Shares a resource with a contact, end-to-end encrypted with their public key: the server sees only a blob. |
| `fed_share_note` | `handle` `category` `title` | Shares one of your notes (by category and title). |
| `fed_share_folder` | `handle` `folder_name` | Proposes sharing a note **folder** (the unit of sharing); once accepted it is a live two-way shared space. |
| `fed_share_project` | `project_name` `handle` | Shares a TODO project with its tasks, in one step. |
| `fed_share_skill` | `handle` `title` `content` | Shares one of your skills; it lands in the contact's pending tray, not directly in their agent. |
| `fed_share_appointment` | `handle` `title` `date` `description?` `urgency?` | Sends an appointment to a contact, encrypted; it waits in their incoming list and needs their `can_add` permission. |
| `fed_share_code` | `path` `handle` | Invites a contact to **co-work** on a code project: the current branch as an end-to-end sealed git bundle, plus the project's memory folder and task project. |
| `fed_accept_note` | `envelope_index?` | Saves a shared note from the inbox into your notes. |
| `fed_accept_folder` | `handle` `folder_name?` `folder_id?` | Accepts a note folder someone proposed sharing with you. |
| `fed_accept_project` | `project_name?` `handle?` | Accepts a shared project and brings its tasks into your TODO. |
| `fed_accept_skill` | `envelope_index?` | Accepts a shared skill into the separate `shared-skills` folder. |
| `fed_accept_appointment` | `title?` `from_handle?` | Accepts an incoming appointment into your calendar. |
| `fed_accept_code` | `handle` `project` `dest_path` | Accepts a code invite: clones the sealed bundle at a path the user chose and accepts the memory folder and task project. Later commits from the peer land in `refs/hoplita/incoming/<peer>/…`, never on your trunk. |
| `fed_list_incoming_appointments` | — | Appointments sent to you that you have not accepted yet. |
| `fed_list_incoming_projects` | — | Projects shared with you pending acceptance. |
| `fed_list_shared_skills` | — | Skills your contacts shared with you that are still pending. |
| `fed_list_appointment_bounces` | — | "No permission" bounces for appointments you sent. GUI internal use (consumed on read). |
| `fed_list_folders` | — | Shared note folders you take part in, with status and with whom. |
| `fed_folder_info` | `folder_name` | A folder's sharing card: the members if you own it, the owner if you are a member. |
| `fed_folder_push` | `folder_name` `resend?` | Syncs outward the notes of a shared folder: to each member, what it has not received yet. `resend: true` sends every note again, forgetting what the members were recorded as having, for a note the other side never got. |
| `fed_folder_pull` | `folder_name?` | Syncs inward the notes shared with you; a colliding note you modified is kept as `<title> (local)`. What the door turned away is listed under `refused`, each with its sender, folder and reason; `waiting` counts what stays in the mailbox for a folder not accepted yet. |
| `fed_unshare_folder` | `folder_name` `peer?` | Unlinks a shared folder (from either side); your local copy stays and stops syncing. |
| `fed_delete_shared_folder` | `folder_name` | Owner only: deletes the shared folder for all members. |
| `fed_is_shared_folder` | `folder_name` | Whether a note category is shared and accepted. GUI internal use. |
| `fed_list_shared_projects` | — | Every shared project you take part in, yours and others'. |
| `fed_project_info` | `project_name` | A project's sharing card: owner to members, or member to owner. |
| `fed_project_push` | `project_name` `resend?` | Syncs outward the tasks of a shared project, with their progress (appointments are not sent). `resend: true` sends every task and board again. |
| `fed_project_pull` | `project_name?` | Syncs inward the tasks shared with you; a colliding task you modified is kept as `(local)`. |
| `fed_unshare_project` | `project_name` `peer?` | Unlinks a shared project; your local copy stays. |
| `fed_delete_shared_project` | `project_name` | Owner only: deletes the shared project for all members. |
| `fed_is_shared_project` | `project_name` | Whether a project is shared and accepted. GUI internal use. |
| `fed_shared_overview` | — | Federated data in one call: contacts plus role and members of each shared folder and project. |
| `fed_publish_folder` | `folder_name` | Makes one of your note folders **public**: anyone on the server can find it by your handle and subscribe read-only. |
| `fed_unpublish_folder` | `folder_name` | Withdraws a folder from your public catalogue and drops its subscribers. |
| `fed_public_folders` | `handle` | The public folders a handle offers, with subscriber counts and whether you are subscribed. |
| `fed_subscribe_folder` | `handle` `folder_name?` `folder_id?` | Subscribes read-only to someone's public folder (no handshake). |
| `fed_unsubscribe_folder` | `handle` `folder_name?` `folder_id?` | Drops a read-only subscription; your local copy stays. |
| `fed_lent_models` | `handle` | The models a contact lends you, asked of their HOPLITA right now (needs them online): name, what it is good at, context, whether it sees images, and whether each is already in your pool. |
| `fed_add_lent_model` | `handle` `alias` `name?` | Adds a contact's model to your pool: their permission decides (auto, or their person is asked once), then a `[MODEL:<name>]` line with `via = <handle>` is written and the config reloaded. From then on `/model`, the router and the council use it like any other, only while they are online. |
| `fed_remove_lent_model` | `alias` | Takes a contact's model out of your pool. |
| `fed_lend_usage` | — | What YOU lend: the lendable models of this machine, which contact may use which, the calls in flight right now, and the tokens each contact used per day. |
| `fed_revoke_lent_model` | `handle` `alias?` | Takes a lent model (or every one, with no alias) back from a contact: their pool line stops answering at once. |


## 🔀 Federation: code

Co-working on code: `fed_share_code` sends the current branch as a sealed git bundle and `fed_accept_code` clones it (both in the sharing family). After that, commits travel as sealed incremental bundles and land in **quarantine** (`refs/hoplita/incoming/<peer>/<branch>`); nothing reaches your trunk until `fed_code_take`. A share starts as a small sealed offer; the bundle is built and sent only after the other side accepts, so nothing large is built or charged before both sides agree.

| Tool | Args | What it does |
|---|---|---|
| `fed_code_state` | — | Where a code share stands, in one call: what is in the mailbox by type, what was offered and accepted, and the path and peers recorded per project. |
| `fed_code_pending` | — | Code invites waiting in your inbox: who, which project, whether all sealed parts arrived. |
| `fed_code_push` | `path?` `project?` | Sends your new commits (current branch) to every code peer as sealed incremental bundles. Runs by itself after `git_commit`; by hand after committing from a terminal. |
| `fed_code_pull` | — | Applies complete incoming packs into `refs/hoplita/incoming/<peer>/<branch>` (quarantine only; your trunk does not move) and notes it in the project journal. The background engine does it on its own. |
| `fed_code_incoming` | `path?` `project?` | What waits in quarantine for a project: per peer and branch, the commits, the files touched, and whether integrating is a fast-forward. |
| `fed_code_take` | `peer` `path?` `project?` `branch?` | Integrates a quarantined branch into the current one: fast-forward when possible, a merge commit otherwise; a conflict is aborted and the files named; a dirty tree is refused. |
| `fed_code_peers` | `path?` `project?` | Who a code project is shared with, and the last commit each got. |
| `fed_code_unshare` | `path?` `project?` `peer?` | Ends co-work on a project with one `peer` or all. Their clone stays theirs. |
| `fed_code_discard` | `project?` `handle?` | Throws away a code transfer stuck half-arrived and frees the mailbox quota it holds. `handle` omitted = every stuck transfer. |
| `fed_code_retract` | `handle` | Takes back your code parts still in flight to a peer who never collected them, freeing your mail-in-flight quota; the next push rebuilds the branch whole. |


## 🔄 Federation: sync and inbox

| Tool | Args | What it does |
|---|---|---|
| `fed_inbox` | `resource_type?` | Collects what others shared with you and decrypts it with your private key; optional `resource_type` filter. |
| `fed_notices` | — | Server-to-client notices for you (broadcast and targeted, with catch-up for those that arrived offline). |
| `fed_activity` | `limit?` | What each contact did to what you share (commits on a shared project, notes and tasks added, edited or deleted), as events plus a grouping by contact, resource and day. |
| `fed_activity_clear` | `peer?` | Erases the contact activity log, all of it or one `peer`'s. Only the log. |
| `fed_save_note_guarded` | `category` `note_uuid` `title` `content` `base_rev?` | Saves a note in a shared folder with a conflict guard: if another version arrived meanwhile, your edit is kept as `(local)`. |
| `fed_delete_note_guarded` | `category` `note_uuid` | Deletes a note from a shared folder with a propagating tombstone. |
| `fed_clear_category_guarded` | `category` | Clears a whole category in one pass instead of one deletion per note. |
| `fed_save_task_guarded` | `project` `task_uuid` `payload` `base_rev?` | Saves a task in a shared project with a version-based collision guard. |
| `fed_delete_task_guarded` | `project` `task_uuid` | Deletes a task from a shared project with a propagating tombstone. |
| `fed_debug_notes` | `category` | Diagnostic: dumps uuid/rev/deleted/title of every note in a category, deleted ones included. |
| `fed_sync_push` | — | Uploads an encrypted snapshot of your data sealed to your own key (the server cannot read it). Answers `needs_pull` when the server has a newer one. |
| `fed_sync_pull` | — | Downloads your snapshot and merges it into this device (newest wins, per item). |
| `fed_sync_breakdown` | — | What your device snapshot weighs, store by store (rows and bytes, heaviest first, plus the 12 heaviest rows). |
| `fed_sync_status` | — | Status of the background sync engine: active or not, last pass, how many folders; and `refused_recent`, the envelopes its pulls turned away lately, each with sender, folder and reason. |

The `*_guarded` tools are the way to write to **shared** folders and projects: without them you overwrite the other side's edit and a deletion never propagates.


## Profiles: which subset each sub-agent sees

An orchestrator sub-agent does not get every tool (the full set overwhelms a small model). The planner declares **capabilities** and the code maps them to a profile (`hoplita_orchestrator.py` → `CAPABILITY_TOOLS`):

| Capability | Profile | What it opens |
|---|---|---|
| `web` | `browse` | search, read and browse the internet |
| `research` | `read` | RAG, notes, experience, read files |
| `files` | `read` | list and read local files |
| `terminal` | `full` | everything: runs commands and acts |
| `code` | `read` | the analysis `code_*` tools |
| `write` | `full` | everything: compose and write |
| `none` | `none` | no tools, reasoning only |

Several capabilities combine into the most permissive profile they need (`browse` + `read` = `browse_read`; anything with `full` is `full`).

Every profile that calls anything also carries the two retrieval tools, `search_conversation` and `search_working_memory`. A sub-agent is born from its brief, the planner's summary of a conversation it never saw, and a summary loses; so it is told so (its system prompt says the brief is a summary, names the two tools, says to search before assuming and that the conversation wins over the brief), and `search_conversation` travels with its full schema whatever the measured hot set says. Reading the conversation grants no capability the profile lacked. The sub-agent also wears the plan's turn on its own thread: the tools it calls see the tab's conversation and project, so its working-memory search finds the tab's texts and what it spills is tagged as the conversation's. What is behind the door is what the turn filed: a plan the model called has the turn's own messages, a plan typed as `@multi` sends the conversation with it, as any turn does, and so does a council convened from its page with the context tick on (its seats keep the tail as a briefing and get the door for the rest). Where nothing is filed, the sub-agent is neither told its brief is a summary nor offered `search_conversation`: the door is promised only where something is behind it.

Profiles resolve to concrete sets in the MCP (`hoplita-mcp.py` → `TOOL_PROFILES`), the single source for both faces:

| Profile | Tools |
|---|---|
| `full` | everything the firewall allows |
| `browse` | `browse_web` `scrape_url` `screenshot_web` `search_web` `search_notes` `search_rag` `get_credential` `alert` + cron self-removal |
| `read` | `search_web` `scrape_url` `search_notes` `search_rag` `search_experience` `search_facts` `list_dir` `read_file` `alert` + 7 read-only `code_*` (`code_index` `code_read_smart` `code_read_range` `code_search` `code_deps` `code_references` `code_lint`) |
| `browse_read` | the union of the two above |
| `monitor` | `browse_web` `scrape_url` `search_web` `alert` + cron self-removal |
| `none` | no tools |

On top of the profile, for every sub-agent:

- `intent` is always in, whatever the profile.
- `background_run`, `background_watch`, `background_stop` and `background_list` are in every profile that calls anything (not `none`): they grant no capability of their own, since `background_run` is unwrapped to its real tool before the profile wall and a watch's probe is judged by that same wall when it is armed. A job a sub-agent starts belongs to the plan's conversation: its report reaches the sub-agent while it still works, the plan's turn once it is gone, and wakes the conversation when both are.
- `plan_and_orchestrate` and `call_council` are always out (a sub-agent starting its own fan-out is recursion; a council member convening a second council would make its own round wait on it). A federated node running a pack keeps them, because its plan is local to its own machine.
- The shared-surface tools `render_html`, `dashboard_render` and `canvas_eval` are given only to a **terminal** sub-task, one nothing else in the plan depends on, so nobody paints a finished-looking canvas while other sub-tasks are still running.
- The general firewall keeps the last word.

**Capabilities are a preset, not the permission system.** A local child's pack may carry an explicit `tools` list, and when it does, that list is the answer; capabilities are only how a child is created from a sentence. The child's settings screen embeds the same picker the general firewall uses (families with a tick each), writes the chosen names into its pack, and `_pack_tool_names` reads them in one place for both the local child and the node wearing the pack. A pack defined by capabilities that declares `projects` also gets the task tools it needs to work in them (`get_todo_context`, `list_todo_projects`, `add_task`, `update_task_progress`, `task_depends`).


## Usage notes

- `code_index` **before** any other `code_*`; `code_symbols` before reading a file to find one function.
- Before renaming: `code_references`, and **read the `source` field**. With `source: "lsp"` the list is authoritative. With `source: "text search"` it is a list of candidates: read the hits, and tell the user the rename may be incomplete rather than claiming it is done.
- `deactivate_lesson` rather than `delete_lesson`: what is deactivated can be recovered.
- `update_note` rather than delete-and-recreate: it keeps the id and does not break sync.
- `scrape_url` before `browse_web`: the first is cheap and already runs JavaScript; the second drives a browser with an LLM and is slow and brittle.
- The `*_guarded` tools are mandatory when writing to **shared** folders or projects.
- `render_html` with `append=true` for long documents: a single huge call gets truncated by the output token limit.

### The language server (optional)

Everything above works with no language server. It works worse, and says so: `code_references` falls back to text, `code_diagnostics` answers `available: false`, and the code view shows "no linters" with the exact install command. Nothing is installed on your behalf.

A language server is a child process speaking JSON-RPC over its stdin/stdout, and a **session**: it is told which folder the project is, then the full text of each file opened, and only then can it answer. It knows nothing about files it was never sent.

    python install.py --lsp          # Python (pylsp), the usual case
    go install golang.org/x/tools/gopls@latest      # Go
    # C/C++: clangd, from your system's package manager

Install `python-lsp-server[all]`, never the bare package: without its plugins pylsp gives navigation and **zero diagnostics**; it publishes an empty list forever, which reads exactly like a clean file.

