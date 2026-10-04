// DoorProbe - measure what a `Door` prefab's SOLID COLLIDERS DO WHEN IT OPENS.
//
// Why: `Door.SetState` (assembly_valheim, IL 434912+) does exactly two things --
// `m_animator.SetInteger("state", s)` and `m_openEnable.SetActive(s != 0)`.  So
// the open-state geometry is NOT readable from a static prefab collider dump;
// it lives in an AnimatorController clip.  A closed-pose dump
// (blueprints/PieceGeometry.cs) therefore cannot answer "how wide is the clear
// opening when this gate is open", which is exactly the acceptance question for
// the Ulfsgard sea gate.
//
// Method: instantiate the prefab INACTIVE (so no ZNetView.Awake without a ZDO),
// strip every game MonoBehaviour, activate, drive the Animator by hand through
// state = 0 / 1 / -1, and dump every solid collider's 8 corners in the prefab
// root's local frame at each state.  Same corner convention as PieceGeometry.cs.
//
// Env: DOORPROBE_OUT (required), DOORPROBE_PREFABS (comma list), DOORPROBE_TIMEOUT.
using SIO = global::System.IO;
using SText = global::System.Text;
using SSys = global::System;
using SCG = global::System.Collections.Generic;
using SRef = global::System.Reflection;
using UE = global::UnityEngine;

namespace JumpstartDoorProbe
{
    [BepInEx.BepInPlugin("vibeheim.doorprobe", "DoorProbe", "1.0.0")]
    public class DoorProbePlugin : BepInEx.BaseUnityPlugin
    {
        string _outPath;
        string[] _want;
        float _deadline;
        bool _done;
        SText.StringBuilder _sb = new SText.StringBuilder(1 << 20);

