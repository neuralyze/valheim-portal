// LampProbe - answer, BY MEASUREMENT INSIDE A LOADED GAME PROCESS, the three
// questions that decide which prefab lights 9 km of road:
//
//   1. DOES IT EMIT LIGHT?  A `Light` component somewhere in the prefab, with
//      its range and intensity, and whether the GameObject carrying it is
//      active in the prefab or parked under a `Fireplace.m_enabledObject` that
//      only switches on when the fire burns.
//   2. DOES IT NEED FUEL, AND DOES IT BURN OUT?  Every instance field of the
//      `Fireplace` component, BY REFLECTION rather than by naming the fields I
//      expect.  Naming them is how a probe answers a question about the field
//      it guessed instead of the field that exists -- `m_infiniteFuel`,
//      `m_startFuel`, `m_maxFuel` and `m_secPerFuel` are what this project's
//      own `data_entry.py` lists, and a version that renamed one of them would
//      read as "absent" under a name filter.
//   3. WHERE IS ITS PIVOT RELATIVE TO THE GROUND IT STANDS ON?  The render
//      mesh's local y extent.  `blueprints/piece_geometry.json` already
//      carries the COLLIDER extent, and for a standing torch the two disagree
//      with nothing: MEASURED, `piece_groundtorch` renders -0.6536..0.8226 and
//      collides -0.6536..0.8226, so its pivot is 0.65 m up the shaft and
//      `base_geometry`'s ground-resting guard drops it for exactly that
//      reason.  A lamp seated as if its pivot were its foot floats 0.65 m.
//
// WHY A GAME PROCESS AND NOT A FILE SCAN.  `Fireplace.m_infiniteFuel` and a
// `Light.range` are serialized PREFAB ASSET values.  They are not in the
// managed assembly (which holds only the field definitions), not in any config
// file, and not derivable from the prefab's name.  `PieceGeometry.cs` had to
// boot the server for the same reason and this is the same instrument with a
// different question, so it is the same shape: BepInEx plugin, poll for
// ZNetScene in Update because plugin Awake runs before the game scene, write
// one file, SIGKILL.
//
// Prefabs are NOT instantiated, for `PieceGeometry.cs`'s reason: instantiating
// a `Piece` on a dedicated server runs `ZNetView.Awake` with no ZDO.  Every
// number here is read off the prefab asset.
//
// Env vars:
//   LAMPPROBE_OUT      output JSONL path (required)
//   LAMPPROBE_MATCH    comma-separated substrings; a prefab is probed when its
//                      name contains one, case-insensitively.  Default
//                      "torch,lantern,brazier,candle,lamp,sconce".  THE MATCH
//                      IS A NET, NOT AN ANSWER: every matched prefab is
//                      reported with what it actually has, so "this name looked
//                      like a lamp and has no Light" is a visible row rather
//                      than a silent omission.
//   LAMPPROBE_NAMES    comma-separated EXACT names, probed in addition to the
//                      match set.  Use it to force a candidate into the report.
//   LAMPPROBE_TIMEOUT  seconds to wait for ZNetScene (default 600)
//
// Output: one JSON object per line, keys stable, unknown values `null`.
using SIO = global::System.IO;
using SText = global::System.Text;
using SSys = global::System;
using SCG = global::System.Collections.Generic;
using SRef = global::System.Reflection;
using UE = global::UnityEngine;

namespace JumpstartLampProbe
{
    [BepInEx.BepInPlugin("vibeheim.lampprobe", "LampProbe", "1.0.0")]
    public class LampProbePlugin : BepInEx.BaseUnityPlugin
    {
        string _outPath;
        float _deadline;
        bool _done;
        string[] _match;
        SCG.HashSet<string> _exact;

