using System;
using System.Collections.Generic;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace NeuralyzeVRFixes
{
    // Contextual actions on whatever you are pointing at.
    //
    // Dozens of mod features are "hold a key while interacting with X" or "press a key and
    // we guess which X you meant". Both are keyboard idioms and neither survives VR: there
    // is no key to hold, and a wrist button cannot say which chest you meant. The audit of
    // this install found 51 of 160 mod bindings are target-dependent in exactly this way.
    //
    // So: point at the thing, hold the GRIP on the pointing hand, and the actions for that kind
    // of thing are offered. That hand's trigger keeps its normal meaning - mount the horse, open
    // the chest, swing the weapon - because nothing here suppresses it, and the grip fires nothing
    // on press so it is free to mean "hold". That was the flaw in every other gesture considered:
    // the plain trigger already acts on press, so a hold-to-open-menu scheme would fire the default
    // action first. Hand-motion gestures were rejected because the physics estimator reports
    // 0.00 m/s on this install. The stick was rejected as the OPENING gesture, being walk and turn,
    // but it is what moves the highlight once the list is up, where it has no other job.
    //
    //     point at it, hold the GRIP        -> the list appears, first option highlighted
    //       (which hand: the Modifier config, RIGHT grip by default)
    //     right stick up / down             -> move the highlight, standing or seated
    //     release the GRIP                  -> run the highlighted one
    //     one push UP, then release         -> Cancel: it is the last entry, and the highlight
    //                                          wraps, so it sits one step from the opening one
    //
    // The list is written to the message area as it changes, so the target teaches its own
    // options rather than needing documentation.
    internal static class HoverMenu
    {
        private sealed class Option
        {
            internal string Label;
            internal string Kind;    // key | hold
            internal string Value;   // key name(s)
        }

        // The way out of the list. Release always runs the highlighted option, so without an
        // entry that does nothing there was no way to close the menu without performing an
        // action - the operator hit exactly that in the first live session. Held as constants
        // because Parse writes it and Commit recognises it.
        internal const string CancelLabel = "Cancel";
        private const string CancelKind = "cancel";

        // target kind -> options, parsed from config so a new target is a config edit
        private static readonly Dictionary<string, List<Option>> _table =
            new Dictionary<string, List<Option>>(StringComparer.OrdinalIgnoreCase);

        private static bool _parsed;
        private static bool _active;
        private static int _index;
        private static bool _stickReady = true;

        // The stick moves the highlight while the list is up, so its normal meaning has to be off
        // for exactly that long - otherwise every downward push also rolls the player, which is
        // what shipping this without unmapping the old binding did.
        internal static bool MenuOpen { get { return _active; } }
        private static string _kind;
        private static GameObject _target;

        private static MethodInfo _mInteract, _mMessage;
        private static FieldInfo _fHovering;
        private static bool _resolved;

        internal static void Parse(string spec)
        {
            _table.Clear();
            _parsed = true;
            if (string.IsNullOrEmpty(spec)) return;
            foreach (string group in spec.Split(';'))
            {
                int colon = group.IndexOf(':');
                if (colon < 1) continue;
                string kind = group.Substring(0, colon).Trim();
                List<Option> options = new List<Option>();
                foreach (string item in group.Substring(colon + 1).Split('|'))
                {
                    int eq = item.IndexOf('=');
                    if (eq < 1) continue;
                    string label = item.Substring(0, eq).Trim();
                    string action = item.Substring(eq + 1).Trim();
                    int sep = action.IndexOf(':');
                    if (sep < 1) continue;
                    options.Add(new Option
                    {
                        Label = label,
                        Kind = action.Substring(0, sep).Trim(),
                        Value = action.Substring(sep + 1).Trim()
                    });
                }
                if (options.Count == 0) continue;
                // Cancel is appended LAST, and deliberately is not what the list opens on.
                //
                // Appended, not prepended, because the group order in the config
                // (neuralyze.vrfixes.cfg:46) is the player's statement of which action he wants
                // first, and the list still opens on index 0: prepending would silently demote
                // every configured first choice by one place. Last costs nothing, because the
                // highlight WRAPS (see the step in TickBody) - so the last entry is exactly one
                // push UP from the opening highlight. Cancel is one step away without putting a
                // step in front of anything else.
                //
                // Not the opening highlight either: a stray grip is already covered, since a hold
                // shorter than MinHoldSeconds runs nothing. What was missing was an exit from a
                // DELIBERATE hold, and that is one push up.
                options.Add(new Option { Label = CancelLabel, Kind = CancelKind, Value = "" });
                _table[kind] = options;
            }
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                + "hover menu: " + _table.Count + " target kind(s) configured");
        }

        private static void Resolve()
        {
            if (_resolved) return;
            _resolved = true;
            _mInteract = AccessTools.Method(typeof(Player), "Interact");
            _fHovering = AccessTools.Field(typeof(Player), "m_hovering");
            // Character.Message(MessageHud.MessageType, string) is how the game itself
            // announces things, so the list lands where players already read messages.
            foreach (MethodInfo m in typeof(Character).GetMethods(BindingFlags.Instance | BindingFlags.Public))
            {
                if (m.Name != "Message") continue;
                ParameterInfo[] ps = m.GetParameters();
                if (ps.Length >= 2 && ps[1].ParameterType == typeof(string)) { _mMessage = m; break; }
            }
        }

        // What kind of thing is being pointed at. Ordered most specific first: a horse is
        // also a Character, a chest is also a piece with WearNTear, so the general kinds
        // have to lose to the specific ones.
        private static string Classify(GameObject go)
        {
            if (go == null) return null;
            if (Has(go, "Tameable") && NameHas(go, "horse")) return "horse";
            if (Has(go, "Container")) return "container";
            if (Has(go, "PrivateArea")) return "ward";
            if (Has(go, "Fireplace")) return "fireplace";
            if (Has(go, "ShipControlls") || Has(go, "Ship")) return "ship";
            if (Has(go, "WearNTear")) return "piece";
            return null;
        }

        private static readonly Dictionary<string, Type> _types = new Dictionary<string, Type>();

        private static bool Has(GameObject go, string component)
        {
            Type t;
            if (!_types.TryGetValue(component, out t))
            {
                t = TypeCache.Get(component);
                _types[component] = t;
            }
            if (t == null) return false;
            try { return go.GetComponentInParent(t) != null; } catch { return false; }
        }

        private static bool NameHas(GameObject go, string fragment)
        {
            return go.name != null && go.name.IndexOf(fragment, StringComparison.OrdinalIgnoreCase) >= 0;
        }

        // The open menu, as text for the hover label. Null when nothing is open.
        internal static string HoverText()
        {
            if (!_active || _kind == null) return null;
            List<Option> options;
            if (!_table.TryGetValue(_kind, out options) || options.Count == 0) return null;

            string text = "";
            if (_kind == "ship")
            {
                string blocker = DirectActions.HelmBlocker();
                if (blocker.Length > 0) text += blocker + "\n";
            }
            for (int i = 0; i < options.Count; i++)
            {
                text += (i == _index ? "> " : "    ") + options[i].Label + "\n";
            }
            return text + "(stick to move, release to run; one push up = Cancel)";
        }

        private static void Announce(Player p, List<Option> options)
        {
            Resolve();
            if (_mMessage == null) return;
            string text = "";
            if (_kind == "ship")
            {
                string blocker = DirectActions.HelmBlocker();
                if (blocker.Length > 0) text = blocker + "\n";
            }
            for (int i = 0; i < options.Count; i++)
            {
                text += (i == _index ? "> " : "   ") + options[i].Label + (i + 1 < options.Count ? "\n" : "");
            }
            try
            {
                // MessageType 1 is Center in every build of the game seen here; the enum is
                // resolved by value to avoid a hard reference from this assembly.
                object type = Enum.ToObject(_mMessage.GetParameters()[0].ParameterType, 1);
                _mMessage.Invoke(p, new object[] { type, text });
            }
            catch { }
        }

        private const float MinHoldSeconds = 0.35f;
        private static float _openedAt;
        private static bool _modWasDown;
        private static float _shown;
        private static string _lastSeen;

        internal static void Tick()
        {
            long _t = HookProfiler.Start();
            try { TickBody(); } finally { HookProfiler.Stop(HookProfiler.Hover, _t); }
        }

        private static void TickBody()
        {
            if (NeuralyzeVRFixesPlugin.HoverMenuEnabled == null
                || !NeuralyzeVRFixesPlugin.HoverMenuEnabled.Value) return;
            if (!_parsed) Parse(NeuralyzeVRFixesPlugin.HoverActions == null ? null : NeuralyzeVRFixesPlugin.HoverActions.Value);
            Player p = Player.m_localPlayer;
            if (p == null) { _active = false; return; }

            // One hand, on the button with no job of its own. The right trigger already acts the
            // instant it is pressed - mount, open, swing - so it can never also mean "hold to open
            // a menu"; the previous gesture dodged that by moving the whole thing to the off hand,
            // which meant pointing with one hand and operating with the other. The grip fires
            // nothing on press, so the hand that points is the hand that chooses.
            //
            // Which hand that is comes from the Modifier config, which until now was declared,
            // bound and never read while this line hardcoded the right grip - the config told the
            // player something that was not true. RightGrip stays the default for the reason above;
            // the setting exists for a left-handed player who points with the other hand.
            bool modifier = HoverModifierHeld();
            if (modifier != _modWasDown)
            {
                _modWasDown = modifier;
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "hover modifier " + (modifier ? "DOWN" : "up"));
            }
            if (!modifier)
            {
                if (_active)
                {
                    if (Time.time - _openedAt >= MinHoldSeconds) Commit(p);
                    else NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                        + "hover menu cancelled - held for less than " + MinHoldSeconds.ToString("F2")
                        + "s, treated as a stray press rather than a choice");
                }
                _active = false;
                return;
            }

            if (!_active)
            {
                GameObject go = DirectActions.MountedObject() ?? DirectActionInvoker.PointedAt(p);
                string kind = Classify(go);
                // Held with nothing usable under the laser is the normal case, but silence here is
                // indistinguishable from a broken gesture - last session produced no line at all and
                // cost a whole test run. Log what the ray and the table actually said, once per
                // change of target.
                string seen = (go == null ? "(nothing)" : go.name) + " -> " + (kind ?? "(unclassified)");
                if (seen != _lastSeen)
                {
                    _lastSeen = seen;
                    NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "hover sees " + seen
                        + (kind != null && _table.ContainsKey(kind) ? " [has options]" : " [no options]"));
                }
                if (kind == null || !_table.ContainsKey(kind)) return;
                _active = true;
                _openedAt = Time.time;
                _index = 0;
                _kind = kind;
                _target = go;
                _stickReady = true;
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "hover menu open on '" + kind + "' (" + go.name + ")");
                Announce(p, _table[_kind]);
                _shown = Time.time;
                return;
            }

            // The right stick moves the highlight: push up or down, one step per push, and the
            // stick must return near centre before it steps again so a held stick cannot run the
            // list away. The stick is free here - nobody walks in the middle of choosing.
            //
            // Read through RawRightStickY, not ApiRightY: the Api readers are zeroed unless the
            // player is seated, so on foot this saw 0.00 every frame and only the first option of a
            // group was ever reachable - Quick Stack but never Restock, Repair Area but never Add
            // Wear. The raw reader is the same VHVR axis without the seated gate, and it changes
            // nothing about mount steering, which still reads the gated Step().
            List<Option> options = _table[_kind];
            float stick = MountControls.RawRightStickY();
            if (Mathf.Abs(stick) < 0.4f) _stickReady = true;
            else if (_stickReady)
            {
                _stickReady = false;
                _index = stick > 0f ? (_index - 1 + options.Count) % options.Count
                                    : (_index + 1) % options.Count;
                Announce(p, options);
                _shown = Time.time;
            }

            // A centre message fades. Announcing once meant the list was gone before the player
            // looked up - the gesture worked all session and was reported as "left trigger does
            // nothing". Repeat it while the modifier is held so the list is simply present.
            if (Time.time - _shown > 2.0f)
            {
                Announce(p, options);
                _shown = Time.time;
            }
        }

        // The hand named by the Modifier config. Two ordinal compares per frame and no reflection
        // lookup: the probes below read the SteamVR actions DirectActionInvoker resolved once in
        // Prepare().
        private static bool HoverModifierHeld()
        {
            string hand = NeuralyzeVRFixesPlugin.HoverModifier == null
                ? null : NeuralyzeVRFixesPlugin.HoverModifier.Value;
            if (string.Equals(hand, "LeftGrip", StringComparison.OrdinalIgnoreCase))
                return DirectActionInvoker.LeftGrabHeld();
            // valheim_UseLeft is the left trigger and nothing else, so this cannot collide with the
            // right trigger's normal use-on-press meaning. There is deliberately no RightTrigger
            // option: that trigger acts the instant it is pressed, so a hold on it would always run
            // the default action before any list could appear.
            if (string.Equals(hand, "LeftTrigger", StringComparison.OrdinalIgnoreCase))
                return DirectActionInvoker.UseLeftHeld();
            return DirectActionInvoker.RightGrabHeld();
        }

        private static void Commit(Player p)
        {
            List<Option> options;
            if (_kind == null || !_table.TryGetValue(_kind, out options) || _index >= options.Count) return;
            Option chosen = options[_index];
            // Chosen Cancel: run nothing, say nothing at Info - a cancel is not an event worth a
            // log line - and leave no state behind for the next open to inherit.
            if (chosen.Kind.Equals(CancelKind, StringComparison.OrdinalIgnoreCase))
            {
                _index = 0;
                _openedAt = 0f;
                _target = null;
                _kind = null;
                return;
            }
            Resolve();
            try
            {
                if (chosen.Kind.Equals("key", StringComparison.OrdinalIgnoreCase))
                {
                    KeyPulse.Send(chosen.Value);
                    NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                        + "hover menu ran '" + chosen.Label + "' (key " + chosen.Value + ")");
                }
                else if (chosen.Kind.Equals("mount", StringComparison.OrdinalIgnoreCase))
                {
                    DirectActions.ReleaseMount();
                }
                else if (chosen.Kind.Equals("sail", StringComparison.OrdinalIgnoreCase))
                {
                    DirectActions.ShipSpeed(chosen.Value, _target);
                }
                else if (chosen.Kind.Equals("hold", StringComparison.OrdinalIgnoreCase))
                {
                    // The mod wants the key held AT THE MOMENT of interaction, so both halves
                    // happen here in one frame: latch, interact with the thing that was
                    // pointed at, release. Nothing is left held if the interact throws.
                    KeyPulse.Latch(chosen.Value);
                    try
                    {
                        if (_mInteract != null && _target != null)
                        {
                            _mInteract.Invoke(p, InteractArgs(_mInteract, _target));
                        }
                    }
                    finally
                    {
                        KeyPulse.Latch(chosen.Value);   // toggles back off
                    }
                    NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                        + "hover menu ran '" + chosen.Label + "' (hold " + chosen.Value + " + interact)");
                }
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "hover menu '" + chosen.Label + "' failed: " + e.Message);
            }
            _target = null;
            _kind = null;
        }

        // Player.Interact's signature has changed across game versions; supply whatever it
        // asks for rather than assuming three parameters.
        private static object[] InteractArgs(MethodInfo interact, GameObject target)
        {
            ParameterInfo[] ps = interact.GetParameters();
            object[] args = new object[ps.Length];
            for (int i = 0; i < ps.Length; i++)
            {
                if (ps[i].ParameterType == typeof(GameObject)) args[i] = target;
                else if (ps[i].ParameterType == typeof(bool)) args[i] = false;
                else args[i] = ps[i].ParameterType.IsValueType ? Activator.CreateInstance(ps[i].ParameterType) : null;
            }
            return args;
        }
    }
}
