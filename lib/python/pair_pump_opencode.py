"""Drive a native V2 session from its event stream, with optional live steering.

Completion includes native background-shell notifications and the executions
they wake. The parent never polls session state or reads implementation logs.
"""

import base64
import json
import os
from pathlib import Path
import queue
import select
import sys
import threading
import time

from opencode_api import OpenCodeAPI, V2Events


def run():
    base, sid, agent, model, fifo, steering, child_log, idle, stall, stall_busy = sys.argv[1:]
    api = OpenCodeAPI(base)
    translator = V2Events(sid)
    events = queue.Queue()
    wake_r, wake_w = os.pipe()
    ready = threading.Event()
    stop = threading.Event()

    def notify(event):
        events.put(event)
        os.write(wake_w, b'x')

    def read_events():
        try:
            with api.request(api.event_path, timeout=None) as response:
                ready.set()
                for raw in response:
                    if stop.is_set():
                        return
                    if raw.startswith(b'data:'):
                        notify(json.loads(raw[5:].strip()))
        except Exception as error:
            notify({'stream_error': str(error)})
        finally:
            ready.set()
            if not stop.is_set():
                notify({'stream_error': 'native event stream closed'})

    def prompt(text):
        for path, payload in api.prompt_requests(sid, agent, model, text):
            with api.request(path, payload):
                pass

    def emit(event):
        print(json.dumps(event), flush=True)

    threading.Thread(target=read_events, daemon=True).start()
    if not ready.wait(10):
        raise RuntimeError('native event subscription did not start')
    fifo_fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK) if fifo else None
    idle_grace, stall_secs, busy_secs = map(float, (idle, stall, stall_busy))
    emit({'type': 'progress', 'sessionID': sid})
    prompt(sys.stdin.read())
    idle_deadline = None
    last_activity = time.monotonic()
    tools = set()
    running_shells = set()
    completed_shells = set()
    pending_notifications = set()
    fifo_buffer = b''
    stem = child_log[:-6] if child_log.endswith('.jsonl') else child_log
    try:
        while True:
            now = time.monotonic()
            deadline = idle_deadline if idle_deadline is not None else last_activity + (busy_secs if tools or running_shells else stall_secs)
            descriptors = [wake_r] + ([fifo_fd] if fifo_fd is not None else [])
            readable, _, _ = select.select(descriptors, [], [], max(0, deadline - now))
            if not readable:
                if idle_deadline is not None:
                    return 0
                Path(stem + '.stalled').touch()
                return 5
            if wake_r in readable:
                os.read(wake_r, 65536)
                while True:
                    try:
                        event = events.get_nowait()
                    except queue.Empty:
                        break
                    if 'stream_error' in event:
                        raise RuntimeError(event['stream_error'])
                    data = event.get('data') or {}
                    if data.get('sessionID') != sid:
                        continue
                    last_activity = time.monotonic()
                    kind = event.get('type')
                    if kind == 'permission.asked':
                        with api.request(f'/api/session/{sid}/permission/{data["id"]}/reply', {'reply': 'reject'}):
                            pass
                    elif kind == 'session.execution.started':
                        idle_deadline = None
                    elif kind == 'session.tool.called':
                        tools.add(data['id'])
                    elif kind in ('session.tool.success', 'session.tool.failed'):
                        tools.discard(data['id'])
                        metadata = data.get('metadata') or {}
                        if metadata.get('status') == 'running' and metadata.get('shellID'):
                            if metadata['shellID'] not in completed_shells:
                                running_shells.add(metadata['shellID'])
                    elif kind == 'session.inbox.enqueued':
                        item = data.get('item') or {}
                        metadata = (item.get('payload') or {}).get('metadata') or {}
                        if item.get('type') == 'user':
                            pending_notifications.add(data['inboxID'])
                            idle_deadline = None
                        if item.get('type') == 'synthetic' and metadata.get('source') == 'shell':
                            completed_shells.add(metadata['shellID'])
                            running_shells.discard(metadata['shellID'])
                            pending_notifications.add(data['inboxID'])
                            idle_deadline = None
                    elif kind == 'session.inbox.delivered' and data.get('inboxID') in pending_notifications:
                        pending_notifications.remove(data['inboxID'])
                        idle_deadline = None
                    translated, terminal = translator.translate(event)
                    for result in translated:
                        emit(result)
                    if terminal:
                        if kind != 'session.execution.succeeded':
                            return 2
                        if not running_shells and not pending_notifications:
                            idle_deadline = last_activity + idle_grace
            if fifo_fd in readable:
                fifo_buffer += os.read(fifo_fd, 65536)
                while b'\n' in fifo_buffer:
                    line, fifo_buffer = fifo_buffer.split(b'\n', 1)
                    prefix, encoded = line.decode().split(' ', 1)
                    text = base64.b64decode(encoded).decode()
                    if prefix == 'R':
                        with api.request(api.abort_path(sid), {}):
                            pass
                        Path(stem + '.restart').write_text(text)
                        return 0
                    if prefix != 'S':
                        raise ValueError('invalid steering prefix')
                    idle_deadline = None
                    prompt(text)
                    with open(steering, 'a') as record:
                        record.write('- ' + text.replace('\n', '\n  ') + '\n')
                    last_activity = time.monotonic()
    finally:
        stop.set()
        if fifo_fd is not None:
            os.close(fifo_fd)


if __name__ == '__main__':
    try:
        sys.exit(run())
    except Exception as error:
        print('cerebro OpenCode: ' + str(error), file=sys.stderr)
        sys.exit(2)
