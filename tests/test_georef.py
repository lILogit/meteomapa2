"""Tests for the Mercator georeferencing."""
from app.georef import build_mapping, km_to_pixel_radius

# bounds from CHMI radar-main.js:1633 ; image 680x460
M = build_mapping(52.167, 48.047, 11.267, 20.770, 680, 460)


def test_corners_map_to_image_edges():
    # NW corner -> near (0,0);  SE corner -> near (679,459)
    assert M.latlon_to_pixel(52.167, 11.267) == (0, 0)
    px, py = M.latlon_to_pixel(48.047, 20.770)
    assert abs(px - 679) <= 1 and abs(py - 459) <= 1


def test_center_is_inside_image():
    px, py = M.latlon_to_pixel(48.9086, 14.5948)
    assert 0 < px < 680 and 0 < py < 460


def test_pixel_radius_sane():
    r = km_to_pixel_radius(12.0, 48.9086, M.meters_per_pixel_x())
    # ~10-14 px for a 12 km radius at this latitude
    assert 8 < r < 16
