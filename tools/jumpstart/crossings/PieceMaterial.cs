// PieceMaterial - dump the STRUCTURAL and WATER-LEGALITY properties of every
// prefab the game ships, plus the vehicle prefabs, so a bridge, dock or ferry
// can be designed against measured numbers instead of folklore.
//
// Why this exists, and what question it answers that nothing else can:
//
//   A bridge is not a picture, it is a load path. MEASURED from
//   assembly_valheim.dll 1.0.12 (`WearNTear::UpdateSupport`,
//   `WearNTear::GetMaterialProperties`, `WearNTear::HaveSupport`):
//
//     support(child) = max over touching neighbours of
//                        support(neighbour) * (1 - loss * (d + 0.1))
//     where d          = distance between the two pieces' centres of mass,
//           loss       = the CHILD's horizontalLoss, verticalLoss, or a Lerp
//                        between them by the angle of the support direction,
//           and a piece whose collider overlaps the `terrain` layer is instead
//           pinned to maxSupport.
//     A piece is destroyed (100 damage per wear tick) once
//           support < minSupport.
//
//   maxSupport / minSupport / horizontalLoss / verticalLoss are a function of
//   `WearNTear.m_materialType` ALONE, and that table is measured and fixed:
//
//     Wood        0  max  100  min  10  hLoss 0.2      vLoss 0.125
//     Stone       1  max 1000  min 100  hLoss 1.0      vLoss 0.125
//     Iron        2  max 1500  min  20  hLoss 0.076923 vLoss 0.076923
//     HardWood    3  max  140  min  10  hLoss 0.166667 vLoss 0.1
//     Marble      4  max 1500  min 100  hLoss 0.5      vLoss 0.125
//     Ashstone    5  max 2000  min 100  hLoss 0.333333 vLoss 0.1
//     Ancient     6  max 5000  min 100  hLoss 0.25     vLoss 0.066667
//     Ice         7  max 1000  min 100  hLoss 0.333333 vLoss 0.125
//     Timberwood  8  max  200  min  10  hLoss 0.2      vLoss 0.076923
//
//   So the maximum pier spacing of a bridge is decided by which material the
//   deck and stringer prefabs carry -- and `m_materialType` is a serialised
//   Unity field on the prefab asset. It is NOT in the IL, NOT in the asset
//   bundle ASCII token scan, and NOT in piece_geometry.json (which measures
//   colliders only). It exists solely on a loaded prefab, which is why this is
//   a plugin inside a real game process.
//
//   The same is true of the two water questions. `Piece.m_waterPiece`,
//   `Piece.m_noInWater` and `Piece.m_groundOnly` decide whether a dock piece is
//   legal over water at all; `ZNetView.m_persistent` decides whether a spawned
//   `Longship` survives a server restart, which decides whether a FERRY is a
//   real thing or a prop that vanishes.
//
// Method, and its limits:
//   * Prefab assets are NOT instantiated, for the same reason PieceGeometry.cs
//     does not: ZNetView.Awake without a ZDO on a dedicated server is not worth
//     the risk. Only serialised fields are read, via GetComponent on the prefab
//     asset, which is safe.
//   * Every prefab in ZNetScene.m_prefabs gets exactly one row, including ones
//     with no WearNTear and no Piece, so "this prefab has no WearNTear" is
//     MEASURED rather than inferred from a missing row.
//   * `na` means the component is absent. `0`/`1` are real measured booleans.
//
// Env vars:
//   PIECEMAT_OUT      output TSV path (required)
//   PIECEMAT_TIMEOUT  seconds to wait for ZNetScene (default 600)
using SIO = global::System.IO;
using SText = global::System.Text;
using SSys = global::System;
using UE = global::UnityEngine;

namespace JumpstartPieceMaterial
{
    [BepInEx.BepInPlugin("vibeheim.piecematerial", "PieceMaterial", "1.0.0")]
    public class PieceMaterialPlugin : BepInEx.BaseUnityPlugin
    {
        string _outPath;
        float _deadline;
        bool _done;

