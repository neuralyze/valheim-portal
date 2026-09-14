// SeedScan - a BepInEx plugin that drives Valheim's OWN WorldGenerator inside
// the real dedicated-server process and dumps biome grids for a list of seeds.
//
// Why this shape: WorldGenerator's biome query depends on UnityEngine.Mathf.PerlinNoise
// and UnityEngine.Random, both of which are native internal calls provided by
// UnityPlayer.so.
//   * A stock `mono` process cannot resolve them at all:
//       "cant resolve internal call to UnityEngine.Mathf::PerlinNoise"
//   * A Unity Doorstop entrypoint (Doorstop.Entrypoint.Start) runs BEFORE Unity
//     registers its icalls and fails with exactly the same message.
//   * A BepInEx plugin Awake() runs on the Unity main thread after the player is
//     initialised, where the icalls do exist. That is the only place this works,
//     so that is where it lives. The generator queried is then bit-for-bit the
//     shipped one - no reimplementation, no third-party map service.
//
// Contract, all measured from assembly_valheim.dll (Valheim 1.0.12) IL:
//   World..ctor(string name, string seed)   -> m_seed = seed.GetStableHashCode(), m_worldGenVersion = 2
//   WorldGenerator.Initialize(World)        -> static, builds m_instance
//   WorldGenerator.instance                 -> static property
//   instance.GetBiome(float wx, float wy, float oceanLevel = 0.02f, bool waterAlwaysOcean = false)
//
// Pregeneration skip: WorldGenerator..ctor calls Pregenerate() (lake/river
// placement) only when world.m_menu == false. GetBiome(float,float) reads only
// m_offset0, m_offset1, m_offset2, m_offset4, maxMarshDistance, minDarklandNoise
// and GetBaseHeight; GetBaseHeight reads only m_offset0, m_offset1 and
// m_minMountainDistance. None of those are touched by Pregenerate, and all are
// assigned in the constructor before the Pregenerate call. So constructing with
// m_menu = true and clearing it immediately afterwards yields identical biome
// answers at a fraction of the cost. SEEDSCAN_PREGEN=1 disables the shortcut so
// the equivalence can be verified empirically instead of trusted.
//
// Env vars:
//   SEEDSCAN_SEEDS    path to a file, one seed string per line (required)
//   SEEDSCAN_OUT      output directory (required)
//   SEEDSCAN_STEP     grid step in world units (default 128)
//   SEEDSCAN_EXTENT   half-extent sampled, world units (default 10496)
//   SEEDSCAN_PREGEN   "1" to run full Pregenerate per seed (default 0)
//   SEEDSCAN_HEIGHT   "1" to also emit a float32 terrain-height plane from
//                     WorldGenerator.GetHeight (implies PREGEN=1, ~80x slower
//                     per seed). Needed to tell land from water, which the biome
//                     plane alone cannot do: GetBiome tests IsAshlands BEFORE
//                     its water test, so the whole southern sea reports
//                     AshLands. Deep North is the other way round - its water
//                     test runs first - so DeepNorth cells are always land.
// With SEEDSCAN_SEEDS/SEEDSCAN_OUT unset the plugin does nothing and the server
// boots normally.
//
// Output: <OUT>/<index>.biome per seed, plus <OUT>/index.tsv and <OUT>/seedscan.log
// .biome layout: "VHBIOME4" (8 bytes ASCII), int32 step, int32 extent, int32 n,
// int32 planes (1 or 2), int32 seedNameByteLen, seedName UTF8 bytes,
// int32 seedHash, then n*n bytes of biome code (see BiomeCode), then - when
// planes == 2 - n*n little-endian float32 terrain heights. Both planes are
// row-major with y outer ascending and x inner ascending.

using SIO = global::System.IO;
using SText = global::System.Text;
using SSys = global::System;

namespace SeedScan
{
    [BepInEx.BepInPlugin("vibeheim.seedscan", "SeedScan", "1.0.0")]
    public class SeedScanPlugin : BepInEx.BaseUnityPlugin
    {
        const string Magic = "VHBIOME4";

