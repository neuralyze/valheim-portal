using System;
using System.Collections.Generic;
using System.Text;
using UnityEngine;

namespace NeuralyzeVRFixes
{
    // Renders a wrist-menu entry's FULL name into its sprite.
    //
    // QuickAbstract draws ONLY the sprite - `itemName` is never displayed, it is a change-detection
    // key. The previous renderer drew two initials, which failed in practice: "Chat" and "Console"
    // both read as CH/CO in a headset, so the console entry went untouched across two sessions.
    //
    // ResizeIcon() sets SpriteDrawMode.Sliced at a uniform world size, so the texture is stretched
    // to a square box regardless of pixel aspect - hence a square texture and word wrapping instead
    // of one long line.
    internal static class MiscLabels
    {
        private const int Size = 256;
        private const int GlyphW = 5, GlyphH = 7;
        private const int Pad = 14;
        private const int MaxLines = 3;
        private const int MaxChars = 8;

        private static readonly Dictionary<string, Sprite> _cache = new Dictionary<string, Sprite>();
        private static int _regenerated;

        private static readonly Dictionary<char, string> Font = new Dictionary<char, string>
        {
            { ' ', "..................................." },
            { '!', "..#....#....#....#....#.........#.." },
            { '+', ".......#....#..#####..#....#......." },
            { '-', "...............#####..............." },
            { '.', "..........................##...##.." },
            { '0', ".###.#...##..###.#.###..##...#.###." },
            { '1', "..#...##....#....#....#....#...###." },
            { '2', ".###.#...#....#...#...#...#...#####" },
            { '3', "####.....#....#.###.....#....#####." },
            { '4', "#...##...##...######....#....#....#" },
            { '5', "######....####.....#....##...#.###." },
            { '6', ".###.#....#....####.#...##...#.###." },
            { '7', "#####....#...#...#...#....#....#..." },
            { '8', ".###.#...##...#.###.#...##...#.###." },
            { '9', ".###.#...##...#.####....#....#.###." },
            { '<', "....#...#...#...#.....#.....#.....#" },
            { '>', "#.....#.....#.....#...#...#...#...." },
            { '?', ".###.#...#....#..##...#.........#.." },
            { 'A', ".###.#...##...#######...##...##...#" },
            { 'B', "####.#...#####.#...##...##...#####." },
            { 'C', ".###.#...##....#....#....#...#.###." },
            { 'D', "####.#...##...##...##...##...#####." },
            { 'E', "######....####.#....#....#....#####" },
            { 'F', "######....####.#....#....#....#...." },
            { 'G', ".###.#...##....#..###...##...#.###." },
            { 'H', "#...##...##...#######...##...##...#" },
            { 'I', "#####..#....#....#....#....#..#####" },
            { 'J', "..###...#....#....#....#.#..#..##.." },
            { 'K', "#...##..#.#.#..##...#.#..#..#.#...#" },
            { 'L', "#....#....#....#....#....#....#####" },
            { 'M', "#...###.###.#.##...##...##...##...#" },
            { 'N', "#...###..##.#.##..###...##...##...#" },
            { 'O', ".###.#...##...##...##...##...#.###." },
            { 'P', "####.#...##...#####.#....#....#...." },
            { 'Q', ".###.#...##...##...##.#.##..#..##.#" },
            { 'R', "####.#...##...#####.#.#..#..#.#...#" },
            { 'S', ".#####....#.....###.....#....#####." },
            { 'T', "#####..#....#....#....#....#....#.." },
            { 'U', "#...##...##...##...##...##...#.###." },
            { 'V', "#...##...##...##...##...#.#.#...#.." },
            { 'W', "#...##...##...##...##.#.###.###...#" },
            { 'X', "#...##...#.#.#...#...#.#.#...##...#" },
            { 'Y', "#...##...#.#.#...#....#....#....#.." },
            { 'Z', "#####....#...#...#...#...#....#####" }
        };

        // Background only. Used when real TextMesh labels are available, so the word is not drawn
        // twice - once blocky in the texture and once sharp on top of it.
        internal static Sprite Plate()
        {
            Sprite cached;
            if (_cache.TryGetValue("\u0000PLATE", out cached) && cached != null) return cached;
            Sprite made = Build(new List<string>());
            _cache["\u0000PLATE"] = made;
            return made;
        }

