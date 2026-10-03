"""Offline checks of the scheduler's real process and delivery boundaries."""

import json
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from parallel import coordinate


GROUPS_RUN = 0


def record_group(group, emit):
    global GROUPS_RUN
    GROUPS_RUN += 1
    previous = os.environ.get('CEREBRO_PARALLEL_TEST')
    os.environ['CEREBRO_PARALLEL_TEST'] = str(group['id'])
    with group['lock']:
        group['active'].value += 1
        group['peak'].value = max(group['peak'].value, group['active'].value)
    try:
        if group.get('barrier'):
            group['barrier'].wait(timeout=5)
        for arm in group['arms']:
            started = time.monotonic()
            time.sleep(.025)
            row = {'group': group['id'], 'arm': arm, 'pid': os.getpid(),
                   'previous_env': previous, 'groups_run': GROUPS_RUN,
                   'started': started, 'ended': time.monotonic()}
            path = Path(group['directory']) / (arm + '.json')
            path.write_text(json.dumps(row))
            emit(row)
    finally:
        time.sleep(.08)
        with group['lock']:
            group['active'].value -= 1
        (Path(group['directory']) / 'finalized').write_text('finished')


def failing_group(group, emit):
    if group.get('barrier'):
        group['barrier'].wait(timeout=5)
    if group['mode'] == 'exit':
        row = {'group': group['id'], 'arm': 'first'}
        (Path(group['directory']) / 'first.json').write_text(json.dumps(row))
        emit(row)
        os._exit(7)
    if group['mode'] == 'missing':
        return
    if group['mode'] == 'extra':
        emit({'arm': 'first'})
        emit({'arm': 'extra'})
        return
    if group['mode'] == 'interrupt':
        emit({'arm': 'first'})
        raise KeyboardInterrupt('fixture interrupted')
    try:
        threading.Event().wait(30)
    finally:
        # Larger than a pipe buffer: shutdown must keep draining worker output.
        emit({'cleanup': 'x' * 300000})
        (Path(group['directory']) / 'finalized').write_text('finished')


def interruptible_group(group, emit):
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    directory = Path(group['directory'])
    (directory / 'child.pid').write_text(str(child.pid))
    try:
        group['barrier'].wait(timeout=5)
        emit({'group': group['id'], 'arm': 'first', 'pid': os.getpid()})
        threading.Event().wait(30)
    finally:
        child.terminate()
        child.wait(timeout=5)
        time.sleep(.08)
        (directory / 'finalized').write_text('finished')


class ParallelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.context = multiprocessing.get_context('spawn')
        self.old_alarm = signal.signal(signal.SIGALRM, self.timeout)
        signal.alarm(15)

    def tearDown(self):
        signal.alarm(0)
        signal.signal(signal.SIGALRM, self.old_alarm)
        self.temp.cleanup()

    @staticmethod
    def timeout(signum, frame):
        raise TimeoutError('parallel test exceeded 15 seconds')

    def group(self, number, **fields):
        directory = self.directory / str(number)
        directory.mkdir()
        return {'id': number, 'directory': str(directory), 'arms': ['first', 'second'], **fields}

    def test_process_isolation_concurrency_sequencing_and_durable_delivery(self):
        for jobs in (1, 2):
            with self.subTest(jobs=jobs):
                lock = self.context.Lock()
                active, peak = self.context.Value('i', 0), self.context.Value('i', 0)
                barrier = self.context.Barrier(2) if jobs == 2 else None
                groups = [self.group(jobs * 10 + number, lock=lock, active=active,
                                     peak=peak, barrier=barrier) for number in range(4)]
                rows = []
                previous = os.environ.get('CEREBRO_PARALLEL_TEST')
                os.environ['CEREBRO_PARALLEL_TEST'] = 'parent'
                try:
                    def receive(row):
                        group = next(group for group in groups if group['id'] == row['group'])
                        path = Path(group['directory']) / (row['arm'] + '.json')
                        self.assertEqual(json.loads(path.read_text()), row)
                        rows.append(row)
                    coordinate(iter(groups), jobs, receive, record_group)
                    self.assertEqual(os.environ['CEREBRO_PARALLEL_TEST'], 'parent')
                finally:
                    if previous is None:
                        os.environ.pop('CEREBRO_PARALLEL_TEST', None)
                    else:
                        os.environ['CEREBRO_PARALLEL_TEST'] = previous
                self.assertEqual(len(rows), 8)
                self.assertEqual(len({row['pid'] for row in rows}), 4)
                self.assertNotIn(os.getpid(), {row['pid'] for row in rows})
                self.assertEqual({row['previous_env'] for row in rows}, {'parent'})
                self.assertEqual({row['groups_run'] for row in rows}, {1})
                self.assertEqual(active.value, 0)
                self.assertEqual(peak.value, jobs)
                for group in groups:
                    first, second = [row for row in rows if row['group'] == group['id']]
                    self.assertEqual([first['arm'], second['arm']], group['arms'])
                    self.assertLessEqual(first['ended'], second['started'])
                    self.assertTrue((Path(group['directory']) / 'finalized').is_file())

    def test_worker_death_preserves_delivered_rows_and_finalizes_only_owned_peer(self):
        barrier = self.context.Barrier(2)
        groups = [self.group(1, mode='exit', barrier=barrier),
                  self.group(2, mode='wait', barrier=barrier)]
        rows = []
        with self.assertRaisesRegex(RuntimeError, 'exit 7'):
            coordinate(groups, 2, rows.append, failing_group)
        self.assertEqual(rows, [{'group': 1, 'arm': 'first'}])
        self.assertEqual(json.loads((self.directory / '1/first.json').read_text()), rows[0])
        self.assertTrue((self.directory / '2/finalized').is_file())

    def test_missing_rows_are_a_failure(self):
        with self.assertRaisesRegex(RuntimeError, 'before every scheduled trial'):
            coordinate([self.group(1, mode='missing')], 1, lambda row: None, failing_group)

    def test_extra_rows_are_a_failure(self):
        group = self.group(1, mode='extra')
        group['arms'] = ['first']
        with self.assertRaisesRegex(RuntimeError, 'more rows'):
            coordinate([group], 1, lambda row: None, failing_group)

    def test_worker_interrupt_remains_an_interrupt_after_last_row(self):
        group = self.group(1, mode='interrupt')
        group['arms'] = ['first']
        rows = []
        with self.assertRaisesRegex(RuntimeError, 'interrupted'):
            coordinate([group], 1, rows.append, failing_group)
        self.assertEqual(rows, [{'arm': 'first'}])

    def test_coordinator_interrupt_runs_owned_finalizers_and_preserves_unrelated_process(self):
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        barrier = self.context.Barrier(2)
        groups = [self.group(number, barrier=barrier) for number in (1, 2)]
        def receive(row):
            os.kill(row['pid'], signal.SIGINT)
            raise KeyboardInterrupt('coordinator interrupted')
        try:
            with self.assertRaisesRegex(KeyboardInterrupt, 'coordinator interrupted'):
                coordinate(groups, 2, receive, interruptible_group)
            self.assertIsNone(unrelated.poll())
            for group in groups:
                directory = Path(group['directory'])
                self.assertTrue((directory / 'finalized').is_file())
                with self.assertRaises(ProcessLookupError):
                    os.kill(int((directory / 'child.pid').read_text()), 0)
        finally:
            unrelated.terminate()
            unrelated.wait(timeout=5)

    def test_empty_schedule_and_invalid_worker_count(self):
        coordinate([], 1, lambda row: self.fail('unexpected row'), record_group)
        for jobs in (0, -1):
            with self.assertRaises(ValueError):
                coordinate([], jobs, lambda row: None, record_group)


if __name__ == '__main__':
    unittest.main()
