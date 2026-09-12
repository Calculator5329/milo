# Agent instructions

Read `CLAUDE.md`. It is the setup contract for any coding agent (Codex, Claude Code, Cursor,
or another) and this file only points at it so the two never drift.

Codex-specific note: `python milo.py --port 8767` must run in the background while you probe
it; start it as a background job and stop it when the probe finishes.
