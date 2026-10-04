// WaterProof - settle, by measurement, whether LOWERING terrain below the water
// plane HOLDS WATER or DRAINS IT.
//
// WHY THIS EXISTS.  This project's standing rule is "never write terrain below
// the water plane", recorded as the one unrepairable damage class: it "drains
// water out from under a shoreline".  The evidence behind that rule is about
// LEVELLING A FOOTPRINT UP to a datum - raising the bed, which removes water -
// and about a road causeway, which is the same operation.  Dredging is the
// OPPOSITE operation: it lowers the bed.  Whether the two share a consequence
// is an engine question, and until it is measured it is an assumption wearing a
// rule's clothes.  A dredged harbour is the operator's request; a drained
// harbour is unrepairable; so the question gets an instrument, not an opinion.
//
// WHAT IT MEASURES, all through the GAME's own code, never a reimplementation:
//
//   ZoneSystem.SpawnZone(zone, SpawnMode.Client, out root)
//       builds the real zone: a real Heightmap from the world generator and
//       whatever else the zone prefab carries - including, if it carries one,
//       the zone's WaterVolume.  Client mode so no vegetation or location ZDOs
//       are written: this harness must not modify the save it measures.
//   WaterVolume.Instances
//       every water volume now live.  Its transform y, its COLLIDER BOUNDS and
//       its m_surfaceOffset / m_forceDepth / m_useGlobalWind are reported per
//       volume, because "is there water over this xz at all" is exactly "does a
//       water volume's collider contain this point" (Floating.GetWaterLevel
//       returns -10000 when no volume does).
//   Heightmap.GetHeight(p, out h)
//       the ground the game would use, sampled BEFORE any compiler exists (the
//       GENERATED bed) and again AFTER (the APPLIED bed).
//   Floating.GetWaterLevel(p, ref vol)
//       THE decisive number: the water surface the game hands to every hull,
//       every swimming character and every floating item.  Sampled before and
//       after the write, at several altitudes, so a surface that moved with the
//       bed would be visible, and so would a volume that fails to reach down
//       to a dredged bed.
//   Floating.IsUnderWater(p, ref vol) / WaterVolume.Depth(p) /
//   Heightmap.GetOceanDepth(p)
//       the wetness predicate, the volume's own NORMALISED depth (a shader
//       input, 0..1) and the generator's ocean depth, so the report does not
//       confuse any of the three with metres of water.
//   a real hull, instantiated and left to physics
//       the float test.  A ship over water settles at the surface; a ship over
//       dry ground falls to the bed.  Reported as the settled y against both.
//
// The cut is driven from a SYNTHESISED TCData blob passed in base64, i.e. the
// same bytes the live transport sends, so a positive result here is a result
// about the real write path and not about a hoe.
//
// PROVE THE GUARD BOTH DIRECTIONS.  The interesting run passes TWO patches in
// one zone: one LOWERED below the plane (the dredge) and one RAISED above it
// (the fill that the existing rule was actually derived from).  If lowering
// holds water and raising removes it, the two operations are opposite and the
// rule needs splitting.  If lowering also loses water, dredging is refused and
// the numbers say so.
//
// Env vars:
//   WATERPROOF_ZONES   semicolon list of "zx,zz" zones to spawn
//   WATERPROOF_BLOBS   semicolon list of "zx,zz,<base64 TCData>" compilers to
//                      create and instantiate (may be empty for a control run)
//   WATERPROOF_POINTS  semicolon list of "label,x,z" world points to sample
//   WATERPROOF_POINTS_FILE  a file of "label\tx\tz" lines, one per point, for
//                      the thousands of points a post-build census needs -- an
//                      env var cannot carry them and a hull ellipse is 160
//                      samples before the basin grid is counted
//   WATERPROOF_FROM_SAVE  "1" to instantiate the `_TerrainCompiler` ZDOs the
//                      SAVE already holds in the requested zones, instead of
//                      synthesising blobs. This is the POST-BUILD instrument:
//                      it makes the game apply the terrain it actually stored
//                      and then reads the result back with the game's own
//                      Heightmap and WaterVolume
//   WATERPROOF_SHIPS   semicolon list of "label,prefab,x,z" hulls to float
//   WATERPROOF_SETTLE  seconds to let physics settle a hull (default 10)
//   WATERPROOF_REF     "x,z" the server reference position to hold, so ZNetScene does
//                      not cull the test hulls before physics has run
//   WATERPROOF_OUT     output TSV path
//
// Like PatchScan and TerrainProof this kills the process when it is done, so a
// non-zero exit is expected and the output file is the only success signal.

using System.Collections;
using System.Collections.Generic;
using BepInEx;
using UnityEngine;
using SIO = System.IO;
using SSys = System;
using SText = System.Text;

namespace WaterProof
{
    [BepInPlugin("terraform.waterproof", "WaterProof", "1.0.0")]
    public class Plugin : BaseUnityPlugin
    {
        const string CompilerPrefab = "_TerrainCompiler";

        // ZoneSystem.SpawnZone and ZNetScene.CreateObject are private, so they are reached by
        // reflection rather than reimplemented: the point of this harness is that the GAME
        // builds the terrain, instantiates the compiler and owns the water, not a copy of it.
        static readonly SSys.Reflection.MethodInfo SpawnZoneMethod =
            typeof(ZoneSystem).GetMethod("SpawnZone",
                SSys.Reflection.BindingFlags.Instance | SSys.Reflection.BindingFlags.NonPublic
                | SSys.Reflection.BindingFlags.Public);
        static readonly SSys.Reflection.MethodInfo CreateObjectMethod =
            typeof(ZNetScene).GetMethod("CreateObject",
                SSys.Reflection.BindingFlags.Instance | SSys.Reflection.BindingFlags.NonPublic
                | SSys.Reflection.BindingFlags.Public);
        // The same binding set, named once, for the private `Door` members the
        // gate test drives.
        const SSys.Reflection.BindingFlags NonPub =
            SSys.Reflection.BindingFlags.Instance
            | SSys.Reflection.BindingFlags.NonPublic
            | SSys.Reflection.BindingFlags.Public;

        readonly List<string> lines = new List<string>();

        void Awake()
        {
            StartCoroutine(Run());
        }

