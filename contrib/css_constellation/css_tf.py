#!/usr/bin/env python3
"""Small transfer-frame helpers for the NOS3 CSS constellation sidecars.

This module intentionally keeps the frame handling configurable.  The CSS paper
uses CCSDS TC/TM transfer frames over UDP between the Front End and ISL/RM
components, while COSMOS and the cFS software bus can remain at the Space Packet
Protocol layer.  NOS3 installations differ in exactly where CryptoLib inserts
or parses transfer-frame fields, so the offsets below are defaults for the demo
sidecar envelope used by this directory and can be overridden from JSON config.

Demo frame layout used by wrap_spp_as_tf():
    byte 0      magic/version marker 0xC5
    byte 1      frame type: 0x01 = TC, 0x02 = TM
    bytes 2-3   spacecraft ID, unsigned big-endian
    bytes 4-5   sequence counter, unsigned big-endian
    bytes 6-7   SDLS SPI bytes, matching the 7th/8th-byte location discussed
                in the paper's CryptoLib CVE example
    bytes 8..   SPP payload

When routing frames produced by an external CryptoLib/CCSDS implementation, set
frame.scid_offset/scid_length/scid_mask/scid_shift/spi_offset/spi_length and
header_length in the JSON configuration rather than using wrap_spp_as_tf().
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

TC_FRAME = 0x01
TM_FRAME = 0x02
DEMO_MAGIC = 0xC5


@dataclass(frozen=True)
class FrameSpec:
    """Location of fields needed by the sidecar routers.

    scid_mask and scid_shift are applied to the big-endian integer read from the
    scid field.  For the demo envelope this is a full 16-bit integer at bytes
    2-3.  For a mission-specific CCSDS frame, adjust the offsets/masks in config.
    """

    scid_offset: int = 2
    scid_length: int = 2
    scid_mask: int = 0xFFFF
    scid_shift: int = 0
    spi_offset: int = 6
    spi_length: int = 2
    header_length: int = 8
    payload_offset: Optional[int] = None

    @classmethod
    def from_mapping(cls, value: Optional[Mapping[str, Any]]) -> "FrameSpec":
        if not value:
            return cls()
        return cls(
            scid_offset=int(value.get("scid_offset", cls.scid_offset)),
            scid_length=int(value.get("scid_length", cls.scid_length)),
            scid_mask=_int(value.get("scid_mask", cls.scid_mask)),
            scid_shift=int(value.get("scid_shift", cls.scid_shift)),
            spi_offset=int(value.get("spi_offset", cls.spi_offset)),
            spi_length=int(value.get("spi_length", cls.spi_length)),
            header_length=int(value.get("header_length", cls.header_length)),
            payload_offset=(
                None
                if value.get("payload_offset") is None
                else int(value.get("payload_offset"))
            ),
        )


def _int(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    return int(value)


def _ensure_len(data: bytes | bytearray, needed: int, field: str) -> None:
    if len(data) < needed:
        raise ValueError(f"frame too short for {field}: need {needed} bytes, got {len(data)}")


def read_scid(frame: bytes | bytearray, spec: FrameSpec = FrameSpec()) -> int:
    """Return the configured spacecraft ID from a transfer frame."""
    end = spec.scid_offset + spec.scid_length
    _ensure_len(frame, end, "spacecraft ID")
    raw = int.from_bytes(frame[spec.scid_offset:end], "big")
    return (raw & spec.scid_mask) >> spec.scid_shift


def write_scid(frame: bytes | bytearray, scid: int, spec: FrameSpec = FrameSpec()) -> bytes:
    """Return a copy of frame with the configured spacecraft ID rewritten."""
    if scid < 0:
        raise ValueError("spacecraft ID must be non-negative")
    end = spec.scid_offset + spec.scid_length
    _ensure_len(frame, end, "spacecraft ID")
    mutable = bytearray(frame)
    raw = int.from_bytes(mutable[spec.scid_offset:end], "big")
    shifted = (scid << spec.scid_shift) & spec.scid_mask
    raw = (raw & ~spec.scid_mask) | shifted
    mutable[spec.scid_offset:end] = raw.to_bytes(spec.scid_length, "big")
    return bytes(mutable)


def read_spi(frame: bytes | bytearray, spec: FrameSpec = FrameSpec()) -> bytes:
    """Return the configured SDLS Security Parameter Index bytes."""
    end = spec.spi_offset + spec.spi_length
    _ensure_len(frame, end, "SPI")
    return bytes(frame[spec.spi_offset:end])


def payload_offset(spec: FrameSpec = FrameSpec()) -> int:
    return spec.header_length if spec.payload_offset is None else spec.payload_offset


def unwrap_tf(frame: bytes | bytearray, spec: FrameSpec = FrameSpec()) -> bytes:
    """Extract the SPP payload from a transfer-frame-like UDP datagram."""
    offset = payload_offset(spec)
    _ensure_len(frame, offset, "payload")
    return bytes(frame[offset:])


def wrap_spp_as_tf(
    spp: bytes | bytearray,
    *,
    scid: int,
    frame_type: int,
    sequence: int = 0,
    spi: bytes | bytearray | int = b"\x00\x00",
) -> bytes:
    """Wrap an SPP datagram in the demo transfer-frame envelope.

    This is not a full CCSDS TC/TM encoder; it is a deterministic lab envelope
    that lets the Front End, ISL/RM and IDS sidecars be exercised before deeper
    integration with NOS3's CryptoLib transfer-frame path.
    """
    if not 0 <= scid <= 0xFFFF:
        raise ValueError("demo frame supports SCID values from 0 to 65535")
    if frame_type not in (TC_FRAME, TM_FRAME):
        raise ValueError("frame_type must be TC_FRAME or TM_FRAME")
    if not 0 <= sequence <= 0xFFFF:
        raise ValueError("sequence must fit in 16 bits")
    if isinstance(spi, int):
        if not 0 <= spi <= 0xFFFF:
            raise ValueError("integer SPI must fit in 16 bits")
        spi_bytes = spi.to_bytes(2, "big")
    else:
        spi_bytes = bytes(spi)
    if len(spi_bytes) != 2:
        raise ValueError("demo frame SPI must be exactly two bytes")
    return bytes(
        [DEMO_MAGIC, frame_type]
    ) + scid.to_bytes(2, "big") + sequence.to_bytes(2, "big") + spi_bytes + bytes(spp)


def is_demo_frame(frame: bytes | bytearray) -> bool:
    return len(frame) >= 8 and frame[0] == DEMO_MAGIC and frame[1] in (TC_FRAME, TM_FRAME)


def parse_hex(value: str) -> bytes:
    cleaned = value.replace(" ", "").replace(":", "").replace("0x", "")
    if len(cleaned) % 2:
        cleaned = "0" + cleaned
    return bytes.fromhex(cleaned)