        void Awake()
        {
            _outPath = SSys.Environment.GetEnvironmentVariable("DOORPROBE_OUT");
            string p = SSys.Environment.GetEnvironmentVariable("DOORPROBE_PREFABS");
            _want = string.IsNullOrEmpty(p) ? new string[0] : p.Split(',');
            int timeout = 600;
            string t = SSys.Environment.GetEnvironmentVariable("DOORPROBE_TIMEOUT");
            if (!string.IsNullOrEmpty(t)) int.TryParse(t, out timeout);
            _deadline = UE.Time.realtimeSinceStartup + timeout;
            SSys.Console.Error.WriteLine("[doorprobe] armed, want=" + string.Join("|", _want));
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
                    L("FATAL " + e.GetType().FullName + ": " + e.Message + "\n" + e.StackTrace);
                    Flush(); Kill(3);
                }
                Flush(); Kill(0);
            }
            else if (UE.Time.realtimeSinceStartup > _deadline)
            {
                _done = true;
                L("TIMEOUT waiting for ZNetScene");
                Flush(); Kill(4);
            }
        }

        void L(string s) { _sb.Append(s).Append('\n'); }
        void Flush()
        {
            try { SIO.File.WriteAllText(_outPath, _sb.ToString(), new SText.UTF8Encoding(false)); }
            catch (SSys.Exception e) { SSys.Console.Error.WriteLine("[doorprobe] write failed " + e.Message); }
            SSys.Console.Error.WriteLine("[doorprobe] wrote " + _outPath + " " + _sb.Length + " chars");
            SSys.Console.Error.Flush();
        }
        static void Kill(int code)
        {
            global::System.Diagnostics.Process.GetCurrentProcess().Kill();
        }
        static string F(float v)
        {
            return v.ToString("0.#####", global::System.Globalization.CultureInfo.InvariantCulture);
        }
        static string Path(UE.Transform root, UE.Transform t)
        {
            string s = t.name;
            while (t != root && t.parent != null) { t = t.parent; s = t.name + "/" + s; }
            return s;
        }

        void Dump(ZNetScene zs)
        {
            var all = new SCG.List<UE.GameObject>(zs.m_prefabs);
            if (zs.m_nonNetViewPrefabs != null) all.AddRange(zs.m_nonNetViewPrefabs);
            L("# prefabs_in_scene\t" + all.Count);
            foreach (string wantRaw in _want)
            {
                string want = wantRaw.Trim();
                if (want.Length == 0) continue;
                UE.GameObject prefab = null;
                foreach (UE.GameObject go in all)
                    if (go != null && go.name == want) { prefab = go; break; }
                if (prefab == null) { L("MISSING\t" + want); continue; }
                try { One(prefab); }
                catch (SSys.Exception e) { L("ERROR\t" + want + "\t" + e.GetType().Name + ": " + e.Message + "\n" + e.StackTrace); }
            }
        }

        void One(UE.GameObject prefab)
        {
            L("");
            L("=== PREFAB\t" + prefab.name);
            UE.Transform root = prefab.transform;

            // -- hierarchy + components ---------------------------------------
            foreach (UE.Transform t in prefab.GetComponentsInChildren<UE.Transform>(true))
            {
                var comps = new SCG.List<string>();
                foreach (UE.Component c in t.GetComponents<UE.Component>())
                    if (c != null && !(c is UE.Transform)) comps.Add(c.GetType().Name);
                L("NODE\t" + Path(root, t) + "\tactive=" + (t.gameObject.activeSelf ? 1 : 0)
                  + "\tlpos=" + F(t.localPosition.x) + "," + F(t.localPosition.y) + "," + F(t.localPosition.z)
                  + "\tlrot=" + F(t.localEulerAngles.x) + "," + F(t.localEulerAngles.y) + "," + F(t.localEulerAngles.z)
                  + "\tlscale=" + F(t.localScale.x) + "," + F(t.localScale.y) + "," + F(t.localScale.z)
                  + "\tcomps=" + string.Join(";", comps.ToArray()));
            }

            // -- Door fields ---------------------------------------------------
            Door d = prefab.GetComponent<Door>();
            if (d == null) L("DOOR\tnone");
            else
            {
                SRef.BindingFlags bf = SRef.BindingFlags.Instance | SRef.BindingFlags.Public
                                     | SRef.BindingFlags.NonPublic;
                var parts = new SCG.List<string>();
                foreach (SRef.FieldInfo fi in typeof(Door).GetFields(bf))
                {
                    object v = fi.GetValue(d);
                    string sv;
                    UE.Object uo = v as UE.Object;
                    if (v == null) sv = "null";
                    else if (uo != null) sv = uo.name + "<" + v.GetType().Name + ">";
                    else if (v is UE.Object) sv = "null(unity)";
                    else sv = v.ToString();
                    parts.Add(fi.Name + "=" + sv);
                }
                L("DOOR\t" + string.Join("\t", parts.ToArray()));
            }

            // -- animator description -----------------------------------------
            UE.Animator an0 = prefab.GetComponentInChildren<UE.Animator>(true);
            if (an0 == null) L("ANIMATOR\tnone");
            else
            {
                L("ANIMATOR\tpath=" + Path(root, an0.transform) + "\tcontroller="
                  + (an0.runtimeAnimatorController == null ? "null" : an0.runtimeAnimatorController.name));
                if (an0.runtimeAnimatorController != null)
                    foreach (UE.AnimationClip c in an0.runtimeAnimatorController.animationClips)
                        if (c != null) L("CLIP\t" + c.name + "\tlen=" + F(c.length) + "\twrap=" + c.wrapMode);
            }

            // -- closed-pose collider dump straight off the asset ---------------
            Colliders("ASSET", root, prefab);

            // -- live clone, animator driven ------------------------------------
            bool wasActive = prefab.activeSelf;
            prefab.SetActive(false);
            UE.GameObject clone = UE.Object.Instantiate(prefab);
            prefab.SetActive(wasActive);
            clone.name = prefab.name + "__probe";
            clone.transform.position = UE.Vector3.zero;
            clone.transform.rotation = UE.Quaternion.identity;
            clone.transform.localScale = UE.Vector3.one;
            int stripped = 0;
            foreach (UE.MonoBehaviour mb in clone.GetComponentsInChildren<UE.MonoBehaviour>(true))
            {
                if (mb == null) continue;
                UE.Object.DestroyImmediate(mb);
                stripped++;
            }
            L("CLONE\tstripped_monobehaviours=" + stripped);
            clone.SetActive(true);
            UE.Animator an = clone.GetComponentInChildren<UE.Animator>(true);
            if (an == null) { L("CLONE\tno animator; open pose unmeasurable"); Colliders("CLONE_STATIC", clone.transform, clone); }
            else
            {
                an.cullingMode = UE.AnimatorCullingMode.AlwaysAnimate;
                int[] states = new int[] { 0, 1, -1, 0 };
                for (int i = 0; i < states.Length; i++)
                {
                    an.SetInteger("state", states[i]);
                    an.Update(0f);
                    for (int k = 0; k < 400; k++) an.Update(0.05f);
                    an.Update(0f);
                    UE.AnimatorStateInfo si = an.GetCurrentAnimatorStateInfo(0);
                    L("STATE\t" + states[i] + "\tpass=" + i + "\tnorm_time=" + F(si.normalizedTime)
                      + "\tshortHash=" + si.shortNameHash);
                    foreach (UE.Transform t in clone.GetComponentsInChildren<UE.Transform>(true))
                        L("POSE\t" + states[i] + "\t" + i + "\t" + Path(clone.transform, t)
                          + "\tlpos=" + F(t.localPosition.x) + "," + F(t.localPosition.y) + "," + F(t.localPosition.z)
                          + "\tlrot=" + F(t.localEulerAngles.x) + "," + F(t.localEulerAngles.y) + "," + F(t.localEulerAngles.z));
                    Colliders("CLONE_s" + states[i] + "_p" + i, clone.transform, clone);
                }
            }
            UE.Object.DestroyImmediate(clone);
        }

        void Colliders(string tag, UE.Transform root, UE.GameObject go)
        {
            foreach (UE.Collider col in go.GetComponentsInChildren<UE.Collider>(true))
            {
                UE.Bounds b; string kind;
                if (!Shape(col, out b, out kind))
                {
                    L("COL\t" + tag + "\t" + Path(root, col.transform) + "\t" + kind
                      + "\ttrig=" + (col.isTrigger ? 1 : 0) + "\tenabled=" + (col.enabled ? 1 : 0)
                      + "\tactive=" + (col.gameObject.activeInHierarchy ? 1 : 0) + "\tna");
                    continue;
                }
                UE.Vector3 c = b.center, e = b.extents;
                float ax = 1e9f, ay = 1e9f, az = 1e9f, bx = -1e9f, by = -1e9f, bz = -1e9f;
                var sb2 = new SText.StringBuilder();
                for (int i = 0; i < 8; i++)
                {
                    UE.Vector3 corner = new UE.Vector3(
                        c.x + ((i & 1) == 0 ? -e.x : e.x),
                        c.y + ((i & 2) == 0 ? -e.y : e.y),
                        c.z + ((i & 4) == 0 ? -e.z : e.z));
                    UE.Vector3 p = root.InverseTransformPoint(col.transform.TransformPoint(corner));
                    if (p.x < ax) ax = p.x; if (p.x > bx) bx = p.x;
                    if (p.y < ay) ay = p.y; if (p.y > by) by = p.y;
                    if (p.z < az) az = p.z; if (p.z > bz) bz = p.z;
                    sb2.Append('\t').Append(F(p.x)).Append(',').Append(F(p.y)).Append(',').Append(F(p.z));
                }
                L("COL\t" + tag + "\t" + Path(root, col.transform) + "\t" + kind
                  + "\ttrig=" + (col.isTrigger ? 1 : 0) + "\tenabled=" + (col.enabled ? 1 : 0)
                  + "\tactive=" + (col.gameObject.activeInHierarchy ? 1 : 0)
                  + "\taabb=" + F(ax) + "," + F(ay) + "," + F(az) + ".." + F(bx) + "," + F(by) + "," + F(bz)
                  + sb2.ToString());
            }
        }

        static bool Shape(UE.Collider col, out UE.Bounds b, out string kind)
        {
            b = new UE.Bounds();
            UE.BoxCollider bc = col as UE.BoxCollider;
            if (bc != null) { b = new UE.Bounds(bc.center, bc.size); kind = "box"; return true; }
            UE.SphereCollider sc = col as UE.SphereCollider;
            if (sc != null) { b = new UE.Bounds(sc.center, UE.Vector3.one * (sc.radius * 2f)); kind = "sphere"; return true; }
            UE.CapsuleCollider cc = col as UE.CapsuleCollider;
            if (cc != null)
            {
                float dd = UE.Mathf.Max(cc.height, cc.radius * 2f);
                UE.Vector3 size = UE.Vector3.one * (cc.radius * 2f);
                if (cc.direction == 0) size.x = dd; else if (cc.direction == 1) size.y = dd; else size.z = dd;
                b = new UE.Bounds(cc.center, size); kind = "capsule"; return true;
            }
            UE.MeshCollider mc = col as UE.MeshCollider;
            if (mc != null)
            {
                if (mc.sharedMesh != null) { b = mc.sharedMesh.bounds; kind = "mesh"; return true; }
                kind = "mesh_null"; return false;
            }
            kind = "unknown:" + col.GetType().Name; return false;
        }
    }
}
