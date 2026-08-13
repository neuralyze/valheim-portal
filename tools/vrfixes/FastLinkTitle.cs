using System;
using UnityEngine;
using UnityEngine.UI;

namespace NeuralyzeVRFixes
{
    // Hide FastLink's title.
    //
    // The panel has one scale setting for the whole thing, so making the server names readable also
    // enlarged a title that was already the biggest element - the mismatch got scaled rather than
    // fixed, and on the character-creation screen the title sits over the character.
    //
    // The title is found by its text, not by a path through the hierarchy. A child index or an
    // object name is a guess that breaks silently on the mod's next release; the word "FastLink"
    // rendered in the panel is the thing being removed, so that is what to look for.
    //
    // One consequence, stated because it cannot be undone from in-game: the title is the panel's
    // drag handle. With it hidden the panel can only be moved by editing Position of the UI in
    // Azumatt.FastLink.cfg.
    internal static class FastLinkTitle
    {
        private const string TitleText = "FastLink";

        private static float _nextLook;
        private static bool _done;
        private static bool _dead;
        private static int _looks;

        internal static void Tick()
        {
            if (_dead || _done) return;
            if (Time.realtimeSinceStartup < _nextLook) return;
            // Once a second while the panel does not exist yet: it is built on the start screen and
            // rebuilt between scenes, and polling a name lookup every frame to catch that would cost
            // more than the title does.
            _nextLook = Time.realtimeSinceStartup + 1f;

            try
            {
                _looks++;

                // No root object by name. Two builds were spent on a name taken from a string inside
                // the mod's assembly that turned out not to be the GameObject's name, and each failure
                // was silent. Every label in the scene is examined instead, including inactive ones,
                // which is affordable because it stops the moment it succeeds.
                int hidden = 0;
                // Each candidate is examined on its own. FindObjectsOfTypeAll returns assets and
                // half-destroyed objects alongside live ones, and one of them threw - which killed
                // the whole sweep and disabled the instrument, because the try sat outside the loop.
                foreach (Text label in Resources.FindObjectsOfTypeAll<Text>())
                {
                    try
                    {
                        if (label == null || label.gameObject == null || !Matches(label.text)) continue;
                        if (!label.gameObject.scene.IsValid()) continue;   // an asset, not the live panel
                        label.gameObject.SetActive(false);
                        hidden++;
                    }
                    catch { }
                }

                Type tmp = Type.GetType("TMPro.TextMeshProUGUI, Unity.TextMeshPro", false)
                        ?? Type.GetType("TMPro.TextMeshProUGUI, TextMeshPro", false);
                if (tmp != null)
                {
                    var property = tmp.GetProperty("text");
                    foreach (UnityEngine.Object found in Resources.FindObjectsOfTypeAll(tmp))
                    {
                        try
                        {
                            Component component = found as Component;
                            if (component == null || property == null || component.gameObject == null) continue;
                            if (!component.gameObject.scene.IsValid()) continue;
                            if (!Matches(property.GetValue(component, null) as string)) continue;
                            component.gameObject.SetActive(false);
                            hidden++;
                        }
                        catch { }
                    }
                }

                // Silent while it waits for the panel: the search is the normal case on every start
                // screen, not an event. Failures below still speak, because a sweep that throws every
                // second and says nothing is what made this take three builds to diagnose.
                if (hidden == 0) return;

                _done = true;
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "FastLink title hidden (" + hidden + " label(s)); the panel is no longer draggable, "
                    + "set Position of the UI in Azumatt.FastLink.cfg to move it");
            }
            catch (Exception e)
            {
                // Not fatal any more: keep looking next second and say so once. Disabling on the
                // first throw is why one bad object in a scene-wide sweep silenced this entirely.
                if (_looks % 10 == 1)
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "FastLink sweep hit " + e.GetType().Name + " (" + e.Message + "), still looking");
            }
        }

        // Spacing is ignored. The label on screen reads "Fast Link"; the assembly, the config and
        // every mention of the mod write it "FastLink", and an exact comparison against the name in
        // the code matched nothing while the title sat there in plain sight.
        private static bool Matches(string value)
        {
            if (value == null) return false;
            return value.Replace(" ", "").Replace("\u00a0", "").Trim()
                        .Equals(TitleText, StringComparison.OrdinalIgnoreCase);
        }
    }
}
