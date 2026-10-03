"""Wait for a durable child result or a scope notice, without status polling."""

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile


def completion_socket(status_path):
    directory = Path('/tmp') / f'cerebro-wait-{os.getuid()}'
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
        raise OSError('completion socket directory must be private')
    key = hashlib.sha256(os.path.realpath(status_path).encode()).hexdigest()[:32]
    return str(directory / (key + '.sock'))


def final_status(path):
    try:
        return int(Path(path).read_text().strip())
    except (OSError, ValueError):
        return None


@contextmanager
def updates_lock(path):
    fd = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _write_updates(path, updates):
    with tempfile.NamedTemporaryFile(mode='w', dir=Path(path).parent, delete=False) as result:
        json.dump(updates, result)
    os.replace(result.name, path)


def write_updates(path, updates):
    with updates_lock(path):
        if Path(path).exists():
            stored = json.loads(Path(path).read_text())
            updates['acknowledged'] = max(updates['acknowledged'], stored['acknowledged'])
            updates['notices'] = [item for item in updates['notices'] if item['sequence'] > updates['acknowledged']]
        _write_updates(path, updates)


def terminal_update(status_path, after):
    rc = final_status(status_path)
    if rc is None:
        return None
    path = Path(status_path + '.updates.json')
    if path.exists():
        path = path.resolve()
        with updates_lock(path):
            updates = json.loads(path.read_text())
            if after > updates['sequence']:
                raise ValueError('wait: notice sequence is ahead of this job')
            if after > updates['acknowledged']:
                updates['acknowledged'] = after
                updates['notices'] = [item for item in updates['notices'] if item['sequence'] > after]
                _write_updates(path, updates)
            pending = [item for item in updates['notices'] if item['sequence'] > updates['acknowledged']]
            if pending:
                return {'kind': 'notice', **pending[0], 'job_exit_code': rc}
    elif after:
        raise ValueError('wait: this job has no scope notices')
    return {'kind': 'completed', 'exit_code': rc}


def wait_for_update(status_path, after=0):
    if not isinstance(after, int) or isinstance(after, bool) or after < 0:
        raise ValueError('wait: --after must be a nonnegative notice sequence')
    update = terminal_update(status_path, after)
    if update:
        return update
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(completion_socket(status_path))
            client.sendall((json.dumps({'wait': after}) + '\n').encode())
            with client.makefile('r') as stream:
                raw = stream.readline()
        if not raw:
            raise OSError('monitor connection closed')
        update = json.loads(raw)
        if 'error' in update:
            raise ValueError(update['error'])
        return update
    except OSError:
        # The final status is durable before socket closure. A closed socket
        # without that status means monitor loss, not successful completion.
        update = terminal_update(status_path, after)
        if update:
            return update
        with tempfile.NamedTemporaryFile(mode='w', dir=Path(status_path).parent, delete=False) as result:
            result.write('125\n')
        os.replace(result.name, status_path)
        sys.stderr.write('cerebro: detached monitor disappeared before completion (exit 125)\n')
        return {'kind': 'completed', 'exit_code': 125}


def job_response(job, update):
    result = {'job_id': job.get('id', ''), 'output': job.get('output', '')}
    if update['kind'] == 'notice':
        rc = update.get('job_exit_code')
        result.update(exit_code=0, state='running' if rc is None else 'completed',
                      job_exit_code=rc, sequence=update['sequence'], notice=update['notice'],
                      text='Inspect the cited scope notice, decide whether to steer or restart '
                           'within task authority, then wait again with --after ' + str(update['sequence']))
    else:
        rc = update['exit_code']
        text = Path(job['result']).read_text(errors='replace') if job.get('result') else ''
        if rc and job.get('output'):
            text += '\n' + Path(job['output']).read_text(errors='replace')[-4000:]
        result.update(exit_code=rc, state='completed', text=text)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('status')
    parser.add_argument('--after', type=int, default=0)
    parser.add_argument('--job-file')
    parser.add_argument('--note')
    parser.add_argument('--disposition', choices=['continue', 'correct', 'stop'])
    args = parser.parse_args()
    job = json.loads(Path(args.job_file).read_text()) if args.job_file else {}
    if args.note is not None or args.disposition is not None:
        if not args.note or not args.disposition or not args.after or not job.get('id'):
            raise ValueError('a concern decision requires a job ID, --after, --disposition and --note')
        updates = json.loads(Path(args.status + '.updates.json').read_text())
        if args.after > updates['sequence']:
            raise ValueError('decision sequence is ahead of this job')
        from child_store_lib import _now_iso
        decision = {'job_id': job['id'], 'sequence': args.after, 'disposition': args.disposition,
                    'reason': args.note, 'ts': _now_iso()}
        journal = Path(os.environ['CEREBRO_SESSION_DIR']) / 'decisions.jsonl'
        with journal.open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            stream.write(json.dumps(decision) + '\n')
    response = job_response(job, wait_for_update(args.status, args.after))
    print(json.dumps(response))
    return response['exit_code']


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError) as error:
        sys.exit('cerebro wait: ' + str(error))
