# CSS-style constellation prototype for NOS3

This directory is an **incremental prototype** based on the architecture in *A Satellite Constellation Simulator for Space Systems Cybersecurity Research and Development*. The first objective is deliberately narrower than reproducing the complete CSS cyber range: make multiple NOS3 spacecraft independently addressable and provide a static Front End + ISL/RM routing fabric that can later be connected to real NOS3 cFS/COSMOS/CryptoLib processes.

## What is implemented in this branch

- A ground `front_end.py` with separate TC/TM UDP ingress ports.
- Per-spacecraft `isl_rm.py` processes with static TC and TM next-hop routing.
- Spacecraft-ID based addressing: the destination SCID remains in the transfer-frame-like header while a frame is forwarded through an intermediate satellite.
- A JSON route/visibility configuration.
- A three-spacecraft example in which SCID 101 is ground-visible, SCID 102 is one ISL hop away and SCID 103 can be reached through 101 -> 102 -> 103.
- A small CLI for listing spacecraft, injecting a TC for one SCID, injecting local TM, and listening for returned TM.
- Optional first-probe-style guards in ISL/RM for minimum TC inter-arrival time and configured SPI values.

## Important limitation: transfer-frame compatibility

`css_tf.py` currently provides a small deterministic **lab envelope**, not a complete CCSDS TC/TM encoder. This is intentional: it proves SCID-based constellation routing without pretending that the exact current NOS3/CryptoLib transfer-frame layout has already been integrated.

The demo envelope is:

```
0       1       2..3       4..5       6..7       8..
0xC5    type    SCID        sequence    SPI         SPP payload
```

The frame parser is configurable (`scid_offset`, `scid_length`, `scid_mask`, `scid_shift`, `spi_offset`, `header_length`). The next integration step is to point those fields at the real TC/TM frame emitted/consumed by the CryptoLib path, or replace the wrapper with CryptoLib calls.

## Fast loopback test

From this directory:

```bash
chmod +x run_loopback_demo.sh
./run_loopback_demo.sh
```

In another terminal, stand in for spacecraft 103's CI application:

```bash
python3 demo_ci_sink.py --config configs/demo_three_sat.json --scid 103
```

Then address **only spacecraft 103**:

```bash
python3 constellation_cli.py --config configs/demo_three_sat.json \
  send-tc --scid 103 --text ONLY_103 --spi 0x0004
```

The intended TC path is:

```
operator/COSMOS -> Front End -> SAT101 ISL/RM -> SAT102 ISL/RM
                -> SAT103 ISL/RM -> SAT103 CI
```

The SCID stays `103` for the entire route. SAT101 and SAT102 forward the frame instead of delivering it to their local CI ports.

For the reverse direction, start a listener for SCID 103:

```bash
python3 constellation_cli.py --config configs/demo_three_sat.json listen-tm --scid 103
```

Then inject local telemetry from spacecraft 103:

```bash
python3 constellation_cli.py --config configs/demo_three_sat.json \
  send-local-tm --scid 103 --text TM_FROM_103
```

The intended TM path is:

```
SAT103 TO -> SAT103 ISL/RM -> SAT102 ISL/RM -> SAT101 ISL/RM
          -> Front End -> COSMOS/CryptoLib instance for SCID 103
```

## How this differs from standard NOS3 operation

The standard NOS3 workflow remains the baseline for building the mission and starting its normal FSW, GSW, 42, and simulator processes. This branch adds a routing layer around the TC/TM boundary rather than replacing cFS or 42.

| Standard NOS3 concept | This branch / CSS-style constellation concept |
| --- | --- |
| One normal command/telemetry path into a spacecraft | Front End is the ground-side constellation entry point |
| CI is effectively the spacecraft TC entry point | ISL/RM becomes the network entry point; only locally addressed TC is unwrapped and delivered to CI |
| TO sends telemetry toward the normal ground path | TO/SPP is first sent to local ISL/RM, which wraps/routes it toward ground |
| SPP is sufficient for the ordinary path | A transfer-frame layer (or this temporary lab envelope) carries SCID so frames can be routed per spacecraft |
| One COSMOS target/path can operate the spacecraft | Configure one logical COSMOS/CryptoLib path per SCID, or equivalent target separation |
| No constellation route table is required | Front End and every ISL/RM have explicit static next-hop tables |
| Visibility is not a constellation routing decision | `visible` / `ground_visible` selects direct feeder connectivity; non-visible satellites use ISL next hops |
| One spacecraft process set | Ultimately run an independently identifiable cFS/NOS3 spacecraft instance per SCID; 42 can remain the common environment source |

