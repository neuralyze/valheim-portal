using System;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace NeuralyzeVRFixes
{
    // Records what the game decided at the instant damage arrived, for the report
    // "the shields didnt seem to block in vr".
    //
    // WHY a probe rather than another explanation: on 2026-08-20 four separate bow
    // theories were produced by reading code and every one collapsed against what the
    // player actually saw; one logged line settled it. The shield mechanism reads
    // clearly enough in source to name a cause, but the same night's guide audit found
    // twenty claims written against a default instead of the running build, so the
    // claim gets measured before it is acted on.
    //
    // The mechanism this probe is pointed at, established from the shipped assemblies:
    //
    //  1. Vanilla. Character.RPC_Damage (assembly_valheim, private, IL line 28164)
    //     runs `if (hit.m_blockable && IsBlocking()) BlockAttack(hit, attacker)`.
    //     Humanoid.IsBlocking (46745) normally reads Character.m_blocking (22769),
    //     which Player.SetControls (68930) assigns from its blockHold argument
    //     (stfld at 69144) - i.e. vanilla blocking is the held Block button and
    //     nothing else. Humanoid.BlockAttack (46263) then rejects hits from behind
    //     (Dot(hit.m_dir, transform.forward) > 0), requires GetCurrentBlocker() to
    //     be non-null, and treats m_blockTimer < 0.25 as a timed block (parry).
    //
    //  2. VHVR REPLACES layer 1 rather than feeding it. ShieldPatches.PatchIsBlocking
    //     is a prefix on Humanoid.IsBlocking that returns false, so vanilla's body
    //     never runs; the result becomes
    //     ShieldBlock.instance?.isBlocking() || WeaponBlock... || FistBlock...
    //     (ShieldPatches.cs:153-156). PatchRPCDamager's prefix calls setBlocking(hit)
    //     on the same three components first (ShieldPatches.cs:204-206), and
    //     PatchBlockAttack overwrites m_blockTimer and rewrites hit.m_dir to
    //     -transform.forward (ShieldPatches.cs:46) so vanilla's rear-hit test cannot
    //     fire. m_blocking survives only as the animation flag, assigned from
    //     ControlPatches.cs:774 (blockHold = ShieldBlock.instance?.isBlocking()).
    //
    //  3. Ours. Nothing in this plugin patches IsBlocking, BlockAttack, RPC_Damage or
    //     SetControls. CombatLatency's Character.Damage postfix only counts, and it is
    //     off in the live profile anyway.
    //
    // Each field below therefore answers one question the player can act on:
    //   shieldInst/weaponInst/fistInst - which of VHVR's three block components exist.
    //     ShieldBlock had no attach site anywhere in ValheimVRMod.dll (161 AddComponent<>
    //     sites, three for WeaponBlock, one GetOrAddComponent<FistBlock>, none for
    //     ShieldBlock), which is what the 2026-08-25 session measured as shieldInst=no on
    //     all forty lines. CAUSE FOUND: upstream commit 666124e6 (2026-05-08) deleted the
    //     only attach - v0.9.21's release DLL still has it at IL_0210 of
    //     PatchSetLeftHandEquipped - and ShieldBlockAttach.cs now restores it. So
    //     shieldInst=yes is the expected reading, and shieldAttach reports the restoration
    //     on the same line.
    //   vrBlocking - whether VHVR considered him blocking, computed exactly the way its
    //     IsBlocking prefix computes it. shieldBlocking is the shield's own half of it.
    //   blockAttack/blocked - whether the game's own block ran, and its verdict.
    //   taken vs wouldTake - the damage he actually ate against what a successful block
    //     would have left, using the game's own armour curve.
    //   shieldDot - Dot(hit.m_dir, shieldFacing), the test ShieldBlock.setBlocking
    //     applies (Gesture needs < -0.5, Realistic < -0.25), so a wrongly-held shield is
    //     distinguishable from a shield the code never consults.
    //   shieldBox - the block-box intersection Realistic mode ALSO requires, so a shield
    //     aimed correctly but standing clear of the hit ray is its own diagnosis.
    //   timerSource/parry - whose blockTimer reached the game, and whether vanilla scored
    //     it as a parry. Not the same question as whether the shield blocked.
    //   weaponAngle/allowBlocking - the WeaponBlock path, which in Gesture mode demands
    //     a two-handed weapon (LocalWeaponWield.allowBlocking, line 419).
    internal static class ShieldDiagnostics
    {
        // A session's worth of evidence without the risk of filling a log. Blocking is
        // frequent; forty lines is several fights.
        private const int MaxLines = 40;

        private static bool _installed;
        private static bool _dead;
        private static int _emitted;

        // VHVR members, all confirmed present in the shipped ValheimVRMod.dll.
        private static FieldInfo _shieldInst;      // ShieldBlock.instance
        private static FieldInfo _weaponInst;      // WeaponBlock.instance
        private static FieldInfo _fistInst;        // FistBlock.instance
        private static MethodInfo _isBlocking;     // Block.isBlocking()
        private static FieldInfo _blockTimer;      // Block.blockTimer
        private static FieldInfo _weaponWield;     // WeaponBlock.weaponWield
        private static MethodInfo _allowBlocking;  // LocalWeaponWield.allowBlocking()
        private static FieldInfo _weaponForward;   // LocalWeaponWield.weaponForward
        private static MethodInfo _useGesture, _useRealistic, _useGrab;
        private static MethodInfo _offHandType;    // EquipScript.CurrentOffHandEquipType()
        private static PropertyInfo _mainIsRight, _leftHand, _rightHand;
        // The remaining half of the answer: which individual test decided the shield's
        // verdict, and whose blockTimer the game was actually handed.
        private static MethodInfo _hitIntersects;  // Block.hitIntersectsBlockBox(HitData), protected
        private static MethodInfo _nonDominantHasWeapon; // LocalWeaponWield.nonDominantHandHasWeapon()
        private static MethodInfo _leftFist, _rightFist; // StaticObjects.leftFist()/rightFist()
        private static MethodInfo _blockingWithFist;     // FistCollision.blockingWithFist()

        // Vanilla members. GetCurrentBlocker is `.method private hidebysig` on Humanoid
        // (assembly_valheim IL 42182), so it is unreachable by name from a subclass even
        // though VHVR writes nameof(Humanoid.GetCurrentBlocker) - VHVR compiles against a
        // publicised stub. Reflection is not defensive here, it is the only route.
        private static FieldInfo _mBlocking;       // Character.m_blocking, protected
        private static MethodInfo _getBlocker;     // Humanoid.GetCurrentBlocker(), private

        // One hit's snapshot, taken in the prefix and reported in the postfix.
        private static bool _pending;
        private static float _hpBefore, _damage, _blockable, _blockPower, _shieldDot, _weaponAngle;
        // Hits that arrived before a shield component existed. Counted, and reported at 1 and
        // 25, so the probe going quiet is never unexplained.
        private static int _skippedPreAttach;
        private static bool _hitBlockable, _vrBlocking, _mBlockingWas;
        private static string _mode, _offhand, _blocker, _components, _timers, _allow;
        private static bool _blockAttackRan, _blockAttackResult;
        private static float _blockAttackTimer;
        private static bool _shieldBlocking;
        private static string _shieldBox, _timerSource;

        internal static void Install(Harmony harmony)
        {
            if (_installed) return;
            _installed = true;
            try
            {
                // Both targets read out of assembly_valheim with monodis rather than assumed:
                //   .method private hidebysig instance default void
                //       RPC_Damage (int64 sender, class HitData hit)          <- private
                //   .method family virtual hidebysig instance default bool
                //       BlockAttack (class HitData hit, class Character attacker)
                MethodInfo damage = AccessTools.Method(typeof(Character), "RPC_Damage",
                    new[] { typeof(long), typeof(HitData) });
                MethodInfo block = AccessTools.Method(typeof(Humanoid), "BlockAttack",
                    new[] { typeof(HitData), typeof(Character) });
                if (damage == null || block == null)
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "shield probe found no target: RPC_Damage=" + (damage != null)
                        + " BlockAttack=" + (block != null));
                    _dead = true;
                    return;
                }

                // Priority.Last on the prefix so it runs AFTER VHVR's own RPC_Damage
                // prefix, which is what computes each component's _blocking for this
                // hit. Priority.First on the postfix so it runs BEFORE VHVR's postfix,
                // which calls resetBlocking() and would erase the answer.
                harmony.Patch(damage,
                    prefix: new HarmonyMethod(Self("BeforeDamage")) { priority = Priority.Last },
                    postfix: new HarmonyMethod(Self("AfterDamage")) { priority = Priority.First });
                harmony.Patch(block, postfix: new HarmonyMethod(Self("AfterBlockAttack")));

                Resolve();
                // LogMessage, not LogInfo: the client's BepInEx.cfg is
                // LogLevels = Fatal, Error, Warning, Message, so Info never reaches the
                // file the player can send back.
                NeuralyzeVRFixesPlugin.Log.LogMessage(NeuralyzeVRFixesPlugin.Tag
                    + "SHIELD probe armed - one line per hit taken, up to " + MaxLines
                    + ". vhvrShieldBlock=" + (_shieldInst != null)
                    + " vhvrWeaponBlock=" + (_weaponInst != null)
                    + " vhvrFistBlock=" + (_fistInst != null)
                    + " isBlocking=" + (_isBlocking != null)
                    + " blockingMode=" + Mode()
                    + " mBlockingField=" + (_mBlocking != null));
                ProbeHealth.Announce("ShieldDiagnostics", true, "RPC_Damage and BlockAttack hooked");
            }
            catch (Exception e)
            {
                _dead = true;
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "shield probe install failed: " + e.Message);
            }
        }

        private static MethodInfo Self(string name)
        {
            return typeof(ShieldDiagnostics).GetMethod(name, BindingFlags.Static | BindingFlags.NonPublic);
        }

        private static void Resolve()
        {
            Type shield = TypeCache.Get("ValheimVRMod.Scripts.Block.ShieldBlock");
            Type weapon = TypeCache.Get("ValheimVRMod.Scripts.Block.WeaponBlock");
            Type fist = TypeCache.Get("ValheimVRMod.Scripts.Block.FistBlock");
            Type block = TypeCache.Get("ValheimVRMod.Scripts.Block.Block");
            Type wield = TypeCache.Get("ValheimVRMod.Scripts.LocalWeaponWield");
            Type cfg = TypeCache.Get("ValheimVRMod.Utilities.VHVRConfig");
            Type equip = TypeCache.Get("ValheimVRMod.Utilities.EquipScript");
            Type vrp = TypeCache.Get("ValheimVRMod.VRCore.VRPlayer");

            if (shield != null) _shieldInst = AccessTools.Field(shield, "instance");
            if (weapon != null)
            {
                _weaponInst = AccessTools.Field(weapon, "instance");
                _weaponWield = AccessTools.Field(weapon, "weaponWield");
            }
            if (fist != null) _fistInst = AccessTools.Field(fist, "instance");
            if (block != null)
            {
                _isBlocking = AccessTools.Method(block, "isBlocking");
                _blockTimer = AccessTools.Field(block, "blockTimer");
                // Typed lookup: Block also carries a static two-argument overload of the
                // same name (Block.cs:200), and the instance one is what setBlocking calls.
                _hitIntersects = AccessTools.Method(block, "hitIntersectsBlockBox", new[] { typeof(HitData) });
            }
            if (wield != null)
            {
                _allowBlocking = AccessTools.Method(wield, "allowBlocking");
                _weaponForward = AccessTools.Field(wield, "weaponForward");
                _nonDominantHasWeapon = AccessTools.Method(wield, "nonDominantHandHasWeapon");
            }
            Type statics = TypeCache.Get("ValheimVRMod.Utilities.StaticObjects");
            Type fistCol = TypeCache.Get("ValheimVRMod.Scripts.FistCollision");
            if (statics != null)
            {
                _leftFist = AccessTools.Method(statics, "leftFist");
                _rightFist = AccessTools.Method(statics, "rightFist");
            }
            if (fistCol != null) _blockingWithFist = AccessTools.Method(fistCol, "blockingWithFist");
            if (cfg != null)
            {
                _useGesture = AccessTools.Method(cfg, "UseGestureBlock");
                _useRealistic = AccessTools.Method(cfg, "UseRealisticBlock");
                _useGrab = AccessTools.Method(cfg, "UseGrabButtonBlock");
            }
            if (equip != null) _offHandType = AccessTools.Method(equip, "CurrentOffHandEquipType");
            if (vrp != null)
            {
                _mainIsRight = vrp.GetProperty("isRightHandMainWeaponHand", BindingFlags.Static | BindingFlags.Public);
                _leftHand = vrp.GetProperty("leftHand", BindingFlags.Static | BindingFlags.Public);
                _rightHand = vrp.GetProperty("rightHand", BindingFlags.Static | BindingFlags.Public);
            }
            _mBlocking = AccessTools.Field(typeof(Character), "m_blocking");
            _getBlocker = AccessTools.Method(typeof(Humanoid), "GetCurrentBlocker");
        }

        private static void BeforeDamage(Character __instance, HitData hit)
        {
            if (_dead || _emitted >= MaxLines) return;
            try
            {
                Player p = Player.m_localPlayer;
                if (p == null || __instance != (Character)p || hit == null) return;

                _pending = true;
                _blockAttackRan = false;
                _blockAttackResult = false;
                _blockAttackTimer = float.NaN;

                _hpBefore = p.GetHealth();
                _damage = hit.GetTotalDamage();
                _blockable = hit.GetTotalBlockableDamage();
                _hitBlockable = hit.m_blockable;
                _mBlockingWas = _mBlocking != null && (bool)_mBlocking.GetValue(p);

                ItemDrop.ItemData blocker = _getBlocker == null
                    ? null
                    : _getBlocker.Invoke(p, null) as ItemDrop.ItemData;
                _blocker = blocker == null ? "none" : blocker.m_shared.m_name;
                // Exactly what BlockAttack computes: GetBlockPower against the Blocking
                // skill factor. SkillType.Blocking is 6 (Skills/SkillType enum), which
                // is the constant BlockAttack loads at IL_005f.
                _blockPower = blocker == null
                    ? 0f
                    : blocker.GetBlockPower(p.GetSkillFactor(Skills.SkillType.Blocking));

                _mode = Mode();
                _offhand = Invoke(_offHandType);

                object sb = Get(_shieldInst);
                object wb = Get(_weaponInst);
                object fb = Get(_fistInst);
                _components = "shieldInst=" + (sb != null ? "yes" : "no")
                            + " weaponInst=" + (wb != null ? "yes" : "no")
                            + " fistInst=" + (fb != null ? "yes" : "no");
                // The same OR that ShieldPatches.PatchIsBlocking hands back as
                // Humanoid.IsBlocking's result. Block.isBlocking() reads config, the
                // stagger state and a mesh cooldown; it mutates nothing, so asking it
                // here cannot change the outcome being measured.
                _vrBlocking = Blocking(sb) || Blocking(wb) || Blocking(fb);
                _timers = "shieldTimer=" + Timer(sb) + " weaponTimer=" + Timer(wb) + " fistTimer=" + Timer(fb);
                // The shield alone, separated from the OR above: with an instance present,
                // "the shield said no" and "the weapon said yes" are different problems.
                _shieldBlocking = Blocking(sb);

                _shieldDot = ShieldDot(hit.m_dir);
                _weaponAngle = WeaponAngle(hit.m_dir);
                _allow = AllowBlocking(wb);
                _shieldBox = ShieldBox(sb, hit);
                _timerSource = TimerSource(wb);
            }
            catch (Exception e)
            {
                _dead = true;
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "shield probe snapshot failed: " + e.Message);
            }
        }

        // Proves the block path ran at all. If this never fires while the player is
        // being hit, IsBlocking() was false and no amount of shield-holding matters.
        private static void AfterBlockAttack(Humanoid __instance, bool __result, float ___m_blockTimer)
        {
            if (_dead || !_pending) return;
            // Every Humanoid runs BlockAttack, so this must be narrowed to us; and
            // m_localPlayer can be null on the frame a world unloads, which would throw
            // inside a postfix and take the game's damage path with it.
            Player local = Player.m_localPlayer;
            if (local == null || __instance != (Humanoid)local) return;
            _blockAttackRan = true;
            _blockAttackResult = __result;
            _blockAttackTimer = ___m_blockTimer;
        }

        private static void AfterDamage(Character __instance)
        {
            if (_dead || !_pending) return;
            _pending = false;
            try
            {
                Player p = Player.m_localPlayer;
                if (p == null || __instance != (Character)p) return;
                if (_emitted >= MaxLines) return;
                // Spend a slot only once a shield component exists. Measured 2026-08-26: all 40
                // slots of a session were consumed by dmg=0.0 events BEFORE the shield was
                // equipped - the attach line landed one log line AFTER "hit 40/40" - so the probe
                // described forty moments in which no shield existed and had nothing left for the
                // hits it was built for.
                //
                // A first attempt at this ALSO required blockable damage above zero, and that
                // suppressed every line of the next session with no trace of why, because this
                // return says nothing. Attachment is the condition that was actually being wasted;
                // a blockable=0 hit AFTER the attach is still evidence, so it is logged. The skip
                // is now counted and reported rather than silent - a probe that can go quiet for a
                // reason it will not state is worse than no probe.
                if (!ShieldBlockAttach.Attached())
                {
                    _skippedPreAttach++;
                    if (_skippedPreAttach == 1 || _skippedPreAttach == 25)
                    {
                        NeuralyzeVRFixesPlugin.Log.LogMessage(NeuralyzeVRFixesPlugin.Tag
                            + "SHIELD probe skipped " + _skippedPreAttach + " hit(s) with no shield"
                            + " component attached - shieldAttach=" + ShieldBlockAttach.Status()
                            + ". These do not spend the " + MaxLines + "-line budget.");
                    }
                    return;
                }
                _emitted++;

                float taken = _hpBefore - p.GetHealth();
                // HitData.DamageTypes.ApplyArmor(dmg, ac) is the game's own armour
                // curve (public static, assembly_valheim IL 384878), so this is what a
                // successful block WOULD have left rather than an approximation of it.
                float wouldTake = HitData.DamageTypes.ApplyArmor(_blockable, _blockPower);

                NeuralyzeVRFixesPlugin.Log.LogMessage(NeuralyzeVRFixesPlugin.Tag
                    + "SHIELD hit " + _emitted + "/" + MaxLines
                    + " dmg=" + _damage.ToString("F1")
                    + " blockable=" + _blockable.ToString("F1")
                    + " hitIsBlockable=" + _hitBlockable
                    + " blockAttack=" + (_blockAttackRan ? (_blockAttackResult ? "ran:blocked" : "ran:failed") : "never")
                    + " taken=" + taken.ToString("F1")
                    + " wouldTake=" + wouldTake.ToString("F1")
                    + " blocker=" + _blocker
                    + " blockPower=" + _blockPower.ToString("F1")
                    + " mode=" + _mode
                    + " offhand=" + _offhand
                    + " vrBlocking=" + _vrBlocking
                    + " mBlocking=" + _mBlockingWas
                    + " " + _components
                    + " shieldDot=" + Fmt(_shieldDot) + "(gestureNeeds<-0.50,realisticNeeds<-0.25)"
                    + " shieldBox=" + _shieldBox
                    + " shieldBlocking=" + _shieldBlocking
                    + " shieldAttach=" + ShieldBlockAttach.Status()
                    + " weaponAngle=" + Fmt(_weaponAngle) + "(weaponPathNeeds60-120)"
                    + " allowBlocking=" + _allow
                    + " " + _timers
                    + " timerSource=" + _timerSource
                    + " blockTimerAtBlockAttack=" + Fmt(_blockAttackTimer)
                    // Vanilla BlockAttack's own parry test: m_blockTimer < 0.25 is a timed
                    // block, everything else is a plain block. This is the field to read
                    // when the question is "did that count as a parry".
                    + " parry=" + (float.IsNaN(_blockAttackTimer)
                        ? "n/a"
                        : (_blockAttackTimer < 0.25f ? "YES" : "no"))
                    + (_emitted == MaxLines ? " [last line - raise LogShieldBlocks limit by restarting]" : ""));
            }
            catch (Exception e)
            {
                _dead = true;
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "shield probe report failed: " + e.Message);
            }
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

        private static string Invoke(MethodInfo m)
        {
            try { return m == null ? "n/a" : Convert.ToString(m.Invoke(null, null)); }
            catch { return "err"; }
        }

        private static object Get(FieldInfo f)
        {
            try
            {
                object v = f == null ? null : f.GetValue(null);
                // A destroyed MonoBehaviour compares equal to null through
                // UnityEngine.Object's operator but not as a plain reference, and a
                // destroyed component is exactly as unable to block as a missing one.
                UnityEngine.Object o = v as UnityEngine.Object;
                return o == null ? null : v;
            }
            catch { return null; }
        }

        private static bool Blocking(object instance)
        {
            try { return instance != null && _isBlocking != null && (bool)_isBlocking.Invoke(instance, null); }
            catch { return false; }
        }

        private static string Timer(object instance)
        {
            try
            {
                if (instance == null || _blockTimer == null) return "n/a";
                return Convert.ToSingle(_blockTimer.GetValue(instance)).ToString("F2");
            }
            catch { return "err"; }
        }

        private static string AllowBlocking(object weaponBlock)
        {
            try
            {
                if (weaponBlock == null || _weaponWield == null || _allowBlocking == null) return "n/a";
                object wield = _weaponWield.GetValue(weaponBlock);
                if (wield == null) return "noWield";
                return Convert.ToString((bool)_allowBlocking.Invoke(wield, null));
            }
            catch { return "err"; }
        }

        // The second half of the Realistic-mode test. ShieldBlock.setBlocking requires
        // Dot(hit.m_dir, shieldFacing) < -0.25 AND hitIntersectsBlockBox(hit)
        // (ShieldBlock.cs:64); without this field, a shield held correctly but standing
        // where the hit ray misses its mesh bounds is indistinguishable from a shield
        // pointed the wrong way.
        //
        // Only asked in the mode that uses it. Gesture and GrabButton never call it
        // (ShieldBlock.cs:58-70), and asking would CREATE the block collider those modes
        // never build - a probe must not manufacture the state it reports. In Realistic
        // mode VHVR's own RPC_Damage prefix has already run it this frame (our prefix is
        // Priority.Last), so the collider exists and re-asking costs one SphereCastAll.
        private static string ShieldBox(object shieldBlock, HitData hit)
        {
            try
            {
                if (shieldBlock == null || _hitIntersects == null) return "n/a";
                if (!Truthy(_useRealistic)) return "unused";
                return (bool)_hitIntersects.Invoke(shieldBlock, new object[] { hit }) ? "pass" : "FAIL";
            }
            catch { return "err"; }
        }

        // Which component's blockTimer PatchBlockAttack.Prefix hands the game, reproducing
        // its own if/else chain (ShieldPatches.cs:31-44). This matters because the shield
        // can block while a DIFFERENT component supplies the parry window: in Realistic
        // mode allowBlocking() is true whenever the weapon-hand grip is held
        // (LocalWeaponWield.cs:415-419), which routes the timer to WeaponBlock - and
        // WeaponBlock.CheckParryMotion then deliberately forces its own timer to
        // blockTimerNonParry while the shield is blocking (WeaponBlock.cs:67-75). So
        // holding the weapon grip suppresses the shield parry, and only this field says so.
        private static string TimerSource(object weaponBlock)
        {
            try
            {
                if (Truthy(_useGrab)) return "grabMode";
                if (FistBlocking()) return "fist";
                if (weaponBlock != null)
                {
                    bool allow = false;
                    if (_weaponWield != null && _allowBlocking != null)
                    {
                        object wield = _weaponWield.GetValue(weaponBlock);
                        if (wield != null) allow = (bool)_allowBlocking.Invoke(wield, null);
                    }
                    bool offhandWeapon = _nonDominantHasWeapon != null
                        && (bool)_nonDominantHasWeapon.Invoke(null, null);
                    if (allow || offhandWeapon) return "weapon";
                }
                return "shield";
            }
            catch { return "err"; }
        }

        private static bool FistBlocking()
        {
            if (_blockingWithFist == null) return false;
            return FistBlocking(_leftFist) || FistBlocking(_rightFist);
        }

        private static bool FistBlocking(MethodInfo accessor)
        {
            try
            {
                if (accessor == null) return false;
                object fist = accessor.Invoke(null, null);
                return fist != null && (bool)_blockingWithFist.Invoke(fist, null);
            }
            catch { return false; }
        }

        // ShieldBlock.shieldFacing, reproduced: the offhand controller's right axis,
        // negated when the right hand is the weapon hand (ShieldBlock.cs:20). Logged
        // even when ShieldBlock.instance is absent, because that is the only way to
        // tell "held wrong" apart from "never consulted".
        private static float ShieldDot(Vector3 hitDir)
        {
            try
            {
                if (_mainIsRight == null || _leftHand == null || _rightHand == null) return float.NaN;
                bool rightIsMain = (bool)_mainIsRight.GetValue(null, null);
                Component hand = (rightIsMain ? _leftHand : _rightHand).GetValue(null, null) as Component;
                if (hand == null) return float.NaN;
                Vector3 facing = rightIsMain ? -hand.transform.right : hand.transform.right;
                return Vector3.Dot(hitDir, facing);
            }
            catch { return float.NaN; }
        }

        private static float WeaponAngle(Vector3 hitDir)
        {
            try
            {
                if (_weaponForward == null) return float.NaN;
                Vector3 fwd = (Vector3)_weaponForward.GetValue(null);
                if (fwd == Vector3.zero) return float.NaN;
                return Vector3.Angle(hitDir, fwd);
            }
            catch { return float.NaN; }
        }

        private static string Fmt(float v)
        {
            return float.IsNaN(v) ? "n/a" : v.ToString("F2");
        }
    }
}
