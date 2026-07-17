#!/usr/bin/env bash
set -euo pipefail

# Intelligent cleanup: removes ONLY packages listed in deps_manifest.json
# Default: pip packages only. To also clean apt packages installed by bootstrap, set CLEANUP_APT=1.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="$ROOT_DIR/data/runtime/state/deps_manifest.json"
CLEANUP_APT="${CLEANUP_APT:-0}"

if [[ ! -f "$MANIFEST" ]]; then
  echo "[cleanup] No manifest found: $MANIFEST"
  exit 0
fi

echo "[cleanup] Using manifest: $MANIFEST"

python3 - <<'PY'
import json, subprocess
from pathlib import Path
m = json.loads(Path("data/runtime/state/deps_manifest.json").read_text(encoding="utf-8"))
pip_pkgs = [i.get("name") for i in m.get("items", []) if i.get("kind")=="pip" and i.get("name")]
seen=set(); pip_pkgs=[p for p in pip_pkgs if not (p in seen or seen.add(p))]
if pip_pkgs:
    subprocess.run(["python3","-m","pip","uninstall","-y",*pip_pkgs], check=False)
print("PIP packages removed candidates:", pip_pkgs)
PY

if [[ "$CLEANUP_APT" == "1" ]]; then
  echo "[cleanup] CLEANUP_APT=1: removing apt packages from manifest"
  python3 - <<'PY'
import json, subprocess
from pathlib import Path
m = json.loads(Path("data/runtime/state/deps_manifest.json").read_text(encoding="utf-8"))
apt_pkgs = [i.get("name") for i in m.get("items", []) if i.get("kind")=="apt" and i.get("name")]
seen=set(); apt_pkgs=[p for p in apt_pkgs if not (p in seen or seen.add(p))]
if apt_pkgs:
    subprocess.run(["sudo","apt-get","remove","-y",*apt_pkgs], check=False)
print("APT packages removed candidates:", apt_pkgs)
PY
  sudo apt-get autoremove -y || true
else
  echo "[cleanup] apt cleanup skipped (set CLEANUP_APT=1 to enable)"
fi

echo "[cleanup] Done"