## Connecting it to a real NOS3 checkout

Do this incrementally. Do not start by cloning the whole VM architecture from the paper.

### 1. Prove two independent cFS instances first

Create two spacecraft instances, for example SCID 101 and 102. Give each instance unique UDP endpoints for CI input and TO output. The key acceptance test is that a command sent to SCID 102 is received by the second cFS instance and not the first.

Suggested local mapping:

```
SCID 101: CI input 15010, TO -> ISL/RM 15011
SCID 102: CI input 25010, TO -> ISL/RM 25011
Front End: TC 18010, TM 18011
```

Update `configs/demo_three_sat.json` to match the actual addresses.

### 2. Insert ISL/RM in front of CI

For each spacecraft, change the external TC destination from CI directly to that spacecraft's `feeder_tc_port`/`isl_tc_port`. ISL/RM reads the SCID. If it matches the local SCID it removes the routing/TF header and sends the remaining SPP bytes to `local_ci_port`. Otherwise it sends the unchanged frame to the configured next hop.

### 3. Insert ISL/RM after TO

Configure each spacecraft's TO UDP destination as its `local_tm_port`. ISL/RM associates the local SCID with the TM and sends the resulting frame to the Front End when ground-visible, or to `tm_route_to_ground` when not.

### 4. Put the Front End between ground software and spacecraft

TC generated for a particular spacecraft must carry that spacecraft's SCID before it reaches `front_end_tc_port`. The Front End selects a directly visible spacecraft or configured gateway but **does not replace the destination SCID**. Returned TM is demultiplexed by origin SCID to the `cosmos_tf[SCID].tm_port` endpoint.

### 5. Replace the lab envelope with the real CryptoLib/CCSDS TF path

The paper's architecture keeps COSMOS and the cFS software bus at SPP while transfer frames exist on the link between Front End and ISL/RM. To reproduce that architecture faithfully, use Standalone CryptoLib (or the equivalent current NOS3 CryptoLib integration) to add/remove the TC/TM transfer-frame layer and SDLS. Verify the real SCID bit field and then update `frame` in the JSON or replace `css_tf.py` parsing with the mission's CryptoLib API.

A useful packet-capture acceptance test is:

```bash
sudo tcpdump -i any -nn -s0 -w constellation.pcap \
  'udp port 18010 or udp port 18011 or udp port 15012 or udp port 25112 or udp port 35112'
```

You should be able to follow one SCID 103 TC across every hop while observing that only spacecraft 103 receives the unwrapped SPP packet.

### 6. Add COSMOS separation

Start with one target/logical command path per spacecraft. Point each through the CryptoLib/TF producer and then into the common Front End TC port. For TM, configure separate receiving endpoints matching `cosmos_tf`. Once this works, a UI convenience layer can expose a `SAT101`, `SAT102`, `SAT103` target selector rather than separate operator processes.

### 7. Only then scale and add dynamics

The example uses static visibility and static routes. Once two/three satellites work reliably, replace the static booleans/next hops with visibility information from 42 or a small routing controller. Keep the same SCID-addressed packet contract so dynamic routing does not require changing cFS commands.

## Recommended minimum thesis experiment

For the immediate research goal, **two satellites plus one ground endpoint is sufficient** to demonstrate the architectural distinction:

1. SCID 101 is ground-visible.
2. SCID 102 is not ground-visible and is reachable only through SCID 101.
3. Send a benign TC addressed to 101 and show only cFS-101 receives it.
4. Send the same benign TC addressed to 102 and show the Front End sends it to 101, ISL/RM-101 forwards it, and only cFS-102 receives it.
5. Return TM from 102 through 101 and show the Front End maps it back to the SCID-102 ground endpoint.
6. Capture the UDP traffic and label each hop by SCID, packet size, inter-arrival time and route.

That is the smallest useful constellation test because it demonstrates both **individual spacecraft addressing** and **an ISL-dependent route** without requiring a seven-spacecraft swarm.

## Paper features intentionally not claimed as complete yet

- Full CCSDS TC/TM transfer-frame encoding/decoding and current CryptoLib integration.
- One VM/container per spacecraft and one MCS VM per operator.
- 42-driven dynamic visibility/routing.
- cFS-native ISL/RM application rather than the Python sidecar prototype.
- CI and Software Bus IDS probes.
- Stateful TC firewall and anomaly detector.
- Imager, input fuzzing framework, Gatewatcher integration, or CITEF cyber range.

These should be layered in after SCID-addressed two/three-satellite routing is proven.
