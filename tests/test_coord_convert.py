"""Tests for Az/Alt <-> RA/Dec coordinate conversion."""

import pytest

from src.starmap.projection import azalt_to_radec, radec_to_azalt


class TestAzAltRadecRoundtrip:
    """Verify that Az/Alt -> RA/Dec -> Az/Alt roundtrips correctly."""

    # Vienna, 2026-03-22 22:00 UTC (MJD ~61456.917)
    LAT = 48.2
    LON = 16.4
    MJD = 61456.917

    def test_south_45deg(self) -> None:
        az, alt = 180.0, 45.0
        ra, dec = azalt_to_radec(az, alt, self.LAT, self.LON, self.MJD)
        az2, alt2 = radec_to_azalt(ra, dec, self.LAT, self.LON, self.MJD)
        assert az2 == pytest.approx(az, abs=0.01)
        assert alt2 == pytest.approx(alt, abs=0.01)

    def test_north_30deg(self) -> None:
        az, alt = 0.0, 30.0
        ra, dec = azalt_to_radec(az, alt, self.LAT, self.LON, self.MJD)
        az2, alt2 = radec_to_azalt(ra, dec, self.LAT, self.LON, self.MJD)
        assert az2 == pytest.approx(az, abs=0.01)
        assert alt2 == pytest.approx(alt, abs=0.01)

    def test_east_60deg(self) -> None:
        az, alt = 90.0, 60.0
        ra, dec = azalt_to_radec(az, alt, self.LAT, self.LON, self.MJD)
        az2, alt2 = radec_to_azalt(ra, dec, self.LAT, self.LON, self.MJD)
        assert az2 == pytest.approx(az, abs=0.01)
        assert alt2 == pytest.approx(alt, abs=0.01)

    def test_zenith(self) -> None:
        az, alt = 0.0, 90.0
        ra, dec = azalt_to_radec(az, alt, self.LAT, self.LON, self.MJD)
        az2, alt2 = radec_to_azalt(ra, dec, self.LAT, self.LON, self.MJD)
        # Azimuth is undefined at zenith, only check altitude
        assert alt2 == pytest.approx(alt, abs=0.01)

    def test_returns_valid_ra_range(self) -> None:
        az, alt = 270.0, 20.0
        ra, dec = azalt_to_radec(az, alt, self.LAT, self.LON, self.MJD)
        assert 0.0 <= ra < 360.0
        assert -90.0 <= dec <= 90.0
