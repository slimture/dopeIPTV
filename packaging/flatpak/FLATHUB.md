# Submitting dopeIPTV to Flathub

`io.github.slimture.dopeIPTV.yaml` in this folder is a **Flathub-ready,
fully offline** manifest: the Python deps are vendored as pinned wheels
(declared `sources`), so the build needs no network — only the runtime does
(`--share=network` in `finish-args`, for streaming).

## Before you submit — checklist

- [ ] **Screenshots are live.** The AppStream metainfo points at
  `https://raw.githubusercontent.com/slimture/dopeIPTV/main/docs/screenshots/*.png`.
  Flathub validates these are reachable, so push the four PNGs to `main`
  first (see `docs/screenshots/README.md`).
- [ ] The app is **tagged and released** (e.g. `v0.6.2`) — the Flathub
  manifest builds from that tag.
- [ ] `flatpak run org.freedesktop.appstream-glib validate
  io.github.slimture.dopeIPTV.metainfo.xml` passes (or `appstreamcli
  validate`).

## One change for the Flathub PR: git source instead of `dir`

The committed manifest builds the app from `type: dir` (`path: ../..`) so
this repo's CI can build the `.flatpak` from a checkout. Flathub builds from
a **pinned git commit** instead. In the copy you submit, replace the last
source of the `dopeiptv` module:

```yaml
      - type: dir
        path: ../..
```

with the release tag + its commit:

```yaml
      - type: git
        url: https://github.com/slimture/dopeIPTV.git
        tag: v0.6.2
        commit: <full 40-char sha of that tag>
```

Everything else (the 17 vendored wheels, libmpv/libplacebo/libass) stays the
same.

## Submission steps

1. Fork `https://github.com/flathub/flathub` and create a branch named
   `io.github.slimture.dopeIPTV`.
2. Add the (git-source) manifest as
   `io.github.slimture.dopeIPTV.yaml` at the repo root of that branch.
3. Open a PR against `flathub/flathub` **targeting the `new-pr` branch**.
   The Flathub bot builds it; fix anything it flags.
4. Common review points to expect:
   - `--filesystem=home` — used for recordings + playlists written directly
     to disk. Be ready to explain, or narrow to `xdg-videos`/portal if asked.
   - Wheel updates must stay pinned (regenerate with the same pip-download
     recipe when bumping deps).

## Regenerating the vendored wheels (when bumping deps)

Download the whole tree for the runtime's Python (24.08 → 3.12) and emit the
`type: file` sources with their PyPI URL + sha256:

```bash
pip download --only-binary=:all: --python-version 3.12 --implementation cp \
  --abi cp312 --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 \
  --platform any --dest wheels \
  PyQt6 requests python-mpv pychromecast "setuptools>=77" wheel
# then, for each wheel, look up its files.pythonhosted.org URL + sha256 on
# the PyPI JSON API and paste them as `- type: file` entries.
```