        void Awake()
        {
            _outPath = SSys.Environment.GetEnvironmentVariable("PIECEMAT_OUT");
            if (string.IsNullOrEmpty(_outPath))
            {
                SSys.Console.Error.WriteLine("[piecemat] PIECEMAT_OUT not set; idle");
                return;
            }
            int timeout = 600;
            string t = SSys.Environment.GetEnvironmentVariable("PIECEMAT_TIMEOUT");
            if (!string.IsNullOrEmpty(t)) int.TryParse(t, out timeout);
            _deadline = UE.Time.realtimeSinceStartup + timeout;
            SSys.Console.Error.WriteLine("[piecemat] waiting for ZNetScene, timeout " + timeout + "s");
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
                    SSys.Console.Error.WriteLine("[piecemat] FATAL " + e.GetType().FullName + ": "
                        + e.Message + "\n" + e.StackTrace);
                    Kill(3);
                }
                Kill(0);
            }
            else if (UE.Time.realtimeSinceStartup > _deadline)
            {
                _done = true;
                SSys.Console.Error.WriteLine("[piecemat] TIMEOUT waiting for ZNetScene.m_prefabs");
                Kill(4);
            }
        }

        static void Kill(int code)
        {
            SSys.Console.Error.Flush();
            if (code != 0) SSys.Console.Error.WriteLine("[piecemat] exiting " + code);
            global::System.Diagnostics.Process.GetCurrentProcess().Kill();
        }

        static string B(bool v) { return v ? "1" : "0"; }
        static string F(float v) { return v.ToString("0.#####", global::System.Globalization.CultureInfo.InvariantCulture); }

        static readonly string[] COLS = {
            "name",
            // ZNetView -- does a spawn of this prefab survive a save?
            "nview", "persistent", "distant", "ztype",
            // Piece -- is this legal over water / off the ground?
            "piece", "water_piece", "no_in_water", "ground_only", "ground_piece",
            "clip_ground", "clip_everything", "not_on_wood", "not_on_tilting",
            "not_on_floor", "no_clipping", "allow_alt_ground", "comfort",
            "space_req", "category", "piece_name",
            // WearNTear -- the load path
            "wnt", "material", "health", "supports", "no_support_wear",
            "no_roof_wear", "hit_noise", "min_tool_tier",
            // vehicles / buoyancy -- is a ferry a real object?
            "ship", "ship_force", "ship_disable_level", "ship_water_offset",
            "ship_impact_dmg", "ship_ashlands_ready", "ship_has_sail",
            "floating", "float_water_offset",
            "rigidbody", "rb_mass", "rb_kinematic",
            "container", "container_slots",
        };

        void Dump(ZNetScene zs)
        {
            SText.StringBuilder sb = new SText.StringBuilder(1 << 22);
            sb.Append(string.Join("\t", COLS)).Append('\n');

            int rows = 0, withWnt = 0, withPiece = 0, withShip = 0, persistent = 0;
            foreach (UE.GameObject go in zs.m_prefabs)
            {
                if (go == null) continue;
                sb.Append(go.name);

                ZNetView nv = go.GetComponent<ZNetView>();
                if (nv == null) sb.Append("\t0\tna\tna\tna");
                else
                {
                    sb.Append("\t1\t").Append(B(nv.m_persistent))
                      .Append('\t').Append(B(nv.m_distant))
                      .Append('\t').Append((int)nv.m_type);
                    if (nv.m_persistent) persistent++;
                }

                Piece pc = go.GetComponent<Piece>();
                if (pc == null)
                {
                    sb.Append("\t0");
                    for (int i = 0; i < 15; i++) sb.Append("\tna");
                }
                else
                {
                    withPiece++;
                    sb.Append("\t1")
                      .Append('\t').Append(B(pc.m_waterPiece))
                      .Append('\t').Append(B(pc.m_noInWater))
                      .Append('\t').Append(B(pc.m_groundOnly))
                      .Append('\t').Append(B(pc.m_groundPiece))
                      .Append('\t').Append(B(pc.m_clipGround))
                      .Append('\t').Append(B(pc.m_clipEverything))
                      .Append('\t').Append(B(pc.m_notOnWood))
                      .Append('\t').Append(B(pc.m_notOnTiltingSurface))
                      .Append('\t').Append(B(pc.m_notOnFloor))
                      .Append('\t').Append(B(pc.m_noClipping))
                      .Append('\t').Append(B(pc.m_allowAltGroundPlacement))
                      .Append('\t').Append(pc.m_comfort)
                      .Append('\t').Append(F(pc.m_spaceRequirement))
                      .Append('\t').Append((int)pc.m_category)
                      .Append('\t').Append((pc.m_name ?? "").Replace('\t', ' '));
                }

                WearNTear wnt = go.GetComponent<WearNTear>();
                if (wnt == null)
                {
                    sb.Append("\t0");
                    for (int i = 0; i < 7; i++) sb.Append("\tna");
                }
                else
                {
                    withWnt++;
                    sb.Append("\t1")
                      .Append('\t').Append((int)wnt.m_materialType)
                      .Append('\t').Append(F(wnt.m_health))
                      .Append('\t').Append(B(wnt.m_supports))
                      .Append('\t').Append(B(wnt.m_noSupportWear))
                      .Append('\t').Append(B(wnt.m_noRoofWear))
                      .Append('\t').Append(F(wnt.m_hitNoise))
                      .Append('\t').Append(wnt.m_minToolTier);
                }

                Ship sh = go.GetComponent<Ship>();
                if (sh == null)
                {
                    sb.Append("\t0");
                    for (int i = 0; i < 6; i++) sb.Append("\tna");
                }
                else
                {
                    withShip++;
                    sb.Append("\t1")
                      .Append('\t').Append(F(sh.m_force))
                      .Append('\t').Append(F(sh.m_disableLevel))
                      .Append('\t').Append(F(sh.m_waterLevelOffset))
                      .Append('\t').Append(F(sh.m_waterImpactDamage))
                      .Append('\t').Append(B(sh.m_ashlandsReady))
                      .Append('\t').Append(B(sh.m_hasSail));
                }

                Floating fl = go.GetComponent<Floating>();
                if (fl == null) sb.Append("\t0\tna");
                else sb.Append("\t1\t").Append(F(fl.m_waterLevelOffset));

                UE.Rigidbody rb = go.GetComponent<UE.Rigidbody>();
                if (rb == null) sb.Append("\t0\tna\tna");
                else sb.Append("\t1\t").Append(F(rb.mass)).Append('\t').Append(B(rb.isKinematic));

                Container ct = go.GetComponent<Container>();
                if (ct == null) sb.Append("\t0\tna");
                else sb.Append("\t1\t").Append(ct.m_width * ct.m_height);

                sb.Append('\n');
                rows++;
            }

            SIO.File.WriteAllText(_outPath, sb.ToString());
            SSys.Console.Error.WriteLine("[piecemat] wrote " + rows + " prefabs to " + _outPath
                + " (piece=" + withPiece + " wearntear=" + withWnt + " ship=" + withShip
                + " persistent=" + persistent + ")");
        }
    }
}
