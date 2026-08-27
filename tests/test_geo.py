"""Maidenhead locator and unit constants."""

from __future__ import annotations

import pytest

from stratumtap.geo import (
    METERS_TO_FEET,
    MPS_TO_KNOTS,
    MPS_TO_KPH,
    MPS_TO_MPH,
    maidenhead,
)


def test_maidenhead_reference_location():
    assert maidenhead(41.71343333, -91.66269) == "EN41er01"


@pytest.mark.parametrize(
    "lat,lon",
    [
        (0.0, 0.0),
        (89.99999, 179.99999),
        (-89.99999, -179.99999),
        (51.5, -0.12),
        (-33.86, 151.21),
    ],
)
def test_maidenhead_shape(lat, lon):
    grid = maidenhead(lat, lon)
    assert len(grid) == 8
    assert grid[0].isupper() and grid[1].isupper()
    assert grid[2].isdigit() and grid[3].isdigit()
    assert grid[4].islower() and grid[5].islower()
    assert grid[6].isdigit() and grid[7].isdigit()


def test_maidenhead_clamps_out_of_range():
    # Values beyond the poles/antimeridian must not raise or overflow the alphabet.
    assert len(maidenhead(95.0, 200.0)) == 8
    assert len(maidenhead(-95.0, -200.0)) == 8


def test_unit_constants():
    assert pytest.approx(3.6) == MPS_TO_KPH
    assert pytest.approx(2.2369362920544) == MPS_TO_MPH
    assert pytest.approx(1.9438444924406) == MPS_TO_KNOTS
    assert pytest.approx(3.28084) == METERS_TO_FEET
