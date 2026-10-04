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
// TWO SURFACES, AND THEY ARE NOT THE SAME FUNCTION.  This is the defect that
// put the wt-peak watchtower 9 m in the air, MEASURED from the deployed
// assembly's IL (assembly_valheim.dll, container
// /opt/valheim/bepinex/valheim_server_Data/Managed, md5
// 47df8869abdf95abedf859fce3deb022):
//
//   WorldGenerator::GetHeight(wx, wy)
//       = GetBiomeHeight(GetBiome(wx, wy), wx, wy)      -- THE POINT'S OWN BIOME
//
//   HeightmapBuilder::Build(data)   -- what the game actually RENDERS
//       corner  = centre - (W*s/2, 0, W*s/2)
//       b0 = GetBiome(corner.x,       corner.z)
//       b1 = GetBiome(corner.x + W*s, corner.z)
//       b2 = GetBiome(corner.x,       corner.z + W*s)
//       b3 = GetBiome(corner.x + W*s, corner.z + W*s)   -- THE ZONE'S FOUR CORNERS
//       per vertex (ix, iz), wx = corner.x + ix*s, wz = corner.z + iz*s:
//         if (b1 == b0 && b2 == b0 && b3 == b0)
//             h = GetBiomeHeight(b0, wx, wz, out mask, false, true)
//         else
//             tx = DUtils.SmoothStep(0, 1, ix / (float)W)
//             tz = DUtils.SmoothStep(0, 1, iz / (float)W)
//             h  = DUtils.Lerp(DUtils.Lerp(h_b0, h_b1, tx),
//                              DUtils.Lerp(h_b2, h_b3, tx), tz)
//       (IL_0114..IL_01bb for the corners, IL_02ee..IL_039c for the blend; the
//        two trailing flags on every GetBiomeHeight call are ldc.i4.0 then
//        ldc.i4.1, i.e. preGeneration=false, riverPreDN=true.)
//
// THE EXACT IMMUNITY PREDICATE, and the obvious version of it is WRONG.  "The
// four corners agree" is NOT immunity: the fast path then evaluates corner
// b0's height function over the WHOLE zone, so a zone whose corners agree but
// whose INTERIOR holds another biome still diverges.  The sufficient and
// necessary test is:
//
//     all four corner biomes equal  AND  every interior sample's OWN biome
//     is that same biome
//
// MEASURED over all 435 written zones of Ulfsland, both surfaces scanned:
// 335 zones immune, 100 not, and the predicate has ZERO exceptions in either
// direction -- no immune zone disagrees by more than 1 cm, and no non-immune
// zone agrees to within 1 cm.  92 of the 100 disagree by more than 1 m; the
// worst is zone (-22,107) at 46.034 m, a Plains/Mistlands boundary under the
// northern trunk road.
//
// The settlement case that found this: Ulfsland zone (22,24), corners
// Meadows/Meadows/Meadows/Mountain with 2,736 of 4,225 interior samples
// Mountain.  The blend is almost pure Meadows away from the single Mountain
// corner, so `wt-peak`'s pad -- levelled to a `GetHeight` datum of 102.310 --
// sits over ground the engine draws at 93.885..99.427, and its 212-piece
// watchtower stands in 3.17..13.54 m of air.  Validated against 1,186
// worldgen-placed trees and rocks on unwritten ground, whose ZDO y IS their
// ground contact point: residual to this surface median -0.001 m with 8 of
// 1,186 over 1 m, against GetHeight's 43 over 1 m and a -10.516 m tail.
//
// So `PATCHSCAN_SURFACE` selects which question is asked, and a flatten datum
// MUST ask `heightmap`.
//
// Env vars:
//   PATCHSCAN_REQ   request file, TSV, one patch per line, '#' comments ok:
//                       <id>\t<centre_x>\t<centre_z>\t<half_extent_m>\t<step_m>
//                   Deliberately TSV and not JSON: this runs under Mono with
//                   only the game's own assembly references, and a hand-rolled
//                   JSON parser is a liability nobody needs.
//   PATCHSCAN_SEED  seed string (required)
//   PATCHSCAN_OUT   output file (required)
//   PATCHSCAN_SURFACE
//                   `worldgen` (default) -> WorldGenerator.GetHeight, the
//                   point's own biome.  Site FINDING and biome questions.
//                   `heightmap` -> HeightmapBuilder's per-zone corner-biome
//                   blend, the surface a player stands on.  Any FLATTEN DATUM
//                   must use this.  Each sample is resolved into its own zone
//                   by ZoneSystem's rule, floor((c + 32) / 64), so a request
//                   need not be zone-aligned; a sample exactly on a zone seam
//                   is answered by the zone that rule picks, which is the same
//                   choice `terraform/tcdata.zone_of` and `roads/applied.py`
//                   already make, and the seam disagreement between two
//                   compilers is a known and separately documented hole.
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
            string surface = SSys.Environment.GetEnvironmentVariable("PATCHSCAN_SURFACE");
            if (string.IsNullOrEmpty(surface)) surface = "worldgen";
            surface = surface.Trim().ToLowerInvariant();
            if (surface != "worldgen" && surface != "heightmap")
            {
                SSys.Console.Error.WriteLine("[patchscan] PATCHSCAN_SURFACE must be "
                    + "`worldgen` or `heightmap`, got " + surface + "; refusing rather than "
                    + "guessing, because the two differ by up to 10 m and the wrong one is "
                    + "silently plausible");
                Die();
                return;
            }
            try { Run(req, outPath, seed, surface); }
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

        // ZoneSystem's own zone rule and zone origin.  Restated here rather than
        // read off ZoneSystem.instance because no ZoneSystem exists in this
        // process -- there is no game scene, only a WorldGenerator.
        const float ZoneSize = 64f;
        const int HmapWidth = 64;       // Heightmap.m_width on a zone
        const float HmapScale = 1f;     // Heightmap.m_scale on a zone

        static int ZoneIndex(float c)
        {
            return (int)SSys.Math.Floor((c + (ZoneSize * 0.5f)) / ZoneSize);
        }

        // One zone's four corner biomes, cached: a 65x65 patch asks for the same
        // zone 4,225 times and GetBiome is not free.
        sealed class ZoneCorners
        {
            public float CornerX, CornerZ;
            public Heightmap.Biome B0, B1, B2, B3;
            public bool Uniform;
        }

        static ZoneCorners CornersFor(WorldGenerator wg, int zx, int zz)
        {
            float cx = zx * ZoneSize, cz = zz * ZoneSize;
            float span = HmapWidth * HmapScale;
            var zc = new ZoneCorners();
            zc.CornerX = cx - (span * 0.5f);
            zc.CornerZ = cz - (span * 0.5f);
            zc.B0 = wg.GetBiome(zc.CornerX, zc.CornerZ);
            zc.B1 = wg.GetBiome(zc.CornerX + span, zc.CornerZ);
            zc.B2 = wg.GetBiome(zc.CornerX, zc.CornerZ + span);
            zc.B3 = wg.GetBiome(zc.CornerX + span, zc.CornerZ + span);
            zc.Uniform = (zc.B1 == zc.B0 && zc.B2 == zc.B0 && zc.B3 == zc.B0);
            return zc;
        }

        // HeightmapBuilder::Build's height for one world point, evaluated in the
        // zone that owns it.  The weights are the vertex INDEX fractions, not the
        // raw offsets: Build computes SmoothStep(0, 1, ix / (float)m_width) where
        // ix is the integer vertex index, and an off-lattice point's fraction is
        // its own offset divided by the zone span, which is the same quantity
        // continued between vertices.
        static float HeightmapHeight(WorldGenerator wg, ZoneCorners zc, float wx, float wz)
        {
            global::UnityEngine.Color mask;
            if (zc.Uniform)
                return wg.GetBiomeHeight(zc.B0, wx, wz, out mask, false, true);
            float span = HmapWidth * HmapScale;
            float tx = DUtils.SmoothStep(0f, 1f, (wx - zc.CornerX) / span);
            float tz = DUtils.SmoothStep(0f, 1f, (wz - zc.CornerZ) / span);
            float h0 = wg.GetBiomeHeight(zc.B0, wx, wz, out mask, false, true);
            float h1 = wg.GetBiomeHeight(zc.B1, wx, wz, out mask, false, true);
            float h2 = wg.GetBiomeHeight(zc.B2, wx, wz, out mask, false, true);
            float h3 = wg.GetBiomeHeight(zc.B3, wx, wz, out mask, false, true);
            return DUtils.Lerp(DUtils.Lerp(h0, h1, tx), DUtils.Lerp(h2, h3, tx), tz);
        }

        static void Run(string reqPath, string outPath, string seedName, string surface)
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
            SSys.Console.Error.WriteLine("[patchscan] seed=" + seedName + " patches="
                + patches.Count + " surface=" + surface);

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
                    bool hmap = (surface == "heightmap");
                    int zoneX = int.MinValue, zoneZ = int.MinValue;
                    ZoneCorners zc = null;
                    int k = 0;
                    for (int i = 0; i < n; i++)
                    {
                        float wz = p.Cz - p.Half + (i * p.Step) + (p.Step * 0.5f);
                        for (int j = 0; j < n; j++)
                        {
                            float wx = p.Cx - p.Half + (j * p.Step) + (p.Step * 0.5f);
                            if (hmap)
                            {
                                int zx = ZoneIndex(wx), zz = ZoneIndex(wz);
                                if (zc == null || zx != zoneX || zz != zoneZ)
                                {
                                    zc = CornersFor(wg, zx, zz);
                                    zoneX = zx; zoneZ = zz;
                                }
                                hgt[k] = HeightmapHeight(wg, zc, wx, wz);
                            }
                            else
                            {
                                hgt[k] = wg.GetHeight(wx, wz);
                            }
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
