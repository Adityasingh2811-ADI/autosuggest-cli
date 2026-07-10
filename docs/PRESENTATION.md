# autosuggest-cli — Presentation to Senior Management

## Tool Highlights

### What It Is
- A **context-aware CLI suggestion engine** that learns from each engineer's command history
- Predicts the next command, offers ranked completions, and shows "next steps" after every action
- Built specifically for **ADI managed hosts** (tcsh/csh login shells, NFS homes, Exceed TurboX)

### Key Differentiators
1. **Zero-config install** — one `bash install-linux.sh` command handles Python, PATH, hooks, and history import
2. **Frecency-based ranking** — combines frequency + recency for suggestions that stay relevant
3. **Context-aware** — boosts suggestions based on current directory and project
4. **EDA workflow intelligence** — knows Vivado, Perforce, simulation, and module flows out of the box
5. **Security-first** — passwords, tokens, and secrets are automatically redacted before storage
6. **NFS-safe** — detects network filesystems and switches SQLite journal mode to avoid corruption
7. **Resilient** — if the daemon is down, recording falls back to direct DB writes (zero data loss)
8. **Non-invasive** — `touch ~/.no_autosuggest` disables instantly; removal is two lines deleted from rc file

### By the Numbers (Live on This Host)
- **1,081 commands** recorded (958 imported from history + 123 live)
- **377 unique commands** tracked
- **97% success rate** across all recorded commands
- **9 EDA workflows** built in (git, Vivado, Perforce, simulation, modules, Docker, etc.)
- **< 2 seconds** for 100 aggregate queries on 1,000-row DB (stress-tested)
- **5 concurrent writers, zero errors** (thread-safety verified)

### Architecture (30-Second Summary)
- **Shell hook** (`precmd` in tcsh, `PROMPT_COMMAND` in bash) records every command
- **Background daemon** receives telemetry over Unix socket (or TCP on Windows)
- **SQLite database** stores history with frecency scoring
- **Prediction engine** serves suggestions via prefix match + context boost + workflow rules
- **Next-step resolver** combines learned sequences with YAML workflow definitions

---

## Flowchart

```
┌─────────────────────────────────────────────────────────────┐
│                     INSTALLATION                             │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────────┐
│ git clone    │────▶│ bash install │────▶│ rehash           │
│ from GitHub  │     │ -linux.sh    │     │ (tcsh picks up   │
│              │     │              │     │  new commands)    │
└──────────────┘     └──────────────┘     └──────────────────┘
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                  ▼
   ┌─────────────┐  ┌─────────────┐   ┌─────────────────┐
   │ Loads Python │  │ Installs to │   │ Writes shell    │
   │ module       │  │ ~/.local    │   │ hooks + bashrc  │
   └─────────────┘  └─────────────┘   └─────────────────┘
                            │
                            ▼
               ┌────────────────────────┐
               │ Imports existing       │
               │ history (bash + tcsh)  │
               └────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                     DAILY USAGE                              │
└─────────────────────────────────────────────────────────────┘

  User types in shell
        │
        ▼
┌──────────────────┐    YES    ┌────────────────────────┐
│ Prefix matches   │─────────▶│ Show ghost-text        │
│ history?         │           │ (grey inline suggest.) │
└──────────────────┘           └────────────────────────┘
        │ NO                           │
        ▼                              ▼  User presses →
┌──────────────────┐           ┌────────────────────────┐
│ Tab pressed?     │           │ Accept suggestion      │
│                  │           └────────────────────────┘
└──────────────────┘
        │ YES
        ▼
┌──────────────────┐
│ Show ranked menu │
│ (frecency order) │
└──────────────────┘

  User runs command
        │
        ▼
┌──────────────────┐         ┌────────────────────────┐
│ precmd hook      │────────▶│ Record to daemon       │
│ fires            │         │ (or direct DB write)   │
└──────────────────┘         └────────────────────────┘
        │
        ▼
┌──────────────────┐         ┌────────────────────────┐
│ Next-step        │────────▶│ Show 1-3 suggestions   │
│ resolver         │         │ "What usually comes    │
│                  │         │  next?"                │
└──────────────────┘         └────────────────────────┘


┌─────────────────────────────────────────────────────────────┐
│                     UNDER THE HOOD                           │
└─────────────────────────────────────────────────────────────┘

┌─────────┐    Unix     ┌──────────┐    SQLite    ┌──────────┐
│  Shell  │───socket───▶│  Daemon  │────write────▶│   DB     │
│  Hook   │             │  (async) │              │ history  │
└─────────┘             └──────────┘              └──────────┘
                                                       │
     ┌───────────────────────────────────────────────┘
     ▼                    ▼                    ▼
┌──────────┐      ┌────────────┐      ┌────────────────┐
│ Frecency │      │ Context    │      │ Workflow YAML  │
│ Scoring  │      │ Boosting   │      │ (EDA rules)   │
└──────────┘      └────────────┘      └────────────────┘
     │                    │                    │
     └────────────────────┼────────────────────┘
                          ▼
                 ┌─────────────────┐
                 │  Ranked         │
                 │  Suggestions    │
                 └─────────────────┘
```

