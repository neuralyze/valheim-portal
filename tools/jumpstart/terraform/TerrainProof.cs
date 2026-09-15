// TerrainProof - prove that synthesised TCData is accepted by the GAME, not
// just by our own reader.
//
// The problem this solves.  Writing a `_TerrainCompiler` ZDO with a handmade
// TCData blob is easy to get subtly wrong: a transposed index, a half-metre
// offset, an array length the game rejects, a delta the game clamps away.
// Reading the blob back with our own parser proves only that we can read what
// we wrote.  The only honest test is to hand the save to Valheim's own code and
// ask the resulting terrain how high it is.
//
// How it does that, using nothing but the game's own methods:
//   ZoneSystem.SpawnZone(zoneID, SpawnMode.Client, out root)
//       instantiates the real zone, which builds a real Heightmap from the
//       world generator.  Client mode so no vegetation or location ZDOs are
//       created - this harness must not modify the save it is measuring.
//   Heightmap.GetHeight(worldPos, out h)
//       the ground height the game would use, sampled BEFORE any compiler is
//       instantiated: that is the generated surface.
//   ZNetScene.CreateObject(zdo)
//       instantiates the _TerrainCompiler from its ZDO, which runs
//       TerrainComp.Awake -> Initialize -> CheckLoad -> Load (Utils.Decompress,
//       the array-length validation, the modified/level/smooth walk) and then
//       Heightmap.Poke -> ApplyToHeightmap.
//   Heightmap.GetHeight(worldPos, out h) again
//       the ground height AFTER the edit.  If this equals the target altitude,
//       the game accepted the bytes, agreed with our index convention and did
//       not clamp the delta.
//
// Env vars:
//   TERRAINPROOF_ZONES   semicolon list of "zx,zz" zones to spawn
//   TERRAINPROOF_POINTS  semicolon list of "label,x,z" world points to sample
//   TERRAINPROOF_OUT     output text file
//
// Output is one TSV line per point: label, x, z, before, after, delta.
// Like PatchScan, this kills the process when it is done, so a non-zero exit is
// expected and the output file is the only success signal.

using System.Collections;
using System.Collections.Generic;
using BepInEx;
using UnityEngine;
using SIO = System.IO;
using SSys = System;
using SText = System.Text;

namespace TerrainProof
{
    [BepInPlugin("terraform.terrainproof", "TerrainProof", "1.0.0")]
    public class Plugin : BaseUnityPlugin
    {
        const string CompilerPrefab = "_TerrainCompiler";

        // ZoneSystem.SpawnZone and ZNetScene.CreateObject are private, so they are reached by
        // reflection rather than reimplemented: the point of this harness is that the GAME
        // builds the terrain and instantiates the compiler, not a copy of its logic.
        static readonly SSys.Reflection.MethodInfo SpawnZoneMethod =
            typeof(ZoneSystem).GetMethod("SpawnZone",
                SSys.Reflection.BindingFlags.Instance | SSys.Reflection.BindingFlags.NonPublic
                | SSys.Reflection.BindingFlags.Public);
        static readonly SSys.Reflection.MethodInfo CreateObjectMethod =
            typeof(ZNetScene).GetMethod("CreateObject",
                SSys.Reflection.BindingFlags.Instance | SSys.Reflection.BindingFlags.NonPublic
                | SSys.Reflection.BindingFlags.Public);

        void Awake()
        {
            StartCoroutine(Run());
        }

