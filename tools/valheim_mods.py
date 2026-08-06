#!/usr/bin/env python3
"""Manifest-driven Valheim mod controller."""
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, subprocess, sys, tempfile, zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import requests
from packaging.version import Version

if __package__:
    from . import portal_paths
else:
    import portal_paths

TOOLS_ROOT = portal_paths.TOOLS_ROOT
API = 'https://thunderstore.io/c/valheim/api/v1/package/'

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

def package_paths(root, identifier):
    plugin_name = package_install_name(identifier)
    world_root = root.parents[2]
    paths = [
        *(cache(root) / side / 'BepInEx' / 'plugins' / plugin_name for side in ('client', 'server')),
        root / 'manual-mods' / plugin_name,
        world_root / 'config_merged' / 'bepinex' / 'plugins' / plugin_name,
        world_root / 'data' / 'bepinex' / 'BepInEx' / 'plugins' / plugin_name,
    ]
    return [path for path in paths if path.exists() and not path.is_symlink()]

def backup_removal_inputs(root, manifest_path, paths, configs):
    world_root = root.parents[2]
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

def assert_package_purged(root, manifest, identifier):
    matches = matching_manifest_entries(manifest, identifier)
    if matches:
        raise RuntimeError(f'Package remains in manifest: {identifier}')
    paths = package_paths(root, identifier)
    if paths:
        raise RuntimeError(f'Package files remain: {", ".join(str(path) for path in paths)}')
    configs = plugin_config_files(root.parents[2], identifier)
    if configs:
        raise RuntimeError(f'Package configs remain: {", ".join(str(config) for config in configs)}')

def client_release_targets(root, manifest):
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
            if entry.get('world') == manifest['world_name'] and entry.get('source_profile') == root.name:
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

def record_release_cutover(root, manifest, identifier):
    targets = client_release_targets(root, manifest)
    if not targets:
        return None
    world_root = root.parents[2]
    path, cutover = load_release_cutover(world_root)
    if cutover is None:
        cutover = {
            'schema_version': 1,
            'world_name': manifest['world_name'],
            'removals': [],
            'confirmations': {},
        }
    if cutover.get('schema_version') != 1 or cutover.get('world_name') != manifest['world_name']:
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
    path, cutover = load_release_cutover(root.parents[2])
    if not cutover:
        print('client_release_cutover=complete')
        return
    require_release_cutover_complete(root.parents[2]) if args.require_complete else None
    for (profile, client_type), identifiers in sorted(pending_release_targets(cutover).items()):
        print(f'pending={profile}/{client_type} identifiers={",".join(sorted(identifiers))}')
    print(f'cutover_record={path}')

