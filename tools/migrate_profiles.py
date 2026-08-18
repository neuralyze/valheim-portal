#!/usr/bin/env python3
"""Fold the per-world profile copies into one shared store, once.

Four worlds hold four directories named ``redesign-alpha``, 2.1 GB each, with no
relationship to each other. They are *almost* the same mod set: one world excludes
VNEI, one holds a disabled package the others never had, one is missing a plugin.
Those differences are the drift the shared model exists to end.

This tool does not guess which of them is right. It prints exactly what differs and
then does what the operator names:

    plan                      what is on disk and how the copies differ
    apply --separate          every copy becomes its own profile, named <world>-<profile>
    apply --fold NAME --take WORLD
                              WORLD's copy becomes the profile NAME and every world
                              links to it; the other copies are set aside, not deleted

Preconditions that are refused rather than worked around:

* a running server - the copy being moved is what the container reads, so the
  servers are stopped first, exactly as ``deploy --apply`` already requires;
* a world with no profile, which would silently end up unlinked.

The settings of every world are recorded to the history store before anything
moves, so a fold that takes the wrong world's copy is recoverable from git rather
than from memory.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

if __package__:
    from . import portal_paths, profile_store, settings_history
else:
    import portal_paths
    import profile_store
    import settings_history

SET_ASIDE = "profiles.migrated"


class MigrationError(RuntimeError):
    """The migration was refused. The message names the condition to clear."""


def running_servers(worlds: list[Path]) -> list[str]:
    """Worlds whose container is up. Moving their profile would pull files out from under it."""
    running = []
    for world in worlds:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", f"valheim-server-{world.name}"],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip() == "true":
            running.append(world.name)
    return running


def _identifiers(manifest: dict, key: str) -> set[str]:
    values = set()
    for item in manifest.get(key, []) or []:
        identifier = item.get("identifier") if isinstance(item, dict) else item
        if identifier:
            values.add(str(identifier))
    return values


def world_profile(world: Path) -> tuple[str, Path]:
    """The profile directory a world currently uses, from its own link file."""
    name = profile_store.linked_profile(world)
    profiles = world / "mods" / "profiles"
    candidates = [d for d in sorted(profiles.iterdir()) if (d / profile_store.MANIFEST_NAME).is_file()] \
        if profiles.is_dir() else []
    if name:
        chosen = profiles / name
        if not (chosen / profile_store.MANIFEST_NAME).is_file():
            raise MigrationError(
                f"{world.name} links to {name} but {chosen} has no {profile_store.MANIFEST_NAME}"
            )
        return name, chosen
    if len(candidates) == 1:
        return candidates[0].name, candidates[0]
    if not candidates:
        raise MigrationError(f"{world.name} has no profile to migrate")
    raise MigrationError(
        f"{world.name} has {len(candidates)} profiles and no link file naming one: "
        f"{', '.join(d.name for d in candidates)}"
    )


def plan(fleet_root: Path) -> dict:
    """What exists, and every package that is not in all of the copies."""
    worlds = profile_store.worlds_in(Path(fleet_root))
    if not worlds:
        raise MigrationError(f"no worlds found under {fleet_root}")
    copies = []
    for world in worlds:
        name, directory = world_profile(world)
        manifest = json.loads((directory / profile_store.MANIFEST_NAME).read_text(encoding="utf-8-sig"))
        copies.append({
            "world": world.name,
            "profile": name,
            "path": str(directory),
            "packages": sorted(_identifiers(manifest, "packages") | _identifiers(manifest, "client_only_packages")),
            "disabled": sorted(_identifiers(manifest, "disabled_packages")),
            "excluded": sorted(_identifiers(manifest, "excluded_packages")),
        })
    differences = _differences(copies)
    return {"copies": copies, "identical": not differences, "differences": differences}


def _differences(copies: list[dict]) -> list[dict]:
    """Only what is NOT the same everywhere.

    Reporting each world's own exclusions listed 28 lines for a fleet with three
    real differences: six of those exclusions are in all four worlds, so they are
    the shared mod set, not a decision. A migration plan that overstates the work
    is a plan nobody reads.
    """
    worlds = [copy["world"] for copy in copies]
    differences = []
    for key, label in (("packages", "in"), ("disabled", "disabled_in"), ("excluded", "excluded_in")):
        everywhere = set.intersection(*[set(copy[key]) for copy in copies])
        anywhere = set.union(*[set(copy[key]) for copy in copies])
        for identifier in sorted(anywhere - everywhere):
            holders = [copy["world"] for copy in copies if identifier in copy[key]]
            differences.append({
                "package": identifier, label: holders,
                "missing_from" if key == "packages" else "not_in":
                    [world for world in worlds if world not in holders],
            })
    return differences


def _move(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise MigrationError(f"destination already exists: {destination}")
    # Same filesystem in every deployment we have, so this is a rename rather than
    # 2.1 GB of copying; shutil.move falls back to a copy if that ever changes.
    shutil.move(str(source), str(destination))


def _strip_world(manifest_file: Path, name: str) -> None:
    manifest = json.loads(manifest_file.read_text(encoding="utf-8-sig"))
    # The display name is overwritten, not preserved. Carrying "Hrafnheim Redesign"
    # onto a profile that four servers link to is how the old model's identity
    # confusion survives the migration: one profile, one name, in one place.
    manifest["profile_name"] = name
    manifest.pop("world_name", None)  # a shared profile belongs to no world
    manifest["schema_version"] = 2
    manifest_file.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def apply(fleet_root: Path, *, fold: str | None = None, take: str | None = None,
          separate: bool = False, profiles_root: Path | None = None) -> list[str]:
    """Move the copies and write the links. Returns the actions taken, in order."""
    fleet_root = Path(fleet_root)
    if bool(fold) == bool(separate):
        raise MigrationError("choose exactly one of --fold NAME or --separate")
    state = plan(fleet_root)
    worlds = profile_store.worlds_in(fleet_root)
    running = running_servers(worlds)
    if running:
        raise MigrationError(f"stop these servers first: {', '.join(running)}")
    root = profiles_root or profile_store.profiles_root(fleet_root)
    root.mkdir(parents=True, exist_ok=True)

    for world in worlds:
        # Recorded before the move: a fold that takes the wrong copy is then a git
        # question rather than a lost mod set.
        settings_history.snapshot(world, f"{world.name}: before profile migration")

    actions = []
    if separate:
        for copy in state["copies"]:
            name = f'{copy["world"]}-{copy["profile"]}'.replace("_", "-")
            profile_store._validate(name)
            _move(Path(copy["path"]), root / name)
            _strip_world(root / name / profile_store.MANIFEST_NAME, name)
            profile_store.link(fleet_root / copy["world"], name, root)
            actions.append(f'{copy["world"]}: {copy["profile"]} -> {name} (linked)')
        return actions

    profile_store._validate(fold)
    chosen = next((copy for copy in state["copies"] if copy["world"] == take), None)
    if not chosen:
        raise MigrationError(
            f"--take names a world with no copy: {take!r}; "
            f"choose from {', '.join(c['world'] for c in state['copies'])}"
        )
    _move(Path(chosen["path"]), root / fold)
    _strip_world(root / fold / profile_store.MANIFEST_NAME, fold)
    actions.append(f'{take}: {chosen["profile"]} -> {fold} (kept)')
    for copy in state["copies"]:
        if copy["world"] != take:
            # Set aside rather than deleted: this is the only copy of that world's
            # mod set, and the whole reason for the fold is that they differ.
            aside = fleet_root / copy["world"] / "mods" / SET_ASIDE / copy["profile"]
            _move(Path(copy["path"]), aside)
            actions.append(f'{copy["world"]}: {copy["profile"]} set aside at {aside}')
    for copy in state["copies"]:
        profile_store.link(fleet_root / copy["world"], fold, root)
        actions.append(f'{copy["world"]}: linked to {fold}')
    return actions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fleet-root", type=Path)
    parser.add_argument("--profiles-root", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("plan", help="print the copies and how they differ")
    show.add_argument("--json", action="store_true")
    run = sub.add_parser("apply", help="move the copies and link the servers")
    run.add_argument("--fold", metavar="NAME", help="one shared profile with this name")
    run.add_argument("--take", metavar="WORLD", help="whose copy becomes that profile")
    run.add_argument("--separate", action="store_true", help="one profile per world instead")
    args = parser.parse_args(argv)

    fleet = args.fleet_root or portal_paths.world_root()
    if args.command == "plan":
        state = plan(fleet)
        if args.json:
            print(json.dumps(state, separators=(",", ":")))
            return 0
        for copy in state["copies"]:
            print(f'{copy["world"]:12s} {copy["profile"]:20s} {len(copy["packages"])} packages')
        if state["identical"]:
            print("the copies hold the same packages; a fold loses nothing")
            return 0
        print("\ndifferences a fold has to decide:")
        for difference in state["differences"]:
            print("  " + json.dumps(difference, separators=(",", ": ")))
        return 0
    if args.fold and not args.take:
        raise MigrationError("--fold NAME also needs --take WORLD naming whose copy to keep")
    for action in apply(fleet, fold=args.fold, take=args.take, separate=args.separate,
                        profiles_root=args.profiles_root):
        print(action)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except portal_paths.ConfigurationError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(portal_paths.EX_CONFIG)
    except (MigrationError, profile_store.ProfileError, settings_history.HistoryError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
