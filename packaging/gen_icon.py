"""Render the app icon to PNG files for packaging (desktop icon themes).

Run headless:  QT_QPA_PLATFORM=offscreen python packaging/gen_icon.py OUTDIR
Produces <OUTDIR>/<size>/io.github.slimture.dopeIPTV.png for each size.
"""

import os
import sys

# Allow running as `python packaging/gen_icon.py` from the repo root: the
# script's own directory (packaging/) is what lands on sys.path, not the repo
# root, so add the repo root explicitly before importing the package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication

from dopeiptv.app import ICON_SAFE_INSET, make_app_icon

APP_ID = "io.github.slimture.dopeIPTV"
SIZES = (256, 128, 64, 48, 32)


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "build/icons"
    app = QApplication([])  # noqa: F841 - needed for QPixmap
    # The same margin the app draws at runtime. GNOME asks for the icon to
    # occupy about 80% of its canvas, and a full-bleed one stands out a
    # quarter larger than everything beside it in the dash - so the .deb's
    # theme icons and the AppImage's must match what install_icon writes,
    # or the two disagree on the same machine.
    icon = make_app_icon(inset=ICON_SAFE_INSET)
    for s in SIZES:
        d = os.path.join(out, f"{s}x{s}")
        os.makedirs(d, exist_ok=True)
        icon.pixmap(s, s).save(os.path.join(d, f"{APP_ID}.png"), "PNG")
    # A plain dopeiptv.png (AppImage / .deb use Icon=dopeiptv or the app id).
    icon.pixmap(256, 256).save(os.path.join(out, "dopeiptv.png"), "PNG")
    print(f"icons written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
