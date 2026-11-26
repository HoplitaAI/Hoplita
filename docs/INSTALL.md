# Installing HOPLITA

HOPLITA needs **Python 3.11 or newer** and, to get the code, **git** (or the
ZIP from the repository page). Everything else is installed by `install.py`
into a folder of its own: no compiler and no administrator rights. The Python
dependencies come as prebuilt packages for Linux, Windows and macOS (Apple
Silicon and Intel).

After installing, read **[First steps](FIRST-STEPS.md)**: the model and the
search key HOPLITA needs before it can do anything useful.

- [One line](#one-line)
- [Linux](#linux)
- [macOS](#macos)
- [Windows](#windows)
- [Optional parts](#optional-parts)
- [Updating](#updating)

## One line

Without installing Python or git first. On Linux and macOS, in a terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/HoplitaAI/Hoplita/main/install.sh | sh
```

On Windows, in PowerShell:

```powershell
irm https://raw.githubusercontent.com/HoplitaAI/Hoplita/main/install.ps1 | iex
```

It downloads HOPLITA, uses the machine's Python when it is 3.11 or newer and
can make a virtual environment, and otherwise fetches a standalone Python 3.12
(with [uv](https://github.com/astral-sh/uv)) into the install folder: no
administrator, nothing installed system-wide, no change to `PATH`. On macOS
and Windows it always uses that standalone Python (on a Mac, the reason is
under [macOS](#macos)). Then it runs `install.py`.

Options go after `sh -s --` on Linux and macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/HoplitaAI/Hoplita/main/install.sh | sh -s -- --all
```

and on Windows through a script block:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/HoplitaAI/Hoplita/main/install.ps1))) --all
```

Running the same line again updates the install, new Python libraries
included; adding `--uninstall` removes it (your data stays; `--uninstall
--purge` deletes it too). The code features (projects, shared repositories,
the whiteboard) still need **git**; the sections below say how to get it.

The rest of this page is the manual install, from a clone.

## Linux

Most distributions already ship Python 3.11+ and git. Check:

```bash
python3 --version
git --version
```

If something is missing:

| Distribution | Command |
|---|---|
| Debian, Ubuntu, Mint | `sudo apt install python3 python3-venv git` |
| Fedora, RHEL, Rocky, Alma | `sudo dnf install python3 git` |
| Arch, Manjaro | `sudo pacman -S python git` |

On Debian and Ubuntu, `python3-venv` is a separate package and the installer
cannot create its virtual environment without it.

Then:

```bash
git clone https://github.com/HoplitaAI/Hoplita.git
cd Hoplita
python3 install.py
```

The installer puts one launcher on the desktop, **HOPLITA**: it starts the
server (it asks for the vault password in a small window) and then the window.
If the server is already running, it opens only the window. The same launcher
is in the applications menu (`~/.local/share/applications`): on GNOME, which
shows no icons on the desktop, open HOPLITA from the activities search or the
app grid.

If the canvas stays blank or the tray icon does not show, the system is missing
the libraries Qt's web engine uses. `python3 install.py --system-libs`
installs them with your distribution's package manager (it is the one flag
that asks for `sudo`).

GNOME has no tray of its own: install and enable the AppIndicator extension
(`gnome-shell-extension-appindicator`; Ubuntu ships it enabled). Without a tray
HOPLITA runs with no icon and the window's ✕ quits it instead of hiding it,
which also stops the scheduled jobs.

## macOS

macOS 13 (Ventura) or newer. Nothing to install first: the installer brings
its own Python. The `python3` that comes with macOS only starts it; if it
is missing, macOS offers the Command Line Tools on the first `git` or
`python3`, and that is all you need.

```bash
git clone https://github.com/HoplitaAI/Hoplita.git hoplita-src
cd hoplita-src
python3 install.py
```

The installer fetches a standalone Python 3.12 into the install folder and
continues on it, whatever Python the machine has. On a Mac it never uses
Homebrew's or python.org's Python even when they are there: those are
*framework* builds, under which HOPLITA's processes run as «Python» and
macOS attributes the Screen Recording and Accessibility permissions to that
Python, not to HOPLITA. The standalone one keeps HOPLITA's name on every
notice and every switch.

The clone gets its own name because macOS folder names ignore letter case: a
clone called `Hoplita` in your home folder would be the same folder as the
install, `~/hoplita`. The installer refuses that and says so.

The installer puts **HOPLITA.app** in `~/Applications`, with a link on the
desktop: Spotlight (⌘ Space → HOPLITA) and Launchpad find it. One double-click
starts everything, with no terminal: the vault password is asked in a window,
then HOPLITA opens.

### What macOS asks, and what to answer

macOS asks for some permissions the first time HOPLITA needs them: the first
time the desktop hands are used, HOPLITA asks macOS to show its notices for
Screen Recording and Accessibility. Neither notice has an Allow button: each
opens **System Settings → Privacy & Security** at its list, and the switch is
turned on there (macOS lets no app turn it on by itself). macOS shows each
notice once; if it was dismissed, go to the same place by hand.

Opened with **HOPLITA.app**, the notices name **HOPLITA**: the installer gives
HOPLITA its own copy of the Python it runs on, so the permissions are
HOPLITA's alone and not every Python script's. Started by hand from a
terminal, they go to that terminal (Terminal, iTerm…) instead. Chat, the
Terminal tab, the canvas, the whiteboard, the web browser and the CLI need
none of them.

In order, the first time a turn uses the desktop hands:

1. If the turn's model is not on this machine or your local network, HOPLITA
   asks first, in a window of its own: *Your screen would leave this
   machine*, with **Allow for this turn** or **No**. A model on this machine
   or your network is not asked. To stop being asked on every turn, set
   `remote_model = allow` under `[HANDS]` in `config.ini` (see
   [CONFIG.md](CONFIG.md)).
2. macOS shows its two notices, Accessibility and Screen Recording. Each
   opens System Settings at its list, with HOPLITA in it: turn HOPLITA on.
   Accessibility asks your Mac password; Screen Recording does not.
3. Screen Recording works at once. For Accessibility, quit HOPLITA (⌘Q) and
   open it again. That first turn ends by saying which permission was
   missing; the next one has both.
4. Nothing is asked again: the switches stay on until you turn them off.

| macOS asks | Answer | What it is for | Without it |
|---|---|---|---|
| «HOPLITA» would like to record this computer's screen (**Screen Recording**) | **Open System Settings**: HOPLITA is in the list, turn it on (if it is not, press **+** and pick `venv/bin/HOPLITA` inside the install folder, `~/hoplita`) | The desktop hands look at the screen (`hands_screenshot`, and the check after each action) | The model sees only the desktop background, no windows |
| «HOPLITA» would like to control this computer using accessibility features (**Accessibility**) | **Open System Settings**: HOPLITA is already in the list, turn it on | The desktop hands click and type for you (`hands_click`, `hands_type`, `hands_key`) | Clicks and keys do nothing (each action reports `verified: false`) |
| «HOPLITA» is requesting to bypass the system private window picker and directly access your screen and audio (macOS 15 and later) | **Allow** | The same Screen Recording, confirmed again: macOS asks it of apps that capture the whole screen, after the permission is on and from time to time later (about monthly) | Captures go on while the notice is open; what refusing it does has not been tried |
| «HOPLITA» would like to access Apple Music… (or Photos, Contacts, Calendars, Reminders…) | **Don't Allow**, unless you asked HOPLITA for something kept there | A command the model ran in the terminal went through your home folder, and macOS guards those folders and libraries one by one | That command does not see those folders. While the notice is unanswered the command waits: one waited until the terminal's time limit (5 minutes) |
| «Google Chrome for Testing» notifications | Either | It is the browser HOPLITA browses with; nothing of HOPLITA uses its notifications | Nothing changes |
| HOPLITA would like to find devices on your local network (**Local Network**) | **Allow**, if your model runs on another machine of your network | Reaching that machine | HOPLITA cannot reach the model there |

Why Accessibility wants the restart: macOS keeps answering a running
HOPLITA with what the switch said when it started. To check, ask HOPLITA for
`hands_status`: it says `accessibility permission: granted` and `screen
recording permission: granted`, or which one is missing. Both permissions
can be turned off again at any time in **System Settings → Privacy &
Security**.

On macOS the desktop hands have both layers: the window-and-element layer
through the accessibility API (the same one Linux drives through AT-SPI and
Windows through UI Automation, so a window's buttons and fields are read and
acted on by name), and the pixel layer for captures, clicks by coordinates
and keys. Both need the two permissions above and nothing more to install:
the plain install brings the capture library, so `--desktop` is not needed
on a Mac.

## Windows

1. **Python**: download the installer from
   [python.org](https://www.python.org/downloads/windows/) (3.11, 3.12 or
   3.13, 64-bit). On its first screen tick **«Add python.exe to PATH»**.
   Use this installer rather than the Microsoft Store version: the Store one
   redirects writes inside `%APPDATA%`, which is where HOPLITA installs.
2. **Git**: download [Git for Windows](https://git-scm.com/download/win) and
   accept the defaults.
3. Open a new **PowerShell** (so it sees both) and run:

```powershell
git clone https://github.com/HoplitaAI/Hoplita.git
cd Hoplita
py install.py
```

The installer creates one shortcut, **HOPLITA**, on the desktop. It starts the
server (asking for the vault password in a small window) and then the
window.

A file open in Word or Excel is locked by Windows: the office tools say they
could not write it, and the file stays as it was.

## Optional parts

Add these flags to the same command (`python3 install.py --browser`, …). Each
one can also be added later.

| Flag | Adds | Needs |
|---|---|---|
| `--browser` | The browsing agent and the scraper (Playwright with its Chromium, browser-use, crawl4ai) | A large download |
| `--cli` | The command-line client | [Go](https://go.dev/dl/) |
| `--desktop` | The desktop hands on Linux and Windows: the model sees and uses your screen (a Mac has them from the plain install) | See the [guide](GUIDE.md) |
| `--lsp` | Diagnostics and navigation in the code editor | — |
| `--diffr` | The structural diff in the whiteboard: Linux x86_64, macOS Apple Silicon, Windows x86_64 | — |
| `--system-libs` | Linux system libraries for the canvas and the tray; together with `--desktop`, also the hands' accessibility binding and input helper | `sudo` |
| `--full` | Every part above that needs neither Go nor root: browser, desktop, lsp, diffr | A large download |
| `--all` | All of the above | |

The default install folder is `~/hoplita` on Linux and macOS and
`%APPDATA%\hoplita` on Windows. `--prefix <folder>` picks another one.

## Updating

Installed with the one-line command: run it again. From a clone:

```bash
git pull
python3 install.py --update
```

`--update` replaces the program and keeps your configuration and data. It does
not install new Python libraries; when a version adds one to
`requirements.txt`, install it into HOPLITA's own Python:

```bash
~/hoplita/venv/bin/python -m pip install -r requirements.txt                 # Linux, macOS
%APPDATA%\hoplita\venv\Scripts\python.exe -m pip install -r requirements.txt   # Windows
```

A tool whose library is missing says so and prints the `pip install` command
for that package, run with HOPLITA's own Python.

To remove HOPLITA, `python3 install.py --uninstall` deletes the program and
keeps your data; `--uninstall --purge` deletes the data too.
