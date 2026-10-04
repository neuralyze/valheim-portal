#!/usr/bin/env python3
"""Pre-flight gate: prove the build harness's console verbs exist before a session uses them.

WHY THIS EXISTS. On 2026-09-19 a profile scope rebuild reclassified
JereKuusela-World_Edit_Commands from `shared` to `client-only` on ulfsland-dn, the
SERVER profile, and the 11:22Z deploy duly stopped installing it server-side. The
classifier was reasoning about how a PLAYER uses the package - a client-side
convenience tool, correctly - and had no category for what it is to this project: the
only verb in the entire toolchain that CREATES anything. `spawn_object` vanished. For
nine hours every terrain write deleted a zone's `_TerrainCompiler` and put nothing
back, and `InvokeConsoleCommand.OnHandle` answered `Command '<x>' executed.` to all of
it, because that reply is a DELIVERY RECEIPT for a string and never the console's
output. One zone of world content was destroyed and five agents were blocked.

The category the classifier lacked, and the one this file exists to encode:

    A CLIENT-SIDE TOOL THE SERVER MUST NONETHELESS RUN.

TWO LAYERS, AND THE FIRST IS THE GATE.

  ARTEFACT  Read the deployed plugin tree. Every required package directory must be
            there and every required verb literal must appear in some DLL inside it.
            No server, no socket, no side effects, and it is the layer that would have
            caught this at 11:22Z instead of at 20:58Z. Verifying the artefact rather
            than the plan is the whole lesson: the manifest SAID the package was
            installed the entire time.

  LIVE      Optional. Send each verb and read the CONTAINER'S STDOUT, never the RCON
            reply. The discriminator is three-way and this is measured, not assumed:

                Console: Unknown command 'spawn_object'        -> verb ABSENT
                Console: Error: Entity id ... not recognized   -> verb RAN (bad args)
                (RCON reply, identical for both)               -> proves NOTHING

            Only non-mutating probes are sent: a bare verb, or arguments chosen to fail
            the parser. Upgrade World's staged verbs are NEVER probed live - sent bare
            with no `start`, `zones_reset` STAGES a world-wide reset and sits in the
            queue waiting for the next `start` anybody sends. For those the artefact
            layer is the only honest answer and it is sufficient.

KNOWN-ABSENT IS PART OF THE ANSWER. `terrain_reset` and `objects_reset` are named in
the harness's own STAGED_VERBS in ledger/replay.py and exist in NO installed plugin -
not UpgradeWorld.dll 1.82.0, not anything on either cache side. No emitter for either
was found, so this is armed rather than firing, but a list that quietly omitted them
would be claiming a surface the server does not have. They are reported every run.
A verb listed in the harness's own taxonomy is not a verb that exists.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

# The packages that are not convenience mods on this fleet: they are the build
# harness's ABI. Each entry names the verbs it owns, so a missing verb is reported
# against the package that should have carried it rather than as a bare string.
#
# `verbs` are literals that must appear in one of the package's assemblies. They are
# the command names the harness actually sends, taken from ledger/replay.py's
# NATIVE_VERBS and STAGED_VERBS plus the consoleCommand path - not from a README.
HARNESS_PACKAGES = {
    'JereKuusela-World_Edit_Commands': {
        'install_name': 'World_Edit_Commands',
        'verbs': ['spawn_object'],
        'live_probe': {'spawn_object': 'consoleCommand spawn_object'},
        'category': 'client-side tool the server must nonetheless run',
        'reason': (
            'spawn_object and the data=<entry> mechanism. The ONLY constructive verb in '
            'the toolchain: every terrain_write, placement and crossing plan ends in it. '
            'Its absence is silent because deleteObjects still works, so a delete-then-'
            'respawn wire destroys and does not restore.'
        ),
    },
    'JereKuusela-Upgrade_World': {
        'install_name': 'Upgrade_World',
        'verbs': ['zones_generate', 'zones_restore', 'zones_reset',
                  'objects_count', 'objects_remove'],
        # objects_count is the one Upgrade World verb that is safe to send live: its
        # reply is a short table and a bad id is a parse error, not an operation.
        'live_probe': {'objects_count': 'consoleCommand objects_count id=__preflight__ pos=0,0 max=1'},
        'category': 'server-side harness dependency',
        'reason': (
            'Zone generation and object census/removal. A terrain write into an '
            'ungenerated zone is discarded AT GENERATION TIME - measured on Ulfsland '
            'zone (-18,28), where a valid 40x40 m flatten spawned, the zone generated, '
            'and all 190 objects planted on the unmodified generator height. That '
            'failure never produces an Unknown command at the point that matters; it '
            'produces a road somewhere else, permanently.'
        ),
    },
    'Tristan-ValheimRcon': {
        'install_name': 'ValheimRcon',
        'verbs': ['findObjects', 'deleteObjects'],
        # Native verbs answer with CONTENT, not a receipt, so their probe is judged on the
        # reply. Both are given a prefab that cannot exist, so deleteObjects matches nothing.
        'live_probe': {'findObjects': 'findObjects -prefab __preflight_nonexistent__ -near 0 0 0 1',
                       'deleteObjects': 'deleteObjects -zone 9999 9999 -prefab __preflight_nonexistent__'},
        'category': 'transport',
        'reason': (
            'The socket itself plus the native findObjects/deleteObjects verbs. Native '
            'replies carry real content, which is why findObjects survives when a '
            'content mod does not - and why every census in this project depends on it.'
        ),
    },
    'JereKuusela-Server_devcommands': {
        'install_name': 'Server_devcommands',
        'verbs': ['devcommands'],
        'live_probe': {},
        'category': 'server-side harness dependency',
        'reason': (
            'The console infrastructure every consoleCommand verb registers into, and a '
            'hard BepInDependency of World_Edit_Commands (>=1.110). It stayed shared '
            'through the 2026-09-19 rebuild, which is the only reason any console work '
            'survived at all.'
        ),
    },
}

# Named in the harness's STAGED_VERBS and owned by nothing. Reported, never required:
# the honest list is the one that says what is missing as well as what is present.
KNOWN_ABSENT_VERBS = {
    'terrain_reset': 'in ledger/replay.py STAGED_VERBS; no literal in UpgradeWorld.dll 1.82.0 or any other installed plugin',
    'objects_reset': 'in ledger/replay.py STAGED_VERBS; no literal in UpgradeWorld.dll 1.82.0 or any other installed plugin',
}

# A staged Upgrade World verb sent bare does not fail - it QUEUES. `zones_reset` with no
# `start` stages a world-wide reset (86 zones, observed) and waits for the next `start`
# ANYBODY sends. Never probe these live; the artefact layer answers for them.
NEVER_PROBE_LIVE = {'zones_generate', 'zones_reset', 'zones_restore', 'objects_remove',
                    'terrain_reset', 'objects_reset'}

# Verbs whose reply IS the result. ValheimRcon natives come back on the socket with real
# content; everything else goes through `consoleCommand`, whose reply is a delivery
# receipt and must never be read as an outcome.
NATIVE_VERBS = {'findObjects', 'deleteObjects'}
NATIVE_RAN = re.compile(r'(Found \d+ objects|No objects found|Deleting \d+ objects)')

# The negative control. Sent on every live run so the probe proves it can still SEE an
# absent verb; without it a clean pass is indistinguishable from a probe that has stopped
# looking. MEASURED 2026-09-19T17:05:54 against the restored server: the console answered
# `Unknown command '__definitely_not_a_verb__'` while the RCON reply said `executed.`
CONTROL_VERB = '__definitely_not_a_verb__'

DEFAULT_TREE = Path('/media/big4/projects/game/valheim/Ulfsland/data/bepinex/BepInEx/plugins')
DEFAULT_ARCHIVE = Path('/media/big4/projects/game/valheim/Ulfsland/logs-archive/console.log')
DEFAULT_CONTAINER = 'valheim-server-Ulfsland'
UNKNOWN_COMMAND = re.compile(r"Unknown command '([^']+)'")


def verb_in_tree(tree: Path, verb: str) -> list[str]:
    """Package directories under `tree` whose assemblies contain `verb` as a literal.

    Both encodings are searched. .NET user strings are UTF-16LE; a verb registered
    from a resource or a const in another section can be plain ASCII. Searching one
    encoding only is the same shape of mistake as a grep that cannot match its target.
    """
    needles = (verb.encode('utf-16-le'), verb.encode('ascii'))
    found = []
    if not tree.is_dir():
        return found
    for package in sorted(p for p in tree.iterdir() if p.is_dir()):
        for dll in package.rglob('*.dll'):
            try:
                data = dll.read_bytes()
            except OSError:
                continue
            if any(n in data for n in needles):
                found.append(package.name)
                break
    return found


def artefact_layer(tree: Path, extra_verbs: list[str]) -> dict:
    """The gate. Packages present, verbs present, and every miss named."""
    result = {'tree': str(tree), 'tree_exists': tree.is_dir(), 'packages': {},
              'missing_packages': [], 'missing_verbs': [], 'known_absent': {},
              'extra_verbs': {}}
    if not tree.is_dir():
        result['missing_packages'] = sorted(HARNESS_PACKAGES)
        return result
    for identifier, spec in HARNESS_PACKAGES.items():
        directory = tree / spec['install_name']
        entry = {'install_name': spec['install_name'], 'present': directory.is_dir(),
                 'category': spec['category'], 'verbs': {}}
        if not entry['present']:
            result['missing_packages'].append(identifier)
        for verb in spec['verbs']:
            owners = verb_in_tree(tree, verb)
            entry['verbs'][verb] = owners
            if not owners:
                result['missing_verbs'].append(f'{verb} (expected in {identifier})')
        result['packages'][identifier] = entry
    for verb, note in KNOWN_ABSENT_VERBS.items():
        owners = verb_in_tree(tree, verb)
        result['known_absent'][verb] = {'owners': owners, 'note': note,
                                        'still_absent': not owners}
    for verb in extra_verbs:
        result['extra_verbs'][verb] = verb_in_tree(tree, verb)
    return result


def rcon_send(command: str, repo: Path, timeout: float) -> tuple[bool, str]:
    """Send one command. The reply is DISCARDED for verdict purposes by design."""
    script = repo / 'tools' / 'jumpstart' / 'terraform' / 'rcon.py'
    try:
        done = subprocess.run([sys.executable, str(script), '--max-print', '0', command],
                              capture_output=True, text=True, timeout=timeout,
                              cwd=str(script.parent))
    except subprocess.TimeoutExpired:
        return False, 'timeout'
    return done.returncode == 0, (done.stdout + done.stderr).strip()[-400:]


def container_log(container: str) -> str | None:
    """Path of the container's CURRENT supervisor stdout log, or None."""
    done = subprocess.run(['docker', 'exec', container, 'sh', '-c',
                           'ls -t /var/log/supervisor/valheim-server-stdout*.log | head -1'],
                          capture_output=True, text=True)
    path = done.stdout.strip()
    return path if done.returncode == 0 and path else None


