# Translating dopeIPTV

dopeIPTV speaks **27 languages**. English is the source language and lives
inline in [`dopeiptv/i18n.py`](../dopeiptv/i18n.py); every other language is a
single file under [`dopeiptv/locale/`](../dopeiptv/locale) named by its code —
`de.json`, `sv.json`, `ja.json`, and so on. Each file is a flat
`{ "key": "translation" }` map.

Most non-English strings started life as machine translation. Native-speaker
corrections are the single most valuable contribution here — you don't need to
know Python or Qt to make them.

## The fastest way: open an issue

Not comfortable editing files? Open a **[Translation fix][tf]** issue: give
the language, the key (if you know it), the current wording and your
suggestion. That's it — we'll apply it.

[tf]: https://github.com/slimture/dopeIPTV/issues/new?template=translation_fix.yml

## Correcting an existing language

1. Open `dopeiptv/locale/<code>.json` (e.g. `de.json`).
2. Find the key you want to fix and edit **only the value** (the right-hand
   side). Leave the key untouched.
3. Keep every `{placeholder}` exactly as in the English source — `{days}`,
   `{title}`, `{n}`, `{version}`, … are filled in at runtime, and a renamed or
   dropped placeholder will make that string fall back to English. HTML bits
   like `<b>…</b>` and line breaks (`\n`) must be preserved too.
4. Save as UTF-8.

Check your work:

```sh
# Health of every language: coverage, stray keys, placeholder mismatches
python tools/i18n_status.py

# Details for one language (missing keys + placeholder problems, with the
# English source next to each)
python tools/i18n_status.py de
```

The test suite enforces the same rules, so run it before opening a PR:

```sh
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_i18n.py tests/test_robustness.py -q
```

## Adding a brand-new language

1. Generate a template with every key and its English text:

   ```sh
   python tools/i18n_locale_template.py <code>      # writes locale/<code>.template.json
   ```

2. Translate the values, then save it as `dopeiptv/locale/<code>.json`.
3. Add the language's **native name** to `_NATIVE_NAMES` in `dopeiptv/i18n.py`
   (this also sets its position in the picker). If it is written
   right-to-left, add its code to `RTL_LANGUAGES` in the same file so the
   whole layout mirrors.
4. A language only appears in the in-app picker once its file covers about
   **90 %** of the keys (`_LOCALE_READY_RATIO`), so a half-finished locale
   never ships a half-English UI. Run `python tools/i18n_status.py` to see
   where it stands.

## Conventions

- Match the tone of the English source: concise, friendly, not stiff.
- Keep product names as-is: **dopeIPTV**, **Xtream Codes**, **M3U**, **Trakt**,
  **TMDB**, **Chromecast**, **mpv**, **VLC**, **EPG**.
- Prefer the natural term your users actually say over a literal gloss.
- When unsure about a UI string's context, `python tools/i18n_status.py <code>`
  prints the English original beside each key, and the key name itself is a
  strong hint (`btn_…` a button, `tab_…` a settings tab, `sc_…` a keyboard
  shortcut, `diag_…` a stream error message).

Thank you — a well-translated app in someone's own language is a real
difference.
