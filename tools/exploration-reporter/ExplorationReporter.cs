using System;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using BepInEx;
using HarmonyLib;
using UnityEngine;

namespace Neuralyze.ValheimExplorationReporter
{
    // Reports what this player has actually uncovered on their own map, so the portal can draw real fog
    // of war instead of an approximation.
    //
    // Why this has to run on the client. A player's revealed map is a grid of booleans held by the
    // minimap in their own process and saved into their character file; a dedicated server never sees
    // it. The portal's current fog is inferred from generated zones - ground the server has loaded
    // because somebody went near it - which is coarse (64 m) and shared by everyone. This is the real
    // thing: Minimap.m_explored, indexed [y * m_textureSize + x], with a cell every m_pixelSize metres.
    //
    // What it writes: the grid as a packed bitset, gzipped, with the two constants needed to place it in
    // the world, plus the player id that pieces are stamped with so the portal can tell whose map this
    // is. Mostly-empty grids compress to a few kilobytes.
    //
    // It reports; it changes nothing. No patch alters game behaviour - the save hook only reads.
    [BepInPlugin("com.neuralyze.valheim.explorationreporter", "Neuralyze Exploration Reporter", "1.0.0")]
    public sealed class ExplorationReporter : BaseUnityPlugin
    {
        private const int Schema = 1;
        private static BepInEx.Logging.ManualLogSource log;
        private static string outputDirectory;
        private static float lastWriteTime = -9999f;
        private Harmony harmony;

        private void Awake()
        {
            log = Logger;
            // The launcher passes the directory it will upload from. Without it, the plugin still works
            // and writes beside its own config, so a hand-installed profile is not silently useless.
            outputDirectory = Environment.GetEnvironmentVariable("VALHEIM_EXPLORATION_DIR");
            if (string.IsNullOrEmpty(outputDirectory))
            {
                outputDirectory = Path.Combine(Paths.ConfigPath, "exploration");
            }
            harmony = new Harmony("com.neuralyze.valheim.explorationreporter");
            harmony.PatchAll();
            log.LogInfo("exploration reported to " + outputDirectory);
        }

        private void OnDestroy()
        {
            if (harmony != null)
            {
                harmony.UnpatchSelf();
            }
        }

        // The game saves the player periodically and on logout, which is exactly when the revealed map
        // has changed and is worth writing. Piggybacking on it means no timer of our own and nothing
        // written while the player is standing still.
        [HarmonyPatch(typeof(Game), "SavePlayerProfile")]
        private static class ReportOnSave
        {
            private static void Postfix()
            {
                Report(false);
            }
        }

        // Logging out is the last chance to record the session, and the one moment a player will notice
        // if it is missed.
        [HarmonyPatch(typeof(Game), "Logout")]
        private static class ReportOnLogout
        {
            private static void Prefix()
            {
                Report(true);
            }
        }

        private static void Report(bool force)
        {
            try
            {
                Minimap minimap = Minimap.instance;
                Player player = Player.m_localPlayer;
                if (minimap == null || player == null)
                {
                    return;
                }
                // A save can fire several times in a moment; once a minute is plenty for a map that
                // changes as fast as somebody can walk.
                if (!force && Time.realtimeSinceStartup - lastWriteTime < 60f)
                {
                    return;
                }
                bool[] explored = ReadExplored(minimap);
                if (explored == null)
                {
                    return;
                }
                int textureSize = minimap.m_textureSize;
                float pixelSize = minimap.m_pixelSize;
                if (textureSize <= 0 || pixelSize <= 0f || explored.Length < textureSize * textureSize)
                {
                    log.LogWarning("the minimap grid does not match its declared size; nothing written");
                    return;
                }
                string world = ZNet.instance != null ? ZNet.instance.GetWorldName() : string.Empty;
                if (string.IsNullOrEmpty(world))
                {
                    return;
                }
                Write(world, player.GetPlayerID(), player.GetPlayerName(), textureSize, pixelSize, explored);
                WritePins(world, player.GetPlayerID(), minimap);
                lastWriteTime = Time.realtimeSinceStartup;
            }
            catch (Exception error)
            {
                log.LogError("could not report exploration: " + error.Message);
            }
        }

