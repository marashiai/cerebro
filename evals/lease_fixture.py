"""A multi-process lease queue repair fixture with observable hidden checks."""

import json
import math
import sys

from fixtures import PARSER_TESTS, bounded_process, file_hashes, seed_repo

REQUIREMENTS = (
    'Repair jobs.py and storage.py for a JSON-backed leased job queue. Preserve this API: '
    'Queue(path); submit(identifier, payload) -> None; get(identifier) -> an independent snapshot of the latest '
    'persisted job, including writes made through another already-open Queue; claim(identifier, *, now, '
    'lease_seconds) -> a nonempty token string or None; complete(identifier, token, result, *, now) -> bool; '
    'retry(identifier, token, *, now) -> bool. submit stores an independent JSON-compatible payload in a pending '
    'job with attempts=0, result=None, token=None and lease_until=None. Duplicate identifiers raise ValueError; '
    'unknown identifiers in get/claim/complete/retry raise KeyError. claim accepts only positive finite lease_seconds. '
    'Pending jobs and running jobs at or beyond lease_until are eligible; expiration is exactly now >= lease_until. '
    'A successful claim atomically increments attempts, sets running, assigns a unique new token and sets '
    'lease_until=now+lease_seconds. A busy running or completed job returns None without mutation. complete and retry '
    'succeed only for a running job with its current token and now < lease_until; otherwise return False without '
    'mutation. Completion stores an independent result, sets status=complete and clears token/lease_until. Retry sets '
    'pending and clears token/lease_until while preserving attempts. A later claim never reuses a token. All reads and '
    'mutations must be safe across Queue instances and simultaneous POSIX processes sharing one path: no double claim, '
    'lost unrelated jobs, stale completion or partial JSON through the API. Successful operations survive reopen. '
    'Use Python standard library only and an existing parent directory. now is finite and data is JSON-compatible. '
    'No service, database, power-loss/fsync promise or symlink threat model is required. Change only jobs.py, storage.py '
    'and optionally test_regressions.py. Preserve supplied tests. Run python3 -m unittest -v before completion; do not '
    'commit or publish.'
)

STORAGE = '''import json
from pathlib import Path


def load(path):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else {}


def save(path, jobs):
    Path(path).write_text(json.dumps(jobs))
'''

JOBS = '''from storage import load, save


class Queue:
    def __init__(self, path):
        self.path = path
        self.jobs = load(path)

    def submit(self, identifier, payload):
        self.jobs[identifier] = {"status": "pending", "payload": payload, "attempts": 0,
                                 "result": None, "token": None, "lease_until": None}
        save(self.path, self.jobs)

    def claim(self, identifier, *, now, lease_seconds):
        job = self.jobs[identifier]
        job.update(status="running", attempts=job["attempts"] + 1,
                   token=str(now), lease_until=now + lease_seconds)
        save(self.path, self.jobs)
        return job["token"]

    def complete(self, identifier, token, result, *, now):
        job = self.jobs[identifier]
        job.update(status="complete", result=result, token=None, lease_until=None)
        save(self.path, self.jobs)
        return True

    def retry(self, identifier, token, *, now):
        self.jobs[identifier].update(status="pending", attempts=0, token=None, lease_until=None)
        save(self.path, self.jobs)
        return True

    def get(self, identifier):
        return dict(self.jobs[identifier])
'''

