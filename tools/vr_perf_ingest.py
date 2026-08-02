#!/usr/bin/env python3
"""Stage 5 of VR mod onboarding: ingest a client diagnostics bundle and compare.

Parses per-plugin startup cost and steady-state PERF telemetry out of a diagnostics
bundle, optionally diffs it against a baseline bundle, and merges the result with the
stage-1 static scan so every mod gets ONE dossier: VR-impact classes plus measured cost.

Read-only. Output goes to stdout and optionally --json.

Exit codes: 0 nothing at or above the gate, 1 something at or above it, 2 tool error.
"""
from __future__ import annotations
import argparse, json, re, sys, time, zipfile
from pathlib import Path
from vr_scan_common import (EXIT_CLEAN, EXIT_ERROR, EXIT_FLAGGED, SEVERITIES, SEVERITY_WEIGHT,
                            fail, load_json, normalise, write_json)

STAGE = '5-ingest-and-compare'

BUNDLE_LOG = 'BepInEx/LogOutput.log'
BUNDLE_PROFILE = 'LoadTimeProfiler/latest.log'
BUNDLE_PLAYER = 'Player.log'
BUNDLE_META = 'metadata.json'

# Sections of LoadTimeProfiler/latest.log that carry per-plugin numbers, and what they mean.
PROFILE_SECTIONS = {
    'Plugin construction/Awake/OnEnable:': ('awake_ms', 'plugin construction + Awake + OnEnable'),
    'Plugin Start methods:': ('start_ms', 'plugin Start()'),
    'Scoped deep lobby attribution:': ('world_ms',
                                       'exclusive Harmony callback time in ObjectDB.Awake + ZNetScene.Awake'),
}
SECTION_END = re.compile(r'^\S')
# "  11.830 s: SouthsilArmor"  /  "  12.302 s (0.055 s + 12.246 s): Comfort Tweaks"
PROFILE_ROW = re.compile(r'^\s+(\d+(?:\.\d+)?)\s*s(?:\s*\([^)]*\))?:\s*(.+?)\s*$')
# "Total: 3 min 15.472 s" / "Chainloader.Start: 2 min 05.297 s" / "...: 1.850 s"
DURATION = re.compile(r'(?:(\d+)\s*min\s*)?(\d+(?:\.\d+)?)\s*s\b')

LOADING = re.compile(r'Loading \[(.+?) ([0-9][^\]]*)\]\s*$')
# BepInEx and the Unity console wrapper emit several timestamp shapes; try them all.
TIMESTAMPS = (
    re.compile(r'^\[?(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})\]?\s'),
    re.compile(r'^\[?\d{1,2}/\d{1,2}/\d{2,4}\s+(\d{1,2}):(\d{2}):(\d{2})()\]?\s'),
    re.compile(r'^\[?(\d{1,2}):(\d{2}):(\d{2})()\]?\s'),
)
# The console wrapper re-emits BepInEx lines; without this the same Loading line counts twice.
CONSOLE_WRAPPER = re.compile(r'^(?:\[[^\]]*\]\s*)?Console:\s*')

PERF_LINE = re.compile(r'\bPERF\s+(\w+)\s+(.*)$')
PERF_PAIR = re.compile(r'(\w+)=(-?[\d.]+|\S+)')

PERF_CAVEAT = ('PERF plugin numbers cover Update + LateUpdate + FixedUpdate only. They are LOWER '
               'BOUNDS: Harmony patches on game methods, coroutines and GC pressure are not included.')


# ---------------------------------------------------------------- bundle I/O

def read_bundle(path):
    """Accept a diagnostics .zip, an extracted directory, or a bare log file."""
    path = Path(path)
    files = {}
    if path.is_file() and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir() or info.file_size > 128 * 1024 * 1024:
                    continue
                files[info.filename] = archive.read(info).decode('utf-8', 'replace')
    elif path.is_dir():
        for entry in sorted(path.rglob('*')):
            if entry.is_file() and entry.stat().st_size <= 128 * 1024 * 1024:
                files[entry.relative_to(path).as_posix()] = entry.read_text('utf-8', 'replace')
    elif path.is_file():
        files[BUNDLE_LOG] = path.read_text('utf-8', 'replace')
    else:
        fail(f'no such bundle: {path}')
    return {'source': str(path), 'files': files}


