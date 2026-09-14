// PatchScan - sample Valheim's WorldGenerator at BUILD resolution inside small
// windows around candidate base sites.
//
// Why this exists, MEASURED and not negotiable:
//   tools/seedscan/run_scan.sh produces a whole-world grid, and at any tractable
//   whole-world step that grid is too coarse to answer "is this pad flat enough
//   to drop a 1900-piece building on". Against a 1 m ground truth over
//   2048x2048 m of Pirate68, the fraction of windows a coarse grid calls flat
//   that really are flat is:
//
//       footprint / max_flat      8 m grid   4 m grid   2 m grid
//       32 m / 2.5 m                 0.18       0.44       0.62
//       60 m / 3.0 m                 0.74       0.82       0.83
//       70 m / 3.5 m                 0.83       0.90       0.90
//       80 m / 4.0 m                 0.85       0.94       0.95
//
//   i.e. at a 32 m footprint the 8 m grid is wrong more than four times out of
//   five, and no whole-world step anyone would pay for fixes it. The terrain
//   mesh Valheim actually builds is a 1 m heightmap, so 1 m is the only honest
//   resolution for the flatness test. Sampling 1 m over the whole world is
//   440 M samples; sampling 1 m over a few hundred 88 m windows is 3 M. So the
//   search stays coarse and the VERDICT is always refined here.
//
// This also fixes a second, quieter wrongness. run_scan.sh defaults
// SEEDSCAN_PREGEN=0, so its height plane is RIVER-FREE: WorldGenerator.AddRivers
// reads the river dictionary that WorldGenerator.Pregenerate() fills, and with
// pregeneration skipped that dictionary is empty. A coarse-grid site can
// therefore sit in a river bed that the grid cannot see. PatchScan always runs
// with full pregeneration, so patch heights include rivers and lakes.
//
// Same hosting constraint as SeedScan (see tools/seedscan/SeedScan.cs): the
// generator depends on UnityEngine.Mathf.PerlinNoise, a native icall that only
// resolves on the Unity main thread of a real player process, so this lives in
// a BepInEx plugin Awake() and kills the process when it is done.
//
// Contract, measured from assembly_valheim.dll (Valheim 1.0.12) IL:
//   World..ctor(string name, string seed) -> m_seed = seed.GetStableHashCode()
//   WorldGenerator.Initialize(World) / .instance / .Deitialize()
//   instance.GetBiome(float wx, float wy)  -> Heightmap.Biome
//   instance.GetHeight(float wx, float wy) -> float, relative to the water plane
//
// Env vars:
//   PATCHSCAN_REQ   request file, TSV, one patch per line, '#' comments ok:
//                       <id>\t<centre_x>\t<centre_z>\t<half_extent_m>\t<step_m>
//                   Deliberately TSV and not JSON: this runs under Mono with
//                   only the game's own assembly references, and a hand-rolled
//                   JSON parser is a liability nobody needs.
//   PATCHSCAN_SEED  seed string (required)
//   PATCHSCAN_OUT   output file (required)
//
// Output "VHPATCH1":
//   magic[8] "VHPATCH1", int32 seedHash, int32 seedNameLen, seedName utf8,
//   int32 patchCount, then per patch:
//     int32 idLen, id utf8, float32 cx, float32 cz, float32 half, float32 step,
//     int32 n, n*n float32 heights, n*n uint8 biome codes
//   Sample (i, j) of a patch is at world
//     x = cx - half + j*step + step/2,  z = cz - half + i*step + step/2
//   row-major with z outer ascending, x inner ascending -- the same convention
//   as the .biome grids.
//   Biome codes are SeedScan's BiomeCode mapping, repeated here so the two
//   tools stay independently buildable.

using SIO = global::System.IO;
using SText = global::System.Text;
using SSys = global::System;
using SCG = global::System.Collections.Generic;

namespace JumpstartPatchScan
{
    [BepInEx.BepInPlugin("vibeheim.patchscan", "PatchScan", "1.0.0")]
    public class PatchScanPlugin : BepInEx.BaseUnityPlugin
    {
        const string Magic = "VHPATCH1";

        void Awake()
        {
            string req = SSys.Environment.GetEnvironmentVariable("PATCHSCAN_REQ");
            string outPath = SSys.Environment.GetEnvironmentVariable("PATCHSCAN_OUT");
            string seed = SSys.Environment.GetEnvironmentVariable("PATCHSCAN_SEED");
            if (string.IsNullOrEmpty(req) || string.IsNullOrEmpty(outPath) || string.IsNullOrEmpty(seed))
            {
                SSys.Console.Error.WriteLine("[patchscan] PATCHSCAN_REQ/OUT/SEED not set; idle");
                return;
            }
            try { Run(req, outPath, seed); }
            catch (SSys.Exception e)
            {
                SSys.Console.Error.WriteLine("[patchscan] FATAL " + e.GetType().FullName + ": "
                    + e.Message + "\n" + e.StackTrace);
            }
            Die();
        }

