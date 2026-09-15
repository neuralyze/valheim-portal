#!/usr/bin/env python3
"""Put library blueprints where a placer can read them, verifying every hash.

Most of this library is **reference only**: the manifest carries identity,
measurement and a SHA-256, and the body stays out of git because its licence
does not permit redistribution (see PROVENANCE.md). This is the copy-from-source
step that turns a manifest row into a file on disk at use time.

    ./materialise.py --verify                      hash-check every source in place
    ./materialise.py --out <dir> --name speeds-bridge2.blueprint [...]
    ./materialise.py --out <dir> --category bridge portal outpost
    ./materialise.py --out <dir> --modules-only --verdict PLACES_CLEAN

`--out` is normally the Infinity Hammer blueprint folder on the admin client:

    <game>/BepInEx/config/PlanBuild/

MEASURED: Infinity Hammer 1.83.0's `HammerBlueprintCommand::LoadFiles`
enumerates `*.blueprint` and `*.vbuild` with `SearchOption.AllDirectories` from
both `<game>/BepInEx/config/<folder>` and `Paths.ConfigPath/<folder>`, where
`<folder>` is the config key *"6. Blueprints" / "Blueprint folder"* whose default
value is the literal string `PlanBuild`.

## --strip-unknown-sections

MEASURED from the deployed `InfinityHammer.dll`: an unrecognised `#Section`
header (second character not whitespace) sets the parser's section to `None`,
and every row after it is discarded until the next recognised header. So
PlanBuild's `#Terrain` block -- rows `shape;x;y;z;radius;rotation;smooth;` --
costs nothing on the admin-client route beyond losing the captured flattening.

A reader that instead *keeps* the current section reads those rows as pieces
called `circle` and `square`. `../blueprints/inventory.py` does exactly that, so
`to_rcon_plan.py` downstream of it will emit `spawn circle ...` for each one.
`--strip-unknown-sections` removes the block so the headless route cannot trip
over it. It changes the SHA-256, so the stripped copy is written with the
original hash recorded in a `# materialised-from` comment on the first line --
which Infinity Hammer treats as a comment (`row.Length > 1` and `row[1]` is
whitespace), leaving the section state untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
STAGING = HERE / "staging"
BODIES = HERE / "bodies"

RECOGNISED_HEADERS = (
    "#name:", "#creator:", "#description:", "#category:", "#center:",
    "#coordinates:", "#rotation:", "#snappoints", "#pieces",
    "#terrainheight:", "#terrainpaint:",
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_rows() -> list[dict]:
    path = DATA / "library_manifest.json"
    if not path.is_file():
        raise SystemExit("run ./classify.py, ./web.py and ./build_manifest.py first")
    return json.loads(path.read_text("utf-8"))["entries"]


def candidates(row: dict) -> list[Path]:
    """Every place this row's body could be, in preference order."""
    source_name = row.get("source_name") or row["name"]
    out: list[Path] = []
    if row["body"] == "committed":
        out.append(BODIES / row["origin"].replace("/", "__") / source_name)
    for origin in (row.get("origins") or row["origin"]).split(", "):
        origin = origin.strip()
        if origin == "fleet corpus":
            corpus = json.loads((DATA / "corpus_roles.json").read_text("utf-8"))
            for entry in corpus["entries"]:
                if entry["file"] == source_name:
                    out.append(Path(entry["source"]))
        elif origin:
            out.append(STAGING / origin.replace("/", "__") / source_name)
    return out


