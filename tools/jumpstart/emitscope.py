#!/usr/bin/env python3
"""THE SEED-SCOPE REFUSAL, POINTED AT WHAT A RUN WROTE.

`worldseed.require_scope` refuses a foreign artefact on the way IN, and five
inputs are guarded that way.  A run can consume ONLY ITS OWN INPUTS and still
emit an output pinned to another world, BECAUSE NOTHING READS THE OUTPUT BACK.
MEASURED twice within one hour on 2026-09-22, both inside a clean exit 0:

  roads/crossings.yaml stamped `seed: Fresh42` whose `measured_by` names
    /tmp/roads/h1m.bin -- ULFSLAND'S FIELD -- twice inside its own evidence.
    A SELF-CONTRADICTION INSIDE ONE FILE.
  roads/segments.yaml stamped `seed: Fresh42 / world: Fresh42`, POI violations
    0, of which 12,849 of 21,938 m -- 59% OF THE PAVED METREAGE -- terminates
    at one of 8 coordinates that are Ulfsland's, verbatim, to the decimal.
    T1-portalhub-temple ran 830.3 m from (-292.1,213.2), a point chosen for a
    different planet, to Fresh42's OWN derived temple at (-24.1,-30.8): one end
    derived from this seed's land and one end transcribed, IN ONE SEGMENT.

THE PRECEDENT, and before this module it was the only output check in the tree:
`roads/field.py::request` calls `require_seed(out, seed)` AFTER PatchScan --
the same refusal used on inputs, pointed at the output.  ONE REFUSAL, TWO
DIRECTIONS.  This module is that, generalised, with the two rungs the header
read alone demonstrably does not reach.

---------------------------------------------------------------------------
THE PARTITION.  It is NOT input-versus-output.

  SELF-EVIDENCING     the artefact declares something checkable against the
                      run's seed: its header, its citation fields, the paths
                      it names.  ONE POPULATION -- the artefact and the seed.
                      Rungs A and B below.
  REFERENTIAL         coordinates.  A NUMBER CANNOT BE SHOWN FOREIGN BY
                      INSPECTION.  The only legitimate second population is AN
                      INPUT THIS RUN ALREADY HOLDS FOR THIS SEED.  Rung C.

THE LINE: a check that needs ULFSLAND'S PLAN to detect FRESH42'S contamination
is the same defect one level up -- THE SECOND POPULATION BELONGS TO A DIFFERENT
WORLD -- and it passes vacuously on the first world that has no reference.
Nothing here reads another world's artefact.  `placements.yaml` under rung C is
resolved FOR THE SEED BEING PLANNED and for no other.

---------------------------------------------------------------------------
ABSENT IS NOT PERMISSION, AND IT IS A PROPERTY OF THE WHOLE SELF-EVIDENCING
HALF, NOT OF ONE FIELD.  Each rung works by asking the artefact what it claims;
AN ARTEFACT THAT CLAIMS NOTHING ANSWERS NOTHING.  `require_scope` already
refuses an input that declares no seed for exactly this reason.  So:

  a document with no scope header          REFUSED
  a document with no citation field at all REFUSED
  a coordinate record with no provenance   REFUSED
  a citation this run cannot resolve       REFUSED  (cannot-be-shown-in-scope
                                                     is not shown-in-scope)

`roads/north_segments.yaml` is the live instance of the second line: its only
top-level key is `segments`.  A guard whose coverage depends on the artefact
having VOLUNTEERED a field is a guard the artefact can OPT OUT OF.

---------------------------------------------------------------------------
THE CITATION POPULATION IS DECLARED, NOT INFERRED.  `CITATION_KEYS` below is
the set of dict keys whose string value is a CLAIM ABOUT WHERE A NUMBER CAME
FROM.  Harvesting path-shaped tokens out of arbitrary prose instead would make
every explanatory sentence in an emitted document into a citation -- and
`segments.yaml` explains `roads/mod_keepout.json` in prose precisely to say it
is NOT read any more.  A checker that refused that sentence would be reading
the docstring rather than the measurement, which is the error this whole class
is made of.

---------------------------------------------------------------------------
WHAT THIS MODULE CANNOT SEE.  Stated here rather than left to be discovered:

  1. A TRANSFORMED foreign value.  Rung C is EQUALITY -- eight coordinates
     identical to the decimal across two planets is not a distribution, it is a
     transcription, and there is no epsilon anywhere in this file.  A value that
     is Ulfsland-derived but rounded, offset, snapped or recomputed passes clean.
  2. A coordinate whose provenance names ONLY code.  "MEASURED,
     crossings/survey.py::approaches" is a claim about a METHOD; the artefact
     that method wrote is what rung C can open.  An emitter must therefore name
     the FILE, via `source`, and one that names only the tool leaves its
     coordinates in `unreachable`.
  3. A value that is authored, seed-independent, and happens to coincide with
     another world's.  Indistinguishable from contamination by any single-world
     check.  The answer is a DECLARATION (`ANCHORED_*`), not a tolerance -- so
     this population SHRINKS as declarations land, which makes it a ticket
     rather than a footnote.
  4. Values outside `coordinates`: rung C is not applied to constants, ids,
     kinds or roles.  A world does not get its own water level, and a byte
     identity test over every value fires on `water_level_y: 30.0`,
     `snap_m: 2.0` and `DECK_DATUM_Y` on every seed forever.  THE POPULATION IS
     VALUES THAT ARE SUPPOSED TO VARY WITH THE WORLD, AND IT IS DECLARED BY THE
     EMITTER.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import worldseed  # noqa: E402

# A suffix that names MEASURED DATA.  Everything a run writes is either one of
# these or SOURCE, and source is AUTHORED: it survives any seed, so it carries
# no scope and citing it is not a scope claim.
DATA_SUFFIXES = frozenset({".yaml", ".yml", ".json", ".jsonl", ".bin",
                           ".tsv", ".csv", ".db", ".fwl", ".dat"})

# THE DECLARED CITATION POPULATION.  A key here means "the string under me IS a
# path to the artefact these numbers came from" -- a RESOLVED path, not a
# sentence about one.  Both measured instances live in this set:
# `height_source.field` and `provenance.height_field` (the Fresh42
# crossings/segments citing /tmp/roads/h1m.bin), `provenance.locations_dump`
# (the 12,301 Pirate68 keep-outs), and `source` on a node record.
#
# `provenance` and `cites` are DELIBERATELY ABSENT.  They hold prose, and prose
# names files it is denying: `spec.py`'s r3-vestvik reads "... NOT from
# plan.json", and `segments.yaml` explains `roads/mod_keepout.json` at length
# precisely to say it is no longer read.  A checker that harvested those would
# be reading the docstring instead of the measurement, which is the error this
# whole class is made of.
CITATION_KEYS = frozenset({
    "field", "height_field", "locations_dump", "measured_by", "source",
    "patch_file", "from_file", "input_file",
})


# ---------------------------------------------------------------------------
# READING WHAT AN ARTEFACT SAYS ABOUT ITSELF.  One reader per family, and each
# one is the reader that family's own loader uses -- never a second parse that
# can disagree with it on exactly the seeds where it matters.
# ---------------------------------------------------------------------------
def _yaml():
    import yaml                                             # noqa: PLC0415
    return yaml


def declared_scope(path: Path) -> tuple[str | None, str | None]:
    """`(seed, world)` as THIS FILE declares them, or `(None, None)`.

    `(None, None)` means "it did not say", which rung B treats as a refusal --
    not as permission.
    """
    p = Path(path)
    sfx = p.suffix.lower()
    try:
        if sfx in (".yaml", ".yml"):
            doc = _yaml().safe_load(p.read_text())
            if isinstance(doc, dict):
                return doc.get("seed"), doc.get("world")
            return None, None
        if sfx == ".json":
            import json                                     # noqa: PLC0415
            doc = json.loads(p.read_text())
            if isinstance(doc, dict):
                if doc.get("seed"):
                    return doc.get("seed"), doc.get("world")
                # A generator location dump declares its seed in its own
                # shape; `roads/poi.py` owns that reading.
                roads = HERE / "roads"
                if str(roads) not in sys.path:
                    sys.path.insert(0, str(roads))
                import poi as poimod                        # noqa: PLC0415
                return poimod.dump_seed(doc), None
            return None, None
        if sfx == ".bin":
            roads = HERE / "roads"
            if str(roads) not in sys.path:
                sys.path.insert(0, str(roads))
            import field as fieldmod                        # noqa: PLC0415
            return fieldmod.header(p).get("seed"), None
    except Exception:                                       # noqa: BLE001
        return None, None
    return None, None


def _coords_of(obj, out: set) -> set:
    """Every `(x, z)` this document states, to four decimals.

    A dict carrying both `x` and `z`, a dict carrying `xz`, and a bare
    two-element numeric sequence.  That is the population rung C searches a
    CITED artefact over; it is deliberately wider than the population rung C
    searches the EMITTED artefact over, because a citation is honoured wherever
    in the cited file the number actually sits.
    """
    if isinstance(obj, dict):
        if "x" in obj and "z" in obj:
            try:
                out.add((round(float(obj["x"]), 4), round(float(obj["z"]), 4)))
            except (TypeError, ValueError):
                pass
        for v in obj.values():
            _coords_of(v, out)
    elif isinstance(obj, (list, tuple)):
        if len(obj) == 2 and all(isinstance(v, (int, float))
                                 and not isinstance(v, bool) for v in obj):
            out.add((round(float(obj[0]), 4), round(float(obj[1]), 4)))
        for v in obj:
            _coords_of(v, out)
    return out


_COORD_CACHE: dict[str, frozenset] = {}


def coordinates_in(path: Path) -> frozenset:
    """Every coordinate a cited artefact states.  Cached: rung C asks once per
    file and the 9 `placements.yaml` are re-asked for every node."""
    key = str(path)
    if key not in _COORD_CACHE:
        p = Path(path)
        try:
            if p.suffix.lower() in (".yaml", ".yml"):
                doc = _yaml().safe_load(p.read_text())
            else:
                import json                                 # noqa: PLC0415
                doc = json.loads(p.read_text())
        except Exception:                                   # noqa: BLE001
            doc = None
        _COORD_CACHE[key] = frozenset(_coords_of(doc, set()))
    return _COORD_CACHE[key]


# ---------------------------------------------------------------------------
# RESOLVING A CITATION FOR THE SEED BEING PLANNED.
#
# USE THE READERS' OWN RESOLVERS, NEVER A FIXED PATH.  A gate that hardcodes
# `roads/geography.yaml` scope-checks a DIFFERENT FILE than a fresh-seed
# planner opens, and reports green about a file nobody read.
# ---------------------------------------------------------------------------
def resolve_citation(token: str, *, seed: str, world: str,
                     root: Path | None = None) -> list[Path]:
    """Every file this citation names, FOR THIS SEED AND WORLD.

    An empty list means UNRESOLVABLE, which rung B and rung C both treat as a
    refusal: a citation this run cannot open has not been shown to be in scope.
    """
    root = Path(root) if root is not None else HERE
    tok = token.strip().strip(",;")
    if not tok:
        return []

    # A CITATION IS WRITTEN FOR EVERY SEED, SO IT IS WRITTEN AS A TEMPLATE, and
    # the run resolves it for the world it is actually planning.  `spec.py`
    # cites `worlds/<world>/*/placements.yaml` precisely so that no seed can
    # inherit another's directory by transcription -- resolving the placeholder
    # here is what makes that citation checkable rather than decorative.
    tok = tok.replace("<world>", str(world)).replace("<seed>", str(seed))
    name = tok.rsplit("/", 1)[-1]
    if Path(name).suffix.lower() not in DATA_SUFFIXES:
        return []

    if "*" in tok or "?" in tok:
        base = Path(tok)
        if base.is_absolute():
            return sorted(Path(base.anchor).glob(
                str(base.relative_to(base.anchor))))
        for b in (root, root.parent, root.parent.parent):
            hits = sorted(b.glob(tok))
            if hits:
                return hits
        return []

    # A bare artefact NAME is a family, and the family's own resolver decides
    # which file this seed's run would open.
    if "/" not in tok:
        if name == "placements.yaml":
            d = root / "worlds" / world
            return sorted(d.glob("*/placements.yaml")) if d.is_dir() else []
        if name.startswith("geography.") and name.endswith(".yaml"):
            roads = root / "roads"
            if str(roads) not in sys.path:
                sys.path.insert(0, str(roads))
            import geography as geomod                      # noqa: PLC0415
            p = geomod.geography_path(seed)
            return [p] if p.exists() else []
        if name == "sites.yaml":
            p = worldseed.sites_path(seed)
            return [p] if p.exists() else []
        return []

    p = Path(tok)
    if not p.is_absolute():
        for base in (root, root.parent, root.parent.parent):
            if (base / p).exists():
                return [base / p]
        return []
    return [p] if p.exists() else []


def _citations(obj, out: list, where: str = "") -> list:
    """Every DECLARED citation in a document, as `(where, token)`."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            w = f"{where}.{k}" if where else str(k)
            if k in CITATION_KEYS and isinstance(v, str):
                for tok in v.replace(",", " ").split():
                    if Path(tok.rsplit("/", 1)[-1]).suffix.lower() in DATA_SUFFIXES:
                        out.append((w, tok))
            _citations(v, out, w)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _citations(v, out, f"{where}[{i}]")
    return out