        IEnumerator Run()
        {
            string zonesSpec = SSys.Environment.GetEnvironmentVariable("WATERPROOF_ZONES") ?? "";
            string blobsSpec = SSys.Environment.GetEnvironmentVariable("WATERPROOF_BLOBS") ?? "";
            string pointsSpec = SSys.Environment.GetEnvironmentVariable("WATERPROOF_POINTS") ?? "";
            string shipsSpec = SSys.Environment.GetEnvironmentVariable("WATERPROOF_SHIPS") ?? "";
            string settleSpec = SSys.Environment.GetEnvironmentVariable("WATERPROOF_SETTLE") ?? "10";
            string refSpec = SSys.Environment.GetEnvironmentVariable("WATERPROOF_REF") ?? "";
            string pointsFile = SSys.Environment.GetEnvironmentVariable("WATERPROOF_POINTS_FILE") ?? "";
            bool fromSave = (SSys.Environment.GetEnvironmentVariable("WATERPROOF_FROM_SAVE") ?? "") == "1";
            // WATERPROOF_DOORS: a prefab name whose ZDOs in the requested
            // zones carry a `Door`, exercised close-and-reopen.  Needs
            // FROM_SAVE and needs the CONTENT MODS staged in the sandbox,
            // because `fb_fortress_portcullis` is a modded prefab and a
            // mod-free mirror has no such entry in its ZNetScene.
            string doorsSpec = SSys.Environment.GetEnvironmentVariable("WATERPROOF_DOORS") ?? "";
            // WATERPROOF_ZDOS: a path to write a CENSUS of every ZDO the save
            // holds -- prefab hash, resolved prefab name, world position and
            // world ROTATION.  It exists because the three defects that are
            // NOT about ground -- something standing in the road, something
            // built the wrong way round, something a walker must climb -- are
            // all questions about objects, and every instrument this project
            // had for them was a live RCON read with a silent 4050-byte reply
            // cap, two different volumes named `objects_count` and
            // `findObjects -near`, and a console that saturates under a
            // writer.  `ZDOMan.m_objectsByID` is the WHOLE population in one
            // dictionary, read offline from a COPY of the save, so a count
            // here cannot be a truncated page and cannot be a different
            // volume from the one the question asked about.
            //
            // ROTATION IS THE POINT, not a bonus field.  A 180-degree yaw flip
            // moves neither footprint nor AABB, so a census that reports only
            // positions cannot see an inverted roof -- which is exactly how
            // 480 inverted panels passed a coverage test at tolerance 0.
            string zdosPath = SSys.Environment.GetEnvironmentVariable("WATERPROOF_ZDOS") ?? "";
            // WATERPROOF_ZDO_STRINGS: a comma-separated list of
            // `prefab:key[:type]` selectors (type in str|int|long|float,
            // default str; prefab `*` means EVERY ZDO), written as a second
            // census file at WATERPROOF_ZDO_FIELDS.
            //
            // WHY IT EXISTS.  `zdos.tsv` carries hash, name, position and
            // rotation and NOTHING ELSE, and the two invariants this world
            // has actually broken are neither geometric nor positional:
            //   * a portal pairs by EXACT STRING EQUALITY on its `tag` ZDO
            //     string (`TeleportWorld` reads ZDOVars.s_tag), so a tag that
            //     did not survive a retire-and-replace is a portal that pairs
            //     at random with the world's blank-tag mod-location portals;
            //   * a guidepost names its arch in its `text` ZDO string, and a
            //     check that binds a board to the NEAREST arch instead of to
            //     the arch it NAMES confirms itself -- 16 of 17 boards were
            //     once nearer the wrong arch and a geometric bind called that
            //     correct.
            // Neither can be read from a position, and the live RCON route to
            // them is the same truncating console the census exists to avoid.
            //
            // BLANKS ARE REPORTED, NOT SKIPPED.  The per-selector population
            // line counts ZDOs of the prefab, how many carried a non-default
            // value and how many were blank, because a reader that emitted
            // only the tagged portals would let a world full of anonymous
            // arches pass.
            string zdoStringsSpec = SSys.Environment.GetEnvironmentVariable("WATERPROOF_ZDO_STRINGS") ?? "";
            string zdoFieldsPath = SSys.Environment.GetEnvironmentVariable("WATERPROOF_ZDO_FIELDS") ?? "";
            string outPath = SSys.Environment.GetEnvironmentVariable("WATERPROOF_OUT");
            if (string.IsNullOrEmpty(outPath))
            {
                SSys.Console.Error.WriteLine("[waterproof] WATERPROOF_OUT not set");
                yield break;
            }
            float settle = ParseF(settleSpec);

            // Wait for the dedicated server to finish loading.  ZoneSystem is the last of the
            // three to appear and WorldGenerator.instance is what proves the heightmap source
            // is live.
            float waited = 0f;
            while (waited < 300f && (ZNet.instance == null || ZoneSystem.instance == null
                                     || ZNetScene.instance == null || WorldGenerator.instance == null))
            {
                yield return new WaitForSeconds(0.5f);
                waited += 0.5f;
            }
            if (ZoneSystem.instance == null || ZNetScene.instance == null)
            {
                SSys.Console.Error.WriteLine("[waterproof] world never came up");
                yield break;
            }
            yield return new WaitForSeconds(3f);

            lines.Add("water_level_engine\tZoneSystem.c_WaterLevel\t"
                      + ZoneSystem.c_WaterLevel.ToString("0.####"));
            lines.Add("water_level_instance\tZoneSystem.instance.m_waterLevel\t"
                      + ZoneSystem.instance.m_waterLevel.ToString("0.####"));

            // THE CENSUS RUNS BEFORE ANY ZONE IS SPAWNED, deliberately.  A
            // `SpawnZone` in Client mode creates no ZDOs, but "deliberately
            // before" is cheaper to defend than "measured not to matter": the
            // census is then unambiguously a reading of the SAVE and of
            // nothing this harness did.
            if (zdosPath.Length > 0)
            {
                int n = WriteZdoCensus(zdosPath);
                lines.Add("zdo_census\t" + zdosPath + "\t" + n);
                SSys.Console.Error.WriteLine("[waterproof] zdo census " + n + " -> " + zdosPath);
            }
            // The FIELD census, on the same pre-spawn pass and for the same
            // reason: it is a reading of the SAVE.  It is a second walk of
            // `m_objectsByID` rather than a column on the first, because the
            // first is read by every section of the gate and adding a
            // caller-chosen predicate to it would put the predicate inside
            // the shared instrument.
            if (zdoStringsSpec.Length > 0)
            {
                if (zdoFieldsPath.Length == 0)
                {
                    SSys.Console.Error.WriteLine(
                        "[waterproof] WATERPROOF_ZDO_STRINGS set without "
                        + "WATERPROOF_ZDO_FIELDS; refusing to drop the reading");
                    yield break;
                }
                foreach (string pop in WriteZdoFields(zdoStringsSpec, zdoFieldsPath))
                    lines.Add(pop);
                SSys.Console.Error.WriteLine("[waterproof] zdo fields -> " + zdoFieldsPath);
            }

            // THE WALKER, read off the Player prefab rather than assumed.
            // Every acceptance number about "can a player get along this" is
            // a function of two quantities: the angle above which the player
            // SLIDES, and how wide the player is.  Both have been guessed in
            // this project before -- an 8 m gradient baseline and a
            // hand-picked corridor width -- so both are emitted here, from
            // the game's own prefab, and the gate refuses to run without
            // them.  `Character.GetSlideAngle` is a switch on the character
            // kind and cannot be called on a prefab that has no ZNetView
            // awake, so the literal it returns for a player
            // (c_MinSlideDegreesPlayer) is recovered by reading the field the
            // Player prefab actually carries where one exists and falling
            // back to the constant the assembly declares.
            GameObject playerPrefab = ZNetScene.instance == null
                ? null : ZNetScene.instance.GetPrefab("Player");
            CapsuleCollider cap = playerPrefab == null
                ? null : playerPrefab.GetComponentInChildren<CapsuleCollider>(true);
            lines.Add("walker\tPlayer\t"
                      + (playerPrefab == null ? "NO_PREFAB" : "prefab")
                      + "\tcapsule_radius_m\t"
                      + (cap == null ? "nan" : cap.radius.ToString("0.####"))
                      + "\tcapsule_height_m\t"
                      + (cap == null ? "nan" : cap.height.ToString("0.####")));

            // KEEP THE SPAWNED OBJECTS ALIVE.  MEASURED in the second run of this harness:
            // all four test hulls came back DESTROYED_BEFORE_SETTLE, because a dedicated
            // server with no peers has no reference position, so `ZoneSystem` holds no
            // active zones and `ZNetScene.CreateDestroyObjects` culls everything the
            // harness instantiates within a frame or two.  A compiler does not care - it
            // has already poked the heightmap by then - but a hull that is deleted before
            // physics runs cannot answer whether it floats.  `ZNet.SetReferencePosition`
            // is the server's own hook for "somebody is here" and is re-asserted every
            // second, because anything that recomputes it would otherwise take the
            // harbour back out of range mid-settle.
            if (!string.IsNullOrEmpty(refSpec))
            {
                string[] rp = refSpec.Split(',');
                var refPos = new Vector3(ParseF(rp[0]), ZoneSystem.c_WaterLevel, ParseF(rp[1]));
                StartCoroutine(HoldReference(refPos));
                lines.Add("reference_position\t" + refPos.x.ToString("0.###") + "\t"
                          + refPos.z.ToString("0.###"));
                yield return new WaitForSeconds(2f);
            }

            var zones = new List<Vector2s>();
            foreach (string spec in zonesSpec.Split(';'))
            {
                if (spec.Trim().Length == 0) continue;
                string[] parts = spec.Split(',');
                zones.Add(new Vector2s(int.Parse(parts[0].Trim()), int.Parse(parts[1].Trim())));
            }

            // SpawnZone returns false while HeightmapBuilder is still building the zone's
            // terrain asynchronously, so it is retried rather than called once.
            foreach (Vector2s zone in zones)
            {
                bool ok = false;
                for (int tries = 0; tries < 200 && !ok; tries++)
                {
                    object[] spawnArgs = { zone, ZoneSystem.SpawnMode.Client, null };
                    ok = (bool)SpawnZoneMethod.Invoke(ZoneSystem.instance, spawnArgs);
                    if (!ok) yield return new WaitForSeconds(0.25f);
                }
                lines.Add("zone\t" + zone.x + "\t" + zone.y + "\tspawned=" + ok);
                SSys.Console.Error.WriteLine("[waterproof] SpawnZone " + zone.x + "," + zone.y + " -> " + ok);
            }
            yield return new WaitForSeconds(2f);

            ReportVolumes("before");

            var points = new List<KeyValuePair<string, Vector3>>();
            foreach (string spec in pointsSpec.Split(';'))
            {
                if (spec.Trim().Length == 0) continue;
                string[] parts = spec.Split(',');
                points.Add(new KeyValuePair<string, Vector3>(
                    parts[0].Trim(),
                    new Vector3(ParseF(parts[1]), 0f, ParseF(parts[2]))));
            }
            if (pointsFile.Length > 0 && SIO.File.Exists(pointsFile))
            {
                foreach (string line in SIO.File.ReadAllLines(pointsFile))
                {
                    if (line.Trim().Length == 0 || line[0] == '#') continue;
                    string[] parts = line.Split('\t');
                    if (parts.Length < 3) continue;
                    points.Add(new KeyValuePair<string, Vector3>(
                        parts[0].Trim(),
                        new Vector3(ParseF(parts[1]), 0f, ParseF(parts[2]))));
                }
                lines.Add("points_file\t" + pointsFile + "\t" + points.Count);
            }

            var genH = new float[points.Count];
            var surfBefore = new float[points.Count];
            for (int i = 0; i < points.Count; i++)
            {
                genH[i] = Ground(points[i].Value);
                surfBefore[i] = Surface(points[i].Value, ZoneSystem.c_WaterLevel - 0.1f);
            }

            // The write.  A ZDO carrying the synthesised TCData, then the game's own object
            // factory, so TerrainComp.Awake -> Initialize -> CheckLoad -> Load -> Heightmap.Poke
            // -> ApplyToHeightmap all run for real, clamp included.
            int created = 0;
            if (fromSave)
            {
                // The POST-BUILD path: the save already holds the compilers, so
                // the game is asked to instantiate ITS OWN bytes.  This is the
                // difference between "we can read what we wrote" and "the world
                // holds what we meant" -- only the second one is a verification.
                // `GetAllZDOsWithPrefabIterative` IS PAGED AND MUST BE CALLED
                // IN A LOOP UNTIL IT RETURNS TRUE.  Calling it once returns
                // only the first page, and this harness did exactly that --
                // which reported `compilers_from_save 0 of_prefab_total 75`
                // on a world whose four dredge compilers were all present and
                // live (1 each at (1024,704) (1024,768) (1088,704)
                // (1088,768), measured by scoped `objects_count` inside
                // 31 m).  With no compiler instantiated, every point then
                // read the GENERATED bed, and the basin's depth came back
                // min 1.593 m -- which is precisely the PRE-dredge depth
                // (design depth 6.000 minus the worst cut 4.4264 = 1.574).
                // So the instrument confidently reported "no dredge" on a
                // dredged world: a verification that could not see the thing
                // it was asked about.
                //
                // MEASURED from the deployed assembly (md5
                // 89ffdb64fefebc011f5a9a826f2968bf): the GAME's own caller
                // does `callvirt GetAllZDOsWithPrefabIterative` at IL_00d4
                // followed by `brfalse.s IL_00cb` -- branch BACK to the call
                // on false -- so false means "more pages remain".  The method
                // never clears the list (no `Clear` in its body, one `Add`
                // per ZDO), so successive calls accumulate.
                var zdos = new List<ZDO>();
                int index = 0;
                int pages = 0;
                while (!ZDOMan.instance.GetAllZDOsWithPrefabIterative(
                           CompilerPrefab, zdos, ref index))
                {
                    pages++;
                    if (pages > 10000) break;   // never spin forever on a
                                                // semantic change
                }
                pages++;
                foreach (ZDO zdo in zdos)
                {
                    Vector2s zn = ZoneSystem.GetZone(zdo.GetPosition());
                    bool wanted = false;
                    foreach (Vector2s z in zones) if (z.x == zn.x && z.y == zn.y) wanted = true;
                    if (!wanted) continue;
                    byte[] data = zdo.GetByteArray(ZDOVars.s_TCData, null);
                    GameObject go = (GameObject)CreateObjectMethod.Invoke(
                        ZNetScene.instance, new object[] { zdo });
                    if (go != null) created++;
                    lines.Add("compiler_from_save\t" + zn.x + "\t" + zn.y
                              + "\tpos=" + zdo.GetPosition().x + "," + zdo.GetPosition().z
                              + "\tbytes=" + (data == null ? -1 : data.Length)
                              + "\tcreated=" + (go != null));
                }
                lines.Add("compilers_from_save\t" + created + "\tof_prefab_total\t"
                          + zdos.Count + "\tpages\t" + pages);

                // THE DOOR TEST.  `fb_fortress_portcullis` carries a real
                // `Door` with `m_keyItem = null`, and "closable" has so far
                // been a COMPONENT FACT rather than an operation anyone
                // performed.  A door that is present is not a door that
                // works, so this instantiates the panels the SAVE holds and
                // actually drives them: read the state, close, read back,
                // reopen, read back, then restore whatever state was there.
                //
                // `Door.SetState(int)` and `Door.UpdateState()` are public
                // (MEASURED from the deployed assembly), so this needs no
                // Humanoid and no reflection -- `Interact` wants a character
                // and a headless server has none.
                if (doorsSpec.Trim().Length > 0)
                {
                    var dz = new List<ZDO>();
                    int di = 0, dpages = 0;
                    while (!ZDOMan.instance.GetAllZDOsWithPrefabIterative(
                               doorsSpec.Trim(), dz, ref di))
                    {
                        dpages++;
                        if (dpages > 10000) break;
                    }
                    dpages++;
                    int tested = 0, worked = 0;
                    foreach (ZDO zdo in dz)
                    {
                        Vector2s zn = ZoneSystem.GetZone(zdo.GetPosition());
                        bool wanted = false;
                        foreach (Vector2s z in zones) if (z.x == zn.x && z.y == zn.y) wanted = true;
                        if (!wanted) continue;
                        GameObject go = (GameObject)CreateObjectMethod.Invoke(
                            ZNetScene.instance, new object[] { zdo });
                        if (go == null)
                        {
                            lines.Add("door\t" + doorsSpec + "\tpos="
                                      + zdo.GetPosition().x + "," + zdo.GetPosition().z
                                      + "\tinstantiated=False");
                            continue;
                        }
                        Door dr = go.GetComponent<Door>();
                        if (dr == null)
                        {
                            lines.Add("door\t" + doorsSpec + "\tpos="
                                      + zdo.GetPosition().x + "," + zdo.GetPosition().z
                                      + "\tno_Door_component=True");
                            continue;
                        }
                        tested++;
                        // THE DATA FLOW RUNS THE OTHER WAY, and the first
                        // version of this test had it backwards.  MEASURED
                        // from the deployed assembly:
                        //
                        //   UpdateState()  reads ZDO.GetInt(s_state) and
                        //                  passes it to SetState();
                        //   SetState(int)  drives m_animator and the open/
                        //                  close EffectLists. It does NOT
                        //                  write the ZDO.
                        //
                        // So the ZDO is the persisted truth and the Animator
                        // is the panel that physically moves. Calling
                        // SetState directly moved nothing observable and read
                        // back 0 six times -- a test driving the display and
                        // then checking the display's source.
                        //
                        // The real operation therefore writes the ZDO, calls
                        // UpdateState(), and reads the ANIMATOR back. That is
                        // the panel responding, not the harness echoing
                        // itself. `Door.SetState` also early-returns when the
                        // animator already holds the requested value, so the
                        // animator is a genuine independent observable.
                        //
                        // All three members are PRIVATE in the deployed
                        // assembly (`.method private hidebysig`), so they are
                        // reached by reflection -- the same route this harness
                        // already uses for `ZNetScene.CreateObject`.
                        var tDoor = typeof(Door);
                        var mUpd = tDoor.GetMethod("UpdateState", NonPub);
                        var mCan = tDoor.GetMethod("CanInteract", NonPub);
                        var fAnim = tDoor.GetField("m_animator", NonPub);
                        if (mUpd == null || fAnim == null)
                        {
                            lines.Add("door\t" + doorsSpec + "\tpos="
                                      + zdo.GetPosition().x + "," + zdo.GetPosition().z
                                      + "\tno_UpdateState_or_animator=True");
                            continue;
                        }
                        var anim = (UnityEngine.Animator)fAnim.GetValue(dr);
                        bool can = mCan != null
                            && (bool)mCan.Invoke(dr, new object[0]);
                        int s0 = zdo.GetInt(ZDOVars.s_state, 0);
                        int a0 = anim == null ? -999 : anim.GetInteger("state");

                        zdo.Set(ZDOVars.s_state, 1);        // OPEN
                        mUpd.Invoke(dr, new object[0]);
                        int aOpen = anim == null ? -999 : anim.GetInteger("state");

                        zdo.Set(ZDOVars.s_state, 0);        // CLOSE
                        mUpd.Invoke(dr, new object[0]);
                        int aClosed = anim == null ? -999 : anim.GetInteger("state");

                        zdo.Set(ZDOVars.s_state, 1);        // REOPEN
                        mUpd.Invoke(dr, new object[0]);
                        int aReopen = anim == null ? -999 : anim.GetInteger("state");

                        zdo.Set(ZDOVars.s_state, s0);       // restore
                        mUpd.Invoke(dr, new object[0]);
                        int aBack = anim == null ? -999 : anim.GetInteger("state");

                        bool ok = can && anim != null && aOpen == 1
                                  && aClosed == 0 && aReopen == 1 && aBack == s0;
                        if (ok) worked++;
                        lines.Add("door\t" + doorsSpec
                                  + "\tpos=" + zdo.GetPosition().x + ","
                                  + zdo.GetPosition().y + "," + zdo.GetPosition().z
                                  + "\tzone=" + zn.x + "," + zn.y
                                  + "\tcan_interact=" + can
                                  + "\thas_animator=" + (anim != null)
                                  + "\tzdo_state_initial=" + s0
                                  + "\tanim_initial=" + a0
                                  + "\tanim_open=" + aOpen
                                  + "\tanim_closed=" + aClosed
                                  + "\tanim_reopen=" + aReopen
                                  + "\tanim_restored=" + aBack
                                  + "\tcloses_and_reopens=" + ok);
                    }
                    lines.Add("doors\t" + doorsSpec + "\ttested\t" + tested
                              + "\tclose_and_reopen_ok\t" + worked
                              + "\tof_prefab_total\t" + dz.Count
                              + "\tpages\t" + dpages);
                }
            }
            foreach (string spec in blobsSpec.Split(';'))
            {
                if (spec.Trim().Length == 0) continue;
                int c1 = spec.IndexOf(',');
                int c2 = spec.IndexOf(',', c1 + 1);
                int zx = int.Parse(spec.Substring(0, c1).Trim());
                int zz = int.Parse(spec.Substring(c1 + 1, c2 - c1 - 1).Trim());
                byte[] data = SSys.Convert.FromBase64String(spec.Substring(c2 + 1).Trim());
                Vector3 centre = ZoneSystem.GetZonePos(new Vector2s(zx, zz));
                int hash = CompilerPrefab.GetStableHashCode();
                ZDO zdo = ZDOMan.instance.CreateNewZDO(centre, hash);
                // MEASURED, and it cost this harness its first run: the
                // `CreateNewZDO(Vector3, int prefabHash)` overload does NOT put the
                // hash on the ZDO.  ZDOMan::CreateNewZDO(uid, pos, prefabHashIn)
                // passes `prefabHashIn` to `AddIfPortal` ONLY (IL_0014-IL_0024) and
                // never stores it, so `zdo.GetPrefab()` still answers 0 and
                // `ZNetScene.CreateObject` has no prefab to instantiate and returns
                // null.  `ZDO.SetPrefab` is the field write (IL_000c `stfld
                // ZDO::m_prefab`), so it is called explicitly.
                zdo.SetPrefab(hash);
                zdo.Set(ZDOVars.s_TCData, data);
                GameObject go = (GameObject)CreateObjectMethod.Invoke(
                    ZNetScene.instance, new object[] { zdo });
                if (go != null) created++;
                lines.Add("compiler\t" + zx + "\t" + zz + "\tcentre=" + centre.x + "," + centre.z
                          + "\tbytes=" + data.Length + "\tprefab_on_zdo=" + zdo.GetPrefab()
                          + "\twant_hash=" + hash + "\tcreated=" + (go != null)
                          + "\tcomp=" + (go == null ? "none"
                              : (go.GetComponent<TerrainComp>() == null ? "no-TerrainComp" : "TerrainComp")));
                SSys.Console.Error.WriteLine("[waterproof] compiler " + zx + "," + zz
                                             + " bytes=" + data.Length + " created=" + (go != null));
            }
            lines.Add("compilers_created\t" + created);

            // Give TerrainComp.Awake / CheckLoad and the heightmap rebuild a few frames.
            yield return new WaitForSeconds(4f);

            // WARM THE LAYER MASK, and this is a real defect in the first run of this
            // harness rather than a nicety.  MEASURED: `Floating.GetWaterLevel` reads
            // `s_waterVolumeMask` directly (IL_0045) with NO lazy initialisation, while
            // `Floating.GetLiquidLevel` initialises it from
            // `LayerMask.GetMask("WaterVolume")` on its own first call (IL_0000-IL_001a).
            // On a headless server where nothing has floated yet the mask is 0, so
            // `Physics.OverlapSphereNonAlloc` is asked for layer-set 0, matches nothing and
            // every point reads -10000 -- including 6.7 m of undisturbed natural water.
            // One `GetLiquidLevel` call sets the mask; `Physics.SyncTransforms` makes the
            // just-instantiated colliders visible to the queries.
            Physics.SyncTransforms();
            float warm = Floating.GetLiquidLevel(
                new Vector3(points.Count > 0 ? points[0].Value.x : 0f,
                            ZoneSystem.c_WaterLevel - 0.1f,
                            points.Count > 0 ? points[0].Value.z : 0f));
            lines.Add("mask_warm\tFloating.GetLiquidLevel\t" + warm.ToString("0.####"));
            yield return new WaitForSeconds(1f);

            ReportVolumes("after");

            for (int i = 0; i < points.Count; i++)
            {
                Vector3 p = points[i].Value;
                float app = Ground(p);
                // Three altitudes, because "is there water here" is "does a water volume's
                // collider contain THIS point": just under the plane, deep in the water
                // column, and a quarter metre off the applied bed.  If the volume reaches the
                // bed, all three answer the same surface.
                float sJustUnder = Surface(p, ZoneSystem.c_WaterLevel - 0.1f);
                float sMid = Surface(p, 0.5f * (app + ZoneSystem.c_WaterLevel));
                float sAtBed = Surface(p, app + 0.25f);
                bool underJustUnder = Under(p, ZoneSystem.c_WaterLevel - 0.1f);
                bool underAtBed = Under(p, app + 0.25f);
                float normDepth = NormalisedDepth(p, ZoneSystem.c_WaterLevel - 0.1f);
                float oceanDepth = OceanDepth(p);
                // The SAME number read without the physics query: walk WaterVolume.Instances,
                // take the volume whose collider bounds contain the point, and ask it for its
                // surface directly.  Reported alongside the Floating.* readings because the
                // two answer the same question by different routes, and on a headless server
                // the physics route has a startup order the direct route does not.
                int nvol;
                float sDirectJustUnder = DirectSurface(p, ZoneSystem.c_WaterLevel - 0.1f, out nvol);
                int nvolBed;
                float sDirectAtBed = DirectSurface(p, app + 0.25f, out nvolBed);
                lines.Add(string.Join("\t", new string[] {
                    "point", points[i].Key,
                    p.x.ToString("0.###"), p.z.ToString("0.###"),
                    genH[i].ToString("0.####"),                                  // generated bed
                    app.ToString("0.####"),                                      // applied bed
                    (app - genH[i]).ToString("0.####"),                          // delta actually applied
                    (ZoneSystem.c_WaterLevel - genH[i]).ToString("0.####"),      // depth before
                    (ZoneSystem.c_WaterLevel - app).ToString("0.####"),          // depth after
                    surfBefore[i].ToString("0.####"),                            // water surface before
                    sJustUnder.ToString("0.####"),
                    sMid.ToString("0.####"),
                    sAtBed.ToString("0.####"),
                    underJustUnder ? "1" : "0",
                    underAtBed ? "1" : "0",
                    normDepth.ToString("0.####"),
                    oceanDepth.ToString("0.####"),
                    sDirectJustUnder.ToString("0.####"),
                    sDirectAtBed.ToString("0.####"),
                    nvol.ToString(), nvolBed.ToString(),
                }));
            }

            // The float test.  Instantiated AFTER the terrain write so the hull meets the
            // dredged bed, not the generated one.
            var ships = new List<GameObject>();
            var shipLabels = new List<string>();
            var shipStart = new List<Vector3>();
            foreach (string spec in shipsSpec.Split(';'))
            {
                if (spec.Trim().Length == 0) continue;
                string[] parts = spec.Split(',');
                string label = parts[0].Trim();
                string prefabName = parts[1].Trim();
                var at = new Vector3(ParseF(parts[2]), ZoneSystem.c_WaterLevel, ParseF(parts[3]));
                GameObject prefab = ZNetScene.instance.GetPrefab(prefabName);
                if (prefab == null)
                {
                    lines.Add("ship\t" + label + "\t" + prefabName + "\tNO_SUCH_PREFAB");
                    continue;
                }
                GameObject go = Instantiate(prefab, at, Quaternion.identity);
                ZNetView nv = null;
                if (go != null) nv = go.GetComponent<ZNetView>();
                // DETACH THE HULL FROM THE SCENE CULL, and the reason is an engine fact
                // worth writing down: `Game.FixedUpdate` calls
                // `ZNet.SetReferencePosition(new Vector3(1000000, 0, 1000000))`
                // UNCONDITIONALLY, every physics tick (IL_0019-IL_0032).  On a dedicated
                // server that IS the reference position, so `ZNetScene.CreateDestroyObjects`
                // asks `ZDOMan.FindSectorObjects` about sector (15625, 15625) and
                // `RemoveObjects` then destroys every ZNetView in `m_instances` whose ZDO was
                // not earmarked this frame -- which is every object this harness spawns.
                // MEASURED: all four hulls read `gone` on the first one-second tick, and
                // holding the reference position from a coroutine loses the race with
                // FixedUpdate.  `RemoveObjects` only ever looks at `m_instances`
                // (IL_0077-IL_00a6), so taking the hull's entry out of that dictionary takes
                // it out of the cull without touching its ZNetView, its ZDO or its physics.
                if (nv != null && nv.GetZDO() != null)
                {
                    var f = typeof(ZNetScene).GetField("m_instances",
                        SSys.Reflection.BindingFlags.Instance | SSys.Reflection.BindingFlags.NonPublic
                        | SSys.Reflection.BindingFlags.Public);
                    var map = f == null ? null
                        : f.GetValue(ZNetScene.instance) as Dictionary<ZDO, ZNetView>;
                    if (map != null) map.Remove(nv.GetZDO());
                }
                lines.Add("ship_spawn\t" + label + "/" + prefabName
                          + "\tinstantiated=" + (go != null)
                          + "\tnview=" + (nv == null ? "none" : "yes")
                          + "\towner=" + (nv == null || nv.GetZDO() == null ? "n/a"
                                          : nv.IsOwner().ToString())
                          + "\tref=" + ZNet.instance.GetReferencePosition().ToString("0.#")
                          + "\tref_zone=" + ZoneSystem.GetZone(ZNet.instance.GetReferencePosition())
                          + "\tship_zone=" + ZoneSystem.GetZone(at)
                          + "\tin_active_area="
                          + ZNetScene.InActiveArea(at, ZNet.instance.GetReferencePosition()));
                ships.Add(go);
                shipLabels.Add(label + "/" + prefabName);
                shipStart.Add(at);
            }
            // Survival is sampled every second rather than only at the end, so a hull that
            // is culled can be distinguished from a hull that sank, and the frame it went
            // is on the record.
            for (float t = 0f; t < settle; t += 1f)
            {
                yield return new WaitForSeconds(1f);
                var alive = new SText.StringBuilder();
                for (int i = 0; i < ships.Count; i++)
                {
                    if (i > 0) alive.Append(" ");
                    alive.Append(shipLabels[i]).Append("=");
                    alive.Append(ships[i] == null ? "gone"
                                 : ships[i].transform.position.y.ToString("0.###"));
                }
                lines.Add("ship_tick\t" + (t + 1f).ToString("0") + "\t" + alive);
            }
            for (int i = 0; i < ships.Count; i++)
            {
                GameObject go = ships[i];
                if (go == null)
                {
                    // ZNetScene culls objects outside its active zones; say so rather than
                    // reporting a zero as a measurement.
                    lines.Add("ship\t" + shipLabels[i] + "\tDESTROYED_BEFORE_SETTLE");
                    continue;
                }
                Vector3 now = go.transform.position;
                float bed = Ground(now);
                float surf = Surface(now, ZoneSystem.c_WaterLevel - 0.1f);
                var ship = go.GetComponent<Ship>();
                lines.Add(string.Join("\t", new string[] {
                    "ship", shipLabels[i],
                    shipStart[i].x.ToString("0.###"), shipStart[i].z.ToString("0.###"),
                    now.x.ToString("0.###"), now.y.ToString("0.####"), now.z.ToString("0.###"),
                    bed.ToString("0.####"),
                    surf.ToString("0.####"),
                    (now.y - bed).ToString("0.####"),                            // hull above bed
                    (now.y - ZoneSystem.c_WaterLevel).ToString("0.####"),        // hull vs plane
                    ship == null ? "no-ship-component"
                                 : ship.m_waterLevelOffset.ToString("0.####"),
                }));
            }

            SIO.File.WriteAllText(outPath, string.Join("\n", lines.ToArray()) + "\n",
                                  new SText.UTF8Encoding(false));
            SSys.Console.Error.WriteLine("[waterproof] wrote " + outPath + " lines=" + lines.Count);
            SSys.Diagnostics.Process.GetCurrentProcess().Kill();
        }