_receipt_prefix = PARSER_TESTS[:PARSER_TESTS.index('receipt("before")') + len('receipt("before")')]
TESTS = _receipt_prefix + '''
import tempfile

from jobs import Queue


class LeaseQueueSmokeTests(unittest.TestCase):
    def run(self, result=None):
        result = result if result is not None else self.defaultTestResult()
        fields = ("errors", "failures", "skipped", "expectedFailures", "unexpectedSuccesses")
        before = [len(getattr(result, name)) for name in fields]
        count = result.testsRun
        completed = super().run(result)
        _results.append({"name": self._testMethodName, "passed": result.testsRun == count + 1
                         and before == [len(getattr(result, name)) for name in fields]})
        return completed

    @classmethod
    def tearDownClass(cls):
        receipt("after")

    def test_submit_and_snapshot_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = Queue(Path(tmp) / "jobs.json")
            payload = {"items": ["one"]}
            queue.submit("one", payload)
            payload["items"].append("changed")
            snapshot = queue.get("one")
            snapshot["payload"]["items"].append("snapshot")
            self.assertEqual(queue.get("one")["payload"], {"items": ["one"]})

    def test_claim_completion_and_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jobs.json"
            queue = Queue(path)
            queue.submit("one", {"input": 3})
            token = queue.claim("one", now=10, lease_seconds=5)
            self.assertIsInstance(token, str)
            self.assertTrue(queue.complete("one", token, {"answer": 9}, now=11))
            self.assertEqual(Queue(path).get("one")["status"], "complete")

    def test_retry_keeps_attempt_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = Queue(Path(tmp) / "jobs.json")
            queue.submit("one", {})
            token = queue.claim("one", now=1, lease_seconds=4)
            self.assertTrue(queue.retry("one", token, now=2))
            self.assertEqual(queue.get("one")["attempts"], 1)
'''

