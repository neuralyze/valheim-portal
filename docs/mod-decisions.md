# Mod decisions

The permanent record of stage 6 of [mod onboarding](mod-onboarding.md). One row per
decision, appended when it is made and corrected in place when it is revisited.

This file exists because undocumented decisions get re-litigated months later by
someone with less context, including your future self. Two of the entries below are
here precisely because the reasoning was once lost and the mod was nearly re-added.

A profile manifest records *what* is selected. It does not record *why*, and a
`reason` string is not an evidence trail. That is what this file is for. The
authoritative selection is always
`valheim/<WORLD>/mods/profiles/<profile>/profile-manifest.json`; if this file and a
manifest disagree, the manifest wins and this file is stale — fix it.

## How to add an entry

Append to the table, then expand it below if the evidence needs more than one line.
State the verdict from the stage-6 table (**Accept**, **Accept with config**, **Accept
with remediation**, **Reject**), the measured evidence, and the remediation actually
applied. "It seemed fine" is not evidence; see the instrument-discipline section of
[mod-onboarding.md](mod-onboarding.md) for why.

| package | version | date | verdict | scope | evidence |
|---|---|---|---|---|---|
| `Azumatt-FastLink` | 1.4.8 | prior to this record | Accept with remediation | client-only | Purely a client-side convenience; nothing for a dedicated server to load. Selected as `client-only` rather than `shared`. |
| `geekstreet-BackpacksVRFix` | 1.0.1 | prior to this record | Accept with remediation | client-only | VR compatibility shim; retained by both Flat and VR profiles so Flat clients render VR-player animation correctly. Never deployed to a dedicated server. |
| `geekstreet-EpicLootVRFix` | 1.0.17 | prior to this record | Accept with remediation | client-only | Same class as above. |
| `geekstreet-CLLCVRFix` | 2.0.1 | prior to this record | Accept with remediation | client-only | Same class as above. |
| `warpalicious-More_World_Locations_AIO` | 5.0.8 | prior to this record | Accept, pinned | shared | See below — the pin is the decision. |
| `ValheimVR-ValheimVR` | 0.9.2100 | prior to this record | Reject (as a package) | excluded | See below. |
| `NightOfGames-Huginn_Map` | 1.0.5 | prior to this record | Reject | excluded | Experimental, test-only. Not approved for a played profile. |
| `OdinPlus-CrystalLights` | 1.1.8 | prior to this record | Reject | excluded | Removed to keep the two worlds on one mod set; not a compatibility finding. |
| `Smoothbrain-Jewelcrafting` | 2.0.1 | prior to this record | Reject | excluded | Removed to keep the two worlds on one mod set; not a compatibility finding. |
| `Azumatt-AAABuildMenu` | any | prior to this record | Reject | excluded | Logged *"SearsCatalog detected ... standing down to avoid conflicts"* — installed, measured, and doing nothing. A duplicate of an installed mod is a rejection, not a tuning problem. |
| `EchoesOfBunglas-New_Horizons_Treelines` | any | prior to this record | Reject | excluded | Excluded from the redesign package set. |

## More World Locations AIO is selected, at exactly 5.0.8

**It is not excluded, and a version other than 5.0.8 is not approved.** It is selected
with scope `shared` at exactly `5.0.8` in the redesign profile of every world that
carries it.

The history is worth keeping because it is the reason for the pin. An isolated
fresh-world test of `5.0.8` — with its required plugin layout, and without the legacy
More World Traders or VR-fix plugins — failed to load the required
`dungeonblackforest` asset bundle and produced follow-on null-reference errors. The
package was quarantined on the strength of that. The layout problem was subsequently
resolved and the package returned to the selected set, at that same version.

What that leaves:

* The pin is load-bearing. Do not let `update --all` carry it forward without a fresh
  isolated fresh-world test that specifically confirms `dungeonblackforest` loads.
* Deploying it into a world whose plugin layout does not match the redesign profile
  reproduces the original failure. That was always the real variable.

## ValheimVR is excluded as a *package*, and shipped as an *artifact*

These are not in conflict and the distinction matters.

`ValheimVR-ValheimVR` 0.9.2100 is excluded from the redesign profile manifest, so no
Thunderstore build of it is pulled into a profile definition. VR support ships through
the portal instead, as a separately built, checksum-validated `vr_runtime` artifact —
and Flat profiles carry a non-VR companion built from the same source. See
[`valheimvr-packaging.md`](valheimvr-packaging.md).

So "ValheimVR is excluded" describes the package pin. It does not mean VR is
unsupported. Reading the manifest alone gets this backwards, which is why it is
recorded here.

## Dependency pins are minimums, not exact versions

Relevant to every entry above, and a frequent source of confused reports.

Thunderstore dependency strings state a minimum. `valheim_mods.py` refuses an addition
only when the profile already selects a version **older** than a dependency requires;
an equal or newer selection satisfies it and the add proceeds silently. Without that
rule every package whose declared BepInExPack version predates the profile's pin would
be unaddable.

Consequence for a decision record: "package X requires Y 1.2.3" does not mean the
profile has to select exactly `1.2.3`. If you need an exact version, it is a decision,
and it belongs in this file with its reason — as the More World Locations pin above.
