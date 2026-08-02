#!/usr/bin/env python3
"""List controlled mod profiles for one configured world as JSON."""
from __future__ import annotations

import json
import re
import sys
if __package__:
    from . import portal_paths
else:
    import portal_paths

VALID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def main() -> int:
    if len(sys.argv) != 2 or not VALID.fullmatch(sys.argv[1]):
        raise ValueError("invalid world")
    root = portal_paths.world_root()
    world = (root / sys.argv[1]).resolve()
    if root not in world.parents or not world.is_dir():
        raise ValueError("world unavailable")
    profiles_root = (world / "mods" / "profiles").resolve()
    result = []
    for directory in sorted(profiles_root.iterdir() if profiles_root.is_dir() else []):
        manifest_path = directory / "profile-manifest.json"
        if directory.is_symlink() or not directory.is_dir() or not VALID.fullmatch(directory.name) or not manifest_path.is_file() or manifest_path.is_symlink():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
            if manifest.get("world_name") != sys.argv[1]:
                continue
            packages = [*manifest.get("packages", []), *manifest.get("client_only_packages", [])]
            result.append({
                "world": sys.argv[1], "profile": directory.name,
                "name": manifest.get("profile_name", directory.name),
                "packages": len(packages), "custom_packages": len(manifest.get("custom_packages", [])),
                "disabled_packages": len(manifest.get("disabled_packages", [])),
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
