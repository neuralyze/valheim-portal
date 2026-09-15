// PieceGeometry - dump the LOCAL-SPACE collider solids of every prefab the game
// ships, so the blueprint placement datum can be computed from real geometry
// instead of an assumed pivot.
//
// Why this exists, and what it is FOR:
//   A PlanBuild `.blueprint` stores each piece as a PIVOT position plus a
//   rotation quaternion and a scale. A pivot is not a surface. MEASURED here:
//   `stone_floor_2x2` is a 2 x 2 x 1 m solid whose pivot sits at MID-THICKNESS,
//   so its pivot Y is 0.5 m above its underside and 0.5 m below the surface a
//   player walks on. `stone_wall_1x1` is the same story. Which means the
//   question "where does this blueprint's floor meet the ground" cannot be
//   answered from the blueprint text, from `assembly_valheim.dll` IL, or from
//   the asset-bundle ASCII token scan that
//   tools/jumpstart/blueprints/scan_piece_prefabs.py does. It is only
//   answerable from the prefab's Collider components, which exist solely inside
//   a loaded game process.
//
//   So: boot the dedicated server in a sandbox, wait for ZNetScene to publish
//   `m_prefabs`, and for every prefab emit each collider as EIGHT CORNER POINTS
//   EXPRESSED IN THE PREFAB ROOT'S LOCAL FRAME. That frame is exactly the frame
//   a blueprint row's x/y/z is in, so a consumer can apply the row's own
//   rotation and scale to those corners and get the piece's true minimum and
//   maximum Y in blueprint-local space.
//
// Why CORNERS and not centre+extent+quaternion:
//   A collider can hang off a child transform with rotation AND non-uniform
//   scale. Under non-uniform scale a rotated box is no longer a box, so
//   centre/extent/rotation cannot represent it and an axis-aligned box around it
//   is looser than the truth. Eight transformed corners describe the actual
//   parallelepiped exactly. This matters: an inflated box is precisely the
//   "a check that confidently answers a question it was not measuring" failure
//   this datum work exists to end.
//
// Method, and its limits:
//   * Prefab assets are NOT instantiated. Instantiating a Piece on a dedicated
//     server runs ZNetView.Awake without a ZDO and is not worth the risk, and
//     `Collider.bounds` on an un-instantiated asset is world-space garbage.
//     Instead each collider's own SHAPE parameters are read (BoxCollider
//     center/size, SphereCollider center/radius, CapsuleCollider
//     center/radius/height/direction, MeshCollider sharedMesh.bounds) and its 8
//     local corners are pushed through the child transform chain up to the
//     prefab root. No physics call is made.
//   * A SphereCollider / CapsuleCollider is reported as its bounding box, which
//     is looser than the curved solid. Recorded in the `kind` column so a
//     consumer can see it rather than be handed it silently.
//   * TRIGGER colliders are emitted with is_trigger=1 and MUST NOT be folded
//     into the solid. A trigger is an interaction volume (a workbench's build
//     radius, a door's use zone), not geometry, and folding one in inflates a
//     piece by metres.
//   * A MeshCollider whose sharedMesh is null (stripped on a server build)
//     emits kind=mesh_null with no corners, so a caller can tell "this piece has
//     no collider" from "this piece's collider could not be measured". That
//     distinction is the whole point: the datum rule must be able to refuse.
//   * MeshFilter geometry is emitted too, kind=mesh_render, for the cases where
//     collider and render meshes disagree. On a -nographics server the mesh
//     asset is still loaded.
//
// Hosting constraint is the same as PatchScan.cs / LocScan.cs: this has to be a
// BepInEx plugin inside a real game process. Unlike PatchScan it cannot do its
// work in Awake, because plugin Awake runs in the start scene where
// ZNetScene.instance is still null; it polls in Update instead, like LocScan.
//
// Env vars:
//   PIECEGEOM_OUT      output TSV path (required)
//   PIECEGEOM_TIMEOUT  seconds to wait for ZNetScene (default 600)
//
// Output: TSV, header then one row per collider (and per render mesh):
//   name  has_piece  kind  is_trigger  x0 y0 z0 ... x7 y7 z7
// `kind` is one of box, sphere, capsule, mesh, mesh_null, mesh_render.
// A prefab with no collider at all still gets one row, kind=none, so every
// prefab in ZNetScene.m_prefabs is present and "absent" is never inferred from
// a missing row.
using SIO = global::System.IO;
using SText = global::System.Text;
using SSys = global::System;
using SCG = global::System.Collections.Generic;
using UE = global::UnityEngine;

