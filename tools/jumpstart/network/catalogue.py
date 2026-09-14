#!/usr/bin/env python3
"""Load and validate the per-tier installation set.

The durable model lives in data/installations.yaml. This module:
  * resolves every `composition: blueprint` reference against
    tools/jumpstart/library/data/library_manifest.json and fills in the sha256, so
    the YAML never has to carry a hash that can drift out of date by hand;
  * refuses a reference the manifest does not know, or whose pinned sha256
    disagrees with the manifest -- a silent mismatch here means stamping the wrong
    building;
  * derives the per-tier SET by accumulation from `first_tier`, which is the whole
    point: a later preset is a world that has been lived in longer;
  * cross-checks each installation's station levels against the preset's own
    station ladder, so an installation cannot claim a level its tier has not
    unlocked;
  * carries the `doorstep` field for the home base of each tier, which is what
    SpawnOnLand derives a spawn point from.

Bodies are never read and never vendored. The corpus came from valheimians.com,
whose terms forbid redistribution, and this repo is published.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import DATA, JUMPSTART

LIBRARY_MANIFEST = JUMPSTART / "library" / "data" / "library_manifest.json"
BLUEPRINT_INVENTORY = JUMPSTART / "blueprints" / "data" / "inventory.json"
PRESET_DIR = JUMPSTART / "presets"

# Roles that must exist exactly once per tier. Everything else may repeat.
SINGLETON_ROLES = {"home_base"}


class CatalogueError(RuntimeError):
    pass


# A typo'd key in a 700-line YAML is silent otherwise: `portals: 8` instead of
# `portal_pieces: 8` would model a hub with no portals and never complain.
KNOWN_INSTALLATION_KEYS = {
    "id", "role", "subrole", "first_tier", "retires_at", "repeatable", "composition",
    "layout", "biome", "requirement", "stations", "portal", "portal_pieces",
    "is_spawn_home", "crops", "berries", "animals", "requires_items", "blueprint",
    "companion_blueprint", "purpose", "justification",
}



@dataclass
class Blueprint:
    name: str
    sha256: str
    pieces: int
    footprint_xzy_m: list[float]
    verdict: str
    kind: str
    creator: str
    licence: str
    body: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "sha256": self.sha256,
            "pieces": self.pieces,
            "footprint_xzy_m": self.footprint_xzy_m,
            "verdict": self.verdict,
            "kind": self.kind,
            "creator": self.creator,
            "licence": self.licence,
            "body": self.body,
        }


@dataclass
class Installation:
    id: str
    role: str
    first_tier: int
    composition: str
    biome: str
    purpose: str
    justification: str
    requirement: dict[str, Any]
    stations: list[dict[str, Any]]
    portal: bool
    portal_pieces: int
    subrole: str | None = None
    repeatable: bool = False
    is_spawn_home: bool = False
    retires_at: int | None = None
    crops: list[str] = field(default_factory=list)
    # Berry bushes and tree saplings are PlantEverything plantables. MEASURED:
    # EnforceBiomes = false, so these are NOT biome-locked, unlike vanilla crops
    # (EnforceBiomesVanilla = true). Kept separate for exactly that reason.
    berries: list[str] = field(default_factory=list)
    animals: list[str] = field(default_factory=list)
    # Items an installation needs but cannot craft, e.g. AncientUpgradeStation for a
    # level-4 Black Forge, which is a Mountain-cave drop. Not a station.
    requires_items: list[str] = field(default_factory=list)
    layout: str | None = None
    blueprint: Blueprint | None = None
    companion_blueprint: Blueprint | None = None

    def piece_estimate(self) -> int:
        """Pieces this installation costs, i.e. persistent ZDOs it adds."""
        n = 0
        if self.blueprint:
            n += self.blueprint.pieces
        if self.companion_blueprint:
            n += self.companion_blueprint.pieces
        if self.composition == "prefabs":
            n += LAYOUT_PIECE_ESTIMATE.get(self.layout or "", 0)
        # Stations and their extension pieces are ZDOs too, and for a prefab-composed
        # installation they are not counted anywhere else.
        if self.composition == "prefabs":
            for s in self.stations:
                n += 1 + len(s.get("extensions") or [])
        n += self.portal_pieces
        return n

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "role": self.role,
            "subrole": self.subrole,
            "first_tier": self.first_tier,
            "retires_at": self.retires_at,
            "repeatable": self.repeatable,
            "composition": self.composition,
            "layout": self.layout,
            "biome": self.biome,
            "requirement": self.requirement,
            "stations": self.stations,
            "portal": self.portal,
            "portal_pieces": self.portal_pieces,
            "is_spawn_home": self.is_spawn_home,
            "crops": self.crops,
            "berries": self.berries,
            "animals": self.animals,
            "requires_items": self.requires_items,
            "piece_estimate": self.piece_estimate(),
            "purpose": self.purpose,
            "justification": self.justification,
        }
        if self.blueprint:
            out["blueprint"] = self.blueprint.as_dict()
        if self.companion_blueprint:
            out["companion_blueprint"] = self.companion_blueprint.as_dict()
        return {k: v for k, v in out.items() if v not in (None, [], "")}


# Piece counts for the prefab-composed layouts. These are the layouts' own piece
# budgets, kept next to the generator in plan.py; duplicated here only as a
# summary so a tier's ZDO cost can be reported without generating every plan.
# plan.py asserts these against what it actually emits, so they cannot drift.
LAYOUT_PIECE_ESTIMATE = {
    "jetty_small": 46,
    "crop_plot": 226,
    "kiln_yard": 78,
    "apiary": 12,
    "animal_pen": 60,
    "mill_yard": 18,
    "sapling_yard": 104,
}


def _load_manifest() -> dict[str, dict[str, Any]]:
    blob = json.loads(LIBRARY_MANIFEST.read_text())
    return {e["name"]: e for e in blob["entries"]}


def _load_inventory() -> dict[str, dict[str, Any]]:
    if not BLUEPRINT_INVENTORY.exists():
        return {}
    rows = json.loads(BLUEPRINT_INVENTORY.read_text())
    return {r["file"]: r for r in rows if isinstance(r, dict) and r.get("file")}


def _parse_footprint(entry: dict[str, Any], inv: dict[str, Any] | None) -> list[float]:
    """Footprint in metres. Prefer inventory.json, which is the re-parsed source.

    inventory.json was corrected for a parser bug that invented pieces from
    PlanBuild #Terrain rows and inflated some bounding boxes, so where it covers a
    file it wins over the manifest's string. Where it does not, fall back to the
    manifest's "XxZxY" string.
    """
    if inv and isinstance(inv.get("footprint_xzy"), list):
        return [float(v) for v in inv["footprint_xzy"]]
    parts = str(entry.get("footprint", "")).split("x")
    if len(parts) != 3:
        raise CatalogueError(f"{entry['name']}: cannot parse footprint {entry.get('footprint')!r}")
    return [float(p) for p in parts]


def _resolve_blueprint(ref: dict[str, Any], manifest, inventory) -> Blueprint:
    name = ref["name"]
    entry = manifest.get(name)
    if entry is None:
        raise CatalogueError(f"blueprint {name!r} is not in {LIBRARY_MANIFEST.name}")
    pinned = ref.get("sha256")
    if pinned and pinned != entry["sha256"]:
        raise CatalogueError(
            f"blueprint {name!r} sha256 mismatch: pinned {pinned}, manifest {entry['sha256']}"
        )
    inv = inventory.get(name)
    return Blueprint(
        name=name,
        sha256=entry["sha256"],
        pieces=int(inv["pieces"]) if inv and inv.get("pieces") is not None else int(entry["pieces"]),
        footprint_xzy_m=_parse_footprint(entry, inv),
        verdict=entry["verdict"],
        kind=entry["kind"],
        creator=entry.get("creator", ""),
        licence=entry.get("licence", ""),
        body=entry.get("body", ""),
    )


def _preset_station_levels(preset: str) -> dict[str, int]:
    path = PRESET_DIR / f"{preset}.yaml"
    if not path.exists():
        return {}
    doc = yaml.safe_load(path.read_text())
    return {s["prefab"]: int(s["level"]) for s in (doc.get("stations") or [])}


@dataclass
class Catalogue:
    world: str
    seed: str
    tiers: dict[int, str]
    crop_biomes: dict[str, list[str]]
    installations: list[Installation]

    def tier_of(self, preset: str) -> int:
        for t, p in self.tiers.items():
            if p == preset:
                return t
        raise CatalogueError(f"unknown preset {preset!r}")

    def set_for_tier(self, tier: int) -> list[Installation]:
        """Accumulated set: everything first placed at or before `tier`."""
        out = [
            i
            for i in self.installations
            if i.first_tier <= tier and (i.retires_at is None or i.retires_at > tier)
        ]
        return sorted(out, key=lambda i: (i.first_tier, i.role, i.id))

    def home_base_for_tier(self, tier: int) -> Installation:
        """The installation a player of this tier spawns at.

        Not simply "the home_base with the highest first_tier": it is the one whose
        first_tier is this tier, because that is the base the preset represents.
        """
        homes = [i for i in self.set_for_tier(tier) if i.role == "home_base"]
        if not homes:
            raise CatalogueError(f"tier {tier} has no home_base")
        exact = [i for i in homes if i.first_tier == tier]
        return (exact or homes)[-1]

    def cumulative_station_levels(self, tier: int) -> dict[str, int]:
        """Highest level each station reaches at or before `tier`, across presets."""
        out: dict[str, int] = {}
        for t, preset in sorted(self.tiers.items()):
            if t > tier:
                break
            for prefab, level in _preset_station_levels(preset).items():
                out[prefab] = max(out.get(prefab, 0), level)
        return out

    def validate(self) -> list[str]:
        """Return a list of problems. Empty list means the model is coherent."""
        problems: list[str] = []
        seen: set[str] = set()
        for inst in self.installations:
            if inst.id in seen:
                problems.append(f"duplicate installation id {inst.id!r}")
            seen.add(inst.id)
            if inst.first_tier not in self.tiers:
                problems.append(f"{inst.id}: first_tier {inst.first_tier} is not a known tier")
            if inst.composition == "blueprint" and inst.blueprint is None:
                problems.append(f"{inst.id}: composition blueprint but no blueprint reference")
            if inst.composition == "prefabs" and not inst.layout:
                problems.append(f"{inst.id}: composition prefabs but no layout")
            if inst.composition == "prefabs" and inst.layout not in LAYOUT_PIECE_ESTIMATE:
                problems.append(f"{inst.id}: unknown layout {inst.layout!r}")
            if inst.portal and inst.portal_pieces < 1:
                problems.append(f"{inst.id}: portal true but portal_pieces {inst.portal_pieces}")
            if not inst.portal and inst.portal_pieces:
                problems.append(f"{inst.id}: portal false but portal_pieces {inst.portal_pieces}")

        for tier, preset in sorted(self.tiers.items()):
            homes = [i for i in self.set_for_tier(tier) if i.role in SINGLETON_ROLES and i.first_tier == tier]
            if len(homes) != 1:
                problems.append(f"tier {tier} ({preset}): expected exactly 1 home_base, found {len(homes)}")
            # The ladder ACCUMULATES: a station unlocked at an earlier tier is still
            # available later, even when a later preset's own list omits it (bed is
            # in pre-eikthyr and in no preset after it). Take the running maximum
            # level across every tier up to and including this one.
            ladder = self.cumulative_station_levels(tier)
            if not ladder:
                continue
            for inst in self.set_for_tier(tier):
                if inst.first_tier != tier:
                    continue  # only check an installation against the tier that introduces it
                for s in inst.stations:
                    prefab, want = s["prefab"], int(s.get("level", 1))
                    have = ladder.get(prefab)
                    if have is None:
                        problems.append(
                            f"tier {tier} ({preset}) {inst.id}: station {prefab} is not in the preset's ladder"
                        )
                    elif want > have:
                        problems.append(
                            f"tier {tier} ({preset}) {inst.id}: station {prefab} wants level {want}, preset allows {have}"
                        )
                    ext = s.get("extensions") or []
                    if ext and len(ext) != want - 1:
                        problems.append(
                            f"tier {tier} {inst.id}: station {prefab} level {want} needs {want - 1} extensions, has {len(ext)}"
                        )
                for crop in inst.crops:
                    allowed = self.crop_biomes.get(inst.biome, [])
                    # Tree saplings are not crops and carry no biome mask.
                    if crop.endswith("_Sapling"):
                        continue
                    if allowed and crop not in allowed:
                        problems.append(
                            f"{inst.id}: crop {crop} is not permitted in {inst.biome} "
                            f"(EnforceBiomesVanilla is true)"
                        )
        return problems


def load(path: Path | None = None) -> Catalogue:
    path = path or (DATA / "installations.yaml")
    doc = yaml.safe_load(path.read_text())
    manifest = _load_manifest()
    inventory = _load_inventory()
    insts: list[Installation] = []
    for raw in doc["installations"]:
        unknown = set(raw) - KNOWN_INSTALLATION_KEYS
        if unknown:
            raise CatalogueError(f"{raw.get('id')}: unknown key(s) {sorted(unknown)}")
        bp = raw.get("blueprint")
        cbp = raw.get("companion_blueprint")
        insts.append(
            Installation(
                id=raw["id"],
                role=raw["role"],
                subrole=raw.get("subrole"),
                first_tier=int(raw["first_tier"]),
                retires_at=raw.get("retires_at"),
                repeatable=bool(raw.get("repeatable", False)),
                composition=raw["composition"],
                layout=raw.get("layout"),
                biome=raw.get("biome", "any"),
                requirement=raw.get("requirement") or {},
                stations=raw.get("stations") or [],
                portal=bool(raw.get("portal", False)),
                portal_pieces=int(raw.get("portal_pieces", 0)),
                is_spawn_home=bool(raw.get("is_spawn_home", False)),
                crops=raw.get("crops") or [],
                berries=raw.get("berries") or [],
                animals=raw.get("animals") or [],
                requires_items=raw.get("requires_items") or [],
                purpose=(raw.get("purpose") or "").strip(),
                justification=(raw.get("justification") or "").strip(),
                blueprint=_resolve_blueprint(bp, manifest, inventory) if bp else None,
                companion_blueprint=_resolve_blueprint(cbp, manifest, inventory) if cbp else None,
            )
        )
    return Catalogue(
        world=doc["world"],
        seed=doc["seed"],
        tiers={int(k): v for k, v in doc["tiers"].items()},
        crop_biomes=doc.get("crop_biomes") or {},
        installations=insts,
    )


def pin_hashes(path: Path | None = None) -> int:
    """Rewrite `sha256: null` in installations.yaml with the manifest's hash.

    Keeps the data file self-describing without making a human maintain hashes.
    Returns the number of hashes written.
    """
    path = path or (DATA / "installations.yaml")
    manifest = _load_manifest()
    lines = path.read_text().splitlines(keepends=True)
    out: list[str] = []
    written = 0
    for line in lines:
        stripped = line.strip()
        if "sha256: null}" in stripped and "{name:" in stripped:
            head, _, tail = line.partition("sha256: null}")
            name = stripped.split("{name:", 1)[1].split(",", 1)[0].strip()
            entry = manifest.get(name)
            if entry is None:
                raise CatalogueError(f"cannot pin unknown blueprint {name!r}")
            line = f"{head}sha256: {entry['sha256']}}}{tail}"
            written += 1
        out.append(line)
    path.write_text("".join(out))
    return written