PROCESS_CHECKS = r'''import json, multiprocessing as mp, tempfile, time
from pathlib import Path
from jobs import Queue

def check(out, name, value):
    out[name] = bool(value)

def submit_worker(queue, barrier, identifier, out):
    barrier.wait(5)
    queue.submit(identifier, {"id": identifier})
    out.put(identifier)

def claim_worker(queue, barrier, out):
    barrier.wait(5)
    out.put(queue.claim("shared", now=10, lease_seconds=5))

def race_worker(action, queue, barrier, token, out):
    barrier.wait(5)
    if action == "complete":
        out.put(("complete", queue.complete("job", token, {"stale": True}, now=15)))
    else:
        out.put(("claim", queue.claim("job", now=15, lease_seconds=5)))

if __name__ == "__main__":
    checks = {}
    import atexit
    atexit.register(lambda: print(json.dumps(checks, sort_keys=True)))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "queue.json"
        q = Queue(path)
        q.submit("seq", {"nested": [1]})
        initial = q.get("seq")
        check(checks, "submit_initial_state", all(initial.get(key) == value for key, value in {
            "status":"pending", "payload":{"nested":[1]}, "attempts":0,
            "result":None, "token":None, "lease_until":None}.items()))
        payload = {"nested":[1]}; q.submit("owned", payload); payload["nested"].append(2)
        snap = q.get("owned"); snap["payload"]["nested"].append(3)
        check(checks, "payload_and_snapshot_independent", q.get("owned")["payload"] == {"nested":[1]})
        try:
            q.submit("owned", {})
            duplicate = False
        except ValueError:
            duplicate = True
        check(checks, "duplicate_rejected", duplicate)
        unknown = []
        for operation in (lambda: q.get("missing"), lambda: q.claim("missing", now=0, lease_seconds=1),
                          lambda: q.complete("missing", "x", None, now=0),
                          lambda: q.retry("missing", "x", now=0)):
            try: operation(); unknown.append(False)
            except KeyError: unknown.append(True)
        check(checks, "unknown_ids_rejected", all(unknown))
        q.submit("invalid", {})
        invalid = []
        for seconds in (0, -1, float("inf"), float("nan")):
            try: q.claim("invalid", now=0, lease_seconds=seconds); invalid.append(False)
            except ValueError: invalid.append(True)
        check(checks, "invalid_leases_rejected", all(invalid) and q.get("invalid")["attempts"] == 0)
        first = q.claim("seq", now=10, lease_seconds=5)
        prior = q.get("seq")
        busy = q.claim("seq", now=14.999, lease_seconds=5)
        check(checks, "busy_claim_does_not_mutate", busy is None and q.get("seq") == prior)
        stale_early = q.complete("seq", "wrong", {"no": 1}, now=11)
        wrong_retry = q.retry("seq", "wrong", now=11)
        expired_retry = q.retry("seq", first, now=15)
        check(checks, "wrong_token_retry_does_not_mutate", wrong_retry is False and q.get("seq") == prior)
        check(checks, "stale_owner_and_exact_expiry_rejected", stale_early is False and expired_retry is False
              and q.get("seq") == prior)
        second = q.claim("seq", now=15, lease_seconds=5)
        stale_completion = q.complete("seq", first, {"stale": True}, now=16)
        check(checks, "expired_claim_replaced_and_old_token_rejected",
              isinstance(second, str) and bool(second) and second != first and stale_completion is False
              and q.get("seq")["token"] == second and q.get("seq")["attempts"] == 2)
        retried = q.retry("seq", second, now=17)
        third = q.claim("seq", now=17, lease_seconds=5)
        result = {"nested": [{"value": 9}]}
        completed = q.complete("seq", third, result, now=19)
        result["nested"][0]["value"] = 0
        done = q.get("seq"); done["result"]["nested"][0]["value"] = 1
        completed_state = q.get("seq")
        reclaimed = q.claim("seq", now=30, lease_seconds=1)
        check(checks, "retry_attempts_and_token_freshness", retried is True and third not in (first, second)
              and q.get("seq")["attempts"] == 3)
        check(checks, "complete_result_and_snapshot_ownership", completed is True and
              completed_state["result"] == {"nested":[{"value":9}]} and
              completed_state["token"] is None and completed_state["lease_until"] is None
              and reclaimed is None and q.get("seq") == completed_state)
        check(checks, "completed_job_is_not_claimable", reclaimed is None)
        q.submit("same-clock", {})
        same_time_first = q.claim("same-clock", now=40, lease_seconds=10)
        same_time_retry = q.retry("same-clock", same_time_first, now=40)
        same_time_second = q.claim("same-clock", now=40, lease_seconds=10)
        check(checks, "token_fresh_at_same_timestamp", same_time_retry is True and
              isinstance(same_time_second, str) and same_time_second != same_time_first)
        check(checks, "reopen_and_other_instance_freshness", Queue(path).get("seq") == q.get("seq"))
        writer_a, writer_b = Queue(path), Queue(path)
        writer_a.submit("visible-a", {"value": "a"})
        writer_b.submit("visible-b", {"value": "b"})
        try:
            sees_both = (writer_a.get("visible-b")["payload"] == {"value":"b"}
                         and writer_b.get("visible-a")["payload"] == {"value":"a"})
        except KeyError:
            sees_both = False
        check(checks, "already_open_instances_reload_before_writes_and_reads", sees_both)

        ctx = mp.get_context("fork")
        barrier = ctx.Barrier(4); out = ctx.Queue()
        opened = [Queue(path) for _ in range(4)]
        workers = [ctx.Process(target=submit_worker, args=(opened[i], barrier, "p"+str(i), out)) for i in range(4)]
        for worker in workers: worker.start()
        deadline = time.monotonic() + 8
        for worker in workers: worker.join(max(0, deadline - time.monotonic()))
        submits_ok = all(not p.is_alive() and p.exitcode == 0 for p in workers)
        for p in workers:
            if p.is_alive(): p.terminate(); p.join(2)
        submitted = [out.get(timeout=1) for _ in range(4)] if submits_ok else []
        fresh = Queue(path)
        try:
            distinct_preserved = all(fresh.get("p"+str(i))["payload"] == {"id":"p"+str(i)} for i in range(4))
        except KeyError:
            distinct_preserved = False
        check(checks, "multiprocess_distinct_submissions_no_lost_updates", submits_ok and len(set(submitted)) == 4
              and distinct_preserved)

        fresh.submit("shared", {})
        barrier = ctx.Barrier(4); out = ctx.Queue(); opened = [Queue(path) for _ in range(4)]
        workers = [ctx.Process(target=claim_worker, args=(opened[i], barrier, out)) for i in range(4)]
        for worker in workers: worker.start()
        deadline = time.monotonic() + 8
        for worker in workers: worker.join(max(0, deadline - time.monotonic()))
        claims_ok = all(not p.is_alive() and p.exitcode == 0 for p in workers)
        for p in workers:
            if p.is_alive(): p.terminate(); p.join(2)
        tokens = [out.get(timeout=1) for _ in range(4)] if claims_ok else []
        try:
            shared_attempts = Queue(path).get("shared")["attempts"]
        except KeyError:
            shared_attempts = -1
        check(checks, "multiprocess_same_id_single_claim", claims_ok and sum(token is not None for token in tokens) == 1
              and shared_attempts == 1)

        fresh.submit("job", {}); owner = fresh.claim("job", now=10, lease_seconds=5)
        barrier = ctx.Barrier(2); out = ctx.Queue(); opened = Queue(path), Queue(path)
        workers = [ctx.Process(target=race_worker, args=("complete", opened[0], barrier, owner, out)),
                   ctx.Process(target=race_worker, args=("claim", opened[1], barrier, owner, out))]
        for worker in workers: worker.start()
        deadline = time.monotonic() + 8
        for worker in workers: worker.join(max(0, deadline - time.monotonic()))
        race_ok = all(not p.is_alive() and p.exitcode == 0 for p in workers)
        for p in workers:
            if p.is_alive(): p.terminate(); p.join(2)
        results = [out.get(timeout=1) for _ in range(2)] if race_ok else []
        by_name = dict(results)
        state = Queue(path).get("job")
        check(checks, "multiprocess_expiry_completion_reclaim_race", race_ok
              and by_name.get("complete") is False and isinstance(by_name.get("claim"), str)
              and by_name["claim"] != owner and state["status"] == "running"
              and state["token"] == by_name["claim"] and state["attempts"] == 2)
'''


