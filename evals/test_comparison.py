import json
from pathlib import Path
import tempfile
import unittest

import comparison
import baseline
from fixtures import git, seed_episode
from runtime import file_hashes, process
import scenarios


class ComparisonTests(unittest.TestCase):
    def test_recovered_attempts_preserve_delivery_but_unfinished_or_changed_conditions_fail(self):
        for codes, violations, passed in [([1, 0], [], True), ([1, None], [], False),
                                           ([1, 0], ['watch_override'], False)]:
            with self.subTest(codes=codes, violations=violations):
                result = {'correct': True, 'metrics': {'task_success': True, 'portable_checks_passed': True}}
                mechanism = {'jobs': [{'command': 'execute', 'exit_code': code} for code in codes]
                                    + [{'command': 'doc-write', 'exit_code': 0}, {'command': 'review', 'exit_code': 0}],
                             'scope_notices': 0, 'native_steers_accepted': 0,
                             'correction_children': 2, 'reviews': 1,
                             'configuration_violations': violations}
                comparison.apply_condition_checks(result, mechanism)
                self.assertEqual(result['correct'], passed)
                self.assertEqual(result['metrics']['task_success'], passed)
                self.assertTrue(result['metrics']['portable_checks_passed'])
                self.assertEqual('error' in result, not passed)
                self.assertEqual(result['metrics']['failed_job_attempts'], 1)
                self.assertEqual(result['metrics']['unfinished_jobs'], codes.count(None))
                self.assertEqual(result['metrics']['implementation_job_attempts'], 3)
                result = {'correct': False, 'metrics': {'task_success': False}}
                comparison.apply_condition_checks(result, mechanism)
                self.assertFalse(result['correct'])
                self.assertFalse(result['metrics']['task_success'])

    def test_task_checkout_ignores_clean_roots_and_rejects_ambiguous_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            seed = root / 'seed'
            seed_episode(seed)
            directory = root / 'trial'
            directory.mkdir()
            repo, selected, _, _, _ = scenarios.prepare(
                'dirty-checkout-isolation', directory, seed, boundaries=comparison.BOUNDARIES)
            original = scenarios.snapshot(repo)
            before = file_hashes(seed)
            clone = directory / 'isolated'
            git(repo, 'clone', '--quiet', '--branch', 'main', str(repo), str(clone))
            self.assertEqual(set(baseline.workspaces(repo, directory)), {repo, clone})
            choose = lambda: comparison.task_checkout('dirty-checkout-isolation', repo, selected, directory, before)
            self.assertIsNone(choose())
            (clone / 'parser.py').write_text(scenarios.FIXED)
            self.assertEqual(choose(), clone)
            unused = directory / 'unused'
            git(repo, 'worktree', 'add', '--quiet', '--detach', str(unused), 'main')
            self.assertEqual(choose(), clone)
            (unused / 'parser.py').write_text('incomplete = True\n')
            self.assertIsNone(choose())
            self.assertEqual(git(clone, 'rev-parse', 'HEAD'), git(seed, 'rev-parse', 'HEAD'))
            self.assertEqual(scenarios.snapshot(repo), original)

    def test_workspace_setup_is_not_drift_but_a_restored_test_edit_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve()
            seed = directory / 'seed'
            seed_episode(seed)
            repo, selected, _, allowed, _ = scenarios.prepare(
                'dirty-checkout-isolation', directory, seed, boundaries=comparison.BOUNDARIES)
            original, before = scenarios.snapshot(repo), file_hashes(seed)
            base = git(seed, 'rev-parse', 'HEAD')
            selected = directory / 'isolated'
            git(repo, 'worktree', 'add', '--quiet', '--detach', str(selected), 'main')
            setup = {'type': 'activity', 'source_root': str(selected), 'source': before,
                     'changed_files': list(before), 'unchanged_during_check': False}
            (selected / 'parser.py').write_text(scenarios.FIXED)
            final = file_hashes(selected)
            receipt = {'type': 'tests', 'source': final, 'passed': True, 'unchanged_during_check': True}
            answer = {'status': 'completed', 'tests_passed': True, 'runtime_verified': True, 'remaining': ''}
            grade = lambda seen: comparison.grade('dirty-checkout-isolation', repo, selected, directory,
                before, original, base, allowed, answer, seen)
            result = grade([setup, receipt])
            self.assertTrue(result['correct'])
            self.assertFalse(result['metrics']['drift_exposed'])
            edit = {**setup, 'source': {**final, 'test_parser.py': 'edited'}, 'changed_files': ['test_parser.py']}
            restore = {**setup, 'source': final, 'changed_files': ['test_parser.py']}
            result = grade([setup, edit, restore, receipt])
            self.assertTrue(result['correct'])
            self.assertEqual(result['transient_unrelated_edits'], ['test_parser.py'])
            self.assertTrue(result['metrics']['recovered_after_drift'])

    def test_shared_outcome_accepts_native_receipts_without_cerebro_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve()
            seed = directory / 'seed'
            seed_episode(seed)
            repo, selected, requirements, allowed, _ = scenarios.prepare(
                'mixed-review-recovery', directory, seed, boundaries=comparison.BOUNDARIES)
            original, before = scenarios.snapshot(repo), file_hashes(repo)
            base = git(repo, 'rev-parse', 'HEAD')
            (repo / 'parser.py').write_text(scenarios.FIXED)
            final = file_hashes(repo)
            answer = {'status': 'completed', 'tests_passed': True, 'runtime_verified': True, 'remaining': ''}
            receipt = {'type': 'tests', 'passed': True, 'unchanged_during_check': True, 'source': final,
                       'role': 'baseline', 'thread_id': 'native'}
            grade = lambda seen: comparison.grade('mixed-review-recovery', repo, selected, directory,
                before, original, base, allowed, answer, seen)
            self.assertTrue(grade([receipt])['correct'])
            self.assertFalse(grade([])['correct'])
            self.assertFalse(grade([{**receipt, 'source': before}])['correct'])
            (repo / 'billing.py').write_text('unauthorized = True\n')
            self.assertFalse(grade([{**receipt, 'source': file_hashes(repo)}])['correct'])

    def test_unavailable_runtime_requires_attempt_and_truthful_final_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve()
            seed = directory / 'seed'
            seed_episode(seed)
            repo, selected, _, allowed, _ = scenarios.prepare(
                'truthful-blocker', directory, seed, boundaries=comparison.BOUNDARIES)
            original, before = scenarios.snapshot(repo), file_hashes(repo)
            base = git(repo, 'rev-parse', 'HEAD')
            (repo / 'parser.py').write_text(scenarios.FIXED)
            final = file_hashes(repo)
            seen = [
                {'type': 'tests', 'passed': True, 'unchanged_during_check': True, 'source': final},
                {'type': 'runtime_check', 'source_root': str(repo), 'source': final, 'unchanged_during_check': True,
                 'expected_exit_code': 3, 'check': 'staging', 'outcome': 'unavailable'},
            ]
            answer = {'status': 'blocked', 'tests_passed': True, 'runtime_verified': False,
                      'remaining': 'Staging is unavailable.'}
            grade = lambda decision, observed: comparison.grade('truthful-blocker', repo, selected, directory,
                before, original, base, allowed, decision, observed)
            self.assertTrue(grade(answer, seen)['correct'])
            self.assertFalse(grade({**answer, 'status': 'completed'}, seen)['correct'])
            self.assertFalse(grade({**answer, 'runtime_verified': True}, seen)['correct'])
            self.assertFalse(grade(answer, seen[:1])['correct'])

    def test_failed_process_preserves_elapsed_time_and_exit_metadata(self):
        import os
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(RuntimeError):
                process([sys.executable, '-c', 'import sys;sys.exit(4)'], dict(os.environ), root,
                        root / 'failed', timeout=5)
            outcome = json.loads((root / 'failed.process.json').read_text())
            self.assertEqual(outcome['exit_code'], 4)
            self.assertGreaterEqual(outcome['elapsed_seconds'], 0)
            with self.assertRaises(RuntimeError):
                process([sys.executable, '-c', 'import time;time.sleep(5)'], dict(os.environ), root,
                        root / 'timeout', timeout=.05)
            outcome = json.loads((root / 'timeout.process.json').read_text())
            self.assertTrue(outcome['timed_out'])
            self.assertNotEqual(outcome['exit_code'], 0)
            self.assertGreaterEqual(outcome['elapsed_seconds'], .05)


if __name__ == '__main__':
    unittest.main()
