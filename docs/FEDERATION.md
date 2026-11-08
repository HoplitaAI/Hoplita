# HOPLITA — Federation, step by step

Federation is optional. Without it HOPLITA works fully on one machine. With it
you get an identity on a federation server, and with that identity you can:

- keep your own machines in sync,
- add contacts and share note folders, task projects, appointments, skills and
  code repositories with them,
- ask a contact's agent to do a task on their machine,
- run headless agents of your own (children) on other machines.

The server is a relay: what you sync or share is sealed end to end with your
keys, and the server only stores and forwards blobs it cannot read. The
concepts are in the user guide
([Federation](GUIDE.md#federation-optional),
[Children](GUIDE.md#children-local-packs-and-remote-nodes),
[Co-work](GUIDE.md#the-co-work-page--sharing-a-repo-and-leaving),
[Activity](GUIDE.md#what-your-contacts-did-to-what-you-share),
[Sharing code](GUIDE.md#sharing-code-asks-before-it-builds)). This page
covers the procedures.

**How to read the steps.** «Chat:» means you type the request in a chat tab and
the agent calls the tool named in brackets. The same request works from the
CLI, because the CLI talks to the same agent: `hoplita-cli "<the request>"`.
The CLI binary lives at `cli/dist/hoplita-cli` inside the install folder
(`install.py --cli` builds it); run it from there or put it on your `PATH`.
Every tool is described in [TOOLS.md](TOOLS.md).

---

## 1. Choose a federation server

You have two options:

- **The public server**, `https://hoplita.net`.
- **Your own server.** The federation server is a separate project:
  `github.com/HoplitaAI/Hoplita-server`. Its repository explains how to deploy
  it. A development instance listens on port 8600 (`http://localhost:8600`, or
  that machine's LAN address).

There is no config key for the server. You give the server URL once, when you
register (step 2). It is then stored inside your identity, and every later call
goes to it. All your contacts must be on the same server. To move to a
different server, leave the federation and register again on the new one
(step 2, *Leave the federation*).

On the server side:

- A server can be invite-only (it then asks for an invitation code) or open.
- A server mails the verification PIN when it has mail configured.
- A development server can return the PIN in the registration reply instead.

## 2. Create your identity

There is no registration form in the GUI. You register from the chat. The
**Contacts** tab says so until you do: *No federated identity. Register from
the chat…*

1. Install HOPLITA and start the MCP and the GUI, or the CLI
   ([Installation](GUIDE.md#installation), [Starting it up](GUIDE.md#starting-it-up)).
2. Register. Chat: «register me in the federation as `alice`, email
   `alice@example.com`, server `https://hoplita.net`, invitation code
   `ABC123`» (`fed_register`). Leave the code out if the server is open.
   - Your handle is a plain lowercase name, for example `alice`.
   - Two key pairs are generated on this machine. Only the public keys go to
     the server.
   - There is no password: you log in by proving you hold the private key.
3. Check the inbox of the email you gave. You get a 6-digit PIN, valid for 15
   minutes. If the reply says the server could **not** send the email, ask the
   server's operator for the PIN. Do not register again: the handle is
   already yours.
4. Verify. Chat: «verify my federation account with PIN 123456»
   (`fed_verify`).
5. Log in. Chat: «log me in to the federation» (`fed_login`). After that the
   session renews itself with your key whenever it expires.
6. Open the **Contacts** tab. The header shows your handle, *🟢 session
   active*, your fingerprint and the server URL.

### Where the identity lives, and what protects it

- The identity is one file, `fed_identity.json.encrypted`, in the HOPLITA data
  folder (the install folder, or `HOPLITA_HOME`). It holds:
  - your private keys: a Curve25519 key that seals content and an Ed25519 key
    that signs your contacts and shares,
  - your handle, the server URL and your session token,
  - the keys you have pinned for your contacts.
- The file is encrypted with the vault master password, the one the MCP asks
  for when it starts.
- Without that password the file is useless. Without the file you cannot be
  that handle. Back it up.
- The rest of the vault (`hoplita.db`) is a separate database. Your synced data
  travels in the device snapshot (section 3), not in this file.

### Use the same identity on a second machine of yours

1. Install HOPLITA on the second machine.
2. Before its first start, or with its MCP stopped, copy
   `fed_identity.json.encrypted` from the first machine into the second
   machine's data folder.
3. Start the MCP there with **the same master password** as on the first
   machine. The file is sealed with that password: a different one fails to
   open it, and the MCP asks for the password again. On its first start the
   window says the machine is **joining** your identity and asks for your
   other machine's password — it is not a new one to choose.

   If this machine was already started on its own, it has a vault of its own,
   sealed with whatever password you chose then. Either that password is
   already the same as on the first machine, or start it clean:
   `python3 install.py --uninstall --purge` (this deletes what that vault
   held), install again, copy the file, then start.
4. Start the GUI. About 3.5 seconds after launch it pulls your snapshot. Press
   **⇊ Pull** in the header if you do not want to wait.

Each machine is shown by its hostname. Nothing has to be named: work is routed
to a machine by an id of its own. To show it under another name, ask in the
chat («call this machine home»).

Do not run `fed_register` on the second machine: that would create a second,
different identity.

### Leave the federation, or move to another server

**Contacts** → **🚪 Leave**, then confirm. Chat: «leave the federation»
(`fed_leave`).

- Your account is deleted on the server, your children are revoked, and the
  identity file on this machine is cleared.
- The account is the identity, so this affects **all** your machines: their
  copies of the identity file stop working on that server.
- Your notes, projects and calendar stay local. They stop syncing.

If the server does not answer, the dialog offers to clear only the local
identity.

To move to another server, leave, then register again on the new one (step
2). Your contacts must add you again there.

### Your plan

A line over the chat tabs, **☁**, is always there. Without an identity it says
**not federated**. With one it shows who you are and through which server,
your plan's name, and a meter for what the server allows your identity and
how much of it you use: your synced copy, your contacts and your children.
`∞` is no limit. Two more meters join it only when they near their limit: **sent, not collected** (what you sent your contacts,
messages, shares and session turns, that they have not picked up yet) and
**to collect** (what waits on the server for you). Hover over the line for the
whole breakdown.

A meter, and the line's edge, turn amber when a count reaches 80 % and red
when one is full; at that point the server refuses what does not fit. The
server's operator sets the plan. If the server does not answer, the line says
so. A click on it opens Contacts. From the chat: «what is my quota?»
(`fed_quota`).

## 3. Keep your own machines in sync

Your machines share one identity, and with it one sealed snapshot on the
server: notes, tasks, appointments, crons, credentials, facts, lessons,
experience, sessions, canvases, whiteboards, local children's packs and the
activity log. The snapshot is sealed to your own key. The guide explains how
deletions and long absences are handled ([Federation](GUIDE.md#federation-optional)).

What does **not** travel:

- note folders marked 🔒 local-only (section 5.7),
- chat tabs set to local-only or incognito logging,
- the identity file itself (you copy it by hand, as above).

When sync happens:

| Moment | What happens | Setting |
|---|---|---|
| MCP start | Pull | `[MCP] sync_on_boot` (`true` in `config.ini.example`; a `config.ini` without the key counts as `false`) |
| GUI start | Pull, about 3.5 seconds after launch | Always |
| While the GUI is open | Pull, then push, every N minutes | `[MCP] sync_every_min` (15; 0 = off) |
| GUI close | Push | Always |
| By hand | **⇊ Pull** / **⇈ Push** in the header | — |

Chat or CLI equivalents: «pull my snapshot» (`fed_sync_pull`) and «push my
snapshot» (`fed_sync_push`). «What does my snapshot weigh?»
(`fed_sync_breakdown`) lists its size store by store.

The snapshot is sealed by you, to you. A copy on the server that this identity
did not seal (one left by a HOPLITA from before protocol 3, or one the server
made) is not merged, and the next upload replaces it.

When you press **⇊ Pull** or **⇈ Push**, the chat tells you what happened:
*⇊ Sync: N item(s) merged*, *⇊ Sync: nothing new on the server.* or
*⇈ Sync: uploaded (vN, M rows).* A push that finds a newer snapshot on the
server pulls and merges first, then pushes again.

**A machine that is not yours.** `sync_on_boot` writes your snapshot into the
local database of whatever machine runs the MCP. Set it to `false` in the
`config.ini` of any machine you do not own, including a headless child running
on somebody else's server.

## 4. Contacts

### Add a contact

1. **Contacts** tab → **+ Add**.
2. Type the contact's handle exactly as they registered it, for example `bob`.
   The handle alone, without `@server`: contacts are looked up on your server.
3. Pick a **Trust:** level:
   - `accept`: their requests to your agent ask you first,
   - `open`: free rein,
   - `analyze`: your agent pre-filters their requests.

   Then press **OK**. Chat: «add bob as a contact» (`fed_add_contact`).
4. The contact appears with *pending* until bob accepts.

### Accept a request

The other side sees your row with *Pending your approval*. To accept, they
right-click the row → **✓ Accept request**. Chat: «accept the contact request
from alice» (`fed_accept_contact`).

Accepting or adding signs the other side's keys with your own key. That
signature is what makes them your contact, on every machine that carries your
identity.

Everything that travels is sealed by its sender for its recipient: notes,
folders, projects, code, appointments, sessions, and your own sync copy,
which you seal for yourself. Inside the seal it says who wrote it, to whom,
what it is, which folder, project or session it is about, when, and a
one-time number. All of it comes in by one door, which takes it only when:

- it opens with the key you signed for its sender (your own key, for your
  own machines);
- what is inside matches what the server says on the outside, and it is
  addressed to you;
- it is not older than its kind allows (a session order, a quarter of an
  hour), and the same one has not been taken before, on this machine or on
  another of yours;
- its sender has the right to it: a member of that very folder or project
  (by its identity, not its name), the contact a session is with, your owner
  for a child's pack.

So the server, which holds every public key, can neither read what you send,
write in anybody's name, replay an old message nor hand you back an old copy
of your own data.

What signing cannot cover on its own is the first contact: the key you sign
is the one the server hands you then. To close that, compare safety codes.
Right-click the contact → **🔐 Verify safety code…** shows twelve groups of
digits made from your key and the one you signed for them. On their own
machine, they right-click YOU in their Contacts and do the same: both of you
get the same number when nobody stands between you. Compare the numbers in person or on a call where you
recognise each other. The same numbers mean nobody stands between you: press
**They match: mark as verified**, and the contact reads *✓ verified* on every
machine of yours (it is signed into your attestation, so the server cannot set
or undo it). Different numbers mean somebody may be in the middle: do not mark
it. A contact whose key changes starts *not verified* again. Marking is yours
alone: no tool lets the agent do it, though it can show you the code
(`fed_safety_code`). A folder or project is not accepted under a name you
already have: it would merge into yours. An older HOPLITA (federation
protocol 3 or earlier) cannot share with this one: both sides need to be
updated.

### Presence

The dot on a contact's avatar shows presence: green is online, grey is
offline. The list refreshes every 5 seconds while the tab is visible, or
press **↻ Refresh**. Presence is shared only between accepted contacts: a
request that has not been accepted shows nothing, either way.

If the tab shows *Your live socket is … — contacts see YOU offline…*, your
own connection is down: everything still works, but others see you offline
(section 8).

### Permissions per contact

Double-click a contact, or right-click it → **⚙ Edit permissions**. The dialog
has one row per resource. Each row is set to **auto-accept**, **review** or
**no**.

| Row | auto-accept | review | no |
|---|---|---|---|
| **Notes** and **Projects** (folder and project offers) | Accepted on their own, when the offer is signed by that contact | The offer waits under **Pending your approval** for you to accept or decline | Same as review: an offer is never declined in the background, so you still decide |
| **Appointments** | Go straight into your calendar | Wait under **Pending** in the TODO tab's calendar | Refused, and the sender is told *you don't have permission* |
| **Remote sessions** | Their agent requests run without asking | You get an approval prompt | Declined |

Appointments are refused until you set their row. When **Remote sessions** is
not set, the contact's trust level decides.

The permissions and the trust level are signed by you together with the
contact's keys, and that signature is what HOPLITA reads. The server keeps a
copy for your other machines; a change made there does not change what your
HOPLITA allows, and a contact listed there that you never added or accepted
is treated as a stranger.

### Verify a contact

A contact's keys are learned from the server the first time, and signed by
you at that moment. From then on, a different key from the server is blocked
with *⚠ The public key for 'bob' does NOT match…*. The only moment the server
could lie is that first time.

To rule it out, before you share anything sensitive:

1. Chat: «list my contacts with their fingerprints» (`fed_list_contacts`).
2. Ask the contact for their fingerprint over another channel: in person, by
   phone, or in a message you already trust. Their own fingerprint is in the
   header of their **Contacts** tab.
3. Compare the two fingerprints. If they differ, remove the contact and do not
   share with it.

The `fed_verify` tool is something else: it only verifies your email at
registration.

### Remove a contact

Right-click → **🗑 Remove contact**. Chat: «remove bob from my contacts»
(`fed_remove_contact`).

Removal is bilateral: neither of you can share with or delegate to the other
until you add each other again. Copies already shared stay where they are.

## 5. Sharing

What has already been shared cannot be pulled back from the other side.
Unsharing stops future changes from travelling. It does not delete their copy.

### 5.1 A note folder

1. **NOTES** tab → right-click the folder in the category list → **🔗 Share
   with…** → pick a contact. Only accepted contacts are listed.
2. The chat says *✓ Folder "…" shared with bob*. Chat: «share the folder
   recipes with bob» (`fed_share_folder`).
3. Unless bob set **Notes** to auto-accept for you, bob gets a **Share
   invitation** dialog: *alice wants to share the folder “recipes” with you.
   Accept?* He presses **Accept**.

   If bob presses **Not now**, the dialog comes back at the next start. Bob can
   also accept from the chat: «accept the folder recipes from alice»
   (`fed_accept_folder`).
4. From then on, edits go out on their own a couple of seconds after each
   save.

To see who has the folder, right-click it:

- **The owner** sees **👥 Shared with N**, with each member (✓ accepted,
  ⏳ pending) and **— Remove** to drop one member. **🗑 Delete for everyone**
  deletes the folder for every member, after a confirmation.
- **A member** sees **👤 Shared by alice**, then **🚪 Leave (keep my copy)** or
  **🚪🗑 Leave and delete my copy**.

What contacts did in shared folders appears in **Contacts → Activity**.

### 5.2 A task project

1. **TODO** tab → right-click the project → **🔗 Share with…** → pick a
   contact. Chat: «share the project launch with bob» (`fed_share_project`).
2. Bob accepts the **Share invitation** for the project, or from the chat
   (`fed_accept_project`).
3. Removing members, **Delete for everyone** and the two **Leave** entries work
   as for folders, from the project's right-click menu.

### 5.3 An appointment

There is no button for this. Chat: «send bob the appointment "Review" on
Friday at 18:00» (`fed_share_appointment`).

What happens on bob's side depends on bob's **Appointments** permission for you
(section 4):

- **auto-accept**: the appointment goes straight into bob's calendar.
- **review**: it waits under **TODO → Appointments → Pending** until bob accepts it.
- **no** or not set: it is refused. Your **Contacts** tab then shows
  *Appointment rejected — You don't have permission to send appointments to:
  bob.*

### 5.4 Skills

There are two ways to share skills:

- **One skill.** Chat: «share my skill "deploy checklist" with bob»
  (`fed_share_skill`). Bob reviews it with «list the shared skills waiting for
  me» (`fed_list_shared_skills`) and accepts with «accept shared skill 0»
  (`fed_accept_skill`). Accepted skills go into bob's `shared-skills` folder,
  which the skill search includes.
- **A folder of skills.** Mark the folder as skills first (**NOTES** →
  right-click → **🧠 Add to skills**), then share the folder (5.1). A child of
  yours uses the folder as skills at once. A contact receives the folder plus
  an offer: one click on the folder's **🧠 Add to skills** turns it on there.

### 5.5 Code (the Co-work page)

You need a git repository open in the CODE view.

1. Open the **Co-work** page (🤝 in the CODE view's rail). You can also
   right-click the chat tab of that project → **🤝 Share code with a
   contact…**.
2. Under **WHO I INVITE**, select a contact → **🤝 Invite the selected one**
   (`fed_share_code`). Only a small offer travels at this point.
3. The other side sees the invite under **INVITES FOR ME**, with its size. They
   select it → **✓ Accept into a folder…** → choose an **empty** or new
   folder (`fed_accept_code`).

   The inviting machine then builds the sealed bundle and sends it. It arrives within a
   couple of minutes while both machines are running. The project's memory
   folder and task project are accepted along with it.
4. **⇈ Send my commits** (`fed_code_push`) sends your new commits to every
   peer. This also happens on its own after a commit made by the agent.
5. **⇊ Bring in what arrived** (`fed_code_pull`) applies incoming commits
   under `refs/hoplita/incoming/<peer>/<branch>`. Your branch does not move.
   This also runs in the background every 90 seconds.
6. Under **Received, in quarantine**, select a branch:
   - **👁 Review** shows its commits and files.
   - **⇓ Integrate into my branch** (`fed_code_take`) brings it in: a
     fast-forward when possible, otherwise a merge commit. On a conflict
     nothing changes, and the files are named.
7. To leave, select a peer under **Already shared with** → **✕ Stop sharing
   with the selected one** (`fed_code_unshare`). Their clone stays theirs.
8. On the receiving side, if a transfer died half-way (*This transfer died
   half-way…*), select it under **INVITES FOR ME** →
   **🗑 Discard the stuck transfer** (`fed_code_discard`). This frees your
   mailbox quota. Then ask the sender to invite you again.

### 5.6 Public folders

**Publish:**

1. **NOTES** → right-click the folder → **🔗 Share with…** → **🌍 Make PUBLIC
   (anyone on the server can subscribe; you write, they read)**
   (`fed_publish_folder`).
2. Anyone on the server can now find the folder under your handle and
   subscribe. Only you can write to it.
3. To withdraw it, chat: «unpublish the folder recipes»
   (`fed_unpublish_folder`). Its subscribers are dropped.

**Subscribe:**

1. **Contacts** → right-click an accepted contact → **🌍 Public folders…** →
   select a folder → **Subscribe**.
2. For a handle that is not your contact, chat: «show the public folders of
   carol» (`fed_public_folders`), then «subscribe to carol's folder recipes»
   (`fed_subscribe_folder`).
3. Their changes reach you read-only.
4. To unsubscribe, chat: «unsubscribe from carol's folder recipes»
   (`fed_unsubscribe_folder`). Your local copy stays.

### 5.7 Keep a folder off the federation (🔒)

1. **NOTES** → right-click the folder → **🔒 This machine only (never
   federate)**.
2. From then on the folder cannot be shared or published, and its content
   leaves the device snapshot. Only its name and the lock travel to your own
   machines, so they keep it locked too.
3. A folder that is already shared or published must be unshared or
   unpublished first. Otherwise you get *🔒 "…" is already shared or
   published…*.
4. **🌐 Make federable again** lifts the lock.

A chat tab has the same choice: right-click the tab → **📓 This tab's
logging** → **🔒 Local only**. The tab is saved and learns on this machine,
and its session never rides the federation.

## 6. Ask a contact's agent, and tie a tab to one of your machines

### A one-off task, or a whole session

**A one-off task.** Chat: «ask bob's agent to check whether the build server is
up» (`fed_ask_agent`).

- The task runs on bob's machine, with bob's model and tools.
- bob must be an accepted contact and online.
- The answer comes back into your turn.

**A session.** **Contacts** → right-click the contact → **💬 Open session**.

1. A chat tab opens, connected to their agent: *Session tab with bob: what
   you type here goes to their agent…*
2. Everything you type there runs on their machine.
3. Attachments do not travel.
4. Press ✕ on the banner to end the session.

**What the tab tells you.** A message is *sent*, then *confirmed*, then it
runs:

- *→ Sent to bob. Waiting for their HOPLITA to confirm it…*
- *⏳ bob's HOPLITA received it and is asking its person…* — when their
  permission for you is review (below).
- *● bob accepted the session: their agent is on it.*
- *⚠ bob's HOPLITA has not confirmed it after 20 s* — their machine is off
  or disconnected; it gets the message when it connects.

**Commands in a session tab.** They act on your own tabs and never travel as
a message. `/model` alone says which model answers you there (the one their
agent really called); `/model <name>` says the model is chosen on their
machine; any other command says it is not available in a session tab.

**What their agent knows of you, and of them.** Their agent works with their
model and tools, and **without their memory**: a session with a contact (or
with a child) never puts the executor's facts and lessons in its context,
because the answer goes back to the other side. What a contact or a child
needs to know is shared on purpose, as a skill (5.4) or a pack (7). Only a
session with **yourself** — your identity on another machine, see `/pin`
below — works with your memory.

**How fast it shows.** What their agent is doing appears on your tab as it
happens: each step goes from their machine to yours through the server without
being stored. The reply, a tool their agent asks your machine to run, and a
stop also go through the server's mailbox, so they arrive even if the
connection blinks. With a contact or a server on an older HOPLITA, everything
goes through the mailbox, as before, and the steps show a little later.

**On the receiving side**, their **Remote sessions** permission for you
decides:

- auto-accept: the request runs.
- review: they get *Incoming session request — "alice" wants your agent to run
  a task. Approve?* with **Approve** / **Reject**, once per session: the next
  messages of an accepted session run without asking. A new session, or one
  idle for 30 minutes, asks again. The prompt needs their GUI open; if nobody
  answers in 5 minutes the request runs out, and you are told nobody
  answered — not that it was refused.
- no: the request is declined. The agent treats a decline as final and does
  not retry.

**Stopping it.** **Stop** on your side ends the turn within seconds, also while
it waits on their agent, and also when the work is a sub-agent of a plan that
you sent to a contact. The stop crosses to their machine. A request still
waiting for their approval is withdrawn: their *Incoming session request*
dialog closes by itself, and nothing runs.

### Tie a tab to one of your machines (`/pin`)

This works between machines that share your identity (step 2).

1. On the machine that has the files, services or disks the tab needs, type
   `/pin` in that tab, or right-click the tab → **📍 Tie to this machine**.
2. The tab travels to your other machines in the snapshot. From any of them
   it is that machine's tab, as if you sat at it: its turns run there, and so
   does everything else about the tab — Stop, plan and council approvals,
   compaction (`/compact` and the automatic one), the context map, the saved
   conversation, the project's files, git, checkpoints and diff, and the
   whiteboards. Only what is drawn on the screen (canvas, the sticky note,
   alerts) comes back to the window you are at.
3. Both windows see the same conversation as it happens. What you type on
   another machine runs on the tied one and its window shows it live; what
   you type at the tied machine itself shows live in the other windows that
   have the tab open, and each of them reloads the whole conversation from
   the tied machine when the turn ends. A window that is closed sees it the
   next time the tab opens. A tab tied elsewhere arrives in a **⇊ Pull**
   too, not only when the window starts.
4. `/pin ?` says which machine the tab is tied to. `/pin off` unties it.

The tied machine must be running and logged in to the federation. If it is
not, the tab says *Could not reach "…"*.

## 7. Children

There are two kinds of child. The guide explains the difference
([Children](GUIDE.md#children-local-packs-and-remote-nodes)):

- An **unemancipated child** is a pack that runs here as a sub-agent. It needs
  no server.
- An **emancipated child** is a headless node on another machine, with its own
  identity.

### 7a. A local child (a pack that runs here)

**Create it:**

1. **Contacts** → **+ 🧒 Local child**.
2. Say **what it is for**: what it knows, what it does, what it delivers,
   which of your notes or projects it works with. The name is optional (no
   `@`); without one, the model picks it.
3. **Create**. The model writes its pack: the role, the capabilities it needs,
   and the folders, projects and pool model it works with, chosen only from
   the ones that exist.
4. The pack editor opens with what it wrote, to adjust.

Chat equivalent: «create a local child called MarketingManager» (`child_create`).

**Edit its pack.** Right-click the child (marked 🧒) → **✎ Edit its pack…**.
The editor has these fields:

| Field | What it sets |
|---|---|
| **Avatar** | An emoji or up to four characters |
| **Model** | A pool model, or *the same as the turn that calls it* |
| **Runs on** | *here, as a sub-agent*, or one of your federated children (7b) |
| **Role** | A paragraph: what it is and how it works. This field matters most |
| **What it can do** | Six capability presets: web, research, files, code, terminal, write. Ticking one fills in the tool list |
| **Its tools** | The tools it may call, by family. The general firewall still has the last word |
| **Where it may look (and write)** | The note folders it can search, read and write. Nothing ticked means no restriction |
| **Task projects it works in** | It sees, does and updates their pending tasks |
| **Its skills folder** | One of your note folders, or *the usual ones* |

**Save** writes the pack exactly as shown.

You can also teach the child in the chat, which adds to the pack: «give my
child MarketingManager the sales-notes folder and reinforce its role as an
expert» (`child_teach`). The agent answers with the diff.

**Talk to it.** Right-click → **💬 Open session**. *Talking to MarketingManager,
your local child: it runs on this machine…* Other ways to give it work:

- Chat: «ask MarketingManager to …» (`fed_ask_agent`),
- a multi-agent plan with `target: "child:MarketingManager"`,
- a CRON job with target `child:MarketingManager`.

**Delete it.** Right-click → **🗑 Delete this local child**. The pack goes.
What it wrote stays.

The pack travels to your other machines in the snapshot.

### 7b. A federated child (a headless node on another machine)

A child of yours that runs on another machine (a server, a VPS, a spare box)
with its own identity on the server. You drive it from here, through the
server. It boots from a token with nothing typed into it. In a container, see
7c. Without one, read on.

**On your machine: create the token.**

- **Contacts** → **+ 👶 Emancipated child** → **Token and commands**, or
- chat: «create a child called server-1 that thinks with my base model»
  (`fed_spawn_child`).

The token carries the child's **starter kit**: the model it thinks with (yours,
a pool model, or one of its own) and optionally your web search. The kit is
sealed to the token, so the server cannot read it (7c, *What travels*). The
answer gives a command for a container and one line for any other machine.
The token is single use and valid for 60 minutes, and it is not shown again.

**On the child machine: one line.** Paste the line from the answer, as the
user the child will run as. Nothing needs installing first (Linux or macOS):

```bash
curl -fsSL https://raw.githubusercontent.com/HoplitaAI/Hoplita/main/install.sh | \
  HOPLITA_SPAWN_SERVER=https://hoplita.net HOPLITA_SPAWN_HANDLE=alice-server-1 \
  HOPLITA_SPAWN_TOKEN=<TOKEN> sh -s -- --child
```

On Windows the answer gives the same in PowerShell.

- It installs a HOPLITA **with no window** (no Qt, no shortcuts) into its own
  folder, `~/hoplita-alice-server-1`. It is never your own install, which may
  already be someone. Without a Python 3.11+ on the machine, it fetches one into
  that folder (see INSTALL.md, *One line*). Add `--browser` at the end if the
  child must browse the web.
- It leaves the child **running as a service** of that user, starting again at
  every boot: a systemd user unit on Linux, with lingering turned on so it
  outlives your ssh session (if the system refuses, the installer prints the
  `sudo loginctl enable-linger` line to run); a LaunchAgent on macOS; a logon
  task on Windows. Where there is no service manager (a container), it runs as
  a detached process and the installer says it will not start again after a
  reboot.
- Nothing is asked. The vault opens by itself (*Its vault*, below), the first
  boot enrolls with the token, writes the kit into its `config.ini` and comes
  online.
- The token goes to `spawn.env` in the child's folder (readable only by that
  user), never into the service definition. The MCP reads it at start, takes it
  out of its environment and deletes the file once the token is spent. Once
  spent it is worthless, so it does no harm in the shell's history.
- Its log: `journalctl --user -u hoplita-alice-server-1 -f` on Linux. Running
  the same line again updates the child. The same line with `--uninstall
  --prefix ~/hoplita-alice-server-1` instead of `--child` removes the service
  and the program and keeps its data.
- What the child runs, it runs on that machine as that user. For anything you
  do not trust, use a container (7c).
- **Several children on one machine**, of one person or of several who share
  its administration, each from its owner's own line, do not get in each
  other's way: each lives in its own folder with its own vault, memory and
  model; each gets a port no other child holds (a stopped one included), and
  one that finds its port taken when it starts moves to the next free one,
  noted in its `.hoplita-child.json`, instead of failing; a file a child makes
  with no path given goes to its own `outputs/`, never to the shared home.
  Uninstalling one leaves the others running.

**Its vault.** By default it is **open**: its key is the file `vault.key`
beside it, and every start says so. It is open when a spawn token boots a
machine with no vault yet, when `vault.key` already exists, or with
`HOPLITA_VAULT=open`. Nobody types into such a machine, and a password kept in
a file next to it would protect nothing. A machine whose vault already exists
never gets an open one because a token shows up. To lock the vault, see
*With a password*, below.

**With a password (optional).** For whoever wants the vault locked at rest:
an MCP given `HOPLITA_VAULT_PASSWORD` uses it, keeps no key file, and takes the
variable out of its environment. It also clears it from the start-up copy that
`/proc/<pid>/environ` shows on Linux and `ps -E` shows on macOS. On Windows that
copy is not cleared. Decide this **before the first start**: a vault made
open does not take a password later, nor the other way round. Keep the password where the MCP's user cannot read it, or the
agent's commands can read it too:

1. The password, readable only by root:

   ```bash
   install -d -m 700 /etc/hoplita
   install -m 600 /dev/null /etc/hoplita/child.env
   IFS= read -rsp 'Child vault password: ' P && printf "HOPLITA_VAULT_PASSWORD='%s'\n" "$P" > /etc/hoplita/child.env; unset P
   ```

   `read -s` asks without echoing it, so the password never reaches the
   shell's history. The single quotes keep backslashes and leading or trailing
   spaces as typed: without them systemd drops them. A password for this file
   cannot contain a single quote (`'`).

2. A system unit instead of the user one the line makes, as root, in
   `/etc/systemd/system/hoplita-child.service` (replace `<user>`):

   ```ini
   [Unit]
   Description=HOPLITA child node
   After=network-online.target
   Wants=network-online.target

   [Service]
   User=<user>
   EnvironmentFile=/etc/hoplita/child.env
   Environment=HOPLITA_HOME=/home/<user>/hoplita-alice-server-1
   ExecStart=/home/<user>/hoplita-alice-server-1/venv/bin/python /home/<user>/hoplita-alice-server-1/hoplita-mcp.py --host 127.0.0.1 --port 9177
   Restart=on-failure

   [Install]
   WantedBy=multi-user.target
   ```

   systemd reads the file as root and passes it to the MCP, which runs as
   `<user>`; neither the MCP nor the agent's commands can read it. Install
   with the line but stop its user unit before the first start
   (`systemctl --user disable --now hoplita-alice-server-1`), then
   `systemctl enable --now hoplita-child`. The token waits in `spawn.env` for
   that first start.

**An older server, or an MCP already running.** A server that does not list
`spawn_kit` mints a plain token with no kit. Set `[API]` in the child's
`config.ini` by hand, start its MCP, and enroll it with the CLI
(`python install.py --cli` builds it):
`cli/dist/hoplita-cli --spawn --server https://hoplita.net --token <TOKEN> --handle server-1`.

**Back on your machine.** The child appears in **Contacts** as an accepted
contact with presence. Its trust is `open` both ways, because it is yours. Chat:
«list my children» (`fed_list_children`).

**Give it work:**

- **💬 Open session** on it,
- Chat: «ask server-1 to …» (`fed_ask_agent`),
- a plan target `child:server-1`,
- a CRON job with target `child:server-1`. Such a job fires only while the
  child is online.

**Give it folders.** Sharing works as with any contact (5.1). The child has no
GUI to press **Accept**, so tell it to accept:

- in a session: «accept the folder recipes from alice», or
- on the child machine: `hoplita-cli "accept the folder recipes from alice"`.

**Emancipate a pack to it.**

1. Open a local child's pack (7a).
2. Set **Runs on** to the node (🖥 server-1), then **Save**.
3. The pack is sent sealed to the node. The node wears it on incoming turns:
   its role and its tool list. A node only accepts a pack from its owner.
4. Saving again re-sends the pack. Setting **Runs on** back to *here, as a
   sub-agent* brings it home.

**Revoke it.** In the **Contacts** tab a federated child carries 👶; right-click
it → **🗑 Revoke child**. Or from the chat: «revoke my child server-1»
(`fed_revoke_child`).

- Its session dies at once, and its identity and contact are deleted.
- Packs that ran on it come back home.
- What it already received stays on that machine.
- Revoking is irreversible. To bring it back, enroll it again with a new token.

### 7c. A child in a container (docker or podman, Linux)

A federated child (7b) that lives in a container on **this** machine. Use it for
jobs you would rather keep off your own system: an MCP server you do not trust,
media work with ffmpeg, «download that tool and figure it out». What it runs,
installs or breaks stays inside the container. It reaches nothing of yours
except what you send it.

**Create it.** **Contacts** → **+ 👶 Emancipated child**:

1. **Name**, for example `video`. Its handle on the server is your handle plus
   the name (`alice-video`).
2. **Model it thinks with**:
   - your base model,
   - one of your pool models, or
   - **A model of its own…**: URL, model id and key.
3. **Hand over my web search too**: optional. It needs a `[SEARCH]` provider
   with an `api_key` on this machine.
4. Pick one:
   - **Start here, in a container** builds the `hoplita-child`
     image if this machine lacks it, then starts the container. The first
     build takes a few minutes.
   - **Token and commands** gives you the commands to run yourself: one for a
     container, here or on another Linux machine with docker or podman, and
     the one line for any machine, with nothing installed first (7b).

From the chat: «create a container child called video and start it»
(`fed_spawn_child` with `launch`).

**What travels.** The enrollment token carries a **starter kit**:

- the model's URL, id, key and `extra_body`;
- your language;
- optionally, the web search provider and its key.

The kit is sealed with a key derived from the token. The server keeps only the
token's hash, so it stores the kit but cannot open it. It hands the kit over once,
on enrollment, and deletes it.

> **Every key in the kit is one the child holds.** Anything running inside the
> container can read it and spend it, including an untrusted MCP server. For a
> child that acts on its own, give it **a model of its own** with a key that has
> a spending cap.

**The command.** Run it as is on any machine that has the image:

```bash
docker run -d --name alice-video --restart unless-stopped -v alice-video:/data \
  -e HOPLITA_SPAWN_SERVER=https://hoplita.net -e HOPLITA_SPAWN_HANDLE=alice-video \
  -e HOPLITA_SPAWN_TOKEN=<TOKEN> hoplita-child
```

- On a machine without the image, build it first from a HOPLITA install:
  `python3 hoplita_child_image.py build`. The build copies only the code, its
  languages, `config.ini.example` and `requirements.txt` (the GUI's packages are
  left out). It never copies your data folder.
- The image has Python, ffmpeg, git, curl, Node (`npx`) and `uv` (`uvx`).
- `/data` is a named volume. It holds the child's vault, config and work, and it
  survives restarts and image rebuilds. Remove it with `docker volume rm
  alice-video`.
- The child's MCP enrolls itself at start and writes the kit into its
  `config.ini`. The token is taken out of its environment. A restart sees the
  identity and does not enroll again.

**Without a container.** Nothing in this boot needs the container: the answer
also gives the one line for any machine, with nothing installed first (7b).

**Its vault.** Open by default, as in 7b: its key is `/data/vault.key`, and
every start says so. To lock it instead, start the container with
`-e HOPLITA_VAULT_PASSWORD=…`. The MCP then keeps no key file and clears the
password from its environment.

**Use it** like any federated child (7b): **💬 Open session**, «ask
alice-video to …», a plan target or a CRON job. To give it a file, share a
folder or send it in a session. It can install an MCP server itself through its
own config tools.

**Server.** Only a server that lists `spawn_kit` in its protocol answer can carry
the kit. An older server still mints a plain token. **Create and start here**
refuses on such a server, because the container would have no model.

## 8. Troubleshooting

| You see | What it means | What to do |
|---|---|---|
| *No federated identity. Register from the chat…* (Contacts header) or *ℹ️ You're not part of a federation (local-only)…* (Pull/Push) | This machine has no identity | Register (step 2), or copy your identity file (step 2, second machine) |
| *Registration failed: You need an invitation code…* / *…not accepting new accounts.* | The server is invite-only, or closed | Get a code from the server's operator, or use another server |
| *Registration failed: That invitation code is used up / expired / withdrawn…* | The code exists but no longer admits anyone: its uses are spent, its date passed, or the operator withdrew it | Ask whoever gave it to you for a new one |
| *Registration failed: That handle already exists* | The handle is taken | Pick another handle |
| *Login failed: … verif…* with a `fed_verify` next step | The email was never verified | Verify with the PIN (step 2.4) |
| *This server has no account for this handle…* | The account was deleted on the server, or you are on the wrong server | Leave with the local-only option (step 2, *Leave the federation*), then register again |
| *Verification failed: Wrong PIN* / *PIN expired* | Typo, or more than 15 minutes have passed | Ask the operator for a new PIN. Do not register again |
| *Could not add contact: Handle not found* | Wrong handle, or the contact is on another server | Type the bare handle, on the same server |
| *⚠ The public key for 'bob' does NOT match…* — BLOCKED | The key the server returns is not the one you signed | Compare fingerprints with bob over another channel (section 4). If bob really reset their identity, remove bob and add the contact again |
| *This server speaks protocol … and your HOPLITA speaks … Update HOPLITA (install.py --update)…* | The server no longer serves your version of HOPLITA | Update: `python3 install.py --update` ([INSTALL.md](INSTALL.md)) |
| *Your live socket is … — contacts see YOU offline…* | The live connection is down; everything else uses REST | Check that a proxy or firewall allows WebSocket to the server, and that `websocket-client` is installed in HOPLITA's venv |
| *📴 Sync: can't reach the server (offline)…* with the cause underneath | No network, the server is down, or it did not answer in time (the line says which, how long it took and the size) | Nothing is lost. Sync runs again later. The same line is in the MCP's log as `[SYNC] push failed after …` |
| *⚠️ Your last automatic upload didn't go through…* (at start) | A background upload of the last session failed, for the reason that follows | Press **⇈ Push** |
| *🔑 Sync: couldn't decrypt the server snapshot…* | This machine has a different identity from the one that pushed | Use the same identity file on both machines (step 2) |
| *⚠ Sync: the server refused the upload twice…* | Two machines pushed at the same moment | Press **⇊ Pull**, then **⇈ Push** |
| *🚫 Quota exceeded (…)* | Your plan's limit on the server: the **☁** line over the chat shows which one, in red | Prune heavy stores (`fed_sync_breakdown`), or ask the server's operator |
| *⚠️ Your last upload on close didn't go through…* | The push on close failed | Press **⇈ Push** |
| *The folder '…' is marked LOCAL-ONLY…* | The folder is locked (5.7) | Lift the lock first, if you really want to share it |
| *Appointment rejected — You don't have permission…* | The recipient's **Appointments** permission for you is **no** or not set | Ask them to set it to auto-accept or review |
| *That invite has not fully arrived yet.* / *This transfer died half-way…* | A code transfer is incomplete | Wait, or discard it and ask for a new invite (5.5) |
| *'…' exists and is not empty.* | The destination for an accepted code project must be empty | Choose an empty or new folder |
| *❌ The MCP is not responding at …* (`--spawn`) | The child's MCP is not running | Start it (7b, *An older server*), then run `--spawn` again |
| *Enrollment failed: …* naming an invalid, used or expired token | Tokens are single use and live 60 minutes | Create a new token |
| *This instance already has a federated identity…* | A child must enroll into a clean vault | Use a fresh install or data folder for the child |
| *Could not reach "…"* on a tied tab | The tied machine is off or not logged in | Start it and log it in, or `/pin off` |
| A federation request does nothing, and the agent says the tool is blocked | The **Tools** firewall blocks the *Federation: …* families | Unblock them in the **Tools** tab |
