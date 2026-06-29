#!/usr/bin/env python3
"""Convenience CLI for the CSS constellation sidecar prototype."""
from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
from pathlib import Path
from typing import Mapping

from css_tf import TC_FRAME, FrameSpec, parse_hex, read_scid, unwrap_tf, wrap_spp_as_tf


def load_config(path: str | Path) -> Mapping[str, object]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def payload_from_args(args: argparse.Namespace) -> bytes:
    modes = [args.hex is not None, args.text is not None, args.file is not None]
    if sum(modes) != 1:
        raise SystemExit("choose exactly one payload source: --hex, --text, or --file")
    if args.hex is not None:
        return parse_hex(args.hex)
    if args.text is not None:
        return args.text.encode("utf-8")
    return Path(args.file).read_bytes()


def cmd_list(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    ground = config.get("ground", {})
    print("Ground Front End:")
    print(f"  TC in : {ground.get('host', '127.0.0.1')}:{ground.get('front_end_tc_port', 8010)}")
    print(f"  TM in : {ground.get('host', '127.0.0.1')}:{ground.get('front_end_tm_port', 8011)}")
    print("Satellites:")
    for key, sat in sorted((config.get("satellites") or {}).items(), key=lambda kv: int(kv[0])):
        print(
            f"  SCID {sat.get('scid', key)}: host={sat.get('host', '127.0.0.1')} "
            f"visible={sat.get('visible', sat.get('ground_visible', True))} "
            f"feeder_tc={sat.get('feeder_tc_port', 5012)} local_ci={sat.get('local_ci_port', 5010)} "
            f"local_tm={sat.get('local_tm_port', 5011)}"
        )


def cmd_send_tc(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    ground = config.get("ground", {})
    payload = payload_from_args(args)
    frame = wrap_spp_as_tf(payload, scid=args.scid, frame_type=TC_FRAME, sequence=args.sequence, spi=args.spi)
    host = args.host or str(ground.get("host", "127.0.0.1"))
    port = args.port or int(ground.get("front_end_tc_port", 8010))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(frame, (host, port))
    print(f"sent TC frame for SCID {args.scid} to Front End {host}:{port} ({len(frame)} bytes)")


def cmd_send_local_tm(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    sats = config.get("satellites") or {}
    sat = sats.get(str(args.scid)) or sats.get(args.scid)
    if not isinstance(sat, Mapping):
        raise SystemExit(f"SCID {args.scid} is not present in {args.config}")
    payload = payload_from_args(args)
    host = args.host or str(sat.get("host", "127.0.0.1"))
    port = args.port or int(sat.get("local_tm_port", sat.get("to_tm_port", 5011)))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(payload, (host, port))
    print(f"sent local TM/SPP payload for SCID {args.scid} to ISL/RM {host}:{port} ({len(payload)} bytes)")


def cmd_listen_tm(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    spec = FrameSpec.from_mapping(config.get("frame") if isinstance(config.get("frame"), Mapping) else None)
    host = args.host or "0.0.0.0"
    port = args.port
    if port is None:
        cosmos = config.get("cosmos_tf") or {}
        route = cosmos.get(str(args.scid)) if args.scid is not None else None
        if isinstance(route, Mapping):
            port = int(route.get("tm_port", route.get("cosmos_tm_port", 6011)))
        else:
            port = 6011
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((host, int(port)))
    print(f"listening for TM frames on {host}:{port}", flush=True)
    while True:
        frame, addr = sock.recvfrom(65535)
        try:
            scid = read_scid(frame, spec)
            spp = unwrap_tf(frame, spec)
            print(f"TM from SCID {scid} via {addr}: frame={frame.hex()} spp={spp.hex()}", flush=True)
        except Exception as exc:  # pragma: no cover - diagnostic path
            print(f"malformed TM from {addr}: {exc}: {frame.hex()}", file=sys.stderr, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate the CSS/NOS3 constellation sidecar prototype")
    parser.add_argument("--config", default="configs/demo_three_sat.json", help="Path to constellation JSON configuration")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List configured spacecraft IDs and ports").set_defaults(func=cmd_list)

    send_tc = sub.add_parser("send-tc", help="Send one demo TC frame addressed to a spacecraft ID")
    send_tc.add_argument("--scid", type=int, required=True, help="Destination spacecraft ID")
    send_tc.add_argument("--sequence", type=int, default=0, help="Demo transfer-frame sequence counter")
    send_tc.add_argument("--spi", type=lambda x: int(x, 0), default=0, help="Demo SPI integer, e.g. 0x0004")
    send_tc.add_argument("--host", help="Override Front End host")
    send_tc.add_argument("--port", type=int, help="Override Front End TC port")
    add_payload_args(send_tc)
    send_tc.set_defaults(func=cmd_send_tc)

    send_tm = sub.add_parser("send-local-tm", help="Inject a local TM/SPP payload into a satellite ISL/RM")
    send_tm.add_argument("--scid", type=int, required=True, help="Origin spacecraft ID")
    send_tm.add_argument("--host", help="Override local ISL/RM host")
    send_tm.add_argument("--port", type=int, help="Override local TM/SPP port")
    add_payload_args(send_tm)
    send_tm.set_defaults(func=cmd_send_local_tm)

    listen = sub.add_parser("listen-tm", help="Listen for TM frames delivered to a COSMOS/CryptoLib port")
    listen.add_argument("--scid", type=int, help="SCID whose configured cosmos_tf tm_port should be used")
    listen.add_argument("--host", help="Bind host, default 0.0.0.0")
    listen.add_argument("--port", type=int, help="Bind port, default from cosmos_tf or 6011")
    listen.set_defaults(func=cmd_listen_tm)
    return parser


def add_payload_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--hex", help="Payload bytes as hex, e.g. '18 80 c0 00'")
    parser.add_argument("--text", help="Payload text encoded as UTF-8")
    parser.add_argument("--file", help="Read payload bytes from a file")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
