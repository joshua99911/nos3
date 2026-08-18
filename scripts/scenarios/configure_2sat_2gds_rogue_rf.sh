#!/usr/bin/env bash
set -euo pipefail

# Configure the combined two-spacecraft / two-GDS / 42 RF scenario.
# Run from the NOS3 repository root before make config.

MISSION_CFG="cfg/nos3-mission.xml"
SIM_MULTI_GDS="cfg/sims/nos3-simulator-multipleGDS.xml"
SIM_SC1="cfg/sims/sc-1-nos3-simulator.xml"
SIM_SC2="cfg/sims/sc-2-nos3-simulator.xml"

for f in "$MISSION_CFG" "$SIM_MULTI_GDS" "$SIM_SC1" "$SIM_SC2" \
         cfg/InOut/Inp_Sim_STF1.txt cfg/InOut/Inp_IPC.txt \
         cfg/InOut/Inp_CommLink.txt cfg/InOut/Orb_LEO.txt; do
    if [[ ! -f "$f" ]]; then
        echo "ERROR: missing $f; run this script from the NOS3 repository root." >&2
        exit 1
    fi
done

python3 - <<'PY'
from pathlib import Path
import re
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# Mission: two cFS spacecraft, COSMOS + YAMCS.
# ---------------------------------------------------------------------------
mission = Path("cfg/nos3-mission.xml")
tree = ET.parse(str(mission))
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
        raise SystemExit("Missing <{}> in {}".format(tag, mission))
    node.text = value

for child in list(root):
    if (child.tag.startswith("sc-") and child.tag.endswith("-cfg") and
            child.tag not in {"sc-1-cfg", "sc-2-cfg"}):
        root.remove(child)
for idx in (1, 2):
    tag = "sc-{}-cfg".format(idx)
    node = root.find(tag)
    if node is None:
        node = ET.SubElement(root, tag)
    node.text = "spacecraft/sc-mission-config.xml"

if hasattr(ET, "indent"):
    ET.indent(tree, space="    ")
tree.write(str(mission), encoding="unicode")
with mission.open("a") as fp:
    fp.write("\n")

# ---------------------------------------------------------------------------
# 42 orbits: two active LEO reference orbits, separated by 120 degrees.
# A third inactive slot is retained because the checkpoint's IPC file contains
# SC[2] prefixes. Keeping the slot allocated avoids invalid 42 prefix indices,
# while FALSE ensures there are only two active/displayed spacecraft.
# ---------------------------------------------------------------------------
def make_orbit(dst_name, anomaly):
    src = Path("cfg/InOut/Orb_LEO.txt")
    lines = src.read_text().splitlines(True)
    found = False
    for i, line in enumerate(lines):
        if "True Anomaly (deg)" in line:
            lines[i] = "{:<30} !  True Anomaly (deg)\n".format("{:.1f}".format(anomaly))
            found = True
            break
    if not found:
        raise SystemExit("Could not find True Anomaly in {}".format(src))
    Path("cfg/InOut/" + dst_name).write_text("".join(lines))

make_orbit("Orb_LEO_SC2.txt", 147.0)
make_orbit("Orb_LEO_SC3_DISABLED.txt", 267.0)

sim_path = Path("cfg/InOut/Inp_Sim_STF1.txt")
lines = sim_path.read_text().splitlines(True)

def find_line(needle, start=0):
    for i in range(start, len(lines)):
        if needle in lines[i]:
            return i
    raise SystemExit("Could not find '{}' in {}".format(needle, sim_path))

ref_marker = find_line("Reference Orbits")
sc_marker = find_line("Spacecraft", ref_marker + 1)
ref_lines = [
    "3                               !  Number of Reference Orbits\n",
    "TRUE   Orb_LEO.txt              !  Input file name for Orb 0\n",
    "TRUE   Orb_LEO_SC2.txt          !  Input file name for Orb 1\n",
    "TRUE   Orb_LEO_SC3_DISABLED.txt !  Input file name for Orb 2 (inactive SC slot)\n",
]
lines = lines[:ref_marker + 1] + ref_lines + lines[sc_marker:]

