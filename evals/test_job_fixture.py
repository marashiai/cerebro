"""Hidden grader checks for the persisted job fixture."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import job_fixture
from fixtures import file_hashes


def repaired_source():
    source = job_fixture.JOBS.replace('from storage import load, save', 'from storage import load, save\nfrom copy import deepcopy')
    source = source.replace('        self.jobs[identifier] = {', '        if identifier in self.jobs:\n            raise ValueError("duplicate")\n        self.jobs[identifier] = {', 1)
    source = source.replace('"payload": payload', '"payload": deepcopy(payload)')
    source = source.replace('        job["status"] = "running"', '        if job["status"] != "pending":\n            raise ValueError("not pending")\n        job["status"] = "running"')
    source = source.replace('        job["status"] = "complete"', '        if job["status"] != "running":\n            raise ValueError("not running")\n        job["status"] = "complete"')
    source = source.replace('        job["result"] = result', '        job["result"] = deepcopy(result)\n        save(self.path, self.jobs)')
    source = source.replace('        self.jobs[identifier]["status"] = "pending"', '        if self.jobs[identifier]["status"] != "running":\n            raise ValueError("not running")\n        self.jobs[identifier]["status"] = "pending"')
    source = source.replace('        self.jobs[identifier]["attempts"] = 0\n', '')
    return source.replace('return dict(self.jobs[identifier])', 'return deepcopy(self.jobs[identifier])')


class JobFixtureTests(unittest.TestCase):
    def test_hidden_state_and_ownership_checks_reject_visible_only_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / 'repo'
            job_fixture.seed(repo)
            before = file_hashes(repo)
            self.assertFalse(job_fixture.grade(repo, before)['functional_pass'])
            (repo / 'jobs.py').write_text(repaired_source())
            self.assertTrue(job_fixture.grade(repo, before)['correct'])
            source = repaired_source().replace('        save(self.path, self.jobs)\n\n    def get',
                                               '        self.jobs[identifier]["attempts"] = 0\n        save(self.path, self.jobs)\n\n    def get')
            (repo / 'jobs.py').write_text(source)
            visible = subprocess.run([sys.executable, '-m', 'unittest', '-v'], cwd=repo, capture_output=True)
            self.assertEqual(visible.returncode, 0)
            self.assertFalse(job_fixture.grade(repo, before)['functional_pass'])
            (repo / 'jobs.py').write_text(repaired_source())
            (repo / 'test_jobs.py').write_text('print("passed")\n')
            self.assertFalse(job_fixture.grade(repo, before)['scope_pass'])

if __name__ == '__main__':
    unittest.main()
