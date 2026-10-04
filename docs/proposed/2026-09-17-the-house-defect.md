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

## The consolidated finding, and every instance below is a special case of it

Arrived at jointly by two agents on 2026-09-18, each from its own defect, and stated
better than the framing this document opened with:

> **A well-formed value in the wrong field silently re-routes the question, and the
> report reads identical either way.**

Seven measured instances, all of them ONE QUESTION with TWO AUTHORITIES:

```
two ledger chains                   one absolute path, never named
two lamps.py                        the fix written against the fork, not the tool
                                    that had placed the 138 lamps
nearly two manual sections          two writers, ownership never declared
a portal exempt from three guards   op="spawn" carrying a portal blob; tag
                                    validation, pair_tag and the two-ends
                                    invariant all key off op == "portal"
a road half-width added twice       to a sample set that already WAS the footprint
a 14.5 m bench width                used as a 4.0 m keep-out
a piece list                        recovered by the wrong one of two implementations
```

Note what is NOT wrong in any of those: no value is malformed, no check errors, and every
report reads exactly as it would have read if the value had been right. A `--dry` run over
the mis-opped portal returned **0 schema problems** - not a clean bill, but three checks
that never ran, because all three key off the op.

**Rule: the fix is never a better check - it is ONE AUTHORITY PER QUESTION, named before
first use.** `clearance.Locations.hard_radius` living in exactly one place matters more
than the number it returns, and `op == "portal"` matters more than the portal blob.

### The same shape twice in one night: a clean report from a check that never executed

The portal above was the mild version. The severe one was found in live consequence by the
agent executing the build and then claimed by the author of the line within the hour, with
the attribution corrected in the room rather than left flattering. Both are worth stating
together because they share a mechanism and differ only in what they cost.

A harbour build's `ubuild --dry` reported **48 records, 0 schema problems**, and then the
live run's first stage `save` was **REFUSED by the ledger** on three counts at once -
`params.'reason'` not in the schema, `expect` mandatory and empty on a mutating op, and no
`wire` to replay. The terrain write sat in an append-only chain with **no save behind it
for about six minutes**. The dry run could not have caught it: **the save emit sat inside
`if not args.dry`**, so the 48 world records were validated and **none of the 8 saves
was**. After the fix - `wire=["save"]`, schema-legal params, an `expect` derived from the
stage's own rows, and the emit made UNCONDITIONAL - the same dry run reported **56 records,
0 schema problems, with the 8 saves validated for the first time.**

```
mild   a portal staged op="spawn" -> exempt from tag validation, pair_tag and
       the (tag -> exactly 2 ZDOs) invariant, all three keyed off op
severe a save emitted only when not --dry -> the dry run never validated the
       one record class whose absence does not survive a restart
```

**And the two together sharpen the consolidated finding: the portal was a well-formed
value in the wrong FIELD, the save was a well-formed record on the wrong BRANCH. A
CONDITIONAL IS A FIELD TOO** - `if not args.dry` routed a record to the one place the
instrument validating it could never reach, and the records placed there were the ones
added last and tested least.

**Rule: a dry run that does not validate what the live run SENDS is not a dry run of it.**
And the general form, which is the same sentence the portal produced: **absence of a
complaint from a check that never executed is not evidence.** A guard's silence is
meaningful only where the guard was reachable - so state, per check, what it was asked
about and what it could not see. The repair was also correctly sized rather than
convenient: one `save` record for one missing record, and the stage deliberately NOT
re-run, because the wire is idempotent on the world but a duplicate `terrain_write` in an
append-only chain is something a later reader has to explain.

**And the rule has a narrower, truer form, established by measuring the validator instead
of assuming it** (`ledger/live.py:136-147`): `LiveBuilder.emit` **builds and validates
every record in dry mode** and skips only append, send and verify - its own comment says
that is "the useful half of a dry run". So the validator was never the weak link; it
cannot validate a call that never happens. **Rule: put emits where EVERY mode reaches
them.** Blaming the checker for a branch it was never offered is itself a substitution -
and it was committed, out loud, by a third agent who restated this entry's rule as "dry
runs don't validate", carried it onto its OWN file as a latent defect, and then retracted
it after measuring: `portal_hall.py`'s save emit is at line 921 and sits inside no
conditional at all. **A defect quoted onto a file nobody measured is this document's own
thesis, committed while agreeing with it.** The retraction is recorded because it is worth
more than the accusation was.

Quote the later number, not the earlier one: **56 records / 0 schema problems with the 8
saves validated** is the measurement; the original **48 / 0** is the same file reporting
on a smaller population, and it read identical.

### The eighth form: one authority, two attempts, a conclusion from the first

Distinct from the seven above and disclosed by its own author against itself:

