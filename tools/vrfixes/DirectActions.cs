using System;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace NeuralyzeVRFixes
{
    // Calls the game directly instead of synthesising input.
    //
    // Written after a measured session in which all eight wrist-menu actions provably fired
    // (`misc zinput pulse '<name>'` x8 in the log) and NOTHING happened in game. The pulse
    // reaches ZInput.GetButtonDown correctly - VHVR's Prefix answers true once and its Postfix
    // ORs the result - but a value nobody reads is not an action. Each of those names has its
    // own gate: MapZoomIn/Out are polled only while the map is open, Joy* names are skipped
    // when ZInput.IsGamepadEnabled() is false, and mod names need that mod polling every frame.
    //
    // Every method here reports what it did, because the previous design's silence is what
    // burned a whole test session.
    internal static class DirectActions
    {
        // Every reflective handle this class needs, resolved once.
        //
        // AccessTools.TypeByName walks every loaded assembly - 115 of them here - and this is the
        // THIRD time that call has landed on a per-frame path and cost the player their frame rate.
        // The first two were in the wrist ring and the input bridge. This one was worse because it
        // hid: AtHelm() is called by the menu context predicate for every "when:helm" entry, on both
        // wrists, every frame, so a single unguarded lookup became roughly a hundred milliseconds
        // per rebuild - and it sat outside every phase timer, in the argument list of another call.
        //
        // The rule this encodes: a reflective lookup by NAME belongs in a static field, resolved on
        // first use, never in a method body that anything might call each frame.
        private static class Refs
        {
            internal static Type Player, Character, Ship, ShipControlls, ZNetScene;
            internal static FieldInfo LocalPlayer, DoodadController;
            internal static MethodInfo IsAttached, IsEncumbered, GetStandingOnShip, StopDoodadControl, AttachStop;
            private static bool _done;

            internal static void Ensure()
            {
                if (_done) return;
                _done = true;
                Player = TypeCache.Get("Player");
                Character = TypeCache.Get("Character");
                Ship = TypeCache.Get("Ship");
                ShipControlls = TypeCache.Get("ShipControlls");
                ZNetScene = TypeCache.Get("ZNetScene");
                if (Player != null)
                {
                    LocalPlayer = AccessTools.Field(Player, "m_localPlayer");
                    DoodadController = AccessTools.Field(Player, "m_doodadController");
                    StopDoodadControl = AccessTools.Method(Player, "StopDoodadControl");
                    AttachStop = AccessTools.Method(Player, "AttachStop");
                }
                if (Character != null)
                {
                    IsAttached = AccessTools.Method(Character, "IsAttached");
                    IsEncumbered = AccessTools.Method(Character, "IsEncumbered");
                    GetStandingOnShip = AccessTools.Method(Character, "GetStandingOnShip");
                }
            }

            internal static object Local()
            {
                Ensure();
                return LocalPlayer == null ? null : LocalPlayer.GetValue(null);
            }

            internal static object Doodad()
            {
                object p = Local();
                return p == null || DoodadController == null ? null : DoodadController.GetValue(p);
            }
        }

        private static void Log(string msg)
        {
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "misc " + msg);
        }

        private static void Warn(string msg)
        {
            NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "misc " + msg);
        }

        private static object Instance(string typeName)
        {
            Type t = TypeCache.Get(typeName);
            if (t == null) return null;
            PropertyInfo p = t.GetProperty("instance", BindingFlags.Static | BindingFlags.Public);
            if (p != null) return p.GetValue(null, null);
            FieldInfo f = AccessTools.Field(t, "m_instance") ?? AccessTools.Field(t, "instance");
            return f == null ? null : f.GetValue(null);
        }

        // Emotes are chat commands in Valheim - there is no emote API to call. Chat.SendText
        // takes (Talker.Type, string), so the enum is resolved by NAME rather than by an
        // assumed ordinal: Talker.Type is not documented to start at Normal.
        internal static bool Emote(string name)
        {
            try
            {
                object chat = Instance("Chat");
                if (chat == null) { Warn("emote '" + name + "': Chat.instance null"); return false; }
                Type talker = TypeCache.Get("Talker");
                Type kind = talker == null ? null : AccessTools.Inner(talker, "Type");
                MethodInfo send = AccessTools.Method(chat.GetType(), "SendText");
                if (kind == null || send == null)
                {
                    Warn("emote '" + name + "': Chat.SendText/Talker.Type not resolvable");
                    return false;
                }
                object normal = Enum.Parse(kind, "Normal", true);
                send.Invoke(chat, new object[] { normal, "/" + name });
                Log("emote '/" + name + "' sent");
                return true;
            }
            catch (Exception e) { Warn("emote '" + name + "' failed: " + e.Message); return false; }
        }

        // Minimap keeps SEPARATE zoom values for the corner and full-screen views
        // (m_smallZoom / m_largeZoom) and picks by m_mode, so writing the wrong one silently
        // does nothing visible. Smaller zoom value = closer in.
        internal static bool Zoom(string direction)
        {
            try
            {
                object map = Instance("Minimap");
                if (map == null) { Warn("zoom: Minimap.instance null"); return false; }
                Type t = map.GetType();
                FieldInfo modeField = AccessTools.Field(t, "m_mode");
                string mode = modeField == null ? "?" : Convert.ToString(modeField.GetValue(map));
                bool large = mode != null && mode.IndexOf("Large", StringComparison.OrdinalIgnoreCase) >= 0;
                FieldInfo zoomField = AccessTools.Field(t, large ? "m_largeZoom" : "m_smallZoom");
                if (zoomField == null) { Warn("zoom: zoom field missing"); return false; }

                float zoom = Convert.ToSingle(zoomField.GetValue(map));
                float min = ReadFloat(t, map, "m_minZoom", 0.01f);
                float max = ReadFloat(t, map, "m_maxZoom", 1.0f);
                bool inward = direction.IndexOf("in", StringComparison.OrdinalIgnoreCase) >= 0;
                float next = Mathf.Clamp(inward ? zoom * 0.6f : zoom / 0.6f, min, max);
                zoomField.SetValue(map, next);
                if (Mathf.Approximately(next, zoom))
                {
                    Log("zoom " + direction + " already at the " + (inward ? "closest" : "widest")
                        + " limit (" + next.ToString("F3") + ", range " + min.ToString("F3") + ".." + max.ToString("F3") + ")");
                    return true;
                }
                Log("zoom " + direction + " on the " + (large ? "full-screen" : "corner") + " map: "
                    + zoom.ToString("F3") + " -> " + next.ToString("F3"));
                return true;
            }
            catch (Exception e) { Warn("zoom failed: " + e.Message); return false; }
        }

        private static float ReadFloat(Type t, object target, string field, float fallback)
        {
            FieldInfo f = AccessTools.Field(t, field);
            if (f == null) return fallback;
            try { return Convert.ToSingle(f.GetValue(target)); } catch { return fallback; }
        }

        // Same shape as the console opener: show the window, focus the field, raise the SteamVR
        // keyboard. Chat is the one text surface VHVR already supports, so this is the path
        // most likely to work.
        // Opens the VR keyboard through VHVR's OWN chat path, which is the only route that
        // actually submits what you type.
        //
        // InputManager.OnKeyboardClosed only acts on the typed text when
        // QuickAbstract.shouldStartChat is set:
        //
        //     if (text.StartsWith("/cmd")) Console.instance.TryRunCommand(text.Remove(0, 5));
        //     else { Chat.instance.m_input.text = text; Chat.instance.InputText(); }
        //
        // Earlier versions raised the keyboard without setting that flag, so the text landed in the
        // field and was never submitted - which is exactly why typing `god` did nothing.
        //
        // `prefill` seeds the field, and ShowKeyboard is given the field's current text, so passing
        // "/cmd " makes everything typed afterwards run as a console command.
        internal static bool OpenChat() { return OpenKeyboard("", "chat"); }

        internal static bool OpenConsoleKeyboard()
        {
            MarkPlayerIssued();
            return OpenKeyboard("/cmd ", "console");
        }

        private static bool OpenKeyboard(string prefill, string what)
        {
            try
            {
                object chat = Instance("Chat");
                if (chat == null) { Warn(what + ": Chat.instance null"); return false; }

                Type quick = TypeCache.Get("ValheimVRMod.Scripts.QuickAbstract");
                FieldInfo flag = quick == null ? null : AccessTools.Field(quick, "shouldStartChat");
                if (flag == null)
                {
                    Warn(what + ": QuickAbstract.shouldStartChat not found - typed text would be discarded");
                    return false;
                }

                FieldInfo inputField = AccessTools.Field(chat.GetType(), "m_input")
                                       ?? AccessTools.Field(TypeCache.Get("Terminal"), "m_input");
                object input = inputField == null ? null : inputField.GetValue(chat);
                if (input == null) { Warn(what + ": chat input field not found"); return false; }

                PropertyInfo textProp = input.GetType().GetProperty("text");
                if (textProp != null) textProp.SetValue(input, prefill, null);

                flag.SetValue(null, true);
                bool keyboard = ConsoleOpener.RaiseKeyboard(input);
                Log(what + " keyboard " + (keyboard ? "opened" : "FAILED to open")
                    + (prefill.Length > 0 ? " prefilled with '" + prefill + "'" : "")
                    + " - submits on keyboard close");
                return keyboard;
            }
            catch (Exception e) { Warn(what + " keyboard failed: " + e.Message); return false; }
        }

        internal static bool GuardianPower()
        {
            try
            {
                Type player = TypeCache.Get("Player");
                object local = player == null ? null : AccessTools.Field(player, "m_localPlayer").GetValue(null);
                if (local == null) { Warn("power: no local player"); return false; }
                MethodInfo start = AccessTools.Method(player, "StartGuardianPower");
                if (start == null) { Warn("power: Player.StartGuardianPower missing"); return false; }
                object ok = start.Invoke(local, null);
                Log("guardian power -> " + (ok == null ? "invoked" : ok.ToString()));
                return true;
            }
            catch (Exception e) { Warn("power failed: " + e.Message); return false; }
        }

        // Escape hatch for an adopted mod panel that has taken the screen and will not give it back.
        // Reported symptom: an AdminQoL window opened, rendered partly off the panel, could not be
        // closed, and blocked thumbstick movement - which ended a test session.
        //
        // Three attempts, cheapest first, each reported so the log says which one worked:
        //   1. every canvas we adopted that is currently active gets deactivated
        //   2. Menu.instance is hidden if it is showing
        //   3. an Escape key pulse, which is what most mod panels actually listen for
        // Seated at a ship's controls.
        // The thing the local player is riding or steering, or null.
        internal static object MountedController()
        {
            try { return Refs.Doodad(); }
            catch { return null; }
        }

        // The GameObject of what you are riding, so a menu can act on a horse you cannot point at.
        internal static GameObject MountedObject()
        {
            Component c = MountedController() as Component;
            return c == null ? null : c.gameObject;
        }

        internal static bool AtHelm()
        {
            try { return Refs.Doodad() != null; }
            catch { return false; }
        }

        // Report which of the game's gates refused a helm, immediately after an attempt.
        //
        // ShipControlls.Interact returns false without a word, and its four conditions are the only
        // explanation. Runs once per attempt, never per frame.
        internal static void ExplainHelm(GameObject target)
        {
            try
            {
                Refs.Ensure();
                object helm = Refs.ShipControlls == null || target == null
                    ? null : target.GetComponentInParent(Refs.ShipControlls);
                if (helm == null) return;

                object local = Refs.Local();
                if (local == null) return;
                if (Refs.IsAttached != null && Convert.ToBoolean(Refs.IsAttached.Invoke(local, null))) return;  // it worked

                MethodInfo dist = AccessTools.Method(Refs.ShipControlls, "InUseDistance");
                object onShip = Refs.GetStandingOnShip == null ? null : Refs.GetStandingOnShip.Invoke(local, null);
                bool onShipReal = onShip != null && !onShip.Equals(null);
                FieldInfo nviewF = AccessTools.Field(Refs.ShipControlls, "m_nview");
                object nview = nviewF == null ? null : nviewF.GetValue(helm);

                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "helm refused: znetValid=" + Convert.ToString(nview == null ? null : SteamVRProbe.Call(nview, "IsValid"))
                    + " inUseDistance=" + (dist == null ? "?" : Convert.ToString(dist.Invoke(helm, new object[] { local })))
                    + " encumbered=" + (Refs.IsEncumbered == null ? "?" : Convert.ToString(Refs.IsEncumbered.Invoke(local, null)))
                    + " standingOnShip=" + (onShipReal ? ((Component)onShip).name : "NULL")
                    + " - all four must pass");
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "helm explain failed: " + e.Message);
            }
        }

        // Why the rudder is ignoring you.
        //
        // Reported as "couldn't click rudder and enter that mode". ShipControlls.Interact refuses
        // unless you are standing ON the ship, inside the game's use range, and unencumbered - none
        // of which the laser tells you, because the laser reaches 8m and the rudder does not care.
        internal static string HelmBlocker()
        {
            try
            {
                Type player = TypeCache.Get("Player");
                object local = player == null ? null : AccessTools.Field(player, "m_localPlayer").GetValue(null);
                if (local == null) return "";

                Type character = TypeCache.Get("Character");
                MethodInfo standing = character == null ? null : AccessTools.Method(character, "GetStandingOnShip");
                object ship = standing == null ? null : standing.Invoke(local, null);
                bool onBoard = ship != null && !ship.Equals(null);

                MethodInfo enc = character == null ? null : AccessTools.Method(character, "IsEncumbered");
                bool loaded = enc != null && Convert.ToBoolean(enc.Invoke(local, null));

                if (!onBoard) return "step onto the boat to steer";
                if (loaded) return "too heavy to steer - drop something";
                return "";
            }
            catch { return ""; }
        }

        // Leave a helm, saddle or chair.
        //
        // Player.StopDoodadControl is the game's own exit and it does TWO things: OnUseStop, which
        // hands control back over the network, and m_doodadController = null. Calling only the first
        // produced the exact failure the player reported - "the A button would just make me jump in
        // place but stay locked": Player.SetControls and UpdateDoodadControls route the movement
        // stick into whatever m_doodadController points at, so with it still set the player was out
        // of the seat and steering the boat with their legs' input. Jumping worked because jumping
        // does not go through that path.
        internal static bool ReleaseMount()
        {
            try
            {
                Type player = TypeCache.Get("Player");
                object local = player == null ? null : AccessTools.Field(player, "m_localPlayer").GetValue(null);
                if (local == null) return false;

                FieldInfo doodadField = AccessTools.Field(player, "m_doodadController");
                object doodad = doodadField == null ? null : doodadField.GetValue(local);

                MethodInfo stopControl = AccessTools.Method(player, "StopDoodadControl");
                if (doodad != null && stopControl != null)
                {
                    stopControl.Invoke(local, null);
                }
                else if (doodad != null)
                {
                    // Fallback only: releases control but leaves the field set, which is the bug
                    // above. Named in the log so a future session can see it happened.
                    MethodInfo stop = AccessTools.Method(doodad.GetType(), "OnUseStop", new Type[] { TypeCache.Get("Humanoid") })
                                   ?? AccessTools.Method(doodad.GetType(), "OnUseStop");
                    if (stop != null)
                    {
                        stop.Invoke(doodad, stop.GetParameters().Length == 0 ? new object[0] : new object[] { local });
                        NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                            + "StopDoodadControl missing; used OnUseStop only - movement may stay routed to the vehicle");
                    }
                }

                Type character = TypeCache.Get("Character");
                MethodInfo attachedM = character == null ? null : AccessTools.Method(character, "IsAttached");
                bool wasAttached = attachedM != null && Convert.ToBoolean(attachedM.Invoke(local, null));
                MethodInfo detach = AccessTools.Method(player, "AttachStop");
                if (wasAttached && detach != null) detach.Invoke(local, null);

                object doodadAfter = doodadField == null ? null : doodadField.GetValue(local);
                bool still = attachedM != null && Convert.ToBoolean(attachedM.Invoke(local, null));

                // Where the release left you, and whether the vehicle still owns your movement.
                string landed = "";
                try
                {
                    MethodInfo swimming = character == null ? null : AccessTools.Method(character, "IsSwimming");
                    MethodInfo grounded = character == null ? null : AccessTools.Method(character, "IsOnGround");
                    Component c = local as Component;
                    landed = " swimming=" + (swimming == null ? "?" : Convert.ToString(swimming.Invoke(local, null)))
                           + " onGround=" + (grounded == null ? "?" : Convert.ToString(grounded.Invoke(local, null)))
                           + (c == null ? "" : " at y=" + c.transform.position.y.ToString("F1"));
                }
                catch { }

                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "release mount: doodad " + (doodad == null ? "none" : "cleared=" + (doodadAfter == null))
                    + ", attached=" + still + landed
                    + " - movement returns to your legs only when doodad is cleared");
                return doodadAfter == null && !still;
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "release mount failed: " + e.Message);
                return false;
            }
        }

        // Step a ship's sail up or down.
        //
        // VHVR removes the keyboard route entirely - "Forward" and "Backward" are in its
        // ignoredZInputs - and replaces it with a two-handed sail-pull gesture that no player would
        // guess and that cannot be performed while one hand steers. Ship.Forward()/Backward() are the
        // game's own speed steps, which is what the gesture ends up calling.
        // The ship under the pointer, else the one being steered.
        private static object ResolveShip(GameObject target)
        {
            Type shipType = TypeCache.Get("Ship");
            if (target != null && shipType != null)
            {
                try
                {
                    object onTarget = target.GetComponentInParent(shipType);
                    if (onTarget != null) return onTarget;
                }
                catch { }
            }

            Type player = TypeCache.Get("Player");
            object local = player == null ? null : AccessTools.Field(player, "m_localPlayer").GetValue(null);
            if (local == null) return null;
            FieldInfo doodadField = AccessTools.Field(player, "m_doodadController");
            object doodad = doodadField == null ? null : doodadField.GetValue(local);
            if (doodad == null) return null;
            FieldInfo shipField = AccessTools.Field(doodad.GetType(), "m_ship");
            return shipField == null ? null : shipField.GetValue(doodad);
        }

        // Name a spawn that did nothing.
        //
        // "spawn Horse" ran, the console printed nothing, and no horse appeared. The prefab table is
        // right there, so resolve the name ourselves and, when it is missing, print what does exist.
        internal static void DiagnoseSpawn(string command)
        {
            try
            {
                string[] parts = command.Split(new char[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
                if (parts.Length < 2 || !parts[0].Equals("spawn", StringComparison.OrdinalIgnoreCase)) return;
                string want = parts[1];

                Type zn = TypeCache.Get("ZNetScene");
                if (zn == null) return;
                object scene = null;
                FieldInfo sf = AccessTools.Field(zn, "m_instance") ?? AccessTools.Field(zn, "s_instance");
                if (sf != null) scene = sf.GetValue(null);
                if (scene == null)
                {
                    PropertyInfo sp = zn.GetProperty("instance", BindingFlags.Static | BindingFlags.Public);
                    if (sp != null) scene = sp.GetValue(null, null);
                }
                if (scene == null) return;

                MethodInfo get = AccessTools.Method(zn, "GetPrefab", new Type[] { typeof(string) });
                object prefab = get == null ? null : get.Invoke(scene, new object[] { want });
                if (prefab != null)
                {
                    NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "spawn '" + want + "': prefab exists");
                    return;
                }

                FieldInfo pf = AccessTools.Field(zn, "m_prefabs");
                var near = new System.Collections.Generic.List<string>();
                string token = want.Length > 4 ? want.Substring(0, 4) : want;
                if (pf != null)
                {
                    var all = pf.GetValue(scene) as System.Collections.IEnumerable;
                    if (all != null)
                    {
                        foreach (object o in all)
                        {
                            var go = o as GameObject;
                            if (go == null) continue;
                            if (go.name.IndexOf(token, StringComparison.OrdinalIgnoreCase) >= 0) near.Add(go.name);
                            if (near.Count >= 12) break;
                        }
                    }
                }
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "spawn '" + want + "': NO SUCH PREFAB. Names containing '" + token + "': "
                    + (near.Count == 0 ? "(none)" : string.Join(", ", near.ToArray())));
            }
            catch { }
        }

        internal static bool ShipSpeed(string step) { return ShipSpeed(step, null); }

        internal static bool ShipSpeed(string step, GameObject target)
        {
            try
            {
                object ship = ResolveShip(target);
                if (ship == null)
                {
                    NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                        + "sail: no ship at the pointer and not at a helm");
                    return false;
                }

                bool up = step == "faster";
                MethodInfo m = AccessTools.Method(ship.GetType(), up ? "Forward" : "Backward");
                if (m == null) return false;
                m.Invoke(ship, null);

                FieldInfo speed = AccessTools.Field(ship.GetType(), "m_speed");
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "sail " + step
                    + " -> " + (speed == null ? "?" : Convert.ToString(speed.GetValue(ship))));
                return true;
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "sail " + step + " failed: " + e.Message);
                return false;
            }
        }

        // Re-measure the player's eye height.
        //
        // WHY this exists at all: the Meta/system button's long press DOES recenter, and it does
        // fix which way the player is facing, because it moves the tracking origin. It does not fix
        // height, because VHVR caches its own measurement and keeps using it - firstPersonHeightOffset
        // (VRPlayer.cs:312) is measured once (VRPlayer.cs:1601-1604) and then added to the camera
        // position every frame at VRPlayer.cs:1188-1193 until something sets it back to null. The
        // system recenter never touches it, so a player who was sitting when the offset was taken
        // stays sunk into the ground after standing up, with the world correctly oriented around him.
        //
        // VRPlayer.RequestRecentering() (VRPlayer.cs:342-347) is the one thing that nulls it, and
        // until now it was reachable only from VHVR's own three call sites - VRManager.cs:175 and
        // EyeRotationPatch.cs:266 and :404 - none of which a player can ask for.
        //
        // Two callers now, both automatic, neither a menu entry: SystemRecenter below, when the
        // player performs a system/Meta recenter, and SitRecenter below that, when his posture
        // changes. The wrist "Reset Height" entry that used to call this was removed on 2026-08-19
        // once the operator confirmed the recenter hook fixes height by itself.
        //
        // Reflection, like every other VHVR reach in this plugin, so a flat profile with no
        // ValheimVRMod.dll still loads the assembly and simply reports the type as missing.
        internal static bool ResetHeight()
        {
            try
            {
                Type vrp = TypeCache.Get("ValheimVRMod.VRCore.VRPlayer");
                if (vrp == null)
                {
                    Warn("reset height: ValheimVRMod.VRCore.VRPlayer not present (no VR profile loaded)");
                    return false;
                }
                MethodInfo request = vrp.GetMethod("RequestRecentering", BindingFlags.Static | BindingFlags.Public);
                if (request == null)
                {
                    Warn("reset height: VRPlayer.RequestRecentering not found in this VHVR build");
                    return false;
                }

                // The offset that was WRONG is readable right now and is the only measured number
                // this call can honestly report. The replacement is taken on a later frame inside
                // VHVR's own update (VRPlayer.cs:1601-1604), so reading it here would either be
                // null or a value invented by polling - neither is worth logging.
                FieldInfo offset = vrp.GetField("firstPersonHeightOffset", BindingFlags.Static | BindingFlags.NonPublic);
                object before = null;
                try { before = offset == null ? null : offset.GetValue(null); } catch { }
                string was = offset == null ? "unreadable in this build"
                    : before == null ? "already cleared"
                    : Convert.ToSingle(before).ToString("F3") + "m";

                request.Invoke(null, null);
                Log("reset height requested: VHVR's cached firstPersonHeightOffset was " + was
                    + " and is now cleared; it is re-measured inside VHVR on a later frame"
                    + " (VRPlayer.cs:1601-1604), which this call cannot read without polling, so the"
                    + " new value is not reported here");
                return true;
            }
            catch (Exception e) { Warn("reset height failed: " + e.Message); return false; }
        }

        internal static bool ClosePanels()
        {
            int closed = 0;
            try
            {
                foreach (string name in VRGuiBridge.AdoptedNames())
                {
                    GameObject go = GameObject.Find(name);
                    if (go == null || !go.activeSelf) continue;
                    go.SetActive(false);
                    closed++;
                    Log("closed adopted canvas '" + name + "'");
                }
            }
            catch (Exception e) { Warn("canvas close failed: " + e.Message); }

            try
            {
                Type menu = TypeCache.Get("Menu");
                object inst = menu == null ? null : Instance("Menu");
                MethodInfo visible = menu == null ? null : AccessTools.Method(menu, "IsVisible");
                MethodInfo hide = menu == null ? null : AccessTools.Method(menu, "Hide");
                bool showing = visible != null && inst != null && Convert.ToBoolean(visible.Invoke(null, null));
                if (showing && hide != null) { hide.Invoke(inst, null); closed++; Log("hid the main menu"); }
            }
            catch (Exception e) { Warn("menu hide failed: " + e.Message); }

            // The console and chat windows are NOT adopted canvases, so the loop above never saw
            // them - yet an open console blocks Player.TakeInput(), which is what left the player
            // unable to move with no obvious way out.
            foreach (string owner in new[] { "Console", "Chat" })
            {
                try
                {
                    object inst = Instance(owner);
                    if (inst == null) continue;
                    Type t = TypeCache.Get("Terminal") ?? inst.GetType();
                    FieldInfo win = AccessTools.Field(t, "m_chatWindow") ?? AccessTools.Field(inst.GetType(), "m_chatWindow");
                    object value = win == null ? null : win.GetValue(inst);
                    GameObject go = value as GameObject;
                    if (go == null && value is Component) go = ((Component)value).gameObject;
                    if (go == null || !go.activeSelf) continue;
                    go.SetActive(false);
                    closed++;
                    Log("closed the " + owner.ToLowerInvariant() + " window");
                }
                catch (Exception e) { Warn("closing " + owner + " failed: " + e.Message); }
            }

            // In VR, an ACTIVE screen-space canvas is by definition not visible to the player:
            // VHVR converts the canvases it knows about to world space, so anything still rendering
            // in screen space goes to the desktop mirror only - which is exactly what was reported,
            // a mod window "rendered to the desktop but not VR" while still holding input and
            // leaving the cursor stuck.
            //
            // Only reached when the player presses Close Panel, so this is an explicit rescue rather
            // than a background policy, and every name is logged so the damage is inspectable.
            try
            {
                foreach (Canvas canvas in UnityEngine.Object.FindObjectsOfType<Canvas>())
                {
                    if (canvas == null || !canvas.gameObject.activeInHierarchy) continue;
                    if (canvas.renderMode == RenderMode.WorldSpace) continue;   // VHVR handles these
                    canvas.gameObject.SetActive(false);
                    closed++;
                    Log("deactivated screen-space canvas '" + canvas.name + "' (invisible in VR)");
                }
            }
            catch (Exception e) { Warn("screen-space sweep failed: " + e.Message); }

            bool esc = KeyPulse.Send("Escape");
            Log("close: " + closed + " panel(s) deactivated, escape pulse " + (esc ? "sent" : "unavailable"));
            return closed > 0 || esc;
        }

        // Runs a console command WITHOUT typing it.
        //
        // A VR session established that typing is not a workable path: the SteamVR keyboard opened,
        // "god" was entered, no output was visible, the command did not take effect, and the player
        // was then stuck because an open console blocks Player.TakeInput(). Two separate causes -
        // cheats were never enabled (the enabler was called with the wrong signature) and closing
        // the keyboard does not necessarily submit to Terminal.
        //
        // Terminal.TryRunCommand(string, bool, bool) executes directly, so a menu entry is both
        // more reliable and faster than a keyboard.
        // "spawn X; tame" - one button, two commands.
        //
        // Splitting these into separate wrist entries made the player press two buttons to get one
        // usable horse, and a spawned creature is not tameable in the same frame it is created, so
        // the follow-up runs a beat later from Update rather than immediately.
        private static readonly System.Collections.Generic.List<System.Collections.Generic.KeyValuePair<float, string>> _queued
            = new System.Collections.Generic.List<System.Collections.Generic.KeyValuePair<float, string>>();

        internal static bool RunCommandSequence(string spec)
        {
            string[] parts = spec.Split(';');
            bool ok = RunCommand(parts[0].Trim());
            float at = UnityEngine.Time.realtimeSinceStartup;
            for (int i = 1; i < parts.Length; i++)
            {
                string next = parts[i].Trim();
                if (next.Length == 0) continue;
                at += 0.4f;
                _queued.Add(new System.Collections.Generic.KeyValuePair<float, string>(at, next));
            }
            return ok;
        }

        // Pumped once per frame by the plugin.
        internal static void PumpQueuedCommands()
        {
            if (_queued.Count == 0) return;
            float now = UnityEngine.Time.realtimeSinceStartup;
            for (int i = _queued.Count - 1; i >= 0; i--)
            {
                if (_queued[i].Key > now) continue;
                string cmd = _queued[i].Value;
                _queued.RemoveAt(i);
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "queued command: " + cmd);
                RunCommand(cmd);
            }
        }

        internal static bool RunCommand(string command)
        {
            if (string.IsNullOrEmpty(command)) return false;
            MarkPlayerIssued();
            try
            {
                Type console = TypeCache.Get("Console");
                object term = Instance("Console");
                if (console == null || term == null)
                {
                    Warn("cmd '" + command + "': no console instance - the server may have it disabled");
                    return false;
                }

                // Cheats are gated on the console being enabled for the session; without this the
                // command parses and silently refuses.
                foreach (string enabler in new[] { "SetConsoleEnabledForThisSession", "SetConsoleEnabled" })
                {
                    MethodInfo m = AccessTools.Method(console, enabler, new Type[0])
                                   ?? AccessTools.Method(console, enabler, new[] { typeof(bool) });
                    if (m == null) continue;
                    try { m.Invoke(null, m.GetParameters().Length == 0 ? null : new object[] { true }); break; }
                    catch { }
                }

                Type terminal = TypeCache.Get("Terminal") ?? console;
                MethodInfo run = AccessTools.Method(terminal, "TryRunCommand",
                    new[] { typeof(string), typeof(bool), typeof(bool) });
                if (run == null)
                {
                    Warn("cmd '" + command + "': Terminal.TryRunCommand(string,bool,bool) not found");
                    return false;
                }
                int before = BufferLength(terminal, term);
                run.Invoke(term, new object[] { command, false, false });

                // Output goes into Terminal.m_chatBuffer, which is rendered inside the console
                // window - and that window is frequently off-view or clipped in VR. Echo the new
                // lines into the log so the result is recoverable even when it cannot be read
                // in the headset.
                string tail = BufferTail(terminal, term, before);
                Log("cmd '" + command + "' ran" + (tail.Length == 0 ? " (no output)" : " -> " + tail));
                DiagnoseSpawn(command);
            return true;
            }
            catch (Exception e) { Warn("cmd '" + command + "' failed: " + e.Message); return false; }
        }

        private static int BufferLength(Type terminal, object term)
        {
            try
            {
                var list = AccessTools.Field(terminal, "m_chatBuffer").GetValue(term) as System.Collections.IList;
                return list == null ? 0 : list.Count;
            }
            catch { return 0; }
        }

        private static string BufferTail(Type terminal, object term, int from)
        {
            try
            {
                var list = AccessTools.Field(terminal, "m_chatBuffer").GetValue(term) as System.Collections.IList;
                if (list == null || list.Count <= from) return "";
                var text = new System.Text.StringBuilder();
                for (int i = from; i < list.Count && i < from + 6; i++)
                {
                    if (text.Length > 0) text.Append(" / ");
                    text.Append(Convert.ToString(list[i]));
                }
                return text.ToString();
            }
            catch { return ""; }
        }

        // Mirrors EVERY console command's output into the BepInEx log, however it was issued -
        // typed through the VR keyboard, run by a cmd: button, or triggered by another mod.
        //
        // Terminal renders its output from m_chatBuffer inside the console window, and that window
        // is routinely clipped or off-view in VR, so the result of a command was simply unreadable.
        internal static void InstallCommandEcho(Harmony harmony)
        {
            try
            {
                Type terminal = TypeCache.Get("Terminal");
                MethodInfo run = terminal == null ? null : AccessTools.Method(terminal, "TryRunCommand",
                    new[] { typeof(string), typeof(bool), typeof(bool) });
                if (run == null)
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "Terminal.TryRunCommand not found; console output stays invisible in VR");
                    return;
                }
                harmony.Patch(run,
                    prefix: new HarmonyMethod(typeof(DirectActions).GetMethod("BeforeCommand", BindingFlags.Static | BindingFlags.NonPublic)),
                    postfix: new HarmonyMethod(typeof(DirectActions).GetMethod("AfterCommand", BindingFlags.Static | BindingFlags.NonPublic)));
                ProbeHealth.Announce("ConsoleEcho", true, "console output mirrored to this log");
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "console echo install failed: " + e.Message);
            }
        }

        private static int _bufferBefore;

        // Only commands the player issued get put on screen. ServerDevcommands registers ~50 aliases
        // through TryRunCommand at boot ("alias tool_area hammer from=<x>,<z>,<y> circle=..."), and
        // echoing each of those to MessageHud painted parameter text over the whole screen including
        // the loading screen. The log echo stays unconditional - it is free and it is what let me
        // find this - but the HUD and chat only show what was asked for.
        private static double _playerIssuedUntil;

        internal static void MarkPlayerIssued()
        {
            _playerIssuedUntil = UnityEngine.Time.realtimeSinceStartupAsDouble + 30.0;
        }

        private static bool PlayerIssued()
        {
            return UnityEngine.Time.realtimeSinceStartupAsDouble <= _playerIssuedUntil;
        }

        private static void BeforeCommand(object __instance)
        {
            _bufferBefore = BufferLength(TypeCache.Get("Terminal"), __instance);
        }

        private static void AfterCommand(object __instance, string text)
        {
            Type terminal = TypeCache.Get("Terminal");
            string tail = BufferTail(terminal, __instance, _bufferBefore);
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                + "CONSOLE '" + text + "' -> " + (tail.Length == 0 ? "(no output)" : tail));

            // The console's own window keeps a screen-space pixel rect in world space, so in VR its
            // output is clipped and effectively unreadable. Chat and MessageHud are the two text
            // surfaces VHVR is known to render correctly - chat is the path its own /cmd handler
            // already uses - so the result is echoed to both: chat for the full text and scrollback,
            // MessageHud for immediate confirmation that the command was accepted at all.
            // Valheim's `god` prints nothing to m_chatBuffer, so the honest report was
            // "god: (no output)" - which told the player nothing about whether it worked. Read the
            // resulting state back off Player instead: InGodMode/InGhostMode/IsDebugFlying are the
            // actual flags those commands toggle.
            if (!PlayerIssued()) return;
            string shown = tail.Length > 0 ? tail : CheatState();
            if (shown.Length == 0) shown = "(no output)";
            EchoToChat("> " + text, shown);
            ShowCentreMessage(text + ": " + Shorten(shown, 90));
        }

        // Reads back the cheat flags a command may have toggled, so a silent command still reports
        // an observable result rather than "(no output)".
        private static string CheatState()
        {
            try
            {
                Type player = TypeCache.Get("Player");
                object local = player == null ? null : AccessTools.Field(player, "m_localPlayer").GetValue(null);
                if (local == null) return "";
                var parts = new System.Collections.Generic.List<string>();
                foreach (var probe in new[] {
                    new[] { "InGodMode", "god" },
                    new[] { "InGhostMode", "ghost" },
                    new[] { "IsDebugFlying", "fly" } })
                {
                    MethodInfo m = AccessTools.Method(player, probe[0]);
                    if (m == null) continue;
                    try { parts.Add(probe[1] + "=" + Convert.ToBoolean(m.Invoke(local, null))); }
                    catch { }
                }
                return parts.Count == 0 ? "" : string.Join(" ", parts.ToArray());
            }
            catch { return ""; }
        }

        private static string Shorten(string value, int limit)
        {
            if (string.IsNullOrEmpty(value) || value.Length <= limit) return value;
            return value.Substring(0, limit - 1) + "\u2026";
        }

        // Appends through Terminal.AddString(string) on the CHAT instance. Chat inherits it from
        // Terminal, so the lookup is on Terminal and the invocation target is Chat.instance.
        private static void EchoToChat(params string[] lines)
        {
            try
            {
                object chat = Instance("Chat");
                Type terminal = TypeCache.Get("Terminal");
                MethodInfo add = terminal == null ? null : AccessTools.Method(terminal, "AddString", new[] { typeof(string) });
                if (chat == null || add == null) return;
                foreach (string line in lines)
                {
                    if (!string.IsNullOrEmpty(line)) add.Invoke(chat, new object[] { line });
                }
            }
            catch (Exception e) { Warn("chat echo failed: " + e.Message); }
        }

        // MessageHud.ShowMessage's trailing parameters have varied across game versions, so the
        // argument array is built from the actual ParameterInfo rather than a hardcoded arity.
        private static void ShowCentreMessage(string message)
        {
            try
            {
                Type hudType = TypeCache.Get("MessageHud");
                object hud = hudType == null ? null : Instance("MessageHud");
                if (hud == null) return;
                MethodInfo show = AccessTools.Method(hudType, "ShowMessage");
                if (show == null) return;

                Type messageType = AccessTools.Inner(hudType, "MessageType");
                if (messageType == null) return;
                object centre = Enum.Parse(messageType, "Center", true);

                ParameterInfo[] parameters = show.GetParameters();
                object[] args = new object[parameters.Length];
                for (int i = 0; i < parameters.Length; i++)
                {
                    Type t = parameters[i].ParameterType;
                    if (t == messageType) args[i] = centre;
                    else if (t == typeof(string)) args[i] = message;
                    else if (parameters[i].HasDefaultValue) args[i] = parameters[i].DefaultValue;
                    else if (t.IsValueType) args[i] = Activator.CreateInstance(t);
                    else args[i] = null;
                }
                show.Invoke(hud, args);
            }
            catch (Exception e) { Warn("hud message failed: " + e.Message); }
        }

        // Brings ONE named mod canvas into VR on demand, and shows it.
        //
        // This is canvas adoption re-introduced deliberately, which is the only responsible way to do
        // it after the default-on list caused three regressions (a window clipped half off the panel,
        // the same window parented to a wrist, and the main menu drawn tiny and duplicated). The
        // differences that matter: it runs only when the player presses a button, it touches exactly
        // the canvas named in the config, and it logs the geometry before and after.
        //
        // The panel is activated DIRECTLY rather than by pulsing the mod's toggle key, because
        // AdminQoL's "Toggle Panel Key" is F6 - shared with PlantEasily and DonegalHorizonLift on this
        // install, so a pulse would fire all three.
        internal static bool AdoptAndShow(string canvasName)
        {
            if (string.IsNullOrEmpty(canvasName)) return false;
            try
            {
                bool converted = VRGuiBridge.Adopt(canvasName);

                GameObject panel = null;
                foreach (Canvas canvas in UnityEngine.Object.FindObjectsOfType<Canvas>(true))
                {
                    if (canvas != null && canvas.name == canvasName) { panel = canvas.gameObject; break; }
                }
                if (panel == null)
                {
                    Warn("panel '" + canvasName + "' not found - the mod may create it lazily;"
                         + " open it once by its own hotkey, then use this button");
                    return false;
                }

                // Never hide it. AdminQoL's canvas ROOT is permanently active - the panel's own
                // visibility lives on a child - so toggling the root hid the whole surface and the
                // next press reported "not found". Converting and ensuring active is all this should
                // do; the mod keeps control of whether its panel is drawn.
                bool wasActive = panel.activeSelf;
                if (!wasActive) panel.SetActive(true);
                Log("panel '" + canvasName + "' ready" + (wasActive ? " (was already active)" : " (activated)")
                    + (converted ? ", converted to VR world space" : ", already registered")
                    + " - open it with its own hotkey and point the laser at it");
                return true;
            }
            catch (Exception e) { Warn("panel '" + canvasName + "' failed: " + e.Message); return false; }
        }
    }

    // The system recenter, made to reset height as well.
    //
    // The operator's Meta button long press already recenters, and he reasonably expects it to fix
    // height too. It does not, for the reason recorded on DirectActions.ResetHeight: SteamVR moves
    // the tracking origin, while VHVR keeps its own cached firstPersonHeightOffset
    // (VRPlayer.cs:1188-1193) until something nulls it. So the recenter he already performs is the
    // right trigger for our reset. It proved itself in the live session on 2026-08-19, which is
    // why the wrist "Reset Height" entry that used to back it up is gone: nothing manual is
    // needed for the case this covers, and SitRecenter below covers the case it does not.
    //
    // Whether it arrives at all is unknown and is exactly what the log line is for: a Quest long
    // press through Link may raise a standing reset, a seated reset, a chaperone change, or - if
    // the Oculus runtime handles it internally - nothing that reaches OpenVR. All three are
    // subscribed and the one that fires names itself.
    //
    // Reached by reflection like the rest of our VHVR/SteamVR access, so nothing here is a load-time
    // dependency: SteamVR_Events.System(EVREventType) lives in the game's own SteamVR.dll and is
    // the same API VHVR itself uses for a system event (Patches/TextInputPatches.cs:153). Delivery
    // is a dictionary lookup at dispatch time inside SteamVR_Render.Update, so subscribing before
    // SteamVR finishes initialising is safe.
    internal static class SystemRecenter
    {
        private static bool _installed;
        private static float _lastAt = -99f;

        private static readonly string[] Events =
        {
            "VREvent_StandingZeroPoseReset",
            "VREvent_SeatedZeroPoseReset",
            "VREvent_ChaperoneUniverseHasChanged"
        };

        internal static void Install()
        {
            if (_installed) return;
            _installed = true;   // once per session either way; a failed resolve must not be retried per frame

            Type events = TypeCache.Get("Valve.VR.SteamVR_Events");
            Type kind = TypeCache.Get("Valve.VR.EVREventType");
            MethodInfo system = events == null ? null
                : events.GetMethod("System", BindingFlags.Static | BindingFlags.Public);
            if (system == null || kind == null)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "system recenter hook: SteamVR_Events.System not found; the Meta button will"
                    + " still recenter facing, and height will then be re-measured only when the"
                    + " player's posture changes (SitRecenter below) - there is no manual entry");
                return;
            }

            // The handler is generic so this file never names Valve.VR.VREvent_t. Instantiated over
            // the event struct the listener actually takes, it matches UnityAction<VREvent_t>
            // exactly, and the source keeps its no-static-VR-references property.
            MethodInfo handler = typeof(SystemRecenter).GetMethod("OnEvent",
                BindingFlags.Static | BindingFlags.NonPublic);
            int subscribed = 0;
            foreach (string name in Events)
            {
                try
                {
                    object ev = system.Invoke(null, new object[] { Enum.Parse(kind, name) });
                    if (ev == null) continue;
                    MethodInfo listen = ev.GetType().GetMethod("Listen");
                    if (listen == null) continue;
                    Type action = listen.GetParameters()[0].ParameterType;          // UnityAction<VREvent_t>
                    Type payload = action.GetGenericArguments()[0];                  // VREvent_t
                    Delegate cb = Delegate.CreateDelegate(action, handler.MakeGenericMethod(payload));
                    listen.Invoke(ev, new object[] { cb });
                    subscribed++;
                }
                catch (Exception e)
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "system recenter hook: " + name + " not subscribed: " + e.Message);
                }
            }
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                + "system recenter hook listening on " + subscribed + " of " + Events.Length
                + " SteamVR events (standing reset, seated reset, chaperone change); a long press of"
                + " the system button will name the one it produces");

            // There is no teardown convention in this plugin to follow: nothing here unpatches or
            // unsubscribes, and Harmony patches likewise live for the process. The subscription is
            // therefore left for the session, which is why Install is idempotent.
        }

        // One argument, named by the event, so a rare event costs a compare and three field writes.
        // Coalesced: a recenter storm - the chaperone change and a zero-pose reset arrive together -
        // must not re-request per event, and RequestRecentering does its work on VHVR's next frame
        // regardless, so a second request inside the window would only duplicate the log line.
        private static void OnEvent<T>(T args)
        {
            try
            {
                float now = Time.realtimeSinceStartup;
                if (now - _lastAt < 0.5f) return;
                _lastAt = now;
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "SteamVR system recenter observed (" + Describe(args)
                    + "); resetting VHVR's cached eye height too, because the origin move alone"
                    + " fixes facing and leaves firstPersonHeightOffset stale (VRPlayer.cs:1188-1193)");
                DirectActions.ResetHeight();
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "system recenter handler failed: " + e.Message);
            }
        }

        // WHICH event fired is the whole point of the line, and it is only available off the event
        // struct's own eventType field, read reflectively for the same reason as everything else.
        private static string Describe(object args)
        {
            try
            {
                if (args == null) return "unknown event";
                FieldInfo f = args.GetType().GetField("eventType");
                if (f == null) return args.GetType().Name;
                uint raw = Convert.ToUInt32(f.GetValue(args));
                Type kind = TypeCache.Get("Valve.VR.EVREventType");
                string name = kind == null ? null : Enum.GetName(kind, Enum.ToObject(kind, raw));
                return (name ?? "event") + " = " + raw;
            }
            catch { return "event name unreadable"; }
        }
    }

    // Re-measure the player's eye height when his posture changes.
    //
    // WHY this exists: VHVR measures firstPersonHeightOffset ONCE (VRPlayer.cs:1601-1604, behind
    // the headPositionInitialized gate at :1593), then adds it to the camera every frame
    // (VRPlayer.cs:1188-1193) until something nulls it - and RequestRecentering (VRPlayer.cs:342-347)
    // is the only thing that does. On top of that measurement, a seated character gets a flat
    // -0.6/-0.7m from getHeadHeightAdjust (VRPlayer.cs:1246-1259), which is correct only if the
    // measurement was taken while the player was physically standing and he then physically sits.
    // That is exactly the operator's report: standing, then sitting physically, THEN triggering Sit
    // looked right, while triggering Sit when he was already seated left the character too high,
    // because the cached number was a standing measurement of an already-seated player. Nulling it
    // as the posture changes is the same mechanism that fixed the system recenter above, and it was
    // that fix proving itself live on 2026-08-19 which identified this one.
    internal static class SitRecenter
    {
        // Character.IsSitting() is the signal, and it is authoritative rather than a proxy: it is
        // the same predicate VHVR's own height model branches on - getHeadHeightAdjust tests it at
        // VRPlayer.cs:1249 and CheckSitRoomscale at :1903 - so it flips exactly when the number our
        // recenter corrects becomes wrong. It is also true for EVERY way a player sits: the "sit"
        // emote VHVR's roomscale path starts (VRPlayer.cs:1913), a chair or bench (IsSitting stays
        // true while attached, which is why VRPlayer.cs:1251 tests IsAttached INSIDE the IsSitting
        // branch), and an emote run from chat. Hooking our own menu entry, or VHVR's startingSit
        // flag, or IsAttached, would each cover one route and miss the others.
        //
        // Called directly rather than reflectively: IsSitting is the game's own public method in
        // assembly_valheim, which this plugin already calls this way (NeuralyzeVRFixes.cs:724 calls
        // IsAttached). The VHVR reach stays reflection-only, inside DirectActions.ResetHeight, so a
        // flat profile with no ValheimVRMod.dll still loads this file and merely logs the miss.
        private static bool _known;
        private static bool _sitting;

        // The thrash bound, two independent parts, both required.
        //
        // A chair attaches in stages - the attach point, the sitting animation and IsAttached do not
        // all land on one frame - so a raw edge can arrive more than once while a single sit settles,
        // and sitting down and standing straight back up is a normal thing to do. A recenter per
        // edge would null VHVR's offset while it was still re-measuring the previous one.
        //
        //   StableFrames - the new state must hold for five consecutive frames (69ms at 72Hz) before
        //   it counts, which swallows a flicker that goes back without deferring a real change
        //   perceptibly.
        //
        //   MinSeconds - at most one request per second. A transition inside that window still
        //   COMMITS its state and leaves the request OWED rather than dropping it, because dropping
        //   it is the bug this class exists to fix: a sit-stand burst would end with the last
        //   posture measured for the previous one. The debt is paid on the first frame the window
        //   allows, from whatever posture the player is in by then, so a burst costs one late
        //   re-measure instead of one per edge.
        private const int StableFrames = 5;
        private const float MinSeconds = 1.0f;
        private static int _stable;
        private static float _lastAt = -99f;
        private static bool _owed;

        internal static void Tick()
        {
            // lint:per-frame bounded - one IsSitting() call and a bool compare per frame; every
            // reflective step is inside ResetHeight, reached only on a settled transition.
            Player p = Player.m_localPlayer;
            if (p == null)
            {
                // Death, teleport, logout: the next sample is a baseline again, not a transition,
                // and an unpaid debt goes with it - VHVR clears headPositionInitialized on a scene
                // change itself (VRPlayer.cs:374 and :1755), so paying it later would be noise.
                _known = false;
                _stable = 0;
                _owed = false;
                return;
            }

            bool sitting;
            try { sitting = p.IsSitting(); }
            catch (Exception e)
            {
                _known = false;
                _owed = false;
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "sit posture watch: IsSitting unreadable: " + e.Message);
                return;
            }

            if (!_known)
            {
                // The first sample establishes the posture without firing: there is no transition to
                // react to, and VHVR's own measurement is fresh at that point.
                _known = true;
                _sitting = sitting;
                _stable = 0;
                return;
            }
            float now = Time.realtimeSinceStartup;

            // A settled transition is handled first, so a request made on this frame carries the
            // posture read on this frame rather than the one a deferred payment would still be
            // holding.
            if (sitting != _sitting)
            {
                if (++_stable < StableFrames) return;
                _stable = 0;
                _sitting = sitting;
                string direction = sitting ? "SEATED" : "STANDING";
                if (now - _lastAt >= MinSeconds)
                {
                    Request(direction, now, "");
                    return;
                }
                _owed = true;
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "sit posture transition: character is now " + direction
                    + "; recenter deferred - one landed less than " + MinSeconds.ToString("F1")
                    + "s ago and VHVR is still re-measuring from it");
                return;
            }
            _stable = 0;

            // Steady posture: pay a debt an earlier transition left owed, as soon as the window
            // allows, and from the CURRENT posture - the only one worth measuring, whatever route
            // the player took to reach it.
            if (_owed && now - _lastAt >= MinSeconds)
            {
                _owed = false;
                Request(_sitting ? "SEATED" : "STANDING", now, " (deferred from a transition inside"
                    + " the " + MinSeconds.ToString("F1") + "s window)");
            }
        }

        private static void Request(string direction, float now, string note)
        {
            _lastAt = now;
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                + "sit posture transition: character is now " + direction + note
                + "; re-measuring eye height, because VHVR keeps the offset it took in the previous"
                + " posture (VRPlayer.cs:1601-1604 measures once, :1188-1193 uses it every frame)."
                + " The replacement is measured inside VHVR on a later frame, so it is not reported"
                + " here; the next line reports the stale value that was cleared");
            DirectActions.ResetHeight();
        }
    }
}