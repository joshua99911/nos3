#!/usr/bin/env python3
"""Ground Front End router for a NOS3 constellation lab.

The Front End is the ground-side entry point described in the CSS paper. It
routes TC transfer frames from COSMOS/CryptoLib to satellites in visibility and
routes TM transfer frames from satellites back to the matching COSMOS/CryptoLib
instance. Routing is intentionally static and JSON-driven, matching the current
paper implementation hypothesis.
"""
from __future__ import annotations

import argparse
import json
import logging
import select
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Tuple

from css_tf import FrameSpec, read_scid

Address = Tuple[str, int]


@dataclass(frozen=True)
class SatelliteRoute:
    scid: int
    host: str
    feeder_tc_port: int
    visible: bool = True

    @property
    def tc_address(self) -> Address:
        return (self.host, self.feeder_tc_port)


@dataclass(frozen=True)
class CosmosRoute:
    scid: int
    host: str
    tm_port: int

    @property
    def tm_address(self) -> Address:
        return (self.host, self.tm_port)


class FrontEnd:
    def __init__(self, config: Mapping[str, object]):
        self.config = config
        self.frame_spec = FrameSpec.from_mapping(config.get("frame") if isinstance(config.get("frame"), Mapping) else None)
        ground = config.get("ground") or {}
        if not isinstance(ground, Mapping):
            raise ValueError("ground must be a JSON object")
        self.bind_host = str(ground.get("host", "0.0.0.0"))
        self.tc_port = int(ground.get("front_end_tc_port", 8010))
        self.tm_port = int(ground.get("front_end_tm_port", 8011))
        self.satellites = self._load_satellites(config.get("satellites") or {})
        self.cosmos_tf = self._load_cosmos(config.get("cosmos_tf") or {})
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.tc_sock = self._bind(self.tc_port)
        self.tm_sock = self._bind(self.tm_port)

    def _bind(self, port: int) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((self.bind_host, port))
        sock.setblocking(False)
        logging.info("Front End listening on %s:%s", self.bind_host, port)
        return sock

    @staticmethod
    def _load_satellites(raw: object) -> Dict[int, SatelliteRoute]:
        if not isinstance(raw, Mapping):
            raise ValueError("satellites must be a JSON object keyed by SCID")
        result: Dict[int, SatelliteRoute] = {}
        for key, value in raw.items():
            if not isinstance(value, Mapping):
                raise ValueError(f"satellites.{key} must be an object")
            scid = int(value.get("scid", key))
            result[scid] = SatelliteRoute(
                scid=scid,
                host=str(value["host"]),
                feeder_tc_port=int(value.get("feeder_tc_port", value.get("tc_port", 5012))),
                visible=bool(value.get("visible", True)),
            )
        return result

    @staticmethod
    def _load_cosmos(raw: object) -> Dict[int, CosmosRoute]:
        if not isinstance(raw, Mapping):
            raise ValueError("cosmos_tf must be a JSON object keyed by SCID")
        result: Dict[int, CosmosRoute] = {}
        for key, value in raw.items():
            if not isinstance(value, Mapping):
                raise ValueError(f"cosmos_tf.{key} must be an object")
            scid = int(value.get("scid", key))
            result[scid] = CosmosRoute(
                scid=scid,
                host=str(value.get("host", "127.0.0.1")),
                tm_port=int(value.get("tm_port", value.get("cosmos_tm_port", 6011))),
            )
        return result

    def list_satellites(self) -> None:
        for scid in sorted(self.satellites):
            sat = self.satellites[scid]
            cosmos = self.cosmos_tf.get(scid)
            logging.info(
                "SCID=%s visible=%s TC->%s:%s TM->%s",
                scid,
                sat.visible,
                sat.host,
                sat.feeder_tc_port,
                f"{cosmos.host}:{cosmos.tm_port}" if cosmos else "not configured",
            )

    def run(self) -> None:
        logging.info("Front End started with %d configured satellites", len(self.satellites))
        self.list_satellites()
        while True:
            readable, _, _ = select.select([self.tc_sock, self.tm_sock], [], [])
            for sock in readable:
                data, addr = sock.recvfrom(65535)
                try:
                    if sock is self.tc_sock:
                        self._handle_tc(data, addr)
                    else:
                        self._handle_tm(data, addr)
                except Exception:
                    logging.exception("dropping malformed datagram from %s", addr)

    def _handle_tc(self, data: bytes, addr: Address) -> None:
        scid = read_scid(data, self.frame_spec)
        sat = self.satellites.get(scid)
        if sat is None:
            logging.warning("TC from %s has unknown SCID %s; dropping", addr, scid)
            return
        if not sat.visible:
            logging.info("TC for SCID %s dropped: satellite not in visibility", scid)
            return
        self.tx.sendto(data, sat.tc_address)
        logging.debug("TC SCID %s: %s -> %s", scid, addr, sat.tc_address)

    def _handle_tm(self, data: bytes, addr: Address) -> None:
        scid = read_scid(data, self.frame_spec)
        cosmos = self.cosmos_tf.get(scid)
        if cosmos is None:
            logging.warning("TM from %s has unknown SCID %s; dropping", addr, scid)
            return
        self.tx.sendto(data, cosmos.tm_address)
        logging.debug("TM SCID %s: %s -> %s", scid, addr, cosmos.tm_address)


def load_config(path: str | Path) -> Mapping[str, object]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the CSS/NOS3 constellation ground Front End")
    parser.add_argument("--config", required=True, help="Path to constellation JSON configuration")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--list-only", action="store_true", help="Print configured satellite routing and exit")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    frontend = FrontEnd(load_config(args.config))
    if args.list_only:
        frontend.list_satellites()
        return
    frontend.run()


if __name__ == "__main__":
    main()
