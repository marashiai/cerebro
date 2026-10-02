"""Native Claude stream-json/Codex app-server responses without model calls."""

import json
import os
import sys
import threading
import time

backend = sys.argv[1]
mode = os.environ.get('NATIVE_FIXTURE_MODE', 'ok')
log_path = os.environ['NATIVE_FIXTURE_LOG']
lock = threading.Lock()
write_lock = threading.Lock()
thread_id = os.environ.get('NATIVE_FIXTURE_SID', 'NATIVE-CHILD-1')
turn_id = None
turn_active = False
turn_sequence = 0
turn_lock = threading.Lock()


def record(event):
    with lock, open(log_path, 'a') as log:
        log.write(json.dumps(event) + '\n')


def send(event):
    with write_lock:
        print(json.dumps(event), flush=True)


def notification(method, **params):
    send({'jsonrpc': '2.0', 'method': method, 'params': {'threadId': thread_id, **params}})


def complete_codex(text, completed_turn):
    global turn_active
    time.sleep(float(os.environ.get('NATIVE_FIXTURE_DELAY', '0')))
    if mode == 'steer':
        time.sleep(0.8)
    if mode == 'background':
        notification('item/started', item={'id': 'background-command', 'type': 'commandExecution',
                     'status': 'inProgress', 'command': 'fixture child'})
    notification('item/completed', item={'id': 'answer', 'type': 'agentMessage', 'text': text})
    with turn_lock:
        turn_active = False
    notification('turn/completed', turn={'id': completed_turn,
                 'status': 'failed' if mode == 'failure' else 'completed',
                 **({'error': {'message': 'fixture failure'}} if mode == 'failure' else {})})
    if mode == 'background':
        time.sleep(0.3)
        notification('item/completed', item={'id': 'background-command', 'type': 'commandExecution',
                     'status': 'completed', 'command': 'fixture child', 'aggregatedOutput': 'BACKGROUND_JOINED', 'exitCode': 0})


record({'argv': sys.argv[2:], 'isolated': os.environ.get('PLAYWRIGHT_MCP_ISOLATED')})
if backend == 'codex':
    if 'mcp' in sys.argv and 'list' in sys.argv:
        print('[{"name":"untrusted.server","enabled":true}]')
        raise SystemExit
    if 'app-server' not in sys.argv:
        raise SystemExit('expected native app-server')
    for line in sys.stdin:
        event = json.loads(line)
        record(event)
        method = event.get('method')
        params = event.get('params', {})
        if method == 'initialized':
            continue
        if method not in ('initialize', 'thread/start', 'thread/resume', 'turn/start'):
            raise SystemExit('unexpected native app-server method: ' + str(method))
        result = {}
        new_turn = False
        if method in ('thread/start', 'thread/resume'):
            if mode == 'resume-reject' and method == 'thread/resume':
                send({'jsonrpc': '2.0', 'id': event['id'], 'error': {'code': -32602, 'message': 'stored thread rejected'}})
                continue
            thread_id = params.get('threadId', thread_id)
            result = {'thread': {'id': thread_id}}
        elif method == 'turn/start':
            with turn_lock:
                if not turn_active:
                    turn_sequence += 1
                    turn_id = 'TURN-' + str(turn_sequence)
                    turn_active = True
                    new_turn = True
            result = {'turn': {'id': turn_id, 'status': 'inProgress'}}
        if 'id' in event:
            send({'jsonrpc': '2.0', 'id': event['id'], 'result': result})
        if new_turn:
            notification('turn/started', turn={'id': turn_id, 'status': 'inProgress'})
            threading.Thread(target=complete_codex, args=('NATIVE_DONE', turn_id), daemon=True).start()
else:
    if mode == 'resume-reject':
        print('stored session rejected', file=sys.stderr)
        raise SystemExit(2)
    send({'type': 'system', 'subtype': 'init', 'session_id': thread_id})
    if '--input-format' not in sys.argv:
        record({'prompt': sys.stdin.read()})
        send({'type': 'result', 'subtype': 'error_during_execution' if mode == 'failure' else 'success',
              'result': 'NATIVE_DONE', 'session_id': thread_id,
              **({'is_error': True, 'errors': ['fixture failure']} if mode == 'failure' else {})})
    else:
        for line in sys.stdin:
            event = json.loads(line)
            record(event)
            if mode == 'steer':
                time.sleep(0.4)
            send({'type': 'result', 'subtype': 'error_during_execution' if mode == 'failure' else 'success',
                  'result': 'NATIVE_DONE', 'session_id': thread_id,
                  **({'is_error': True, 'errors': ['fixture failure']} if mode == 'failure' else {})})
