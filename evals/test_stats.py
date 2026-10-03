"""Offline checks for matched reporting, incomplete usage and public artifacts."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

import publish
import stats


SETTINGS = {'models': {role: 'test-model' for role in stats.ROLES},
            'efforts': {role: 'low' for role in stats.ROLES},
            'jev_model': 'jev-test', 'jev_confidence': .8, 'timeout': 120,
            'jev_endpoint': 'https://private.example.org/private-endpoint'}
PRICES = {'as_of': '2026-10-03', 'source': 'https://example.org/prices',
          'basis': 'Synthetic test rates; not measured provider prices.',
          'models': {'test-model': {'input_per_million': 2, 'cached_input_per_million': .2,
                                   'cache_write_input_per_million': 2.5, 'output_per_million': 8}}}


def trial(arm='bare', case='parser', repeat=0, **changes):
    row = {'case': case, 'kind': 'delivery', 'mode': 'comparison', 'arm': arm,
           'repeat': repeat, 'paired': True, 'expected_arms': ['bare', 'cerebro'],
           'settings': copy.deepcopy(SETTINGS), 'correct': True, 'elapsed_seconds': 10,
           'usage_complete': True,
           'usage_ledger': [{'provider': 'openai', 'role': 'baseline' if arm == 'bare' else 'supervisor',
                             'model': 'test-model', 'input_tokens': 100, 'cached_input_tokens': 20,
                             'cache_write_input_tokens': 10, 'output_tokens': 50, 'complete': True}]}
    row.update(changes)
    return row


def document(*rows):
    return {'manifest': {'secret': '/private/config/token'}, 'trials': list(rows)}


class StatisticsTests(unittest.TestCase):
    def test_only_matched_units_enter_comparison_and_failures_keep_time_cost(self):
        report = stats.summarize(document(trial(), trial('cerebro', error='timeout', elapsed_seconds=30),
                                          trial(case='unmatched', elapsed_seconds=999)), PRICES)
        cohort = report['cohorts'][0]
        self.assertEqual((cohort['matched_units'], cohort['excluded_trials'], cohort['all_errors']), (1, 1, 1))
        self.assertEqual(cohort['arms']['bare']['mean_seconds'], 10)
        failed = cohort['arms']['cerebro']
        self.assertEqual((failed['trials'], failed['passed'], failed['errors']), (1, 0, 1))
        self.assertEqual(failed['mean_seconds'], 30)
        self.assertAlmostEqual(failed['mean_cost_usd'], .000569)
        self.assertEqual(cohort['deltas'][0]['pass_rate_delta_pp'], -100)
        self.assertEqual(cohort['deltas'][0]['mean_seconds_delta'], 20)

    def test_full_model_and_effort_configuration_must_match(self):
        for component, value in [('models', 'another-model'), ('efforts', 'high')]:
            different = trial('cerebro')
            different['settings'][component]['review'] = value
            cohorts = stats.summarize(document(trial(), different))['cohorts']
            self.assertEqual(len(cohorts), 2)
            self.assertEqual(sum(item['matched_units'] for item in cohorts), 0)
            self.assertEqual(sum(item['excluded_trials'] for item in cohorts), 2)

    def test_jev_configuration_and_time_budget_must_also_match(self):
        for key, value in [('jev_model', 'jev-other'), ('jev_confidence', .9), ('timeout', 180),
                           ('jev_endpoint', 'https://different.example.org')]:
            changed = trial('cerebro')
            changed['settings'][key] = value
            cohorts = stats.summarize(document(trial(), changed))['cohorts']
            self.assertEqual(sum(item['matched_units'] for item in cohorts), 0)
            self.assertEqual(len(cohorts), 2)
            self.assertNotIn('https://', json.dumps(cohorts))

    def test_public_model_names_can_use_provider_namespaces_and_version_tags(self):
        row = trial(capability='truthful-blocker')
        row['settings']['models']['baseline'] = 'provider/future-model:release+preview@v2'
        row['settings']['efforts']['baseline'] = 'future-effort'
        public = stats.public_trials(document(row))[0]
        self.assertEqual(public['settings']['models'], row['settings']['models'])
        self.assertEqual(public['capability'], 'truthful-blocker')

    def test_duplicate_and_inconsistent_arm_definitions_are_errors(self):
        with self.assertRaisesRegex(ValueError, 'duplicate arm'):
            stats.summarize(document(trial(), trial()))
        with self.assertRaisesRegex(ValueError, 'inconsistent kind or expected arms'):
            stats.summarize(document(trial(), trial('cerebro', expected_arms=['bare', 'cerebro', 'cerebro_jev'])))

    def test_pairs_and_triplets_are_distinct_cohorts(self):
        triplet = ['bare', 'cerebro', 'cerebro_jev']
        rows = [trial(), trial('cerebro')]
        rows += [trial(arm, 'watched', expected_arms=triplet) for arm in triplet]
        cohorts = stats.summarize(document(*rows))['cohorts']
        self.assertEqual(sorted(len(item['expected_arms']) for item in cohorts), [2, 3])
        self.assertEqual([item['matched_units'] for item in cohorts], [1, 1])
        jev = next(item for item in cohorts if len(item['expected_arms']) == 3)
        self.assertEqual([(item['before'], item['after']) for item in jev['deltas']],
                         [('bare', 'cerebro'), ('bare', 'cerebro_jev'), ('cerebro', 'cerebro_jev')])

    def test_expected_arms_define_pairing_independently_of_runner_dispatch_flag(self):
        report = stats.summarize(document(trial(paired=False), trial('cerebro', paired=False)))
        self.assertEqual(report['cohorts'][0]['matched_units'], 1)
        self.assertTrue(all(row['paired'] for row in report['trials']))

    def test_repetitions_do_not_create_independent_case_samples(self):
        rows = [trial(arm, repeat=repeat) for repeat in range(12) for arm in ('bare', 'cerebro')]
        delta = stats.summarize(document(*rows))['cohorts'][0]['deltas'][0]
        self.assertEqual((delta['pairs'], delta['independent_cases']), (12, 1))
        self.assertTrue(all(value is None for value in delta['cluster_bootstrap_95'].values()))

    def test_cluster_intervals_preserve_direction_and_are_reproducible(self):
        rows = [trial(arm, 'case-%d' % case, correct=arm == 'cerebro' or case % 2 == 0)
                for case in range(stats.BOOTSTRAP_MIN_CASES) for arm in ('bare', 'cerebro')]
        first = stats.summarize(document(*rows))['cohorts'][0]['deltas'][0]
        second = stats.summarize(document(*rows))['cohorts'][0]['deltas'][0]
        self.assertEqual(first, second)
        self.assertEqual((first['pass_rate_delta_pp'], first['improved'], first['tied']), (50, 5, 5))
        low, high = first['cluster_bootstrap_95']['pass_rate_delta_pp']
        self.assertTrue(0 <= low < 50 < high <= 100)

    def test_wilson_single_success_is_not_certainty(self):
        low, high = stats.wilson(1, 1)
        self.assertAlmostEqual(low, .2065493144)
        self.assertAlmostEqual(high, 1)
        self.assertIsNone(stats.wilson(0, 0))

    def test_missing_usage_and_cost_remain_unknown_with_known_partial_tokens(self):
        incomplete = trial('cerebro', usage_complete=False)
        incomplete['usage_ledger'].append({'provider': 'openai', 'role': 'execute', 'model': 'test-model',
                                         'input_tokens': None, 'output_tokens': None, 'complete': False})
        no_usage = trial('cerebro', 'other', usage_complete=False, usage_ledger=[])
        cohort = stats.summarize(document(trial(), incomplete, trial(case='other'), no_usage), PRICES)['cohorts'][0]
        item = cohort['arms']['cerebro']
        self.assertEqual(item['known_tokens']['input_tokens'], 100)
        self.assertEqual(item['usage_complete_trials'], 0)
        self.assertEqual(item['cost_known_trials'], 0)
        self.assertIsNone(item['mean_cost_usd'])
        self.assertIsNone(item['total_cost_usd'])
        self.assertIsNone(cohort['deltas'][0]['mean_cost_usd_delta'])
        missing_worker = next(worker for worker in item['usage_by_role'] if worker['role'] == 'execute')
        self.assertIsNone(missing_worker['known_tokens']['input_tokens'])
        self.assertEqual(missing_worker['token_known_entries']['input_tokens'], 0)

    def test_cache_subdivisions_needed_for_different_rates_but_not_uniform_rates(self):
        row = trial()
        row['usage_ledger'][0]['cached_input_tokens'] = None
        row['usage_ledger'][0]['cache_write_input_tokens'] = None
        public = stats.public_trials(document(row), PRICES)[0]
        self.assertIsNone(public['cost_usd'])
        self.assertTrue(public['usage_complete'])
        uniform = copy.deepcopy(PRICES)
        uniform['models']['test-model'] = dict.fromkeys(stats.PRICE_KEYS, .042)
        uniform['models']['test-model']['output_per_million'] = 0
        public = stats.public_trials(document(row), uniform)[0]
        self.assertAlmostEqual(public['cost_usd'], .0000042)

    def test_missing_price_or_invalid_cache_count_cannot_silently_underbill(self):
        missing = copy.deepcopy(PRICES)
        del missing['models']['test-model']['cache_write_input_per_million']
        self.assertIsNone(stats.public_trials(document(trial()), missing)[0]['cost_usd'])
        row = trial()
        row['usage_ledger'][0]['cache_write_input_tokens'] = 90
        with self.assertRaisesRegex(ValueError, 'exceed input'):
            stats.public_trials(document(row), PRICES)

    def test_public_allowlist_removes_paths_credentials_and_provider_prose(self):
        secret = 'DO-NOT-PUBLISH-private-path-and-token'
        row = trial(error=secret, artifacts='/private/' + secret, decision={'reason': secret},
                    metrics={'observed_recovery': True, 'sensitive': secret, 'nested': {'path': secret}},
                    checks={'behavior': False}, protocol_violations=[secret],
                    transient_unrelated_edits=[secret], usage={'secret': secret})
        row['settings']['jev_api_key'] = secret
        row['usage_ledger'][0]['path'] = secret
        public = stats.public_trials(document(row))[0]
        self.assertNotIn(secret, json.dumps(public))
        self.assertEqual(public['metrics']['protocol_violation_count'], 1)
        self.assertEqual(public['metrics']['transient_unrelated_edit_count'], 1)
        self.assertTrue(public['metrics']['observed_recovery'])
        self.assertTrue(public['error'])
        self.assertFalse(public['correct'])

    def test_malformed_public_fields_are_rejected(self):
        for change in ({'correct': 1}, {'elapsed_seconds': -1}, {'case': '/private/path'},
                       {'metrics': {'time': float('inf')}}, {'checks': {'claim': 'yes'}},
                       {'expected_arms': None}, {'settings': None}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                stats.summarize(document(trial(**change)))

    def test_protocol_and_calibration_results_do_not_enter_product_aggregates(self):
        rows = [trial(), trial('cerebro')]
        rows += [trial('protocol', 'guard', mode='protocol', paired=False, expected_arms=['protocol'], correct=False)]
        rows += [trial(arm, 'review', mode='calibration', kind='review', expected_arms=['without_jev', 'with_jev'])
                 for arm in ('without_jev', 'with_jev')]
        cohorts = stats.summarize(document(*rows))['cohorts']
        self.assertEqual(len(cohorts), 3)
        product = next(item for item in cohorts if item['mode'] == 'comparison')
        self.assertEqual(product['matched_units'], 1)
        self.assertEqual(product['all_errors'], 0)


class PublicationTests(unittest.TestCase):
    def write_run(self, root, rows):
        private = root / 'private'
        private.mkdir()
        (private / 'results.json').write_text(json.dumps(document(*rows)))
        return private

    def test_private_markdown_omits_unrendered_chart_links(self):
        report = stats.summarize(document(trial(), trial('cerebro')), PRICES)
        text = publish.report_markdown(report, charts=False)
        self.assertNotIn('.svg', text)
        self.assertNotIn('.png', text)
        self.assertIn('[Sanitized trial data](trials.json)', text)
        self.assertIn('[Aggregate statistics](aggregate.json)', text)
        self.assertIn('Bare agent | 1/1', text)
        self.assertIn('### Recorded provider and role usage', text)

    def test_real_publication_renders_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = self.write_run(root, [trial(), trial('cerebro', correct=False)])
            output = root / 'results' / 'test'
            readme = root / 'README.md'
            before, after = '# Intro\r\nRetain this.\r\n', '\r\n## Other\r\nRetain too.\r\n'
            readme.write_bytes((before + publish.START + '\nOld overview\n' + publish.END + after).encode())
            path = publish.publish(private, output, PRICES, readme)
            self.assertEqual(path, output / 'report.md')
            self.assertEqual(len(list(output.glob('*.png'))), 2)
            self.assertEqual(len(list(output.glob('*.svg'))), 2)
            for image in output.glob('*.png'):
                self.assertEqual(image.read_bytes()[:8], b'\x89PNG\r\n\x1a\n')
            for image in output.glob('*.svg'):
                self.assertIn('Bare agent', image.read_text())
            published = '\n'.join(path.read_text() for path in output.iterdir() if path.suffix != '.png')
            self.assertNotIn('/private/config/token', published)
            text = readme.read_bytes().decode()
            self.assertTrue(text.startswith(before + publish.START))
            self.assertTrue(text.endswith(publish.END + after))
            self.assertIn('results/test/report.md', text)
            self.assertIn('-100.0 percentage points', text)
            original = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'already exists'):
                publish.publish(private, output, PRICES)
            self.assertEqual(path.read_bytes(), original)

    def test_readme_markers_validated_before_writing_public_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = self.write_run(root, [trial(), trial('cerebro')])
            readme = root / 'README.md'
            readme.write_text('Untouched documentation.\n')
            with self.assertRaisesRegex(ValueError, 'marker pair'):
                publish.publish(private, root / 'results', readme=readme)
            self.assertFalse((root / 'results').exists())
            self.assertEqual(readme.read_text(), 'Untouched documentation.\n')

    def test_provenance_contains_validated_reproduction_evidence_only(self):
        source = {'source_commit': 'a' * 40, 'source_diff_sha256': 'b' * 64, 'corpus_sha256': 'c' * 64,
                  'eval_sources': {'run.py': 'd' * 64, '.gitignore': 'e' * 64}, 'created_at': '2026-10-03T12:00:00+00:00',
                  'native_version': 'codex-cli 0.160.0', 'repetitions': 1, 'seed': 42,
                  'secret_config': '/private/never-copy-this'}
        public = publish.public_provenance(source)
        self.assertNotIn('secret_config', public)
        self.assertEqual(public['eval_sources'], source['eval_sources'])
        for key, value in [('eval_sources', {'/private/path': 'd' * 64}),
                           ('native_version', 'codex-cli 0.160.0\nprivate data'),
                           ('created_at', '2026-10-03T12:00:00')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                publish.public_provenance({key: value})


if __name__ == '__main__':
    unittest.main()
