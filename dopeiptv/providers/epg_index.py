"""The guide parser as a process of its own, for a checkout or a pip
install (``python -m dopeiptv.providers.epg_index --epg-index``); the
frozen app gets there through dopeiptv.py. See XmltvGuide._parse_elsewhere."""
from __future__ import annotations

import sys

from .epg import index_main

if __name__ == "__main__":
    sys.exit(index_main())
