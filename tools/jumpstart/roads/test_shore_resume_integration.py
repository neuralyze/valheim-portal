"""Physical search callbacks and actual file-content checkpoint invalidation."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import poi
import route
import shores as S
from shore_basis import checkpoint_basis
from shore_progress import ShoreProgress


class ResumeIntegrationTests(unittest.TestCase):
    def test_changed_resolved_location_dump_invalidates_completed_geometry(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temporary)
            dump = root / 'resolved-location.json'
            dump.write_text(json.dumps({'locations': [{'prefab': 'ruin', 'reach': 8}]}))
            requirements = [dict(id='dock', node='dock', anchor='anchor')]
            with patch.object(poi, 'location_dump', return_value=dump), patch('sys.argv', ['shore-search']):
                before = checkpoint_basis(root/'field.bin', {'sha256': 'unchanged-field'}, {}, requirements)
                progress = ShoreProgress(root/'progress.json', before)
                progress.complete_requirement('dock', {'id': 'dock', 'accepted': True})
                dump.write_text(json.dumps({'locations': [{'prefab': 'ruin', 'reach': 80}]}))
                after = checkpoint_basis(root/'field.bin', {'sha256': 'unchanged-field'}, {}, requirements)
                resumed = ShoreProgress(root/'progress.json', after)
            self.assertEqual(resumed.load_completed(), {})

    def test_source_context_is_restored_after_nested_candidate_refusal(self):
        half = 800.5
        axis = -half + 0.5 + np.arange(1601)
        z = np.broadcast_to(axis[:, None], (1601, 1601))
        # Keep the constructed wooden jetty supportable so this case reaches
        # its nested callback instead of exhausting an invalid deep-bed fixture.
        heights = np.where(z <= 0, S.DECK_Y,
                           np.maximum(25., S.DECK_Y-z*.8)).astype(np.float32)
        field = S.W.Field('shore-trunk-primary', 0, 0, half, 1, 1601, heights,
                          np.ones((1601, 1601), dtype=np.uint8))
        fields = {field.id: field, 'trunk-primary': field}
        nodes = {'stathub': dict(xz=(0., -40.), patch='trunk-primary', island='spawn')}
        requirement = next(r for r in S.REQUIREMENTS if r['id'] == 'dock-stationhub')
        def graph(field, cls):
            corridor = route.Corridor(field, cell_m=4., grade_max=route.GRADE[cls])
            return S.geography.RoadGraph(field, cls=cls,
                forbid=np.zeros((corridor.m, corridor.m), dtype=bool))
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            path = Path(temporary)/'progress.json'
            progress = ShoreProgress(path, {'requirement_ids': [requirement['id']]},
                                     interval_seconds=1e-9)
            attempts = 0
            def accept(candidate):
                nonlocal attempts
                if candidate['length_m'] < 60:
                    return False
                attempts += 1
                if attempts == 1:
                    # The far-end search changes shared progress, then refuses.
                    progress(dict(requirement_id='far-end', root_index=999,
                                  root_total=999, length=94, bearing=180))
                    return False
                event = json.loads(path.read_text())['progress']['event']
                self.assertEqual(event['requirement_id'], requirement['id'])
                self.assertNotEqual(event['root_index'], 999)
                self.assertEqual(event['length'], candidate['length_m'])
                self.assertEqual(event['bearing'], candidate['bearing'])
                return True
            result = S.solve_site(requirement, nodes, fields, graph_factory=graph,
                                  accept=accept, progress=progress)
            self.assertTrue(result['structural']['ok'])
            self.assertGreaterEqual(attempts, 2)


if __name__ == '__main__':
    unittest.main()
