#!/usr/bin/env python3
"""Satellite-side Inter-Satellite Link / Routing Machine sidecar.

Run one instance next to each NOS3 spacecraft VM.  The component accepts TC
transfer frames from the ground Front End or neighbouring satellites, delivers
locally addressed commands to the spacecraft CI input as SPP, and forwards other
TC frames according to the static route table.  It also wraps local TM/SPP from
TO into transfer frames and forwards them either to the Front End or through an
inter-satellite route to a visible spacecraft.
"""
from __future__ import annotations

import argparse
import json
import logging
import select
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

from css_tf import FrameSpec, TC_FRAME, TM_FRAME, read_scid, read_spi, unwrap_tf, wrap_spp_as_tf

Address = Tuple[str, int]


@dataclass(frozen=True)
class SatelliteConfig:
    scid: int
    host: str
    feeder_tc_port: int
    isl_tc_port: int
    isl_tm_port: int
    local_ci_host: str
    local_ci_port: int
    local_tm_port: int
    ground_visible: bool
    tc_routes: Dict[int, int]
    tm_route_to_ground: Optional[int]
    drop_spi_values: tuple[bytes, ...]
    min_tc_interval_ms: int

    @property
    def feeder_tc_bind(self) -> Address:
        return (self.host, self.feeder_tc_port)

    @property
    def isl_tc_bind(self) -> Address:
        return (self.host, self.isl_tc_port)

    @property
    def isl_tm_bind(self) -> Address:
        return (self.host, self.isl_tm_port)

    @property
    def local_tm_bind(self) -> Address:
        return (self.host, self.local_tm_port)

    @property
    def ci_address(self) -> Address:
        return (self.local_ci_host, self.local_ci_port)


