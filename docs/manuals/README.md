# Building a Valheim world programmatically: manual set, doctrine, post-mortem

Written 2026-09-18 from the session transcript itself, at the operator's instruction
(2026-09-18T04:20:09Z): *"lets also extract the functionality you have worked out for all
the various building and generation techniques. go back through the session history and do
a post-mortum on what you have learned and techniques and rules you have learned and
generate some documents which instruct future sessions how to build a world. include the
option to also generate again from a previous generation if the replayproof works"*.

The world under measurement is **Ulfsland**, seed **`Pirate68`**, Valheim 1.0.x / network
40. The build ran **2026-09-16T02:28:11Z - 2026-09-18T03:52:20Z** and is recorded in
`tools/jumpstart/ledger/runs/Ulfsland/ledger.jsonl`, **2,483 records** at the time of
writing.

**Provenance of this document set.** Written READ-ONLY: zero RCON calls, zero world
writes, zero ledger appends, zero deploys, and Ulfsland only - the other four worlds and
the shared `admin` / `flat` / `vr` profiles were untouched, per an explicit operator
directive. Sources: the session transcript (parsed record by record, every claim dated
from its own `timestamp`), `ledger.jsonl`, the live tooling under
`tools/jumpstart/`, and the findings documents listed below. Where two records conflict,
the later measurement wins and the earlier one is named.

Every manual in this directory is written for an agent with no memory of that session.
Each rule in them carries the measurement that produced it and the defect it prevents,
and every number is labelled **MEASURED** or **INFERRED** with its instrument.

---

## 1. The index - read the right one first

| Manual | The question it answers |
| --- | --- |
| `terrain-and-roads.md` | How do I cut a road that a player can actually walk - routing, the +/-8 m terrain clamp, grades against the game's 38-degree slide angle (and the fact that Valheim has NO step-height constant, §3.7), batter and interstices, vegetation clearing, fords and junctions, arriving on a datum measured from what is STANDING rather than on a `plan.json` coordinate or a ledger-composed surface (§4.6), auditing the per-zone compilers that ARE the road and triaging their absences by what claims them (§1.5), why a blob a segment carries is a snapshot of the whole ZONE and what that does to a repair (§1.7), why "nothing will revert my fix" is not "my fix will land" (§1.8), why the walkability check returned WALKABLE over 84 impassable locations (§3.7), which corridor population a number actually counts and whether it blocks a walker (§7.8), and lamp siting by sightline? |
| `structures-and-settlements.md` | How do I put a building on the ground so it is level, supported, entered, and not floating - pivot offsets, collider-vs-mesh seating, the game's own collapse algorithm, site pads, POI clearance, piece registration, building around mod content that is already there, how two sub-builders sharing one boundary element put 52 duplicate pieces on the ground (§3.6), why four grounding censuses called a floating 212-piece tower clean (§3.7), why no volume-shaped census can see 480 roof panels pitched the wrong way (§3.8), and how four of our own bodies - including a bridge - were carried as mod keep-outs (§10.5)? |
| `water-harbours-and-vessels.md` | How do I build anything that touches water - the `c_WaterLevel` 30.0 plane, depth over a hull FOOTPRINT rather than a point, deck datums, piers on piles, the ~96 m bridge span ceiling, the bridge-vs-ferry decision, berthing real vessels, and the classes of waterfront installation? |
| `build-harness-and-protocol.md` | How do I drive the server without breaking it or lying to myself - the `consoleCommand` RCON bridge, **who owns which verb** and why the single constructive verb is a single point of failure (§16.1), verbs the harness names that no plugin owns (§16.4), the four packages it cannot run without and nothing marks (§16.5), the two-part operations whose halves fail separately (§16.3), why a transport receipt is not an execution result and why the log proving it expires in ~25 minutes (§3.5), why `docker logs` can freeze for 26 hours beside a healthy server and which path to diagnose instead (§3.3), instruments that abbreviate and so lie to a parser (§8.5), a census that reads the file it writes and makes every body its own witness (§8.6), why a `findObjects` census is what wedges the server (§8.4, §3.2), which `--dry` paths still leave a file in the watched directory (§6.5), batch sizing and the stdout-pipe wedge plus its host-side drain, the write token, saves that are verified on disk, the append-only ledger, postconditions, and idempotency (read-before-write) in every builder. |
| `world-generation-and-replay.md` | How do I choose a seed and rebuild a world - **the regeneration contract at its head**: a replay re-emits the RECORDED BLOB, so it reproduces every defect whose bytes are in the record, and the fixes that survive a regeneration are the ones living in the TOOLS rather than in the world; plus what is seed-derived and survives a regeneration, what is save-derived and does not, the computable seed-selection criterion, the ledger **projection** (fold to final state per prefab and full world position) rather than a literal history replay, which defects a rebuild repairs for free versus which it reproduces versus which the fold DESTROYED while its key dropped y (§5.3.1, §5.5.4), and which headline fidelity figures are currently `[UNVERIFIED]`. |

