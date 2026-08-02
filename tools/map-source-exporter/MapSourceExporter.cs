using System;
using System.IO;
using System.IO.Compression;
using BepInEx;
using HarmonyLib;
using UnityEngine;

namespace Neuralyze.ValheimMapSourceExporter
{
    [BepInPlugin("com.neuralyze.valheim.mapsourceexporter", "Neuralyze Map Source Exporter", "1.0.0")]
    public sealed class MapSourceExporter : BaseUnityPlugin
    {
        private Harmony harmony;

        private void Awake()
        {
            harmony = new Harmony("com.neuralyze.valheim.mapsourceexporter");
            harmony.PatchAll();
        }

        private void OnDestroy()
        {
            if (harmony != null)
            {
                harmony.UnpatchSelf();
            }
        }

        [HarmonyPatch(typeof(ZoneSystem), "Start")]
        private static class ZoneSystemStartPatch
        {
            private static bool exported;

            private static void Postfix()
            {
                if (exported)
                {
                    return;
                }
                exported = true;
                try
                {
                    Export();
                    Application.Quit(0);
                }
                catch (Exception error)
                {
                    ZLog.LogError("Map source export failed: " + error);
                    Application.Quit(1);
                }
            }
        }

        private static void Export()
        {
            string output = Environment.GetEnvironmentVariable("VALHEIM_MAP_EXPORT_DIR");
            if (String.IsNullOrEmpty(output) || !Path.IsPathRooted(output))
            {
                throw new InvalidOperationException("VALHEIM_MAP_EXPORT_DIR must be an absolute path");
            }
            Directory.CreateDirectory(output);

            const int size = 12288;
            const int pixelSize = 2;
            int half = size / 2;
            float halfPixel = pixelSize / 2f;
            int count = size * size;
            Color32[] biomePixels = new Color32[count];
            Color32[] heightPixels = new Color32[count];
            WorldGenerator generator = WorldGenerator.instance;
            if (generator == null)
            {
                throw new InvalidOperationException("WorldGenerator is unavailable");
            }

            for (int y = 0; y < size; ++y)
            {
                float worldZ = (y - half) * pixelSize + halfPixel;
                for (int x = 0; x < size; ++x)
                {
                    float worldX = (x - half) * pixelSize + halfPixel;
                    int index = y * size + x;
                    Heightmap.Biome biome = generator.GetBiome(worldX, worldZ, 0.02f, false);
                    biomePixels[index] = BiomeColor(biome);
                    Color mask;
                    float height = generator.GetBiomeHeight(biome, worldX, worldZ, out mask, false);
                    int encodedHeight = Mathf.Clamp(Mathf.RoundToInt((height + 512f) * 8192f), 0, 0xffffff);
                    heightPixels[index] = new Color32((byte)(encodedHeight >> 16), (byte)(encodedHeight >> 8), (byte)encodedHeight, 255);
                }
            }

            WriteTexture(Path.Combine(output, "biome.png"), biomePixels, size);
            WriteTexture(Path.Combine(output, "height.png"), heightPixels, size);
            File.WriteAllText(Path.Combine(output, "complete"), "ok\n");
            ZLog.Log("Map source export complete: " + output);
        }

        private static void WriteTexture(string path, Color32[] pixels, int size)
        {
            using (FileStream file = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.None))
            using (BufferedStream output = new BufferedStream(file, 1 << 20))
            {
                output.Write(PngSignature, 0, PngSignature.Length);
                byte[] header = new byte[13];
                PutUInt32(header, 0, (uint)size);
                PutUInt32(header, 4, (uint)size);
                header[8] = 8;
                header[9] = 6;
                WriteChunk(output, Ihdr, header, 0, header.Length);

                using (IdatChunkStream idat = new IdatChunkStream(output))
                {
                    idat.Write(new byte[] { 0x78, 0x9c }, 0, 2);
                    ulong adlerA = 1;
                    ulong adlerB = 0;
                    byte[] row = new byte[1 + size * 4];
                    using (DeflateStream deflate = new DeflateStream(idat, System.IO.Compression.CompressionLevel.Optimal, true))
                    {
                        for (int y = 0; y < size; ++y)
                        {
                            row[0] = 1;
                            byte previousR = 0;
                            byte previousG = 0;
                            byte previousB = 0;
                            byte previousA = 0;
                            int source = y * size;
                            int target = 1;
                            for (int x = 0; x < size; ++x)
                            {
                                Color32 pixel = pixels[source + x];
                                row[target++] = (byte)(pixel.r - previousR);
                                row[target++] = (byte)(pixel.g - previousG);
                                row[target++] = (byte)(pixel.b - previousB);
                                row[target++] = (byte)(pixel.a - previousA);
                                previousR = pixel.r;
                                previousG = pixel.g;
                                previousB = pixel.b;
                                previousA = pixel.a;
                            }
                            for (int index = 0; index < row.Length; ++index)
                            {
                                adlerA += row[index];
                                adlerB += adlerA;
                            }
                            adlerA %= 65521;
                            adlerB %= 65521;
                            deflate.Write(row, 0, row.Length);
                        }
                    }
                    uint adler = (uint)((adlerB << 16) | adlerA);
                    byte[] checksum = new byte[4];
                    PutUInt32(checksum, 0, adler);
                    idat.Write(checksum, 0, checksum.Length);
                    idat.Complete();
                }
                WriteChunk(output, Iend, new byte[0], 0, 0);
            }
        }

