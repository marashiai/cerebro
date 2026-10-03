"""Own detached child processes and notify disposable waiters on completion."""

import json
import os
import socket
import subprocess
import sys
import threading
import time

from wait_detached import completion_socket, final_status, terminal_update, updates_lock, write_updates


def write_atomic(path, value):
    tmp = f'{path}.tmp.{os.getpid()}'
    with open(tmp, 'w') as fh:
        fh.write(value)
    os.replace(tmp, path)


def write_status(paths, value):
    for path in paths:
        write_atomic(path, value)


def monitor(fd, output, output_status, job_status, input_path, result_path, job_id, command):
    statuses = (output_status, job_status)
    listener = socket.socket(fileno=fd)
    completed = threading.Event()
    changed = threading.Condition()
    clients = []
    updates_path = job_status + '.updates.json'
    updates = {'sequence': 0, 'acknowledged': 0, 'notices': []}
    write_updates(updates_path, updates)
    rc = 127

    def notify(client):
        with client:
            try:
                client.settimeout(5)
                with client.makefile('r') as source:
                    raw = source.readline(65537)
                if len(raw) > 65536:
                    raise ValueError('job notification exceeded 64 KiB')
                request = json.loads(raw)
                client.settimeout(None)
                with changed:
                    if set(request) == {'notify'} and isinstance(request['notify'], dict):
                        if completed.is_set():
                            raise ValueError('cannot publish a notice after job completion')
                        updates['sequence'] += 1
                        sequence = updates['sequence']
                        updates['notices'].append({'sequence': sequence, 'notice': request['notify']})
                        write_updates(updates_path, updates)
                        changed.notify_all()
                        changed.wait_for(lambda: completed.is_set() or updates['acknowledged'] >= sequence)
                        response = {'acknowledged': sequence}
                    elif set(request) == {'wait'} and isinstance(request['wait'], int) \
                            and not isinstance(request['wait'], bool) and 0 <= request['wait'] <= updates['sequence']:
                        after = request['wait']
                        if not completed.is_set() and after > updates['acknowledged']:
                            updates['acknowledged'] = after
                            updates['notices'] = [item for item in updates['notices'] if item['sequence'] > after]
                            write_updates(updates_path, updates)
                            changed.notify_all()
                        changed.wait_for(lambda: completed.is_set() or bool(updates['notices']))
                        response = (terminal_update(job_status, after) if completed.is_set() else
                                    {'kind': 'notice', **updates['notices'][0], 'job_exit_code': None})
                    else:
                        raise ValueError('invalid job notification or notice sequence')
                client.sendall((json.dumps(response) + '\n').encode())
            except (ValueError, TypeError) as error:
                try:
                    client.sendall((json.dumps({'error': str(error)}) + '\n').encode())
                except OSError:
                    pass
            except OSError:
                pass

    def accept():
        while not completed.is_set():
            try:
                client, _ = listener.accept()
            except OSError:
                return
            worker = threading.Thread(target=notify, args=(client,), daemon=True)
            clients.append(worker)
            worker.start()

    threading.Thread(target=accept, daemon=True).start()
    if final_status(job_status) is None:
        write_status(statuses, 'running\n')
    try:
        with open(output, 'ab', buffering=0) as log, open(input_path or os.devnull, 'rb') as source:
            with open(result_path or output, 'ab', buffering=0) as result:
                rc = subprocess.call(command, stdin=source, stdout=result,
                                     stderr=log, close_fds=True,
                                     env={**os.environ, 'CEREBRO_JOB_STATUS': job_status, 'CEREBRO_JOB_ID': job_id})
    except Exception as exc:
        with open(output, 'ab', buffering=0) as log:
            log.write(f'cerebro detach: launch failed: {exc}\n'.encode())
    if final_status(job_status) == 130:
        rc = 130
    with changed:
        # Terminal waiters use this same lock. Once numeric status is visible,
        # no live waiter can overwrite a newer persisted acknowledgment.
        with updates_lock(updates_path):
            write_status(statuses, f'{rc}\n')
            completed.set()
        changed.notify_all()
    listener.close()
    for worker in clients:
        worker.join(timeout=5)
    for status in statuses:
        try:
            os.unlink(completion_socket(status))
        except FileNotFoundError:
            pass
    if input_path:
        os.unlink(input_path)


def launch(output, status, pid_path, job_file, job_id, label, command,
           input_path='', result_path='', announce=True):
    os.makedirs(os.path.dirname(output), exist_ok=True)
    open(output, 'wb').close()
    if result_path:
        open(result_path, 'wb').close()
    job_status = f'{job_file}.status'
    job_pid = f'{job_file}.pid'
    write_status((status, job_status), 'starting\n')
    listener = socket.socket(socket.AF_UNIX)
    socket_path = completion_socket(job_status)
    listener.bind(socket_path)
    os.chmod(socket_path, 0o600)
    listener.listen()
    alias = completion_socket(status)
    if os.path.lexists(alias):
        os.unlink(alias)
    os.symlink(socket_path, alias)
    updates_alias = status + '.updates.json'
    if os.path.lexists(updates_alias):
        os.unlink(updates_alias)
    os.symlink(job_status + '.updates.json', updates_alias)
    try:
        proc = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), '--monitor',
             str(listener.fileno()), output, status, job_status, input_path,
             result_path, job_id, *command],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
            close_fds=True, pass_fds=(listener.fileno(),))
    finally:
        listener.close()
    for path in (pid_path, job_pid):
        write_atomic(path, f'{proc.pid}\n')
    job = {
        'id': job_id, 'command': label, 'output': output,
        'status': job_status, 'pid_file': job_pid,
        'output_status': status, 'output_pid_file': pid_path,
        'pid': proc.pid, 'result': result_path or output,
        'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    write_atomic(job_file, json.dumps(job, indent=2) + '\n')
    if announce:
        print(f'cerebro: detached job {job_id} (pid {proc.pid})')
        print(f'  output: {output}')
        print(f'  status: {status} (running, then the numeric exit code)')
        print(f'  notify: cerebro wait {job_id}')
        print(f'  cancel: cerebro cancel {job_id}')
    return job


if __name__ == '__main__':
    if len(sys.argv) >= 10 and sys.argv[1] == '--monitor':
        monitor(int(sys.argv[2]), *sys.argv[3:9], sys.argv[9:])
    elif len(sys.argv) >= 8:
        launch(*sys.argv[1:7], sys.argv[7:])
    else:
        sys.exit('detach_process: missing arguments')
