#!/usr/bin/env python3
"""Inventory and 1.0-viability check for PlanBuild-format Valheim blueprints.

Reads ``*.blueprint`` (PlanBuild text format) and ``*.vbuild`` (legacy
BuildShare) files, and for each one reports

  * header metadata (name, creator, description, category, saved coordinates),
  * piece count and axis-aligned footprint in metres,
  * the distinct prefab names it places,
  * which of those prefabs this fleet can actually satisfy -- resolved against
    the evidence file written by ``scan_piece_prefabs.py``:
      ``vanilla``  the name is an ASCII token in Valheim 1.0.12's own asset
                   bundles, so the piece still exists,
      ``mod:<X>``  the name only appears in deployed mod ``X``, so the piece
                   exists but is mod-supplied,
      ``MISSING``  the name appears nowhere -- the piece was renamed or removed
                   by Iron Gate, or it belongs to a mod this fleet lacks.  A
                   blueprint with any MISSING prefab will place incompletely.

File format, MEASURED from ``InfinityHammer.dll`` 1.83.0
(``HammerBlueprintCommand::GetPlanBuildObject`` /
``HammerSaveCommand::GetPlanBuildObject``):

    header rows, case-insensitive, each ``#<key>:<value>``
        #Name: #Creator: #Description: #Category:
        #Center:<piece prefab used as the placement origin>
        #Coordinates:<x,z,y of the save position>
        #Rotation:<y,x,z euler of the save rotation>
    ``#SnapPoints``  section: ``x;y;z`` rows
    ``#TerrainHeight:`` / ``#TerrainPaint:`` sections (Infinity Hammer only)
    ``#Pieces``      section, one row per object, ``;``-separated:
        0  prefab name
        1  (PlanBuild category -- parsed but unused; Infinity Hammer writes "")
        2..4   position x, y, z
        5..8   rotation quaternion x, y, z, w
        9      extra info / m_text        (default "")
        10..12 scale x, y, z             (default 1)
        13     base64 ZDO data           (only read when data=true)
        14     placement chance          (default 1)
    A ``,`` anywhere in a piece row is rewritten to ``.`` before splitting, so
    decimal-comma locales parse.  Floats are invariant-culture ``0.###``.

    Sections are a state machine, and the rule that matters is what happens to
    an UNRECOGNISED header: the section becomes None and every row after it is
    discarded until the next recognised header. See ``header_section``. A header
    of length 1, or with whitespace at index 1, is a comment and the section
    survives it. ``#Height:`` / ``#Paint:`` are recognised and then REJECTED --
    Infinity Hammer refuses the whole file.

Usage:
    python3 inventory.py <dir-or-file> [...] [--evidence data/piece_prefabs.json]
                        [--json out.json] [--prune-evidence data/prefab_evidence.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Every header literal below is MEASURED from the DEPLOYED
# Infinity_Hammer/InfinityHammer.dll user-string heap (`monodis --userstrings`),
# not inferred from documentation. The reader's lowercased set is exactly:
#   "#"  "#name:"  "#creator:"  "#description:"  "#category:"  "#center:"
#   "#coordinates:"  "#rotation:"  "#pieces"  "#height:"  "#paint:"
# plus "#SnapPoints" / "#TerrainHeight:" / "#TerrainPaint:" compared
# case-insensitively. There is NO "#objects" literal anywhere in the assembly,
# which is why it is not a section here: an `#Objects` header is unknown, and an
# unknown header discards what follows it.
HEADER_KEYS = (
    "name",
    "creator",
    "description",
    "category",
    "center",
    "coordinates",
    "rotation",
)
SECTIONS = ("#pieces", "#snappoints", "#terrainheight:", "#terrainpaint:")
# Recognised, then rejected outright: InfinityHammer throws "Legacy #Height/#Paint
# terrain format is no longer supported. Re-save the blueprint with a newer
# Infinity Hammer version." A file carrying these does not place at all.
LEGACY_SECTIONS = ("#height:", "#paint:")


class LegacyTerrainFormat(ValueError):
    """The blueprint uses the #Height/#Paint sections Infinity Hammer rejects."""