        // --- the object census ------------------------------------------------------------
        //
        // `ZDOMan.m_objectsByID` is private, so it is reached the same way
        // `SpawnZone` and `CreateObject` are: by reflection, because the point
        // is to read the GAME's loaded save rather than to reimplement its
        // file format.  Every ZDO in the world, one row each, no paging, no
        // radius, no reply cap.
        //
        // The row is deliberately RAW - hash, name, position, quaternion - and
        // carries no classification at all.  Deciding here what counts as
        // "vegetation" or "a blocker" would put the predicate inside the
        // instrument, and this project has already been bitten once by a count
        // that answered a different question from the one asked ("184 objects
        // left standing" was a census of CLEARABLE vegetation, quoted in
        // answer to a question about structures).  The instrument reports the
        // population; the gate chooses the predicate, in the open.
        static readonly SSys.Reflection.FieldInfo ObjectsByIDField =
            typeof(ZDOMan).GetField("m_objectsByID", NonPub);

        int WriteZdoCensus(string path)
        {
            if (ZDOMan.instance == null || ObjectsByIDField == null)
            {
                SSys.Console.Error.WriteLine("[waterproof] no ZDOMan / no m_objectsByID field");
                return -1;
            }
            var byId = (Dictionary<ZDOID, ZDO>)ObjectsByIDField.GetValue(ZDOMan.instance);
            if (byId == null) return -1;
            var sb = new SText.StringBuilder(1 << 22);
            sb.Append("# hash\tname\tx\ty\tz\tqx\tqy\tqz\tqw\n");
            // Prefab-name resolution is CACHED per hash, not looked up per ZDO:
            // a quarter of a million `ZNetScene.GetPrefab` calls is the
            // difference between a census that finishes and one that does not.
            // A hash with no prefab in this ZNetScene (a modded prefab in a
            // mod-free sandbox) resolves to the empty string rather than to a
            // guess; the caller maps those by recomputing the stable hash of
            // names it already knows.
            var names = new Dictionary<int, string>();
            int n = 0;
            foreach (KeyValuePair<ZDOID, ZDO> kv in byId)
            {
                ZDO zdo = kv.Value;
                if (zdo == null) continue;
                int hash = zdo.GetPrefab();
                string name;
                if (!names.TryGetValue(hash, out name))
                {
                    GameObject pf = ZNetScene.instance == null
                        ? null : ZNetScene.instance.GetPrefab(hash);
                    name = pf == null ? "" : pf.name;
                    names[hash] = name;
                }
                Vector3 p = zdo.GetPosition();
                Quaternion q = zdo.GetRotation();
                sb.Append(hash).Append('\t').Append(name).Append('\t')
                  .Append(p.x.ToString("0.####")).Append('\t')
                  .Append(p.y.ToString("0.####")).Append('\t')
                  .Append(p.z.ToString("0.####")).Append('\t')
                  .Append(q.x.ToString("0.######")).Append('\t')
                  .Append(q.y.ToString("0.######")).Append('\t')
                  .Append(q.z.ToString("0.######")).Append('\t')
                  .Append(q.w.ToString("0.######")).Append('\n');
                n++;
            }
            SIO.File.WriteAllText(path, sb.ToString(), new SText.UTF8Encoding(false));
            return n;
        }

