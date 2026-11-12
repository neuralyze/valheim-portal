"""Library resolution and checksum-bound blueprint repairs must compose in either import order."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parent
PROOF = r'''
import hashlib
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, sys.argv[1])
if sys.argv[2] == 'library-first':
    from terraform import sites
    from blueprints import to_rcon_plan as converter
else:
    from blueprints import to_rcon_plan as converter
    from terraform import sites
from blueprints import survey_datum
from library import audit_bases
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    (root/'fixture').mkdir()
    body = root/'fixture'/'namespace-order.blueprint'
    body.write_text('#pieces\nwood_floor;0;1;2;3;0;0;0;1;;1;1;1;\n')
    addition = root/'addition.blueprint'
    addition.write_text('#pieces\nsign;0;4;5;6;0;0;0;1;;1;1;1;\n')
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    repairs = root/'repairs.json'
    repairs.write_text(json.dumps({body.name: dict(source_sha256=digest(body),
        addition=addition.name, addition_sha256=digest(addition))}))
    sites.materialise.STAGING = root
    resolved = sites.materialise.locate(dict(name=body.name, body='staged',
        origin='fixture', sha256=digest(body)))
    assert resolved == body, (resolved, body)
    converter.materialise.AUTHORED_REPAIRS = repairs
    expected = [('wood_floor', (1., 2., 3.)), ('sign', (4., 5., 6.))]
    result = []
    for reader in (converter.read_objects, survey_datum.read_objects, audit_bases.read_objects):
        objects = [(obj.prefab, obj.pos) for obj in reader(resolved)]
        assert objects == expected, objects
        result.append(objects)
    addition.write_text(addition.read_text()+'# changed\n')
    try:
        converter.read_objects(resolved)
    except ValueError as error:
        assert 'SHA-256' in str(error), error
    else:
        raise AssertionError('checksum-bound authored addition was not enforced')
    print(json.dumps(result))
'''


class MaterialiseNamespacesTests(unittest.TestCase):
    def test_library_resolution_and_authored_repairs_survive_import_order(self):
        expected = [[['wood_floor', [1., 2., 3.]], ['sign', [4., 5., 6.]]]]*3
        for order in ('library-first', 'blueprints-first'):
            with self.subTest(order=order):
                result = subprocess.run([sys.executable, '-c', PROOF, str(ROOT), order],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                self.assertEqual(json.loads(result.stdout), expected)


if __name__ == '__main__':
    unittest.main()
