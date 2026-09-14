#!/usr/bin/env python3
"""Derive the per-world, per-preset jumpstart tree.

    tools/jumpstart/presets/<preset>.yaml   WORLD-AGNOSTIC tier definition
    tools/jumpstart/worlds/tier-policy.yaml WORLD-AGNOSTIC tier -> settings policy
    tools/jumpstart/worlds/<World>/world.yaml   WORLD-SPECIFIC facts
        |
        +--> tools/jumpstart/worlds/<World>/<preset>/
                 settings/world-modifiers.env    generated
                 settings/overrides.yaml         generated
                 settings/chest-manifest.yaml    generated
                 keys.yaml                       generated
                 charactertemplate.yml           generated
                 blueprints/                     created empty, owned by others
                 placements.yaml                 NOT written; read if present

Nothing here is hand-maintained. Every generated file carries a provenance
header naming the three inputs, and `--check` re-derives in memory and diffs, so
a hand edit or a stale regenerate is detectable rather than silent.

    derive.py list
    derive.py build <World> [--preset NAME ...]
    derive.py check <World> [--preset NAME ...]

`build` writes; `check` never writes and exits 1 on drift.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
REPO = JUMPSTART.parent.parent
POLICY_PATH = HERE / "tier-policy.yaml"

GENERATED_BY = "tools/jumpstart/worlds/derive.py"
# Files this tool owns. Anything else in a preset directory belongs to someone
# else and is never written, moved or deleted here.
OWNED = (
    "settings/world-modifiers.env",
    "settings/overrides.yaml",
    "settings/chest-manifest.yaml",
    "keys.yaml",
    "charactertemplate.yml",
)


def _load_jumpstart():
    """Import jumpstart.py as a module so preset loading has exactly one impl.

    Re-parsing the preset schema here is how the two directories would drift, so
    the loader is borrowed rather than copied. jumpstart.py guards its CLI behind
    __main__, so importing it runs nothing.
    """
    spec = importlib.util.spec_from_file_location("jumpstart", JUMPSTART / "jumpstart.py")
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit(f"cannot import {JUMPSTART / 'jumpstart.py'}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["jumpstart"] = module
    spec.loader.exec_module(module)
    return module


js = _load_jumpstart()


# ==========================================================================
# inputs
# ==========================================================================
@dataclass
class World:
    name: str
    root: Path
    raw: dict

    @property
    def baseline_modifiers(self) -> dict[str, str]:
        return dict((self.raw.get("baseline") or {}).get("world_modifiers") or {})

    @property
    def pinned_modifiers(self) -> list[str]:
        """Modifier keys the world owns; a preset cannot move these."""
        return list((self.raw.get("baseline") or {}).get("pinned") or [])

    @property
    def launch_preset(self) -> str:
        return str((self.raw.get("baseline") or {}).get("launch_preset") or "Normal")

    @property
    def slots(self) -> int:
        return int((self.raw.get("inventory") or {}).get("slots") or 32)

    @property
    def stack_table_path(self) -> Path:
        rel = (self.raw.get("inventory") or {}).get("stack_table")
        if not rel:
            return Path()
        # Paths in world.yaml are written relative to VALHEIM_ROOT, which is where
        # the deployed profiles live; they are not repo paths.
        return Path(js.DEFAULT_VALHEIM_ROOT) / rel

    @property
    def stack_fallbacks(self) -> dict[str, int]:
        raw = (self.raw.get("inventory") or {}).get("stack_fallbacks") or {}
        return {str(k): int(v) for k, v in raw.items()}


def load_world(name: str) -> World:
    root = HERE / name
    path = root / "world.yaml"
    if not path.exists():
        raise SystemExit(f"no such world: {name!r} (looked for {path})")
    raw = yaml.safe_load(path.read_text("utf-8"))
    if not isinstance(raw, dict) or raw.get("world") != name:
        raise SystemExit(f"{path}: 'world' must be {name!r}")
    return World(name=name, root=root, raw=raw)


def load_policy() -> dict:
    raw = yaml.safe_load(POLICY_PATH.read_text("utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit(f"{POLICY_PATH}: policy must be a YAML mapping")
    return raw


STACK_RE = re.compile(r"^([A-Za-z0-9_]+)_max_stack\s*=\s*(\d+)\s*$")


def load_stacks(world: World) -> tuple[dict[str, int], dict[str, int]]:
    """(measured stack sizes, inferred fallbacks). Missing table is not fatal."""
    measured: dict[str, int] = {}
    path = world.stack_table_path
    if path and path.exists():
        for line in path.read_text("utf-8", errors="replace").splitlines():
            m = STACK_RE.match(line.strip())
            if m:
                measured[m.group(1)] = int(m.group(2))
    return measured, world.stack_fallbacks


def load_spawn_points(preset_dir: Path) -> list[list[int]]:
    """Read `spawn_points:` out of BlueprintHunt's placements.yaml, if it exists."""
    path = preset_dir / "placements.yaml"
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text("utf-8")) or {}
    points = raw.get("spawn_points") if isinstance(raw, dict) else None
    out: list[list[int]] = []
    for point in points or []:
        if isinstance(point, dict):
            triple = [point.get("x"), point.get("y"), point.get("z")]
        else:
            triple = list(point)
        if len(triple) != 3 or any(v is None for v in triple):
            raise SystemExit(f"{path}: spawn_points entry {point!r} is not an [x, y, z] triple")
        out.append([int(round(float(v))) for v in triple])
    return out


