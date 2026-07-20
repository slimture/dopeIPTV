# Add-on UI locales

Each `<code>.json` here translates dopeIPTV's interface into one more language.
The eight core languages (English, Swedish, Spanish, German, French, Chinese,
Russian, Thai) live inline in `../i18n.py`; everything else is a file here.

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