---

## Presentation Script (20 Minutes + Live Demo)

---

### PART 1: CONTEXT & MOTIVATION (4 min)

---

### Slide 1: The Problem We All Have (1.5 min)

**Talking points:**
> "How many commands do you think you type in a day? On average, engineers on our managed hosts run 200-400 commands daily. Module loads, Perforce syncs, Vivado builds, simulation runs, directory navigation."

> "Here's the thing — about 80% of those are repetitive sequences. You sync from Perforce, then open files, then build, then check the log. Every single time. But our shells have zero memory of these patterns."

> "On our managed hosts specifically, we face unique friction:"
- tcsh login shell — no modern autocompletion
- NFS home directories — slow filesystem operations
- Dozens of module loads and pinit commands to remember
- Complex EDA tool invocations with flags nobody memorizes

> "The result: wasted time, typos, and constant context-switching to look up the right command."

---

### Slide 2: What If Your Shell Learned From You? (1 min)

**Talking points:**
> "What if every command you typed made your shell smarter? What if it could predict what you're about to type, and after you run something, tell you what usually comes next?"

> "That's what autosuggest-cli does. It's a personal CLI assistant that:"
- Watches what you type (securely — passwords are never stored)
- Learns your patterns over time
- Predicts your next command in real-time
- Knows EDA workflows out of the box

> "Think of it like Google's autocomplete, but for your terminal — trained only on YOUR history, running entirely locally."

---

### Slide 3: How It Compares (1.5 min)

**Talking points:**
> "You might be thinking — doesn't bash have history search? Yes, Ctrl+R exists. Here's why this is different:"

- **Ctrl+R** — searches backwards through flat text. No ranking, no context, no predictions.
- **fish shell** — has great autosuggestions, but nobody runs fish on managed hosts. And it doesn't know EDA workflows.
- **autosuggest-cli** — frecency ranking (frequency × recency), directory-aware context boosting, EDA workflow intelligence, and it works within our tcsh environment.

> "The key insight: it doesn't just match text — it understands which commands are relevant RIGHT NOW based on where you are and what you just did."

---

### PART 2: LIVE DEMO — INSTALLATION (3 min)

---

### Slide 4: Install in Under 60 Seconds (Live Demo)

**Script:**
> "Let me show you the entire setup. I'll do it live."

**Demo actions:**
```
git clone https://github.com/Adityasingh2811-ADI/autosuggest-cli.git
cd autosuggest-cli
bash install-linux.sh
rehash
```

**While installer runs, narrate:**
> "That's it. Three commands. No admin privileges, no IT ticket, no Python setup. The installer:"
- Detects and loads the right Python module
- Installs everything into your personal ~/.local (no root needed)
- Imports your existing bash and tcsh history as seed data
- Writes the shell hooks that make it all work
- Sets up auto-launch so every new terminal is ready

> "It's also completely idempotent — re-running it just refreshes things. And if you ever want to remove it, it's two lines deleted from your rc file."

---

### Slide 5: Verify It's Working (Live Demo)

**Demo actions:**
```
suggest-selftest
```

**Narrate the output:**
> "One command tells you everything. Green PASS on version, redaction, database, engine, workflows, completers, auth, PATH. The INFO line at the bottom shows we already have over 1,000 commands in the database from the history import. Ready to go."

---

### PART 3: LIVE DEMO — DAILY USAGE (6 min)

---

### Slide 6: Ghost-Text Suggestions (Live Demo, 2 min)

**Demo actions:**
```
suggest-start
```

> "I'm now in the hooked shell. Watch what happens as I type:"

**Demo sequence 1 — Ghost text:**
- Type `git s` slowly → grey ghost text appears: `git status`
- Press Right Arrow → command is accepted
- Run it → output shows, then "Next steps:" appears

> "See that? As soon as I typed 'git s', it predicted 'git status' in grey. Right arrow accepts it instantly. And after it runs, it tells me what usually comes next."

**Demo sequence 2 — Tab completion:**
- Type `mod` → press Tab → ranked menu shows module commands
- Select `module load` from menu

> "Tab gives you a ranked menu. Not alphabetical like basic completion — ranked by how often and how recently you've used each command."

**Demo sequence 3 — Context awareness:**
- `cd` to a project directory
- Start typing → suggestions change to match what you do in THAT directory

