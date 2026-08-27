"""NMEA checksum and sentence parsing."""

from __future__ import annotations

import pytest

from stratumtap.nmea import nmea_checksum, nmea_sentence, parse_nmea_line

GGA = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
RMC = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
AIVDM = "!AIVDM,1,1,,B,177KQJ5000G?tO`K>RA1wUbN0TKH,0*5C"
PMTK = "$PMTK010,001*2E"


# --------------------------------------------------------------------------
# checksum
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,", "47"),
        ("GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W", "6A"),
        ("PMTK010,001", "2E"),
        ("AIVDM,1,1,,B,177KQJ5000G?tO`K>RA1wUbN0TKH,0", "5C"),
    ],
)
def test_checksum_matches_the_published_sentences(body, expected):
    assert nmea_checksum(body) == expected


def test_checksum_is_two_uppercase_hex_digits():
    # "A" alone is 0x41 -> one hex digit's worth of value, still zero-padded.
    assert nmea_checksum("A") == "41"
    assert nmea_checksum("AB") == "03"  # 0x41 ^ 0x42
    assert nmea_checksum("") == "00"


def test_nmea_sentence_round_trips_through_the_parser():
    line = nmea_sentence("GPVTG,63.51,T,64.40,M,0.04,N,0.08,K,D")
    assert line.startswith("$GPVTG,")
    parsed = parse_nmea_line(line)
    assert parsed["checksum_ok"] is True
    assert (parsed["talker"], parsed["type"]) == ("GP", "VTG")

    ais = nmea_sentence("AIVDM,1,1,,A,test,0", sentinel="!")
    assert ais.startswith("!AIVDM")
    assert parse_nmea_line(ais)["checksum_ok"] is True


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "talker", "type_"),
    [
        (GGA, "GP", "GGA"),
        (RMC, "GP", "RMC"),
        ("$GNRMC,155953.000,A,4402.7276,N,08801.0331,W,0.02,63.5,260826,,,D*4E", "GN", "RMC"),
        ("$GLGSV,3,1,09,65,10,050,30*5A", "GL", "GSV"),
        ("$GAGSV,1,1,00*74", "GA", "GSV"),
        ("$GBGSV,1,1,00*76", "GB", "GSV"),
        (AIVDM, "AI", "VDM"),
        (PMTK, "P", "MTK010"),
    ],
)
def test_talker_and_type(line, talker, type_):
    parsed = parse_nmea_line(line)
    assert parsed["talker"] == talker
    assert parsed["type"] == type_
    assert parsed["line"] == line


def test_valid_sentence():
    parsed = parse_nmea_line(GGA + "\r\n")
    assert parsed == {
        "line": GGA,
        "type": "GGA",
        "talker": "GP",
        "checksum_ok": True,
    }


def test_corrupted_sentence_fails_the_checksum():
    corrupted = GGA.replace("545.4", "545.5")
    parsed = parse_nmea_line(corrupted)
    assert parsed["checksum_ok"] is False
    # A garbled payload is still classified, so the UI can show what broke.
    assert (parsed["talker"], parsed["type"]) == ("GP", "GGA")


def test_truncated_checksum_digit_fails():
    assert parse_nmea_line(GGA[:-1])["checksum_ok"] is False
    assert parse_nmea_line(GGA[:-2])["checksum_ok"] is False


def test_sentence_without_a_checksum():
    parsed = parse_nmea_line("$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,")
    assert parsed["checksum_ok"] is None
    assert parsed["type"] == "GGA"


def test_encapsulated_ais_sentence():
    parsed = parse_nmea_line(AIVDM)
    assert parsed["checksum_ok"] is True
    assert parsed["talker"] == "AI"
    assert parsed["type"] == "VDM"


def test_proprietary_sentence():
    parsed = parse_nmea_line(PMTK)
    assert parsed == {
        "line": PMTK,
        "type": "MTK010",
        "talker": "P",
        "checksum_ok": True,
    }
    # Proprietary sentences vary wildly in length; the type is simply the rest.
    assert parse_nmea_line("$PGRMT,GPS24xd-HVS,,,,,,,,*4C")["type"] == "GRMT"


@pytest.mark.parametrize("line", ["", "   ", "\r\n", "garbage", "$", "*4C"])
def test_junk_never_raises(line):
    parsed = parse_nmea_line(line)
    assert set(parsed) == {"line", "type", "talker", "checksum_ok"}
    assert isinstance(parsed["line"], str)