        // --- the FIELD census: ZDO data, not ZDO geometry --------------------------------
        //
        // One selector is `prefab:key[:type]`.  The prefab is matched on the
        // resolved prefab NAME (`*` matches every ZDO); the key is hashed
        // with the game's own `GetStableHashCode`, which is how the game
        // itself addresses ZDO fields, so `tag` here is the same `tag`
        // `TeleportWorld` reads.  The type says which typed getter is used,
        // because `ZDOExtraData` keeps strings, ints, longs and floats in
        // four separate tables and asking the wrong one returns the default
        // rather than an error -- an empty answer that looks like a blank
        // field is the exact failure mode this census exists to expose, so
        // the type is caller-stated and printed back on the population line.
        //
        // A DEFAULT IS NOT A BLANK AND NEITHER IS SILENT.  Every ZDO of a
        // selected prefab is counted; the ones carrying no value are counted
        // separately and their positions are still emitted with an empty
        // value, because "this arch has no tag" is a finding and dropping the
        // row would make it unfindable.
        string[] WriteZdoFields(string spec, string path)
        {
            var pops = new List<string>();
            if (ZDOMan.instance == null || ObjectsByIDField == null)
            {
                SSys.Console.Error.WriteLine("[waterproof] no ZDOMan / no m_objectsByID field");
                return new string[] { "zdo_fields\tNO_ZDOMAN" };
            }
            var byId = (Dictionary<ZDOID, ZDO>)ObjectsByIDField.GetValue(ZDOMan.instance);
            if (byId == null) return new string[] { "zdo_fields\tNO_OBJECTS" };

            var wantPrefab = new List<string>();
            var wantKey = new List<string>();
            var wantHash = new List<int>();
            var wantType = new List<string>();
            var seen = new List<int>();        // ZDOs of this selector's prefab
            var held = new List<int>();        // ... carrying a non-default value
            bool any = false;
            foreach (string sel in spec.Split(','))
            {
                string s = sel.Trim();
                if (s.Length == 0) continue;
                string[] f = s.Split(':');
                if (f.Length < 2)
                {
                    pops.Add("zdo_fields_bad_selector\t" + s);
                    continue;
                }
                wantPrefab.Add(f[0].Trim());
                wantKey.Add(f[1].Trim());
                wantHash.Add(f[1].Trim().GetStableHashCode());
                wantType.Add(f.Length > 2 ? f[2].Trim() : "str");
                seen.Add(0);
                held.Add(0);
                if (f[0].Trim() == "*") any = true;
            }

            var sb = new SText.StringBuilder(1 << 20);
            sb.Append("# kind\tprefab\tx\ty\tz\tkey\tvalue\n");
            var names = new Dictionary<int, string>();
            int rows = 0;
            foreach (KeyValuePair<ZDOID, ZDO> kv in byId)
            {
                ZDO zdo = kv.Value;
                if (zdo == null) continue;
                int hash = zdo.GetPrefab();
                string name;
                if (!names.TryGetValue(hash, out name))
                {
                    GameObject pf = ZNetScene.instance == null
                        ? null : ZNetScene.instance.GetPrefab(hash);
                    name = pf == null ? "" : pf.name;
                    names[hash] = name;
                }
                for (int i = 0; i < wantPrefab.Count; i++)
                {
                    if (!any && wantPrefab[i] != name) continue;
                    if (any && wantPrefab[i] != "*" && wantPrefab[i] != name) continue;
                    seen[i] = seen[i] + 1;
                    string val;
                    bool got;
                    switch (wantType[i])
                    {
                        case "int":
                            int iv = zdo.GetInt(wantHash[i], int.MinValue);
                            got = iv != int.MinValue;
                            val = got ? iv.ToString() : "";
                            break;
                        case "long":
                            long lv = zdo.GetLong(wantHash[i], long.MinValue);
                            got = lv != long.MinValue;
                            val = got ? lv.ToString() : "";
                            break;
                        case "float":
                            float fv = zdo.GetFloat(wantHash[i], float.NaN);
                            got = !float.IsNaN(fv);
                            val = got ? fv.ToString("0.######") : "";
                            break;
                        default:
                            val = zdo.GetString(wantHash[i], "");
                            got = val.Length > 0;
                            break;
                    }
                    if (got) held[i] = held[i] + 1;
                    // A NAMED prefab emits its blanks: "this arch has no tag"
                    // is a finding with a position and dropping the row makes
                    // it unfindable.  A `*` selector does NOT, because 491,792
                    // rows of nothing would bury the ones that say something;
                    // its blanks are carried by the population line instead,
                    // which is why that line is not optional.
                    if (!got && wantPrefab[i] == "*") continue;
                    // A newline inside a sign's text would otherwise end the
                    // row and turn one board into two; the escape is undone by
                    // the reader.
                    val = val.Replace("\\", "\\\\").Replace("\n", "\\n")
                             .Replace("\r", "\\r").Replace("\t", "\\t");
                    Vector3 p = zdo.GetPosition();
                    sb.Append("zdofield\t").Append(name).Append('\t')
                      .Append(p.x.ToString("0.####")).Append('\t')
                      .Append(p.y.ToString("0.####")).Append('\t')
                      .Append(p.z.ToString("0.####")).Append('\t')
                      .Append(wantKey[i]).Append('\t').Append(val).Append('\n');
                    rows++;
                }
            }
            SIO.File.WriteAllText(path, sb.ToString(), new SText.UTF8Encoding(false));
            for (int i = 0; i < wantPrefab.Count; i++)
                pops.Add("zdo_field_population\t" + wantPrefab[i] + "\t" + wantKey[i]
                         + "\t" + wantType[i] + "\tzdos\t" + seen[i]
                         + "\twith_value\t" + held[i]
                         + "\tblank\t" + (seen[i] - held[i]));
            pops.Add("zdo_fields\t" + path + "\t" + rows);
            return pops.ToArray();
        }

