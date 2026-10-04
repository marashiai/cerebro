"""Hidden grader checks for the lease queue fixture."""

from pathlib import Path
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest

import lease_fixture
from fixtures import file_hashes

REFERENCE_STORAGE = '''import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def locked(path):
    path = Path(path)
    with path.with_name(path.name + ".lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield path
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read(path):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else {}


def write(path, value):
    path = Path(path)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = handle.name
        json.dump(value, handle)
    os.replace(temporary, path)


def inspect(path, action):
    with locked(path):
        return action(read(path))


def update(path, action):
    with locked(path):
        jobs = read(path)
        result = action(jobs)
        write(path, jobs)
        return result
'''

REFERENCE_JOBS = '''from copy import deepcopy
import math
import secrets

from storage import inspect, update


class Queue:
    def __init__(self, path):
        self.path = path

    def submit(self, identifier, payload):
        def add(jobs):
            if identifier in jobs:
                raise ValueError("duplicate identifier")
            jobs[identifier] = {"status": "pending", "payload": deepcopy(payload), "attempts": 0,
                                "result": None, "token": None, "lease_until": None}
        update(self.path, add)

    def get(self, identifier):
        def find(jobs):
            return deepcopy(jobs[identifier])
        return inspect(self.path, find)

    def claim(self, identifier, *, now, lease_seconds):
        if not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive and finite")
        def take(jobs):
            job = jobs[identifier]
            if job["status"] == "complete":
                return None
            if job["status"] == "running" and now < job["lease_until"]:
                return None
            token = secrets.token_urlsafe(24)
            job.update(status="running", attempts=job["attempts"] + 1,
                       token=token, lease_until=now + lease_seconds)
            return token
        return update(self.path, take)

    def complete(self, identifier, token, result, *, now):
        def finish(jobs):
            job = jobs[identifier]
            if job["status"] != "running" or job["token"] != token or now >= job["lease_until"]:
                return False
            job.update(status="complete", result=deepcopy(result), token=None, lease_until=None)
            return True
        return update(self.path, finish)

    def retry(self, identifier, token, *, now):
        def release(jobs):
            job = jobs[identifier]
            if job["status"] != "running" or job["token"] != token or now >= job["lease_until"]:
                return False
            job.update(status="pending", token=None, lease_until=None)
            return True
        return update(self.path, release)
'''

STALE_SNAPSHOT_JOBS = '''from copy import deepcopy
import math
import secrets

from storage import inspect, locked, write


class Queue:
    def __init__(self, path):
        self.path = path
        self.jobs = inspect(path, lambda jobs: jobs)

    def _save(self):
        with locked(self.path):
            write(self.path, self.jobs)

    def submit(self, identifier, payload):
        if identifier in self.jobs:
            raise ValueError("duplicate identifier")
        self.jobs[identifier] = {"status":"pending", "payload":deepcopy(payload), "attempts":0,
                                 "result":None, "token":None, "lease_until":None}
        self._save()

    def get(self, identifier):
        return inspect(self.path, lambda jobs: deepcopy(jobs[identifier]))

    def claim(self, identifier, *, now, lease_seconds):
        if not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("invalid lease")
        job = self.jobs[identifier]
        if job["status"] == "complete" or (job["status"] == "running" and now < job["lease_until"]):
            return None
        token = secrets.token_urlsafe(24)
        job.update(status="running", attempts=job["attempts"]+1, token=token,
                   lease_until=now+lease_seconds)
        self._save()
        return token

    def complete(self, identifier, token, result, *, now):
        job = self.jobs[identifier]
        if job["status"] != "running" or job["token"] != token or now >= job["lease_until"]:
            return False
        job.update(status="complete", result=deepcopy(result), token=None, lease_until=None)
        self._save()
        return True

    def retry(self, identifier, token, *, now):
        job = self.jobs[identifier]
        if job["status"] != "running" or job["token"] != token or now >= job["lease_until"]:
            return False
        job.update(status="pending", token=None, lease_until=None)
        self._save()
        return True
'''


class LeaseFixtureTests(unittest.TestCase):
    def test_seed_fails_and_reference_passes_named_behavior_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / 'seed'
            lease_fixture.seed(repo)
            before = file_hashes(repo)
            seeded = lease_fixture.grade(repo, before)
            self.assertFalse(seeded['functional_pass'])
            self.assertFalse(seeded['checks']['multiprocess_distinct_submissions_no_lost_updates'])
            self.assertFalse(seeded['checks']['multiprocess_same_id_single_claim'])
            (repo / 'storage.py').write_text(REFERENCE_STORAGE)
            (repo / 'jobs.py').write_text(REFERENCE_JOBS)
            repaired = lease_fixture.grade(repo, before)
            self.assertTrue(repaired['functional_pass'], repaired['checks'])
            self.assertTrue(repaired['scope_pass'])
            self.assertTrue(repaired['checks']['public_suite_pass'])

    def test_plausible_partial_repairs_fail_their_specific_hidden_checks(self):
        partials = (
            (REFERENCE_JOBS.replace(' or now >= job["lease_until"]', ''),
             'stale_owner_and_exact_expiry_rejected'),
            (REFERENCE_JOBS.replace('now < job["lease_until"]', 'now <= job["lease_until"]'),
             'expired_claim_replaced_and_old_token_rejected'),
            (REFERENCE_JOBS.replace('secrets.token_urlsafe(24)', 'str(now)'),
             'token_fresh_at_same_timestamp'),
            (STALE_SNAPSHOT_JOBS, 'already_open_instances_reload_before_writes_and_reads'),
        )
        for source, expected_failure in partials:
            with self.subTest(expected_failure=expected_failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo = root / 'repo'
                lease_fixture.seed(repo)
                before = file_hashes(repo)
                (repo / 'jobs.py').write_text(source)
                (repo / 'storage.py').write_text(REFERENCE_STORAGE)
                outcome = lease_fixture.grade(repo, before)
                self.assertFalse(outcome['checks'][expected_failure])

    def test_retry_token_comparison_is_independently_required(self):
        partial = REFERENCE_JOBS.replace(
            'if job["status"] != "running" or job["token"] != token or now >= job["lease_until"]:\n'
            '                return False\n            job.update(status="pending"',
            'if job["status"] != "running" or now >= job["lease_until"]:\n'
            '                return False\n            job.update(status="pending"')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / 'repo'
            lease_fixture.seed(repo)
            before = file_hashes(repo)
            (repo / 'jobs.py').write_text(partial)
            (repo / 'storage.py').write_text(REFERENCE_STORAGE)
            outcome = lease_fixture.grade(repo, before)
            self.assertFalse(outcome['checks']['wrong_token_retry_does_not_mutate'])

if __name__ == '__main__':
    unittest.main()
