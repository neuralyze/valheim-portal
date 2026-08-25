using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace NeuralyzeVRFixes
{
    // Removes a wrist-menu entry whose backing content is not on this install.
    //
    // Incident 2026-08-25. The operator: "remove add horse, add saddle from wrist menu if odinhorse
    // is not installed in mod list". Both entries were in the ring, both ran, and neither did
    // anything. His own session log had already said why, at the moment he pressed one
    // (LogOutput.log:57, written by DirectActions.DiagnoseSpawn, DirectActions.cs:498-501):
    //
    //   spawn 'rae_OdinHorse': NO SUCH PREFAB. Names containing 'rae_': rae_Campsite_MiniPit, ...
    //
    // The button was fine; the prefab was gone. OdinHorse had been uninstalled - the package is
    // absent from the profile manifest and from BepInEx/plugins, though its 68KB
    // Raelaziel.OdinHorse.cfg is still sitting in the config directory.
    //
    // WHAT IS TESTED, AND WHY IT IS NOT A MOD NAME
    //
    // A cmd: entry already names its own dependency, so the dependency is what gets asked about:
    //
    //   * "spawn <prefab> ..."  -> does ZNetScene.instance.GetPrefab(<prefab>) resolve? This is
    //     literally the call vanilla's own spawn handler makes (assembly_valheim
    //     Terminal::<InitTerminal>g__spawn|7_143 at av.il:122868, ZNetScene::GetPrefab(string) at
    //     av.il:122889), and it is what prints "Missing object " at av.il:122897 when it misses.
    //     Asking it here answers exactly the question the operator asked with his thumb.
    //
    //   * any other verb -> is it a key of Terminal.commands (av.il:117235)? That is the lookup
    //     Terminal.TryRunCommand performs - lowercase the first token, TryGetValue - before it
    //     prints "is not a recognized command" (av.il:120441-120516).
    //
    // A mod-name match was rejected. It cannot be done from the config at all: nothing in
    // "cmd:spawn rae_OdinHorse" names the plugin GUID Raelaziel.OdinHorse, so a name check needs a
    // hand-written prefab-to-mod table that has to be edited every time an entry is added - the
    // opposite of "adding a mod means adding a config line, not changing code" (MiscMenu.cs:191).
    // It is also wrong in the two ways this install actually exhibits. The operator swaps mods
    // constantly, so the equivalent-replacement case is real: install a different horse mod and a
    // GUID check on Raelaziel.OdinHorse still hides an entry that now works, or keeps one that does
    // not. And the name surfaces on disk lie - Raelaziel.OdinHorse.cfg is still present for a mod
    // that is gone, OdinPlus-OdinsHorsePen IS installed and is a building piece by another author,
    // and Raelaziel content is loaded on this profile (rae_Campsite, rae_bird_house, rae_compost)
    // with only the horse missing. No fuzzy "is there a horse mod / is there a Raelaziel mod" test
    // separates those. The prefab lookup separates them without knowing any of it.
    //
    // The capability test is also strictly stronger: it withholds an entry whose prefab name is a
    // typo, or was renamed by a mod update, even with the mod installed - because in that case the
    // button does nothing either.
    //
    // WITHHELD MEANS GONE, NOT INERT. A present button that does nothing is the complaint; a silent
    // no-op would be the same bug with extra steps. So the entry leaves the ring, and one line at
    // Message level says which entries went and why - Message is the client's log floor
    // (BepInEx.cfg LogLevels is Fatal, Error, Warning, Message, so LogInfo is invisible to him) -
    // because a button that vanishes with no explanation is just a different mystery.
    internal static class ActionAvailability
    {
        // Verdicts, keyed by "kind:value" so two entries running the same command are probed once.
        // A key in neither map has no verdict yet, and no verdict means SHOWN.
        private static readonly Dictionary<string, string> _withheld = new Dictionary<string, string>();
        private static readonly HashSet<string> _kept = new HashSet<string>();

        // The ZNetScene the verdicts were measured against. Held rather than a bool because loading
        // a different world builds a new ZNetScene with a different prefab table, and a verdict from
        // the previous world would then be asserted about content nobody has looked at.
        private static object _measuredAgainst;

        // Console verbs whose FIRST argument names a prefab. Vanilla has exactly one; the set exists
        // so a mod's own spawner verb is a one-line addition rather than a rewrite.
        private static readonly HashSet<string> PrefabArgVerbs =
            new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "spawn" };

        private static FieldInfo _commandsField;
        private static bool _commandsResolved;

        // Called from MiscMenu.Load: a re-read of the config can change every entry, so no verdict
        // from the previous list may survive it.
        internal static void Reset()
        {
            _withheld.Clear();
            _kept.Clear();
            _measuredAgainst = null;
        }

        // False only for an entry measured and found to have no backing content.
        internal static bool Offer(MiscMenu.Entry entry)
        {
            return entry == null || !_withheld.ContainsKey(Key(entry));
        }

        // Probes the WHOLE list in one pass, not one group at a time, so the report is complete the
        // first time it prints rather than filling in as the operator wanders into groups.
        internal static void Refresh(List<MiscMenu.Entry> entries)
        {
            if (entries == null || entries.Count == 0) return;
            try
            {
                // "Cannot tell" must never read as "absent". Prefabs are registered during
                // ZNetScene.Awake and the console table during Terminal.InitTerminal; a local player
                // means both have finished, including Jotunn's registrations for every mod. Before
                // that, every entry is offered and nothing is logged.
                ZNetScene scene = ZNetScene.instance;
                if (scene == null || Player.m_localPlayer == null) return;
                IDictionary commands = Commands();
                if (commands == null || commands.Count == 0) return;
                if (ReferenceEquals(_measuredAgainst, scene)) return;

                _measuredAgainst = scene;
                _withheld.Clear();
                _kept.Clear();

                // The two game lookups are bound once, here, and the decision itself is a pure
                // function of them (Explain). That split is not decoration: it is the only way this
                // rule can be exercised at all without a headset, since ZNetScene and Terminal both
                // need a running game. A harness feeds Explain the prefab and command sets measured
                // from a real profile and reads the verdicts back.
                Func<string, bool> commandExists = delegate(string verb) { return commands.Contains(verb); };
                Func<string, bool> prefabExists = delegate(string name) { return PrefabExists(scene, name); };

                List<string> gone = new List<string>();
                foreach (MiscMenu.Entry e in entries)
                {
                    string key = Key(e);
                    string reason;
                    if (!_withheld.TryGetValue(key, out reason) && !_kept.Contains(key))
                    {
                        reason = Explain(e.Kind, e.Value, commandExists, prefabExists);
                        if (reason == null) _kept.Add(key); else _withheld[key] = reason;
                    }
                    if (reason != null) gone.Add("'" + Path(e) + "' (" + key + ") - " + reason);
                }

                if (gone.Count == 0)
                {
                    NeuralyzeVRFixesPlugin.Log.LogMessage(NeuralyzeVRFixesPlugin.Tag
                        + "wrist menu: all " + entries.Count
                        + " entries have their backing content on this install; nothing withheld");
                    return;
                }
                NeuralyzeVRFixesPlugin.Log.LogMessage(NeuralyzeVRFixesPlugin.Tag
                    + "wrist menu WITHHELD " + gone.Count + " of " + entries.Count
                    + " entries - removed from the ring, not left to do nothing: "
                    + string.Join("; ", gone.ToArray())
                    + ". Install the mod that provides it, or delete the line from Actions in neuralyze.vrfixes.cfg.");
            }
            catch (Exception ex)
            {
                // A probe that throws must not cost the operator his menu: leave every verdict
                // unset, which offers everything, exactly as before this file existed.
                _measuredAgainst = null;
                _withheld.Clear();
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "wrist menu availability probe failed, offering every entry: " + ex.Message);
            }
        }

        private static string Key(MiscMenu.Entry e) { return e.Kind + ":" + e.Value; }

        // The path the OPERATOR sees, so the withheld line names the button he is looking for. Sub
        // joined this on 2026-08-25 with the second menu level: an entry moved to Admin/Spawn/Add
        // Horse would otherwise have been reported as 'Admin/Add Horse', which is a false statement
        // about where the missing button was. Key() is untouched - it stays Kind:Value, because it
        // is the dedup key for the probe and two entries running one command must be probed once.
        private static string Path(MiscMenu.Entry e)
        {
            if (e.Group.Length == 0) return e.Label;
            return e.Sub.Length == 0 ? e.Group + "/" + e.Label : e.Group + "/" + e.Sub + "/" + e.Label;
        }

        // null = available. Anything else is the sentence the log prints.
        //
        // Only cmd: is judged. Every other kind pulses a key or a ZInput name, and that is not
        // something this can decide about: the receiver may be any of the 111 plugins loaded here,
        // registered at any point, and hiding a working button is worse than showing one. cmd: is
        // different because the command layer itself can be asked, exactly and cheaply.
        internal static string Explain(string kind, string value,
                                       Func<string, bool> commandExists, Func<string, bool> prefabExists)
        {
            if (kind != "cmd") return null;

            // "spawn X; tame" is one button running two commands (DirectActions.RunCommandSequence).
            // Both have to be real, or the button half-works - which is its own mystery.
            foreach (string raw in value.Split(';'))
            {
                string command = raw.Trim();
                if (command.Length == 0) continue;
                string[] tok = command.Split(new char[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
                if (tok.Length == 0) continue;

                string verb = tok[0].ToLowerInvariant();
                if (!commandExists(verb))
                    return "console command '" + verb + "' is not registered on this install";

                if (tok.Length < 2 || !PrefabArgVerbs.Contains(verb)) continue;
                if (!prefabExists(tok[1]))
                    return "prefab '" + tok[1] + "' is on no loaded mod nor on the game itself, so '"
                           + verb + "' prints \"Missing object\" and nothing appears";
            }
            return null;
        }

        private static bool PrefabExists(ZNetScene scene, string name)
        {
            // A trailing star is vanilla's "one of everything containing this word" mode, which
            // resolves against GetPrefabNames() rather than GetPrefab() - the same list its own
            // handler walks. An empty stem matches the whole table, so it is available by
            // definition.
            if (name.EndsWith("*", StringComparison.Ordinal))
            {
                string stem = name.Substring(0, name.Length - 1);
                if (stem.Length == 0) return true;
                List<string> names = scene.GetPrefabNames();
                if (names == null) return true;
                for (int i = 0; i < names.Count; i++)
                {
                    if (names[i] != null && names[i].IndexOf(stem, StringComparison.OrdinalIgnoreCase) >= 0)
                        return true;
                }
                return false;
            }
            return scene.GetPrefab(name) != null;
        }

        // Terminal.commands is protected static, so it needs reflection; everything else here is a
        // public game API and is called directly.
        private static IDictionary Commands()
        {
            if (!_commandsResolved)
            {
                _commandsResolved = true;
                _commandsField = AccessTools.Field(typeof(Terminal), "commands");
                if (_commandsField == null)
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "Terminal.commands not found; cmd: entries cannot be checked and are all offered");
            }
            return _commandsField == null ? null : _commandsField.GetValue(null) as IDictionary;
        }
    }
}