def locate(row: dict) -> Path | None:
    """Where this row's body lives right now, if anywhere.

    Two rules, and each of them has already cost this project a building.

    1. **A zero-byte candidate never wins.** MEASURED: the `old_Storgard`
       corpus root holds 0-byte stubs for `s-ren-dockhouse.blueprint` and
       `salty-dick-cottage-final.blueprint` while another root holds the real
       101,856 and 83,966-byte bodies. First-candidate-wins resolved both
       buildings to nothing, and the survey downstream reported them as
       FLOORLESS rather than as unresolved -- a confident answer to a question
       that was never measured.
    2. **A row with no hash resolves to nothing.** Ten rows are `resolvable:
       false` with an empty `sha256` (see `classify.sha256` for why the empty
       string rather than the hash of zero bytes). An empty claim must not be
       satisfiable by an empty file, or `--verify` certifies a truncated
       download as intact.

    Beyond those, the body is required to HASH to what the row claims. A
    candidate that exists but disagrees is not this body.
    """
    if not row.get("sha256"):
        return None
    mismatched: Path | None = None
    for candidate in candidates(row):
        if not candidate.is_file() or candidate.stat().st_size == 0:
            continue
        if sha256(candidate.read_bytes()) == row["sha256"]:
            return candidate
        mismatched = mismatched or candidate
    return mismatched


def locate_by_filename(name: str) -> Path | None:
    """Resolve a body from the file name a placement or a console command uses.

    The library's `name` is a unique HANDLE; a `placements.yaml` `blueprint:`
    field and an Infinity Hammer `hammer_blueprint` argument are both the SOURCE
    file name, which is not guaranteed unique. So this accepts either, and
    REFUSES rather than guesses when the source name covers more than one body:
    `brokkr-the-cathedral.blueprint` is two different cathedrals and
    `PuP_Minicastle.blueprint` is two different castles (MEASURED), and a
    resolver that silently returns one of them is how a survey came to measure a
    3,334-piece body while the manifest claimed a 3,810-piece one.

    Returns None when no body exists. Raises when the name is ambiguous, because
    an ambiguous answer is worse than no answer.
    """
    rows = [r for r in load_rows() if name in (r["name"], r.get("source_name"))]
    if not rows:
        return None
    if len(rows) > 1:
        raise SystemExit(
            f"{name} names {len(rows)} DIFFERENT bodies -- ask for one by handle: "
            + ", ".join(r["name"] for r in rows)
        )
    return locate(rows[0])