Findings already written this session. **Point at these; do not restate them.**

| Document | What it is |
| --- | --- |
| `docs/proposed/2026-09-17-the-house-defect.md` | The defect taxonomy: a check that answers a question it is not measuring, with every measured instance found in one session. The doctrine below is its executive summary; the taxonomy is the evidence. |
| `docs/proposed/2026-09-17-next-world-road-requirements.md` | The operator's own requirements for the next world, recorded verbatim and non-negotiable, plus the measured constraints they must live inside. Read this BEFORE designing any road network. |
| `docs/proposed/2026-09-18-mod-update-and-piece-access-survey.md` | Which mod pieces actually exist at runtime, which packages are current, the `AddRWPieces` stub, the orphaned-config census, and the positive controls used on each instrument. |
| `docs/proposed/2026-09-18-vhvr-dev-channel-survey.md` | The VR client channel survey; relevant only because the operator inspects the world through a headset. |
| `local://harbour-city-survey.md` | The harbour A `(1050,720)` / harbour B `(1190,444)` survey: a fully designed, never-built walled harbour city, with its seed-derived and save-derived figures already separated. |

---

## 2. Doctrine

These apply to every domain. Each is stated with the instance that produced it.

1. **A thing that could be COMPUTED must not be ASSERTED.** This is the project's own
   diagnosis of itself and the root of nearly every defect found. The session found the
   shape more than twenty times in one night, in tooling, in mods, in the game, and in the
   briefs the orchestrator wrote for its own agents. The cure never varied: **re-ask the
   runtime instead of re-reading a derived number.**
2. **Ask the thing that decides, in a loop.** Where a guard is the authority, call it per
   candidate rather than predicting its verdict. A closed-form shrink radius silently
   skipped **6 cylinders and 127 trees that the gate itself passes**; bisecting against the
   gate fixed it.
3. **Validate the instrument against a known positive before trusting a clean result.** A
   scanner that finds the four known-bad DLLs and does not flag a fifth is evidence; a bare
   zero is not. Positive control that earned its keep: an IL registration count of **29**
   matched the mod author's own itemised README total of **29** - two unrelated
   instruments, one number. Its sibling control **failed on first run** (1 unresolved token
   of 2,542 call sites, from an incomplete opcode table that desynchronised every following
   instruction), which is exactly why it was run.
   The cheapest instance of the rule and the one that nearly cost a healthy server:
   **`grep -i worldedit` cannot match `World_Edit_Commands`** - underscore directory,
   camel-case assembly - so it answered **0** four times to one agent and for an hour
   to another, who published it as "verified absent" while it agreed with reality by
   luck. It still answers 0 with the package installed. **A negative from a pattern
   you have not proved can match is not evidence of absence**, the positive control
   was one command against a sibling package in the same directory, and the real check
   was an enumeration you can diff: directory present, plugin count **98** where it was
   97, and a `server-plugins.previous` diff holding exactly one line.
4. **Agreement between two instruments is evidence only if they were capable of
   disagreeing.** A modded location dump was run to compare against the mod-free one; the
   two agreed exactly - **12,301 locations, 177 types, identical hash**. That was not
   corroboration: `More_World_Locations_AIO`'s `Awake` had THROWN in the sandbox
   (`LocationQuantityManager.EnsureDefaultsLoaded -> LoadOrMigrateConfigs`), so its ~178
   locations never registered and the "modded" dump was mod-free too. One instrument was
   silently disarmed and both shared the same blindness.
5. **When a measurement meant to TIGHTEN a bound instead LOOSENS it, the instrument is
   measuring something else.** A 32 m blanket stand-off was to be refined into a measured
   per-marker reach; it returned **57.64-71.45 m**, larger than the worst case it was meant
   to replace, because a bounded neighbourhood census holds **218-707 objects** that are
   overwhelmingly birch, fir and rock. The number was a function of the census BOX SIZE.
   This inversion is the cheapest tell available.
6. **A timeout MUST NOT be able to produce a measurement.** On timeout write NOTHING and
   fail loudly; wait on real-time deadlines, never frame counts; print the population size
   beside every result. A prefab-registration probe waited **6000 FRAMES** for `ZNetScene`
   on a dedicated server that had not loaded a world, so `ZNetScene.instance` was null and
   `m_prefabs` read **-1**. It then wrote a complete, well-formed, fully populated TSV in
   which **every prefab read `znet=0`** - including `fb_fortress_wall`, which is registered
   and already standing in the world. A crash announces itself; a clean file full of false
   absences gets acted on.
7. **A screen proposes; a second independent instrument concludes.** The neighbourhood
   census in rule 5 is a perfectly good screen and was being read as a verdict.