> "Notice the suggestions changed when I moved directories. It knows that in this project, I usually run make, not git. That's context boosting."

---

### Slide 7: Next-Step Intelligence (Live Demo, 2 min)

**Demo actions:**
- Run `p4 sync //depot/...` (or simulate)
- Show the "Next steps:" output: `p4 opened`, `p4 resolve`, etc.

> "After a Perforce sync, it suggests opening files or resolving. These come from two sources:"

> "First — learned patterns. If you always run 'make' after 'cd project', it learns that sequence."

> "Second — built-in EDA workflow rules. We ship 9 workflow definitions covering:"
- Git flow (status → add → commit → push)
- Vivado (build → check log → open GUI)
- Perforce (sync → opened → resolve → submit)
- Simulation (compile → run → check waveform)
- Module management (load → list → swap)

**Demo actions:**
- Type `1` to run the first suggested next-step

> "You can just type the number to execute a suggestion. No retyping."

---

### Slide 8: The Standalone Window (Live Demo, 1 min)

**Demo actions:**
```
exit          # leave suggest-start
suggest       # open standalone window
```

> "There's also a standalone mode. This works in ANY shell — tcsh, bash, zsh, doesn't matter. Same ghost-text, same Tab completion, same next-steps. Useful for engineers who prefer to stay in tcsh but want suggestions on demand."

**Demo a few completions, then exit.**

---

### Slide 9: Import & Statistics (Live Demo, 1 min)

**Demo actions:**
```
suggest-stats
```

> "suggest-stats shows your usage profile:"
- Total commands tracked
- Unique commands
- Success rate
- Top commands ranked by frequency
- Top workflows (learned sequences)

> "This is all local to your machine. Nobody else sees your data."

---

### PART 4: UNDER THE HOOD (4 min)

---

### Slide 10: Architecture (1.5 min)

**Show the flowchart (from earlier in this doc) on screen.**

**Talking points:**
> "The architecture is simple and lightweight:"

- **Shell hook** — a `precmd` alias (tcsh) or `PROMPT_COMMAND` function (bash) that fires after every command
- **Daemon** — a background process that receives command telemetry over a Unix socket and writes to the database
- **SQLite database** — local file in ~/.local/share/autosuggest/history.db
- **Prediction engine** — queries the DB with frecency scoring + context boosting + workflow matching
- **Fallback path** — if the daemon is down, the hook writes directly to the DB (zero data loss)

> "Total overhead per command: under 50ms. You'll never notice it."

---

### Slide 11: Security & Safety (1.5 min)

**Talking points:**
> "I want to address the first question anyone asks: is this safe?"

**Redaction:**
> "Every command passes through a redaction engine before storage. It automatically detects and masks:"
- Passwords (`--password=`, `-p`, `-P`)
- API keys and tokens (AWS keys, GitHub tokens, Bearer tokens)
- Secrets in environment exports
- Git URLs with embedded credentials
- sshpass and ssh-keygen passphrases

> "If a command is on the denylist (like `p4 -P` which contains a ticket), it's dropped entirely — never stored."

**Demo:**
```
suggest-stats    # show that no passwords appear in top commands
```

**NFS Safety:**
> "We detect NFS/CIFS/GPFS filesystems and automatically switch SQLite from WAL mode to TRUNCATE journaling. This prevents the corruption that SQLite is documented to suffer on network filesystems."

**Non-invasive:**
> "One file to disable: `touch ~/.no_autosuggest`. Delete two marked lines from your rc file to fully remove. Your history data stays — it just stops suggesting."

---

### Slide 12: Stress Test Results (1 min)

**Talking points:**
> "I've run a comprehensive stress test covering every subsystem:"

- ✅ 1,000-row bulk insert — instant
- ✅ 100 aggregate queries in < 2 seconds
- ✅ 5 concurrent writer threads, 250 inserts, zero errors
- ✅ All 9 workflows loaded and validated
- ✅ All redaction patterns working (8 secret types + denylist)
- ✅ History import with deduplication (re-import = 0 new rows)
- ✅ Prediction engine: prefix match, scoring, context boost, limits
- ✅ 134 unit tests passing, 62 integration checks passing

> "This has been running on my workstation for over a month. 1,081 commands tracked. Zero crashes, zero data loss, zero interference with existing workflows."

---

### PART 5: ROLLOUT & FUTURE (3 min)

---

### Slide 13: Deployment Model (1 min)

**Talking points:**
> "Deployment is via GitHub — completely self-service:"

- Engineer clones the repo
- Runs one installer script
- Done. Under 60 seconds.

> "No infrastructure changes. No server to maintain. No shared database. Everything is per-user, in their home directory."