```
zone (-3,65) failed its zones_generate postcondition at 7 of 8 _ZoneCtrl and
  PASSED on a plain retry
that was generalised, (-9,76) was retried the same way twice, and
  "this zone will not generate" was concluded from a SAMPLE OF TWO
the same question was then answered OFF THE SAVE FILE -- m_generatedZones from
  _main.319.db2 -- validated in BOTH directions at 312 of 312 ever-written zones
  present and all 12 post-save zones correctly ABSENT
```

Two attempts is not a sample of a RACE, and the control was already in the evidence: a
deterministic refusal does not pass on a plain retry.

**Rule: one authority per question, named before first use - and when the authority is the
live world, ask whether a FILE already holds the answer.** A wire read is not free: it
contends for the main thread, and it is the only instrument in this project that **leaves
a record behind when it fails** - ledger seq 2665 is that record, unrepaired, with its
author's name on it.

These two and the sub-water-plane instance below are the same shape at three scales - **a
value, a boundary, and a rule**: a true measurement of one flatten, consumed as an answer
to a different question (any write below the plane).

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
- A **64 m ZONE for a 4,450-sample footprint**, and this one was CAUGHT before it cost
  anything, which is why it is worth recording. A portal seat at (1048, 673) sat inside
  zone (16,11), which the dredge record lists among the four zones it wrote to a floor of
  y 24.0 - so by containment the seat was 6 m under water and had to be refused.
  Re-measured against the WRITTEN BYTES instead (`ribbon.delta_at` over the four
  compilers rebuilt from the ledger's own blobs): delta at the seat **exactly 0.000000 m**,
  worst `|delta|` over all four corners of the arch's AABB **0.000000 m**, nearest cut
  sample **13.60 m away** at (1052, 686) with delta -4.132 m, over 927 cut samples probed.
  **"The seat is in a dredged zone" and "the seat was dredged" are different claims and
  only the second one decides.** A correct seat was one containment test away from being
  refused.
  And note WHY that zero is evidence, because a zero usually is not: **a 0.000000 m
  surrounded by zeros proves nothing; a 0.000000 m sitting 13.60 m from a measured
  −4.132 m proves the probe can see the cut and that the seat is outside it.** Same rule
  as the two-instrument entry below - a clean reading counts only where the instrument
  demonstrably could have returned a dirty one. The check was then generalised into
  `seat_guard()`, which re-reads the chain for EVERY `terrain_write` intersecting the
  seat's zones, rebuilds those compilers from their blobs, re-probes the foot and all four
  AABB corners, and quotes the `head_seq` it answered at - so the claim is "no write in
  this build has moved the ground under this foot", pinned to a chain state, rather than
  "the one save I knew about verified".

### A shared name for your own state
The defect is not confined to the product. It is in the harness the agents run on.

- The Python eval kernel is **SHARED between task subagents**. Two agents hit it
  independently within minutes: `HarbourCity` had globals `W` and `fl` rebound mid-survey
  (`W` became a module object, `fl` became a `Field` instance), and `FerryPair` had its
  own `W` - holding `crossings.water` - become a float. Nothing reached disk wrong, but a
  measurement computed across two cells can be computed against another agent's
  variables, with no error and no signal.

**Rule: anything load-bearing runs as a standalone script in ONE invocation - imports,
inputs and computation in a single file. In the shared kernel, prefix every global with
the agent's own name and never rely on a name surviving between cells. Re-derive rather
than remember.**

This is the thesis in its purest form: **state you believe you own, silently rebound from
outside your view.** A variable read from a shared kernel is exactly as untrustworthy as
a config file read as evidence a mod is installed, or a ledger record read as live world
state. Both agents found it the same way the rest of this document was found - a number
changed for no reason they could name, and they stopped to ask why instead of reporting
it.

### A timeout that looks like a measurement
The most dangerous instrument failure found tonight, because its output is clean.

- A prefab-registration probe waited **6000 FRAMES** for `ZNetScene` instead of real
  time. The dedicated server had not loaded a world, so `ZNetScene.instance` was null and
  `m_prefabs` read `-1`. The tool then wrote a **complete, well-formed, fully-populated
  TSV in which every prefab read `znet=0`** - including `fb_fortress_wall`, which is
  demonstrably registered and already standing in the world. The conclusion available
  from that file was that the entire Ravenwood kit is unregistered: the exact opposite of
  the truth, from an output with no error in it.
  Caught only because the author also printed the prefab total and saw `-1`.
- The same shape produced a confident zero from a `find -maxdepth 4` that could not reach
  the path it was asked about, and 95 phantom piece removals from `strings` over an
  embedded asset bundle.

**Rule: a timeout MUST NOT be able to produce a measurement. On timeout, write NOTHING
and fail loudly. Wait on real-time deadlines, not frame counts, and print the population
size beside every result so an empty world is distinguishable from an empty answer.**

A crash is safe: it announces itself. A clean file full of false absences is read,
trusted, and acted on.

### Agreement between two instruments that shared a failure
The hardest instance in this document to notice, because a wrong number looks wrong and
this looked right.

- A survey needed the placement radii of modded world locations, so it ran a **modded**
  location dump to compare against the existing **mod-free** one. The two agreed exactly:
  **12,301 locations, 177 types, identical hash.** That reads as corroboration from two
  independent instruments. It was not. `More_World_Locations_AIO`'s `Awake` had THROWN in
  the sandbox (`LocationQuantityManager.EnsureDefaultsLoaded -> LoadOrMigrateConfigs`,
  wanting a config the sandbox did not stage), so its ~178 locations never registered and
  the "modded" dump was mod-free too. **One instrument was silently disarmed and both
  shared the same blindness.**
- The same survey tried to refine a 32 m blanket stand-off into a measured per-marker
  reach. It returned **57.64-71.45 m — LARGER than the worst case it was meant to
  refine** — because a bounded census of a marker's neighbourhood holds 218-707 objects
  that are overwhelmingly birch, fir and rock. The number was a function of the census
  BOX SIZE, not of the location.

**Rule: agreement between two instruments is evidence ONLY if they were capable of
disagreeing. Prove the second instrument can see what the first cannot before treating a
match as corroboration.**

**SHARPENED, by a later instance that cuts the other way — a pair that WORKED.** A
per-tag portal sweep reported **5 of 14 tags with zero far ends**, on a world where
nothing was wrong: it probed all fourteen at a single default `y=45`, and
`findObjects -near` is a **cube**, so at `r=12` it could only see y 33-57 while those
five ends sit at y 30.60, 102.31, 32.63, 31.59 and 75.70. The false alarm was caught
because a second instrument had already reported 28 portals world-wide — and
`objects_count` is an **XZ cylinder with no height bound**, so it *physically cannot be
y-blind.*

> **Independence is not the property that saves you. NON-SHARED FAILURE MODES is.** Two
> independent implementations of the same blindness catch nothing.

So the pairing rule has an operational form: **know each instrument's blind axis, and
pair instruments whose blind axes are orthogonal.**
```
objects_count id=<p> pos=<x>,<z> max=<r>   XZ cylinder, NO height bound
                                           -> counts a whole PILE COLUMN as N, not 1
                                           -> cannot miss a piece for being at a wrong y
findObjects -near <x> <y> <z> <r>          CUBE, half-extent r in all three axes
                                           -> resolves WHICH y each piece is at
                                           -> WILL miss a deck 12 m above the probe y
```
**Use the cylinder for "is the right NUMBER there", the cube for "is it at the right
HEIGHT", and never let either answer the other's question.** Probe the cube at each
piece's OWN recorded y, never at one default — three separate agents made that exact
mistake with that exact verb in one night.

**THE CONSTRUCTIVE FORM — this is the rule the rest of this document is a list of
failures to follow.** Independence and orthogonal blind axes are both diagnostic; this is
the one you can act on before writing anything:

> **Do not ask whether your checks are independent. Ask what each one is BLIND TO, and
> whether anything else can see it. If nothing can, build the check that would fail on
> that defect — and prove it fails, by running it against the defect.**

Two proofs from one night, constructed rather than observed:
```
FOOTPRINT BOX   synthetic cross slope, grade 2.5, relief 20.00 m over the +/-4 m footprint
  OLD box  0 infeasible spans      <- clean, confident, SILENT
  NEW box  1 infeasible, REFUSES
  truth    worst |dy| 10.000 m against an 8 m clamp
  controls at relief 4.00 m and 12.00 m: BOTH boxes accept, correctly
           -> the new check DISCRIMINATES; it is not merely stricter

GUARD SET       a probe leg driven deliberately into a zone whose defect was known
  location_check clear · schema.validate OK · _zone_clobber_check PASSES
  _pad_footprint_check PASSES · _flatten_forbidden_check REFUSES, 244 problems
           -> four clean and one refusing ON THE SAME INPUT is the proof
```
And the same construction underlies the strongest artefact check of the night: scoring a
terrain read against the bed **reflected across the basin diagonal** gave RMS 0.935 m
against 2.5e-05 m true — **a 37,410x discriminator.** None of these three numbers is
about the artefact. **All three are about the instrument.**

**A version of a check that has only ever passed is indistinguishable from the broken
version it replaced.**

### One word, two populations, and the flattering one
The sharpest instance in this document, because the metric was honest, the number was
correct, and the word attached to it described a different physical quantity.

A road-lighting report said **3,014 of 3,039 stations have "a lamp in view"** — and "in
view" was computed as an unobstructed **sightline at an 80 m cap.** The lamp is a POINT
light with `range = 15.0 m`.
```
the terminus is 26.65 m from the nearest flame
  -> "a lamp in view"  TRUE
  -> illuminated        FALSE, by 11.65 m
```
**A sightline at 80 m is not illumination at 15 m.** Every "dark stretch" figure quoted
from that instrument — 8 m, 40 m, 0 m at various sight caps — is a statement about
whether a flame is VISIBLE, not about whether the ground is LIT. Both readings were
correct measurements; one word carried them both.

**Rule: when a metric's name is a physical claim, check that the name and the computation
refer to the same quantity.** `in_view` and `illuminated` differ by the light's own
`range`, which is available and was not consulted.

This is the same error as a corridor census quoted as a world total and a 2 m station
count quoted as a road — but it is worse in one respect: **the flattering reading is the
one the word suggests**, so nobody re-derives it. It survived a whole night of review by
four agents, including being quoted back to the operator repeatedly by this document's
author.

**And the corollary that cost the same word twice in one hour: once the two populations
are separated, THE FIX CHANGES TOO, not just the claim.** Having used the sightline count
to report "lit end to end", the same agent then proposed *"two or three lamps closing the
last 26.65 m"* — and measured it to be impossible:
```
BIOME_POLICY["Mountain"] min_m 40.0     piece_groundtorch Light POINT range 15.0 m
   m back from the road end   nearest lamp   lit (<=15 m)   placeable (>=40 m)
        0.00                    26.65 m          NO              NO
       10.00                    16.65 m          NO              NO
       15.00                    11.65 m         yes              NO
       20.00                     6.65 m         yes              NO
```
A 40 m minimum around a 15 m light leaves a **25 m annulus unlit BY DESIGN**, and at a
road END nothing exists on the far side to close it. The windows cannot overlap.
**This is the first instance recorded here where a substitution survived being caught
once and reappeared inside the repair.**

**And it forces a distinction between two zeros that look identical:**
```
CLIPPED 0 from a gate with no refusal branch   -> a FALSE PASS. No seat COULD
                                                  have been refused.
a gap no correct gate run can close            -> a TRUE REFUSAL. Structural,
                                                  and "fixing" it means rewriting
                                                  a world-wide rule for one case.
```
**Same shape of number, opposite meaning, and only the MECHANISM tells them apart.** The
second was correctly refused rather than fixed — widening a biome minimum to light one
arch is the `FORD_MAX_DEPTH_M` shape: a real local fix that silently changes a rule
everywhere. The right instrument for a 10 m courtyard is the structure's own fire, not a
corridor-scoped road gate — exactly as a 182 m dock face needed quay torches that the
road's 55 m Meadows minimum would have refused.

### Five passing checks and half the roof open to the sky
The last defect of the session, and the cleanest statement of what a passing suite is
worth.

A shelter's six `wood_roof` panels were pivoted 1 m from the ridge instead of half the
body, so every panel crossed **at** the ridge rather than meeting there:
```
48 of 96 deck samples OPEN TO THE SKY      covered fraction 0.500
both eaves open, 1 m gap to the wall-tops
AND ALL OF THESE PASSED ON IT:
  grounding census        worst air gap, worst burial, by column
  post reach              every column supported
  per-prefab count        tolerance 0, zero strays, zero missing
  the neighbour-arch guard   all four corners still 33.55600, spread 0.0000
  illumination            nearest flame 2.693 m against a 15.0 m range
```
**Not one of them asks what is OVERHEAD.** The suite was not weak; it was complete over a
different set of questions.

> **A full set of passing checks is not coverage. It is the coverage of the checks you
> have.**

The repair is the method: a `cover()` check written afterwards, modelling each panel as
**the sloping plane it is rather than its bounding box** — *a box cannot tell a gable
from an X* — proven against the as-built geometry BEFORE repairing, so its first verdict
was a known-true negative. Then retire by **prefab name**, which makes the neighbouring
arch unreachable by the delete rather than merely outside its radius. 96 of 96 after.

And two more defects caught in the same harness before they shipped, both the familiar
shape: a clash test **silently skipped every natural object** because their geometry was
absent from a local table (a confident zero from an instrument that could not see), and a
census rasterised yaw-90 walls with **unrotated extents**, inventing two floating columns
1 m beyond the body. Ten defect cases written, ten fire.

**Rule: when a measurement intended to TIGHTEN a bound instead LOOSENS it, the instrument
is measuring something else.** That inversion is the cheapest available tell, and it is
what caught the second failure here.

And the division of labour that prevents both: **a screen proposes, a second independent
instrument concludes.** A neighbourhood census is a screen; it was being read as a
verdict.

### A number detached from the population it was measured over
The most common instance in this project, and the one that survives review because
every figure involved is honestly measured. Four agents produced it in one night, in
four different units.

> **A check that samples a centreline cannot certify a 29 m footprint, and a mean over a
> cell cannot certify a sample inside it.**

| the claim | what it was actually true of | how it was caught |
|---|---|---|
| `stations_over_clamp: 0` | the fitted profile at 2 m STATIONS. The rasteriser checks every 1 m sample in the widened 29 m footprint and refused the same leg at 67 samples past the clamp, worst -11.02 m. A 4 m routing cell carries a MEAN, so an 11 m knoll inside one cell is invisible to the search and to a station that misses its crest. | the rasteriser, run before writing |
| "158 torches" | a census at `reach_m = 300` from a road centreline - corridor-scoped, quoted as a world total | an independent world count returned 128, not the predicted 134. The 14 missing were castle lamps 3,406-3,422 m from the nearest road node. `152 recorded - 24 retired = 128` exactly. |
| "128 torches, world total" | true for built content on the day. An XZ cylinder of radius 4,500 m about (500,-300); the northern frontier is 8,296 m out | ruled in by naming every extreme site and its distance. **Its validity expires on the next build.** |
| "worst dark run 40 m, this removal is safe" | a road centreline in a FORKED checkout, re-routed after the fork | re-measuring on canonical geometry after the cull: 40.0 -> 56.0 m. The road was made darker and two lamps had to be restored. |
| "delete radius safe, 2.590 m clearance" | a 3-D cube, while the postcondition is `objects_count` - a 2-D XZ disc | a sibling's 34th record failed: the delete was correct, but a post 1.09 m away in XZ and 2.90 m below in Y was counted |
| "17 untagged portals" | every portal within 30 m of a castle and 40 m of each village centre - a radius about named centres, not a world census | a full census found 19. One missed portal sat 58.9 m from a village centre, outside the disc; the other was inside the disc but outside the y window. |
| "worst unlit stretch 8 m" | ONE segment's junction approach, on the sightline instrument | a second instrument reported 40 m network-wide at 30 m sight, and 0 m at 50 m. **All three are true of the same road.** |

**Rule: every measurement is quoted with its population and its parameters, or it is not
quoted. A census names its bounds; a spacing figure names its sight distance; a clearance
proof names the metric the postcondition uses.**

### A defect invisible to the code path that matters
- A census accumulated results in a list rather than a set, so a ZDO returned by two
  overlapping search volumes appeared twice. It **never affected the gate's verdict** - a
  duplicate still refuses correctly - so no test of the gate's behaviour could have found
  it. It surfaced only when something COUNTED the census: 83 torches reported where 66
  stand.

**Rule: verify with the instrument you are shipping, not with a second one that agrees
with it.** A behavioural test cannot see a defect that behaviour is indifferent to.

### One DIRECTION of an operation, generalised to both — and enforced as an absolute
Added 2026-09-18. The longest-lived instance in this document: it survived for weeks,
every refusal it produced was CORRECT, and it was caught only when someone asked for the
opposite operation and measured it.

The original observation was true and honestly recorded: a dock pad `flatten`ed a
rectangle at `DECK_DATUM_Y` 30.6 over a submerged bed, the water under the footprint went
away, and the pier read as floating forever - the `early-dock` defect. What that measured
was a **RAISE**: ground moved from below y = 30.0 to above it. What was written down was
**"never write terrain below the water plane"** - a statement about the PLANE, from
evidence about the DIRECTION - together with a mechanism nobody had measured: *"cutting
the bank down beside a shoreline drains the water out from under whatever stands over
it."*

MEASURED, one run, both directions, by a purpose-built instrument
(`terraform/WaterProof.cs` + `run_waterproof.sh`, a BepInEx plugin in a sandbox mirror
against a COPY of the world, reading the game's own `Heightmap.GetHeight`,
`Floating.GetLiquidLevel`, `WaterVolume.GetWaterSurface/Depth` and floating real hulls
under physics):

| direction | bed | depth before -> after | outcome |
|---|---|---|---|
| LOWER 7.000 m at (1088, 720) | 23.726 -> 16.726 | 6.274 -> **13.274 m** | a `VikingShip` settles 13.381 m above the new bed, bobbing, indistinguishable from the untouched natural-water control |
| RAISE at (1064, 686) | 27.208 -> 30.900 | 2.792 -> **-0.900** | **DRY GROUND**, `is_under_water` false - the recorded defect, reproduced |
| ring cells 1, 2, 4 and 8 m outside every patch | unchanged | `applied - generated` **EXACTLY 0.0000** | lowering drains no neighbour |

The mechanism, from IL, is why this is a fact rather than a happy accident:
`WaterVolume::GetWaterSurface` returns `transform.position.y + wave + m_surfaceOffset`
and **the heightmap is not in it**; every zone carries a water volume spanning y -20..+40
at `transform.y` 30.0, including a zone measured 0.0% naturally wet; and
`TerrainComp::ApplyToHeightmap` adds `levelDelta + smoothDelta` per sample with no
spatial falloff, with `smoothDelta = 0`.

**The cost of the generalisation was not the refusals - those were right - it was that
the rule became uncorrectable.** It had been restated in **eleven places in one manual**
(prose, a datum table, a curtain-wall refusal, a procedure step, a declaration step, a
one-line invariant) and **two more in another**, plus two source comments. Correcting it
meant finding all of them, and a document right in one section and wrong in its
invariant list is worse than one uniformly wrong.

**Rule: a rule derived from one direction of an operation is a rule about that
direction. Name the direction in the rule, or measure the other one before generalising.**

**Rule: state a load-bearing rule ONCE, with its measurement and its instrument, and cite
it from everywhere else.** A rule repeated in thirteen places has stopped being
checkable. The reason it could not be re-derived is that no document named an instrument
capable of testing it.

And the guard survives with a stateable reason instead of by fiat: the batter's water-edge
refusal is absolute because **a batter beside a shoreline cuts the bank DOWN FROM ABOVE
the plane to the waterline**, which is the removing direction reached from above; the
carriageway's water rule is scoped to protected structures because a causeway must be
possible, and a causeway RAISES a bed. *Do not weaken a correct guard because its stated
reason was wrong; fix the reason.*

**Two instrument traps found in the same work, both of which return a confident WRONG
answer** - this document's own thesis, met twice while measuring it:

- `Floating::GetWaterLevel` reads `s_waterVolumeMask` **with no lazy init**, while
  `GetLiquidLevel` initialises it. On a headless server the mask is 0, so **every point in
  the world reads -10000 - including 6.7 m of undisturbed natural water.** An instrument
  that reports "no water anywhere" over an ocean is measuring its own uninitialised field.
  Caught by cross-reading an UNTOUCHED natural control beside the changed sample.
- `Game::FixedUpdate` calls `ZNet.SetReferencePosition(1000000, 0, 1000000)`
  **unconditionally every physics tick**, so a dedicated server holds no active zones and
  `ZNetScene::RemoveObjects` culls whatever a harness spawns. Three float tests returned
  `DESTROYED_BEFORE_SETTLE`, which READS as "the hull sank" and MEANS "the harness was
  deleted".
- A third, found while verifying the real cut: a collateral check sampled the NEAREST
  lattice point and reported 235 of 955 positions moved, worst 2.9584 m. The write was
  right and the test was wrong - piece positions are not on the 1 m lattice and
  `Heightmap` interpolates bilinearly over four samples, so a pile inside the basin is
  SUPPOSED to drop 2.96 m. Re-scored against the four samples that carry the point:
  0.0000 at all 512 points no written sample can reach.

**The worst instance of this class, and the one to carry: an instrument that cannot see
the thing it measures returns the PREVIOUS STATE, with full confidence, reproducibly.**
`GetAllZDOsWithPrefabIterative` is PAGED. A verification tool called it once, read the
first page of 75 ZDOs, matched none of the four terrain compilers, instantiated zero, and
so answered from the GENERATED bed:
```
reported basin depth      1.593 m   on a world dredged to 6.000 m
pre-dredge arithmetic     6.000 design - 4.4264 worst cut = 1.574 m
                          -> it was reporting the state BEFORE the write, exactly
the game's own caller does `brfalse` back to the call: false means "more pages remain"
fixed by looping        4 of 318 compilers over 9 PAGES; basin then read 6.000 m
```
**That is worse than a crash and worse than an obviously wrong number.** It is plausible,
it is the state that existed an hour earlier, and it REPRODUCES — which is what makes it
convincing. The first page happened to contain enough to answer credibly.

**Defence, and it is not "read more carefully" — it is a validation the instrument runs
on itself every time.** A sibling tool reads `m_generatedZones` out of the save by
SCANNING for the longest run of in-range `(int16, int16)` pairs rather than trusting a
fixed offset, so a save-layout change could lock it onto a different int16 array and
return a confidently wrong zone set. What protects it is the pair of checks it prints on
every invocation:
```
every zone the ledger has ever terrain-written MUST be present   -> 316 of 316, 0 missing
zones generated AFTER the last save MUST be absent               -> all 12 absent
```
**The presences alone would have passed on a wrong array. It is the ABSENCES that prove
the reader is looking at the right object** - and the rule is to refuse the reading if
either half fails, not to note it.

**And the tell, which is the cheapest diagnostic in this document: the wrong answer was
not NOISY, it was the PREVIOUS STATE, exactly.** An instrument blind to a delta does not
return garbage — it returns the world without the delta, which is internally consistent,
reproducible, and the most convincing kind of wrong.

> **If a verification hands you a number that is precisely what you would have measured
> BEFORE you acted, suspect the instrument before the world.**

The paging defect is the proof by counter-example for the two-directional rule above: 318
compilers existed, the reader saw 75, matched 0 of the 4 it wanted, instantiated 0, and
reported to four decimal places. **A presence-only check would have sailed through it,
because there was nothing present to contradict.**

**The class is wider than readers and iterators — a third instance widened it.** A
`straighten` pass replaced a curved station run with its chord and never re-measured `s`,
so every downstream consumer received **the arclength of the line as it was before the
transform**. Not noise: the previous state, internally consistent, reproducible, and 4.6%
wrong on one leg (1,786.9 m quoted against 1,708.6 m real). It survived a night of review
because every cross-check agreed with it — they were all reading the same stale array.

> **Any quantity DERIVED from a structure, and not recomputed after that structure is
> transformed, returns the pre-transform state with full confidence.** A paged iterator
> returns the world without your delta. A cached arclength returns the line without your
> straightening. Neither looks wrong.

**The two defences compose, and each catches what the other cannot:**
- **the two-directional check catches a READER** — something that must be present and
  something that must be ABSENT, because presence is satisfied by any plausible array;
- **a self-consistency check catches a DERIVED QUANTITY**, and it is trivial where it
  exists: *does `length_m` equal the polyline sum of `nodes`?* After the fix it agrees to
  **0.04 m on all six legs**; before, it disagreed by up to 78.3 m — and the disagreement
  **scaled with `bridged_stations`** (1.9 m at 0 bridged, 47.3 m at 123, 78.3 m at 221),
  which is the signature that confirmed the mechanism rather than merely the error.

**Rule: after any transform that moves the elements, recompute everything derived from
their positions — and assert the derived value against a direct recomputation before
quoting it.**

### A check that exists is not a check that ran everywhere it applies
`Ledger.append` runs three guards on a `terrain_write`. The offline pre-flight ran **one**
and printed `0 problems`. All three existed, all three were correct, one was never called
— and the cost landed on a live world: a leg passed pre-flight and was then refused at
append for 38 pieces moved 0.020-0.751 m. Reproduced offline afterwards with the other
two passing and `schema.validate` clean: the missing guard **REFUSES with 244 problems.**

> **The pre-flight was not a weaker check. It was a DIFFERENT check wearing the same
> words.**

**Rule: the pre-flight and the post-build verification must be the same comparison, not
two different ones.** And the structural fix is better than reconciling them, because
reconciliation is a promise and this is a property:

> **Do not build a second instrument and check it against the first — CALL the first
> one.**

The corrected pre-flight does not model the append's guards; it invokes them, on a
sandbox chain with symlinked blobs. Same functions, same objects, one authority. Two
details worth copying:
- the three guards are named **individually rather than looped**, so a guard added to
  `append` later surfaces here as a *missing name in review* rather than being silently
  covered or silently skipped. A loop would have been shorter and would have hidden the
  next instance of this.
- **do not pair a live read with a computed read and call the agreement a verification.**
  One harness had computed-vs-game clean on the SEND path and blind on the READ-BACK
  path; only one of the two was ever exercised against known-good data.

And the diagnostic that ties this whole section together, from three independent
instances in one night:

> **If a wrong number contains its own explanation, you have the mechanism, not just the
> error.**

`1.593 = 6.000 - 4.4264` — a reported depth with its own worst cut subtracted back out.
`78.3 m` of length overstatement scaling with `bridged_stations` 0 -> 123 -> 221. Neither
was noise; each carried the shape of its own cause.

**CORRECTION, and it is a correction to the rule above rather than a caveat on it — the
rule as first written here was over-general, which is this document's own recurring
failure committed once more.** *"Do not model the guard, call it"* is right for a
**computed expectation** and WRONG for a **postcondition**. A verification that re-reads
the agent's own blobs proves only that it can encode and decode; it does not prove the
world holds what was meant. Collapsing the two paths would have destroyed the only check
capable of telling those apart.

```
computed expectation   ONE AUTHORITY   call the deciding function, never model it
                                       (a pre-flight runs append's own three guards)
postcondition          INDEPENDENCE    the world answers by its own route
                                       (the save's stored ZDOs, asked of the game)
```
They are not in tension once the populations are separated: **they are statements about
different objects** — which is the whole subject of this document arriving one more time.

**And independence has a price that must be paid explicitly: an independent postcondition
reader is UNVALIDATED CODE ON THE CRITICAL PATH, sitting in the one place no other check
can see.** So it must be proven in both directions on known-good data before its verdict
is trusted — and the bootstrap is nearly free, because by then known-good data always
exists:

> **"The thing I just wrote must be PRESENT, and something I have not written must be
> ABSENT."**

The paged iterator would have failed that instantly on *"the four zones I just wrote must
be present"* — it matched 0 of 4. The save-scanning zone reader is the worked example in
the other direction: same hazard class, trusted only because it prints both halves on
**every** invocation rather than having been checked once and remembered.

**Rule: an instrument's wrong answers must be implausible.** `-10000` reads as "dry" and a
deleted hull reads as a sinking, so neither fault announces itself. Measure an UNTOUCHED
control in the same run as the change, and prefer a cross-check that could have failed -
scoring the same readings against the bed reflected across the basin diagonal gave
RMS 0.935 m against 2.6e-05 m in true orientation, a ~37,000x margin, which is what rules
out a transposition rather than merely agreeing with itself.

### A conditional is a field too
The session's final generalisation, arrived at by the agent whose code contained both
halves of it.

Two defects in one file, hours apart, with the same shape at different scales:
- A **portal** was staged as `op="spawn"` carrying a portal blob. Tag validation, the
  `pair_tag` requirement and the `close()`-time `(tag -> exactly 2 portal ZDOs)`
  invariant all key off `op == "portal"`, so the record was invisible to all three.
  **A well-formed value in the wrong FIELD.**
- A **stage save** was emitted inside `if not args.dry`:
  ```python
  if not args.dry:
      b.emit("save", params={"reason": ...})   # `reason` not a schema key,
                                               # no `expect`, no `wire`
  ```
  Three independent schema errors, and the conditional is the one that hid the other
  two. The dry run validated 48 world records and **none of the 8 saves** - the records
  added last and tested least. **A well-formed record on the wrong BRANCH.**

> **A conditional is a field too.**

Cost asymmetry, worth stating because it decides what to check first: a portal exempt
from a guard is inert furniture; a `terrain_write` with no save behind it **does not
survive a restart.** The second sat in the chain unsaved for six minutes of live
consequence that a dry run should have found.

**Rule, and it must be stated narrowly or it misfires: PUT EMITS WHERE EVERY MODE
REACHES THEM.** The validator was never the weak link — `LiveBuilder.emit` builds and
validates every record in dry mode and skips only append, send and verify, exactly as
its own comment says: *"the record is still BUILT and VALIDATED, because that is the
useful half of a dry run."* It cannot validate a call that never happens. After the fix:
56 records, 0 schema problems, and the 8 saves validated for the first time. Quote 56,
never 48.

**This rule was first written here in the broader form "a dry run that does not validate
what the live run sends is not a dry run of it" — and that wording immediately caused a
fresh instance of the very defect it describes.** A second agent read it, believed its
own tooling shared the flaw, and publicly offered to port a fix it did not need; the
retraction came from measuring `live.py:140-147` and finding the unconditional emit
already there. **A rule stated wider than its evidence propagates as a false positive,
which is the same failure as a measurement quoted past its population** — recorded here
against this document's own author, because the broad wording was mine.

**Rule: derive a postcondition from the stage's OWN rows**, via the same sentinel the
content records use, so a save cannot pass on a neighbour's pieces. The original saves
carried no postcondition at all.

And the repair judgement, which is the harder call and the one worth copying: the
missing save was landed as **one record for one missing record**, not by re-running the
stage. The wire was delete-compiler-then-respawn-same-blob and therefore idempotent on
the world, so re-running was *safe* - and still wrong, because it would leave a duplicate
`terrain_write` in an append-only chain for a later reader to explain. **Choosing the
smaller correct action over the safe convenient one.**

Related, from the same build: the portal refusal was **appended as a non-mutating note
rather than omitted**, because *a record that is neither sent nor written down is
indistinguishable at replay from one nobody thought of.*

### The right property, tested on the wrong object
A re-route was built to avoid a zone the game refuses to generate. The keep-out was
grown by `CARRIAGEWAY_HALF_M` (4.0 m) instead of `CORRIDOR_HALF_M` (14.5 m), with a
plausible written justification: *"a batter sample in the bad zone is exactly as
unwritable as a carriageway one, so reserving 14.5 m would close necks the road can
legitimately use."* **The first clause is true. The conclusion is wrong.**

```
N2 with the 4.0 m keep-out
  densified centreline cells inside (-9,76)   0       <- looks like success
  stamped zone list                           40 zones, CONTAINING (-9,76)
  -> the route avoided the zone and then asked the game to generate it anyway
```

**The question was never which SAMPLES are unwritable — it is WHICH ZONES THE WRITE
TOUCHES.** The ribbon derives its zone list, its `zones_generate` batches and its
`terrain_write` entries from where samples LAND, so one batter sample inside a zone puts
that whole zone in the write. A centreline test can never show this; it was caught only
by asking *"which zones are not yet in `m_generatedZones`"* before sending, and finding
the avoided zone in the answer.

Corrected with the full 29 m bench held clear: 38 stamped zones, the bad zone absent,
penalty **+69.7 m** rather than the +13.1 m first reported — a 5x price increase that
still beats permanently forfeiting 37 zones of content.

**Rule: test the guard against the ARTEFACT the write produces — the stamped zone list,
the emitted sample set, the record as it will be appended — not against the geometry the
plan was reasoned from.** Every earlier instance in this document is a special case of
that sentence.


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