def pick(files, *names):
    for name in names:
        if name in files:
            return files[name]
    for name in names:
        tail = name.rsplit('/', 1)[-1]
        for key, value in files.items():
            if key.rsplit('/', 1)[-1] == tail:
                return value
    return ''


def seconds(text):
    match = DURATION.search(text)
    if not match:
        return None
    minutes, secs = match.group(1), match.group(2)
    return (int(minutes) * 60 if minutes else 0) + float(secs)


# ------------------------------------------------------- (a) startup per mod

def parse_profile_sections(text):
    """Per-plugin timings from LoadTimeProfiler/latest.log. Exact, and present retroactively."""
    plugins, totals, section = {}, {}, None
    for line in text.splitlines():
        heading = line.strip()
        if heading in PROFILE_SECTIONS:
            section = PROFILE_SECTIONS[heading][0]
            continue
        if section and SECTION_END.match(line):
            section = None
        if line.startswith('Total:') or line.startswith('  Total:'):
            totals.setdefault('phase_totals', []).append(seconds(line))
        if not section:
            continue
        row = PROFILE_ROW.match(line)
        if not row:
            continue
        value, name = float(row.group(1)) * 1000.0, row.group(2)
        # A plugin can appear in both lobby phases; the later one is a second real cost.
        entry = plugins.setdefault(normalise(name), {'name': name})
        entry[section] = entry.get(section, 0.0) + value
    return plugins, totals


def parse_loading_deltas(text):
    """Fallback: delta between consecutive 'Loading [X]' lines, when the log is timestamped.

    Returns (plugins, note). BepInEx 5 writes LogOutput.log without timestamps by default, so
    this yields nothing on most bundles - parse_profile_sections is the real source.
    """
    stamped, seen = [], set()
    for raw in text.splitlines():
        line = CONSOLE_WRAPPER.sub('', raw).strip()
        match = LOADING.search(line)
        if not match:
            continue
        when = None
        for pattern in TIMESTAMPS:
            hit = pattern.match(raw.strip())
            if hit:
                hour, minute, second, milli = hit.groups()
                when = int(hour) * 3600 + int(minute) * 60 + int(second) + int(milli or 0) / 1000.0
                break
        key = (match.group(1), match.group(2))
        if key in seen:      # console-wrapper re-emission of the same line
            continue
        seen.add(key)
        stamped.append((when, match.group(1), match.group(2)))
    if len(stamped) < 2 or any(when is None for when, _n, _v in stamped):
        return {}, f'{len(stamped)} Loading lines, no usable timestamps'
    plugins = {}
    for (start, name, version), (end, _n, _v) in zip(stamped, stamped[1:]):
        span = (end - start) * 1000.0
        if span < 0:                       # midnight rollover
            span += 24 * 3600 * 1000.0
        plugins[normalise(name)] = {'name': name, 'version': version, 'load_ms': round(span, 1)}
    return plugins, f'{len(stamped)} Loading lines, serial load order assumed'


def parse_load_order(text):
    order = []
    seen = set()
    for raw in text.splitlines():
        match = LOADING.search(CONSOLE_WRAPPER.sub('', raw).strip())
        if match and match.group(1) not in seen:
            seen.add(match.group(1))
            order.append({'name': match.group(1), 'version': match.group(2)})
    return order


# --------------------------------------------------- (b) steady-state / PERF

def parse_perf(text):
    perf = {'frame': [], 'plugin': [], 'vr': [], 'compositor': [], 'sweep': [], 'other': []}
    for raw in text.splitlines():
        match = PERF_LINE.search(raw)
        if not match:
            continue
        kind, rest = match.group(1), match.group(2)
        row = {}
        for key, value in PERF_PAIR.findall(rest):
            try:
                row[key] = float(value) if re.fullmatch(r'-?\d+(?:\.\d+)?', value) else value
            except ValueError:
                row[key] = value
        perf.setdefault(kind if kind in perf else 'other', []).append(row)
    return perf


