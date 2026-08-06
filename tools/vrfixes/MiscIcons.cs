using System;
using System.Collections.Generic;
using UnityEngine;

namespace NeuralyzeVRFixes
{
    // Draws readable labels for wrist-menu entries.
    //
    // Necessary because QuickAbstract renders ONLY the sprite: `itemName` is never displayed,
    // it is used solely as a change-detection key (`if (extraElements[i].itemName !=
    // "QuickActionSIT")`). Passing a null sprite therefore produces a correctly-wired but
    // completely blank box, which is exactly what happened.
    //
    // VHVR's own entries use textures from its asset bundle (`VRAssetManager.GetAsset<Texture2D>`
    // with "sit", "map", "recenter", "black_screen"). Those four names are the only ones
    // referenced in its source, so there is no general icon set to draw from for arbitrary mod
    // actions. Generating a two-character glyph is self-contained, needs no assets, and scales
    // to any action a mod adds later - which is the whole point of a data-driven menu.
    internal static class MiscIcons
    {
        // 5x7 bitmap font. Deliberately tiny and complete enough for short labels; anything
        // unmapped falls back to a filled box so a missing glyph is visible rather than blank.
        private static readonly Dictionary<char, string> Font = new Dictionary<char, string>
        {
            { '!', "..#....#....#....#....#.........#.." },
            { '+', ".......#....#..#####..#....#......." },
            { '-', "...............#####..............." },
            { '0', ".###.#...##..###.#.###..##...#.###." },
            { '1', "..#...##....#....#....#....#..#####" },
            { '2', ".###.#...#....#...#...#...#...#####" },
            { '3', "####.....#....#.###.....#....#####." },
            { '4', "...#...##..#.#.#..#.#####...#....#." },
            { '5', "######....####.....#....##...#.###." },
            { '6', ".###.#....#....####.#...##...#.###." },
            { '7', "#####....#...#...#...#...#....#...." },
            { '8', ".###.#...##...#.###.#...##...#.###." },
            { '9', ".###.#...##...#.####....#....#.###." },
            { '<', "....#...#...#...#.....#.....#.....#" },
            { '>', "#.....#.....#.....#...#...#...#...." },
            { '?', ".###.#...#....#...#...#.........#.." },
            { 'A', ".###.#...##...#######...##...##...#" },
            { 'B', "####.#...#####.#...##...##...#####." },
            { 'C', ".###.#...##....#....#....#...#.###." },
            { 'D', "####.#...##...##...##...##...#####." },
            { 'E', "######....#....####.#....#....#####" },
            { 'F', "######....#....####.#....#....#...." },
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
            { 'Z', "#####....#...#...#...#...#....#####" },
        };

        private static readonly Dictionary<string, Sprite> _cache = new Dictionary<string, Sprite>();

        // Two characters read clearly at wrist-icon size; more becomes mush.
        internal static string ShortLabel(string label)
        {
            if (string.IsNullOrEmpty(label)) return "??";
            // Prefer initials of the first two words - "Map Zoom In" -> "MZ" reads better than "MA".
            string[] words = label.Split(new[] { ' ', '_', '-', '(', ')' }, StringSplitOptions.RemoveEmptyEntries);
            if (words.Length >= 2)
                return ("" + char.ToUpperInvariant(words[0][0]) + char.ToUpperInvariant(words[1][0]));
            string one = words.Length == 1 ? words[0] : label;
            return one.Length >= 2
                ? ("" + char.ToUpperInvariant(one[0]) + char.ToUpperInvariant(one[1]))
                : ("" + char.ToUpperInvariant(one[0]));
        }

        internal static Sprite For(string label)
        {
            string key = ShortLabel(label);
            Sprite cached;
            if (_cache.TryGetValue(key, out cached) && cached != null) return cached;
            try
            {
                Sprite made = Build(key);
                _cache[key] = made;
                return made;
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "icon build failed for '" + label + "': " + e.Message);
                return null;
            }
        }

        private const int Size = 64;
        private const int Scale = 4;      // 5x7 glyph -> 20x28 drawn
        private const int GlyphW = 5, GlyphH = 7;

        private static Sprite Build(string text)
        {
            var tex = new Texture2D(Size, Size, TextureFormat.RGBA32, false);
            tex.filterMode = FilterMode.Bilinear;

            Color32 bg = new Color32(18, 18, 22, 210);       // dark, mostly opaque so it reads on any background
            Color32 border = new Color32(190, 170, 120, 255); // Valheim-ish parchment gold
            Color32 ink = new Color32(245, 240, 225, 255);

            var pixels = new Color32[Size * Size];
            for (int i = 0; i < pixels.Length; i++) pixels[i] = bg;
            // 2px border, so an unhovered box is still visibly a box
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

            int textW = text.Length * GlyphW * Scale + (text.Length - 1) * Scale;
            int originX = (Size - textW) / 2;
            int originY = (Size - GlyphH * Scale) / 2;

            for (int c = 0; c < text.Length; c++)
            {
                string glyph;
                bool known = Font.TryGetValue(text[c], out glyph) && glyph.Length >= GlyphW * GlyphH;
                int cx = originX + c * (GlyphW + 1) * Scale;
                for (int gy = 0; gy < GlyphH; gy++)
                    for (int gx = 0; gx < GlyphW; gx++)
                    {
                        bool on = known ? glyph[gy * GlyphW + gx] == '#' : true;
                        if (!on) continue;
                        // Texture rows run bottom-up, so invert gy to draw upright.
                        int px = cx + gx * Scale;
                        int py = originY + (GlyphH - 1 - gy) * Scale;
                        for (int sy = 0; sy < Scale; sy++)
                            for (int sx = 0; sx < Scale; sx++)
                            {
                                int x = px + sx, y = py + sy;
                                if (x >= 0 && x < Size && y >= 0 && y < Size) pixels[x + y * Size] = ink;
                            }
                    }
            }

            tex.SetPixels32(pixels);
            tex.Apply(false, false);
            return Sprite.Create(tex, new Rect(0f, 0f, Size, Size), new Vector2(0.5f, 0.5f), 500f);
        }
    }
}
