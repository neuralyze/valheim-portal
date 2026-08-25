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

        // The wrist bar VHVR actually gives us: QuickAbstract.initialize allocates extraElements
        // with `newarr 8` and reorderElements iterates 0..7, laying it out as two rows of four at a
        // fixed 0.05m pitch. Used only where the real array is not in hand (label warm-up at load).
        private const int RingSlots = 8;

        private static bool Compact()
        {
            return NeuralyzeVRFixesPlugin.WristMenuCompactNav == null
                || NeuralyzeVRFixesPlugin.WristMenuCompactNav.Value;
        }

        // How many slots a page of content gets: the ring minus the navigation. Six was hard-coded
        // for the old shape, which spent TWO whole slots on "More >" and "< Back"; the compact
        // control spends one and buys a seventh entry per page.
        private static int PerPage(int ringSize)
        {
            return Mathf.Max(1, ringSize - (Compact() ? 1 : 2));
        }

        // How many pages the level being drawn has, as of the last rebuild. The compact control's
        // callback needs it - it must not page past the end - and the callback runs from
        // selectHoveredItem during Update, after a rebuild in the same frame, so it is fresh.
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

        // The X: "always closes the current menu level. if the menu is two levels deep, it goes back
        // to previous menu. if it was on top level, then it closes the wrist strip menu alltogether."
        // - the operator, 2026-08-25.
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

        // THREE FUNCTIONS IN ONE SLOT, chosen by where the thumb is at the instant of the press.
        //
        // A radial slot cannot host three independently selectable targets. VHVR's selection state
        // is a single int: hoverItem() picks the nearest whole slot by
        // Vector3.Distance(handTransform.position, extraElements[i].transform.position) < 0.05f and
        // writes hoveredIndex; selectHoveredItem() then calls extraElements[hoveredIndex -
        // elementCount].execute(), which invokes a QuickMenuItemCallback that takes NO arguments
        // (all verified by IL against the shipped ValheimVRMod.dll). Nothing between the hover test
        // and the callback carries WHERE in the button the hand was, and reorderElements only ever
        // sets localPosition - never localScale - so a slot cannot be made smaller either.
        //
        // So the three functions share one slot's space by sharing one slot. The stick is the
        // discriminator because this codebase already drives a menu with it: HoverMenu reads
        // MountControls.RawRightStickY() to move its highlight and uses the same 0.4 threshold
        // (HoverMenu.cs:314-315). Centred is CLOSE rather than a page step, so the default action of
        // a player who does not know about the stick is the safe, reversible one.
        private const float StickEdge = 0.4f;

        private static bool ComboPressed()
        {
            float x = MountControls.RawRightStickX();
            if (_pagesNow > 1 && x <= -StickEdge) return PageBy(-1);
            if (_pagesNow > 1 && x >= StickEdge) return PageBy(1);
            return CloseLevel();
        }

        // Cached because Assign is called on every rebuild and a method-group conversion allocates a
        // delegate at each conversion site; :614 records what twenty of those a frame cost.
        private static readonly Func<bool> _actOpen = Open;
        private static readonly Func<bool> _actCombo = ComboPressed;
        private static readonly Func<bool> _actNextPage = NextPage;
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

        // THE LABEL IS THE ONLY AFFORDANCE. A control whose meaning depends on the thumbstick is
        // invisible without one, so it names all three functions and the page it is on.
        //
        // Composed so MiscLabels' greedy wrap (MiscLabels.cs:120-147; MaxChars 8, MaxLines 3) breaks
        // it where intended:
        //     "< X > CLOSE PG1OF2"  ->  "< X >" / "CLOSE" / "PG1OF2"
        //     "X CLOSE"             ->  "X CLOSE"
        // One page means no arrows at all: there is nowhere to page to, and a single slot cannot be
        // half-disabled, so the arrows leave the label and the stick directions become inert.
        // CLOSE at the top strip, BACK inside a group, because that is what the same press does.
        //
        // It reads PG1OF2 and not PG1/2 because '/' is not in that font's glyph table
        // (MiscLabels.cs:28-74) and Build substitutes '?' for anything missing (:207) - an
        // unrenderable character in the one label that has to teach the control would be the same
        // invisible-control bug in a new place.
        private static string ComboText(int depth, int page, int pages)
        {
            string verb = depth == 0 ? "CLOSE" : "BACK";
            if (pages <= 1) return "X " + verb;
            return "< X > " + verb + " PG" + (page + 1) + "OF" + pages;
        }

        private static int _comboKey = -1;
        private static string _comboLabel = "X CLOSE";

        private static string ComboLabel(int pages)
        {
            int key = ((pages * 64) + _page) * 4 + Depth();
            if (key != _comboKey)
            {
                _comboKey = key;
                _comboLabel = ComboText(Depth(), _page, pages);
            }
            return _comboLabel;
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
        // Under the compact navigation its count is every action the strip can still reach at any
        // depth, which is the number the open log line reports, and the parentheses go: '(' and ')'
        // are not in the glyph table, so "Misc (8)" was drawing as MISC ?8?. The old shape is kept
        // byte-for-byte behind the toggle so the fallback is the entry he already knows.
        private static string DoorLabel()
        {
            bool compact = Compact();
            int n = compact ? OfferedCount() : VisibleEntries().Count;
            if (n != _doorCount)
            {
                _doorCount = n;
                _doorLabel = compact ? "Misc " + n : "Misc (" + n + ")";
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
            foreach (string fixedLabel in new string[] { "More >", "< Back", "X CLOSE", "X BACK",
                                                         "Misc " + _entries.Count,
                                                         "Misc (" + _entries.Count + ")" })
            {
                MiscLabels.For(fixedLabel);
                warmed++;
            }
            WarmComboLabels(ref warmed);
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                + "misc menu: " + warmed + " label sprites pre-drawn at load");

        }

        // The compact control's label carries the page number, so it needs one sprite per page per
        // level shape. Drawn from the ACTUAL per-level item counts rather than a generous grid,
        // because each sprite is a 256x256 RGBA texture and a grid wide enough to be safe would
        // spend several megabytes on labels nobody sees. A count that turns out low at runtime -
        // availability withholds an entry, a context predicate hides one - only ever shrinks a
        // level, and the single-page shapes are pre-drawn unconditionally above.
        private static void WarmComboLabels(ref int warmed)
        {
            var items = new Dictionary<string, int>();
            var counted = new List<string>();
            foreach (Entry e in _entries)
            {
                string level = e.Group.Length == 0 ? "" : (e.Sub.Length == 0 ? e.Group : e.Group + "/" + e.Sub);
                Bump(items, level);
                // A door is an item on its PARENT level, and it is counted once.
                if (e.Group.Length > 0 && !counted.Contains(e.Group))
                {
                    counted.Add(e.Group);
                    Bump(items, "");
                }
                if (e.Sub.Length > 0 && !counted.Contains(level))
                {
                    counted.Add(level);
                    Bump(items, e.Group);
                }
            }
            int perPage = Mathf.Max(1, RingSlots - 1);
            foreach (KeyValuePair<string, int> level in items)
            {
                int pages = level.Value <= perPage ? 1 : (level.Value + perPage - 1) / perPage;
                int depth = level.Key.Length == 0 ? 0 : 1;   // only CLOSE-vs-BACK matters to the text
                for (int p = 0; p < pages; p++)
                {
                    MiscLabels.For(ComboText(depth, p, pages));
                    warmed++;
                }
            }
        }

        private static void Bump(Dictionary<string, int> counts, string key)
        {
            int n;
            counts[key] = counts.TryGetValue(key, out n) ? n + 1 : 1;
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
                MethodInfo pre = typeof(MiscMenu).GetMethod("BeforeReorder", BindingFlags.Static | BindingFlags.NonPublic);
                MethodInfo reorder = AccessTools.Method(qa, "reorderElements");
                if (reorder == null)
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "QuickAbstract.reorderElements not found; misc menu unavailable");
                    return;
                }
                harmony.Patch(reorder, prefix: new HarmonyMethod(pre));
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "misc menu hooked into QuickAbstract.reorderElements");
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
                int max = extra.Length;

                // Measured while OPEN, when the strip is parented and positioned. Logged at load it
                // read "524m from both hands" - a number with no meaning, which is worse than none.
                if (_open && _whereLogged < 2)
                {
                    _whereLogged++;
                    NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                        + "misc ring " + __instance.GetType().Name + Where(__instance));
                }

                if (!_open)
                {
                    // Append after VHVR's own entries and extend the count so reorderElements
                    // activates and positions it, and hoverItem can reach it.
                    if (vhvrCount >= max) return;
                    Assign(extra.GetValue(vhvrCount), DoorLabel(), _actOpen);
                    _countField.SetValue(__instance, vhvrCount + 1);
                    if (!_placedOnce)
                    {
                        _placedOnce = true;
                        NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                            + "misc entry appended at wrist slot " + vhvrCount + " of " + max
                            + " (VHVR used " + vhvrCount + "); it is the LAST item on that wrist strip"
                            + " - reach your other hand to it");
                    }
                    return;
                }

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

                bool compact = Compact();
                int perPage = PerPage(max);
                int total = doors.Count + entries.Count;
                int pages = total <= perPage ? 1 : (total + perPage - 1) / perPage;
                if (_page >= pages || _page < 0) _page = 0;
                _pagesNow = pages;

                int used = 0;
                if (compact)
                {
                    // Slot 0 is the LEFT end of the top row, which is what "always lives at the
                    // left" means here: reorderElements positions the wrist bar as two rows of four
                    // at x = col*0.05 - (count/2)*0.05, so index 0 is always leftmost whatever the
                    // count. Assigned before the content so it keeps that slot on every page.
                    Assign(extra.GetValue(0), ComboLabel(pages), _actCombo);
                    used = 1;
                }

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

                if (!compact)
                {
                    // The pre-2026-08-25 shape, kept whole behind CompactWristNavigation: two full
                    // slots, tapped. "< Back" closes the current LEVEL here too - that part of the
                    // spec is not the risky half, and a fallback that cannot leave Admin/Spawn
                    // would be worse than what it falls back from.
                    if (start + perPage < total && used < max)
                    {
                        Assign(extra.GetValue(used), "More >", _actNextPage);
                        used++;
                    }
                    if (used < max)
                    {
                        Assign(extra.GetValue(used), "< Back", _actCloseLevel);
                        used++;
                    }
                }

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
                        + (_page + 1) + " of " + pages + ", " + perPage + " slots per page: "
                        + doors.Count + " subgroup door(s) and " + entries.Count
                        + " action(s) on this level. Of " + _entries.Count + " configured actions "
                        + offered + " are offered and " + (_entries.Count - offered)
                        + " withheld for absent content. Navigation: "
                        + (compact
                            ? "one compact slot at the left - stick left/right pages, centred closes the level"
                            : "'More >' and '< Back'"));
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

        private static readonly Dictionary<string, Delegate> _callbacks = new Dictionary<string, Delegate>();

        private static PropertyInfo _itemName;
        private static int _skipped, _rebuilt;

        private static void Assign(object element, string label, Func<bool> action)
        {
            if (element == null) return;
            // Untouched if it already says the right thing. useAsQuickAction rebuilds the slot's
            // sprite and layout, and doing that to every slot on every frame both burned 4-5ms per
            // frame and made the ring flicker in and out. VHVR guards its own entries the same way.
            if (_itemName == null) _itemName = element.GetType().GetProperty("itemName");
            if (_itemName != null)
            {
                object current = _itemName.GetValue(element, null);
                if (current is string && (string)current == label) { _skipped++; return; }
            }
            _rebuilt++;
            // Cached by label. Delegate.CreateDelegate allocated once per slot per frame - roughly
            // twenty allocations a frame that the GC then had to collect during play.
            Delegate cb;
            if (!_callbacks.TryGetValue(label, out cb))
            {
                cb = Delegate.CreateDelegate(_callbackType, action.Target, action.Method);
                _callbacks[label] = cb;
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