class IslRm:
    def __init__(self, config: Mapping[str, object], scid: int):
        self.config = config
        self.frame_spec = FrameSpec.from_mapping(config.get("frame") if isinstance(config.get("frame"), Mapping) else None)
        self.ground = config.get("ground") or {}
        if not isinstance(self.ground, Mapping):
            raise ValueError("ground must be a JSON object")
        self.satellites = self._load_satellites(config.get("satellites") or {})
        if scid not in self.satellites:
            raise ValueError(f"SCID {scid} is not configured")
        self.sat = self.satellites[scid]
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.feeder_tc_sock = self._bind(self.sat.feeder_tc_bind, "feeder TC")
        self.isl_tc_sock = self._bind(self.sat.isl_tc_bind, "ISL TC")
        self.isl_tm_sock = self._bind(self.sat.isl_tm_bind, "ISL TM")
        self.local_tm_sock = self._bind(self.sat.local_tm_bind, "local TO/SPP TM")
        self.last_tc_time = 0.0

    def _bind(self, addr: Address, label: str) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(addr)
        sock.setblocking(False)
        logging.info("ISL/RM SCID %s listening for %s on %s:%s", self.sat.scid if hasattr(self, "sat") else "?", label, addr[0], addr[1])
        return sock

    @staticmethod
    def _load_satellites(raw: object) -> Dict[int, SatelliteConfig]:
        if not isinstance(raw, Mapping):
            raise ValueError("satellites must be a JSON object keyed by SCID")
        result: Dict[int, SatelliteConfig] = {}
        for key, value in raw.items():
            if not isinstance(value, Mapping):
                raise ValueError(f"satellites.{key} must be an object")
            scid = int(value.get("scid", key))
            drop_spi_values = tuple(_parse_spi_list(value.get("drop_spi_values", [])))
            result[scid] = SatelliteConfig(
                scid=scid,
                host=str(value.get("host", "127.0.0.1")),
                feeder_tc_port=int(value.get("feeder_tc_port", value.get("tc_port", 5012))),
                isl_tc_port=int(value.get("isl_tc_port", value.get("feeder_tc_port", 5012) + 1000)),
                isl_tm_port=int(value.get("isl_tm_port", value.get("feeder_tc_port", 5012) + 1001)),
                local_ci_host=str(value.get("local_ci_host", "127.0.0.1")),
                local_ci_port=int(value.get("local_ci_port", 5010)),
                local_tm_port=int(value.get("local_tm_port", value.get("to_tm_port", 5011))),
                ground_visible=bool(value.get("ground_visible", value.get("visible", True))),
                tc_routes=_int_key_map(value.get("tc_routes", {})),
                tm_route_to_ground=(None if value.get("tm_route_to_ground") is None else int(value.get("tm_route_to_ground"))),
                drop_spi_values=drop_spi_values,
                min_tc_interval_ms=int(value.get("min_tc_interval_ms", 0)),
            )
        return result

    def run(self) -> None:
        logging.info(
            "ISL/RM started for SCID %s ground_visible=%s ci=%s:%s",
            self.sat.scid,
            self.sat.ground_visible,
            self.sat.local_ci_host,
            self.sat.local_ci_port,
        )
        sockets = [self.feeder_tc_sock, self.isl_tc_sock, self.isl_tm_sock, self.local_tm_sock]
        while True:
            readable, _, _ = select.select(sockets, [], [])
            for sock in readable:
                data, addr = sock.recvfrom(65535)
                try:
                    if sock in (self.feeder_tc_sock, self.isl_tc_sock):
                        self._handle_tc_frame(data, addr)
                    elif sock is self.local_tm_sock:
                        self._handle_local_tm_spp(data, addr)
                    else:
                        self._handle_tm_frame(data, addr)
                except Exception:
                    logging.exception("SCID %s dropping malformed datagram from %s", self.sat.scid, addr)

    def _handle_tc_frame(self, frame: bytes, addr: Address) -> None:
        dest_scid = read_scid(frame, self.frame_spec)
        if not self._tc_admitted(frame, dest_scid, addr):
            return
        if dest_scid == self.sat.scid:
            spp = unwrap_tf(frame, self.frame_spec)
            self.tx.sendto(spp, self.sat.ci_address)
            logging.debug("TC for local SCID %s delivered to CI %s", dest_scid, self.sat.ci_address)
            return
        next_scid = self.sat.tc_routes.get(dest_scid)
        if next_scid is None:
            logging.warning("TC for SCID %s arrived at SCID %s with no route; dropping", dest_scid, self.sat.scid)
            return
        next_sat = self.satellites.get(next_scid)
        if next_sat is None:
            logging.warning("TC route for SCID %s points to unknown next SCID %s", dest_scid, next_scid)
            return
        next_addr = (next_sat.host, next_sat.isl_tc_port)
        self.tx.sendto(frame, next_addr)
        logging.debug("TC for SCID %s forwarded via SCID %s to %s", dest_scid, next_scid, next_addr)

    def _tc_admitted(self, frame: bytes, dest_scid: int, addr: Address) -> bool:
        now = time.monotonic()
        if self.sat.min_tc_interval_ms > 0:
            delta_ms = (now - self.last_tc_time) * 1000.0
            if self.last_tc_time and delta_ms < self.sat.min_tc_interval_ms:
                logging.warning(
                    "TC for SCID %s from %s dropped by anti-flood guard: %.1f ms < %s ms",
                    dest_scid,
                    addr,
                    delta_ms,
                    self.sat.min_tc_interval_ms,
                )
                return False
        self.last_tc_time = now
        if self.sat.drop_spi_values:
            spi = read_spi(frame, self.frame_spec)
            if spi in self.sat.drop_spi_values:
                logging.warning("TC for SCID %s from %s dropped by SPI guard: %s", dest_scid, addr, spi.hex())
                return False
        return True

    def _handle_local_tm_spp(self, spp: bytes, addr: Address) -> None:
        frame = wrap_spp_as_tf(spp, scid=self.sat.scid, frame_type=TM_FRAME)
        self._route_tm_frame(frame, source_addr=addr)

    def _handle_tm_frame(self, frame: bytes, addr: Address) -> None:
        self._route_tm_frame(frame, source_addr=addr)

    def _route_tm_frame(self, frame: bytes, source_addr: Address) -> None:
        origin_scid = read_scid(frame, self.frame_spec)
        if self.sat.ground_visible:
            ground_addr = (
                str(self.ground.get("host", "127.0.0.1")),
                int(self.ground.get("front_end_tm_port", 8011)),
            )
            self.tx.sendto(frame, ground_addr)
            logging.debug("TM from SCID %s forwarded from SCID %s to Front End %s", origin_scid, self.sat.scid, ground_addr)
            return
        next_scid = self.sat.tm_route_to_ground
        if next_scid is None:
            logging.warning("TM from SCID %s at SCID %s has no ground route; dropping", origin_scid, self.sat.scid)
            return
        next_sat = self.satellites.get(next_scid)
        if next_sat is None:
            logging.warning("TM route to ground points to unknown next SCID %s", next_scid)
            return
        next_addr = (next_sat.host, next_sat.isl_tm_port)
        self.tx.sendto(frame, next_addr)
        logging.debug("TM from SCID %s forwarded via SCID %s to %s", origin_scid, next_scid, next_addr)


def _parse_spi_list(raw: object) -> list[bytes]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("drop_spi_values must be a list")
    values: list[bytes] = []
    for item in raw:
        if isinstance(item, int):
            if not 0 <= item <= 0xFFFF:
                raise ValueError("SPI integer must fit in 16 bits")
            values.append(item.to_bytes(2, "big"))
        elif isinstance(item, str):
            cleaned = item.replace(" ", "").replace(":", "").replace("0x", "")
            if len(cleaned) % 2:
                cleaned = "0" + cleaned
            values.append(bytes.fromhex(cleaned))
        else:
            raise ValueError("SPI entries must be strings or integers")
    return values


def _int_key_map(raw: object) -> Dict[int, int]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError("route maps must be JSON objects keyed by SCID")
    return {int(k): int(v) for k, v in raw.items()}


def load_config(path: str | Path) -> Mapping[str, object]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one CSS/NOS3 satellite ISL/RM sidecar")
    parser.add_argument("--config", required=True, help="Path to constellation JSON configuration")
    parser.add_argument("--scid", required=True, type=int, help="Local spacecraft ID")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    IslRm(load_config(args.config), args.scid).run()


if __name__ == "__main__":
    main()
