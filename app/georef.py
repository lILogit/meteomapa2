"""Web Mercator projection + lat/lon→pixel mapping for the radar composite.

The composite PNG is a linear image of EPSG:3857 (Web Mercator) projected
space, so we project both the bounds corners and the target point, then
interpolate. Interpolating raw lat/lon (equirectangular) would be wrong.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

R = 6378137.0  # Web Mercator sphere radius (meters)


def mercator(lat: float, lon: float) -> tuple[float, float]:
    x = R * math.radians(lon)
    y = R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    return x, y


@dataclass(frozen=True)
class ImageMapping:
    img_w: int
    img_h: int
    x_w: float  # west  edge, meters
    x_e: float  # east  edge, meters
    y_n: float  # north edge, meters (y grows northward)
    y_s: float  # south edge, meters

    def latlon_to_pixel(self, lat: float, lon: float) -> tuple[int, int]:
        xt, yt = mercator(lat, lon)
        px = (xt - self.x_w) / (self.x_e - self.x_w) * self.img_w
        # image row 0 = north → invert y
        py = (self.y_n - yt) / (self.y_n - self.y_s) * self.img_h
        return int(round(px)), int(round(py))

    def meters_per_pixel_x(self) -> float:
        return (self.x_e - self.x_w) / self.img_w


def build_mapping(north: float, south: float, west: float, east: float,
                  img_w: int, img_h: int) -> ImageMapping:
    x_w, y_n = mercator(north, west)
    x_e, y_s = mercator(south, east)
    return ImageMapping(img_w=img_w, img_h=img_h, x_w=x_w, x_e=x_e, y_n=y_n, y_s=y_s)


def disk_mask(img_w: int, img_h: int, cx: int, cy: int, px_radius: float) -> np.ndarray:
    """Boolean disk of ``px_radius`` around (cx, cy), clipped to the image."""
    yy, xx = np.ogrid[:img_h, :img_w]
    return (xx - cx) ** 2 + (yy - cy) ** 2 <= px_radius ** 2


def km_to_pixel_radius(radius_km: float, center_lat: float, m_per_px_x: float) -> float:
    """Ground circle → pixel radius. Mercator is conformal: local scale = sec(lat)."""
    ground_m_per_px = m_per_px_x * math.cos(math.radians(center_lat))
    return (radius_km * 1000.0) / ground_m_per_px
