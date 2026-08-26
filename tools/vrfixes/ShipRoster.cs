using System;
using System.Reflection;
using System.Collections.Generic;
using HarmonyLib;
using UnityEngine;

namespace NeuralyzeVRFixes
{
    // A VR PLAYER WHO CANNOT RETAKE A HELM, AND THE LEDGER THAT LOST HIM.
    //
    // Reported over several sessions and finally MEASURED on 2026-08-26: nineteen presses out of
    // nineteen, every one identical, every gate green except one -
    //
    //   helm REFUSED #19 - still not steering 0.52s after the press
    //   At the press: znetValid=True nviewIsOwner=True inUseDistance=True encumbered=False
    //     standingOnShip=Raft(Clone) isPlayerInBoat=False shipUser=0 myPlayerID=484206363
    //   Since the press: reachedInteract=True ownerRanHere=True ownerDecided=False
    //     ownerSaw[isOwner=True isPlayerInBoat=False userBefore=0 haveValidUserBefore=False]
    //
    // The press reached ShipControlls::Interact, the ship's own ZDO owner ran RPC_RequestControl on
    // this very machine, the helm was genuinely free - and the method returned WITHOUT ANSWERING,
    // because RPC_RequestControl returns on the spot if `!m_ship.IsPlayerInBoat(playerID)`
    // (av.il:419467-419473). No attach, no "$msg_inuse", no message of any kind. A dead rudder and
    // total silence is the only shape this refusal has, which is why it survived several sessions.
    //
    // WHAT IS ACTUALLY WRONG, established from IL rather than reasoned about
    // ---------------------------------------------------------------------
    // Ship keeps TWO separate records of "this player is aboard", and they are not the same ledger:
    //
    //   * Ship::m_players, a List<Player>, appended in Ship::OnTriggerEnter (av.il:418387-418390)
    //     and removed in Ship::OnTriggerExit (av.il:418444-418448). This is the ONLY thing
    //     IsPlayerInBoat(long) consults - it walks that list comparing GetPlayerID()
    //     (av.il:418556-418575).
    //   * Character::InNumShipVolumes, a plain int incremented in the SAME OnTriggerEnter
    //     (av.il:418415-418422) and decremented in the SAME OnTriggerExit (av.il:418474-418481).
    //
    // They are driven by the same two callbacks on the same collider - OnTriggerEnter tests
    // `collider.GetComponent<Player>()` for the list and `collider.GetComponent<Character>()` for
    // the counter, and Player IS a Character, so both halves always agree about WHETHER to act. But
    // they do not agree about what an unmatched callback COSTS:
    //
    //   List<Player>.Remove on a player who is not in the list is a no-op - OnTriggerExit pops its
    //   result (av.il:418447-418448) - so the list SATURATES at "absent". The counter does not: it
    //   decrements unconditionally, straight past zero into negative.
    //
    // And Character::GetStandingOnShip tests that counter with `brtrue` (av.il:29950-29952), which
    // is TRUE for -1 as readily as for 1, then answers from m_lastGroundBody - the rigidbody the
    // ground check found under his feet, which is an entirely independent fact (av.il:29962-29970).
    //
    // So one surplus OnTriggerExit permanently offsets the counter, and from that moment on
    // GetStandingOnShip() keeps naming the raft he is genuinely standing on while m_players no
    // longer contains him. That is exactly the log above, and it is why every one of nineteen
    // attempts read the same: nothing in the game repairs the offset.
    //
    // THIS IS ALSO THE 2026-08-25 DEAD RUDDER. Ship::CustomFixedUpdate - Ship implements
    // IMonoUpdater, so this is its physics step (av.il:416010-416012) - reads
    // `if (m_players.Count == 0) { m_speed = Stop; m_rudderValue = 0f; }`
    // (av.il:416633-416643), and hands HaveControllingPlayer() to UpdateRudder
    // (av.il:416603-416615), where HaveControllingPlayer is `m_players.Count != 0 &&
    // m_shipControlls.HaveValidUser()` (av.il:418715-418722) and HaveValidUser is itself
    // `GetUser() != 0 && m_ship.IsPlayerInBoat(GetUser())` (av.il:419639-419647). An empty roster
    // therefore zeroes the rudder every step even for a player who somehow got attached - which is
    // the "gameStick.x was 0.64 and the rudder was 0.00" line from that session. One cause, both
    // symptoms.
    //
    // WHAT WAS RULED OUT, and on what evidence
    // ----------------------------------------
    // * Ship::RefreshPlayerList pruning him on a zero owner. It cannot be the remover of the LOCAL
    //   player. Its only call site is Ship::UpdateOwner (av.il:418269-418270), which is itself
    //   gated on `!IsPlayerInBoat(Player.m_localPlayer)` (av.il:418264-418267) - it runs only when
    //   the local player is ALREADY absent from the list. And Character::GetOwner returns
    //   GetZDO().GetOwner() (av.il:31462-31466), which for his own character is his own uid; the
    //   refused line even prints it, from=327815790. Not zero, and not the remover.
    // * The Physics.IgnoreCollision from AttachStart outliving AttachStop. It is never called for a
    //   helm at all: Player::AttachStart enters the IgnoreCollision loop only when
    //   `colliderRoot != null` (av.il:68333-68336) and ShipControlls::RPC_RequestRespons passes
    //   null for that argument (av.il:419571-419572). AttachStop then clears every ignore it did
    //   set and nulls the array (av.il:68620-68656). Neither half applies here.
    // * VHVR disabling, replacing or reparenting the player's collider. Grepped the whole shipped
    //   source tree: no write to Character.m_collider, no Collider.enabled on the player, and no
    //   reference to m_players, InNumShipVolumes or IsPlayerInBoat anywhere in ValheimVRMod.
    //
    // WHAT IS VR-SPECIFIC, and this last step is INFERENCE
    // ---------------------------------------------------
    // VHVR moves the local player by writing the rigidbody's position directly, every physics step
    // he moves his feet - `__instance.m_body.position += groundMovement` in
    // Character_SyncVelocity_ApplyRoomscaleVelocity.Prefix (RoomscalePatches.cs:66) - and while he
    // is standing on a ship it deliberately drops the ship's carry for that step,
    // `__instance.m_lastAttachBody = null` in Character_ApplyGroundForce_DetachIfRoomscaleMovement
    // .Prefix (RoomscalePatches.cs:44). So in VR the player's body is TELEPORTED relative to a
    // moving trigger volume, repeatedly, with the ship's carry suppressed. That regime exists in no
    // flat-screen session, and it is the only VR-specific difference in the movement path. Which
    // teleport drops which callback is not derivable from IL and is not claimed here: what IS
    // proven above is that a single dropped enter, or one surplus exit, is permanent, silent, and
    // produces precisely the measured log.
    //
    // THE REPAIR
    // ----------
    // The list is the thing that is wrong, so the list is what is corrected - once, on a helm
    // request, never per frame. A prefix on RPC_RequestControl runs before the gate that reads it,
    // so a single Add makes the same call proceed to its own occupancy check.
    //
    // It has to be a persistent Add and not a borrow-for-the-duration: as shown above,
    // Ship::CustomFixedUpdate re-reads m_players.Count every physics step and zeroes speed and rudder
    // when it is empty (av.il:416633-416643). An entry that vanished when the prefix returned would
    // grant the helm and then hold it dead, which is worse than refusing it.
    //
    // Three other repairs were considered and rejected. Forcing a trigger re-evaluation means
    // toggling somebody else's collider and hoping - fighting the engine every time he presses.
    // Correcting InNumShipVolumes fixes the wrong ledger: nothing consults it for membership.
    // Duplicating the grant ourselves - writing s_user and invoking RequestRespons from the prefix -
    // means reimplementing the occupancy rule, and a second copy of that rule is how a helm gets
    // stolen. Adding the one list entry the game's own OnTriggerExit already knows how to remove is
    // the smallest change that is also correct.
    //
    // THE GUARD, which is a real test of being aboard and not an assumption that a press implies it
    // ------------------------------------------------------------------------------------------
    // All five must hold, and any one of them failing leaves the request exactly as the game left
    // it - refused:
    //
    //   1. The requester is the LOCAL player. This machine simulates his physics and no other's; a
    //      request that arrived over the wire naming somebody else is never vouched for. Restricting
    //      it here is also what makes it impossible to grant a helm to a player we cannot see.
    //   2. IsPlayerInBoat(playerID) is currently FALSE - we act only when the ledger is provably
    //      wrong. When it is right the vanilla path already works and this method does nothing.
    //   3. GetStandingOnShip() returns THIS Ship. The rigidbody the ground check found under his
    //      feet is this hull. ShipControlls::Interact already demands the same thing before the
    //      request may leave the machine (av.il:419351-419359); it is re-read here rather than
    //      inferred from the fact that a request exists.
    //   4. His position is geometrically INSIDE one of this ship's own trigger volumes, by
    //      Collider.ClosestPoint - which returns the query point itself if and only if the point is
    //      inside a convex collider. This asks the geometry the exact question m_players is supposed
    //      to answer, instead of trusting the drifted answer.
    //   5. That geometric test was CONCLUSIVE - at least one enabled primitive or convex trigger
    //      was found on the ship to test against. If a ship's volume cannot be evaluated we decline
    //      and say so, rather than guessing.
    //
    // FOR A PLAYER STANDING ON LAND BESIDE A BOAT: nothing happens, three times over. He cannot
    // reach this method at all, because ShipControlls::Interact refuses locally when
    // GetStandingOnShip() is not this ship and never sends the RPC (av.il:419351-419359) - and
    // ShipControlls::m_maxUseRange defaults to 10f (av.il:419708-419709), so that ground test is the
    // only thing keeping a shore-stander off a helm even in vanilla. If a request for him arrived
    // anyway, guard 3 fails on the same ground test and guard 4 fails on the geometry: dry land is
    // not inside the hull's trigger box.
    //
    // FOR A SECOND PLAYER ALREADY STEERING: untouched. This prefix writes ONE list entry and
    // nothing else - it never touches ZDOVars.s_user and never invokes RequestRespons. The vanilla
    // body then runs its own occupancy test, `GetUser() != playerID && HaveValidUser()`
    // (av.il:419474-419481), and answers granted=false, which is the "$msg_inuse" path
    // (av.il:419583-419588). The repair in fact makes occupancy MORE respected than before: while
    // the roster was empty HaveValidUser() was false for the sitting helmsman too
    // (av.il:419639-419647), so a newcomer's request skipped straight to the grant and took the
    // helm out from under him.
    //
    // WHAT IT DOES NOT REACH: m_players is per-machine, and RPC_RequestControl runs on whichever
    // machine owns the ship's ZDO. If that is another client or a server, this plugin is not there
    // and the request still dies silently. His own log reads nviewIsOwner=True, which is the normal
    // case for the player sailing the boat, and there is no way to repair another machine's list
    // from here.
    internal static class ShipRoster
    {
        // A helm press is a rare event - nineteen in a whole session - so a line per press is
        // affordable, and a session that produced none is itself the answer. Capped anyway so a
        // pathological loop cannot fill his disk.
        private const int MaxLines = 24;

