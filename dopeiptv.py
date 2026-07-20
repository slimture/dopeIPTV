#!/usr/bin/env python3
"""dopeIPTV - an elegant IPTV client for Xtream Codes with EPG.

Run with:  python3 dopeiptv.py
"""

import sys

# Optionally re-exec with a custom memory allocator (jemalloc/mimalloc)
# preloaded, before anything heavy is imported. Strict no-op unless a library
# is actually found for this platform, so it can never affect a normal start.
from dopeiptv.core._alloc import maybe_preload_allocator

maybe_preload_allocator()

from dopeiptv.app import main  # noqa: E402  (after the optional re-exec)

if __name__ == "__main__":
    sys.exit(main())
