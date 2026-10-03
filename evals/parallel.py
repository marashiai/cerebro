"""Process-isolated trial groups; only the coordinator aggregates reports."""

import multiprocessing
from multiprocessing.connection import wait
import signal
import time


def stop_worker(signum, frame):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise KeyboardInterrupt('eval worker interrupted')


def worker(group, events, run_group):
    signal.signal(signal.SIGINT, stop_worker)
    signal.signal(signal.SIGTERM, stop_worker)
    try:
        run_group(group, events.send)
    except KeyboardInterrupt:
        raise SystemExit(130)
    finally:
        events.close()


def stop_workers(active):
    for entry in active.values():
        if entry['process'].is_alive():
            entry['process'].terminate()
    deadline = time.monotonic() + 20
    pending = list(active.values())
    while pending:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        handles = [entry['process'].sentinel for entry in pending]
        handles.extend(entry['pipe'] for entry in pending if not entry['closed'])
        ready = wait(handles, timeout=remaining)
        for entry in pending[:]:
            if entry['pipe'] in ready:
                try:
                    entry['pipe'].recv()
                except (EOFError, OSError):
                    entry['closed'] = True
            if entry['process'].sentinel in ready:
                entry['process'].join()
                pending.remove(entry)
    for entry in active.values():
        process = entry['process']
        if process.is_alive():
            process.kill()
        process.join()


def coordinate(groups, jobs, receive, run_group):
    if not isinstance(jobs, int) or jobs < 1:
        raise ValueError('jobs must be a positive integer')
    context = multiprocessing.get_context('spawn')
    groups = iter(groups)
    active = {}
    exhausted = False

    def read(entry):
        try:
            value = entry['pipe'].recv()
        except EOFError:
            entry['closed'] = True
            return
        except OSError as error:
            raise RuntimeError('eval worker result delivery failed') from error
        entry['received'] += 1
        if entry['received'] > entry['expected']:
            raise RuntimeError('eval worker returned more rows than scheduled trials')
        receive(value)

    try:
        while active or not exhausted:
            while len(active) < jobs and not exhausted:
                try:
                    group = next(groups)
                except StopIteration:
                    exhausted = True
                    break
                expected = len(group['arms'])
                parent, child = context.Pipe(duplex=False)
                process = context.Process(target=worker, args=(group, child, run_group))
                try:
                    process.start()
                except BaseException:
                    parent.close()
                    raise
                finally:
                    child.close()
                active[process.sentinel] = {'process': process, 'pipe': parent,
                                            'expected': expected, 'received': 0,
                                            'closed': False}
            if not active:
                break
            handles = list(active)
            handles.extend(entry['pipe'] for entry in active.values() if not entry['closed'])
            ready = wait(handles)
            for sentinel, entry in list(active.items()):
                if entry['pipe'] in ready:
                    read(entry)
                if sentinel not in ready:
                    continue
                entry['process'].join()
                while not entry['closed']:
                    read(entry)
                code = entry['process'].exitcode
                if code:
                    detail = 'interrupted' if code == 130 else 'failed'
                    raise RuntimeError(f'eval worker {detail} (exit {code})')
                if entry['received'] != entry['expected']:
                    raise RuntimeError('eval worker exited before every scheduled trial returned')
                entry['pipe'].close()
                entry['process'].close()
                del active[sentinel]
    finally:
        if active:
            stop_workers(active)
        for entry in active.values():
            entry['pipe'].close()
            entry['process'].close()