8. **A config file is not evidence a mod is installed. Check the plugin.** Eleven orphaned
   configs were counted mid-session; the 2026-09-18 survey re-measured and found **18 true
   orphans** plus 3 client-only leaks. `deploy_server_config` only COPIES and never
   deletes, so every mod ever removed re-lands its settings on the next deploy in every
   world - a cleanup must delete from the profile's own `server-config/`, not from the
   world tree. An inventory mod's config was read as evidence its features were merely
   switched off; the mod was absent from all 20 current editions, and **no config value
   could have produced the feature.**
9. **A package instrument is not a runtime instrument.** `RavenwoodVikingHouses` 7.7.8
   carries **94 prefab paths in the bundle's own container index** and **131 registration
   call sites in IL**, of which **29 are reachable and 102 are dead** behind
   `RavenwoodPiecesPlugin::AddRWPieces`, whose entire method body is one byte: `2a` =
   `ret`. A live server `ZNetScene` dump of **4,872 prefabs** contains **zero** `mm_` /
   `RW_` / `jj_` prefabs. Related refusals from the same family: a `strings` sweep over a
   17 MB embedded asset bundle invented **95 phantom piece removals**, and a prefix filter
   returned **31 noise hits out of 55,789 strings** where the container index gave the
   authoritative answer.
10. **Measure over the FOOTPRINT of the thing, at the surface it actually touches.** A
    number that is correct about the wrong solid is not a measurement. Tower beacons went
    **5.173 m into the air** because **15.402 m** is a tower's mesh AABB while its walkable
    parapet is a collider at **10.229 m**. A hull filter read a `mesh_render` bound of
    **11.64 x 35.43 m** for a ship whose hull is **9.16 x 21.57 m** (the bound included a
    water-ripple plane) and would have oversized every slip by 60%. Two visually identical
    torches differ by **0.602 m** of pure prefab authoring.
11. **Prove both directions.** A guard that fires on the fixture AND passes the legitimate
    case is a guard; one that only fires is a refusal wearing a guard's name.
12. **Publish the measurement before the fix.** Then a refuted premise costs a paragraph
    rather than a deployment. The counter-example: a mod update was pre-classified
    DANGEROUS **in the brief**, so the agent inherited the verdict instead of measuring
    one - the changelog was purely additive.
13. **A postcondition is the proof; predicting it is not.** An existence probe expected to
    fail was still run - one `spawn mm_large_wall` through the ledger with a `prefab_count`
    postcondition - because a failed postcondition IS the evidence.
14. **Refuse rather than force, and record the refusal with its numbers - then re-check the
    numbers.** The east ferry exists because a **223.65 m** shore gap is **2.33x** the
    measured ~96 m free-span ceiling AND the water reaches the **30.0 m** ocean-floor clamp
    against ~20 m of pile reach: both ceilings exceeded, so a ferry rather than a bridge
    built in hope. The FIRST reason given for that same ferry - "a 174.7 m crossing exceeds
    the ceiling" - was retracted as a reporting artefact on a different segment, where six
    narrow channels had been merged into one fictitious run. A correct decision can rest on
    a wrong number. `plan.aim_seaward()` refused Vestvik's 133.7 m shoreline at all **72**
    bearings and has no default. In the Stenvik pass **5 pads were moved 4-7.07 m and each
    re-solved at its new centre, and 3 were DROPPED** once the search was exhausted, rather
    than sited badly.
15. **Any guard that narrows a request rather than refusing it MUST record what it narrowed
    and by how much - A SHRINK IS SILENT.** A blocked removal disc was shrunk
    CONCENTRICALLY, so a blocker near its centre erased it: **12.57 m became 3.84 m,
    removed 4 objects, abandoned 14, and recorded no skip reason because nothing was
    skipped.** A later audit then read post-shrink state and blamed the disc for not
    reaching what the pre-shrink disc did reach.
16. **When a limit needs tuning to make correct output pass, suspect the measurement.**
    Five threshold arguments this session turned out to be defects in the thing being
    thresholded: a 3.6 m station range, an inferred 11 m POI overshoot, a 0.5 m transverse
    step, a `>= 1` versus `== 1` zone-control count, and a batter grade being compared
    against `grade + the hillside's own slope`.
17. **Verify the artefact, never the exit code, and never the receipt.** `update --apply`
    returned `rc=0 / updated=1` while the pin never moved, because the download timed out
    and the failure was swallowed. The wire has the same shape:
    `InvokeConsoleCommand.OnHandle` returns `Command '<x>' executed.` UNCONDITIONALLY, so
    three probes - a verb that ran, a verb that ran with a bad argument, and a verb that
    does not exist at all - came back with identical receipts. Read the container's own
    stdout, or take a census; `Unknown command` means absent, `Error: ... not recognized`
    means present with a bad argument. Nine hours passed before anyone read a log line
    instead of a reply, and in that window a `_TerrainCompiler` was deleted with nothing
    put back. **And copy the line out the moment you read it: that log rotates at 1 MB,
    measured at six rotations in ~26 minutes and about 25 minutes of retention, burned
    by our own `objects_count` probes at two lines per call - the 15:58:47 incident
    lines were gone before a second agent looked.** Evidence that expires is evidence
    you must move; a live census answers the same question tomorrow.