sc_marker = find_line("Spacecraft", ref_marker + 1)
env_marker = find_line("Environment", sc_marker + 1)
sc_lines = [
    "3                               !  Number of Spacecraft slots (2 active)\n",
    "TRUE  0 SC_NOS3.txt             !  SC 0 / sc01\n",
    "TRUE  1 SC_NOS3.txt             !  SC 1 / sc02\n",
    "FALSE 2 SC_NOS3.txt             !  SC 2 slot retained for IPC compatibility\n",
]
lines = lines[:sc_marker + 1] + sc_lines + lines[env_marker:]
for i, line in enumerate(lines):
    if "Graphics Front End?" in line:
        lines[i] = "TRUE                            !  Graphics Front End?\n"
        break
sim_path.write_text("".join(lines))

# ---------------------------------------------------------------------------
# 42 RF links: links 0/1 are sc01; add links 2/3 for sc02.
# Terminal IDs follow 42 uplink/downlink convention used by the RF PR:
# ground terminal 0 <-> spacecraft terminal 0/1.
# ---------------------------------------------------------------------------
comm_path = Path("cfg/InOut/Inp_CommLink.txt")
comm_lines = comm_path.read_text().splitlines(True)
link_markers = [i for i, line in enumerate(comm_lines) if "Link " in line and "====" in line]
if len(link_markers) < 2:
    raise SystemExit("Expected at least two links in {}".format(comm_path))
header = comm_lines[:link_markers[0]]
block0 = comm_lines[link_markers[0]:link_markers[1]]
block1 = comm_lines[link_markers[1]:]

def clone_link(block, number, description, tx_terminal, rx_terminal, seed):
    out = list(block)
    out[0] = "===============================  Link {}  ================================\n".format(number)
    tx_done = False
    rx_done = False
    for i, line in enumerate(out):
        if "! Description" in line:
            out[i] = "{:<21} ! Description\n".format(description)
        elif "Tx Terminal ID, Body" in line:
            out[i] = "{}  0                 ! Tx Terminal ID, Body\n".format(tx_terminal)
            tx_done = True
        elif "Rx Terminal ID, Body" in line:
            out[i] = "{}  0                 ! Rx Terminal ID, Body\n".format(rx_terminal)
            rx_done = True
        elif "Atmo Loss Ran Walk" in line:
            out[i] = "0.1  10.0   {:<3}      ! Atmo Loss Ran Walk std (dB), Corr Time (sec), Seed\n".format(seed)
    if not (tx_done and rx_done):
        raise SystemExit("Could not identify terminal lines while cloning RF link")
    return out

for i, line in enumerate(header):
    if "Number of Links" in line:
        header[i] = "4                    ! Number of Links\n"
        break
block0 = clone_link(block0, 0, "SC01 S-Band Uplink", 0, 0, 100)
block1 = clone_link(block1, 1, "SC01 X-Band Downlink", 0, 0, 101)
block2 = clone_link(block0, 2, "SC02 S-Band Uplink", 0, 1, 102)
block3 = clone_link(block1, 3, "SC02 X-Band Downlink", 1, 0, 103)
comm_path.write_text("".join(header + block0 + block1 + block2 + block3))

# ---------------------------------------------------------------------------
# 42 IPC: give sc02's radio its own CommLink socket.  The checkpoint already
# contains sensor IPC groups for SC[0], SC[1], and the disabled SC[2] slot.
# ---------------------------------------------------------------------------
ipc_path = Path("cfg/InOut/Inp_IPC.txt")
ipc = ipc_path.read_text()
radio2_marker = '"Radio_SC02.42"'
if radio2_marker not in ipc:
    m = re.search(r'^(\d+)(\s+! Number of Sockets)', ipc, flags=re.M)
    if not m:
        raise SystemExit("Could not find socket count in {}".format(ipc_path))
    new_count = int(m.group(1)) + 1
    ipc = ipc[:m.start()] + str(new_count) + m.group(2) + ipc[m.end():]
    ipc += '''\n**********************************  Radio IPC SC02  *****************************\nTX                                      ! IPC Mode (OFF,TX,RX,TXRX,ACS,WRITEFILE,READFILE)\n"Radio_SC02.42"                         ! File name for WRITE or READ\nSERVER                                  ! Socket Role (SERVER,CLIENT,GMSEC_CLIENT)\nfortytwo       5286                     ! Server Host Name, Port\nFALSE                                   ! Allow Blocking (i.e. wait on RX)\nFALSE                                   ! Echo to stdout\n1                                       ! Number of TX prefixes\n"CommLink"                              ! Prefix 0\n'''
ipc_path.write_text(ipc)

