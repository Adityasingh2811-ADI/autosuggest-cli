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

### ADI team install (managed hosts) — no git, no network

If your team lead has published a shared copy, you don't need git, GitHub
access, or a modern default Python. Just run the installer from the shared
path they gave you — it loads Python for you and installs from a local,
offline wheelhouse into your own `~/.local`:

```bash
bash <shared-path>/autosuggest-cli/install-linux.sh
rehash            # tcsh only, so it finds the new commands
suggest-start     # hooked bash with Python + Perforce + modules
```

`<shared-path>` is whatever your team lead published to (e.g.
`/home/<lead>/autosuggest-cli`). Re-running it is safe (it clean-reinstalls).
Maintainers: to publish or refresh that shared copy, see
[Publishing a shared copy](#publishing-a-shared-copy-maintainers) below.

### Managed Linux hosts (Exceed TurboX / EDA-CAD farms) — one-shot install

On managed ADI hosts the login shell is csh/tcsh, the system Python is too
old, Perforce comes from environment modules, and the system dotfiles are
read-only. A single idempotent script handles all of that. Run it **once**:

```bash
# from a clone of this repo:
bash install-linux.sh

# or straight from GitHub, no clone needed:
curl -fsSL https://raw.githubusercontent.com/Adityasingh2811-ADI/autosuggest-cli/master/install-linux.sh | bash
```

It loads the right Python module, `pip install --user`s the package, writes
`~/.suggest_bashrc` (Modules init → `module load` Python + Perforce →
re-adds `~/.local/bin` to PATH → activates the hook), and adds an idempotent
auto-launch block to `~/.cshrc.user` so every interactive login drops you
into a hooked bash with `p4` already working. No further changes are needed.

Re-running the script is safe (it never duplicates dotfile entries). Useful
overrides:

```bash
PY_MODULE=python/adi/3.12.2 \
P4_MODULES="perforce/adi/r19.1 p4v/adi/p4v-2024.1.2591061" \
NO_AUTOLAUNCH=1 \
  bash install-linux.sh        # NO_AUTOLAUNCH keeps csh as login shell;
                               # start the tool manually with 'suggest-start'
```

Kill switch (no reinstall required):

```bash
touch ~/.no_autosuggest   # disable auto-bash on next login
rm    ~/.no_autosuggest   # re-enable
exec tcsh -f              # drop to plain csh for the current session
```

See [BASH_PORTING_NOTES.txt](docs/design/BASH_PORTING_NOTES.txt) for the full rationale.

### Publishing a shared copy (maintainers)

To let teammates install without git or network access, publish a shared,
read-only copy once (and again whenever you want to ship an update). With no
`AUTOSUGGEST_SHARE` set it publishes to `$HOME/autosuggest-cli`:

```bash
module load python/adi/3.12.2
bash install-linux.sh publish                       # -> $HOME/autosuggest-cli
# or a custom location:
AUTOSUGGEST_SHARE=/some/shared/path bash install-linux.sh publish
```

This clones/updates the repo there, builds an **offline wheelhouse** (`dist/` —
the package plus all dependencies), makes it world-readable (`a+rX`), and
**checks that a non-group user can actually reach it** — warning you with the
exact `chmod` to run if any parent directory isn't traversable.

Because the farm is all NFS, any user on any host can then install with **two
commands** (no copy, no git, no network) — the `publish` output prints them:

```bash
bash $HOME/autosuggest-cli/install-linux.sh
rehash
```

**Reaching users outside your group:** they only need read + traverse on the
path, not group membership. If you publish under your home, open just the
traverse bit so others can pass through to the (world-readable) tool dir:

```bash
chmod o+x $HOME       # lets others traverse to a known path; does NOT list your home
```

The `publish` step's reachability check tells you if this is needed. Project
spaces (`/proj*`) are already world-traversable, so no `chmod` is needed there.

## Quick Start

**First, know your login shell** — the setup differs, and the most common
mistake is running a bash line in csh/tcsh:

```bash
echo $0        # or:  ps -p $$ -o comm=
```

### Step 1 — bootstrap the engine (any shell)

```bash
suggest-import     # import existing history (optional, recommended)
suggest            # launch the interactive REPL — works in ANY shell
```

`suggest` is a self-contained REPL with inline ghost-text. It works everywhere
(bash, zsh, **tcsh**, PowerShell) and needs no hook, so it's the fastest way to
start. The daemon auto-starts; suggestions improve as you use it.

### Step 2 — hook your real shell (optional, for suggestions at your normal prompt)

Pick the line that matches your shell. **The syntax is not interchangeable.**

| Login shell | Add this to your rc file |
|---|---|
| **bash** | `eval "$(suggest-hook bash)"` &nbsp;→ `~/.bashrc` |
| **zsh** | `eval "$(suggest-hook zsh)"` &nbsp;→ `~/.zshrc` |
| **tcsh / csh** | `` eval `suggest-hook tcsh` `` &nbsp;→ `~/.tcshrc` *(backticks!)* |
| **PowerShell** | `suggest-hook install powershell` |

> ⚠️ **csh/tcsh users:** do **not** run `eval "$(suggest-hook bash)"`. csh/tcsh
> cannot parse `$(...)` and it fails with `Illegal variable name`. Use the
> backtick line above, or let the installer do it for you (below).

### Managed EDA-CAD host (Exceed TurboX, tcsh login)? Skip the steps above

Run the one-shot installer — it's a plain command tcsh runs fine (no `$(...)`),
and it loads Python, puts `~/.local/bin` on PATH, and writes the correct hook
into your dotfiles so suggestions just work on next login:

```bash
bash install-linux.sh
```

See [Managed Linux hosts](#managed-linux-hosts-exceed-turbox--eda-cad-farms--one-shot-install)
above for details and options.


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

**tcsh / csh** (common on managed EDA-CAD hosts):
```tcsh
# Add to ~/.tcshrc — note the BACKTICKS, not $(...):
eval `suggest-hook tcsh`

# Or auto-install (also adds ~/.local/bin to PATH):
suggest-hook install tcsh
```

> ⚠️ **csh/tcsh cannot parse `$(...)`.** Running the bash line
> `eval "$(suggest-hook bash)"` in tcsh fails with `Illegal variable name`.
> In tcsh you **must** use backticks: `` eval `suggest-hook tcsh` ``.
> The tcsh hook records telemetry and prints next-step suggestions; it does
> **not** provide inline ghost-text or Tab completion (tcsh has no
> programmable line editor — use the `suggest` REPL or the zsh hook for that).
> On a managed host, prefer the [one-shot installer](#managed-linux-hosts-exceed-turbox--eda-cad-farms--one-shot-install),
> which wires everything up for you so you never hand-type a hook line.

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

> If `suggest-import: Command not found` in tcsh, the pip `--user` scripts
> aren't on your `PATH` yet. Fix it with:
> `set path = ( $HOME/.local/bin $path ); rehash`

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
