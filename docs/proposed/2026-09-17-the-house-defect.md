# The house defect: a check that answers a question it is not measuring

Written 2026-09-17 after a session that found this shape more than twenty times, in
tooling, in mods, in the game, and in the instructions the orchestrator gave its own
agents. It is recorded because the SHAPE recurs while the instances all look unrelated,
and because three separate agents independently named it the house defect rather than
anyone's lapse.

Every entry below is something that was MEASURED and something that a reasonable person
had already believed. None of it is hypothetical.

## The shape

A check runs, returns a confident answer, and the answer is about a different question
than the one the caller asked. Nothing errors. Nothing is obviously wrong. The verdict is
used, and the wrongness surfaces much later as a defect somewhere else entirely.

The cure is always the same and it is the through-line of every instance that got caught:
**re-ask the runtime instead of re-reading a derived number.**

## The hardest variant: a name that survived onto different storage

Valheim 1.0 did not delete `PieceTable.m_availablePieces`. It **reused the name** for a
`HashSet<Piece>` and moved the old `List<List<Piece>>` to `m_availablePiecesByCategory`.

So by-name reflection SUCCEEDS and only the signature fails.

- A rename you can find with grep.
- A name that survived onto different storage you can only find by asking the runtime.

Four of ten bundled `PieceManager` copies read the old signature. The broken prefix sat
second in the patch order on `PieceTable.UpdateAvailable`, so it aborted the chain for
everything behind it - including three correct copies and our own compatibility shim's
correct sizing. Result: `m_availablePiecesByCategory.Count = 0` on all 8 piece tables,
every call, every table. The build menu never populated. The exception names the field:

```
System.MissingFieldException: Field not found:
  List`1<List`1<Piece>> PieceTable.m_availablePieces
from (wrapper dynamic-method) PieceTable.DMD<PieceTable::UpdateAvailable>
```

The same variant caused a second, unrelated error the same night: a TSV column HEADER was
correct while the position it resolved to was not, so `m_notOnTiltingSurface` was read in
the `m_notOnWood` slot.

## Instances, grouped by what the check substituted

### A name for a thing
- A keyword scan for the craft-from-chests mod missed `AzuCraftyBoxes` and the absence of
  a match was read as the absence of a mod.
- A prefix filter over a mod bundle returned 31 noise hits out of 55,789 strings; reading
  the bundle's own container index gave the authoritative 94 prefab paths.
- A compatibility guard named two "offenders" reflecting on a field; both were its own
  members, which merely contained the string literal. A string literal is not a
  `Type.GetField`.
- A drawer mod's config was attributed to the installed drawer mod. Different GUIDs.

### A config for an installed mod
Eleven orphaned configs found in one session. `deploy_server_config` only COPIES, never
deletes, so every mod ever removed leaves its settings behind in all five worlds.
- The inventory mod's config was read as evidence its features were merely switched off.
  The mod was absent from all 20 current editions. **No config value could have produced
  the feature.**
- A fishing mod's server config was deployed to five worlds with no plugin to read it.
- 196 of a briefed piece count belonged to mods that are not installed at all.

**Rule: a config file is not evidence a mod is installed. Check the plugin.**

### A subset for the fleet
- A "439 pieces collapsed onto one category id" finding was an artefact of a sandbox
  staged with six mods. Measured with all 121 DLLs loaded, every id is distinct and the
  real defect was one mod and one tab.
- A hull filter read a `mesh_render` bound of 11.64 x 35.43 m for a ship whose hull is
  9.16 x 21.57 m, because the bound included a water-ripple plane. It would have
  oversized every slip by 60%.

**Rule: a sandbox with a subset of plugins measures a different game.**

### A bounding box for a surface
- Tower beacons were placed 5.173 m in the air because 15.402 m is a tower's mesh AABB
  while its parapet platform is a collider at 10.229 m.
- Two visually identical torches differ by 0.602 m of pure prefab authoring: one's pivot
  is at its foot, the other's is 0.654 m up the shaft.

**Rule: seat on the surface a player stands on, plus the prefab's own measured pivot
offset. A number that is correct about the wrong solid is not a measurement of the thing
you asked about.**

### History for state
- Counting `portal` ledger records per tag reported 19 of 20 tags as one-ended. Nineteen
  of them had three records and two live ends - the third was a relocation. Subtracting
  retirements gives the right answer: every tag is two-ended.
- A verified-removal set taken from a run's own emit output would have reported 764
  already-removed objects as still standing; the ledger holds the truth.

### A threshold for the quantity it bounds
Five times a threshold argument turned out to be a defect in the thing being thresholded:
a 3.6 m station range, an inferred 11 m POI overshoot, a 0.5 m transverse step, a
`>= 1` versus `== 1` zone-control count, and a batter grade that was being compared
against `grade + the hillside's own slope`.

**Rule: when a limit needs tuning to make correct output pass, suspect the measurement.**