        internal static Sprite For(string label)
        {
            string key = (label ?? "").ToUpperInvariant().Trim();
            if (key.Length == 0) key = "?";
            Sprite cached;
            if (_cache.TryGetValue(key, out cached) && cached != null) return cached;
            if (_cache.ContainsKey(key))
            {
                _regenerated++;
                if (_regenerated == 1 || _regenerated == 50)
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "label sprite '" + key + "' was destroyed and had to be redrawn ("
                        + _regenerated + " so far) - something is unloading our textures despite"
                        + " HideAndDontSave, and redrawing them costs whole frames");
                }
            }
            try
            {
                Sprite made = Build(Wrap(key));
                _cache[key] = made;
                return made;
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "label render failed for '" + label + "': " + e.Message);
                return null;
            }
        }

        // Greedy word wrap. A word longer than one line is hard-split rather than dropped, because
        // silently truncating a label is the exact failure this class exists to fix.
        private static List<string> Wrap(string text)
        {
            var lines = new List<string>();
            var current = new StringBuilder();
            foreach (string word in text.Split(' '))
            {
                if (word.Length == 0) continue;
                string w = word;
                while (w.Length > MaxChars)
                {
                    if (current.Length > 0) { lines.Add(current.ToString()); current.Length = 0; }
                    lines.Add(w.Substring(0, MaxChars));
                    w = w.Substring(MaxChars);
                    if (lines.Count >= MaxLines) return Trim(lines);
                }
                if (current.Length == 0) current.Append(w);
                else if (current.Length + 1 + w.Length <= MaxChars) current.Append(' ').Append(w);
                else
                {
                    lines.Add(current.ToString());
                    current.Length = 0;
                    current.Append(w);
                    if (lines.Count >= MaxLines) return Trim(lines);
                }
            }
            if (current.Length > 0) lines.Add(current.ToString());
            return Trim(lines);
        }

        private static List<string> Trim(List<string> lines)
        {
            while (lines.Count > MaxLines) lines.RemoveAt(lines.Count - 1);
            if (lines.Count == 0) lines.Add("?");
            return lines;
        }

        private static Sprite Build(List<string> lines)
        {
            var tex = new Texture2D(Size, Size, TextureFormat.RGBA32, false);
            // Survive Resources.UnloadUnusedAssets().
            //
            // Nothing in the Unity scene references these textures - only our dictionary does - so
            // an unload destroys them. Unity's overloaded == then reports the cached sprite as null,
            // the cache misses, and every label is redrawn. Measured while the inventory was open,
            // which is what triggers the unload: miscRing = 168ms in a single frame, about 6fps.
            tex.hideFlags = HideFlags.HideAndDontSave;
            tex.filterMode = FilterMode.Bilinear;

            Color32 bg     = new Color32(18, 18, 22, 230);
            Color32 border = new Color32(190, 170, 120, 255);
            Color32 ink    = new Color32(248, 244, 232, 255);

            var pixels = new Color32[Size * Size];
            for (int i = 0; i < pixels.Length; i++) pixels[i] = bg;
            for (int x = 0; x < Size; x++)
                for (int b = 0; b < 2; b++)
                {
                    pixels[x + b * Size] = border;
                    pixels[x + (Size - 1 - b) * Size] = border;
                }
            for (int y = 0; y < Size; y++)
                for (int b = 0; b < 2; b++)
                {
                    pixels[b + y * Size] = border;
                    pixels[Size - 1 - b + y * Size] = border;
                }

            // Scale from the widest line AND the line count, so a short label renders large and a
            // long one still fits. Integer scale keeps the pixel font sharp.
            int longest = 1;
            foreach (string l in lines) if (l.Length > longest) longest = l.Length;
            int wPer = longest * (GlyphW + 1) - 1;
            int hPer = lines.Count * (GlyphH + 2) - 2;
            int scale = Mathf.Max(1, Mathf.Min((Size - 2 * Pad) / wPer, (Size - 2 * Pad) / hPer));

            int blockH = lines.Count * (GlyphH + 2) * scale - 2 * scale;
            int originY = (Size + blockH) / 2 - GlyphH * scale;

            for (int li = 0; li < lines.Count; li++)
            {
                string line = lines[li];
                int lineW = (line.Length * (GlyphW + 1) - 1) * scale;
                int originX = (Size - lineW) / 2;
                int baseY = originY - li * (GlyphH + 2) * scale;
                for (int c = 0; c < line.Length; c++)
                {
                    string glyph;
                    if (!Font.TryGetValue(line[c], out glyph)) Font.TryGetValue('?', out glyph);
                    if (glyph == null) continue;
                    int cx = originX + c * (GlyphW + 1) * scale;
                    for (int gy = 0; gy < GlyphH; gy++)
                        for (int gx = 0; gx < GlyphW; gx++)
                        {
                            if (glyph[gy * GlyphW + gx] != '#') continue;
                            int px = cx + gx * scale;
                            int py = baseY + (GlyphH - 1 - gy) * scale;   // texture rows run bottom-up
                            for (int sy = 0; sy < scale; sy++)
                                for (int sx = 0; sx < scale; sx++)
                                {
                                    int x = px + sx, y = py + sy;
                                    if (x < 0 || y < 0 || x >= Size || y >= Size) continue;
                                    pixels[x + y * Size] = ink;
                                }
                        }
                }
            }

            tex.SetPixels32(pixels);
            tex.Apply(false, false);
            Sprite made = Sprite.Create(tex, new Rect(0f, 0f, Size, Size), new Vector2(0.5f, 0.5f), 500f);
            made.hideFlags = HideFlags.HideAndDontSave;
            return made;
        }
    }
}