18. **Anything load-bearing runs as a standalone script in ONE invocation.** The Python
    eval kernel is SHARED between task subagents. Two agents hit it independently within
    minutes: `HarbourCity` had globals `W` and `fl` rebound mid-survey (`W` became a module
    object, `fl` became a `Field`), and `FerryPair`'s own `W` - holding `crossings.water` -
    became a float. Nothing reached disk wrong, but a measurement computed across two cells
    can be computed against another agent's variables with no error and no signal. Prefix
    globals with the agent's own name and **re-derive rather than remember.**
19. **A road must ARRIVE, and a plan coordinate is not a world coordinate.** Solve a
    terminus against the DATUM of what it meets. Defects paid for: a profile solved from
    terrain alone that stopped **10.414 m** below Stenvik's floor; a road ending **0.648 m**
    above a dock's `DECK_DATUM_Y`, a datum that exists precisely so the join has no step;
    `step_across_m -7.644` sitting unread in a segment's own metadata; and a character
    spawn pointed at `(-4673, 73, -350)` on the strength of a PLAN, where a live census
    later found **0 smelters, 0 kilns, 0 chests, 0 workbenches, 0 torches, 0 portals** and
    826 natural objects. Two more, from a pad and from a city with no pad at all:
    `wt-peak`'s pad was written to `target_y 102.31 m FROM PLAN.JSON` and its body
    anchored to the same number (seq 545 / 546), and Ulfsgard's only datum is its quay
    deck at **y = 30.503** - a road aimed at its planned centre would have arrived on open
    ground **18.1 m outside the city**. Measure the thing that is standing.
20. **Never delete the player's world to make a plan fit.** A structure standing in a
    carriageway is a ROUTING failure, fixed by re-routing or re-terminating. Road clearing
    removes vegetation by explicit prefab list and has never removed a building; `id=*` at
    a radius is what once made a ring unclearable because it would have deleted a bridge.
