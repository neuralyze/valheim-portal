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
        private static MethodInfo _consoleVisible, _chatFocus, _chatInstance;
        private static MethodInfo _inventoryVisible, _menuVisible, _storeVisible, _textInputVisible;
        private static bool _resolved;
        private static int _allowed;

        internal static void Install(Harmony harmony)
        {
            Type player = AccessTools.TypeByName("Player");
            MethodInfo take = player == null ? null : AccessTools.Method(player, "TakeInput");
            if (take == null)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "Player.TakeInput not found; movement stays frozen while panels are open");
                return;
            }
            harmony.Patch(take, postfix: new HarmonyMethod(
                typeof(PanelInput).GetMethod("After", BindingFlags.Static | BindingFlags.NonPublic)));
            ProbeHealth.Announce("PanelInput", true, "movement allowed while console/chat is open");
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
            Type chat = AccessTools.TypeByName("Chat");
            if (chat != null)
            {
                PropertyInfo instance = chat.GetProperty("instance", BindingFlags.Static | BindingFlags.Public);
                _chatInstance = instance == null ? null : instance.GetGetMethod();
                _chatFocus = AccessTools.Method(chat, "HasFocus");
            }
        }

        private static MethodInfo Method(string typeName, string name)
        {
            Type t = AccessTools.TypeByName(typeName);
            return t == null ? null : AccessTools.Method(t, name);
        }

        private static bool True(MethodInfo method)
        {
            if (method == null) return false;
            try { return Convert.ToBoolean(method.Invoke(null, null)); } catch { return false; }
        }

        private static void After(ref bool __result)
        {
            if (__result) { _lastReason = null; return; }
            if (!NeuralyzeVRFixesPlugin.MoveWhilePanelOpen.Value) return;
            Resolve();

            // Name the closed gate. Movement locking up with no clue which panel did it has now cost
            // several sessions; Player.TakeInput() returns a bare bool, so the reason has to be
            // reconstructed by asking each gate individually.
            // Only reached when TakeInput() already returned false, i.e. something IS open, so the
            // reflective probes below run rarely. They must never run on a normal frame.
            string reason = True(_consoleVisible) ? "console"
                          : ChatFocused() ? "chat"
                          : True(_textInputVisible) ? "textInput"
                          : True(_inventoryVisible) ? "inventory"
                          : True(_storeVisible) ? "store"
                          : True(_menuVisible) ? "menu"
                          : "unknown";

            if (reason != _lastReason)
            {
                _lastReason = reason;
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "input was blocked by '" + reason + "'; movement re-enabled");
            }

            // Every panel, not just the text ones. In VR a panel floats in front of you and there is
            // no keyboard competing for the movement axes, so freezing the player only means they
            // cannot walk to the wrist button that closes it. "unknown" is included deliberately:
            // an unidentified gate is exactly the case that stranded the player before.
            __result = true;
            _allowed++;
        }

        private static string _lastReason;

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
}