def container_lines(container: str, log: str, start: int) -> list[str]:
    done = subprocess.run(['docker', 'exec', container, 'sh', '-c',
                           f'tail -n +{start} {log}'], capture_output=True, text=True)
    return done.stdout.splitlines() if done.returncode == 0 else []


def container_length(container: str, log: str) -> int:
    done = subprocess.run(['docker', 'exec', container, 'sh', '-c', f'wc -l < {log}'],
                          capture_output=True, text=True)
    try:
        return int(done.stdout.strip())
    except ValueError:
        return 0


def live_layer(repo: Path, container: str, archive: Path, timeout: float, settle: float) -> dict:
    """Send the safe probes and read the CONSOLE, not the reply.

    Reads the container's own stdout, NOT the host archiver. MEASURED 2026-09-19T17:04Z:
    the archiver trails the container by more than the settle window, so every probe came
    back NO_CONSOLE_OUTPUT while the same commands were visibly answering inside the
    container. An instrument that reads a lagging copy reports absence for a verb that is
    present - which is the exact failure this file exists to prevent, so it is not allowed
    to be the failure this file commits. The archiver stays useful for AFTERWARDS: the
    container log rotates at 1 MB and has held roughly 25 minutes tonight, so the archive
    path is recorded here as where the evidence will still be readable later.

    The control verb is sent FIRST. If a string that cannot possibly be a command does not
    produce `Unknown command`, the probe is not reading the console and every later PASS
    in this run is worthless; the run fails on the control rather than on the subject.
    """
    result = {'container': container, 'archive_for_later': str(archive), 'probes': {},
              'absent': [], 'unreadable': False, 'control': {}}
    log = container_log(container)
    if not log:
        result['unreadable'] = True
        return result
    result['log'] = log

    before = container_length(container, log)
    sent, reply = rcon_send(f'consoleCommand {CONTROL_VERB}', repo, timeout)
    time.sleep(settle)
    console = [line for line in container_lines(container, log, before + 1) if 'Console:' in line]
    saw_unknown = any(CONTROL_VERB in line and UNKNOWN_COMMAND.search(line) for line in console)
    result['control'] = {'verb': CONTROL_VERB, 'sent': sent, 'saw_unknown_command': saw_unknown,
                         'console': console[-2:], 'rcon_reply_ignored': reply[-120:]}
    if not saw_unknown:
        result['unreadable'] = True
        return result

    for identifier, spec in HARNESS_PACKAGES.items():
        for verb, command in spec['live_probe'].items():
            if verb in NEVER_PROBE_LIVE:
                continue
            before = container_length(container, log)
            sent, reply = rcon_send(command, repo, timeout)
            time.sleep(settle)
            console = [line for line in container_lines(container, log, before + 1)
                       if 'Console:' in line]
            if verb in NATIVE_VERBS:
                # The reply IS the result for a native verb, and it never reaches the
                # console at all.
                verdict = 'REGISTERED' if (sent and NATIVE_RAN.search(reply)) else 'NO_RESULT'
                evidence = [reply.splitlines()[-1]] if reply else []
            else:
                # The ONLY sound discriminator, and it is an ABSENCE: a registered verb
                # that rejects its arguments and a verb that does not exist produce
                # IDENTICAL `executed.` receipts, so the console is the sole witness and
                # the pass condition is "no `Unknown command '<verb>'` in the window".
                # Deliberately NOT "some console output appeared": this console is shared
                # with every other agent on the world, so a neighbour's reply lands in the
                # window and would manufacture a pass. The control probe above is what
                # makes the absence meaningful - it proves this reader can still SEE an
                # Unknown command line when there is one to see.
                unknown = [line for line in console if UNKNOWN_COMMAND.search(line) and verb in line]
                verdict = 'ABSENT' if unknown else ('REGISTERED' if sent else 'NOT_SENT')
                evidence = (unknown or console)[-3:]
            result['probes'][verb] = {
                'command': command, 'sent': sent, 'verdict': verdict,
                'evidence': evidence, 'rcon_reply_ignored': reply[-120:],
                'evidence_note': ('native reply, carries content' if verb in NATIVE_VERBS
                                  else 'shared console window; verdict is the ABSENCE of '
                                       "Unknown command '<verb>', not the presence of these lines"),
            }
            if verdict != 'REGISTERED':
                result['absent'].append(verb)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prove the build harness's console verbs exist before a session uses them.",
        epilog='exit 0 = every required verb present, 1 = something required is missing, 2 = could not run')
    parser.add_argument('--tree', type=Path, default=DEFAULT_TREE,
                        help='deployed plugin tree to inspect (default: Ulfsland runtime tree)')
    parser.add_argument('--live', action='store_true',
                        help='also send the safe probes and read the container console')
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--archive', type=Path, default=DEFAULT_ARCHIVE,
                        help='host-side copy of the container stdout')
    parser.add_argument('--container', default=DEFAULT_CONTAINER,
                        help='container whose stdout carries the console')
    parser.add_argument('--verb', action='append', default=[],
                        help='also report this verb (repeatable). Point it at a verb you '
                             'know is gone to prove the probe can fail.')
    parser.add_argument('--require-verb', action='append', default=[],
                        help='same as --verb, but its absence FAILS the run. The '
                             'negative-direction proof.')
    parser.add_argument('--timeout', type=float, default=45.0)
    parser.add_argument('--settle', type=float, default=3.0)
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()

    report = {'artefact': artefact_layer(args.tree, args.verb + args.require_verb)}
    if args.live:
        report['live'] = live_layer(args.repo, args.container, args.archive,
                                    args.timeout, args.settle)

    art = report['artefact']
    print(f"tree={art['tree']} exists={art['tree_exists']}")
    for identifier, entry in art['packages'].items():
        state = 'PRESENT' if entry['present'] else 'MISSING'
        print(f"  {state:8} {identifier}  [{entry['category']}]")
        for verb, owners in entry['verbs'].items():
            print(f"      {'ok  ' if owners else 'GONE'} {verb:16} {','.join(owners) or '- no assembly carries this literal'}")
    for verb, info in art['known_absent'].items():
        state = 'still absent' if info['still_absent'] else f"NOW PRESENT in {','.join(info['owners'])}"
        print(f"  known-absent {verb:16} {state}")
        print(f"      {info['note']}")
    for verb, owners in art['extra_verbs'].items():
        print(f"  extra        {verb:16} {','.join(owners) or 'ABSENT'}")

    failures = list(art['missing_packages']) + list(art['missing_verbs'])
    for verb in args.require_verb:
        if not art['extra_verbs'].get(verb):
            failures.append(f'{verb} (required via --require-verb)')

    if 'live' in report:
        live = report['live']
        control = live.get('control') or {}
        if control:
            print(f"  control      {control['verb']}  saw_unknown_command={control['saw_unknown_command']}")
            for line in control.get('console', []):
                print(f"      {line[:200]}")
        if live['unreadable']:
            print('live: FAILED - could not read the container console, or the control verb '
                  'did not produce "Unknown command". Every live PASS below would be '
                  'meaningless, so none is claimed.')
            failures.append('live console unreadable or control failed')
        for verb, probe in live['probes'].items():
            source = 'native reply' if verb in NATIVE_VERBS else 'console'
            print(f"  live {probe['verdict']:16} {verb:14} (verdict from {source})")
            for line in probe['evidence']:
                print(f"      {line[:200]}")
        failures.extend(f'{verb} (live probe)' for verb in live['absent'])
        print(f"  evidence will outlive the 1 MB rotation at {live['archive_for_later']}")

    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + '\n')
        print(f'json={args.json}')

    if failures:
        print('PREFLIGHT FAILED: ' + '; '.join(failures))
        print('Do not start a build session. A missing constructive verb does not stop the '
              'harness - it lets the destructive half of every two-part operation land alone.')
        return 1
    print('PREFLIGHT OK: every required harness verb is present.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