        // m_explored is private, and copying the reference rather than the contents is deliberate: the
        // grid is only read, and a 4-million-entry copy every save would be waste.
        private static bool[] ReadExplored(Minimap minimap)
        {
            FieldInfo field = typeof(Minimap).GetField("m_explored", BindingFlags.Instance | BindingFlags.NonPublic);
            if (field == null)
            {
                log.LogWarning("this build of Valheim has no Minimap.m_explored; exploration cannot be read");
                return null;
            }
            return field.GetValue(minimap) as bool[];
        }

        // The file is a short text header - readable, so a person can see what it is - then a gzipped
        // bitset of the grid, one bit per cell, row-major exactly as the game indexes it.
        private static void Write(string world, long playerID, string playerName, int textureSize, float pixelSize, bool[] explored)
        {
            Directory.CreateDirectory(outputDirectory);
            int cells = textureSize * textureSize;
            byte[] bits = new byte[(cells + 7) / 8];
            int uncovered = 0;
            for (int index = 0; index < cells; index++)
            {
                if (!explored[index])
                {
                    continue;
                }
                bits[index >> 3] |= (byte)(1 << (index & 7));
                uncovered++;
            }
            string safeWorld = Sanitize(world);
            string path = Path.Combine(outputDirectory, safeWorld + "-" + playerID.ToString(System.Globalization.CultureInfo.InvariantCulture) + ".explored");
            string temporary = path + ".tmp";
            using (FileStream file = File.Create(temporary))
            {
                string header = "neuralyze-exploration " + Schema
                    + " world=" + safeWorld
                    + " player_id=" + playerID.ToString(System.Globalization.CultureInfo.InvariantCulture)
                    + " player_name=" + Sanitize(playerName)
                    + " texture_size=" + textureSize.ToString(System.Globalization.CultureInfo.InvariantCulture)
                    + " pixel_size=" + pixelSize.ToString("0.####", System.Globalization.CultureInfo.InvariantCulture)
                    + " cells_uncovered=" + uncovered.ToString(System.Globalization.CultureInfo.InvariantCulture)
                    + " written=" + DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ", System.Globalization.CultureInfo.InvariantCulture)
                    + "\n";
                byte[] headerBytes = System.Text.Encoding.UTF8.GetBytes(header);
                file.Write(headerBytes, 0, headerBytes.Length);
                using (GZipStream compressed = new GZipStream(file, CompressionMode.Compress))
                {
                    compressed.Write(bits, 0, bits.Length);
                }
            }
            if (File.Exists(path))
            {
                File.Delete(path);
            }
            File.Move(temporary, path);
            log.LogInfo("reported " + uncovered + " uncovered cells for " + safeWorld);
        }

        // The pins the player placed: the part of their map they made themselves. Only saved pins are
    // reported - the game also keeps transient ones for pings, shouts and event areas, which are UI
    // effects rather than anything a player would expect to see on a shared map.
    //
    // A pin's type is a number in the game; the readable name goes out beside it so the portal does not
    // have to carry its own copy of an enum that could drift with a game update.
    private static readonly string[] PinTypeNames =
    {
        "icon0", "icon1", "icon2", "icon3", "death", "bed", "icon4", "shout",
        "none", "boss", "player", "random-event", "ping", "event-area",
        "hildir1", "hildir2", "hildir3",
    };

