using System;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace NeuralyzeVRFixes
{
    // Speed control for anything you sit on, through the game's own path.
    //
    // VHVR deletes the keyboard route for vehicle speed - "Forward" and "Backward" are in its
    // ignoredZInputs - and replaces it with a two-handed sail-pull gesture. That gesture cannot be
    // performed while one hand steers, it is undiscoverable, and it does not exist at all for a
    // horse. The result was a player on a raft who could turn but never move.
    //
    // Rather than drive each vehicle's API separately, this answers the two buttons the game already
    // reads. PlayerController.FixedUpdate reads GetButton("Forward"/"Backward") and feeds whatever
    // you are attached to, so one answer covers ships and saddles, and every mod vehicle built on
    // them, using vanilla logic rather than a parallel implementation.
    //
    // Safe against a held stick: Ship.ApplyControlls only steps when its own m_forwardPressed flag
    // is not already set, so holding the stick up raises the sail exactly one step, not ten.
    //
    // The right stick is free while seated - VHVR reads its Y axis only in build mode
    // (getPieceRefModifier and the context scroll, both gated on InPlaceMode) - which is why this
    // does not need a chord or a modifier.
    internal static class MountControls
    {
        private const float Deflection = 0.5f;

        private static MethodInfo _stickY;
        private static MethodInfo _stickX;
        private static MethodInfo _leftStickX;
        private static MethodInfo _leftStickY;
        private static PropertyInfo _vrControls;
        private static MethodInfo _isAttached;
        private static FieldInfo _localPlayer;
        private static FieldInfo _doodadField;
        private static bool _resolved;

        private static int _frame = -1;
        private static float _cachedY;
        private static float _cachedApiLeftX, _cachedApiLeftY, _cachedApiRightX, _cachedApiRightY;
        private static bool _cachedAttached;

        private static void Resolve()
        {
            if (_resolved) return;
            _resolved = true;
            try
            {
                Type vr = AccessTools.TypeByName("ValheimVRMod.VRCore.UI.VRControls");
                if (vr != null)
                {
                    _vrControls = vr.GetProperty("instance", BindingFlags.Static | BindingFlags.Public);
                    _stickY = AccessTools.Method(vr, "GetJoyRightStickY");
                    _stickX = AccessTools.Method(vr, "GetJoyRightStickX");
                    _leftStickX = AccessTools.Method(vr, "GetJoyLeftStickX");
                    _leftStickY = AccessTools.Method(vr, "GetJoyLeftStickY");
                }
                Type character = AccessTools.TypeByName("Character");
                Type player = AccessTools.TypeByName("Player");
                _isAttached = character == null ? null : AccessTools.Method(character, "IsAttached");
                _localPlayer = player == null ? null : AccessTools.Field(player, "m_localPlayer");
                _doodadField = player == null ? null : AccessTools.Field(player, "m_doodadController");

                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "mount controls: stickY=" + (_stickY != null) + " attachedProbe=" + (_isAttached != null)
                    + " - right stick Y steps vehicle speed while seated");
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "mount controls unavailable: " + e.Message);
            }
        }

        // Both reads are cached per frame: this is consulted from a button postfix that the game
        // calls dozens of times per frame, and uncached reflection on that path is exactly what cost
        // the player their frame rate twice already.
        private static void Sample()
        {
            if (_frame == Time.frameCount) return;
            _frame = Time.frameCount;
            Resolve();

            _cachedAttached = false;
            _cachedY = 0f;
            _cachedApiLeftX = _cachedApiLeftY = _cachedApiRightX = _cachedApiRightY = 0f;
            try
            {
                object p = _localPlayer == null ? null : _localPlayer.GetValue(null);
                if (p == null) return;
                object doodad = _doodadField == null ? null : _doodadField.GetValue(p);
                bool attached = _isAttached != null && Convert.ToBoolean(_isAttached.Invoke(p, null));
                _cachedAttached = doodad != null || attached;
                if (!_cachedAttached) return;   // the stick is only ours while seated

                object controls = _vrControls == null ? null : _vrControls.GetValue(null, null);
                if (controls == null || _stickY == null) return;
                _cachedY = Convert.ToSingle(_stickY.Invoke(controls, null));
                if (_leftStickX != null) _cachedApiLeftX = Convert.ToSingle(_leftStickX.Invoke(controls, null));
                if (_leftStickY != null) _cachedApiLeftY = Convert.ToSingle(_leftStickY.Invoke(controls, null));
                if (_stickX != null) _cachedApiRightX = Convert.ToSingle(_stickX.Invoke(controls, null));
                if (_stickY != null) _cachedApiRightY = Convert.ToSingle(_stickY.Invoke(controls, null));
            }
            catch { }
        }

        internal static bool Attached()
        {
            Sample();
            return _cachedAttached;
        }

        // +1 push forward, -1 pull back, 0 centred.
        // The left stick's X, which is free while riding: the legs are not walking.
        private static bool UseApiRight
        {
            get
            {
                return NeuralyzeVRFixesPlugin.HorseStick == null
                    || NeuralyzeVRFixesPlugin.HorseStick.Value != "VhvrLeft";
            }
        }

        internal static float ApiLeftX()  { Sample(); return _cachedApiLeftX; }
        internal static float ApiLeftY()  { Sample(); return _cachedApiLeftY; }
        internal static float ApiRightX() { Sample(); return _cachedApiRightX; }
        internal static float ApiRightY() { Sample(); return _cachedApiRightY; }

        internal static int DriveChannel { get { return _driveChannel; } }
        private static int _driveChannel;

        internal static void LearnDriveChannel(float appliedSpeed)
        {
            Sample();
            float dl = Mathf.Abs(Mathf.Abs(_cachedApiLeftY) - appliedSpeed);
            float dr = Mathf.Abs(Mathf.Abs(_cachedApiRightY) - appliedSpeed);
            _driveChannel = dl <= dr ? 1 : 2;
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                + "mount: VHVR-" + (_driveChannel == 1 ? "left" : "right") + " is the accelerator; the OTHER stick steers");
        }

        // The DRIVE stick's sideways axis. Established by the calibration ride: pushing the left
        // stick down stopped the horse and showed up as apiLeft, so that channel is the rider's
        // left hand, and it is also the one the game takes speed from. One stick, both jobs.
        internal static float SteeringAxis()
        {
            Sample();
            return _cachedApiLeftX;
        }

        internal static float SteerAxis()
        {
            Sample();
            return UseApiRight ? _cachedApiRightX : _cachedApiLeftX;
        }

        internal static float DriveAxis()
        {
            Sample();
            return UseApiRight ? _cachedApiRightY : _cachedApiLeftY;
        }

        // Both pairs, for the log line.
        internal static string StickReport()
        {
            Sample();
            return "apiLeft=(" + _cachedApiLeftX.ToString("F2") + "," + _cachedApiLeftY.ToString("F2")
                 + ") apiRight=(" + _cachedApiRightX.ToString("F2") + "," + _cachedApiRightY.ToString("F2") + ")";
        }

        internal static bool SteerByLook
        {
            get
            {
                return NeuralyzeVRFixesPlugin.HorseSteering != null
                    && NeuralyzeVRFixesPlugin.HorseSteering.Value == "Look";
            }
        }

        internal static int Step()
        {
            Sample();
            if (!_cachedAttached) return 0;
            if (_cachedY > Deflection) return 1;
            if (_cachedY < -Deflection) return -1;
            return 0;
        }

        private static int _lastLogged;

        // On the EDGE of a stick push, act directly as well as answering the button.
        //
        // Answering GetButton("Forward") demonstrably reached the game - the log recorded every
        // push - and the ship still did not change speed, so the consumer of that answer is not
        // doing what the bytecode suggested. Ship.Forward()/Backward() are a single RPC each with no
        // gate at all, so calling them is verifiable in a way that emulating a keypress is not.
        // The button answer stays for saddles, which have no equivalent entry point.
        internal static void Note(int step)
        {
            if (step == _lastLogged) return;
            int previous = _lastLogged;
            _lastLogged = step;
            if (step == 0 || previous != 0) return;

            string before = ShipSpeedNow();
            DirectActions.ShipSpeed(step > 0 ? "faster" : "slower");
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                + "mount speed " + (step > 0 ? "forward" : "back") + " from the right stick: "
                + before + " -> " + ShipSpeedNow());
        }

        // The ship's own speed enum, so a push that does nothing is visible as a push that did
        // nothing rather than as a push that was never seen.
        private static string ShipSpeedNow()
        {
            try
            {
                Type player = AccessTools.TypeByName("Player");
                object local = player == null ? null : AccessTools.Field(player, "m_localPlayer").GetValue(null);
                if (local == null) return "?";
                FieldInfo doodadField = AccessTools.Field(player, "m_doodadController");
                object doodad = doodadField == null ? null : doodadField.GetValue(local);
                if (doodad == null) return "not-at-a-helm";
                FieldInfo shipField = AccessTools.Field(doodad.GetType(), "m_ship");
                object ship = shipField == null ? null : shipField.GetValue(doodad);
                if (ship == null) return "no-ship";
                FieldInfo speed = AccessTools.Field(ship.GetType(), "m_speed");
                return speed == null ? "?" : Convert.ToString(speed.GetValue(ship));
            }
            catch { return "?"; }
        }
    }

    // Answers the two buttons the game reads for vehicle speed, and only while seated.
    //
    // Scoped exactly as narrowly as the fly patch: outside a seat these names are untouched, so
    // walking, running and everything else keep reading the real input.

    // Steer with the same stick that drives.
    //
    // The calibration ride settled which stick is which and this follows it rather than any API
    // name: stopping the horse with the left stick appeared on the apiLeft channel, which is also
    // the channel the drive detector matches against the applied speed. So the rider's left stick
    // both accelerates and steers, exactly like a gamepad.
    //
    // OdinHorse implements no riding logic (decompiled: stats and the saddle item only), so this is
    // a stock Sadle, which the game steers by look direction. Its own rule, copied exactly:
    //
    //     dir = normalize(lookDir)  ONLY IF (block || moveDir.z > 0.5 || run)
    //
    // Nothing here writes moveDir. Speed, stopping and the capture condition all stay the game's,
    // so a wrong guess can only fail to steer - it cannot take away a control that works.
    [HarmonyPatch]
    internal static class Sadle_Steer
    {
        private const float TurnOffsetDegrees = 70f;

        private static float _lastLog;

        private static System.Reflection.MethodBase TargetMethod()
        {
            Type sadle = AccessTools.TypeByName("Sadle");
            return sadle == null ? null : AccessTools.Method(sadle, "ApplyControlls");
        }

        // Degrees per second the mount turns while the stick is held over. A rate, not an angle:
        // the rider holds the stick until the horse points where they want, exactly like a gamepad.
        // How far off its own nose the horse is aimed while the stick is held. Small enough that
        // the animal reaches it and keeps turning; large enough to be a decisive turn, not a lean.
        internal const float SteeringLeadDegrees = 40f;
        private static float _heading;

        private static bool Prepare() { return TargetMethod() != null; }

        [HarmonyPriority(Priority.Last)]
        private static void Prefix(object __instance, Vector3 moveDir, bool run)
        {
            if (!ReferenceEquals(DirectActions.MountedController(), __instance)) return;
            Component mount = __instance as Component;
            if (mount == null) return;

            // Left stick X turns the mount; left stick Y stays the game's own speed control.
            //
            // Two things the ride logs settled. The saddle only reads lookDir while
            // moveDir.z > 0.5 || run (its own bytecode), and VHVR's move vector carries one axis
            // at a time - lean sideways and moveDir.z drops to zero. So a heading written during a
            // lean is computed and discarded unless the forward component is held up. It is held
            // only when the rider is not asking to stop, because forcing it through a pull-back is
            // what made the horse unstoppable.
            //
            // The target is a fixed lead off the horse's own nose, recomputed every frame, never
            // integrated. Integration was measured running away from the animal - gaps of 144, 153
            // and 169 degrees while the horse fell further behind - because the number advanced at
            // 150 deg/s and the animal turns far slower. It cannot chase a point that moves faster
            // than it does. A lead it can reach produces a continuous turn for as long as the stick
            // is held, which is how the right stick already turns it: the view leads by a little.
            float steer = MountControls.SteeringAxis();
            bool wantsStop = moveDir.z < -0.2f;
            bool steering = Mathf.Abs(steer) > 0.15f && !wantsStop;

            // Nothing is written here any more. Forcing the forward component to keep the game's
            // capture condition satisfied is what stopped pull-back from stopping the horse, and
            // the heading itself is applied in Sadle_ControlDir, after VHVR, where it survives.
            bool capturing = moveDir.z > 0.5f || run;

            if (Time.time - _lastLog >= 1.0f && (capturing || Mathf.Abs(steer) > 0.2f))
            {
                _lastLog = Time.time;
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "horse: moveDir.z=" + moveDir.z.ToString("F2") + " run=" + run
                    + " capturing=" + capturing + " steer=" + steer.ToString("F2")
                    + " driveChannel=" + MountControls.DriveChannel
                    + " facing=" + mount.transform.eulerAngles.y.ToString("F0")
                    + " heading=" + _heading.ToString("F0")
                    + " gap=" + Mathf.DeltaAngle(mount.transform.eulerAngles.y, _heading).ToString("F0")
                    + " " + MountControls.StickReport());
            }
        }
    }

    // The horse is not driven by anything local. Sadle.ApplyControlls packs the rider's direction
    // into an RPC - Controls(dir, speed) - and sends it to whoever OWNS the animal, which on a
    // dedicated server is not this machine. The owner acts on the vector inside that message.
    //
    // Measured, not assumed: writing m_controlDir locally persisted (each write showed up as the
    // next tick's starting value) and the horse ignored all of it, while the right stick kept
    // working - because turning your head changes lookDir before the message is built.
    //
    // So the stick is applied to the outgoing message. It rotates the direction the game is already
    // sending and never invents one: no message, no rotation, so stopping stays entirely the game's.
    [HarmonyPatch]
    internal static class Sadle_ControlsRPC
    {
        private static System.Reflection.MethodBase TargetMethod()
        {
            Type view = AccessTools.TypeByName("ZNetView");
            return view == null ? null : AccessTools.Method(view, "InvokeRPC", new Type[] { typeof(string), typeof(object[]) });
        }

        private static bool Prepare() { return TargetMethod() != null; }

        private static float _lastLog;

        private static void Prefix(string method, object[] parameters)
        {
            // Every network message in the game passes through here. A throw would break the call
            // it rode in on, so nothing in this method is allowed to escape.
            try { Apply(method, parameters); } catch { }
        }

        private static void Apply(string method, object[] args)
        {
            if (method != "Controls" || args == null || args.Length == 0) return;
            if (!(args[0] is Vector3)) return;
            if (DirectActions.MountedController() == null) return;

            float steer = MountControls.SteeringAxis();
            if (Mathf.Abs(steer) <= 0.15f) return;

            Vector3 dir = (Vector3)args[0];
            if (dir.sqrMagnitude < 0.01f) return;

            Vector3 turned = Quaternion.Euler(0f, Mathf.Sign(steer) * Sadle_Steer.SteeringLeadDegrees, 0f) * dir;
            args[0] = turned;

            if (Time.time - _lastLog >= 1.0f)
            {
                _lastLog = Time.time;
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "mount RPC steer: stick=" + steer.ToString("F2")
                    + " sent=" + dir.x.ToString("F2") + "," + dir.z.ToString("F2")
                    + " -> " + turned.x.ToString("F2") + "," + turned.z.ToString("F2"));
            }
        }
    }
}