21. **Distinguish SEED-DERIVED from SAVE-DERIVED on every number.** Seed-derived facts
    (generated height, biome, water plane, land/water topology, location REQUEST lists,
    prefab geometry read off assets) survive a world regeneration and can be re-measured
    before a single piece is placed. Save-derived facts (live censuses, applied terrain
    deltas, ZDO ids, what is standing) do not. The test is mechanical: *if the world were
    regenerated on the same seed, would this number be identical?* Two corollaries measured
    here: `plan.json`'s location dump holds **12,301 entries all reporting `placed:
    false`** - it is the generator's REQUEST list, and resolving against it put the nearest
    named location 20-74 m from clusters sitting 0.3-6.9 m from a real placed marker; and
    the durable key for anything replayable is **prefab + world position, deliberately NOT
    ZDO id**, because ZDO ids are reassigned on load.
22. **Prove the constructive half before running the destructive half.** Every mutating
    operation in this harness is delete-then-place, and the halves are owned by different
    packages - `deleteObjects` is ValheimRcon NATIVE, `spawn_object` belongs to World Edit
    Commands - so one can be live while the other is gone. On 2026-09-19 the delete landed
    and the respawn evaporated: `Deleting 1 objects: -Prefab: _TerrainCompiler Id: 89145:1
    Position: (1024 0 704) Zone: (16 11) [DELETED]`, and the `ulfsgard-dredge` harbour
    basin is gone. The standing consequence is structural: **this toolchain has exactly ONE
    constructive verb and several destructive ones**, so a missing content mod leaves a
    harness that can only subtract. Verb ownership is a table in
    `build-harness-and-protocol.md` §16.1, settled from `strings ValheimRcon.dll`.
    **And a verb named in the harness's own taxonomy is not a verb that exists:**
    `terrain_reset` and `objects_reset` sit in `STAGED_VERBS` and **no installed plugin
    owns either literal**, so the first caller will get `Unknown command` behind an
    `executed.` receipt. Probe the surface, do not read the table - and note that the
    four packages this harness cannot run without (`Server_devcommands` 1.113.0,
    `Upgrade_World` 1.82.0, `ValheimRcon` 1.6.2, `World_Edit_Commands` 1.77.0) are
    marked as harness dependencies nowhere, which is exactly how one of them was
    reclassified out from under the server.
23. **A count that disagrees with its own list is a failed read, not a short answer.** An
    `rcon.py --max-print` default of **600 characters** - a courtesy for a human at a
    terminal - turned a `findObjects` header declaring **41 objects** into **6** parsed
    rows, and was caught only because two transports censused **1,102 against 1,408** with
    identical subdivision counts. Compare the declared count against the rows you actually
    parsed, on every call. Its siblings are the delivery receipt (rule 17) and the
    4050-byte packet cap, which returns a page shaped exactly like a whole.
24. **A fold is not a replay, and a fold is a repair ONLY where its key is exact.**
    A fidelity percentage measures AGREEMENT WITH THE RECORD, not correctness: a
    faithfully replayed defect scores 100%. Three outcomes, all measured on one ledger
    in one night. **FOLDS AWAY**: records that collide at the key AND are the same
    object - the 52 duplicate pieces at Ulfsgard. **SURVIVES**: defects laundered into
    a surviving record's payload - the ghost road, carried forward verbatim into seq
    2053, with **0 of 5** zones' surviving blobs belonging to a retired write.
    **DESTROYS**: distinct objects that collide because the key is too coarse -
    `project.py:97` keyed on `(prefab, u, v)` under a docstring promising world
    position, so of **12,550** collapses only **689** joined pieces at the same y and
    **11,861** joined distinct stacked pieces, worst separation **38.00 m**; 32,946
    applied became **19,777** survivors, a **36% demolition**, and Ulfsgard's sea gate
    replayed halved (`fb_fortress_portcullis` 6 -> 3, rows at y 23.774 and 29.481).
    `project.py` now keys in 3D; post-fix counts are `[UNMEASURED]` until the fold is
    re-run. Two discriminators, and the first alone is what made this rule wrong for a
    night: *does the defect occupy a key another record also claims?* and *is the key
    sufficient to tell two real objects apart?* Prove the free repair per defect; never
    assume it - the "rebuild rather than cull" recommendation this rule once carried
    was withdrawn by the agent that made it, two minutes after making it, by testing it
    against the rule beside it. Every replay report must separate "differs because the
    replay is wrong" from "differs because the source world is wrong" from "differs
    because the fold destroyed it". **And the sequence is fixed: fix the world, fix the
    tools, document, and only then regenerate - a regeneration re-runs corrected tools
    over a clean save, it is NOT a repair mechanism**
    (`world-generation-and-replay.md`, the regeneration contract at its head).
25. **An absence is a defect only if some record CLAIMS the thing - ask what claims it
    before you call it damage.** A compiler audit over all **435** zone centres the
    ledger claims a `terrain_write` for found **429 present (98.62%), 6 absent, 0
    duplicated**. Of the six, **three were claimed only by a write that never landed**
    and carried **0.0 m** of live centreline each: nothing was ever written there, so
    nothing is missing. One is the deleted `ulfsgard-dredge` compiler (rule 22). Only
    **two** are real unexplained damage - and they carry **125.0 m** of graded
    carriageway whose blob deltas ran to **-2.938 m**, so that much of a junction has
    reverted to generated height with every piece census still reading clean. The same
    question answered in the other direction stops an under-scoped repair: scope a
    re-emit to what the BLOBS claim, never to what the centrelines cross - live T8 has
    **0 m** of centreline in those two zones and **878** and **175** modified samples
    in them.
26. **Measure against the surface the ENGINE DRAWS, and never against the number the
    builder chose.** `WorldGenerator::GetHeight` uses the point's own biome;
    `HeightmapBuilder::Build` blends the ZONE'S FOUR CORNER biomes and has a fast path
    that renders a four-agreeing zone from one of them. They differ wherever a zone is
    not biome-uniform, `PatchScan` sampled the first, and every pad in this world was
    levelled to it: `wt-peak`'s pad sits at **102.310** over rendered ground of
    **93.885-99.427**, so 212 pieces, a portal and a guidepost stand in **3.17-13.54 m
    of air, median 4.92 m**. Settled against **1,186** worldgen-placed trees and rocks,
    whose y IS their ground contact: residual against the heightmap surface median
    **-0.001** with **8** samples over 1 m; against the worldgen surface, **43** and a
    minimum of **-10.516**. **Four censuses had reported this world clean, and none was
    buggy:** `build.py::grounding(plan_path, pad_y)` compares each body to the scalar
    its pad was levelled to, so it is satisfied by construction - **a check whose
    reference is the thing under test is not a check**, and `ribbon.py::walkability` has
    the same shape. **The triage predicate is corners AND interior** - four corner
    samples alone is NOT immunity, a correction that landed after four agents had
    gated on the corners, because the fast path evaluates corner b0's function over
    the whole zone: three four-corner-uniform zones diverge by **5.751**, **6.191**
    and **2.079 m**. With the full predicate there are **zero exceptions over all 435
    written zones (335 immune, 100 not)**, and under the 21 built segments 189 zones
    are immune, 47 need a re-scan and **100 cannot be answered at all for want of
    field coverage - UNKNOWN is not CLEAN**. The one waterfront zone it flagged held a
    **-9.4634 m** error under a real piece while that same structure's bay depths
    agreed to 7 mm: **inside a mixed zone the error is not uniform, so agreement at
    one end of a body clears nothing at the other.**

---

## 3. Post-mortem

### What this set out to build

The operator's founding instruction, 2026-09-16T01:54:18Z:

> build as much as you can, including flattening and creating road systems all up and down
> the main continent and other areas by inspecting the topography and picking the most
> logical route keeping the gradients as flat as possible. build bridges over chasms and
> rivers, streams, etc.. create a log of everything you build and where and how so that you
> can repeat it all during the next regeneration. [...] dont forget docs, harbors,
> boathouses for longboats, etc.. even ferries across two points on a larger body of water.
> you can even try to build a town or city in some places in the meadows.

Ulfsland was explicitly the EXPERIMENT world - "9 chunks, 1.6 MB, zero player buildings,
zero edited terrain zones", a blank canvas, with Hrafnheim, Doggerland, Storgard and
Vangard named as real worlds never to be modified.

### What it achieved, measured

Read off the ledger and live censuses on 2026-09-18, not from memory. The site inventory
below was re-derived for this document by scanning every `spawn` / `spawn_plan` record in
`ledger.jsonl` for its site name, because the operator-facing summary written at
2026-09-18T02:16Z undercounted the watchtowers:

```
SITES        15 of 15 in roads/spec.py::SETTLEMENT_SITES have placements standing:
             1 town (stenvik, 15 buildings around an intact mod hamlet)
             1 village (vestvik, 11 of 12) · 1 castle (hognest)
             6 watchtowers (wt-spawn/south/town/peak/north/east)
             3 lighthouses (lh-south/east/west) · 3 treehouse clusters
             plus two sites added outside that table: sudrberg-keep and lh-north
             UNBUILT units: tree-near-3, tree-west-3 (7 of 9 treehouse units stand)
             and dock-stationhub, which never received a piece