# ---------------------------------------------------------------------------
# Per-spacecraft radio simulators: add GDS2 and use 42 RF state.  These are the
# configs used by the multiple-spacecraft launcher; patching only
# nos3-simulator-multipleGDS.xml is not sufficient.
# ---------------------------------------------------------------------------
def patch_spacecraft_radio(path, port, uplink, downlink):
    p = Path(path)
    text = p.read_text()
    name_pos = text.find("<name>generic-radio-sim</name>")
    if name_pos < 0:
        raise SystemExit("No generic-radio-sim in {}".format(p))
    start = text.rfind("<simulator>", 0, name_pos)
    end = text.find("</simulator>", name_pos)
    if start < 0 or end < 0:
        raise SystemExit("Could not isolate radio simulator in {}".format(p))
    end += len("</simulator>")
    block = text[start:end]

    if "<name>gsw2</name>" not in block:
        m = re.search(r'(\s*<connection>\s*<name>gsw</name>.*?</connection>)', block, flags=re.S)
        if not m:
            raise SystemExit("Could not find GSW connection in {}".format(p))
        indent = "                    "
        gsw2 = '''\n{0}<connection>\n{0}    <name>gsw2</name>\n{0}    <ip>cryptolib2</ip>\n{0}    <cmd-port>8010</cmd-port>\n{0}    <tlm-port>8013</tlm-port>\n{0}</connection>'''.format(indent)
        block = block[:m.end()] + gsw2 + block[m.end():]

    provider = '''<data-provider>\n                    <type>GENERIC_RADIO_42_PROVIDER</type>\n                    <hostname>fortytwo</hostname>\n                    <port>{}</port>\n                    <max-connection-attempts>30</max-connection-attempts>\n                    <retry-wait-seconds>5</retry-wait-seconds>\n                    <comm-uplink>{}</comm-uplink>\n                    <uplink-close-criteria>occulted</uplink-close-criteria>\n                    <uplink-cnr-limit>15</uplink-cnr-limit>\n                    <uplink-delay-on>true</uplink-delay-on>\n                    <comm-downlink>{}</comm-downlink>\n                    <downlink-close-criteria>occulted</downlink-close-criteria>\n                    <downlink-cnr-limit>15</downlink-cnr-limit>\n                    <downlink-delay-on>true</downlink-delay-on>\n                </data-provider>'''.format(port, uplink, downlink)

    provider_pattern = re.compile(
        r'<data-provider>\s*<type>GENERIC_RADIO(?:_42)?_PROVIDER</type>.*?</data-provider>',
        flags=re.S,
    )
    block, count = provider_pattern.subn(provider, block, count=1)
    if count != 1:
        raise SystemExit("Could not replace radio data provider in {}".format(p))

    text = text[:start] + block + text[end:]
    p.write_text(text)

patch_spacecraft_radio("cfg/sims/sc-1-nos3-simulator.xml", 4286, 0, 1)
patch_spacecraft_radio("cfg/sims/sc-2-nos3-simulator.xml", 5286, 2, 3)

# Retain the same RF behavior in the generic multi-GDS config as a fallback.
sim = Path("cfg/sims/nos3-simulator-multipleGDS.xml")
text = sim.read_text()
for old, new in {
    "<uplink-close-criteria>none</uplink-close-criteria>": "<uplink-close-criteria>occulted</uplink-close-criteria>",
    "<downlink-close-criteria>none</downlink-close-criteria>": "<downlink-close-criteria>occulted</downlink-close-criteria>",
    "<uplink-delay-on>false</uplink-delay-on>": "<uplink-delay-on>true</uplink-delay-on>",
    "<downlink-delay-on>false</downlink-delay-on>": "<downlink-delay-on>true</downlink-delay-on>",
}.items():
    text = text.replace(old, new)
sim.write_text(text)

print("Configured:")
print("  2 active spacecraft (sc01 + sc02)")
print("  shared 42 process with two LEO reference orbits")
print("  COSMOS rogue/trusted GDS + YAMCS legitimate GDS")
print("  per-spacecraft CryptoLib paths")
print("  42 RF occultation gating and propagation delay")
print("  sc01 RF links: 0/1 on 42 port 4286")
print("  sc02 RF links: 2/3 on 42 port 5286")
PY

echo
echo "Next (after pulling this branch):"
echo "  make stop 2>/dev/null || true"
echo "  make uninstall"
echo "  git submodule sync --recursive"
echo "  git submodule update --init --recursive"
echo "  make prep"
echo "  make config"
echo "  make -j\"$(nproc)\""
echo "  make launch"
