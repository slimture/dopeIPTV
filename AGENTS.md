# AI Agent Guide

This document helps AI coding assistants (Aider, Cursor, Copilot, etc.)
work effectively with the dopeIPTV codebase.  Each module is kept small
enough to fit in a 14-16B model's context window.

## Module responsibilities

| Module | Lines | What it owns |
|--------|------:|--------------|
| `app.py` | ~136 | Entry point, QApplication, login loop |
| `main_window.py` | ~2700 | MainWindow class (UI + business logic) |
| `channel_list.py` | ~320 | List model, view, delegate painting |
| `dialogs.py` | ~315 | All dialog windows |
| `embedded.py` | ~720 | Embedded mpv player widget |
| `players.py` | ~236 | External player launch, libmpv detection |
| `recording.py` | ~363 | Recording manager |
| `client.py` | ~162 | Xtream Codes API client |
| `epg.py` | ~236 | EPG/XMLTV parser and cache |
| `stores.py` | ~329 | Data persistence layer |
| `workers.py` | ~101 | Thread pool helpers |
| `chromecast.py` | ~175 | Chromecast support |
| `theme.py` | ~280 | Theming engine |
| `wakelock.py` | ~74 | Screensaver inhibitor |

## Working with individual modules

Most modules are independent.  The main dependency is that
`main_window.py` imports from nearly every other module.  When making
changes:

- **UI changes**: start in `main_window.py`.  Search for the method
  name (e.g. `_open_settings`, `_build_context_menu`).
- **Playback**: `embedded.py` for the in-app player, `players.py` for
  external players.
- **Theme/style**: `theme.py` contains `THEMES`, `ACCENTS`, palette
  dict `P`, and `build_style()`.
- **Data/API**: `client.py` for HTTP calls, `stores.py` for persistence,
  `epg.py` for programme data.
- **Recording**: `recording.py` is self-contained; `main_window.py`
  calls its public API.

## Conventions

- Python 3.10+, `from __future__ import annotations` in every file
- PyQt6 (not PyQt5) - all Qt imports use the PyQt6 namespace
- No comments unless the "why" is non-obvious
- Type hints on public method signatures
- UI strings, comments, docstrings, and commits in English
- `P` dict from `theme.py` for all colors (never hardcode hex in widgets)
- `ACCENT` string from `theme.py` for the user's chosen accent color

## Common tasks

**Add a new dialog**: create a class in `dialogs.py`, import it in
`main_window.py`, wire it to a menu action.

**Add a new setting**: add the QSettings key read/write in the relevant
method in `main_window.py` (usually `_open_settings`).

**Add a new data store**: add a class to `stores.py` following the
pattern of `FavoriteStore` or `HistoryStore`.

**Change the channel list appearance**: modify `ChannelDelegate` paint
methods in `channel_list.py`.
