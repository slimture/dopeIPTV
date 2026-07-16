# AI Agent Guide

This document helps AI coding assistants (Aider, Cursor, Copilot, etc.)
work effectively with the dopeIPTV codebase.  Each module is kept small
enough to fit in a 14-16B model's context window.

## Module responsibilities

Modules are grouped into layered subpackages (`providers/`, `core/`,
`media/`, `services/`, `ui/`).  See `docs/ARCHITECTURE.md` for the full
map and the one-way import rule.

| Module | What it owns |
|--------|--------------|
| `app.py` | Entry point, QApplication, login loop |
| `i18n.py` | Translation tables, `set_language`, `tr()` |
| `providers/client.py` | Xtream Codes API client |
| `providers/epg.py` | EPG/XMLTV parser, cache and compact index |
| `providers/metadata.py` | TMDB client, PosterResolver |
| `providers/trakt.py` | Trakt.tv sync |
| `providers/chromecast.py` | Chromecast support |
| `core/stores.py` | Data persistence layer |
| `core/workers.py` | Thread pool helpers, LogoLoader, image caches |
| `core/recording.py` | Recording manager |
| `core/updates.py` | GitHub latest-release check |
| `core/wakelock.py` | Screensaver inhibitor |
| `core/log.py` | Central logging (`configure_logging`, `DOPEIPTV_LOG`) |
| `core/platform_macos.py` | macOS-specific helpers |
| `core/platform_windows.py` | Windows-specific helpers |
| `media/embedded.py` | Embedded mpv player widget |
| `media/players.py` | External player launch, libmpv detection |
| `ui/main_window.py` | MainWindow composition root (+ `mw_*` mixins) |
| `ui/channel_list.py` | List model, view, delegate painting |
| `ui/dialogs.py` | Dialog windows |
| `ui/tmdb_match.py` | Manual TMDB match dialog |
| `ui/widgets.py` | Small standalone widgets |
| `ui/theme.py` | Theming engine |

## Working with individual modules

`ui/main_window.py` is a thin composition root: the real behaviour lives
in the `mw_*` mixins beside it, each mixed into `MainWindow`:
`mw_settings`, `mw_trakt`, `mw_recording`, `mw_context`, `mw_detail`,
`mw_search` (sidebar search), `mw_sidebar` (panel collapse / icon rail),
`mw_nav` (focus mode, nav colours/icons, Library group), `mw_shortcuts`
(keyboard shortcuts), `mw_busy` (loading indicator), `mw_updates`
(update badge) and `mw_reminders`. What stays in `main_window.py` is the
`__init__`/`_build_ui` composition and the playback path (start/stop,
seek, PiP, fullscreen, reconnect) - keep playback code there.
When making changes:

- **UI changes**: find the method by name across `ui/main_window.py` and
  the `mw_*` mixins (e.g. `open_settings` lives in `mw_settings.py`).
  A mixin method resolves on `MainWindow` via MRO, so a pure move between
  them never changes behaviour.
- **Playback**: `media/embedded.py` for the in-app player,
  `media/players.py` for external players.
- **Theme/style**: `ui/theme.py` contains `THEMES`, `ACCENTS`, palette
  dict `P`, and `build_style()`.
- **Data/API**: `providers/client.py` for HTTP calls, `core/stores.py`
  for persistence, `providers/epg.py` for programme data.
- **Recording**: `core/recording.py` is self-contained; the
  `mw_recording` mixin calls its public API.

## Conventions

- Python 3.11+, `from __future__ import annotations` in every file
- PyQt6 (not PyQt5) - all Qt imports use the PyQt6 namespace
- No comments unless the "why" is non-obvious
- Type hints on public method signatures
- UI strings, comments, docstrings, and commits in English
- `P` dict from `ui/theme.py` for all colors (never hardcode hex)
- `ACCENT` string from `ui/theme.py` for the user's chosen accent color

## Common tasks

**Add a new dialog**: create a class in `ui/dialogs.py`, import it where
it is used, wire it to a menu action.

**Add a new setting**: add the QSettings key read/write in the relevant
method in `ui/mw_settings.py` (usually `open_settings`).

**Add a new data store**: add a class to `core/stores.py` following the
pattern of `FavoriteStore` or `HistoryStore`.

**Change the channel list appearance**: modify `ChannelDelegate` paint
methods in `ui/channel_list.py`.

## Quality gate

CI (`.github/workflows/ci.yml`) runs on every push/PR - reproduce it
locally before committing:

```bash
ruff check dopeiptv tests     # lint (pyflakes + bugbear + comprehensions)
mypy                          # types - scoped to the logic modules today
QT_QPA_PLATFORM=offscreen pytest -q
```

- Prefer `print()` never; log through the shared logger:
  `from ..core.log import log` then `log.info/warning/error/debug(...)`.
  `DOPEIPTV_LOG=debug` shows debug traces; `DOPEIPTV_LOG_FILE=/path` tees
  to a rotating file.
- `mypy` currently type-checks `providers/client.py`, `providers/epg.py`
  and `core/stores.py` (see `[tool.mypy] files` in `pyproject.toml`);
  grow that list as modules gain type hints.
- Bump the version in **both** `dopeiptv/__init__.py` and `pyproject.toml`
  on a release - `tests/test_version.py` fails the build if they drift.