def citations(doc) -> list[tuple[str, str]]:
    """The declared citation population of one emitted document."""
    return _citations(doc, [])


# ---------------------------------------------------------------------------
# THE THREE RUNGS.
# ---------------------------------------------------------------------------
def audit(label: str, doc, *, seed: str, world: str,
          coordinates: str | None = None,
          root: Path | None = None) -> dict:
    """Scope-check ONE EMITTED DOCUMENT.  Reports; never raises.

    `coordinates` names the key holding the records rung C is applied to -- the
    values THIS EMITTER DECLARES are supposed to vary with the world.  Each
    record is `{"xz": [x, z], "cites": <str>, "source": <str|None>, ...}`.

    Returns `{"label", "rows", "violations", "unreachable", "counts"}`.
    """
    rows: list[dict] = []
    viol: list[str] = []
    unreachable: list[dict] = []

    # --- RUNG A: THE HEADER.  Necessary and demonstrably NOT sufficient: the
    # artefact that motivated this module stamps `seed: Fresh42` correctly.
    got_s = doc.get("seed") if isinstance(doc, dict) else None
    got_w = doc.get("world") if isinstance(doc, dict) else None
    if not got_s:
        viol.append(f"{label}: declares no seed. An output that does not say "
                    f"which world it describes is how one world's coordinates "
                    f"end up in another's plan.")
    elif str(got_s) != str(seed):
        viol.append(f"{label}: stamps seed {got_s!r} but this run planned "
                    f"{seed!r}.")
    if got_w and str(got_w) != str(world):
        viol.append(f"{label}: stamps world {got_w!r} but this run built "
                    f"{world!r}.")
    rows.append({"rung": "A-header", "seed": got_s, "world": got_w,
                 "ok": not viol})

    # --- RUNG B: THE CITATION RULE, AT THE POINT OF EMISSION.
    # A DERIVED ARTEFACT INHERITS THE SCOPE OF EVERY ARTEFACT IT CITES.  A
    # `measured_by` naming another seed's file is a scope violation EVEN WHEN
    # THE NUMBERS WERE COMPUTED CORRECTLY.
    cites = citations(doc)
    if not cites:
        viol.append(f"{label}: names no artefact in any citation field "
                    f"({sorted(CITATION_KEYS)}). It claims nothing, so nothing "
                    f"about it can be checked -- and absent is not permission.")
    for where, tok in cites:
        found = resolve_citation(tok, seed=seed, world=world, root=root)
        if not found:
            viol.append(f"{label}: {where} cites {tok!r}, which this run cannot "
                        f"resolve for seed {seed!r} / world {world!r}. A "
                        f"citation that cannot be opened has not been shown to "
                        f"be in scope.")
            rows.append({"rung": "B-citation", "where": where, "cites": tok,
                         "resolved": None, "ok": False})
            continue
        for p in found:
            ds, dw = declared_scope(p)
            ok = bool(ds) and str(ds) == str(seed) and (
                not dw or not world or str(dw) == str(world))
            if not ok:
                viol.append(
                    f"{label}: {where} cites {p}, which declares seed {ds!r} / "
                    f"world {dw!r} while this output declares seed {seed!r} / "
                    f"world {world!r}. A derived artefact inherits the scope of "
                    f"every artefact it cites.")
            rows.append({"rung": "B-citation", "where": where, "cites": tok,
                         "resolved": str(p), "declared_seed": ds,
                         "declared_world": dw, "ok": ok})

    # --- RUNG C: THE COORDINATES, AGAINST AN ARTEFACT THIS RUN ALREADY HOLDS
    # FOR THIS SEED, AND AGAINST NOTHING ELSE.
    #
    # A RECORD IS READ OFF `source` -- THE ARTEFACT THAT WAS ACTUALLY OPENED --
    # AND NEVER OUT OF THE PROSE.  `spec.py`'s r3-vestvik provenance contains
    # the words "NOT from plan.json"; a checker that harvested file names from
    # the sentence would read that as a citation of plan.json and refuse a
    # correct record over a word in an explanation.  READ THE DECLARATION OFF
    # THE THING THAT WAS USED, NEVER OFF THE THING THAT WAS MEANT.
    #
    # `verbatim` is the emitter's statement that the coordinate was COPIED out
    # of `source` without transformation, and it is the only class equality can
    # decide.  MEASURED, and it is why the flag exists rather than testing
    # every record: `wtspawn` stands at (110.0,-104.0) while `sites.yaml` says
    # (114.0,-108.0) -- the road node is the site's APPROACH, derived from the
    # centre, and demanding equality there refuses a correct artefact.  A
    # transformed value is NAMED UNREACHABLE, not quietly passed and not
    # tolerance-checked: a tolerance standing in for a population is how an
    # exclusion list starts.
    recs = (doc.get(coordinates) if coordinates and isinstance(doc, dict)
            else None)
    if coordinates and not isinstance(recs, dict):
        viol.append(f"{label}: carries no {coordinates!r} block, so not one of "
                    f"its coordinates says where it came from. The emitting "
                    f"stage declares the fields a seed-scoped artefact must "
                    f"carry, and emission refuses if any is missing.")
        recs = {}
    for nid, rec in (recs or {}).items():
        xz = rec.get("xz")
        try:
            key = (round(float(xz[0]), 4), round(float(xz[1]), 4))
        except (TypeError, ValueError, IndexError):
            viol.append(f"{label}: {coordinates}.{nid} states no usable xz.")
            continue
        src = rec.get("source")
        if not (src or rec.get("cites")):
            viol.append(f"{label}: {coordinates}.{nid} at {key} carries no "
                        f"provenance at all. A coordinate that claims nothing "
                        f"answers nothing, and absent is not permission.")
            continue
        if not src:
            unreachable.append({"id": nid, "xz": list(key),
                                "cites": rec.get("cites"),
                                "why": "provenance names a method, a hub "
                                       "publication or a live reading, not an "
                                       "artefact this run can open"})
            rows.append({"rung": "C-coordinate", "id": nid, "xz": list(key),
                         "ok": None, "unreachable": True})
            continue
        opened = resolve_citation(src, seed=seed, world=world, root=root)
        if not opened:
            viol.append(
                f"{label}: {coordinates}.{nid} at {key} says it came from "
                f"{src!r}, which does not exist for seed {seed!r} / world "
                f"{world!r}. The coordinate is a number this run cannot "
                f"attribute to any artefact it holds -- which is exactly what "
                f"8 transcribed installation nodes looked like on Fresh42, "
                f"where they carried 11 of 20 segments and 59% of the metreage.")
            rows.append({"rung": "C-coordinate", "id": nid, "xz": list(key),
                         "ok": False, "resolved": []})
            continue
        if not rec.get("verbatim"):
            unreachable.append({"id": nid, "xz": list(key),
                                "source": str(opened[0]),
                                "why": "derived from the cited artefact, not "
                                       "copied from it: equality is the wrong "
                                       "question and there is no epsilon here"})
            rows.append({"rung": "C-coordinate", "id": nid, "xz": list(key),
                         "ok": None, "unreachable": True,
                         "resolved": [str(p) for p in opened]})
            continue
        cand = {key}
        raw = rec.get("raw_xz")
        if raw:
            try:
                cand.add((round(float(raw[0]), 4), round(float(raw[1]), 4)))
            except (TypeError, ValueError, IndexError):
                pass
        present = any(c in coordinates_in(p) for p in opened for c in cand)
        if not present:
            viol.append(
                f"{label}: {coordinates}.{nid} states {key} and says it was "
                f"copied verbatim out of {[str(p) for p in opened]}, AND THAT "
                f"COORDINATE IS NOT IN THEM. A citation is a claim about where "
                f"a number came from; it was checked by looking there.")
        rows.append({"rung": "C-coordinate", "id": nid, "xz": list(key),
                     "ok": present, "verbatim": True,
                     "resolved": [str(p) for p in opened]})

    cr = [r for r in rows if r["rung"] == "C-coordinate"]
    return {
        "label": label, "rows": rows, "violations": viol,
        "unreachable": unreachable,
        "counts": {
            "citations": len(cites),
            "citations_ok": sum(1 for r in rows
                                if r["rung"] == "B-citation" and r["ok"]),
            "coordinates": len(cr),
            "coordinates_attributed": sum(1 for r in cr if r["ok"] is True),
            "coordinates_unreachable": len(unreachable),
            "violations": len(viol),
        },
    }


