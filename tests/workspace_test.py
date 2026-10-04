"""Workspace selection through the CLI, real Git and the native child fixture."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'lib/python'))
from user_input import record_text


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
        record_text(self.session, 'Continue the requested work and preserve the selected checkout.',
                    source='fixture')
        self.log = self.directory / 'native.jsonl'
        executable = self.directory / 'codex'
        executable.write_text('#!/bin/sh\nexec python3 ' + shlex.quote(str(ROOT / 'tests/native_child_fixture.py')) + ' codex "$@"\n')
        executable.chmod(0o755)
        self.env = {**os.environ, 'CEREBRO_HOME': str(self.home), 'CEREBRO_SESSION_ID': self.session.name,
                    'CEREBRO_BACKEND': 'codex', 'CEREBRO_CODEX_CMD': str(executable),
                    'CEREBRO_JEV_ENABLED': '0', 'CEREBRO_MODEL': 'implementor', 'CEREBRO_REVIEW_MODEL': 'reviewer', 'CEREBRO_PAIR_IDLE': '0',
                    'NATIVE_FIXTURE_LOG': str(self.log), 'NATIVE_FIXTURE_SID': 'workspace-child'}
        self.env.pop('CEREBRO_RESUME_BACKEND', None)
        self.env.pop('CEREBRO_SESSION_DIR', None)
        self.fixture_config = self.directory / 'task-fixture.json'
        self.fixture_config.write_text(json.dumps({'acceptance': ['Preserve checkout'], 'execute': {}, 'review': {}}))
        self.env['TASK_FIXTURE_CONFIG'] = str(self.fixture_config)

    def git(self, *args, repo=None):
        return subprocess.check_output(['git', '-C', str(repo or self.repo), *args], text=True).strip()

    def cli(self, *args, ok=True, env=None, packet=None):
        result = subprocess.run([str(ROOT / 'bin/cerebro'), *map(str, args)], env=env or self.env,
                                input=json.dumps(packet) if packet else None, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode == 0, ok, result.stderr + result.stdout)
        return result

    def execute(self, *args, **kwargs):
        packet = {'goal': 'Preserve checkout', 'task': 'Continue the requested work.',
                  'repo': str(self.repo), 'base': 'main', 'acceptance': ['Preserve checkout']}
        args = iter(args)
        for flag in args:
            if flag == '--worktree':
                packet['worktree'] = True
            else:
                packet[flag.removeprefix('--')] = next(args)
        return self.cli('execute', packet=packet, **kwargs)

    def record(self):
        return next(row for row in json.loads((self.session / 'child-sessions.json').read_text()).values() if row.get('role') == 'execute')

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
        self.fixture_config.write_text(json.dumps({'acceptance': ['Preserve checkout'], 'execute': {'status': 'question'}, 'review': {}}))
        outcome = self.execute('--worktree', '--branch', 'feat/isolated')
        record = self.record()
        workspace = Path(record['repo'])
        self.assertNotEqual(workspace, self.repo)
        self.assertTrue(record['workspace']['created_worktree'])
        self.assertEqual(self.git('branch', '--show-current'), 'main')
        self.assertEqual(self.git('branch', '--show-current', repo=workspace), 'feat/isolated')
        self.assertEqual((workspace / 'file.txt').read_text(), 'original\n')
        self.log.write_text('')
        self.fixture_config.write_text(json.dumps({'acceptance': ['Preserve checkout'], 'execute': {}, 'review': {}}))
        self.cli('answer', json.loads(outcome.stdout)['task_id'], 'Continue here.')
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
        self.cli('execute', packet={'goal': 'Preserve checkout', 'task': 'Continue here.', 'acceptance': ['Preserve checkout'], 'repo': str(elsewhere), 'base': 'main', 'branch': 'feat/occupied'})
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