def cmd_release_confirm(root, manifest, args):
    world_root = root.parents[2]
    path, cutover = load_release_cutover(world_root)
    if not cutover:
        raise RuntimeError('No client release cutover is pending')
    target = (args.profile_name, args.client_type)
    pending = pending_release_targets(cutover)
    identifiers = pending.get(target)
    if not identifiers:
        raise RuntimeError(f'No pending release target: {args.profile_name}/{args.client_type}')
    release = archive_manifest(args.archive.resolve())
    if release.get('world') != manifest['world_name'] or release.get('profile') != args.profile_name or release.get('client_type') != args.client_type:
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
def custom_root(root): return root.parents[2] / 'mods' / 'custom'

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
    if not zipfile.is_zipfile(archive): raise RuntimeError(f'Invalid package archive: {archive}')
    target = cache(root) / side / 'BepInEx' / 'plugins' / p['name']
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    with zipfile.ZipFile(archive) as z:
        total = 0
        for member in z.infolist():
            raw_name = member.filename.replace('\\','/')
            name = raw_name
            for prefix in (f'BepInEx/plugins/{p["name"]}/', 'BepInEx/plugins/', f'plugins/{p["name"]}/', 'plugins/', f'{p["name"]}/'):
                if name.startswith(prefix):
                    name = name[len(prefix):]
                    break
            path = PurePosixPath(name)
            if not name or raw_name.endswith('/'):
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
            'world': m['world_name'],
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
def cmd_check(root, m, _):
    reg=index(); updates=[]
    for item in all_packages(m):
        remote=latest(reg[item['identifier']])['version_number']
        if Version(remote)>Version(item['version']): updates.append((item['identifier'], item['version'], remote))
    for row in updates: print(f'{row[0]} {row[1]} -> {row[2]}')
    print(f'updates={len(updates)}'); return 1 if updates else 0
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
def cmd_remove(root, m, args):
    matches = matching_manifest_entries(m, args.identifier)
    if not matches:
        raise RuntimeError(f'Not present: {args.identifier}')
    require_stopped(m['world_name'])
    paths = package_paths(root, args.identifier)
    configs = plugin_config_files(root.parents[2], args.identifier)
    backup = backup_removal_inputs(root, args.manifest, paths, configs)
    # Filter through the same shape-tolerant reader the lookup above uses: a package list
    # may hold objects and bare identifier strings side by side, and `.get` on a string
    # raises. Rebuilding these lists is what actually deselects the package, so a crash
    # here left the manifest untouched while the caller had already been told it matched.
    for key in MANIFEST_PACKAGE_KEYS:
        m[key] = [item for item in m.get(key, []) if package_identifier(item) != args.identifier]
    m['custom_packages'] = [item for item in custom_packages(m)
                            if (item.get('id') if isinstance(item, dict) else item) != args.identifier]
    save(args.manifest, m)
    remove_paths(paths)
    remove_paths(configs)
    assert_package_purged(root, load(args.manifest), args.identifier)
    cutover = record_release_cutover(root, m, args.identifier)
    (backup / 'removal.json').write_text(json.dumps({
        'identifier': args.identifier,
        'reason': args.reason,
        'removed_at': datetime.now(timezone.utc).isoformat(),
        'client_release_cutover': str(cutover) if cutover else None,
    }, indent=2) + '\n')
    print(f'source_removed={args.identifier}\nbackup={backup}')
    if cutover:
        print(f'client_release_cutover_required={cutover}')

def cmd_purge(root, m, args):
    if matching_manifest_entries(m, args.identifier):
        raise RuntimeError(f'Package is still selected: {args.identifier}; use remove instead')
    require_stopped(m['world_name'])
    paths = package_paths(root, args.identifier)
    configs = plugin_config_files(root.parents[2], args.identifier)
    if not paths and not configs:
        raise RuntimeError(f'No orphaned package files found: {args.identifier}')
    backup = backup_removal_inputs(root, args.manifest, paths, configs)
    remove_paths(paths)
    remove_paths(configs)
    assert_package_purged(root, m, args.identifier)
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

def cmd_deploy(root,m,args):
    world=m['world_name']
    server_plugins=cache(root)/'server'/'BepInEx'/'plugins'
    manual_plugins=root/'manual-mods'
    world_root=root.parents[2]
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
    print('deployed=true')
