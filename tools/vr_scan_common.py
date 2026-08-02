#!/usr/bin/env python3
"""Shared vocabulary for the VR mod-onboarding tools (stage 1 scan, stage 5 ingest)."""
from __future__ import annotations
import json, re, sys
from pathlib import Path

SEVERITIES = ('info', 'low', 'medium', 'high')
SEVERITY_WEIGHT = {'info': 0, 'low': 1, 'medium': 4, 'high': 10}

CLASS_TITLES = {
    'C1': 'screen-space canvas not converted to world space',
    'C2': 'UI object never assigned the UI layer',
    'C3': 'custom ZInput action with no VR binding',
    'C4': 'keyboard-only hotkey / KeyboardShortcut config',
    'C5': 'mouse-dependent UI (buttons, wheel, drag)',
    'C6': 'hover/tooltip text naming keyboard or gamepad keys',
    'C7': 'camera or quality-settings mutation',
}
CLASS_RECALL = {
    'C1': 'high recall, medium precision - nesting is only decidable when the reparenting call sits in the same method',
    'C2': 'high recall, low precision - absence-of-symbol test across the whole assembly',
    'C3': 'high recall, high precision - direct ZInput.AddButton reference',
    'C4': 'high recall, high precision for KeyboardShortcut binds; medium precision for raw Input.GetKey*',
    'C5': 'high recall, low precision - mouse input is not always UI-bound',
    'C6': 'high recall, low precision - string heuristics over the user-string heap',
    'C7': 'high recall, high precision - direct property setter references',
}

# Exit codes. Stage tools share these so a pipeline can gate on them.
EXIT_CLEAN = 0      # nothing at or above the threshold
EXIT_FLAGGED = 1    # findings at or above the threshold
EXIT_ERROR = 2      # the tool could not run


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    return path


def split_package(stem):
    """'AzuAutoStore-3.0.14' -> ('AzuAutoStore', '3.0.14')."""
    match = re.match(r'^(.*)-(\d+(?:\.\d+)*)$', stem)
    return (match.group(1), match.group(2)) if match else (stem, '')


def manifest_entries(path):
    """Short package name (lowercased) -> {'version', 'scope'} from a profile-manifest.json."""
    data = load_json(path)
    entries = {}
    for key, default in (('packages', 'shared'), ('client_only_packages', 'client-only'),
                         ('manual_server_packages', 'server')):
        for item in data.get(key, []):
            identifier = item.get('identifier', '')
            short = identifier.partition('-')[2] or identifier
            entries[short.lower()] = {'identifier': identifier, 'version': item.get('version'),
                                      'scope': item.get('scope', default)}
    return entries


def normalise(name):
    """Fold a plugin display name / package name to a join key.

    'Epic Loot' / 'EpicLoot' / 'epic_loot-0.9.1' all fold to 'epicloot'.
    """
    return re.sub(r'[^a-z0-9]', '', (name or '').lower())


def fail(message):
    print(f'error: {message}', file=sys.stderr)
    raise SystemExit(EXIT_ERROR)