        IEnumerator Run()
        {
            string zonesSpec = SSys.Environment.GetEnvironmentVariable("TERRAINPROOF_ZONES") ?? "";
            string pointsSpec = SSys.Environment.GetEnvironmentVariable("TERRAINPROOF_POINTS") ?? "";
            string outPath = SSys.Environment.GetEnvironmentVariable("TERRAINPROOF_OUT");
            if (string.IsNullOrEmpty(outPath))
            {
                SSys.Console.Error.WriteLine("[terrainproof] TERRAINPROOF_OUT not set");
                yield break;
            }

            // Wait for the dedicated server to finish loading the world.  ZoneSystem is the
            // last of the three to appear, and WorldGenerator.instance is what proves the
            // world itself (and therefore the heightmap source) is live.
            float waited = 0f;
            while (waited < 300f && (ZNet.instance == null || ZoneSystem.instance == null
                                     || ZNetScene.instance == null || WorldGenerator.instance == null))
            {
                yield return new WaitForSeconds(0.5f);
                waited += 0.5f;
            }
            if (ZoneSystem.instance == null || ZNetScene.instance == null)
            {
                SSys.Console.Error.WriteLine("[terrainproof] world never came up");
                yield break;
            }
            yield return new WaitForSeconds(3f);

            var lines = new List<string>();
            var zones = new List<Vector2s>();
            foreach (string spec in zonesSpec.Split(';'))
            {
                if (spec.Trim().Length == 0) continue;
                string[] parts = spec.Split(',');
                zones.Add(new Vector2s(int.Parse(parts[0].Trim()), int.Parse(parts[1].Trim())));
            }

            // SpawnZone returns false while HeightmapBuilder is still building the zone's
            // terrain asynchronously (MEASURED: its first branch is
            // HeightmapBuilder.IsTerrainReady(zonePos, width, scale, WorldGenerator.instance)),
            // so it has to be retried rather than called once.
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
                SSys.Console.Error.WriteLine("[terrainproof] SpawnZone " + zone.x + "," + zone.y + " -> " + ok);
            }
            yield return new WaitForSeconds(2f);

            var points = new List<KeyValuePair<string, Vector3>>();
            foreach (string spec in pointsSpec.Split(';'))
            {
                if (spec.Trim().Length == 0) continue;
                string[] parts = spec.Split(',');
                points.Add(new KeyValuePair<string, Vector3>(
                    parts[0].Trim(),
                    new Vector3(ParseF(parts[1]), 0f, ParseF(parts[2]))));
            }

            var before = new float[points.Count];
            for (int i = 0; i < points.Count; i++)
            {
                float h;
                before[i] = Heightmap.GetHeight(points[i].Value, out h) ? h : float.NaN;
            }

            // Instantiate every _TerrainCompiler the save holds in the spawned zones, through
            // the game's own object factory, so TerrainComp.Load runs for real.
            int created = 0;
            var zdos = new List<ZDO>();
            int index = 0;
            ZDOMan.instance.GetAllZDOsWithPrefabIterative(CompilerPrefab, zdos, ref index);
            foreach (ZDO zdo in zdos)
            {
                Vector2s zone = ZoneSystem.GetZone(zdo.GetPosition());
                bool wanted = false;
                foreach (Vector2s z in zones) if (z.x == zone.x && z.y == zone.y) wanted = true;
                if (!wanted) continue;
                byte[] data = zdo.GetByteArray(ZDOVars.s_TCData, null);
                lines.Add("compiler\t" + zdo.GetPosition().x + "\t" + zdo.GetPosition().z
                          + "\tbytes=" + (data == null ? -1 : data.Length));
                GameObject go = (GameObject)CreateObjectMethod.Invoke(ZNetScene.instance, new object[] { zdo });
                if (go != null) created++;
                SSys.Console.Error.WriteLine("[terrainproof] created compiler at " + zdo.GetPosition()
                    + " tcdata=" + (data == null ? -1 : data.Length));
            }
            lines.Add("compilers_created\t" + created + "\tof\t" + zdos.Count);

            // Give TerrainComp.Awake / CheckLoad and the heightmap rebuild a few frames.
            yield return new WaitForSeconds(4f);

            for (int i = 0; i < points.Count; i++)
            {
                float h;
                float after = Heightmap.GetHeight(points[i].Value, out h) ? h : float.NaN;
                lines.Add("point\t" + points[i].Key + "\t" + points[i].Value.x + "\t" + points[i].Value.z
                          + "\t" + before[i].ToString("0.####") + "\t" + after.ToString("0.####")
                          + "\t" + (after - before[i]).ToString("0.####"));
            }

            SIO.File.WriteAllText(outPath, string.Join("\n", lines.ToArray()) + "\n",
                                  new SText.UTF8Encoding(false));
            SSys.Console.Error.WriteLine("[terrainproof] wrote " + outPath + " lines=" + lines.Count);
            SSys.Diagnostics.Process.GetCurrentProcess().Kill();
        }

        static float ParseF(string s)
        {
            return float.Parse(s.Trim(), global::System.Globalization.CultureInfo.InvariantCulture);
        }
    }
}