        // Environment.Exit from Awake deadlocks Unity's -batchmode shutdown
        // (measured in SeedScan). Outputs are flushed by now; tear down hard and
        // let the wrapper validate by output rather than by exit status.
        static void Die()
        {
            SSys.Console.Error.Flush();
            global::System.Diagnostics.Process.GetCurrentProcess().Kill();
        }

        struct Patch
        {
            public string Id;
            public float Cx, Cz, Half, Step;
        }

        static void Run(string reqPath, string outPath, string seedName)
        {
            var patches = new SCG.List<Patch>();
            foreach (string raw in SIO.File.ReadAllLines(reqPath))
            {
                string line = raw.Trim();
                if (line.Length == 0 || line.StartsWith("#")) continue;
                string[] f = line.Split('\t');
                if (f.Length < 5)
                    throw new SSys.FormatException("bad request line (want 5 tab fields): " + line);
                var p = new Patch();
                p.Id = f[0];
                p.Cx = ParseF(f[1]);
                p.Cz = ParseF(f[2]);
                p.Half = ParseF(f[3]);
                p.Step = ParseF(f[4]);
                if (p.Step <= 0f || p.Half <= 0f)
                    throw new SSys.FormatException("non-positive half/step: " + line);
                patches.Add(p);
            }
            SSys.Console.Error.WriteLine("[patchscan] seed=" + seedName + " patches=" + patches.Count);

            // Full pregeneration: rivers and lakes must be in the heights, which
            // is the whole reason a patch is more trustworthy than the grid.
            World w = new World("patchscan", seedName);
            w.m_menu = false;
            WorldGenerator.Initialize(w);
            WorldGenerator wg = WorldGenerator.instance;

            var sw = global::System.Diagnostics.Stopwatch.StartNew();
            long samples = 0;
            using (var fs = new SIO.FileStream(outPath, SIO.FileMode.Create, SIO.FileAccess.Write))
            using (var bw = new SIO.BinaryWriter(fs))
            {
                bw.Write(SText.Encoding.ASCII.GetBytes(Magic));
                bw.Write(w.m_seed);
                byte[] nameBytes = SText.Encoding.UTF8.GetBytes(seedName);
                bw.Write(nameBytes.Length);
                bw.Write(nameBytes);
                bw.Write(patches.Count);

                for (int pi = 0; pi < patches.Count; pi++)
                {
                    Patch p = patches[pi];
                    int n = (int)SSys.Math.Round((2f * p.Half) / p.Step);
                    if (n < 2) n = 2;
                    byte[] idBytes = SText.Encoding.UTF8.GetBytes(p.Id);
                    bw.Write(idBytes.Length);
                    bw.Write(idBytes);
                    bw.Write(p.Cx);
                    bw.Write(p.Cz);
                    bw.Write(p.Half);
                    bw.Write(p.Step);
                    bw.Write(n);

                    float[] hgt = new float[n * n];
                    byte[] bio = new byte[n * n];
                    int k = 0;
                    for (int i = 0; i < n; i++)
                    {
                        float wz = p.Cz - p.Half + (i * p.Step) + (p.Step * 0.5f);
                        for (int j = 0; j < n; j++)
                        {
                            float wx = p.Cx - p.Half + (j * p.Step) + (p.Step * 0.5f);
                            hgt[k] = wg.GetHeight(wx, wz);
                            bio[k] = BiomeCode(wg.GetBiome(wx, wz));
                            k++;
                        }
                    }
                    for (int q = 0; q < hgt.Length; q++) bw.Write(hgt[q]);
                    bw.Write(bio);
                    samples += n * (long)n;
                }
            }
            WorldGenerator.Deitialize();
            SSys.Console.Error.WriteLine("[patchscan] wrote " + outPath + " samples=" + samples
                + " ms=" + sw.ElapsedMilliseconds);
        }

        static float ParseF(string s)
        {
            return float.Parse(s.Trim(), global::System.Globalization.CultureInfo.InvariantCulture);
        }

        static byte BiomeCode(Heightmap.Biome b)
        {
            switch (b)
            {
                case Heightmap.Biome.None: return 0;
                case Heightmap.Biome.Meadows: return 1;
                case Heightmap.Biome.Swamp: return 2;
                case Heightmap.Biome.Mountain: return 3;
                case Heightmap.Biome.BlackForest: return 4;
                case Heightmap.Biome.Plains: return 5;
                case Heightmap.Biome.AshLands: return 6;
                case Heightmap.Biome.DeepNorth: return 7;
                case Heightmap.Biome.Ocean: return 8;
                case Heightmap.Biome.Mistlands: return 9;
                default: return 255;
            }
        }
    }
}