PORTAL HALL  a real building on a levelled pad, 18 tagged arches on its floor,
             18 guideposts, 20 tags ALL two-ended, worst portal seating error
             4.2 cm across 109 objects
ROADS        15 of 15 segments cleared, edge-graded and walkable
             T12 carriageway step p50 0.517 -> 0.152 m
             T4 legacy band p50 1.036 -> 0.781, the design grade exactly
LIGHTING     133 piece_groundtorch lamps, 30 ledger records (15 spawn_plan + 15 save),
             every one applied, nothing retired, nothing re-emitted
             worst air under any lamp foot 0.000 m, worst burial 0.144 m
             worst unlit road in the network 8 m, on T4 at 1192 m
BRIDGES      bridge-s1-temple-strait  27/27 deck 18/18 post 16/16 rail 4/4 piles 1/1 cap
                                      12 m strait, 2.67 m deep, deck 30.6, 0.6 m freeboard
                                      joins the two halves of the largest landmass;
                                      next nearest approach between them is 197 m
             bridge-s2-portalhall     60/40/38/14/4, 32 m span, 4.83 m deep
             structural margin worst 58 against a minimum of 10, under the game's OWN
             collapse algorithm (support is only evaluated near a player, so an
             unverified bridge looks perfect in a save and fails under your feet)
WATER        harbour-temple-south  34 of 39 deck tiles over water, portal x-harbour
             ferry PAIR: ferry-terminal-east 22/22 floors, 11/11 poles, 22/22 log piles,
               sign, CargoShip - every prefab exact at tolerance 0, 0 strays
               ferry-terminal-eastisle re-verified, VikingShip re-floated
               deck datum IDENTICAL both ends at 30.597, so a road arriving has no step
               sail line 196.82 m, wet fraction 1.0, one continuous run, shallowest 5.16 m
               guideposts read back off the ZDOs, naming each other in both directions
NORTH CAPE   lh-north        1255 pieces, 51.06 m above sea level, CargoShip berthed
                             with 3.038 m under its hull, portal x-north paired
             dock-northcape  127-piece pier, 48 m, head depth 2.977 m
LEDGER       2,483 records, append-only, hash-chained, ordered by FILE POSITION
             objects_clear 854 · observe 488 · zones_generate 242 · spawn 185
             retire 173 · spawn_plan 158 · save 123 · terrain_write 100 · note 97
             portal 59 · zones_restore 2 · world_manifest 1 · chain_reset 1
