#!/usr/bin/env python3
"""List the mod profiles one world may run, as JSON.

Profiles are shared, so every profile is available to every world: the ``world`` field
is the world that was asked about, not a property of the profile. The shape is
unchanged because the portal filters on it - it checks ``world`` matches the world it
queried before offering a profile in the admin UI.
"""
from __future__ import annotations

import json
import re
import sys
if __package__:
    from . import portal_paths, profile_store
else:
    import portal_paths
    import profile_store

VALID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def main() -> int:
    if len(sys.argv) != 2 or not VALID.fullmatch(sys.argv[1]):
        raise ValueError("invalid world")
    root = portal_paths.world_root()
    world = (root / sys.argv[1]).resolve()
    if root not in world.parents or not world.is_dir():
        raise ValueError("world unavailable")
    store = profile_store.profiles_root(root)
    linked = profile_store.linked_profile(world)
    result = []
    for name in profile_store.profile_names(store):
        manifest_path = store / name / profile_store.MANIFEST_NAME
        try:
            manifest = json.loads(manifest_path.read_text())
            packages = [*manifest.get("packages", []), *manifest.get("client_only_packages", [])]
            result.append({
                "world": sys.argv[1], "profile": name,
                "name": manifest.get("profile_name", name),
                "packages": len(packages), "custom_packages": len(manifest.get("custom_packages", [])),
                "disabled_packages": len(manifest.get("disabled_packages", [])),
                # Which one this world runs today. Several worlds can name the same profile,
                # so this is the only per-world fact in the row.
                "linked": name == linked,
            })
        except (OSError, ValueError, TypeError):
            continue
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except portal_paths.ConfigurationError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(portal_paths.EX_CONFIG)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
