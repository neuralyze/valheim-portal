# Upstream report: AmbiguityGuard blocks Valheim10Compatibility's own PieceTable bridge

For **Wubarrk/Valheim10Compatibility 1.4.0** (MIT). Written 2026-09-13 from a
measured server profile; happy to supply logs.

## Summary

`AmbiguityGuard` scans `BepInEx/plugins` for by-name reflection on the members
each bridge would add, and refuses colliding bridges. It does not exclude
**Valheim10Compatibility's own assemblies** from that scan. The
`PieceTable.m_availablePieces` bridge reflects on the very field it injects, so
the guard sees the injector as a conflicting reflector and blocks it. The
bridge can therefore never be injected on any install.

## Evidence

Boot log, Valheim 1.0.12 dedicated server, BepInEx 5.4.23.3, Valheim10-
Compatibility 1.4.0, 95 plugins:

```
[Warning:Valheim10Compatibility] BLOCKED PieceTable.m_availablePieces
(List<List<Piece>>, alias of m_availablePiecesByCategory):
Valheim10Compatibility.Patcher.dll (AmbiguityGuard..cctor),
Valheim10Compatibility.Patcher.dll (Patcher.PatchPieceTableAlias)
look this field up by name through Type.GetField / Traverse.Field, which
throws AmbiguousMatchException the moment two fields share the name.
Left unaliased deliberately.
```

Both named reflectors are your own patcher assembly. No third-party plugin is
listed, so on this profile the block is entirely self-inflicted.

Contrast the neighbouring block, which is a genuine external finding and
behaves as designed:

```
[Warning:Valheim10Compatibility] BLOCKED ZoneSystem.GetZone(Vector3) -> Vector2i:
this would differ from the existing Vector2s GetZone(...) by RETURN TYPE ALONE.
```

## Impact

The README describes this bridge as restoring 15 build-piece mods. On our
profile **13 deployed mods reference `[assembly_valheim]PieceTable.m_available-
Pieces`** and none of them receive it. Found with `monodis --memberref` over
every DLL under the deployed plugin tree:

Basements, BoatAdditions, CraftyCartsRemake, Infinity_Hammer, Jotunn,
MagicRevamp, OdinCampsite, OdinsFoodBarrels, OdinsHorsePen, OdinsKingdom,
OdinsUndercroft, RavenwoodRestorations, RossItemDrawers

## Suggested fix

Exclude your own assemblies from the guard's reflector scan - skip any
candidate whose declaring assembly is `Valheim10Compatibility.Patcher` or
`Valheim10Compatibility.Adapter` before recording it as a by-name reflector.

A narrower variant, if self-exclusion feels too broad: exclude only the
injector method that implements the bridge being tested, so a genuine
unrelated reflection inside your own code would still block.

## Note on the return-type policy, offered as data not as a request

You block return-type-only overloads on the grounds that every plugin
reflecting on the name would throw `AmbiguousMatchException`. We measured the
opposite on this profile. We run a small companion preloader patcher that
injects exactly the two you block - `ZDO.GetSector() -> Vector2i` and
`ZoneSystem.GetZone(Vector3) -> Vector2i` - and across four 7-minute boots the
count of real plugin `AmbiguousMatchException` failures was **zero**, checked
line by line rather than by grep count; every residual "Ambiguous" line was a
Jotunn asset-name warning or your own BLOCKED text. Your AccessTools detour
appears to be the reason it is safe.

Leaving `ZDO.GetSector` unbridged cost us **7774 `MissingMethodException:
Method not found: Vector2i .ZDO.GetSector()` per boot** from
CreatureLevelAndLootControl, Serverside_Simulations and ServersideQoL, which
was enough to dominate the log.

Your patcher and ours compose correctly and in either order: your injector's
pre-existence check logs "present natively, no bridge needed" when it finds the
overload already there, and ours skips anything already present. So this is
informational - a config switch to opt into return-type bridges would let
profiles like ours stop carrying a second patcher, but nothing is broken as it
stands.
