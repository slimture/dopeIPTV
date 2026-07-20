# Contributing to dopeIPTV

Thanks for wanting to help! dopeIPTV is a desktop IPTV player written in
Python (PyQt6 + libmpv). **Linux is the primary target** — macOS and Windows
are supported best-effort, and platform-specific changes must never affect
the Linux experience.

## Getting started

```bash
git clone https://github.com/slimture/dopeIPTV
cd dopeIPTV
python3 -m pip install -r requirements.txt
# libmpv must be available (e.g. apt install libmpv2 / brew install mpv)
python3 dopeiptv.py
```

No provider account handy? The app has a built-in demo mode on the login
screen, enough to exercise most of the UI.

Read `docs/ARCHITECTURE.md` for the module map and the one-way import rule
(`providers/ → core/ → media/ → services/ → ui/`), and
`docs/DEVELOPER_NOTES.md` for subsystem specifics.

## Before you open a pull request

Run the same quality gate as CI — all four must pass:

```bash
ruff check dopeiptv tests              # lint (pyflakes, bugbear, comprehensions)
mypy                                   # types (scoped via pyproject.toml)
QT_QPA_PLATFORM=offscreen pytest -q    # full suite, headless
python3 dopeiptv.py --self-check       # libmpv loads, GL player constructs
```

## Ground rules

- **Never regress video playback.** The embedded mpv/OpenGL pipeline is the
  heart of the app; changes that touch it need a clear explanation of why
  they're safe. When in doubt, don't touch the render path.
- **Every user-visible string goes through `tr()`.** Add the English source
  to `_STRINGS` in `dopeiptv/i18n.py` (English is the only language inline);
  each of the other 26 languages lives in its own `dopeiptv/locale/<code>.json`
  file. A new key falls back to English until a locale file covers it, so you
  can add the string and translate the locales in follow-up. Tests enforce
  placeholder consistency across languages and full coverage for the core
  locales; machine translation for the non-English entries is acceptable.
  Right-to-left languages (`ar`, `fa`, `he`, `ur`) are listed in
  `RTL_LANGUAGES` so the UI mirrors its layout. `python tools/i18n_status.py`
  reports every language's health; see
  [`docs/TRANSLATING.md`](docs/TRANSLATING.md) for the full translator
  workflow (native-speaker corrections are especially welcome).
- **Icons are drawn in code** (QPainter vector art), never emoji glyphs —
  emoji render differently (or invisibly) per platform.
- **Platform-specific code** lives behind `sys.platform` checks or in
  `core/platform_macos.py` / `core/platform_windows.py`, and must be a
  no-op everywhere else.
- **Persisted data is untrusted.** Anything read back from QSettings/JSON
  must survive corrupt or hand-edited input (see `tests/test_robustness.py`
  for the pattern).

## Pull requests

- Target the `main` branch. Keep PRs small and focused — one fix or feature
  per PR beats a grab-bag.
- Describe **how you tested it** (real provider, demo mode, OS).
- UI changes: include a screenshot, and check both a dark and the
  OLED pure-black theme.
- New behavior should come with a test where the logic is testable headless.

## Reporting bugs

Use the issue templates. Logs help enormously:

```bash
DOPEIPTV_LOG=debug DOPEIPTV_LOG_FILE=/tmp/dope.log python3 dopeiptv.py
```

Credentials are redacted from logs automatically, but skim before posting.