> "Updates are just: `cd ~/autosuggest-cli && git pull && bash install-linux.sh`"

> "It works today on every Exceed TurboX session, every EDA-CAD farm node, and any personal Linux/Mac machine with Python 3.10+."

---

### Slide 14: Roadmap (1 min)

**Talking points:**
> "What's next if we adopt this more broadly:"

**Near-term (next sprint):**
- Project-specific workflow definitions (team can contribute YAML)
- `suggest-selftest` in CI to validate the tool on new OS images

**Medium-term (next quarter):**
- Team-shared suggestion layer — anonymized common sequences across the group
- Integration with project-specific aliases and scripts
- Metrics dashboard showing team productivity patterns

**Long-term:**
- AI-powered suggestions (LLM-ranked completions from natural language intent)
- Cross-tool integration (IDE terminal, Vivado Tcl console)

---

### Slide 15: The Ask (1 min)

**Talking points:**
> "I'm looking for three things:"

1. **Approval** to share this as an opt-in tool with the broader team
2. **Feedback** on which project-specific workflows would be most valuable to encode
3. **Time allocation** (1-2 sprints) to build the team-shared suggestion layer

> "The tool is production-ready today. 134 tests passing, stress-tested, running live for a month. Risk is zero — it's opt-in, non-invasive, and one command to disable."

> "Questions?"

---

## DEMO SCRIPT — Exact Commands (Cheat Sheet)

Print this and keep it next to your keyboard during the presentation.

```
# === INSTALLATION DEMO ===
git clone https://github.com/Adityasingh2811-ADI/autosuggest-cli.git
cd autosuggest-cli
bash install-linux.sh
rehash

# === HEALTH CHECK ===
suggest-selftest

# === ENTER HOOKED SHELL ===
suggest-start

# === GHOST TEXT DEMO ===
# Type slowly:  git s        → see ghost text → press Right Arrow
# Type slowly:  module l     → see ghost text → press Right Arrow
# Type slowly:  p4 s         → see ghost text → press Right Arrow

# === TAB COMPLETION DEMO ===
# Type:  mak<TAB>   → ranked menu appears
# Type:  git <TAB>  → ranked menu of git subcommands

# === NEXT STEPS DEMO ===
git status              # → "Next steps:" appears
# Type: 1              # → runs first suggestion

# === CONTEXT DEMO ===
cd /proj/ee_sandbox     # suggestions change to project commands
cd ~                    # suggestions change back to general commands

# === STANDALONE WINDOW ===
exit                    # leave suggest-start
suggest                 # open standalone (works in tcsh too)
exit                    # leave standalone

# === STATISTICS ===
suggest-stats

# === SAFETY DEMO ===
touch ~/.no_autosuggest    # disabled
suggest-start              # won't auto-launch
rm ~/.no_autosuggest       # re-enabled
```

---

## BACKUP: Anticipated Questions & Answers

**Q: Does this send data anywhere?**
> No. Everything is local — SQLite file in your home directory. No network calls, no telemetry to any server. The "daemon" is a local background process, not a remote service.

**Q: Will this slow down my shell?**
> Overhead is < 50ms per command. The daemon runs in the background. If you're on NFS and notice latency, `setenv AUTOSUGGEST_NO_NEXTSTEPS 1` disables the per-prompt Python call entirely — recording continues silently.

**Q: What if I have sensitive commands?**
> Passwords, tokens, API keys, and credentials are automatically redacted before storage. Commands containing high-risk patterns (like `p4 -P`) are dropped entirely. Run `suggest-stats` — you'll never see a secret in the output.

**Q: Can I use this with tcsh directly (no bash)?**
> Yes. `suggest-hook tcsh` gives you telemetry recording + next-steps in native tcsh. You won't get inline ghost-text (tcsh limitation), but you get the `suggest` standalone window which has full features.

**Q: What happens if I break something?**
> `touch ~/.no_autosuggest` instantly disables auto-launch. `exec tcsh -f` gives you a clean shell. Re-running the installer repairs everything. Uninstall = delete 2 lines from ~/.cshrc.user.

**Q: How is this different from zsh-autosuggestions?**
> Three ways: (1) it works on our tcsh-based managed hosts, (2) it has context-aware ranking not just history matching, (3) it has built-in EDA workflow intelligence that knows Vivado/Perforce/simulation sequences.

**Q: Can the team customize workflows?**
> Yes. Workflows are defined in a YAML file. Any engineer can add patterns like "after command X, suggest Y and Z". These can be shared via the repo.

**Q: What's the maintenance burden?**
> Zero server infrastructure. It's a pip package installed per-user. Updates are `git pull && bash install-linux.sh`. The database is self-managing with auto-pruning.
