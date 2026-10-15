# Installing HOPLITA

HOPLITA needs **Python 3.11 or newer** and, to get the code, **git** (or the
ZIP from the repository page). Everything else is installed by `install.py`
into a folder of its own: no compiler and no administrator rights. The Python
dependencies come as prebuilt packages for Linux, Windows and macOS (Apple
Silicon and Intel).

After installing, read **[First steps](FIRST-STEPS.md)**: the model and the
search key HOPLITA needs before it can do anything useful.

- [Linux](#linux)
- [macOS](#macos)
- [Windows](#windows)
- [Optional parts](#optional-parts)
- [Updating](#updating)

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

The installer puts two launchers on the desktop: **HOPLITA MCP Server** (start
it first; it asks for the vault password in a small window) and **HOPLITA**,
the window.

If the canvas stays blank or the tray icon does not show, the system is missing
the libraries Qt's web engine uses. `python3 install.py --system-libs`
installs them with your distribution's package manager (it is the one flag
that asks for `sudo`).

## macOS

macOS 13 (Ventura) or newer. The `python3` that comes with the Command Line
Tools is too old, so install a current one. With [Homebrew](https://brew.sh):

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install python@3.12 git
```

Homebrew installs Apple's Command Line Tools on the way if they are missing.
Without Homebrew, the installer from
[python.org](https://www.python.org/downloads/macos/) works too, and
`xcode-select --install` gives you git.

Then:

```bash
git clone https://github.com/HoplitaAI/Hoplita.git
cd Hoplita
python3.12 install.py
```

The installer puts two launchers on the desktop: **HOPLITA-MCP** (start it
first; it opens a terminal and asks for the vault password there) and
**HOPLITA-GUI**, the window.

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
| `--desktop` | The desktop hands: the model sees and uses your screen | See the [guide](GUIDE.md) |
| `--lsp` | Diagnostics and navigation in the code editor | — |
| `--diffr` | The structural diff in the whiteboard | — |
| `--system-libs` | Linux system libraries for the canvas, the tray and the hands | `sudo` |
| `--all` | All of the above | |

The default install folder is `~/hoplita` on Linux and macOS and
`%APPDATA%\hoplita` on Windows. `--prefix <folder>` picks another one.

## Updating

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

A tool whose library is missing says so and prints that same command.

To remove HOPLITA, `python3 install.py --uninstall` deletes the program and
keeps your data; `--uninstall --purge` deletes the data too.
