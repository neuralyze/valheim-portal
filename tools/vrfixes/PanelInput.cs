using System;
using System.Reflection;
using HarmonyLib;

namespace NeuralyzeVRFixes
{
    // Keeps the player mobile while a text panel is open.
    //
    // Player.TakeInput() is a chain of IsVisible() gates - Chat.HasFocus, Console.IsVisible,
    // TextInput, Menu, StoreGui, InventoryGui, InFreeFly, the barber GUI - and any one of them
    // returns false, which freezes locomotion entirely.
    //
    // On flatscreen that is correct: the keyboard is typing, so movement keys must not also drive
    // the character. In VR it is actively harmful. Typing happens on the SteamVR overlay keyboard,
    // locomotion is a thumbstick, and the menu that closes the panel is on your wrist - so a frozen
    // player has to physically turn to find a button they cannot walk toward. Reported as
    // "being frozen in place and trying to navigate back to the close panel button is too difficult".
    //
    // Only the text surfaces are overridden. Inventory, the main menu, stores, free-fly and the
    // barber still stop input, because those are cases where movement genuinely conflicts.
    internal static class PanelInput
    {
        private static MethodInfo _pieceSelection, _minimapOpen;
        private static MethodInfo _consoleVisible, _chatFocus, _chatInstance;
        private static MethodInfo _inventoryVisible, _menuVisible, _storeVisible, _textInputVisible;
        private static MethodInfo _isAttached;
        private static FieldInfo _localPlayer;
        private static bool _resolved;
        private static int _allowed;

        internal static void Install(Harmony harmony)
        {
            Type player = TypeCache.Get("Player");
            MethodInfo take = player == null ? null : AccessTools.Method(player, "TakeInput");
            if (take == null)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "Player.TakeInput not found; movement stays frozen while panels are open");
                return;
            }
            HarmonyMethod after = new HarmonyMethod(
                typeof(PanelInput).GetMethod("After", BindingFlags.Static | BindingFlags.NonPublic));
            harmony.Patch(take, postfix: after);