    private static void WritePins(string world, long playerID, Minimap minimap)
    {
        FieldInfo field = typeof(Minimap).GetField("m_pins", BindingFlags.Instance | BindingFlags.NonPublic);
        if (field == null)
        {
            log.LogWarning("this build of Valheim has no Minimap.m_pins; pins cannot be read");
            return;
        }
        System.Collections.Generic.List<Minimap.PinData> pins = field.GetValue(minimap) as System.Collections.Generic.List<Minimap.PinData>;
        if (pins == null)
        {
            return;
        }
        System.Text.StringBuilder json = new System.Text.StringBuilder();
        json.Append("{\"schema\":");
        json.Append(Schema);
        json.Append(",\"world\":\"");
        json.Append(Sanitize(world));
        json.Append("\",\"player_id\":");
        json.Append(playerID.ToString(System.Globalization.CultureInfo.InvariantCulture));
        json.Append(",\"written\":\"");
        json.Append(DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ", System.Globalization.CultureInfo.InvariantCulture));
        json.Append("\",\"pins\":[");
        int written = 0;
        foreach (Minimap.PinData pin in pins)
        {
            if (pin == null || !pin.m_save)
            {
                continue;
            }
            if (written > 0)
            {
                json.Append(',');
            }
            written++;
            int type = (int)pin.m_type;
            json.Append("{\"name\":\"");
            json.Append(EscapeJson(pin.m_name));
            json.Append("\",\"type\":");
            json.Append(type.ToString(System.Globalization.CultureInfo.InvariantCulture));
            json.Append(",\"type_name\":\"");
            json.Append(type >= 0 && type < PinTypeNames.Length ? PinTypeNames[type] : "unknown");
            json.Append("\",\"x\":");
            json.Append(pin.m_pos.x.ToString("0.##", System.Globalization.CultureInfo.InvariantCulture));
            json.Append(",\"z\":");
            json.Append(pin.m_pos.z.ToString("0.##", System.Globalization.CultureInfo.InvariantCulture));
            json.Append(",\"crossed_off\":");
            json.Append(pin.m_checked ? "true" : "false");
            json.Append(",\"owner_id\":");
            json.Append(pin.m_ownerID.ToString(System.Globalization.CultureInfo.InvariantCulture));
            json.Append('}');
        }
        json.Append("]}\n");
        string path = Path.Combine(outputDirectory, Sanitize(world) + "-" + playerID.ToString(System.Globalization.CultureInfo.InvariantCulture) + ".pins.json");
        string temporary = path + ".tmp";
        File.WriteAllText(temporary, json.ToString());
        if (File.Exists(path))
        {
            File.Delete(path);
        }
        File.Move(temporary, path);
        log.LogInfo("reported " + written + " saved pins for " + Sanitize(world));
    }

    // Pin names are player-typed, so they are escaped rather than stripped: a name is the whole point of
    // a pin, and "Bob_s_house" is not what somebody wrote.
    private static string EscapeJson(string value)
    {
        if (string.IsNullOrEmpty(value))
        {
            return string.Empty;
        }
        System.Text.StringBuilder escaped = new System.Text.StringBuilder(value.Length);
        foreach (char c in value)
        {
            switch (c)
            {
                case '"':
                    escaped.Append("\\\"");
                    break;
                case '\\':
                    escaped.Append("\\\\");
                    break;
                case '\n':
                case '\r':
                case '\t':
                    escaped.Append(' ');
                    break;
                default:
                    if (c < ' ')
                    {
                        escaped.Append(' ');
                    }
                    else
                    {
                        escaped.Append(c);
                    }
                    break;
            }
        }
        return escaped.ToString();
    }

    // File names and header fields both end up in paths and logs on two operating systems, so the
        // plugin refuses anything that is not plainly safe rather than trying to escape it.
        private static string Sanitize(string value)
        {
            if (string.IsNullOrEmpty(value))
            {
                return "unknown";
            }
            System.Text.StringBuilder safe = new System.Text.StringBuilder(value.Length);
            foreach (char c in value)
            {
                if (char.IsLetterOrDigit(c) || c == '-' || c == '_')
                {
                    safe.Append(c);
                }
                else
                {
                    safe.Append('_');
                }
            }
            return safe.ToString();
        }
    }
}