def sweep_verdict(rows):
    """(c) frame time falling with render scale means GPU-bound, flat means CPU-bound."""
    usable = [r for r in rows if isinstance(r.get('scale'), float) and isinstance(r.get('mean'), float)]
    if len(usable) < 3:
        return {'verdict': 'insufficient data',
                'detail': f'{len(usable)} usable PERF sweep rows; 3 are required'}
    usable.sort(key=lambda r: r['scale'])
    low, high = usable[0], usable[-1]
    if low['mean'] <= 0:
        return {'verdict': 'insufficient data', 'detail': 'non-positive mean frame time at the lowest scale'}
    change = (high['mean'] - low['mean']) / low['mean']
    verdict = 'GPU-bound' if change >= 0.15 else ('CPU-bound' if change <= 0.05 else 'mixed')
    return {'verdict': verdict,
            'detail': f'mean {low["mean"]:.2f} ms at scale {low["scale"]:g} -> {high["mean"]:.2f} ms '
                      f'at scale {high["scale"]:g} ({change * 100:+.1f}%)',
            'change_fraction': round(change, 4), 'rows': len(usable)}


# ------------------------------------------------------------------ ingest

def ingest(path):
    bundle = read_bundle(path)
    files = bundle['files']
    profile_text = pick(files, BUNDLE_PROFILE)
    log_text = pick(files, BUNDLE_LOG) or pick(files, BUNDLE_PLAYER)

    plugins, totals = parse_profile_sections(profile_text)
    deltas, delta_note = parse_loading_deltas(log_text)
    if plugins:
        startup_source = 'LoadTimeProfiler per-plugin sections (exact)'
        for key, value in deltas.items():
            plugins.setdefault(key, {'name': value['name']})['load_ms'] = value['load_ms']
    elif deltas:
        plugins, startup_source = deltas, f'Loading-line timestamp deltas ({delta_note})'
    else:
        startup_source = f'none - no LoadTimeProfiler section and {delta_note}'

    for entry in plugins.values():
        entry['startup_ms'] = round(sum(entry.get(field, 0.0) for field in
                                        ('awake_ms', 'start_ms', 'world_ms')) or entry.get('load_ms', 0.0), 1)
        for field in ('awake_ms', 'start_ms', 'world_ms'):
            if field in entry:
                entry[field] = round(entry[field], 1)

    perf = parse_perf(log_text + '\n' + pick(files, BUNDLE_PLAYER))
    for row in perf['plugin']:
        key = normalise(str(row.get('name', '')))
        if key:
            entry = plugins.setdefault(key, {'name': row.get('name')})
            entry['ms_per_frame'] = row.get('msPerFrame')
            entry['pct'] = row.get('pct')
            entry['calls'] = row.get('calls')

    metadata = {}
    if BUNDLE_META in files:
        try:
            metadata = json.loads(files[BUNDLE_META])
        except json.JSONDecodeError:
            metadata = {}

    return {'source': bundle['source'], 'metadata': metadata, 'plugins': plugins,
            'startup_source': startup_source, 'startup_note': delta_note,
            'load_order': parse_load_order(log_text), 'perf': perf,
            'frame': perf['frame'][-1] if perf['frame'] else None,
            'vr': perf['vr'][-1] if perf['vr'] else None,
            'compositor': perf['compositor'][-1] if perf['compositor'] else None,
            'bound_by': sweep_verdict(perf['sweep']),
            'phase_totals': totals.get('phase_totals', [])}


# ------------------------------------------------------------------- merge

def static_index(path):
    """Join key -> {'package', 'severity', 'score', 'classes'} from a stage-1 --json file."""
    data = load_json(path)
    index = {}
    for result in data.get('ranked', []):
        record = {'package': result['package'], 'severity': result['severity'],
                  'score': result['score'], 'classes': result['classes'],
                  'handled_by': result.get('handled_by')}
        index[normalise(result['name'])] = record
        for plugin in result.get('plugins', []):
            if plugin.get('name'):
                index[normalise(plugin['name'])] = record
    # Packages with no findings still need a key so "expensive but VR-fine" is reachable.
    for plugin in data.get('plugins', []):
        if plugin.get('name'):
            index.setdefault(normalise(plugin['name']),
                             {'package': plugin.get('assembly'), 'severity': 'info',
                              'score': 0, 'classes': {}, 'handled_by': None})
    return index, data


def quadrant(expensive, broken):
    if expensive and broken:
        return 'expensive AND VR-broken', 'reject or remove candidate'
    if broken:
        return 'cheap but VR-broken', 'fix candidate'
    if expensive:
        return 'expensive but VR-fine', 'frame-rate decision, not a VR decision'
    return 'cheap and VR-fine', 'no action'


