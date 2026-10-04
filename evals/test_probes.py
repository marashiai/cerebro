"""Protocol selection keeps native contracts distinct from live model evidence."""

import unittest

import probes


class ProbeTests(unittest.TestCase):
    def test_catalogue_selects_completion_steering_cancel_resume_and_native_failure(self):
        self.assertEqual({case['id'] for case in probes.CASES}, set(probes.TESTS))
        self.assertTrue({'native-completion', 'native-answer-resume', 'native-steering',
                         'native-cancel-disconnect', 'native-failure'} <= set(probes.TESTS))
        self.assertTrue(all(case['mode'] == 'protocol' for case in probes.CASES))


if __name__ == '__main__':
    unittest.main()
