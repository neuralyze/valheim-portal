#!/usr/bin/env python3
"""Tests for blueprint body IDENTITY -- `library/build_manifest.identity()` and
`library/materialise.locate()`.

Three defects, all the same mistake: treating a file NAME as an identity. Each
one had already produced a confident wrong answer before it was found.

  1. **Two different bodies under one name.** MEASURED on this fleet's own
     disk: `brokkr-the-cathedral.blueprint` is an 8,269-piece cathedral in
     `Oosquai/SavheimIV` and an 8,240-piece one in
     `offsetkeyz/valheim_mod_sync`, with different SHA-256; and
     `PuP_Minicastle.blueprint` is a 352,256-byte / 3,334-row castle in one
     corpus directory and a 404,763-byte / 3,810-row one in the other. A
     name-keyed lookup returned whichever the iteration order reached first, so
     WHICH of two 8,000-piece cathedrals you got depended on directory order --
     and for the Minicastle, the catalogue recorded one body's hash while the
     survey measured the other's geometry.

  2. **Two sources of one body counted twice.** `salty-dick-cottage-final` is
     byte-identical (sha `6b588d9c46e2`) in the corpus and in
     `offsetkeyz/valheim_mod_sync`. That is one body reachable from two places,
     and rowing it twice inflated the library by a row no consumer could ask
     for separately.

  3. **A zero-byte candidate winning over a real body.** MEASURED: the
     `old_Storgard` root holds 0-byte stubs for `s-ren-dockhouse` and
     `salty-dick-cottage-final` while another root holds the real 101,856 and
     83,966-byte bodies. First-candidate-wins resolved both to nothing and the
     downstream survey then reported two real buildings as FLOORLESS.

Everything here is offline: synthetic manifest rows and temp-dir bodies. The
real catalogue is asserted against only for the invariant it must always hold --
that no two rows answer to one handle.
"""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIBRARY = HERE / "jumpstart" / "library"
sys.path.insert(0, str(LIBRARY))

import build_manifest  # noqa: E402
import materialise  # noqa: E402


def _row(name, sha, origin, body="reference", **extra):
    row = {
        "name": name, "sha256": sha, "origin": origin, "body": body,
        "category": "base", "kind": "module", "verdict": "PLACES_CLEAN",
        "resolvable": bool(sha), "unresolved_reason": "" if sha else "zero-byte source",
        "metadata": "curated", "pieces": 1, "footprint": "1x1x1",
        "top_tier": "meadows(1)", "format": "blueprint", "creator": "",
        "licence": "none stated", "role": "", "fit": "", "gaps": "",
    }
    row.update(extra)
    return row


