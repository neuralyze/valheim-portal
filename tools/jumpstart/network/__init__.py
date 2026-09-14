"""World-network tooling: per-tier installation sets, a portal graph, and a road router.

Owned by the WorldNetwork work. Reads, and never writes, the siblings' trees:
  tools/jumpstart/library/    -- blueprint manifest (name + sha256; no bodies)
  tools/jumpstart/blueprints/ -- site_finder.load_patches, run_patchscan.sh, inventory.json
  tools/jumpstart/presets/    -- the tier ladder and station levels
  tools/seedscan/             -- biome/height grids and the ZoneSystem location dump

Nothing here places anything in a live world. `plan.py` emits a plan; putting it in
the ground is a separate, operator-approved step from the admin client seat.
"""

from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
REPO = JUMPSTART.parent.parent
DATA = HERE / "data"

__all__ = ["HERE", "JUMPSTART", "REPO", "DATA"]