### An exit code for an outcome
- `update --apply` returned `rc=0 / updated=1` while the pin never moved, because the
  download timed out and the failure was swallowed.

**Rule: verify the artefact, never the exit code.**

### A silent narrowing for a refusal
Four instances, and the phrase that names it is **A SHRINK IS SILENT**:
- A blocked removal disc was shrunk CONCENTRICALLY, so a blocker near its centre erased
  it: a 12.57 m disc became 3.84 m, removed 4 objects, abandoned 14, and recorded no skip
  reason because nothing was skipped.
- A guard narrowed a request rather than refusing it.
- A batter ray terminated at the lattice edge with no clip and no residual.
- An audit read post-shrink state and reported a disc as unable to reach what a
  pre-shrink disc did reach.

**Rule: any guard that narrows a request rather than refusing it MUST record what it
narrowed and by how much.**

### A datum that existed and was never read
- A road terminated at road freeboard 0.648 m above a dock's `DECK_DATUM_Y`, which exists
  precisely so a join has no step.
- A road's profile stopped 10.4 m below the town datum it was arriving at.
- `step_across_m -7.644` sat in a segment's own metadata, recorded at build time, and
  nothing consulted it.

**Rule: a road must ARRIVE. Solve the terminus against the datum of the thing it meets.**

### A request list for a placed world
- `plan.json`'s location dump holds 12,301 entries and every one reports `placed: false`.
  It is the generator's REQUEST list. Resolving against it put the nearest named location
  20-74 m from clusters sitting 0.3-6.9 m from a real placed marker.

### A district for a footprint
- `pad_radius_m: 100.0` on two town records is a DISTRICT radius. Read as a building
  footprint it made a road refuse to write for 294 m and leave a town behind a 10.4 m
  step. The foundations are the per-building rectangles, worst half-extent 13.05 m.

## The orchestrator's own instances

Recorded because the pattern is not a property of subagents:

- Told four agents that large console replies wedge the server's main thread. The real
  mechanism is a 4096-byte CLIENT buffer plus a log pipe, and the stated cause could not
  explain a `connect()` failing with nothing sent.
- Then poisoned an RCON peer personally with an unbounded 40 m listing for 448 pieces -
  the exact hazard broadcast an hour earlier.
- Read a BepInEx log with one boot header and a current mtime as a per-boot record, and
  drew three wrong conclusions from it, one of which was later found to be RIGHT for a
  different reason.
- Declared that log "unusable" - an over-correction, since it was accurate about what it
  recorded and only unable to date it.
- Stretched one mechanism across two symptoms twice: a category collapse over an upstream
  registration stub, and a piece-table block over two mods that were fine.
- Pre-classified a mod update as DANGEROUS **in the brief**, so the agent inherited the
  verdict instead of measuring one. The changelog was purely additive.
- Counted ledger records and called them live portal ends.
- **Hand-wrote RCON probes without the `consoleCommand` bridge, read the resulting
  `Unknown command` as a regression in a live world, spent an hour diagnosing it, and
  reported it to the operator as broken.** The command surface was fully alive - 443
  registered commands. ValheimRcon's verb registry is closed over its own assembly with
  no console fallback, so a bare verb is rejected by design; `save` and `players` worked
  only because they are two of its 41 own verbs. The project had already recorded this
  exact trap in two source comments (`terraform/place.py:33`,
  `blueprints/to_rcon_plan.py:65`). Every tool in the repo sends the bridge; only the
  hand-written probe did not. **The instrument was the defect, and the subject was
  healthy the whole time** - which is this document's thesis, committed by its author,
  twice on the same world within one hour.

## What actually worked

Every instance above was caught the same way, and it is worth stating as practice rather
than as virtue:

1. **Ask the thing that decides, in a loop.** Where a guard is the authority, call it per
   candidate rather than predicting its verdict. A closed-form shrink radius silently
   skipped 6 cylinders and 127 trees the gate itself passes; bisecting against the gate
   fixed it.
2. **Validate the instrument against a known positive before trusting a clean result.** A
   scanner that finds the four known-bad DLLs and does not flag the new mod is evidence; a
   bare zero is not.
3. **Prove both directions.** A guard that fires on the fixture and passes the legitimate
   case is a guard. One that only fires is a refusal.
4. **Publish the measurement before the fix.** Then a refuted premise costs a paragraph
   rather than a deployment.
5. **Refute your own hypothesis out loud.** Three agents reversed the orchestrator on
   measured evidence in one session, and every reversal was cheaper than the work it
   prevented.
6. **A postcondition is the proof; predicting it is not.** An existence probe expected to
   fail was still run, because a failed postcondition IS the evidence.

## The open item this document cannot close

Three slices landed whose entire subject is what a player PERCEIVES - road lighting, the
build menu grid, and which tabs exist - and no agent had a client session. Every claim
about them is read off prefab assets, IL and object counts.

**One night walk and one hammer-open closes all three.** That is not a gap in the
measurements; it is the boundary of what this kind of measurement can reach.
