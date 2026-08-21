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
        // Which Game object's exit has already been reported. A quit runs several of the hooks below in
        // sequence, and one exit must produce one upload; instance ids are unique per object, so this
        // also lets a second session in the same process (main menu, play again) report again.
        private static int exitReportedForGame;
        // How long the whole exit upload may take, across both files. The game itself only blocks for
        // two seconds on the way out - Game.OnApplicationQuit ends in Thread.Sleep(2000),
        // assembly_valheim.dll IL_0033 - and a player whose game appears to hang on quit kills it,
        // which loses the very upload this is trying to make. Anything left unsent is left unmarked for
        // the launcher's sweep, which is the path that has always worked.
        private const int ExitUploadBudgetMs = 5000;
        private static int exitUploadDeadline;
        private Harmony harmony;

        private static string uploadURL;
        private static string uploadToken;

        private void Awake()
        {
            log = Logger;
            // Given by the launcher. Absent for a hand-installed profile, in which case the files still
            // get written and the launcher collects them next time - the upload is an improvement on
            // that path, never a replacement for it.
            uploadURL = Environment.GetEnvironmentVariable("VALHEIM_EXPLORATION_UPLOAD_URL");
            uploadToken = Environment.GetEnvironmentVariable("VALHEIM_EXPLORATION_TOKEN");
            // The launcher passes the directory it will upload from. Without it, the plugin still works
            // and writes beside its own config, so a hand-installed profile is not silently useless.
            outputDirectory = Environment.GetEnvironmentVariable("VALHEIM_EXPLORATION_DIR");
            if (string.IsNullOrEmpty(outputDirectory))
            {
                outputDirectory = Path.Combine(Paths.ConfigPath, "exploration");
            }
            harmony = new Harmony("com.neuralyze.valheim.explorationreporter");
            harmony.PatchAll();
            // LogMessage, not LogInfo: the client's BepInEx.cfg excludes Info from the disk log, so
            // everything the plugin said about itself was invisible on 2026-08-20 while it was quietly
            // failing to upload anything at all. Whether the upload is configured goes out here too -
            // an empty URL or token is the other way this ends up sending nothing without a word.
            log.LogMessage("exploration reporter loaded; writing to " + outputDirectory
                + (string.IsNullOrEmpty(uploadURL) || string.IsNullOrEmpty(uploadToken)
                    ? "; no upload configured, the launcher will collect these"
                    : "; uploading on exit"));
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

        // Every way out of a session funnels through Game.Shutdown: the logout paths reach it from
        // ContinueLogout (assembly_valheim.dll, ContinueLogout IL_0009) and a quit straight to the
        // desktop reaches it from OnApplicationQuit (IL_0024). Those are its only two callers in the
        // whole assembly, and Game.m_startScene is only ever loaded by ContinueLogout, so there is no
        // return to the main menu that skips this.
        //
        // Patching Game.Logout alone - which is what this did until 2026-08-20 - covered exactly one of
        // those two routes. That evening four players finished a session and the portal received
        // nothing: the maps were written (Hrafnheim--322254472.explored at 19:40:14 with
        // cells_uncovered=2278) while its .sent marker still held the 934-cell digest from 17:20:15,
        // and the session log contained no warning from this plugin, because nothing was attempted.
        //
        // A prefix, not a postfix: the body sets m_shuttingDown, saves the profile, then tears down
        // ZNetScene and ZNet (IL_0034), and Report needs ZNet.instance for the world name.
        [HarmonyPatch(typeof(Game), "Shutdown")]
        private static class ReportOnShutdown
        {
            private static void Prefix(Game __instance)
            {
                ReportExit(__instance);
            }
        }

        // The backstop for a quit that does not reach Shutdown again: OnApplicationQuit returns before
        // its own call to Shutdown when m_shuttingDown is already set (IL_0006). A prefix still runs,
        // and it is the last moment at which the minimap and the player object are alive - the body
        // ends in Thread.Sleep(2000) and then Unity destroys the scene.
        //
        // Application.quitting exists (UnityEngine.CoreModule, add_quitting) and is not used: it fires
        // after OnApplicationQuit has already run Game.Shutdown, so ZNet is down and Report would bail
        // on an empty world name. ZNet.Shutdown is not used either - Game.Shutdown is its only caller
        // in the assembly, so it would add a second hook covering nothing. Game.OnDestroy is not used:
        // it only nulls Game.instance, and by then Player.m_localPlayer may already be gone.
        [HarmonyPatch(typeof(Game), "OnApplicationQuit")]
        private static class ReportOnApplicationQuit
        {
            private static void Prefix(Game __instance)
            {
                ReportExit(__instance);
            }
        }

        // One upload per exit, however many of the hooks above fire. A quit from the in-game menu runs
        // Logout, ContinueLogout, Shutdown and then OnApplicationQuit, so two of them firing for one
        // quit is the ordinary case rather than the exception.
        private static void ReportExit(Game game)
        {
            int id = game.GetInstanceID();
            if (id == exitReportedForGame)
            {
                return;
            }
            exitReportedForGame = id;
            exitUploadDeadline = Environment.TickCount + ExitUploadBudgetMs;
            Report(true);
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
                string explored_path = Write(world, player.GetPlayerID(), player.GetPlayerName(), textureSize, pixelSize, explored);
                string pins_path = WritePins(world, player.GetPlayerID(), minimap);
                lastWriteTime = Time.realtimeSinceStartup;
                // Only on the way out. Uploading on every autosave would put a request on the wire every
                // few minutes for a map that has usually not changed, and the launcher's sweep already
                // covers anything this misses.
                if (force)
                {
                    Upload(explored_path);
                    Upload(pins_path);
                }
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
        private static string Write(string world, long playerID, string playerName, int textureSize, float pixelSize, bool[] explored)
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
            // LogMessage: with Info excluded from the client's disk log, "it wrote" and "it sent" were
            // both unanswerable from a session log on 2026-08-20. "wrote", not "reported", because
            // reporting is what the upload below does and confusing the two is how a written-but-never-
            // sent map read as a delivered one.
            log.LogMessage("wrote " + uncovered + " uncovered cells for " + safeWorld);
            return path;
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

    private static string WritePins(string world, long playerID, Minimap minimap)
    {
        FieldInfo field = typeof(Minimap).GetField("m_pins", BindingFlags.Instance | BindingFlags.NonPublic);
        if (field == null)
        {
            log.LogWarning("this build of Valheim has no Minimap.m_pins; pins cannot be read");
            return null;
        }
        System.Collections.Generic.List<Minimap.PinData> pins = field.GetValue(minimap) as System.Collections.Generic.List<Minimap.PinData>;
        if (pins == null)
        {
            return null;
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
        return path;
    }

    // Sends one report to the portal, then writes the same .sent marker the launcher writes, so the two
    // upload paths never send the same bytes twice. Best effort throughout, and bounded: this runs while
    // the game is shutting down, so it gets whatever is left of ExitUploadBudgetMs and no more. Anything
    // missed here keeps no marker and is collected by the launcher's sweep next launch.
    private static void Upload(string path)
    {
        if (string.IsNullOrEmpty(path) || string.IsNullOrEmpty(uploadURL) || string.IsNullOrEmpty(uploadToken))
        {
            return;
        }
        // The budget for the whole exit, shared by both files. TickCount wraps roughly every 25 days;
        // the subtraction is correct across the wrap for an interval this short, which is why it is
        // written as a difference rather than a comparison.
        int remaining = exitUploadDeadline - Environment.TickCount;
        if (remaining <= 0)
        {
            // A Warning, not Info: this is the one thing an operator needs to see if quits start
            // outrunning the budget, and Warning is in the client's disk log.
            log.LogWarning("out of time on the way out; leaving " + Path.GetFileName(path) + " for the launcher");
            return;
        }
        try
        {
            byte[] payload = File.ReadAllBytes(path);
            string digest = Sha256Hex(payload);
            string marker = path + ".sent";
            if (File.Exists(marker) && File.ReadAllText(marker).Trim() == digest)
            {
                return;
            }
            string field = path.EndsWith(".pins.json", StringComparison.Ordinal) ? "pins" : "explored";
            // Written by hand rather than with a helper: this runs inside the game's own runtime, where
            // pulling in an HTTP client library is how a mod breaks somebody's session.
            string boundary = "----neuralyze" + DateTime.UtcNow.Ticks.ToString(System.Globalization.CultureInfo.InvariantCulture);
            byte[] head = System.Text.Encoding.UTF8.GetBytes(
                "--" + boundary + "\r\n"
                + "Content-Disposition: form-data; name=\"" + field + "\"; filename=\"" + Path.GetFileName(path) + "\"\r\n"
                + "Content-Type: application/octet-stream\r\n\r\n");
            byte[] tail = System.Text.Encoding.UTF8.GetBytes("\r\n--" + boundary + "--\r\n");

            System.Net.HttpWebRequest request = (System.Net.HttpWebRequest)System.Net.WebRequest.Create(uploadURL);
            request.Method = "POST";
            request.ContentType = "multipart/form-data; boundary=" + boundary;
            request.Headers.Add("Authorization", "Bearer " + uploadToken);
            // Was a flat 15 s each, so two files could hold a quitting game for up to a minute.
            // Timeout is applied to GetRequestStream and again to GetResponse and cannot be changed
            // once the request has started, so half the remaining budget each bounds one request by
            // what is actually left of it.
            int slice = Math.Max(1, remaining / 2);
            request.Timeout = slice;
            request.ReadWriteTimeout = slice;
            request.ContentLength = head.Length + payload.Length + tail.Length;
            using (System.IO.Stream body = request.GetRequestStream())
            {
                body.Write(head, 0, head.Length);
                body.Write(payload, 0, payload.Length);
                body.Write(tail, 0, tail.Length);
            }
            int status;
            using (System.Net.HttpWebResponse response = (System.Net.HttpWebResponse)request.GetResponse())
            {
                status = (int)response.StatusCode;
            }
            if (status != (int)System.Net.HttpStatusCode.Created)
            {
                // No marker: only a 201 means the portal has it, and anything else must stay collectable.
                log.LogWarning("portal refused the report: " + status);
                return;
            }
            File.WriteAllText(marker, digest);
            log.LogMessage("sent " + Path.GetFileName(path) + " (" + payload.Length + " bytes) to the portal: HTTP " + status);
        }
        catch (Exception error)
        {
            // No marker written, so the launcher will send it on the next run. This is the reason the
            // sweep stays: a logout with no network still reaches the portal eventually.
            log.LogWarning("could not send " + Path.GetFileName(path) + " now, leaving it for the launcher: " + error.Message);
        }
    }

    private static string Sha256Hex(byte[] payload)
    {
        using (System.Security.Cryptography.SHA256 hash = System.Security.Cryptography.SHA256.Create())
        {
            byte[] sum = hash.ComputeHash(payload);
            System.Text.StringBuilder hex = new System.Text.StringBuilder(sum.Length * 2);
            foreach (byte b in sum)
            {
                hex.Append(b.ToString("x2", System.Globalization.CultureInfo.InvariantCulture));
            }
            return hex.ToString();
        }
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
