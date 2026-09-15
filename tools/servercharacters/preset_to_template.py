#!/usr/bin/env python3
"""Render a tools/jumpstart tier preset as a ServerCharacters CharacterTemplate.yml.

ServerCharacters reads `CharacterTemplate.yml` from its own plugin directory on the
SERVER, watches it for changes, and ServerSyncs the text to every client. A client
creating a NEW character on this server wipes the starting inventory and applies the
template: skills first, then items, then a spawn point. No console command, no RCON
`give`, nothing dropped on the ground for DropCleaner to eat - the character simply
begins at that tier.

What the format can express (ServerCharacters/PlayerTemplate.cs, applied in
ClientSide.cs `acquireCharacterFromTemplate`):

    skills:            map of skill name -> float, applied with Skills.CheatRaiseSkill.
                       Names are Skills.SkillType members, case-insensitive; skills
                       registered by other mods (Foraging, Lumberjacking, Mining,
                       Blacksmithing here) work too. A name the server cannot resolve
                       is logged and skipped, not fatal. The call ADDS to the current
                       level, which for a brand-new character is the same as setting it.
    items:             map of prefab name -> count, applied with Inventory.AddItem.
    quality:           map of prefab name -> quality. OUR BUILD ONLY - ServerCharacters
                       1.4.17.1 and later, from tools/servercharacters/patches/. Clamped
                       at runtime to the item's own m_maxQuality, and the clamp is logged.
    equip:             ordered list of prefab names, HIGHEST PRIORITY FIRST, equipped with
                       Humanoid.EquipItem. OUR BUILD ONLY. Taken verbatim from the
                       preset's own `equip` list - this renderer does not decide what to
                       wear, because with two chest pieces in a kit there is no defensible
                       guess. Slots are never named: the game derives them from
                       m_itemType.
    contents:          map of container prefab -> (prefab -> count), which fills a
                       Vapok-AdventureBackpacks backpack through that mod's own API. OUR
                       BUILD ONLY. Taken verbatim from the preset's `contents` mapping.
    spawn:             list of {x, y, z} INTEGERS. One entry is picked per player, by a
                       hash of their user id, so a list spreads players over several
                       points deterministically. An empty list leaves the normal spawn
                       alone.

`quality`, `equip` and `contents` are FATAL to ServerCharacters 1.4.17 and older: its
deserialiser throws YamlDotNet.Core.YamlException on an unmatched property and the only
`catch` around it catches SerializationException, which YamlDotNet never throws, so the
exception escapes and the WHOLE template is discarded - no skills, no items, no spawn.
Each of the three is therefore emitted only when the preset actually needs it, and the
rollout order is: new DLL to the server and every client edition FIRST, template SECOND.

What this render still cannot express:

    stations           buildings, not inventory. Still an admin job from the
                       ulfsland-admin edition.
    global_keys        world state, not character state. Still RCON `setkey`.
    world_modifiers    launch arguments.

usage: preset_to_template.py PRESET [-o OUT]
       preset_to_template.py --all -d DIR
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

PRESETS = Path(__file__).resolve().parent.parent / "jumpstart" / "presets"


def render(preset: dict) -> tuple[dict, list[str]]:
    """Template body plus the list of things the format dropped on the floor."""
    lost: list[str] = []

    # Zero-level skills are noise: CheatRaiseSkill(name, 0) is a no-op, and every preset
    # carries a full roster with the not-yet-relevant schools at 0.
    skills = {name: float(level) for name, level in sorted(preset.get("skills", {}).items()) if level}

    items: dict[str, int] = {}
    quality: dict[str, int] = {}
    for entry in list(preset.get("kit", [])) + list(preset.get("materials", [])) + list(preset.get("chain_items", [])):
        prefab, count = entry["prefab"], int(entry["count"])
        # A prefab named by both the kit and the materials is one inventory key, so the
        # counts add rather than the second silently winning.
        items[prefab] = items.get(prefab, 0) + count
        wanted = int(entry.get("quality", 1))
        if wanted > 1:
            # One inventory key means one quality. Two entries naming the same prefab at
            # different qualities cannot both be granted, so take the higher and say so
            # rather than letting declaration order decide silently.
            if quality.get(prefab, 1) not in (1, wanted):
                lost.append(
                    f"{prefab} is asked for at both quality {quality[prefab]} and {wanted}; "
                    f"granting {max(quality[prefab], wanted)} because one prefab is one inventory key"
                )
            quality[prefab] = max(quality.get(prefab, 1), wanted)

    # `equip` and `contents` are the preset's decisions, not this renderer's. Guessing what
    # to wear would invent a policy the preset author did not write - and with two chest
    # pieces or two shields in a kit there is no defensible guess. Pass through or report.
    equip = [str(prefab) for prefab in preset.get("equip", [])]
    unknown = [prefab for prefab in equip if prefab not in items]
    if unknown:
        lost.append(f"equip names {', '.join(unknown)}, which the kit does not grant - they will be skipped")
    if not equip:
        lost.append("no equip order declared, so the character carries the kit but wears none of it")

    contents = {
        str(container): {str(p): int(c) for p, c in sorted(payload.items())}
        for container, payload in sorted(preset.get("contents", {}).items())
    }
    missing = [container for container in contents if container not in items]
    if missing:
        lost.append(f"contents names {', '.join(missing)}, which the kit does not grant - they will be skipped")

    if preset.get("stations"):
        lost.append(f"{len(preset['stations'])} crafting station(s) - build these in-game from ulfsland-admin")
    if preset.get("global_keys"):
        lost.append(f"global keys {', '.join(preset['global_keys'])} - set these with RCON setkey")
    if preset.get("world_modifiers"):
        lost.append("world modifiers - these are server launch arguments")

    body: dict = {"skills": skills, "items": dict(sorted(items.items()))}
    # Emitted only when non-empty: every one of these three keys is FATAL to a
    # ServerCharacters older than 1.4.17.1, so a preset that needs none of them still
    # renders a template the old build can read.
    if quality:
        body["quality"] = dict(sorted(quality.items()))
    if equip:
        body["equip"] = equip
    if contents:
        body["contents"] = contents
    body["spawn"] = []
    return body, lost


def emit(path: Path, preset: dict, body: dict, lost: list[str]) -> str:
    header = [
        f"# ServerCharacters CharacterTemplate.yml - {preset['name']} (tier {preset['tier']}, {preset['biome']})",
        "#",
        f"# {preset['summary']}",
        "#",
        "# Generated by tools/servercharacters/preset_to_template.py from",
        f"# tools/jumpstart/presets/{path.name.replace('CharacterTemplate.', '').replace('.yml', '.yaml')}.",
        "# Applied to NEW characters only, by the client, from the server's copy of this file.",
        "# The server re-reads it on write; no restart is needed to switch tiers.",
        "#",
    ]
    if lost:
        header.append("# Not expressible in this format, still manual:")
        header += [f"#   - {item}" for item in lost]
        header.append("#")
    return "\n".join(header) + "\n" + yaml.safe_dump(body, sort_keys=False, default_flow_style=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("preset", nargs="?", help="preset name or path, e.g. pre-bonemass")
    parser.add_argument("-o", "--out", help="output file; default stdout")
    parser.add_argument("--all", action="store_true", help="render every preset")
    parser.add_argument("-d", "--dir", help="output directory for --all")
    args = parser.parse_args()

    if args.all:
        if not args.dir:
            parser.error("--all needs -d DIR")
        target = Path(args.dir)
        target.mkdir(parents=True, exist_ok=True)
        for source in sorted(PRESETS.glob("*.yaml")):
            preset = yaml.safe_load(source.read_text())
            body, lost = render(preset)
            path = target / f"CharacterTemplate.{preset['name']}.yml"
            path.write_text(emit(path, preset, body, lost))
            print(f"{path}  {len(body['skills'])} skills, {len(body['items'])} item stacks")
        return 0

    if not args.preset:
        parser.error("give a preset, or --all")
    source = Path(args.preset)
    if not source.is_file():
        source = PRESETS / f"{args.preset}.yaml"
    if not source.is_file():
        print(f"no such preset: {args.preset}", file=sys.stderr)
        return 2
    preset = yaml.safe_load(source.read_text())
    body, lost = render(preset)
    path = Path(args.out) if args.out else Path(f"CharacterTemplate.{preset['name']}.yml")
    text = emit(path, preset, body, lost)
    if args.out:
        path.write_text(text)
        print(f"{path}  {len(body['skills'])} skills, {len(body['items'])} item stacks")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
