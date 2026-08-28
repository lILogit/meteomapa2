#!/usr/bin/env python3
"""Reconstruct the CHMI reflectivity color table from the vendored legend image
and a live frame, and print a Python snippet to paste into app/colormap.py.

The legend bar is vertical: top = highest dBZ (white ≈ 60), bottom = lowest
(dark purple ≈ 4), in steps of 4 dBZ. The dBZ labels were confirmed by reading
the legend visually; Marshall–Palmer reproduces its printed mm/h reference values.

Run:  python scripts/build_colormap.py
"""
from __future__ import annotations

import io
import os
import re
import sys
import urllib.request
from urllib.parse import urljoin

PRODUCT = "https://produkty.chmi.cz/radar"
OPENDATA = "https://opendata.chmi.cz/meteorology/weather/radar/composite/pseudocappi2km/png"


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read()


def legend_swatches(legend_path: str):
    """Return ordered list of (y, rgb) for the legend bar (top→bottom)."""
    from PIL import Image
    import numpy as np
    arr = np.asarray(Image.open(legend_path).convert("RGBA"))
    H, W = arr.shape[:2]
    TEXT = {(28, 28, 28), (64, 64, 64), (255, 255, 255)}
    runs = []
    for y in range(H):
        r, g, b, a = arr[y, W // 8]  # sample inside the bar (left ~1/8)
        if a > 128 and (int(r), int(g), int(b)) not in TEXT:
            c = (int(r), int(g), int(b))
            if not runs or runs[-1][1] != c:
                runs.append((y, c))
    return runs


def main() -> int:
    from PIL import Image

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    legend = os.path.join(here, "static", "assets", "scl-dbz-mmh.png")
    if not os.path.exists(legend):
        data = fetch(f"{PRODUCT}/scl/scl-dbz-mmh.png")
        with open(legend, "wb") as f:
            f.write(data)

    runs = legend_swatches(legend)
    n = len(runs)
    # dBZ from 60 (top) down to 4 (bottom) in 15 steps of 4
    dbz_top = 60
    step = (dbz_top - 4) / (n - 1)

    # collect extra colors present in a live frame but absent from the legend
    try:
        html = fetch(OPENDATA + "/").decode("utf-8", "ignore")
        names = sorted(re.findall(r'href="([^"]+\.png)"', html))
        if names:
            fa = Image.open(io.BytesIO(fetch(urljoin(OPENDATA + "/", names[-1])))).convert("RGBA")
            frame_cols = set()
            fa_data = fa.getdata()
            for r, g, b, a in list(fa_data)[::37]:  # subsample
                if a > 128:
                    frame_cols.add((r, g, b))
    except Exception as e:  # noqa: BLE001
        print(f"  WARN could not read live frame: {e}", file=sys.stderr)
        frame_cols = set()

    legend_cols = {c for _, c in runs}
    extras = sorted(frame_cols - legend_cols)

    print("# ---- derived by scripts/build_colormap.py ----")
    print("_TABLE: list[tuple[int,int,int,int]] = [")
    for i, (y, (r, g, b)) in enumerate(runs):
        dbz = round(dbz_top - step * i)
        # bottom of legend is lowest; list lowest→highest for readability
        pass
    # emit lowest → highest
    for i in reversed(range(n)):
        y, (r, g, b) = runs[i]
        dbz = round(dbz_top - step * i)
        print(f"    ({r:3d}, {g:3d}, {b:3d}, {dbz}),")
    print("    # colors present in live frames but not in the legend bar:")
    for (r, g, b) in extras:
        print(f"    ({r:3d}, {g:3d}, {b:3d}, 4),  # trace/clutter")
    print("]")
    print(f"# {n} legend swatches + {len(extras)} frame-only colors", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
