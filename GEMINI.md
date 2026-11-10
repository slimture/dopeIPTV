# Instructions for AI assistants

This project's instructions for AI coding assistants live in
[`AGENTS.md`](AGENTS.md) - one file for every tool, so they never drift
apart. Read it first; the maintainer's runbook is
[`docs/MAINTAINING.md`](docs/MAINTAINING.md).

Gemini CLI reads this file by default; it can also be pointed straight at
AGENTS.md with `"context": {"fileName": "AGENTS.md"}` in `.gemini/settings.json`.
