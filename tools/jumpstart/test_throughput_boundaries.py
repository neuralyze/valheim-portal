"""Consumer-visible offline session and fork provenance boundaries."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pipeline
from ledger import live, writer, schema


class ThroughputBoundaries(unittest.TestCase):
    def test_existing_ledger_opens_without_remeasuring_an_offline_install(self):
        manifest = dict(schema_version=schema.SCHEMA_VERSION,world='offline-fixture',
            seed_name='constructed',seed=1,world_version=1,world_gen_version=1,
            game_build_anchor=schema.GAME_BUILD_ANCHOR_DEFAULT,
            modifiers={},mods={},tool={})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = writer.Ledger.open('offline-fixture',actor='fixture',
                root=root/'run',manifest=manifest)
            original = before.records()
            with mock.patch.dict(os.environ,{'VALHEIM_WORLD_ROOT':str(root)}), \
                    mock.patch.object(writer,'mod_inventory',
                        side_effect=RuntimeError('installation is offline')):
                builder = live.LiveBuilder(actor='fixture',world='offline-fixture',root=root/'run')
                self.assertEqual(builder.led.records(),original)
                with self.assertRaisesRegex(RuntimeError,'installation is offline'):
                    live.LiveBuilder(actor='fixture',world='offline-fixture',root=root/'new-run')

    @unittest.skipUnless(hasattr(os,'fork'),'fork-specific producer tracing')
    def test_fork_child_only_dependency_is_recorded_without_atexit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dependency = root/'child-only.txt'
            dependency.write_text('actual producer input')
            trace,environment = pipeline._trace_begin(root)
            try:
                script = '''import os,sys
from pathlib import Path
pid=os.fork()
if pid==0:
    Path(sys.argv[1]).read_text()
    flush=getattr(sys,'_vh_producer_trace_flush',None)
    if flush is not None: flush()
    os._exit(0)
_,status=os.waitpid(pid,0)
assert status==0
'''
                result = subprocess.run([sys.executable,'-c',script,str(dependency)],
                    env=dict(os.environ,**environment),capture_output=True,text=True,timeout=20)
                self.assertEqual(result.returncode,0,result.stderr)
                report = pipeline._trace_read(trace)
                self.assertEqual(report['processes'],2)
                self.assertEqual(report['silent'],[])
                self.assertIn(dependency,report['read'])
            finally:
                import shutil
                shutil.rmtree(trace)

    @unittest.skipUnless(hasattr(os,'fork'),'fork-specific producer tracing')
    def test_cancelled_speculative_worker_keeps_child_only_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dependency = root/'cancelled-worker-input.txt'
            dependency.write_text('observed before cancellation')
            trace,environment = pipeline._trace_begin(root)
            roads = Path(__file__).parent/'roads'
            script = '''import os,sys
from pathlib import Path
from contextlib import closing
sys.path.insert(0,sys.argv[1])
from crossing_parallel import ordered_candidates
ready_r,ready_w=os.pipe()
block_r,block_w=os.pipe()
class Query:
    def candidate(self,crossing,bank,other,datum):
        if bank['rank']==1:
            Path(sys.argv[2]).read_text()
            os.write(ready_w,b'R')
            os.read(block_r,1)
        else:
            assert os.read(ready_r,1)==b'R'
        return {'ok':True}
requests=[dict(near_bank={'rank':rank},far_bank={},datum=0) for rank in range(2)]
with closing(ordered_candidates(Query(),{},requests,2)) as results:
    assert next(results)[1]['ok']
for fd in (ready_r,ready_w,block_r,block_w): os.close(fd)
'''
            try:
                result = subprocess.run([sys.executable,'-c',script,str(roads),str(dependency)],
                    env=dict(os.environ,**environment),capture_output=True,text=True,timeout=20)
                self.assertEqual(result.returncode,0,result.stderr)
                report = pipeline._trace_read(trace)
                self.assertEqual(report['processes'],3)
                self.assertEqual(report['silent'],[])
                self.assertIn(dependency,report['read'])
            finally:
                import shutil
                shutil.rmtree(trace)


if __name__ == '__main__':
    unittest.main()
