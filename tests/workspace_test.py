"""Workspace selection through the CLI, real Git and the native child fixture."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cerebro-workspace-')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.repo = self.directory / 'repo'
        self.repo.mkdir()
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Workspace Test')
        self.git('config', 'user.email', 'workspace@example.com')
        (self.repo / 'file.txt').write_text('original\n')
        self.git('add', 'file.txt')
        self.git('commit', '-qm', 'initial')
        self.head = self.git('rev-parse', 'HEAD')
        self.home = self.directory / 'home'
        self.session = self.home / 'sessions' / 'workspace-test'
        (self.session / 'children').mkdir(parents=True)
        (self.session / 'plans').mkdir()
        (self.session / 'metadata.json').write_text('{"backend":"codex"}')
        (self.session / 'transcript.jsonl').touch()
        self.log = self.directory / 'native.jsonl'
        executable = self.directory / 'codex'
        executable.write_text('#!/bin/sh\nexec python3 ' + shlex.quote(str(ROOT / 'tests/native_child_fixture.py')) + ' codex "$@"\n')
        executable.chmod(0o755)
        self.env = {**os.environ, 'CEREBRO_HOME': str(self.home), 'CEREBRO_SESSION_ID': self.session.name,
                    'CEREBRO_BACKEND': 'codex', 'CEREBRO_CODEX_CMD': str(executable),
                    'CEREBRO_JEV_ENABLED': '0', 'CEREBRO_MODEL': '', 'CEREBRO_PAIR_IDLE': '0',
                    'NATIVE_FIXTURE_LOG': str(self.log), 'NATIVE_FIXTURE_SID': 'workspace-child'}
        self.env.pop('CEREBRO_RESUME_BACKEND', None)

    def git(self, *args, repo=None):
        return subprocess.check_output(['git', '-C', str(repo or self.repo), *args], text=True).strip()

    def cli(self, *args, ok=True, env=None):
        result = subprocess.run([str(ROOT / 'bin/cerebro'), *map(str, args)], env=env or self.env,
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode == 0, ok, result.stderr + result.stdout)
        return result

    def execute(self, *args, **kwargs):
        return self.cli('execute', self.repo, '--prompt', 'Continue the requested work.', *args, **kwargs)

    def record(self):
        return next(iter(json.loads((self.session / 'child-sessions.json').read_text()).values()))

    def test_default_reuses_dirty_related_checkout(self):
        self.git('switch', '-qc', 'feat/related')
        (self.repo / 'file.txt').write_text('unfinished related work\n')
        (self.repo / 'user.txt').write_text('preserve me')
        self.execute()
        self.assertEqual(self.record()['repo'], str(self.repo))
        starts = [json.loads(line) for line in self.log.read_text().splitlines()]
        start = next(row for row in starts if row.get('method') == 'thread/start')
        self.assertEqual(start['params']['cwd'], str(self.repo))
        self.assertEqual(self.git('branch', '--show-current'), 'feat/related')
        self.assertEqual((self.repo / 'file.txt').read_text(), 'unfinished related work\n')
        self.assertEqual((self.repo / 'user.txt').read_text(), 'preserve me')
        self.assertEqual(self.git('worktree', 'list', '--porcelain').count('worktree '), 1)

    def test_clean_default_branch_does_not_require_new_branch(self):
        self.execute()
        self.assertEqual(self.git('branch', '--show-current'), 'main')
        self.assertEqual(self.record()['repo'], str(self.repo))

    def test_base_does_not_change_current_checkout_or_its_starting_commit(self):
        self.git('commit', '-q', '--allow-empty', '-m', 'later current work')
        current = self.git('rev-parse', 'HEAD')
        self.execute('--base', self.head)
        self.assertEqual(self.record()['workspace']['start_head'], current)
        self.assertEqual(self.git('rev-parse', 'HEAD'), current)

    def test_existing_branch_keeps_its_commits_despite_base(self):
        self.git('switch', '-qc', 'feat/related')
        self.git('commit', '-q', '--allow-empty', '-m', 'existing work')
        existing = self.git('rev-parse', 'HEAD')
        self.git('switch', '-q', 'main')
        self.execute('--branch', 'feat/related', '--base', 'main')
        self.assertEqual(self.git('branch', '--show-current'), 'feat/related')
        self.assertEqual(self.git('rev-parse', 'HEAD'), existing)
        self.assertFalse(self.record()['workspace']['created_branch'])

    def test_same_base_and_branch_can_continue_existing_branch(self):
        self.execute('--branch', 'main', '--base', 'main')
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.head)

    def test_new_branch_uses_exact_base_and_preserves_dirty_files(self):
        self.git('branch', 'feat/base')
        self.git('commit', '-q', '--allow-empty', '-m', 'later main work')
        (self.repo / 'user.txt').write_text('preserve me')
        self.execute('--branch', 'fix/new', '--base', 'feat/base')
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.head)
        self.assertEqual(self.git('branch', '--show-current'), 'fix/new')
        self.assertEqual((self.repo / 'user.txt').read_text(), 'preserve me')
        self.assertTrue(self.record()['workspace']['created_branch'])

    def test_explicit_worktree_and_answer_use_selected_checkout(self):
        (self.repo / 'file.txt').write_text('unrelated local work\n')
        self.execute('--worktree', '--branch', 'feat/isolated')
        record = self.record()
        workspace = Path(record['repo'])
        self.assertNotEqual(workspace, self.repo)
        self.assertTrue(record['workspace']['created_worktree'])
        self.assertEqual(self.git('branch', '--show-current'), 'main')
        self.assertEqual(self.git('branch', '--show-current', repo=workspace), 'feat/isolated')
        self.assertEqual((workspace / 'file.txt').read_text(), 'original\n')
        self.log.write_text('')
        self.cli('answer', record['id'], 'Continue here.')
        starts = [json.loads(line) for line in self.log.read_text().splitlines()]
        resumed = next(row for row in starts if row.get('method') == 'thread/resume')
        self.assertEqual(resumed['params']['cwd'], str(workspace))
        self.assertEqual((self.repo / 'file.txt').read_text(), 'unrelated local work\n')

    def test_missing_explicit_base_does_not_fall_back(self):
        self.execute('--worktree', '--branch', 'feat/missing', '--base', 'missing-ref', ok=False)
        self.assertEqual(self.git('branch', '--show-current'), 'main')
        self.assertEqual(self.git('worktree', 'list', '--porcelain').count('worktree '), 1)

    def test_branch_in_other_checkout_is_not_forced_or_duplicated(self):
        elsewhere = self.directory / 'elsewhere'
        self.git('worktree', 'add', '-qb', 'feat/occupied', str(elsewhere))
        self.execute('--branch', 'feat/occupied', ok=False)
        self.assertEqual(self.git('branch', '--show-current'), 'main')
        self.cli('execute', elsewhere, '--prompt', 'Continue here.', '--branch', 'feat/occupied')
        self.assertEqual(self.git('branch', '--show-current', repo=elsewhere), 'feat/occupied')

    def test_remote_tracking_branch_is_continued_from_its_own_head(self):
        self.git('commit', '-q', '--allow-empty', '-m', 'remote work')
        remote_head = self.git('rev-parse', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/feat/remote', remote_head)
        self.git('reset', '--hard', self.head)
        origin = self.directory / 'origin.git'
        subprocess.run(['git', 'init', '--bare', '-q', str(origin)], check=True)
        self.git('remote', 'add', 'origin', str(origin))
        self.execute('--branch', 'feat/remote', '--base', 'main')
        self.assertEqual(self.git('rev-parse', 'HEAD'), remote_head)
        self.assertFalse(self.record()['workspace']['created_branch'])

    def test_failed_child_resumes_only_in_recorded_workspace(self):
        self.execute('--worktree', ok=False, env={**self.env, 'NATIVE_FIXTURE_MODE': 'failure'})
        workspace = Path(self.record()['repo'])
        self.log.write_text('')
        self.execute('--worktree')
        resumed = next(json.loads(line) for line in self.log.read_text().splitlines()
                       if json.loads(line).get('method') == 'thread/resume')
        self.assertEqual(resumed['params']['cwd'], str(workspace))
        self.assertEqual(resumed['params']['threadId'], 'workspace-child')

    def test_cancelled_child_resumes_after_creating_a_branch(self):
        for continuation in ('execute', 'answer'):
            with self.subTest(continuation=continuation):
                self.cli('detach', '--output', self.session / (continuation + '.out'), '--',
                         'execute', self.repo, '--prompt', 'Continue the requested work.', '--worktree',
                         env={**self.env, 'NATIVE_FIXTURE_DELAY': '60'})
                job_file = max((self.session / 'detached-jobs').glob('*.json'), key=lambda path: path.stat().st_mtime_ns)
                job = json.loads(job_file.read_text())
                try:
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        store = self.session / 'child-sessions.json'
                        if store.exists() and self.record().get('id') and self.record().get('status') == 'running':
                            break
                        time.sleep(0.02)
                    record = self.record()
                    self.assertEqual(record['id'], 'workspace-child')
                    workspace = Path(record['repo'])
                    branch = 'feat/retained-' + continuation
                    self.git('switch', '-qc', branch, repo=workspace)
                    (workspace / 'retained.txt').write_text(continuation)
                    self.cli('cancel', job['id'])
                    self.assertEqual(json.loads(self.cli('wait', job['id'], ok=False).stdout)['exit_code'], 130)
                    self.log.write_text('')
                    if continuation == 'execute':
                        self.execute('--worktree')
                    else:
                        self.cli('answer', record['id'], 'Continue the retained task.')
                    resumed = next(json.loads(line) for line in self.log.read_text().splitlines()
                                   if json.loads(line).get('method') == 'thread/resume')
                    self.assertEqual(resumed['params']['cwd'], str(workspace))
                    self.assertEqual(resumed['params']['threadId'], record['id'])
                    self.assertEqual(self.record()['workspace']['branch'], branch)
                    self.assertEqual((workspace / 'retained.txt').read_text(), continuation)
                finally:
                    if Path(job['status']).read_text().strip() in ('starting', 'running'):
                        self.cli('cancel', job['id'])

    def test_missing_resume_checkout_is_not_recreated(self):
        self.execute('--worktree', ok=False, env={**self.env, 'NATIVE_FIXTURE_MODE': 'failure'})
        workspace = Path(self.record()['repo'])
        self.git('worktree', 'remove', str(workspace))
        self.log.write_text('')
        self.execute('--worktree', ok=False)
        self.assertFalse(workspace.exists())
        self.assertEqual(self.log.read_text(), '')
        self.assertEqual(self.record()['id'], 'workspace-child')

    def test_replacement_repository_is_not_resumed_or_cleaned(self):
        self.execute('--worktree', ok=False, env={**self.env, 'NATIVE_FIXTURE_MODE': 'failure'})
        workspace = Path(self.record()['repo'])
        self.git('worktree', 'remove', str(workspace))
        workspace.mkdir()
        self.git('init', '-q', '-b', 'main', repo=workspace)
        (workspace / 'precious.txt').write_text('different repository')
        self.log.write_text('')
        self.execute('--worktree', ok=False)
        self.cli('answer', 'workspace-child', 'Continue.', ok=False)
        self.assertEqual(self.log.read_text(), '')
        self.cli('worktrees', 'cleanup')
        self.assertEqual((workspace / 'precious.txt').read_text(), 'different repository')

    def test_conflicting_branch_switch_preserves_current_changes(self):
        self.git('switch', '-qc', 'feat/other')
        (self.repo / 'file.txt').write_text('other branch\n')
        self.git('commit', '-qam', 'other branch work')
        self.git('switch', '-q', 'main')
        (self.repo / 'file.txt').write_text('unfinished current work\n')
        self.execute('--branch', 'feat/other', ok=False)
        self.assertEqual(self.git('branch', '--show-current'), 'main')
        self.assertEqual((self.repo / 'file.txt').read_text(), 'unfinished current work\n')
        self.assertFalse(self.log.exists())

    def test_restart_preserves_checkout_and_retires_conversation(self):
        for isolated in (False, True):
            with self.subTest(isolated=isolated):
                branch = 'feat/restart-' + str(int(isolated))
                args = ['--worktree'] if isolated else []
                proc = subprocess.Popen([str(ROOT / 'bin/cerebro'), 'execute', str(self.repo),
                                         '--prompt', 'Restart this task.', '--branch', branch, '--pair', *args],
                                        env={**self.env, 'CEREBRO_PAIR_IDLE': '15'}, text=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        fifos = list((self.session / 'children').glob('*.steer.fifo'))
                        store = self.session / 'child-sessions.json'
                        rows = json.loads(store.read_text()).values() if store.exists() else []
                        records = [row for row in rows if row.get('id') == 'workspace-child']
                        if fifos and records:
                            break
                        self.assertIsNone(proc.poll(), proc.communicate() if proc.poll() is not None else '')
                        time.sleep(0.02)
                    self.assertTrue(fifos and records)
                    workspace = Path(records[0]['repo'])
                    (workspace / 'retain.txt').write_text('unfinished work')
                    self.cli('restart', fifos[0], 'Continue with a corrected plan.')
                    stdout, stderr = proc.communicate(timeout=10)
                    self.assertEqual(proc.returncode, 0, stderr)
                    self.assertIn(str(workspace), stdout)
                    self.assertEqual((workspace / 'retain.txt').read_text(), 'unfinished work')
                    self.assertEqual(self.git('branch', '--show-current', repo=workspace), branch)
                    self.cli('answer', 'workspace-child', 'Must not resume a retired conversation.', ok=False)
                    self.cli('execute', workspace, '--prompt', 'Corrected task.')
                    self.assertEqual(self.git('branch', '--show-current', repo=workspace), branch)
                    # Leave no completed ID to confuse the next fixture's fixed native ID.
                    state = json.loads(store.read_text())
                    for row in state.values():
                        row.pop('id', None)
                    store.write_text(json.dumps(state))
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.communicate()

    def test_store_failure_is_reported(self):
        import sys
        sys.path.insert(0, str(ROOT / 'lib/python'))
        from child_store_lib import store_upsert
        store = self.session / 'child-sessions.json'
        store.write_text('{}')
        with patch('child_store_lib.os.replace', side_effect=OSError('fixture disk failure')):
            with self.assertRaisesRegex(OSError, 'fixture disk failure'):
                store_upsert(str(store), 'task', {'workspace': {'path': str(self.repo)}})
        self.assertEqual(store.read_text(), '{}')


if __name__ == '__main__':
    unittest.main()