def header_section(row: str, current: str | None) -> tuple[str | None, str | None]:
    """Infinity Hammer's section state machine for one `#` row.

    Returns `(section, metadata_key)`. MEASURED from `GetPlanBuild`: the machine
    is five-state (None / Pieces / SnapPoints / TerrainHeight / TerrainPaint) and
    starts at Pieces, so a headerless file is all pieces.

    The part that matters, and that this code used to get wrong: an UNRECOGNISED
    header whose second character is not whitespace sets the section to None and
    every row after it is discarded until the next recognised header. Keeping the
    previous section instead reads PlanBuild's `#Terrain` block -- rows shaped
    `shape;x;y;z;radius;rotation;smooth;` -- as pieces named `circle` and
    `square`, and the bundle-token viability check cannot catch that because both
    words genuinely occur as tokens in the game's asset bundles. Downstream that
    becomes `spawn circle` against a live server.

    A header of length 1, or with whitespace at index 1, is a comment: the
    section survives it.
    """
    low = row.lower()
    if len(row) == 1 or row[1].isspace():
        return current, None
    for key in HEADER_KEYS:
        if low.startswith("#" + key + ":"):
            return current, key
    for name in SECTIONS:
        if low.startswith(name):
            return name, None
    for name in LEGACY_SECTIONS:
        if low.startswith(name):
            raise LegacyTerrainFormat(row.strip())
    return None, None


class Piece:
    __slots__ = ("prefab", "x", "y", "z", "scale")

    def __init__(self, prefab: str, x: float, y: float, z: float, scale: tuple):
        self.prefab = prefab
        self.x, self.y, self.z = x, y, z
        self.scale = scale


def _f(parts: list[str], index: int, default: float) -> float:
    if index >= len(parts):
        return default
    try:
        return float(parts[index])
    except ValueError:
        return default


def parse_blueprint(path: Path) -> dict:
    """Parse one .blueprint or .vbuild file into a plain dict."""
    text = path.read_text("utf-8", "replace")
    rows = text.splitlines()
    meta: dict[str, str] = {}
    pieces: list[Piece] = []
    snap_points = 0
    terrain_height = 0
    terrain_paint = 0
    suffix = path.suffix.lower()

    if suffix == ".vbuild":
        # Legacy BuildShare. MEASURED from InfinityHammer.dll
        # `HammerBlueprintCommand::GetBuildShareObject`: the row is split on a
        # single space with empty entries KEPT, so the layout is fixed-arity and
        # a zero component serialises as an empty field:
        #     0       prefab name
        #     1..4    rotation quaternion x, y, z, w   (default 0)
        #     5..7    position x, y, z                 (default 0)
        #     8       extra info                       (default "")
        #     9       placement chance                 (default 1)
        # There is no scale column; Infinity Hammer forces Vector3.one.
        # Splitting on whitespace *runs* silently drops the empty fields and
        # shifts every column, which is why this must be `split(" ")`.
        for row in rows:
            line = row.rstrip("\r")
            if not line.strip():
                continue
            parts = line.replace(",", ".").split(" ") if "," in line else line.split(" ")
            prefab = parts[0].strip()
            if not prefab:
                continue
            pieces.append(
                Piece(prefab, _f(parts, 5, 0.0), _f(parts, 6, 0.0), _f(parts, 7, 0.0), (1.0, 1.0, 1.0))
            )
        return _finish(path, meta, pieces, snap_points, terrain_height, terrain_paint, "vbuild")

    section: str | None = "#pieces"  # a headerless .blueprint is all pieces
    discarded = 0
    unknown: list[str] = []
    legacy = False
    for row in rows:
        if not row:
            continue
        if row.startswith("#"):
            try:
                section, key = header_section(row, section)
            except LegacyTerrainFormat:
                legacy = True
                section = None
                continue
            if key is not None:
                meta[key] = row[len(key) + 2 :].strip()
            elif section is None:
                unknown.append(row.split(":", 1)[0].strip())
            continue
        if section is None:
            # inside an unknown section: Infinity Hammer throws these rows away,
            # so counting them as pieces would invent content that never places
            discarded += 1
            continue
        if section == "#snappoints":
            snap_points += 1
            continue
        if section == "#terrainheight:":
            terrain_height += 1
            continue
        if section == "#terrainpaint:":
            terrain_paint += 1
            continue
        line = row.replace(",", ".") if "," in row else row
        parts = line.split(";")
        prefab = parts[0].strip()
        if not prefab:
            continue
        pieces.append(
            Piece(
                prefab,
                _f(parts, 2, 0.0),
                _f(parts, 3, 0.0),
                _f(parts, 4, 0.0),
                (_f(parts, 10, 1.0), _f(parts, 11, 1.0), _f(parts, 12, 1.0)),
            )
        )
    return _finish(
        path,
        meta,
        pieces,
        snap_points,
        terrain_height,
        terrain_paint,
        "blueprint",
        discarded_rows=discarded,
        unknown_sections=unknown,
        legacy_terrain=legacy,
    )