def strip_unknown_sections(text: str) -> tuple[str, int, int]:
    """Drop exactly the DATA rows Infinity Hammer would discard.

    An unknown section's header is kept, demoted to a comment (``#`` + space),
    because some of them are author metadata worth preserving -- PiNoKi's builds
    carry ``#LayoutProfile:``, ``#TableZone:``, ``#BedZone:`` and ``#DoorZone:``
    with no rows beneath them at all. Demoted headers parse as comments under the
    measured rule (length > 1, ``row[1]`` is whitespace), so the section state is
    untouched either way; this just keeps the information.
    """
    out: list[str] = []
    dropping = False
    rows_dropped = 0
    headers_demoted = 0
    for row in text.splitlines():
        low = row.lower()
        if low.startswith("#"):
            if any(low.startswith(h) for h in RECOGNISED_HEADERS):
                dropping = False
                out.append(row)
                continue
            if len(row) > 1 and not row[1].isspace():
                dropping = True
                headers_demoted += 1
                out.append("# " + row[1:])
                continue
            out.append(row)  # already a comment; harmless
            continue
        if dropping:
            if row.strip():
                rows_dropped += 1
                continue
            out.append(row)
            continue
        out.append(row)
    return "\n".join(out) + "\n", rows_dropped, headers_demoted


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="destination directory")
    ap.add_argument("--name", nargs="*", default=[],
                    help="library handles, or source file names when unambiguous")
    ap.add_argument("--category", nargs="*", default=[])
    ap.add_argument("--verdict", nargs="*", default=[])
    ap.add_argument("--modules-only", action="store_true")
    ap.add_argument("--verify", action="store_true", help="hash-check sources in place, copy nothing")
    ap.add_argument("--strip-unknown-sections", action="store_true")
    args = ap.parse_args()

    rows = load_rows()
    if args.name:
        # `--name` takes the library HANDLE, and for convenience the source file
        # name too -- but ONLY when that name identifies one body. MEASURED:
        # `brokkr-the-cathedral.blueprint` is two different 8000-piece
        # cathedrals, so accepting it silently means materialising whichever row
        # sorted first. The whole point of the handle is that this refuses.
        wanted = set(args.name)
        by_handle = {r["name"]: r for r in load_rows()}
        ambiguous: dict[str, list[str]] = {}
        for want in sorted(wanted - set(by_handle)):
            hits = [r["name"] for r in load_rows() if r.get("source_name") == want]
            if len(hits) > 1:
                ambiguous[want] = hits
        if ambiguous:
            raise SystemExit(
                "\n".join(
                    f"{want} names {len(hits)} DIFFERENT bodies -- ask for one by handle: "
                    + ", ".join(hits)
                    for want, hits in ambiguous.items()
                )
            )
        rows = [
            r for r in rows
            if r["name"] in wanted or r.get("source_name") in wanted
        ]
        unmatched = wanted - {r["name"] for r in rows} - {
            r.get("source_name") for r in rows
        }
        if unmatched:
            raise SystemExit("no manifest row named: " + ", ".join(sorted(unmatched)))
    if args.category:
        rows = [r for r in rows if r["category"] in set(args.category)]
    if args.verdict:
        rows = [r for r in rows if r["verdict"] in set(args.verdict)]
    if args.modules_only:
        rows = [r for r in rows if r["kind"] == "module"]
    if not rows:
        raise SystemExit("no manifest rows selected")

    if args.verify:
        present = missing = drifted = unresolvable = 0
        for row in rows:
            if not row.get("sha256"):
                unresolvable += 1
                print(f"UNRESOLVABLE  {row['name']}  "
                      f"{row.get('unresolved_reason') or 'no sha256 claimed'}")
                continue
            path = locate(row)
            if path is None:
                missing += 1
                print(f"MISSING  {row['name']}  ({row['origins'] if row.get('origins') else row['origin']})")
                continue
            actual = sha256(path.read_bytes())
            if actual == row["sha256"]:
                present += 1
            else:
                drifted += 1
                print(f"DRIFTED  {row['name']}  manifest {row['sha256'][:12]} != disk {actual[:12]}")
        print(f"{present} verified, {drifted} drifted, {missing} unavailable, "
              f"{unresolvable} unresolvable (no body exists)")
        return 1 if (drifted or missing) else 0

    if not args.out:
        ap.error("--out is required unless --verify is given")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    copied = skipped = 0
    for row in rows:
        if not row.get("sha256"):
            print(f"SKIP {row['name']}: {row.get('unresolved_reason') or 'unresolvable row'}",
                  file=sys.stderr)
            skipped += 1
            continue
        path = locate(row)
        if path is None:
            print(f"SKIP {row['name']}: body unavailable. "
                  + ("run ./web.py --fetch" if row["origin"] != "fleet corpus" else "corpus directory gone"),
                  file=sys.stderr)
            skipped += 1
            continue
        if sha256(path.read_bytes()) != row["sha256"]:
            print(f"SKIP {row['name']}: SHA-256 does not match the manifest", file=sys.stderr)
            skipped += 1
            continue
        # Written under the library HANDLE, not the source name: two bodies that
        # shared a file name have to be able to sit in one blueprint folder, and
        # the handle is what the manifest, the audit and the shortlist all use.
        target = out / row["name"]
        if args.strip_unknown_sections and path.suffix.lower() == ".blueprint":
            text, dropped, demoted = strip_unknown_sections(path.read_text("utf-8", "replace"))
            if dropped or demoted:
                header = (
                    f"# materialised-from sha256 {row['sha256']}"
                    f" -- {dropped} unknown-section data rows stripped,"
                    f" {demoted} unknown headers demoted to comments\n"
                )
                target.write_text(header + text, "utf-8")
                print(f"{row['name']}: stripped {dropped} rows, demoted {demoted} headers")
                copied += 1
                continue
        shutil.copy2(path, target)
        copied += 1
    print(f"{copied} materialised into {out}, {skipped} skipped")
    return 1 if skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