            // Player.TakeInput() gates actions; MOVEMENT asks PlayerController.TakeInput(bool look),
            // a separate private method with its own copy of the same gates. Patching only the
            // first unfroze actions while the stick stayed dead with the inventory or build menu
            // open - the log showed this postfix firing and the player still unable to walk.
            Type controller = TypeCache.Get("PlayerController");
            MethodInfo controllerTake = controller == null
                ? null
                : AccessTools.Method(controller, "TakeInput", new[] { typeof(bool) });
            if (controllerTake != null)
            {
                harmony.Patch(controllerTake, postfix: after);
            }
            ProbeHealth.Announce("PanelInput", true,
                "movement allowed while panels are open (Player and PlayerController gates)"
                + (controllerTake == null ? "; PlayerController.TakeInput NOT FOUND, movement stays frozen" : ""));
        }

        private static void Resolve()
        {
            if (_resolved) return;
            _resolved = true;
            _consoleVisible   = Method("Console", "IsVisible");
            _inventoryVisible = Method("InventoryGui", "IsVisible");
            _menuVisible      = Method("Menu", "IsVisible");
            _storeVisible     = Method("StoreGui", "IsVisible");
            _textInputVisible = Method("TextInput", "IsVisible");
            _pieceSelection   = Method("Hud", "IsPieceSelectionVisible");
            Type character = TypeCache.Get("Character");
            Type playerType = TypeCache.Get("Player");
            _isAttached  = character  == null ? null : AccessTools.Method(character, "IsAttached");
            _localPlayer = playerType == null ? null : AccessTools.Field(playerType, "m_localPlayer");
            _minimapOpen      = Method("Minimap", "IsOpen");
            Type chat = TypeCache.Get("Chat");
            if (chat != null)
            {
                PropertyInfo instance = chat.GetProperty("instance", BindingFlags.Static | BindingFlags.Public);
                _chatInstance = instance == null ? null : instance.GetGetMethod();
                _chatFocus = AccessTools.Method(chat, "HasFocus");
            }
        }

        private static MethodInfo Method(string typeName, string name)
        {
            Type t = TypeCache.Get(typeName);
            return t == null ? null : AccessTools.Method(t, name);
        }

        private static bool True(MethodInfo method)
        {
            if (method == null) return false;
            try { return Convert.ToBoolean(method.Invoke(null, null)); } catch { return false; }
        }

        private static void After(ref bool __result)
        {
            long _t = HookProfiler.Start();
            try { AfterBody(ref __result); } finally { HookProfiler.Stop(HookProfiler.Panel, _t); }
        }

        private static void AfterBody(ref bool __result)
        {
            if (__result) { _lastReason = null; return; }
            if (!NeuralyzeVRFixesPlugin.MoveWhilePanelOpen.Value) return;
            Resolve();

            // Name the closed gate. Movement locking up with no clue which panel did it has now cost
            // several sessions; Player.TakeInput() returns a bare bool, so the reason has to be
            // reconstructed by asking each gate individually.
            // Only reached when TakeInput() already returned false, i.e. something IS open, so the
            // reflective probes below run rarely. They must never run on a normal frame.
            // Once per frame, not once per call. TakeInput is asked 400-465 times a frame and the
            // measured cost of probing every gate that often was 9.6ms in a single frame - two
            // thirds of a 72Hz frame, spent answering the same question repeatedly.
            if (_reasonFrame == UnityEngine.Time.frameCount)
            {
                if (_frameDecline) return;
                __result = true;
                _allowed++;
                return;
            }
            _reasonFrame = UnityEngine.Time.frameCount;

            string reason = True(_consoleVisible) ? "console"
                          : ChatFocused() ? "chat"
                          : True(_textInputVisible) ? "textInput"
                          : True(_inventoryVisible) ? "inventory"
                          : True(_storeVisible) ? "store"
                          : True(_menuVisible) ? "menu"
                          : True(_pieceSelection) ? "buildMenu"
                          : True(_minimapOpen) ? "map"
                          : "unknown";

            // Two gates must stay shut, both learned from a stuck session.
            //
            // The escape menu is the player's last resort: forcing input through it left a player
            // unable to open the menu and quit at all.
            //
            // Attachment - a helm, a saddle, a chair - routes movement into the mounted controller
            // instead of the legs. Overriding that is what made a raft's rudder impossible to let go
            // of: the player was steering, and every grip kept feeding the helm.
            bool decline = reason == "menu" || Attached();

            if (reason != _lastReason)
            {
                _lastReason = reason;
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "input was blocked by '" + reason + "'; "
                    + (decline ? "left blocked deliberately" : "movement re-enabled"));
            }

            _frameDecline = decline;
            if (decline) return;

            // Every other panel. In VR a panel floats in front of you and there is no keyboard
            // competing for the movement axes, so freezing the player only means they cannot walk to
            // the wrist button that closes it. "unknown" is included deliberately: an unidentified
            // gate is exactly the case that stranded the player before.
            __result = true;
            _allowed++;
        }

        // Attached to something the game steers with the movement axes.
        private static bool Attached()
        {
            if (_localPlayer == null || _isAttached == null) return false;
            try
            {
                object p = _localPlayer.GetValue(null);
                if (p == null) return false;
                return Convert.ToBoolean(_isAttached.Invoke(p, null));
            }
            catch { return false; }
        }

        private static string _lastReason;
        private static int _reasonFrame = -1;
        private static bool _frameDecline;

        private static bool ChatFocused()
        {
            if (_chatInstance == null || _chatFocus == null) return false;
            try
            {
                object chat = _chatInstance.Invoke(null, null);
                return chat != null && Convert.ToBoolean(_chatFocus.Invoke(chat, null));
            }
            catch { return false; }
        }
    }

    // Dodge is on the same stick the hover menu uses to move its highlight, so it is suppressed for
    // as long as the list is up. Patching the player's own dodge step catches every route into it -
    // stick, gesture or button - rather than guessing which binding VHVR used this build.
    [HarmonyPatch]
    internal static class Dodge_WhileMenuOpen
    {
        private static System.Reflection.MethodBase TargetMethod()
        {
            System.Type player = TypeCache.Get("Player");
            return player == null ? null : AccessTools.Method(player, "UpdateDodge");
        }

        private static bool Prepare() { return TargetMethod() != null; }

        private static bool Prefix() { return !HoverMenu.MenuOpen; }
    }
}
