"""Decision rule and log accounting of the paired evaluation."""

import json
from pathlib import Path
import tempfile
import unittest

import run


def row(case, repeat, arm, delivered, seconds=100.0):
    return {'case': case, 'repeat': repeat, 'arm': arm, 'delivered': delivered, 'elapsed_seconds': seconds,
            'estimated_usd': 0.01, 'nudges': [], 'jev_calls': 0, 'jev_failures': 0}


class DecisionTests(unittest.TestCase):
    def test_sign_test_is_exact_and_one_sided(self):
        self.assertEqual(run.sign_test(0, 0), 1.0)
        self.assertAlmostEqual(run.sign_test(5, 0), 1 / 32)
        self.assertAlmostEqual(run.sign_test(4, 1), 6 / 32)
        self.assertAlmostEqual(run.sign_test(1, 4), 31 / 32)

    def test_better_needs_significant_wins_within_time_budget(self):
        wins = [row('t', i, 'bare', False) for i in range(6)] + [row('t', i, 'jev', True, 110) for i in range(6)]
        self.assertEqual(run.summarize(wins)['decision'], 'better')
        slow = [row('t', i, 'bare', False) for i in range(6)] + [row('t', i, 'jev', True, 130) for i in range(6)]
        self.assertEqual(run.summarize(slow)['decision'], 'not better')
        tied = [row('t', i, 'bare', i % 2 == 0) for i in range(6)] + [row('t', i, 'jev', i % 2 == 1) for i in range(6)]
        summary = run.summarize(tied)
        self.assertEqual((summary['jev_wins'], summary['jev_losses'], summary['decision']), (3, 3, 'not better'))

    def test_usage_reads_agent_and_jev_tokens_and_nudge_replies(self):
        records = [
            {'type': 'native', 'line': {'method': 'thread/tokenUsage/updated', 'params': {'tokenUsage': {'total': {
                'inputTokens': 1000, 'cachedInputTokens': 600, 'outputTokens': 100}}}}},
            {'type': 'jev', 'raw': {'usage': {'input_tokens': 2000, 'output_tokens': 50}}},
            {'type': 'jev', 'raw': None, 'error': 'jev HTTP 429'},
            {'type': 'nudge', 'nudge': {'reason': 'scope_creep', 'text': 'stay', 'interrupt': False}},
            {'type': 'event', 'event': {'kind': 'command', 'command': 'ls'}},
            {'type': 'event', 'event': {'kind': 'message', 'text': 'It is needed because the test imports it.'}},
        ]
        prices = {'m': {'input_per_million': 1.0, 'cached_input_per_million': 0.1, 'output_per_million': 10.0},
                  'jev-latest': {'input_per_million': 0.042}}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'run.jsonl'
            path.write_text(''.join(json.dumps(record) + '\n' for record in records))
            result = run.usage(path, 'm', prices)
        self.assertAlmostEqual(result['estimated_usd'], (400 * 1 + 600 * 0.1 + 100 * 10 + 2000 * 0.042) / 1e6)
        self.assertEqual(result['nudges'][0]['next_agent_message'], 'It is needed because the test imports it.')
        self.assertEqual((result['jev_calls'], result['jev_failures']), (2, 1))

    def test_unknown_model_price_stays_unknown(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'run.jsonl'
            path.write_text('')
            self.assertIsNone(run.usage(path, 'unpriced', {})['estimated_usd'])


if __name__ == '__main__':
    unittest.main()
