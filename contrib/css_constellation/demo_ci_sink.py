#!/usr/bin/env python3
"""Tiny UDP sink that stands in for a NOS3 CI app during loopback tests.

It lets you verify that a TC addressed to SCID N is delivered only to satellite
N's local CI/SPP port before wiring the sidecars into cFS.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path
from typing import Mapping


def load_config(path: str | Path) -> Mapping[str, object]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main() -> None:
    parser = argparse.ArgumentParser(description="Listen on one configured satellite CI/SPP port")
    parser.add_argument("--config", required=True, help="Path to constellation JSON configuration")
    parser.add_argument("--scid", required=True, type=int, help="Local spacecraft ID")
    parser.add_argument("--host", help="Bind host override")
    parser.add_argument("--port", type=int, help="Bind port override")
    args = parser.parse_args()
    config = load_config(args.config)
    sat = (config.get("satellites") or {}).get(str(args.scid))
    if not isinstance(sat, Mapping):
        raise SystemExit(f"SCID {args.scid} is not present in {args.config}")
    host = args.host or str(sat.get("local_ci_host", sat.get("host", "127.0.0.1")))
    port = args.port or int(sat.get("local_ci_port", 5010))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((host, port))
    print(f"CI sink for SCID {args.scid} listening on {host}:{port}", flush=True)
    while True:
        data, addr = sock.recvfrom(65535)
        print(f"SCID {args.scid} CI received {len(data)} bytes from {addr}: {data.hex()} {data!r}", flush=True)
        sys.stdout.flush()


if __name__ == "__main__":
    main()