        // Re-assert the server's reference position once a second for the life of the run.
        IEnumerator HoldReference(Vector3 at)
        {
            while (true)
            {
                if (ZNet.instance != null) ZNet.instance.SetReferencePosition(at);
                yield return new WaitForSeconds(1f);
            }
        }

        // --- the game's own readings, wrapped so a miss is NaN and never a zero -----------

        static float Ground(Vector3 p)
        {
            float h;
            return Heightmap.GetHeight(p, out h) ? h : float.NaN;
        }

        static float Surface(Vector3 p, float y)
        {
            WaterVolume vol = null;
            // Floating.GetWaterLevel returns -10000 when no water volume's collider contains
            // the point, which is the engine's own "there is no water here".
            return Floating.GetWaterLevel(new Vector3(p.x, y, p.z), ref vol);
        }

        static bool Under(Vector3 p, float y)
        {
            WaterVolume vol = null;
            return Floating.IsUnderWater(new Vector3(p.x, y, p.z), ref vol);
        }

        static float NormalisedDepth(Vector3 p, float y)
        {
            var at = new Vector3(p.x, y, p.z);
            foreach (WaterVolume wv in WaterVolume.Instances)
            {
                if (wv == null) continue;
                Collider c = wv.GetComponent<Collider>();
                if (c == null || !c.bounds.Contains(at)) continue;
                return wv.Depth(at);
            }
            return float.NaN;
        }

