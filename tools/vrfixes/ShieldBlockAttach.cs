using System;
using System.Collections.Generic;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace NeuralyzeVRFixes
{
    // Restores the one line UPSTREAM DELETED BY ACCIDENT, which is why shields neither
    // block nor parry in VR.
    //
    // WHEN AND HOW IT BROKE, measured rather than reasoned:
    //
    //   Upstream commit 666124e6 "Migrate to cache weapon collision using item hash
    //   instead of object name" (brandonmousseau/vhvr-mod, 2026-05-08) removed the
    //   injected `string ___m_leftItem` parameter from PatchSetLeftHandEquipped.Postfix
    //   and swapped every remaining use for the int hash. Its EquipPatches.cs hunk:
    //
    //        case EquipType.Shield:
    //   -        meshFilter.gameObject.AddComponent<ShieldBlock>().itemName = ___m_leftItem;
    //            return;
    //
    //   and, in the same commit, ShieldBlock.cs:
    //
    //   -    public string itemName;
    //
    //   The only consumer of ___m_leftItem in that case was the `.itemName =` TAIL of the
    //   statement, and the whole statement went with it - taking AddComponent<ShieldBlock>()
    //   along. The two neighbouring cases survived the same refactor because their string
    //   use was an ARGUMENT that could be swapped for `hash` rather than an assignment:
    //   `crossbowManager.Initialize(..., hash, ...)` kept its AddComponent<WeaponBlock>()
    //   (EquipPatches.cs:455-457 today), and ButtonSecondaryAttackManager kept its
    //   AddComponent and merely lost an argument (:469). Shield was the one place where
    //   deleting the string deleted the constructor call. `itemName` was write-only, so
    //   nothing complained.
    //
    //   Confirmed from both directions with binaries, not source:
    //     * The last official release, v0.9.21 (2026-03-01, before the refactor), contains
    //       `callvirt AddComponent<class ValheimVRMod.Scripts.Block.ShieldBlock>()` at
    //       IL_0210 of that postfix, immediately followed by `stfld ShieldBlock::itemName`.
    //       The receiver is `ldloc.1` - the same MeshFilter local that the surviving
    //       BowLocalManager (IL_01ba) and ButtonSecondaryAttackManager (IL_021c) attaches
    //       use, and NOT `ldarg.3`, the item instance that CrossbowManager uses. That is
    //       the measurement behind attaching to meshFilter.gameObject below.
    //     * The ValheimVRMod.dll this profile actually loads - our own fork build, proven
    //       ours by the "[DIAG] " literal our LogUtils.LogDiagnostic added - has ZERO
    //       AddComponent sites for ShieldBlock among 161, while WeaponBlock has three and
    //       FistBlock one. That is exactly the operator's log: shieldInst=no beside
    //       weaponInst=yes and fistInst=yes.
    //
    //   Master is still broken today (compare 50d333d..master is "identical", ahead_by 0),
    //   so there is no newer upstream commit to take and nothing to wait for. The tuning
    //   issues that prove it once worked - #270, #273, #276, #278 - were all closed in
    //   January 2023, three years before the refactor orphaned the component.
    //
    //   Our fork did NOT cause this: the seven commits on top of 50d333d touch no Shield,
    //   Block or EquipPatches file, and the one shield-adjacent line they do change - the
    //   null idiom in LocalWeaponWield.nonDominantHandHasWeapon - is equivalent. Measured:
    //   the v0.9.21 release DLL, built by Roslyn from `!(leftHandItem is null)`, and our
    //   mcs build, from `leftHandItem != null`, both open the return with
    //   `Nullable<ItemType>::get_HasValue()` and branch straight to `return false`. (mcs
    //   cannot even compile `is null` - "Feature `pattern matching' cannot be used because
    //   it is not part of the C# 7.3 language specification" - which is WHY that line was
    //   rewritten.)
    //
    // WHAT THIS DOES: re-adds the component from our side, on the same GameObject upstream
    // used, with the lifetime upstream relied on. It is a one-line restoration wrapped in
    // the guards a separate plugin needs, not a new mechanism. Every consumer is already
    // present in the shipped DLL and needs no change: ShieldPatches.cs:49 and :154 OR
    // ShieldBlock.instance.isBlocking() into Humanoid.IsBlocking, :43 hands the game the
    // shield's blockTimer, :205 calls setBlocking(hit), and WeaponBlock.cs:67-75 stands
    // down its own parry "when using shield to block" - arbitration that has also been
    // dead for as long as the instance has been missing.
    internal static class ShieldBlockAttach
    {
        // How long a shield equipped before VR finished attaching stays eligible. Reached
        // only while a shield is waiting, and cleared the frame it succeeds; ten seconds at
        // 60fps is generous for a world load and still terminates on its own.
        private const int PendingFrameBudget = 600;

        private static bool _installed;
        private static bool _dead;

        private static Type _shieldBlockType;
        private static FieldInfo _instanceField;      // ShieldBlock.instance, static
        private static MethodInfo _useVrControls;     // VHVRConfig.UseVrControls()
        private static MethodInfo _nonVrPlayer;       // VHVRConfig.NonVrPlayer()
        private static MethodInfo _useGesture, _useRealistic, _useGrab;
        private static PropertyInfo _mainWeaponHand;  // VRPlayer.mainWeaponHand
        private static PropertyInfo _mainIsRight;     // VRPlayer.isRightHandMainWeaponHand
        private static FieldInfo _attachedToPlayer;   // VRPlayer.attachedToPlayer, static field
        private static FieldInfo _otherHand;          // Valve.VR.InteractionSystem.Hand.otherHand
        private static MethodInfo _getLeftItem;       // Humanoid.GetLeftItem(), protected

        // The component we last added. Held so the static can be re-pointed after Unity's
        // deferred destruction of a PREVIOUS shield nulls it - see ReassertInstance.
        private static Component _attached;
        private static int _reassertFrames;

        // A shield that was equipped before VR was ready. Held so readiness is retried
        // rather than the shield being written off for the rest of its life in the offhand.
        private static GameObject _pendingHost;
        private static string _pendingItem;
        private static int _pendingFrames;

        private static int _attachCount, _reassertCount;
        private static string _lastSkip = "none";
        private static readonly HashSet<string> _skipsLogged = new HashSet<string>();

        internal static void Install(Harmony harmony)
        {
            if (_installed) return;
            _installed = true;
            try
            {
                // Read out of assembly_valheim with monodis rather than assumed:
                //   .method public hidebysig instance default bool
                //       SetLeftHandEquipped (int32 hash, int32 'variant')
                // Its body returns false the moment hash AND variant are unchanged, then
                // Destroy()s the old m_leftItemInstance (IL_0027) and AttachItem()s a new
                // one. So __result == true means "a new item instance exists as of this
                // call", which is precisely the event upstream attached on.
                MethodInfo target = AccessTools.Method(typeof(VisEquipment), "SetLeftHandEquipped",
                    new[] { typeof(int), typeof(int) });
                if (target == null)
                {
                    Die("VisEquipment.SetLeftHandEquipped(int,int) not found");
                    return;
                }

                Resolve();
                if (_shieldBlockType == null || _instanceField == null)
                {
                    Die("ValheimVRMod.Scripts.Block.ShieldBlock not loaded - is VHVR installed?");
                    return;
                }
                if (_getLeftItem == null)
                {
                    Die("Humanoid.GetLeftItem not found - cannot tell a shield from a torch");
                    return;
                }

                // Priority.Last so we run AFTER VHVR's own postfix on the same method. Ours
                // is the last word on the left-hand item; VHVR's has already refreshed the
                // quick menus and the fist block box by then, and its own shield case is
                // the bare `return` this fix exists to fill in (EquipPatches.cs:465-466).
                harmony.Patch(target,
                    postfix: new HarmonyMethod(Self("AfterSetLeftHandEquipped")) { priority = Priority.Last });

                // LogMessage, not LogInfo: the client's BepInEx.cfg is
                // LogLevels = Fatal, Error, Warning, Message, so Info never reaches the file
                // the operator can send back.
                NeuralyzeVRFixesPlugin.Log.LogMessage(NeuralyzeVRFixesPlugin.Tag
                    + "SHIELD block attach armed - restores VHVR's ShieldBlock, deleted upstream in "
                    + "666124e6. mode=" + Mode()
                    + " instanceField=" + (_instanceField != null)
                    + " vrControls=" + (_useVrControls != null)
                    + " mainWeaponHand=" + (_mainWeaponHand != null)
                    + " attachedToPlayer=" + (_attachedToPlayer != null)
                    + ". Set [0 - Combat] RestoreShieldBlock=false to turn this off.");
                ProbeHealth.Announce("ShieldBlockAttach", true, "VisEquipment.SetLeftHandEquipped postfix");
            }
            catch (Exception e)
            {
                Die("install failed: " + e.Message);
            }
        }

        // One short field for the per-hit diagnostic line, so a missing instance is
        // explained on the same line that reports it rather than needing a second run.
        internal static string Status()
        {
            if (!_installed) return "off";
            if (_dead) return "dead:" + _lastSkip;
            return "n=" + _attachCount
                + (_reassertCount > 0 ? " repoint=" + _reassertCount : "")
                + (_pendingHost != null ? " waiting" : "")
                + (_attachCount == 0 ? " skip=" + _lastSkip : "");
        }

        // Whether a shield component is live RIGHT NOW, for callers that must not act before it
        // exists. Deliberately not derived from Status()'s text: a caller branching on a string
        // built for humans breaks the moment that wording changes, and the wording has already
        // changed once. Reads the same counters Status() reports.
        internal static bool Attached()
        {
            return _installed && !_dead && _attachCount > 0 && _pendingHost == null;
        }

        private static MethodInfo Self(string name)
        {
            return typeof(ShieldBlockAttach).GetMethod(name, BindingFlags.Static | BindingFlags.NonPublic);
        }

        private static void Resolve()
        {
            _shieldBlockType = TypeCache.Get("ValheimVRMod.Scripts.Block.ShieldBlock");
            Type cfg = TypeCache.Get("ValheimVRMod.Utilities.VHVRConfig");
            Type vrp = TypeCache.Get("ValheimVRMod.VRCore.VRPlayer");

            if (_shieldBlockType != null) _instanceField = AccessTools.Field(_shieldBlockType, "instance");
            if (cfg != null)
            {
                _useVrControls = AccessTools.Method(cfg, "UseVrControls");
                _nonVrPlayer = AccessTools.Method(cfg, "NonVrPlayer");
                // Resolved here rather than borrowed from ShieldDiagnostics: the two
                // features are independently toggled, and this one must name the mode in
                // its own attach line even when the per-hit probe is off.
                _useGesture = AccessTools.Method(cfg, "UseGestureBlock");
                _useRealistic = AccessTools.Method(cfg, "UseRealisticBlock");
                _useGrab = AccessTools.Method(cfg, "UseGrabButtonBlock");
            }
            if (vrp != null)
            {
                _mainWeaponHand = vrp.GetProperty("mainWeaponHand", BindingFlags.Static | BindingFlags.Public);
                _mainIsRight = vrp.GetProperty("isRightHandMainWeaponHand", BindingFlags.Static | BindingFlags.Public);
                _attachedToPlayer = AccessTools.Field(vrp, "attachedToPlayer");
            }
            // `.method family` on Humanoid in the shipped assembly_valheim, so it is
            // unreachable by name even though VHVR writes GetLeftItem() outright - VHVR
            // compiles against a publicised stub. Reflection is not defensive here, it is
            // the only route.
            _getLeftItem = AccessTools.Method(typeof(Humanoid), "GetLeftItem");
        }

        // VisEquipment.CustomUpdate -> UpdateVisuals -> UpdateEquipmentVisuals ->
        // SetLeftHandEquipped runs every frame, so this method does too. Everything past
        // the __result gate is reached only on an actual equip change; the idle path is two
        // reference compares and an int compare, which is the same order of cost as VHVR's
        // own postfix on this method.
        private static void AfterSetLeftHandEquipped(VisEquipment __instance, bool __result, GameObject ___m_leftItemInstance)
        {
            if (_dead) return;
            try
            {
                // lint:per-frame bounded - both counters are armed only by an equip event
                // and count themselves back to zero; neither is re-armed while pending.
                if (_pendingHost != null) RetryPending();
                if (_reassertFrames > 0) ReassertInstance();

                if (!__result) return;

                Player player = __instance.GetComponentInParent<Player>();
                if (player == null || player != Player.m_localPlayer) return;

                // The exact source EquipScript.CurrentOffHandEquipType reads
                // (EquipScript.cs:53-55) before mapping ItemType.Shield to EquipType.Shield
                // (:226-227), which is the gate upstream's attach sat behind.
                ItemDrop.ItemData left = _getLeftItem.Invoke(player, null) as ItemDrop.ItemData;
                bool isShield = left != null && left.m_shared != null
                    && left.m_shared.m_itemType == ItemDrop.ItemData.ItemType.Shield;

                if (!isShield)
                {
                    // The shield left the offhand. Its own OnDisable (ShieldBlock.cs:24-26)
                    // correctly nulls the static, and re-pointing at the corpse would be
                    // worse than nothing because ShieldPatches consults it on every hit.
                    Forget();
                    return;
                }

                if (___m_leftItemInstance == null) { Skip("noItemInstance"); return; }

                // VHVR's own line, EquipPatches.cs:392. The block box the Realistic path
                // needs is derived from THIS mesh (Block.cs:264, 286-288), and the shield
                // scaling in ShieldBlock.OnRenderObject writes THIS transform against a
                // posRef/scaleRef captured from it (ShieldBlock.cs:51-52, 95-101), so the
                // mesh child - not the item root - is the correct host. The v0.9.21 IL
                // agrees: the AddComponent receiver is the MeshFilter local.
                MeshFilter meshFilter = ___m_leftItemInstance.GetComponentInChildren<MeshFilter>();
                if (meshFilter == null) { Skip("noMeshFilter"); return; }

                GameObject host = meshFilter.gameObject;
                if (host.GetComponent(_shieldBlockType) != null) { Skip("alreadyPresent"); return; }

                // Block.Awake dereferences GetComponentInParent<Player>().transform
                // unconditionally (Block.cs:40). The item instance is parented to
                // VisEquipment.m_leftHand, which is under the player, but a null here would
                // throw inside AddComponent and leave a half-built component behind.
                if (host.GetComponentInParent<Player>() == null) { Skip("noPlayerParent"); return; }

                // Config-derived, so these are settled for the session: no point retrying.
                if (Truthy(_nonVrPlayer)) { Skip("nonVrPlayer"); return; }
                if (!Truthy(_useVrControls)) { Skip("vrControlsOff"); return; }

                string notReady = VrNotReady();
                if (notReady != null)
                {
                    // A shield can be equipped before VHVR has attached to the player -
                    // plugin update order on world load is not fixed - and Awake would then
                    // throw on VRPlayer.mainWeaponHand. Wait for readiness instead of
                    // writing the shield off until it is manually re-equipped.
                    _pendingHost = host;
                    _pendingItem = left.m_shared.m_name;
                    _pendingFrames = PendingFrameBudget;
                    Skip(notReady + ":waiting");
                    return;
                }

                Attach(host, left.m_shared.m_name);
            }
            catch (Exception e)
            {
                Die("attach failed: " + e.Message);
            }
        }

        private static void Attach(GameObject host, string itemName)
        {
            Component added = host.AddComponent(_shieldBlockType);
            if (added == null) { Skip("addComponentReturnedNull"); return; }

            _attached = added;
            // Unity runs Awake immediately but defers the previous instance's OnDisable to
            // the end of THIS frame, and SetLeftHandEquipped Destroy()s the old item
            // instance in the same call it creates the new one. A shield -> shield swap
            // therefore ends the frame with ShieldBlock.instance == null even though a live
            // component exists. Three frames of countdown covers that ordering.
            _reassertFrames = 3;
            _attachCount++;

            MeshFilter meshFilter = host.GetComponent<MeshFilter>();
            bool rightIsMain = _mainIsRight != null && (bool)_mainIsRight.GetValue(null, null);
            NeuralyzeVRFixesPlugin.Log.LogMessage(NeuralyzeVRFixesPlugin.Tag
                + "SHIELD block attached to '" + Path(host) + "'"
                + " item=" + itemName
                + " mode=" + Mode()
                + " shieldHand=" + (rightIsMain ? "left" : "right")
                + " blockBox=" + (meshFilter != null && meshFilter.sharedMesh != null
                    ? "yes" : "NO(realisticBlockBoxTestWillPassEverything)")
                + " instance=" + (Alive(_instanceField.GetValue(null)) ? "set" : "NOT SET")
                + " attach#" + _attachCount);
        }

        private static void RetryPending()
        {
            _pendingFrames--;
            if (!Alive(_pendingHost)) { _pendingHost = null; return; }
            if (_pendingFrames <= 0)
            {
                _pendingHost = null;
                Skip("vrNeverBecameReady");
                return;
            }
            if (VrNotReady() != null) return;
            if (_pendingHost.GetComponent(_shieldBlockType) != null) { _pendingHost = null; return; }

            GameObject host = _pendingHost;
            string item = _pendingItem;
            _pendingHost = null;
            Attach(host, item);
        }

        // Null when ShieldBlock.Awake can safely run, otherwise the name of the thing that
        // is not up yet. Each of these is dereferenced unconditionally by the component:
        // InitShield reads VRPlayer.mainWeaponHand.otherHand.transform (ShieldBlock.cs:53-54),
        // and CheckParryMotion reads VRPlayer.leftHandPhysicsEstimator, whose getter returns
        // null unless attachedToPlayer (VRPlayer.cs:173) - FixedUpdate would then throw every
        // third tick.
        private static string VrNotReady()
        {
            if (_attachedToPlayer == null || !(bool)_attachedToPlayer.GetValue(null)) return "notAttachedToPlayer";
            object mainHand = _mainWeaponHand == null ? null : _mainWeaponHand.GetValue(null, null);
            if (!Alive(mainHand)) return "noMainWeaponHand";
            // Resolved off the live object's type: otherHand is a public field on
            // Valve.VR.InteractionSystem.Hand, which lives in the separate SteamVR assembly
            // this plugin deliberately does not reference.
            if (_otherHand == null) _otherHand = AccessTools.Field(mainHand.GetType(), "otherHand");
            if (_otherHand == null || !Alive(_otherHand.GetValue(mainHand))) return "noOtherHand";
            return null;
        }

        private static void ReassertInstance()
        {
            _reassertFrames--;
            if (!Alive(_attached)) { _attached = null; _reassertFrames = 0; return; }
            if (Alive(_instanceField.GetValue(null))) return;

            _instanceField.SetValue(null, _attached);
            _reassertCount++;
            NeuralyzeVRFixesPlugin.Log.LogMessage(NeuralyzeVRFixesPlugin.Tag
                + "SHIELD block instance re-pointed at the live component after a shield swap"
                + " (repoint#" + _reassertCount + ")");
        }

        private static void Forget()
        {
            _attached = null;
            _reassertFrames = 0;
            _pendingHost = null;
            _pendingFrames = 0;
        }

        // Reasons are fixed strings, so the set is bounded and one line is emitted per
        // distinct cause rather than per equip.
        private static void Skip(string reason)
        {
            _lastSkip = reason;
            if (!_skipsLogged.Add(reason)) return;
            NeuralyzeVRFixesPlugin.Log.LogMessage(NeuralyzeVRFixesPlugin.Tag
                + "SHIELD block NOT attached: " + reason
                + " - shields will not block until this clears.");
        }

        private static void Die(string why)
        {
            _dead = true;
            _lastSkip = why;
            NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "SHIELD block attach " + why);
            ProbeHealth.Announce("ShieldBlockAttach", false, why);
        }

        private static string Path(GameObject go)
        {
            Transform parent = go.transform.parent;
            return parent == null ? go.name : parent.name + "/" + go.name;
        }

        private static string Mode()
        {
            if (Truthy(_useGesture)) return "Gesture";
            if (Truthy(_useRealistic)) return "Realistic";
            if (Truthy(_useGrab)) return "GrabButton";
            return "unknown";
        }

        private static bool Truthy(MethodInfo m)
        {
            try { return m != null && (bool)m.Invoke(null, null); }
            catch { return false; }
        }

        // A destroyed MonoBehaviour is not a null reference but compares equal to null
        // through UnityEngine.Object's operator, and it is exactly as unable to block as a
        // missing one.
        private static bool Alive(object o)
        {
            UnityEngine.Object u = o as UnityEngine.Object;
            return u != null;
        }
    }
}
