# Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          autosuggest-cli                                     │
└─────────────────────────────────────────────────────────────────────────────┘

 ┌──────────────────┐         ┌──────────────────┐        ┌─────────────────┐
 │   Your Shell     │         │  suggest REPL    │        │  suggest-stats  │
 │  (bash / pwsh)   │         │  (prompt-toolkit)│        │  (metrics CLI)  │
 └────────┬─────────┘         └───────┬──────────┘        └────────┬────────┘
          │                           │                             │
          │ shell hook                │ inline telemetry            │ read-only
          │ (precmd/preexec)          │                             │ queries
          ▼                           ▼                             │
 ┌─────────────────────────────────────────────┐                   │
 │            Telemetry Daemon                  │                   │
 │  (async socket server — Unix/TCP)           │                   │
 │                                             │                   │
 │  • Receives JSON: {command, cwd, exit_code} │                   │
 │  • Non-blocking fire-and-forget writes      │                   │
 │  • Auto-started by REPL if not running      │                   │
 └────────────────────┬────────────────────────┘                   │
                      │                                            │
                      │ INSERT                                     │
                      ▼                                            ▼
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                         SQLite Database                                  │
 │                     (~/.cli_autosuggest.db)                              │
 │                                                                         │
 │  command_history:  id | command | cwd | exit_status | timestamp          │
 │                                                                         │
 │  Indexes: idx_frecency (cwd, timestamp DESC, command)                   │
 │           idx_global_recency (timestamp DESC)                            │
 │           idx_frequency (command, cwd)                                   │
 │                                                                         │
 │  WAL mode — concurrent reads + single writer without blocking           │
 └────────────────────────────────┬────────────────────────────────────────┘
                                  │
                                  │ SELECT (read-only)
                                  ▼
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                       Prediction Engine                                  │
 │                                                                         │
 │  ┌─────────────────────┐    ┌──────────────────────┐                    │
 │  │  Frecency Scorer    │    │  Next-Step Resolver   │                    │
 │  │                     │    │                       │                    │
 │  │  score = Σ recency  │    │  Learned patterns     │                    │
 │  │    × context_boost  │    │    (sequential pairs  │                    │
 │  │                     │    │     from history)     │                    │
 │  │  recency = 2^(-age  │    │         +             │                    │
 │  │    / half_life)     │    │  Workflow rules       │                    │
 │  │                     │    │    (git-flow, docker, │                    │
 │  │  context_boost = 3x │    │     python-dev, etc.) │                    │
 │  │  if same directory  │    │                       │                    │
 │  └─────────────────────┘    └──────────────────────┘                    │
 └────────────────────┬───────────────────┬────────────────────────────────┘
                      │                   │
                      ▼                   ▼
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                         TUI Layer (prompt-toolkit)                       │
 │                                                                         │
 │  ┌───────────────┐  ┌──────────────────┐  ┌─────────────────────────┐  │
 │  │  Ghost Text   │  │  Tab Completion  │  │   Next-Step Display     │  │
 │  │  (auto-       │  │  Menu (ranked    │  │   (numbered follow-up   │  │
 │  │   suggest)    │  │   suggestions)   │  │    commands)            │  │
 │  │               │  │                  │  │                         │  │
 │  │  Accept: →    │  │  Cycle: Tab/     │  │  Accept: type 1/2/3    │  │
 │  │               │  │   Shift+Tab      │  │                         │  │
 │  └───────────────┘  └──────────────────┘  └─────────────────────────┘  │
 │                                                                         │
 │  ┌─────────────────────────────────────────────────────────────────┐   │
 │  │  Argument-Aware Completers                                       │   │
 │  │  git branches/remotes • docker images/containers • make targets  │   │
 │  │  pip packages  (live subprocess queries, 5s TTL cache)           │   │
 │  └─────────────────────────────────────────────────────────────────┘   │
 └─────────────────────────────────────────────────────────────────────────┘


 ┌─────────────────────────────────────────────────────────────────────────┐
 │                          Data Flow Summary                               │
 │                                                                         │
 │  1. User types → Engine queries DB → Ghost text / completions shown     │
 │  2. User executes → Daemon records to DB → Engine learns for next time  │
 │  3. Command completes → Next-step resolver suggests follow-ups          │
 │  4. Feedback loop: more usage → better predictions (frecency decay)     │
 └─────────────────────────────────────────────────────────────────────────┘
```

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| SQLite with WAL mode | Concurrent reads from engine while daemon writes — no locking |
| Socket-based daemon | Telemetry is fire-and-forget — never blocks the user's shell |
| Exponential decay (1h half-life) | Recent commands are strongly preferred, but old habits still surface |
| 3x directory context boost | "What I do HERE" matters more than global history |
| Predefined + learned workflows | Works immediately (predefined) and improves over time (learned) |
| Argument completers via subprocess | Live data (current branches, running containers) — not stale |
