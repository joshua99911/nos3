# Scenario - Two Spacecraft, Dual Ground Systems, Rogue GDS and RF In-View Gating

This research scenario combines the NOS3 multiple-spacecraft, multiple-GDS, rogue-ground-station, and 42 RF in-view/delay capabilities into a reduced two-spacecraft constellation cell.

## Architecture

The scenario contains:

- `sc01`: first cFS spacecraft with the standard mission component set and generic radio.
- `sc02`: second cFS spacecraft with the standard mission component set and generic radio.
- YAMCS: legitimate/blue-team ground data system.
- COSMOS: second trusted GDS used as the compromised/rogue/red-team ground station.
- CryptoLib paths for both ground systems.
- 42 RF geometry and radio link state, with uplink/downlink closed while occulted and propagation delay enabled.
- The existing multiple-spacecraft proximity-radio path between spacecraft.

Both GDS instances deliberately possess valid cryptographic material. The cyber condition being studied is therefore a compromised trusted ground endpoint rather than an unauthenticated external transmitter.

## Why two spacecraft?

Two spacecraft are sufficient to retain the key constellation property needed by this experiment: commands, telemetry and radio behaviour can be observed per spacecraft while retaining an inter-spacecraft/proximity path. This substantially reduces the process/container count compared with the three-spacecraft demonstration while preserving a minimal constellation research cell.

## One-time checkout and preparation

From a terminal:

```bash
git fetch origin
git checkout feature/2sat-2gds-rogue-rf

make uninstall
git submodule sync --recursive
git submodule update --init --recursive

bash scripts/scenarios/configure_2sat_2gds_rogue_rf.sh
make prep
make config
make -j"$(nproc)"
```

The setup helper makes the experiment explicit and repeatable. It verifies/sets:

```text
gsw                 = multiple
fsw                 = cfs
scenario            = STF1
number-spacecraft   = 2
uplink-close        = occulted
downlink-close      = occulted
uplink-delay        = true
downlink-delay      = true
```

## Launch

```bash
make launch
```

Expected major interfaces include:

- `sc01 - NOS3 Flight Software`
- `sc02 - NOS3 Flight Software`
- CryptoLib ground-side interfaces for the two GDS paths
- COSMOS
- YAMCS, available in the NOS3 environment at `http://localhost:8090`
- 42 / NOS Time Driver / simulator windows

If switching into this branch from a materially different NOS3 branch, do not skip `make uninstall`, `make prep`, or the recursive submodule update.

## Phase 1 - Verify two spacecraft

Before testing the cyber condition, establish a clean baseline.

1. Confirm that both `sc01` and `sc02` flight-software windows are running.
2. In COSMOS, open the Command and Telemetry Server and verify telemetry is incrementing for both spacecraft targets.
3. Send an ordinary NOOP to `sc01` and then separately to `sc02` using their spacecraft-specific targets.
4. Verify each command appears only in the intended spacecraft flight-software terminal.
5. Optionally exercise the existing proximity-forwarding command from spacecraft 1 and verify spacecraft 2 receives the forwarded command.

This proves that spacecraft identity and the two-satellite topology are working before adding the ground-system and RF variables.

## Phase 2 - Verify both ground systems

YAMCS is the legitimate GDS and COSMOS is the second, potentially compromised, trusted GDS.

1. Open COSMOS and its Command and Telemetry Server.
2. Open YAMCS at `http://localhost:8090` and select the `nos3` instance.
3. In YAMCS, navigate to **Commanding -> Send a command** and send a benign command such as a NOOP to `sc01` while the link is in view.
4. Compare radio/telemetry counters between COSMOS and YAMCS to establish that both receive the spacecraft telemetry stream.
5. Repeat the benign command/telemetry check for `sc02`.

## Phase 3 - Verify RF occultation and delay

The important difference from the original rogue-ground-station walkthrough is that the radio is not forced permanently open.

The generic radio 42 provider is configured with:

```xml
<uplink-close-criteria>occulted</uplink-close-criteria>
<uplink-delay-on>true</uplink-delay-on>
<downlink-close-criteria>occulted</downlink-close-criteria>
<downlink-delay-on>true</downlink-delay-on>
```