def _finish(
    path,
    meta,
    pieces,
    snap_points,
    terrain_height,
    terrain_paint,
    fmt,
    discarded_rows: int = 0,
    unknown_sections: list | None = None,
    legacy_terrain: bool = False,
) -> dict:
    if pieces:
        xs = [p.x for p in pieces]
        ys = [p.y for p in pieces]
        zs = [p.z for p in pieces]
        footprint = (
            round(max(xs) - min(xs), 1),
            round(max(zs) - min(zs), 1),
            round(max(ys) - min(ys), 1),
        )
    else:
        footprint = (0.0, 0.0, 0.0)
    counts: dict[str, int] = {}
    for p in pieces:
        counts[p.prefab] = counts.get(p.prefab, 0) + 1
    return {
        "file": path.name,
        "path": str(path),
        "format": fmt,
        "bytes": path.stat().st_size,
        "name": meta.get("name", ""),
        "creator": meta.get("creator", ""),
        "description": meta.get("description", ""),
        "category": meta.get("category", ""),
        "coordinates": meta.get("coordinates", ""),
        "center_piece": meta.get("center", ""),
        "pieces": len(pieces),
        "distinct_prefabs": len(counts),
        "footprint_xzy": list(footprint),
        "snap_points": snap_points,
        "terrain_height_rows": terrain_height,
        "terrain_paint_rows": terrain_paint,
        "prefab_counts": counts,
        # rows Infinity Hammer throws away because they sit under a header it
        # does not recognise. Non-zero means the file carries a section written
        # by some other tool -- PlanBuild's `#Terrain` is the common case -- and
        # those rows are NOT pieces however much they look like them.
        "discarded_rows": discarded_rows,
        "unknown_sections": sorted(set(unknown_sections or ())),
        # the file does not load AT ALL in Infinity Hammer 1.83.0
        "legacy_terrain_format": legacy_terrain,
    }


# --------------------------------------------------------------------------
def load_evidence(path: Path) -> tuple[set[str], dict[str, set[str]]]:
    data = json.loads(path.read_text("utf-8"))
    vanilla = set(data["vanilla"])
    mods = {k: set(v) for k, v in data.get("mods", {}).items()}
    return vanilla, mods


def resolve(prefab: str, vanilla: set[str], mods: dict[str, set[str]]) -> str:
    if prefab in vanilla:
        return "vanilla"
    owners = [name for name, names in mods.items() if prefab in names]
    if owners:
        return "mod:" + "|".join(sorted(owners)[:3])
    return "MISSING"


# Blueprints capture whatever the author had selected, and a PlanBuild-format
# file can therefore carry objects that are not build pieces at all.  Some of
# those are harmless flavour (a stew on a table, a ward); some hand the party
# free progression.  A blueprint with hundreds of `TreasureChest_mountaincave`
# entries is a loot-spam exploit, not a base, and placing it would wreck a
# tiered jumpstart.  These patterns are reported per file so the caller can
# decide rather than discover it in-game.
LOOT_RE = re.compile(r"^(TreasureChest_|Pickable_.*(Core|Stand|Egg|Meteorite|Crystal|Remains))", re.I)
SPAWN_RE = re.compile(
    r"^(Spawner|Deer$|Boar$|Boar_piggy$|Greydwarf|Skeleton|Draugr|Troll$|Wraith$|Blob$|BlobTar$"
    r"|Surtling$|Goblin$|GoblinBrute$|GoblinShaman$|Seeker|Lox$|Wolf$|Serpent$|Neck$|Leech$"
    r"|Ghost$|Abomination$|Charred(Melee|Archer|Mage|Twitcher)|Asksvin$|Morgen$|FallenValkyrie$"
    r"|Hen$|Chicken$|Hatchling$)",
    re.I,
)
FORAGE_RE = re.compile(r"^Pickable_", re.I)
WARD_RE = re.compile(r"^(guard_stone|piece_guardstone)$", re.I)


def scan_payload(report: dict) -> None:
    """Split the object list into build pieces and everything else."""
    loot: dict[str, int] = {}
    spawns: dict[str, int] = {}
    forage: dict[str, int] = {}
    wards = 0
    for prefab, count in report["prefab_counts"].items():
        if WARD_RE.match(prefab):
            wards += count
        elif LOOT_RE.match(prefab):
            loot[prefab] = count
        elif SPAWN_RE.match(prefab):
            spawns[prefab] = count
        elif FORAGE_RE.match(prefab):
            forage[prefab] = count
    report["loot_objects"] = dict(sorted(loot.items(), key=lambda kv: -kv[1]))
    report["creature_objects"] = dict(sorted(spawns.items(), key=lambda kv: -kv[1]))
    report["forage_objects"] = dict(sorted(forage.items(), key=lambda kv: -kv[1]))
    report["ward_pieces"] = wards
    report["loot_count"] = sum(loot.values())
    report["creature_count"] = sum(spawns.values())
    # A build whose pieces sit thousands of metres above or below the rest is
    # not a build: it is a capture artefact, and the vertical span proves it.
    report["loot_exploit"] = report["loot_count"] >= 20 or report["footprint_xzy"][2] > 200