def seed(repo):
    seed_repo(repo, {'jobs.py': JOBS, 'storage.py': STORAGE, 'test_jobs.py': TESTS,
                     'AGENTS.md': 'Use Python standard-library code. Do not commit or publish.\n'})


def grade(repo, before):
    try:
        _, output, _, _ = bounded_process([sys.executable, '-c', PROCESS_CHECKS], repo, 35)
        records = [line for line in output.splitlines() if line.startswith('{')]
        parsed = json.loads(records[-1]) if records else {}
        checks = parsed if isinstance(parsed, dict) else {}
    except (ValueError, OSError):
        checks = {}
    check_names = (
        'submit_initial_state', 'payload_and_snapshot_independent', 'duplicate_rejected', 'unknown_ids_rejected',
        'invalid_leases_rejected', 'busy_claim_does_not_mutate', 'wrong_token_retry_does_not_mutate',
        'stale_owner_and_exact_expiry_rejected',
        'expired_claim_replaced_and_old_token_rejected', 'retry_attempts_and_token_freshness',
        'complete_result_and_snapshot_ownership', 'completed_job_is_not_claimable',
        'token_fresh_at_same_timestamp', 'reopen_and_other_instance_freshness',
        'already_open_instances_reload_before_writes_and_reads',
        'multiprocess_distinct_submissions_no_lost_updates',
        'multiprocess_same_id_single_claim', 'multiprocess_expiry_completion_reclaim_race',
    )
    checks = {name: checks.get(name) is True for name in check_names}
    try:
        code, _, _, timed_out = bounded_process([sys.executable, '-m', 'unittest', '-v'], repo, 30)
        checks['public_suite_pass'] = code == 0 and not timed_out
    except OSError:
        checks['public_suite_pass'] = False
    after = file_hashes(repo)
    changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
    allowed = {'jobs.py', 'storage.py', 'test_regressions.py'}
    scope = (not (set(changed) - allowed) and after.get('test_jobs.py') == before.get('test_jobs.py')
             and not any(path.is_symlink() for path in repo.rglob('*')))
    return {'correct': all(checks.values()) and scope, 'functional_pass': all(checks.values()),
            'scope_pass': scope, 'checks': checks, 'changed_files': changed}