        void Awake()
        {
            string outDir = SSys.Environment.GetEnvironmentVariable("SEEDSCAN_OUT");
            string seedFile = SSys.Environment.GetEnvironmentVariable("SEEDSCAN_SEEDS");
            if (string.IsNullOrEmpty(outDir) || string.IsNullOrEmpty(seedFile))
            {
                SSys.Console.Error.WriteLine("[seedscan] SEEDSCAN_OUT / SEEDSCAN_SEEDS not set; idle");
                return;
            }
            string log = null;
            try
            {
                SIO.Directory.CreateDirectory(outDir);
                log = SIO.Path.Combine(outDir, "seedscan.log");
                Run(outDir, seedFile, log);
                Die();
            }
            catch (SSys.Exception e)
            {
                string msg = "[seedscan] FATAL " + e.GetType().FullName + ": " + e.Message + "\n" + e.StackTrace + "\n";
                SSys.Console.Error.WriteLine(msg);
                try { if (log != null) SIO.File.AppendAllText(log, msg); } catch { }
                Die();
            }
        }

        // Environment.Exit / Application.Quit from Awake deadlocks Unity's
        // shutdown path in -batchmode (measured: process hung indefinitely
        // after the scan completed). Outputs are already flushed to disk at
        // this point, so tear the process down hard. The wrapper validates
        // grid count rather than exit status.
        static void Die()
        {
            SSys.Console.Error.Flush();
            global::System.Diagnostics.Process.GetCurrentProcess().Kill();
        }

