using System;
using System.Diagnostics;

namespace NeuralyzeVRFixes
{
    // Measures what OUR hooks cost, per frame, and says so.
    //
    // Three separate frame rate regressions this session were each found by reading the code and
    // recognising a mistake - name-based reflection on a per-frame path, twice, then a delegate
    // allocated per menu slot per frame. That worked, but it is guesswork dressed up as analysis:
    // the player pays a whole session to test each guess, and "still a little lower" cannot be
    // attributed to anything.
    //
    // So the hooks now time themselves. Indices rather than a dictionary, one timestamp pair per
    // call, and a single log line every few seconds naming the millisecond cost per frame of each
    // hook. A hook that is not the problem can then be cleared by measurement instead of argument.
    internal static class HookProfiler
    {
        internal const int Misc = 0;
        internal const int Hover = 1;
        internal const int Panel = 2;
        internal const int Fly = 3;
        internal const int Companion = 4;
        private const int Count = 5;

        private static readonly string[] Names = { "miscRing", "hoverMenu", "panelInput", "flyHooks", "companion" };
        private static readonly long[] _ticks = new long[Count];
        private static readonly int[] _calls = new int[Count];
        private static int _frames;
        private static float _reportedAt;
        private static readonly double TicksToMs = 1000.0 / Stopwatch.Frequency;

        internal static bool Enabled
        {
            get
            {
                return NeuralyzeVRFixesPlugin.ProfileHooks != null && NeuralyzeVRFixesPlugin.ProfileHooks.Value;
            }
        }

        internal static long Start()
        {
            return Enabled ? Stopwatch.GetTimestamp() : 0L;
        }

        internal static void Stop(int hook, long started)
        {
            if (started == 0L) return;
            _ticks[hook] += Stopwatch.GetTimestamp() - started;
            _calls[hook]++;
        }

        // Called once per frame from the plugin's own Update, which is also where the report is
        // emitted - a per-frame number is only meaningful against a frame count.
        internal static void Frame()
        {
            if (!Enabled) return;
            _frames++;
            float now = UnityEngine.Time.realtimeSinceStartup;
            if (_reportedAt == 0f) { _reportedAt = now; return; }
            if (now - _reportedAt < 5f) return;

            string line = "hook cost over " + _frames + " frames:";
            double total = 0;
            for (int i = 0; i < Count; i++)
            {
                double ms = _ticks[i] * TicksToMs;
                total += ms;
                line += " " + Names[i] + "=" + (_frames == 0 ? 0 : ms / _frames).ToString("F3") + "ms/frame"
                      + "(" + (_frames == 0 ? 0 : _calls[i] / _frames) + " calls)";
                _ticks[i] = 0;
                _calls[i] = 0;
            }
            line += " | ours total=" + (_frames == 0 ? 0 : total / _frames).ToString("F3") + "ms/frame";
            // A frame at 72Hz is 13.9ms. Anything of ours approaching a milliseconds is ours to fix;
            // anything well under it and still stuttering is not, and that is worth knowing too.
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + line);
            _frames = 0;
            _reportedAt = now;
        }
    }
}
