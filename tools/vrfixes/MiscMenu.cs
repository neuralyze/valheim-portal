using System;
using System.Collections.Generic;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace NeuralyzeVRFixes
{
    // Makes mod features reachable in VR.
    //
    // The measured problem: a mod that registers its own ZInput button gets no VR binding,
    // and VHVR logs "Unmapped ZInput Key: <name>" once then adds the name to a PERMANENT
    // ignore set (VRControls.cs:521, initIgnoredZInputs :986-996) - so the feature is dead
    // for the rest of the session. 22 distinct unmapped actions were observed on this
    // install. Separately, mods bound to keyboard shortcuts have no VR path at all, which
    // is the single largest gap: VHVR patches only get_mousePosition, GetKeyDownInt and
    // GetKeyInt, and nothing injects into them.
    //
    // Rather than build a parallel UI, this injects into VHVR's existing wrist radial menu.
    // QuickAbstract already exposes a public useAsQuickAction(name, sprite, callback) and
    // already populates extraElements with non-item actions such as Sit and Map, so the
    // structure for this exists; only the entries were missing. Extra levels come from
    // paging that same radial rather than a third menu.
    internal static class MiscMenu
    {
        internal sealed class Entry
        {
            internal string Label;
            internal string Group;   // level 1; "" = the top level ring
            internal string Sub;     // level 2, inside Group; "" = none
            internal string Kind;
            internal string Value;
            internal string When;    // MenuContext predicate; empty means always

            // Held by the delegate handed to VHVR, so each radial slot knows which action
            // it represents. A static callback could not carry that.
            internal bool Execute()
            {
                try
                {
                    if (Kind == "zinput") return ZInputPulse.Send(Value);
                    if (Kind == "key") return KeyPulse.Send(Value);
                    // A held modifier, not a press. Several mods only act while a key is held
                    // WHILE you do something else - OdinHorse says so outright: "the key needed
                    // to be HELD while interacting with the horse". A momentary pulse can never
                    // satisfy that, so these latch on, you interact, and you tap again to release.
                    if (Kind == "hold") return KeyPulse.Latch(Value);
                    if (Kind == "console") return ConsoleOpener.Open() && DirectActions.OpenConsoleKeyboard();
                    if (Kind == "emote") return DirectActions.Emote(Value);
                    if (Kind == "zoom") return DirectActions.Zoom(Value);
                    if (Kind == "chat") return DirectActions.OpenChat();
                    if (Kind == "power") return DirectActions.GuardianPower();
                    if (Kind == "panel") return DirectActions.ClosePanels();
                    if (Kind == "cmd") return DirectActions.RunCommandSequence(Value);
                    if (Kind == "ui") return DirectActions.AdoptAndShow(Value);
                    if (Kind == "mount") return DirectActions.ReleaseMount();
                    if (Kind == "sail") return DirectActions.ShipSpeed(Value);
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "misc action '" + Label + "' has unknown kind '" + Kind + "'");
                    return false;
                }
                catch (Exception e)
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "misc action '" + Label + "' failed: " + e.Message);
                    return false;
                }
            }
        }

        private static readonly List<Entry> _entries = new List<Entry>();

        // WHERE THE MENU IS, as a path rather than a single name.
        //
        // This was one string, which made the menu exactly one level deep: a config label of
        // "Admin/Spawn/Add Ward" produced a group called "Admin" holding a leaf literally named
        // "Spawn/Add Ward". The operator asked for a Spawn subtree under Admin and for an X that
        // "if the menu is two levels deep, it goes back to previous menu", so depth is now real.
        //
        // Two fields, not a List<string>, because the spec has exactly two levels and this is read
        // on the ring rebuild path - twice a frame. Nothing here allocates.
        private static string _l1 = "";   // level-1 group, "" = the top strip
        private static string _l2 = "";   // level-2 subgroup, "" = none
        private static readonly List<string> _groupsBuf = new List<string>();
        private static readonly List<Entry> _visibleBuf = new List<Entry>();

        private static int Depth() { return _l2.Length != 0 ? 2 : (_l1.Length != 0 ? 1 : 0); }

        // Called ONLY on a navigation press, never on the rebuild path: it concatenates, and this
        // file has had three separate frame-rate incidents from putting a lookup or an allocation on
        // that path (the last is documented at PhysicalHand, :346-349). The page the ring draws from
        // is _page, a plain int.
        private static string LevelKey()
        {
            if (_l1.Length == 0) return "";
            return _l2.Length == 0 ? _l1 : _l1 + "/" + _l2;
        }

        private static string LevelName()
        {
            string key = LevelKey();
            return key.Length == 0 ? "the top strip" : key;
        }

        // WHICH PAGE EACH LEVEL WAS LEFT ON.
        //
        // Both entering a group and backing out of one used to reset the page to 1, so coming back
        // out of Admin dropped him at the top of a strip he had paged away from, and going back INTO
        // Admin made him page to Save again. Keyed by the level path, not by depth, so two sibling
        // groups do not share one remembered page. Read and written on navigation presses only.
        //
        // The deliberate consequence: re-entering a group RESUMES its page, so Admin can open on
        // page 2. That is visible rather than mysterious - the compact control's label says
        // "PG2OF2" - and it is the annoyance he actually reported.
        private static readonly Dictionary<string, int> _pageAt = new Dictionary<string, int>();

        private static void Remember() { _pageAt[LevelKey()] = _page; }

        private static int Recall()
        {
            int page;
            return _pageAt.TryGetValue(LevelKey(), out page) ? page : 0;
        }

        // Admin gating, evaluated FIRST and unconditionally - see the comment in VisibleEntries.
        // AdminCheck.IsAdmin() caches its verdict for two seconds (AdminCheck.cs:38-40), so asking
        // it once for the doors and once for the entries costs one dictionary-free bool read.
        private static bool AdminAllowed()
        {
            bool detected = AdminCheck.IsAdmin();
            return !NeuralyzeVRFixesPlugin.HideAdminEntries.Value || detected;
        }

        private static bool Reachable(Entry e, bool admin)
        {
            // Availability is NOT overridable and is checked first: an entry whose prefab or command
            // does not exist here cannot work for an admin either.
            if (!ActionAvailability.Offer(e)) return false;
            if (e.When == "admin") return admin;
            return MenuContext.Active(e.When);
        }

        // The doors offered at the CURRENT level, in config order.
        //
        // A door is offered only when something is behind it right now, so a non-admin never sees an
        // "Admin >" door with nothing behind it - and, since 2026-08-25, never sees a door whose
        // whole contents were withheld for having no backing content either. Nesting made that a
        // two-level question: "Admin >" must survive when every DIRECT Admin entry is withheld but
        // something under Admin/Spawn is not, which is why the top-level pass looks at e.Group for
        // every entry regardless of its Sub.
        //
        // Refresh is handed the WHOLE list, never the current level's subset, so the one
        // Message-level withheld report is complete the first time it prints instead of filling in
        // as the operator wanders into groups.
        private static List<string> Doors()
        {
            bool admin = AdminAllowed();
            ActionAvailability.Refresh(_entries);
            List<string> doors = _groupsBuf;
            doors.Clear();
            if (_l2.Length != 0) return doors;   // depth two is the floor; it has no doors
            bool top = _l1.Length == 0;
            foreach (Entry e in _entries)
            {
                string door = top ? e.Group : (e.Group == _l1 ? e.Sub : "");
                if (door.Length == 0 || doors.Contains(door)) continue;
                if (!Reachable(e, admin)) continue;
                doors.Add(door);
            }
            return doors;
        }

        // Kinds that only make sense for an admin. Gated on ZNet.LocalPlayerIsAdminOrHost(), the
        // vanilla check the game itself uses, so a non-admin never sees a button that would be
        // refused. Evaluated per rebuild rather than cached: admin status arrives from the server
        // over RPC_AdminList after connect, so a value read at load time would be wrong.
        private static readonly HashSet<string> AdminKinds = new HashSet<string> { "console", "cmd" };
        private static MethodInfo _isAdmin;
        private static MethodInfo _serverSyncAdmin;
        private static PropertyInfo _serverSyncInstance, _serverSyncAdminProp;
        private static bool _adminResolved;
        private static int _lastAdminState = -1;

        // Admin status is read from more than one source and re-evaluated on every rebuild.
        //
        // ZNet.LocalPlayerIsAdminOrHost() is the vanilla check, but on a client it depends on the
        // admin list having arrived over RPC_AdminList, and a measured session logged admin=False on
        // a player who IS in the server's adminlist - which hid the console entirely.
        //
        // So: OR together every signal available, and if NONE of them resolves, fail OPEN. These
        // buttons only ATTEMPT a command; the server refuses a non-admin regardless, so a visible
        // button that gets refused is strictly better than a hidden button an admin needs.
        private static bool LocalPlayerIsAdmin()
        {
            if (!_adminResolved)
            {
                _adminResolved = true;
                Type znet = TypeCache.Get("ZNet");
                _isAdmin = znet == null ? null : AccessTools.Method(znet, "LocalPlayerIsAdminOrHost");

                // ServerSync is bundled by most server-aware mods and syncs admin state to clients,
                // so it is often correct when the vanilla check is not yet populated.
                Type sync = TypeCache.Get("ServerSync.SynchronizationManager");
                if (sync != null)
                {
                    _serverSyncInstance = sync.GetProperty("Instance", BindingFlags.Static | BindingFlags.Public);
                    _serverSyncAdminProp = sync.GetProperty("PlayerIsAdmin", BindingFlags.Instance | BindingFlags.Public);
                }
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "admin signals: ZNet=" + (_isAdmin != null) + " ServerSync=" + (_serverSyncAdminProp != null));
            }

            bool any = false;
            if (_isAdmin != null)
            {
                any = true;
                try { if (Convert.ToBoolean(_isAdmin.Invoke(null, null))) return true; } catch { }
            }
            if (_serverSyncInstance != null && _serverSyncAdminProp != null)
            {
                any = true;
                try
                {
                    object inst = _serverSyncInstance.GetValue(null, null);
                    if (inst != null && Convert.ToBoolean(_serverSyncAdminProp.GetValue(inst, null))) return true;
                }
                catch { }
            }
            return !any;   // nothing to ask -> show, and let the server decide
        }

        // The list the menu actually pages through: admin entries removed for non-admins.
        private static List<Entry> VisibleEntries()
        {
            // Gating is opt-in: see HideAdminEntriesForNonAdmins. Detection demonstrably lies on a
            // client, and hiding the admin console from an admin is a worse failure than showing a
            // button the server will refuse.
            // Evaluate the check FIRST, unconditionally. Writing this as
            //     !HideAdminEntries.Value || AdminCheck.IsAdmin()
            // short-circuited, so with hiding disabled the check never ran and never logged - which
            // meant the digit-comparison fix could not be confirmed from a session at all. The
            // detection and the decision to act on it are separate concerns.
            bool detected = AdminCheck.IsAdmin();
            bool admin = !NeuralyzeVRFixesPlugin.HideAdminEntries.Value || detected;
            ActionAvailability.Refresh(_entries);
            List<Entry> visible = _visibleBuf;
            visible.Clear();
            foreach (Entry e in _entries)
            {
                // "admin" is the one predicate that stays overridable, because its detection was
                // wrong for three releases and hiding the console from its own admin is worse than
                // showing a button the server refuses.
                if (e.Group != _l1 || e.Sub != _l2) continue;
                // Availability is NOT overridable, and is checked before the admin gate: an entry
                // whose prefab or command does not exist here cannot work for an admin either, and
                // "it is present but does nothing" is the 2026-08-25 complaint itself.
                if (!ActionAvailability.Offer(e)) continue;
                if (e.When == "admin") { if (admin) visible.Add(e); continue; }
                if (MenuContext.Active(e.When)) visible.Add(e);
            }
            int state = admin ? 1 : 0;
            if (_lastAdminState != state)
            {
                _lastAdminState = state;
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "misc menu: admin=" + admin + ", showing " + visible.Count + " of " + _entries.Count
                    + " entries (re-evaluated each rebuild; admin arrives over RPC after connect)");
            }
            return visible;
        }
        private static bool _open, _placedOnce;
        private static int _page;

        // THE WRIST STRIP, AND HOW MUCH OF IT IS OURS.
        //
        // VHVR gives eight extraElements - QuickAbstract.initialize allocates them with `newarr 8`
        // and lays them out as two rows of four at a fixed 0.05m pitch - and that is the strip shape
        // the operator already knows, so the FOOTPRINT count stays eight. What changed on
        // 2026-08-25 is that the eight is an array length, not a layout law: the extra-element loop
        // in reorderElements ends `ldc.i4.8 / blt` - a literal, verified by Cecil against BOTH
        // ValheimVRMod.dll builds on this machine: md5 0beed51d, which build.sh references from the
        // Vangard profile, and md5 69a2644f, which is the one the operator's Hrafnheim profile
        // actually loads. Exactly one ldc.i4.8 in that method in each - so VHVR never activates,
        // positions or
        // deactivates an index at or above 8. Those indices are ours alone once the array is
        // longer, and extraElements has exactly ONE stfld in the whole assembly, in Awake, so a
        // grown array stays grown with no per-frame work to sustain it.
        //
        // hoverItem and selectHoveredItem bound on extraElementCount, not on 8, so they DO reach
        // the grown indices. That is the whole trick: the array carries ten elements, the strip
        // shows eight footprints, and the three navigation elements share the leftmost one.
        private const int NavSlots = 3;        // X, <, >
        private const int ContentCells = 7;    // footprints left for doors and actions
        private const int RingCells = ContentCells + 1;
        private const int RingSlots = NavSlots + ContentCells;

        // VHVR's own pitch and row drop, taken from the formula in reorderElements. Not a taste
        // choice: matching them is what makes our re-layout indistinguishable from the strip VHVR
        // draws beside it.
        private const float Pitch = 0.05f;
        private const float RowDrop = -0.05f;

        // THREE BUTTONS INSIDE ONE BUTTON'S SPACE. The pitch is a third of a slot's and the scale
        // matches it, so the triple occupies exactly the footprint one ordinary button would.
        //
        // VHVR's hit test separates them for free: hoverItem takes the NEAREST extraElement whose
        // distance to the hand is under 0.05, so three elements 0.0167 apart all qualify and the
        // closest wins. Each therefore gets a third of a slot's catchment, which is what a
        // third-sized button should get. Nothing had to be subdivided - the elements were always
        // independently selectable - and the only reason this read as impossible is that
        // reorderElements sets localPosition and never localScale, so nobody had written the scale.
        private const float NavPitch = Pitch / 3f;
        private const float NavScale = 1f / 3f;

        // How many content footprints a page gets. ContentCells whenever the strip is the size it
        // should be, and the navigation shares one footprint whether it draws one button or three,
        // so a page never trades content for arrows.
        //
        // Derived from the LIVE array length rather than asserted, because Grow can decline - a
        // clone that carries no QuickMenuItem leaves the strip at eight. Asserting seven then would
        // page as though seven fitted while only five were drawn, and the two items in between would
        // exist on no page at all: unreachable, unmentioned, and indistinguishable from a button that
        // does nothing. NavSlots is reserved unconditionally, not `nav`, because the page count has
        // to be decided before the arrows are known and the worst case is the one that must fit.
        //
        // The arrows are OMITTED on a single-page level rather than drawn inert. There is nowhere to
        // page to, and a control that silently does nothing is the complaint this file spent
        // 2026-08-25 answering. They cost no content space either way, so omitting them buys nothing
        // and hides nothing - it only refuses to draw a lie. X does not move for it: X is the left
        // third of the leftmost footprint at every depth, on every page, arrows or not.
        private static int PerPage(int arrayLength)
        {
            int room = arrayLength - NavSlots;
            if (room > ContentCells) room = ContentCells;
            return room < 1 ? 1 : room;
        }

        // How many pages the level being drawn has, as of the last rebuild. The arrow callbacks need
        // it - they must not page past the end - and they run from selectHoveredItem during Update,
        // after a rebuild in the same frame, so it is fresh.
        private static int _pagesNow = 1;
        private static bool _logOpenPending;

        private static bool Open()
        {
            _open = true;
            _l1 = "";
            _l2 = "";
            _page = Recall();
            _logOpenPending = true;
            return true;
        }

        private static void Enter(string name)
        {
            Remember();
            if (_l1.Length == 0) _l1 = name; else _l2 = name;
            _page = Recall();
        }

        // THE X BUTTON: "always closes the current menu level. if the menu is two levels deep, it
        // goes back to previous menu. if it was on top level, then it closes the wrist strip menu
        // alltogether." - the operator, 2026-08-25.
        //
        // Level semantics, deliberately not a history stack: the level you are on is the only thing
        // it needs, so there is no way for it to walk somewhere you were five minutes ago.
        private static bool CloseLevel()
        {
            Remember();
            if (_l2.Length != 0) _l2 = "";
            else if (_l1.Length != 0) _l1 = "";
            else { _open = false; return true; }
            _page = Recall();
            return true;
        }

        private static bool PageBy(int step)
        {
            int pages = _pagesNow < 1 ? 1 : _pagesNow;
            _page = (_page + step + pages) % pages;
            return true;
        }

        private static bool NextPage() { return PageBy(1); }
        private static bool PrevPage() { return PageBy(-1); }

        // Cached because Assign is called on every rebuild and a method-group conversion allocates a
        // delegate at each conversion site; :614 records what twenty of those a frame cost.
        private static readonly Func<bool> _actOpen = Open;
        private static readonly Func<bool> _actNextPage = NextPage;
        private static readonly Func<bool> _actPrevPage = PrevPage;
        private static readonly Func<bool> _actCloseLevel = CloseLevel;
        private static readonly Dictionary<string, Func<bool>> _doorActions = new Dictionary<string, Func<bool>>();

        // The closure captures the NAME only; which level it opens is decided at press time from the
        // live path (Enter). It has to be that way round, because Assign caches delegates by LABEL
        // (:617) - so a subgroup sharing a name with a top-level group would otherwise be handed a
        // closure built for the other one.
        private static Func<bool> DoorAction(string name)
        {
            Func<bool> action;
            if (_doorActions.TryGetValue(name, out action)) return action;
            string captured = name;
            action = delegate { Enter(captured); return true; };
            _doorActions[name] = action;
            return action;
        }

        // WHAT THE X SAYS IT WILL DO. CLOSE at the top strip, BACK inside a level, because that is
        // what the same press does there.
        //
        // No "X " prefix on it, and that is a legibility decision with arithmetic behind it. These
        // three buttons draw at a third scale, and MiscLabels sizes its pixel font from the longest
        // line: scale = (256 - 28) / (chars * 6 - 1), capped by the line count (MiscLabels.cs:189-193).
        // "X CLOSE" is seven characters and renders at scale 5 - 35px glyphs in a 256px texture,
        // then shrunk to a third - while "CLOSE" renders at 7, "BACK" at 9, and the single-character
        // "<" and ">" at the 32 cap, filling their buttons. Halving the glyph size of the one
        // control that has to teach itself would defeat the point of labelling it at all. The "X" in
        // the request named the function, not the character.
        private const string NavPrev = "<";
        private const string NavNext = ">";

        private static int _closeDepth = -1;
        private static string _closeLabel = "CLOSE";

        private static string CloseLabel()
        {
            int depth = Depth();
            if (depth != _closeDepth)
            {
                _closeDepth = depth;
                _closeLabel = depth == 0 ? "CLOSE" : "BACK";
            }
            return _closeLabel;
        }

        private static int OfferedCount()
        {
            int offered = 0;
            foreach (Entry e in _entries) if (ActionAvailability.Offer(e)) offered++;
            return offered;
        }

        private static int _doorCount = -1;
        private static string _doorLabel = "Misc";

        // The one slot appended to VHVR's own strip while our menu is CLOSED. It is the door in, not
        // a navigation slot - it costs nothing while the menu is open - so it stays.
        //
        // Its count is every action the strip can still reach at any depth, which is the number the
        // open log line reports. No parentheses: '(' and ')' are not in the glyph table, so
        // "Misc (8)" drew as MISC ?8?.
        private static string DoorLabel()
        {
            int n = OfferedCount();
            if (n != _doorCount)
            {
                _doorCount = n;
                _doorLabel = "Misc " + n;
            }
            return _doorLabel;
        }

        internal static int Count { get { return _entries.Count; } }

        // Format: Label = kind:value, comma separated. Deliberately data-driven so adding a
        // mod means adding a line, not editing code.
        internal static void Load(string spec)
        {
            _entries.Clear();
            // Verdicts belong to the list that was measured, so a re-read of Actions discards them.
            ActionAvailability.Reset();
            // An empty Actions string is now simply an empty ring. The built-in "Reset Height"
            // entry that used to be appended below is gone: height is re-measured without a wrist
            // entry, by the SteamVR recenter hook and by the sit transition (both in
            // DirectActions.cs - SystemRecenter and SitRecenter), which is what the operator
            // confirmed live on 2026-08-19, so nothing has to survive a cleared config any more.
            // "".Split(',') yields one empty item, which the loop skips, and BeforeReorderTimed
            // bails on zero entries, so the ring simply does not appear.
            if (spec == null) spec = "";
            foreach (string raw in spec.Split(','))
            {
                string item = raw.Trim();
                if (item.Length == 0) continue;
                int eq = item.IndexOf('=');
                if (eq <= 0) continue;
                string label = item.Substring(0, eq).Trim();
                string action = item.Substring(eq + 1).Trim();
                int colon = action.IndexOf(':');
                if (colon <= 0) continue;
                string kind = action.Substring(0, colon).Trim().ToLowerInvariant();
                string value = action.Substring(colon + 1).Trim();

                // Optional trailing "when:<predicate>" makes the entry contextual, so the ring only
                // shows what is usable right now: "Quick Stack = azu:quickstack when:container".
                string when = "";
                int whenAt = value.LastIndexOf(" when:", StringComparison.OrdinalIgnoreCase);
                if (whenAt >= 0)
                {
                    when = value.Substring(whenAt + 6).Trim().ToLowerInvariant();
                    value = value.Substring(0, whenAt).Trim();
                }
                // "Group/Label" nests the entry one ring deeper and "Group/Sub/Label" two, which is
                // what makes Admin/Spawn/Add Ward a leaf inside Spawn inside Admin. Eleven flat
                // admin commands buried the everyday entries behind a "More >" page; grouping is
                // what a menu is for, and nineteen of them needed a second level.
                //
                // Two separators is the floor, because the navigation has exactly three levels to
                // close through. A deeper label is REFUSED and named in the log rather than
                // flattened or truncated: both of those put the operator's button somewhere he did
                // not ask for and say nothing about it, and a button silently somewhere else is the
                // same class of mystery as a button that silently does nothing
                // (ActionAvailability.cs:54-58). An empty segment - "Admin//Ward" - is refused for
                // the same reason: it names a level with no name.
                string group = "", sub = "";
                if (label.IndexOf('/') >= 0)
                {
                    string[] parts = label.Split('/');
                    if (parts.Length > 3)
                    {
                        NeuralyzeVRFixesPlugin.Log.LogMessage(NeuralyzeVRFixesPlugin.Tag
                            + "wrist menu REFUSED '" + item + "': a label may nest at most two levels"
                            + " deep (Group/Sub/Label) and that one has " + (parts.Length - 1)
                            + " separators. It was not flattened or truncated - rename it or drop a"
                            + " level.");
                        continue;
                    }
                    bool empty = false;
                    for (int p = 0; p < parts.Length; p++)
                    {
                        parts[p] = parts[p].Trim();
                        if (parts[p].Length == 0) empty = true;
                    }
                    if (empty)
                    {
                        NeuralyzeVRFixesPlugin.Log.LogMessage(NeuralyzeVRFixesPlugin.Tag
                            + "wrist menu REFUSED '" + item + "': a label path has an empty segment,"
                            + " so it names a group or a button with no name.");
                        continue;
                    }
                    group = parts[0];
                    if (parts.Length == 3) { sub = parts[1]; label = parts[2]; }
                    else label = parts[1];
                }
                if (label.Length == 0 || value.Length == 0) continue;
                if (kind != "zinput" && kind != "key" && kind != "hold" && kind != "console"
                    && kind != "emote" && kind != "zoom" && kind != "chat" && kind != "power"
                    && kind != "panel" && kind != "cmd" && kind != "ui"
                    && kind != "mount" && kind != "sail") continue;
                // console and cmd are admin surfaces by nature; an explicit when: still wins.
                if (when.Length == 0 && (kind == "console" || kind == "cmd")) when = "admin";
                _entries.Add(new Entry { Label = label, Group = group, Sub = sub, Kind = kind, Value = value, When = when });
            }

            var contexts = new List<string>();
            foreach (Entry e in _entries)
            {
                string tag = e.When.Length == 0 ? "always" : e.When;
                if (!contexts.Contains(tag)) contexts.Add(tag);
            }
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                + "misc menu loaded " + _entries.Count + " action(s); contexts used: "
                + string.Join(", ", contexts.ToArray()));
            // Draw every label now, at load, instead of on first open.
            //
            // Measured: the first frame the ring appeared cost 21.35ms - one and a half frames at
            // 72Hz, a visible hitch - because each label's text is rendered into a texture on first
            // use. Doing it here moves that cost to the loading screen where nobody feels it.
            int warmed = 0;
            foreach (Entry e in _entries) { MiscLabels.For(e.Label); warmed++; }
            // Doors, at whatever depth they appear. A group and a subgroup that share a name share
            // one sprite, which is correct - the label is the same.
            var seenDoors = new List<string>();
            foreach (Entry e in _entries)
            {
                if (e.Group.Length > 0 && !seenDoors.Contains(e.Group))
                {
                    seenDoors.Add(e.Group);
                    MiscLabels.For(e.Group + " >");
                    warmed++;
                }
                if (e.Sub.Length > 0 && !seenDoors.Contains(e.Sub))
                {
                    seenDoors.Add(e.Sub);
                    MiscLabels.For(e.Sub + " >");
                    warmed++;
                }
            }
            // The navigation labels are a closed set now - four strings, no page number in any of
            // them - so the whole set is drawn here and nothing is ever rendered mid-session.
            foreach (string fixedLabel in new string[] { "CLOSE", "BACK", NavPrev, NavNext,
                                                         "Misc " + _entries.Count })
            {
                MiscLabels.For(fixedLabel);
                warmed++;
            }
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                + "misc menu: " + warmed + " label sprites pre-drawn at load");

        }


        internal static void Install(Harmony harmony)
        {
            try
            {
                Type qa = TypeCache.Get("ValheimVRMod.Scripts.QuickAbstract");
                if (qa == null)
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "QuickAbstract not found; misc menu unavailable");
                    return;
                }
                // Must be a PREFIX on reorderElements, not a postfix on refreshItems:
                // refreshItems calls reorderElements as its LAST statement, and
                // reorderElements is what activates and positions slots using
                // extraElementCount. Appending after refreshItems returns therefore raised
                // the count too late every single frame, so the entry was never activated.
                // reorderElements is defined on the base class, so one patch covers both hands.
                //
                // And a POSTFIX as well, because the two do opposite halves of one job. The prefix
                // decides WHAT is on the strip - it writes extraElementCount, which is the only
                // thing reorderElements consults - and the postfix decides WHERE, because
                // reorderElements overwrites localPosition on every slot it owns, so any layout
                // written before it runs is thrown away. Packing the navigation triple into one
                // footprint is therefore only expressible after the fact.
                MethodInfo pre = typeof(MiscMenu).GetMethod("BeforeReorder", BindingFlags.Static | BindingFlags.NonPublic);
                MethodInfo post = typeof(MiscMenu).GetMethod("AfterReorder", BindingFlags.Static | BindingFlags.NonPublic);
                MethodInfo reorder = AccessTools.Method(qa, "reorderElements");
                if (reorder == null)
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "QuickAbstract.reorderElements not found; misc menu unavailable");
                    return;
                }
                harmony.Patch(reorder, prefix: new HarmonyMethod(pre), postfix: new HarmonyMethod(post));
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "misc menu hooked into QuickAbstract.reorderElements (prefix populates, postfix"
                    + " packs the navigation triple into one footprint)");

                // And hoverItem, for the highlight. Separate from the pair above because it is a
                // separate object with a separate owner: reorderElements lays out the ELEMENTS,
                // hoverItem moves the one shared hoveredItem sprite. See AfterHover for why the
                // scale cannot be written from the layout postfix.
                MethodInfo hover = AccessTools.Method(qa, "hoverItem");
                MethodInfo afterHover = typeof(MiscMenu).GetMethod("AfterHover", BindingFlags.Static | BindingFlags.NonPublic);
                if (hover == null)
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "QuickAbstract.hoverItem not found; the navigation buttons keep VHVR's"
                        + " full-size hover highlight");
                }
                else
                {
                    harmony.Patch(hover, postfix: new HarmonyMethod(afterHover));
                    NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                        + "misc menu hooked into QuickAbstract.hoverItem (sizes the shared hover"
                        + " highlight to the button under the finger)");
                }
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "misc menu install failed: " + e.Message);
            }
        }

        // Right, Left or Both. Anything unrecognised is treated as Right rather than
        // silently showing on both wrists, which is the behaviour being fixed.
        private static bool WantedHand(object instance)
        {
            string want = NeuralyzeVRFixesPlugin.MiscMenuHand == null
                ? "Right" : (NeuralyzeVRFixesPlugin.MiscMenuHand.Value ?? "Right");
            if (want.Equals("Both", StringComparison.OrdinalIgnoreCase)) return true;

            string hand = PhysicalHand(instance);
            if (hand.Length == 0)
            {
                // Last resort only. The class name is a role, not a controller: with MenuHand=Right
                // and RightHandQuickMenu.handTransform returning VRPlayer.rightHand, the ring still
                // appeared on the player's LEFT wrist - so this mapping is not trustworthy and is
                // kept only so the entry exists at all when SteamVR cannot be asked.
                string type = instance == null ? "" : instance.GetType().Name;
                bool isLeft = type.IndexOf("Left", StringComparison.OrdinalIgnoreCase) >= 0;
                return want.Equals("Left", StringComparison.OrdinalIgnoreCase) ? isLeft : !isLeft;
            }
            return hand.Equals(want, StringComparison.OrdinalIgnoreCase);
        }

        // Which controller this menu is really parented to, asked of SteamVR rather than inferred.
        // Hand.handType is the device the interaction system bound, so it survives whatever VHVR
        // assigned to leftHand/rightHand.
        private static readonly Dictionary<Type, string> _handByType = new Dictionary<Type, string>();

        private static string PhysicalHand(object instance)
        {
            if (instance == null) return "";
            // Resolved ONCE per menu class. The first version of this ran TypeByName - which walks
            // every loaded assembly - on every rebuild, i.e. twice a frame with 115 plugins loaded.
            // That was the frame rate the player reported, and it is the third time a name-based
            // lookup has landed on a hot path in this file.
            string cached;
            if (_handByType.TryGetValue(instance.GetType(), out cached)) return cached;
            string answer = ResolveHand(instance);
            _handByType[instance.GetType()] = answer;
            return answer;
        }

        private static string ResolveHand(object instance)
        {
            try
            {
                PropertyInfo prop = instance.GetType().GetProperty("handTransform",
                    BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public | BindingFlags.FlattenHierarchy);
                Type walk = instance.GetType();
                while (prop == null && walk != null)
                {
                    prop = walk.GetProperty("handTransform",
                        BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
                    walk = walk.BaseType;
                }
                object t = prop == null ? null : prop.GetValue(instance, null);
                if (t == null) return "";

                Type handClass = TypeCache.Get("Valve.VR.InteractionSystem.Hand");
                if (handClass == null) return "";
                MethodInfo inParent = AccessTools.Method(typeof(Component), "GetComponentInParent", new Type[] { typeof(Type) });
                object hand = inParent == null ? null : inParent.Invoke(t, new object[] { handClass });
                if (hand == null) return "";

                object src = null;
                FieldInfo f = AccessTools.Field(handClass, "handType");
                if (f != null) src = f.GetValue(hand);
                if (src == null)
                {
                    PropertyInfo hp = handClass.GetProperty("handType");
                    if (hp != null) src = hp.GetValue(hand, null);
                }
                string name = Convert.ToString(src);
                string resolved = name.IndexOf("Left", StringComparison.OrdinalIgnoreCase) >= 0 ? "Left"
                                : name.IndexOf("Right", StringComparison.OrdinalIgnoreCase) >= 0 ? "Right" : "";

                string key = instance.GetType().Name;
                if (resolved.Length > 0 && !_handLogged.Contains(key))
                {
                    _handLogged.Add(key);
                    NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                        + "misc menu: " + key + " sits on the " + resolved + " controller (SteamVR handType="
                        + name + ")");
                }
                return resolved;
            }
            catch { return ""; }
        }

        private static readonly HashSet<string> _handLogged = new HashSet<string>();
        private static int _whereLogged;

        // Where the strip actually renders, in metres from each hand.
        //
        // Every name in this chain agreed on "right" while the player watched the ring appear on
        // their LEFT wrist, so names are no longer evidence: this measures the rendered position
        // against the two hand bones and reports both distances.
        private static string Where(object instance)
        {
            try
            {
                Component c = instance as Component;
                Type vrp = TypeCache.Get("ValheimVRMod.VRCore.VRPlayer");
                if (c == null || vrp == null) return "";
                PropertyInfo lp = vrp.GetProperty("leftHandBone", BindingFlags.Static | BindingFlags.Public);
                PropertyInfo rp = vrp.GetProperty("rightHandBone", BindingFlags.Static | BindingFlags.Public);
                Transform l = lp == null ? null : lp.GetValue(null, null) as Transform;
                Transform r = rp == null ? null : rp.GetValue(null, null) as Transform;
                if (l == null || r == null) return "";
                float dl = Vector3.Distance(c.transform.position, l.position);
                float dr = Vector3.Distance(c.transform.position, r.position);
                return "; rendered " + dl.ToString("F2") + "m from the LEFT hand and "
                     + dr.ToString("F2") + "m from the RIGHT hand -> physically "
                     + (dl < dr ? "LEFT" : "RIGHT");
            }
            catch { return ""; }
        }

        private static FieldInfo _extraField, _countField;
        private static MethodInfo _useAsQuickAction, _useAsNoOp;
        private static Type _callbackType;

        private static bool Resolve(object menu)
        {
            if (_useAsQuickAction != null) return true;
            Type qa = TypeCache.Get("ValheimVRMod.Scripts.QuickAbstract");
            if (qa == null) return false;
            _extraField = AccessTools.Field(qa, "extraElements");
            // reorderElements() deactivates every slot at or beyond extraElementCount, and
            // hoverItem() only scans below it. Writing into an unused slot therefore produces
            // an item that is invisible AND unselectable - it must be counted, not just filled.
            _countField = AccessTools.Field(qa, "extraElementCount");
            Type item = AccessTools.Inner(qa, "QuickMenuItem");
            if (item == null || _extraField == null) return false;
            _callbackType = AccessTools.Inner(item, "QuickMenuItemCallback");
            _useAsQuickAction = AccessTools.Method(item, "useAsQuickAction");
            _useAsNoOp = AccessTools.Method(item, "useAsNoOp");
            return _useAsQuickAction != null && _callbackType != null && _countField != null;
        }

        // VHVR fills extraElements first; we take whatever it left as no-ops. When the misc
        // page is open we own the whole ring, which is what gives the second level.
        private static void BeforeReorder(object __instance)
        {
            long _t = HookProfiler.Start();
            try { BeforeReorderBody(__instance); } finally { HookProfiler.Stop(HookProfiler.Misc, _t); }
        }

        private static long _tPhase;
        private static double _msVisible, _msGroups, _msAssign, _msLabels;
        private static float _lastBreakdown;

        private static void PhaseStart() { _tPhase = System.Diagnostics.Stopwatch.GetTimestamp(); }
        private static double PhaseEnd()
        {
            return (System.Diagnostics.Stopwatch.GetTimestamp() - _tPhase) * 1000.0 / System.Diagnostics.Stopwatch.Frequency;
        }

        private static void BeforeReorderBody(object __instance)
        {
            long callStart = System.Diagnostics.Stopwatch.GetTimestamp();
            _msVisible = _msGroups = _msAssign = _msLabels = 0;
            try { BeforeReorderTimed(__instance); }
            finally
            {
                double ms = (System.Diagnostics.Stopwatch.GetTimestamp() - callStart) * 1000.0 / System.Diagnostics.Stopwatch.Frequency;
                if (ms > 5.0 && Time.realtimeSinceStartup - _lastBreakdown > 5f)
                {
                    _lastBreakdown = Time.realtimeSinceStartup;
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "misc ring rebuild took " + ms.ToString("F1") + "ms: visibleEntries="
                        + _msVisible.ToString("F1") + " groups=" + _msGroups.ToString("F1")
                        + " assign=" + _msAssign.ToString("F1") + " labels=" + _msLabels.ToString("F1")
                        + " open=" + _open + " level='" + LevelKey() + "'");
                }
            }
        }

        private static void BeforeReorderTimed(object __instance)
        {
            if (!NeuralyzeVRFixesPlugin.MiscMenuEnabled.Value) return;
            if (_entries.Count == 0) return;
            // reorderElements is defined on QuickAbstract, so a single patch fires for both
            // wrists and the entry appeared twice. VHVR's two subclasses are the only way to
            // tell them apart at this point - RightHandQuickMenu and LeftHandQuickMenu - so
            // the configured hand is matched by type name. Note this is the physical hand,
            // not VHVR's QuickActionOnLeftHand swap: the entry should stay put when that
            // setting changes which strip holds the quick bar.
            if (!WantedHand(__instance)) return;
            try
            {
                if (!Resolve(__instance)) return;
                Array extra = _extraField.GetValue(__instance) as Array;
                if (extra == null || extra.Length == 0) return;

                int vhvrCount = Convert.ToInt32(_countField.GetValue(__instance));

                // Measured while OPEN, when the strip is parented and positioned. Logged at load it
                // read "524m from both hands" - a number with no meaning, which is worse than none.
                if (_open && _whereLogged < 2)
                {
                    _whereLogged++;
                    NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                        + "misc ring " + __instance.GetType().Name + Where(__instance));
                }

                // The postfix acts only on the call its own prefix claimed, so a second hand whose
                // prefix bailed on WantedHand cannot be laid out with this hand's numbers.
                _layoutOwner = __instance;
                _layoutFrame = Time.frameCount;

                if (!_open)
                {
                    // Append after VHVR's own entries and extend the count so reorderElements
                    // activates and positions it, and hoverItem can reach it.
                    //
                    // Capped at VHVR's own eight even when the array is longer: everything above
                    // that index is positioned by us, and the closed strip is VHVR's layout, not
                    // ours. There is nothing to gain from a ninth slot on a strip we do not own.
                    _layoutNav = 0;
                    int room = extra.Length < VhvrSlots ? extra.Length : VhvrSlots;
                    if (vhvrCount >= room) return;
                    Assign(extra.GetValue(vhvrCount), DoorLabel(), _actOpen);
                    _countField.SetValue(__instance, vhvrCount + 1);
                    if (!_placedOnce)
                    {
                        _placedOnce = true;
                        NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                            + "misc entry appended at wrist slot " + vhvrCount + " of " + room
                            + " (VHVR used " + vhvrCount + "); it is the LAST item on that wrist strip"
                            + " - reach your other hand to it");
                    }
                    return;
                }

                // Ten elements for eight footprints, grown once. See the NavSlots comment above for
                // why this is permanent and why VHVR will not fight it.
                if (extra.Length < RingSlots) extra = Grow(__instance, extra);
                int max = extra.Length;

                // Page open: own the whole strip. refreshItems repopulates it every call, so
                // closing the page self-heals without us restoring anything.
                //
                // ONE ordered run per level - doors first, then entries - paged as a single list.
                // Doors used to be appended after the entries of every page, which meant that with
                // eight top-level entries and six content slots the "Admin >" door existed only on
                // page 2: a door that moves depending on how full the page happens to be. First is
                // a place. It also means the page count finally covers everything the level offers,
                // so a level whose entries were withheld cannot leave a page with nothing on it.
                PhaseStart();
                List<Entry> entries = VisibleEntries();
                _msVisible += PhaseEnd();
                PhaseStart();
                List<string> doors = Doors();
                _msGroups += PhaseEnd();

                int perPage = PerPage(max);
                int total = doors.Count + entries.Count;
                int pages = total <= perPage ? 1 : (total + perPage - 1) / perPage;
                if (_page >= pages || _page < 0) _page = 0;
                _pagesNow = pages;

                // THREE BUTTONS, THREE CALLBACKS, ONE FOOTPRINT. They are the first elements in the
                // array, which is what "always lives at the left" means: the postfix packs
                // 0..nav-1 into the leftmost footprint and gives X its left third at every depth,
                // on every page, whether the arrows are drawn or not.
                //
                // Contiguous from zero on purpose. extraElementCount is the only bound hoverItem
                // has, and it does not check whether an element is active - so an element inside the
                // counted range that we had hidden would keep answering the nearest-wins test from
                // wherever we left it. A hidden button that still takes presses is worse than a
                // visible one that does nothing, so the arrows are absent from the COUNT on a
                // single-page level, not merely switched off.
                int nav = pages > 1 ? NavSlots : 1;
                Assign(extra.GetValue(0), CloseLabel(), _actCloseLevel);
                if (nav == NavSlots)
                {
                    Assign(extra.GetValue(1), NavPrev, _actPrevPage);
                    Assign(extra.GetValue(2), NavNext, _actNextPage);
                }
                int used = nav;

                int start = _page * perPage;
                for (int i = start; i < total && used < max && i - start < perPage; i++)
                {
                    object slot = extra.GetValue(used);
                    if (i < doors.Count)
                    {
                        string name = doors[i];
                        Assign(slot, name + " >", DoorAction(name));
                    }
                    else
                    {
                        Entry e = entries[i - doors.Count];
                        Assign(slot, e.Label, e.Execute);
                    }
                    used++;
                }

                _layoutNav = nav;
                _layoutUsed = used;

                // One line per open, at Message level because that is the client's log floor
                // (BepInEx.cfg LogLevels stops at Message, so LogInfo is invisible to him), naming
                // where the menu is and what it decided to offer. A confusing menu is then
                // diagnosable from the log he already sends instead of from another session.
                if (_logOpenPending)
                {
                    _logOpenPending = false;
                    int offered = OfferedCount();
                    NeuralyzeVRFixesPlugin.Log.LogMessage(NeuralyzeVRFixesPlugin.Tag
                        + "wrist menu open at depth " + Depth() + " (" + LevelName() + "), page "
                        + (_page + 1) + " of " + pages + ", " + perPage + " content slots per page: "
                        + doors.Count + " subgroup door(s) and " + entries.Count
                        + " action(s) on this level. Of " + _entries.Count + " configured actions "
                        + offered + " are offered and " + (_entries.Count - offered)
                        + (ActionAvailability.PassComplete
                            ? " withheld for absent content."
                            : " withheld so far - the availability probe is still running, four"
                              + " entries per rebuild, and anything not yet judged is offered.")
                        + " Navigation: " + nav
                        + " small button(s) sharing the leftmost footprint - '" + CloseLabel()
                        + "'" + (nav == NavSlots
                            ? ", '" + NavPrev + "' previous page, '" + NavNext + "' next page"
                            : " only; this level has one page, so the arrows are not drawn"));
                }

                _countField.SetValue(__instance, used);
                for (int i = used; i < extra.Length; i++)
                {
                    object el = extra.GetValue(i);
                    if (el != null && _useAsNoOp != null) _useAsNoOp.Invoke(el, null);
                }
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.MiscMenuEnabled.Value = false;
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "misc menu refresh failed, disabling: " + e.Message);
            }
        }

        // WHAT THE PREFIX DECIDED, HANDED TO THE POSTFIX. Same call, same frame - Harmony runs a
        // postfix immediately after the body its prefix ran before - so this is a handoff, not
        // state with a lifetime.
        private static object _layoutOwner;
        private static int _layoutFrame = -1;
        private static int _layoutNav, _layoutUsed;

        // Indices VHVR's own loops reach. initialize, reorderElements and RefreshWristQuickAction
        // all end their extra-element loop on a literal 8 (`ldc.i4.8 / blt`, Cecil against the
        // shipped ValheimVRMod.dll), so 8 and above are ours to activate, position and - the part
        // that bites if it is forgotten - to DEACTIVATE.
        private const int VhvrSlots = 8;

        private static readonly Vector3 NavScaleVec = new Vector3(NavScale, NavScale, NavScale);
        private static bool _cellsDone;
        private static readonly Vector3[] _cells = new Vector3[RingCells];

        // WHERE THE EIGHT FOOTPRINTS ARE. VHVR's own wrist formula - two rows of four at a 0.05m
        // pitch, centred on the count - evaluated once for the FULL count and then never again.
        //
        // Pinned to the full eight rather than to however many a page happens to use, and that is
        // the whole point of the method existing. VHVR re-centres its strip on extraElementCount, so
        // a page holding two items would draw them centred, sliding the navigation triple 0.025m to
        // the right of where it sat on a full page - the operator would be reaching for an X that
        // moved because a page ran short. Pinning costs an empty right-hand end on a short page,
        // which is honest: the strip starts where the strip always starts, and every button on it -
        // navigation and content alike - has one home at every depth, on every page.
        //
        // Constant, therefore computed once. The first call fills it; every later call is one bool.
        private static Vector3[] Cells()
        {
            if (_cellsDone) return _cells;
            _cellsDone = true;
            for (int c = 0; c < RingCells; c++)
            {
                int center = RingCells < 4 ? RingCells : 4;
                float row = 0f;
                int column = c;
                if (c >= 4) { row = RowDrop; center = RingCells - 4; column = c - 4; }
                float x = (column * Pitch) - (center / 2 * Pitch) + (center % 2 == 0 ? Pitch * 0.5f : 0f);
                _cells[c] = new Vector3(x, row, 0f);
            }
            return _cells;
        }

        private static void AfterReorder(object __instance)
        {
            long _t = HookProfiler.Start();
            try
            {
                if (_layoutFrame != Time.frameCount || !ReferenceEquals(__instance, _layoutOwner)) return;
                Array extra = _extraField.GetValue(__instance) as Array;
                if (extra == null) return;
                LayOut(extra);
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.MiscMenuEnabled.Value = false;
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "misc menu layout failed, disabling: " + e.Message);
            }
            finally { HookProfiler.Stop(HookProfiler.Misc, _t); }
        }

        // WHERE EVERYTHING GOES, after reorderElements has had its say.
        //
        // It must be after: reorderElements writes localPosition on every element it owns, as the
        // last thing refreshItems does, so a layout written earlier is overwritten in the same
        // frame. It never writes localScale, which is why the scale survives and only the positions
        // have to be restated.
        //
        // Per frame, while the menu is open: eight to ten localPosition writes, up to three
        // localScale compares, and two activeSelf compares. reorderElements itself does nineteen
        // localPosition writes and nineteen SetActive calls on the same objects immediately before,
        // so this is a fraction of a cost the frame was already paying.
        private static void LayOut(Array extra)
        {
            if (_layoutNav == 0)
            {
                // Closed. VHVR's layout stands; we only undo what only we could have done. Both
                // loops are compare-then-write rather than write, and both are bounded by three and
                // by two, so a closed strip costs five property reads per frame and no writes.
                for (int i = 0; i < NavSlots && i < extra.Length; i++)
                {
                    Transform t = TransformOf(extra.GetValue(i));
                    if (t != null && t.localScale.x != 1f) t.localScale = Vector3.one;
                }
                Stand(extra, 0);
                return;
            }

            int content = _layoutUsed - _layoutNav;
            Vector3[] cells = Cells();
            Vector3 home = cells[0];

            // The triple, packed. i - 1 puts X in the LEFT third, so with the arrows absent X does
            // not slide to the middle: its place is a property of the button, not of the page count.
            for (int i = 0; i < _layoutNav; i++)
            {
                Transform t = TransformOf(extra.GetValue(i));
                if (t == null) continue;
                t.localPosition = new Vector3(home.x + (i - 1) * NavPitch, home.y, home.z);
                if (t.localScale.x != NavScale) t.localScale = NavScaleVec;
            }

            // Content fills the remaining footprints at full size. The scale is restated because an
            // index that held an arrow a moment ago - the page count fell to one - would otherwise
            // keep a third of its size.
            for (int i = 0; i < content; i++)
            {
                Transform t = TransformOf(extra.GetValue(_layoutNav + i));
                if (t == null) continue;
                t.localPosition = cells[i + 1];
                if (t.localScale.x != 1f) t.localScale = Vector3.one;
            }

            Stand(extra, _layoutUsed);
        }

        // The grown indices, which VHVR neither activates nor deactivates. A live element left up
        // there after the strip shrank is a PHANTOM BUTTON: hoverItem's nearest-wins test reads
        // transform.position and never asks whether the object is active, so it would keep taking
        // presses at a position nothing is drawn at. This is the only place that answers for them.
        private static void Stand(Array extra, int liveCount)
        {
            for (int i = VhvrSlots; i < extra.Length; i++)
            {
                GameObject go = GameObjectOf(extra.GetValue(i));
                if (go == null) continue;
                bool live = i < liveCount;
                if (go.activeSelf != live) go.SetActive(live);
            }
        }

        // THE HIGHLIGHT, SIZED TO THE BUTTON IT IS HIGHLIGHTING.
        //
        // Reported 2026-08-25, after the navigation triple shipped: "better, but when you highlight
        // the small menus, the highlight is still the full size". Correct, and nothing in LayOut
        // above could have fixed it, because the highlight is not part of the element.
        //
        // Read with Mono.Cecil out of the shipped ValheimVRMod.dll (monodis asserts on that
        // assembly), and confirmed in BOTH builds on this machine - md5 0beed51d, which build.sh
        // references, and md5 69a2644f, which the operator's profile actually loads. IL offsets
        // below are from 0beed51d; 69a2644f has the same calls at 0293/02A4/02D0 for the hoveredItem
        // reads. Nothing here is compile-bound: every member is reached reflectively, and the four
        // it needs are present in both (hoveredItem protected GameObject, hoveredIndex protected
        // Int32, elementCount private Int32, hoverItem private void()).
        //
        // QuickAbstract holds `protected GameObject hoveredItem`: ONE object per menu
        // instance, newobj'd in initialize (IL_012D) and parented to the QuickAbstract root with
        // SetParent(transform, false) at IL_0147-0159 - a SIBLING of the elements, not a child of
        // the hovered one, which is exactly why an element's localScale never reached it. initialize
        // scales it once, `localScale *= 4f` at IL_015F-0179, and gives it a SpriteRenderer holding
        // the 1x1 tex_hovered sprite. hoverItem then, every frame, writes only set_position,
        // set_rotation and SetActive on it (IL_0271-02B2) - there is no localScale write in
        // hoverItem, Update or reorderElements in EITHER build, checked instruction by instruction.
        // So one write from outside holds, and nothing has to be restated per frame beyond deciding
        // what it should be.
        //
        // Written from a POSTFIX on hoverItem rather than from AfterReorder: hoveredIndex is
        // computed inside hoverItem, and Update calls reorderElements first, so reading it from the
        // layout postfix would size this frame's highlight from last frame's hover - a visible
        // full-size flash on the frame the finger arrives.
        //
        // The base scale is remembered per highlight OBJECT, not per menu, because a re-initialize
        // makes a new GameObject with a fresh `*= 4f` applied to a fresh (1,1,1); keying on the menu
        // would multiply our factor into a stale base. Two slots, because VHVR constructs exactly
        // two QuickAbstract subclasses (StaticObjects.addQuickMenus: LeftHandQuickMenu and
        // RightHandQuickMenu) and therefore exactly two highlights.
        private static FieldInfo _hoveredItemField, _hoveredIndexField, _elementCountField;
        private static bool _hoverResolved;
        private static readonly GameObject[] _hlObject = new GameObject[2];
        private static readonly Vector3[] _hlBase = new Vector3[2];

        // Bound against QuickAbstract, where all three are declared, rather than against whichever
        // subclass happened to call first - both subclasses inherit the same three fields, so a
        // handle taken from either is the same handle, and naming the declaring type says so.
        private static bool ResolveHover()
        {
            if (_hoverResolved) return _hoveredItemField != null;
            _hoverResolved = true;
            Type qa = TypeCache.Get("ValheimVRMod.Scripts.QuickAbstract");
            _hoveredItemField = qa == null ? null : AccessTools.Field(qa, "hoveredItem");
            _hoveredIndexField = qa == null ? null : AccessTools.Field(qa, "hoveredIndex");
            _elementCountField = qa == null ? null : AccessTools.Field(qa, "elementCount");
            if (_hoveredItemField == null || _hoveredIndexField == null || _elementCountField == null)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "QuickAbstract hover fields not found (hoveredItem=" + (_hoveredItemField != null)
                    + " hoveredIndex=" + (_hoveredIndexField != null)
                    + " elementCount=" + (_elementCountField != null)
                    + "); the navigation buttons keep VHVR's full-size highlight");
                _hoveredItemField = null;
                return false;
            }
            return true;
        }

        private static Vector3 BaseScale(GameObject highlight, Transform t)
        {
            for (int i = 0; i < _hlObject.Length; i++)
                if (ReferenceEquals(_hlObject[i], highlight)) return _hlBase[i];
            // Captured before we have ever written to this object, so it is VHVR's own 4x value.
            for (int i = 0; i < _hlObject.Length; i++)
            {
                if (_hlObject[i] != null && _hlObject[i] != highlight) continue;
                _hlObject[i] = highlight;
                _hlBase[i] = t.localScale;
                return _hlBase[i];
            }
            // Three highlights would mean VHVR grew a third menu; size it from what it has now
            // rather than from a base we never recorded, which is worse than leaving it alone.
            return t.localScale;
        }

        private static int _hlLogged;

        private static void AfterHover(object __instance)
        {
            long _t = HookProfiler.Start();
            try
            {
                if (!NeuralyzeVRFixesPlugin.MiscMenuEnabled.Value) return;
                if (!ResolveHover()) return;

                GameObject highlight = _hoveredItemField.GetValue(__instance) as GameObject;
                if (highlight == null) return;
                Transform t = highlight.transform;
                Vector3 want = BaseScale(highlight, t);

                // A third of the base only for OUR navigation elements, on the strip whose layout we
                // own, while the page is open. Everything else - VHVR's own radial items, VHVR's own
                // extra elements, our full-size content buttons, and the closed strip - keeps the
                // size VHVR chose.
                //
                // No _layoutFrame check, unlike AfterReorder. That guard exists there because the
                // pair is a one-frame handoff of numbers computed for that call. Here _layoutNav is
                // a description of the strip's current state, which does not expire: QuickAbstract
                // .Update calls reorderElements before hoverItem in the SAME Update (so the two
                // subclasses cannot cross), and on any frame where reorderElements is skipped the
                // strip has not changed and last frame's value is still the right one. Demanding the
                // same frame would drop the highlight to full size on exactly those frames.
                int index = Convert.ToInt32(_hoveredIndexField.GetValue(__instance));
                if (index >= 0 && _layoutNav > 0 && ReferenceEquals(__instance, _layoutOwner))
                {
                    // hoverItem stores an extra element as elementCount + its index into
                    // extraElements (IL_00AA-00B4), which is how a single int addresses both rings.
                    int extraIndex = index - Convert.ToInt32(_elementCountField.GetValue(__instance));
                    if (extraIndex >= 0 && extraIndex < _layoutNav)
                    {
                        want = want * NavScale;
                        if (_hlLogged < 1)
                        {
                            _hlLogged++;
                            NeuralyzeVRFixesPlugin.Log.LogMessage(NeuralyzeVRFixesPlugin.Tag
                                + "wrist highlight scaled to " + NavScale.ToString("F2")
                                + " over navigation button " + extraIndex + " of " + _layoutNav
                                + " - QuickAbstract.hoveredItem is one shared object parented to the"
                                + " menu root, so an element's own scale never reached it");
                        }
                    }
                }
                if (t.localScale != want) t.localScale = want;
            }
            catch (Exception e)
            {
                // The highlight is cosmetic; a failure here must not take the menu with it, so this
                // one does NOT set MiscMenuEnabled=false. Resolution is abandoned instead, which
                // leaves VHVR's full-size highlight - the behaviour being improved on, not a break.
                _hoveredItemField = null;
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "wrist highlight scaling failed, leaving VHVR's own size: " + e.Message);
            }
            finally { HookProfiler.Stop(HookProfiler.Misc, _t); }
        }

        private static Transform TransformOf(object element)
        {
            Component c = element as Component;
            return c == null ? null : c.transform;
        }

        private static GameObject GameObjectOf(object element)
        {
            Component c = element as Component;
            return c == null ? null : c.gameObject;
        }

        // ONCE PER HAND, AND THEN NEVER AGAIN.
        //
        // extraElements has exactly one stfld in the whole of ValheimVRMod - in Awake - so nothing
        // puts the eight-element array back, and the guard at the call site is a single int compare
        // per frame thereafter. The two new elements are CLONED from one that already exists rather
        // than built, so they carry the same three sprite layers, the same parent and the same local
        // space; a hand-built element would have to reproduce CreateItemLayers from the outside.
        //
        // itemName and the callback come out of Instantiate unset - a delegate and a
        // compiler-generated backing field are not serialized - and Assign would rebuild them
        // regardless, since its skip test needs the slot to be in _slotAction and a new element
        // never is.
        private static bool _grewLogged;

        private static Array Grow(object instance, Array extra)
        {
            Type element = extra.GetType().GetElementType();
            Component template = extra.GetValue(0) as Component;
            if (element == null || template == null) return extra;
            Array grown = Array.CreateInstance(element, RingSlots);
            Array.Copy(extra, grown, extra.Length);
            // Clones are tracked so a failure halfway can take them all back with it. Abandoning
            // one is not a leak that shows up as memory: the field is never assigned, so nothing
            // ever positions or deactivates it, and an orphan sits lit on the wrist forever at
            // whatever position it inherited. That is the phantom button again, arrived by the
            // error path.
            List<GameObject> made = new List<GameObject>();
            for (int i = extra.Length; i < RingSlots; i++)
            {
                GameObject clone = UnityEngine.Object.Instantiate(
                    template.gameObject, template.transform.parent, false);
                clone.name = "NeuralyzeWristExtra" + i;
                made.Add(clone);
                Component slot = clone.GetComponent(element);
                if (slot == null)
                {
                    foreach (GameObject dead in made) UnityEngine.Object.Destroy(dead);
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "wrist strip could not be grown: a cloned element carries no "
                        + element.Name + ", so the strip stays at " + extra.Length
                        + " slots and a page holds " + PerPage(extra.Length)
                        + " content buttons instead of " + ContentCells + ".");
                    return extra;
                }
                grown.SetValue(slot, i);
            }
            _extraField.SetValue(instance, grown);
            if (!_grewLogged)
            {
                _grewLogged = true;
                NeuralyzeVRFixesPlugin.Log.LogMessage(NeuralyzeVRFixesPlugin.Tag
                    + "wrist strip grown from " + extra.Length + " to " + RingSlots
                    + " elements, once, by cloning: " + NavSlots + " navigation buttons share the"
                    + " leftmost footprint at " + NavScale.ToString("F2") + " scale, leaving "
                    + ContentCells + " full-size footprints for content. VHVR's own loops end on a"
                    + " literal 8, so it never touches the new indices and never puts the short"
                    + " array back.");
            }
            return grown;
        }

        // Keyed by the ACTION, not by its label.
        //
        // Delegate equality is target-plus-method, so `entry.Execute` handed in fresh on every
        // rebuild still hits this cache: that is what it is for - Delegate.CreateDelegate allocated
        // once per slot per frame, roughly twenty allocations a frame that the GC then collected
        // during play.
        //
        // Keying it by LABEL was a wrong-command hazard, and the second menu level put it within
        // reach. Two entries may legitimately share a label at different depths - "Admin/Save" and
        // "Admin/Spawn/Save" both draw SAVE - and the label-keyed cache handed the second one the
        // first one's callback. Same reason the skip test below now has to agree with this: itemName
        // is a change-detection key and nothing else (MiscLabels.cs:10), so two same-named entries
        // landing on the same slot index at two different levels made the slot keep the level it
        // came from. The word on the button is not an identity.
        private static readonly Dictionary<Delegate, Delegate> _callbacks = new Dictionary<Delegate, Delegate>();
        private static readonly Dictionary<object, Func<bool>> _slotAction = new Dictionary<object, Func<bool>>();

        private static PropertyInfo _itemName;
        private static int _skipped, _rebuilt;

        private static void Assign(object element, string label, Func<bool> action)
        {
            if (element == null) return;
            // Untouched if it already says the right thing AND already does the right thing.
            // useAsQuickAction rebuilds the slot's sprite and layout, and doing that to every slot
            // on every frame both burned 4-5ms per frame and made the ring flicker in and out.
            // VHVR guards its own entries the same way.
            if (_itemName == null) _itemName = element.GetType().GetProperty("itemName");
            if (_itemName != null)
            {
                object current = _itemName.GetValue(element, null);
                Func<bool> last;
                if (current is string && (string)current == label
                    && _slotAction.TryGetValue(element, out last) && last.Equals(action))
                {
                    _skipped++;
                    return;
                }
            }
            _rebuilt++;
            _slotAction[element] = action;
            Delegate cb;
            if (!_callbacks.TryGetValue(action, out cb))
            {
                cb = Delegate.CreateDelegate(_callbackType, action.Target, action.Method);
                _callbacks[action] = cb;
            }
            // The word is baked INTO the sprite, deliberately.
            //
            // A Unity TextMesh child was tried in 2.1.82 and reverted: it inherits the item's
            // transform scale, so the text rendered far larger than the box, spilled outside it,
            // overlapped its neighbours, and stayed visible on the arm after the menu closed.
            // ResizeIcon() sizes the SpriteRenderer to the box for us, so anything drawn into the
            // texture is guaranteed to stay inside the button and to vanish with it.
            long tl = System.Diagnostics.Stopwatch.GetTimestamp();
            Sprite icon = MiscLabels.For(label);
            _msLabels += (System.Diagnostics.Stopwatch.GetTimestamp() - tl) * 1000.0 / System.Diagnostics.Stopwatch.Frequency;

            long ta = System.Diagnostics.Stopwatch.GetTimestamp();
            _useAsQuickAction.Invoke(element, new object[] { label, icon, cb });
            _msAssign += (System.Diagnostics.Stopwatch.GetTimestamp() - ta) * 1000.0 / System.Diagnostics.Stopwatch.Frequency;
        }
    }

    // Opens Valheim's dev console with the VR keyboard attached.
    //
    // Console.IsVisible() is `m_instance.m_chatWindow.gameObject.activeInHierarchy` (verified
    // IL), so showing it means activating that window. The console must also be ENABLED -
    // Console.SetConsoleEnabledForThisSession exists for exactly this. Typing then works
    // because VHVR's InputManager.start(inputField, tmpField, guiField) calls
    // SteamVR.instance.overlay.ShowKeyboard (TextInputPatches.cs:157) - the same call it uses
    // for chat and signs. That answers whether console commands can use the VR keyboard: yes,
    // through the identical path.
    internal static class ConsoleOpener
    {
        internal static bool Open()
        {
            try
            {
                Type console = TypeCache.Get("Console");
                if (console == null) { Log("Console type not found"); return false; }

                // Enable for this session if it is off, else the window refuses to accept input.
                // AccessTools logged 'Could not find method ... SetConsoleEnabledForThisSession
                // and parameters (bool)' - the real overloads are not all (bool), so try the
                // no-argument shape too rather than giving up silently.
                // Verified against assembly_valheim 0.221.12:
                //   Console.SetConsoleEnabledForThisSession()  static, NO arguments
                //   Console.SetConsoleEnabled(bool)            static
                // The previous code asked for a (bool) overload of the first one, which does not
                // exist, so AccessTools logged "Could not find method" and the console opened
                // without cheats enabled - which is why `god` did nothing.
                foreach (string enabler in new[] { "SetConsoleEnabledForThisSession", "SetConsoleEnabled" })
                {
                    MethodInfo m = AccessTools.Method(console, enabler, new Type[0])
                                   ?? AccessTools.Method(console, enabler, new[] { typeof(bool) });
                    if (m == null) continue;
                    object[] args = m.GetParameters().Length == 0 ? null : new object[] { true };
                    try { m.Invoke(null, args); break; } catch { }
                }

                PropertyInfo instProp = console.GetProperty("instance", BindingFlags.Static | BindingFlags.Public);
                object inst = instProp == null ? null : instProp.GetValue(null, null);
                if (inst == null) { Log("Console.instance null - console may be disabled by the server"); return false; }

                // m_chatWindow lives on Terminal, the base class.
                Type terminal = TypeCache.Get("Terminal") ?? console;
                FieldInfo winField = AccessTools.Field(terminal, "m_chatWindow");
                object win = winField == null ? null : winField.GetValue(inst);
                GameObject windowObject = null;
                if (win is GameObject) windowObject = (GameObject)win;
                else if (win is Component) windowObject = ((Component)win).gameObject;
                if (windowObject == null) { Log("console chat window not found"); return false; }
                windowObject.SetActive(true);

                // Focus its input field and raise the SteamVR keyboard.
                FieldInfo inputField = AccessTools.Field(terminal, "m_input");
                object input = inputField == null ? null : inputField.GetValue(inst);
                // Deliberately does NOT raise a keyboard here. DirectActions.OpenConsoleKeyboard
                // opens ONE keyboard, on the chat field, prefilled with "/cmd " - which is the only
                // path that actually submits. Raising one here too meant the player saw the console's
                // own keyboard first, with no prefill and no submit, and reported "I don't see a /cmd".
                Log("console enabled and window shown; keyboard is opened separately with the /cmd prefill");
                return true;
            }
            catch (Exception e) { Log("console open failed: " + e.Message); return false; }
        }

        // InputManager.start takes (InputField, TMP_InputField, GuiInputField); pass whichever
        // slot matches the field's actual type and leave the others null.
        internal static bool RaiseKeyboard(object input) { return OpenKeyboardFor(input); }

        private static bool OpenKeyboardFor(object input)
        {
            if (input == null) return false;
            try
            {
                Type im = TypeCache.Get("ValheimVRMod.Patches.InputManager")
                       ?? TypeCache.Get("InputManager");
                if (im == null) return false;
                foreach (MethodInfo m in im.GetMethods(BindingFlags.Static | BindingFlags.Public))
                {
                    if (m.Name != "start") continue;
                    ParameterInfo[] ps = m.GetParameters();
                    object[] args = new object[ps.Length];
                    bool placed = false;
                    for (int i = 0; i < ps.Length; i++)
                    {
                        if (!placed && ps[i].ParameterType.IsInstanceOfType(input)) { args[i] = input; placed = true; }
                        else args[i] = null;
                    }
                    if (!placed) continue;
                    m.Invoke(null, args);
                    return true;
                }
                return false;
            }
            catch { return false; }
        }

        private static void Log(string msg)
        {
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + msg);
        }
    }

    // Pulses a vanilla/mod ZInput action through VHVR's own public hook. Single-consumption,
    // which is unsuitable for held input but exactly right for a one-shot menu selection.
    internal static class ZInputPulse
    {
        private static MethodInfo _emulate;
        private static bool _dead;
        private static readonly HashSet<string> _warned = new HashSet<string>();

        internal static bool Send(string name)
        {
            if (_dead) return false;
            if (_emulate == null)
            {
                Type patch = TypeCache.Get("ValheimVRMod.Patches.ZInput_GetButtonDown_Patch");
                _emulate = patch == null ? null : patch.GetMethod("EmulateButtonDown",
                    BindingFlags.Static | BindingFlags.Public, null, new[] { typeof(string) }, null);
                if (_emulate == null)
                {
                    _dead = true;
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "ZInput_GetButtonDown_Patch.EmulateButtonDown not resolvable;"
                        + " every zinput misc action is now dead");
                    return false;
                }
            }
            _emulate.Invoke(null, new object[] { name });
            // ZInput_GetButtonDown_Patch.Prefix answers true exactly once for this name, and its
            // Postfix ORs rather than overwrites, so the value does reach the caller. What is NOT
            // guaranteed is that anything ASKS: MapZoomIn/Out are only polled while the map is
            // open, Joy* names are skipped entirely when ZInput.IsGamepadEnabled() is false, and
            // mod names depend on that mod polling every frame. Delivery of the pulse is
            // therefore not evidence the action happened - which is why the direct kinds exist.
            if (_warned.Add(name))
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "misc zinput '" + name + "' pulsed, but ZInput delivery is unverifiable -"
                    + " nothing may poll this name in the current game state");
            }
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "misc zinput pulse '" + name + "'");
            return true;
        }
    }

    // Injects a keyboard press so mods whose entire feature set is bound to a keyboard
    // shortcut become reachable. Two paths are needed: VHVR's own hook covers
    // ZInput.GetKeyDown, but BepInEx KeyboardShortcut and most mods read UnityEngine.Input
    // directly, which VHVR only patches at GetKeyDownInt / GetKeyInt and never injects into.
    // So we patch those two ourselves and answer true for one frame.
    internal static class KeyPulse
    {
        private static readonly HashSet<int> _pending = new HashSet<int>();
        private static readonly HashSet<int> _holdFor = new HashSet<int>();
        private static MethodInfo _vhvrEmulate;
        private static bool _installed;

        internal static void Install(Harmony harmony)
        {
            if (_installed) return;
            _installed = true;
            try
            {
                // UnityEngine.Input is type-forwarded to UnityEngine.InputLegacyModule,
                // which is not among the available reference assemblies, so it is resolved
                // at runtime rather than compiled against.
                Type input = TypeCache.Get("UnityEngine.Input");
                if (input == null)
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "UnityEngine.Input not resolvable; keyboard-bound mod actions stay unreachable");
                    return;
                }
                MethodInfo down = AccessTools.Method(input, "GetKeyDownInt", new[] { typeof(KeyCode) });
                MethodInfo held = AccessTools.Method(input, "GetKeyInt", new[] { typeof(KeyCode) });
                MethodInfo pre = typeof(KeyPulse).GetMethod("PrefixDown", BindingFlags.Static | BindingFlags.NonPublic);
                MethodInfo preHeld = typeof(KeyPulse).GetMethod("PrefixHeld", BindingFlags.Static | BindingFlags.NonPublic);
                int n = 0;
                if (down != null) { harmony.Patch(down, prefix: new HarmonyMethod(pre)); n++; }
                if (held != null) { harmony.Patch(held, prefix: new HarmonyMethod(preHeld)); n++; }
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "keyboard pulse installed on " + n + "/2 Input entry point(s)"
                    + (n == 2 ? "" : " - some mod hotkeys may stay unreachable"));
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "keyboard pulse install failed: " + e.Message);
            }
        }

        private static bool PrefixDown(KeyCode key, ref bool __result)
        {
            if (!_pending.Remove((int)key)) return true;
            __result = true;
            _holdFor.Add((int)key);
            return false;
        }

        private static bool PrefixHeld(KeyCode key, ref bool __result)
        {
            if (_latched.Contains((int)key)) { __result = true; return false; }
            // Some mods test IsDown() as "key held this frame"; answer once so a chord
            // built from KeyboardShortcut still resolves, then stop.
            if (!_holdFor.Remove((int)key)) return true;
            __result = true;
            return false;
        }

        private static readonly HashSet<int> _latched = new HashSet<int>();

        // Toggles a key into the held set. PrefixHeld answers true for as long as it is
        // latched, so a mod polling Input.GetKey sees the modifier down across frames while
        // you point and click with the laser. Tapping the entry again releases it.
        internal static bool Latch(string spec)
        {
            bool any = false;
            foreach (string part in spec.Split('+'))
            {
                string k = part.Trim();
                if (k.Length == 0) continue;
                try
                {
                    int code = (int)(KeyCode)Enum.Parse(typeof(KeyCode), k, true);
                    if (_latched.Remove(code))
                    {
                        NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "hold released: " + k);
                    }
                    else
                    {
                        _latched.Add(code);
                        _pending.Add(code);   // also deliver one press, for mods that want the edge
                        NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                            + "hold engaged: " + k + " - interact now, tap again to release");
                    }
                    any = true;
                }
                catch
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "unknown KeyCode '" + k + "'");
                }
            }
            return any;
        }

        internal static bool Send(string spec)
        {
            bool any = false;
            foreach (string part in spec.Split('+'))
            {
                string k = part.Trim();
                if (k.Length == 0) continue;
                try
                {
                    KeyCode code = (KeyCode)Enum.Parse(typeof(KeyCode), k, true);
                    _pending.Add((int)code);
                    // Also answer GetKey once, for every key in the chord.
                    //
                    // A chord was only half delivered before, and the half that was missing is the
                    // half a mod checks. BepInEx KeyboardShortcut.IsDown() is
                    // "Input.GetKeyDown(MainKey) && every modifier passes Input.GetKey" - two
                    // different entry points. Send only queued _pending, which PrefixDown answers,
                    // and PrefixDown only puts the key it just answered into _holdFor. So for
                    // "LeftShift+F" the main key F resolved and the MODIFIER never did:
                    // Input.GetKey(LeftShift) fell through to the real keyboard, read false, and
                    // IsDown() returned false. Every 'key:' entry with a modifier in it - the
                    // shipped hover ship group's Anchor=key:LeftShift+F, piece Repair Area, Add
                    // Wear - was therefore a pulse that no mod could see. Queuing the whole chord
                    // into _holdFor as well closes it; PrefixHeld still answers once and stops, so
                    // this stays a pulse and cannot leave a modifier stuck down.
                    _holdFor.Add((int)code);
                    if (_vhvrEmulate == null)
                    {
                        Type patch = TypeCache.Get("ValheimVRMod.Patches.ZInput_GetKeyDown_Patch");
                        _vhvrEmulate = patch == null ? null : patch.GetMethod("EmulateKeyDown",
                            BindingFlags.Static | BindingFlags.Public, null, new[] { typeof(KeyCode) }, null);
                    }
                    if (_vhvrEmulate != null) _vhvrEmulate.Invoke(null, new object[] { code });
                    any = true;
                }
                catch
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "unknown KeyCode '" + k + "'");
                }
            }
            if (any) NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "misc key pulse '" + spec + "'");
            return any;
        }
    }
}