        static void Run(string outDir, string seedFile, string logPath)
        {
            int step = EnvInt("SEEDSCAN_STEP", 128);
            int extent = EnvInt("SEEDSCAN_EXTENT", 10496);
            // GetHeight() is river-aware, but rivers live in the pregenerated
            // data. Without Pregenerate the river dictionary is simply empty and
            // AddRivers returns the height unchanged - so heights come out
            // river-free. Rivers are 60-100 m wide and only inland, so they do
            // not affect sea/land classification at 32 m sampling or coarser.
            // Leaving pregeneration off makes each seed ~80x cheaper. Set
            // SEEDSCAN_PREGEN=1 to include rivers and lakes.
            bool heights = SSys.Environment.GetEnvironmentVariable("SEEDSCAN_HEIGHT") == "1";
            bool pregen = SSys.Environment.GetEnvironmentVariable("SEEDSCAN_PREGEN") == "1";

            var sb = new SText.StringBuilder();
            sb.Append("[seedscan] step=").Append(step).Append(" extent=").Append(extent)
              .Append(" pregen=").Append(pregen).Append(" heights=").Append(heights).Append('\n');
            // Prove the Unity icalls this depends on are live in this process.
            float probe = UnityEngine.Mathf.PerlinNoise(0.3f, 0.7f);
            sb.Append("[seedscan] Mathf.PerlinNoise(0.3,0.7)=").Append(probe.ToString("R")).Append('\n');
            UnityEngine.Random.InitState(12345);
            sb.Append("[seedscan] Random.Range after InitState(12345)=").Append(UnityEngine.Random.Range(-10000, 10000)).Append('\n');

            var seeds = new global::System.Collections.Generic.List<string>();
            foreach (string raw in SIO.File.ReadAllLines(seedFile))
            {
                string s = raw.Trim();
                if (s.Length > 0 && !s.StartsWith("#")) seeds.Add(s);
            }
            sb.Append("[seedscan] seeds=").Append(seeds.Count).Append('\n');
            SIO.File.AppendAllText(logPath, sb.ToString());
            sb.Length = 0;

            int n = (2 * extent) / step;
            var index = new SText.StringBuilder();
            index.Append("idx\tseed\thash\tfile\n");

            var swAll = global::System.Diagnostics.Stopwatch.StartNew();
            byte[] grid = new byte[n * n];
            float[] hgt = heights ? new float[n * n] : null;

            for (int si = 0; si < seeds.Count; si++)
            {
                string seedName = seeds[si];
                var sw = global::System.Diagnostics.Stopwatch.StartNew();

                World w = new World("seedscan", seedName);
                w.m_menu = !pregen;                 // true => constructor skips Pregenerate()
                WorldGenerator.Initialize(w);
                w.m_menu = false;                   // real (non-menu) biome classification path
                WorldGenerator wg = WorldGenerator.instance;

                int k = 0;
                for (int iy = 0; iy < n; iy++)
                {
                    float wy = -extent + (iy * step) + (step * 0.5f);
                    for (int ix = 0; ix < n; ix++)
                    {
                        float wx = -extent + (ix * step) + (step * 0.5f);
                        grid[k] = BiomeCode(wg.GetBiome(wx, wy));
                        if (heights) hgt[k] = wg.GetHeight(wx, wy);
                        k++;
                    }
                }

                string file = si.ToString("D5") + ".biome";
                using (var fs = new SIO.FileStream(SIO.Path.Combine(outDir, file), SIO.FileMode.Create, SIO.FileAccess.Write))
                using (var bw = new SIO.BinaryWriter(fs))
                {
                    bw.Write(SText.Encoding.ASCII.GetBytes(Magic));
                    bw.Write(step);
                    bw.Write(extent);
                    bw.Write(n);
                    bw.Write(heights ? 2 : 1);      // plane count
                    byte[] nameBytes = SText.Encoding.UTF8.GetBytes(seedName);
                    bw.Write(nameBytes.Length);
                    bw.Write(nameBytes);
                    bw.Write(w.m_seed);
                    bw.Write(grid);
                    if (heights) { for (int q = 0; q < hgt.Length; q++) bw.Write(hgt[q]); }
                }
                index.Append(si).Append('\t').Append(seedName).Append('\t').Append(w.m_seed).Append('\t').Append(file).Append('\n');
                sb.Append("[seedscan] ").Append(si).Append(' ').Append(seedName)
                  .Append(" hash=").Append(w.m_seed)
                  .Append(" ms=").Append(sw.ElapsedMilliseconds).Append('\n');

                WorldGenerator.Deitialize();

                if ((si % 25) == 24)
                {
                    SIO.File.AppendAllText(logPath, sb.ToString()); sb.Length = 0;
                    SSys.Console.Error.WriteLine("[seedscan] progress " + (si + 1) + "/" + seeds.Count);
                }
            }

            sb.Append("[seedscan] done in ").Append(swAll.ElapsedMilliseconds).Append("ms\n");
            SIO.File.AppendAllText(logPath, sb.ToString());
            SIO.File.WriteAllText(SIO.Path.Combine(outDir, "index.tsv"), index.ToString());
            SSys.Console.Error.WriteLine("[seedscan] complete: " + seeds.Count + " seeds in " + swAll.ElapsedMilliseconds + "ms");
        }

        static int EnvInt(string key, int dflt)
        {
            string v = SSys.Environment.GetEnvironmentVariable(key);
            int r;
            return (!string.IsNullOrEmpty(v) && int.TryParse(v, out r)) ? r : dflt;
        }

        // Compact code so grids are 1 byte/cell. Source values are the shipped
        // Heightmap.Biome flag values (measured from IL).
        static byte BiomeCode(Heightmap.Biome b)
        {
            switch (b)
            {
                case Heightmap.Biome.None: return 0;
                case Heightmap.Biome.Meadows: return 1;        // 0x001
                case Heightmap.Biome.Swamp: return 2;          // 0x002
                case Heightmap.Biome.Mountain: return 3;       // 0x004
                case Heightmap.Biome.BlackForest: return 4;    // 0x008
                case Heightmap.Biome.Plains: return 5;         // 0x010
                case Heightmap.Biome.AshLands: return 6;       // 0x020
                case Heightmap.Biome.DeepNorth: return 7;      // 0x040
                case Heightmap.Biome.Ocean: return 8;          // 0x100
                case Heightmap.Biome.Mistlands: return 9;      // 0x200
                default: return 255;
            }
        }
    }
}