namespace JumpstartPieceGeometry
{
    [BepInEx.BepInPlugin("vibeheim.piecegeometry", "PieceGeometry", "1.0.0")]
    public class PieceGeometryPlugin : BepInEx.BaseUnityPlugin
    {
        string _outPath;
        float _deadline;
        bool _done;

        void Awake()
        {
            _outPath = SSys.Environment.GetEnvironmentVariable("PIECEGEOM_OUT");
            if (string.IsNullOrEmpty(_outPath))
            {
                SSys.Console.Error.WriteLine("[piecegeom] PIECEGEOM_OUT not set; idle");
                return;
            }
            int timeout = 600;
            string t = SSys.Environment.GetEnvironmentVariable("PIECEGEOM_TIMEOUT");
            if (!string.IsNullOrEmpty(t)) int.TryParse(t, out timeout);
            _deadline = UE.Time.realtimeSinceStartup + timeout;
            SSys.Console.Error.WriteLine("[piecegeom] waiting for ZNetScene, timeout " + timeout + "s");
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
                    SSys.Console.Error.WriteLine("[piecegeom] FATAL " + e.GetType().FullName + ": "
                        + e.Message + "\n" + e.StackTrace);
                    Kill(3);
                }
                Kill(0);
            }
            else if (UE.Time.realtimeSinceStartup > _deadline)
            {
                _done = true;
                SSys.Console.Error.WriteLine("[piecegeom] TIMEOUT waiting for ZNetScene.m_prefabs");
                Kill(4);
            }
        }

        static void Kill(int code)
        {
            SSys.Console.Error.Flush();
            if (code != 0) SSys.Console.Error.WriteLine("[piecegeom] exiting " + code);
            global::System.Diagnostics.Process.GetCurrentProcess().Kill();
        }

        // Push the 8 corners of a local-space AABB through `from`'s transform up
        // into `root`'s local frame and append them to the row. Corners rather
        // than centre+extent is what makes a rotated, non-uniformly scaled child
        // collider honest -- see the header note.
        static void AppendCorners(SText.StringBuilder sb, UE.Transform root, UE.Transform from,
                                  UE.Bounds b)
        {
            UE.Vector3 c = b.center, e = b.extents;
            for (int i = 0; i < 8; i++)
            {
                UE.Vector3 corner = new UE.Vector3(
                    c.x + ((i & 1) == 0 ? -e.x : e.x),
                    c.y + ((i & 2) == 0 ? -e.y : e.y),
                    c.z + ((i & 4) == 0 ? -e.z : e.z));
                UE.Vector3 p = root.InverseTransformPoint(from.TransformPoint(corner));
                sb.Append('\t').Append(F(p.x)).Append('\t').Append(F(p.y)).Append('\t').Append(F(p.z));
            }
        }

        static void AppendNoCorners(SText.StringBuilder sb)
        {
            for (int i = 0; i < 24; i++) sb.Append("\tna");
        }

        // Returns the collider's shape as a local AABB in ITS OWN transform's
        // space, plus a kind tag. `false` means the shape could not be measured
        // at all, which is reported rather than skipped.
        static bool ShapeBounds(UE.Collider col, out UE.Bounds b, out string kind)
        {
            b = new UE.Bounds();
            UE.BoxCollider bc = col as UE.BoxCollider;
            if (bc != null) { b = new UE.Bounds(bc.center, bc.size); kind = "box"; return true; }
            UE.SphereCollider sc = col as UE.SphereCollider;
            if (sc != null)
            {
                b = new UE.Bounds(sc.center, UE.Vector3.one * (sc.radius * 2f));
                kind = "sphere";
                return true;
            }
            UE.CapsuleCollider cc = col as UE.CapsuleCollider;
            if (cc != null)
            {
                float d = UE.Mathf.Max(cc.height, cc.radius * 2f);
                UE.Vector3 size = UE.Vector3.one * (cc.radius * 2f);
                if (cc.direction == 0) size.x = d;
                else if (cc.direction == 1) size.y = d;
                else size.z = d;
                b = new UE.Bounds(cc.center, size);
                kind = "capsule";
                return true;
            }
            UE.MeshCollider mc = col as UE.MeshCollider;
            if (mc != null)
            {
                if (mc.sharedMesh != null) { b = mc.sharedMesh.bounds; kind = "mesh"; return true; }
                kind = "mesh_null";
                return false;
            }
            kind = "unknown:" + col.GetType().Name;
            return false;
        }

        static string F(float v)
        {
            return v.ToString("0.#####", global::System.Globalization.CultureInfo.InvariantCulture);
        }

        // lint:per-frame bounded by Update()'s `_done` latch, which is set to true BEFORE this
        // is called, so the whole scan runs at most once per process. This is a one-shot
        // diagnostic that walks ZNetScene's prefab list to dump every piece's collider
        // extents - the measurement that produced the floor datum - and the process exits
        // after writing the file. It is never on a steady-state frame path.
        void Dump(ZNetScene zs)
        {
            var sb = new SText.StringBuilder(1 << 22);
            sb.Append("name\thas_piece\tkind\tis_trigger");
            for (int i = 0; i < 8; i++)
                sb.Append("\tx").Append(i).Append("\ty").Append(i).Append("\tz").Append(i);
            sb.Append('\n');

            int prefabs = 0, rows = 0, withPiece = 0, unmeasurable = 0;
            var seen = new SCG.HashSet<string>();
            var all = new SCG.List<UE.GameObject>(zs.m_prefabs);
            if (zs.m_nonNetViewPrefabs != null) all.AddRange(zs.m_nonNetViewPrefabs);

            foreach (UE.GameObject go in all)
            {
                if (go == null || !seen.Add(go.name)) continue;
                prefabs++;
                UE.Transform root = go.transform;
                bool hasPiece = go.GetComponent<Piece>() != null;
                if (hasPiece) withPiece++;
                int emitted = 0;

                // lint:per-frame bounded by the one-shot `_done` latch in Update() and by the
                // prefab's own hierarchy - this walks one prefab, not the live scene.
                foreach (UE.Collider col in go.GetComponentsInChildren<UE.Collider>(true))
                {
                    UE.Bounds b; string kind;
                    bool ok = ShapeBounds(col, out b, out kind);
                    sb.Append(go.name).Append('\t').Append(hasPiece ? 1 : 0)
                      .Append('\t').Append(kind).Append('\t').Append(col.isTrigger ? 1 : 0);
                    if (ok) AppendCorners(sb, root, col.transform, b);
                    else { AppendNoCorners(sb); unmeasurable++; }
                    sb.Append('\n');
                    rows++;
                    emitted++;
                }
                // lint:per-frame bounded by the one-shot `_done` latch in Update() and by the
                // prefab's own hierarchy - this walks one prefab, not the live scene.
                foreach (UE.MeshFilter mf in go.GetComponentsInChildren<UE.MeshFilter>(true))
                {
                    if (mf.sharedMesh == null) continue;
                    sb.Append(go.name).Append('\t').Append(hasPiece ? 1 : 0)
                      .Append("\tmesh_render\t0");
                    AppendCorners(sb, root, mf.transform, mf.sharedMesh.bounds);
                    sb.Append('\n');
                    rows++;
                    emitted++;
                }
                if (emitted == 0)
                {
                    sb.Append(go.name).Append('\t').Append(hasPiece ? 1 : 0).Append("\tnone\t0");
                    AppendNoCorners(sb);
                    sb.Append('\n');
                    rows++;
                }
            }

            SIO.File.WriteAllText(_outPath, sb.ToString(), new SText.UTF8Encoding(false));
            SSys.Console.Error.WriteLine("[piecegeom] wrote " + _outPath + " prefabs=" + prefabs
                + " rows=" + rows + " with_piece=" + withPiece + " unmeasurable=" + unmeasurable);
        }
    }
}
