"""Disposable task fixtures and behavior checks independent of agent-written tests."""

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys


PARSER_TESTS = '''import hashlib
import json
import os
from pathlib import Path
import time
import unittest
import uuid

_run_id = uuid.uuid4().hex
_results = []

def receipt(phase):
    journal = os.environ.get("CEREBRO_EVAL_TEST_JOURNAL")
    if not journal:
        return
    root = Path(__file__).resolve().parent
    files = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in root.rglob("*") if p.is_file() and not p.is_symlink()
             and not any(part in (".git", "__pycache__") for part in p.relative_to(root).parts)}
    record = {"run_id": _run_id, "phase": phase, "source": files, "time": time.time(),
              "cwd": str(Path.cwd()), "source_root": str(root),
              "worker_id": os.environ["CEREBRO_EVAL_WORKER_ID"],
              "role": os.environ["CEREBRO_CHILD_ROLE"], "tests": list(_results)}
    descriptor = os.open(journal, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "a") as output:
        output.write(json.dumps(record) + "\\n")

receipt("before")
from parser import parse_row

class ParserTests(unittest.TestCase):
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

    def test_empty(self):
        self.assertEqual(parse_row(""), [])

    def test_cells(self):
        self.assertEqual(parse_row("a,b,"), ["a", "b", ""])

    def test_quoted_comma(self):
        self.assertEqual(parse_row('a,"b,c",d'), ["a", "b,c", "d"])

if __name__ == "__main__":
    unittest.main()
'''

def file_hashes(repo):
    return {str(path.relative_to(repo)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in repo.rglob('*') if path.is_file() and not path.is_symlink()
            and not any(part in ('.git', '__pycache__') for part in path.relative_to(repo).parts)}


def write_json(path, value):
    temporary = path.with_name('.' + path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.PIPE, text=True).strip()


def seed_repo(repo, files):
    repo.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(repo, 'init', '-qb', 'main')
    git(repo, 'add', '.')
    git(repo, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@localhost',
        '-c', 'commit.gpgsign=false', 'commit', '-qm', 'test: seed disposable eval fixture')


def bounded_process(command, repo, timeout):
    process = subprocess.Popen(command, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
    return process.returncode, stdout, stderr, timed_out


def package_tests(imports, name, methods):
    """Supplied tests in tests/ whose receipts hash the repository one level up."""
    start = PARSER_TESTS.index('    def test_empty(')
    end = PARSER_TESTS.index('if __name__')
    return (PARSER_TESTS[:start]
            .replace('root = Path(__file__).resolve().parent\n', 'root = Path(__file__).resolve().parent.parent\n')
            .replace('from parser import parse_row\n', imports)
            .replace('class ParserTests', 'class ' + name)
            + methods + '\n' + PARSER_TESTS[end:])


# Each hidden check runs under its own alarm and the cumulative result is flushed after every
# check, so a hanging or crashing candidate only loses the checks it actually breaks. Candidate
# output goes to stderr so it cannot impersonate a result line.
CHECK_HARNESS = r'''import json, os, signal, sys

results = os.fdopen(os.dup(1), "w")
os.dup2(2, 1)
LIMIT = float(sys.argv[1])
checks = {}


class CheckTimeout(BaseException):
    pass


def timeout(signum, frame):
    raise CheckTimeout()


signal.signal(signal.SIGALRM, timeout)


def check(function):
    signal.setitimer(signal.ITIMER_REAL, LIMIT)
    try:
        checks[function.__name__] = function() is True
    except BaseException:
        checks[function.__name__] = False
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    results.write(json.dumps(checks, sort_keys=True) + "\n")
    results.flush()


def raises(error, action):
    try:
        action()
    except error:
        return True
    except Exception:
        return False
    return False

'''


def grade_hidden(repo, before, script, names, allowed, *, check_seconds, checks_seconds, suite_seconds):
    recorded = {}
    try:
        _, output, _, _ = bounded_process([sys.executable, '-c', CHECK_HARNESS + script, str(check_seconds)],
                                          repo, checks_seconds)
        for line in output.splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                recorded = value
    except OSError:
        pass
    checks = {name: recorded.get(name) is True for name in names}
    try:
        code, _, _, timed_out = bounded_process([sys.executable, '-m', 'unittest', '-v'], repo, suite_seconds)
        checks['public_suite_pass'] = code == 0 and not timed_out
    except OSError:
        checks['public_suite_pass'] = False
    after = file_hashes(repo)
    changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
    scope = not (set(changed) - set(allowed)) and not any(path.is_symlink() for path in repo.rglob('*'))
    return {'correct': all(checks.values()) and scope, 'functional_pass': all(checks.values()),
            'scope_pass': scope, 'checks': checks, 'changed_files': changed}
