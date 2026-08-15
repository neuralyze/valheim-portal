using System;
using System.Collections.Generic;
using HarmonyLib;

namespace NeuralyzeVRFixes
{
    // One assembly scan per distinct type name, for the whole session.
    //
    // AccessTools.TypeByName is not cached. Its own IL does this per call: Type.GetType(name),
    // which fails for a bare name like "Player"; then AllTypes().FirstOrDefault over every type in
    // every loaded assembly; then, if that misses, a SECOND full scan with a different predicate;
    // and then a log line. Measured on this install with 111 plugins loaded: 100 ms for one
    // predicate's lookup and 1980 ms for a lookup that failed - the failing case is the expensive
    // one, and it is also the case a null check cannot avoid repeating.
    //
    // Misses are therefore cached too. A name that does not resolve is usually a mod that is not
    // installed, so without negative caching the most expensive path is the one that repeats.
    internal static class TypeCache
    {
        private static readonly Dictionary<string, Type> Resolved = new Dictionary<string, Type>();
        private static readonly object Gate = new object();

        internal static Type Get(string name)
        {
            if (string.IsNullOrEmpty(name))
            {
                return null;
            }
            lock (Gate)
            {
                Type found;
                if (Resolved.TryGetValue(name, out found))
                {
                    return found;
                }
                // lint:per-frame bounded - the only uncached call, reached once per distinct name
                found = AccessTools.TypeByName(name);
                Resolved[name] = found;
                return found;
            }
        }

        // Names looked up so far, and how many resolved, for a one-line report rather than a guess
        // about how much of the frame this used to cost.
        internal static string Report()
        {
            lock (Gate)
            {
                int hits = 0;
                foreach (KeyValuePair<string, Type> entry in Resolved)
                {
                    if (entry.Value != null)
                    {
                        hits++;
                    }
                }
                return Resolved.Count + " type name(s) resolved once, " + (Resolved.Count - hits) + " absent";
            }
        }
    }
}
