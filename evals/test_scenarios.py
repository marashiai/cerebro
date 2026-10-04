"""Negative controls for live-task evidence and single-condition reporting."""

import json
from pathlib import Path
import tempfile
import unittest

from fixtures import seed_episode
from native import shell_commands
import run
import scenarios


class ScenarioTests(unittest.TestCase):
    def test_native_shell_wrapper_is_parsed_without_treating_echo_as_execution(self):
        self.assertEqual(list(shell_commands("/bin/zsh -lc 'python3 -m doctest -v README.md'")),
                         [['python3', '-m', 'doctest', '-v', 'README.md']])
        self.assertEqual(list(shell_commands("/bin/zsh -lc 'echo python3 -m doctest README.md'")),
                         [['echo', 'python3', '-m', 'doctest', 'README.md']])

    def test_documentation_receipt_is_bound_to_requested_file_and_checkout(self):
        repo = Path('/tmp/task')
        item = {'exit_code': 0, 'cwd': str(repo), 'output': '3 passed and 0 failed.\n',
                'command': "/bin/zsh -lc 'python3 -m doctest -v README.md'"}
        self.assertTrue(scenarios.documentation_receipt(item, repo))
        self.assertFalse(scenarios.documentation_receipt(dict(item, cwd='/tmp/other'), repo))
        self.assertFalse(scenarios.documentation_receipt(dict(item, changed_files=['README.md']), repo))
        self.assertFalse(scenarios.documentation_receipt(dict(item, command='python3 -m doctest other.md'), repo))
        self.assertFalse(scenarios.documentation_receipt(dict(item, command='cd /tmp/other && python3 -m doctest README.md'), repo))

    def test_documentation_requires_executable_examples_for_both_features(self):
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            (repo / 'parser.py').write_text(scenarios.FIXED)
            self.assertFalse(scenarios.grade_docs(repo))
            doc = repo / 'README.md'
            doc.write_text('The parser supports quoted commas and doubled quotes.\n')
            self.assertFalse(scenarios.grade_docs(repo))
            doc.write_text('>>> from parser import parse_row\n'
                           '>>> parse_row(\'x,"arbitrary,example",\')\n'
                           "['x', 'arbitrary,example', '']\n"
                           '>>> parse_row(\'"say ""hello""",y\')\n'
                           "['say \"hello\"', 'y']\n")
            self.assertTrue(scenarios.grade_docs(repo))
            doc.write_text(doc.read_text().replace("['x', 'arbitrary,example', '']", "['wrong']"))
            self.assertFalse(scenarios.grade_docs(repo))

    def test_workspace_fixtures_have_real_git_state_to_preserve(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            seed = root / 'seed'
            seed_episode(seed)
            repo, related, *_ = scenarios.prepare('related-branch-reuse', root / 'related-case', seed)
            self.assertNotEqual(repo, related)
            self.assertEqual(scenarios.snapshot(related)['branch'], 'feat/csv-parser')
            self.assertIn('?? user-notes.txt', scenarios.snapshot(repo)['status'])
            repo, selected, *_ = scenarios.prepare('dirty-checkout-isolation', root / 'dirty-case', seed)
            state = scenarios.snapshot(repo)
            self.assertIsNone(selected)
            self.assertEqual(state['branch'], 'feat/user-wip')
            self.assertIn('parser.py', state['status'])
            self.assertIn('?? user-draft.txt', state['status'])
            self.assertIn('user-staged.txt', state['index'])

    def test_report_keeps_single_conditions_out_of_missing_pair_counts(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            rows = [{'case': 'single', 'kind': 'delivery', 'mode': 'protocol', 'paired': False,
                     'correct': False, 'checks': {'required_review': False}, 'repeat': 0,
                     'arm': 'protocol', 'artifacts': str(root / 'single'), 'elapsed_seconds': 1.0,
                     'expected_arms': ['protocol'], 'settings': {**run.resolve_config({}),
                         'jev_model': 'jev-latest', 'jev_confidence': .8, 'timeout': 900,
                         'jev_endpoint': 'https://unused.invalid', 'jobs_requested': 1, 'jobs_effective': 1,
                         'timing_mode': 'isolated', 'baseline_roles': {'bare_implementor': 'implementation', 'bare_supervisor': 'supervisor'}}}]
            manifest = {}
            run.report(root, rows, manifest)
            result = json.loads((root / 'results.json').read_text())
            self.assertEqual(result['exit_status'], 1)
            cohort = json.loads((root / 'aggregate.json').read_text())['cohorts'][0]
            self.assertEqual(cohort['incomplete_units'], [])
            self.assertEqual(cohort['arms']['protocol']['trials'], 1)
            self.assertIn('required_review', (root / 'report.md').read_text())

    def test_catalogue_has_all_requested_areas_without_duplicate_ids(self):
        entries = run.catalogue()
        self.assertEqual(len(entries), len({entry['id'] for entry in entries}))
        self.assertEqual({entry['suite'] for entry in entries}, {'comparison', 'protocol'})
        self.assertIn('comparison-persisted-job-restart', {entry['id'] for entry in entries})
        self.assertTrue(all(not entry['paired'] for entry in entries if entry['mode'] == 'protocol'))


if __name__ == '__main__':
    unittest.main()