        static float DirectSurface(Vector3 p, float y, out int nvol)
        {
            var at = new Vector3(p.x, y, p.z);
            nvol = 0;
            float best = float.NaN;
            foreach (WaterVolume wv in WaterVolume.Instances)
            {
                if (wv == null) continue;
                Collider c = wv.GetComponent<Collider>();
                if (c == null || !c.bounds.Contains(at)) continue;
                nvol++;
                float s = wv.GetWaterSurface(at, 1f);
                if (float.IsNaN(best) || s > best) best = s;
            }
            return best;
        }

        static float OceanDepth(Vector3 p)
        {
            Heightmap hm = Heightmap.FindHeightmap(p);
            return hm == null ? float.NaN : hm.GetOceanDepth(p);
        }

        void ReportVolumes(string phase)
        {
            List<WaterVolume> all = WaterVolume.Instances;
            lines.Add("volumes\t" + phase + "\tcount=" + (all == null ? -1 : all.Count));
            if (all == null) return;
            foreach (WaterVolume wv in all)
            {
                if (wv == null) continue;
                Collider c = wv.GetComponent<Collider>();
                Bounds b = c == null ? new Bounds() : c.bounds;
                Vector3 t = wv.transform.position;
                lines.Add(string.Join("\t", new string[] {
                    "volume", phase,
                    t.x.ToString("0.###"), t.y.ToString("0.####"), t.z.ToString("0.###"),
                    b.center.x.ToString("0.###"), b.center.y.ToString("0.####"), b.center.z.ToString("0.###"),
                    b.size.x.ToString("0.###"), b.size.y.ToString("0.####"), b.size.z.ToString("0.###"),
                    b.min.y.ToString("0.####"), b.max.y.ToString("0.####"),
                    wv.m_surfaceOffset.ToString("0.####"),
                    wv.m_forceDepth.ToString("0.####"),
                    wv.m_useGlobalWind ? "1" : "0",
                    ((int)wv.GetLiquidType()).ToString(),
                    wv.m_heightmap == null ? "no-heightmap" : "heightmap",
                    c == null ? "no-collider" : (c.isTrigger ? "trigger" : "solid"),
                }));
            }
        }

        static float ParseF(string s)
        {
            return float.Parse(s.Trim(), global::System.Globalization.CultureInfo.InvariantCulture);
        }
    }
}
