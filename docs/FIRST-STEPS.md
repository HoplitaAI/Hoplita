# First steps — what HOPLITA needs to start

HOPLITA brings the tools, the memory and the window. Two things it cannot bring
come from you: **a language model** and **a web search key**. With those two it
works; everything else is optional.

Not installed yet? See **[Installing HOPLITA](INSTALL.md)**.

## 1. A model (required)

HOPLITA talks to any server that speaks the **OpenAI API**
(`/v1/chat/completions`). It can be a cloud service or a model on your own
machine.

| Where | Examples | Server URL |
|---|---|---|
| Cloud | OpenAI, OpenRouter, xAI, DeepSeek, Mistral, Groq… | the provider's, e.g. `https://api.openai.com/v1` |
| Your machine | llama.cpp (`llama-server`), Ollama, LM Studio, vLLM, llama-swap | e.g. `http://127.0.0.1:8080/v1` (llama.cpp), `http://127.0.0.1:11434/v1` (Ollama), `http://127.0.0.1:1234/v1` (LM Studio) |

What the model must have:

- **Tool calling.** HOPLITA is an agent: the model acts by calling tools. A
  model or server without function calling can chat but cannot do anything.
  With llama.cpp, start `llama-server` with `--jinja`; with Ollama and LM
  Studio, pick a model marked as supporting tools.
- **Room to work.** The tools and the instructions take several thousand
  tokens before the first word of the conversation. Give it a context window of
  32k tokens at least; 64k or more is comfortable. **Ollama** runs models with
  a 4096-token window unless told otherwise, and it cuts a longer prompt
  without an error: set **Context length** in the Ollama app's settings, or
  start it with `OLLAMA_CONTEXT_LENGTH=32768`. HOPLITA reads the window Ollama
  runs with and, if its instructions do not fit, stops and says this instead
  of letting the model work without them.
- **Thinking.** A small model that thinks before every answer may think and
  stop without acting. For Ollama, `extra_body = {"reasoning_effort": "none"}`
  on that model's line turns the thinking off.

The URL goes **up to `/v1`**; HOPLITA adds the rest. A local server usually
needs no key.

**Where it goes:** start HOPLITA, press **Configuration** (top left). The dialog
opens on **Models**. Press **＋ Add model** and fill in a name of your choice,
the model id (as the server calls it), the server URL and the key. Save. That
model is your **base**: the chat, the browser, the memory, the planner and the
reviewer all run on it until you say otherwise.

## 2. A web search key (strongly recommended)

The model searches the web through a search provider, and every provider needs
its own key. Pick one:

| Provider | What it returns |
|---|---|
| [Perplexity](https://www.perplexity.ai/settings/api) | A written answer with its sources |
| [Tavily](https://tavily.com) | An answer and the sources behind it |
| [Exa](https://exa.ai) | The passages that answer, for the model to read and quote |
| [Firecrawl](https://www.firecrawl.dev) | Results with the pages as text |

**Where it goes:** **Configuration** → **Search**: choose the provider and paste
its key.

Without a key everything else works, but when the model needs the web it gets
an error saying the key is missing.

## 3. The vault password

The first time the server starts (the **HOPLITA** shortcut on the desktop) it
asks for a password. That first
password creates the encrypted database where notes, tasks, credentials,
memory and settings live; every later start asks for it again. **Keep it
safe**: the data is encrypted with it.

To change it later, with the server running: **Configuration** → **App** →
**Vault** → **Change the password…**, or from a terminal with HOPLITA's own
Python: `~/hoplita/venv/bin/python ~/hoplita/hoplita_passwd.py` (on Windows,
`%APPDATA%\hoplita\venv\Scripts\python.exe %APPDATA%\hoplita\hoplita_passwd.py`). The database and the identity file are
sealed again with the new one in place, all or nothing; the backups made
before that moment still open with the old one. A machine that starts
unattended with `HOPLITA_VAULT_PASSWORD` needs the new one written there too.
The password belongs to this machine: your other machines carrying the same
identity keep theirs, and keep syncing (section *Use the same identity on a
second machine* in [FEDERATION.md](FEDERATION.md)).

## That is all you need

With a model and a search key, write in the chat. Some things to try:

- «search the latest news about …» — web search
- «read this PDF and summarise it» (attach it) — files
- «remind me tomorrow at 9 to …» — tasks and cron
- the **Code** page with a folder of yours — a coding agent with git

## Optional, when you want more

| What | What it needs | Where |
|---|---|---|
| A browser the agent drives | `install.py --browser` | — |
| More models: a coder, one that sees images, a small fast one | Their servers | **Configuration** → **Models** |
| Pictures (make and edit images) | A server with the OpenAI Images API | **Configuration** → **Models** → **Pictures** |
| Federation: contacts, shared notes and projects | An account on a HOPLITA server | See [Federation](FEDERATION.md) |
| The command-line client | Go, then `install.py --cli` | — |

The full manual is the **[user guide](GUIDE.md)**; every setting is in
**[CONFIG.md](CONFIG.md)**.