def require_emission_scope(label: str, path, doc, *, seed: str | None = None,
                           world: str | None = None,
                           coordinates: str | None = None,
                           root: Path | None = None) -> dict:
    """REFUSE to write an output that is not a document about THIS world.

    Fails CLOSED, by raising, exactly as `worldseed.require_scope` does on the
    way in.  ONE REFUSAL, TWO DIRECTIONS.
    """
    s = seed if seed is not None else worldseed.seed()
    w = world if world is not None else worldseed.world()
    rep = audit(label, doc, seed=s, world=w, coordinates=coordinates, root=root)
    if rep["violations"]:
        lines = "\n  ".join(rep["violations"][:20])
        more = ("" if len(rep["violations"]) <= 20
                else f"\n  ... and {len(rep['violations']) - 20} more")
        raise SystemExit(
            f"REFUSING TO WRITE {path}: this run planned seed {s!r} / world "
            f"{w!r} and the document it is about to emit is not a document "
            f"about that world.\n  {lines}{more}\n"
            f"MEASURED 2026-09-22: 12,849 of 21,938 m of a Fresh42 road plan -- "
            f"59% of the paved metreage -- terminated at Ulfsland building "
            f"coordinates inside a run that stamped `seed: Fresh42` and exited "
            f"0. Nothing read the output back.")
    rep["path"] = str(path)
    return rep
