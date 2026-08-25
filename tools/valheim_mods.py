#!/usr/bin/env python3
"""Manifest-driven Valheim mod controller."""
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, subprocess, sys, tempfile, zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import requests
from packaging.version import Version

if __package__:
    from . import config_merge, portal_paths, profile_store, settings_history
else:
    import config_merge
    import portal_paths
    import profile_store
    import settings_history

TOOLS_ROOT = portal_paths.TOOLS_ROOT
API = 'https://thunderstore.io/c/valheim/api/v1/package/'

# ---------------------------------------------------------------------------------------------
# The player-visible mod list
# ---------------------------------------------------------------------------------------------
# The two profiles a player installs. Everything outside this pair - today the `admin` profile,
# tomorrow whatever else an operator creates - is off the player list without anything naming it,
# and that is the whole point: a list of admin tool NAMES rots the moment a new tool is added,
# while a set difference cannot. Measured on this fleet 2026-08-21: union(vr, flat) is 108
# identifiers, admin is 111, and the 6 that are admin's alone (Azumatt-PerfectPlacement,
# JereKuusela-Structure_Tweaks, JereKuusela-Upgrade_World, Tristan-ValheimRcon,
# sighsorry-AdminQoL, sighsorry-LoadTimeProfiler) fall out with no rule mentioning them.
#
# `vr` and `flat` are also the only two client types the portal accepts anywhere
# (internal/agent/agent.go refuses any other), so this is a closed pair, not a growing list.
PLAYER_EDITIONS = ('vr', 'flat')

# Thunderstore's own category for code that exists for other mods to call.
LIBRARY_CATEGORY = 'Libraries'

# Categories that mean a player can see or touch the thing. A package tagged Libraries AND one of
# these is a mod whose author also exposes an API, not a library: dropping on the Libraries tag
# alone removed Smoothbrain-Backpacks (Gear, Libraries) and Smoothbrain-CreatureLevelAndLootControl
# (Enemies, Libraries), two of the most visible mods on the server. Checked against all 7
# Libraries-tagged packages in the player union: the other five carry no content category at all.
CONTENT_CATEGORIES = frozenset({
    'Gear', 'Building', 'Enemies', 'Crafting', 'Vehicles', 'Transportation', 'Skins',
    'World Generation',
})

# Installed in a player edition, and still nothing a player can see or use. Every entry carries
# the reason it is here, because "meaningless to players" is a judgement and an unexplained
# identifier here is indistinguishable from a mistake.
#
# Libraries do NOT belong in this dict: the category rule above already removes them.
#
# The set difference is necessary but NOT sufficient, which was measured and not assumed: only 6
# packages are the admin profile's alone, while four admin tools ship to BOTH player editions as
# client_only_packages and so survive the union. They are named here because there is no structural
# fact left to filter them on - what makes them admin tools is that the server's adminlist.txt
# gates every command they expose, so to a player who is not on that list they are capabilities
# that do nothing.
PLAYER_IRRELEVANT = {
    # The loader and the plumbing.
    'denikson-BepInExPack_Valheim': 'The BepInEx loader itself; ships the mod entry point and no gameplay content.',
    'mvp-Serverside_Simulations': 'Moves world and monster simulation onto the dedicated server; no client-side surface.',
    # Compatibility shims. Each one exists to make a mod that IS on the list work; the feature a
    # player sees belongs to that mod, and listing the shim beside it describes the same thing twice.
    'DragonMotion-VHModpackFix': 'Compatibility shim that patches other listed mods; adds no feature of its own.',
    'geekstreet-BackpacksVRFix': 'Compatibility patch so Backpacks works under VR; the feature it fixes is Backpacks, already listed.',
    'geekstreet-EpicLootVRFix': 'Compatibility patch so EpicLoot works under VR; the feature it fixes is EpicLoot, already listed.',
    'geekstreet-CLLCVRFix': 'Compatibility patch so CreatureLevelAndLootControl works under VR; the feature it fixes is CLLC, already listed.',
    # Admin tools that the union keeps, because they are installed in both player editions. The
    # operator's own classification: he restored these to his player edition because he plays as an
    # admin, and grouped RuinsMaker with the other three - a survey of the packages alone read
    # RuinsMaker as player-facing, and the operator's grouping overrides that reading.
    'JereKuusela-Server_devcommands': 'Console commands gated by the server adminlist; a player who is not an admin cannot run any of them.',
    'JereKuusela-World_Edit_Commands': 'World editing through admin console commands; gated by the same adminlist.',
    'JereKuusela-Infinity_Hammer': 'Unrestricted building and spawning through the admin console; gated by the same adminlist.',
    'Neobotics-RuinsMaker': 'Terrain and ruin generation for building the world, not for playing in it; grouped with the admin tools by the operator.',
    # CookieMilk-YouAreBeingWatched is deliberately NOT here. It can spectate a player and inspect
    # their inventory, and hiding a surveillance capability from the people subject to it is a worse
    # failure than a confusing list entry. Uncommenting the line below is the whole change if that
    # judgement is ever reversed.
    # 'CookieMilk-YouAreBeingWatched': '<reason required before this is switched on>',
}

def load(path): return json.loads(path.read_text(encoding='utf-8-sig'))
def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(data, indent=2) + '\n').encode()
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        os.fchmod(fd, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        with os.fdopen(fd, 'wb') as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise
def all_packages(m): return [*m.get('packages', []), *m.get('client_only_packages', [])]
def disabled_packages(m): return m.get('disabled_packages', [])
def custom_packages(m): return m.get('custom_packages', [])

def package_summary(package):
    current = latest(package)
    return {
        'identifier': package['full_name'],
        'name': package['name'],
        'owner': package['owner'],
        'description': current.get('description', '')[:1200],
        'version': current['version_number'],
        'versions': [item['version_number'] for item in sorted(package['versions'], key=lambda item: Version(item['version_number']), reverse=True)[:20]],
        'dependencies': current.get('dependencies', [])[:100],
        'categories': package.get('categories', []),
        'icon': current.get('icon', ''),
        'website': current.get('website_url') or package.get('package_url', ''),
        'downloads': current.get('downloads', 0),
        'rating': package.get('rating_score', 0),
        'deprecated': bool(package.get('is_deprecated')),
    }

def package_plugin_name(registry, identifier):
    package = registry.get(identifier)
    if not package:
        raise RuntimeError(f'Unknown package: {identifier}')
    return package['name']

def remove_package_files(root, plugin_name):
    for side in ('client', 'server'):
        shutil.rmtree(cache(root) / side / 'BepInEx' / 'plugins' / plugin_name, ignore_errors=True)


MANIFEST_PACKAGE_KEYS = ('packages', 'client_only_packages', 'disabled_packages', 'excluded_packages', 'manual_server_packages')

def package_install_name(identifier):
    _, separator, name = identifier.partition('-')
    if not separator or not name:
        raise RuntimeError(f'Invalid package identifier: {identifier}')
    return name

def matching_manifest_entries(manifest, identifier):
    matches = []
    for key in MANIFEST_PACKAGE_KEYS:
        matches.extend((key, item) for item in manifest.get(key, []) if package_identifier(item) == identifier)
    matches.extend(('custom_packages', item) for item in custom_packages(manifest)
                   if (item.get('id') if isinstance(item, dict) else item) == identifier)
    return matches

def plugin_config_files(world_root, identifier):
    config_root = world_root / 'config_merged' / 'bepinex'
    marker = f'Plugin GUID: {identifier}'
    matches = []
    for config in config_root.rglob('*.cfg') if config_root.is_dir() else []:
        if config.is_file() and not config.is_symlink() and marker in config.read_text(errors='replace'):
            matches.append(config)
    return matches

def package_paths(root, world_root, identifier):
    plugin_name = package_install_name(identifier)
    paths = [
        *(cache(root) / side / 'BepInEx' / 'plugins' / plugin_name for side in ('client', 'server')),
        root / 'manual-mods' / plugin_name,
        world_root / 'config_merged' / 'bepinex' / 'plugins' / plugin_name,
        world_root / 'data' / 'bepinex' / 'BepInEx' / 'plugins' / plugin_name,
    ]
    return [path for path in paths if path.exists() and not path.is_symlink()]

def backup_removal_inputs(root, world_root, manifest_path, paths, configs):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = world_root / 'mods' / 'removal-backups' / root.name / stamp
    backup.mkdir(parents=True)
    shutil.copy2(manifest_path, backup / 'profile-manifest.json')
    for index, path in enumerate(paths):
        destination = backup / 'paths' / f'{index}-{path.name}'
        destination.parent.mkdir(parents=True, exist_ok=True)
        if path.is_dir():
            shutil.copytree(path, destination)
        else:
            shutil.copy2(path, destination)
    for config in configs:
        destination = backup / 'configs' / config.relative_to(world_root / 'config_merged' / 'bepinex')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config, destination)
    return backup