        void Awake()
        {
            _outPath = SSys.Environment.GetEnvironmentVariable("LAMPPROBE_OUT");
            if (string.IsNullOrEmpty(_outPath))
            {
                SSys.Console.Error.WriteLine("[lampprobe] LAMPPROBE_OUT not set; idle");
                return;
            }
            string m = SSys.Environment.GetEnvironmentVariable("LAMPPROBE_MATCH");
            if (string.IsNullOrEmpty(m)) m = "torch,lantern,brazier,candle,lamp,sconce";
            _match = m.ToLowerInvariant().Split(',');
            _exact = new SCG.HashSet<string>();
            string n = SSys.Environment.GetEnvironmentVariable("LAMPPROBE_NAMES");
            if (!string.IsNullOrEmpty(n))
                foreach (string s in n.Split(',')) if (s.Length > 0) _exact.Add(s);
            int timeout = 600;
            string t = SSys.Environment.GetEnvironmentVariable("LAMPPROBE_TIMEOUT");
            if (!string.IsNullOrEmpty(t)) int.TryParse(t, out timeout);
            _deadline = UE.Time.realtimeSinceStartup + timeout;
            SSys.Console.Error.WriteLine("[lampprobe] waiting for ZNetScene, timeout " + timeout + "s");
        }

        void Update()
        {
            if (_done || string.IsNullOrEmpty(_outPath)) return;
            ZNetScene zs = ZNetScene.instance;
            if (zs != null && zs.m_prefabs != null && zs.m_prefabs.Count > 0)
            {
                _done = true;
                try { Dump(zs); }
                catch (SSys.Exception e)
                {
                    SSys.Console.Error.WriteLine("[lampprobe] FATAL " + e.GetType().FullName + ": "
                        + e.Message + "\n" + e.StackTrace);
                    Kill(3);
                }
                Kill(0);
            }
            else if (UE.Time.realtimeSinceStartup > _deadline)
            {
                _done = true;
                SSys.Console.Error.WriteLine("[lampprobe] TIMEOUT waiting for ZNetScene.m_prefabs");
                Kill(4);
            }
        }

        static void Kill(int code)
        {
            SSys.Console.Error.Flush();
            if (code != 0) SSys.Console.Error.WriteLine("[lampprobe] exiting " + code);
            global::System.Diagnostics.Process.GetCurrentProcess().Kill();
        }

        static string F(float v)
        {
            return v.ToString("0.#####", global::System.Globalization.CultureInfo.InvariantCulture);
        }

        static string Q(string s)
        {
            if (s == null) return "null";
            var sb = new SText.StringBuilder(s.Length + 2);
            sb.Append('"');
            foreach (char c in s)
            {
                if (c == '"' || c == '\\') sb.Append('\\').Append(c);
                else if (c < ' ') sb.Append(' ');
                else sb.Append(c);
            }
            return sb.Append('"').ToString();
        }

        bool Wanted(string name)
        {
            if (_exact.Contains(name)) return true;
            string low = name.ToLowerInvariant();
            foreach (string frag in _match)
                if (frag.Length > 0 && low.Contains(frag)) return true;
            return false;
        }

        // Every instance field of `comp`, rendered as JSON. Reflection and not a
        // field list: see the header. An ItemDrop or a GameObject reference is
        // reduced to its name, which is the only part of it that survives a file.
        static string Fields(object comp)
        {
            if (comp == null) return "null";
            var sb = new SText.StringBuilder("{");
            bool first = true;
            SRef.FieldInfo[] fis = comp.GetType().GetFields(
                SRef.BindingFlags.Instance | SRef.BindingFlags.Public
                | SRef.BindingFlags.NonPublic);
            foreach (SRef.FieldInfo fi in fis)
            {
                object v;
                try { v = fi.GetValue(comp); }
                catch (SSys.Exception) { continue; }
                string rendered = null;
                if (v == null) rendered = "null";
                else if (v is bool) rendered = ((bool)v) ? "true" : "false";
                else if (v is float) rendered = F((float)v);
                else if (v is double) rendered = F((float)(double)v);
                else if (v is int || v is long || v is short || v is byte)
                    rendered = v.ToString();
                else if (v is string) rendered = Q((string)v);
                else if (v is UE.Object) rendered = Q(((UE.Object)v).name);
                else continue;   // collections and behaviours: not a scalar fact
                if (!first) sb.Append(',');
                first = false;
                sb.Append(Q(fi.Name)).Append(':').Append(rendered);
            }
            return sb.Append('}').ToString();
        }

