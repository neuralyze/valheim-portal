using System;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace NeuralyzeVRFixes
{
    // Measures where a VR bow shot actually goes instead of arguing about it.
    //
    // INCIDENT 2026-08-19: the operator reported arrows leaving roughly 30 degrees off the aim
    // line in VR, and far worse than flat mode with the same bow and the same character. Four
    // explanations were offered from static reading alone and every one of them collapsed against
    // something he had already observed:
    //   1. "you are shooting at partial charge"     - he waited for the charge disc to vanish
    //   2. "that is the bow skill"                  - same character is fine in flat mode
    //   3. "the prediction line lies by one frame"  - the ARROW is off, not just the line
    //   4. "charge-scaled spread"                   - would not differ between VR and flat
    // Diagnosis by inference cost an evening and produced nothing. This probe produces one line
    // per bow shot with every quantity needed to close the question in one headset session.
    //
    // The three directions it compares, and why each one is the one it is:
    //
    //   aimVHVR  - ValheimVRMod.Scripts.BowLocalManager.aimDir, the public static field at
    //              BowLocalManager.cs:29. Written while pulling (BowLocalManager.cs:301) and
    //              re-written at release (BowLocalManager.cs:337) from getAimDir()
    //              (BowLocalManager.cs:549-551) = (arrowRest - pullObj.position).normalized.
    //              This is what VHVR BELIEVES the player is aiming at.
    //
    //   handoff  - the aimDir out-parameter of Attack.GetProjectileSpawnPoint as it stands after
    //              VHVR's prefix has written it. That prefix is
    //              BowAndFishingPatches.cs:142-157, and line 156 is literally
    //              "aimDir = BowLocalManager.aimDir;" then "return false". A POSTFIX therefore
    //              observes exactly the value vanilla will proceed with.
    //              Verified signature (monodis of assembly_valheim.dll, Attack method line 51):
    //                instance default void GetProjectileSpawnPoint(
    //                    [out] UnityEngine.Vector3& spawnPoint, [out] UnityEngine.Vector3& aimDir)
    //
    //   fly      - the direction the spawned arrow actually flies. Attack.FireProjectileBurst
    //              (verified signature: "instance default void FireProjectileBurst()", no
    //              parameters, private) instantiates the projectile with
    //                Instantiate(prefab, spawnPoint, Quaternion.LookRotation(dir))   IL_03d8-03e7
    //              and then stores that GameObject on the weapon:
    //                this.m_weapon.m_lastProjectile = go;                            IL_06bf-06c7
    //              so a postfix reaches the real arrow through __instance.m_weapon.m_lastProjectile
    //              and reads transform.forward, which IS the post-spread launch direction.
    //              Control: IProjectile.Setup was handed dir * speed (IL_069a-06ba), stored in
    //              Projectile.m_vel, so velDir is an independent second read of the same fact.
    //              If fly and velDir disagree the model of the spawn path is wrong and the whole
    //              line should be distrusted - that is the point of printing both.
    //
    // The two angles split the candidate causes cleanly:
    //   angAimHandoff  non-zero  => the HAND-OFF is wrong (VHVR's field is not what vanilla got)
    //   angAimHandoff ~0 and angAimFly ~30 => vanilla rotated the shot AFTER the hand-off, i.e.
    //                                         spread/launch angle inside FireProjectileBurst
    //
    // Which is why the spread inputs are on the same line, read from the very fields vanilla
    // uses, AFTER VHVR's FireProjectileBurst prefix (BowAndFishingPatches.cs:217-259) has had its
    // chance to zero them. Reproduced from the IL of FireProjectileBurst:
    //   accFull = m_projectileAccuracy    + ammo.m_shared.m_attack.m_projectileAccuracy
    //   accZero = m_projectileAccuracyMin + ammo.m_shared.m_attack.m_projectileAccuracyMin
    //   if (m_bowDraw) spread = Mathf.Lerp(accZero, accFull, Mathf.Pow(m_attackDrawPercentage, .5f))
    //                                                                            IL_01ec-0205
    //   dir = AngleAxis(Random.Range(-spread, spread), Cross(dir, up)) * dir      IL_03b3-03cb
    //   dir = AngleAxis(Random.Range(-spread, spread), up)             * dir      IL_03cd-03d6
    // Note m_projectileAccuracy is the spread at FULL draw and m_projectileAccuracyMin is the
    // spread at ZERO draw - the names read backwards, VHVR's own comment at
    // BowAndFishingPatches.cs:253 calls m_projectileAccuracyMin "the max spread". Two random
    // rotations of up to `spread` each means the observable deviation can reach about 1.41x it.
    //
    // vanillaDraw is the field that scales all of that: Attack.m_attackDrawPercentage, private,
    // set by vanilla's own draw tracking - NOT by BowLocalManager.attackDrawPercentage
    // (BowLocalManager.cs:314/334, exposed by GetAttackPercentage() at :564-567). The two are
    // logged side by side because a full VHVR draw sitting next to vanillaDraw=0 would explain a
    // 30-degree shot completely and is invisible to anyone reading either file alone.
    //
    // Bow skill is logged for completeness but is NOT on the bow spread path: the skill lerp at
    // IL_023a-026a is the `else` of `if (m_bowDraw)`, so bows never take it. Logged anyway so the
    // next session can retire explanation 2 with a number rather than an argument.
    internal static class BowDiagnostics
    {
        // Set by the GetProjectileSpawnPoint postfix, consumed by the FireProjectileBurst
        // postfix. Both run on the main thread inside the same call, so a plain static is
        // correct and allocates nothing.
        private static Vector3 _handoffAim, _handoffSpawn;
        private static int _handoffs, _handoffFrame = -1;

        private static bool _installed, _dead;
        // One warning per failed lookup for the whole session. A bow is fired hundreds of times
        // in an evening and a per-shot warning would bury the very lines this exists to produce.
        private static bool _warnedAim, _warnedCharge, _warnedConfig, _warnedVel, _warnedProj;

        // VHVR is reached by reflection, never by a compile-time reference, because that is how
        // every other probe in this plugin touches it (see CombatLatency.Install) and because the
        // plugin has to keep loading in a flat profile where ValheimVRMod is absent.
        private static FieldInfo _fAimDir, _fInstance, _fTimeCharge;
        private static PropertyInfo _pRealPull;
        private static MethodInfo _mAttackPct, _mUseVrControls, _mRestrict;
        // Projectile.m_vel is private (assembly_valheim: ".field private Vector3 m_vel" inside
        // class Projectile), so the control read needs reflection even though Projectile is public.
        private static FieldInfo _fProjVel;

        internal static void Install(Harmony harmony)
        {
            if (_installed) return;
            _installed = true;
            try
            {
                MethodInfo spawnPoint = AccessTools.Method(typeof(Attack), "GetProjectileSpawnPoint");
                MethodInfo burst = AccessTools.Method(typeof(Attack), "FireProjectileBurst");
                MethodInfo afterSpawn = typeof(BowDiagnostics).GetMethod("AfterSpawnPoint", BindingFlags.Static | BindingFlags.NonPublic);
                MethodInfo afterBurst = typeof(BowDiagnostics).GetMethod("AfterBurst", BindingFlags.Static | BindingFlags.NonPublic);

                // Both halves must resolve. A null patch method would otherwise throw inside the
                // catch below and disable the probe with a message that blamed the wrong thing.
                bool hooked = spawnPoint != null && afterSpawn != null && burst != null && afterBurst != null;
                if (hooked)
                {
                    harmony.Patch(spawnPoint, postfix: new HarmonyMethod(afterSpawn));
                    harmony.Patch(burst, postfix: new HarmonyMethod(afterBurst));
                }

                Type blm = TypeCache.Get("ValheimVRMod.Scripts.BowLocalManager");
                if (blm != null)
                {
                    _fAimDir = AccessTools.Field(blm, "aimDir");
                    _fInstance = AccessTools.Field(blm, "instance");
                    _mAttackPct = AccessTools.Method(blm, "GetAttackPercentage");
                    _pRealPull = blm.GetProperty("realLifePullPercentage", BindingFlags.Static | BindingFlags.Public);
                    // Declared on the BowManager base (BowManager.cs:57), public, so an inherited
                    // lookup finds it.
                    _fTimeCharge = AccessTools.Field(blm, "timeBasedChargePercentage");
                }
                Type cfg = TypeCache.Get("ValheimVRMod.Utilities.VHVRConfig");
                if (cfg != null)
                {
                    _mUseVrControls = cfg.GetMethod("UseVrControls", BindingFlags.Static | BindingFlags.Public);
                    // VHVRConfig.cs:1379 "public static String RestrictBowDrawSpeed()" - the live
                    // BowDrawRestrictType the operator has set to Full.
                    _mRestrict = cfg.GetMethod("RestrictBowDrawSpeed", BindingFlags.Static | BindingFlags.Public);
                }
                _fProjVel = AccessTools.Field(typeof(Projectile), "m_vel");

                // One record of what was actually resolved, before any of it is parsed, so a line
                // reading "n/a" later can be told apart from a line reading a real zero.
                ProbeHealth.Announce("BowDiagnostics", hooked,
                    "hooked=" + hooked
                    + " spawnPointTarget=" + (spawnPoint != null) + " burstTarget=" + (burst != null)
                    + " spawnPointPostfix=" + (afterSpawn != null) + " burstPostfix=" + (afterBurst != null)
                    + " bowLocalManager=" + (blm != null) + " aimDirField=" + (_fAimDir != null)
                    + " attackPct=" + (_mAttackPct != null) + " realPull=" + (_pRealPull != null)
                    + " timeCharge=" + (_fTimeCharge != null) + " useVrControls=" + (_mUseVrControls != null)
                    + " restrictType=" + (_mRestrict != null) + " projectileVel=" + (_fProjVel != null));
            }
            catch (Exception e)
            {
                _dead = true;
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "bow diagnostics install failed: " + e.Message);
            }
        }

        // Bows only, local player only. Every other weapon returns on the first comparison, so
        // this costs one field read on a spear throw and nothing at all when not attacking:
        // both patched methods are reached only from Attack.ProjectileAttackTriggered.
        private static bool IsLocalBow(ItemDrop.ItemData weapon, Humanoid character)
        {
            if (_dead) return false;
            if (weapon == null || weapon.m_shared == null) return false;
            if (weapon.m_shared.m_skillType != Skills.SkillType.Bows) return false;
            return character != null && character == (Humanoid)Player.m_localPlayer;
        }

        // POSTFIX on Attack.GetProjectileSpawnPoint. The out-parameters are declared `ref` and
        // never written: this patch observes the hand-off, it does not participate in it.
        //
        // Called twice per shot: Attack.ProjectileAttackTriggered calls it once for the trigger
        // effect (IL_0005) and FireProjectileBurst calls it again for the real spawn (IL_0271).
        // The later call wins, which is the one that matters, and handoffs= in the log is the
        // control on that claim - if it ever prints 1 the call graph assumed here is wrong.
        private static void AfterSpawnPoint(Attack __instance, ref Vector3 spawnPoint, ref Vector3 aimDir,
                                            ItemDrop.ItemData ___m_weapon, Humanoid ___m_character)
        {
            try
            {
                if (!IsLocalBow(___m_weapon, ___m_character)) return;
                if (Time.frameCount != _handoffFrame) { _handoffFrame = Time.frameCount; _handoffs = 0; }
                _handoffAim = aimDir;
                _handoffSpawn = spawnPoint;
                _handoffs++;
            }
            catch { }
        }

        // POSTFIX on Attack.FireProjectileBurst.
        //
        // Safe alongside VHVR's own patch on this method (BowAndFishingPatches.cs:217-259) by
        // construction, not by luck. Theirs is a PREFIX that mutates Attack and the ammo item
        // before the body runs; this is a POSTFIX that runs after the body and writes nothing:
        // no field assignment, no __result (the method is void), no __state, no ref parameter
        // (the method has no parameters at all - verified signature above). Harmony composes a
        // prefix and an unrelated postfix without ordering constraints, and because this one is
        // purely observational there is no ordering that could change what VHVR's prefix did. It
        // deliberately reads the spread fields AFTER their mutation, since the mutated values are
        // the ones vanilla actually fired with.
        private static void AfterBurst(Attack __instance, ItemDrop.ItemData ___m_weapon,
                                       ItemDrop.ItemData ___m_ammoItem, Humanoid ___m_character,
                                       float ___m_attackDrawPercentage)
        {
            try
            {
                if (!IsLocalBow(___m_weapon, ___m_character)) return;

                Vector3 aimVhvr = Vector3.zero;
                bool haveAim = false;
                if (_fAimDir != null)
                {
                    object v = _fAimDir.GetValue(null);
                    if (v is Vector3) { aimVhvr = (Vector3)v; haveAim = true; }
                }
                if (!haveAim && !_warnedAim)
                {
                    _warnedAim = true;
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "bow diagnostics cannot read BowLocalManager.aimDir; aimVHVR will read n/a");
                }

                // The arrow itself, via the weapon slot vanilla just wrote it to.
                GameObject proj = ___m_weapon.m_lastProjectile;
                Vector3 fly = Vector3.zero, velDir = Vector3.zero, projPos = Vector3.zero;
                float speed = 0f;
                string projName = "none";
                if (proj != null)
                {
                    projName = proj.name;
                    fly = proj.transform.forward;
                    projPos = proj.transform.position;
                    Projectile p = proj.GetComponent<Projectile>();
                    if (p != null && _fProjVel != null)
                    {
                        object pv = _fProjVel.GetValue(p);
                        if (pv is Vector3)
                        {
                            Vector3 vel = (Vector3)pv;
                            speed = vel.magnitude;
                            velDir = speed > 0f ? vel / speed : Vector3.zero;
                        }
                    }
                    else if (!_warnedVel)
                    {
                        _warnedVel = true;
                        NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                            + "bow diagnostics cannot read Projectile.m_vel; velDir/speed will read 0"
                            + " (component=" + (p == null ? "notProjectile" : "present") + ")");
                    }
                }
                else if (!_warnedProj)
                {
                    _warnedProj = true;
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "bow diagnostics found no m_lastProjectile; fly direction unmeasurable");
                }

                // Charge context. vhvrDraw is VHVR's own accepted draw, pull the physical draw,
                // timeCharge the vanilla-time charge; min(physical, time) is what VHVR commits to
                // (BowLocalManager.cs:363), and vanillaDraw is the field the spread lerp reads.
                string vhvrDraw = "n/a", pull = "n/a", timeCharge = "n/a";
                object blmInstance = _fInstance != null ? _fInstance.GetValue(null) : null;
                if (blmInstance != null)
                {
                    if (_mAttackPct != null)
                        vhvrDraw = Convert.ToSingle(_mAttackPct.Invoke(blmInstance, null)).ToString("F3");
                    if (_fTimeCharge != null)
                        timeCharge = Convert.ToSingle(_fTimeCharge.GetValue(blmInstance)).ToString("F3");
                }
                if (_pRealPull != null)
                    pull = Convert.ToSingle(_pRealPull.GetValue(null, null)).ToString("F3");
                if (vhvrDraw == "n/a" && !_warnedCharge)
                {
                    _warnedCharge = true;
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "bow diagnostics cannot read VHVR draw percentage (instance="
                        + (blmInstance != null) + "); vhvrDraw will read n/a");
                }

                string vrControls = "n/a", restrict = "n/a";
                if (_mUseVrControls != null)
                    vrControls = Convert.ToBoolean(_mUseVrControls.Invoke(null, null)).ToString();
                if (_mRestrict != null)
                    restrict = Convert.ToString(_mRestrict.Invoke(null, null));
                if (vrControls == "n/a" && !_warnedConfig)
                {
                    _warnedConfig = true;
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "bow diagnostics cannot read VHVRConfig; vrControls/restrict will read n/a");
                }

                float accFull = __instance.m_projectileAccuracy;
                float accZero = __instance.m_projectileAccuracyMin;
                if (___m_ammoItem != null && ___m_ammoItem.m_shared != null && ___m_ammoItem.m_shared.m_attack != null)
                {
                    accFull += ___m_ammoItem.m_shared.m_attack.m_projectileAccuracy;
                    accZero += ___m_ammoItem.m_shared.m_attack.m_projectileAccuracyMin;
                }
                float spread = __instance.m_bowDraw
                    ? Mathf.Lerp(accZero, accFull, Mathf.Pow(___m_attackDrawPercentage, 0.5f))
                    : accFull;

                float bowsSkill = -1f;
                Player lp = Player.m_localPlayer;
                if (lp != null) bowsSkill = lp.GetSkillFactor(Skills.SkillType.Bows);

                // One line, Info level, read from a text log after the session - never watched live.
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "BOWSHOT vrControls=" + vrControls
                    + " restrict=" + restrict
                    + " handoffs=" + _handoffs
                    + " aimVHVR=" + (haveAim ? V(aimVhvr) : "n/a")
                    + " handoff=" + V(_handoffAim)
                    + " fly=" + V(fly)
                    + " velDir=" + V(velDir)
                    + " angAimFly=" + (haveAim ? Ang(aimVhvr, fly) : "n/a")
                    + " angAimHandoff=" + (haveAim ? Ang(aimVhvr, _handoffAim) : "n/a")
                    + " angFlyVel=" + Ang(fly, velDir)
                    + " angHandoffFly=" + Ang(_handoffAim, fly)
                    + " vhvrDraw=" + vhvrDraw
                    + " pull=" + pull
                    + " timeCharge=" + timeCharge
                    + " vanillaDraw=" + ___m_attackDrawPercentage.ToString("F3")
                    + " bowsSkill=" + (bowsSkill >= 0f ? bowsSkill.ToString("F3") : "n/a")
                    + " spread=" + spread.ToString("F2")
                    + " spreadAtFull=" + accFull.ToString("F2")
                    + " spreadAtZero=" + accZero.ToString("F2")
                    + " bowDraw=" + __instance.m_bowDraw
                    + " skillAcc=" + __instance.m_skillAccuracy
                    + " launchAngle=" + __instance.m_launchAngle.ToString("F2")
                    + " charFacing=" + __instance.m_useCharacterFacing
                    + " projectiles=" + __instance.m_projectiles
                    + " speed=" + speed.ToString("F2")
                    + " proj=" + projName
                    // Control on projectile identity: the arrow must have been instantiated AT the
                    // handed-off spawn point (IL_03d8 passes it straight to Instantiate). A large
                    // spawnDelta means m_lastProjectile is not this shot's arrow and fly= is junk.
                    + " spawnDelta=" + (proj != null ? (projPos - _handoffSpawn).magnitude.ToString("F3") : "n/a")
                    + " frameGap=" + (Time.frameCount - _handoffFrame));
            }
            catch (Exception e)
            {
                // A probe must never break the shot it is measuring.
                _dead = true;
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "bow diagnostics disabled after error: " + e.Message);
            }
        }

        private static string V(Vector3 v)
        {
            return "(" + v.x.ToString("F3") + "," + v.y.ToString("F3") + "," + v.z.ToString("F3") + ")";
        }

        // Vector3.Angle returns 0 for a zero-length input rather than NaN, so an unmeasured
        // direction shows as 0.00 beside its own "n/a" or (0.000,0.000,0.000) and cannot be
        // mistaken for a measured alignment.
        private static string Ang(Vector3 a, Vector3 b)
        {
            return Vector3.Angle(a, b).ToString("F2");
        }
    }
}