        private static readonly byte[] PngSignature = { 137, 80, 78, 71, 13, 10, 26, 10 };
        private static readonly byte[] Ihdr = { 73, 72, 68, 82 };
        private static readonly byte[] Idat = { 73, 68, 65, 84 };
        private static readonly byte[] Iend = { 73, 69, 78, 68 };
        private static readonly uint[] CrcTable = BuildCrcTable();

        private static void PutUInt32(byte[] buffer, int offset, uint value)
        {
            buffer[offset] = (byte)(value >> 24);
            buffer[offset + 1] = (byte)(value >> 16);
            buffer[offset + 2] = (byte)(value >> 8);
            buffer[offset + 3] = (byte)value;
        }

        private static void WriteUInt32(Stream output, uint value)
        {
            byte[] buffer = new byte[4];
            PutUInt32(buffer, 0, value);
            output.Write(buffer, 0, buffer.Length);
        }

        private static void WriteChunk(Stream output, byte[] type, byte[] data, int offset, int count)
        {
            WriteUInt32(output, (uint)count);
            output.Write(type, 0, type.Length);
            output.Write(data, offset, count);
            uint crc = 0xffffffff;
            crc = UpdateCrc(crc, type, 0, type.Length);
            crc = UpdateCrc(crc, data, offset, count);
            WriteUInt32(output, crc ^ 0xffffffff);
        }

        private static uint UpdateCrc(uint crc, byte[] data, int offset, int count)
        {
            for (int index = 0; index < count; ++index)
            {
                crc = CrcTable[(crc ^ data[offset + index]) & 0xff] ^ (crc >> 8);
            }
            return crc;
        }

        private static uint[] BuildCrcTable()
        {
            uint[] table = new uint[256];
            for (uint index = 0; index < table.Length; ++index)
            {
                uint value = index;
                for (int bit = 0; bit < 8; ++bit)
                {
                    value = (value & 1) != 0 ? 0xedb88320 ^ (value >> 1) : value >> 1;
                }
                table[index] = value;
            }
            return table;
        }

        private sealed class IdatChunkStream : Stream
        {
            private readonly Stream output;
            private readonly byte[] buffer = new byte[1 << 20];
            private int buffered;
            private bool completed;

            internal IdatChunkStream(Stream output)
            {
                this.output = output;
            }

            public override bool CanRead { get { return false; } }
            public override bool CanSeek { get { return false; } }
            public override bool CanWrite { get { return true; } }
            public override long Length { get { throw new NotSupportedException(); } }
            public override long Position
            {
                get { throw new NotSupportedException(); }
                set { throw new NotSupportedException(); }
            }

            public override void Write(byte[] source, int offset, int count)
            {
                while (count > 0)
                {
                    int copy = Math.Min(count, buffer.Length - buffered);
                    Buffer.BlockCopy(source, offset, buffer, buffered, copy);
                    buffered += copy;
                    offset += copy;
                    count -= copy;
                    if (buffered == buffer.Length)
                    {
                        FlushChunk();
                    }
                }
            }

            public override void Flush()
            {
                FlushChunk();
            }

            internal void Complete()
            {
                if (!completed)
                {
                    FlushChunk();
                    completed = true;
                }
            }

            protected override void Dispose(bool disposing)
            {
                if (disposing)
                {
                    Complete();
                }
                base.Dispose(disposing);
            }

            private void FlushChunk()
            {
                if (buffered != 0)
                {
                    WriteChunk(output, Idat, buffer, 0, buffered);
                    buffered = 0;
                }
            }

            public override int Read(byte[] buffer, int offset, int count) { throw new NotSupportedException(); }
            public override long Seek(long offset, SeekOrigin origin) { throw new NotSupportedException(); }
            public override void SetLength(long value) { throw new NotSupportedException(); }
        }

        private static Color32 BiomeColor(Heightmap.Biome biome)
        {
            switch (biome)
            {
                case Heightmap.Biome.Meadows:
                    return new Color32(0x91, 0xa7, 0x5b, 255);
                case Heightmap.Biome.Swamp:
                    return new Color32(0x52, 0x52, 0x52, 255);
                case Heightmap.Biome.Mountain:
                case Heightmap.Biome.DeepNorth:
                    return new Color32(255, 255, 255, 255);
                case Heightmap.Biome.BlackForest:
                    return new Color32(0x34, 0x5e, 0x3b, 255);
                case Heightmap.Biome.Plains:
                    return new Color32(0xc7, 0xc7, 0x31, 255);
                case Heightmap.Biome.AshLands:
                    return new Color32(255, 0, 0, 255);
                case Heightmap.Biome.Mistlands:
                    return new Color32(0xa3, 0x71, 0x57, 255);
                default:
                    return new Color32(0, 0, 0x99, 255);
            }
        }
    }
}
