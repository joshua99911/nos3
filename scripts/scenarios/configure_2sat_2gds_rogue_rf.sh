#!/usr/bin/env bash
set -euo pipefail

# Configure the source simulator definition used by the dual-GDS build so that
# 42 controls whether commands/telemetry may pass based on RF occultation.
# Run this from the NOS3 repository root before `make config`/`make`.

SIM_CFG="cfg/sims/nos3-simulator-multipleGDS.xml"
MISSION_CFG="cfg/nos3-mission.xml"

if [[ ! -f "$SIM_CFG" || ! -f "$MISSION_CFG" ]]; then
    echo "ERROR: run this script from the NOS3 repository root." >&2
    exit 1
fi

python3 - <<'PY'
from pathlib import Path
import xml.etree.ElementTree as ET

mission = Path("cfg/nos3-mission.xml")
tree = ET.parse(mission)
root = tree.getroot()
required = {
    "gsw": "multiple",
    "fsw": "cfs",
    "scenario": "STF1",
    "number-spacecraft": "2",
}
for tag, value in required.items():
    node = root.find(tag)
    if node is None:
        raise SystemExit(f"Missing <{tag}> in {mission}")
    node.text = value

# Keep exactly the first two spacecraft definitions in the scenario mission.
for child in list(root):
    if child.tag.startswith("sc-") and child.tag.endswith("-cfg") and child.tag not in {"sc-1-cfg", "sc-2-cfg"}:
        root.remove(child)
for idx in (1, 2):
    tag = f"sc-{idx}-cfg"
    node = root.find(tag)
    if node is None:
        node = ET.SubElement(root, tag)
    node.text = "spacecraft/sc-mission-config.xml"

ET.indent(tree, space="    ")
tree.write(mission, encoding="unicode")
with mission.open("a") as fp:
    fp.write("\n")

sim = Path("cfg/sims/nos3-simulator-multipleGDS.xml")
text = sim.read_text()
replacements = {
    "<uplink-close-criteria>none</uplink-close-criteria>": "<uplink-close-criteria>occulted</uplink-close-criteria>",
    "<downlink-close-criteria>none</downlink-close-criteria>": "<downlink-close-criteria>occulted</downlink-close-criteria>",
    "<uplink-delay-on>false</uplink-delay-on>": "<uplink-delay-on>true</uplink-delay-on>",
    "<downlink-delay-on>false</downlink-delay-on>": "<downlink-delay-on>true</downlink-delay-on>",
}
for old, new in replacements.items():
    text = text.replace(old, new)
sim.write_text(text)

print("Configured:")
print("  2 spacecraft")
print("  multiple GDS (COSMOS + YAMCS)")
print("  42 RF occultation gating on uplink/downlink")
print("  RF propagation delay enabled")
PY

echo
echo "Next:"
echo "  make uninstall"
echo "  git submodule sync --recursive"
echo "  git submodule update --init --recursive"
echo "  make prep"
echo "  make config"
echo "  make -j\"$(nproc)\""
echo "  make launch"
