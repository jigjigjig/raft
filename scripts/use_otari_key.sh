#!/bin/sh
# Put one Otari key into .env for every role and switch Raft to live mode.
#   sh scripts/use_otari_key.sh sk-your-key
set -e
[ -n "$1" ] || { echo "usage: sh scripts/use_otari_key.sh <otari-api-key>" >&2; exit 2; }
KEY="$1"
python3 - "$KEY" <<'PY'
import pathlib, sys
key = sys.argv[1]
path = pathlib.Path(".env")
lines = []
for line in path.read_text().splitlines():
    if line.startswith("RAFT_OTARI_MODE="):
        line = "RAFT_OTARI_MODE=live"
    elif line.startswith("OTARI_") and line.endswith("_API_KEY="):
        line = f"{line}{key}"
    lines.append(line)
path.write_text("\n".join(lines) + "\n")
print("updated .env: live mode, key set for all roles")
PY