def dossiers(current, baseline, static, args):
    index, _static_data = static if static else ({}, {})
    rows = []
    for key, entry in current['plugins'].items():
        before = (baseline or {}).get('plugins', {}).get(key, {})
        stat = index.get(key, {})
        startup = entry.get('startup_ms', 0.0) or 0.0
        ms_frame = entry.get('ms_per_frame')
        expensive = startup >= args.startup_ms or (ms_frame is not None and ms_frame >= args.frame_ms)
        broken = SEVERITY_WEIGHT.get(stat.get('severity', 'info'), 0) >= SEVERITY_WEIGHT[args.min_severity]
        label, action = quadrant(expensive, broken)
        rows.append({
            'plugin': entry.get('name'), 'package': stat.get('package'),
            'startup_ms': startup, 'awake_ms': entry.get('awake_ms'), 'start_ms': entry.get('start_ms'),
            'world_ms': entry.get('world_ms'), 'load_ms': entry.get('load_ms'),
            'ms_per_frame': ms_frame, 'pct': entry.get('pct'), 'calls': entry.get('calls'),
            'static_severity': stat.get('severity', 'not-scanned'),
            'static_score': stat.get('score', 0), 'static_classes': stat.get('classes', {}),
            'handled_by': stat.get('handled_by'),
            'startup_delta_ms': (round(startup - (before.get('startup_ms') or 0.0), 1)
                                 if before else None),
            'frame_delta_ms': (round((ms_frame or 0.0) - (before.get('ms_per_frame') or 0.0), 3)
                               if before and ms_frame is not None else None),
            'quadrant': label, 'action': action,
        })
    rows.sort(key=lambda r: (-(r['ms_per_frame'] or 0.0) * 1000, -r['startup_ms'], -r['static_score']))
    return rows


def new_plugins(current, baseline):
    if not baseline:
        return []
    known = set(baseline['plugins'])
    return sorted(entry.get('name') for key, entry in current['plugins'].items() if key not in known)


# ------------------------------------------------------------------ report

def report(current, baseline, rows, args, stream=sys.stdout):
    write = lambda text='': print(text, file=stream)
    meta = current['metadata']
    write(f'stage={STAGE} bundle={current["source"]} '
          f'release={meta.get("ReleaseID", "?")} profile={meta.get("Profile", "?")} '
          f'client={meta.get("ClientType", "?")} collected={meta.get("CollectedAt", "?")}')
    write(f'plugins={len(current["plugins"])} load_order={len(current["load_order"])} '
          f'startup_source={current["startup_source"]}')
    frame, vr, compositor = current['frame'], current['vr'], current['compositor']
    write('frame       ' + (' '.join(f'{k}={v}' for k, v in frame.items()) if frame
                            else 'no PERF frame rows in this bundle'))
    write('vr          ' + (' '.join(f'{k}={v}' for k, v in vr.items()) if vr
                            else 'no PERF vr rows in this bundle'))
    write('compositor  ' + (' '.join(f'{k}={v}' for k, v in compositor.items()) if compositor
                            else 'no PERF compositor rows in this bundle'))
    bound = current['bound_by']
    write(f'bound_by    {bound["verdict"]} ({bound["detail"]})')
    if baseline:
        write(f'baseline    {baseline["source"]} release={baseline["metadata"].get("ReleaseID", "?")}')
        added = new_plugins(current, baseline)
        write(f'new_plugins {len(added)}' + (': ' + ', '.join(added) if added else ''))

    write('\n== per-mod dossier (ranked by measured cost, then static severity) ==')
    header = (f'{"plugin":<34} {"startup":>9} {"awake":>8} {"start":>8} {"world":>8} '
              f'{"ms/frame":>9} {"sev":<7} {"quadrant":<24} classes')
    write(header)
    for row in rows[:args.top]:
        if (row['startup_ms'] < args.min_startup_ms and not row['ms_per_frame']
                and row['static_severity'] in ('info', 'not-scanned')):
            continue
        classes = ' '.join(f'{c}x{n}' for c, n in sorted(row['static_classes'].items()))
        delta = f'  d={row["startup_delta_ms"]:+.1f}ms' if row['startup_delta_ms'] is not None else ''
        write(f'{(row["plugin"] or "?")[:34]:<34} {row["startup_ms"]:>8.1f}ms '
              f'{_ms(row["awake_ms"]):>8} {_ms(row["start_ms"]):>8} {_ms(row["world_ms"]):>8} '
              f'{_ms(row["ms_per_frame"], 3):>9} {row["static_severity"]:<7} '
              f'{row["quadrant"]:<24} {classes}{delta}')

    write('\n== quadrants ==')
    for label in ('expensive AND VR-broken', 'cheap but VR-broken', 'expensive but VR-fine'):
        members = [r for r in rows if r['quadrant'] == label]
        if not members:
            write(f'{label:<24} (none)')
            continue
        write(f'{label:<24} {len(members)}  -> {members[0]["action"]}')
        for row in members[:args.top]:
            write(f'    {(row["plugin"] or "?"):<34} startup={row["startup_ms"]:.1f}ms '
                  f'ms/frame={_ms(row["ms_per_frame"], 3)} sev={row["static_severity"]} '
                  f'score={row["static_score"]}'
                  + (f' handled_by={row["handled_by"]}' if row['handled_by'] else ''))

    write('\n== limitations ==')
    write(f'  {PERF_CAVEAT}')
    write('  Startup deltas assume serial plugin load order (BepInEx Chainloader is serial).')
    write('  A/B through separate probe profiles is the only route to a true total-cost number.')
    if not current['perf']['plugin']:
        write('  This bundle carries no PERF plugin rows, so steady-state cost is UNMEASURED here; '
              'only startup cost is real.')


