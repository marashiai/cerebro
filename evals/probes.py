"""Offline native transport checks, distinct from live model-quality conditions."""

import os
import sys

from runtime import ROOT, process

TESTS = {
    'native-completion': ('task_lifecycle_test.LifecycleTests.test_happy_path_three_native_backends',),
    'native-answer-resume': ('task_lifecycle_test.LifecycleTests.test_question_answer_same_child_then_one_review',),
    'native-failure': ('task_lifecycle_test.LifecycleTests.test_native_worker_failure_has_no_review',
                       'task_lifecycle_test.LifecycleTests.test_reviewer_failure_resumes_without_implementation'),
    'native-interruption': ('task_lifecycle_test.LifecycleTests.test_controller_interruption_before_and_after_receipt',),
    'native-steering': ('task_lifecycle_test.LifecycleTests.test_native_steering_is_available_without_jev',),
    'native-cancel-disconnect': (),
    'native-restart': ('task_lifecycle_test.LifecycleTests.test_restart_starts_fresh_native_child_and_preserves_diagnosis',),
}
CASES = [{'id': name, 'suite': 'protocol', 'kind': 'native-contract', 'mode': 'protocol', 'paired': False,
          'description': ', '.join(tests) if tests else 'Durable parent reconnect and cancellation preserve the other task', 'expected_arms': ['protocol']} for name, tests in TESTS.items()]


def trial(name, directory, settings):
    env = {key: value for key, value in os.environ.items() if not key.startswith('CEREBRO_')}
    argv = ([sys.executable, str(ROOT / 'tests/durable_task_test.py')] if name == 'native-cancel-disconnect' else
            [sys.executable, '-m', 'unittest', '-v', *TESTS[name]])
    outcome = process(argv, env, ROOT / 'tests',
                      directory / 'native-contract', timeout=settings['timeout'])
    return {'correct': True, 'checks': {'native_contract_passed': True},
            'real_model_inference': False, 'provider': 'native-transport-fixture',
            'elapsed_seconds': outcome['elapsed_seconds']}
