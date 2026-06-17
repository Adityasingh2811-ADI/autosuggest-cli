# autosuggest-cli

A context-aware CLI autosuggestion engine that provides real-time ghost-text predictions based on your command history. It uses a **frecency** algorithm (frequency + recency + directory context) to surface the most relevant suggestions as you type.

## Features

- **Ghost-text inline suggestions** — greyed-out predictions appear as you type, accept with `Right Arrow`
- **Tab completion menu** — cycle through ranked suggestions with `Tab` / `Shift+Tab`
- **Next-step suggestions** — after each command, see likely follow-up commands (learned from your patterns + predefined workflows)
- **Frecency scoring** — combines how often and how recently you used a command, boosted when you're in the same directory
- **Background telemetry daemon** — records commands via a lightweight socket without blocking your shell
- **Shell hook integration** — use your real shell (bash/PowerShell) while still feeding data to the engine
- **History import** — bootstrap suggestions from your existing bash or PowerShell history
- **Cross-platform** — works on Linux, macOS, and Windows

## Installation

```bash
pip install git+https://github.com/Adityasingh2811-ADI/autosuggest-cli.git
```

Requires Python 3.10+.

## Quick Start

```bash
# 1. Import your existing shell history (optional but recommended)
suggest-import

# 2. Launch the interactive shell
suggest
```

The daemon starts automatically when you launch `suggest`. Suggestions improve as you use it.

## Usage

### Interactive Shell

```bash
suggest
```

Opens a prompt-toolkit REPL with:
- Inline ghost-text (accept with `Right Arrow`)
- Completion menu (`Tab` / `Shift+Tab` to cycle)
- Next-step suggestions shown after each command (type the number `1`-`3` to accept)
- Built-in `cd` support with directory tracking

### Daemon Management

The background daemon records commands to a local SQLite database.

```bash
suggest-daemon start    # Start the daemon (auto-started by suggest)
suggest-daemon stop     # Stop the daemon
suggest-daemon status   # Check if running
```

### Shell Hooks

To record commands from your regular shell (not just the `suggest` REPL):

**Bash:**
```bash
# Add to ~/.bashrc
eval "$(suggest-hook bash)"

# Or auto-install:
suggest-hook install bash
```

The bash hook records telemetry and adds next-step suggestions after each
command, a frecency-aware Tab completion, and an accept-top-suggestion
binding (`Ctrl+F`, or `Right Arrow` at end of line). Installing `socat` is
recommended for lowest-latency telemetry. Stock bash cannot draw
continuously-updating inline ghost text — use the zsh hook for that.

**Zsh:**
```zsh
# Add to ~/.zshrc
eval "$(suggest-hook zsh)"

# Or auto-install:
suggest-hook install zsh
```

The zsh hook adds true inline ghost-text (accept with `Right Arrow` or
`Ctrl+F`) plus frecency completion and next-step suggestions.

**PowerShell:**
```powershell
# Auto-install to $PROFILE:
suggest-hook install powershell
```

### History Import

Bootstrap the engine with your existing command history:

```bash
suggest-import                     # Auto-detect and import all found history
suggest-import --bash ~/.bash_history
suggest-import --zsh ~/.zsh_history
suggest-import --powershell PATH
```

## How It Works

1. **Daemon** (`suggest-daemon`) — an async socket server that receives command telemetry and writes to `~/.cli_autosuggest.db` (SQLite with WAL mode)
2. **Engine** — queries the database with prefix matching, scores results using exponential recency decay (1-hour half-life) and a 3x context boost for commands run in the same directory
3. **Next-step resolver** — combines learned sequential patterns (command A often followed by command B) with predefined workflow rules (git flow, python dev, docker, etc.)
4. **TUI** — prompt-toolkit session that ties it all together with ghost-text, completion menus, and keybindings

## Predefined Workflows

The engine ships with workflow rules for common sequences:

- **git-flow** — `git status` -> `git add .` -> `git commit` -> `git push`
- **python-dev** — `pip install` -> `pytest` -> `pip freeze`
- **build-test-deploy** — `make build` -> `make test` -> `make deploy`
- **docker** — `docker build` -> `docker run` -> `docker ps`
- **navigation** — `cd` -> `ls` / `git status`

These complement (not replace) learned patterns from your actual usage.

## Data Storage

All data stays local:
- **Database:** `~/.cli_autosuggest.db`
- **PID file:** `~/.cli_autosuggest.pid`
- **Socket:** `/tmp/cli_autosuggest.sock` (Unix) or `127.0.0.1:19526` (Windows)

## License

MIT
