"""NMEA 0183 sentence helpers: checksum and a tolerant one-line parser.

gpsd forwards raw sentences when watched with ``"nmea":true``; they arrive on the
same socket as the JSON, interleaved with it. Everything here is pure so it can be
unit-tested without a socket, and nothing ever raises on malformed input — a
receiver that garbles a line must not kill the reader task.
"""

from __future__ import annotations

#: Sentence start delimiters: ``$`` for talker/proprietary, ``!`` for encapsulated (AIS).
SENTINELS = "$!"


def nmea_checksum(body: str) -> str:
    """Return the two-digit uppercase hex XOR checksum of *body*.

    *body* is the part between the sentinel (``$``/``!``) and the ``*``, both
    excluded — exactly what NMEA 0183 checksums.
    """
    checksum = 0
    for byte in body.encode("utf-8", "replace"):
        checksum ^= byte
    return f"{checksum:02X}"


def nmea_sentence(body: str, sentinel: str = "$") -> str:
    """Wrap *body* into a complete sentence with its checksum appended."""
    return f"{sentinel}{body}*{nmea_checksum(body)}"


def _split_address(address: str) -> tuple[str | None, str | None]:
    """``"GPRMC"`` -> ``("GP", "RMC")``; ``"PMTK010"`` -> ``("P", "MTK010")``."""
    if not address:
        return None, None
    if address[0] == "P":
        # Proprietary: "P" + manufacturer mnemonic + message, e.g. $PMTK010.
        rest = address[1:]
        return "P", rest or None
    if len(address) >= 3:
        return address[:2], address[2:]
    return address, None


def parse_nmea_line(line: str) -> dict:
    """Parse one raw NMEA line into ``line``/``type``/``talker``/``checksum_ok``.

    ``checksum_ok`` is ``None`` when the sentence carries no ``*NN`` suffix,
    ``True``/``False`` otherwise. ``type`` is the sentence type after the talker
    id (``"RMC"``, ``"GGA"``, ``"VDM"``, …); for proprietary ``$P…`` sentences the
    talker is ``"P"`` and the type is the remainder (``"MTK010"``). Unparseable
    input yields ``None`` fields rather than an exception.
    """
    text = (line or "").strip().strip("\r\n").strip()
    result: dict = {"line": text, "type": None, "talker": None, "checksum_ok": None}
    if not text:
        return result

    body = text[1:] if text[0] in SENTINELS else text

    star = body.rfind("*")
    if star >= 0:
        payload, given = body[:star], body[star + 1 :].strip()
        result["checksum_ok"] = bool(given) and given.upper() == nmea_checksum(payload)
    else:
        payload = body

    address = payload.split(",", 1)[0].strip()
    talker, sentence_type = _split_address(address)
    result["talker"] = talker
    result["type"] = sentence_type
    return result


def nmea_fields(line: str) -> list[str]:
    """The comma-separated fields of *line*, address first, checksum stripped."""
    text = (line or "").strip()
    if text[:1] in SENTINELS:
        text = text[1:]
    star = text.rfind("*")
    if star >= 0:
        text = text[:star]
    return text.split(",") if text else []
