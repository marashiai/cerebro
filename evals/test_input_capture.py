"""Eval ingress preserves original requirements independently of model packets."""

from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import comparison
import lease_fixture
import runtime
from user_input import snapshot


def setup_session(directory, settings, watch):
    session = directory / 'home/sessions/eval'
    (session / 'children').mkdir(parents=True)
    return {'CEREBRO_SESSION_DIR': str(session), 'CEREBRO_INPUT_OWNER': 'external'}, session


class InputCaptureTests(unittest.TestCase):
    def test_missing_original_input_errors_before_inference_or_execute(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            env, session = setup_session(directory, {}, False)
            with patch('baseline.run') as native, patch('runtime.process') as command:
                with self.assertRaisesRegex(ValueError, 'no original user input'):
                    runtime.codex(directory, env, 'Model-written summary', {})
                with self.assertRaisesRegex(ValueError, 'no original user input'):
                    runtime.command(directory, env, ['execute'], 10, stdin='{}')
                native.assert_not_called()
                command.assert_not_called()
            raw = '  claim returns None; complete returns False.\n\n'
            captured = runtime.capture_task_input(env, raw)
            self.assertEqual(captured, snapshot(session))
            self.assertEqual(captured[0]['content'], [{'type': 'text', 'text': raw}])

    def test_every_comparison_arm_captures_identical_original_lease_task_before_model_call(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed = root / 'seed'
            lease_fixture.seed(seed)
            settings = {'models': {role: 'configured-model' for role in runtime.ROLE_GROUPS.values()},
                        'efforts': {role: 'configured-effort' for role in runtime.ROLE_GROUPS.values()},
                        'timeout': 300}
            originals = []

            def inspect(directory, env):
                session = Path(env['CEREBRO_SESSION_DIR'])
                recorded = snapshot(session)
                self.assertEqual(len(recorded), 1)
                text = recorded[0]['content'][0]['text']
                self.assertIn(lease_fixture.REQUIREMENTS, text)
                self.assertNotIn('Initial packet:', text)
                self.assertNotIn('advisory Jev', text)
                document = json.loads((session / 'task.json').read_text())
                self.assertEqual(document['user_inputs'], recorded)
                originals.append(text.replace(str(directory), '<trial>'))
                return text

            def bare(directory, repo, prompt, settings, schema, role, *, env):
                self.assertEqual(prompt, inspect(directory, env))
                return {'answer': {}, 'elapsed_seconds': 1}

            def supervised(directory, env, prompt, settings, **kwargs):
                self.assertTrue(prompt.startswith(inspect(directory, env)))
                return {'answer': {}, 'elapsed_seconds': 1}

            def direct(directory, env, argv, timeout, *, stdin):
                original = inspect(directory, env)
                self.assertTrue(json.loads(stdin)['task'].startswith(original))
                return {'state': 'completed', 'exit_code': 0,
                        'text': json.dumps({'implementation': {'delivery': {}}})}

            with ExitStack() as patches:
                patches.enter_context(patch('comparison.setup', side_effect=setup_session))
                patches.enter_context(patch('comparison.cleanup'))
                patches.enter_context(patch('comparison.baseline.run', side_effect=bare))
                patches.enter_context(patch('comparison.codex', side_effect=supervised))
                patches.enter_context(patch('comparison.command', side_effect=direct))
                patches.enter_context(patch('comparison.grade', return_value={'correct': True, 'metrics': {}}))
                patches.enter_context(patch('comparison.condition_metrics', return_value={}))
                patches.enter_context(patch('comparison.apply_condition_checks'))
                for arm in comparison.ARMS:
                    directory = root / arm
                    directory.mkdir()
                    comparison.trial('comparison-lease-queue-concurrency', directory, seed, arm, settings)
            self.assertEqual(len(originals), len(comparison.ARMS))
            self.assertEqual(len(set(originals)), 1)


if __name__ == '__main__':
    unittest.main()