class Identity(unittest.TestCase):
    def test_same_name_different_content_gets_two_distinct_handles(self):
        rows = build_manifest.identity([
            _row("cathedral.blueprint", "3060609d536b" + "0" * 52, "Oosquai/SavheimIV"),
            _row("cathedral.blueprint", "8127bac49f36" + "0" * 52, "offsetkeyz/sync"),
        ])
        self.assertEqual(len(rows), 2)
        handles = sorted(r["name"] for r in rows)
        self.assertEqual(handles, [
            "cathedral__3060609d536b.blueprint",
            "cathedral__8127bac49f36.blueprint",
        ])
        # The handle must be a function of the CONTENT, not of the source, so
        # that reordering the inputs cannot change which body a handle names.
        for r in rows:
            self.assertIn(r["sha256"][:12], r["name"])
            self.assertEqual(r["source_name"], "cathedral.blueprint")

    def test_handles_do_not_depend_on_input_order(self):
        a = _row("cathedral.blueprint", "3060609d536b" + "0" * 52, "Oosquai/SavheimIV")
        b = _row("cathedral.blueprint", "8127bac49f36" + "0" * 52, "offsetkeyz/sync")
        forward = {r["name"]: r["sha256"] for r in build_manifest.identity([a, b])}
        reverse = {r["name"]: r["sha256"] for r in build_manifest.identity([b, a])}
        self.assertEqual(forward, reverse)

    def test_same_name_same_content_merges_to_one_row_listing_both_origins(self):
        sha = "6b588d9c46e2" + "0" * 52
        rows = build_manifest.identity([
            _row("cottage.blueprint", sha, "fleet corpus"),
            _row("cottage.blueprint", sha, "offsetkeyz/sync"),
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "cottage.blueprint")
        self.assertEqual(rows[0]["origins"], "fleet corpus, offsetkeyz/sync")

    def test_a_committed_body_is_the_primary_origin_of_a_merged_row(self):
        """`materialise.locate` tries candidates in order, and a committed body
        is the one it can satisfy without a fetch."""
        sha = "a" * 64
        rows = build_manifest.identity([
            _row("hut.blueprint", sha, "zzz/unfetched"),
            _row("hut.blueprint", sha, "constXife/bygd", body="committed"),
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["origin"], "constXife/bygd")
        self.assertEqual(rows[0]["body"], "committed")

    def test_unresolvable_rows_keep_their_name_and_do_not_merge(self):
        rows = build_manifest.identity([
            _row("stable.blueprint", "", "fleet corpus"),
            _row("tavern.blueprint", "", "fleet corpus"),
        ])
        self.assertEqual(sorted(r["name"] for r in rows),
                         ["stable.blueprint", "tavern.blueprint"])
        self.assertFalse(any(r["resolvable"] for r in rows))

    def test_the_real_catalogue_has_no_two_rows_under_one_handle(self):
        blob = json.loads(
            (LIBRARY / "data" / "library_manifest.json").read_text("utf-8"))
        names = [e["name"] for e in blob["entries"]]
        self.assertEqual(len(names), len(set(names)))
        # And the count the manifest advertises is the count of bodies that
        # exist, not of rows -- the lie this work was opened to fix.
        totals = blob["totals"]
        self.assertEqual(
            totals["resolvable_bodies"] + totals["unresolvable_rows"], totals["rows"])
        self.assertEqual(
            totals["resolvable_bodies"],
            sum(1 for e in blob["entries"] if e["sha256"]))


class Locate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._staging = materialise.STAGING
        materialise.STAGING = self.root
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: setattr(materialise, "STAGING", self._staging))

    def _write(self, origin, name, data: bytes) -> Path:
        d = self.root / origin.replace("/", "__")
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_bytes(data)
        return p

    def test_a_zero_byte_candidate_never_wins_over_a_real_body(self):
        body = b"#Pieces\nwood_floor;;0;0;0;0;0;0;1;;1;1;1\n"
        sha = hashlib.sha256(body).hexdigest()
        self._write("stub/root", "dockhouse.blueprint", b"")
        real = self._write("real/root", "dockhouse.blueprint", body)
        row = _row("dockhouse.blueprint", sha, "stub/root",
                   source_name="dockhouse.blueprint",
                   origins="stub/root, real/root")
        self.assertEqual(materialise.locate(row), real)

    def test_a_row_with_no_hash_resolves_to_nothing_even_beside_an_empty_file(self):
        """An empty claim must not be satisfiable by an empty file, or
        `--verify` certifies a truncated download as intact."""
        self._write("fleet/corpus", "stable.blueprint", b"")
        row = _row("stable.blueprint", "", "fleet/corpus",
                   source_name="stable.blueprint", origins="fleet/corpus")
        self.assertIsNone(materialise.locate(row))

    def test_the_body_must_hash_to_what_the_row_claims(self):
        wanted = b"the right body"
        self._write("some/repo", "x.blueprint", b"a different body entirely")
        row = _row("x.blueprint", hashlib.sha256(wanted).hexdigest(), "some/repo",
                   source_name="x.blueprint", origins="some/repo")
        found = materialise.locate(row)
        # Reported rather than silently dropped: the caller prints DRIFTED, which
        # is a different problem from MISSING and needs a different fix.
        self.assertIsNotNone(found)
        self.assertNotEqual(hashlib.sha256(found.read_bytes()).hexdigest(),
                            row["sha256"])

    def test_two_bodies_under_one_source_name_resolve_to_their_own_content(self):
        one, two = b"cathedral one", b"cathedral two, different"
        self._write("Oosquai/SavheimIV", "cathedral.blueprint", one)
        self._write("offsetkeyz/sync", "cathedral.blueprint", two)
        for blob, origin in ((one, "Oosquai/SavheimIV"), (two, "offsetkeyz/sync")):
            sha = hashlib.sha256(blob).hexdigest()
            row = _row(f"cathedral__{sha[:12]}.blueprint", sha, origin,
                       source_name="cathedral.blueprint",
                       origins="Oosquai/SavheimIV, offsetkeyz/sync")
            found = materialise.locate(row)
            self.assertIsNotNone(found)
            self.assertEqual(found.read_bytes(), blob)


class LocateByFilename(unittest.TestCase):
    def test_an_ambiguous_source_name_raises_instead_of_picking_one(self):
        rows = [
            _row("cathedral__aaaaaaaaaaaa.blueprint", "a" * 64, "one/repo",
                 source_name="cathedral.blueprint"),
            _row("cathedral__bbbbbbbbbbbb.blueprint", "b" * 64, "two/repo",
                 source_name="cathedral.blueprint"),
        ]
        original = materialise.load_rows
        materialise.load_rows = lambda: rows
        try:
            with self.assertRaises(SystemExit) as caught:
                materialise.locate_by_filename("cathedral.blueprint")
        finally:
            materialise.load_rows = original
        message = str(caught.exception)
        self.assertIn("cathedral__aaaaaaaaaaaa.blueprint", message)
        self.assertIn("cathedral__bbbbbbbbbbbb.blueprint", message)

    def test_an_unknown_name_is_none_rather_than_an_error(self):
        original = materialise.load_rows
        materialise.load_rows = lambda: []
        try:
            self.assertIsNone(materialise.locate_by_filename("nothing.blueprint"))
        finally:
            materialise.load_rows = original


if __name__ == "__main__":
    unittest.main()