def _ms(value, digits=1):
    return '-' if value is None else f'{value:.{digits}f}ms'


def main():
    parser = argparse.ArgumentParser(
        description='Stage 5 of VR mod onboarding: ingest a diagnostics bundle, compare, and merge '
                    'with the stage-1 static scan.',
        epilog='exit 0 = nothing at or above the gate, 1 = something at or above it, 2 = tool error')
    parser.add_argument('--bundle', type=Path, required=True,
                        help='diagnostics .zip, an extracted bundle directory, or a bare LogOutput.log')
    parser.add_argument('--baseline', type=Path, help='bundle captured before the mod was installed')
    parser.add_argument('--static', type=Path, help='stage-1 --json output to merge into the dossier')
    parser.add_argument('--json', type=Path, help='write the machine-readable stage-5 result here')
    parser.add_argument('--min-severity', choices=SEVERITIES, default='high',
                        help='static severity that counts as VR-broken (default: high)')
    parser.add_argument('--startup-ms', type=float, default=1000.0,
                        help='startup cost that counts as expensive (default: 1000)')
    parser.add_argument('--frame-ms', type=float, default=0.5,
                        help='steady-state ms/frame that counts as expensive (default: 0.5)')
    parser.add_argument('--min-startup-ms', type=float, default=0.0,
                        help='hide dossier rows cheaper than this with no other signal')
    parser.add_argument('--top', type=int, default=40, help='rows per table (default: 40)')
    args = parser.parse_args()

    started = time.monotonic()
    current = ingest(args.bundle)
    baseline = ingest(args.baseline) if args.baseline else None
    static = static_index(args.static) if args.static else None
    rows = dossiers(current, baseline, static, args)
    elapsed = time.monotonic() - started

    report(current, baseline, rows, args)
    gated = [r for r in rows if r['quadrant'] in ('expensive AND VR-broken', 'cheap but VR-broken')]
    print(f'\ngated_mods={len(gated)} elapsed={elapsed:.2f}s')

    if args.json:
        write_json(args.json, {
            'schema': 1, 'stage': STAGE,
            'bundle': str(args.bundle),
            'baseline': str(args.baseline) if args.baseline else None,
            'static': str(args.static) if args.static else None,
            'metadata': current['metadata'],
            'startup_source': current['startup_source'],
            'thresholds': {'min_severity': args.min_severity, 'startup_ms': args.startup_ms,
                           'frame_ms': args.frame_ms},
            'frame': current['frame'], 'vr': current['vr'], 'compositor': current['compositor'],
            'bound_by': current['bound_by'],
            'phase_totals_seconds': current['phase_totals'],
            'load_order': current['load_order'],
            'new_plugins': new_plugins(current, baseline),
            'dossiers': rows,
            'perf_rows': current['perf'],
            'caveats': [PERF_CAVEAT,
                        'Startup deltas assume serial plugin load order.',
                        'A/B through separate probe profiles is the only route to total cost.'],
        })
        print(f'json={args.json}')
    return EXIT_FLAGGED if gated else EXIT_CLEAN


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, json.JSONDecodeError) as error:
        print(f'error: {error}', file=sys.stderr)
        raise SystemExit(EXIT_ERROR)
