#!/usr/bin/env python3
"""Vendor the static legend/borders images from CHMI into static/assets/ and
create a transparent placeholder PNG for the initial radar overlay.

Run once after install:  python scripts/fetch_assets.py
"""
from __future__ import annotations

import io
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(HERE, "static", "assets")
os.makedirs(ASSETS, exist_ok=True)

PRODUCT = "https://produkty.chmi.cz/radar"

TARGETS = {
    # dBZ -> mm/h legend (used by the UI + by build_colormap.py)
    "scl-dbz-mmh.png": f"{PRODUCT}/scl/scl-dbz-mmh.png",
    # Czech state borders overlay (same Web-Mercator bounds as the composite)
    "borders.png": f"{PRODUCT}/und/pacz2gmaps6.und.015.hranice2px_4b.png",
    # orography background (optional; matches the CHMI viewer look)
    "oro.jpg": f"{PRODUCT}/und/pacz2gmaps9.oro_col2sharp40.jpg",
}


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read()


def main() -> int:
    from PIL import Image  # local import: only needed when running the script

    for name, url in TARGETS.items():
        dest = os.path.join(ASSETS, name)
        try:
            data = fetch(url)
            with open(dest, "wb") as f:
                f.write(data)
            print(f"  vendored {name} ({len(data)} bytes)")
        except Exception as e:  # noqa: BLE001
            print(f"  WARN could not fetch {name}: {e}", file=sys.stderr)

    # transparent 680x460 placeholder so the initial overlay doesn't 404
    blank = Image.new("RGBA", (680, 460), (0, 0, 0, 0))
    blank.save(os.path.join(ASSETS, "blank.png"))
    print("  wrote blank.png (transparent placeholder)")
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
