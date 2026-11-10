# Instructions for GitHub Copilot

This repository's instructions for AI coding assistants live in
[`AGENTS.md`](../AGENTS.md) at the root - one file for every tool. Read it
before changing anything; the maintainer's runbook is
[`docs/MAINTAINING.md`](../docs/MAINTAINING.md).

The short version: work on the `testing` branch; never break video
playback; Linux first; every user-visible string through `tr()`; colours
from the `P` palette in `ui/theme.py`; run `ruff check dopeiptv tests`,
`mypy` and `QT_QPA_PLATFORM=offscreen pytest -q` before you finish.
