"""Render the app icon into a macOS .iconset directory.

Run:  QT_QPA_PLATFORM=offscreen python packaging/gen_iconset.py OUTDIR
Produces <OUTDIR>/dopeiptv.iconset/icon_<size>.png at every size iconutil
expects, ready for  `iconutil -c icns <OUTDIR>/dopeiptv.iconset`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication

from dopeiptv.app import make_app_icon


# (size in points, "@2x" pixel doubling flag) -> Apple's required filenames.
ICONSET = [
    (16,  False), (16,  True),
    (32,  False), (32,  True),
    (128, False), (128, True),
    (256, False), (256, True),
    (512, False), (512, True),
]


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "build"
    iconset = os.path.join(out, "dopeiptv.iconset")
    os.makedirs(iconset, exist_ok=True)
    _app = QApplication([])  # noqa: F841 - needed for QPixmap.
    icon = make_app_icon()
    for size, retina in ICONSET:
        pixels = size * (2 if retina else 1)
        suffix = f"{size}x{size}" + ("@2x" if retina else "")
        path = os.path.join(iconset, f"icon_{suffix}.png")
        icon.pixmap(pixels, pixels).save(path, "PNG")
    print(f"iconset written to {iconset}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