def load_container_capacity(preset_dir: Path) -> int | None:
    """Chest slot capacity of the placed base, if placements.yaml reports it."""
    path = preset_dir / "placements.yaml"
    if not path.exists():
        return None
    raw = yaml.safe_load(path.read_text("utf-8")) or {}
    if not isinstance(raw, dict):
        return None
    # BlueprintHunt's schema, counted out of the blueprint object list:
    #   containers: {by_prefab: {<piece>: n}, containers: n, slots_floor: n}
    # `slots_floor` is the conservative slot total, which is the number worth
    # comparing an overflow manifest against.
    total = 0
    seen = False
    for placement in raw.get("placements") or []:
        containers = (placement or {}).get("containers") or {}
        if "slots_floor" in containers:
            seen = True
            total += int(containers["slots_floor"])
    return total if seen else None


# ==========================================================================
# derivation
# ==========================================================================
def item_map(preset) -> tuple[dict[str, int], list[tuple[str, str, int]], list[str]]:
    """Collapse the preset's three item buckets into the map the template needs.

    Returns (summed map, ordered [(bucket, prefab, count)], duplicate prefabs).
    The buckets are LISTS and the template field is a MAP, so a prefab named in
    two buckets would be silently overwritten by a naive conversion. pre-kall and
    deepnorth-sandbox both do that, so the counts are summed and the collisions
    reported.
    """
    merged: dict[str, int] = {}
    ordered: list[tuple[str, str, int]] = []
    dupes: list[str] = []
    for bucket in ("kit", "chain_items", "materials"):
        for row in preset.raw.get(bucket) or []:
            prefab = str(row["prefab"])
            count = int(row.get("count", 1))
            if prefab in merged:
                dupes.append(prefab)
            merged[prefab] = merged.get(prefab, 0) + count
            ordered.append((bucket, prefab, count))
    return merged, ordered, sorted(set(dupes))


