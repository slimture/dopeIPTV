# Third-Party Licenses and Licensing Rationale

dopeIPTV is licensed under the **GNU General Public License, version 3 or
later (GPL-3.0-or-later)**. See the [`LICENSE`](LICENSE) file for the full
text.

## Why GPLv3?

dopeIPTV builds on, links against, or drives several components whose
licenses impose copyleft obligations. GPLv3 is the licence that is
compatible with all of them at once:

| Component | Role in dopeIPTV | License | Compatibility note |
|-----------|------------------|---------|--------------------|
| **PyQt6** (Riverbank Computing) | GUI toolkit the whole app is written against | GPL-3.0 (or commercial) | Using PyQt6 under its free licence requires the combined work to be GPL-3.0-compatible. This is the strongest single constraint and is why the project is GPLv3 rather than GPLv2. |
| **libmpv / mpv** | Embedded video playback (OpenGL render API) | GPL-2.0-or-later (parts LGPL-2.1+) | GPL-2.0-**or-later** can be combined into a GPLv3 work. |
| **FFmpeg** (via mpv) | Demuxing/decoding backend | LGPL-2.1-or-later, or GPL if built with `--enable-gpl` | Compatible; if a GPL build is used the GPL terms apply, which the project already satisfies. |
| **VLC / libVLC** (optional external player) | Alternative playback backend, launched as a separate process | GPL-2.0-or-later / LGPL-2.1+ | dopeIPTV only *invokes* VLC as a separate program; even so, GPLv3 keeps the project clear of any combined-work concerns. |
| **python-mpv** | Python bindings to libmpv | GPL-2.0-or-later / LGPL-2.1+ (per file) | Compatible with GPLv3. |
| **PyChromecast** | Chromecast / Google Cast support | MIT | Permissive — combinable with GPLv3. |
| **zeroconf** | mDNS discovery (Cast) | LGPL-2.1-or-later | Compatible with GPLv3. |
| **requests** | HTTP client for provider/TMDB APIs | Apache-2.0 | Apache-2.0 is one-way compatible into GPLv3. |
| **PyInstaller** (build only) | Packaging | GPL-2.0-or-later with a bootloader exception | Build tool; does not affect the licence of the produced binary beyond the bundled libraries' own terms. |

Because PyQt6's free edition is GPLv3, and every other copyleft dependency
is "GPLv2 **or later**" or LGPL, the smallest licence that legally covers a
distributable combined work is **GPLv3**. Distributing dopeIPTV (source or
built binaries) therefore means honouring the GPLv3: you must pass on the
source, keep the licence and copyright notices, and license any derivative
under GPLv3-compatible terms.

## Runtime services and data

- **TMDB** — Metadata and artwork are fetched from The Movie Database. This
  product uses the TMDB API but is **not endorsed or certified by TMDB**.
  You must supply your own API key, and use is subject to the
  [TMDB Terms of Use](https://www.themoviedb.org/terms-of-use).
- **IPTV playlists / EPG** — dopeIPTV does not include, host, or distribute
  any media, playlists, or channel data. Users supply their own provider
  URLs and are responsible for the legality of the content they access.

## Bundling note for binary distributions

When shipping a bundled binary (e.g. via PyInstaller), the corresponding
source code of dopeIPTV **and** of every GPL/LGPL library included in the
bundle must be made available to recipients, as required by the GPL. Prefer
linking to the upstream source releases of mpv, FFmpeg, and the Python
dependencies, and include this file plus `LICENSE` in the distribution.
