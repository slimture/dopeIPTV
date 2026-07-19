## What & why

<!-- What does this PR change, and what problem does it solve?
     Link the issue if there is one: Fixes #123 -->

## How it was tested

<!-- Real provider / demo mode? Which OS? What did you actually click/run? -->

- [ ] `ruff check dopeiptv tests` passes
- [ ] `mypy` passes
- [ ] `QT_QPA_PLATFORM=offscreen pytest -q` passes (true exit code, not piped away)
- [ ] `python3 dopeiptv.py --self-check` passes

## Checklist

- [ ] Does not touch the video playback path — or explains below why the change is safe
- [ ] New user-visible strings go through `tr()` with entries for all 8 languages
- [ ] Platform-specific code is gated (`sys.platform` / `core/platform_*.py`) and a no-op elsewhere
- [ ] UI change → screenshot attached (checked on a dark theme and OLED pure-black)
- [ ] New logic that runs headless has a test