def slot_cost(prefab: str, count: int, measured: dict[str, int], fallbacks: dict[str, int]) -> tuple[int, str]:
    if count <= 1:
        # One item is one slot whatever the stack size, so this needs no table.
        return 1, "trivial"
    if prefab in measured:
        return -(-count // measured[prefab]), "measured"
    if prefab in fallbacks:
        return -(-count // fallbacks[prefab]), "inferred"
    return count, "unknown"


@dataclass
class Partition:
    carried: dict[str, int]
    carried_slots: int
    overflow: dict[str, int]
    overflow_slots: int
    detail: dict[str, tuple[int, str, str]]  # prefab -> (slots, evidence, bucket)
    dupes: list[str]
    unknown: list[str]


def partition(preset, world: World, measured: dict[str, int], fallbacks: dict[str, int]) -> Partition:
    """Split the preset's items into what a 32-slot grid can hold and what cannot.

    Priority is kit, then chain_items, then the cheapest materials, because the
    kit is what makes a character read as "at this tier" and materials are also
    reachable from a chest. An entry that does not fit is skipped and the next is
    tried -- deterministic, and it wastes no slots -- rather than truncating the
    whole tail.
    """
    merged, ordered, dupes = item_map(preset)

    detail: dict[str, tuple[int, str, str]] = {}
    first_bucket: dict[str, str] = {}
    for bucket, prefab, _count in ordered:
        first_bucket.setdefault(prefab, bucket)
    unknown: list[str] = []
    for prefab, count in merged.items():
        slots, evidence = slot_cost(prefab, count, measured, fallbacks)
        if evidence == "unknown":
            unknown.append(prefab)
        detail[prefab] = (slots, evidence, first_bucket[prefab])

    rank = {"kit": 0, "chain_items": 1, "materials": 2}
    order = sorted(
        merged,
        key=lambda p: (rank[detail[p][2]], detail[p][0] if detail[p][2] == "materials" else 0, p),
    )

    carried: dict[str, int] = {}
    used = 0
    for prefab in order:
        cost = detail[prefab][0]
        if used + cost <= world.slots:
            carried[prefab] = merged[prefab]
            used += cost
    overflow = {p: c for p, c in merged.items() if p not in carried}
    return Partition(
        carried=carried,
        carried_slots=used,
        overflow=overflow,
        overflow_slots=sum(detail[p][0] for p in overflow),
        detail=detail,
        dupes=dupes,
        unknown=sorted(unknown),
    )


def effective_modifiers(preset, world: World, policy: dict) -> tuple[dict[str, str], list[str]]:
    """Baseline, overlaid with the preset's non-Default modifiers, except pins.

    A PINNED key belongs to the world and a preset cannot move it. Without pins
    the merge is backwards for exactly the value the operator asked to have one
    home: every `pre-*` preset declares `Resources: More`, so an unpinned merge
    would let nine tier files quietly downgrade the world's `MuchMore` baseline.
    Pinning keeps the fleet's resource rate and portal policy a single edit in
    world.yaml, while leaving the keys no preset shares -- Raids, DeathPenalty,
    Combat -- free for a preset to set.
    """
    ignore = str((policy.get("world_modifiers") or {}).get("ignore_value") or "Default")
    pinned = set(world.pinned_modifiers)
    effective = dict(world.baseline_modifiers)
    changes: list[str] = []
    for key in js.MODIFIER_KEYS:
        value = preset.world_modifiers.get(key)
        if not value or value == ignore:
            continue
        previous = effective.get(key)
        if key in pinned:
            if previous != value:
                changes.append(
                    f"{key}: preset asks {value}, world pins {previous} -- world wins"
                )
            continue
        effective[key] = value
        if previous is None:
            changes.append(f"{key}: (unset) -> {value}  [preset]")
        elif previous != value:
            changes.append(f"{key}: {previous} -> {value}  [preset]")
    return effective, changes


def applicable_rules(preset, policy: dict) -> list[dict]:
    out = []
    for rule in policy.get("server_config_rules") or []:
        by_tier = rule.get("by_tier") or {}
        if preset.tier in by_tier:
            out.append((rule, by_tier[preset.tier]))
    return out


# ==========================================================================
# rendering
# ==========================================================================
def provenance(world: World, preset, kind: str) -> list[str]:
    return [
        f"# GENERATED by {GENERATED_BY} -- do not edit.",
        f"# {kind} for world {world.name}, preset {preset.name} (tier {preset.tier}).",
        "# Inputs:",
        f"#   tools/jumpstart/presets/{preset.name}.yaml   (tier definition, world-agnostic)",
        "#   tools/jumpstart/worlds/tier-policy.yaml      (tier -> settings policy, world-agnostic)",
        f"#   tools/jumpstart/worlds/{world.name}/world.yaml   (this world's facts)",
        "# Regenerate:  tools/jumpstart/worlds/derive.py build " + world.name,
    ]


def render_modifiers(world: World, preset, policy: dict) -> str:
    effective, changes = effective_modifiers(preset, world, policy)
    args = " ".join(f"-modifier {k} {effective[k]}" for k in js.MODIFIER_KEYS if k in effective)
    lines = provenance(world, preset, "World modifiers (launch arguments, not config keys)")
    lines += [
        "#",
        "# These are process arguments: they belong in valheim.env's SERVER_ARGS and",
        "# need a server restart (~5 minutes to joinable on Ulfsland). Nothing in this",
        "# tree applies them.",
        "#",
        f"# Baseline (world.yaml baseline.world_modifiers): "
        + ", ".join(f"{k}={v}" for k, v in sorted(world.baseline_modifiers.items())),
    ]
    if changes:
        lines += ["# Preset changes:"] + [f"#   {c}" for c in changes]
    else:
        lines += ["# Preset changes: none -- this preset asks for nothing the baseline does not give."]
    lines += [
        "",
        f"SERVER_ARGS='-preset {world.launch_preset} {args}'",
        "",
    ]
    return "\n".join(lines)


def render_overrides(world: World, preset, policy: dict) -> str:
    rules = applicable_rules(preset, policy)
    doc: list[str] = provenance(world, preset, "Server-config overrides")
    doc += [
        "#",
        "# Each entry is a SINGLE key in a SINGLE section of one deployed config file,",
        "# with the value currently deployed alongside the value this tier wants. Whole",
        "# config files are deliberately not copied here: nine copies of a 700-line file",
        "# is the drift trap this layout exists to avoid.",
        "#",
        f"# Deployed files live under {world.raw.get('server_config_root')} .",
        "# Applying these is an operator action; derive.py never writes a profile.",
        "",
    ]
    body = {
        "world": world.name,
        "preset": preset.name,
        "tier": preset.tier,
        "server_config_root": world.raw.get("server_config_root"),
        "overrides": [
            {
                "id": rule["id"],
                "config": rule["config"],
                "section": rule["section"],
                "key": rule["key"],
                "base": rule["base"],
                "override": value,
                "evidence": rule["evidence"],
                "why": rule["why"],
            }
            for rule, value in rules
        ],
    }
    if not rules:
        body["overrides"] = []
        body["note"] = (
            "No overrides. Every deployed value is already correct for this tier -- "
            "recorded explicitly so the next reader knows this is a decision, not a gap."
        )
    return "\n".join(doc) + yaml.safe_dump(body, sort_keys=False, width=88, allow_unicode=True)


def render_keys(world: World, preset, policy: dict) -> str:
    keys = preset.global_keys
    doc = provenance(world, preset, "Global keys")
    doc += [
        "#",
        "# Global keys are free-form strings that persist in the world DB. They are not",
        "# cosmetic on this server: CLLC reads them (Second factor = BossesKilled),",
        "# ZenWorldSettings gates per-biome spawns on them, ZenRaids appends them to",
        "# player keys, and EpicLoot picks biome loot tables from them with",
        "# `Defer Chest Loot Roll = true`, so every unopened chest in the world rerolls",
        "# at the new key state on first interaction.",
        "#",
        "# Applied by tools/jumpstart/jumpstart.py, one at a time, re-reading globalKeys",
        "# after each so a half-applied chain is diagnosable. Not applied by derive.py.",
        "",
    ]
    body: dict = {
        "world": world.name,
        "preset": preset.name,
        "tier": preset.tier,
        "boss": preset.raw.get("boss"),
        "global_keys": keys,
        "apply": {
            "tool": "tools/jumpstart/jumpstart.py",
            "command": f"jumpstart.py apply {preset.name} --players <steamid> --keys-only --commit",
            "order": "as listed; the driver reads globalKeys back after each key",
        },
    }
    warnings: list[str] = []
    if not keys:
        warnings.append(
            "This preset sets NO keys. That is correct for tier 1 and it is also what "
            "makes tier 1 safe: almost every raid and every high-tier spawn is key-gated."
        )
    if "defeated_frozenking_p3" in keys:
        warnings.append(
            "defeated_frozenking_p3 is INFERRED spelling. It has never been observed set "
            "on this fleet. Do not apply it blind -- confirm it against the game's own "
            "GlobalKeys data first; a misspelled key silently persists in the world DB "
            "and cannot be distinguished from a real one."
        )
    if len(keys) >= 6:
        warnings.append(
            "CLLC world level is driven by boss keys and saturates at 6, so this preset "
            "maxes the creature-level curve world-wide and immediately, including in "
            "Meadows. settings/overrides.yaml lowers CLLC Difficulty to compensate."
        )
    if "defeated_fader" in keys:
        warnings.append(
            "defeated_fader adds no further CLLC difficulty (the curve is already "
            "saturated) but is the sharpest early-biome spawn offender. It is blocked in "
            "Meadows through Mistlands by ZenWorldSettings; Ocean is the hole, which is "
            "why tiers 7-8 override `Ocean - Blocked Keys`."
        )
    if warnings:
        body["warnings"] = warnings
    return "\n".join(doc) + yaml.safe_dump(body, sort_keys=False, width=88, allow_unicode=True)


def render_chest_manifest(world: World, preset, part: Partition, capacity: int | None) -> str:
    doc = provenance(world, preset, "Chest manifest (template overflow)")
    chest_slots = 32
    implied = -(-part.overflow_slots // chest_slots) if part.overflow_slots else 0
    doc += [
        "#",
        "# What the ServerCharacters template CANNOT carry. ServerCharacters grants items",
        "# with Inventory.AddItem, which returns null once the grid is full, and the mod",
        "# ignores the return -- so anything past the last free slot vanishes silently.",
        f"# This world's grid is {world.slots} slots (world.yaml inventory.slots).",
        "#",
        "# Delivery is headless and does not need the player online, per BlueprintHunt:",
        "#   findObjects -prefab piece_chest -near <x> <y> <z> <r>   -> ZDO ids",
        "#   addItemToContainer <id:userid> <item_name> -count <n>   -> stock each chest",
        "# Chest inventories are separate grids, so this sidesteps the truncation above.",
        "# Caveat: RCON responses hard-truncate at 4050 payload bytes and findObjects has",
        "# no pagination, so keep the -near radius tight on a populated world.",
        "# Alternative: a base hand-built and saved from the ulfsland-admin seat with",
        "# Infinity Hammer `hammer_save data=true` carries its chest contents in the",
        "# blueprint, which makes this manifest unnecessary for that preset.",
        "",
    ]
    body: dict = {
        "world": world.name,
        "preset": preset.name,
        "tier": preset.tier,
        "carried_by_template_slots": part.carried_slots,
        "inventory_slots": world.slots,
        "overflow_slots": part.overflow_slots,
        "implied_chests_at_32_slots": implied,
        "items": {p: part.overflow[p] for p in sorted(part.overflow)},
    }
    if capacity is not None:
        body["placed_chest_slots"] = capacity
        if part.overflow_slots > capacity:
            body["capacity_shortfall_slots"] = part.overflow_slots - capacity
            body["action"] = (
                "Placed chest capacity is smaller than the overflow. BlueprintHunt adds a "
                "standalone storage blueprint to this preset's placement rather than "
                "dropping items."
            )
    else:
        body["placed_chest_slots"] = None
        body["note"] = (
            "placements.yaml reports no `containers:` block yet, so placed capacity is "
            "unknown and no shortfall check was made."
        )
    if part.unknown:
        body["stack_size_unknown"] = part.unknown
        body["stack_size_unknown_note"] = (
            "Neither the deployed ItemStacksRewrite table nor world.yaml's "
            "inventory.stack_fallbacks covers these, so their slot cost was counted as one "
            "slot per item -- an over-estimate, which errs towards the chest."
        )
    return "\n".join(doc) + yaml.safe_dump(body, sort_keys=False, width=88, allow_unicode=True)


def render_template(world: World, preset, part: Partition, spawn: list[list[int]]) -> str:
    sc = world.raw.get("servercharacters") or {}
    omit = dict((world.raw.get("skills") or {}).get("omit") or {})
    skills = {
        name: level
        for name, level in sorted(preset.skills.items())
        if level and name not in omit
    }
    dropped_inert = sorted(n for n in omit if preset.skills.get(n))
    quality_lost = sorted({str(r["prefab"]) for r in preset.raw.get("kit") or [] if int(r.get("quality", 1)) > 1})

    lines = provenance(world, preset, "ServerCharacters CharacterTemplate.yml")
    lines += [
        "#",
        f"# REQUIRES ServerCharacters {sc.get('requires_version')} or newer. 1.4.16 must NOT be",
        "# installed: it hard-fails on Valheim 1.0.12 and ServerSync rejects it against a",
        f"# {sc.get('requires_version')} server.",
        f"# STATUS: {sc.get('status')} -- see world.yaml servercharacters.status_note.",
        "#",
        "# Install: copy this file to",
        f"#   {sc.get('durable_path')}",
        "# The server watches it and live-reloads; no restart. One server holds ONE",
        "# template, so exactly one preset is active at a time.",
        "#",
        "# APPLIES TO NEW CHARACTERS ONLY. The client wipes inventory, skills, known",
        "# recipes and stations, calls GiveDefaultItems(), then applies this. An existing",
        "# character sees nothing.",
        "#",
        "# Exactly three top-level keys are permitted. The deserializer is built with",
        "# IgnoreFields() and the only catch is for SerializationException, which does not",
        "# catch YamlDotNet's YamlException -- so an unknown top-level key throws out of a",
        "# Harmony prefix. Comments are safe; extra keys are not.",
        "#",
        "# What this format drops, and where it went instead:",
        f"#   quality     AddItem hardcodes 1. {len(quality_lost)} kit entries ask for quality > 1 and",
        "#               arrive unupgraded; they must be upgraded at the station.",
        f"#   overflow    {len(part.overflow)} prefabs ({part.overflow_slots} slots) do not fit in "
        f"{world.slots} inventory slots",
        "#               and are in settings/chest-manifest.yaml instead.",
        "#   stations    buildings, not inventory -- blueprints/ and placements.yaml.",
        "#   keys        world state, not character state -- keys.yaml.",
        "#   modifiers   launch arguments -- settings/world-modifiers.env.",
        "#   equipping   nothing is auto-equipped; the kit arrives in the bag.",
        "#",
        "# Which of these skills actually move yield (MEASURED by YieldDesign). Five",
        "# skills drive the gathering economy and three of them are custom skills",
        "# registered by name through SkillManager, not members of the vanilla enum:",
        "#   ore / stone   Smoothbrain `Mining` AND vanilla `Pickaxes` -- ImpactfulSkills'",
        "#                 mining bonus reads vanilla Pickaxes (id 12). Both are needed.",
        "#   wood          Smoothbrain `Lumberjacking` only. Vanilla `WoodCutting` is",
        "#                 DELIBERATELY OMITTED below, not forgotten, and it is DOUBLY",
        "#                 dead: Lumberjacking prefixes CheatRaiseSkill with",
        "#                 RemoveWoodcuttingFromSkillFunctions, which rewrites the",
        "#                 literal to \"Woodcutting \" so it matches nothing, and also",
        "#                 prefixes GetSkill with ReturnDummyWoodcuttingSkill so any",
        "#                 raise that got through lands on a throwaway object.",
        "#                 ImpactfulSkills' WoodCuttingLootFactor = 3 is dead code.",
        "#                 Do not add it back.",
        "#   berries etc   Smoothbrain `Foraging` AND vanilla `Farming`.",
        "#   crops         vanilla `Farming` only -- Smoothbrain Foraging's scope",
        "#                 predicate needs Pickable.m_respawnTimeMinutes > 0 and crops are 0.",
        "#   meat / hide   no skill at all; flat, at whatever the world resource rate is.",
        "#",
        "# Can this map REACH the custom skills? YES, all five, by plain name,",
        "# case-insensitively. Established twice and independently: from the IL of",
        "# assembly_valheim.dll and the deployed plugins, and from the live SkillManager",
        "# registries and Harmony patch chain on a booted server. The two agree.",
        "#   vanilla   Skills::.cctor sets s_allSkills = Enum.GetValues(SkillType) and",
        "#             CheatRaiseSkill string-matches SkillType.ToString() against it, so",
        "#             unaided it reaches enum members only. m_level += value, clamped",
        "#             0..100 -- an ADD, so a set on a fresh character but it would",
        "#             STACK if the template were applied twice.",
        "#   custom    Each Smoothbrain plugin ILRepacks its own SkillManager, which",
        "#             Harmony-PREFIXES CheatRaiseSkill with Patch_Skills_CheatRaiseskill.",
        "#             It matches its registry's `internalSkillName` with",
        "#             StringComparison.CurrentCultureIgnoreCase and returns false to",
        "#             consume the call, else true and the chain continues. Three",
        "#             independent prefixes, each matching only its own skill, so there",
        "#             is no ordering hazard.",
        "#   ids       Not enum members, so the prefix is the ONLY route: Mining =",
        "#             1408976878, Foraging = 47719919, Lumberjacking = 1363793286,",
        "#             each (SkillType)englishName.GetStableHashCode().",
        "#   literal   internalSkillName = Regex.Replace(englishName, \"[^a-zA-Z]\", \"_\").",
        "#             Registered english names are literally Mining / Foraging /",
        "#             Lumberjacking -- pure alpha, so internal == display == what you",
        "#             type. The exact casing below is the registered casing.",
        "#   where     Applied CLIENT-side, in ServerCharacters.ClientSide",
        "#             .InitializePlayerFromTemplate.Postfix, so the three Smoothbrain",
        "#             mods must reach the client. They do -- all four are scope: shared",
        "#             in the ulfsland-dn manifest.",
        "#   trap      A matching prefix consumes the call, so a custom skill whose name",
        "#             collides with a vanilla one captures it. Smoothbrain-Farming did",
        "#             exactly that and was removed tonight; re-adding it would silently",
        "#             change what the `Farming` line below means.",
        "# `Farming` is RESOLVED: vanilla SkillType Farming is 0x6a = 106 in this build",
        "# and there is no id 15 at all. ImpactfulSkills contains no SkillManager",
        "# reference and reads GetSkillLevel(0x6a) -- vanilla Farming. Exactly one",
        "# Farming exists and this line reaches it. ImpactfulSkills' OWN custom skills",
        "# (Hauling, Voyager) are not template-settable; neither drives yield.",
        "",
    ]

    lines.append("skills:")
    if skills:
        for name, level in skills.items():
            lines.append(f"  {name}: {float(level)}")
        zero = sum(1 for level in preset.skills.values() if not level)
        lines.append(
            f"# {zero} skills omitted at level 0: CheatRaiseSkill(name, 0) is a no-op, and an"
        )
        lines.append("# unresolvable name is logged and skipped, not fatal.")
        for name in dropped_inert:
            lines.append(
                f"# {name} omitted at {preset.skills[name]} on purpose -- inert on this mod set,"
            )
            lines.append("# see world.yaml skills.omit. Re-adding it does nothing.")
    else:
        lines.append("  {}")

    lines.append("items:")
    if part.carried:
        for prefab in sorted(part.carried):
            lines.append(f"  {prefab}: {part.carried[prefab]}")
        lines.append(f"# {part.carried_slots} of {world.slots} inventory slots used.")
        if part.dupes:
            lines.append(
                "# Summed across buckets (the preset lists these twice, and `items` is a map): "
                + ", ".join(part.dupes)
            )
    else:
        lines.append("  {}")

    lines.append("spawn:")
    if spawn:
        emitted = list(spawn)
        if len(emitted) > 1:
            # Random.Range(0, count - 1) has an EXCLUSIVE upper bound, so the last
            # entry is never selected. Duplicating it makes every real point
            # reachable. placements.yaml lists real points only.
            emitted.append(list(emitted[-1]))
        for point in emitted:
            lines.append(f"  - {{x: {point[0]}, y: {point[1]}, z: {point[2]}}}")
        if len(spawn) > 1:
            lines.append(
                "# Last entry is a deliberate duplicate: selection is "
                "Random.Range(0, count - 1),"
            )
            lines.append("# whose upper bound is exclusive, so without it the real last point is dead.")
    else:
        lines.append("  []")
        lines.append("# No spawn_points in placements.yaml; vanilla spawn is left alone.")

    return "\n".join(lines) + "\n"


# ==========================================================================
# drive
# ==========================================================================
def generate(world: World, preset, policy: dict, measured, fallbacks) -> dict[str, str]:
    preset_dir = world.root / preset.name
    part = partition(preset, world, measured, fallbacks)
    spawn = load_spawn_points(preset_dir)
    capacity = load_container_capacity(preset_dir)
    return {
        "settings/world-modifiers.env": render_modifiers(world, preset, policy),
        "settings/overrides.yaml": render_overrides(world, preset, policy),
        "settings/chest-manifest.yaml": render_chest_manifest(world, preset, part, capacity),
        "keys.yaml": render_keys(world, preset, policy),
        "charactertemplate.yml": render_template(world, preset, part, spawn),
    }


def run(world_name: str, preset_names: list[str] | None, write: bool) -> int:
    world = load_world(world_name)
    policy = load_policy()
    measured, fallbacks = load_stacks(world)
    if not measured:
        print(
            f"warning: stack table {world.stack_table_path} not readable; "
            "slot costs fall back to one slot per item",
            file=sys.stderr,
        )
    names = preset_names or js.all_preset_names()

    drift = 0
    for name in names:
        preset = js.load_preset(name)
        preset_dir = world.root / name
        files = generate(world, preset, policy, measured, fallbacks)
        for rel, content in files.items():
            path = preset_dir / rel
            current = path.read_text("utf-8") if path.exists() else None
            if current == content:
                status = "ok"
            elif write:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, "utf-8")
                status = "wrote" if current is None else "updated"
            else:
                status = "DRIFT" if current is not None else "MISSING"
                drift += 1
            print(f"{status:8s} {world.name}/{name}/{rel}")
        if write:
            # Owned by BlueprintHunt. Created so the layout is complete; never
            # written into, never cleaned.
            (preset_dir / "blueprints").mkdir(parents=True, exist_ok=True)
    if drift:
        print(f"\n{drift} file(s) differ from what the inputs derive.", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list worlds and presets")
    for cmd, help_text in (("build", "write the tree"), ("check", "re-derive and diff, never write")):
        p = sub.add_parser(cmd, help=help_text)
        p.add_argument("world")
        p.add_argument("--preset", action="append", dest="presets")

    args = parser.parse_args(argv)
    if args.cmd == "list":
        worlds = sorted(p.parent.name for p in HERE.glob("*/world.yaml"))
        print("worlds:  " + (", ".join(worlds) or "(none)"))
        print("presets: " + ", ".join(js.all_preset_names()))
        print(f"owned:   {', '.join(OWNED)}")
        print("other:   blueprints/, placements.yaml  (BlueprintHunt)")
        return 0
    return run(args.world, args.presets, write=(args.cmd == "build"))


if __name__ == "__main__":
    raise SystemExit(main())
