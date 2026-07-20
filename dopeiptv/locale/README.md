# Add-on UI locales

Each `<code>.json` here translates dopeIPTV's interface into one language.
**English is the only language that lives in code** (`../i18n.py`, as the source
of truth); every other language — Swedish, Spanish, German, French, Chinese,
Russian, Thai and all the rest — is a file here. A language joins the in-app
picker once its file covers ~90 % of the keys (English fallback covers the
rest); the seven listed above ship 100 % complete.

## Format

A flat JSON object mapping string keys to translations:

```json
{
  "nav_tv": "TV",
  "btn_play": "Oynat",
  "status_playing": "Oynatılıyor: {title}"
}
```

- Keys must match those in `i18n.py` (run the template tool to get them all).
- Keep every `{placeholder}` exactly as in the English source — a test fails
  the build if a placeholder is dropped or invented.
- Any key you leave out falls back to English automatically, so a partial
  file is safe. A locale joins the in-app language picker once it covers
  ~90 % of the keys.

## Workflow

```bash
# full key set + English reference, ready to translate in place
python tools/i18n_locale_template.py tr
mv dopeiptv/locale/tr.template.json dopeiptv/locale/tr.json
# …translate the values…

# later, list only what's still missing after new strings were added
python tools/i18n_locale_template.py tr --missing
```

Native-speaker corrections are very welcome — one file, no code to touch.
