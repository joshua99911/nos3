#!/bin/bash -i
set -e
#
# Two-spacecraft + two-GDS launcher.
#
# The upstream multiple-GDS launcher is still a single-spacecraft launcher,
# while the upstream multiple-spacecraft launcher has the correct shared-42
# topology but only launches one GDS/CryptoLib path.  This wrapper derives a
# two-spacecraft, dual-GDS launcher from the upstream multiple-spacecraft
# launcher at runtime so the two capabilities are combined without duplicating
# the large upstream launch script.
#
# This file is copied to cfg/build/launch.sh by configure.py when <gsw>multiple</gsw>.

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source "$SCRIPT_DIR/../../scripts/env.sh"

BASE_LAUNCH="$BASE_DIR/scripts/fsw/fsw_cfs_launch_multiple_sc.sh"
# The upstream launch script is designed to run from cfg/build after configure.py
# copies it there.  Keep the generated launcher in that directory so its
# BASH_SOURCE-relative env.sh and cfg/build/fsw paths remain valid.  Generating
# it in /tmp makes the upstream script incorrectly resolve /scripts/env.sh.
GENERATED_LAUNCH="$BASE_DIR/cfg/build/launch_2sat_2gds.generated.sh"

if [ ! -f "$BASE_LAUNCH" ]; then
    echo "ERROR: multiple-spacecraft launcher not found: $BASE_LAUNCH" >&2
    exit 1
fi

python3 - "$BASE_LAUNCH" "$GENERATED_LAUNCH" <<'PY'
from pathlib import Path
import sys

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
text = src.read_text()


def replace_once(old, new, description):
    global text
    if old not in text:
        raise SystemExit("ERROR: could not patch multiple-spacecraft launcher: " + description)
    text = text.replace(old, new, 1)

# Launch both ground systems on nos3-core.
replace_once(
    'source $BASE_DIR/cfg/build/gsw_launch.sh\n',
    'source $BASE_DIR/cfg/build/gsw_launch.sh\nsource $BASE_DIR/cfg/build/gsw_launch2.sh\n',
    'second GDS launch',
)

# The reference launcher is a 3-spacecraft demonstration.  This scenario is 2 SC.
replace_once('export SATNUM=3', 'export SATNUM=2', 'SATNUM')
replace_once(
    'echo "sc03 - Create spacecraft network..."\n$DNETWORK create "nos3-sc03" --subnet=192.168.3.0/24\n',
    '',
    'remove sc03 network',
)

# 42 and the NOS time driver are shared by both active spacecraft.
text = text.replace(
    '--network nos3-core --network nos3-sc01 --network nos3-sc02 --network nos3-sc03',
    '--network nos3-core --network nos3-sc01 --network nos3-sc02',
)
replace_once(
    'echo "Closing the ring: connecting sc01-radio-sim to nos3-sc03 as \'next-radio\'"',
    'echo "Closing the two-spacecraft ring: connecting sc01-radio-sim to nos3-sc02 as \'next-radio\'"',
    'two-spacecraft ring message',
)
replace_once(
    'docker network connect --alias "next-radio" "nos3-sc03" sc01-radio-sim',
    'docker network connect --alias "next-radio" "nos3-sc02" sc01-radio-sim',
    'two-spacecraft ring connection',
)

# Attach both ground systems to each spacecraft network.
gsw1 = '$DNETWORK connect  $SC_NETNAME "${GSW:-cosmos-openc3-operator-1}" --alias cosmos --alias active-gs --ip 192.168.$i.100'
replace_once(
    gsw1,
    gsw1 + '\n'
    '    echo $SC_NUM " - Connect YAMCS GDS to spacecraft network..."\n'
    '    $DNETWORK connect $SC_NETNAME cosmos-openc3-operator-2 --alias yamcs --alias active-gs2 --ip 192.168.$i.101',
    'second GDS spacecraft connection',
)

# Generic radio must expose the second GDS path when launched in this scenario.
replace_once(
    '        $DFLAGS -v $SIM_DIR:$SIM_DIR \\\n        --name $SC_NUM"-radio-sim"',
    '        $DFLAGS -e "TCP_GROUND=0" -e "MULTI_GDS=1" -v $SIM_DIR:$SIM_DIR \\\n        --name $SC_NUM"-radio-sim"',
    'multi-GDS radio environment',
)

# Replace the single CryptoLib process with the two trusted GDS CryptoLib paths.
crypto = 'gnome-terminal --tab --title=$SC_NUM" - CryptoLib GSW" -- $DFLAGS -v $BASE_DIR:$BASE_DIR --name $SC_NUM"-cryptolib-gsw"  -h cryptolib --network $SC_NETNAME --network-alias=cryptolib -w $BASE_DIR/gsw/build $DBOX ./support/standalone'
replace_once(
    crypto,
    'gnome-terminal --tab --title=$SC_NUM" - CryptoLib GSW" -- $DFLAGS -e "STANDALONE_TCP=0" -e "GSWALIAS=cosmos" -e "CRYPTO_HOST=cryptolib" -v $BASE_DIR:$BASE_DIR --name $SC_NUM"-cryptolib-gsw" -h cryptolib --network $SC_NETNAME --network-alias=cryptolib -w $BASE_DIR/gsw/build $DBOX ./support/standalone\n'
    '    gnome-terminal --tab --title=$SC_NUM" - CryptoLib GSW2" -- $DFLAGS -e "STANDALONE_TCP=0" -e "GSWALIAS=yamcs" -e "CRYPTO_HOST=cryptolib2" -v $BASE_DIR:$BASE_DIR --name $SC_NUM"-cryptolib-gsw2" -h cryptolib2 --network $SC_NETNAME --network-alias=cryptolib2 -w $BASE_DIR/gsw/build $DBOX ./support/standalone',
    'dual CryptoLib launch',
)

dst.write_text(text)
PY

chmod +x "$GENERATED_LAUNCH"
echo "Launching combined 2-spacecraft / 2-GDS topology..."
source "$GENERATED_LAUNCH"
