# AI Agent Guide

This document helps AI coding assistants (Aider, Cursor, Copilot, etc.)
work effectively with the dopeIPTV codebase.  Each module is kept small
enough to fit in a 14-16B model's context window.

## Module responsibilities

Modules are grouped into layered subpackages (`providers/`, `core/`,
`media/`, `ui/`).  See `ARCHITECTURE.md` for the full map and the
one-way import rule.

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
| `core/wakelock.py` | Screensaver inhibitor |
| `core/platform_macos.py` | macOS-specific helpers |
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
in the `mw_*` mixins beside it (`mw_settings`, `mw_trakt`,
`mw_recording`, `mw_context`, `mw_detail`), each mixed into `MainWindow`.
When making changes:

- **UI changes**: find the method by name across `ui/main_window.py` and
  the `mw_*` mixins (e.g. `open_settings` lives in `mw_settings.py`).
- **Playback**: `media/embedded.py` for the in-app player,
  `media/players.py` for external players.
- **Theme/style**: `ui/theme.py` contains `THEMES`, `ACCENTS`, palette
  dict `P`, and `build_style()`.
- **Data/API**: `providers/client.py` for HTTP calls, `core/stores.py`
  for persistence, `providers/epg.py` for programme data.
- **Recording**: `core/recording.py` is self-contained; the
  `mw_recording` mixin calls its public API.

## Conventions

- Python 3.10+, `from __future__ import annotations` in every file
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
