"""Geodesy helpers: Maidenhead grid locator and unit conversion constants.

The unit constants are here for tests/demo data; the frontend does its own
conversions so that the API stays SI.
"""

from __future__ import annotations

MPS_TO_KPH = 3.6
MPS_TO_MPH = 1.0 / 0.44704
MPS_TO_KNOTS = 3600.0 / 1852.0
METERS_TO_FEET = 3.28084

_FIELD_MAX = 17  # 'R' - 'A'


def _cap(n: int) -> int:
    """Clamp a field index into 'A'..'R'."""
    if n < 0:
        return 0
    if n > _FIELD_MAX:
        return _FIELD_MAX
    return n


def maidenhead(lat: float, lon: float) -> str:
    """Return the 8-character Maidenhead grid locator for *lat*/*lon*.

    Port of gpsd's ``gpsd_maidenhead()`` (gpsd/libgps/gpsdclient.c), which
    produces e.g. ``EN41er01`` for 41.71343333 N, 91.66269 W.
    """
    lon = float(lon)
    lat = float(lat)

    # Longitude: 20 deg fields, 2 deg squares, 5 min subsquares, 30 s extended.
    if lon > 179.99999:
        lon = 179.99999
    elif lon < -180.0:
        lon = -180.0
    lon += 180.0
    lon_field = _cap(int(lon / 20.0))
    lon -= lon_field * 20.0
    lon_square = int(lon / 2.0)
    lon -= lon_square * 2.0
    lon *= 60.0
    lon_sub = int(lon / 5.0)
    lon -= lon_sub * 5.0
    lon *= 60.0
    lon_ext = min(9, int(lon / 30.0))

    # Latitude: 10 deg fields, 1 deg squares, 2.5 min subsquares, 15 s extended.
    if lat > 89.99999:
        lat = 89.99999
    elif lat < -90.0:
        lat = -90.0
    lat += 90.0
    lat_field = _cap(int(lat / 10.0))
    lat -= lat_field * 10.0
    lat_square = int(lat)
    lat -= lat_square
    lat *= 60.0
    lat_sub = int(lat / 2.5)
    lat -= lat_sub * 2.5
    lat *= 60.0
    lat_ext = min(9, int(lat / 15.0))

    return "".join(
        (
            chr(ord("A") + lon_field),
            chr(ord("A") + lat_field),
            chr(ord("0") + lon_square),
            chr(ord("0") + lat_square),
            chr(ord("a") + lon_sub),
            chr(ord("a") + lat_sub),
            chr(ord("0") + lon_ext),
            chr(ord("0") + lat_ext),
        )
    )