```

The ledger is the real artefact: every placement with prefab and exact world position,
every terrain write with its blob digest AND the generated-height digest it was computed
against, every clearing with its object and reason, every portal with its tag and both
ends, and every refusal with the measurement that justified it.

### What it did NOT achieve

- **The north/south trunk road does not exist.** All 15 segments sit in one cluster,
  extent `z -212 .. +1087`, `x -348 .. +812`. Northernmost road point: **S7 at
  (610, 1087)**. The north cape is at **lh-north (-182, 2592)** / **dock-northcape
  (-174, 2625)** - a **1.7 km gap with no road at all**. The lighthouse and its berthed
  ship are reachable only by the `x-north` portal or by sailing.
- **No mountain routing was attempted.** `wt-peak` (z 1538), `wt-north` (1197), Hognest
  castle (1348), Vestvik (z 1766-1948) and `lh-west` (2360) have no road reaching them.
- **The regenerated world was never started.** The operator asked *"do you think you can
  rebuild the world again with all these fixes in place?"*; the answer was that it needed
  proving on a throwaway world first, and the rebuild itself never happened. The proof did:
  `tools/jumpstart/ledger/project.py` folds all 2,483 records to final state keyed on
  **prefab + world position** and was reconciled against live Ulfsland -
  **placements 15,674 / 15,683 = 99.94%** at tolerance +/-0.05 m, **terrain 191 / 191 =
  100.00% byte-exact**, **portals 20 / 20 tags with both ends present**, **sign texts
  40 / 44 verbatim**, with nine named discrepancies [MEASURED, `ReplayProof`, 2026-09-18].
  That figure says the projection can DESCRIBE the world. The **same-seed fresh-world
  replay** then landed on throwaway world `Replaytest` (seed `Pirate68`, derived seed
  `f59dcc08`, `worldGenVersion 41`): **placements 15,682 / 15,683 = 99.994%** at +/-0.05 m,
  **terrain 191 / 191 byte-exact**, **portals 20 / 20 two-ended**, **sign texts 44 / 44
  verbatim** [MEASURED, `ReplayProof` / `ManualWorldgen`, 2026-09-18]. It is HIGHER than
  the same-world figure because 7 waypoint pieces and 4 sign texts were records written
  behind a wire that never landed, so the replay HAS pieces the original build lost. The
  honest ceiling is 15,682 / 15,683: `mm_large_wall` is unregisterable (rule 9).
  **`[UNVERIFIED as of 2026-09-19]` - every figure in this bullet.** The fold those
  percentages were computed through keys on `(prefab, u, v)` and drops y
  (`project.py:97`), and on this ledger it emits **19,777** survivors from **32,946**
  applied placements, so neither the numerator nor the denominator above can be what
  it claims. The figures are left standing, undeleted and undefended, until
  `FoldKeyFix` reports what was actually measured. **Until then the sentence this
  bullet used to end on - "a rebuild on this seed costs essentially nothing in placed
  content" - is WITHDRAWN: a rebuild through the shipped fold is a 36% demolition.**
  Both figures, the nine named discrepancies and the three-class fold model live in
  `world-generation-and-replay.md` §5.5.4, which owns them.
- **No boathouses were ever built.** The four waterfront structures are decking on piles -
  floors, poles and log piles, no roof, walls, doors or bays: `boathouse-temple-strait`
  (171 objects), `harbour-vestvik` (184 + CargoShip), `dock-dockshore` (140 + VikingShip),
  `dock-northcape` (127). Only the berthing half of the requirement landed: three ships,
  each measured against its own hull draught.
- **The walled harbour city was designed, approved, and held UNBUILT with zero pieces
  placed** - deliberately, while the regeneration question was open. Its blocker is real:
  the fortification kit the design wants is the 102 unreachable prefabs of rule 9. One
  measured siting constraint survives regardless: on this world exactly **one** site
  satisfies both "floats a large ship" and "next to a town" - **Vestvik**, CargoShip at
  4.632 m of water with the village 227 m away. The Stenvik creek has the best shelter but
  is **15.7 m wide against an 11 m hull**. The northern tip is unusable for a port: 112 m
  from usable water, portal-only, 2.8 km from the nearest road.
- **Nothing a PLAYER perceives was ever verified by a player.** Road lighting, the build
  menu grid and which tabs exist are all read off prefab assets, IL and object counts,
  because no agent had a client session. One night walk and one hammer-open closes all
  three.

### The failure modes that cost the most, and how to avoid each

**1. Diagnosing the subject when the instrument was broken - the costliest false alarm.**
Hand-written RCON probes were sent as bare verbs without the `consoleCommand`
bridge. `Unknown command` came back, was read as a regression in a live world, and was
reported to the operator as broken. Measured window in the transcript:
**2026-09-17T13:35:13Z to 14:03:38Z** - 28 minutes of orchestrator wall time, recorded as
"an hour" in the house-defect document because the dispatched bisect agent ran on past the
correction. Inside it: two server restarts, a permissions file wrongly moved aside and
restored, a bisect agent dispatched, and one false lead (`Infinity_Hammer 1.84.0`'s
unsatisfied dependency) chased and ruled out. The command surface was fully alive the whole
time: **443 registered commands**. `ValheimRcon`'s verb registry is closed over its own
assembly with **41 own verbs and no console fallback**, so a bare verb is rejected by
design; `save` and `players` worked only because they are two of those 41. The project had
already recorded this exact trap in two source comments - **`terraform/place.py:33`** and
**`blueprints/to_rcon_plan.py:65`** - and every tool in the repo sends the bridge. Only the
hand-written probe did not.
*Avoid it:* send every command through the bridge; before reporting a regression, prove the
instrument on a known-good call.

**2. A by-name sweep that cannot see an IL callsite - a live player-facing regression
re-armed.** A compatibility shim row for
`PlayerProfile.IncrementStat(PlayerStatType, float, bool)` was retired with the reason *"NO
installed mod resolves this method by name. Swept all 115 deployed DLLs for a UTF-16
literal."* The row's own comment named its consumers: **EpicLoot and LongshipUpgrades call
the two-parameter form directly in IL**, which emits a `callvirt` against a 2-argument
memberref and contains no such string. The row existed because a player's log had shown
**898 `MissingMethodException: void PlayerProfile.IncrementStat(PlayerStatType,single)` in
39 SECONDS in-world** on the stat-increment hot path. The retirement's evidence and the
row's recorded consumers were measuring different things, and it took a dedicated IL pass
to reverse.
*Avoid it:* for callsite questions decode IL with Mono.Cecil or `dnfile` and a complete
opcode table, never `strings`; .NET strings are UTF-16LE and an ASCII grep finds nothing
anyway.

**3. Reading depth at a POINT where a FOOTPRINT was required - a ferry that could not
sail.** The existing ferry's `VikingShip` was measured **aground**: the seabed sat **0.465 m
ABOVE the water plane** under part of a **9.16 x 21.57 m** hull, while the record that
sited it had measured a single POINT and read **4.21 m**. The same shape put a 20 m jetty
**4.4 m up a hillside with 0 of 22 deck tiles over water** - caught only because
`aim_seaward()` refused all 72 bearings rather than defaulting. The fix that now stands is
per-berth **minimum depth under the hull footprint**, and the ferry pair's deck datum is
identical at both ends (30.597) for the same reason.
*Avoid it:* every water, ground or clearance test samples the FOOTPRINT of the object, not
its anchor; report the worst sample, not the mean; and test water against `c_WaterLevel`
30.0, never `height > 0` (the `h > 0` form classifies the entire seabed as dry land, and is
how two witness pads were sited 12 m under water and quoted to the operator four times).

**4. Nine releases of mount steering that never worked - nine of the operator's own test
sessions.** Mount steering was attempted **nine times and removed entirely**. It never
produced a horse that answered the stick, and it left per-frame work and a logger behind.
The reasons, established only at the end: the saddle discards `lookDir` outside a narrow
capture condition, the game's move vector carries one axis at a time, and VHVR's stick and
hand naming is inverted on this hardware. Every release cost a headset session that only
the operator could run.
*Avoid it:* when the only instrument is a human in a headset, spend the first session
measuring the mechanism (what the saddle does with the value) instead of shipping the ninth
guess; and never re-ship a fix whose failure mode you cannot state.

**5. A batch that wedged the server, on top of a builder that was not idempotent.** A
**1255-piece** record backed up the game's stdout pipe: **pid 120208 sat in `pipe_write`**
and the main thread stopped answering RCON, leaving `lh-north` at **272 of 1255 pieces**.
Because the builder had **no read-before-write census**, re-sending would have doubled all
272, so the partial had to be retired piece by piece by prefab + world position and rebuilt
whole - which **ate an agent's entire budget**. The recovery needs no restart and is now
measured twice: from the HOST, find the process holding the read end (`supervisord` pid
113676 on fd 21) and drain it - `timeout 75 cat /proc/113676/fd/21 >/dev/null` - after which
120208 moved to `hrtimer_nanosleep` and RCON answered in **34 ms**, with no `SIGKILL` and no
restart.
*Avoid it:* keep records to **48-64 pieces** with a host-side sink check between; make
**read-before-write a first-class property of every builder**; and never stop a container
while an agent is mid-build, because spawns exist only in the running process until `World
save (5/5) done` lands on disk.

**6. Replaying history instead of projecting state.** The ledger is a HISTORY and contains
the mistakes as well as the repairs: a lighthouse built to 272 pieces then retired, portals
placed then relocated into the hall, **764 objects cleared then re-cleared**, roads graded
then **re-graded four times**. Replaying it literally would repeat roughly twenty hours of
defects.
*Avoid it:* fold the records to final state per prefab and position - honouring every
retirement - and place that. That projection is no longer a hypothesis: it replayed onto a
fresh same-seed world at **15,682 / 15,683 placements** and **191 / 191 terrain writes
byte-exact** - **the placement figure `[UNVERIFIED 2026-09-19]`, because the fold key
drops y and collapses 11,861 distinct stacked pieces into their ground course. Fold to
final state per prefab and FULL world position; the shipped key is `(prefab, u, v)`.**
See `world-generation-and-replay.md` §5.3.1 and §5.5.4.

### The one thing that consistently worked

Every instance above was caught the same way, and it is worth stating as practice rather
than as virtue: **someone saw a number change for a reason they could not name, and stopped
to ask why instead of reporting it.** Three agents reversed the orchestrator on measured
evidence in this session, and every reversal was cheaper than the work it prevented.
