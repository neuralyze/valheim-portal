"""Content basis for reusable completed shore requirements, not live-world proof."""
import hashlib
import json
import os
from pathlib import Path
import sys


def checkpoint_basis(field_path, manifest, nodes, requirements, *,
                     placed_objects=None, pad_keepouts=None):
    import assemble
    import geography
    import worldseed
    import poi
    root = Path(__file__).resolve().parent.parent

    def digest(path):
        path = Path(path)
        if not path.is_file():
            return None
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()

    sources = {}
    for directory, children, files in os.walk(root):
        children[:] = sorted(child for child in children if child not in {
            '.git', '__pycache__', 'runs', 'census', 'library'})
        for name in sorted(files):
            if name.endswith('.py'):
                path = Path(directory) / name
                sources[str(path.relative_to(root))] = digest(path)
    inputs = [geography.geography_path(), worldseed.sites_path(), worldseed.plan_path(),
              assemble.GEOMETRY, assemble.MATERIAL,
              Path(os.environ.get('ACCEPT_PIECE_GEOMETRY') or assemble.GEOMETRY),
              poi.location_dump(worldseed.seed())]
    installation_dir, pattern, _ = worldseed.installations_read(worldseed.world())
    inputs.extend(sorted(installation_dir.glob(pattern)))
    inputs.extend(sorted((root / 'roads').glob('mod_keepout*.json')))
    # Bind the actual RoadGraph resolver above, plus explicitly declared inputs.
    for flag in ('--locations', '--grid', '--census'):
        if flag in sys.argv:
            index = sys.argv.index(flag)
            if index + 1 < len(sys.argv):
                inputs.append(Path(sys.argv[index + 1]))
    # Bind the actual provider, including effective written samples and datum
    # records, not just nominal rectangles or a count of planned foundations.
    pad_context = None if pad_keepouts is None else {
        "rects": pad_keepouts.rects, "unlaid": pad_keepouts.unlaid,
        "discs": pad_keepouts.discs, "by_id": pad_keepouts.by_id,
        "levelled": [[x, z, owner] for (x, z), owner in
                     sorted(pad_keepouts.levelled.items())],
        "undecodable": pad_keepouts.undecodable}
    basis = dict(seed=worldseed.seed(), world=worldseed.world(),
        field=str(Path(field_path).resolve()),
        field_sha256=manifest['sha256'], field_manifest=manifest,
        code=sources, inputs={str(path.resolve()): digest(path) for path in inputs},
        semantic_nodes=nodes, requirements=requirements,
        placed_objects_sha256=(None if placed_objects is None else
            hashlib.sha256(json.dumps(
                placed_objects, allow_nan=False).encode()).hexdigest()),
        pad_keepouts_sha256=(None if pad_context is None else
            hashlib.sha256(json.dumps(
                pad_context, sort_keys=True, allow_nan=False).encode()).hexdigest()),
        preset=os.environ.get('VH_NORTH_PRESET', 'pre-kall'),
        requirement_ids=[r['id'] for r in requirements if r['node'] != 'ferry-east']
                        + ['ferry-east-isle'])
    # Canonicalize tuples used by semantic coordinates without lossy rounding.
    return json.loads(json.dumps(basis, sort_keys=True, allow_nan=False))