def cmd_profile(root, m, args):
    world_root=root.parents[2]
    profiles_root=world_root/'mods'/'profiles'
    active_path=world_root/'mods'/'.active-mod-profile'
    active=active_path.read_text().strip() if active_path.is_file() else None
    if args.profile_command == 'list':
        for profile in sorted(profiles_root.iterdir() if profiles_root.is_dir() else []):
            if (profile/'profile-manifest.json').is_file():
                marker=' *' if profile.name == active else ''
                print(f'{profile.name}{marker}')
        return
    valid=lambda name: __import__('re').fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name)
    if not valid(args.name):
        raise RuntimeError(f'Invalid profile name: {args.name}')
    destination=profiles_root/args.name
    if args.profile_command == 'create':
        if destination.exists():
            raise RuntimeError(f'Profile already exists: {args.name}')
        destination.mkdir(parents=True)
        for side in ('client', 'server'):
            (destination/'manager-cache'/side/'BepInEx'/'plugins').mkdir(parents=True)
        (destination/'manual-mods').mkdir()
        save(destination/'profile-manifest.json', {
            'schema_version': 1, 'profile_name': args.name, 'world_name': m['world_name'],
            'packages': [], 'client_only_packages': [], 'disabled_packages': [], 'custom_packages': [],
            'manual_server_packages': [], 'excluded_packages': [],
        })
        print(f'created={args.name}')
        return
    if args.profile_command == 'copy':
        source=profiles_root/args.source
        if not valid(args.source) or not (source/'profile-manifest.json').is_file():
            raise RuntimeError(f'Profile not found: {args.source}')
        if destination.exists():
            raise RuntimeError(f'Profile already exists: {args.name}')
        shutil.copytree(source, destination)
        copied=load(destination/'profile-manifest.json')
        copied['profile_name']=args.name
        copied['world_name']=m['world_name']
        save(destination/'profile-manifest.json', copied)
        print(f'copied={args.source}->{args.name}')
        return
    if args.name == active:
        raise RuntimeError('Cannot remove the active profile; activate another profile first')
    if not destination.is_dir():
        raise RuntimeError(f'Profile not found: {args.name}')
    shutil.rmtree(destination)
    print(f'removed={args.name}')
def resolve_manifest(args):
    if args.manifest:
        manifest = args.manifest.resolve()
        if manifest.name != 'profile-manifest.json':
            raise RuntimeError('Manifest must be named profile-manifest.json')
        return manifest
    valid = lambda value: bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', value or ''))
    if not valid(args.world):
        raise RuntimeError('Specify a valid --world WORLD')
    base = portal_paths.world_root()
    world_root = (base / args.world).resolve()
    if base not in world_root.parents or not world_root.is_dir():
        raise RuntimeError(f'World not found: {args.world}')
    profile = args.profile
    if not profile:
        active = world_root / 'mods' / '.active-mod-profile'
        profile = active.read_text().strip() if active.is_file() else ''
    if not valid(profile):
        raise RuntimeError('Specify a valid --profile PROFILE')
    manifest = (world_root / 'mods' / 'profiles' / profile / 'profile-manifest.json').resolve()
    profiles_root = (world_root / 'mods' / 'profiles').resolve()
    if profiles_root not in manifest.parents or not manifest.is_file():
        raise RuntimeError(f'Manifest not found for {args.world}/{profile}')
    return manifest

COMMANDS={
    'list':cmd_list, 'check-updates':cmd_check, 'search':cmd_search, 'add':cmd_add, 'sync':cmd_sync,
    'remove':cmd_remove, 'purge':cmd_purge, 'exclude':cmd_exclude, 'disable':cmd_disable, 'enable':cmd_enable, 'custom-list':cmd_custom_list,
    'custom-add':cmd_custom_add, 'custom-remove':cmd_custom_remove, 'custom-disable':cmd_custom_disable,
    'custom-enable':cmd_custom_enable, 'update':cmd_update, 'export-code':cmd_export,
    'deploy':cmd_deploy, 'profile':cmd_profile, 'release-status':cmd_release_status,
    'release-confirm':cmd_release_confirm,
}

def build_parser():
    p=argparse.ArgumentParser(); p.add_argument('--world'); p.add_argument('--profile'); p.add_argument('--manifest',type=Path); sub=p.add_subparsers(dest='command',required=True)
    listing=sub.add_parser('list'); listing.add_argument('--json',action='store_true')
    sub.add_parser('check-updates')
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
    args.manifest=resolve_manifest(args); root=args.manifest.parent; m=load(args.manifest)
    return handler(root,m,args) or 0
if __name__=='__main__':
    try: raise SystemExit(main())
    except portal_paths.ConfigurationError as e: print(f'error: {e}',file=sys.stderr); raise SystemExit(portal_paths.EX_CONFIG)
    except (RuntimeError, requests.RequestException, subprocess.CalledProcessError) as e: print(f'error: {e}',file=sys.stderr); raise SystemExit(2)
