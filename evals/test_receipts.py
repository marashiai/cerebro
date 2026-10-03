"""Run the actual immutable fixture to check private, source-bound test receipts."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from fixtures import seed_episode
from native import TestJournal
from runtime import file_hashes

FIXED = 'import csv\ndef parse_row(text):\n    return next(csv.reader([text])) if text else []\n'


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / 'repo'
        seed_episode(self.repo)
        (self.repo / 'parser.py').write_text(FIXED)

    def journal(self):
        return TestJournal(self.root, uuid.uuid4().hex, 'verify')

    def execute(self, journal, argv=None, cwd=None):
        return subprocess.run([sys.executable, *(argv or ['-m', 'unittest', '-v'])],
                              cwd=cwd or self.repo, text=True, capture_output=True,
                              env={**os.environ, **journal.environment(), 'CEREBRO_CHILD_ROLE': 'verify'}, timeout=10)

    def test_captured_output_has_complete_receipt_with_real_start_time_and_no_replay(self):
        journal = self.journal()
        started = time.time()
        result = self.execute(journal)
        finished = time.time()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('CEREBRO_EVAL_TEST_RECEIPT', result.stdout + result.stderr)
        records = journal.take(self.repo, 'native-thread')
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]['passed'])
        self.assertEqual(records[0]['source'], file_hashes(self.repo))
        self.assertEqual(records[0]['thread_id'], 'native-thread')
        self.assertLessEqual(started, records[0]['started_at'])
        self.assertLessEqual(records[0]['started_at'], records[0]['finished_at'])
        self.assertLessEqual(records[0]['finished_at'], finished)
        self.assertEqual(journal.path.stat().st_mode & 0o777, 0o600)
        original = journal.path.read_bytes()
        with journal.path.open('ab') as target:
            target.write(original)
        self.assertEqual(journal.take(self.repo, 'native-thread'), [])
        with self.assertRaisesRegex(RuntimeError, 'multiple native threads'):
            journal.take(self.repo, 'another-thread')

    def test_failed_skipped_and_partial_suites_cannot_pass(self):
        scenarios = {
            'failed': ['-m', 'unittest', '-v'],
            'skipped': ['-c', 'import unittest,test_parser\n'
                        'test_parser.ParserTests.test_empty=unittest.skip("probe")(test_parser.ParserTests.test_empty)\n'
                        'unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromModule(test_parser))'],
            'partial': ['-m', 'unittest', 'test_parser.ParserTests.test_empty'],
        }
        for name, argv in scenarios.items():
            with self.subTest(name=name):
                (self.repo / 'parser.py').write_text('def parse_row(text):\n    return []\n' if name == 'failed' else FIXED)
                journal = self.journal()
                self.execute(journal, argv)
                receipts = journal.take(self.repo, 'native-thread')
                self.assertEqual(len(receipts), 1)
                self.assertFalse(receipts[0]['passed'])
        journal = self.journal()
        self.execute(journal, ['-c', 'import test_parser'])
        self.assertEqual(journal.take(self.repo, 'native-thread'), [])

    def test_wrong_cwd_and_different_source_root_are_rejected(self):
        journal = self.journal()
        result = self.execute(journal, [str(self.repo / 'test_parser.py'), '-v'], cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(journal.take(self.repo, 'native-thread'), [])
        journal = self.journal()
        self.execute(journal)
        self.assertEqual(journal.take(self.root, 'native-thread'), [])

    def test_changed_stale_and_modified_fixture_sources_are_rejected(self):
        journal = self.journal()
        self.execute(journal)
        (self.repo / 'parser.py').write_text(FIXED + '# changed after verification\n')
        self.assertEqual(journal.take(self.repo, 'native-thread'), [])
        journal = self.journal()
        self.execute(journal, ['-c', 'import unittest,test_parser\n'
            'from pathlib import Path\n'
            'p=Path("parser.py");p.write_text(p.read_text()+"# changed during verification\\n")\n'
            'unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromModule(test_parser))'])
        self.assertEqual(journal.take(self.repo, 'native-thread'), [])
        journal = self.journal()
        self.execute(journal)
        journal.started_at = time.time() + 1
        self.assertEqual(journal.take(self.repo, 'native-thread'), [])
        fixture = self.repo / 'test_parser.py'
        fixture.write_text(fixture.read_text() + '# modified immutable fixture\n')
        journal = self.journal()
        self.execute(journal)
        self.assertEqual(journal.take(self.repo, 'native-thread'), [])

    def test_wrong_worker_or_role_and_duplicate_before_records_are_rejected(self):
        for field, value in [('worker_id', 'another-worker'), ('role', 'execute'), ('phase', 'before')]:
            with self.subTest(field=field):
                journal = self.journal()
                self.execute(journal)
                records = [json.loads(line) for line in journal.path.read_text().splitlines()]
                records[-1][field] = value
                journal.path.write_text(''.join(json.dumps(record) + '\n' for record in records))
                self.assertEqual(journal.take(self.repo, 'native-thread'), [])

    def test_native_recorder_collects_captured_subprocess_tests_without_stdout_markers(self):
        native = self.root / 'native-fixture.py'
        native.write_text('''import json,os,subprocess,sys
from pathlib import Path
def emit(value):
    print(json.dumps(value), flush=True)
for raw in sys.stdin:
    request=json.loads(raw)
    if request['method']=='thread/start':
        repo=request['params']['cwd']
        emit({'id':request['id'],'result':{'thread':{'id':'native-thread'},'model':'fixture','reasoningEffort':'medium'}})
    elif request['method']=='turn/start':
        item={'id':'verification','type':'commandExecution','cwd':str(Path.cwd()),'command':'captured verification'}
        emit({'method':'item/started','params':{'item':item}})
        result=subprocess.run([sys.executable,'-m','unittest','-v'],cwd=repo,text=True,capture_output=True)
        Path('private-report.txt').write_text(result.stdout+result.stderr)
        emit({'method':'item/completed','params':{'item':{**item,'exitCode':result.returncode,'aggregatedOutput':'Report written.'}}})
        emit({'method':'turn/completed','params':{'turn':{'status':'completed'}}})
''')
        requests = [{'id': 1, 'method': 'thread/start', 'params': {'cwd': str(self.repo), 'model': 'fixture'}},
                    {'id': 2, 'method': 'turn/start', 'params': {'threadId': 'native-thread', 'input': []}}]
        recorder = Path(__file__).with_name('native.py')
        result = subprocess.run([sys.executable, str(recorder), sys.executable, str(native), 'app-server'],
                                input=''.join(json.dumps(value) + '\n' for value in requests),
                                cwd=self.root, text=True, capture_output=True, timeout=10,
                                env={**os.environ, 'CEREBRO_EVAL_DIR': str(self.root),
                                     'CEREBRO_EVAL_REPO': str(self.repo), 'CEREBRO_CHILD_ROLE': 'verify'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('test_empty', result.stdout)
        self.assertNotIn('CEREBRO_EVAL_TEST_RECEIPT', result.stdout)
        self.assertIn('Ran 3 tests', (self.root / 'private-report.txt').read_text())
        seen = [json.loads(line) for path in self.root.glob('observations-*.jsonl') for line in path.read_text().splitlines()]
        receipts = [value for value in seen if value['type'] == 'tests']
        self.assertEqual(len(receipts), 1)
        self.assertTrue(receipts[0]['passed'])
        self.assertEqual(receipts[0]['role'], 'verify')
        self.assertEqual(receipts[0]['thread_id'], 'native-thread')
        self.assertEqual(receipts[0]['source_root'], str(self.repo))
        activity = next(value for value in seen if value['type'] == 'activity')
        self.assertEqual(activity['cwd'], str(self.root))
        self.assertEqual(activity['source_root'], str(self.repo))


if __name__ == '__main__':
    unittest.main()