def classify(report: dict, vanilla, mods) -> None:
    res: dict[str, str] = {}
    missing: dict[str, int] = {}
    modded: dict[str, int] = {}
    for prefab, count in report["prefab_counts"].items():
        verdict = resolve(prefab, vanilla, mods)
        res[prefab] = verdict
        if verdict == "MISSING":
            missing[prefab] = count
        elif verdict != "vanilla":
            modded[prefab] = count
    report["resolution"] = res
    report["missing_prefabs"] = dict(sorted(missing.items(), key=lambda kv: -kv[1]))
    report["mod_prefabs"] = dict(sorted(modded.items(), key=lambda kv: -kv[1]))
    total = max(report["pieces"], 1)
    report["missing_pieces"] = sum(missing.values())
    report["missing_pct"] = round(100.0 * sum(missing.values()) / total, 2)
    report["mod_pieces"] = sum(modded.values())
    if report["pieces"] == 0:
        report["verdict"] = "EMPTY"
    elif not missing and not modded:
        report["verdict"] = "PLACES_CLEAN"
    elif not missing:
        report["verdict"] = "PLACES_MOD_DEPENDENT"
    elif report["missing_pct"] < 1.0:
        report["verdict"] = "PLACES_WITH_GAPS"
    else:
        report["verdict"] = "BROKEN"


def collect(targets: list[str]) -> list[Path]:
    out: list[Path] = []
    for t in targets:
        p = Path(t)
        if p.is_dir():
            out += sorted(p.rglob("*.blueprint"))
            out += sorted(p.rglob("*.vbuild"))
        elif p.is_file():
            out.append(p)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("targets", nargs="+")
    ap.add_argument("--evidence", default=str(HERE / "data" / "piece_prefabs.json"))
    ap.add_argument("--json", help="write the full report here")
    ap.add_argument(
        "--prune-evidence",
        help="write a small evidence file covering only the prefabs these blueprints use",
    )
    ap.add_argument("--sort", default="pieces", choices=["pieces", "name", "missing"])
    args = ap.parse_args(argv)

    evidence = Path(args.evidence)
    if not evidence.exists():
        print(
            f"evidence file {evidence} not found -- run scan_piece_prefabs.py first",
            file=sys.stderr,
        )
        return 2
    vanilla, mods = load_evidence(evidence)

    files = collect(args.targets)
    if not files:
        print("no blueprint files found", file=sys.stderr)
        return 1

    reports = []
    for path in files:
        try:
            rep = parse_blueprint(path)
        except Exception as exc:
            print(f"!! {path}: {exc}", file=sys.stderr)
            continue
        scan_payload(rep)
        classify(rep, vanilla, mods)
        reports.append(rep)

    key = {
        "pieces": lambda r: -r["pieces"],
        "name": lambda r: r["file"].lower(),
        "missing": lambda r: -r["missing_pct"],
    }[args.sort]
    reports.sort(key=key)

    hdr = (
        f"{'file':44} {'fmt':9} {'pieces':>7} {'x*z*y footprint':>20} {'miss%':>6} "
        f"{'loot':>5} {'crit':>5} {'verdict':21} creator"
    )
    print(hdr)
    print("-" * len(hdr))
    for r in reports:
        fp = "{}x{}x{}".format(*r["footprint_xzy"])
        loot = str(r["loot_count"]) + ("!" if r["loot_exploit"] else "")
        print(
            f"{r['file'][:44]:44} {r['format']:9} {r['pieces']:7} {fp:>20} "
            f"{r['missing_pct']:6} {loot:>5} {r['creature_count']:5} "
            f"{r['verdict']:21} {r['creator'][:24]}"
        )

    tally: dict[str, int] = {}
    for r in reports:
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    print()
    print("verdicts:", ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    print("files:", len(reports))

    if args.json:
        # `resolution` is the same map for every file; it lives once in
        # prefab_evidence.json instead of 112 times in here.
        slim = [{k: v for k, v in r.items() if k != "resolution"} for r in reports]
        Path(args.json).write_text(json.dumps(slim, indent=1) + "\n", "utf-8")
        print("wrote", args.json)

    if args.prune_evidence:
        used: set[str] = set()
        for r in reports:
            used |= set(r["prefab_counts"])
        out = {
            "note": "pruned from piece_prefabs.json: only prefabs referenced by the "
            "inventoried blueprints. Regenerate the full evidence with scan_piece_prefabs.py.",
            "source_evidence": json.loads(evidence.read_text("utf-8"))["generated_utc"],
            "resolution": {p: resolve(p, vanilla, mods) for p in sorted(used)},
        }
        Path(args.prune_evidence).write_text(json.dumps(out, indent=1) + "\n", "utf-8")
        print("wrote", args.prune_evidence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