def remove_paths(paths):
    for path in paths:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

def assert_package_purged(root, world_root, manifest, identifier):
    matches = matching_manifest_entries(manifest, identifier)
    if matches:
        raise RuntimeError(f'Package remains in manifest: {identifier}')
    paths = package_paths(root, world_root, identifier)
    if paths:
        raise RuntimeError(f'Package files remain: {", ".join(str(path) for path in paths)}')
    configs = plugin_config_files(world_root, identifier)
    if configs:
        raise RuntimeError(f'Package configs remain: {", ".join(str(config) for config in configs)}')

def client_release_targets(root, world_root, manifest):
    targets_path = portal_paths.REPO_ROOT / 'release-targets.json'
    if not targets_path.is_file():
        # Returning [] here silently disables the whole client-release cutover guard:
        # `remove` records no pending targets, so start_valheim_server.sh's
        # --require-complete check passes and a server runs without a package every
        # player's installed client still expects. The catalog is untracked operator
        # data, so a fresh clone hits this by default - it has to be loud.
        raise RuntimeError(
            f'Release target catalog is missing: {targets_path}. '
            f'Copy {targets_path.name}.example beside it and set the published '
            'profile for each world, otherwise the client-release cutover guard '
            'cannot tell which profiles a package removal would invalidate.'
        )
    mapping = load(targets_path)
    if mapping.get('schema') != 1:
        raise RuntimeError(f'Unsupported release target schema: {targets_path}')
    targets = []
    for client_type in ('flat', 'vr'):
        entries = mapping.get(client_type, [])
        if not isinstance(entries, list):
            raise RuntimeError(f'Invalid {client_type} release targets: {targets_path}')
        for entry in entries:
            if not isinstance(entry, dict):
                raise RuntimeError(f'Invalid {client_type} release target: {targets_path}')
            if entry.get('world') == world_root.name and entry.get('source_profile') == root.name:
                profile = entry.get('published_profile')
                if not isinstance(profile, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', profile):
                    raise RuntimeError(f'Invalid published profile in {targets_path}')
                targets.append({'profile': profile, 'client_type': client_type})
    return targets

def release_cutover_path(world_root):
    return world_root / 'mods' / '.client-release-cutover.json'

def load_release_cutover(world_root):
    path = release_cutover_path(world_root)
    return path, load(path) if path.is_file() else None

def pending_release_targets(cutover):
    targets = {}
    for removal in cutover.get('removals', []):
        identifier = removal.get('identifier')
        for target in removal.get('targets', []):
            profile = target.get('profile')
            client_type = target.get('client_type')
            if isinstance(identifier, str) and isinstance(profile, str) and client_type in ('flat', 'vr'):
                targets.setdefault((profile, client_type), set()).add(identifier)
    return targets

def record_release_cutover(root, world_root, manifest, identifier):
    targets = client_release_targets(root, world_root, manifest)
    if not targets:
        return None
    path, cutover = load_release_cutover(world_root)
    if cutover is None:
        cutover = {
            'schema_version': 1,
            'world_name': world_root.name,
            'removals': [],
            'confirmations': {},
        }
    if cutover.get('schema_version') != 1 or cutover.get('world_name') != world_root.name:
        raise RuntimeError(f'Invalid client release cutover record: {path}')
    cutover['removals'].append({
        'identifier': identifier,
        'source_profile': root.name,
        'targets': targets,
        'recorded_at': datetime.now(timezone.utc).isoformat(),
    })
    for target in targets:
        cutover['confirmations'].pop(f"{target['profile']}/{target['client_type']}", None)
    save(path, cutover)
    return path

def require_release_cutover_complete(world_root):
    path, cutover = load_release_cutover(world_root)
    if not cutover:
        return
    pending = pending_release_targets(cutover)
    confirmations = cutover.get('confirmations', {})
    outstanding = [
        f'{profile}/{client_type} ({",".join(sorted(identifiers))})'
        for (profile, client_type), identifiers in sorted(pending.items())
        if f'{profile}/{client_type}' not in confirmations
    ]
    if outstanding:
        raise RuntimeError(
            'Client release cutover is incomplete; publish and confirm: ' +
            '; '.join(outstanding)
        )

def package_identifier(item):
    # A manifest list may hold either a package OBJECT or a bare identifier STRING, and a
    # real profile carries both in the SAME list: an exclusion needs only a name, while a
    # selection also carries its version and hashes. This is the one place either shape is
    # read, because `'identifier' in item` is a substring test on a string and the `.get`
    # below then raises AttributeError -- which is exactly how `remove` came to crash on
    # every profile whose excluded_packages mixed the two.
    if isinstance(item, str):
        return item.strip() or None
    if not isinstance(item, dict):
        return None
    if 'identifier' in item:
        return item['identifier']
    namespace = item.get('namespace')
    name = item.get('name')
    return f'{namespace}-{name}' if isinstance(namespace, str) and isinstance(name, str) else None

def archive_manifest(path):
    if not path.is_file():
        raise RuntimeError(f'Profile archive not found: {path}')
    try:
        with zipfile.ZipFile(path) as archive:
            return json.loads(archive.read('profile-manifest.json'))
    except (KeyError, zipfile.BadZipFile, json.JSONDecodeError) as error:
        raise RuntimeError(f'Invalid profile archive: {path}') from error

def cmd_release_status(root, manifest, args):
    path, cutover = load_release_cutover(args.world_dir)
    if not cutover:
        print('client_release_cutover=complete')
        return
    require_release_cutover_complete(args.world_dir) if args.require_complete else None
    for (profile, client_type), identifiers in sorted(pending_release_targets(cutover).items()):
        print(f'pending={profile}/{client_type} identifiers={",".join(sorted(identifiers))}')
    print(f'cutover_record={path}')

def cmd_release_confirm(root, manifest, args):
    world_root = args.world_dir
    path, cutover = load_release_cutover(world_root)
    if not cutover:
        raise RuntimeError('No client release cutover is pending')
    target = (args.profile_name, args.client_type)
    pending = pending_release_targets(cutover)
    identifiers = pending.get(target)
    if not identifiers:
        raise RuntimeError(f'No pending release target: {args.profile_name}/{args.client_type}')
    release = archive_manifest(args.archive.resolve())
    if release.get('world') != world_root.name or release.get('profile') != args.profile_name or release.get('client_type') != args.client_type:
        raise RuntimeError('Profile archive does not match the pending release target')
    archive_identifiers = {
        identifier for item in release.get('packages', []) + release.get('client_only_packages', [])
        if (identifier := package_identifier(item))
    }
    present = sorted(identifiers & archive_identifiers)
    if present:
        raise RuntimeError(f'Profile archive still selects: {",".join(present)}')
    confirmations = cutover.setdefault('confirmations', {})
    confirmations[f'{args.profile_name}/{args.client_type}'] = {
        'release_id': args.release_id,
        'archive': str(args.archive.resolve()),
        'confirmed_at': datetime.now(timezone.utc).isoformat(),
    }
    save(path, cutover)
    outstanding = [
        f'{profile}/{client_type}'
        for profile, client_type in pending
        if f'{profile}/{client_type}' not in confirmations
    ]
    if outstanding:
        print(f'client_release_confirmed={args.profile_name}/{args.client_type}')
        print(f'client_release_cutover_pending={",".join(sorted(outstanding))}')
        return
    path.unlink()
    print(f'client_release_confirmed={args.profile_name}/{args.client_type}')
def custom_root(root):
    # Inside the profile, not the world. A profile is shared by several servers, so an
    # archive its custom_packages names cannot live in one server's directory: the same
    # entry would resolve on one world and be missing on the next.
    return root / 'custom'

def valid_custom_id(value):
    path = PurePosixPath(value)
    return bool(value) and len(value) <= 240 and not path.is_absolute() and '..' not in path.parts and value.lower().endswith('.zip')

def custom_archive(root, identifier):
    if not valid_custom_id(identifier):
        raise RuntimeError('Invalid custom package identifier')
    base = custom_root(root).resolve()
    archive = (base / Path(*PurePosixPath(identifier).parts)).resolve()
    if base not in archive.parents or not archive.is_file() or archive.is_symlink():
        raise RuntimeError(f'Custom package not found: {identifier}')
    return archive

def archive_metadata(root, archive):
    digest = hashlib.sha256()
    with archive.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    description = ''
    dlls = []
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            normalized = member.filename.replace('\\', '/')
            if normalized.lower().endswith('.dll') and len(dlls) < 20:
                dlls.append(normalized)
            if not description and normalized.lower() in ('readme.md', 'readme.txt', 'install.txt') and member.file_size <= 64 * 1024:
                description = package.read(member)[:4096].decode('utf-8', errors='replace').strip()
    return {
        'id': archive.relative_to(custom_root(root).resolve()).as_posix(),
        'filename': archive.name,
        'size': archive.stat().st_size,
        'sha256': digest.hexdigest(),
        'description': description,
        'dlls': dlls,
    }

def extract_custom(root, entry):
    archive = custom_archive(root, entry['id'])
    metadata = archive_metadata(root, archive)
    if metadata['sha256'] != entry['sha256'] or metadata['size'] != entry['size']:
        raise RuntimeError(f'Custom package changed since selection: {entry["id"]}')
    key = re.sub(r'[^A-Za-z0-9._-]+', '-', Path(entry['id']).stem).strip('-')[:100]
    if not key:
        raise RuntimeError('Custom package has no safe installation name')
    sides = ('client', 'server') if entry.get('scope') == 'shared' else ('client',)
    found_plugin = False
    with zipfile.ZipFile(archive) as package:
        members = []
        total = 0
        for member in package.infolist():
            name = member.filename.replace('\\', '/')
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or member.external_attr >> 16 & 0o170000 == 0o120000:
                raise RuntimeError(f'Unsafe custom package path: {name}')
            if member.file_size > 512 * 1024 * 1024:
                raise RuntimeError('Custom package entry is too large')
            total += member.file_size
            if total > 1024 * 1024 * 1024:
                raise RuntimeError('Custom package is too large')
            lower = name.lower()
            marker = 'bepinex/plugins/'
            position = lower.find(marker)
            if position < 0 or member.is_dir():
                continue
            relative = PurePosixPath(name[position + len(marker):])
            if not relative.parts or '..' in relative.parts:
                continue
            found_plugin = found_plugin or relative.suffix.lower() == '.dll'
            members.append((member, relative))
        if not found_plugin:
            raise RuntimeError('Custom package contains no BepInEx plugin DLL')
        for side in sides:
            destination_root = cache(root) / side / 'BepInEx' / 'plugins' / key
            shutil.rmtree(destination_root, ignore_errors=True)
            for member, relative in members:
                destination = destination_root.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with package.open(member) as source, destination.open('wb') as output:
                    shutil.copyfileobj(source, output)
    entry['install_name'] = key

def remove_custom_files(root, entry):
    key = entry.get('install_name')
    if not key:
        return
    for side in ('client', 'server'):
        shutil.rmtree(cache(root) / side / 'BepInEx' / 'plugins' / key, ignore_errors=True)
def index():
    r = requests.get(API, timeout=60); r.raise_for_status()
    return {p['full_name']: p for p in r.json()}
def latest(p): return max(p['versions'], key=lambda v: Version(v['version_number']))
def version(p, ver):
    return next((v for v in p['versions'] if v['version_number'] == ver), None)
def require_stopped(world):
    result = subprocess.run(['docker','inspect','-f','{{.State.Running}}',f'valheim-server-{world}'], capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip() == 'true': raise RuntimeError(f'{world} is running; stop it before deploy --apply')
def cache(root): return root / 'manager-cache'
def archive_path(root, p, ver): return cache(root) / 'packages' / f"{p['name']}-{ver}.zip"
def install(root, p, ver, side):
    archive = archive_path(root, p, ver); archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.is_file():
        v = version(p, ver)
        if not v: raise RuntimeError(f'{p["full_name"]} has no version {ver}')
        r = requests.get(v['download_url'], headers={'User-Agent':'r2modman/3.1.57'}, timeout=120)
        r.raise_for_status()
        archive.write_bytes(r.content)
    extract_package(archive, cache(root) / side / 'BepInEx' / 'plugins' / p['name'], p['name'])

def extract_package(archive, target, name):
    """Unpack one Thunderstore archive into `target`, flattening its plugin prefix.

    Split out of install() so the same unpacking serves a destination that is not the
    profile's own cache: the per-world admin-mode overlay below needs exactly this and
    must not get a second, subtly different copy of the path-safety checks.
    """
    if not zipfile.is_zipfile(archive): raise RuntimeError(f'Invalid package archive: {archive}')
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    with zipfile.ZipFile(archive) as z:
        total = 0
        for member in z.infolist():
            raw_name = member.filename.replace('\\','/')
            entry_name = raw_name
            for prefix in (f'BepInEx/plugins/{name}/', 'BepInEx/plugins/', f'plugins/{name}/', 'plugins/', f'{name}/'):
                if entry_name.startswith(prefix):
                    entry_name = entry_name[len(prefix):]
                    break
            path = PurePosixPath(entry_name)
            if not entry_name or raw_name.endswith('/'):
                continue
            if path.is_absolute() or '..' in path.parts or member.external_attr >> 16 & 0o170000 == 0o120000:
                raise RuntimeError(f'Unsafe package archive path: {raw_name}')
            if member.file_size > 512 * 1024 * 1024:
                raise RuntimeError(f'Package entry is too large: {raw_name}')
            total += member.file_size
            if total > 1024 * 1024 * 1024:
                raise RuntimeError(f'Package archive is too large: {archive}')
            destination = target.joinpath(*path.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with z.open(member) as src, destination.open('wb') as dst:
                shutil.copyfileobj(src, dst)

# The two mods that disconnect every connected player when they are loaded server-side.
# They were removed from all four servers on 2026-08-20 for exactly that, which also
# removed the admin capability they provide. They come back only for a maintenance
# window, armed on one named world at a time.
ADMIN_MODE_PACKAGES = ('Azumatt-PerfectPlacement', 'JereKuusela-Structure_Tweaks')

# The overlay is per world, and it has to be: measured 2026-08-25, all four worlds
# (Hrafnheim, Doggerland, Storgard, Vangard) link to the same `admin` profile, and
# cmd_deploy's server-side sources - manager-cache/server and manual-mods - are both
# per profile. Anything expressed in the profile manifest therefore arms the whole
# fleet on the next deploy of each world, which is the fleet-wide switch the operator
# rejected. A directory under the world's own mods/ is the only per-world lever.
ADMIN_MODE_DIR = 'admin-mode'

def admin_mode_overlay(world_root):
    return world_root / 'mods' / ADMIN_MODE_DIR

def admin_mode_armed(world_root):
    """The plugin directories this world's overlay would add, newest read each call.

    The filesystem is the authority for what a deploy will copy. The portal keeps its
    own durable record of the operator's decision; this answers the different question
    of what is actually staged, so the two can be compared instead of assumed equal.
    """
    overlay = admin_mode_overlay(world_root)
    if not overlay.is_dir():
        return []
    return sorted(entry.name for entry in overlay.iterdir() if entry.is_dir())
def selected_versions(manifest):
    selected = {}
    for item in all_packages(manifest):
        identifier = item['identifier']
        version_number = item['version']
        existing = selected.get(identifier)
        if existing and existing != version_number:
            raise RuntimeError(f'Profile selects {identifier} at both {existing} and {version_number}')
        selected[identifier] = version_number
    return selected

def version_tuple(value):
    parts = []
    for chunk in str(value).split('.'):
        digits = ''.join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)

def resolve_dependencies(registry, package, version_number, selected, seen, ordered):
    identifier = package['full_name']
    existing = selected.get(identifier)
    if existing and existing != version_number:
        # Thunderstore dependency strings are minimums, not exact pins. A profile
        # that already selects a NEWER build satisfies the requirement; only an
        # older selection is a genuine conflict. Without this, every package whose
        # declared BepInExPack version predates the profile's pin is unaddable.
        if version_tuple(existing) >= version_tuple(version_number):
            return
        raise RuntimeError(
            f'Dependency conflict: {identifier} {version_number} is required, '
            f'but the active profile selects the older {existing}'
        )
    if identifier in seen:
        return
    selected[identifier] = version_number
    seen.add(identifier)
    package_version = version(package, version_number)
    if not package_version:
        raise RuntimeError(f'{identifier} has no version {version_number}')
    for dependency in package_version['dependencies']:
        dependency_name, dependency_version = dependency.rsplit('-', 1)
        resolve_dependencies(registry, registry[dependency_name], dependency_version, selected, seen, ordered)
    ordered.append((package, version_number))

def ensure_dependencies(root, registry, package, version_number, scope, selected=None):
    selected = dict(selected or {})
    ordered = []
    resolve_dependencies(registry, package, version_number, selected, set(), ordered)
    server_required = scope == 'shared' and 'Server-side' in package.get('categories', [])
    for dependency, dependency_version in ordered:
        install(root, dependency, dependency_version, 'client')
        if server_required:
            install(root, dependency, dependency_version, 'server')
    return [
        {'identifier': dependency['full_name'], 'version': dependency_version, 'scope': scope}
        for dependency, dependency_version in ordered
    ]
def cmd_list(root, m, args):
    if getattr(args, 'json', False):
        payload = {
            'world': args.world_dir.name if args.world_dir else '',
            'profile': m['profile_name'],
            'packages': [{**item, 'enabled': True, 'source': 'thunderstore'} for item in all_packages(m)],
            'disabled_packages': [{**item, 'enabled': False, 'source': 'thunderstore'} for item in disabled_packages(m)],
            'custom_packages': [{**item, 'source': 'custom'} for item in custom_packages(m)],
            'excluded_packages': m.get('excluded_packages', []),
        }
        print(json.dumps(payload, separators=(',', ':')))
        return
    for p in all_packages(m):
        print(f"{p.get('scope','client-only'):11} {p['identifier']} {p['version']}")
    for p in disabled_packages(m):
        print(f"{'disabled':11} {p['identifier']} {p['version']}")
    for p in custom_packages(m):
        state = p.get('scope', 'client-only') if p.get('enabled', True) else 'disabled'
        print(f"{state:11} custom:{p['id']}")

def player_package_versions(store):
    """Every identifier the player editions install, mapped to the versions they install.

    The union is taken over `packages` and `client_only_packages` of each edition. Which array an
    entry sits in says nothing here, and `scope` is not read at all: a real manifest carries
    entries with no `scope` key (Azumatt-FastLink in client_only_packages of all three profiles
    today), and admin's `packages` array carries scope "client-only" entries. Only the identifier
    and the version are needed, and those two keys are always present.

    A package both editions install at the same version yields one version; if the editions ever
    disagree, both are kept, so the fingerprint moves when either one moves.
    """
    versions = {}
    for edition in PLAYER_EDITIONS:
        manifest = profile_store.manifest_path(edition, store)
        if not manifest.is_file():
            continue
        for item in all_packages(load(manifest)):
            identifier = package_identifier(item)
            if not identifier or not isinstance(item, dict):
                continue
            versions.setdefault(identifier, set()).add(item.get('version', ''))
    return versions


def catalog_fingerprint(versions):
    """The sorted identifier@version set of the player editions, hashed.

    This is the staleness check, and it is deliberately taken over the INPUT to the derivation
    rather than its output: the output needs Thunderstore's categories, which is the expensive
    part being avoided. Anything that can change the list changes this.
    """
    rows = sorted(f'{identifier}@{version}' for identifier, entries in versions.items() for version in entries)
    return hashlib.sha256('\n'.join(rows).encode()).hexdigest()


def is_library(categories):
    return LIBRARY_CATEGORY in categories and not (CONTENT_CATEGORIES & set(categories))


def local_plugin_metadata(world_root, identifier):
    """What the installed plugin says about itself, when Thunderstore cannot be reached.

    Read with utf-8-sig: 26 of the 100 manifests shipped under this world carry a UTF-8 BOM, and
    strict utf-8 json fails all 26 with "Unexpected UTF-8 BOM". On 2026-08-20 that made a mod
    that was installed read as absent, so a manifest that cannot be read returns nothing at all
    and the caller lists the mod with unknown metadata. It never drops it.
    """
    try:
        manifest = world_root / 'config_merged' / 'bepinex' / 'plugins' / package_install_name(identifier) / 'manifest.json'
        if not manifest.is_file():
            return None
        local = load(manifest)
    except (OSError, ValueError, RuntimeError):
        return None
    if not isinstance(local, dict):
        return None
    return {
        'name': local.get('name') or '',
        'description': local.get('description') or '',
        'url': local.get('website_url') or '',
        'source': 'plugin-manifest',
    }


def player_catalog(world_root, store):
    """The mods a player of this world has, with what to say about each one.

    Three subtractions, in order: everything outside the player editions is gone by set
    difference, then Thunderstore's Libraries category, then PLAYER_IRRELEVANT.

    Metadata comes from Thunderstore, which carries all 108 identifiers in the player union with a
    non-empty description for every one. The installed plugin's own manifest is therefore an
    UNAVAILABILITY path, not a coverage path: it is read when Thunderstore could not be reached at
    rebuild time. `metadata_complete` says which happened, so a page can be honest rather than
    showing a name with no description and implying the mod has none.
    """
    versions = player_package_versions(store)
    fingerprint = catalog_fingerprint(versions)
    try:
        registry = index()
        metadata_complete = True
    except (requests.RequestException, ValueError):
        registry = {}
        metadata_complete = False
    mods = []
    for identifier in sorted(versions, key=str.lower):
        if identifier in PLAYER_IRRELEVANT:
            continue
        installed = sorted(entry for entry in versions[identifier] if entry)
        version_text = ' / '.join(installed)
        package = registry.get(identifier)
        if package:
            categories = package.get('categories', [])
            if is_library(categories):
                continue
            # The description belongs to a version, not to the package, so it is read from the
            # version actually installed. versions[0] is the fallback rather than latest(): a
            # description for a release nobody here runs is the wrong description.
            current = (installed and version(package, installed[0])) or package['versions'][0]
            mods.append({
                'identifier': identifier,
                'name': package.get('name') or identifier,
                'version': version_text,
                'description': current.get('description', '')[:1200],
                'categories': [category for category in categories if category != LIBRARY_CATEGORY],
                'url': package.get('package_url', ''),
                'source': 'thunderstore',
            })
            continue
        # No Thunderstore record. A library cannot be recognised without categories, so a mod that
        # only the local manifest describes is listed: over-listing is recoverable, and silently
        # dropping an installed mod because a network call failed is what must not happen.
        local = local_plugin_metadata(world_root, identifier) or {'name': '', 'description': '', 'url': '', 'source': 'unknown'}
        mods.append({
            'identifier': identifier,
            'name': local['name'] or identifier,
            'version': version_text,
            'description': local['description'],
            'categories': [],
            'url': local['url'],
            'source': local['source'],
        })
    return {
        'world': world_root.name,
        'fingerprint': fingerprint,
        'metadata_complete': metadata_complete,
        'editions': list(PLAYER_EDITIONS),
        'installed': sum(len(entries) for entries in versions.values()),
        'mods': mods,
    }


def cmd_player_catalog(_root, _manifest, args):
    store = profile_store.profiles_root(portal_paths.world_root())
    if args.state:
        # The cheap half: two manifest reads and a hash, no network. The portal calls this on
        # every world page view, because mods also change through this script on the host, where
        # the portal never sees the mutation and pure event invalidation would serve a stale list.
        print(json.dumps({'fingerprint': catalog_fingerprint(player_package_versions(store))}, separators=(',', ':')))
        return 0
    print(json.dumps(player_catalog(args.world_dir, store), separators=(',', ':')))
    return 0

def cmd_check(root, m, _):
    reg=index(); updates=[]
    for item in all_packages(m):
        remote=latest(reg[item['identifier']])['version_number']
        if Version(remote)>Version(item['version']): updates.append((item['identifier'], item['version'], remote))
    for row in updates: print(f'{row[0]} {row[1]} -> {row[2]}')
    print(f'updates={len(updates)}'); return 1 if updates else 0
# What changed, before deciding to take it.
#
# check-updates answers "is there a newer version" and nothing else, so every upgrade tonight was
# taken blind - including a skills mod jumping three minor versions, which is exactly the kind of
# change that rewrites character data. Thunderstore serves a per-version changelog when the package
# ships CHANGELOG.md, and most authors keep the real detail in a GitHub release; neither is any use
# unless something collects them for the versions actually being crossed.
THUNDERSTORE_PACKAGE = 'https://thunderstore.io/api/experimental/package/{namespace}/{name}/'
THUNDERSTORE_CHANGELOG = 'https://thunderstore.io/api/experimental/package/{namespace}/{name}/{version}/changelog/'
GITHUB_RELEASES = 'https://api.github.com/repos/{owner}/{repo}/releases?per_page=100'


def crossed_versions(package, installed, remote):
    """Every published version above the installed one, up to and including the new one.

    A jump from 0.9.5 to 0.12.0 crosses whatever was released between them, and the note that
    matters is often in one of those, not in the newest.
    """
    versions = []
    for entry in package['versions']:
        number = entry['version_number']
        if Version(installed) < Version(number) <= Version(remote):
            versions.append(number)
    return sorted(versions, key=Version)


def thunderstore_changelog(namespace, name, version_number):
    try:
        response = requests.get(THUNDERSTORE_CHANGELOG.format(namespace=namespace, name=name, version=version_number), timeout=30)
        if response.status_code != 200:
            return None
        return (response.json() or {}).get('markdown') or None
    except Exception:
        return None


def github_repository(package):
    """The owner/repo a package points at, or None. Only github.com, and only a plain repo path."""
    for field in ('website_url', 'package_url'):
        url = (package.get(field) or '').strip()
        if 'github.com/' not in url:
            continue
        tail = url.split('github.com/', 1)[1].strip('/')
        parts = [segment for segment in tail.split('/') if segment]
        if len(parts) >= 2:
            return parts[0], parts[1].removesuffix('.git')
    return None


def github_release_notes(owner, repo, wanted):
    """Release bodies whose tag mentions one of the versions being crossed.

    Tags are matched loosely - v1.2.3, 1.2.3, release-1.2.3 are all the same release to an author -
    because a strict match returns nothing for most repositories.
    """
    try:
        response = requests.get(GITHUB_RELEASES.format(owner=owner, repo=repo), timeout=30,
                                headers={'Accept': 'application/vnd.github+json'})
        if response.status_code != 200:
            return {}
        found = {}
        for release in response.json() or []:
            tag = (release.get('tag_name') or '').lstrip('vV')
            for version_number in wanted:
                if tag == version_number or tag.endswith(version_number):
                    body = (release.get('body') or '').strip()
                    if body:
                        found[version_number] = body
        return found
    except Exception:
        return {}


def cmd_notes(root, m, args):
    registry = index()
    pending = []
    for item in all_packages(m):
        package = registry.get(item['identifier'])
        if not package:
            continue
        remote = latest(package)['version_number']
        if Version(remote) > Version(item['version']):
            pending.append((item['identifier'], item['version'], remote, package))

    if not pending:
        print('updates=0')
        return 0

    for identifier, installed, remote, package in pending:
        namespace, _, name = identifier.partition('-')
        versions = crossed_versions(package, installed, remote)
        print(f'=== {identifier} {installed} -> {remote} ({len(versions)} version(s) crossed)')
        repo = github_repository(package)
        releases = github_release_notes(repo[0], repo[1], versions) if repo else {}
        if repo:
            print(f'    github {repo[0]}/{repo[1]}')
        printed = False
        # CHANGELOG.md is cumulative, so the newest version's copy already contains every entry
        # below it. Fetching one per crossed version printed 0.11.2's notes four times.
        changelog = thunderstore_changelog(namespace, name, versions[-1]) if versions else None
        if changelog:
            printed = True
            print(f'  -- changelog through {versions[-1]} [thunderstore]')
            for line in changelog.strip().splitlines()[:args.lines]:
                print('     ' + line.rstrip())
        # GitHub releases are per tag, so each one is its own note and worth listing separately.
        for version_number in reversed(versions):
            body = releases.get(version_number)
            if not body:
                continue
            printed = True
            print(f'  -- {version_number} [github]')
            for line in body.strip().splitlines()[:args.lines]:
                print('     ' + line.rstrip())
        if not printed:
            # Said plainly: an author who publishes no notes and a lookup that failed are different
            # situations, and a silent gap reads as "nothing changed", which it never means.
            print('     no notes published on either source for the versions being crossed')
        print()

    print(f'updates={len(pending)}')
    return 1


def cmd_search(root, m, args):
    query = args.query.lower().strip()
    if len(query) < 2:
        raise RuntimeError('Search query must contain at least two characters')
    terms = query.split()
    matches = []
    for package in index().values():
        current = latest(package)
        haystack = ' '.join([
            package.get('full_name', ''), package.get('owner', ''), package.get('name', ''),
            current.get('description', ''), ' '.join(package.get('categories', [])),
        ]).lower()
        if all(term in haystack for term in terms):
            matches.append(package)
    matches.sort(key=lambda package: (bool(package.get('is_deprecated')), -latest(package).get('downloads', 0), package['full_name'].lower()))
    matches = matches[:50]
    if getattr(args, 'json', False):
        print(json.dumps([package_summary(package) for package in matches], separators=(',', ':')))
    else:
        for package in matches:
            print(f"{package['full_name']} {latest(package)['version_number']}")
    return 0 if getattr(args, 'json', False) else (1 if not matches else 0)
def cmd_add(root,m,args):
    reg=index(); p=reg.get(args.identifier)
    if not p: raise RuntimeError(f'Unknown package: {args.identifier}')
    ver=args.version or latest(p)['version_number']; scope='client-only' if args.client_only else 'shared'
    ids={x['identifier'] for x in all_packages(m)}
    if p['full_name'] in ids: raise RuntimeError(f'Already present: {p["full_name"]}')
    added=ensure_dependencies(root, reg, p, ver, scope, selected_versions(m))
    for item in added:
        if item['identifier'] not in ids:
            m['client_only_packages' if item['scope']=='client-only' else 'packages'].append(item); ids.add(item['identifier'])
    m['excluded_packages']=[item for item in m.get('excluded_packages', []) if package_identifier(item) not in ids]
    save(args.manifest,m); print('added=' + ','.join(x['identifier'] for x in added))
def cmd_sync(root, m, args):
    item=next((item for item in all_packages(m) if item['identifier'] == args.identifier), None)
    if not item:
        raise RuntimeError(f'Not present: {args.identifier}')
    registry=index()
    package=registry[item['identifier']]
    ensure_dependencies(root, registry, package, item['version'], item.get('scope', 'client-only'), selected_versions(m))
    print(f'synced={args.identifier}')
def write_removal_record(path, identifier, reason, started_at, state, cutover=None, failure=None):
    record = {
        'identifier': identifier,
        'reason': reason,
        'started_at': started_at,
        'state': state,
        'recorded_at': datetime.now(timezone.utc).isoformat(),
        'client_release_cutover': str(cutover) if cutover else None,
    }
    if state == 'completed':
        record['removed_at'] = record['recorded_at']
    if failure:
        record['failure'] = failure
    path.write_text(json.dumps(record, indent=2) + '\n')

def cmd_remove(root, m, args):
    matches = matching_manifest_entries(m, args.identifier)
    if not matches:
        raise RuntimeError(f'Not present: {args.identifier}')
    require_stopped(args.world_dir.name)
    paths = package_paths(root, args.world_dir, args.identifier)
    configs = plugin_config_files(args.world_dir, args.identifier)
    backup = backup_removal_inputs(root, args.world_dir, args.manifest, paths, configs)
    # The record is written before anything is mutated and rewritten with the outcome. A failure
    # after the manifest save used to leave the backup with no record at all, and because the
    # manifest no longer selected the package, the next run answered 'Not present' - which reads
    # as "nothing happened" when the manifest had in fact already been rewritten.
    record = backup / 'removal.json'
    started = datetime.now(timezone.utc).isoformat()
    write_removal_record(record, args.identifier, args.reason, started, 'started')
    # Filter through the same shape-tolerant reader the lookup above uses: a package list
    # may hold objects and bare identifier strings side by side, and `.get` on a string
    # raises. Rebuilding these lists is what actually deselects the package, so a crash
    # here left the manifest untouched while the caller had already been told it matched.
    try:
        for key in MANIFEST_PACKAGE_KEYS:
            m[key] = [item for item in m.get(key, []) if package_identifier(item) != args.identifier]
        m['custom_packages'] = [item for item in custom_packages(m)
                                if (item.get('id') if isinstance(item, dict) else item) != args.identifier]
        save(args.manifest, m)
        remove_paths(paths)
        remove_paths(configs)
        assert_package_purged(root, args.world_dir, load(args.manifest), args.identifier)
        cutover = record_release_cutover(root, args.world_dir, m, args.identifier)
    except Exception as failure:
        write_removal_record(record, args.identifier, args.reason, started, 'failed',
                             failure=f'{type(failure).__name__}: {failure}')
        raise RuntimeError(
            f'{failure}\n'
            f'The manifest was already rewritten, so re-running answers "Not present" even though '
            f'work was done. What was reached is recorded in {record} and the originals are in {backup}.'
        ) from failure
    write_removal_record(record, args.identifier, args.reason, started, 'completed', cutover=cutover)
    print(f'source_removed={args.identifier}\nbackup={backup}')
    if cutover:
        print(f'client_release_cutover_required={cutover}')

def cmd_purge(root, m, args):
    if matching_manifest_entries(m, args.identifier):
        raise RuntimeError(f'Package is still selected: {args.identifier}; use remove instead')
    require_stopped(args.world_dir.name)
    paths = package_paths(root, args.world_dir, args.identifier)
    configs = plugin_config_files(args.world_dir, args.identifier)
    if not paths and not configs:
        raise RuntimeError(f'No orphaned package files found: {args.identifier}')
    backup = backup_removal_inputs(root, args.world_dir, args.manifest, paths, configs)
    remove_paths(paths)
    remove_paths(configs)
    assert_package_purged(root, args.world_dir, m, args.identifier)
    (backup / 'removal.json').write_text(json.dumps({
        'identifier': args.identifier,
        'reason': args.reason,
        'operation': 'purge',
        'removed_at': datetime.now(timezone.utc).isoformat(),
    }, indent=2) + '\n')
    print(f'purged={args.identifier}\nbackup={backup}')

def cmd_exclude(root, m, args):
    if any(package_identifier(item) == args.identifier for item in all_packages(m)):
        raise RuntimeError(f'Remove {args.identifier} before excluding it')
    m['excluded_packages'] = [item for item in m.get('excluded_packages', []) if package_identifier(item) != args.identifier]
    m['excluded_packages'].append({'identifier': args.identifier, 'version': args.version, 'reason': args.reason})
    save(args.manifest, m)
    print(f'excluded={args.identifier}')
def cmd_disable(root, m, args):
    found = next((item for item in all_packages(m) if item['identifier'] == args.identifier), None)
    if not found:
        raise RuntimeError(f'Enabled package not found: {args.identifier}')
    registry = index()
    for key in ('packages', 'client_only_packages'):
        m[key] = [item for item in m.get(key, []) if package_identifier(item) != args.identifier]
    m.setdefault('disabled_packages', []).append(found)
    remove_package_files(root, package_plugin_name(registry, args.identifier))
    save(args.manifest, m)
    print('disabled=' + args.identifier)
def cmd_enable(root, m, args):
    found = next((item for item in disabled_packages(m) if item['identifier'] == args.identifier), None)
    if not found:
        raise RuntimeError(f'Disabled package not found: {args.identifier}')
    registry = index()
    package = registry.get(args.identifier)
    if not package:
        raise RuntimeError(f'Unknown package: {args.identifier}')
    added = ensure_dependencies(root, registry, package, found['version'], found.get('scope', 'client-only'), set())
    m['disabled_packages'] = [item for item in disabled_packages(m) if item['identifier'] != args.identifier]
    ids = {item['identifier'] for item in all_packages(m)}
    for item in added:
        if item['identifier'] not in ids:
            m['client_only_packages' if item['scope'] == 'client-only' else 'packages'].append(item)
            ids.add(item['identifier'])
    save(args.manifest, m)
    print('enabled=' + args.identifier)
def cmd_custom_list(root, m, args):
    base = custom_root(root)
    selected = {item['id']: item for item in custom_packages(m)}
    result = []
    for archive in sorted(base.rglob('*.zip') if base.is_dir() else []):
        if archive.is_symlink():
            continue
        try:
            metadata = archive_metadata(root, archive.resolve())
            choice = selected.get(metadata['id'])
            metadata['selected'] = choice is not None
            metadata['enabled'] = choice.get('enabled', True) if choice else False
            metadata['scope'] = choice.get('scope', 'client-only') if choice else 'client-only'
            result.append(metadata)
        except (OSError, zipfile.BadZipFile, RuntimeError):
            continue
    print(json.dumps(result, separators=(',', ':')))
def cmd_custom_add(root, m, args):
    if any(item['id'] == args.identifier for item in custom_packages(m)):
        raise RuntimeError(f'Custom package already selected: {args.identifier}')
    archive = custom_archive(root, args.identifier)
    entry = archive_metadata(root, archive)
    entry.update({'scope': args.scope, 'enabled': True})
    extract_custom(root, entry)
    m.setdefault('custom_packages', []).append(entry)
    save(args.manifest, m)
    print('custom-added=' + args.identifier)
def custom_entry(m, identifier):
    return next((item for item in custom_packages(m) if item['id'] == identifier), None)
def cmd_custom_remove(root, m, args):
    entry = custom_entry(m, args.identifier)
    if not entry:
        raise RuntimeError(f'Custom package not selected: {args.identifier}')
    remove_custom_files(root, entry)
    m['custom_packages'] = [item for item in custom_packages(m) if item['id'] != args.identifier]
    save(args.manifest, m)
    print('custom-removed=' + args.identifier)
def cmd_custom_disable(root, m, args):
    entry = custom_entry(m, args.identifier)
    if not entry or not entry.get('enabled', True):
        raise RuntimeError(f'Enabled custom package not found: {args.identifier}')
    remove_custom_files(root, entry)
    entry['enabled'] = False
    save(args.manifest, m)
    print('custom-disabled=' + args.identifier)
def cmd_custom_enable(root, m, args):
    entry = custom_entry(m, args.identifier)
    if not entry or entry.get('enabled', True):
        raise RuntimeError(f'Disabled custom package not found: {args.identifier}')
    extract_custom(root, entry)
    entry['enabled'] = True
    save(args.manifest, m)
    print('custom-enabled=' + args.identifier)
def cmd_update(root,m,args):
    reg=index(); selected=[x for x in all_packages(m) if args.all or x['identifier']==args.identifier]
    if not selected: raise RuntimeError('No selected packages')
    changes=[]
    for item in selected:
        new=latest(reg[item['identifier']])['version_number']
        if Version(new)>Version(item['version']): changes.append((item,new))
    if not args.apply:
        for item,new in changes: print(f"{item['identifier']} {item['version']} -> {new}")
        print(f'updates={len(changes)}; rerun with --apply to record them'); return
    for item,new in changes:
        item['version']=new; p=reg[item['identifier']]; install(root,p,new,'client')
        if item.get('scope')=='shared' and 'Server-side' in p.get('categories',[]): install(root,p,new,'server')
    save(args.manifest,m); print(f'updated={len(changes)}')
def cmd_export(root,m,args):
    script=root/'export_profile_code.py'
    if not script.is_file():
        raise RuntimeError(f'Profile code exporter is not bundled with this checkout; expected {script}')
    subprocess.run([sys.executable,str(script)],check=True)
def validate_server_cache(root, manifest):
    server_plugins = cache(root) / 'server' / 'BepInEx' / 'plugins'
    for item in all_packages(manifest):
        plugin = server_plugins / package_install_name(item['identifier'])
        if not plugin.is_dir():
            continue
        metadata_path = plugin / 'manifest.json'
        if not metadata_path.is_file():
            raise RuntimeError(f'Cached server package has no manifest: {plugin}')
        metadata = load(metadata_path)
        expected_name = package_install_name(item['identifier'])
        if metadata.get('name') != expected_name or metadata.get('version_number') != item['version']:
            raise RuntimeError(
                f'Cached server package does not match profile manifest: {item["identifier"]} '
                f'expected {item["version"]}, found {metadata.get("name")} {metadata.get("version_number")}'
            )

SERVER_CONFIG_DIR = 'server-config'
OVERRIDE_DIR = 'overrides'

def server_config_source(root):
    """The profile's canonical server settings, or None when it declares none.

    A profile that has never had them stays as it was: the plugins write their own
    defaults on first run, which is how every world worked before. Declaring them makes
    the profile the source of truth for every linked server instead.
    """
    source = root / SERVER_CONFIG_DIR
    return source if source.is_dir() and any(source.rglob('*.cfg')) else None

def deploy_server_config(root, world_root):
    """Place the profile's server settings, with this server's overrides applied.

    The override is per key, not per file: a server that needed one value used to copy
    the whole config, and the copy then kept yesterday's defaults for every other
    setting when the profile moved on. That is the same drift that left four worlds
    running four different mod sets.

    The previous configs are kept beside the deployment backup rather than replaced in
    place, because a plugin's own file is the only record of what a server was running
    before the profile claimed ownership.
    """
    source = server_config_source(root)
    if source is None:
        return
    target = world_root / 'config_merged' / 'bepinex'
    overrides = world_root / 'mods' / OVERRIDE_DIR / 'server'
    backup = world_root / 'mods' / 'deployment-backups' / root.name / 'server-config.previous'
    with tempfile.TemporaryDirectory(dir=target.parent) as temp:
        staged = Path(temp) / 'config'
        touched = config_merge.merge_tree(source, overrides, staged)
        shutil.rmtree(backup, ignore_errors=True)
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.mkdir()
        for entry in sorted(staged.rglob('*')):
            if not entry.is_file():
                continue
            relative = entry.relative_to(staged)
            live = target / relative
            if live.is_file():
                (backup / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(live, backup / relative)
            live.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, live)
    print(f'server_config={len(list(source.rglob("*.cfg")))} files')
    for entry in touched:
        print(f'server_config_override={entry}')

def cmd_deploy(root,m,args):
    world=args.world_dir.name
    server_plugins=cache(root)/'server'/'BepInEx'/'plugins'
    manual_plugins=root/'manual-mods'
    world_root=args.world_dir
    target=world_root/'config_merged'/'bepinex'/'plugins'
    runtime_plugins=world_root/'data'/'bepinex'/'BepInEx'/'plugins'
    print(f'source={server_plugins}\ntarget={target}\nmanual={manual_plugins}')
    if not args.apply:
        print('plan only; rerun with --apply after stopping server')
        return
    require_stopped(world)
    if not server_plugins.is_dir() or not manual_plugins.is_dir() or not target.parent.is_dir():
        raise RuntimeError('Profile deployment directories are incomplete')
    require_release_cutover_complete(world_root)
    validate_server_cache(root, m)
    with tempfile.TemporaryDirectory(dir=target.parent) as temp:
        staged=Path(temp)/'plugins'
        shutil.copytree(server_plugins,staged)
        for manual in manual_plugins.iterdir():
            if manual.is_dir() and not manual.is_symlink():
                shutil.copytree(manual,staged/manual.name,dirs_exist_ok=True)
            elif manual.is_file() and not manual.is_symlink():
                shutil.copy2(manual,staged/manual.name)
        # This world's own additions, layered last for the same reason manual-mods is layered
        # after the profile cache: it is a per-world addition to a shared definition. Empty or
        # absent for every world that is not in an admin-mode maintenance window, and a world
        # whose overlay is non-empty kicks every player who joins it.
        overlay = admin_mode_overlay(world_root)
        if overlay.is_dir():
            for entry in overlay.iterdir():
                if entry.is_dir() and not entry.is_symlink():
                    shutil.copytree(entry,staged/entry.name,dirs_exist_ok=True)
                elif entry.is_file() and not entry.is_symlink():
                    shutil.copy2(entry,staged/entry.name)
        backup_root=world_root/'mods'/'deployment-backups'/root.name
        backup=backup_root/'server-plugins.previous'
        legacy_backup=target.with_name('plugins.previous')
        legacy_archive=backup_root/'legacy-plugins.previous'
        backup_root.mkdir(parents=True,exist_ok=True)
        if legacy_backup.exists():
            shutil.rmtree(legacy_archive,ignore_errors=True)
            legacy_backup.rename(legacy_archive)
        shutil.rmtree(backup,ignore_errors=True)
        if target.exists():
            target.rename(backup)
        try:
            staged.rename(target)
        except BaseException:
            if backup.exists() and not target.exists():
                backup.rename(target)
            raise
    if runtime_plugins.is_dir():
        for entry in runtime_plugins.iterdir():
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
    deploy_server_config(root, world_root)
    print('deployed=true')

def cmd_admin_mode(root, m, args):
    """Arm or disarm this world's admin-mode plugin overlay.

    Staging only. Nothing reaches the running server until a deploy copies the overlay,
    which is why the caller's ordering is arm-then-deploy-then-start and never arm alone.

    The archives come from the profile's own package store, so this reads no network: a
    maintenance window must not be able to fail because Thunderstore is down.
    """
    world_root = args.world_dir
    overlay = admin_mode_overlay(world_root)
    if args.admin_mode_command == 'state':
        for name in admin_mode_armed(world_root):
            print(f'armed={name}')
        print('admin_mode=' + ('on' if admin_mode_armed(world_root) else 'off'))
        return
    require_stopped(world_root.name)
    if args.admin_mode_command == 'off':
        # Disarming is the recovery path - it is what gets a world back to a state players
        # can join - so it refuses nothing. An absent overlay is the wanted end state, not
        # an error, which also makes turning off a world that was never on a no-op.
        shutil.rmtree(overlay, ignore_errors=True)
        print('admin_mode=off')
        return
    selected = selected_versions(m)
    staged = {}
    for identifier in ADMIN_MODE_PACKAGES:
        version_number = selected.get(identifier)
        if not version_number:
            raise RuntimeError(
                f'{identifier} is not selected by profile {root.name}; admin mode arms the '
                f'version the profile already pins and will not invent one'
            )
        name = package_install_name(identifier)
        archive = cache(root) / 'packages' / f'{name}-{version_number}.zip'
        if not archive.is_file():
            raise RuntimeError(f'No archive for {identifier} {version_number} at {archive}')
        staged[name] = archive
    # Built whole, then moved into place: a half-populated overlay would deploy one of the
    # two mods, which is a state no operator asked for and none would recognise.
    overlay.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=overlay.parent) as temp:
        build = Path(temp) / ADMIN_MODE_DIR
        build.mkdir()
        for name, archive in staged.items():
            extract_package(archive, build / name, name)
        shutil.rmtree(overlay, ignore_errors=True)
        build.rename(overlay)
    for name in admin_mode_armed(world_root):
        print(f'armed={name}')
    print('admin_mode=on')
def cmd_profile(root, m, args):
    """Profile lifecycle, delegated to the shared store.

    Nothing here writes a profile directory itself any more: a profile is one shared
    definition and `profile_store` owns its layout, its name rules and the refusal to
    delete one a server still links to.
    """
    fleet = portal_paths.world_root()
    store = profile_store.profiles_root(fleet)
    if args.profile_command == 'list':
        linked = profile_store.linked_profile(args.world_dir) if args.world_dir else None
        for row in profile_store.describe(fleet, store):
            marker = ' *' if row['profile'] == linked else ''
            servers = ', '.join(row['servers']) or 'no servers'
            print(f'{row["profile"]}{marker}  {row["packages"]} packages  {servers}')
        return
    if args.profile_command == 'create':
        profile_store.create(args.name, store)
        print(f'created={args.name}')
        return
    if args.profile_command == 'copy':
        profile_store.copy(args.source, args.name, store)
        print(f'copied={args.source}->{args.name}')
        return
    if args.profile_command == 'link':
        if not args.world_dir:
            raise RuntimeError('link needs --world WORLD')
        profile_store.link(args.world_dir, args.name, store)
        print(f'linked={args.world_dir.name}->{args.name}')
        return
    profile_store.delete(args.name, fleet, store)
    print(f'removed={args.name}')

def resolve_manifest(args):
    """The manifest of the profile this invocation acts on.

    Profiles live once, in the shared store, so this no longer walks into a world's
    directory. A world names its profile in its link file; --profile overrides it.
    """
    if args.manifest:
        manifest = args.manifest.resolve()
        if manifest.name != 'profile-manifest.json':
            raise RuntimeError('Manifest must be named profile-manifest.json')
        return manifest
    store = profile_store.profiles_root(portal_paths.world_root())
    profile = args.profile
    if not profile:
        world = resolve_world(args)
        if not world:
            raise RuntimeError('Specify --profile PROFILE, or --world WORLD to use that world\'s profile')
        profile = profile_store.linked_profile(world)
        if not profile:
            raise RuntimeError(
                f'{world.name} is not linked to a profile. Link it with '
                f'"valheim_mods.py --world {world.name} profile link <name>", or name one with --profile.'
            )
    manifest = profile_store.manifest_path(profile, store)
    if not manifest.is_file():
        raise RuntimeError(f'Profile not found: {profile} (looked in {store})')
    return manifest

def resolve_world(args):
    """The world this invocation acts on, or None when it named no world.

    A profile no longer sits inside a world, so the world cannot be recovered from the
    manifest path. Commands that touch a server require it and say so; the rest work
    without one.
    """
    if not args.world:
        return None
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', args.world):
        raise RuntimeError('Specify a valid --world WORLD')
    base = portal_paths.world_root()
    world = (base / args.world).resolve()
    if base not in world.parents or not world.is_dir():
        raise RuntimeError(f'World not found: {args.world}')
    return world

COMMANDS={
    'list':cmd_list, 'check-updates':cmd_check, 'notes':cmd_notes, 'search':cmd_search, 'add':cmd_add, 'sync':cmd_sync,
    'remove':cmd_remove, 'purge':cmd_purge, 'exclude':cmd_exclude, 'disable':cmd_disable, 'enable':cmd_enable, 'custom-list':cmd_custom_list,
    'custom-add':cmd_custom_add, 'custom-remove':cmd_custom_remove, 'custom-disable':cmd_custom_disable,
    'custom-enable':cmd_custom_enable, 'update':cmd_update, 'export-code':cmd_export,
    'deploy':cmd_deploy, 'profile':cmd_profile, 'release-status':cmd_release_status,
    'release-confirm':cmd_release_confirm, 'player-catalog':cmd_player_catalog,
    'admin-mode':cmd_admin_mode,
}

# Commands that can change settings text. `deploy` is here because the overlay it
# copies is what makes the server generate its configs, and `update` because
# --apply rewrites versions; a run of either that changes nothing records nothing,
# since snapshot() answers None rather than committing an empty tree.
HISTORY_COMMANDS = {
    'add', 'sync', 'remove', 'purge', 'exclude', 'disable', 'enable',
    'custom-add', 'custom-remove', 'custom-disable', 'custom-enable',
    'update', 'deploy', 'profile',
}

# Commands that delete settings. For these the snapshot BEFORE the work is not
# advisory: if the store cannot be written, the only copy of those configs is
# about to be deleted, so the operation is refused instead.
HISTORY_REQUIRED_COMMANDS = {'remove', 'purge'}

# Commands that read or write one server's own directories, which a profile does not
# name. Without --world these used to walk out of the profile path and land in the
# wrong tree; now they refuse.
WORLD_COMMANDS = {'remove', 'purge', 'deploy', 'release-status', 'release-confirm', 'admin-mode'}

# Commands that span the player editions rather than acting on one profile, so they resolve no
# manifest. Forcing one would make the answer depend on which profile the caller happened to
# name, which is exactly the ambiguity the union rule exists to remove.
PROFILE_FREE_COMMANDS = {'player-catalog'}

def record_settings(fleet_root, message, required):
    """Snapshot the fleet's settings. Returns True when the store is usable.

    History must not be able to fail a mod operation that already succeeded, so
    every call after the work only warns. The call before a deletion is the one
    that matters, and its caller refuses the work when this returns False.
    """
    try:
        commit = settings_history.snapshot(fleet_root, message)
    except settings_history.HistoryError as failure:
        if required:
            print(f'error: settings history is unwritable: {failure}', file=sys.stderr)
            return False
        print(f'warning: settings not recorded: {failure}', file=sys.stderr)
        return False
    if commit:
        print(f'settings_history={commit[:12]}')
    return True

def build_parser():
    p=argparse.ArgumentParser(); p.add_argument('--world'); p.add_argument('--profile'); p.add_argument('--manifest',type=Path); sub=p.add_subparsers(dest='command',required=True)
    listing=sub.add_parser('list'); listing.add_argument('--json',action='store_true')
    sub.add_parser('check-updates')
    notes = sub.add_parser('notes')
    notes.add_argument('--lines', type=int, default=40,
                       help='Cap on lines printed per release note; some authors paste an entire history.')
    s=sub.add_parser('search'); s.add_argument('query'); s.add_argument('--json',action='store_true')
    a=sub.add_parser('add'); a.add_argument('identifier'); a.add_argument('version',nargs='?'); a.add_argument('--client-only',action='store_true')
    sync=sub.add_parser('sync'); sync.add_argument('identifier')
    r=sub.add_parser('remove'); r.add_argument('identifier'); r.add_argument('--reason',required=True)
    purge=sub.add_parser('purge'); purge.add_argument('identifier'); purge.add_argument('--reason',required=True)
    exclude=sub.add_parser('exclude'); exclude.add_argument('identifier'); exclude.add_argument('version'); exclude.add_argument('--reason', required=True)
    disable=sub.add_parser('disable'); disable.add_argument('identifier')
    enable=sub.add_parser('enable'); enable.add_argument('identifier')
    sub.add_parser('custom-list')
    custom_add=sub.add_parser('custom-add'); custom_add.add_argument('identifier'); custom_add.add_argument('--scope',choices=('shared','client-only'),default='client-only')
    custom_remove=sub.add_parser('custom-remove'); custom_remove.add_argument('identifier')
    custom_disable=sub.add_parser('custom-disable'); custom_disable.add_argument('identifier')
    custom_enable=sub.add_parser('custom-enable'); custom_enable.add_argument('identifier')
    u=sub.add_parser('update'); u.add_argument('identifier',nargs='?'); u.add_argument('--all',action='store_true'); u.add_argument('--apply',action='store_true')
    sub.add_parser('export-code'); d=sub.add_parser('deploy'); d.add_argument('--apply',action='store_true')
    release_status=sub.add_parser('release-status'); release_status.add_argument('--require-complete',action='store_true')
    release_confirm=sub.add_parser('release-confirm')
    release_confirm.add_argument('profile_name')
    release_confirm.add_argument('client_type', choices=('flat', 'vr'))
    release_confirm.add_argument('release_id')
    release_confirm.add_argument('archive', type=Path)
    profile=sub.add_parser('profile'); profile_sub=profile.add_subparsers(dest='profile_command',required=True)
    profile_sub.add_parser('list')
    profile_create=profile_sub.add_parser('create'); profile_create.add_argument('name')
    profile_copy=profile_sub.add_parser('copy'); profile_copy.add_argument('source'); profile_copy.add_argument('name')
    profile_remove=profile_sub.add_parser('remove'); profile_remove.add_argument('name')
    profile_link=profile_sub.add_parser('link'); profile_link.add_argument('name')
    player_catalog=sub.add_parser('player-catalog')
    player_catalog.add_argument('--state', action='store_true',
                                help='Print only the fingerprint of the installed player set, reading no network.')
    admin_mode = sub.add_parser('admin-mode')
    admin_mode_sub = admin_mode.add_subparsers(dest='admin_mode_command', required=True)
    admin_mode_sub.add_parser('on')
    admin_mode_sub.add_parser('off')
    admin_mode_sub.add_parser('state')
    return p

def main():
    args=build_parser().parse_args()
    handler=COMMANDS.get(args.command)
    # A subparser registered without a COMMANDS entry used to surface as a bare
    # KeyError traceback through the portal admin UI; refuse it before any world
    # state is touched so the operator sees the wiring gap instead.
    if handler is None:
        print(f'error: {args.command} has no handler; the subcommand is registered but unwired',file=sys.stderr)
        return 2
    args.world_dir = resolve_world(args)
    if args.command in PROFILE_FREE_COMMANDS:
        if not args.world_dir:
            print(f'error: {args.command} needs --world WORLD; it reads that world\'s installed plugins',
                  file=sys.stderr)
            return 2
        return handler(None, None, args)
    args.manifest=resolve_manifest(args); root=args.manifest.parent; m=load(args.manifest)
    # A profile is shared, so the world can no longer be read off the manifest path.
    # Commands that touch a server say which one they need rather than assuming.
    if args.command in WORLD_COMMANDS and not args.world_dir:
        print(f'error: {args.command} needs --world WORLD; a profile alone does not name a server',
              file=sys.stderr)
        return 2
    # The subject reads as the operation an operator recognises. Slicing from the
    # subcommand drops --manifest/--world/--profile, whose absolute paths are noise
    # in a log and differ between the agent's invocation and a hand-run one.
    operation = ' '.join(sys.argv[sys.argv.index(args.command):])
    # The profile sits at <fleet>/profiles/<name>, so the fleet is two levels up. Read
    # that from the resolved manifest rather than the environment: a --manifest run has
    # no VALHEIM_ROOT and still has settings worth recording.
    try:
        fleet_root = portal_paths.world_root()
    except portal_paths.ConfigurationError:
        fleet_root = root.parents[1]
    label = args.world_dir.name if args.world_dir else root.name
    if args.command in HISTORY_COMMANDS:
        recorded = record_settings(fleet_root, f'{label}: before {operation}',
                                   args.command in HISTORY_REQUIRED_COMMANDS)
        if not recorded and args.command in HISTORY_REQUIRED_COMMANDS:
            print('error: refusing to delete settings that history cannot recover',file=sys.stderr)
            return 2
    outcome = handler(root,m,args) or 0
    if args.command in HISTORY_COMMANDS and outcome == 0:
        record_settings(fleet_root, f'{label}: {operation}', required=False)
    return outcome
if __name__=='__main__':
    try: raise SystemExit(main())
    except portal_paths.ConfigurationError as e: print(f'error: {e}',file=sys.stderr); raise SystemExit(portal_paths.EX_CONFIG)
    except (RuntimeError, requests.RequestException, subprocess.CalledProcessError) as e: print(f'error: {e}',file=sys.stderr); raise SystemExit(2)