        private static bool _dead;
        private static int _repairs, _declines;

        private static FieldInfo _playersField;
        private static MethodInfo _isPlayerInBoat;

        // One-deep cache of the trigger volumes belonging to the last ship asked about. Re-pressing
        // the same helm is the common case, and GetComponentsInChildren allocates.
        private static Ship _triggerShip;
        private static Collider[] _triggers;

        internal static void Install(Harmony harmony)
        {
            if (NeuralyzeVRFixesPlugin.RepairShipRoster == null
                || !NeuralyzeVRFixesPlugin.RepairShipRoster.Value) return;

            MethodInfo target = AccessTools.Method(typeof(ShipControlls), "RPC_RequestControl");
            _playersField = AccessTools.Field(typeof(Ship), "m_players");
            _isPlayerInBoat = AccessTools.Method(typeof(Ship), "IsPlayerInBoat", new Type[] { typeof(long) });
            if (target == null || _playersField == null || _isPlayerInBoat == null)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "ship roster repair not installed (RPC_RequestControl=" + (target != null)
                    + " Ship.m_players=" + (_playersField != null)
                    + " IsPlayerInBoat(long)=" + (_isPlayerInBoat != null)
                    + "); a helm whose roster lost you will keep refusing in silence");
                return;
            }

            // BEFORE HelmRequestWatch's own prefix on the same method, so the diagnostic reports
            // the state the vanilla body will actually see. If it ran first it would print
            // isPlayerInBoat=False and ownerDecided=False for a request that then succeeded - the
            // lying-diagnostic bug 83daa29 was written to end. Priority is set on the HarmonyMethod
            // rather than by attribute because Harmony reads [HarmonyPriority] only on the
            // attribute-driven PatchAll path, and this is a manual Patch call.
            HarmonyMethod hm = new HarmonyMethod(
                typeof(ShipRoster).GetMethod("BeforeRequestControl", BindingFlags.Static | BindingFlags.NonPublic));
            hm.priority = Priority.First;
            harmony.Patch(target, prefix: hm);
        }

        private static void BeforeRequestControl(ShipControlls __instance, long playerID)
        {
            if (_dead) return;
            try
            {
                Ship ship = __instance == null ? null : __instance.m_ship;
                if (ship == null) return;

                // Guard 1. Cheapest, and the one that makes the rest safe: we vouch only for the
                // character whose physics this machine simulates.
                Player local = Player.m_localPlayer;
                if (local == null || local.GetPlayerID() != playerID) return;

                // Guard 2. Only when the ledger is provably wrong.
                if (Convert.ToBoolean(_isPlayerInBoat.Invoke(ship, new object[] { playerID }))) return;

                List<Player> roster = _playersField.GetValue(ship) as List<Player>;
                if (roster == null) return;
                // The list is appended without a duplicate check in vanilla (av.il:418387-418390)
                // and OnTriggerExit removes one occurrence, so a second entry for the same player
                // would outlive him stepping off. Guard 2 already implies this for the long
                // overload; Contains is the reference test the list itself uses.
                if (roster.Contains(local)) return;

                // Guard 3. The ground body under his feet is this hull.
                Ship standing = local.GetStandingOnShip();
                if (standing != ship)
                {
                    Decline(ship, playerID, "GetStandingOnShip()="
                        + (standing == null ? "NULL" : standing.name) + " is not this hull");
                    return;
                }

                // Guards 4 and 5. Inside one of this ship's own trigger volumes, by geometry.
                string inside = InsideTrigger(ship, local.transform.position);
                if (inside == null)
                {
                    Decline(ship, playerID, "his position is not inside any evaluable trigger"
                        + " volume on this ship");
                    return;
                }

                roster.Add(local);
                bool after = Convert.ToBoolean(_isPlayerInBoat.Invoke(ship, new object[] { playerID }));
                if (_repairs < MaxLines)
                {
                    _repairs++;
                    NeuralyzeVRFixesPlugin.Log.LogMessage(NeuralyzeVRFixesPlugin.Tag
                        + "ship roster REPAIRED on '" + ship.name + "': player " + playerID
                        + " was missing from Ship.m_players while standing on that hull -"
                        + " IsPlayerInBoat before=False after=" + after
                        + "; roster now holds " + roster.Count + " player(s). Proof used:"
                        + " GetStandingOnShip()='" + ship.name + "' and his feet are inside trigger '"
                        + inside + "'. RPC_RequestControl returns without answering at all when"
                        + " that test is false (av.il:419467-419473), which is the silent refusal"
                        + " with no $msg_inuse; the same empty roster also zeroes m_rudderValue"
                        + " every physics step (av.il:416633-416643).");
                }
            }
            catch (Exception e)
            {
                // One failure disables the repair for the session rather than throwing inside an
                // RPC handler every press. The helm goes back to refusing, which is the behaviour
                // being improved on, not a new break.
                _dead = true;
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "ship roster repair DISABLED for this session after: "
                    + e.GetType().Name + ": " + e.Message);
            }
        }

        private static void Decline(Ship ship, long playerID, string why)
        {
            if (_declines >= MaxLines) return;
            _declines++;
            NeuralyzeVRFixesPlugin.Log.LogMessage(NeuralyzeVRFixesPlugin.Tag
                + "ship roster NOT repaired on '" + ship.name + "': player " + playerID
                + " is absent from Ship.m_players - IsPlayerInBoat before=False, and left False -"
                + " but aboardness could not be proved, so the request was left to be refused. "
                + why + ". This line means the roster is wrong AND he may not be on that hull;"
                + " a helm press from the deck should have produced the REPAIRED line instead.");
        }

        // IS HE ACTUALLY INSIDE ONE OF THIS SHIP'S TRIGGER VOLUMES.
        //
        // Returns the name of the trigger that contains him, or null - which covers both "outside
        // every one of them" and "there was nothing here that could be evaluated". Those two are
        // deliberately the same answer: neither is proof of being aboard.
        //
        // Collider.ClosestPoint returns the query point itself when the point is inside a convex
        // collider, which makes exact containment one call and no allocation. It is only defined
        // for the primitives and for a CONVEX MeshCollider - on a concave mesh it logs an error and
        // hands back the input, which would read as "inside" for every point in the world. So the
        // filter below is a correctness requirement, not tidiness.
        private static string InsideTrigger(Ship ship, Vector3 p)
        {
            if (!ReferenceEquals(_triggerShip, ship) || _triggers == null)
            {
                _triggerShip = ship;
                _triggers = ship.GetComponentsInChildren<Collider>(true);
            }
            for (int i = 0; i < _triggers.Length; i++)
            {
                Collider c = _triggers[i];
                if (c == null || !c.isTrigger || !c.enabled || !c.gameObject.activeInHierarchy) continue;
                if (!Evaluable(c)) continue;
                if ((c.ClosestPoint(p) - p).sqrMagnitude < 1e-8f) return c.name;
            }
            return null;
        }

        private static bool Evaluable(Collider c)
        {
            if (c is BoxCollider || c is SphereCollider || c is CapsuleCollider) return true;
            MeshCollider m = c as MeshCollider;
            return m != null && m.convex;
        }
    }
}