At the STF1 scenario start, the reference RF test places the ground station out of view of the spacecraft. Commands sent during an occulted interval should therefore not reach the flight software. When 42 reports the link in view, commands should pass again. The exact transition for each spacecraft can differ because the two spacecraft occupy different constellation positions; use the 42/radio status rather than assuming both satellites acquire the ground station simultaneously.

Recommended observation sequence:

1. Immediately after startup, send a benign NOOP to `sc01` from the legitimate GDS.
2. Confirm it is not delivered while the ground-to-space RF link is occulted.
3. Watch the 42/radio link state until the uplink becomes available.
4. Send the same NOOP again and verify receipt in `sc01`.
5. Repeat for `sc02` and record the different access interval if present.
6. Compare command send time and receipt time to validate the delay path.

This creates useful benign dataset labels such as:

```text
spacecraft_id
ground_system_id
command_sent
command_received
rf_in_view
rf_occulted
uplink_delay
downlink_delay
```

## Phase 4 - Rogue trusted-ground-station experiment

Only perform the following after the benign dual-GDS and RF baseline is verified.

The rogue condition follows the NASA-ITC trusted-ground-station scenario: COSMOS represents a compromised station that still possesses valid cryptographic keys, while YAMCS represents the legitimate operator.

### Establish the shared cryptographic context

In the two CryptoLib ground interfaces for `sc01`, set the same VCID used by the upstream rogue-GDS demonstration:

```text
vcid 2
```

Do this for both ground paths. Confirm ordinary authenticated commands from each GDS work while the RF link is in view before continuing.

### Generate a controlled command burst from the rogue COSMOS GDS

In the COSMOS Script Runner, use the upstream demonstration's small benign-command burst against the test spacecraft:

```ruby
20.times do
  cmd("SAMPLE_RADIO SAMPLE_NOOP_CC")
  wait(0.01)
end
```

Run it only while the selected spacecraft is in view. If the link is occulted, 42 should prevent delivery and the burst will not reproduce the intended trusted-GDS sequence-counter condition.

### Observe the legitimate GDS

After the controlled COSMOS burst:

1. In YAMCS, send the equivalent benign NOOP to the same spacecraft.
2. Observe the spacecraft and CryptoLib output.
3. The upstream rogue scenario demonstrates the legitimate path being rejected with `Crypto_TC_ProcessSecurity returned error -23` after the competing trusted GDS advances the shared cryptographic state.
4. Record whether telemetry remains visible while commanding is rejected.
5. Repeat the experiment against `sc02` as a separate run rather than attacking both spacecraft simultaneously. This provides cleaner per-spacecraft measurements.

## Suggested experimental runs

For thesis/data-collection use, keep runs separate and label them explicitly:

| Run | Spacecraft | GDS condition | RF condition | Expected purpose |
|---|---|---|---|---|
| B1 | sc01 | legitimate only | in view | benign command baseline |
| B2 | sc01 | legitimate only | occulted | physically blocked command baseline |
| B3 | sc02 | legitimate only | in view/occulted | second-satellite access-window baseline |
| M1 | sc01 | legitimate + rogue | in view | trusted rogue command burst / lockout observation |
| M2 | sc02 | legitimate + rogue | in view | repeatability on second spacecraft |
| R1 | sc01 | rogue attempts burst | occulted | verify physical RF gating prevents delivery |

This separation is useful because a cryptographic rejection and a physically unavailable RF path should not be collapsed into the same label.

## Stop and relaunch

Stop the scenario with:

```bash
make stop
```

For a clean experiment rerun after configuration changes:

```bash
make stop
make clean
bash scripts/scenarios/configure_2sat_2gds_rogue_rf.sh
make config
make -j"$(nproc)"
make launch
```

If submodule revisions were changed, use `make uninstall`, resynchronise/update submodules, then `make prep` again.

## Relationship to upstream work

This branch is based on the NASA-ITC multiple-spacecraft checkpoint and combines concepts introduced by:

- NOS3 multiple-spacecraft scenario / PR 855.
- NOS3 multiple-GDS capability / PR 851.
- NASA-ITC rogue ground station scenario associated with commit `32304d6077bca78d5fcfde2e125f7f992c45308f`.
- NOS3 42 RF in-view and delay support / PR 852.

The deliberate change for this research branch is reduction to two spacecraft and restoration of physical RF gating in the rogue-ground-station configuration.
