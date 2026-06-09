# Context-Aware CLI Autosuggestion Engine

## System Vision
A zero-latency, Python-based CLI tool using `prompt_toolkit`. It tracks user telemetry (commands, working directory) via a decoupled SQLite background daemon, and surfaces inline ghost-text predictions based on a "frecency" (frequency + recency + context) algorithm.

## Architecture State
- [x] Phase 1: Storage Layer & Unix Socket Daemon (`daemon.py`, `schema.sql`)
- [x] Phase 2: Core Prediction Engine (`engine.py`)
- [ ] Phase 3: Foreground TUI Integration (`prompt_toolkit` bindings)

## Active Routine: PHASE 3
Goal: Implement the `@TUI-Lead` interface. Build a `prompt_toolkit` REPL wrapper that queries the Prediction Engine, renders inline ghost text, handles `Shift+Tab` cycling, and sends telemetry to the daemon socket upon command execution.
