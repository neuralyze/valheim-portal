using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using BepInEx;
using HarmonyLib;

namespace Neuralyze.ValheimPlayerIdentities
{
    // Records which player id belongs to which character name, so the portal's map can say "Jarn built
    // this" instead of "builder 9451".
    //
    // Why a plugin is needed at all. Valheim stamps a piece with the builder's player id - a number
    // generated when the character was created - and nothing on a dedicated server ever learns the name
    // that goes with it: the name lives in the player's own character file on their machine. The server
    // log prints names beside a per-session network id, which is a different number space; the names in
    // Hrafnheim's log matched none of the five ids stamped on its pieces.
    //
    // Inside the running game both facts sit together. Player.SetPlayerID writes ZDOVars.s_playerID and
    // ZDOVars.s_playerName onto the player's own ZDO, and the server holds that ZDO like any other. A
    // connected peer carries m_characterID pointing at it. So the pairing is already there to be read -
    // it is simply never written down. This writes it down.
    //
    // What it deliberately does not do: guess. A pair is recorded only when the game itself supplies
    // both halves for the same character. Nothing is inferred from log lines, join order, or timing.
    [BepInPlugin("com.neuralyze.valheim.playeridentities", "Neuralyze Player Identities", "1.0.0")]
    public sealed class PlayerIdentities : BaseUnityPlugin
    {
        private const string DefaultDirectory = "/config";
        private const string FileName = "player_identities.json";

        // The pairs seen this run, so an unchanged file is never rewritten.
        private static readonly Dictionary<long, string> Known = new Dictionary<long, string>();
        private static string outputPath;
        private static BepInEx.Logging.ManualLogSource log;
        private Harmony harmony;

        private void Awake()
        {
            log = Logger;
            string directory = Environment.GetEnvironmentVariable("VALHEIM_PLAYER_IDENTITY_DIR");
            if (string.IsNullOrEmpty(directory))
            {
                directory = DefaultDirectory;
            }
            outputPath = Path.Combine(directory, FileName);
            Load();
            harmony = new Harmony("com.neuralyze.valheim.playeridentities");
            harmony.PatchAll();
            // Written immediately, even with nothing in it. The file's existence is how the portal - and
            // whoever is debugging a deployment - can tell "the plugin is running and nobody has
            // connected yet" from "the plugin is not installed". Log lines cannot do that job here: this
            // server's console filters most of them.
            Save();
            log.LogInfo("player identities recorded to " + outputPath + "; " + Known.Count + " already known");
        }

        private void OnDestroy()
        {
            if (harmony != null)
            {
                harmony.UnpatchSelf();
            }
        }

        // A character's ZDO arrives after the peer does, so the pairing is attempted on a timer rather
        // than once at connect. Cheap: a handful of peers, one dictionary lookup each.
        [HarmonyPatch(typeof(ZNet), "UpdatePeers")]
        private static class RecordConnectedPeers
        {
            private static void Postfix(ZNet __instance)
            {
                if (__instance == null || !ZNet.instance.IsServer() || ZDOMan.instance == null)
                {
                    return;
                }
                bool changed = false;
                foreach (ZNetPeer peer in __instance.GetConnectedPeers())
                {
                    if (peer == null || peer.m_characterID == ZDOID.None)
                    {
                        continue;
                    }
                    ZDO zdo = ZDOMan.instance.GetZDO(peer.m_characterID);
                    if (zdo == null)
                    {
                        continue;
                    }
                    long playerID = zdo.GetLong(ZDOVars.s_playerID, 0L);
                    string name = zdo.GetString(ZDOVars.s_playerName, string.Empty);
                    // Both halves from the same character, or nothing. A peer name without the id it
                    // stamps on pieces is exactly the useless pairing the portal already had.
                    if (playerID == 0L || string.IsNullOrEmpty(name))
                    {
                        continue;
                    }
                    string existing;
                    if (Known.TryGetValue(playerID, out existing) && existing == name)
                    {
                        continue;
                    }
                    // A renamed character keeps its id, so the newest name wins and the change is logged
                    // rather than hidden.
                    if (existing != null)
                    {
                        log.LogInfo("player " + playerID + " is now called " + name + " (was " + existing + ")");
                    }
                    else
                    {
                        log.LogInfo("player " + playerID + " is " + name);
                    }
                    Known[playerID] = name;
                    changed = true;
                }
                if (changed)
                {
                    Save();
                }
            }
        }

        private static void Load()
        {
            try
            {
                if (!File.Exists(outputPath))
                {
                    return;
                }
                // A tiny reader rather than a JSON dependency: this file is written by the method below
                // and by nothing else, so its shape is known.
                foreach (string chunk in File.ReadAllText(outputPath).Split('{'))
                {
                    int idAt = chunk.IndexOf("\"player_id\":", StringComparison.Ordinal);
                    int nameAt = chunk.IndexOf("\"name\":\"", StringComparison.Ordinal);
                    if (idAt < 0 || nameAt < 0)
                    {
                        continue;
                    }
                    string idText = chunk.Substring(idAt + 12).Split(',')[0].Trim();
                    string name = chunk.Substring(nameAt + 8);
                    name = name.Substring(0, name.IndexOf('"'));
                    long id;
                    if (long.TryParse(idText, NumberStyles.Integer, CultureInfo.InvariantCulture, out id) && id != 0L)
                    {
                        Known[id] = Unescape(name);
                    }
                }
            }
            catch (Exception error)
            {
                log.LogWarning("could not read existing player identities: " + error.Message);
            }
        }

        // Written whole and moved into place, so the portal never reads a half-written file.
        private static void Save()
        {
            try
            {
                string directory = Path.GetDirectoryName(outputPath);
                if (!string.IsNullOrEmpty(directory))
                {
                    Directory.CreateDirectory(directory);
                }
                StringBuilder json = new StringBuilder();
                json.Append("{\"schema\":1,\"updated\":\"");
                json.Append(DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture));
                json.Append("\",\"players\":[");
                bool first = true;
                foreach (KeyValuePair<long, string> pair in Known)
                {
                    if (!first)
                    {
                        json.Append(',');
                    }
                    first = false;
                    json.Append("{\"player_id\":");
                    json.Append(pair.Key.ToString(CultureInfo.InvariantCulture));
                    json.Append(",\"name\":\"");
                    json.Append(Escape(pair.Value));
                    json.Append("\"}");
                }
                json.Append("]}\n");
                string temporary = outputPath + ".tmp";
                File.WriteAllText(temporary, json.ToString());
                if (File.Exists(outputPath))
                {
                    File.Delete(outputPath);
                }
                File.Move(temporary, outputPath);
            }
            catch (Exception error)
            {
                log.LogError("could not write player identities: " + error.Message);
            }
        }

        private static string Escape(string value)
        {
            StringBuilder escaped = new StringBuilder(value.Length);
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

        private static string Unescape(string value)
        {
            return value.Replace("\\\"", "\"").Replace("\\\\", "\\");
        }
    }
}