        // Is `t` active all the way up to `root`? A Light parked under an
        // inactive child is the normal shape for a Fireplace: the fire object is
        // switched on when it burns. Reporting `enabled` alone would call every
        // torch dark.
        static bool ActiveToRoot(UE.Transform t, UE.Transform root)
        {
            while (t != null)
            {
                if (!t.gameObject.activeSelf) return false;
                if (t == root) return true;
                t = t.parent;
            }
            return true;
        }

        // The child path from `root` down to `t`, so a reader can tell which
        // object a light hangs on -- the name alone is routinely "point light".
        static string PathTo(UE.Transform t, UE.Transform root)
        {
            string path = t.name;
            UE.Transform p = t.parent;
            while (p != null && p != root)
            {
                path = p.name + "/" + path;
                p = p.parent;
            }
            return path;
        }

        // lint:per-frame bounded by Update()'s `_done` latch, set true BEFORE this runs, so
        // the scan happens at most once per process and the process then exits. A one-shot
        // diagnostic over ZNetScene's prefab list, never on a steady-state frame path.
        void Dump(ZNetScene zs)
        {
            var sb = new SText.StringBuilder(1 << 20);
            int probed = 0, seenCount = 0;
            var seen = new SCG.HashSet<string>();
            var all = new SCG.List<UE.GameObject>(zs.m_prefabs);
            if (zs.m_nonNetViewPrefabs != null) all.AddRange(zs.m_nonNetViewPrefabs);

            foreach (UE.GameObject go in all)
            {
                if (go == null || !seen.Add(go.name)) continue;
                seenCount++;
                UE.Transform root = go.transform;

                Piece piece = go.GetComponent<Piece>();
                ZNetView nview = go.GetComponent<ZNetView>();
                Fireplace fp = go.GetComponent<Fireplace>();
                LightFlicker flicker = go.GetComponentInChildren<LightFlicker>(true);
                UE.Light anyLight = go.GetComponentInChildren<UE.Light>(true);

                // THE GATE IS A COMPONENT TEST FIRST AND A NAME TEST SECOND.
                // "Do not guess the shape of a name": a mod's lamp may be called
                // `rk_glowstone` and a prefix filter over a mod bundle has
                // already returned 31 noise hits out of 55,789 strings in this
                // project. Anything BUILDABLE that carries a Light or a
                // Fireplace is a lighting candidate whatever it is called, and
                // the name net is kept only so an unlit thing called "torch"
                // still appears as a row that says it is unlit.
                bool byComponent = piece != null && (anyLight != null || fp != null);
                if (!byComponent && !Wanted(go.name)) continue;
                probed++;
                sb.Append('{').Append(Q("matched_by")).Append(':')
                  .Append(Q(byComponent ? "component" : "name"));
                sb.Append(',').Append(Q("name")).Append(':').Append(Q(go.name));
                sb.Append(',').Append(Q("has_piece")).Append(':').Append(piece != null ? "true" : "false");
                sb.Append(',').Append(Q("piece_name")).Append(':')
                  .Append(piece != null ? Q(piece.m_name) : "null");
                sb.Append(',').Append(Q("piece_description")).Append(':')
                  .Append(piece != null ? Q(piece.m_description) : "null");
                sb.Append(',').Append(Q("has_znetview")).Append(':').Append(nview != null ? "true" : "false");
                sb.Append(',').Append(Q("nview_persistent")).Append(':')
                  .Append(nview != null ? (nview.m_persistent ? "true" : "false") : "null");
                sb.Append(',').Append(Q("nview_type")).Append(':')
                  .Append(nview != null ? Q(nview.m_type.ToString()) : "null");
                sb.Append(',').Append(Q("has_fireplace")).Append(':').Append(fp != null ? "true" : "false");
                sb.Append(',').Append(Q("fireplace")).Append(':').Append(Fields(fp));
                sb.Append(',').Append(Q("has_lightflicker")).Append(':')
                  .Append(flicker != null ? "true" : "false");
                sb.Append(',').Append(Q("piece")).Append(':').Append(Fields(piece));
                sb.Append(',').Append(Q("wearntear")).Append(':')
                  .Append(Fields(go.GetComponent<WearNTear>()));
                // EVERY COMPONENT TYPE ON THE PREFAB, root and children.  The
                // question this answers is the one a field dump cannot: is this
                // piece gated by something -- a seasonal or event behaviour, a
                // pickable, a timer -- that would take a road's lighting away in
                // February.  A component that is not looked for is not absent,
                // so the whole set is listed rather than a few probed by name.
                var comps = new SCG.SortedSet<string>();
                // lint:per-frame one-shot, see Dump's latch note; walks one prefab's hierarchy.
                foreach (UE.Component c in go.GetComponentsInChildren<UE.Component>(true))
                    if (c != null) comps.Add(c.GetType().Name);
                var clist = new SText.StringBuilder("[");
                bool firstComp = true;
                foreach (string c in comps)
                {
                    if (!firstComp) clist.Append(',');
                    firstComp = false;
                    clist.Append(Q(c));
                }
                sb.Append(',').Append(Q("components")).Append(':')
                  .Append(clist.Append(']').ToString());

                // --- lights ---------------------------------------------------
                var lights = new SText.StringBuilder("[");
                bool firstLight = true;
                int litCount = 0;
                float maxRange = 0f, maxIntensity = 0f;
                // lint:per-frame one-shot, see Dump's latch note; walks one prefab's hierarchy.
                foreach (UE.Light lt in go.GetComponentsInChildren<UE.Light>(true))
                {
                    litCount++;
                    if (lt.range > maxRange) maxRange = lt.range;
                    if (lt.intensity > maxIntensity) maxIntensity = lt.intensity;
                    if (!firstLight) lights.Append(',');
                    firstLight = false;
                    UE.Color c = lt.color;
                    lights.Append('{')
                        .Append(Q("path")).Append(':').Append(Q(PathTo(lt.transform, root)))
                        .Append(',').Append(Q("type")).Append(':').Append(Q(lt.type.ToString()))
                        .Append(',').Append(Q("range")).Append(':').Append(F(lt.range))
                        .Append(',').Append(Q("intensity")).Append(':').Append(F(lt.intensity))
                        .Append(',').Append(Q("enabled")).Append(':').Append(lt.enabled ? "true" : "false")
                        .Append(',').Append(Q("active_to_root")).Append(':')
                        .Append(ActiveToRoot(lt.transform, root) ? "true" : "false")
                        .Append(',').Append(Q("colour_rgb")).Append(':')
                        .Append('[').Append(F(c.r)).Append(',').Append(F(c.g)).Append(',')
                        .Append(F(c.b)).Append(']')
                        .Append(',').Append(Q("local_y")).Append(':')
                        .Append(F(root.InverseTransformPoint(lt.transform.position).y))
                        // WHERE A SPOT POINTS decides whether its range is a
                        // reach along the road or a pool at the lamp's foot, and
                        // the two are not the same lighting.
                        .Append(',').Append(Q("spot_angle")).Append(':').Append(F(lt.spotAngle))
                        .Append(',').Append(Q("aim_local")).Append(':')
                        .Append('[')
                        .Append(F(root.InverseTransformDirection(lt.transform.forward).x)).Append(',')
                        .Append(F(root.InverseTransformDirection(lt.transform.forward).y)).Append(',')
                        .Append(F(root.InverseTransformDirection(lt.transform.forward).z))
                        .Append(']')
                        .Append('}');
                }
                sb.Append(',').Append(Q("light_count")).Append(':').Append(litCount);
                sb.Append(',').Append(Q("light_max_range")).Append(':').Append(F(maxRange));
                sb.Append(',').Append(Q("light_max_intensity")).Append(':').Append(F(maxIntensity));
                sb.Append(',').Append(Q("lights")).Append(':').Append(lights.Append(']').ToString());

                // --- extents, render mesh AND collider ------------------------
                // Both, because they are two different questions and this project
                // has already paid for conflating them: the collider is what
                // `fixtures.audit()` tests for overlap, the RENDER mesh is what
                // the player sees floating.
                bool anyMesh = false, anySolid = false;
                float mMinY = 0f, mMaxY = 0f, sMinY = 0f, sMaxY = 0f;
                // lint:per-frame one-shot, see Dump's latch note; walks one prefab's hierarchy.
                foreach (UE.MeshFilter mf in go.GetComponentsInChildren<UE.MeshFilter>(true))
                {
                    if (mf.sharedMesh == null) continue;
                    UE.Bounds b = mf.sharedMesh.bounds;
                    for (int i = 0; i < 8; i++)
                    {
                        UE.Vector3 corner = new UE.Vector3(
                            b.center.x + ((i & 1) == 0 ? -b.extents.x : b.extents.x),
                            b.center.y + ((i & 2) == 0 ? -b.extents.y : b.extents.y),
                            b.center.z + ((i & 4) == 0 ? -b.extents.z : b.extents.z));
                        float y = root.InverseTransformPoint(mf.transform.TransformPoint(corner)).y;
                        if (!anyMesh) { mMinY = mMaxY = y; anyMesh = true; }
                        else { if (y < mMinY) mMinY = y; if (y > mMaxY) mMaxY = y; }
                    }
                }
                // lint:per-frame one-shot, see Dump's latch note; walks one prefab's hierarchy.
                foreach (UE.Collider col in go.GetComponentsInChildren<UE.Collider>(true))
                {
                    if (col.isTrigger) continue;
                    UE.Bounds b;
                    UE.BoxCollider bc = col as UE.BoxCollider;
                    UE.SphereCollider sc = col as UE.SphereCollider;
                    UE.CapsuleCollider cc = col as UE.CapsuleCollider;
                    UE.MeshCollider mc = col as UE.MeshCollider;
                    if (bc != null) b = new UE.Bounds(bc.center, bc.size);
                    else if (sc != null) b = new UE.Bounds(sc.center, UE.Vector3.one * (sc.radius * 2f));
                    else if (cc != null)
                    {
                        float d = UE.Mathf.Max(cc.height, cc.radius * 2f);
                        UE.Vector3 size = UE.Vector3.one * (cc.radius * 2f);
                        if (cc.direction == 0) size.x = d;
                        else if (cc.direction == 1) size.y = d;
                        else size.z = d;
                        b = new UE.Bounds(cc.center, size);
                    }
                    else if (mc != null && mc.sharedMesh != null) b = mc.sharedMesh.bounds;
                    else continue;
                    for (int i = 0; i < 8; i++)
                    {
                        UE.Vector3 corner = new UE.Vector3(
                            b.center.x + ((i & 1) == 0 ? -b.extents.x : b.extents.x),
                            b.center.y + ((i & 2) == 0 ? -b.extents.y : b.extents.y),
                            b.center.z + ((i & 4) == 0 ? -b.extents.z : b.extents.z));
                        float y = root.InverseTransformPoint(col.transform.TransformPoint(corner)).y;
                        if (!anySolid) { sMinY = sMaxY = y; anySolid = true; }
                        else { if (y < sMinY) sMinY = y; if (y > sMaxY) sMaxY = y; }
                    }
                }
                sb.Append(',').Append(Q("mesh_min_y")).Append(':').Append(anyMesh ? F(mMinY) : "null");
                sb.Append(',').Append(Q("mesh_max_y")).Append(':').Append(anyMesh ? F(mMaxY) : "null");
                sb.Append(',').Append(Q("solid_min_y")).Append(':').Append(anySolid ? F(sMinY) : "null");
                sb.Append(',').Append(Q("solid_max_y")).Append(':').Append(anySolid ? F(sMaxY) : "null");
                sb.Append("}\n");
            }

            SIO.File.WriteAllText(_outPath, sb.ToString(), new SText.UTF8Encoding(false));
            SSys.Console.Error.WriteLine("[lampprobe] wrote " + _outPath + " prefabs_seen="
                + seenCount + " probed=" + probed);
        }
    }
}
